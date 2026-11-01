"""Scoped semantic comparison, not proof of relevance, permission or freshness."""

from __future__ import annotations

import json
import math
import unicodedata
from typing import Any

from agenda import AgendaError, _text
from agenda_operations import fields

KINDS = {"input", "condition", "constraint", "prerequisite-result", "observation", "insight"}
METADATA = {"id", "uuid", "timestamp", "session", "revision", "definition_revision",
            "operation_id", "created_at", "updated_at", "observed_at", "context_id",
            "attempt_id", "record_id", "result_id", "child_result_id", "metadata",
            "actor", "worktree", "committed_at", "entered_at", "occurrence_id", "generated_at",
            "definition_id", "board_revision", "request_id", "recorded_at",
            "evidence_ref", "evidence_refs"}


def semantic(value: Any, *, raw_input: bool = False) -> Any:
    if isinstance(value, str):
        return value if raw_input else " ".join(unicodedata.normalize("NFC", value).split())
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise AgendaError("semantic value keys must be text")
        return {key: semantic(item, raw_input=raw_input)
                for key, item in sorted(value.items()) if key not in METADATA}
    if isinstance(value, list):
        return [semantic(item, raw_input=raw_input) for item in value]
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise AgendaError("semantic value must be finite JSON")


def nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if value is None or value == "" or value == {} or value == []:
        return False
    if isinstance(value, (list, dict)):
        items = value.values() if isinstance(value, dict) else value
        return any(nonempty(item) for item in items)
    return True


def facts(value: Any, subjects: set[str], *,
          input_policy: dict[str, bool] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise AgendaError("context facts must be a nonempty list")
    normalized: dict[str, Any] = {}
    original: list[dict[str, Any]] = []
    for fact in value:
        fields(fact, {"kind", "subject", "value", "evidence_ref"})
        subject = _text(fact["subject"], "context subject")
        if subject not in subjects:
            raise AgendaError(f"context subject is outside relevant_subjects: {subject}")
        if subject in normalized:
            raise AgendaError("duplicate context subject")
        if not isinstance(fact["kind"], str) or fact["kind"] not in KINDS:
            raise AgendaError("unsupported context fact kind")
        _text(fact["evidence_ref"], "context evidence reference")
        policy = input_policy if input_policy is not None else {}
        content = semantic(fact["value"], raw_input=policy.get(subject, fact["kind"] == "input"))
        if not nonempty(content):
            raise AgendaError("context value is normalized-empty or metadata-only")
        # A later category-label change cannot alter the original comparison policy.
        normalized[subject] = {"value": content}
        original.append(fact)
    return original, normalized


def comparison(value: Any) -> tuple[Any, ...]:
    """Compare exact numbers without converting integers to lossy floats."""
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("boolean", value)
    if type(value) is int:
        return ("number", value, 1)
    if type(value) is float and math.isfinite(value):
        numerator, denominator = value.as_integer_ratio()
        return ("number", numerator, denominator)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(comparison(item) for item in value))
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ("object", tuple((key, comparison(item)) for key, item in sorted(value.items())))
    raise AgendaError("context comparison requires finite JSON")


def signature(values: dict[str, Any], subjects: set[str]) -> str:
    return json.dumps(comparison({key: item for key, item in values.items() if key in subjects}),
                      ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_equal(left: Any, right: Any) -> bool:
    """Numerically equivalent JSON values agree, but booleans stay distinct."""
    return comparison(left) == comparison(right)


def knowledge(value: Any, path: tuple[Any, ...] = ()) -> set[tuple[Any, ...]]:
    """Retain known scoped leaf values/structure without crediting bundle churn."""
    if not nonempty(value):
        return set()
    if isinstance(value, dict):
        result = {(path, "object")}
        for key, child in value.items():
            result.update(knowledge(child, path + (("key", key),)))
        return result
    if isinstance(value, list):
        result = {(path, "array")}
        for index, child in enumerate(value):
            result.update(knowledge(child, path + (("index", index),)))
        return result
    return {(path, "value", comparison(value))}


def qualification(
    proposed: dict[str, Any], current: dict[str, Any], used: list[dict[str, Any]],
    subjects: set[str], judgment: Any,
) -> tuple[bool, str, dict[str, list[str]]]:
    fields(judgment, {"qualification", "changed_subjects", "causal_change", "evidence_refs"})
    if judgment["qualification"] != "agent-judgment":
        raise AgendaError("causal relevance requires qualified agent judgment")
    _text(judgment["causal_change"], "causal change explanation")
    claimed = judgment["changed_subjects"]
    refs = judgment["evidence_refs"]
    if not isinstance(claimed, list) or not all(isinstance(item, str) for item in claimed):
        raise AgendaError("changed_subjects must be a list of subject names")
    if len(set(claimed)) != len(claimed) or not set(claimed) <= subjects:
        raise AgendaError("changed_subjects are duplicated or outside scope")
    if not isinstance(refs, list) or not refs:
        raise AgendaError("judgment requires evidence references")
    for ref in refs:
        _text(ref, "judgment evidence reference")
    old = {key: item for key, item in current.items() if key in subjects}
    added = sorted(proposed.keys() - old.keys())
    changed = sorted(key for key in proposed.keys() & old.keys() if not canonical_equal(proposed[key], old[key]))
    removed = sorted(old.keys() - proposed.keys())
    delta = {"added": added, "changed": changed, "removed": removed}
    if not added and not changed:
        return False, "duplicate-context" if not removed else "removed-facts-only", delta
    if set(claimed) != set(added + changed):
        raise AgendaError("judgment must explain exactly the changed or added semantic subjects")
    candidate = signature(proposed, subjects)
    if any(signature(previous, subjects) == candidate for previous in used):
        return False, "previously-used-context", delta
    for subject in added + changed:
        known: set[tuple[Any, ...]] = set()
        for previous in [current, *used]:
            if subject in previous:
                known.update(knowledge(previous[subject]))
        if knowledge(proposed[subject]) - known:
            return True, "qualified-agent-judgment", delta
    # Omission never erases learned facts. A different bundle of known values
    # needs a genuinely new scoped relationship/insight, not a new wrapper.
    return False, "previously-known-facts", delta


def context_policy(records: dict[str, Any], identity: str) -> dict[str, bool]:
    """Freeze each subject's input/prose treatment at its first lineage occurrence."""
    from agenda_v2_schema import record

    lineage: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = identity
    while current is not None:
        current = _text(current, "context lineage identity")
        if current in seen:
            raise AgendaError("context predecessor lineage is cyclic")
        seen.add(current)
        body = record(records, current, "context")
        fields(body, {"facts", "semantic", "parent_context_id", "judgment"}, {"delta"})
        if not isinstance(body.get("facts"), list):
            raise AgendaError("context predecessor has no valid fact list")
        lineage.append(body)
        current = body["parent_context_id"]
    policy: dict[str, bool] = {}
    for body in reversed(lineage):
        for fact in body["facts"]:
            fields(fact, {"kind", "subject", "value", "evidence_ref"})
            subject = _text(fact["subject"], "context subject")
            if not isinstance(fact["kind"], str) or fact["kind"] not in KINDS:
                raise AgendaError("unsupported context fact kind")
            policy.setdefault(subject, fact["kind"] == "input")
    return policy


def context_body(value: Any, subjects: set[str], *,
                 input_policy: dict[str, bool] | None = None) -> dict[str, Any]:
    fields(value, {"facts"})
    original, normalized = facts(value["facts"], subjects, input_policy=input_policy)
    return {"facts": original, "semantic": normalized}
