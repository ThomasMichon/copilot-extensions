"""Source-level disposable ledger ABI and generic bypass regression tests."""

from __future__ import annotations

import argparse
import copy
import io
import json

import pytest
import yaml

from agent_worktrees import claim_handoffs, claim_providers, config as cfg
from agent_worktrees import finalize, resource_producer as producer, resource_producer_cli as cli
from agent_worktrees import tracking, tracking_claim_write, tracking_write
from agent_worktrees.tracking_producer_write import apply_resource_producer


@pytest.fixture
def owner(tmp_path, monkeypatch):
    project = tmp_path / "project"
    records = project / "worktrees"
    records.mkdir(parents=True)
    config = cfg.Config(
        srcroot=str(tmp_path), machine="m", platform="linux", repo_name="p",
        repos={"p": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    for name in ("owner", "consumer"):
        tracking.create_new_record(name, f"worktree/{name}", str(tmp_path / name), "p", "m", "linux", records)
    return records / "owner.yaml", config


def call(path, operation, payload, ref="m/p/owner"):
    return apply_resource_producer({
        "schema": producer.SCHEMA, "version": 1, "yaml_path": str(path),
        "owner_ref": ref, "operation": operation, "input": payload,
    })


def reserve(path):
    result = call(path, "reserve", {"kind": "container", "namespace": "raw-resource", "recovery": {"endpoint": "opaque"}})
    assert result.get("ok"), result
    return result


def bind(path, reservation):
    inspected = call(path, "inspect", {"token": reservation["token"]})
    payload = {"token": reservation["token"], "identity": {"immutable_id": "full-id", "daemon": "opaque"}}
    if inspected["phase"] == "pending":
        permit = call(path, "begin", {"token": reservation["token"]})
        assert permit.get("ok"), permit
        payload["allocation_fence"] = permit["allocation_fence"]
    elif inspected["phase"] == "allocating":
        payload["allocation_fence"] = reservation["allocation_fence"]
    result = call(path, "bind", payload)
    assert result.get("ok"), result
    return result


def absence(ref):
    return {"available": True, "exists": False, "producer_verification": {"version": 1, "subject": ref, "absent": True}}


def test_reserve_durable_metadata_callback_roundtrip_and_legacy_shape(owner):
    path, _ = owner
    result = reserve(path)
    claim = tracking.load_record(path).resources[0]
    assert claim.producer == result["producer"]
    assert result["owner"] == result["creator"] == "m/p/owner"
    assert claim.producer["lifetime"] == "disposable-owned"
    assert claim.is_unsettled and claim.is_live
    envelope = producer.decode_ref(claim.ref)
    assert envelope == {key: result["producer"][key] for key in ("version", "token", "creator", "phase", "recovery")}
    assert claim_providers.split_namespaced_ref(claim.ref)[1].startswith("r1.")
    legacy = tracking.ResourceClaim(kind="container", ref="container:fleet")
    assert tracking._claim_to_yaml_dict(legacy) == {"kind": "container", "ref": "container:fleet"}
    assert tracking._parse_claim_mapping(tracking._claim_to_yaml_dict(legacy)).has_producer is False
    assert "producer" not in legacy.to_dict() and "producer_present" not in legacy.to_dict()


@pytest.mark.parametrize("metadata", [None, {}, [], "unknown", {"version": 99}, {"version": True}])
def test_malformed_present_metadata_is_preserved_and_blocks(metadata):
    raw = {"kind": "session", "ref": "broken", "state": "released", "producer": metadata}
    claim = tracking._parse_claim_mapping(raw)
    assert claim.has_producer and claim.is_live and claim.is_unsettled
    assert not claim.is_at_rest and not claim.is_abandoned
    assert tracking._claim_to_yaml_dict(claim)["producer"] == metadata
    assert not finalize._assert_obligations_settled(
        argparse.Namespace(resources=[claim]), "owner", abandon=True, handoff_to="recipient",
    )


@pytest.mark.parametrize("reference", [None, "", 0, False, [], {}, "missing"])
def test_damaged_reference_preserves_producer_through_load_save_and_finalize(owner, reference):
    path, _ = owner
    pending = reserve(path)
    raw = yaml.safe_load(path.read_text())
    if reference == "missing":
        raw["resources"][0].pop("ref")
    else:
        raw["resources"][0]["ref"] = reference
    path.write_text(yaml.safe_dump(raw))
    record = tracking.load_record(path)
    assert len(record.resources) == 1
    assert record.resources[0].producer == pending["producer"]
    assert record.resources[0].is_unsettled
    assert not finalize._assert_obligations_settled(record, "owner", abandon=False)
    tracking.save_record(record, path)
    current = tracking.load_record(path)
    assert len(current.resources) == 1 and current.resources[0].is_unsettled
    assert current.resources[0].producer == pending["producer"]


@pytest.mark.parametrize("authorized", [False, True])
def test_duplicate_producer_references_cannot_be_collapsed_by_stale_save(owner, monkeypatch, authorized):
    path, _ = owner
    stale = tracking.load_record(path)
    pending = reserve(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: absence(ref))
    assert call(path, "close", {"token": pending["token"]})["phase"] == "closed"
    raw = yaml.safe_load(path.read_text())
    malformed = copy.deepcopy(raw["resources"][0])
    malformed["producer"] = {"version": 99}
    raw["resources"].insert(0, malformed)
    path.write_text(yaml.safe_dump(raw))
    before = path.read_bytes()
    assert tracking.load_record(path).resources[0].is_unsettled
    if authorized:
        with producer.transaction(path), pytest.raises(ValueError, match="duplicate producer"):
            tracking.save_record(stale, path)
    else:
        with pytest.raises(ValueError, match="duplicate producer"):
            tracking.save_record(stale, path)
    assert path.read_bytes() == before
    assert not finalize._assert_obligations_settled(tracking.load_record(path), "owner", abandon=False)


@pytest.mark.parametrize("state", ["released", "at-rest", "abandoned", "unknown"])
def test_producer_phase_overrides_corrupt_generic_state(owner, state):
    path, _ = owner
    result = reserve(path)
    raw = tracking._claim_to_yaml_dict(tracking.load_record(path).resources[0])
    raw["state"] = state
    claim = tracking._parse_claim_mapping(raw)
    assert claim.is_unsettled and claim.is_live and not claim.is_at_rest
    raw["producer"]["phase"] = "closed"
    assert tracking._parse_claim_mapping(raw).is_unsettled
    assert result["phase"] == "pending"


def test_bind_immutable_idempotent_and_does_not_allocate(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    stale = tracking.load_record(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda *a, **k: pytest.fail("bind probed or allocated a provider"))
    result = bind(path, pending)
    before = path.read_bytes()
    assert bind(path, pending) == result
    assert path.read_bytes() == before
    refused = call(path, "bind", {"token": pending["token"], "identity": {"replacement": True}})
    assert refused["error"] == "rejected" and path.read_bytes() == before
    tracking.save_record(stale, path)
    current = tracking.load_record(path)
    assert len(current.resources) == 1 and current.resources[0].ref == result["ref"]
    assert current.resources[0].producer["identity"] == result["producer"]["identity"]


def test_failed_bind_retains_allocation_and_rejects_ref_collision(owner):
    path, _ = owner
    pending = reserve(path)
    permit = call(path, "begin", {"token": pending["token"]})
    identity = {"immutable_id": "full-id", "daemon": "opaque"}
    collision = producer.claim_ref({**permit["producer"], "phase": "bound", "identity": identity})
    record = tracking.load_record(path)
    tracking.add_resource_claim(record, tracking.ResourceClaim(kind="container", ref=collision), save=False)
    tracking.save_record(record, path)
    before = path.read_bytes()
    result = call(path, "bind", {"token": pending["token"], "identity": identity, "allocation_fence": permit["allocation_fence"]})
    assert result["error"] and path.read_bytes() == before
    assert tracking.load_record(path).resources[0].producer["phase"] == "allocating"


@pytest.mark.parametrize("status", ["finalized", "finalizing", "orphaned", "archived", "complete", "completed"])
def test_terminal_owner_cannot_reserve_or_reopen(owner, status):
    path, _ = owner
    record = tracking.load_record(path)
    record.status = status
    tracking.save_record(record, path)
    before = path.read_bytes()
    result = call(path, "reserve", {"kind": "container", "namespace": "raw-resource", "recovery": {}})
    assert result["error"] and path.read_bytes() == before


def test_wrong_record_authority_refuses_before_reservation(owner):
    path, _ = owner
    before = path.read_bytes()
    result = call(path, "reserve", {"kind": "container", "namespace": "raw-resource", "recovery": {}}, ref="m/p/not-owner")
    assert result["error"] and path.read_bytes() == before


def test_registered_api_rejects_foreign_legacy_owner_even_without_machine_registry(owner):
    path, _ = owner
    record = tracking.load_record(path)
    record.machine = "remote"
    tracking.save_record(record, path)
    before = path.read_bytes()
    result = call(path, "reserve", {"kind": "container", "namespace": "raw-resource", "recovery": {}}, ref="remote/p/owner")
    assert result["error"] and path.read_bytes() == before


@pytest.mark.parametrize("result", [
    {"available": False}, {"available": True, "exists": True},
    {"available": True, "exists": False}, {"available": True, "exists": False, "producer_verification": []},
    {"available": True, "exists": False, "producer_verification": {"version": True, "subject": "wrong", "absent": True}},
    {"available": True, "exists": False, "producer_verification": {"version": 1, "subject": "other:r1.provider-mismatch", "absent": True}},
])
def test_close_unknown_unverified_or_wrong_subject_keeps_active(owner, monkeypatch, result):
    path, _ = owner
    pending = reserve(path)
    before = path.read_bytes()
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: result)
    closed = call(path, "close", {"token": pending["token"]})
    assert closed["error"] and path.read_bytes() == before
    assert tracking.load_record(path).resources[0].is_unsettled


def test_close_forged_caller_proof_never_probes(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda *a: pytest.fail("forged close reached provider"))
    before = path.read_bytes()
    result = call(path, "close", {"token": pending["token"], "absence": True})
    assert result["error"] and path.read_bytes() == before


@pytest.mark.parametrize("bound", [False, True])
def test_registered_provider_exact_absence_closes_without_bootstrap(owner, monkeypatch, bound):
    path, _ = owner
    result = reserve(path)
    if bound:
        result = bind(path, result)
    manifest = claim_providers.ClaimProviderManifest(
        namespace="raw-resource", plugin="test-provider@test", plugin_root="/unused",
        status_command=("/unused/bin/provider",),
    )
    monkeypatch.setattr(claim_providers, "discover_claim_providers", lambda *a: ({"raw-resource": manifest}, ()))
    seen = []
    def callback(provider, **kwargs):
        seen.append(kwargs["callback_args"])
        return argparse.Namespace(returncode=0, stdout=json.dumps(absence(result["ref"])))
    monkeypatch.setattr(claim_providers, "_run_provider_process", callback)
    closed = call(path, "close", {"token": result["token"]})
    assert closed["phase"] == "closed"
    assert seen == [("claim-status", result["ref"].split(":", 1)[1])]
    assert not tracking.load_record(path).resources[0].is_unsettled
    before = path.read_bytes()
    assert call(path, "close", {"token": result["token"]}) == closed
    assert path.read_bytes() == before and len(seen) == 1


def test_missing_actual_registry_and_failed_malformed_callbacks_block(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(claim_providers, "discover_claim_providers", lambda *a: ({}, ()))
    assert call(path, "close", {"token": pending["token"]})["error"]
    manifest = claim_providers.ClaimProviderManifest("raw-resource", "test@test", "/unused", ("/unused/provider",))
    monkeypatch.setattr(claim_providers, "discover_claim_providers", lambda *a: ({"raw-resource": manifest}, ()))
    for proc in (None, argparse.Namespace(returncode=1, stdout="{}"),
                 argparse.Namespace(returncode=0, stdout="[]"),
                 argparse.Namespace(returncode=0, stdout='{"exists":"false"}')):
        monkeypatch.setattr(claim_providers, "_run_provider_process", lambda *a, **k: proc)
        assert call(path, "close", {"token": pending["token"]})["error"]
    assert tracking.load_record(path).resources[0].is_unsettled


def test_all_generic_bypasses_cannot_discharge_or_erase_producer(owner):
    path, _ = owner
    pending = reserve(path)
    ref = pending["ref"]
    record = tracking.load_record(path)
    with pytest.raises(ValueError, match="overwrite"):
        tracking.add_resource_claim(record, tracking.ResourceClaim(kind="container", ref=ref, state="released"), save=False)
    for remove in (False, True):
        assert tracking_claim_write.apply_claim_release({"worktree_id": "owner", "yaml_path": str(path), "ref": ref, "remove": remove})["error"]
    for disposition in ("released", "at-rest", "abandoned"):
        assert tracking_claim_write.apply_claim_settle({"worktree_id": "owner", "yaml_path": str(path), "ref": ref, "disposition": disposition, "skip_if_released": True})["error"]
        with pytest.raises(ValueError):
            tracking.settle_resource_claim(record, ref, disposition, save=False)
    with pytest.raises(ValueError):
        tracking.release_resource_claim(record, ref, save=False)
    assert tracking.sweep_abandoned_obligations(record, gone_of=lambda c: True, safe_of=lambda c: True, save=False) == []
    assert tracking.release_all_resources(record, save=False) == []
    with pytest.raises(ValueError, match="orphan"):
        tracking.rehome_abandoned_obligations(
            record.resources, source_worktree="owner", config=owner[1], handoff_to="recipient",
        )
    record.resources[0].state = "at-rest"
    assert tracking.release_at_rest_resources(record, save=False) == []
    # Generic disconnect/repair saves, including reservation repair's opt-out,
    # cannot drop metadata or forge a terminal phase.
    record.resources[0].producer = {**record.resources[0].producer, "phase": "closed", "closeout": absence(ref)}
    tracking.save_record(record, path, preserve_handoff_reservations=False)
    assert tracking.load_record(path).resources[0].producer["phase"] == "pending"
    record = tracking.load_record(path)
    record.resources = []
    tracking.save_record(record, path, preserve_handoff_reservations=False)
    assert tracking.load_record(path).resources[0].is_unsettled


def test_legacy_fleet_return_sweep_and_cascade_are_unchanged(owner):
    path, _ = owner
    record = tracking.load_record(path)
    claim = tracking.ResourceClaim(kind="container", ref="container:fleet")
    tracking.add_resource_claim(record, claim, save=False)
    assert tracking.settle_resource_claim(record, claim.ref, save=False).is_at_rest
    assert tracking.release_at_rest_resources(record, save=False) == [claim]
    claim.state = "active"
    assert tracking.release_all_resources(record, save=False) == [claim]
    claim.state = "active"
    assert tracking.sweep_abandoned_obligations(record, gone_of=lambda c: True, safe_of=lambda c: True, save=False) == [claim]
    assert claim.is_abandoned


def test_pending_handoff_refused_bound_accept_preserves_creator(owner):
    path, _ = owner
    pending = reserve(path)
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="bound"):
        claim_handoffs.offer("m/p/owner", "m/p/consumer", [pending["ref"]], machine="m")
    bound = bind(path, pending)
    bundle, _ = claim_handoffs.offer("m/p/owner", "m/p/consumer", [bound["ref"]], machine="m")
    assert bundle.claims[0]["producer"] == bound["producer"]
    accepted = claim_handoffs.accept(bundle.bundle_id, actor="m/p/consumer", machine="m")
    assert accepted.state == "accepted"
    source = tracking.load_record(path).resources[0]
    recipient = tracking.load_record(path.with_name("consumer.yaml")).resources[0]
    assert source.producer["phase"] == "transferred" and not source.is_unsettled
    assert source.producer["transfer"]["accepted"] is True
    assert recipient.producer == bound["producer"] and recipient.is_unsettled
    assert recipient.producer["creator"] == "m/p/owner"
    assert recipient.handoff_bundle == ""
    assert claim_handoffs.accept(bundle.bundle_id, actor="m/p/consumer", machine="m").state == "accepted"
    inspected = call(path.with_name("consumer.yaml"), "inspect", {"token": bound["token"]}, ref="m/p/consumer")
    assert inspected["owner"] == "m/p/consumer" and inspected["creator"] == "m/p/owner"


@pytest.mark.parametrize("status", ["finalized", "finalizing", "orphaned", "archived", "complete", "completed"])
def test_accept_terminal_consumer_retains_source_and_both_records(owner, status):
    path, _ = owner
    bound = bind(path, reserve(path))
    bundle, _ = claim_handoffs.offer("m/p/owner", "m/p/consumer", [bound["ref"]], machine="m")
    consumer_path = path.with_name("consumer.yaml")
    consumer = tracking.load_record(consumer_path)
    consumer.status = status
    tracking.save_record(consumer, consumer_path)
    before = {target: target.read_bytes() for target in (path, consumer_path, claim_handoffs.registry_path())}
    with pytest.raises(claim_handoffs.ClaimHandoffError, match=status):
        claim_handoffs.accept(bundle.bundle_id, actor="m/p/consumer", machine="m")
    assert all(target.read_bytes() == content for target, content in before.items())
    retained = tracking.load_record(path).resources[0]
    assert retained.is_unsettled and retained.producer == bound["producer"]
    assert retained.handoff_bundle == bundle.bundle_id


def test_accept_consumer_persistence_failure_keeps_source_obligation(owner, monkeypatch):
    path, _ = owner
    bound = bind(path, reserve(path))
    bundle, _ = claim_handoffs.offer("m/p/owner", "m/p/consumer", [bound["ref"]], machine="m")
    original_save = tracking.save_record
    def save(record, target=None, **kwargs):
        if target == path.with_name("consumer.yaml"):
            raise OSError("injected recipient persistence failure")
        return original_save(record, target, **kwargs)
    monkeypatch.setattr(tracking, "save_record", save)
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="persistence failure"):
        claim_handoffs.accept(bundle.bundle_id, actor="m/p/consumer", machine="m")
    assert tracking.load_record(path).resources[0].is_unsettled
    monkeypatch.setattr(tracking, "save_record", original_save)
    assert claim_handoffs.accept(bundle.bundle_id, actor="m/p/consumer", machine="m").state == "accepted"


def test_capabilities_and_exact_stdin_cli(owner, monkeypatch, capsys):
    path, _ = owner
    args = argparse.Namespace(operation="capabilities", owner_ref=None, json=True)
    emitted = []
    monkeypatch.setattr(cli.output, "_json_output", emitted.append)
    monkeypatch.setattr(cfg, "load_config", lambda: pytest.fail("capabilities resolved an owner"))
    assert cli.cmd_resource_producer(args) == 0
    assert emitted.pop()["schema"] == producer.SCHEMA
    # Input errors must be rejected before config/ledger resolution.
    args.operation = "reserve"
    for raw in ("[]", "{} {}", '{"kind":"a","kind":"b"}', "{}\nnull"):
        monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(raw.encode("utf-8"))))
        assert cli.cmd_resource_producer(args) == 1
        assert emitted.pop()["error"]
    assert "resource_producer" in tracking_write.registered_verbs()


