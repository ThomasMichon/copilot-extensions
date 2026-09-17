"""Standalone mux pane lifecycle primitives plus a diagnostic CLI harness.

``handoff-cutover`` currently owns pane spawn, confirmation, foregrounding, and
retirement inside one larger choreography. This module isolates the mux-facing
create/terminate legs so they can be exercised directly -- both hermetically and
against a disposable live mux session -- before the broader handoff flow is
rewired onto them.
"""

from __future__ import annotations

import argparse
import re
import secrets
import subprocess
import time
from pathlib import Path

from . import activity, output

# Provisional, intentionally overridable signatures for "Copilot exited cleanly"
# seen in a pane capture. Liveness remains the authoritative gone/not-gone
# verdict; these patterns are additive observability only until the new harness
# can capture a live real-world shutdown transcript and tighten them.
_COPILOT_EXIT_SIGNATURE_PATTERNS: tuple[str, ...] = (
    r"(?im)\b(?:goodbye|session ended|thanks for using)\b",
    r"(?m)^(?:PS [^\r\n>]+>|[A-Za-z]:\\[^>\r\n]*>|[^@\r\n]+@[^:\r\n]+:[^\r\n$#]*[$#])\s?$",
)


def _normalize_cmd_remainder(cmd: list[str]) -> list[str]:
    """Drop argparse's leading ``--`` separator from a remainder argv."""
    if cmd and cmd[0] == "--":
        return cmd[1:]
    return list(cmd)


def _parse_env_assignments(values: list[str]) -> dict[str, str]:
    """Parse repeatable ``KEY=VALUE`` CLI env overrides."""
    env: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"invalid --env value {item!r}; expected KEY=VALUE")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid --env value {item!r}; key is empty")
        env[key] = value
    return env


def _pane_receipt_path(token: str) -> Path:
    from . import sessions

    return sessions._initial_prompt_receipt_path(token)


def _wait_for_launch_receipt(
    receipt_path: Path,
    pane_id: str | None,
    *,
    mux_bin: str,
    receipt_timeout: float,
    startup_grace: float,
) -> tuple[bool, str | None]:
    """Wait for the pane wrapper's launch receipt and startup grace outcome."""
    from . import sessions

    status: str | None = None
    deadline = time.monotonic() + max(receipt_timeout, 0.0)
    while time.monotonic() < deadline:
        if receipt_path.exists():
            try:
                candidate = receipt_path.read_text("utf-8").strip()
            except OSError:
                candidate = ""
            if candidate == "launching" or candidate.startswith("failed:"):
                status = candidate
                break
        time.sleep(0.05)
    if status == "launching":
        startup_deadline = time.monotonic() + max(startup_grace, 0.0)
        while time.monotonic() < startup_deadline:
            try:
                status = receipt_path.read_text("utf-8").strip()
            except OSError:
                status = None
            if status != "launching":
                break
            if not pane_id or not sessions._mux_pane_alive(pane_id, mux_bin):
                status = "failed:pane-exited"
                break
            time.sleep(0.05)
        confirmed = bool(
            status == "launching"
            and pane_id
            and sessions._mux_pane_alive(pane_id, mux_bin)
        )
        if status == "launching" and not confirmed:
            status = "failed:pane-exited"
        return confirmed, status
    return False, status


