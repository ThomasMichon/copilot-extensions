from __future__ import annotations

import types
from pathlib import Path
import pytest

from agent_worktrees import daemon_health


def test_candidates_are_scoped_to_the_active_runtime_home(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(daemon_health.sys, "platform", "linux")
    runtime_home = tmp_path / "cell-a"
    runtime_home.mkdir()
    monkeypatch.setattr(
        daemon_health.status_monitor_runtime,
        "_aw_runtime_home",
        lambda: runtime_home,
    )
    monkeypatch.setattr(
        daemon_health.status_monitor_cutover,
        "_iter_status_monitor_pids",
        lambda: {101, 202},
    )
    monkeypatch.setattr(
        daemon_health.procs,
        "processes_with_cwd_under",
        lambda root: (
            [{"pid": 101, "name": "python"}] if root == str(runtime_home) else []
        ),
    )
    monkeypatch.setattr(
        daemon_health.locks,
        "process_start_time",
        lambda pid: f"start-{pid}",
    )

    candidates = daemon_health._candidates()

    assert [candidate.pid for candidate in candidates] == [101]
    assert candidates[0].start_time == "start-101"


def test_candidates_use_darwin_cwd_probe_when_procfs_is_unavailable(
    tmp_path: Path, monkeypatch
):
    runtime_home = tmp_path / "cell-a"
    runtime_home.mkdir()
    monkeypatch.setattr(daemon_health.sys, "platform", "darwin")
    monkeypatch.setattr(
        daemon_health.status_monitor_runtime,
        "_aw_runtime_home",
        lambda: runtime_home,
    )
    monkeypatch.setattr(
        daemon_health.status_monitor_cutover,
        "_iter_status_monitor_pids",
        lambda: {101, 202},
    )
    monkeypatch.setattr(
        daemon_health,
        "_darwin_pid_under_root",
        lambda pid, runtime_root: pid == 202 and runtime_root == str(runtime_home),
    )
    monkeypatch.setattr(
        daemon_health.locks,
        "process_start_time",
        lambda pid: f"start-{pid}",
    )

    candidates = daemon_health._candidates()

    assert [candidate.pid for candidate in candidates] == [202]


def test_windows_candidates_use_one_fresh_native_snapshot(tmp_path, monkeypatch):
    from agent_worktrees import windows_process_census

    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    calls = []

    def snapshot():
        calls.append(True)
        return [
            {"pid": 101, "cwd": str(tmp_path), "start_time": "owner",
             "command_line": '"python.exe" -m agent_worktrees status-monitor --passive'},
            {"pid": 202, "cwd": str(tmp_path.parent / "another-cell"), "start_time": "other",
             "command_line": "python -m agent_worktrees status-monitor"},
            {"pid": 303, "cwd": str(tmp_path), "start_time": "cli",
             "command_line": "python -m agent_worktrees status"},
        ]

    monkeypatch.setattr(windows_process_census, "snapshot", snapshot)
    assert [c.pid for c in daemon_health._candidates()] == [101]
    assert [c.pid for c in daemon_health._candidates()] == [101]
    assert len(calls) == 2


def test_windows_matching_monitor_without_cwd_blocks_census(tmp_path, monkeypatch):
    from agent_worktrees import windows_process_census

    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(windows_process_census, "snapshot", lambda: [
        {"pid": 101, "cwd": "", "start_time": "monitor",
         "command_line": "python -m agent_worktrees status-monitor"},
    ])
    with pytest.raises(OSError, match="runtime scope for monitor PID 101"):
        daemon_health._candidates()


def _monitor_venv(runtime_home: Path) -> Path:
    scripts = runtime_home / "versions" / "1.0.0" / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts.parent / "pyvenv.cfg").write_text("home = C:/py\n", encoding="utf-8")
    return scripts / "python.exe"


def _pair_snapshot(tmp_path):
    return [
        {"pid": pid, "cwd": str(tmp_path), "start_time": str(pid),
         "command_line": "python -m agent_worktrees status-monitor"}
        for pid in (101, 202)
    ]


