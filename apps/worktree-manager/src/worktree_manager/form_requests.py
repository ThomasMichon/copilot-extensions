"""Provider-declared preparation and local original-request recovery.

Local copies and command observations are recovery data, never acceptance
authority. Only a currently attributed provider can interpret its request.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
import string
import uuid

from .form_drafts import read_path
from .form_draft_storage import write_record


def validate_request_placement(data: Mapping) -> None:
    if data.get("placement") != "config":
        return
    for key in ("actions", "view_actions"):
        entries = data.get(key)
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
            if any(isinstance(item, Mapping) and item.get("request") is not None for item in entries):
                raise ValueError("request forms require an operational pivot; Configuration uses Apply/Cancel")


def parse_request(value: object) -> dict | None:
    if value is None:
        return None
    selectors = ("scope_from", "prepared_scope_from", "identity_from")
    if not isinstance(value, Mapping) or set(value) != {"prepare", "receipt", *selectors}:
        raise ValueError("request requires prepare, receipt and all scope/identity selectors")
    result = dict(value)
    for key in ("prepare", "receipt"):
        argv = value[key]
        if (not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)):
            raise ValueError(f"request.{key} must be a non-empty argv")
        result[key] = tuple(argv)
    for key in selectors:
        if (not isinstance(value[key], str)
                or not re.fullmatch(r"[^.\s{}\x00]+(?:\.[^.\s{}\x00]+)*", value[key])):
            raise ValueError(f"request.{key} must be a non-empty dotted path")
    return result


def json_data(value):
    return json.loads(json.dumps(value, allow_nan=False))


def request_contract(reg, action) -> dict:
    return json_data({
        "provider": [reg.source_path, reg.list_cmd],
        "source_owner": reg.source_owner, "scope": reg.scope, "pivot": reg.name,
        "action": [action.key, action.run, action.form, action.confirm, action.when],
    })


def freeze_context(templates, ctx) -> dict:
    """Retain referenced data only, not the whole board row or task results."""
    frozen = {}
    formatter = string.Formatter()
    for template in templates:
        for arg in template:
            for _literal, token, _format, _conversion in formatter.parse(arg):
                if not token or token == "fields" or token.startswith("field."):
                    continue
                if token.startswith("json."):
                    token = token[5:]
                if token == "prepared_request":
                    continue
                value = json_data(ctx[token] if token in ctx else read_path(ctx, token))
                target = frozen
                parts = token.split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
    return json_data(frozen)


def prepared_record(reg, action, ctx, values, binding, prepared) -> dict:
    spec = action.form["request"]
    scope = read_path(ctx, spec["scope_from"])
    if (not isinstance(prepared, dict) or not prepared
            or not isinstance(scope, (str, Mapping)) or not scope
            or read_path(prepared, spec["prepared_scope_from"]) != scope
            or read_path(prepared, spec["identity_from"]) != ctx["request_id"]):
        raise ValueError("Prepared request has missing or mismatched identity/scope")
    frozen = freeze_context((spec["prepare"], spec["receipt"], action.run), ctx)
    for key in ("request_id", "task_id", "machine"):
        frozen[key] = ctx.get(key, "")
    # Keep the complete original scope even if argv references only a child.
    target = frozen
    parts = spec["scope_from"].split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = scope
    return json_data({
        "schema": "worktree-manager.form-request", "version": 1,
        "contract": request_contract(reg, action), "binding": binding,
        "context": frozen, "values": values, "prepared_request": prepared,
        "consent": "original-confirm", "task_id": str(ctx.get("task_id") or ""),
        "machine": str(ctx.get("machine") or ""),
    })


def retain_request(directory: Path, record: dict) -> Path:
    path = directory / f"request-{uuid.uuid4().hex}.json"
    write_record(path, record)
    return path


def observe_request(path: Path, *, operation: str, ok: bool, output: str) -> None:
    write_record(
        path.with_name(f"{path.stem}.evidence-{uuid.uuid4().hex}.json"),
        {"operation": operation, "command_ok": ok, "output": output},
    )


def recovery_records(directory: Path) -> list[tuple[Path, dict, str]]:
    """Include rejected/legacy data visibly, without adopting or deleting it."""
    records = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        if ".evidence-" in path.name:
            continue
        raw = None
        try:
            raw = path.read_text(encoding="utf-8")
            record = json.loads(raw)
            valid = (
                isinstance(record, dict)
                and record.get("schema") == "worktree-manager.form-request"
                and type(record.get("version")) is int and record["version"] == 1
            )
            records.append((
                path, record if isinstance(record, dict) else {"previous_data": record},
                "" if valid else "Previous values; not a prepared request",
            ))
        except (OSError, ValueError) as exc:
            records.append((
                path, {"unparsed_text": raw} if raw is not None else {},
                f"Unreadable recovery: {type(exc).__name__}: {exc}",
            ))
    return records


def resolve_request(record: dict, registrations) -> tuple:
    """Stored commands are compared as data; only the current action is used."""
    if (record.get("schema") != "worktree-manager.form-request"
            or type(record.get("version")) is not int or record["version"] != 1
            or record.get("consent") != "original-confirm"
            or not isinstance(record.get("context"), dict)
            or not isinstance(record.get("values"), dict)):
        raise ValueError("Legacy or invalid recovery is read-only")
    matches = [
        (reg, action) for reg in registrations for action in (*reg.actions, *reg.view_actions)
        if action.form and action.form.get("request")
        and request_contract(reg, action) == record.get("contract")
    ]
    if len(matches) != 1:
        raise ValueError("Original attributed provider/action unavailable or changed; recovery is read-only")
    reg, action = matches[0]
    validated = prepared_record(
        reg, action, record["context"], record["values"],
        record.get("binding"), record.get("prepared_request"),
    )
    if validated != record:
        raise ValueError("Original request context is incomplete or inconsistent; recovery is read-only")
    return reg, action


def recovery_text(path: Path, record: dict, diagnostic: str = "") -> str:
    text = (
        "Local recovery data, not proof of acceptance. Check the original owner's receipt.\n"
        "Missing or failed receipt reads do not authorize a new send.\n\n"
        f"{diagnostic}\nPath: {path}\n\n"
        + json.dumps(record, ensure_ascii=False, indent=2)
    )
    for evidence in sorted(path.parent.glob(f"{path.stem}.evidence-*.json")):
        try:
            text += "\n\nCommand observation (not acceptance authority):\n" + evidence.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            text += f"\n\nCannot read observation {evidence.name}: {type(exc).__name__}: {exc}"
    return text
