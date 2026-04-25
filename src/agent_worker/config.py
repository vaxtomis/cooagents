"""Worker configuration loaded from environment variables.

The worker is short-lived and process-isolated — there is no settings
file to mount on the remote host. Operators export the env vars in the
host's systemd unit / shell profile.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_REQUIRED_BASE = ("COOAGENTS_URL", "COOAGENTS_AGENT_TOKEN", "WORKSPACES_ROOT")
_REQUIRED_OSS = (
    "OSS_BUCKET", "OSS_REGION", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET",
)


class WorkerConfigError(RuntimeError):
    """Raised when required worker env vars are missing or invalid."""


@dataclass(frozen=True)
class OSSConfig:
    bucket: str
    region: str
    endpoint: str  # may be empty when SDK derives it from region
    access_key_id: str
    access_key_secret: str
    prefix: str = ""


@dataclass(frozen=True)
class WorkerConfig:
    cooagents_url: str
    cooagents_token: str
    workspaces_root: Path
    oss: OSSConfig

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "WorkerConfig":
        env = dict(env if env is not None else os.environ)
        missing_base = [n for n in _REQUIRED_BASE if not env.get(n, "").strip()]
        missing_oss = [n for n in _REQUIRED_OSS if not env.get(n, "").strip()]
        missing = missing_base + missing_oss
        if missing:
            raise WorkerConfigError(
                "Missing required worker env vars: " + ", ".join(missing)
            )
        url = env["COOAGENTS_URL"].rstrip("/")
        token = env["COOAGENTS_AGENT_TOKEN"].strip()
        if len(token) < 32:
            raise WorkerConfigError(
                "COOAGENTS_AGENT_TOKEN must be at least 32 characters"
            )
        root = Path(env["WORKSPACES_ROOT"]).expanduser()
        prefix = env.get("OSS_PREFIX", "").strip()
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        return cls(
            cooagents_url=url,
            cooagents_token=token,
            workspaces_root=root,
            oss=OSSConfig(
                bucket=env["OSS_BUCKET"].strip(),
                region=env["OSS_REGION"].strip(),
                endpoint=env.get("OSS_ENDPOINT", "").strip(),
                access_key_id=env["OSS_ACCESS_KEY_ID"].strip(),
                access_key_secret=env["OSS_ACCESS_KEY_SECRET"].strip(),
                prefix=prefix,
            ),
        )
