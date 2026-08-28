from __future__ import annotations

import json

import pytest

from agent_containers import __main__ as cli
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.installer_readiness import emit, evaluate, inspect_toolchain
from agent_containers.lifecycle import DockerContainerInfo


def _container() -> DockerContainerInfo:
    return DockerContainerInfo(
        name="example-1",
        container_id="container-id",
        image="example/image",
        state="running",
        status="Up",
        fleet="example",
    )


def test_valid_empty_configuration_is_explicit(capsys):
    result = evaluate(ContainersConfig(), [], [])

    assert result["state"] == "configuration-empty"
    assert "did not create a container or pull an image" in result["detail"]
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_configured_but_unprovisioned_fleet_is_explicit():
    config = ContainersConfig(fleets={"example": FleetConfig(image="example/image")})

    result = evaluate(config, [], [])

    assert result["state"] == "configuration-empty"
    assert "none is provisioned" in result["detail"]


def test_discovered_fleet_is_ready():
    config = ContainersConfig(fleets={"example": FleetConfig(image="example/image")})

    result = evaluate(config, [_container()], [])

    assert result["state"] == "ready"


def test_docker_or_configuration_failure_is_failed(capsys):
    result = evaluate(None, [], ["Docker daemon not reachable"])

    assert result["state"] == "failed"
    assert "Docker daemon not reachable" in result["detail"]
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_toolchain_matches_configured_backend():
    config = ContainersConfig(
        fleets={
            "trusted": FleetConfig(devcontainer_path="/source/example"),
            "restricted": FleetConfig(
                image="example/image",
                security_profile="restricted",
            ),
        }
    )

    findings = inspect_toolchain(
        config,
        command_finder=lambda command: "docker" if command == "docker" else None,
    )

    assert findings == (
        "devcontainer CLI is required by a configured devcontainer fleet",
        "OpenSSH is required by a configured trusted fleet",
    )


def test_payload_command_is_read_only(monkeypatch, capsys):
    config = ContainersConfig()
    monkeypatch.setattr(cli, "load_config", lambda **_kwargs: config)
    monkeypatch.setattr(
        "agent_containers.lifecycle.list_containers",
        lambda _config: [],
    )
    monkeypatch.setattr(
        "agent_containers.installer_readiness.inspect_toolchain",
        lambda _config: (),
    )
    monkeypatch.setattr(
        "agent_containers.fleet.reconcile_up",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("readiness must not create containers")
        ),
    )

    assert cli.main(["installer-readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"


def test_payload_command_rejects_malformed_config_before_docker_probe(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "containers.yaml"
    path.write_text("fleets: [", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    monkeypatch.setattr(
        "agent_containers.lifecycle.list_containers",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("invalid config must fail before probing Docker")
        ),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "Failed to read" in result["detail"]


def test_strict_shape_validation_does_not_change_normal_config_fallback(
    tmp_path, monkeypatch
):
    path = tmp_path / "containers.yaml"
    path.write_text("fleets: []\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))

    assert cli.load_config().fleets == {}


@pytest.mark.parametrize("content", ("[]\n", "false\n", "0\n", '""\n'))
def test_strict_config_rejects_every_falsy_non_mapping(
    content, tmp_path, monkeypatch
):
    path = tmp_path / "containers.yaml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))

    with pytest.raises(RuntimeError, match="top-level configuration must be a mapping"):
        cli.load_config(strict=True)


def test_strict_config_accepts_empty_yaml_document(tmp_path, monkeypatch):
    path = tmp_path / "containers.yaml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))

    assert cli.load_config(strict=True).fleets == {}


@pytest.mark.parametrize(
    ("value", "content"),
    (
        ("false", "fleets: false\n"),
        ("zero", "fleets: 0\n"),
        ("empty string", 'fleets: ""\n'),
        ("array", "fleets: []\n"),
    ),
)
def test_strict_config_rejects_every_falsy_non_mapping_fleets_value(
    value, content, tmp_path, monkeypatch
):
    path = tmp_path / "containers.yaml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))

    with pytest.raises(RuntimeError, match="fleets config must be a key/value mapping"):
        cli.load_config(strict=True)


@pytest.mark.parametrize("content", ("{}\n", "fleets: null\n"))
def test_strict_config_treats_missing_or_null_fleets_as_empty(
    content, tmp_path, monkeypatch
):
    path = tmp_path / "containers.yaml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))

    assert cli.load_config(strict=True).fleets == {}