def pane_create(
    worktree_id: str,
    work_dir: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    mux: str | None = None,
    session_name: str | None = None,
    payload_receipt_token: str | None = None,
    initial_prompt: str | None = None,
    prompt_receipt_timeout: float = 8.0,
    prompt_startup_grace: float = 3.5,
) -> dict[str, object]:
    """Create, confirm, and foreground a mux pane/session running ``cmd``."""
    from . import sessions

    mux_bin = sessions._mux_bin(mux)
    resolved_session = (
        str(session_name).strip() if session_name else sessions.mux_session_name(worktree_id)
    )
    if session_name:
        has_target_session = sessions.has_mux_session_named(resolved_session, mux=mux)
        create_kind = "new-window"
    else:
        has_target_session = sessions.has_mux_session(worktree_id)
        create_kind = "new-window" if has_target_session else "new-session"
    if session_name and not has_target_session:
        return {
            "ok": False,
            "new_pane": None,
            "pane_id": None,
            "prompt_received": False,
            "prompt_status": None,
            "receipt_received": False,
            "receipt_status": None,
            "foregrounded": False,
            "mux_session": resolved_session,
            "error": f"no live mux session {resolved_session}",
        }

    receipt_token = payload_receipt_token or secrets.token_hex(16)
    receipt_path = _pane_receipt_path(receipt_token)
    receipt_path.unlink(missing_ok=True)

    activity.log_event(
        "pane_create_started",
        worktree_id=worktree_id,
        mux_session=resolved_session,
        method=create_kind,
        payload_receipt_token=receipt_token,
    )

    try:
        if create_kind == "new-window":
            argv = sessions.build_mux_new_window_argv(
                worktree_id,
                work_dir,
                cmd,
                env,
                mux=mux,
                initial_prompt=initial_prompt,
                prompt_receipt=str(receipt_path),
                session_name=resolved_session,
            )
        else:
            argv = sessions.build_mux_new_session_argv(
                worktree_id,
                work_dir,
                cmd,
                env,
                mux=mux,
                initial_prompt=initial_prompt,
                prompt_receipt=str(receipt_path),
            )
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        receipt_path.unlink(missing_ok=True)
        return {
            "ok": False,
            "new_pane": None,
            "pane_id": None,
            "prompt_received": False,
            "prompt_status": None,
            "receipt_received": False,
            "receipt_status": None,
            "foregrounded": False,
            "mux_session": resolved_session,
            "error": str(exc),
        }

    if result.returncode != 0:
        receipt_path.unlink(missing_ok=True)
        return {
            "ok": False,
            "new_pane": None,
            "pane_id": None,
            "prompt_received": False,
            "prompt_status": None,
            "receipt_received": False,
            "receipt_status": None,
            "foregrounded": False,
            "mux_session": resolved_session,
            "error": result.stderr.strip() or f"exit {result.returncode}",
        }

    new_pane = result.stdout.strip() or None
    if not new_pane:
        receipt_path.unlink(missing_ok=True)
        return {
            "ok": False,
            "new_pane": None,
            "pane_id": None,
            "prompt_received": False,
            "prompt_status": None,
            "receipt_received": False,
            "receipt_status": None,
            "foregrounded": False,
            "mux_session": resolved_session,
            "error": "mux created a pane but returned no pane id",
        }

    activity.log_event(
        "mux_session_assigned",
        worktree_id=worktree_id,
        mux_session=resolved_session,
        new_pane=new_pane,
        method=f"pane_create:{create_kind}",
    )

    prompt_received, prompt_status = _wait_for_launch_receipt(
        receipt_path,
        new_pane,
        mux_bin=mux_bin,
        receipt_timeout=prompt_receipt_timeout,
        startup_grace=prompt_startup_grace,
    )
    receipt_path.unlink(missing_ok=True)
    if not prompt_received:
        process_tree = sessions._mux_pane_process_tree(new_pane, mux=mux)
        cleanup = sessions._retire_failed_successor(new_pane, process_tree, mux=mux)
        return {
            "ok": False,
            "new_pane": new_pane,
            "pane_id": new_pane,
            "prompt_received": False,
            "prompt_status": prompt_status,
            "receipt_received": False,
            "receipt_status": prompt_status,
            "foregrounded": False,
            "mux_session": resolved_session,
            "cleanup": cleanup,
            "error": (
                "successor did not confirm a stable pane launch "
                f"(status: {prompt_status or 'no-receipt'})"
            ),
        }

    foregrounded = sessions.mux_focus_pane(resolved_session, new_pane, mux=mux)
    payload: dict[str, object] = {
        "ok": foregrounded,
        "new_pane": new_pane,
        "pane_id": new_pane,
        "prompt_received": True,
        "prompt_status": prompt_status,
        "receipt_received": True,
        "receipt_status": prompt_status,
        "foregrounded": foregrounded,
        "mux_session": resolved_session,
        "method": create_kind,
        "error": None,
    }
    if not foregrounded:
        payload["error"] = "pane launch confirmed, but mux_focus_pane could not foreground it"
    return payload


