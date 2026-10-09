"""Execution-side preference receipts, independent of ACP and transport code."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SOURCE_ENV = "AGENT_BRIDGE_PREFERENCE_SOURCE"
CONTEXT_ENV = "AGENT_BRIDGE_PREFERENCE_CONTEXT"
SOURCES = ("caller-settings", "target-settings")
KEYS = {"model": "model", "effortLevel": "reasoning_effort", "contextTier": "context"}
FLAGS = {"--model": "model", "--reasoning-effort": "reasoning_effort", "--context": "context"}
RECEIPT_VERSION = 1
MAX_RECEIPT_BYTES = 4096


class PreferenceApplicationError(RuntimeError):
    """Target-mode required model/effort could not be verified."""


def clean(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def strip_comments(raw: str) -> str:
    out: list[str] = []
    for line in raw.splitlines():
        quoted = escaped = False
        for index, char in enumerate(line):
            if escaped:
                escaped = False
            elif quoted and char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted and line[index:index + 2] == "//":
                line = line[:index]
                break
        out.append(line)
    return "\n".join(out)


def launch_preferences(argv: list[str], env: dict[str, str]) -> dict[str, str]:
    """Only explicit launch arguments/provider settings, never caller defaults."""
    values: dict[str, str] = {}
    if value := clean(env.get("COPILOT_MODEL")):
        values["model"] = value
    for index, arg in enumerate(argv):
        flag, separator, value = arg.partition("=")
        key = FLAGS.get(flag)
        if key:
            if not separator and index + 1 < len(argv):
                value = argv[index + 1]
            if value := clean(value):
                values[key] = value
    return values


def execution_receipt(
    argv: list[str], env: dict[str, str] | None, child_pid: int,
) -> dict[str, Any]:
    """Configured executable names do not establish execution authority."""
    return {"version": RECEIPT_VERSION, "child_pid": child_pid,
            "status": "unsupported", "reason": "unverified-executable-provenance",
            "values": {}, "sources": {}}


def execution_settings(
    argv: list[str], env: dict[str, str] | None, child_pid: int,
) -> dict[str, Any]:
    """Read local candidate settings; not an authenticated authority receipt.

    Only a target-local attestor may seal this data after establishing its final
    execution context. Session Hosts must not infer that context from argv.
    """
    effective_env = {**os.environ, **(env or {})}
    if any(key in (env or {}) and env[key] != os.environ.get(key)
           for key in ("HOME", "USERPROFILE")):
        return {"version": RECEIPT_VERSION, "child_pid": child_pid,
                "status": "unsupported", "reason": "execution-home-overridden",
                "values": {}, "sources": {}}
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    status = "resolved"
    try:
        data = json.loads(strip_comments(
            (Path.home() / ".copilot" / "settings.json").read_text(encoding="utf-8")
        ))
        if not isinstance(data, dict):
            raise ValueError("settings must be an object")
        for setting, key in KEYS.items():
            if value := clean(data.get(setting)):
                values[key] = value
                sources[key] = "target-settings"
    except FileNotFoundError:
        status = "missing"
    except (OSError, ValueError, UnicodeError):
        status = "error"
    backend = launch_preferences(argv, effective_env)
    provider_selected = bool(clean(effective_env.get("COPILOT_PROVIDER_BASE_URL"))) or (
        (clean(effective_env.get("COPILOT_OFFLINE")) or "").lower() in {"1", "true", "yes", "on"}
    )
    if provider_selected:
        # An intentionally selected provider's native model is authoritative
        # when it has no explicit COPILOT_MODEL/--model selection.
        values.pop("model", None)
        sources.pop("model", None)
    for key, value in backend.items():
        values[key] = value
        sources[key] = "launch-profile"
    receipt = {"version": RECEIPT_VERSION, "child_pid": child_pid,
               "status": status, "values": values, "sources": sources}
    if provider_selected:
        receipt["provider_selected"] = True
    if len(json.dumps(receipt).encode("utf-8")) > MAX_RECEIPT_BYTES:
        return {"version": RECEIPT_VERSION, "child_pid": child_pid,
                "status": "error", "reason": "receipt-too-large", "values": {}, "sources": {}}
    return receipt


def validate_receipt(receipt: Any, child_pid: int) -> dict[str, Any] | None:
    if not isinstance(receipt, dict):
        return None
    if receipt.get("version") != RECEIPT_VERSION or receipt.get("child_pid") != child_pid:
        return None
    if receipt.get("status") not in {"resolved", "missing", "unsupported", "error"}:
        return None
    if receipt["status"] != "unsupported":
        return None
    if "provider_selected" in receipt and not isinstance(receipt["provider_selected"], bool):
        return None
    values, sources = receipt.get("values"), receipt.get("sources")
    if not isinstance(values, dict) or not isinstance(sources, dict):
        return None
    if values or sources or receipt.get("provider_selected"):
        return None
    if any(key not in KEYS.values() or not clean(value) for key, value in values.items()):
        return None
    if any(sources.get(key) not in {"target-settings", "launch-profile"} for key in values):
        return None
    if len(json.dumps(receipt).encode("utf-8")) > MAX_RECEIPT_BYTES:
        return None
    return receipt


def client_preferences(target: Any, db: Any = None, session_id: str = "") -> dict[str, Any]:
    """Persist policy in request-owned target env, including provider refresh."""
    env = getattr(target, "env", {}) or {}
    source = env.get(SOURCE_ENV, "caller-settings")
    if source not in SOURCES:
        raise ValueError("unsupported preference_source")
    options: dict[str, Any] = {"preference_source": source}
    if source == "target-settings":
        options["context_override"] = clean(env.get(CONTEXT_ENV))
        options["launch_preferences"] = launch_preferences(
            getattr(target, "copilot_args", []) or [], env,
        )
        if db is not None and session_id:
            db.flush()
            rows = db.execute_read(
                "SELECT data_json FROM events WHERE session_id=? "
                "AND event_type='preference_selected' ORDER BY event_id DESC LIMIT 1",
                (session_id,),
            )
            if rows:
                options["confirmed_preferences"] = json.loads(rows[0]["data_json"])
    return options
