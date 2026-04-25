"""Phase 8b: WorkerConfig env-var loader."""
from __future__ import annotations

import pytest

from src.agent_worker.config import WorkerConfig, WorkerConfigError


def _base_env() -> dict[str, str]:
    return {
        "COOAGENTS_URL": "https://control.example.com",
        "COOAGENTS_AGENT_TOKEN": "x" * 32,
        "WORKSPACES_ROOT": "/var/lib/cooagents/workspaces",
        "OSS_BUCKET": "my-bucket",
        "OSS_REGION": "cn-shanghai",
        "OSS_ACCESS_KEY_ID": "AKID",
        "OSS_ACCESS_KEY_SECRET": "AKSECRET",
    }


def test_loads_minimal_env():
    cfg = WorkerConfig.from_env(_base_env())
    assert cfg.cooagents_url == "https://control.example.com"
    # Path normalises separators per platform — compare as Path.
    from pathlib import Path
    assert cfg.workspaces_root == Path("/var/lib/cooagents/workspaces")
    assert cfg.oss.bucket == "my-bucket"
    assert cfg.oss.prefix == ""


def test_strips_trailing_slash_on_url():
    env = _base_env()
    env["COOAGENTS_URL"] = "https://x.example.com/"
    cfg = WorkerConfig.from_env(env)
    assert cfg.cooagents_url == "https://x.example.com"


def test_normalises_oss_prefix_trailing_slash():
    env = _base_env()
    env["OSS_PREFIX"] = "shared"
    cfg = WorkerConfig.from_env(env)
    assert cfg.oss.prefix == "shared/"


def test_token_must_be_at_least_32_chars():
    env = _base_env()
    env["COOAGENTS_AGENT_TOKEN"] = "short"
    with pytest.raises(WorkerConfigError):
        WorkerConfig.from_env(env)


def test_missing_required_env_listed():
    env = _base_env()
    del env["OSS_BUCKET"]
    del env["WORKSPACES_ROOT"]
    with pytest.raises(WorkerConfigError) as exc:
        WorkerConfig.from_env(env)
    assert "OSS_BUCKET" in str(exc.value)
    assert "WORKSPACES_ROOT" in str(exc.value)
