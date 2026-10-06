"""Tests for root-chain resolution (effort ``pr-attribution-codenames``,
root-chain-capture slice): walking a worktree's ``owner_ref`` chain to its
ROOT ancestor and resolving that root's own public-safe codename, plus the
PR-marker composition helpers that fold it into the ``codename`` marker.
"""

from __future__ import annotations

import types

import pytest

from agent_worktrees import root_chain, tracking
from agent_worktrees.codename_config import CodenameConfig


@pytest.fixture(autouse=True)
def _clear_project_configs():
    """Reset the per-project config registry ``_seed`` populates, so one
    test's registered projects never leak into the next."""
    _PROJECT_CONFIGS.clear()
    yield
    _PROJECT_CONFIGS.clear()


def _cfg(machine="anomalous-potato", *, source_attribution_configured=False,
         pr_enabled=True, wordlist_path="", wordlist_path_configured=False):
    return types.SimpleNamespace(
        machine=machine,
        default_repo=types.SimpleNamespace(
            anchor="/anchor",
            codename=CodenameConfig(
                wordlist_path=wordlist_path,
                wordlist_path_configured=wordlist_path_configured,
            ),
            pr=types.SimpleNamespace(
                enabled=pr_enabled,
                source_attribution_configured=source_attribution_configured,
            ),
        ),
    )


#: Project-name -> config registered by ``_seed`` calls THIS test. Looked up
#: by the patched ``load_config``/``load_project_config`` below so multiple
#: ``_seed`` calls for DIFFERENT projects in the same test each keep their
#: own config, rather than the last call's config silently winning for
#: every project (each real project's config is independent on disk).
_PROJECT_CONFIGS: dict[str, object] = {}


