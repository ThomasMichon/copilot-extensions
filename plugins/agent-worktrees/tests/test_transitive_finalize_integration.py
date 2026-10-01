"""End-to-end proof that transitive worktree-finalization safety holds.

A worktree that creates another worktree carries a ``worktree``-kind
:class:`~agent_worktrees.tracking.ResourceClaim` on it, which only clears
when the child itself finalizes (``finalize._settle_parent_obligation``,
the bottom-up "recursion-collapse"). Every scenario below drives that
guarantee through real ``finalize.validate_and_finalize`` calls -- the same
entry point ``agent-worktrees finalize`` itself uses -- rather than reading
the claim graph by hand; the one exception is the cross-machine case, which
also inspects the parent record directly to confirm it was left untouched,
since "nothing changed" has no observable `finalize` return-value signal of
its own.

Scenario walked by the single- and multi-hop tests below:

    Worktree A is created.
    Worktree A creates PR 1. A --> 1
    Worktree A creates Worktree B. A --> B
    Worktree B creates PR 2. B --> 2

    PR 1 merges. A -/-> 1
    Worktree A attempts to finalize -- blocked: B still holds 2.

    PR 2 merges. B -/-> 2
    Worktree B finalizes. A -/-> B

    Worktree A can now finalize.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize
from agent_worktrees import obligations as ob
from agent_worktrees import tracking

MACHINE = "machine-x"
PROJECT = "test-project"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _init_identity(repo: Path) -> None:
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", f"add {name}", cwd=repo)


@pytest.fixture
def _env(tmp_path: Path, monkeypatch):
    """A real anchor+origin repo, plus a shared tracking dir resolved
    identically by both ``config.tracking_dir()`` (the "current worktree's
    own record" path every finalize call uses) and ``config.project_dir()``
    (the path ``_settle_parent_obligation`` uses to find a SAME-MACHINE
    parent's record) -- the same directory under both names is exactly what
    lets A and B's records settle each other for real, not via two
    disjoint stores.
    """
    tracking_d = tmp_path / f".{PROJECT}" / "worktrees"
    tracking_d.mkdir(parents=True)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_d)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{PROJECT}")
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / ".agent-worktrees")

    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "base", str(origin), cwd=tmp_path)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    _git("init", "-q", "-b", "base", cwd=anchor)
    _init_identity(anchor)
    _commit(anchor, "base.txt", "base\n")
    _git("remote", "add", "origin", str(origin), cwd=anchor)
    _git("push", "-q", "origin", "base", cwd=anchor)

    repo_cfg = cfg.RepoConfig(
        anchor=str(anchor), worktree_root=str(tmp_path),
        default_branch="base", remote="origin",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine=MACHINE, platform="linux",
        repo_name=PROJECT, repos={PROJECT: repo_cfg},
    )
    return tmp_path, tracking_d, config


def _save_worktree(
    tracking_d: Path, worktree_id: str, *,
    owner_ref: str | None = None,
    resources: list[tracking.ResourceClaim] | None = None,
) -> None:
    """Journal a worktree record the way ``worktree_creation``/``pr_ops`` would
    have: a nonexistent on-disk checkout (so ``validate_and_finalize`` takes
    the lightweight "worktree directory already gone" content path and this
    fixture never needs a real ``git worktree add``), an untracked branch (so
    the content-on-upstream check is skipped entirely -- it only runs when the
    branch actually exists in the anchor), and whatever claim ledger /
    ownership link the scenario calls for.
    """
    rec = tracking.WorktreeRecord(
        worktree_id=worktree_id, branch=f"worktree/{worktree_id}",
        worktree_path=str(tracking_d.parent.parent / f"gone-{worktree_id}"),
        repo="o/r", machine=MACHINE, platform="linux",
        started_at="2026-10-01T00:00:00", last_resumed_at="2026-10-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        owner_ref=owner_ref, resources=list(resources or []),
    )
    tracking.save_record(rec, tracking_d / f"{worktree_id}.yaml")


def _settle_pr(tracking_d: Path, worktree_id: str, pr_ref: str) -> None:
    """Simulate the PR merging: flip its ``pr``-kind claim to ``released``."""
    path = tracking_d / f"{worktree_id}.yaml"
    rec = tracking.load_record(path)
    settled = tracking.settle_resource_claim(rec, pr_ref, ob.RELEASED, save=False)
    assert settled is not None, f"no live {pr_ref!r} claim on {worktree_id}"
    tracking.save_record(rec, path)


class TestSingleHopAToB:
    """The operator's exact scenario, one hop: A creates PR1 + worktree B;
    B creates PR2."""

    def test_finalize_a_blocked_then_unblocked_by_b(self, _env):
        _tmp_path, tracking_d, config = _env

        pr1_ref, pr2_ref = "o/r#1", "o/r#2"
        b_ref = tracking.format_claim_ref(MACHINE, PROJECT, "wt-A-child-B")
        a_ref = tracking.format_claim_ref(MACHINE, PROJECT, "wt-A")

        # A --> 1 (PR1, still open at this point) and A --> B (the child claim).
        _save_worktree(
            tracking_d, "wt-A",
            resources=[
                tracking.ResourceClaim(kind="pr", ref=pr1_ref, state=ob.ACTIVE),
                tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE),
            ],
        )
        # B --> 2 (PR2, still open), owned by A.
        _save_worktree(
            tracking_d, "wt-A-child-B", owner_ref=a_ref,
            resources=[tracking.ResourceClaim(kind="pr", ref=pr2_ref, state=ob.ACTIVE)],
        )

        # PR 1 merges -- A no longer owns it.
        _settle_pr(tracking_d, "wt-A", pr1_ref)

        # Worktree A attempts to finalize: B still holds 2 (open), so A's
        # OWN "worktree" claim on B is still unsettled -- no manual graph
        # read, just the real call.
        assert finalize.validate_and_finalize("wt-A", config) is False

        # PR 2 merges -- B no longer owns it.
        _settle_pr(tracking_d, "wt-A-child-B", pr2_ref)

        # Worktree B finalizes -- this must itself trigger the propagation
        # (`_settle_parent_obligation`) that settles A's claim on B.
        assert finalize.validate_and_finalize("wt-A-child-B", config) is True

        # Now Worktree A can finalize.
        assert finalize.validate_and_finalize("wt-A", config) is True


class TestMultiHopAToBToC:
    """One level deeper: A creates B, B creates C -- genuine multi-hop
    propagation, not just the single hop the existing unit tests cover."""

    def test_finalize_a_blocked_until_c_then_b_finalize(self, _env):
        _tmp_path, tracking_d, config = _env

        pr_c_ref = "o/r#3"
        c_ref = tracking.format_claim_ref(MACHINE, PROJECT, "wt-M-C")
        b_ref = tracking.format_claim_ref(MACHINE, PROJECT, "wt-M-B")
        a_ref = tracking.format_claim_ref(MACHINE, PROJECT, "wt-M-A")

        # A --> B (no PRs of its own in this variant -- isolating the pure
        # multi-hop worktree-claim chain).
        _save_worktree(
            tracking_d, "wt-M-A",
            resources=[tracking.ResourceClaim(kind="worktree", ref=b_ref, state=ob.ACTIVE)],
        )
        # B --> C, owned by A.
        _save_worktree(
            tracking_d, "wt-M-B", owner_ref=a_ref,
            resources=[tracking.ResourceClaim(kind="worktree", ref=c_ref, state=ob.ACTIVE)],
        )
        # C --> PR (open), owned by B.
        _save_worktree(
            tracking_d, "wt-M-C", owner_ref=b_ref,
            resources=[tracking.ResourceClaim(kind="pr", ref=pr_c_ref, state=ob.ACTIVE)],
        )

        # A is blocked (B unsettled) and B is blocked too (C unsettled) --
        # neither ancestor can finalize while the leaf still owes its PR.
        assert finalize.validate_and_finalize("wt-M-A", config) is False
        assert finalize.validate_and_finalize("wt-M-B", config) is False

        # The leaf's PR merges; C finalizes -- propagates to settle B's claim
        # on C, but A's claim on B is still unsettled (B hasn't finalized yet).
        _settle_pr(tracking_d, "wt-M-C", pr_c_ref)
        assert finalize.validate_and_finalize("wt-M-C", config) is True
        assert finalize.validate_and_finalize("wt-M-A", config) is False

        # B finalizes -- propagates one hop further, settling A's claim on B.
        assert finalize.validate_and_finalize("wt-M-B", config) is True
        assert finalize.validate_and_finalize("wt-M-A", config) is True


class TestCrossMachineParentDeferred:
    """Phase 1, item 3: a parent on a DIFFERENT machine is explicitly
    out-of-scope for same-machine propagation -- ``_settle_parent_obligation``
    no-ops and the lease mirror / ``claims sweep`` backstop is the resolution
    path instead. This proves the finalize call itself never raises or
    silently "succeeds" at settling a foreign-machine parent, since that would
    be a false transitive-safety signal."""

    def test_child_finalize_does_not_touch_foreign_machine_parent(self, _env):
        _tmp_path, tracking_d, config = _env

        pr_ref = "o/r#9"
        child_ref = tracking.format_claim_ref(MACHINE, PROJECT, "wt-X-child")
        foreign_parent_ref = tracking.format_claim_ref(
            "other-machine", PROJECT, "wt-X-parent")

        # The parent record lives in the SAME tracking dir for this test's
        # own bookkeeping convenience, but is declared as owned by a
        # different machine -- the exact shape `_settle_parent_obligation`
        # checks before writing anything.
        _save_worktree(
            tracking_d, "wt-X-parent",
            resources=[tracking.ResourceClaim(
                kind="worktree", ref=child_ref, state=ob.ACTIVE)],
        )
        _save_worktree(
            tracking_d, "wt-X-child", owner_ref=foreign_parent_ref,
            resources=[tracking.ResourceClaim(kind="pr", ref=pr_ref, state=ob.ACTIVE)],
        )

        _settle_pr(tracking_d, "wt-X-child", pr_ref)
        assert finalize.validate_and_finalize("wt-X-child", config) is True

        # The "parent"'s claim on the child is untouched -- same-machine
        # propagation explicitly skipped a foreign-machine owner ref.
        parent_after = tracking.load_record(tracking_d / "wt-X-parent.yaml")
        assert parent_after.resources[0].state == ob.ACTIVE
