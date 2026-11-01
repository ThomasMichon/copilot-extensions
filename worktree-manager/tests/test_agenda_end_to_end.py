"""Actual core commit -> publisher CLI -> resident -> held Manager -> Textual."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from threading import Event
from textwrap import indent
from typing import Any

import pytest
from rich.text import Text
from textual.widgets import Static, TabbedContent
from textual.worker import WorkerCancelled

from worktree_manager import agenda_transport, engine_client, mux_companion

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "plugins/efforts/skills/driving-effort-agendas"
# Manager's prepared venv supplies its declared dependencies. These additional
# first-party sources are explicit test closure, never an installed AW runtime.
SOURCE_PATHS = [
    ROOT / "plugins/agent-worktrees/src",
    SKILL / "scripts",
    ROOT / "libs/config-migrate/src",
    ROOT / "libs/lazy-cli-dispatch/src",
    ROOT / "libs/repository-policy/src",
    *[ROOT / "libs" / name / "src" for name in (
        "configuration-layers", "plugin-resolve", "agent-procutil", "dropin-registry",
        "plugin-activation", "zdd", "single-instance-lease", "work-coalescing-singleton",
        "remote-login-shell", "machine-transport", "worktree-identity", "update-telemetry",
        "update-driver",
    )],
]

_ENTRY = """
import argparse,json,os,sys
from pathlib import Path
configuration=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path[:0]=configuration["source_paths"]
from agent_worktrees import agenda_cli,config,locks,tracking,worktrees_client
from agent_worktrees.status_monitor_cutover import ControlClient

def client():
    return ControlClient(configuration["endpoint"],token=configuration["token"],timeout=2)

def locator(wid,repo=None):
    if wid != configuration["worktree"] or repo not in (None,configuration["project"]):
        raise ValueError("fixture tracking selection mismatch")
    return Path(configuration["records"])

config.project_name=lambda:configuration["project"]
config.tracking_dir=lambda:Path(configuration["records"])
tracking._owning_tracking_dir=locator
agenda_cli._control_client=client
worktrees_client._control_client=client
with Path(configuration["trace"]).open("a",encoding="utf-8") as trace:
    trace.write(json.dumps({"pid":os.getpid(),"birth":locks.process_start_time(os.getpid()),
        "argv":sys.argv[2:]})+"\\n")

if sys.argv[2:] == ["--resident-health"]:
    _HEALTH_PROBE_BODY
    sys.exit(0)

project_parser=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
project_parser.add_argument("--project")
project,argv=project_parser.parse_known_args(sys.argv[2:])
if project.project not in (None,configuration["project"]):
    raise ValueError("fixture project discovery mismatch")
parser=argparse.ArgumentParser(allow_abbrev=False)
commands=parser.add_subparsers(dest="command",required=True)
agenda_cli.add_parsers(commands)
status=commands.add_parser("status-segment",allow_abbrev=False)
status.add_argument("--json",action="store_true")
status.add_argument("--path",required=True)
lineage=commands.add_parser("list-sessions",allow_abbrev=False)
lineage.add_argument("--json",action="store_true")
lineage.add_argument("--worktree",required=True)
args=parser.parse_args(argv)
if args.command.startswith("agenda-"):
    sys.exit(args.func(args))

# Only current-worktree/project discovery is redirected. Both responses derive
# from the actual canonical record; no agenda observation or head is fabricated.
record=tracking.load_record(Path(configuration["records"])/(configuration["worktree"]+".yaml"))
if args.command == "status-segment":
    if Path(args.path).resolve() != Path(record.worktree_path).resolve():
        raise ValueError("fixture cwd discovery mismatch")
    result={"id":record.worktree_id,"repo":record.repo,"path":record.worktree_path,
        "branch":record.branch,"title":record.title,"status":record.status,"state":record.status}
else:
    if args.worktree != record.worktree_id:
        raise ValueError("fixture lineage discovery mismatch")
    result={"sessions":[{"id":entry.session_id,"state":entry.state,
        "is_head":entry.session_id == record.resolved_head_session}
        for entry in record.sessions or []]}