def _seed(tmp_path, monkeypatch, project, wt_id, *, machine="anomalous-potato",
          owner_ref=None, codename=None, codename_source=None, config=None):
    _PROJECT_CONFIGS[project] = config or _cfg(machine)
    monkeypatch.setattr(
        "agent_worktrees.config.load_config",
        lambda *a, **k: _PROJECT_CONFIGS.get(project, _cfg(machine)),
    )
    monkeypatch.setattr(
        "agent_worktrees.config.load_project_config",
        lambda name, **k: _PROJECT_CONFIGS.get(name, _cfg(machine)),
    )
    monkeypatch.setattr("agent_worktrees.config.project_dir",
                        lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr("agent_worktrees.config.tracking_dir",
                        lambda name=None: tmp_path / f".{name}" / "worktrees")
    wdir = tmp_path / "trees" / wt_id
    wdir.mkdir(parents=True, exist_ok=True)
    tdir = tmp_path / f".{project}" / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    tracking.create_new_record(
        wt_id, f"worktree/{wt_id}", str(wdir), project, machine, "wsl", tdir,
        owner_ref=owner_ref, codename=codename, codename_source=codename_source,
    )
    return tracking.load_record(tdir / f"{wt_id}.yaml")


class TestResolveRootCodename:
    def test_no_owner_ref_returns_none(self, tmp_path, monkeypatch):
        rec = _seed(tmp_path, monkeypatch, "ext", "wt-child")
        assert root_chain.resolve_root_codename(rec, project="ext") is None

    def test_single_hop_returns_parent_codename(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "amber-thicket"
        )

    def test_multi_hop_walks_to_the_true_root(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        _seed(tmp_path, monkeypatch, "mid-repo", "wt-mid",
              owner_ref="anomalous-potato/harness/wt-root#s1")
        grandchild = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/mid-repo/wt-mid#s2",
        )
        assert root_chain.resolve_root_codename(grandchild, project="ext") == (
            "amber-thicket"
        )

    def test_cross_machine_owner_returns_none(self, tmp_path, monkeypatch):
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            machine="anomalous-potato",
            owner_ref="emancipation-cube/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") is None

    def test_missing_owner_record_returns_none(self, tmp_path, monkeypatch):
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-ghost#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") is None

    def test_cyclic_owner_graph_returns_none(self, tmp_path, monkeypatch):
        # Seed A -> owner B, then rewrite B -> owner A (a corrupted/hand-
        # edited cycle); the walk must refuse to loop forever.
        _seed(tmp_path, monkeypatch, "harness", "wt-a",
              owner_ref="anomalous-potato/ext/wt-b#s1")
        b = _seed(tmp_path, monkeypatch, "ext", "wt-b",
                  owner_ref="anomalous-potato/harness/wt-a#s2")
        assert root_chain.resolve_root_codename(b, project="ext") is None

    def test_root_missing_codename_is_backfilled_when_ensure(
        self, tmp_path, monkeypatch,
    ):
        root = _seed(tmp_path, monkeypatch, "harness", "wt-root")
        assert root.codename is None
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        result = root_chain.resolve_root_codename(child, project="ext")
        assert isinstance(result, str) and result

        # Persisted on the root's own record, not just returned.
        root_after = tracking.load_record(
            tmp_path / ".harness" / "worktrees" / "wt-root.yaml"
        )
        assert root_after.codename == result

    def test_root_missing_codename_not_backfilled_when_ensure_false(
        self, tmp_path, monkeypatch,
    ):
        _seed(tmp_path, monkeypatch, "harness", "wt-root")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(
            child, project="ext", ensure=False,
        ) is None

    def test_root_unsafe_provenance_is_not_published(self, tmp_path, monkeypatch):
        # A "custom" codename_source under an implicit (unconfigured)
        # source_attribution is never safe to publish (the same
        # provenance gate `may_publish_codename` enforces for the
        # PRIMARY codename) -- must apply identically to the root field.
        _seed(
            tmp_path, monkeypatch, "harness", "wt-root",
            codename="harbor-lattice", codename_source="custom",
            config=_cfg(source_attribution_configured=False),
        )
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") is None

    def test_root_custom_provenance_published_when_explicitly_configured(
        self, tmp_path, monkeypatch,
    ):
        _seed(
            tmp_path, monkeypatch, "harness", "wt-root",
            codename="harbor-lattice", codename_source="custom",
            config=_cfg(source_attribution_configured=True),
        )
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "harbor-lattice"
        )

    def test_never_returns_the_callers_own_codename(self, tmp_path, monkeypatch):
        # A root worktree (no owner_ref at all) has no chain to annotate --
        # even if it carries its own codename, it must never be echoed back
        # as its own "root".
        rec = _seed(tmp_path, monkeypatch, "ext", "wt-root",
                    codename="amber-thicket", codename_source="built-in")
        assert root_chain.resolve_root_codename(rec, project="ext") is None


class TestMarkerComposition:
    def test_build_codename_marker_with_root_includes_root_field(
        self, tmp_path, monkeypatch,
    ):
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        config = types.SimpleNamespace(repo_name="ext")
        marker = root_chain.build_codename_marker_with_root(
            "harbor-lattice", child, config,
        )
        assert marker == (
            "<!-- agent-worktrees:source codename=harbor-lattice "
            "root=amber-thicket -->"
        )

    def test_build_codename_marker_with_root_omits_field_when_unresolved(
        self, tmp_path, monkeypatch,
    ):
        child = _seed(tmp_path, monkeypatch, "ext", "wt-child")
        config = types.SimpleNamespace(repo_name="ext")
        marker = root_chain.build_codename_marker_with_root(
            "harbor-lattice", child, config,
        )
        assert marker == "<!-- agent-worktrees:source codename=harbor-lattice -->"

    def test_compose_codename_body_strips_stale_marker_when_not_published(self):
        body = "hello\n\n<!-- agent-worktrees:source codename=stale -->\n"
        result = root_chain.compose_codename_body(
            body, "harbor-lattice", False, None, None,
        )
        assert "agent-worktrees:source" not in result
        assert "hello" in result

    def test_compose_codename_body_appends_marker_when_published(
        self, tmp_path, monkeypatch,
    ):
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        config = types.SimpleNamespace(repo_name="ext")
        result = root_chain.compose_codename_body(
            "hello", "harbor-lattice", True, child, config,
        )
        assert "codename=harbor-lattice" in result
        assert "root=amber-thicket" in result
