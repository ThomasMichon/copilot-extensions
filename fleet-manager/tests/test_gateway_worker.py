from __future__ import annotations

import json
import threading
import time

import pytest
from fleet_manager.gateway_client import CredentialMaterial
from fleet_manager.gateway_contract import GatewayError
from fleet_manager.gateway_server import build_server
from fleet_manager.gateway_worker import CredentialWorker, token_provider
from gateway_fixtures import Fixtures, credential_request, public
from test_gateway_runtime import controller, running


def _worker(fixture, client) -> CredentialWorker:
    return CredentialWorker(
        client, service="index", installation="index-install", generation=1,
        recipient_key=fixture.recipient, controller="controller", controller_generation=1,
        controller_keys={"keys": [public(fixture.proofs["controller"], "proof")]},
    )


def test_real_worker_renews_source_credentials_and_fails_after_disconnect(tmp_path, capsys):
    fixture = Fixtures(tmp_path)
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        responder = controller(fixture, fixture.client("controller", gateway.server_port))
        responder.start()
        stop = threading.Event()
        failures = []

        def respond():
            try:
                while not stop.is_set():
                    responder.step()
                    stop.wait(0.05)
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            worker = _worker(fixture, fixture.client("worker", gateway.server_port))
            parameters = credential_request()["parameters"]
            first = worker.acquire(parameters)
            second = worker.acquire(parameters)
            assert first.token == second.token == "SYNTHETIC-SECRET-NEVER-PRODUCTION"
            assert first.expires_at > time.time()
            assert "SYNTHETIC" not in repr(first)
        finally:
            stop.set()
            thread.join(5)
            responder.close()
        assert not thread.is_alive() and failures == []
        with pytest.raises(GatewayError, match="controller-unavailable"):
            worker.acquire(parameters)
    captured = capsys.readouterr()
    assert "SYNTHETIC-SECRET" not in captured.out + captured.err


def test_worker_never_retries_terminal_uncertainty(tmp_path):
    fixture = Fixtures(tmp_path)

    class Client:
        principal = "worker"
        epoch = "epoch"
        calls = 0

        def request_credential(self, value, *, deadline):
            from fleet_manager.planning import digest
            return {
                "state": "PENDING", "request_id": value["request_id"],
                "request_digest": digest(value), "expires_at": int(time.time()) + 10,
            }

        def credential_result(self, request_id, request_digest, *, deadline):
            self.calls += 1
            return {"state": "UNKNOWN"}

    client = Client()
    worker = _worker(fixture, client)
    with pytest.raises(GatewayError, match="credential-unavailable"):
        worker.acquire(credential_request()["parameters"])
    assert client.calls == 1


def test_expired_connect_never_submits_provider_work(tmp_path, monkeypatch):
    fixture = Fixtures(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("fleet_manager.gateway_worker.time.monotonic", lambda: clock[0])

    class Client:
        principal = "worker"
        epoch = None

        def connect(self, *, deadline):
            assert deadline == 101.0
            clock[0] = 102.0
            self.epoch = "epoch"

        def request_credential(self, value, *, deadline):
            pytest.fail("provider work submitted after acquisition deadline")

    with pytest.raises(GatewayError, match="credential-unavailable"):
        _worker(fixture, Client()).acquire(credential_request()["parameters"], timeout=1)


@pytest.mark.parametrize("phase", ["challenge", "session", "result", "ack"])
def test_real_acquisition_deadline_covers_connect_poll_and_ack(tmp_path, phase):
    fixture = Fixtures(tmp_path)
    route = {
        "challenge": "/v1/challenge", "session": "/v1/session",
        "result": "/v1/credentials/result", "ack": "/v1/credentials/ack",
    }[phase]
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        responder = controller(fixture, fixture.client("controller", gateway.server_port))
        responder.start()
        dispatched = []
        original = gateway.gateway.dispatch

        def slow_dispatch(path, message, **kwargs):
            dispatched.append(path)
            if path == route:
                time.sleep(1.5)
            return original(path, message, **kwargs)

        gateway.gateway.dispatch = slow_dispatch
        stop = threading.Event()
        failures = []

        def respond():
            try:
                while not stop.is_set():
                    if responder.step() == "READY":
                        return
                    stop.wait(0.15)
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            worker = _worker(fixture, fixture.client("worker", gateway.server_port))
            started = time.monotonic()
            with pytest.raises(GatewayError, match="ack-uncertain|transport-unavailable"):
                worker.acquire(credential_request()["parameters"], timeout=0.7)
            assert time.monotonic() - started < 1.4
            if phase in ("challenge", "session"):
                assert "/v1/credentials/request" not in dispatched
            assert dispatched.count(route) >= 1
        finally:
            stop.set()
            thread.join(3)
            responder.close()
        assert not thread.is_alive() and failures == []


def test_policy_loader_reads_rotated_access_reference_and_exact_binding(tmp_path, monkeypatch):
    fixture = Fixtures(tmp_path)
    access = tmp_path / "access.jwt"
    access.write_text("first-issued-token", encoding="utf-8")
    recipient = tmp_path / "recipient.pem"
    recipient.write_bytes(fixture.recipient.export_to_pem(private_key=True, password=None))
    discovery = tmp_path / "discovery.json"
    discovery.write_text(json.dumps({
        "schema": "fleet-manager.gateway-endpoint", "version": 1,
        "endpoint": "https://127.0.0.1:1", "ca": str(tmp_path / "tls.pem"),
        "issuer": "fixture-issuer", "audience": "gateway-a", "gateway": "gateway-install",
        "jwks": fixture.gateway_keys, "remote": False,
    }), encoding="utf-8")
    policy = tmp_path / "worker.json"
    value = {
        "schema": "fleet-manager.credential-worker", "version": 1,
        "discovery": str(discovery), "principal": "worker", "service": "index",
        "installation": "index-install", "generation": 1,
        "proof_key": str(tmp_path / "identity-worker.pem"), "access_token_file": str(access),
        "recipient_key": str(recipient), "controller": "controller", "controller_generation": 1,
        "controller_keys": {"keys": [public(fixture.proofs["controller"], "proof")]},
        "bindings": [credential_request()["parameters"]],
    }
    policy.write_text(json.dumps(value), encoding="utf-8")
    consumed = []

    def acquire(self, parameters, **kwargs):
        consumed.append(self.client._access_token())
        assert parameters == value["bindings"][0]
        return CredentialMaterial(
            "source-token", int(time.time()) + 15, "source-a", "account-a", "ingestion-a", ("read",),
        )

    monkeypatch.setattr(CredentialWorker, "acquire", acquire)
    provider = token_provider(policy, source="source-a", account="account-a")
    assert provider() == "source-token"
    access.write_text("second-issued-token", encoding="utf-8")
    assert provider() == "source-token"
    assert consumed == ["first-issued-token", "second-issued-token"]
    with pytest.raises(GatewayError, match="provider-policy-denied"):
        token_provider(policy, source="source-a", account="other-account")
    value["bindings"].append(value["bindings"][0])
    policy.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(GatewayError, match="ambiguous-provider-policy"):
        token_provider(policy, source="source-a", account="account-a")
