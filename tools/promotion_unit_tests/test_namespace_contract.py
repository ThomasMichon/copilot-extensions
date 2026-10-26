"""Join the actual Containers JSON producer to the actual Bridge roster reader."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, TypeVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
T = TypeVar("T")


def complete_inline(coroutine: Coroutine[Any, Any, T]) -> T:
    """Run only coroutines whose substituted I/O completes without suspension."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("namespace unit attempted to suspend or start an actor")


def unavailable(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("unrelated provider integration was invoked")


@dataclass
class NamespaceContract:
    inventory: list[Any]
    config: SimpleNamespace
    leases: dict[str, Any]
    holds: dict[str, dict[str, Any]]
    calls: list[tuple[list[str], float]]
    wire: list[tuple[int, str, str]]
    lifecycle: ModuleType
    reader: Any
    incomplete: type[Exception]


@pytest.fixture
def contract(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> NamespaceContract:
    def module(name: str, **attributes: Any) -> ModuleType:
        result = ModuleType(name)
        result.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    def load(package: str, child: str, plugin: str) -> ModuleType:
        path = ROOT / f"plugins/{plugin}/src/{plugin.replace('-', '_')}/{child}.py"
        specification = importlib.util.spec_from_file_location(f"{package}.{child}", path)
        assert specification and specification.loader
        result = importlib.util.module_from_spec(specification)
        monkeypatch.setitem(sys.modules, specification.name, result)
        specification.loader.exec_module(result)
        setattr(sys.modules[package], child, result)
        return result

    provider = "promotion_namespace_provider"
    bridge = "promotion_namespace_consumer"
    module(provider, __path__=[])
    module(bridge, __path__=[])
    procutil = module("agent_procutil", __path__=[], no_window_flags=unavailable)
    module("agent_procutil.metrics", CpuObservation=object)
    module("plugin_resolve", __path__=[])
    module("plugin_resolve.reflection", selection_spec=unavailable)
    config = SimpleNamespace(fleets={}, image_prefixes=[])
    configuration = module(
        f"{provider}.config",
        ContainersConfig=SimpleNamespace,
        FleetConfig=SimpleNamespace,
        load_config=lambda: config,
        is_sensitive_environment_name=unavailable,
        TRUSTED_PROFILE="trusted",
        RESTRICTED_PROFILE="restricted",
    )
    for label in (
        "FLEET", "DISPATCH_ONLY", "DISPATCH_BINDING", "SECURITY_PROFILE",
        "SECURITY_POLICY", "SECURITY_HOME", "SECURITY_UID", "SECURITY_GID",
        "SECURITY_IMAGE_ID",
    ):
        setattr(configuration, f"{label}_LABEL", f"synthetic.{label.lower()}")
    for child, names in {
        "_invoke": ["payload_command_argv"],
        "inference": ["NativeInferenceConfig"],
        "private_state": [
            "enforce_mode", "fsync_directory", "private_file_lock", "strict_posix_modes",
        ],
        "ssh_transport": [
            "prepare_ssh_config", "build_remote_command", "build_ssh_command",
            "cleanup_remote_env", "cleanup_remote_envs", "container_environment",
            "write_remote_env",
        ],
        "lifecycle_ownership": ["lifecycle_blocker", "validate_session_owner"],
    }.items():
        module(f"{provider}.{child}", **dict.fromkeys(names, unavailable))
    inventory: list[Any] = []
    leases: dict[str, Any] = {}
    holds: dict[str, dict[str, Any]] = {}
    module(
        f"{provider}.lease",
        get_lease=lambda name: leases.get(name),
        get_deploy_hold=unavailable,
        deploy_hold_status=lambda name: holds[name],
    )
    lifecycle = load(provider, "lifecycle", "agent-containers")

    def list_containers(actual: Any) -> list[Any]:
        assert actual is config
        return inventory.copy()

    monkeypatch.setattr(lifecycle, "list_containers", list_containers)
    load(provider, "resolver", "agent-containers")
    for child in ("hosting_policy", "copilot_process_auth"):
        module(f"{provider}.{child}")
    for child, names in {
        "claim_provider_cli": ["add_claim_provider_parsers"],
        "rescue_capture_cli": ["add_rescue_capture_parser"],
        "dispatch_projection": ["add_exec_subparser", "add_subparsers"],
        "copilot_login": ["add_copilot_login_subparser"],
        "copilot_venue": ["add_copilot_subparser", "add_plugin_selection_arguments"],
        "forward_keeper": ["add_subparser"],
        "workspace": ["add_workspace_subparsers"],
    }.items():
        module(f"{provider}.{child}", **dict.fromkeys(names, lambda parser: None))
    cli = load(provider, "__main__", "agent-containers")

    common = load(bridge, "agent_registry_common", "agent-bridge")
    load(bridge, "reader_admission", "agent-bridge")
    module(f"{bridge}.transport", PluginRef=object, SpawnTarget=object)
    module(
        f"{bridge}.reader_observation",
        ReaderCpuObservation=lambda prefix: SimpleNamespace(),
    )
    consumer = load(bridge, "agent_registry_namespace", "agent-bridge")
    reader = consumer.CliNamespaceResolver("container", "synthetic-provider")
    calls: list[tuple[list[str], float]] = []
    wire: list[tuple[int, str, str]] = []

    async def to_thread(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return function(*args, **kwargs)

    async def run(argv: list[str], *, timeout: float = 90.0) -> tuple[int, str, str]:
        assert argv == ["namespace-list"]
        calls.append((argv.copy(), timeout))
        code = cli.main(argv)
        output = capsys.readouterr()
        result = code, output.out, output.err
        wire.append(result)
        return result

    monkeypatch.setattr(asyncio, "run", complete_inline)
    monkeypatch.setattr(asyncio, "to_thread", to_thread)
    monkeypatch.setattr(consumer.shutil, "which", lambda command: "/synthetic/provider")
    monkeypatch.setattr(reader, "_run", run)
    assert common.NamespaceAgentInfo is consumer.NamespaceAgentInfo
    assert procutil.no_window_flags is unavailable
    return NamespaceContract(
        inventory, config, leases, holds, calls, wire, lifecycle, reader,
        consumer.NamespaceListIncomplete,
    )


@pytest.mark.parametrize(
    "state,profile,leased,held,expected_state",
    [
        ("running", "trusted", False, False, "running"),
        ("exited", "trusted", True, False, "exited"),
        ("", "trusted", False, False, "unknown"),
        ("running", "restricted", True, True, "draining"),
        ("running", "restricted", False, False, "running"),
    ],
    ids=["running", "stopped-leased", "unknown", "held-restricted", "ready-restricted"],
)
def test_actual_namespace_output_reaches_bridge(
    contract: NamespaceContract, state: str, profile: str, leased: bool,
    held: bool, expected_state: str,
) -> None:
    contract.config.fleets["example"] = SimpleNamespace(restricted=profile == "restricted")
    contract.inventory.append(contract.lifecycle.DockerContainerInfo(
        name="example-1", container_id="synthetic-id", image="example/image:latest",
        state=state, status="synthetic status", fleet="example",
        local_folder="/synthetic/project", security_profile=profile,
    ))
    if leased:
        contract.leases["example-1"] = SimpleNamespace(effort="example-effort")
    contract.holds["example-1"] = {
        "state": "held" if held else "none", "operation": None, "reason": None,
    }
    agents = complete_inline(contract.reader._enumerate_once(timeout=2.5, ttl=12.0))
    assert len(agents) == 1
    agent = agents[0]
    assert (agent.name, agent.display_name, agent.icon, agent.state, agent.aliases) == (
        "example-1", "example-1 (project)", "container", expected_state, [],
    )
    description = f"Local dev container: example/image:latest \u2014 {profile}"
    if leased:
        description += " \u2014 leased by example-effort"
    assert agent.description == description
    assert contract.calls == [(["namespace-list"], 2.5)]
    assert contract.wire[0][0] == 0 and contract.wire[0][2] == ""
    assert json.loads(contract.wire[0][1])[0]["name"] == agent.name
    assert contract.reader._list_cache[1] is agents
    assert contract.reader._list_native_success is True


def test_actual_empty_provider_is_an_authoritative_empty_roster(
    contract: NamespaceContract,
) -> None:
    assert complete_inline(contract.reader._enumerate_once(timeout=None, ttl=0)) == []
    assert contract.wire == [(0, "[]\n", "")]
    assert contract.reader._list_cache is None
    assert contract.reader._list_native_success is True


def test_actual_provider_preserves_multiple_agents_and_repository_fallbacks(
    contract: NamespaceContract,
) -> None:
    for name, fleet in [("example-1", "example-fleet"), ("example-2", None)]:
        contract.inventory.append(contract.lifecycle.DockerContainerInfo(
            name=name, container_id=f"synthetic-{name}", image="example/image",
            state="exited", status="synthetic status", fleet=fleet,
        ))
    agents = complete_inline(contract.reader._enumerate_once(timeout=None, ttl=0))
    assert [(agent.name, agent.display_name, agent.state) for agent in agents] == [
        ("example-1", "example-1 (example-fleet)", "exited"),
        ("example-2", "example-2", "exited"),
    ]
    assert len(contract.calls) == 1
    assert contract.reader._list_native_success is True


@pytest.mark.parametrize(
    "fault",
    ["renamed-name", "extra-field", "object-root", "invalid-row", "truncated-json", "partial"],
)
def test_actual_provider_wire_drift_is_incomplete_not_empty(
    contract: NamespaceContract, monkeypatch: pytest.MonkeyPatch, caplog: Any, fault: str,
) -> None:
    contract.inventory.append(contract.lifecycle.DockerContainerInfo(
        name="example-1", container_id="synthetic-id", image="example/image",
        state="running", status="synthetic status",
    ))
    original = contract.reader._run

    async def drift(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
        code, stdout, stderr = await original(argv, **kwargs)
        assert code == 0 and stderr == ""
        rows = json.loads(stdout)
        if fault == "renamed-name":
            rows[0]["target"] = rows[0].pop("name")
        elif fault == "extra-field":
            rows[0]["new_provider_field"] = True
        elif fault == "object-root":
            rows = {"agents": rows}
        elif fault == "invalid-row":
            rows = [None]
        elif fault == "partial":
            rows.append({"target": "example-2"})
        return code, stdout[:-2] if fault == "truncated-json" else json.dumps(rows), stderr

    monkeypatch.setattr(contract.reader, "_run", drift)
    with pytest.raises(contract.incomplete, match="namespace-list produced unparseable output"):
        complete_inline(contract.reader._enumerate_once(timeout=2.5, ttl=12.0))
    assert "namespace-list output unparseable" in caplog.text
    assert contract.reader._list_cache is None
    assert contract.reader._list_native_success is False
    assert len(contract.wire) == 1


def test_actual_provider_failure_cannot_publish_an_empty_roster(
    contract: NamespaceContract, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = sys.modules["promotion_namespace_provider.resolver"]

    def failed_inventory(config: Any) -> list[Any]:
        raise RuntimeError("synthetic Docker inventory unavailable")

    monkeypatch.setattr(provider, "list_containers", failed_inventory)
    with pytest.raises(contract.incomplete, match="namespace-list exited 1"):
        complete_inline(contract.reader._enumerate_once(timeout=None, ttl=12.0))
    assert contract.wire == [(1, "", "ERROR: synthetic Docker inventory unavailable\n")]
    assert contract.reader._list_cache is None
    assert contract.reader._list_native_success is False
