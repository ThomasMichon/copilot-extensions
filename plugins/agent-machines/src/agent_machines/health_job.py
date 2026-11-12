"""Installed finite task root; no payload lookup, provisioning or scheduler effects."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ACTION_SECONDS = 55


def main(argv=None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--expect-policy", required=True)
    args = parser.parse_args(argv)
    if (
        sys.platform != "win32"
        or not args.store.is_absolute() or ".." in args.store.parts
        or re.fullmatch(r"[a-f0-9]{64}", args.expect_policy) is None
    ):
        return 2
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

    from ._peer_launch import retained_runtime_identity, retained_runtime_prefix

    environment = Path(sys.prefix)
    slot = environment.parent if environment.name == ".venv" else environment
    root = slot.parent.parent
    try:
        from .health_enrollment import require_collection_support

        require_collection_support(Path(sys.executable))
        identity = retained_runtime_identity(root, Path(sys.executable), "agent_machines")
        prefix = retained_runtime_prefix(
            root, Path(sys.executable), "agent_machines", runtime_identity=identity,
        )
        from . import _peer_launch

        environment = _peer_launch.legacy_peer_environment(dict(os.environ))
        environment["AGENT_RT_ROOT"] = str(root)
        if (root / "install.json").exists():
            _peer_launch.validate_owner("agent-machines", root, str(root / "install.json"))
            environment[_peer_launch.CONTEXT_ENV] = str(root / "install.json")
        completed = run_caller_owned(
            [*prefix, "health", "run", "--store-dir", str(args.store),
             "--apply", "--expect-policy", args.expect_policy],
            timeout=ACTION_SECONDS,
            budget=OwnedExecutionBudget(deadline=started + ACTION_SECONDS, cleanup_reserve=5),
            max_stdout_bytes=256 * 1024, max_stderr_bytes=64 * 1024, cwd=slot, env=environment,
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
