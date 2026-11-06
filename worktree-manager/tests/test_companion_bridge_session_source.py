"""Private process pipes exercise owning parsers, semantic admission and EventLog wakes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import select
import socket
import sys
from threading import Event, Lock, Thread
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest
from textual.widgets import Select, Static, TabbedContent

from worktree_manager import companion_bridge_source as bridge
from worktree_manager import mux_companion as companion
from worktree_manager.companion_bridge_session_source import BridgeSessionSource
from worktree_manager.companion_timeline_contract import Availability
from worktree_manager.companion_worktree_source import WorktreeTimelineSource
from test_agenda_end_to_end import ROOT, SOURCE_PATHS
from test_companion_agenda_live import current_data, until
from test_companion_bridge_codec import OWNER, member

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.timeout(90),
    pytest.mark.skipif(os.name == "nt", reason="POSIX private CLI pipe, not native Windows qualification"),
]


@pytest.fixture
def semantic_peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = [ROOT / "plugins/agent-bridge/src", *SOURCE_PATHS]
    for path in reversed(paths):
        monkeypatch.syspath_prepend(str(path))
    db_module = importlib.import_module("agent_bridge.db")
    events = importlib.import_module("agent_bridge.events")
    membership = importlib.import_module("agent_bridge.owner_membership")
    wire = importlib.import_module("agent_bridge.session_observation_contract")
    routes = importlib.import_module("agent_bridge.routes.session_observations")
    membership_routes = importlib.import_module("agent_bridge.routes.owner_membership")
    db = db_module.Database(tmp_path / "bridge.sqlite3")
    db.create_session(
        "child", "fixture", None, str(tmp_path), "local", "starting", 1.0,
        target_json=json.dumps({
            "caller_owner_ref": OWNER + "#head-a", "project": "example", "worktree_id": "child-worktree",
        }),
    )
    log = events.EventLog(db=db, session_id="child", worktree_id="child-worktree")
    session = SimpleNamespace(
        session_id="child", target=SimpleNamespace(project="example", worktree_id="child-worktree", type="local"),
        event_log=log, acp_session_id=None, subscriber_count=0, touch=lambda: None,
    )
    manager = SimpleNamespace(
        db=db, _generation_id="fixture-generation", get_session=lambda key: session if key == "child" else None,
    )
    state = SimpleNamespace(session_manager=manager, db=db)
    owner = membership.source_for(manager, OWNER)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.1)
    stop, following, lock = Event(), Event(), Lock()
    requests, failures, peers, workers = [], [], [], []
    counts = {"held": 0, "max_held": 0}
    unsupported = tmp_path / "unsupported-session"
    history_entered, block_history = Event(), Event()
    old_cli, old_provider = tmp_path / "old-current-cli", tmp_path / "old-current-provider"
    declaration_override, reject_flag = tmp_path / "cli-declaration.json", tmp_path / "reject-current-flag"
    refusal_version = tmp_path / "refusal-version.json"
    old_statistics_cli = tmp_path / "old-statistics-cli"
    old_statistics_provider = tmp_path / "old-statistics-provider"
    reject_statistics = tmp_path / "reject-statistics-flag"
    invocations = tmp_path / "invocations.jsonl"
    diagnostic = tmp_path / "bridge-failure.json"
    owner_loop = asyncio.new_event_loop()
    owner_thread = Thread(target=owner_loop.run_forever)
    owner_thread.start()

    def connection(peer: socket.socket) -> None:
        held = False
        try:
            with peer, peer.makefile("rb") as incoming:
                request = json.loads(incoming.readline(8192))
                requests.append(request)
                url = urlsplit(request["path"])
                pairs = parse_qsl(url.query)
                query = dict(pairs)
                held = query.get("follow") == "true"
                if held:
                    with lock:
                        counts["held"] += 1
                        counts["max_held"] = max(counts["max_held"], counts["held"])
                membership_read = url.path.endswith("/owner-observations")
                if membership_read:
                    assert query["owner_ref"] == OWNER
                    read = membership_routes.OwnerRead.parse(pairs)
                else:
                    history = "/session-observation-history/" in url.path
                    read = wire.ReadRequest.parse(url.path.rsplit("/", 1)[-1], pairs, history=history)
                if not membership_read and history:
                    history_entered.set()
                    while block_history.is_set() and not stop.wait(0.05):
                        if select.select([peer], [], [], 0)[0] and not peer.recv(1, socket.MSG_PEEK):
                            return

                async def serve() -> None:
                    async def disconnected() -> bool:
                        try:
                            return stop.is_set() or bool(
                                select.select([peer], [], [], 0)[0] and not peer.recv(1, socket.MSG_PEEK)
                            )
                        except OSError:
                            return True

                    request = SimpleNamespace(app=SimpleNamespace(state=state), is_disconnected=disconnected)
                    stream = (
                        membership_routes.stream(request, read) if membership_read
                        else routes.observations(request, read)
                    )
                    try:
                        async for chunk in stream:
                            await asyncio.to_thread(peer.sendall, chunk)
                            if held:
                                following.set()
                    finally:
                        await stream.aclose()

                asyncio.run_coroutine_threadsafe(serve(), owner_loop).result(timeout=80)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BaseException as error:
            failures.append(error)
        finally:
            if held:
                with lock:
                    counts["held"] -= 1

    def accept() -> None:
        while not stop.is_set():
            try:
                peer, _ = listener.accept()
            except socket.timeout:
                continue
            peers.append(peer)
            worker = Thread(target=connection, args=(peer,))
            workers.append(worker)
            worker.start()

    thread = Thread(target=accept)
    thread.start()
    script = tmp_path / "semantic-bridge"
    script.write_text(
        f"#!{sys.executable} -I\n"
        "import argparse,json,os,socket,sys,types\n"
        f"with open({str(invocations)!r},'a') as log: log.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"sys.path[:0]={[str(path) for path in paths]!r}\n"
        "core=types.ModuleType('agent_bridge.__main__')\n"
        "sys.modules['agent_bridge.__main__']=core\n"
        "from agent_bridge.session_observation_cli import register_commands\n"
        "from agent_bridge.owner_membership_cli import register_commands as membership\n"
        "from agent_bridge.resume_cli_contract import cli_contracts\n"
        "from agent_worktrees.locks import process_start_time\n"
        "assert os.environ.get('AGENT_BRIDGE_NO_SELFPROVISION')=='1'\n"
        "class Client:\n"
        "    def health(self): return {'capabilities':{'bridge-owner-membership-v1':1,"
        f"**({{}} if os.path.exists({str(unsupported)!r}) else "
        "{'bridge-session-observations-v1':1,'bridge-session-observation-history-v1':1}),"
        f"**({{}} if os.path.exists({str(old_provider)!r}) else "
        "{'bridge-session-current-requests-v1':1}),"
        f"**({{}} if os.path.exists({str(old_statistics_provider)!r}) else "
        "{'bridge-session-tool-statistics-v1':1})}}\n"
        "    def _stream_sse(self,path,*,request_timeout,stream_factory):\n"
        f"        peer=socket.create_connection(('127.0.0.1',{listener.getsockname()[1]}),timeout=2)\n"
        "        peer.settimeout(15)\n"
        "        peer.sendall(json.dumps({'path':path,'pid':os.getpid(),"
        "'birth':process_start_time(os.getpid())}).encode()+b'\\n')\n"
        "        incoming=peer.makefile('rb')\n"
        "        class Response:\n"
        "            def readline(self,size): return incoming.readline(size)\n"
        "            def __iter__(self): return self\n"
        "            def __next__(self): raise AssertionError('raw SSE fallback consumed')\n"
        "            def close(self): incoming.close(); peer.close()\n"
        "        return stream_factory(Response())\n"
        "def client(*,ensure=True):\n"
        "    assert ensure is False,'reader attempted service startup'\n"
        "    return Client()\n"
        "core._get_client=client\n"
        "parser=argparse.ArgumentParser()\n"
        "parser.add_argument('--project',default=None)\n"
        "sub=parser.add_subparsers(dest='command',required=True)\n"
        "register_commands(sub); membership(sub)\n"
        "def version(args):\n"
        "    body=cli_contracts()\n"
        f"    if os.path.exists({str(old_cli)!r}): body['contracts'].pop('bridge-session-current-requests-v1')\n"
        f"    if os.path.exists({str(old_statistics_cli)!r}): body['contracts'].pop('bridge-session-tool-statistics-v1')\n"
        f"    if os.path.exists({str(declaration_override)!r}): body=json.load(open({str(declaration_override)!r}))\n"
        "    print(json.dumps(body,indent=2))\n"
        "ver=sub.add_parser('version'); ver.add_argument('--contracts-json',action='store_true')\n"
        "ver.set_defaults(func=version)\n"
        f"if os.path.exists({str(reject_flag)!r}) and '--current-requests' in sys.argv:\n"
        "    parser.error('unrecognized arguments: --current-requests')\n"
        f"if os.path.exists({str(reject_statistics)!r}) and '--tool-statistics' in sys.argv:\n"
        "    parser.error('unrecognized arguments: --tool-statistics')\n"
        "args=parser.parse_args()\n"
        f"if args.command=='observe-session' and args.current_requests and os.path.exists({str(refusal_version)!r}):\n"
        "    print(json.dumps({'schema':'bridge-session-observations-v1',"
        f"'version':json.load(open({str(refusal_version)!r})),'kind':'error','code':'unsupported',"
        "'message':'selected daemon does not support bridge-session-current-requests-v1'}),flush=True)\n"
        "    raise SystemExit(3)\n"
        "try:\n"
        "    args.func(args)\n"
        "except BaseException:\n"
        "    import traceback\n"
        "    from pathlib import Path\n"
        f"    Path({str(diagnostic)!r}).write_text(traceback.format_exc(),encoding='utf-8')\n"
        "    raise\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    monkeypatch.setattr(bridge, "bridge_command", lambda root: [sys.executable, "-I", str(script)])
    result = {
        "log": log, "session": session, "requests": requests, "following": following,
        "counts": counts, "unsupported": unsupported,
        "history_entered": history_entered, "block_history": block_history,
        "manager": manager,
        "owner_loop": owner_loop, "old_cli": old_cli, "old_provider": old_provider,
        "declaration_override": declaration_override, "reject_flag": reject_flag,
        "refusal_version": refusal_version,
        "old_statistics_cli": old_statistics_cli, "old_statistics_provider": old_statistics_provider,
        "reject_statistics": reject_statistics, "invocations": invocations,
        "failures": failures,
        "diagnostic": diagnostic,
    }
    try:
        yield result
    finally:
        stop.set()
        thread.join(timeout=3)
        for peer in peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in workers:
            worker.join(timeout=3)
        owner_loop.call_soon_threadsafe(owner_loop.stop)
        owner_thread.join(timeout=3)
        owner_loop.run_until_complete(owner_loop.shutdown_default_executor())
        owner_loop.close()
        listener.close()
        owner.close()
        getattr(db, "_owner_membership_source", owner).close()
        db.flush()
        db.close()
        assert not thread.is_alive() and not owner_thread.is_alive() and not any(
            worker.is_alive() for worker in workers
        )
        assert not failures, failures
        assert session.subscriber_count == 0
        locks = importlib.import_module("agent_worktrees.locks")
        for request in requests:
            assert not locks.pid_alive(request["pid"]) or locks.process_start_time(request["pid"]) != request["birth"]


def source(tmp_path: Path) -> BridgeSessionSource:
    return BridgeSessionSource(
        "example", "selected", "head-a", owner_ref=OWNER, member=member(),
        hosting_generation="fixture-generation", cwd=str(tmp_path), project_root=str(tmp_path),
    )


def test_real_session_history_live_cursor_and_independent_cancellation(semantic_peer, tmp_path) -> None:
    for index in range(70):
        semantic_peer["log"].append("tool_call_start", {"tool_call_id": str(index)})

    async def check() -> None:
        reader, stop = source(tmp_path), asyncio.Event()
        initial = await reader.prepare(2, stop=stop)
        assert 1 <= len(initial.events) <= 64 and initial.before is not None
        stream = reader.observe(initial.token, stop=stop)
        await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        try:
            assert await asyncio.to_thread(semantic_peer["following"].wait, 5)
            delivered = reader._cursor
            before, loaded, pages = initial.before, list(initial.events), 0
            while before is not None:
                page = await reader.history(initial.token, before, stop=asyncio.Event())
                loaded = [*page.events, *loaded]
                before, pages = page.next_before, pages + 1
                assert pages < 10
            assert [row.event_id for row in loaded] == [f"event:{index}" for index in range(1, 71)]
            assert reader._cursor == delivered and not waiting.done()
            semantic_peer["log"].append("permission_request", {"request_id": "permission-a"})
            value = await waiting
            assert value.base == initial.position and value.events[0].event_id == "event:71"
            assert semantic_peer["counts"]["held"] == 1
            assert len(semantic_peer["requests"]) == 2 + pages
        finally:
            stop.set()
            await asyncio.gather(waiting, return_exceptions=True)
            await stream.aclose()

    asyncio.run(check())


def test_missing_running_semantic_capability_never_uses_raw_or_session_route(semantic_peer, tmp_path) -> None:
    semantic_peer["unsupported"].touch()

    async def check() -> None:
        with pytest.raises(bridge.OwnerReadError, match="unsupported"):
            await source(tmp_path).prepare(1, stop=asyncio.Event())
        assert not semantic_peer["requests"]

    asyncio.run(check())


def test_empty_log_live_first_event_uses_real_continuity(semantic_peer, tmp_path) -> None:
    async def check() -> None:
        reader, stop = source(tmp_path), asyncio.Event()
        preparing = asyncio.create_task(reader.prepare(1, stop=stop))
        try:
            assert await asyncio.to_thread(semantic_peer["following"].wait, 5)
            assert not preparing.done() and reader.initial is None
            semantic_peer["log"].append("ask_user_request", {"request_id": "input-a"})
            initial = await preparing
            assert initial.events[0].kind.value == "input-request"
            assert initial.position is not None
            # Refreshing the same real incarnation must not change its integrity scope.
            second = await source(tmp_path).prepare(2, stop=asyncio.Event())
            assert second.token.identity == initial.token.identity
            assert second.events == initial.events
        finally:
            stop.set()
            await asyncio.gather(preparing, return_exceptions=True)

    asyncio.run(check())


def test_empty_log_first_event_wait_is_owned_and_cancellable(semantic_peer, tmp_path) -> None:
    async def check() -> None:
        reader, stop = source(tmp_path), asyncio.Event()
        preparing = asyncio.create_task(reader.prepare(1, stop=stop))
        try:
            assert await asyncio.to_thread(semantic_peer["following"].wait, 5)
        finally:
            stop.set()
        with pytest.raises(asyncio.CancelledError):
            await preparing
        assert reader.initial is None and reader.codec.source["journal_incarnation"] is None

    asyncio.run(check())


def test_cancelled_history_retires_only_its_pipe_without_advancing_live_position(semantic_peer, tmp_path) -> None:
    for index in range(70):
        semantic_peer["log"].append("tool_call_start", {"tool_call_id": str(index)})

    async def check() -> None:
        reader = source(tmp_path)
        initial = await reader.prepare(1, stop=asyncio.Event())
        assert initial.before is not None
        cancelled = asyncio.Event()
        semantic_peer["block_history"].set()
        task = asyncio.create_task(reader.history(initial.token, initial.before, stop=cancelled))
        try:
            assert await asyncio.to_thread(semantic_peer["history_entered"].wait, 5)
        finally:
            cancelled.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reader.initial == initial and reader._cursor is not None
        assert semantic_peer["log"]._next_id == 71

    asyncio.run(check())


def test_keyboard_selected_session_replaces_membership_reader_and_retains_independent_views(
    semantic_peer, tmp_path, monkeypatch, record_property,
) -> None:
    from test_companion_visual import offline_svg

    def data():
        result = current_data()
        result.worktree["path"] = str(tmp_path)
        result.bundle = {"facts": {"claims": {"value": {"owner_ref": OWNER}, "confirmed": True}}}
        return result

    async def unavailable(*args, **kwargs):
        raise bridge.OwnerReadError("unavailable", "fixture Worktrees source unavailable")

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: data())
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(WorktreeTimelineSource, "prepare", unavailable)
    semantic_peer["log"].append("subagent_started", {"agent_id": "helper", "parent_tool_call_id": "tool-a"})

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: "owned:child" in app._bridge_attachment._members)
            controller = app._bridge_attachment.controller
            await app._bridge_attachment.choose("owned:vanished")
            assert app._bridge_attachment.controller is controller and app._bridge_attachment.choice == "membership"
            await pilot.click("CollapsibleTitle")
            await pilot.pause()
            control = app.query_one("#bridge-feed", Select)
            control.focus()
            await pilot.press("enter", "down", "enter")
            await until(pilot, lambda: any(
                row.event_id == "event:1" for row in app._timeline_panel.window.events
            ))
            assert app._bridge_attachment.choice == "owned:child"
            await until(pilot, semantic_peer["following"].is_set)
            assert "membership discovery paused" in str(app.query_one("#timeline-bridge-status", Static).content)
            assert app._bridge_attachment.controller.reader_count == 1
            membership = next(
                state for state in app._timeline_panel.window.sources.values()
                if state.token.identity.source_key.startswith("bridge-membership:")
            )
            assert membership.availability is Availability.UNKNOWN
            assert membership.requested_before is None
            assert "Bridge reader paused" not in str(app.query_one(".timeline-state", Static).content)
            semantic_peer["log"].append("ask_user_request", {"request_id": "input-b"})
            await until(pilot, lambda: any(
                row.event_id == "event:2" for row in app._timeline_panel.window.events
            ))
            record_property("sidecar-state", "integration-session")
            record_property("sidecar-svg", offline_svg(app.export_screenshot()))
            control.value = "membership"
            await until(pilot, lambda: app._bridge_attachment.choice == "membership"
                        and "child: starting" in str(app.query_one("#timeline-bridge-status", Static).content))
            assert any(row.event_id == "event:2" for row in app._timeline_panel.window.events)
            assert semantic_peer["counts"]["max_held"] == 1
            retired = next(state for state in app._timeline_panel.window.sources.values()
                           if state.token.identity.source_key.startswith("bridge-session:"))
            app._timeline_panel.window.sources[retired.token.identity.source_key] = replace(
                retired, availability=Availability.HEAD_CHANGED,
            )
            revision = app._timeline_sync_revision
            app.on_subway_timeline_changed(None)
            await pilot.pause()
            assert app._timeline_sync_revision == revision, "retained old sources restarted the active reader"
            assert app._data.error is None
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await until(pilot, lambda: app._bridge_attachment.controller is None)
            await until(pilot, lambda: semantic_peer["session"].subscriber_count == 0)
            requests = len(semantic_peer["requests"])
            await app._bridge_attachment.choose("owned:child")
            await pilot.pause()
            assert app._bridge_attachment.controller is None and len(semantic_peer["requests"]) == requests

    asyncio.run(check())


def test_selected_session_is_fenced_by_independent_head_change(semantic_peer, tmp_path, monkeypatch) -> None:
    def data():
        result = current_data()
        result.worktree["path"] = str(tmp_path)
        result.bundle = {"facts": {"claims": {"value": {"owner_ref": OWNER}, "confirmed": True}}}
        return result

    async def unavailable(*args, **kwargs):
        raise bridge.OwnerReadError("unavailable", "fixture Worktrees source unavailable")

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: data())
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(WorktreeTimelineSource, "prepare", unavailable)
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: "owned:child" in app._bridge_attachment._members)
            app.query_one("#bridge-feed", Select).value = "owned:child"
            await until(pilot, semantic_peer["following"].is_set)
            app._agenda_cancel = Event()
            app._receive_agenda(
                app._data, app._agenda_generation, app._agenda_cancel, {
                    "project": "example", "worktree_id": "selected", "session_id": "head-a",
                    "availability": "head-changed", "observation": None,
                }, None,
            )
            await until(pilot, lambda: app._bridge_attachment.controller is None)
            assert all(state.availability is Availability.HEAD_CHANGED
                       for state in app._timeline_panel.window.sources.values())
            count = len(semantic_peer["requests"])
            semantic_peer["log"].append("permission_request", {"request_id": "late-input"})
            await pilot.pause()
            assert len(semantic_peer["requests"]) == count and not app._timeline_panel.window.events

    asyncio.run(check())
