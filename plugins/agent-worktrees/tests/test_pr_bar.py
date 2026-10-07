"""``pr bar``: the merge bar's clauses, full pagination, and fail-closed reads."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_worktrees import pr_bar

HEAD, OLD = "a" * 40, "b" * 40
COPILOT = pr_bar.COPILOT_REVIEWER
APPROVED_BODY = ("<!-- ccr-overview-v2 -->\n\n### \U0001f7e2 Approved\n\nLooks right.\n\n"
                 "**0 open findings**\n\n<details>\n<summary><strong>1 resolved since last review"
                 "</strong></summary>\n</details>")
CHANGES_BODY = ("### \U0001f7e1 Changes recommended\n\n<details open>\n<summary><strong>2 open findings"
                "</strong></summary>\n</details>\n\n<details>\n<summary><strong>Previously missed (1)"
                "</strong></summary>\n</details>")


def _review(author=COPILOT, state="COMMENTED", commit=HEAD, body=APPROVED_BODY, at="2026-10-06T10:00:00Z"):
    return {"author": {"login": author}, "state": state, "commit": {"oid": commit},
            "body": body, "submittedAt": at}


def _thread(resolved=True, author=COPILOT, path="src/x.py"):
    return {"isResolved": resolved, "isOutdated": False, "path": path,
            "comments": {"nodes": [{"author": {"login": author}}]}}


def _check(name="tests", status="COMPLETED", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


class FakeGh:
    """Serves ``gh api graphql`` pages: each connection's nodes in pages of
    ``page``; ``fail`` names a connection whose second page errors; ``heads``
    is the head each successive PR-core read reports."""

    def __init__(self, *, checks=None, reviews=None, threads=None, page=100, fail="",
                 heads=(HEAD, HEAD), state="OPEN", mergeable="MERGEABLE", stuck="", raw=None,
                 draft=False, title="Add a thing", labels=()):
        self.data = {"checks": checks if checks is not None else [_check()],
                     "reviews": reviews if reviews is not None else [_review()],
                     "threads": threads if threads is not None else []}
        self.page, self.fail, self.stuck = page, fail, stuck
        self.heads, self.state, self.mergeable = list(heads), state, mergeable
        self.draft, self.title, self.labels = draft, title, labels
        self.calls = []
        self.raw = raw or {}  # kind -> the literal response body for that list

    def __call__(self, args, *, env=None):
        query = next(a[len("query="):] for a in args if a.startswith("query="))
        after = next((a[len("after="):] for a in args if a.startswith("after=")), "")
        self.calls.append((query.split("{")[2][:20], after))
        if "statusCheckRollup" in query:
            kind = "checks"
        elif "reviewThreads" in query:
            kind = "threads"
        elif "reviews(" in query:
            kind = "reviews"
        else:
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return self._ok({"state": self.state, "mergeable": self.mergeable,
                             "headRefOid": head, "author": {"login": "author"},
                             "isDraft": self.draft, "title": self.title,
                             "labels": {"nodes": [{"name": n} for n in self.labels]}})
        if kind in self.raw:
            return subprocess.CompletedProcess(args, 0, self.raw[kind], "")
        start = int(after or 0)
        if kind == self.fail and start:
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502: Bad Gateway")
        nodes = self.data[kind][start:start + self.page]
        more = start + self.page < len(self.data[kind])
        cursor = after if kind == self.stuck and after else str(start + self.page)
        conn = {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}
        if kind == "checks":
            return self._ok({"commits": {"nodes": [{"commit": {"oid": HEAD, "statusCheckRollup": {
                "contexts": conn}}}]}})
        return self._ok({"reviewThreads" if kind == "threads" else "reviews": conn})

    @staticmethod
    def _ok(pr):
        out = json.dumps({"data": {"repository": {"pullRequest": pr}}})
        return subprocess.CompletedProcess([], 0, out, "")


NO_APPROVAL = {"approval_required": False, "hold_labels": ("do-not-merge",),
               "wip_title_prefixes": ("wip:",), "review_blocking": True}


def _bar(fake, **kw):
    snap = pr_bar.read_github("owner/repo", 7, host="github.com", token="t", run=fake)
    kw.setdefault("policy", NO_APPROVAL)
    return pr_bar.evaluate(snap, now="2026-10-06T12:00:00+00:00", **kw)


def _status(bar):
    return {c.id: c.status for c in bar.clauses}


def test_every_clause_met_on_one_head_is_met():
    bar = _bar(FakeGh())
    assert bar.verdict == "met" and set(_status(bar).values()) == {"met"}
    assert bar.head == HEAD


def test_every_page_of_every_list_is_read():
    """Past the first 100: the 150th thread is the unresolved one, and the newest
    Copilot review (on the head) is on the second page of reviews."""
    threads = [_thread() for _ in range(149)] + [_thread(resolved=False, path="late.py")]
    reviews = [_review(commit=OLD, body=CHANGES_BODY, at=f"2026-10-05T{i // 60:02}:{i % 60:02}:00Z")
               for i in range(120)] + [_review(at="2026-10-06T10:00:00Z")]
    fake = FakeGh(threads=threads, reviews=reviews)
    bar = _bar(fake)
    clauses = {c.id: c for c in bar.clauses}
    assert clauses["threads_unresolved_zero"].status == "failed"
    assert "late.py" in clauses["threads_unresolved_zero"].evidence
    assert clauses["review_on_head"].status == "met"
    assert [after for q, after in fake.calls if after] == ["100", "100"]


def test_a_failed_page_is_unknown_never_met():
    """A 502 on the second page of threads: the clauses built on threads are unknown,
    and so is the verdict -- an empty or partial list is not a passing one."""
    bar = _bar(FakeGh(threads=[_thread() for _ in range(150)], fail="threads"))
    status = _status(bar)
    assert status["threads_unresolved_zero"] == "unknown"
    assert status["review_findings_zero"] == "unknown"
    assert bar.verdict == "unknown"
    assert "502" in next(c.error for c in bar.clauses if c.id == "threads_unresolved_zero")


def test_a_cursor_that_does_not_advance_is_unknown():
    bar = _bar(FakeGh(threads=[_thread() for _ in range(250)], stuck="threads"))
    assert _status(bar)["threads_unresolved_zero"] == "unknown"


def test_a_head_that_moves_during_the_read_makes_every_clause_unknown():
    bar = _bar(FakeGh(heads=(HEAD, OLD)))
    assert set(_status(bar).values()) == {"unknown"} and bar.verdict == "unknown"
    assert "moved" in bar.clauses[0].evidence


def test_no_checks_is_unknown_not_green():
    assert _status(_bar(FakeGh(checks=[])))["ci_green"] == "unknown"


@pytest.mark.parametrize("check, expected", [
    (_check(conclusion="FAILURE"), "failed"),
    (_check(conclusion="CANCELLED"), "failed"),
    (_check(status="IN_PROGRESS", conclusion=""), "pending"),
    ({"__typename": "StatusContext", "context": "legacy", "state": "SUCCESS"}, "met"),
    ({"__typename": "StatusContext", "context": "legacy", "state": "ERROR"}, "failed"),
    (_check(conclusion="SKIPPED"), "met"),
])
def test_ci_green_folds_check_runs_and_statuses(check, expected):
    assert _status(_bar(FakeGh(checks=[_check(), check])))["ci_green"] == expected


def test_a_review_of_an_older_head_is_pending():
    status = _status(_bar(FakeGh(reviews=[_review(commit=OLD)])))
    assert status["review_on_head"] == "pending"
    assert status["review_findings_zero"] == "pending"


def test_open_and_previously_missed_findings_fail():
    bar = _bar(FakeGh(reviews=[_review(body=CHANGES_BODY)],
                      threads=[_thread(resolved=False), _thread(resolved=False)]))
    clause = next(c for c in bar.clauses if c.id == "review_findings_zero")
    assert clause.status == "failed" and clause.evidence.startswith("2 open, 1 previously missed")
    assert bar.verdict == "failed"


def test_previously_missed_alone_fails():
    body = APPROVED_BODY + "<details><summary>Previously missed (2)</summary></details>"
    assert _status(_bar(FakeGh(reviews=[_review(body=body)])))["review_findings_zero"] == "failed"


def test_a_summary_that_disagrees_with_the_threads_is_unknown():
    """The summary reports open findings, yet no thread by the reviewer is unresolved."""
    status = _status(_bar(FakeGh(reviews=[_review(body=CHANGES_BODY)], threads=[_thread()])))
    assert status["review_findings_zero"] == "unknown"


def test_a_summary_reporting_none_open_with_an_unresolved_reviewer_thread_is_unknown():
    """Disagreement either way fails closed: 0 open in the summary, 1 open thread."""
    status = _status(_bar(FakeGh(threads=[_thread(resolved=False)])))
    assert status["review_findings_zero"] == "unknown"


def test_a_closed_unmerged_pr_fails():
    bar = _bar(FakeGh(state="CLOSED"))
    assert _status(bar)["mergeable"] == "failed" and bar.verdict == "failed"


@pytest.mark.parametrize("raw", [
    "[1]",
    json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": None}}}}),
])
def test_an_unexpected_response_shape_is_unknown(raw):
    bar = _bar(FakeGh(raw={"threads": raw}))
    assert _status(bar)["threads_unresolved_zero"] == "unknown" and bar.verdict == "unknown"


def test_a_head_commit_without_a_check_rollup_has_no_checks():
    raw = json.dumps({"data": {"repository": {"pullRequest": {"commits": {"nodes": [
        {"commit": {"oid": HEAD, "statusCheckRollup": None}}]}}}}})
    clause = next(c for c in _bar(FakeGh(raw={"checks": raw})).clauses if c.id == "ci_green")
    assert (clause.status, clause.error) == ("unknown", "")
    assert "no checks reported" in clause.evidence


def test_a_summary_without_a_count_is_unknown():
    assert _status(_bar(FakeGh(reviews=[_review(body="LGTM")])))["review_findings_zero"] == "unknown"


def test_a_human_change_request_stands_until_that_human_approves():
    asked = _review(author="alice", state="CHANGES_REQUESTED", at="2026-10-06T09:00:00Z")
    status = _status(_bar(FakeGh(reviews=[asked, _review()])))
    assert status["human_reviews_answered"] == "failed"
    approved = _review(author="alice", state="APPROVED", at="2026-10-06T11:00:00Z")
    bar = _bar(FakeGh(reviews=[asked, _review(), approved]))
    assert _status(bar)["human_reviews_answered"] == "met" and bar.verdict == "met"


def test_merge_conflicts_fail_and_uncomputed_mergeability_is_pending():
    assert _status(_bar(FakeGh(mergeable="CONFLICTING")))["mergeable"] == "failed"
    assert _status(_bar(FakeGh(mergeable="UNKNOWN")))["mergeable"] == "pending"


def test_a_merged_pr_reports_merged():
    assert _bar(FakeGh(state="MERGED", mergeable="UNKNOWN")).verdict == "merged"


def test_an_unreadable_pr_is_unknown():
    def gh(args, *, env=None):
        return subprocess.CompletedProcess(args, 1, "", "HTTP 401: Bad credentials")

    bar = _bar(gh)
    assert bar.verdict == "unknown" and set(_status(bar).values()) == {"unknown"}


def test_finding_counts_reads_the_review_summary():
    assert pr_bar.finding_counts(APPROVED_BODY) == (0, 0)
    assert pr_bar.finding_counts(CHANGES_BODY) == (2, 1)
    assert pr_bar.finding_counts("1 open finding") == (1, 0)
    assert pr_bar.finding_counts("no summary") is None


def test_no_supplied_policy_never_meets_the_bar():
    bar = _bar(FakeGh(), policy=None)
    assert _status(bar)["merge_policy"] == "unknown" and bar.verdict == "unknown"


def test_a_policy_requiring_approval_is_pending_on_comments_alone():
    """Copilot's reviews are comments: a repo whose policy requires an approval isn't
    clear until one lands on the head."""
    policy = {**NO_APPROVAL, "approval_required": True}
    bar = _bar(FakeGh(), policy=policy)
    assert _status(bar)["merge_policy"] == "pending" and bar.verdict == "pending"
    approved = _review(author="alice", state="APPROVED", at="2026-10-06T11:00:00Z")
    assert _bar(FakeGh(reviews=[_review(), approved]), policy=policy).verdict == "met"
    stale = _review(author="alice", state="APPROVED", commit=OLD, at="2026-10-06T11:00:00Z")
    assert _status(_bar(FakeGh(reviews=[_review(), stale]), policy=policy))["merge_policy"] == "pending"


@pytest.mark.parametrize("fake", [
    FakeGh(labels=("do-not-merge",)), FakeGh(draft=True), FakeGh(title="WIP: not yet"),
])
def test_holds_drafts_and_wip_titles_fail_the_policy(fake):
    bar = _bar(fake)
    assert _status(bar)["merge_policy"] == "failed" and bar.verdict == "failed"


def test_evidence_is_capped():
    clause = pr_bar.Clause("ci_green", "failed", "x" * 5000)
    assert len(clause.evidence) == pr_bar.EVIDENCE_CAP


def test_pr_bar_cli_reports_the_verdict_as_its_exit_code(monkeypatch, capsys):
    """``pr bar <owner/name> <n> --json`` through the ``pr`` namespace: the bar as
    JSON, and the verdict as the exit code."""
    from types import SimpleNamespace

    from agent_worktrees import config as cfg
    from agent_worktrees import pr_cli, providers

    from agent_worktrees import pr_config

    prcfg = SimpleNamespace(provider="github", api_base="")
    monkeypatch.delenv("GH_HOST", raising=False)
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: SimpleNamespace(
        default_repo=SimpleNamespace(pr=SimpleNamespace(provider="azure-devops", api_base=""))))
    registered = {"owner/repo": SimpleNamespace(pr=prcfg)}  # the slug's own binding, not the active one
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda _c, slug: SimpleNamespace(
        resolved=slug in registered, repo_config=registered.get(slug)))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, p: "t")
    seen = {}
    real_read = pr_bar.read_github

    def read(repo, number, *, host, token):
        seen.update(repo=repo, number=number, host=host, token=token)
        return real_read(repo, number, host=host, token=token,
                         run=FakeGh(reviews=[_review(body=CHANGES_BODY)],
                                    threads=[_thread(resolved=False)] * 2))

    monkeypatch.setattr(pr_bar, "read_github", read)
    assert pr_cli.cmd_pr_dispatch(["bar", "owner/repo", "7", "--json"]) == pr_bar.EXIT["failed"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "failed" and payload["head"] == HEAD
    assert {c["id"] for c in payload["clauses"]} == set(pr_bar.CLAUSES)
    assert seen == {"repo": "owner/repo", "number": 7, "host": "github.com", "token": "t"}
    assert pr_cli.cmd_pr_dispatch(["bar", "a", "b", "c"]) == 2
    assert pr_cli.cmd_pr_dispatch(["bar", "someone/else", "7"]) == 2  # unregistered: no borrowed binding
    prcfg.provider = "azure-devops"
    assert pr_cli.cmd_pr_dispatch(["bar", "owner/repo", "7"]) == 2


@pytest.mark.parametrize("conn", [
    {"nodes": [_thread()]},                                        # no pageInfo: maybe truncated
    {"nodes": [_thread()], "pageInfo": {"endCursor": "x"}},        # no hasNextPage
    {"pageInfo": {"hasNextPage": False, "endCursor": ""}},          # no nodes
    {"nodes": "oops", "pageInfo": {"hasNextPage": False}},          # nodes not a list
])
def test_malformed_pagination_is_unknown_never_complete(conn):
    raw = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": conn}}}})
    assert _status(_bar(FakeGh(raw={"threads": raw})))["threads_unresolved_zero"] == "unknown"


def test_a_worktree_target_reads_the_supplied_configs_tracking_dir(monkeypatch, tmp_path):
    """--config naming another project: its tracking dir, never the ambient one."""
    from types import SimpleNamespace

    from agent_worktrees import config as cfg
    from agent_worktrees import pr_bar_cli, tracking, worktree_identity

    seen = []
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda wid, _c: wid)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: seen.append(name) or tmp_path)
    active = SimpleNamespace(number=7, repo="owner/other")
    monkeypatch.setattr(tracking, "load_record", lambda path: SimpleNamespace(
        active_pr=lambda: active, repo="owner/other"))
    slug, number, error = pr_bar_cli._target(["wt-1"], SimpleNamespace(repo_name="other-project"))
    assert (slug, number, error) == ("owner/other", 7, "") and seen == ["other-project"]
