"""Actual CLI pipes and Textual widgets; only disposable synthetic peers."""

import asyncio
import json
import socket
import sys
from threading import Event

import pytest
from rich.text import Text
from textual.widgets import Static, TabbedContent

from worktree_manager import agenda_transport as transport
from worktree_manager import mux_companion as companion
from worktree_manager.agenda_contract import AgendaUnavailable, MAX_FRAME_BYTES
from test_agenda_contract import publication, publication_v2


_PEER = """
import json,socket,sys
capabilities=json.loads(sys.argv[2])
if sys.argv[3:] == ['--resident-health']:
    print(json.dumps({"ok":True,"health":{"capabilities":capabilities,
        "worktrees":{"status":"ready","generation":"a"*32}},
        "cli_contract_versions":[1,2] if 'worktree-agendas-v2' in capabilities else [1]}))
    sys.exit(0)
expected=[
    'agenda-feed','--project','example','--worktree','selected','--session','head-a','--json'
]
if 'worktree-agendas-v2' in capabilities:
    expected += ['--contract-version','2']
if sys.argv[3:] != expected:
    print('unexpected owned argv', file=sys.stderr, flush=True)
    sys.exit(2)
with socket.create_connection(('127.0.0.1',int(sys.argv[1])), timeout=5) as peer:
    peer.settimeout(None)
    peer.sendall(b'ready\\n')
    while True:
        chunk = peer.recv(4096)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
"""


@pytest.fixture
def synthetic_peer(tmp_path, monkeypatch, request):
    script = tmp_path / "synthetic_agenda_peer.py"
    script.write_text(_PEER, encoding="utf-8")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(8)
    caps = ["worktree-agendas-v1"]
    if getattr(request, "param", 1) == 2:
        caps.append("worktree-agendas-v2")
    command = [sys.executable, str(script), str(listener.getsockname()[1]), json.dumps(caps)]
    monkeypatch.setattr(companion.ec, "engine_base_command", lambda: list(command))
    monkeypatch.setattr(transport, "health_argv", lambda base: [*base, "--resident-health"])
    yield listener
    listener.close()


def accept(listener):
    peer, _ = listener.accept()
    peer.settimeout(4)
    assert peer.recv(6) == b"ready\n"
    return peer


def send(peer, frame):
    peer.sendall(json.dumps(frame, separators=(",", ":")).encode() + b"\n")


def current_data():
    return companion._CompanionData(
        worktree={"repo": "example", "id": "selected", "path": "synthetic"},
        sessions=[{"id": "head-a", "is_head": True}],
    )


@pytest.fixture(autouse=True)
def isolated_companion(monkeypatch):
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: current_data())
    monkeypatch.setattr(companion.ca, "read_session_attention", lambda *a, **kw: {
        "attention": None, "error": "synthetic-unavailable",
    })


def widget(app):
    content = app.query_one("#agenda", Static).content
    return content.plain if isinstance(content, Text) else content.label.plain


async def until(pilot, predicate):
    for _ in range(80):
        if predicate():
            return
        await pilot.pause(0.025)
    assert predicate()


