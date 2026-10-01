"""Collect a portable coverage baseline from a real pytest run.

Spawns an ephemeral `uv run --with coverage --with pytest-cov` subprocess so
baseline collection needs no ambient dependency beyond `uv` itself -- the
same technique validated directly against aperture-labs' `tools/hooks/tests`
during this effort's originating low-risk spike (see this effort's own
2026-10-01 Journal entry).

The resulting baseline is deliberately pure JSON (`BASELINE_SCHEMA_VERSION`):
no live `coverage.py` database is carried past collection, so `select` and
`fallback` stay pure-stdlib and have nothing upstream to go stale against
except the baseline file itself.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASELINE_SCHEMA_VERSION = 1

# Matches a pytest `--durations=0` line for the "call" phase, e.g.:
#   "0.01s call     tools/hooks/tests/test_secret_scan.py::test_foo"
_DURATION_LINE_RE = re.compile(
    r"^\s*([\d.]+)s\s+call\s+(\S+)\s*$", re.MULTILINE
)

# coverage.py dynamic-context labels end in "|run" for the real execution
# phase (as opposed to "|setup"/"|teardown", or "" for collection-time-only
# coverage outside any test context, e.g. module-level import statements).
_RUN_CONTEXT_SUFFIX = "|run"


class BaselineCollectionError(RuntimeError):
    """Raised when the ephemeral pytest+coverage subprocess fails outright."""

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
    relative to `cwd`) whose lines/branches are attributed to tests.
    """
    with tempfile.TemporaryDirectory(prefix="cgs-baseline-") as tmp:
        cov_data_file = Path(tmp) / ".coverage"
        proc = subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "pytest-cov",
                "--with",
                "coverage",
                "python",
                "-m",
                "pytest",
                test_path,
                "-q",
                f"--cov={cov_source}",
                "--cov-context=test",
                "--durations=0",
                "--durations-min=0",
                "-p",
                "no:cacheprovider",
            ],
            cwd=cwd,
            env={**_subprocess_env(), "COVERAGE_FILE": str(cov_data_file)},
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if proc.returncode not in (0, 1):
            # 0 = all passed, 1 = some tests failed (still a valid baseline
            # run); anything else is an infrastructure failure.
            raise BaselineCollectionError(proc.returncode, proc.stdout, proc.stderr)

        durations = _parse_durations(proc.stdout)
        coverage_map = _parse_coverage_contexts(cov_data_file, cwd, cov_source)

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


def _parse_durations(stdout: str) -> dict:
    return {
        nodeid: float(seconds)
        for seconds, nodeid in _DURATION_LINE_RE.findall(stdout)
    }


def _parse_coverage_contexts(cov_data_file: Path, cwd: Path, cov_source: str) -> dict:
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
