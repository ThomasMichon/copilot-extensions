"""Pure declared SSH policy; no topology discovery, enrollment or authority.

The configuration owner projects canonical machine/environment selections into
this bounded document. Connector egress and logical SSH directions are separate
permissions. Returned decisions never authorize execution or assert readiness.
"""

from dataclasses import dataclass
import json

from .planning import PlanError, boolean, canonical, decode, digest, identifier, record
from .release import SHA256

ENVIRONMENTS = ("windows", "wsl", "linux")
BACKENDS = ("gateway", "dtssh", "cloudflare")
ROLES = ("interactive", "service", "client-only", "offline")
_ROW_FIELDS = {"machine", "environment", "driver", "role", "inbound", "outbound"}
_DECISION_FIELDS = {
    "machine", "environment", "backend", "role", "controller", "state", "reason",
    "requested_inbound", "requested_outbound", "effective_inbound", "effective_outbound",
    "gateway_egress", "host_path", "policy_revision", "policy_digest",
    "execution_authorized", "live_readiness_verified",
}


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise PlanError(f"unsupported SSH {label}")


def _reference(value):
    if not isinstance(value, dict) or set(value) != {"machine", "environment"}:
        raise PlanError("invalid SSH host reference")
    identifier(value["machine"], "SSH machine")
    _choice(value["environment"], ENVIRONMENTS, "environment")
    return value["machine"], value["environment"]


def _key(row):
    return row["machine"], row["environment"]


def _normalize(value):
    canonical(value)
    record(value, "fleet-manager.gateway-ssh-policy", {"revision", "controllers", "machines"})
    identifier(value["revision"], "SSH policy revision")
    controllers = value["controllers"]
    if not isinstance(controllers, list):
        raise PlanError("SSH controllers must be a bounded list")
    for controller in controllers:
        identifier(controller, "SSH controller")
    if len({item.casefold() for item in controllers}) != len(controllers):
        raise PlanError("SSH controllers must be unique without case collisions")
    machines = value["machines"]
    if not isinstance(machines, list) or not machines:
        raise PlanError("SSH machines must be a nonempty bounded list")
    rows, names, targets = [], {}, set()
    for row in machines:
        if not isinstance(row, dict):
            raise PlanError("SSH machine declaration must be an object")
        _choice(row.get("driver"), BACKENDS, "transport")
        fields = _ROW_FIELDS | (
            {"controller", "gateway_egress"} if row["driver"] == "gateway" else set()
        )
        optional = {"host_hop"} if row["driver"] == "gateway" else set()
        if not fields <= row.keys() or row.keys() - fields - optional:
            raise PlanError("SSH machine has missing or unsupported fields")
        identifier(row["machine"], "SSH machine")
        _choice(row["environment"], ENVIRONMENTS, "environment")
        _choice(row["role"], ROLES, "role")
        for field in ("inbound", "outbound"):
            boolean(row[field], "SSH direction")
        if row["role"] == "client-only" and row["inbound"]:
            raise PlanError("client-only SSH role cannot request inbound access")
        folded = row["machine"].casefold()
        if folded in names and names[folded] != row["machine"]:
            raise PlanError("SSH machine names have a case collision")
        names[folded] = row["machine"]
        if _key(row) in targets:
            raise PlanError("duplicate SSH machine/environment declaration")
        targets.add(_key(row))
        snapshot = dict(row)
        if row["driver"] == "gateway":
            identifier(row["controller"], "SSH controller")
            if row["controller"] not in controllers:
                raise PlanError("SSH controller is not declared")
            boolean(row["gateway_egress"], "Gateway connector egress")
            if "host_hop" in row:
                _reference(row["host_hop"])
                if row["gateway_egress"]:
                    raise PlanError("local Gateway egress and host mediation are exclusive")
                snapshot["host_hop"] = dict(row["host_hop"])
        rows.append(snapshot)
    index = {_key(row): row for row in rows}
    for row in rows:
        seen, current = {_key(row)}, row
        while "host_hop" in current:
            key = _reference(current["host_hop"])
            if key in seen:
                raise PlanError("SSH host mediation contains a cycle")
            seen.add(key)
            host = index.get(key)
            if host is None:
                raise PlanError("SSH host reference is not declared")
            if host["driver"] != "gateway" or host["controller"] != row["controller"]:
                raise PlanError("SSH host mediation requires the same declared Gateway controller")
            current = host
    return {
        "schema": value["schema"], "version": value["version"], "revision": value["revision"],
        "controllers": sorted(controllers), "machines": sorted(rows, key=_key),
    }


