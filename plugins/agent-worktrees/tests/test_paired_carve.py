"""Tests for the citadel paired -harness/-knowledge carve (#957 slice 2).

Covers ``_carve_paired_knowledge`` (the carve-both helper wired into
``_create_worktree_core``) and the ``state-root --pair`` anchor-kind resolver.
"""

from __future__ import annotations

import types

import agent_worktrees.__main__ as m
from agent_worktrees import knowledge_plugins as kp
from agent_worktrees import repos as repos_mod
from agent_worktrees import state_root as sr
from agent_worktrees import tracking as tk


def _state_root(*, path, repo, requires_external=True, bound=True):
    return sr.StateRoot(
        path=path, source="knowledge_repo", repo=repo, stateless=True,
        requires_external=requires_external, bound=bound, error=None,
    )


def _config(machine="test", repo_name="citadel-harness"):
    return types.SimpleNamespace(machine=machine, repo_name=repo_name)


def _common_patches(monkeypatch, tmp_path):
    """Patch tracking dir + best-effort permissions/activity to no-ops."""
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(
        m.cfg, "project_dir", lambda name=None: tmp_path / f".{name}"
    )
    monkeypatch.setattr(
        tk.cfg, "project_dir", lambda name=None: tmp_path / f".{name}"
    )
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda a, b: False)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda p: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
    # codename-attribution-by-default (PR #3037 review finding): the
    # allocation-policy second revalidation reloads config fresh -- tests
    # in this module don't register a real project on disk, so give the
    # reload a benign default (no custom wordlist, PR inactive) rather
    # than letting it fail into the conservative-deny fallback for every
    # test that doesn't explicitly opt into the policy scenario.
    # TestCarvePairedKnowledgeAttributionPolicy's own `_setup` overrides
    # this with a scenario-specific config afterward.
    from agent_worktrees.codename_config import CodenameConfig
    monkeypatch.setattr(
        m.cfg, "load_config",
        lambda project=None: types.SimpleNamespace(
            default_repo=types.SimpleNamespace(
                codename=CodenameConfig(),
                pr=types.SimpleNamespace(
                    enabled=False, source_attribution_configured=False,
                ),
            )
        ),
    )


