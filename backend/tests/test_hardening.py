"""Regression tests for the Gate-4 verifier findings:
concurrent loop-budget safety, authorization, and mention-parser precision."""
import asyncio

from .conftest import make_room


# ---------------------------------------------------------------------------
# Finding 1 (critical): budget must hold under concurrent requests
# ---------------------------------------------------------------------------
def test_concurrent_posts_are_serialized_per_room(client):
    """Two overlapping human posts to the same room run as two clean,
    non-interleaved 6-cycle rounds instead of racing (Design 02 Stage 2 / H5)."""
    room = make_room(client, "RaceBank")
    app = client.app
    orchestrator = app.state.orchestrator

    from app.db.base import get_sessionmaker
    from app.db.models import Room

    async def post(content: str):
        async with get_sessionmaker()() as session:
            db_room = await session.get(Room, room["id"])
            await orchestrator.handle_human_message(
                session, db_room, sender_name="racer@thetaray.com", content=content
            )

    async def race():
        await asyncio.gather(post("first concurrent kick"), post("second concurrent kick"))

    client.portal.call(race)

    messages = client.get(f"/api/rooms/{room['id']}/messages").json()
    agent_msgs = [m for m in messages if m["sender_type"] == "agent"]
    assert len(agent_msgs) == 12, "two full, non-interleaved 6-turn rounds"
    first_round, second_round = agent_msgs[:6], agent_msgs[6:]
    for round_msgs in (first_round, second_round):
        assert [m["cycle_number"] for m in round_msgs] == [1, 2, 3, 4, 5, 6]
        speakers = [m["agent_key"] for m in round_msgs]
        assert all(a != b for a, b in zip(speakers, speakers[1:]))

    status = client.get(f"/api/rooms/{room['id']}").json()
    assert status["cycles_used"] == 6
    assert status["status"] == "paused_awaiting_human"


def test_concurrent_resume_and_new_message_are_serialized(client):
    room = make_room(client, "ResumeSerialBank")
    client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})  # pauses it

    import httpx

    async def race():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await asyncio.gather(
                ac.post(f"/api/rooms/{room['id']}/resume", timeout=60),
                ac.post(
                    f"/api/rooms/{room['id']}/messages",
                    json={"content": "meanwhile a human posts"},
                    timeout=60,
                ),
            )

    first, second = client.portal.call(race)
    for resp in (first, second):
        assert resp.status_code == 200, resp.text

    status = client.get(f"/api/rooms/{room['id']}").json()
    assert status["cycles_used"] <= status["cycle_limit"]
    messages = client.get(f"/api/rooms/{room['id']}/messages").json()
    agent_msgs = [m for m in messages if m["sender_type"] == "agent"]
    # No matter which acquires the lock first, both run to completion
    # serially, each a clean 6-turn round, never interleaved: the initial
    # "go" round (6) plus two more full rounds triggered by the race (6 + 6).
    assert len(agent_msgs) == 18


def test_resume_and_post_message_both_attempt_replica_lock(client, monkeypatch):
    """Both critical-section entry points must attempt the cross-replica
    Postgres advisory lock (no-op here on SQLite, but the call itself must
    happen). Design 02 Stage 2 wired this into handle_human_message but
    originally missed resume_room, leaving /resume with zero cross-replica
    protection even though its own docstring claimed both paths were
    covered — the in-process room_lock only serializes callers within one
    process, so a second replica would sail straight through unresumed."""
    from app.agents.orchestrator import Orchestrator

    calls: list[str] = []
    original = Orchestrator.acquire_replica_lock

    async def spy(self, session, room_id):
        calls.append(room_id)
        return await original(self, session, room_id)

    monkeypatch.setattr(Orchestrator, "acquire_replica_lock", spy)

    room = make_room(client, "ReplicaLockBank")

    client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})
    assert calls == [room["id"]], "handle_human_message must attempt the replica lock"
    calls.clear()

    resp = client.post(f"/api/rooms/{room['id']}/resume")
    assert resp.status_code == 200, resp.text
    assert calls == [room["id"]], "resume_room must attempt the replica lock too"


def test_concurrent_resumes_grant_single_budget(client):
    room = make_room(client, "ResumeRaceBank")
    client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})

    import httpx

    async def race_resumes():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            return await asyncio.gather(
                ac.post(f"/api/rooms/{room['id']}/resume", timeout=60),
                ac.post(f"/api/rooms/{room['id']}/resume", timeout=60),
            )

    first, second = client.portal.call(race_resumes)
    codes = sorted([first.status_code, second.status_code])
    # Truly-overlapping resumes: one wins, one 409s. With the instant mock
    # LLM the first may fully drain its budget and re-pause before the second
    # arrives — then both legitimately succeed as *sequential* resumes.
    assert codes in ([200, 409], [200, 200]), codes

    # The invariant either way: no response ever reports an over-budget room,
    # and no agent turn ever carries a cycle number beyond the limit.
    for resp in (first, second):
        if resp.status_code == 200:
            body = resp.json()
            assert body["cycles_used"] <= body["cycle_limit"]
            for m in body["messages"]:
                assert m["cycle_number"] is None or m["cycle_number"] <= 6

    status = client.get(f"/api/rooms/{room['id']}").json()
    assert status["cycles_used"] <= status["cycle_limit"]


