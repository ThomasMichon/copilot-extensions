"""Storage-only acceptance for the independently addressable current roster."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_worktrees import worktrees_projection as projection


def _row(wid="worktree-one", **extra):
    return {"id": wid, "state": "wip", "title": "Current task", **extra}


def test_fact_only_publication_reuses_complete_identity_and_filter_indexes(tmp_path, monkeypatch):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    first = _row(status="active", platform="windows", checkout_present=True)
    store.publish("project", [first], observed_at=1)
    monkeypatch.setattr(
        projection, "SelectionIndex", lambda _: pytest.fail("unchanged identity index rebuilt"),
    )
    monkeypatch.setattr(
        type(store._projects["project"].index), "persist",
        lambda *a: pytest.fail("unchanged identity index rewritten"),
    )
    second = {**first, "state": "merged"}
    store.publish("project", [second], observed_at=2)
    assert store.read("project", worktree_id="one")["worktrees"] == [second]
    assert projection.read_advisory(
        path, "project", status_filter="active", platform_filter="windows",
        include_missing=False,
    )["worktrees"] == [second]


def test_cold_and_restarted_live_reads_are_explicitly_unready(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    with pytest.raises(projection.ProjectionUnavailable):
        store.read("project")
    assert not path.exists()
    store.publish("project", [_row()], observed_at=10)
    store.close()
    restarted = projection.WorktreesProjection(path)
    with pytest.raises(projection.ProjectionUnavailable):
        restarted.read("project")
    assert projection.read_advisory(path, "project")["projection"]["quality"] == "advisory"
    with pytest.raises(projection.ProjectionUnavailable):
        projection.read_advisory(tmp_path / "missing.sqlite3", "project")
    assert not (tmp_path / "missing.sqlite3").exists()


def test_publication_is_atomic_replaces_deletes_and_preserves_project_isolation(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row("one"), _row("two")], observed_at=10)
    store.publish("other", [_row("three")], observed_at=11)
    store.publish("project", [_row("one", state="merged")], observed_at=12)
    live = store.read("project")
    assert live["worktrees"] == [_row("one", state="merged")]
    assert live["projection"]["revision"] == 2
    assert live["projection"]["observed_at"] == 12
    assert live["projection"]["quality"] == "current"
    assert store.read("project", worktree_id="two")["worktrees"] == []
    advisory = projection.read_advisory(path, "project")
    assert advisory["worktrees"] == live["worktrees"]
    assert advisory["projection"] == {**live["projection"], "quality": "advisory"}
    assert store.read("other")["worktrees"] == [_row("three")]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM worktrees_projection_row").fetchone()[0] == 2


def test_invalid_replacement_never_replaces_last_complete_publication(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row()], observed_at=1)
    with pytest.raises(projection.ProjectionError, match="duplicate"):
        store.publish("project", [_row(), _row()], observed_at=2)
    assert store.read("project")["projection"]["revision"] == 1
    assert projection.read_advisory(path, "project")["projection"]["revision"] == 1


def test_inputs_and_results_cannot_mutate_published_state(tmp_path):
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    row = _row(closure={"eligibility": {"safe": False}})
    store.publish("project", [row], observed_at=1)
    row["closure"]["eligibility"]["safe"] = True
    first = store.read("project")
    first["worktrees"][0]["closure"]["eligibility"]["safe"] = True
    assert store.read("project")["worktrees"][0]["closure"]["eligibility"]["safe"] is False


@pytest.mark.parametrize("field", ["resources", "prs", "sessions", "handoffs", "journal"])
def test_history_is_not_a_current_row_field(tmp_path, field):
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    with pytest.raises(projection.ProjectionError):
        store.publish("project", [_row(**{field: []})], observed_at=1)
    with pytest.raises(projection.ProjectionError):
        store.publish("project", [_row(closure={field: []})], observed_at=1)


@pytest.mark.parametrize("extra", [
    {"title": "x" * (projection.MAX_TEXT_BYTES + 1)},
    {"live_session_ids": ["s"] * (projection.MAX_COLLECTION_ITEMS + 1)},
    {"closure": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 0}}}}}}}},
    {"ahead": float("inf")},
    {"ahead": 2**63},
    {"state": []},
    {"dirty": "false"},
    {"dirty": object()},
])
def test_schema_bounds_refuse_instead_of_truncating(tmp_path, extra):
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    with pytest.raises(projection.ProjectionError):
        store.publish("project", [_row(**extra)], observed_at=1)


def test_point_reads_decode_one_slot_independent_of_unrelated_roster(tmp_path, monkeypatch):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row()] + [_row(f"other-{i}") for i in range(1100)], observed_at=1)
    decoded = []
    original = projection._decode_row

    def counted(wid, payload):
        decoded.append((wid, len(payload.encode("utf-8"))))
        return original(wid, payload)

    monkeypatch.setattr(projection, "_decode_row", counted)
    assert store.read("project", worktree_id="worktree-one")["worktrees"] == [_row()]
    assert projection.read_advisory(
        path, "project", worktree_id="worktree-one",
    )["worktrees"] == [_row()]
    assert decoded == [("worktree-one", decoded[0][1])] * 2
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE worktrees_projection_row SET payload=? WHERE worktree_id LIKE 'other-%'",
            ("not current JSON",),
        )
    assert projection.read_advisory(
        path, "project", worktree_id="worktree-one",
    )["worktrees"] == [_row()]


def test_durable_oversize_and_identity_corruption_are_explicit(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row()], observed_at=1)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE worktrees_projection_row SET payload=?",
            ("x" * (projection.MAX_ROW_BYTES + 1),),
        )
    with pytest.raises(projection.ProjectionError, match="byte bound"):
        projection.read_advisory(path, "project", worktree_id="worktree-one")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE worktrees_projection_row SET payload=?", (json.dumps(_row("wrong")),),
        )
    with pytest.raises(projection.ProjectionError, match="identity mismatch"):
        projection.read_advisory(path, "project", worktree_id="worktree-one")


def test_failed_durability_does_not_publish_partial_live_state(tmp_path, monkeypatch):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row()], observed_at=1)

    def refused(*args, **kwargs):
        raise sqlite3.OperationalError("writer unavailable")

    monkeypatch.setattr(projection.sqlite3, "connect", refused)
    with pytest.raises(sqlite3.OperationalError):
        store.publish("project", [_row(state="merged")], observed_at=2)
    assert store.read("project")["worktrees"] == [_row()]
    assert store.read("project")["projection"]["revision"] == 1


def test_closed_publisher_cannot_accept_or_serve_new_current_rows(tmp_path):
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    store.publish("project", [_row()], observed_at=1)
    store.close()
    with pytest.raises(projection.ProjectionUnavailable):
        store.publish("project", [_row()], observed_at=2)
    with pytest.raises(projection.ProjectionUnavailable):
        store.read("project")


def test_incomplete_durable_roster_is_not_an_empty_success(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row()], observed_at=1)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM worktrees_projection_row")
    with pytest.raises(projection.ProjectionError, match="incomplete"):
        projection.read_advisory(path, "project")
    store.publish("project", [], observed_at=2)
    assert projection.read_advisory(path, "project")["worktrees"] == []
    assert projection.read_advisory(path, "project")["projection"]["roster_count"] == 0


def test_total_row_byte_bound_is_not_just_a_field_size_bound(tmp_path):
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    row = _row()
    scalar_fields = projection.ROW_FIELDS - (
        projection._BOOL_FIELDS | projection._INT_FIELDS |
        projection._OBJECT_FIELDS | projection._ARRAY_FIELDS | {"id"}
    )
    row.update({field: "x" * projection.MAX_TEXT_BYTES for field in scalar_fields})
    with pytest.raises(projection.ProjectionError, match="byte bound"):
        store.publish("project", [row], observed_at=1)


def test_slow_persistence_never_holds_the_current_read_lock(tmp_path, monkeypatch):
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    store.publish("project", [_row()], observed_at=1)
    entered = threading.Event()
    release = threading.Event()
    connect = projection.sqlite3.connect

    def slow_connect(*args, **kwargs):
        entered.set()
        if not release.wait(2):
            raise AssertionError("publisher was not released")
        return connect(*args, **kwargs)

    monkeypatch.setattr(projection.sqlite3, "connect", slow_connect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        published = executor.submit(
            store.publish, "project", [_row(state="merged")], observed_at=2,
        )
        try:
            assert entered.wait(2)
            assert store.read("project", worktree_id="worktree-one")["worktrees"] == [_row()]
        finally:
            release.set()
        published.result(timeout=2)
    assert store.read("project")["worktrees"] == [_row(state="merged")]


def test_filters_precede_exact_and_unique_suffix_selection(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    rows = [
        _row("one-tail", status="active", platform="windows", checkout_present=True),
        _row("two-tail", status="merged", platform="linux", checkout_present=True),
        _row("tail", status="active", platform="linux", checkout_present=False),
    ]
    store.publish("project", rows, observed_at=1)
    for read in (store.read, lambda project, **kw: projection.read_advisory(path, project, **kw)):
        assert read("project", worktree_id="tail")["worktrees"] == [rows[2]]
        assert read(
            "project", worktree_id="tail", platform_filter="windows",
        )["worktrees"] == rows[:1]
        assert read(
            "project", worktree_id="tail", status_filter="merged",
        )["worktrees"] == rows[1:2]
        assert read(
            "project", platform_filter="linux", include_missing=False,
        )["worktrees"] == rows[1:2]
        with pytest.raises(projection.ProjectionError, match="Ambiguous short ID"):
            read("project", worktree_id="tail", include_missing=False)
        assert read("project", status_filter="unknown")["worktrees"] == []
        assert read("project", worktree_id="missing")["worktrees"] == []


def test_codename_is_case_sensitive_and_ambiguity_precedes_filters(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    rows = [
        _row("one", codename="quiet-knob", status="active"),
        _row("two", codename="quiet-knob", status="merged"),
        _row("three", codename="Quiet-Knob", status="active"),
    ]
    store.publish("project", rows, observed_at=1)
    for read in (store.read, lambda project, **kw: projection.read_advisory(path, project, **kw)):
        with pytest.raises(projection.ProjectionError, match="Ambiguous codename"):
            read("project", codename="quiet-knob", status_filter="active")
        assert read("project", codename="Quiet-Knob")["worktrees"] == rows[2:]
        assert read("project", codename="Quiet-Knob", status_filter="merged")["worktrees"] == []
        assert read("project", codename="QUIET-KNOB")["worktrees"] == []
        assert read("project", worktree_id="one", codename="quiet-knob")["worktrees"] == rows[:1]


def test_unknown_checkout_presence_is_unready_not_silently_dropped(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    rows = [
        _row("unknown", status="active"),
        _row("known", status="merged", checkout_present=True),
    ]
    store.publish("project", rows, observed_at=1)
    for read in (store.read, lambda project, **kw: projection.read_advisory(path, project, **kw)):
        with pytest.raises(projection.ProjectionUnavailable, match="checkout presence"):
            read("project", include_missing=False)
        assert read(
            "project", include_missing=False, status_filter="merged",
        )["worktrees"] == rows[1:]


def test_replacement_removes_stale_codenames_suffixes_and_filters(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish(
        "project", [_row("old-tail", codename="old-name", status="merged")], observed_at=1,
    )
    new = _row("new-tail", codename="new-name", status="active", checkout_present=True)
    store.publish("project", [new], observed_at=2)
    for read in (store.read, lambda project, **kw: projection.read_advisory(path, project, **kw)):
        assert read("project", codename="old-name")["worktrees"] == []
        assert read("project", status_filter="merged")["worktrees"] == []
        assert read("project", worktree_id="old-tail")["worktrees"] == []
        assert read("project", worktree_id="tail")["worktrees"] == [new]


def test_indexed_selectors_do_not_visit_or_decode_unrelated_rows(tmp_path, monkeypatch):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    target = _row("prefix-target", codename="quiet-knob", status="active", platform="windows")
    store.publish("project", [target] + [
        _row(f"other-{i}", status="merged", platform="linux") for i in range(1100)
    ], observed_at=1)
    decoded = []
    original = projection._decode_row

    def counted(wid, payload):
        decoded.append(wid)
        return original(wid, payload)

    class PointOnly(dict):
        def __iter__(self):
            raise AssertionError("point read enumerated the roster")

        def items(self):
            raise AssertionError("point read enumerated the roster")

    publication = store._projects["project"]
    object.__setattr__(publication, "rows", PointOnly(publication.rows))
    monkeypatch.setattr(projection, "_decode_row", counted)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE worktrees_projection_row SET payload='invalid' "
            "WHERE worktree_id LIKE 'other-%'",
        )
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT worktree_id FROM worktrees_projection_selector "
            "WHERE project=? AND scope=? AND selector=?", ("project", "codename", "quiet-knob"),
        ).fetchall()
        assert any("SEARCH" in entry[3] and "INDEX" in entry[3] for entry in plan)
        assert not any("SCAN" in entry[3] for entry in plan)
    for read in (store.read, lambda project, **kw: projection.read_advisory(path, project, **kw)):
        for selector in ({"worktree_id": "target"}, {"codename": "quiet-knob"}):
            assert read(
                "project", status_filter="active", platform_filter="windows", **selector,
            )["worktrees"] == [target]
    assert decoded == ["prefix-target"] * 4


def test_ambiguous_selector_diagnostics_are_bounded(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row(f"other-{i}-tail") for i in range(1100)], observed_at=1)
    for read in (store.read, lambda project, **kw: projection.read_advisory(path, project, **kw)):
        with pytest.raises(projection.ProjectionError, match="matches 1100") as caught:
            read("project", worktree_id="tail")
        assert len(str(caught.value).encode("utf-8")) < 512


def test_only_explicit_generation_handover_can_replace_the_durable_writer(tmp_path):
    path = tmp_path / "current.sqlite3"
    first = projection.WorktreesProjection(path)
    first.publish("project", [_row()], observed_at=1)
    replacement = projection.WorktreesProjection(path)
    with pytest.raises(projection.ProjectionUnavailable, match="generation changed"):
        replacement.publish("project", [_row(state="merged")], observed_at=2)
    assert projection.read_advisory(path, "project")["worktrees"] == [_row()]
    replacement.activate(expected_generation=first.generation)
    with pytest.raises(projection.ProjectionUnavailable, match="not published"):
        replacement.read("project")
    replacement.publish("project", [_row(state="merged")], observed_at=2)
    with pytest.raises(projection.ProjectionUnavailable, match="superseded"):
        first.publish("project", [_row(state="dirty")], observed_at=3)
    with pytest.raises(projection.ProjectionUnavailable):
        first.read("project")
    assert replacement.read("project")["worktrees"] == [_row(state="merged")]
    assert (
        projection.read_advisory(path, "project")["projection"]["generation"]
        == replacement.generation
    )


def test_late_owner_cannot_reclaim_or_reuse_a_successor_generation(tmp_path):
    path = tmp_path / "current.sqlite3"
    first = projection.WorktreesProjection(path)
    first.activate()
    replacement = projection.WorktreesProjection(path)
    replacement.activate(expected_generation=first.generation)
    with pytest.raises(projection.ProjectionUnavailable, match="superseded"):
        first.activate(expected_generation=replacement.generation)
    duplicate = projection.WorktreesProjection(path, generation=replacement.generation)
    with pytest.raises(projection.ProjectionError, match="new generation"):
        duplicate.activate(expected_generation=replacement.generation)
    replacement.publish("project", [_row()], observed_at=1)
    replacement.activate()
    assert replacement.read("project")["projection"]["revision"] == 1


def test_advisory_read_never_claims_publication_ownership(tmp_path):
    path = tmp_path / "current.sqlite3"
    store = projection.WorktreesProjection(path)
    store.publish("project", [_row()], observed_at=1)
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT * FROM worktrees_projection_owner").fetchall()
    projection.read_advisory(path, "project", worktree_id="one")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM worktrees_projection_owner").fetchall() == before
