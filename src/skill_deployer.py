"""Deploy cooagents skills to OpenClaw and/or Hermes managed skills directories.

The same ``skills/`` bundle (``cooagents-setup`` / ``cooagents-upgrade`` /
``cooagents-workflow``) is runtime-agnostic and is copied verbatim into each
configured destination. Each destination is described by a
``_DeployDestination`` — this lets one call fan out to both
``~/.openclaw/skills`` and ``~/.hermes/skills`` on hosts that run both
runtimes.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

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
    is_remote: bool = False  # SSH targets: still unimplemented
    remote_host: str | None = None


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
            logger.warning(
                "SSH skill deployment not yet implemented, skipping target %s:%s",
                dest_spec.remote_host, dest_spec.skills_dir,
            )
            for sd in skill_dirs:
                results.append(DeployResult(
                    target_type="ssh",
                    skills_dir=dest_spec.skills_dir,
                    skill_name=sd.name,
                    ok=False,
                    error="SSH deployment not yet implemented",
                ))
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
