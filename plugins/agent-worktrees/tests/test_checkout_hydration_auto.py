"""Automatic post-exit dehydration and hydrate-before-entry (Phase 4).

Real Git checkouts and, where retirement evidence matters, a real child
process; session/mux probes are stubbed at their strict-verdict boundary.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from test_checkout_hydration import WT_ID, _git, _registered
from test_checkout_hydration import env as env  # noqa: F401  (pytest fixture)

from agent_worktrees import agent_policy, launch_seed_state, locks, procs, sessions, tracking, tracking_lookup
from agent_worktrees import checkout_hydration as hydration
from agent_worktrees import checkout_hydration_auto as auto
from agent_worktrees import checkout_hydration_catalog as catalog
from agent_worktrees import config as cfg
from agent_worktrees import checkout_hydration_resident as resident
from agent_worktrees import embody_receipt, execution_spaces, launch_request, output, resolve_cli, worktree_identity
from agent_worktrees import knowledge_plugins, status_monitor_runtime
from agent_worktrees.launch_preflight import LaunchPreflight

_REAL_BLOCKER = procs.checkout_retirement_blocker


def _automatic(env: SimpleNamespace, mode: str = "automatic") -> None:
    (env.ledger.parent.parent / "config.yaml").write_text(
        f"checkout_dehydration:\n  mode: {mode}\n", encoding="utf-8",
    )


def _expire_lease(env: SimpleNamespace) -> None:
    lease = auto._state_dir(env.ledger) / auto.ENTRY_LEASE
    if lease.exists():
        lease.write_text(json.dumps({"version": 1, "pid": 0, "at": time.time() - 3600}), encoding="utf-8")


def _marker(env: SimpleNamespace) -> Path:
    return auto._state_dir(env.ledger) / auto.CANDIDATE


def _record(env: SimpleNamespace) -> tracking.WorktreeRecord:
    return tracking.load_record(env.ledger)


# -- policy -------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    (None, "manual"),
    ("checkout_dehydration:\n  mode: automatic\n", "automatic"),
    ("checkout_dehydration:\n  mode: Automatic\n", "manual"),
    ("checkout_dehydration: automatic\n", "manual"),
    ("checkout_dehydration: [\n", "manual"),
])
def test_policy_is_manual_unless_exactly_automatic(env: SimpleNamespace, text: str | None, expected: str) -> None:
    if text is not None:
        (env.ledger.parent.parent / "config.yaml").write_text(text, encoding="utf-8")
    assert auto.policy_mode("sample") == expected


def test_manual_policy_never_queues_or_dehydrates(env: SimpleNamespace) -> None:
    assert auto.note_exit(_record(env), env.config) is False
    auto.after_post_exit(WT_ID, env.config)
    assert env.checkout.exists() and not env.entry.exists() and not _marker(env).exists()


# -- process retirement -------------------------------------------------------


def test_post_exit_waits_for_real_process_retirement(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Stop/post-exit callback alone is not retirement; a live CWD user blocks."""
    _automatic(env)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", _REAL_BLOCKER)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], cwd=env.checkout,
    )
    try:
        deadline = time.monotonic() + 15
        while _REAL_BLOCKER(str(env.checkout)) is None and time.monotonic() < deadline:
            time.sleep(0.2)
        auto.after_post_exit(WT_ID, env.config)  # queues only
        assert auto.run_pending(env.config)["candidates"][0]["outcome"] == "retained"
        assert env.checkout.exists() and not env.entry.exists()
        data = json.loads(_marker(env).read_text(encoding="utf-8"))
        assert data["last_reason"] == "live-process" and data["attempts"] == 1
    finally:
        child.kill()
        child.wait()

    report = auto.run_pending(env.config)

    assert [c["outcome"] for c in report["candidates"]] == ["dehydrated"]
    assert not env.checkout.exists() and not _registered(env)
    assert (catalog.read_entry(env.entry) or {})["stage"] == catalog.STAGE_DEHYDRATED
    assert not _marker(env).exists()
    record = _record(env)
    assert record.status == "active" and catalog.is_dehydrated(record)


@pytest.mark.parametrize("verdict,reason", [
    (SimpleNamespace(probes_ok=True, active=True), "live-session"),
    (SimpleNamespace(probes_ok=True, active=False, mux_live=True), "live-session"),
    (SimpleNamespace(probes_ok=False, active=False), "probes-unavailable"),
])
def test_unconfirmed_retirement_retains_the_candidate(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, verdict: Any, reason: str,
) -> None:
    _automatic(env)
    assert auto.note_exit(_record(env), env.config)
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda *_a, **_k: verdict)
    report = auto.run_pending(env.config)
    assert report["candidates"][0] == {"worktree_id": WT_ID, "outcome": "retained", "reason": reason}
    assert env.checkout.exists() and not env.entry.exists()


