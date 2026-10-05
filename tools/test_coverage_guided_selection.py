"""Tests for tools/coverage_guided_selection -- the coverage-guided-ci Phase 0
design-spike prototype (see efforts/active/coverage-guided-ci's 2026-10-01
Journal entry).

`test_select_*` and `test_fallback_*` are synthetic and fast: they construct
a baseline dict by hand so selection/curation logic is verified against a
known-correct expectation, independent of any real pytest/coverage run.

`test_collect_baseline_round_trips_against_a_real_plugin_suite` is the one
real integration check: it runs `baseline.collect_baseline` against the
`ai-attribution` plugin's own (small, fast) suite via an ephemeral
`uv run --with coverage --with pytest-cov` subprocess, and asserts the
round-trip produces internally consistent, real coverage/duration data --
proving Phase 0's "spike coverage collection ... confirm the artifact it
produces round-trips through the chosen storage/correlation mechanism"
checklist item against a real suite, not just synthetic data. It is
deliberately **opt-in**: it self-skips unless `CGS_RUN_INTEGRATION_TEST=1`
is set, since it spawns a real subprocess with network-dependent package
resolution rather than running as part of the fast, always-on, pure-stdlib
synthetic tests above. `.github/workflows/ci.yml` runs the synthetic tests
in every PR's required `checks` job, and this one test only in a separate,
workflow_dispatch-only `coverage-guided-selection-integration` job.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from tools.coverage_guided_selection import baseline as baseline_mod  # noqa: E402
from tools.coverage_guided_selection import correlation  # noqa: E402
from tools.coverage_guided_selection import fallback, selection as select  # noqa: E402
from tools.coverage_guided_selection import ancestor_resolution as ar
from tools.coverage_guided_selection import debt  # noqa: E402
from tools.coverage_guided_selection import decide as decide_mod  # noqa: E402


def _synthetic_baseline() -> dict:
    """A hand-built baseline: 3 tests covering 5 lines across 2 files.

    - test_a: file.py lines 1,2 (cheap, 0.1s)
    - test_b: file.py lines 2,3 + other.py line 10 (expensive, 2.0s)
    - test_c: other.py line 11 only (cheap, 0.2s)

    file.py line 4 has no attribution at all (exists in the coverage map
    with an empty-implying absence -- simply no key), and third.py is never
    in the coverage map at all.
    """
    return {
        "schema_version": 1,
        "plugin": "synthetic",
        "tests": {
            "test_a": {"duration_s": 0.1},
            "test_b": {"duration_s": 2.0},
            "test_c": {"duration_s": 0.2},
        },
        "coverage": {
            "file.py": {
                "1": ["test_a"],
                "2": ["test_a", "test_b"],
                "3": ["test_b"],
            },
            "other.py": {
                "10": ["test_b"],
                "11": ["test_c"],
            },
        },
    }


class TestSelectTests:
    def test_selects_exact_covering_tests_for_a_changed_line(self) -> None:
        result = select.select_tests(_synthetic_baseline(), {"file.py": [1]})
        assert result.selected_tests == ("test_a",)
        assert not result.fallback_triggered

    def test_selects_union_across_multiple_changed_lines(self) -> None:
        result = select.select_tests(
            _synthetic_baseline(), {"file.py": [1, 3], "other.py": [11]}
        )
        assert result.selected_tests == ("test_a", "test_b", "test_c")
        assert not result.fallback_triggered

    def test_falls_back_for_a_line_with_no_attribution_in_a_known_file(self) -> None:
        # file.py line 4 has no key at all -- a partially-attributed file is
        # not a fully-covered one; this must trigger fallback, not silently
        # select nothing.
        result = select.select_tests(_synthetic_baseline(), {"file.py": [4]})
        assert result.selected_tests == ()
        assert result.fallback_triggered
        assert result.fallback_reasons[0].reason == "line_not_attributed"

    def test_falls_back_for_a_file_absent_from_the_baseline(self) -> None:
        result = select.select_tests(_synthetic_baseline(), {"third.py": [1]})
        assert result.selected_tests == ()
        assert result.fallback_triggered
        assert result.fallback_reasons[0].reason == "no_baseline_entry"

    def test_mixed_known_and_unattributed_lines_still_selects_the_known_ones(
        self,
    ) -> None:
        # Fallback for one line never suppresses a real selection for
        # another, correctly-attributed line in the same diff.
        result = select.select_tests(
            _synthetic_baseline(), {"file.py": [1, 4]}
        )
        assert result.selected_tests == ("test_a",)
        assert result.fallback_triggered
        assert len(result.fallback_reasons) == 1


class TestComputeFallbackSet:
    def test_covers_the_full_universe_within_a_generous_budget(self) -> None:
        fb = fallback.compute_fallback_set(_synthetic_baseline(), runtime_budget_s=10.0)
        assert fb.covered_fraction == 1.0
        assert fb.universe_size == 5

    def test_prefers_cheaper_higher_yield_tests_under_a_tight_budget(self) -> None:
        # Budget only large enough for the two cheap tests (0.1 + 0.2 = 0.3s),
        # not the expensive one (2.0s) -- greedy-by-score should still pick
        # test_a and test_c (covering file.py#1,2 and other.py#11) before
        # ever considering test_b, since both have a strictly better
        # coverage-per-second score (test_a: 2 lines/0.1s=20; test_c: 1
        # line/0.2s=5; test_b initially 4 lines/2.0s=2).
        fb = fallback.compute_fallback_set(_synthetic_baseline(), runtime_budget_s=0.35)
        assert "test_a" in fb.selected_tests
        assert "test_b" not in fb.selected_tests
        assert fb.total_runtime_s <= 0.35

    def test_budget_too_small_for_any_candidate_yields_an_empty_selection(
        self,
    ) -> None:
        # The budget is a hard cap: if even the cheapest useful candidate
        # (test_a, 0.1s) would exceed it, the correct result is an honestly
        # empty, incomplete fallback -- never a pick that silently breaches
        # the caller's own stated budget.
        fb = fallback.compute_fallback_set(_synthetic_baseline(), runtime_budget_s=0.001)
        assert fb.selected_tests == ()
        assert fb.total_runtime_s == 0.0
        assert fb.covered_fraction < 1.0

    def test_empty_baseline_yields_an_empty_fully_covered_fallback(self) -> None:
        empty = {"tests": {}, "coverage": {}}
        fb = fallback.compute_fallback_set(empty, runtime_budget_s=10.0)
        assert fb.selected_tests == ()
        assert fb.covered_fraction == 1.0
        assert fb.universe_size == 0

    def test_eligible_tests_restriction_excludes_ineligible_candidates(self) -> None:
        # Restricting to {test_a, test_c} must leave test_b's unique line
        # (other.py#10) in the universe (the denominator is always the full
        # baseline) but permanently unreachable -- covered_fraction must
        # show that gap, never hide it by shrinking its own denominator.
        fb = fallback.compute_fallback_set(
            _synthetic_baseline(),
            runtime_budget_s=10.0,
            eligible_tests=frozenset({"test_a", "test_c"}),
        )
        assert "test_b" not in fb.selected_tests
        assert fb.universe_size == 5  # the full baseline's own universe
        assert fb.covered_fraction == pytest.approx(3 / 5)  # other.py#10, file.py#3 unreachable

    def test_missing_or_invalid_duration_excludes_a_test_as_ineligible(self) -> None:
        # test_bad is the *only* test covering file.py#2; a missing duration
        # must exclude it as a candidate (never price it as free/near-zero),
        # so that line becomes unreachable by this curation rather than
        # test_bad being selected purely because its cost looks attractive.
        baseline = {
            "tests": {
                "test_a": {"duration_s": 0.1},
                "test_bad": {},  # no duration_s at all
                "test_c": {"duration_s": 0.2},
            },
            "coverage": {
                "file.py": {
                    "1": ["test_a"],
                    "2": ["test_bad"],
                    "3": ["test_c"],
                },
            },
        }
        fb = fallback.compute_fallback_set(baseline, runtime_budget_s=10.0)
        assert "test_bad" not in fb.selected_tests
        assert fb.universe_size == 3
        assert fb.covered_fraction < 1.0  # file.py#2 is unreachably excluded

    def test_negative_or_non_finite_duration_is_also_excluded(self) -> None:
        baseline = {
            "tests": {
                "test_a": {"duration_s": 0.1},
                "test_neg": {"duration_s": -1.0},
                "test_nan": {"duration_s": float("nan")},
            },
            "coverage": {
                "file.py": {
                    "1": ["test_a"],
                    "2": ["test_neg"],
                    "3": ["test_nan"],
                },
            },
        }
        fb = fallback.compute_fallback_set(baseline, runtime_budget_s=10.0)
        assert "test_neg" not in fb.selected_tests
        assert "test_nan" not in fb.selected_tests
        assert fb.covered_fraction == pytest.approx(1 / 3)

    def test_skips_an_unaffordable_higher_scoring_candidate_for_a_cheaper_one(
        self,
    ) -> None:
        # Regression test for "skip unaffordable candidates instead of
        # stopping": after picking test1 (cost 1.0), the highest-scoring
        # remaining candidate (test2, cost 1.0) no longer fits a 1.9s
        # budget, but a lower-scoring, cheaper candidate (test3, cost 0.8)
        # still does and must still be picked rather than ending the pass.
        baseline = {
            "tests": {
                "test1": {"duration_s": 1.0},
                "test2": {"duration_s": 1.0},
                "test3": {"duration_s": 0.8},
            },
            "coverage": {
                "file.py": {
                    "1": ["test1"],
                    "2": ["test1"],
                    "3": ["test2"],
                    "4": ["test2"],
                    "5": ["test3"],
                },
            },
        }
        fb = fallback.compute_fallback_set(baseline, runtime_budget_s=1.9)
        assert fb.selected_tests == ("test1", "test3")
        assert "test2" not in fb.selected_tests
        assert fb.total_runtime_s == pytest.approx(1.8)
        assert fb.covered_fraction == pytest.approx(0.6)

    def test_curation_is_deterministic_across_repeated_runs(self) -> None:
        # Two tests tied on coverage-per-cost score must still produce an
        # identical result run after run (stable tie-break), not one that
        # varies with set/hash iteration order.
        baseline = _synthetic_baseline()
        first = fallback.compute_fallback_set(baseline, runtime_budget_s=0.35)
        second = fallback.compute_fallback_set(baseline, runtime_budget_s=0.35)
        assert first.selected_tests == second.selected_tests


class TestNoStdlibModuleNameCollisions:
    """Incident regression (the `select.py` shadowing-stdlib outage): this
    package's `baseline.py` is invoked directly as a script by
    `validate-and-promote.yml`, which prepends this package's own directory
    to `sys.path` -- so a sibling module here that collides with any
    top-level stdlib module name silently shadows it for every later import
    in that same process (confirmed live: `select.py` broke `subprocess`'s
    own transitive `import selectors -> import select`). A package-import
    test alone (the rest of this file) never catches this class of bug,
    since importing the package normally never prepends this directory to
    `sys.path` the way the real script-style invocation does."""

    def test_no_sibling_module_name_collides_with_a_stdlib_module(self) -> None:
        package_dir = _REPO_ROOT / "tools" / "coverage_guided_selection"
        local_names = {
            p.stem for p in package_dir.glob("*.py") if p.name != "__init__.py"
        }
        # A sibling PACKAGE directory (one with its own __init__.py) is just
        # as importable -- and just as capable of shadowing a stdlib
        # top-level package (e.g. a future `email/` here would shadow
        # stdlib `email`) -- as a sibling module file, so it must be
        # collected the same way, not just *.py files.
        local_names |= {
            d.name
            for d in package_dir.iterdir()
            if d.is_dir() and (d / "__init__.py").is_file()
        }
        collisions = local_names & set(sys.stdlib_module_names)
        assert not collisions, (
            f"{collisions!r} collide with stdlib top-level module names -- "
            "a module here would shadow the real stdlib module for any "
            "script-style invocation of baseline.py (see this test class's "
            "own docstring for the exact outage this already caused)"
        )

    def test_baseline_cli_runs_as_a_plain_script_without_crashing(self) -> None:
        # Directly reproduces the real invocation that broke: running
        # baseline.py as a script (not importing the package) prepends its
        # own directory to sys.path. --help exits 0 after argparse runs,
        # without needing a real pytest/coverage subprocess -- enough to
        # prove every top-level import in baseline.py (including the
        # `import subprocess` that crashed) still succeeds in script mode.
        baseline_script = _REPO_ROOT / "tools" / "coverage_guided_selection" / "baseline.py"
        proc = subprocess.run(
            [sys.executable, str(baseline_script), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestCorrelation:
    def test_baseline_path_on_main_is_one_file_per_plugin(self) -> None:
        assert (
            correlation.baseline_path_on_main("ai-attribution")
            == ".github/coverage-baselines/ai-attribution.json"
        )
        assert (
            correlation.baseline_path_on_main("agent-worktrees")
            == ".github/coverage-baselines/agent-worktrees.json"
        )

    def test_require_measured_commit_returns_the_sha_when_present(self) -> None:
        baseline = {**_synthetic_baseline(), "measured_commit": "abc123"}
        assert correlation.require_measured_commit(baseline) == "abc123"

    def test_require_measured_commit_rejects_a_missing_sha(self) -> None:
        baseline = {**_synthetic_baseline(), "measured_commit": None}
        with pytest.raises(correlation.BaselineCorrelationError):
            correlation.require_measured_commit(baseline)

    def test_require_measured_commit_rejects_an_absent_key(self) -> None:
        baseline = _synthetic_baseline()  # no "measured_commit" key at all
        with pytest.raises(correlation.BaselineCorrelationError):
            correlation.require_measured_commit(baseline)

    def test_release_tag_for_uses_the_full_measured_commit_not_a_prefix(self) -> None:
        """A truncated prefix risks two distinct commits colliding on the
        same release tag (silently overwriting an unrelated commit's
        already-published baseline assets) -- the tag must embed the full
        SHA."""
        sha = "abc123def456abc123def456abc123def456abc"
        assert correlation.release_tag_for(sha) == f"coverage-baselines-{sha}"

    def test_release_tag_for_is_stable_for_the_same_commit(self) -> None:
        sha = "f" * 40
        assert correlation.release_tag_for(sha) == correlation.release_tag_for(sha)

    def test_asset_name_for_matches_the_collection_step_artifact_name(self) -> None:
        assert correlation.asset_name_for("agent-dispatch") == "agent-dispatch.json"

    def test_build_pointer_embeds_the_release_tag_and_asset_name(self) -> None:
        sha = "a" * 40
        pointer = correlation.build_pointer("agent-worktrees", sha)
        assert pointer["schema"] == correlation.POINTER_SCHEMA
        assert pointer["plugin"] == "agent-worktrees"
        assert pointer["measured_commit"] == sha
        assert pointer["release_tag"] == correlation.release_tag_for(sha)
        assert pointer["asset"] == "agent-worktrees.json"
        # The pointer is a correlation index, never the payload -- no
        # per-line coverage map should ever be embedded here.
        assert "coverage" not in pointer


class TestFetchBaselineAsset:
    _POINTER = {
        "release_tag": "coverage-baselines-abc", "asset": "agent-x.json",
        "plugin": "agent-x", "measured_commit": "abc",
    }
    _VALID_PAYLOAD = {
        "plugin": "agent-x", "measured_commit": "abc",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "coverage": {}, "tests": {},
    }

    def _write_asset(self, args, payload) -> None:
        dest_dir = Path(args[args.index("--dir") + 1])
        (dest_dir / "agent-x.json").write_text(json.dumps(payload))

    def _fake_run_writing(self, payload):
        def _fake_run(args, **_kwargs):
            self._write_asset(args, payload)

            class _Result:
                returncode = 0
                stdout = ""
                stderr = ""
            return _Result()
        return _fake_run

    def test_rejects_a_pointer_missing_release_tag_or_asset(self) -> None:
        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", {"plugin": "x", "measured_commit": "abc"})

    def test_rejects_a_pointer_missing_plugin_or_measured_commit(self) -> None:
        # Regression: a pointer that omits its own identity fields must be
        # rejected outright, not silently skip the later
        # plugin/measured_commit correlation check.
        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset(
                "owner/repo", {"release_tag": "coverage-baselines-abc", "asset": "agent-x.json"}
            )

    def test_rejects_a_pointer_with_non_string_fields(self) -> None:
        # Regression: a syntactically-truthy but non-string field (e.g. a
        # numeric release_tag, a list-valued asset) would otherwise reach
        # subprocess.run/Path and raise a raw TypeError, bypassing
        # BaselineFetchError entirely.
        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset(
                "owner/repo",
                {"release_tag": 12345, "asset": "agent-x.json", "plugin": "agent-x", "measured_commit": "abc"},
            )
        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset(
                "owner/repo",
                {"release_tag": "t", "asset": ["agent-x.json"], "plugin": "agent-x", "measured_commit": "abc"},
            )

    def test_downloads_and_parses_the_asset(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(self._VALID_PAYLOAD))

        result = correlation.fetch_baseline_asset("owner/repo", self._POINTER)

        assert result == self._VALID_PAYLOAD

    def test_raises_on_a_failed_download(self, monkeypatch) -> None:
        class _Failed:
            returncode = 1
            stdout = ""
            stderr = "release not found"

        monkeypatch.setattr(correlation.subprocess, "run", lambda *a, **k: _Failed())

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_gh_is_not_launchable(self, monkeypatch) -> None:
        # Regression: `subprocess.run` itself raises OSError (e.g.
        # FileNotFoundError) when `gh` isn't on PATH at all -- this must
        # not bypass BaselineFetchError.
        def _raise_oserror(*_a, **_k):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr(correlation.subprocess, "run", _raise_oserror)

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_on_a_timed_out_download(self, monkeypatch) -> None:
        # Regression: a stalled gh connection must not block the calling
        # CI decision process indefinitely -- subprocess.TimeoutExpired
        # must convert to BaselineFetchError like every other failure mode.
        def _raise_timeout(args, **kwargs):
            raise correlation.subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 1))

        monkeypatch.setattr(correlation.subprocess, "run", _raise_timeout)

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER, timeout_s=1.0)

    def test_raises_on_malformed_json(self, tmp_path, monkeypatch) -> None:
        def _fake_run(args, **kwargs):
            dest_dir = Path(args[args.index("--dir") + 1])
            (dest_dir / "agent-x.json").write_text("not valid json {{{")

            class _Result:
                returncode = 0
                stdout = ""
                stderr = ""
            return _Result()

        monkeypatch.setattr(correlation.subprocess, "run", _fake_run)

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_on_non_utf8_content(self, tmp_path, monkeypatch) -> None:
        # Regression: invalid UTF-8 raises UnicodeDecodeError, which must
        # also be converted to BaselineFetchError, not bypass it.
        def _fake_run(args, **kwargs):
            dest_dir = Path(args[args.index("--dir") + 1])
            (dest_dir / "agent-x.json").write_bytes(b"\xff\xfe\x00\x01invalid-utf8")

            class _Result:
                returncode = 0
                stdout = ""
                stderr = ""
            return _Result()

        monkeypatch.setattr(correlation.subprocess, "run", _fake_run)

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_downloaded_json_is_not_an_object(self, monkeypatch) -> None:
        def _fake_run(args, **kwargs):
            dest_dir = Path(args[args.index("--dir") + 1])
            (dest_dir / "agent-x.json").write_text("[1, 2, 3]")

            class _Result:
                returncode = 0
                stdout = ""
                stderr = ""
            return _Result()

        monkeypatch.setattr(correlation.subprocess, "run", _fake_run)

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_generated_at_is_missing(self, monkeypatch) -> None:
        payload = {**self._VALID_PAYLOAD}
        del payload["generated_at"]
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_generated_at_is_not_a_parseable_timestamp(self, monkeypatch) -> None:
        # Regression: a syntactically-present but non-ISO8601 (or
        # non-string) generated_at must be rejected here, not crash
        # downstream in debt.assess_debt with a confusing error.
        payload = {**self._VALID_PAYLOAD, "generated_at": "not-a-timestamp"}
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_generated_at_is_timezone_naive(self, monkeypatch) -> None:
        # Regression: datetime.fromisoformat also accepts a naive timestamp
        # (e.g. a bare date, or a time with no offset) even though the
        # baseline contract writes an offset-aware UTC timestamp -- a naive
        # value would later be interpreted in whichever timezone the
        # CONSUMING host happens to run in, making coverage age
        # environment-dependent.
        payload = {**self._VALID_PAYLOAD, "generated_at": "2026-01-01T00:00:00"}  # no offset
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_coverage_or_tests_is_not_a_mapping(self, monkeypatch) -> None:
        # Regression: a missing/non-dict coverage map must be rejected
        # here -- not silently curated downstream as "empty, nothing
        # covered" (a fundamentally different, evidenced outcome).
        payload = {**self._VALID_PAYLOAD, "coverage": []}
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_a_coverage_entrys_per_line_value_is_not_a_mapping(self, monkeypatch) -> None:
        # Regression: a shallow-valid top-level coverage dict whose PER-FILE
        # entry is itself a list (not a line-number -> test-list mapping)
        # must also be rejected here -- not escape as a raw AttributeError
        # from `ancestor_resolution.remap_or_invalidate_baseline`'s own
        # `per_line.items()` call downstream.
        payload = {**self._VALID_PAYLOAD, "coverage": {"f.py": []}}
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_a_coverage_lines_test_list_is_malformed(self, monkeypatch) -> None:
        payload = {**self._VALID_PAYLOAD, "coverage": {"f.py": {"1": "not-a-list"}}}
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_a_tests_record_is_not_a_mapping(self, monkeypatch) -> None:
        # Regression: a non-mapping test record must also be rejected here
        # -- not escape as a raw AttributeError from
        # `fallback.compute_fallback_set`'s own `.get("duration_s")` call
        # downstream.
        payload = {**self._VALID_PAYLOAD, "tests": {"test_x": "not-a-dict"}}
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_downloaded_plugin_does_not_match_the_pointer(self, monkeypatch) -> None:
        # Regression: a syntactically valid baseline document whose OWN
        # plugin/measured_commit disagree with the pointer that named it
        # must never be trusted -- that would silently select against the
        # wrong generation.
        payload = {**self._VALID_PAYLOAD, "plugin": "agent-y"}  # mismatch
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)

    def test_raises_when_downloaded_measured_commit_does_not_match_the_pointer(self, monkeypatch) -> None:
        payload = {**self._VALID_PAYLOAD, "measured_commit": "different-sha"}  # mismatch
        monkeypatch.setattr(correlation.subprocess, "run", self._fake_run_writing(payload))

        with pytest.raises(correlation.BaselineFetchError):
            correlation.fetch_baseline_asset("owner/repo", self._POINTER)


class TestPlanChunks:
    """Fast, pure-function tests for `_plan_chunks` -- no real subprocess."""

    def test_small_suite_stays_a_single_unsplit_chunk(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        for i in range(3):
            (tests_dir / f"test_{i}.py").write_text("def test_x(): pass\n")

        chunks = baseline_mod._plan_chunks(tmp_path, "tests", max_files_per_chunk=25)

        # Exactly the original, unsplit `test_path` -- confirms collection
        # behavior for every suite within the limit is bit-for-bit
        # identical to before chunking existed.
        assert chunks == [["tests"]]

    def test_a_single_file_test_path_stays_a_single_chunk(self, tmp_path: Path) -> None:
        chunks = baseline_mod._plan_chunks(
            tmp_path, "tests/test_one.py", max_files_per_chunk=25
        )
        assert chunks == [["tests/test_one.py"]]

    def test_large_suite_splits_into_bounded_chunks(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        names = [f"test_{i:02d}.py" for i in range(7)]
        for name in names:
            (tests_dir / name).write_text("def test_x(): pass\n")

        chunks = baseline_mod._plan_chunks(tmp_path, "tests", max_files_per_chunk=3)

        assert [len(c) for c in chunks] == [3, 3, 1]
        # Every discovered file appears in exactly one chunk, and chunk
        # order matches sorted discovery order (stable, reproducible
        # chunking across runs).
        flattened = [path for chunk in chunks for path in chunk]
        assert flattened == sorted(f"tests/{name}" for name in names)

    def test_large_suite_includes_both_default_pytest_filename_patterns(
        self, tmp_path: Path
    ) -> None:
        # Regression test: pytest's own default collection matches BOTH
        # `test_*.py` and `*_test.py` -- a suite large enough to chunk must
        # not silently drop files matching the second pattern just because
        # it crossed the threshold (a suite below the threshold passes the
        # whole directory straight to pytest, which already finds both).
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        names = [f"test_{i:02d}.py" for i in range(6)] + ["extra_test.py"]
        for name in names:
            (tests_dir / name).write_text("def test_x(): pass\n")

        chunks = baseline_mod._plan_chunks(tmp_path, "tests", max_files_per_chunk=3)

        flattened = {path for chunk in chunks for path in chunk}
        assert flattened == {f"tests/{name}" for name in names}

    def test_large_suite_outside_cwd_keeps_absolute_paths(
        self, tmp_path: Path
    ) -> None:
        # Regression test: a test_path outside cwd entirely (e.g. a shared
        # test directory) must keep working once it's large enough to
        # chunk, not raise ValueError from an impossible relative_to(cwd)
        # -- a suite below the threshold passes the whole (possibly
        # absolute, possibly out-of-tree) test_path straight through
        # unmodified, so chunking must preserve that same tolerance.
        cwd = tmp_path / "repo"
        cwd.mkdir()
        external_tests = tmp_path / "shared-tests"
        external_tests.mkdir()
        names = [f"test_{i:02d}.py" for i in range(5)]
        for name in names:
            (external_tests / name).write_text("def test_x(): pass\n")

        chunks = baseline_mod._plan_chunks(
            cwd, str(external_tests), max_files_per_chunk=2
        )

        flattened = {path for chunk in chunks for path in chunk}
        assert flattened == {str(external_tests / name) for name in names}


class TestMergeChunkResults:
    """Fast, pure-function tests for `_merge_chunk_results` -- no real
    subprocess."""

    def test_durations_union_across_chunks(self) -> None:
        chunks = [
            {"durations": {"tests/test_a.py::test_1": 0.1}, "coverage": {}},
            {"durations": {"tests/test_b.py::test_2": 0.2}, "coverage": {}},
        ]
        merged = baseline_mod._merge_chunk_results(chunks)
        assert merged["durations"] == {
            "tests/test_a.py::test_1": 0.1,
            "tests/test_b.py::test_2": 0.2,
        }

    def test_coverage_lines_union_when_a_shared_file_spans_chunks(self) -> None:
        # A shared helper module touched by tests from two different
        # chunks must have both chunks' own attributed tests present on
        # the same line, not one silently clobbering the other.
        chunks = [
            {
                "durations": {},
                "coverage": {"src/helper.py": {"10": ["tests/test_a.py::test_1"]}},
            },
            {
                "durations": {},
                "coverage": {"src/helper.py": {"10": ["tests/test_b.py::test_2"]}},
            },
        ]
        merged = baseline_mod._merge_chunk_results(chunks)
        assert merged["coverage"]["src/helper.py"]["10"] == [
            "tests/test_a.py::test_1",
            "tests/test_b.py::test_2",
        ]


class TestBaselineCollectionErrorContract:
    """Fast, mocked tests for the two non-clean collection outcomes --
    neither spawns a real subprocess, so both run in the always-on
    synthetic lane alongside `TestSelectTests`/`TestComputeFallbackSet`."""

    def test_nonzero_exit_raises_baseline_collection_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_result = type(
            "FakeCompletedProcess",
            (),
            {"returncode": 1, "stdout": "1 failed", "stderr": ""},
        )()
        monkeypatch.setattr(
            baseline_mod.subprocess, "run", lambda *a, **k: fake_result
        )
        with pytest.raises(baseline_mod.BaselineCollectionError) as exc_info:
            baseline_mod.collect_baseline(
                cwd=tmp_path,
                test_path="tests",
                cov_source="src",
                plugin="mocked",
            )
        assert exc_info.value.returncode == 1
        assert "1 failed" in str(exc_info.value)

    def test_timeout_raises_baseline_collection_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import subprocess as subprocess_module

        def _raise_timeout(*args, **kwargs):
            raise subprocess_module.TimeoutExpired(cmd=["uv", "run"], timeout=1.0)

        monkeypatch.setattr(baseline_mod.subprocess, "run", _raise_timeout)
        with pytest.raises(baseline_mod.BaselineCollectionError) as exc_info:
            baseline_mod.collect_baseline(
                cwd=tmp_path,
                test_path="tests",
                cov_source="src",
                plugin="mocked",
                timeout_s=1.0,
            )
        assert "timed out after 1.0s" in str(exc_info.value)
        # The documented contract is specifically that callers only ever
        # need to catch `BaselineCollectionError`; confirm the raw
        # `TimeoutExpired` never escapes as the exception type itself.
        assert not isinstance(exc_info.value, subprocess_module.TimeoutExpired)

    def test_measured_commit_round_trips_into_the_baseline_dict(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Mocked, no real subprocess: the driver's own out-file argument is
        # the 6th of 7 positional args after the driver script path (see
        # _DRIVER_SCRIPT's own argv unpacking: test_paths, cov_source, cwd,
        # cov_data_file, json_report_file, out_file, basetemp), so a fake
        # "subprocess" just has to write valid merged JSON there and
        # report success.
        import json as json_module

        def _fake_run(args, **kwargs):
            out_file = Path(args[-2])
            out_file.write_text(
                json_module.dumps({"durations": {}, "coverage": {}})
            )
            return type(
                "FakeCompletedProcess",
                (),
                {"returncode": 0, "stdout": "", "stderr": ""},
            )()

        monkeypatch.setattr(baseline_mod.subprocess, "run", _fake_run)
        result = baseline_mod.collect_baseline(
            cwd=tmp_path,
            test_path="tests",
            cov_source="src",
            plugin="mocked",
            measured_commit="deadbeef",
        )
        assert result["measured_commit"] == "deadbeef"
        assert result["schema_version"] == baseline_mod.BASELINE_SCHEMA_VERSION

        # Omitting it entirely must still produce a valid (locally-usable)
        # baseline -- only `correlation.require_measured_commit` enforces
        # its presence, not `collect_baseline` itself.
        local_result = baseline_mod.collect_baseline(
            cwd=tmp_path,
            test_path="tests",
            cov_source="src",
            plugin="mocked",
        )
        assert local_result["measured_commit"] is None

    def test_subprocess_env_scrubs_ambient_containment_variables(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Baseline subprocesses must exclude the same ambient host
        # variables as the trusted runner, because a plugin under test may
        # consult them before a test's own monkeypatch takes effect.
        from tools.plugin_test_containment import ALWAYS_SCRUB_NAMES

        for name in ALWAYS_SCRUB_NAMES:
            monkeypatch.setenv(name, "ambient-leak-should-not-survive")
        monkeypatch.setenv("PYTHONPATH", "/some/stale/source/tree")
        env = baseline_mod._subprocess_env(
            tmp_path / ".coverage", tmp_path / "sandbox"
        )
        for name in ALWAYS_SCRUB_NAMES:
            assert name not in env, f"{name} should be scrubbed from the baseline subprocess env"
        assert env["COVERAGE_FILE"] == str(tmp_path / ".coverage")
        # An ambient PYTHONPATH must never take import precedence over the
        # repository's own `tools/` path, the same override
        # `run-plugin-tests.py` always applies.
        assert env["PYTHONPATH"] == str(_REPO_ROOT / "tools")


def test_collect_baseline_round_trips_against_a_real_plugin_suite() -> None:
    # Deliberately opt-in: spawns a real "uv run --with coverage ..."
    # subprocess against a real plugin's suite (seconds, network-dependent
    # package resolution), which doesn't belong in the fast, always-on PR
    # lane -- see this effort's own Journal and ci.yml's separate,
    # workflow_dispatch-only "coverage-guided-selection-integration" job.
    if os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1":
        pytest.skip(
            "opt-in only: set CGS_RUN_INTEGRATION_TEST=1 to run the real "
            "uv/coverage subprocess integration test"
        )
    # `collect_baseline`'s own `timeout_s` bounds the ephemeral subprocess;
    # no separate pytest-timeout dependency is needed for this test itself.
    plugin_dir = _REPO_ROOT / "plugins" / "ai-attribution"
    if not plugin_dir.is_dir():
        pytest.skip("ai-attribution plugin not present in this checkout")

    result = baseline_mod.collect_baseline(
        cwd=_REPO_ROOT,
        test_path="plugins/ai-attribution/tests",
        cov_source="plugins/ai-attribution/scripts",
        plugin="ai-attribution",
        timeout_s=120.0,
    )

    assert result["schema_version"] == baseline_mod.BASELINE_SCHEMA_VERSION
    assert result["plugin"] == "ai-attribution"
    assert len(result["tests"]) > 0, "expected at least one parsed test duration"
    assert all(v["duration_s"] >= 0 for v in result["tests"].values())

    covered_file = "plugins/ai-attribution/scripts/write_session_guidance.py"
    assert covered_file in result["coverage"], (
        "write_session_guidance.py is exercised in-process by "
        "test_write_session_guidance.py and must round-trip real "
        "coverage-context attribution"
    )
    per_line = result["coverage"][covered_file]
    assert per_line, "expected at least one attributed line"
    for tests in per_line.values():
        assert tests, "a present line key must never map to an empty test list"
        for test_id in tests:
            assert test_id in result["tests"], (
                f"selected test id {test_id!r} must also appear in the "
                "duration map collected from the same run"
            )

    # The round-tripped baseline must be directly usable by select/fallback
    # without any further transformation.
    selection = select.select_tests(result, {covered_file: [int(next(iter(per_line)))]})
    assert selection.selected_tests
    assert not selection.fallback_triggered

    fb = fallback.compute_fallback_set(result, runtime_budget_s=5.0)
    assert fb.covered_fraction > 0.0
    full_suite_cost = sum(v["duration_s"] for v in result["tests"].values())
    assert fb.total_runtime_s < full_suite_cost, (
        "the curated fallback set must cost less than running the full suite "
        "-- otherwise it isn't a fallback"
    )


def test_collect_baseline_attributes_fixture_setup_and_teardown_coverage(
    tmp_path: Path,
) -> None:
    # Regression test for the "run"-context-only bug: a line that only ever
    # executes during a test's fixture setup/teardown phase (never during
    # its "call" phase) must still be attributed to that test. Constructs a
    # throwaway module + suite rather than relying on an existing plugin's
    # tests, since this needs a line that is deliberately *only* reachable
    # from setup/teardown.
    if os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1":
        pytest.skip(
            "opt-in only: set CGS_RUN_INTEGRATION_TEST=1 to run the real "
            "uv/coverage subprocess integration test"
        )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("")
    (src_dir / "helper.py").write_text(
        "def setup_only_line():\n"
        "    return 'this line only ever runs during fixture setup'\n"
        "\n\n"
        "def teardown_only_line():\n"
        "    return 'this line only ever runs during fixture teardown'\n"
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_fixture_phases.py").write_text(
        "import sys\n"
        "sys.path.insert(0, str((__import__('pathlib').Path(__file__).parent.parent / 'src')))\n"
        "import pytest\n"
        "from helper import setup_only_line, teardown_only_line\n"
        "\n\n"
        "@pytest.fixture\n"
        "def resource():\n"
        "    setup_only_line()\n"
        "    yield object()\n"
        "    teardown_only_line()\n"
        "\n\n"
        "def test_uses_the_fixture(resource):\n"
        "    assert resource is not None\n"
    )

    result = baseline_mod.collect_baseline(
        cwd=tmp_path,
        test_path="tests",
        cov_source="src",
        plugin="fixture-phase-regression",
        timeout_s=60.0,
    )

    helper_file = "src/helper.py"
    assert helper_file in result["coverage"], "helper.py must be attributed at all"
    attributed_tests = {
        test_id
        for tests in result["coverage"][helper_file].values()
        for test_id in tests
    }
    assert any("test_uses_the_fixture" in t for t in attributed_tests), (
        "a line executed only during fixture setup/teardown must still be "
        f"attributed to the test using that fixture; got {attributed_tests!r}"
    )


def test_collect_baseline_with_project_dir_resolves_real_plugin_dependencies() -> None:
    # Regression/proof test for the Phase 1 pilot wiring (agent-ssh): a
    # plugin with real dependencies (including `[tool.uv.sources]` vendored
    # path deps) cannot be measured via the bare ephemeral `uv run --with`
    # venv the ai-attribution pilot used -- it needs `project_dir` to
    # install the plugin editable (with its vendored deps resolved) first.
    # Picks `agent-ssh` specifically because it is both small (16 test
    # files) and has real vendored path dependencies
    # (agent-ssh-manager/agent-procutil/agent-zdd/agent-dropin-registry),
    # so this proves the general case, not just a dependency-free plugin.
    if os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1":
        pytest.skip(
            "opt-in only: set CGS_RUN_INTEGRATION_TEST=1 to run the real "
            "uv/coverage subprocess integration test"
        )
    plugin_dir = _REPO_ROOT / "plugins" / "agent-ssh"
    if not plugin_dir.is_dir():
        pytest.skip("agent-ssh plugin not present in this checkout")

    result = baseline_mod.collect_baseline(
        cwd=_REPO_ROOT,
        test_path="plugins/agent-ssh/tests",
        cov_source="plugins/agent-ssh/src/agent_ssh",
        plugin="agent-ssh",
        project_dir=plugin_dir,
        timeout_s=180.0,
    )

    assert result["plugin"] == "agent-ssh"
    assert len(result["tests"]) > 0, "expected at least one parsed test duration"
    assert len(result["coverage"]) > 0, (
        "expected at least one attributed source file under "
        "plugins/agent-ssh/src/agent_ssh -- an empty coverage map would "
        "mean the editable install/vendored deps silently failed to "
        "resolve and the suite ran against nothing real"
    )
    for file_coverage in result["coverage"].values():
        for tests in file_coverage.values():
            for test_id in tests:
                assert test_id in result["tests"]


def test_collect_baseline_chunks_a_large_suite_and_merges_the_results(
    tmp_path: Path,
) -> None:
    # Real, opt-in end-to-end proof of the chunking fix this effort's
    # agent-mcp enrollment surfaced the need for: constructs a synthetic
    # suite larger than `max_files_per_chunk` (forced down to 2 here, so
    # this stays fast) with a module shared across every test file, and
    # confirms `collect_baseline` genuinely runs more than one pytest
    # process (not just one covering everything) yet still returns a
    # single, correctly merged baseline -- every test's own duration
    # present, and the shared module's coverage attributed to tests from
    # every chunk, not just whichever chunk happened to run first.
    if os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1":
        pytest.skip(
            "opt-in only: set CGS_RUN_INTEGRATION_TEST=1 to run the real "
            "uv/coverage subprocess integration test"
        )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("")
    (src_dir / "shared.py").write_text(
        "def shared_line():\n    return 'touched by every chunk'\n"
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_names = [f"test_chunk_{i}.py" for i in range(5)]
    for name in test_names:
        (tests_dir / name).write_text(
            "import sys\n"
            "sys.path.insert(0, str((__import__('pathlib').Path(__file__)."
            "parent.parent / 'src')))\n"
            "from shared import shared_line\n"
            "\n\n"
            f"def test_{name[:-3]}():\n"
            "    assert shared_line()\n"
        )

    result = baseline_mod.collect_baseline(
        cwd=tmp_path,
        test_path="tests",
        cov_source="src",
        plugin="chunking-regression",
        timeout_s=60.0,
        max_files_per_chunk=2,
    )

    assert len(result["tests"]) == len(test_names), (
        "every test across every chunk must round-trip into the merged "
        "duration map"
    )
    shared_file = "src/shared.py"
    assert shared_file in result["coverage"]
    attributed_tests = {
        test_id
        for tests in result["coverage"][shared_file].values()
        for test_id in tests
    }
    assert len(attributed_tests) == len(test_names), (
        "the shared module's coverage must be attributed to a test from "
        f"every chunk, not just one; got {sorted(attributed_tests)!r}"
    )


def test_collect_baseline_survives_a_real_spawn_based_multiprocessing_child(
    tmp_path: Path,
) -> None:
    # Regression test for a real failure this effort's agent-worktrees
    # enrollment surfaced: `_DRIVER_SCRIPT` used to call `pytest.main(...)`
    # unguarded at module scope. A plugin's own tests may spawn a real
    # child process via `multiprocessing.get_context("spawn")` (e.g. to
    # test genuine cross-process file-lock contention) -- `spawn`
    # bootstraps a fresh interpreter that re-imports the driver script as
    # a plain module (not `__main__`) to reconstruct its pickled target,
    # which re-executed `pytest.main(...)` unconditionally and tripped
    # multiprocessing's own bootstrap-safety guard ("An attempt has been
    # made to start a new process before the current process has finished
    # its bootstrapping phase"), killing the spawned child before it ever
    # ran its real target -- and the doomed re-execution's own
    # half-started pytest-cov instance corrupted the real coverage data
    # file the parent was still writing to. Constructs a throwaway suite
    # with a real `spawn`-context child (mirroring the real failure, not
    # just a synthetic unit test of the guard itself) to confirm the fix
    # holds end to end.
    if os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1":
        pytest.skip(
            "opt-in only: set CGS_RUN_INTEGRATION_TEST=1 to run the real "
            "uv/coverage subprocess integration test"
        )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_spawn_child.py").write_text(
        "import multiprocessing\n"
        "\n\n"
        "def _child_target(ready_path):\n"
        "    with open(ready_path, 'w', encoding='utf-8') as fh:\n"
        "        fh.write('ok')\n"
        "\n\n"
        "def test_real_spawn_child_completes(tmp_path):\n"
        "    ready = tmp_path / 'ready'\n"
        "    ctx = multiprocessing.get_context('spawn')\n"
        "    proc = ctx.Process(target=_child_target, args=(str(ready),))\n"
        "    proc.start()\n"
        "    proc.join(timeout=30)\n"
        "    assert proc.exitcode == 0, (\n"
        "        f'spawned child must exit cleanly, got {proc.exitcode}'\n"
        "    )\n"
        "    assert ready.read_text(encoding='utf-8') == 'ok'\n"
    )

    result = baseline_mod.collect_baseline(
        cwd=tmp_path,
        test_path="tests",
        cov_source="src",
        plugin="spawn-regression",
        timeout_s=60.0,
    )

    assert any(
        "test_real_spawn_child_completes" in nodeid for nodeid in result["tests"]
    )


def _run_git(args: list, cwd: Path) -> subprocess.CompletedProcess:
    # Scrub ambient Git repository-selection variables (GIT_DIR,
    # GIT_WORK_TREE, etc.) before layering on test author identity --
    # otherwise a runner/harness that happens to set one of these isolates
    # these "independent" throwaway test repos a lot less than their own
    # fresh `tmp_path` cwd implies, since such a variable silently overrides
    # `cwd` for every git invocation below. Matches the production
    # convention in `ancestor_resolution.scrubbed_git_env` /
    # `agent_worktrees.git_ops`.
    env = ar.scrubbed_git_env()
    env.update({
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    })
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=False,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(["init", "-q", "-b", "dev"], cwd=repo)
    _run_git(["config", "user.name", "Test"], cwd=repo)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo)
    return repo


def _commit(repo: Path, message: str) -> str:
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-q", "-m", message], cwd=repo)
    return _run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()


class TestIsAncestor:
    def test_true_for_a_real_ancestor(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("1\n2\n")
        c2 = _commit(repo, "second")
        assert ar.is_ancestor(repo, c1, c2) is True

    def test_false_for_a_non_ancestor(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")
        _run_git(["checkout", "-q", "-b", "side", c1], cwd=repo)
        (repo / "b.txt").write_text("x\n")
        c2 = _commit(repo, "side commit")
        _run_git(["checkout", "-q", "dev"], cwd=repo)
        (repo / "a.txt").write_text("1\n2\n")
        c3 = _commit(repo, "dev commit")
        assert ar.is_ancestor(repo, c2, c3) is False

    def test_a_commit_is_its_own_ancestor(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")
        assert ar.is_ancestor(repo, c1, c1) is True

    def test_raises_for_an_unreachable_commit(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")
        with pytest.raises(ar.AncestorResolutionError):
            ar.is_ancestor(repo, "0" * 40, c1)


class TestResolveNearestBaseline:
    def test_finds_the_newest_qualifying_generation(self, tmp_path):
        repo = _init_repo(tmp_path)
        # A sequence of dev-side commits to serve as measured_commit values
        # and as the fork point.
        (repo / "src.py").write_text("line1\n")
        dev_c1 = _commit(repo, "dev c1")
        (repo / "src.py").write_text("line1\nline2\n")
        dev_c2 = _commit(repo, "dev c2")
        (repo / "src.py").write_text("line1\nline2\nline3\n")
        dev_c3 = _commit(repo, "dev c3")

        # main branch carries 3 baseline generations, oldest to newest,
        # each measured against one of the dev commits above.
        _run_git(["checkout", "-q", "-b", "main"], cwd=repo)
        baseline_path = repo / ".github" / "coverage-baselines" / "myplugin.json"
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_text(json.dumps({"measured_commit": dev_c1, "coverage": {}}))
        _commit(repo, "baseline gen 1")
        baseline_path.write_text(json.dumps({"measured_commit": dev_c2, "coverage": {}}))
        _commit(repo, "baseline gen 2")
        baseline_path.write_text(json.dumps({"measured_commit": dev_c3, "coverage": {}}))
        gen3_commit = _commit(repo, "baseline gen 3")

        # A fork point between dev_c2 and dev_c3 (a PR branched before the
        # newest baseline generation was ever measured).
        _run_git(["checkout", "-q", "-b", "pr", dev_c2], cwd=repo)
        (repo / "pr_only.py").write_text("x\n")
        fork_commit = _commit(repo, "pr commit")

        resolved = ar.resolve_nearest_baseline(repo, "myplugin", fork_commit, main_ref="main")
        assert resolved is not None
        assert resolved.baseline["measured_commit"] == dev_c2
        assert resolved.baseline_commit != gen3_commit

    def test_returns_none_when_no_generation_qualifies(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "src.py").write_text("line1\n")
        dev_c1 = _commit(repo, "dev c1")

        # A real commit that exists in the repo but sits on a disjoint
        # side branch -- genuinely reachable (so merge-base can answer),
        # just not an ancestor of the fork point below.
        _run_git(["checkout", "-q", "-b", "unrelated", dev_c1], cwd=repo)
        (repo / "side.py").write_text("x\n")
        unrelated_commit = _commit(repo, "unrelated side commit")

        _run_git(["checkout", "-q", "-b", "main", dev_c1], cwd=repo)
        baseline_path = repo / ".github" / "coverage-baselines" / "myplugin.json"
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_text(
            json.dumps({"measured_commit": unrelated_commit, "coverage": {}})
        )
        _commit(repo, "baseline gen 1")

        _run_git(["checkout", "-q", "-b", "pr", dev_c1], cwd=repo)
        fork_commit = dev_c1

        resolved = ar.resolve_nearest_baseline(repo, "myplugin", fork_commit, main_ref="main")
        assert resolved is None

    def test_returns_none_when_baseline_file_never_existed(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "src.py").write_text("line1\n")
        c1 = _commit(repo, "only commit")
        resolved = ar.resolve_nearest_baseline(repo, "nope", c1, main_ref="dev")
        assert resolved is None

    def test_skips_a_generation_whose_document_is_not_an_object(self, tmp_path):
        # Regression: valid JSON that isn't a dict at all (e.g. a bare
        # list) must be skipped like a JSON-decode failure -- `.get()` on
        # it would otherwise raise AttributeError, crashing past this
        # function's own "raises only for a genuine plumbing failure"
        # contract.
        repo = _init_repo(tmp_path)
        (repo / "src.py").write_text("line1\n")
        dev_c1 = _commit(repo, "dev c1")

        _run_git(["checkout", "-q", "-b", "main"], cwd=repo)
        baseline_path = repo / ".github" / "coverage-baselines" / "myplugin.json"
        baseline_path.parent.mkdir(parents=True)
        # Older, genuinely valid generation.
        baseline_path.write_text(json.dumps({"measured_commit": dev_c1, "coverage": {}}))
        _commit(repo, "valid older generation")
        # Newest generation is malformed (a bare list, not an object).
        baseline_path.write_text(json.dumps([dev_c1]))
        _commit(repo, "malformed newest generation")

        _run_git(["checkout", "-q", "-b", "pr", dev_c1], cwd=repo)
        fork_commit = dev_c1

        resolved = ar.resolve_nearest_baseline(repo, "myplugin", fork_commit, main_ref="main")

        assert resolved is not None
        assert resolved.baseline["measured_commit"] == dev_c1

    def test_skips_a_generation_whose_measured_commit_is_not_a_string(self, tmp_path):
        # Regression: a numeric/list-valued measured_commit is truthy, so
        # the old `if not measured_commit: continue` check didn't catch it
        # -- it reached `is_ancestor`'s own `subprocess.run` call and
        # raised a raw TypeError there instead.
        repo = _init_repo(tmp_path)
        (repo / "src.py").write_text("line1\n")
        dev_c1 = _commit(repo, "dev c1")

        _run_git(["checkout", "-q", "-b", "main"], cwd=repo)
        baseline_path = repo / ".github" / "coverage-baselines" / "myplugin.json"
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_text(json.dumps({"measured_commit": dev_c1, "coverage": {}}))
        _commit(repo, "valid older generation")
        baseline_path.write_text(json.dumps({"measured_commit": 12345, "coverage": {}}))
        _commit(repo, "malformed newest generation")

        _run_git(["checkout", "-q", "-b", "pr", dev_c1], cwd=repo)
        fork_commit = dev_c1

        resolved = ar.resolve_nearest_baseline(repo, "myplugin", fork_commit, main_ref="main")

        assert resolved is not None
        assert resolved.baseline["measured_commit"] == dev_c1


class TestComputeFileRemap:
    def test_unchanged_file(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n2\n3\n")
        (repo / "b.txt").write_text("x\n")
        c1 = _commit(repo, "first")
        (repo / "b.txt").write_text("y\n")
        c2 = _commit(repo, "second")
        result = ar.compute_file_remap(repo, "a.txt", c1, c2)
        assert result.status == "unchanged"

    def test_pure_insertion_invalidates_lines_after_the_insertion_point(self, tmp_path):
        """A pure line-coordinate shift proves nothing about *execution* --
        inserted code can introduce new control flow (an early `return`,
        a new guard clause) that causes a test which used to reach a line
        to no longer reach it, even though the line number itself
        translates cleanly. So a preceding insertion must invalidate
        (return None), never silently remap."""
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n2\n3\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("1\nNEW\n2\n3\n")
        c2 = _commit(repo, "insert a line")
        result = ar.compute_file_remap(repo, "a.txt", c1, c2)
        assert result.status == "remapped"
        # old line 1 sits strictly before the insertion -- still safe.
        assert ar.remap_line(1, result.hunks) == 1
        # old lines 2/3 sit after the insertion -- conservatively dropped,
        # NOT remapped to their shifted positions (3/4), since nothing
        # about hunk lengths proves the insertion was execution-neutral.
        assert ar.remap_line(2, result.hunks) is None
        assert ar.remap_line(3, result.hunks) is None

    def test_control_flow_changing_insertion_before_a_covered_line_is_invalidated(
        self, tmp_path,
    ):
        """Inserting an early guard clause/return before a
        previously-covered line must not carry that line's old attribution
        forward, even though the line number itself maps cleanly to a new
        position."""
        repo = _init_repo(tmp_path)
        (repo / "f.py").write_text(
            "def handler(x):\n"
            "    do_setup()\n"
            "    return process(x)\n"  # old line 3 -- covered below
        )
        old_commit = _commit(repo, "baseline measured here")
        (repo / "f.py").write_text(
            "def handler(x):\n"
            "    do_setup()\n"
            "    if not x:\n"
            "        return None\n"  # a NEW early exit inserted above
            "    return process(x)\n"
        )
        fork_commit = _commit(repo, "insert an early-exit guard clause")

        baseline = {
            "measured_commit": old_commit,
            "coverage": {"f.py": {"3": ["test_handler_with_x"]}},
        }
        resolved = ar.ResolvedBaseline(baseline=baseline, baseline_commit=old_commit)
        result = ar.remap_or_invalidate_baseline(repo, resolved, fork_commit)

        # The old covered line's attribution must NOT survive as a
        # confident remap to the new line 5 -- the whole file is dropped
        # (its only covered line had nothing safely attributable left).
        assert "f.py" not in result["coverage"]
        assert result["remap_invalidated_files"] == ["f.py"]

    def test_pure_deletion_is_remapped_and_drops_removed_lines(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n2\n3\n4\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("1\n4\n")
        c2 = _commit(repo, "delete two lines")
        result = ar.compute_file_remap(repo, "a.txt", c1, c2)
        assert result.status == "remapped"
        assert ar.remap_line(1, result.hunks) == 1
        assert ar.remap_line(2, result.hunks) is None  # deleted
        assert ar.remap_line(3, result.hunks) is None  # deleted
        assert ar.remap_line(4, result.hunks) == 2

    def test_cumulative_shift_across_several_real_intervening_deletion_commits(
        self, tmp_path,
    ):
        """The Validation Plan's own required shape: baseline measured here
        -> several real, separate line-shifting commits -> fork point.
        `compute_file_remap` diffs directly between the two endpoints
        (never walking or applying each intervening commit one at a time),
        so this also confirms that approach produces the same cumulative
        result a step-by-step replay would. Uses deletions (not
        insertions) throughout: only a preceding deletion is safely
        remappable under the asymmetric insertion/deletion policy above."""
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n2\n3\n4\n5\n6\n7\n")
        baseline_commit = _commit(repo, "baseline measured here")

        # Commit 2: delete old line 2.
        (repo / "a.txt").write_text("1\n3\n4\n5\n6\n7\n")
        _commit(repo, "intervening commit 1: delete old line 2")

        # Commit 3: delete two more lines (old lines 3 and 4) -- a second,
        # independent real commit, not folded into commit 2's own diff.
        (repo / "a.txt").write_text("1\n5\n6\n7\n")
        _commit(repo, "intervening commit 2: delete two more lines")

        # Commit 4 (the fork point): delete what was originally old line 7.
        (repo / "a.txt").write_text("1\n5\n6\n")
        fork_commit = _commit(repo, "fork point: delete the old last line")

        result = ar.compute_file_remap(repo, "a.txt", baseline_commit, fork_commit)
        assert result.status == "remapped"

        # Hand-computed expected mapping from the baseline's old line
        # numbers (1-7) to the fork point's new line numbers, reflecting
        # the CUMULATIVE effect of all three intervening deletion commits
        # combined: old 1 -> new 1 ("1"); old lines 2-4 were each deleted
        # by one of the three commits -> None; old 5 -> new 2 ("5", 3
        # lines removed ahead of it); old 6 -> new 3; old 7 was deleted by
        # the fork-point commit itself -> None.
        assert ar.remap_line(1, result.hunks) == 1
        assert ar.remap_line(2, result.hunks) is None
        assert ar.remap_line(3, result.hunks) is None
        assert ar.remap_line(4, result.hunks) is None
        assert ar.remap_line(5, result.hunks) == 2
        assert ar.remap_line(6, result.hunks) == 3
        assert ar.remap_line(7, result.hunks) is None

    def test_content_replacement_is_invalid(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n2\n3\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("1\nCHANGED\n3\n")
        c2 = _commit(repo, "replace a line")
        result = ar.compute_file_remap(repo, "a.txt", c1, c2)
        assert result.status == "invalid"

    def test_mixed_insertion_and_replacement_is_invalid(self, tmp_path):
        """A file can have one hunk that's a pure insertion and another
        that's a real replacement -- the whole file must still invalidate,
        not just the replaced hunk's range."""
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("1\nNEW\n2\n3\n4\n5\n6\n7\nCHANGED\n9\n10\n")
        c2 = _commit(repo, "insert then replace")
        result = ar.compute_file_remap(repo, "a.txt", c1, c2)
        assert result.status == "invalid"

    def test_binary_file_change_with_no_parsed_hunks_is_invalid(self, tmp_path):
        """A nonempty diff isn't always textual: a binary file change
        produces `Binary files ... differ` with no `@@` hunks at all.
        Treating "no hunks parsed" the same as "unchanged" would silently
        carry every old attribution forward across a real, unparsed
        change -- this must invalidate instead."""
        repo = _init_repo(tmp_path)
        (repo / "a.bin").write_bytes(b"\x00\x01\x02")
        c1 = _commit(repo, "first")
        (repo / "a.bin").write_bytes(b"\xff\xfe\xfd")
        c2 = _commit(repo, "change binary content")
        result = ar.compute_file_remap(repo, "a.bin", c1, c2)
        assert result.status == "invalid"


