"""Real owner/producer/feed/Picker convergence with periodic production disabled.

Only native termination and Git/mux OS probes are fixture boundaries. The
separate engine native-exit suite exercises actual owned-process retirement.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

from agent_worktrees import config, git_ops, hook_ipc, mux_link, reclaim, reclaim_cli
from agent_worktrees import resident_push, sessions, tracking, worktrees_client, worktrees_producer
from agent_worktrees import __main__ as core
from agent_worktrees.status_monitor_cutover import ControlClient, ControlServer
from agent_worktrees.worktree_notifications import notify_worktree_changed
from agent_worktrees.worktrees_feed_client import transport
from agent_worktrees.worktrees_runtime import WorktreesRuntime
from worktree_manager import engine_client
from worktree_manager.production_picker.picker_tui import data_ssh, derive, maintenance
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.worktree_feed_contract import CurrentFeed


def test_resume_mux_message_and_one_reclaim_reach_open_picker_without_poll_or_reconnect(tmp_path, monkeypatch):
    root, project, checkout = (tmp_path / name for name in ("runtime", "project", "checkout"))
    records, session_root = project / "worktrees", tmp_path / "sessions"
    for path in (root, records, checkout / ".git", session_root / "fixture-session"):
        path.mkdir(parents=True)
    (project / "config.yaml").write_text(json.dumps({
        "repos": {"project": {"anchor": str(checkout)}},
    }), encoding="utf-8")
    monkeypatch.setattr(config, "install_dir", lambda: root)
    monkeypatch.setattr(config, "project_name", lambda: "project")
    monkeypatch.setattr(config, "project_dir", lambda *args: project)
    monkeypatch.setattr(config, "tracking_dir", lambda *args: records)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: session_root)
    live = [False]
    monkeypatch.setattr(sessions, "_is_copilot_process", lambda pid: pid == 77777 and live[0])
    monkeypatch.setattr(sessions, "_is_process_alive", lambda pid: pid == 77777 and live[0])
    monkeypatch.setattr(sessions, "has_mux_session", lambda *args: False)
    monkeypatch.setattr(
        sessions, "mux_status_many", lambda ids: {wid: sessions.MuxInfo(False, 0) for wid in ids},
    )
    monkeypatch.setattr(core, "_classify_records_live", lambda records, *args, **kwargs: {
        record.worktree_id: git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.WIP, dirty=0)
        for record in records
    })
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    record = tracking.WorktreeRecord(
        worktree_id="fixture-one", branch="worktree/fixture-one", worktree_path=str(checkout),
        repo="project", machine="fixture-host", platform="windows", title="Fixture",
        started_at="2026-01-01T00:00:00", last_resumed_at=None, resume_count=0,
        status="active", completed_at=None, creation_nonce="fixture-incarnation",
        codename="fixture-torch", sessions=[tracking.SessionEntry("fixture-session", "2026-01-01")],
        head_session="fixture-session",
    )
    tracking.save_record(record)
    events = session_root / "fixture-session" / "events.jsonl"
    events.write_text(json.dumps({
        "type": "user.message", "timestamp": "2026-01-01T00:00:00Z",
    }) + "\n", encoding="utf-8")
    cache = mux_link.ManagedMuxCache()
    owner = WorktreesRuntime(root / "current.sqlite3")
    owner.activate()
    worktrees_producer.publish_active_project(owner, managed_mux_cache=cache)
    wake, stop, publication_gate = threading.Event(), threading.Event(), threading.Event()
    publication_gate.set()
    resident_push.bind(wake, SimpleNamespace(invalidate=lambda path: None))
    hook = hook_ipc.HookIpcServer(lambda *args: {}, worktree_changed=resident_push.notify)
    hook.start()
    (root / "status-monitor.lock").write_text(json.dumps(hook.rendezvous()), encoding="utf-8")
    control = ControlServer(
        lambda action, body: owner.handle(body), token="fixture",
        stream_handle=lambda action, body, disconnected: owner.feed.stream(body, disconnected),
    )
    control.start()
    errors, receiver_threads, loaders = [], [], []

    def produce():
        try:
            while not stop.is_set():
                if not wake.wait(1):
                    continue
                wake.clear()
                if stop.is_set():
                    return
                assert publication_gate.wait(5), "fixture publication gate was never released"
                worktrees_producer.publish_active_project(owner, managed_mux_cache=cache)
        except Exception as error:
            errors.append(error)

    producer = threading.Thread(target=produce)
    producer.start()

    def receive(loader, source):
        stream = transport(
            ControlClient(f"http://127.0.0.1:{control.port}", token="fixture"),
            {"contract_version": 1, "project": "project"},
        )
        receiver = CurrentFeed()
        try:
            for frame in stream:
                rows = receiver.receive(frame)
                if rows is not None:
                    loader._feed_commit(source, 0, rows, availability=receiver.availability)
                if frame["type"] == "going-away":
                    break
        except Exception as error:
            if not stop.is_set():
                errors.append(error)
        finally:
            stream.close()

    for _ in range(2):
        source = data_ssh.Source("fixture-host", "Win", None, local=True)
        loader = data_ssh.LiveLoader([source])
        loaders.append(loader)
        worker = threading.Thread(target=receive, args=(loader, source))
        receiver_threads.append(worker)
        worker.start()
    local = ("fixture-host", "Win")
    source = SimpleNamespace(
        LOCAL=local, LOCAL_LABEL="Fixture", bucket=derive.bucket, for_machine=derive.for_machine,
        is_local=lambda m, e: (m, e) == local,
        machines=lambda: [("Fixture", *local, True)], load=lambda: loaders[0].records(),
    )
    reaped, result = [], []
    target = {"pid": 77777, "start_time": "fixture-birth", "session_id": "fixture-session",
              "worktree_id": record.worktree_id, "homing": "bare"}
    monkeypatch.setattr(reclaim, "build_process_table", lambda: {
        77777: {"name": "copilot", "ppid": 1, "start_time": "fixture-birth"},
    })
    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **kwargs: [target] if live[0] else [])
    monkeypatch.setattr(reclaim, "resolve_bridge_bound", lambda *args, **kwargs: [])
    monkeypatch.setattr(reclaim, "teardown_detached_mux", lambda *args, **kwargs: [])
    monkeypatch.setattr(worktrees_client, "read_current", lambda request, **kwargs: owner.handle(request))

    def retire(targets, **kwargs):
        reaped.extend(targets)
        live[0] = False
        return [{**target, "killed": True}]

    monkeypatch.setattr(reclaim, "reap_bound_copilots", retire)

    def execute(project_name, argv, **kwargs):
        assert project_name == "project"
        assert argv == ["reclaim", "--worktree-id", record.worktree_id, "--bare-only", "--yes", "--json"]
        return reclaim_cli.reclaim_one(record.worktree_id)

    monkeypatch.setattr(engine_client, "run_json", execute)
    reclaim_worker = None

    async def run():
        nonlocal reclaim_worker
        async def until(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                assert not errors, errors
                if predicate():
                    return
                await asyncio.sleep(0.02)
            raise AssertionError("event-driven owner/feed/Picker did not converge")

        await until(lambda: all(loader.records() for loader in loaders))
        app = PickerApp(source, live=False)
        async with app.run_test(size=(118, 32)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            scr.machine_idx = scr.local_index()
            scr.live, scr.loader = True, loaders[0]
            monkeypatch.setattr(scr, "_maybe_repoll", lambda: None)
            scr.sel = ("L", 0)
            scr._sync_focus_to_sel()
            stable = scr._selected_record()["selection_id"]

            def rows_are(predicate):
                return all(predicate(loader.records()[0]) for loader in loaders) and predicate(
                    scr._wt_visible_records()[0],
                )

            live[0] = True
            (session_root / "fixture-session" / "inuse.77777.lock").touch()
            tracking.mark_resumed(tracking.load_record(records / "fixture-one.yaml"))
            await until(lambda: rows_are(lambda row: row["sess"] == "PROC"))
            compute = mux_link.build_compute(cache, on_change=lambda _: resident_push.notify(str(checkout)))
            mapping = {
                "project": "project", "worktree_id": record.worktree_id, "mux_session": "fixture-mux",
                "mapping_revision": 1, "live": True, "attached_clients": 1,
                "panes": [], "worktree_path": str(checkout),
            }
            assert compute(mux_link.KIND, mapping)["applied"]
            await until(lambda: rows_are(lambda row: row["mux_live"]))
            assert compute(mux_link.KIND, {**mapping, "mapping_revision": 2, "live": False})["applied"]
            await until(lambda: rows_are(lambda row: row["sess"] == "PROC"))
            message_at = "2026-11-07T01:00:00Z"
            with events.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "assistant.message", "timestamp": message_at}) + "\n")
            assert notify_worktree_changed(str(checkout))
            await until(lambda: rows_are(lambda row: row["used_at"] == message_at))
            assert scr._selected_record()["selection_id"] == stable

            publication_gate.clear()
            selected = scr._wt_visible_records()[0]
            tasks = maintenance.build_tasks("reclaim", [selected], source, project="project", local=local)
            reclaim_worker = threading.Thread(target=lambda: result.append(tasks[0][1]()))
            reclaim_worker.start()
            await until(lambda: len(reaped) == 1)
            await asyncio.sleep(0.1)
            assert not result and selected["sess"] == "PROC"
            publication_gate.set()
            await until(lambda: bool(result) and rows_are(lambda row: row["sess"] == "-"))
            assert result[0]["ok"] and result[0]["publication"]["confirmed"]
            assert len(reaped) == 1 and len(receiver_threads) == 2
            assert scr._selected_record()["selection_id"] == stable

    try:
        asyncio.run(run())
    finally:
        publication_gate.set()
        stop.set()
        wake.set()
        producer.join(6)
        hook.close()
        owner.close_admission("fixture-complete")
        for worker in receiver_threads:
            worker.join(6)
        if reclaim_worker is not None:
            reclaim_worker.join(6)
        for loader in loaders:
            loader.cancel()
        control.close()
        owner.shutdown()
        resident_push.reset()
    assert not producer.is_alive() and all(not worker.is_alive() for worker in receiver_threads)
    assert reclaim_worker is None or not reclaim_worker.is_alive()
    assert not errors
