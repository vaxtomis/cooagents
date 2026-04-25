import pytest
from src.config import load_repos, load_settings, Settings
from src.exceptions import BadRequestError

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

def test_acpx_config_defaults():
    from src.config import Settings
    s = Settings()
    assert s.acpx.permission_mode == "approve-all"
    assert s.acpx.default_format == "json"
    assert s.acpx.ttl == 600

def test_turns_config_defaults():
    from src.config import Settings
    s = Settings()
    # Defaults enable the revise branch (turn_count starts at 1, force-accept
    # fires on ``turn >= max_turns``). 3 = initial turn + up to 2 revisions.
    assert s.turns.design_max_turns == 3
    assert s.turns.dev_max_turns == 3

def test_tracing_config_defaults():
    s = Settings()
    assert s.tracing.enabled is True
    assert s.tracing.retention_days == 7
    assert s.tracing.debug_retention_days == 3
    assert s.tracing.orphan_retention_days == 3
    assert s.tracing.cleanup_interval_hours == 24


def test_hermes_config_defaults():
    s = Settings()
    assert s.hermes.enabled is False
    assert s.hermes.deploy_skills is True
    assert s.hermes.skills_dir == "~/.hermes/skills"
    assert s.hermes.webhook.enabled is False
    assert s.hermes.webhook.url == "http://127.0.0.1:8644/webhook/cooagents"
    assert s.hermes.webhook.events == []


def test_hermes_webhook_secret_env_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_WEBHOOK_SECRET", "env-hermes-secret")
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("hermes:\n  enabled: true\n  webhook:\n    enabled: true\n")
    s = load_settings(cfg)
    assert s.hermes.webhook.secret == "env-hermes-secret"

def test_tracing_config_from_dict():
    s = Settings.model_validate({"tracing": {"enabled": False, "retention_days": 14}})
    assert s.tracing.enabled is False
    assert s.tracing.retention_days == 14
    assert s.tracing.debug_retention_days == 3

def test_agent_preference_config_defaults():
    s = Settings()
    assert s.preferred_design_agent == "claude"
    assert s.preferred_dev_agent == "claude"

def test_agent_preference_config_from_dict():
    s = Settings.model_validate({
        "preferred_design_agent": "codex",
        "preferred_dev_agent": "codex",
    })
    assert s.preferred_design_agent == "codex"
    assert s.preferred_dev_agent == "codex"


# ---- load_repos (repo-registry Phase 1) -----------------------------------

def test_load_repos_missing_file(tmp_path):
    cfg = load_repos(tmp_path / "nope.yaml")
    assert cfg.repos == []
    # Defaults are wired even when the file is absent.
    assert cfg.fetch.interval_s == 300
    assert cfg.ssh_strict_host_key is True


def test_load_repos_valid(tmp_path):
    p = tmp_path / "repos.yaml"
    p.write_text(
        "repos:\n"
        "  - name: frontend\n"
        "    url: git@github.com:org/frontend.git\n"
        "  - name: backend\n"
        "    url: git@github.com:org/backend.git\n"
        "    default_branch: develop\n"
        "    labels: [api, python]\n",
        encoding="utf-8",
    )
    cfg = load_repos(p)
    assert {r.name for r in cfg.repos} == {"frontend", "backend"}
    backend = next(r for r in cfg.repos if r.name == "backend")
    assert backend.default_branch == "develop"
    assert backend.labels == ["api", "python"]


def test_load_repos_rejects_duplicate_names(tmp_path):
    p = tmp_path / "repos.yaml"
    p.write_text(
        "repos:\n"
        "  - {name: dup, url: 'git@x:o/r.git'}\n"
        "  - {name: dup, url: 'git@x:o/r2.git'}\n",
        encoding="utf-8",
    )
    with pytest.raises(BadRequestError):
        load_repos(p)


def test_load_repos_rejects_invalid_name(tmp_path):
    p = tmp_path / "repos.yaml"
    p.write_text(
        "repos:\n  - {name: '-bad', url: 'git@x:o/r.git'}\n",
        encoding="utf-8",
    )
    with pytest.raises(BadRequestError):
        load_repos(p)


def test_load_repos_legacy_list_shape(tmp_path):
    p = tmp_path / "repos.yaml"
    p.write_text(
        "- {name: frontend, url: 'git@x:o/r.git'}\n",
        encoding="utf-8",
    )
    cfg = load_repos(p)
    assert [r.name for r in cfg.repos] == ["frontend"]


def test_load_repos_rejects_scalar(tmp_path):
    p = tmp_path / "repos.yaml"
    p.write_text("just-a-string\n", encoding="utf-8")
    with pytest.raises(BadRequestError):
        load_repos(p)
