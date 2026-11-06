"""Actual private source owners through PSMux/ConPTY, not installed or pixel proof."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid

import psutil
import pytest

from worktree_manager import companion_pane, mux_mapping_registry
from _companion_joined_fixtures import managed_join
from test_companion_bridge_session_source import semantic_peer as semantic_peer
from test_companion_current_requests import actual_waiters as actual_waiters
from test_companion_joined_tmux import source_entrypoint

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.timeout(180),
    pytest.mark.skipif(
        os.name != "nt" or not (os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux"))
        or not shutil.which("pwsh"),
        reason="Native Windows PSMux, PowerShell and ConPTY are required",
    ),
]


def live_reader_pids(trace: Path, requests: list[dict[str, Any]], locks: Any) -> set[int]:
    candidates = [
        child for child in map(json.loads, trace.read_text(encoding="utf-8").splitlines())
        if "--follow" in child["argv"] or "agenda-feed" in child["argv"]
    ]
    candidates.extend(request for request in requests if "follow=true" in request["path"])
    return {
        child["pid"] for child in candidates if locks.pid_alive(child["pid"])
        and locks.process_start_time(child["pid"]) == child["birth"]
    }


def owner_commands_settled(trace: Path, locks: Any, client: Any, pinned: dict[int, Any]) -> bool:
    for child in map(json.loads, trace.read_text(encoding="utf-8").splitlines()):
        pid, birth = child["pid"], str(child["birth"])
        handle = pinned.get(pid)
        if handle is not None and str(client.birth(handle)) == birth:
            if not client.exited(handle):
                return False
            continue
        if locks.pid_alive(pid):
            current = locks.process_start_time(pid)
            if current is None or str(current) == birth:
                return False
    return True


def test_native_windows_three_owner_history_live_and_same_cli(
    semantic_peer: dict[str, Any], actual_waiters: Callable[[str], Awaitable[dict[str, Any]]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    from _terminal_console_fixture import Console

    for number in range(140):
        semantic_peer["log"].append("tool_call_start", {
            "tool_call_id": str(number), "title": f"Native task {number:03}",
        })
    asyncio.run(actual_waiters("clear"))
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    session = "companion-joined-" + uuid.uuid4().hex
    profile, manager = tmp_path / "profile", tmp_path / "manager"
    profile.mkdir()
    for key in list(os.environ):
        if key.startswith("PSMUX_") or key in {"TMUX", "TMUX_PANE", "WT_SESSION"}:
            monkeypatch.delenv(key)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(profile))
    monkeypatch.setenv("PSMUX_NO_WARM", "1")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(manager))
    config = tmp_path / "psmux.conf"
    config.write_text("", encoding="utf-8")

    with managed_join(
        tmp_path / "joined-owners", monkeypatch, clock_origin=time.time() - 60,
    ) as peer:
        source_entrypoint(peer, monkeypatch)
        inert, started = tmp_path / "inert.py", tmp_path / "inert-started.json"
        inert.write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            f"Path({str(started)!r}).write_text(json.dumps({{'pid':os.getpid()}}))\n"
            "print('INERT SOURCE CLI',flush=True)\n"
            "for line in sys.stdin: print('CLI RECEIVED '+line.strip(),flush=True)\n",
            encoding="utf-8",
        )
        environment = dict(os.environ)

        def mux(*args: str, check: bool = True) -> Any:
            arguments = list(args)
            if "-t" in arguments:
                index = arguments.index("-t") + 1
                if arguments[index].startswith("%"):
                    arguments[index] = companion_pane._psmux_pane_target(
                        binary, session, arguments[index], incarnation,
                    )
            result = subprocess.run(
                [binary, *arguments], capture_output=True, encoding="utf-8", timeout=5,
                env=dict(environment, PSMUX_SESSION=session),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if check:
                assert result.returncode == 0, result.stdout + result.stderr
                return result.stdout.rstrip("\r\n")
            return result

        argv = [
            binary, "-f", str(config), "new-session", "-s", session, "-c", str(peer["checkout"]),
            "--", sys.executable, "-I", "-u", str(inert),
        ]
        pinned: dict[int, Any] = {}
        observer = None
        cli_pid = None
        with Console(argv, env=environment, cwd=peer["checkout"]) as client:
            def screen() -> str:
                return mux("capture-pane", "-p", "-t", observer or session)

            def app_state() -> dict[str, Any]:
                path = peer["checkout"].parent / "native-state.json"
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

            def wait_for(
                condition: Callable[[], bool], describe: Callable[[], dict[str, Any]] | None = None,
            ) -> None:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert not client.exited(client.process.process), client.output.decode("utf-8", "replace")
                    assert client.read_error is None, client.read_error
                    assert not peer["failures"] and not semantic_peer["failures"]
                    if condition() and time.monotonic() < deadline:
                        return
                    time.sleep(.025)
                pytest.fail(json.dumps({
                    "observation": describe() if describe is not None else {"screen": screen()},
                    "state": app_state(),
                    "client": client.output.decode("utf-8", "replace"),
                    "cli_started": started.read_text() if started.exists() else None,
                    "bridge_failure": (
                        semantic_peer["diagnostic"].read_text(encoding="utf-8")
                        if semantic_peer["diagnostic"].exists() else None
                    ),
                    "native_process": client.process_state(cli_pid) if cli_pid is not None else None,
                }))

            def pin_owned(pid: int) -> Any:
                process = psutil.Process(pid)
                parents = process.parents()
                parent_ids = [parent.pid for parent in parents]
                assert client.process.pid in parent_ids, "Reader escaped strict caller ownership"
                for owned in [process, *parents[:parent_ids.index(client.process.pid)]]:
                    if owned.pid not in pinned:
                        pinned[owned.pid] = client.pin(owned.pid)
                return pinned[pid]

            def focus(kind: str) -> None:
                for _ in range(16):
                    if app_state().get("focus") == kind:
                        return
                    client.send(b"\t")
                    time.sleep(.1)
                pytest.fail(f"Client could not focus {kind}: {app_state()}\n{screen()}")

            try:
                wait_for(lambda: mux("has-session", "-t", session, check=False).returncode == 0)
                cli, incarnation, pid, window = mux(
                    "display-message", "-p", "-t", session,
                    "#{pane_id}\t#{session_id}:#{session_created}\t#{pane_pid}\t#{window_id}",
                ).split("\t")
                cli_pid = int(pid)
                cli_handle = pin_owned(int(pid))
                birth = client.birth(cli_handle)
                wait_for(lambda: "INERT SOURCE CLI" in mux("capture-pane", "-p", "-t", cli))
                mux_mapping_registry.register_mapping({
                    "project": "example", "worktree_id": "selected", "mux_bin": binary,
                    "mux_session": session, "worktree_path": str(peer["checkout"]),
                    "session_incarnation": incarnation,
                    "panes": [{"pane_id": cli, "role": "cli", "live": True}],
                    "mapping_revision": 1, "live": True,
                }, root=manager)
                observer = companion_pane.switch("show", str(peer["checkout"]))
                observer_handle = pin_owned(int(mux(
                    "display-message", "-p", "-t", observer, "#{pane_pid}",
                )))
                wait_for(lambda: "Purpose" in screen() and "Summary" in screen())
                wait_for(lambda: any(action == "agenda-feed" for action, _ in peer["streams"]))
                publication = peer["agenda_publisher"].publish(
                    peer["agenda_home"], [], provider_python=peer["provider_python"],
                    provider_project="example",
                )
                assert publication["state"] == "delivered", publication
                client.send(b"\x1b[C")
                wait_for(lambda: "Current slice" in screen() and "Evidence" in screen())
                focus("CollapsibleTitle")
                client.send(b"\r")
                focus("Select")
                wait_for(lambda: "Bridge feed" in screen()
                         and app_state().get("bridge_members", 0) > 0)
                client.send(b"\r\x1b[B\r")
                wait_for(lambda: "140 starts (observed range)" in screen()
                         and "Bridge: retained / observed" in screen())
                focus("CollapsibleTitle")
                client.send(b"\r")
                wait_for(lambda: "\u25b6 Evidence" in screen() and "Tool started" in screen())
                for width, height in ((110, 40), (60, 24)):
                    client.resize(width, height)
                    wait_for(lambda: app_state().get("app_cells", [0])[0] == width
                             and "140 starts (observed range)" in screen()
                             and "Tool started" in screen())
                    value = screen()
                    assert "No local waiters" in value and "source-error" not in value
                    assert "native-tool-start-record-occurrences" not in value
                    record_property("native-windows-joined-screen", value)
                    record_property("native-windows-joined-geometry", json.dumps({
                        "client_cells": [width, height], "observed_app": app_state(),
                    }))
                client.resize(110, 40)
                wait_for(lambda: app_state().get("app_cells", [0])[0] == 110)
                focus("SubwayTimeline")
                initial = re.search(r"Native task (\d{3})", screen()).group(1)
                for _ in range(4):
                    before_count = app_state()["event_count"]
                    before = app_state()["sources"]
                    client.send(b"\x1b[H")
                    wait_for(lambda: any(
                        "/session-observation-history/" in request["path"]
                        for request in semantic_peer["requests"]
                    ) or app_state().get("sources") != before
                        and all(not item["pending"] for item in app_state().get("sources", [])))
                    if any("/session-observation-history/" in request["path"]
                           for request in semantic_peer["requests"]):
                        break
                else:
                    pytest.fail("Independent owner history did not reach the selected session")
                wait_for(lambda: app_state().get("event_count", 0) > before_count
                    and all(not item["pending"] for item in app_state().get("sources", [])))
                wait_for(lambda: (match := re.search(r"Native task (\d{3})", screen())) is not None
                         and int(match.group(1)) < int(initial))
                anchor = re.search(r"Native task \d{3}", screen()).group()
                semantic_peer["log"].append("tool_call_start", {
                    "tool_call_id": "live-140", "title": "Native task 140",
                })
                peer["publish"]("Native pushed purpose", 4)
                wait_for(lambda: "Native pushed purpose" in screen()
                         and "141 starts (observed range)" in screen()
                         and re.search(r"\b[1-9]\d* unread", screen()) is not None)
                assert anchor in screen() and "Native task 140" not in screen()
                client.send(b"\x1b[F")
                wait_for(lambda: "Native task 140" in screen() and "0 unread" in screen())
                locks = sys.modules["agent_worktrees.locks"]
                reader_pids = live_reader_pids(peer["trace"], semantic_peer["requests"], locks)
                assert len(reader_pids) == 3, reader_pids
                readers = [pin_owned(reader) for reader in reader_pids]
                client.send(b"\x1b")
                wait_for(lambda: mux("display-message", "-p", "-t", session, "#{pane_id}") == cli)
                wait_for(lambda: client.exited(observer_handle)
                         and all(client.exited(handle) for handle in readers)
                         and owner_commands_settled(peer["trace"], locks, client, pinned)
                         and semantic_peer["session"].subscriber_count == 0,
                         describe=lambda: {
                             "phase": "reader-and-observer-exit",
                             "observer_exited": client.exited(observer_handle),
                             "readers_exited": {
                                 reader: client.exited(pinned[reader]) for reader in sorted(reader_pids)
                             },
                             "owner_commands_settled": owner_commands_settled(
                                 peer["trace"], locks, client, pinned,
                             ),
                             "pid_existence_only_settled": peer["settled"](),
                             "pid_present_owner_commands": [
                                 child for child in map(json.loads, peer["trace"].read_text().splitlines())
                                 if locks.pid_alive(child["pid"])
                                 and locks.process_start_time(child["pid"]) == child["birth"]
                             ],
                             "bridge_subscribers": semantic_peer["session"].subscriber_count,
                             "bridge_held_connections": semantic_peer["counts"]["held"],
                         })
                client.send(b"joined-return\r")
                wait_for(lambda: "CLI RECEIVED joined-return" in mux("capture-pane", "-p", "-t", cli))
                assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
                assert client.birth(cli_handle) == birth and not client.exited(cli_handle)
                assert mux("display-message", "-p", "-t", cli, "#{session_id}:#{session_created}") == incarnation
                assert mux("display-message", "-p", "-t", cli, "#{window_id}") == window
                record_property("native-windows-joined-boundary", json.dumps({
                    "input": "actual PSMux/ConPTY client", "mux": mux("-V"),
                    "owners": "private Worktrees resident, managed Agenda, Bridge semantic route/EventLog",
                    "substitutions": "root selection/discovery, Bridge loopback carrier, inert CLI",
                    "app": "unchanged source application and readers", "client_bytes": len(client.output),
                    "display": "server capture, not headed pixels or frame/input-ready latency",
                    "installed": False, "sdk_actor": False,
                }))
            finally:
                try:
                    mux("kill-session", "-t", session, check=False)
                finally:
                    client.job.close()
                    client.job = None
                    assert all(client.exited(handle, 5000) for handle in pinned.values()), (
                        "Owned native process survived strict Job cleanup",
                    )
        assert client.handles == []
