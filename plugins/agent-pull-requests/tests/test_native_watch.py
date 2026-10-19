"""Native snapshot and continuous delivery contracts without remote access."""

from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from agent_pull_requests.watch_contract import ALL_TRANSITIONS, Baseline, compute_transitions
from agent_pull_requests.watch_daemon import WatchDaemon
from agent_pull_requests.watch_delivery import Deliveries
from agent_pull_requests.watch_notification import (
    ACKNOWLEDGED_NOTIFICATIONS, default_notify, event_payload,
)
from agent_pull_requests.watch_provider import (
    CONTINUOUS_NOTIFICATIONS, NATIVE_SNAPSHOTS, decode_snapshot, fetch_snapshot, target_id,
)
from agent_pull_requests.watch_registry import WatchKey, WatchRegistry
from agent_pull_requests import __main__ as cli
from agent_pull_requests import watch_provider
from agent_procutil import owned_process as watch_process
from agent_pull_requests.watch_storage import read_subscriptions_state
from agent_pull_requests.watch_storage import StateCommitUncertain


def report(repo="project/repo", number=7, revision="a", *, merged=False):
    observation = {
        "schema": "agent-worktrees.pr-observation", "version": 1,
        "provider": "azure-devops", "repo": repo, "number": number,
        "head_sha": "head", "complete": True, "errors": [],
        "votes": [], "approval_events": [], "discussions": [], "policies": [], "gates": [],
        "fingerprints": {
            name: revision for name in ("votes", "approvals", "discussions", "policies", "gates")
        },
    }
    return {
        "schema": "agent-worktrees.pr-snapshot", "version": 1, "repo": repo, "pr": number,
        "provider": "azure-devops", "pr_state": "closed" if merged else "open",
        "merged": merged, "head_sha": "head", "base_ref": "main", "mergeable": True,
        "checks_state": "pending", "title": "example", "draft": False,
        "observation": observation,
    }


def snapshot(revision="a", **kwargs):
    value = report(revision=revision, **kwargs)
    return decode_snapshot(value, value["repo"], value["pr"])


def registry(command, *, timeout=None):
    reg = WatchRegistry()
    key = WatchKey("project/repo", 7, str(command))
    reg.register(
        key, "pr-watch:owner", until=ALL_TRANSITIONS, notify={"argv": ["callback"]},
        acknowledged=True, continuous=True,
        target_id=target_id(snapshot()),
        timeout=timeout,
    )
    return reg, key


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_decoder_preserves_native_evidence_without_interpreting_approval():
    value = report()
    value.update(
        api_base="https://dev.azure.com/example", author="author", cursor="opaque",
        checks_complete=True, check_failures=[{"name": "example", "state": "pending"}],
    )
    value["observation"]["votes"] = [
        {"actor_id": "author", "vote": 10}, {"actor_id": "reviewer", "vote": 10},
    ]
    value["observation"]["approval_events"] = [{"actor_id": "author", "vote": 10}]
    snap = decode_snapshot(value, "project/repo", 7)
    assert snap.native_snapshot["observation"] == value["observation"]
    assert snap.native_snapshot["cursor"] == "opaque"
    assert snap.native_snapshot["checks_complete"] is True
    value["observation"]["votes"].clear()
    assert len(snap.observation["votes"]) == 2


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"repo": "foreign/repo"}, {"pr": True},
    {"mergeable": "yes"}, {"merged": 1}, {"observation": {}},
    {"observation": {"schema": "unknown"}}, {"draft": None},
])
def test_decoder_rejects_malformed_or_foreign_snapshot(change):
    value = report()
    value.update(change)
    with pytest.raises(ValueError):
        decode_snapshot(value, "project/repo", 7)


def test_native_fetch_is_one_bounded_headless_operation(tmp_path, monkeypatch):
    command = tmp_path / "worktrees"
    command.touch()
    seen = []

    def run(argv):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(report()), "")

    monkeypatch.setattr(watch_provider, "_run_native", run)
    assert fetch_snapshot("project/repo", 7, str(command)).observation
    assert seen[0] == [
        str(command), "pr-watch", "snapshot", "project/repo", "7", "--json",
    ]
    assert len(seen) == 1


def test_native_fetch_crosses_real_cli_process_boundary(tmp_path):
    script = tmp_path / "snapshot.py"
    script.write_text(
        "import json,sys\n"
        "assert sys.argv[1:] == ['pr-watch','snapshot','project/repo','7','--json']\n"
        f"print({json.dumps(report())!r})\n",
        encoding="utf-8",
    )
    command = tmp_path / ("worktrees.cmd" if os.name == "nt" else "worktrees")
    command.write_text(
        f'@"{sys.executable}" "{script}" %*\n' if os.name == "nt" else
        f'#!{sys.executable}\nexec(compile(open({str(script)!r}).read(),{str(script)!r},"exec"))\n',
        encoding="utf-8",
    )
    if os.name != "nt":
        command.chmod(0o700)
    assert fetch_snapshot("project/repo", 7, str(command)).provider == "azure-devops"


def test_governed_native_read_uses_owned_peer_prefix_not_command_hint(monkeypatch):
    prefix = [sys.executable, "-B", "-I", "immutable-peer-bootstrap"]
    launched = []
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", object())
    monkeypatch.setattr(cli, "_agent_worktrees_prefix", lambda: list(prefix))

    def run(argv):
        launched.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(report()), "")

    monkeypatch.setattr(watch_provider, "_run_native", run)
    result = cli._watch_native_snapshot("project/repo", 7, "untrusted-or-missing-command")
    assert result.provider == "azure-devops"
    assert launched == [[*prefix, "pr-watch", "snapshot", "project/repo", "7", "--json"]]


def test_governed_native_peer_refusal_never_uses_legacy_command(monkeypatch):
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", object())

    def refused():
        raise PermissionError("selected cell refused")

    monkeypatch.setattr(cli, "_agent_worktrees_prefix", refused)
    monkeypatch.setattr(
        watch_provider, "_run_native", lambda *_: pytest.fail("refused peer was launched"),
    )
    with pytest.raises(PermissionError, match="selected cell"):
        cli._watch_native_snapshot("project/repo", 7, sys.executable)


