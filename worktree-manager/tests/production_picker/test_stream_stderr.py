"""Real-child controls for separate, bounded registered-stream diagnostics."""

import sys
import threading
from types import SimpleNamespace

import pytest

from worktree_manager.production_picker.picker_tui import pivots, stream_stderr, tasks


pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.contract("worktree_manager.stream_stderr"),
]


_PROVIDER = r'''
import json
import subprocess
import sys
import time

kind, count = sys.argv[1], int(sys.argv[2])
behavior = sys.argv[3] if len(sys.argv) > 3 else "normal"
def diagnostics():
    sys.stderr.write("x" * count)
    sys.stderr.flush()
if behavior != "after" and not (behavior == "unsupported" and "--stream" not in sys.argv):
    diagnostics()
if behavior == "hang":
    time.sleep(30)
if behavior == "exit":
    sys.stderr.write("\nfixture diagnostic\n")
    sys.exit(7)
if behavior == "unsupported" and "--stream" in sys.argv:
    sys.stderr.write("\nerror: unrecognized arguments: --stream\n")
    sys.exit(2)
if behavior == "unsupported":
    print(json.dumps([{"id": "legacy"}]), flush=True)
    sys.exit(0)
if behavior == "descendant":
    subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time; from pathlib import Path\n"
         "for _ in range(3000):\n"
         " if Path(sys.argv[1]).exists(): break\n"
         " time.sleep(.01)\n", sys.argv[4]],
        stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
if kind == "list":
    print(json.dumps({"type": "row", "entry": {"id": "one"}}), flush=True)
else:
    print(json.dumps({"type": "progress", "pct": 50, "msg": "half"}), flush=True)
print(json.dumps({"type": "done", "message": "finished"}), flush=True)
if behavior == "after":
    diagnostics()
'''


def _runtime(tmp_path, kind, count, behavior="normal"):
    script = tmp_path / "provider.py"
    script.write_text(_PROVIDER, encoding="utf-8")
    argv = [sys.executable, str(script), kind, str(count), behavior]
    if behavior == "descendant":
        argv.append(str(tmp_path / "descendant-release"))
    pivot = pivots.parse_manifest(
        {"label": "Fixture", "list": argv, "entry": {"id": "id"}, "stream": True},
        name="fixture", source_path=str(script),
    )
    runtime = tasks.RegisteredPivotRuntime(pivot)
    children = []
    spawn = runtime._spawn_stream

    def captured(argv):
        process = spawn(argv)
        children.append(process)
        return process

    runtime._spawn_stream = captured
    runtime.fixture_children = children
    return runtime, argv


def _assert_settled(runtime):
    assert runtime._procs == []
    for process in runtime.fixture_children:
        assert process.poll() is not None
        drain = process._picker_stderr
        assert not drain.thread.is_alive()
        assert drain.stream.closed
        assert len(drain.output) <= stream_stderr.STDERR_LIMIT


@pytest.mark.parametrize("kind", ["list", "progress"])
@pytest.mark.parametrize("count", [11, 330_000])
def test_stderr_burst_cannot_block_valid_frames(tmp_path, monkeypatch, kind, count):
    runtime, argv = _runtime(tmp_path, kind, count)
    monkeypatch.setattr(tasks, "STREAM_TIMEOUT", 1.5)
    cancel = threading.Event()
    bound = threading.Timer(3, cancel.set)
    bound.start()
    try:
        if kind == "list":
            runtime._run_list_stream(None, {})
            state, rows, error = runtime.get(None)
            assert (state, rows, error) == ("ready", [{"id": "one"}], "")
        else:
            frames = []
            result = runtime.run_action_stream(
                SimpleNamespace(run=argv), {},
                lambda pct, message: frames.append((pct, message)),
                should_cancel=cancel.is_set,
            )
            assert result == (True, "finished")
            assert frames == [(50.0, "half")]
        _assert_settled(runtime)
    finally:
        bound.cancel()
        bound.join()
        runtime.close()


