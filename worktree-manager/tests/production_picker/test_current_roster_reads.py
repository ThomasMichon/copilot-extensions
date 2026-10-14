"""Local repaint and row refresh cannot run the old reconcile overlay."""

import pytest

from worktree_manager import engine_client
from worktree_manager.production_picker.picker_tui import data_local
from worktree_manager.worktree_rows_contract import CurrentSnapshot, parse_current_rows


def envelope(rows, *, quality="current"):
    value = {
        "ok": True,
        "contract_version": 1,
        "worktrees": rows,
        "projection": {
            "schema_version": 1,
            "quality": quality,
            "generation": "a" * 32,
            "revision": 1,
            "observed_at": 123,
            "roster_count": len(rows),
            "selected_count": len(rows),
        },
    }
    if quality == "advisory":
        value["availability"] = {"status": "advisory", "reason": "resident unavailable"}
    return value


@pytest.mark.parametrize("classify", [False, True])
def test_current_local_load_never_runs_reconcile_or_overlays(monkeypatch, classify):
    def forbidden(*args, **kwargs):
        pytest.fail("current rows reached caller-side reconcile/overlay")

    monkeypatch.setattr(data_local, "reconcile_local_batch", forbidden)
    monkeypatch.setattr(data_local, "_overlay_reconcile_rows", forbidden)
    monkeypatch.setattr(data_local.context, "project", lambda: "project")
    rows = parse_current_rows(envelope([{"id": "one", "state": "wip", "dirty": 3}]))
    calls = []
    monkeypatch.setattr(
        engine_client,
        "list_worktree_rows",
        lambda project, **kwargs: calls.append((project, kwargs)) or rows,
    )
    result = data_local.load("host", "Win", classify=classify)
    assert len(result) == 1 and result[0]["raw"]["dirty"] == 3
    assert result[0]["raw"]["_projection"]["quality"] == "current"
    assert calls[0][0] == "project"


def test_current_point_refresh_does_not_reconcile_or_enumerate_fleet(monkeypatch):
    monkeypatch.setattr(data_local.context, "project", lambda: "project")
    monkeypatch.setattr(
        data_local,
        "_overlay_reconcile_rows",
        lambda *a, **kw: pytest.fail("point refresh reconciled"),
    )
    calls = []
    rows = parse_current_rows(envelope([{"id": "one", "state": "wip"}]))
    monkeypatch.setattr(
        engine_client,
        "list_worktree_rows",
        lambda project, **kwargs: calls.append(kwargs) or rows,
    )
    result = data_local.refresh_one("one", "host", "Win")
    assert result["id"] == "one" and calls[0]["worktree_id"] == "one"


def test_current_advisory_rows_retain_provenance_and_are_visibly_labeled(monkeypatch):
    monkeypatch.setattr(data_local.context, "project", lambda: "project")
    rows = parse_current_rows(
        envelope([{"id": "one", "title": "Current task"}], quality="advisory")
    )
    monkeypatch.setattr(engine_client, "list_worktree_rows", lambda *a, **kw: rows)
    result = data_local.load("host", "Win")
    assert result[0]["title"] == "[advisory] Current task"
    assert result[0]["raw"]["_availability"]["reason"] == "resident unavailable"


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"worktrees": []},
        {"ok": True, "contract_version": True},
        {**envelope([]), "projection": {}},
        {**envelope([]), "worktrees": [None]},
        envelope([{"id": "one"}, {"id": "one"}]),
        {**envelope([], quality="advisory"), "availability": None},
        {**envelope([{"id": "one"}]), "worktrees": []},
        envelope([{"id": "one", "resources": []}]),
        envelope([{"id": "one", "controllers": [None] * 129}]),
        envelope([{"id": "one", "title": "x" * 4097}]),
    ],
)
def test_current_incompatible_and_malformed_rows_fail_explicitly(value):
    with pytest.raises((TypeError, ValueError)):
        parse_current_rows(value)


