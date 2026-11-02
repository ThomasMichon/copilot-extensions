"""Test-driver custody around the Gateway witness's synthetic service."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_fixture(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "libs" / "agent-procutil" / "src"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.web = types.SimpleNamespace()
    jwk = types.ModuleType("joserfc.jwk")
    jwk.ECKey = object
    connector = types.ModuleType("fleet_manager.gateway_service_connector")
    connector.ServiceConnector = object
    for parent in ("joserfc", "fleet_manager"):
        package = types.ModuleType(parent)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, parent, package)
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)
    monkeypatch.setitem(sys.modules, "joserfc.jwk", jwk)
    monkeypatch.setitem(sys.modules, "fleet_manager.gateway_service_connector", connector)
    path = ROOT / "tools" / "clean-room" / "gateway-role-tunnels" / "fixture_service.py"
    spec = importlib.util.spec_from_file_location("gateway_role_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.STATE_ROOT = tmp_path
    return module


@pytest.mark.parametrize("body_fails", [False, True])
def test_gateway_fixture_inherits_runner_custody_without_self_renewal(
    monkeypatch, tmp_path, body_fails
):
    fixture = load_fixture(monkeypatch, tmp_path)
    from agent_procutil.daemon_instances import (
        admit_daemon_instance, daemon_test_lifecycle, inspect_instances,
    )

    before = dict(os.environ)
    observed = []

    async def body(letter, instance):
        assert (letter, instance) == ("a", "fixture-a")
        with admit_daemon_instance(
            "gateway-role-fixture",
            runtime_root=tmp_path / "gateway-role-runtime",
            source=Path(fixture.__file__),
        ) as guard:
            rows = inspect_instances(tmp_path / ".gateway-role-instance-registry")
            current = next(
                row for row in rows if row["instance_id"] == guard.receipt.instance_id
            )
            observed.append(current["purpose"])
            if body_fails:
                raise RuntimeError("fixture-body-failed")

    monkeypatch.setattr(fixture, "serve", body)
    monkeypatch.setattr(
        fixture, "daemon_test_lifecycle",
        lambda **_kwargs: pytest.fail("the service must not own lease renewal"),
    )
    with daemon_test_lifecycle(
        owner="proof-runner", registry_root=tmp_path / ".gateway-role-instance-registry",
    ) as environment:
        with monkeypatch.context() as child:
            for key, value in environment.items():
                child.setenv(key, value)
            if body_fails:
                with pytest.raises(RuntimeError, match="fixture-body-failed"):
                    fixture.main("a", "fixture-a")
            else:
                fixture.main("a", "fixture-a")
    assert observed == ["unattended-test"]
    assert dict(os.environ) == before


def test_synthetic_production_probe_refuses_host_execution(monkeypatch, tmp_path):
    path = (
        ROOT / "tools" / "clean-room" / "scenarios" / "leased-daemon-instances"
        / "fixtures" / "probe.py"
    )
    spec = importlib.util.spec_from_file_location("daemon_instance_proof", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    work = tmp_path / "must-not-be-created"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        sys, "argv", [str(path), "--source", str(ROOT), "--work-root", str(work)]
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert not work.exists()
