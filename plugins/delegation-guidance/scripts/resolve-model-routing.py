#!/usr/bin/env python3
"""Resolve inert repository and operator model-routing configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA = "copilot-extensions.model-routing"
VERSION = 1
MAX_CONFIG_BYTES = 64 * 1024
PURPOSE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
STATES = {"demonstrated", "candidate", "held", "failed"}
CONTEXT_TIERS = {"default", "long_context"}
REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max"}
TOP_KEYS = {"schema", "version", "purposes"}
ENTRY_KEYS = {
    "model",
    "state",
    "surfaces",
    "contextTiers",
    "reasoningEfforts",
    "costRank",
    "constraints",
    "fallbackOrder",
    "escalationConditions",
    "evidence",
    "recheckAfter",
}
EVIDENCE_KEYS = {"ref", "observedAt", "sampleCount", "notes"}


class ConfigError(ValueError):
    """Routing configuration is malformed or unsupported."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve model-routing config as deterministic JSON.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository path; defaults to the current directory.",
    )
    return parser


def _repo_root(path: Path) -> Path:
    current = path.expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _decode_json(content: str) -> object:
    try:
        return json.loads(content, object_pairs_hook=_object)
    except json.JSONDecodeError as exc:
        raise ConfigError("configuration is not valid JSON") from exc


def _jsonc(path: Path) -> dict[str, Any] | None:
    try:
        body = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("//")
        )
        value = _decode_json(body)
    except (OSError, UnicodeDecodeError, ConfigError):
        return None
    return value if isinstance(value, dict) else None


def _repo_is_trusted(repo: Path, home: Path) -> bool:
    config = _jsonc(home / ".copilot" / "config.json")
    folders = config.get("trustedFolders") if config else None
    if not isinstance(folders, list):
        return False
    resolved = repo.resolve()
    for raw in folders:
        if not isinstance(raw, str):
            continue
        try:
            if Path(raw).expanduser().resolve(strict=True) == resolved:
                return True
        except OSError:
            continue
    return False


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be a non-empty string")
    return value


def _string_list(
    value: object,
    *,
    field: str,
    allowed: set[str] | None = None,
    pattern: re.Pattern[str] | None = None,
    required: bool = False,
) -> list[str]:
    if value is None:
        qualifier = "a non-empty" if required else "an"
        raise ConfigError(f"{field} must be {qualifier} array")
    if not isinstance(value, list) or (required and not value):
        qualifier = "a non-empty" if required else "an"
        raise ConfigError(f"{field} must be {qualifier} array")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _string(item, field=f"{field}[{index}]")
        if allowed is not None and text not in allowed:
            raise ConfigError(f"{field}[{index}] has an unsupported value")
        if pattern is not None and pattern.fullmatch(text) is None:
            raise ConfigError(f"{field}[{index}] has an invalid format")
        if text in result:
            raise ConfigError(f"{field} contains a duplicate value")
        result.append(text)
    return result


def _date(value: object, *, field: str) -> str:
    text = _string(value, field=field)
    if DATE_PATTERN.fullmatch(text) is None:
        raise ConfigError(f"{field} must be an ISO date")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ConfigError(f"{field} must be an ISO date") from exc
    return text


