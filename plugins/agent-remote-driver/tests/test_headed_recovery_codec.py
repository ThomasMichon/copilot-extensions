"""Complete owner-store record serialization, without native actors or writes."""

from dataclasses import replace
import json

import pytest

from agent_remote_driver.headed.recovery import (
    PermitRecoveryIntent, RecoveryDescriptor, RecoveryRecord, RecoveryStep,
)
from agent_remote_driver.headed.recovery_codec import (
    MAX_RECORD_BYTES, decode_permit_recovery_intent, decode_recovery_record,
    encode_permit_recovery_intent, encode_recovery_record, load_recovery_status,
)


@pytest.fixture
def record():
    return RecoveryRecord(
        "original-operation",
        RecoveryDescriptor(
            "owner", "worktree", "descriptor-revision", "source-owner", "source-revision",
            "same-conversation", "environment", "boot", "scope", "incarnation-1", 1,
        ),
        (
            RecoveryStep("retire_intent", "original-admission", ("action-outcome-unknown",)),
            RecoveryStep("settled", "independent-provider-settlement"),
            RecoveryStep("resume_intent", "current-source", ("resume-ack-lost",),
                         authority_revision=2),
            RecoveryStep("observation_restored", "same-conversation-observed",
                         restored_incarnation="incarnation-2"),
        ),
        ("native-connection-lost",),
    )


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_complete_phase_history_roundtrips_without_loss_or_synthesized_authority(record, count):
    record = replace(record, steps=record.steps[:count])
    encoded = encode_recovery_record(record)
    restored = decode_recovery_record(encoded)
    assert restored == record
    assert encode_recovery_record(restored) == encoded
    assert restored.descriptor.session_id == "same-conversation"
    assert restored.legacy_holds == ("native-connection-lost",)
    assert restored.unknown_effects == record.unknown_effects
    assert "effects_wired" not in json.loads(encoded)["record"]
    if count == 4:
        assert restored.unknown_effects == ("action-outcome-unknown", "resume-ack-lost")


def test_store_status_is_read_only_and_preserves_uncertainty(record):
    class ReadOnlyStore:
        def __init__(self):
            self.loads = []
            self.current = record

        def load(self, descriptor):
            self.loads.append(descriptor)
            return self.current

        def compare_and_swap(self, *_):
            raise AssertionError("A status read must never write")

    store = ReadOnlyStore()
    status = load_recovery_status(store, record.descriptor)
    assert status["phase"] == "observation_restored"
    assert status["unknown_effects"] == ["action-outcome-unknown", "resume-ack-lost"]
    assert status["legacy_holds"] == ["native-connection-lost"]
    assert status["native_recovery"] == "unqualified"
    assert not status["effects_wired"] and status["reconciliation_required"]
    store.current = None
    assert load_recovery_status(store, record.descriptor)["phase"] == "unknown"
    store.current = replace(record, descriptor=replace(record.descriptor, boot_ref="other-boot"))
    with pytest.raises(ValueError):
        load_recovery_status(store, record.descriptor)
    assert store.loads == [record.descriptor] * 3


@pytest.mark.parametrize("path,value", [
    (("version",), True), (("version",), 2), (("version",), "1"),
    (("contract",), "other-contract"),
    (("record", "operation_id"), 7),
    (("record", "descriptor", "lease_revision"), True),
    (("record", "descriptor", "lease_revision"), 1.0),
    (("record", "descriptor", "owner_ref"), None),
    (("record", "descriptor", "session_id"), ""),
    (("record", "legacy_holds"), "native-connection-lost"),
    (("record", "legacy_holds"), ["hold", "hold"]),
    (("record", "legacy_holds"), ["hold"] * 65),
    (("record", "steps"), []),
    (("record", "steps", 0, "phase"), "settled"),
    (("record", "steps", 0, "unknown_effects"), [None]),
    (("record", "steps", 2, "authority_revision"), True),
    (("record", "steps", 2, "authority_revision"), 1),
    (("record", "steps", 3, "restored_incarnation"), "incarnation-1"),
])
def test_invalid_types_bindings_and_history_refuse_without_repair(record, path, value):
    envelope = json.loads(encode_recovery_record(record))
    target = envelope
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        decode_recovery_record(json.dumps(envelope).encode())


@pytest.mark.parametrize("path", [
    (), ("record",), ("record", "descriptor"), ("record", "steps", 0),
])
def test_unknown_missing_and_duplicate_fields_refuse_at_every_object_level(record, path):
    envelope = json.loads(encode_recovery_record(record))
    target = envelope
    for key in path:
        target = target[key]
    target["retirement_authorized"] = True
    with pytest.raises(ValueError):
        decode_recovery_record(json.dumps(envelope).encode())
    del target["retirement_authorized"]
    key = next(iter(target))
    original = json.dumps(envelope)
    marker = json.dumps(key) + ":"
    duplicate = original.replace(marker, marker + json.dumps(target[key]) + "," + marker, 1)
    with pytest.raises(ValueError):
        decode_recovery_record(duplicate.encode())
    del target[key]
    with pytest.raises(ValueError):
        decode_recovery_record(json.dumps(envelope).encode())


@pytest.mark.parametrize("payload", [
    b"", b"\xff", b"\xef\xbb\xbf{}", b"{}", b"[]", b"null",
    b'{"value":NaN}', b'{"value":Infinity}', b'{"value":1e1000}',
    b"[" * 2000 + b"]" * 2000, b" " * (MAX_RECORD_BYTES + 1),
])
def test_malformed_oversized_and_non_utf8_records_are_rejected(payload):
    with pytest.raises(ValueError):
        decode_recovery_record(payload)


