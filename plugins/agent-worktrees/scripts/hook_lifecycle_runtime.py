"""Captured lifecycle subprocess transport; runtime selection stays caller-owned."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