print(json.dumps(result))
"""


def request(change: dict[str, Any], revision: int, operation_id: str) -> dict[str, Any]:
    return {
        "schema": "efforts.agenda-request", "version": 1,
        "expected_revision": revision, "operation_id": operation_id, "change": change,
    }


def widget(app: mux_companion.MuxCompanionApp) -> str:
    content = app.query_one("#agenda", Static).content
    return content.plain if isinstance(content, Text) else content.label.plain


async def eventually(pilot, predicate, *, detail: str) -> None:
    for _ in range(120):
        if predicate():
            return
        await pilot.pause(0.025)
    assert predicate(), detail


@pytest.fixture
def chain(tmp_path: Path, monkeypatch, request):
    for source in reversed(SOURCE_PATHS):
        assert source.is_dir(), f"missing declared source closure: {source}"
        monkeypatch.syspath_prepend(str(source))
    private_home = tmp_path / "profile"
    records, workdir, home = tmp_path / "records", tmp_path / "worktree", tmp_path / "agenda"
    for directory in (private_home, records, workdir, home):
        directory.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(private_home))

    store = importlib.import_module("agenda_store")
    publisher = importlib.import_module("agenda_publish")
    tracking = importlib.import_module("agent_worktrees.tracking")
    config = importlib.import_module("agent_worktrees.config")
    locks = importlib.import_module("agent_worktrees.locks")
    runtime = importlib.import_module("agent_worktrees.worktrees_runtime")
    control = importlib.import_module("agent_worktrees.status_monitor_cutover")
    monkeypatch.setattr(config, "project_name", lambda: "example")
    monkeypatch.setattr(config, "tracking_dir", lambda: records)

    def locator(wid, repo=None):
        assert wid == "selected" and repo in (None, "example")
        return records

    monkeypatch.setattr(tracking, "_owning_tracking_dir", locator)
    record_path = records / "selected.yaml"
    record = tracking.WorktreeRecord(
        worktree_id="selected", branch="worktree/selected", worktree_path=str(workdir),
        repo="example", machine="synthetic", platform="linux",
        started_at="2026-11-01T07:00:00Z", last_resumed_at=None,
        resume_count=0, title="Synthetic agenda chain", status="active", completed_at=None,
        sessions=[tracking.SessionEntry("head-a", "2026-11-01T07:00:00Z")],
        head_session="head-a",
    )
    tracking.save_record(record, record_path)
    assert tracking.load_record(record_path).resolved_head_session == "head-a"
    assert record_path.with_suffix(".lock").exists(), "canonical record lock was not used"

    wake = Event()
    owner = runtime.WorktreesRuntime(
        tmp_path / "resident.sqlite3", elected=lambda: True, wake=wake,
    )
    owner.activate()
    if getattr(request, "param", True):
        owner.publish("example", [{"id": "selected"}], observed_at=1)
    wake.clear()
    actions = []

    def handle(action, payload):
        actions.append((action, deepcopy(payload)))
        if action == "health":
            return owner.health()
        if action == "worktrees-read":
            return owner.handle(payload)
        return owner.agenda_handle(action, payload)

    server = control.ControlServer(
        handle, token="synthetic-agenda-token", stream_handle=owner.stream,
    )
    server.start()
    endpoint = f"http://127.0.0.1:{server.port}"
    trace, configuration = tmp_path / "children.jsonl", tmp_path / "discovery.json"
    metadata = {
        "source_paths": [str(source) for source in SOURCE_PATHS],
        "endpoint": endpoint, "token": server.token, "records": str(records),
        "trace": str(trace), "project": "example", "worktree": "selected",
    }

    def endpoint_discovery(value):
        metadata["endpoint"] = value
        pending = configuration.with_suffix(".next")
        pending.write_text(json.dumps(metadata), encoding="utf-8")
        pending.replace(configuration)

    endpoint_discovery(endpoint)
    entry = tmp_path / "owned_provider.py"
    entry.write_text(
        _ENTRY.replace("    _HEALTH_PROBE_BODY", indent(agenda_transport._HEALTH_PROBE.strip(), "    ")),
        encoding="utf-8",
    )
    argv = [sys.executable, str(entry), str(configuration)]
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: list(argv))
    monkeypatch.setattr(agenda_transport, "health_argv", lambda base: [*base, "--resident-health"])
    if getattr(request, "param", None) == "context-qualified":
        bootstrap = tmp_path / "provider-binding"
        bootstrap.mkdir()
        binding = (
            "import json,sys\nfrom pathlib import Path\n"
            f"configuration=json.loads(Path({str(configuration)!r}).read_text(encoding='utf-8'))\n"
            "sys.path[:0]=configuration['source_paths']\n"
            "from agent_worktrees import agenda_cli,config,locks,tracking,worktrees_client\n"
            "from agent_worktrees.status_monitor_cutover import ControlClient\n"
            "def client():\n"
            "    return ControlClient(configuration['endpoint'],token=configuration['token'],timeout=2)\n"
            "def locator(wid,repo=None):\n"
            "    if wid != configuration['worktree'] or repo not in (None,configuration['project']):\n"
            "        raise ValueError('fixture tracking selection mismatch')\n"
            "    return Path(configuration['records'])\n"
            "config.project_name=lambda:configuration['project']\n"
            "tracking._owning_tracking_dir=locator\n"
            "agenda_cli._control_client=client\n"
            "worktrees_client._control_client=client\n"
            "with Path(configuration['trace']).open('a',encoding='utf-8') as trace:\n"
            "    trace.write(json.dumps({'pid':__import__('os').getpid(),"
            "'birth':locks.process_start_time(__import__('os').getpid()),'argv':sys.argv})+'\\n')\n"
        )
        (bootstrap / "sitecustomize.py").write_text(binding, encoding="utf-8")
        inherited = os.environ.get("PYTHONPATH")
        monkeypatch.setenv(
            "PYTHONPATH", os.pathsep.join([str(bootstrap), *([inherited] if inherited else [])]),
        )

    board = json.loads((SKILL / "assets/agenda.example.json").read_text())
    cursor = json.loads((SKILL / "assets/cursor.example.json").read_text())
    cursor["worktree"] = record.worktree_id
    documents = {"cursor": cursor, "boards": {"agenda.json": board}}

    def settled(*, argv=None):
        if not trace.exists():
            return argv is None
        children = [
            child for child in map(json.loads, trace.read_text().splitlines())
            if argv is None or child["argv"] == argv
        ]
        if argv is not None and not children:
            return False
        return all(
            not locks.pid_alive(child["pid"])
            or locks.process_start_time(child["pid"]) != child["birth"]
            for child in children
        )

    try:
        yield {
            "store": store, "publisher": publisher, "home": home, "documents": documents,
            "tracking": tracking, "record_path": record_path, "workdir": workdir,
            "owner": owner, "server": server, "argv": argv, "endpoint": endpoint,
            "endpoint_discovery": endpoint_discovery, "actions": actions,
            "settled": settled, "trace": trace, "wake": wake,
            "provider_python": sys.executable, "configuration": configuration,
        }
    finally:
        owner.close_admission("closed")
        assert owner.agendas.feed.wait_closed(3), "actual held subscriber did not retire"
        owner.shutdown()
        server.close()
        deadline = time.monotonic() + 3
        while not settled() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert settled(), "a birth-qualified fixture CLI process survived cleanup"


def test_actual_commits_publish_to_resident_and_textual_without_replay(chain):
    store, publisher, home = chain["store"], chain["publisher"], chain["home"]
    owner, server = chain["owner"], chain["server"]
    argv = [*chain["argv"], "agenda-publish", "--project", "example", "--json"]
    initial = request({"operation": "init", "documents": chain["documents"]}, 0, "init")
    identity = {"session": "head-a", "actor": "synthetic-test", "publisher_configured": True}

    async def publish():
        return await asyncio.to_thread(publisher.publish, home, argv)

    async def receive(pilot, app, revision):
        await eventually(
            pilot,
            lambda: app._data.agenda is not None
            and app._data.agenda.get("observation") is not None
            and app._data.agenda["observation"]["revision"] == revision,
            detail=f"actual publication revision {revision} did not reach Textual: {widget(app)}",
        )
        assert app._data.agenda["observation"] == store.delivery_record(home)[1]

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail="actual resident first snapshot did not reach Manager",
            )
            data, worker = app._data, app._agenda_worker
            assert data.agenda["availability"] == "not-published"
            assert data.agenda["observation"] is None
            app.query_one("#views", TabbedContent).active = "agenda-tab"
            await pilot.pause()
            assert app.query_one("#agenda", Static).region.height > 0

            accepted, newly_accepted = store.mutate(home, initial, **identity)
            original = deepcopy(accepted)
            assert newly_accepted and accepted["persisted"]
            assert accepted["revision"] == 1
            assert data.agenda["availability"] == "not-published", "commit alone fabricated delivery"
            delivery = await publish()
            assert delivery["state"] == "delivered", (
                "actual core -> publisher -> provider contract blocked: "
                f"{delivery}; unmodified source={store.delivery_record(home)[1]['source']}"
            )
            await receive(pilot, app, 1)
            assert "Parked: 0" in widget(app)

            parked = request(
                {"operation": "park", "ref": "fixture-follow-up", "reason": "nonblocking"},
                1, "park",
            )
            result, fresh = store.mutate(home, parked, **identity)
            assert fresh and result["revision"] == 2
            assert data.agenda["observation"]["revision"] == 1
            assert (await publish())["state"] == "delivered"
            await receive(pilot, app, 2)
            assert "Parked: 1" in widget(app)
            assert app._data is data and app._agenda_worker is worker
            assert store.mutate(home, initial, **identity) == (original, False)
            assert len(store.history(home)) == 2

            # Reserve a private non-listening endpoint; no production route or
            # port-reuse race can turn the intended offline publication into success.
            with socket.socket() as offline:
                offline.bind(("127.0.0.1", 0))
                chain["endpoint_discovery"](f"http://127.0.0.1:{offline.getsockname()[1]}")
                blocked = request({
                    "operation": "block", "reason": "fixture prerequisite",
                    "next_action": "Review fixture prerequisite",
                    "evidence": [{"criterion": "Retain current root", "ref": "synthetic",
                                  "inputs": "revision 2", "kind": "measured"}],
                }, 2, "block")
                blocked_result, fresh = store.mutate(home, blocked, **identity)
                assert fresh and blocked_result["revision"] == 3
                pending = await publish()
                assert pending["state"] == "pending" and pending["error"]
                assert store.read(home)["revision"] == 3
                assert store.mutate(home, blocked, **identity) == (blocked_result, False)
                assert store.mutate(home, initial, **identity) == (original, False)
                assert len(store.history(home)) == 3
                assert data.agenda["observation"]["revision"] == 2

            chain["endpoint_discovery"](chain["endpoint"])
            assert (await publish())["state"] == "delivered"
            await receive(pilot, app, 3)
            assert "Review fixture prerequisite" in widget(app)
            assert app._data is data and app._agenda_worker is worker
            assert store.read(home)["revision"] == 3 and len(store.history(home)) == 3
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0
                and server.current_read_count("agenda-feed") == 0 and chain["settled"](),
                detail="actual cancellation did not settle held reader and birth-qualified children",
            )

        # Reopen on the real current snapshot, then change the actual canonical
        # record through its locked writer; the resident notification must clear it.
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await receive(pilot, app, 3)
            current = chain["tracking"].load_record(chain["record_path"])
            current.sessions.append(chain["tracking"].SessionEntry("head-b", "2026-11-01T08:00:00Z"))
            current.head_session = "head-b"
            current.head_revision += 1
            current.lifecycle_revision += 1
            chain["tracking"].save_record(current, chain["record_path"])
            await eventually(
                pilot, lambda: app._data.agenda is not None
                and app._data.agenda["availability"] == "head-changed",
                detail=f"actual canonical head change did not invalidate Textual: {widget(app)}",
            )
            assert app._data.agenda["observation"] is None
            assert "Refresh required" in widget(app)
            assert "Review fixture prerequisite" not in widget(app)
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="head invalidation retained an owned feed/process",
            )

        # A fresh, correctly selected head must keep its subscription while
        # the durable cache still contains its predecessor's observation.
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail=f"new-head snapshot did not reach Textual: {widget(app)}",
            )
            data, worker = app._data, app._agenda_worker
            assert data.agenda["session_id"] == "head-b"
            assert data.agenda["availability"] == "not-published"
            assert data.agenda["observation"] is None
            assert owner.agendas.feed.active_count() == 1
            changed = request(
                {"operation": "park", "ref": "new-head-follow-up", "reason": "nonblocking"},
                3, "head-b-park",
            )
            accepted, fresh = store.mutate(
                home, changed, session="head-b", actor="synthetic-test",
                publisher_configured=True,
            )
            assert fresh and accepted["revision"] == 4
            assert (await publish())["state"] == "delivered"
            await receive(pilot, app, 4)
            assert data.agenda["observation"]["source"]["session"] == "head-b"
            assert "Parked: 2" in widget(app)
            assert app._data is data and app._agenda_worker is worker
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="new-head first publication retained a reader or child after quit",
            )
        assert store.mutate(home, initial, **identity) == (original, False)
        assert len(store.history(home)) == 4
        assert sum(action == "health" for action, _ in chain["actions"]) >= 7
        assert sum(action == "agenda-publish" for action, _ in chain["actions"]) == 4

    asyncio.run(check())


def context_request(change, revision, operation_id):
    return {
        "schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
        "expected_revision": revision, "operation_id": operation_id, "change": change,
    }


def context_board():
    names = (
        "parent", "sibling-a", "sibling-b", "investigate-a", "investigate-b",
        "nested", "tail", "authorized-other",
    )
    return {
        "version": 2, "source": {"kind": "task", "ref": "fixture-assignment"}, "revision": 1,
        "items": {name: {
            "source_ref": f"fixture/{name}", "scope_ref": "assigned-fixture", "action": f"Run {name}",
            "exit": [f"{name} result recorded"],
            "on": {"done": "advance", "blocked": "wait", "no-progress": "replan"},
            "relevant_subjects": ["dataset"],
        } for name in names},
        "order": ["parent", "sibling-a", "sibling-b"],
    }


def context_facts(value):
    return {"facts": [{
        "kind": "input", "subject": "dataset", "value": value, "evidence_ref": "fixture-input",
    }]}


def context_head(state):
    cursor = state["documents"]["cursor"]
    frame = cursor["frames"][-1]
    item = cursor["frame_state"][frame]["pending"][0]
    return frame, item, cursor["item_state"][item]["current_attempt_id"]


def context_cli(
    chain, tmp_path, command, request=None, *, deliver=False, expected_exit=0, session="head-a",
):
    argv = [
        sys.executable, "-B", str(SKILL / "scripts" / "agenda_cli.py"),
        command, "--state-home", str(chain["home"]),
    ]
    if request is not None:
        path = tmp_path / f"{request['operation_id']}.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        argv += ["--request", str(path), "--session", session, "--actor", "synthetic-test"]
        if command == "init":
            argv += ["--mode", "context-qualified"]
    if deliver:
        argv += ["--publisher-python", chain["provider_python"], "--publisher-project", "example"]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected_exit, f"actual core CLI failed: {result.stderr}"
    if expected_exit:
        assert result.stderr and not result.stdout
        return {"exit_code": result.returncode, "diagnostic": result.stderr}
    return json.loads(result.stdout)


@pytest.mark.parametrize("chain", ["context-qualified"], indirect=True)
@pytest.mark.timeout(120)
def test_context_qualified_core_cli_subqueues_revisits_and_delivery_reach_textual(chain, tmp_path):
    store, home, owner = chain["store"], chain["home"], chain["owner"]
    initial = context_request({
        "operation": "init", "worktree": "selected", "board": context_board(),
        "baseline_context": context_facts({"content": "initial"}),
        "resource_caps": {"max_depth": 4, "max_pending_items": 32,
                          "max_deferred_frames": 4, "max_no_parent_progress": 4},
    }, 0, "context-init")

    def cli(command, request=None, *, deliver=False):
        return context_cli(chain, tmp_path, command, request, deliver=deliver)

    def read():
        return cli("read")

    def evidence(criterion):
        return [{"criterion": criterion, "kind": "measured",
                 "ref": "synthetic-result", "inputs": "fixture revision"}]

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail=f"version-2 first snapshot unavailable: {widget(app)}",
            )
            data, worker = app._data, app._agenda_worker
            assert data.agenda["contract_version"] == 2
            assert data.agenda["availability"] == "not-published"
            initial_output = await asyncio.to_thread(cli, "init", initial)
            accepted = deepcopy(initial_output["result"])
            state = await asyncio.to_thread(read)
            assert state["snapshot"]["cursor_version"] == 4
            assert data.agenda["availability"] == "not-published"
            root_frame, root_item, root_attempt = context_head(state)
            original_records = deepcopy(state["records"])
            original_source = deepcopy(state["documents"]["boards"]["root"]["source"])
            original_root = state["snapshot"]["root"]

            async def publish():
                output = await asyncio.to_thread(cli, "publish", deliver=True)
                assert output["delivery"]["state"] == "delivered", (
                    f"actual core-v2 provider adapter rejected the committed body: {output}"
                )
                return output

            async def receive(state):
                await eventually(
                    pilot, lambda: data.agenda is not None
                    and data.agenda["observation"] is not None
                    and data.agenda["observation"]["revision"] == state["revision"],
                    detail=f"actual committed revision {state['revision']} did not reach Textual: {widget(app)}",
                )
                assert data.agenda["contract_version"] == 2
                assert data.agenda["observation"] == store.delivery_record(home)[1]
                assert data.agenda["observation"]["version"] == 2
                assert data.agenda["observation"]["snapshot"] == state["snapshot"]
                assert app._data is data and app._agenda_worker is worker
                for identity, record in original_records.items():
                    assert state["records"][identity] == record
                assert state["documents"]["boards"]["root"]["source"] == original_source

            await publish()
            await receive(state)
            assert "fixture-assignment / parent" in widget(app)

            async def change(operation):
                before = await asyncio.to_thread(read)
                request = context_request(operation, before["revision"], f"context-op-{before['revision']}")
                output = await asyncio.to_thread(cli, "update", request)
                current = await asyncio.to_thread(read)
                assert output["result"]["persisted"] and current["revision"] == before["revision"] + 1
                assert data.agenda["observation"]["revision"] == before["revision"]
                await publish()
                await receive(current)
                return output["result"], current

            async def outcome(value="done"):
                current = await asyncio.to_thread(read)
                frame, item, attempt = context_head(current)
                return await change({
                    "operation": "outcome", "frame_id": frame, "item_id": item,
                    "attempt_id": attempt, "outcome": value,
                    "evidence": evidence(current["snapshot"]["exit_criteria"][0]),
                })

            _, state = await change({
                "operation": "append", "frame_id": root_frame, "items": [{"definition_ref": "tail"}],
                "assignment_ref": "assigned-tail", "evidence": evidence("retain original tail order"),
            })
            pending = list(state["documents"]["cursor"]["frame_state"][root_frame]["pending"])
            assert len(pending) == 4 and pending[0] == root_item
            assert state["snapshot"]["queued"] == [
                f"{root_frame}: Run sibling-a", f"{root_frame}: Run sibling-b",
                f"{root_frame}: Run tail",
            ]
            await outcome("no-progress")
            expanded, state = await change({
                "operation": "expand", "frame_id": root_frame, "item_id": root_item,
                "attempt_id": root_attempt,
                "items": [{"definition_ref": "investigate-a"}, {"definition_ref": "investigate-b"}],
                "continuation": {"parent_item_id": root_item, "next_action": "Reassess parent with child evidence"},
                "evidence": evidence("bounded investigation"),
            })
            child_frame, child_item, child_attempt = context_head(state)
            assert state["documents"]["cursor"]["frame_state"][root_frame]["pending"] == pending
            nested, state = await change({
                "operation": "expand", "frame_id": child_frame, "item_id": child_item,
                "attempt_id": child_attempt, "items": [{"definition_ref": "nested"}],
                "continuation": {"parent_item_id": child_item, "next_action": "Use nested result"},
                "evidence": evidence("nested investigation"),
            })
            assert len(state["documents"]["cursor"]["frames"]) == 3
            assert len(state["snapshot"]["stack"]) == 3
            assert state["snapshot"]["root"] == original_root
            queued = "\n".join(state["snapshot"]["queued"])
            for action in ("Run sibling-a", "Run sibling-b", "Run tail", "Run investigate-b"):
                assert action in queued
            await outcome()
            returned, state = await change({
                "operation": "return", "frame_id": nested["transition"]["frame_id"],
                "expansion_id": nested["transition"]["expansion_id"], "acceptance_ref": "nested-accepted",
                "evidence": evidence("nested acceptance"),
            })
            assert context_head(state) == (child_frame, child_item, child_attempt)
            assert returned["transition"]["attempt_id"] == child_attempt
            assert state["snapshot"]["next_action"] == returned["transition"]["next_action"]
            await outcome()
            await outcome()
            returned, state = await change({
                "operation": "return", "frame_id": child_frame,
                "expansion_id": expanded["transition"]["expansion_id"], "acceptance_ref": "child-accepted",
                "evidence": evidence("child acceptance"),
            })
            assert context_head(state) == (root_frame, root_item, root_attempt)
            assert state["documents"]["cursor"]["frame_state"][root_frame]["pending"] == pending
            assert returned["transition"]["attempt_id"] == root_attempt
            assert state["snapshot"]["next_action"] == returned["transition"]["next_action"]
            assert state["documents"]["cursor"]["item_state"][root_item]["visits"] == 1

            for value in range(1, 6):
                if value > 1:
                    await outcome("no-progress")
                frame, item, previous = context_head(state if value == 1 else await asyncio.to_thread(read))
                revisited, state = await change({
                    "operation": "revisit", "frame_id": frame, "item_id": item,
                    "predecessor_attempt_id": previous,
                    "context": context_facts({"content": f"corrected-{value}"}),
                    "judgment": {"qualification": "agent-judgment", "changed_subjects": ["dataset"],
                                 "causal_change": "Changed relevant input enables the same command.",
                                 "evidence_refs": ["fixture-input"]},
                    "approach": {"action": "Run parent"},
                })
                transition = revisited["transition"]
                assert transition["decision"] == "revisit-admitted"
                assert transition["attempt_id"] != previous
                assert state["records"][transition["attempt_id"]]["body"]["approach"]["action"] == "Run parent"
            assert state["documents"]["cursor"]["item_state"][root_item]["visits"] == 6
            await outcome("no-progress")
            state = await asyncio.to_thread(read)
            frame, item, previous = context_head(state)
            refused, state = await change({
                "operation": "revisit", "frame_id": frame, "item_id": item,
                "predecessor_attempt_id": previous,
                "context": context_facts({"content": "corrected-5", "uuid": "cosmetic", "timestamp": "later"}),
                "judgment": {"qualification": "agent-judgment", "changed_subjects": ["dataset"],
                             "causal_change": "New prose does not change the current facts.",
                             "evidence_refs": ["fixture-input"]},
                "approach": {"action": "Run parent"},
            })
            assert refused["transition"]["decision"] == "not-admitted"
            assert refused["transition"]["reason_code"] == "duplicate-context"
            assert refused["transition"]["attempt_id"] is None
            assert context_head(state)[2] == previous
            assert state["documents"]["cursor"]["item_state"][root_item]["visits"] == 6
            assert state["snapshot"]["status"] == "replanning" and "replanning" in widget(app)
            _, state = await change({
                "operation": "refine", "board_ref": "root", "expected_definition_revision": 1,
                "patches": [{"definition_id": "parent", "label": "Retained parent",
                             "source_aliases_append": ["fixture/parent-alias"]}],
                "evidence": evidence("retain original definition and scope"),
            })
            definition = state["documents"]["boards"]["root"]["items"]["parent"]
            assert definition["source_ref"] == "fixture/parent"
            assert definition["scope_ref"] == "assigned-fixture"
            assert definition["source_aliases"] == ["fixture/parent-alias"]
            before_defer = deepcopy(state["documents"]["cursor"])
            revisit_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            deferred, state = await change({
                "operation": "defer", "frame_id": root_frame,
                "decision_ref": "fixture-decision", "decision_packet_ref": "fixture-proposal",
                "revisit_at": revisit_at,
                "evidence": evidence("preserve stack while awaiting acceptance"),
            })
            assert state["documents"]["cursor"]["frames"] == []
            assert state["snapshot"]["root"] == original_root
            assert len(state["snapshot"]["deferred"]) == 1
            assert datetime.fromisoformat(
                state["snapshot"]["next_revisit_at"].replace("Z", "+00:00"),
            ) == datetime.fromisoformat(revisit_at)
            assert state["snapshot"]["due_rechecks"] == []
            before_other_root = await asyncio.to_thread(read)
            refused_root = context_request({
                "operation": "assign-root", "items": [{"definition_ref": "parent"}],
                "assignment_ref": "cannot-relabel-original",
                "baseline_context": context_facts({"content": "independent-work"}),
                "evidence": evidence("original item identity must not be replaced"),
            }, state["revision"], "context-refuse-root-reuse")
            refusal = await asyncio.to_thread(
                context_cli, chain, tmp_path, "update", refused_root, expected_exit=2,
            )
            assert refusal["exit_code"] == 2
            assert await asyncio.to_thread(read) == before_other_root
            assigned, state = await change({
                "operation": "assign-root", "items": [{"definition_ref": "authorized-other"}],
                "assignment_ref": "explicit-other-assignment",
                "baseline_context": context_facts({"content": "independent-work"}),
                "evidence": evidence("separate assigned root with original deferred stack retained"),
            })
            other_frame = assigned["transition"]["frame_id"]
            assert assigned["transition"]["decision"] == "root-assigned"
            cursor = state["documents"]["cursor"]
            assert cursor["frames"] == [other_frame]
            assert cursor["deferred"][0]["id"] == deferred["transition"]["deferred_id"]
            assert cursor["frame_state"][root_frame]["pending"] == pending
            assert cursor["item_state"][root_item]["current_attempt_id"] == previous
            assert cursor["item_state"][root_item]["attempts"] == before_defer["item_state"][root_item]["attempts"]
            assert len(state["snapshot"]["deferred"]) == 1
            assert state["snapshot"]["deferred"][0]["root"] == original_root
            other_origin = state["records"][cursor["frame_state"][other_frame]["origin_id"]]["body"]
            root_origin = state["records"][cursor["frame_state"][root_frame]["origin_id"]]["body"]
            assert other_origin["root_id"] != root_origin["root_id"]
            assert "authorized-other" in widget(app)
            await outcome()
            state = await asyncio.to_thread(read)
            assert state["documents"]["cursor"]["frames"] == []
            assert len(state["snapshot"]["deferred"]) == 1
            assert state["snapshot"]["root"] == original_root
            restored, state = await change({
                "operation": "recover", "deferred_id": deferred["transition"]["deferred_id"],
                "recheck_ref": "fixture-current-recheck", "acceptance_ref": "fixture-current-acceptance",
                "attempt_policy": "resume-only", "evidence": evidence("retain original attempt"),
            })
            assert restored["transition"]["attempt_id"] == previous
            assert state["documents"]["cursor"]["frames"] == before_defer["frames"]
            assert state["documents"]["cursor"]["frame_state"][root_frame]["pending"] == pending
            assert state["documents"]["cursor"]["item_state"][root_item]["attempts"] == (
                before_defer["item_state"][root_item]["attempts"]
            )

            with socket.socket() as offline:
                offline.bind(("127.0.0.1", 0))
                chain["endpoint_discovery"](f"http://127.0.0.1:{offline.getsockname()[1]}")
                waiting = context_request({
                    "operation": "wait", "frame_id": root_frame, "item_id": root_item,
                    "reason_kind": "permission", "reason_ref": "fixture-permission-pending",
                    "next_action": "Read current permission", "evidence": evidence("permission scope"),
                }, state["revision"], "context-wait")
                wait_output = await asyncio.to_thread(cli, "update", waiting)
                pending_delivery = await asyncio.to_thread(cli, "publish", deliver=True)
                assert wait_output["result"]["persisted"] and pending_delivery["delivery"]["state"] == "pending"
                pending_state = await asyncio.to_thread(read)
                assert data.agenda["observation"]["revision"] == state["revision"]
            chain["endpoint_discovery"](chain["endpoint"])
            before_retry = deepcopy({key: pending_state[key] for key in (
                "store_id", "revision", "documents", "metadata", "records", "snapshot",
            )})
            await publish()
            await receive(pending_state)
            after_retry = await asyncio.to_thread(read)
            assert after_retry["snapshot"]["next_action"] == waiting["change"]["next_action"]
            assert {key: after_retry[key] for key in before_retry} == before_retry
            duplicate = await asyncio.to_thread(cli, "init", initial)
            assert duplicate["result"] == accepted
            assert await asyncio.to_thread(read) == after_retry
            history = await asyncio.to_thread(cli, "history")
            assert len(history["history"]) == after_retry["revision"]
            assert data.agenda["observation"]["source"]["store_id"] == accepted["store_id"]
            assert data.agenda["observation"]["source"]["session"] == "head-a"
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="context-qualified witness retained a reader or child after cancellation",
            )
    asyncio.run(check())


@pytest.mark.parametrize("chain", ["context-qualified"], indirect=True)
def test_context_qualified_old_resident_keeps_delivery_pending_without_mutation_replay(
    chain, monkeypatch, tmp_path,
):
    owner = chain["owner"]
    health = owner.health

    def old_health():
        result = health()
        result["capabilities"] = [
            capability for capability in result["capabilities"] if capability != "worktree-agendas-v2"
        ]
        return result

    monkeypatch.setattr(owner, "health", old_health)
    initial = context_request({
        "operation": "init", "worktree": "selected", "board": context_board(),
        "baseline_context": context_facts({"content": "initial"}),
        "resource_caps": {"max_depth": 4, "max_pending_items": 32,
                          "max_deferred_frames": 4, "max_no_parent_progress": 4},
    }, 0, "context-init")

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail=f"legacy held snapshot unavailable: {widget(app)}",
            )
            assert app._data.agenda["contract_version"] == 1
            assert app._data.agenda["availability"] == "not-published"
            accepted = await asyncio.to_thread(context_cli, chain, tmp_path, "init", initial)
            state = await asyncio.to_thread(context_cli, chain, tmp_path, "read")
            assert accepted["result"]["persisted"] and state["snapshot"]["cursor_version"] == 4
            for _ in range(2):
                delivered = await asyncio.to_thread(
                    context_cli, chain, tmp_path, "publish", deliver=True,
                )
                assert delivered["delivery"]["state"] == "pending"
                assert delivered["delivery"]["error"]["code"] == "unsupported"
                after = await asyncio.to_thread(context_cli, chain, tmp_path, "read")
                assert {
                    key: after[key] for key in after if key != "delivery"
                } == {key: state[key] for key in state if key != "delivery"}
            assert app._data.agenda["observation"] is None
            assert not any(action == "agenda-publish" for action, _ in chain["actions"])
            assert len(chain["store"].history(chain["home"])) == 1
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="old-provider pending delivery retained a reader or child",
            )
    asyncio.run(check())


def context_init():
    return context_request({
        "operation": "init", "worktree": "selected", "board": context_board(),
        "baseline_context": context_facts({"content": "initial"}),
        "resource_caps": {"max_depth": 4, "max_pending_items": 32,
                          "max_deferred_frames": 4, "max_no_parent_progress": 4},
    }, 0, "context-init")


@pytest.mark.parametrize("chain", ["context-qualified"], indirect=True)
def test_context_qualified_reconnect_reads_current_body_and_rejects_stale_publication(
    chain, monkeypatch, tmp_path,
):
    from agent_worktrees.status_monitor_cutover import ControlClient

    owner, server, store, home = chain["owner"], chain["server"], chain["store"], chain["home"]
    context_cli(chain, tmp_path, "init", context_init())
    assert context_cli(chain, tmp_path, "publish", deliver=True)["delivery"]["state"] == "delivered"
    original_body = deepcopy(store.delivery_record(home)[1])
    original_stream = server.stream_handle
    disconnect = Event()
    calls = []

    def stream(action, payload, disconnected):
        if action != "agenda-feed":
            return original_stream(action, payload, disconnected)
        calls.append(payload["contract_version"])
        if len(calls) != 1:
            return original_stream(action, payload, disconnected)

        def first():
            inner = original_stream(action, payload, disconnected)
            try:
                yield next(inner)
                assert disconnect.wait(15), "test did not close the first actual v2 stream"
            finally:
                inner.close()
        return first()

    monkeypatch.setattr(server, "stream_handle", stream)

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        try:
            async with app.run_test() as pilot:
                await eventually(
                    pilot, lambda: app._data.agenda is not None,
                    detail=f"first v2 observation unavailable: {widget(app)}",
                )
                data, worker = app._data, app._agenda_worker
                assert data.agenda["observation"] == original_body
                state = await asyncio.to_thread(context_cli, chain, tmp_path, "read")
                frame, _item, _attempt = context_head(state)
                appended = context_request({
                    "operation": "append", "frame_id": frame, "items": [{"definition_ref": "tail"}],
                    "assignment_ref": "tail-during-disconnect",
                    "evidence": [{"criterion": "retain queue", "kind": "measured",
                                  "ref": "synthetic", "inputs": "revision 1"}],
                }, state["revision"], "context-disconnect-append")
                await asyncio.to_thread(context_cli, chain, tmp_path, "update", appended)
                delivery = await asyncio.to_thread(context_cli, chain, tmp_path, "publish", deliver=True)
                assert delivery["delivery"]["state"] == "delivered"
                current_body = deepcopy(store.delivery_record(home)[1])
                assert data.agenda["observation"] == original_body
                disconnect.set()
                await eventually(
                    pilot, lambda: data.agenda is not None
                    and data.agenda["observation"] == current_body,
                    detail=f"v2 reconnect did not admit a current snapshot: {widget(app)}",
                )
                assert data.agenda["type"] == "snapshot" and data.agenda["contract_version"] == 2
                assert app._data is data and app._agenda_worker is worker
                assert calls == [2, 2]
                client = ControlClient(chain["endpoint"], token=server.token)
                rejected = await asyncio.to_thread(client._request, "agenda-publish", {
                    "contract_version": 2, "project": "example",
                    "expected_generation": owner.store.generation, "observation": original_body,
                })
                assert rejected["ok"] is False and rejected["error"]["code"] == "stale-revision"
                assert owner.agendas.read({
                    "contract_version": 2, "project": "example",
                    "worktree_id": "selected", "session_id": "head-a",
                })["observation"] == current_body
                assert len(store.history(home)) == 2
                await pilot.press("escape")
                await eventually(
                    pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                    detail="v2 reconnect retained an owned reader or child",
                )
        finally:
            disconnect.set()
    asyncio.run(check())


@pytest.mark.parametrize("chain", ["context-qualified"], indirect=True)
def test_context_qualified_new_head_waits_for_its_first_publication_and_fences_late_source(
    chain, tmp_path,
):
    from agent_worktrees.status_monitor_cutover import ControlClient

    owner, store, home = chain["owner"], chain["store"], chain["home"]
    context_cli(chain, tmp_path, "init", context_init())
    assert context_cli(chain, tmp_path, "publish", deliver=True)["delivery"]["state"] == "delivered"
    old_body = deepcopy(store.delivery_record(home)[1])

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail="original v2 head observation unavailable",
            )
            record = chain["tracking"].load_record(chain["record_path"])
            record.sessions.append(chain["tracking"].SessionEntry("head-b", "2026-11-01T08:00:00Z"))
            record.head_session = "head-b"
            record.head_revision += 1
            record.lifecycle_revision += 1
            chain["tracking"].save_record(record, chain["record_path"])
            await eventually(
                pilot, lambda: app._data.agenda is not None
                and app._data.agenda["availability"] == "head-changed",
                detail="actual canonical head change did not invalidate v2 publication",
            )
            assert app._data.agenda["observation"] is None
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="old v2 head reader did not settle before admitting a new UI",
            )
            await pilot.press("escape")
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail="new-head v2 snapshot unavailable",
            )
            assert app._data.agenda["session_id"] == "head-b"
            assert app._data.agenda["availability"] == "not-published"
            assert app._data.agenda["observation"] is None and owner.agendas.feed.active_count() == 1
            state = await asyncio.to_thread(context_cli, chain, tmp_path, "read")
            frame, _item, _attempt = context_head(state)
            appended = context_request({
                "operation": "append", "frame_id": frame, "items": [{"definition_ref": "tail"}],
                "assignment_ref": "new-head-tail",
                "evidence": [{"criterion": "retain previous queue", "kind": "measured",
                              "ref": "synthetic", "inputs": "revision 1"}],
            }, state["revision"], "context-new-head-append")
            await asyncio.to_thread(
                context_cli, chain, tmp_path, "update", appended, session="head-b",
            )
            delivery = await asyncio.to_thread(context_cli, chain, tmp_path, "publish", deliver=True)
            assert delivery["delivery"]["state"] == "delivered"
            current_body = store.delivery_record(home)[1]
            await eventually(
                pilot, lambda: app._data.agenda is not None
                and app._data.agenda["observation"] == current_body,
                detail="new head did not receive its first actual v2 publication",
            )
            assert current_body["source"]["session"] == "head-b"
            client = ControlClient(chain["endpoint"], token=chain["server"].token)
            rejected = await asyncio.to_thread(client._request, "agenda-publish", {
                "contract_version": 2, "project": "example",
                "expected_generation": owner.store.generation, "observation": old_body,
            })
            assert rejected["error"]["code"] == "head-changed"
            assert app._data.agenda["observation"] == current_body
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="new-head v2 publication retained an owned reader or child",
            )
    asyncio.run(check())


def feed_invocations(chain) -> list[dict]:
    return [
        child for child in map(json.loads, chain["trace"].read_text().splitlines())
        if child["argv"] and child["argv"][0] == "agenda-feed"
    ]


@pytest.mark.parametrize("offline_probe", [False, True])
def test_actual_relay_disconnect_reconnects_to_current_snapshot(chain, monkeypatch, offline_probe):
    owner, server = chain["owner"], chain["server"]
    original_stream = server.stream_handle
    calls, disconnect = [], Event()

    def stream(action, payload, disconnected):
        if action != "agenda-feed":
            return original_stream(action, payload, disconnected)
        calls.append(1)
        if len(calls) != 1:
            return original_stream(action, payload, disconnected)

        def close_after_snapshot():
            inner = original_stream(action, payload, disconnected)
            try:
                yield next(inner)
                assert disconnect.wait(5), "test did not request the actual socket closure"
            finally:
                inner.close()

        return close_after_snapshot()

    monkeypatch.setattr(server, "stream_handle", stream)

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        offline = socket.socket() if offline_probe else None
        try:
            async with app.run_test() as pilot:
                await eventually(
                    pilot, lambda: app._data.agenda is not None,
                    detail=f"first actual snapshot did not arrive: {widget(app)}",
                )
                data, worker = app._data, app._agenda_worker
                assert data.agenda["availability"] == "not-published"
                accepted, fresh = chain["store"].mutate(
                    chain["home"],
                    request({"operation": "init", "documents": chain["documents"]}, 0, "init"),
                    session="head-a", actor="synthetic-test", publisher_configured=True,
                )
                assert fresh and accepted["revision"] == 1
                delivery = await asyncio.to_thread(
                    chain["publisher"].publish, chain["home"],
                    [*chain["argv"], "agenda-publish", "--project", "example", "--json"],
                )
                assert delivery["state"] == "delivered"
                assert data.agenda["availability"] == "not-published"
                if offline is not None:
                    offline.bind(("127.0.0.1", 0))
                    chain["endpoint_discovery"](f"http://127.0.0.1:{offline.getsockname()[1]}")
                disconnect.set()
                await eventually(
                    pilot,
                    lambda: "disconnected: agenda feed disconnected without terminal frame" in widget(app)
                    and "reconnecting in 1s" in widget(app),
                    detail=f"actual CLI structured disconnect did not enter bounded recovery: {widget(app)}",
                )
                assert data.agenda is None
                if offline is not None:
                    await eventually(
                        pilot, lambda: "resident health is unavailable: disconnected:" in widget(app)
                        and "reconnecting in 2s" in widget(app),
                        detail=f"real offline reprobe discarded the remaining retry budget: {widget(app)}",
                    )
                    assert data.agenda is None
                    chain["endpoint_discovery"](chain["endpoint"])
                await eventually(
                    pilot, lambda: data.agenda is not None
                    and data.agenda["availability"] == "observed",
                    detail=f"actual reconnect snapshot did not arrive: {widget(app)}",
                )
                assert data.agenda["type"] == "snapshot"
                assert data.agenda["observation"] == chain["store"].delivery_record(chain["home"])[1]
                assert app._data is data and app._agenda_worker is worker
                assert len(calls) == 2 and len(feed_invocations(chain)) == 2
                probes = [
                    child for child in map(json.loads, chain["trace"].read_text().splitlines())
                    if child["argv"] == ["--resident-health"]
                ]
                assert len(probes) == (3 if offline_probe else 2)
                await pilot.press("escape")
                await eventually(
                    pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                    detail="actual reconnect retained a reader or child after cancellation",
                )
        finally:
            disconnect.set()
            if offline is not None:
                offline.close()

    asyncio.run(check())


def test_actual_relay_disconnect_reconnects_at_most_three_times(chain, monkeypatch):
    server = chain["server"]
    original_stream = server.stream_handle
    calls = []

    def stream(action, payload, disconnected):
        if action != "agenda-feed":
            return original_stream(action, payload, disconnected)
        calls.append(1)

        def close_after_snapshot():
            inner = original_stream(action, payload, disconnected)
            try:
                yield next(inner)
            finally:
                inner.close()

        return close_after_snapshot()

    monkeypatch.setattr(server, "stream_handle", stream)

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._agenda_worker is not None,
                detail="actual subscription worker did not start",
            )
            await app._agenda_worker.wait()
            assert app._agenda_worker.error is None
            assert len(calls) == 4 and len(feed_invocations(chain)) == 4
            assert "disconnected: agenda feed disconnected without terminal frame" in widget(app)
            assert "reconnecting" not in widget(app)
            assert app._data.agenda is None
            assert chain["owner"].agendas.feed.wait_closed(3)
            assert chain["settled"]()

    asyncio.run(check())


@pytest.mark.parametrize("reason", ["draining", "unready", "unavailable"])
def test_actual_undrain_reconnects_and_updates_same_generation(chain, reason):
    owner = chain["owner"]
    store, publisher, home = chain["store"], chain["publisher"], chain["home"]
    argv = [*chain["argv"], "agenda-publish", "--project", "example", "--json"]

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail=f"initial actual snapshot unavailable: {widget(app)}",
            )
            data, worker = app._data, app._agenda_worker
            result, fresh = store.mutate(
                home, request({"operation": "init", "documents": chain["documents"]}, 0, "init"),
                session="head-a", actor="synthetic-test", publisher_configured=True,
            )
            assert fresh and result["revision"] == 1
            assert (await asyncio.to_thread(publisher.publish, home, argv))["state"] == "delivered"
            await eventually(
                pilot, lambda: data.agenda is not None and data.agenda["availability"] == "observed",
                detail="first actual publication unavailable",
            )
            generation, sequence = data.agenda["generation"], data.agenda["sequence"]
            owner.close_admission(reason)
            await eventually(
                pilot, lambda: f"provider going away: {reason}" in widget(app),
                detail=f"actual drain notice did not reach Manager: {widget(app)}",
            )
            assert data.agenda is None
            owner.activate()
            assert owner.store.generation == generation
            await eventually(
                pilot, lambda: data.agenda is not None and data.agenda["availability"] == "observed",
                detail=f"same-generation undrain was incorrectly retired: {widget(app)}",
            )
            assert data.agenda["type"] == "snapshot"
            assert data.agenda["generation"] == generation and data.agenda["sequence"] >= sequence
            assert data.agenda["observation"] == store.delivery_record(home)[1]
            result, fresh = store.mutate(
                home, request({"operation": "park", "ref": "undrain", "reason": "nonblocking"}, 1, "park"),
                session="head-a", actor="synthetic-test", publisher_configured=True,
            )
            assert fresh and result["revision"] == 2
            assert (await asyncio.to_thread(publisher.publish, home, argv))["state"] == "delivered"
            await eventually(
                pilot, lambda: "Parked: 1" in widget(app),
                detail="actual post-undrain update did not reach the same subscription",
            )
            assert data.agenda["type"] == "update" and data.agenda["generation"] == generation
            assert data.agenda["sequence"] > sequence
            assert data.agenda["observation"] == store.delivery_record(home)[1]
            assert app._data is data and app._agenda_worker is worker
            assert len(feed_invocations(chain)) == 2
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="actual undrain retained a child/subscriber after quit",
            )

    asyncio.run(check())


@pytest.mark.parametrize("chain", [False], indirect=True)
def test_actual_initial_unready_demand_becomes_first_snapshot(chain):
    owner = chain["owner"]

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: "unready" in widget(app) and "reconnecting in 1s" in widget(app),
                detail=f"initial authenticated unready reply did not retain recovery: {widget(app)}",
            )
            assert app._data.agenda is None
            data, worker = app._data, app._agenda_worker
            assert chain["wake"].is_set(), "actual provider did not wake its existing bounded producer"
            assert "example" in owner.demand_projects(), "actual provider did not retain project demand"
            owner.publish("example", [{"id": "selected"}], observed_at=1)
            await eventually(
                pilot, lambda: data.agenda is not None,
                detail=f"existing producer publication did not permit the first snapshot: {widget(app)}",
            )
            assert data.agenda["type"] == "snapshot"
            assert data.agenda["availability"] == "not-published"
            assert app._data is data and app._agenda_worker is worker
            assert len(feed_invocations(chain)) == 2
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="initial-unready recovery retained children after quit",
            )

    asyncio.run(check())


def test_actual_initial_health_unready_recovers_before_any_snapshot(chain):
    owner = chain["owner"]
    generation = owner.store.generation
    owner.close_admission("unready")

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: "authority is unready" in widget(app) and "reconnecting in 1s" in widget(app),
                detail=f"authenticated health proof was lost before a first snapshot: {widget(app)}",
            )
            assert app._data.agenda is None and not feed_invocations(chain)
            owner.activate()
            assert owner.store.generation == generation
            await eventually(
                pilot, lambda: app._data.agenda is not None,
                detail=f"actual ready authority did not produce a first snapshot: {widget(app)}",
            )
            assert app._data.agenda["type"] == "snapshot"
            assert app._data.agenda["generation"] == generation
            assert len(feed_invocations(chain)) == 1
            await pilot.press("escape")
            await eventually(
                pilot, lambda: owner.agendas.feed.active_count() == 0 and chain["settled"](),
                detail="initial health recovery retained children after quit",
            )

    asyncio.run(check())


@pytest.mark.parametrize("action", ["refresh", "quit"])
def test_cancellation_at_real_health_completion_settles_worker(chain, monkeypatch, action):
    completed, release, returned = Event(), Event(), Event()
    original_runner = agenda_transport.cancellable_read_runner
    original_subscribe = mux_companion.subscribe
    gated = []

    def runner(cancelled, *, cwd):
        execute = original_runner(cancelled, cwd=cwd)

        def health_completion(argv, timeout):
            result = execute(argv, timeout)
            if "--resident-health" in argv and not gated:
                gated.append(1)
                assert result.returncode == 0
                assert json.loads(result.stdout)["ok"] is True
                completed.set()
                assert release.wait(5), "test did not release the completed health result"
            return result

        return health_completion

    def observed_subscribe(*args, **kwargs):
        result = original_subscribe(*args, **kwargs)
        returned.set()
        return result

    monkeypatch.setattr(agenda_transport, "cancellable_read_runner", runner)
    monkeypatch.setattr(mux_companion, "subscribe", observed_subscribe)

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        try:
            async with app.run_test() as pilot:
                assert await asyncio.to_thread(completed.wait, 3), "real health child did not finish"
                data, worker = app._data, app._agenda_worker
                cancelled = app._agenda_cancel
                assert chain["settled"](argv=["--resident-health"]), (
                    "health result returned before its exact child retired"
                )
                assert not feed_invocations(chain)
                if action == "refresh":
                    app.action_refresh()
                else:
                    await pilot.press("escape")
                await eventually(
                    pilot, cancelled.is_set, detail="Refresh/quit did not cancel the completed health read",
                )
                release.set()
                assert await asyncio.to_thread(returned.wait, 3), "subscribe did not return normally"
                try:
                    await worker.wait()
                except WorkerCancelled:
                    assert action == "quit", "Refresh unexpectedly cancelled rather than joined its worker"
                assert worker.error is None or (
                    action == "quit" and isinstance(worker.error, asyncio.CancelledError)
                )
                assert data.agenda is None
                if action == "refresh":
                    await eventually(
                        pilot, lambda: app._data is not data and app._data.agenda is not None,
                        detail=f"Refresh failed to join/replace the cancelled worker: {widget(app)}",
                    )
                    assert app._data.agenda["availability"] == "not-published"
                    assert len(feed_invocations(chain)) == 1, "cancelled generation invoked a feed"
                    await pilot.press("escape")
                else:
                    assert not feed_invocations(chain)
                await eventually(
                    pilot, lambda: chain["owner"].agendas.feed.active_count() == 0
                    and chain["settled"](), detail="health-completion cancellation leaked an owned child",
                )
        finally:
            release.set()

    asyncio.run(check())


def test_unexpected_health_completion_interruption_is_visible(chain, monkeypatch):
    original = agenda_transport.require_capability

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        assert not kwargs["cancelled"].is_set()
        raise InterruptedError("synthetic unexpected health-completion interruption")

    monkeypatch.setattr(agenda_transport, "require_capability", interrupted)

    async def check():
        app = mux_companion.MuxCompanionApp(cwd=str(chain["workdir"]))
        async with app.run_test() as pilot:
            await eventually(
                pilot, lambda: "agenda read interrupted unexpectedly" in widget(app),
                detail=f"unexpected interruption was not visibly classified: {widget(app)}",
            )
            await app._agenda_worker.wait()
            assert app._agenda_worker.error is None
            assert app._data.agenda is None
            assert not feed_invocations(chain)
            assert chain["settled"](argv=["--resident-health"])
            await pilot.press("escape")
            await eventually(
                pilot, chain["settled"], detail="unexpected interruption retained a child after quit",
            )

    asyncio.run(check())
