"""Tests for finalize's backup open-PR gate (`finalize_open_pr_gate`).

Defense-in-depth: `_pr_finalize_precondition`'s content-vs-upstream fast path
can wave a worktree through as "safe to prune" purely because its content
happens to already match upstream -- independent of whether the worktree's
OWN attributed PR is still open. This gate re-checks every tracked PR against
the live provider (never trusting the possibly-stale local `state` field) and
refuses finalize while any of them is still genuinely open.
"""

from __future__ import annotations

from agent_worktrees import config as cfg
from agent_worktrees import tracking
from agent_worktrees.finalize_open_pr_gate import (
    assert_no_live_pr as _assert_no_live_pr,
    reconcile_every_live_pr as _reconcile_every_live_pr,
)


def _config():
    return cfg.Config(
        srcroot="/s", machine="m", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            pr=cfg.PRConfig(enabled=True, provider="gitea"),
        )},
    )


def _record(tmp_path, monkeypatch, worktree_id, *, prs=None, repo="o/r"):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(
        tracking, "_owning_tracking_dir",
        lambda worktree_id, repo=None: tmp_path,
    )
    rec = tracking.WorktreeRecord(
        worktree_id=worktree_id, branch=f"worktree/{worktree_id}",
        worktree_path=str(tmp_path / worktree_id), repo=repo, machine="m",
        platform="linux", started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
        status="active", completed_at=None, sessions=None,
        prs=list(prs or []),
    )
    tracking.save_record(rec)
    return rec


class _FakeProvider:
    """Resolves `get_pull` by (repo, number) from an in-memory map."""

    name = "gitea"

    def __init__(self, by_number):
        self._by_number = by_number

    def get_pull(self, repo, number, *, api_base="", token=None):
        return self._by_number[number]

    def find_pull_by_head(self, repo, head, *, api_base="", token=None):
        return None


class _BoomProvider:
    name = "gitea"

    def get_pull(self, repo, number, *, api_base="", token=None):
        raise RuntimeError("provider unreachable")

    def find_pull_by_head(self, repo, head, *, api_base="", token=None):
        raise RuntimeError("provider unreachable")


def _patch_provider(monkeypatch, fake):
    import agent_worktrees.providers as prov

    monkeypatch.setattr(prov, "get_provider", lambda name: fake)
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "t")


