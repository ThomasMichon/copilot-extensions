"""Pure selection/owner controls; native channel reads remain in their own lane."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope="module")
def context_module():
    source = (
        Path(__file__).resolve().parents[2] / "libs/installation-context/installation_context.py"
    )
    spec = importlib.util.spec_from_file_location("_profile_policy_context", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "settings,backend,retained,existing,expected",
    [
        ({}, None, None, False, "cpu"),
        ({}, None, "cuda12", True, "cuda12"),
        ({}, "cpu", None, True, "cpu"),
        ({}, "cuda12", "cpu", True, "cuda12"),
        ({"backend": "cuda12"}, None, None, True, "cuda12"),
        ({"backend": "cpu"}, "cpu", "cuda12", True, "cpu"),
    ],
)
def test_capability_selection_retains_intent_or_requires_explicit_migration(
    context_module,
    settings,
    backend,
    retained,
    existing,
    expected,
):
    assert context_module._runtime_profile_selection(
        settings,
        backend=backend,
        retained_backend=retained,
        existing_installation=existing,
    ) == {"backend": expected, "preferArtifact": True, "allowSourceFallback": True}


@pytest.mark.parametrize(
    "settings,backend,retained,existing",
    [
        ({}, None, None, True),
        ({"backend": "cpu"}, "cuda12", None, False),
        ({}, "", None, False),
        ({}, False, None, False),
        ({}, None, "../cuda", False),
        ({}, None, None, 1),
    ],
)
def test_ambiguous_invalid_or_unprofiled_intent_refuses(
    context_module,
    settings,
    backend,
    retained,
    existing,
):
    with pytest.raises(context_module.InstallationContextError):
        context_module._runtime_profile_selection(
            settings,
            backend=backend,
            retained_backend=retained,
            existing_installation=existing,
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"allowSourceFallback": None},
        {"allowSourceFallback": 0},
        {"allowSourceFallback": "false"},
        {"backend": ""},
        {"backend": "cpu\n"},
        {"backend": "https://public.example/simple"},
        {"backend": "x" * 257},
        {"approved": True},
        {"approval_source": "self"},
        {"approvals": {}},
        {"preferArtifact": False},
        {"Backend": "cpu"},
    ],
)
def test_configuration_is_not_a_self_approved_record(context_module, value):
    with pytest.raises(context_module.InstallationContextError):
        context_module._runtime_profile_settings(value)


@pytest.mark.parametrize("fallback", [True, False])
def test_optional_fallback_policy_is_detached_and_exact(context_module, fallback):
    settings = {"allowSourceFallback": fallback}
    result = context_module._runtime_profile_selection(
        settings,
        backend=None,
        retained_backend=None,
        existing_installation=False,
    )
    settings["allowSourceFallback"] = not fallback
    assert result["allowSourceFallback"] is fallback


@pytest.fixture
def policy_owner(context_module, monkeypatch):
    resolution = {
        "status": "ready",
        "deployment": {"state": "missing", "managed": False},
    }
    owner = {
        "sourceFingerprint": "sha256:" + "1" * 64,
        "pluginId": "example",
        "mode": "legacy",
        "runtimeRoot": "fixture-runtime",
        "environment": {"platform": "fixture"},
    }
    calls = []
    monkeypatch.setattr(
        context_module,
        "resolve_runtime_deployment",
        lambda **args: (calls.append(args) or copy.deepcopy(resolution)),
    )
    monkeypatch.setattr(context_module, "_deployment_owner", lambda *_args: copy.deepcopy(owner))
    return resolution, owner, calls


def test_resolved_policy_binds_existing_owner_and_unchanged_intent(context_module, policy_owner):
    resolution, owner, calls = policy_owner
    result = context_module.resolve_runtime_profile_policy(payload_root="configured-payload")
    profiles = result["runtimeProfiles"]
    assert profiles["owner"] == owner
    assert profiles["status"] == "ready"
    assert calls == [{"payload_root": "configured-payload"}]
    assert (
        context_module.resolve_runtime_profile_policy(
            payload_root="configured-payload",
        )["runtimeProfiles"]
        == profiles
    )
    assert "runtimeProfiles" not in resolution
    for key, value in (
        ("sourceFingerprint", "sha256:" + "2" * 64),
        ("runtimeRoot", "other-runtime"),
        ("environment", {"platform": "other"}),
    ):
        previous = owner[key]
        owner[key] = value
        assert (
            context_module.resolve_runtime_profile_policy()["runtimeProfiles"]["policyId"]
            != profiles["policyId"]
        )
        owner[key] = previous


@pytest.mark.parametrize(
    "status,state",
    [
        ("provenance-blocked", "missing"),
        ("activation-required", "missing"),
        ("ready", "invalid"),
        ("ready", "unsupported"),
    ],
)
def test_unresolved_owner_or_policy_never_falls_back_to_cpu(
    context_module,
    policy_owner,
    status,
    state,
):
    resolution, _, _ = policy_owner
    resolution["status"] = status
    resolution["deployment"].update(state=state, reason="fixture-refusal")
    profiles = context_module.resolve_runtime_profile_policy()["runtimeProfiles"]
    assert profiles["status"] == "blocked"
    assert profiles["selection"] is None
    assert profiles["owner"] is None
    assert profiles["policyId"] is None


def test_effective_policy_change_invalidates_identity_without_widening_publication(
    context_module,
    policy_owner,
):
    resolution, _, _ = policy_owner
    original = context_module.resolve_runtime_profile_policy()["runtimeProfiles"]["policyId"]
    resolution["deployment"].update(
        state="valid",
        managed=True,
        allowPluginPublication=False,
        runtimeProfiles={"allowSourceFallback": False},
    )
    result = context_module.resolve_runtime_profile_policy()
    assert result["runtimeProfiles"]["policyId"] != original
    assert result["runtimeProfiles"]["selection"]["allowSourceFallback"] is False
    assert result["deployment"]["allowPluginPublication"] is False


@pytest.mark.parametrize("ready", [True, False])
def test_cli_routes_policy_arguments_and_preserves_refusal(
    context_module,
    monkeypatch,
    capsys,
    ready,
):
    calls = []
    monkeypatch.setattr(
        context_module,
        "resolve_runtime_profile_policy",
        lambda **args: (
            calls.append(args) or {"runtimeProfiles": {"status": "ready" if ready else "blocked"}}
        ),
    )
    result = context_module.main(
        [
            "runtime-profile-policy",
            "--legacy-root",
            "fixture-runtime",
            "--backend",
            "cpu",
            "--retained-backend",
            "cuda12",
            "--existing-installation",
        ]
    )
    assert result == (0 if ready else 1)
    assert calls[0]["backend"] == "cpu"
    assert calls[0]["retained_backend"] == "cuda12"
    assert calls[0]["existing_installation"] is True
    assert '"runtimeProfiles"' in capsys.readouterr().out
