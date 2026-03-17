import pytest
from unittest.mock import patch
from pathlib import Path

from src.config import Settings, OpenclawConfig, OpenclawTarget
from src.skill_deployer import deploy_skills, ROOT


def _make_skill(base: Path, name: str, content: str = "# Test") -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content)
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("# Guide")
    return skill_dir


@pytest.mark.asyncio
async def test_deploy_local_copies_files(tmp_path):
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill", "---\nname: my-skill\n---\n# Body")

    target_dir = tmp_path / "openclaw-skills"

    settings = Settings(openclaw=OpenclawConfig(
        deploy_skills=True,
        targets=[OpenclawTarget(type="local", skills_dir=str(target_dir))],
    ))

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].skill_name == "my-skill"
    assert (target_dir / "my-skill" / "SKILL.md").exists()
    assert (target_dir / "my-skill" / "references" / "guide.md").exists()


@pytest.mark.asyncio
async def test_deploy_local_creates_dir(tmp_path):
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "test-skill")

    target_dir = tmp_path / "nonexistent" / "deep" / "path"

    settings = Settings(openclaw=OpenclawConfig(
        deploy_skills=True,
        targets=[OpenclawTarget(type="local", skills_dir=str(target_dir))],
    ))

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert results[0].ok is True
    assert target_dir.exists()


@pytest.mark.asyncio
async def test_deploy_local_overwrites(tmp_path):
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill", "# Updated content")

    target_dir = tmp_path / "openclaw-skills"
    # Pre-create with old content
    old = target_dir / "my-skill"
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text("# Old content")

    settings = Settings(openclaw=OpenclawConfig(
        deploy_skills=True,
        targets=[OpenclawTarget(type="local", skills_dir=str(target_dir))],
    ))

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert results[0].ok is True
    assert (target_dir / "my-skill" / "SKILL.md").read_text() == "# Updated content"


@pytest.mark.asyncio
async def test_deploy_disabled(tmp_path):
    target_dir = tmp_path / "target"
    settings = Settings(openclaw=OpenclawConfig(
        deploy_skills=False,
        targets=[OpenclawTarget(type="local", skills_dir=str(target_dir))],
    ))

    results = await deploy_skills(settings)
    assert results == []
    assert not target_dir.exists()  # Nothing written


@pytest.mark.asyncio
async def test_deploy_ssh_not_implemented(tmp_path):
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill")

    settings = Settings(openclaw=OpenclawConfig(
        deploy_skills=True,
        targets=[OpenclawTarget(type="ssh", host="remote", skills_dir="/remote/skills")],
    ))

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert len(results) == 1
    assert results[0].ok is False
    assert "not yet implemented" in results[0].error.lower()


@pytest.mark.asyncio
async def test_deploy_no_targets(tmp_path):
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill")

    settings = Settings(openclaw=OpenclawConfig(
        deploy_skills=True,
        targets=[],
    ))

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert results == []