@pytest.mark.parametrize("frame", [None, [], [1], "invalid", 1, True])
@pytest.mark.parametrize("started", [False, True])
def test_current_snapshot_rejects_non_object_frames_explicitly(frame, started):
    snapshot = CurrentSnapshot()
    if started:
        owner = envelope([{"id": "one"}])
        snapshot.receive({
            "type": "begin", "contract_version": 1, "projection": owner["projection"],
            "selected_count": 1,
        })
    with pytest.raises(TypeError, match="non-object frame"):
        snapshot.receive(frame)
    assert snapshot._rows == []
    assert snapshot._complete is False


def test_current_snapshot_loader_reports_non_object_decode_instead_of_dying(monkeypatch):
    import json
    from types import SimpleNamespace

    from worktree_manager.production_picker.picker_tui import data_ssh

    source = data_ssh.Source("peer", "Linux", [], alias="peer", shell="bash")
    loader = data_ssh.LiveLoader([source])
    process = SimpleNamespace(
        stdout=iter(["[]\n"]), returncode=0, communicate=lambda **kwargs: ("", ""),
    )
    monkeypatch.setattr(loader, "_spawn_stream", lambda argv: process)
    monkeypatch.setattr(data_ssh, "_parse_ndjson_line", json.loads)
    assert loader._load_remote_stream(source, 0) is True
    assert loader.state_for_source(source.source_id) == "failed"
    assert "non-object frame" in loader.error_for_source(source.source_id)
    assert loader.records_for_source(source.source_id) == []


def test_current_snapshot_commits_only_after_its_complete_publication():
    owner = envelope([{"id": "one"}, {"id": "two"}])
    snapshot = CurrentSnapshot()
    begin = {
        "type": "begin",
        "contract_version": 1,
        "projection": owner["projection"],
        "selected_count": 2,
    }
    assert snapshot.receive(begin) is None
    for row in owner["worktrees"]:
        assert (
            snapshot.receive(
                {
                    "type": "worktree",
                    "wt": row,
                    "projection": owner["projection"],
                }
            )
            is None
        )
    rows = snapshot.receive({"type": "done", "projection": owner["projection"]})
    assert [row["id"] for row in rows] == ["one", "two"]
    assert rows[0]["_projection"]["revision"] == 1


@pytest.mark.parametrize(
    ("quality", "availability"),
    [("current", "advisory"), ("advisory", "stale")],
)
def test_current_rows_reject_contradictory_availability_in_envelopes_and_feeds(
    quality, availability,
):
    from worktree_manager.worktree_feed_contract import CurrentFeed

    value = envelope([{"id": "one"}], quality=quality)
    value["availability"] = {"status": availability, "reason": "fixture producer unavailable"}
    with pytest.raises(ValueError):
        parse_current_rows(value)
    frame = {
        "type": "begin", "contract_version": 1, "projection": value["projection"],
        "availability": value["availability"], "selected_count": 1, "cursor": 1,
        "base_cursor": None,
    }
    with pytest.raises(ValueError):
        CurrentSnapshot().receive(frame)
    with pytest.raises(ValueError):
        CurrentFeed().receive(frame)


@pytest.mark.parametrize("failure", ["missing", "mixed", "duplicate", "excess"])
def test_current_snapshot_rejects_incomplete_or_mixed_publications(failure):
    owner = envelope([{"id": "one"}])
    snapshot = CurrentSnapshot()
    snapshot.receive(
        {
            "type": "begin",
            "contract_version": 1,
            "projection": owner["projection"],
            "selected_count": 1,
        }
    )
    if failure == "missing":
        frame = {"type": "done", "projection": owner["projection"]}
    elif failure == "mixed":
        frame = {
            "type": "worktree",
            "wt": {"id": "one"},
            "projection": {**owner["projection"], "revision": 2},
        }
    else:
        snapshot.receive(
            {
                "type": "worktree",
                "wt": {"id": "one"},
                "projection": owner["projection"],
            }
        )
        frame = {
            "type": "worktree",
            "wt": {"id": "one" if failure == "duplicate" else "two"},
            "projection": owner["projection"],
        }
    with pytest.raises(ValueError):
        snapshot.receive(frame)
