import pytest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
import sys

from src.config import (
    HermesConfig,
    HermesWebhookConfig,
    OpenclawConfig,
    OpenclawTarget,
    Settings,
)
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
async def test_deploy_ssh_target_uses_asyncssh_copy(tmp_path):
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill")

    calls: list[tuple] = []

    class _FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def run(self, cmd, check=False):
            calls.append(("run", cmd, check))
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

    class _FakeAsyncSSH:
        def connect(self, **kwargs):
            calls.append(("connect", kwargs))
            return _FakeConn()

        async def scp(self, src, dest, recurse=False):
            calls.append(("scp", src, dest, recurse))

    settings = Settings(openclaw=OpenclawConfig(
        deploy_skills=True,
        targets=[OpenclawTarget(type="ssh", host="remote", skills_dir="/remote/skills")],
    ))

    with patch("src.skill_deployer.ROOT", tmp_path), patch.dict(sys.modules, {"asyncssh": _FakeAsyncSSH()}):
        results = await deploy_skills(settings)

    assert len(results) == 1
    assert results[0].ok is True
    assert any(call[0] == "connect" for call in calls)
    assert any(call[0] == "scp" for call in calls)


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


@pytest.mark.asyncio
async def test_deploy_hermes_target_copies_files(tmp_path):
    """When hermes is enabled, the same skill bundle lands in the hermes dir."""
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill", "---\nname: my-skill\n---\n# Body")

    hermes_dir = tmp_path / "hermes-skills"

    settings = Settings(
        openclaw=OpenclawConfig(deploy_skills=False, targets=[]),
        hermes=HermesConfig(
            enabled=True,
            deploy_skills=True,
            skills_dir=str(hermes_dir),
            webhook=HermesWebhookConfig(enabled=False),
        ),
    )

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].target_type == "hermes"
    assert (hermes_dir / "my-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_deploy_dual_runtime(tmp_path):
    """Deploying to OpenClaw + Hermes fans out to both destinations."""
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill")

    openclaw_dir = tmp_path / "openclaw-skills"
    hermes_dir = tmp_path / "hermes-skills"

    settings = Settings(
        openclaw=OpenclawConfig(
            deploy_skills=True,
            targets=[OpenclawTarget(type="local", skills_dir=str(openclaw_dir))],
        ),
        hermes=HermesConfig(
            enabled=True,
            deploy_skills=True,
            skills_dir=str(hermes_dir),
        ),
    )

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert {r.target_type for r in results} == {"local", "hermes"}
    assert all(r.ok for r in results)
    assert (openclaw_dir / "my-skill" / "SKILL.md").exists()
    assert (hermes_dir / "my-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_deploy_hermes_disabled(tmp_path):
    """hermes.enabled=False keeps the hermes dir untouched."""
    src_skills = tmp_path / "skills"
    src_skills.mkdir()
    _make_skill(src_skills, "my-skill")

    hermes_dir = tmp_path / "hermes-skills"

    settings = Settings(
        openclaw=OpenclawConfig(deploy_skills=False, targets=[]),
        hermes=HermesConfig(
            enabled=False,
            deploy_skills=True,
            skills_dir=str(hermes_dir),
        ),
    )

    with patch("src.skill_deployer.ROOT", tmp_path):
        results = await deploy_skills(settings)

    assert results == []
    assert not hermes_dir.exists()