def _evidence(value: object, *, field: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be an array")
    result: list[dict[str, object]] = []
    for index, item in enumerate(value):
        prefix = f"{field}[{index}]"
        if not isinstance(item, dict) or set(item) - EVIDENCE_KEYS:
            raise ConfigError(f"{prefix} contains unsupported fields")
        sample_count = item.get("sampleCount")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            raise ConfigError(f"{prefix}.sampleCount must be a positive integer")
        if sample_count < 1:
            raise ConfigError(f"{prefix}.sampleCount must be a positive integer")
        normalized: dict[str, object] = {
            "ref": _string(item.get("ref"), field=f"{prefix}.ref"),
            "observedAt": _date(
                item.get("observedAt"),
                field=f"{prefix}.observedAt",
            ),
            "sampleCount": sample_count,
        }
        if "notes" in item:
            notes = item["notes"]
            if not isinstance(notes, str):
                raise ConfigError(f"{prefix}.notes must be a string")
            normalized["notes"] = notes
        result.append(normalized)
    return result


def _entry(
    value: object,
    *,
    field: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) - ENTRY_KEYS:
        raise ConfigError(f"{field} contains unsupported fields")
    model = _string(value.get("model"), field=f"{field}.model")
    state = _string(value.get("state"), field=f"{field}.state")
    if state not in STATES:
        raise ConfigError(f"{field}.state has an unsupported value")
    evidence = (
        _evidence(value["evidence"], field=f"{field}.evidence")
        if "evidence" in value
        else []
    )
    if state == "demonstrated" and not evidence:
        raise ConfigError(f"{field}.evidence is required for demonstrated models")
    normalized: dict[str, object] = {
        "model": model,
        "state": state,
        "surfaces": _string_list(
            value.get("surfaces"),
            field=f"{field}.surfaces",
            pattern=PURPOSE_PATTERN,
            required=True,
        ),
    }
    optional_lists = {
        "contextTiers": CONTEXT_TIERS,
        "reasoningEfforts": REASONING_EFFORTS,
        "constraints": None,
        "fallbackOrder": None,
        "escalationConditions": None,
    }
    for key, allowed in optional_lists.items():
        if key in value:
            normalized[key] = _string_list(
                value[key],
                field=f"{field}.{key}",
                allowed=allowed,
            )
    if "costRank" in value:
        cost_rank = value["costRank"]
        if isinstance(cost_rank, bool) or not isinstance(cost_rank, int):
            raise ConfigError(f"{field}.costRank must be a non-negative integer")
        if cost_rank < 0:
            raise ConfigError(f"{field}.costRank must be a non-negative integer")
        normalized["costRank"] = cost_rank
    if evidence or "evidence" in value:
        normalized["evidence"] = evidence
    if "recheckAfter" in value:
        normalized["recheckAfter"] = _date(
            value["recheckAfter"],
            field=f"{field}.recheckAfter",
        )
    return normalized


def _load(path: Path) -> dict[str, list[dict[str, object]]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ConfigError("configuration is unreadable") from exc
    if len(payload) > MAX_CONFIG_BYTES:
        raise ConfigError("configuration exceeds the size limit")
    try:
        value = _decode_json(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError("configuration is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != TOP_KEYS:
        raise ConfigError("configuration must contain only schema, version, purposes")
    if value.get("schema") != SCHEMA or value.get("version") != VERSION:
        raise ConfigError("configuration has an unsupported schema or version")
    purposes = value.get("purposes")
    if not isinstance(purposes, dict):
        raise ConfigError("purposes must be an object")
    normalized: dict[str, list[dict[str, object]]] = {}
    for purpose, entries in purposes.items():
        if not isinstance(purpose, str) or PURPOSE_PATTERN.fullmatch(purpose) is None:
            raise ConfigError("purpose names must be lowercase kebab-case")
        if not isinstance(entries, list):
            raise ConfigError(f"purposes.{purpose} must be an array")
        seen: set[str] = set()
        normalized_entries: list[dict[str, object]] = []
        for index, raw in enumerate(entries):
            entry = _entry(raw, field=f"purposes.{purpose}[{index}]")
            model = str(entry["model"])
            if model in seen:
                raise ConfigError(f"purposes.{purpose} repeats model {model}")
            seen.add(model)
            normalized_entries.append(entry)
        normalized[purpose] = normalized_entries
    return normalized


def _merge(
    repository: dict[str, list[dict[str, object]]],
    operator: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    merged: dict[str, dict[str, dict[str, object]]] = {}
    for layer in (repository, operator):
        for purpose, entries in layer.items():
            target = merged.setdefault(purpose, {})
            for entry in entries:
                target[str(entry["model"])] = entry
    result: dict[str, list[dict[str, object]]] = {}
    for purpose in sorted(merged):
        result[purpose] = sorted(
            merged[purpose].values(),
            key=lambda entry: (
                int(entry.get("costRank", sys.maxsize)),
                str(entry["model"]),
            ),
        )
    return result


def _layer(
    path: Path,
    *,
    kind: str,
    allowed: bool,
    diagnostics: list[str],
) -> tuple[str, dict[str, list[dict[str, object]]]]:
    if not path.exists():
        return "missing", {}
    if not allowed:
        diagnostics.append(f"{kind} config ignored because the repository is untrusted")
        return "untrusted", {}
    try:
        return "loaded", _load(path)
    except ConfigError as exc:
        diagnostics.append(f"{kind} config ignored: {exc}")
        return "invalid", {}


def main() -> int:
    args = _parser().parse_args()
    repo = _repo_root(args.repo)
    home = Path.home().resolve()
    diagnostics: list[str] = []
    repo_status, repository = _layer(
        repo / ".github" / "copilot" / "model-routing.json",
        kind="repository",
        allowed=_repo_is_trusted(repo, home),
        diagnostics=diagnostics,
    )
    operator_status, operator = _layer(
        home / ".copilot" / "model-routing.json",
        kind="operator",
        allowed=True,
        diagnostics=diagnostics,
    )
    if operator_status == "invalid" and repository:
        diagnostics.append(
            "repository config suppressed because operator config is invalid"
        )
        repository = {}
    result = {
        "schema": SCHEMA,
        "version": VERSION,
        "sources": {
            "repository": repo_status,
            "operator": operator_status,
        },
        "purposes": _merge(repository, operator),
        "diagnostics": diagnostics,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    for diagnostic in diagnostics:
        print(f"[delegation-guidance] {diagnostic}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
