"""Phase 8a: agent host registry and dispatch primitives.

Components:
  * :class:`AgentHostRepo` — DB-layer CRUD for ``agent_hosts``
  * :class:`AgentDispatchRepo` — DB-layer lifecycle for ``agent_dispatches``
  * :class:`SshDispatcher` — healthcheck (8a) + ``run_remote`` (8b)
  * :func:`choose_host` — round-robin healthy-host selector with local fallback
  * :func:`choose_configured_host` — config-first selector for tool routing
  * :class:`HealthProbeLoop` — background task that polls every host

The package keeps Phase 8 wiring isolated so existing single-host execution
paths stay unaffected (``host_id="local"`` is the default everywhere).
"""
from src.agent_hosts.dispatch_decider import (
    choose_configured_host,
    choose_host,
    configured_agent_types,
    resolve_configured_agent,
)
from src.agent_hosts.health_probe import HealthProbeLoop
from src.agent_hosts.repo import AgentDispatchRepo, AgentHostRepo
from src.agent_hosts.ssh_dispatcher import SshDispatcher

__all__ = [
    "AgentHostRepo",
    "AgentDispatchRepo",
    "SshDispatcher",
    "HealthProbeLoop",
    "choose_configured_host",
    "choose_host",
    "configured_agent_types",
    "resolve_configured_agent",
]
