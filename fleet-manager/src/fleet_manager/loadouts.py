"""Bounded template actualization and resource-grid design, with no live effects."""

from __future__ import annotations

import copy
import math

from .loadout_templates import builtin_templates
from .planning import (
    MAX_BYTES, MAX_ITEMS, MAX_PLAN_ITEMS, PlanError, boolean, canonical, configuration_capacity,
    digest, identifier, integer, record,
)
from .role_bindings import fields, reference, references

DIMENSIONS = ("cpu_millis", "memory_bytes", "disk_bytes")


def resources(value: object) -> dict[str, int]:
    entry = fields(value, set(DIMENSIONS))
    for name in DIMENSIONS:
        integer(entry[name], name)
    return dict(entry)


class _ExpansionBudget:
    def __init__(self) -> None:
        self.remaining = MAX_BYTES

    def take(self, size: int) -> None:
        if size > self.remaining:
            raise PlanError("loadout expansion exceeds byte budget")
        self.remaining -= size

    def mapping(self, value: dict) -> None:
        self.take(2 + max(0, len(value) - 1)
                  + sum(len(canonical(key)) + 1 for key in value))


def _expand(value: object, slots: dict, definitions: dict, budget: _ExpansionBudget) -> object:
    if isinstance(value, dict):
        if "slot" in value:
            fields(value, {"slot"})
            name = reference(value["slot"], "slot reference")
            if name not in definitions:
                raise PlanError("template refers to an undeclared slot")
            budget.take(len(canonical(slots[name])))
            return copy.deepcopy(slots[name])
        budget.mapping(value)
        return {key: _expand(child, slots, definitions, budget) for key, child in value.items()}
    if isinstance(value, list):
        budget.take(2 + max(0, len(value) - 1))
        return [_expand(child, slots, definitions, budget) for child in value]
    budget.take(len(canonical(value)))
    return value


def _slots(definitions: object, supplied: object) -> dict:
    if not isinstance(definitions, dict) or not isinstance(supplied, dict):
        raise PlanError("template slots must be objects")
    if definitions.keys() != supplied.keys():
        raise PlanError("template slots are missing or unsupported")
    for name, definition in definitions.items():
        identifier(name, "slot name")
        declaration = fields(definition, {"type"}, {"minimum", "maximum"})
        value = supplied[name]
        if declaration["type"] == "integer":
            minimum, maximum = declaration.get("minimum", 0), declaration.get("maximum", 2**63 - 1)
            integer(minimum, "slot minimum")
            integer(maximum, "slot maximum", minimum=minimum)
            integer(value, "slot integer", minimum=minimum, maximum=maximum)
        elif declaration == {"type": "reference"}:
            reference(value, "slot reference")
        elif declaration == {"type": "references"}:
            references(value, "slot references")
            if not isinstance(value, list):
                raise PlanError("slot references must be a JSON list")
        else:
            raise PlanError("unsupported slot type")
    return supplied


def _catalog(contributions: object) -> dict:
    record(contributions, "fleet-manager.loadout-catalog", {"templates"})
    supplied = contributions["templates"]
    if not isinstance(supplied, list) or len(supplied) >= MAX_ITEMS:
        raise PlanError("template catalog must be a bounded list")
    result = {}
    for template in builtin_templates() + supplied:
        record(template, "fleet-manager.loadout-template", {
            "id", "revision", "source", "slots", "provides", "requires", "resources", "configuration",
        })
        for name in ("id", "revision", "source"):
            identifier(template[name], "template provenance")
        if template["id"] in result:
            raise PlanError("duplicate template identity")
        if not isinstance(template["configuration"], dict):
            raise PlanError("template configuration must be an object")
        references(template["provides"], "provided capability")
        result[template["id"]] = template
    return result


