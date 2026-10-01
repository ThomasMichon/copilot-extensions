"""Collect a portable coverage baseline from a real pytest run.

Spawns an ephemeral `uv run --with coverage --with pytest-cov --with
pytest-json-report` subprocess so baseline collection needs no ambient
dependency beyond `uv` itself -- the same technique validated directly
against a downstream consumer repository's own small test suite during
this effort's originating low-risk spike (see this effort's own 2026-10-01
Journal entry).

The resulting baseline is deliberately pure JSON (`BASELINE_SCHEMA_VERSION`):
no live `coverage.py` database is carried past collection, so `select` and
`fallback` stay pure-stdlib and have nothing upstream to go stale against
except the baseline file itself.

**Phase 0 scope note:** this prototype collects and serializes **line**
coverage only (no `--cov-branch`/arc data). Attributing changed *branches*
rather than changed *lines* is left to a later phase if the vision's own
line/branch distinction turns out to matter in practice for this repo's
test portfolio -- see the vision's own Non-Goals section.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASELINE_SCHEMA_VERSION = 1

# coverage.py dynamic-context labels end in "|run" for the real execution
# phase (as opposed to "|setup"/"|teardown", or "" for collection-time-only
# coverage outside any test context, e.g. module-level import statements).
_RUN_CONTEXT_SUFFIX = "|run"


class BaselineCollectionError(RuntimeError):
    """Raised when the ephemeral pytest+coverage subprocess fails outright.

    A baseline is only ever earned from a run where **every** collected test
    passed: a failed/errored run can leave later tests unexecuted and
    coverage/duration data partial, which would silently under-test any
    change that relies on it -- the same "never silently under-test"
    Behavior the vision requires of selection applies just as much to the
    baseline it selects against.
    """

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"coverage baseline collection failed (exit {returncode}):\n"
            f"{stderr or stdout}"
        )


def collect_baseline(
    *,
    cwd: Path,
    test_path: str,
    cov_source: str,
    plugin: str,
    timeout_s: float = 300.0,
) -> dict:
    """Run `test_path` under coverage and return a portable baseline dict.

    `cov_source` is the `--cov` target (an import path or directory,
    relative to `cwd`) whose lines are attributed to tests.

    Raises `BaselineCollectionError` for any outcome other than a clean,
    fully-passing run (exit code 0) -- a baseline is only ever earned from
    evidence the validation gate itself would accept.
    """
    with tempfile.TemporaryDirectory(prefix="cgs-baseline-") as tmp:
        cov_data_file = Path(tmp) / ".coverage"
        json_report_file = Path(tmp) / "report.json"
        proc = subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "pytest-cov",
                "--with",
                "coverage",
                "--with",
                "pytest-json-report",
                "python",
                "-m",
                "pytest",
                test_path,
                "-q",
                f"--cov={cov_source}",
                "--cov-context=test",
                "--json-report",
                f"--json-report-file={json_report_file}",
                "-p",
                "no:cacheprovider",
            ],
            cwd=cwd,
            env={**_subprocess_env(), "COVERAGE_FILE": str(cov_data_file)},
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if proc.returncode != 0:
            raise BaselineCollectionError(proc.returncode, proc.stdout, proc.stderr)

        durations = _parse_durations(json_report_file)
        coverage_map = _parse_coverage_contexts(cov_data_file, cwd)

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "plugin": plugin,
        "cov_source": cov_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tests": {nodeid: {"duration_s": d} for nodeid, d in durations.items()},
        "coverage": coverage_map,
    }


def _subprocess_env() -> dict:
    import os

    env = dict(os.environ)
    # Never let a cached dev-venv's own interpreter/env leak into the
    # ephemeral baseline run (mirrors test-supervisor's own caller-env
    # scrubbing for the same reason: these describe the *caller's* bootstrap
    # environment, not the selector's).
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    env.pop("VIRTUAL_ENV", None)
    return env


def _parse_durations(json_report_file: Path) -> dict:
    """Exact, machine-readable per-test durations keyed by pytest node ID.

    Uses `pytest-json-report` instead of `--durations` text output: the
    human-readable durations report (a) rounds to hundredths of a second,
    collapsing fast tests to a misleading `0.00s`, (b) only records the
    `call` phase, discarding setup/teardown fixture cost, and (c) is a
    free-text table that cannot safely round-trip a node ID containing
    whitespace. The JSON report's `nodeid` and `duration` (seconds, float,
    across setup+call+teardown) avoid all three.
    """
    report = json.loads(json_report_file.read_text())
    durations: dict = {}
    for test in report.get("tests", []):
        nodeid = test.get("nodeid")
        total = 0.0
        for phase in ("setup", "call", "teardown"):
            phase_data = test.get(phase)
            if phase_data:
                total += float(phase_data.get("duration", 0.0))
        if nodeid is not None:
            durations[nodeid] = total
    return durations


def _parse_coverage_contexts(cov_data_file: Path, cwd: Path) -> dict:
    import coverage

    cov = coverage.CoverageData(basename=str(cov_data_file))
    cov.read()

    coverage_map: dict = {}
    for measured_file in cov.measured_files():
        try:
            rel = str(Path(measured_file).resolve().relative_to(cwd.resolve()))
        except ValueError:
            rel = measured_file
        contexts_by_line = cov.contexts_by_lineno(measured_file)
        per_line: dict = {}
        for lineno, contexts in contexts_by_line.items():
            tests = sorted(
                ctx[: -len(_RUN_CONTEXT_SUFFIX)]
                for ctx in contexts
                if ctx.endswith(_RUN_CONTEXT_SUFFIX)
            )
            if tests:
                per_line[str(lineno)] = tests
        if per_line:
            coverage_map[rel] = per_line
    return coverage_map


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("test_path")
    parser.add_argument("--cov-source", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    baseline = collect_baseline(
        cwd=Path(args.cwd),
        test_path=args.test_path,
        cov_source=args.cov_source,
        plugin=args.plugin,
    )
    Path(args.out).write_text(json.dumps(baseline, indent=2, sort_keys=True))
    print(f"wrote baseline for {args.plugin} to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
