"""Owner-side current serialization preserves aggregates, not growing ledgers."""

from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import git_ops, tracking
from agent_worktrees import worktrees_current_rows as current
from agent_worktrees import worktrees_projection as projection


def test_worktrees_current_rows_bound_links_and_keep_exact_asset_totals(tmp_path):
    record = SimpleNamespace(resources=[
        tracking.ResourceClaim(kind="worktree", ref=f"child-{i}", state="active")
        for i in range(1100)
    ] + [
        tracking.ResourceClaim(kind="pr", ref="held", state="at-rest"),
        tracking.ResourceClaim(kind="pr", ref="gone", state="released"),
    ])
    entries = [{"label": f"child-{i}", "url": f"https://example/{i}"} for i in range(1100)]
    row = {"id": "owner"}
    current.add_claims(row, record, entries, current_only=True)
    assert row["claims_assets"] == {
        "held_count": 1101, "by_kind": {"worktree": 1100, "pr": 1}, "details_available": True,
    }
    assert row["claims_link_count"] == 1100
    assert row["claims_links_complete"] is False
    assert len(row["claims_links"]) == current.MAX_CURRENT_CLAIM_LINKS
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    store.publish("project", [row], observed_at=1)
    assert store.read("project", worktree_id="owner")["worktrees"] == [row]


def test_worktrees_current_rows_keep_legacy_claim_summary_and_details_unchanged():
    record = SimpleNamespace(resources=[])
    entries = [{"label": str(i), "url": str(i)} for i in range(100)]
    row = {}
    current.add_claims(row, record, entries, current_only=False)
    assert row == {"claims_summary": " \u00b7 ".join(str(i) for i in range(100)),
                   "claims_links": entries}


def test_worktrees_current_rows_bound_summary_bytes_not_just_link_count():
    row = {}
    current.add_claims(
        row, SimpleNamespace(resources=[]),
        [{"label": "x" * projection.MAX_TEXT_BYTES}] * 10, current_only=True,
    )
    assert len(row["claims_summary"].encode("utf-8")) == projection.MAX_TEXT_BYTES
    assert len(row["claims_links"]) == 1
    assert row["claims_links_complete"] is False
    assert row["claims_link_count"] == 10


def test_worktrees_current_rows_do_not_hide_claim_materialization_errors(monkeypatch):
    def unavailable():
        raise OSError("claim registry unavailable")

    monkeypatch.setattr(core.claim_kinds_registry, "effective_pecking_order", unavailable)
    with pytest.raises(OSError, match="registry unavailable"):
        current.populate_claims({}, SimpleNamespace(resources=[], repo="project"), None,
                                current_only=True)


def test_worktrees_current_rows_materialization_is_producer_only_and_marks_unknown_facts(tmp_path):
    path = tmp_path / "checkout"
    path.mkdir()
    (path / ".git").write_text("gitdir: unused")
    record = SimpleNamespace(worktree_id="one", worktree_path=str(path))
    calls = []

    def serialize(rec, **kwargs):
        calls.append((rec, kwargs))
        return {"id": rec.worktree_id}

    rows = current.materialize(
        [record], serialize=serialize, observed_at=123, state_map={}, mux_map={},
        session_ctx=None, bare_orphan_wts=None, bridge_live_wts=None,
    )
    assert calls[0][1]["current_only"] is True
    assert rows[0]["checkout_present"] is True
    assert rows[0]["fact_provenance"]["record"]["quality"] == "current"
    assert rows[0]["fact_provenance"]["git"]["quality"] == "unavailable"
    assert rows[0]["fact_provenance"]["session"]["observed_at"] is None
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    store.publish("project", rows, observed_at=123)
    calls.clear()
    store.read("project", worktree_id="one")
    projection.read_advisory(tmp_path / "current.sqlite3", "project", worktree_id="one")
    assert calls == []


@pytest.mark.parametrize("yielded", [False, True])
def test_worktrees_current_rows_real_engine_serialization_preserves_counts_and_git_state(
    tmp_path, yielded,
):
    record = tracking.WorktreeRecord(
        worktree_id="one", branch="worktree/one", worktree_path=str(tmp_path),
        repo="project", machine="host", platform="windows",
        started_at="2026-10-13T00:00:00", last_resumed_at="2026-10-13T00:00:00",
        resume_count=0, title="Current task", status="active", completed_at=None,
        sessions=None, prs=[tracking.PRRecord(state="open", branch="worktree/one", number=7)],
        resources=[tracking.ResourceClaim(kind="worktree", ref=f"child-{i}") for i in range(1000)],
        creation_nonce="current-creation",
    )
    if yielded:
        record.sessions = [
            tracking.SessionEntry(
                session_id="yielded-head", started_at=record.started_at, state="yielded",
            ),
        ]
        record.head_transitions = [
            tracking.HeadTransition(1, "yielded-head", "handoff", record.started_at),
        ]
        assert record.resolved_head_session is None
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.WIP, ahead=3, behind=2, dirty=4)
    row = core._worktree_to_dict(record, state_info=info, current_only=True)
    assert "resources" not in row and "prs" not in row and "sessions" not in row
    assert row["claims_assets"]["held_count"] == 1000
    assert row["claims_assets"]["by_kind"] == {"worktree": 1000}
    assert row["pr_count"] == 1 and row["pr"]["number"] == 7
    assert row["dirty"] == 4
    assert row["creation_nonce"] == "current-creation"
    if yielded:
        assert row["head_yielded"] is True
        assert row["last_session_id"] == "yielded-head"
    else:
        assert "head_yielded" not in row
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    store.publish("project", [row], observed_at=123)
    assert store.read("project", worktree_id="one")["worktrees"] == [row]
    assert projection.read_advisory(
        tmp_path / "current.sqlite3", "project", worktree_id="one",
    )["worktrees"] == [row]


@pytest.mark.parametrize(
    ("field", "value"), [("creation_nonce", True), ("head_yielded", "true")],
)
def test_worktrees_current_rows_reject_invalid_current_identity_field_types(tmp_path, field, value):
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    with pytest.raises(projection.ProjectionError, match=f"field type: {field}"):
        store.publish("project", [{"id": "one", field: value}], observed_at=123)
