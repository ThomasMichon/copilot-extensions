"""Pure R1a deadline/ownership contracts; native consumers are a separate tier."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops as go
from agent_worktrees import push_timeout as pt
from agent_worktrees import status_bar_cli as sb


@pytest.fixture
def clock(monkeypatch):
    from agent_procutil import owned_process

    value = SimpleNamespace(now=100.0)
    monkeypatch.setattr(go, "_windows_observation", lambda: True)
    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(go.time, "monotonic", lambda: value.now)
    return value


@pytest.fixture
def checkout(tmp_path):
    (tmp_path / ".git").mkdir()
    return str(tmp_path)


def result(args, stdout=""):
    return subprocess.CompletedProcess(["git", *args], 0, stdout, "")


@pytest.mark.parametrize("active", [False, True])
def test_initial_timeout_retains_only_known_activity(monkeypatch, checkout, clock, active):
    calls = []

    def git(*args, **options):
        calls.append(options)
        raise subprocess.TimeoutExpired(args, options["timeout"])

    monkeypatch.setattr(go, "git", git)
    info = go.classify_worktree(
        checkout, "tracked", fetch=True,
        active_paths={checkout} if active else None,
    )
    assert info.state == (go.WorktreeState.ACTIVE if active else go.WorktreeState.UNKNOWN)
    assert info.current_branch is None and not info.branch_drift
    assert not info.fetch_requested and not info.fetch_failed
    assert len(calls) == 1
    assert calls[0]["observation_deadline"] == 115.0
    assert calls[0]["kill_tree"] is True


def test_classification_reuses_one_deadline(monkeypatch, checkout, clock):
    calls = []

    def git(*args, **options):
        calls.append((args, options))
        clock.now += 1
        if args[0] == "rev-parse":
            return result(args, "actual\n")
        if args[0] == "status":
            return result(args, " M changed\n")
        if args[0] == "rev-list":
            return result(args, "2\t0\n")
        return result(args, "subject\n")

    monkeypatch.setattr(go, "git", git)
    info = go.classify_worktree(checkout, "tracked", observation_deadline=112.0)
    assert info.state == go.WorktreeState.DIRTY
    assert info.current_branch == "actual" and info.branch_drift
    assert [options["observation_deadline"] for _, options in calls] == [112.0] * 4
    assert [options["timeout"] for _, options in calls] == [12.0, 11.0, 10.0, 9.0]


@pytest.mark.parametrize("deadline", [99.0, 100.0, 105.0])
def test_insufficient_budget_starts_no_git(monkeypatch, checkout, clock, deadline):
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("expired observation spawned"))
    info = go.classify_worktree(checkout, "tracked", fetch=True, observation_deadline=deadline)
    assert info.state == go.WorktreeState.UNKNOWN
    assert not info.fetch_requested


@pytest.mark.parametrize("started", [False, True])
def test_fetch_timeout_tracks_actual_start(monkeypatch, checkout, clock, started):
    def git(*args, **options):
        if args[0] == "rev-parse":
            return result(args, "tracked\n")
        error = subprocess.TimeoutExpired(args, options["timeout"])
        error.observation_started = started
        raise error

    monkeypatch.setattr(go, "git", git)
    info = go.classify_worktree(checkout, "tracked", fetch=True)
    assert info.state == go.WorktreeState.UNKNOWN
    assert info.fetch_requested is started and info.fetch_failed is started


def test_exhaustion_before_fetch_is_not_attempt(monkeypatch, checkout, clock):
    def git(*args, **options):
        assert args[0] == "rev-parse"
        clock.now = 111.0
        return result(args, "tracked\n")

    monkeypatch.setattr(go, "git", git)
    info = go.classify_worktree(checkout, "tracked", fetch=True)
    assert info.state == go.WorktreeState.UNKNOWN
    assert not info.fetch_requested


def test_gone_does_not_probe(monkeypatch, tmp_path, clock):
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("GONE probed Git"))
    assert go.classify_worktree(str(tmp_path), "tracked").state == go.WorktreeState.GONE


def test_legacy_branch_helper_does_not_opt_in(monkeypatch):
    calls = []

    def git(*args, **options):
        calls.append(options)
        return result(args, "main\n")

    monkeypatch.setattr(go, "git", git)
    assert go.current_branch(".") == "main"
    assert calls == [{"cwd": ".", "check": False}]


def test_git_forwards_absolute_deadline_to_contained_runner(monkeypatch, clock):
    calls = []
    monkeypatch.setattr(
        pt, "run_bounded",
        lambda cmd, **options: calls.append(options) or result(cmd),
    )
    go.git("status", timeout=11, observation_deadline=111)
    assert calls[0]["observation_deadline"] == 111
    assert calls[0]["timeout"] == 11


@pytest.mark.parametrize("surface", ["render", "json"])
@pytest.mark.parametrize("stall", ["default", "branch"])
def test_segment_prefix_timeout_is_unknown(monkeypatch, checkout, clock, surface, stall):
    monkeypatch.setattr(sb, "_core_helper", lambda name, local: local)
    monkeypatch.setattr(sb.cfg, "load_config", lambda **k: SimpleNamespace(
        default_repo=SimpleNamespace(remote="origin", default_branch="main"),
    ))
    monkeypatch.setattr(sb, "_find_record_for_path", lambda path: None)

    def probe(*args, **options):
        assert options["observation_deadline"] == 115
        raise subprocess.TimeoutExpired(args, 10)

    monkeypatch.setattr(sb, "_detect_upstream_branch", probe if stall == "default" else (
        lambda *a, **k: "main"
    ))
    monkeypatch.setattr(go, "_get_current_branch_safe", probe)
    monkeypatch.setattr(go, "classify_worktree", lambda *a, **k: pytest.fail("late classify"))
    if surface == "json":
        value = sb._status_segment_json(checkout, fetch=True)
        assert value["state"] == "unknown"
        assert value["closure"] is None
    else:
        assert "?" in sb._render_status_segment(checkout, plain=True, no_title=True)


def test_default_network_cap_and_timeout_do_not_fall_back(monkeypatch, clock):
    monkeypatch.setattr(sb, "_core_helper", lambda name, local: local)
    calls = []

    def git(*args, **options):
        calls.append((args, options))
        clock.now += 1
        if args[0] == "ls-remote":
            raise subprocess.TimeoutExpired(args, options["timeout"])
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(go, "git", git)
    with pytest.raises(subprocess.TimeoutExpired):
        sb._resolve_remote_default_branch(".", "origin", config_default="main", allow_remote=True)
    assert [args[0] for args, _ in calls] == ["rev-parse", "symbolic-ref", "ls-remote"]
    assert calls[-1][1]["timeout"] == 10
    assert {options["observation_deadline"] for _, options in calls} == {115}


@pytest.mark.parametrize("cancel", [False, True])
def test_bounded_runner_reserves_cleanup_inside_total(monkeypatch, clock, cancel):
    calls = []

    class Stream:
        def close(self):
            calls.append(("close-pipe", clock.now))

    class Process:
        stdin = None
        stdout, stderr = Stream(), Stream()
        returncode = 0

        def communicate(self, *, timeout):
            calls.append(("communicate", timeout))
            if len([item for item in calls if item[0] == "communicate"]) == 1:
                clock.now = 110
                if cancel:
                    raise KeyboardInterrupt
                raise subprocess.TimeoutExpired("git", timeout)
            clock.now += 1
            return "", ""

        def wait(self, *, timeout):
            calls.append(("wait", timeout))

    class Job:
        def terminate_and_wait(self, timeout):
            calls.append(("terminate", timeout))
            clock.now += 1
            self.close()

        def close(self):
            calls.append(("close-job", clock.now))

    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pt, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(pt, "_spawn_observation", lambda *a, **k: (Process(), Job()))
    with pytest.raises(KeyboardInterrupt if cancel else subprocess.TimeoutExpired) as error:
        pt.run_bounded(["git"], cwd=".", env={}, timeout=15, observation_deadline=115)
    if not cancel:
        assert error.value.observation_started is True
    assert ("communicate", 10) in calls
    assert ("terminate", 5) in calls
    assert ("communicate", 4) in calls
    assert ("wait", 3) in calls
    assert len([item for item in calls if item[0] == "close-pipe"]) == 2
    assert clock.now == 112


def test_runner_short_deadline_refuses_before_spawn(monkeypatch, clock):
    monkeypatch.setattr(pt, "_spawn_observation", lambda *a, **k: pytest.fail("spawn"))
    with pytest.raises(subprocess.TimeoutExpired) as error:
        pt.run_bounded(["git"], cwd=".", env={}, timeout=15, observation_deadline=104)
    assert error.value.observation_started is False


def test_inherited_posix_scope_is_not_swept(monkeypatch, clock):
    monkeypatch.setattr(pt.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pt, "detached_kwargs", lambda: {})
    monkeypatch.setattr(pt, "_spawn_observation", lambda *a, **k: pytest.fail("spawn"))
    with pytest.raises(pt.ObservationUnavailable):
        pt.run_bounded(["git"], cwd=".", env={}, timeout=15, observation_deadline=115)


def test_legacy_binary_input_and_inherited_group_remain_unchanged(monkeypatch):
    captured = {}

    class Process:
        returncode = 0

        def communicate(self, **options):
            captured.update(options)
            return b"output", b"error"

    def spawn(*args, **options):
        captured.update(options)
        return Process(), None

    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pt, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(pt, "spawn_sync_in_kill_on_close_job", spawn)
    value = pt.run_bounded(
        ["git"], cwd=".", env={}, timeout=180, binary=True,
        input=b"request", inherit_process_group=True,
    )
    assert value.stdout == b"output" and value.stderr == b"error"
    assert captured["text"] is False and captured["input"] == b"request"
    assert captured["timeout"] == 180


def test_legacy_timeout_still_has_its_original_cleanup_allowance(monkeypatch):
    waits = []

    class Process:
        returncode = 1

        def communicate(self, *, timeout):
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired("git", timeout)
            return "partial", ""

    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pt, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(pt, "spawn_sync_in_kill_on_close_job", lambda *a, **k: (Process(), None))
    monkeypatch.setattr(pt, "_kill_tree", lambda process: None)
    with pytest.raises(subprocess.TimeoutExpired):
        pt.run_bounded(["git"], cwd=".", env={}, timeout=180)
    assert waits == [180, 5]


@pytest.mark.parametrize("failure", [RuntimeError("unsupported"), PermissionError("denied"), OSError("no proc")])
def test_full_capability_failure_prevents_spawn(monkeypatch, clock, failure):
    from agent_procutil import owned_process

    calls = []

    def require_support():
        calls.append("preflight")
        raise failure

    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pt, "detached_kwargs", lambda: {"start_new_session": True})
    monkeypatch.setattr(owned_process, "_require_posix_execution_support", lambda: None)
    monkeypatch.setattr(owned_process, "require_caller_owned_support", require_support)
    monkeypatch.setattr(pt, "_spawn_observation", lambda *a, **k: pytest.fail("spawn before support"))
    with pytest.raises(pt.ObservationUnavailable) as error:
        pt.run_bounded(["git"], cwd=".", env={}, timeout=15, observation_deadline=115)
    assert error.value.__cause__ is failure
    assert calls == ["preflight"]


def test_failed_job_cleanup_retains_handle_for_confirmation_retry(monkeypatch, clock):
    class Process:
        stdin = stdout = stderr = None
        returncode = 0

        def communicate(self, **options):
            return "", ""

    class Job:
        handle = object()
        attempts = 0
        closes = 0

        def terminate_and_wait(self, timeout):
            assert self.handle is not None
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("still active")
            self.close()

        def close(self):
            self.closes += 1
            self.handle = None

    process, job = Process(), Job()
    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pt, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(pt, "_spawn_observation", lambda *a, **k: (process, job))
    with pytest.raises(pt.ObservationCleanupError) as error:
        pt.run_bounded(["git"], cwd=".", env={}, timeout=15, observation_deadline=115)
    assert error.value.process is process and error.value.job is job
    assert job.handle is not None and job.closes == 0
    # This is explicit owner cleanup, not a new observation or renewed D.
    error.value.job.terminate_and_wait(1)
    assert job.handle is None and job.attempts == 2 and job.closes == 1


def test_default_cleanup_failure_does_not_probe_fallback(monkeypatch, clock):
    monkeypatch.setattr(sb, "_core_helper", lambda name, local: local)
    calls = []
    failure = pt.ObservationCleanupError("unconfirmed child")

    def git(*args, **options):
        calls.append(args[0])
        if args[0] == "ls-remote":
            raise failure
        if len(calls) > 3:
            pytest.fail("fallback after unconfirmed cleanup")
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(go, "git", git)
    with pytest.raises(pt.ObservationCleanupError) as error:
        sb._resolve_remote_default_branch(".", "origin", config_default="main", allow_remote=True)
    assert error.value is failure
    assert calls == ["rev-parse", "symbolic-ref", "ls-remote"]


@pytest.mark.parametrize("surface", ["render", "json"])
def test_segment_preserves_cleanup_failure_custody(monkeypatch, checkout, clock, surface):
    monkeypatch.setattr(sb, "_core_helper", lambda name, local: local)
    failure = pt.ObservationCleanupError("retained ownership")
    failure.process, failure.job = object(), object()

    def observation(*args):
        raise failure

    monkeypatch.setattr(sb, "_segment_git_observation", observation)
    with pytest.raises(pt.ObservationCleanupError) as error:
        if surface == "render":
            sb._render_status_segment(checkout, plain=True, no_title=True)
        else:
            sb._status_segment_json(checkout)
    assert error.value is failure
    assert error.value.process is failure.process and error.value.job is failure.job
