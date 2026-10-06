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

    def test_path_traversal_in_owner_ref_worktree_id_is_rejected(
        self, tmp_path, monkeypatch,
    ):
        # A hand-edited/corrupted owner_ref embedding a path separator in
        # the worktree-id component must never be joined into a real path.
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/../../secret#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") is None

    def test_path_traversal_in_owner_ref_project_is_rejected(
        self, tmp_path, monkeypatch,
    ):
        # Same, but the traversal attempt is in the PROJECT component --
        # must not get a chance to evaluate a codename under the wrong
        # project's provenance policy.
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/../secret/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") is None

    def test_chain_of_exactly_the_cap_depth_resolves(self, tmp_path, monkeypatch):
        # A chain of exactly _MAX_CHAIN_DEPTH ancestors is the documented
        # boundary and must still resolve -- only a STRICTLY longer chain
        # is rejected.
        depth = root_chain._MAX_CHAIN_DEPTH
        _seed(tmp_path, monkeypatch, "p0", "wt-0",
              codename="amber-thicket", codename_source="built-in")
        leaf = None
        for i in range(1, depth + 1):
            owner_ref = f"anomalous-potato/p{i - 1}/wt-{i - 1}#s{i}"
            leaf = _seed(tmp_path, monkeypatch, f"p{i}", f"wt-{i}", owner_ref=owner_ref)
        assert root_chain.resolve_root_codename(leaf, project=f"p{depth}") == (
            "amber-thicket"
        )

    def test_chain_exceeding_the_cap_depth_is_rejected(self, tmp_path, monkeypatch):
        depth = root_chain._MAX_CHAIN_DEPTH + 1
        _seed(tmp_path, monkeypatch, "p0", "wt-0",
              codename="amber-thicket", codename_source="built-in")
        leaf = None
        for i in range(1, depth + 1):
            owner_ref = f"anomalous-potato/p{i - 1}/wt-{i - 1}#s{i}"
            leaf = _seed(tmp_path, monkeypatch, f"p{i}", f"wt-{i}", owner_ref=owner_ref)
        assert root_chain.resolve_root_codename(leaf, project=f"p{depth}") is None

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

    def test_frozen_decision_survives_a_later_config_change_to_withhold(
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
        # The root repo later tightens its config -- the ALREADY-FROZEN
        # decision for this worktree must not retroactively hide the
        # codename it already published.
        _PROJECT_CONFIGS["harness"] = _cfg(source_attribution_configured=False)
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "harbor-lattice"
        )

    def test_frozen_decision_survives_a_later_config_change_to_permit(
        self, tmp_path, monkeypatch,
    ):
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
        # The root repo later opts in -- the ALREADY-FROZEN "withheld"
        # decision for this worktree must not retroactively expose it.
        _PROJECT_CONFIGS["harness"] = _cfg(source_attribution_configured=True)
        assert root_chain.resolve_root_codename(child, project="ext") is None

    def test_owner_ref_handoff_invalidates_the_freeze(
        self, tmp_path, monkeypatch,
    ):
        # A worktree claim handoff rewrites owner_ref (claim_handoffs.py) --
        # the frozen decision bound to the OLD owner_ref must not survive
        # it; a new one is computed and frozen against the NEW owner_ref.
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        _seed(tmp_path, monkeypatch, "other-harness", "wt-other-root",
              codename="cobalt-ember", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "amber-thicket"
        )
        # Simulate a handoff: the record's owner_ref is reassigned.
        tdir = tmp_path / ".ext" / "worktrees"
        rec = tracking.load_record(tdir / "wt-child.yaml")
        rec.owner_ref = "anomalous-potato/other-harness/wt-other-root#s2"
        tracking.save_record(rec, tdir / "wt-child.yaml")
        assert root_chain.resolve_root_codename(rec, project="ext") == (
            "cobalt-ember"
        )

    def test_intermediate_hop_handoff_invalidates_the_freeze(
        self, tmp_path, monkeypatch,
    ):
        # child -> mid -> root-A initially; a handoff rewrites MID's own
        # owner_ref (not the leaf's) to point at a DIFFERENT root-B. The
        # leaf's own (owner_ref, creation_nonce) is unchanged, so a
        # leaf-only freeze would incorrectly keep returning root-A forever
        # -- the freeze must be bound to the WHOLE chain's fingerprint.
        _seed(tmp_path, monkeypatch, "harness-a", "wt-root-a",
              codename="amber-thicket", codename_source="built-in")
        _seed(tmp_path, monkeypatch, "harness-b", "wt-root-b",
              codename="cobalt-ember", codename_source="built-in")
        _seed(tmp_path, monkeypatch, "mid-repo", "wt-mid",
              owner_ref="anomalous-potato/harness-a/wt-root-a#s1")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/mid-repo/wt-mid#s2",
        )
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "amber-thicket"
        )
        # Simulate a handoff at the INTERMEDIATE hop only -- the leaf
        # (child)'s own record is untouched.
        mid_dir = tmp_path / ".mid-repo" / "worktrees"
        mid = tracking.load_record(mid_dir / "wt-mid.yaml")
        mid.owner_ref = "anomalous-potato/harness-b/wt-root-b#s3"
        tracking.save_record(mid, mid_dir / "wt-mid.yaml")
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "cobalt-ember"
        )

    def test_root_anonymous_opt_out_blocks_publication(
        self, tmp_path, monkeypatch,
    ):
        # A root repo that has explicitly chosen the fully anonymous
        # opt-out (source_attribution: false) must never have its codename
        # exposed via someone ELSE's marker, even if the codename itself
        # is otherwise valid and built-in (never needing the custom-
        # wordlist provenance gate at all).
        root_config = _cfg()
        root_config.default_repo.pr.source_attribution = False
        _seed(
            tmp_path, monkeypatch, "harness", "wt-root",
            codename="amber-thicket", codename_source="built-in",
            config=root_config,
        )
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") is None

    def test_root_raw_marker_mode_still_publishes_its_codename(
        self, tmp_path, monkeypatch,
    ):
        # A root repo in `true` (raw marker) mode already accepts full
        # exposure on its OWN PRs -- publishing the mere codename via
        # someone else's marker is strictly less revealing, so it's
        # allowed unconditionally (no custom-wordlist gate applies).
        root_config = _cfg()
        root_config.default_repo.pr.source_attribution = True
        _seed(
            tmp_path, monkeypatch, "harness", "wt-root",
            codename="harbor-lattice", codename_source="custom",
            config=root_config,
        )
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "harbor-lattice"
        )

    def test_worktree_id_reuse_invalidates_a_stale_sidecar(
        self, tmp_path, monkeypatch,
    ):
        # A recreated worktree (same id, a fresh `creation_nonce`) must
        # never inherit a predecessor's frozen root decision from an
        # orphaned sidecar the tracking-record deletion path didn't clean
        # up -- even if it happens to land in the same wall-clock second.
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        _seed(tmp_path, monkeypatch, "other-harness", "wt-other-root",
              codename="cobalt-ember", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "amber-thicket"
        )
        # The worktree id is reaped and recreated (a genuinely fresh
        # `creation_nonce`, regardless of timing) with a different origin,
        # but the OLD sidecar was never removed (what this invalidation
        # protects against even without explicit cleanup wiring).
        recreated = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/other-harness/wt-other-root#s9",
        )
        assert recreated.creation_nonce != child.creation_nonce
        assert root_chain.resolve_root_codename(recreated, project="ext") == (
            "cobalt-ember"
        )

    def test_legacy_record_without_creation_nonce_never_freezes(
        self, tmp_path, monkeypatch,
    ):
        # A record predating `creation_nonce` can't be bound to a reliable
        # identity -- it must simply never freeze (always computed live),
        # rather than being treated as a permanently-matching empty key.
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        tdir = tmp_path / ".ext" / "worktrees"
        child.creation_nonce = ""
        tracking.save_record(child, tdir / "wt-child.yaml")
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "amber-thicket"
        )
        sidecar = tdir / "wt-child.root-attribution.json"
        assert not sidecar.exists()

    def test_malformed_frozen_sidecar_is_never_trusted(
        self, tmp_path, monkeypatch,
    ):
        # A hand-edited/corrupted sidecar must never be interpolated
        # straight into the HTML marker -- an invalid value degrades to
        # "never frozen" so the normal (validated) provenance path re-runs.
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        sidecar = tmp_path / ".ext" / "worktrees" / "wt-child.root-attribution.json"
        sidecar.write_text('{"root_codename": "not valid --> injected"}')
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "amber-thicket"
        )
        # The re-resolution also re-freezes a CLEAN value for next time.
        assert "amber-thicket" in sidecar.read_text()
        assert "-->" not in sidecar.read_text().replace(
            '"root_codename": "amber-thicket"', ""
        )

    def test_freeze_is_serialized_under_the_cross_process_record_lock(
        self, tmp_path, monkeypatch,
    ):
        # The read-check -> compute -> write must be wrapped in the shared
        # cross-process tracking lock (never a bare unlocked read-then-
        # write), so two concurrent processes can't independently derive
        # and clobber each other's decision.
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        seen_paths = []
        real_lock = tracking._RecordLock

        class _SpyLock(real_lock):
            def __enter__(self):
                seen_paths.append(self._yaml_path)
                return super().__enter__()

        monkeypatch.setattr(tracking, "_RecordLock", _SpyLock)
        result = root_chain.resolve_root_codename(child, project="ext")
        assert result == "amber-thicket"
        assert seen_paths == [
            tmp_path / ".ext" / "worktrees" / "wt-child.root-attribution.json"
        ]

    def test_another_processs_frozen_value_wins_over_a_racing_recompute(
        self, tmp_path, monkeypatch,
    ):
        # Simulate another process freezing a decision WHILE this call is
        # resolving this_machine/project (before it takes the lock): the
        # in-lock re-check must return that WINNING value, never a
        # locally-computed one that lost the race.
        _seed(tmp_path, monkeypatch, "harness", "wt-root",
              codename="amber-thicket", codename_source="built-in")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        sidecar = tmp_path / ".ext" / "worktrees" / "wt-child.root-attribution.json"
        real_load_config = root_chain.cfg.load_config

        def _racing_load_config(*a, **k):
            # Another process wins the race and freezes first, bound to
            # the SAME chain fingerprint this call will itself compute.
            import json
            walked = root_chain._walk_to_root(
                child, project="ext", this_machine="anomalous-potato",
            )
            chain_key = root_chain._chain_identity_key(walked[2])
            sidecar.write_text(json.dumps({
                "root_codename": "winner-codename", "chain_key": chain_key,
            }))
            return real_load_config(*a, **k)

        monkeypatch.setattr(
            "agent_worktrees.config.load_config", _racing_load_config,
        )
        assert root_chain.resolve_root_codename(child, project="ext") == (
            "winner-codename"
        )

    def test_ensure_false_peek_never_freezes_a_premature_decision(
        self, tmp_path, monkeypatch,
    ):
        root = _seed(tmp_path, monkeypatch, "harness", "wt-root")
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        assert root.codename is None
        # A diagnostic peek (ensure=False) finds nothing to publish yet --
        # must NOT lock that in.
        assert root_chain.resolve_root_codename(
            child, project="ext", ensure=False,
        ) is None
        # The REAL publish call (ensure=True, the default) must still be
        # free to backfill + resolve fresh, not inherit the peek's None.
        result = root_chain.resolve_root_codename(child, project="ext")
        assert isinstance(result, str) and result

    def test_cross_machine_owner_fails_closed_when_this_machine_unresolvable(
        self, tmp_path, monkeypatch,
    ):
        child = _seed(
            tmp_path, monkeypatch, "ext", "wt-child",
            owner_ref="anomalous-potato/harness/wt-root#s1",
        )
        monkeypatch.setattr(
            "agent_worktrees.config.load_config",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unresolvable")),
        )
        assert root_chain.resolve_root_codename(
            child, project="ext", this_machine=None,
        ) is None


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