def _validate_decision(value):
    canonical(value)
    record(value, "fleet-manager.gateway-ssh-decision", _DECISION_FIELDS)
    identifier(value["machine"], "SSH machine")
    identifier(value["policy_revision"], "SSH policy revision")
    _choice(value["environment"], ENVIRONMENTS, "environment")
    _choice(value["backend"], BACKENDS, "transport")
    _choice(value["role"], ROLES, "role")
    _choice(value["state"], ("configured", "offline", "disabled", "blocked"), "decision state")
    for field in (
        "requested_inbound", "requested_outbound", "effective_inbound", "effective_outbound",
        "gateway_egress", "execution_authorized", "live_readiness_verified",
    ):
        boolean(value[field], "SSH decision permission")
    if value["execution_authorized"] or value["live_readiness_verified"]:
        raise PlanError("SSH policy cannot assert execution or live readiness")
    if not isinstance(value["policy_digest"], str) or not SHA256.fullmatch(value["policy_digest"]):
        raise PlanError("SSH policy digest must be lowercase SHA-256")
    path = value["host_path"]
    if not isinstance(path, list):
        raise PlanError("SSH host path must be a bounded list")
    seen = {(value["machine"].casefold(), value["environment"])}
    for hop in path:
        machine, environment = _reference(hop)
        key = machine.casefold(), environment
        if key in seen:
            raise PlanError("SSH host path repeats a target")
        seen.add(key)
    if value["backend"] == "gateway":
        identifier(value["controller"], "SSH controller")
        if value["gateway_egress"] and path:
            raise PlanError("local Gateway egress and host mediation are exclusive")
    elif value["controller"] is not None or value["gateway_egress"] or path:
        raise PlanError("non-Gateway SSH decisions cannot select Gateway bindings")
    requested = value["requested_inbound"], value["requested_outbound"]
    effective = value["effective_inbound"], value["effective_outbound"]
    if value["role"] == "client-only" and requested[0]:
        raise PlanError("client-only SSH role cannot request inbound access")
    state, reason = value["state"], value["reason"]
    if not isinstance(reason, str):
        raise PlanError("unsupported SSH decision reason")
    if state != "configured" and any(effective):
        raise PlanError("nonconfigured SSH decision cannot assert effective permissions")
    if state == "offline":
        valid = value["role"] == "offline" and reason == "offline-role"
    elif value["role"] == "offline":
        valid = False
    elif state == "disabled":
        valid = not any(requested) and reason == "ssh-disabled"
    elif state == "blocked":
        valid = (
            value["backend"] == "gateway" and any(requested)
            and not value["gateway_egress"]
            and (reason == "gateway-egress-required"
                 or (reason == "host-hop-unavailable" and bool(path) and not requested[1]))
        )
    else:
        valid = any(requested) and effective == requested and reason == "declared-policy"
        if value["backend"] == "gateway":
            valid = valid and (value["gateway_egress"] or bool(path))
            valid = valid and (not requested[1] or value["gateway_egress"])
    if not valid:
        raise PlanError("SSH decision state does not match its declared permissions")


@dataclass(frozen=True)
class SSHPolicyDecision:
    """Immutable permission data, not proof of provenance or execution authority."""

    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes:
            raise PlanError("SSH decision requires immutable canonical bytes")
        value = decode(self.encoded)
        _validate_decision(value)
        if canonical(value) != self.encoded:
            raise PlanError("SSH decision encoding must be canonical")

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def decision_digest(self):
        return digest(self.to_dict())


@dataclass(frozen=True)
class GatewaySSHPolicy:
    """Normalized projection of supplied intent; never another topology registry.

    Root fields are schema/version, an identifier revision, a controller-ID list
    and machine rows. Each row requires machine/environment/driver/role and
    independent inbound/outbound booleans. Gateway rows also require controller
    and gateway_egress, and may select a strict machine/environment host_hop.
    Other drivers cannot carry Gateway-only fields. Lists are order-insensitive;
    canonical target spelling is exact and case collisions are refused.
    """

    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes:
            raise PlanError("SSH policy requires immutable normalized canonical bytes")
        if canonical(_normalize(decode(self.encoded))) != self.encoded:
            raise PlanError("SSH policy encoding must be normalized and canonical")

    @classmethod
    def from_dict(cls, value):
        return cls(canonical(_normalize(value)))

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def policy_digest(self):
        return digest(self.to_dict())

    def resolve(self, machine, environment):
        """Resolve only an exact declared target; no fallback, I/O or effects.

        All-or-none effective permissions fail closed on an unavailable host
        path. Every mediator must itself permit effective inbound access; a
        path cannot invent guest outbound connectivity or widen host policy.
        Even a configured decision requires independently trusted runtime
        observation, selected identity and explicit execution authorization.
        """
        identifier(machine, "SSH machine")
        _choice(environment, ENVIRONMENTS, "environment")
        policy = self.to_dict()
        index = {_key(row): row for row in policy["machines"]}
        row = index.get((machine, environment))
        if row is None:
            raise PlanError("SSH machine/environment is not declared")
        hosts, current = [], row
        while "host_hop" in current:
            current = index[_reference(current["host_hop"])]
            hosts.append(current)
        egress = row.get("gateway_egress", False)
        if row["role"] == "offline":
            state, reason = "offline", "offline-role"
        elif not row["inbound"] and not row["outbound"]:
            state, reason = "disabled", "ssh-disabled"
        elif row["driver"] == "gateway" and not egress:
            if row["outbound"] or not hosts:
                state, reason = "blocked", "gateway-egress-required"
            elif (not current["gateway_egress"] or any(
                host["role"] == "offline" or not host["inbound"]
                or (host["outbound"] and not host["gateway_egress"])
                for host in hosts
            )):
                state, reason = "blocked", "host-hop-unavailable"
            else:
                state, reason = "configured", "declared-policy"
        else:
            state, reason = "configured", "declared-policy"
        value = {
            "schema": "fleet-manager.gateway-ssh-decision", "version": 1,
            "machine": machine, "environment": environment, "backend": row["driver"],
            "role": row["role"], "controller": row.get("controller"),
            "state": state, "reason": reason,
            "requested_inbound": row["inbound"], "requested_outbound": row["outbound"],
            "effective_inbound": state == "configured" and row["inbound"],
            "effective_outbound": state == "configured" and row["outbound"],
            "gateway_egress": egress,
            "host_path": [{"machine": host["machine"], "environment": host["environment"]}
                          for host in hosts],
            "policy_revision": policy["revision"], "policy_digest": self.policy_digest,
            "execution_authorized": False, "live_readiness_verified": False,
        }
        return SSHPolicyDecision(canonical(value))
