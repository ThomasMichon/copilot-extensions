"""Cold Manager acceptance under the existing 300-second native group boundary.

Prepare real installed candidates before pytest's unchanged 30-second item
checks, then execute those checks through the canonical plugin runner.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from plugin_test_containment import Limits, isolated_environment, no_window_kwargs, run_contained
from _admission_protocol import acquire

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--contract", choices=("runtime", "callbacks", "updater", "source", "engine-source"),
        default="runtime",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if root == REPO or not root.is_relative_to(REPO):
        raise ValueError("Acceptance storage must be an explicit directory below this checkout")
    root.mkdir(parents=True, exist_ok=True)
    prepared = subprocess.run(
        [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager", "--prepare-only"],
        cwd=REPO, check=False, **no_window_kwargs(),
    )
    if prepared.returncode:
        return prepared.returncode
    python = REPO / ".test-venvs" / sys.platform / "worktree-manager" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    filename = {
        "runtime": "test_manager_runtime_install.py",
        "callbacks": "test_manager_mux_callbacks.py",
        "updater": "test_manager_updater_recovery.py",
        "source": "test_manager_source_install.py",
        "engine-source": "test_manager_engine_source.py",
    }[args.contract]
    native = REPO / "worktree-manager" / "tests" / "native" / filename
    with ExitStack() as ownership:
        lease = acquire(0, python.parent.parent)
        ownership.callback(lease.release)
        # Source + repair identities are longer than release-only slots. Keep
        # their default-home fixture below OS temp, not a deep source checkout.
        storage = {} if args.contract in {"source", "engine-source"} else {"dir": root}
        raw = ownership.enter_context(tempfile.TemporaryDirectory(prefix="manager-acceptance-", **storage))
        sandbox = Path(raw)
        env = isolated_environment(os.environ, sandbox)
        env["MANAGER_RUNTIME_ACCEPTANCE_DATA"] = str(sandbox / "fixtures.json")
        prepare_command = [str(python), "-I", "-B", str(native), "--prepare", str(sandbox)]
        assertions = [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager",
                      "--test-file", "tests/native/" + filename]
        groups = (
            [("archived-fixture preparation", [prepare_command]),
             ("normal-update consumer", [
                 [str(python), "-I", "-B", str(native), "--exercise", str(sandbox)],
             ]),
             ("recovery admission and concurrent caller", [
                 [str(python), "-I", "-B", str(native), "--safety", str(sandbox)], assertions,
             ])]
            if args.contract == "updater" else [("construction and assertions", [prepare_command, assertions])]
        )
        for label, commands in groups:
            started = time.monotonic()
            for command in commands:
                remaining = 300 - (time.monotonic() - started)
                if remaining <= 0:
                    return 124
                print(f"Manager native acceptance ({label}): running", flush=True)
                result = run_contained(
                    command, cwd=REPO, env=env, sandbox=sandbox,
                    limits=Limits(wall_seconds=remaining),
                )
                if result:
                    return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