def test_committed_push_changes_real_widget_without_reload_or_agenda_scan(synthetic_peer, tmp_path):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                send(peer, publication())
                await until(pilot, lambda: "Action 1" in widget(app))
                app.query_one("#views", TabbedContent).active = "agenda-tab"
                await pilot.pause()
                assert app.query_one("#agenda", Static).region.height > 0
                data = app._data
                candidate = publication(1, kind="update", revision=2)
                # A partial pipe write is not a commit and must not update the UI.
                encoded = json.dumps(candidate, separators=(",", ":")).encode()
                peer.sendall(encoded[:len(encoded) // 2])
                await pilot.pause(0.1)
                assert "Action 1" in widget(app)
                peer.sendall(encoded[len(encoded) // 2:] + b"\n")
                await until(pilot, lambda: "Action 2" in widget(app))
                assert app._data is data
                assert app._data.agenda["sequence"] == 1
                assert "not acceptance" in widget(app)
                await pilot.press("escape")
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


@pytest.mark.parametrize("synthetic_peer", [2], indirect=True)
def test_v2_cursor_four_projection_reaches_real_widget_after_negotiation(synthetic_peer, tmp_path):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                first = publication_v2()
                send(peer, first)
                await until(pilot, lambda: app._data.agenda is not None)
                assert app._data.agenda == first
                assert "replanning" in widget(app) and "Original root" in widget(app)
                data, worker = app._data, app._agenda_worker
                updated = publication_v2(1, kind="update", revision=2)
                updated["observation"]["snapshot"]["notes"] = ["New context reference: context-3"]
                send(peer, updated)
                await until(pilot, lambda: "context-3" in widget(app))
                assert app._data is data and app._agenda_worker is worker
                assert app._data.agenda["observation"] == updated["observation"]
                await pilot.press("escape")
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


def test_reload_retires_held_pipe_and_late_reply_cannot_overwrite(synthetic_peer, tmp_path):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            first = await asyncio.to_thread(accept, synthetic_peer)
            with first:
                send(first, publication())
                await until(pilot, lambda: app._data.agenda is not None)
                old_data, old_generation, old_cancel = app._data, app._agenda_generation, app._agenda_cancel
                app.action_refresh()
                second = await asyncio.to_thread(accept, synthetic_peer)
                with second:
                    assert await asyncio.to_thread(first.recv, 1) == b""
                    assert old_cancel.is_set()
                    send(second, publication(revision=4, generation="c" * 32))
                    await until(pilot, lambda: "Action 4" in widget(app))
                    app._receive_agenda(old_data, old_generation, old_cancel, publication(), None)
                    assert "Action 4" in widget(app)
                    assert app._data is not old_data
                    await pilot.press("escape")
                    assert await asyncio.to_thread(second.recv, 1) == b""
    asyncio.run(check())


@pytest.mark.parametrize("change, expected", [
    ({"project": "foreign"}, "selected source"),
    ({"generation": "c" * 32}, "regressed"),
    ({"sequence": 0}, "regressed"),
    ({"availability": "head-changed", "observation": None}, "Refresh required"),
    ({"generation": []}, "selected source"),
])
def test_invalid_or_changed_head_clears_real_widget(synthetic_peer, tmp_path, change, expected):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                send(peer, publication())
                await until(pilot, lambda: "Action 1" in widget(app))
                send(peer, {**publication(1, kind="update", revision=2), **change})
                await until(pilot, lambda: expected in widget(app))
                assert "Action 1" not in widget(app)
                assert app._data.agenda is None or app._data.agenda["observation"] is None
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


def test_unsupported_provider_does_not_retry_or_disable_summary(synthetic_peer, tmp_path):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                send(peer, {
                    "type": "error", "ok": False, "contract_version": 1,
                    "error": {"code": "incompatible", "message": "worktree-agendas-v1 unavailable"},
                })
                await until(pilot, lambda: "incompatible" in widget(app))
                await app._agenda_worker.wait()
                assert "Unavailable" in widget(app)
                assert app._data.error is None
                assert await asyncio.to_thread(peer.recv, 1) == b""
            synthetic_peer.settimeout(0.15)
            with pytest.raises(TimeoutError):
                synthetic_peer.accept()
    asyncio.run(check())


def test_cutover_reconnect_requires_new_atomic_snapshot(synthetic_peer, tmp_path):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            first = await asyncio.to_thread(accept, synthetic_peer)
            with first:
                send(first, publication())
                await until(pilot, lambda: "Action 1" in widget(app))
                send(first, {"type": "going-away", "generation": "a" * 32, "sequence": 0, "reason": "cutover"})
                await until(pilot, lambda: "new snapshot required" in widget(app))
                assert "Action 1" not in widget(app)
                second = await asyncio.to_thread(accept, synthetic_peer)
                with second:
                    send(second, publication(revision=2, generation="c" * 32))
                    await until(pilot, lambda: "Action 2" in widget(app))
                    assert app._data.agenda["generation"] == "c" * 32
                    await pilot.press("escape")
                    assert await asyncio.to_thread(second.recv, 1) == b""
    asyncio.run(check())


@pytest.mark.parametrize("missing", ["head", "ambiguous", "project"])
def test_absent_selection_is_explicitly_unavailable_without_command(monkeypatch, missing):
    data = current_data()
    if missing == "head":
        data.sessions = []
    elif missing == "ambiguous":
        data.sessions.append({"id": "head-b", "is_head": True})
    else:
        data.worktree.pop("repo")
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data)
    monkeypatch.setattr(companion.ec, "engine_base_command", lambda: pytest.fail("no selection must not spawn"))

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test():
            await app.workers.wait_for_complete()
            assert "Unavailable" in widget(app)
            assert app._agenda_worker is None
    asyncio.run(check())


@pytest.mark.parametrize("raw, expected", [
    (b"{" + b"x" * MAX_FRAME_BYTES, "frame bound"),
    (b"not-json\n", "Expecting"),
    (b'{"type":"snapshot","type":"update"}\n', "duplicate JSON member"),
])
def test_actual_transport_refuses_oversize_and_malformed(synthetic_peer, tmp_path, raw, expected):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                peer.sendall(raw)
                await until(pilot, lambda: expected in widget(app))
                assert "Unavailable" in widget(app)
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


def test_deadline_and_cancellation_while_no_complete_frame(synthetic_peer, tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "STARTUP_TIMEOUT", 0.2)
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                peer.sendall(b'{"type":')
                await until(pilot, lambda: "frame deadline" in widget(app))
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


def test_unresolved_or_bare_path_engine_is_unavailable(monkeypatch):
    for command in (None, ["agent-worktrees"]):
        monkeypatch.setattr(companion.ec, "engine_base_command", lambda: command)
        with pytest.raises(AgendaUnavailable, match="resolved absolute"):
            transport.feed_argv("example", "selected", "head-a")


def test_unadmitted_wrapper_never_reaches_legacy_cli(monkeypatch, tmp_path):
    command = [
        sys.executable, "-c",
        "import sys; print('unrecognized arguments: agenda-feed',file=sys.stderr); sys.exit(2)",
    ]
    calls = []

    def resolved():
        calls.append(1)
        return command

    monkeypatch.setattr(companion.ec, "engine_base_command", resolved)

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            await until(pilot, lambda: "no admitted read-only resident health probe" in widget(app))
            await app._agenda_worker.wait()
            assert calls == [1]
            assert app._data.agenda is None
    asyncio.run(check())


@pytest.mark.parametrize("state", ["unsupported", "offline", "malformed", "failed"])
def test_old_provider_probe_never_invokes_new_argv_or_default_launch(monkeypatch, tmp_path, state):
    script = tmp_path / "legacy_provider.py"
    trace = tmp_path / "legacy_argv.jsonl"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"trace=Path({str(trace)!r})\n"
        "with trace.open('a') as log: log.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if sys.argv[1:] != ['--resident-health']:\n"
        "    with trace.open('a') as log: log.write('DEFAULT-LAUNCH\\n')\n"
        "    sys.exit(91)\n"
        f"state={state!r}\n"
        "if state == 'offline':\n"
        "    print(json.dumps({'ok':False,'error':'resident offline'}))\n"
        "else:\n"
        "    print(json.dumps({'ok':True,'health':{'capabilities':"
        "[] if state == 'unsupported' else ['worktree-agendas-v1'],"
        "'worktrees':{'status':'ready','generation':"
        "'bad' if state == 'malformed' else 'a'*32}}}))\n"
        "sys.exit(2 if state == 'failed' else 0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(companion.ec, "engine_base_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(transport, "health_argv", lambda base: [*base, "--resident-health"])

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            await until(pilot, lambda: trace.exists())
            await app._agenda_worker.wait()
            assert "Unavailable" in widget(app)
            assert app._data.agenda is None
            assert trace.read_text().splitlines() == ['["--resident-health"]']
    asyncio.run(check())


@pytest.mark.parametrize("probe_state", ["unsupported", "offline", "missing"])
def test_real_health_probe_code_never_enters_legacy_main(monkeypatch, tmp_path, probe_state):
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "default-launch"
    (package / "__main__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('launch')\n",
        encoding="utf-8",
    )
    if probe_state != "missing":
        (package / "worktrees_client.py").write_text(
            "def read_health():\n" + (
                "    raise ConnectionError('resident offline')\n"
                if probe_state == "offline"
                else "    return {'capabilities':[], 'worktrees':{'status':'ready','generation':'a'*32}}\n"
            ), encoding="utf-8",
        )
    monkeypatch.setattr(companion.ec, "engine_base_command", lambda: [
        sys.executable, "-m", "agent_worktrees",
    ])
    monkeypatch.setattr(transport, "_HEALTH_PROBE",
        f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n" + transport._HEALTH_PROBE)
    expected = {
        "unsupported": "lacks worktree-agendas-v1",
        "offline": "resident offline",
        "missing": "health API is unsupported",
    }[probe_state]

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            await until(pilot, lambda: expected in widget(app))
            await app._agenda_worker.wait()
            assert not marker.exists()
            assert app._data.agenda is None
    asyncio.run(check())


@pytest.mark.parametrize("declaration,expected", [
    ("CAPABILITY='worktree-agendas-v1'\n", 1),
    ("CAPABILITY='worktree-agendas-v1'\nCONTRACT_VERSIONS=(1,2)\n", 2),
    (None, None),
])
def test_actual_probe_requires_selected_cli_support_before_new_flag(
    monkeypatch, tmp_path, declaration, expected,
):
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "default-launch"
    (package / "__main__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('launch')\n",
        encoding="utf-8",
    )
    (package / "worktrees_client.py").write_text(
        "def read_health():\n"
        "    return {'capabilities':['worktree-agendas-v1','worktree-agendas-v2'],"
        "'worktrees':{'status':'ready','generation':'a'*32}}\n",
        encoding="utf-8",
    )
    if declaration is not None:
        (package / "agenda_contract.py").write_text(declaration, encoding="utf-8")
    monkeypatch.setattr(
        transport, "_HEALTH_PROBE",
        f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n" + transport._HEALTH_PROBE,
    )
    base = [sys.executable, "-m", "agent_worktrees"]
    if expected is None:
        with pytest.raises(AgendaUnavailable) as error:
            transport.require_capability(base, cancelled=Event(), cwd=str(tmp_path))
        assert error.value.code == "unsupported"
    else:
        version = transport.require_capability(base, cancelled=Event(), cwd=str(tmp_path))
        assert version == expected
        argv = transport.feed_argv(
            "example", "selected", "head-a", engine_command=base, contract_version=version,
        )
        assert ("--contract-version" in argv) == (expected == 2)
    assert not marker.exists()


def test_preflight_keeps_the_same_resolved_provider_for_feed(monkeypatch, synthetic_peer, tmp_path):
    base = companion.ec.engine_base_command()
    resolutions = []

    def resolve():
        resolutions.append(1)
        assert len(resolutions) == 1, "feed must retain the probed provider argv"
        return list(base)

    monkeypatch.setattr(companion.ec, "engine_base_command", resolve)

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                send(peer, publication())
                await until(pilot, lambda: "Action 1" in widget(app))
                assert resolutions == [1]
                await pilot.press("escape")
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


@pytest.mark.parametrize("statement,code,retry", [
    ("raise ConnectionRefusedError('permission denied')", "disconnected", True),
    ("raise TimeoutError('malformed response')", "timeout", True),
    ("raise PermissionError('connection timed out')", "permission", False),
    ("raise FileNotFoundError('connection refused')", "authentication", False),
    ("raise ValueError('unready')", "malformed", False),
    ("raise RuntimeError('connection timed out')", "probe-failed", False),
    ("error=ValueError('temporary'); error.code='unready'; raise error", "unready", True),
])
def test_owned_health_probe_classifies_failures_without_message_guesses(
    monkeypatch, tmp_path, statement, code, retry,
):
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "worktrees_client.py").write_text(
        "def read_health():\n    " + statement + "\n", encoding="utf-8",
    )
    monkeypatch.setattr(transport, "_HEALTH_PROBE",
        f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n" + transport._HEALTH_PROBE)
    admitted = []
    with pytest.raises(AgendaUnavailable) as error:
        transport.require_capability(
            [sys.executable, "-m", "agent_worktrees"],
            cancelled=Event(), cwd=str(tmp_path), on_supported=lambda: admitted.append(True),
        )
    assert error.value.code == code and error.value.retry is retry
    assert not admitted


@pytest.mark.parametrize("status", ["closed", "shutdown"])
def test_known_terminal_health_states_are_not_malformed(monkeypatch, tmp_path, status):
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    health = {
        "capabilities": ["worktree-agendas-v1"],
        "worktrees": {"status": status, "generation": "a" * 32},
    }
    (package / "worktrees_client.py").write_text(
        f"def read_health():\n    return {health!r}\n", encoding="utf-8",
    )
    monkeypatch.setattr(
        transport, "_HEALTH_PROBE",
        f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n" + transport._HEALTH_PROBE,
    )
    admitted = []
    with pytest.raises(AgendaUnavailable) as error:
        transport.require_capability(
            [sys.executable, "-m", "agent_worktrees"], cancelled=Event(),
            cwd=str(tmp_path), on_supported=lambda: admitted.append(True),
        )
    assert error.value.code == status and not error.value.retry
    assert admitted == [True]


def test_reconnect_attempts_are_bounded_under_one_worker(synthetic_peer, tmp_path):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            worker = None
            for attempt in range(4):
                peer = await asyncio.to_thread(accept, synthetic_peer)
                with peer:
                    if worker is None:
                        worker = app._agenda_worker
                    assert app._agenda_worker is worker
                    generation = format(attempt + 10, "032x")
                    send(peer, publication(revision=attempt + 1, generation=generation))
                    await until(pilot, lambda: app._data.agenda is not None)
                    send(peer, {
                        "type": "going-away", "generation": generation,
                        "sequence": 0, "reason": "synthetic cutover",
                    })
                    await until(pilot, lambda: app._data.agenda is None)
                    assert await asyncio.to_thread(peer.recv, 1) == b""
            await worker.wait()
            assert "reconnecting" not in widget(app)
            synthetic_peer.settimeout(0.15)
            with pytest.raises(TimeoutError):
                synthetic_peer.accept()
    asyncio.run(check())


def test_observer_project_fallback_requires_selected_worktree(monkeypatch, synthetic_peer, tmp_path):
    data = current_data()
    data.worktree.pop("repo")
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data)

    async def check():
        app = companion.MuxCompanionApp(
            cwd=str(tmp_path), project="example", worktree_id="selected",
            session_incarnation="pane-incarnation-not-head-id",
        )
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                send(peer, publication())
                await until(pilot, lambda: "Action 1" in widget(app))
                assert app._data.agenda["session_id"] == "head-a"
                await pilot.press("escape")
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


@pytest.mark.parametrize("contained", [True, False])
def test_escape_retires_actual_transport_before_first_snapshot(
    monkeypatch, synthetic_peer, tmp_path, contained,
):
    monkeypatch.setattr(transport, "contained_test_mode", lambda: contained)

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                peer.sendall(b'{"type":')
                await pilot.press("escape")
                assert await asyncio.to_thread(peer.recv, 1) == b""
    asyncio.run(check())


def test_retired_generation_cannot_return_on_reconnect(synthetic_peer, tmp_path):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            first = await asyncio.to_thread(accept, synthetic_peer)
            with first:
                send(first, publication())
                await until(pilot, lambda: "Action 1" in widget(app))
                send(first, {"type": "going-away", "generation": "a" * 32, "sequence": 0, "reason": "cutover"})
                second = await asyncio.to_thread(accept, synthetic_peer)
                with second:
                    send(second, publication(revision=2))
                    await until(pilot, lambda: "retired resident generation" in widget(app))
                    assert "Action 1" not in widget(app)
                    assert await asyncio.to_thread(second.recv, 1) == b""
    asyncio.run(check())


@pytest.mark.parametrize("extra_byte", [0, 1])
def test_actual_atomic_frame_exact_35_kib_boundary(synthetic_peer, tmp_path, extra_byte):
    frame = publication()
    frame["observation"]["snapshot"]["notes"] = ["x" * 4000] * 8
    frame["padding"] = ""
    raw = json.dumps(frame, separators=(",", ":")).encode()
    frame["padding"] = "x" * (MAX_FRAME_BYTES - len(raw) - 1 + extra_byte)
    raw = json.dumps(frame, separators=(",", ":")).encode() + b"\n"
    assert len(raw) == MAX_FRAME_BYTES + extra_byte

    async def check():
        cancelled = Event()
        stream = transport.frames(
            "example", "selected", "head-a", cancelled=cancelled, cwd=str(tmp_path),
        )
        task = asyncio.create_task(asyncio.to_thread(next, stream, None))
        try:
            peer = await asyncio.to_thread(accept, synthetic_peer)
            with peer:
                peer.sendall(raw)
                if extra_byte:
                    with pytest.raises(ValueError, match="frame bound"):
                        await task
                else:
                    assert await task == frame
                cancelled.set()
                await asyncio.to_thread(stream.close)
                assert await asyncio.to_thread(peer.recv, 1) == b""
        finally:
            cancelled.set()
            if not task.done():
                await task
            await asyncio.to_thread(stream.close)
    asyncio.run(check())


def changed_snapshot(frame, **values):
    frame["observation"]["snapshot"].update(values)
    return frame


@pytest.mark.parametrize("next_frame, expected", [
    (publication(4, revision=2), "resident sequence"),
    (publication(6, revision=1, generation="c" * 32), "store revision"),
    (publication(5, revision=2), None),
    (publication(5, revision=3), "without advancing"),
    ({**publication(5), "availability": "not-published", "observation": None}, "without advancing"),
    (changed_snapshot(publication(5, revision=2), status="steering"), "without advancing"),
    (changed_snapshot(publication(5, revision=2), next_action="Changed body"), "without advancing"),
])
def test_reconnect_snapshot_cannot_regress_known_publication(
    synthetic_peer, tmp_path, next_frame, expected,
):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            first = await asyncio.to_thread(accept, synthetic_peer)
            with first:
                send(first, publication(5, revision=2))
                await until(pilot, lambda: "Action 2" in widget(app))
            second = await asyncio.to_thread(accept, synthetic_peer)
            with second:
                send(second, next_frame)
                if expected:
                    await until(pilot, lambda: expected in widget(app))
                    assert app._data.agenda is None
                else:
                    await until(pilot, lambda: app._data.agenda is not None)
                    assert app._data.agenda == next_frame
                await pilot.press("escape")
                assert await asyncio.to_thread(second.recv, 1) == b""
    asyncio.run(check())


@pytest.mark.parametrize("generation,sequence", [("a" * 32, 7), ("c" * 32, 0)])
def test_revision_fence_survives_unpublished_reconnect_without_bad_paint(
    synthetic_peer, tmp_path, monkeypatch, generation, sequence,
):
    paints = []
    update = Static.update

    def observe_paint(self, *args, **kwargs):
        result = update(self, *args, **kwargs)
        if self.id == "agenda":
            content = self.content
            paints.append(content.plain if isinstance(content, Text) else content.label.plain)
        return result

    monkeypatch.setattr(Static, "update", observe_paint)
    transient = {
        "type": "error", "ok": False, "contract_version": 1,
        "error": {"code": "unavailable", "message": "synthetic route retry"},
    }

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            first = await asyncio.to_thread(accept, synthetic_peer)
            with first:
                send(first, publication(5, revision=3))
                await until(pilot, lambda: "Action 3" in widget(app))
                data, worker = app._data, app._agenda_worker
                send(first, transient)
                await until(pilot, lambda: "reconnecting in 1s" in widget(app))
                assert await asyncio.to_thread(first.recv, 1) == b""
            second = await asyncio.to_thread(accept, synthetic_peer)
            with second:
                send(second, {**publication(6), "availability": "not-published", "observation": None})
                await until(pilot, lambda: "Not published" in widget(app))
                assert app._data.agenda["observation"] is None
                assert "Action 3" not in widget(app)
                send(second, transient)
                await until(pilot, lambda: "reconnecting in 2s" in widget(app))
                assert await asyncio.to_thread(second.recv, 1) == b""
            third = await asyncio.to_thread(accept, synthetic_peer)
            with third:
                send(third, publication(sequence, revision=2, generation=generation))
                await until(pilot, lambda: "store revision" in widget(app))
                await worker.wait()
                assert app._data is data and app._agenda_worker is worker
                assert app._data.agenda is None
                assert "Action 2" not in widget(app)
                assert not any("Action 2" in paint for paint in paints)
                assert await asyncio.to_thread(third.recv, 1) == b""

    asyncio.run(check())


@pytest.mark.parametrize("sequence,generation,revision", [
    (6, "a" * 32, 3), (7, "a" * 32, 4), (0, "c" * 32, 4),
])
def test_reconnect_publication_fence_allows_qualified_snapshot_after_update(
    synthetic_peer, tmp_path, sequence, generation, revision,
):
    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            first = await asyncio.to_thread(accept, synthetic_peer)
            with first:
                send(first, publication(5, revision=2))
                await until(pilot, lambda: "Action 2" in widget(app))
                send(first, publication(6, kind="update", revision=3))
                await until(pilot, lambda: "Action 3" in widget(app))
                data, worker = app._data, app._agenda_worker
                send(first, {
                    "type": "error", "ok": False, "contract_version": 1,
                    "error": {"code": "disconnected", "message": "synthetic route retry"},
                })
                await until(pilot, lambda: "reconnecting in 1s" in widget(app))
                assert await asyncio.to_thread(first.recv, 1) == b""
            second = await asyncio.to_thread(accept, synthetic_peer)
            with second:
                frame = publication(sequence, revision=revision, generation=generation)
                send(second, frame)
                await until(pilot, lambda: app._data.agenda is not None)
                assert app._data.agenda == frame
                assert app._data is data and app._agenda_worker is worker
                assert f"Action {revision}" in widget(app)
                await pilot.press("escape")
                assert await asyncio.to_thread(second.recv, 1) == b""

    asyncio.run(check())
