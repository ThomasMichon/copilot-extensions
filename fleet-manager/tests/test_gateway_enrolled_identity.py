"""Complete version-three publication, admission and actual TLS serving."""

import asyncio
import copy
from pathlib import Path
import runpy
import time

from joserfc.jwk import ECKey
import pytest

from fleet_manager import enrolled_identity, planning
from fleet_manager.gateway_service_context import parse_context
from fleet_manager.gateway_service_policy import ServicePolicyError
from gateway_service_transport_fixtures import Rig, eventually
from test_enrolled_identity import application_pair
from test_gateway_identity_provider import selected
from test_gateway_service_policy import Harness, TARGET
from test_runtime_identity import AUDIENCE, NOW


class EnrolledHarness(Harness):
    def __init__(self):
        super().__init__()
        self.provider, self.trust, self.calls = selected(self)
        self.document["version"] = 3
        self.document["identity"]["profile"] = "enrolled-pop-v1"
        self.document["enrollment"] = {
            "allowed_audiences": [AUDIENCE, "another-resource"],
            "mappings": [
                {"subject": "issuer-" + principal, "client_id": "client-" + principal,
                 "principal": principal, "kind": kind, "resource": AUDIENCE,
                 "key_source": principal, "expires_at": NOW + 600}
                for principal, kind in (("service-a", "service"), ("service-b", "service"),
                                        ("caller-a", "caller"))
            ],
        }

    def credentials(self, *, subject="service-a", key=1, method="POST", nonce=None,
                    target=TARGET, access=None, proof=None, signer=None):
        self.serial += 1
        return application_pair(
            (self.keys[0], self.keys[key]), nonce or self.owner.challenge(), now=self.clock.value,
            access={"sub": "issuer-" + subject, "client_id": "client-" + subject,
                    **(access or {})},
            proof={"htm": method, "htu": target, "jti": str(self.serial), **(proof or {})},
            signer=signer)


def test_actual_source_inert_provider_and_complete_immutable_context():
    h = EnrolledHarness()
    assert not h.calls and not h.reads
    assert Path(enrolled_identity.__file__).resolve() == (
        Path(__file__).parents[1] / "src/fleet_manager/enrolled_identity.py").resolve()
    context, verifier = parse_context(h.source(), h.clock, identity_provider=h.provider)
    assert type(verifier) is enrolled_identity.EnrolledIdentityVerifier
    assert context.trust_digest != planning.digest(h.document["identity"])
    h.owner.reload()
    service, caller = h.service(), h.caller()
    assert h.owner.revalidate_service(service).principal == "service-a"
    assert len(h.calls) == 2
    assert h.owner.revalidate_caller(caller).principal == "caller-a"
    assert service.expires_at == caller.expires_at == NOW + 600
    h.document["enrollment"]["mappings"][0]["expires_at"] = NOW
    assert h.owner.revalidate_service(service).principal == "service-a"


def test_exact_source_fixture_allowlist_includes_the_complete_current_package():
    root = Path(__file__).parents[2]
    source = runpy.run_path(str(root / "tools/clean-room/gateway-role-tunnels/source.py"))
    modules = {path.stem for path in (root / "fleet-manager/src/fleet_manager").glob("*.py")}
    assert source["PACKAGE_MODULES"] == modules


@pytest.mark.parametrize("version,profile,extra,code", [
    (2, "enrolled-pop-v1", False, "unsupported-profile"),
    (3, "jwt-pop-v1", True, "unsupported-profile"),
    (3, "enrolled-pop-v1", False, "invalid-fields"),
    (1, "enrolled-pop-v1", True, "invalid-fields"),
])
def test_opt_in_is_exact_not_a_legacy_schema_broadening(version, profile, extra, code):
    h = EnrolledHarness()
    h.document["version"] = version
    h.document["identity"]["profile"] = profile
    if not extra:
        del h.document["enrollment"]
    with pytest.raises(ServicePolicyError, match=f"^{code}$"):
        h.owner.reload()
    assert not h.calls


