"""Real signature evidence into isolated, current, correlated-scope decisions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fleet_manager.operations import OperationIntent
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AdmissionError, AuthoritySnapshot, Enrollment,
    MAX_ENROLLMENTS, MAX_GRANTS, MAX_PERMITS, ObservationRead, ObservationScope,
    OperationGrant, PERMIT_SECONDS, REQUEST_SECONDS,
)
from test_runtime_identity import AUDIENCE, ISSUER, NOW, TARGET, Clock, pair, verifier
from test_runtime_identity import keys as keys


A = ObservationScope("origin-a", "project-a", "entity-a", ("status",))
B = ObservationScope("origin-b", "project-b", "entity-b", ("title",))


def actor(subject="principal-1", **changes):
    return Enrollment(
        **{
            "subject": subject, "principal": "actor-a", "account": "account-a",
            "kind": "operator", "role": "client", "installation": "client-a",
            "generation": 1, "status": "active", "reads": (A, B),
            "operations": (OperationGrant("task.submit", "node-a", 7),),
            **changes,
        },
    )


def policy(**changes):
    return AuthoritySnapshot(**{
        "authority": "gateway-a", "fleet": "fleet-a", "issuer": ISSUER,
        "audience": AUDIENCE, "revision": 1, "valid_from": NOW - 1,
        "valid_until": NOW + 86400 * 31, "identity_trust_until": NOW + 86400 * 31,
        "enrollments": (actor(), actor(
            "principal-2", principal="actor-b", account="account-b",
            reads=(B,), operations=(),
        )),
        **changes,
    })


def intent(**changes):
    return OperationIntent.from_dict({
        "schema": "fleet-manager.operation-intent", "version": 1,
        "operation": "original-operation", "authority": "gateway-a",
        "principal": "actor-a", "account": "account-a", "target": "node-a",
        "generation": 7, "command": "task.submit", "parameters": {"objective": "job-a"},
        **changes,
    })


class Harness:
    def __init__(self, keys, **kwargs):
        self.keys = keys
        self.clock = Clock()
        self.verifier = verifier(keys, clock=self.clock)
        self.current = policy()
        self.calls = 0
        self.counter = 0
        self.owner = AdmissionAuthority(
            snapshot=self.snapshot, clock=self.clock, **kwargs,
        )

    def snapshot(self):
        self.calls += 1
        return self.current

    def identity(self, subject="principal-1", **claims):
        self.counter += 1
        credentials = pair(
            self.keys, self.verifier, now=self.clock.value,
            access={"sub": subject, **claims}, proof={"jti": f"proof-{self.counter}"},
        )
        return self.verifier.verify(*credentials, method="GET", target_uri=TARGET)

    def admit(self, request=None, subject="principal-1", **kwargs):
        return self.owner.authorize(
            self.identity(subject), request or ObservationRead("fleet-a"),
            role=kwargs.pop("role", "client"),
            installation=kwargs.pop("installation", "client-a"), **kwargs,
        )


@pytest.fixture
def harness(keys):
    return Harness(keys)


def refuses(code, function, *args, **kwargs):
    with pytest.raises(AdmissionError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert str(caught.value) == code


def test_real_two_principals_have_separate_views_and_no_token_role_grants(harness):
    first = harness.admit()
    second = harness.admit(subject="principal-2")
    assert first.enrollment.principal == "actor-a" and first.resources == (A, B)
    assert second.enrollment.principal == "actor-b" and second.resources == (B,)
    assert first.request_digest == second.request_digest
    refuses("forbidden-scope", harness.owner.authorize,
            harness.identity("principal-2", roles=["admin"]),
            ObservationRead("fleet-a", (A,)), role="client", installation="client-a")
    assert harness.calls == 3
    with pytest.raises(FrozenInstanceError):
        first.expires_at += 100


@pytest.mark.parametrize("branch", [
    ObservationScope("origin-a", "project-b", "entity-a", ("status",)),
    ObservationScope("origin-b", "project-a", "entity-b", ("title",)),
    ObservationScope("origin-a", "project-a", "entity-b", ("status",)),
    ObservationScope("origin-a", "project-a", "entity-a", ("title",)),
    ObservationScope("origin-a", "project-a", "entity-a", ("status", "title")),
    ObservationScope("origin-a-prefix", "project-a", "entity-a", ("status",)),
])
def test_no_cartesian_field_entity_or_prefix_widening(harness, branch):
    refuses("forbidden-scope", harness.admit, ObservationRead("fleet-a", (branch,)))


def test_empty_omitted_narrowed_and_foreign_fleet_filters(harness):
    refuses("forbidden-scope", harness.admit, ObservationRead("fleet-a", ()))
    refuses("forbidden-scope", harness.admit, ObservationRead("fleet-b"))
    assert harness.admit(ObservationRead("fleet-a", (A,))).resources == (A,)
    harness.current = policy(revision=2, enrollments=(actor(reads=()),))
    refuses("forbidden-scope", harness.admit)


@pytest.mark.parametrize("fields,included", [
    (("status",), True),
    (("title",), True),
    (("title", "status"), True),
    (("checkout_present",), False),
    (("status", "checkout_present"), False),
])
def test_scope_subset_comparison_preserves_exact_fields(fields, included):
    grant = ObservationScope("origin-a", "project-a", "entity-a", ("status", "title"))
    requested = ObservationScope("origin-a", "project-a", "entity-a", fields)
    assert grant.includes(requested) is included


@pytest.mark.parametrize("explicit", [False, True])
def test_effective_branches_reuse_canonical_request_or_enrollment(harness, explicit):
    request = ObservationRead("fleet-a", (B, A) if explicit else None)
    permit = harness.admit(request)
    selected = request.scopes if explicit else permit.enrollment.reads
    assert permit.resources is selected
    assert permit.resources == (A, B)
    assert harness.owner.revalidate(permit, request).resources is selected


@pytest.mark.parametrize("changes,code", [
    ({"principal": "actor-b"}, "actor-mismatch"),
    ({"account": "account-b"}, "actor-mismatch"),
    ({"authority": "other-gateway"}, "actor-mismatch"),
    ({"target": "node-b"}, "forbidden-scope"),
    ({"generation": 8}, "forbidden-scope"),
    ({"command": "prompt.deliver", "parameters": {
        "session": "session-a", "message_ref": "message-a",
    }}, "forbidden-scope"),
])
def test_operation_actor_action_and_generation_denied_before_lookup(harness, changes, code):
    request = intent(**changes)
    original = request.encoded
    refuses(code, harness.admit, request)
    assert request.encoded == original


def test_canonical_operation_binding_and_content_retry(harness):
    request = intent()
    first = harness.admit(request)
    second = harness.admit(request)
    assert first.request_digest == second.request_digest == request.request_digest
    assert first.action == "task.submit"
    assert first.resources == OperationGrant("task.submit", "node-a", 7)
    refuses("request-mismatch", harness.owner.revalidate, first,
            intent(parameters={"objective": "different-job"}))
    refuses("unknown-permit", harness.owner.revalidate, first, request)
    assert harness.owner.revalidate(second, request).request_digest == request.request_digest
    assert request.to_dict()["operation"] == "original-operation"


@pytest.mark.parametrize("changes,code", [
    ({"subject": "unknown"}, "unknown-enrollment"),
    ({"role": "admin"}, "unknown-enrollment"),
    ({"installation": "client-b"}, "unknown-enrollment"),
])
def test_explicit_enrollment_role_and_installation(harness, changes, code):
    refuses(code, harness.admit, **changes)


def test_unknown_revoked_unavailable_stale_and_expired_are_distinct(harness):
    harness.current = policy(enrollments=(actor(status="revoked"),))
    refuses("revoked-enrollment", harness.admit)
    harness.current = None
    refuses("authority-unavailable", harness.admit)
    harness.current = policy(revision=2, valid_until=NOW, enrollments=(actor(generation=2),))
    refuses("stale-policy", harness.admit)
    harness.current = policy(
        revision=3, identity_trust_until=NOW, enrollments=(actor(generation=2),),
    )
    refuses("stale-identity-trust", harness.admit)
    harness.current = policy(revision=4, enrollments=(actor(generation=2),))
    identity = harness.identity(exp=NOW + 1)
    harness.clock.value += 1
    refuses("expired-identity", harness.owner.authorize, identity,
            ObservationRead("fleet-a"), role="client", installation="client-a")


@pytest.mark.parametrize("changes,code", [
    ({"revision": 1, "enrollments": (actor(reads=(A,)),)}, "conflicting-policy"),
    ({"revision": 0}, "invalid-record"),
    ({"revision": 2, "authority": "other"}, "authority-changed"),
    ({"revision": 2, "fleet": "other"}, "authority-changed"),
    ({"revision": 2, "issuer": "https://other.example"}, "authority-changed"),
    ({"revision": 2, "audience": "other"}, "authority-changed"),
    ({"revision": 2, "enrollments": (actor(account="other"),)}, "conflicting-enrollment"),
])
def test_no_policy_conflict_or_implicit_namespace_replacement(harness, changes, code):
    harness.admit()
    if code == "invalid-record":
        refuses(code, policy, **changes)
    else:
        harness.current = policy(**changes)
        refuses(code, harness.admit)


def test_revision_and_enrollment_generation_rollback(harness):
    harness.current = policy(revision=2, enrollments=(actor(generation=2),))
    harness.admit()
    harness.current = policy(revision=1)
    refuses("stale-policy", harness.admit)
    harness.current = policy(revision=3)
    refuses("stale-enrollment", harness.admit)


@pytest.mark.parametrize("enrollment,code", [
    (actor(status="revoked"), "revoked-enrollment"),
    (actor(generation=2), "enrollment-changed"),
    (actor(reads=(B,)), "scope-changed"),
    (actor(reads=(A, B, ObservationScope("other", "other", "other", ("status",)))),
     "scope-changed"),
])
def test_current_scope_loss_widening_revocation_and_generation_invalidate(harness, enrollment, code):
    request = ObservationRead("fleet-a")
    permit = harness.admit(request)
    harness.current = policy(revision=2, enrollments=(enrollment,))
    refuses(code, harness.owner.revalidate, permit, request)
    refuses("unknown-permit", harness.owner.revalidate, permit, request)


def test_unrelated_policy_change_does_not_expand_or_restart_view(harness):
    request = ObservationRead("fleet-a", (A,))
    permit = harness.admit(request)
    harness.clock.value += 1
    harness.current = policy(revision=2, enrollments=(
        actor(), actor("principal-2", principal="actor-b", account="account-b", reads=()),
    ))
    current = harness.owner.revalidate(permit, request)
    assert current.resources == (A,)
    assert current.policy_revision == 2
    assert current.expires_at == permit.expires_at
    refuses("unknown-permit", harness.owner.revalidate, permit, request)


def test_snapshot_and_nested_grant_permutations_preserve_same_revision(harness):
    grants = (
        OperationGrant("task.submit", "node-a", 7),
        OperationGrant("task.submit", "node-b", 8),
    )
    initial = policy(enrollments=(
        actor(operations=grants),
        actor("principal-2", principal="actor-b", account="account-b", reads=(B,)),
    ))
    harness.current = initial
    request = ObservationRead("fleet-a")
    permit = harness.admit(request)
    reordered = replace(initial, enrollments=tuple(
        replace(item, reads=tuple(reversed(item.reads)),
                operations=tuple(reversed(item.operations)))
        for item in reversed(initial.enrollments)
    ))
    assert reordered == initial
    harness.current = reordered
    current = harness.owner.revalidate(permit, ObservationRead("fleet-a"))
    assert current.resources == (A, B) and current.policy_revision == initial.revision
    harness.current = replace(initial, enrollments=(actor(reads=(A,)),))
    refuses("conflicting-policy", harness.admit)


@pytest.mark.parametrize("original", [ObservationRead("fleet-a", (A, B)), intent()])
def test_revalidation_binds_canonical_action_digest_not_request_instance(harness, original):
    permit = harness.admit(original)
    if isinstance(original, OperationIntent):
        reconstructed = OperationIntent.from_dict(original.to_dict())
    else:
        reconstructed = ObservationRead("fleet-a", (B, A))
    assert reconstructed is not original
    current = harness.owner.revalidate(permit, reconstructed)
    assert (current.action, current.request_digest) == (permit.action, permit.request_digest)


def test_permits_cannot_be_reconstructed_transferred_or_extended(harness):
    request = ObservationRead("fleet-a")
    permit = harness.admit(request)
    retained = dict(harness.owner._issued)
    refuses("unknown-permit", harness.owner.revalidate, replace(permit), request)
    refuses("unknown-permit", harness.owner.revalidate,
            replace(permit, expires_at=NOW + 86400), request)
    assert harness.owner._issued == retained
    permit = harness.owner.revalidate(permit, request)
    other = AdmissionAuthority(snapshot=harness.snapshot, clock=harness.clock)
    refuses("unknown-permit", other.revalidate, permit, request)
    harness.clock.value = permit.expires_at
    refuses("expired-permit", harness.owner.revalidate, permit, request)


@pytest.mark.parametrize("bound", ["valid_until", "identity_trust_until"])
def test_derived_permit_never_outlives_authority_bounds(harness, bound):
    harness.current = policy(**{bound: NOW + 2})
    permit = harness.admit()
    assert permit.expires_at == NOW + 2
    harness.clock.value += 2
    refuses("expired-permit", harness.owner.revalidate, permit, ObservationRead("fleet-a"))


def test_evidence_is_one_request_even_after_denial_and_bounded_in_time(harness):
    identity = harness.identity()
    request = ObservationRead("fleet-b")
    refuses("forbidden-scope", harness.owner.authorize, identity, request,
            role="client", installation="client-a")
    refuses("identity-reused", harness.owner.authorize, identity,
            ObservationRead("fleet-a"), role="client", installation="client-a")
    harness.clock.value += REQUEST_SECONDS
    refuses("stale-identity", harness.owner.authorize, identity,
            ObservationRead("fleet-a"), role="client", installation="client-a")


def test_future_and_old_verification_times_have_distinct_prelookup_refusals(harness):
    identity = harness.identity()
    request = ObservationRead("fleet-a")
    harness.clock.value = NOW - 1
    refuses("invalid-identity", harness.owner.authorize, identity, request,
            role="client", installation="client-a")
    harness.clock.value = NOW + REQUEST_SECONDS
    refuses("stale-identity", harness.owner.authorize, identity, request,
            role="client", installation="client-a")
    assert harness.calls == 0 and not harness.owner._evidence


def test_capacity_refuses_without_evicting_live_handle_or_proof(harness):
    small = AdmissionAuthority(snapshot=harness.snapshot, clock=harness.clock, capacity=1)
    request = ObservationRead("fleet-a")
    identity = harness.identity()
    permit = small.authorize(identity, request, role="client", installation="client-a")
    refuses("resource-limit", small.authorize, harness.identity(), request,
            role="client", installation="client-a")
    assert small.revalidate(permit, request).expires_at == NOW + PERMIT_SECONDS
    harness.clock.value += PERMIT_SECONDS
    small.authorize(harness.identity(), request, role="client", installation="client-a")
    refuses("identity-reused", small.authorize, identity, request,
            role="client", installation="client-a")


def test_expiry_prunes_in_place_without_discarding_live_handles_or_evidence(harness):
    first = harness.admit()
    first_evidence = harness.owner._issued[id(first)].identity.evidence
    issued, evidence = harness.owner._issued, harness.owner._evidence
    harness.clock.value = NOW + 10
    second = harness.admit()
    second_evidence = issued[id(second)].identity.evidence
    harness.clock.value = NOW + PERMIT_SECONDS
    third = harness.admit()
    assert harness.owner._issued is issued and harness.owner._evidence is evidence
    assert id(first) not in issued and id(second) in issued and id(third) in issued
    assert first_evidence in evidence and second_evidence in evidence
    harness.owner.revalidate(second, ObservationRead("fleet-a"))
    harness.clock.value = NOW + REQUEST_SECONDS
    fourth = harness.admit()
    assert harness.owner._issued is issued and harness.owner._evidence is evidence
    assert list(issued) == [id(fourth)]
    assert first_evidence not in evidence and second_evidence in evidence


def test_exact_production_capacity_and_expired_fence_recovery(harness):
    request = ObservationRead("fleet-a", (A,))
    verified = harness.identity()
    permits = []
    # Capacity is a trusted-record boundary; real signature admission is covered above.
    for index in range(MAX_PERMITS):
        permits.append(harness.owner.authorize(
            replace(verified, evidence=f"{index:064x}"), request,
            role="client", installation="client-a",
        ))
    refuses("resource-limit", harness.admit, request)
    assert harness.owner.revalidate(permits[0], request).resources == (A,)
    harness.clock.value += REQUEST_SECONDS
    assert harness.admit(request).resources == (A,)


def test_long_provider_session_silently_renews_proofs_and_permits(harness):
    request = intent()
    for day in range(30):
        harness.clock.value = NOW + day * 86400
        permit = harness.admit(request)
        assert permit.request_digest == request.request_digest
        assert permit.expires_at == harness.clock.value + PERMIT_SECONDS
        assert harness.owner.revalidate(permit, request).expires_at == permit.expires_at
    assert harness.calls == 60


def test_snapshot_outage_and_exception_never_fall_back_to_cached_allow(harness):
    request = ObservationRead("fleet-a")
    permit = harness.admit(request)
    harness.current = None
    refuses("authority-unavailable", harness.owner.revalidate, permit, request)
    harness.current = policy(revision=2)
    refuses("unknown-permit", harness.owner.revalidate, permit, request)

    calls = []

    def broken():
        calls.append(True)
        raise RuntimeError("owner source failed")

    failing = AdmissionAuthority(snapshot=broken, clock=harness.clock)
    identity = harness.identity()
    with pytest.raises(RuntimeError, match="owner source failed"):
        failing.authorize(identity, request, role="client", installation="client-a")
    refuses("identity-reused", failing.authorize, identity, request,
            role="client", installation="client-a")
    assert calls == [True]
    with pytest.raises(RuntimeError, match="owner source failed"):
        failing.authorize(harness.identity(), request, role="client", installation="client-a")
    assert calls == [True, True]


def test_invalid_identity_is_refused_before_enrollment_resolution(harness):
    identity = harness.identity("unknown")
    identity = replace(identity, issuer="https://foreign.example")
    refuses("invalid-identity", harness.owner.authorize, identity,
            ObservationRead("fleet-a"), role="client", installation="client-a")


def test_revoked_generation_cannot_reactivate_without_owner_generation_change(harness):
    harness.current = policy(enrollments=(actor(status="revoked"),))
    refuses("revoked-enrollment", harness.admit)
    harness.current = policy(revision=2)
    refuses("stale-enrollment", harness.admit)
    harness.current = policy(revision=3, enrollments=(actor(generation=2),))
    assert harness.admit().enrollment.generation == 2


def test_final_clock_checkpoint_catches_expiry_during_authority_resolution(harness):
    identity = harness.identity(exp=NOW + 1)

    def slow():
        harness.clock.value += 1
        return harness.current

    owner = AdmissionAuthority(snapshot=slow, clock=harness.clock)
    refuses("expired-identity", owner.authorize, identity,
            ObservationRead("fleet-a"), role="client", installation="client-a")


def test_clock_rollback_and_concurrent_one_use_admission(harness):
    identity = harness.identity()
    request = ObservationRead("fleet-a")

    def call():
        try:
            return harness.owner.authorize(identity, request, role="client", installation="client-a")
        except AdmissionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: call(), range(8)))
    assert results.count("identity-reused") == 7
    harness.clock.value -= 1
    refuses("clock-rollback", harness.owner.revalidate,
            next(item for item in results if not isinstance(item, str)), request)


@pytest.mark.parametrize("function,args", [
    (ObservationScope, ("a", "b", "c", [])),
    (ObservationScope, ("a", "b", "c", ())),
    (ObservationScope, ("a", "b", "c", ("status", "status"))),
    (ObservationRead, ("fleet-a", [A])),
    (ObservationRead, ("fleet-a", (A,) * (MAX_GRANTS + 1))),
    (OperationGrant, ("task.submit", "node-a", True)),
    (Enrollment, ()),
])
def test_malformed_records_are_not_success_shaped(function, args):
    with pytest.raises((AdmissionError, TypeError)):
        function(*args)


def test_exact_snapshot_bounds_and_duplicate_actor_mappings():
    base = actor(reads=(), operations=())
    permitted = tuple(replace(
        base, subject=f"subject-{index}", principal=f"actor-{index}",
    ) for index in range(MAX_ENROLLMENTS))
    assert len(policy(enrollments=permitted).enrollments) == MAX_ENROLLMENTS
    refuses("invalid-record", policy, enrollments=permitted + (actor("extra"),))
    refuses("invalid-record", policy, enrollments=(actor(), actor()))
    refuses("invalid-record", policy, enrollments=(
        actor(), actor(role="other", account="other"),
    ))
    grants = tuple(OperationGrant("task.submit", f"target-{index}", 1)
                   for index in range(MAX_GRANTS))
    assert len(policy(enrollments=(actor(reads=(), operations=grants),)).enrollments) == 1
    refuses("invalid-record", policy, enrollments=(
        actor(reads=(A,), operations=grants),
    ))
    refuses("invalid-record", AdmissionAuthority, snapshot=lambda: policy(),
            clock=Clock(), capacity=MAX_PERMITS + 1)


def test_fresh_process_admission_import_does_not_load_optional_jose():
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import json, sys
from fleet_manager.runtime_admission import ObservationRead
value = ObservationRead("example")
assert value.scopes is None
assert not any(name.startswith(("joserfc", "cryptography")) for name in sys.modules)
print(json.dumps({"optional_crypto_loaded": False}))
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", script], env={**os.environ, "PYTHONPATH": str(source)},
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"optional_crypto_loaded": False}


def test_fresh_process_real_verifier_admission_and_scoped_store_read(tmp_path):
    script = """
