"""Synthetic records built by the actual owner wire contract, never a live owner."""

from __future__ import annotations

import json
import ast
import inspect
from contextlib import ExitStack, contextmanager
from pathlib import Path
import sys
import textwrap
import time
from worktree_manager import agenda_transport, engine_posix_process


def _retirement_timeout(function, operation: str) -> float:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    values = [
        option.value.value for call in ast.walk(tree) if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute) and call.func.attr == operation
        for option in call.keywords if option.arg == "timeout"
        and isinstance(option.value, ast.Constant) and type(option.value.value) in (int, float)
    ]
    assert values, f"production retirement bound missing: {operation}"
    return float(max(values))


JOIN_PREPARATION_READS = 4  # One probe and selected read/latest-page/replay.
JOIN_PIPE_READERS = 3
JOIN_PROBE_CLEANUP = _retirement_timeout(engine_posix_process.run, "communicate")
JOIN_COMMAND_CLEANUP = (
    _retirement_timeout(agenda_transport._owned_process, "wait")
    + JOIN_PIPE_READERS * _retirement_timeout(agenda_transport.command_frames, "join")
)
JOIN_PREPARATION_SECONDS = (
    JOIN_PREPARATION_READS * agenda_transport.STARTUP_TIMEOUT
    + JOIN_PROBE_CLEANUP + (JOIN_PREPARATION_READS - 1) * JOIN_COMMAND_CLEANUP
)
JOIN_ADMISSION_SECONDS = agenda_transport.STARTUP_TIMEOUT
JOIN_CLEANUP_SECONDS = JOIN_PREPARATION_READS * JOIN_COMMAND_CLEANUP
JOIN_TEST_SECONDS = (
    JOIN_PREPARATION_SECONDS + JOIN_ADMISSION_SECONDS + agenda_transport.IDLE_TIMEOUT
    + agenda_transport.STARTUP_TIMEOUT + JOIN_COMMAND_CLEANUP + JOIN_CLEANUP_SECONDS
)


class JoinPhase:
    """One fixed monotonic fixture deadline; observations never renew it."""

    def __init__(self, name: str, seconds: float, *, clock=time.monotonic) -> None:
        self.name, self.seconds, self.clock = name, seconds, clock
        self.started = clock()
        self.deadline = self.started + seconds

    async def wait(self, condition, pause, failure, diagnostic) -> None:
        while True:
            error = failure()
            now = self.clock()
            if error is not None or now >= self.deadline:
                details = {
                    "phase": self.name, "started": self.started, "now": now,
                    "deadline": self.deadline, "budget": self.seconds, **diagnostic(),
                }
                if error is not None:
                    raise AssertionError(details) from error
                raise AssertionError(details)
            if condition():
                return
            await pause(min(0.025, self.deadline - now))

OWNER_SOURCE = Path(__file__).resolve().parents[2] / "plugins" / "agent-worktrees" / "src"
if str(OWNER_SOURCE) not in sys.path:
    sys.path.insert(0, str(OWNER_SOURCE))

from agent_worktrees import owner_observation_contract as wire

RESIDENT = "a" * 32
JOURNAL = "b" * 32
SOURCE = wire.Source("example", "selected", "current-roster:record-one", JOURNAL, RESIDENT, "head-a")
AT = "2026-11-02T00:00:00+00:00"


def cursor(number: int) -> str:
    return wire.position(SOURCE, number)


def facts(summary: str = "Current accepted purpose.") -> dict:
    return {
        "origin": "resident-current-roster",
        "coverage": "sampled-selected-fields-without-timestamp-metadata",
        "head_basis": "asserted-listing-head",
        "row": {
            "id": "selected", "repo": "example", "last_session_id": "head-a",
            "creation_nonce": "record-one", "head_yielded": False, "summary": summary,
            "active_effort": {"path": "efforts/active/example/README.md", "slug": "example"},
        },
    }


