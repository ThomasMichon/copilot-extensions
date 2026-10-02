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
        # the 6th positional arg after the driver script path (see
        # _DRIVER_SCRIPT's own argv unpacking), so a fake "subprocess" just
        # has to write valid merged JSON there and report success.
        import json as json_module

        def _fake_run(args, **kwargs):
            out_file = Path(args[-1])
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