@pytest.mark.parametrize("change", [
    "missing-enrollment", "unknown-enrollment-field", "missing-mappings", "non-object",
    "empty-audiences", "duplicate-audiences", "missing-resource", "too-many-audiences",
    "malformed-audience", "too-many-mappings", "unknown-mapping-field", "missing-client",
    "bad-subject", "bad-client", "bad-principal", "unknown-principal", "wrong-source",
    "unknown-source", "wrong-resource", "wrong-kind", "invalid-kind", "bad-expiry", "ambiguous",
])
def test_strict_bounded_enrollment_schema(change):
    h = EnrolledHarness()
    envelope = h.document["enrollment"]
    mapping = envelope["mappings"][0]
    if change == "missing-enrollment":
        del h.document["enrollment"]
    elif change == "unknown-enrollment-field":
        envelope["secret"] = "synthetic-private-never-echo"
    elif change == "missing-mappings":
        del envelope["mappings"]
    elif change == "non-object":
        h.document["enrollment"] = []
    elif change == "empty-audiences":
        envelope["allowed_audiences"] = []
    elif change == "duplicate-audiences":
        envelope["allowed_audiences"] = [AUDIENCE, AUDIENCE]
    elif change == "missing-resource":
        envelope["allowed_audiences"] = ["another-resource"]
    elif change == "too-many-audiences":
        envelope["allowed_audiences"] = [AUDIENCE] + [f"resource-{i}" for i in range(16)]
    elif change == "malformed-audience":
        envelope["allowed_audiences"] = [AUDIENCE, {}]
    elif change == "too-many-mappings":
        envelope["mappings"] *= 44
    elif change == "unknown-mapping-field":
        mapping["revoked"] = False
    elif change == "missing-client":
        del mapping["client_id"]
    elif change == "bad-subject":
        mapping["subject"] = []
    elif change == "bad-client":
        mapping["client_id"] = ""
    elif change == "bad-principal":
        mapping["principal"] = "bad/principal"
    elif change == "unknown-principal":
        mapping["principal"] = "not-granted"
    elif change == "wrong-source":
        mapping["key_source"] = "caller-a"
    elif change == "unknown-source":
        mapping["key_source"] = "absent"
    elif change == "wrong-resource":
        mapping["resource"] = "another-resource"
    elif change == "wrong-kind":
        mapping["kind"] = "caller"
    elif change == "invalid-kind":
        mapping["kind"] = []
    elif change == "bad-expiry":
        mapping["expires_at"] = True
    else:
        envelope["mappings"].append({**mapping, "expires_at": NOW + 700})
    with pytest.raises(ServicePolicyError) as caught:
        h.owner.reload()
    assert "synthetic-private-never-echo" not in repr(caught.value)
    assert h.owner.status().revision is None


def test_same_principal_and_key_do_not_conflate_service_and_caller_kind():
    h = EnrolledHarness()
    h.document["callers"][0].update(principal="service-a", key_source="service-a")
    h.document["enrollment"]["mappings"].pop()
    h.owner.reload()
    grant = h.service()
    credentials = h.credentials(method="GET", access={"kind": "caller"})
    with pytest.raises(ServicePolicyError, match="^invalid-key-binding$"):
        h.owner.authenticate_caller(*credentials, method="GET", target_uri=TARGET,
                                    slot="service-a", request_method="GET")
    mapping = h.document["enrollment"]["mappings"][0]
    h.document["enrollment"]["mappings"].append({**mapping, "kind": "caller"})
    h.update()
    caller = h.owner.authenticate_caller(*h.credentials(method="GET"), method="GET",
                                         target_uri=TARGET, slot="service-a", request_method="GET")
    assert h.owner.revalidate_caller(caller).principal == "service-a"
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_service(grant)
    h.document["enrollment"]["mappings"] = [
        item for item in h.document["enrollment"]["mappings"] if item["kind"] == "caller"]
    h.update()
    with pytest.raises(ServicePolicyError, match="^invalid-key-binding$"):
        h.service()


def test_full_conjunction_includes_current_route_scope_and_resolved_keys():
    h = EnrolledHarness()
    h.owner.reload()
    for options in (
        {"subject": "service-b"}, {"key": 2}, {"access": {"client_id": "client-caller-a"}},
        {"access": {"sub": "issuer-caller-a"}}, {"access": {"aud": "another-resource"}},
    ):
        with pytest.raises(ServicePolicyError):
            h.service(**options)
    with pytest.raises(ServicePolicyError, match="^caller-denied$"):
        h.caller(slot="service-b")
    with pytest.raises(ServicePolicyError, match="^caller-denied$"):
        h.caller(method="DELETE")
    caller = h.caller()
    h.document["callers"][0]["methods"] = ["POST"]
    h.update()
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_caller(caller)


