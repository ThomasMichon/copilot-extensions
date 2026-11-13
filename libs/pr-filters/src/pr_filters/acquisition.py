"""Pure executing-driver constraints shared by admission and native acquisition."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from . import Expression, FilterError, _walk

PROTOCOL = "agent-pull-requests.filter-acquisition/v1"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _queries(node: Mapping[str, Any], fields: Mapping[str, str]) -> set[tuple[str, str]]:
    if "any" in node:
        result: set[tuple[str, str]] = set()
        for child in node["any"]:
            branch = _queries(child, fields)
            if not branch:
                return set()
            result.update(branch)
        return result
    if "all" in node:
        choices = [_queries(child, fields) for child in node["all"]]
    else:
        choices = [{(key, value) for value in node.get(field, {}).get("users", [])}
                   for field, key in fields.items()]
    return min((choice for choice in choices if choice), key=lambda x: (len(x), sorted(x)),
               default=set())


def plan(expression: Expression, constraints: Mapping[str, Any]) -> list[tuple[str, str] | None]:
    """Validate all identities and the bounded query cover without provider IO."""
    if not isinstance(constraints, Mapping) or set(constraints) != {
        "protocol", "user_ids", "query_fields", "max_query_branches",
    } or constraints["protocol"] != PROTOCOL or constraints["user_ids"] != "uuid-lowercase":
        raise FilterError("executing acquisition constraints unsupported")
    fields = constraints["query_fields"]
    maximum = constraints["max_query_branches"]
    if not isinstance(fields, Mapping) or not fields or set(fields) - {"from", "assigned"} or any(
        not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", value) is None
        for value in fields.values()
    ) or type(maximum) is not int or not 1 <= maximum <= 64:
        raise FilterError("executing acquisition constraints invalid")
    where = expression.document()["where"]
    for leaf in _walk(where):
        for field in ("from", "assigned"):
            if any(_UUID.fullmatch(value) is None for value in leaf.get(field, {}).get("users", [])):
                raise FilterError("population requires canonical provider user IDs")
    queries = sorted(_queries(where, fields))
    if len(queries) > maximum:
        raise FilterError("population query branch limit")
    return list(queries) if queries else [None]
