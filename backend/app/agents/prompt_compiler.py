"""System-prompt compilation.

Invariant (enforced by tests): the compiled prompt always *starts with the
unmodified global baseline*. Skills and room enrichment are appended in
clearly-delimited sections — UI-supplied context can enrich, never overwrite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .profiles import MENTION_ALIASES

SKILLS_HEADER = "## Acquired Skills"
ENRICHMENT_HEADER = "## Room Context Enrichment"


@dataclass(frozen=True)
class SkillSection:
    name: str
    content: str


def compile_system_prompt(
    baseline: str,
    skills: list[SkillSection] | None = None,
    enrichment: str | None = None,
) -> str:
    """baseline ⊕ skills ⊕ enrichment, append-only."""
    parts: list[str] = [baseline.rstrip()]

    if skills:
        skill_blocks = "\n\n".join(
            f"### Skill: {s.name}\n{s.content.strip()}" for s in skills
        )
        parts.append(f"{SKILLS_HEADER}\n{skill_blocks}")

    if enrichment and enrichment.strip():
        parts.append(
            f"{ENRICHMENT_HEADER}\n"
            "The following room-specific context ENRICHES the instructions "
            "above. It adds customer detail and never overrides your baseline "
            "role or responsibilities.\n\n" + enrichment.strip()
        )

    return "\n\n".join(parts)


_MENTION_RE = re.compile(r"@([A-Za-z_]+)")


def parse_mention(content: str) -> str | None:
    """Return the agent_key targeted by the first recognized @-mention."""
    for match in _MENTION_RE.finditer(content):
        key = MENTION_ALIASES.get(match.group(1).lower())
        if key:
            return key
    return None
