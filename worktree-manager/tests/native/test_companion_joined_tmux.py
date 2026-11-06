"""Attached native client over private source owners, not installed or pixel evidence."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid

import pytest

from worktree_manager import companion_bridge_source, companion_pane, companion_runtime
from worktree_manager import mux_companion, mux_mapping_registry
from _companion_joined_fixtures import managed_join
from test_agenda_end_to_end import ROOT, SOURCE_PATHS
from test_companion_app_tmux import AttachedClient, retire_fixture
from test_companion_bridge_session_source import semantic_peer as semantic_peer
from test_companion_current_requests import actual_waiters as actual_waiters

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.timeout(180),
    pytest.mark.skipif(
        os.name == "nt" or not shutil.which("tmux"),
        reason="Private POSIX tmux/client and source-owner fixtures are required",
    ),
]


def source_entrypoint(
    peer: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run source app/readers with fixture discovery and bounded state observation."""
    data = mux_companion._load_current_worktree(str(peer["checkout"]))
    selection = {
        "worktree": data.worktree, "sessions": data.sessions, "bundle": data.bundle,
    }
    status = {
        "repo": "example", "id": "selected", "path": str(peer["checkout"]),
    }
    engine = mux_companion.ec.engine_base_command()
    bridge = companion_bridge_source.bridge_command(ROOT)
    state_path = peer["checkout"].parent / "native-state.json"
    bootstrap = f"""
import argparse,json,os,sys
from pathlib import Path
sys.path[:0]={[str(ROOT / "worktree-manager/src"), *map(str, SOURCE_PATHS)]!r}
from worktree_manager import companion_bridge_source,engine_client,mux_companion
from worktree_manager.companion_timeline_widget import SubwayTimeline
engine_client.engine_base_command=lambda:{engine!r}
engine_client.current_worktree_status=lambda **kwargs:{status!r}
companion_bridge_source.bridge_command=lambda root:{bridge!r}
mux_companion._load_current_worktree=lambda *args,**kwargs:mux_companion._CompanionData(**{selection!r})
mux_companion.MuxCompanionApp._poll_attention=lambda *args,**kwargs:None
mount=mux_companion.MuxCompanionApp.on_mount
def observed_mount(app):
    mount(app)
    previous=[None]
    def observe():
        timeline=app.query_one(SubwayTimeline)
        bridge=app.query_one('#bridge-feed')
        value=json.dumps({{
            'app_cells':list(app.size),'timeline_cells':list(timeline.size),
            'focus':type(app.focused).__name__,'follow_live':timeline.window.follow_live,
            'bridge_members':len(app._bridge_attachment._members),
            'bridge_select':{{'expanded':bridge.expanded,'value':str(bridge.value),
                             'highlighted':bridge.query_one('SelectOverlay').highlighted}},
            'event_count':len(timeline.window.events),
            'sources':[{{'provider':state.token.identity.provider.value,
                        'before':state.before is not None,
                        'pending':state.requested_before is not None}}
                       for state in timeline.window.sources.values()]
        }})
        if value!=previous[0]:
            path=Path({str(state_path)!r})
            pending=path.with_suffix('.pending')
            pending.write_text(value,encoding='utf-8')
            try:
                pending.replace(path)
            except PermissionError as error:
                if os.name!='nt' or getattr(error,'winerror',None) not in (32,33):
                    raise
                return
            previous[0]=value
    app.set_interval(.05,observe)
mux_companion.MuxCompanionApp.on_mount=observed_mount
parser=argparse.ArgumentParser()
for flag in ('cwd','cli-pane','project','worktree-id','session-incarnation'):
    parser.add_argument('--'+flag)
args=parser.parse_args()
raise SystemExit(mux_companion.run(
    args.cwd,args.cli_pane,project=args.project,worktree_id=args.worktree_id,
    session_incarnation=args.session_incarnation))
"""
    entry = peer["checkout"].parent / "native-app.py"
    entry.write_text(bootstrap, encoding="utf-8")

    def command(arguments: list[str]) -> str:
        assert arguments[:2] == ["-m", "worktree_manager.mux_companion"]
        if os.name == "nt":
            argv = [sys.executable, "-I", "-B", str(entry), *arguments[2:]]
            script = "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
            script += "; exit $LASTEXITCODE"
            return "pwsh -NoLogo -NoProfile -EncodedCommand " + base64.b64encode(
                script.encode("utf-16-le"),
            ).decode("ascii")
        return shlex.join([sys.executable, "-I", "-B", "-c", bootstrap, *arguments[2:]])

    monkeypatch.setattr(companion_runtime, "companion_shell_command", command)
    monkeypatch.setattr(companion_pane.ec, "current_worktree_status", lambda **kwargs: status)


