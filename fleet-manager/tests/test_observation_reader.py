"""Real identity and SQLite reads across an explicit local policy fence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from fleet_manager.observation_reader import FENCE_WAIT_SECONDS, ScopedObservationReader
from fleet_manager.observation_store import ObservationStore
from fleet_manager.observations import CurrentUpdate, OriginBinding
from fleet_manager.planning import PlanError
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AdmissionError, ObservationRead, ObservationScope,
)
from test_runtime_admission import A, B, Harness, actor, intent, policy, refuses
from test_runtime_identity import NOW
from test_runtime_identity import keys as keys


class Fence:
    def __init__(self):
        self.lock = threading.RLock()
        self.timeouts = []
        self.releases = 0

    def acquire(self, *, timeout):
        self.timeouts.append(timeout)
        return self.lock.acquire(timeout=timeout)

    def release(self):
        self.releases += 1
        self.lock.release()


def update(binding, branch, *, sequence=1, **changes):
    return CurrentUpdate.from_dict({
        "schema": "fleet-manager.current-update", "version": 1,
        **asdict(binding), "project": branch.project, "entity": branch.entity,
        "sequence": sequence, "source_revision": sequence, "observed_at": NOW,
        "availability": "current", "reason": None,
        "state": {"status": "active", "title": f"private-{branch.origin}"},
        **changes,
    })


class ReaderHarness(Harness):
    def __init__(self, keys, path, monkeypatch):
        self.fence = Fence()
        super().__init__(keys, capacity=1, fence=self.fence)
        self.store = ObservationStore(path, authority="gateway-a", installation="gateway-a")
        self.bindings = {}
        for branch in (A, B):
            binding = OriginBinding(branch.origin, "installation", 1, "producer")
            self.bindings[branch.origin] = binding
            self.store.bind_origin(binding)
            self.store.apply(update(binding, branch))
        self.reader = ScopedObservationReader(
            authority=self.owner, store=self.store, fence=self.fence,
        )
        self.lookups = []
        self.original_read = self.store.read

        def read(**scope):
            self.lookups.append(scope)
            return self.original_read(**scope)

        monkeypatch.setattr(self.store, "read", read)

    def read(self, request=None, *, subject="principal-1", identity=None, **kwargs):
        return self.reader.read(
            identity or self.identity(subject), request or ObservationRead("fleet-a", (A,)),
            role=kwargs.pop("role", "client"),
            installation=kwargs.pop("installation", "client-a"), **kwargs,
        )


@pytest.fixture
def reader(keys, tmp_path, monkeypatch):
    return ReaderHarness(keys, tmp_path / "observations.db", monkeypatch)


def test_real_principals_get_only_exact_authorized_point_fields(reader):
    first = reader.read()
    second = reader.read(ObservationRead("fleet-a", (B,)), subject="principal-2")
    assert first.state == (("status", "active"),)
    assert second.state == (("title", "private-origin-b"),)
    assert first.source_revision == first.generation == 1
    assert first.observed_at == NOW and first.coverage == "point-observation"
    assert reader.lookups == [
        {"origin": A.origin, "project": A.project, "entity": A.entity},
        {"origin": B.origin, "project": B.project, "entity": B.entity},
    ]
    encoded = json.dumps(asdict(first))
    for forbidden in (
        "private-", "cursor", "position", "sequence", "producer", "installation",
        "account", "enrollment", "permit",
    ):
        assert forbidden not in encoded
    assert not reader.owner._issued
    with pytest.raises(FrozenInstanceError):
        first.availability = "other"
    with pytest.raises(TypeError):
        first.state[0] = ("status", "other")


@pytest.mark.parametrize("selection", [
    ObservationRead("fleet-a"), ObservationRead("fleet-a", ()),
    ObservationRead("fleet-a", (A, B)), intent(), object(),
])
def test_unsupported_read_shape_never_opens_fence_or_store(reader, selection):
    refuses("unsupported-read", reader.read, selection)
    assert not reader.lookups and not reader.fence.timeouts
    assert not reader.owner._issued


@pytest.mark.parametrize("branch", [
    A,
    ObservationScope("origin-b", "project-a", "entity-b", ("title",)),
    ObservationScope("origin-b", "project-b", "entity-a", ("title",)),
    ObservationScope("origin-b", "project-b", "entity-b", ("status",)),
    ObservationScope("origin-b", "project-b", "entity-b", ("status", "title")),
])
def test_denied_actor_and_correlated_scope_never_reach_point_lookup(reader, branch):
    refuses("forbidden-scope", reader.read,
            ObservationRead("fleet-a", (branch,)), subject="principal-2")
    assert not reader.lookups and not reader.owner._issued


@pytest.mark.parametrize("changes,code", [
    ({"role": "unknown"}, "unknown-enrollment"),
    ({"installation": "unknown"}, "unknown-enrollment"),
    ({"subject": "unknown"}, "unknown-enrollment"),
])
def test_unknown_enrollment_never_queries(reader, changes, code):
    refuses(code, reader.read, **changes)
    assert not reader.lookups


def test_foreign_fleet_and_store_authority_never_query(reader, tmp_path, monkeypatch):
    refuses("forbidden-scope", reader.read, ObservationRead("other-fleet", (A,)))
    foreign = ObservationStore(tmp_path / "foreign.db", authority="other", installation="gateway-a")
    monkeypatch.setattr(foreign, "read", lambda **_: pytest.fail("foreign store queried"))
    adapter = ScopedObservationReader(authority=reader.owner, store=foreign, fence=reader.fence)
    refuses("authority-mismatch", adapter.read, reader.identity(),
            ObservationRead("fleet-a", (A,)), role="client", installation="client-a")
    assert not reader.lookups and not reader.owner._issued


def test_fence_is_required_and_cannot_be_replaced_by_an_unusable_record(reader):
    with pytest.raises(TypeError):
        ScopedObservationReader(authority=reader.owner, store=reader.store)
    refuses("invalid-record", ScopedObservationReader,
            authority=reader.owner, store=reader.store, fence=object())
    unfenced = AdmissionAuthority(snapshot=reader.snapshot, clock=reader.clock)
    refuses("invalid-record", ScopedObservationReader,
            authority=unfenced, store=reader.store, fence=reader.fence)
    refuses("invalid-record", ScopedObservationReader,
            authority=reader.owner, store=reader.store, fence=Fence())
    assert not reader.lookups


def test_unobserved_removed_and_resync_are_not_complete_or_absent_claims(reader):
    missing = ObservationScope(A.origin, A.project, "missing", ("status",))
    reader.current = policy(revision=2, enrollments=(actor(reads=(A, missing)),))
    result = reader.read(ObservationRead("fleet-a", (missing,)))
    assert result.state is result.generation is result.source_revision is None
    assert (result.availability, result.reason, result.coverage) == (
        "unknown", "unobserved", "point-observations-only",
    )
    binding = reader.bindings[A.origin]
    reader.store.apply(update(binding, A, sequence=2, state=None, reason="projection-removed"))
    removed = reader.read()
    assert removed.state is None and removed.reason == "projection-removed"
    reader.store.bind_origin(replace(binding, generation=2, producer="new"), expected=binding)
    stale = reader.read()
    assert stale.generation == 1 and stale.coverage == "origin-resync-required"


def test_absent_selected_fields_are_not_synthesized_as_null(reader):
    branch = replace(A, fields=("status", "title", "checkout_present"))
    reader.current = policy(revision=2, enrollments=(actor(reads=(branch,)),))
    reader.store.apply(update(
        reader.bindings[A.origin], A, sequence=2,
        state={"status": "active", "checkout_present": None},
    ))
    result = reader.read(ObservationRead("fleet-a", (branch,)))
    assert result.state == (("checkout_present", None), ("status", "active"))


@pytest.mark.parametrize("change,code", [
    ("revoked", "revoked-enrollment"), ("generation", "enrollment-changed"),
    ("scope", "forbidden-scope"), ("policy", "stale-policy"),
    ("trust", "stale-identity-trust"), ("expiry", "expired-permit"),
    ("clock", "clock-rollback"), ("unavailable", "authority-unavailable"),
])
def test_final_checkpoint_withholds_read_after_authority_changes(reader, monkeypatch, change, code):
    def changed(**scope):
        result = reader.original_read(**scope)
        if change == "expiry":
            reader.clock.value += 30
        elif change == "clock":
            reader.clock.value -= 1
        elif change == "unavailable":
            reader.current = None
        else:
            changes = {"revision": 2}
            if change == "revoked":
                changes["enrollments"] = (actor(status="revoked"),)
            elif change == "generation":
                changes["enrollments"] = (actor(generation=2),)
            elif change == "scope":
                changes["enrollments"] = (actor(reads=(B,)),)
            elif change == "policy":
                changes["valid_until"] = NOW
            elif change == "trust":
                changes["identity_trust_until"] = NOW
            reader.current = policy(**changes)
        return result

    monkeypatch.setattr(reader.store, "read", changed)
    refuses(code, reader.read)
    assert not reader.owner._issued
    assert reader.fence.releases == len(reader.fence.timeouts)


def test_storage_and_final_callback_failures_are_errors_not_empty_success(reader, monkeypatch):
    def broken(**_):
        raise PlanError("storage-unavailable")

    monkeypatch.setattr(reader.store, "read", broken)
    with pytest.raises(PlanError, match="storage-unavailable"):
        reader.read()
    assert not reader.owner._issued
    monkeypatch.setattr(reader.store, "read", reader.original_read)
    assert reader.read().state == (("status", "active"),)

    def unavailable():
        reader.calls += 1
        if reader.calls % 2 == 0:
            raise RuntimeError("owner-unavailable")
        return reader.current

    reader.owner._snapshot = unavailable
    reader.calls = 0
    with pytest.raises(RuntimeError, match="owner-unavailable"):
        reader.read()
    assert not reader.owner._issued


def test_terminal_completion_preserves_identity_replay_and_exact_permit_identity(reader):
    identity = reader.identity()
    request = ObservationRead("fleet-a", (A,))
    permit = reader.owner.authorize(identity, request, role="client", installation="client-a")
    refuses("unknown-permit", reader.owner.complete, replace(permit), request)
    reader.owner.complete(permit, request)
    refuses("unknown-permit", reader.owner.revalidate, permit, request)
    refuses("identity-reused", reader.read, identity=identity)
    assert not reader.lookups and not reader.owner._issued
    assert reader.read().state == (("status", "active"),)


def test_terminal_request_mismatch_consumes_only_the_matching_retained_handle(reader):
    selection = ObservationRead("fleet-a", (A,))
    permit = reader.owner.authorize(
        reader.identity(), selection, role="client", installation="client-a",
    )
    refuses("request-mismatch", reader.owner.complete,
            permit, ObservationRead("fleet-a", (B,)))
    assert not reader.owner._issued
    refuses("unknown-permit", reader.owner.complete, permit, selection)
    assert reader.read().state == (("status", "active"),)


def test_real_owner_fence_timeout_does_not_unlock_another_reader(reader):
    identity = reader.identity()
    with ThreadPoolExecutor(max_workers=1) as pool, reader.fence.lock:
        future = pool.submit(reader.read, identity=identity)
        with pytest.raises(AdmissionError, match="authority-busy"):
            future.result(timeout=3)
    assert reader.fence.timeouts == [FENCE_WAIT_SECONDS]
    assert reader.fence.releases == 0 and not reader.lookups


def test_participating_revocation_writer_is_serialized_with_real_read(reader, monkeypatch):
    entered, release, attempted = (threading.Event() for _ in range(3))

    def paused(**scope):
        entered.set()
        assert release.wait(3)
        return reader.original_read(**scope)

    def revoke():
        attempted.set()
        with reader.fence.lock:
            reader.current = policy(revision=2, enrollments=(actor(status="revoked"),))

    monkeypatch.setattr(reader.store, "read", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(reader.read)
        assert entered.wait(3)
        writing = pool.submit(revoke)
        assert attempted.wait(3) and not writing.done()
        release.set()
        assert reading.result(timeout=3).state == (("status", "active"),)
        writing.result(timeout=3)
    monkeypatch.setattr(reader.store, "read",
                        lambda **_: pytest.fail("revoked principal reached storage"))
    refuses("revoked-enrollment", reader.read)


def test_dependency_free_import_and_real_fresh_process_consumer(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(source)}
    imported = subprocess.run(
        [sys.executable, "-S", "-c", """
