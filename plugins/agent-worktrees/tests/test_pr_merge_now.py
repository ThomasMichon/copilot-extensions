"""Tests for `pr-merge --now` -- the submitter-direct merge dispatch.

Covers ``_pr_merge_now``: it merges only on a ``pr-self-merge`` repo, uses an
admin bypass only for non-blocking review posture, refuses-with-reminder on
every other flow profile, and previews without merging under ``--dry-run``.
The provider seam is mocked so no ``gh`` is invoked.
"""

from __future__ import annotations

from types import SimpleNamespace

import agent_worktrees.__main__ as m
from agent_worktrees import pr_contract as pc
from agent_worktrees import providers as prov


def _args(**over):
    base = dict(repo="o/r", pr=7, sweep=False, host="", token=None,
                json=False, now=True)
    base.update(over)
    return SimpleNamespace(**base)


def _prcfg(**over):
    base = dict(provider="github", api_base="", prefer_auto_merge=False)
    base.update(over)
    return SimpleNamespace(**base)


def _self_merge_flow():
    return pc.classify_pr_flow(enabled=True, required=True, provider="github",
                               automerge_label="", self_approve=True,
                               reviewer="copilot", review_blocking=False)


class _FakeProvider:
    name = "github"

    def __init__(self, err="", auto_err="unsupported"):
        self._err = err
        self._auto_err = auto_err
        self.calls = []
        self.auto_calls = []

    def merge_pull(self, repo, number, *, squash=True, admin=False,
                   api_base="", token=None):
        self.calls.append(dict(repo=repo, number=number, squash=squash,
                               admin=admin))
        return self._err

    def enable_auto_merge(self, repo, number, *, squash=True,
                          api_base="", token=None):
        self.auto_calls.append(dict(repo=repo, number=number, squash=squash))
        return self._auto_err


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(prov, "get_provider", lambda name: provider)
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "tok")


def test_now_self_merge_calls_merge_pull_squash_admin(monkeypatch, capsys):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True)]


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
        dict(repo="o/r", number=7, squash=True, admin=False)
    ]


def test_now_refused_on_non_self_merge(monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    human = pc.classify_pr_flow(enabled=True, required=True, provider="github",
                                automerge_label="", reviewer="agent:reviewer")
    rc = m._pr_merge_now(_args(), _prcfg(), human, apply=True)
    assert rc == 2
    assert fake.calls == []  # never merged


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
    assert fake.auto_calls == [dict(repo="o/r", number=7, squash=True)]
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
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True)]


def test_prefer_auto_merge_off_merges_directly(monkeypatch):
    # prefer_auto_merge=False -> straight to the immediate merge, no auto attempt.
    fake = _FakeProvider(auto_err="")
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(prefer_auto_merge=False),
                         _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.auto_calls == []
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True)]


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
