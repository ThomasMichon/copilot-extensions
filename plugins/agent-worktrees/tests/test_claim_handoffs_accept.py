"""Acceptance tests for affirmative claim-bundle handoff."""

from __future__ import annotations

import argparse
import json
import subprocess
import types
from pathlib import Path

import pytest
from agent_worktrees import __main__ as m
from agent_worktrees import (
    claim_handoff_accept_support,
    claim_handoffs,
    claim_history,
    finalize,
    tracking,
    tracking_claim_write,
)
from agent_worktrees import worktree_identity
from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_store import GitLeaseStore

MACHINE = "anomalous-potato"


def _record(
    tmp_path: Path,
    project: str,
    worktree_id: str,
    *,
    machine: str = MACHINE,
    owner_ref: str | None = None,
    claims=(),
):
    tdir = tmp_path / project / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    wdir = tmp_path / "trees" / project / worktree_id
    wdir.mkdir(parents=True, exist_ok=True)
    record = tracking.create_new_record(
        worktree_id,
        f"worktree/{worktree_id}",
        str(wdir),
        project,
        machine,
        "linux",
        tdir,
        owner_ref=owner_ref,
    )
    record.resources = list(claims)
    tracking.save_record(record, tdir / f"{worktree_id}.yaml")
    return tdir / f"{worktree_id}.yaml"


@pytest.fixture
def handoff_world(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(claim_handoffs.cfg, "install_dir", lambda: runtime)
    monkeypatch.setattr(
        claim_handoffs.cfg, "project_dir", lambda name=None: tmp_path / str(name)
    )
    return {"tmp_path": tmp_path}


def _setup_bundle(
    handoff_world,
    *,
    source_machine: str = MACHINE,
    consumer_machine: str = MACHINE,
    source_project: str = "source-project",
    consumer_project: str = "consumer-project",
    include_codespace: bool = False,
    include_unsupported: bool = False,
):
    tmp_path = handoff_world["tmp_path"]
    source = f"{source_machine}/{source_project}/wt-source"
    consumer = f"{consumer_machine}/{consumer_project}/wt-consumer"
    child_ref = f"{consumer_machine}/child-project/wt-child"
    claims = [
        tracking.ResourceClaim(
            kind="worktree",
            ref=child_ref,
            created_at="2026-08-25T12:00:00",
            state="active",
            note="child",
        ),
        tracking.ResourceClaim(
            kind="pr",
            ref="ThomasMichon/example#42",
            created_at="2026-08-25T12:01:00",
            state="active",
            note="tracking pr",
        ),
    ]
    if include_codespace:
        claims.append(
            tracking.ResourceClaim(
                kind="codespace",
                ref="octo-space",
                created_at="2026-08-25T12:02:00",
                state="active",
                note="lease-backed",
            )
        )
    if include_unsupported:
        claims.append(
            tracking.ResourceClaim(
                kind="ssh",
                ref="atlas-core",
                created_at="2026-08-25T12:03:00",
                state="active",
                note="unsupported",
            )
        )
    _record(
        tmp_path,
        source_project,
        "wt-source",
        machine=source_machine,
        claims=claims,
    )
    _record(
        tmp_path,
        consumer_project,
        "wt-consumer",
        machine=consumer_machine,
    )
    _record(
        tmp_path,
        "child-project",
        "wt-child",
        machine=consumer_machine,
        owner_ref=source,
    )
    return {
        "source": source,
        "consumer": consumer,
        "claims": claims,
        "source_path": tmp_path / source_project / "worktrees" / "wt-source.yaml",
        "consumer_path": tmp_path / consumer_project / "worktrees" / "wt-consumer.yaml",
        "child_path": tmp_path / "child-project" / "worktrees" / "wt-child.yaml",
    }


def _offer(world, refs, *, bundle_id="bundle-1"):
    return claim_handoffs.offer(
        world["source"],
        world["consumer"],
        refs,
        machine=MACHINE,
        id_factory=lambda: bundle_id,
    )


def _offer_cross_machine(world, refs, *, bundle_id="bundle-1"):
    source_record = tracking.load_record(world["source_path"])
    by_ref = {claim.ref: claim for claim in source_record.resources}
    snapshots = []
    for ref in sorted(refs):
        claim = by_ref[ref]
        claim.handoff_bundle = bundle_id
        snapshots.append(claim_handoffs._claim_snapshot(claim))
    tracking.save_record(
        source_record,
        world["source_path"],
        preserve_handoff_reservations=False,
    )
    bundle = claim_handoffs.ClaimBundle(
        bundle_id=bundle_id,
        state="offered",
        source=world["source"],
        consumer=world["consumer"],
        claims=tuple(snapshots),
        offered_at="2026-08-25T12:05:00",
        updated_at="2026-08-25T12:05:00",
    )
    claim_handoffs._save_registry(claim_handoffs.registry_path(), [bundle])
    return bundle


def _args(target, **kwargs):
    values = {
        "target": target,
        "json": True,
        "release_worktree": None,
        "handoff_to": None,
        "reason": "",
    }
    values.update(kwargs)
    return argparse.Namespace(**values)


def _init_lease_store(tmp_path: Path, monkeypatch):
    origin = tmp_path / "lease-origin.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(origin)],
        check=True,
        capture_output=True,
        text=True,
    )
    settings = LeaseSettings(origin=str(origin))
    monkeypatch.setattr(claim_handoffs, "load_lease_settings", lambda: settings)
    monkeypatch.setattr(
        claim_handoff_accept_support, "load_lease_settings", lambda: settings
    )
    return GitLeaseStore(settings)


