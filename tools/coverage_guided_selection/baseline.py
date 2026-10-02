"""Collect a portable coverage baseline from a real pytest run.

Spawns a single ephemeral `uv run --with coverage --with pytest-cov --with
pytest-json-report` subprocess running a small in-process driver (written
to a temp file and executed in that same ephemeral venv) so baseline
collection needs no ambient dependency beyond `uv` itself: the driver runs
pytest, reads the resulting coverage database, and writes one merged JSON
result -- `coverage` and `pytest-json-report` are only ever imported inside
that ephemeral venv, never in this module's own caller process. This was
validated directly against a downstream consumer repository's own small
test suite during this effort's originating low-risk spike (see this
effort's own 2026-10-01 Journal entry).

The resulting baseline is deliberately pure JSON (`BASELINE_SCHEMA_VERSION`):
no live `coverage.py` database is carried past collection, so `selection` and
`fallback` stay pure-stdlib and have nothing upstream to go stale against
except the baseline file itself.

**Phase 0 scope note:** this prototype collects and serializes **line**
coverage only (no `--cov-branch`/arc data) -- the vision's own Concepts use
"lines/branches" generically since a realizing effort may pick either;
Phase 0 narrows that choice to lines only for this pilot, and this contract
is that narrowing's single source of truth for the code in this package.
Branch-level attribution, if ever needed, is later-phase scope.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

BASELINE_SCHEMA_VERSION = 2
# v1: {schema_version, plugin, cov_source, generated_at, tests, coverage}
# v2: adds `measured_commit` (the `dev` SHA this run was measured against,
#     see `correlation.py`) -- nullable, so a v1 consumer that only reads
#     the fields it already knows about is unaffected.

# Environment variables that can silently narrow which tests pytest
# actually collects/runs (e.g. `PYTEST_ADDOPTS=-k smoke` or `-m guard`)
# while pytest still exits 0 -- any of these would make a partial run look
# like a complete, authoritative baseline. Scrubbed before the ephemeral
# subprocess launches; the driver's own `-o addopts=` override (below)
# separately neutralizes the same risk from a *project-configured* addopts
# (pytest.ini/pyproject.toml/setup.cfg), which this environment-variable
# scrub alone cannot reach.
_AMBIENT_PYTEST_SELECTION_ENV_VARS = ("PYTEST_ADDOPTS",)

# Executed inside the ephemeral `uv run` venv (never the caller's own
# process): runs pytest in-process, then reads the coverage database that
# same run just produced, and writes one merged JSON result. Keeping both
# steps in the same ephemeral interpreter means neither `coverage` nor
# `pytest-json-report` is ever imported in this module's own process.
_DRIVER_SCRIPT = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    import pytest

    test_path, cov_source, cwd, cov_data_file, json_report_file, out_file = sys.argv[1:7]

    exit_code = pytest.main(
        [
            test_path,
            "-q",
            f"--cov={cov_source}",
            "--cov-context=test",
            "--json-report",
            f"--json-report-file={json_report_file}",
            "-p",
            "no:cacheprovider",
            # Override any project-configured `addopts` (pytest.ini/
            # pyproject.toml/setup.cfg) for this invocation only: an
            # addopts like "-m smoke" would otherwise silently narrow
            # collection to a subset while this run still exits 0, letting
            # a partial run be recorded as a complete, authoritative
            # baseline. Clearing the *environment* variable alone (see
            # _AMBIENT_PYTEST_SELECTION_ENV_VARS) does not reach this
            # configured-file source.
            "-o",
            "addopts=",
        ]
    )

    if exit_code != 0:
        sys.exit(exit_code)

    import coverage

    cov = coverage.CoverageData(basename=cov_data_file)
    cov.read()

    cwd_path = Path(cwd).resolve()
    phase_suffixes = ("|run", "|setup", "|teardown")
    coverage_map = {}
    for measured_file in cov.measured_files():
        try:
            rel = str(Path(measured_file).resolve().relative_to(cwd_path))
        except ValueError:
            rel = measured_file
        per_line = {}
        for lineno, contexts in cov.contexts_by_lineno(measured_file).items():
            tests = set()
            for ctx in contexts:
                for suffix in phase_suffixes:
                    if ctx.endswith(suffix):
                        tests.add(ctx[: -len(suffix)])
                        break
            if tests:
                per_line[str(lineno)] = sorted(tests)
        if per_line:
            coverage_map[rel] = per_line

    report = json.loads(Path(json_report_file).read_text())
    durations = {}
    for test in report.get("tests", []):
        nodeid = test.get("nodeid")
        if nodeid is None:
            continue
        total = 0.0
        for phase in ("setup", "call", "teardown"):
            phase_data = test.get(phase)
            if phase_data:
                total += float(phase_data.get("duration", 0.0))
        durations[nodeid] = total

    Path(out_file).write_text(json.dumps({"durations": durations, "coverage": coverage_map}))
    sys.exit(0)
    """
)


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


