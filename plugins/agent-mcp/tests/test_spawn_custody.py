"""The launcher must reserve the socket-state root inherited by the daemon."""

import os
from types import SimpleNamespace

import pytest
import agent_procutil
from agent_procutil import daemon_instances as instances

from agent_mcp import cutover, forward


class StopBeforeSpawn(RuntimeError):
    pass


@pytest.mark.parametrize("kind", ["forward", "cutover"])
@pytest.mark.parametrize("linked", [False, True])
def test_first_reservation_checks_selected_socket_parent(tmp_path, monkeypatch, kind, linked):
    home = tmp_path / "home"
    home.mkdir()
    socket_root = tmp_path / "socket-state"
    socket_root.mkdir()
    selected = socket_root
    if linked:
        selected = tmp_path / "linked-socket-state"
        try:
            selected.symlink_to(socket_root, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"native directory symlink unavailable: {error}")
    monkeypatch.setenv("AGENT_MCP_HOME", str(home))
    observed = []

    def reserve(command, **kwargs):
        receipt = instances.reserve_instance(
            kwargs["instance"], environment={**os.environ, **kwargs["env_overrides"]},
        )
        try:
            row = next(row for row in instances.inspect_instances(receipt.registry_root)
                       if row["instance_id"] == receipt.instance_id)
            assert row["runtime_root"] == str(socket_root)
            assert command[command.index("--socket") + 1] == str(selected / "serve.sock")
            assert kwargs["cwd"] == home
            assert kwargs["env_overrides"]["AGENT_MCP_HOME"] == str(home)
            observed.append(True)
        finally:
            instances.cancel_instance_reservation(receipt, creation_refused=True)
        raise StopBeforeSpawn

    monkeypatch.setattr(agent_procutil, "spawn_daemon", reserve)
    with pytest.raises((OSError, ValueError) if linked else StopBeforeSpawn):
        if kind == "forward":
            forward._spawn_serve_host(str(selected / "serve.sock"))
        else:
            cutover.spawn_passive_daemon(SimpleNamespace(
                data_root=home, new_socket_path=selected / "serve.sock",
                new_log_path=home / "serve.log", new_control_token="synthetic",
                token_by_port={},
            ), 41234)
    assert observed == ([] if linked else [True])
