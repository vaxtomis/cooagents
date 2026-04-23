import subprocess
import sys
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


def test_generate_password_hash_script_emits_real_env_values():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "generate_password_hash.py"
    # Support both POSIX (.venv/bin/python) and Windows (.venv/Scripts/python.exe)
    # venv layouts; fall back to the interpreter running the tests if no venv.
    candidates = [
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    ]
    python = next((p for p in candidates if p.exists()), Path(sys.executable))
    proc = subprocess.run(
        [str(python), str(script), "--username", "admin", "--password", "hunter22"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    env_lines = {}
    for line in proc.stdout.splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        env_lines[key] = value.strip().strip("'")

    assert env_lines["ADMIN_USERNAME"] == "admin"
    assert env_lines["ADMIN_PASSWORD_HASH"].startswith("$argon2")
    assert env_lines["JWT_SECRET"]
    assert env_lines["AGENT_API_TOKEN"]

def test_setup_skill_uses_hermes_webhooks_route():
    skill = Path("skills/cooagents-setup/SKILL.md").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8644/webhooks/cooagents" in skill
    assert "http://127.0.0.1:8644/webhook/cooagents" not in skill


def test_readme_describes_local_dashboard_build_in_bootstrap():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "npm ci" in readme
    assert "npm run build" in readme
    assert "http://127.0.0.1:8321/" in readme