def test_accept_same_project_transfers_worktree_lease_and_ledger_claims(
    handoff_world, monkeypatch
):
    world = _setup_bundle(
        handoff_world,
        consumer_project="source-project",
        include_codespace=True,
    )
    store = _init_lease_store(handoff_world["tmp_path"], monkeypatch)
    store.acquire("codespace", "octo-space", world["source"])
    refs = [claim.ref for claim in world["claims"]]
    bundle = _offer(world, refs)[0]

    accepted = claim_handoffs.accept(
        bundle.bundle_id,
        actor=world["consumer"],
        machine=MACHINE,
    )

    assert accepted.state == "accepted"
    assert claim_handoffs.show(bundle.bundle_id).state == "accepted"
    source = tracking.load_record(world["source_path"])
    consumer = tracking.load_record(world["consumer_path"])
    child = tracking.load_record(world["child_path"])
    assert source.resources == []
    assert {claim.ref for claim in consumer.resources} == set(refs)
    assert all(claim.state == "active" for claim in consumer.resources)
    assert all("prior owner: " in claim.note for claim in consumer.resources)
    assert child.owner_ref == world["consumer"]
    assert store.inspect("codespace", "octo-space").record.holder == world["consumer"]


def test_accept_cross_project_worktree_transfer_rewrites_child_owner(handoff_world):
    world = _setup_bundle(handoff_world)
    bundle = _offer(world, [world["claims"][0].ref])[0]

    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    source = tracking.load_record(world["source_path"])
    consumer = tracking.load_record(world["consumer_path"])
    child = tracking.load_record(world["child_path"])
    assert [claim.ref for claim in source.resources] == [world["claims"][1].ref]
    assert [claim.ref for claim in consumer.resources] == [world["claims"][0].ref]
    assert child.owner_ref == world["consumer"]


def test_accept_is_idempotent(handoff_world):
    world = _setup_bundle(handoff_world)
    bundle = _offer(world, [world["claims"][1].ref])[0]

    first = claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)
    second = claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    assert first.state == "accepted"
    assert second.to_dict() == first.to_dict()
    source = tracking.load_record(world["source_path"])
    consumer = tracking.load_record(world["consumer_path"])
    assert {claim.ref for claim in source.resources} == {world["claims"][0].ref}
    assert {claim.ref for claim in consumer.resources} == {world["claims"][1].ref}


