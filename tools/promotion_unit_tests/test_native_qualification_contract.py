"""Pure/fake observations only; no native Job or qualification execution."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import native_qualification_contract as contract

UTC = "2026-10-31T08:00:00+00:00"


def qualification(tmp_path: Path) -> contract.Qualification:
    (tmp_path / "evidence").mkdir()
    result = contract.Qualification(contract.Boundary(10, UTC), 7, "a" * 64, tmp_path)
    result.validate(tmp_path, lambda: 20)
    return result


@pytest.mark.parametrize("origin", [0, -1, True, float("nan"), float("inf"), 21])
def test_supplied_origin_never_accepts_nonfinite_future_or_new_clock(origin: float) -> None:
    with pytest.raises(contract.QualificationError):
        contract.Boundary.supplied(origin, UTC, lambda: 20)


@pytest.mark.parametrize("utc", ["start", "2026-10-31T08:00:00", "2026-10-31T08:00:00-07:00", None])
def test_audit_origin_requires_explicit_utc_without_controlling_deadlines(utc: str) -> None:
    with pytest.raises((ValueError, contract.QualificationError)):
        contract.Boundary.supplied(10, utc, lambda: 20)


def test_setup_and_retirement_share_one_original_bound() -> None:
    boundary = contract.Boundary.supplied(10, UTC, lambda: 20)
    assert boundary.remaining(work=True, clock=lambda: 174) == 1
    with pytest.raises(contract.QualificationError):
        boundary.remaining(work=True, clock=lambda: 175)
    assert boundary.remaining(clock=lambda: 175) == 15
    with pytest.raises(contract.QualificationError):
        boundary.remaining(clock=lambda: 190)


def test_work_completion_admits_only_the_existing_retirement_slice(tmp_path: Path) -> None:
    result = qualification(tmp_path)
    contract.publish_work_completion(result, candidate_exit=0, workers_exited=True, clock=lambda: 174)
    assert result.phase_remaining(lambda: 175) == 15
    assert result.phase_remaining(lambda: 189) == 1
    with pytest.raises(contract.QualificationError):
        result.phase_remaining(lambda: 190)


def test_missing_work_proof_cannot_turn_whole_bound_into_a_work_allowance(tmp_path: Path) -> None:
    result = qualification(tmp_path)
    with pytest.raises(contract.QualificationError):
        result.phase_remaining(lambda: 175)
    assert not result.work_completed


@pytest.mark.parametrize("code,closed,now", [(1, True, 20), (False, True, 20), (0, False, 20), (0, True, 175)])
def test_failed_or_late_work_cannot_publish_a_phase(
    tmp_path: Path, code: int, closed: bool, now: float,
) -> None:
    result = qualification(tmp_path)
    with pytest.raises(contract.QualificationError):
        contract.publish_work_completion(result, candidate_exit=code, workers_exited=closed, clock=lambda: now)
    assert not result.work_receipt.exists()


def test_scope_and_source_proof_cannot_be_replayed_or_changed(tmp_path: Path) -> None:
    result = qualification(tmp_path)
    contract.publish_work_completion(result, candidate_exit=0, workers_exited=True, clock=lambda: 20)
    with pytest.raises(contract.QualificationError, match="already used"):
        result.validate(tmp_path, lambda: 20)
    assert result.observe_work(lambda: 20)
    value = json.loads(result.work_receipt.read_bytes())
    value["scope"] = 8
    result.work_receipt.write_text(json.dumps(value))
    with pytest.raises(contract.QualificationError, match="changed"):
        result.observe_work(lambda: 20)


def test_source_mismatched_work_proof_is_not_retirement_permission(tmp_path: Path) -> None:
    result = qualification(tmp_path)
    contract.publish_work_completion(result, candidate_exit=0, workers_exited=True, clock=lambda: 20)
    value = json.loads(result.work_receipt.read_bytes())
    value["manifest_sha256"] = "b" * 64
    result.work_receipt.write_text(json.dumps(value))
    with pytest.raises(contract.QualificationError):
        result.observe_work(lambda: 20)
    assert not result.work_completed


def receiver_report(result: contract.Qualification, **changes: Any) -> bytes:
    terminal = {
        "execution_scope": result.scope, "monotonic_start": result.boundary.start,
        "evidence_phase": contract.DESCENDANT_PHASE, "original_job_drained": False,
        "original_owner_settlement_required": True, "functional_positive": True,
    }
    raw = f"FUNCTIONAL START: scope {result.scope}; functional only\n".encode() + json.dumps(terminal).encode()
    value = {
        "kind": "native-qualification-receiver", "evidence_phase": contract.DESCENDANT_PHASE,
        "passed": True, "original_job_drained": False, "original_owner_settlement_required": True,
        "execution_scope": result.scope, "manifest_sha256": result.manifest_sha256,
        "monotonic_start": result.boundary.start, "started_utc": result.boundary.utc,
        "runtime_root": str(result.runtime_root), "desktop_qualified": False,
        "visible_controls_attempted": 0, "errors": [], "native_exit": 0,
        "native_exited": True, "streams_closed": True, "readers_settled": True,
        "process_handle_closed": True, "preparation_sources_unchanged_after": True,
        "post_capture_monotonic": 20, "post_artifact_verification_monotonic": 20,
        "terminal": terminal, "captured_streams": {
            name: {
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "base64": base64.b64encode(data).decode(), "complete": True,
            } for name, data in (("stdout", raw), ("stderr", b""))
        },
    }
    value.update(changes)
    return json.dumps(value).encode("ascii")


@pytest.mark.parametrize("change", [
    {"original_job_drained": True}, {"original_owner_settlement_required": False},
    {"evidence_phase": "settled"}, {"execution_scope": True}, {"native_exit": False},
    {"desktop_qualified": True}, {"visible_controls_attempted": False},
    {"streams_closed": False}, {"manifest_sha256": "b" * 64},
    {"post_capture_monotonic": True}, {"post_capture_monotonic": 21},
    {"post_artifact_verification_monotonic": 190}, {"captured_streams": {}},
])
def test_descendant_report_cannot_claim_original_owner_settlement(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    result = qualification(tmp_path)
    result.capture.streams[0].data.extend(receiver_report(result, **change))
    with pytest.raises(contract.QualificationError):
        result.observe_receiver(lambda: 20)
    assert result.receiver is None


@pytest.mark.parametrize("missing", [
    "worker_reaped", "worker_handle_closed", "job_zero_observed", "job_handle_closed",
    "lifecycle_settled", "registry_unchanged", "capture", "forced",
])
def test_positive_descendants_cannot_hide_missing_outside_custody(
    tmp_path: Path, missing: str,
) -> None:
    result = qualification(tmp_path)
    contract.publish_work_completion(result, candidate_exit=0, workers_exited=True, clock=lambda: 20)
    result.observe_work(lambda: 20)
    result.capture.streams[0].data.extend(receiver_report(result))
    result.observe_receiver(lambda: 20)
    result.native_exit = 0
    result.worker_reaped = result.worker_handle_closed = True
    result.job_zero_observed = result.job_handle_closed = True
    result.lifecycle_settled = result.registry_unchanged = True
    result.capture.settled = result.capture.closed = True
    result.require_settled(lambda: 20)
    if missing == "capture":
        result.capture.closed = False
    elif missing == "forced":
        result.forced_termination = True
    else:
        setattr(result, missing, False)
    with pytest.raises(contract.QualificationError):
        result.require_settled(lambda: 20)


def test_stream_overflow_is_bounded_and_drained_to_eof() -> None:
    source = io.BytesIO(b"x" * (contract.STREAM_BYTES + 1))
    stream = contract.Stream()
    stream.read(source)
    assert len(stream.data) == contract.STREAM_BYTES
    assert source.tell() == contract.STREAM_BYTES + 1
    assert len(stream.errors) == 1


def test_capture_does_not_close_a_stream_under_a_live_reader() -> None:
    capture = contract.Capture()
    capture.readers.append(SimpleNamespace(join=lambda _timeout: None, is_alive=lambda: True))
    sources = (io.BytesIO(), io.BytesIO())
    with pytest.raises(contract.QualificationError, match="live"):
        capture.finish(sources, contract.Boundary(10, UTC), lambda: 20)
    assert not any(source.closed for source in sources)
    assert not capture.closed and not capture.settled


def test_owner_envelope_bound_accounts_for_raw_stream_base64_and_terminal() -> None:
    capture = contract.Capture()
    assert capture.streams[0].limit == 4 * contract.STREAM_BYTES + 65536
    assert capture.streams[1].limit == contract.STREAM_BYTES
