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

import logging
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import AgentGlobalConfig, AgentSkill, Message, Room
from .foundry_client import ChatTurn, LLMBackend, LLMError
from .profiles import AGENT_KEYS, DATA_EXPERT_KEY, DISPLAY_NAMES, FCE_KEY
from .prompt_compiler import SkillSection, compile_system_prompt, parse_mention

logger = logging.getLogger(__name__)

HANDOFF_TOKEN = "HANDOFF_TO_HUMAN"

PAUSED = "paused_awaiting_human"
ACTIVE = "active"


class RealtimeBroker(Protocol):
    async def publish(self, room_id: str, event: dict) -> None: ...


class Orchestrator:
    def __init__(
        self, settings: Settings, llm: LLMBackend, broker: RealtimeBroker
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._broker = broker

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
        mention = parse_mention(content)

        human_msg = Message(
            room_id=room.id,
            sender_type="human",
            sender_name=sender_name,
            mention_target=mention,
            content=content,
        )
        session.add(human_msg)

        # Human input always resets the loop budget and unpauses the room.
        # Written as an explicit UPDATE so concurrent requests contend on the
        # row rather than clobbering each other through stale ORM state.
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
        result = await self._llm.complete(
            agent_key=agent_key, system_prompt=system_prompt, turns=turns
        )
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

        result = await session.execute(
            select(AgentSkill)
            .where(
                AgentSkill.agent_key == agent_key,
                (AgentSkill.room_id == room.id) | (AgentSkill.room_id.is_(None)),
            )
            .order_by(AgentSkill.created_at)
        )
        skills = [
            SkillSection(name=s.skill_name, content=s.content_text)
            for s in result.scalars().all()
        ]
        return compile_system_prompt(
            baseline=config.system_prompt,
            skills=skills,
            enrichment=room.enrichment_prompt,
        )

    async def _history_as_turns(
        self, session: AsyncSession, room: Room, agent_key: str
    ) -> list[ChatTurn]:
        """Compile the recent room history from this agent's point of view.

        The agent's own past messages become "assistant" turns; everything
        else (humans, the other agent, system notices) becomes labeled "user"
        turns. Consecutive same-role turns are merged.
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
                role, text = "assistant", m.content
            else:
                role, text = "user", f"{m.sender_name}: {m.content}"
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
    """Ensure both expert baselines exist (idempotent, first-boot seed)."""
    from .profiles import DEFAULT_BASELINES

    for key in AGENT_KEYS:
        if await session.get(AgentGlobalConfig, key) is None:
            session.add(
                AgentGlobalConfig(
                    agent_key=key,
                    display_name=DISPLAY_NAMES[key],
                    system_prompt=DEFAULT_BASELINES[key],
                )
            )
    await session.commit()
