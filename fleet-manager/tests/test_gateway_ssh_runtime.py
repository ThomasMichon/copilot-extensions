from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from fleet_manager.gateway_ssh_runtime import (
    GatewaySSHAuthorization, GatewaySSHObservation, GatewaySSHPlan,
    plan_gateway_ssh, reconcile_gateway_ssh,
)
from fleet_manager.planning import PlanError, canonical, digest
import fleet_manager.gateway_ssh_runtime as runtime


def fingerprint(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Decision:
    encoded: bytes

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def decision_digest(self):
        return digest(self.to_dict())


def decision(**changes):
    value = {
        "machine": "node-a", "environment": "windows", "controller": "controller-a",
        "backend": "gateway", "state": "configured", "policy_digest": "a" * 64,
        "schema": "fleet-manager.gateway-ssh-decision", "version": 1,
        "policy_revision": "r1", "role": "interactive", "reason": "declared-policy",
        "requested_inbound": True, "requested_outbound": True,
        "effective_inbound": True, "effective_outbound": True,
        "gateway_egress": True, "host_path": [],
        "execution_authorized": False, "live_readiness_verified": False,
    }
    value.update(changes)
    if value["backend"] != "gateway":
        value.update(controller=None, gateway_egress=False, host_path=[])
    if value["state"] != "configured":
        value.update(effective_inbound=False, effective_outbound=False)
    return Decision(canonical(value))


def observation(selected=None, **changes):
    selected = decision() if selected is None else selected
    value = selected.to_dict()
    now = int(time.time())
    result = {
        "schema": "fleet-manager.gateway-ssh-observation", "version": 1,
        "target": {key: value[key] for key in ("machine", "environment", "controller", "backend")},
        "owner": "profile-owner", "generation": 3, "revision": 7,
        "observed_at": now - 1, "valid_until": now + 300,
        "policy_digest": value["policy_digest"], "decision_digest": digest(value),
        "current": {"fleet": fingerprint(b"drift")},
        "desired": {"fleet": fingerprint(b"selected-profile")},
        "protected_digest": fingerprint(b"unrelated-dtssh-and-user-profiles"),
        "pending_operation": None,
    }
    result.update(changes)
    return GatewaySSHObservation.from_dict(result)


class FileOwner:
    """Isolated fixed-operation consumer; no real SSH/profile owner is invoked."""

    def __init__(self, root, selected=None):
        self.selected = decision() if selected is None else selected
        self.root = root
        self.profile = root / "managed-profile"
        self.unrelated = root / "unrelated-profile"
        self.profile.write_bytes(b"drift")
        self.unrelated.write_bytes(b"unrelated-dtssh-and-user-profiles")
        self.snapshot = observation(self.selected)
        self.calls = []
        self.receipts = {}
        self.fail = None
        self.after_change = {}

    def authenticate(self, target, *, timeout):
        assert 0 < timeout <= 30
        self.calls.append("authenticate")
        assert target == {
            key: self.selected.to_dict()[key]
            for key in ("machine", "environment", "controller", "backend")
        }
        if self.fail == "authenticate":
            raise OSError("private transport diagnostic must not escape")
        return b"verified-connection-node-a-generation-3"

    def observe(self, target, *, timeout):
        assert 0 < timeout <= 30
        self.calls.append("observe")
        if self.fail == "observe":
            raise TimeoutError("private path must not escape")
        return self.snapshot

    def reconcile(self, plan, authorization, *, timeout):
        assert 0 < timeout <= 30
        self.calls.append("reconcile")
        if self.fail == "before-effect":
            raise TimeoutError("accepted effect outcome unavailable")
        key = plan.plan_digest
        if key in self.receipts:
            return self.receipts[key]
        expected = plan.to_dict()["observation"]
        current = self.snapshot.to_dict()
        assert current["revision"] == expected["revision"]
        assert current["generation"] == expected["generation"]
        assert authorization.observation_digest == self.snapshot.observation_digest
        assert time.time() < authorization.valid_until
        assert current["protected_digest"] == fingerprint(self.unrelated.read_bytes())
        assert current["desired"] == {"fleet": fingerprint(b"selected-profile")}
        staged = self.root / "staged-profile"
        staged.write_bytes(b"selected-profile")
        os.replace(staged, self.profile)
        current.update(current={"fleet": fingerprint(self.profile.read_bytes())},
                       revision=current["revision"] + 1)
        current.update(self.after_change)
        if self.fail == "after-effect":
            current["pending_operation"] = key
        self.snapshot = GatewaySSHObservation.from_dict(current)
        self.receipts[key] = self.snapshot
        if self.fail == "after-effect":
            raise TimeoutError("private accepted-result path")
        return self.snapshot


class Authority:
    """Trusted fixture checks selected identity, current intent and desired bytes."""

    def __init__(self, selected=None, desired=None):
        self.selected = decision() if selected is None else selected
        self.desired = {"fleet": fingerprint(b"selected-profile")} if desired is None else desired
        self.calls = 0
        self.refuse = False
        self.override = None

    def authorize(self, plan, current, identity, *, timeout):
        assert 0 < timeout <= 30
        self.calls += 1
        if (self.refuse or identity != b"verified-connection-node-a-generation-3"
                or plan.to_dict()["decision"] != self.selected.to_dict()
                or current.to_dict()["desired"] != self.desired):
            raise PermissionError("private authorization reason")
        if self.override is not None:
            return self.override
        return GatewaySSHAuthorization(
            plan.plan_digest, current.observation_digest,
            fingerprint(identity), int(time.time()) + 60,
        )


def setup(tmp_path, **decision_changes):
    selected = decision(**decision_changes)
    owner = FileOwner(tmp_path, selected)
    authority = Authority(selected)
    return plan_gateway_ssh(selected, owner.snapshot), authority, owner


def test_real_fixed_owner_repairs_only_managed_projection_then_stale_plan_is_inert(tmp_path):
    plan, authority, owner = setup(tmp_path)
    before = owner.unrelated.read_bytes()
    assert plan.to_dict()["changes"] == ["fleet"]
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.state == "reconciled" and result.effect == "applied"
    assert owner.profile.read_bytes() == b"selected-profile"
    assert owner.unrelated.read_bytes() == before
    assert owner.calls == ["authenticate", "observe", "reconcile"]
    assert authority.calls == 1
    repeated = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert repeated.state == "blocked" and repeated.reason == "observation-changed"
    assert owner.calls.count("reconcile") == 1
    fresh = plan_gateway_ssh(owner.selected, owner.snapshot)
    assert fresh.to_dict()["state"] == "unchanged"
    assert reconcile_gateway_ssh(fresh, authority=authority, owner=owner).state == "unchanged"
    assert owner.calls.count("reconcile") == 1


def test_float_rounding_never_expands_the_owner_operation_budget(tmp_path, monkeypatch):
    plan, authority, owner = setup(tmp_path)
    instant = math.nextafter(256.0, 0.0)
    assert (instant + 30) - instant > 30
    monkeypatch.setattr(runtime, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: instant,
    ))
    assert reconcile_gateway_ssh(plan, authority=authority, owner=owner).state == "reconciled"


