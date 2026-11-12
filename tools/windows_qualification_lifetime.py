"""The explicitly selected hosted Worktrees caller-Job lifetime owner."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import os
from pathlib import Path
import sys
import time


@contextmanager
def capture_native_streams(stdout, stderr):
    """Keep native descendants' real handles in the same sanitizable evidence."""
    original = sys.__stdout__, sys.__stderr__
    try:
        sys.__stdout__, sys.__stderr__ = stdout, stderr
        with redirect_stdout(stdout), redirect_stderr(stderr):
            yield
    finally:
        sys.__stdout__, sys.__stderr__ = original


def worktrees_lifetime(
    root: Path, output: Path, *, prepare, packet: str = "native-worktrees-caller-jobs",
) -> int:
    """Prepare normally, then use the existing leased, permissive native owner."""
    from _admission_protocol import acquire
    from agent_procutil.daemon_instances import DaemonLeasePolicy
    from plugin_test_cache import ordinary_cache_root
    from plugin_test_containment import Limits, TestSandbox, isolated_environment, run_contained
    from plugin_test_resources import venv_root
    from windows_qualification_contract import NATIVE_LIFETIME_PACKETS, NATIVE_PACKETS

    if packet not in NATIVE_LIFETIME_PACKETS:
        raise ValueError("Worktrees lifetime requires a selected caller-Job packet")
    case = NATIVE_PACKETS[packet][2]
    started = time.monotonic()
    prepare([
        sys.executable, str(root / "tools/run-plugin-tests.py"), "agent-worktrees", "--prepare-only",
    ], 300)
    environment_root = ordinary_cache_root(venv_root(root) / "agent-worktrees")
    python = environment_root / "Scripts" / "python.exe"
    if not python.is_file():
        raise ValueError("Worktrees lifetime requires the prepared canonical test environment")
    temporary = os.environ.get("RUNNER_TEMP")
    if not temporary:
        raise ValueError("Worktrees lifetime requires its disposable hosted test owner")
    (output / "junit").mkdir(parents=True, exist_ok=True)
    lease = acquire(0, environment_root)
    try:
        with TestSandbox(prefix="wl-", dir=Path(temporary)) as directory:
            sandbox = Path(directory)
            environment = isolated_environment(os.environ, sandbox, allow_explicit_tiers=True)
            environment.update({
                "PYTHONPATH": str(root / "tools"),
                "COPILOT_EXTENSIONS_TEST_VENV_ROOT": str(venv_root(root)),
                "WORKTREES_NATIVE_CALLER_JOBS": "1",
            })
            with (
                (output / "phase-native-lifetime.stdout.log").open("w", encoding="utf-8") as stdout,
                (output / "phase-native-lifetime.stderr.log").open("w", encoding="utf-8") as stderr,
                capture_native_streams(stdout, stderr),
            ):
                result = run_contained(
                    [
                        str(python), "-m", "pytest", "-q", "-s",
                        str(root / "plugins/agent-worktrees/tests/native/test_native_seed_application.py"),
                        "-k", case,
                        "--basetemp", str(sandbox / "t"),
                        "-o", f"cache_dir={sandbox / 'pytest-cache'}", "--timeout=900",
                        "--junitxml=" + str(output / "junit/worktrees-lifetime.xml"),
                    ],
                    cwd=root / "plugins/agent-worktrees", env=environment, sandbox=sandbox,
                    limits=Limits(wall_seconds=900), native_application=True,
                    daemon_lease=DaemonLeasePolicy(ttl_seconds=15, max_lifetime_seconds=900),
                )
                if result:
                    from qualify_foundation_lifetime import report_fixture_logs

                    report_fixture_logs(sandbox)
    finally:
        lease.release()
    if result:
        raise ValueError(f"Worktrees native lifetime fixture failed ({result})")
    if time.monotonic() - started >= 1200:
        raise ValueError("Worktrees lifetime exceeded 1200 seconds including preparation and cleanup")
    return result
