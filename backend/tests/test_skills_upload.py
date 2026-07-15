"""Dynamic skill accumulation: .md expands agent context; .zip bundles ingest."""
import io
import zipfile

from .conftest import make_room

MD_SKILL = b"""# SEPA Instant Rulebook
When defining rules for SEPA Instant, cap the decision window at 10 seconds
and treat cross-border instant credits above EUR 100k as high-risk.
"""


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _deflated_zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_md_upload_expands_compiled_prompt(client):
    room = make_room(client, "SkillBank")
    baseline = client.get("/api/admin/agents/fce").json()["system_prompt"]

    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("sepa.md", MD_SKILL, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    skill = resp.json()
    assert skill["skill_type"] == "md"
    assert skill["skill_name"] == "SEPA Instant Rulebook"

    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert compiled.startswith(baseline.rstrip())  # baseline still intact
    assert "## Acquired Skills" in compiled
    assert "cap the decision window at 10 seconds" in compiled


def test_zip_bundle_with_skill_md_is_ingested(client):
    room = make_room(client, "ZipBank")
    bundle = _zip_bytes(
        {
            "SKILL.md": b"# Parquet Landing Zone\nPartition by ingest_date.",
            "reference/layout.txt": b"raw/ staged/ curated/",
        }
    )
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/data_expert/skills",
        files={"file": ("parquet-skill.zip", bundle, "application/zip")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["skill_type"] == "zip"

    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/data_expert/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "Partition by ingest_date" in compiled

    listed = client.get(
        f"/api/rooms/{room['id']}/agents/data_expert/skills"
    ).json()
    assert len(listed) == 1
    assert listed[0]["blob_path"]


def test_zip_without_skill_md_rejected(client):
    room = make_room(client, "BadZipBank")
    bundle = _zip_bytes({"readme.txt": b"nothing here"})
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("bad.zip", bundle, "application/zip")},
    )
    assert resp.status_code == 400


def test_unsupported_extension_rejected(client):
    room = make_room(client, "ExeBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_oversized_markdown_upload_rejected(client):
    room = make_room(client, "LargeMarkdownBank")
    oversized = b"# Too Large\n" + (b"x" * 1_048_577)
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("large.md", oversized, "text/markdown")},
    )
    assert resp.status_code == 413, resp.text


def test_oversized_zip_upload_rejected_before_ingest(client):
    room = make_room(client, "LargeZipBank")
    oversized = _zip_bytes({"SKILL.md": b"# Large\n" + (b"x" * 5_242_881)})
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("large.zip", oversized, "application/zip")},
    )
    assert resp.status_code == 413, resp.text


def test_zip_with_oversized_skill_md_rejected(client):
    room = make_room(client, "ZipSkillLimitBank")
    bundle = _deflated_zip_bytes({"SKILL.md": b"# Too Large\n" + (b"x" * 1_048_577)})
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("skill-bundle.zip", bundle, "application/zip")},
    )
    assert resp.status_code == 413, resp.text


def test_zip_with_suspicious_compression_ratio_rejected(client):
    room = make_room(client, "ZipBombBank")
    bundle = _deflated_zip_bytes(
        {
            "SKILL.md": b"# Safe\nUse layered approval.",
            "payload.bin": b"x" * 2_000_000,
        }
    )
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("bomb.zip", bundle, "application/zip")},
    )
    assert resp.status_code == 422, resp.text


def test_fake_zip_extension_rejected(client):
    room = make_room(client, "FakeZipBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("fake.zip", b"not really a zip", "application/zip")},
    )
    assert resp.status_code == 400, resp.text


def test_skill_blob_persisted_to_storage(client, tmp_path):
    room = make_room(client, "BlobBank")
    client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("sepa.md", MD_SKILL, "text/markdown")},
    )
    blob_root = tmp_path / "blob"
    stored = list(blob_root.rglob("*sepa.md"))
    assert stored, "raw skill upload must be persisted to blob storage"
    assert stored[0].read_bytes() == MD_SKILL
