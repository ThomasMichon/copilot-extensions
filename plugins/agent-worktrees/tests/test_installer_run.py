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
    # An exited process its parent has not reaped yet still answers kill(0).
    return not _state(pid).startswith("Z")


def _state(pid: int) -> str:
    return subprocess.run(["ps", "-o", "state=", "-p", str(pid)], capture_output=True,
                          text=True, check=False).stdout.strip() or "Z"


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


def test_watchdog_exit_kills_what_the_installer_left(tmp_path, stub):
    # The installers' own watchdog exits 124 after signalling only its staged
    # group; whatever survived must not publish after the reported timeout.
    run = installer_run.run_installer(
        stub(delay=4, code=124), cwd=tmp_path, env=os.environ, deadline_s=60,
    )
    assert run.status == "timed out (deadline hit during build)"
    grandchild = int((tmp_path / "slot.pid").read_text())
    assert _wait_dead(grandchild), "the installer's leftover must die with it"
    time.sleep(5)
    assert not (tmp_path / "slot").exists(), "no slot may appear after the timeout"


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


# A stub installer whose staged child leaves its group the way the installers'
# self-stage watchdog does (``set -m``), and whose daemon leaves the session.
_REGROUPING_STUB = textwrap.dedent("""
    import os, subprocess, sys, time
    out = sys.argv[1]
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    staged = subprocess.Popen(sleeper, preexec_fn=lambda: os.setpgid(0, 0))
    daemon = subprocess.Popen(sleeper, start_new_session=True)
    open(out, "w").write(f"{staged.pid} {daemon.pid}")
    time.sleep(120)
""")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_posix_deadline_reaches_a_regrouped_child_but_not_a_daemon(tmp_path, stub):
    script = tmp_path / "regroup.py"
    script.write_text(_REGROUPING_STUB, encoding="utf-8")
    pids = tmp_path / "pids"
    run = installer_run.run_installer(
        [sys.executable, str(script), str(pids)], cwd=tmp_path, env=os.environ,
        deadline_s=3,
    )
    staged, daemon = map(int, pids.read_text().split())
    try:
        assert run.status.startswith("timed out")
        assert _wait_dead(staged), "a child in its own group must die with the session"
        assert _alive(daemon), "a daemon that left the session must survive"
    finally:
        for pid in (staged, daemon):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def _stopped(pid: int) -> bool:
    return _state(pid).startswith("T")


@pytest.mark.skipif(os.name == "nt", reason="POSIX zombies")
def test_an_unreaped_child_counts_as_dead():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        assert _wait_for(lambda: _state(child.pid).startswith("Z")), "the child exits unreaped"
        assert not _alive(child.pid)
    finally:
        child.wait()


@pytest.mark.skipif(os.name == "nt", reason="POSIX sessions")
@pytest.mark.parametrize("stale", ["detached", "replaced"])
def test_session_sweep_revalidates_each_census_entry(monkeypatch, stale):
    """Between the census and the signal, an entry can leave the session
    (``setsid``) or its PID can be reused; either way it must be left running."""
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    leader = subprocess.Popen(sleeper, start_new_session=True)
    other = subprocess.Popen(sleeper, start_new_session=True)
    try:
        assert _wait_for(lambda: installer_run._start_time(other.pid) is not None)
        # "detached": the census saw this very process inside the session.
        # "replaced": the census saw an earlier process that had this PID.
        started = installer_run._start_time(other.pid) if stale == "detached" else "earlier"
        censuses = iter([{other.pid: started}])
        monkeypatch.setattr(installer_run, "_session_census", lambda _sid: next(censuses, {}))
        installer_run._kill_session(leader)
        # Owned child: reap it, or os.kill(pid, 0) still sees the zombie.
        assert leader.wait(timeout=10) is not None, "the installer itself still dies"
        assert other.poll() is None and _alive(other.pid), "a stale entry is never killed"
        assert not _stopped(other.pid), "a stale entry is never left stopped"
    finally:
        for proc in (leader, other):
            proc.kill()
            proc.wait()


def _wait_for(predicate, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.1)
    return predicate()

@pytest.mark.skipif(os.name != "nt", reason="Windows Job containment")
def test_windows_spawn_requires_the_job(monkeypatch, tmp_path):
    seen = {}

    def fake_spawn(argv, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("Windows descendant Job assignment failed before child resume")

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", fake_spawn)
    run = installer_run.run_installer(["installer"], cwd=tmp_path, env={}, deadline_s=5)
    assert seen["require_job"] is True
    assert "allow_breakaway" not in seen, "installer daemons may still break away"
    assert run.rc is None and "Job assignment failed" in run.status


def test_a_containment_failure_becomes_the_status(monkeypatch, tmp_path):
    def refuse(*_a, **_k):
        raise RuntimeError("failed to resume suspended process 1")

    monkeypatch.setattr(installer_run, "_spawn", refuse)
    run = installer_run.run_installer(["installer"], cwd=tmp_path, env={}, deadline_s=5)
    assert run.rc is None and run.status == "failed to resume suspended process 1"


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


def test_plan_step_uses_the_service_manifest_deadline(tmp_path, monkeypatch):
    for var in ("AGENT_X_INSTALL_DEADLINE_SEC", "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"):
        monkeypatch.delenv(var, raising=False)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text('{"installerDeadlineSeconds": 7}', encoding="utf-8")
    monkeypatch.setattr(reconcile, "core_installed_payload_dir",
                        lambda name: payload if name == "agent-x" else None)
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return installer_run.InstallerRun("OK", 0, None, False, 0.0)

    monkeypatch.setattr(installer_run, "run_installer", fake_run)
    assert reconcile._run_plan_step(["installer"], {"service": "agent-x"}, lambda _l: None) == 0
    assert seen["deadline_s"] == 7 + 30

    # An unreadable manifest fails the step instead of escaping the sweep.
    (payload / "plugin.json").unlink()
    (payload / "plugin.json").mkdir()
    logged: list[str] = []
    assert reconcile._run_plan_step(["installer"], {"service": "agent-x"}, logged.append) == 1
    assert logged and "plugin.json" in logged[0]