def test_no_truncation_status_shortcut_or_lone_surrogate_repair(record):
    with pytest.raises(ValueError):
        encode_recovery_record(record.status())
    envelope = json.loads(encode_recovery_record(record))
    envelope["record"]["operation_id"] = "\ud800"
    with pytest.raises(ValueError):
        decode_recovery_record(json.dumps(envelope).encode())
    with pytest.raises(ValueError):
        encode_recovery_record(replace(record, operation_id="\ud800"))
    with pytest.raises(ValueError):
        decode_recovery_record(encode_recovery_record(record)[:-1])


def test_unicode_references_and_reference_bounds_are_lossless(record):
    record = replace(record, operation_id="operation-\u00e9")
    assert decode_recovery_record(encode_recovery_record(record)) == record
    envelope = json.loads(encode_recovery_record(record))
    envelope["record"]["descriptor"]["owner_ref"] = "x" * 257
    with pytest.raises(ValueError):
        decode_recovery_record(json.dumps(envelope).encode())


@pytest.fixture
def permit_intent(record):
    return PermitRecoveryIntent(
        descriptor=replace(record.descriptor, lease_revision=71),
        operation_id=record.operation_id, phase="resume_intent", record_revision=3,
        source_lease_ref="opaque-source-lease", receiver_ref="original-receiver", receiver_revision=900,
        permit_id="original-permit", original_permit_revision=1, permit_revision=4,
        decision_ref="owner-retained-restore-decision",
    )


@pytest.mark.parametrize("phase,revision,action", [
    ("retire_intent", 1, "retire"), ("resume_intent", 3, "restore"),
])
def test_permit_intent_keeps_three_domains_independent_without_upgrading_v1(
    record, permit_intent, phase, revision, action,
):
    before = encode_recovery_record(record)
    intent = replace(permit_intent, phase=phase, record_revision=revision)
    encoded = encode_permit_recovery_intent(intent)
    restored = decode_permit_recovery_intent(encoded)
    assert restored == intent
    assert encode_permit_recovery_intent(restored) == encoded
    assert restored.descriptor.lease_revision == 71
    assert restored.source_lease_ref == "opaque-source-lease"
    assert restored.receiver_ref == "original-receiver"
    assert restored.receiver_revision == 900
    assert restored.permit_revision == 4
    status = restored.status()
    assert status["action"] == action
    assert status["owner_store"] == status["native_recovery"] == "unqualified"
    assert not status["effects_wired"] and status["reconciliation_required"]
    assert status["replay"] == "never"
    with pytest.raises(ValueError):
        decode_recovery_record(encoded)
    with pytest.raises(ValueError):
        decode_permit_recovery_intent(before)
    with pytest.raises(ValueError):
        encode_permit_recovery_intent(record)
    assert encode_recovery_record(decode_recovery_record(before)) == before


@pytest.mark.parametrize("field,value", [
    ("phase", "settled"), ("phase", []), ("record_revision", True), ("record_revision", 2),
    ("source_lease_ref", 5), ("source_lease_ref", ""), ("receiver_ref", ""),
    ("receiver_revision", True),
    ("receiver_revision", 0), ("permit_id", ""), ("permit_id", "x" * 257),
    ("original_permit_revision", True), ("original_permit_revision", 0),
    ("permit_revision", True), ("permit_revision", 1), ("permit_revision", "4"),
    ("decision_ref", ""), ("decision_ref", "decision\nother"),
])
def test_invalid_or_start_only_permit_intents_refuse(permit_intent, field, value):
    envelope = json.loads(encode_permit_recovery_intent(permit_intent))
    envelope["intent"][field] = value
    with pytest.raises(ValueError):
        decode_permit_recovery_intent(json.dumps(envelope).encode())
    with pytest.raises(ValueError):
        replace(permit_intent, **{field: value})


@pytest.mark.parametrize("path", [(), ("intent",), ("intent", "descriptor")])
def test_permit_intent_strict_fields_and_versions_cannot_claim_extra_authority(permit_intent, path):
    envelope = json.loads(encode_permit_recovery_intent(permit_intent))
    target = envelope
    for key in path:
        target = target[key]
    target["retirement_authorized"] = True
    with pytest.raises(ValueError):
        decode_permit_recovery_intent(json.dumps(envelope).encode())
    del target["retirement_authorized"]
    key = next(iter(target))
    marker = json.dumps(key) + ":"
    duplicate = json.dumps(envelope).replace(
        marker, marker + json.dumps(target[key]) + "," + marker, 1,
    )
    with pytest.raises(ValueError):
        decode_permit_recovery_intent(duplicate.encode())
    del target[key]
    with pytest.raises(ValueError):
        decode_permit_recovery_intent(json.dumps(envelope).encode())
    envelope = json.loads(encode_permit_recovery_intent(permit_intent))
    envelope["version"] = 2
    with pytest.raises(ValueError):
        decode_permit_recovery_intent(json.dumps(envelope).encode())


@pytest.mark.parametrize("payload", [
    b"", b"\xff", b'{"value":NaN}', b'{"value":1.5}',
    b"[" * 2000 + b"]" * 2000, b" " * (MAX_RECORD_BYTES + 1),
])
def test_permit_intent_malformed_encoding_refuses(payload):
    with pytest.raises(ValueError):
        decode_permit_recovery_intent(payload)
