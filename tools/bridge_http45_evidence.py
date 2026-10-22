"""Operation-family checks for HTTP-45 evidence; hashes alone do not prove shape."""
from __future__ import annotations

import copy
import json


KEYED = "ordinary-native-keyed-admission-v1"
CREATION = "ordinary-workspace-creation-cli-v1"


def advertised(value):
    if isinstance(value, dict):
        return {key: 45 if key == "protocol_version" and item == 44 else advertised(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [advertised(item) for item in value]
    return value


def body(value):
    result = copy.deepcopy(value)
    for key in ("captured_from", "reattested_from"):
        result.pop(key, None)
    return result


def mapping(value):
    return value if isinstance(value, dict) else {}


def validate_families(fixtures: list[dict], loaded: dict, errors: list[str]) -> None:
    """Require real predecessor links or explicitly shared new-operation evidence."""
    sources = {}
    for registered in fixtures:
        if (not isinstance(registered, dict) or registered.get("generation") != 45
                or not isinstance(registered.get("path"), str) or registered["path"] not in loaded):
            continue
        path = registered["path"]
        _, value = loaded[path]
        source = mapping(value.get("captured_from")).get("source_path")
        source = source if isinstance(source, str) else None
        sources.setdefault(source, []).append(value)
        previous = value.get("reattested_from")
        if previous is not None:
            if not isinstance(previous, dict):
                errors.append(f"{path}: invalid HTTP-45 predecessor shape")
                continue
            reference = previous.get("path")
            old = loaded.get(reference) if isinstance(reference, str) else None
            if (
                old is None or old[0] != previous.get("sha256")
                or mapping(old[1].get("captured_from")).get("source_path") != source
                or canonical(body(value)) != canonical(body(advertised(old[1])))
            ):
                errors.append(f"{path}: invalid HTTP-45 same-family predecessor")
            continue
        family = value.get("evidence_family")
        if family == KEYED:
            if not source or not any(source.endswith("/" + name) for name in (
                "native_keyed_admission.py", "native_keyed_client.py", "session_targeting_cli.py",
                "routes/prompts.py", "session_prompts.py", "db_schema.py", "db_events.py",
            )):
                errors.append(f"{path}: keyed operation family does not own this source")
            request = mapping(value.get("request"))
            receipt = mapping(value.get("admission_response"))
            if not (
                set(request) == {"version", "invocation_key", "expected_native_session_id", "prompt", "binding"}
                and type(request.get("version")) is int and request["version"] == 1
                and isinstance(request.get("binding"), dict)
                and request["binding"].get("origin_kind") in {"created", "resumed"}
                and type(request["binding"].get("expected_turn_index")) is int
                and type(receipt.get("turn_index")) is int
                and receipt["turn_index"] == request["binding"]["expected_turn_index"]
                and receipt.get("invocation_key") == request.get("invocation_key")
                and receipt.get("native_session_id") == request.get("expected_native_session_id")
                and receipt.get("queued") is False and receipt.get("admission") == "accepted"
                and isinstance(value.get("changed_prompt_refusal"), dict)
                and isinstance(value.get("missing_receipt_refusal"), dict)
            ):
                errors.append(f"{path}: malformed keyed-native operation family")
            shared = value.get("shared_operation_evidence")
            if shared is not None:
                if not isinstance(shared, dict):
                    errors.append(f"{path}: invalid shared keyed operation shape")
                    continue
                reference = shared.get("path")
                primary = loaded.get(reference) if isinstance(reference, str) else None
                if primary is None or primary[0] != shared.get("sha256"):
                    errors.append(f"{path}: missing shared keyed operation evidence")
                else:
                    actual, expected = body(value), body(primary[1])
                    actual.pop("shared_operation_evidence", None)
                    if canonical(actual) != canonical(expected):
                        errors.append(f"{path}: shared keyed operation vectors changed")
            elif not source or not source.endswith("/native_keyed_admission.py"):
                errors.append(f"{path}: new keyed consumer must reference shared operation evidence")
        elif family == CREATION:
            if not source or not source.endswith("/workspace_creation_cli.py"):
                errors.append(f"{path}: creation CLI operation family does not own this source")
            request = mapping(value.get("request"))
            complete = mapping(value.get("complete_receipt"))
            refusal = mapping(value.get("floor_refusal"))
            if not (
                isinstance(request.get("argv"), list) and "--creation-key" in request["argv"]
                and "--min-daemon-protocol" in request["argv"]
                and mapping(request.get("wire")).get("creation_key")
                and mapping(complete.get("creation")).get("status") == "complete"
                and complete.get("ready") is True
                and mapping(complete.get("connection")).get("native_session_id")
                and refusal.get("daemon_protocol") == 44 and refusal.get("status") == 426
                and refusal.get("creation_requests") == 0
            ):
                errors.append(f"{path}: malformed keyed-creation CLI operation family")
        else:
            errors.append(f"{path}: HTTP-45 fixture lacks a predecessor or named operation family")
    for suffix, predecessor_name in (
        ("/routes/prompts.py", "native-bound-extracted-routes-prompts-generation-44.json"),
        ("/session_prompts.py", "managed-drain-generation-44.json"),
    ):
        records = [value for source, values in sources.items() if source and source.endswith(suffix)
                   for value in values]
        if not any(str(mapping(value.get("reattested_from")).get("path", "")).endswith(predecessor_name)
                   for value in records):
            errors.append(f"HTTP-45 {suffix}: preserved unkeyed/native/drain family is missing")
        if not any(value.get("evidence_family") == KEYED for value in records):
            errors.append(f"HTTP-45 {suffix}: keyed operation family is missing")
    if not any(
        isinstance(row, dict) and row.get("generation") == 45
        and str(row.get("path", "")).endswith("/provider-creation-refresh-generation-45.json")
        for row in fixtures
    ):
        errors.append("HTTP-45: consumer-facing provider-refresh family alias is missing")


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)
