"""Generic contributed draft commands and provenance-bound local recovery."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping


def parse_draft(value: object) -> dict | None:
    if value is None:
        return None
    selectors = (
        "scope_from", "revision_from", "version_from", "saved_version_from", "values_from",
    )
    if not isinstance(value, Mapping) or set(value) != {"save", "clear", *selectors}:
        raise ValueError("draft requires save, clear and all provenance/value selectors")
    result = dict(value)
    for key in ("save", "clear"):
        argv = value[key]
        if (not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)):
            raise ValueError(f"draft.{key} must be a non-empty argv")
        result[key] = tuple(argv)
    for key in selectors:
        if (not isinstance(value[key], str)
                or not re.fullmatch(r"[^.\s{}\x00]+(?:\.[^.\s{}\x00]+)*", value[key])):
            raise ValueError(f"draft.{key} must be a non-empty dotted path")
    return result


def read_path(value, path, default=None):
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return default
        value = value[part]
    return value


def update_draft_context(spec, ctx, result) -> None:
    for selector in ("revision_from", "values_from", "saved_version_from"):
        *parents, key = spec[selector].split(".")
        target = ctx
        for parent in parents:
            if not isinstance(target.get(parent), dict):
                target[parent] = {}
            target = target[parent]
        target[key] = read_path(result, spec[selector])


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def form_binding(reg, action, rec, ctx, fields, card) -> dict:
    draft = (action.form or {}).get("draft")
    binding = {
        "provider": [reg.source_path, reg.list_cmd],
        "machine": ctx.get("machine", ""),
        "task": str(ctx.get("task_id") or ""),
        "action": [action.key, action.run, draft],
        "fields": fields,
        "card": card,
    }
    if draft:
        for key in ("scope_from", "version_from", "revision_from"):
            value = read_path(rec, draft[key])
            valid = (
                isinstance(value, (str, Mapping)) and bool(value) if key == "scope_from"
                else isinstance(value, str) and bool(value)
            )
            if not valid:
                raise ValueError(f"Draft provenance unavailable: {draft[key]}; refresh or upgrade the provider")
            binding[key] = value
    return json.loads(json.dumps(binding))


def shared_values(spec, rec, fields=()) -> dict:
    if not spec or read_path(rec, spec["saved_version_from"]) != read_path(rec, spec["version_from"]):
        return {}
    values = read_path(rec, spec["values_from"])
    values = dict(values) if isinstance(values, Mapping) else {}
    for field in fields:
        name = field["name"]
        if field["type"] == "multichoice" and isinstance(values.get(name), str):
            try:
                decoded = json.loads(values[name])
            except ValueError:
                continue
            if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
                values[name] = decoded
    return values


def draft_ack(spec, ctx, message, *, clear: bool, expected_values=None) -> dict:
    result = json.loads(message)
    missing = object()
    revision = read_path(result, spec["revision_from"])
    version = read_path(result, spec["version_from"])
    values = read_path(result, spec["values_from"], missing)
    if (not isinstance(revision, str) or not revision
            or not isinstance(version, str) or not version
            or version != read_path(ctx, spec["version_from"])
            or read_path(result, spec["scope_from"]) != read_path(ctx, spec["scope_from"])):
        raise ValueError("Draft acknowledgement has missing or stale provenance")
    if clear:
        # A null parent is a valid explicit empty value, unlike an omitted parent.
        parent = spec["values_from"].rsplit(".", 1)[0]
        if values is not None and read_path(result, parent, missing) is not None:
            raise ValueError("Clear acknowledgement does not explicitly remove the draft")
        if (isinstance(read_path(ctx, spec["values_from"]), Mapping)
                and revision == read_path(ctx, spec["revision_from"])):
            raise ValueError("Clear acknowledgement retains the old draft revision")
    elif (revision == read_path(ctx, spec["revision_from"])
          or not isinstance(values, Mapping)
          or read_path(result, spec["saved_version_from"]) != version):
        raise ValueError("Save acknowledgement has no fresh bound draft")
    if not clear and expected_values is not None:
        decoded = dict(values)
        for key, expected in expected_values.items():
            if isinstance(expected, list) and isinstance(decoded.get(key), str):
                try:
                    decoded[key] = json.loads(decoded[key])
                except ValueError:
                    pass
        if decoded != expected_values:
            raise ValueError("Save acknowledgement does not contain the submitted answers")
    return result
