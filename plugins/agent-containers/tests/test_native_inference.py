"""Nonsecret native inference is a separate restricted ACP capability."""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_containers import __main__ as cli
from agent_containers import config as config_mod
from agent_containers import resolver
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.inference import NativeInferenceConfig

pytestmark = pytest.mark.contract("agent_containers.native_inference")

PROFILE = NativeInferenceConfig(
    endpoint="http://inference-broker:8080",
    reference="openshell:resolve:env:EXAMPLE_INFERENCE",
)


def _fleet(**kwargs):
    return FleetConfig(
        image="example/agent:latest",
        security_profile="restricted",
        network="inference-only",
        acp_command="copilot --acp --stdio",
        native_inference=PROFILE,
        **kwargs,
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com", "http://127.0.0.1:8080", "http://localhost",
        "http://host.docker.internal", "http://user@broker", "http://broker/path",
        "http://broker?query=1", "http://broker#fragment", "http://broker:0",
        "http://broker:65536", "http://broker\n", "file://broker",
        "http://-broker", "http://" + "a" * 64, None,
    ],
)
def test_profile_rejects_non_broker_origins(endpoint):
    with pytest.raises(RuntimeError, match="native_inference.endpoint"):
        replace(PROFILE, endpoint=endpoint).validate()


@pytest.mark.parametrize(
    "reference",
    ["secret-value", "ghp_example", "openshell:resolve:env:", None,
     "openshell:resolve:env:NAME\n", "openshell:resolve:file:/credential"],
)
def test_profile_rejects_credentials_and_other_reference_protocols(reference):
    with pytest.raises(RuntimeError) as failure:
        replace(PROFILE, reference=reference).validate()
    if isinstance(reference, str) and reference:
        assert reference not in str(failure.value)


@pytest.mark.parametrize("raw", [None, [], {}, {"endpoint": "http://broker"},
                                {"endpoint": "http://broker", "reference": "x", "token": "x"}])
def test_profile_requires_exact_typed_mapping(raw):
    with pytest.raises(RuntimeError, match="exactly endpoint and reference"):
        NativeInferenceConfig.from_mapping(raw)


def test_restricted_configuration_and_fingerprint(monkeypatch, tmp_path):
    path = tmp_path / "containers.yaml"
    path.write_text(json.dumps({"fleets": {"example": {
        "image": "example/agent:latest", "security_profile": "restricted",
        "network": "inference-only", "acp_command": "copilot --acp --stdio",
        "native_inference": {"endpoint": PROFILE.endpoint, "reference": PROFILE.reference},
    }}}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "_config_path", lambda: path)
    config = config_mod.load_config(strict=True)
    fleet = config.fleets["example"]
    assert fleet.native_inference == PROFILE
    assert config.credentials_for(fleet) == (False, False)
    policy = fleet.security_policy_fingerprint("/workspace", "agent")
    assert policy != replace(fleet, native_inference=None).security_policy_fingerprint(
        "/workspace", "agent",
    )
    assert policy != replace(
        fleet, native_inference=replace(PROFILE, reference="openshell:resolve:env:OTHER"),
    ).security_policy_fingerprint("/workspace", "agent")
    assert policy != replace(
        fleet, native_inference=replace(PROFILE, endpoint="https://other-broker"),
    ).security_policy_fingerprint("/workspace", "agent")


@pytest.mark.parametrize("changes", [
    {"security_profile": "trusted"}, {"network": "none"},
    {"environment": {"COPILOT_API_URL": "http://other"}},
    {"environment": {"COPILOT_GITHUB_TOKEN": "secret-value"}},
])
def test_profile_rejects_wrong_posture_and_environment_overrides(changes):
    with pytest.raises(RuntimeError, match="native_inference"):
        replace(_fleet(), **changes).validate_restricted()


def test_generic_sensitive_environment_guard_is_unchanged():
    fleet = replace(_fleet(), native_inference=None,
                    environment={"COPILOT_GITHUB_TOKEN": PROFILE.reference})
    with pytest.raises(RuntimeError, match="credential-bearing"):
        fleet.validate_restricted()


def test_builder_projects_only_explicit_nonsecret_inputs():
    command = resolver.build_restricted_inference_spawn_command(
        "instance-id", "agent", "copilot --acp --stdio", PROFILE,
    )
    assert command == [
        "docker", "exec", "-i",
        "-e", f"COPILOT_API_URL={PROFILE.endpoint}",
        "-e", f"COPILOT_GITHUB_TOKEN={PROFILE.reference}",
        "-u", "agent", "instance-id", "bash", "-lc", "copilot --acp --stdio",
    ]
    with pytest.raises(RuntimeError):
        resolver.build_restricted_inference_spawn_command(
            "instance-id", "agent", "copilot", replace(PROFILE, reference="secret-value"),
        )


@pytest.mark.parametrize("stdio", [True, False])
def test_actual_acp_launch_uses_profile_without_host_auth(monkeypatch, stdio):
    fleet = _fleet()
    config = ContainersConfig(fleets={"example": fleet})
    monkeypatch.setattr(
        config, "credentials_for",
        lambda *_: pytest.fail("restricted launch must not resolve host credentials"),
    )
    monkeypatch.setattr(cli, "host_gh_token",
                        lambda: pytest.fail("host token must not be read"))
    monkeypatch.setattr(cli, "_require_live_relay_port",
                        lambda: pytest.fail("relay must not be reached"))
    seen = []
    monkeypatch.setattr(
        cli, "_exec_stdio", lambda command, env: seen.append(command) or 0,
    )
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda command, **kwargs: seen.append(command) or SimpleNamespace(returncode=0),
    )
    assert cli._launch_container_agent(
        Namespace(name="example-1", stdio=stdio), config, fleet, "restricted",
        "agent", fleet.acp_command, container_id="instance-id",
    ) == 0
    assert seen == [resolver.build_restricted_inference_spawn_command(
        "instance-id", "agent",
        cli.hosting_policy.guarded_launch(
            fleet.acp_command, fleet.workspace_folder or config.workspace_folder,
        ),
        PROFILE,
    )]


def test_namespace_spec_never_persists_inference_inputs(monkeypatch):
    fleet = _fleet()
    config = ContainersConfig(fleets={"example": fleet})
    monkeypatch.setattr(resolver, "load_config", lambda: config)
    monkeypatch.setattr(resolver, "get_container", lambda *_: SimpleNamespace(
        fleet="example", container_id="instance-id", security_profile="restricted",
        state="running", is_running=True,
    ))
    monkeypatch.setattr(resolver, "get_lease", lambda *_: None)
    monkeypatch.setattr(resolver, "deploy_hold_status",
                        lambda *_: {"state": "none", "operation": None, "reason": None})
    spec = asyncio.run(resolver.ContainerResolver().resolve_spec("example-1"))
    assert spec["spawn_command"][-3:] == ["exec", "--stdio", "example-1"]
    assert PROFILE.reference not in json.dumps(spec)
    assert PROFILE.endpoint not in json.dumps(spec)
    assert spec["venue"]["capabilities"]["host_credentials"] is False
    assert spec["venue"]["capabilities"]["credential_relay"] is False
