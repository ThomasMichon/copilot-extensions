"""Tests for `pr-merge --now` -- the submitter-direct merge dispatch.

Covers ``_pr_merge_now``: it merges only on a ``pr-self-merge`` repo, limits
admin fallback to a positively identified review exception, refuses unsafe
or unreadable mutation evidence, and previews without merging under ``--dry-run``.
The provider seam is mocked so no ``gh`` is invoked.
"""

from __future__ import annotations

from types import SimpleNamespace

import agent_worktrees.__main__ as m
import pytest
from agent_worktrees import config as cfg
from agent_worktrees import pr_config
from agent_worktrees import pr_contract as pc
from agent_worktrees import providers as prov

HEAD = "a" * 40
PUSHED = "b" * 40
MOVED = "c" * 40


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

    def __init__(self, err="", auto_err="unsupported", review_gate=None, live_head=HEAD,
                 merge_pending=False, confirmation_error=False):
        self._live_head = live_head
        self._err = err
        self._auto_err = auto_err
        self._review_gate = review_gate
        self.calls = []
        self.auto_calls = []
        self.complete_calls = []
        self.review_gate_calls = []
        self._merge_pending = merge_pending
        self._confirmation_error = confirmation_error

    def merge_pull(self, repo, number, *, squash=True, admin=False,
                   api_base="", token=None, delete_source_branch=True,
                   expected_head_sha="", hold_labels=(), wip_title_prefixes=()):
        self.calls.append(dict(repo=repo, number=number, squash=squash,
                               admin=admin,
                               delete_source_branch=delete_source_branch,
                               expected_head_sha=expected_head_sha))
        if not self._err and expected_head_sha:
            self._live_head = expected_head_sha
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
        if self.calls and self._confirmation_error:
            raise prov.ProviderError("completion unavailable")
        return prov.PullResult(
            number=number, head_sha=self._live_head,
            merged=bool(self.calls) and not self._merge_pending,
        )

    def get_snapshot(
        self, repo, number, *, api_base="", token=None, strict_safety=False,
    ):
        head = self._live_head if len(self._live_head) == 40 else HEAD
        return pc.PRSnapshot(
            pr_state="open", head_sha=head, mergeable=True,
            reviews=(pc.Review(id=1, state="COMMENTED", user="copilot",
                               commit_id=head),),
        )

    def pull_review_exception(self, repo, number, *, expected_head_sha,
                              hold_labels=(), wip_title_prefixes=(),
                              api_base="", token=None):
        self.review_gate_calls.append(dict(repo=repo, number=number))
        required, bypassable = (
            self._review_gate if self._review_gate is not None else (False, None)
        )
        return bool(required and bypassable), False, ""

    def pull_review_gate(self, repo, number, *, api_base="", token=None):
        self.review_gate_calls.append(dict(repo=repo, number=number))
        return self._review_gate if self._review_gate is not None else (False, None)


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(prov, "get_provider", lambda name: provider)
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "tok")
    monkeypatch.setattr(prov, "actor_viewer_permission", lambda *args, **kwargs: "maintain")


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


def test_now_self_merge_calls_normal_head_pinned_merge(monkeypatch, capsys):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=False, delete_source_branch=True, expected_head_sha=HEAD)]


def test_normal_merge_queue_admission_is_not_reported_as_merged(monkeypatch, capsys):
    import json

    fake = _FakeProvider(merge_pending=True)
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(_args(json=True), _prcfg(), _self_merge_flow(), apply=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "merge-pending"
    assert result["applied"] is True and result["merged"] is False
    assert result["completion_verified"] is False
    assert result["reminder"]["verb"] == "pr-watch"
    assert len(fake.calls) == 1


def test_normal_merge_unreadable_completion_never_claims_landed(monkeypatch, capsys):
    import json

    fake = _FakeProvider(confirmation_error=True)
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(_args(json=True), _prcfg(), _self_merge_flow(), apply=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True and result["merged"] is False
    assert result["completion_verified"] is False
    assert "completion lookup failed (ProviderError)" in result["error"]


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("failure_type", [
    prov.ProviderError, OSError, ValueError, TypeError, AttributeError,
])
def test_completion_lookup_diagnostics_do_not_expose_exception_values(
    monkeypatch, capsys, json_output, failure_type,
):
    fake = _FakeProvider()

    def unavailable(*args, **kwargs):
        if not fake.calls:
            return prov.PullResult(number=7, head_sha=HEAD)
        raise failure_type("fixture-private-completion-secret\nadditional private details")

    fake.get_pull = unavailable
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(
        _args(json=json_output), _prcfg(), _self_merge_flow(), apply=True,
    ) == 1
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert failure_type.__name__ in text
    assert "fixture-private-completion-secret" not in text
    assert "additional private details" not in text
    assert "completion lookup failed" in text


def test_malformed_provider_number_after_accepted_request_is_redacted(monkeypatch, capsys):
    import json
    import subprocess
    from agent_worktrees.providers import github

    fake = _FakeProvider()
    fake.get_pull = github.GitHubProvider().get_pull
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        github, "run_cli",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, json.dumps({
                "number": None if fake.calls else 7,
                "state": "MERGED" if fake.calls else "OPEN",
                "headRefOid": HEAD,
            }), "",
        ),
    )
    assert m._pr_merge_now(
        _args(json=True), _prcfg(), _self_merge_flow(), apply=True,
    ) == 1
    result = json.loads(capsys.readouterr().out)
    assert len(fake.calls) == 1 and result["applied"] is True
    assert result["merged"] is False and result["completion_verified"] is False
    assert result["error"].endswith("(TypeError)")


