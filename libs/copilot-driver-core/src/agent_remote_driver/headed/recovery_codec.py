"""Strict, complete recovery records for an original-owner store adapter."""

from __future__ import annotations

from dataclasses import asdict, fields
import json

from .recovery import (
    PERMIT_INTENT_CONTRACT, PERMIT_INTENT_VERSION, PermitRecoveryIntent,
    RecoveryDescriptor, RecoveryRecord, RecoveryStep, RecoveryStore,
)

CONTRACT = "copilot-driver.headed-recovery-record"
VERSION = 1
MAX_RECORD_BYTES = 262144


def _object(value: object, names: set[str]) -> dict:
    if type(value) is not dict or set(value) != names:
        raise ValueError("Recovery record fields must match the supported format")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ValueError("Recovery references must be arrays of strings")
    return tuple(value)


def _record(value: object) -> RecoveryRecord:
    record = _object(value, {"operation_id", "descriptor", "steps", "legacy_holds"})
    descriptor = _object(
        record["descriptor"], {field.name for field in fields(RecoveryDescriptor)},
    )
    if (
        any(type(item) is not str for key, item in descriptor.items() if key != "lease_revision")
        or type(descriptor["lease_revision"]) is not int
        or type(record["operation_id"]) is not str
        or type(record["steps"]) is not list or not 1 <= len(record["steps"]) <= 4
    ):
        raise ValueError("Recovery binding and history types must be exact")
    steps = []
    for item in record["steps"]:
        step = _object(item, {field.name for field in fields(RecoveryStep)})
        if (
            type(step["phase"]) is not str or type(step["evidence_ref"]) is not str
            or (
                step["authority_revision"] is not None
                and type(step["authority_revision"]) is not int
            )
            or (
                step["restored_incarnation"] is not None
                and type(step["restored_incarnation"]) is not str
            )
        ):
            raise ValueError("Recovery step types must be exact")
        steps.append(RecoveryStep(**{**step, "unknown_effects": _strings(step["unknown_effects"])}))
    return RecoveryRecord(
        record["operation_id"], RecoveryDescriptor(**descriptor), tuple(steps),
        _strings(record["legacy_holds"]),
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate recovery record fields are unsupported")
        value[key] = item
    return value


def _unsupported_number(_value: str) -> None:
    raise ValueError("Recovery records do not accept floating-point or non-finite numbers")


def decode_recovery_record(payload: bytes) -> RecoveryRecord:
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_RECORD_BYTES:
        raise ValueError("A bounded UTF-8 recovery record is required")
    try:
        value = _object(json.loads(
            payload.decode("utf-8"), object_pairs_hook=_pairs,
            parse_float=_unsupported_number, parse_constant=_unsupported_number,
        ), {"contract", "version", "record"})
        if (
            value["contract"] != CONTRACT
            or type(value["version"]) is not int or value["version"] != VERSION
        ):
            raise ValueError("Unsupported recovery record contract or version")
        return _record(value["record"])
    except (UnicodeError, RecursionError, TypeError) as error:
        raise ValueError("Invalid recovery record encoding") from error


def encode_recovery_record(record: RecoveryRecord) -> bytes:
    if type(record) is not RecoveryRecord:
        raise ValueError("A complete typed recovery record is required")
    value = {
        "operation_id": record.operation_id, "descriptor": asdict(record.descriptor),
        "steps": [
            {**asdict(step), "unknown_effects": list(step.unknown_effects)}
            for step in record.steps
        ],
        "legacy_holds": list(record.legacy_holds),
    }
    _record(value)
    try:
        payload = json.dumps(
            {"contract": CONTRACT, "version": VERSION, "record": value},
            ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (UnicodeError, TypeError) as error:
        raise ValueError("Invalid recovery record encoding") from error
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Recovery record exceeds the supported size")
    return payload


def _permit_intent(value: object) -> PermitRecoveryIntent:
    intent = dict(_object(value, {field.name for field in fields(PermitRecoveryIntent)}))
    descriptor = _object(
        intent["descriptor"], {field.name for field in fields(RecoveryDescriptor)},
    )
    if (
        any(type(item) is not str for key, item in descriptor.items() if key != "lease_revision")
        or type(descriptor["lease_revision"]) is not int
    ):
        raise ValueError("Recovery descriptor types must be exact")
    intent["descriptor"] = RecoveryDescriptor(**descriptor)
    return PermitRecoveryIntent(**intent)


def decode_permit_recovery_intent(payload: bytes) -> PermitRecoveryIntent:
    """Decode only the explicit data contract; never reinterpret a v1 record."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_RECORD_BYTES:
        raise ValueError("A bounded UTF-8 recovery intent is required")
    try:
        value = _object(json.loads(
            payload.decode("utf-8"), object_pairs_hook=_pairs,
            parse_float=_unsupported_number, parse_constant=_unsupported_number,
        ), {"contract", "version", "intent"})
        if (
            value["contract"] != PERMIT_INTENT_CONTRACT
            or type(value["version"]) is not int or value["version"] != PERMIT_INTENT_VERSION
        ):
            raise ValueError("Unsupported permit recovery intent contract or version")
        return _permit_intent(value["intent"])
    except (UnicodeError, RecursionError, TypeError) as error:
        raise ValueError("Invalid recovery intent encoding") from error


def encode_permit_recovery_intent(intent: PermitRecoveryIntent) -> bytes:
    if type(intent) is not PermitRecoveryIntent:
        raise ValueError("An explicit typed permit recovery intent is required")
    value = asdict(intent)
    _permit_intent(value)
    try:
        payload = json.dumps(
            {"contract": PERMIT_INTENT_CONTRACT, "version": PERMIT_INTENT_VERSION, "intent": value},
            ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (UnicodeError, TypeError) as error:
        raise ValueError("Invalid recovery intent encoding") from error
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Recovery intent exceeds the supported size")
    return payload


def load_recovery_status(store: RecoveryStore, descriptor: RecoveryDescriptor) -> dict:
    """Read only; no status result is an admission to execute recovery."""
    record = store.load(descriptor)
    if record is None:
        return {
            "descriptor": asdict(descriptor), "phase": "unknown", "revision": None,
            "native_recovery": "unqualified", "effects_wired": False,
            "reconciliation_required": True, "replay": "never",
        }
    if type(record) is not RecoveryRecord or record.descriptor != descriptor:
        raise ValueError("Owner store returned a different recovery binding")
    return decode_recovery_record(encode_recovery_record(record)).status()
