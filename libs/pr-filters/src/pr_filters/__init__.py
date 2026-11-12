"""Pure bounded selection data; provider IO, subscription custody and access stay external."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator, Mapping
import hashlib
import json
import math
import re
from typing import Any

PROTOCOL = "agent-pull-requests.filters/v1"
FIELDS = frozenset({"repo", "from", "assigned", "age", "tags", "publication"})
MAX_BYTES = 32768
MAX_NODES = 128
MAX_DEPTH = 8
MAX_VALUES = 64
MAX_AGE = 100 * 366 * 86400
MATCH = "match"
NONMATCH = "nonmatch"
UNKNOWN = "unknown"


class FilterError(ValueError):
    """Malformed or unsupported intent cannot become a broader selection."""


def _unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise FilterError("duplicate filter field")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise FilterError("nonfinite filter value")


def _text(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise FilterError("invalid filter identity")
    return value


def _values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_VALUES:
        raise FilterError("filter values must be a nonempty bounded array")
    result = tuple(_text(item) for item in value)
    if len(set(result)) != len(result):
        raise FilterError("duplicate filter value")
    return tuple(sorted(result))


def _duration(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_AGE:
        raise FilterError("invalid filter duration")
    return value


def _canonical(value: object) -> str:
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(result.encode("utf-8")) > MAX_BYTES:
            raise FilterError("filter byte limit")
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise FilterError("invalid or oversized filter JSON") from exc


def _node(value: object, depth: int, count: list[int], fields: set[str]) -> dict[str, Any]:
    count[0] += 1
    if depth > MAX_DEPTH or count[0] > MAX_NODES:
        raise FilterError("filter complexity limit")
    if not isinstance(value, dict) or not value:
        raise FilterError("filter expression must be a nonempty object")
    combinators = value.keys() & {"all", "any"}
    if combinators:
        if len(value) != 1:
            raise FilterError("composition cannot mix with filter fields")
        name = next(iter(combinators))
        children = value[name]
        if not isinstance(children, list) or not 1 <= len(children) <= MAX_VALUES:
            raise FilterError("composition requires bounded nonempty children")
        normalized = [_node(child, depth + 1, count, fields) for child in children]
        return {name: sorted(normalized, key=_canonical)}
    if value.keys() - FIELDS:
        raise FilterError("unknown filter field")
    result: dict[str, Any] = {}
    for field, selected in value.items():
        fields.add(field)
        if field in {"repo", "publication"}:
            result[field] = list(_values(selected))
            if field == "publication" and set(result[field]) - {"draft", "published"}:
                raise FilterError("unsupported publication state")
        elif field in {"from", "assigned"}:
            if not isinstance(selected, dict) or not selected or selected.keys() - {"users", "groups"}:
                raise FilterError("identity filter requires explicit users or groups")
            result[field] = {key: list(_values(items)) for key, items in selected.items()}
        elif field == "tags":
            if not isinstance(selected, dict) or len(selected) != 1 or (
                set(selected) not in ({"any"}, {"all"})
            ):
                raise FilterError("tags require explicit any/all matching")
            result[field] = {key: list(_values(items)) for key, items in selected.items()}
        else:
            if not isinstance(selected, dict) or (
                not {"field"} < selected.keys()
                or selected.keys() - {"field", "min_seconds", "max_seconds"}
                or not isinstance(selected["field"], str)
                or selected["field"] not in {"created", "activity"}
            ):
                raise FilterError("age requires field and duration bounds")
            bounds = {key: _duration(item) for key, item in selected.items() if key != "field"}
            if bounds.get("min_seconds", 0) > bounds.get("max_seconds", MAX_AGE):
                raise FilterError("age bounds are reversed")
            result[field] = {"field": selected["field"], **bounds}
    return result


def _walk(node: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    if "all" in node or "any" in node:
        for child in next(iter(node.values())):
            yield from _walk(child)
    else:
        yield node


@dataclass(frozen=True)
class Expression:
    """Canonical JSON detaches caller-owned dictionaries from accepted immutable intent."""

    canonical: str
    fields: frozenset[str]

    @classmethod
    def parse(cls, value: object) -> Expression:
        _canonical(value)
        if not isinstance(value, dict) or set(value) != {"protocol", "where"} or (
            value["protocol"] != PROTOCOL
        ):
            raise FilterError("unsupported filter envelope")
        fields: set[str] = set()
        where = _node(value["where"], 0, [0], fields)
        return cls(_canonical({"protocol": PROTOCOL, "where": where}), frozenset(fields))

    @classmethod
    def loads(cls, raw: bytes | str) -> Expression:
        try:
            if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_BYTES:
                raise FilterError("filter byte limit")
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            return cls.parse(json.loads(text, object_pairs_hook=_unique, parse_constant=_constant))
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise FilterError("invalid bounded filter JSON") from exc

    def document(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.canonical)
        return value

    @property
    def revision(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()

    def require(self, capabilities: Mapping[str, Any], repositories: set[str]) -> None:
        if not isinstance(capabilities, Mapping) or capabilities.get("protocol") != PROTOCOL or any(
            not isinstance(capabilities.get(name, []), list)
            or any(not isinstance(item, str) for item in capabilities.get(name, []))
            for name in ("fields", "groups", "age_fields")
        ) or "fields" not in capabilities:
            raise FilterError("executing filter driver unsupported")
        if self.fields - set(capabilities["fields"]):
            raise FilterError("required filter field unsupported")
        if not repositories:
            raise FilterError("filter requires admitted repositories")
        for leaf in _walk(self.document()["where"]):
            if set(leaf.get("repo", [])) - repositories:
                raise FilterError("filter repository outside admitted scope")
            for name in ("from", "assigned"):
                if "groups" in leaf.get(name, {}) and name not in capabilities.get("groups", []):
                    raise FilterError("required group semantics unsupported")
            age = leaf.get("age")
            if age and age["field"] not in capabilities.get("age_fields", []):
                raise FilterError("required age semantics unsupported")

    def evaluate(
        self, facts: Mapping[str, Any], *, now: float,
        groups: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> str:
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise FilterError("invalid evaluation instant")
        return _evaluate(self.document()["where"], facts, now, groups or {})


def _combine(results: list[str], operator: str) -> str:
    decisive, neutral = (NONMATCH, MATCH) if operator == "all" else (MATCH, NONMATCH)
    return decisive if decisive in results else UNKNOWN if UNKNOWN in results else neutral


def _identity(
    selected: Mapping[str, Any], actual: object,
    groups: Mapping[str, Mapping[str, Any]],
) -> str:
    if not isinstance(actual, list) or any(not isinstance(item, str) for item in actual):
        return UNKNOWN
    users = set(selected.get("users", []))
    incomplete = False
    for group in selected.get("groups", []):
        evidence = groups.get(group)
        if not isinstance(evidence, Mapping) or not isinstance(evidence.get("members"), list) or any(
            not isinstance(item, str) for item in evidence["members"]
        ):
            incomplete = True
            continue
        users.update(evidence["members"])
        incomplete |= evidence.get("complete") is not True
    return MATCH if users.intersection(actual) else UNKNOWN if incomplete else NONMATCH


def _evaluate(
    node: Mapping[str, Any], facts: Mapping[str, Any], now: float,
    groups: Mapping[str, Mapping[str, Any]],
) -> str:
    for operator in ("all", "any"):
        if operator in node:
            return _combine([_evaluate(child, facts, now, groups) for child in node[operator]], operator)
    results = []
    for name, selected in node.items():
        value = facts.get(name)
        if name in {"from", "assigned"}:
            results.append(_identity(selected, value, groups))
        elif name == "age":
            instant = facts.get("created_at" if selected["field"] == "created" else "activity_at")
            if isinstance(instant, bool) or not isinstance(instant, (int, float)) or (
                not math.isfinite(instant) or instant > now
            ):
                results.append(UNKNOWN)
            else:
                age = now - instant
                results.append(MATCH if (
                    selected.get("min_seconds", 0) <= age <= selected.get("max_seconds", MAX_AGE)
                ) else NONMATCH)
        elif name == "tags":
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                results.append(UNKNOWN)
            else:
                operation, tags = next(iter(selected.items()))
                included = set(tags).issubset(value) if operation == "all" else bool(set(tags).intersection(value))
                results.append(MATCH if included else NONMATCH)
        else:
            results.append(UNKNOWN if not isinstance(value, str) else MATCH if value in selected else NONMATCH)
    return _combine(results, "all")


@dataclass(frozen=True)
class Definitions:
    """Inert named selections; resolution records content, not a mutable live reference."""

    entries: tuple[tuple[str, Expression], ...]

    @classmethod
    def parse(cls, value: object) -> Definitions:
        _canonical(value)
        if not isinstance(value, dict) or set(value) != {"protocol", "sets"} or (
            value["protocol"] != PROTOCOL or not isinstance(value["sets"], dict)
            or len(value["sets"]) > MAX_VALUES
        ):
            raise FilterError("invalid filter definitions")
        entries: list[tuple[str, Expression]] = []
        for name, expression in value["sets"].items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
                raise FilterError("invalid filter-set name")
            entries.append((name, Expression.parse(expression)))
        return cls(tuple(sorted(entries)))

    def resolve(self, name: str) -> Expression:
        try:
            return dict(self.entries)[name]
        except KeyError:
            raise FilterError("unknown named filter set") from None
