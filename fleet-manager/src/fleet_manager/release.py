"""Role-selective release contracts and inspection of explicit local snapshots."""

import math
import re

from .planning import MAX_ITEMS, PlanError, canonical, digest, identifier, integer, record

ROLES = ("client", "gateway", "node")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def _hash(value, label="artifact digest"):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise PlanError(f"{label} must be lowercase SHA-256")


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PlanError(f"invalid {label} fields; expected: {', '.join(sorted(expected))}")


def _range(value):
    _fields(value, ("min", "max"), "API range")
    integer(value["min"], "API minimum", minimum=1, maximum=65535)
    integer(value["max"], "API maximum", minimum=value["min"], maximum=65535)


def _schemas(value):
    if not isinstance(value, list) or not value or len(value) > 32:
        raise PlanError("data schemas must be a nonempty bounded list")
    for schema in value:
        integer(schema, "data schema", minimum=1, maximum=65535)
    if len(set(value)) != len(value):
        raise PlanError("duplicate data schema")


def validate_release(value):
    """Validate declarations, not artifact availability, signatures or readiness."""
    canonical(value)
    record(value, "fleet-manager.release", {"id", "components", "roles"})
    identifier(value["id"], "release id")
    components = value["components"]
    if not isinstance(components, dict) or not 1 <= len(components) <= MAX_ITEMS:
        raise PlanError("release components must be a nonempty bounded object")
    for name, component in components.items():
        identifier(name, "component id")
        _fields(component, ("kind", "artifact_sha256", "api", "data_schemas"), "component")
        if component["kind"] not in ("client", "control", "executor"):
            raise PlanError("unsupported component kind")
        _hash(component["artifact_sha256"])
        _range(component["api"])
        _schemas(component["data_schemas"])
    _fields(value["roles"], ROLES, "release roles")
    referenced = set()
    for role, names in value["roles"].items():
        if not isinstance(names, list) or not names:
            raise PlanError("role requires a nonempty component list")
        for name in names:
            identifier(name, "role component id")
            if name not in components:
                raise PlanError("role references undeclared component")
            if role == "client" and components[name]["kind"] != "client":
                raise PlanError("client role cannot require control or executor components")
        if len(set(names)) != len(names):
            raise PlanError("role contains duplicate component")
        referenced.update(names)
    if referenced != set(components):
        raise PlanError("release contains unreferenced components")
    return value


def _time(value, name):
    if type(value) not in (int, float) or not 0 <= value <= 2**63 - 1 or not math.isfinite(value):
        raise PlanError(f"{name} must be a finite epoch time")


def _snapshot(value):
    canonical(value)
    record(value, "fleet-manager.installation-observation",
           {"authority", "installation", "release_digest", "observed_at",
            "expires_at", "complete", "components"})
    identifier(value["authority"], "authority id")
    identifier(value["installation"], "installation id")
    _hash(value["release_digest"], "release digest")
    _time(value["observed_at"], "observation time")
    _time(value["expires_at"], "expiry time")
    if value["expires_at"] <= value["observed_at"]:
        raise PlanError("observation expiry must follow observation time")
    if type(value["complete"]) is not bool:
        raise PlanError("observation completeness must be boolean")
    if not isinstance(value["components"], dict) or len(value["components"]) > MAX_ITEMS:
        raise PlanError("observed components must be a bounded object")
    for name, observed in value["components"].items():
        identifier(name, "observed component id")
        _fields(observed, ("artifact_sha256", "api", "data_schema", "state"),
                "component observation")
        _hash(observed["artifact_sha256"])
        integer(observed["api"], "observed API", minimum=1, maximum=65535)
        integer(observed["data_schema"], "observed data schema", minimum=1, maximum=65535)
        if observed["state"] not in ("installed", "ready", "unknown"):
            raise PlanError("unsupported observed state")


def inspect_release(release, observation, *, role, authority, installation, now):
    """Return snapshot assertions only; never probe, download, activate or repair."""
    validate_release(release)
    if role not in ROLES:
        raise PlanError("unsupported installation role")
    identifier(authority, "expected authority id")
    identifier(installation, "expected installation id")
    _time(now, "inspection time")
    selected_digest = digest(release)
    required = sorted(release["roles"][role])
    reason = None
    if observation is None:
        reason = "observation-not-supplied"
    else:
        _snapshot(observation)
        if (observation["authority"], observation["installation"]) != (authority, installation):
            raise PlanError("observation authority or installation mismatch")
        if observation["release_digest"] != selected_digest:
            reason = "release-mismatch"
        elif observation["observed_at"] > now:
            reason = "observation-from-future"
        elif now >= observation["expires_at"]:
            reason = "observation-expired"
        elif not observation["complete"]:
            reason = "observation-incomplete"
    results = []
    for name in required:
        declaration = release["components"][name]
        observed = observation["components"].get(name) if observation else None
        if reason:
            state = "incompatible" if reason == "release-mismatch" else "unknown"
            component_reason = reason
        elif observed is None:
            state, component_reason = "missing", "not-in-complete-observation"
        elif observed["artifact_sha256"] != declaration["artifact_sha256"]:
            state, component_reason = "incompatible", "artifact-mismatch"
        elif not declaration["api"]["min"] <= observed["api"] <= declaration["api"]["max"]:
            state, component_reason = "incompatible", "api-mismatch"
        elif observed["data_schema"] not in declaration["data_schemas"]:
            state, component_reason = "incompatible", "data-schema-mismatch"
        else:
            state, component_reason = observed["state"], "snapshot-assertion"
        results.append({"component": name, "state": state, "reason": component_reason})
    states = {item["state"] for item in results}
    status = next((state for state in ("incompatible", "missing", "unknown", "installed")
                   if state in states), "ready")
    result = {
        "schema": "fleet-manager.release-inspection", "version": 1,
        "release": release["id"], "release_digest": selected_digest, "role": role,
        "authority": authority, "installation": installation,
        "inspected_at": now,
        "observed_at": observation["observed_at"] if observation else None,
        "valid_until": observation["expires_at"] if observation else None,
        "status": status, "components": results, "evidence": "supplied-snapshot-only",
        "live_readiness_verified": False, "mutations_performed": False,
    }
    canonical(result)
    return result