class TestAssertNoLivePr:
    def test_no_record_proceeds(self):
        assert _assert_no_live_pr(None, _config(), "wt") is True

    def test_no_tracked_prs_proceeds(self, tmp_path, monkeypatch):
        rec = _record(tmp_path, monkeypatch, "wt-a", prs=[])
        _patch_provider(monkeypatch, _FakeProvider({}))
        assert _assert_no_live_pr(rec, _config(), "wt-a") is True

    def test_blocks_when_live_check_confirms_still_open(
        self, tmp_path, monkeypatch, capsys,
    ):
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-b",
            prs=[tracking.PRRecord(
                state="open", number=42, branch="worktree/wt-b",
                provider="gitea", repo="o/r", url="https://x/pull/42",
            )],
        )
        _patch_provider(
            monkeypatch,
            _FakeProvider({42: PullResult(number=42, state="open", merged=False)}),
        )
        assert _assert_no_live_pr(rec, _config(), "wt-b") is False
        combined = capsys.readouterr()
        assert "still-open" in (combined.err + combined.out).lower()
        assert "https://x/pull/42" in (combined.err + combined.out)

    def test_heals_stale_local_open_state_then_proceeds(
        self, tmp_path, monkeypatch,
    ):
        """The exact incident this gate targets in reverse: local state says
        `open`, but the LIVE provider now says merged -- the gate must heal
        and proceed, not block on stale cached state."""
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-c",
            prs=[tracking.PRRecord(
                state="open", number=7, branch="worktree/wt-c",
                provider="gitea", repo="o/r",
            )],
        )
        _patch_provider(
            monkeypatch,
            _FakeProvider({7: PullResult(number=7, state="merged", merged=True)}),
        )
        assert _assert_no_live_pr(rec, _config(), "wt-c") is True
        assert rec.active_pr().state == "merged"
        assert tracking.load_record(rec.yaml_path).active_pr().state == "merged"

    def test_force_overrides_the_block(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-d",
            prs=[tracking.PRRecord(
                state="open", number=8, branch="worktree/wt-d",
                provider="gitea", repo="o/r",
            )],
        )
        _patch_provider(
            monkeypatch,
            _FakeProvider({8: PullResult(number=8, state="open", merged=False)}),
        )
        assert _assert_no_live_pr(rec, _config(), "wt-d", force=True) is True
        combined = capsys.readouterr()
        assert "finalizing anyway" in (combined.err + combined.out).lower()

    def test_already_terminal_record_proceeds_without_provider_call(
        self, tmp_path, monkeypatch,
    ):
        rec = _record(
            tmp_path, monkeypatch, "wt-e",
            prs=[tracking.PRRecord(
                state="merged", number=9, branch="worktree/wt-e",
                provider="gitea", repo="o/r", closed_at="2026-01-01T00:00:00",
            )],
        )
        _patch_provider(monkeypatch, _BoomProvider())
        assert _assert_no_live_pr(rec, _config(), "wt-e") is True

    def test_provider_failure_fails_open(self, tmp_path, monkeypatch):
        """A transient provider/network outage must not block finalize for
        every worktree that ever had a PR -- the reconcile helpers already
        degrade to the untouched local state on any provider exception."""
        rec = _record(
            tmp_path, monkeypatch, "wt-f",
            prs=[tracking.PRRecord(
                state="merged", number=10, branch="worktree/wt-f",
                provider="gitea", repo="o/r", closed_at="2026-01-01T00:00:00",
            )],
        )
        _patch_provider(monkeypatch, _BoomProvider())
        # Already terminal locally -- no provider call happens, so this is a
        # plain proceed regardless of the (unreachable) provider.
        assert _assert_no_live_pr(rec, _config(), "wt-f") is True


class TestReconcileEveryLivePr:
    def test_reconciles_a_non_active_parallel_pr(self, tmp_path, monkeypatch):
        """Two non-terminal PRs tracked at once (parallel PRs) -- only one is
        `active_pr()`; the OTHER must still get a live re-check."""
        from agent_worktrees.providers import PullResult

        older = tracking.PRRecord(
            state="open", number=1, branch="pr/older", provider="gitea",
            repo="o/r", opened_at="2026-01-01T00:00:00",
        )
        newer = tracking.PRRecord(
            state="open", number=2, branch="pr/newer", provider="gitea",
            repo="o/r", opened_at="2026-02-01T00:00:00",
        )
        rec = _record(tmp_path, monkeypatch, "wt-g", prs=[older, newer])
        assert rec.active_pr() is newer
        _patch_provider(
            monkeypatch,
            _FakeProvider({
                1: PullResult(number=1, state="merged", merged=True),
                2: PullResult(number=2, state="open", merged=False),
            }),
        )
        _reconcile_every_live_pr(rec, _config())
        assert rec.active_pr() is newer
        older_after = next(p for p in rec.prs if p.number == 1)
        assert older_after.state == "merged"

    def test_other_entry_provider_failure_does_not_raise(
        self, tmp_path, monkeypatch,
    ):
        older = tracking.PRRecord(
            state="open", number=3, branch="pr/older2", provider="gitea",
            repo="o/r", opened_at="2026-01-01T00:00:00",
        )
        newer = tracking.PRRecord(
            state="merged", number=4, branch="pr/newer2", provider="gitea",
            repo="o/r", opened_at="2026-02-01T00:00:00",
            closed_at="2026-02-02T00:00:00",
        )
        rec = _record(tmp_path, monkeypatch, "wt-h", prs=[older, newer])
        _patch_provider(monkeypatch, _BoomProvider())
        _reconcile_every_live_pr(rec, _config())  # must not raise
        assert next(p for p in rec.prs if p.number == 3).state == "open"


