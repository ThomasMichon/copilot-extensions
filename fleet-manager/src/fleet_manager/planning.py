"""Compare recommendations, adopter choices and authoritative capacity snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

MAX_BYTES = 1024 * 1024
MAX_ITEMS = 256
MAX_PLAN_BYTES = 4 * MAX_BYTES
MAX_PLAN_ITEMS = 2 * MAX_ITEMS + 1
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class PlanError(ValueError):
    """Invalid local input; diagnostics intentionally exclude supplied values."""


def identifier(value, name):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PlanError(f"{name} must be a bounded identifier")


def integer(value, name, *, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PlanError(f"{name} must be an integer between {minimum} and {maximum}")


def boolean(value, name):
    if type(value) is not bool:
        raise PlanError(f"{name} must be boolean")


def record(value, schema, required, optional=()):
    if not isinstance(value, dict):
        raise PlanError("input must be an object")
    fields = {"schema", "version", *required}
    if not fields <= value.keys() or value.keys() - fields - set(optional):
        raise PlanError("input has missing or unsupported fields")
    if value["schema"] != schema or type(value["version"]) is not int or value["version"] != 1:
        raise PlanError("unsupported input schema/version")


def _tree(value, depth=0, *, output=False):
    label = "plan output" if output else "input"
    if depth > (15 if output else 12):
        raise PlanError(f"{label} exceeds nesting limit")
    if isinstance(value, dict):
        if len(value) > (MAX_PLAN_ITEMS if output else MAX_ITEMS) or any(not isinstance(key, str) for key in value):
            raise PlanError(f"{label} object exceeds bounds")
        for key, child in value.items():
            if len(key.encode("utf-8")) > 256:
                raise PlanError(f"{label} key exceeds bounds")
            _tree(child, depth + 1, output=output)
    elif isinstance(value, list):
        if len(value) > (MAX_PLAN_ITEMS if output else MAX_ITEMS):
            raise PlanError(f"{label} collection exceeds bounds")
        for child in value:
            _tree(child, depth + 1, output=output)
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > 65536:
            raise PlanError(f"{label} text exceeds bounds")
    elif value is not None and type(value) not in (bool, int, float):
        raise PlanError(f"{label} contains unsupported value")
    elif type(value) is float and not math.isfinite(value):
        raise PlanError(f"{label} contains nonfinite number")


def canonical(value, *, output=False):
    label = "plan output" if output else "input"
    try:
        _tree(value, output=output)
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except PlanError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError(f"invalid bounded JSON {label}") from exc
    if len(raw) > (MAX_PLAN_BYTES if output else MAX_BYTES):
        raise PlanError(f"{label} exceeds byte limit")
    return raw


def digest(value, *, output=False):
    return hashlib.sha256(canonical(value, output=output)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlanError("input contains duplicate JSON keys")
        result[key] = value
    return result


def decode(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise PlanError("input exceeds byte limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        canonical(value)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError("invalid bounded JSON input") from exc
    return value


def _roles(value):
    if not isinstance(value, dict) or len(value) > MAX_ITEMS:
        raise PlanError("roles must be a bounded object")
    for name, role in value.items():
        identifier(name, "role id")
        if not isinstance(role, dict):
            raise PlanError("role declaration must be an object")


def configuration_capacity(config, *, fleet_id):
    """Validate the shared adopter contract without altering custom declarations."""
    record(config, "fleet-manager.config", {"fleet_id", "revision", "capacity", "roles"},
           {"template", "extensions"})
    if config["fleet_id"] != fleet_id:
        raise PlanError("adopter fleet identity mismatch")
    identifier(config["revision"], "config revision")
    capacity = config["capacity"]
    if not isinstance(capacity, dict) or set(capacity) != {"machine_limit", "execution_hosts"}:
        raise PlanError("unsupported capacity declaration")
    integer(capacity["machine_limit"], "configured machine limit", minimum=1, maximum=MAX_ITEMS)
    integer(capacity["execution_hosts"], "configured execution hosts", maximum=MAX_ITEMS)
    _roles(config["roles"])
    if "template" in config:
        provenance = config["template"]
        if not isinstance(provenance, dict) or set(provenance) != {"id", "revision"}:
            raise PlanError("unsupported template provenance")
        identifier(provenance["id"], "template provenance id")
        identifier(provenance["revision"], "template provenance revision")
    return capacity


def plan(template, config, live, *, fleet_id, machine_limit, now):
    """Return proposals only; never mutate input, create hosts, or retire resources."""
    identifier(fleet_id, "fleet id")
    integer(machine_limit, "machine limit", minimum=1, maximum=MAX_ITEMS)
    if type(now) not in (int, float) or not 0 <= now <= 2**63 - 1 or not math.isfinite(now):
        raise PlanError("now must be a finite epoch time")
    for value in (template, config, live):
        canonical(value)
    record(template, "fleet-manager.template", {"id", "revision", "execution_hosts", "roles"})
    identifier(template["id"], "template id")
    identifier(template["revision"], "template revision")
    integer(template["execution_hosts"], "execution host recommendation", maximum=MAX_ITEMS)
    _roles(template["roles"])
    for role in template["roles"].values():
        if set(role) != {"kind", "required", "available"} or role["kind"] not in ("service", "execution"):
            raise PlanError("unsupported template role contract")
        boolean(role["required"], "required")
        boolean(role["available"], "available")
    if not any(role["required"] and role["kind"] == "service" for role in template["roles"].values()):
        raise PlanError("template requires at least one central service role")

    if config is not None:
        capacity = configuration_capacity(config, fleet_id=fleet_id)
    else:
        capacity = {"machine_limit": machine_limit, "execution_hosts": template["execution_hosts"]}

    record(live, "fleet-manager.inventory",
           {"provider", "revision", "fleet_id", "observed_at", "expires_at",
            "complete", "creation_allowed", "quota_remaining", "hosts"})
    identifier(live["provider"], "provider id")
    identifier(live["revision"], "inventory revision")
    if live["fleet_id"] != fleet_id:
        raise PlanError("inventory fleet identity mismatch")
    for name in ("observed_at", "expires_at"):
        integer(live[name], name)
    if live["expires_at"] <= live["observed_at"] or live["observed_at"] > now:
        raise PlanError("invalid inventory freshness interval")
    boolean(live["complete"], "inventory completeness")
    boolean(live["creation_allowed"], "provider creation admission")
    quota = live["quota_remaining"]
    if quota is not None:
        integer(quota, "provider remaining quota")
    hosts = live["hosts"]
    if not isinstance(hosts, list) or len(hosts) > MAX_ITEMS:
        raise PlanError("hosts must be a bounded collection")
    ids = set()
    for host in hosts:
        if not isinstance(host, dict) or set(host) != {"id", "owned", "ready", "roles"}:
            raise PlanError("unsupported host snapshot")
        identifier(host["id"], "host id")
        if host["id"] in ids:
            raise PlanError("duplicate host id")
        ids.add(host["id"])
        boolean(host["owned"], "host ownership")
        boolean(host["ready"], "host readiness")
        if not isinstance(host["roles"], dict) or len(host["roles"]) > MAX_ITEMS:
            raise PlanError("host roles must be bounded")
        for role, status in host["roles"].items():
            identifier(role, "host role id")
            if status not in ("ready", "installed", "unavailable", "unknown"):
                raise PlanError("unsupported observed role state")

    proposed = copy.deepcopy(config) if config is not None else {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": fleet_id,
        "revision": "unapplied", "capacity": copy.deepcopy(capacity), "roles": {},
    }
    suggestions = []
    for name in sorted(template["roles"]):
        recommendation = template["roles"][name]
        if name not in proposed["roles"]:
            proposed["roles"][name] = copy.deepcopy(recommendation)
            suggestions.append({"role": name, "change": "add"})
        elif proposed["roles"][name] != recommendation:
            suggestions.append({"role": name, "change": "retain-custom"})
    if config is None:
        proposed["template"] = {"id": template["id"], "revision": template["revision"]}
    elif config.get("template") != {"id": template["id"], "revision": template["revision"]}:
        suggestions.append({"change": "review-template-revision"})

    blockers = []
    if config is not None and "template" in config and config["template"] != {
        "id": template["id"], "revision": template["revision"],
    }:
        blockers.append("template-provenance-mismatch")
    fresh = live["complete"] and live["expires_at"] > now
    if not fresh:
        blockers.append("inventory-stale-or-incomplete")
    owned = [host for host in hosts if host["owned"]]
    ready = [host for host in owned if host["ready"]]
    effective_limit = min(machine_limit, capacity["machine_limit"])
    desired = 1 + capacity["execution_hosts"]
    budgeted = min(desired, effective_limit)
    if desired > effective_limit:
        blockers.append("requested-capacity-exceeds-budget")
    if len(owned) > effective_limit:
        blockers.append("existing-capacity-exceeds-budget-retained")
    need = max(0, budgeted - len(ready))
    remaining_budget = max(0, effective_limit - len(owned))
    create = min(need, remaining_budget, quota or 0) if fresh and live["creation_allowed"] else 0
    if need and quota is None:
        blockers.append("provider-quota-unknown")
    if need and not live["creation_allowed"]:
        blockers.append("creation-not-admitted")
    if need > create:
        blockers.append("capacity-unmet")

    role_observations = []
    for name in sorted(proposed["roles"]):
        observed = sorted(host["id"] for host in ready if host["roles"].get(name) == "ready") if fresh else []
        recommended = template["roles"].get(name)
        if recommended is not None and recommended["required"] and not recommended["available"]:
            blockers.append("required-role-unsupported:" + name)
        role_observations.append({
            "role": name, "ready_hosts": observed,
            "state": "observed-ready" if observed else "not-proven-ready",
        })
    result = {
        "schema": "fleet-manager.plan", "version": 1, "fleet_id": fleet_id,
        "input_digests": {"template": digest(template), "config": digest(config), "inventory": digest(live),
                         "policy": digest({"fleet_id": fleet_id, "machine_limit": machine_limit})},
        "valid_until": live["expires_at"], "effective_machine_limit": effective_limit,
        "retained_hosts": sorted(ids), "suggested_config": proposed, "config_changes": suggestions,
        "capacity": {"owned": len(owned), "ready": len(ready), "requested": desired,
                     "proposed_creations": create, "service_hosts": 1,
                     "budgeted_execution_hosts": max(0, budgeted - 1)},
        "roles": role_observations, "blockers": sorted(set(blockers)),
        "status": "blocked" if blockers else "review-required",
        "mutations_performed": False, "cleanup_actions": [],
    }
    result["plan_digest"] = digest(result, output=True)
    canonical(result, output=True)
    return result


def verify_plan(saved, template, config, live, *, fleet_id, machine_limit, now):
    """Validate all planning assumptions before a future, separately authorized apply."""
    if not isinstance(saved, dict) or "plan_digest" not in saved:
        raise PlanError("invalid saved plan")
    body = {key: value for key, value in saved.items() if key != "plan_digest"}
    canonical(saved, output=True)
    if digest(body, output=True) != saved["plan_digest"]:
        raise PlanError("saved plan integrity mismatch")
    current = plan(template, config, live, fleet_id=fleet_id, machine_limit=machine_limit, now=now)
    if current["plan_digest"] != saved["plan_digest"] or live["expires_at"] <= now:
        raise PlanError("plan assumptions changed or expired")
    if saved["blockers"]:
        raise PlanError("plan has unresolved blockers")
    return current
