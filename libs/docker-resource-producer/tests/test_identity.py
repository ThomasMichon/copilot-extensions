"""Exact daemon/birth identity and absence over actual fake CLI processes."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from docker_resource_producer import DockerClient, Endpoint, Identity, VerificationError, birth_labels

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


def test_probe_and_authorized_exact_removal(fixture: tuple) -> None:
    client, path, _state = fixture
    kind = "container"
    assert client.probe(identity(kind)) is True
    assert client.remove(identity(kind), authorized=True) is True
    assert client.probe(identity(kind)) is False
    assert client.remove(identity(kind), authorized=True) is False
    calls = logs(path)
    removes = [c["args"] for c in calls if "rm" in c["args"]]
    assert len(removes) == 1 and removes[0][-2:] == ["--", identity(kind).resource_id]
    for call in calls:
        assert call["args"][:2] == ["--host", "unix:///fixture.sock"]
        assert not any(arg.startswith("--tlsverify") for arg in call["args"])
        assert set(call["routing"].values()) == {None}


@pytest.mark.parametrize("state_name", ["running", "exited", "created"])
def test_container_rename_and_stopped_state_preserve_identity(fixture: tuple, state_name: str) -> None:
    client, path, state = fixture
    state["container"][CONTAINER]["Name"] = "/renamed"
    state["container"][CONTAINER]["State"]["Status"] = state_name
    path.write_text(json.dumps(state), encoding="utf-8")
    assert client.probe(identity()) is True


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
        client.probe(identity("volume"))
    assert all("rm" not in call["args"] for call in logs(path))


@pytest.mark.parametrize("kind", ["container", "volume"])
@pytest.mark.parametrize("failure", ["ls", "inspect", "rm"])
def test_permission_or_backend_failure_is_never_absence(fixture: tuple, kind: str, failure: str) -> None:
    client, path, state = fixture
    state["fail"] = f"{kind} {failure}"
    path.write_text(json.dumps(state), encoding="utf-8")
    if kind == "volume" and failure == "rm":
        with pytest.raises(VerificationError, match="birth-conditional"):
            client.remove(identity(kind), authorized=True)
    else:
        with pytest.raises(VerificationError, match="exit 7"):
            if failure == "rm":
                client.remove(identity(kind), authorized=True)
            else:
                client.probe(identity(kind))


@pytest.mark.parametrize("operation", ["info", "container ls", "container inspect", "volume ls", "volume inspect"])
def test_malformed_response_keeps_identity_unknown(fixture: tuple, operation: str) -> None:
    client, path, state = fixture
    state["malformed"] = operation
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError):
        client.probe(identity("volume" if operation.startswith("volume") else "container"))


def test_absence_needs_same_daemon_after_inventory(fixture: tuple) -> None:
    client, path, state = fixture
    state["container"] = {}
    state["change_daemon_after"] = "container ls"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError, match="daemon identity changed"):
        client.probe(identity())


def test_reservation_search_covers_all_resource_types_and_attempts(fixture: tuple) -> None:
    client, path, state = fixture
    state["volume"]["unique-volume"]["Labels"]["copilot-extensions.disposable.generation"] = "2"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert client.find_reservation(TOKEN, CREATOR) == [identity(), replace(identity("volume"), generation=2)]


def test_cleanup_requires_explicit_authority_before_any_probe(fixture: tuple) -> None:
    client, path, _state = fixture
    with pytest.raises(VerificationError, match="not authorized"):
        client.remove(identity())
    assert not path.with_suffix(".log").exists()


def test_success_shaped_remove_without_absence_fails(fixture: tuple) -> None:
    client, path, state = fixture
    state["retain"] = True
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError, match="did not establish"):
        client.remove(identity(), authorized=True)


def test_pin_resolves_context_once_then_ignores_ambient_host(fixture: tuple) -> None:
    client, path, _state = fixture
    env = {**client.environment, "DOCKER_CONTEXT": "fixture"}
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint == Endpoint("unix:///fixture.sock", "fixture-daemon", context="fixture")
    assert pinned.probe(identity()) is True
    assert logs(path)[0]["args"] == ["context", "inspect", "--", "fixture"]
    assert all(c["args"][0] == "--host" for c in logs(path)[1:])


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


def test_identity_and_endpoint_round_trip_strictly() -> None:
    assert Identity.from_json(identity().to_json()) == identity()
    endpoint = Endpoint("tcp://example.invalid:2376", "fixture", tls=True, tls_verify=True)
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
    assert client.remove(identity(), authorized=True) is False
    assert replacement_id in json.loads(path.read_text())["container"]
    assert all("rm" not in call["args"] for call in logs(path))


def test_container_cleanup_refuses_changed_creator_labels(fixture: tuple) -> None:
    client, path, state = fixture
    state["container"][CONTAINER]["Config"]["Labels"]["copilot-extensions.disposable.creator"] = "another/owner/id"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(VerificationError, match="ownership labels"):
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
        docker_argv=client.docker_argv, environment={**client.environment, "DOCKER_CONTEXT": "fixture"},
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
    env = {**client.environment, "DOCKER_HOST": "tcp://example.invalid:2376",
           "DOCKER_CERT_PATH": str(certs), "DOCKER_TLS_VERIFY": "1"}
    env.pop("DOCKER_CONTEXT", None)
    pinned = DockerClient.pin(docker_argv=client.docker_argv, environment=env)
    assert pinned.endpoint.tls_ca == str(certs / "ca.pem")
    assert pinned.endpoint.tls_cert is None and pinned.endpoint.tls_key is None
    assert pinned.probe(identity()) is True
    for call in logs(path):
        assert "--tlsverify=true" in call["args"]
        assert "--tlscert" not in call["args"] and "--tlskey" not in call["args"]


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
    assert "--tlscert" in logs(path)[0]["args"] and "--tlskey" in logs(path)[0]["args"]


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