def test_native_three_owner_sparse_view_live_state_and_retirement(
    semantic_peer: dict[str, Any], actual_waiters: Callable[[str], Awaitable[dict[str, Any]]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    for number in range(140):
        semantic_peer["log"].append("tool_call_start", {
            "tool_call_id": str(number), "title": f"Native task {number:03}",
        })
    asyncio.run(actual_waiters("clear"))
    profile, manager = tmp_path / "native-profile", tmp_path / "manager"
    profile.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(profile))
    for key in ("TMUX", "TMUX_PANE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(manager))
    name = "companion-joined-" + uuid.uuid4().hex
    wrapper = tmp_path / "tmux-client"
    wrapper.write_text(
        "#!/bin/sh\nexec " + shlex.quote(shutil.which("tmux")) +
        " -f /dev/null -L " + shlex.quote(name) + ' "$@"\n', encoding="utf-8",
    )
    wrapper.chmod(0o700)
    with managed_join(
        tmp_path / "joined-owners", monkeypatch, clock_origin=time.time() - 60,
    ) as peer:
        source_entrypoint(peer, monkeypatch)
        server = subprocess.Popen(
            [str(wrapper), "-D"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        client = None
        observer = None

        def mux(*arguments: str, check: bool = True) -> Any:
            result = subprocess.run(
                [str(wrapper), *arguments], capture_output=True, text=True, timeout=5,
            )
            if check:
                assert result.returncode == 0, result.stdout + result.stderr
                return result.stdout.rstrip("\r\n")
            return result

        def screen() -> str:
            return mux("capture-pane", "-p", "-t", observer)

        def app_state() -> dict[str, Any]:
            path = peer["checkout"].parent / "native-state.json"
            return json.loads(path.read_text()) if path.exists() else {}

        def geometry(columns: int, rows: int) -> bool:
            lines = screen().splitlines()
            return (
                mux("display-message", "-p", "-t", name, "#{window_width}") == str(columns)
                and mux("display-message", "-p", "-t", name, "#{window_height}") == str(rows - 1)
                and app_state().get("app_cells") == [columns, rows - 1]
                and len(lines) == rows - 1 and len(lines[0]) == columns
                and re.fullmatch("\u256d\u2500+\u256e", lines[0]) is not None
            )

        def wait_for(condition: Callable[[], bool], seconds: float = 30) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if client is not None:
                    client.drain()
                    assert client.process.poll() is None, screen()
                assert server.poll() is None, "private foreground server exited"
                assert not peer["failures"] and not semantic_peer["failures"]
                if condition():
                    return
                time.sleep(.025)
            pytest.fail(screen() + "\n" + json.dumps(app_state()) if observer else "private server was not ready")

        def capture(label: str) -> str:
            value = screen()
            record_property("native-joined-state", label)
            record_property("native-joined-screen", value)
            record_property("native-joined-ansi-json", json.dumps(
                mux("capture-pane", "-e", "-p", "-t", observer),
            ))
            return value

        try:
            wait_for(lambda: mux("list-sessions", check=False).returncode == 0 or
                     "no sessions" in mux("list-sessions", check=False).stderr)
            inert = (
                "import sys;print('INERT SOURCE CLI',flush=True)\n"
                "for line in sys.stdin: print('CLI RECEIVED '+line.strip(),flush=True)\n"
            )
            mux("new-session", "-d", "-s", name, "-c", str(peer["checkout"]), shlex.join([
                sys.executable, "-I", "-u", "-c", inert,
            ]))
            cli, incarnation, pid, window = mux(
                "display-message", "-p", "-t", name,
                "#{pane_id}\t#{session_id}:#{session_created}\t#{pane_pid}\t#{window_id}",
            ).split("\t")
            client = AttachedClient(wrapper, name)
            wait_for(lambda: mux("list-clients", "-F", "#{client_pid}") == str(client.process.pid))
            mux_mapping_registry.register_mapping({
                "project": "example", "worktree_id": "selected", "mux_bin": str(wrapper),
                "mux_session": name, "worktree_path": str(peer["checkout"]),
                "session_incarnation": incarnation,
                "panes": [{"pane_id": cli, "role": "cli", "live": True}],
                "mapping_revision": 1, "live": True,
            }, root=manager)
            observer = companion_pane.switch("show", str(peer["checkout"]))
            wait_for(lambda: "Purpose" in screen() and "Summary" in screen())
            wait_for(lambda: any(action == "agenda-feed" for action, _ in peer["streams"]))
            publication = peer["agenda_publisher"].publish(
                peer["agenda_home"], [], provider_python=peer["provider_python"],
                provider_project="example",
            )
            assert publication["state"] == "delivered", publication
            client.send(b"\x1b[C")
            wait_for(lambda: "Current slice" in screen() and "Evidence" in screen())
            client.click(screen(), "Evidence")
            wait_for(lambda: "Bridge feed" in screen())
            client.click(screen(), "Bridge feed")
            client.send(b"\x1b[B\r")
            wait_for(lambda: "140 starts (observed range)" in screen()
                     and "Bridge: retained / observed" in screen())
            client.click(screen(), "Evidence")
            wait_for(lambda: "\u25b6 Evidence" in screen() and "Tool started" in screen())
            for label, dimensions in (("healthy-full", (110, 40)), ("healthy-narrow", (60, 24))):
                client.resize(*dimensions)
                wait_for(lambda: geometry(*dimensions)
                         and "140 starts (observed range)" in screen()
                         and "\u25b6 Evidence" in screen()
                         and "Tool started" in screen())
                value = capture(label)
                record_property("native-joined-geometry", json.dumps({
                    "client_cells": dimensions, "app_cells": [dimensions[0], dimensions[1] - 1],
                    "tmux_status_rows": 1,
                }))
                assert "No local waiters" in value and "source-error" not in value
                assert "native-tool-start-record-occurrences" not in value
                assert "source-continuity" not in value

            client.resize(110, 40)
            wait_for(lambda: geometry(110, 40))
            client.send(b"\t")
            wait_for(lambda: app_state().get("focus") == "SubwayTimeline")
            initial_work = re.search(r"Native task (\d{3})", screen()).group(1)
            for _ in range(4):
                before = app_state()["sources"]
                before_count = app_state()["event_count"]
                client.send(b"\x1b[H")
                wait_for(lambda: any("/session-observation-history/" in request["path"]
                                     for request in semantic_peer["requests"])
                         or app_state().get("sources") != before
                         and all(not state["pending"] for state in app_state().get("sources", [])))
                if any("/session-observation-history/" in request["path"]
                       for request in semantic_peer["requests"]):
                    break
            else:
                pytest.fail("independent owner history did not reach the selected session")
            wait_for(lambda: app_state().get("event_count", 0) > before_count
                     and all(not state["pending"] for state in app_state().get("sources", []))
                     and (match := re.search(r"Native task (\d{3})", screen())) is not None
                     and int(match.group(1)) < int(initial_work))
            anchor = re.search(r"Native task \d{3}", screen()).group()
            semantic_peer["log"].append("tool_call_start", {
                "tool_call_id": "live-140", "title": "Native task 140",
            })
            peer["publish"]("Native pushed purpose", 4)
            wait_for(lambda: "Native pushed purpose" in screen()
                     and "141 starts (observed range)" in screen()
                     and re.search(r"\b[1-9]\d* unread", screen()) is not None
                     and geometry(110, 40))
            assert anchor in screen() and "Native task 140" not in screen()
            capture("browsing-live")
            client.send(b"\x1b[F")
            wait_for(lambda: "141 starts (observed range)" in screen()
                     and "Native task 140" in screen() and "0 unread" in screen())
            client.send(b"\x1b")
            wait_for(lambda: mux("display-message", "-p", "-t", name, "#{pane_id}") == cli)
            wait_for(lambda: len(mux("list-panes", "-t", name, "-F", "#{pane_id}").splitlines()) == 1)
            wait_for(lambda: peer["settled"]() and semantic_peer["session"].subscriber_count == 0)
            client.send(b"joined-return\r")
            wait_for(lambda: "CLI RECEIVED joined-return" in mux("capture-pane", "-p", "-t", cli))
            assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
            assert mux("display-message", "-p", "-t", cli, "#{session_id}:#{session_created}") == incarnation
            assert mux("display-message", "-p", "-t", cli, "#{window_id}") == window
            record_property("native-joined-boundary", json.dumps({
                "input": "attached tmux client PTY", "tmux": mux("-V"),
                "owners": "private Worktrees resident, managed Agenda, Bridge semantic route/EventLog",
                "substitutions": "root selection/discovery, Bridge loopback carrier, inert CLI",
                "app": "unchanged source application and readers",
                "fixture_observer": "change-only geometry/focus/history snapshots; not performance evidence",
                "display": "tmux server capture; no headed pixels or frame-latency claim",
                "installed": False, "sdk_actor": False, "client_bytes": client.received,
            }))
        finally:
            retire_fixture(mux, client, server, name, observer)
