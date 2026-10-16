"""Opt-in real owner/HTTP/backend continuity, with no shared daemon changes."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid

import psutil
import pytest
import uvicorn
import yaml
from agent_procutil import no_window_kwargs

pytestmark = [
    pytest.mark.contract("agent_bridge.worktree_native_resume"),
    pytest.mark.skipif(
        os.environ.get("AGENT_BRIDGE_NATIVE_CONTINUITY") != "1",
        reason="opt-in real Copilot owner/HTTP continuity",
    ),
]

REPO = Path(__file__).resolve().parents[3]
OWNER_FIXTURE = REPO / "plugins" / "agent-worktrees" / "tests" / "workspace_owner_fixture.py"


def _run(argv, *, cwd=None, env=None, timeout=60):
    return subprocess.run(
        [str(part) for part in argv], cwd=cwd, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        **no_window_kwargs(),
    )


def _owner(python, root, *args):
    result = _run([python, OWNER_FIXTURE, root, "--project", "fixture", *args])
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _trusted_folders(path):
    from agent_worktrees.permissions import _read_config_jsonc

    try:
        _, data = _read_config_jsonc(path)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot parse fixture Copilot configuration ({type(exc).__name__})"
        ) from None
    folders = data.get("trustedFolders")
    assert isinstance(folders, list), "Fixture owner did not publish folder trust"
    return folders


def _native_processes(native_id, workspace):
    found = []
    for process in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            if (
                "copilot" in (process.info["name"] or "").lower()
                and any(native_id in arg for arg in (process.info["cmdline"] or []))
                and Path(process.cwd()).resolve() == workspace.resolve()
            ):
                found.append((process.pid, process.info["create_time"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def _terminate_fixture(pid, started_at):
    """Only an identity-pinned process created by this approved test run."""
    process = psutil.Process(pid)
    assert process.create_time() == started_at, "fixture PID was reused"
    descendants = [(child.pid, child.create_time()) for child in process.children(recursive=True)]
    if sys.platform == "win32":
        result = _run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"Stop-Process -Id {pid} -ErrorAction Stop",
        ], timeout=20)
        assert result.returncode == 0, result.stderr
    else:
        process.terminate()
    process.wait(timeout=20)
    for child_pid, child_started in descendants:
        try:
            child = psutil.Process(child_pid)
        except psutil.NoSuchProcess:
            continue
        assert child.create_time() == child_started, "fixture descendant PID was reused"
        if sys.platform == "win32":
            result = _run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"Stop-Process -Id {child_pid} -ErrorAction Stop",
            ], timeout=20)
            assert result.returncode == 0, result.stderr
        else:
            child.terminate()
        child.wait(timeout=20)


@pytest.mark.asyncio
@pytest.mark.timeout(300)
@pytest.mark.parametrize("mode", ["acp", "cli"])
async def test_native_worktree_resume_through_owner_http_and_bridge_control(
    monkeypatch, mode,
):
    assert os.environ.get("AGENT_BRIDGE_NATIVE_LIFECYCLE_APPROVED") == "1", (
        "Explicit approval for fixture-only process creation and teardown is required"
    )
    task_root = Path(os.environ["AGENT_BRIDGE_NATIVE_ROOT"]).resolve()
    root = task_root / f"{mode}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    root.mkdir()
    owner_python = Path(os.environ["AGENT_BRIDGE_NATIVE_OWNER_PYTHON"]).resolve()
    assert owner_python.is_file(), "Prepare the candidate Worktrees test environment first"
    copilot = shutil.which("copilot")
    assert copilot, "A native Copilot CLI with persisted OAuth is required"
    preferences = json.loads(
        Path(os.environ["AGENT_BRIDGE_NATIVE_PREFERENCES_FILE"]).read_text(encoding="utf-8")
    )
    assert preferences.get("model"), "Do not substitute a built-in model for operator preferences"
    monkeypatch.syspath_prepend(str(REPO / "plugins" / "agent-worktrees" / "src"))
    owner_root = task_root / "owner"
    anchor = owner_root / "anchor"
    authority = anchor / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    if not (anchor / ".git").exists():
        authority.parent.mkdir(parents=True)
        authority.write_text("default_branch: main\n", encoding="utf-8")
        for command in (
            ["init", "-q", "--initial-branch=main"],
            ["add", ".copilot-extensions/agent-worktrees/config.yaml"],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-qm", "fixture authority"],
            ["remote", "add", "origin", str(anchor)],
        ):
            result = _run(["git", "-C", anchor, *command], timeout=20)
            assert result.returncode == 0, result.stderr
    else:
        configured_remote = _run(["git", "-C", anchor, "remote", "get-url", "origin"])
        assert configured_remote.returncode == 0 and configured_remote.stdout.strip() == str(anchor)

    home = owner_root / "home"
    copilot_home = home / ".copilot"
    copilot_home.mkdir(parents=True, exist_ok=True)
    identity = preferences.get("copilot_identity", {})
    assert isinstance(identity, dict) and set(identity) <= {"loggedInUsers", "lastLoggedInUser"}
    assert isinstance(identity.get("loggedInUsers", []), list)
    selectors = list(identity.get("loggedInUsers", []))
    if identity.get("lastLoggedInUser"):
        selectors.append(identity["lastLoggedInUser"])
    assert all(
        isinstance(item, dict) and set(item) == {"host", "login"}
        and all(isinstance(value, str) for value in item.values())
        for item in selectors
    ), "Fixture identity may contain public account selectors only, never tokens"
    if not (copilot_home / "config.json").exists():
        (copilot_home / "config.json").write_text(json.dumps(identity), encoding="utf-8")
    selected = {
        key: preferences[key] for key in (
            "model", "effortLevel", "contextTier", "experimental", "memory", "powershellFlags",
        ) if key in preferences
    }
    (copilot_home / "settings.json").write_text(json.dumps(selected), encoding="utf-8")
    bridge_root = root / "bridge"
    bridge_root.mkdir()
    for key in tuple(os.environ):
        if key.startswith("AGENT_BRIDGE_") or key in {
            "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_ALLOW_ALL",
            "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_WORKTREES_PAYLOAD_ROOT",
            "SESSION_ID", "COPILOT_AGENT_SESSION_ID", "WORKTREE_ID", "WORKTREE_PROJECT",
            "APERTURE_WORKTREE_ID",
        }:
            monkeypatch.delenv(key)
    for key, value in {
        "USERPROFILE": home, "HOME": home, "COPILOT_HOME": copilot_home,
        "AGENT_HOME": home, "XDG_CONFIG_HOME": home / ".config",
        "XDG_DATA_HOME": home / ".local" / "share", "XDG_STATE_HOME": home / ".local" / "state",
        "APPDATA": home / "AppData" / "Roaming", "LOCALAPPDATA": home / "AppData" / "Local",
        "AGENT_BRIDGE_CONFIG_DIR": bridge_root,
    }.items():
        monkeypatch.setenv(key, str(value))

    plugin = root / "bridge-plugin"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "bridge-native-fixture", "version": "0.0.0"}), encoding="utf-8",
    )
    shutil.copytree(
        REPO / "plugins" / "agent-bridge" / "extensions", plugin / "extensions",
    )
    launch_args = [
        "--disable-builtin-mcps", "--no-custom-instructions", "--no-ask-user",
        "--no-auto-update", "--no-remote", "--no-remote-export",
        "--allow-all-tools", "--available-tools=powershell",
        "--deny-tool=shell", "--deny-tool=write", "--max-ai-credits", "100",
    ]
    monkeypatch.setenv("FIXTURE_COPILOT_PATH", copilot)
    monkeypatch.setenv("FIXTURE_COPILOT_ARGS", json.dumps([
        *launch_args, "--plugin-dir", str(plugin),
    ]))
    bins = root / "bin"
    bins.mkdir()
    prefix = [str(owner_python), str(OWNER_FIXTURE), str(owner_root)]
    if sys.platform == "win32":
        wrapper = bins / "agent-worktrees.cmd"
        wrapper.write_text(
            "@echo off\n" + subprocess.list2cmdline(prefix) + " %*\n", encoding="utf-8",
        )
    else:
        wrapper = bins / "agent-worktrees"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.join(prefix) + ' "$@"\n', encoding="utf-8")
        wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", str(bins) + os.pathsep + os.environ["PATH"])

    from agent_bridge import transport, worktree_owner_operation
    from agent_bridge.agent_registry import AgentConfig, AgentResolver
    from agent_bridge.app import create_app
    from agent_bridge.client import BridgeClient
    from agent_bridge.config import config_dir
    from agent_bridge.db import Database
    from agent_bridge.models import ServiceConfig
    from agent_bridge.routes import worktrees as wt
    from agent_bridge.session_manager import session_manager_from_config

    assert config_dir().resolve() == bridge_root, "Candidate configuration escaped the fixture"
    monkeypatch.setattr(transport, "_agent_worktrees_launch_prefix", lambda: prefix)
    monkeypatch.setattr(worktree_owner_operation, "_agent_worktrees_launch_prefix", lambda: prefix)
    monkeypatch.setattr(wt, "_resolve_local_binstub", lambda project: str(wrapper))
    created = await asyncio.to_thread(
        _owner, owner_python, owner_root, "create", "--creation-key", uuid.uuid4().hex,
        "--no-owner", "--interface", "cli", "--origin", "user", "--json",
    )
    worktree_id = created["worktree"]["id"]
    workspace = Path(created["worktree"]["path"])
    assert str(workspace) in _trusted_folders(copilot_home / "config.json"), (
        "Owner must pre-trust before launch"
    )
    native_id = str(uuid.uuid4())
    marker = "native-marker-" + uuid.uuid4().hex
    history_root = copilot_home / "session-state"
    previous_folders = {
        path.name for path in history_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    } if history_root.exists() else set()
    seed = await asyncio.to_thread(
        _run,
        [
            copilot, "--session-id", native_id, *launch_args,
            "--model", selected["model"],
            "--reasoning-effort", selected.get("effortLevel", "medium"),
            "--context", selected.get("contextTier", "default"),
            "--output-format", "json", "-p",
            f"Remember the exact test marker {marker}. Reply only ACK. Do not use tools.",
        ],
        cwd=workspace, timeout=90,
    )
    assert seed.returncode == 0, seed.stderr
    results = [json.loads(line) for line in seed.stdout.splitlines() if line.startswith("{")]
    assert any(row.get("type") == "result" and row.get("sessionId") == native_id for row in results)
    assert not _native_processes(native_id, workspace), "Predecessor must exit before cold resume"
    await asyncio.to_thread(
        _owner, owner_python, owner_root, "bind-session",
        "--worktree-id", worktree_id, "--session-id", native_id,
    )
    cfg = ServiceConfig(
        db_path=str(bridge_root / "sessions.db"), enable_credential_relay=False,
        worktree_discovery_interval=0, idle_reap_ttl_seconds=0,
    )
    app = create_app(config=cfg, token=secrets.token_urlsafe(24))
    db = Database(bridge_root / "sessions.db")
    manager = session_manager_from_config(db, cfg)
    resolver = AgentResolver({"fixture": AgentConfig(name="fixture", project="fixture")}, {})
    manager.set_resolver(resolver)
    app.state.db, app.state.session_manager, app.state.resolver = db, manager, resolver
    app.state.ready = app.state.topology_ready = True
    app.state.readiness_error = None
    cache = wt.WorktreeDiscoveryCache()
    cache.start(resolver)
    monkeypatch.setattr(wt, "_discovery_cache", cache)
    assert not manager.list_sessions(), "The importing frontend must have no retained session"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    (bridge_root / "active.json").write_text(
        json.dumps({"active": {"port": port}}), encoding="utf-8",
    )
    (bridge_root / "auth.yaml").write_text(
        yaml.safe_dump({"token": app.state.auth_token}), encoding="utf-8",
    )
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    client = BridgeClient(f"http://127.0.0.1:{port}", app.state.auth_token)
    imported = None
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert server.started
        imported = await asyncio.to_thread(
            client.resume_worktree, worktree_id, mode=mode, request_timeout=120,
        )
        assert imported["durable_session_id"] == imported["acp_session_id"] == native_id
        assert Path(imported["target_dir"]).resolve() == workspace.resolve()
        suffix = "-bridge-restored-" + uuid.uuid4().hex[:8]
        expected = marker + suffix
        prompt = f"Recall my exact test marker and append '{suffix}'. Reply only that string; no tools."
        if mode == "acp":
            await asyncio.to_thread(client.submit_prompt, imported["session_id"], prompt)
        else:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                represented = await asyncio.to_thread(client.resolve_live_session, worktree_id)
                if represented.get("session_id") == native_id:
                    break
                await asyncio.sleep(0.25)
            assert represented.get("session_id") == native_id, "Real CLI extension must register"
            await asyncio.to_thread(
                client.send_live_message, native_id, sender="native-fixture", body=prompt,
                expected_session_id=native_id, wait=False,
            )
        deadline = time.monotonic() + 90
        snapshot = {}
        settled = False
        while time.monotonic() < deadline:
            snapshot = await asyncio.to_thread(
                client.get_result_snapshot if mode == "acp" else client.get_live_result_snapshot,
                imported["session_id"] if mode == "acp" else native_id,
            )
            if expected in json.dumps(snapshot):
                state = await asyncio.to_thread(
                    client.get_session if mode == "acp" else client.get_live_session,
                    imported["session_id"] if mode == "acp" else native_id,
                )
                if state.get("status" if mode == "acp" else "turn_state") == "idle":
                    settled = True
                    break
            await asyncio.sleep(0.25)
        assert expected in json.dumps(snapshot), snapshot
        assert settled, "The restored turn must reach its actual idle boundary"
        if mode == "cli":
            assert db.get_cli_mode_reservation(worktree_id) is None
        record = await asyncio.to_thread(
            _owner, owner_python, owner_root, "record", "--worktree-id", worktree_id, "--json",
        )
        assert record["worktree"]["last_session_id"] == native_id
        folders = [path.name for path in (copilot_home / "session-state").iterdir()
                   if path.is_dir() and not path.name.startswith(".")]
        assert set(folders) == previous_folders | {native_id}, (
            "Cold resume must not create replacement history"
        )
        (root / "evidence.json").write_text(json.dumps({
            "mode": mode, "native_session_id": native_id, "worktree_id": worktree_id,
            "bridge_session_id": imported["session_id"], "remembered_context": True,
            "owner_http": True, "bridge_control": True,
        }, indent=2), encoding="utf-8")
    finally:
        try:
            if mode == "cli":
                mux = shutil.which("psmux" if sys.platform == "win32" else "tmux")
                if mux:
                    pane = await asyncio.to_thread(
                        _run, [
                            mux, "capture-pane", "-t", f"wt-{worktree_id}",
                            "-p", "-S", "-100",
                        ],
                    )
                    (root / "pane.txt").write_text(
                        pane.stdout + pane.stderr, encoding="utf-8",
                    )
                observed = await asyncio.to_thread(
                    _owner, owner_python, owner_root, "list-sessions",
                    "--worktree", worktree_id, "--json",
                )
                (root / "owner-sessions.json").write_text(
                    json.dumps(observed, indent=2), encoding="utf-8",
                )
                diagnostics = {
                    "native_id": native_id,
                    "locks": [
                        path.name for path in (history_root / native_id).glob("inuse.*.lock")
                    ],
                    "native_processes": [],
                    "registrations": [
                        {key: row.get(key) for key in (
                            "session_id", "worktree_id", "machine", "repo", "cwd",
                            "pid", "process_started_at", "turn_state",
                        )}
                        for row in db.list_live_sessions()
                        if row["session_id"] == native_id
                    ],
                }
                for pid, started_at in _native_processes(native_id, workspace):
                    process = psutil.Process(pid)
                    diagnostics["native_processes"].append({
                        "pid": pid, "started_at": started_at,
                        "name": process.name(), "exe": process.exe(),
                    })
                (root / "liveness.json").write_text(
                    json.dumps(diagnostics, indent=2), encoding="utf-8",
                )
            if mode == "acp":
                for session in manager.list_sessions():
                    if session.target.worktree_id == worktree_id and session.acp_session_id == native_id:
                        await asyncio.to_thread(
                            client.stop_session, session.session_id, reap_host=True,
                        )
        finally:
            try:
                # Represented CLI messaging does not claim manager-owned stop semantics.
                for pid, started_at in _native_processes(native_id, workspace):
                    await asyncio.to_thread(_terminate_fixture, pid, started_at)
            finally:
                server.should_exit = True
                await asyncio.wait_for(serving, 15)
                await cache.stop()
                db.close()
    assert not _native_processes(native_id, workspace)
