"""Exact daemon/birth identity and absence over actual fake CLI processes."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from docker_resource_producer import DiagnosticObservation, DockerClient, Endpoint, Identity, VerificationError, birth_labels

TOKEN = "r" * 43
CREATOR = "machine/project/creator"
CONTAINER = "a" * 64
CREATED = "2026-10-13T00:00:00.123456789Z"


@pytest.fixture
def fixture(tmp_path: Path) -> tuple[DockerClient, Path, dict]:
    path = tmp_path / "fake state.json"
    labels = birth_labels(TOKEN, CREATOR, 1)
    state = {
        "daemon_id": "fixture-daemon",
        "container": {
            CONTAINER: {
                "Id": CONTAINER, "Created": CREATED, "Name": "/renamable",
                "Config": {"Labels": dict(labels)}, "State": {"Status": "running"},
            },
        },
        "volume": {
            "unique-volume": {"Name": "unique-volume", "CreatedAt": CREATED, "Labels": dict(labels)},
        },
        "context": {
            "Name": "fixture", "Endpoints": {"docker": {"Host": "unix:///fixture.sock", "SkipTLSVerify": False}},
            "TLSMaterial": {},
        },
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    client = DockerClient(
        Endpoint("unix:///fixture.sock", "fixture-daemon"),
        docker_argv=[sys.executable, str(Path(__file__).with_name("fake_docker.py"))],
        environment={**os.environ, "FAKE_DOCKER_STATE": str(path), "DOCKER_CONTEXT": "wrong-context",
                     "DOCKER_HOST": "tcp://wrong-host:2375", "DOCKER_TLS_VERIFY": "1"},
    )
    return client, path, state


def identity(kind: str = "container") -> Identity:
    return Identity(kind, CONTAINER if kind == "container" else "unique-volume",
                    CREATED, "fixture-daemon", TOKEN, CREATOR, 1)


def logs(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.with_suffix(".log").read_text().splitlines()]


def test_diagnostic_snapshot_cannot_authorize_proof_or_removal(fixture: tuple) -> None:
    client, path, _state = fixture
    kind = "container"
    observation = client.diagnostic_observation(identity(kind))
    assert observation.observed_exists is True
    assert observation.to_json()["authority"] == "diagnostic-only"
    assert "exists" not in observation.to_json() and "producer_verification" not in observation.to_json()
    with pytest.raises(VerificationError, match="cannot authorize"):
        bool(observation)
    before = len(logs(path))
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.probe(identity(kind))
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.remove(identity(kind), authorized=True)
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.find_reservation(TOKEN, CREATOR)
    assert len(logs(path)) == before
    calls = logs(path)
    removes = [c["args"] for c in calls if "rm" in c["args"]]
    assert removes == []
    for call in calls:
        assert call["args"][:2] == ["--host", "unix:///fixture.sock"]
        assert not any(arg.startswith("--tlsverify") for arg in call["args"])
        assert set(call["routing"].values()) == {None}


@pytest.mark.parametrize("kind", ["container", "volume"])
def test_authoritative_probe_refuses_before_any_transport(fixture: tuple, kind: str) -> None:
    client, path, _state = fixture
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.probe(identity(kind))
    assert not path.with_suffix(".log").exists()


@pytest.mark.parametrize("observed", [False, True])
def test_diagnostic_coercion_is_not_presence_or_absence_authority(observed: bool) -> None:
    observation = DiagnosticObservation(identity(), observed)
    with pytest.raises(VerificationError, match="cannot authorize"):
        bool(observation)
    assert set(observation.to_json()) == {"authority", "observed_exists", "identity"}


@pytest.mark.parametrize("state_name", ["running", "exited", "created"])
def test_container_rename_and_stopped_state_preserve_identity(fixture: tuple, state_name: str) -> None:
    client, path, state = fixture
    state["container"][CONTAINER]["Name"] = "/renamed"
    state["container"][CONTAINER]["State"]["Status"] = state_name
    path.write_text(json.dumps(state), encoding="utf-8")
    assert client.diagnostic_observation(identity()).observed_exists is True


@pytest.mark.parametrize("change", ["birth", "creator", "token", "generation", "daemon", "inspect-id"])
def test_changed_birth_identity_never_removes(fixture: tuple, change: str) -> None:
    client, path, state = fixture
    resource = state["volume"]["unique-volume"]
    if change == "birth":
        resource["CreatedAt"] = "2026-10-14T00:00:00Z"
    elif change == "daemon":
        state["daemon_id"] = "replacement-daemon"
    elif change == "inspect-id":
        resource["Name"] = "other-volume"
    else:
        resource["Labels"]["copilot-extensions.disposable." + (
            "reservation" if change == "token" else change
        )] = {"creator": "other/owner/id", "token": "x" * 43, "generation": "2"}[change]
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError):
        client.diagnostic_observation(identity("volume"))
    assert all("rm" not in call["args"] for call in logs(path))


@pytest.mark.parametrize("kind", ["container", "volume"])
@pytest.mark.parametrize("failure", ["ls", "inspect"])
def test_permission_or_backend_failure_is_never_absence(fixture: tuple, kind: str, failure: str) -> None:
    client, path, state = fixture
    state["fail"] = f"{kind} {failure}"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError, match="exit 7"):
        client.diagnostic_observation(identity(kind))


@pytest.mark.parametrize("operation", ["info", "container ls", "container inspect", "volume ls", "volume inspect"])
def test_malformed_response_keeps_identity_unknown(fixture: tuple, operation: str) -> None:
    client, path, state = fixture
    state["malformed"] = operation
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError):
        client.diagnostic_observation(identity("volume" if operation.startswith("volume") else "container"))


def test_absence_needs_same_daemon_after_inventory(fixture: tuple) -> None:
    client, path, state = fixture
    state["container"] = {}
    state["change_daemon_after"] = "container ls"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError, match="daemon identity changed"):
        client.diagnostic_observation(identity())


def test_daemon_aba_routing_cannot_prove_absence(fixture: tuple) -> None:
    client, path, state = fixture
    state["inventory_on_other_daemon"] = {
        "daemon_id": "another-daemon", "container": {}, "volume": {},
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    observation = client.diagnostic_observation(identity())
    assert observation.observed_exists is False
    assert CONTAINER in json.loads(path.read_text())["container"]
    assert observation.to_json()["authority"] == "diagnostic-only"
    before = len(logs(path))
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.probe(identity())
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.remove(identity(), authorized=True)
    assert len(logs(path)) == before


def test_reservation_search_covers_all_resource_types_and_attempts(fixture: tuple) -> None:
    client, path, state = fixture
    state["volume"]["unique-volume"]["Labels"]["copilot-extensions.disposable.generation"] = "2"
    path.write_text(json.dumps(state), encoding="utf-8")
    observations = client.diagnostic_reservations(TOKEN, CREATOR)
    assert [o.identity for o in observations] == [identity(), replace(identity("volume"), generation=2)]
    assert all(o.to_json()["authority"] == "diagnostic-only" for o in observations)


def test_cleanup_requires_explicit_authority_before_any_probe(fixture: tuple) -> None:
    client, path, _state = fixture
    with pytest.raises(VerificationError, match="not authorized"):
        client.remove(identity())
    assert not path.with_suffix(".log").exists()


def test_retained_resource_does_not_receive_a_cleanup_success(fixture: tuple) -> None:
    client, path, state = fixture
    state["retain"] = True
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.remove(identity(), authorized=True)
    assert CONTAINER in json.loads(path.read_text())["container"]
    assert not path.with_suffix(".log").exists()


def test_pin_resolves_context_once_then_ignores_ambient_host(fixture: tuple) -> None:
    client, path, _state = fixture
    env = {**client.environment, "DOCKER_CONTEXT": "fixture"}
    env.pop("DOCKER_HOST")
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint == Endpoint("unix:///fixture.sock", "fixture-daemon", context="fixture")
    assert pinned.diagnostic_observation(identity()).observed_exists is True
    assert logs(path)[0]["args"] == ["context", "inspect", "--", "fixture"]
    assert all(c["args"][0] == "--host" for c in logs(path)[1:])


def test_explicit_host_environment_wins_over_context_environment(fixture: tuple) -> None:
    client, path, _state = fixture
    env = {**client.environment, "DOCKER_CONTEXT": "fixture", "DOCKER_TLS": "", "DOCKER_TLS_VERIFY": ""}
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.host == env["DOCKER_HOST"]
    assert pinned.endpoint.context == "default" and pinned.endpoint.tls is False
    assert pinned.diagnostic_observation(identity()).observed_exists is True
    assert logs(path)[0]["args"] == ["context", "inspect", "--", "default"]
    assert all(call["args"][:2] == ["--host", env["DOCKER_HOST"]] for call in logs(path)[1:])


@pytest.mark.parametrize("configured,resolved", [
    ("localhost:5555", "tcp://localhost:5555"),
    ("unix://", "unix:///var/run/docker.sock"),
])
def test_host_shorthand_uses_cli_resolved_default_endpoint(fixture: tuple, configured: str, resolved: str) -> None:
    client, path, state = fixture
    state["default_context"] = {
        "Name": "default", "Endpoints": {"docker": {"Host": resolved, "SkipTLSVerify": False}}, "TLSMaterial": {},
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    env = {**client.environment, "DOCKER_HOST": configured, "DOCKER_TLS": "", "DOCKER_TLS_VERIFY": ""}
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.host == resolved
    assert pinned.diagnostic_observation(identity()).observed_exists is True
    assert logs(path)[0]["args"] == ["context", "inspect", "--", "default"]
    assert all(call["args"][:2] == ["--host", resolved] for call in logs(path)[1:])


def test_socket_context_does_not_replay_unused_tls_material(fixture: tuple) -> None:
    client, path, state = fixture
    state["context"]["Endpoints"]["docker"]["SkipTLSVerify"] = True
    state["context"]["TLSMaterial"] = {"docker": ["ca.pem"]}
    path.write_text(json.dumps(state), encoding="utf-8")
    env = {key: value for key, value in client.environment.items() if key != "DOCKER_HOST"}
    env["DOCKER_CONTEXT"] = "fixture"
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.tls is False and pinned.endpoint.tls_ca is None
    assert pinned.diagnostic_observation(identity()).observed_exists is True


@pytest.mark.parametrize("verify", [True, False])
def test_default_context_in_memory_tls_uses_default_files(fixture: tuple, verify: bool) -> None:
    client, path, state = fixture
    certs = path.parent / "default context certificates"
    certs.mkdir()
    (certs / "ca.pem").write_text("fixture CA", encoding="utf-8")
    state["context"] = {
        "Name": "default",
        "Endpoints": {"docker": {"Host": "tcp://example.invalid:2376", "SkipTLSVerify": not verify}},
        "TLSMaterial": {"docker": ["ca.pem"]},
        "Storage": {"MetadataPath": "<IN MEMORY>", "TLSPath": "<IN MEMORY>"},
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    env = {**client.environment, "DOCKER_CERT_PATH": str(certs),
           "DOCKER_TLS": "1", "DOCKER_TLS_VERIFY": "1" if verify else ""}
    env.pop("DOCKER_HOST")
    env.pop("DOCKER_CONTEXT")
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.context == "default" and pinned.endpoint.tls_verify is verify
    assert pinned.endpoint.tls_ca == str(certs / "ca.pem")
    assert pinned.diagnostic_observation(identity()).observed_exists is True
    for call in logs(path)[2:]:
        assert "--tls=true" in call["args"]
        assert call["tls_files"] == {"ca.pem": str(certs / "ca.pem"), "cert.pem": "", "key.pem": ""}


@pytest.mark.parametrize("context", [None, "default"])
def test_tls_home_fallback_uses_supplied_environment_not_ambient(fixture: tuple, monkeypatch, context: str | None) -> None:
    client, path, state = fixture
    isolated = path.parent / "isolated home"
    ambient = path.parent / "ambient home"
    for home in (isolated, ambient):
        certs = home / ".docker"
        certs.mkdir(parents=True)
        (certs / "ca.pem").write_text("fixture CA", encoding="utf-8")
    for name in ("cert.pem", "key.pem"):
        (ambient / ".docker" / name).write_text("unrelated fixture identity", encoding="utf-8")
    state["context"] = {
        "Name": "default", "Endpoints": {"docker": {"Host": "tcp://example.invalid:2376", "SkipTLSVerify": False}},
        "TLSMaterial": {"docker": ["ca.pem"]}, "Storage": {"TLSPath": "<IN MEMORY>"},
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    env = {**client.environment, "HOME": str(isolated), "USERPROFILE": str(isolated),
           "DOCKER_HOST": "tcp://example.invalid:2376", "DOCKER_TLS_VERIFY": "1"}
    for name in ("DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_CONTEXT"):
        env.pop(name, None)
    if context:
        env.pop("DOCKER_HOST")
        env["DOCKER_CONTEXT"] = context
    monkeypatch.setenv("HOME", str(ambient))
    monkeypatch.setenv("USERPROFILE", str(ambient))
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.tls_ca == str(isolated / ".docker" / "ca.pem")
    assert pinned.endpoint.tls_cert is None and pinned.endpoint.tls_key is None
    assert pinned.diagnostic_observation(identity()).observed_exists is True
    for call in logs(path):
        if call["args"][0] == "--host":
            assert call["tls_files"] == {"ca.pem": str(isolated / ".docker" / "ca.pem"), "cert.pem": "", "key.pem": ""}


def test_tls_missing_supplied_home_refuses_ambient_fallback(fixture: tuple) -> None:
    client, path, _state = fixture
    env = {key: value for key, value in client.environment.items() if key not in {
        "HOME", "USERPROFILE", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_CONTEXT",
    }}
    with pytest.raises(VerificationError, match="requires supplied"):
        DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert not path.with_suffix(".log").exists()


@pytest.mark.parametrize("directory_key", ["DOCKER_CERT_PATH", "DOCKER_CONFIG"])
def test_explicit_tls_directory_does_not_expand_parent_home(fixture: tuple, monkeypatch, directory_key: str) -> None:
    client, path, _state = fixture
    monkeypatch.chdir(path.parent)
    certs = path.parent / "~" / "literal certificates"
    certs.mkdir(parents=True)
    (certs / "ca.pem").write_text("fixture CA", encoding="utf-8")
    env = {**client.environment, directory_key: "~/literal certificates"}
    env.pop("DOCKER_CONTEXT")
    if directory_key == "DOCKER_CONFIG":
        env.pop("DOCKER_CERT_PATH", None)
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.tls_ca == str(certs / "ca.pem")


@pytest.mark.parametrize("field,value", [
    ("resource_id", "short-id"), ("generation", True), ("generation", 0),
    ("created", "2026-99-99T00:00:00Z"), ("token", "short"),
    ("daemon_id", " "), ("resource_type", []),
])
def test_invalid_identity_rejected_before_transport(field: str, value: object) -> None:
    payload = identity().to_json()
    payload[field] = value
    with pytest.raises(VerificationError):
        Identity.from_json(payload)


def test_identity_and_endpoint_round_trip_strictly(tmp_path: Path) -> None:
    assert Identity.from_json(identity().to_json()) == identity()
    endpoint = Endpoint("tcp://example.invalid:2376", "fixture", tls=True, tls_verify=True,
                        tls_ca=str(tmp_path / "ca.pem"))
    assert Endpoint.from_json(endpoint.to_json()) == endpoint
    with pytest.raises(VerificationError):
        Endpoint.from_json({**endpoint.to_json(), "extra": "refused"})


def test_volume_cleanup_refuses_before_probe_or_name_based_mutation(fixture: tuple) -> None:
    client, path, _state = fixture
    with pytest.raises(VerificationError, match="birth-conditional"):
        client.remove(identity("volume"), authorized=True)
    assert not path.with_suffix(".log").exists()


def test_reused_container_name_never_selects_the_replacement(fixture: tuple) -> None:
    client, path, state = fixture
    replacement_id = "b" * 64
    replacement = state["container"].pop(CONTAINER)
    replacement["Id"] = replacement_id
    state["container"][replacement_id] = replacement
    path.write_text(json.dumps(state), encoding="utf-8")
    assert client.diagnostic_observation(identity()).observed_exists is False
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.remove(identity(), authorized=True)
    assert replacement_id in json.loads(path.read_text())["container"]
    assert all("rm" not in call["args"] for call in logs(path))


def test_container_cleanup_refuses_changed_creator_labels(fixture: tuple) -> None:
    client, path, state = fixture
    state["container"][CONTAINER]["Config"]["Labels"]["copilot-extensions.disposable.creator"] = "another/owner/id"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError, match="ownership labels"):
        client.diagnostic_observation(identity())
    with pytest.raises(VerificationError, match="daemon-bound"):
        client.remove(identity(), authorized=True)
    assert all("rm" not in call["args"] for call in logs(path))


@pytest.mark.parametrize("verify", [True, False])
def test_named_context_tls_paths_and_verification_are_pinned(fixture: tuple, verify: bool) -> None:
    client, path, state = fixture
    root = path.parent / "tls material"
    state["context"]["Endpoints"]["docker"] = {
        "Host": "tcp://example.invalid:2376", "SkipTLSVerify": not verify,
    }
    state["context"]["TLSMaterial"] = {"docker": ["ca.pem", "cert.pem", "key.pem"]}
    state["context"]["Storage"] = {"TLSPath": str(root)}
    path.write_text(json.dumps(state), encoding="utf-8")
    pinned = DockerClient.pin(
        docker_argv=client.docker_argv,
        environment={**{key: value for key, value in client.environment.items() if key != "DOCKER_HOST"},
                     "DOCKER_CONTEXT": "fixture"},
    )
    assert pinned.endpoint.tls is True
    assert pinned.endpoint.tls_verify is verify
    assert pinned.endpoint.tls_ca == str(root / "docker" / "ca.pem")
    for call in logs(path)[1:]:
        assert "--tls=true" in call["args"]
        assert f"--tlsverify={'true' if verify else 'false'}" in call["args"]
        assert call["args"][call["args"].index("--tlskey") + 1] == str(root / "docker" / "key.pem")


@pytest.mark.parametrize("fraction", ["", ".1", ".123", ".12345", ".123456", ".123456789"])
def test_valid_timestamp_fraction_preserves_original_identity(fraction: str) -> None:
    created = "2026-10-13T00:00:00" + fraction + "Z"
    value = replace(identity(), created=created)
    assert value.created == created
    assert value.to_json()["created"] == created


def test_explicit_host_ca_only_tls_omits_absent_client_identity(fixture: tuple) -> None:
    client, path, _state = fixture
    certs = path.parent / "CA only material"
    certs.mkdir()
    (certs / "ca.pem").write_text("fixture CA", encoding="utf-8")
    ambient = path.parent / "unrelated ambient material"
    ambient.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (ambient / name).write_text("unrelated fixture material", encoding="utf-8")
    env = {**client.environment, "DOCKER_HOST": "tcp://example.invalid:2376",
           "DOCKER_CERT_PATH": str(certs), "DOCKER_CONFIG": str(ambient), "DOCKER_TLS_VERIFY": "1"}
    env.pop("DOCKER_CONTEXT", None)
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.tls_ca == str(certs / "ca.pem")
    assert pinned.endpoint.tls_cert is None and pinned.endpoint.tls_key is None
    assert pinned.diagnostic_observation(identity()).observed_exists is True
    for call in logs(path):
        if call["args"][0] != "--host":
            continue
        assert "--tlsverify=true" in call["args"]
        assert call["tls_files"] == {"ca.pem": str(certs / "ca.pem"), "cert.pem": "", "key.pem": ""}


@pytest.mark.parametrize("material,skip_verify", [([], True), (["cert.pem", "key.pem"], False)])
def test_context_without_ca_preserves_tls_and_system_roots(fixture: tuple, material: list[str], skip_verify: bool) -> None:
    client, path, state = fixture
    state["context"]["Endpoints"]["docker"] = {
        "Host": "tcp://example.invalid:2376", "SkipTLSVerify": skip_verify,
    }
    state["context"]["TLSMaterial"] = {"docker": material}
    root = path.parent / "context material"
    state["context"]["Storage"] = {"TLSPath": str(root)}
    ambient = path.parent / "unrelated ambient material"
    ambient.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (ambient / name).write_text("unrelated fixture material", encoding="utf-8")
    path.write_text(json.dumps(state), encoding="utf-8")
    pinned = DockerClient.pin(
        docker_argv=client.docker_argv,
        environment={**{key: value for key, value in client.environment.items() if key != "DOCKER_HOST"},
                     "DOCKER_CONTEXT": "fixture", "DOCKER_CONFIG": str(ambient)},
    )
    assert pinned.endpoint.tls is True and pinned.endpoint.tls_verify is not skip_verify
    assert pinned.endpoint.tls_ca is None
    assert pinned.diagnostic_observation(identity()).observed_exists is True
    for call in logs(path)[1:]:
        assert "--tls=true" in call["args"]
        assert f"--tlsverify={'false' if skip_verify else 'true'}" in call["args"]
        assert call["tls_files"] == {
            name: str(root / "docker" / name) if name in material else ""
            for name in ("ca.pem", "cert.pem", "key.pem")
        }


def test_explicit_host_client_identity_files_are_retained(fixture: tuple) -> None:
    client, path, _state = fixture
    certs = path.parent / "client material"
    certs.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (certs / name).write_text("fixture material", encoding="utf-8")
    env = {**client.environment, "DOCKER_HOST": "tcp://example.invalid:2376",
           "DOCKER_CERT_PATH": str(certs), "DOCKER_TLS_VERIFY": "1"}
    env.pop("DOCKER_CONTEXT", None)
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.tls_cert == str(certs / "cert.pem")
    assert pinned.endpoint.tls_key == str(certs / "key.pem")
    assert "--tlscert" in logs(path)[1]["args"] and "--tlskey" in logs(path)[1]["args"]


def test_inaccessible_optional_client_identity_is_not_ca_only_fallback(monkeypatch, tmp_path: Path) -> None:
    from docker_resource_producer.client import _optional_client_file

    def denied(_path: Path, *args, **kwargs):
        raise PermissionError("fixture permission denial")

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(VerificationError, match="Cannot inspect"):
        _optional_client_file(tmp_path / "key.pem")


def test_partial_client_identity_refuses_before_docker_invocation(fixture: tuple) -> None:
    client, path, _state = fixture
    certs = path.parent / "partial client material"
    certs.mkdir()
    (certs / "cert.pem").write_text("fixture certificate", encoding="utf-8")
    env = {**client.environment, "DOCKER_HOST": "tcp://example.invalid:2376",
           "DOCKER_CERT_PATH": str(certs), "DOCKER_TLS_VERIFY": "1"}
    env.pop("DOCKER_CONTEXT", None)
    with pytest.raises(VerificationError, match="must be paired"):
        DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert not path.with_suffix(".log").exists()


@pytest.mark.parametrize("restore", [False, True])
def test_verified_tls_without_material_refuses_before_invocation(fixture: tuple, restore: bool) -> None:
    client, path, _state = fixture
    endpoint = Endpoint("tcp://example.invalid:2376", "fixture-daemon", tls=True)
    with pytest.raises(VerificationError, match="requires pinned CA or client TLS material"):
        if restore:
            value = endpoint.to_json()
            value["tls_verify"] = True
            endpoint = Endpoint.from_json(value)
        else:
            endpoint = replace(endpoint, tls_verify=True)
        DockerClient(endpoint, docker_argv=client.docker_argv,
                     environment=client.environment).diagnostic_observation(identity())
    assert not path.with_suffix(".log").exists()
