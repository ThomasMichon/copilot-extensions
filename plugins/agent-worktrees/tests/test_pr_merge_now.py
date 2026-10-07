"""Tests for `pr-merge --now` -- the submitter-direct merge dispatch.

Covers ``_pr_merge_now``: it merges only on a ``pr-self-merge`` repo, uses an
admin bypass only for non-blocking review posture, refuses-with-reminder on
every other flow profile, and previews without merging under ``--dry-run``.
The provider seam is mocked so no ``gh`` is invoked.
"""

from __future__ import annotations

from types import SimpleNamespace

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import pr_config
from agent_worktrees import pr_contract as pc
from agent_worktrees import providers as prov


def _args(**over):
    base = dict(repo="o/r", pr=7, sweep=False, host="", token=None,
                json=False, now=True)
    base.update(over)
    return SimpleNamespace(**base)


def _prcfg(**over):
    base = dict(
        provider="github",
        api_base="",
        prefer_auto_merge=False,
        automerge_label="",
        squash=True,
        delete_source_branch=True,
        bypass_policy=False,
        bypass_reason="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _self_merge_flow():
    return pc.classify_pr_flow(enabled=True, required=True, provider="github",
                               automerge_label="", self_approve=True,
                               reviewer="copilot", review_blocking=False)


class _FakeProvider:
    name = "github"

    def __init__(self, err="", auto_err="unsupported", review_gate=None, live_head=""):
        self._live_head = live_head
        self._err = err
        self._auto_err = auto_err
        self._review_gate = review_gate
        self.calls = []
        self.auto_calls = []
        self.complete_calls = []
        self.review_gate_calls = []

    def merge_pull(self, repo, number, *, squash=True, admin=False,
                   api_base="", token=None, delete_source_branch=True,
                   expected_head_sha=""):
        self.calls.append(dict(repo=repo, number=number, squash=squash,
                               admin=admin,
                               delete_source_branch=delete_source_branch,
                               expected_head_sha=expected_head_sha))
        return self._err

    def enable_auto_merge(self, repo, number, *, squash=True,
                          api_base="", token=None, delete_source_branch=True,
                          expected_head_sha=""):
        self.auto_calls.append(dict(repo=repo, number=number, squash=squash,
                                    delete_source_branch=delete_source_branch,
                                    expected_head_sha=expected_head_sha))
        return self._auto_err

    def request_auto_complete(
        self, repo, number, *, api_base="", token=None, automerge_label="",
        squash=True, delete_source_branch=True, bypass_policy=False,
        bypass_reason="",
    ):
        self.complete_calls.append(dict(
            repo=repo,
            number=number,
            api_base=api_base,
            token=token,
            automerge_label=automerge_label,
            squash=squash,
            delete_source_branch=delete_source_branch,
            bypass_policy=bypass_policy,
            bypass_reason=bypass_reason,
        ))
        return self._auto_err

    def get_pull(self, repo, number, *, api_base="", token=None):
        return prov.PullResult(number=number, head_sha=self._live_head)

    def pull_review_gate(self, repo, number, *, api_base="", token=None):
        self.review_gate_calls.append(dict(repo=repo, number=number))
        return self._review_gate if self._review_gate is not None else (False, None)


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(prov, "get_provider", lambda name: provider)
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "tok")


def _config(prcfg):
    repo = cfg.RepoConfig(
        anchor="/anchor",
        worktree_root="/worktrees",
        default_branch="main",
        pr=prcfg,
    )
    return cfg.Config(
        srcroot="/src",
        machine="host",
        platform="linux",
        repo_name="repo",
        repos={"repo": repo},
    )


def _patch_same_repo_resolution(monkeypatch):
    """These tests use a fake anchor with no real registry/remote, so
    ``pr_config.resolve_repo_config_for_slug``'s normal registry-based
    resolution can't find a match -- patch it to report the explicit slug
    as the caller's own active repo, the way it would on a real checkout
    whose registry/git remote actually resolves to that slug."""
    monkeypatch.setattr(
        pr_config,
        "resolve_repo_config_for_slug",
        lambda config, slug: pr_config.ForeignRepoResolution(
            config.default_repo, config.repo_name, same_as_active=True
        ),
    )


