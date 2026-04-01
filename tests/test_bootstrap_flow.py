from pathlib import Path


def test_bootstrap_requires_local_web_build():
    bootstrap = Path("scripts/bootstrap.sh").read_text(encoding="utf-8")

    assert "npm --version" in bootstrap
    assert "cd web" in bootstrap
    assert "npm ci" in bootstrap
    assert "npm run build" in bootstrap
    assert 'web/dist/index.html' in bootstrap


def test_bootstrap_script_uses_lf_line_endings():
    bootstrap = Path("scripts/bootstrap.sh").read_bytes()

    assert b"\r\n" not in bootstrap


def test_setup_skill_requires_dashboard_root_validation():
    skill = Path("skills/cooagents-setup/SKILL.md").read_text(encoding="utf-8")

    assert "exec curl -s http://127.0.0.1:8321/" in skill
    assert "<html" in skill


def test_upgrade_skill_requires_dashboard_root_validation():
    skill = Path("skills/cooagents-upgrade/SKILL.md").read_text(encoding="utf-8")

    assert "exec curl -s http://127.0.0.1:8321/" in skill
    assert "<html" in skill


def test_readme_describes_local_dashboard_build_in_bootstrap():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "npm ci" in readme
    assert "npm run build" in readme
    assert "http://127.0.0.1:8321/" in readme