@pytest.mark.parametrize("kind", ["list", "progress"])
@pytest.mark.parametrize("behavior", ["normal", "after"])
def test_stderr_overflow_is_failure_even_after_terminal_stdout(tmp_path, kind, behavior):
    runtime, argv = _runtime(tmp_path, kind, stream_stderr.STDERR_LIMIT + 8192, behavior)
    try:
        if kind == "list":
            runtime._run_list_stream(None, {})
            state, _, message = runtime.get(None)
            assert state == "error"
        else:
            ok, message = runtime.run_action_stream(
                SimpleNamespace(run=argv), {}, lambda *_: None,
            )
            assert not ok
        assert "stderr output exceeded" in message
        _assert_settled(runtime)
    finally:
        runtime.close()


@pytest.mark.parametrize("failure", ["start", "read"])
@pytest.mark.parametrize("kind", ["list", "progress"])
def test_stderr_reader_failure_settles_real_child(tmp_path, monkeypatch, failure, kind):
    runtime, argv = _runtime(tmp_path, kind, 330_000)
    if failure == "start":
        start = threading.Thread.start

        def failed_start(thread):
            if thread.name == "registered-stream-stderr":
                raise RuntimeError("fixture reader start failure")
            return start(thread)

        monkeypatch.setattr(threading.Thread, "start", failed_start)
    else:
        start = stream_stderr.StreamStderr.start

        class BrokenPipe:
            def __init__(self, stream):
                self.stream = stream

            def read(self, _size):
                raise OSError("fixture reader read failure")

            def close(self):
                self.stream.close()

            @property
            def closed(self):
                return self.stream.closed

        def failed_read(drain):
            drain.stream = BrokenPipe(drain.stream)
            start(drain)

        monkeypatch.setattr(stream_stderr.StreamStderr, "start", failed_read)
    try:
        if kind == "list":
            runtime._run_list_stream(None, {})
            state, _, message = runtime.get(None)
            assert state == "error"
        else:
            ok, message = runtime.run_action_stream(
                SimpleNamespace(run=argv), {}, lambda *_: None,
            )
            assert not ok
        assert "stderr reader" in message and f"fixture reader {failure} failure" in message
        _assert_settled(runtime)
    finally:
        runtime.close()


def test_noisy_legacy_provider_preserves_unsupported_fallback(tmp_path):
    runtime, _ = _runtime(tmp_path, "list", 330_000, "unsupported")
    try:
        runtime._run_list_stream(None, {})
        assert runtime.get(None) == ("ready", [{"id": "legacy"}], "")
        _assert_settled(runtime)
    finally:
        runtime.close()


def test_nonzero_action_preserves_separate_stderr_diagnostic(tmp_path):
    runtime, argv = _runtime(tmp_path, "progress", 330_000, "exit")
    try:
        assert runtime.run_action_stream(
            SimpleNamespace(run=argv), {}, lambda *_: None,
        ) == (False, "fixture diagnostic")
        _assert_settled(runtime)
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", ["list", "progress"])
def test_silent_stdout_close_or_cancel_joins_stderr_reader(tmp_path, kind):
    runtime, argv = _runtime(tmp_path, kind, 330_000, "hang")
    cancel = threading.Event()
    bound = threading.Timer(.5, runtime.close if kind == "list" else cancel.set)
    bound.start()
    try:
        if kind == "list":
            runtime._run_list_stream(None, {})
        else:
            assert runtime.run_action_stream(
                SimpleNamespace(run=argv), {}, lambda *_: None,
                should_cancel=cancel.is_set,
            ) == (False, "cancelled")
        _assert_settled(runtime)
    finally:
        bound.cancel()
        bound.join()
        runtime.close()


def test_stderr_diagnostics_do_not_renew_stdout_publication_deadline(tmp_path, monkeypatch):
    runtime, _ = _runtime(tmp_path, "list", 330_000, "hang")
    monkeypatch.setattr(tasks, "STREAM_TIMEOUT", .5)
    try:
        runtime._run_list_stream(None, {})
        state, _, message = runtime.get(None)
        assert state == "error" and "no completed initial read" in message
        _assert_settled(runtime)
    finally:
        runtime.close()