def test_accept_feeds_claim_history_for_pr_claim(handoff_world):
    """worktree-claims-transitive-finalization Phase 3b: an explicit
    claim-handoff bundle accept is a genuine ownership transfer -- the
    source worktree must get a "released" entry and the consumer worktree
    a "claimed" entry for the transferred `pr`-kind claim, each only after
    its own save confirms."""
    world = _setup_bundle(handoff_world)
    pr_ref = world["claims"][1].ref
    bundle = _offer(world, [pr_ref])[0]

    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    events = claim_history.history_for_ref(pr_ref)
    assert [e["event"] for e in events] == ["released", "claimed"]
    released, claimed = events
    assert released["worktree_id"] == "wt-source"
    assert bundle.bundle_id in released["note"] and world["consumer"] in released["note"]
    assert claimed["worktree_id"] == "wt-consumer"
    assert bundle.bundle_id in claimed["note"] and world["source"] in claimed["note"]


def test_accept_worktree_claim_does_not_feed_claim_history(handoff_world):
    """A `worktree`-kind claim transferred by the same bundle is outside
    claim_history's scope (`pr`-kind only) and must never feed an entry."""
    world = _setup_bundle(handoff_world)
    worktree_ref = world["claims"][0].ref
    bundle = _offer(world, [worktree_ref])[0]

    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    assert claim_history.history_for_ref(worktree_ref) == []


def test_accept_retry_does_not_duplicate_claim_history(handoff_world):
    """A retried/idempotent accept call on an already-accepted bundle must
    never double-record the transfer."""
    world = _setup_bundle(handoff_world)
    pr_ref = world["claims"][1].ref
    bundle = _offer(world, [pr_ref])[0]

    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)
    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    events = claim_history.history_for_ref(pr_ref)
    assert [e["event"] for e in events] == ["released", "claimed"]


def test_accept_records_claim_history_even_if_a_later_child_save_fails(
    handoff_world, monkeypatch,
):
    """A review finding on the first version of this wiring: the consumer
    claim is already durably saved before any child-ownership record save
    -- a later child save failure (e.g. disk/lock contention) must not
    permanently suppress the consumer's `claimed` event. On retry the ref
    is already present, so `new_refs` would be empty and the event would
    never re-fire if recording happened after the child saves instead."""
    world = _setup_bundle(handoff_world)
    worktree_ref = world["claims"][0].ref
    pr_ref = world["claims"][1].ref
    bundle = _offer(world, [worktree_ref, pr_ref])[0]

    real_save = tracking.save_record

    def _fail_on_child(record, path=None, **kwargs):
        if path is not None and str(path) == str(world["child_path"]):
            raise OSError("simulated child-record save failure")
        return real_save(record, path, **kwargs)

    monkeypatch.setattr(claim_handoffs.tracking, "save_record", _fail_on_child)

    with pytest.raises(claim_handoffs.ClaimHandoffError):
        claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    consumer = tracking.load_record(world["consumer_path"])
    assert pr_ref in {c.ref for c in consumer.resources}
    events = claim_history.history_for_ref(pr_ref)
    assert [e["event"] for e in events] == ["released", "claimed"]


def test_accept_moves_finalize_block_from_source_to_consumer(handoff_world, monkeypatch):
    world = _setup_bundle(handoff_world)
    refs = [claim.ref for claim in world["claims"]]
    bundle = _offer(world, refs)[0]
    source_before = tracking.load_record(world["source_path"])
    assert finalize._assert_obligations_settled(
        source_before, source_before.worktree_id, abandon=False
    ) is False

    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    source_after = tracking.load_record(world["source_path"])
    consumer_after = tracking.load_record(world["consumer_path"])
    assert finalize._assert_obligations_settled(
        source_after, source_after.worktree_id, abandon=False
    ) is True
    assert finalize._assert_obligations_settled(
        consumer_after, consumer_after.worktree_id, abandon=False
    ) is False


