"""Exercise the production Docker inventory parser without process effects."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-containers/src/agent_containers/lifecycle.py"


@pytest.fixture
def inventory(monkeypatch):
    name = "promotion_container_inventory"
    package = types.ModuleType(name)
    package.__path__ = []
    config = types.ModuleType(f"{name}.config")
    for constant in (
        "FLEET_LABEL", "RESTRICTED_PROFILE", "SECURITY_GID_LABEL",
        "SECURITY_HOME_LABEL", "SECURITY_IMAGE_ID_LABEL", "SECURITY_POLICY_LABEL",
        "SECURITY_PROFILE_LABEL", "SECURITY_UID_LABEL", "TRUSTED_PROFILE",
    ):
        setattr(config, constant, constant.lower())
    config.ContainersConfig = types.SimpleNamespace
    config.FleetConfig = types.SimpleNamespace
    config.is_sensitive_environment_name = lambda _: False
    monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, config.__name__, config)
    procutil = types.ModuleType("agent_procutil")
    procutil.no_window_flags = lambda: 0
    monkeypatch.setitem(sys.modules, "agent_procutil", procutil)
    specification = importlib.util.spec_from_file_location(f"{name}.lifecycle", SOURCE)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, specification.name, module)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize("all_containers", [True, False])
def test_inventory_uses_one_query_and_preserves_fields(inventory, monkeypatch, all_containers):
    row = (
        "example-1\tfull-id\tvsc-example\texited\tExited\t"
        "devcontainer.local_folder=/example\trestricted\tpolicy-v1\tsha256:image"
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, row + "\n\nunrelated\tbad\n", "")

    monkeypatch.setattr(inventory.subprocess, "run", run)
    result = inventory.list_containers(
        types.SimpleNamespace(image_prefixes=["vsc-"]), all_containers=all_containers,
    )
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        "docker", "ps", *(["-a"] if all_containers else []),
        "--no-trunc", "--format", inventory._PS_FORMAT,
    ]
    assert options == {
        "capture_output": True, "text": True, "timeout": 30.0, "creationflags": 0,
    }
    assert len(result) == 1
    info = result[0]
    assert (info.name, info.container_id, info.image, info.state, info.status) == (
        "example-1", "full-id", "vsc-example", "exited", "Exited",
    )
    assert info.local_folder == "/example"
    assert (info.security_profile, info.security_policy, info.security_image_id) == (
        "restricted", "policy-v1", "sha256:image",
    )


def test_empty_inventory_is_success(inventory, monkeypatch):
    monkeypatch.setattr(
        inventory.subprocess, "run",
        lambda command, **_: subprocess.CompletedProcess(command, 0, "", ""),
    )
    assert inventory.list_containers(types.SimpleNamespace(image_prefixes=[])) == []


@pytest.mark.parametrize("failure", ["missing-cli", "query", "timeout"])
def test_inventory_failures_are_not_empty_results(inventory, monkeypatch, failure):
    def run(command, **kwargs):
        if failure == "missing-cli":
            raise FileNotFoundError("missing")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 1, "", "daemon permission denied")

    monkeypatch.setattr(inventory.subprocess, "run", run)
    expected = {
        "missing-cli": "docker CLI not found on PATH",
        "query": "docker ps failed: daemon permission denied",
        "timeout": "docker ps timed out after 30s",
    }
    with pytest.raises(RuntimeError, match=expected[failure]):
        inventory.list_containers(types.SimpleNamespace(image_prefixes=[]))