class TestCarvePairedKnowledge:
    def test_returns_none_when_not_stateless(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=None, repo="", requires_external=False,
                                  bound=False),
        )
        out = m._carve_paired_knowledge(
            _config(), harness_id="test-win-ts-ab", timestamp="ts",
            suffix="ab", plat="windows", plat_short="win",
        )
        assert out is None

    def test_env_disable(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        monkeypatch.setenv("AGENT_WORKTREES_NO_PAIR", "1")
        # Even a bound stateless resolve must yield None when disabled.
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(tmp_path), repo="k"),
        )
        out = m._carve_paired_knowledge(
            _config(), harness_id="h", timestamp="ts", suffix="ab",
            plat="windows", plat_short="win",
        )
        assert out is None

    def test_worktree_class_carves_and_cross_stamps(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        k_anchor = tmp_path / "knowledge"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="citadel-knowledge"),
        )
        entry = repos_mod.RepoEntry(
            name="citadel-knowledge",
            repo_class="worktree",
            remote="https://example.com/citadel-knowledge.git",
            default_branch="main",
        )
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        monkeypatch.setattr(
            m.git_ops,
            "resolve_remote_name",
            lambda value, *, cwd: "origin",
        )
        # Stub the git side-effects.
        carved = {}

        def _create_worktree(anchor, wt_path, branch, start_point):
            carved["anchor"] = anchor
            carved["wt_path"] = wt_path
            carved["branch"] = branch
            carved["start_point"] = start_point

        monkeypatch.setattr(
            m.git_ops,
            "prepare_worktree_base",
            lambda *a, **k: types.SimpleNamespace(
                start_point="origin/main",
                fetched=True,
                fetch_error=None,
                anchor=types.SimpleNamespace(
                    updated=True,
                    reason="updated",
                    behind=2,
                ),
            ),
        )
        monkeypatch.setattr(m.git_ops, "create_worktree", _create_worktree)

        stamp = m._carve_paired_knowledge(
            _config(machine="test"), harness_id="test-win-20260806-ab",
            timestamp="20260806", suffix="ab", plat="windows",
            plat_short="win",
        )
        # Harness stamp.
        assert stamp is not None
        assert stamp["pair_kind"] == "worktree"
        assert stamp["pair_role"] == "harness"
        assert stamp["pair_id"] == "20260806-ab"
        assert stamp["pair_ref"] == "test/citadel-knowledge/test-win-20260806-ab-k"
        # A knowledge worktree was carved.
        assert carved["branch"] == "worktree/test-win-20260806-ab-k"
        assert carved["start_point"] == "origin/main"
        # Knowledge tracking record written, cross-stamped back to harness.
        knowledge_tracking = tmp_path / ".citadel-knowledge" / "worktrees"
        krec = tk.load_record_by_id(
            "test-win-20260806-ab-k",
            tracking_path=knowledge_tracking,
        )
        assert krec is not None
        assert krec.repo == "citadel-knowledge"
        assert krec.pair_role == "knowledge"
        assert krec.pair_kind == "worktree"
        # pr-attribution-codenames Phase 2 (#2838): the paired knowledge
        # worktree is a real, independently-lookup-able record and must get
        # its own codename, scoped to its own project's tracking directory.
        assert krec.codename
        # codename-attribution-by-default: a config-load failure (no real
        # project config for this knowledge repo in the test env) degrades
        # only the wordlist resolution, never authorizes an allocation the
        # policy would otherwise block -- classifies to the safe
        # "built-in" default.
        assert krec.codename_source == "built-in"
        assert krec.pair_id == "20260806-ab"
        assert krec.pair_ref == "test/citadel-harness/test-win-20260806-ab"
        assert not (tmp_path / "test-win-20260806-ab-k.yaml").exists()

    def test_non_worktree_class_pairs_anchor(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        k_anchor = tmp_path / "kb-singleton"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="kb"),
        )
        entry = repos_mod.RepoEntry(name="kb", repo_class="singleton")
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        called = {"carve": False}
        monkeypatch.setattr(
            m.git_ops, "create_worktree",
            lambda *a, **k: called.__setitem__("carve", True),
        )
        refreshed = {"called": False}
        monkeypatch.setattr(
            m.git_ops,
            "prepare_worktree_base",
            lambda *a, **k: (
                refreshed.__setitem__("called", True)
                or types.SimpleNamespace(
                    start_point="HEAD",
                    fetched=False,
                    fetch_error="offline",
                    anchor=types.SimpleNamespace(
                        updated=False,
                        reason="no-upstream",
                        behind=0,
                    ),
                )
            ),
        )
        stamp = m._carve_paired_knowledge(
            _config(), harness_id="h", timestamp="ts", suffix="ab",
            plat="windows", plat_short="win",
        )
        assert stamp is not None
        assert stamp["pair_kind"] == "anchor"
        assert stamp["pair_role"] == "harness"
        assert stamp["pair_ref"] == "test/kb/kb-singleton"
        # No second worktree carved for a non-worktree-class knowledge repo.
        assert called["carve"] is False
        assert refreshed["called"] is True
        # No knowledge tracking record either.
        assert list(tmp_path.glob("*.yaml")) == []