@pytest.mark.parametrize("expected_head", ["", "a" * 40])
@pytest.mark.parametrize("confirmation", [
    None, SimpleNamespace(head_sha="a" * 40),
    prov.PullResult(number=7, merged=None),
    prov.PullResult(number=7, merged=1),
])
def test_invalid_completion_shape_is_explicit_not_a_post_merge_crash(
    monkeypatch, capsys, expected_head, confirmation,
):
    import json
    from agent_worktrees import pr_cli

    fake = _FakeProvider(live_head=expected_head)
    fake.get_pull = lambda *args, **kwargs: (
        confirmation if fake.calls else prov.PullResult(number=7, head_sha=expected_head or HEAD)
    )
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(pr_cli, "_tracked_pr_pushed_head", lambda *args: expected_head)
    assert m._pr_merge_now(
        _args(json=True), _prcfg(), _self_merge_flow(), apply=True,
        config=object() if expected_head else None,
    ) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True and result["action"] == "merge-pending"
    assert result["merged"] is False and result["completion_verified"] is False
    assert "invalid lifecycle data" in result["error"]


@pytest.mark.parametrize("recorded_head", [False, True])
def test_normal_merge_completion_with_wrong_head_is_not_success(monkeypatch, capsys, recorded_head):
    import json
    from agent_worktrees import pr_cli

    fake = _FakeProvider(live_head="a" * 40)
    read = fake.get_pull

    def changed(repo, number, **kwargs):
        if fake.calls:
            return prov.PullResult(number=number, head_sha="b" * 40, merged=True)
        return read(repo, number, **kwargs)

    fake.get_pull = changed
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(pr_cli, "_tracked_pr_pushed_head", lambda *args: "a" * 40)
    assert m._pr_merge_now(
        _args(json=True), _prcfg(), _self_merge_flow(), apply=True,
        config=object() if recorded_head else None,
    ) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["merged"] is False and result["completion_verified"] is False
    assert "different or unavailable head" in result["error"]


@pytest.mark.parametrize("head", [None, "", "short", "z" * 40])
def test_untracked_merge_refuses_unavailable_discovery_before_mutation(monkeypatch, capsys, head):
    import json

    fake = _FakeProvider(live_head=head)
    fake.get_snapshot = lambda *args, **kwargs: pc.PRSnapshot(
        pr_state="open", head_sha=head, mergeable=True,
    )
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(_args(json=True), _prcfg(), _self_merge_flow(), apply=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert fake.calls == []
    assert result["applied"] is False and result["merged"] is False
    assert "merge head is unavailable or invalid" in result["error"]


def test_untracked_merge_head_lookup_failure_is_redacted_before_mutation(monkeypatch, capsys):
    import json

    fake = _FakeProvider()
    fake.get_pull = lambda *args, **kwargs: (_ for _ in ()).throw(
        prov.ProviderError("fixture-private-head-discovery-secret")
    )
    fake.get_snapshot = lambda *args, **kwargs: (_ for _ in ()).throw(
        prov.ProviderError("fixture-private-head-discovery-secret")
    )
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(_args(json=True), _prcfg(), _self_merge_flow(), apply=True) == 1
    result = json.loads(capsys.readouterr().out)
    assert fake.calls == []
    assert result["applied"] is False and result["completion_verified"] is False
    assert "safety evidence is unreadable (ProviderError)" in result["error"]
    assert "fixture-private" not in result["error"]


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
        dict(repo="o/r", number=7, squash=True, admin=False, delete_source_branch=True, expected_head_sha=HEAD)
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
    monkeypatch.setattr(
        prov, "actor_viewer_permission", lambda *args, **kwargs: "read",
    )
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


@pytest.mark.parametrize("prefer_auto", [False, True])
def test_now_refuses_when_permission_read_unsupported(monkeypatch, prefer_auto):
    fake = _FakeProvider()
    fake.get_repo_policy = lambda repo, **kw: SimpleNamespace(supported=False)
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        prov, "actor_viewer_permission", lambda *args, **kwargs: None,
    )
    rc = m._pr_merge_now(
        _args(), _prcfg(prefer_auto_merge=prefer_auto),
        _self_merge_flow(), apply=True,
    )
    assert rc == 1
    assert fake.auto_calls == []
    assert fake.calls == []


