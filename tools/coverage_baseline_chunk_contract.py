"""Contracts for bounded, opt-in coverage chunk concurrency."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from threading import Event, Lock

import pytest

from tools import plugin_test_containment as containment
from tools.coverage_guided_selection import baseline
from tools.coverage_guided_selection import baseline_chunks as chunks


def test_coverage_collection_is_independent_of_source_promotion() -> None:
    import yaml

    repo = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (repo / ".github/workflows/validate-and-promote.yml").read_text(encoding="utf-8"),
    )
    ci = yaml.safe_load((repo / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert "source-units" in workflow["jobs"]
    assert "unit-plan" in ci["jobs"] and "unit-tests" in ci["jobs"]
    for jobs in (workflow["jobs"], ci["jobs"]):
        assert "full" not in jobs
        steps = [step for job in jobs.values() for step in job.get("steps", [])]
        assert not any(step.get("name", "").startswith(("Coverage baseline", "Upload coverage"))
                       for step in steps)
        assert all("coverage_guided_selection/standalone.py" not in step.get("run", "")
                   and "--workers \"$BASELINE_WORKERS\"" not in step.get("run", "")
                   for step in steps)


def _spec(tmp_path: Path, index: int = 0) -> chunks.Chunk:
    return chunks.Chunk(
        index=index, total=6, command=["driver", '["tests"]', "src", str(tmp_path),
                                     "coverage", "report", str(tmp_path / "out.json"), "pt"],
        cwd=str(tmp_path), sandbox=str(tmp_path / f"s{index}"),
        coverage_file=str(tmp_path / f"coverage{index}"),
        output_file=str(tmp_path / "out.json"), timeout_s=300.0,
    )


def test_two_workers_overlap_without_reordering_or_queuing_extra_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    second_started = Event()
    lock = Lock()
    active = peak = 0
    seen = []

    def run(spec: chunks.Chunk) -> dict:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(spec.index)
        if spec.index == 0:
            assert second_started.wait(2), "second chunk must overlap the first"
        elif spec.index == 1:
            second_started.set()
        with lock:
            active -= 1
        return {"index": spec.index}

    monkeypatch.setattr(chunks, "run_chunk", run)
    assert chunks.run_parallel([_spec(tmp_path, i) for i in range(6)]) == [
        {"index": i} for i in range(6)
    ]
    assert peak == 2 and active == 0
    assert sorted(seen) == list(range(6))


def test_failure_drains_owned_work_without_starting_later_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    second_started, first_failed = Event(), Event()
    seen = []
    error = chunks.BaselineCollectionError(2, "failed-test", "diagnostic")

    def run(spec: chunks.Chunk) -> dict:
        seen.append(spec.index)
        if spec.index == 0:
            assert second_started.wait(2)
            first_failed.set()
            raise error
        second_started.set()
        assert first_failed.wait(2)
        # The running sibling may finish, but cannot authorize more work.
        return {"index": spec.index}

    monkeypatch.setattr(chunks, "run_chunk", run)
    with pytest.raises(chunks.BaselineCollectionError) as caught:
        chunks.run_parallel([_spec(tmp_path, i) for i in range(6)])
    assert caught.value is error
    assert sorted(seen) == [0, 1]


@pytest.mark.parametrize("workers", [1, 2])
def test_collector_keeps_all_paths_and_isolates_parallel_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workers: int,
) -> None:
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    for i in range(6):
        (test_dir / f"test_{i}.py").write_text("def test_x(): pass\n")
    observed = []

    def run(spec: chunks.Chunk) -> dict:
        observed.append(spec)
        assert spec.timeout_s == 300
        paths = json.loads(spec.command[-7])
        return {
            "durations": {path: 0.1 for path in paths},
            "tiers": {path: "T1" for path in paths},
            "coverage": {"src/shared.py": {"1": paths}},
        }

    monkeypatch.setattr(baseline, "run_chunk", run)
    monkeypatch.setattr(baseline, "collect_contained", lambda specs, tmp: [run(s) for s in specs])
    result = baseline.collect_baseline(
        cwd=tmp_path, test_path="tests", cov_source="src", plugin="fixture",
        workers=workers, max_files_per_chunk=2, measured_commit="exact-source",
    )
    assert len(observed) == 3
    for field in ("coverage_file", "output_file"):
        assert len({getattr(s, field) for s in observed}) == 3
    assert len({s.sandbox for s in observed}) == (1 if workers == 1 else 3)
    assert len({s.command[-1] for s in observed}) == 3
    assert len(result["tests"]) == 6
    assert result["coverage"]["src/shared.py"]["1"] == sorted(result["tests"])
    assert result["measured_commit"] == "exact-source"
    assert all(t["portfolio_tier"] == "T1" for t in result["tests"].values())


@pytest.mark.parametrize("returncode", [0, 124])
def test_workers_share_one_unchanged_containment_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int,
) -> None:
    calls = []

    def contain(command: list[str], **kwargs: object) -> int:
        calls.append(command)
        assert kwargs["sandbox"] == tmp_path
        assert kwargs["limits"] == containment.Limits(
            wall_seconds=900, max_processes=128, max_memory_mb=4096, max_temp_mb=2048,
        )
        manifest, output = map(Path, command[2:4])
        specs = json.loads(manifest.read_text(encoding="utf-8"))
        assert len(specs) == 6
        assert all("env" not in spec for spec in specs)
        output.write_text(json.dumps([{"index": s["index"]} for s in specs]))
        return returncode

    monkeypatch.setattr(containment, "run_contained", contain)
    specs = [_spec(tmp_path, i) for i in range(6)]
    if returncode:
        with pytest.raises(chunks.BaselineCollectionError) as caught:
            chunks.collect_contained(specs, tmp_path)
        assert caught.value.returncode == 124
    else:
        assert chunks.collect_contained(specs, tmp_path) == [{"index": i} for i in range(6)]
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "missing", "invalid"])
def test_chunk_refuses_incomplete_evidence_and_keeps_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert kwargs["timeout"] == 300
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 300, output=b"running", stderr=b"timeout")
        if failure == "invalid":
            (tmp_path / "out.json").write_text("{}")
        return subprocess.CompletedProcess(
            command, 1 if failure == "nonzero" else 0, "test-output", "driver-diagnostic",
        )

    monkeypatch.setattr(chunks.subprocess, "run", run)
    with pytest.raises(chunks.BaselineCollectionError) as caught:
        chunks.run_chunk(_spec(tmp_path))
    assert 'chunk 1/6 ["tests"]' in str(caught.value)
    if failure == "timeout":
        assert "running" in str(caught.value) and "timed out after 300" in str(caught.value)
    else:
        assert "test-output" in str(caught.value) and "driver-diagnostic" in str(caught.value)


@pytest.mark.parametrize("workers", [0, 3, True, "2", 1.5])
def test_invalid_worker_count_refuses_before_launch(tmp_path: Path, workers: object) -> None:
    with pytest.raises(ValueError, match="workers must be"):
        baseline.collect_baseline(
            cwd=tmp_path, test_path="tests", cov_source="src", plugin="fixture", workers=workers,
        )


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1", reason="opt-in real coverage drivers",
)
def test_real_parallel_matches_serial_contexts_tiers_and_fixture_phases(tmp_path: Path) -> None:
    source, tests = tmp_path / "src", tmp_path / "tests"
    source.mkdir()
    tests.mkdir()
    (source / "shared.py").write_text(
        "def setup_line():\n    return 'setup'\n"
        "def call_line():\n    return 'call'\n"
        "def teardown_line():\n    return 'teardown'\n",
    )
    (tmp_path / ".coveragerc").write_text(
        "[run]\nparallel = true\ndynamic_context = test_function\n",
    )
    (tests / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\nimport pytest\n"
        "sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n"
        "import shared\n"
        "@pytest.fixture\n"
        "def resource():\n    shared.setup_line()\n    yield\n    shared.teardown_line()\n",
    )
    for i in range(6):
        (tests / f"test_{i}.py").write_text(
            "import pytest\nimport shared\n"
            "@pytest.mark.portfolio_tier('T1')\n"
            f"def test_{i}(resource):\n    assert shared.call_line() == 'call'\n",
        )
    results = [
        baseline.collect_baseline(
            cwd=tmp_path, test_path="tests", cov_source="src", plugin="fixture",
            workers=workers, max_files_per_chunk=2, measured_commit="exact-source",
        )
        for workers in (1, 2)
    ]
    serial, parallel = results
    assert parallel["coverage"] == serial["coverage"]
    assert set(parallel["tests"]) == set(serial["tests"])
    assert len(parallel["tests"]) == 6
    for line in ("2", "4", "6"):
        assert set(parallel["coverage"]["src/shared.py"][line]) == set(parallel["tests"])
    assert all(t["portfolio_tier"] == "T1" for t in parallel["tests"].values())
    assert parallel["measured_commit"] == serial["measured_commit"] == "exact-source"


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1", reason="opt-in real coverage drivers",
)
def test_real_parallel_failure_cannot_write_a_baseline(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    for i in range(3):
        (tests / f"test_{i}.py").write_text(f"def test_{i}(): assert {i} != 1\n")
    output = tmp_path / "baseline.json"
    with pytest.raises(chunks.BaselineCollectionError, match="failed"):
        baseline.main([
            str(tests), "--cwd", str(tmp_path), "--cov-source", "tests",
            "--plugin", "fixture", "--workers", "2", "--max-files-per-chunk", "1",
            "--out", str(output),
        ])
    assert not output.exists()
