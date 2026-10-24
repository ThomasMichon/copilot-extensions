"""Legacy unscoped guest tuples must route task embodiment in-process."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from machine_transport import MachineEntry, SSHEnvironment
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, tasks
from worktree_manager.production_picker.picker_tui import engine_worktree_actions as actions
from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
    PickerScreenMaintenanceActionsMixin,
)


@pytest.mark.parametrize("target", [
    "example-host", "EXAMPLE-HOST", "example-host-wsl", "EXAMPLE-HOST-WSL",
    "Generated-Host", "guest-alias",
])
def test_unscoped_guest_tuple_builds_local_source_and_local_task_command(monkeypatch, target):
    entries = {
        "example-host": MachineEntry(
            key="example-host", display_name="Native", hostname="Generated-Host",
            ssh_environments=[SSHEnvironment("windows", "native-profile")], ssh_ready=True,
        ),
        "example-host-wsl": MachineEntry(
            key="example-host-wsl", display_name="Guest", hostname="Generated-Host",
            alias="guest-alias", ssh_environments=[SSHEnvironment("wsl", "guest-profile")],
            ssh_ready=True,
        ),
    }
    assert not any(entry.execution_platform for entry in entries.values())
    config = SimpleNamespace(
        machine="example-host-wsl", default_repo=SimpleNamespace(anchor="/fixture"),
    )
    monkeypatch.setattr(data_local.cfg, "load_config", lambda: config)
    monkeypatch.setattr(data_local.cfg, "load_machines_yaml", lambda anchor: entries)
    monkeypatch.setattr(data_local, "local_execution_platform", lambda: "wsl")
    monkeypatch.setattr(data_local, "LOCAL", ("Generated-Host", "WSL"))
    monkeypatch.setattr(data_ssh, "_local_identity", lambda: ("Generated-Host", "wsl"))
    monkeypatch.setattr(data_ssh, "_project", lambda: "example")
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda project: [])
    sources = data_ssh._build_sources()
    local, = [source for source in sources if source.local]
    assert local.key == ("example-host-wsl", "WSL") and local.argv is None
    native, = [source for source in sources if source.env == "Win"]
    assert not native.local and native.argv[0] == "ssh" and native.alias == "native-profile"
    assert native.source_id != local.source_id
    assert data_local.is_local(target, "WSL")
    assert not data_local.is_local(target, "Win")
    launched: list[list[str]] = []

    def no_remote(*args, **kwargs):
        pytest.fail("a known local guest tuple attempted remote source lookup")

    monkeypatch.setattr(data_ssh, "_find_source", no_remote)
    monkeypatch.setattr(tasks, "_resolve_argv", lambda argv, ctx: ["fixture-dispatch", *argv[1:]])
    monkeypatch.setattr(tasks, "_child_process_env", lambda: {})

    class Process:
        returncode = 0

        def communicate(self, timeout=None):
            return json.dumps({"worktree": "created-worktree", "project": "example"}), ""

    def popen(argv, **kwargs):
        launched.append(argv)
        return Process()

    monkeypatch.setattr(actions.subprocess, "Popen", popen)

    class Screen(actions.PickerScreenWorktreeActionsMixin, PickerScreenMaintenanceActionsMixin):
        src = data_local
        app = SimpleNamespace(result=None)
        source_tabs = data_ssh.source_tabs(sources)
        debug = ""

        def _current_tab(self):
            return next(tab for tab in self.source_tabs if tab["source_id"] == local.source_id)

        def _find_internal_worktree(self, wid, source_id):
            return None, "not loaded yet"

        def _decide(self, decision):
            self.app.result = decision

        def _parse_action_json(self, value):
            return json.loads(value)

        def _run_bg(self, label, work, done):
            done(work())

    screen = Screen()
    ok, _ = screen._embody_task_cli({
        "task_id": "task-one", "machine": target, "env": "WSL",
        "source_kind": "machine-ssh", "source_id": local.source_id,
    })
    assert ok and len(launched) == 1, screen.debug
    assert launched[0] == [
        "fixture-dispatch", "embody", "task-one", "--interactive", "--machine", target,
    ]
    assert screen.app.result["is_local"] is True
    assert screen.app.result["machine"] == target
    assert screen.app.result["env"] == "WSL"


def test_explicit_native_key_does_not_become_local_through_guest_context(monkeypatch):
    entries = {
        "example-host": MachineEntry(
            key="example-host", display_name="Native", execution_platform="windows",
            physical_host="example-host",
        ),
        "guest-cell": MachineEntry(
            key="guest-cell", display_name="Guest", execution_platform="wsl",
            physical_host="example-host",
        ),
    }
    config = SimpleNamespace(machine="guest-cell", default_repo=SimpleNamespace(anchor="/fixture"))
    monkeypatch.setattr(data_local.cfg, "load_config", lambda: config)
    monkeypatch.setattr(data_local.cfg, "load_machines_yaml", lambda anchor: entries)
    monkeypatch.setattr(data_local, "local_execution_platform", lambda: "wsl")
    monkeypatch.setattr(data_local, "LOCAL", ("Generated-Host", "WSL"))
    assert data_local.is_local("GUEST-CELL", "WSL")
    assert not data_local.is_local("example-host", "WSL")