class _SlugCheckingProvider:
    """Answers only for the hosting ``o/r`` slug, as a real provider does: a
    project-name slug is an unknown repository."""

    name = "gitea"

    def __init__(self, by_number):
        self._by_number = by_number

    def get_pull(self, repo, number, *, api_base="", token=None):
        if repo != "o/r":
            raise RuntimeError(f"404: no repository {repo!r}")
        return self._by_number[number]

    def find_pull_by_head(self, repo, head, *, api_base="", token=None):
        return None


class TestLegacyProjectNameRecords:
    """An older record keeps the project name as its ``repo`` and no PR-level repo;
    only the PR URL names the hosting repository. A PR merged long ago must heal,
    not read as open forever."""

    def _legacy(self, tmp_path, monkeypatch, worktree_id, numbers):
        prs = [tracking.PRRecord(state="open", number=n, url=f"https://x/o/r/pulls/{n}")
               for n in numbers]
        return _record(tmp_path, monkeypatch, worktree_id, prs=prs, repo="ext")

    def test_a_merged_pr_tracked_by_url_heals_and_finalize_proceeds(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult

        rec = self._legacy(tmp_path, monkeypatch, "wt-legacy", [7])
        _patch_provider(monkeypatch, _SlugCheckingProvider(
            {7: PullResult(number=7, state="closed", merged=True)}))
        assert _assert_no_live_pr(rec, _config(), "wt-legacy") is True
        assert tracking.load_record(rec.yaml_path).active_pr().state == "merged"

    def test_every_tracked_legacy_pr_heals(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult

        rec = self._legacy(tmp_path, monkeypatch, "wt-legacy2", [7, 8])
        _patch_provider(monkeypatch, _SlugCheckingProvider({
            7: PullResult(number=7, state="closed", merged=True),
            8: PullResult(number=8, state="closed", merged=True)}))
        assert _assert_no_live_pr(rec, _config(), "wt-legacy2") is True
        assert [p.state for p in tracking.load_record(rec.yaml_path).prs] == ["merged", "merged"]

    def test_a_url_naming_another_pr_is_never_trusted(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult

        rec = _record(tmp_path, monkeypatch, "wt-mismatch", repo="ext", prs=[
            tracking.PRRecord(state="open", number=7, url="https://x/o/r/pulls/9")])
        _patch_provider(monkeypatch, _SlugCheckingProvider(
            {7: PullResult(number=7, state="closed", merged=True)}))
        assert _assert_no_live_pr(rec, _config(), "wt-mismatch") is False


def test_tracked_pr_slug():
    from agent_worktrees.pr_reconcile import tracked_pr_slug

    def pr(**kw):
        return tracking.PRRecord(**{"number": 7, **kw})

    assert tracked_pr_slug(pr(repo="o/r")) == "o/r"
    assert tracked_pr_slug(pr(), "o/r") == "o/r"
    assert tracked_pr_slug(pr(url="https://x/o/r/pulls/7"), "ext") == "o/r"
    assert tracked_pr_slug(pr(repo="ext", url="https://x/o/r/pulls/7")) == "o/r"
    assert tracked_pr_slug(pr(url="https://x/o/r/pulls/9"), "ext") is None   # another PR
    assert tracked_pr_slug(pr(), "ext") is None                              # nothing to resolve by
    assert tracked_pr_slug(pr()) == ""                                       # nothing recorded
    assert tracked_pr_slug(pr(url="https://h/gitea/o/r/pulls/7"), "ext", "https://h/gitea") == "o/r"


def test_healing_the_active_pr_does_not_skip_the_next_one(tmp_path, monkeypatch):
    """Two open entries, both merged on the provider: reconciling the active one
    makes the other the new active entry, and it must still be read."""
    from agent_worktrees.providers import PullResult

    rec = _record(tmp_path, monkeypatch, "wt-two", prs=[
        tracking.PRRecord(state="open", number=n, provider="gitea", repo="o/r") for n in (7, 8)])
    _patch_provider(monkeypatch, _FakeProvider({
        7: PullResult(number=7, state="closed", merged=True),
        8: PullResult(number=8, state="closed", merged=True)}))
    assert _assert_no_live_pr(rec, _config(), "wt-two") is True