import sys
from fleet_manager.observation_reader import ScopedObservationReader
assert not any(name.startswith(("joserfc", "cryptography")) for name in sys.modules)
"""],
        env=env, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert imported.returncode == 0, imported.stderr
    script = """
import base64, hashlib, json, sys, threading
from dataclasses import asdict
from joserfc import jwt
from joserfc.jwk import ECKey
from fleet_manager.observation_reader import ScopedObservationReader
from fleet_manager.observation_store import ObservationStore
from fleet_manager.observations import CurrentUpdate, OriginBinding
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AdmissionError, AuthoritySnapshot, Enrollment,
    ObservationRead, ObservationScope,
)
from fleet_manager.runtime_identity import IdentityVerifier
now = 1_900_000_000
issuer, audience, target = "https://issuer.example", "example", "https://gateway.example/read"
signer, proof_key = ECKey.generate_key("P-256"), ECKey.generate_key("P-256")
verifier = IdentityVerifier(
    issuer=issuer, audience=audience,
    jwks={"keys": [{**signer.as_dict(private=False), "kid": "issuer-1"}]},
    trust_valid_until=now + 3600, clock=lambda: now,
)
scope = ObservationScope("origin-a", "project-a", "entity-a", ("status",))
other = ObservationScope("origin-a", "project-a", "entity-b", ("title",))
policy = AuthoritySnapshot(
    authority="gateway-a", fleet="fleet-a", issuer=issuer, audience=audience,
    revision=1, valid_from=now - 1, valid_until=now + 3600,
    identity_trust_until=now + 3600,
    enrollments=tuple(Enrollment(
        subject=subject, principal=subject, account=subject, kind="operator",
        role="client", installation="client-a", generation=1, status="active", reads=reads,
    ) for subject, reads in (("first", (scope, other)), ("second", ()))),
)
fence = threading.RLock()
owner = AdmissionAuthority(snapshot=lambda: policy, clock=lambda: now, capacity=1, fence=fence)
store = ObservationStore(sys.argv[1], authority="gateway-a", installation="gateway-a")
binding = OriginBinding("origin-a", "installation", 1, "producer")
store.bind_origin(binding)
store.apply(CurrentUpdate.from_dict({
    "schema": "fleet-manager.current-update", "version": 1, **asdict(binding),
    "project": "project-a", "entity": "entity-a", "sequence": 1, "source_revision": 1,
    "observed_at": now, "availability": "current", "reason": None,
    "state": {"status": "active", "title": "never-returned"},
}))
store.apply(CurrentUpdate.from_dict({
    "schema": "fleet-manager.current-update", "version": 1, **asdict(binding),
    "project": "project-a", "entity": "entity-b", "sequence": 2, "source_revision": 2,
    "observed_at": now, "availability": "current", "reason": None,
    "state": {"title": "selected-title", "status": "never-returned"},
}))
lookups = []
original = store._point
def read(connection, origin, project, entity, head):
    lookups.append((origin, project, entity))
    return original(connection, origin, project, entity, head)