def test_accept_rejects_unsupported_kind_without_transferring_anything(handoff_world):
    world = _setup_bundle(handoff_world, include_unsupported=True)
    refs = [claim.ref for claim in world["claims"]]
    bundle = _offer(world, refs)[0]

    with pytest.raises(claim_handoffs.ClaimHandoffError, match="unsupported claim kinds"):
        claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    source = tracking.load_record(world["source_path"])
    consumer = tracking.load_record(world["consumer_path"])
    child = tracking.load_record(world["child_path"])
    assert {claim.ref for claim in source.resources} == set(refs)
    assert {claim.handoff_bundle for claim in source.resources} == {bundle.bundle_id}
    assert consumer.resources == []
    assert child.owner_ref == world["source"]
    assert claim_handoffs.show(bundle.bundle_id).state == "offered"


def test_accepted_bundle_rejects_decline_and_cancel(handoff_world):
    world = _setup_bundle(handoff_world)
    bundle = _offer(world, [world["claims"][1].ref])[0]
    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    with pytest.raises(claim_handoffs.ClaimHandoffError, match="already accepted"):
        claim_handoffs.transition(
            bundle.bundle_id,
            actor=world["consumer"],
            action="declined",
            reason="too late",
        )
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="already accepted"):
        claim_handoffs.transition(
            bundle.bundle_id,
            actor=world["source"],
            action="cancelled",
            reason="too late",
        )


def test_accept_cross_machine_runs_remote_source_leg_then_finishes_locally(
    handoff_world, monkeypatch
):
    remote_machine = "ember"
    world = _setup_bundle(
        handoff_world,
        source_machine=remote_machine,
        include_codespace=True,
    )
    store = _init_lease_store(handoff_world["tmp_path"], monkeypatch)
    store.acquire("codespace", "octo-space", world["source"])
    refs = [claim.ref for claim in world["claims"]]
    bundle = _offer_cross_machine(world, refs)
    seen = {}

    monkeypatch.setattr(
        claim_handoff_accept_support.claimant,
        "resolve_machine_ssh",
        lambda key: ("ember-wsl", "bash") if key == remote_machine else None,
    )
    real_run = subprocess.run

    def _fake_remote(argv, **kwargs):
        if argv[0] != "ssh":
            return real_run(argv, **kwargs)
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        accepted = claim_handoffs.accept_source(bundle.bundle_id, actor=world["consumer"])
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(accepted.to_dict()),
            stderr="",
        )

    monkeypatch.setattr(claim_handoff_accept_support.subprocess, "run", _fake_remote)

    accepted = claim_handoffs.accept(
        bundle.bundle_id,
        actor=world["consumer"],
        machine=MACHINE,
    )

    assert accepted.state == "accepted"
    assert seen["argv"][:4] == [
        "ssh", "-o", "BatchMode=yes", "-o",
    ]
    assert "ember-wsl" in seen["argv"]
    assert "accept-source" in seen["argv"][-1]
    assert " --actor " in seen["argv"][-1]
    assert claim_handoffs.show(bundle.bundle_id).state == "accepted"
    source = tracking.load_record(world["source_path"])
    consumer = tracking.load_record(world["consumer_path"])
    child = tracking.load_record(world["child_path"])
    assert source.resources == []
    assert {claim.ref for claim in consumer.resources} == set(refs)
    assert child.owner_ref == world["consumer"]
    assert store.inspect("codespace", "octo-space").record.holder == world["consumer"]
    pr_ref = next(c.ref for c in world["claims"] if c.kind == "pr")
    events = claim_history.history_for_ref(pr_ref)
    assert [e["event"] for e in events] == ["released", "claimed"]
    assert events[0]["machine"] == remote_machine
    assert events[1]["machine"] == MACHINE


