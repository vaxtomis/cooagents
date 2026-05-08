from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from src.deployment import (
    _ensure_workspace_root,
    integrate_hermes,
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


def test_integrate_hermes_uses_default_webhook_route(tmp_path):
    (tmp_path / "config").mkdir()
    settings = tmp_path / "config" / "settings.yaml"
    settings.write_text("hermes:\n  enabled: false\n", encoding="utf-8")
    write_env_file(
        tmp_path / ".env",
        {
            "AGENT_API_TOKEN": "agent-token",
            "HERMES_WEBHOOK_SECRET": "hermes-secret",
        },
    )
    hermes_env = tmp_path / "hermes.env"
    hermes_config = tmp_path / "hermes.yaml"

    def fake_run_checked(cmd, **_kwargs):
        if cmd == ["hermes", "config", "env-path"]:
            return SimpleNamespace(stdout=str(hermes_env))
        if cmd == ["hermes", "config", "path"]:
            return SimpleNamespace(stdout=str(hermes_config))
        return SimpleNamespace(stdout="")

    with (
        patch("src.deployment._require_cmd"),
        patch("src.deployment._run_checked", side_effect=fake_run_checked),
        patch("src.deployment._run", return_value=SimpleNamespace(returncode=0)),
    ):
        integrate_hermes(
            tmp_path,
            agent_api_token="agent-token",
            restart_service_after=False,
        )

    saved = yaml.safe_load(settings.read_text(encoding="utf-8"))
    route = saved["hermes"]["webhook"]["url"]

    assert route == "http://127.0.0.1:8644/webhook/cooagents"