@pytest.mark.parametrize("phase", ["authenticate", "observe", "authorize"])
def test_budget_expiry_before_effect_is_distinct_from_provider_unavailability(
    tmp_path, monkeypatch, phase,
):
    plan, authority, owner = setup(tmp_path)
    clock = {"now": 1000.0}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: clock["now"],
    ))
    adapter = authority if phase == "authorize" else owner
    original = getattr(adapter, phase)

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        clock["now"] += 31
        return value

    monkeypatch.setattr(adapter, phase, delayed)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert (result.state, result.reason, result.effect) == ("blocked", "operation-timeout", "none")
    assert "reconcile" not in owner.calls and owner.profile.read_bytes() == b"drift"


def test_budget_expiry_at_effect_entry_does_not_claim_an_uncertain_effect(tmp_path, monkeypatch):
    plan, authority, owner = setup(tmp_path)
    ticks = iter([0, 0, 0, 0, 0, 31])
    monkeypatch.setattr(runtime, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: next(ticks),
    ))
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert (result.state, result.reason, result.effect) == ("blocked", "operation-timeout", "none")
    assert "reconcile" not in owner.calls


@pytest.mark.parametrize("phase,reason", [
    ("authenticate", "target-authentication-timeout"),
    ("observe", "owner-observation-timeout"),
    ("authorize", "execution-authority-timeout"),
])
def test_provider_timeouts_have_explicit_stage_codes(tmp_path, monkeypatch, phase, reason):
    plan, authority, owner = setup(tmp_path)

    def expired(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(authority if phase == "authorize" else owner, phase, expired)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert (result.state, result.reason, result.effect) == ("blocked", reason, "none")
    assert "reconcile" not in owner.calls


def test_late_effect_result_retains_observation_without_reporting_success(tmp_path, monkeypatch):
    plan, authority, owner = setup(tmp_path)
    clock = {"now": 1000.0}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: clock["now"],
    ))
    original = owner.reconcile

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        clock["now"] += 31
        return value

    monkeypatch.setattr(owner, "reconcile", delayed)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.state == "unknown" and result.reason == "effect-verification-timeout"
    assert result.observation == owner.snapshot and result.effect == "unknown"
    assert result.to_dict()["observation"] == owner.snapshot.to_dict()
    assert result.to_dict()["reobserve_required"] is True
    assert owner.profile.read_bytes() == b"selected-profile"


