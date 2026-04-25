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


def _matches(host_row: dict, agent_type: str, labels: frozenset[str]) -> bool:
    if host_row["agent_type"] not in (agent_type, "both"):
        return False
    if host_row["health_status"] != "healthy":
        return False
    have = set(host_row.get("labels") or [])
    return labels.issubset(have)


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