import base64, hashlib
from dataclasses import asdict
import json, sys
from joserfc import jwt
from joserfc.jwk import ECKey
from fleet_manager.observation_store import ObservationStore
from fleet_manager.observations import CurrentUpdate, OriginBinding
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AdmissionError, AuthoritySnapshot, Enrollment,
    ObservationRead, ObservationScope,
)
from fleet_manager.runtime_identity import IdentityVerifier
now = 1_900_000_000
issuer, audience = "https://issuer.example", "example-gateway"
target = "https://gateway.example/v1/observations"
keys = (ECKey.generate_key("P-256"), ECKey.generate_key("P-256"))
verifier = IdentityVerifier(
    issuer=issuer, audience=audience,
    jwks={"keys": [{**keys[0].as_dict(private=False), "kid": "issuer-1"}]},
    trust_valid_until=now + 3600, clock=lambda: now,
)
A = ObservationScope("origin-a", "project-a", "entity-a", ("status",))
B = ObservationScope("origin-b", "project-b", "entity-b", ("title",))
snapshot = AuthoritySnapshot(
    authority="gateway-a", fleet="fleet-a", issuer=issuer, audience=audience,
    revision=1, valid_from=now - 1, valid_until=now + 3600,
    identity_trust_until=now + 3600,
    enrollments=tuple(Enrollment(
        subject=subject, principal=principal, account=account, kind="operator",
        role="client", installation="client-a", generation=1, status="active",
        reads=reads,
    ) for subject, principal, account, reads in (
        ("principal-1", "actor-a", "account-a", (A, B)),
        ("principal-2", "actor-b", "account-b", (B,)),
    )),
)
owner = AdmissionAuthority(snapshot=lambda: snapshot, clock=lambda: now)
store = ObservationStore(sys.argv[1], authority="fleet-a", installation="gateway-a")
for branch in (A, B):
    binding = OriginBinding(branch.origin, "installation", 1, "producer")
    store.bind_origin(binding)
    store.apply(CurrentUpdate.from_dict({
        "schema": "fleet-manager.current-update", "version": 1, **asdict(binding),
        "project": branch.project, "entity": branch.entity,
        "sequence": 1, "source_revision": 1, "observed_at": now,
        "availability": "current", "reason": None,
        "state": {"status": "active", "title": "not-selected"},
    }))
