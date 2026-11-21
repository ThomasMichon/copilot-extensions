"""Tests for the ``session_conclude``/``session_link_succession`` verbs
(agent-worktrees-authoritative-daemon effort, Phase 3's fourth migrated
call-site cluster).

Covers the verb functions directly (the lifecycle-error rejections, the
head-clearing/moving behavior) and one end-to-end proof that
``tracking_write.dispatch`` reaches the exact same functions via a real,
live daemon.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from agent_worktrees import config as cfg, sessions, tracking_session_lifecycle_write, tracking_write
from agent_worktrees.tracking import (
    SessionEntry, WorktreeRecord, load_record, save_record,
)
from agent_worktrees.tracking_claims import ResourceClaim, format_claim_ref


def _entry(record: WorktreeRecord, session_id: str) -> SessionEntry:
    entry = record.session_entry(session_id)
    assert entry is not None
    return entry


@pytest.fixture(autouse=True)
def _clean_verb_registry() -> Iterator[None]:
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = cfg.Config(
        srcroot=str(tmp_tracking_dir), machine="test", platform="wsl", repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(
            anchor=str(tmp_tracking_dir), worktree_root=str(tmp_tracking_dir / "trees"),
        )},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **kwargs: config)
    path = tmp_tracking_dir / "wt-1.yaml"
    rec = WorktreeRecord(
        worktree_id="wt-1",
        branch="worktree/wt-1",
        worktree_path="/tmp/wt-1",
        repo="test-repo",
        machine="test",
        platform="wsl",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[SessionEntry("solo", "2026-01-01T00:00:00")],
    )
    rec.head_session = "solo"
    save_record(rec, path)
    return path


def test_importing_the_module_registers_both_verbs() -> None:
    verbs = tracking_write.registered_verbs()
    assert "session_conclude" in verbs
    assert "session_link_succession" in verbs
    assert tracking_write._VERB_VERSIONS["session_conclude"] == 2
    assert tracking_write._VERB_VERSIONS["session_link_succession"] == 2
    assert tracking_write._VERB_VERSIONS["session_resolve_handoff_successor"] == 2


def test_conclude_hands_off_and_clears_head(record_path: Path) -> None:
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "handed-off",
        }
    )
    assert result["ok"] is True
    assert result["state"] == "handed-off"
    assert result["head_session"] is None
    record = load_record(record_path)
    assert _entry(record, "solo").state == "handed-off"


def test_conclude_rejects_an_unknown_session(record_path: Path) -> None:
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "ghost",
            "state": "handed-off",
        }
    )
    assert result["error"] == "lifecycle"
    # Refused -- untouched.
    assert _entry(load_record(record_path), "solo").state == "active"


@pytest.mark.parametrize("initial_state", ["active", "concluded"])
def test_explicit_conclusion_recovers_only_finalized_at_rest_self_claim(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, initial_state: Literal["active", "concluded"],
) -> None:
    record = load_record(record_path)
    record.status = "finalized"
    _entry(record, "solo").state = initial_state
    own_ref = format_claim_ref(
        record.execution_machine, record.repo, record.worktree_id, session="solo",
    )
    record.resources = [
        ResourceClaim(kind="session", ref=own_ref, state="at-rest"),
        ResourceClaim(kind="session", ref="other/repo/tree#solo", state="at-rest"),
        ResourceClaim(kind="session", ref="other-session", state="active"),
        ResourceClaim(kind="worktree", ref="child", state="active"),
    ]
    save_record(record, record_path)
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda rec, **kwargs: SimpleNamespace(active=False, probes_ok=True),
    )
    args = {
        "worktree_id": "wt-1", "yaml_path": str(record_path),
        "session_id": "solo", "state": "concluded",
    }

    assert tracking_session_lifecycle_write.apply_session_conclude(args)["ok"]
    assert tracking_session_lifecycle_write.apply_session_conclude(args)["ok"]

    saved = load_record(record_path)
    assert _entry(saved, "solo").state == "concluded"
    assert [claim.state for claim in saved.resources] == [
        "released", "at-rest", "active", "active",
    ]


@pytest.mark.parametrize("active,probes_ok", [(True, True), (False, False)])
def test_session_claim_recovery_preserves_live_or_unknown_worktree(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, active: bool, probes_ok: bool,
) -> None:
    record = load_record(record_path)
    record.status = "finalized"
    record.resources = [ResourceClaim(
        kind="session",
        ref=format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id, session="solo",
        ),
        state="at-rest",
    )]
    save_record(record, record_path)
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda rec, **kwargs: SimpleNamespace(active=active, probes_ok=probes_ok),
    )

    result = tracking_session_lifecycle_write.apply_session_conclude({
        "worktree_id": "wt-1", "yaml_path": str(record_path),
        "session_id": "solo", "state": "concluded",
    })

    assert result["error"] == "lifecycle"
    saved = load_record(record_path)
    assert _entry(saved, "solo").state == "active"
    assert saved.resources[0].state == "at-rest"


@pytest.mark.parametrize("kind", ["session", "worktree"])
def test_session_claim_recovery_rejects_colliding_refs(record_path: Path, kind: str) -> None:
    record = load_record(record_path)
    record.status = "finalized"
    own_ref = format_claim_ref(
        record.execution_machine, record.repo, record.worktree_id, session="solo",
    )
    record.resources = [
        ResourceClaim(kind=kind, ref=own_ref, state="active"),
        ResourceClaim(kind="session", ref=own_ref, state="at-rest"),
    ]
    save_record(record, record_path)

    result = tracking_session_lifecycle_write.apply_session_conclude({
        "worktree_id": "wt-1", "yaml_path": str(record_path),
        "session_id": "solo", "state": "concluded",
    })

    assert result["error"] == "lifecycle"
    saved = load_record(record_path)
    assert _entry(saved, "solo").state == "active"
    assert [claim.state for claim in saved.resources] == ["active", "at-rest"]


@pytest.mark.parametrize("missing_directory", [True, False])
def test_session_claim_recovery_uses_strict_binding_observation(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing_directory: bool,
) -> None:
    from agent_worktrees import process_table_cache, session_liveness, sessions

    session_id = "00000000-0000-4000-8000-000000000001"
    record = load_record(record_path)
    record.status = "finalized"
    record.sessions = [SessionEntry(session_id, "2026-01-01T00:00:00")]
    record.head_session = session_id
    record.resources = [ResourceClaim(
        kind="session",
        ref=format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id, session=session_id,
        ),
        state="at-rest",
    )]
    save_record(record, record_path)
    state_dir = tmp_path / "session-state"
    state_dir.mkdir()
    if not missing_directory:
        directory = state_dir / session_id
        directory.mkdir()
        (directory / "inuse.123.lock").touch()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state_dir)
    monkeypatch.setattr(
        sessions, "mux_status_many", lambda ids, **kwargs: {"wt-1": sessions.MuxInfo()},
    )
    monkeypatch.setattr(process_table_cache, "cached", lambda build: {})
    queried: list[int] = []

    def unavailable(pid: int) -> None:
        queried.append(pid)
        return None

    monkeypatch.setattr(session_liveness, "copilot_pid_state", unavailable)

    result = tracking_session_lifecycle_write.apply_session_conclude({
        "worktree_id": "wt-1", "yaml_path": str(record_path),
        "session_id": session_id, "state": "concluded",
    })

    assert result["error"] == "lifecycle"
    assert queried == ([] if missing_directory else [123])
    saved = load_record(record_path)
    assert _entry(saved, session_id).state == "active"
    assert saved.resources[0].state == "at-rest"


def test_refused_session_claim_release_does_not_conclude(record_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import claim_handoffs

    record = load_record(record_path)
    record.status = "finalized"
    record.resources = [ResourceClaim(
        kind="session",
        ref=format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id, session="solo",
        ),
        state="at-rest",
    )]
    save_record(record, record_path)
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda rec, **kwargs: SimpleNamespace(active=False, probes_ok=True),
    )

    def unavailable(*args: Any) -> None:
        raise OSError("handoff registry unavailable")

    monkeypatch.setattr(claim_handoffs, "active_bundle_for_claim", unavailable)
    before = record_path.read_bytes()

    result = tracking_session_lifecycle_write.apply_session_conclude({
        "worktree_id": "wt-1", "yaml_path": str(record_path),
        "session_id": "solo", "state": "concluded",
    })

    assert result["error"] == "lifecycle"
    assert "release" in result["message"]
    assert record_path.read_bytes() == before


@pytest.mark.parametrize("reactivate", [None, "before-recovery", "before-cleanup"])
def test_recovery_composes_with_guarded_cleanup_and_reactivation(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reactivate: str | None,
) -> None:
    from _cleanup_revalidation_helpers import CleanupHarness, S, info
    from agent_worktrees import config_authority, tracking_session_registration_write

    root = tmp_path / "cleanup"
    root.mkdir()
    harness = CleanupHarness(monkeypatch, root)
    record = load_record(record_path)
    authority = cfg.load_project_config(record.repo)
    monkeypatch.setattr(
        config_authority, "load_project_authority_config", lambda name, **kwargs: authority,
    )
    record.status = "finalized"
    record.worktree_path = str(harness.worktree_root / record.worktree_id)
    Path(record.worktree_path).mkdir()
    own_ref = format_claim_ref(
        record.execution_machine, record.repo, record.worktree_id, session="solo",
    )
    record.resources = [ResourceClaim(kind="session", ref=own_ref, state="at-rest")]
    path = harness.seed(record)
    harness.set_classifier(info(S.COMPLETED))
    resumed = False
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda rec, **kwargs: SimpleNamespace(active=resumed, probes_ok=True),
    )

    def resume() -> None:
        nonlocal resumed
        result = tracking_session_registration_write.apply_session_register({
            "worktree_id": record.worktree_id, "yaml_path": str(path),
            "session_id": "solo", "source_identity": "resumed-launch",
        })
        assert result["ok"]
        resumed = True

    if reactivate == "before-recovery":
        resume()
    result = tracking_session_lifecycle_write.apply_session_conclude({
        "worktree_id": record.worktree_id, "yaml_path": str(path),
        "session_id": "solo", "state": "concluded",
    })
    if reactivate == "before-recovery":
        assert result["error"] == "lifecycle"
    else:
        assert result["ok"]
    if reactivate == "before-cleanup":
        harness.set_finalize_lock(on_acquire=resume)

    outcome = harness.run_reap_one(record.worktree_id)

    if reactivate:
        assert outcome["removed"] is False
        assert harness.reaped == []
        assert Path(record.worktree_path).exists()
        renewed = load_record(path)
        assert renewed.status == "finalized"
        assert renewed.resources[0].state == "at-rest"
        activation = _entry(renewed, "solo").activations[-1]
        assert activation.source_identity == "resumed-launch"
        assert activation.ended_at is None
    else:
        assert outcome["removed"] is True
        assert harness.reaped == [(record.worktree_id, "completed")]


@pytest.mark.parametrize(
    "status,state,claim_state",
    [("active", "concluded", "at-rest"),
     ("finalized", "handed-off", "at-rest"),
     ("finalized", "concluded", "active")],
)
def test_ordinary_conclusion_does_not_release_unqualified_session_claims(
    record_path: Path, status: Literal["active", "finalized"], state: str, claim_state: str,
) -> None:
    record = load_record(record_path)
    record.status = status
    record.resources = [ResourceClaim(
        kind="session",
        ref=format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id, session="solo",
        ),
        state=claim_state,
    )]
    save_record(record, record_path)

    result = tracking_session_lifecycle_write.apply_session_conclude({
        "worktree_id": "wt-1", "yaml_path": str(record_path),
        "session_id": "solo", "state": state,
    })

    assert result["ok"]
    assert load_record(record_path).resources[0].state == claim_state


def test_conclude_only_if_active_no_ops_an_already_concluded_session(record_path: Path) -> None:
    """The ``only_if_active`` guard (added for ``handoff_cutover.py``'s
    ``_conclude_retired_predecessor`` repair) must never re-process a
    session whose ``SessionEntry.state`` isn't ``"active"`` -- a repair
    that raced behind some other transition must leave it alone."""
    tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "handed-off",
        }
    )
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "concluded",
            "only_if_active": True,
        }
    )
    assert result == {"ok": True, "skipped": "not_active"}
    assert _entry(load_record(record_path), "solo").state == "handed-off"


def test_conclude_only_if_active_still_concludes_an_active_session(record_path: Path) -> None:
    """Confirms the guard is opt-in only and does not block a genuine
    concluding of a currently-``active`` session."""
    result = tracking_session_lifecycle_write.apply_session_conclude(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "session_id": "solo",
            "state": "concluded",
            "only_if_active": True,
        }
    )
    assert result["ok"] is True
    assert result["state"] == "concluded"
    assert _entry(load_record(record_path), "solo").state == "concluded"


def test_link_succession_writes_two_way_chain_and_moves_head(record_path: Path) -> None:
    record = load_record(record_path)
    assert record.sessions is not None
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    save_record(record, record_path)

    result = tracking_session_lifecycle_write.apply_session_link_succession(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "predecessor": "solo",
            "successor": "new",
            "predecessor_state": "handed-off",
        }
    )
    assert result["ok"] is True
    assert result["head_session"] == "new"
    assert result["predecessor_state"] == "handed-off"
    record = load_record(record_path)
    assert _entry(record, "solo").successor == "new"
    assert _entry(record, "new").predecessor == "solo"
    assert record.head_session == "new"


def test_link_succession_rejects_an_unknown_session(record_path: Path) -> None:
    result = tracking_session_lifecycle_write.apply_session_link_succession(
        {
            "worktree_id": "wt-1",
            "yaml_path": str(record_path),
            "predecessor": "solo",
            "successor": "ghost",
            "predecessor_state": "handed-off",
        }
    )
    assert result["error"] == "lifecycle"


def test_link_succession_feeds_claim_history_once_for_an_explicit_token_retry(
    record_path: Path,
) -> None:
    """worktree-claims-transitive-finalization Phase 3b: a retried call with
    the SAME explicit ``handoff_token`` that's already linked to this exact
    successor is an idempotent replay -- ownership did not change a second
    time, so it must not append a second "reassigned" entry."""
    from agent_worktrees import claim_history, obligations
    record = load_record(record_path)
    assert record.sessions is not None
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    args = {
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "handed-off",
        "handoff_token": "token-1",
    }
    result1 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result1["ok"] is True
    result2 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]


def test_link_succession_feeds_claim_history_once_for_a_default_tokenless_retry(
    record_path: Path,
) -> None:
    """A retry with NO explicit ``handoff_token`` (the CLI's default) makes
    `link_succession` generate a fresh ``manual-<n>`` token on every call --
    the handoff-token-presence check alone would miss this, so idempotency
    must come from the succession link itself already being in place."""
    from agent_worktrees import claim_history, obligations
    record = load_record(record_path)
    assert record.sessions is not None
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    args = {
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "handed-off",
    }
    result1 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result1["ok"] is True
    result2 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]


def test_link_succession_records_a_fresh_reassignment_after_head_moved_elsewhere(
    record_path: Path,
) -> None:
    """The two-way link fields alone are NOT a sufficient idempotency
    signal: if some other mechanism moves the resolved head elsewhere
    later while leaving this pair's predecessor.successor/
    successor.predecessor fields intact, a replay of the SAME pair
    genuinely moves head (and ownership) back to the successor -- that
    must fire a second "reassigned" entry, not be suppressed as a no-op."""
    from agent_worktrees import claim_history, obligations, tracking_lifecycle

    record = load_record(record_path)
    assert record.sessions is not None
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.sessions.append(SessionEntry("third", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    args = {
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "handed-off",
    }
    result1 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result1["ok"] is True

    # Head moves elsewhere via some other mechanism, leaving the
    # solo->new link fields themselves untouched.
    record = load_record(record_path)
    tracking_lifecycle._append_head_transition(record, "third", reason="test-rebind")
    save_record(record, record_path)

    result2 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned", "reassigned"]


def test_link_succession_feeds_claim_history_once_for_a_concluded_retry(
    record_path: Path,
) -> None:
    """The ``concluded`` (or any non-``handed-off``) ``predecessor_state``
    takes `link_succession`'s direct branch, which never touches
    ``record.handoffs`` at all -- idempotency there must be detected from
    the succession link itself already being in place, not a handoff
    token, or a retried call with the identical args duplicates the
    "reassigned" entry even though ownership did not change again."""
    from agent_worktrees import claim_history, obligations
    record = load_record(record_path)
    assert record.sessions is not None
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    args = {
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "concluded",
        "handoff_token": "token-1",
    }
    result1 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result1["ok"] is True
    result2 = tracking_session_lifecycle_write.apply_session_link_succession(args)
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]


def test_link_succession_does_not_duplicate_on_a_predecessor_state_repair(
    record_path: Path,
) -> None:
    """A second call repairing only the predecessor's recorded state (e.g.
    correcting ``handed-off`` to ``concluded``) for the SAME, already-settled
    predecessor/successor link must not count as a fresh reassignment -- the
    acting successor never changed, only predecessor metadata did."""
    from agent_worktrees import claim_history, obligations
    record = load_record(record_path)
    assert record.sessions is not None
    record.sessions.append(SessionEntry("new", "2026-01-01T00:00:00"))
    record.resources = [
        ResourceClaim(kind="pr", ref="o/r#1", state=obligations.ACTIVE),
    ]
    save_record(record, record_path)

    result1 = tracking_session_lifecycle_write.apply_session_link_succession({
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "handed-off",
    })
    assert result1["ok"] is True
    result2 = tracking_session_lifecycle_write.apply_session_link_succession({
        "worktree_id": "wt-1",
        "yaml_path": str(record_path),
        "predecessor": "solo",
        "successor": "new",
        "predecessor_state": "concluded",
    })
    assert result2["ok"] is True

    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["reassigned"]


def test_dispatch_reaches_session_conclude_through_a_live_daemon(record_path: Path) -> None:
    """End-to-end: proves ``tracking_write.dispatch`` reaches
    ``apply_session_conclude`` via an actual ``CoalescingServer``, the
    two-process-shaped path real production traffic will use."""
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        result = tracking_write.dispatch(
            "session_conclude",
            {
                "worktree_id": "wt-1",
                "yaml_path": str(record_path),
                "session_id": "solo",
                "state": "concluded",
            },
            read_lock_data=lambda: lock_data,
            ensure_monitor=None,
        )
    finally:
        server.close()
    assert result["ok"] is True
    record = load_record(record_path)
    assert _entry(record, "solo").state == "concluded"