def test_now_self_merge_calls_merge_pull_squash_admin(monkeypatch, capsys):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True, delete_source_branch=True, expected_head_sha="")]


def test_now_blocking_review_never_uses_admin_bypass(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    flow = pc.classify_pr_flow(
        enabled=True,
        required=True,
        provider="github",
        automerge_label="",
        merge_actor="submitter-direct",
        reviewer="independent reviewer",
        review_blocking=True,
    )
    rc = m._pr_merge_now(_args(), _prcfg(), flow, apply=True)
    assert rc == 0
    assert fake.calls == [
        dict(repo="o/r", number=7, squash=True, admin=False, delete_source_branch=True, expected_head_sha="")
    ]


def test_now_refused_on_non_self_merge(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    human = pc.classify_pr_flow(enabled=True, required=True, provider="github",
                                automerge_label="", reviewer="agent:reviewer")
    rc = m._pr_merge_now(_args(), _prcfg(), human, apply=True)
    assert rc == 2
    assert fake.calls == []  # never merged


def test_now_refused_when_live_permission_is_read_only(monkeypatch, capsys):
    # Repo config selects pr-self-merge (a maintainer set it up), but the
    # ACTING identity's own live permission is read-only -- a contributor
    # running the same flow must be refused, not silently attempt (and fail)
    # a merge they have no rights to.
    fake = _FakeProvider()
    fake.get_repo_policy = lambda repo, **kw: SimpleNamespace(
        supported=True, viewer_permission="read",
    )
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 2
    assert fake.calls == []  # never attempted
    # The refusal must NOT tell a permission-denied contributor to retry
    # `pr-merge --now` -- that's the wrong-caller guidance meant for someone
    # who forgot --now, not for a confirmed lack of write access. output.err
    # prints to stdout (not stderr) so check the combined captured output,
    # not just capsys.err.
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "pr-merge --now" not in combined
    assert "wait for a maintainer" in combined


def test_now_proceeds_when_live_permission_is_write(monkeypatch):
    fake = _FakeProvider()
    fake.get_repo_policy = lambda repo, **kw: SimpleNamespace(
        supported=True, viewer_permission="write",
    )
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert len(fake.calls) == 1


def test_now_proceeds_when_permission_read_unsupported(monkeypatch):
    # A provider/policy read that can't determine viewer_permission (unknown,
    # not a confident denial) must fail OPEN -- unchanged from before this
    # gate existed.
    fake = _FakeProvider()
    fake.get_repo_policy = lambda repo, **kw: SimpleNamespace(supported=False)
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert len(fake.calls) == 1


def test_now_rejects_all_sweep(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(sweep=True, pr=None), _prcfg(),
                         _self_merge_flow(), apply=True)
    assert rc == 2
    assert fake.calls == []


def test_now_dry_run_previews_without_merging(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=False)
    assert rc == 0
    assert fake.calls == []  # dry-run merges nothing


def test_now_surfaces_merge_failure(monkeypatch):
    fake = _FakeProvider(err="gh pr merge failed for o/r#7: not mergeable")
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 1


def test_now_passes_tracked_pushed_head_as_match_head_commit(monkeypatch):
    """`pr-merge --now` must hand the provider's merge call the locally
    tracked pushed head (via --match-head-commit on GitHub) whenever a
    ``config`` is available, so a stale PR-object read can never silently
    merge the wrong commit (ThomasMichon/copilot-extensions#4949)."""
    from agent_worktrees import pr_cli

    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        pr_cli, "_tracked_pr_pushed_head",
        lambda config, repo, number, provider: "just-pushed-sha",
    )
    rc = m._pr_merge_now(
        _args(), _prcfg(), _self_merge_flow(), apply=True, config=object(),
    )
    assert rc == 0
    assert fake.calls == [
        dict(repo="o/r", number=7, squash=True, admin=True,
             delete_source_branch=True, expected_head_sha="just-pushed-sha")
    ]


def test_now_passes_tracked_pushed_head_to_auto_merge_default_path(monkeypatch):
    """The SAME ``--match-head-commit`` protection must reach the native
    AUTO-MERGE path too -- ``prefer_auto_merge=True`` is this repo's
    default, and auto-merge can complete immediately rather than only arm,
    so a regression that stopped threading ``expected_head_sha`` through
    THIS branch specifically would leave the stated protection unverified
    on the path most callers actually take (ThomasMichon/copilot-extensions#4949)."""
    from agent_worktrees import pr_cli

    fake = _FakeProvider(auto_err="")  # auto-merge succeeds
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        pr_cli, "_tracked_pr_pushed_head",
        lambda config, repo, number, provider: "just-pushed-sha",
    )
    rc = m._pr_merge_now(
        _args(), _prcfg(prefer_auto_merge=True), _self_merge_flow(),
        apply=True, config=object(),
    )
    assert rc == 0
    assert fake.auto_calls == [
        dict(repo="o/r", number=7, squash=True,
             delete_source_branch=True, expected_head_sha="just-pushed-sha")
    ]
    assert fake.calls == []  # no immediate direct merge


