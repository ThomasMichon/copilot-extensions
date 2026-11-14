"""Explicit local-only user access and independent remote service authentication."""

import asyncio
import copy
from dataclasses import replace

import pytest

from fleet_manager import planning
from fleet_manager.bearer_identity import PROFILE
from fleet_manager.gateway_identity_provider import IdentityProvider, IdentityTrust
from fleet_manager.gateway_local_handler import LocalHandlerBinding
from fleet_manager.gateway_manifest import GatewayManifest
from fleet_manager.gateway_service_handler import ServiceResponse
from fleet_manager.gateway_service_policy import ServiceAuthority, ServicePolicyError
from fleet_manager.runtime_admission import AdmissionAuthority, AdmissionError
from gateway_service_transport_fixtures import Rig
from test_bearer_identity import public_jwks, rsa_keys as rsa_keys, token
from test_gateway_service_policy import Harness, TARGET
from test_runtime_admission import actor, intent, policy
from test_runtime_identity import AUDIENCE, ISSUER, NOW


def user_document(harness, *, service=False):
    old = copy.deepcopy(harness.document)
    manifest = copy.deepcopy(old["manifest"])
    manifest["version"] = 2
    if not service:
        manifest["key_sources"] = []
        manifest["slots"] = [{
            "name": "local-a", "role": "service", "delivery": "local",
            "routes": [{"prefix": "/api", "methods": ["GET", "POST"], "capabilities": ["http"]}],
        }]
    return {
        "schema": old["schema"], "version": 5, "manifest": manifest,
        "service": old if service else None,
        "user_callers": {
            "identity": {"provider": "user-provider", "profile": PROFILE,
                         "configuration": {"snapshot": "selected"}},
            "required_claims": [{"claim": "realm", "values": ["realm-a"]},
                                {"claim": "roles", "values": ["gateway-user"]}],
            "principal_claim": "user_id",
            "mappings": [{"subject": "issuer-user-a", "principal": "user-a"}],
            "callers": [{"principal": "user-a", "slots": ["service-a" if service else "local-a"],
                         "methods": ["GET", "POST"]}],
        },
    }


def provider(key, harness):
    trust = IdentityTrust(
        ISSUER, AUDIENCE, planning.canonical(public_jwks(key)), harness.clock.value + 3600)
    calls = []

    def prepare(configuration):
        assert configuration == b'{"snapshot":"selected"}'
        calls.append(configuration)
        return trust

    return IdentityProvider("user-provider", prepare), calls


def configured(key, *, service=False):
    h = Harness()
    h.document = user_document(h, service=service)
    selected, calls = provider(key, h)
    h.owner = ServiceAuthority(
        fleet="fleet-a", gateway="gateway-a", source=h.source, clock=h.clock,
        user_identity_provider=selected)
    return h, calls


def caller(h, key, *, method="POST", **changes):
    return h.owner.authenticate_user_caller(
        token(key, now=h.clock.value, **changes), method=method, request_method=method,
        target_uri=TARGET, slot=h.document["user_callers"]["callers"][0]["slots"][0])


def admission(h):
    now = h.clock.value
    current = [policy(
        identity_profile=PROFILE, valid_from=now - 1, valid_until=now + 3600,
        identity_trust_until=now + 3600, enrollments=(actor(subject="user-a"),))]
    return AdmissionAuthority(snapshot=lambda: current[0], clock=h.clock), current


def authorize(h, grant, owner):
    return h.owner.authorize_operation(
        grant, intent(), owner, authority="gateway-a", role="client", installation="client-a",
        checkpoint=lambda: None)


def test_local_only_needs_no_service_identity_key_source_or_enrollment(rsa_keys):
    h, calls = configured(rsa_keys[0])
    assert not h.reads and not calls
    snapshot = h.owner.reload().snapshot
    assert not snapshot.context.keys and not snapshot.context.manifest.key_sources
    assert snapshot.context.manifest.slots[0].principal is None
    assert snapshot.context.manifest.slots[0].key_source is None
    assert h.owner.user_profile() and not h.owner.machine_profile()
    grant = caller(h, rsa_keys[0])
    assert h.owner.revalidate_caller(grant).principal == "user-a"
    owner, current = admission(h)
    permit = authorize(h, grant, owner)
    assert permit.enrollment.principal == "actor-a"
    current[0] = replace(
        current[0], revision=2, enrollments=(replace(current[0].enrollments[0], status="revoked"),))
    with pytest.raises(AdmissionError, match="revoked-enrollment"):
        owner.complete(permit, intent())
    with pytest.raises(ServicePolicyError, match="unsupported-profile"):
        h.owner.authenticate_service(token(rsa_keys[0]), "", target_uri=TARGET, slot="local-a")
    with pytest.raises(ServicePolicyError, match="unsupported-profile"):
        h.owner.challenge()