@pytest.mark.parametrize("permission", [None, ""])
@pytest.mark.parametrize("prefer_auto", [False, True])
def test_unknown_permission_values_never_reach_merge(
    monkeypatch, permission, prefer_auto,
):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        prov, "actor_viewer_permission", lambda *args, **kwargs: permission,
    )
    assert m._pr_merge_now(
        _args(), _prcfg(prefer_auto_merge=prefer_auto),
        _self_merge_flow(), apply=True, viewer_permission=None,
    ) == 1
    assert fake.auto_calls == []
    assert fake.calls == []


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

    fake = _FakeProvider(live_head=PUSHED)
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        pr_cli, "_tracked_pr_pushed_head",
        lambda config, repo, number, provider: PUSHED,
    )
    rc = m._pr_merge_now(
        _args(), _prcfg(), _self_merge_flow(), apply=True, config=object(),
    )
    assert rc == 0
    assert fake.calls == [
        dict(repo="o/r", number=7, squash=True, admin=False,
             delete_source_branch=True, expected_head_sha=PUSHED)
    ]


def test_now_passes_tracked_pushed_head_to_auto_merge_default_path(monkeypatch):
    """The SAME ``--match-head-commit`` protection must reach the native
    AUTO-MERGE path too -- ``prefer_auto_merge=True`` is this repo's
    default, and auto-merge can complete immediately rather than only arm,
    so a regression that stopped threading ``expected_head_sha`` through
    THIS branch specifically would leave the stated protection unverified
    on the path most callers actually take (ThomasMichon/copilot-extensions#4949)."""
    from agent_worktrees import pr_cli

    fake = _FakeProvider(auto_err="", live_head=PUSHED)
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        pr_cli, "_tracked_pr_pushed_head",
        lambda config, repo, number, provider: PUSHED,
    )
    rc = m._pr_merge_now(
        _args(), _prcfg(prefer_auto_merge=True), _self_merge_flow(),
        apply=True, config=object(),
    )
    assert rc == 0
    assert fake.auto_calls == [
        dict(repo="o/r", number=7, squash=True,
             delete_source_branch=True, expected_head_sha=PUSHED)
    ]
    assert fake.calls == []  # no immediate direct merge


@pytest.mark.parametrize(("adopted", "expected"), [(MOVED, MOVED), ("", PUSHED)])
def test_now_adopts_a_moved_head_only_when_it_is_the_worktrees_own(monkeypatch, adopted, expected):
    """The provider's head moved past the recorded one (a plain `git push`):
    `pr-merge` merges it only when the tracking worktree's own HEAD is that
    head, else the stale expectation stands and the provider refuses."""
    from agent_worktrees import pr_cli

    fake = _FakeProvider(live_head=MOVED)
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(pr_cli, "_tracked_pr_pushed_head",
                        lambda config, repo, number, provider: PUSHED)
    asked = []
    monkeypatch.setattr(pr_cli, "_adopt_pushed_head",
                        lambda config, repo, number, provider, live: asked.append(live) or adopted)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True, config=object())
    assert asked == [MOVED]
    if adopted:
        assert rc == 0 and fake.calls[0]["expected_head_sha"] == expected
    else:
        assert rc == 1 and fake.calls == []