def test_equivalent_mapping_order_preserves_nonce_handles_and_replays():
    h = EnrolledHarness()
    h.owner.reload()
    caller = h.caller()
    credentials = h.credentials()
    h.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")
    grant = h.service()
    nonce = h.owner.challenge()
    h.document["enrollment"]["allowed_audiences"].reverse()
    h.document["enrollment"]["mappings"].reverse()
    h.document["bindings"].reverse()
    assert not h.owner.reload().changed
    h.update()
    assert h.owner.challenge() == nonce
    assert h.owner.revalidate_service(grant).name == "service-a"
    assert h.owner.revalidate_caller(caller).principal == "caller-a"
    with pytest.raises(ServicePolicyError, match="^proof-replayed$"):
        h.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")


@pytest.mark.parametrize("change", [
    "withdraw", "client", "subject", "expiry", "proof-key", "key-reference", "key-source",
    "principal", "kind", "audience", "allowed-audience", "configuration",
])
def test_every_material_mapping_input_replaces_epoch_and_withdraws_old_authority(change):
    h = EnrolledHarness()
    h.owner.reload()
    grant, caller = h.service(), h.caller()
    pending = h.owner.prepare_service(*h.credentials(), target_uri=TARGET, slot="service-a")
    unconsumed = h.credentials()
    nonce = h.owner.challenge()
    mapping = h.document["enrollment"]["mappings"][0]
    if change == "withdraw":
        h.document["enrollment"]["mappings"].pop(0)
    elif change == "client":
        mapping["client_id"] = "replacement-client"
    elif change == "subject":
        mapping["subject"] = "replacement-subject"
    elif change == "expiry":
        mapping["expires_at"] += 1
    elif change == "proof-key":
        h.document["bindings"][0]["jwk"] = ECKey.generate_key("P-256").as_dict(private=False)
    elif change == "key-reference":
        h.document["manifest"]["key_sources"][0]["reference"] = "keys/replacement"
        h.document["bindings"][0]["reference"] = "keys/replacement"
    elif change == "key-source":
        mapping["key_source"] = "service-b"
        h.document["manifest"]["slots"][0]["key_source"] = "service-b"
    elif change == "principal":
        mapping["principal"] = "replacement"
        h.document["manifest"]["slots"][0]["principal"] = "replacement"
    elif change == "kind":
        mapping["kind"] = "caller"
        h.document["callers"].append({
            "principal": "service-a", "key_source": "service-a",
            "slots": ["service-a"], "methods": ["GET"]})
    elif change == "audience":
        h.trust["audience"] = "replacement-resource"
        h.document["enrollment"]["allowed_audiences"].append("replacement-resource")
        for item in h.document["enrollment"]["mappings"]:
            item["resource"] = "replacement-resource"
    elif change == "allowed-audience":
        h.document["enrollment"]["allowed_audiences"].append("additional-resource")
    else:
        h.document["identity"]["configuration"]["snapshot"] = "replacement"
    h.update()
    assert h.owner.challenge() != nonce
    for validate, handle in (
        (h.owner.revalidate_service, grant), (h.owner.revalidate_caller, caller),
        (h.owner.revalidate_candidate, pending),
    ):
        with pytest.raises(ServicePolicyError):
            validate(handle)
    with pytest.raises(ServicePolicyError):
        h.owner.authenticate_service(*unconsumed, target_uri=TARGET, slot="service-a")


def test_invalid_partial_refresh_keeps_bounded_last_good_and_expiry():
    h = EnrolledHarness()
    h.trust["trust_valid_until"] = NOW + 20
    h.document["enrollment"]["mappings"][0]["expires_at"] = NOW + 10
    h.owner.reload()
    grant = h.service()
    nonce = h.owner.challenge()
    h.document["enrollment"]["mappings"][0]["expires_at"] = NOW + 1000
    del h.document["enrollment"]["mappings"][1]["client_id"]
    h.document["manifest"]["revision"] += 1
    with pytest.raises(ServicePolicyError, match="^invalid-fields$"):
        h.owner.reload()
    assert h.owner.status().revision == 1
    assert h.owner.challenge() == nonce
    assert h.owner.revalidate_service(grant).name == "service-a"
    h.clock.value = NOW + 10
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_service(grant)
    with pytest.raises(ServicePolicyError, match="^expired-credential$"):
        h.service()
    h.clock.value = NOW + 20
    with pytest.raises(ServicePolicyError, match="^authority-unavailable$"):
        h.owner.challenge()


