"""Tests for `pr-merge --now` -- the direct submitter self-merge dispatch.

Covers ``_pr_merge_now``: it merges only on a ``pr-self-merge`` repo (squash +
admin, via the provider's ``merge_pull``), refuses-with-reminder on every other
flow profile, and previews without merging under ``--dry-run``. The provider
seam is mocked so no ``gh`` is invoked.
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
    base = dict(provider="github", api_base="")
    base.update(over)
    return SimpleNamespace(**base)


def _self_merge_flow():
    return pc.classify_pr_flow(enabled=True, required=True, provider="github",
                               automerge_label="", self_approve=True,
                               reviewer="copilot", review_blocking=False)


class _FakeProvider:
    name = "github"

    def __init__(self, err=""):
        self._err = err
        self.calls = []

    def merge_pull(self, repo, number, *, squash=True, admin=False,
                   api_base="", token=None):
        self.calls.append(dict(repo=repo, number=number, squash=squash,
                               admin=admin))
        return self._err


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(prov, "get_provider", lambda name: provider)
    monkeypatch.setattr(prov, "account_token_for_slug", lambda slug, prcfg: "tok")


def test_now_self_merge_calls_merge_pull_squash_admin(monkeypatch, capsys):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    rc = m._pr_merge_now(_args(), _prcfg(), _self_merge_flow(), apply=True)
    assert rc == 0
    assert fake.calls == [dict(repo="o/r", number=7, squash=True, admin=True)]


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
