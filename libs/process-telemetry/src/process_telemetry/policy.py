"""Composed, capability-aware command telemetry policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Literal, cast

from configuration_layers import ConfigurationError, compose, plan_layers

from .command_config import ProviderCapabilities

POLICY_VERSION = 2
POLICY_FIELD = "telemetry"
USAGES = frozenset({"lifecycle"})
SOURCE_SCHEMAS = frozenset({"bridge.lifecycle.v1", "dispatch.lifecycle.v1"})
TRANSPORTS = frozenset({"command-jsonl-v1"})
_NAME = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_MISSING = object()


@dataclass(frozen=True)
class SinkDefinition:
    """Harness-declared command sink and its closed route capabilities."""

    name: str
    transport: str
    argv: tuple[str, ...]
    source_schemas: frozenset[str]


@dataclass(frozen=True)
class PolicyOrigin:
    """Origin data supplied by an existing, separately authorized owner."""

    repository_id: str
    worktree_id: str | None = None


@dataclass(frozen=True)
class Scope:
    repositories: frozenset[str] | None
    worktrees: frozenset[str] | None


@dataclass(frozen=True)
class PolicyDecision:
    state: Literal[
        "active",
        "unconfigured",
        "disabled",
        "origin_unavailable",
        "excluded",
        "out_of_scope",
        "unsupported_source",
        "unsupported_provider",
    ]
    sink: SinkDefinition | None = None
    provenance: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PolicySnapshot:
    """Immutable policy snapshot; resolving it never starts a sink."""

    sinks: Mapping[str, SinkDefinition]
    defaults: Mapping[str, str | None]
    usages: Mapping[str, str | None]
    scopes: Mapping[str, Scope]
    exclusions: Mapping[str, Scope]
    opted_out: frozenset[str]
    provenance: Mapping[str, Mapping[str, Any]]
    scope_sources: Mapping[str, tuple[Mapping[str, Any], ...]]
    exclusion_sources: Mapping[str, tuple[Mapping[str, Any], ...]]
    opt_out_sources: Mapping[str, tuple[Mapping[str, Any], ...]]
    layers: tuple[str, ...]
    configured: bool

    def resolve(
        self,
        usage: str,
        origin: PolicyOrigin | None,
        source_schema: str,
        provider: ProviderCapabilities | None,
    ) -> PolicyDecision:
        """Resolve an already-admitted operation against this policy snapshot."""
        if usage not in USAGES or source_schema not in SOURCE_SCHEMAS:
            raise ConfigurationError("unknown telemetry usage or source schema")
        if usage in self.opted_out:
            return PolicyDecision("disabled")
        sink_name = self.usages.get(usage, self.defaults.get(usage, _MISSING))
        if sink_name is _MISSING:
            return PolicyDecision("unconfigured")
        if sink_name is None:
            return PolicyDecision("disabled")
        if type(sink_name) is not str:
            raise ConfigurationError("invalid telemetry sink selection")
        if origin is None:
            return PolicyDecision("origin_unavailable")
        _identity(origin.repository_id)
        if origin.worktree_id is not None:
            _identity(origin.worktree_id)

        scope = self.scopes[usage]
        excluded = self.exclusions.get(usage)
        if excluded is not None and (
            excluded.repositories is not None
            and origin.repository_id in excluded.repositories
            or excluded.worktrees is not None
            and origin.worktree_id is not None
            and origin.worktree_id in excluded.worktrees
        ):
            return PolicyDecision("excluded")
        if (
            scope.repositories is None
            or origin.repository_id not in scope.repositories
            or scope.worktrees is not None
            and (origin.worktree_id is None or origin.worktree_id not in scope.worktrees)
        ):
            return PolicyDecision("out_of_scope")

        sink = self.sinks[sink_name]
        if source_schema not in sink.source_schemas:
            return PolicyDecision("unsupported_source")
        if provider is None or (
            POLICY_VERSION not in provider.policy_versions
            or sink.transport not in provider.transports
            or source_schema not in provider.source_schemas
        ):
            return PolicyDecision("unsupported_provider")
        pointer = f"/{POLICY_FIELD}/usages/{usage}"
        if usage not in self.usages:
            pointer = f"/{POLICY_FIELD}/defaults/{usage}"
        provenance = self.provenance.get(pointer)
        return PolicyDecision(
            "active",
            sink,
            _layer_provenance(provenance) if provenance is not None else None,
        )


def _identity(value: object) -> str:
    if type(value) is not str or not value or value.strip() != value or "\0" in value:
        raise ConfigurationError("telemetry scope identity must be a nonempty exact string")
    return value


def _name(value: object) -> str:
    if type(value) is not str or not _NAME.fullmatch(value):
        raise ConfigurationError("invalid telemetry policy key")
    return value


def _mapping(value: object, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ConfigurationError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _identities(value: object, field: str) -> frozenset[str]:
    if type(value) is not list:
        raise ConfigurationError(f"{field} must be an array")
    return frozenset(_identity(item) for item in cast(list[object], value))


def _scopes(value: object, *, base: bool, field: str) -> Scope:
    record = _mapping(value, field)
    if set(record) - {"repositories", "worktrees"}:
        raise ConfigurationError(f"unknown {field} field")
    if base and "repositories" not in record:
        raise ConfigurationError("harness telemetry scope requires repositories")
    if not record:
        raise ConfigurationError(f"{field} must narrow at least one scope")
    repositories = (
        _identities(record["repositories"], f"{field}.repositories")
        if "repositories" in record
        else frozenset()
    )
    worktrees = (
        _identities(record["worktrees"], f"{field}.worktrees") if "worktrees" in record else None
    )
    return Scope(
        repositories if "repositories" in record else None,
        worktrees,
    )


def _sink(name: str, value: object) -> SinkDefinition:
    record = _mapping(value, "sink")
    if set(record) != {"transport", "argv", "source_schemas"}:
        raise ConfigurationError("invalid telemetry sink declaration")
    transport = record["transport"]
    if type(transport) is not str or transport not in TRANSPORTS:
        raise ConfigurationError("unsupported telemetry transport")
    argv = record["argv"]
    if (
        type(argv) is not list
        or not 1 <= len(argv) <= 32
        or any(
            type(arg) is not str or not arg or len(arg) > 512 or "\0" in arg
            for arg in cast(list[object], argv)
        )
        or sum(len(arg.encode("utf-8")) for arg in cast(list[str], argv)) > 8192
    ):
        raise ConfigurationError("invalid telemetry sink argv")
    source_schemas = record["source_schemas"]
    if type(source_schemas) is not list or not source_schemas:
        raise ConfigurationError("sink source_schemas must be a nonempty array")
    source_values = cast(list[object], source_schemas)
    if any(type(source) is not str or source not in SOURCE_SCHEMAS for source in source_values):
        raise ConfigurationError("unsupported telemetry source schema")
    sources = frozenset(cast(list[str], source_values))
    if len(sources) != len(source_values):
        raise ConfigurationError("duplicate telemetry source schema")
    return SinkDefinition(name, transport, tuple(cast(list[str], argv)), sources)


def _selections(value: object, field: str) -> dict[str, str | None]:
    record = _mapping(value, field)
    result: dict[str, str | None] = {}
    for raw_usage, raw_sink in record.items():
        usage = _name(raw_usage)
        if (
            usage not in USAGES
            or raw_sink is not None
            and (type(raw_sink) is not str or not _NAME.fullmatch(raw_sink))
        ):
            raise ConfigurationError(f"invalid telemetry {field} entry")
        result[usage] = raw_sink
    return result


def _validate_document(layer: Mapping[str, Any]) -> dict[str, Any] | None:
    values = _mapping(layer["values"], "layer values")
    if POLICY_FIELD not in values:
        return None
    if layer["origin"] == "defaults":
        raise ConfigurationError("telemetry policy cannot come from built-in defaults")
    document = _mapping(values[POLICY_FIELD], POLICY_FIELD)
    allowed = {
        "schema_version",
        "sinks",
        "defaults",
        "usages",
        "scopes",
        "exclusions",
        "opt_out",
    }
    if (
        set(document) - allowed
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != POLICY_VERSION
    ):
        raise ConfigurationError("invalid or unsupported telemetry policy")
    if layer["origin"] != "base" and ({"sinks", "defaults"} & document.keys()):
        raise ConfigurationError("only the harness repository may define sinks and defaults")
    if "sinks" in document:
        sinks = _mapping(document["sinks"], "sinks")
        for raw_name, value in sinks.items():
            _sink(_name(raw_name), value)
    for field in ("defaults", "usages"):
        if field in document:
            _selections(document[field], field)
    for field in ("scopes", "exclusions"):
        if field in document:
            entries = _mapping(document[field], field)
            for raw_usage, value in entries.items():
                usage = _name(raw_usage)
                if usage not in USAGES:
                    raise ConfigurationError(f"unknown telemetry {field} usage")
                _scopes(
                    value,
                    base=field == "scopes" and layer["origin"] == "base",
                    field=f"{field}.{usage}",
                )
    if "opt_out" in document:
        entries = _mapping(document["opt_out"], "opt_out")
        if any(
            _name(usage) not in USAGES or type(enabled) is not bool
            for usage, enabled in entries.items()
        ):
            raise ConfigurationError("invalid telemetry opt_out entry")
    return document


def _narrow_scope(current: Scope, candidate: Scope) -> Scope:
    if candidate.repositories is None:
        repositories = current.repositories
    elif current.repositories is None:
        repositories = candidate.repositories
    else:
        repositories = current.repositories & candidate.repositories
    if candidate.worktrees is None:
        worktrees = current.worktrees
    elif current.worktrees is None:
        worktrees = candidate.worktrees
    else:
        worktrees = current.worktrees & candidate.worktrees
    return Scope(repositories, worktrees)


def _layer_provenance(layer: Mapping[str, Any]) -> Mapping[str, Any]:
    owner = {key: value for key, value in layer.items() if key != "values"}
    if "source" in owner:
        owner["source"] = MappingProxyType(dict(owner["source"]))
    return MappingProxyType(owner)


def _merge_scopes(
    selected: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Scope], dict[str, tuple[Mapping[str, Any], ...]]]:
    scopes: dict[str, Scope] = {}
    sources: dict[str, list[Mapping[str, Any]]] = {}
    for layer in selected:
        document = _validate_document(layer)
        if document is None or "scopes" not in document:
            continue
        for usage, value in document["scopes"].items():
            candidate = _scopes(value, base=layer["origin"] == "base", field=f"scopes.{usage}")
            if usage not in scopes:
                if layer["origin"] != "base":
                    raise ConfigurationError(
                        "telemetry scope cannot expand a missing harness scope"
                    )
                scopes[usage] = candidate
                sources[usage] = []
            else:
                scopes[usage] = _narrow_scope(scopes[usage], candidate)
            sources[usage].append(_layer_provenance(layer))
    return scopes, {usage: tuple(owners) for usage, owners in sources.items()}


def _merge_exclusions(
    selected: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Scope], dict[str, tuple[Mapping[str, Any], ...]]]:
    repositories: dict[str, set[str]] = {}
    worktrees: dict[str, set[str]] = {}
    sources: dict[str, list[Mapping[str, Any]]] = {}
    for layer in selected:
        document = _validate_document(layer)
        if document is None or "exclusions" not in document:
            continue
        for usage, value in document["exclusions"].items():
            entry = _scopes(value, base=False, field=f"exclusions.{usage}")
            if entry.repositories is not None:
                repositories.setdefault(usage, set()).update(entry.repositories)
            worktrees.setdefault(usage, set()).update(entry.worktrees or ())
            sources.setdefault(usage, []).append(_layer_provenance(layer))
    combined = {
        usage: Scope(
            frozenset(repositories[usage]) if usage in repositories else None,
            frozenset(worktrees[usage]) if usage in worktrees else None,
        )
        for usage in set(repositories) | set(worktrees)
    }
    return combined, {usage: tuple(owners) for usage, owners in sources.items()}


def compose_policy(context: object, layers: object) -> PolicySnapshot:
    """Compose owner-selected layers; never discovers roots or starts a sink."""
    selected = plan_layers(context, layers)
    policy_layers = [layer for layer in selected if _validate_document(layer) is not None]
    if not policy_layers:
        return PolicySnapshot(
            MappingProxyType({}),
            MappingProxyType({}),
            MappingProxyType({}),
            MappingProxyType({}),
            MappingProxyType({}),
            frozenset(),
            MappingProxyType({}),
            MappingProxyType({}),
            MappingProxyType({}),
            MappingProxyType({}),
            tuple(layer["id"] for layer in selected),
            False,
        )

    composed = compose(context, selected)
    values = _mapping(composed["values"].get(POLICY_FIELD), POLICY_FIELD)
    sinks: dict[str, SinkDefinition] = {}
    for layer in policy_layers:
        document = _validate_document(layer)
        if document is None:
            continue
        for raw_name, value in document.get("sinks", {}).items():
            name = _name(raw_name)
            if name in sinks:
                raise ConfigurationError("duplicate telemetry sink definition")
            sinks[name] = _sink(name, value)
    defaults = _selections(values.get("defaults", {}), "defaults")
    usages = _selections(values.get("usages", {}), "usages")
    scopes, scope_sources = _merge_scopes(policy_layers)
    exclusions, exclusion_sources = _merge_exclusions(policy_layers)
    opted_out: set[str] = set()
    opt_out_sources: dict[str, list[Mapping[str, Any]]] = {}
    for layer in policy_layers:
        document = _validate_document(layer)
        if document is None:
            continue
        for usage, enabled in document.get("opt_out", {}).items():
            if enabled:
                opted_out.add(usage)
                opt_out_sources.setdefault(usage, []).append(_layer_provenance(layer))

    for selections in (defaults, usages):
        for usage, selected_name in selections.items():
            if selected_name is not None and selected_name not in sinks:
                raise ConfigurationError("telemetry usage selects an undefined sink")
            if selected_name is not None and usage not in scopes:
                raise ConfigurationError("selected telemetry usage has no harness scope")
    for usage in exclusions:
        if usage not in scopes:
            raise ConfigurationError("telemetry exclusion has no harness scope")
    provenance = {
        key: _layer_provenance(owner)
        for key, owner in composed["provenance"].items()
        if key.startswith(f"/{POLICY_FIELD}/")
    }
    return PolicySnapshot(
        MappingProxyType(sinks),
        MappingProxyType(defaults),
        MappingProxyType(usages),
        MappingProxyType(scopes),
        MappingProxyType(exclusions),
        frozenset(opted_out),
        MappingProxyType(provenance),
        MappingProxyType(scope_sources),
        MappingProxyType(exclusion_sources),
        MappingProxyType({usage: tuple(owners) for usage, owners in opt_out_sources.items()}),
        tuple(layer["id"] for layer in selected),
        True,
    )
