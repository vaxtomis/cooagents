"""Round-robin agent host selector with local fallback (Phase 8a).

The counter is intentionally process-local: cooagents is a single-process
service, and host selection is best-effort load spreading, not a hard
contract. A process restart resetting the counter is acceptable in Phase 8.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from src.models import LOCAL_HOST_ID

if TYPE_CHECKING:  # avoid runtime cycle: repo imports config which imports models
    from src.agent_hosts.repo import AgentHostRepo

# (agent_type, frozenset(labels)) → next-index counter.
_RR_COUNTERS: dict[tuple[str, frozenset[str]], int] = {}
_EXEC_AGENT_TYPES: tuple[str, ...] = ("codex", "claude")


def _matches(host_row: dict, agent_type: str, labels: frozenset[str]) -> bool:
    if host_row["agent_type"] not in (agent_type, "both"):
        return False
    if host_row["health_status"] != "healthy":
        return False
    have = set(host_row.get("labels") or [])
    return labels.issubset(have)


def _supports_agent(host_row: dict, agent_type: str) -> bool:
    return host_row["agent_type"] in (agent_type, "both")


def configured_agent_types(hosts: Iterable[dict]) -> set[str]:
    """Return execution tools advertised by Agent Host config rows.

    This intentionally ignores health status: it answers "is this tool
    configured anywhere?", not "did a probe recently pass?".
    """
    out: set[str] = set()
    for host in hosts:
        agent_type = host.get("agent_type")
        if agent_type == "both":
            out.update(_EXEC_AGENT_TYPES)
        elif agent_type in _EXEC_AGENT_TYPES:
            out.add(agent_type)
    return out


def resolve_configured_agent(
    hosts: Iterable[dict],
    requested: str | None,
    *,
    preferred: str | None = None,
) -> str:
    """Resolve the execution tool using only Agent Host configuration."""
    configured = configured_agent_types(hosts)
    candidates = [requested, preferred, *_EXEC_AGENT_TYPES]
    if configured:
        for candidate in candidates:
            if candidate in configured:
                return candidate
        return sorted(configured)[0]
    for candidate in candidates:
        if candidate in _EXEC_AGENT_TYPES:
            return candidate
    return _EXEC_AGENT_TYPES[0]


async def choose_configured_host(
    repo: "AgentHostRepo",
    agent_type: str,
    *,
    labels: Iterable[str] | None = None,
) -> str:
    """Choose a host by config, with health as a preference only.

    Unlike :func:`choose_host`, this never falls back to a host that does not
    advertise the requested agent type.
    """
    label_set = frozenset(labels or ())
    all_hosts = [
        h for h in await repo.list_all()
        if _supports_agent(h, agent_type)
        and label_set.issubset(set(h.get("labels") or []))
    ]
    if not all_hosts:
        return LOCAL_HOST_ID

    hosts = [h for h in all_hosts if h["health_status"] == "healthy"] or all_hosts
    key = (agent_type, label_set)
    idx = _RR_COUNTERS.get(key, 0) % len(hosts)
    _RR_COUNTERS[key] = idx + 1
    return hosts[idx]["id"]


async def choose_host(
    repo: "AgentHostRepo",
    agent_type: str,
    *,
    labels: Iterable[str] | None = None,
) -> str:
    """Return the next host id eligible for ``agent_type`` + ``labels``.

    Falls back to the reserved ``"local"`` id when no remote host matches.
    The fallback is the entire reason cooagents stays usable when every
    remote host fails its healthcheck.
    """
    label_set = frozenset(labels or ())
    hosts = [
        h for h in await repo.list_all()
        if _matches(h, agent_type, label_set)
    ]
    if not hosts:
        return LOCAL_HOST_ID

    key = (agent_type, label_set)
    idx = _RR_COUNTERS.get(key, 0) % len(hosts)
    _RR_COUNTERS[key] = idx + 1
    return hosts[idx]["id"]


def reset_counters() -> None:
    """Test helper: zero the round-robin counters."""
    _RR_COUNTERS.clear()
