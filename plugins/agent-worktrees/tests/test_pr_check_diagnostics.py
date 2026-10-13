"""Observed check failures remain attributable without implying CI policy."""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import config, pr_contract as pc, pr_ops, pr_watch, providers
from agent_worktrees.pr_checks import checks_payload, print_check_failures
from agent_worktrees.providers import github
from agent_worktrees.providers.github_checks import (
    FAILED_CONCLUSIONS, MAX_FAILURES, MAX_TEXT_CHARS, read_checks,
    _text,
)


def _run(name: str = "optional-reviewer", conclusion: str = "failure") -> dict:
    return {
        "name": name, "status": "completed", "conclusion": conclusion,
        "details_url": "https://example.com/jobs/17", "output": {"summary": None},
    }


class CheckFixture:
    def __init__(self, runs: list | None = None, status: dict | None = None) -> None:
        self.calls: list[list[str]] = []
        self.status = status if status is not None else {"statuses": []}
        self.pages = [{"check_runs": runs or [], "total_count": len(runs or [])}]
        self.unreadable = ""

    def invoke(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        assert "ghe.example.com" in args
        if "/status" in args[-1]:
            key, body = "status", json.dumps(self.status)
        else:
            assert "--paginate" in args
            key, body = "runs", "\n".join(json.dumps(page) for page in self.pages)
        return subprocess.CompletedProcess(
            args, 1 if key == self.unreadable else 0, body, "unavailable",
        )

    def read(self):
        return read_checks("owner/repo", "head", host="ghe.example.com", invoke=self.invoke)


@pytest.mark.parametrize("conclusion", sorted(FAILED_CONCLUSIONS))
@pytest.mark.guard
def test_check_diagnostics_all_failing_conclusions(conclusion: str) -> None:
    fixture = CheckFixture([_run(conclusion=conclusion)])
    report = fixture.read()
    assert report.state == "failure" and report.complete
    assert len(fixture.calls) == 2
    failure = report.failures[0]
    assert (failure.name, failure.state, failure.source) == (
        "optional-reviewer", conclusion, "check-run",
    )
    assert failure.url == "https://example.com/jobs/17"
    assert failure.description == ""


def test_check_diagnostics_legacy_status_and_pending_run() -> None:
    fixture = CheckFixture(
        [{"status": "in_progress"}],
        {"state": "failure", "statuses": [{
            "context": "build", "state": "error", "target_url": "https://example.com/build",
            "description": "Could not acquire runner",
        }]},
    )
    report = fixture.read()
    assert report.state == "failure"
    assert report.failures[0].description == "Could not acquire runner"
    assert report.failures[0].source == "commit-status"


@pytest.mark.parametrize("latest", ["success", "pending"])
@pytest.mark.guard
def test_check_diagnostics_legacy_rerun_supersedes_failed_context(latest: str) -> None:
    report = CheckFixture(status={
        "state": latest, "statuses": [
            {"context": "Build", "state": latest},
            {"context": "build", "state": "failure", "description": "Old attempt"},
        ],
    }).read()
    assert report.state == latest and report.complete
    assert report.failures == ()


def test_check_diagnostics_legacy_rerun_keeps_other_failed_context() -> None:
    report = CheckFixture(status={
        "state": "failure", "statuses": [
            {"context": "build", "state": "success"},
            {"context": "lint", "state": "error", "description": "Latest lint"},
            {"context": "build", "state": "failure"},
            {"context": "lint", "state": "failure", "description": "Old lint"},
        ],
    }).read()
    assert report.state == "failure"
    assert [failure.name for failure in report.failures] == ["lint"]
    assert report.failures[0].description == "Latest lint"


@pytest.mark.guard
@pytest.mark.parametrize("invalid", [None, "", "unknown", 1, {}])
def test_check_diagnostics_invalid_legacy_state_cannot_claim_success(invalid: object) -> None:
    report = CheckFixture([_run("build", "success")], status={
        "state": "success", "statuses": [{"context": "legacy", "state": invalid}],
    }).read()
    assert report.state == "" and not report.complete


def test_check_diagnostics_invalid_legacy_state_retains_other_failure() -> None:
    report = CheckFixture(status={
        "state": "success", "statuses": [
            {"context": "broken", "state": None},
            {"context": "lint", "state": "error"},
        ],
    }).read()
    assert report.state == "failure" and not report.complete
    assert [failure.name for failure in report.failures] == ["lint"]


@pytest.mark.guard
@pytest.mark.parametrize("invalid", [None, "", "unknown"])
def test_check_diagnostics_malformed_latest_supersedes_stale_failure(invalid: object) -> None:
    report = CheckFixture(status={
        "state": "success", "statuses": [
            {"context": "Build", "state": invalid},
            {"context": "build", "state": "failure"},
        ],
    }).read()
    assert report.state == "" and not report.complete
    assert report.failures == ()


def test_check_diagnostics_malformed_latest_retains_other_context_failure() -> None:
    report = CheckFixture(status={
        "state": "failure", "statuses": [
            {"context": "Build", "state": None},
            {"context": "build", "state": "failure"},
            {"context": "lint", "state": "error"},
        ],
    }).read()
    assert report.state == "failure" and not report.complete
    assert [failure.name for failure in report.failures] == ["lint"]


def test_check_diagnostics_legacy_pending_prevents_inconsistent_green_rollup() -> None:
    report = CheckFixture(status={
        "state": "success", "statuses": [{"context": "build", "state": "pending"}],
    }).read()
    assert report.state == "pending" and report.complete


@pytest.mark.guard
@pytest.mark.parametrize("total", [1, -1, None, True, "0"])
@pytest.mark.parametrize("runs", [[], [_run("build", "success")]])
def test_check_diagnostics_empty_status_page_with_invalid_count_is_incomplete(
    total: object, runs: list,
) -> None:
    report = CheckFixture(runs, status={"statuses": [], "total_count": total}).read()
    assert report.state == "" and not report.complete


def test_check_diagnostics_confirmed_empty_status_count_is_complete() -> None:
    report = CheckFixture(status={"statuses": [], "total_count": 0}).read()
    assert report.state == "none" and report.complete


def test_check_diagnostics_incomplete_legacy_count_retains_named_failure() -> None:
    report = CheckFixture(status={
        "state": "failure", "statuses": [{"context": "lint", "state": "error"}],
        "total_count": 2,
    }).read()
    assert report.state == "failure" and not report.complete
    assert report.failures[0].name == "lint"


@pytest.mark.parametrize("invalid", [None, "", "unknown"])
def test_check_diagnostics_invalid_run_status_is_incomplete(invalid: object) -> None:
    run = _run("build", "success")
    run["status"] = invalid
    report = CheckFixture([run]).read()
    assert report.state == "" and not report.complete


def test_check_diagnostics_preserve_later_page_failure() -> None:
    fixture = CheckFixture()
    fixture.pages = [
        {"check_runs": [_run("build", "success")], "total_count": 2},
        {"check_runs": [_run()], "total_count": 2},
    ]
    assert fixture.read().failures[0].name == "optional-reviewer"


def test_check_diagnostics_incomplete_page_retains_observed_failure() -> None:
    fixture = CheckFixture()
    fixture.pages = [{"check_runs": [_run()], "total_count": 2}]
    report = fixture.read()
    assert report.state == "failure" and not report.complete
    assert report.failures[0].name == "optional-reviewer"


@pytest.mark.parametrize("conclusion", ["success", "skipped", "neutral"])
def test_check_diagnostics_incomplete_page_cannot_claim_success(conclusion: str) -> None:
    fixture = CheckFixture()
    fixture.pages = [{"check_runs": [_run(conclusion=conclusion)], "total_count": 2}]
    report = fixture.read()
    assert report.state == "" and not report.complete


@pytest.mark.guard
@pytest.mark.parametrize("conclusion", ["success", "failure"])
@pytest.mark.parametrize("page_count", [1, 2])
def test_check_diagnostics_excess_runs_are_incomplete(
    conclusion: str, page_count: int,
) -> None:
    from agent_worktrees.providers.github_check_pages import (
        check_run_evidence_from_pages, check_runs_from_pages,
    )
    fixture = CheckFixture()
    fixture.pages = [
        {"check_runs": [_run(conclusion=conclusion)], "total_count": page_count - 1}
        for _ in range(page_count)
    ]
    raw = "\n".join(json.dumps(page) for page in fixture.pages)
    evidence = check_run_evidence_from_pages(raw)
    assert len(evidence.runs) == page_count and not evidence.complete
    assert check_runs_from_pages(raw) is None
    report = fixture.read()
    assert not report.complete
    assert report.state == ("failure" if conclusion == "failure" else "")
    assert len(report.failures) == (page_count if conclusion == "failure" else 0)


def test_check_diagnostics_partial_failed_command_retains_evidence() -> None:
    fixture = CheckFixture([_run()])
    fixture.unreadable = "runs"
    report = fixture.read()
    assert report.state == "failure" and not report.complete
    assert report.failures[0].url == "https://example.com/jobs/17"


def test_check_diagnostics_malformed_later_page_retains_evidence() -> None:
    from agent_worktrees.providers.github_check_pages import (
        check_run_evidence_from_pages, check_runs_from_pages,
    )
    raw = json.dumps({"check_runs": [_run()], "total_count": 2}) + "\n{"
    evidence = check_run_evidence_from_pages(raw)
    assert not evidence.complete and isinstance(evidence.runs[0], dict)
    assert evidence.runs[0]["name"] == "optional-reviewer"
    assert check_runs_from_pages(raw) is None


def test_check_diagnostics_failed_source_does_not_erase_other_failure() -> None:
    fixture = CheckFixture([_run()])
    fixture.unreadable = "status"
    report = fixture.read()
    assert report.state == "failure" and not report.complete
    assert report.failures[0].name == "optional-reviewer"
    fixture = CheckFixture([_run("build", "success")])
    fixture.unreadable = "status"
    assert fixture.read().state == ""


def test_check_diagnostics_bounded_sanitized_output() -> None:
    run = _run("\x1b bad\ncheck")
    run["details_url"] = "https://user:password@example.com/job"
    run["output"] = {"summary": "x" * (MAX_TEXT_CHARS + 1)}
    fixture = CheckFixture([run] * (MAX_FAILURES + 3))
    report = fixture.read()
    assert len(report.failures) == MAX_FAILURES and report.omitted == 3
    assert report.failures[0].name == "bad check"
    assert report.failures[0].url == ""
    assert report.failures[0].description.endswith("[truncated]")
    assert len(report.failures[0].description) == MAX_TEXT_CHARS


@pytest.mark.parametrize("size", [MAX_TEXT_CHARS - 1, MAX_TEXT_CHARS, MAX_TEXT_CHARS + 1])
@pytest.mark.guard
def test_check_diagnostics_text_cap_includes_marker(size: int) -> None:
    text = _text("x" * size)
    assert len(text) <= MAX_TEXT_CHARS
    assert text.endswith("[truncated]") == (size > MAX_TEXT_CHARS)


def test_check_diagnostics_aggregate_without_named_status_is_explicit() -> None:
    report = CheckFixture(status={
        "state": "failure", "statuses": [{"state": "pending"}],
    }).read()
    assert report.state == "failure"
    assert report.failures[0].name == "combined commit status"
    assert "without an identified" in report.failures[0].description


def test_check_diagnostics_empty_head_does_not_read() -> None:
    fixture = CheckFixture()
    assert read_checks("", "", host="", invoke=fixture.invoke).state == ""
    assert fixture.calls == []


@pytest.mark.guard
def test_check_diagnostics_snapshot_watch_event_and_timeout(monkeypatch) -> None:
    fixture = CheckFixture([_run()])

    def invoke(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if "/pulls/" in args[-1]:
            data = [] if "/reviews?" in args[-1] else {
                "state": "open", "head": {"sha": "head"}, "base": {"ref": "dev"},
            }
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        return fixture.invoke(args)

    monkeypatch.setattr(github, "run_cli", invoke)
    snap = github.GitHubProvider().get_snapshot(
        "owner/repo", 1, api_base="https://ghe.example.com/api/v3",
    )
    result = pr_watch.run_wait(
        repo="owner/repo", pr=1, until=["checks_failed"],
        baseline=pc.Baseline(checks_state="pending"), fetch=lambda: snap,
        timeout=1, interval=1, now=lambda: 0, sleep=lambda _: None,
    )
    assert result.matched
    payload = result.payload
    assert payload["check_failures"][0]["name"] == "optional-reviewer"
    assert payload["checks_scope"] == "observed"
    assert payload["events"][0]["check_failures"] == payload["check_failures"]
    assert len(fixture.calls) == 2
    # Timeout decoration retains the same diagnostic evidence.
    clock = iter([0.0, 2.0])
    timeout = pr_watch.run_wait(
        repo="owner/repo", pr=1, until=["checks_failed"],
        baseline=pc.Baseline(checks_state="failure"), fetch=lambda: snap,
        timeout=1, interval=1, now=lambda: next(clock), sleep=lambda _: None,
    )
    assert timeout.payload["timed_out"]
    assert timeout.payload["check_failures"] == payload["check_failures"]


def test_check_diagnostics_live_status_and_human_output(monkeypatch) -> None:
    report = CheckFixture([_run()]).read()
    snap = pc.PRSnapshot(
        checks_state=report.state, check_failures=report.failures,
        checks_complete=report.complete,
    )
    provider = SimpleNamespace(
        authority_endpoint=lambda _: "https://example.com",
        get_snapshot=lambda *args, **kwargs: snap,
    )
    monkeypatch.setattr(providers, "get_provider", lambda _: provider)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *args: None)
    monkeypatch.setattr(pr_ops, "self_merge_bypass_note", lambda *args, **kwargs: None)
    active = SimpleNamespace(
        number=1, provider="github", repo="owner/repo",
        head_observed_api_base="", head_sha="", head_observed_at="",
    )
    cfg = SimpleNamespace(default_repo=SimpleNamespace(pr=config.PRConfig(provider="github")))
    live = pr_ops._live_pr_state(None, active, cfg)["live"]
    assert live["check_failures"][0]["name"] == "optional-reviewer"
    output = io.StringIO()
    print_check_failures(live, stream=output, prefix="watch: ")
    assert "optional-reviewer: failure" in output.getvalue()
    assert "https://example.com/jobs/17" in output.getvalue()
    assert "not a required-CI verdict" in output.getvalue()


def test_check_diagnostics_unsupported_provider_reports_unavailable() -> None:
    snap = replace(pc.PRSnapshot(), checks_state="failure")
    payload = checks_payload(snap.checks_state, snap.check_failures)
    assert payload["checks_complete"] is None
    output = io.StringIO()
    print_check_failures(payload, stream=output, prefix="")
    assert "named diagnostics unavailable" in output.getvalue()


@pytest.mark.guard
@pytest.mark.parametrize("state", ["", "pending", "success", "failure"])
@pytest.mark.parametrize("complete", [False, True, None])
def test_check_diagnostics_human_incomplete_without_named_failures(
    state: str, complete: bool | None,
) -> None:
    output = io.StringIO()
    print_check_failures(
        checks_payload(state, (), complete=complete), stream=output, prefix="watch: ",
    )
    text = output.getvalue()
    assert ("check evidence is incomplete" in text) is (complete is False)
    assert ("named diagnostics unavailable" in text) is (state == "failure")


def test_check_diagnostics_cli_status_heading_precedes_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import pr_state_cli, context_cli, worktree_identity

    cfg = SimpleNamespace(default_repo=SimpleNamespace(pr=config.PRConfig()))
    report = CheckFixture([_run()]).read()
    result = {"has_pr": True, "state": "open", "live": checks_payload(
        report.state, report.failures, complete=report.complete,
    )}
    monkeypatch.setattr(config, "load_config", lambda *args: cfg)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda *args: "worktree")
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda *args: "worktree")
    monkeypatch.setattr(pr_ops, "pr_status", lambda *args, **kwargs: result)
    monkeypatch.setattr(context_cli, "_pr_reminder_for", lambda *args, **kwargs: None)
    args = SimpleNamespace(json=False, config=None, worktree_id="worktree", no_live=True)
    assert pr_state_cli.cmd_pr_status(args) == 0
    output = capsys.readouterr().out
    assert output.index("  live:") < output.index("optional-reviewer: failure")
