"""Cold Manager acceptance under independently contained native group boundaries.

Prepare real installed candidates before pytest's unchanged 30-second item
checks, then execute those checks through the canonical plugin runner.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from plugin_test_containment import Limits, TestSandbox, isolated_environment, no_window_kwargs, run_contained
from _admission_protocol import acquire
from plugin_test_resources import record_owner, run_prefix, runs_root, venv_root

REPO = Path(__file__).resolve().parents[1]


def _engine_routing(root: Path, venv_root: Path, *, prepare_only: bool = False) -> int:
    """Keep installed-artifact preparation outside unchanged routing timers."""
    namespace = runpy.run_path(str(REPO / "tools" / "run-plugin-tests.py"))
    runner = namespace["main"]
    runner.__globals__["VENV_ROOT"] = venv_root
    install = runner.__globals__["run_uv_install"]

    def diagnostics(*args, **kwargs):
        try:
            return install(*args, **kwargs)
        except subprocess.CalledProcessError as error:
            print("Preparation stdout:", error.stdout or "", flush=True)
            print("Preparation stderr:", error.stderr or "", flush=True)
            raise

    runner.__globals__["run_uv_install"] = diagnostics
    result = runner(["agent-worktrees", "--prepare-only"])
    if result:
        return result
    python = venv_root / "agent-worktrees" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    fixture = REPO / "plugins" / "agent-worktrees" / "tests" / "fixtures" / "update_driver_context.py"
    with tempfile.TemporaryDirectory(prefix="routing-acceptance-", dir=root) as raw:
        sandbox = Path(raw)
        env = isolated_environment(os.environ, sandbox)
        env["UPDATE_DRIVER_TEST_PHASE_LOG"] = str(root / "routing-preparation-phases.jsonl")
        env["UPDATE_DRIVER_TEST_PREPARATION_LOG"] = str(root / "routing-preparation")
        if prepare_only:
            env["UPDATE_DRIVER_TEST_INSTALLED_PROBE_DIAGNOSTICS"] = str(root / "installed-child-probe.log")
        lease = acquire(0, python.parent.parent)
        try:
            # The existing native group supervisor owns the preparation bound.
            result = run_contained(
                [str(python), "-I", "-B", str(fixture), "prepare", str(sandbox)],
                cwd=REPO, env=env, sandbox=sandbox, limits=Limits(wall_seconds=300),
            )
        finally:
            lease.release()
        if result:
            for name in ("stdout", "stderr"):
                print((root / f"routing-preparation.{name}.log").read_text(
                    encoding="utf-8", errors="replace",
                ))
            return result
        prepared = sandbox / "prepared-runtime.json"
        (root / "routing-prepared-identities.json").write_text(
            prepared.read_text(encoding="utf-8"), encoding="utf-8",
        )
        if prepare_only:
            print("Healthy native candidates prepared; timed routing was not started.", flush=True)
            return 0
        # The canonical runner acquires its environment lease for timed execution.
        with ExitStack() as restored:
            for key, value in {
                "UPDATE_DRIVER_TEST_RUNTIME_MODE": "prepared",
                "UPDATE_DRIVER_TEST_PREPARED_RUNTIME": str(prepared),
            }.items():
                previous = os.environ.get(key)
                restored.callback(
                    lambda key=key, previous=previous: (
                        os.environ.pop(key, None) if previous is None
                        else os.environ.__setitem__(key, previous)
                    ),
                )
                os.environ[key] = value
            result = runner([
                "agent-worktrees", "--test-file", "tests/test_update_driver.py",
                "-k", "actual_queued_engine_keeps", "--timeout", "600",
                "--plugin-timeout", "900", "--test-timeout", "240",
            ])
        (root / "routing-result.json").write_text(json.dumps({
            "prepared": True, "timed_runner_exit": result,
            "worker_timeout": 20, "watchdog_timeout": 60,
            "cold_first_touch_proved": False,
        }, indent=2), encoding="utf-8")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--contract", choices=("runtime", "callbacks", "updater", "source", "engine-source", "uninstall", "engine-routing"),
        default="runtime",
    )
    parser.add_argument("--venv-root", type=Path, default=REPO / ".test-venvs" / sys.platform)
    parser.add_argument("--prepare-only", action="store_true", help="engine-routing preparation diagnostic only")
    args = parser.parse_args()
    if args.prepare_only and args.contract != "engine-routing":
        parser.error("--prepare-only requires --contract engine-routing")
    root = args.root.resolve()
    if root == REPO or not root.is_relative_to(REPO):
        raise ValueError("Acceptance storage must be an explicit directory below this checkout")
    root.mkdir(parents=True, exist_ok=True)
    if args.contract == "engine-routing":
        return _engine_routing(root, args.venv_root.resolve(), prepare_only=args.prepare_only)
    prepared = subprocess.run(
        [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager", "--prepare-only"],
        cwd=REPO, check=False, **no_window_kwargs(),
    )
    if prepared.returncode:
        return prepared.returncode
    python = venv_root(REPO) / "worktree-manager" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    filename = {
        "runtime": "test_manager_runtime_install.py",
        "callbacks": "test_manager_mux_callbacks.py",
        "updater": "test_manager_updater_recovery.py",
        "source": "test_manager_source_install.py",
        "engine-source": "test_manager_engine_source.py",
        "uninstall": "test_manager_runtime_uninstall.py",
    }[args.contract]
    native = REPO / "worktree-manager" / "tests" / "native" / filename
    with ExitStack() as ownership:
        lease = acquire(0, python.parent.parent)
        ownership.callback(lease.release)
        raw = ownership.enter_context(TestSandbox(
            prefix=run_prefix("manager"), dir=runs_root(REPO),
        ))
        sandbox = Path(raw)
        record_owner(sandbox, REPO, retention="disposable after verified process cleanup")
        env = isolated_environment(os.environ, sandbox)
        env["MANAGER_RUNTIME_ACCEPTANCE_DATA"] = str(sandbox / "fixtures.json")
        prepare_command = [str(python), "-I", "-B", str(native), "--prepare", str(sandbox)]
        assertions = [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager",
                      "--test-file", "tests/native/" + filename]
        groups = (
            [("source constructions and assertions", [
                *[[*prepare_command, "--prepare-step", step]
                  for step in ("release", "qualified", "source", "repair")],
                assertions,
            ])]
            if args.contract == "source" else
            [("archived-fixture preparation", [prepare_command]),
             ("normal-update consumer", [
                 [str(python), "-I", "-B", str(native), "--exercise", str(sandbox)],
             ]),
             ("recovery admission and concurrent caller", [
                 [str(python), "-I", "-B", str(native), "--safety", str(sandbox)], assertions,
             ])]
            if args.contract == "updater" else
            [("installed Manager preparation", [prepare_command]),
             ("removal, reinstall and assertions", [
                 [str(python), "-I", "-B", str(native), "--exercise", str(sandbox)], assertions,
             ])]
            if args.contract == "uninstall" else
            [("construction and assertions", [prepare_command, assertions])]
        )
        group_seconds = 1800 if args.contract == "source" else 300
        for label, commands in groups:
            started = time.monotonic()
            for command in commands:
                remaining = group_seconds - (time.monotonic() - started)
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