@pytest.mark.parametrize("failure_type", [ValueError, TypeError])
def test_now_attempts_recorded_head_but_cannot_confirm_unreadable_completion(
    monkeypatch, capsys, failure_type,
):
    """A malformed initial read still permits a pinned attempt, not a false
    completed-merge receipt when the subsequent read also fails."""
    from agent_worktrees import pr_cli

    fake = _FakeProvider(live_head=PUSHED)
    fake.get_pull = lambda *a, **kw: (_ for _ in ()).throw(failure_type("Malformed metadata"))
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(pr_cli, "_tracked_pr_pushed_head",
                        lambda config, repo, number, provider: PUSHED)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True, config=object())
    assert rc == 1 and fake.calls[0]["expected_head_sha"] == PUSHED
    assert "completion lookup failed" in capsys.readouterr().out


def test_now_discovers_and_pins_head_without_config(monkeypatch):
    """An untracked caller discovers and retains the actual provider head."""
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.calls == [
        dict(repo="o/r", number=7, squash=True, admin=False,
             delete_source_branch=True, expected_head_sha=HEAD)
    ]


@pytest.mark.parametrize("provider_name,verified", [("github", True), ("gitea", None)])
def test_now_json_success_shape(monkeypatch, capsys, provider_name, verified):
    import json as _json
    fake = _FakeProvider()
    fake.name = provider_name
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(provider=provider_name), _self_merge_flow(),
                         apply=True)
    assert rc == 0
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["applied"] is True
    assert out["merged"] is True and out["completion_verified"] is verified
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
        dict(repo="o/r", number=7, squash=True, admin=False, delete_source_branch=True, expected_head_sha=HEAD),
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
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha=HEAD)]
    assert fake.calls == []  # no immediate merge
    out = _json.loads(capsys.readouterr().out.strip())
    assert out["action"] == "auto-merge"
    assert out["merged"] is False
    assert out["completion_verified"] is False
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
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=False, delete_source_branch=True, expected_head_sha=HEAD)]


def test_prefer_auto_merge_off_merges_directly(monkeypatch):
    # prefer_auto_merge=False -> straight to the immediate merge, no auto attempt.
    fake = _FakeProvider(auto_err="")
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(prefer_auto_merge=False),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == []
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=False, delete_source_branch=True, expected_head_sha=HEAD)]


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

@pytest.mark.parametrize("prefer_auto", [False, True])
def test_bypassable_review_gate_skips_auto_merge_and_admin_bypasses(
    monkeypatch, capsys, prefer_auto,
):
    # A live, required review that the acting identity CAN bypass must skip
    # straight to an admin merge -- auto-merge would otherwise arm
    # successfully and then sit blocked on that same review forever.
    import json as _json
    fake = _FakeProvider(auto_err="", review_gate=(True, True))
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(json=True), _prcfg(prefer_auto_merge=prefer_auto),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == []  # never attempted -- would have queued forever
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True, delete_source_branch=True, expected_head_sha=HEAD)]
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
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha=HEAD)]
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
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha=HEAD)]
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
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True, delete_source_branch=True, expected_head_sha=HEAD)]
    assert fake.calls == []


@pytest.mark.parametrize("review_gate", [(False, None), (True, False), (True, None)])
def test_direct_mode_consults_exception_without_inventing_bypass(monkeypatch, review_gate):
    fake = _FakeProvider(auto_err="", review_gate=review_gate)
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(prefer_auto_merge=False),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.review_gate_calls == [dict(repo="o/r", number=7)]
    assert fake.auto_calls == []
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=False, delete_source_branch=True, expected_head_sha=HEAD)]


@pytest.mark.parametrize("blocked", ["negative", "draft", "hold", "wip", "conflict"])
@pytest.mark.parametrize("prefer_auto", [False, True])
def test_mutation_preflight_blocks_every_write(monkeypatch, blocked, prefer_auto):
    fake = _FakeProvider(auto_err="", review_gate=(True, True))
    snap = pc.PRSnapshot(pr_state="open", head_sha=HEAD, mergeable=True)
    prcfg = _prcfg(prefer_auto_merge=prefer_auto)
    if blocked == "negative":
        snap = pc.PRSnapshot(
            pr_state="open", head_sha=HEAD, mergeable=True,
            reviews=(pc.Review(id=1, state="CHANGES_REQUESTED", user="human"),),
        )
    elif blocked == "draft":
        snap = pc.PRSnapshot(pr_state="open", head_sha=HEAD, mergeable=True, draft=True)
    elif blocked == "hold":
        snap = pc.PRSnapshot(
            pr_state="open", head_sha=HEAD, mergeable=True, labels=("hold",),
        )
        prcfg = _prcfg(prefer_auto_merge=prefer_auto, hold_labels=("hold",))
    elif blocked == "wip":
        snap = pc.PRSnapshot(
            pr_state="open", head_sha=HEAD, mergeable=True, title="WIP: unsafe",
        )
        prcfg = _prcfg(prefer_auto_merge=prefer_auto, wip_title_prefixes=("WIP:",))
    elif blocked == "conflict":
        snap = pc.PRSnapshot(pr_state="open", head_sha=HEAD, mergeable=False)
    fake.get_snapshot = lambda *args, **kwargs: snap
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(_args(), prcfg, _self_merge_flow(), apply=True) == 1
    assert fake.auto_calls == []
    assert fake.calls == []
    assert fake.review_gate_calls == []


