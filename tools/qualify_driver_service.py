"""Explicit leased Windows qualification of the installed Driver service lifetime."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
import os
from pathlib import Path
import sys
import time

from _admission_protocol import acquire
from single_instance_lease import AlreadyRunningError
from plugin_test_cache import ordinary_cache_root
from plugin_test_containment import Limits, TestSandbox, isolated_environment, run_contained
from plugin_test_resources import record_owner, venv_root


ROOT = Path(__file__).resolve().parents[1]


def pytest_command(root: Path, python: Path, sandbox: Path) -> list[str]:
    return [
        str(python), "-m", "pytest", "-q", "--rootdir", str(root),
        "--basetemp", str(sandbox / "t"),
        "-o", f"cache_dir={sandbox / 'pytest-cache'}", "--timeout=300",
        str(root / "plugins/agent-remote-driver/tests/native/test_session_host_application.py"),
        str(root / "libs/agent-procutil/tests/test_durable_wmi_job_survival.py"),
        "-k", "installed_service_survives_all_installer_caller_jobs or "
        "tracked_cleanup_scope_refuses_a_child_outside_the_immediate_caller_job",
    ]


def main() -> int:
    if len(sys.argv) != 1:
        raise ValueError("Driver service qualification accepts no arguments")
    return qualify(ROOT)


def qualify(root: Path, *, output: Path | None = None, deadline: float | None = None) -> int:
    """Qualify the installed service under the unchanged hosted native owner."""
    if os.name != "nt":
        raise ValueError("Driver service caller-Job qualification requires Windows")
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
        raise ValueError("Select an existing native Copilot executable before qualification")
    temporary = os.environ.get("RUNNER_TEMP")
    if not temporary or not Path(temporary).is_absolute():
        raise ValueError("Driver service qualification requires its disposable hosted test owner")
    deadline = time.monotonic() + 420 if deadline is None else deadline
    if deadline <= time.monotonic():
        raise ValueError("Driver service preparation exhausted its aggregate deadline")
    environment_root = ordinary_cache_root(venv_root(root) / "agent-remote-driver")
    python = environment_root / "Scripts" / "python.exe"
    if not python.is_file():
        raise ValueError("Prepare the Driver test environment through run-plugin-tests first")
    try:
        lease = acquire(0, environment_root)
    except AlreadyRunningError:
        print("[BUSY] Prepared Driver environment is in use; qualification not started.")
        return 3
    try:
        sandbox_factory: Callable[..., AbstractContextManager[str]] = TestSandbox
        with sandbox_factory(prefix="c-", dir=Path(temporary)) as temporary:
            sandbox = Path(temporary)
            record_owner(
                sandbox, root,
                retention="installed Driver service lifetime evidence until exact process cleanup",
            )
            environment = isolated_environment(os.environ, sandbox, allow_explicit_tiers=True)
            environment["PYTHONPATH"] = str(root / "tools")
            environment["COPILOT_EXTENSIONS_TEST_VENV_ROOT"] = str(venv_root(root))
            environment["COPILOT_NATIVE_TEST_EXECUTABLE"] = executable
            environment["COPILOT_EXTENSIONS_NATIVE_DRIVER_SERVICE"] = "1"
            command = pytest_command(root, python, sandbox)
            with ExitStack() as capture:
                if output is not None:
                    from windows_qualification_lifetime import capture_native_streams

                    (output / "junit").mkdir(parents=True, exist_ok=True)
                    command.append("--junitxml=" + str(output / "junit/driver-service.xml"))
                    stdout = capture.enter_context(
                        (output / "phase-driver-service.stdout.log").open("w", encoding="utf-8"),
                    )
                    stderr = capture.enter_context(
                        (output / "phase-driver-service.stderr.log").open("w", encoding="utf-8"),
                    )
                    capture.enter_context(capture_native_streams(stdout, stderr))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Driver service preparation exhausted its aggregate deadline")
                result = run_contained(
                    command, cwd=root, env=environment, sandbox=sandbox,
                    limits=Limits(wall_seconds=remaining), native_application=True,
                )
    finally:
        lease.release()
    if result:
        raise ValueError(f"Driver service native lifetime fixture failed ({result})")
    if time.monotonic() >= deadline:
        raise ValueError("Driver service exceeded 420 seconds including preparation and cleanup")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
