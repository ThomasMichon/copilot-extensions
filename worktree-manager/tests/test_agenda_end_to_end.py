"""Actual core commit -> publisher CLI -> resident -> held Manager -> Textual."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import socket
import sys
import time
from typing import Any

import pytest
from rich.text import Text
from textual.widgets import Static, TabbedContent

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
    print(json.dumps({"ok":True,"health":worktrees_client.read_health()}))
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
def chain(tmp_path: Path, monkeypatch):
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

    owner = runtime.WorktreesRuntime(tmp_path / "resident.sqlite3", elected=lambda: True)
    owner.activate()
    owner.publish("example", [{"id": "selected"}], observed_at=1)
    actions = []

    def handle(action, payload):
        actions.append((action, deepcopy(payload)))
        return owner.health() if action == "health" else owner.agenda_handle(action, payload)

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
    entry.write_text(_ENTRY, encoding="utf-8")
    argv = [sys.executable, str(entry), str(configuration)]
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: list(argv))
    monkeypatch.setattr(agenda_transport, "health_argv", lambda base: [*base, "--resident-health"])

    board = json.loads((SKILL / "assets/agenda.example.json").read_text())
    cursor = json.loads((SKILL / "assets/cursor.example.json").read_text())
    cursor["worktree"] = record.worktree_id
    documents = {"cursor": cursor, "boards": {"agenda.json": board}}

    def settled():
        if not trace.exists():
            return True
        return all(
            not locks.pid_alive(child["pid"])
            or locks.process_start_time(child["pid"]) != child["birth"]
            for child in map(json.loads, trace.read_text().splitlines())
        )

    try:
        yield {
            "store": store, "publisher": publisher, "home": home, "documents": documents,
            "tracking": tracking, "record_path": record_path, "workdir": workdir,
            "owner": owner, "server": server, "argv": argv, "endpoint": endpoint,
            "endpoint_discovery": endpoint_discovery, "actions": actions,
            "settled": settled,
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
        assert store.mutate(home, initial, **identity) == (original, False)
        assert len(store.history(home)) == 3
        assert sum(action == "health" for action, _ in chain["actions"]) >= 7
        assert sum(action == "agenda-publish" for action, _ in chain["actions"]) == 3

    asyncio.run(check())
