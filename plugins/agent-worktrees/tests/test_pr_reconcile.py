"""Tests for agent_worktrees.pr_reconcile + the `reconcile` CLI verb
(agent-worktrees-fleet-flows Phase 2, aperture-labs #2740)."""

from __future__ import annotations

from agent_worktrees import config as cfg
from agent_worktrees import pr_reconcile, reconcile_cli, tracking


def _config():
    return cfg.Config(
        srcroot="/s", machine="m", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            pr=cfg.PRConfig(enabled=True, provider="gitea"),
        )},
    )


def _record(tmp_path, monkeypatch, worktree_id, *, status="active", pr=None):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(
        tracking, "_owning_tracking_dir",
        lambda worktree_id, repo=None: tmp_path,
    )
    rec = tracking.WorktreeRecord(
        worktree_id=worktree_id, branch=f"worktree/{worktree_id}",
        worktree_path=str(tmp_path / worktree_id), repo="o/r", machine="m",
        platform="linux", started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
        status=status, completed_at=None, sessions=None,
    )
    if pr is not None:
        rec.pr = pr
    tracking.save_record(rec)
    return rec


class _FakeHeadSearchProvider:
    """A fake provider resolving `find_pull_by_head` from an in-memory map."""

    name = "gitea"

    def __init__(self, by_head):
        self._by_head = by_head

    def find_pull_by_head(self, repo, head, *, api_base="", token=None):
        return self._by_head.get(head)

    def get_pull(self, repo, number, *, api_base="", token=None):
        for pull in self._by_head.values():
            if pull is not None and pull.number == number:
                return pull
        raise AssertionError(f"no fake PR #{number}")


def _patch_provider(monkeypatch, fake):
    import agent_worktrees.providers as prov

    monkeypatch.setattr(prov, "get_provider", lambda name: fake)
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "t")


class TestHealNumberlessActivePr:
    def test_resolves_number_via_head_branch(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-a",
            pr=tracking.PRRecord(state="creating", branch="pr/wt-a", provider="gitea", repo="o/r"),
        )
        found = PullResult(
            url="https://x/pull/9", number=9, state="open", merged=False,
            head_sha="abc", base_ref="master",
        )
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({"pr/wt-a": found}))

        healed = pr_reconcile.heal_numberless_active_pr(rec, _config())
        assert healed is True
        assert rec.active_pr().number == 9
        assert rec.active_pr().state == "open"
        # Persisted to disk.
        assert tracking.load_record(rec.yaml_path).active_pr().number == 9

    def test_heals_a_since_merged_pr(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-b",
            pr=tracking.PRRecord(state="creating", branch="pr/wt-b", provider="gitea", repo="o/r"),
        )
        found = PullResult(
            url="https://x/pull/11", number=11, state="merged", merged=True,
        )
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({"pr/wt-b": found}))

        assert pr_reconcile.heal_numberless_active_pr(rec, _config()) is True
        assert rec.active_pr().number == 11
        assert rec.active_pr().state == "merged"
        assert rec.active_pr().closed_at

    def test_noop_when_number_already_present(self, tmp_path, monkeypatch):
        rec = _record(
            tmp_path, monkeypatch, "wt-c",
            pr=tracking.PRRecord(state="open", number=3, branch="pr/wt-c", provider="gitea", repo="o/r"),
        )
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({}))
        assert pr_reconcile.heal_numberless_active_pr(rec, _config()) is False

    def test_noop_when_no_tracked_branch(self, tmp_path, monkeypatch):
        rec = _record(
            tmp_path, monkeypatch, "wt-d",
            pr=tracking.PRRecord(state="creating", branch="", provider="gitea", repo="o/r"),
        )
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({}))
        assert pr_reconcile.heal_numberless_active_pr(rec, _config()) is False

    def test_noop_when_provider_finds_nothing(self, tmp_path, monkeypatch):
        rec = _record(
            tmp_path, monkeypatch, "wt-e",
            pr=tracking.PRRecord(state="creating", branch="pr/wt-e", provider="gitea", repo="o/r"),
        )
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({}))
        assert pr_reconcile.heal_numberless_active_pr(rec, _config()) is False
        assert rec.active_pr().number is None