def test_accept_cross_machine_remote_failure_leaves_state_unchanged_and_no_live_fence(
    handoff_world, monkeypatch
):
    world = _setup_bundle(handoff_world, source_machine="ember")
    store = _init_lease_store(handoff_world["tmp_path"], monkeypatch)
    bundle = _offer_cross_machine(world, [claim.ref for claim in world["claims"]])
    monkeypatch.setattr(
        claim_handoff_accept_support.claimant,
        "resolve_machine_ssh",
        lambda key: ("ember-wsl", "bash"),
    )
    real_run = subprocess.run
    monkeypatch.setattr(
        claim_handoff_accept_support.subprocess,
        "run",
        lambda argv, **kwargs: (
            subprocess.CompletedProcess(argv, 255, stdout="", stderr="ssh down")
            if argv[0] == "ssh"
            else real_run(argv, **kwargs)
        ),
    )

    with pytest.raises(
        claim_handoffs.ClaimHandoffError,
        match="source-side accept failed",
    ):
        claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    source = tracking.load_record(world["source_path"])
    consumer = tracking.load_record(world["consumer_path"])
    child = tracking.load_record(world["child_path"])
    assert {claim.ref for claim in source.resources} == {claim.ref for claim in world["claims"]}
    assert {claim.handoff_bundle for claim in source.resources} == {bundle.bundle_id}
    assert consumer.resources == []
    assert child.owner_ref == world["source"]
    assert claim_handoffs.show(bundle.bundle_id).state == "offered"
    fence = store.inspect("claim-handoff", bundle.bundle_id)
    assert fence is not None and fence.live is False


def test_accept_cross_machine_rejects_live_fence_conflict(handoff_world, monkeypatch):
    world = _setup_bundle(handoff_world, source_machine="ember")
    store = _init_lease_store(handoff_world["tmp_path"], monkeypatch)
    bundle = _offer_cross_machine(world, [world["claims"][0].ref])
    store.acquire("claim-handoff", bundle.bundle_id, "other/project/wt-other")
    real_run = subprocess.run
    monkeypatch.setattr(
        claim_handoff_accept_support.subprocess,
        "run",
        lambda argv, **kwargs: (
            pytest.fail("remote SSH should not run")
            if argv[0] == "ssh"
            else real_run(argv, **kwargs)
        ),
    )

    with pytest.raises(
        claim_handoffs.ClaimHandoffError,
        match="already being mutated by other/project/wt-other",
    ):
        claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    assert claim_handoffs.show(bundle.bundle_id).state == "offered"
    assert tracking.load_record(world["consumer_path"]).resources == []


def test_accept_source_is_atomic_and_idempotent(handoff_world):
    world = _setup_bundle(handoff_world, source_machine="ember")
    bundle = _offer_cross_machine(world, [claim.ref for claim in world["claims"]])

    first = claim_handoffs.accept_source(bundle.bundle_id, actor=world["consumer"])
    second = claim_handoffs.accept_source(bundle.bundle_id, actor=world["consumer"])

    assert first.state == "accepted"
    assert second.to_dict() == first.to_dict()
    assert tracking.load_record(world["source_path"]).resources == []
    assert claim_handoffs.show(bundle.bundle_id).state == "accepted"


def test_cli_accept_source_returns_json_without_logging_main_accept(
    handoff_world, monkeypatch, capfd
):
    world = _setup_bundle(handoff_world, source_machine="ember")
    bundle = _offer_cross_machine(world, [world["claims"][1].ref])
    config = types.SimpleNamespace(machine="ember", repo_name="source-project")
    monkeypatch.setattr(m.cfg, "load_config", lambda: config)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-source")

    assert m.cmd_claims(
        _args(
            ["handoff", "accept-source", bundle.bundle_id],
            claim_actor=world["consumer"],
        )
    ) == 0

    payload = json.loads(capfd.readouterr().out)
    assert payload["state"] == "accepted"
    assert tracking.load_record(world["source_path"]).resources[0].ref == world["claims"][0].ref


