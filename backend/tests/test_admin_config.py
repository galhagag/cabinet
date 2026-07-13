"""Global admin configuration: baseline prompts are readable and editable."""
from .conftest import make_room


def test_defaults_seeded(client):
    resp = client.get("/api/admin/agents")
    assert resp.status_code == 200
    agents = {a["agent_key"]: a for a in resp.json()}
    assert set(agents) == {"data_expert", "fce"}
    assert "ThetaRay" in agents["data_expert"]["system_prompt"]
    assert "Financial Crime Expert" in agents["fce"]["display_name"]


def test_update_baseline_prompt(client):
    new_prompt = "You are the Data Expert. NEW GLOBAL BASELINE v2."
    resp = client.put(
        "/api/admin/agents/data_expert", json={"system_prompt": new_prompt}
    )
    assert resp.status_code == 200
    assert resp.json()["system_prompt"] == new_prompt

    # The updated baseline flows into room prompt compilation.
    room = make_room(client)
    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/data_expert/compiled-prompt"
    ).json()["compiled_prompt"]
    assert compiled.startswith(new_prompt)


def test_update_unknown_agent_404(client):
    resp = client.put("/api/admin/agents/nope", json={"system_prompt": "x"})
    assert resp.status_code == 404


def test_admin_denied_when_entra_mode_and_allowlist_empty(entra_client):
    from .conftest import install_mock_entra, make_entra_keypair, make_entra_token

    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(private_key)

    resp = entra_client.put(
        "/api/admin/agents/fce",
        json={"system_prompt": "hijacked"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