def test_pending_launch_retains_the_candidate(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _automatic(env)
    assert auto.note_exit(_record(env), env.config)
    monkeypatch.setattr(launch_seed_state, "peek", lambda *_a, **_k: object())
    assert auto.run_pending(env.config)["candidates"][0]["reason"] == "pending-launch"
    monkeypatch.setattr(launch_seed_state, "peek", lambda *_a, **_k: None)
    record = _record(env)
    record.pending_seed = "first prompt"
    tracking.save_record(record, env.ledger)
    assert auto.run_pending(env.config)["candidates"][0]["reason"] == "pending-launch"
    assert env.checkout.exists()


def test_permanent_refusal_drops_and_preserves_work(env: SimpleNamespace) -> None:
    _automatic(env)
    assert auto.note_exit(_record(env), env.config)
    (env.checkout / "unsaved.txt").write_text("keep me", encoding="utf-8")
    report = auto.run_pending(env.config)
    assert report["candidates"][0] == {"worktree_id": WT_ID, "outcome": "dropped", "reason": "dirty-checkout"}
    assert (env.checkout / "unsaved.txt").read_text(encoding="utf-8") == "keep me"
    assert not _marker(env).exists() and not env.entry.exists()


def test_candidate_expires_after_bounded_attempts(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _automatic(env)
    assert auto.note_exit(_record(env), env.config)
    monkeypatch.setattr(sessions, "verify_worktree_active",
                        lambda *_a, **_k: SimpleNamespace(probes_ok=True, active=True))
    outcomes = [auto.run_pending(env.config)["candidates"][0]["outcome"] for _ in range(auto.MAX_ATTEMPTS)]
    assert outcomes[-1] == "expired" and set(outcomes[:-1]) == {"retained"}
    assert not _marker(env).exists() and env.checkout.exists()


# -- entry / dehydrate serialization -----------------------------------------


def test_entry_lease_refuses_dehydration_and_rolls_back_catalog(env: SimpleNamespace) -> None:
    auto.write_entry_lease(env.ledger)
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "entry-pending"
    assert env.checkout.exists() and _registered(env) and not env.entry.exists()


def test_entry_arriving_after_catalog_publish_wins(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic interleave: entry's lease lands between dehydrate's publish and its recheck."""
    real_write = catalog.write_entry
    calls: list[str] = []

    def write_then_enter(path: Path, data: dict[str, Any]) -> None:
        real_write(path, data)
        if not calls:
            calls.append(data["stage"])
            auto.write_entry_lease(env.ledger)

    monkeypatch.setattr(catalog, "write_entry", write_then_enter)
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "entry-pending" and calls == [catalog.STAGE_REMOVING]
    assert env.checkout.exists() and _registered(env) and not env.entry.exists()
    assert auto.entry_refusal(_record(env), env.config) is None and env.checkout.exists()


def test_concurrent_entry_and_dehydration_leave_a_usable_checkout(env: SimpleNamespace) -> None:
    for _round in range(3):
        _expire_lease(env)
        barrier = threading.Barrier(2)
        outcome: dict[str, Any] = {}

        def dehydrate() -> None:
            barrier.wait()
            try:
                outcome["dehydrate"] = hydration.dehydrate(WT_ID, env.config)["state"]
            except hydration.HydrationRefused as exc:
                outcome["dehydrate"] = exc.code
            except TimeoutError:
                outcome["dehydrate"] = "busy"

        def enter() -> None:
            barrier.wait()
            outcome["entry"] = auto.entry_refusal(_record(env), env.config)

        threads = [threading.Thread(target=dehydrate), threading.Thread(target=enter)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert outcome["entry"] is None, outcome
        if outcome["dehydrate"] == "dehydrated":
            # Dehydration ran after the entry finished: a complete dehydrated
            # state that the next entry restores, never a partial one.
            assert not env.checkout.exists() and catalog.is_dehydrated(_record(env)), outcome
            assert auto.entry_refusal(_record(env), env.config) is None
        assert env.checkout.exists() and _registered(env), outcome
        assert _git(env.checkout, "rev-parse", "HEAD") == env.head
        assert not env.entry.exists(), outcome


def test_two_automatic_cycles(env: SimpleNamespace) -> None:
    _automatic(env)
    for _cycle in range(2):
        auto.after_post_exit(WT_ID, env.config)
        assert _marker(env).exists(), "post-exit did not queue a candidate"
        report = auto.run_pending(env.config)  # the resident pass
        # The pass report names any refusal or error, so a failure is self-explaining.
        assert not env.checkout.exists() and catalog.is_dehydrated(_record(env)), report
        assert auto.entry_refusal(_record(env), env.config) is None
        assert env.checkout.exists() and not env.entry.exists()
        assert _git(env.checkout, "symbolic-ref", "HEAD") == f"refs/heads/{env.branch}"
        assert _git(env.checkout, "rev-parse", "HEAD") == env.head
        # The new entry superseded any queued exit and released its lease on finishing.
        assert not _marker(env).exists() and not auto.entry_lease_pending(env.ledger)
    record = _record(env)
    assert record.worktree_id == WT_ID and record.status == "active"


def test_manual_dehydrate_right_after_entry_does_not_wait(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    assert auto.entry_refusal(_record(env), env.config) is None and env.checkout.exists()
    started = time.monotonic()
    assert hydration.dehydrate(WT_ID, env.config)["state"] == catalog.STATE_DEHYDRATED
    assert time.monotonic() - started < auto.ENTRY_BUSY_WAIT and not env.checkout.exists()
    # A refused entry releases its lease too.
    env.checkout.mkdir()
    (env.checkout / "stray").write_text("x", encoding="utf-8")
    assert auto.entry_refusal(_record(env), env.config) is not None
    assert not (auto._state_dir(env.ledger) / auto.ENTRY_LEASE).exists()


def test_lease_of_a_replaced_entry_process_is_released(env: SimpleNamespace) -> None:
    path = auto._state_dir(env.ledger) / auto.ENTRY_LEASE
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = {"version": 1, "pid": os.getpid(), "start": "not-this-process", "token": "t", "at": time.time()}
    path.write_text(json.dumps(lease), encoding="utf-8")
    assert not auto.entry_lease_pending(env.ledger)  # the pid now names a different process
    lease["start"] = locks.process_start_time(os.getpid())
    path.write_text(json.dumps(lease), encoding="utf-8")
    assert auto.entry_lease_pending(env.ledger)  # a live, unreleased entry still holds it
    path.unlink()
    assert hydration.dehydrate(WT_ID, env.config)["state"] == catalog.STATE_DEHYDRATED


def test_entry_that_finished_during_automatic_dehydration_supersedes_it(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry withdraws the candidate before releasing its lease, so a late check still sees it."""
    _automatic(env)
    assert auto.note_exit(_record(env), env.config)
    real_write = catalog.write_entry

    def enter_then_write(path: Path, data: dict[str, Any]) -> None:
        if data["stage"] == catalog.STAGE_REMOVING:
            # The entry reads no catalog yet, withdraws the candidate and releases its lease.
            assert auto.entry_refusal(_record(env), env.config) is None
            assert not auto.entry_lease_pending(env.ledger)
        real_write(path, data)

    monkeypatch.setattr(catalog, "write_entry", enter_then_write)
    report = auto.run_pending(env.config)
    assert report["candidates"] == [{"worktree_id": WT_ID, "outcome": "dropped", "reason": "superseded"}]
    assert env.checkout.exists() and _registered(env) and not env.entry.exists()


# -- inspection, skew and rollback -------------------------------------------


def test_preview_and_inspection_never_hydrate(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    assert auto.entry_refusal(_record(env), env.config, preview=True) is None
    assert catalog.hydration_state(_record(env)) == catalog.STATE_DEHYDRATED
    with pytest.raises(hydration.HydrationRefused):
        hydration.plan(WT_ID, env.config)
    assert not env.checkout.exists()
    assert not (auto._state_dir(env.ledger) / auto.ENTRY_LEASE).exists()


def test_newer_catalog_schema_is_a_hold_not_a_launch(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    data = json.loads(env.entry.read_text(encoding="utf-8"))
    data["schema_version"] = catalog.SCHEMA_VERSION + 1
    env.entry.write_text(json.dumps(data), encoding="utf-8")
    message = auto.entry_refusal(_record(env), env.config)
    assert message is not None and "(hold)" in message
    assert message.startswith("checkout-dehydrated:") and f"agent-worktrees rehydrate {WT_ID}" in message
    assert not env.checkout.exists() and env.entry.exists()
    _automatic(env)
    assert auto.run_pending(env.config)["reconciled"] == []  # unreadable entries are left for inspection
    assert env.entry.exists()


def test_rollback_to_manual_keeps_dehydrated_entries_held_and_enterable(env: SimpleNamespace) -> None:
    _automatic(env)
    auto.after_post_exit(WT_ID, env.config)
    auto.run_pending(env.config)
    assert not env.checkout.exists()
    _automatic(env, "manual")
    auto.note_exit(_record(env), env.config)
    assert auto.run_pending(env.config)["candidates"] == []
    assert env.entry.exists() and catalog.is_dehydrated(_record(env))
    assert auto.entry_refusal(_record(env), env.config) is None and env.checkout.exists()


def test_restart_reconciliation_completes_interrupted_dehydration(env: SimpleNamespace) -> None:
    snapshot = hydration._inspect(_record(env), env.checkout, env.anchor)
    catalog.write_entry(env.entry, {
        "schema": catalog.SCHEMA, "schema_version": catalog.SCHEMA_VERSION, "operation_id": "op",
        "stage": catalog.STAGE_REMOVING, "prior_state": {},
        "identity": {
            "repo": "sample", "worktree_id": WT_ID, "checkout_path": str(env.checkout), "anchor": str(env.anchor),
        },
        "git": snapshot, "agency": {},
    })
    _git(env.anchor, "worktree", "remove", str(env.checkout))
    report = auto.run_pending(env.config)
    assert report["reconciled"] == [{"worktree_id": WT_ID, "state": catalog.STATE_DEHYDRATED}]
    assert (catalog.read_entry(env.entry) or {})["stage"] == catalog.STAGE_DEHYDRATED


def _interrupted_entry(env: SimpleNamespace, worktree_id: str) -> Path:
    """A schema-valid interrupted catalog entry for ``worktree_id`` (reconcile is stubbed by callers)."""
    snapshot = hydration._inspect(_record(env), env.checkout, env.anchor)
    path = catalog.entry_path(env.config.default_repo.worktree_root, worktree_id)
    catalog.write_entry(path, {
        "schema": catalog.SCHEMA, "schema_version": catalog.SCHEMA_VERSION, "operation_id": f"op-{worktree_id}",
        "stage": catalog.STAGE_REHYDRATING, "prior_state": {},
        "identity": {
            "repo": "sample", "worktree_id": worktree_id,
            "checkout_path": str(env.checkout.parent / worktree_id), "anchor": str(env.anchor),
        },
        "git": snapshot, "agency": {},
    })
    return path


def test_persistent_holds_rotate_fairly_and_back_off(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three unchanged holds ahead of a fourth entry neither starve it nor keep resident demand alive."""
    held_ids = ["held-1", "held-2", "held-3"]
    paths = {wid: _interrupted_entry(env, wid) for wid in [*held_ids, "zlast-4"]}
    catalog.register_root("sample", env.entry.parent.parent)
    _automatic(env)
    attempts: list[str] = []

    def reconcile(worktree_id: str, _config: Any) -> dict[str, Any]:
        attempts.append(worktree_id)
        if worktree_id in held_ids:
            return {"worktree_id": worktree_id, "state": "hold"}  # inspection-only: nothing changes
        entry = catalog.read_entry(paths[worktree_id]) or {}
        catalog.write_entry(paths[worktree_id], {**entry, "stage": catalog.STAGE_DEHYDRATED})
        return {"worktree_id": worktree_id, "state": catalog.STATE_DEHYDRATED}

    monkeypatch.setattr(hydration, "reconcile", reconcile)
    assert attempts == [] and resident._has_work("sample")
    for _ in range(2):
        auto.run_pending(env.config)
    assert attempts == [*held_ids, "zlast-4"]  # reconciled within two bounded passes
    assert (catalog.read_entry(paths["zlast-4"]) or {})["stage"] == catalog.STAGE_DEHYDRATED
    assert not resident._has_work("sample")  # unchanged holds are not demand
    auto.run_pending(env.config)
    assert len(attempts) == 4  # and not runnable work
    time.sleep(0.05)
    entry = catalog.read_entry(paths["held-2"]) or {}
    catalog.write_entry(paths["held-2"], {**entry, "operation_id": "op-changed"})  # its evidence changed
    assert [wid for wid, _p in auto.interrupted_entries("sample", [env.entry.parent.parent])] == ["held-2"]
    monkeypatch.setattr(auto, "HOLD_BACKOFF", 0.0)  # backoff elapsed: every hold is retried, oldest first
    assert [wid for wid, _p in auto.interrupted_entries("sample", [env.entry.parent.parent])][-1] == "held-3"
    assert resident._has_work("sample")


def _anchor_paired(env: SimpleNamespace) -> tracking.WorktreeRecord:
    record = _record(env)
    record.pair_role, record.pair_kind, record.pair_ref = "harness", "anchor", "machine/knowledge/@anchor"
    return record


def test_reconciled_rehydrate_leaves_paired_entry_pending(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A restore completed by reconciliation (or manual rehydrate) still requires pair preparation."""
    hydration.dehydrate(WT_ID, env.config)
    tracking.save_record(_anchor_paired(env), env.ledger)
    pending = hydration.pair_readiness_marker(env.ledger)
    entry = catalog.read_entry(env.entry) or {}
    catalog.write_entry(env.entry, {**entry, "stage": catalog.STAGE_REHYDRATING})
    _git(env.anchor, "worktree", "add", "--no-checkout", str(env.checkout), entry["git"]["branch"])
    # The interrupted rehydrate left an added but unpopulated checkout; restart reconciliation completes it.
    report = auto.run_pending(env.config)
    assert report["reconciled"] == [{"worktree_id": WT_ID, "state": catalog.STATE_HYDRATED}]
    assert not env.entry.exists() and pending.exists()
    composed: list[Any] = []
    def compose(**k: Any) -> dict[str, str]:
        composed.append(k)
        return {"action": "composed"}

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
    assert auto.entry_refusal(_record(env), env.config) is None
    assert len(composed) == 1 and not pending.exists()
    hydration.dehydrate(WT_ID, env.config)
    hydration.rehydrate(WT_ID, env.config)  # a manual restore marks it too
    assert pending.exists()


# -- pairing ------------------------------------------------------------------


def test_pair_entry_hydrates_the_knowledge_sibling(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    hydration.dehydrate(WT_ID, env.config)
    sibling = SimpleNamespace(worktree_id="sibling-win-20261121-100000-ef01")
    sibling_config = SimpleNamespace(repo_name="knowledge")
    hydrated: list[tuple[str, Any]] = []
    real = auto.ensure_hydrated

    def ensure(record: Any, config: Any) -> dict[str, Any]:
        hydrated.append((record.worktree_id, config))
        return real(record, config) if record.worktree_id == WT_ID else {"state": "hydrated", "already": True}

    monkeypatch.setattr(auto, "ensure_hydrated", ensure)
    monkeypatch.setattr(tracking_lookup, "find_paired_record", lambda _r: sibling)
    monkeypatch.setattr(execution_spaces, "project_record_config", lambda _r: sibling_config)
    composed: list[tuple[str, Any]] = []

    def compose(*, cwd: Any = None, config: Any = None) -> dict[str, Any]:
        composed.append((str(cwd), config))
        return {"action": "composed", "paired": True}

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
    record = _paired(env)

    assert auto.entry_refusal(record, env.config) is None

    assert hydrated == [(WT_ID, env.config), (sibling.worktree_id, sibling_config)]
    assert env.checkout.exists() and record.pair_role == "harness"
    # Restored pair settings and companion instructions are prepared before entry is ready.
    assert composed == [(str(record.worktree_path), env.config)]
    assert auto.entry_refusal(record, env.config) is None and len(composed) == 1  # nothing restored


@pytest.mark.parametrize("result,refused", [
    ({"action": "disabled", "paired": True}, False),  # a composition opt-out is preserved
    ({"action": "no-op", "pair_error": "binding changed"}, True),
    ({"action": "error", "error": "overlay write failed"}, True),
])
def test_pair_preparation_after_restore(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, result: dict[str, Any], refused: bool,
) -> None:
    hydration.dehydrate(WT_ID, env.config)
    monkeypatch.setattr(tracking_lookup, "find_paired_record", lambda _r: SimpleNamespace(worktree_id="sib"))
    monkeypatch.setattr(execution_spaces, "project_record_config", lambda _r: SimpleNamespace())
    monkeypatch.setattr(auto, "ensure_hydrated", _hydrate_only(WT_ID))
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **_k: result)
    message = auto.entry_refusal(_paired(env), env.config)
    assert (message is not None and "pair-preparation-failed" in message) is refused
    assert env.checkout.exists()


def _paired(env: SimpleNamespace, sibling_id: str = "sibling-win-20261121-100000-ef01") -> Any:
    record = _record(env)
    record.pair_role, record.pair_kind = "harness", "worktree"
    record.pair_ref = f"machine/knowledge/{sibling_id}"
    return record


def _hydrate_only(worktree_id: str) -> Any:
    real = auto.ensure_hydrated

    def ensure(record: Any, config: Any) -> dict[str, Any]:
        return real(record, config) if record.worktree_id == worktree_id else {"state": "hydrated", "already": True}

    return ensure


@pytest.mark.parametrize("malformed", [False, True])
def test_unresolved_declared_sibling_refuses_a_restoring_entry(env: SimpleNamespace, malformed: bool) -> None:
    """When entry restores the checkout, a missing or malformed declared sibling is not treated as unpaired."""
    hydration.dehydrate(WT_ID, env.config)
    sibling_id = "sibling-win-20261121-100000-ef01"
    ledger = cfg.project_dir("knowledge") / "worktrees" / f"{sibling_id}.yaml"
    if malformed:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("worktree_id: [\n", encoding="utf-8")
    message = auto.entry_refusal(_paired(env, sibling_id), env.config)
    assert message is not None and "sibling-unavailable" in message
    assert ("unreadable" if malformed else "missing") in message


def test_refused_paired_entry_stays_incomplete_until_the_sibling_is_ready(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after a refused restoring entry still validates the sibling and prepares the pair."""
    hydration.dehydrate(WT_ID, env.config)
    composed: list[Any] = []

    def compose(**kwargs: Any) -> dict[str, str]:
        composed.append(kwargs)
        return {"action": "composed"}

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
    pending = auto._state_dir(env.ledger) / auto.PAIR_READINESS
    for _attempt in range(2):  # the first restores the checkout; the unchanged retry finds it present
        message = auto.entry_refusal(_paired(env), env.config)
        assert message is not None and "sibling-unavailable" in message
        assert env.checkout.exists() and pending.exists() and composed == []
    sibling = SimpleNamespace(worktree_id="sibling-win-20261121-100000-ef01")
    monkeypatch.setattr(tracking_lookup, "find_paired_record", lambda _r: sibling)
    monkeypatch.setattr(execution_spaces, "project_record_config", lambda _r: SimpleNamespace())
    monkeypatch.setattr(auto, "ensure_hydrated", _hydrate_only(WT_ID))
    assert auto.entry_refusal(_paired(env), env.config) is None
    assert len(composed) == 1 and not pending.exists()
    assert auto.entry_refusal(_paired(env), env.config) is None and len(composed) == 1  # ready: no rerun


def test_interrupted_sibling_only_restore_keeps_paired_entry_pending(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hydrated primary whose sibling restore was interrupted before preparation prepares on retry."""
    sibling = SimpleNamespace(worktree_id="sibling-win-20261121-100000-ef01")
    sibling_config = SimpleNamespace(repo_name="knowledge")
    sibling_dehydrated = [True]
    real_claims, real_ensure = auto._catalog_claims, auto.ensure_hydrated

    def claims(record: Any, config: Any) -> bool:
        return sibling_dehydrated[0] if record is sibling else real_claims(record, config)

    def ensure(record: Any, config: Any) -> dict[str, Any]:
        if record is not sibling:
            return real_ensure(record, config)
        restored, sibling_dehydrated[0] = sibling_dehydrated[0], False
        if restored:  # the sibling checkout is back, but entry stops before it reports
            raise KeyboardInterrupt("interrupted after the sibling restore")
        return {"state": "hydrated", "already": True}

    attempts: list[int] = []

    def compose(**_kwargs: Any) -> dict[str, str]:
        attempts.append(1)
        return {"action": "composed"}

    monkeypatch.setattr(auto, "_catalog_claims", claims)
    monkeypatch.setattr(auto, "ensure_hydrated", ensure)
    monkeypatch.setattr(tracking_lookup, "find_paired_record", lambda _r: sibling)
    monkeypatch.setattr(execution_spaces, "project_record_config", lambda _r: sibling_config)
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
    pending = auto._state_dir(env.ledger) / auto.PAIR_READINESS
    assert env.checkout.exists() and not real_claims(_paired(env), env.config)  # primary hydrated

    with pytest.raises(KeyboardInterrupt):
        auto.entry_refusal(_paired(env), env.config)
    assert not sibling_dehydrated[0] and pending.exists() and attempts == []  # restored; still pending
    assert auto.entry_refusal(_paired(env), env.config) is None  # both present: the retry still prepares
    assert len(attempts) == 1 and not pending.exists()
    assert auto.entry_refusal(_paired(env), env.config) is None and len(attempts) == 1  # ready: no rerun


def test_anchor_paired_restore_prepares_the_pair(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """An anchor pair has no sibling checkout to hydrate but still regains its knowledge overlay."""
    hydration.dehydrate(WT_ID, env.config)

    def no_sibling(_record: Any) -> Any:
        raise AssertionError("an anchor pair has no sibling worktree to hydrate")

    monkeypatch.setattr(tracking_lookup, "find_paired_record", no_sibling)
    composed: list[Any] = []
    def compose(**k: Any) -> dict[str, str]:
        composed.append(k)
        return {"action": "composed"}

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
    record = _anchor_paired(env)
    assert auto.entry_refusal(record, env.config) is None
    assert env.checkout.exists() and len(composed) == 1
    assert not hydration.pair_readiness_marker(env.ledger).exists()
    assert auto.entry_refusal(record, env.config) is None and len(composed) == 1  # ready: no rerun


def test_unqualified_pair_reference_refuses_a_restoring_entry(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    record = _paired(env)
    record.pair_ref = "sibling-win-20261121-100000-ef01"
    message = auto.entry_refusal(record, env.config)
    assert message is not None and "sibling-unavailable" in message


def test_unavailable_sibling_refuses_a_restoring_entry(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_r: Any) -> Any:
        raise execution_spaces.ExecutionSpaceError("no project")

    monkeypatch.setattr(tracking_lookup, "find_paired_record", lambda _r: SimpleNamespace(worktree_id="x"))
    monkeypatch.setattr(execution_spaces, "project_record_config", unavailable)
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **_k: {"action": "composed"})
    assert auto.entry_refusal(_paired(env), env.config) is None  # nothing dehydrated: the pair owner's concern
    hydration.dehydrate(WT_ID, env.config)
    message = auto.entry_refusal(_paired(env), env.config)
    assert message is not None and "sibling-unavailable" in message


def test_undehydrated_entry_with_a_missing_sibling_is_unchanged(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    """Without dehydration on either side, a missing declared sibling does not change entry."""
    record = _paired(env)
    tracking.save_record(record, env.ledger)
    composed: list[Any] = []
    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **k: composed.append(k))
    assert auto.entry_refusal(_record(env), env.config) is None
    seen: list[str] = []
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda wt_id: wt_id)
    monkeypatch.setattr(resolve_cli, "_relocate_active_project_for_worktree", lambda _wt: False)
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda _c: None)

    def preflight(_config: Any, _args: Any, work_dir: str) -> LaunchPreflight:
        seen.append(work_dir)
        return LaunchPreflight(policy_error="stop at the launch boundary")

    monkeypatch.setattr(resolve_cli, "_preflight_launch", preflight)
    assert resolve_cli._resolve_json_mode(_state(env, use_json=True)) == 3
    assert "stop at the launch boundary" in capfd.readouterr().out
    assert [Path(p) for p in seen] == [env.checkout] and composed == []
    assert env.checkout.exists() and not env.entry.exists() and not _marker(env).exists()
    assert not (auto._state_dir(env.ledger) / auto.ENTRY_LEASE).exists()


@pytest.mark.parametrize("evidence", ["dehydrated", "unreadable"])
def test_dehydrated_or_unknown_sibling_catalog_refuses_entry(
    env: SimpleNamespace, tmp_path: Path, evidence: str,
) -> None:
    """A missing sibling record whose catalog shows (or may show) a dehydrated checkout refuses."""
    sibling_id = "sibling-win-20261121-100000-ef01"
    if evidence == "dehydrated":
        root = tmp_path / "knowledge-worktrees"
        catalog.register_root("knowledge", root)
        entry = catalog.entry_path(root, sibling_id)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("{}", encoding="utf-8")
    else:
        roots = catalog._roots_file("knowledge")
        roots.parent.mkdir(parents=True, exist_ok=True)
        roots.write_text("{not json", encoding="utf-8")
    message = auto.entry_refusal(_paired(env, sibling_id), env.config)
    assert message is not None and "sibling-unavailable" in message and "missing" in message
    assert env.checkout.exists()


# -- real entry functions -----------------------------------------------------


def _state(env: SimpleNamespace, *, use_json: bool, dry_run: bool = False) -> resolve_cli.ResolveCommandState:
    return resolve_cli.ResolveCommandState(
        args=argparse.Namespace(dry_run=dry_run, bare_resume=False, restore=False),
        use_json=use_json, use_base=False, use_new=False, requested_machine=None,
        worktree_id=WT_ID, config=cast(cfg.Config, env.config),
    )


@pytest.fixture
def entry_env(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    hydration.dehydrate(WT_ID, env.config)
    assert not env.checkout.exists()
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda wt_id: wt_id)
    monkeypatch.setattr(resolve_cli, "_relocate_active_project_for_worktree", lambda _wt: False)

    def policy(path: str) -> None:
        seen.append(("policy", Path(path).exists()))

    def preflight(_config: Any, _args: Any, work_dir: str) -> LaunchPreflight:
        seen.append(("preflight", Path(work_dir).exists()))
        return LaunchPreflight(policy_error="stop at the launch boundary")

    monkeypatch.setattr(agent_policy, "require", policy)
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda _c: None)
    monkeypatch.setattr(resolve_cli, "_preflight_launch", preflight)
    env.seen = seen
    return env


def test_mux_entry_hydrates_before_policy_and_cwd_use(entry_env: SimpleNamespace) -> None:
    resolve_cli._resolve_noninteractive_worktree(_state(entry_env, use_json=False))
    assert entry_env.seen[:2] == [("policy", True), ("preflight", True)]
    assert _git(entry_env.checkout, "rev-parse", "HEAD") == entry_env.head
    assert _record(entry_env).worktree_id == WT_ID


def test_bridge_entry_hydrates_before_preflight(entry_env: SimpleNamespace, capfd: pytest.CaptureFixture[str]) -> None:
    rc = resolve_cli._resolve_json_mode(_state(entry_env, use_json=True))
    assert rc == 3 and "stop at the launch boundary" in capfd.readouterr().out
    assert entry_env.seen == [("preflight", True)]
    assert entry_env.checkout.exists() and not entry_env.entry.exists()


def test_entry_preview_does_not_hydrate(entry_env: SimpleNamespace) -> None:
    resolve_cli._resolve_json_mode(_state(entry_env, use_json=True, dry_run=True))
    assert entry_env.seen == [("preflight", False)] and not entry_env.checkout.exists()


def test_entry_refusal_blocks_launch(entry_env: SimpleNamespace, capfd: pytest.CaptureFixture[str]) -> None:
    entry_env.checkout.mkdir()
    (entry_env.checkout / "stray").write_text("x", encoding="utf-8")
    rc = resolve_cli._resolve_json_mode(_state(entry_env, use_json=True))
    assert rc == 3 and "(hold)" in capfd.readouterr().out
    assert entry_env.seen == [] and (entry_env.checkout / "stray").exists()


def _embody(env: SimpleNamespace, **flags: Any) -> list[bool]:
    seen: list[bool] = []

    def operation(target: dict[str, Any]) -> int:
        seen.append(env.checkout.exists())
        target["worktree_id"] = WT_ID
        return output._json_error("stop at the launch boundary", exit_code=3)

    args = argparse.Namespace(**{"worktree_id": WT_ID, "wait_session_timeout": 0.0, **flags})
    embody_receipt.run(args, operation)
    return seen


def test_embody_entry_hydrates_before_the_operation(
    entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: entry_env.config)
    assert _embody(entry_env) == [True]
    assert _git(entry_env.checkout, "rev-parse", "HEAD") == entry_env.head
    assert not entry_env.entry.exists()
    assert _embody(entry_env, dry_run=True) == [True]  # already hydrated: idempotent


def test_embody_preview_and_new_never_hydrate(entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: entry_env.config)
    assert _embody(entry_env, dry_run=True) == [False]
    assert _embody(entry_env, new=True) == [False]
    assert not entry_env.checkout.exists() and entry_env.entry.exists()


def test_embody_codename_entry_hydrates_before_the_operation(
    entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: entry_env.config)
    record = _record(entry_env)
    record.codename = "brave-otter"
    tracking.save_record(record, entry_env.ledger)
    assert _embody(entry_env, worktree_id=None, codename="brave-otter") == [True]
    assert not entry_env.entry.exists()


def test_embody_refusal_is_structured(
    entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: entry_env.config)
    entry_env.checkout.mkdir()
    (entry_env.checkout / "stray").write_text("x", encoding="utf-8")
    assert _embody(entry_env) == []
    payload = json.loads(capfd.readouterr().out.strip())
    assert payload["reason"] == auto.DEHYDRATED_REFUSAL and payload["code"] == "hold"
    assert payload["remedy"] == f"agent-worktrees rehydrate {WT_ID}" and payload["ok"] is False


@pytest.mark.parametrize("flags", [
    {"deliver_pending_seed": True, "fresh": True},
    {"deliver_pending_seed": True, "seed": "hello"},
    {"copilot_args": ["--acp"]},
    {"bridge_acquisition": "fresh", "require_native_admission": True},
])
def test_rejected_embody_never_hydrates(
    entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, flags: dict[str, Any],
) -> None:
    """Embody's own argument validation rejects these before resolving: nothing is hydrated or withdrawn."""
    monkeypatch.setattr(cfg, "load_config", lambda: entry_env.config)
    marker = _marker(entry_env)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"version": 1, "generation": "g"}', encoding="utf-8")
    catalog_bytes = entry_env.entry.read_bytes()
    assert _embody(entry_env, **flags) == [False]
    assert not entry_env.checkout.exists() and entry_env.entry.read_bytes() == catalog_bytes
    assert marker.read_text(encoding="utf-8") == '{"version": 1, "generation": "g"}'


def _launch_request(entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> tuple[int, list[bool]]:
    """Run the real resolve entry for a remote seed resume envelope up to its request command."""
    monkeypatch.setattr(cfg, "load_config", lambda: entry_env.config)
    seen: list[bool] = []

    def cmd_request(_args: Any) -> int:
        seen.append(entry_env.checkout.exists())
        return 0

    monkeypatch.setattr(launch_request, "cmd_request", cmd_request)
    intent = {"version": 1, "request_id": "a" * 32, "kind": "resume", "worktree_id": WT_ID,
              "text": "task", "no_mux": False}
    encoded = base64.b64encode(json.dumps(intent).encode()).decode()
    assert launch_request.decode(encoded) == intent
    args = argparse.Namespace(json=True, launch_request_b64=encoded, launch_request_status=None)
    return resolve_cli._cmd_resolve(args), seen


def test_remote_seed_resume_hydrates_before_admission(
    entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _launch_request(entry_env, monkeypatch) == (0, [True])
    assert not entry_env.entry.exists()
    assert _git(entry_env.checkout, "rev-parse", "HEAD") == entry_env.head


def test_remote_seed_resume_refuses_an_unrestorable_checkout(
    entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    entry_env.checkout.mkdir()
    (entry_env.checkout / "stray").write_text("x", encoding="utf-8")
    assert _launch_request(entry_env, monkeypatch) == (3, [])
    payload = json.loads(capfd.readouterr().out.strip())
    assert payload["reason"] == "checkout-dehydrated" and payload["worktree_id"] == WT_ID
    assert payload["remedy"] == f"agent-worktrees rehydrate {WT_ID}" and entry_env.entry.exists()


@pytest.mark.parametrize("flags,version", [
    ({"dry_run": True}, 1),  # rejected: not a protocol option
    ({"require_native_seed": True}, 1),  # rejected: native flag and protocol disagree
    ({}, 2),  # rejected: a v2 envelope without the native flag
])
def test_rejected_request_never_hydrates(
    entry_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    flags: dict[str, Any], version: int,
) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: entry_env.config)
    _automatic(entry_env)
    marker = _marker(entry_env)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"version": 1, "exited_at": 1.0, "attempts": 0, "generation": "g"}),
                      encoding="utf-8")
    before = (entry_env.entry.read_bytes(), marker.read_bytes())
    intent = {"version": version, "request_id": "b" * 32, "kind": "resume", "worktree_id": WT_ID,
              "text": "task", "no_mux": False}
    encoded = base64.b64encode(json.dumps(intent).encode()).decode()
    args = argparse.Namespace(json=True, launch_request_b64=encoded, launch_request_status=None, **flags)
    assert resolve_cli._cmd_resolve(args) == 3  # the real request owner rejects it
    assert "failed" in capfd.readouterr().out
    assert not entry_env.checkout.exists()
    assert (entry_env.entry.read_bytes(), marker.read_bytes()) == before


# -- resident status-monitor cadence ------------------------------------------


def _maintenance(env: SimpleNamespace, clock: list[float]) -> resident.DehydrationMaintenance:
    def run_pass(_project: str) -> None:
        auto.run_pending(env.config)  # in-process stand-in for the bounded child pass

    return resident.DehydrationMaintenance(projects=lambda: ["sample"], run_pass=run_pass, now=lambda: clock[0])


def test_resident_pass_dehydrates_a_sole_worktree_after_retirement(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No other exit in the project: the periodic monitor pass completes the dehydration."""
    _automatic(env)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", _REAL_BLOCKER)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=env.checkout)
    try:
        deadline = time.monotonic() + 15
        while _REAL_BLOCKER(str(env.checkout)) is None and time.monotonic() < deadline:
            time.sleep(0.2)
        auto.after_post_exit(WT_ID, env.config)
        assert env.checkout.exists() and _marker(env).exists()
    finally:
        child.kill()
        child.wait()
    clock = [1000.0]
    maintenance = _maintenance(env, clock)
    assert maintenance.tick()  # the first tick runs at once (startup)
    maintenance.wait(60)
    assert not env.checkout.exists() and catalog.is_dehydrated(_record(env)) and not _marker(env).exists()
    assert not maintenance.has_demand()
    assert not maintenance.tick()  # next pass waits for the interval
    clock[0] += resident.INTERVAL
    assert maintenance.tick()
    maintenance.wait(60)


def test_resident_startup_reconciles_and_manual_policy_skips(env: SimpleNamespace) -> None:
    snapshot = hydration._inspect(_record(env), env.checkout, env.anchor)
    catalog.write_entry(env.entry, {
        "schema": catalog.SCHEMA, "schema_version": catalog.SCHEMA_VERSION, "operation_id": "op",
        "stage": catalog.STAGE_REMOVING, "prior_state": {},
        "identity": {
            "repo": "sample", "worktree_id": WT_ID, "checkout_path": str(env.checkout), "anchor": str(env.anchor),
        },
        "git": snapshot, "agency": {},
    })
    catalog.register_root("sample", env.entry.parent.parent)
    _git(env.anchor, "worktree", "remove", str(env.checkout))
    assert not resident._has_work("sample")  # manual policy: the monitor does nothing
    _automatic(env)
    assert resident._has_work("sample")
    maintenance = _maintenance(env, [0.0])
    assert maintenance.tick()
    maintenance.wait(60)
    assert (catalog.read_entry(env.entry) or {})["stage"] == catalog.STAGE_DEHYDRATED
    assert not resident._has_work("sample")


def test_resident_child_pass_is_bounded_and_windowless(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    waits: list[Any] = []

    class Child:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            calls.append((argv, kw))

        def wait(self, timeout: Any = None) -> int:
            waits.append(timeout)
            return 0

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", Child)
    resident.run_project_pass("sample")
    argv, kw = calls[0]
    assert argv[-5:] == ["--project", "sample", "hydration", "--run-pending", "--json"]
    assert waits == [resident.PASS_TIMEOUT] and kw["stdin"] is subprocess.DEVNULL
    assert not resident._CHILDREN


# -- review regressions: generation fencing, final check, wake-only exit ------


def test_retry_never_recreates_a_withdrawn_candidate(env: SimpleNamespace) -> None:
    _automatic(env)
    marker = _marker(env)
    assert auto.note_exit(_record(env), env.config)
    first = auto._generation(marker)
    assert first is not None
    auto.withdraw_candidate(env.ledger)  # entry
    assert auto._retain(marker, first, "busy") == "superseded" and not marker.exists()
    assert auto.note_exit(_record(env), env.config)  # a later exit
    second = auto._generation(marker)
    assert second not in (None, first)
    assert auto._retain(marker, first, "busy") == "superseded"
    auto._drop_generation(marker, first)
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["generation"] == second and data["attempts"] == 0


def test_seed_staged_after_the_first_check_is_seen_before_removal(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _automatic(env)
    assert auto.note_exit(_record(env), env.config)
    real = auto._confirm_retired
    calls: list[str | None] = []

    def confirm(record: Any, ledger: Path) -> str | None:
        reason = real(record, ledger)
        if not calls:  # stage a real seed between the first check and lock acquisition
            launch_seed_state._write(ledger, launch_seed_state.LaunchSeed(
                seed_id="s" * 16, kind="resume", text="next", revision=1_000_000))
            reason_after = None
        else:
            reason_after = reason
        calls.append(reason_after)
        return reason_after

    monkeypatch.setattr(auto, "_confirm_retired", confirm)
    report = auto.run_pending(env.config)
    assert report["candidates"][0] == {"worktree_id": WT_ID, "outcome": "retained", "reason": "pending-launch"}
    assert calls == [None, "pending-launch"]  # the final pre-removal recheck refused
    assert env.checkout.exists() and _registered(env) and not env.entry.exists()
    assert json.loads(_marker(env).read_text(encoding="utf-8"))["last_reason"] == "pending-launch"


def test_post_exit_only_queues_and_wakes(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit processing performs no dehydration or pass work inline."""
    _automatic(env)

    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("exit processing must not dehydrate inline")

    woken: list[bool] = []
    monkeypatch.setattr(hydration, "dehydrate", forbidden)
    monkeypatch.setattr(auto, "run_pending", forbidden)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(status_monitor_runtime, "_ensure_status_monitor", lambda: woken.append(True))
    monkeypatch.setattr(cfg, "load_config", lambda: env.config)
    auto.after_post_exit(WT_ID, env.config)
    assert woken == [True] and _marker(env).exists() and env.checkout.exists()
    auto.run_housekeeping()
    assert woken == [True, True]
    _automatic(env, "manual")
    auto.run_housekeeping()  # no queued work under manual policy: no wake
    assert woken == [True, True]


def test_in_flight_pass_keeps_the_monitor_busy_after_admission_closes() -> None:
    started, release = threading.Event(), threading.Event()

    def run_pass(_project: str) -> None:
        started.set()
        release.wait(30)

    maintenance = resident.DehydrationMaintenance(
        projects=lambda: ["sample"], has_work=lambda _p: True, run_pass=run_pass, now=lambda: 0.0,
    )
    assert maintenance.tick() and started.wait(30)
    maintenance.close_admission()
    assert maintenance.busy() and maintenance.has_demand()
    release.set()
    maintenance.wait(30)
    assert not maintenance.busy() and not maintenance.has_demand()


def test_maintenance_reopens_after_a_drain() -> None:
    clock = [0.0]
    runs: list[str] = []
    maintenance = resident.DehydrationMaintenance(
        projects=lambda: ["sample"], has_work=lambda _p: True, run_pass=runs.append,
        now=lambda: clock[0], admitting=False,
    )
    assert not maintenance.tick()  # inactive until request surfaces activate
    maintenance.activate()
    assert maintenance.tick()
    maintenance.wait(30)
    maintenance.close_admission()  # drain
    clock[0] += resident.INTERVAL
    assert not maintenance.tick()
    maintenance.activate()  # undrain
    assert maintenance.tick()
    maintenance.wait(30)
    assert runs == ["sample", "sample"]


def test_shutdown_settles_an_in_flight_pass_and_terminates_an_overrun() -> None:
    started, release = threading.Event(), threading.Event()
    terminated: list[bool] = []
    clock = [0.0]

    def run_pass(_project: str) -> None:
        started.set()
        release.wait(30)

    def terminate() -> None:
        terminated.append(True)
        release.set()

    maintenance = resident.DehydrationMaintenance(
        projects=lambda: ["sample"], has_work=lambda _p: True, run_pass=run_pass,
        now=lambda: clock[0], terminate=terminate,
    )
    assert maintenance.tick() and started.wait(30)
    threading.Timer(0.3, release.set).start()
    assert maintenance.shutdown(timeout=30) and terminated == [] and not maintenance.busy()
    assert not maintenance.tick()  # admission stays closed
    release.clear()
    started.clear()
    maintenance.activate()
    clock[0] += resident.INTERVAL
    assert maintenance.tick() and started.wait(30)
    assert maintenance.shutdown(timeout=0.2) and terminated == [True]  # bounded: the child is stopped


_TREE_PASS = """
import subprocess, sys, time
beat = sys.argv[1]
grandchild = (
    "import sys, time\\n"
    "end = time.monotonic() + 60\\n"
    "while time.monotonic() < end:\\n"
    "    open(sys.argv[1], 'a').write('.')\\n"
    "    time.sleep(0.05)\\n"
)
subprocess.Popen([sys.executable, "-c", grandchild, beat])
time.sleep(60)
"""


@pytest.mark.parametrize("stop", ["timeout", "shutdown"])
def test_run_project_pass_stops_the_whole_process_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str,
) -> None:
    """A Git grandchild of the pass must not keep changing storage after timeout or shutdown."""
    beat = tmp_path / "beat"
    real = subprocess.Popen

    def popen(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if "--run-pending" in argv:  # only the pass; the tree-kill command launches for real
            argv = [sys.executable, "-c", _TREE_PASS, str(beat)]
        return real(argv, *args, **kwargs)

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(subprocess, "Popen", popen)
    if stop == "timeout":
        monkeypatch.setattr(resident, "PASS_TIMEOUT", 3.0)
    worker = threading.Thread(target=resident.run_project_pass, args=("sample",))
    worker.start()
    deadline = time.monotonic() + 30
    while not (beat.exists() and beat.stat().st_size > 2) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert beat.exists(), "the grandchild never started"
    if stop == "shutdown":
        resident.terminate_passes()
    worker.join(30)
    assert not worker.is_alive() and not resident._CHILDREN
    time.sleep(0.5)
    size = beat.stat().st_size
    time.sleep(1.0)
    assert beat.stat().st_size == size, "the grandchild outlived its pass"


class _StubbornPass:
    """A pass root that survives its kill until the test lets it exit."""

    pid = 424242

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.exited = False
        self.kills = 0

    def poll(self) -> int | None:
        return 0 if self.exited else None

    def wait(self, timeout: Any = None) -> int:
        if self.exited:
            return 0
        raise subprocess.TimeoutExpired("pass", timeout)

    def kill(self) -> None:
        self.kills += 1


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_unconfirmed_tree_stop_keeps_the_pass_busy(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """A failed or timed-out tree sweep still kills the root and keeps maintenance busy until it settles."""
    if sys.platform == "win32":
        real_run = subprocess.run

        def run(argv: Any, *args: Any, **kwargs: Any) -> Any:
            if Path(str(argv[0])).stem.lower() == "task" + "kill":
                if failure == "error":
                    raise OSError("sweep unavailable")
                raise subprocess.TimeoutExpired(argv, 30)
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)
    else:
        if failure == "timeout":
            pytest.skip("the POSIX group signal has no timeout")
        monkeypatch.setattr(os, "getpgid", lambda _pid: -7, raising=False)
        monkeypatch.setattr(os, "killpg", lambda *_a: (_ for _ in ()).throw(OSError("denied")), raising=False)
    child: Any = _StubbornPass()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(resident, "PASS_TIMEOUT", 0.05)
    monkeypatch.setattr(resident, "SHUTDOWN_GRACE", 0.05)
    monkeypatch.setattr(resident, "SETTLE_LIMIT", 0.5)
    maintenance = resident.DehydrationMaintenance(projects=lambda: [], admitting=False)
    try:
        resident.run_project_pass("sample")
        assert child.kills == 1, "the handle-bound fallback kill must still run"
        assert child in resident._CHILDREN and maintenance.busy() and maintenance.has_demand()
        assert not maintenance.shutdown(timeout=0)  # bounded: ownership kept, condition logged
        assert child.kills == 2 and maintenance.busy()
        child.exited = True
        assert maintenance.busy(), "an unconfirmed tree stays busy for the settle bound"
        time.sleep(0.6)
        assert not maintenance.busy() and child not in resident._CHILDREN
    finally:
        child.exited = True
        resident._CHILDREN.discard(child)
        resident._UNCONFIRMED.pop(child, None)


@pytest.mark.parametrize("settle", ["in-time", "unsettled"])
@pytest.mark.parametrize("exit_mode", ["timed-shutdown", "loop-error"])
def test_monitor_exit_waits_for_a_running_dehydration_pass(
    monkeypatch: pytest.MonkeyPatch, exit_mode: str, settle: str,
) -> None:
    """Route ownership is not released while a pass child may still change checkout storage."""
    import ast
    import inspect
    from types import SimpleNamespace

    from agent_worktrees import status_monitor_cli
    from agent_worktrees import status_monitor_retirement as retirement

    # Both exits (a timed shutdown that bypassed busy reasons, or a loop error)
    # leave through the same finally: retire_monitor(close_admission=...), and
    # that close settles the pass outside the state lock.
    tree = ast.parse(inspect.getsource(status_monitor_cli))
    close = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_close_monitor_admission"
    )
    last = close.body[-1]
    assert isinstance(last, ast.Expr) and ast.unparse(last.value) == "dehydration.shutdown_or_raise()"
    assert any(isinstance(node, ast.With) for node in close.body[:-1])
    finals = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Try) and any(
            "close_admission=_close_monitor_admission" in ast.unparse(stmt) for stmt in node.finalbody
        )
    ]
    assert finals

    started, release = threading.Event(), threading.Event()
    events: list[str] = []

    def run_pass(_project: str) -> None:
        started.set()
        release.wait(20)
        events.append("pass-done")

    maintenance = resident.DehydrationMaintenance(
        projects=lambda: ["proof"], has_work=lambda _p: True, run_pass=run_pass,
        terminate=lambda: events.append("terminate"),
    )
    assert maintenance.tick() and started.wait(5)
    threading.Timer(0.3 if settle == "in-time" else 1.0, release.set).start()
    attempts: list[bool] = []

    def close_admission() -> None:
        # "unsettled": each bounded attempt ends with the pass still running.
        attempts.append(True)
        maintenance.shutdown_or_raise(timeout=30 if settle == "in-time" else 0.05)

    def record(name: str) -> Any:
        return lambda *args, **kwargs: events.append(name)

    runtime = SimpleNamespace(
        shutdown=lambda: None, shutdown_and_wait=lambda: None,
        feed=SimpleNamespace(wait_closed=lambda _t: None),
        agendas=SimpleNamespace(feed=SimpleNamespace(wait_closed=lambda _t: None)),
        observation_wait_closed=lambda _t: None,
    )
    from agent_worktrees import locks, status_monitor_cutover

    monkeypatch.setattr(status_monitor_cutover, "clear_route_if_owner", record("ownership-released"))
    monkeypatch.setattr(locks, "read_lock", lambda _lock: None)
    original = ValueError("monitor loop failed") if exit_mode == "loop-error" else None
    if settle == "unsettled":
        monkeypatch.setattr(resident, "SETTLE_LIMIT", 0.05)
        monkeypatch.setattr(resident, "SHUTDOWN_GRACE", 0.05)
    try:
        retirement.retire_monitor(
            close_admission=close_admission, repositories_runtime=runtime,
            worktrees_runtime=runtime, close_request_surfaces=lambda: None,
            control_server=SimpleNamespace(close=lambda: None),
            receipts=SimpleNamespace(wait_idle=lambda: None),
            worktree_status_runtime=runtime, managed_mux_runtime=runtime,
            lock="unused.lock", original_error=original,
            retirement_guard=SimpleNamespace(release=record("guard-release")),
        )
    except ValueError as exc:
        assert exc is original
    except RuntimeError as exc:  # the first pending-settlement error resurfaces after release
        assert original is None and settle == "unsettled" and "remains pending" in str(exc)
    else:
        assert original is None and settle == "in-time"
    finally:
        release.set()
    events = [event for event in events if event != "terminate"]
    assert events == ["pass-done", "ownership-released", "guard-release"]
    assert (len(attempts) > 1) == (settle == "unsettled")