def test_cli_accept_logs_and_returns_accepted_bundle(handoff_world, monkeypatch, capfd):
    world = _setup_bundle(handoff_world)
    bundle = _offer(world, [world["claims"][1].ref])[0]
    config = types.SimpleNamespace(machine=MACHINE, repo_name="consumer-project")
    monkeypatch.setattr(m.cfg, "load_config", lambda: config)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda explicit, config: "wt-consumer")
    logged = []
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: logged.append((a, k)))

    assert m.cmd_claims(_args(["handoff", "accept", bundle.bundle_id])) == 0

    payload = json.loads(capfd.readouterr().out)
    assert payload["state"] == "accepted"
    assert logged[-1] == (("claim_handoff_accepted",), {
        "worktree_id": world["consumer"],
        "bundle_id": bundle.bundle_id,
        "source": world["source"],
        "refs": [world["claims"][1].ref],
    })


def test_full_lifecycle_history_is_ordered_across_rebind_and_bundle_transfer(
    handoff_world,
):
    """worktree-claims-transitive-finalization Phase 3b's own Validation
    Plan item: a PR handed off across worktrees/sessions (an agent-bridge
    session rebind, then an explicit claim-handoff bundle transfer, then a
    final release) must produce one complete, correctly-ordered history
    via ``claims history <ref>`` -- no hop silently dropped, regardless of
    which worktree record each event actually lives on. (The effort's own
    agent-dispatch-redrive half of this scenario has no live code path to
    simulate -- investigated and confirmed a no-op today, see this
    effort's own Plan Phase 3b -- so this composite only covers the two
    mechanisms that genuinely exist: rebind and bundle transfer.)
    """
    world = _setup_bundle(handoff_world)
    pr_ref = world["claims"][1].ref

    # Hop 1: an agent-bridge session rebind to the SOURCE worktree, before
    # any bundle transfer -- the same implicit-reassignment path
    # `test_link_handoff_default_save_feeds_history_after_its_own_save`
    # exercises in isolation, here chained into a longer real lifecycle.
    source_path = world["source_path"]
    source_rec = tracking.load_record(source_path)
    source_rec.sessions = [
        tracking.SessionEntry("old-session", "t"),
        tracking.SessionEntry("new-session", "t"),
    ]
    tracking.save_record(source_rec, source_path)
    source_rec = tracking.load_record(source_path)
    tracking.open_handoff(source_rec, "old-session", "token", save=False)
    tracking.save_record(source_rec, source_path)
    source_rec = tracking.load_record(source_path)
    tracking.link_handoff(source_rec, "token", "new-session")  # save=True

    # Hop 2: an explicit claim-handoff bundle transfer from source to
    # consumer -- the same mechanism `test_accept_feeds_claim_history_for_pr_claim`
    # exercises in isolation.
    bundle = _offer(world, [pr_ref])[0]
    claim_handoffs.accept(bundle.bundle_id, actor=world["consumer"], machine=MACHINE)

    # Hop 3: the consumer releases the claim (e.g. the PR merged).
    tracking_claim_write.apply_claim_release({
        "worktree_id": "wt-consumer",
        "yaml_path": str(world["consumer_path"]),
        "ref": pr_ref,
    })

    events = claim_history.history_for_ref(pr_ref)
    assert [e["event"] for e in events] == [
        "reassigned", "released", "claimed", "released",
    ]
    reassigned, bundle_release, bundle_claim, final_release = events
    assert reassigned["worktree_id"] == "wt-source"
    assert "old-session -> new-session" in reassigned["note"]
    assert bundle_release["worktree_id"] == "wt-source"
    assert bundle.bundle_id in bundle_release["note"]
    assert bundle_claim["worktree_id"] == "wt-consumer"
    assert bundle.bundle_id in bundle_claim["note"]
    assert final_release["worktree_id"] == "wt-consumer"