lookups = []
def consume(subject, requested):
    token = jwt.encode(
        {"alg": "ES256", "typ": "at+jwt", "kid": "issuer-1"},
        {"iss": issuer, "sub": subject, "aud": audience, "iat": now,
         "exp": now + 3600, "cnf": {"jkt": keys[1].thumbprint()}}, keys[0],
    )
    token_hash = base64.urlsafe_b64encode(
        hashlib.sha256(token.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    proof = jwt.encode(
        {"alg": "ES256", "typ": "dpop+jwt", "jwk": keys[1].as_dict(private=False)},
        {"jti": f"proof-{len(lookups)}", "htm": "GET", "htu": target, "iat": now,
         "ath": token_hash, "nonce": verifier.nonce}, keys[1],
    )
    identity = verifier.verify(token, proof, method="GET", target_uri=target)
    permit = owner.authorize(identity, requested, role="client", installation="client-a")
    permit = owner.revalidate(permit, requested)
    result = []
    for scope in permit.resources:
        lookups.append((scope.origin, scope.project, scope.entity))
        row = store.read(origin=scope.origin, project=scope.project, entity=scope.entity)
        result.append({key: row["row"]["update"]["state"][key] for key in scope.fields})
    return result
assert consume("principal-1", ObservationRead("fleet-a", (A,))) == [{"status": "active"}]
assert consume("principal-2", ObservationRead("fleet-a", (B,))) == [{"title": "not-selected"}]
before = list(lookups)
try:
    consume("principal-2", ObservationRead("fleet-a", (A,)))
except AdmissionError as error:
    assert error.code == "forbidden-scope"
else:
    raise AssertionError("foreign view was admitted")
assert lookups == before
assert not any(name.startswith("test_") for name in sys.modules)
print(json.dumps({"scoped_reads": len(lookups), "forbidden_lookups": 0}))
"""
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "observations.db")],
        env=env, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"scoped_reads": 2, "forbidden_lookups": 0}
