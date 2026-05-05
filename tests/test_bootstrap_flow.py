import subprocess
import sys
from pathlib import Path


def test_bootstrap_script_delegates_to_unified_cli():
    bootstrap = Path("scripts/bootstrap.sh").read_text(encoding="utf-8")

    assert "scripts/deploy.py bootstrap" in bootstrap


def test_bootstrap_script_uses_lf_line_endings():
    bootstrap = Path("scripts/bootstrap.sh").read_bytes()

    assert b"\r\n" not in bootstrap


def test_setup_skill_uses_unified_setup_command():
    skill = Path("skills/cooagents-setup/SKILL.md").read_text(encoding="utf-8")

    assert "python scripts/deploy.py setup" in skill
    assert "--runtime" in skill
    assert "python scripts/deploy.py integrate-runtime" in skill


def test_upgrade_skill_uses_unified_upgrade_command():
    skill = Path("skills/cooagents-upgrade/SKILL.md").read_text(encoding="utf-8")

    assert "python scripts/deploy.py upgrade" in skill
    assert "python scripts/deploy.py sync-skills" in skill


def test_setup_skill_mandates_auth_env_generation():
    skill = Path("skills/cooagents-setup/SKILL.md").read_text(encoding="utf-8")

    assert "AGENT_API_TOKEN" in skill
    assert "--admin-password" in skill
    assert "repo-first" in skill.lower()


def test_generate_password_hash_script_emits_real_env_values():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "generate_password_hash.py"
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
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_lines[key] = value.strip().strip("'")

    assert env_lines["ADMIN_USERNAME"] == "admin"
    assert env_lines["ADMIN_PASSWORD_HASH"].startswith("$argon2")
    assert env_lines["JWT_SECRET"]
    assert env_lines["AGENT_API_TOKEN"]


def test_readme_points_to_unified_setup_path():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "python scripts/deploy.py setup" in readme
    assert "./scripts/bootstrap.sh" in readme
    assert "/cooagents-setup" in readme
