"""Tests for the plugin pr-merge module + provider label-apply/list reads.

Covers ``pr_merge`` (single-PR apply/already/skip, base-branch guard, sweep,
loop) and the Gitea provider ``add_label`` / ``list_open_pulls`` (curl seam
mocked).  The pure eligibility classifier is tested in ``test_pr_contract.py``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import pr_contract as pc
from agent_worktrees import pr_merge as pm
from agent_worktrees.providers import ProviderError, gitea

_BINDING = dict(
    automerge_label="auto-merge",
    hold_labels=("do-not-merge", "needs-rebase", "wip"),
    wip_title_prefixes=("wip:", "[wip]", "draft:"),
)


def _prcfg(**over):
    base = dict(provider="gitea", api_base="https://h/gitea",
                token_command="echo tok", **_BINDING)
    base.update(over)
    return cfg.PRConfig(**base)


def _snap(**kw):
    base = dict(pr_state="open", merged=False, head_sha="h", base_ref="master",
                author="alice", mergeable=True, title="A change",
                reviews=(pc.Review(1, "APPROVED", "bob", commit_id="h"),))
    base.update(kw)
    return pc.PRSnapshot(**base)


class _FakeProvider:
    """A provider whose reads are canned and whose add_label is recorded."""

    name = "gitea"

    def __init__(self, snapshots, *, add_error="", open_pulls=()):
        # snapshots: dict {number: PRSnapshot} or a single PRSnapshot for any.
        self._snapshots = snapshots
        self._add_error = add_error
        self._open = tuple(open_pulls)
        self.added: list[tuple] = []

    def get_snapshot(self, repo, number, *, api_base="", token=None):
        if isinstance(self._snapshots, dict):
            return self._snapshots[number]
        return self._snapshots

    def add_label(self, repo, number, label, *, api_base="", token=None):
        self.added.append((repo, number, label))
        return self._add_error

    def request_auto_complete(
        self, repo, number, *, api_base="", token=None, automerge_label="",
        squash=True, delete_source_branch=True, bypass_policy=False,
        bypass_reason="",
    ):
        # The label-apply is the gitea/github implementation of "request
        # auto-complete"; delegate so subclasses overriding add_label still work.
        return self.add_label(repo, number, automerge_label, api_base=api_base,
                              token=token)

    def list_open_pulls(self, repo, *, api_base="", token=None):
        return self._open


# ---------------------------------------------------------------------------
# merge_one
# ---------------------------------------------------------------------------

class TestMergeOne:
    def test_eligible_applies(self):
        prov = _FakeProvider(_snap())
        row = pm.merge_one(_prcfg(), "o/r", 7, token="t", apply=True,
                           default_branch="master", provider=prov)
        assert row["action"] == "apply"
        assert row["applied"] is True
        assert prov.added == [("o/r", 7, "auto-merge")]

    def test_dry_run_does_not_apply(self):
        prov = _FakeProvider(_snap())
        row = pm.merge_one(_prcfg(), "o/r", 7, token="t", apply=False,
                           default_branch="master", provider=prov)
        assert row["action"] == "apply"
        assert "applied" not in row
        assert prov.added == []

    def test_already_present_skips_apply(self):
        prov = _FakeProvider(_snap(labels=("auto-merge",)))
        row = pm.merge_one(_prcfg(), "o/r", 7, token="t", apply=True,
                           default_branch="master", provider=prov)
        assert row["action"] == "already"
        assert prov.added == []

    def test_hold_label_skips(self):
        prov = _FakeProvider(_snap(labels=("needs-rebase",)))
        row = pm.merge_one(_prcfg(), "o/r", 7, token="t", apply=True,
                           default_branch="master", provider=prov)
        assert row["action"] == "skip"
        assert "hold label" in row["reason"]
        assert prov.added == []

    def test_not_approved_skips(self):
        prov = _FakeProvider(_snap(reviews=()))
        row = pm.merge_one(_prcfg(), "o/r", 7, token="t", apply=True,
                           default_branch="master", provider=prov)
        assert row["action"] == "skip"
        assert row["reason"] == "not yet approved"

    def test_base_branch_mismatch_skips(self):
        prov = _FakeProvider(_snap(base_ref="release-1.x"))
        row = pm.merge_one(_prcfg(), "o/r", 7, token="t", apply=True,
                           default_branch="master", provider=prov)
        assert row["action"] == "skip"
        assert "!=" in row["reason"]
        assert prov.added == []

    def test_apply_error_recorded(self):
        prov = _FakeProvider(_snap(), add_error="label not found in o/r: auto-merge")
        row = pm.merge_one(_prcfg(), "o/r", 7, token="t", apply=True,
                           default_branch="master", provider=prov)
        assert row["action"] == "apply"
        assert row["applied"] is False
        assert "not found" in row["error"]

    def test_no_binding_is_noop_skip(self):
        prov = _FakeProvider(_snap())
        prcfg = _prcfg(automerge_label="")
        row = pm.merge_one(prcfg, "o/r", 7, token="t", apply=True,
                           default_branch="master", provider=prov)
        assert row["action"] == "skip"
        assert "no auto-merge label" in row["reason"]
        assert prov.added == []


# ---------------------------------------------------------------------------
# sweep_once / run_sweep
# ---------------------------------------------------------------------------

class TestSweep:
    def test_sweep_applies_to_eligible_only(self):
        snaps = {
            1: _snap(),                                   # eligible
            2: _snap(labels=("auto-merge",)),             # already
            3: _snap(reviews=()),                         # not approved
            4: _snap(labels=("do-not-merge",)),           # hold
        }
        prov = _FakeProvider(snaps, open_pulls=(1, 2, 3, 4))
        summary = pm.sweep_once(_prcfg(), "o/r", token="t", apply=True,
                                default_branch="master", provider=prov)
        assert summary["open"] == 4
        assert summary["eligible"] == 1
        assert summary["applied"] == 1
        assert summary["failed"] == 0
        assert prov.added == [("o/r", 1, "auto-merge")]

    def test_sweep_dry_run(self):
        prov = _FakeProvider({1: _snap()}, open_pulls=(1,))
        summary = pm.sweep_once(_prcfg(), "o/r", token="t", apply=False,
                                default_branch="master", provider=prov)
        assert summary["eligible"] == 1
        assert summary["applied"] == 0
        assert prov.added == []

    def test_sweep_counts_failures(self):
        prov = _FakeProvider({1: _snap()}, add_error="boom", open_pulls=(1,))
        summary = pm.sweep_once(_prcfg(), "o/r", token="t", apply=True,
                                default_branch="master", provider=prov)
        assert summary["applied"] == 0
        assert summary["failed"] == 1

    def test_run_sweep_single_pass_when_not_loop(self):
        prov = _FakeProvider({1: _snap()}, open_pulls=(1,))
        passes = []
        pm.run_sweep(_prcfg(), "o/r", token="t", apply=True, loop=False,
                     default_branch="master", provider=prov,
                     on_pass=passes.append, sleep=lambda s: None)
        assert len(passes) == 1

    def test_run_sweep_loops_until_none_eligible(self):
        # First pass eligible (applies), second pass the label is now present.
        states = {"n": 0}

        class _Prov(_FakeProvider):
            def get_snapshot(self, repo, number, *, api_base="", token=None):
                labels = ("auto-merge",) if states["n"] >= 1 else ()
                return _snap(labels=labels)

            def add_label(self, repo, number, label, *, api_base="", token=None):
                states["n"] += 1
                return ""

        prov = _Prov({}, open_pulls=(1,))
        passes = []
        pm.run_sweep(_prcfg(), "o/r", token="t", apply=True, loop=True,
                     max_passes=5, default_branch="master", provider=prov,
                     on_pass=passes.append, sleep=lambda s: None)
        # pass 1 applies (eligible=1), pass 2 sees 'already' (eligible=0) -> stop.
        assert len(passes) == 2
        assert passes[-1]["eligible"] == 0


# ---------------------------------------------------------------------------
# GiteaProvider.add_label / list_open_pulls (curl seam mocked)
# ---------------------------------------------------------------------------

def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class TestGiteaLabelAndList:
    def test_add_label_resolves_and_verifies(self, monkeypatch):
        # GET labels -> id map; POST labels -> ok; GET issue labels -> present.
        def fake(args, **kw):
            url = next((a for a in args if "http" in a or "/repos/" in a), "")
            method = args[args.index("-X") + 1] if "-X" in args else "GET"
            if "/labels" in url and "/issues/" not in url and method == "GET":
                # Repo label list is paginated: serve page 1, then an empty page
                # so _all_labels terminates (it stops only on an empty batch).
                page1 = "page=1" in url or "page=" not in url
                body = [{"name": "auto-merge", "id": 42}] if page1 else []
                return _proc(stdout=json.dumps(body) + "\n200")
            if "/issues/" in url and method == "POST":
                return _proc(stdout="[]\n201")
            if "/issues/" in url and method == "GET":
                return _proc(stdout=json.dumps([{"name": "auto-merge"}]) + "\n200")
            return _proc(stdout="[]\n200")

        monkeypatch.setattr(gitea, "run_cli", fake)
        monkeypatch.setattr(gitea, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
        err = gitea.GiteaProvider().add_label("o/r", 7, "auto-merge",
                                              api_base="h", token="t")
        assert err == ""

    def test_add_label_missing_label(self, monkeypatch):
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout=json.dumps([]) + "\n200"))
        err = gitea.GiteaProvider().add_label("o/r", 7, "auto-merge",
                                              api_base="h", token="t")
        assert "not found" in err

    def test_add_label_needs_token(self):
        err = gitea.GiteaProvider().add_label("o/r", 7, "auto-merge",
                                              api_base="h", token=None)
        assert "needs a token" in err

    def test_list_open_pulls_paginates(self, monkeypatch):
        page1 = [{"number": i} for i in range(1, 51)]
        page2 = [{"number": 51}]
        state = {"page": 0}

        def fake(args, **kw):
            pages = [page1, page2, []]
            body = pages[state["page"]] if state["page"] < len(pages) else []
            state["page"] += 1
            return _proc(stdout=json.dumps(body) + "\n200")

        monkeypatch.setattr(gitea, "run_cli", fake)
        nums = gitea.GiteaProvider().list_open_pulls("o/r", api_base="h", token="t")
        assert nums == tuple(range(1, 52))

    def test_list_open_pulls_needs_token(self):
        with pytest.raises(ProviderError, match="needs a token"):
            gitea.GiteaProvider().list_open_pulls("o/r", api_base="h", token=None)

    def test_list_open_pulls_http_error(self, monkeypatch):
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout="err\n500"))
        with pytest.raises(ProviderError) as ei:
            gitea.GiteaProvider().list_open_pulls("o/r", api_base="h", token="t")
        assert ei.value.transient is True


class TestUnsupportedProviders:
    def test_github_add_label_unsupported(self):
        from agent_worktrees.providers import get_provider
        err = get_provider("github").add_label("o/r", 1, "x", token="t")
        assert "not supported" in err

    def test_ado_list_open_pulls_supported(self, monkeypatch):
        # ADO now supports the sweep list via `az repos pr list`.
        import subprocess

        from agent_worktrees.providers import azure_devops as azure
        monkeypatch.setattr(
            azure, "run_cli",
            lambda args, **kw: subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout='[{"pullRequestId": 5}, {"pullRequestId": 8}]', stderr=""))
        nums = azure.AzureDevOpsProvider().list_open_pulls(
            "proj/repo", api_base="https://dev.azure.com/org", token="t")
        assert nums == (5, 8)