@pytest.mark.parametrize("state", ["configured", "offline", "disabled", "blocked"])
def test_empty_managed_sets_are_representable_and_never_invent_projection_work(tmp_path, state):
    selected = decision(state=state)
    owner = FileOwner(tmp_path, selected)
    owner.snapshot = observation(
        selected, current={}, desired={},
        protected_digest=fingerprint(owner.profile.read_bytes() + owner.unrelated.read_bytes()),
    )
    authority = Authority(selected, desired={})
    plan = plan_gateway_ssh(selected, owner.snapshot)
    assert plan.to_dict()["changes"] == []
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.state == ("unchanged" if state == "configured" else state)
    assert result.effect == "none" and "reconcile" not in owner.calls
    assert owner.profile.read_bytes() == b"drift"
    assert owner.unrelated.read_bytes() == b"unrelated-dtssh-and-user-profiles"


@pytest.mark.parametrize("field", ["generation", "revision"])
def test_observation_sequence_diagnostics_identify_the_invalid_field(field):
    value = observation().to_dict()
    value[field] = True
    with pytest.raises(PlanError, match="SSH observation " + field):
        GatewaySSHObservation.from_dict(value)


def test_plan_and_observation_do_not_retain_mutable_aliases():
    selected = decision()
    source = observation().to_dict()
    before = deepcopy(source)
    retained = GatewaySSHObservation.from_dict(source)
    plan = plan_gateway_ssh(selected, retained)
    source["desired"].clear()
    plan.to_dict()["observation"]["desired"].clear()
    retained.to_dict()["target"]["machine"] = "other"
    assert retained.to_dict() == before
    assert GatewaySSHPlan(plan.encoded).plan_digest == plan.plan_digest
    with pytest.raises(FrozenInstanceError):
        plan.encoded = b"changed"


@pytest.mark.parametrize("state", ["offline", "disabled", "blocked"])
def test_nonconfigured_policy_never_touches_owner_or_authority(tmp_path, state):
    plan, authority, owner = setup(tmp_path, state=state)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.state == state and result.effect == "none"
    assert not owner.calls and authority.calls == 0


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("backend", ["gateway", "dtssh", "cloudflare"])
def test_environment_and_transport_are_explicit_selected_bindings(tmp_path, environment, backend):
    plan, authority, owner = setup(tmp_path, environment=environment, backend=backend)
    assert reconcile_gateway_ssh(plan, authority=authority, owner=owner).state == "reconciled"


def test_current_policy_or_authenticated_identity_cannot_be_replaced_by_plan_data(tmp_path):
    plan, authority, owner = setup(tmp_path)
    authority.selected = decision(controller="controller-b")
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.reason == "execution-authority-refused"
    assert owner.profile.read_bytes() == b"drift" and "reconcile" not in owner.calls


def test_wrong_authenticated_peer_is_refused_by_current_authority(tmp_path):
    plan, authority, owner = setup(tmp_path)
    owner.authenticate = lambda *args, **kwargs: b"verified-but-different-peer"
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.reason == "execution-authority-refused"
    assert owner.profile.read_bytes() == b"drift" and "reconcile" not in owner.calls


