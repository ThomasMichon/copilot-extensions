"""Separate protected-review delivery through the existing private TLS fixtures."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable

import pytest
from fleet_manager.gateway_auth import Signer, verify
from fleet_manager.gateway_client import CredentialMaterial
from fleet_manager.gateway_contract import (
    REVIEW_CREDENTIAL_CONTRACT, GatewayError, ReviewScope, credential, encode, parse,
)
from fleet_manager.gateway_controller import Controller, RelayCredentialProvider, RelayPolicy
from fleet_manager.gateway_server import build_server
from fleet_manager.gateway_worker import credential_provider, review_credential_provider
from gateway_fixtures import Fixtures, public
from jwcrypto import jwe
from test_gateway_pr_credentials import (
    ScopedProvider, configured, forge, responding, worker, worker_policy,
)
from test_gateway_runtime import running


def review_scope() -> ReviewScope:
    return ReviewScope(
        "https://dev.azure.test/example", "project/repository", "bearer",
        "project-id", "repository-id", 7, "native-principal", ("comment", "reply"),
    )


def review_configured(tmp_path: Path) -> tuple[Fixtures, dict]:
    fixture, parameters = configured(tmp_path)
    fixture.value["version"] = 2
    for selected in (parameters, fixture.value["credential_rules"][0]):
        del selected["forge"]
        selected["review"] = review_scope().to_dict()
    return fixture, parameters


class ReviewProvider:
    """Synthetic verifier, not actual permission or installed-provider evidence."""

    review_contract = REVIEW_CREDENTIAL_CONTRACT

    def __init__(
        self, transform: Callable[[CredentialMaterial], CredentialMaterial] = lambda value: value,
    ) -> None:
        self.calls = []
        self.transform = transform

    async def acquire(self, request: dict, *, timeout: float) -> CredentialMaterial:
        self.calls.append(copy.deepcopy(request))
        return self.transform(CredentialMaterial(
            "synthetic-review-secret", int(time.time()) + 300,
            request["source"], request["account"], request["audience"], tuple(request["scopes"]),
            review=ReviewScope.load(request["review"]),
        ))


def test_review_loader_retains_separate_binding_and_current_controller_support(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    fixture, parameters = review_configured(tmp_path)
    source = ReviewProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        policy = worker_policy(fixture, parameters, gateway.server_port)
        value = parse(policy.read_bytes())
        value["version"] = 2
        policy.write_bytes(encode(value))
        acquire = review_credential_provider(
            policy, source="source-a", account="account-a", review=review_scope(),
        )
        with pytest.raises(GatewayError, match="unsupported-review-contract"):
            acquire()
        assert source.calls == []
        with responding(fixture, gateway, source, protected_review=True):
            first, second = acquire(), acquire()
            assert first.review == second.review == review_scope()
            assert first.forge is second.forge is None
            assert "synthetic-review-secret" not in repr(first)
            assert source.calls == [parameters, parameters]
        with pytest.raises(GatewayError, match="unsupported-review-contract"):
            acquire()
        assert len(source.calls) == 2
        assert all(item.state == "ACKED" and item.delivery is None
                   for item in gateway.gateway.broker._items.values())
    captured = capsys.readouterr()
    assert "synthetic-review-secret" not in captured.out + captured.err


def test_prior_controller_and_gateway_refuse_review_before_acquisition(tmp_path: Path) -> None:
    fixture, parameters = review_configured(tmp_path)
    source = ReviewProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            with pytest.raises(GatewayError, match="unsupported-review-contract"):
                worker(fixture, fixture.client("worker", gateway.server_port),
                       protected_review=True).acquire(parameters)
            client = fixture.client("worker", gateway.server_port)
            client.connect()
            with pytest.raises(GatewayError):
                client.request_credential({
                    "request_id": "unsupported-review", "service_id": "pr-observation",
                    "installation_id": "pr-install", "generation": 1,
                    "operation": "credential.review", "parameters": parameters,
                })
            assert gateway.gateway.broker._items == {}
    fixture.value["version"] = 1
    del fixture.value["credential_rules"][0]["review"]
    fixture.value["credential_rules"][0]["forge"] = forge().to_dict()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        responder = Controller(
            fixture.client("controller", gateway.server_port), source,
            Signer(fixture.proofs["controller"], "proof", "controller", "controller"),
            fleet="fleet-a", generation=1, protected_review=True,
        )
        with pytest.raises(GatewayError, match="unsupported-review-contract"):
            responder.start()
        assert responder.subscription is None
        with pytest.raises(GatewayError, match="unsupported-review-contract"):
            worker(fixture, fixture.client("worker", gateway.server_port),
                   protected_review=True).acquire(parameters)
        assert gateway.gateway.broker._items == {}
    assert source.calls == []


def test_new_gateway_preserves_ordinary_observation_acquisition(tmp_path: Path) -> None:
    fixture, parameters = configured(tmp_path)
    fixture.value["version"] = 2
    source = ScopedProvider()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source):
            policy = worker_policy(fixture, parameters, gateway.server_port)
            material = credential_provider(
                policy, source="source-a", account="account-a", forge=forge(),
            )()
            assert material.forge == forge() and material.review is None
            assert source.calls == [parameters]


@pytest.mark.parametrize("field,value", [
    ("api_base", "https://other.example.test"), ("repository", "other/repository"),
    ("project_id", "other"), ("repository_id", "other"), ("number", 8),
    ("principal_id", "other"), ("actions", ["verdict"]),
])
def test_exact_review_selection_is_checked_before_provider_acquisition(
    tmp_path: Path, field: str, value: object,
) -> None:
    fixture, parameters = review_configured(tmp_path)
    source = ReviewProvider()
    parameters["review"][field] = value
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source, protected_review=True):
            with pytest.raises(GatewayError):
                worker(fixture, fixture.client("worker", gateway.server_port),
                       protected_review=True).acquire(parameters)
    assert source.calls == []


@pytest.mark.parametrize("transform", [
    lambda value: replace(value, review=None),
    lambda value: replace(value, forge=forge()),
    lambda value: replace(value, review=replace(value.review, principal_id="other")),
    lambda value: replace(value, expires_at=int(time.time()) - 1),
])
def test_controller_refuses_mismatched_review_material(
    tmp_path: Path, transform: Callable[[CredentialMaterial], CredentialMaterial],
) -> None:
    fixture, parameters = review_configured(tmp_path)
    source = ReviewProvider(transform)
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source, protected_review=True):
            with pytest.raises(GatewayError, match="credential-unavailable"):
                worker(fixture, fixture.client("worker", gateway.server_port),
                       protected_review=True).acquire(parameters)
            assert len(source.calls) == 1
            assert all(item.delivery is None for item in gateway.gateway.broker._items.values())


def test_worker_rechecks_sealed_native_principal_before_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, parameters = review_configured(tmp_path)
    source = ReviewProvider()
    keys = {"keys": [public(fixture.proofs["controller"], "proof")]}
    signer = Signer(fixture.proofs["controller"], "proof", "controller", "controller")
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with responding(fixture, gateway, source, protected_review=True):
            client = fixture.client("worker", gateway.server_port)
            original = client.credential_result

            def altered(*args, **kwargs) -> dict:
                result = original(*args, **kwargs)
                if result.get("state") != "READY":
                    return result
                claims = verify(result["receipt"], keys, "controller", "worker", lifetime=60)
                old = jwe.JWE(algs=["RSA-OAEP-256", "A256GCM"])
                old.deserialize(claims["sealed"], key=fixture.recipient)
                plain = parse(old.payload)
                plain["review"]["principal_id"] = "other"
                envelope = jwe.JWE(encode(plain), protected=encode(old.jose_header),
                                   algs=["RSA-OAEP-256", "A256GCM"])
                envelope.add_recipient(fixture.recipient)
                sealed = envelope.serialize(compact=True)
                result["receipt"] = signer.sign({
                    "binding": result["binding"], "state": "READY", "sealed": sealed,
                    "sealed_digest": hashlib.sha256(sealed.encode()).hexdigest(),
                }, "worker", expires_at=claims["exp"])
                return result

            monkeypatch.setattr(client, "credential_result", altered)
            with pytest.raises(GatewayError, match="credential-scope-mismatch"):
                worker(fixture, client, protected_review=True).acquire(parameters)
            assert all(item.state == "DELIVERED" for item in gateway.gateway.broker._items.values())


def test_observation_parser_and_loader_do_not_accept_review_configuration(tmp_path: Path) -> None:
    fixture, parameters = review_configured(tmp_path)
    with pytest.raises(GatewayError):
        credential(parameters)
    with pytest.raises(GatewayError):
        credential({**parameters, "forge": forge().to_dict()}, protected_review=True)
    policy = worker_policy(fixture, parameters)
    value = parse(policy.read_bytes())
    value["version"] = 2
    value["proof_key"] = str(tmp_path / "must-not-read")
    policy.write_bytes(encode(value))
    with pytest.raises(GatewayError):
        credential_provider(policy, source="source-a", account="account-a", forge=forge())
    value["version"] = 1
    policy.write_bytes(encode(value))
    with pytest.raises(GatewayError):
        review_credential_provider(policy, source="source-a", account="account-a", review=review_scope())


def test_review_relay_uses_exact_existing_policy_and_actual_verifier() -> None:
    selected = review_scope()
    policy = RelayPolicy("source-a", "account-a", "ingestion-a", ("read",), "get-access-token",
                         {"resource": "native-resource"}, review=selected)
    calls = []

    class Relay:
        def supports(self, action: str, fields: dict[str, str]) -> bool:
            return True

        async def resolve(self, action: str, fields: dict[str, str], *, timeout: float) -> str:
            calls.append("acquired")
            return "synthetic-review-secret"

    def verify_material(raw: str, actual: RelayPolicy) -> None:
        assert raw == "synthetic-review-secret" and actual is policy
        calls.append("verified")
        return None

    provider = RelayCredentialProvider(Relay(), (policy,), verify_material)
    request = {
        "source": policy.source, "account": policy.account, "audience": policy.audience,
        "scopes": list(policy.scopes), "action": policy.action, "review": selected.to_dict(),
    }
    assert asyncio.run(provider.acquire(request, timeout=1)) is None
    assert calls == ["acquired", "verified"]
    request["review"]["principal_id"] = "other"
    assert asyncio.run(provider.acquire(request, timeout=1)) is None
    assert calls == ["acquired", "verified"]
