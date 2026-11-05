"""Additive ordinary-embodiment receipts; native restart keeps its own contract."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time

from yaml import YAMLError

from . import config as cfg, output, session_liveness, sessions, tracking


def wait_timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be finite, nonnegative seconds") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("must be finite, nonnegative seconds")
    return seconds


def _observe(result: dict, bindings: dict) -> dict:
    observation = {"state": "unknown", "session_id": None, "session_verified": False,
                   "session_reason": "registration-unavailable", "native_process": None}
    wt_id = result.get("worktree_id")
    if not wt_id or result.get("anchor"):
        return observation
    try:
        record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
        sid = record.resolved_head_session
        if not sid:
            return {**observation, "state": "starting", "session_reason": "unregistered"}
        if not isinstance(sid, str) or sid in (".", "..") or "/" in sid or "\\" in sid:
            return observation
        entry = record.session_entry(sid)
        expected_resume = result.get("resume_session")
        pane = result.get("new_pane")
        if result.get("created") and (
            (expected_resume and sid != expected_resume)
            or not pane or entry is None or entry.pane_id != pane
        ):
            return {**observation, "state": "starting", "session_reason": "head-not-launch"}
        observation["session_id"] = sid
        live = session_liveness.session_liveness(sid)
        if live != "live":
            return {**observation, "session_reason": f"native-{live}"}
        # A single process/mux census per compatible live candidate, not per poll.
        key = (sid, entry.pane_id if entry else None)
        if key not in bindings:
            bindings[key] = sessions.mux_binding_for_session(
                sid, expected_session_name=result.get("session"),
            )
        binding = bindings[key]
        if not binding:
            return {**observation, "session_reason": "mux-binding-unconfirmed"}
        if (
            binding.get("session_name") != sessions.mux_session_name(wt_id)
            or binding.get("session_name") != result.get("session")
            or (result.get("created") and binding.get("pane_id") != pane)
        ):
            return {**observation, "session_reason": "mux-binding-mismatch"}
        # Re-read the head after the process observation; don't confirm an owner
        # replaced while the census was running.
        if tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml").resolved_head_session != sid:
            bindings[key] = None
            return {**observation, "session_reason": "head-changed"}
        return {**observation, "state": "live", "session_verified": True,
                "session_reason": "native-mux-binding",
                "native_process": {**binding, "worktree_id": wt_id}}
    except (OSError, ValueError, AttributeError, KeyError, TypeError, RuntimeError,
            subprocess.SubprocessError, YAMLError):
        for key in bindings:
            bindings[key] = None
        return observation


def run(args, operation) -> int:
    timeout = getattr(args, "wait_session_timeout", 0.0) or 0.0
    if not math.isfinite(timeout) or timeout < 0:
        return output._json_error("--wait-session-timeout must be finite and nonnegative", 2)
    target = {}
    with output.capture_json_output() as captured, output.stdout_to_stderr():
        rc = operation(target)
    result = json.loads(captured.getvalue())
    result = {**target, **result}
    result.setdefault("ok", rc == 0)
    if isinstance(result.get("worktree"), dict):
        result.setdefault("worktree_id", result["worktree"].get("id"))
        result.setdefault("work_dir", result["worktree"].get("path"))
    action = (
        "dry-run" if result.get("dry_run") else
        "reuse" if result.get("created") is False else
        ("resume" if result.get("resume_session") else "create") if (
            result.get("created") and result.get("session")
        ) else
        "attempted" if result.get("launch_attempted") else "not-started"
    )
    state = "not-started" if action == "not-started" else "launch-failed" if rc else "dry-run"
    receipt = {"embody_receipt": {"schema": "agent-worktrees.embody", "version": 1,
                                 "supports_wait_session": True},
               "launch_action": action, "state": state,
               "session_id": None, "session_verified": False,
               "session_reason": state,
               "native_process": None, "session_wait": "not-requested"}
    if action in ("reuse", "resume", "create"):
        deadline = time.monotonic() + (timeout if not rc else 0)
        bindings = {}
        while True:
            receipt.update(_observe(result, bindings))
            if receipt["session_verified"]:
                if timeout and not rc:
                    receipt["session_wait"] = "confirmed"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if timeout and not rc:
                    receipt["session_wait"] = "timeout"
                    result["ok"] = False
                    result["error"] = (
                        "Native session was not confirmed before the observation deadline; "
                        "launch may have occurred. Inspect this target before retrying; "
                        "do not create another process or resend an unconfirmed prompt."
                    )
                    rc = 3
                break
            time.sleep(min(0.3, remaining))
    output._json_output({**result, **receipt})
    return rc
