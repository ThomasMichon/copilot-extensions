"""Fixture observation failures cannot manufacture Companion application failures."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from agent_procutil import no_window_kwargs
from test_companion_bridge_codec import OWNER
from test_companion_bridge_session_source import semantic_peer as semantic_peer
from test_companion_joined_tmux import source_entrypoint
from test_companion_joined_psmux import live_reader_pids
from worktree_manager import agenda_transport, companion_bridge_source, mux_companion


@pytest.mark.parametrize(
    "platform,code,retry",
    [("nt", 32, True), ("nt", 33, True), ("nt", 5, False), ("posix", 32, False)],
)
def test_observer_retries_only_native_sharing_without_advancing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, code: int, retry: bool,
) -> None:
    monkeypatch.setattr(mux_companion, "_load_current_worktree", lambda *_args, **_kw: SimpleNamespace(
        worktree={}, sessions=[], bundle={},
    ))
    monkeypatch.setattr(mux_companion.ec, "engine_base_command", lambda: ["fixture"])
    monkeypatch.setattr(companion_bridge_source, "bridge_command", lambda _root: ["fixture"])
    source_entrypoint({"checkout": tmp_path / "checkout"}, monkeypatch)
    parsed = ast.parse((tmp_path / "native-app.py").read_text(encoding="utf-8"))
    mount = next(node for node in parsed.body if isinstance(node, ast.FunctionDef)
                 and node.name == "observed_mount")
    callbacks = []
    timeline = SimpleNamespace(
        size=(80, 20), window=SimpleNamespace(follow_live=True, events=[], sources={}),
    )
    app = SimpleNamespace(
        size=(80, 24), focused=None, query_one=lambda _kind: timeline,
        set_interval=lambda _seconds, callback: callbacks.append(callback),
    )
    namespace = {
        "Path": Path, "json": json, "os": SimpleNamespace(name=platform),
        "mount": lambda _app: None, "SubwayTimeline": object,
    }
    exec(compile(ast.Module(body=[mount], type_ignores=[]), "<fixture observer>", "exec"), namespace)
    namespace["observed_mount"](app)
    real_replace, calls = Path.replace, []

    def replace(path: Path, target: Path) -> Path:
        calls.append(path)
        if len(calls) == 1:
            error = PermissionError("synthetic native sharing refusal")
            error.winerror = code
            raise error
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    target = tmp_path / "native-state.json"
    if not retry:
        with pytest.raises(PermissionError, match="native sharing refusal"):
            callbacks[0]()
        assert not target.exists()
        return
    callbacks[0]()
    assert not target.exists()
    callbacks[0]()
    assert json.loads(target.read_text(encoding="utf-8"))["app_cells"] == [80, 24]
    callbacks[0]()
    assert len(calls) == 2


def test_native_reader_selection_includes_actual_agenda_command_and_rejects_reuse(
    tmp_path: Path,
) -> None:
    agenda = agenda_transport.feed_argv(
        "example", "selected", "head-a", engine_command=[sys.executable, "-m", "agent_worktrees"],
    )
    trace = tmp_path / "children.jsonl"
    trace.write_text("\n".join(map(json.dumps, [
        {"pid": 1, "birth": 101, "argv": ["worktree-observations", "--follow"]},
        {"pid": 2, "birth": 102, "argv": agenda},
        {"pid": 4, "birth": 104, "argv": ["worktree-observation-history"]},
        {"pid": 5, "birth": 1, "argv": agenda},
        {"pid": 6, "birth": 106, "argv": agenda},
    ])), encoding="utf-8")
    requests = [
        {"pid": 3, "birth": 103, "path": "/session-observations/child?follow=true"},
        {"pid": 7, "birth": 107, "path": "/session-observation-history/child"},
    ]
    locks = SimpleNamespace(
        pid_alive=lambda pid: pid != 6, process_start_time=lambda pid: 100 + pid,
    )
    assert live_reader_pids(trace, requests, locks) == {1, 2, 3}


def test_private_bridge_membership_cli_completes_before_client_composition(
    semantic_peer: dict, tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            *companion_bridge_source.bridge_command(str(tmp_path)),
            "observe-owner", "--owner-ref", OWNER, "--contract-version", "1", "--json",
        ],
        env=dict(os.environ, AGENT_BRIDGE_NO_SELFPROVISION="1"),
        capture_output=True, encoding="utf-8", timeout=15, **no_window_kwargs(),
    )
    diagnostic = semantic_peer["diagnostic"]
    assert result.returncode == 0, (
        result.stdout, result.stderr,
        diagnostic.read_text(encoding="utf-8") if diagnostic.exists() else None,
    )
    assert json.loads(result.stdout)["kind"] == "snapshot"
    stream = companion_bridge_source.MembershipCommands(
        OWNER, cwd=str(tmp_path), project_root=str(tmp_path),
    ).frames(Event(), follow=True)
    try:
        assert next(stream)["kind"] == "snapshot"
        assert next(stream)["kind"] == "heartbeat"
    finally:
        stream.close()
