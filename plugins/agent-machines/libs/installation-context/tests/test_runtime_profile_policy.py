"""Read-only runtime intent through the real installation/configuration authority."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_installation_mode_governance import (
    PYTHON_SCRIPT,
    _api_arguments,
    _cell_layout,
    _load_module,
    _profile_environment,
    _policy,
    _write_json,
)


@pytest.fixture
def adopter(tmp_path):
    module = _load_module()
    layout = _cell_layout(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    copilot = profile / ".copilot"
    key = str(layout["vector"]["marketplaceKey"])
    payload = copilot / "installed-plugins" / key / "agent-example"
    payload.mkdir(parents=True)
    project = tmp_path / "project"
    settings = project / ".github" / "copilot" / "settings.json"
    _write_json(
        settings,
        {
            "extraKnownMarketplaces": {key: {"source": layout["vector"]["descriptor"]}},
            "enabledPlugins": {f"agent-example@{key}": True},
        },
    )
    arguments = _api_arguments(
        layout,
        profile,
        profile / ".agent-example",
        payload_root=payload,
        copilot_home=copilot,
        project_root=project,
        source_descriptor=None,
        platform=_profile_environment(profile)["platform"],
    )
    # A fresh adopter has no installation, activation, policy or approval files.
    for path in (layout["install"], layout["namespace"]):
        path.unlink()
    return module, arguments, settings


def owned_policy(module, arguments, **profiles):
    resolution = module.resolve_runtime_deployment(**arguments)
    path = Path(resolution["deployment"]["path"])
    policy = {
        "schema": module.RUNTIME_DEPLOYMENT_SCHEMA,
        "version": 1,
        "owner": module._deployment_owner(resolution, arguments),
        "managed": False,
        "runtimeProfiles": profiles,
    }
    _write_json(path, policy)
    return path, policy


def test_fresh_configured_adopter_needs_no_profile_or_approval_file(adopter):
    module, arguments, _ = adopter
    before = sorted(arguments["os_profile"].rglob("*"))
    result = module.resolve_runtime_profile_policy(**arguments)
    profiles = result["runtimeProfiles"]
    assert profiles["status"] == "ready"
    assert profiles["selection"] == {
        "preferArtifact": True,
        "allowSourceFallback": True,
        "backend": "cpu",
    }
    assert profiles["owner"]["sourceFingerprint"].startswith("sha256:")
    assert profiles["owner"]["runtimeRoot"] == str(arguments["legacy_root"])
    assert profiles["policyId"].startswith("sha256:")
    assert sorted(arguments["os_profile"].rglob("*")) == before
    assert module.resolve_runtime_profile_policy(**arguments) == result


def test_pending_activation_never_selects_a_profile_for_the_legacy_root(adopter):
    module, arguments, _ = adopter
    source = module.resolve_payload_source(**{
        key: arguments[key]
        for key in (
            "payload_root", "plugin_id", "copilot_home", "project_root",
            "source_descriptor", "marketplace_key", "environment",
        )
        if key in arguments
    })
    _write_json(
        arguments["os_profile"] / ".copilot-extensions" / "installation-mode.json",
        _policy(True, source["marketplaceId"]),
    )
    arguments = {
        **arguments,
        "legacy_probe": {
            "declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z",
        },
    }
    mode = module.resolve_installation_mode(**arguments)
    assert (mode["desiredMode"], mode["actualMode"]) == ("namespaced", "legacy")
    assert (mode["status"], mode["reason"]) == ("ready", "activation-required")
    before = sorted(arguments["os_profile"].rglob("*"))
    result = module.resolve_runtime_profile_policy(**arguments)
    assert result["runtimeProfiles"] == {
        "status": "blocked", "reason": "runtime-profile-owner-unresolved",
        "owner": None, "selection": None, "policyId": None,
    }
    assert sorted(arguments["os_profile"].rglob("*")) == before


def test_owner_bound_optional_policy_can_disable_source_and_select_accelerator(adopter):
    module, arguments, _ = adopter
    path, _ = owned_policy(module, arguments, allowSourceFallback=False, backend="cuda12")
    before = path.read_bytes()
    result = module.resolve_runtime_profile_policy(**arguments)
    assert result["runtimeProfiles"]["selection"] == {
        "preferArtifact": True,
        "allowSourceFallback": False,
        "backend": "cuda12",
    }
    assert result["deployment"]["allowPluginPublication"] is False
    assert result["deployment"]["reason"] == "runtime-profile-consumer-required"
    assert path.read_bytes() == before


def test_download_origins_are_optional_canonical_and_bound_to_policy_identity(adopter):
    module, arguments, _ = adopter
    path, policy = owned_policy(module, arguments)
    before = module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]
    policy["runtimeProfiles"]["downloadOrigins"] = []
    _write_json(path, policy)
    assert module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"] == before
    policy["runtimeProfiles"]["downloadOrigins"] = [
        "https://FILES.example:443/", "https://other.example:8443",
    ]
    _write_json(path, policy)
    first = module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]
    assert first["selection"]["downloadOrigins"] == [
        "https://files.example", "https://other.example:8443",
    ]
    assert first["policyId"] != before["policyId"]
    policy["runtimeProfiles"]["downloadOrigins"].reverse()
    _write_json(path, policy)
    assert module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"] == first
    policy["runtimeProfiles"]["downloadOrigins"] = ["https://files.example"]
    _write_json(path, policy)
    assert module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]["policyId"] != first["policyId"]


@pytest.mark.parametrize("origins", [
    None, "https://files.example", {},
    ["http://files.example"], ["https://*.example"], ["https://files.example/path"],
    ["https://user:password@files.example"], ["https://files.example?credential=hidden"],
    ["https://files.example#fragment"], ["https://files.example\n"],
    ["https://files.example\\other"], ["https://files.example:0"],
    ["https://files.example:65536"], ["https://files.example", "https://FILES.example:443/"],
    ["https://[1:2:3:4:5:6:7:8:]"], ["https://[:1::2]"],
    ["https://files.example"] * 257,
])
def test_invalid_download_origins_block_policy_without_mutation(adopter, origins):
    module, arguments, _ = adopter
    path, _ = owned_policy(module, arguments, downloadOrigins=origins)
    before = path.read_bytes()
    result = module.resolve_runtime_profile_policy(**arguments)
    assert result["runtimeProfiles"]["status"] == "blocked"
    assert result["runtimeProfiles"]["selection"] is None
    assert result["runtimeProfiles"]["policyId"] is None
    assert path.read_bytes() == before


def test_download_origins_do_not_make_a_foreign_policy_authoritative(adopter):
    module, arguments, _ = adopter
    path, policy = owned_policy(module, arguments, downloadOrigins=["https://files.example"])
    policy["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
    _write_json(path, policy)
    result = module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]
    assert result["status"] == "blocked"
    assert result["selection"] is None


def test_policy_identity_changes_for_effective_policy_and_owner_not_file_format(adopter):
    module, arguments, _ = adopter
    first = module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]
    path, policy = owned_policy(module, arguments)
    policy.pop("runtimeProfiles")
    _write_json(path, policy)
    assert module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"] == first
    path.write_text(json.dumps(policy, separators=(",", ":")), encoding="utf-8")
    assert module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"] == first
    policy["runtimeProfiles"] = {"allowSourceFallback": False}
    _write_json(path, policy)
    changed = module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]
    assert changed["policyId"] != first["policyId"]
    other = {**arguments, "legacy_root": arguments["os_profile"] / ".other"}
    other_result = module.resolve_runtime_profile_policy(**other)["runtimeProfiles"]
    assert other_result["status"] == "ready"
    assert other_result["policyId"] != first["policyId"]


def test_retained_accelerator_and_explicit_migration_do_not_guess_device(adopter):
    module, arguments, _ = adopter
    retained = module.resolve_runtime_profile_policy(
        **arguments,
        retained_backend="cuda12",
        existing_installation=True,
    )["runtimeProfiles"]
    assert retained["selection"]["backend"] == "cuda12"
    with pytest.raises(module.InstallationContextError, match="explicit migration"):
        module.resolve_runtime_profile_policy(**arguments, existing_installation=True)
    migrated = module.resolve_runtime_profile_policy(
        **arguments,
        existing_installation=True,
        backend="cpu",
    )["runtimeProfiles"]
    assert migrated["selection"]["backend"] == "cpu"


@pytest.mark.parametrize(
    "profiles",
    [
        None,
        [],
        {"allowSourceFallback": "true"},
        {"allowSourceFallback": 1},
        {"backend": ""},
        {"backend": True},
        {"backend": "cuda12 "},
        {"backend": "https://public.example/simple"},
        {"approved": True},
        {"approvals": {"sha256:fake": "self"}},
        {"preferArtifact": False},
    ],
)
def test_malformed_or_self_authorizing_profile_configuration_refuses(adopter, profiles):
    module, arguments, _ = adopter
    path, policy = owned_policy(module, arguments)
    policy["runtimeProfiles"] = profiles
    _write_json(path, policy)
    result = module.resolve_runtime_profile_policy(**arguments)
    assert result["deployment"]["allowPluginPublication"] is False
    assert result["runtimeProfiles"]["status"] == "blocked"
    assert result["runtimeProfiles"]["selection"] is None
    assert result["runtimeProfiles"]["policyId"] is None


def test_conflicting_explicit_backend_refuses_without_mutation(adopter):
    module, arguments, _ = adopter
    path, _ = owned_policy(module, arguments, backend="cuda12")
    before = path.read_bytes()
    with pytest.raises(module.InstallationContextError, match="conflicts"):
        module.resolve_runtime_profile_policy(**arguments, backend="cpu")
    assert path.read_bytes() == before


def test_foreign_owner_and_missing_source_do_not_inherit_defaults(adopter):
    module, arguments, settings = adopter
    path, policy = owned_policy(module, arguments)
    foreign = copy.deepcopy(policy)
    foreign["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
    _write_json(path, foreign)
    assert (
        module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]["status"]
        == "blocked"
    )
    path.unlink()
    settings.unlink()
    result = module.resolve_runtime_profile_policy(**arguments)
    assert result["runtimeProfiles"]["status"] == "blocked"
    assert result["runtimeProfiles"]["selection"] is None


def test_profile_policy_never_clears_managed_publication_gate(adopter):
    module, arguments, _ = adopter
    path, policy = owned_policy(module, arguments)
    first = module.resolve_runtime_profile_policy(**arguments)["runtimeProfiles"]["policyId"]
    policy["managed"] = True
    _write_json(path, policy)
    result = module.resolve_runtime_profile_policy(**arguments)
    assert result["runtimeProfiles"]["status"] == "ready"
    assert result["deployment"]["allowPluginPublication"] is False
    assert result["runtimeProfiles"]["policyId"] != first
    profiled = result["runtimeProfiles"]
    policy.pop("runtimeProfiles")
    _write_json(path, policy)
    managed_only = module.resolve_runtime_profile_policy(**arguments)
    assert managed_only["deployment"]["allowPluginPublication"] is False
    assert managed_only["runtimeProfiles"]["selection"] == profiled["selection"]
    assert managed_only["runtimeProfiles"]["owner"] == profiled["owner"]
    assert managed_only["runtimeProfiles"]["policyId"] != profiled["policyId"]


def test_actual_fresh_isolated_cli_reads_configured_channel_and_cpu_default(adopter):
    _, arguments, _ = adopter
    command = [sys.executable, "-I", "-S", str(PYTHON_SCRIPT), "runtime-profile-policy"]
    for option, name in (
        ("--payload-root", "payload_root"),
        ("--plugin-id", "plugin_id"),
        ("--copilot-home", "copilot_home"),
        ("--project-root", "project_root"),
        ("--durable-home", "durable_home"),
        ("--legacy-root", "legacy_root"),
    ):
        command.extend([option, str(arguments[name])])
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "PYTHON", "AGENT_"))
    }
    for name in ("HOME", "USERPROFILE"):
        environment[name] = str(arguments["os_profile"])
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtimeProfiles"]["selection"] == {
        "preferArtifact": True,
        "allowSourceFallback": True,
        "backend": "cpu",
    }
    blocked = subprocess.run(
        [*command, "--existing-installation"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert blocked.returncode != 0
    assert "explicit migration" in blocked.stderr
