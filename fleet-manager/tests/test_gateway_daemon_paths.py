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


def test_gateway_opens_the_same_lexical_selection_as_admission(tmp_path, monkeypatch):
    from pathlib import Path

    launch = tmp_path / "launch"
    launch.mkdir()
    selected = launch / "state"
    fixture = Fixtures(selected)
    (selected / "gateway.json").write_text(json.dumps(fixture.value))
    redirected = tmp_path / "redirected"
    (redirected / "child").mkdir(parents=True)
    (redirected / "state").mkdir()
    (redirected / "state" / "gateway.json").write_text(json.dumps(
        fixture.value | {"installation": "redirected-install"},
    ))
    alias = launch / "link"
    try:
        alias.symlink_to(redirected / "child", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native directory symlink unavailable: {error}")
    opened, installations = [], []
    original_open = Path.open

    def open_path(path, *args, **kwargs):
        if path.name == "gateway.json":
            opened.append(path)
        return original_open(path, *args, **kwargs)

    class Server:
        def serve_forever(self, **kwargs):
            pass

        def server_close(self):
            pass

    def build(config, _signer):
        installations.append(config.installation)
        return Server()

    monkeypatch.setattr(Path, "open", open_path)
    monkeypatch.setattr(gateway_server, "build_server", build)
    assert gateway_server.serve(alias / ".." / "state" / "gateway.json") == 0
    assert installations == ["gateway-install"]
    assert opened == [selected / "gateway.json"]
