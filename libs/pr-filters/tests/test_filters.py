import json
from types import MappingProxyType
from typing import Any

import pytest

from pr_filters import (
    Definitions, Expression, FilterError, MATCH, NONMATCH, PROTOCOL, UNKNOWN,
)


def expression(where: object) -> Expression:
    return Expression.parse({"protocol": PROTOCOL, "where": where})


def support(**overrides: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "fields": ["repo", "from", "assigned", "age", "tags", "publication"],
        "groups": ["from", "assigned"], "age_fields": ["created", "activity"],
        **overrides,
    }


def test_six_fields_share_exact_conjunction_and_driver_identity() -> None:
    selected = expression({
        "repo": ["Project/repo"],
        "from": {"users": ["alice"], "groups": ["authors"]},
        "assigned": {"groups": ["reviewers"]},
        "age": {"field": "created", "min_seconds": 5, "max_seconds": 10},
        "tags": {"all": ["ready", "small"]},
        "publication": ["published"],
    })
    facts = {
        "repo": "Project/repo", "from": ["bob"], "assigned": ["carol"],
        "created_at": 95, "tags": ["small", "ready", "other"], "publication": "published",
    }
    groups = {
        "authors": {"members": ["bob"], "complete": True},
        "reviewers": {"members": ["carol"], "complete": True},
    }
    selected.require(support(), {"Project/repo"})
    assert selected.evaluate(facts, now=100, groups=groups) == MATCH
    assert selected.evaluate({**facts, "publication": "draft"}, now=100, groups=groups) == NONMATCH
    assert selected.evaluate({**facts, "assigned": None}, now=100, groups=groups) == UNKNOWN


@pytest.mark.parametrize(("operator", "missing", "expected"), [
    ("all", {"repo": "other"}, NONMATCH),
    ("all", {"repo": "Project/repo"}, UNKNOWN),
    ("any", {"repo": "other"}, UNKNOWN),
    ("any", {"repo": "Project/repo"}, MATCH),
])
def test_composition_preserves_unknown(operator: str, missing: dict[str, Any], expected: str) -> None:
    selected = expression({operator: [{"repo": ["Project/repo"]}, {"tags": {"any": ["ready"]}}]})
    assert selected.evaluate(missing, now=100) == expected


def test_partial_group_membership_never_proves_negative() -> None:
    selected = expression({"from": {"groups": ["team"]}})
    facts = {"from": ["alice"]}
    assert selected.evaluate(facts, now=10) == UNKNOWN
    assert selected.evaluate(facts, now=10, groups={"team": {"members": [], "complete": False}}) == UNKNOWN
    assert selected.evaluate(facts, now=10, groups={"team": {"members": [], "complete": True}}) == NONMATCH
    assert selected.evaluate(
        facts, now=10, groups={"team": {"members": ["alice"], "complete": False}},
    ) == MATCH
    assert selected.evaluate({"from": []}, now=10) == UNKNOWN


def test_read_only_driver_and_group_mappings_preserve_positive_evidence() -> None:
    selected = expression({"from": {"groups": ["team"]}})
    selected.require(MappingProxyType(support()), {"Project/repo"})
    assert selected.evaluate(
        MappingProxyType({"from": ["alice"]}), now=10,
        groups=MappingProxyType({"team": MappingProxyType({"members": ["alice"], "complete": True})}),
    ) == MATCH


def test_age_rechecks_clock_without_a_new_head_and_preserves_future_unknown() -> None:
    selected = expression({"age": {"field": "activity", "min_seconds": 5, "max_seconds": 10}})
    facts = {"activity_at": 100}
    assert selected.evaluate(facts, now=104) == NONMATCH
    assert selected.evaluate(facts, now=105) == MATCH
    assert selected.evaluate(facts, now=110) == MATCH
    assert selected.evaluate(facts, now=111) == NONMATCH
    assert selected.evaluate(facts, now=99) == UNKNOWN
    assert selected.evaluate({"activity_at": float("nan")}, now=110) == UNKNOWN
    with pytest.raises(FilterError):
        selected.evaluate(facts, now=float("inf"))


