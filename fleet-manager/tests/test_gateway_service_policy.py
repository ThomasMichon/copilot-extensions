"""Real synthetic-issuer authentication and current retained grant custody."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from typing import Any

from joserfc.jwk import ECKey
import pytest

from fleet_manager import planning
from fleet_manager.gateway_service_policy import (
    MAX_CALLER_GRANTS, CallerGrant, ServiceAuthority, ServiceGrant, ServicePolicyError,
)
from fleet_manager.runtime_identity import IdentityVerifier
from test_runtime_identity import AUDIENCE, ISSUER, NOW, Clock, pair

TARGET = "https://gateway.example/api"


class Harness:
    def __init__(self) -> None:
        self.keys = tuple(ECKey.generate_key("P-256") for _ in range(4))
        self.clock = Clock()
        self.serial = 0
        self.reads = 0
        self.failure: Exception | None = None
        self.raw: bytes | None = None
        self.document: dict[str, Any] = {
            "schema": "fleet-manager.gateway-service-policy", "version": 1,
            "manifest": {
                "schema": "fleet-manager.gateway-manifest", "version": 1,
                "fleet": "fleet-a", "gateway": "gateway-a", "revision": 1,
                "roles": ["service", "assets"],
                "key_sources": [
                    {"name": name, "provider": "owner-store", "reference": "keys/" + name}
                    for name in ("service-a", "service-b", "caller-a")
                ],
                "slots": [
                    {"name": name, "role": "service", "principal": name,
                     "key_source": name,
                     "routes": [{"prefix": prefix, "methods": ["GET", "POST"],
                                 "capabilities": ["http"]}]}
                    for name, prefix in (("service-a", "/api"), ("service-b", "/assets"))
                ],
            },
            "identity": {
                "issuer": ISSUER, "audience": AUDIENCE,
                "jwks": {"keys": [{**self.keys[0].as_dict(private=False), "kid": "issuer-1"}]},
                "trust_valid_until": NOW + 86400,
            },
            "bindings": [
                {"provider": "owner-store", "reference": "keys/" + name,
                 "jwk": self.keys[index].as_dict(private=False)}
                for index, name in enumerate(("service-a", "service-b", "caller-a"), 1)
            ],
            "callers": [{"principal": "caller-a", "key_source": "caller-a",
                         "slots": ["service-a"], "methods": ["GET", "POST"]}],
        }
        self.owner = ServiceAuthority(fleet="fleet-a", gateway="gateway-a",
                                      source=self.source, clock=self.clock)

    def source(self) -> bytes:
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return self.raw if self.raw is not None else planning.canonical(self.document)

    def update(self) -> None:
        self.document["manifest"]["revision"] += 1
        self.owner.reload()

    def credentials(self, *, subject: str = "service-a", key: int = 1,
                    method: str = "POST", nonce: str | None = None,
                    target: str = TARGET, access: dict[str, Any] | None = None,
                    proof: dict[str, Any] | None = None,
                    signer: ECKey | None = None) -> tuple[str, str]:
        self.serial += 1
        return pair(
            (self.keys[0], self.keys[key]),
            SimpleNamespace(nonce=nonce if nonce is not None else self.owner.challenge()),
            now=self.clock.value, access={"sub": subject, **(access or {})},
            proof={"jti": str(self.serial), "htm": method, "htu": target, **(proof or {})},
            signer=signer,
        )

    def service(self, *, slot: str = "service-a", **kwargs: Any) -> ServiceGrant:
        return self.owner.authenticate_service(*self.credentials(**kwargs),
                                               target_uri=TARGET, slot=slot)

    def caller(self, *, slot: str = "service-a", method: str = "GET",
               request_method: str | None = None, **kwargs: Any) -> CallerGrant:
        return self.owner.authenticate_caller(
            *self.credentials(subject="caller-a", key=3, method=method, **kwargs),
            method=method, target_uri=TARGET, slot=slot,
            request_method=request_method if request_method is not None else method,
        )


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_inert_owner_and_explicit_initial_publication(harness: Harness) -> None:
    assert harness.reads == 0
    assert harness.owner.status().revision is None
    with pytest.raises(ServicePolicyError, match="^not-configured$"):
        harness.owner.challenge()
    result = harness.owner.reload()
    assert result.changed and result.revision == result.generation == 1
    assert harness.reads == 1 and harness.owner.status().slots == 2
    grant = harness.service()
    assert harness.owner.revalidate_service(grant).role == "service"
    assert harness.owner.revalidate_service(grant).principal == "service-a"
    with pytest.raises(FrozenInstanceError):
        grant.slot = "service-b"


@pytest.mark.parametrize("kwargs", [
    {"subject": "service-b"}, {"key": 2}, {"subject": "query"},
    {"subject": "index-worker"}, {"subject": "credential-controller"},
    {"slot": "service-b"}, {"slot": "absent"},
])
def test_service_assignment_is_not_a_legacy_or_self_selected_role(
    harness: Harness, kwargs: dict[str, Any],
) -> None:
    harness.owner.reload()
    with pytest.raises(ServicePolicyError):
        harness.service(**kwargs)


def test_actual_crypto_request_binding_expiry_replay_and_value_privacy(harness: Harness) -> None:
    harness.owner.reload()
    credentials = harness.credentials()
    grant = harness.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")
    assert harness.owner.revalidate_service(grant).name == "service-a"
    with pytest.raises(ServicePolicyError, match="^proof-replayed$") as caught:
        harness.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")
    assert caught.value.code == str(caught.value)
    assert all(value not in str(caught.value) for value in credentials)
    for kwargs in (
        {"signer": harness.keys[2]}, {"proof": {"htm": "GET"}},
        {"proof": {"htu": TARGET + "/other"}}, {"proof": {"nonce": "wrong"}},
        {"access": {"iat": NOW - 60, "exp": NOW}},
        {"access": {"iss": "https://wrong.example"}},
        {"access": {"aud": "other-gateway"}},
    ):
        with pytest.raises(ServicePolicyError):
            harness.service(**kwargs)
    with pytest.raises(ServicePolicyError, match="^invalid-request-context$"):
        harness.owner.authenticate_service(*harness.credentials(),
                                           target_uri="http://gateway.example/api",
                                           slot="service-a")


def test_exact_handle_replacement_release_and_foreign_owner(harness: Harness) -> None:
    harness.owner.reload()
    first, second = harness.service(), harness.service()
    assert second.generation > first.generation
    for counterfeit in (first, replace(second), copy.copy(second),
                        ServiceGrant(second.slot, second.generation, second.expires_at)):
        with pytest.raises(ServicePolicyError, match="^invalid-grant$"):
            harness.owner.revalidate_service(counterfeit)
        assert not harness.owner.release_service(counterfeit)
    foreign = ServiceAuthority(fleet="fleet-a", gateway="gateway-a",
                               source=harness.source, clock=harness.clock)
    foreign.reload()
    with pytest.raises(ServicePolicyError, match="^invalid-grant$"):
        foreign.revalidate_service(second)
    assert harness.owner.revalidate_service(second).name == "service-a"
    assert harness.owner.release_service(second)
    assert not harness.owner.release_service(second)
    with pytest.raises(ServicePolicyError):
        harness.owner.revalidate_service(second)


@pytest.mark.parametrize("change", [
    "role", "principal", "key-source", "binding", "reference",
    "routes", "methods", "capabilities", "configuration", "removed",
])
def test_slot_policy_changes_permanently_withdraw_service_handle(
    harness: Harness, change: str,
) -> None:
    harness.owner.reload()
    grant = harness.service()
    original = copy.deepcopy(harness.document)
    slot = harness.document["manifest"]["slots"][0]
    if change == "role":
        slot["role"] = "assets"
    elif change == "principal":
        slot["principal"] = "different"
    elif change == "key-source":
        slot["key_source"] = "service-b"
    elif change == "binding":
        harness.document["bindings"][0]["jwk"] = harness.keys[2].as_dict(private=False)
    elif change == "reference":
        harness.document["manifest"]["key_sources"][0]["reference"] = "keys/rotated"
        harness.document["bindings"][0]["reference"] = "keys/rotated"
    elif change == "routes":
        slot["routes"][0]["prefix"] = "/changed"
    elif change == "methods":
        slot["routes"][0]["methods"] = ["GET"]
    elif change == "capabilities":
        slot["routes"][0]["capabilities"].append("response-stream")
    elif change == "configuration":
        slot["configuration_ref"] = "settings/v2"
    else:
        harness.document["manifest"]["slots"].pop(0)
        harness.document["callers"] = []
    harness.update()
    with pytest.raises(ServicePolicyError):
        harness.owner.revalidate_service(grant)
    harness.document = original
    harness.document["manifest"]["revision"] = 3
    harness.owner.reload()
    with pytest.raises(ServicePolicyError):
        harness.owner.revalidate_service(grant)


def test_unrelated_update_and_noop_reuse_nonce_replay_and_slot_handle(harness: Harness) -> None:
    harness.owner.reload()
    credentials = harness.credentials()
    grant = harness.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")
    nonce = harness.owner.challenge()
    noop = harness.owner.reload()
    assert not noop.changed
    harness.document["manifest"]["slots"][1]["configuration_ref"] = "config/new"
    harness.update()
    assert harness.owner.challenge() == nonce
    assert harness.owner.revalidate_service(grant).name == "service-a"
    with pytest.raises(ServicePolicyError, match="^proof-replayed$"):
        harness.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")


def test_trust_epoch_change_invalidates_all_handles_and_old_proofs(harness: Harness) -> None:
    harness.owner.reload()
    service, caller = harness.service(), harness.caller()
    old_nonce = harness.owner.challenge()
    old_credentials = harness.credentials()
    harness.document["identity"]["trust_valid_until"] += 1
    harness.update()
    assert harness.owner.challenge() != old_nonce
    for function, grant in ((harness.owner.revalidate_service, service),
                            (harness.owner.revalidate_caller, caller)):
        with pytest.raises(ServicePolicyError):
            function(grant)
    with pytest.raises(ServicePolicyError, match="^invalid-proof$"):
        harness.owner.authenticate_service(*old_credentials, target_uri=TARGET, slot="service-a")
    assert harness.owner.revalidate_service(harness.service()).name == "service-a"


@pytest.mark.parametrize("trust_change", [False, True])
def test_policy_publication_during_real_verification_is_rechecked(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, trust_change: bool,
) -> None:
    harness.owner.reload()
    original = IdentityVerifier.verify

    def verify(owner: IdentityVerifier, *args: Any, **kwargs: Any) -> Any:
        result = original(owner, *args, **kwargs)
        if trust_change:
            harness.document["identity"]["trust_valid_until"] += 1
        else:
            harness.document["manifest"]["slots"][0]["principal"] = "replacement"
        harness.update()
        return result

    monkeypatch.setattr(IdentityVerifier, "verify", verify)
    with pytest.raises(ServicePolicyError, match=(
        "^trust-changed$" if trust_change else "^service-denied$"
    )):
        harness.service()


def test_unrelated_update_during_real_verification_uses_current_slot(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.owner.reload()
    original = IdentityVerifier.verify

    def verify(owner: IdentityVerifier, *args: Any, **kwargs: Any) -> Any:
        result = original(owner, *args, **kwargs)
        harness.document["manifest"]["slots"][1]["configuration_ref"] = "config/new"
        harness.update()
        return result

    monkeypatch.setattr(IdentityVerifier, "verify", verify)
    assert harness.owner.revalidate_service(harness.service()).name == "service-a"


def test_callers_are_explicit_scopes_not_global_or_connector_permissions(harness: Harness) -> None:
    harness.owner.reload()
    grant = harness.caller()
    scope = harness.owner.revalidate_caller(grant)
    assert scope.slot.name == "service-a" and scope.method == "GET"
    assert scope.target_uri == grant.target_uri == TARGET
    for kwargs in (
        {"slot": "service-b"}, {"method": "DELETE"}, {"key": 2},
    ):
        if "key" in kwargs:
            with pytest.raises(ServicePolicyError, match="^caller-denied$"):
                harness.owner.authenticate_caller(
                    *harness.credentials(subject="caller-a", key=2, method="GET"),
                    method="GET", target_uri=TARGET, slot="service-a", request_method="GET",
                )
        else:
            with pytest.raises(ServicePolicyError):
                harness.caller(**kwargs)
    with pytest.raises(ServicePolicyError, match="^caller-denied$"):
        harness.owner.authenticate_caller(
            *harness.credentials(method="GET"), method="GET", target_uri=TARGET,
            slot="service-a", request_method="GET",
        )
    with pytest.raises(ServicePolicyError, match="^method-denied$"):
        harness.caller(method="POST", request_method="GET")
    harness.document["callers"][0]["methods"] = ["DELETE"]
    harness.update()
    with pytest.raises(ServicePolicyError, match="^caller-denied$"):
        harness.caller(method="DELETE")


@pytest.mark.parametrize("change", ["removed", "key", "slots", "methods", "slot-policy"])
def test_current_caller_withdrawal_and_scope_changes(harness: Harness, change: str) -> None:
    harness.owner.reload()
    grant = harness.caller()
    if change == "removed":
        harness.document["callers"] = []
    elif change == "key":
        harness.document["callers"][0]["key_source"] = "service-b"
    elif change == "slots":
        harness.document["callers"][0]["slots"] = ["service-b"]
    elif change == "methods":
        harness.document["callers"][0]["methods"] = ["POST"]
    else:
        harness.document["manifest"]["slots"][0]["routes"][0]["methods"] = ["POST"]
    harness.update()
    with pytest.raises(ServicePolicyError):
        harness.owner.revalidate_caller(grant)


def test_caller_authentication_rechecks_withdrawal_during_verification(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.owner.reload()
    original = IdentityVerifier.verify

    def verify(owner: IdentityVerifier, *args: Any, **kwargs: Any) -> Any:
        result = original(owner, *args, **kwargs)
        harness.document["callers"] = []
        harness.update()
        return result

    monkeypatch.setattr(IdentityVerifier, "verify", verify)
    with pytest.raises(ServicePolicyError, match="^caller-denied$"):
        harness.caller()


def test_caller_capacity_exact_handles_release_and_expired_reclamation(harness: Harness) -> None:
    harness.owner.reload()
    grants = [harness.caller() for _ in range(MAX_CALLER_GRANTS)]
    with pytest.raises(ServicePolicyError, match="^resource-limit$"):
        harness.caller()
    assert not harness.owner.release_caller(replace(grants[0]))
    with pytest.raises(ServicePolicyError, match="^invalid-grant$"):
        harness.owner.revalidate_caller(replace(grants[0]))
    assert harness.owner.release_caller(grants[0])
    replacement = harness.caller()
    assert not harness.owner.release_caller(grants[0])
    assert harness.owner.revalidate_caller(replacement).method == "GET"
    harness.clock.value += 3600
    with pytest.raises(ServicePolicyError):
        harness.owner.revalidate_caller(replacement)
    fresh = harness.caller()
    assert harness.owner.revalidate_caller(fresh).method == "GET"


def test_expiry_trust_deadline_invalid_clock_and_rollback(harness: Harness) -> None:
    harness.owner.reload()
    service, caller = harness.service(), harness.caller()
    harness.clock.value += 3600
    for function, grant in ((harness.owner.revalidate_service, service),
                            (harness.owner.revalidate_caller, caller)):
        with pytest.raises(ServicePolicyError):
            function(grant)
    harness.clock.value -= 1
    with pytest.raises(ServicePolicyError, match="^clock-rollback$"):
        harness.owner.challenge()
    harness.clock.value = True
    with pytest.raises(ServicePolicyError, match="^invalid-clock$"):
        harness.owner.challenge()
    harness.clock.value = NOW + 86400
    with pytest.raises(ServicePolicyError, match="^authority-unavailable$"):
        harness.owner.challenge()


def test_caller_proof_binds_actual_method_and_target_and_replay(harness: Harness) -> None:
    harness.owner.reload()
    for proof in ({"htm": "POST"}, {"htu": TARGET + "/other"}):
        with pytest.raises(ServicePolicyError, match="^invalid-proof$"):
            harness.caller(proof=proof)
    credentials = harness.credentials(subject="caller-a", key=3, method="GET")
    grant = harness.owner.authenticate_caller(
        *credentials, method="GET", target_uri=TARGET,
        slot="service-a", request_method="GET",
    )
    with pytest.raises(ServicePolicyError, match="^proof-replayed$"):
        harness.owner.authenticate_caller(
            *credentials, method="GET", target_uri=TARGET,
            slot="service-a", request_method="GET",
        )
    assert harness.owner.revalidate_caller(grant).target_uri == TARGET


def test_key_rotation_accepts_only_new_key_and_release_cannot_retire_it(harness: Harness) -> None:
    harness.owner.reload()
    previous = harness.service()
    harness.document["bindings"][0]["jwk"] = harness.keys[2].as_dict(private=False)
    harness.update()
    with pytest.raises(ServicePolicyError, match="^service-denied$"):
        harness.service()
    replacement = harness.service(key=2)
    assert not harness.owner.release_service(previous)
    assert harness.owner.revalidate_service(replacement).name == "service-a"


def test_service_retention_is_one_current_grant_per_slot(harness: Harness) -> None:
    harness.owner.reload()
    first = harness.service()
    for _ in range(70):
        last = harness.service()
    assert len(harness.owner._services) == 1
    assert not harness.owner.release_service(first)
    assert harness.owner.revalidate_service(last).name == "service-a"


def test_invalid_key_candidate_preserves_live_context(harness: Harness) -> None:
    harness.owner.reload()
    service, caller = harness.service(), harness.caller()
    nonce = harness.owner.challenge()
    harness.document["bindings"][0]["jwk"]["d"] = "synthetic-private"
    harness.document["manifest"]["revision"] += 1
    with pytest.raises(ServicePolicyError, match="^invalid-binding-key$"):
        harness.owner.reload()
    assert harness.owner.status().revision == 1
    assert harness.owner.challenge() == nonce
    assert harness.owner.revalidate_service(service).name == "service-a"
    assert harness.owner.revalidate_caller(caller).method == "GET"
