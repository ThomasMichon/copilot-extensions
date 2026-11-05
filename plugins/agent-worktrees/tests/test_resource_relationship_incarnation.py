"""Expected workspace identity is checked by the actual locked writer."""

from __future__ import annotations

import copy

import pytest
import yaml

from agent_worktrees import claim_providers, tracking, tracking_write
from agent_worktrees import resource_relationship as relationship
from test_resource_producer import owner as owner
from test_resource_relationship import call, reserve, retired


def expected(path, payload):
    return {**payload, "expected_creation_nonce": tracking.load_record(path).creation_nonce}


def test_current_writer_returns_incarnation_for_reserve_replay_bind_and_inspect(owner):
    path, _ = owner
    payload = expected(path, {
        "kind": "bridge", "namespace": "example", "request_id": "expected-owner", "recovery": {},
    })
    pending = call(path, "reserve", payload)
    assert pending.get("ok"), pending
    assert pending["creation_nonce"] == payload["expected_creation_nonce"]
    assert pending["writer_contract"] == relationship.writer_contract()
    before = path.read_bytes()
    assert call(path, "reserve", payload) == pending and path.read_bytes() == before
    assert call(path, "inspect", expected(path, {"token": pending["token"]})) == pending
    bound = call(path, "bind", expected(path, {"token": pending["token"], "identity": {"native_id": "one"}}))
    assert bound["phase"] == "bound" and bound["creation_nonce"] == pending["creation_nonce"]
    assert call(path, "reserve", payload) == bound


@pytest.mark.parametrize("operation,payload", [
    ("reserve", {"kind": "bridge", "namespace": "example", "request_id": "new", "recovery": {}}),
    ("inspect", {"request_id": "missing", "allow_missing": True}),
    ("bind", {"identity": {"native_id": "one"}}),
    ("close", {}),
])
def test_wrong_incarnation_cannot_write_inspect_absence_or_probe(owner, monkeypatch, operation, payload):
    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: pytest.fail("wrong owner probed"))
    if operation in {"bind", "close"}:
        payload = {**payload, "token": pending["token"]}
    before = path.read_bytes()
    result = call(path, operation, {**payload, "expected_creation_nonce": "a-different-incarnation"})
    assert result.get("error") and "incarnation" in result["message"]
    assert "ok" not in result and "absent" not in result
    assert path.read_bytes() == before


@pytest.mark.parametrize("nonce", [None, "", " ", 1, True, [], {}])
def test_invalid_expected_incarnation_is_not_a_legacy_request(owner, nonce):
    path, _ = owner
    before = path.read_bytes()
    result = call(path, "inspect", {
        "request_id": "missing", "allow_missing": True, "expected_creation_nonce": nonce,
    })
    assert result.get("error") and path.read_bytes() == before


def test_expected_incarnation_does_not_backfill_a_legacy_record(owner):
    path, _ = owner
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.pop("creation_nonce")
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    before = path.read_bytes()
    assert call(path, "inspect", {
        "request_id": "missing", "allow_missing": True, "expected_creation_nonce": "not-established",
    }).get("error")
    assert path.read_bytes() == before
    legacy = call(path, "inspect", {"request_id": "missing", "allow_missing": True})
    assert legacy["absent"] is True and "creation_nonce" not in legacy


