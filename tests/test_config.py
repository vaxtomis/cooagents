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