def test_empty_enrollment_revokes_all_and_exact_bounded_capacity_is_valid():
    h = EnrolledHarness()
    template = h.document["enrollment"]["mappings"][0]
    h.document["enrollment"]["mappings"] = [
        {**template, "subject": f"issuer-subject-{i}"} for i in range(128)]
    h.document["enrollment"]["allowed_audiences"] = [
        AUDIENCE, *(f"resource-{i}" for i in range(15))]
    h.owner.reload()
    grant = h.service(access={"sub": "issuer-subject-127"})
    assert h.owner.revalidate_service(grant).principal == "service-a"
    h.document["enrollment"]["mappings"] = []
    h.update()
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_service(grant)
    with pytest.raises(ServicePolicyError, match="^invalid-key-binding$"):
        h.service(access={"sub": "issuer-subject-127"})


def test_real_key_rotation_requires_new_key_and_fresh_challenge():
    h = EnrolledHarness()
    h.owner.reload()
    credentials, nonce = h.credentials(), h.owner.challenge()
    grant = h.owner.authenticate_service(*credentials, target_uri=TARGET, slot="service-a")
    old_keys = h.keys
    replacement = ECKey.generate_key("P-256")
    h.document["bindings"][0]["jwk"] = replacement.as_dict(private=False)
    h.update()
    assert h.owner.challenge() != nonce
    with pytest.raises(ServicePolicyError):
        h.owner.revalidate_service(grant)
    with pytest.raises(ServicePolicyError, match="^invalid-key-binding$"):
        h.service()
    h.keys = (h.keys[0], replacement, *h.keys[2:])
    grant = h.service()
    assert h.owner.revalidate_service(grant).name == "service-a"
    h.keys = old_keys
    with pytest.raises(ServicePolicyError):
        h.service()


class EnrolledRig(Rig):
    def __init__(self, root):
        super().__init__(root)
        self.h = EnrolledHarness()
        self.h.clock.value = int(time.time())
        self.h.trust["trust_valid_until"] = self.h.clock.value + 1000
        self.h.document["callers"][0]["slots"].append("service-b")
        for mapping in self.h.document["enrollment"]["mappings"]:
            mapping["expires_at"] = self.h.clock.value + 600
        self.token_overrides = {}

    def access(self, subject, key):
        return application_pair(
            (self.h.keys[0], self.h.keys[key]), "unused", now=self.h.clock.value,
            access={"sub": "issuer-" + subject, "client_id": "client-" + subject,
                    "aud": [AUDIENCE, "another-resource"], **self.token_overrides})[0]


def test_real_tls_atomic_key_replacement_withdraws_old_channel_and_admits_new_key(tmp_path):
    async def scenario():
        rig = EnrolledRig(tmp_path)
        try:
            await rig.start(identity_provider=rig.h.provider)
            await rig.connect()
            old = rig.state.channels["service-a"]
            nonce = rig.state.authority.challenge()
            replacement = ECKey.generate_key("P-256")
            rig.h.document["bindings"][0]["jwk"] = replacement.as_dict(private=False)
            await rig.update()
            await eventually(lambda: old.closed)
            assert rig.state.authority.challenge() != nonce
            with pytest.raises(ServicePolicyError):
                rig.state.authority.revalidate_service(old.grant)
            selected_headers = await rig.auth(
                "/_fleet/service-admit", "POST", subject="service-a", key=1)
            scheme = "DP" + "oP "
            async with rig.session.post(
                rig.origin + "/_fleet/service-admit",
                json={"slot": "service-a",
                      "access": selected_headers["Authorization"][len(scheme):],
                      "proof": selected_headers["DPoP"]},
            ) as response:
                assert response.status == 401
                assert (await response.json())["error"] == "authorization-denied"
            rig.h.keys = (rig.h.keys[0], replacement, *rig.h.keys[2:])
            await rig.connect()
            current = rig.state.channels["service-a"]
            assert current is not old and not current.closed
            assert rig.state.authority.revalidate_service(current.grant).name == "service-a"
            async with await rig.request() as response:
                assert response.status == 200
                assert (await response.json())["service"] == "a"
        finally:
            await rig.close()

    asyncio.run(scenario())


