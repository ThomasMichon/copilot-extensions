"""Launch origins retain policy before passive overrides, without activation."""

from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from zdd.private_protocol import PrivateIPCError, Refusal

from agent_bridge.launch_runtime import executing_executable, record_launch_origins
from agent_bridge.models import ServiceConfig


def test_record_resolves_startup_roots_and_original_policy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", "relative-config")
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", "relative-install")
    monkeypatch.setenv("DECLARED_FIXTURE_SECRET", "not-part-of-origins")
    config = ServiceConfig(
        port=43210, enable_credential_relay=True, db_path=str(tmp_path / "synthetic.db"),
    )
    origins = record_launch_origins(
        config, required_environment=("DECLARED_FIXTURE_SECRET", "DECLARED_ABSENCE"),
    )
    config.port = 54321
    config.enable_credential_relay = False
    assert origins.config_root == tmp_path / "relative-config"
    assert origins.install_root == tmp_path / "relative-install"
    assert origins.executable == executing_executable()
    assert origins.port_policy == 43210
    assert origins.relay_policy
    assert origins.required_environment == ("DECLARED_FIXTURE_SECRET", "DECLARED_ABSENCE")
    assert "not-part-of-origins" not in repr(origins)
    assert not origins.config_root.exists()
    assert not origins.install_root.exists()


@pytest.mark.parametrize("contract", [None, [], ["SECRET"], ("SECRET", "SECRET"), ("A=B",)])
def test_invalid_or_implicit_environment_contract_refuses(contract):
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        record_launch_origins(ServiceConfig(), required_environment=contract)


def test_explicit_empty_contract_is_not_guessed_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("UNDECLARED_FIXTURE_SECRET", "never-inspect")
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    origins = record_launch_origins(ServiceConfig(), required_environment=())
    assert origins.required_environment == ()
    assert "never-inspect" not in repr(origins)


@pytest.mark.parametrize("executable", ["", "/nonexistent-private-interpreter"])
def test_unknown_executing_executable_refuses_without_private_details(monkeypatch, executable):
    if os.name == "nt":
        from zdd.private_windows_native import Native

        def failed_query(self, handle):
            raise PrivateIPCError(Refusal.IDENTITY)

        monkeypatch.setattr(Native, "process_image", failed_query)
    else:
        monkeypatch.setattr(sys, "executable", executable)
    with pytest.raises(PrivateIPCError) as error:
        executing_executable()
    assert str(error.value) == Refusal.AUTHORITY.value


@pytest.mark.parametrize("contract", [None, (), ("DECLARED_FIXTURE_SECRET",)])
def test_cmd_start_records_only_explicit_contract_before_activation(
    tmp_path, monkeypatch, contract,
):
    import agent_bridge.app as app_module
    import agent_bridge.config as config_module
    import agent_bridge.service_start_cli as start
    import agent_bridge.singleton as singleton
    import agent_bridge.watchdog as watchdog
    import agent_bridge.winjob as winjob

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(tmp_path / "install"))
    config = ServiceConfig(
        port=43210, enable_credential_relay=True, db_path=str(tmp_path / "synthetic.db"),
    )
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(config_module, "migrate_config", lambda cfg: cfg)
    monkeypatch.setattr(config_module, "write_default_config", lambda cfg: None)
    monkeypatch.setattr(config_module, "load_or_create_auth_token", lambda: "fixture-auth")
    monkeypatch.setattr(winjob, "setup_kill_on_close_job", lambda: None)
    monkeypatch.setattr(watchdog, "arm_serving_watchdog", lambda *args, **kwargs: None)
    monkeypatch.setattr(start.os, "chdir", lambda path: None)
    monkeypatch.setattr(start, "_dynamic_bind_requested", lambda *args: False)
    released = []
    monkeypatch.setattr(
        singleton, "SingleInstance",
        lambda *args, **kwargs: SimpleNamespace(
            acquire=lambda: None, release=lambda: released.append(True),
        ),
    )
    closed = []
    monkeypatch.setattr(
        start, "_bind_listen_socket",
        lambda *args: SimpleNamespace(
            getsockname=lambda: ("127.0.0.1", 54321), close=lambda: closed.append(True),
        ),
    )
    app = FastAPI()

    def factory(*, config, token):
        app.state.config = config
        return app

    monkeypatch.setattr(app_module, "create_app", factory)
    monkeypatch.setattr(start.uvicorn, "Config", lambda app, **kwargs: object())

    def server_run(*, sockets):
        assert app.state.config.port == 54321
        assert not app.state.config.enable_credential_relay
        assert app.state.passive
        assert not app.state.publish_on_ready
        if contract is None:
            assert not hasattr(app.state, "runtime_origins")
        else:
            assert app.state.runtime_origins.port_policy == 43210
            assert app.state.runtime_origins.relay_policy
            assert app.state.runtime_origins.required_environment == contract
            assert app.state.runtime_origins.executable == executing_executable()

    monkeypatch.setattr(
        start.uvicorn, "Server",
        lambda config: SimpleNamespace(run=server_run),
    )
    start._cmd_start(
        argparse.Namespace(port=54321, bind=None, idle_shutdown=None, passive=True),
        required_runtime_environment=contract,
    )
    assert released == [True]
    assert closed == [True]
