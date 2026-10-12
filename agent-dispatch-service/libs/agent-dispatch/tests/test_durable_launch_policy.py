"""Durable launch callers request escape; the canonical primitive owns suppression."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import agent_procutil
import pytest

from agent_dispatch import __main__ as main, execution_cli, hibernation, procutil, supervise_cli

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("site", ["coordinator", "supervisor", "waiter"])
@pytest.mark.parametrize("contained", [False, True])
def test_durable_callers_request_escape_and_preserve_test_containment(
    tmp_path, monkeypatch, site, contained,
):
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: True)
    monkeypatch.setattr(agent_procutil, "contained_test_mode", lambda: contained)
    canonical = agent_procutil.windowless_daemon_kwargs
    requests = []

    def kwargs(*, breakaway=False):
        requests.append(breakaway)
        return canonical(breakaway=breakaway)

    monkeypatch.setattr(procutil, "windowless_daemon_kwargs", kwargs)
    monkeypatch.setattr(procutil, "resolve_own_runtime_python", lambda: sys.executable)
    monkeypatch.setattr(procutil, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr("agent_dispatch.install_paths.install_dir", lambda: tmp_path)
    monkeypatch.setattr(
        hibernation, "detached_run_argv",
        lambda spec, python: [python, "-c", "pass"],
    )
    calls = []

    def spawn(argv, **options):
        calls.append((argv, options))
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    if site == "coordinator":
        main._spawn_coordinator_process()
    elif site == "supervisor":
        assert supervise_cli._spawn_supervisor_daemon_detached("fixture", "fixture")
    else:
        monkeypatch.setattr(execution_cli, "_DETACHED_CLI_ARGS", None)
        execution_cli._spawn_detached_waiter(object())

    assert requests == [True]
    assert len(calls) == 1
    flags = calls[0][1]["creationflags"]
    assert flags & 0x08000000
    assert bool(flags & 0x01000000) is not contained