class TestRemapOrInvalidateBaseline:
    def test_full_integration_across_three_files(self, tmp_path):
        """One untouched file, one cleanly-shiftable (deletion-only) file,
        one content-replaced file -- in the same remap pass."""
        repo = _init_repo(tmp_path)
        (repo / "unchanged.py").write_text("a\nb\n")
        (repo / "shifted.py").write_text("1\n2\n3\n")
        (repo / "replaced.py").write_text("x\ny\nz\n")
        old_commit = _commit(repo, "baseline measured here")

        (repo / "shifted.py").write_text("2\n3\n")  # delete old line 1
        (repo / "replaced.py").write_text("x\nCHANGED\nz\n")
        fork_commit = _commit(repo, "fork point")

        baseline = {
            "measured_commit": old_commit,
            "coverage": {
                "unchanged.py": {"1": ["test_u"]},
                "shifted.py": {"2": ["test_s"], "3": ["test_s2"]},
                "replaced.py": {"2": ["test_r"]},
            },
        }
        resolved = ar.ResolvedBaseline(baseline=baseline, baseline_commit=old_commit)
        result = ar.remap_or_invalidate_baseline(repo, resolved, fork_commit)

        assert result["coverage"]["unchanged.py"] == {"1": ["test_u"]}
        assert result["coverage"]["shifted.py"] == {"1": ["test_s"], "2": ["test_s2"]}
        assert "replaced.py" not in result["coverage"]
        assert result["remap_invalidated_files"] == ["replaced.py"]
        assert result["remapped_to_commit"] == fork_commit
        assert result["measured_commit"] == old_commit  # provenance preserved

    def test_a_file_whose_only_covered_lines_were_deleted_is_invalidated(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "f.py").write_text("1\n2\n3\n")
        old_commit = _commit(repo, "baseline measured here")
        (repo / "f.py").write_text("1\n3\n")  # deletes line 2
        fork_commit = _commit(repo, "delete the only covered line")

        baseline = {
            "measured_commit": old_commit,
            "coverage": {"f.py": {"2": ["only_test"]}},
        }
        resolved = ar.ResolvedBaseline(baseline=baseline, baseline_commit=old_commit)
        result = ar.remap_or_invalidate_baseline(repo, resolved, fork_commit)

        assert "f.py" not in result["coverage"]
        assert result["remap_invalidated_files"] == ["f.py"]

    def test_does_not_mutate_the_input_baseline(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "f.py").write_text("1\n2\n")
        old_commit = _commit(repo, "c1")
        fork_commit = old_commit  # no changes at all

        baseline = {"measured_commit": old_commit, "coverage": {"f.py": {"1": ["t"]}}}
        resolved = ar.ResolvedBaseline(baseline=baseline, baseline_commit=old_commit)
        ar.remap_or_invalidate_baseline(repo, resolved, fork_commit)

        assert baseline == {"measured_commit": old_commit, "coverage": {"f.py": {"1": ["t"]}}}


