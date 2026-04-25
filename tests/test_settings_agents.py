"""Phase 8a: AgentsConfig YAML loading + validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import AgentsConfig, load_agents
from src.exceptions import BadRequestError


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agents.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_agents_missing_file_returns_empty(tmp_path: Path) -> None:
    cfg = load_agents(tmp_path / "does-not-exist.yaml")
    assert cfg == AgentsConfig()
    assert cfg.hosts == []
    # Secure default — operators must opt out explicitly per host network.
    assert cfg.ssh_strict_host_key is True


def test_load_agents_empty_file(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, "")
    cfg = load_agents(p)
    assert cfg.hosts == []


def test_load_agents_local_only(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, "hosts:\n  - id: local\n    host: local\n")
    cfg = load_agents(p)
    assert len(cfg.hosts) == 1
    h = cfg.hosts[0]
    assert h.id == "local"
    assert h.host == "local"
    assert h.agent_type == "both"
    assert h.max_concurrent == 1


def test_load_agents_remote_ssh(tmp_path: Path) -> None:
    body = (
        "hosts:\n"
        "  - id: dev-server\n"
        "    host: dev@10.0.0.5\n"
        "    agent_type: codex\n"
        "    max_concurrent: 4\n"
        "    ssh_key: ~/.ssh/id_rsa\n"
        "    labels: [fast]\n"
    )
    cfg = load_agents(_write_yaml(tmp_path, body))
    h = cfg.hosts[0]
    assert h.host == "dev@10.0.0.5"
    assert h.max_concurrent == 4
    assert h.labels == ["fast"]
    # ~ must be expanded
    assert "~" not in (h.ssh_key or "")


def test_load_agents_invalid_host_format(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, "hosts:\n  - id: bad\n    host: not-an-ssh-spec\n")
    with pytest.raises(BadRequestError):
        load_agents(p)


def test_load_agents_duplicate_ids(tmp_path: Path) -> None:
    body = (
        "hosts:\n"
        "  - id: dup\n    host: a@h\n"
        "  - id: dup\n    host: b@h\n"
    )
    with pytest.raises(BadRequestError):
        load_agents(_write_yaml(tmp_path, body))


def test_load_agents_ssh_strict_default(tmp_path: Path) -> None:
    cfg = load_agents(_write_yaml(tmp_path, "hosts: []"))
    assert cfg.ssh_strict_host_key is True


def test_load_agents_ssh_strict_override(tmp_path: Path) -> None:
    cfg = load_agents(_write_yaml(tmp_path, "ssh_strict_host_key: false\nhosts: []"))
    assert cfg.ssh_strict_host_key is False


def test_load_agents_legacy_list_shape(tmp_path: Path) -> None:
    """Top-level list shape (just `[ ... ]` without `hosts:` wrapper) supported."""
    body = "- id: local\n  host: local\n"
    cfg = load_agents(_write_yaml(tmp_path, body))
    assert len(cfg.hosts) == 1
