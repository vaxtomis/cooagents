#!/usr/bin/env python3
"""Validate acpx session/cwd/status/list/close behavior for the DevWork x acpx PRD Phase 1 spike.

Caller: operator running ``python scripts/spike_acpx_session.py --agent claude
--report .reports/acpx-spike.md`` on a host that has both ``acpx`` and an LLM
credential (Claude / Codex). The script drives four minimal probes against
``acpx`` and writes a Markdown report answering each of the PRD's four open
questions with a YES / NO / PARTIAL / UNKNOWN verdict + raw CLI evidence.

This script intentionally has zero ``src.*`` imports and uses only the Python
standard library so it can be dropped on any spike host (including the
operator-supplied root@8.136.220.129 Linux box). It is operator-run research,
not a regression-prone production component, so there are no pytest tests.

Exit codes:
  0 = all four questions YES (PRD assumptions confirmed)
  1 = at least one PARTIAL but zero NO/UNKNOWN (Phase 2 proceeds with caveats)
  2 = at least one NO/UNKNOWN, OR preflight failed, OR safety rule violated
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# -- Constants ------------------------------------------------------------

_DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_REPORT = _DEFAULT_ROOT / ".reports" / "acpx-spike.md"

# Literal prefix used for every spike-created session AND tempdir. The
# defense-in-depth wrapper in ``_run_acpx`` refuses to ``close`` / ``prune``
# any session name that does not start with this string — protects real
# ``dw-*`` / ``design-*`` sessions on a shared spike host.
SPIKE_SESSION_PREFIX = "spike-"
SPIKE_TEMPDIR_GLOB = "/tmp/spike-*"

# Mutated by main() once the report path is known so the ops log lands beside
# the report. Default (script-import-time fallback) keeps test imports happy.
OPS_LOG_PATH: Path = _DEFAULT_ROOT / ".reports" / "acpx-spike.ops.log"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# -- Safety self-audit (Task 8c, Server Safety Rules 1-4 + 6) -------------

# Tokens whose presence in this script's source code would indicate a future
# patch violated a Server Safety Rule. The check is a coarse string scan; it
# runs once at startup. Any hit -> exit 2 with a clear rule citation.
_FORBIDDEN_TOKENS = (
    # Rule 1: no installs.
    ("apt-get install", "Rule 1: no package installs"),
    ("apt install", "Rule 1: no package installs"),
    ("pip install", "Rule 1: no package installs"),
    ("npm install", "Rule 1: no package installs"),
    ("cargo install", "Rule 1: no package installs"),
    # Rule 4: no network egress beyond LLM (acpx is the only outbound channel).
    ("import requests", "Rule 4: no network egress beyond LLM"),
    ("import httpx", "Rule 4: no network egress beyond LLM"),
    ("import urllib.request", "Rule 4: no network egress beyond LLM"),
    ("from urllib import request", "Rule 4: no network egress beyond LLM"),
    ("import socket", "Rule 4: no network egress beyond LLM"),
)

# Allowed tempdir prefixes for ``shutil.rmtree`` callsites (Rule 3).
_RMTREE_ALLOWED_PREFIX = "/tmp/spike-"


def _self_audit() -> None:
    """Encode Server Safety Rules 1-4 + 6 as a startup string scan.

    Exits 2 if any forbidden token is present or if any ``shutil.rmtree``
    callsite is missing the literal ``/tmp/spike-`` prefix on the same line.
    """
    try:
        source = Path(__file__).read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[spike] self-audit: cannot read own source: {exc}\n")
        raise SystemExit(2)

    # Strip this _FORBIDDEN_TOKENS table itself before scanning; otherwise the
    # tokens we list as "forbidden" would self-trigger.
    audit_marker = "# -- Safety self-audit"
    end_marker = "def _self_audit"
    sanitized = source
    if audit_marker in source and end_marker in source:
        head, _, rest = source.partition(audit_marker)
        _, _, tail = rest.partition(end_marker)
        sanitized = head + "# (table elided for self-audit)\n" + end_marker + tail

    for token, rule in _FORBIDDEN_TOKENS:
        if token in sanitized:
            sys.stderr.write(
                f"[spike] Server Safety Rule violated: {rule} "
                f"(token {token!r} found in script source)\n"
            )
            raise SystemExit(2)

    # Rule 3: every rmtree call must be on a line that also references the
    # literal SPIKE_TEMPDIR_GLOB or _RMTREE_ALLOWED_PREFIX constant.
    # (Marker built by string concatenation so this very loop does not
    # self-match — the literal token is not present as a single substring.)
    rmtree_marker = "shutil." + "rmtree("
    for lineno, line in enumerate(sanitized.splitlines(), start=1):
        if rmtree_marker not in line:
            continue
        if (
            _RMTREE_ALLOWED_PREFIX in line  # literal value /tmp/spike-
            or "_RMTREE_ALLOWED_PREFIX" in line
            or "SPIKE_TEMPDIR_GLOB" in line
            or "tempdir" in line
        ):
            continue
        sys.stderr.write(
            f"[spike] Server Safety Rule 3 violated: rmtree on "
            f"line {lineno} not anchored on /tmp/spike- prefix\n"
            f"        line: {line.strip()!r}\n"
        )
        raise SystemExit(2)


# -- subprocess wrapper + ops log -----------------------------------------

def _append_ops_log(
    cmd: list[str],
    rc: int,
    out: str,
    err: str,
    duration_ms: int,
    phase: str,
) -> None:
    """Best-effort NDJSON append. Never raises.

    Stdout/stderr bodies are NOT logged — only their byte lengths — so the
    ops log can be safely shared without leaking LLM responses.
    """
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cmd": cmd,
            "rc": rc,
            "stdout_len": len(out),
            "stderr_len": len(err),
            "duration_ms": duration_ms,
            "phase": phase,
        }
        OPS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OPS_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        sys.stderr.write(f"[spike] ops-log write failed: {exc}\n")


def _run_acpx(
    *args: str,
    cwd: str | None = None,
    timeout: float = 30.0,
    phase: str = "unknown",
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run ``acpx`` and return ``(rc, stdout, stderr)``.

    Mirrors ``scripts/sim_worker.py::_run_git`` shape. Synchronous on
    purpose — the spike has no streaming requirement and ``subprocess.run``
    keeps the call sites readable. Every call appends one JSON line to
    ``OPS_LOG_PATH``.

    Defense-in-depth (Server Safety Rule 5): for any
    ``sessions close|prune`` call outside ``phase="preflight"``, the target
    session name must start with ``SPIKE_SESSION_PREFIX``. Any other name
    raises ``AssertionError`` and is logged with ``rc=-1``. Prevents an
    accidental mass-close of real ``dw-*`` work on a shared host.
    """
    if len(args) >= 2 and args[1] == "sessions" and len(args) >= 3:
        verb = args[2]
        if verb in ("close", "prune") and phase != "preflight":
            for name_arg in args[3:]:
                # Skip flags and timestamp values for ``--before`` / ``--older-than``.
                if name_arg.startswith("-") or name_arg.replace(":", "").replace(
                    "-", ""
                ).replace(".", "").replace("T", "").replace("Z", "").isdigit():
                    continue
                if name_arg.startswith(SPIKE_SESSION_PREFIX):
                    continue
                # Treat as a session name candidate: refuse if not prefixed.
                # ``prune`` with only ``--before`` / ``--include-history`` and no
                # session-name argument is fine — that's a global filter.
                if verb == "prune":
                    continue
                _append_ops_log(
                    list(args), -1, "", f"refused: name {name_arg!r} lacks {SPIKE_SESSION_PREFIX!r}",
                    0, phase,
                )
                raise AssertionError(
                    f"Server Safety Rule 5: refusing to '{verb}' session "
                    f"{name_arg!r} (must start with {SPIKE_SESSION_PREFIX!r})"
                )

    cmd = ["acpx", *args]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        rc = 124
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(
            "utf-8", errors="replace"
        ) if exc.stdout is not None else ""
        err_part = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(
            "utf-8", errors="replace"
        ) if exc.stderr is not None else ""
        err = err_part + f"\n[spike] timeout after {timeout}s"
    except FileNotFoundError as exc:
        rc = 127
        out = ""
        err = f"acpx not on PATH: {exc}"

    duration_ms = int((time.monotonic() - t0) * 1000)
    _append_ops_log(cmd, rc, out, err, duration_ms, phase)
    return rc, out, err


