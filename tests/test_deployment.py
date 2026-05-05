from pathlib import Path

from src.deployment import (
    _ensure_workspace_root,
    read_env_file,
    write_env_file,
)


def test_env_round_trip_preserves_shell_safe_values(tmp_path):
    env_path = tmp_path / ".env"
    write_env_file(
        env_path,
        {
            "ADMIN_USERNAME": "admin",
            "JWT_SECRET": "value with spaces",
            "AGENT_API_TOKEN": "token-value",
        },
    )

    loaded = read_env_file(env_path)

    assert loaded["ADMIN_USERNAME"] == "admin"
    assert loaded["JWT_SECRET"] == "value with spaces"
    assert loaded["AGENT_API_TOKEN"] == "token-value"


def test_ensure_workspace_root_updates_settings_yaml(tmp_path):
    (tmp_path / "config").mkdir()
    settings = tmp_path / "config" / "settings.yaml"
    settings.write_text("server:\n  host: 127.0.0.1\n", encoding="utf-8")

    _ensure_workspace_root(tmp_path, "~/custom-root")

    content = settings.read_text(encoding="utf-8")
    assert "workspace_root: ~/custom-root" in content