class TestReconcilePrState:
    def test_heals_and_flips_merged_on_a_finalized_worktree(self, tmp_path, monkeypatch):
        # The exact Phase-2 validation scenario: a stale-open, number-less PR
        # record on a FINALIZED worktree (the render-time reconcile, #2102,
        # never runs against it -- no live session drives it there).
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-f", status="finalized",
            pr=tracking.PRRecord(state="creating", branch="pr/wt-f", provider="gitea", repo="o/r"),
        )
        found = PullResult(
            url="https://x/pull/21", number=21, state="merged", merged=True,
        )
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({"pr/wt-f": found}))

        changed = pr_reconcile.reconcile_pr_state(rec, _config())
        assert changed is True
        assert rec.active_pr().number == 21
        assert rec.active_pr().state == "merged"
        assert tracking.load_record(rec.yaml_path).active_pr().state == "merged"

    def test_returns_false_when_nothing_changed(self, tmp_path, monkeypatch):
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-g",
            pr=tracking.PRRecord(state="merged", number=5, branch="pr/wt-g", provider="gitea", repo="o/r", closed_at="2026-01-01T00:00:00"),
        )
        # Already-terminal: _reconcile_active_pr short-circuits before ever
        # calling the provider.
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({"pr/wt-g": PullResult(number=5, state="merged", merged=True)}))
        assert pr_reconcile.reconcile_pr_state(rec, _config()) is False

    def test_none_record_is_a_noop(self):
        assert pr_reconcile.reconcile_pr_state(None, _config()) is False


class TestReconcileCli:
    def test_reconcile_one_worktree_json(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees.providers import PullResult

        rec = _record(
            tmp_path, monkeypatch, "wt-h",
            pr=tracking.PRRecord(state="creating", branch="pr/wt-h", provider="gitea", repo="o/r"),
        )
        found = PullResult(number=31, state="open", merged=False)
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({"pr/wt-h": found}))
        monkeypatch.setattr(cfg, "load_config", lambda: _config())

        rc = reconcile_cli.run_reconcile(["--worktree-id", "wt-h", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"changed": ["wt-h"]' in out
        assert tracking.load_record(rec.yaml_path).active_pr().number == 31

    def test_reconcile_all_includes_finalized(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees.providers import PullResult

        _record(tmp_path, monkeypatch, "wt-i", status="active")
        _record(
            tmp_path, monkeypatch, "wt-j", status="finalized",
            pr=tracking.PRRecord(state="creating", branch="pr/wt-j", provider="gitea", repo="o/r"),
        )
        found = PullResult(number=41, state="merged", merged=True)
        _patch_provider(monkeypatch, _FakeHeadSearchProvider({"pr/wt-j": found}))
        monkeypatch.setattr(cfg, "load_config", lambda: _config())

        rc = reconcile_cli.run_reconcile(["--json"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"checked": 2' in out
        assert "wt-j" in out

    def test_reconcile_unknown_worktree_id_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        monkeypatch.setattr(cfg, "load_config", lambda: _config())
        rc = reconcile_cli.run_reconcile(["--worktree-id", "no-such-wt", "--json"])
        assert rc == 1
        assert "no-such-wt" in capsys.readouterr().out

    def test_reconcile_rejects_path_traversal_worktree_id(
        self, tmp_path, monkeypatch, capsys
    ):
        # A worktree_id is a raw user-supplied string -- '../../etc/passwd'
        # must never be allowed to escape cfg.tracking_dir().
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        monkeypatch.setattr(cfg, "load_config", lambda: _config())
        secret = tmp_path.parent / "escaped.yaml"
        secret.write_text("do-not-touch: true", encoding="utf-8")
        rc = reconcile_cli.run_reconcile(
            ["--worktree-id", "../escaped", "--json"]
        )
        assert rc == 1
        assert "invalid --worktree-id" in capsys.readouterr().out
        assert secret.read_text(encoding="utf-8") == "do-not-touch: true"
