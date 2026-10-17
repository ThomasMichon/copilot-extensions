"""Parent rows derive actionable PR links without copying child claim ownership."""

from pathlib import Path

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import config as cfg
from agent_worktrees import tracking
from agent_worktrees import worktrees_current_rows as current
from agent_worktrees import worktrees_projection as projection


@pytest.fixture
def graph(tmp_path, monkeypatch):
    config = cfg.Config(
        srcroot=str(tmp_path), machine="host", platform="linux", repo_name="control",
        repos={
            "control": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path)),
        },
    )
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name or 'control'}")
    return tmp_path


def _record(root: Path, project: str, wid: str, resources=(), *, prs=()):
    directory = root / f".{project}" / "worktrees"
    directory.mkdir(parents=True, exist_ok=True)
    record = tracking.create_new_record(
        wid, f"worktree/{wid}", str(root / project / wid),
        project, "host", "linux", directory,
    )
    record.resources = list(resources)
    record.prs = list(prs)
    tracking.save_record(record, directory / f"{wid}.yaml")
    return record


def _child(project, wid, *, state="active", machine="host"):
    return tracking.ResourceClaim(
        kind="worktree", ref=f"{machine}/{project}/{wid}", state=state,
    )


def _pr(number, *, state="active"):
    return tracking.ResourceClaim(
        kind="pr", ref=f"https://github.com/example/dependency/pull/{number}", state=state,
    )


@pytest.mark.parametrize("current_only", [False, True])
def test_parent_projects_child_pr_before_worktree_link_without_ledger_writes(graph, current_only):
    child = _record(graph, "dependency", "child", [_pr(42)])
    parent = _record(graph, "control", "parent", [_child("dependency", "child")])
    parent_file = graph / ".control" / "worktrees" / "parent.yaml"
    child_file = graph / ".dependency" / "worktrees" / "child.yaml"
    before = (parent_file.read_bytes(), child_file.read_bytes())

    row = core._worktree_to_dict(parent, current_only=current_only)

    assert row["claims_links"][0] == {
        "label": "dependency#42", "url": "https://github.com/example/dependency/pull/42",
    }
    assert row["claims_projection"] == {
        "inherited_pr_count": 1, "unresolved_count": 0, "complete": True,
    }
    assert parent.resources == [_child("dependency", "child")]
    assert child.resources == [_pr(42)]
    assert (parent_file.read_bytes(), child_file.read_bytes()) == before
    if current_only:
        assert row["claims_assets"]["by_kind"] == {"worktree": 1}
        store = projection.WorktreesProjection(graph / "current.sqlite3")
        store.publish("control", [row], observed_at=1)
        assert store.read("control", worktree_id="parent")["worktrees"] == [row]
        assert projection.read_advisory(
            graph / "current.sqlite3", "control", worktree_id="parent",
        )["worktrees"] == [row]


def test_multihop_pr_dedup_preserves_own_pr_backfill(graph):
    _record(graph, "dependency", "leaf", [_pr(42)])
    _record(graph, "dependency", "child-abcd", [
        _child("dependency", "leaf"), _pr(42),
        _pr(43, state="released"), _pr(44, state="at-rest"),
    ])
    parent = _record(
        graph, "control", "parent", [_child("dependency", "child-abcd")],
        prs=[tracking.PRRecord(state="open", number=7, repo="example/control")],
    )
    row = {}
    current.populate_claims(
        row, parent, {"state": "open", "number": 7, "repo": "example/control"},
        current_only=True,
    )

    assert [entry["label"] for entry in row["claims_links"]] == [
        "#7", "dependency#42", "WT dependency:abcd",
    ]
    assert row["claims_projection"]["inherited_pr_count"] == 1
    assert row["claims_link_count"] == 3
    assert row["claims_links_complete"] is True


def test_released_direct_pr_does_not_hide_active_descendant_pr(graph):
    _record(graph, "dependency", "child", [_pr(42)])
    parent = _record(
        graph, "control", "parent", [_pr(42, state="released"), _child("dependency", "child")],
    )
    row = {}
    current.populate_claims(row, parent, None, current_only=True)
    assert row["claims_links"][0]["label"] == "dependency#42"
    assert row["claims_projection"]["inherited_pr_count"] == 1
    assert row["claims_assets"]["held_count"] == 1