class TestAssessDebt:
    _GENERATED_AT = "2026-01-01T00:00:00+00:00"

    def test_zero_debt_when_head_equals_measured_commit(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")

        result = debt.assess_debt(repo, c1, self._GENERATED_AT, head=c1)

        assert result.commit_volume == 0
        assert result.exceeded is False

    def test_commit_volume_counts_commits_since_measured_commit(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("2\n")
        _commit(repo, "second")
        (repo / "a.txt").write_text("3\n")
        c3 = _commit(repo, "third")

        result = debt.assess_debt(repo, c1, self._GENERATED_AT, head=c3)

        assert result.commit_volume == 2

    def test_exceeded_when_commit_volume_crosses_threshold(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("2\n")
        c2 = _commit(repo, "second")

        under = debt.assess_debt(
            repo, c1, self._GENERATED_AT, head=c2, commit_volume_threshold=5
        )
        over = debt.assess_debt(
            repo, c1, self._GENERATED_AT, head=c2, commit_volume_threshold=0
        )

        assert under.exceeded is False
        assert over.exceeded is True
        assert "commit_volume" in over.reasons[0]

    def test_exceeded_when_age_crosses_threshold(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")

        # `now` is injected explicitly rather than sleeping a real wall-clock
        # interval -- deterministic and fast.
        generated_at_s = debt.datetime.fromisoformat(self._GENERATED_AT).timestamp()

        under = debt.assess_debt(
            repo, c1, self._GENERATED_AT, head=c1,
            age_threshold_seconds=3600, now=generated_at_s + 10,
        )
        over = debt.assess_debt(
            repo, c1, self._GENERATED_AT, head=c1,
            age_threshold_seconds=3600, now=generated_at_s + 7200,
        )

        assert under.exceeded is False
        assert over.exceeded is True
        assert "age_seconds" in over.reasons[0]

    def test_age_is_anchored_to_generated_at_not_the_commits_own_timestamp(self, tmp_path):
        # Regression: a commit can sit for days before CI finally collects
        # coverage against it. Age must reflect when the baseline was
        # EARNED (`generated_at`), not the commit's own, potentially much
        # older or newer, commit time -- otherwise a freshly-collected
        # baseline would report a stale (or falsely fresh) age driven by
        # the commit's timestamp instead of when collection actually ran.
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")

        fresh_generated_at = "2026-06-01T00:00:00+00:00"
        now = debt.datetime.fromisoformat(fresh_generated_at).timestamp() + 5

        result = debt.assess_debt(
            repo, c1, fresh_generated_at, head=c1,
            age_threshold_seconds=3600, now=now,
        )

        assert result.exceeded is False
        assert result.age_seconds < 3600
        assert abs(result.age_seconds - 5) < 1e-3

    def test_recollecting_against_the_same_old_commit_resets_age(self, tmp_path):
        # Regression: re-running coverage collection against the SAME
        # commit must reset reported age to near-zero -- a stale baseline for an unchanged commit must become
        # fresh again once re-collected, which anchoring age to the
        # commit's own timestamp could never allow.
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")

        first_collection_at = "2026-01-01T00:00:00+00:00"
        recollection_at = "2026-06-01T00:00:00+00:00"
        now = debt.datetime.fromisoformat(recollection_at).timestamp()

        stale = debt.assess_debt(
            repo, c1, first_collection_at, head=c1,
            age_threshold_seconds=3600, now=now,
        )
        fresh = debt.assess_debt(
            repo, c1, recollection_at, head=c1,
            age_threshold_seconds=3600, now=now,
        )

        assert stale.exceeded is True
        assert fresh.exceeded is False
        assert fresh.age_seconds < stale.age_seconds

    def test_neither_threshold_configured_never_exceeds(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")
        (repo / "a.txt").write_text("2\n")
        c2 = _commit(repo, "second")

        result = debt.assess_debt(repo, c1, self._GENERATED_AT, head=c2)

        assert result.exceeded is False
        assert result.reasons == ()
        # Both dimensions are still measured/reported for observability even
        # though neither is enforced.
        assert result.commit_volume == 1
        assert result.age_seconds >= 0.0

    def test_as_dict_round_trips_through_json(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        c1 = _commit(repo, "first")

        result = debt.assess_debt(
            repo, c1, self._GENERATED_AT, head=c1, commit_volume_threshold=10
        )
        json.dumps(result.as_dict())  # must not raise

    def test_raises_for_an_unreachable_measured_commit(self, tmp_path):
        repo = _init_repo(tmp_path)
        (repo / "a.txt").write_text("1\n")
        _commit(repo, "first")

        with pytest.raises(debt.CoverageDebtError):
            debt.assess_debt(repo, "0" * 40, self._GENERATED_AT)


class TestDecide:
    """`decide()` orchestrates every other module via its own imported
    names, so each collaborator is monkeypatched directly on `decide_mod`
    rather than re-exercised here (each already has its own dedicated test
    class above)."""

    _FULL_BASELINE = {
        "measured_commit": "m1", "generated_at": "2026-01-01T00:00:00+00:00",
        "coverage": {}, "tests": {},
    }
    # A deliberately DIFFERENT object from _FULL_BASELINE so a test can
    # prove which one `compute_fallback_set` actually received.
    _REMAPPED_BASELINE = {
        "measured_commit": "m1", "generated_at": "2026-01-01T00:00:00+00:00",
        "coverage": {"invalidated-away": "marker"}, "tests": {},
    }

    def _resolved(self, measured_commit="m1", baseline_commit="b1", plugin="plugin"):
        return ar.ResolvedBaseline(
            baseline={
                "measured_commit": measured_commit, "release_tag": "t", "asset": "a.json",
                "plugin": plugin,
            },
            baseline_commit=baseline_commit,
        )

    def test_no_baseline_resolved_falls_back_with_no_evidence(self, tmp_path, monkeypatch):
        monkeypatch.setattr(decide_mod, "resolve_nearest_baseline", lambda *a, **k: None)

        def _boom(*_a, **_k):
            raise AssertionError("must not fetch/assess/select without a resolved baseline")

        monkeypatch.setattr(decide_mod, "fetch_baseline_asset", _boom)

        result = decide_mod.decide(tmp_path, "owner/repo", "plugin", "fork", {})

        assert result.mode == "fallback"
        assert result.reason == decide_mod.NO_BASELINE_AVAILABLE
        assert result.baseline_generation is None
        # None (not ()): no curated evidence exists at all -- the caller
        # must run its own full/default suite, never interpret this as
        # "run nothing".
        assert result.selected_tests is None

    def test_pointer_plugin_mismatch_falls_back_without_fetching(self, tmp_path, monkeypatch):
        # Regression: fetch_baseline_asset only proves the downloaded asset
        # agrees with the POINTER -- it can't know which plugin the caller
        # actually asked about. A misplaced/corrupt pointer resolved for
        # plugin B while the caller asked about plugin A must never be
        # fetched/trusted, even if B's own asset is internally consistent.
        monkeypatch.setattr(
            decide_mod, "resolve_nearest_baseline",
            lambda *a, **k: self._resolved(plugin="other-plugin"),
        )

        def _boom(*_a, **_k):
            raise AssertionError("must not fetch an asset for a mismatched pointer")

        monkeypatch.setattr(decide_mod, "fetch_baseline_asset", _boom)

        result = decide_mod.decide(tmp_path, "owner/repo", "plugin", "fork", {})

        assert result.mode == "fallback"
        assert result.reason.startswith(decide_mod.POINTER_PLUGIN_MISMATCH_PREFIX)
        assert result.baseline_generation == "m1"
        assert result.selected_tests is None

    def test_fetch_failure_falls_back_with_the_error_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(decide_mod, "resolve_nearest_baseline", lambda *a, **k: self._resolved())

        def _fail(*_a, **_k):
            raise decide_mod.BaselineFetchError("boom")

        monkeypatch.setattr(decide_mod, "fetch_baseline_asset", _fail)

        result = decide_mod.decide(tmp_path, "owner/repo", "plugin", "fork", {})

        assert result.mode == "fallback"
        assert result.reason.startswith(decide_mod.FETCH_FAILED_PREFIX)
        assert result.baseline_generation == "m1"
        assert result.baseline_commit_on_main == "b1"
        assert result.selected_tests is None

    def test_debt_exceeded_falls_back_to_the_curated_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(decide_mod, "resolve_nearest_baseline", lambda *a, **k: self._resolved())
        monkeypatch.setattr(decide_mod, "fetch_baseline_asset", lambda *a, **k: self._FULL_BASELINE)

        def _boom_remap(*_a, **_k):
            raise AssertionError("must not remap/invalidate once debt already trips fallback")

        monkeypatch.setattr(decide_mod, "remap_or_invalidate_baseline", _boom_remap)
        over_debt = debt.DebtAssessment(
            commit_volume=100, age_seconds=1.0,
            commit_volume_threshold=5, age_threshold_seconds=None,
            exceeded=True, reasons=("commit_volume 100 exceeds threshold 5",),
        )
        monkeypatch.setattr(decide_mod, "assess_debt", lambda *a, **k: over_debt)
        curated = fallback.FallbackSet(selected_tests=("test_smoke",), total_runtime_s=1.0, covered_fraction=0.5, universe_size=2)
        captured_baseline = {}

        def _fake_compute_fallback_set(baseline, *a, **k):
            captured_baseline["value"] = baseline
            return curated

        monkeypatch.setattr(decide_mod, "compute_fallback_set", _fake_compute_fallback_set)

        def _boom_select(*_a, **_k):
            raise AssertionError("must not run diff-scoped selection once debt already trips fallback")

        monkeypatch.setattr(decide_mod, "select_tests", _boom_select)

        result = decide_mod.decide(tmp_path, "owner/repo", "plugin", "fork", {}, commit_volume_threshold=5)

        assert result.mode == "fallback"
        assert result.reason.startswith(decide_mod.COVERAGE_DEBT_PREFIX)
        assert result.selected_tests == ("test_smoke",)
        assert result.debt == over_debt.as_dict()
        assert result.fallback_set == curated.as_dict()
        # Curated from the FULL earned baseline, never a remapped one --
        # remap wasn't even called here.
        assert captured_baseline["value"] is self._FULL_BASELINE

    def test_selection_fallback_trigger_curates_from_the_full_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(decide_mod, "resolve_nearest_baseline", lambda *a, **k: self._resolved())
        monkeypatch.setattr(decide_mod, "fetch_baseline_asset", lambda *a, **k: self._FULL_BASELINE)
        monkeypatch.setattr(decide_mod, "remap_or_invalidate_baseline", lambda *a, **k: self._REMAPPED_BASELINE)
        clean_debt = debt.DebtAssessment(
            commit_volume=1, age_seconds=1.0,
            commit_volume_threshold=None, age_threshold_seconds=None,
            exceeded=False, reasons=(),
        )
        monkeypatch.setattr(decide_mod, "assess_debt", lambda *a, **k: clean_debt)
        triggered = select.SelectionResult(
            selected_tests=(), fallback_triggered=True,
            fallback_reasons=(select.FallbackReason("f.py", 3, "no_baseline_entry"),),
        )

        def _fake_select_tests(baseline, changed_lines):
            # select_tests DOES get the remapped baseline -- it needs
            # fork-commit-relative line coordinates.
            assert baseline is self._REMAPPED_BASELINE
            return triggered

        monkeypatch.setattr(decide_mod, "select_tests", _fake_select_tests)
        curated = fallback.FallbackSet(selected_tests=("test_smoke",), total_runtime_s=1.0, covered_fraction=0.5, universe_size=2)
        captured_baseline = {}

        def _fake_compute_fallback_set(baseline, *a, **k):
            captured_baseline["value"] = baseline
            return curated

        monkeypatch.setattr(decide_mod, "compute_fallback_set", _fake_compute_fallback_set)

        result = decide_mod.decide(tmp_path, "owner/repo", "plugin", "fork", {"f.py": [3]})

        assert result.mode == "fallback"
        assert result.reason.startswith(decide_mod.SELECTION_FALLBACK_PREFIX)
        assert result.selection_fallback_reasons == ({"file": "f.py", "line": 3, "reason": "no_baseline_entry"},)
        assert result.selected_tests == ("test_smoke",)
        # Curated from the FULL earned baseline, never the remapped one
        # that just dropped this diff's own touched-file coverage.
        assert captured_baseline["value"] is self._FULL_BASELINE

    def test_selection_fallback_unions_real_selected_tests_with_the_curated_set(self, tmp_path, monkeypatch):
        # Regression: a mixed diff can have SOME changed lines genuinely
        # attributed (select_tests returns real selected_tests for those)
        # while others trip the fallback trigger. The fallback must only
        # ever ADD safety-net coverage for the unattributed lines, never
        # silently drop already-earned coverage evidence for the
        # attributed ones.
        monkeypatch.setattr(decide_mod, "resolve_nearest_baseline", lambda *a, **k: self._resolved())
        monkeypatch.setattr(decide_mod, "fetch_baseline_asset", lambda *a, **k: self._FULL_BASELINE)
        monkeypatch.setattr(decide_mod, "remap_or_invalidate_baseline", lambda *a, **k: self._REMAPPED_BASELINE)
        clean_debt = debt.DebtAssessment(
            commit_volume=1, age_seconds=1.0,
            commit_volume_threshold=None, age_threshold_seconds=None,
            exceeded=False, reasons=(),
        )
        monkeypatch.setattr(decide_mod, "assess_debt", lambda *a, **k: clean_debt)
        mixed = select.SelectionResult(
            selected_tests=("test_attributed",), fallback_triggered=True,
            fallback_reasons=(select.FallbackReason("g.py", 7, "no_baseline_entry"),),
        )
        monkeypatch.setattr(decide_mod, "select_tests", lambda *a, **k: mixed)
        curated = fallback.FallbackSet(
            selected_tests=("test_smoke", "test_attributed"),  # overlap is fine, union dedupes
            total_runtime_s=1.0, covered_fraction=0.5, universe_size=2,
        )
        monkeypatch.setattr(decide_mod, "compute_fallback_set", lambda *a, **k: curated)

        result = decide_mod.decide(
            tmp_path, "owner/repo", "plugin", "fork", {"f.py": [3], "g.py": [7]},
        )

        assert result.mode == "fallback"
        assert set(result.selected_tests) == {"test_attributed", "test_smoke"}

    def test_clean_selection_returns_the_selected_tests(self, tmp_path, monkeypatch):
        monkeypatch.setattr(decide_mod, "resolve_nearest_baseline", lambda *a, **k: self._resolved())
        monkeypatch.setattr(decide_mod, "fetch_baseline_asset", lambda *a, **k: self._FULL_BASELINE)
        monkeypatch.setattr(decide_mod, "remap_or_invalidate_baseline", lambda *a, **k: self._REMAPPED_BASELINE)
        clean_debt = debt.DebtAssessment(
            commit_volume=1, age_seconds=1.0,
            commit_volume_threshold=None, age_threshold_seconds=None,
            exceeded=False, reasons=(),
        )
        monkeypatch.setattr(decide_mod, "assess_debt", lambda *a, **k: clean_debt)
        clean_selection = select.SelectionResult(
            selected_tests=("test_a", "test_b"), fallback_triggered=False,
        )
        monkeypatch.setattr(decide_mod, "select_tests", lambda *a, **k: clean_selection)

        def _boom(*_a, **_k):
            raise AssertionError("must not curate a fallback set on a clean selection")

        monkeypatch.setattr(decide_mod, "compute_fallback_set", _boom)

        result = decide_mod.decide(tmp_path, "owner/repo", "plugin", "fork", {"f.py": [1]})

        assert result.mode == "selected"
        assert result.reason == decide_mod.FRESH_SELECTION
        assert result.selected_tests == ("test_a", "test_b")
        assert result.baseline_generation == "m1"
        assert result.fallback_set is None

    def test_as_dict_round_trips_through_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(decide_mod, "resolve_nearest_baseline", lambda *a, **k: None)

        result = decide_mod.decide(tmp_path, "owner/repo", "plugin", "fork", {})

        json.dumps(result.as_dict())  # must not raise