store._point = read
adapter = ScopedObservationReader(authority=owner, store=store, fence=fence)
for index, (method, subject) in enumerate(
    (method, subject) for method in ("read", "snapshot", "refresh", "refresh")
    for subject in ("first", "second")
):
    token = jwt.encode(
        {"alg": "ES256", "typ": "at+jwt", "kid": "issuer-1"},
        {"iss": issuer, "sub": subject, "aud": audience, "iat": now, "exp": now + 3600,
         "cnf": {"jkt": proof_key.thumbprint()}}, signer,
    )
    ath = base64.urlsafe_b64encode(hashlib.sha256(token.encode("ascii")).digest()).rstrip(b"=").decode()
    proof = jwt.encode(
        {"alg": "ES256", "typ": "dpop+jwt", "jwk": proof_key.as_dict(private=False)},
        {"jti": f"proof-{index}", "htm": "GET", "htu": target, "iat": now,
         "ath": ath, "nonce": verifier.nonce}, proof_key,
    )
    identity = verifier.verify(token, proof, method="GET", target_uri=target)
    try:
        result = getattr(adapter, method)(
            identity, ObservationRead("fleet-a", (scope,) if method == "read" else (scope, other)),
            role="client", installation="client-a",
            **({"validator": baseline} if method == "refresh" and index >= 6 else {}),
        )
    except AdmissionError as error:
        assert subject == "second" and error.code == "forbidden-scope"
    else:
        assert subject == "first"
        if method == "refresh":
            assert len(result.validator) == 64
            if index >= 6:
                assert result.status == "unchanged" and result.observations == ()
                continue
            assert result.status == "snapshot"
            baseline = result.validator
            points = result.observations
        else:
            points = (result,) if method == "read" else result
        assert points[0].state == (("status", "active"),)
        if method != "read":
            assert points[1].state == (("title", "selected-title"),)
        encoded = json.dumps([asdict(point) for point in points])
        assert "never-returned" not in encoded and "cursor" not in encoded