class TestCarvePairedKnowledgeAttributionPolicy:
    """codename-attribution-by-default (round-10/11/12/13/14 findings):
    the paired-knowledge carve must preflight the SAME allocation-time
    policy the harness's own create path enforces, for the KNOWLEDGE
    project's own config -- and never silently swallow a policy violation
    into a built-in-wordlist fallback."""

    def _knowledge_config_with_custom_wordlist(
        self, tmp_path, *, source_attribution_configured: bool,
    ):
        from agent_worktrees.codename_config import CodenameConfig
        wordlist_file = tmp_path / "custom-words.yaml"
        wordlist_file.write_text("- alpha\n- bravo\n- charlie\n")
        return types.SimpleNamespace(
            default_repo=types.SimpleNamespace(
                codename=CodenameConfig(
                    wordlist_path=str(wordlist_file), wordlist_path_configured=True,
                ),
                pr=types.SimpleNamespace(
                    enabled=True,
                    source_attribution_configured=source_attribution_configured,
                ),
                worktree_root=str(tmp_path / "worktrees"),
            )
        )

    def _setup(self, monkeypatch, tmp_path, *, source_attribution_configured):
        _common_patches(monkeypatch, tmp_path)
        k_anchor = tmp_path / "knowledge"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="citadel-knowledge"),
        )
        entry = repos_mod.RepoEntry(
            name="citadel-knowledge", repo_class="worktree",
            remote="https://example.com/citadel-knowledge.git",
            default_branch="main",
        )
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        monkeypatch.setattr(
            m.git_ops, "resolve_remote_name", lambda value, *, cwd: "origin",
        )
        knowledge_config = self._knowledge_config_with_custom_wordlist(
            tmp_path, source_attribution_configured=source_attribution_configured,
        )
        monkeypatch.setattr(m.cfg, "load_config", lambda project=None: knowledge_config)
        called = {"carve": False}
        monkeypatch.setattr(
            m.git_ops, "create_worktree",
            lambda *a, **k: called.__setitem__("carve", True),
        )
        monkeypatch.setattr(
            m.git_ops, "prepare_worktree_base",
            lambda *a, **k: types.SimpleNamespace(
                start_point="origin/main", fetched=True, fetch_error=None,
                anchor=types.SimpleNamespace(updated=True, reason="updated", behind=0),
            ),
        )
        return called

    def test_custom_wordlist_unconfigured_blocks_before_any_side_effect(
        self, monkeypatch, tmp_path,
    ):
        called = self._setup(
            monkeypatch, tmp_path, source_attribution_configured=False,
        )
        from agent_worktrees import codename_tracking
        try:
            m._carve_paired_knowledge(
                _config(machine="test"), harness_id="test-win-20260806-ab",
                timestamp="20260806", suffix="ab", plat="windows",
                plat_short="win",
            )
        except codename_tracking.CodenameAttributionPolicyError:
            pass
        else:
            raise AssertionError("expected CodenameAttributionPolicyError")
        assert called["carve"] is False
        assert not (tmp_path / ".citadel-knowledge").exists()

    def test_explicit_opt_in_allows_carve(self, monkeypatch, tmp_path):
        called = self._setup(
            monkeypatch, tmp_path, source_attribution_configured=True,
        )
        stamp = m._carve_paired_knowledge(
            _config(machine="test"), harness_id="test-win-20260806-ab",
            timestamp="20260806", suffix="ab", plat="windows", plat_short="win",
        )
        assert stamp is not None
        assert called["carve"] is True
        krec = tk.load_record_by_id(
            "test-win-20260806-ab-k",
            tracking_path=tmp_path / ".citadel-knowledge" / "worktrees",
        )
        assert krec is not None
        assert krec.codename_source == "custom"

    def test_second_revalidation_recomputes_from_fresh_config(
        self, monkeypatch, tmp_path,
    ):
        # PR #3037 review finding: the second revalidation (immediately
        # before the first side effect, under the allocation lock) must
        # RECOMPUTE the policy from a freshly-loaded config, not reuse the
        # pre-lock snapshot -- otherwise it can never observe a config
        # change landing between the preflight and this point, making the
        # claimed TOCTOU-window shrink hollow. Simulate exactly that: the
        # preflight sees an EXPLICIT opt-in (passes), but by the time the
        # lock is held, cfg.load_config resolves an UNCONFIGURED policy --
        # the second check must catch it.
        called = self._setup(
            monkeypatch, tmp_path, source_attribution_configured=True,
        )
        unconfigured_config = self._knowledge_config_with_custom_wordlist(
            tmp_path, source_attribution_configured=False,
        )
        monkeypatch.setattr(
            m.cfg, "load_config", lambda project=None: unconfigured_config,
        )
        from agent_worktrees import codename_tracking
        try:
            m._carve_paired_knowledge(
                _config(machine="test"), harness_id="test-win-20260806-ab",
                timestamp="20260806", suffix="ab", plat="windows",
                plat_short="win",
            )
        except codename_tracking.CodenameAttributionPolicyError:
            pass
        else:
            raise AssertionError("expected CodenameAttributionPolicyError")
        assert called["carve"] is False

    def test_residual_race_names_the_orphaned_harness_path_and_branch(
        self, monkeypatch, tmp_path,
    ):
        # Validation Plan (round-14 finding); round-1 review finding on
        # this test itself (must drive the real `create` path, not call
        # `_carve_paired_knowledge` directly, to actually prove `create`
        # propagates the late race past its outer exception boundary and
        # leaves the harness side effects behind unrolled-back). Simulate
        # the residual TOCTOU race: the pre-lock preflights (both
        # `_paired_knowledge_allocation_preflight` and
        # `_carve_paired_knowledge`'s own) pass on a compliant config, but
        # the config becomes policy-violating by the time the SECOND,
        # lock-held revalidation reloads it. Assert `_create_worktree_core`
        # itself fails, the HARNESS worktree/branch/record side effects
        # already happened and are NOT rolled back, and the surfaced error
        # names the exact orphaned harness worktree path and branch.
        import types as types_mod

        from agent_worktrees import codename_tracking
        from agent_worktrees import config as cfg_mod

        k_anchor = tmp_path / "knowledge"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="citadel-knowledge"),
        )
        entry = repos_mod.RepoEntry(
            name="citadel-knowledge", repo_class="worktree",
            remote="https://example.com/citadel-knowledge.git",
            default_branch="main",
        )
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        monkeypatch.setattr(
            m.git_ops, "resolve_remote_name", lambda value, *, cwd: "origin",
        )
        monkeypatch.setattr(
            m.git_ops, "prepare_worktree_base",
            lambda *a, **k: types_mod.SimpleNamespace(
                start_point="origin/main", fetched=True, fetch_error=None,
                anchor=types_mod.SimpleNamespace(updated=True, reason="updated", behind=0),
            ),
        )
        compliant_config = self._knowledge_config_with_custom_wordlist(
            tmp_path, source_attribution_configured=True,
        )
        noncompliant_config = self._knowledge_config_with_custom_wordlist(
            tmp_path, source_attribution_configured=False,
        )
        # Flip to policy-violating only after the harness's own worktree is
        # created AND `_carve_paired_knowledge`'s own first (pre-lock)
        # preflight has already passed once more -- not a fragile absolute
        # call-count guess. This models the race landing in the EXACT
        # window the round-14 finding describes: the config changes after
        # every pre-lock preflight already passed (both the harness's own
        # early gate and the carve's own first preflight), and is only
        # ever observed by the second, lock-held revalidation.
        state = {"harness_created": False, "post_harness_load_count": 0}

        def _stateful_load_config(project=None):
            if not state["harness_created"]:
                return compliant_config
            state["post_harness_load_count"] += 1
            # The first load after the harness exists is
            # `_carve_paired_knowledge`'s own pre-lock preflight -- must
            # still pass. Every load after that is the second, lock-held
            # revalidation -- must now observe the changed, violating
            # config.
            return (
                compliant_config
                if state["post_harness_load_count"] == 1
                else noncompliant_config
            )

        monkeypatch.setattr(m.cfg, "load_config", _stateful_load_config)

        harness_anchor = tmp_path / "harness-anchor"
        harness_anchor.mkdir()
        harness_worktree_root = tmp_path / "harness-worktrees"
        harness_config = cfg_mod.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="citadel-harness",
            repos={
                "citadel-harness": cfg_mod.RepoConfig(
                    anchor=str(harness_anchor),
                    worktree_root=str(harness_worktree_root),
                )
            },
        )
        create_worktree_calls: list[object] = []

        def _create_worktree(*a, **k):
            create_worktree_calls.append((a, k))
            state["harness_created"] = True

        monkeypatch.setattr(m.git_ops, "create_worktree", _create_worktree)
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
        monkeypatch.setattr(m.permissions, "clone_permissions", lambda *a: False)
        monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *a: False)
        monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)

        try:
            m._create_worktree_core(harness_config)
        except codename_tracking.CodenameAttributionPolicyError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected CodenameAttributionPolicyError")

        # The HARNESS worktree's own creation already happened (the harness
        # itself was never policy-blocked and the config only flipped
        # AFTER that side effect) and is genuinely left behind -- exactly
        # one `create_worktree` call (the harness's own), proving this is
        # a real post-side-effect orphan, not an early-preflight refusal
        # that never reached the harness's own git worktree/branch.
        assert len(create_worktree_calls) == 1
        harness_id = tk.list_records(tracking_path=tmp_path / "tracking")[
            0
        ].worktree_id
        # The surfaced message embeds the path via `!r}` (repr), which on
        # Windows renders backslashes doubled -- compare against that same
        # repr, not the plain path string, or this never matches on Windows.
        expected_path = repr(str(harness_worktree_root / harness_id))
        assert expected_path in message
        assert f"worktree/{harness_id}" in message
        assert "no automatic rollback" in message.lower() or "manually" in message.lower()