# ---------------------------------------------------------------------------
# Finding 3 (major): membership + admin authorization
# ---------------------------------------------------------------------------
OUTSIDER = {"X-User-Email": "stranger@elsewhere.example"}


def test_non_member_is_locked_out_of_room(client):
    room = make_room(client, "PrivateBank")
    rid = room["id"]
    assert client.get(f"/api/rooms/{rid}", headers=OUTSIDER).status_code == 403
    assert (
        client.get(f"/api/rooms/{rid}/messages", headers=OUTSIDER).status_code == 403
    )
    assert (
        client.post(
            f"/api/rooms/{rid}/messages", json={"content": "hi"}, headers=OUTSIDER
        ).status_code
        == 403
    )
    assert client.post(f"/api/rooms/{rid}/resume", headers=OUTSIDER).status_code == 403
    assert (
        client.post(f"/api/rooms/{rid}/invites", headers=OUTSIDER).status_code == 403
    )
    assert (
        client.get(f"/api/rooms/{rid}/gdrive/status", headers=OUTSIDER).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/rooms/{rid}/agents/fce/skills",
            files={"file": ("x.md", b"# X", "text/markdown")},
            headers=OUTSIDER,
        ).status_code
        == 403
    )


def test_join_grants_access(client):
    room = make_room(client, "JoinableBank")
    token = client.post(f"/api/rooms/{room['id']}/invites").json()["token"]
    member = {"X-User-Email": "newbie@bank.example"}

    assert client.get(f"/api/rooms/{room['id']}", headers=member).status_code == 403
    client.post("/api/rooms/join", json={"token": token}, headers=member)
    assert client.get(f"/api/rooms/{room['id']}", headers=member).status_code == 200


def test_ws_rejects_non_member(client):
    room = make_room(client, "WsPrivateBank")
    import pytest

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/ws/rooms/{room['id']}", headers=[(b"x-user-email", b"spy@x.example")]
        ) as ws:
            ws.receive_json()


def test_admin_allowlist_gates_baseline_updates(client, monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("CABINET_ADMIN_EMAILS", "boss@thetaray.com")
    reset_settings_cache()
    try:
        denied = client.put(
            "/api/admin/agents/fce", json={"system_prompt": "hijacked"}
        )
        assert denied.status_code == 403

        allowed = client.put(
            "/api/admin/agents/fce",
            json={"system_prompt": "authorized update"},
            headers={"X-User-Email": "boss@thetaray.com"},
        )
        assert allowed.status_code == 200
    finally:
        monkeypatch.delenv("CABINET_ADMIN_EMAILS")
        reset_settings_cache()


def test_global_skill_applies_to_every_room(client):
    resp = client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global.md", b"# Global Watchlist Policy\nAlways screen.", "text/markdown")},
    )
    assert resp.status_code == 201
    assert resp.json()["room_id"] is None

    room = make_room(client, "GlobalSkillBank")
    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "Global Watchlist Policy" in compiled


# ---------------------------------------------------------------------------
# Finding 5 (minor): mention parser must not fire on email addresses
# ---------------------------------------------------------------------------
def test_email_addresses_are_not_mentions():
    from app.agents.prompt_compiler import parse_mention

    assert parse_mention("loop in john@fce-bank.com please") is None
    assert parse_mention("send results to data@dataexpert.io") is None
    assert parse_mention("@FCE check john@fce-bank.com's case") == "fce"
    assert parse_mention("ping @DataExpert about it") == "data_expert"


# ---------------------------------------------------------------------------
# H3: list_rooms must be scoped to the caller's memberships
# ---------------------------------------------------------------------------
def test_list_rooms_scoped_to_membership(client):
    mine = make_room(client, "MyBank")
    stranger = {"X-User-Email": "stranger@elsewhere.example"}
    client.post(
        "/api/rooms",
        json={"customer_name": "StrangerBank", "enrichment_prompt": None},
        headers=stranger,
    )

    mine_ids = {r["id"] for r in client.get("/api/rooms").json()}
    assert mine_ids == {mine["id"]}

    stranger_ids = {r["id"] for r in client.get("/api/rooms", headers=stranger).json()}
    assert mine["id"] not in stranger_ids
    assert len(stranger_ids) == 1


# ---------------------------------------------------------------------------
# H4: admin READ endpoints must be gated exactly like admin writes
# ---------------------------------------------------------------------------
def test_admin_read_endpoints_denied_for_non_admin(client, monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("CABINET_ADMIN_EMAILS", "boss@thetaray.com")
    reset_settings_cache()
    try:
        assert client.get("/api/admin/agents").status_code == 403
        assert client.get("/api/admin/agents/fce").status_code == 403
        assert client.get("/api/admin/agents/fce/skills").status_code == 403

        admin = {"X-User-Email": "boss@thetaray.com"}
        assert client.get("/api/admin/agents", headers=admin).status_code == 200
        assert client.get("/api/admin/agents/fce", headers=admin).status_code == 200
        assert client.get("/api/admin/agents/fce/skills", headers=admin).status_code == 200
    finally:
        monkeypatch.delenv("CABINET_ADMIN_EMAILS")
        reset_settings_cache()
