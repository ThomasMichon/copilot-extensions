"""Real instance admission preserves lexical runtime selection before provider work."""

import asyncio
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import agent_procutil
from agent_procutil import daemon_instances as instances
from agent_codespaces import _invoke, config, connection_owner, owner_startup


class StopBeforeProvider(RuntimeError):
    pass


def redirected(alias, target):
    if os.name == "nt":
        result = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("selection", ["absolute-parent", "relative-parent", "relative"])
def test_owner_reserves_real_selected_private_root_and_pins_child_environment(
    tmp_path, monkeypatch, selection,
):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.chdir(launch)
    raw = str(launch / ".." / "state") if selection == "absolute-parent" else (
        "../state" if selection == "relative-parent" else "state"
    )
    if selection == "relative":
        monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_CODESPACES_HOME", raw)
    assert config._runtime_dir() == root
    assert _invoke._runtime_root() == root
    observed = []

    def reserve(argv, **kwargs):
        receipt = instances.reserve_instance(kwargs["instance"])
        try:
            row = next(row for row in instances.inspect_instances(receipt.registry_root)
                       if row["instance_id"] == receipt.instance_id)
            assert row["runtime_root"] == str(root)
            assert kwargs["env_overrides"]["AGENT_CODESPACES_HOME"] == str(root)
            assert kwargs["log_path"].is_relative_to(root)
            observed.append(row["state"])
        finally:
            instances.cancel_instance_reservation(receipt, creation_refused=True)
        raise StopBeforeProvider

    monkeypatch.setattr(agent_procutil, "spawn_daemon", reserve)
    with pytest.raises(StopBeforeProvider):
        owner_startup.spawn_owner_process(Path(raw), source=owner_startup.__file__)
    assert observed == ["reserved"]


def test_owner_launch_does_not_hide_linked_runtime_root(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    redirected(alias, root)

    def reserve(argv, **kwargs):
        instances.reserve_instance(kwargs["instance"])
        pytest.fail("linked runtime root passed real reservation")

    monkeypatch.setattr(agent_procutil, "spawn_daemon", reserve)
    with pytest.raises((OSError, ValueError)):
        owner_startup.spawn_owner_process(alias, source=owner_startup.__file__)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("linked", [False, True])
def test_direct_owner_admission_precedes_singleton_or_provider_work(tmp_path, monkeypatch, linked):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    launch = tmp_path / "launch"
    launch.mkdir()
    selected = launch / ".." / "state"
    if linked:
        selected = tmp_path / "alias"
        redirected(selected, root)
    monkeypatch.setattr(connection_owner, "RUNTIME_DIR", selected)
    acquired = []
    real = instances.admit_daemon_instance

    def admit(*args, **kwargs):
        guard = real(*args, **kwargs)
        acquired.append(guard)
        return guard

    def claim(*args):
        row = next(row for row in instances.inspect_instances(acquired[0].receipt.registry_root)
                   if row["instance_id"] == acquired[0].receipt.instance_id)
        assert row["runtime_root"] == str(root) and row["state"] == "running"
        raise StopBeforeProvider

    monkeypatch.setattr(instances, "admit_daemon_instance", admit)
    monkeypatch.setattr(connection_owner, "claim_owner_singleton", claim)
    with pytest.raises((OSError, ValueError) if linked else StopBeforeProvider):
        asyncio.run(connection_owner.run_owner_daemon(SimpleNamespace()))
    assert len(acquired) == (0 if linked else 1)
    assert list(root.iterdir()) == []
