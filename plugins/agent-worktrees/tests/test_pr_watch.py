"""Tests for the plugin pr-watch watch loop + provider snapshot reads.

Covers the network+timing half (``pr_watch.run_wait`` / ``build_fetch`` /
``decorate_events``) and the Gitea provider ``get_snapshot`` (curl seam mocked),
complementing the pure-transition tests in ``test_pr_contract.py``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import pr_contract as pc
from agent_worktrees import pr_watch as prw
from agent_worktrees.providers import ProviderError, base, gitea

# ---------------------------------------------------------------------------
# run_wait -- the poll/timeout/baseline loop
# ---------------------------------------------------------------------------

class _Clock:
    """Deterministic monotonic clock: advances by `step` on each read."""

    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def now(self):
        v = self.t
        self.t += self.step
        return v


def _snap(**kw):
    return pc.PRSnapshot(**kw)


class TestRunWait:
    def _run(self, snaps, *, until=None, baseline=None, timeout=100.0, **kw):
        seq = list(snaps)
        clock = _Clock()
        return prw.run_wait(
            repo="o/r", pr=1, until=until or list(pc.DEFAULT_UNTIL),
            baseline=baseline, fetch=lambda: seq.pop(0),
            timeout=timeout, interval=1.0,
            now=clock.now, sleep=lambda s: None, **kw,
        )

    def test_auto_baseline_terminal_merge_fires(self):
        res = self._run([_snap(pr_state="closed", merged=True)])
        assert res.matched
        assert res.payload["transitions"] == ["merged"]
        assert res.payload["cursor"] == "r0.mc"

    def test_auto_baseline_terminal_closed_fires(self):
        res = self._run([_snap(pr_state="closed", merged=False)])
        assert res.matched
        assert res.payload["transitions"] == ["closed"]

    def test_auto_baseline_open_does_not_fire_on_existing_review(self):
        # A pre-existing approval at arm time must NOT fire under auto-baseline;
        # the second poll (a NEW approval) should.
        snap0 = _snap(reviews=(pc.Review(1, "APPROVED", "bob"),))
        snap1 = _snap(reviews=(pc.Review(1, "APPROVED", "bob"),
                               pc.Review(2, "APPROVED", "carol")))
        res = self._run([snap0, snap1])
        assert res.matched
        assert res.payload["transitions"] == ["approved"]
        assert res.payload["events"][0]["review"]["id"] == 2

    def test_since_cursor_baseline_fires_on_new_review(self):
        base_cur = pc.Baseline.from_cursor("r5")
        snap = _snap(reviews=(pc.Review(6, "REQUEST_CHANGES", "bob"),))
        res = self._run([snap], baseline=base_cur)
        assert res.matched
        assert res.payload["transitions"] == ["changes_requested"]

    def test_mergeable_none_baseline_adopted_without_firing(self):
        # since-cursor baseline starts mergeable unknown; first concrete value is
        # adopted (no fire), then a flip to False fires conflict.
        b = pc.Baseline.from_cursor("r0")
        s_true = _snap(pr_state="open", mergeable=True)
        s_false = _snap(pr_state="open", mergeable=False)
        res = self._run([s_true, s_false], baseline=b)
        assert res.matched
        assert res.payload["transitions"] == ["conflict"]

    def test_timeout_returns_unmatched(self):
        clock = _Clock(step=60.0)
        res = prw.run_wait(
            repo="o/r", pr=1, until=list(pc.DEFAULT_UNTIL), baseline=pc.Baseline(),
            fetch=lambda: _snap(pr_state="open", mergeable=True),
            timeout=1.0, interval=1.0, now=clock.now, sleep=lambda s: None,
        )
        assert res.matched is False
        assert res.payload == {}

    def test_transient_error_retried_then_fires(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ProviderError("blip", transient=True)
            return _snap(pr_state="closed", merged=True)

        errors = []
        clock = _Clock()
        res = prw.run_wait(
            repo="o/r", pr=1, until=list(pc.DEFAULT_UNTIL), baseline=None,
            fetch=fetch, timeout=100.0, interval=1.0,
            now=clock.now, sleep=lambda s: None,
            on_error=errors.append,
        )
        assert res.matched
        assert len(errors) == 1

    def test_permanent_error_propagates(self):
        def fetch():
            raise ProviderError("bad token", transient=False)

        with pytest.raises(ProviderError):
            prw.run_wait(
                repo="o/r", pr=1, until=list(pc.DEFAULT_UNTIL), baseline=None,
                fetch=fetch, timeout=100.0, interval=1.0,
                now=_Clock().now, sleep=lambda s: None,
            )


class TestDecorateEvents:
    def test_payload_shape(self):
        snap = _snap(pr_state="open", merged=False, mergeable=True,
                     head_sha="abc", base_ref="master",
                     reviews=(pc.Review(3, "APPROVED", "bob"),))
        events = [{"event": "approved"}]
        payload = prw.decorate_events(events, "o/r", 7, snap)
        assert payload == {
            "repo": "o/r", "pr": 7, "events": events,
            "transitions": ["approved"], "pr_state": "open", "merged": False,
            "mergeable": True, "head_sha": "abc", "base_ref": "master",
            "cursor": "r3",
        }


# ---------------------------------------------------------------------------
# build_fetch -- config-driven provider/token resolution
# ---------------------------------------------------------------------------

class TestBuildFetch:
    def test_resolves_gitea_provider(self, monkeypatch):
        captured = {}

        def fake_snapshot(repo, number, *, api_base="", token=None):
            captured.update(repo=repo, number=number, api_base=api_base, token=token)
            return pc.PRSnapshot()

        monkeypatch.setattr(gitea.GiteaProvider, "get_snapshot",
                            staticmethod(fake_snapshot))
        prcfg = cfg.PRConfig(provider="gitea", api_base="https://h/gitea",
                             token_command="echo tok")
        fetch = prw.build_fetch(prcfg, "o/r", 5)
        fetch()
        assert captured["repo"] == "o/r"
        assert captured["number"] == 5
        assert captured["api_base"] == "https://h/gitea"
        assert captured["token"] == "tok"

    def test_api_base_and_token_override(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            gitea.GiteaProvider, "get_snapshot",
            staticmethod(lambda repo, number, *, api_base="", token=None:
                         captured.update(api_base=api_base, token=token) or pc.PRSnapshot()),
        )
        prcfg = cfg.PRConfig(provider="gitea", api_base="https://cfg/gitea",
                             token_command="echo cfgtok")
        prw.build_fetch(prcfg, "o/r", 5, api_base="https://override", token="ovtok")()
        assert captured["api_base"] == "https://override"
        assert captured["token"] == "ovtok"

    def test_unsupported_provider_fails_fast(self):
        prcfg = cfg.PRConfig(provider="github")
        fetch = prw.build_fetch(prcfg, "o/r", 5, token="x")
        with pytest.raises(ProviderError, match="does not support snapshot"):
            fetch()


# ---------------------------------------------------------------------------
# GiteaProvider.get_snapshot (curl seam mocked)
# ---------------------------------------------------------------------------

def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _pr_payload(**over):
    base_pr = {
        "number": 9, "state": "open", "merged": False, "mergeable": True,
        "title": "A change", "draft": False,
        "head": {"sha": "deadbeef"}, "base": {"ref": "master"},
        "user": {"login": "cjohnson"},
        "labels": [{"name": "auto-merge"}, {"name": "source:wheatley"}],
    }
    base_pr.update(over)
    return base_pr


class TestGiteaGetSnapshot:
    def _fake_run(self, pr_payload, reviews_pages):
        """Build a run_cli fake dispatching on the request URL.

        ``reviews_pages`` is a list of page bodies (each a list); pages are
        served in order, an empty list ends pagination.
        """
        state = {"page": 0}

        def fake(args, **kw):
            is_reviews = any("/reviews" in a for a in args)
            if is_reviews:
                page = reviews_pages[state["page"]] if state["page"] < len(reviews_pages) else []
                state["page"] += 1
                return _proc(stdout=json.dumps(page) + "\n200")
            return _proc(stdout=json.dumps(pr_payload) + "\n200")

        return fake

    def test_parses_pr_fields_and_labels(self, monkeypatch):
        monkeypatch.setattr(gitea, "run_cli", self._fake_run(_pr_payload(), [[]]))
        snap = gitea.GiteaProvider().get_snapshot(
            "o/r", 9, api_base="https://h/gitea", token="tok")
        assert snap.pr_state == "open"
        assert snap.merged is False
        assert snap.mergeable is True
        assert snap.head_sha == "deadbeef"
        assert snap.base_ref == "master"
        assert snap.author == "cjohnson"
        assert snap.title == "A change"
        assert snap.draft is False
        assert snap.labels == ("auto-merge", "source:wheatley")
        assert snap.reviews == ()

    def test_merged_pr(self, monkeypatch):
        payload = _pr_payload(state="closed", merged=True)
        monkeypatch.setattr(gitea, "run_cli", self._fake_run(payload, [[]]))
        snap = gitea.GiteaProvider().get_snapshot("o/r", 9, api_base="h", token="t")
        assert snap.merged is True
        assert snap.pr_state == "closed"

    def test_mergeable_null_becomes_none(self, monkeypatch):
        payload = _pr_payload(mergeable=None)
        monkeypatch.setattr(gitea, "run_cli", self._fake_run(payload, [[]]))
        snap = gitea.GiteaProvider().get_snapshot("o/r", 9, api_base="h", token="t")
        assert snap.mergeable is None

    def test_reviews_parsed_and_paginated(self, monkeypatch):
        page1 = [
            {"id": i, "state": "COMMENT", "user": {"login": "bot"},
             "submitted_at": "t", "commit_id": "c", "dismissed": False}
            for i in range(1, 51)
        ]
        page2 = [{"id": 51, "state": "APPROVED", "user": {"login": "wheatley"},
                  "submitted_at": "t2", "commit_id": "deadbeef", "dismissed": False}]
        monkeypatch.setattr(gitea, "run_cli",
                            self._fake_run(_pr_payload(), [page1, page2, []]))
        snap = gitea.GiteaProvider().get_snapshot("o/r", 9, api_base="h", token="t")
        assert len(snap.reviews) == 51
        assert snap.reviews[-1].id == 51
        assert snap.reviews[-1].state == "APPROVED"
        assert snap.reviews[-1].user == "wheatley"

    def test_needs_token(self):
        with pytest.raises(ProviderError, match="needs a token"):
            gitea.GiteaProvider().get_snapshot("o/r", 9, api_base="h", token=None)

    def test_http_error_transient_classification(self, monkeypatch):
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout="err\n503"))
        with pytest.raises(ProviderError) as ei:
            gitea.GiteaProvider().get_snapshot("o/r", 9, api_base="h", token="t")
        assert ei.value.transient is True

    def test_http_error_permanent_classification(self, monkeypatch):
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout="nope\n404"))
        with pytest.raises(ProviderError) as ei:
            gitea.GiteaProvider().get_snapshot("o/r", 9, api_base="h", token="t")
        assert ei.value.transient is False


class TestUnsupportedSnapshot:
    def test_base_helper_raises(self):
        with pytest.raises(ProviderError, match="does not support snapshot"):
            base._unsupported_snapshot("github")