import pytest  # noqa: E402


@pytest.mark.parametrize(("adopted", "expected"), [("manually-pushed-sha", "manually-pushed-sha"),
                                                   ("", "just-pushed-sha")])
def test_now_adopts_a_moved_head_only_when_it_is_the_worktrees_own(monkeypatch, adopted, expected):
    """The provider's head moved past the recorded one (a plain `git push`):
    `pr-merge` merges it only when the tracking worktree's own HEAD is that
    head, else the stale expectation stands and the provider refuses."""
    from agent_worktrees import pr_cli

    fake = _FakeProvider(live_head="manually-pushed-sha")
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(pr_cli, "_tracked_pr_pushed_head",
                        lambda config, repo, number, provider: "just-pushed-sha")
    asked = []
    monkeypatch.setattr(pr_cli, "_adopt_pushed_head",
                        lambda config, repo, number, provider, live: asked.append(live) or adopted)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True, config=object())
    assert rc == 0 and asked == ["manually-pushed-sha"]
    assert fake.calls[0]["expected_head_sha"] == expected


def test_now_omits_match_head_commit_without_config(monkeypatch):
    """No ``config`` (e.g. an older caller) must not crash and must not
    fabricate a safety check it has no evidence for."""
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.calls == [
        dict(repo="o/r", number=7, squash=True, admin=True,
             delete_source_branch=True, expected_head_sha="")
    ]


def test_now_json_success_shape(monkeypatch, capsys):
    import json as _json
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(), _self_merge_flow(),
                         apply=True)
    assert rc == 0
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["applied"] is True
    assert out["action"] == "merge"
    assert out["reminder"]["profile"] == "pr-self-merge"