def _timeout_error(exc: subprocess.TimeoutExpired, *, timeout_s: float) -> BaselineCollectionError:
    def _decode(value) -> str:
        return value.decode() if isinstance(value, bytes) else (value or "")

    return BaselineCollectionError(
        -1,
        _decode(exc.stdout),
        f"timed out after {timeout_s}s" + ((": " + _decode(exc.stderr)) if exc.stderr else ""),
    )


def _project_has_dev_extra(project_dir: Path) -> bool:
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        import tomllib

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return False
    extras = data.get("project", {}).get("optional-dependencies", {})
    return "dev" in extras


def _prepare_project_venv(project_dir: Path, tmp_path: Path, *, timeout_s: float) -> Path:
    """Build an ephemeral venv with `project_dir`'s own package installed
    editable (so its `[tool.uv.sources]` vendored path dependencies resolve
    exactly the way `tools/run-plugin-tests.py`'s own cached-venv builder
    does -- see that script's `_ensure_venv`), plus the coverage-collection
    extras on top. Needed for any plugin with real dependencies (e.g.
    `agent-ssh`'s vendored `libs/ssh-manager`/`libs/agent-procutil`
    references) -- a bare `uv run --with` ephemeral venv (the no-`project_dir`
    path below) has no project context to resolve those from and only works
    for a dependency-free, script-only plugin like `ai-attribution`.

    POSIX-only (`venv/bin/python`) -- this is scoped to this pipeline's own
    `ubuntu-latest` runners, not a general cross-platform contract.
    """
    venv_dir = tmp_path / "venv"
    try:
        subprocess.run(
            ["uv", "venv", str(venv_dir)],
            check=True, capture_output=True, text=True, timeout=timeout_s,
        )
        python_exe = venv_dir / "bin" / "python"
        spec = ".[dev]" if _project_has_dev_extra(project_dir) else "."
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python_exe), "-e", spec],
            cwd=project_dir, check=True, capture_output=True, text=True, timeout=timeout_s,
        )
        subprocess.run(
            [
                "uv", "pip", "install", "--python", str(python_exe),
                "coverage", "pytest-cov", "pytest-json-report",
            ],
            check=True, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.CalledProcessError as exc:
        raise BaselineCollectionError(exc.returncode, exc.stdout or "", exc.stderr or "") from exc
    except subprocess.TimeoutExpired as exc:
        raise _timeout_error(exc, timeout_s=timeout_s) from exc
    return python_exe


def collect_baseline(
    *,
    cwd: Path,
    test_path: str,
    cov_source: str,
    plugin: str,
    timeout_s: float = 300.0,
    measured_commit: str | None = None,
    project_dir: Path | None = None,
) -> dict:
    """Run `test_path` under coverage and return a portable baseline dict.

    `cov_source` is the `--cov` target (an import path or directory,
    relative to `cwd`) whose lines are attributed to tests.

    `measured_commit` is the `dev` commit SHA this run's coverage was
    actually measured against -- e.g. the promotion gate's own `dev_head`
    (see `correlation.py` and this effort's own 2026-10-01 storage/
    correlation Journal entry). Embedding it directly in the baseline makes
    a single baseline file self-correlating, without requiring a reader to
    cross-reference a second file (`.github/release-pipeline-state.json`)
    to know what it was measured against. Optional here (a local/manual run
    has no promotion commit to record), but required by `correlation.py`'s
    own writer before a baseline is checked into `main`.

    `project_dir`, when given, is a plugin's own root (containing its
    `pyproject.toml`) -- the coverage run installs that project editable
    (with its `dev` extra, if declared) into an ephemeral venv first, so a
    plugin with real dependencies (vendored path deps included) can be
    measured, not just a dependency-free script plugin. Omit it for a
    plugin like `ai-attribution` with no installable package.

    Raises `BaselineCollectionError` for any outcome other than a clean,
    fully-passing run (exit code 0) -- a baseline is only ever earned from
    evidence the validation gate itself would accept.
    """
    with tempfile.TemporaryDirectory(prefix="cgs-baseline-") as tmp:
        cwd = cwd.resolve()  # resolve once: both the subprocess cwd and the
        # driver's own cwd argv must agree, or a relative `cwd` double-joins
        # itself when the driver re-resolves it from inside that directory.
        tmp_path = Path(tmp)
        driver_file = tmp_path / "_cgs_driver.py"
        driver_file.write_text(_DRIVER_SCRIPT)
        cov_data_file = tmp_path / ".coverage"
        json_report_file = tmp_path / "report.json"
        out_file = tmp_path / "baseline.json"
        driver_args = [
            test_path, cov_source, str(cwd),
            str(cov_data_file), str(json_report_file), str(out_file),
        ]

        if project_dir is not None:
            python_exe = _prepare_project_venv(project_dir, tmp_path, timeout_s=timeout_s)
            command = [str(python_exe), str(driver_file), *driver_args]
        else:
            command = [
                "uv", "run",
                "--with", "pytest-cov",
                "--with", "coverage",
                "--with", "pytest-json-report",
                "python", str(driver_file), *driver_args,
            ]

        proc = None
        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                env=_subprocess_env(cov_data_file, tmp_path / "sandbox"),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            # A hang is just as non-clean a collection outcome as a nonzero
            # exit: translate it into the same error contract instead of
            # letting it escape as an undocumented `TimeoutExpired`, so every
            # caller only ever needs to catch `BaselineCollectionError`.
            raise _timeout_error(exc, timeout_s=timeout_s) from exc
        if proc.returncode != 0:
            raise BaselineCollectionError(proc.returncode, proc.stdout, proc.stderr)

        merged = json.loads(out_file.read_text())

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "plugin": plugin,
        "cov_source": cov_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measured_commit": measured_commit,
        "tests": {nodeid: {"duration_s": d} for nodeid, d in merged["durations"].items()},
        "coverage": merged["coverage"],
    }