assert len(lookups) == 7 and not owner._issued
assert not any(name.startswith("test_") for name in sys.modules)
print(json.dumps({"authorized_reads": 4, "foreign_reads": 0, "point_lookups": 7}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "consumer.db")],
        env=env, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "authorized_reads": 4, "foreign_reads": 0, "point_lookups": 7,
    }


@pytest.mark.parametrize("inverted", [False, True])
@pytest.mark.parametrize("method", ["read", "snapshot", "refresh"])
def test_mixed_direct_and_reader_calls_have_one_lock_order(reader, tmp_path, inverted, method):
    identities = tmp_path / "verified-identities.json"
    identities.write_text(json.dumps([
        asdict(reader.identity()), asdict(reader.identity()),
    ]), encoding="utf-8")
    script = """
from contextlib import contextmanager
import json, sys, threading
from pathlib import Path
from fleet_manager.observation_reader import ScopedObservationReader
from fleet_manager.observation_store import ObservationStore
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AuthoritySnapshot, Enrollment, ObservationRead, ObservationScope,
)
from fleet_manager.runtime_identity import VerifiedIdentity
identities = [VerifiedIdentity(**value) for value in json.loads(
    Path(sys.argv[2]).read_text(encoding="utf-8"))]
now = identities[0].verified_at
entered, release, attempted = (threading.Event() for _ in range(3))
class Fence:
    def __init__(self):
        self.lock = threading.RLock()
        self.paused = False
    def acquire(self, *, timeout):
        name = threading.current_thread().name
        if name == "direct":
            attempted.set()
        acquired = self.lock.acquire(timeout=timeout)
        if acquired and name == "reader" and not self.paused:
            self.paused = True
            entered.set()
            if not release.wait(3):
                self.lock.release()
                raise RuntimeError("fixture-release-missing")
        return acquired
    def release(self):
        self.lock.release()
fence = Fence()
scope = ObservationScope("origin-a", "project-a", "entity-a", ("status",))
selection = ObservationRead("fleet-a", (scope,))
snapshot = AuthoritySnapshot(
    authority="gateway-a", fleet="fleet-a",
    issuer=identities[0].issuer, audience=identities[0].audience, revision=1,
    valid_from=now - 1, valid_until=now + 3600, identity_trust_until=now + 3600,
    enrollments=(Enrollment(
        subject=identities[0].subject, principal="actor-a", account="account-a",
        kind="operator", role="client", installation="client-a",
        generation=1, status="active", reads=(scope,),
    ),),
)
def current():
    if threading.current_thread().name == "direct":
        attempted.set()
    with fence.lock:
        return snapshot
owner = AdmissionAuthority(snapshot=current, clock=lambda: now, fence=fence)
if sys.argv[3] == "inverted":
    @contextmanager
    def legacy_order():
        with owner._lock:
            yield
    owner._guard = legacy_order
store = ObservationStore(sys.argv[1], authority="gateway-a", installation="gateway-a")
adapter = ScopedObservationReader(authority=owner, store=store, fence=fence)
outcomes, failures = [], []
def read():
    try:
        result = getattr(adapter, sys.argv[4])(
            identities[0], selection, role="client", installation="client-a",
        )
        point = (result if sys.argv[4] == "read" else
                 result.observations[0] if sys.argv[4] == "refresh" else result[0])
        assert point.state == (("status", "active"),)
        outcomes.append("reader")
    except Exception as error:
        failures.append(str(error))
def direct():
    try:
        permit = owner.authorize(identities[1], selection, role="client", installation="client-a")
        owner.complete(permit, selection)
        outcomes.append("direct")
    except Exception as error:
        failures.append(str(error))
reading = threading.Thread(target=read, name="reader", daemon=True)
writing = threading.Thread(target=direct, name="direct", daemon=True)
reading.start()
assert entered.wait(3)
writing.start()
assert attempted.wait(3)
release.set()
reading.join(0.5)
writing.join(0.5)
deadlock = reading.is_alive() and writing.is_alive()
if not deadlock:
    assert not failures and sorted(outcomes) == ["direct", "reader"] and not owner._issued
print(json.dumps({"deadlock": deadlock, "completed": len(outcomes)}))
"""
    source = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-c", script, str(reader.store.path), str(identities),
         "inverted" if inverted else "normal", method],
        env={**os.environ, "PYTHONPATH": str(source)},
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "deadlock": inverted, "completed": 0 if inverted else 2,
    }