def test_dispatch_maintain_role_resolves_self_merge_once(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    calls = []
    monkeypatch.setattr(
        prov,
        "actor_viewer_permission",
        lambda *a, **k: calls.append((a, k)) or "maintain",
    )
    prcfg = cfg.PRConfig(
        enabled=True,
        required=True,
        provider="github",
        prefer_auto_merge=False,
        roles={
            "maintain": cfg.PRRoleOverride(merge_actor="submitter-direct"),
        },
    )
    monkeypatch.setattr(cfg, "load_config", lambda path=None: _config(prcfg))
    _patch_same_repo_resolution(monkeypatch)

    rc = m.cmd_pr_merge_dispatch(["o/r", "7", "--now"])

    assert rc == 0
    assert len(calls) == 1
    assert fake.calls == [
        dict(repo="o/r", number=7, squash=True, admin=True, delete_source_branch=True, expected_head_sha=""),
    ]


def test_dispatch_write_role_explicitly_disables_self_merge(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    calls = []
    monkeypatch.setattr(
        prov,
        "actor_viewer_permission",
        lambda *a, **k: calls.append((a, k)) or "write",
    )
    prcfg = cfg.PRConfig(
        enabled=True,
        required=True,
        provider="github",
        merge_actor="submitter-direct",
        prefer_auto_merge=False,
        roles={"write": cfg.PRRoleOverride(merge_actor="")},
    )
    monkeypatch.setattr(cfg, "load_config", lambda path=None: _config(prcfg))
    _patch_same_repo_resolution(monkeypatch)

    rc = m.cmd_pr_merge_dispatch(["o/r", "7", "--now"])

    assert rc == 2
    assert len(calls) == 1
    assert fake.calls == []


def test_dispatch_unknown_permission_uses_safe_base_profile(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    calls = []
    monkeypatch.setattr(
        prov,
        "actor_viewer_permission",
        lambda *a, **k: calls.append((a, k)) or "",
    )
    prcfg = cfg.PRConfig(
        enabled=True,
        required=True,
        provider="github",
        prefer_auto_merge=False,
        roles={
            "maintain": cfg.PRRoleOverride(merge_actor="submitter-direct"),
        },
    )
    monkeypatch.setattr(cfg, "load_config", lambda path=None: _config(prcfg))
    _patch_same_repo_resolution(monkeypatch)

    rc = m.cmd_pr_merge_dispatch(["o/r", "7", "--now"])

    assert rc == 2
    assert len(calls) == 1
    assert fake.calls == []


# --- prefer_auto_merge policy (#225) ---------------------------------------

def test_prefer_auto_merge_arms_native_auto_merge(monkeypatch, capsys):
    # prefer_auto_merge=True + provider supports it -> arm auto-merge, do NOT
    # perform an immediate merge; report pending (not merged) + steer to watch.
    import json as _json
    fake = _FakeProvider(auto_err="")  # auto-merge succeeds
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(prefer_auto_merge=True),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha="")]
    assert fake.calls == []  # no immediate merge
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["action"] == "auto-merge"
    assert out["merged"] is False
    assert out["applied"] is True


def test_prefer_auto_merge_falls_back_to_direct_when_unsupported(monkeypatch):
    # prefer_auto_merge=True but auto-merge can't be armed -> fall back to the
    # immediate admin squash merge (never leaves the PR un-merged).
    fake = _FakeProvider(auto_err="auto-merge not allowed on this repo")
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(prefer_auto_merge=True),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls  # attempted
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True, delete_source_branch=True, expected_head_sha="")]


def test_prefer_auto_merge_off_merges_directly(monkeypatch):
    # prefer_auto_merge=False -> straight to the immediate merge, no auto attempt.
    fake = _FakeProvider(auto_err="")
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(prefer_auto_merge=False),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == []
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True, delete_source_branch=True, expected_head_sha="")]


def test_prefer_auto_merge_dry_run_previews_auto(monkeypatch, capsys):
    import json as _json
    fake = _FakeProvider(auto_err="")
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(prefer_auto_merge=True),
                         _self_merge_flow(), apply=False)
    assert rc == 0
    assert fake.auto_calls == [] and fake.calls == []  # dry-run touches nothing
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["prefer_auto_merge"] is True
    assert "auto-merge" in out["would"]


# --- bypassable review-gate short-circuit (#3296 follow-up) ----------------

def test_bypassable_review_gate_skips_auto_merge_and_admin_bypasses(
    monkeypatch, capsys,
):
    # A live, required review that the acting identity CAN bypass must skip
    # straight to an admin merge -- auto-merge would otherwise arm
    # successfully and then sit blocked on that same review forever.
    import json as _json
    fake = _FakeProvider(auto_err="", review_gate=(True, True))
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(prefer_auto_merge=True),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == []  # never attempted -- would have queued forever
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True, delete_source_branch=True, expected_head_sha="")]
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["action"] == "merge"
    assert out["applied"] is True
    assert out["bypassed_review_gate"] is True


