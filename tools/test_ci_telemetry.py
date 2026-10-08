"""Regression tests for the CI-telemetry pipeline's pure aggregation logic
(rerun-recovery detection, blocking-impact, signature stats) -- the `gh` API
fetch itself is intentionally left untested here, mirroring
`tools/test_ci_failure_watchdog.py`'s own convention.

Run:  python -m pytest tools/test_ci_telemetry.py
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "ci_telemetry.py"


def _load_telemetry():
    spec = importlib.util.spec_from_file_location("ci_telemetry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def telemetry():
    return _load_telemetry()


def _t(offset_minutes: int) -> datetime:
    return datetime(2026, 9, 27, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)


def test_compute_flaky_shas_marks_a_commit_that_failed_then_succeeded(telemetry):
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1"),
        RunRecord(run_id=2, head_sha="a", conclusion="success", created_at=_t(10), html_url="u2"),
    ]
    assert telemetry.compute_flaky_shas(runs) == {"a"}


def test_compute_flaky_shas_excludes_a_commit_that_only_ever_failed(telemetry):
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="b", conclusion="failure", created_at=_t(0), html_url="u1"),
    ]
    assert telemetry.compute_flaky_shas(runs) == set()


def test_compute_flaky_shas_excludes_a_commit_that_only_ever_succeeded(telemetry):
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="c", conclusion="success", created_at=_t(0), html_url="u1"),
    ]
    assert telemetry.compute_flaky_shas(runs) == set()


def test_compute_blocking_impact_counts_runs_stalled_until_next_success(telemetry):
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1"),
        RunRecord(run_id=2, head_sha="b", conclusion="failure", created_at=_t(10), html_url="u2"),
        RunRecord(run_id=3, head_sha="c", conclusion="success", created_at=_t(20), html_url="u3"),
        RunRecord(run_id=4, head_sha="d", conclusion="success", created_at=_t(30), html_url="u4"),
    ]
    impact = telemetry.compute_blocking_impact(runs)
    # run 1 blocked runs 2 and 3 (stops counting once a success is reached)
    assert impact[(1, 1)] == 2
    # run 2 blocked only run 3
    assert impact[(2, 1)] == 1
    # successful runs never appear as blockers
    assert (3, 1) not in impact
    assert (4, 1) not in impact


def test_compute_blocking_impact_a_trailing_failure_with_nothing_after_it_blocks_nothing(telemetry):
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="a", conclusion="success", created_at=_t(0), html_url="u1"),
        RunRecord(run_id=2, head_sha="b", conclusion="failure", created_at=_t(10), html_url="u2"),
    ]
    assert telemetry.compute_blocking_impact(runs)[(2, 1)] == 0


def test_compute_signature_stats_aggregates_occurrences_recovery_and_blocking(telemetry):
    RunRecord = telemetry.RunRecord
    FailureRecord = telemetry.FailureRecord
    FailureSignature = telemetry.FailureSignature

    sig = FailureSignature(job_name="full - x", test_id="tests/x.py::test_y", key="deadbeef1234", excerpt="boom")

    runs = [
        RunRecord(run_id=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1"),
        RunRecord(run_id=2, head_sha="a", conclusion="success", created_at=_t(10), html_url="u2"),
        RunRecord(run_id=3, head_sha="b", conclusion="failure", created_at=_t(20), html_url="u3"),
        RunRecord(run_id=4, head_sha="c", conclusion="success", created_at=_t(30), html_url="u4"),
    ]
    failures = [
        FailureRecord(run_id=1, head_sha="a", created_at=_t(0), job_name="full - x", signature=sig),
        FailureRecord(run_id=3, head_sha="b", created_at=_t(20), job_name="full - x", signature=sig),
    ]

    stats = telemetry.compute_signature_stats(failures, runs)
    assert len(stats) == 1
    stat = stats[0]
    assert stat.key == "deadbeef1234"
    assert stat.occurrences == 2
    assert stat.distinct_shas == 2
    # only sha "a" recovered (failed then later succeeded); "b" never did
    assert stat.recovered_shas == 1
    assert stat.recovery_rate == pytest.approx(0.5)
    # run 1 blocked run 2 (1); run 3 blocked run 4 (1) -> total 2
    assert stat.blocking_impact == 2


def test_render_report_known_noisy_nonblocking_checks_section_is_empty(telemetry):
    # The sole prior entry (identifier-leak-guard-unconfigured) was removed
    # 2026-10-06 once efforts/active/ci-identifier-leak-guard/ (#3923)
    # provisioned the denylist secrets and the check stopped firing red --
    # see KNOWN_NOISY_NONBLOCKING_CHECKS's own docstring. The section still
    # renders, just empty, so a future entry has somewhere to land.
    report = telemetry.render_report([])
    assert "Known noisy, non-blocking checks" in report
    assert "none currently tracked" in report
    assert "identifier-leak-guard-unconfigured" not in report


def test_replace_source_snapshot_drops_stale_out_of_window_rows(telemetry, tmp_path):
    # A repeated `refresh` must not accumulate stale runs that have since
    # fallen out of the lookback window (or a source `--dev-only` no longer
    # fetches) -- `report` loads every persisted row unconditionally, so a
    # snapshot that only ever grows would silently rank stale data forever.
    # Regression for a real Copilot review finding on PR #4296.
    RunRecord = telemetry.RunRecord
    conn = telemetry.open_db(tmp_path / "telemetry.sqlite3")
    telemetry.persist_runs(
        conn,
        [
            RunRecord(run_id=1, head_sha="old", conclusion="failure", created_at=_t(0), html_url="u1"),
        ],
    )
    telemetry.replace_source_snapshot(conn, {"dev-push"})
    telemetry.persist_runs(
        conn,
        [
            RunRecord(run_id=2, head_sha="new", conclusion="success", created_at=_t(10), html_url="u2"),
        ],
    )
    runs = telemetry.load_runs(conn)
    assert [r.run_id for r in runs] == [2]


def test_compute_flaky_shas_detects_a_same_run_rerun_recovery(telemetry):
    # `gh run rerun --failed` reuses the SAME run_id at a higher attempt
    # number rather than creating a new run -- the exact case
    # `_fetch_prior_attempts` exists to surface as a distinct RunRecord.
    # Both attempts share the IDENTICAL top-level `created_at` (a real
    # constraint of the fetched attempts-endpoint fields) -- regression for
    # the tie-break bug where sorting by `created_at` alone left the
    # attempts in fetch/insertion order (latest-attempt-first), which
    # silently hid every real recovery. `attempt` must break the tie.
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, attempt=2, head_sha="a", conclusion="success", created_at=_t(0), html_url="u1", source="pr"),
        RunRecord(run_id=1, attempt=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1", source="pr"),
    ]
    assert telemetry.compute_flaky_shas(runs) == {"a"}


def test_compute_blocking_impact_ignores_pr_sourced_runs(telemetry):
    # PR-time runs are concurrent across unrelated PRs -- they never
    # represent a single serialized promotion queue, so they must never
    # contribute to blocking-impact accounting (only dev-push runs do).
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1", source="pr"),
        RunRecord(run_id=2, head_sha="b", conclusion="success", created_at=_t(10), html_url="u2", source="pr"),
    ]
    assert telemetry.compute_blocking_impact(runs) == {}


def test_compute_blocking_impact_does_not_double_count_a_same_run_failed_rerun(telemetry):
    # A later failed attempt of the SAME run_id is that run's own retry, not
    # a newly-stalled, separate commit -- it must not inflate the blocked
    # count. Regression for a real Copilot review finding on PR #4296.
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1", attempt=1),
        RunRecord(run_id=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1", attempt=2),
        RunRecord(run_id=2, head_sha="b", conclusion="success", created_at=_t(10), html_url="u2"),
    ]
    impact = telemetry.compute_blocking_impact(runs)
    # run 1's own attempt 2 (same run_id) never counts as a blocked run;
    # only run 2 (a genuinely different commit) does.
    assert impact[(1, 1)] == 1


def test_compute_blocking_impact_a_same_run_successful_rerun_still_ends_the_interval(telemetry):
    # A same-run SUCCESSFUL rerun still legitimately unblocks the queue --
    # it must terminate the blocked count exactly like any other success,
    # even though it doesn't itself increment `blocked`.
    RunRecord = telemetry.RunRecord
    runs = [
        RunRecord(run_id=1, head_sha="a", conclusion="failure", created_at=_t(0), html_url="u1", attempt=1),
        RunRecord(run_id=1, head_sha="a", conclusion="success", created_at=_t(0), html_url="u1", attempt=2),
        RunRecord(run_id=2, head_sha="b", conclusion="failure", created_at=_t(10), html_url="u2"),
    ]
    impact = telemetry.compute_blocking_impact(runs)
    # run 1 is unblocked by its own successful rerun before run 2 ever runs.
    assert impact[(1, 1)] == 0
