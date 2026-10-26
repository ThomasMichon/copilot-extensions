"""Pure controls only; synthetic inspect inputs cannot qualify a real lab."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_module() -> ModuleType:
    path = Path(__file__).parent / "reproducer-lab" / "boundary.py"
    spec = importlib.util.spec_from_file_location("reproducer_lab_boundary", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def declaration() -> dict[str, Any]:
    return {
        "schema": mod.SCHEMA,
        "version": 1,
        "name": "synthetic-spawn",
        "image": "sha256:" + "a" * 64,
        "source_sha": "b" * 40,
        "budgets": dict(mod._LIMITS),
    }


def inspection() -> dict[str, Any]:
    experiment = mod.parse_declaration(declaration())
    budget = experiment.budgets
    return {
        "Id": "c" * 64,
        "Image": experiment.image,
        "Config": {
            "User": "1000:1000",
            "Env": ["HOME=/home/lab", "PATH=/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED=1"],
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges"],
            "Binds": [],
            "Devices": [],
            "DeviceRequests": [],
            "PortBindings": {},
            "ExtraHosts": [],
            "Links": [],
            "VolumesFrom": [],
            "PublishAllPorts": False,
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "CgroupnsMode": "private",
            "Memory": budget.memory_bytes,
            "MemorySwap": budget.memory_bytes,
            "NanoCpus": budget.cpus * 10**9,
            "PidsLimit": budget.pids,
            "NetworkMode": experiment.resource_prefix + "-internal",
            "Mounts": [
                {"Type": "volume", "Source": name, "Target": destination, "ReadOnly": False}
                for destination, name in experiment.volumes.items()
            ],
        },
        "Mounts": [
            {"Type": "volume", "Name": name, "Destination": destination, "RW": True,
             "Source": f"/volumes/{name}"}
            for destination, name in experiment.volumes.items()
        ],
    }


def test_static_positive_is_not_provisioning_or_capacity_proof() -> None:
    experiment = mod.parse_declaration(declaration())
    observed = inspection()
    before = copy.deepcopy(observed)
    result = mod.inspect_static_boundary(experiment, observed)
    assert result.violations == ()
    assert len(result.unresolved) == 5
    assert any("monitoring must be measured separately" in item for item in result.unresolved)
    assert any("egress" in item for item in result.unresolved)
    assert not hasattr(result, "ready")
    assert observed == before


@pytest.mark.parametrize("field,value", [
    ("schema", "other"),
    ("version", True),
    ("version", 2),
    ("name", "../foreign"),
    ("name", "x" * 41),
    ("name", "host:/runtime"),
    ("image", "example:latest"),
    ("image", "sha256:short"),
    ("source_sha", "main"),
    ("source_sha", "b" * 39),
])
def test_invalid_declaration(field: str, value: object) -> None:
    doc = declaration()
    doc[field] = value
    with pytest.raises(mod.DeclarationError):
        mod.parse_declaration(doc)


@pytest.mark.parametrize("field", ["environment", "binds", "command", "ready"])
def test_unknown_fields_cannot_supply_effects_or_authority(field: str) -> None:
    doc = declaration()
    doc[field] = "synthetic"
    with pytest.raises(mod.DeclarationError):
        mod.parse_declaration(doc)


@pytest.mark.parametrize("field", list(mod._LIMITS))
@pytest.mark.parametrize("value", [0, -1, True, "1", 1.5, 10**15])
def test_budgets_are_finite_positive_bounded_integers(field: str, value: object) -> None:
    doc = declaration()
    doc["budgets"][field] = value
    with pytest.raises(mod.DeclarationError):
        mod.parse_declaration(doc)


@pytest.mark.parametrize("field,maximum", list(mod._LIMITS.items()))
def test_exact_budget_ceiling_and_one_above(field: str, maximum: int) -> None:
    doc = declaration()
    doc["budgets"][field] = 1
    mod.parse_declaration(doc)
    doc["budgets"][field] = maximum
    mod.parse_declaration(doc)
    doc["budgets"][field] = maximum + 1
    with pytest.raises(mod.DeclarationError):
        mod.parse_declaration(doc)


@pytest.mark.parametrize("value", [None, [], {}, "synthetic", 1])
def test_malformed_declaration_shape(value: object) -> None:
    with pytest.raises(mod.DeclarationError):
        mod.parse_declaration(value)


def test_diagnostics_do_not_echo_untrusted_payloads() -> None:
    doc = declaration()
    doc["name"] = "/synthetic-sensitive-input"
    with pytest.raises(mod.DeclarationError) as error:
        mod.parse_declaration(doc)
    assert "synthetic-sensitive-input" not in str(error.value)


def test_git_sha256_declaration() -> None:
    doc = declaration()
    doc["source_sha"] = "b" * 64
    assert mod.parse_declaration(doc).source_sha == "b" * 64


@pytest.mark.parametrize("field,value", [
    ("ReadonlyRootfs", False),
    ("Privileged", True),
    ("CapDrop", []),
    ("CapAdd", ["SYS_ADMIN"]),
    ("SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
    ("Binds", ["/synthetic-host:/host"]),
    ("Devices", [{"PathOnHost": "/dev/synthetic"}]),
    ("DeviceRequests", [{"Count": -1}]),
    ("PortBindings", {"1/tcp": [{"HostPort": "1"}]}),
    ("ExtraHosts", ["synthetic:host-gateway"]),
    ("Links", ["foreign:peer"]),
    ("VolumesFrom", ["foreign"]),
    ("PublishAllPorts", True),
    ("PidMode", "host"),
    ("IpcMode", "container:foreign"),
    ("UTSMode", "host"),
    ("UsernsMode", "host"),
    ("CgroupnsMode", "host"),
    ("CgroupnsMode", ""),
    ("Memory", 0),
    ("MemorySwap", -1),
    ("NanoCpus", 0),
    ("PidsLimit", 0),
    ("NetworkMode", "host"),
])
def test_unsafe_host_observation_is_rejected(field: str, value: object) -> None:
    observed = inspection()
    observed["HostConfig"][field] = value
    result = mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed)
    assert result.violations
    assert result.unresolved


@pytest.mark.parametrize("field", [
    "ReadonlyRootfs", "Privileged", "CapDrop", "CapAdd", "SecurityOpt", "PublishAllPorts",
    "PidMode", "IpcMode", "UTSMode", "UsernsMode", "Memory", "MemorySwap",
    "NanoCpus", "PidsLimit", "NetworkMode", "CgroupnsMode", "Binds", "Devices",
    "DeviceRequests", "PortBindings", "ExtraHosts", "Links", "VolumesFrom",
])
def test_missing_required_observation_is_not_a_safe_default(field: str) -> None:
    observed = inspection()
    del observed["HostConfig"][field]
    assert mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed).violations


@pytest.mark.parametrize("value", [None, {}, [], {"Config": {}, "HostConfig": []}])
def test_incomplete_inspection_never_succeeds(value: object) -> None:
    observed = value if isinstance(value, dict) else {}
    result = mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed)
    assert result.violations
    assert result.unresolved


@pytest.mark.parametrize("change", ["foreign", "duplicate", "bind", "tmpfs", "extra", "readonly"])
def test_mount_inventory_preserves_exact_experiment_scope(change: str) -> None:
    observed = inspection()
    mounts = observed["Mounts"]
    if change == "foreign":
        mounts[0]["Name"] = "reproducer-lab-other-home"
    elif change == "duplicate":
        mounts[1] = copy.deepcopy(mounts[0])
    elif change == "bind":
        mounts[0]["Type"] = "bind"
    elif change == "tmpfs":
        mounts[0]["Type"] = "tmpfs"
    elif change == "extra":
        mounts.append({"Destination": "/var/run/docker.sock", "Type": "bind", "RW": True})
    else:
        mounts[0]["RW"] = False
    assert mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed).violations


@pytest.mark.parametrize("entry", [
    "SYNTHETIC_TOKEN=not-a-real-secret",
    "HTTP_PROXY=http://example.invalid",
    "AGENT_BRIDGE_CONFIG_DIR=/foreign",
    "HOME=/foreign",
    "HOME=/home/lab",
])
def test_environment_additions_and_duplicates_are_rejected(entry: str) -> None:
    observed = inspection()
    observed["Config"]["Env"].append(entry)
    assert mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed).violations


def test_resource_names_are_disjoint_between_experiments() -> None:
    first = mod.parse_declaration(declaration())
    doc = declaration()
    doc["name"] = "other"
    second = mod.parse_declaration(doc)
    assert set(first.volumes.values()).isdisjoint(second.volumes.values())


@pytest.mark.parametrize("field,value", [
    ("Id", "short"),
    ("Image", "sha256:" + "d" * 64),
])
def test_wrong_or_incomplete_physical_identity_is_rejected(field: str, value: str) -> None:
    observed = inspection()
    observed[field] = value
    assert mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed).violations


@pytest.mark.parametrize("field,value", [
    ("User", "0:0"),
    ("Env", ["HOME=/foreign", "PATH=/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED=1"]),
    ("Env", ["HOME=/home/lab", "HOME=/home/lab", "PYTHONUNBUFFERED=1"]),
    ("Env", [None, "PATH=/usr/local/bin:/usr/bin:/bin", "PYTHONUNBUFFERED=1"]),
])
def test_user_and_same_length_environment_tampering_are_rejected(
    field: str, value: object,
) -> None:
    observed = inspection()
    observed["Config"][field] = value
    assert mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed).violations


def test_unsupported_quota_claim_cannot_clear_pending_measurement() -> None:
    observed = inspection()
    observed["quota_verified"] = True
    observed["ready"] = True
    result = mod.inspect_static_boundary(mod.parse_declaration(declaration()), observed)
    assert result.violations == ()
    assert any("monitoring must be measured separately" in item for item in result.unresolved)


def test_explicit_null_cap_add_is_distinct_from_missing_observation() -> None:
    observed = inspection()
    observed["HostConfig"]["CapAdd"] = None
    experiment = mod.parse_declaration(declaration())
    assert mod.inspect_static_boundary(experiment, observed).violations == ()
    del observed["HostConfig"]["CapAdd"]
    assert mod.inspect_static_boundary(experiment, observed).violations