def test_identity_alternatives_and_explicit_tag_any() -> None:
    selected = expression({"from": {"users": ["alice", "bob"]}, "tags": {"any": ["x", "y"]}})
    assert selected.evaluate({"from": ["bob"], "tags": ["y"]}, now=0) == MATCH
    assert selected.evaluate({"from": ["Bob"], "tags": ["y"]}, now=0) == NONMATCH
    assert selected.evaluate({"from": ["bob"], "tags": []}, now=0) == NONMATCH


@pytest.mark.parametrize("where", [
    {}, {"repo": []}, {"repo": ["x", "x"]}, {"repo": "x"}, {"unknown": ["x"]},
    {"any": []}, {"all": [{"repo": ["x"]}], "publication": ["draft"]},
    {"from": {}}, {"from": {"aliases": ["alice"]}},
    {"assigned": {"users": [False]}}, {"tags": ["x"]}, {"tags": {"any": ["x"], "all": ["y"]}},
    {"publication": ["open"]}, {"age": {"field": "created"}},
    {"age": {"field": "updated", "max_seconds": 10}},
    {"age": {"field": "created", "max_seconds": True}},
    {"age": {"field": "created", "min_seconds": 10, "max_seconds": 2}},
])
def test_malformed_expressions_refuse(where: object) -> None:
    with pytest.raises(FilterError):
        expression(where)


def test_scope_and_driver_capabilities_are_enforced_in_every_any_branch() -> None:
    selected = expression({"any": [{"repo": ["allowed"]}, {"repo": ["foreign"]}]})
    with pytest.raises(FilterError, match="scope"):
        selected.require(support(), {"allowed"})
    with pytest.raises(FilterError, match="field unsupported"):
        expression({"tags": {"any": ["ready"]}}).require(support(fields=["repo"]), {"allowed"})
    with pytest.raises(FilterError, match="group semantics"):
        expression({"assigned": {"groups": ["team"]}}).require(support(groups=[]), {"allowed"})
    with pytest.raises(FilterError, match="age semantics"):
        expression({"age": {"field": "created", "max_seconds": 5}}).require(
            support(age_fields=["activity"]), {"allowed"},
        )
    for malformed in (None, {}, support(fields=[[]]), support(groups="assigned")):
        with pytest.raises(FilterError, match="unsupported"):
            expression({"repo": ["allowed"]}).require(malformed, {"allowed"})  # type: ignore[arg-type]


def test_canonical_intent_is_detached_and_definition_revision_is_content_bound() -> None:
    raw: dict[str, Any] = {"protocol": PROTOCOL, "where": {"from": {"users": ["bob", "alice"]}}}
    first = Expression.parse(raw)
    definitions = Definitions.parse({"protocol": PROTOCOL, "sets": {"team": raw}})
    raw["where"]["from"]["users"].append("mallory")
    assert definitions.resolve("team") == first
    assert first == expression({"from": {"users": ["alice", "bob"]}})
    assert first.revision != Expression.parse(raw).revision
    first.document()["where"].clear()
    assert first.document()["where"]
    with pytest.raises(FilterError, match="unknown named"):
        definitions.resolve("missing")


def test_json_and_complexity_bounds_refuse_before_evaluation() -> None:
    for raw in (
        '{"protocol":"' + PROTOCOL + '","where":{"repo":["x"],"repo":["y"]}}',
        '{"protocol":"' + PROTOCOL + '","where":{"age":{"field":"created","max_seconds":NaN}}}',
        b"\xff", b" " * 32769,
    ):
        with pytest.raises(FilterError):
            Expression.loads(raw)
    nested: dict[str, Any] = {"repo": ["x"]}
    for _ in range(10):
        nested = {"all": [nested]}
    with pytest.raises(FilterError, match="complexity"):
        expression(nested)
    with pytest.raises(FilterError, match="complexity"):
        expression({"all": [{"any": [{"repo": ["x"]}] * 64}] * 3})
    selected = expression({"repo": ["x"]})
    assert Expression.loads(json.dumps(selected.document())) == selected
    for encoding in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
        with pytest.raises(FilterError):
            Expression.loads(json.dumps(selected.document()).encode(encoding))
