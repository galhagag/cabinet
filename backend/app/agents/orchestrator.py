"""Multi-agent orchestrator.

Owns the two behaviors at the heart of the Cabinet:

1. **@-mention routing** — a tagged message goes exclusively to that agent,
   which produces exactly one targeted reply compiled from the surrounding
   room history.

2. **Autonomous agent-to-agent loop with a hard budget** — without a mention,
   the Data Expert and FCE alternate turns. Each agent turn consumes one
   *cycle*; when ``room.cycles_used`` reaches ``room.cycle_limit`` (product
   default: 6) the room flips to ``paused_awaiting_human`` and agents are
   locked out until a human posts. A new human message resets the budget and
   reactivates the room. An agent can also end the exchange early by emitting
   the ``HANDOFF_TO_HUMAN`` token.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Protocol

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import AgentGlobalConfig, AgentSkill, Message, Room, RoomAgent, RoomSkillOverride
from .foundry_client import ChatTurn, LLMBackend, LLMError, strip_mock_tag
from .profiles import AGENT_KEYS, DATA_EXPERT_KEY, DISPLAY_NAMES, FCE_KEY
from .prompt_compiler import SkillSection, compile_system_prompt, parse_mention

logger = logging.getLogger(__name__)

HANDOFF_TOKEN = "HANDOFF_TO_HUMAN"

PAUSED = "paused_awaiting_human"
ACTIVE = "active"


class RealtimeBroker(Protocol):
    async def publish(self, room_id: str, event: dict) -> None: ...
    async def client_access(self, room_id: str, user_email: str) -> dict: ...


def _escape_participant_markup(value: str) -> str:
    """Neutralize every character an untrusted turn could use to fabricate,
    prematurely close, or break out of a <participant> tag. Escaping the
    whole class of markup characters ('<', '>', '"') — rather than only the
    literal, exact-case '<participant' / '</participant' substrings — also
    defeats case (<PARTICIPANT>), whitespace (< participant>), and other
    tag-name variants, since no literal '<' or '>' survives at all."""
    return value.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _wrap_participant(name: str, content: str) -> str:
    """Frame an untrusted turn so it can never be mistaken for the model's
    own output or re-open the framing early. Strips control chars from the
    name, then neutralizes markup-forging characters in BOTH the name and
    the content: the name interpolates directly into the ``name="..."``
    attribute, so an unescaped name is just as capable of fabricating a
    nested <participant> block (or breaking out of the attribute quote) as
    unescaped content is (Design 06 / H14)."""
    safe_name = _escape_participant_markup(
        re.sub(r"[\r\n\x00-\x1f]", " ", name).strip()
    )
    safe_content = _escape_participant_markup(content)
    return f'<participant name="{safe_name}">\n{safe_content}\n</participant>'


class Orchestrator:
    def __init__(
        self, settings: Settings, llm: LLMBackend, broker: RealtimeBroker
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._broker = broker
        self._room_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def room_lock(self, room_id: str) -> asyncio.Lock:
        """One lock per room, serializing the entire human-message→loop and
        resume→loop critical sections so two concurrent entry points into the
        same room's autonomous loop can never both be mid-flight *within this
        process* (Design 02 Stage 2 / H5). In-process only — on a single
        replica this is sufficient on its own.

        On multiple replicas this lock provides no protection at all (each
        replica has its own `_room_locks` dict in its own memory); callers
        additionally take `acquire_replica_lock` at the top of each
        transaction they want to defend against a same-room collision from
        another replica. That only covers the specific transaction it's
        called within — see `acquire_replica_lock` for why it does *not*
        extend across this whole critical section.
        """
        return self._room_locks[room_id]

    async def acquire_replica_lock(self, session: AsyncSession, room_id: str) -> None:
        """No-op on SQLite (tests); on Postgres, takes a transaction-scoped
        advisory lock (`pg_advisory_xact_lock`) so a same-keyed call from
        another replica blocks until this transaction commits or rolls back.

        Caution: this protects only the transaction it's called within, not
        the caller's whole critical section. `pg_advisory_xact_lock` is
        released automatically at the *next* commit/rollback on this
        session — and both `handle_human_message` and `run_autonomous_loop`
        commit repeatedly (once per turn) before returning. So on a
        multi-replica deployment this call, taken once at the top of
        `handle_human_message` or `resume_room`, only guards the initial
        paused→active/cycle-reset transition against a same-room collision
        from another replica; it does NOT make the subsequent multi-turn
        loop mutually exclusive across replicas (that requires either a
        single long-lived transaction — incompatible with committing each
        turn as it happens — or a distributed loop-ownership record, i.e.
        Design 02 Stage 3's outbox `locked_by`/`locked_at`, which does not
        exist yet). Treat it as defense-in-depth for the claim step only,
        not a substitute for Stage 3 on a multi-replica deployment.
        """
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:room_id))"),
                {"room_id": room_id},
            )

    # ------------------------------------------------------------------
    # Entry point: a human posted a message
    # ------------------------------------------------------------------
    async def handle_human_message(
        self, session: AsyncSession, room: Room, sender_name: str, content: str
    ) -> list[Message]:
        """Persist the human message, then drive the agents.

        Returns every message created during this interaction (human + agent),
        in order.
        """
        async with self.room_lock(room.id):
            await self.acquire_replica_lock(session, room.id)
            mention = parse_mention(content)

            human_msg = Message(
                room_id=room.id,
                sender_type="human",
                sender_name=sender_name,
                mention_target=mention,
                content=content,
            )
            session.add(human_msg)

            # Freshly read under the lock: no concurrent handle_human_message
            # or resume_room can be mid-transition on this room right now, so
            # this reflects the true committed state (fixes the duplicate
            # room_resumed Low — previously read from a pre-UPDATE ORM object
            # that could already be stale under concurrency).
            await session.refresh(room)
            was_paused = room.status == PAUSED
            await session.execute(
                update(Room)
                .where(Room.id == room.id)
                .values(cycles_used=0, status=ACTIVE)
            )
            await session.commit()
            await session.refresh(room)
            if was_paused:
                await self._broker.publish(
                    room.id, {"type": "room_resumed", "room_id": room.id}
                )
            await self._broker.publish(room.id, self._msg_event(human_msg))

            created = [human_msg]
            if mention:
                created += await self._run_mention_reply(session, room, mention)
            else:
                created += await self.run_autonomous_loop(session, room)
            return created

    # ------------------------------------------------------------------
    # Mention routing: exactly one targeted reply
    # ------------------------------------------------------------------
    async def _run_mention_reply(
        self, session: AsyncSession, room: Room, agent_key: str
    ) -> list[Message]:
        system_prompt = await self.compiled_prompt(session, room, agent_key)
        turns = await self._history_as_turns(session, room, agent_key)
        await self._broker.publish(
            room.id, {"type": "agent_thinking", "agent_key": agent_key}
        )
        try:
            result = await self._llm.complete(
                agent_key=agent_key, system_prompt=system_prompt, turns=turns
            )
        except LLMError as exc:
            fail_msg = await self._fail_turn(session, room, agent_key, exc)
            return [fail_msg]
        msg = Message(
            room_id=room.id,
            sender_type="agent",
            sender_name=DISPLAY_NAMES[agent_key],
            agent_key=agent_key,
            content=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        session.add(msg)
        await session.commit()
        await self._broker.publish(room.id, self._msg_event(msg))
        return [msg]

    # ------------------------------------------------------------------
    # Autonomous agent-to-agent loop, hard-capped by the cycle budget
    # ------------------------------------------------------------------
    async def run_autonomous_loop(
        self, session: AsyncSession, room: Room
    ) -> list[Message]:
        created: list[Message] = []

        speaker = await self._first_speaker(session, room)
        while True:
            # Atomically claim one cycle from the shared budget. The WHERE
            # clause is the server-side enforcement: a paused room or an
            # exhausted budget claims nothing, no matter how many requests
            # (or replicas) run this loop concurrently.
            cycle = await self._claim_cycle(session, room)
            if cycle is None:
                break

            system_prompt = await self.compiled_prompt(session, room, speaker)
            turns = await self._history_as_turns(session, room, speaker)
            await self._broker.publish(
                room.id, {"type": "agent_thinking", "agent_key": speaker}
            )
            try:
                result = await self._llm.complete(
                    agent_key=speaker, system_prompt=system_prompt, turns=turns
                )
            except LLMError as exc:
                fail_msg = await self._fail_turn(session, room, speaker, exc)
                created.append(fail_msg)
                break

            msg = Message(
                room_id=room.id,
                sender_type="agent",
                sender_name=DISPLAY_NAMES[speaker],
                agent_key=speaker,
                cycle_number=cycle,
                content=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            session.add(msg)
            await session.commit()
            await self._broker.publish(room.id, self._msg_event(msg))
            created.append(msg)

            if HANDOFF_TOKEN in result.text:
                break
            speaker = FCE_KEY if speaker == DATA_EXPERT_KEY else DATA_EXPERT_KEY

        await self._pause_if_exhausted(session, room)
        await session.refresh(room)
        return created

    async def _fail_turn(
        self, session: AsyncSession, room: Room, agent_key: str, exc: Exception
    ) -> Message:
        """An LLM call failed mid-loop.

        The cycle was already claimed before the call, so without this the
        room is stranded ACTIVE at an exhausted budget — no agent can ever
        speak again and /resume 409s (Design 02 / C2). Leave a visible system
        notice, pause the room so /resume works, and tell clients the
        pending typing indicator is done.
        """
        logger.warning(
            "LLM completion failed for %s in room %s: %s", agent_key, room.id, exc
        )
        msg = Message(
            room_id=room.id,
            sender_type="system",
            sender_name="System",
            content=(
                f"⚠️ {DISPLAY_NAMES[agent_key]} could not respond (upstream error). "
                "The room is paused — resume to retry."
            ),
        )
        session.add(msg)
        await session.execute(
            update(Room)
            .where(Room.id == room.id, Room.status == ACTIVE)
            .values(status=PAUSED)
        )
        await session.commit()
        await self._broker.publish(room.id, self._msg_event(msg))
        await self._broker.publish(
            room.id,
            {"type": "agent_error", "agent_key": agent_key, "recoverable": True},
        )
        return msg

    async def _claim_cycle(self, session: AsyncSession, room: Room) -> int | None:
        """Atomically consume one cycle; None when paused or budget exhausted."""
        result = await session.execute(
            update(Room)
            .where(
                Room.id == room.id,
                Room.status == ACTIVE,
                Room.cycles_used < Room.cycle_limit,
            )
            .values(cycles_used=Room.cycles_used + 1)
            .returning(Room.cycles_used)
        )
        claimed = result.scalar_one_or_none()
        await session.commit()
        return claimed

    async def _pause_if_exhausted(self, session: AsyncSession, room: Room) -> None:
        """Transition active→paused exactly once when the budget is spent."""
        result = await session.execute(
            update(Room)
            .where(
                Room.id == room.id,
                Room.status == ACTIVE,
                Room.cycles_used >= Room.cycle_limit,
            )
            .values(status=PAUSED)
            .returning(Room.cycles_used, Room.cycle_limit)
        )
        row = result.one_or_none()
        await session.commit()
        if row is not None:
            await self._broker.publish(
                room.id,
                {
                    "type": "room_paused",
                    "room_id": room.id,
                    "reason": "cycle_budget_exhausted",
                    "cycles_used": row.cycles_used,
                    "cycle_limit": row.cycle_limit,
                },
            )

    # ------------------------------------------------------------------
    # Prompt & history compilation
    # ------------------------------------------------------------------
    async def compiled_prompt(
        self, session: AsyncSession, room: Room, agent_key: str
    ) -> str:
        config = await session.get(AgentGlobalConfig, agent_key)
        if config is None:
            raise ValueError(f"unknown agent: {agent_key}")

        room_agent_result = await session.execute(
            select(RoomAgent.instructions).where(
                RoomAgent.room_id == room.id, RoomAgent.agent_key == agent_key
            )
        )
        instructions = room_agent_result.scalar_one_or_none() or ""

        result = await session.execute(
            select(AgentSkill)
            .where(
                AgentSkill.agent_key == agent_key,
                (AgentSkill.room_id == room.id) | (AgentSkill.room_id.is_(None)),
            )
            .order_by(AgentSkill.created_at)
        )
        all_skills = result.scalars().all()

        # Fetch disabled skill IDs for this room
        overrides = await session.execute(
            select(RoomSkillOverride.skill_id).where(RoomSkillOverride.room_id == room.id)
        )
        disabled_ids = set(overrides.scalars().all())

        # Filter out disabled skills
        skills = [
            SkillSection(name=s.skill_name, content=s.content_text)
            for s in all_skills
            if s.id not in disabled_ids
        ]
        return compile_system_prompt(
            baseline=config.system_prompt,
            skills=skills,
            enrichment=room.enrichment_prompt,
            instructions=instructions,
        )

    async def _history_as_turns(
        self, session: AsyncSession, room: Room, agent_key: str
    ) -> list[ChatTurn]:
        """Compile the recent room history from this agent's point of view.

        The agent's own past messages become "assistant" turns — the only
        role the model should ever treat as itself. Every other message
        (human or the other agent) is wrapped in a <participant> block so a
        member forging a line that mimics the other expert's speaker prefix
        cannot appear indistinguishable from a genuine turn (Design 06 / H14).

        Any stale "[key·mock]" tag is stripped from the agent's own past
        turns before replay. A room's history can span a MockLLM→real-backend
        transition (e.g. a CABINET_LLM_MODE flip); without stripping, a real
        model sees its own prior "assistant" turns literally start with that
        tag and imitates it as an established reply-prefix convention,
        perpetuating a mock-looking tag in genuine output indefinitely.
        """
        result = await session.execute(
            select(Message)
            .where(Message.room_id == room.id)
            .order_by(Message.seq.desc(), Message.id.desc())
            .limit(self._settings.history_window)
        )
        history = list(reversed(result.scalars().all()))

        turns: list[ChatTurn] = []
        for m in history:
            if m.agent_key == agent_key:
                role, text = "assistant", strip_mock_tag(m.content)
            else:
                role = "user"
                text = _wrap_participant(m.sender_name, m.content)
            if turns and turns[-1].role == role:
                turns[-1] = ChatTurn(role=role, content=turns[-1].content + "\n" + text)
            else:
                turns.append(ChatTurn(role=role, content=text))

        if not turns or turns[0].role != "user":
            turns.insert(
                0,
                ChatTurn(
                    role="user",
                    content="(onboarding workspace opened — begin collaboration)",
                ),
            )
        if turns[-1].role == "assistant":
            turns.append(
                ChatTurn(role="user", content="(continue the onboarding discussion)")
            )
        return turns

    async def _first_speaker(self, session: AsyncSession, room: Room) -> str:
        """Alternate: whoever did NOT speak last goes first; default Data Expert."""
        result = await session.execute(
            select(Message.agent_key)
            .where(Message.room_id == room.id, Message.sender_type == "agent")
            .order_by(Message.seq.desc(), Message.id.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        if last == DATA_EXPERT_KEY:
            return FCE_KEY
        return DATA_EXPERT_KEY

    @staticmethod
    def _msg_event(m: Message) -> dict:
        return {
            "type": "message_created",
            "message": {
                "id": m.id,
                "room_id": m.room_id,
                "sender_type": m.sender_type,
                "sender_name": m.sender_name,
                "agent_key": m.agent_key,
                "mention_target": m.mention_target,
                "cycle_number": m.cycle_number,
                "content": m.content,
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            },
        }


async def seed_global_config(session: AsyncSession) -> None:
    """Ensure both expert baselines exist (idempotent, race-safe upsert —
    concurrent replicas cold-booting can no longer crash each other on a PK
    IntegrityError, Design 05 / H13)."""
    from .profiles import DEFAULT_BASELINES

    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

    for key in AGENT_KEYS:
        stmt = (
            dialect_insert(AgentGlobalConfig)
            .values(
                agent_key=key,
                display_name=DISPLAY_NAMES[key],
                system_prompt=DEFAULT_BASELINES[key],
            )
            .on_conflict_do_nothing(index_elements=["agent_key"])
        )
        await session.execute(stmt)
    await session.commit()