def test_real_main_capabilities_and_reserve_use_owning_project_without_cluster(owner, monkeypatch):
    from agent_worktrees import __main__ as main, claims_cli, state_root

    path, _ = owner
    emitted = []
    monkeypatch.setattr(cli.output, "_json_output", emitted.append)
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: pytest.fail("producer booted the full CLI cluster"))
    monkeypatch.setattr(cli.worktree_ops_cli, "_resolve_owner_ref", lambda: "m/p/owner")
    monkeypatch.setattr(
        claims_cli, "_coordination_readiness_for_owner_ref",
        lambda *a: state_root.CoordinationReadiness(True, "ready", state_root.StateRoot(str(path.parent), "launch_repo", "p", False, False, True)),
    )
    assert main.main(["resource-producer", "capabilities", "--json"]) == 0
    assert emitted.pop()["schema"] == producer.SCHEMA
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"kind":"container","namespace":"raw-resource","recovery":{}}')))
    assert main.main(["resource-producer", "reserve", "--json", "--owner-ref", "m/p/owner"]) == 0
    result = emitted.pop()
    assert result["phase"] == "pending" and tracking.load_record(path).resources[0].is_unsettled
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps({"token": result["token"]}).encode("utf-8"))))
    assert main.main(["resource-producer", "inspect", "--json"]) == 0
    assert emitted.pop()["token"] == result["token"]


