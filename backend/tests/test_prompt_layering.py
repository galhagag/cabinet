"""Full prompt layering: baseline -> skills -> room enrichment -> per-agent instructions."""
from .conftest import make_room

ENRICHMENT = "Customer is a Nordic neobank; SEPA instant payments only."
INSTRUCTIONS = "For this agent: prioritize the core-banking migration timeline."
MD_SKILL = b"# Timeline Skill\nMigration cutover is Q3.\n"


def test_instructions_appear_after_enrichment_in_compiled_prompt(client):
    room = make_room(client, "LayeringBank", enrichment=ENRICHMENT)
    client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("timeline.md", MD_SKILL, "text/markdown")},
    )
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": INSTRUCTIONS},
    )

    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]

    assert "## Acquired Skills" in compiled
    assert "## Room Context Enrichment" in compiled
    assert "## Agent Instructions (this room)" in compiled

    skills_pos = compiled.index("## Acquired Skills")
    enrichment_pos = compiled.index("## Room Context Enrichment")
    instructions_pos = compiled.index("## Agent Instructions (this room)")
    assert skills_pos < enrichment_pos < instructions_pos
    assert INSTRUCTIONS in compiled


def test_no_instructions_section_when_empty(client):
    room = make_room(client, "LayeringBank2")
    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "## Agent Instructions (this room)" not in compiled
