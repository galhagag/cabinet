"""Prompt-enrichment invariant: room context appends, never overwrites."""
from .conftest import make_room

ENRICHMENT = (
    "Customer is a Nordic neobank; SEPA instant payments only; "
    "Norwegian FSA reporting obligations apply."
)


def test_compiled_prompt_is_baseline_plus_appended_enrichment(client):
    baseline = client.get("/api/admin/agents/fce").json()["system_prompt"]
    room = make_room(client, "Nordic Neobank", enrichment=ENRICHMENT)

    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]

    # Invariant: output starts with the byte-identical global baseline.
    assert compiled.startswith(baseline.rstrip())
    # Enrichment is appended under its delimited section.
    assert "## Room Context Enrichment" in compiled
    assert ENRICHMENT in compiled
    assert compiled.index(ENRICHMENT) > len(baseline.rstrip())


def test_room_without_enrichment_gets_pure_baseline(client):
    baseline = client.get("/api/admin/agents/data_expert").json()["system_prompt"]
    room = make_room(client, "Plain Bank")
    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/data_expert/compiled-prompt"
    ).json()["compiled_prompt"]
    assert compiled == baseline.rstrip()
    assert "## Room Context Enrichment" not in compiled
