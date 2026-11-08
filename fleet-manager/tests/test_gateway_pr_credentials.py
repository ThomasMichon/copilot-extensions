"""PR-scoped credentials through the real TLS/PoP/broker/controller/recipient path."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fleet_manager.gateway_auth import Signer, verify
from fleet_manager.gateway_client import CredentialMaterial
from fleet_manager.gateway_contract import ForgeScope, GatewayConfig, GatewayError, credential, encode, parse
from fleet_manager.gateway_controller import Controller, RelayCredentialProvider, RelayPolicy
from fleet_manager.gateway_server import build_server
from fleet_manager.gateway_worker import CredentialWorker, credential_provider, token_provider
from gateway_fixtures import Fixtures, public, query
from jwcrypto import jwe
from test_gateway_runtime import running


def forge(provider="azure-devops", authentication="bearer"):
    return ForgeScope(
        provider, "https://dev.azure.test/example" if provider == "azure-devops"
        else "https://api.github.test", "project/repository", "observe", authentication,
    )


def configured(tmp_path, scope=None):
    fixture = Fixtures(tmp_path)
    scope = scope or forge()
    for peer in fixture.value["enrollments"]:
        if peer["principal"] in ("worker", "controller"):
            peer["corpora"] = []
            peer["service"] = "pr-observation"
            peer["installation"] = "pr-install"
    fixture.value["enrollments"][2]["role"] = "pr-worker"
    fixture.value["credential_rules"][0]["forge"] = scope.to_dict()
    parameters = {
        "job": "job-a", "source": "source-a", "account": "account-a", "audience": "ingestion-a",
        "scopes": ["read"], "action": "get-access-token", "recipient": "worker",
        "forge": scope.to_dict(),
    }
    fixture.value["credential_rules"][0]["action"] = parameters["action"]
    return fixture, parameters


def worker(fixture, client):
    return CredentialWorker(
        client, service="pr-observation", installation="pr-install", generation=1,
        recipient_key=fixture.recipient, controller="controller", controller_generation=1,
        controller_keys={"keys": [public(fixture.proofs["controller"], "proof")]},
    )


class ScopedProvider:
    """An explicit synthetic provider, not a production permission verifier."""

    def __init__(self, transform=lambda value: value):
        self.calls = []
        self.transform = transform

    async def acquire(self, request, *, timeout):
        self.calls.append(copy.deepcopy(request))
        return self.transform(CredentialMaterial(
            f"synthetic-forge-secret-{len(self.calls)}", int(time.time()) + 300,
            request["source"], request["account"], request["audience"], tuple(request["scopes"]),
            ForgeScope.load(request["forge"]),
        ))


@contextmanager
def responding(fixture, gateway, provider):
    responder = Controller(
        fixture.client("controller", gateway.server_port), provider,
        Signer(fixture.proofs["controller"], "proof", "controller", "controller"),
        fleet="fleet-a", generation=1, lease_seconds=15,
    )
    responder.start()
    stop = threading.Event()
    failures = []

    def respond():
        try:
            while not stop.is_set():
                responder.step()
                stop.wait(0.15)
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=respond)
    thread.start()
    try:
        yield responder
    finally:
        stop.set()
        thread.join(5)
        responder.close()
        assert not thread.is_alive() and failures == []


@pytest.mark.parametrize("provider,authentication", [
    ("github", "bearer"), ("azure-devops", "bearer"), ("azure-devops", "basic"),
])
def test_pr_worker_renews_exact_scoped_material_without_index_identity(
    tmp_path, capsys, provider, authentication,
):
    scope = forge(provider, authentication)
    fixture, parameters = configured(tmp_path, scope)
    source = ScopedProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        client = fixture.client("worker", gateway.server_port)
        assert client.connect()["supported"] == ["credential.get"]
        with responding(fixture, gateway, source):
            consumer = worker(fixture, client)
            first, second = consumer.acquire(parameters), consumer.acquire(parameters)
            assert first.token != second.token
            assert first.forge == second.forge == scope
            assert first.expires_at > time.time()
            assert "synthetic-forge-secret" not in repr(first)
            assert source.calls == [parameters, parameters]
            with pytest.raises(GatewayError, match="gateway-request-denied"):
                client.query(query())
            stored = list(gateway.gateway.broker._items.values())
            assert len(stored) == 2 and all(item.state == "ACKED" for item in stored)
            assert all(item.delivery is None for item in stored)
            assert "synthetic-forge-secret" not in repr(stored)
        with pytest.raises(GatewayError, match="controller-unavailable"):
            consumer.acquire(parameters)
    captured = capsys.readouterr()
    assert "synthetic-forge-secret" not in captured.out + captured.err


@pytest.mark.parametrize("field,value", [
    ("provider", "github"), ("api_base", "https://other.example.test"),
    ("repository", "other/repository"), ("authentication", "basic"),
])
def test_pr_worker_cannot_select_another_forge_target(tmp_path, field, value):
    fixture, parameters = configured(tmp_path)
    source = ScopedProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            parameters["forge"][field] = value
            with pytest.raises(GatewayError, match="gateway-request-denied"):
                worker(fixture, fixture.client("worker", gateway.server_port)).acquire(parameters)
            assert source.calls == []


@pytest.mark.parametrize("change", [
    {"account": "other"}, {"scopes": ["write"]}, {"job": "other"},
    {"recipient": "controller"},
])
def test_pr_worker_cannot_cross_existing_account_permission_job_or_recipient(tmp_path, change):
    fixture, parameters = configured(tmp_path)
    source = ScopedProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            parameters.update(change)
            with pytest.raises(GatewayError):
                worker(fixture, fixture.client("worker", gateway.server_port)).acquire(parameters)
            assert source.calls == []


@pytest.mark.parametrize("transform", [
    lambda value: replace(value, forge=None),
    lambda value: replace(value, forge=replace(value.forge, repository="other/repository")),
    lambda value: replace(value, forge=replace(value.forge, api_base="https://other.example.test")),
    lambda value: replace(value, forge=replace(value.forge, authentication="basic")),
    lambda value: replace(value, account="other"),
    lambda value: replace(value, audience="other"),
    lambda value: replace(value, scopes=("write",)),
    lambda value: replace(value, expires_at=int(time.time()) - 1),
])
def test_controller_refuses_incorrect_unscoped_or_expired_provider_material(tmp_path, transform):
    fixture, parameters = configured(tmp_path)
    source = ScopedProvider(transform)
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            with pytest.raises(GatewayError, match="credential-unavailable"):
                worker(fixture, fixture.client("worker", gateway.server_port)).acquire(parameters)
            assert len(source.calls) == 1
            assert all(item.delivery is None for item in gateway.gateway.broker._items.values())


def test_missing_forge_is_not_an_index_fallback(tmp_path):
    fixture, parameters = configured(tmp_path)
    source = ScopedProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            del parameters["forge"]
            with pytest.raises(GatewayError, match="gateway-request-denied"):
                worker(fixture, fixture.client("worker", gateway.server_port)).acquire(parameters)
            assert source.calls == []


@pytest.mark.parametrize("field,value", [
    ("repository", "other/repository"), ("api_base", "https://other.example.test"),
    ("authentication", "basic"), ("permission", "write"), (None, None),
])
def test_worker_checks_sealed_target_before_ack_even_with_valid_controller_signature(
    tmp_path, monkeypatch, field, value,
):
    fixture, parameters = configured(tmp_path)
    source = ScopedProvider()
    keys = {"keys": [public(fixture.proofs["controller"], "proof")]}
    signer = Signer(fixture.proofs["controller"], "proof", "controller", "controller")
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            client = fixture.client("worker", gateway.server_port)
            original = client.credential_result

            def altered_result(*args, **kwargs):
                result = original(*args, **kwargs)
                if result.get("state") != "READY":
                    return result
                claims = verify(result["receipt"], keys, "controller", "worker", lifetime=60)
                old = jwe.JWE(algs=["RSA-OAEP-256", "A256GCM"])
                old.deserialize(claims["sealed"], key=fixture.recipient)
                plain = parse(old.payload)
                if field is None:
                    del plain["forge"]
                else:
                    plain["forge"][field] = value
                envelope = jwe.JWE(
                    encode(plain), protected=encode(old.jose_header),
                    algs=["RSA-OAEP-256", "A256GCM"],
                )
                envelope.add_recipient(fixture.recipient)
                sealed = envelope.serialize(compact=True)
                result["receipt"] = signer.sign({
                    "binding": result["binding"], "state": "READY", "sealed": sealed,
                    "sealed_digest": hashlib.sha256(sealed.encode()).hexdigest(),
                }, "worker", expires_at=claims["exp"])
                return result

            monkeypatch.setattr(client, "credential_result", altered_result)
            with pytest.raises(GatewayError, match="credential-scope-mismatch|unsupported-forge-permission"):
                worker(fixture, client).acquire(parameters)
            assert len(source.calls) == 1
            assert all(item.state == "DELIVERED" for item in gateway.gateway.broker._items.values())


@pytest.mark.parametrize("revoked", [False, True])
def test_restarted_policy_checks_revocation_for_fresh_client(tmp_path, revoked):
    fixture, parameters = configured(tmp_path)
    source = ScopedProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            worker(fixture, fixture.client("worker", gateway.server_port)).acquire(parameters)
            port = gateway.server_port
    fixture.value["tls"]["port"] = port
    fixture.value["revoked"] = ["worker"] if revoked else []
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            fresh = worker(fixture, fixture.client("worker", gateway.server_port))
            if revoked:
                with pytest.raises(GatewayError):
                    fresh.acquire(parameters)
                assert len(source.calls) == 1
                assert gateway.gateway.broker._items == {}
            else:
                assert fresh.acquire(parameters).forge == forge()
                assert len(source.calls) == 2


@pytest.mark.parametrize("change", [
    lambda value: value["credential_rules"][0].pop("forge"),
    lambda value: value["enrollments"][2].update(corpora=["invented-corpus"]),
    lambda value: value["enrollments"][2].update(role="index-worker"),
    lambda value: value["credential_rules"][0]["forge"].update(permission="merge"),
    lambda value: value["credential_rules"].append(copy.deepcopy(value["credential_rules"][0])),
])
def test_invalid_pr_enrollment_or_rule_refuses_before_serving(tmp_path, change):
    fixture, _ = configured(tmp_path)
    change(fixture.value)
    with pytest.raises(GatewayError):
        GatewayConfig.load(fixture.value)


@pytest.mark.parametrize("change", [
    {"provider": "other"}, {"provider": []}, {"api_base": "http://forge.example.test"},
    {"api_base": "https://@forge.example.test"},
    {"api_base": "https://forge.example.test?other"},
    {"api_base": "https://forge.example.test/"},
    {"api_base": "https://forge.example.test:invalid"},
    {"repository": "../repository"}, {"repository": "project/repository/other"},
    {"repository": "project/%2e%2e"}, {"repository": ["project", "repository"]},
    {"permission": "write"}, {"authentication": "ambient"},
])
def test_forge_scope_strict_shape_and_read_only_contract(change):
    value = forge().to_dict() | change
    with pytest.raises(GatewayError):
        ForgeScope.load(value)


def test_relay_requires_exact_target_and_actual_provider_verifier():
    scope = forge()
    policy = RelayPolicy(
        "source-a", "account-a", "ingestion-a", ("read",), "get-access-token",
        {"resource": "provider-native-resource"}, scope,
    )
    resolved, verified = [], []

    class Relay:
        def supports(self, action, fields):
            return action == policy.action and fields == policy.fields

        async def resolve(self, action, fields, *, timeout):
            resolved.append((action, fields))
            return "synthetic-relay-secret"

    def verifier(raw, selected):
        verified.append((raw, selected))
        # A real provider verifier must check actual resource/permission/revocation.
        return None

    provider = RelayCredentialProvider(Relay(), (policy,), verifier)
    request = credential({
        "job": "job-a", "source": policy.source, "account": policy.account,
        "audience": policy.audience, "scopes": list(policy.scopes), "action": policy.action,
        "recipient": "worker", "forge": scope.to_dict(),
    })
    assert asyncio.run(provider.acquire(request, timeout=1)) is None
    assert resolved == [(policy.action, policy.fields)]
    assert verified == [("synthetic-relay-secret", policy)]
    request["forge"]["repository"] = "other/repository"
    assert asyncio.run(provider.acquire(request, timeout=1)) is None
    assert len(resolved) == len(verified) == 1


def test_string_only_loader_refuses_pr_binding_before_key_or_token_consumption(tmp_path):
    fixture, parameters = configured(tmp_path)
    path = tmp_path / "worker-policy.json"
    path.write_text(json.dumps({
        "schema": "fleet-manager.credential-worker", "version": 1,
        "discovery": str(tmp_path / "absent-discovery.json"), "principal": "worker",
        "service": "pr-observation", "installation": "pr-install", "generation": 1,
        "proof_key": str(tmp_path / "absent-proof.pem"),
        "access_token_file": str(tmp_path / "absent-access-token"),
        "recipient_key": str(tmp_path / "absent-recipient.pem"),
        "controller": "controller", "controller_generation": 1,
        "controller_keys": {"keys": [public(fixture.proofs["controller"], "proof")]},
        "bindings": [parameters],
    }), encoding="utf-8")
    with pytest.raises(GatewayError, match="typed-forge-consumer-required"):
        token_provider(path, source="source-a", account="account-a")


def worker_policy(fixture, parameters, port=1):
    root = fixture.root
    recipient = root / "typed-recipient.pem"
    recipient.write_bytes(fixture.recipient.export_to_pem(private_key=True, **{"password": None}))
    access = root / "typed-access.jwt"
    access.write_text(fixture.access("worker"), encoding="utf-8")
    discovery = root / "typed-discovery.json"
    discovery.write_bytes(encode({
        "schema": "fleet-manager.gateway-endpoint", "version": 1,
        "endpoint": f"https://127.0.0.1:{port}", "ca": str(root / "tls.pem"),
        "issuer": "fixture-issuer", "audience": "gateway-a", "gateway": "gateway-install",
        "jwks": fixture.gateway_keys, "remote": False,
    }))
    policy = root / "typed-worker.json"
    policy.write_bytes(encode({
        "schema": "fleet-manager.credential-worker", "version": 1,
        "discovery": str(discovery), "principal": "worker", "service": "pr-observation",
        "installation": "pr-install", "generation": 1,
        "proof_key": str(root / "identity-worker.pem"), "access_token_file": str(access),
        "recipient_key": str(recipient), "controller": "controller", "controller_generation": 1,
        "controller_keys": {"keys": [public(fixture.proofs["controller"], "proof")]},
        "bindings": [parameters],
    }))
    return policy


@pytest.mark.parametrize("provider,authentication", [
    ("github", "bearer"), ("azure-devops", "bearer"), ("azure-devops", "basic"),
])
def test_typed_policy_and_real_credential_cli_renew_through_tls(tmp_path, provider, authentication):
    scope = forge(provider, authentication)
    fixture, parameters = configured(tmp_path, scope)
    source = ScopedProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        policy = worker_policy(fixture, parameters, gateway.server_port)
        argv = [
            sys.executable, "-m", "fleet_manager", "credential", "--config", str(policy),
            "--source", parameters["source"], "--account", parameters["account"],
            "--forge", json.dumps(scope.to_dict()), "--timeout", "5",
        ]

        def invoke():
            return subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        with responding(fixture, gateway, source):
            acquire = credential_provider(
                policy, source=parameters["source"], account=parameters["account"], forge=scope,
            )
            assert acquire().forge == scope
            first, second = invoke(), invoke()
            assert first.returncode == second.returncode == 0
            for index, result in enumerate((first, second), 2):
                assert result.stderr == ""
                assert parse(result.stdout.encode()) == {
                    "schema": "fleet-manager.forge-credential", "version": 1,
                    "forge": scope.to_dict(), "expires_at": parse(result.stdout.encode())["expires_at"],
                    "token": f"synthetic-forge-secret-{index}",
                }
            assert source.calls == [parameters] * 3
            assert all(item.state == "ACKED" and item.delivery is None
                       for item in gateway.gateway.broker._items.values())
        refused = invoke()
        assert refused.returncode == 1 and refused.stdout == ""
        assert refused.stderr.strip() == "fleet-manager: controller-unavailable"
        assert len(source.calls) == 3
    assert not any("synthetic-forge-secret" in part for part in argv)


@pytest.mark.parametrize("change,code", [
    ("account", "provider-policy-denied"),
    ("forge", "provider-policy-denied"),
    ("duplicate", "ambiguous-provider-policy"),
    ("legacy", "typed-forge-policy-required"),
])
def test_typed_loader_refuses_before_secret_reference_reads(tmp_path, change, code):
    fixture, parameters = configured(tmp_path)
    policy = worker_policy(fixture, parameters)
    value = parse(policy.read_bytes())
    value["proof_key"] = str(tmp_path / "absent-proof.pem")
    if change == "duplicate":
        value["bindings"].append(copy.deepcopy(value["bindings"][0]))
    if change == "legacy":
        del value["bindings"][0]["forge"]
    policy.write_bytes(encode(value))
    scope = replace(forge(), repository="other/repository") if change == "forge" else forge()
    with pytest.raises(GatewayError, match=code):
        credential_provider(
            policy, source="source-a", account="other" if change == "account" else "account-a",
            forge=scope,
        )


def test_credential_cli_refuses_terminal_output_and_nonfinite_deadlines(tmp_path, monkeypatch, capsys):
    from fleet_manager import __main__ as cli

    argv = [
        "credential", "--config", str(tmp_path / "absent.json"),
        "--source", "source-a", "--account", "account-a", "--forge", json.dumps(forge().to_dict()),
    ]
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(argv) == 1
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    for deadline in ("nan", "inf", "0", "-1", "61"):
        assert cli.main([*argv, "--timeout", deadline]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip() == "fleet-manager: invalid-deadline"
