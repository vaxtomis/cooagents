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
    # Public deployment binds localhost only; HTTPS is added by a reverse proxy.
    assert "--host 127.0.0.1 --port 8321" in skill
    assert "<html" in skill


def test_upgrade_skill_requires_dashboard_root_validation():
    skill = Path("skills/cooagents-upgrade/SKILL.md").read_text(encoding="utf-8")

    assert "exec curl -s http://127.0.0.1:8321/" in skill
    assert "--host 127.0.0.1 --port 8321" in skill
    assert "<html" in skill


def test_setup_skill_mandates_auth_env_generation():
    """Setup must walk the user through generating the required auth env vars."""
    skill = Path("skills/cooagents-setup/SKILL.md").read_text(encoding="utf-8")
    assert "generate_password_hash.py" in skill
    assert "ADMIN_PASSWORD_HASH" in skill
    assert "JWT_SECRET" in skill
    assert "AGENT_API_TOKEN" in skill


def test_workflow_skill_uses_agent_token_header():
    """All curl calls in the workflow skill must carry X-Agent-Token."""
    for path in [
        "skills/cooagents-workflow/SKILL.md",
        "skills/cooagents-workflow/references/api-playbook.md",
        "skills/cooagents-workflow/references/error-handling.md",
    ]:
        text = Path(path).read_text(encoding="utf-8")
        assert "X-Agent-Token: $AGENT_API_TOKEN" in text, path


def test_workflow_skill_no_longer_sends_by_field():
    """approve/reject/retry/resolve-conflict payloads must not carry `by`."""
    skill = Path("skills/cooagents-workflow/SKILL.md").read_text(encoding="utf-8")
    # No JSON payload in the skill should include a "by" key.
    assert '"by":"' not in skill
    assert '"by": "' not in skill


def test_readme_describes_local_dashboard_build_in_bootstrap():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "npm ci" in readme
    assert "npm run build" in readme
    assert "http://127.0.0.1:8321/" in readme