def test_internal_source_authorization_keeps_original_user_admission(rsa_keys):
    h, _ = configured(rsa_keys[0], service=True)
    h.document["user_callers"]["callers"][0]["slots"].append("service-b")
    h.owner.reload()
    grant = caller(h, rsa_keys[0])
    assert h.owner.authorize_service_call(grant, slot="service-b").name == "service-b"
    original = h.owner.revalidate_caller(grant)
    assert original.principal == "user-a" and original.slot.name == "service-a"
    assert original.target_uri == TARGET
    h.document["user_callers"]["callers"][0]["slots"] = ["service-a"]
    h.document["service"]["manifest"]["revision"] += 1
    h.update()
    grant = caller(h, rsa_keys[0])
    with pytest.raises(ServicePolicyError, match="source-caller-denied"):
        h.owner.authorize_service_call(grant, slot="service-b")


@pytest.mark.parametrize("change", [
    "mapping", "claim", "shape", "trust", "caller", "route", "operation", "profile", "expiry",
])
def test_retained_operation_permit_obeys_current_independent_policy(rsa_keys, change):
    h, _ = configured(rsa_keys[0])
    h.owner.reload()
    owner, current = admission(h)
    permit = authorize(h, caller(h, rsa_keys[0]), owner)
    if change in ("operation", "profile", "expiry"):
        if change == "operation":
            current[0] = replace(
                current[0], revision=2,
                enrollments=(replace(current[0].enrollments[0], operations=()),))
        elif change == "profile":
            current[0] = replace(current[0], revision=2, identity_profile="jwt-pop-v1")
        else:
            h.clock.value += 301
    else:
        selected = h.document["user_callers"]
        if change == "mapping":
            selected["mappings"] = []
        elif change == "claim":
            selected["required_claims"][0]["values"] = ["other-realm"]
        elif change == "shape":
            selected["required_claims"][0]["shape"] = "scalar"
        elif change == "trust":
            selected["identity"]["configuration"] = {"snapshot": "changed"}
            trust = IdentityTrust(
                ISSUER, AUDIENCE, planning.canonical(public_jwks(rsa_keys[1])), NOW + 3600)
            h.owner._user_identity_provider = IdentityProvider("user-provider", lambda _: trust)
        elif change == "caller":
            selected["callers"][0]["methods"] = ["GET"]
        else:
            h.document["manifest"]["slots"][0]["routes"][0]["prefix"] = "/changed"
        h.update()
    with pytest.raises((ServicePolicyError, AdmissionError)):
        owner.complete(permit, intent())
    assert not owner._issued


def test_user_changes_do_not_replace_remote_service_proof_or_nonce(rsa_keys):
    h, _ = configured(rsa_keys[0], service=True)
    h.owner.reload()
    service = h.service()
    nonce = h.owner.challenge()
    user = caller(h, rsa_keys[0])
    h.document["manifest"]["revision"] += 1
    h.document["service"]["manifest"]["revision"] += 1
    h.document["user_callers"]["mappings"] = []
    h.owner.reload()
    assert h.owner.challenge() == nonce
    assert h.owner.revalidate_service(service).principal == "service-a"
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_caller(user)
    # A user credential never falls back to service/PoP admission.
    with pytest.raises(ServicePolicyError):
        h.owner.authenticate_caller(
            token(rsa_keys[0]), "invalid-proof", method="GET", request_method="GET",
            target_uri=TARGET, slot="service-a")


