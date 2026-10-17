"""Bounded named SSH-profile reconciliation through an explicitly supplied owner.

This leaf does not select a transport, open SSH, enroll a machine, run an
installer or start a loop. The setup/controller owner supplies the resolved
policy decision, authenticated target-local profile owner and current execution
authority. Durable artifact admission is not execution authority.

The profile owner must atomically compare the observation revision, preserve
all unrelated state, and deduplicate the plan digest using its own effect
journal. An uncertain effect requires observation, never automatic redelivery.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from typing import Protocol

from .planning import PlanError, boolean, canonical, decode, digest, identifier, integer, record
from .release import SHA256

MAX_PROFILES = 32
MAX_HOST_HOPS = 32
OPERATION_SECONDS = 30
TARGET_FIELDS = ("machine", "environment", "controller", "backend")


class _BudgetExpired(TimeoutError):
    """Operation deadline expiry, distinct from a provider's transport timeout."""


def _fingerprint(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise PlanError("SSH digest must be lowercase SHA-256")


def _target(value):
    if not isinstance(value, dict) or set(value) != set(TARGET_FIELDS):
        raise PlanError("invalid selected SSH target")
    for field in ("machine", "environment", "backend"):
        identifier(value[field], "selected SSH target")
    if value["environment"] not in ("windows", "wsl", "linux"):
        raise PlanError("unsupported selected SSH environment")
    if value["backend"] not in ("gateway", "dtssh", "cloudflare"):
        raise PlanError("unsupported selected SSH transport")
    if value["backend"] == "gateway":
        identifier(value["controller"], "selected SSH controller")
    elif value["controller"] is not None:
        raise PlanError("non-Gateway SSH target cannot select a Gateway controller")


def _profiles(value, *, absent=False):
    if not isinstance(value, dict) or len(value) > MAX_PROFILES:
        raise PlanError("SSH profiles must be a bounded object")
    for name, binding in value.items():
        identifier(name, "managed SSH profile")
        if not absent or binding is not None:
            _fingerprint(binding)


def _validate_observation(value):
    canonical(value)
    record(value, "fleet-manager.gateway-ssh-observation", {
        "target", "owner", "generation", "revision", "observed_at", "valid_until",
        "policy_digest", "decision_digest", "current", "desired",
        "protected_digest", "pending_operation",
    })
    _target(value["target"])
    identifier(value["owner"], "SSH profile owner")
    for field in ("generation", "revision"):
        integer(value[field], "SSH observation " + field, minimum=1)
    for field in ("observed_at", "valid_until"):
        integer(value[field], "SSH observation time")
    if value["valid_until"] <= value["observed_at"]:
        raise PlanError("invalid SSH observation validity interval")
    for field in ("policy_digest", "decision_digest", "protected_digest"):
        _fingerprint(value[field])
    _profiles(value["current"], absent=True)
    _profiles(value["desired"])
    if set(value["current"]) != set(value["desired"]):
        raise PlanError("SSH observation managed profile sets differ")
    if value["pending_operation"] is not None:
        _fingerprint(value["pending_operation"])


@dataclass(frozen=True)
class GatewaySSHObservation:
    """Supplied owner assertions; parsing does not authenticate this snapshot.

    Bindings are digests of owner-resolved profile inputs, never paths, shell
    commands or credentials. ``protected_digest`` covers all unrelated state.
    Desired bindings require independent current-policy approval by authority.
    Empty matching current/desired sets represent no managed projection work.
    """

    encoded: bytes

    def __post_init__(self):
        value = decode(self.encoded)
        _validate_observation(value)
        if type(self.encoded) is not bytes or canonical(value) != self.encoded:
            raise PlanError("SSH observation requires immutable canonical bytes")

    @classmethod
    def from_dict(cls, value):
        _validate_observation(value)
        return cls(canonical(value))

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def observation_digest(self):
        return digest(self.to_dict())


def _plan_value(decision, observation):
    _validate_observation(observation)
    canonical(decision)
    record(decision, "fleet-manager.gateway-ssh-decision", {
        *TARGET_FIELDS, "state", "reason", "role", "policy_revision", "policy_digest",
        "requested_inbound", "requested_outbound", "effective_inbound",
        "effective_outbound", "gateway_egress", "host_path", "execution_authorized",
        "live_readiness_verified",
    })
    identifier(decision["policy_revision"], "SSH policy revision")
    for field in ("requested_inbound", "requested_outbound", "effective_inbound",
                  "effective_outbound", "gateway_egress", "execution_authorized",
                  "live_readiness_verified"):
        boolean(decision[field], "SSH policy permission")
    if decision["execution_authorized"] or decision["live_readiness_verified"]:
        raise PlanError("SSH policy data cannot assert execution or live readiness")
    if decision["role"] not in ("interactive", "service", "client-only", "offline"):
        raise PlanError("unsupported SSH policy role")
    if decision["reason"] not in (
        "declared-policy", "offline-role", "ssh-disabled",
        "gateway-egress-required", "host-hop-unavailable",
    ):
        raise PlanError("unsupported SSH policy reason")
    if (not isinstance(decision["host_path"], list)
            or len(decision["host_path"]) > MAX_HOST_HOPS):
        raise PlanError("SSH host path must be a bounded list")
    for hop in decision["host_path"]:
        if not isinstance(hop, dict) or set(hop) != {"machine", "environment"}:
            raise PlanError("invalid SSH host reference")
        identifier(hop["machine"], "SSH host reference")
        if hop["environment"] not in ("windows", "wsl", "linux"):
            raise PlanError("invalid SSH host environment")
    target = {field: decision.get(field) for field in TARGET_FIELDS}
    _target(target)
    state = decision.get("state")
    if state not in ("configured", "offline", "disabled", "blocked"):
        raise PlanError("unsupported resolved SSH decision state")
    if state != "configured" and (decision["effective_inbound"] or decision["effective_outbound"]):
        raise PlanError("nonconfigured SSH policy cannot assert effective permissions")
    if target["backend"] != "gateway" and (decision["gateway_egress"] or decision["host_path"]):
        raise PlanError("non-Gateway policy cannot select Gateway egress or a host path")
    if target != observation["target"]:
        raise PlanError("SSH decision target differs from observation")
    if (digest(decision) != observation["decision_digest"]
            or decision.get("policy_digest") != observation["policy_digest"]):
        raise PlanError("SSH observation differs from resolved policy decision")
    changes = sorted(name for name, binding in observation["desired"].items()
                     if observation["current"][name] != binding)
    if state != "configured":
        status, reason = state, "policy-" + state
    elif observation["pending_operation"] is not None:
        status, reason = "blocked", "effect-reobservation-required"
    elif not changes:
        status, reason = "unchanged", "profiles-current"
    else:
        status, reason = "planned", "managed-profile-drift"
    return {
        "schema": "fleet-manager.gateway-ssh-plan", "version": 1,
        "decision": decision, "observation": observation, "changes": changes,
        "state": status, "reason": reason,
    }


@dataclass(frozen=True)
class GatewaySSHPlan:
    """Immutable deterministic proposal, never an effect authorization."""

    encoded: bytes

    def __post_init__(self):
        value = decode(self.encoded)
        record(value, "fleet-manager.gateway-ssh-plan",
               {"decision", "observation", "changes", "state", "reason"})
        if (type(self.encoded) is not bytes
                or canonical(_plan_value(value["decision"], value["observation"])) != self.encoded):
            raise PlanError("SSH plan differs from its deterministic inputs")

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def plan_digest(self):
        return digest(self.to_dict())


def plan_gateway_ssh(decision, observation: GatewaySSHObservation) -> GatewaySSHPlan:
    """Freeze SSHPolicyDecision.to_dict() and an explicit local observation.

    The policy leaf owns transport/direction/host-hop resolution. Its immutable
    decision_digest is SHA-256 of that complete canonical dictionary; it is not
    embedded recursively in the dictionary and grants no execution authority.
    """
    if not isinstance(observation, GatewaySSHObservation):
        raise PlanError("typed SSH observation required")
    serialize = getattr(decision, "to_dict", None)
    if not callable(serialize):
        raise PlanError("resolved SSH policy decision required")
    value = serialize()
    if getattr(decision, "decision_digest", None) != digest(value):
        raise PlanError("SSH policy decision digest mismatch")
    return GatewaySSHPlan(canonical(_plan_value(value, observation.to_dict())))


@dataclass(frozen=True)
class GatewaySSHAuthorization:
    """Returned only by the independently trusted current execution authority.

    The authority authenticates the owner's selected target identity and checks
    its current policy, owner/generation, desired bindings and exact plan.
    A caller-supplied boolean or artifact-admission receipt cannot substitute.
    """

    plan_digest: str
    observation_digest: str
    identity_digest: str
    valid_until: int

    def __post_init__(self):
        for value in (self.plan_digest, self.observation_digest, self.identity_digest):
            _fingerprint(value)
        integer(self.valid_until, "SSH authorization expiry")


class GatewaySSHOwner(Protocol):
    def authenticate(self, target: dict, *, timeout: float) -> bytes:
        """Return bounded authenticated connection identity for authority to verify."""
        ...

    def observe(self, target: dict, *, timeout: float) -> GatewaySSHObservation:
        ...

    def reconcile(self, plan: GatewaySSHPlan, authorization: GatewaySSHAuthorization,
                  *, timeout: float) -> GatewaySSHObservation:
        """Atomically CAS, dedup plan digest, repair named profiles, preserve others.

        Record an uncertain accepted effect in the existing owner's journal;
        subsequent observe must expose pending_operation until resolved.
        """
        ...


class GatewaySSHAuthority(Protocol):
    def authorize(self, plan: GatewaySSHPlan, current: GatewaySSHObservation,
                  identity: bytes, *, timeout: float) -> GatewaySSHAuthorization:
        """Authenticate identity and current policy; raise PermissionError to refuse."""
        ...


@dataclass(frozen=True)
class GatewaySSHResult:
    state: str
    reason: str
    plan_digest: str
    effect: str
    observation: GatewaySSHObservation | None = None

    def to_dict(self):
        observation = None if self.observation is None else self.observation.to_dict()
        return {
            "schema": "fleet-manager.gateway-ssh-result", "version": 1,
            "state": self.state, "reason": self.reason, "plan_digest": self.plan_digest,
            "effect": self.effect,
            "reobserve_required": (
                self.state == "unknown"
                or self.reason in ("effect-reobservation-required", "observation-expired")
                or (observation is not None and observation["pending_operation"] is not None)
            ),
            "observation": observation,
        }


def reconcile_gateway_ssh(plan: GatewaySSHPlan, *, authority: GatewaySSHAuthority,
                          owner: GatewaySSHOwner) -> GatewaySSHResult:
    """One bounded attempt via a fixed profile owner; no loop or automatic retry.

    Supplied trusted adapters must enforce their timeout and atomic effect-side
    fences. This leaf cannot cancel arbitrary in-process adapter code. Transport
    errors before an effect refuse; after invocation they are indeterminate.
    Unexpected programming errors propagate, never become successful results.
    """
    if not isinstance(plan, GatewaySSHPlan):
        raise PlanError("typed SSH reconciliation plan required")
    value = plan.to_dict()
    def result(state, reason, effect="none", observation=None):
        return GatewaySSHResult(state, reason, plan.plan_digest, effect, observation)

    if value["state"] not in ("planned", "unchanged"):
        return result(value["state"], value["reason"])
    expected = GatewaySSHObservation.from_dict(value["observation"])
    snapshot = expected.to_dict()
    if not snapshot["observed_at"] <= time.time() < snapshot["valid_until"]:
        return result("blocked", "observation-expired")
    deadline = time.monotonic() + OPERATION_SECONDS

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise _BudgetExpired
        return min(float(OPERATION_SECONDS), left)

    stage = "target-authentication"
    try:
        identity = owner.authenticate(dict(snapshot["target"]), timeout=remaining())
        if type(identity) is not bytes or not 1 <= len(identity) <= 16384:
            return result("blocked", "authenticated-identity-invalid")
        stage = "owner-observation"
        current = owner.observe(dict(snapshot["target"]), timeout=remaining())
        if not isinstance(current, GatewaySSHObservation):
            return result("blocked", "owner-observation-invalid")
        current_value = current.to_dict()
        now = time.time()
        if not (snapshot["observed_at"] <= now < snapshot["valid_until"]
                and current_value["observed_at"] <= now < current_value["valid_until"]):
            return result("blocked", "observation-expired")
        binding = set(snapshot) - {"observed_at", "valid_until"}
        if any(current_value[field] != snapshot[field] for field in binding):
            return result("blocked", "observation-changed", observation=current)
        stage = "execution-authority"
        authorization = authority.authorize(plan, current, identity, timeout=remaining())
        if not isinstance(authorization, GatewaySSHAuthorization):
            return result("blocked", "execution-authorization-required")
        now = time.time()
        if not (snapshot["observed_at"] <= now < snapshot["valid_until"]
                and current_value["observed_at"] <= now < current_value["valid_until"]):
            return result("blocked", "observation-expired")
        if (authorization.plan_digest != plan.plan_digest
                or authorization.observation_digest != current.observation_digest
                or authorization.identity_digest != hashlib.sha256(identity).hexdigest()
                or not now < authorization.valid_until):
            return result("blocked", "execution-authorization-mismatch")
        remaining()
    except _BudgetExpired:
        return result("blocked", "operation-timeout")
    except TimeoutError:
        return result("blocked", stage + "-timeout")
    except PermissionError:
        return result("blocked", stage + "-refused")
    except OSError:
        return result("blocked", stage + "-unavailable")
    if value["state"] == "unchanged":
        return result("unchanged", "profiles-current", observation=current)
    try:
        timeout = remaining()
    except _BudgetExpired:
        return result("blocked", "operation-timeout")
    try:
        after = owner.reconcile(plan, authorization, timeout=timeout)
    except TimeoutError:
        return result("unknown", "effect-timeout", "unknown")
    except OSError:
        return result("unknown", "effect-outcome-unknown", "unknown")
    if not isinstance(after, GatewaySSHObservation):
        return result("unknown", "effect-observation-invalid", "unknown")
    try:
        remaining()
    except _BudgetExpired:
        return result("unknown", "effect-verification-timeout", "unknown", after)
    observed = after.to_dict()
    same = ("target", "owner", "generation", "policy_digest", "decision_digest",
            "desired", "protected_digest")
    if (any(observed[field] != snapshot[field] for field in same)
            or observed["revision"] <= snapshot["revision"]
            or observed["pending_operation"] is not None
            or observed["current"] != snapshot["desired"]
            or not observed["observed_at"] <= time.time() < observed["valid_until"]):
        return result("unknown", "effect-not-verified", "unknown", after)
    return result("reconciled", "managed-profiles-current", "applied", after)
