"""Tests for ``agent-worktrees claims transitive`` (Plan Phase 3 of the
``worktree-claims-transitive-finalization`` effort): a read-only convenience
query that reports everything a worktree's whole subtree still owes, without
requiring a caller to manually walk each child's own ``claims`` output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import claims_cli
from agent_worktrees import claims_transitive_cli
from agent_worktrees import config as cfg
from agent_worktrees import obligations as ob
from agent_worktrees import output
from agent_worktrees import tracking

MACHINE = "machine-x"


def _config(project: str = "proj") -> cfg.Config:
    return cfg.Config(
        srcroot="/s", machine=MACHINE, platform="linux", repo_name=project,
        repos={project: cfg.RepoConfig(anchor="/a", worktree_root="/w")},
    )


def _save(
    tracking_d: Path, worktree_id: str, *,
    owner_ref: str | None = None,
    resources: list[tracking.ResourceClaim] | None = None,
) -> None:
    rec = tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(tracking_d / worktree_id),
        "proj", MACHINE, "linux", tracking_d, owner_ref=owner_ref,
    )
    rec.resources = list(resources or [])
    tracking.save_record(rec, tracking_d / f"{worktree_id}.yaml")


@pytest.fixture
def _tracking_d(tmp_path: Path, monkeypatch) -> Path:
    """A single tracking dir resolved identically by ``project_dir(name)``
    for any project name, so a same-machine, same-"project" subtree can be
    built and walked without a real multi-project registry."""
    d = tmp_path / ".proj" / "worktrees"
    d.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / ".proj")
    monkeypatch.setattr(cfg, "tracking_dir", lambda: d)
    return d


def test_leaf_with_no_resources_is_fully_settled(_tracking_d):
    _save(_tracking_d, "wt-leaf")
    found, unresolved = claims_transitive_cli.transitive_obligations("wt-leaf", "proj", _config())
    assert found == []
    assert unresolved == []


def test_single_hop_reports_childs_unsettled_claim(_tracking_d):
    b_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-B")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
    ])
    _save(_tracking_d, "wt-B", owner_ref=tracking.format_claim_ref(MACHINE, "proj", "wt-A"),
          resources=[tracking.ResourceClaim(kind="pr", ref="o/r#2", state=ob.ACTIVE)])

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert unresolved == []
    assert len(found) == 1
    assert found[0]["path"] == ("wt-A", "wt-B")
    assert found[0]["kind"] == "pr"
    assert found[0]["ref"] == "o/r#2"


def test_multi_hop_a_to_b_to_c(_tracking_d):
    b_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-B")
    c_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-C")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
    ])
    _save(_tracking_d, "wt-B", resources=[
        tracking.ResourceClaim(kind="worktree", ref=c_ref, state=ob.ACTIVE),
    ])
    _save(_tracking_d, "wt-C", resources=[
        tracking.ResourceClaim(kind="pr", ref="o/r#9", state=ob.ACTIVE),
    ])

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert unresolved == []
    assert len(found) == 1
    assert found[0]["path"] == ("wt-A", "wt-B", "wt-C")
    assert found[0]["ref"] == "o/r#9"


def test_session_claims_are_never_reported_as_obligations(_tracking_d):
    """A `session`-kind claim is advisory-only -- finalize's own gate never
    counts it as unsettled (a normally-running worktree always carries one
    for its own live session). A resource-clean subtree must not appear to
    "owe" its own invoking session."""
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="session", ref="m/proj/wt-A#sess-1", state=ob.ACTIVE),
    ])

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert found == []
    assert unresolved == []


def test_malicious_child_ref_cannot_escape_the_worktrees_directory(_tracking_d):
    """A corrupted/malicious ref whose parsed worktree_id contains a path
    traversal component must be rejected (reported unresolved), never
    joined into a real filesystem path."""
    evil_ref = tracking.format_claim_ref(MACHINE, "proj", "../../etc/passwd")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=evil_ref, state=ob.ACTIVE),
    ])

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert found == []
    assert len(unresolved) == 1
    assert "unsafe" in unresolved[0]["reason"]


def test_settled_worktree_edge_is_not_descended(_tracking_d):
    """An at-rest/released `worktree`-kind claim means that child already
    settled -- the query must not descend into it (and, for a since-pruned
    child, could not anyway)."""
    b_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-B")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.AT_REST),
    ])
    # Deliberately no wt-B.yaml on disk -- proves descent never even tries.

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert found == []
    assert unresolved == []


def test_cross_machine_child_is_unresolved_not_raised(_tracking_d):
    foreign_ref = tracking.format_claim_ref("other-machine", "proj", "wt-B")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=foreign_ref, state=ob.ACTIVE),
    ])

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert found == []
    assert len(unresolved) == 1
    assert unresolved[0]["path"] == ("wt-A",)
    assert "cross-machine" in unresolved[0]["reason"]


def test_unresolvable_project_root_is_unresolved_not_raised(_tracking_d, monkeypatch):
    """A same-machine child naming a project this machine cannot resolve
    (e.g. an unavailable namespaced state root) must degrade to an
    `unresolved` entry, never crash the whole query."""
    b_ref = tracking.format_claim_ref(MACHINE, "missing-proj", "wt-ghost")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
    ])

    def _raising_project_dir(name=None):
        if name == "missing-proj":
            raise RuntimeError("project state root unavailable")
        return _tracking_d.parent

    monkeypatch.setattr(cfg, "project_dir", _raising_project_dir)

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert found == []
    assert len(unresolved) == 1
    assert unresolved[0]["path"] == ("wt-A", "wt-ghost")
    assert "unreadable" in unresolved[0]["reason"]


def test_missing_child_record_is_unresolved_not_raised(_tracking_d):
    b_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-ghost")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
    ])

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert found == []
    assert len(unresolved) == 1
    assert unresolved[0]["path"] == ("wt-A", "wt-ghost")
    assert "not found" in unresolved[0]["reason"]


def test_cyclic_ledger_terminates_instead_of_looping(_tracking_d):
    """Never expected from normal creation, but a hand-edited or corrupted
    ledger must not hang this query forever."""
    a_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-A")
    b_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-B")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
    ])
    _save(_tracking_d, "wt-B", resources=[
        tracking.ResourceClaim(kind="worktree", ref=a_ref, state=ob.ACTIVE),
    ])

    found, unresolved = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    assert found == []
    assert any(u["reason"] == "cycle detected" for u in unresolved)


def test_sibling_claims_at_the_same_level_both_surface(_tracking_d):
    b_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-B")
    _save(_tracking_d, "wt-A", resources=[
        tracking.ResourceClaim(kind="pr", ref="o/r#1", state=ob.ACTIVE),
        tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
    ])
    _save(_tracking_d, "wt-B", resources=[
        tracking.ResourceClaim(kind="pr", ref="o/r#2", state=ob.ACTIVE),
    ])

    found, _ = claims_transitive_cli.transitive_obligations("wt-A", "proj", _config())

    refs = {(o["path"], o["ref"]) for o in found}
    assert refs == {(("wt-A",), "o/r#1"), (("wt-A", "wt-B"), "o/r#2")}


class TestClaimsTransitiveCli:
    """The thin CLI wrapper: worktree resolution + rendering."""

    def test_unknown_worktree_errors(self, _tracking_d, monkeypatch, capsys):
        monkeypatch.setattr(cfg, "load_config", lambda: _config())
        args = _ns(target=["wt-nope"], json=False)
        rc = claims_cli._claims_transitive(args, "wt-nope")
        assert rc == 1
        # `output.err` prints to plain stdout (not stderr).
        assert "not found" in capsys.readouterr().out

    def test_never_reports_settled_when_an_edge_is_unresolved(
        self, _tracking_d, monkeypatch, capsys,
    ):
        """No confirmed (found) obligation must never be rendered as
        "the whole subtree is settled" while an edge remains genuinely
        unresolved -- that would be a false affirmative."""
        monkeypatch.setattr(cfg, "load_config", lambda: _config())
        foreign_ref = tracking.format_claim_ref("other-machine", "proj", "wt-B")
        _save(_tracking_d, "wt-A", resources=[
            tracking.ResourceClaim(kind="worktree", ref=foreign_ref, state=ob.ACTIVE),
        ])
        args = _ns(target=["wt-A"], json=False)
        rc = claims_cli._claims_transitive(args, "wt-A")
        assert rc == 0
        out = capsys.readouterr().out
        assert "the whole subtree is settled" not in out
        assert "NOT provably settled" in out
        assert "could not check" in out

    def test_json_mode_serializes_paths_as_lists(self, _tracking_d, monkeypatch):
        monkeypatch.setattr(cfg, "load_config", lambda: _config())
        b_ref = tracking.format_claim_ref(MACHINE, "proj", "wt-B")
        _save(_tracking_d, "wt-A", resources=[
            tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
        ])
        _save(_tracking_d, "wt-B", resources=[
            tracking.ResourceClaim(kind="pr", ref="o/r#2", state=ob.ACTIVE),
        ])
        args = _ns(target=["wt-A"], json=True)
        # `_json_output` deliberately writes to `sys.__stdout__`, not
        # `sys.stdout` -- `capsys` never sees it; use the helper built for
        # exactly this in-process-capture case.
        with output.capture_json_output() as buf:
            rc = claims_cli._claims_transitive(args, "wt-A")
        assert rc == 0
        import json as _json
        payload = _json.loads(buf.getvalue())
        assert payload["worktree_id"] == "wt-A"
        assert payload["obligations"][0]["path"] == ["wt-A", "wt-B"]


def _ns(**kwargs):
    import argparse
    return argparse.Namespace(**kwargs)
