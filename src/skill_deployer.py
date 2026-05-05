"""Deploy cooagents skills to OpenClaw and/or Hermes managed skills directories.

The local ``skills/`` bundle is runtime-agnostic and is copied verbatim into
each configured destination. Each destination is described by a
``_DeployDestination`` — this lets one call fan out to both
``~/.openclaw/skills`` and ``~/.hermes/skills`` on hosts that run both
runtimes.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import ROOT, Settings

logger = logging.getLogger(__name__)


@dataclass
class DeployResult:
    target_type: str   # "local" | "ssh" | "hermes"
    skills_dir: str
    skill_name: str
    ok: bool
    error: str | None = None


@dataclass
class _DeployDestination:
    target_type: str
    skills_dir: str
    is_remote: bool = False
    remote_host: str | None = None
    remote_port: int = 22
    remote_user: str | None = None
    remote_key: str | None = None


def _collect_destinations(settings: Settings) -> list[_DeployDestination]:
    """Build the deploy fan-out from settings.

    Honors the legacy ``openclaw.deploy_skills`` gate per runtime so existing
    operators can disable either side independently.
    """
    destinations: list[_DeployDestination] = []

    if settings.openclaw.deploy_skills:
        for target in settings.openclaw.targets:
            if target.type == "ssh":
                destinations.append(_DeployDestination(
                    target_type="ssh",
                    skills_dir=target.skills_dir,
                    is_remote=True,
                    remote_host=target.host,
                    remote_port=target.port,
                    remote_user=target.user,
                    remote_key=target.key,
                ))
            else:
                destinations.append(_DeployDestination(
                    target_type="local",
                    skills_dir=target.skills_dir,
                ))
    else:
        logger.info("OpenClaw skill deployment disabled (openclaw.deploy_skills=false)")

    hermes = settings.hermes
    if hermes.enabled and hermes.deploy_skills:
        destinations.append(_DeployDestination(
            target_type="hermes",
            skills_dir=hermes.skills_dir,
        ))
    elif hermes.enabled and not hermes.deploy_skills:
        logger.info("Hermes skill deployment disabled (hermes.deploy_skills=false)")

    return destinations


async def deploy_skills(settings: Settings) -> list[DeployResult]:
    """Sync ``skills/`` to all configured OpenClaw + Hermes skill directories.

    For each skill directory (containing ``SKILL.md``) under ``ROOT/skills/``,
    copies it to every configured destination. SSH targets are still logged
    as unimplemented. Failures on one destination do not stop delivery to
    the others — each result is recorded independently.
    """
    destinations = _collect_destinations(settings)
    if not destinations:
        return []

    skills_root = ROOT / "skills"
    if not skills_root.is_dir():
        logger.info("No skills/ directory found, skipping deployment")
        return []

    skill_dirs = [
        d for d in skills_root.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    ]
    if not skill_dirs:
        logger.info("No skills found in %s", skills_root)
        return []

    results: list[DeployResult] = []

    for dest_spec in destinations:
        if dest_spec.is_remote:
            remote_results = await _deploy_remote_skills(dest_spec, skill_dirs)
            results.extend(remote_results)
            continue

        target_base = Path(dest_spec.skills_dir).expanduser()

        for sd in skill_dirs:
            dest = target_base / sd.name
            try:
                if dest.exists():
                    await asyncio.to_thread(shutil.rmtree, dest)
                await asyncio.to_thread(shutil.copytree, sd, dest)
                logger.info("Deployed skill %s → %s (%s)", sd.name, dest, dest_spec.target_type)
                results.append(DeployResult(
                    target_type=dest_spec.target_type,
                    skills_dir=str(target_base),
                    skill_name=sd.name,
                    ok=True,
                ))
            except Exception as exc:
                logger.error("Failed to deploy skill %s → %s: %s", sd.name, dest, exc)
                results.append(DeployResult(
                    target_type=dest_spec.target_type,
                    skills_dir=str(target_base),
                    skill_name=sd.name,
                    ok=False,
                    error=str(exc),
                ))

    return results


def _ssh_connect_kwargs(dest_spec: _DeployDestination) -> dict[str, Any]:
    if not dest_spec.remote_host:
        raise ValueError("remote_host is required for SSH skill deployment")
    hostname = dest_spec.remote_host
    username = dest_spec.remote_user
    port = dest_spec.remote_port or 22
    if "@" in hostname and not username:
        username, hostname = hostname.split("@", 1)
    if ":" in hostname:
        host_only, maybe_port = hostname.rsplit(":", 1)
        if maybe_port.isdigit():
            hostname = host_only
            if dest_spec.remote_port == 22:
                port = int(maybe_port)
    kwargs: dict[str, Any] = {
        "host": hostname,
        "port": port,
    }
    if username:
        kwargs["username"] = username
    if dest_spec.remote_key:
        kwargs["client_keys"] = [str(Path(dest_spec.remote_key).expanduser())]
    return kwargs


async def _deploy_remote_skills(
    dest_spec: _DeployDestination,
    skill_dirs: list[Path],
) -> list[DeployResult]:
    try:
        import asyncssh  # type: ignore[import-not-found]
    except ImportError:
        logger.error("asyncssh is required for SSH skill deployment")
        return [
            DeployResult(
                target_type="ssh",
                skills_dir=dest_spec.skills_dir,
                skill_name=sd.name,
                ok=False,
                error="asyncssh is not installed",
            )
            for sd in skill_dirs
        ]

    connect_kwargs = _ssh_connect_kwargs(dest_spec)
    results: list[DeployResult] = []
    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            await conn.run(
                f"mkdir -p {shlex.quote(dest_spec.skills_dir)}",
                check=True,
            )
            for sd in skill_dirs:
                remote_dest = f"{dest_spec.skills_dir.rstrip('/')}/{sd.name}"
                try:
                    await conn.run(
                        f"rm -rf {shlex.quote(remote_dest)}",
                        check=True,
                    )
                    await asyncssh.scp(
                        str(sd),
                        (conn, dest_spec.skills_dir),
                        recurse=True,
                    )
                    logger.info(
                        "Deployed skill %s -> %s (%s)",
                        sd.name, remote_dest, dest_spec.target_type,
                    )
                    results.append(DeployResult(
                        target_type="ssh",
                        skills_dir=dest_spec.skills_dir,
                        skill_name=sd.name,
                        ok=True,
                    ))
                except Exception as exc:
                    logger.error(
                        "Failed to deploy skill %s -> %s: %s",
                        sd.name, remote_dest, exc,
                    )
                    results.append(DeployResult(
                        target_type="ssh",
                        skills_dir=dest_spec.skills_dir,
                        skill_name=sd.name,
                        ok=False,
                        error=str(exc),
                    ))
    except Exception as exc:
        logger.error(
            "SSH skill deployment failed for target %s:%s: %s",
            dest_spec.remote_host, dest_spec.skills_dir, exc,
        )
        return [
            DeployResult(
                target_type="ssh",
                skills_dir=dest_spec.skills_dir,
                skill_name=sd.name,
                ok=False,
                error=str(exc),
            )
            for sd in skill_dirs
        ]
    return results
