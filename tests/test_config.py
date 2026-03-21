import pytest
from src.config import load_settings, load_agent_hosts, Settings

def test_load_settings_defaults():
    settings = load_settings()
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 8321
    assert settings.timeouts.dispatch_startup == 300

def test_load_settings_from_path(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("server:\n  host: '0.0.0.0'\n  port: 9999\n")
    settings = load_settings(cfg)
    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 9999

def test_load_agent_hosts_empty(tmp_path):
    cfg = tmp_path / "agents.yaml"
    cfg.write_text("hosts: []\n")
    hosts = load_agent_hosts(cfg)
    assert hosts == []

def test_acpx_config_defaults():
    from src.config import Settings
    s = Settings()
    assert s.acpx.permission_mode == "approve-all"
    assert s.acpx.default_format == "json"
    assert s.acpx.ttl == 600

def test_turns_config_defaults():
    from src.config import Settings
    s = Settings()
    assert s.turns.design_max_turns == 1
    assert s.turns.dev_max_turns == 1

def test_tracing_config_defaults():
    s = Settings()
    assert s.tracing.enabled is True
    assert s.tracing.retention_days == 7
    assert s.tracing.debug_retention_days == 3
    assert s.tracing.orphan_retention_days == 3
    assert s.tracing.cleanup_interval_hours == 24

def test_tracing_config_from_dict():
    s = Settings.model_validate({"tracing": {"enabled": False, "retention_days": 14}})
    assert s.tracing.enabled is False
    assert s.tracing.retention_days == 14
    assert s.tracing.debug_retention_days == 3