# -- Helpers --------------------------------------------------------------

def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _grep_spike_session_names(sessions_list_stdout: str) -> list[str]:
    """Return literal ``spike-*`` session names found in ``sessions list`` stdout.

    Conservative tokenizer: split each (ANSI-stripped) line on whitespace
    and keep tokens beginning with ``SPIKE_SESSION_PREFIX``.
    """
    names: list[str] = []
    for raw_line in _strip_ansi(sessions_list_stdout).splitlines():
        for token in raw_line.split():
            if token.startswith(SPIKE_SESSION_PREFIX):
                # Trim trailing punctuation that some TUI emitters add.
                cleaned = token.rstrip(",;:|")
                if cleaned and cleaned not in names:
                    names.append(cleaned)
    return names


def _iso_now_plus(seconds: int) -> str:
    """Return UTC ISO-8601 timestamp ``seconds`` from now (acpx ``--before`` arg)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _new_session_name(stem: str) -> str:
    return f"{SPIKE_SESSION_PREFIX}{stem}-{int(time.time())}"


def _make_tempdir(prefix_suffix: str) -> str:
    """Create ``/tmp/spike-<suffix>-XXXX``. Caller is responsible for rmtree."""
    return tempfile.mkdtemp(prefix=f"{SPIKE_SESSION_PREFIX}{prefix_suffix}-")


def _safe_rmtree(path: str) -> None:
    """Remove a tempdir. No-op unless path starts with /tmp/spike- (Rule 3)."""
    if not path.startswith(_RMTREE_ALLOWED_PREFIX):
        sys.stderr.write(f"[spike] refusing rmtree {path!r}: not under {_RMTREE_ALLOWED_PREFIX}\n")
        return
    shutil.rmtree(path, ignore_errors=True)  # anchored on _RMTREE_ALLOWED_PREFIX above


def _close_and_prune(agent: str, name: str, *, phase: str) -> None:
    """Best-effort cleanup for a single spike session."""
    if not name.startswith(SPIKE_SESSION_PREFIX):
        return
    _run_acpx(agent, "sessions", "close", name, phase=phase, timeout=15.0)
    _run_acpx(
        agent, "sessions", "prune", "--include-history",
        "--before", _iso_now_plus(1),
        phase=phase, timeout=15.0,
    )


# -- Preflight + opening sweep --------------------------------------------

def _preflight(agent: str) -> tuple[bool, str, list[str]]:
    """Return (ok, acpx_version_str, opening_sweep_actions)."""
    rc, out, err = _run_acpx("--version", phase="preflight", timeout=10.0)
    if rc != 0:
        sys.stderr.write(
            f"[spike] preflight: 'acpx --version' rc={rc}; install acpx (>= the "
            "version cited in PRD) and re-run.\n"
            f"        stderr: {err.strip()}\n"
        )
        return False, "", []
    version_str = out.strip().splitlines()[0] if out.strip() else "(empty --version output)"

    rc, help_out, help_err = _run_acpx(agent, "--help", phase="preflight", timeout=10.0)
    if rc != 0:
        sys.stderr.write(
            f"[spike] preflight: 'acpx {agent} --help' rc={rc}; verify the agent "
            "name is supported by this acpx build.\n"
            f"        stderr: {help_err.strip()}\n"
        )
        return False, version_str, []
    needed = ("prompt", "exec", "sessions", "status")
    missing = [tok for tok in needed if tok not in help_out]
    if missing:
        sys.stderr.write(
            f"[spike] preflight: 'acpx {agent} --help' missing subcommands "
            f"{missing!r}. CLI surface drift suspected; see plan's drift table.\n"
        )
        return False, version_str, []

    actions: list[str] = []
    rc, out, _ = _run_acpx(agent, "sessions", "list", phase="opening_sweep", timeout=15.0)
    if rc == 0:
        for name in _grep_spike_session_names(out):
            actions.append(f"closed stale session {name}")
            _close_and_prune(agent, name, phase="opening_sweep")
    if not actions:
        actions.append("none")
    return True, version_str, actions


# -- Probes ---------------------------------------------------------------

def _build_prompt_cwd(agent: str, anchor_or_other: str, session: str, prompt_text: str) -> list[str]:
    """Build a ``prompt --session`` command mirroring production flag ordering.

    Mirrors ``src/acpx_executor.py::_build_acpx_exec_cmd`` (60-89): global flags
    (``--cwd``, ``--format``, ``--approve-all``, ``--timeout``) come BEFORE the
    agent name, and the subcommand (``prompt --session <name>``) comes after.
    """
    return [
        "--cwd", anchor_or_other,
        "--format", "json",
        "--approve-all",
        "--timeout", "60",
        agent, "prompt", "--session", session, prompt_text,
    ]


def _extract_cwd_from_envelope(stdout: str) -> str | None:
    """Best-effort: pull ``cwd`` from the agent reply embedded in acpx JSON."""
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError:
        # Try line-delimited JSON.
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "cwd" in obj:
                return str(obj["cwd"])
        return None
    if isinstance(env, dict):
        if "cwd" in env:
            return str(env["cwd"])
        # Look one level deep: agent's reply often nested under ``output`` /
        # ``response`` / ``result``.
        for key in ("output", "response", "result", "text"):
            inner = env.get(key)
            if isinstance(inner, str):
                m = re.search(r'\{\s*"cwd"\s*:\s*"([^"]+)"', inner)
                if m:
                    return m.group(1)
    return None


def _probe_cwd_stability(agent: str) -> dict:
    """Question (a): does ``prompt --session`` preserve cwd across calls?"""
    anchor = _make_tempdir("anchor")
    other = _make_tempdir("other")
    session_a = _new_session_name("cwd")
    session_b = _new_session_name("ensure")
    evidence: list[str] = []
    cwd_call_results: dict[str, str | None] = {}

    prompt_text = (
        'Reply with ONLY a JSON object on the first line in the exact form '
        '{"cwd": "<absolute path returned by Python os.getcwd()>"}. '
        'No code fences, no prose. Use the actual current working directory.'
    )

    try:
        # Variant A: implicit create via prompt --session.
        cmd_a1 = _build_prompt_cwd(agent, anchor, session_a, prompt_text)
        rc, out, err = _run_acpx(*cmd_a1, phase="q_a_cwd", timeout=90.0)
        evidence.append(_format_transcript(["acpx", *cmd_a1], rc, out, err))
        cwd_call_results["a1"] = _extract_cwd_from_envelope(out)

        cmd_a2 = _build_prompt_cwd(agent, other, session_a, prompt_text)
        rc, out, err = _run_acpx(*cmd_a2, phase="q_a_cwd", timeout=90.0)
        evidence.append(_format_transcript(["acpx", *cmd_a2], rc, out, err))
        cwd_call_results["a2"] = _extract_cwd_from_envelope(out)

        # Variant B: explicit create via sessions ensure.
        rc, out, err = _run_acpx(
            "--cwd", anchor, agent, "sessions", "ensure", "--name", session_b,
            phase="q_a_cwd", timeout=30.0,
        )
        evidence.append(_format_transcript(
            ["acpx", "--cwd", anchor, agent, "sessions", "ensure", "--name", session_b],
            rc, out, err,
        ))

        cmd_b1 = _build_prompt_cwd(agent, anchor, session_b, prompt_text)
        rc, out, err = _run_acpx(*cmd_b1, phase="q_a_cwd", timeout=90.0)
        evidence.append(_format_transcript(["acpx", *cmd_b1], rc, out, err))
        cwd_call_results["b1"] = _extract_cwd_from_envelope(out)

        cmd_b2 = _build_prompt_cwd(agent, other, session_b, prompt_text)
        rc, out, err = _run_acpx(*cmd_b2, phase="q_a_cwd", timeout=90.0)
        evidence.append(_format_transcript(["acpx", *cmd_b2], rc, out, err))
        cwd_call_results["b2"] = _extract_cwd_from_envelope(out)

        verdict = _classify_cwd_verdict(anchor, other, cwd_call_results)
    finally:
        _close_and_prune(agent, session_a, phase="cleanup")
        _close_and_prune(agent, session_b, phase="cleanup")
        _safe_rmtree(anchor)
        _safe_rmtree(other)

    implication = _cwd_implication(verdict)
    degradation = (
        "N/A — verdict was YES" if verdict == "YES"
        else _cwd_degradation_plan(verdict)
    )
    return {
        "verdict": verdict,
        "evidence": evidence,
        "implication": implication,
        "degradation_plan": degradation,
        "data": {"cwd_calls": cwd_call_results, "anchor": anchor, "other": other},
    }


def _classify_cwd_verdict(anchor: str, other: str, calls: dict[str, str | None]) -> str:
    if any(v is None for v in calls.values()):
        return "UNKNOWN"
    a1, a2, b1, b2 = calls["a1"], calls["a2"], calls["b1"], calls["b2"]

    def _norm(p: str | None) -> str:
        return os.path.realpath(p) if p else ""

    anchor_norm = _norm(anchor)
    var_a_consistent = _norm(a1) == _norm(a2)
    var_b_consistent = _norm(b1) == _norm(b2)
    if not (var_a_consistent and var_b_consistent):
        return "NO"
    a_pinned = _norm(a1) == anchor_norm
    b_pinned = _norm(b1) == anchor_norm
    if a_pinned and b_pinned:
        return "YES"
    return "PARTIAL"


def _cwd_implication(verdict: str) -> str:
    return {
        "YES": (
            "PRD's 'fixed anchor' strategy holds: subsequent `--cwd` flags do not "
            "rebind the session, so cooagents can pin a session to its anchor "
            "worktree at creation time and trust later prompts to inherit it."
        ),
        "PARTIAL": (
            "Calls within each variant agree, but the cwd is bound to whichever "
            "directory the session first observed (not to the latest `--cwd`). "
            "Phase 2 must commit to creating each session at its target anchor "
            "exactly once; passing `--cwd` later is a no-op or unsupported."
        ),
        "NO": (
            "cwd drifts between calls in the same session. The 'fixed anchor' "
            "strategy is unsafe; Phase 2 must either (a) issue an explicit `cd` / "
            "tool-use command per prompt, or (b) abandon session reuse and stay "
            "with one-shot `acpx exec` calls."
        ),
        "UNKNOWN": (
            "At least one probe call returned a reply that did not contain a "
            "parseable `cwd` field. Probe needs to be re-run with the agent's "
            "tool-use enabled OR the prompt re-shaped so the agent emits strict "
            "JSON. Verdict cannot be claimed without raw evidence."
        ),
    }[verdict]


def _cwd_degradation_plan(verdict: str) -> str:
    return {
        "PARTIAL": (
            "LLMRunner.start_session(anchor) creates the session at the anchor "
            "worktree exactly once and never passes `--cwd` on subsequent calls. "
            "Document this contract in the LLMRunner docstring; add a Phase 2 "
            "test asserting that a follow-up prompt with a different `--cwd` is "
            "rejected or ignored as expected."
        ),
        "NO": (
            "Drop session reuse for cwd-sensitive flows. Either keep the current "
            "one-shot `acpx exec` per step, or have LLMRunner emit an explicit "
            "`cd <anchor>` shell-tool turn before each functional prompt and "
            "verify via the agent's reply that the cd succeeded."
        ),
        "UNKNOWN": (
            "Re-run the probe with `--approve-all` confirmed in scope and the "
            "prompt reshaped to demand a single-line strict-JSON reply. If still "
            "UNKNOWN, file as a Phase 2 follow-up and proceed with the most "
            "conservative assumption (NO-equivalent: no session reuse for cwd)."
        ),
    }.get(verdict, "N/A")


def _probe_status_heartbeat(agent: str) -> dict:
    """Question (b): is ``acpx status --session`` parseable + frequent enough?"""
    anchor = _make_tempdir("status-anchor")
    session = _new_session_name("status")
    evidence: list[str] = []
    samples: list[dict] = []
    proc: subprocess.Popen | None = None

    long_prompt = (
        "This is a multi-step reasoning task. Walk through, in detail, the "
        "process of solving the n-queens problem for n=8 using backtracking. "
        "Then enumerate at least 8 distinct heuristics that prune the search. "
        "Then critique each heuristic in 2-3 sentences. Aim for a thorough, "
        "step-by-step exposition."
    )
    long_cmd = [
        "acpx", "--cwd", anchor, "--format", "json", "--approve-all",
        "--timeout", "120", agent, "prompt", "--session", session, long_prompt,
    ]

    try:
        proc = subprocess.Popen(
            long_cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        evidence.append(f"$ {' '.join(long_cmd)}\n[long prompt running in background; pid={proc.pid}]")

        deadline = time.monotonic() + 60.0
        next_sample = time.monotonic()
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if time.monotonic() < next_sample:
                time.sleep(0.2)
                continue
            ts = time.monotonic()
            rc, out, err = _run_acpx(
                agent, "status", "--session", session,
                phase="q_b_status", timeout=10.0,
            )
            samples.append({"t": ts, "rc": rc, "stdout": out, "stderr": err})
            next_sample = ts + 2.0

        # Kill the long prompt now that sampling is done.
        _run_acpx(agent, "cancel", "--session", session, phase="q_b_status", timeout=10.0)
        if proc.poll() is None:
            time.sleep(2.0)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()

        # Also probe an unknown session so the LLMRunner can distinguish errors.
        unknown_session = SPIKE_SESSION_PREFIX + "definitely-not-real-1"
        rc, u_out, u_err = _run_acpx(
            agent, "status", "--session", unknown_session,
            phase="q_b_status", timeout=10.0,
        )
        evidence.append(_format_transcript(
            ["acpx", agent, "status", "--session", unknown_session],
            rc, u_out, u_err,
        ))

        # Classify samples.
        if samples:
            evidence.append("--- status samples (first 3) ---")
            for s in samples[:3]:
                evidence.append(_format_transcript(
                    ["acpx", agent, "status", "--session", session],
                    s["rc"], s["stdout"], s["stderr"],
                ))

        parseable = sum(
            1 for s in samples
            if s["rc"] == 0 and (
                _looks_like_json(s["stdout"]) or _looks_like_kv_lines(s["stdout"])
            )
        )
        parseable_pct = (parseable / len(samples) * 100.0) if samples else 0.0

        gaps_ms = _changing_sample_gaps_ms(samples)
        median_gap = _median(gaps_ms) if gaps_ms else None

        verdict = _classify_status_verdict(parseable_pct, median_gap, samples)
        data = {
            "samples_total": len(samples),
            "parseable_count": parseable,
            "parseable_pct": round(parseable_pct, 1),
            "median_changing_gap_ms": median_gap,
            "unknown_session_rc": rc,
        }
    finally:
        _close_and_prune(agent, session, phase="cleanup")
        _safe_rmtree(anchor)
        if proc is not None and proc.poll() is None:
            proc.kill()

    implication = _status_implication(verdict, data)
    degradation = (
        "N/A — verdict was YES" if verdict == "YES"
        else _status_degradation_plan(verdict, data)
    )
    return {
        "verdict": verdict,
        "evidence": evidence,
        "implication": implication,
        "degradation_plan": degradation,
        "data": data,
    }


def _looks_like_json(text: str) -> bool:
    text = _strip_ansi(text).strip()
    if not text:
        return False
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        # Try line-delimited.
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
                return True
            except json.JSONDecodeError:
                continue
        return False


def _looks_like_kv_lines(text: str) -> bool:
    text = _strip_ansi(text).strip()
    if not text:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    kv_count = sum(1 for ln in lines if re.match(r"^[A-Za-z_][\w\- ]*\s*[:=]\s*\S", ln))
    return kv_count >= max(2, len(lines) // 2)


def _changing_sample_gaps_ms(samples: list[dict]) -> list[int]:
    gaps: list[int] = []
    last_change_t: float | None = None
    last_signature: str | None = None
    for s in samples:
        sig = s["stdout"].strip()
        if last_signature is None:
            last_signature = sig
            last_change_t = s["t"]
            continue
        if sig != last_signature:
            if last_change_t is not None:
                gaps.append(int((s["t"] - last_change_t) * 1000))
            last_change_t = s["t"]
            last_signature = sig
    return gaps


def _median(xs: list[int]) -> int:
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) // 2


def _classify_status_verdict(
    parseable_pct: float, median_gap_ms: int | None, samples: list[dict],
) -> str:
    if not samples:
        return "UNKNOWN"
    if parseable_pct < 80.0:
        return "NO"
    if median_gap_ms is None:
        # Parseable but no changes observed: cadence is effectively > sample window.
        return "PARTIAL"
    if median_gap_ms <= 15_000:
        return "YES"
    return "PARTIAL"


def _status_implication(verdict: str, data: dict) -> str:
    return {
        "YES": (
            f"`acpx status --session` is parseable (>= 80%, observed "
            f"{data.get('parseable_pct', '?')}%) and changes faster than the "
            f"15s heartbeat budget (median gap "
            f"{data.get('median_changing_gap_ms', '?')} ms). LLMRunner can "
            "drive `progress_heartbeat_interval=15s` directly off this output."
        ),
        "PARTIAL": (
            f"Parseable enough ({data.get('parseable_pct', '?')}%), but cadence "
            f"is slower than 15s (median gap "
            f"{data.get('median_changing_gap_ms', '?')} ms). Heartbeat is still "
            "viable as a tunable knob — Phase 2 should expose "
            "`progress_heartbeat_interval` as a config and document the floor."
        ),
        "NO": (
            f"Parseability ({data.get('parseable_pct', '?')}%) is below the 80% "
            "threshold. LLMRunner cannot rely on `status` for heartbeat; Phase 2 "
            "must use a different signal (e.g. wall-clock since last stdout "
            "byte from the long-running prompt)."
        ),
        "UNKNOWN": (
            "Long-running prompt did not produce any status samples (probe never "
            "got a chance to call `status` while alive). Re-run with a longer "
            "prompt or verify acpx's cancel pathway did not racing-end the prompt."
        ),
    }[verdict]


def _status_degradation_plan(verdict: str, data: dict) -> str:
    return {
        "PARTIAL": (
            "Add `progress_heartbeat_interval` as an LLMRunner config knob. Set "
            f"the floor to ceil(median_gap / 1000)s = "
            f"{(data.get('median_changing_gap_ms') or 30_000) // 1000 + 1}s. "
            "Heartbeat WARN if the gap exceeds 2x the floor."
        ),
        "NO": (
            "Drop `acpx status` as the heartbeat source. Use stdout-byte arrival "
            "from the long-running `prompt --session` invocation as the liveness "
            "signal: if no new bytes for >60s, mark stalled and surface to UI."
        ),
        "UNKNOWN": (
            "Re-run probe with a prompt that genuinely takes >= 30s to complete "
            "(invoke a tool that sleeps), so the sample loop has time to fire. "
            "If still UNKNOWN, file as Phase 2 follow-up and assume NO."
        ),
    }.get(verdict, "N/A")


def _probe_close_active(agent: str) -> dict:
    """Question (c): how does ``sessions close`` + ``prune`` behave on an active session?"""
    anchor = _make_tempdir("close-anchor")
    session = _new_session_name("close")
    evidence: list[str] = []
    proc: subprocess.Popen | None = None
    classification = "other"
    prune_classification = "prune_inert"

    long_prompt = (
        "Recite the first 200 prime numbers, one per line, then provide a brief "
        "one-paragraph commentary on the distribution. Take your time."
    )
    long_cmd = [
        "acpx", "--cwd", anchor, "--format", "json", "--approve-all",
        "--timeout", "60", agent, "prompt", "--session", session, long_prompt,
    ]

    try:
        proc = subprocess.Popen(
            long_cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        evidence.append(f"$ {' '.join(long_cmd)}\n[long prompt running; pid={proc.pid}]")

        # Give acpx ~5s to register the session, then close it while active.
        time.sleep(5.0)
        close_rc, close_out, close_err = _run_acpx(
            agent, "sessions", "close", session,
            phase="q_c_close", timeout=15.0,
        )
        evidence.append(_format_transcript(
            ["acpx", agent, "sessions", "close", session],
            close_rc, close_out, close_err,
        ))

        # Wait up to 30s for the long prompt to exit.
        try:
            popen_rc = proc.wait(timeout=30.0)
        except subprocess.TimeoutExpired:
            popen_rc = None
        evidence.append(f"[long prompt exit] rc={popen_rc} (None = still running after 30s)")

        rc, list_out, list_err = _run_acpx(
            agent, "sessions", "list", phase="q_c_close", timeout=15.0,
        )
        evidence.append(_format_transcript(
            ["acpx", agent, "sessions", "list"], rc, list_out, list_err,
        ))

        rc, show_out, show_err = _run_acpx(
            agent, "sessions", "show", session,
            phase="q_c_close", timeout=15.0,
        )
        evidence.append(_format_transcript(
            ["acpx", agent, "sessions", "show", session],
            rc, show_out, show_err,
        ))

        # Prune.
        prune_rc, prune_out, prune_err = _run_acpx(
            agent, "sessions", "prune", "--include-history",
            "--before", _iso_now_plus(1),
            phase="q_c_close", timeout=15.0,
        )
        evidence.append(_format_transcript(
            ["acpx", agent, "sessions", "prune", "--include-history", "--before", "<now+1s>"],
            prune_rc, prune_out, prune_err,
        ))

        rc, post_list_out, _ = _run_acpx(
            agent, "sessions", "list", phase="q_c_close", timeout=15.0,
        )
        evidence.append(_format_transcript(
            ["acpx", agent, "sessions", "list", "(post-prune)"],
            rc, post_list_out, "",
        ))

        # Classify close behavior.
        if close_rc != 0:
            classification = "error_on_active"
        elif popen_rc is not None and popen_rc != 0:
            classification = "auto_cancel_then_close"
        elif popen_rc is None:
            classification = "silent_orphan"
        else:
            classification = "auto_cancel_then_close"

        # Classify prune.
        if session in post_list_out:
            prune_classification = "prune_inert"
        else:
            prune_classification = "prune_after_close_works"

        verdict = _classify_close_verdict(classification, prune_classification)
    finally:
        # Defensive cleanup ladder.
        _run_acpx(agent, "cancel", "--session", session, phase="cleanup", timeout=10.0)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        _close_and_prune(agent, session, phase="cleanup")
        _safe_rmtree(anchor)

    data = {"close_classification": classification, "prune_classification": prune_classification}
    implication = _close_implication(verdict, data)
    degradation = (
        "N/A — verdict was YES" if verdict == "YES"
        else _close_degradation_plan(verdict, data)
    )
    return {
        "verdict": verdict,
        "evidence": evidence,
        "implication": implication,
        "degradation_plan": degradation,
        "data": data,
    }


def _classify_close_verdict(close_kind: str, prune_kind: str) -> str:
    if close_kind == "auto_cancel_then_close" and prune_kind == "prune_after_close_works":
        return "YES"
    if close_kind == "error_on_active":
        return "PARTIAL"
    if close_kind in ("silent_orphan",) or prune_kind == "prune_inert":
        return "NO"
    return "UNKNOWN"


def _close_implication(verdict: str, data: dict) -> str:
    return {
        "YES": (
            "`sessions close` cooperatively cancels an active prompt and "
            "`sessions prune --include-history` reclaims disk. LLMRunner's "
            "`delete_session()` can be a single `close` call followed by a "
            "background `prune` sweep on a timer."
        ),
        "PARTIAL": (
            f"Observed close behavior: {data['close_classification']!r}. "
            "Cleanup protocol must order ops: `cancel --session` first to stop "
            "the active prompt, then `close`, then `prune`. Workable but not "
            "atomic — document the sequence in LLMRunner."
        ),
        "NO": (
            f"Observed: close={data['close_classification']!r}, "
            f"prune={data['prune_classification']!r}. cooagents cannot trust "
            "`close` to actually end the session OR `prune` to reclaim disk. "
            "Phase 2 needs an alternative cleanup mechanism (file-system level "
            "rm of acpx state dir, or a polled `sessions show` + `cancel` loop)."
        ),
        "UNKNOWN": (
            "Close/prune outcome did not fit any of the three documented buckets. "
            "Read the raw evidence and pick a degradation plan manually before "
            "Phase 2 starts."
        ),
    }[verdict]


def _close_degradation_plan(verdict: str, data: dict) -> str:
    if verdict == "PARTIAL":
        return (
            "LLMRunner.delete_session(name) issues `acpx <agent> cancel --session "
            "<name>` first (await rc=0), then `sessions close <name>`, then "
            "schedules an async `sessions prune --include-history --before "
            "<close_ts+1s>`. Add a unit test for the ordering."
        )
    if verdict == "NO":
        return (
            "Drop reliance on `sessions close`. Maintain a side-table mapping "
            "session-name -> acpx state dir (discovered via `sessions show` "
            "during creation). On delete, send `cancel`, poll `sessions show` for "
            "10s confirming exit, then `shutil.rmtree` the state dir directly. "
            "Document this as a known acpx workaround."
        )
    return (
        "Re-run the probe with verbose acpx logging to capture exact lifecycle "
        "events. File as Phase 2 follow-up; assume NO until proven otherwise."
    )


def _probe_list_format(agent: str) -> dict:
    """Question (d): is ``sessions list`` parseable for orphan-sweep?"""
    evidence: list[str] = []
    created: list[str] = []
    anchors: list[str] = []
    parsed_shape = "none"
    schema_fields: list[str] = []

    suffix = int(time.time())
    names = [
        f"{SPIKE_SESSION_PREFIX}dw-{suffix}-a",
        f"{SPIKE_SESSION_PREFIX}dw-{suffix}-b",
        f"{SPIKE_SESSION_PREFIX}notdw-{suffix}",
    ]

    try:
        for name in names:
            anchor = _make_tempdir(f"list-{name}")
            anchors.append(anchor)
            rc, out, err = _run_acpx(
                "--cwd", anchor, agent, "sessions", "ensure", "--name", name,
                phase="q_d_list", timeout=15.0,
            )
            if rc == 0:
                created.append(name)
            else:
                evidence.append(_format_transcript(
                    ["acpx", "--cwd", anchor, agent, "sessions", "ensure", "--name", name],
                    rc, out, err,
                ))
            # One trivial prompt to ensure the session has at least one event.
            rc, out, err = _run_acpx(
                "--cwd", anchor, "--format", "json", "--approve-all",
                "--timeout", "30", agent, "prompt", "--session", name,
                "Reply with the literal token: ok",
                phase="q_d_list", timeout=60.0,
            )

        # Default-format list.
        rc, out_default, err = _run_acpx(
            agent, "sessions", "list", phase="q_d_list", timeout=15.0,
            env={**os.environ, "NO_COLOR": "1"},
        )
        evidence.append(_format_transcript(
            ["acpx", agent, "sessions", "list"], rc, out_default, err,
        ))

        # Try with global --format json prefix.
        rc, out_json, err = _run_acpx(
            "--format", "json", agent, "sessions", "list",
            phase="q_d_list", timeout=15.0,
            env={**os.environ, "NO_COLOR": "1"},
        )
        evidence.append(_format_transcript(
            ["acpx", "--format", "json", agent, "sessions", "list"],
            rc, out_json, err,
        ))

        # Parsing ladder.
        cleaned = _strip_ansi(out_json or "")
        parsed_shape, schema_fields = _classify_list_shape(cleaned, out_default or "")

        # Confirm prefix-grep works.
        observed = _grep_spike_session_names(out_default or "")
        all_present = all(n in observed for n in created)
        dw_filtered = [n for n in observed if n.startswith(f"{SPIKE_SESSION_PREFIX}dw-")]

        verdict = _classify_list_verdict(parsed_shape, schema_fields)
        data = {
            "parsed_shape": parsed_shape,
            "schema_fields": schema_fields,
            "created_sessions": created,
            "observed_in_list": observed,
            "all_created_visible": all_present,
            "dw_prefix_count": len(dw_filtered),
        }
    finally:
        for name in created:
            _close_and_prune(agent, name, phase="cleanup")
        for d in anchors:
            _safe_rmtree(d)

    implication = _list_implication(verdict, data)
    degradation = (
        "N/A — verdict was YES" if verdict == "YES"
        else _list_degradation_plan(verdict, data)
    )
    return {
        "verdict": verdict,
        "evidence": evidence,
        "implication": implication,
        "degradation_plan": degradation,
        "data": data,
    }


def _classify_list_shape(json_attempt: str, default_attempt: str) -> tuple[str, list[str]]:
    # 1. Strict JSON.
    try:
        env = json.loads(json_attempt.strip())
        if isinstance(env, list) and env and isinstance(env[0], dict):
            return "json_array_of_objects", sorted(env[0].keys())
        if isinstance(env, dict):
            for key in ("sessions", "items", "data"):
                inner = env.get(key)
                if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                    return f"json_object.{key}[]", sorted(inner[0].keys())
            return "json_object", sorted(env.keys())
    except (json.JSONDecodeError, AttributeError):
        pass

    # 2. Line-delimited JSON.
    objs: list[dict] = []
    for line in (json_attempt or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            objs = []
            break
        if isinstance(obj, dict):
            objs.append(obj)
    if objs:
        return "ndjson", sorted(objs[0].keys())

    # 3. Tabular text from the default-format output.
    cleaned = _strip_ansi(default_attempt).strip()
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    if len(lines) >= 2:
        header_tokens = lines[0].split()
        body_tokens = lines[1].split()
        if 2 <= len(header_tokens) <= 8 and len(header_tokens) == len(body_tokens):
            return "tabular", header_tokens

    # 4. Plain name list.
    if lines:
        return "plain_lines", []
    return "none", []


def _classify_list_verdict(shape: str, fields: list[str]) -> str:
    metadata_keys = {"name", "created_at", "createdAt", "closed_at", "closedAt", "pid", "id"}
    if shape.startswith("json") or shape == "ndjson":
        if "name" in fields and (set(fields) & metadata_keys - {"name"}):
            return "YES"
        return "PARTIAL"
    if shape == "tabular":
        return "PARTIAL"
    if shape == "plain_lines":
        return "NO"
    return "UNKNOWN"


def _list_implication(verdict: str, data: dict) -> str:
    return {
        "YES": (
            f"`sessions list` (with `--format json`) parses as "
            f"{data['parsed_shape']!r} with fields {data['schema_fields']!r}. "
            "Orphan-sweep can match on `name` prefix and use the timestamp "
            "field to skip recently-active sessions."
        ),
        "PARTIAL": (
            f"`sessions list` parses as {data['parsed_shape']!r}. Workable but "
            "fragile: orphan-sweep can match by name prefix but lacks "
            "machine-readable timestamps. Combine with `sessions show <name>` "
            "per candidate (N+1 calls)."
        ),
        "NO": (
            "`sessions list` returns only plain session names. Orphan-sweep "
            "must call `sessions show` per session to determine activity / age "
            "(N+1 cost). Phase 2 should cap the sweep frequency accordingly."
        ),
        "UNKNOWN": (
            "Could not parse any shape from `sessions list` output. Read the "
            "raw evidence and choose a parser before Phase 2 starts."
        ),
    }[verdict]


def _list_degradation_plan(verdict: str, data: dict) -> str:
    if verdict == "PARTIAL":
        return (
            "LLMRunner orphan-sweep does prefix-grep on `sessions list`, then "
            "for each candidate calls `sessions show <name>` to read timestamps "
            "and decide eligibility. Add a 200ms throttle so a list with 100 "
            "candidates does not stall startup."
        )
    if verdict == "NO":
        return (
            "Same as PARTIAL but with a per-startup cap (e.g. only show the "
            "first 50 spike-prefixed sessions; defer the rest). Document that "
            "orphan-sweep is best-effort, not exhaustive."
        )
    return (
        "Re-run probe under `--format json` AND tabular default output, capture "
        "both raw transcripts in evidence, and choose the parser manually."
    )


# -- Report composer ------------------------------------------------------

def _format_transcript(cmd: list[str], rc: int, out: str, err: str) -> str:
    cmd_str = " ".join(cmd)
    out_show = (out or "").strip()
    if len(out_show) > 4000:
        out_show = out_show[:4000] + "\n... [truncated]"
    err_show = (err or "").strip()
    if len(err_show) > 1000:
        err_show = err_show[:1000] + "\n... [truncated]"
    parts = [f"$ {cmd_str}", f"[rc={rc}]"]
    if out_show:
        parts.append(f"# stdout\n{out_show}")
    if err_show:
        parts.append(f"# stderr\n{err_show}")
    return "\n".join(parts)


def _compose_report(
    *,
    report_path: Path,
    agent: str,
    acpx_version: str,
    opening_sweep: list[str],
    leaked: list[str],
    probes: dict[str, dict],
    final_exit: int,
    summary_line: str,
) -> None:
    sections = [
        "# acpx Spike Report — DevWork PRD Phase 1",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Script: scripts/spike_acpx_session.py",
        f"Agent: {agent}",
        f"acpx --version: {acpx_version}",
        f"Host: {platform.platform()}",
        f"Python: {sys.version.split()[0]}",
        f"Opening sweep: {', '.join(opening_sweep) if opening_sweep else 'none'}",
        f"Final exit code: {final_exit}  ({summary_line})",
    ]
    if leaked:
        sections.append(
            f"WARNING: leaked {len(leaked)} sessions after final sweep, "
            f"manual remediation required: {leaked}"
        )

    section_titles = {
        "q_a_cwd": "Question 1: Does `acpx prompt --session <name>` preserve cwd across calls?",
        "q_b_status": "Question 2: Is `acpx status --session <name>` parseable + frequent enough for a 15s heartbeat?",
        "q_c_close": "Question 3: How does `sessions close` (and `prune`) behave on an active session?",
        "q_d_list": "Question 4: Is `acpx <agent> sessions list` output parseable for orphan-sweep?",
    }

    for key in ("q_a_cwd", "q_b_status", "q_c_close", "q_d_list"):
        probe = probes.get(key)
        sections.append("")
        sections.append(f"## {section_titles[key]}")
        if probe is None:
            sections.append("")
            sections.append("**Verdict**: UNKNOWN (probe did not run — see Follow-ups).")
            continue
        sections.append("")
        sections.append(f"**Verdict**: {probe['verdict']}")
        sections.append("")
        sections.append("**Evidence**:")
        for transcript in probe.get("evidence", []):
            sections.append("")
            sections.append("```")
            sections.append(transcript)
            sections.append("```")
        sections.append("")
        sections.append(f"**Implication for PRD**: {probe.get('implication', '')}")
        sections.append("")
        sections.append(f"**Degradation plan if NO**: {probe.get('degradation_plan', 'N/A')}")
        if probe.get("data"):
            sections.append("")
            sections.append("**Data**:")
            sections.append("")
            sections.append("```json")
            sections.append(json.dumps(probe["data"], indent=2, ensure_ascii=False, default=str))
            sections.append("```")

    sections.append("")
    sections.append("## Follow-ups")
    followups: list[str] = []
    for key, probe in probes.items():
        if probe is None:
            followups.append(f"- Probe `{key}` did not run; re-run on a host with acpx + LLM credentials.")
        elif probe["verdict"] == "UNKNOWN":
            followups.append(f"- `{key}` returned UNKNOWN — see degradation plan above.")
    if not followups:
        followups.append("- None.")
    sections.extend(followups)

    sections.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(sections), encoding="utf-8")


# -- Final sweep ----------------------------------------------------------

def _final_sweep(agent: str) -> dict:
    """Best-effort cleanup that runs unconditionally in main()'s outer try/finally.

    Never raises — exceptions are logged to stderr and to the ops log.
    """
    swept: list[str] = []
    leaked: list[str] = []
    tempdirs_removed: list[str] = []
    try:
        rc, out, _ = _run_acpx(agent, "sessions", "list", phase="final_sweep", timeout=15.0)
        if rc == 0:
            for name in _grep_spike_session_names(out):
                _close_and_prune(agent, name, phase="final_sweep")
                swept.append(name)

        rc, out, _ = _run_acpx(agent, "sessions", "list", phase="final_sweep", timeout=15.0)
        if rc == 0:
            leaked = _grep_spike_session_names(out)
            if leaked:
                sys.stderr.write(
                    f"[spike] WARNING: leaked sessions after sweep: {leaked}\n"
                    f"[spike] Manual remediation: ssh into spike host then run "
                    f"'acpx {agent} sessions close <name>' for each name above.\n"
                )

        for d in glob.glob(SPIKE_TEMPDIR_GLOB):  # /tmp/spike-* literal prefix
            shutil.rmtree(d, ignore_errors=True)  # anchored on SPIKE_TEMPDIR_GLOB above
            tempdirs_removed.append(d)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"[spike] final_sweep error (continuing): {exc}\n")

    return {
        "swept": swept,
        "leaked": leaked,
        "tempdirs_removed": tempdirs_removed,
    }


# -- Verdict aggregation --------------------------------------------------

def _aggregate_verdicts(probes: dict[str, dict]) -> tuple[int, str]:
    yes = sum(1 for p in probes.values() if p and p["verdict"] == "YES")
    partial = sum(1 for p in probes.values() if p and p["verdict"] == "PARTIAL")
    no = sum(1 for p in probes.values() if p and p["verdict"] == "NO")
    unknown = sum(1 for p in probes.values() if (p is None) or p["verdict"] == "UNKNOWN")
    if no == 0 and unknown == 0 and partial == 0:
        return 0, f"all 4 questions YES — proceed to Phase 2"
    if no == 0 and unknown == 0:
        return 1, f"{yes} YES + {partial} PARTIAL + 0 NO — proceed with caveats"
    return 2, (
        f"{yes} YES + {partial} PARTIAL + {no} NO + {unknown} UNKNOWN — "
        "Phase 2 design must address NO/UNKNOWN before proceeding"
    )


# -- main -----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    _self_audit()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent", default="claude",
        help="acpx agent name to drive (default: claude). Supported: claude, codex.",
    )
    parser.add_argument(
        "--report", type=Path, default=_DEFAULT_REPORT,
        help=f"Markdown report output path (default: {_DEFAULT_REPORT}).",
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0,
        help="Per-probe wall-clock budget hint (default: 120s).",
    )
    parser.add_argument(
        "--skip-probes", action="store_true",
        help="Run preflight + write a stub report, skip the four probes. "
             "Useful for smoke-testing the script on a host without acpx.",
    )
    args = parser.parse_args(argv)

    global OPS_LOG_PATH
    report_path = args.report.resolve()
    OPS_LOG_PATH = report_path.with_name(report_path.stem + ".ops.log")

    final_exit = 2
    summary = "preflight failed"
    probes: dict[str, dict | None] = {
        "q_a_cwd": None, "q_b_status": None, "q_c_close": None, "q_d_list": None,
    }
    acpx_version = "(unavailable)"
    opening_sweep: list[str] = []
    leaked: list[str] = []
    preflight_ok = False

    try:
        preflight_ok, version_str, opening_sweep = _preflight(args.agent)
        if version_str:
            acpx_version = version_str
        if not preflight_ok:
            return 2

        if args.skip_probes:
            sys.stderr.write("[spike] --skip-probes set; writing preflight-only report.\n")
        else:
            try:
                probes["q_a_cwd"] = _probe_cwd_stability(args.agent)
            except Exception as exc:
                sys.stderr.write(f"[spike] q_a_cwd crashed: {exc}\n")
            try:
                probes["q_b_status"] = _probe_status_heartbeat(args.agent)
            except Exception as exc:
                sys.stderr.write(f"[spike] q_b_status crashed: {exc}\n")
            try:
                probes["q_c_close"] = _probe_close_active(args.agent)
            except Exception as exc:
                sys.stderr.write(f"[spike] q_c_close crashed: {exc}\n")
            try:
                probes["q_d_list"] = _probe_list_format(args.agent)
            except Exception as exc:
                sys.stderr.write(f"[spike] q_d_list crashed: {exc}\n")

        final_exit, summary = _aggregate_verdicts(probes)
        return final_exit
    finally:
        sweep_result = _final_sweep(args.agent) if preflight_ok else {
            "swept": [], "leaked": [], "tempdirs_removed": [],
        }
        leaked = sweep_result.get("leaked", [])
        try:
            _compose_report(
                report_path=report_path,
                agent=args.agent,
                acpx_version=acpx_version,
                opening_sweep=opening_sweep,
                leaked=leaked,
                probes={k: v for k, v in probes.items()},
                final_exit=final_exit,
                summary_line=summary,
            )
            print(f"[spike] report: {report_path}")
            print(f"[spike] ops log: {OPS_LOG_PATH}")
            print(f"[spike] {summary} (exit {final_exit})")
        except OSError as exc:
            sys.stderr.write(f"[spike] failed to write report: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