@pytest.mark.parametrize("grant", [True, False, {"activation_authorized": True}, "receipt"])
def test_boolean_or_artifact_receipt_is_not_execution_authorization(tmp_path, grant):
    plan, authority, owner = setup(tmp_path)
    authority.override = grant
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.reason == "execution-authorization-required"
    assert "reconcile" not in owner.calls


@pytest.mark.parametrize("field", ["plan_digest", "observation_digest", "identity_digest", "valid_until"])
def test_exact_authorization_binding_and_expiry_checked_before_effect(tmp_path, field):
    plan, authority, owner = setup(tmp_path)
    grant = {
        "plan_digest": plan.plan_digest,
        "observation_digest": owner.snapshot.observation_digest,
        "identity_digest": fingerprint(b"verified-connection-node-a-generation-3"),
        "valid_until": int(time.time()) + 60,
    }
    grant[field] = 0 if field == "valid_until" else "f" * 64
    authority.override = GatewaySSHAuthorization(**grant)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.reason == "execution-authorization-mismatch"
    assert owner.profile.read_bytes() == b"drift"


@pytest.mark.parametrize("field,value", [
    ("owner", "other-owner"), ("generation", 4), ("revision", 8),
    ("policy_digest", "f" * 64), ("decision_digest", "f" * 64),
    ("protected_digest", "f" * 64), ("pending_operation", "f" * 64),
    ("current", {"fleet": "b" * 64}), ("desired", {"fleet": "c" * 64}),
    ("target", {"machine": "other", "environment": "windows",
                "controller": "controller-a", "backend": "gateway"}),
])
def test_changed_observation_refuses_without_calling_effect(tmp_path, field, value):
    plan, authority, owner = setup(tmp_path)
    current = owner.snapshot.to_dict()
    current[field] = value
    owner.snapshot = GatewaySSHObservation.from_dict(current)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.reason == "observation-changed" and result.effect == "none"
    assert "reconcile" not in owner.calls and authority.calls == 0
    if field == "pending_operation":
        assert result.to_dict()["reobserve_required"] is True


def test_fresh_observation_time_is_not_mistaken_for_changed_generation(tmp_path):
    plan, authority, owner = setup(tmp_path)
    current = owner.snapshot.to_dict()
    current["observed_at"] = int(time.time())
    current["valid_until"] += 1
    owner.snapshot = GatewaySSHObservation.from_dict(current)
    assert reconcile_gateway_ssh(plan, authority=authority, owner=owner).state == "reconciled"


@pytest.mark.parametrize("future", [False, True])
def test_invalid_owner_observation_window_requires_refresh_without_attaching_stale_state(
    tmp_path, future,
):
    plan, authority, owner = setup(tmp_path)
    current = owner.snapshot.to_dict()
    now = int(time.time())
    current.update(observed_at=now + 60 if future else 0,
                   valid_until=now + 120 if future else 1)
    owner.snapshot = GatewaySSHObservation.from_dict(current)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert (result.state, result.reason, result.effect) == ("blocked", "observation-expired", "none")
    assert result.observation is None and result.to_dict()["reobserve_required"] is True
    assert authority.calls == 0 and "reconcile" not in owner.calls
    assert owner.profile.read_bytes() == b"drift"


@pytest.mark.parametrize("phase", ["authenticate", "observe", "authorize"])
def test_observation_expiry_during_adapter_stages_is_not_an_authorization_mismatch(
    tmp_path, monkeypatch, phase,
):
    plan, authority, owner = setup(tmp_path)
    clock = {"now": time.time()}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(
        time=lambda: clock["now"], monotonic=time.monotonic,
    ))
    authority.override = GatewaySSHAuthorization(
        plan.plan_digest, owner.snapshot.observation_digest,
        fingerprint(b"verified-connection-node-a-generation-3"), int(clock["now"]) + 1000,
    )
    adapter = authority if phase == "authorize" else owner
    original = getattr(adapter, phase)

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        clock["now"] += 301
        return value

    monkeypatch.setattr(adapter, phase, delayed)
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert (result.state, result.reason, result.effect) == ("blocked", "observation-expired", "none")
    assert result.observation is None and result.to_dict()["reobserve_required"] is True
    assert "reconcile" not in owner.calls and owner.profile.read_bytes() == b"drift"


