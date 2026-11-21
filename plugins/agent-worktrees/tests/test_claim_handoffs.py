"""Affirmative claim-bundle handoff Phase 1."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import types
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import activity, claim_handoffs, config as cfg, finalize, state_root, tracking
from agent_worktrees import tracking_claims, tracking_lifecycle
from agent_worktrees.tracking_claims import ResourceClaim
from agent_worktrees import worktree_identity


MACHINE = "anomalous-potato"
SOURCE = f"{MACHINE}/source-project/wt-source"
CONSUMER = f"{MACHINE}/consumer-project/wt-consumer"


def _record(
    tmp_path: Path, project: str, worktree_id: str, *,
    claims: Iterable[ResourceClaim] = (),
) -> Path:
    tdir = tmp_path / project / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    wdir = tmp_path / "trees" / worktree_id
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / ".agent-worktrees").mkdir(exist_ok=True)
    (wdir / ".agent-worktrees" / "machines.yaml").write_text(
        "machines: {}\n", encoding="utf-8",
    )
    (tmp_path / project / "config.yaml").write_text(json.dumps({
        "repo_name": project, "machine": MACHINE, "platform": "windows",
        "repos": {project: {"anchor": str(wdir), "worktree_root": str(wdir.parent)}},
    }), encoding="utf-8")
    record = tracking_lifecycle.create_new_record(
        worktree_id,
        f"worktree/{worktree_id}",
        str(wdir),
        project,
        MACHINE,
        "windows",
        tdir,
    )
    record.resources = list(claims)
    tracking.save_record(record, tdir / f"{worktree_id}.yaml")
    return tdir / f"{worktree_id}.yaml"


@pytest.fixture
def handoff_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[ResourceClaim]:
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(socket, "gethostname", lambda: MACHINE)
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda anchor: {})
    monkeypatch.setattr(cfg, "install_dir", lambda: runtime)
    monkeypatch.setattr(
        cfg, "project_dir", lambda name=None: tmp_path / str(name)
    )
    load_config = cfg.load_config
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda name, **kwargs: load_config(tmp_path / name / "config.yaml", project=name, **kwargs),
    )
    ready_root = state_root.StateRoot(
        str(tmp_path), "launch_repo", "source-project", False, False, True)
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(
            True, "ready", ready_root),
    )
    claims = [
        ResourceClaim(
            kind="worktree",
            ref=f"{MACHINE}/child-project/wt-child",
            created_at="2026-08-25T12:00:00",
            state="active",
            note="child",
        ),
        ResourceClaim(
            kind="pr",
            ref="ThomasMichon/example#42",
            created_at="2026-08-25T12:01:00",
            state="active",
        ),
    ]
    _record(tmp_path, "source-project", "wt-source", claims=claims)
    _record(tmp_path, "consumer-project", "wt-consumer")
    return claims


def _offer(refs: list[str], *, bundle_id: str = "bundle-1") -> tuple[claim_handoffs.ClaimBundle, bool]:
    return claim_handoffs.offer(
        SOURCE,
        CONSUMER,
        refs,
        machine=MACHINE,
        id_factory=lambda: bundle_id,
    )


def test_offer_snapshots_claims_without_mutating_source(handoff_state: list[ResourceClaim]) -> None:
    refs = [claim.ref for claim in handoff_state]
    bundle, created = _offer(refs)
    assert created is True
    assert bundle.state == "offered"
    assert bundle.source == SOURCE and bundle.consumer == CONSUMER
    assert [claim["ref"] for claim in bundle.claims] == sorted(refs)
    source = tracking.load_record(
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    assert [claim.state for claim in source.resources] == ["active", "active"]
    assert [claim.handoff_bundle for claim in source.resources] == [
        bundle.bundle_id, bundle.bundle_id]


@pytest.mark.parametrize("legacy_offer", [False, True])
def test_unfinished_paired_worktree_cannot_be_transferred(
    handoff_state: list[ResourceClaim], tmp_path: Path, legacy_offer: bool,
) -> None:
    ref = handoff_state[0].ref
    bundle = _offer([ref])[0] if legacy_offer else None
    source_path = cfg.project_dir("source-project") / "worktrees" / "wt-source.yaml"
    source = tracking.load_record(source_path)
    source.pair_id, source.pair_role, source.pair_kind = "pair", "harness", "worktree"
    source.pair_ref = ref
    tracking.save_record(source, source_path)
    child_path = _record(tmp_path, "child-project", "wt-child")
    child = tracking.load_record(child_path)
    child.pair_id, child.pair_role, child.pair_kind = "pair", "knowledge", "worktree"
    child.pair_ref = SOURCE
    tracking.save_record(child, child_path)
    before = source_path.read_bytes()

    with pytest.raises(claim_handoffs.ClaimHandoffError, match="paired.*finaliz"):
        if bundle is None:
            _offer([ref])
        else:
            claim_handoffs.accept_source(bundle.bundle_id, actor=CONSUMER)
    assert source_path.read_bytes() == before
    if bundle is not None:
        assert claim_handoffs.show(bundle.bundle_id).state == "offered"


def test_identical_offer_is_idempotent(handoff_state: list[ResourceClaim]) -> None:
    refs = [handoff_state[0].ref]
    first, created = _offer(refs)
    second, created_again = _offer(refs, bundle_id="different")
    assert created is True and created_again is False
    assert second.bundle_id == first.bundle_id


def test_overlapping_offer_is_rejected(handoff_state: list[ResourceClaim]) -> None:
    refs = [claim.ref for claim in handoff_state]
    _offer([refs[0]])
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="already belong"):
        _offer(refs, bundle_id="bundle-2")


def test_offer_rejects_missing_inactive_and_cross_machine(handoff_state: list[ResourceClaim]) -> None:
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="does not own"):
        _offer(["missing"])
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    source = tracking.load_record(source_path)
    source.resources[0].state = "at-rest"
    tracking.save_record(source, source_path)
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="finalize-blocking"):
        _offer([source.resources[0].ref])
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cross-machine"):
        claim_handoffs.offer(
            SOURCE,
            "other/consumer-project/wt-consumer",
            [source.resources[1].ref],
            machine=MACHINE,
        )


def test_finalized_source_and_consumer_may_still_offer(handoff_state: list[ResourceClaim]) -> None:
    """``finalized`` is not terminal -- a resumed source/consumer worktree may
    still participate in a claim handoff (docs/worktree-lifecycle.md)."""
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    source = tracking.load_record(source_path)
    source.status = "finalized"
    tracking.save_record(source, source_path)
    consumer_path = (
        cfg.project_dir("consumer-project")
        / "worktrees"
        / "wt-consumer.yaml"
    )
    consumer = tracking.load_record(consumer_path)
    consumer.status = "finalized"
    tracking.save_record(consumer, consumer_path)
    refs = [handoff_state[0].ref]
    bundle, created = _offer(refs)
    assert created is True
    assert bundle.source == SOURCE and bundle.consumer == CONSUMER


def test_decline_and_cancel_are_actor_checked_and_idempotent(handoff_state: list[ResourceClaim]) -> None:
    ref = handoff_state[0].ref
    declined = _offer([ref])[0]
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="only bundle consumer"):
        claim_handoffs.transition(
            declined.bundle_id, actor=SOURCE, action="declined", reason="no"
        )
    first = claim_handoffs.transition(
        declined.bundle_id, actor=CONSUMER, action="declined", reason="not mine"
    )
    retry = claim_handoffs.transition(
        declined.bundle_id, actor=CONSUMER, action="declined", reason="retry"
    )
    assert first.state == "declined" and retry.reason == "not mine"
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    assert tracking.load_record(source_path).resources[0].handoff_bundle == ""
    cancelled = _offer([handoff_state[1].ref], bundle_id="bundle-2")[0]
    result = claim_handoffs.transition(
        cancelled.bundle_id, actor=SOURCE, action="cancelled", reason="superseded"
    )
    assert result.state == "cancelled" and result.reason == "superseded"
    assert tracking.load_record(source_path).resources[1].handoff_bundle == ""


def test_terminal_transition_cannot_be_rewritten(handoff_state: list[ResourceClaim]) -> None:
    bundle = _offer([handoff_state[0].ref])[0]
    claim_handoffs.transition(
        bundle.bundle_id, actor=SOURCE, action="cancelled", reason="stop"
    )
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="already cancelled"):
        claim_handoffs.transition(
            bundle.bundle_id,
            actor=CONSUMER,
            action="declined",
            reason="too late",
        )


def test_corrupt_registry_fails_closed_without_overwrite(handoff_state: list[ResourceClaim]) -> None:
    path = claim_handoffs.registry_path()
    path.parent.mkdir(parents=True)
    path.write_text("version: 1\nbundles: [", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cannot read"):
        _offer([handoff_state[0].ref])
    assert path.read_text(encoding="utf-8") == before


def test_failed_atomic_write_leaves_no_success(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tracking,
        "_atomic_write",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cannot write"):
        _offer([handoff_state[0].ref])
    assert not claim_handoffs.registry_path().exists()
    source = tracking.load_record(
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    assert source.resources[0].handoff_bundle == ""


def test_interrupted_offer_replays_to_offered(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = handoff_state[0].ref
    original = claim_handoffs._save_registry
    calls = {"count": 0}

    def fail_second(path: Path, bundles: list[claim_handoffs.ClaimBundle]) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("crash before offered commit")
        return original(path, bundles)

    monkeypatch.setattr(claim_handoffs, "_save_registry", fail_second)
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cannot offer"):
        _offer([ref])
    monkeypatch.setattr(claim_handoffs, "_save_registry", original)
    recovered, created = _offer([ref], bundle_id="ignored")
    assert created is False and recovered.state == "offered"
    source = tracking.load_record(
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    assert source.resources[0].handoff_bundle == recovered.bundle_id


def test_interrupted_decline_replays_to_terminal(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _offer([handoff_state[0].ref])[0]
    original = claim_handoffs._save_registry
    calls = {"count": 0}

    def fail_second(path: Path, bundles: list[claim_handoffs.ClaimBundle]) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("crash before declined commit")
        return original(path, bundles)

    monkeypatch.setattr(claim_handoffs, "_save_registry", fail_second)
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cannot transition"):
        claim_handoffs.transition(
            bundle.bundle_id,
            actor=CONSUMER,
            action="declined",
            reason="busy",
        )
    monkeypatch.setattr(claim_handoffs, "_save_registry", original)
    recovered = claim_handoffs.transition(
        bundle.bundle_id,
        actor=CONSUMER,
        action="declined",
        reason="retry",
    )
    assert recovered.state == "declined" and recovered.reason == "busy"


def test_offered_claim_cannot_settle_sweep_or_release(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    ref = handoff_state[0].ref
    bundle = _offer([ref])[0]
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    source = tracking.load_record(source_path)
    assert tracking_claims.settle_resource_claim(
        source, ref, "at-rest", save=False) is None
    reclaimed = tracking_claims.sweep_abandoned_obligations(
        source,
        gone_of=lambda claim: True,
        safe_of=lambda claim: True,
        save=False,
    )
    assert all(claim.ref != ref for claim in reclaimed)
    config = types.SimpleNamespace(machine=MACHINE, repo_name="source-project")
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: source_path.parent)
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-source")
    args = argparse.Namespace(
        release_worktree=None, remove=False, json=True)
    assert m._claims_release(args, ref) == 1
    assert bundle.bundle_id in json.loads(capfd.readouterr().out)["error"]
    source = tracking.load_record(source_path)
    assert source.resources[0].state == "active"
    assert source.resources[0].handoff_bundle == bundle.bundle_id


def test_unrelated_stale_record_save_preserves_reserved_claim(handoff_state: list[ResourceClaim]) -> None:
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    stale = tracking.load_record(source_path)
    bundle = _offer([handoff_state[0].ref])[0]
    stale.resources[0].state = "released"
    stale.resources[0].note = "stale writer"
    tracking.save_record(stale, source_path)
    fresh = tracking.load_record(source_path)
    assert fresh.resources[0].state == "active"
    assert fresh.resources[0].note == "child"
    assert fresh.resources[0].handoff_bundle == bundle.bundle_id


def test_stale_save_cannot_resurrect_cleared_reservation(handoff_state: list[ResourceClaim]) -> None:
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    bundle = _offer([handoff_state[0].ref])[0]
    stale = tracking.load_record(source_path)
    claim_handoffs.transition(
        bundle.bundle_id,
        actor=CONSUMER,
        action="declined",
        reason="busy",
    )
    tracking.save_record(stale, source_path)
    fresh = tracking.load_record(source_path)
    assert fresh.resources[0].handoff_bundle == ""


def test_nonterminal_registry_blocks_finalize_without_reservation(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = handoff_state[0].ref
    original = claim_handoffs._save_registry
    calls = {"count": 0}

    def fail_source_phase(path: Path, bundles: list[claim_handoffs.ClaimBundle]) -> None:
        calls["count"] += 1
        result = original(path, bundles)
        if calls["count"] == 1:
            raise OSError("stop after offering intent")
        return result

    monkeypatch.setattr(claim_handoffs, "_save_registry", fail_source_phase)
    with pytest.raises(claim_handoffs.ClaimHandoffError):
        _offer([ref])
    monkeypatch.setattr(claim_handoffs, "_save_registry", original)
    source = tracking.load_record(
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    assert source.resources[0].handoff_bundle == ""
    assert tracking_claims.claim_handoff_reservation(
        source, source.resources[0]) == "bundle-1"
    assert tracking_claims.settle_resource_claim(
        source, ref, "at-rest", save=False) is None
    assert finalize._assert_obligations_settled(
        source, source.worktree_id, abandon=False) is False


def test_source_can_cancel_incomplete_offer_after_record_disappears(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = handoff_state[0].ref
    original = claim_handoffs._save_registry
    calls = {"count": 0}

    def fail_second(path: Path, bundles: list[claim_handoffs.ClaimBundle]) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("stop with offering intent")
        return original(path, bundles)

    monkeypatch.setattr(claim_handoffs, "_save_registry", fail_second)
    with pytest.raises(claim_handoffs.ClaimHandoffError):
        _offer([ref])
    monkeypatch.setattr(claim_handoffs, "_save_registry", original)
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    source_path.unlink()
    bundle = claim_handoffs.show("bundle-1")
    cancelled = claim_handoffs.transition(
        bundle.bundle_id,
        actor=SOURCE,
        action="cancelled",
        reason="source disappeared",
    )
    assert cancelled.state == "cancelled"


def test_source_can_cancel_incomplete_offer_after_claim_drift(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = handoff_state[0].ref
    original = claim_handoffs._save_registry
    calls = {"count": 0}

    def fail_first_after_write(path: Path, bundles: list[claim_handoffs.ClaimBundle]) -> None:
        calls["count"] += 1
        result = original(path, bundles)
        if calls["count"] == 1:
            raise OSError("stop after offering intent")
        return result

    monkeypatch.setattr(
        claim_handoffs, "_save_registry", fail_first_after_write)
    with pytest.raises(claim_handoffs.ClaimHandoffError):
        _offer([ref])
    monkeypatch.setattr(claim_handoffs, "_save_registry", original)
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    source = tracking.load_record(source_path)
    source.resources = [
        claim for claim in source.resources if claim.ref != ref]
    tracking.save_record(
        source, source_path, preserve_handoff_reservations=False)
    cancelled = claim_handoffs.transition(
        "bundle-1",
        actor=SOURCE,
        action="cancelled",
        reason="repair incomplete offer",
    )
    assert cancelled.state == "cancelled"


def test_concurrent_identical_offers_create_one_bundle(handoff_state: list[ResourceClaim]) -> None:
    results: list[tuple[claim_handoffs.ClaimBundle, bool]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def run(index: int) -> None:
        try:
            barrier.wait()
            results.append(_offer(
                [handoff_state[0].ref], bundle_id=f"bundle-{index}"
            ))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sum(1 for _, created in results if created) == 1
    assert len({bundle.bundle_id for bundle, _ in results}) == 1


def test_parser_accepts_claim_handoff_surface() -> None:
    parser = m.build_parser()
    offered = parser.parse_args([
        "claims",
        "handoff",
        "offer",
        "--to",
        CONSUMER,
        "child-ref",
        "--json",
    ])
    assert offered.target == ["handoff", "offer"]
    assert offered.handoff_to == [CONSUMER, "child-ref"] and offered.json is True
    accepted = parser.parse_args([
        "claims", "handoff", "accept", "bundle-1"
    ])
    assert accepted.target == ["handoff", "accept", "bundle-1"]
    declined = parser.parse_args([
        "claims", "handoff", "decline", "bundle-1", "--reason", "busy"
    ])
    assert declined.reason == "busy"


def _args(target: list[str], **kwargs: Any) -> argparse.Namespace:
    values = {
        "target": target,
        "json": True,
        "release_worktree": None,
        "handoff_to": None,
        "reason": "",
    }
    values.update(kwargs)
    return argparse.Namespace(**values)


def test_cli_offer_rejects_unready_coordination_without_side_effects(
    handoff_state: list[ResourceClaim],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    config = types.SimpleNamespace(machine=MACHINE, repo_name="source-project")
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-source")
    root = state_root.StateRoot(
        None,
        "knowledge_repo",
        "",
        True,
        True,
        False,
        error="no knowledge_repo is bound",
    )
    readiness = state_root.CoordinationReadiness(
        False,
        "knowledge_binding_required",
        root,
        error="Bind the knowledge repository and retry the same operation.",
    )
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: readiness,
    )
    source_path = (
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    before = source_path.read_bytes()

    rc = m.cmd_claims(_args(
        ["handoff", "offer", handoff_state[0].ref],
        handoff_to=[CONSUMER],
    ))
    assert rc == 3
    assert json.loads(capfd.readouterr().out)["code"] == (
        "knowledge_binding_required"
    )
    assert source_path.read_bytes() == before
    assert not claim_handoffs.registry_path().exists()


def test_cli_decline_remains_available_when_coordination_is_unready(
    handoff_state: list[ResourceClaim],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    bundle = _offer([handoff_state[0].ref])[0]
    config = types.SimpleNamespace(machine=MACHINE, repo_name="consumer-project")
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-consumer")
    monkeypatch.setattr(
        state_root,
        "coordination_readiness",
        lambda config: pytest.fail("decline ran coordination readiness"),
    )

    rc = m.cmd_claims(_args(
        ["handoff", "decline", bundle.bundle_id],
        reason="binding unavailable",
    ))
    assert rc == 0
    assert json.loads(capfd.readouterr().out)["state"] == "declined"


def test_cli_offer_show_decline_cancel(
    handoff_state: list[ResourceClaim], monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    config = types.SimpleNamespace(machine=MACHINE, repo_name="source-project")
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-source")
    ref = handoff_state[0].ref
    logged: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(activity, "log_event", lambda *a, **k: logged.append((a, k)))
    assert m.cmd_claims(_args(
        ["handoff", "offer", ref], handoff_to=[CONSUMER]
    )) == 0
    offered = json.loads(capfd.readouterr().out)
    bundle_id = offered["id"]
    assert offered["state"] == "offered" and offered["created"] is True
    assert logged[-1] == (("claim_handoff_offered",), {
        "worktree_id": SOURCE, "bundle_id": bundle_id, "consumer": CONSUMER,
        "refs": [ref], "created": True})
    assert m.cmd_claims(_args(["handoff", "show", bundle_id])) == 0
    assert json.loads(capfd.readouterr().out)["id"] == bundle_id
    assert len(logged) == 1  # show is read-only -- never logs a mutation
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-consumer")
    config.repo_name = "consumer-project"
    assert m.cmd_claims(_args(
        ["handoff", "decline", bundle_id], reason="busy"
    )) == 0
    assert json.loads(capfd.readouterr().out)["state"] == "declined"
    assert logged[-1] == (("claim_handoff_declined",), {
        "worktree_id": SOURCE, "bundle_id": bundle_id, "consumer": CONSUMER,
        "reason": "busy"})
    source = tracking.load_record(
        cfg.project_dir("source-project")
        / "worktrees"
        / "wt-source.yaml"
    )
    assert source.resources[0].state == "active"

    config.repo_name = "source-project"
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-source")
    assert m.cmd_claims(_args(
        ["handoff", "offer", handoff_state[1].ref],
        handoff_to=[CONSUMER],
    )) == 0
    second_id = json.loads(capfd.readouterr().out)["id"]
    assert m.cmd_claims(_args(
        ["handoff", "cancel", second_id], reason="superseded"
    )) == 0
    assert json.loads(capfd.readouterr().out)["state"] == "cancelled"
    assert logged[-1] == (("claim_handoff_cancelled",), {
        "worktree_id": SOURCE, "bundle_id": second_id, "consumer": CONSUMER,
        "reason": "superseded"})


def test_cli_reports_invalid_action_as_json(
    handoff_state: list[ResourceClaim], capfd: pytest.CaptureFixture[str],
) -> None:
    assert m.cmd_claims(_args(["handoff", "adopt", "bundle-1"])) == 1
    assert "unknown action" in json.loads(capfd.readouterr().out)["error"]
