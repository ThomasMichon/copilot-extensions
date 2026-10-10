import json
from dataclasses import replace

import pytest

from fleet_contracts import (
    BackendResponseError,
    ContractError,
    IndexHealth,
    IndexHit,
    IndexSearchResult,
    decode_json,
    encode_json,
)
from fleet_contracts.records import MAX_MESSAGE_BYTES, MAX_RESPONSE_BYTES


def health(**changes):
    return {
        "status": "ok", "plugin": "agent-index", "version": "0.10.12-dev1",
        "passive": False, "promoted": True, "installationId": "mkt-id/agent-index",
        "instanceToken": "private-control-token", "pid": 123, **changes,
    }


def hit(**changes):
    return {
        "id": "source/chunk-1", "chunk_id": "source/chunk-1", "score": 0.75,
        "file_path": "src/file.py", "line_start": 1, "line_end": 4,
        "source": "Docs and notes", "chunk_type": "function", "language": "python",
        "content": "full content\n" * 100, **changes,
    }


def payload(value):
    return encode_json(value, max_bytes=MAX_RESPONSE_BYTES)


def test_health_preserves_native_identity_but_does_not_export_control_capability():
    parsed = IndexHealth.from_backend(payload(health()), expected_installation_id="mkt-id/agent-index")
    assert parsed.can_accept_reads
    assert parsed.installation_id == "mkt-id/agent-index"
    exported = parsed.to_dict()
    assert "pid" not in exported and "instanceToken" not in exported
    assert "private-control-token" not in json.dumps(exported)
    assert "search_ready" not in exported


@pytest.mark.parametrize("state,passive,promoted", [
    ("passive", True, False), ("draining", False, True), ("draining", True, False),
])
def test_native_health_distinguishes_read_admission_from_liveness(state, passive, promoted):
    parsed = IndexHealth.from_backend(
        payload(health(status=state, passive=passive, promoted=promoted)),
        expected_installation_id="mkt-id/agent-index",
    )
    assert parsed.state == state
    assert not parsed.can_accept_reads


@pytest.mark.parametrize("changes", [
    {"installationId": "other"}, {"plugin": "other"}, {"status": "ready"},
    {"passive": "false"}, {"promoted": 1}, {"passive": True}, {"promoted": False},
])
def test_health_rejects_wrong_backend_or_inconsistent_evidence(changes):
    with pytest.raises(BackendResponseError):
        IndexHealth.from_backend(payload(health(**changes)),
                                 expected_installation_id="mkt-id/agent-index")


def test_search_preserves_original_wire_shape_and_unclipped_content():
    original = {"query": "query", "available": True, "hits": [hit()]}
    parsed = IndexSearchResult.from_backend(payload(original), expected_query="query",
                                           requested_limit=10)
    assert parsed.to_dict() == original
    assert len(parsed.hits[0].content) > 500
    assert parsed.hits[0].source == "Docs and notes"
    assert decode_json(payload(parsed.to_dict()), max_bytes=MAX_RESPONSE_BYTES) == original


def test_http_success_with_available_false_is_explicit_failure_not_empty_success():
    unavailable = {"query": "query", "available": False, "error": "private-backend-detail", "hits": []}
    with pytest.raises(BackendResponseError) as caught:
        IndexSearchResult.from_backend(payload(unavailable), expected_query="query",
                                       requested_limit=10)
    assert "private-backend-detail" not in str(caught.value)


@pytest.mark.parametrize("changes", [
    {"query": "other"}, {"available": 1}, {"hits": [hit(), hit()]},
    {"url": "https://untrusted.example"},
])
def test_search_rejects_wrong_query_shape_or_result_count(changes):
    value = {"query": "query", "available": True, "hits": [hit()], **changes}
    with pytest.raises(ContractError):
        IndexSearchResult.from_backend(payload(value), expected_query="query", requested_limit=1)


@pytest.mark.parametrize("changes", [
    {"id": "other"}, {"score": True}, {"score": float("inf")}, {"score": 10**400},
    {"line_start": True}, {"line_start": -1}, {"content": None},
    {"content": "\ud800"}, {"file_path": 5},
])
def test_hit_validation_retains_types_without_coercion(changes):
    with pytest.raises(ContractError):
        IndexHit.from_dict(hit(**changes))


def test_large_response_has_separate_exact_byte_budget_and_no_silent_clipping():
    original = {"query": "query", "available": True, "hits": [hit(content="x" * 70000)]}
    raw = payload(original)
    assert MAX_MESSAGE_BYTES < len(raw) < MAX_RESPONSE_BYTES
    with pytest.raises(ContractError):
        decode_json(raw)
    parsed = IndexSearchResult.from_backend(raw, expected_query="query", requested_limit=1)
    assert parsed.hits[0].content == original["hits"][0]["content"]
    with pytest.raises(ContractError):
        decode_json(b" " * (MAX_RESPONSE_BYTES + 1), max_bytes=MAX_RESPONSE_BYTES)
    for invalid in (True, 0, MAX_RESPONSE_BYTES + 1):
        with pytest.raises(ContractError):
            encode_json({}, max_bytes=invalid)
    with pytest.raises(BackendResponseError):
        replace(parsed, available=False)


def test_empty_search_success_remains_success_but_unvalidated_hit_collection_is_rejected():
    result = IndexSearchResult.from_backend(
        payload({"query": "query", "available": True, "hits": []}),
        expected_query="query", requested_limit=10,
    )
    assert result.to_dict()["hits"] == []
    with pytest.raises(ContractError):
        replace(result, hits=[IndexHit.from_dict(hit())])


@pytest.mark.parametrize("control", ["\x7f", "\x85", "\x9f"])
def test_health_rejects_control_characters_in_opaque_installation_identity(control):
    with pytest.raises(ContractError):
        IndexHealth.from_backend(
            payload(health(installationId=f"market{control}/agent-index")),
            expected_installation_id=f"market{control}/agent-index",
        )