def test_normalized_user_policy_preserves_handles_and_mode_expiry_is_independent(rsa_keys):
    h, _ = configured(rsa_keys[0], service=True)
    h.document["service"]["identity"]["trust_valid_until"] = NOW + 100
    h.owner.reload()
    user = caller(h, rsa_keys[0])
    service = h.service()
    h.document["user_callers"]["required_claims"].reverse()
    assert not h.owner.reload().changed
    h.clock.value = NOW + 101
    assert h.owner.revalidate_caller(user).principal == "user-a"
    with pytest.raises(ServicePolicyError, match="authority-unavailable"):
        h.owner.revalidate_service(service)
    h.clock.value = NOW + 3600
    with pytest.raises(ServicePolicyError, match="authority-unavailable"):
        caller(h, rsa_keys[0])


def test_old_policy_cannot_select_new_local_slot_shape(rsa_keys):
    h, _ = configured(rsa_keys[0])
    old = Harness().document
    old["manifest"] = h.document["manifest"]
    h.document = old
    with pytest.raises(ServicePolicyError, match="unsupported-schema"):
        h.owner.reload()


@pytest.mark.parametrize("expired", ["user", "service"])
def test_revocation_publishes_when_the_other_mode_trust_has_expired(rsa_keys, expired):
    h, _ = configured(rsa_keys[0], service=True)
    if expired == "service":
        h.document["service"]["identity"]["trust_valid_until"] = NOW + 100
    else:
        trust = IdentityTrust(
            ISSUER, AUDIENCE, planning.canonical(public_jwks(rsa_keys[0])), NOW + 100)
        h.owner._user_identity_provider = IdentityProvider("user-provider", lambda _: trust)
    h.owner.reload()
    user, proof = caller(h, rsa_keys[0]), h.caller()
    h.clock.value = NOW + 101
    if expired == "service":
        h.document["user_callers"]["callers"] = []
        h.document["user_callers"]["mappings"] = []
        revoked = user
    else:
        h.document["service"]["callers"] = []
        revoked = proof
    h.document["manifest"]["revision"] += 1
    h.document["service"]["manifest"]["revision"] += 1
    assert h.owner.reload().changed
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_caller(revoked)


@pytest.mark.parametrize("change", [
    "provider", "old-schema", "local-remote", "service-scope", "duplicate-mapping",
    "ambiguous-principal", "unknown-field", "unknown-principal",
])
def test_explicit_closed_schema_and_mapping_grant_separation(rsa_keys, change):
    h, _ = configured(rsa_keys[0], service=change in ("service-scope", "ambiguous-principal"))
    if change == "provider":
        h.document["user_callers"]["identity"]["provider"] = "unregistered"
    elif change == "old-schema":
        h.document["version"] = 4
    elif change == "local-remote":
        h.document["manifest"]["slots"][0].update(principal="invented", key_source="invented")
    elif change == "service-scope":
        h.document["service"]["manifest"]["slots"][0]["routes"][0]["prefix"] = "/foreign"
    elif change == "duplicate-mapping":
        h.document["user_callers"]["mappings"] *= 2
    elif change == "ambiguous-principal":
        h.document["user_callers"]["callers"][0]["principal"] = "caller-a"
        h.document["user_callers"]["mappings"][0]["principal"] = "caller-a"
    elif change == "unknown-field":
        h.document["user_callers"]["callers"][0]["key_source"] = "not-a-proof"
    else:
        h.document["user_callers"]["mappings"][0]["principal"] = "ungranted"
    with pytest.raises(ServicePolicyError):
        h.owner.reload()
    assert h.owner.status().revision is None


def test_claim_shape_policy_validation_preserves_legacy_normalization(rsa_keys, monkeypatch):
    h, _ = configured(rsa_keys[0])
    original_digest = planning.digest
    observed = []

    def digest(value):
        if isinstance(value, dict) and "required_claims" in value:
            observed.append(value["required_claims"])
        return original_digest(value)

    monkeypatch.setattr(planning, "digest", digest)
    original = h.owner.reload().snapshot
    assert observed == [h.document["user_callers"]["required_claims"]]
    requirement = h.document["user_callers"]["required_claims"][0]
    for shape in (None, True, 1, [], {}, "", "either"):
        requirement["shape"] = shape
        h.document["manifest"]["revision"] += 1
        with pytest.raises(ServicePolicyError, match="^invalid-claim-shape$"):
            h.owner.reload()
        assert h.owner.status().revision == original.revision
    requirement.pop("shape")
    current = h.owner.reload().snapshot
    assert current.context.user_trust_digest == original.context.user_trust_digest


