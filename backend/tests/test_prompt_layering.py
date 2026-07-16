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


def test_baseline_documents_the_handoff_token(client):
    """The autonomous loop only exits early when an agent's reply contains
    HANDOFF_TO_HUMAN (Orchestrator.run_autonomous_loop). A baseline that never
    mentions the token gives the model no way to know that mechanism exists,
    so every unmentioned message — including a bare "good morning" — burns
    the full cycle budget instead of closing out early."""
    room = make_room(client, "LayeringBank3")
    for agent_key in ("data_expert", "fce"):
        compiled = client.get(
            f"/api/rooms/{room['id']}/agents/{agent_key}/compiled-prompt"
        ).json()["compiled_prompt"]
        assert "HANDOFF_TO_HUMAN" in compiled