def test_registered_profile_reaches_real_tls_admission_routing_and_withdrawal(tmp_path):
    async def scenario():
        rig = EnrolledRig(tmp_path)
        try:
            await rig.start(identity_provider=rig.h.provider)
            await rig.connect()
            old = rig.state.channels["service-a"]
            nonce = rig.state.authority.challenge()
            async with await rig.request() as response:
                assert response.status == 200
                assert (await response.json())["service"] == "a"
            rig.h.document["enrollment"]["mappings"].reverse()
            await rig.update()
            assert rig.state.channels["service-a"] is old
            assert rig.state.authority.challenge() == nonce
            for changes in (
                {"client_id": "unapproved"}, {"aud": [AUDIENCE, "unapproved"]},
                {"cnf": {"jkt": rig.h.keys[1].thumbprint()}},
            ):
                rig.token_overrides = changes
                async with await rig.request() as response:
                    assert response.status == 401
            rig.token_overrides = {}
            headers = await rig.auth("/api")
            async with rig.session.get(rig.origin + "/api", headers=headers) as response:
                assert response.status == 200
            async with rig.session.get(rig.origin + "/api", headers=headers) as response:
                assert response.status == 401
            assert len(rig.backends[0].requests) == 2
            rig.h.document["enrollment"]["mappings"] = [
                item for item in rig.h.document["enrollment"]["mappings"]
                if item["kind"] != "service"]
            await rig.update()
            await eventually(lambda: old.closed)
            assert rig.state.authority.challenge() != nonce
            before = len(rig.backends[0].requests)
            async with await rig.request() as response:
                assert response.status == 503
            assert len(rig.backends[0].requests) == before
        finally:
            await rig.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["withdrawal", "expiry"])
def test_live_caller_revocation_bounds_pending_response_without_service_reenrollment(
        tmp_path, change):
    async def scenario():
        rig = EnrolledRig(tmp_path)
        pending = None
        try:
            now = rig.h.clock.value
            for mapping in rig.h.document["enrollment"]["mappings"]:
                if mapping["kind"] == "caller":
                    mapping["expires_at"] = now + 2
            await rig.start(identity_provider=rig.h.provider)
            await rig.connect()
            old = rig.state.channels["service-a"]
            pending = asyncio.create_task(rig.request("/api/hold"))
            await asyncio.wait_for(rig.backends[0].started.wait(), 3)
            if change == "expiry":
                rig.h.clock.value = now + 2
            else:
                # Resource-grant removal alone retains the same enrolled crypto epoch.
                rig.h.document["callers"][0]["slots"] = ["service-b"]
                await rig.update()
            async with await asyncio.wait_for(pending, 3) as response:
                assert response.status == 403
            assert rig.state.channels["service-a"] is old and not old.closed
            assert rig.backends[0].requests[0][1] == "/hold"
        finally:
            if pending:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await rig.close()

    asyncio.run(scenario())


def test_invalid_live_refresh_preserves_channel_without_extending_enrollment(tmp_path):
    async def scenario():
        rig = EnrolledRig(tmp_path)
        try:
            now = rig.h.clock.value
            rig.h.document["enrollment"]["mappings"][0]["expires_at"] = now + 3
            await rig.start(identity_provider=rig.h.provider)
            await rig.connect()
            old = rig.state.channels["service-a"]
            original = copy.deepcopy(rig.h.document)
            nonce = rig.state.authority.challenge()
            rig.h.document["enrollment"]["mappings"][0]["expires_at"] = now + 1000
            del rig.h.document["enrollment"]["mappings"][1]["client_id"]
            rig.save()
            await eventually(lambda: rig.state.authority.status().last_error == "invalid-fields")
            assert rig.state.authority.challenge() == nonce
            async with await rig.request() as response:
                assert response.status == 200
            assert rig.state.context.manifest.revision == original["manifest"]["revision"]
            rig.h.clock.value = now + 3
            await eventually(lambda: old.closed)
            assert rig.state.authority.challenge() == nonce
        finally:
            await rig.close()

    asyncio.run(scenario())