def test_expired_snapshot_and_pending_effect_never_start_execution(tmp_path):
    plan, authority, owner = setup(tmp_path)
    expired = owner.snapshot.to_dict()
    expired.update(observed_at=0, valid_until=1)
    plan = plan_gateway_ssh(owner.selected, GatewaySSHObservation.from_dict(expired))
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert (result.state, result.reason, result.effect) == ("blocked", "observation-expired", "none")
    assert result.to_dict()["reobserve_required"] is True
    assert result.observation is None
    pending = owner.snapshot.to_dict()
    pending["pending_operation"] = "f" * 64
    plan = plan_gateway_ssh(owner.selected, GatewaySSHObservation.from_dict(pending))
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.reason == "effect-reobservation-required"
    assert result.to_dict()["reobserve_required"] is True
    assert not owner.calls


@pytest.mark.parametrize("value", ["invalid", "F" * 64, None])
def test_nonprofile_digest_validation_has_an_accurate_diagnostic(value):
    with pytest.raises(PlanError, match="SSH digest must be lowercase SHA-256"):
        GatewaySSHAuthorization(value, "f" * 64, "f" * 64, int(time.time()) + 60)


@pytest.mark.parametrize("failure", ["authenticate", "observe", "before-effect", "after-effect"])
def test_transport_failure_is_bounded_sanitized_and_never_retried(tmp_path, failure):
    plan, authority, owner = setup(tmp_path)
    owner.fail = failure
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    effect_started = failure in ("before-effect", "after-effect")
    assert result.state == ("unknown" if effect_started else "blocked")
    assert result.effect == ("unknown" if effect_started else "none")
    assert result.to_dict()["reobserve_required"] == effect_started
    assert "private" not in json.dumps(result.to_dict())
    assert owner.calls.count("reconcile") == int(effect_started)
    if failure == "after-effect":
        assert owner.profile.read_bytes() == b"selected-profile"
        observed_plan = plan_gateway_ssh(owner.selected, owner.snapshot)
        assert observed_plan.to_dict()["reason"] == "effect-reobservation-required"
        pending = reconcile_gateway_ssh(observed_plan, authority=authority, owner=owner)
        assert pending.effect == "none"
        assert pending.to_dict()["reobserve_required"] is True
        assert owner.calls.count("reconcile") == 1


@pytest.mark.parametrize("change", [
    {"protected_digest": "f" * 64}, {"generation": 4}, {"owner": "other-owner"},
    {"current": {"fleet": "f" * 64}}, {"revision": 7},
    {"decision_digest": "f" * 64}, {"policy_digest": "f" * 64},
    {"desired": {"fleet": "f" * 64}}, {"pending_operation": "f" * 64},
    {"observed_at": 0, "valid_until": 1},
])
def test_effect_with_unverified_or_broadened_result_is_unknown_not_success(tmp_path, change):
    plan, authority, owner = setup(tmp_path)
    owner.after_change = change
    result = reconcile_gateway_ssh(plan, authority=authority, owner=owner)
    assert result.state == "unknown" and result.effect == "unknown"
    assert result.reason == "effect-not-verified"


@pytest.mark.parametrize("change", [
    {"version": True}, {"extra": True}, {"owner": "../owner"}, {"generation": True},
    {"revision": 0}, {"policy_digest": "F" * 64}, {"current": {}}, {"desired": {}},
    {"current": {"fleet": "b" * 64, "other": None}},
    {"desired": {"fleet": None}}, {"desired": {"../config": "c" * 64}},
    {"pending_operation": True}, {"valid_until": 0},
    {"desired": {f"profile-{n}": "c" * 64 for n in range(33)}},
])
def test_observation_contract_refuses_invalid_or_unbounded_input(change):
    value = observation().to_dict()
    value.update(change)
    with pytest.raises(PlanError):
        GatewaySSHObservation.from_dict(value)