@pytest.mark.guard
def test_windows_candidates_carry_ppid_and_image(tmp_path, monkeypatch):
    from agent_worktrees import windows_process_census

    launcher = _monitor_venv(tmp_path)
    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(windows_process_census, "snapshot", lambda: _pair_snapshot(tmp_path))
    monkeypatch.setattr(daemon_health.locks, "process_start_time", str)
    monkeypatch.setattr(daemon_health.procs, "parent_pid", lambda pid: 101 if pid == 202 else 1)
    images = {101: str(launcher), 202: "C:/py/python.exe"}
    monkeypatch.setattr(daemon_health.procs, "process_executable_path", images.get)

    candidates = daemon_health._candidates()

    assert [(c.pid, c.ppid, c.image) for c in candidates] == [
        (101, 1, str(launcher)), (202, 101, "C:/py/python.exe"),
    ]


@pytest.mark.guard
@pytest.mark.parametrize("changes_at", [0, 1], ids=["before-reads", "during-reads"])
def test_windows_candidate_identity_change_makes_structure_unreadable(
    tmp_path, monkeypatch, changes_at
):
    """ppid/image are only trusted while the PID still names the snapshot's
    process: a PID that exits or is reused mid-read is reported unreadable,
    so grouping stays uncertain instead of certifying a replacement."""
    from agent_worktrees import windows_process_census

    launcher = _monitor_venv(tmp_path)
    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(windows_process_census, "snapshot", lambda: _pair_snapshot(tmp_path))
    monkeypatch.setattr(daemon_health.procs, "parent_pid", lambda pid: 101 if pid == 202 else 1)
    images = {101: str(launcher), 202: "C:/py/python.exe"}
    monkeypatch.setattr(daemon_health.procs, "process_executable_path", images.get)
    reads = {101: 0, 202: 0}

    def start_time(pid):
        reads[pid] += 1
        if pid == 202 and reads[pid] > changes_at:
            return "reused"
        return str(pid)

    monkeypatch.setattr(daemon_health.locks, "process_start_time", start_time)

    candidates = {c.pid: c for c in daemon_health._candidates()}

    assert (candidates[202].ppid, candidates[202].image) == (None, None)
    assert (candidates[101].ppid, candidates[101].image) == (1, str(launcher))

@pytest.mark.guard
def test_windows_interpreter_pair_is_one_generation_not_a_duplicate(tmp_path, monkeypatch):
    """A venv-redirector launcher and its base-interpreter worker are one
    status-monitor generation: the census groups them instead of refusing
    (or, worse, offering one half as a duplicate to terminate)."""
    from zdd import diagnostics

    from agent_worktrees import windows_process_census

    launcher = _monitor_venv(tmp_path)
    monkeypatch.setattr(daemon_health.sys, "platform", "win32")
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(windows_process_census, "snapshot", lambda: _pair_snapshot(tmp_path))
    monkeypatch.setattr(daemon_health.locks, "process_start_time", str)
    monkeypatch.setattr(daemon_health.procs, "parent_pid", lambda pid: 101 if pid == 202 else 1)
    images = {101: str(launcher), 202: "C:/py/python.exe"}
    monkeypatch.setattr(daemon_health.procs, "process_executable_path", images.get)
    ctx = daemon_health._context()
    assert ctx.group_launchers is True

    candidates = {c.pid: c for c in daemon_health._candidates()}
    generations, uncertain = diagnostics.group_generations(
        candidates, structural=ctx.group_launchers, is_launcher_image=ctx.is_launcher_image,
    )

    assert uncertain is None
    assert [(g.worker.pid, g.launcher.pid) for g in generations] == [(202, 101)]


@pytest.mark.guard
def test_launcher_outside_the_runtime_root_is_not_trusted(tmp_path, monkeypatch):
    elsewhere = _monitor_venv(tmp_path / "other-cell")
    runtime_home = tmp_path / "cell"
    runtime_home.mkdir()
    monkeypatch.setattr(
        daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: runtime_home)

    assert daemon_health._is_runtime_launcher(str(elsewhere)) is False
    assert daemon_health._is_runtime_launcher(str(_monitor_venv(runtime_home))) is True


@pytest.mark.guard
def test_posix_context_does_not_group_launchers(monkeypatch):
    monkeypatch.setattr(daemon_health.sys, "platform", "linux")
    assert daemon_health._context().group_launchers is False


