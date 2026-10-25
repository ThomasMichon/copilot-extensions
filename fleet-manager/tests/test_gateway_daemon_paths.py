"""CLI path selection reaches real admission without dereferencing runtime aliases."""
import json

import pytest

from fleet_manager import gateway_server
from fleet_manager.__main__ import main
from gateway_fixtures import Fixtures


@pytest.fixture(autouse=True)
def private_registry(tmp_path, monkeypatch):
    from agent_procutil import _daemon_instance_store as store

    monkeypatch.setenv(store.REGISTRY_ENV, str(tmp_path / "registry"))
    monkeypatch.delenv(store.INSTANCE_ENV, raising=False)
    monkeypatch.delenv(store.TEST_CONTEXT_ENV, raising=False)


def test_gateway_cli_parent_relative_config_reaches_admitted_body(tmp_path, monkeypatch):
    directory = tmp_path / "config"
    fixture = Fixtures(directory)
    path = directory / "gateway.json"
    path.write_text(json.dumps(fixture.value))
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.chdir(launch)
    observed = []

    class Server:
        def serve_forever(self, **kwargs):
            observed.append("serve")

        def server_close(self):
            observed.append("closed")

    monkeypatch.setattr(gateway_server, "build_server", lambda *_args: Server())
    assert main(["serve", "--role", "gateway", "--config", "../config/gateway.json"]) == 0
    assert observed == ["serve", "closed"]


def test_gateway_cli_linked_config_parent_refuses_before_body(tmp_path, monkeypatch):
    directory = tmp_path / "config"
    Fixtures(directory)
    (directory / "gateway.json").write_text("{}")
    launch = tmp_path / "launch"
    launch.mkdir()
    alias = launch / "linked"
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native directory symlink unavailable: {error}")
    monkeypatch.chdir(launch)
    monkeypatch.setattr(
        gateway_server, "build_server", lambda *_args: pytest.fail("linked root reached server body"),
    )
    with pytest.raises((OSError, ValueError, RuntimeError)):
        main(["serve", "--role", "gateway", "--config", "linked/gateway.json"])
