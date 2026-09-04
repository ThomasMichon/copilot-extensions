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
    parser.add_argument(
        "--purpose",
        help="Select a model for this configured purpose.",
    )
    parser.add_argument(
        "--surface",
        help="Required execution surface for model selection.",
    )
    parser.add_argument(
        "--available-model",
        action="append",
        default=[],
        help="Available model identifier; repeat for each available model.",
    )
    parser.add_argument(
        "--context-tier",
        choices=sorted(CONTEXT_TIERS),
        help="Required context tier.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=sorted(REASONING_EFFORTS),
        help="Required reasoning effort.",
    )
    parser.add_argument(
        "--constraint",
        action="append",
        default=[],
        help="Satisfied configured constraint; repeat as needed.",
    )
    parser.add_argument(
        "--trial-model",
        help="Explicit candidate model requested for a trial.",
    )
    parser.add_argument(
        "--trial-id",
        help="Non-empty trial identity required with --trial-model.",
    )
    parser.add_argument(
        "--as-of",
        help="ISO date used for evidence-expiry checks; defaults to today.",
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


def _selection_date(raw: str | None) -> date:
    if raw is None:
        return date.today()
    if DATE_PATTERN.fullmatch(raw) is None:
        raise ConfigError("--as-of must be an ISO date")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ConfigError("--as-of must be an ISO date") from exc


def _entry_reasons(
    entry: dict[str, object],
    *,
    available: set[str],
    surface: str,
    context_tier: str | None,
    reasoning_effort: str | None,
    constraints: set[str],
    as_of: date,
) -> list[str]:
    reasons: list[str] = []
    model = str(entry["model"])
    state = str(entry["state"])
    if model not in available:
        reasons.append("unavailable")
    if surface not in entry["surfaces"]:
        reasons.append("surface-mismatch")
    tiers = entry.get("contextTiers", [])
    if tiers and context_tier not in tiers:
        reasons.append("context-tier-mismatch")
    efforts = entry.get("reasoningEfforts", [])
    if efforts and reasoning_effort not in efforts:
        reasons.append("reasoning-effort-mismatch")
    missing_constraints = sorted(set(entry.get("constraints", [])) - constraints)
    if missing_constraints:
        reasons.append("unsatisfied-constraints:" + ",".join(missing_constraints))
    recheck_after = entry.get("recheckAfter")
    if isinstance(recheck_after, str) and date.fromisoformat(recheck_after) < as_of:
        reasons.append("evidence-expired")
    if state in {"held", "failed"}:
        reasons.append(f"state-{state}")
    return reasons


def _cost_key(entry: dict[str, object]) -> tuple[int, str]:
    return (
        int(entry.get("costRank", sys.maxsize)),
        str(entry["model"]),
    )


def _fallbacks(
    selected: dict[str, object],
    demonstrated: list[dict[str, object]],
) -> list[str]:
    by_model = {str(entry["model"]): entry for entry in demonstrated}
    result: list[str] = []
    for model in selected.get("fallbackOrder", []):
        if model in by_model and model != selected["model"] and model not in result:
            result.append(str(model))
    for entry in sorted(demonstrated, key=_cost_key):
        model = str(entry["model"])
        if model != selected["model"] and model not in result:
            result.append(model)
    return result


def _decision(
    purposes: dict[str, list[dict[str, object]]],
    *,
    purpose: str,
    surface: str,
    available_models: list[str],
    context_tier: str | None,
    reasoning_effort: str | None,
    constraints: list[str],
    trial_model: str | None,
    trial_id: str | None,
    as_of: date,
) -> dict[str, object]:
    available = set(available_models)
    satisfied = set(constraints)
    considered: list[dict[str, object]] = []
    eligible_demonstrated: list[dict[str, object]] = []
    eligible_candidates: dict[str, dict[str, object]] = {}
    for entry in purposes.get(purpose, []):
        reasons = _entry_reasons(
            entry,
            available=available,
            surface=surface,
            context_tier=context_tier,
            reasoning_effort=reasoning_effort,
            constraints=satisfied,
            as_of=as_of,
        )
        state = str(entry["state"])
        considered.append({
            "model": entry["model"],
            "state": state,
            "eligible": not reasons,
            "reasons": reasons,
        })
        if reasons:
            continue
        if state == "demonstrated":
            eligible_demonstrated.append(entry)
        elif state == "candidate":
            eligible_candidates[str(entry["model"])] = entry

    selected: dict[str, object] | None = None
    mode = "none"
    reason = "no eligible demonstrated model"
    if trial_model is not None:
        if not trial_id:
            reason = "explicit candidate trial requires --trial-id"
        elif trial_model not in eligible_candidates:
            reason = "requested candidate is not eligible for this assignment"
        else:
            selected = eligible_candidates[trial_model]
            mode = "trial"
            reason = "explicit eligible candidate trial"
    elif eligible_demonstrated:
        selected = min(eligible_demonstrated, key=_cost_key)
        mode = "ordinary"
        reason = "lowest-cost demonstrated eligible model"

    result: dict[str, object] = {
        "status": "selected" if selected is not None else "no-eligible-model",
        "mode": mode,
        "purpose": purpose,
        "surface": surface,
        "asOf": as_of.isoformat(),
        "reason": reason,
        "considered": considered,
        "fallbacks": [],
    }
    if selected is not None:
        result["model"] = selected["model"]
        result["state"] = selected["state"]
        result["fallbacks"] = _fallbacks(selected, eligible_demonstrated)
        if mode == "trial":
            result["trialId"] = trial_id
    return result


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
    selection_requested = any(
        value is not None
        for value in (
            args.purpose,
            args.surface,
            args.context_tier,
            args.reasoning_effort,
            args.trial_model,
            args.trial_id,
            args.as_of,
        )
    ) or bool(args.available_model or args.constraint)
    if selection_requested:
        if not args.purpose or not PURPOSE_PATTERN.fullmatch(args.purpose):
            raise SystemExit("--purpose must be lowercase kebab-case for selection")
        if not args.surface or not PURPOSE_PATTERN.fullmatch(args.surface):
            raise SystemExit("--surface must be lowercase kebab-case for selection")
        if not args.available_model:
            raise SystemExit("at least one --available-model is required for selection")
        if args.trial_id and not args.trial_model:
            raise SystemExit("--trial-id requires --trial-model")
        if args.trial_model and not (args.trial_id and args.trial_id.strip()):
            raise SystemExit("--trial-model requires a non-empty --trial-id")
        trial_id = args.trial_id.strip() if args.trial_id else None
        try:
            as_of = _selection_date(args.as_of)
        except ConfigError as exc:
            raise SystemExit(str(exc)) from exc
        result["decision"] = _decision(
            result["purposes"],
            purpose=args.purpose,
            surface=args.surface,
            available_models=args.available_model,
            context_tier=args.context_tier,
            reasoning_effort=args.reasoning_effort,
            constraints=args.constraint,
            trial_model=args.trial_model,
            trial_id=trial_id,
            as_of=as_of,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    for diagnostic in diagnostics:
        print(f"[delegation-guidance] {diagnostic}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