def _windows_backstop(
    tmp_path, monkeypatch, processes, *, lock_pid, abandoned_pid=None, outside_parents=None,
):
    """Drive the real post-cutover backstop (``status-monitor-reap-stale``)
    and the real status-monitor doctor wiring over a fake Windows census.

    ``processes`` maps pid -> (ppid, "launcher" | "worker"); start times
    follow pid order. ``abandoned_pid`` names the never-published passive of
    a stale cutover breadcrumb -- the one target generic repair terminates.
    ``outside_parents`` maps a live parent pid the census omits to its
    executable (``"launcher"``, any other image, or ``None`` if unreadable).
    Returns the list of pids the doctor terminated.
    """
    import argparse
    import dataclasses
    import json
    import time
    from datetime import datetime, timedelta, timezone

    from zdd import breadcrumb, routing

    from agent_worktrees import status_monitor_reap_stale, windows_process_census

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    launcher = str(_monitor_venv(runtime))
    live = {pid: str(1000 + pid) for pid in processes}
    terminated: list[int] = []
    # Only daemon_health sees "win32": patching the global sys.platform would
    # also send zdd's routing lock down its msvcrt branch, which does not
    # exist off Windows, so this backstop could not run on a Linux lane.
    monkeypatch.setattr(daemon_health, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(daemon_health.status_monitor_runtime, "_aw_runtime_home", lambda: runtime)
    monkeypatch.setattr(windows_process_census, "snapshot", lambda: [
        {"pid": pid, "cwd": str(runtime), "start_time": start,
         "command_line": "python -m agent_worktrees status-monitor"}
        for pid, start in sorted(live.items())
    ])
    monkeypatch.setattr(daemon_health.procs, "parent_pid", lambda pid: processes[pid][0])
    outside = dict(outside_parents or {})

    def image_of(pid):
        if pid in outside:
            return launcher if outside[pid] == "launcher" else outside[pid]
        return launcher if processes[pid][1] == "launcher" else "C:/py/python.exe"

    monkeypatch.setattr(daemon_health.procs, "process_executable_path", image_of)
    real_alive, real_start = daemon_health.locks.pid_alive, daemon_health.locks.process_start_time
    monkeypatch.setattr(
        daemon_health.locks, "pid_alive",
        lambda pid: True if pid in outside else real_alive(pid),
    )
    # An outside parent started before every census member (no PID reuse);
    # census members keep the start token the snapshot recorded.
    monkeypatch.setattr(
        daemon_health.locks, "process_start_time",
        lambda pid: "1" if pid in outside else live.get(pid) or real_start(pid),
    )

    def terminate(pid, expected_start_time, *, exit_timeout=None):
        if live.get(pid) != expected_start_time:
            return {"killed": False, "identity_verified": False}
        terminated.append(pid)
        live.pop(pid)
        return {"killed": True, "identity_verified": True, "exit_confirmed": True}

    lock = {"pid": lock_pid, "start_time": live[lock_pid]}
    real_context = daemon_health._context
    monkeypatch.setattr(daemon_health, "_context", lambda: dataclasses.replace(
        real_context(),
        read_lock=lambda: lock,
        lock_is_live=lambda data: data == lock and lock_pid in live,
        acquire_cutover_guard=None,
        repair_supported=True,
        is_superseded=lambda pid, generation: False,
        terminate_pid_if_identity=terminate,
        make_client=None,
        health_check=None,
        reachability_check=None,
        abandoned_passive_grace_seconds=0.0,
    ))
    config_dir = daemon_health.status_monitor_cutover.routing_dir()
    routing.publish_active(
        config_dir, bind="127.0.0.1", port=9281, pid=lock_pid, version="1.0.0",
    )
    if abandoned_pid is not None:
        record = dict(breadcrumb.write_breadcrumb(
            config_dir, state="started", old={"bind": "127.0.0.1", "port": 9281},
            new_port=9282, new_pid=abandoned_pid,
            new_process_start_time=live[abandoned_pid],
            publication_witness=routing.publication_witness(
                config_dir, deadline=time.monotonic() + 1),
        ))
        record["updated_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=9999)).isoformat()
        (config_dir / "cutover.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        status_monitor_reap_stale, "log_path", lambda: tmp_path / "logs" / "reap-stale.log")
    monkeypatch.setattr(
        status_monitor_reap_stale,
        "_ensure_monitor_if_zero_candidates_under_cutover_guard",
        lambda: "skipped-live",
    )

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=0.0))
    assert rc == 0
    return terminated


@pytest.mark.guard
def test_backstop_leaves_a_lone_launcher_worker_generation_running(
    tmp_path, monkeypatch, capsys
):
    from agent_worktrees import doctor_render

    terminated = _windows_backstop(
        tmp_path, monkeypatch, {101: (1, "launcher"), 202: (101, "worker")}, lock_pid=202,
    )

    assert terminated == []
    assert "applied daemon-health repair (0 finding(s))" in capsys.readouterr().out
    report = daemon_health.doctor_report(apply=False)
    assert report["candidate_count"] == 2
    assert report["generation_count"] == 1
    doctor_render.render_daemon_health_report(report)
    out = capsys.readouterr().out
    assert "no abnormal cutover findings" in out
    assert "generation: launcher pid 101 -> worker pid 202 (one daemon)" in out


@pytest.mark.guard
def test_doctor_reports_a_second_generation_as_one_duplicate(tmp_path, monkeypatch, capsys):
    from agent_worktrees import doctor_render

    _windows_backstop(
        tmp_path, monkeypatch,
        {101: (1, "launcher"), 202: (101, "worker"),
         303: (1, "launcher"), 404: (303, "worker")},
        lock_pid=202,
    )
    capsys.readouterr()

    report = daemon_health.doctor_report(apply=False)

    assert report["generation_count"] == 2
    (finding,) = report["findings"]
    assert finding["kind"] == "duplicate_resident"
    doctor_render.render_daemon_health_report(report)
    out = capsys.readouterr().out
    assert "-> pid 404 (worker), pid 303 (launcher)" in out
    assert "generation: launcher pid 101 -> worker pid 202 (one daemon)" in out
    assert "generation: launcher pid 303 -> worker pid 404 (one daemon)" in out


@pytest.mark.guard
def test_backstop_repairs_an_abandoned_passive_when_grouping_is_certain(
    tmp_path, monkeypatch, capsys
):
    # The owner runs as a launcher/worker pair; the abandoned passive is the
    # never-published process a stale cutover breadcrumb names.
    terminated = _windows_backstop(
        tmp_path, monkeypatch,
        {101: (1, "launcher"), 202: (101, "worker"), 303: (1, "worker")},
        lock_pid=202, abandoned_pid=303,
    )

    assert terminated == [303]
    assert "terminations skipped" not in capsys.readouterr().out


@pytest.mark.guard
def test_backstop_terminates_nothing_when_grouping_is_uncertain(
    tmp_path, monkeypatch, capsys
):
    # The same repair, but 303's parent is unreadable: the automatic backstop
    # must not guess whether it is a lone daemon or half of a generation.
    terminated = _windows_backstop(
        tmp_path, monkeypatch, {101: (1, "worker"), 303: (None, "worker")},
        lock_pid=101, abandoned_pid=303,
    )

    assert terminated == []
    out = capsys.readouterr().out
    assert "terminations skipped" in out
    assert "parent of candidate 303 unreadable" in out
    # The backstop runs detached with no console: the refusal is also durable.
    logged = (tmp_path / "logs" / "reap-stale.log").read_text(encoding="utf-8")
    assert "terminations skipped -- generation grouping uncertain" in logged


@pytest.mark.guard
def test_backstop_terminates_nothing_for_a_launcher_whose_worker_was_missed(
    tmp_path, monkeypatch, capsys
):
    # The abandoned passive's census shows only its launcher (a venv
    # redirector): the worker it started may be the half the census missed,
    # so the backstop must not repair the launcher as a whole generation.
    terminated = _windows_backstop(
        tmp_path, monkeypatch, {101: (1, "worker"), 303: (1, "launcher")},
        lock_pid=101, abandoned_pid=303,
    )

    assert terminated == []
    out = capsys.readouterr().out
    assert "terminations skipped" in out
    assert "launcher candidate 303 has no captured worker" in out


@pytest.mark.guard
@pytest.mark.parametrize("parent_image", [None, "launcher"])
def test_backstop_terminates_nothing_for_a_worker_whose_live_launcher_was_missed(
    tmp_path, monkeypatch, capsys, parent_image
):
    # The abandoned passive's launcher (303) is alive but absent from the
    # census (e.g. its command line was unreadable); only its worker 404 was
    # captured. Terminating 404 would kill half of a live pair.
    terminated = _windows_backstop(
        tmp_path, monkeypatch, {101: (1, "worker"), 404: (303, "worker")},
        lock_pid=101, abandoned_pid=404, outside_parents={303: parent_image},
    )

    assert terminated == []
    out = capsys.readouterr().out
    assert "terminations skipped" in out
    assert "parent 303 of candidate 404 not in census" in out


@pytest.mark.guard
def test_backstop_still_repairs_a_worker_whose_missing_parent_is_not_a_launcher(
    tmp_path, monkeypatch
):
    terminated = _windows_backstop(
        tmp_path, monkeypatch, {101: (1, "worker"), 404: (303, "worker")},
        lock_pid=101, abandoned_pid=404,
        outside_parents={303: "C:/Windows/explorer.exe"},
    )

    assert terminated == [404]


@pytest.mark.guard
def test_outside_parent_is_gone_only_when_dead_or_reused(monkeypatch):
    starts = {}
    alive = {}
    monkeypatch.setattr(daemon_health.locks, "pid_alive", lambda pid: alive[pid])
    monkeypatch.setattr(daemon_health.locks, "process_start_time", lambda pid: starts[pid].pop(0))
    monkeypatch.setattr(daemon_health.procs, "process_executable_path", lambda pid: "C:/x.exe")

    alive[303] = False
    assert daemon_health._outside_parent(303, "500") == (None, True)
    alive[303], starts[303] = True, ["900"]
    assert daemon_health._outside_parent(303, "500") == (None, True), "PID reused later"
    starts[303] = ["100", "100"]
    assert daemon_health._outside_parent(303, "500") == ("C:/x.exe", False)
    starts[303] = ["100", "700"]
    assert daemon_health._outside_parent(303, "500") == (None, False), "identity changed"
    starts[303] = [None]
    assert daemon_health._outside_parent(303, "500") == (None, False)


@pytest.mark.guard
def test_backstop_never_logs_a_refused_repair_as_applied(tmp_path, monkeypatch, capsys):
    terminated = _windows_backstop(
        tmp_path, monkeypatch, {101: (1, "worker"), 303: (None, "worker")},
        lock_pid=101, abandoned_pid=303,
    )

    assert terminated == []
    out = capsys.readouterr().out
    logged = (tmp_path / "logs" / "reap-stale.log").read_text(encoding="utf-8")
    for text in (out, logged):
        assert "terminations skipped -- generation grouping uncertain" in text
        assert "applied daemon-health repair" not in text


@pytest.mark.guard
def test_doctor_shows_the_reason_when_only_the_re_census_is_uncertain(capsys):
    from agent_worktrees import doctor_render

    finding = {"kind": "abandoned_passive", "targets": [{"pid": 303, "role": "worker"}]}
    report = {
        "mode": "apply",
        "findings": [finding],
        "remaining_findings": [finding],
        "before": {"findings": [finding], "grouping_uncertain": False},
        "after": {
            "findings": [finding],
            "grouping_uncertain": True,
            "grouping_uncertain_reason": "parent of candidate 303 unreadable",
        },
        "actions": [{
            "kind": "generation_grouping", "blocked": True,
            "reason": "generation grouping uncertain",
        }],
    }

    doctor_render.render_daemon_health_report(report)

    out = capsys.readouterr().out
    assert "generation grouping uncertain; terminations refused" in out
    assert "parent of candidate 303 unreadable" in out
@pytest.mark.guard
def test_backstop_log_lives_under_the_runtime_install_root(tmp_path, monkeypatch):
    from agent_worktrees import config as cfg
    from agent_worktrees import status_monitor_reap_stale

    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "cell")

    assert status_monitor_reap_stale.log_path() == (
        tmp_path / "cell" / "logs" / "status-monitor-reap-stale.log")