class TestPairedKnowledgeAllocationEarlyPreflight:
    """Round-6 review finding: `_paired_knowledge_allocation_preflight`
    lets `_create_worktree_core` catch a paired-knowledge policy
    violation BEFORE it creates the harness's own worktree/branch/record
    -- `_carve_paired_knowledge`'s own preflight only runs AFTER those
    harness side effects already exist."""

    def _knowledge_config_with_custom_wordlist(
        self, tmp_path, *, source_attribution_configured: bool,
    ):
        from agent_worktrees.codename_config import CodenameConfig
        wordlist_file = tmp_path / "custom-words.yaml"
        wordlist_file.write_text("- alpha\n- bravo\n- charlie\n")
        return types.SimpleNamespace(
            default_repo=types.SimpleNamespace(
                codename=CodenameConfig(
                    wordlist_path=str(wordlist_file), wordlist_path_configured=True,
                ),
                pr=types.SimpleNamespace(
                    enabled=True,
                    source_attribution_configured=source_attribution_configured,
                ),
            )
        )

    def _setup(self, monkeypatch, tmp_path, *, source_attribution_configured):
        k_anchor = tmp_path / "knowledge"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="citadel-knowledge"),
        )
        entry = repos_mod.RepoEntry(
            name="citadel-knowledge", repo_class="worktree",
            remote="https://example.com/citadel-knowledge.git",
            default_branch="main",
        )
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        knowledge_config = self._knowledge_config_with_custom_wordlist(
            tmp_path, source_attribution_configured=source_attribution_configured,
        )
        monkeypatch.setattr(m.cfg, "load_config", lambda project=None: knowledge_config)

    def test_blocks_before_harness_can_be_examined(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, source_attribution_configured=False)
        from agent_worktrees import codename_tracking

        try:
            m._paired_knowledge_allocation_preflight(_config(machine="test"))
        except codename_tracking.CodenameAttributionPolicyError:
            pass
        else:
            raise AssertionError("expected CodenameAttributionPolicyError")

    def test_allows_explicit_opt_in(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, source_attribution_configured=True)
        # Must not raise.
        m._paired_knowledge_allocation_preflight(_config(machine="test"))

    def test_unbound_harness_is_a_no_op(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=None, repo="", requires_external=False,
                                   bound=False),
        )
        # Must not raise -- no pairing applies.
        m._paired_knowledge_allocation_preflight(_config(machine="test"))

    def test_create_worktree_core_refuses_before_harness_side_effects(
        self, monkeypatch, tmp_path,
    ):
        # Full integration: the harness's OWN policy is fine (built-in
        # wordlist, PR disabled), but the paired-knowledge repo's is not
        # -- `_create_worktree_core` must refuse before creating the
        # harness worktree/branch/record, not only inside
        # `_carve_paired_knowledge` (which runs after those exist).
        self._setup(monkeypatch, tmp_path, source_attribution_configured=False)
        from agent_worktrees import config as cfg_mod

        harness_anchor = tmp_path / "harness-anchor"
        harness_anchor.mkdir()
        harness_config = cfg_mod.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="citadel-harness",
            repos={
                "citadel-harness": cfg_mod.RepoConfig(
                    anchor=str(harness_anchor),
                    worktree_root=str(tmp_path / "harness-worktrees"),
                )
            },
        )
        create_worktree_calls: list[object] = []
        monkeypatch.setattr(
            m.git_ops, "create_worktree",
            lambda *a, **k: create_worktree_calls.append((a, k)),
        )
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")

        from agent_worktrees import codename_tracking
        try:
            m._create_worktree_core(harness_config)
        except codename_tracking.CodenameAttributionPolicyError:
            pass
        else:
            raise AssertionError("expected CodenameAttributionPolicyError")
        assert not create_worktree_calls
        assert not (tmp_path / "harness-worktrees").exists()


