"""Actual placement CLI/efforts reader joined to demand-driven Textual presentation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event

import pytest
from textual.widgets import Static, TabbedContent

from worktree_manager import engine_client as ec, mux_companion as companion
from worktree_manager.companion_agenda_attachment import AgendaReader, AgendaTimelineAttachment
from worktree_manager.companion_agenda_commands import AgendaCommands
from worktree_manager import companion_agenda_commands as rich_commands
from worktree_manager.companion_timeline_contract import Provider
from worktree_manager.companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from worktree_manager.companion_timeline_controller import TimelineCleanupError
from worktree_manager.companion_worktree_codec import OwnerReadError
from test_agenda_end_to_end import SOURCE_PATHS, ROOT, SKILL
from test_companion_agenda_live import current_data, until

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX attributed-interpreter fixture; native qualification remains open"),
]

STORE = "b" * 32


def current_managed_data(managed):
    data = current_data()
    data.worktree["path"] = str(managed["cwd"])
    return data


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for path in reversed(SOURCE_PATHS):
        monkeypatch.syspath_prepend(str(path))
    home, workdir = tmp_path / "profile", tmp_path / "selected"
    home.mkdir()
    workdir.mkdir()
    for name in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    config = importlib.import_module("agent_worktrees.config")
    state = importlib.import_module("agent_worktrees.project_state")
    placement = importlib.import_module("agent_worktrees.worktree_data_store")
    store = importlib.import_module("agenda_store")
    monkeypatch.setattr(config, "_home", lambda: home)
    monkeypatch.setattr(state, "_normalized_repository_remote", lambda project: f"network:example.invalid/{project}")
    records = config.project_dir("example") / "worktrees"
    records.mkdir(parents=True)
    (records / "selected.yaml").write_text(json.dumps({
        "worktree_id": "selected", "creation_nonce": "retained-incarnation", "repo": "example",
        "branch": "worktree/selected", "worktree_path": str(workdir),
        "machine": "fixture", "platform": "linux",
    }), encoding="utf-8")
    allocated = placement.allocate_worktree_data(
        "example", "selected", creation_nonce="retained-incarnation",
        domain="agendas", lane="main", store_id=STORE,
    )
    agenda_home = Path(allocated.state_home)
    initialization = json.loads((SKILL / "assets/init.context-qualified.example.json").read_text())
    initialization["change"]["worktree"] = "selected"
    store.mutate(agenda_home, initialization, session="head-a", initial_store_id=STORE)
    settings = workdir / ".github/copilot/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {"efforts@source-fixture": True},
        "extraKnownMarketplaces": {"source-fixture": {
            "source": {"source": "directory", "path": str(ROOT), "manifestName": "copilot-extensions"},
        }},
    }), encoding="utf-8")
    trace = tmp_path / "placement-calls.jsonl"
    runtime_configuration = tmp_path / "runtime.json"
    binding = (
        f"import sys,json\nfrom pathlib import Path\nsys.path[:0]={[str(path) for path in SOURCE_PATHS]!r}\n"
        "from agent_worktrees import config,project_state,__main__ as main\n"
        f"config._home=lambda:Path({str(home)!r})\n"
        "project_state._normalized_repository_remote=lambda project:f'network:example.invalid/{project}'\n"
        "def no_cluster():\n    raise AssertionError('passive placement loaded a command cluster')\n"
        "main._ensure_cluster_loaded=no_cluster\n"
        f"runtime=Path({str(runtime_configuration)!r})\n"
        "if runtime.exists():\n"
        "    from agent_worktrees import agenda_cli,tracking,worktrees_client\n"
        "    from agent_worktrees.status_monitor_cutover import ControlClient\n"
        "    settings=json.loads(runtime.read_text())\n"
        "    def client():\n"
        "        return ControlClient(settings['endpoint'],token=settings['token'],timeout=2)\n"
        f"    tracking._owning_tracking_dir=lambda wid,repo=None:Path({str(records)!r})\n"
        "    agenda_cli._control_client=client\n"
        "    worktrees_client._control_client=client\n"
        f"with Path({str(trace)!r}).open('a') as trace: trace.write(json.dumps(sys.argv[1:])+'\\n')\n"
    )
    python = tmp_path / "selected-python"
    python.write_text(
        f"#!{sys.executable} -I\nimport os,sys\n"
        f"binding={binding!r}\n"
        "if '-c' in sys.argv[1:]:\n"
        "    index=sys.argv.index('-c',1)\n"
        "    assert sys.argv[1:index] in (['-I'],['-I','-B'])\n"
        "    code=binding+'\\n'+sys.argv[index+1]\n"
        "    args=sys.argv[index+2:]\n"
        "else:\n"
        "    assert sys.argv[1:3]==['-m','agent_worktrees']\n"
        "    code=binding+'\\nraise SystemExit(main.main(sys.argv[1:]))'\n"
        "    args=sys.argv[3:]\n"
        "os.execv(sys.executable,[sys.executable,'-I','-B','-c',code,*args])\n",
        encoding="utf-8",
    )
    python.chmod(0o700)
    monkeypatch.setattr(ec, "engine_base_command", lambda: [str(python), "-m", "agent_worktrees"])

    def publication() -> dict:
        _, body = store.delivery_record(agenda_home)
        return {
            "type": "snapshot", "ok": True, "contract_version": 2,
            "generation": "a" * 32, "sequence": body["revision"],
            "project": "example", "worktree_id": "selected", "session_id": body["source"]["session"],
            "availability": "observed", "observation": body,
        }

    def update(*, session: str = "head-a") -> dict:
        state = store.read(agenda_home)
        revision = state["revision"]
        cursor = state["documents"]["cursor"]
        frame = cursor["frames"][-1]
        item = cursor["frame_state"][frame]["pending"][0]
        store.mutate(agenda_home, {
            "schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
            "operation_id": f"wait-{revision}", "expected_revision": revision,
            "change": {
                "operation": "wait", "frame_id": frame, "item_id": item,
                "reason_kind": "external", "reason_ref": f"fixture-{revision}",
                "next_action": f"Wait for fixture evidence {revision}",
                "evidence": [{"criterion": "await fixture input", "kind": "measured",
                              "ref": f"fixture-{revision}", "inputs": "isolated fixture"}],
            },
        }, session=session)
        return publication()

    return {
        "home": agenda_home, "cwd": workdir, "store": store, "settings": settings,
        "trace": trace, "publication": publication, "update": update, "records": records,
        "runtime_configuration": runtime_configuration,
    }


def test_actual_passive_discovery_and_reader_share_exact_managed_store(managed) -> None:
    reader = AgendaReader(("example", "selected", "head-a"), STORE, cwd=str(managed["cwd"]), project_root=str(managed["cwd"]))
    pages = reader.latest(managed["publication"]()["observation"], Event(), None)
    assert len(pages) == 1 and len(pages[0]["entries"]) == 1
    assert reader.commands.placement["creation_nonce"] == "retained-incarnation"
    assert reader.commands.placement["state_home"] == str(managed["home"])
    assert reader.revision == 1
    frame = managed["update"]()
    advanced = reader.latest(frame["observation"], Event(), pages[0]["cursor"])
    assert reader.revision == 2 and len(advanced[-1]["entries"]) == 2
    commands = [json.loads(line) for line in managed["trace"].read_text().splitlines()]
    assert commands and all("worktree-data-allocate" not in argv for argv in commands)
    assert "--creation-nonce" in commands[-1] and "--placement-generation" in commands[-1]
    assert not list(managed["cwd"].glob("**/agenda.sqlite3"))


def test_unregistered_store_and_old_owner_refuse_before_reader_or_new_syntax(managed, monkeypatch) -> None:
    commands = AgendaCommands("example", "selected", "c" * 32, cwd=str(managed["cwd"]), project_root=str(managed["cwd"]))
    with pytest.raises(OwnerReadError, match="not-registered"):
        commands.discover(Event())
    assert commands.script is None and commands.placement is None
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-m", "json"])
    with pytest.raises(ValueError, match="unsupported"):
        AgendaCommands("example", "selected", STORE, cwd=str(managed["cwd"]), project_root=str(managed["cwd"])).discover(Event())


def test_actual_parser_refuses_inconsistent_old_owner_before_default_dispatch(managed, monkeypatch) -> None:
    root = managed["cwd"].parent / "old-owner"
    package = root / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "worktree_data_cli.py").write_text(
        "CAPABILITY='worktree-data-v1'\nVERSION=1\nCOMMANDS={'worktree-data-read'}\n",
    )
    dispatched = root / "unexpected-launch"
    (package / "__main__.py").write_text(
        "import argparse\nfrom pathlib import Path\n"
        "def build_parser():\n"
        "    parser=argparse.ArgumentParser()\n"
        "    parser.add_argument('command',choices=['status'])\n"
        "    return parser\n"
        f"def main(argv):\n    Path({str(dispatched)!r}).write_text('must not dispatch')\n",
    )
    interpreter = root / "selected-python"
    prefix = f"import sys;sys.path.insert(0,{str(root)!r})\n"
    interpreter.write_text(
        f"#!{sys.executable} -I\nimport os,sys\n"
        "assert sys.argv[1:4]==['-I','-B','-c']\n"
        f"code={prefix!r}+sys.argv[4]\n"
        "os.execv(sys.executable,[sys.executable,'-I','-B','-c',code,*sys.argv[5:]])\n",
    )
    interpreter.chmod(0o700)
    monkeypatch.setattr(ec, "engine_base_command", lambda: [str(interpreter), "-m", "agent_worktrees"])
    with pytest.raises(OwnerReadError, match="unsupported"):
        AgendaCommands("example", "selected", STORE, cwd=str(managed["cwd"]), project_root=str(managed["cwd"])).discover(Event())
    assert not dispatched.exists()


def test_rich_reader_rejects_rewound_publication_join_and_changed_placement(managed) -> None:
    reader = AgendaReader(("example", "selected", "head-a"), STORE, cwd=str(managed["cwd"]), project_root=str(managed["cwd"]))
    publication = managed["publication"]()["observation"]
    reader.latest(publication, Event(), None)
    ahead = deepcopy(publication)
    ahead["revision"] = 20
    with pytest.raises(OwnerReadError, match="behind"):
        reader.latest(ahead, Event(), None)
    changed = deepcopy(reader.commands.placement)
    changed["placement_generation"] = 2
    reader.commands.placement = changed
    with pytest.raises(OwnerReadError, match="generation|placement"):
        reader.commands.discover(Event())


def test_actual_rich_pages_and_multi_page_catchup_do_not_consume_history_boundary(managed) -> None:
    reader = AgendaReader(("example", "selected", "head-a"), STORE, cwd=str(managed["cwd"]), project_root=str(managed["cwd"]))
    initial = reader.latest(managed["publication"]()["observation"], Event(), None)[0]
    for _ in range(70):
        managed["update"]()
    pages = reader.latest(managed["publication"]()["observation"], Event(), initial["cursor"])
    revisions = [
        reader.codec.revision(entry["cursor"]) for page in pages for entry in page["entries"]
    ]
    assert 2 <= len(pages) <= 4 and revisions == list(range(revisions[0], 72))
    assert revisions[0] <= 2 and reader.revision == 71
    latest = pages[-1]
    token = reader.codec.token(1)
    snapshot = reader.codec.snapshot(latest, token)
    before = snapshot.before
    assert before is not None
    historical = reader.history(Event(), before.cursor)
    page = reader.codec.page(historical, token, before)
    assert page.before == before and page.events
    assert all(reader.codec.revision(event.position.cursor) < reader.codec.revision(before.cursor) for event in page.events)
    assert snapshot.position.cursor == latest["cursor"] and reader.revision == 71


def test_rich_push_history_hide_reopen_and_reload_reuse_one_coarse_subscription(managed, monkeypatch) -> None:
    publications: Queue = Queue()
    publications.put(managed["publication"]())
    calls: list[tuple[str, str, str]] = []
    retired: list[bool] = []

    def subscribe(project, worktree, session, *, cancelled, publish, **kwargs):
        calls.append((project, worktree, session))
        try:
            while not cancelled.is_set():
                try:
                    frame = publications.get(timeout=0.05)
                except Empty:
                    continue
                publish(frame, None)
        finally:
            retired.append(True)

    async def no_worktree_source(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(companion, "subscribe", subscribe)
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: current_managed_data(managed))
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(companion.TimelineAttachment, "set_selection", no_worktree_source)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: app._data.agenda is not None)
            except AssertionError as error:
                raise AssertionError({
                    "agenda": app._data.agenda_error, "worktree": app._data.error,
                    "calls": managed["trace"].read_text() if managed["trace"].exists() else None,
                }) from error
            worker = app._agenda_worker
            assert calls == [("example", "selected", "head-a")]
            assert not managed["trace"].exists(), "hidden Timeline started optional rich reads"
            for _ in range(12):
                managed["update"]()
            publications.put(managed["publication"]())
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: len(timeline.window.events) == 13)
            await pilot.pause()
            assert all(event.source.provider is Provider.AGENDA for event in timeline.window.events)
            assert all(event.actor.qualification.value != "authoritative" for event in timeline.window.events)
            timeline.focus()
            await pilot.press("pageup")
            anchor = timeline.capture_anchor()
            assert anchor is not None and not timeline.window.follow_live
            app._receive_agenda(app._data, app._agenda_generation, app._agenda_cancel, None, None)
            await pilot.pause()
            agenda_key = app._rich_agenda_attachment.token.identity.source_key
            assert timeline.window.sources[agenda_key].availability.value == "unknown"
            assert "Agenda: error" not in str(app.query_one(".timeline-health", Static).content)
            publications.put(managed["update"]())
            await until(pilot, lambda: len(timeline.window.events) == 14)
            assert timeline.window.sources[agenda_key].availability.value == "observed"
            await pilot.pause()
            assert len(timeline.window.unread) == 1
            row = timeline.layout_index.anchor_row(anchor)
            assert row + timeline._padding - int(timeline.scroll_offset.y) == anchor.screen_row
            assert app._agenda_worker is worker and len(calls) == 1
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await until(pilot, lambda: app._rich_agenda_attachment.selection is None)
            trace = managed["trace"].read_bytes()
            publications.put(managed["update"]())
            await until(pilot, lambda: app._data.agenda["observation"]["revision"] == 15)
            assert managed["trace"].read_bytes() == trace
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: len(timeline.window.events) == 15)
            assert len(calls) == 1 and app._agenda_worker is worker
            assert "accepted operations" in str(app.query_one("#timeline-agenda-status", Static).content)
            retained = tuple(timeline.window.events)
            app._receive_agenda(
                app._data, app._agenda_generation, app._agenda_cancel, None, "fixture transient disconnect",
            )
            key = app._rich_agenda_attachment.token.identity.source_key
            await until(pilot, lambda: timeline.window.sources[key].availability.value == "unavailable")
            assert "Agenda: error" in str(app.query_one(".timeline-health", Static).content)
            app._receive_agenda(app._data, app._agenda_generation, app._agenda_cancel, None, None)
            await pilot.pause()
            assert "Agenda: error" in str(app.query_one(".timeline-health", Static).content)
            for _ in range(70):
                managed["update"]()
            publications.put(managed["publication"]())
            await until(pilot, lambda: len(timeline.window.events) == 85)
            assert tuple(timeline.window.events[:15]) == retained
            assert timeline.window.sources[key].availability.value == "observed"
            assert Provider.AGENDA not in app._timeline_panel._provider_notices
            old_data, old_generation, old_cancelled = app._data, app._agenda_generation, app._agenda_cancel
            app.action_refresh()
            await until(pilot, lambda: len(calls) == 2 and not app._loading)
            publications.put(managed["publication"]())
            await until(pilot, lambda: app._data.agenda is not None and app._rich_agenda_attachment.token is not None)
            assert retired == [True] and app._agenda_worker is not worker
            app._receive_agenda(old_data, old_generation, old_cancelled, None, "late retired callback")
            assert app._data.agenda_error is None and len(calls) == 2
            await pilot.press("escape")
        assert retired == [True, True]
        assert app._rich_agenda_attachment._task is None and app._rich_agenda_attachment._page is None

    asyncio.run(check())


def test_actual_commit_publisher_resident_cli_and_managed_reader_join(managed, monkeypatch) -> None:
    tracking = importlib.import_module("agent_worktrees.tracking")
    runtime = importlib.import_module("agent_worktrees.worktrees_runtime")
    control = importlib.import_module("agent_worktrees.status_monitor_cutover")
    publisher = importlib.import_module("agenda_publish")
    record_path = managed["records"] / "selected.yaml"
    record = tracking.WorktreeRecord(
        worktree_id="selected", branch="worktree/selected", worktree_path=str(managed["cwd"]),
        repo="example", machine="fixture", platform="linux", started_at="2026-11-01T07:00:00Z",
        last_resumed_at=None, resume_count=0, title="Managed source join", status="active",
        completed_at=None, creation_nonce="retained-incarnation",
        sessions=[tracking.SessionEntry("head-a", "2026-11-01T07:00:00Z")], head_session="head-a",
    )
    tracking.save_record(record, record_path)
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda wid, repo=None: managed["records"])
    owner = runtime.WorktreesRuntime(managed["cwd"].parent / "resident.sqlite3", elected=lambda: True)
    owner.activate()
    owner.publish("example", [{"id": "selected"}], observed_at=1)

    def handle(action, payload):
        return owner.health() if action == "health" else owner.agenda_handle(action, payload)

    server = control.ControlServer(handle, token="fixture-rich-agenda-token", stream_handle=owner.stream)
    server.start()
    managed["runtime_configuration"].write_text(json.dumps({
        "endpoint": f"http://127.0.0.1:{server.port}", "token": server.token,
    }))
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: current_managed_data(managed))
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)

    async def no_worktree_source(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(companion.TimelineAttachment, "set_selection", no_worktree_source)
    provider_python = ec.engine_base_command()[0]

    async def publish() -> dict:
        return await asyncio.to_thread(
            publisher.publish, managed["home"], [],
            provider_python=provider_python, provider_project="example",
        )

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: app._data.agenda is not None)
            except AssertionError as error:
                raise AssertionError({
                    "agenda": app._data.agenda_error, "worktree": app._data.error,
                    "calls": managed["trace"].read_text() if managed["trace"].exists() else None,
                }) from error
            assert app._data.agenda["availability"] == "not-published"
            committed = deepcopy(managed["store"].delivery_record(managed["home"])[1])
            delivered = await publish()
            assert delivered["state"] == "delivered", delivered
            await until(pilot, lambda: app._data.agenda.get("observation") == committed)
            worker = app._agenda_worker
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: len(timeline.window.events) == 1)
            managed["update"]()
            assert app._data.agenda["observation"] == committed, "canonical mutation fabricated delivery"
            delivered = await publish()
            assert delivered["state"] == "delivered", delivered
            await until(pilot, lambda: len(timeline.window.events) == 2)
            assert app._data.agenda["observation"] == managed["store"].delivery_record(managed["home"])[1]
            assert app._agenda_worker is worker
            calls = [json.loads(line) for line in managed["trace"].read_text().splitlines()]
            assert sum("agenda-feed" in argv for argv in calls) == 1
            record.sessions.append(tracking.SessionEntry("head-b", "2026-11-01T08:00:00Z"))
            record.head_session = "head-b"
            tracking.save_record(record, record_path)
            await until(pilot, lambda: app._data.agenda["availability"] == "head-changed")
            await until(pilot, lambda: not timeline.window.events)
            await pilot.press("escape")

    try:
        asyncio.run(check())
    finally:
        owner.close_admission("closed")
        assert owner.agendas.feed.wait_closed(3)
        owner.shutdown()
        server.close()


@pytest.mark.parametrize("reason", ["hide", "head-change"])
def test_hiding_timeline_cancels_and_reaps_an_owned_rich_point_process(managed, monkeypatch, reason) -> None:
    locks = importlib.import_module("agent_worktrees.locks")
    marker = managed["cwd"].parent / "blocked-reader.json"
    original = rich_commands._point
    block = Event()
    child = (
        "import json,os,sys,time\nfrom pathlib import Path\n"
        f"sys.path[:0]={[str(path) for path in SOURCE_PATHS]!r}\n"
        "from agent_worktrees.locks import process_start_time\n"
        f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),process_start_time(os.getpid())]))\n"
        "time.sleep(60)\n"
    )

    def point(argv, cancelled, cwd):
        if block.is_set() and rich_commands._READ in argv:
            return original([sys.executable, "-I", "-B", "-c", child], cancelled, cwd)
        return original(argv, cancelled, cwd)

    async def no_worktree_source(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(rich_commands, "_point", point)
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: current_managed_data(managed))
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(companion.TimelineAttachment, "set_selection", no_worktree_source)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app._data.agenda, app._data.agenda_error = managed["publication"](), None
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: len(app._timeline_panel.window.events) == 1)
            block.set()
            app._agenda_cancel = Event()
            app._receive_agenda(
                app._data, app._agenda_generation, app._agenda_cancel, managed["update"](), None,
            )
            await until(pilot, marker.exists)
            pid, birth = json.loads(marker.read_text())
            assert locks.pid_alive(pid) and locks.process_start_time(pid) == birth
            if reason == "head-change":
                changed = {**managed["publication"](), "availability": "head-changed", "observation": None}
                app._receive_agenda(app._data, app._agenda_generation, app._agenda_cancel, changed, None)
                assert not app._timeline_panel.window.events, "head clearing waited for a blocked rich read"
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await until(pilot, lambda: app._rich_agenda_attachment._task is None)
            assert not locks.pid_alive(pid) or locks.process_start_time(pid) != birth
            assert app._rich_agenda_attachment._page is None
            assert len(app._timeline_panel.window.events) == (1 if reason == "hide" else 0)
            await pilot.press("escape")

    asyncio.run(check())


def test_terminal_retirement_fences_refresh_and_keeps_failure_custody(managed) -> None:
    async def check() -> None:
        attachment = AgendaTimelineAttachment(
            CompanionTimelinePanel(), cwd=str(managed["cwd"]), status=lambda message: None,
            validate_events=lambda token, events: pytest.fail("terminal retirement must not admit events"),
        )

        async def failed() -> None:
            raise TimelineCleanupError("fixture retirement receipt missing")

        task = asyncio.create_task(failed())
        attachment._task = task
        await attachment.set_selection(None)
        assert attachment._cleanup_failure is task.exception()
        await attachment.set_selection(("example", "selected", "head-a"), managed["publication"]())
        assert attachment.selection is None and attachment._task is task
        assert not managed["trace"].exists(), "terminal cleanup admitted a replacement process"
        with pytest.raises(TimelineCleanupError, match="receipt missing"):
            await attachment.close()

    asyncio.run(check())