def test_close_rechecks_incarnation_after_external_probe(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    payload = expected(path, {"token": pending["token"]})

    def replace_record(ref):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["creation_nonce"] = "replacement-incarnation"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        return retired(ref)

    monkeypatch.setattr(claim_providers, "resolve_claim_status", replace_record)
    result = call(path, "close", payload)
    assert result.get("error") and "incarnation" in result["message"]
    assert tracking.load_record(path).resources[0].is_unsettled


def test_close_and_terminal_replay_keep_exact_incarnation(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    payload = expected(path, {"token": pending["token"]})
    monkeypatch.setattr(claim_providers, "resolve_claim_status", retired)
    closed = call(path, "close", payload)
    assert closed["phase"] == "closed" and closed["creation_nonce"] == payload["expected_creation_nonce"]
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    before = path.read_bytes()
    assert call(path, "close", payload) == closed and path.read_bytes() == before


@pytest.fixture
def installed_writer(monkeypatch, tmp_path):
    from agent_worktrees import config, runtime_identity

    root = tmp_path / "selected-installation"
    monkeypatch.setattr(config, "install_dir", lambda: root)
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", "1" * 32)
    return {"installation_root": str(root), "runtime_generation": "1" * 32}


def test_guarded_writer_preserves_exact_relationship_through_replay_and_close(owner, installed_writer, monkeypatch):
    path, _ = owner

    def guarded(payload):
        return {**expected(path, payload), "expected_writer_identity": installed_writer}

    payload = guarded({
        "kind": "bridge", "namespace": "example", "request_id": "guarded", "recovery": {},
    })
    pending = call(path, "reserve", payload)
    assert pending.get("ok"), pending
    assert pending["writer_identity"] == installed_writer
    before = path.read_bytes()
    assert call(path, "reserve", payload) == pending and path.read_bytes() == before
    bound = call(path, "bind", guarded({"token": pending["token"], "identity": {"native_id": "one"}}))
    assert bound["phase"] == "bound" and bound["token"] == pending["token"]
    assert call(path, "inspect", guarded({"token": pending["token"]})) == bound
    monkeypatch.setattr(claim_providers, "resolve_claim_status", retired)
    closed = call(path, "close", guarded({"token": pending["token"]}))
    assert closed["phase"] == "closed" and closed["writer_identity"] == installed_writer
    before = path.read_bytes()
    assert call(path, "close", guarded({"token": pending["token"]})) == closed
    assert path.read_bytes() == before


@pytest.mark.parametrize("operation,payload", [
    ("reserve", {"kind": "bridge", "namespace": "example", "request_id": "new", "recovery": {}}),
    ("inspect", {"request_id": "missing", "allow_missing": True}),
    ("bind", {"identity": {"native_id": "one"}}),
    ("close", {}),
])
@pytest.mark.parametrize("change", ["installation", "build", "unknown"])
def test_changed_executing_writer_refuses_before_record_access_or_probe(
    owner, installed_writer, monkeypatch, tmp_path, operation, payload, change,
):
    from agent_worktrees import config, runtime_identity, tracking_relationship_write

    path, _ = owner
    pending = reserve(path)
    if operation in {"bind", "close"}:
        payload = {**payload, "token": pending["token"]}
    payload = {**expected(path, payload), "expected_writer_identity": installed_writer}
    if change == "installation":
        monkeypatch.setattr(config, "install_dir", lambda: tmp_path / "other-installation")
    else:
        monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", None if change == "unknown" else "2" * 32)
    monkeypatch.setattr(tracking_relationship_write, "_owned_record", lambda *a, **k: pytest.fail("unadmitted writer loaded record"))
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: pytest.fail("unadmitted writer probed"))
    before = path.read_bytes()
    result = call(path, operation, payload)
    assert result.get("error") and "expected_writer_identity" in result["message"]
    assert "ok" not in result and "absent" not in result
    assert path.read_bytes() == before


@pytest.mark.parametrize("identity", [
    None, {}, True, {"installation_root": "root", "runtime_generation": None},
    {"installation_root": "", "runtime_generation": "1" * 32},
    {"installation_root": "root", "runtime_generation": "package-version"},
    {"installation_root": "root", "runtime_generation": 1},
    {"installation_root": "root", "runtime_generation": "1" * 32, "extra": True},
])
def test_malformed_writer_guard_is_not_a_legacy_request(owner, identity):
    path, _ = owner
    before = path.read_bytes()
    result = call(path, "inspect", {
        **expected(path, {"request_id": "missing", "allow_missing": True}),
        "expected_writer_identity": identity,
    })
    assert result.get("error") and "expected_writer_identity" in result["message"]
    assert path.read_bytes() == before


def test_writer_guard_requires_original_nonce_and_does_not_disable_baseline(owner, installed_writer, monkeypatch):
    from agent_worktrees import runtime_identity

    path, _ = owner
    payload = {"request_id": "missing", "allow_missing": True}
    before = path.read_bytes()
    assert call(path, "inspect", {**payload, "expected_writer_identity": installed_writer}).get("error")
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", None)
    assert call(path, "inspect", payload)["absent"] is True
    observed = call(path, "inspect", expected(path, payload))
    assert observed["absent"] is True and observed["writer_identity"]["runtime_generation"] is None
    assert path.read_bytes() == before


def test_close_rechecks_writer_after_external_probe(owner, installed_writer, monkeypatch, tmp_path):
    from agent_worktrees import config

    path, _ = owner
    pending = reserve(path)
    payload = {**expected(path, {"token": pending["token"]}), "expected_writer_identity": installed_writer}
    before = path.read_bytes()

    def change_selection(ref):
        monkeypatch.setattr(config, "install_dir", lambda: tmp_path / "replacement-installation")
        return retired(ref)

    monkeypatch.setattr(claim_providers, "resolve_claim_status", change_selection)
    result = call(path, "close", payload)
    assert result.get("error") and "expected_writer_identity" in result["message"]
    assert path.read_bytes() == before and tracking.load_record(path).resources[0].is_unsettled


def test_generic_serializer_preserves_backfill_and_refuses_a_reused_record(owner):
    path, _ = owner
    stale = tracking.load_record(path)
    stale.creation_nonce = ""
    original_nonce = tracking.load_record(path).creation_nonce
    reserve(path)
    tracking.save_record(stale, path)
    assert tracking.load_record(path).creation_nonce == original_nonce
    assert tracking.load_record(path).resources[0].is_unsettled
    foreign = copy.deepcopy(stale)
    foreign.creation_nonce = "replacement-incarnation"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="incarnation"):
        tracking.save_record(foreign, path)
    assert path.read_bytes() == before