@pytest.mark.parametrize("prefer_auto", [False, True])
def test_unreadable_mutation_evidence_blocks_every_write(monkeypatch, prefer_auto):
    fake = _FakeProvider(auto_err="", review_gate=(True, True))
    fake.get_snapshot = lambda *args, **kwargs: (_ for _ in ()).throw(
        prov.ProviderError("unreadable")
    )
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(
        _args(), _prcfg(prefer_auto_merge=prefer_auto), _self_merge_flow(), apply=True,
    ) == 1
    assert fake.auto_calls == fake.calls == fake.review_gate_calls == []


@pytest.mark.parametrize("mutation", ["null-label", "missing-fields"])
def test_direct_mode_rejects_malformed_raw_safety_metadata(monkeypatch, mutation):
    import json
    import subprocess

    from agent_worktrees.providers import github

    provider = github.GitHubProvider()
    _patch_provider(monkeypatch, provider)
    calls = []

    def run_cli(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["gh", "api"] and any(
            arg.endswith("/pulls/7") for arg in argv
        ):
            payload = {
                "state": "open", "merged": False, "mergeable": True,
                "head": {"sha": HEAD}, "base": {"ref": "dev"},
                "user": {"login": "author"}, "title": "Ready",
                "draft": False, "labels": [],
            }
            if mutation == "null-label":
                payload["labels"] = [None]
            else:
                payload.pop("title")
                payload.pop("draft")
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        raise AssertionError(f"unexpected mutation after unsafe snapshot: {argv}")

    monkeypatch.setattr(github, "run_cli", run_cli)
    assert m._pr_merge_now(
        _args(), _prcfg(prefer_auto_merge=False),
        _self_merge_flow(), apply=True,
    ) == 1
    assert not any(argv[:3] == ["gh", "pr", "merge"] for argv in calls)


@pytest.mark.parametrize("prefer_auto", [False, True])
def test_new_unsafe_evidence_after_snapshot_blocks_every_write(monkeypatch, prefer_auto):
    fake = _FakeProvider(auto_err="", review_gate=(True, True))
    fake.pull_review_exception = lambda *args, **kwargs: (
        False, True, "hold label present: hold",
    )
    _patch_provider(monkeypatch, fake)
    assert m._pr_merge_now(
        _args(), _prcfg(prefer_auto_merge=prefer_auto), _self_merge_flow(), apply=True,
    ) == 1
    assert fake.auto_calls == []
    assert fake.calls == []


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
    assert out["merged"] is False and out["completion_verified"] is None


def test_ado_native_completion_does_not_require_unsupported_authority_read(
    monkeypatch,
):
    fake = _FakeProvider(auto_err="")
    fake.name = "azure-devops"
    _patch_provider(monkeypatch, fake)
    monkeypatch.setattr(
        prov, "actor_viewer_permission", lambda *args, **kwargs: "",
    )
    flow = pc.classify_pr_flow(
        enabled=True, required=True, provider="azure-devops",
        automerge_label="", merge_actor="submitter-direct",
    )
    assert m._pr_merge_now(
        _args(repo="Project/repo"), _prcfg(provider="azure-devops"),
        flow, apply=True,
    ) == 0
    assert fake.complete_calls


@pytest.mark.parametrize("provider_name", ["github", "gitea", "azure-devops"])
def test_merge_request_failure_has_stable_completion_keys(monkeypatch, capsys, provider_name):
    import json

    fake = _FakeProvider(err="refused", auto_err="refused")
    fake.name = provider_name
    _patch_provider(monkeypatch, fake)
    flow = pc.classify_pr_flow(
        enabled=True, required=True, provider=provider_name,
        automerge_label="", merge_actor="submitter-direct",
    )
    assert m._pr_merge_now(
        _args(json=True), _prcfg(provider=provider_name), flow, apply=True,
    ) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["merged"] is False
    assert result["completion_verified"] is (False if provider_name == "github" else None)
    assert result["error"] == "refused"


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
