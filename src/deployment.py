from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8321
DEFAULT_WORKSPACE_ROOT = "~/cooagents-workspace"
ENV_FILE_NAME = ".env"
LOG_FILE_NAME = "cooagents.log"
PID_FILE_NAME = ".coop/cooagents.pid"


class DeploymentError(RuntimeError):
    """Raised when the deployment command cannot complete safely."""


@dataclass
class BootstrapResult:
    python_bin: str
    venv_python: str
    using_venv: bool


def _print_step(message: str) -> None:
    print(f"==> {message}", flush=True)


def _platform_is_windows() -> bool:
    return os.name == "nt"


def _repo_root() -> Path:
    return ROOT


def _env_path(repo_root: Path) -> Path:
    return repo_root / ENV_FILE_NAME


def _pid_path(repo_root: Path) -> Path:
    return repo_root / PID_FILE_NAME


def _log_path(repo_root: Path) -> Path:
    return repo_root / LOG_FILE_NAME


def _choose_bootstrap_python() -> str:
    candidates = []
    if sys.version_info >= (3, 11):
        candidates.append(sys.executable)
    for name in ("python3.11", "python3", "python"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        proc = subprocess.run(
            [candidate, "-c", "import sys; print(int(sys.version_info >= (3, 11)))"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip() == "1":
            return candidate
    raise DeploymentError("Python >= 3.11 is required")


def _venv_python(repo_root: Path) -> Path:
    if _platform_is_windows():
        return repo_root / ".venv" / "Scripts" / "python.exe"
    return repo_root / ".venv" / "bin" / "python"


def _require_cmd(name: str, *, hint: str | None = None) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    suffix = f" ({hint})" if hint else ""
    raise DeploymentError(f"Required command not found: {name}{suffix}")


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    if capture:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        check=False,
    )


def _run_checked(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    proc = _run(cmd, cwd=cwd, env=env, capture=capture)
    if proc.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        raise DeploymentError(f"Command failed ({proc.returncode}): {rendered}")
    return proc


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_env_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    try:
        parts = shlex.split(f"v={raw}", posix=True)
    except ValueError:
        return raw.strip("'\"")
    if len(parts) != 1 or "=" not in parts[0]:
        return raw.strip("'\"")
    return parts[0].split("=", 1)[1]


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        env[key.strip()] = _parse_env_value(raw)
    return env


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [
        f"{key}={shlex.quote(value)}"
        for key, value in sorted(values.items())
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows ignores POSIX modes here.
        pass


def _require_auth_values(env: dict[str, str]) -> None:
    missing = [
        key for key in (
            "ADMIN_USERNAME",
            "ADMIN_PASSWORD_HASH",
            "JWT_SECRET",
            "AGENT_API_TOKEN",
        )
        if not env.get(key)
    ]
    if missing:
        raise DeploymentError(
            "Auth environment is incomplete. Missing: " + ", ".join(missing)
        )


def _service_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(read_env_file(_env_path(repo_root)))
    _require_auth_values(env)
    return env


def _load_yaml() -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - installed by bootstrap
        raise DeploymentError(
            "PyYAML is required after bootstrap. Re-run `python scripts/deploy.py bootstrap`."
        ) from exc
    return yaml


def _load_settings_yaml(repo_root: Path) -> dict[str, Any]:
    yaml = _load_yaml()
    path = repo_root / "config" / "settings.yaml"
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise DeploymentError("config/settings.yaml must be a YAML mapping")
    return data


def _write_settings_yaml(repo_root: Path, data: dict[str, Any]) -> None:
    yaml = _load_yaml()
    path = repo_root / "config" / "settings.yaml"
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _ensure_workspace_root(repo_root: Path, workspace_root: str) -> None:
    resolved = Path(workspace_root).expanduser()
    resolved.mkdir(parents=True, exist_ok=True)
    settings = _load_settings_yaml(repo_root)
    security = settings.setdefault("security", {})
    if not isinstance(security, dict):
        raise DeploymentError("settings.security must be a mapping")
    security["workspace_root"] = workspace_root
    _write_settings_yaml(repo_root, settings)


def _server_bind(repo_root: Path) -> tuple[str, int]:
    try:
        settings = _load_settings_yaml(repo_root)
    except DeploymentError:
        return DEFAULT_HOST, DEFAULT_PORT
    server = settings.get("server") or {}
    if not isinstance(server, dict):
        return DEFAULT_HOST, DEFAULT_PORT
    host = str(server.get("host") or DEFAULT_HOST)
    port = int(server.get("port") or DEFAULT_PORT)
    return host, port


def _init_database(repo_root: Path) -> None:
    coop_dir = repo_root / ".coop"
    coop_dir.mkdir(parents=True, exist_ok=True)
    db_path = coop_dir / "state.db"
    backup_path = coop_dir / "state.db.bak"
    if db_path.exists():
        shutil.copy2(db_path, backup_path)
        print(f"  Backed up existing DB to {backup_path}")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript((repo_root / "db" / "schema.sql").read_text(encoding="utf-8"))
    finally:
        conn.close()
    print("  Database initialized.")


def bootstrap(
    repo_root: Path,
    *,
    python_bin: str | None = None,
) -> BootstrapResult:
    python_bin = python_bin or _choose_bootstrap_python()
    pyver = _run_checked(
        [python_bin, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        capture=True,
    ).stdout.strip()

    print("=== cooagents bootstrap ===", flush=True)
    _print_step(f"Python {pyver} ({python_bin})")
    _require_cmd("git")
    _print_step("git available")
    _require_cmd("node", hint="required for acpx and web build")
    _require_cmd("npm", hint="required for web build")
    _print_step(f"node { _run_checked(['node', '--version'], capture=True).stdout.strip() }")
    _print_step(f"npm { _run_checked(['npm', '--version'], capture=True).stdout.strip() }")

    if shutil.which("acpx"):
        _print_step("acpx available")
    else:
        _print_step("Installing acpx")
        _run_checked(["npm", "install", "-g", "acpx@latest"])
        _run_checked(["acpx", "--version"], capture=True)

    using_venv = False
    venv_python = _venv_python(repo_root)
    _print_step("Installing Python dependencies")
    try:
        _run_checked([python_bin, "-m", "venv", ".venv"], cwd=repo_root)
        using_venv = True
    except DeploymentError:
        using_venv = False

    if using_venv:
        if not venv_python.exists():
            raise DeploymentError(f"Virtualenv created but interpreter missing: {venv_python}")
        _run_checked([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"], cwd=repo_root)
    else:
        print("WARN: venv creation failed, falling back to global pip", flush=True)
        _run_checked([python_bin, "-m", "pip", "install", "-r", "requirements.txt"], cwd=repo_root)

    _print_step("Building web dashboard")
    web_dir = repo_root / "web"
    if not (web_dir / "package.json").exists():
        raise DeploymentError("web/package.json not found")
    if not (web_dir / "package-lock.json").exists():
        raise DeploymentError("web/package-lock.json not found")
    _run_checked(["npm", "ci"], cwd=web_dir)
    _run_checked(["npm", "run", "build"], cwd=web_dir)
    if not (web_dir / "dist" / "index.html").exists():
        raise DeploymentError("web build did not produce web/dist/index.html")

    _print_step("Initializing database")
    _init_database(repo_root)

    print("")
    print("=== Bootstrap complete ===")
    print(f"  Service URL: http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    return BootstrapResult(
        python_bin=python_bin,
        venv_python=str(venv_python if using_venv else python_bin),
        using_venv=using_venv,
    )


def _capture_auth_bundle(
    repo_root: Path,
    *,
    username: str,
    password: str,
) -> dict[str, str]:
    python_path = _venv_python(repo_root)
    if python_path.exists():
        python_bin = str(python_path)
    else:
        python_bin = _choose_bootstrap_python()
    proc = _run_checked(
        [
            python_bin,
            "scripts/generate_password_hash.py",
            "--username",
            username,
            "--password",
            password,
        ],
        cwd=repo_root,
        capture=True,
    )
    bundle: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        bundle[key.strip()] = _parse_env_value(raw)
    _require_auth_values(bundle)
    return bundle


def ensure_auth_env(
    repo_root: Path,
    *,
    username: str,
    password: str | None,
    replace_existing: bool,
) -> dict[str, str]:
    env_path = _env_path(repo_root)
    existing = read_env_file(env_path)
    if replace_existing or not all(existing.get(key) for key in (
        "ADMIN_USERNAME",
        "ADMIN_PASSWORD_HASH",
        "JWT_SECRET",
        "AGENT_API_TOKEN",
    )):
        if not password:
            raise DeploymentError(
                "--admin-password is required when auth environment is missing or being replaced"
            )
        bundle = _capture_auth_bundle(repo_root, username=username, password=password)
        merged = dict(existing)
        merged.update(bundle)
        write_env_file(env_path, merged)
        return merged
    return existing


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop_service(repo_root: Path, *, ignore_missing: bool = False) -> None:
    pid_path = _pid_path(repo_root)
    if not pid_path.exists():
        if ignore_missing:
            return
        raise DeploymentError("Service pid file not found")
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError as exc:
        raise DeploymentError("Invalid service pid file") from exc

    if _platform_is_windows():
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.time() + 10
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
    pid_path.unlink(missing_ok=True)


def start_service(
    repo_root: Path,
    *,
    force_restart: bool = False,
    host: str | None = None,
    port: int | None = None,
) -> tuple[str, int]:
    if force_restart:
        stop_service(repo_root, ignore_missing=True)

    host = host or _server_bind(repo_root)[0]
    port = port or _server_bind(repo_root)[1]
    env = _service_env(repo_root)
    python_bin = str(_venv_python(repo_root)) if _venv_python(repo_root).exists() else _choose_bootstrap_python()
    log_path = _log_path(repo_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path = _pid_path(repo_root)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = log_path.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": str(repo_root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": subprocess.STDOUT,
    }
    if _platform_is_windows():
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [
            python_bin,
            "-m",
            "uvicorn",
            "src.app:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        **kwargs,
    )
    stdout.close()
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    return host, port


def _http_get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except urllib.error.URLError as exc:
        raise DeploymentError(f"Request failed: {url} ({exc.reason})") from exc


def wait_for_service(
    host: str,
    port: int,
    *,
    timeout_s: int = 30,
) -> None:
    health_url = f"http://{host}:{port}/health"
    root_url = f"http://{host}:{port}/"
    deadline = time.time() + timeout_s
    last_error = "service did not become healthy"
    while time.time() < deadline:
        try:
            status, body = _http_get(health_url)
            if status == 200:
                payload = json.loads(body.decode("utf-8"))
                if payload.get("status") == "ok":
                    root_status, root_body = _http_get(root_url)
                    if root_status == 200 and b"<html" in root_body.lower():
                        return
                    last_error = "dashboard root did not return HTML"
                else:
                    last_error = f"health payload missing ok status: {payload!r}"
            else:
                last_error = f"health returned HTTP {status}"
        except (json.JSONDecodeError, DeploymentError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise DeploymentError(last_error)


def _upsert_runtime_env(path: Path, updates: dict[str, str]) -> None:
    current = read_env_file(path)
    current.update(updates)
    write_env_file(path, current)


def _runtime_choice(value: str) -> tuple[str, ...]:
    if value == "both":
        return ("openclaw", "hermes")
    if value == "none":
        return ()
    return (value,)


def _detect_gateway_port() -> int:
    proc = _run(
        ["openclaw", "config", "get", "gateway.port"],
        capture=True,
    )
    if proc.returncode != 0:
        return 18789
    raw = proc.stdout.strip()
    if not raw:
        return 18789
    try:
        return int(raw)
    except ValueError:
        return 18789


def _validate_openclaw_hook(gateway_port: int, hooks_token: str) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{gateway_port}/hooks/agent",
        data=json.dumps(
            {
                "message": "cooagents hook test",
                "name": "cooagents-deploy",
                "wakeMode": "next-heartbeat",
                "deliver": False,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {hooks_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise DeploymentError("OpenClaw hook endpoint is not ready") from exc
    if payload.get("ok") is not True:
        raise DeploymentError("OpenClaw hook endpoint rejected the test payload")


def integrate_openclaw(repo_root: Path, *, agent_api_token: str, restart_service_after: bool) -> None:
    _require_cmd("openclaw")
    _run_checked(["openclaw", "--version"])
    hooks_token = read_env_file(_env_path(repo_root)).get("OPENCLAW_HOOK_TOKEN")
    if not hooks_token:
        hooks_token = subprocess.run(
            [sys.executable, "-c", "import secrets; print(secrets.token_hex(32))"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    gateway_port = _detect_gateway_port()
    _run_checked(["openclaw", "config", "set", "hooks.enabled", "true", "--strict-json"])
    _run_checked(["openclaw", "config", "set", "hooks.token", hooks_token])
    _run_checked(["openclaw", "config", "set", "hooks.defaultSessionKey", "hook:ingress"])
    _run_checked(["openclaw", "config", "set", "hooks.allowRequestSessionKey", "false", "--strict-json"])
    _run_checked(["openclaw", "config", "set", "hooks.allowedSessionKeyPrefixes", "[\"hook:\"]", "--strict-json"])
    _validate_openclaw_hook(gateway_port, hooks_token)
    _run_checked(["openclaw", "config", "set", "env.AGENT_API_TOKEN", agent_api_token])
    env_values = read_env_file(_env_path(repo_root))
    env_values["OPENCLAW_HOOK_TOKEN"] = hooks_token
    write_env_file(_env_path(repo_root), env_values)

    settings = _load_settings_yaml(repo_root)
    openclaw = settings.setdefault("openclaw", {})
    if not isinstance(openclaw, dict):
        raise DeploymentError("settings.openclaw must be a mapping")
    hooks = openclaw.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise DeploymentError("settings.openclaw.hooks must be a mapping")
    hooks["enabled"] = True
    hooks["url"] = f"http://127.0.0.1:{gateway_port}/hooks/agent"
    if "token" in hooks:
        hooks["token"] = ""
    _write_settings_yaml(repo_root, settings)
    if restart_service_after:
        host, port = start_service(repo_root, force_restart=True)
        wait_for_service(host, port)


def _hermes_env_path() -> Path:
    proc = _run_checked(["hermes", "config", "env-path"], capture=True)
    return Path(proc.stdout.strip()).expanduser()


def _hermes_config_path() -> Path:
    proc = _run_checked(["hermes", "config", "path"], capture=True)
    return Path(proc.stdout.strip()).expanduser()


def integrate_hermes(repo_root: Path, *, agent_api_token: str, restart_service_after: bool) -> None:
    _require_cmd("hermes")
    _run_checked(["hermes", "--version"])
    env_path = _hermes_env_path()
    config_path = _hermes_config_path()
    hermes_secret = read_env_file(_env_path(repo_root)).get("HERMES_WEBHOOK_SECRET")
    if not hermes_secret:
        hermes_secret = subprocess.run(
            [sys.executable, "-c", "import secrets; print(secrets.token_hex(32))"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    _upsert_runtime_env(
        env_path,
        {
            "HERMES_WEBHOOK_SECRET": hermes_secret,
            "AGENT_API_TOKEN": agent_api_token,
        },
    )

    yaml = _load_yaml()
    hermes_cfg = {}
    if config_path.exists():
        hermes_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(hermes_cfg, dict):
        raise DeploymentError("Hermes config must be a YAML mapping")
    platforms = hermes_cfg.setdefault("platforms", {})
    webhook = platforms.setdefault("webhook", {})
    webhook["enabled"] = True
    extra = webhook.setdefault("extra", {})
    extra["host"] = "127.0.0.1"
    extra["port"] = 8644
    routes = extra.setdefault("routes", {})
    routes["cooagents"] = {
        "events": ["*"],
        "secret": "${HERMES_WEBHOOK_SECRET}",
        "skills": [],
        "prompt": (
            "cooagents push event: {event_type}\n"
            "run_id: {run_id}\n"
            "ticket: {ticket}\n\n"
            "payload: {payload}\n"
        ),
        "deliver": "log",
    }
    config_path.write_text(
        yaml.safe_dump(hermes_cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    env_values = read_env_file(_env_path(repo_root))
    env_values["HERMES_WEBHOOK_SECRET"] = hermes_secret
    write_env_file(_env_path(repo_root), env_values)

    settings = _load_settings_yaml(repo_root)
    hermes = settings.setdefault("hermes", {})
    if not isinstance(hermes, dict):
        raise DeploymentError("settings.hermes must be a mapping")
    hermes["enabled"] = True
    hermes["deploy_skills"] = True
    hermes.setdefault("skills_dir", "~/.hermes/skills")
    webhook_cfg = hermes.setdefault("webhook", {})
    if not isinstance(webhook_cfg, dict):
        raise DeploymentError("settings.hermes.webhook must be a mapping")
    webhook_cfg["enabled"] = True
    webhook_cfg["url"] = "http://127.0.0.1:8644/webhooks/cooagents"
    webhook_cfg["events"] = [
        "gate.waiting",
        "run.completed",
        "run.failed",
        "merge.conflict",
    ]
    if "secret" in webhook_cfg:
        webhook_cfg["secret"] = ""
    _write_settings_yaml(repo_root, settings)

    _run(["hermes", "gateway", "restart"])
    if restart_service_after:
        host, port = start_service(repo_root, force_restart=True)
        wait_for_service(host, port)


def sync_skills(repo_root: Path) -> None:
    # Bootstrap must have run already because src.skill_deployer imports the full app config stack.
    from src.config import load_settings
    from src.skill_deployer import deploy_skills

    async def _run_sync() -> None:
        results = await deploy_skills(load_settings())
        failures = [r for r in results if not r.ok]
        if failures:
            labels = [f"{r.target_type}:{r.skill_name}:{r.error}" for r in failures]
            raise DeploymentError("Skill sync failed: " + "; ".join(labels))

    _ = repo_root
    asyncio.run(_run_sync())


def setup_command(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    bootstrap(repo_root)
    env = ensure_auth_env(
        repo_root,
        username=args.admin_username,
        password=args.admin_password,
        replace_existing=args.replace_env,
    )
    _ensure_workspace_root(repo_root, args.workspace_root)
    for runtime in _runtime_choice(args.runtime):
        if runtime == "openclaw":
            integrate_openclaw(
                repo_root,
                agent_api_token=env["AGENT_API_TOKEN"],
                restart_service_after=False,
            )
        elif runtime == "hermes":
            integrate_hermes(
                repo_root,
                agent_api_token=env["AGENT_API_TOKEN"],
                restart_service_after=False,
            )
    if not args.skip_start:
        host, port = start_service(repo_root, force_restart=True)
        wait_for_service(host, port)
        print(f"Service healthy at http://{host}:{port}")
    return 0


def upgrade_command(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    if not args.skip_pull:
        _run_checked(["git", "pull", "origin", args.branch], cwd=repo_root)
    bootstrap(repo_root)
    host, port = start_service(repo_root, force_restart=True)
    wait_for_service(host, port)
    print(f"Service healthy at http://{host}:{port}")
    return 0


def service_command(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    if args.action == "start":
        host, port = start_service(repo_root, force_restart=args.force)
        wait_for_service(host, port)
        print(f"Service healthy at http://{host}:{port}")
        return 0
    if args.action == "stop":
        stop_service(repo_root, ignore_missing=args.ignore_missing)
        print("Service stopped")
        return 0
    if args.action == "restart":
        host, port = start_service(repo_root, force_restart=True)
        wait_for_service(host, port)
        print(f"Service healthy at http://{host}:{port}")
        return 0
    if args.action == "status":
        host, port = _server_bind(repo_root)
        try:
            wait_for_service(host, port, timeout_s=2)
            print(json.dumps({"status": "ok", "url": f"http://{host}:{port}"}))
            return 0
        except DeploymentError:
            print(json.dumps({"status": "stopped", "url": f"http://{host}:{port}"}))
            return 1
    raise DeploymentError(f"Unknown service action: {args.action}")


def integrate_runtime_command(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    env = read_env_file(_env_path(repo_root))
    agent_token = env.get("AGENT_API_TOKEN")
    if not agent_token:
        raise DeploymentError("AGENT_API_TOKEN is missing from .env; run setup first")
    for runtime in _runtime_choice(args.runtime):
        if runtime == "openclaw":
            integrate_openclaw(
                repo_root,
                agent_api_token=agent_token,
                restart_service_after=False,
            )
        elif runtime == "hermes":
            integrate_hermes(
                repo_root,
                agent_api_token=agent_token,
                restart_service_after=False,
            )
    if args.restart_service:
        host, port = start_service(repo_root, force_restart=True)
        wait_for_service(host, port)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deploy.py",
        description="Unified repo-local deployment entrypoint for cooagents.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", help="install dependencies, build web, initialize the database")

    setup = sub.add_parser("setup", help="bootstrap, ensure auth env, optionally integrate runtimes, and start the service")
    setup.add_argument("--admin-username", default="admin")
    setup.add_argument("--admin-password")
    setup.add_argument("--workspace-root", default=DEFAULT_WORKSPACE_ROOT)
    setup.add_argument("--runtime", choices=("none", "openclaw", "hermes", "both"), default="none")
    setup.add_argument("--replace-env", action="store_true")
    setup.add_argument("--skip-start", action="store_true")

    upgrade = sub.add_parser("upgrade", help="pull the repo, re-bootstrap, and restart the service")
    upgrade.add_argument("--branch", default="main")
    upgrade.add_argument("--skip-pull", action="store_true")

    service = sub.add_parser("service", help="manage the local cooagents process")
    service.add_argument("action", choices=("start", "stop", "restart", "status"))
    service.add_argument("--force", action="store_true", help="restart before starting")
    service.add_argument("--ignore-missing", action="store_true", help="do not fail if the pid file is absent when stopping")

    runtime = sub.add_parser("integrate-runtime", help="configure OpenClaw and/or Hermes against the current repo")
    runtime.add_argument("--runtime", choices=("openclaw", "hermes", "both"), required=True)
    runtime.add_argument("--restart-service", action="store_true")

    sub.add_parser("sync-skills", help="push the local skills bundle to configured runtime targets")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "bootstrap":
            bootstrap(_repo_root())
            return 0
        if args.cmd == "setup":
            return setup_command(args)
        if args.cmd == "upgrade":
            return upgrade_command(args)
        if args.cmd == "service":
            return service_command(args)
        if args.cmd == "integrate-runtime":
            return integrate_runtime_command(args)
        if args.cmd == "sync-skills":
            sync_skills(_repo_root())
            return 0
        raise DeploymentError(f"Unknown command: {args.cmd}")
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
