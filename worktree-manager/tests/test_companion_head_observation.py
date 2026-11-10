"""Optional comparison codec, actual selected-process and Recovery controls."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from threading import Event
import time

import pytest
from textual.widgets import DataTable, Static, TabbedContent

from worktree_manager import companion_attention as attention
from worktree_manager import companion_head_observation as head
from worktree_manager import engine_client as ec
from worktree_manager import mux_companion as companion
from worktree_manager.companion_client import cancellable_read_runner

TIME = "2026-11-06T00:00:00+00:00"


def declaration():
    return {"contract": head.CONTRACT, "version": 1, "command": head.COMMAND}


def observation(*, durable="new", picker="new", status=None):
    return {
        "version": 1,
        "contract": head.CONTRACT,
        "contract_version": 1,
        "project": "example",
        "worktree_id": "selected",
        "subject": {"creation_nonce": "nonce-a", "lifecycle_revision": 5, "head_revision": 4},
        "status": status or ("match" if durable == picker == "new" else "mismatch"),
        "reason": "sampled-identities-match"
        if durable == picker == "new"
        else "durable-head-mismatch",
        "expected": {
            "token": "baton-a",
            "predecessor": "old",
            "consumer": "new",
            "source": "file",
            "consumed_at": TIME,
            "identity": {
                "version": 1,
                "project": "example",
                "worktreeId": "selected",
                "creationNonce": "nonce-a",
                "sessionId": "new",
                "activation": {
                    "version": 1,
                    "ordinal": 2,
                    "startedAt": TIME,
                    "startRecordedAt": TIME,
                    "sourceIdentity": "activation:new",
                },
            },
        },
        "durable_head": {"status": "observed", "session_id": durable, "revision": 4},
        "picker": {"status": "observed", "session_id": picker},
    }


@pytest.mark.parametrize(
    "durable,picker", [("new", "new"), ("old", "new"), ("new", "old"), (None, None)]
)
def test_actual_json_command_retains_three_independent_facts(monkeypatch, durable, picker):
    value = observation(durable=durable, picker=picker)
    calls = []
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["selected-engine"])

    def runner(command, timeout):
        from subprocess import CompletedProcess

        calls.append((command, timeout))
        return CompletedProcess(command, 0, json.dumps(value), "")

    result, error = head.read_observation(
        "example",
        "selected",
        {"handoff_head_observation": declaration()},
        runner=runner,
    )
    assert result == value and error is None
    assert calls == [
        (
            [
                "selected-engine",
                "--project",
                "example",
                head.COMMAND,
                "--worktree",
                "selected",
                "--contract-version",
                "1",
                "--json",
            ],
            5,
        )
    ]
    rendered = head.observation_text(result, error).plain
    assert f"Expected consumer: new\nDurable head: {durable or '(none)'}" in rendered
    assert f"Picker target (sampled): {picker or '(none)'}" in rendered


@pytest.mark.parametrize(
    "snapshot,error",
    [
        ({}, "unsupported-provider"),
        ({"handoff_head_observation": None}, "malformed-declaration"),
        (
            {"handoff_head_observation": {**declaration(), "version": True}},
            "malformed-declaration",
        ),
        (
            {"handoff_head_observation": {**declaration(), "version": 2}},
            "unsupported-contract-version",
        ),
        (
            {"handoff_head_observation": {**declaration(), "command": "repair-head"}},
            "malformed-declaration",
        ),
    ],
)
def test_missing_invalid_or_rolled_back_declaration_never_probes_syntax(
    monkeypatch, snapshot, error
):
    monkeypatch.setattr(
        ec, "run_json", lambda *a, **k: pytest.fail("unsupported optional command")
    )
    assert head.read_observation("example", "selected", snapshot) == (None, error)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("project", "other"),
        ("worktree_id", "other"),
        ("contract_version", True),
        ("status", []),
        ("status", "complete"),
        ("reason", "[red]forged"),
        ("expected", []),
        ("durable_head", []),
        ("subject", []),
    ],
)
def test_malformed_response_is_not_a_qualified_comparison(field, bad):
    value = observation()
    value[field] = bad
    assert head.observation_error(value, "example", "selected")


@pytest.mark.parametrize(
    "change",
    [
        "false-match",
        "false-mismatch",
        "missing-fact",
        "boolean-revision",
        "wrong-revision",
        "wrong-nonce",
        "wrong-consumer",
        "boolean-activation",
        "missing-start",
        "unsafe-session",
        "missing-consumed-time",
        "unobserved-id",
    ],
)
def test_contradictory_subject_identity_and_fact_qualifications_are_rejected(change):
    value = observation()
    if change == "false-match":
        value["picker"]["session_id"] = "old"
    elif change == "false-mismatch":
        value["status"] = "mismatch"
    elif change == "missing-fact":
        del value["picker"]["session_id"]
    elif change == "boolean-revision":
        value["subject"]["head_revision"] = True
    elif change == "wrong-revision":
        value["durable_head"]["revision"] = 3
    elif change == "wrong-nonce":
        value["expected"]["identity"]["creationNonce"] = "other"
    elif change == "wrong-consumer":
        value["expected"]["identity"]["sessionId"] = "old"
    elif change == "boolean-activation":
        value["expected"]["identity"]["activation"]["ordinal"] = True
    elif change == "missing-start":
        del value["expected"]["identity"]["activation"]["startedAt"]
    elif change == "unsafe-session":
        value["expected"]["consumer"] = "../new"
    elif change == "missing-consumed-time":
        value["expected"]["consumed_at"] = None
    else:
        value["picker"]["status"] = "stale"
    assert head.observation_error(value, "example", "selected")


@pytest.mark.parametrize("status", ["unknown", "unavailable"])
def test_unknown_does_not_invent_expected_identity_or_a_null_head(status):
    value = observation(status=status)
    value.pop("subject")
    value.update(
        expected=None,
        durable_head={"status": "unavailable"},
        picker={"status": "stale"},
        reason="source-unreadable-or-invalid",
    )
    assert head.observation_error(value, "example", "selected") is None
    rendered = head.observation_text(value, None).plain
    assert f"comparison: {status}" in rendered and "Expected consumer: unknown" in rendered
    assert "Durable head: unavailable" in rendered and "(none)" not in rendered


def test_real_selected_process_preserves_envelope_and_old_row_api(tmp_path):
    target = tmp_path / "selected checkout"
    target.mkdir()
    snapshot = {
        "sessions": [{"id": "old", "is_head": True}],
        "handoff_head_observation": declaration(),
    }
    value = observation(durable="old")
    script = tmp_path / "fixture_engine.py"
    script.write_text(
        "import json,os,sys\n"
        f"assert os.path.realpath(os.getcwd()) == {str(target.resolve())!r}\n"
        "assert sys.argv[1:3] == ['--project','example']\n"
        "args=sys.argv[3:]\n"
        "if args == ['list-sessions','--worktree','selected','--json']:\n"
        f" print({json.dumps(snapshot)!r})\n"
        "elif args == ['handoff-head-observation','--worktree','selected','--contract-version','1','--json']:\n"
        f" print({json.dumps(value)!r})\n"
        "else: raise SystemExit(2)\n",
        encoding="utf-8",
    )
    ec.set_engine_command([sys.executable, str(script)])
    try:
        runner = cancellable_read_runner(Event(), cwd=str(target))
        actual = ec.list_worktree_session_snapshot("example", "selected", runner=runner)
        assert actual == snapshot
        assert (
            ec.list_worktree_sessions("example", "selected", runner=runner) == snapshot["sessions"]
        )
        assert head.read_observation("example", "selected", actual, runner=runner) == (value, None)
    finally:
        ec.set_engine_command(None)


def _wire_app(monkeypatch, *, result=None):
    values = {"observation": result or observation(durable="old"), "reads": 0}
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["fixture-engine"])
    monkeypatch.setattr(
        ec,
        "current_worktree_status",
        lambda **k: {
            "id": "selected",
            "repo": "example",
            "state": "active",
        },
    )
    monkeypatch.setattr(
        ec,
        "list_worktree_session_snapshot",
        lambda *a, **k: {
            "sessions": [{"id": "old", "is_head": True}, {"id": "new", "is_head": False}],
            "handoff_head_observation": declaration(),
        },
    )
    monkeypatch.setattr(
        companion.cc,
        "worktree_status_bundle",
        lambda *a, **k: {
            "worktree_id": "selected",
            "facts": {},
        },
    )
    monkeypatch.setattr(
        attention,
        "read_session_attention",
        lambda *a, **k: {
            "attention": None,
            "error": "unsupported",
        },
    )

    def read(project, args, **kwargs):
        assert project == "example" and args == [
            head.COMMAND,
            "--worktree",
            "selected",
            "--contract-version",
            "1",
            "--json",
        ]
        values["reads"] += 1
        return deepcopy(values["observation"])

    monkeypatch.setattr(ec, "run_json", read)
    monkeypatch.setattr(
        companion.hc, "trigger_cutover", lambda *a, **k: pytest.fail("implicit cutover")
    )
    return values


@pytest.mark.parametrize("declared", [False, True])
def test_load_pins_discovery_runtime_across_activation_or_rollback(
    monkeypatch, tmp_path, declared
):
    value = observation()
    snapshot = {"sessions": [{"id": "new", "is_head": True}]}
    if declared:
        snapshot["handoff_head_observation"] = declaration()
    selected = tmp_path / "selected.py"
    selected.write_text(
        "import sys\n"
        "if sys.argv[1:] == ['list-sessions','--worktree','selected','--json']:\n"
        f" print({json.dumps(snapshot)!r})\n"
        "elif sys.argv[1:] == ['--project','example','handoff-head-observation',"
        "'--worktree','selected','--contract-version','1','--json']:\n"
        f" print({json.dumps(value)!r})\n"
        "else: raise SystemExit(2)\n",
        encoding="utf-8",
    )
    replacement = tmp_path / "replacement.py"
    replacement.write_text(
        "raise SystemExit('unexpected replacement invocation')\n", encoding="utf-8"
    )
    selected_command = [sys.executable, str(selected)]
    monkeypatch.setattr(
        ec, "current_worktree_status", lambda **k: {"id": "selected", "repo": "example"}
    )
    monkeypatch.setattr(companion.cc, "worktree_status_bundle", lambda *a, **k: {})
    actual_runner = cancellable_read_runner(Event(), cwd=str(tmp_path))
    calls = []

    def runner(command, timeout):
        calls.append(command)
        result = actual_runner(command, timeout)
        if "list-sessions" in command:
            ec.set_engine_command([sys.executable, str(replacement)])
        return result

    ec.set_engine_command(selected_command)
    try:
        data = companion._load_current_worktree(str(tmp_path), runner=runner)
    finally:
        ec.set_engine_command(None)
    assert not data.error and not data.sessions_error and data.sessions == snapshot["sessions"]
    assert all(command[:2] == selected_command for command in calls)
    if declared:
        assert len(calls) == 2 and data.head_observation == value
        assert data.head_observation_error is None
    else:
        assert len(calls) == 1 and data.head_observation is None
        assert data.head_observation_error == "unsupported-provider"


def test_load_cannot_reuse_a_declaration_when_no_engine_is_selected(monkeypatch):
    monkeypatch.setattr(
        ec, "current_worktree_status", lambda **k: {"id": "selected", "repo": "example"}
    )
    monkeypatch.setattr(ec, "engine_base_command", lambda: None)
    monkeypatch.setattr(
        ec, "list_worktree_session_snapshot", lambda *a, **k: pytest.fail("unselected discovery")
    )
    monkeypatch.setattr(companion.cc, "worktree_status_bundle", lambda *a, **k: {})
    data = companion._load_current_worktree()
    assert not data.error and data.sessions_error == "The selected engine is unavailable."
    assert data.head_observation is None


@pytest.mark.parametrize("size", [(50, 24), (110, 40)])
def test_textual_recovery_refresh_reopen_and_independent_lineage(monkeypatch, tmp_path, size):
    values = _wire_app(monkeypatch)

    async def run():
        for expected in ("mismatch", "match"):
            app = companion.MuxCompanionApp(cwd=str(tmp_path))
            async with app.run_test(size=size) as pilot:
                await app.workers.wait_for_complete()
                tabs = app.query_one("#views", TabbedContent)
                assert tabs.active == "summary-tab"
                tabs.active = "recovery-tab"
                await pilot.pause()
                display = app.query_one("#head-comparison", Static)
                assert f"comparison: {expected}" in str(display.content)
                assert app.query_one("#lineage", DataTable).row_count == 2
                assert app.query_one("#cutover-btn").disabled
                assert values["reads"] == (1 if expected == "mismatch" else 3)
                values["observation"] = observation()
                await pilot.press("r")
                await app.workers.wait_for_complete()
                assert "comparison: match" in str(display.content)
                assert app.query_one("#lineage", DataTable).get_row_at(0)[0] == "\u25cf"
                await pilot.press("escape")
            assert not app.is_running
        assert values["reads"] == 4

    asyncio.run(run())


def test_textual_late_comparison_cannot_replace_a_refreshed_selection(monkeypatch, tmp_path):
    _wire_app(monkeypatch)
    first_started, first_released = Event(), Event()
    original = companion._load_current_worktree
    calls = []

    def load(*a, **k):
        data = original(*a, **k)
        calls.append(data)
        if len(calls) == 1:
            first_started.set()
            assert first_released.wait(5)
        else:
            data.head_observation = observation()
        return data

    monkeypatch.setattr(companion, "_load_current_worktree", load)

    async def run():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            assert await asyncio.to_thread(first_started.wait, 5)
            app._reload()
            for _ in range(100):
                if not app._loading:
                    break
                await pilot.pause(0.01)
            assert app._data.head_observation["status"] == "match"
            first_released.set()
            await pilot.pause(0.05)
            assert app._data is calls[1]
            await pilot.press("escape")

    try:
        asyncio.run(run())
    finally:
        first_released.set()


@pytest.mark.parametrize("failure", ["malformed", "failed", "rollback"])
def test_optional_failure_preserves_lineage_and_other_views(monkeypatch, tmp_path, failure):
    _wire_app(monkeypatch)
    if failure == "rollback":
        monkeypatch.setattr(
            ec,
            "list_worktree_session_snapshot",
            lambda *a, **k: {
                "sessions": [{"id": "old", "is_head": True}],
            },
        )
    else:

        def read(*a, **k):
            if failure == "failed":
                raise ec.EngineError("[red]literal\x1b diagnostic")
            return {"status": "match"}

        monkeypatch.setattr(ec, "run_json", read)

    async def run():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(50, 24)) as pilot:
            await app.workers.wait_for_complete()
            assert not app._data.error and not app._data.sessions_error
            assert app._data.sessions
            tabs = app.query_one("#views", TabbedContent)
            for name in ("summary-tab", "claims-tab", "timeline-tab", "recovery-tab"):
                tabs.active = name
                await pilot.pause()
            rendered = str(app.query_one("#head-comparison", Static).content)
            assert "unavailable" in rendered
            if failure == "failed":
                assert "[red]literal\\x1b diagnostic" in rendered and "\x1b" not in rendered
            await pilot.press("escape")

    asyncio.run(run())


def _exited(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


# Signal-0 polling, not pidfd: managed test interpreters may lack os.pidfd_open.
@pytest.mark.skipif(os.name == "nt", reason="POSIX signal-0 child-exit witness")
def test_blocked_optional_process_is_retired_on_escape(tmp_path, monkeypatch):
    started = tmp_path / "comparison.pid"
    script = tmp_path / "blocked_comparison.py"
    script.write_text(
        "import json,os,sys,time\n"
        "from pathlib import Path\n"
        "verb=next(arg for arg in sys.argv[1:] if arg in ['status-segment','list-sessions',"
        "'handoff-head-observation','worktree-status-bundle'])\n"
        "if verb == 'status-segment': print(json.dumps({'id':'selected','repo':'example','state':'active'}))\n"
        "elif verb == 'list-sessions':\n"
        f" print({json.dumps({'sessions': [], 'handoff_head_observation': declaration()})!r})\n"
        "elif verb == 'handoff-head-observation':\n"
        f" Path({str(started)!r}).write_text(str(os.getpid()))\n"
        " time.sleep(60)\n"
        "else: print(json.dumps({'worktree_id':'selected','facts':{}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        attention,
        "read_session_attention",
        lambda *a, **k: {
            "attention": None,
            "error": "unsupported",
        },
    )
    ec.set_engine_command([sys.executable, str(script)])

    async def run():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            for _ in range(100):
                if started.exists():
                    break
                await pilot.pause(0.01)
            assert started.exists()
            pid = int(started.read_text())
            await pilot.press("escape")
            assert await asyncio.to_thread(_exited, pid, 3)
        assert not app.is_running and not app._engine_cleanup_failures

    try:
        asyncio.run(run())
    finally:
        ec.set_engine_command(None)


@pytest.mark.parametrize("task", [False, True])
@pytest.mark.parametrize("durable", ["new", "old"])
def test_actual_owner_receipts_through_selected_process_and_codec(tmp_path, task, durable):
    import yaml

    project = tmp_path / "project"
    state = tmp_path / "session-state"
    directory = project / "worktrees" / "selected" / "handoff"
    directory.mkdir(parents=True)

    def identity(session):
        value = deepcopy(observation()["expected"]["identity"])
        value["sessionId"] = session
        value["activation"].update(ordinal=1, sourceIdentity=f"source:{session}")
        return value

    record = {
        "repo": "example",
        "worktree_id": "selected",
        "creation_nonce": "nonce-a",
        "branch": "worktree/selected",
        "worktree_path": str(tmp_path),
        "machine": "example",
        "platform": "linux",
        "started_at": TIME,
        "last_resumed_at": TIME,
        "resume_count": 0,
        "title": None,
        "status": "active",
        "completed_at": None,
        "head_session": durable,
        "head_revision": 1,
        "lifecycle_revision": 1,
        "sessions": [
            {
                "session_id": session,
                "started_at": TIME,
                "activations": [
                    {
                        "ordinal": 1,
                        "started_at": TIME,
                        "start_recorded_at": TIME,
                        "source_identity": f"source:{session}",
                    },
                ],
            }
            for session in ("old", "new")
        ],
    }
    (project / "worktrees" / "selected.yaml").write_text(yaml.safe_dump(record), encoding="utf-8")
    for session in ("old", "new"):
        home = state / session
        home.mkdir(parents=True)
        (home / "events.jsonl").write_text("", encoding="utf-8")
    token = "baton-a" if task else "handoff-a"
    metadata = {
        "kind": "context-handoff",
        "version": 2,
        "id": token,
        "worktree": "selected",
        "storage": "agent-dispatch" if task else "file",
        "sessionId": "old",
        "createdAt": TIME,
        "authorIdentity": identity("old"),
    }
    consumed = "2026-11-06T00:01:00+00:00"
    if task:
        receipt = {
            "kind": "context-handoff-delivery",
            "version": 1,
            "metadata": metadata,
            "handoffToken": "baton-a",
            "createdAt": TIME,
            "predecessorSession": "old",
            "consumerSession": "new",
            "steps": {"consumeAttempted": True, "taskConsumed": True},
            "stepTimes": {"consumeAttempted": TIME, "taskConsumed": consumed},
            "consumerIdentity": identity("new"),
        }
    else:
        receipt = {
            **metadata,
            "consumed": True,
            "consumedAt": consumed,
            "consumedBySession": "new",
            "consumerIdentity": identity("new"),
        }
    (directory / (f"delivery-{token}.json" if task else f"{token}.json")).write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )
    source = Path(__file__).resolve().parents[2] / "plugins" / "agent-worktrees" / "src"
    script = tmp_path / "actual_provider.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(source)!r})\n"
        "from agent_worktrees.handoff_head_observation import observe\n"
        f"print(json.dumps(observe('example','selected',Path({str(project)!r}),Path({str(state)!r}))))\n",
        encoding="utf-8",
    )
    before = {
        path: path.read_bytes()
        for root in (project, state)
        for path in root.rglob("*")
        if path.is_file()
    }
    ec.set_engine_command([sys.executable, str(script)])
    try:
        value, error = head.read_observation(
            "example",
            "selected",
            {"handoff_head_observation": declaration()},
            runner=cancellable_read_runner(Event(), cwd=str(tmp_path)),
        )
    finally:
        ec.set_engine_command(None)
    assert error is None and value["status"] == ("match" if durable == "new" else "mismatch"), (
        value
    )
    assert value["expected"]["source"] == ("task" if task else "file")
    assert "Consumer activation: 1 (source:new)" in head.observation_details(value).plain
    assert before == {
        path: path.read_bytes()
        for root in (project, state)
        for path in root.rglob("*")
        if path.is_file()
    }