def _host_inventory(inventory: object, fleet_id: str, now: float) -> dict:
    record(inventory, "fleet-manager.loadout-inventory", {
        "fleet_id", "revision", "observed_at", "expires_at", "complete", "hosts",
        "baseline_excludes",
    })
    identifier(inventory["revision"], "inventory revision")
    if inventory["fleet_id"] != fleet_id:
        raise PlanError("loadout inventory fleet mismatch")
    for key in ("observed_at", "expires_at"):
        integer(inventory[key], key)
    if not inventory["observed_at"] <= now < 2**63 or inventory["expires_at"] <= inventory["observed_at"]:
        raise PlanError("invalid inventory freshness interval")
    boolean(inventory["complete"], "inventory completeness")
    hosts = inventory["hosts"]
    if not isinstance(hosts, list):
        raise PlanError("loadout hosts must be a list")
    result = {}
    for host in hosts:
        fields(host, {"id", "owned", "ready", "capacity", "headroom", "reserved", "observed"})
        name = reference(host["id"], "host id")
        if name in result:
            raise PlanError("duplicate loadout host")
        boolean(host["owned"], "host ownership")
        boolean(host["ready"], "host readiness")
        for key in ("capacity", "headroom", "reserved"):
            resources(host[key])
        if host["observed"] is not None:
            resources(host["observed"])
        result[name] = host
    return result