def test_bound_writer_handoff_preserves_native_pending_deadline_and_continuation(
    tmp_path, monkeypatch,
):
    from agent_pull_requests.watch_writer import WatchWriter

    state = tmp_path / "cell-state"
    foreign = tmp_path / "foreign-legacy"
    command = tmp_path / "worktrees"
    command.touch()
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(foreign))
    revision = ["a"]
    replayed = []
    owners = []

    def make(notify):
        owner = WatchWriter(
            root=state, authorize=lambda _: None,
            fetch=lambda *_: pytest.fail("native writer used legacy fetch"),
            native_fetch=lambda *_: snapshot(revision[0]),
            notify=notify, poll_interval=0.01,
        )
        owners.append(owner)
        return owner

    try:
        first = make(lambda _: 1)
        first.adopt(timeout=3)
        first.activate(timeout=3)
        registration = first.compute("register", {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True, "timeout": 60,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        })
        assert registration["registered"]
        deadline = read_subscriptions_state(state)[0]["deadline_at"]
        revision[0] = "b"
        wait_until(lambda: bool(first.daemon._registry.pending_events()))
        first.drain_and_release(timeout=3)
        frozen = first.daemon._registry.pending_events()[0].frozen_payload
        monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path / "another-legacy"))

        def notify(event):
            replayed.append(event_payload(event))
            return 0

        second = make(notify)
        second.adopt(timeout=3)
        assert second.daemon.quiescent
        assert replayed == []
        restored = read_subscriptions_state(state)[0]
        assert restored["deadline_at"] == deadline
        assert restored["registration_id"] == registration["registration_id"]
        assert restored["native_baseline"]["fingerprints"]["votes"] == "b"
        assert restored["pending"]["payload"] == frozen
        second.activate(timeout=3)
        key = WatchKey("project/repo", 7, str(command))
        wait_until(lambda: bool(replayed) and second.daemon._registry.subscriber(
            key, "owner",
        ).pending is None)
        assert replayed[0] == frozen
        revision[0] = "c"
        wait_until(lambda: len(replayed) == 2)
        assert replayed[1]["snapshot"]["observation"]["fingerprints"]["votes"] == "c"
        assert read_subscriptions_state(state)[0]["deadline_at"] == deadline
        assert not (foreign / "watch-subscriptions.json").exists()
        assert not (tmp_path / "another-legacy" / "watch-subscriptions.json").exists()
    finally:
        for owner in reversed(owners):
            owner.close(timeout=3)


@pytest.mark.skipif(os.name != "nt", reason="native Windows callback Job ownership")
def test_native_callback_closes_owned_windows_descendants(tmp_path):
    import ctypes
    from ctypes import wintypes

    marker = tmp_path / "descendant-id"
    script = (
        "import pathlib,subprocess,sys\n"
        "sys.stdin.read()\n"
        "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "creationflags=subprocess.CREATE_NO_WINDOW)\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
    )
    reg, key = registry(tmp_path / "worktrees")
    reg.apply_snapshot(key, snapshot("a"))
    event = reg.apply_snapshot(key, snapshot("b"))[0]
    event.subscriber.notify = {"argv": [sys.executable, "-I", "-c", script], "timeout": 5}
    before = {thread for thread in threading.enumerate() if thread.name == "pr-native-input-deadline"}
    assert default_notify(event) == 0
    assert {thread for thread in threading.enumerate() if thread.name == "pr-native-input-deadline"} == before
    pid = int(marker.read_text())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87


