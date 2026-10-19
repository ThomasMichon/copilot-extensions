"""Every resident agent-dispatch process launches through ``spawn_daemon``.

Each site keeps its console policy (the coordinator and supervisor shell out
to console programs, so they keep one hidden console those children inherit;
the self-deploy does not) and gains an append-only stdio log under the
runtime root, so a startup crash leaves a traceback instead of nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import agent_procutil
import pytest

from agent_dispatch import (
    __main__ as main,
    coordinator_loops,
    procutil,
    supervise_cli,
    supervisor_registration,
)


def _home() -> str:
    return os.path.expanduser("~")


@pytest.mark.parametrize(
    ("site", "log_name", "console_children", "cwd"),
    [
        ("coordinator", "serve-service.log", True, None),
        ("supervisor", "logs/supervisor-daemon.log", True, None),
        ("supervisor-successor", "logs/supervisor-successor.log", True, _home),
        ("self-deploy", "logs/self-deploy.log", False, _home),
    ],
)
def test_resident_launch_sites_use_spawn_daemon(
    tmp_path, monkeypatch, site, log_name, console_children, cwd
):
    monkeypatch.setattr(procutil, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr("agent_dispatch.install_paths.install_dir", lambda: tmp_path)
    monkeypatch.setattr(procutil, "resolve_own_runtime_python", lambda: sys.executable)
    calls = []

    def spawn(argv, **options):
        calls.append((list(argv), options))
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn)
    if site == "coordinator":
        main._spawn_coordinator_process()
    elif site == "supervisor":
        assert supervise_cli._spawn_supervisor_daemon_detached("m", "default")
    elif site == "supervisor-successor":
        supervisor_registration._spawn_self_update_successor(
            sys.executable, ["supervise", "serve", "--machine", "m"]
        )
    else:
        coordinator_loops._spawn_self_deploy(sys.executable)

    assert len(calls) == 1
    _argv, options = calls[0]
    assert options["log_path"] == tmp_path / log_name
    assert options["console_children"] is console_children
    assert Path(options["cwd"]) == (Path(cwd()) if cwd else tmp_path)
    assert options["display"].startswith("agent_dispatch ")


def test_spawn_dispatch_daemon_writes_its_log_under_the_runtime_root(tmp_path, monkeypatch):
    monkeypatch.setattr(procutil, "runtime_root", lambda: tmp_path / "root")
    seen = {}
    monkeypatch.setattr(agent_procutil, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    procutil.spawn_dispatch_daemon(
        [sys.executable, "-m", "agent_dispatch", "serve", "--token", "secret"],
        log_name="serve-service.log",
    )
    assert seen["cwd"] == tmp_path / "root"
    assert (tmp_path / "root").is_dir()
    # The label names the subcommand only, never later flags or values.
    assert seen["display"] == "agent_dispatch serve"