def entry(number: int) -> dict:
    return {
        "kind": "observed-change", "evidence_kind": "observed-change", "native_kind": None,
        "occurred_at": None, "observed_at": AT,
        "actor": {"kind": "worktree", "id": "selected", "qualification": "authoritative",
                  "session_id": "head-a"},
        "correlation": {name: None for name in wire.CORRELATION_FIELDS},
        "relations": [], "record_fence": {"facts_sha256": "synthetic"},
        "source_ref": {"source": SOURCE.source, "generation": "record-one",
                       "record_id": None, "payload_ref": None},
        "summary": f"Observed change {number}", "details": "A sampled change, not a native mutation.",
        "reason": None, "phase": None, "payload": {"before_sha256": "before", "after_sha256": "after"},
        "event_id": f"event-{number}", "cursor": cursor(number), "head_session_id": "head-a",
    }


def qualified() -> dict:
    return {
        "availability": "observed", "completeness": "partial", "durability": "committed",
        "history_scope": "retained-range", "history_floor": cursor(0), "observed_at": AT,
    }


def snapshot(tip: int = 40, *, after: int | None = None, summary: str = "Current accepted purpose.") -> dict:
    return wire.frame("snapshot", SOURCE, cursor(tip), {
        **qualified(), "replay_barrier": cursor(tip),
        "base_cursor": None if after is None else cursor(after),
        "entries": [] if after is None else [entry(n) for n in range(after + 1, tip + 1)],
        "replay_complete": True, "facts": facts(summary),
    })


def page(*, before: int | None = None) -> dict:
    numbers = range(21, 41) if before is None else range(1, before)
    entries = [entry(number) for number in numbers]
    return wire.frame("history-page", SOURCE, cursor(40), {
        **qualified(), "before_cursor": None if before is None else cursor(before),
        "entries": entries, "has_more": before is None,
        "next_before": cursor(21) if before is None else None,
    })


def delta() -> dict:
    return wire.frame("delta", SOURCE, cursor(41), {
        **qualified(), "base_cursor": cursor(40), "entries": [entry(41)],
        "facts": facts("Updated purpose from pushed owner facts."),
    })


def gap(before: int = 21) -> dict:
    return wire.frame("gap", SOURCE, cursor(40), {
        **qualified(), "availability": "unavailable", "history_floor": cursor(before),
        "base_cursor": cursor(before), "reason": "retention", "details": "Requested page expired.",
        "reconcile": "snapshot-and-history", "source_ref": None,
    })