def test_non_bypassable_review_gate_still_arms_auto_merge(monkeypatch, capsys):
    # A required review the acting identity CANNOT bypass must still go
    # through ordinary auto-merge -- never an unauthorized admin bypass.
    import json as _json
    fake = _FakeProvider(auto_err="", review_gate=(True, False))
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(prefer_auto_merge=True),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha="")]
    assert fake.calls == []
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["action"] == "auto-merge"


def test_no_review_required_arms_auto_merge_as_before(monkeypatch, capsys):
    # No review required at all (the ordinary case) -- unchanged behavior.
    import json as _json
    fake = _FakeProvider(auto_err="", review_gate=(False, None))
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(prefer_auto_merge=True),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha="")]
    assert fake.calls == []
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["action"] == "auto-merge"


def test_unknown_bypassability_still_arms_auto_merge(monkeypatch):
    # A required review whose bypassability couldn't be determined (rulesets
    # unreadable, classic protection with no per-actor bypass signal, etc.)
    # must never be treated as an affirmative "yes" -- ordinary auto-merge.
    fake = _FakeProvider(auto_err="", review_gate=(True, None))
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(prefer_auto_merge=True),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha="")]
    assert fake.calls == []


def test_bypassable_review_gate_not_consulted_when_prefer_auto_off(monkeypatch):
    # The whole short-circuit only matters when auto-merge would otherwise be
    # tried -- with prefer_auto_merge off, the existing direct-merge path is
    # unaffected and pull_review_gate is never even called.
    fake = _FakeProvider(auto_err="", review_gate=(True, True))
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(prefer_auto_merge=False),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.review_gate_calls == []
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True, delete_source_branch=True, expected_head_sha="")]


def test_ado_self_merge_uses_native_completion(monkeypatch, capsys):
    import json as _json

    fake = _FakeProvider(auto_err="")
    fake.name = "azure-devops"
    _patch_provider(monkeypatch, fake)
    prcfg = _prcfg(
        provider="azure-devops",
        api_base="https://example.visualstudio.com",
        automerge_label="",
        prefer_auto_merge=True,
        delete_source_branch=False,
        bypass_policy=True,
        bypass_reason="Self-complete owned repo.",
    )
    flow = pc.classify_pr_flow(
        enabled=True,
        required=True,
        provider="azure-devops",
        automerge_label="",
        merge_actor="submitter-direct",
    )

    rc = m._pr_merge_now(
        _args(repo="Project/repo", json=True),
        prcfg,
        flow,
        apply=True,
    )

    assert rc == 0
    assert fake.complete_calls == [{
        "repo": "Project/repo",
        "number": 7,
        "api_base": "https://example.visualstudio.com",
        "token": "tok",
        "automerge_label": "",
        "squash": True,
        "delete_source_branch": False,
        "bypass_policy": True,
        "bypass_reason": "Self-complete owned repo.",
    }]
    assert fake.auto_calls == []
    assert fake.calls == []
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["action"] == "auto-complete"
    assert out["applied"] is True


def test_ado_self_merge_surfaces_completion_failure(monkeypatch):
    fake = _FakeProvider(auto_err="ADO completion failed")
    fake.name = "azure-devops"
    _patch_provider(monkeypatch, fake)
    flow = pc.classify_pr_flow(
        enabled=True,
        required=True,
        provider="azure-devops",
        automerge_label="",
        merge_actor="submitter-direct",
    )

    rc = m._pr_merge_now(
        _args(repo="Project/repo"),
        _prcfg(provider="azure-devops"),
        flow,
        apply=True,
    )

    assert rc == 1
    assert fake.complete_calls
    assert fake.auto_calls == []
    assert fake.calls == []
