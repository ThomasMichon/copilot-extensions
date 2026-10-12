import copy
import json
import os
import subprocess
import sys

import pytest

from fleet_manager.operations import OperationIntent, TargetReceipt
from fleet_manager.planning import PlanError


def request():
    return {"schema": "fleet-manager.operation-intent", "version": 1,
            "operation": "op-1", "authority": "fleet-1", "principal": "actor-1",
            "account": "account-1", "target": "node-1", "generation": 1,
            "command": "task.submit", "parameters": {"objective": "objective-1"}}


def receipt(intent, status="accepted"):
    data = intent.to_dict()
    del data["parameters"]
    data.update(schema="fleet-manager.target-receipt", request_digest=intent.request_digest,
                sequence=1, status=status,
                result_ref="outcome-1" if status in ("succeeded", "failed") else None)
    return data


def test_intent_is_immutable_content_bound_and_canonical():
    source = request()
    intent = OperationIntent.from_dict(source)
    source["parameters"]["objective"] = "changed"
    assert intent.to_dict()["parameters"]["objective"] == "objective-1"
    exported = intent.to_dict()
    exported["target"] = "changed"
    assert intent.to_dict()["target"] == "node-1"
    reordered = dict(reversed(list(request().items())))
    assert intent.request_digest == OperationIntent.from_dict(reordered).request_digest
    intent.check_repeat(OperationIntent.from_dict(request()))


@pytest.mark.parametrize("field,value", [
    ("target", "node-2"), ("generation", 2),
    ("parameters", {"objective": "objective-2"}),
])
def test_identity_reuse_rejects_changed_content(field, value):
    intent = OperationIntent.from_dict(request())
    changed = request()
    changed[field] = value
    with pytest.raises(PlanError, match="conflicting intent"):
        intent.check_repeat(OperationIntent.from_dict(changed))


@pytest.mark.parametrize("field", ["authority", "principal", "account", "operation"])
def test_distinct_identity_is_not_deduplication(field):
    intent = OperationIntent.from_dict(request())
    changed = request()
    changed[field] = "other"
    with pytest.raises(PlanError, match="identity mismatch"):
        intent.check_repeat(OperationIntent.from_dict(changed))


@pytest.mark.parametrize("status", ["accepted", "succeeded", "failed", "unknown"])
def test_receipt_correlates_and_keeps_acceptance_distinct_from_outcome(status):
    intent = OperationIntent.from_dict(request())
    claim = TargetReceipt.from_dict(receipt(intent, status))
    claim.check_intent(intent)
    assert claim.to_dict()["status"] == status
    assert "objective_complete" not in claim.to_dict()
    if status in ("accepted", "unknown"):
        assert claim.to_dict()["result_ref"] is None
    else:
        assert claim.to_dict()["result_ref"] == "outcome-1"


@pytest.mark.parametrize("field", ["authority", "principal", "account", "operation", "target", "generation"])
def test_receipt_wrong_scope_is_refused(field):
    intent = OperationIntent.from_dict(request())
    wrong = receipt(intent)
    wrong[field] = 2 if field == "generation" else "other"
    with pytest.raises(PlanError, match="scope or generation mismatch"):
        TargetReceipt.from_dict(wrong).check_intent(intent)


def test_receipt_digest_and_command_bind_the_actual_request():
    intent = OperationIntent.from_dict(request())
    wrong = receipt(intent)
    wrong["request_digest"] = "f" * 64
    with pytest.raises(PlanError, match="correlation mismatch"):
        TargetReceipt.from_dict(wrong).check_intent(intent)
    wrong = receipt(intent)
    wrong["command"] = "prompt.deliver"
    with pytest.raises(PlanError, match="scope or generation mismatch"):
        TargetReceipt.from_dict(wrong).check_intent(intent)


@pytest.mark.parametrize("change", [
    {"command": "execute"}, {"generation": True}, {"generation": 10**1000},
    {"parameters": {"objective": "x", "script": "private-shell"}},
    {"unknown-private": "private-value"}, {"version": True},
])
def test_intent_strict_validation_omits_private_input(change):
    value = request()
    value.update(change)
    with pytest.raises(PlanError) as error:
        OperationIntent.from_dict(value)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("change", [
    {"sequence": True}, {"status": "done"}, {"request_digest": "A" * 64},
    {"status": "unknown", "result_ref": "claimed-success"},
    {"status": "accepted", "result_ref": "claimed-success"},
    {"status": "succeeded", "result_ref": None},
])
def test_receipt_strict_validation(change):
    intent = OperationIntent.from_dict(request())
    value = receipt(intent)
    value.update(change)
    with pytest.raises(PlanError):
        TargetReceipt.from_dict(value)


def test_direct_construction_and_roundtrip_preserve_validated_bytes():
    intent = OperationIntent.from_dict(request())
    assert OperationIntent(intent.encoded).request_digest == intent.request_digest
    with pytest.raises(PlanError):
        OperationIntent(bytearray(intent.encoded))
    with pytest.raises(PlanError, match="canonical"):
        OperationIntent(json.dumps(request(), indent=2).encode())
    data = receipt(intent, "unknown")
    before = copy.deepcopy(data)
    claim = TargetReceipt.from_dict(data)
    assert data == before
    assert TargetReceipt(claim.encoded).to_dict() == data


@pytest.mark.parametrize("encoded", [
    b'{"schema":"one","schema":"two"}', b"\xff", b"[]", b"null",
    b'{"unexpected":' + b"[" * 20 + b"0" + b"]" * 20 + b"}",
    b"x" * (1024 * 1024 + 1),
], ids=["duplicate-keys", "invalid-utf8", "array-root", "null-root", "depth-limit", "byte-limit"])
def test_direct_wire_construction_refuses_invalid_bounded_json(encoded):
    for constructor in (OperationIntent, TargetReceipt):
        with pytest.raises(PlanError):
            constructor(encoded)


def test_installed_operation_contract_roundtrip_in_isolated_process(tmp_path):
    source = tmp_path / "intent.json"
    source.write_text(json.dumps(request()), encoding="utf-8")
    before = source.read_bytes()
    script = (
        "import json,sys;"
        "from fleet_manager.operations import OperationIntent,TargetReceipt;"
        "i=OperationIntent.from_dict(json.load(open(sys.argv[1])));"
        "v=i.to_dict();v.pop('parameters');"
        "v.update(schema='fleet-manager.target-receipt',request_digest=i.request_digest,"
        "sequence=1,status='unknown',result_ref=None);"
        "r=TargetReceipt.from_dict(v);r.check_intent(i);"
        "print(json.dumps(r.to_dict()))"
    )
    run = subprocess.run([sys.executable, "-I", "-c", script, str(source)],
                         capture_output=True, text=True, timeout=15, cwd=tmp_path,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "unknown"
    assert source.read_bytes() == before
    assert [path.name for path in tmp_path.iterdir()] == ["intent.json"]
