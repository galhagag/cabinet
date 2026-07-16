"""Owner-only room archive/unarchive/delete lifecycle."""
from datetime import datetime

from .conftest import make_room


def _parse(iso: str) -> datetime:
    # SQLite drops tzinfo on read, so a freshly-set value ("...Z") and a
    # value re-fetched from the DB ("..." naive) can differ only in
    # suffix — compare as instants, not raw strings.
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)

MEMBER_EMAIL = "kate@bank.example"


def _join_as_member(client, room_id: str) -> None:
    invite = client.post(f"/api/rooms/{room_id}/invites")
    token = invite.json()["token"]
    joined = client.post(
        "/api/rooms/join",
        json={"token": token, "display_name": "Compliance Kate"},
        headers={"X-User-Email": MEMBER_EMAIL},
    )
    assert joined.status_code == 200


def test_owner_can_archive_and_unarchive(client):
    room = make_room(client, "ArchiveBank")
    assert room["role"] == "owner"
    assert room["archived_at"] is None

    archived = client.post(f"/api/rooms/{room['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    # Archived rooms stay in the list (frontend buckets them) but carry the timestamp.
    listed = {r["id"]: r for r in client.get("/api/rooms").json()}
    assert listed[room["id"]]["archived_at"] is not None

    unarchived = client.post(f"/api/rooms/{room['id']}/unarchive")
    assert unarchived.status_code == 200
    assert unarchived.json()["archived_at"] is None


def test_archive_is_idempotent(client):
    room = make_room(client, "IdempotentBank")
    first = client.post(f"/api/rooms/{room['id']}/archive").json()
    second = client.post(f"/api/rooms/{room['id']}/archive").json()
    assert _parse(first["archived_at"]) == _parse(second["archived_at"])


def test_non_owner_member_cannot_archive_or_delete(client):
    room = make_room(client, "MemberBank")
    _join_as_member(client, room["id"])

    archive_resp = client.post(
        f"/api/rooms/{room['id']}/archive", headers={"X-User-Email": MEMBER_EMAIL}
    )
    assert archive_resp.status_code == 403

    delete_resp = client.delete(
        f"/api/rooms/{room['id']}", headers={"X-User-Email": MEMBER_EMAIL}
    )
    assert delete_resp.status_code == 403

    # Member's own room listing shows their non-owner role.
    listed = {r["id"]: r for r in client.get("/api/rooms", headers={"X-User-Email": MEMBER_EMAIL}).json()}
    assert listed[room["id"]]["role"] == "member"


def test_non_member_gets_403_not_404(client):
    room = make_room(client, "StrangerBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/archive", headers={"X-User-Email": "stranger@nowhere.example"}
    )
    assert resp.status_code == 403


def test_archive_unknown_room_404(client):
    resp = client.post("/api/rooms/not-a-real-id/archive")
    assert resp.status_code == 404


def test_owner_can_delete_room(client):
    room = make_room(client, "DeleteBank")
    resp = client.delete(f"/api/rooms/{room['id']}")
    assert resp.status_code == 204

    assert client.get(f"/api/rooms/{room['id']}").status_code == 404
    ids = {r["id"] for r in client.get("/api/rooms").json()}
    assert room["id"] not in ids


def test_deleted_room_cannot_be_archived(client):
    room = make_room(client, "GoneBank")
    client.delete(f"/api/rooms/{room['id']}")
    resp = client.post(f"/api/rooms/{room['id']}/archive")
    assert resp.status_code == 404
