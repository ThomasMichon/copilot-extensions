"""First credential-free guest configuration; no service or backend launch."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import stat
from pathlib import Path

from guest_operation import GuestOperation, LOCK, STATE
from process import run

SOURCE = Path("/opt/lab/source")
WORKSPACE = Path("/lab/workspace")
EVIDENCE = Path("/lab/evidence")
HOME = STATE / "home"
PROJECT = "reproducer-lab"


def environment() -> dict[str, str]:
    """Return a fresh minimal environment, never an ambient-env overlay."""
    registry = HOME / ".agent-worktrees"
    return {
        "HOME": str(HOME), "USERPROFILE": str(HOME), "AGENT_HOME": str(HOME),
        "COPILOT_HOME": str(HOME / ".copilot"),
        "XDG_CONFIG_HOME": str(HOME / ".config"),
        "XDG_DATA_HOME": str(HOME / ".local/share"),
        "XDG_STATE_HOME": str(HOME / ".local/state"),
        "PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "AGENT_WORKTREES_PAYLOAD_ROOT": str(SOURCE / "plugins/agent-worktrees"),
        "AGENT_WORKTREES_PROJECTS_YAML": str(registry / "projects.yaml"),
        "AGENT_WORKTREES_REPOS_YAML": str(registry / "repos.yaml"),
        "AGENT_WORKTREES_STATUS_MONITOR": "0",
        "AGENT_BRIDGE_INSTALL_DIR": str(HOME / ".agent-bridge"),
        "AGENT_BRIDGE_CONFIG_DIR": str(HOME / ".agent-bridge"),
        "AGENT_BRIDGE_PROVIDERS_DIR": str(HOME / ".agent-bridge/providers.d"),
        "AGENT_BRIDGE_SELF_RETIRE": "0",
        "AGENT_DISPATCH_INSTALL_DIR": str(HOME / ".agent-dispatch"),
        "AGENT_DISPATCH_DB": str(STATE / "dispatch/queue.db"),
        "AGENT_DISPATCH_HOST": "127.0.0.1", "AGENT_DISPATCH_PORT": "0",
        "AGENT_DISPATCH_NO_AUTOSTART": "1",
        "AGENT_DISPATCH_GC_INTERVAL": "0",
        "AGENT_DISPATCH_HANDOFF_FALLBACK": "0",
    }


def documents() -> dict[Path, dict[str, object]]:
    """Supported owner inputs, not an installation or readiness receipt."""
    anchor = WORKSPACE / "anchor"
    registry = HOME / ".agent-worktrees"
    project = HOME / ("." + PROJECT)
    peer = HOME / "bin/mock-acp"
    repo: dict[str, object] = {
        "anchor": str(anchor), "worktree_root": str(WORKSPACE / "worktrees"),
        "default_branch": "main", "auto_fast_forward": False,
        "expose_agent": True,
        "launch": {"linux": [str(peer)]},
        "session_env": environment(),
    }
    return {
        registry / "config.yaml": {
            "machine": "reproducer", "platform": "linux",
            "srcroot": str(WORKSPACE), "auto_fast_forward": False,
        },
        registry / "projects.yaml": {
            "schema_version": 2,
            "projects": {PROJECT: {
                "config_dir": str(project), "anchor": str(anchor),
                "expose_agent": True,
            }},
        },
        registry / "repos.yaml": {"repos": {}},
        project / "config.yaml": {
            "machine": "reproducer", "platform": "linux", "repo_name": PROJECT,
            "srcroot": str(WORKSPACE), "auto_fast_forward": False,
            "repos": {PROJECT: repo},
        },
        HOME / ".copilot/config.json": {"trustedFolders": [str(anchor)]},
        HOME / ".agent-bridge/config.yaml": {
            "schema_version": 2, "bind": "127.0.0.1", "port": 0,
            "db_path": str(STATE / "bridge/bridge.db"),
            "enable_credential_relay": False, "idle_reap_ttl_seconds": 0,
            "worktree_discovery_interval": 0,
            "topologies": {"reproducer": {
                "agents_config": str(HOME / ".agent-bridge/acp-agents.json"),
            }},
        },
        HOME / ".agent-bridge/acp-agents.json": {PROJECT: {
            "project": PROJECT, "copilot_path": str(peer),
            "copilot_args": [],
            "env": {
                **environment(), "MOCK_ACP_TRANSCRIPT": str(EVIDENCE / "acp-peer.jsonl"),
            },
            "worktree_discovery": False, "spawnable_as_target": True,
        }},
        anchor / ".copilot-extensions/agent-worktrees/config.yaml": {
            "expose_agent": True, "auto_fast_forward": False,
        },
        anchor / ".github/copilot/settings.json": {
            "enabledPlugins": {}, "extraKnownMarketplaces": {},
        },
    }


def _private_directory(path: Path, *, device: int | None = None) -> None:
    info = path.lstat()
    if (
        path.resolve(strict=True) != path or not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (1000, 1000, 0o700)
        or device is not None and info.st_dev != device
    ):
        raise ValueError("fixture requires private, non-redirected guest directories")


def _initial_roots() -> None:
    _private_directory(STATE)
    _private_directory(STATE / "runtime", device=STATE.lstat().st_dev)
    _private_directory(WORKSPACE)
    _private_directory(EVIDENCE)
    expected = {LOCK.name, "runtime"}
    with os.scandir(STATE) as entries:
        for entry in entries:
            if entry.name not in expected:
                raise ValueError("retained guest state requires its original owner; no fixture preparation")
            expected.remove(entry.name)
    if expected:
        raise ValueError("initial fixture state is incomplete")
    with os.scandir(WORKSPACE) as entries:
        if next(entries, None) is not None:
            raise ValueError("retained workspace requires its original owner; no fixture preparation")


def _write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def _write_document(path: Path, data: dict[str, object]) -> None:
    _write_text(path, json.dumps(data, sort_keys=True) + "\n")


def prepare_fixture(custody: GuestOperation) -> None:
    """Prepare only the first synthetic harness and owner configuration.

    Requires already qualified resources/source/runtime, fresh host monitoring,
    no competing writers and explicit authorization. Retains partial attempts
    on failure. Does not initialize databases, start services, call a backend,
    create a task, install a plugin or qualify subsequent lifecycle behavior.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("fixture preparation must run outside an event loop")
    custody.assert_owned()
    _initial_roots()
    # An exclusively created HOME retains the attempt, not a completion marker.
    HOME.mkdir(mode=0o700)
    for path in (STATE / "bridge", STATE / "dispatch", WORKSPACE / "anchor"):
        path.mkdir(mode=0o700)
    for path in (HOME / ".agent-bridge", HOME / ".agent-dispatch"):
        path.mkdir(mode=0o700)
    (HOME / ".agent-bridge/providers.d").mkdir(mode=0o700)
    template = HOME / "git-template"
    template.mkdir(mode=0o700)
    # Bridge appends ACP flags immediately after the executable, before args.
    peer = SOURCE / "plugins/agent-bridge/tests/mock_acp_peer.py"
    _write_text(
        HOME / "bin/mock-acp",
        "#!/bin/sh\nexec " + shlex.join(["/usr/bin/python3", "-u", str(peer)]) + ' "$@"\n',
        mode=0o700,
    )
    for path, data in documents().items():
        custody.assert_owned()
        _write_document(path, data)
    anchor = WORKSPACE / "anchor"
    git = [
        "/usr/bin/git", "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgSign=false", "-c", "user.name=Synthetic Reproducer",
        "-c", "user.email=reproducer@example.invalid", "-C", str(anchor),
    ]
    for command in (
        ["init", "--initial-branch=main", "--template=" + str(template)],
        ["add", "--", ".copilot-extensions", ".github"],
        ["commit", "--message=Create synthetic offline harness"],
    ):
        custody.assert_owned()
        result = run([*git, *command], timeout=15, max_stdout_bytes=16 * 1024, env=environment())
        if result.returncode:
            raise RuntimeError(
                f"synthetic harness Git preparation failed ({result.returncode}): "
                f"{result.stderr}; retain the partial attempt"
            )
    custody.assert_owned()
