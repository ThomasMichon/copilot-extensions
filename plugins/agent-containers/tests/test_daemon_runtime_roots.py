"""Real reservation/admission stops before detached process or Docker operations."""

import argparse
import os
import subprocess

import pytest
from agent_procutil import daemon_instances as instances
from ssh_manager.forward_keeper import KeeperStore

from agent_containers import _invoke, config, docker_proxy, forward_keeper


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


@pytest.mark.parametrize("kind", ["keeper", "proxy"])
@pytest.mark.parametrize("linked", [False, True])
def test_launch_uses_real_reservation_and_retains_link_refusal(tmp_path, monkeypatch, kind, linked):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    launch = tmp_path / "launch"
    launch.mkdir()
    selected = launch / ".." / "state"
    if linked:
        selected = tmp_path / "alias"
        redirected(selected, root)
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(selected))
    assert config._runtime_home() == (selected if linked else root)
    assert _invoke.runtime_root() == (selected if linked else root)
    observed = []
    expected_root = root / "forward-keepers" if kind == "keeper" else root

    def reserve(argv, **kwargs):
        receipt = instances.reserve_instance(kwargs["instance"], environment=kwargs.get("env"))
        try:
            row = next(row for row in instances.inspect_instances(receipt.registry_root)
                       if row["instance_id"] == receipt.instance_id)
            assert row["runtime_root"] == str(expected_root)
            if kind == "proxy":
                assert argv[-1] == str(root / "endpoint.json")
                assert kwargs["log_path"] == root / "docker-proxy.log"
            else:
                assert kwargs["env"]["AGENT_CONTAINERS_HOME"] == str(root)
            observed.append(row["state"])
        finally:
            instances.cancel_instance_reservation(receipt, creation_refused=True)
        raise StopBeforeProvider

    monkeypatch.setattr(docker_proxy, "spawn_daemon", reserve)
    monkeypatch.setattr(forward_keeper, "spawn_instance", reserve)
    monkeypatch.setattr(forward_keeper, "_STORE", KeeperStore(selected / "forward-keepers"))
    with pytest.raises((OSError, ValueError) if linked else StopBeforeProvider):
        if kind == "proxy":
            docker_proxy.ensure_broker("synthetic", "id", selected / "endpoint.json", timeout=0)
        else:
            forward_keeper.ensure_running(
                "synthetic", venue_port=42, mux="synthetic", mux_alive=lambda _: True,
            )
    assert observed == ([] if linked else ["reserved"])


@pytest.mark.parametrize("kind", ["keeper", "proxy"])
@pytest.mark.parametrize("linked", [False, True])
def test_direct_entry_real_admission_precedes_provider_or_listener(tmp_path, monkeypatch, kind, linked):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    launch = tmp_path / "launch"
    launch.mkdir()
    selected = launch / ".." / "state"
    if linked:
        selected = tmp_path / "alias"
        redirected(selected, root)
    monkeypatch.setattr(forward_keeper, "_STORE", KeeperStore(selected / "forward-keepers"))
    real = instances.admit_daemon_instance
    acquired = []
    expected_root = root / "forward-keepers" if kind == "keeper" else root

    def admit(*args, **kwargs):
        guard = real(*args, **kwargs)
        acquired.append(guard)
        return guard

    def work(*args, **kwargs):
        row = next(row for row in instances.inspect_instances(acquired[0].receipt.registry_root)
                   if row["instance_id"] == acquired[0].receipt.instance_id)
        assert row["runtime_root"] == str(expected_root) and row["state"] == "running"
        raise StopBeforeProvider

    async def run(*args):
        work()

    monkeypatch.setattr(instances, "admit_daemon_instance", admit)
    monkeypatch.setattr(forward_keeper, "_run", run)
    if kind == "proxy":
        monkeypatch.setattr(docker_proxy.socket, "socket", work)
    with pytest.raises((OSError, ValueError) if linked else StopBeforeProvider):
        if kind == "proxy":
            docker_proxy.serve("synthetic", "id", selected / "endpoint.json")
        else:
            forward_keeper.cmd_forward_keeper(argparse.Namespace())
    assert len(acquired) == (0 if linked else 1)
    assert list(root.iterdir()) == ([root / "forward-keepers"] if kind == "keeper" and not linked else [])


@pytest.mark.parametrize("environment", ["relative", "missing", "foreign"])
def test_keeper_child_home_stays_with_selected_store_after_cwd_changes(
    tmp_path, monkeypatch, environment,
):
    home = tmp_path / "selected"
    home.mkdir()
    launch = tmp_path / "launch"
    launch.mkdir()
    later = tmp_path / "later" / "cwd"
    later.mkdir(parents=True)
    monkeypatch.chdir(launch)
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", "../selected")
    store = KeeperStore(home / "forward-keepers")
    monkeypatch.setattr(forward_keeper, "_STORE", store)
    monkeypatch.chdir(later)
    if environment == "missing":
        monkeypatch.delenv("AGENT_CONTAINERS_HOME")
    elif environment == "foreign":
        monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(tmp_path / "other-home"))
    observed = []

    def reserve(argv, **kwargs):
        receipt = instances.reserve_instance(kwargs["instance"], environment=kwargs["env"])
        try:
            assert kwargs["env"]["AGENT_CONTAINERS_HOME"] == str(home)
            assert kwargs["instance"].runtime_root == store.state_dir
            with monkeypatch.context() as child:
                child.setenv("AGENT_CONTAINERS_HOME", kwargs["env"]["AGENT_CONTAINERS_HOME"])
                assert config._runtime_home() / "forward-keepers" == store.state_dir
            observed.append(True)
        finally:
            instances.cancel_instance_reservation(receipt, creation_refused=True)
        raise StopBeforeProvider

    monkeypatch.setattr(forward_keeper, "spawn_instance", reserve)
    with pytest.raises(StopBeforeProvider):
        forward_keeper.ensure_running(
            "synthetic", venue_port=42, mux="synthetic", mux_alive=lambda _: True,
        )
    assert observed == [True]