def raw(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode()


def peer(root: Path, *, supported: bool = True) -> tuple[Path, Path]:
    """A fake installed interpreter executes the actual Manager passive probe."""
    package = root / "modules" / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "owner_observation_contract.py").write_text("VERSION=1\n", encoding="utf-8")
    caps = ["worktree-observations-feed-v1", "worktree-observations-history-v1"] if supported else []
    health = {"capabilities": caps, "published": True, "status": "ready",
              "worktree_observations": {"status": "ready", "generation": RESIDENT},
              "worktrees": {"status": "ready", "generation": RESIDENT}}
    (package / "worktrees_client.py").write_text(
        f"def read_health():\n    return {health!r}\n", encoding="utf-8",
    )
    (package / "owner_observation_cli.py").write_text(
        "COMMANDS=frozenset({'worktree-observations','worktree-observation-history'})\n",
        encoding="utf-8",
    )
    # All responses are first validated by the real source owner contract.
    responses = {
        "point": snapshot(), "replay": snapshot(after=21), "follow": snapshot(after=40),
        "latest": page(), "older": page(before=21), "heartbeat": wire.frame("heartbeat", SOURCE, cursor(40), {}),
    }
    (root / "responses.json").write_text(json.dumps(responses), encoding="utf-8")
    script = r'''
import argparse,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def build_parser():
    parser=argparse.ArgumentParser(allow_abbrev=False)
    commands=parser.add_subparsers(dest="command",required=True)
    for name in ("worktree-observations","worktree-observation-history"):
        sub=commands.add_parser(name,allow_abbrev=False)
        sub.add_argument("--worktree",required=True)
        sub.add_argument("--session",required=True)
        sub.add_argument("--contract-version",type=int,choices=(1,),default=1)
        sub.add_argument("--json",action="store_true")
        if name=="worktree-observations":
            sub.add_argument("--follow",action="store_true")
            sub.add_argument("--after-cursor")
        else:
            sub.add_argument("--source-generation",required=True)
            sub.add_argument("--before-cursor")
            sub.add_argument("--limit",type=int,default=64)
    return parser
def emit(value):
    print(json.dumps(value,separators=(",",":")),flush=True)
def main():
    project_parser=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    project_parser.add_argument("--project",required=True)
    project,remaining=project_parser.parse_known_args()
    if project.project!="example":
        raise ValueError("wrong fixture project")
    args=build_parser().parse_args(remaining)
    with (ROOT/"calls.jsonl").open("a") as out:
        out.write(json.dumps(sys.argv[1:])+"\n")
    data=json.loads((ROOT/"responses.json").read_text())
    if args.command=="worktree-observation-history":
        if args.before_cursor is not None and (ROOT/"block-history").exists():
            (ROOT/"page-pid").write_text(str(os.getpid()))
            while not (ROOT/"release-history").exists():
                time.sleep(.02)
        emit(data["latest" if args.before_cursor is None else "older"])
    elif not args.follow:
        emit(data["point" if args.after_cursor is None else "replay"])
    else:
        (ROOT/"feed-pid").write_text(str(os.getpid()))
        emit(data["follow"])
        current=data["heartbeat"]
        while True:
            behavior=ROOT/"feed-behavior"
            mode=behavior.read_text().strip() if behavior.exists() else "normal"
            if mode=="disconnect":
                return
            if mode=="timeout":
                time.sleep(.05)
                continue
            delivery=ROOT/"push.json"
            if delivery.exists():
                value=json.loads(delivery.read_text())
                if value["cursor"]!=current["cursor"]:
                    emit(value)
                    current={key:value[key] for key in data["heartbeat"]}
                    current["type"]="heartbeat"
            emit(current)
            time.sleep(.05)
if __name__=="__main__":
    main()
'''
    (package / "__main__.py").write_text(script, encoding="utf-8")
    interpreter = root / "owned-python"
    interpreter.write_text(
        f"#!{sys.executable}\nimport runpy,sys\nsys.path.insert(0,{str(root / 'modules')!r})\n"
        "if '-c' in sys.argv:\n"
        "    i=sys.argv.index('-c'); code=sys.argv[i+1]; sys.argv=['-c',*sys.argv[i+2:]]\n"
        "    exec(code,{'__name__':'__main__'})\n"
        "else:\n"
        "    i=sys.argv.index('-m'); module=sys.argv[i+1]; sys.argv=[module,*sys.argv[i+2:]]\n"
        "    runpy.run_module(module,run_name='__main__')\n", encoding="utf-8",
    )
    interpreter.chmod(0o700)
    return interpreter, root / "calls.jsonl"


@contextmanager
def resident_join(root: Path, monkeypatch):
    """Actual record projection -> resident -> owning CLI, with private discovery."""
    with ExitStack() as cleanup:
        yield from _resident_join(root, monkeypatch, cleanup)