@pytest.mark.skipif(os.name != "nt", reason="native Windows synchronous pipe writes")
@pytest.mark.timeout(20)
def test_windows_nonreading_native_callback_input_obeys_independent_deadline():
    from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

    script = f"""
import json,subprocess,sys,threading
from agent_pull_requests.watch_notification import default_notify
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_registry import WatchKey,WatchRegistry
value=json.loads({json.dumps(report(merged=True))!r})
value["title"]="x"*524288
snap=decode_snapshot(value,"project/repo",7)
reg=WatchRegistry()
key=WatchKey("project/repo",7,sys.executable)
reg.register(key,"owner",until=("merged",),acknowledged=True,notify={{
    "argv":[sys.executable,"-I","-c","import time;time.sleep(60)"],"timeout":0.3,
}})
event=reg.apply_snapshot(key,snap)[0]
print("invoking nonreading callback",flush=True)
try:
    default_notify(event)
except subprocess.TimeoutExpired:
    print("deadline enforced",flush=True)
else:
    raise AssertionError("nonreading callback did not time out")
assert not any(t.name.startswith("pr-native-input-deadline") for t in threading.enumerate())
"""
    process, job = spawn_sync_in_kill_on_close_job(
        [sys.executable, "-I", "-c", script], require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=6)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            job.terminate_and_wait(2)
        if timed_out:
            stdout, stderr = process.communicate(timeout=2)
        assert not timed_out, stdout + stderr
        assert process.returncode == 0, stdout + stderr
        assert "deadline enforced" in stdout
    finally:
        process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _owned_posix_fixture(monkeypatch):
    state = SimpleNamespace(signals=[], child_signals=[], events=[], exitcode=0, spawn=[])

    class Process:
        pid = 12345
        returncode = None
        reaped = False
        stdin = stdout = stderr = None
        args = ["owned-command"]

        def communicate(self, *_args, **_kwargs):
            self.returncode = 0
            self.reaped = True
            return "stdout", "stderr"

        def wait(self, **_kwargs):
            state.events.append("wait")
            self.reaped = True
            self.returncode = state.exitcode
            return self.returncode

    process = Process()

    def spawn(*args, **kwargs):
        state.spawn.append((args, kwargs))
        return process, None

    def signal_group(pid, _signal):
        state.events.append("signal")
        state.signals.append((pid, process.reaped))

    def signal_child(pid, _signal):
        state.events.append("signal-child")
        state.child_signals.append((pid, process.reaped))

    owned_os = SimpleNamespace(
        name="posix", killpg=signal_group, waitid=lambda *_: SimpleNamespace(si_pid=process.pid),
        kill=signal_child, getpid=lambda: 999, getpgrp=lambda: 999, getsid=lambda _: 999,
        P_PID=1, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000,
    )
    monkeypatch.setattr(watch_process, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(watch_process, "os", owned_os)
    monkeypatch.setattr(watch_process, "signal", SimpleNamespace(
        SIGKILL=9, SIGCHLD=17, SIG_DFL=0, getsignal=lambda _: 0,
    ))
    monkeypatch.setattr(watch_process, "_communicate_unreaped", lambda *_args, **_kwargs: ("stdout", "stderr"))
    return process, state, owned_os


@pytest.mark.parametrize("site", ["snapshot", "callback"])
def test_posix_native_helpers_do_not_signal_after_reaping_leader(tmp_path, monkeypatch, site):
    process, state, _ = _owned_posix_fixture(monkeypatch)
    if site == "snapshot":
        watch_provider._run_native(["owned-command"])
    else:
        reg, key = registry(tmp_path / "worktrees")
        reg.apply_snapshot(key, snapshot("a"))
        event = reg.apply_snapshot(key, snapshot("b"))[0]
        assert default_notify(event) == 0
    assert state.signals == [(process.pid, False)]
    assert state.events == ["signal", "wait"]


@pytest.mark.parametrize("failure", ["reaped", "not-a-child", "foreign-child", "auto-reaped"])
def test_posix_owned_child_refuses_reused_or_lost_identity(monkeypatch, failure):
    process, state, owned_os = _owned_posix_fixture(monkeypatch)
    if failure == "reaped":
        process.returncode = 0
        process.reaped = True
    elif failure == "not-a-child":
        def lost(*_):
            raise ChildProcessError("original child has been reaped")
        owned_os.waitid = lost
    elif failure == "foreign-child":
        owned_os.waitid = lambda *_: SimpleNamespace(si_pid=process.pid + 1)
    else:
        monkeypatch.setattr(watch_process.signal, "getsignal", lambda _: 1)
    with pytest.raises(RuntimeError, match="custody|identity"):
        watch_process.run_owned(["owned-command"], timeout=1)
    assert not state.signals
    assert not state.events


@pytest.mark.parametrize("cancelled", [False, True])
def test_posix_owned_child_timeout_or_cancellation_retires_before_reap(monkeypatch, cancelled):
    process, state, owned_os = _owned_posix_fixture(monkeypatch)
    owned_os.waitid = lambda *_: None
    state.exitcode = -9
    exception = KeyboardInterrupt("controlled cancellation") if cancelled else subprocess.TimeoutExpired(
        process.args, 1,
    )

    def interrupted(*_args, **_kwargs):
        raise exception

    monkeypatch.setattr(watch_process, "_communicate_unreaped", interrupted)
    with pytest.raises(type(exception)):
        watch_process.run_owned(process.args, timeout=1)
    assert state.signals == [(process.pid, False)]
    assert state.events == ["signal", "wait"]


def test_posix_owned_child_without_nonreaping_wait_refuses_before_spawn(monkeypatch):
    monkeypatch.setattr(watch_process, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        watch_process, "spawn_sync_in_kill_on_close_job",
        lambda *_args, **_kwargs: pytest.fail("unsupported custody launched a child"),
    )
    with pytest.raises(RuntimeError, match="unreaped POSIX custody"):
        watch_process.run_owned(["owned-command"], timeout=1)


def test_apr_owned_process_adapter_uses_canonical_implementation():
    from agent_pull_requests.watch_process import run_owned
    assert run_owned is watch_process.run_owned
    assert watch_provider.run_owned is watch_process.run_owned


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancel"])
def test_inherited_posix_peer_never_detaches_or_sweeps_outer_group(monkeypatch, outcome):
    process, state, _ = _owned_posix_fixture(monkeypatch)
    if outcome != "success":
        exception = (
            subprocess.TimeoutExpired(process.args, 1)
            if outcome == "timeout" else KeyboardInterrupt("controlled cancellation")
        )

        def interrupted(*_args, **_kwargs):
            raise exception
        monkeypatch.setattr(watch_process, "_communicate_unreaped", interrupted)
        with pytest.raises(type(exception)):
            watch_process.run_owned(process.args, timeout=1, inherit_process_group=True)
    else:
        assert watch_process.run_owned(
            process.args, timeout=1, inherit_process_group=True,
        ).returncode == 0
    assert state.spawn[0][1].get("start_new_session") is not True
    assert state.child_signals == [(process.pid, False)]
    assert not state.signals
    assert state.events == ["signal-child", "wait"]


@pytest.mark.parametrize("mismatch", ["group", "session"])
def test_inherited_posix_peer_refuses_unverified_worker_before_launch(monkeypatch, mismatch):
    _, state, owned_os = _owned_posix_fixture(monkeypatch)
    if mismatch == "group":
        owned_os.getpgrp = lambda: 998
    else:
        owned_os.getsid = lambda _: 998
    with pytest.raises(RuntimeError, match="session/group root"):
        watch_process.run_owned(["owned-command"], timeout=1, inherit_process_group=True)
    assert not state.spawn
    assert not state.signals
    assert not state.child_signals


@pytest.mark.parametrize("member", [False, True])
def test_contained_worker_checks_windows_membership_not_ambiguous_job_flags(monkeypatch, member):
    import agent_procutil

    def is_member(process, job, result):
        assert process == -1 and job is None
        result._obj.value = member
        return True

    kernel = SimpleNamespace(
        GetCurrentProcess=lambda: -1, IsProcessInJob=is_member,
        QueryInformationJobObject=lambda *_: pytest.fail("NULL query cannot attest the owned root"),
    )
    monkeypatch.setattr(agent_procutil, "_kernel32", lambda: kernel)
    monkeypatch.setattr(watch_process, "os", SimpleNamespace(name="nt"))
    if member:
        watch_process.require_contained_worker()
    else:
        with pytest.raises(RuntimeError, match="Job"):
            watch_process.require_contained_worker()


@pytest.mark.parametrize("cancelled", [False, True])
def test_owned_windows_job_is_retained_through_timeout_or_cancellation(monkeypatch, cancelled):
    events = []
    exception = KeyboardInterrupt("controlled cancellation") if cancelled else subprocess.TimeoutExpired(
        ["owned-command"], 1,
    )

    class Process:
        args = ["owned-command"]
        returncode = None
        stdin = stdout = stderr = None

        def communicate(self, *_args, **_kwargs):
            raise exception

        def wait(self, **_kwargs):
            events.append("wait")
            self.returncode = 1

    class Job:
        def terminate_and_wait(self, _timeout):
            events.append("job-retire")

    monkeypatch.setattr(watch_process, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        watch_process, "spawn_sync_in_kill_on_close_job",
        lambda *_args, **_kwargs: (Process(), Job()),
    )
    with pytest.raises(type(exception)):
        watch_process.run_owned(["owned-command"], timeout=1)
    assert events == ["job-retire", "wait"]


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancel"])
def test_windows_input_supervision_joins_after_completion_timeout_or_cancel(monkeypatch, outcome):
    retired = threading.Event()
    events = []
    before = {
        thread for thread in threading.enumerate()
        if thread.name.startswith("pr-native-input-deadline")
    }

    class Process:
        args = ["owned-command"]
        returncode = None
        stdin = stdout = stderr = None

        def communicate(self, _input, **_kwargs):
            events.append("communicate")
            if outcome == "timeout":
                assert retired.wait(3), "independent deadline did not retire the Job"
                self.returncode = 1
            elif outcome == "cancel":
                raise KeyboardInterrupt("controlled cancellation")
            else:
                self.returncode = 0
            return None, None

        def wait(self, **_kwargs):
            events.append("wait")
            return self.returncode

    class Job:
        def terminate_and_wait(self, _timeout):
            events.append("job-retire")
            retired.set()

    monkeypatch.setattr(watch_process, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        watch_process, "spawn_sync_in_kill_on_close_job",
        lambda *_args, **_kwargs: (Process(), Job()),
    )
    arguments = {
        "timeout": 0.05 if outcome == "timeout" else 5.0,
        "input_text": "x" * 131072, "capture": False,
    }
    if outcome == "success":
        assert watch_process.run_owned(["owned-command"], **arguments).returncode == 0
    else:
        expected = subprocess.TimeoutExpired if outcome == "timeout" else KeyboardInterrupt
        with pytest.raises(expected):
            watch_process.run_owned(["owned-command"], **arguments)
    assert events == ["communicate", "job-retire", "wait"]
    assert {
        thread for thread in threading.enumerate()
        if thread.name.startswith("pr-native-input-deadline")
    } == before


def test_windows_input_supervision_start_failure_retires_job_without_fallback(monkeypatch):
    events = []

    class Process:
        args = ["owned-command"]
        returncode = None
        stdin = stdout = stderr = None

        def communicate(self, *_args, **_kwargs):
            pytest.fail("stdin was written without independent supervision")

        def wait(self, **_kwargs):
            events.append("wait")

    class Job:
        def terminate_and_wait(self, _timeout):
            events.append("job-retire")

    class RefusedThread:
        ident = None

        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("controlled supervision startup refusal")

    monkeypatch.setattr(watch_process, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(watch_process, "threading", SimpleNamespace(
        Event=threading.Event, Lock=threading.Lock, Thread=RefusedThread,
    ))
    monkeypatch.setattr(
        watch_process, "spawn_sync_in_kill_on_close_job",
        lambda *_args, **_kwargs: (Process(), Job()),
    )
    with pytest.raises(RuntimeError, match="startup refusal"):
        watch_process.run_owned(["owned-command"], timeout=1, input_text="payload", capture=False)
    assert events == ["job-retire", "wait"]


@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux WNOWAIT and /proc exit evidence")
@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("outcome", ["success", "timeout", "cancel"])
def test_posix_owned_child_retires_real_descendants(tmp_path, monkeypatch, capture, outcome):
    marker = tmp_path / "descendant.json"
    script = (
        "import json,pathlib,subprocess,sys,time\n"
        f"outcome={outcome!r}\n"
        "if outcome!='cancel': assert len(sys.stdin.read())==65536\n"
        "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "stat=pathlib.Path('/proc',str(child.pid),'stat').read_text().rsplit(') ',1)[1].split()\n"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'pid':child.pid,'start':stat[19]}}))\n"
        "print('done',flush=True)\n"
        "if outcome!='success': time.sleep(60)\n"
    )
    if outcome == "cancel":
        def cancel(*_args, **_kwargs):
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert marker.exists()
            raise KeyboardInterrupt("controlled cancellation")
        monkeypatch.setattr(watch_process, "_communicate_unreaped", cancel)
    arguments = [sys.executable, "-I", "-c", script]
    kwargs = {
        "timeout": 2.0 if outcome == "timeout" else 5.0,
        "input_text": None if outcome == "cancel" else "\u03bb" * 65536,
        "capture": capture,
    }
    if outcome == "success":
        completed = watch_process.run_owned(arguments, **kwargs)
        assert completed.returncode == 0
        assert completed.stdout == ("done\n" if capture else None)
    else:
        expected = subprocess.TimeoutExpired if outcome == "timeout" else KeyboardInterrupt
        with pytest.raises(expected):
            watch_process.run_owned(arguments, **kwargs)
    child = json.loads(marker.read_text())
    stat_path = Path("/proc") / str(child["pid"]) / "stat"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            stat = stat_path.read_text().rsplit(") ", 1)[1].split()
        except FileNotFoundError:
            return
        if stat[19] != child["start"] or stat[0] in ("Z", "X"):
            return
        time.sleep(.01)
    pytest.fail("original owned descendant remained running")


@pytest.mark.skipif(sys.platform != "linux", reason="real nested POSIX callback/Bridge containment")
@pytest.mark.parametrize("outcome", ["success", "timeout", "cancel", "quiesce"])
def test_nested_bridge_cannot_write_after_outer_callback_scope_ends(tmp_path, monkeypatch, outcome):
    ready, root = tmp_path / "bridge-ready.json", tmp_path / "callback-root.json"
    permit, late_permit = tmp_path / "permit", tmp_path / "late-permit"
    effect, late_effect = tmp_path / "admitted", tmp_path / "late-write"
    late = (
        "import pathlib,time\n"
        f"permit=pathlib.Path({str(late_permit)!r})\n"
        "deadline=time.monotonic()+5\n"
        "while not permit.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        f"if permit.exists(): pathlib.Path({str(late_effect)!r}).write_text('late')\n"
    )
    receipt = {
        "ok": True, "session_id": "live-head", "message_id": 12, "receipt_current": True,
        "receipt_error": None,
        "receipt": {"session_id": "live-head", "message_id": 12, "stage": "admitted", "admitted_at": 1.0},
    }
    bridge = tmp_path / "bridge"
    bridge.write_text(
        f"#!{sys.executable}\n"
        "import json,os,pathlib,subprocess,sys,time\n"
        "if 'resolve' in sys.argv:\n"
        " print(json.dumps({'session_id':'live-head'}));raise SystemExit(0)\n"
        "assert '--expected-session-id' in sys.argv and '--idempotency-key' in sys.argv\n"
        "assert 'event_id' in sys.stdin.read()\n"
        f"ready=pathlib.Path({str(ready)!r});temporary=ready.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),'group':os.getpgrp(),'session':os.getsid(0)}));temporary.replace(ready)\n"
        f"permit=pathlib.Path({str(permit)!r})\n"
        "deadline=time.monotonic()+5\n"
        "while not permit.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "if not permit.exists(): raise SystemExit(3)\n"
        f"pathlib.Path({str(effect)!r}).write_text('admitted')\n"
        f"subprocess.Popen([sys.executable,'-I','-c',{late!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"print({json.dumps(receipt)!r},flush=True)\n",
        encoding="utf-8",
    )
    bridge.chmod(0o700)
    worker = (
        "import json,os,pathlib,sys\n"
        f"sys.path.insert(0,{str(Path(__file__).resolve().parents[3] / 'plugins' / 'agent-worktrees' / 'src')!r})\n"
        "os.environ.pop('COPILOT_EXTENSIONS_CONTEXT',None)\n"
        "from agent_worktrees.pr_watch_owner import dispatch\n"
        f"root=pathlib.Path({str(root)!r});temporary=root.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),'group':os.getpgrp(),'session':os.getsid(0)}));temporary.replace(root)\n"
        f"raise SystemExit(dispatch(['deliver','--contained-worker','--notify','owner','--bridge',{str(bridge)!r}]))\n"
    )
    arguments = [sys.executable, "-I", "-c", worker]
    reg, key = registry(sys.executable)
    reg.apply_snapshot(key, snapshot("a"))
    body = json.dumps(event_payload(reg.apply_snapshot(key, snapshot("b"))[0]))
    results, failures = [], []
    cancelled = threading.Event()
    if outcome == "cancel":
        observe = watch_process._observe_unreaped

        def cancel(process):
            status = observe(process)
            if ready.exists() and not cancelled.is_set():
                cancelled.set()
                raise KeyboardInterrupt("controlled callback cancellation")
            return status
        monkeypatch.setattr(watch_process, "_observe_unreaped", cancel)

    def run():
        try:
            results.append(watch_process.run_owned(
                arguments, timeout=3.0 if outcome == "timeout" else 5.0,
                input_text=body, capture=False,
            ))
        except BaseException as error:
            failures.append(error)

    daemon = None
    thread = None
    try:
        if outcome == "quiesce":
            command = tmp_path / "worktrees"
            command.touch()
            daemon = WatchDaemon(
                fetch=lambda *_: pytest.fail("native callback fell back to legacy"),
                native_fetch=lambda *_: snapshot(merged=True), poll_interval=.01,
            )
            assert daemon.compute("register", {
                "repo": "project/repo", "number": 7, "subscriber_id": "pr-watch:owner",
                "worktrees_command": str(command), "continuous": True,
                "until": ["merged", "closed"],
                "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
                "notify": {"argv": arguments, "timeout": 2.0},
            })["registered"]
        else:
            thread = threading.Thread(target=run)
            thread.start()
        wait_until(ready.exists)
        observed, owner = json.loads(ready.read_text()), json.loads(root.read_text())
        assert observed["group"] == observed["session"] == owner["pid"]
        assert owner["pid"] == owner["group"] == owner["session"]
        if outcome == "success":
            permit.write_text("go")
        if daemon is not None:
            with pytest.raises(TimeoutError):
                daemon.quiesce(timeout=.01)
            assert not daemon.quiescent
            wait_until(lambda: any(
                entry["last_error"] == "callback_timeout"
                for entry in daemon.status()["pending_deliveries"]
            ))
            daemon.quiesce(timeout=3)
            assert daemon.quiescent
            assert read_subscriptions_state()[0]["pending"] is not None
        else:
            thread.join(8)
            assert not thread.is_alive()
            if outcome == "success":
                assert not failures and results[0].returncode == 0
            else:
                expected = KeyboardInterrupt if outcome == "cancel" else subprocess.TimeoutExpired
                assert len(failures) == 1 and isinstance(failures[0], expected)
        permit.write_text("go")
        late_permit.write_text("go")
        time.sleep(.3)
        assert effect.exists() == (outcome == "success")
        assert not late_effect.exists()
    finally:
        permit.write_text("go")
        late_permit.write_text("go")
        if daemon is not None:
            daemon.close()
        if thread is not None:
            thread.join(8)
            assert not thread.is_alive()


def test_native_transitions_detect_changes_with_unchanged_rollups():
    before, after = snapshot("a"), snapshot("b")
    transitions = compute_transitions(Baseline.from_snapshot(before), after, ALL_TRANSITIONS)
    assert set(transitions) == {
        "review_changed", "votes_changed", "approvals_changed", "threads_changed",
        "policies_changed", "gates_changed",
    }
    assert compute_transitions(Baseline.from_snapshot(after), after, ALL_TRANSITIONS) == ()


def test_incomplete_observation_and_guidance_changes_are_visible():
    value = report()
    before = decode_snapshot(value, "project/repo", 7)
    value["observation"].update(complete=False, errors=["policies_unavailable"], guidance="Wait")
    after = decode_snapshot(value, "project/repo", 7)
    assert compute_transitions(Baseline.from_snapshot(before), after, ALL_TRANSITIONS) == (
        "gates_changed",
    )


def test_stream_freezes_pending_and_coalesces_successor_through_restart(tmp_path):
    reg, key = registry(tmp_path / "worktrees")
    reg.apply_snapshot(key, snapshot("a"))
    first = reg.apply_snapshot(key, snapshot("b"))[0]
    immutable = event_payload(first)
    reg.apply_snapshot(key, snapshot("c"))
    reg.apply_snapshot(key, snapshot("d"))
    assert event_payload(first) == immutable
    assert reg.subscriber(key, "pr-watch:owner").queued["payload"]["coalesced"]
    restored = WatchRegistry()
    assert restored.restore_state(reg.snapshot_state(), strict=True) == 1
    event = restored.pending_events()[0]
    assert event_payload(event) == immutable
    restored.acknowledge(event)
    successor = restored.pending_events()[0]
    assert event_payload(event) == immutable
    restored.acknowledge(event)
    assert restored.pending_events()[0].frozen_payload == successor.frozen_payload
    assert event_payload(successor)["snapshot"]["observation"]["fingerprints"]["votes"] == "d"
    assert event_payload(successor)["event_id"] != immutable["event_id"]
    restored.acknowledge(successor)
    assert restored.watching_count(key) == 1
    terminal = restored.apply_snapshot(key, snapshot("e", merged=True))[0]
    assert restored.watching_count(key) == 0
    restored.acknowledge(terminal)
    assert restored.status() == {}


def test_terminal_first_snapshot_retires_only_after_ack(tmp_path):
    reg, key = registry(tmp_path / "worktrees")
    event = reg.apply_snapshot(key, snapshot(merged=True))[0]
    assert reg.subscriber_count(key) == 1
    assert reg.watching_count(key) == 0
    reg.acknowledge(event)
    assert not reg.active_keys()


def test_native_baseline_and_target_installation_are_preserved(tmp_path):
    reg, key = registry(tmp_path / "first")
    other = WatchKey(key.repo, key.number, str(tmp_path / "second"))
    reg.register(
        other, "other", until=ALL_TRANSITIONS, notify={"argv": ["callback"]}, acknowledged=True,
        target_id=target_id(snapshot()),
    )
    reg.apply_snapshot(key, snapshot("a"))
    restored = WatchRegistry()
    restored.restore_state(reg.snapshot_state(), strict=True)
    assert len(restored.active_keys()) == 2
    assert restored.apply_snapshot(key, snapshot("a")) == ()
    assert len(restored.apply_snapshot(key, snapshot("b"))) == 1
    assert restored.subscriber_count(other) == 1


def test_legacy_state_and_event_remain_legacy():
    reg = WatchRegistry()
    key = WatchKey("example/repo", 1)
    reg.register(key, "legacy", until=("merged",), notify={})
    reg.apply_snapshot(key, snapshot(repo=key.repo, number=1))
    entry = reg.snapshot_state()[0]
    assert "continuous" not in entry and "native_baseline" not in entry
    assert set(entry["baseline"]) == {
        "merged", "closed", "review_decision", "mergeable", "checks_state",
    }


def test_owner_keeps_polling_while_callback_is_pending(tmp_path):
    command = tmp_path / "worktrees"
    command.touch()
    revision = ["a"]
    entered, release = threading.Event(), threading.Event()
    delivered = []

    def notify(event):
        delivered.append(event_payload(event))
        entered.set()
        assert release.wait(3)
        return 0

    daemon = WatchDaemon(
        fetch=lambda *_: pytest.fail("legacy fetch used"),
        native_fetch=lambda *_: snapshot(revision[0]),
        notify=notify, poll_interval=0.01, idle_exit=0.01,
    )
    try:
        health = daemon.compute("health", {})
        assert {NATIVE_SNAPSHOTS, CONTINUOUS_NOTIFICATIONS} <= set(health["capabilities"])
        registered = daemon.compute("register", {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
            "worktrees_command": str(command), "continuous": True,
        })
        assert registered["registered"]
        wait_until(lambda: daemon._registry.subscriber(
            WatchKey("project/repo", 7, str(command)), "owner",
        ).baseline is not None)
        revision[0] = "b"
        assert entered.wait(3)
        revision[0] = "c"
        wait_until(lambda: daemon._registry.subscriber(
            WatchKey("project/repo", 7, str(command)), "owner",
        ).queued is not None)
        assert delivered[0]["snapshot"]["observation"]["fingerprints"]["votes"] == "b"
        release.set()
        wait_until(lambda: len(delivered) == 2)
        assert delivered[1]["snapshot"]["observation"]["fingerprints"]["votes"] == "c"
    finally:
        release.set()
        daemon.close()


def test_native_capability_failure_never_registers(tmp_path):
    command = tmp_path / "worktrees"
    command.touch()
    daemon = WatchDaemon(fetch=lambda *_: snapshot())
    try:
        assert NATIVE_SNAPSHOTS not in daemon.compute("health", {})["capabilities"]
        result = daemon.compute("register", {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        })
        assert "error" in result
        assert not daemon.status()["subscribers"]
    finally:
        daemon.close()


def test_continuous_retry_keeps_existing_pending_generation(tmp_path):
    command = tmp_path / "worktrees"
    command.touch()
    daemon = WatchDaemon(
        fetch=lambda *_: snapshot(), native_fetch=lambda *_: snapshot(),
        active=False,
    )
    try:
        # Exercise registration deterministically without starting worker effects.
        daemon._ensure_poller = lambda *_: None
        payload = {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        }
        first = daemon._handle_register(payload)
        key = WatchKey("project/repo", 7, str(command))
        daemon._registry.apply_snapshot(key, snapshot("a"))
        event = daemon._registry.apply_snapshot(key, snapshot("b"))[0]
        expected = event_payload(event)
        second = daemon._handle_register(payload)
        assert first["registration_id"] == second["registration_id"]
        assert event_payload(daemon._registry.pending_events()[0]) == expected
        assert "error" in daemon._handle_register({**payload, "notify": {"argv": ["different"]}})
    finally:
        daemon.close()


def test_stream_timeout_is_queued_behind_pending_and_retires_after_ack(tmp_path):
    reg, key = registry(tmp_path / "worktrees")
    reg.apply_snapshot(key, snapshot("a"))
    first = reg.apply_snapshot(key, snapshot("b"))[0]
    reg.subscriber(key, "pr-watch:owner").deadline = time.monotonic() - 1
    timed_out = reg.sweep_timeouts((key,))
    assert timed_out and timed_out[0].timed_out
    reg.acknowledge(first)
    terminal = reg.pending_events()[0]
    assert event_payload(terminal)["timed_out"]
    reg.acknowledge(terminal)
    assert not reg.active_keys()


@pytest.mark.parametrize("changed_field", ["head", "gates"])
def test_continuous_deadline_stops_changing_observations_after_prior_ack(
    tmp_path, monkeypatch, changed_field,
):
    clock = [10.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    reg, key = registry(tmp_path / "worktrees")

    def changed(revision):
        value = report()
        if changed_field == "head":
            value["head_sha"] = revision
            value["observation"]["head_sha"] = revision
        else:
            value["observation"]["fingerprints"]["gates"] = revision
        return decode_snapshot(value, key.repo, key.number)

    reg.apply_snapshot(key, changed("A"))
    reg.subscriber(key, "pr-watch:owner").deadline = 11.0
    clock[0] = 10.5
    admitted = reg.apply_snapshot(key, changed("B"))[0]
    assert not admitted.timed_out
    reg.acknowledge(admitted)
    assert reg.watching_count(key) == 1
    clock[0] = 12.0
    outcomes = []
    for revision in ("C", "D", "E"):
        for event in reg.apply_snapshot(key, changed(revision)):
            outcomes.append(event.timed_out)
            reg.acknowledge(event)
    assert outcomes == [True]
    assert not reg.active_keys()


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("primed", [False, True])
def test_continuous_deadline_preserves_merged_and_closed_priority(
    tmp_path, monkeypatch, merged, primed,
):
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    reg, key = registry(tmp_path / "worktrees")
    if primed:
        reg.apply_snapshot(key, snapshot("a"))
    reg.subscriber(key, "pr-watch:owner").deadline = 10.0
    value = report(revision="b")
    value.update(pr_state="closed", merged=merged)
    event = reg.apply_snapshot(key, decode_snapshot(value, key.repo, key.number))[0]
    assert not event.timed_out
    assert ("merged" if merged else "closed") in event.transitions
    assert reg.watching_count(key) == 0
    reg.acknowledge(event)
    assert not reg.active_keys()


def test_continuous_expiry_preserves_pending_and_replays_final_timeout(
    tmp_path, monkeypatch,
):
    clock = [10.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    reg, key = registry(tmp_path / "worktrees", timeout=1.0)
    reg.apply_snapshot(key, snapshot("a"))
    pending = reg.apply_snapshot(key, snapshot("b"))[0]
    frozen = event_payload(pending)
    reg.apply_snapshot(key, snapshot("c"))
    clock[0] = 12.0
    final = reg.apply_snapshot(key, snapshot("d"))[0]
    assert final.timed_out
    assert final.transitions == ()
    assert event_payload(pending) == frozen
    assert reg.apply_snapshot(key, snapshot("e")) == ()
    restored = WatchRegistry()
    restored.restore_state(reg.snapshot_state(), strict=True)
    recovered = restored.pending_events()[0]
    assert event_payload(recovered) == frozen
    restored.acknowledge(recovered)
    timeout = restored.pending_events()[0]
    assert event_payload(timeout) == event_payload(final)
    assert restored.watching_count(key) == 0
    restored.acknowledge(timeout)
    assert not restored.active_keys()


def test_corrupt_coalesced_snapshot_fails_closed(tmp_path):
    reg, key = registry(tmp_path / "worktrees")
    reg.apply_snapshot(key, snapshot("a"))
    reg.apply_snapshot(key, snapshot("b"))
    reg.apply_snapshot(key, snapshot("c"))
    entries = reg.snapshot_state()
    entries[0]["queued"]["payload"]["snapshot"]["repo"] = "foreign/repo"
    with pytest.raises(ValueError, match="persisted"):
        WatchRegistry().restore_state(entries)


@pytest.mark.parametrize("corruption", ["source", "downgrade", "identity", "terminal"])
def test_native_stream_state_cannot_silently_downgrade_or_lose_successor(tmp_path, corruption):
    reg, key = registry(tmp_path / "worktrees")
    reg.apply_snapshot(key, snapshot("a"))
    reg.apply_snapshot(key, snapshot("b"))
    reg.apply_snapshot(key, snapshot("c"))
    entries = reg.snapshot_state()
    if corruption == "source":
        del entries[0]["worktrees_command"]
    elif corruption == "downgrade":
        entries[0]["continuous"] = False
    elif corruption == "identity":
        pending = entries[0]["pending"]
        entries[0]["queued"]["event_id"] = pending["event_id"]
        entries[0]["queued"]["payload"]["event_id"] = pending["event_id"]
    else:
        entries[0]["terminal_observed"] = True
    with pytest.raises(ValueError, match="persisted"):
        WatchRegistry().restore_state(entries)


def test_ready_for_human_is_observed_not_performed():
    before = report()
    after = report()
    before["observation"]["ready_for_human"] = False
    after["observation"]["ready_for_human"] = True
    transitions = compute_transitions(
        Baseline.from_snapshot(decode_snapshot(before, "project/repo", 7)),
        decode_snapshot(after, "project/repo", 7), ALL_TRANSITIONS,
    )
    assert set(transitions) == {"gates_changed", "ready_for_human"}


def test_reconfigured_ado_endpoint_cannot_replace_existing_target(tmp_path):
    command = tmp_path / "worktrees"
    command.touch()
    endpoint = ["https://dev.azure.com/first"]

    def fetch(*_):
        value = report()
        value["api_base"] = endpoint[0]
        return decode_snapshot(value, "project/repo", 7)

    daemon = WatchDaemon(fetch=lambda *_: snapshot(), native_fetch=fetch, active=False)
    try:
        daemon._ensure_poller = lambda *_: None
        payload = {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        }
        first = daemon._handle_register(payload)
        assert first["registered"]
        endpoint[0] = "https://dev.azure.com/second"
        assert "error" in daemon._handle_register({**payload, "subscriber_id": "another"})
        registrations = daemon.status()["registrations"]
        assert len(registrations) == 1
        assert registrations[0]["registration_id"] == first["registration_id"]
    finally:
        daemon.close()


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("initially_open", [False, True])
def test_native_terminal_without_retained_head_or_observation_retires_stream(
    tmp_path, merged, initially_open,
):
    command = tmp_path / "worktrees"
    command.touch()
    value = report()
    value.update(pr_state="closed", merged=merged, head_sha="", observation=None)
    terminal = decode_snapshot(value, "project/repo", 7)
    current = [snapshot() if initially_open else terminal]
    delivered = []
    daemon = WatchDaemon(
        fetch=lambda *_: pytest.fail("terminal observation fell back to legacy fetching"),
        native_fetch=lambda *_: current[0],
        notify=lambda event: (delivered.append(event_payload(event)), 0)[1],
        poll_interval=0.01, idle_exit=0.01,
    )
    try:
        response = daemon.compute("register", {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        })
        assert response["registered"]
        if initially_open:
            key = WatchKey("project/repo", 7, str(command))
            wait_until(lambda: daemon._registry.subscriber(key, "owner").baseline is not None)
            current[0] = terminal
        wait_until(lambda: bool(delivered) and not daemon.status()["subscribers"])
        assert len(delivered) == 1
        assert delivered[0]["transitions"] == ["merged" if merged else "closed"]
        assert delivered[0]["snapshot"]["observation"] is None
        assert delivered[0]["snapshot"]["head_sha"] == ""
        assert not daemon.status()["observation_errors"]
    finally:
        daemon.close()


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("continuous", [False, True])
def test_terminal_admission_is_durable_without_a_second_read_and_replays_until_ack(
    tmp_path, monkeypatch, merged, continuous,
):
    command = tmp_path / "worktrees"
    command.touch()
    value = report()
    value.update(pr_state="closed", merged=merged, head_sha="", observation=None)
    terminal = decode_snapshot(value, "project/repo", 7)
    reads, replayed = [], []

    def admission_only(*_):
        reads.append("read")
        if len(reads) != 1:
            raise RuntimeError("retained source is unavailable")
        return terminal

    first = WatchDaemon(
        fetch=lambda *_: pytest.fail("terminal admission used legacy fetch"),
        native_fetch=admission_only,
    )
    second = None
    try:
        monkeypatch.setattr(first, "_ensure_poller", lambda *_: None)
        monkeypatch.setattr(first._deliveries, "resume", lambda: None)
        transition = "merged" if merged else "closed"
        response = first.compute("register", {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": continuous,
            "until": ["merged", "closed"] if continuous else [transition],
            "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        })
        assert response["registered"]
        saved = read_subscriptions_state()
        assert saved[0]["pending"] is not None
        frozen = saved[0]["pending"]["payload"]
        assert frozen["transitions"] == [transition]
        assert frozen["registration_id"] == response["registration_id"]
        assert frozen["snapshot"]["observation"] is None
        restored = WatchRegistry()
        assert restored.restore_state(saved, strict=True) == 1
        assert event_payload(restored.pending_events()[0]) == frozen
        first.close()

        def acknowledge(event):
            replayed.append(event_payload(event))
            return 0

        second = WatchDaemon(
            fetch=lambda *_: pytest.fail("terminal replay used legacy fetch"),
            native_fetch=admission_only, notify=acknowledge, poll_interval=0.01,
        )
        wait_until(lambda: bool(replayed) and not second.status()["subscribers"])
        assert replayed == [frozen]
        assert reads == ["read"]
        assert not read_subscriptions_state()
    finally:
        if second is not None:
            second.close()
        first.close()


@pytest.mark.parametrize("merged", [False, True])
def test_repeated_continuous_terminal_admission_notifies_before_inflight_read_returns(
    tmp_path, merged,
):
    command = tmp_path / "worktrees"
    command.touch()
    value = report()
    value.update(pr_state="closed", merged=merged, head_sha="", observation=None)
    terminal = decode_snapshot(value, "project/repo", 7)
    old_started, release_old = threading.Event(), threading.Event()
    reads, delivered = [], []

    def fetch(*_):
        reads.append(len(reads) + 1)
        call = reads[-1]
        if call == 1:
            return snapshot()
        if call == 2:
            old_started.set()
            assert release_old.wait(10)
            raise RuntimeError("retained source is unavailable")
        if call == 3:
            return terminal
        raise RuntimeError("retained source is unavailable")

    def acknowledge(event):
        delivered.append(event_payload(event))
        return 0

    daemon = WatchDaemon(
        fetch=lambda *_: pytest.fail("native admission used legacy fetch"),
        native_fetch=fetch, notify=acknowledge, poll_interval=0.01,
    )
    try:
        payload = {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True,
            "until": ["merged", "closed"], "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        }
        first = daemon.compute("register", payload)
        assert first["registered"]
        assert old_started.wait(3)
        repeated = daemon.compute("register", payload)
        assert repeated["registration_id"] == first["registration_id"]
        wait_until(lambda: bool(delivered))
        assert not release_old.is_set()
        assert delivered[0]["registration_id"] == first["registration_id"]
        assert delivered[0]["transitions"] == ["merged" if merged else "closed"]
        assert reads == [1, 2, 3]
    finally:
        release_old.set()
        daemon.close()


@pytest.mark.parametrize("repeated", [False, True])
@pytest.mark.parametrize("committed", [False, True])
def test_terminal_admission_write_failure_preserves_registration_outcome(
    tmp_path, monkeypatch, repeated, committed,
):
    command = tmp_path / "worktrees"
    command.touch()
    current = [snapshot()]
    daemon = WatchDaemon(fetch=lambda *_: snapshot(), native_fetch=lambda *_: current[0])
    persist = daemon._persist
    try:
        monkeypatch.setattr(daemon, "_ensure_poller", lambda *_: None)
        monkeypatch.setattr(daemon._deliveries, "resume", lambda: None)
        payload = {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        }
        key = WatchKey("project/repo", 7, str(command))
        prior_event = None
        if repeated:
            assert daemon.compute("register", payload)["registered"]
            prior_event = daemon._registry.apply_snapshot(key, snapshot("b"))[0]
            daemon._persist()
        previous = daemon._registry.subscriber(key, "owner")
        saved_before = read_subscriptions_state()
        value = report()
        value.update(pr_state="closed", merged=True, head_sha="", observation=None)
        current[0] = decode_snapshot(value, key.repo, key.number)
        def fail(*args, **kwargs):
            if committed:
                persist(*args, **kwargs)
                raise StateCommitUncertain("controlled directory-sync failure")
            raise OSError("controlled state-write failure")

        monkeypatch.setattr(daemon, "_persist", fail)
        if not committed:
            with pytest.raises(OSError, match="state-write"):
                daemon.compute("register", payload)
            assert daemon._registry.subscriber(key, "owner") is previous
            assert read_subscriptions_state() == saved_before
            assert daemon._registry.snapshot_state() == saved_before
            if prior_event is not None:
                assert daemon._registry.is_current(prior_event)
            return
        response = daemon.compute("register", payload)
        assert response["ambiguous_registration"]
        assert not response.get("registered")
        saved = read_subscriptions_state()
        assert daemon._registry.snapshot_state() == saved
        assert saved[0]["registration_id"] == response["registration_id"]
        restored = WatchRegistry()
        restored.restore_state(saved, strict=True)
        if prior_event is not None:
            assert saved[0]["pending"]["payload"] == event_payload(prior_event)
            restored.acknowledge(restored.pending_events()[0])
        terminal = restored.pending_events()[0]
        assert terminal.transitions == ("merged",)
        assert event_payload(terminal)["snapshot"]["merged"] is True
        restored.acknowledge(terminal)
        assert not restored.active_keys()
    finally:
        monkeypatch.setattr(daemon, "_persist", persist)
        daemon.close()


@pytest.mark.parametrize("retry_delay", [0.0, 60.0])
def test_actual_scheduler_survives_continuous_ack_between_capture_and_launch(
    tmp_path, retry_delay,
):
    reg, key = registry(tmp_path / "worktrees")
    reg.apply_snapshot(key, snapshot("a"))
    first = reg.apply_snapshot(key, snapshot("b"))[0]
    first.subscriber.pending["next_attempt"] = time.time() + retry_delay
    captured, continue_scheduling, waiting, delivered = (
        threading.Event() for _ in range(4)
    )
    shutdown = threading.Event()
    failures = []
    notifications = []

    class AdmissionTransaction:
        def __init__(self):
            self.lock = threading.RLock()
            self.exits = 0

        def __enter__(self):
            self.lock.acquire()
            return self

        def __exit__(self, *_):
            self.exits += 1
            first_exit = self.exits == 1
            self.lock.release()
            if first_exit:
                captured.set()
                assert continue_scheduling.wait(3)

    class SchedulerChanged:
        def __init__(self):
            self.event = threading.Event()

        def clear(self):
            self.event.clear()

        def set(self):
            self.event.set()

        def wait(self, timeout=None):
            waiting.set()
            return self.event.wait(timeout)

    def notify(event):
        notifications.append(event_payload(event))
        delivered.set()
        return 0

    deliveries = Deliveries(reg, notify, lambda *_: None, AdmissionTransaction(), shutdown)
    deliveries.changed = SchedulerChanged()

    def schedule():
        try:
            deliveries._schedule()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=schedule)
    try:
        thread.start()
        assert captured.wait(3)
        reg.acknowledge(first)
        assert first.subscriber.pending is None
        continue_scheduling.set()
        assert waiting.wait(3), failures
        assert not failures
        assert thread.is_alive()
        reg.apply_snapshot(key, snapshot("c"))
        deliveries.changed.set()
        assert delivered.wait(3), failures
        assert notifications[0]["snapshot"]["observation"]["fingerprints"]["votes"] == "c"
    finally:
        continue_scheduling.set()
        shutdown.set()
        deliveries.changed.set()
        thread.join(3)
        deliveries.close(time.monotonic() + 3)
    assert not thread.is_alive()
    assert not failures


def test_successful_native_registration_persists_admission_baseline_before_first_poll(tmp_path):
    command = tmp_path / "worktrees"
    command.touch()
    daemon = WatchDaemon(fetch=lambda *_: snapshot(), native_fetch=lambda *_: snapshot("a"))
    try:
        daemon._ensure_poller = lambda *_: None
        response = daemon.compute("register", {
            "repo": "project/repo", "number": 7, "subscriber_id": "owner",
            "worktrees_command": str(command), "continuous": True,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        })
        assert response["registered"]
        saved = read_subscriptions_state()[0]
        assert saved["native_baseline"]["fingerprints"]["votes"] == "a"
        restored = WatchRegistry()
        restored.restore_state(read_subscriptions_state(), strict=True)
        key = WatchKey("project/repo", 7, str(command))
        fired = restored.apply_snapshot(key, snapshot("b"))
        assert len(fired) == 1
        assert {"review_changed", "votes_changed", "approvals_changed", "threads_changed",
                "policies_changed", "gates_changed"} <= set(fired[0].transitions)
    finally:
        daemon.close()


def test_inflight_native_poll_cannot_roll_new_subscriber_back_before_admission(tmp_path):
    command = tmp_path / "worktrees"
    command.touch()
    old_started, release_old, next_started, release_next = (
        threading.Event() for _ in range(4)
    )
    calls = []
    delivered = []

    def fetch(*_):
        calls.append(len(calls) + 1)
        call = calls[-1]
        if call == 2:
            old_started.set()
            assert release_old.wait(3)
            return snapshot("a")
        if call == 3:
            return snapshot("b")
        if call >= 4:
            next_started.set()
            assert release_next.wait(3)
            return snapshot("c")
        return snapshot("a")

    daemon = WatchDaemon(
        fetch=lambda *_: pytest.fail("native polling fell back to a legacy provider"),
        native_fetch=fetch, notify=lambda event: (delivered.append(event_payload(event)), 0)[1],
        poll_interval=0.01,
    )
    try:
        payload = {
            "repo": "project/repo", "number": 7, "subscriber_id": "early",
            "worktrees_command": str(command), "continuous": True,
            "until": list(ALL_TRANSITIONS), "notify": {"argv": ["callback"]},
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
        }
        assert daemon.compute("register", payload)["registered"]
        assert old_started.wait(3)
        assert daemon.compute("register", {**payload, "subscriber_id": "late"})["registered"]
        release_old.set()
        assert next_started.wait(3)
        key = WatchKey("project/repo", 7, str(command))
        late = daemon._registry.subscriber(key, "late")
        assert late.baseline.fingerprints["votes"] == "b"
        assert late.pending is None
        assert not any(event["subscriber_id"] == "late" for event in delivered)
        release_next.set()
        wait_until(lambda: any(event["subscriber_id"] == "late" for event in delivered))
        event = next(event for event in delivered if event["subscriber_id"] == "late")
        assert event["snapshot"]["observation"]["fingerprints"]["votes"] == "c"
    finally:
        release_old.set()
        release_next.set()
        daemon.close()


def test_native_subscribe_never_silently_downgrades_to_legacy_owner(tmp_path, monkeypatch, capsys):
    command = tmp_path / "worktrees"
    command.touch()
    requests = []

    def request(kind, payload):
        requests.append((kind, payload))
        return {"capabilities": [ACKNOWLEDGED_NOTIFICATIONS]}

    monkeypatch.setattr(cli, "_watch_request", request)
    assert cli.main([
        "watch", "subscribe", "--repo", "project/repo", "--number", "7",
        "--subscriber-id", "owner", "--worktrees-command", str(command),
        "--continuous", "--acknowledged-notifications",
        "--until", "merged", "--until", "closed", "--json",
        "--notify-argv", "callback",
    ]) == 1
    assert "native_snapshots" in json.loads(capsys.readouterr().out)["error"]
    assert [kind for kind, _ in requests] == ["health"]


def test_native_cli_accepts_option_looking_callback_arguments_over_stdin(
    tmp_path, monkeypatch, capsys,
):
    command = tmp_path / "worktrees"
    command.touch()
    callback = [sys.executable, "-m", "callback", "--option"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"argv": callback})))
    seen = []

    def request(kind, payload):
        seen.append((kind, payload))
        if kind == "health":
            return {"capabilities": [
                ACKNOWLEDGED_NOTIFICATIONS, NATIVE_SNAPSHOTS, CONTINUOUS_NOTIFICATIONS,
            ]}
        return {
            "registered": True, "registration_id": "generation",
            "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
            "snapshot_protocol": "agent-worktrees.pr-snapshot/v1", "continuous": True,
        }

    monkeypatch.setattr(cli, "_watch_request", request)
    assert cli.main([
        "watch", "subscribe", "--repo", "project/repo", "--number", "7",
        "--subscriber-id", "owner", "--worktrees-command", str(command), "--continuous",
        "--acknowledged-notifications", "--until", "merged", "--until", "closed",
        "--notify-stdin", "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["registered"]
    assert seen[1][1]["notify"] == {"argv": callback}
