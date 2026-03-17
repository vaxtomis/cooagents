"""Deploy cooagents skills to OpenClaw managed skills directories."""

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
    target_type: str
    skills_dir: str
    skill_name: str
    ok: bool
    error: str | None = None


async def deploy_skills(settings: Settings) -> list[DeployResult]:
    """Sync skills/ directory contents to all configured OpenClaw targets.

    For each skill directory (containing SKILL.md) under ROOT/skills/,
    copies it to each configured target's skills_dir.

    Only local targets are supported currently. SSH targets log a warning.
    """
    if not settings.openclaw.deploy_skills:
        logger.info("Skill deployment disabled (openclaw.deploy_skills=false)")
        return []

    skills_root = ROOT / "skills"
    if not skills_root.is_dir():
        logger.info("No skills/ directory found, skipping deployment")
        return []

    # Discover skills (directories containing SKILL.md)
    skill_dirs = [
        d for d in skills_root.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    ]

    if not skill_dirs:
        logger.info("No skills found in %s", skills_root)
        return []

    results: list[DeployResult] = []

    for target in settings.openclaw.targets:
        if target.type == "ssh":
            logger.warning(
                "SSH skill deployment not yet implemented, skipping target %s:%s",
                target.host, target.skills_dir,
            )
            for sd in skill_dirs:
                results.append(DeployResult(
                    target_type="ssh",
                    skills_dir=target.skills_dir,
                    skill_name=sd.name,
                    ok=False,
                    error="SSH deployment not yet implemented",
                ))
            continue

        # Local deployment
        target_base = Path(target.skills_dir).expanduser()

        for sd in skill_dirs:
            dest = target_base / sd.name
            try:
                if dest.exists():
                    await asyncio.to_thread(shutil.rmtree, dest)
                await asyncio.to_thread(shutil.copytree, sd, dest)
                logger.info("Deployed skill %s → %s", sd.name, dest)
                results.append(DeployResult(
                    target_type="local",
                    skills_dir=str(target_base),
                    skill_name=sd.name,
                    ok=True,
                ))
            except Exception as exc:
                logger.error("Failed to deploy skill %s → %s: %s", sd.name, dest, exc)
                results.append(DeployResult(
                    target_type="local",
                    skills_dir=str(target_base),
                    skill_name=sd.name,
                    ok=False,
                    error=str(exc),
                ))

    return results