def design_loadout(config: object, inventory: object, catalog: object | None = None, *, now: float) -> dict:
    """Actualize the registrar's loadouts; never publish, authorize or deploy."""
    if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < 2**63:
        raise PlanError("now must be a finite epoch time")
    catalog = catalog if catalog is not None else {
        "schema": "fleet-manager.loadout-catalog", "version": 1, "templates": [],
    }
    for value in (config, inventory, catalog):
        canonical(value)
    record(config, "fleet-manager.config", {"fleet_id", "revision", "capacity", "roles"}, {"template", "extensions"})
    identifier(config["fleet_id"], "fleet id")
    capacity = configuration_capacity(config, fleet_id=config["fleet_id"])
    extensions = config.get("extensions")
    if not isinstance(extensions, dict) or "loadouts" not in extensions:
        raise PlanError("config requires an explicit loadouts registrar")
    registrar = extensions["loadouts"]
    record(registrar, "fleet-manager.loadout-registrar", {"revision", "allocations"})
    identifier(registrar["revision"], "registrar revision")
    allocations = registrar["allocations"]
    if not isinstance(allocations, dict) or not allocations:
        raise PlanError("loadout allocations must be a nonempty object")
    templates = _catalog(catalog)
    hosts = _host_inventory(inventory, config["fleet_id"], now)
    if set(references(inventory["baseline_excludes"], "baseline allocation")) != set(allocations):
        raise PlanError("inventory baseline must explicitly exclude exactly the proposed allocations")
    blockers = []
    if not inventory["complete"] or now >= inventory["expires_at"]:
        blockers.append("inventory-stale-or-incomplete")
    if sum(host["owned"] for host in hosts.values()) > capacity["machine_limit"]:
        blockers.append("existing-capacity-exceeds-budget-retained")
    actual, runner_owners = {}, set()
    budget = _ExpansionBudget()
    for name, raw in sorted(allocations.items()):
        identifier(name, "allocation id")
        entry = fields(raw, {"template", "target", "slots"})
        selected = fields(entry["template"], {"id", "revision", "source"})
        template = templates.get(reference(selected["id"], "template id"))
        if template is None or any(selected[key] != template[key] for key in ("id", "revision", "source")):
            raise PlanError("unknown or mismatched contributed template")
        slots = _slots(template["slots"], entry["slots"])
        if not isinstance(template["requires"], dict):
            raise PlanError("template requirements must map capabilities to allocations")
        budget.mapping(template["requires"])
        requirements = {key: _expand(value, slots, template["slots"], budget)
                        for key, value in template["requires"].items()}
        for capability, dependency in requirements.items():
            reference(capability, "required capability")
            reference(dependency, "allocation dependency")
        required = tuple(sorted(set(requirements.values())))
        target = reference(entry["target"], "allocation target")
        demand = fields(_expand(template["resources"], slots, template["slots"], budget),
                        {"units", "reservation", "limit"})
        integer(demand["units"], "allocation units", minimum=1, maximum=MAX_ITEMS)
        reservation, limit = resources(demand["reservation"]), resources(demand["limit"])
        if any(limit[key] < reservation[key] for key in DIMENSIONS):
            raise PlanError("allocation limit is below its reservation")
        for values in (reservation, limit):
            for key in DIMENSIONS:
                values[key] *= demand["units"]
                integer(values[key], "total allocation resource")
        configuration = _expand(template["configuration"], slots, template["slots"], budget)
        if not isinstance(configuration, dict):
            raise PlanError("actualized configuration must be an object")
        if selected["id"] == "actions.arc":
            if slots["min_runners"] > slots["max_runners"]:
                raise PlanError("minimum runners exceeds maximum")
            if not all(reservation[key] > 0 for key in DIMENSIONS):
                raise PlanError("runner resource reservations must be positive")
            owner = (slots["cluster"], slots["scale_set"])
            if owner in runner_owners:
                raise PlanError("multiple allocations claim the same ARC scale set")
            runner_owners.add(owner)
        actual[name] = {
            "template": dict(selected), "target": target, "slots": copy.deepcopy(slots),
            "requires": list(required), "reservation": reservation, "limit": limit,
            "configuration": configuration, "template_digest": digest(template),
            "provides": list(references(template["provides"], "provided capability")),
            "requirements": requirements,
        }
        if target not in hosts:
            blockers.append(f"target-missing:{name}")
        elif not hosts[target]["owned"] or not hosts[target]["ready"]:
            blockers.append(f"target-not-admitted:{name}")
    order, visiting, visited = [], set(), set()

    def visit(name: str) -> None:
        if name in visiting:
            raise PlanError("loadout dependency cycle")
        if name in visited:
            return
        if name not in actual:
            raise PlanError("loadout dependency is not allocated")
        visiting.add(name)
        for dependency in actual[name]["requires"]:
            visit(dependency)
        for capability, dependency in actual[name]["requirements"].items():
            if capability not in actual[dependency]["provides"]:
                raise PlanError("allocation dependency does not provide required capability")
        visiting.remove(name)
        visited.add(name)
        order.append(name)

    for name in actual:
        visit(name)
    grid = {}
    for name, host in sorted(hosts.items()):
        selected = [entry for entry in actual.values() if entry["target"] == name]
        proposed = {key: sum(entry["reservation"][key] for entry in selected) for key in DIMENSIONS}
        limits = {key: sum(entry["limit"][key] for entry in selected) for key in DIMENSIONS}
        for values in (proposed, limits):
            for value in values.values():
                integer(value, "host resource total")
        baseline = {key: max(host["reserved"][key], (host["observed"] or {}).get(key, 0))
                    for key in DIMENSIONS}
        available = {key: host["capacity"][key] - host["headroom"][key] - baseline[key]
                     for key in DIMENSIONS}
        remaining = {key: available[key] - proposed[key] for key in DIMENSIONS}
        burst_remaining = {key: available[key] - limits[key] for key in DIMENSIONS}
        if selected:
            for key in DIMENSIONS:
                if remaining[key] < 0:
                    blockers.append(f"capacity-exceeded:{name}:{key}")
                elif burst_remaining[key] < 0:
                    blockers.append(f"burst-capacity-exceeded:{name}:{key}")
            if host["observed"] is None:
                blockers.append(f"observed-usage-unknown:{name}")
        grid[name] = {
            **{key: copy.deepcopy(host[key]) for key in ("capacity", "headroom", "reserved", "observed")},
            "proposed_reservation": proposed, "proposed_limit": limits, "remaining": remaining,
            "baseline": baseline, "burst_remaining": burst_remaining,
        }
    composition = {
        "schema": "fleet-manager.loadout-composition", "version": 1,
        "fleet_id": config["fleet_id"], "registrar_revision": registrar["revision"],
        "allocations": actual, "dependency_order": order,
    }
    suggested = copy.deepcopy(config)
    suggested["extensions"]["loadout_composition"] = copy.deepcopy(composition)
    canonical(suggested)
    diagnostics = sorted(set(blockers))
    result = {
        "schema": "fleet-manager.loadout-design", "version": 1,
        "status": "blocked" if diagnostics else "review-required",
        "blockers": diagnostics[:MAX_PLAN_ITEMS], "blocker_count": len(diagnostics),
        "blockers_omitted": max(0, len(diagnostics) - MAX_PLAN_ITEMS),
        "grid": grid, "composition": composition,
        "suggested_config": suggested,
        "source_digests": {key: digest(value) for key, value in (
            ("config", config), ("inventory", inventory), ("catalog", catalog),
        )},
        "inventory_expires_at": inventory["expires_at"], "mutations_performed": False,
        "activation_authorized": False, "live_readiness_verified": False,
    }
    result["digest"] = digest(result, output=True)
    canonical(result, output=True)
    return result