def test_forged_plan_decision_target_and_changed_snapshot_refuse():
    selected = decision()
    observed = observation()
    with pytest.raises(PlanError):
        plan_gateway_ssh(decision(machine="node-b"), observed)
    with pytest.raises(PlanError):
        plan_gateway_ssh(decision(policy_digest="b" * 64), observed)
    plan = plan_gateway_ssh(selected, observed)
    modified = plan.to_dict()
    modified["state"] = "unchanged"
    with pytest.raises(PlanError):
        GatewaySSHPlan(canonical(modified))
    with pytest.raises(PlanError):
        GatewaySSHPlan(json.dumps(plan.to_dict(), indent=2).encode())
    with pytest.raises(PlanError):
        plan_gateway_ssh(selected.to_dict(), observed)
    with pytest.raises(PlanError):
        plan_gateway_ssh(selected, observed.to_dict())


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("version", True), ("environment", "unknown"),
    ("backend", "ssh"), ("controller", None), ("role", "controller"),
    ("policy_revision", "../revision"), ("reason", "ready"),
    ("requested_inbound", 1), ("requested_outbound", None),
    ("effective_inbound", "true"), ("effective_outbound", 0),
    ("gateway_egress", "yes"), ("execution_authorized", True),
    ("live_readiness_verified", True), ("host_path", "host"),
    ("host_path", [{"machine": "host", "environment": "linux", "command": "run"}]),
    ("host_path", [{"machine": "host", "environment": "unknown"}]),
    ("host_path", [{"machine": "../host", "environment": "linux"}]),
    ("extra", "field"),
])
def test_exact_sibling_policy_decision_contract_refuses_malformed_claims(field, value):
    raw = decision().to_dict()
    raw[field] = value
    selected = Decision(canonical(raw))
    with pytest.raises(PlanError):
        plan_gateway_ssh(selected, observation(selected))


def test_policy_host_hops_are_retained_without_inventing_execution_authority():
    selected = decision(host_path=[{"machine": "host-a", "environment": "windows"}])
    plan = plan_gateway_ssh(selected, observation(selected))
    assert plan.to_dict()["decision"]["host_path"] == [
        {"machine": "host-a", "environment": "windows"},
    ]
    assert plan.to_dict()["decision"]["execution_authorized"] is False


def test_policy_host_path_is_bounded_at_the_declared_maximum():
    hops = [{"machine": "host-" + str(i), "environment": "linux"}
            for i in range(runtime.MAX_HOST_HOPS)]
    selected = decision(host_path=hops)
    assert plan_gateway_ssh(selected, observation(selected)).to_dict()["decision"]["host_path"] == hops
    selected = decision(host_path=[*hops, {"machine": "overflow", "environment": "linux"}])
    with pytest.raises(PlanError, match="SSH host path must be a bounded list"):
        plan_gateway_ssh(selected, observation(selected))


def test_sibling_decision_digest_must_bind_the_entire_canonical_dictionary():
    class WrongDigest:
        decision_digest = "f" * 64

        def to_dict(self):
            return decision().to_dict()

    with pytest.raises(PlanError, match="decision digest"):
        plan_gateway_ssh(WrongDigest(), observation())


def test_programming_failure_is_not_silently_reported_as_a_transport_result(tmp_path):
    plan, authority, owner = setup(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("adapter programming failure")

    owner.reconcile = broken
    with pytest.raises(RuntimeError, match="programming"):
        reconcile_gateway_ssh(plan, authority=authority, owner=owner)


def test_native_installed_consumer_import_is_inert_and_plan_is_portable(tmp_path):
    plan = plan_gateway_ssh(decision(), observation())
    script = """
import json, socket, sys
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('offline SSH leaf must not open network connections')
socket.socket = socket.create_connection = forbidden
import fleet_manager.gateway_ssh_runtime as module
assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
plan = module.GatewaySSHPlan(sys.stdin.buffer.read())
assert plan.to_dict()['state'] == 'planned'
assert list(Path.cwd().iterdir()) == []
print(json.dumps({'plan_digest': plan.plan_digest, 'effect': 'none'}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        input=plan.encoded, cwd=tmp_path, capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout) == {"plan_digest": plan.plan_digest, "effect": "none"}
    assert list(tmp_path.iterdir()) == []
