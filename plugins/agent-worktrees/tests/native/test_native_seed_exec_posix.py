"""Real exec replacement and finite receipts; not production consumer activation."""

from __future__ import annotations

import json
import os
import signal
import sys

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import config as cfg, launch_execution as audit
from agent_worktrees import launch_seed_exec as legacy, launch_seed_native as native
from agent_worktrees import launch_seed_state as seeds, tracking
from agent_worktrees import launch_seed_posix
from agent_worktrees.sessions import LiveVerdict

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native POSIX fork/exec contract")


@pytest.mark.parametrize("fault", ["truncated", "invalid-presence"])
def test_backend_environment_pipe_refuses_invalid_frames_and_closes_its_descriptor(fault):
    fields = [
        b"agent-worktrees.backend-environment.v1",
        b"x", b"literal ' \n", b"", b"", b"x", b"", b"x", b"mac launcher",
        b"complete", b"",
    ]
    if fault == "truncated":
        fields.pop()
    else:
        fields[3] = b"unexpected"
    reader, writer = os.pipe()
    try:
        os.write(writer, b"\0".join(fields))
    finally:
        os.close(writer)
    with pytest.raises(ValueError):
        launch_seed_posix._backend_environment(reader)
    with pytest.raises(OSError):
        os.fstat(reader)


@pytest.mark.parametrize("name", [
    name for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ") if hasattr(signal, name)
])
def test_non_python_backend_receives_default_spawn_signals(record, tmp_path, native_bash, name):
    signum = getattr(signal, name)
    _seed, _prepared, _correlation, _assets, result = _run(
        record, tmp_path, native_bash, ["/bin/sh", "-c", f"kill -{name[3:]} $$; exit 42"],
    )
    assert result.returncode == 128 + signum, result.stderr


@pytest.fixture
def record(tmp_path, monkeypatch):
    project = cfg.project_dir("demo")
    project.mkdir(parents=True, exist_ok=True)
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "test",
        "repos": {"demo": {"anchor": str(tmp_path), "default_branch": "dev"}},
    }), encoding="utf-8")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    path = tracking.create_new_record(
        "wt-exec", "worktree/wt-exec", str(checkout), "demo", "test",
        cfg.detect_platform(), tmp_path,
    ).yaml_path
    monkeypatch.setattr(legacy.sessions, "verify_worktree_active", lambda *a: LiveVerdict())
    return path


BACKEND = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
from agent_worktrees import locks
root = Path(sys.argv[1])
deadline = time.monotonic() + 5
while not (root / ".native-seed-exec.complete").read_text() and time.monotonic() < deadline:
    time.sleep(0.01)
if sys.platform == "linux":
    children = Path("/proc/self/task/" + str(os.getpid()) + "/children").read_text()