@pytest.mark.parametrize("state", ["at-rest", "released", "abandoned"])
def test_settled_child_edges_are_not_followed(graph, state, monkeypatch):
    parent = _record(graph, "control", "parent", [_child("dependency", "child", state=state)])

    def no_read(*args, **kwargs):
        pytest.fail("settled child must not be read")

    monkeypatch.setattr(tracking, "load_record", no_read)
    row = {}
    current.populate_claims(row, parent, None, current_only=True)
    assert "claims_projection" not in row
    assert not any(entry["url"] for entry in row["claims_links"])


@pytest.mark.parametrize("ref", [
    "other/dependency/child", "host/dependency/missing",
    "host/dependency/../../outside", "unqualified",
])
@pytest.mark.parametrize("current_only", [False, True])
def test_unresolvable_edges_are_visible_incomplete_not_empty_success(graph, ref, current_only):
    parent = _record(graph, "control", "parent", [
        tracking.ResourceClaim(kind="worktree", ref=ref),
    ])
    row = {}
    current.populate_claims(row, parent, None, current_only=current_only)
    assert row["claims_projection"] == {
        "inherited_pr_count": 0, "unresolved_count": 1, "complete": False,
    }
    if current_only:
        assert row["claims_links_complete"] is False
        assert row["claims_assets"]["held_count"] == 1
    else:
        assert "claims_links_complete" not in row
        assert "claims_link_count" not in row


def test_cycle_retains_found_pr_and_marks_projection_incomplete(graph):
    _record(graph, "dependency", "child", [_pr(42), _child("control", "parent")])
    parent = _record(graph, "control", "parent", [_child("dependency", "child")])
    row = {}
    current.populate_claims(row, parent, None, current_only=True)
    assert row["claims_links"][0]["label"] == "dependency#42"
    assert row["claims_projection"]["complete"] is False
    assert row["claims_projection"]["unresolved_count"] == 1


@pytest.mark.parametrize("current_only", [False, True])
def test_missing_root_machine_preserves_direct_claims_and_marks_incomplete(graph, current_only):
    _record(graph, "dependency", "child", [_pr(42)])
    parent = _record(graph, "control", "parent", [_pr(7), _child("dependency", "child")])
    parent.machine = ""
    row = core._worktree_to_dict(parent, current_only=current_only)
    assert row["claims_links"][0]["label"] == "dependency#7"
    assert row["claims_projection"] == {
        "inherited_pr_count": 0, "unresolved_count": 1, "complete": False,
    }
    if current_only:
        assert row["claims_assets"]["held_count"] == 2
        assert row["claims_links_complete"] is False
        store = projection.WorktreesProjection(graph / "current.sqlite3")
        store.publish("control", [row], observed_at=1)
        assert store.read("control")["worktrees"] == [row]
    else:
        assert "claims_links_complete" not in row


def test_current_projection_counts_all_links_before_bounding_visible_entries(graph):
    _record(graph, "dependency", "child", [_pr(number) for number in range(20)])
    parent = _record(graph, "control", "parent", [_child("dependency", "child")])
    row = {}
    current.populate_claims(row, parent, None, current_only=True)
    assert row["claims_projection"]["inherited_pr_count"] == 20
    assert row["claims_link_count"] == 21
    assert len(row["claims_links"]) == current.MAX_CURRENT_CLAIM_LINKS
    assert row["claims_links_complete"] is False
    assert row["claims_assets"]["held_count"] == 1


def test_publication_reuses_child_records_and_refreshes_them_next_time(graph, monkeypatch):
    child = _record(graph, "dependency", "child", [_pr(42)])
    parents = [
        _record(graph, "control", f"parent-{i}", [_child("dependency", "child")])
        for i in range(2)
    ]
    real_load = tracking.load_record
    reads = []

    def load(path):
        reads.append(path)
        return real_load(path)

    monkeypatch.setattr(tracking, "load_record", load)

    def publish():
        return current.materialize(
            parents, serialize=core._worktree_to_dict, observed_at=1,
            state_map={}, mux_map={}, session_ctx=None,
            bare_orphan_wts=None, bridge_live_wts=None,
        )

    assert all(row["claims_links"][0]["label"] == "dependency#42" for row in publish())
    assert len(reads) == 1
    child.resources[0].state = "released"
    tracking.save_record(child, graph / ".dependency" / "worktrees" / "child.yaml")
    reads.clear()
    assert all(row["claims_projection"]["inherited_pr_count"] == 0 for row in publish())
    assert len(reads) == 1