def _subprocess_env(cov_data_file: Path, sandbox: Path) -> dict:
    import os

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from plugin_test_containment import isolated_environment

    # Run baseline collection under the exact same containment
    # `run-plugin-tests.py`'s own trusted path always uses: `HOME`,
    # `USERPROFILE`, every XDG dir, and plugin-specific state paths are all
    # redirected into `sandbox` (never the caller's real host state), and
    # `GH_TOKEN`/`GITHUB_TOKEN`/facility env vars (e.g. `AGENT_RT_ROOT`) are
    # scrubbed outright -- confirmed live: without this, a plugin under test
    # (e.g. `agent-containers`' own `provider_ssh.py`) can read a leaked
    # ambient value ahead of a test's own monkeypatched substitute, or a
    # baseline run can read/write real host state a sandboxed test run
    # never would. A baseline is only ever earned from the same conditions
    # the real validation gate runs under (see `collect_baseline`'s own
    # docstring).
    env = isolated_environment(os.environ, sandbox)
    # Never let a cached dev-venv's own interpreter/env leak into the
    # ephemeral baseline run (mirrors test-supervisor's own caller-env
    # scrubbing for the same reason: these describe the *caller's* bootstrap
    # environment, not the selector's). Not part of `isolated_environment`'s
    # own scrub list, so handled separately here.
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    env.pop("VIRTUAL_ENV", None)
    # Never let an ambient pytest-selection override make a partial
    # collection look like a complete, authoritative baseline run. Also not
    # part of `isolated_environment`'s own scrub list (that targets host
    # state/credentials, not pytest-specific selection overrides).
    for var in _AMBIENT_PYTEST_SELECTION_ENV_VARS:
        env.pop(var, None)
    # Direct pytest-cov's own data file to our temp path -- without this,
    # it defaults to "./.coverage" relative to the subprocess's cwd (the
    # caller's own repo checkout), which both pollutes that checkout and
    # means the driver reads back nothing from its own intended path.
    env["COVERAGE_FILE"] = str(cov_data_file)
    return env


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("test_path")
    parser.add_argument("--cov-source", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--measured-commit",
        default=None,
        help="the dev commit SHA this run is measured against (see correlation.py)",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="a plugin's own root (pyproject.toml) to install editable before "
             "collecting -- needed for a plugin with real dependencies",
    )
    args = parser.parse_args(argv)

    baseline = collect_baseline(
        cwd=Path(args.cwd),
        test_path=args.test_path,
        cov_source=args.cov_source,
        plugin=args.plugin,
        measured_commit=args.measured_commit,
        project_dir=Path(args.project_dir) if args.project_dir else None,
    )
    Path(args.out).write_text(json.dumps(baseline, indent=2, sort_keys=True))
    print(f"wrote baseline for {args.plugin} to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
