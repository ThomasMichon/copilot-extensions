"""Finite GUI-subsystem scheduler root; the public payload still selects collection."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import site
import subprocess
import sys

ACTION_SECONDS = 55


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--powershell", required=True, type=Path)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--expect-policy", required=True)
    args = parser.parse_args(argv)
    if sys.platform != "win32" or re.fullmatch(r"[a-f0-9]{64}", args.expect_policy) is None:
        return 2
    for path in (args.site, args.powershell, args.payload, args.store):
        if not path.is_absolute() or ".." in path.parts:
            return 2
    if not args.site.is_dir():
        return 2
    # This path comes from the preparing installation, not synchronized input.
    library = args.site.resolve()
    sys.path.insert(0, str(library))
    site.addsitedir(str(library))
    support = importlib.util.find_spec("agent_procutil")
    if support is None or not support.origin or not Path(support.origin).resolve().is_relative_to(library):
        return 2
    try:
        from agent_procutil.owned_budget import OwnedExecutionBudget
        from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
    except ImportError:
        return 2

    budget = OwnedExecutionBudget.from_timeout(ACTION_SECONDS, cleanup_reserve=5)
    try:
        completed = run_caller_owned(
            [
                str(args.powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(args.payload / "bin" / "agent-machines.ps1"),
                "health", "run", "--store-dir", str(args.store), "--apply",
                "--expect-policy", args.expect_policy,
            ],
            timeout=ACTION_SECONDS, budget=budget,
            max_stdout_bytes=256 * 1024, max_stderr_bytes=64 * 1024,
            cwd=args.payload,
        )
        return completed.returncode if completed.returncode in (0, 2) else 2
    except subprocess.TimeoutExpired:
        return 124
    except OwnedCleanupError:
        return 125
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