def _capture_pane_output(
    pane_id: str,
    *,
    mux_bin: str,
) -> str:
    """Best-effort capture of the pane's visible output."""
    try:
        result = subprocess.run(
            [mux_bin, "capture-pane", "-t", pane_id, "-p"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _first_matching_exit_signature(
    pane_text: str,
    patterns: tuple[str, ...],
) -> str | None:
    """Return the first configured clean-exit pattern found in ``pane_text``."""
    for pattern in patterns:
        try:
            if re.search(pattern, pane_text):
                return pattern
        except re.error:
            continue
    return None


def _cleanup_pane_lock_residue(
    pane_id: str,
    pane_session: str | None,
    process_tree: set[int],
) -> list[dict[str, object]]:
    """Remove stale ``inuse.<pid>.lock`` residue attributable to one pane."""
    from . import reclaim, sessions

    session_name = pane_session or sessions.current_mux_session(pane_id)
    if not session_name or not session_name.startswith("wt-"):
        return []
    worktree_id = sessions.worktree_id_from_mux_session(session_name)
    if not worktree_id:
        return []
    try:
        table = reclaim.build_process_table()
    except OSError:
        table = None
    return reclaim.clear_lock_residue(
        worktree_id=worktree_id,
        force_pids={pid for pid in process_tree if pid > 0},
        table=table,
    )


def pane_terminate(
    pane_id: str,
    *,
    mux: str | None = None,
    mux_session: str | None = None,
    overall_budget: float = 30.0,
    poll_interval: float = 0.3,
    ctrl_c_gap: float = 0.6,
    escalate_after: float = 1.5,
    hard_kill_settle: float = 1.5,
    exit_signature_patterns: tuple[str, ...] | list[str] | None = None,
) -> dict[str, object]:
    """Gracefully terminate one pane, falling back to kill-pane on timeout."""
    from . import sessions

    mux_bin = sessions._mux_bin(mux)
    patterns = tuple(exit_signature_patterns or _COPILOT_EXIT_SIGNATURE_PATTERNS)
    live_session = mux_session or sessions.current_mux_session(pane_id, mux=mux)

    if not sessions._mux_pane_alive(pane_id, mux_bin):
        return {
            "ok": True,
            "pane": pane_id,
            "gone": True,
            "method": "already-gone",
            "signature_seen": False,
            "signature_pattern": None,
            "locks_cleared": [],
        }

    guard = sessions._mux_last_window_guard(pane_id, mux_bin)
    if guard:
        activity.log_event(
            "handoff_retire_guard",
            source="python",
            old_pane=pane_id,
            reason="last-window-skip",
            method="guard",
            outcome="left-running",
            mux_session=guard.get("session"),
            window_count=guard.get("window_count"),
        )
        return {
            "ok": True,
            "pane": pane_id,
            "gone": False,
            "method": "last-window-skip",
            "session": guard.get("session"),
            "signature_seen": False,
            "signature_pattern": None,
            "locks_cleared": [],
        }

    process_tree = sessions._mux_pane_process_tree(pane_id, mux=mux)
    signature_pattern: str | None = None

    def _observe_until(window: float) -> bool:
        nonlocal signature_pattern

        deadline = time.monotonic() + max(window, 0.0)
        while time.monotonic() < deadline:
            if not sessions._mux_pane_alive(pane_id, mux_bin):
                return True
            capture = _capture_pane_output(pane_id, mux_bin=mux_bin)
            if signature_pattern is None:
                signature_pattern = _first_matching_exit_signature(capture, patterns)
            time.sleep(poll_interval)
        if not sessions._mux_pane_alive(pane_id, mux_bin):
            return True
        capture = _capture_pane_output(pane_id, mux_bin=mux_bin)
        if signature_pattern is None:
            signature_pattern = _first_matching_exit_signature(capture, patterns)
        return not sessions._mux_pane_alive(pane_id, mux_bin)

    def _send_ctrl_c() -> bool:
        try:
            result = subprocess.run(
                [mux_bin, "send-keys", "-t", pane_id, "C-c"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    graceful_budget = max(overall_budget - max(hard_kill_settle, 0.0), 0.0)
    escalate_window = min(max(escalate_after, 0.0), graceful_budget)

    if not _send_ctrl_c():
        gone = not sessions._mux_pane_alive(pane_id, mux_bin)
        return {
            "ok": gone,
            "pane": pane_id,
            "gone": gone,
            "method": "already-gone" if gone else "failed",
            "session": live_session,
            "signature_seen": bool(signature_pattern),
            "signature_pattern": signature_pattern,
            "locks_cleared": [],
        }
    _observe_until(0.0)
    time.sleep(ctrl_c_gap)
    _send_ctrl_c()
    if _observe_until(escalate_window):
        return {
            "ok": True,
            "pane": pane_id,
            "gone": True,
            "method": (
                "graceful-signature-confirmed"
                if signature_pattern is not None
                else "graceful"
            ),
            "session": live_session,
            "signature_seen": bool(signature_pattern),
            "signature_pattern": signature_pattern,
            "locks_cleared": [],
        }

    _send_ctrl_c()
    if _observe_until(max(graceful_budget - escalate_window, 0.0)):
        return {
            "ok": True,
            "pane": pane_id,
            "gone": True,
            "method": (
                "graceful-signature-confirmed"
                if signature_pattern is not None
                else "graceful"
            ),
            "session": live_session,
            "signature_seen": bool(signature_pattern),
            "signature_pattern": signature_pattern,
            "locks_cleared": [],
        }

    try:
        subprocess.run(
            [mux_bin, "kill-pane", "-t", pane_id],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    gone = _observe_until(max(hard_kill_settle, 0.0))
    locks_cleared: list[dict[str, object]] = []
    if gone:
        locks_cleared = _cleanup_pane_lock_residue(
            pane_id,
            live_session,
            process_tree,
        )
    return {
        "ok": gone,
        "pane": pane_id,
        "gone": gone,
        "method": "hard" if gone else "failed",
        "session": live_session,
        "signature_seen": bool(signature_pattern),
        "signature_pattern": signature_pattern,
        "locks_cleared": locks_cleared,
    }


def cmd_pane_create(args: argparse.Namespace) -> int:
    """Invoke ``pane_create`` and print a JSON result."""
    try:
        env = _parse_env_assignments(getattr(args, "env", []) or [])
    except ValueError as exc:
        return output._json_error(str(exc), exit_code=2)
    cmd = _normalize_cmd_remainder(getattr(args, "cmd", []) or [])
    if not cmd:
        return output._json_error("pane-create requires a payload command after --", exit_code=2)
    result = pane_create(
        args.worktree_id,
        args.work_dir,
        cmd,
        env or None,
        mux=getattr(args, "mux", None),
        session_name=getattr(args, "session_name", None),
        payload_receipt_token=getattr(args, "payload_receipt_token", None),
        initial_prompt=getattr(args, "initial_prompt", None),
        prompt_receipt_timeout=getattr(args, "receipt_timeout", 8.0),
        prompt_startup_grace=getattr(args, "startup_grace", 3.5),
    )
    output._json_output(result)
    return 0 if result.get("ok") else 1


def cmd_pane_terminate(args: argparse.Namespace) -> int:
    """Invoke ``pane_terminate`` and print a JSON result."""
    result = pane_terminate(
        args.pane_id,
        mux=getattr(args, "mux", None),
        mux_session=getattr(args, "mux_session", None),
        overall_budget=getattr(args, "overall_budget", 30.0),
        poll_interval=getattr(args, "poll_interval", 0.3),
        ctrl_c_gap=getattr(args, "ctrl_c_gap", 0.6),
        escalate_after=getattr(args, "escalate_after", 1.5),
        hard_kill_settle=getattr(args, "hard_kill_settle", 1.5),
        exit_signature_patterns=getattr(args, "exit_pattern", None),
    )
    output._json_output(result)
    return 0 if result.get("ok") else 1


def register_cli(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the isolated pane lifecycle diagnostic harness commands."""
    p = sub.add_parser(
        "pane-create",
        help="Diagnostic mux primitive: create, bootstrap-confirm, and foreground a pane",
        description=(
            "Create a pane or detached worktree mux session in isolation, wait "
            "for the pane wrapper's bootstrap receipt, and then foreground the "
            "result explicitly. Manual live validation: use only against a "
            "throwaway worktree or detached session, never the attached session "
            "you are actively driving from."
        ),
    )
    p.add_argument("--worktree-id", required=True, help="Worktree id owning the pane/session")
    p.add_argument("--work-dir", required=True, help="Working directory for the pane payload")
    p.add_argument("--mux", default=None, help="Override the mux binary (tmux/psmux)")
    p.add_argument(
        "--session-name",
        default=None,
        help="Existing mux session to target; if omitted, resolve wt-<id> and create it on demand",
    )
    p.add_argument(
        "--initial-prompt",
        default=None,
        help="Optional seed appended as a native --interactive argument by the pane wrapper",
    )
    p.add_argument(
        "--payload-receipt-token",
        default=None,
        help="Explicit receipt token for live diagnostics (default: random)",
    )
    p.add_argument(
        "--receipt-timeout",
        type=float,
        default=8.0,
        help="Seconds to wait for the wrapper to acknowledge landing (default: 8)",
    )
    p.add_argument(
        "--startup-grace",
        type=float,
        default=3.5,
        help="Seconds the child must survive after receipt before success (default: 3.5)",
    )
    p.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Repeatable environment override for the pane payload",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="JSON output mode (stdout is JSON only; always on)",
    )
    p.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Payload command argv; pass after --",
    )

    p = sub.add_parser(
        "pane-terminate",
        help="Diagnostic mux primitive: gracefully retire one pane in isolation",
        description=(
            "Retire one pane with the existing Ctrl-C ladder plus live "
            "capture-pane shutdown-signature checks, falling back to kill-pane "
            "and stale-lock cleanup inside one bounded budget. Manual live "
            "validation: use only against a throwaway worktree or detached "
            "session, never the attached session you are actively driving from."
        ),
    )
    p.add_argument("--pane-id", required=True, help="Exact pane id to terminate")
    p.add_argument("--mux", default=None, help="Override the mux binary (tmux/psmux)")
    p.add_argument(
        "--mux-session",
        default=None,
        help="Known mux session containing the pane (diagnostic context only)",
    )
    p.add_argument(
        "--overall-budget",
        type=float,
        default=30.0,
        help="Total seconds budget, including any hard-kill settle (default: 30)",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=0.3,
        help="Pane liveness/signature poll interval in seconds (default: 0.3)",
    )
    p.add_argument(
        "--ctrl-c-gap",
        type=float,
        default=0.6,
        help="Seconds between the first and second Ctrl-C (default: 0.6)",
    )
    p.add_argument(
        "--escalate-after",
        type=float,
        default=1.5,
        help="Seconds to wait after the double Ctrl-C before the conditional third (default: 1.5)",
    )
    p.add_argument(
        "--hard-kill-settle",
        type=float,
        default=1.5,
        help="Seconds reserved after kill-pane to confirm disappearance (default: 1.5)",
    )
    p.add_argument(
        "--exit-pattern",
        action="append",
        default=None,
        help="Repeatable regex override for clean-exit signature detection",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="JSON output mode (stdout is JSON only; always on)",
    )
