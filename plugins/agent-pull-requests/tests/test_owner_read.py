"""Synthetic owner gates; these controls do not establish an operational owner."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from agent_pull_requests.owner_read import (
    OwnerReadBinding, OwnerReadBudgetExceeded, OwnerReadHandler,
    OwnerReadResponseTooLarge, OwnerReadUnavailable, parse_request,
)
from agent_pull_requests.owner_read_cli import main
from agent_pull_requests.review_workspace import compare_generations


@pytest.fixture
def request_value():
    return {
        "schema": "agent-pull-requests.owner-read-request", "version": 1,
        "binding_ref": "example-read-binding", "number": 42, "budget_seconds": 30,
        "expected_principal": "effective-actor",
    }


@pytest.fixture
def fixture_owner():
    path = Path(__file__).resolve().parents[1] / "docs" / "review-evidence.example.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["scope"]["principal_id"] = "effective-actor"
    evidence["acquisition"] = {
        "source": "owner_executed_review_evidence/v1", "effects": "none",
        "principal_semantics": "connectionData.authorizedUser.id",
        "snapshot_atomic": False, "owner_identity_checks": 2,
        "private_credential": "synthetic-secret-not-for-response",
    }
    state = {"allowed": True, "reads": 0, "checks": 0, "change_scope": False, "revoke": False}

    def checkpoint():
        state["checks"] += 1
        if not state["allowed"]:
            raise OwnerReadUnavailable("synthetic owner context expired or denied")

    def collect(number, remaining):
        assert number == 42 and 0 < remaining() <= 30
        state["reads"] += 1
        value = copy.deepcopy(evidence)
        if state["change_scope"]:
            value["scope"]["principal_id"] = "another-effective-actor"
        if state["revoke"]:
            state["allowed"] = False
        return value

    scope = {key: value for key, value in evidence["scope"].items() if key != "number"}
    binding = OwnerReadBinding(scope, "synthetic-context", checkpoint, collect)

    def resolver(reference, remaining):
        checkpoint()
        assert remaining() > 0
        if reference != "example-read-binding":
            raise OwnerReadUnavailable("binding is not authorized for this caller")
        return binding

    return state, resolver


def test_protected_embedding_returns_a_bounded_honestly_incomplete_snapshot(request_value, fixture_owner):
    state, resolver = fixture_owner
    handler = OwnerReadHandler(resolver, max_work_seconds=30, max_payload_bytes=65536)
    response = handler.read(request_value)
    assert response["generation"]["complete"] is False
    assert response["generation"]["scope"]["principal_id"] == "effective-actor"
    assert response["generation"]["sections"]["policies"]["complete"] is False
    assert response["owner"]["principal_semantics"] == "connectionData.authorizedUser.id"
    encoded = json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert response["payload_bytes"] == len(encoded.encode("utf-8"))
    assert response["payload_limit_bytes"] == 65536
    assert "synthetic-secret" not in encoded
    assert state["reads"] == 1 and state["checks"] >= 4
    assert response["effects"] == "none"
    compared = compare_generations(response["generation"], response["generation"])
    assert compared["changed_sections"] == compared["invalidations"] == []


@pytest.mark.parametrize("failure", ["wrong_binding", "wrong_expected_principal", "denied"])
def test_wrong_or_expired_owner_context_refuses_before_read(request_value, fixture_owner, failure):
    state, resolver = fixture_owner
    if failure == "wrong_binding":
        request_value["binding_ref"] = "another-binding"
    elif failure == "wrong_expected_principal":
        request_value["expected_principal"] = "transport-actor"
    else:
        state["allowed"] = False
    handler = OwnerReadHandler(resolver, max_work_seconds=30, max_payload_bytes=65536)
    with pytest.raises(OwnerReadUnavailable):
        handler.read(request_value)
    assert state["reads"] == 0


@pytest.mark.parametrize("failure", ["change_scope", "revoke"])
def test_scope_drift_or_lost_authority_cannot_publish(request_value, fixture_owner, failure):
    state, resolver = fixture_owner
    state[failure] = True
    with pytest.raises(OwnerReadUnavailable):
        OwnerReadHandler(resolver, max_work_seconds=30, max_payload_bytes=65536).read(request_value)
    assert state["reads"] == 1


def test_owner_limits_are_not_inferred_from_the_request(request_value, fixture_owner):
    _, resolver = fixture_owner
    request_value["budget_seconds"] = 300
    response = OwnerReadHandler(resolver, max_work_seconds=5, max_payload_bytes=65536).read(request_value)
    assert response["work_budget_seconds"] == 5
    with pytest.raises(OwnerReadResponseTooLarge):
        OwnerReadHandler(resolver, max_work_seconds=5, max_payload_bytes=256).read(request_value)


def test_overrun_after_collection_does_not_publish(request_value, fixture_owner):
    _, resolver = fixture_owner
    ticks = [0.0]
    original = resolver("example-read-binding", lambda: 10)

    def collect(number, remaining):
        result = original.collect(number, remaining)
        ticks[0] = 4
        return result

    binding = OwnerReadBinding(original.scope, original.context_id, original.checkpoint, collect)
    handler = OwnerReadHandler(
        lambda *_: binding, max_work_seconds=3, max_payload_bytes=65536, clock=lambda: ticks[0],
    )
    with pytest.raises(OwnerReadBudgetExceeded):
        handler.read(request_value)


@pytest.mark.parametrize("extra", ["endpoint", "account", "token", "authorized", "command", "verdict"])
def test_caller_labels_and_commands_never_become_an_owner(request_value, extra):
    request_value[extra] = "synthetic-value"
    with pytest.raises(ValueError):
        parse_request(request_value)


def test_no_implicit_owner_or_credential_fallback(request_value):
    with pytest.raises(OwnerReadUnavailable):
        OwnerReadHandler(None, max_work_seconds=30, max_payload_bytes=65536)
    with pytest.raises(OwnerReadUnavailable):
        OwnerReadHandler(lambda *_: {"authorized": True}, max_work_seconds=30,
                         max_payload_bytes=65536).read(request_value)


def test_existing_owner_permission_denial_is_preserved_without_new_ceremony(request_value):
    denied = PermissionError("existing owner pipeline denied this request")

    def resolver(*_):
        raise denied

    handler = OwnerReadHandler(resolver, max_work_seconds=30, max_payload_bytes=65536)
    with pytest.raises(PermissionError) as failure:
        handler.read(request_value)
    assert failure.value is denied


@pytest.mark.parametrize("value", [True, False])
def test_boolean_labels_cannot_be_used_as_owner_checkpoints(request_value, fixture_owner, value):
    state, resolver = fixture_owner
    binding = resolver("example-read-binding", lambda: 10)
    invalid = OwnerReadBinding(binding.scope, binding.context_id, lambda: value, binding.collect)
    handler = OwnerReadHandler(lambda *_: invalid, max_work_seconds=30, max_payload_bytes=65536)
    with pytest.raises(OwnerReadUnavailable):
        handler.read(request_value)
    assert state["reads"] == 0


def test_offline_inspector_does_not_advertise_operational_adoption(request_value, tmp_path, capsys):
    assert main(["contract"]) == 0
    assert json.loads(capsys.readouterr().out)["operational_owner"] is False
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request_value), encoding="utf-8")
    assert main(["validate-request", "--request", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["owner_adoption"] == "not_established"
    assert result["backend_access"] is False
    path.write_text('{"token":"synthetic-secret","token":1}', encoding="utf-8")
    assert main(["validate-request", "--request", str(path)]) == 1
    assert "synthetic-secret" not in capsys.readouterr().out
    with pytest.raises(SystemExit) as failure:
        main(["read"])
    assert failure.value.code == 2


def test_returned_generations_compare_through_the_actual_offline_cli(
    request_value, fixture_owner, tmp_path, capsys,
):
    from agent_pull_requests.review_workspace_cli import main as workspace_main

    _, resolver = fixture_owner
    response = OwnerReadHandler(resolver, max_work_seconds=30, max_payload_bytes=65536).read(request_value)
    path = tmp_path / "generation.json"
    path.write_text(json.dumps(response["generation"]), encoding="utf-8")
    assert workspace_main(["compare", "--generation", str(path), "--previous", str(path)]) == 0
    compared = json.loads(capsys.readouterr().out)
    assert compared["changed_sections"] == compared["invalidations"] == []
    path.write_text("null", encoding="utf-8")
    assert workspace_main(["compare", "--generation", str(path), "--previous", str(path)]) == 1


@pytest.mark.parametrize("side", ["current", "previous"])
@pytest.mark.parametrize("revision", [{}, {"head_sha": []}])
def test_correct_hash_does_not_make_malformed_revision_comparable(
    request_value, fixture_owner, tmp_path, capsys, side, revision,
):
    from agent_pull_requests.review_workspace_cli import main as workspace_main

    _, resolver = fixture_owner
    generation = OwnerReadHandler(
        resolver, max_work_seconds=30, max_payload_bytes=65536,
    ).read(request_value)["generation"]
    malformed = copy.deepcopy(generation)
    malformed["revision"] = revision
    malformed["id"] = hashlib.sha256(json.dumps(
        {key: value for key, value in malformed.items() if key != "id"},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    current = tmp_path / "current.json"
    previous = tmp_path / "previous.json"
    current.write_text(json.dumps(malformed if side == "current" else generation), encoding="utf-8")
    previous.write_text(json.dumps(malformed if side == "previous" else generation), encoding="utf-8")
    assert workspace_main(["compare", "--generation", str(current), "--previous", str(previous)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "error" in result and "generation" not in result
