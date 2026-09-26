"""Regression tests for the CI-failure watchdog's pure decision logic
(signature extraction, dedup lookup, rate-limiting) -- the `gh` issue-filing
I/O itself is intentionally left untested here, mirroring
`tools/test_module_health_watchdog.py`'s own convention.

Run:  python -m pytest tools/test_ci_failure_watchdog.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "ci_failure_watchdog.py"

# Realistic Actions job log shape: EVERY line is timestamp-prefixed by the
# runner, including pytest's own FAILED summary line -- a fixture without
# that prefix would hide a real "^FAILED never matches a real log" bug
# (exactly the bug a review pass caught here).
SAMPLE_PYTEST_LOG = """
2026-09-26T05:08:32.1000000Z tests/test_first_install_bootstrap.py::test_a PASSED
2026-09-26T05:08:33.2000000Z tests/test_first_install_bootstrap.py::test_b FAILED
2026-09-26T05:08:34.3000000Z =========================== short test summary info ============================
2026-09-26T05:08:34.4000000Z FAILED tests/test_first_install_bootstrap.py::test_posix_lean_provision_installs_resolver_and_launchers_reenter_runtime - Failed: Timeout (>30.0s) from pytest-timeout.
2026-09-26T05:08:34.5000000Z 1 failed, 592 passed, 5 skipped in 64.97s (0:01:04)
"""

SAMPLE_NON_PYTEST_LOG = "\n".join(f"line {i}" for i in range(60)) + "\nERROR: module too large\nExit 1\n"



def _load_watchdog():
    spec = importlib.util.spec_from_file_location("ci_failure_watchdog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def watchdog():
    return _load_watchdog()


def test_extract_failed_test_ids_finds_the_node_id(watchdog):
    ids = watchdog.extract_failed_test_ids(SAMPLE_PYTEST_LOG)
    assert ids == [
        "tests/test_first_install_bootstrap.py::test_posix_lean_provision_installs_resolver_and_launchers_reenter_runtime"
    ]


def test_extract_failed_test_ids_matches_a_timestamp_prefixed_line(watchdog):
    # A real Actions log timestamps EVERY line, including pytest's own
    # FAILED summary line -- an un-stripped `^FAILED` anchor never matches
    # a real log at all. Regression for that exact bug.
    log = "2026-09-26T05:08:34.4000000Z FAILED tests/x.py::test_y - AssertionError\n"
    assert watchdog.extract_failed_test_ids(log) == ["tests/x.py::test_y"]


def test_extract_failed_test_ids_dedupes_and_preserves_order(watchdog):
    log = "FAILED a::x\nFAILED b::y\nFAILED a::x\n"
    assert watchdog.extract_failed_test_ids(log) == ["a::x", "b::y"]


def test_extract_failed_test_ids_preserves_spaces_in_parametrized_ids(watchdog):
    # A naive \S+-style match would truncate at the first space inside the
    # brackets; the real separator between node id and reason is " - ".
    log = "FAILED tests/test_x.py::test_case[a b] - AssertionError: boom\n"
    assert watchdog.extract_failed_test_ids(log) == ["tests/test_x.py::test_case[a b]"]


def test_extract_failed_test_ids_with_no_reason_suffix(watchdog):
    log = "FAILED tests/test_x.py::test_case[a b]\n"
    assert watchdog.extract_failed_test_ids(log) == ["tests/test_x.py::test_case[a b]"]


def test_extract_failed_test_ids_reason_containing_a_hyphen_separator(watchdog):
    # A naive rsplit(" - ", 1) would cut at the LAST " - ", mistaking part
    # of the reason itself for the node id when the reason contains " - ".
    log = "FAILED tests/test_x.py::test_case - AssertionError: left - right\n"
    assert watchdog.extract_failed_test_ids(log) == ["tests/test_x.py::test_case"]


def test_build_signatures_one_per_failing_test(watchdog):
    sigs = watchdog.build_signatures("full - agent-worktrees", SAMPLE_PYTEST_LOG)
    assert len(sigs) == 1
    assert sigs[0].test_id.endswith("reenter_runtime")
    assert sigs[0].title == f"CI failure: {sigs[0].test_id}"
    assert "FAILED" in sigs[0].excerpt


def test_build_signatures_falls_back_to_whole_job_when_no_test_id(watchdog):
    sigs = watchdog.build_signatures("guards (full-tree, non-PR-scoped)", SAMPLE_NON_PYTEST_LOG)
    assert len(sigs) == 1
    assert sigs[0].test_id is None
    assert sigs[0].title == "CI failure: guards (full-tree, non-PR-scoped)"
    assert "module too large" in sigs[0].excerpt


def test_extract_failed_test_ids_ignores_run_plugin_tests_wrapper_summary(watchdog):
    # tools/run-plugin-tests.py's own wrapper emits "FAILED plugins: <name>"
    # on a failed job -- it starts with "FAILED" like a real pytest summary
    # line but has no "::" node-id shape, and must never produce a
    # misleading extra signature/issue.
    log = "FAILED plugins: agent-worktrees\n"
    assert watchdog.extract_failed_test_ids(log) == []


def test_build_signatures_falls_back_to_whole_job_for_the_wrapper_summary_alone(watchdog):
    log = "some earlier output\nFAILED plugins: agent-worktrees\n"
    sigs = watchdog.build_signatures("full - agent-worktrees", log)
    assert len(sigs) == 1
    assert sigs[0].test_id is None


def test_signature_key_fallback_is_stable_across_different_timestamps(watchdog):
    # The real Actions log timestamps every line -- the same underlying
    # failure must still dedupe across two runs whose timestamps differ.
    excerpt_a = "2026-09-26T05:08:32.0000000Z ERROR: module too large\n2026-09-26T05:08:32.1000000Z Exit 1\n"
    excerpt_b = "2026-09-27T11:22:33.4444444Z ERROR: module too large\n2026-09-27T11:22:33.5555555Z Exit 1\n"

    sigs_a = watchdog.build_signatures("guards (full-tree, non-PR-scoped)", excerpt_a)
    sigs_b = watchdog.build_signatures("guards (full-tree, non-PR-scoped)", excerpt_b)

    assert sigs_a[0].key == sigs_b[0].key


def test_signature_key_is_stable_for_the_same_test_id(watchdog):
    a = watchdog.signature_key("job", "path::test")
    b = watchdog.signature_key("job", "path::test")
    assert a == b


def test_signature_key_differs_for_different_test_ids(watchdog):
    a = watchdog.signature_key("job", "path::test_a")
    b = watchdog.signature_key("job", "path::test_b")
    assert a != b


def test_signature_key_whole_job_incorporates_job_name(watchdog):
    a = watchdog.signature_key("job-a", None, "same tail")
    b = watchdog.signature_key("job-b", None, "same tail")
    assert a != b


def test_signature_key_cross_job_same_test_id_produces_different_keys(watchdog):
    # This repo vendors shared libs into multiple plugins, so `full` can run
    # the identical test node id as separate jobs (e.g. `full - agent-bridge`
    # vs `full - agent-mcp`) -- those must never collapse into one dedup
    # key/issue, since they're failures in different plugins' own copies.
    a = watchdog.signature_key("full - agent-bridge", "tests/test_shared.py::test_x")
    b = watchdog.signature_key("full - agent-mcp", "tests/test_shared.py::test_x")
    assert a != b


def test_is_rate_limited_true_within_the_window(watchdog):
    now = datetime.now(timezone.utc)
    issue = {"updatedAt": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")}
    assert watchdog.is_rate_limited(issue, now, hours=6) is True


def test_is_rate_limited_false_outside_the_window(watchdog):
    now = datetime.now(timezone.utc)
    issue = {"updatedAt": (now - timedelta(hours=7)).isoformat().replace("+00:00", "Z")}
    assert watchdog.is_rate_limited(issue, now, hours=6) is False


def test_existing_issue_raises_lookup_failed_on_a_nonzero_exit(watchdog, monkeypatch):
    class _FailedRun:
        returncode = 1
        stdout = ""
        stderr = "label 'ci-failure-signature' not found"

    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: _FailedRun())

    with pytest.raises(watchdog.LookupFailed):
        watchdog._existing_issue("owner/repo", "abc123")


def test_existing_issue_returns_none_on_a_clean_no_match(watchdog, monkeypatch):
    class _EmptyRun:
        returncode = 0
        stdout = "[]"
        stderr = ""

    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: _EmptyRun())

    assert watchdog._existing_issue("owner/repo", "abc123") is None


def test_existing_issue_returns_the_match(watchdog, monkeypatch):
    class _MatchRun:
        returncode = 0
        stdout = json.dumps([{"number": 42, "updatedAt": "2026-01-01T00:00:00Z"}])
        stderr = ""

    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: _MatchRun())

    issue = watchdog._existing_issue("owner/repo", "abc123")
    assert issue["number"] == 42


def test_process_signature_dry_run_never_shells_out(watchdog, monkeypatch):
    called = False

    def _fail(*_a, **_k):
        nonlocal called
        called = True
        raise AssertionError("should not shell out in dry-run mode")

    monkeypatch.setattr(watchdog.subprocess, "run", _fail)
    sig = watchdog.FailureSignature(job_name="j", test_id="t", key="k", excerpt="e")

    rc = watchdog.process_signature("owner/repo", sig, "1", "sha", 6, file_issue=False)

    assert rc == 0
    assert called is False


def test_process_signature_aborts_without_filing_when_lookup_fails(watchdog, monkeypatch, capsys):
    def _raise_lookup_failed(*_a, **_k):
        raise watchdog.LookupFailed("simulated transient failure")

    called_file_issue = False

    def _fake_file_issue(*_a, **_k):
        nonlocal called_file_issue
        called_file_issue = True
        return True

    monkeypatch.setattr(watchdog, "_existing_issue", _raise_lookup_failed)
    monkeypatch.setattr(watchdog, "_file_issue", _fake_file_issue)
    sig = watchdog.FailureSignature(job_name="j", test_id="t", key="k", excerpt="e")

    rc = watchdog.process_signature("owner/repo", sig, "1", "sha", 6, file_issue=True)

    # Nonzero, not zero: nothing was actually filed/commented in
    # --file-issue mode, so reporting success would mask a broken dedup
    # lookup behind a green step.
    assert rc == 1
    assert called_file_issue is False
    assert "aborting without filing" in capsys.readouterr().err


def test_process_signature_files_when_no_existing_issue(watchdog, monkeypatch):
    monkeypatch.setattr(watchdog, "_existing_issue", lambda *_a, **_k: None)
    filed_with: dict = {}

    def _fake_file_issue(repo, sig, run_id, sha):
        filed_with["repo"] = repo
        return True

    monkeypatch.setattr(watchdog, "_file_issue", _fake_file_issue)
    sig = watchdog.FailureSignature(job_name="j", test_id="t", key="k", excerpt="e")

    rc = watchdog.process_signature("owner/repo", sig, "1", "sha", 6, file_issue=True)

    assert rc == 0
    assert filed_with["repo"] == "owner/repo"


def test_process_signature_skips_comment_when_rate_limited(watchdog, monkeypatch):
    now = datetime.now(timezone.utc)
    existing = {
        "number": 7,
        "updatedAt": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }
    monkeypatch.setattr(watchdog, "_existing_issue", lambda *_a, **_k: existing)

    called = False

    def _fake_comment(*_a, **_k):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(watchdog, "_comment_occurrence", _fake_comment)
    sig = watchdog.FailureSignature(job_name="j", test_id="t", key="k", excerpt="e")

    rc = watchdog.process_signature("owner/repo", sig, "1", "sha", 6, file_issue=True)

    assert rc == 0
    assert called is False


def test_process_signature_comments_when_outside_rate_limit(watchdog, monkeypatch):
    now = datetime.now(timezone.utc)
    existing = {
        "number": 7,
        "updatedAt": (now - timedelta(hours=30)).isoformat().replace("+00:00", "Z"),
    }
    monkeypatch.setattr(watchdog, "_existing_issue", lambda *_a, **_k: existing)

    commented_on = {}

    def _fake_comment(repo, issue_number, sig, run_id, sha):
        commented_on["number"] = issue_number
        return True

    monkeypatch.setattr(watchdog, "_comment_occurrence", _fake_comment)
    sig = watchdog.FailureSignature(job_name="j", test_id="t", key="k", excerpt="e")

    rc = watchdog.process_signature("owner/repo", sig, "1", "sha", 6, file_issue=True)

    assert rc == 0
    assert commented_on["number"] == 7


def test_main_skips_control_jobs_and_reports_dry_run(watchdog, monkeypatch, capsys):
    jobs_response = {
        "jobs": [
            {"id": 1, "name": "gate (confirm this is a dev commit)", "conclusion": "success"},
            {"id": 2, "name": "full - agent-worktrees", "conclusion": "failure"},
            {"id": 3, "name": "Promote dev -> main", "conclusion": "skipped"},
        ]
    }

    class _JobsRun:
        returncode = 0
        stdout = json.dumps(jobs_response)
        stderr = ""

    def _fake_gh(args):
        if args[:2] == ["api", "repos/owner/repo/actions/runs/1/jobs?per_page=100"]:
            return _JobsRun()
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(watchdog, "_run_gh", _fake_gh)
    monkeypatch.setattr(watchdog, "_fetch_job_log", lambda repo, job_id: SAMPLE_PYTEST_LOG)

    rc = watchdog.main(["--repo", "owner/repo", "--run-id", "1", "--sha", "deadbeef"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "reenter_runtime" in out


def test_main_returns_zero_when_nothing_failed(watchdog, monkeypatch, capsys):
    jobs_response = {
        "jobs": [
            {"id": 1, "name": "gate (confirm this is a dev commit)", "conclusion": "success"},
            {"id": 2, "name": "full - agent-worktrees", "conclusion": "success"},
        ]
    }

    class _JobsRun:
        returncode = 0
        stdout = json.dumps(jobs_response)
        stderr = ""

    monkeypatch.setattr(watchdog, "_run_gh", lambda args: _JobsRun())

    rc = watchdog.main(["--repo", "owner/repo", "--run-id", "1", "--sha", "deadbeef"])

    assert rc == 0
    assert "nothing to report" in capsys.readouterr().out


def test_main_reports_a_timed_out_job_not_just_failure(watchdog, monkeypatch, capsys):
    # A job that exceeds its own timeout-minutes gets conclusion timed_out,
    # not failure -- it still turns the run red and must be reportable.
    jobs_response = {
        "jobs": [
            {"id": 1, "name": "gate (confirm this is a dev commit)", "conclusion": "success"},
            {"id": 2, "name": "full - agent-worktrees", "conclusion": "timed_out"},
        ]
    }

    class _JobsRun:
        returncode = 0
        stdout = json.dumps(jobs_response)
        stderr = ""

    def _fake_gh(args):
        if args[:2] == ["api", "repos/owner/repo/actions/runs/1/jobs?per_page=100"]:
            return _JobsRun()
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(watchdog, "_run_gh", _fake_gh)
    monkeypatch.setattr(watchdog, "_fetch_job_log", lambda repo, job_id: SAMPLE_NON_PYTEST_LOG)

    rc = watchdog.main(["--repo", "owner/repo", "--run-id", "1", "--sha", "deadbeef"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing to report" not in out
    assert "dry run" in out


def test_main_returns_nonzero_when_job_list_fetch_fails_in_file_issue_mode(watchdog, monkeypatch, capsys):
    class _FailedRun:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(watchdog, "_run_gh", lambda args: _FailedRun())

    rc = watchdog.main(["--repo", "owner/repo", "--run-id", "1", "--sha", "deadbeef", "--file-issue"])

    assert rc == 1
    assert "could not fetch run jobs" in capsys.readouterr().err


def test_main_stays_zero_when_job_list_fetch_fails_in_dry_run_mode(watchdog, monkeypatch):
    class _FailedRun:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(watchdog, "_run_gh", lambda args: _FailedRun())

    rc = watchdog.main(["--repo", "owner/repo", "--run-id", "1", "--sha", "deadbeef"])

    assert rc == 0


def test_main_returns_nonzero_when_a_job_log_fetch_fails_in_file_issue_mode(watchdog, monkeypatch, capsys):
    jobs_response = {
        "jobs": [
            {"id": 2, "name": "full - agent-worktrees", "conclusion": "failure"},
        ]
    }

    class _JobsRun:
        returncode = 0
        stdout = json.dumps(jobs_response)
        stderr = ""

    def _fake_gh(args):
        if args[:2] == ["api", "repos/owner/repo/actions/runs/1/jobs?per_page=100"]:
            return _JobsRun()
        raise AssertionError(f"unexpected gh call: {args}")

    def _raise_log_fetch(repo, job_id):
        raise watchdog.LookupFailed("simulated log fetch failure")

    monkeypatch.setattr(watchdog, "_run_gh", _fake_gh)
    monkeypatch.setattr(watchdog, "_fetch_job_log", _raise_log_fetch)

    rc = watchdog.main(["--repo", "owner/repo", "--run-id", "1", "--sha", "deadbeef", "--file-issue"])

    assert rc == 1
    assert "could not fetch log" in capsys.readouterr().err