def test_unsettled_reader_cannot_release_process_tracking():
    untracked = []
    drain = SimpleNamespace(
        error="fixture cleanup refused", finish=lambda: False,
        settle_lock=threading.Lock(), consumer_finished=threading.Event(),
    )
    process = SimpleNamespace(
        _picker_stderr=drain, returncode=0, communicate=lambda **_: ("", None),
    )
    runtime = SimpleNamespace(_untrack=untracked.append)
    assert stream_stderr.settle_stream(runtime, process) == ("", "fixture cleanup refused")
    assert not untracked


def test_close_preserves_failed_custody_and_retires_remaining_children(tmp_path, monkeypatch):
    runtime, _ = _runtime(tmp_path, "list", 0)
    refused = SimpleNamespace(
        poll=lambda: 0, _picker_stderr=SimpleNamespace(
            error="fixture cleanup refused", finish=lambda: False,
        ),
    )
    settled = SimpleNamespace(poll=lambda: 0)
    runtime._procs = [refused, settled]
    retired = []
    monkeypatch.setattr(tasks, "_kill_proc_tree", retired.append)
    with pytest.raises(RuntimeError, match="fixture cleanup refused"):
        runtime.close()
    assert retired == [refused, settled]
    assert runtime._procs == [refused]


def test_reader_construction_failure_retires_actual_spawn(tmp_path, monkeypatch):
    runtime, _ = _runtime(tmp_path, "list", 330_000, "hang")
    spawned = []
    spawn = tasks.spawn_sync_in_kill_on_close_job

    def captured(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        spawned.append(process)
        return process, job

    def failed(*args):
        raise RuntimeError("fixture reader construction failure")

    monkeypatch.setattr(tasks, "spawn_sync_in_kill_on_close_job", captured)
    monkeypatch.setattr(stream_stderr.StreamStderr, "__init__", failed)
    try:
        runtime._run_list_stream(None, {})
        assert runtime.get(None)[0] == "error"
        assert "fixture reader construction failure" in runtime.get(None)[2]
        assert len(spawned) == 1 and spawned[0].poll() is not None
        assert not runtime._procs
    finally:
        runtime.close()


def test_exited_provider_retains_stdout_reader_custody_until_actual_settlement(
    tmp_path, monkeypatch,
):
    runtime, argv = _runtime(tmp_path, "progress", 11, "descendant")
    spawn = runtime._spawn_stream
    communicate = []
    retire = tasks._kill_proc_tree

    def captured(argv):
        process = spawn(argv)
        original = process.communicate
        communicate.append(original)

        def short_communication(**_kwargs):
            process.wait(timeout=3)
            return original(timeout=.1)

        process.communicate = short_communication
        return process

    monkeypatch.setattr(runtime, "_spawn_stream", captured)
    monkeypatch.setattr(tasks, "_kill_proc_tree", lambda _process: None)
    try:
        ok, message = runtime.run_action_stream(
            SimpleNamespace(run=argv), {}, lambda *_: None,
        )
        process = runtime.fixture_children[0]
        assert not ok and "cleanup failed" in message
        assert process.returncode == 0 and not process.stdout.closed
        assert runtime._procs == [process]
        with pytest.raises(RuntimeError, match="stdout"):
            runtime.close()
        assert runtime._procs == [process]
        monkeypatch.setattr(tasks, "_kill_proc_tree", retire)
        process.communicate = communicate[0]
        (tmp_path / "descendant-release").touch()
        runtime.close()
        assert not runtime._procs and process.stdout.closed
        reader = getattr(process, "_stdout_thread", None)
        assert reader is None or not reader.is_alive()
    finally:
        (tmp_path / "descendant-release").touch()
        for index, process in enumerate(runtime.fixture_children):
            retire(process)
            communicate[index](timeout=3)
        monkeypatch.setattr(tasks, "_kill_proc_tree", retire)
        runtime.close()
    _assert_settled(runtime)
