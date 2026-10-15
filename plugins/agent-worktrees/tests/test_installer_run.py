"""``installer_run.run_installer``: deadline-owned installer trees, receipt-judged outcomes.

``subprocess.run(timeout=...)`` killed only the installer's own process, so a
grandchild (a staged installer copy, ``uv``, a build) kept running and could
publish a slot after the updater had labelled the run from its timer; and an
installer that exited 0 without activating the target counted as success.
These tests run real child processes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from agent_worktrees import installer_run, reconcile, update_events

pytestmark = pytest.mark.real_installer_processes

# A stub installer: optionally spawns a grandchild that writes a slot marker
# after a delay, records it, then sleeps or exits with a code.
_STUB = textwrap.dedent("""
    import subprocess, sys, time
    marker, delay, sleep, code = sys.argv[1], *map(float, sys.argv[2:4]), int(sys.argv[4])
    if delay >= 0:
        grandchild = subprocess.Popen([sys.executable, "-c",
            "import sys, time; time.sleep(float(sys.argv[2])); "
            "open(sys.argv[1], 'w').write('slot')",
            marker, str(delay)])
        open(marker + ".pid", "w").write(str(grandchild.pid))
    print("stub installer running", flush=True)
    time.sleep(sleep)
    sys.exit(code)
""")


@pytest.fixture()
def stub(tmp_path, monkeypatch):
    # Real process-tree semantics: outside contained test mode the POSIX
    # installer gets its own session, which the deadline kill targets.
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    script = tmp_path / "installer.py"
    script.write_text(_STUB, encoding="utf-8")

    def argv(*, delay: float = -1, sleep: float = 0, code: int = 0) -> list[str]:
        return [sys.executable, str(script), str(tmp_path / "slot"), str(delay),
                str(sleep), str(code)]

    return argv


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, check=False).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_dead(pid: int, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    return not _alive(pid)


def test_deadline_kills_grandchild(tmp_path, stub):
    run = installer_run.run_installer(
        stub(delay=6, sleep=60), cwd=tmp_path, env=os.environ, deadline_s=2,
    )
    assert run.status == "timed out (deadline hit during build)"
    assert run.rc is None
    assert run.duration_s < 30
    grandchild = int((tmp_path / "slot.pid").read_text())
    assert _wait_dead(grandchild), "the installer's grandchild must die with the tree"
    time.sleep(6)
    assert not (tmp_path / "slot").exists(), "no slot may appear after the deadline"


def test_exit_code_alone_decides_without_a_target(tmp_path, stub):
    assert installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60).status == "OK"
    assert installer_run.run_installer(
        stub(code=3), cwd=tmp_path, env=os.environ, deadline_s=60).status == "installer exited 3"


def test_rc0_without_receipt_fails(tmp_path, stub):
    run = installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="1.2.0", receipt=lambda: "1.1.0",
    )
    assert run.status.startswith("runtime not at target")
    assert "deployed 1.1.0, expected 1.2.0" in run.status
    assert run.receipt_version == "1.1.0" and not run.receipt_complete
    assert update_events.classify_status(run.status)[0] != "verified_current"

    missing = installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="1.2.0", receipt=lambda: None,
    )
    assert "deployed nothing" in missing.status


def test_rc0_with_the_target_receipt_is_ok(tmp_path, stub):
    run = installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="1.2.0", receipt=lambda: "1.2.0",
    )
    assert run.status == "OK" and run.receipt_complete


@pytest.mark.parametrize("published,expected", [
    (True, "timed out (slot published; deadline hit during cutover)"),
    (False, "timed out (deadline hit during build)"),
])
def test_receipt_overrides_watchdog_rc(tmp_path, stub, published, expected):
    run = installer_run.run_installer(
        stub(code=124), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="2.0.0", receipt=lambda: "2.0.0" if published else "1.0.0",
    )
    assert run.status == expected
    assert update_events.classify_status(run.status)[0] == "timeout"


@pytest.mark.parametrize("code,outcome", [(75, "blocked_by_lock"), (76, "command_failed")])
def test_cutover_exits_keep_their_outcomes(tmp_path, stub, code, outcome):
    run = installer_run.run_installer(
        stub(code=code), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="2.0.0", receipt=lambda: "2.0.0",
    )
    assert run.status == f"installer exited {code}"
    assert update_events.classify_status(run.status) == (outcome, f"installer_exit_{code}")


def test_spawn_failure_becomes_the_status(tmp_path):
    run = installer_run.run_installer(
        [str(tmp_path / "missing-installer")], cwd=tmp_path, env=os.environ, deadline_s=5,
    )
    assert run.rc is None and run.status and not run.status.startswith("OK")


def test_captured_output_reaches_the_log_after_a_deadline_kill(tmp_path, stub):
    lines: list[str] = []
    run = installer_run.run_installer(
        stub(sleep=60), cwd=tmp_path, env=os.environ, deadline_s=2, log=lines.append,
    )
    assert run.status.startswith("timed out")
    assert "stub installer running" in lines


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_posix_group_killed(tmp_path, stub):
    run = installer_run.run_installer(
        stub(delay=30, sleep=60), cwd=tmp_path, env=os.environ, deadline_s=2,
    )
    assert run.status.startswith("timed out")
    assert _wait_dead(int((tmp_path / "slot.pid").read_text()))


def test_plan_step_is_bounded_and_reports_the_deadline(tmp_path, stub, monkeypatch):
    from agent_worktrees import update_runtime

    monkeypatch.setattr(update_runtime, "_installer_timeout", lambda *_a, **_k: 2)
    logged: list[str] = []
    started = time.monotonic()
    rc = reconcile._run_plan_step(stub(sleep=60), {"service": "agent-x"}, logged.append)
    assert rc == 124
    assert time.monotonic() - started < 40
    assert any("timed out" in line for line in logged)
    assert "    stub installer running" in logged


def test_plan_step_applies_the_step_environment(tmp_path, monkeypatch):
    from agent_worktrees import update_runtime

    monkeypatch.setattr(update_runtime, "_installer_timeout", lambda *_a, **_k: 60)
    monkeypatch.setenv("AW_PLAN_STEP_DROP", "present")
    logged: list[str] = []
    argv = [sys.executable, "-c",
            "import os; print(os.environ.get('AW_PLAN_STEP_SET'), "
            "os.environ.get('AW_PLAN_STEP_DROP'))"]
    rc = reconcile._run_plan_step(
        argv,
        {"service": "agent-x", "environment": {"AW_PLAN_STEP_SET": "value"},
         "unset_environment": ["AW_PLAN_STEP_DROP"]},
        logged.append,
    )
    assert rc == 0
    assert "    value None" in logged