def test_status_probe_subject_changed_by_concurrent_bind_cannot_close(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    def probe(ref):
        bind(path, pending)
        return absence(ref)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", probe)
    assert call(path, "close", {"token": pending["token"]})["error"]
    claim = tracking.load_record(path).resources[0]
    assert claim.producer["phase"] == "bound" and claim.is_unsettled


def test_real_disconnect_and_handoff_session_repair_retain_producer(owner, monkeypatch):
    from agent_worktrees import handoff_cutover, tracking_session_deregistration_write

    path, _ = owner
    pending = reserve(path)
    record = tracking.load_record(path)
    record.sessions = [tracking.SessionEntry(session_id="session", started_at="2026-10-01T00:00:00")]
    session_ref = tracking.format_claim_ref("m", "p", "owner", session="session")
    tracking.add_resource_claim(record, tracking.ResourceClaim(kind="session", ref=session_ref), save=False)
    tracking.save_record(record, path)
    monkeypatch.setattr(tracking_session_deregistration_write, "stop_fsmonitor_daemon", lambda *a: None)
    assert tracking_session_deregistration_write.apply_session_deregister({
        "worktree_id": "owner", "yaml_path": str(path), "session_id": "session",
    }) == {"ok": True}
    assert handoff_cutover._settle_predecessor_session_claim("owner", "session")["ok"]
    current = tracking.load_record(path)
    producer_claim = next(c for c in current.resources if c.has_producer)
    assert producer_claim.producer == pending["producer"] and producer_claim.is_unsettled
    assert next(c for c in current.resources if not c.has_producer).state == "released"


def test_reserve_token_collision_refuses_without_replacement(owner, monkeypatch):
    from agent_worktrees import tracking_producer_write

    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(tracking_producer_write.secrets, "token_urlsafe", lambda n: pending["token"])
    before = path.read_bytes()
    assert call(path, "reserve", {"kind": "container", "namespace": "other-namespace", "recovery": {}})["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("metadata", [None, {"version": 99, "token": ["malformed"]}])
def test_actual_malformed_record_resave_preserves_raw_claim(owner, metadata):
    import yaml

    path, _ = owner
    raw = yaml.safe_load(path.read_text())
    raw["resources"] = [{"kind": "container", "ref": "bad", "state": "released", "producer": metadata}]
    path.write_text(yaml.safe_dump(raw))
    record = tracking.load_record(path)
    tracking.save_record(record, path)
    current = tracking.load_record(path).resources[0]
    assert current.producer == metadata and current.has_producer and current.is_unsettled


def test_oversize_callback_receipt_is_not_success_shaped(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: {**absence(ref), "detail": "x" * 20000})
    before = path.read_bytes()
    assert call(path, "close", {"token": pending["token"]})["error"]
    assert path.read_bytes() == before and tracking.load_record(path).resources[0].is_unsettled


@pytest.mark.parametrize("change", [
    {"version": 2}, {"phase": "unknown"}, {"recovery": []},
    {"namespace": "--apply"}, {"identity": "not-object"}, {"unknown": True},
])
def test_invalid_metadata_fields_remain_blocking(owner, change):
    path, _ = owner
    result = reserve(path)
    malformed = {**copy.deepcopy(result["producer"]), **change}
    assert not producer.valid_metadata(malformed)
    claim = tracking._parse_claim_mapping({"kind": "container", "ref": result["ref"], "state": "released", "producer": malformed})
    assert claim.is_unsettled