class TestOriginDoesNotSkipPairing:
    """Regression (catch-22 follow-up to #3207): a delegate/system-origin
    dispatch worker needs its own paired knowledge sibling exactly as much
    as an interactive operator does -- excluding non-"user" origins from
    the carve merely traded one leak (orphaned knowledge worktrees, since
    fixed) for another (a dispatch worker silently missing the knowledge
    repo its own session guidance/skills depend on). The actual fix for
    the orphan leak is reciprocal disposal: a concluding dispatch attempt
    now disposes of BOTH halves of its own pair together (see
    `terminal_conclusion.conclude_disposable_worktree` /
    `TestReciprocalDisposal`), so pairing itself no longer needs to be
    skipped by origin. The only remaining opt-out is the explicit,
    per-call ``no_pair`` flag (for a registrar/pool with no bound
    knowledge repo to give its workers) or the whole-host
    ``AGENT_WORKTREES_NO_PAIR`` env var."""

    def _setup(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        k_anchor = tmp_path / "knowledge"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="citadel-knowledge"),
        )
        entry = repos_mod.RepoEntry(
            name="citadel-knowledge", repo_class="worktree",
            remote="https://example.com/citadel-knowledge.git",
            default_branch="main",
        )
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        monkeypatch.setattr(
            m.git_ops, "resolve_remote_name", lambda value, *, cwd: "origin",
        )
        monkeypatch.setattr(
            m.git_ops, "prepare_worktree_base",
            lambda *a, **k: types.SimpleNamespace(
                start_point="origin/main", fetched=True, fetch_error=None,
                anchor=types.SimpleNamespace(updated=True, reason="updated", behind=0),
            ),
        )
        from agent_worktrees.codename_config import CodenameConfig
        knowledge_config = types.SimpleNamespace(
            default_repo=types.SimpleNamespace(
                codename=CodenameConfig(),
                pr=types.SimpleNamespace(
                    enabled=False, source_attribution_configured=False,
                ),
                worktree_root=str(tmp_path / "worktrees"),
            )
        )
        monkeypatch.setattr(m.cfg, "load_config", lambda project=None: knowledge_config)
        create_worktree_calls: list[object] = []
        monkeypatch.setattr(
            m.git_ops, "create_worktree",
            lambda *a, **k: create_worktree_calls.append((a, k)),
        )
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
        return create_worktree_calls

    def _harness_config(self, tmp_path):
        from agent_worktrees import config as cfg_mod

        harness_anchor = tmp_path / "harness-anchor"
        harness_anchor.mkdir()
        return cfg_mod.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="citadel-harness",
            repos={
                "citadel-harness": cfg_mod.RepoConfig(
                    anchor=str(harness_anchor),
                    worktree_root=str(tmp_path / "harness-worktrees"),
                )
            },
        )

    def test_default_origin_still_carves(self, monkeypatch, tmp_path):
        create_worktree_calls = self._setup(monkeypatch, tmp_path)
        m._create_worktree_core(self._harness_config(tmp_path))
        # One for the harness worktree, one for its paired knowledge sibling.
        assert len(create_worktree_calls) == 2

    def test_user_origin_still_carves(self, monkeypatch, tmp_path):
        create_worktree_calls = self._setup(monkeypatch, tmp_path)
        m._create_worktree_core(
            self._harness_config(tmp_path), origin="user",
        )
        assert len(create_worktree_calls) == 2

    def test_delegate_origin_still_carves(self, monkeypatch, tmp_path):
        create_worktree_calls = self._setup(monkeypatch, tmp_path)
        m._create_worktree_core(
            self._harness_config(tmp_path), origin="delegate",
        )
        # Both the dispatch-owned worktree and its knowledge sibling.
        assert len(create_worktree_calls) == 2

    def test_system_origin_still_carves(self, monkeypatch, tmp_path):
        # A "system"-origin worktree can still carry kind="session" (kind
        # and origin are independent CLI flags); origin is no longer part
        # of the pairing gate at all.
        create_worktree_calls = self._setup(monkeypatch, tmp_path)
        m._create_worktree_core(
            self._harness_config(tmp_path), origin="system",
        )
        assert len(create_worktree_calls) == 2

    def test_no_pair_flag_skips_pairing_regardless_of_origin(self, monkeypatch, tmp_path):
        # The explicit per-call opt-out still works for any origin,
        # including the default ("user"-equivalent) one.
        create_worktree_calls = self._setup(monkeypatch, tmp_path)
        m._create_worktree_core(
            self._harness_config(tmp_path), origin="delegate", no_pair=True,
        )
        assert len(create_worktree_calls) == 1
        yaml_files = list((tmp_path / "tracking").glob("*.yaml"))
        assert len(yaml_files) == 1
        record = tk.load_record_by_id(
            yaml_files[0].stem, tracking_path=tmp_path / "tracking",
        )
        assert record is not None
        assert not record.is_paired


