"""Canonical Bridge commits, its owning CLI and caller-owned Manager pipes."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import socket
import sys
from threading import Event, Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from textual.widgets import Static, TabbedContent

from worktree_manager import companion_bridge_source as bridge
from worktree_manager import mux_companion as companion
from worktree_manager.agenda_transport import STARTUP_TIMEOUT
from worktree_manager.companion_bridge_codec import MembershipCodec
from worktree_manager.companion_timeline_contract import Provider
from worktree_manager.companion_timeline_widget import SubwayTimeline
from test_agenda_end_to_end import ROOT, SOURCE_PATHS
from test_companion_agenda_live import current_data, until
from test_companion_bridge_codec import OWNER, raw
from _worktree_timeline_fixtures import (
    JOIN_COMMAND_CLEANUP, JOIN_PREPARATION_SECONDS, JoinPhase, resident_join,
)

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX attributed executable; not native Windows qualification"),
]


@pytest.fixture
def native_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = [ROOT / "plugins/agent-bridge/src", *SOURCE_PATHS]
    for path in reversed(paths):
        monkeypatch.syspath_prepend(str(path))
    database = importlib.import_module("agent_bridge.db")
    membership = importlib.import_module("agent_bridge.owner_membership")
    wire = importlib.import_module("agent_bridge.session_observation_contract")
    db = database.Database(tmp_path / "bridge.sqlite3")
    db.create_session(
        "child", "fixture", None, str(tmp_path), "local", "starting", 1.0,
        target_json=json.dumps({
            "caller_owner_ref": OWNER + "#head-a", "project": "example", "worktree_id": "child-worktree",
            "env": {"PASSWORD": "excluded-private-value"},
        }),
    )
    manager = SimpleNamespace(db=db, _generation_id="fixture-serving-generation")
    source = membership.source_for(manager, OWNER)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(0.1)
    stop, changed, following = Event(), Event(), Event()
    requests, failures, peers, outgoing = [], [], [], []
    unsupported = tmp_path / "unsupported-membership"

    def serve() -> None:
        try:
            while not stop.is_set():
                try:
                    peer, _ = listener.accept()
                except socket.timeout:
                    continue
                peers.append(peer)
                peer.settimeout(2)
                with peer, peer.makefile("rb") as incoming:
                    request = json.loads(incoming.readline(8192))
                    requests.append(request)
                    query = parse_qs(urlsplit(request["path"]).query)
                    assert query["owner_ref"] == [OWNER] and query["contract_version"] == ["1"]
                    after = query.get("after", [None])[0]
                    body = outgoing.pop(0) if outgoing else source.frame(OWNER, after)
                    peer.sendall(wire.sse(body))
                    if query["follow"] == ["false"]:
                        continue
                    following.set()
                    after = body["position"]
                    while not stop.is_set():
                        if not changed.wait(0.05):
                            continue
                        changed.clear()
                        body = outgoing.pop(0) if outgoing else source.frame(OWNER, after)
                        peer.sendall(wire.sse(body))
                        if body["kind"] in {"gap", "error", "going-away"}:
                            break
                        after = body["position"]
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=serve, name="private-bridge-transport")
    thread.start()
    script = tmp_path / "bridge-observer"
    script.write_text(
        f"#!{sys.executable} -I\n"
        "import argparse,json,os,socket,sys,types\n"
        f"sys.path[:0]={[str(path) for path in paths]!r}\n"
        "core=types.ModuleType('agent_bridge.__main__')\n"
        "sys.modules['agent_bridge.__main__']=core\n"
        "from agent_bridge.owner_membership_cli import register_commands\n"
        "from agent_worktrees.locks import process_start_time\n"
        "assert os.environ.get('AGENT_BRIDGE_NO_SELFPROVISION')=='1'\n"
        "class Client:\n"
        f"    def health(self): return {{'capabilities':{{}} if os.path.exists({str(unsupported)!r}) else {{'bridge-owner-membership-v1':1}}}}\n"
        "    def _stream_sse(self,path,*,request_timeout,stream_factory):\n"
        f"        peer=socket.create_connection(('127.0.0.1',{listener.getsockname()[1]}),timeout=2)\n"
        "        peer.settimeout(15)\n"
        "        peer.sendall(json.dumps({'path':path,'pid':os.getpid(),"
        "'birth':process_start_time(os.getpid())}).encode()+b'\\n')\n"
        "        stream=peer.makefile('rb')\n"
        "        class Response:\n"
        "            def __iter__(self): return self\n"
        "            def __next__(self): raise AssertionError('raw SSE iterator consumed')\n"
        "            def readline(self,size): return stream.readline(size)\n"
        "            def close(self): stream.close(); peer.close()\n"
        "        return stream_factory(Response())\n"
        "def client(*,ensure=True):\n"
        "    assert ensure is False,'observation attempted service ensure'\n"
        "    return Client()\n"
        "core._get_client=client\n"
        "parser=argparse.ArgumentParser()\n"
        "parser.set_defaults(project=None)\n"
        "register_commands(parser.add_subparsers(dest='command',required=True))\n"
        "args=parser.parse_args()\n"
        "args.func(args)\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    monkeypatch.setattr(bridge, "bridge_command", lambda root: [str(script)])
    result = {
        "db": db, "source": source, "following": following, "requests": requests,
        "changed": changed, "failures": failures, "manager": manager,
        "outgoing": outgoing, "unsupported": unsupported,
    }
    try:
        yield result
    finally:
        stop.set()
        changed.set()
        for peer in peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        thread.join(timeout=3)
        listener.close()
        source.close()
        db.close()
        assert not thread.is_alive()
        assert not failures, failures


def test_actual_canonical_snapshot_and_retirement_decode_without_private_data(native_bridge) -> None:
    source = native_bridge["source"]
    codec = MembershipCodec("example", "selected", OWNER)
    initial = codec.decode(raw(source.frame(OWNER, None)))
    assert "excluded-private-value" not in json.dumps(initial)
    token = codec.token(initial, 1)
    assert not codec.apply(initial, token).events
    native_bridge["db"].update_session_status("child", "ended", 2.0)
    frame = codec.decode(raw(source.frame(OWNER, initial["position"])))
    event = codec.delta(frame, token).events[0]
    assert event.summary == "Bridge membership: session-retired"
    assert json.loads(event.data_json)["grants_claim_release"] is False
    assert not json.loads(codec.apply(frame, token).facts_json)["members"]


def test_old_running_capability_refuses_before_membership_transport(native_bridge, tmp_path) -> None:
    native_bridge["unsupported"].touch()

    async def check() -> None:
        source = bridge.BridgeMembershipSource(
            "example", "selected", "head-a", owner_ref=OWNER,
            cwd=str(tmp_path), project_root=str(tmp_path),
        )
        with pytest.raises(bridge.OwnerReadError, match="unsupported"):
            await source.prepare(1, stop=asyncio.Event())
        assert not native_bridge["requests"] and source.initial is None

    asyncio.run(check())


def test_owning_cli_live_push_and_independent_cancellation_retire_only_the_reader(native_bridge, tmp_path) -> None:
    async def check() -> None:
        source = bridge.BridgeMembershipSource(
            "example", "selected", "head-a", owner_ref=OWNER,
            cwd=str(tmp_path), project_root=str(tmp_path),
        )
        stop = asyncio.Event()
        initial = await source.prepare(1, stop=stop)
        assert initial.token.identity.head_session_id is None and not initial.events
        stream = source.observe(initial.token, stop=stop)
        assert await anext(stream) == initial
        waiting = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(native_bridge["following"].wait, 5)
        native_bridge["db"].update_session_status("child", "idle", 2.0)
        native_bridge["changed"].set()
        value = await waiting
        assert value.events[0].actor.reported_state == "idle"
        snapshot = await anext(stream)
        assert json.loads(snapshot.facts_json)["members"][0]["state"] == "idle"
        stop.set()
        await stream.aclose()
        assert native_bridge["db"].get_session("child")["status"] == "idle"
        assert not native_bridge["failures"]
        requests = native_bridge["requests"]
        assert len(requests) == 2 and "after=" in requests[1]["path"]
        locks = importlib.import_module("agent_worktrees.locks")
        for request in requests:
            assert not locks.pid_alive(request["pid"]) or locks.process_start_time(request["pid"]) != request["birth"]

    asyncio.run(check())


def test_unrelated_owner_heartbeat_preserves_membership_splice_without_a_milestone(
    native_bridge, tmp_path, monkeypatch,
) -> None:
    async def check() -> None:
        source = bridge.BridgeMembershipSource(
            "example", "selected", "head-a", owner_ref=OWNER,
            cwd=str(tmp_path), project_root=str(tmp_path),
        )
        stop, heartbeat = asyncio.Event(), Event()
        initial = await source.prepare(1, stop=stop)
        decode = source.codec.decode

        def capture(raw):
            body = decode(raw)
            if body["kind"] == "heartbeat":
                heartbeat.set()
            return body

        monkeypatch.setattr(source.codec, "decode", capture)
        stream = source.observe(initial.token, stop=stop)
        await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        try:
            assert await asyncio.to_thread(native_bridge["following"].wait, 5)
            db = native_bridge["db"]
            other = "fixture-host/example/another-worktree"
            db.create_session(
                "other-child", "fixture", None, str(tmp_path), "local", "starting", 1.0,
                target_json=json.dumps({"caller_owner_ref": other}),
            )
            membership = importlib.import_module("agent_bridge.owner_membership")
            membership.source_for(native_bridge["manager"], other)
            db.update_session_status("other-child", "idle", 2.0)
            native_bridge["source"].frame(other, None)
            body = native_bridge["source"].frame(OWNER, source._cursor)
            native_bridge["outgoing"].append({
                "schema": body["schema"], "version": 1, "kind": "heartbeat",
                "source": body["source"], "position": body["position"],
            })
            native_bridge["changed"].set()
            assert await asyncio.to_thread(heartbeat.wait, 5)
            assert not waiting.done(), "a heartbeat became a productive timeline update"
            db.update_session_status("child", "idle", 3.0)
            native_bridge["changed"].set()
            value = await waiting
            assert value.base == initial.position and len(value.events) == 1
            assert value.events[0].actor.actor_id == "owned:child"
        finally:
            stop.set()
            await asyncio.gather(waiting, return_exceptions=True)
            await stream.aclose()

    asyncio.run(check())


def test_retention_gap_does_not_replay_or_retire_the_session(native_bridge, tmp_path) -> None:
    async def check() -> None:
        source = bridge.BridgeMembershipSource(
            "example", "selected", "head-a", owner_ref=OWNER,
            cwd=str(tmp_path), project_root=str(tmp_path),
        )
        stop = asyncio.Event()
        initial = await source.prepare(1, stop=stop)
        stream = source.observe(initial.token, stop=stop)
        await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        try:
            assert await asyncio.to_thread(native_bridge["following"].wait, 5)
            native_bridge["outgoing"].append({
                "schema": "bridge-owner-membership-v1", "version": 1, "kind": "gap",
                "source": source.codec.source, "code": "retention-gap", "message": "fixture retention gap",
            })
            native_bridge["changed"].set()
            with pytest.raises(bridge.OwnerReadError, match="retention-gap"):
                await waiting
            assert len(native_bridge["requests"]) == 2
            assert native_bridge["db"].get_session("child")["status"] == "starting"
        finally:
            stop.set()
            await asyncio.gather(waiting, return_exceptions=True)
            await stream.aclose()

    asyncio.run(check())


def test_membership_is_demand_driven_and_does_not_disable_independent_summary(
    native_bridge, tmp_path, monkeypatch,
) -> None:
    def data():
        result = current_data()
        result.worktree["path"] = str(tmp_path)
        result.bundle = {"facts": {"claims": {"value": {"owner_ref": OWNER}, "confirmed": True}}}
        return result

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: data())
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(companion.ec, "engine_base_command", lambda: [sys.executable, "-m", "json"])

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            assert not native_bridge["requests"]
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: native_bridge["following"].is_set())
            assert "child: starting" in str(app.query_one("#timeline-bridge-status", Static).content)
            native_bridge["db"].update_session_status("child", "idle", 2.0)
            native_bridge["changed"].set()
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: any(event.source.provider is Provider.BRIDGE for event in timeline.window.events))
            event = next(event for event in timeline.window.events if event.source.provider is Provider.BRIDGE)
            assert event.actor.qualification.value == "reported"
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await until(pilot, lambda: app._bridge_attachment.controller is None)
            assert app._data.error is None
            assert native_bridge["db"].get_session("child")["status"] == "idle"
            await pilot.press("escape")

    asyncio.run(check())


@pytest.mark.timeout(180)
def test_actual_three_source_commit_binding_and_child_join_preserves_history(
    native_bridge, tmp_path, monkeypatch,
) -> None:
    root = tmp_path / "joined-owner"
    root.mkdir()
    with resident_join(root, monkeypatch, with_agenda=True) as peer:
        config = importlib.import_module("agent_worktrees.config")
        project_state = importlib.import_module("agent_worktrees.project_state")
        placement = importlib.import_module("agent_worktrees.worktree_data_store")
        store = importlib.import_module("agenda_store")
        publisher = importlib.import_module("agenda_publish")
        monkeypatch.setattr(config, "_home", lambda: root / "profile")
        monkeypatch.setattr(project_state, "_normalized_repository_remote", lambda project: f"network:example.invalid/{project}")
        checkout = peer["checkout"]
        settings = checkout / ".github/copilot/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "enabledPlugins": {"efforts@source-fixture": True},
            "extraKnownMarketplaces": {"source-fixture": {
                "source": {"source": "directory", "path": str(ROOT), "manifestName": "copilot-extensions"},
            }},
        }))
        bootstrap = root / "bootstrap/sitecustomize.py"
        bootstrap.write_text(bootstrap.read_text() + (
            f"\nconfig._home=lambda:Path({str(root / 'profile')!r})\n"
            "from agent_worktrees import project_state\n"
            "project_state._normalized_repository_remote=lambda project:f'network:example.invalid/{project}'\n"
        ))
        interpreter = root / "selected-python"
        prefix = (
            f"import sys;sys.path[:0]={[str(root / 'bootstrap'), *map(str, SOURCE_PATHS)]!r};"
            "import sitecustomize\n"
        )
        interpreter.write_text(
            f"#!{sys.executable} -I\nimport os,sys\n"
            f"prefix={prefix!r}\n"
            "if '-c' in sys.argv:\n"
            "    index=sys.argv.index('-c')\n"
            "    code=prefix+sys.argv[index+1]; args=sys.argv[index+2:]\n"
            "else:\n"
            "    assert sys.argv[1:3]==['-m','agent_worktrees']\n"
            "    code=prefix+'from agent_worktrees.__main__ import main;main(sys.argv[1:])'; args=sys.argv[3:]\n"
            "os.execv(sys.executable,[sys.executable,'-I','-B','-c',code,*args])\n",
        )
        interpreter.chmod(0o700)
        monkeypatch.setattr(companion.ec, "engine_base_command", lambda: [str(interpreter), "-m", "agent_worktrees"])
        allocated = placement.allocate_worktree_data(
            "example", "selected", creation_nonce="f" * 32,
            domain="agendas", lane="main", store_id="c" * 32,
        )
        home = Path(allocated.state_home)
        initialization = json.loads((ROOT / "plugins/efforts/skills/driving-effort-agendas/assets/init.context-qualified.example.json").read_text())
        initialization["change"]["worktree"] = "selected"
        store.mutate(home, initialization, session="head-a", initial_store_id="c" * 32)

        def data():
            return companion._CompanionData(
                worktree={**peer["row"], "repo": "example", "path": str(checkout)},
                sessions=[{"id": "head-a", "is_head": True}],
                bundle={"facts": {"claims": {"value": {"owner_ref": OWNER}, "confirmed": True}}},
            )

        monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: data())

        async def publish() -> None:
            result = await asyncio.to_thread(
                publisher.publish, home, [], provider_python=str(interpreter), provider_project="example",
            )
            assert result["state"] == "delivered", result

        async def check() -> None:
            app = companion.MuxCompanionApp(cwd=str(checkout))
            async with app.run_test(size=(110, 50)) as pilot:
                await until(pilot, lambda: app._data.agenda is not None)
                await publish()
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                timeline = app.query_one(SubwayTimeline)
                phase = JoinPhase(
                    "three-source-preparation",
                    JOIN_PREPARATION_SECONDS + bridge.BRIDGE_STARTUP_TIMEOUT + JOIN_COMMAND_CLEANUP
                    + 4 * (STARTUP_TIMEOUT + JOIN_COMMAND_CLEANUP),
                )
                await phase.wait(
                    lambda: {state.token.identity.provider for state in timeline.window.sources.values()} == set(Provider),
                    pilot.pause,
                    lambda: peer["failures"][0] if peer["failures"] else None,
                    lambda: {
                        "sources": list(timeline.window.sources),
                        "owner": str(app.query_one(".timeline-state", Static).content),
                        "bridge": str(app.query_one("#timeline-bridge-status", Static).content),
                        "agenda": str(app.query_one("#timeline-agenda-status", Static).content),
                        "stages": peer["stages"],
                    },
                )
                await until(pilot, lambda: app._rich_agenda_attachment.token is not None)
                assert "child: starting" in str(app.query_one("#timeline-bridge-status", Static).content)
                assert app._bridge_attachment.controller.reader_count == 1
                assert app._timeline_attachment.controller.reader_count == 1
                coarse = app._agenda_worker
                retained = tuple(timeline.window.events)
                peer["publish"]("Joined binding transition", 4)
                state = store.read(home)
                cursor = state["documents"]["cursor"]
                frame_id = cursor["frames"][-1]
                item_id = cursor["frame_state"][frame_id]["pending"][0]
                store.mutate(home, {
                    "schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
                    "operation_id": "joined-wait", "expected_revision": state["revision"],
                    "change": {
                        "operation": "wait", "frame_id": frame_id, "item_id": item_id,
                        "reason_kind": "external", "reason_ref": "joined-evidence",
                        "next_action": "Wait for the joined fixture",
                        "evidence": [{"criterion": "joined input", "kind": "measured",
                                      "ref": "joined-evidence", "inputs": "isolated fixture"}],
                    },
                }, session="head-a")
                await publish()
                native_bridge["db"].update_session_status("child", "idle", 3.0)
                native_bridge["changed"].set()
                await until(pilot, lambda: any(
                    event.source.provider is Provider.BRIDGE for event in timeline.window.events
                ))
                await until(pilot, lambda: sum(
                    event.source.provider is Provider.AGENDA for event in timeline.window.events
                ) == 2)
                await until(pilot, lambda: "Joined binding transition" in str(
                    app.query_one(".timeline-context", Static).content,
                ))
                assert all(event in timeline.window.events for event in retained)
                assert app._agenda_worker is coarse
                assert len([row for row in peer["trace"].read_text().splitlines() if "agenda-feed" in row]) == 1
                assert native_bridge["db"].get_session("child")["status"] == "idle"
                app._receive_agenda(
                    app._data, app._agenda_generation, app._agenda_cancel,
                    {**app._data.agenda, "availability": "head-changed", "observation": None}, None,
                )
                await until(pilot, lambda: app._bridge_attachment.controller is None)
                assert not timeline.window.events
                assert all(state.availability.value == "head-changed" for state in timeline.window.sources.values())
                assert "retained membership is not current" in str(app.query_one("#timeline-bridge-status", Static).content)
                assert native_bridge["db"].get_session("child")["status"] == "idle"
                await pilot.press("escape")
            assert app._bridge_attachment.controller is None
            assert app._rich_agenda_attachment._task is None
            await JoinPhase("joined-reader-retirement", JOIN_COMMAND_CLEANUP).wait(
                peer["settled"], asyncio.sleep,
                lambda: peer["failures"][0] if peer["failures"] else None,
                lambda: {"trace": peer["trace"].read_text(), "stages": peer["stages"]},
            )

        asyncio.run(check())
