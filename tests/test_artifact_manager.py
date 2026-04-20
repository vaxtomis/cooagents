import pytest
import os
from src.database import Database
from src.artifact_manager import ArtifactManager

@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    # Insert a dummy run so artifacts FK constraint is satisfied
    await d.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run1", "T-1", "/repo", "running", "INIT", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    yield d
    await d.close()

@pytest.fixture
async def am(db, tmp_path):
    return ArtifactManager(db, project_root=tmp_path)

async def test_register_artifact(am, tmp_path):
    # Create a fake file
    f = tmp_path / "REQ-T1.md"
    f.write_text("# Requirement\nSome content")
    aid = await am.register("run1", "req", str(f), "REQ_COLLECTING")
    assert aid is not None
    arts = await am.get_by_run("run1")
    assert len(arts) == 1
    assert arts[0]["kind"] == "req"
    assert arts[0]["version"] == 1

async def test_register_artifact_version_increment(am, tmp_path, db):
    f = tmp_path / "DES-T1.md"
    f.write_text("# Design v1")
    aid1 = await am.register("run1", "design", str(f), "DESIGN_RUNNING")
    await am.update_status(aid1, "submitted")
    await am.update_status(aid1, "rejected", review_comment="needs work")

    f.write_text("# Design v2")
    aid2 = await am.register("run1", "design", str(f), "DESIGN_RUNNING")
    arts = await am.get_by_run("run1", kind="design")
    # Should have 2 versions
    assert len(arts) == 2
    versions = sorted([a["version"] for a in arts])
    assert versions == [1, 2]

async def test_get_artifact_content(am, tmp_path):
    f = tmp_path / "REQ-T2.md"
    content = "# Requirement T2\nDetailed content here"
    f.write_text(content)
    aid = await am.register("run1", "req", str(f), "REQ_COLLECTING")
    result = await am.get_content(aid)
    assert result == content

async def test_approve_artifact(am, tmp_path):
    f = tmp_path / "DES-T1.md"
    f.write_text("# Design")
    aid = await am.register("run1", "design", str(f), "DESIGN_RUNNING")
    await am.update_status(aid, "submitted")
    await am.update_status(aid, "approved")
    arts = await am.get_by_run("run1", status="approved")
    assert len(arts) == 1

async def test_submit_all(am, tmp_path):
    f1 = tmp_path / "DES-T1.md"
    f1.write_text("# Design")
    f2 = tmp_path / "ADR-T1-001.md"
    f2.write_text("# ADR")
    await am.register("run1", "design", str(f1), "DESIGN_RUNNING")
    await am.register("run1", "adr", str(f2), "DESIGN_RUNNING")
    await am.submit_all("run1", "DESIGN_RUNNING")
    arts = await am.get_by_run("run1", status="submitted")
    assert len(arts) == 2

async def test_render_task(am, tmp_path):
    tpl = tmp_path / "template.md"
    tpl.write_text("Run: {{run_id}}\nTicket: {{ticket}}")
    out = tmp_path / "output.md"
    result = await am.render_task(str(tpl), {"run_id": "r123", "ticket": "T-1"}, str(out))
    content = out.read_text()
    assert "Run: r123" in content
    assert "Ticket: T-1" in content

async def test_render_task_jinja2(db, tmp_path):
    am = ArtifactManager(db, project_root=tmp_path)
    template = tmp_path / "template.md"
    template.write_text("# Task for {{ ticket }}\n{% if feedback %}Feedback: {{ feedback }}{% endif %}")
    output = tmp_path / "out.md"
    await am.render_task(str(template), {"ticket": "T-1", "feedback": "needs ADR"}, str(output))
    content = output.read_text()
    assert "Task for T-1" in content
    assert "Feedback: needs ADR" in content