class TestCreatePairPluginComposition:
    def test_stamps_pair_before_composing_plugins(self, monkeypatch, tmp_path):
        record = tk.WorktreeRecord(
            worktree_id="wt-h",
            branch="worktree/wt-h",
            worktree_path=str(tmp_path / "harness"),
            repo="citadel-harness",
            machine="test",
            platform="windows",
            started_at="2026-09-02T00:00:00",
            last_resumed_at="2026-09-02T00:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        stamp = {
            "pair_id": "pair-1",
            "pair_role": "harness",
            "pair_ref": "test/knowledge/wt-k",
            "pair_kind": "worktree",
        }
        saved = []

        def _save(current):
            saved.append(
                (
                    current.pair_id,
                    current.pair_role,
                    current.pair_ref,
                    current.pair_kind,
                )
            )

        composed = []

        def _compose(*, cwd, config):
            assert saved == [
                ("pair-1", "harness", "test/knowledge/wt-k", "worktree")
            ]
            composed.append((cwd, config))
            return {"action": "composed"}

        config = _config()
        monkeypatch.setattr(tk, "save_record", _save)
        monkeypatch.setattr(kp, "compose_from_pair", _compose)

        result = m._stamp_and_compose_paired_knowledge(
            config, record, str(tmp_path / "harness"), stamp
        )

        assert result == {"action": "composed"}
        assert composed == [(str(tmp_path / "harness"), config)]

    def test_unpaired_create_does_not_compose_plugins(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            kp,
            "compose_from_pair",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("unpaired create must not compose")
            ),
        )

        assert (
            m._stamp_and_compose_paired_knowledge(
                _config(), object(), str(tmp_path / "harness"), None
            )
            is None
        )

    def test_composition_failure_preserves_created_pair_identity(
        self, monkeypatch, tmp_path, capsys
    ):
        record = types.SimpleNamespace(
            pair_id=None,
            pair_role=None,
            pair_ref=None,
            pair_kind=None,
        )
        saved = []
        monkeypatch.setattr(
            tk,
            "save_record",
            lambda current: saved.append(current.pair_id),
        )
        monkeypatch.setattr(
            kp,
            "compose_from_pair",
            lambda **_kwargs: (_ for _ in ()).throw(
                kp.KnowledgePluginError("settings are malformed")
            ),
        )

        result = m._stamp_and_compose_paired_knowledge(
            _config(),
            record,
            str(tmp_path / "harness"),
            {
                "pair_id": "pair-1",
                "pair_role": "harness",
                "pair_ref": "test/knowledge/wt-k",
                "pair_kind": "worktree",
            },
        )

        assert saved == ["pair-1"]
        assert record.pair_ref == "test/knowledge/wt-k"
        assert result == {"action": "error", "error": "settings are malformed"}
        assert "launch preflight will retry" in capsys.readouterr().err


class TestStateRootPairAnchor:
    def test_pair_anchor_resolves_via_state_root(
        self, monkeypatch, tmp_path, capsys
    ):
        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        harness = tmp_path / "harness"
        harness.mkdir()
        k_anchor = tmp_path / "kb"
        k_anchor.mkdir()
        rec = tk.WorktreeRecord(
            worktree_id="wt-h", branch="worktree/wt-h",
            worktree_path=str(harness), repo="citadel-harness", machine="test",
            platform="wsl", started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
            status="active", completed_at=None, sessions=[],
            pair_id="p", pair_role="harness", pair_ref="test/kb/kb",
            pair_kind="anchor",
        )
        tk.save_record(rec, tracking_dir / "wt-h.yaml")
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr(
            "agent_worktrees.__main__.os.getcwd", lambda: str(harness)
        )
        monkeypatch.setattr(
            m.cfg, "load_config", lambda: types.SimpleNamespace(knowledge_repo="kb")
        )
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="kb"),
        )
        rc = m.cmd_state_root_dispatch(["--pair"])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(k_anchor)