else:
    probe = subprocess.Popen(
        ["/bin/ps", "-axo", "pid=,ppid="], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    output, error = probe.communicate(timeout=2)
    assert probe.returncode == 0, error
    children = " ".join(
        pid for pid, parent in (line.split() for line in output.decode().splitlines())
        if int(parent) == os.getpid() and int(pid) != probe.pid
    )
closed = []
for descriptor in json.loads((root / "pipe.json").read_text()):
    try:
        os.fstat(descriptor)
        closed.append(False)
    except OSError:
        closed.append(True)
(root / "backend.json").write_text(json.dumps({
    "pid": os.getpid(), "birth": locks.process_start_time(os.getpid()),
    "argv": sys.argv[3:], "stdin": sys.stdin.read(),
    "children": children, "closed_pipe": closed,
}), encoding="utf-8")
print("backend stdout", flush=True)
print("backend stderr", file=sys.stderr, flush=True)
code = int(sys.argv[2])
if code < 0:
    os.kill(os.getpid(), -code)
raise SystemExit(code)
'''


def _run(record, tmp_path, native_bash, command, *, fault=None):
    seed = seeds.stage(record, kind="new", text="literal prompt ' \" \u03a9\n")
    prepared = native.prepare(record, command, seed_id=seed.seed_id)
    correlation = audit.Correlation(
        prepared["attempt_id"], prepared["resolution_id"], prepared["parent_id"],
    )
    assets = record.parent / "launch-attempts" / prepared["attempt_id"] / "exec.shell"
    assets.mkdir()
    completion = assets / ".native-seed-exec.complete"
    completion.touch(mode=0o600)
    request = tmp_path / "exec-request.json"
    request.write_text(json.dumps({
        "record": str(record), "attempt": prepared["attempt_id"],
        "resolution": prepared["resolution_id"], "parent": prepared["parent_id"],
        "operation": prepared["operation_id"], "command": prepared["argv"],
        "completion": str(completion), "assets": str(assets),
        "fault": fault,
    }), encoding="utf-8")
    driver = tmp_path / "exec-driver.py"
    driver.write_text('''
import json, os, signal, sys, time
from pathlib import Path
from agent_worktrees import locks
from agent_worktrees.launch_execution import Correlation
from agent_worktrees import launch_seed_posix
r = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
Path(r["assets"], "before.json").write_text(json.dumps({
    "pid": os.getpid(), "birth": locks.process_start_time(os.getpid()),
}))
inherited = []
if r["fault"] == "inherited-fd":
    import fcntl, resource
    descriptor = os.open(os.devnull, os.O_RDONLY)
    inherited = [fcntl.fcntl(descriptor, fcntl.F_DUPFD, 200)]
    os.set_inheritable(inherited[0], True)
    os.close(descriptor)
    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
original_pipe = launch_seed_posix.os.pipe
def observed_pipe():
    descriptors = original_pipe()
    Path(r["assets"], "pipe.json").write_text(json.dumps([*descriptors, *inherited]))
    return descriptors
launch_seed_posix.os.pipe = observed_pipe
original_signals = {
    name: signal.getsignal(getattr(signal, name))
    for name in ("SIGINT", "SIGPIPE", "SIGXFZ", "SIGXFSZ") if hasattr(signal, name)
}
if r["fault"] in ("interrupt", "encoding", "interrupt-success"):
    original_exec = launch_seed_posix.os.execvpe
    def failed_exec(*args):
        if r["fault"] in ("interrupt", "interrupt-success"):
            signal.raise_signal(signal.SIGINT)
        if r["fault"] == "interrupt-success":
            return original_exec(*args)
        raise UnicodeError("fixture encoding refusal")
    launch_seed_posix.os.execvpe = failed_exec
elif r["fault"] == "observer-timeout":
    def stalled_receipt(*args, **kwargs):
        time.sleep(30)
    launch_seed_posix.native.spawned = stalled_receipt
elif r["fault"] == "restore-interrupt":
    original_signal = signal.signal
    interrupted_restore = False
    def interrupt_restore(signum, handler):
        global interrupted_restore
        result = original_signal(signum, handler)
        if signum == signal.SIGPIPE and handler == signal.SIG_IGN and not interrupted_restore:
            interrupted_restore = True
            signal.raise_signal(signal.SIGINT)
        return result
    signal.signal = interrupt_restore
try:
    code = launch_seed_posix.execute(
        Path(r["record"]), Correlation(r["attempt"], r["resolution"], r["parent"]),
        r["operation"], r["command"], Path(r["completion"]),
    )
except (KeyboardInterrupt, UnicodeError):
    deadline = time.monotonic() + 12
    while not Path(r["completion"]).read_text() and time.monotonic() < deadline:
        time.sleep(0.01)
    code = 3
Path(r["assets"], "signals-restored").write_text(str(all(
    signal.getsignal(getattr(signal, name)) == handler
    for name, handler in original_signals.items()
)))
raise SystemExit(code)
''', encoding="utf-8")
    environment = dict(os.environ)
    if prepared["environment"] is not None:
        environment = prepared["environment"]
    result = run_caller_owned(
        [native_bash, "--noprofile", "--norc", "-c",
         'trap : INT; "$@"; code=$?; '
         'for ((i=0;i<1100;i++)); do [[ -s "$AW_EXEC_COMPLETION" ]] && break; sleep 0.01; done; '
         'exit "$code"',
         "owner", sys.executable, "-I", "-B", str(driver), str(request)],
        env={**environment, "AW_EXEC_COMPLETION": str(completion)},
        input_text="retained stdin\n", timeout=20,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    return seed, prepared, correlation, assets, result


@pytest.mark.parametrize("exit_code, fault", [
    (0, None), (7, None), (-signal.SIGTERM, None), (7, "inherited-fd"),
])
def test_foreground_exec_keeps_pid_birth_streams_and_actual_exit(
    record, tmp_path, native_bash, exit_code, fault,
):
    # The backend finds its launch-owned completion file without receiving
    # shell source or having a Python lifetime waiter above it.
    assets_parent = record.parent / "launch-attempts"
    command = [
        sys.executable, "-c",
        BACKEND.replace(
            'root = Path(sys.argv[1])',
            'root = next(Path(sys.argv[1]).glob("*/*/.native-seed-exec.complete")).parent',
        ),
        str(assets_parent), str(exit_code), "", "two words", "literal;$()",
    ]
    seed, prepared, correlation, assets, result = _run(record, tmp_path, native_bash, command, fault=fault)
    assert result.returncode == (128 - exit_code if exit_code < 0 else exit_code), result.stderr
    assert (assets / ".native-seed-exec.complete").read_text() == "0\n"
    before = json.loads((assets / "before.json").read_text())
    backend = json.loads((assets / "backend.json").read_text())
    assert {"pid": backend["pid"], "birth": backend["birth"]} == before
    assert backend["stdin"] == "retained stdin\n"
    assert backend["argv"] == ["", "two words", "literal;$()", "--interactive", seed.text]
    assert backend["children"] == ""
    assert backend["closed_pipe"] == ([True, True, True] if fault == "inherited-fd" else [True, True])
    assert result.stdout == "backend stdout\n"
    assert "backend stderr\n" in result.stderr
    assert seeds.peek(record) is None
    native.returned(record, correlation, prepared["operation_id"], exit_code=result.returncode)
    events = audit.read(record.parent, prepared["attempt_id"], prepared["operation_id"])["events"]
    assert [event["phase"] for event in events] == [
        "execution-requested", "seed-claimed", "process-spawned", "execution-returned",
    ]
    assert events[2]["details"]["pid"] == backend["pid"]
    assert events[-1]["details"]["process_exit_code"] == result.returncode


@pytest.mark.parametrize("failure", ["missing", "not-executable"])
def test_definite_exec_failure_restores_without_backend_spawn(
    record, tmp_path, native_bash, failure,
):
    executable = tmp_path / failure
    if failure == "not-executable":
        executable.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        executable.chmod(0o600)
    seed, prepared, _correlation, assets, result = _run(
        record, tmp_path, native_bash, [str(executable)],
    )
    assert result.returncode == 3, result.stderr
    assert (assets / ".native-seed-exec.complete").read_text() == "3\n"
    assert (assets / "signals-restored").read_text() == "True"
    restored = seeds.peek(record)
    assert restored.seed_id == seed.seed_id and restored.handoff_id is None
    events = audit.read(record.parent, prepared["attempt_id"], prepared["operation_id"])["events"]
    assert [event["phase"] for event in events] == [
        "execution-requested", "seed-claimed", "execution-returned",
    ]


def test_fast_success_is_spawned_or_unknown_never_restored(record, tmp_path, native_bash):
    seed, prepared, correlation, assets, result = _run(
        record, tmp_path, native_bash, [sys.executable, "-c", "raise SystemExit(0)"],
    )
    assert result.returncode == 0, result.stderr
    status = (assets / ".native-seed-exec.complete").read_text()
    events = audit.read(record.parent, prepared["attempt_id"], prepared["operation_id"])["events"]
    if status == "0\n":
        assert seeds.peek(record) is None
        native.returned(record, correlation, prepared["operation_id"], exit_code=0)
    else:
        assert status == "3\n"
        assert events[-1]["phase"] == "unknown"
        retained = seeds.peek(record)
        assert retained.seed_id == seed.seed_id and retained.handoff_id
    assert not any(
        event["phase"] == "execution-returned" and event["details"].get("exit_code") == 3
        for event in events
    )


@pytest.mark.parametrize("fault", ["interrupt", "encoding", "interrupt-success"])
def test_catchable_pre_exec_loss_cannot_finish_or_restore(
    record, tmp_path, native_bash, fault,
):
    seed, prepared, _correlation, assets, result = _run(
        record, tmp_path, native_bash, [sys.executable, "-c", "raise SystemExit(99)"], fault=fault,
    )
    assert result.returncode == 3, result.stderr
    assert (assets / ".native-seed-exec.complete").read_text() == "3\n"
    assert (assets / "signals-restored").read_text() == "True"
    events = audit.read(record.parent, prepared["attempt_id"], prepared["operation_id"])["events"]
    assert [event["phase"] for event in events] == [
        "execution-requested", "seed-claimed", "unknown",
    ]
    retained = seeds.peek(record)
    assert retained.seed_id == seed.seed_id and retained.handoff_id


def test_observer_timeout_is_finite_unknown_not_a_lifetime_waiter(record, tmp_path, native_bash):
    seed, prepared, _correlation, assets, result = _run(
        record, tmp_path, native_bash,
        [sys.executable, "-c", "import time; time.sleep(0.1)"], fault="observer-timeout",
    )
    assert result.returncode == 0, result.stderr
    assert (assets / ".native-seed-exec.complete").read_text() == "3\n"
    events = audit.read(record.parent, prepared["attempt_id"], prepared["operation_id"])["events"]
    assert events[-1]["phase"] == "unknown"
    retained = seeds.peek(record)
    assert retained.seed_id == seed.seed_id and retained.handoff_id


def test_sigint_during_failed_exec_cleanup_cannot_finish_seed(record, tmp_path, native_bash):
    seed, prepared, _correlation, assets, result = _run(
        record, tmp_path, native_bash, [str(tmp_path / "absent-backend")],
        fault="restore-interrupt",
    )
    assert result.returncode == 3, result.stderr
    assert (assets / ".native-seed-exec.complete").read_text() == "3\n"
    assert (assets / "signals-restored").read_text() == "True"
    restored = seeds.peek(record)
    assert restored.seed_id == seed.seed_id and restored.handoff_id is None
    events = audit.read(record.parent, prepared["attempt_id"], prepared["operation_id"])["events"]
    assert [event["phase"] for event in events] == [
        "execution-requested", "seed-claimed", "execution-returned",
    ]
