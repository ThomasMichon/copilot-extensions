"""Reuse the existing caller-owned process boundary for explicit lab tooling."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(
    argv: list[str], *, timeout: float, env: dict[str, str] | None = None,
    max_stdout_bytes: int = 256 * 1024,
) -> subprocess.CompletedProcess[str]:
    source = Path(__file__).resolve().parents[2] / "libs" / "agent-procutil" / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from agent_procutil.owned_process import run_caller_owned

    return run_caller_owned(
        argv, timeout=timeout, max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=16 * 1024, input_text=None, capture=True,
        **({"env": env} if env is not None else {}),
    )
