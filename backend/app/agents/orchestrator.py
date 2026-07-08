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

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import AgentGlobalConfig, AgentSkill, Message, Room
from .foundry_client import ChatTurn, LLMBackend
from .profiles import AGENT_KEYS, DATA_EXPERT_KEY, DISPLAY_NAMES, FCE_KEY
from .prompt_compiler import SkillSection, compile_system_prompt, parse_mention

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
        room.cycles_used = 0
        if room.status == PAUSED:
            room.status = ACTIVE
            await self._broker.publish(
                room.id, {"type": "room_resumed", "room_id": room.id}
            )
        await session.commit()
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
        reply = await self._llm.complete(
            agent_key=agent_key, system_prompt=system_prompt, turns=turns
        )
        msg = Message(
            room_id=room.id,
            sender_type="agent",
            sender_name=DISPLAY_NAMES[agent_key],
            agent_key=agent_key,
            content=reply,
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
        if room.status == PAUSED:
            return created  # server-side lockout: no agent speaks while paused

        speaker = await self._first_speaker(session, room)
        while room.cycles_used < room.cycle_limit:
            system_prompt = await self.compiled_prompt(session, room, speaker)
            turns = await self._history_as_turns(session, room, speaker)
            await self._broker.publish(
                room.id, {"type": "agent_thinking", "agent_key": speaker}
            )
            reply = await self._llm.complete(
                agent_key=speaker, system_prompt=system_prompt, turns=turns
            )

            room.cycles_used += 1
            msg = Message(
                room_id=room.id,
                sender_type="agent",
                sender_name=DISPLAY_NAMES[speaker],
                agent_key=speaker,
                cycle_number=room.cycles_used,
                content=reply,
            )
            session.add(msg)
            await session.commit()
            await self._broker.publish(room.id, self._msg_event(msg))
            created.append(msg)

            if HANDOFF_TOKEN in reply:
                break
            speaker = FCE_KEY if speaker == DATA_EXPERT_KEY else DATA_EXPERT_KEY

        if room.cycles_used >= room.cycle_limit:
            room.status = PAUSED
            await session.commit()
            await self._broker.publish(
                room.id,
                {
                    "type": "room_paused",
                    "room_id": room.id,
                    "reason": "cycle_budget_exhausted",
                    "cycles_used": room.cycles_used,
                    "cycle_limit": room.cycle_limit,
                },
            )
        return created

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
            .order_by(Message.created_at.desc(), Message.id.desc())
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
            .order_by(Message.created_at.desc(), Message.id.desc())
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
