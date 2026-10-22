"""Code-selected public trust still traverses the common crypto/serving fence."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import FrozenInstanceError, replace

import pytest

from fleet_manager import planning
from fleet_manager.gateway_identity_provider import (
    IdentityProvider, IdentityProviderError, IdentityTrust,
)
from fleet_manager.gateway_service_context import parse_context
from fleet_manager.gateway_service_policy import ServiceAuthority, ServicePolicyError
from fleet_manager.runtime_identity import IdentityError
from gateway_service_transport_fixtures import Rig, eventually
from test_gateway_service_policy import Harness, TARGET
from test_runtime_identity import NOW


def selected(harness, prepare=None):
    trust = copy.deepcopy(harness.document["identity"])
    calls = []

    def resolve(raw):
        assert type(raw) is bytes
        calls.append(raw)
        configuration = planning.decode(raw)
        assert set(configuration) == {"snapshot"} and isinstance(configuration["snapshot"], str)
        return IdentityTrust(
            trust["issuer"], trust["audience"], planning.canonical(trust["jwks"]),
            trust["trust_valid_until"])

    provider = IdentityProvider("example-provider", prepare or resolve)
    harness.document["version"] = 2
    harness.document["identity"] = {
        "provider": provider.name, "profile": "jwt-pop-v1",
        "configuration": {"snapshot": "selected"},
    }
    harness.owner = ServiceAuthority(
        fleet="fleet-a", gateway="gateway-a", source=harness.source, clock=harness.clock,
        identity_provider=provider)
    return provider, trust, calls


def test_registration_is_inert_frozen_and_not_a_verifier():
    h = Harness()
    provider, _, calls = selected(h)
    assert calls == [] and h.reads == 0
    with pytest.raises(FrozenInstanceError):
        provider.name = "replacement"
    with pytest.raises(FrozenInstanceError):
        provider.prepare = lambda raw: None
    for name in ("", "module:callable", "python -c unsafe", 1):
        with pytest.raises(IdentityError, match="^invalid-identity-provider$"):
            IdentityProvider(name, lambda raw: None)
    with pytest.raises(IdentityError, match="^invalid-identity-provider$"):
        IdentityProvider("example-provider", None)
    with pytest.raises(ServicePolicyError, match="^invalid-identity-provider$"):
        ServiceAuthority(fleet="fleet-a", gateway="gateway-a", source=h.source,
                         clock=h.clock, identity_provider=lambda raw: None)


@pytest.mark.parametrize("change,code", [
    ("unregistered", "unsupported-identity-provider"),
    ("wrong-provider", "unsupported-identity-provider"),
    ("wrong-profile", "unsupported-profile"),
    ("extra-field", "invalid-trust"), ("missing-field", "invalid-trust"),
    ("non-object", "invalid-trust"), ("boolean-version", "unsupported-schema"),
])
def test_configuration_never_loads_or_falls_back_to_an_arbitrary_provider(change, code):
    h = Harness()
    provider, _, calls = selected(h)
    identity = h.document["identity"]
    if change == "unregistered":
        h.owner = ServiceAuthority(fleet="fleet-a", gateway="gateway-a",
                                   source=h.source, clock=h.clock)
    elif change == "wrong-provider":
        identity["provider"] = "module:unregistered"
    elif change == "wrong-profile":
        identity["profile"] = "bearer"
    elif change == "extra-field":
        identity["command"] = "never-executed"
    elif change == "missing-field":
        del identity["configuration"]
    elif change == "non-object":
        identity["configuration"] = []
    else:
        h.document["version"] = True
    with pytest.raises(ServicePolicyError, match=f"^{code}$"):
        h.owner.reload()
    assert calls == [] and h.owner.status().revision is None
    assert provider.name == "example-provider"


def test_default_schema_and_digest_are_unchanged_and_do_not_call_an_adapter():
    h = Harness()
    raw = h.source()
    original, _ = parse_context(raw, h.clock)
    provider = IdentityProvider("unused", lambda raw: pytest.fail("legacy invoked plugin"))
    selected_context, _ = parse_context(raw, h.clock, identity_provider=provider)
    assert selected_context == original
    assert original.trust_digest == planning.digest(h.document["identity"])


def test_provider_never_substitutes_for_real_crypto_binding_or_replay():
    h = Harness()
    _, _, calls = selected(h)
    h.owner.reload()
    assert len(calls) == 1
    credentials = h.credentials()
    grant = h.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")
    assert h.owner.revalidate_service(grant).name == "service-a"
    with pytest.raises(ServicePolicyError, match="^proof-replayed$"):
        h.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")
    for kwargs in (
        {"signer": h.keys[2]}, {"proof": {"nonce": "wrong"}},
        {"proof": {"htm": "GET"}}, {"proof": {"htu": TARGET + "/other"}},
        {"access": {"aud": "unrelated-api"}},
        {"access": {"cnf": {}}}, {"access": {"aud": ["fleet-api"]}},
        {"access": {"iat": NOW - 60, "exp": NOW}},
    ):
        with pytest.raises(ServicePolicyError):
            h.service(**kwargs)
    assert len(calls) == 1


def test_equivalent_publication_preserves_nonce_replay_and_exact_handles():
    h = Harness()
    _, trust, calls = selected(h)
    trust["jwks"]["keys"].append({**h.keys[2].as_dict(private=False), "kid": "issuer-2"})
    first = h.owner.reload()
    grant = h.service()
    replay = h.credentials(subject="caller-a", key=3, method="GET")
    h.owner.authenticate_caller(*replay, method="GET", target_uri=TARGET,
                                slot="service-a", request_method="GET")
    nonce = h.owner.challenge()
    trust["jwks"]["keys"].reverse()
    assert not h.owner.reload().changed
    assert h.owner.status().generation == first.generation
    h.document["manifest"]["revision"] += 1
    assert h.owner.reload().changed
    assert h.owner.challenge() == nonce
    assert h.owner.revalidate_service(grant).name == "service-a"
    with pytest.raises(ServicePolicyError, match="^proof-replayed$"):
        h.owner.authenticate_caller(*replay, method="GET", target_uri=TARGET,
                                    slot="service-a", request_method="GET")
    assert len(calls) == 3


@pytest.mark.parametrize("change", ["trust", "configuration"])
def test_provider_trust_or_configuration_changes_replace_nonce_and_revoke_handles(change):
    h = Harness()
    _, trust, _ = selected(h)
    h.owner.reload()
    grant = h.service()
    nonce = h.owner.challenge()
    if change == "trust":
        trust["trust_valid_until"] += 1
    else:
        # Same prepared trust, but a distinct provider configuration is a new epoch.
        h.document["identity"]["configuration"] = {"snapshot": "replacement"}
    h.update()
    assert h.owner.challenge() != nonce
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_service(grant)


def _with_provider(h, provider):
    h.owner = ServiceAuthority(fleet="fleet-a", gateway="gateway-a",
                               source=h.source, clock=h.clock, identity_provider=provider)
    return h


def test_invalid_candidates_are_value_free_atomic_and_expire_without_refresh():
    h = Harness()
    _, trust, _ = selected(h)
    trust["trust_valid_until"] = NOW + 10
    h.owner.reload()
    grant = h.service()
    nonce = h.owner.challenge()
    for change, code in (
        (lambda: trust.update(issuer="http://invalid.example"), "invalid-trust"),
        (lambda: trust.update(issuer="https://issuer.example",
                              jwks={"keys": []}), "invalid-trust"),
    ):
        change()
        h.document["manifest"]["revision"] += 1
        with pytest.raises(ServicePolicyError, match=f"^{code}$"):
            h.owner.reload()
        assert h.owner.challenge() == nonce
        assert h.owner.revalidate_service(grant).name == "service-a"
    h.clock.value = NOW + 10
    with pytest.raises(ServicePolicyError, match="^authority-unavailable$"):
        h.owner.revalidate_service(grant)


@pytest.mark.parametrize("result", [None, {}, b"serialized-identity", "identity"])
def test_only_immutable_public_trust_can_be_returned(result):
    h = Harness()
    selected(h, lambda raw: result)
    with pytest.raises(ServicePolicyError, match="^invalid-provider-trust$"):
        h.owner.reload()


def test_adapter_failure_is_explicit_and_never_echoes_provider_details():
    h = Harness()

    def unavailable(raw):
        raise IdentityProviderError() from OSError("private-provider-detail")

    selected(h, unavailable)
    with pytest.raises(ServicePolicyError, match="^provider-trust-unavailable$") as caught:
        h.owner.reload()
    assert caught.value.__cause__ is None
    assert h.owner.status().last_error == "provider-trust-unavailable"
    assert "private-provider-detail" not in repr(h.owner.status())

    def programming_error(raw):
        raise RuntimeError("adapter-programming-error")

    h = _with_provider(h, IdentityProvider("example-provider", programming_error))
    with pytest.raises(RuntimeError, match="^adapter-programming-error$"):
        h.owner.reload()


@pytest.mark.parametrize("change", ["private-key", "mutable-jwks", "oversized", "bad-time"])
def test_provider_output_still_obeys_common_public_key_and_bound_constraints(change):
    h = Harness()
    identity = copy.deepcopy(h.document["identity"])
    result = IdentityTrust(identity["issuer"], identity["audience"],
                           planning.canonical(identity["jwks"]), identity["trust_valid_until"])
    if change == "private-key":
        result = replace(result, jwks=planning.canonical({"keys": [
            {**h.keys[0].as_dict(private=True), "kid": "issuer-1"}]}))
    elif change == "mutable-jwks":
        result = replace(result, jwks=bytearray(result.jwks))
    elif change == "oversized":
        result = replace(result, jwks=b"x" * (planning.MAX_BYTES + 1))
    else:
        result = replace(result, trust_valid_until=True)
    selected(h, lambda raw: result)
    with pytest.raises(ServicePolicyError):
        h.owner.reload()


def test_registered_provider_reaches_actual_tls_service_and_caller_paths(tmp_path):
    async def scenario():
        rig = Rig(tmp_path)
        provider, trust, calls = selected(rig.h)
        try:
            await rig.start(identity_provider=provider)
            await rig.connect()
            async with await rig.request() as response:
                assert response.status == 200
                assert (await response.json())["service"] == "a"
            old = rig.state.channels["service-a"]
            nonce = rig.state.authority.challenge()
            await rig.update()
            assert rig.state.channels["service-a"] is old
            assert rig.state.authority.challenge() == nonce
            async with rig.session.get(rig.origin + "/api") as response:
                assert response.status == 401
            selected_headers = await rig.auth("/api", proof_target=rig.origin + "/assets")
            async with rig.session.get(rig.origin + "/api", headers=selected_headers) as response:
                assert response.status == 401
            assert len(rig.backends[0].requests) == 1
            trust["trust_valid_until"] += 1
            await rig.update()
            await eventually(lambda: old.closed)
            assert rig.state.authority.challenge() != nonce
            await rig.connect()
            async with await rig.request() as response:
                assert response.status == 200
            assert calls
        finally:
            await rig.close()
    asyncio.run(scenario())