@pytest.mark.parametrize("shapes", [False, True])
def test_real_protected_tls_local_route_current_admission_and_mixed_auth(rsa_keys, tmp_path, shapes):
    async def scenario():
        rig = Rig(tmp_path)
        rig.h.document = user_document(rig.h)
        if shapes:
            requirements = rig.h.document["user_callers"]["required_claims"]
            requirements[0]["shape"], requirements[1]["shape"] = "scalar", "list"
        selected, _ = provider(rsa_keys[0], rig.h)
        owner, current = admission(rig.h)
        received, effects = [], []

        async def handle(local):
            received.append(local)
            assert local.request.context.principal == "user-a"
            assert all(name.lower() not in ("authorization", "cookie", "dpop")
                       for name, _ in local.request.headers)
            try:
                permit = local.authorize_operation(
                    intent(), owner, authority="gateway-a", role="client", installation="client-a")
                owner.complete(permit, intent())
            except AdmissionError:
                return ServiceResponse(403)
            effects.append("admitted")
            return ServiceResponse(200, (), b"admitted")

        slot = GatewayManifest.load(rig.h.document["manifest"]).slots[0]
        try:
            await rig.start(
                user_identity_provider=selected, local_handlers=(LocalHandlerBinding(slot, handle),))
            scheme = "Bear" + "er "
            access = token(rsa_keys[0], now=rig.h.clock.value)
            headers = {"Authorization": scheme + access}
            assert headers["Authorization"].endswith(access)
            if shapes:
                for claims in ({"realm": ["realm-a"]}, {"roles": "gateway-user"}):
                    denied = token(rsa_keys[0], now=rig.h.clock.value, claims=claims)
                    async with rig.session.post(
                        rig.origin + "/api/run",
                        headers={"Authorization": scheme + denied}) as response:
                        assert response.status == 401
                assert not received and not effects
            for spelling in (scheme, scheme.lower(), scheme.upper()):
                selected_headers = {"Authorization": spelling + access}
                async with rig.session.post(
                    rig.origin + "/api/run", headers=selected_headers) as response:
                    assert response.status == 200 and await response.read() == b"admitted"
                    assert response.headers["Cache-Control"] == "no-store"
            assert len(effects) == 3  # Repeated bearer still verifies as a NEW request, not PoP.
            for extra, code in (
                ({"DPoP": "synthetic"}, 401), ({"Fleet-Machine-Proof": "synthetic"}, 401),
                ({"Cookie": "synthetic"}, 401), ({"X-Identity": "user-a"}, 401),
                ({"Origin": "https://foreign.example"}, 403),
                ({"Sec-Fetch-Site": "cross-site"}, 403),
            ):
                async with rig.session.post(
                    rig.origin + "/api/run", headers=headers | extra) as response:
                    assert response.status == code
            async with rig.session.post(
                rig.origin + "/api/run",
                headers=[("Authorization", headers["Authorization"])] * 2) as response:
                assert response.status == 401
            async with rig.session.post(
                rig.origin + "/api/run", headers={"Cookie": "synthetic"}) as response:
                assert response.status == 401
            if shapes:
                requirements[0]["shape"] = "list"
                await rig.update()
                async with rig.session.post(rig.origin + "/api/run", headers=headers) as response:
                    assert response.status == 401
                access = token(
                    rsa_keys[0], now=rig.h.clock.value, claims={"realm": ["realm-a"]})
                headers = {"Authorization": scheme + access}
                async with rig.session.post(rig.origin + "/api/run", headers=headers) as response:
                    assert response.status == 200
            current[0] = replace(current[0], revision=2, enrollments=())
            async with rig.session.post(rig.origin + "/api/run", headers=headers) as response:
                assert response.status == 403
            successes = 3 + int(shapes)
            assert len(effects) == successes and len(received) == successes + 1 and not owner._issued
            assert not rig.state.channels and not rig.state.tickets
            assert not rig.state.authority._callers
        finally:
            await rig.close()
        assert not rig.state.local_active and not rig.tasks
    asyncio.run(scenario())