def _resident_join(root: Path, monkeypatch, cleanup: ExitStack):
    import importlib
    import os
    from threading import Event
    import time
    from types import SimpleNamespace

    from test_agenda_end_to_end import SOURCE_PATHS
    from worktree_manager import agenda_transport, companion_worktree_source, engine_client, mux_companion

    for path in reversed(SOURCE_PATHS):
        monkeypatch.syspath_prepend(str(path))
    profile, records, checkout, route, bootstrap = [
        root / name for name in ("profile", "records", "checkout", "route", "bootstrap")
    ]
    for path in (profile, records, checkout, route, bootstrap):
        path.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(profile))
    config = importlib.import_module("agent_worktrees.config")
    tracking = importlib.import_module("agent_worktrees.tracking")
    core = importlib.import_module("agent_worktrees.__main__")
    current = importlib.import_module("agent_worktrees.worktrees_current_rows")
    monitor = importlib.import_module("agent_worktrees.status_monitor_runtime")
    control = importlib.import_module("agent_worktrees.status_monitor_cutover")
    health = importlib.import_module("agent_worktrees.status_monitor_health")
    runtime = importlib.import_module("agent_worktrees.worktrees_runtime")
    effort = importlib.import_module("agent_worktrees.effort_focus")
    locks = importlib.import_module("agent_worktrees.locks")
    monkeypatch.setattr(config, "project_name", lambda: "example")
    monkeypatch.setattr(config, "tracking_dir", lambda: records)
    monkeypatch.setattr(config, "install_dir", lambda: route)
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda wid, repo=None: records)
    forbidden = root / "forbidden-starts"

    def no_start(*args, **kwargs):
        forbidden.write_text("ensure/start was attempted", encoding="utf-8")
        raise AssertionError("private observation join attempted ensure/start")

    for name in ("_ensure_status_monitor", "_restart_status_monitor", "_spawn_detached"):
        monkeypatch.setattr(monitor, name, no_start)
    monkeypatch.setattr(core.inst, "setup_application", no_start)
    effort_path = "efforts/active/private-join/README.md"
    document = checkout / effort_path
    document.parent.mkdir(parents=True)
    document.write_text(
        "# Private join\n\n- **Slug:** private-join\n- **Status:** Active\n\n"
        "## Plan\n\n- [ ] Verify the actual receiver.\n\n"
        "## Validation Plan\n\n- [ ] Observe a live change and cleanup.\n", encoding="utf-8",
    )
    record_path = records / "selected.yaml"
    record = tracking.WorktreeRecord(
        worktree_id="selected", branch="worktree/selected", worktree_path=str(checkout),
        repo="example", machine="synthetic", platform="linux",
        started_at=AT, last_resumed_at=None, resume_count=0, title="Original creation label",
        summary="Record summary is not the active effort purpose.", status="active", completed_at=None,
        sessions=[tracking.SessionEntry("head-a", AT)], head_session="head-a",
        creation_nonce="f" * 32,
        active_effort=effort.ActiveEffort(effort_path, "receiver", "Initial slice"), effort_revision=1,
    )
    tracking.save_record(record, record_path)

    def project(at: float) -> dict:
        loaded = tracking.load_record(record_path)
        rows = current.materialize(
            [loaded], serialize=core._worktree_to_dict, observed_at=at,
            state_map={}, mux_map={}, session_ctx=None, bare_orphan_wts=set(), bridge_live_wts=set(),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["creation_nonce"] == loaded.creation_nonce
        assert row["last_session_id"] == loaded.resolved_head_session
        assert row["active_effort"]["state"] == "open"
        assert row["fact_provenance"]["record"]["quality"] == "current"
        return row

    owner = runtime.WorktreesRuntime(root / "resident.sqlite3", elected=lambda: True, wake=Event())
    cleanup.callback(owner.shutdown)
    owner.activate()
    owner.observations.runtime.heartbeat = 0.05
    selection = {
        "contract_version": 1, "project": "example", "worktree_id": "selected",
        "expected_head_session_id": "head-a", "after_cursor": None,
    }
    assert owner.observation_read(selection)["error"]["code"] == "unready"
    first = project(1)
    owner.publish("example", [first], observed_at=1)
    actions, streams, stages, failures = [], [], [], []

    def mark(name: str) -> None:
        stages.append((name, time.monotonic()))

    prepare = companion_worktree_source.WorktreeTimelineSource.prepare

    async def traced_prepare(source, epoch, *, stop):
        mark("prepare.begin")
        try:
            return await prepare(source, epoch, stop=stop)
        except (companion_worktree_source.OwnerReadError, ValueError, OSError) as exc:
            failures.append(exc)
            raise
        finally:
            mark("prepare.end")

    monkeypatch.setattr(companion_worktree_source.WorktreeTimelineSource, "prepare", traced_prepare)

    def publish(slice_name: str, at: float) -> dict:
        loaded = tracking.load_record(record_path)
        loaded.active_effort = effort.ActiveEffort(effort_path, "receiver", slice_name)
        loaded.effort_revision += 1
        tracking.save_record(loaded, record_path)
        row = project(at)
        owner.publish("example", [row], observed_at=at)
        return row

    publish("Warmup slice", 2)
    expected = publish("Current slice", 3)

    def disabled(field: str):
        return SimpleNamespace(health=lambda: {"capabilities": [], field: {"status": "unavailable"}})

    def handle(action, payload):
        mark("rpc." + action)
        actions.append((action, payload.copy()))
        if action == "health":
            return health.response(
                worktrees=owner, receipts=disabled("promptReceipt"),
                repositories=disabled("repositories"), allocations=disabled("allocationLinker"),
                admission_closed=False, published=server.port > 0, shutdown=False,
            )
        if action == "worktree-observations-read":
            return owner.observation_read(payload)
        if action == "worktree-observations-history":
            return owner.observation_history(payload)
        raise AssertionError(f"unexpected private point action: {action}")

    def stream(action, payload, disconnected):
        mark("feed.request")
        streams.append((action, payload.copy()))
        assert action == "worktree-observations-feed"
        return owner.stream(action, payload, disconnected)

    server = control.ControlServer(handle, token="private-worktree-join", stream_handle=stream)
    cleanup.callback(server.close)
    server.start()
    control.control_token_path(route).write_text(server.token, encoding="utf-8")
    endpoint = f"http://127.0.0.1:{server.port}"
    trace = root / "children.jsonl"
    startup = f'''
import json,os,sys,time
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]={list(map(str, SOURCE_PATHS))!r}
from agent_worktrees import config,front_door_cli,invocation_project,locks,worktrees_client
from agent_worktrees import installer,status_monitor_runtime
def refuse(*args,**kwargs):
    Path({str(forbidden)!r}).write_text("ensure/start was attempted",encoding="utf-8")
    raise AssertionError("private observation CLI attempted ensure/start")
installer.setup_application=refuse
status_monitor_runtime._ensure_status_monitor=refuse
status_monitor_runtime._restart_status_monitor=refuse
status_monitor_runtime._spawn_detached=refuse
def resolve(project):
    if project not in (None,"example"):
        raise ValueError("foreign private project")
    return "example",Path({str(checkout)!r})
front_door_cli._resolve_active_project=resolve
invocation_project._resolve_active_project=resolve
config.set_active_project("example")
config.tracking_dir=lambda:Path({str(records)!r})
config.install_dir=lambda:Path({str(route)!r})
worktrees_client.resident_endpoint=lambda root:SimpleNamespace(base_url={endpoint!r})
with Path({str(trace)!r}).open("a",encoding="utf-8") as out:
    out.write(json.dumps({{"pid":os.getpid(),"birth":locks.process_start_time(os.getpid()),
                         "argv":sys.argv[1:],"monotonic":time.monotonic()}})+"\\n")
'''
    (bootstrap / "sitecustomize.py").write_text(startup, encoding="utf-8")
    actual_environment = agenda_transport._engine_environment

    def environment():
        value = actual_environment()
        value["PYTHONPATH"] = os.pathsep.join([str(bootstrap), *map(str, SOURCE_PATHS)])
        return value

    monkeypatch.setattr(agenda_transport, "_engine_environment", environment)
    monkeypatch.setattr(
        companion_worktree_source, "_PROBE",
        f"import sys; sys.path[:0]=[{str(bootstrap)!r}]; import sitecustomize\n"
        + companion_worktree_source._PROBE,
    )
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: [sys.executable, "-m", "agent_worktrees"])
    monkeypatch.setattr(mux_companion, "_load_current_worktree", lambda *args, **kwargs: mux_companion._CompanionData(
        worktree=first.copy(), sessions=[{"id": record.resolved_head_session, "is_head": True}],
    ))
    monkeypatch.setattr(mux_companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(mux_companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)

    def settled():
        if not trace.exists():
            return True
        return all(
            not locks.pid_alive(child["pid"]) or locks.process_start_time(child["pid"]) != child["birth"]
            for child in map(json.loads, trace.read_text().splitlines())
        )

    try:
        yield {
            "owner": owner, "server": server, "record_path": record_path, "checkout": checkout,
            "row": expected, "publish": publish, "selection": selection,
            "actions": actions, "streams": streams, "settled": settled, "forbidden": forbidden,
            "stages": stages, "failures": failures, "trace": trace, "mark": mark,
        }
    finally:
        owner.close_admission("closed")
        owner.observation_wait_closed(3)
        deadline = time.monotonic() + 3
        while not settled() and time.monotonic() < deadline:
            time.sleep(0.025)
        assert settled(), "a birth-qualified owning CLI survived private join cleanup"
        assert not forbidden.exists(), "an ensure/start attempt occurred"
