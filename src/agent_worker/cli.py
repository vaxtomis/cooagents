"""Worker CLI entry point: ``cooagents-worker run ...``.

The worker is short-lived. One invocation handles exactly one DevWork or
DesignWork: recovery scan → materialize → spawn ``acpx`` → POST diff outputs
back to cooagents → exit with the acpx return code.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import hashlib
import logging
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import Any

from src.agent_worker.config import WorkerConfig, WorkerConfigError
from src.agent_worker.cooagents_client import (
    CooagentsClient, CooagentsClientError,
)
from src.agent_worker.materialize import materialize
from src.agent_worker.recovery import recovery_scan

logger = logging.getLogger("cooagents-worker")


# Exit codes reserved by the worker; acpx's own exit codes pass through
# unchanged when the worker reaches step 4.
EXIT_OK = 0
EXIT_USAGE = 64
EXIT_CONFIG = 78
EXIT_HASH_DRIFT = 2
EXIT_MATERIALIZE_FAIL = 3
EXIT_REGISTER_FAIL = 4
EXIT_ACPX_LAUNCH_FAIL = 5
EXIT_CLEANUP_FAIL = 6
HEARTBEAT_INTERVAL_S = 30.0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cooagents-worker",
        description="Phase 8b agent worker — runs one DevWork/DesignWork "
                    "on this host, brokered by the cooagents control plane.",
    )
    parser.add_argument("--version", action="store_true",
                        help="print version and exit")
    sub = parser.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="execute one work unit")
    run.add_argument("--workspace-id", required=True)
    run.add_argument("--task-file", required=True,
                     help="workspace-relative POSIX path to the task prompt "
                          "(e.g. designs/DES-foo-prompt.md)")
    run.add_argument("--agent", required=True, choices=("claude", "codex"))
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--correlation-id", default="",
                     help="DevWork or DesignWork id (informational)")
    run.add_argument("--execution-id", default="")
    run.add_argument("--run-token", default="")
    run.add_argument("--host-id", default="")
    run.add_argument("--session-name", default="")
    cleanup = sub.add_parser(
        "cleanup-once",
        help="cleanup expired cooagents-owned acpx executions on this host",
    )
    cleanup.add_argument("--host-id", required=True)
    cleanup.add_argument("--limit", type=int, default=50)
    cleanup.add_argument("--terminate-grace", type=float, default=15.0)
    cleanup.add_argument("--kill-grace", type=float, default=10.0)
    cleanup.add_argument("--no-kill", action="store_true")
    return parser


def _pid_starttime(pid: int) -> str | None:
    if os.name == "nt":
        return None
    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        tail = data.rsplit(") ", 1)[1].split()
        return tail[19]
    except (OSError, IndexError, ValueError):
        return None


def _process_group(pid: int) -> int | None:
    if os.name == "nt":
        return None
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


def _execution_env(
    *,
    execution_id: str | None,
    run_token: str | None,
    host_id: str | None,
    session_name: str | None,
) -> dict[str, str] | None:
    if not execution_id or not run_token:
        return None
    env = os.environ.copy()
    env.update(
        {
            "COOAGENTS_OWNER": "cooagents",
            "COOAGENTS_EXECUTION_ID": execution_id,
            "COOAGENTS_RUN_TOKEN": run_token,
        }
    )
    if host_id:
        env["COOAGENTS_HOST_ID"] = host_id
    if session_name:
        env["COOAGENTS_SESSION_NAME"] = session_name
    return env


def _read_proc_environ(pid: int) -> dict[str, str]:
    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for raw in data.split(b"\0"):
        if not raw or b"=" not in raw:
            continue
        key, value = raw.split(b"=", 1)
        try:
            env[key.decode()] = value.decode(errors="replace")
        except UnicodeDecodeError:
            continue
    return env


def _pid_cwd(pid: int) -> Path | None:
    try:
        return Path(f"/proc/{pid}/cwd").resolve()
    except OSError:
        return None


def _tagged_pids(row: dict[str, Any]) -> list[int]:
    token = str(row.get("run_token") or "")
    if not token or os.name == "nt":
        return []
    expected_pgid = row.get("pgid")
    candidates: set[int] = set()
    if row.get("pid") is not None:
        with suppress(TypeError, ValueError):
            candidates.add(int(row["pid"]))
    try:
        proc_entries = list(Path("/proc").iterdir())
    except OSError:
        proc_entries = []
    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if expected_pgid is not None and _process_group(pid) != int(expected_pgid):
            continue
        if _read_proc_environ(pid).get("COOAGENTS_RUN_TOKEN") == token:
            candidates.add(pid)
    return sorted(candidates)


def _validate_cleanup_row(
    row: dict[str, Any], *, workspaces_root: Path,
) -> tuple[bool, list[int], str]:
    pids = _tagged_pids(row)
    if not pids:
        return False, [], "no tagged live process"
    direct_pid = row.get("pid")
    if direct_pid is not None and row.get("pid_starttime"):
        with suppress(TypeError, ValueError):
            direct_pid_i = int(direct_pid)
            if direct_pid_i in pids:
                if _pid_starttime(direct_pid_i) != str(row["pid_starttime"]):
                    return False, pids, "pid starttime mismatch"
    for pid in pids:
        cwd = _pid_cwd(pid)
        if cwd is None:
            continue
        try:
            cwd.relative_to(workspaces_root)
        except ValueError:
            return False, pids, f"cwd outside workspace root: {cwd}"
    return True, pids, "ok"


async def _terminate_process_group(
    pgid: int, *, terminate_grace: float, kill_grace: float, kill_enabled: bool,
) -> tuple[str, str]:
    if os.name == "nt":
        return "abandoned", "cleanup unsupported on windows"
    try:
        os.killpg(int(pgid), signal.SIGTERM)
    except ProcessLookupError:
        return "terminated", "already gone"
    except PermissionError:
        return "abandoned", "permission denied"
    await asyncio.sleep(terminate_grace)
    if not kill_enabled:
        return "stale", "SIGTERM sent; kill disabled"
    try:
        os.killpg(int(pgid), signal.SIGKILL)
    except ProcessLookupError:
        return "terminated", "SIGTERM"
    except PermissionError:
        return "abandoned", "permission denied"
    await asyncio.sleep(kill_grace)
    return "killed", "SIGKILL"


async def _run_acpx(
    *,
    agent: str,
    cwd: Path,
    task_file: str,
    timeout: int,
    client: CooagentsClient,
    execution_id: str | None = None,
    run_token: str | None = None,
    host_id: str | None = None,
    session_name: str | None = None,
) -> int:
    """Spawn ``acpx <agent> exec --cwd <cwd> --file <task_file>`` locally.

    stdout/stderr are inherited so the SSH parent (cooagents) sees the
    output as if acpx had run there directly.
    """
    cmd = [
        "acpx", "--cwd", str(cwd),
        "--format", "json", "--approve-all",
        "--timeout", str(timeout),
        agent, "exec", "--file", task_file,
    ]
    logger.info("worker: launching acpx: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            start_new_session=True,
            env=_execution_env(
                execution_id=execution_id,
                run_token=run_token,
                host_id=host_id,
                session_name=session_name,
            ),
        )
    except FileNotFoundError:
        logger.error("worker: acpx binary not found on $PATH")
        return EXIT_ACPX_LAUNCH_FAIL
    if execution_id:
        try:
            await client.mark_execution_started(
                execution_id,
                pid=proc.pid,
                pgid=_process_group(proc.pid),
                pid_starttime=_pid_starttime(proc.pid),
                cwd=str(cwd),
                worker_pid=os.getpid(),
                worker_pid_starttime=_pid_starttime(os.getpid()),
            )
        except CooagentsClientError as exc:
            logger.warning(
                "worker: execution started callback failed %s: %s",
                execution_id, exc,
            )
    deadline = asyncio.get_running_loop().time() + float(timeout + 30)
    rc: int | None = None
    try:
        while proc.returncode is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                pgid = _process_group(proc.pid)
                if pgid is not None:
                    await _terminate_process_group(
                        pgid,
                        terminate_grace=5.0,
                        kill_grace=2.0,
                        kill_enabled=True,
                    )
                with suppress(ProcessLookupError):
                    proc.kill()
                with suppress(asyncio.TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                rc = 124
                break
            try:
                await asyncio.wait_for(
                    proc.wait(), timeout=min(HEARTBEAT_INTERVAL_S, remaining),
                )
            except asyncio.TimeoutError:
                if execution_id:
                    try:
                        await client.heartbeat_execution(execution_id)
                    except CooagentsClientError as exc:
                        logger.warning(
                            "worker: execution heartbeat failed %s: %s",
                            execution_id, exc,
                        )
        if rc is None:
            rc = proc.returncode
        return int(rc if rc is not None else 1)
    finally:
        if execution_id:
            try:
                await client.mark_execution_exited(
                    execution_id, exit_code=rc if rc is not None else proc.returncode,
                )
            except CooagentsClientError as exc:
                logger.warning(
                    "worker: execution exited callback failed %s: %s",
                    execution_id, exc,
                )


def _collect_local_files(slug_root: Path) -> dict[str, tuple[bytes, str]]:
    """Return ``{relative_path: (bytes, hash)}`` for every file under root."""
    out: dict[str, tuple[bytes, str]] = {}
    if not slug_root.exists():
        return out
    for entry in slug_root.rglob("*"):
        if not entry.is_file():
            continue
        # Ignore atomic-write tempfiles that any other writer might have
        # left behind (matches LocalFileStore convention).
        if ".tmp-" in entry.name:
            continue
        rel = entry.relative_to(slug_root).as_posix()
        data = entry.read_bytes()
        out[rel] = (data, hashlib.sha256(data).hexdigest())
    return out


def _infer_kind(relative_path: str) -> str:
    """Best-effort kind inference for new files the worker discovers."""
    p = relative_path.lower()
    if p.startswith("designs/") and p.endswith("-prompt.md"):
        return "prompt"
    if p.startswith("designs/") and p.endswith(".md"):
        return "design_doc"
    if p.startswith("design-inputs/"):
        return "design_input"
    if p.startswith("notes/") and p.endswith(".md"):
        return "iteration_note"
    if p == "workspace.md":
        return "workspace_md"
    if p.startswith("context/"):
        return "context"
    if p.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return "image"
    if p.startswith(("artifacts/", "output/")):
        return "artifact"
    return "other"


async def _post_diff(
    *,
    client: CooagentsClient,
    workspace_id: str,
    pre_index: dict[str, dict[str, Any]],
    post_files: dict[str, tuple[bytes, str]],
) -> tuple[int, list[str], list[tuple[str, str]]]:
    """POST every new/changed file. Returns (registered, skipped, failures).

    ``failures`` is a list of ``(relative_path, error_label)``.
    """
    registered: list[str] = []
    skipped: list[str] = []
    failures: list[tuple[str, str]] = []
    for rel, (data, new_hash) in sorted(post_files.items()):
        prior = pre_index.get(rel)
        if prior is not None and prior.get("content_hash") == new_hash:
            skipped.append(rel)
            continue
        expected: str | None
        if prior is None:
            expected = None  # "first write" CAS
        else:
            expected = prior.get("content_hash") or None
        kind = (prior or {}).get("kind") or _infer_kind(rel)
        try:
            await client.post_file(
                workspace_id,
                relative_path=rel,
                kind=kind,
                payload=data,
                expected_prior_hash=expected,
            )
        except CooagentsClientError as exc:
            failures.append((rel, f"http_{exc.status_code}"))
            logger.warning("post_file failed %s: %s", rel, exc.body)
            continue
        registered.append(rel)
    return len(registered), skipped, failures


async def _run(args: argparse.Namespace) -> int:
    try:
        cfg = WorkerConfig.from_env()
    except WorkerConfigError as exc:
        print(f"cooagents-worker: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    # Lazy OSS import so `cooagents-worker --version` works on hosts where
    # the SDK is not installed (e.g. CI bootstrapping).
    from src.storage.oss import OSSFileStore
    import alibabacloud_oss_v2 as oss

    credentials_provider = oss.credentials.StaticCredentialsProvider(
        access_key_id=cfg.oss.access_key_id,
        access_key_secret=cfg.oss.access_key_secret,
    )
    store = OSSFileStore(
        bucket=cfg.oss.bucket,
        region=cfg.oss.region,
        endpoint=cfg.oss.endpoint,
        credentials_provider=credentials_provider,
        prefix=cfg.oss.prefix,
    )

    correlation = args.correlation_id or f"adhoc-{uuid.uuid4().hex[:8]}"
    logger.info(
        "worker run start workspace=%s correlation=%s agent=%s",
        args.workspace_id, correlation, args.agent,
    )

    async with CooagentsClient(
        base_url=cfg.cooagents_url, agent_token=cfg.cooagents_token,
    ) as client:
        try:
            index_resp = await client.get_files_index(args.workspace_id)
        except CooagentsClientError as exc:
            print(f"cooagents-worker: index fetch failed: {exc}",
                  file=sys.stderr)
            return EXIT_REGISTER_FAIL
        slug = index_resp["slug"]
        files_index: list[dict[str, Any]] = list(index_resp.get("files", []))

        report = recovery_scan(
            workspace_root=cfg.workspaces_root,
            workspace_id=args.workspace_id,
            slug=slug,
            files_index=files_index,
        )
        if report.local_only:
            logger.warning(
                "worker recovery: %d local_only files (informational): %s",
                len(report.local_only), report.local_only[:5],
            )
        if report.has_blocking_drift:
            print(
                "cooagents-worker: hash_mismatch detected, refusing to "
                f"overwrite local edits: {report.hash_mismatch}",
                file=sys.stderr,
            )
            return EXIT_HASH_DRIFT

        if report.db_only_missing:
            mat = await materialize(
                store=store,
                workspace_root=cfg.workspaces_root,
                slug=slug,
                files_index=files_index,
                paths_to_pull=report.db_only_missing,
            )
            if mat.failed:
                print(
                    "cooagents-worker: materialize failed for "
                    f"{list(mat.failed)}: {mat.failed}",
                    file=sys.stderr,
                )
                return EXIT_MATERIALIZE_FAIL
            logger.info("worker materialize: pulled=%d", len(mat.pulled))

        slug_root = (cfg.workspaces_root / slug).resolve()
        pre_files = _collect_local_files(slug_root)

        rc = await _run_acpx(
            agent=args.agent,
            cwd=slug_root,
            task_file=args.task_file,
            timeout=args.timeout,
            client=client,
            execution_id=args.execution_id or None,
            run_token=args.run_token or None,
            host_id=args.host_id or None,
            session_name=args.session_name or None,
        )

        post_files = _collect_local_files(slug_root)
        pre_index = {row["relative_path"]: dict(row) for row in files_index}
        # Fold the snapshot of pre-acpx local-only files into the diff
        # baseline so the registered hash reflects what acpx actually saw.
        for rel, (_, h) in pre_files.items():
            pre_index.setdefault(rel, {"relative_path": rel,
                                       "content_hash": h, "kind": None})

        registered, skipped, failures = await _post_diff(
            client=client,
            workspace_id=args.workspace_id,
            pre_index=pre_index,
            post_files=post_files,
        )
        logger.info(
            "worker register: registered=%d skipped=%d failures=%d",
            registered, len(skipped), len(failures),
        )
        if failures:
            print(
                f"cooagents-worker: {len(failures)} register failures: "
                f"{failures[:5]}",
                file=sys.stderr,
            )
            # Honour the acpx rc but escalate to a non-zero exit if acpx
            # itself succeeded — register failure is a real failure.
            return rc if rc != 0 else EXIT_REGISTER_FAIL
    return rc


async def _cleanup_once(args: argparse.Namespace) -> int:
    if os.name == "nt":
        logger.info("worker cleanup: unsupported on windows")
        return EXIT_OK
    try:
        cfg = WorkerConfig.from_env()
    except WorkerConfigError as exc:
        print(f"cooagents-worker: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    workspaces_root = cfg.workspaces_root.resolve()
    async with CooagentsClient(
        base_url=cfg.cooagents_url, agent_token=cfg.cooagents_token,
    ) as client:
        try:
            rows = await client.list_expired_executions(
                host_id=args.host_id, limit=args.limit,
            )
        except CooagentsClientError as exc:
            print(
                f"cooagents-worker: expired execution fetch failed: {exc}",
                file=sys.stderr,
            )
            return EXIT_CLEANUP_FAIL
        failures = 0
        for row in rows:
            execution_id = row.get("id")
            ok, pids, reason = _validate_cleanup_row(
                row, workspaces_root=workspaces_root,
            )
            if not ok:
                logger.warning(
                    "worker cleanup skip execution=%s: %s",
                    execution_id, reason,
                )
                continue
            pgid = row.get("pgid")
            if pgid is None and pids:
                pgid = _process_group(pids[0])
            if pgid is None:
                state, cleanup_reason = "abandoned", "missing pgid"
            else:
                state, cleanup_reason = await _terminate_process_group(
                    int(pgid),
                    terminate_grace=args.terminate_grace,
                    kill_grace=args.kill_grace,
                    kill_enabled=not args.no_kill,
                )
            try:
                await client.mark_cleanup_result(
                    str(execution_id),
                    state=state,
                    cleanup_reason=cleanup_reason,
                )
            except CooagentsClientError as exc:
                failures += 1
                logger.warning(
                    "worker cleanup result callback failed %s: %s",
                    execution_id, exc,
                )
        if failures:
            return EXIT_CLEANUP_FAIL
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("COOAGENTS_WORKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.version:
        from src.agent_worker import __version__
        print(f"cooagents-worker {__version__}")
        return EXIT_OK
    if args.cmd == "cleanup-once":
        return asyncio.run(_cleanup_once(args))
    if args.cmd != "run":
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover — entry point
    sys.exit(main())
