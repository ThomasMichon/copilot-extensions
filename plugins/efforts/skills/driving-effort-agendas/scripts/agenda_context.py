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


def semantic(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise AgendaError("semantic value keys must be text")
        return {key: semantic(item) for key, item in sorted(value.items()) if key not in METADATA}
    if isinstance(value, list):
        return [semantic(item) for item in value]
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise AgendaError("semantic value must be finite JSON")


def nonempty(value: Any) -> bool:
    if value is None or value == "" or value == {} or value == []:
        return False
    if isinstance(value, (list, dict)):
        items = value.values() if isinstance(value, dict) else value
        return any(nonempty(item) for item in items)
    return True


def facts(value: Any, subjects: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
        content = semantic(fact["value"])
        if not nonempty(content):
            raise AgendaError("context value is normalized-empty or metadata-only")
        # A category label is attribution, not a substantive change to the input.
        normalized[subject] = {"value": content}
        original.append(fact)
    return original, normalized


def signature(values: dict[str, Any], subjects: set[str]) -> str:
    return json.dumps({key: item for key, item in values.items() if key in subjects},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


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
    changed = sorted(key for key in proposed.keys() & old.keys() if proposed[key] != old[key])
    removed = sorted(old.keys() - proposed.keys())
    delta = {"added": added, "changed": changed, "removed": removed}
    if not added and not changed:
        return False, "duplicate-context" if not removed else "removed-facts-only", delta
    if set(claimed) != set(added + changed):
        raise AgendaError("judgment must explain exactly the changed or added semantic subjects")
    candidate = signature(proposed, subjects)
    if any(signature(previous, subjects) == candidate for previous in used):
        return False, "previously-used-context", delta
    return True, "qualified-agent-judgment", delta


def context_body(value: Any, subjects: set[str]) -> dict[str, Any]:
    fields(value, {"facts"})
    original, normalized = facts(value["facts"], subjects)
    return {"facts": original, "semantic": normalized}
