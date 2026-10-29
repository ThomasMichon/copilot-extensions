"""Explicit Windows production-lifetime fixtures under the native test owner."""

from __future__ import annotations

import argparse
from itertools import islice
import os
from pathlib import Path
import sys
import time

from _admission_protocol import AlreadyRunningError, acquire
from plugin_test_cache import ordinary_cache_root
from plugin_test_containment import Limits, TestSandbox, isolated_environment, run_contained
from plugin_test_resources import record_owner, runs_root, venv_root


ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "foundation": "test_foundation_genesis_process.py",
    "first-use": "test_first_use_genesis.py",
    "vault-command": "test_first_use_vault_command.py",
    "vault-bootstrap": "test_first_use_vault_bootstrap.py",
}


def report_fixture_logs(sandbox: Path) -> None:
    for path in islice(sandbox.rglob("*.log"), 16):
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 16000))
                content = stream.read(16000).decode("utf-8", errors="replace")
            print(f"[FIXTURE] {path.relative_to(sandbox)}\n{content}", flush=True)
        except OSError as error:
            print(f"[FIXTURE] log unavailable: {type(error).__name__}", flush=True)


def _run_packet(module: str, python: Path) -> int:
    selected = MODULES[module]
    with TestSandbox(prefix="c-", dir=runs_root(ROOT)) as temporary:
        sandbox = Path(temporary)
        record_owner(sandbox, ROOT, retention="native lifetime evidence until verified process cleanup")
        environment = isolated_environment(os.environ, sandbox, allow_explicit_tiers=True)
        environment["PYTHONPATH"] = str(ROOT / "tools")
        environment["COPILOT_EXTENSIONS_TEST_VENV_ROOT"] = str(venv_root(ROOT))
        environment["UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"] = "1"
        environment["UPDATE_DRIVER_NATIVE_LIFETIME"] = "1"
        environment.pop("UPDATE_DRIVER_NATIVE_SERVICE_LIFETIME", None)
        if module in {"vault-command", "vault-bootstrap"}:
            environment["UPDATE_DRIVER_NATIVE_SERVICE_LIFETIME"] = "1"
        tests = ROOT / "libs" / "update-driver" / "tests"
        try:
            result = run_contained(
                [
                    str(python), "-m", "pytest", "-q",
                    "--basetemp", str(sandbox / "t"),
                    "-o", f"cache_dir={sandbox / 'pytest-cache'}", "--timeout=1200",
                    str(tests / selected), str(tests / "test_native_lifetime.py"),
                ],
                cwd=tests.parent, env=environment, sandbox=sandbox,
                limits=Limits(
                    wall_seconds=180 if os.environ.get("UPDATE_DRIVER_NATIVE_STACKS") == "1" else 900,
                ), native_application=True,
            )
        except BaseException:
            try:
                report_fixture_logs(sandbox)
            except OSError as error:
                print(f"[FIXTURE] log inventory unavailable: {type(error).__name__}", flush=True)
            raise
        if result != 0:
            report_fixture_logs(sandbox)
    return result


def main() -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", choices=MODULES)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("production caller-Job qualification requires native Windows")
    environment_root = ordinary_cache_root(venv_root(ROOT) / "update-driver")
    if not environment_root.is_dir():
        parser.error("prepare the update-driver test environment through run-plugin-tests first")
    try:
        lease = acquire(0, environment_root)
    except AlreadyRunningError:
        print("[BUSY] Prepared update-driver environment is in use; qualification not started.", file=sys.stderr)
        return 3
    try:
        python = environment_root / "Scripts" / "python.exe"
        if not python.is_file():
            parser.error("prepare the update-driver test environment through run-plugin-tests first")
        result = _run_packet(args.module, python)
    finally:
        lease.release()
    elapsed = time.monotonic() - started
    if elapsed >= 900:
        print(
            f"[LIMIT] native packet exceeded 900s including daemon settlement and sandbox cleanup: {elapsed:.2f}s",
            flush=True,
        )
        return 124
    if os.environ.get("UPDATE_DRIVER_NATIVE_STACKS") == "1":
        print("[DIAGNOSTIC] copied stack instrumentation; not native acceptance", flush=True)
        return result or 125
    return result


if __name__ == "__main__":
    raise SystemExit(main())
