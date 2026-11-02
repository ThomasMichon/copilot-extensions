"""Captured lifecycle subprocess transport; runtime selection stays caller-owned."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def admitted(kind: str, source_aware: bool, capabilities: object, *, preflight: bool) -> bool:
    """Require source support before sending a mutating lifecycle packet."""
    required = (
        {"session-start-source-v1"} if kind == "sessionStart" and source_aware
        else {"session-end-source-v1"} if kind == "sessionEnd"
        else set()
    )
    if kind == "sessionStart" and not preflight:
        required.add("session-lifecycle-v1")
    return not required or (
        isinstance(capabilities, list) and all(item in capabilities for item in required)
    )


def unavailable(kind: str) -> dict | None:
    """Keep Windows warm-hook refusal diagnostic without spawning a fallback."""
    if os.name != "nt":
        return None
    message = (
        "Resident source-aware sessionStart is unavailable."
        if kind == "sessionStart" else "Resident native sessionEnd is unqualified here."
    )
    return {"_stderr": f"[agent-worktrees] {message}\n"}


def run_start(
    python: Path, payload: dict, *, source_aware: bool, timeout: float,
    decision_timeout: float,
) -> dict | None:
    """Run current Start; ``None`` requests only the legacy packet fallback."""
    try:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = ""
        result = subprocess.run(
            [
                str(python),
                "-m",
                "agent_worktrees",
                "session-lifecycle",
                "--stdin",
                *(["--source-bound"] if source_aware else []),
                "--timeout-seconds",
                str(decision_timeout),
            ],
            input=json.dumps(payload, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if source_aware:
            return {"_stderr": f"[agent-worktrees] Source-aware sessionStart failed: {exc}\n"}
        return {}
    if result.returncode != 0:
        if source_aware:
            return {"_stderr": (
                f"[agent-worktrees] Source-aware sessionStart failed ({result.returncode}); "
                "older lifecycle fallback refused.\n" + str(result.stderr or "")[-4096:]
            )}
        return None
    if result.stderr:
        sys.stderr.write(result.stderr)
    try:
        value = json.loads(result.stdout or "{}")
    except (TypeError, ValueError):
        if source_aware:
            return {"_stderr": "[agent-worktrees] Source-aware sessionStart returned invalid output.\n"}
        return None
    return value if isinstance(value, dict) else {}


def run_end(python: Path, payload: dict, *, timeout: float) -> dict:
    """Run source-bound End without permitting an older parser downgrade."""
    try:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = ""
        result = subprocess.run(
            [
                str(python),
                "-m",
                "agent_worktrees",
                "deregister-session",
                "--stdin",
                "--source-bound",
            ],
            input=json.dumps(payload, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"_stderr": f"[agent-worktrees] SessionEnd failed; closure is unconfirmed: {exc}\n"}
    if result.returncode:
        return {"_stderr": (
            f"[agent-worktrees] SessionEnd failed ({result.returncode}); closure is unconfirmed.\n"
            + str(result.stderr or "")[-4096:]
        )}
    if result.stderr:
        return {"_stderr": str(result.stderr)[-4096:]}
    return {}


def bootstrap_result(completed: subprocess.CompletedProcess) -> dict:
    """Preserve legacy bootstrap JSON/text output without lifecycle side effects."""
    result: dict = {}
    stdout = (completed.stdout or "").strip()
    if stdout:
        try:
            value = json.loads(stdout)
        except (TypeError, ValueError):
            result["additionalContext"] = stdout
        else:
            if isinstance(value, dict):
                result.update(value)
            else:
                result["additionalContext"] = stdout
    if completed.stderr:
        result["_stderr"] = completed.stderr
    return result