def test_real_resident_contract_and_registered_handler_are_not_cli_payload_support(owner):
    path, _ = owner
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        rendezvous = tracking_write.rendezvous_fields(server)
        contract = rendezvous["tracking_write_record_contract"]
        assert contract["schema"] == "copilot-extensions.worktree-record-writer"
        assert contract["version"] == contract["retained_relationships"] == contract["creation_nonce"] == 1
        args = {
            "schema": relationship.SCHEMA, "version": 1, "operation": "inspect",
            "input": expected(path, {"request_id": "missing", "allow_missing": True}),
            "owner_ref": "m/p/owner", "yaml_path": str(path),
        }
        before = path.read_bytes()
        result = tracking_write.dispatch(
            "resource_relationship", args, read_lock_data=lambda: rendezvous,
            ensure_monitor=None, min_version=2,
        )
        assert result["absent"] is True
        assert result["writer_contract"] == contract
        assert result["creation_nonce"] == args["input"]["expected_creation_nonce"]
        assert path.read_bytes() == before
        args["input"] = {"request_id": "missing", "allow_missing": True}
        baseline = tracking_write.dispatch(
            "resource_relationship", args, read_lock_data=lambda: rendezvous,
            ensure_monitor=None,
        )
        assert baseline["absent"] is True and "writer_contract" not in baseline
        assert "creation_nonce" not in baseline and path.read_bytes() == before
        legacy = {key: value for key, value in rendezvous.items() if key != "tracking_write_record_contract"}
        legacy["tracking_write_verb_versions"] = {}
        assert tracking_write.endpoint_from_rendezvous(legacy, verb="resource_relationship", min_version=2) is None
        assert tracking_write.endpoint_from_rendezvous(legacy, verb="resource_relationship") is not None
    finally:
        server.close()
