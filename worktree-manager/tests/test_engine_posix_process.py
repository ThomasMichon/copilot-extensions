"""Group lifetime remains owned after engine success and inner capture failure."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from worktree_manager import engine_posix_process as pp
from worktree_manager import engine_process, mux_companion, agenda_transport
from worktree_manager import companion_client, engine_client
from worktree_manager import picker_review
from threading import Event
from worktree_manager.companion_timeline_attachment import TimelineAttachment
from worktree_manager.companion_timeline_controller import TimelineCleanupError, TimelineController
from worktree_manager.companion_timeline_contract import HistoryPage, Completeness, Provider
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.engine_process import EngineCleanupError
from test_companion_timeline_widget import TimelineApp
from test_companion_timeline_controller import Source, token, position, snapshot
from test_companion_worktree_source import configured as configured
from test_companion_agenda_live import current_data, until
from test_agenda_posix_process import provider as provider
from test_agenda_contract import publication
from contextlib import contextmanager
import asyncio
from dataclasses import replace


def test_external_progress_destination_is_not_rewound_or_recaptured(tmp_path, monkeypatch):
    owner = SimpleNamespace(
        pid=123, stdout=io.BytesIO(b'{"returncode":0}\n'), communicate=lambda **kw: None,
    )
    monkeypatch.setattr(pp.select, "select", lambda *args: ([owner.stdout], [], []))
    monkeypatch.setattr(pp.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(pp.os, "killpg", lambda *args: None, raising=False)

    def spawn(argv, **kwargs):
        os.write(int(argv[-1]), b'{"type":"progress"}\n')
        return owner

    monkeypatch.setattr(pp.subprocess, "Popen", spawn)
    with (tmp_path / "progress").open("w+b") as output:
        result = pp.run(["engine"], timeout=1, env={}, stdout=output, stderr=subprocess.DEVNULL)
        assert result.returncode == 0 and result.stdout is None
        assert output.tell() == len(b'{"type":"progress"}\n')
        output.seek(0)
        assert output.read() == b'{"type":"progress"}\n'


def test_failed_group_retirement_retains_the_exact_unreaped_owner(monkeypatch):
    owner = SimpleNamespace(pid=123, stdout=io.BytesIO(b'{"returncode":0}\n'))
    monkeypatch.setattr(pp.subprocess, "Popen", lambda *a, **k: owner)
    monkeypatch.setattr(pp.select, "select", lambda *args: ([owner.stdout], [], []))
    monkeypatch.setattr(pp.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(pp.os, "killpg",
                        lambda *args: (_ for _ in ()).throw(OSError("fixture retirement failed")),
                        raising=False)
    with pytest.raises(EngineCleanupError) as caught:
        pp.run(["engine"], timeout=1, env={}, stderr=subprocess.DEVNULL)
    assert caught.value.process is owner and caught.value.group_id == owner.pid


@pytest.mark.parametrize("outcome", ["success", "timeout", "interrupt", "invalid", "closed", "launch-failed"])
def test_group_owner_is_not_reaped_before_descendant_sweep(monkeypatch, outcome):
    events = []
    owner = SimpleNamespace(
        pid=123,
        stdout=io.StringIO(
            '{"error":"engine executable missing"}\n' if outcome == "launch-failed" else
            '{"returncode":0}\n' if outcome not in ("invalid", "closed")
            else '{"returncode":true}\n' if outcome == "invalid" else ""
        ),
        poll=lambda: pytest.fail("reaped the group owner before cleanup"),
    )
    owner.communicate = lambda **kwargs: events.append(("reap", kwargs["timeout"]))

    def spawn(argv, **kwargs):
        events.append(("spawn", kwargs))
        os.write(int(argv[-1]), b'{"action":"exec"}')
        return owner

    def ready(*args):
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return ([] if outcome == "timeout" else [owner.stdout]), [], []

    monkeypatch.setattr(pp.subprocess, "Popen", spawn)
    monkeypatch.setattr(pp.select, "select", ready)
    monkeypatch.setattr(pp.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(pp.os, "killpg", lambda pid, sig: events.append(("sweep", pid, sig)), raising=False)
    if outcome == "success":
        result = pp.run(["engine"], timeout=1, text=True, env={}, stderr=subprocess.DEVNULL)
        assert result.returncode == 0
        assert result.stdout == '{"action":"exec"}'
    else:
        error = (
            subprocess.TimeoutExpired if outcome == "timeout" else
            KeyboardInterrupt if outcome == "interrupt" else OSError
        )
        with pytest.raises(error):
            pp.run(["engine"], timeout=1, text=True, env={}, stderr=subprocess.DEVNULL)
    assert events[0][1]["start_new_session"] is True
    assert events[0][1]["stdin"] is subprocess.PIPE
    assert events[-2:] == [("sweep", 123, 9), ("reap", 5)]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX stderr capture")
def test_real_stderr_capture_does_not_fill_a_pipe():
    detail = "owner-diagnostic\n" * 20000
    result = pp.run(
        [sys.executable, "-c", "import sys;sys.stderr.write('owner-diagnostic\\n'*20000);sys.exit(2)"],
        timeout=10, text=True, env=dict(os.environ), stderr=subprocess.PIPE,
    )
    assert result.returncode == 2
    assert result.stderr == detail


@pytest.mark.skipif(os.name == "nt", reason="native POSIX group lifetime")
def test_real_spawn_failure_preserves_the_engine_error(tmp_path):
    with pytest.raises(OSError, match="No such file"):
        pp.run(
            [str(tmp_path / "missing-engine")], timeout=10, text=True,
            env=dict(os.environ), stderr=subprocess.DEVNULL,
        )


@pytest.mark.skipif(os.name == "nt", reason="native POSIX group lifetime")
def test_real_degraded_capture_leaves_no_live_grandchild(tmp_path):
    ready = tmp_path / "ready"
    evidence = tmp_path / "alive"
    grandchild = (
        "import time\nfrom pathlib import Path\n"
        f"p=Path({str(evidence)!r})\n"
        "while True:\n    p.write_text(str(time.monotonic()))\n    time.sleep(.05)\n"
    )
    renderer = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}])\n"
        "time.sleep(60)\n"
    )
    root = Path(__file__).resolve().parents[2]
    engine = (
        "import importlib.util,json,os,sys,time\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(root / 'libs' / 'agent-procutil' / 'src')!r})\n"
        f"s=importlib.util.spec_from_file_location('push_timeout',"
        f"{str(root / 'plugins' / 'agent-worktrees' / 'src' / 'agent_worktrees' / 'push_timeout.py')!r})\n"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
        "import agent_procutil\nspawn=agent_procutil.spawn_sync_in_kill_on_close_job\n"
        "def failing_spawn(*args,**kwargs):\n"
        "    child,job=spawn(*args,**kwargs)\n"
        "    def failed_capture(**kwargs):\n"
        "        deadline=time.monotonic()+10\n"
        f"        while not Path({str(evidence)!r}).exists():\n"
        "            if time.monotonic()>deadline: raise RuntimeError('grandchild never started')\n"
        "            time.sleep(.05)\n"
        "        raise OSError('injected renderer capture failure')\n"
        "    child.communicate=failed_capture\n"
        "    return child,job\n"
        "agent_procutil.spawn_sync_in_kill_on_close_job=failing_spawn\n"
        "try:\n"
        f"    m.run_bounded([sys.executable,'-c',{renderer!r}],cwd=None,env=dict(os.environ),"
        "timeout=None,inherit_process_group=True)\n"
        "except OSError:\n"
        f"    Path({str(ready)!r}).write_text('degraded')\n"
        "    print(json.dumps({'action':'exec'}))\n"
    )
    result = pp.run(
        [sys.executable, "-c", engine], timeout=20, text=True,
        env=dict(os.environ), stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"action": "exec"}
    assert ready.read_text() == "degraded"
    assert evidence.exists()
    stopped = evidence.read_text()
    time.sleep(0.3)
    assert evidence.read_text() == stopped


@pytest.mark.skipif(os.name == "nt", reason="actual reserved POSIX group owner")
@pytest.mark.parametrize("failure", ["read-timeout", "sweep-error", "retirement-timeout"])
def test_actual_posix_probe_distinguishes_read_and_retirement_failures(configured, monkeypatch, failure):
    root, _calls = configured
    owners = []
    actual_spawn, actual_kill, actual_read = pp.subprocess.Popen, pp.os.killpg, pp.read_completion_receipt
    armed = [True]

    def spawn(*args, **kwargs):
        process = actual_spawn(*args, **kwargs)
        if kwargs.get("start_new_session") and kwargs.get("pass_fds"):
            communicate = process.communicate
            owners.append((process, communicate))
            if armed[0] and failure == "retirement-timeout":
                def failed(**options):
                    raise subprocess.TimeoutExpired("owned retirement", options["timeout"])
                process.communicate = failed
        return process

    def sweep(pid, sig):
        if armed[0] and failure == "sweep-error":
            raise OSError("private owned-group retirement refused")
        return actual_kill(pid, sig)

    def read(*args, **kwargs):
        if armed[0] and failure == "read-timeout":
            raise subprocess.TimeoutExpired("owned read", kwargs["timeout"])
        return actual_read(*args, **kwargs)

    monkeypatch.setattr(engine_process, "contained_test_mode", lambda: False)
    monkeypatch.setattr(pp.subprocess, "Popen", spawn)
    monkeypatch.setattr(pp.os, "killpg", sweep)
    monkeypatch.setattr(pp, "read_completion_receipt", read)

    async def check():
        app = TimelineApp(TimelineWindow())
        async with app.run_test(size=(100, 36)) as pilot:
            attachment = TimelineAttachment(app.query_one("CompanionTimelinePanel"), cwd=str(root))
            try:
                await attachment.set_selection(("example", "selected", "head-a"))
                if failure == "read-timeout":
                    assert attachment._cleanup_error is None
                    armed[0] = False
                    await attachment.set_selection(("example", "selected", "head-a"))
                    await until(pilot, lambda: attachment.controller is not None
                                and attachment.controller.reader_count == 1)
                    await attachment.close()
                else:
                    assert attachment._cleanup_error is not None, "POSIX retirement was misclassified as a read failure"
                    count = len(owners)
                    armed[0] = False
                    await attachment.set_selection(("example", "selected", "head-a"))
                    assert len(owners) == count
                    with pytest.raises(TimelineCleanupError):
                        await attachment.close()
            finally:
                armed[0] = False
                for process, communicate in owners:
                    process.communicate = communicate
                    try:
                        actual_kill(process.pid, pp.signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    communicate(timeout=5)

    asyncio.run(check())


def test_legacy_agenda_cleanup_is_terminal_without_killing_companion(provider, monkeypatch, tmp_path):
    from textual.widgets import TabbedContent

    provider(f"print({json.dumps(publication())!r},flush=True)")
    monkeypatch.setattr(mux_companion, "_load_current_worktree", lambda *args, **kwargs: current_data())
    monkeypatch.setattr(mux_companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    actual_owned = agenda_transport._owned_process
    leases = []
    launches = []
    path = tmp_path / "legacy-agenda-resource"
    path.write_bytes(b"private descriptor lease")

    @contextmanager
    def owned(argv, cwd):
        lease = path.open("rb")
        leases.append(lease)
        launches.append(argv)
        try:
            with actual_owned(argv, cwd) as handles:
                yield handles
        finally:
            if not lease.closed:
                raise EngineCleanupError("legacy agenda descriptor did not retire")

    monkeypatch.setattr(agenda_transport, "_owned_process", owned)

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(tmp_path))
        try:
            async with app.run_test(size=(100, 36)) as pilot:
                await until(pilot, lambda: app._agenda_worker is not None)
                await app._agenda_worker.wait()
                assert app.is_running
                assert "retire" in (app._data.agenda_error or "").casefold()
                assert len(launches) == 1 and not leases[0].closed
                assert app._timeline_attachment._cleanup_error is None
                assert len(app._agenda_cleanup_failures) == 1 and not app._engine_cleanup_failures
                assert await pilot.click("#refresh-btn")
                await until(pilot, lambda: not app._loading)
                assert len(launches) == 1
                views = app.query_one("#views", TabbedContent)
                views.active = "claims-tab"
                await pilot.pause()
                assert views.active == "claims-tab" and app.is_running
                assert app._timeline_attachment._cleanup_error is None
                await pilot.press("escape")
        finally:
            for lease in leases:
                lease.close()

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["read-timeout", "cleanup"])
def test_base_engine_api_preserves_terminal_type_and_fences_read_scope(monkeypatch, tmp_path, kind):
    calls = []
    cleanup = EngineCleanupError("unrecognized arguments: --current --classify")

    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            if kind == "cleanup":
                raise cleanup
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, '{"worktrees":[]}', "")

    monkeypatch.setattr(engine_process, "run", run)
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: [sys.executable, "-m", "agent_worktrees"])
    failures = []
    runner = companion_client.cancellable_read_runner(Event(), cwd=str(tmp_path), cleanup_failures=failures)
    if kind == "cleanup":
        with pytest.raises(EngineCleanupError) as failed:
            engine_client.list_worktree_rows("example", runner=runner)
        assert isinstance(failed.value, engine_client.EngineError)
        assert failures == [cleanup] and len(calls) == 1
        with pytest.raises(EngineCleanupError):
            engine_client.orphaned_obligations("example", runner=runner)
        assert len(calls) == 1, "a terminal scope spawned another read or compatibility retry"
    else:
        with pytest.raises(engine_client.EngineError) as failed:
            engine_client.list_worktree_rows("example", runner=runner)
        assert not isinstance(failed.value, EngineCleanupError) and failures == []
        engine_client.run_json("example", ["probe", "--json"], runner=runner)
        assert len(calls) == 2


def test_review_reader_retains_terminal_cleanup_without_relaxing_read_only_policy(monkeypatch, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise EngineCleanupError("private review reader did not retire")

    monkeypatch.setattr(engine_process, "run", run)
    review = picker_review.Review(tmp_path, tmp_path, "synthetic-candidate", (sys.executable,))
    monkeypatch.setattr(picker_review, "_review", review)
    for _ in range(2):
        with pytest.raises(EngineCleanupError, match="review reader"):
            picker_review.run_read([sys.executable, "-c", "pass"], timeout=1, env={})
    assert len(calls) == 1
    with pytest.raises(picker_review.ReadOnlyError):
        picker_review.require_writable()
    review.reads.close()


@pytest.mark.parametrize("failure_kind", ["page", "reader"])
@pytest.mark.parametrize("retirement", ["select", "close"])
def test_terminal_failure_survives_second_read_and_task_slot_replacement(failure_kind, retirement):
    async def run():
        result = asyncio.get_running_loop().create_future()
        delivered, errors = [], []
        changed = asyncio.Event()
        selected, other = token(), token(Provider.BRIDGE)

        def second_request():
            assert not errors, "the done callback ran before the race witness"
            try:
                accepted = controller.request_history(selected, position(selected))
            except TimelineCleanupError as failure:
                result.set_result(failure)
            else:
                result.set_result(accepted)

        class FailedSource(Source):
            async def observe(self, requested, *, stop):
                if failure_kind == "reader":
                    yield snapshot(requested)
                    asyncio.get_running_loop().call_soon(second_request)
                    raise TimelineCleanupError("reader resource retirement failed")
                async for value in super().observe(requested, stop=stop):
                    yield value

            async def history(self, requested, before, *, stop):
                if failure_kind == "page" and not self.page_started.is_set():
                    self.page_started.set()
                    asyncio.get_running_loop().call_soon(second_request)
                    raise TimelineCleanupError("page resource retirement failed")
                return HistoryPage(requested, before, None, (), Completeness.PARTIAL)

        healthy = Source()

        def receive(value):
            delivered.append(value)
            if value.purpose == "other provider still usable":
                changed.set()

        controller = TimelineController(
            {Provider.WORKTREES: FailedSource(), Provider.BRIDGE: healthy},
            observation=receive, page=lambda value: None, error=lambda requested, message: errors.append(message),
        )
        try:
            await controller.select((selected, other))
            if failure_kind == "page":
                assert controller.request_history(selected, position(selected))
            witness = await asyncio.wait_for(result, 1)
            assert isinstance(witness, TimelineCleanupError), "a second read replaced terminal failure custody"
            await healthy.queue.put(replace(snapshot(other), purpose="other provider still usable"))
            await asyncio.wait_for(changed.wait(), 1)
            assert controller.request_history(other, position(other))
            healthy.page_release.set()
            controller._pages[Provider.WORKTREES] = asyncio.create_task(asyncio.sleep(0))
            action = controller.close() if retirement == "close" else controller.select((token(epoch=1),))
            with pytest.raises(TimelineCleanupError):
                await action
            with pytest.raises(TimelineCleanupError):
                await controller.close()
            assert controller.reader_count == controller.page_count == 0
        finally:
            try:
                await controller.close()
            except TimelineCleanupError:
                pass

    asyncio.run(run())
