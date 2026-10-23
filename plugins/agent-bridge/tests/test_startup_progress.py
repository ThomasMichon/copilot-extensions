import asyncio
import json
from types import SimpleNamespace

import pytest

from agent_procutil.streaming import communicate_progress
from agent_bridge import startup_progress as progress
from agent_bridge.connect import ConnectStage, ConnectTracker
from agent_bridge.events import EventLog


class Manager:
    def __init__(self):
        self.log = EventLog()
        self._sessions = {"original": SimpleNamespace(event_log=self.log)}

    def _resolve_ref(self, ref):
        return ref if ref in self._sessions else None

    @progress.observed
    async def resume(self, session_id, work):
        return await work()


@pytest.mark.asyncio
async def test_target_reports_real_waiting_and_incremental_nested_phases(monkeypatch):
    manager = Manager()
    monkeypatch.setattr(progress, "WAIT_INTERVAL", .01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def work():
        with progress.phase("plugin-sync"):
            entered.set()
            await release.wait()
        return "same-native-session"

    task = asyncio.create_task(manager.resume("original", work))
    await entered.wait()
    await asyncio.sleep(.03)
    before = manager.log.get_events(after=0)
    assert not task.done()
    assert any(item.data.get("phase") == "plugin-sync" for item in before)
    assert any(item.data.get("state") == "waiting" for item in before)
    release.set()
    assert await task == "same-native-session"
    events = manager.log.get_events(after=0)
    assert events[-1].data == {
        **events[-1].data, "phase": "ready", "state": "finished",
    }
    count = len(events)
    await asyncio.sleep(.02)
    assert len(manager.log.get_events(after=0)) == count


@pytest.mark.asyncio
async def test_lifecycle_lock_wait_is_observable_without_claiming_acquisition(monkeypatch):
    manager = Manager()
    monkeypatch.setattr(progress, "WAIT_INTERVAL", .01)
    lock = asyncio.Lock()
    await lock.acquire()

    async def work():
        async with progress.lifecycle_lock(lock):
            return 1

    task = asyncio.create_task(manager.resume("original", work))
    await asyncio.sleep(.03)
    events = manager.log.get_events(after=0)
    assert any(item.data.get("phase") == "lifecycle-lock" for item in events)
    assert not task.done()
    lock.release()
    assert await task == 1


@pytest.mark.asyncio
async def test_context_default_tracker_routes_existing_checkpoints_and_safe_child_progress():
    manager = Manager()

    async def work():
        tracker = ConnectTracker(session_id="original")
        tracker.started(ConnectStage.WORKTREE)
        progress.preparation_output(b"[12:00:00Z] launch/instruction-sync running [elapsed 1s]")
        progress.preparation_output(b"private-token-and-opaque-diagnostics")
        progress.preparation_output(b'{"launch/opaque_secret starting":true}', parse_phase=False)
        tracker.reached(ConnectStage.WORKTREE)

    await manager.resume("original", work)
    events = manager.log.get_events(after=0)
    encoded = json.dumps([item.data for item in events])
    assert any(item.event == "connect_checkpoint" for item in events)
    assert "launch/instruction-sync" in encoded
    assert "private-token" not in encoded
    assert "opaque_secret" not in encoded


@pytest.mark.asyncio
async def test_progress_failure_is_visible_without_replacing_lifecycle(caplog):
    def failed(*args):
        raise RuntimeError("private database detail")

    item = progress.StartupProgress(failed)
    item.update("worktree")
    item.update("worktree")
    assert caplog.text.count("Startup progress delivery failed: RuntimeError") == 1
    assert "private database detail" not in caplog.text


@pytest.mark.asyncio
async def test_stream_capture_forwards_before_exit_and_preserves_result_framing():
    stdout = asyncio.StreamReader()
    stderr = asyncio.StreamReader()
    updates = []
    exited = asyncio.Event()

    async def wait():
        await exited.wait()
        return 0

    process = SimpleNamespace(stdout=stdout, stderr=stderr, stdin=None, wait=wait)
    task = asyncio.create_task(communicate_progress(
        process, on_output=lambda name, chunk: updates.append((name, chunk)),
    ))
    stderr.feed_data(b"[launch] Git synchronization: starting\n")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert updates == [("stderr", b"[launch] Git synchronization: starting\n")]
    assert not task.done()
    stdout.feed_data(b'{"worktree":"original"}\n')
    stdout.feed_eof()
    stderr.feed_eof()
    exited.set()
    out, err = await task
    assert json.loads(out) == {"worktree": "original"}
    assert err == b"[launch] Git synchronization: starting\n"


@pytest.mark.asyncio
async def test_stream_capture_preserves_exact_input_while_draining_both_outputs():
    received = bytearray()
    closed = []

    class Writer:
        def write(self, data):
            received.extend(data)

        async def drain(self):
            await asyncio.sleep(0)

        def close(self):
            closed.append(True)

    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_data(b'{"ready":true}\n')
    stderr.feed_data(b"phase update\n")
    stdout.feed_eof()
    stderr.feed_eof()

    async def wait():
        return 0

    process = SimpleNamespace(stdout=stdout, stderr=stderr, stdin=Writer(), wait=wait)
    prompt = ("line one\nline two-\u03b1\n" * 4096).encode()
    result = await communicate_progress(process, input_bytes=prompt, on_output=lambda *args: None)
    assert bytes(received) == prompt
    assert closed == [True]
    assert result == (b'{"ready":true}\n', b"phase update\n")


@pytest.mark.asyncio
async def test_invalid_input_pipe_is_refused_before_starting_readers():
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    updates = []
    process = SimpleNamespace(stdout=stdout, stderr=stderr, stdin=None)
    with pytest.raises(ValueError, match="stdin pipe"):
        await communicate_progress(
            process, input_bytes=b"unsubmitted", on_output=lambda *args: updates.append(args),
        )
    assert updates == []
