"""Collect a portable coverage baseline from a real pytest run.

Spawns a single ephemeral `uv run --with coverage --with
pytest-json-report` subprocess running a small in-process driver (written
to a temp file and executed in that same ephemeral venv) so baseline
collection needs no ambient dependency beyond `uv` itself: the driver runs
pytest, reads the resulting coverage database, and writes one merged JSON
result -- `coverage` and `pytest-json-report` are only ever imported inside
that ephemeral venv, never in this module's own caller process. This was
validated directly against a downstream consumer repository's own small
test suite during this effort's originating low-risk spike (see this
effort's own 2026-10-01 Journal entry), and against a much larger,
multi-threaded downstream suite when the coverage-attach race below
surfaced and was fixed.

The driver manages a `coverage.Coverage` object directly (not
`pytest-cov`'s own `--cov=`/`--cov-context=test` CLI options) -- see
`_DRIVER_SCRIPT`'s own comment on the coverage-attach race this avoids.

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

BASELINE_SCHEMA_VERSION = 3
# v1: {schema_version, plugin, cov_source, generated_at, tests, coverage}
# v2: adds `measured_commit` (the `dev` SHA this run was measured against,
#     see `correlation.py`) -- nullable, so a v1 consumer that only reads
#     the fields it already knows about is unaffected.
# v3: each `tests` entry adds `portfolio_tier` (the test's own
#     `@pytest.mark.portfolio_tier(...)` value, upper-cased, or `None` for
#     an untiered test) alongside `duration_s` -- lets `fallback.py`'s
#     curation restrict its candidates to the portfolio's own default,
#     always-on tiers (see `fallback.default_tier_eligible_tests`) instead
#     of treating every collected test as an equally-safe smoke-tier
#     candidate. A v1/v2 reader that only looks at `duration_s` is
#     unaffected; a v1/v2 baseline (collected before this field existed)
#     simply has no `portfolio_tier` key per test, which
#     `default_tier_eligible_tests` treats as eligible (same as an
#     explicitly untiered test), not as excluded.

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

    import coverage
    import pytest

    # Every statement below must stay inside this guard. A plugin's own
    # tests may spawn real child processes via `multiprocessing.get_context
    # ("spawn")` -- `spawn` bootstraps a fresh interpreter that re-imports
    # this very script as a plain module (not as `__main__`) to reconstruct
    # the pickled target it needs to run. Without this guard, that
    # re-import re-executes `pytest.main(...)` unconditionally, which
    # multiprocessing's own bootstrap-safety check catches and raises
    # "An attempt has been made to start a new process before the current
    # process has finished its bootstrapping phase" for -- confirmed live:
    # `agent-worktrees`' own real cross-process file-lock tests spawn
    # exactly such a child, which died at bootstrap with that exact error
    # (observable as the spawned process simply never living), and the
    # doomed re-execution's own half-started coverage instance is what
    # corrupted the real coverage data file the parent process was still
    # writing to (`coverage.exceptions.DataError: ... no such table:
    # context`). This is the same "Safe importing of main module" hazard
    # Python's own `multiprocessing` docs describe for any script, not
    # something specific to pytest or coverage.
    if __name__ == "__main__":
        test_paths_json, cov_source, cwd, cov_data_file, json_report_file, out_file, basetemp = sys.argv[1:8]
        test_paths = json.loads(test_paths_json)

        # Captured via a hook plugin (not pytest-json-report's own
        # "keywords" field, which only records marker NAMES present on an
        # item, never a marker's own argument) -- `fallback.py`'s tier
        # eligibility needs the actual declared tier value ("T0".."T4"),
        # not just whether some `portfolio_tier` marker was applied at
        # all. Independent of whether `pytest_portfolio_guard` itself is
        # loaded in this ephemeral run: a test's own marker is attached by
        # its source decorator regardless, so this always sees the real
        # declared tier even though this driver never registers that
        # guard plugin.
        tier_by_nodeid = {}

        class _TierCollector:
            def pytest_collection_modifyitems(self, items):
                for item in items:
                    marker = item.get_closest_marker("portfolio_tier")
                    tier_by_nodeid[item.nodeid] = (
                        str(marker.args[0]).upper()
                        if marker and marker.args
                        else None
                    )

        class _ContextSwitcher:
            # One switch_context() per test (at setup, covering setup +
            # call + teardown under the same context) rather than a
            # phase-level split -- this baseline only ever needed "which
            # test" per line, never phase-level granularity.
            #
            # `tryfirst=True` is required, not cosmetic (review finding):
            # this plugin is registered via `pytest.main(..., plugins=
            # [...])` before a project's own conftest.py plugins load.
            # Pluggy calls same-hook implementations in reverse
            # registration order by default, so a downstream conftest's
            # own `pytest_runtest_setup` hookimpl (if one exists) would
            # otherwise run *before* this switch and get its source lines
            # attributed to the previous test (or no test at all). The
            # old pytest-cov context plugin never had this risk -- it
            # registered at session start, after every conftest.
            @pytest.hookimpl(tryfirst=True)
            def pytest_runtest_setup(self, item):
                cov = coverage.Coverage.current()
                if cov is not None:
                    cov.switch_context(item.nodeid)

        # Managed directly via the `coverage` API rather than
        # pytest-cov's own `--cov=`/`--cov-context=test` CLI options --
        # confirmed root cause of an intermittent (at scale, close to
        # constant) `sqlite3.OperationalError: unable to open database
        # file` inside coverage.py's own internal data file against a
        # large, multi-threaded downstream consumer suite:
        # `pytest-cov`'s own `CoverageData` always suffixes its data file
        # (host+pid+random, unconditional, not just for `pytest-xdist`),
        # and coverage.py creates its sqlite schema *lazily*, on whichever
        # thread's data flushes first (one internal `SqliteDb` handle per
        # `threading.get_ident()`) -- multiple threads' first-ever flush
        # can race that lazy schema creation. Two targeted experiments
        # ruled out a live-thread-write race specifically (the identical
        # failure reproduced with zero dynamic context switching at all,
        # and reproduced even after explicitly joining every non-main
        # thread before the final save) before finding this. Managing
        # `Coverage` ourselves fixes the data file to one exact,
        # caller-controlled path (no suffix to race against) and forces
        # the schema to exist via one single-threaded `save()`
        # immediately after `start()`, before `pytest.main()` launches any
        # test or thread -- eliminating the race window entirely rather
        # than narrowing it. Validated against the real downstream suite
        # that surfaced this: fully clean and reproducible across three
        # separate runs, where the pytest-cov-driven version failed on
        # almost every attempt. `data_suffix=False` is explicit, not
        # relied-on-as-default: passing `data_file` alone does not
        # override a *project-configured* `parallel = true` (or
        # `concurrency = multiprocessing`) that a downstream consumer's
        # own .coveragerc/pyproject.toml may set for its own reasons --
        # `coverage.Coverage()` still reads that config and would
        # re-enable a generated suffix, silently reintroducing the exact
        # race this fix exists to close (flagged in review; see
        # ThomasMichon/copilot-extensions#5471).
        cov = coverage.Coverage(
            data_file=cov_data_file, source=[cov_source], data_suffix=False
        )
        cov.start()
        cov.save()  # force schema creation now, single-threaded

        exit_code = pytest.main(
            [
                *test_paths,
                "-q",
                f"--basetemp={basetemp}",
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
            ],
            plugins=[_TierCollector(), _ContextSwitcher()],
        )

        cov.stop()
        cov.save()

        if exit_code != 0:
            sys.exit(exit_code)

        cov_data = coverage.CoverageData(basename=cov_data_file)
        cov_data.read()

        cwd_path = Path(cwd).resolve()
        # `_ContextSwitcher` above records one context per test (the bare
        # nodeid, no phase suffix) -- but tolerate the `|run`/`|setup`/
        # `|teardown` suffixes pytest-cov's own TestContextPlugin would
        # have produced too, so a baseline measured with that plugin
        # instead (or an older one of this driver's own baselines) still
        # reads back correctly.
        phase_suffixes = ("|run", "|setup", "|teardown")
        coverage_map = {}
        for measured_file in cov_data.measured_files():
            try:
                rel = str(Path(measured_file).resolve().relative_to(cwd_path))
            except ValueError:
                rel = measured_file
            per_line = {}
            for lineno, contexts in cov_data.contexts_by_lineno(measured_file).items():
                tests = set()
                for ctx in contexts:
                    if not ctx:
                        continue
                    for suffix in phase_suffixes:
                        if ctx.endswith(suffix):
                            tests.add(ctx[: -len(suffix)])
                            break
                    else:
                        tests.add(ctx)
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

        Path(out_file).write_text(
            json.dumps(
                {"durations": durations, "tiers": tier_by_nodeid, "coverage": coverage_map}
            )
        )
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
                "coverage", "pytest-json-report",
            ],
            check=True, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.CalledProcessError as exc:
        raise BaselineCollectionError(exc.returncode, exc.stdout or "", exc.stderr or "") from exc
    except subprocess.TimeoutExpired as exc:
        raise _timeout_error(exc, timeout_s=timeout_s) from exc
    return python_exe


def _discover_test_files(cwd: Path, test_path: str) -> list[Path] | None:
    """Every file under `test_path` matching pytest's own default
    collection patterns (`test_*.py` and `*_test.py` -- confirmed in
    pytest's documented `python_files` default; a project overriding that
    default via its own `pyproject.toml`/`pytest.ini` is not accounted for
    here, same as `run-plugin-tests.py`'s own single-pattern discovery),
    sorted, deduped (a name like `test_foo_test.py` matches both patterns).
    Returns `None` if `test_path` isn't a directory (e.g. it already names
    a single file) -- the caller then treats it as a single, unsplit
    chunk."""
    target = (cwd / test_path).resolve()
    if not target.is_dir():
        return None
    return sorted(
        {*target.rglob("test_*.py"), *target.rglob("*_test.py")}
    )


def _relative_or_absolute(path: Path, cwd: Path) -> str:
    """`path` relative to `cwd` when possible, or `path` itself (resolved,
    absolute) when it isn't -- e.g. a test directory given as an absolute
    path outside `cwd` entirely. Pytest accepts either form as a positional
    collection argument regardless of its own `cwd`, so an out-of-tree
    `test_path` keeps working once a suite is large enough to chunk, not
    just while it stays under the single-chunk threshold."""
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def _plan_chunks(cwd: Path, test_path: str, max_files_per_chunk: int) -> list[list[str]]:
    """Split `test_path` into chunks of at most `max_files_per_chunk` test
    files each -- mirrors `tools/run-plugin-tests.py`'s own sub-suite
    chunking (`_test_file_groups`, default 25 files), which this function
    reuses the same `partition` helper for. A suite within the limit (or a
    single file rather than a directory) stays exactly one chunk, naming
    `test_path` itself unchanged, so collection behavior for every
    already-enrolled plugin is bit-for-bit identical to before chunking
    existed."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from plugin_test_containment import partition

    test_files = _discover_test_files(cwd, test_path)
    if test_files is None or len(test_files) <= max_files_per_chunk:
        return [[test_path]]
    return partition(
        [_relative_or_absolute(f, cwd) for f in test_files], max_files_per_chunk
    )


def _merge_chunk_results(chunks: list[dict]) -> dict:
    """Combine each chunk's own `{"durations": ..., "tiers": ..., "coverage":
    ...}` dict (as `_DRIVER_SCRIPT` emits per chunk) into one. Test
    durations and tiers are both a flat union (a given test belongs to
    exactly one chunk); coverage lines need a real per-line union of
    attributed test names, since a shared source file touched by tests
    from more than one chunk would otherwise have one chunk's attribution
    silently clobber another's. `tiers` is read with a default so a chunk
    dict predating its introduction (schema v2 and earlier) still merges
    cleanly, with every one of its tests treated as untiered rather than
    raising a `KeyError`."""
    durations: dict[str, float] = {}
    tiers: dict[str, str | None] = {}
    coverage: dict[str, dict[str, list[str]]] = {}
    for chunk in chunks:
        durations.update(chunk["durations"])
        tiers.update(chunk.get("tiers", {}))
        for file, per_line in chunk["coverage"].items():
            merged_file = coverage.setdefault(file, {})
            for line, tests in per_line.items():
                existing = set(merged_file.get(line, ()))
                existing.update(tests)
                merged_file[line] = sorted(existing)
    return {"durations": durations, "tiers": tiers, "coverage": coverage}


def collect_baseline(
    *,
    cwd: Path,
    test_path: str,
    cov_source: str,
    plugin: str,
    timeout_s: float = 300.0,
    measured_commit: str | None = None,
    project_dir: Path | None = None,
    max_files_per_chunk: int = 25,
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

    `max_files_per_chunk` bounds how many test files run in a single pytest
    process at once (default 25, matching `run-plugin-tests.py`'s own
    `--max-files-per-sub-suite` default). Confirmed live: `agent-mcp`'s
    full suite (54 files, real process/watchdog-management tests) reliably
    crashed a single unchunked collection pass with near-zero captured
    output, while the trusted runner's own chunked sub-suites pass it
    cleanly -- this mirrors that same chunking so a large or
    subprocess-heavy suite doesn't need its own one-off accommodation.

    Raises `BaselineCollectionError` for any outcome other than a clean,
    fully-passing run (exit code 0) -- a baseline is only ever earned from
    evidence the validation gate itself would accept. A chunked suite fails
    fast on the first non-clean chunk, exactly like a single-chunk suite
    always has.
    """
    with tempfile.TemporaryDirectory(
        prefix="cgs-baseline-", ignore_cleanup_errors=True
    ) as tmp:
        # `ignore_cleanup_errors=True` matches `run-plugin-tests.py`'s own
        # sandboxed-tempdir handling -- confirmed live: a plugin whose own
        # real tests spawn child processes (e.g. `agent-worktrees`' real
        # cross-process file-lock tests) can leave a lingering handle open
        # on a file under this tempdir past this function's own return,
        # which would otherwise turn a clean, fully-passing collection run
        # into a raised `OSError` on cleanup -- discarding a baseline that
        # was already earned.
        cwd = cwd.resolve()  # resolve once: both the subprocess cwd and the
        # driver's own cwd argv must agree, or a relative `cwd` double-joins
        # itself when the driver re-resolves it from inside that directory.
        tmp_path = Path(tmp)
        driver_file = tmp_path / "_cgs_driver.py"
        driver_file.write_text(_DRIVER_SCRIPT)
        sandbox = tmp_path / "sandbox"  # one sandbox reused across every
        # chunk -- matches `run-plugin-tests.py`'s own sequential sub-suite
        # loop, which shares a single sandbox the same way.

        if project_dir is not None:
            python_exe = _prepare_project_venv(project_dir, tmp_path, timeout_s=timeout_s)
            command_prefix = [str(python_exe), str(driver_file)]
        else:
            command_prefix = [
                "uv", "run",
                "--with", "coverage",
                "--with", "pytest-json-report",
                "python", str(driver_file),
            ]

        chunks = _plan_chunks(cwd, test_path, max_files_per_chunk)
        chunk_results = []
        for index, chunk_paths in enumerate(chunks):
            chunk_cov_data_file = tmp_path / f".coverage.{index}"
            chunk_json_report_file = tmp_path / f"report.{index}.json"
            chunk_out_file = tmp_path / f"baseline.{index}.json"
            # An explicit, short `--basetemp` (rather than pytest's own
            # TMPDIR-derived default) keeps every `tmp_path`-fixture path a
            # test creates well clear of AF_UNIX's 108-byte `sun_path`
            # limit -- confirmed live: without this, `agent-mcp`'s own
            # real-socket cutover tests failed with "AF_UNIX path too
            # long" once `_subprocess_env`'s `isolated_environment`
            # redirected TMPDIR into a deeply nested sandbox path pytest's
            # default tmp_path root would otherwise nest even deeper under
            # (`.../sandbox/tmp/pytest-of-<user>/pytest-<n>/...`). Mirrors
            # `run-plugin-tests.py`'s own `--basetemp` override for the
            # identical reason.
            basetemp = tmp_path / f"pt{index}"
            driver_args = [
                json.dumps(chunk_paths), cov_source, str(cwd),
                str(chunk_cov_data_file), str(chunk_json_report_file), str(chunk_out_file),
                str(basetemp),
            ]
            command = [*command_prefix, *driver_args]

            proc = None
            try:
                proc = subprocess.run(
                    command,
                    cwd=cwd,
                    env=_subprocess_env(chunk_cov_data_file, sandbox),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                # A hang is just as non-clean a collection outcome as a
                # nonzero exit: translate it into the same error contract
                # instead of letting it escape as an undocumented
                # `TimeoutExpired`, so every caller only ever needs to catch
                # `BaselineCollectionError`.
                raise _timeout_error(exc, timeout_s=timeout_s) from exc
            if proc.returncode != 0:
                raise BaselineCollectionError(proc.returncode, proc.stdout, proc.stderr)

            chunk_results.append(json.loads(chunk_out_file.read_text()))

        merged = _merge_chunk_results(chunk_results)

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "plugin": plugin,
        "cov_source": cov_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measured_commit": measured_commit,
        "tests": {
            nodeid: {"duration_s": d, "portfolio_tier": merged["tiers"].get(nodeid)}
            for nodeid, d in merged["durations"].items()
        },
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
    # Match `run-plugin-tests.py`'s own override exactly (not just leave
    # `isolated_environment`'s caller-preserved `PYTHONPATH` as-is): without
    # this, an ambient stale source tree on `PYTHONPATH` could still take
    # import precedence in this baseline's own pytest run even though the
    # trusted validation run never would, breaking the "same conditions"
    # contract this isolation exists to provide.
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
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
    # Unused now that the driver passes `data_file=cov_data_file` explicitly
    # to `coverage.Coverage()` -- kept as a harmless, redundant default in
    # case any future code path falls back to env-var discovery.
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
    parser.add_argument(
        "--max-files-per-chunk",
        type=int,
        default=25,
        help="split a suite larger than this many test files into sequential "
             "chunks, matching run-plugin-tests.py's own sub-suite chunking "
             "(default: 25)",
    )
    args = parser.parse_args(argv)

    baseline = collect_baseline(
        cwd=Path(args.cwd),
        test_path=args.test_path,
        cov_source=args.cov_source,
        plugin=args.plugin,
        measured_commit=args.measured_commit,
        project_dir=Path(args.project_dir) if args.project_dir else None,
        max_files_per_chunk=args.max_files_per_chunk,
    )
    Path(args.out).write_text(json.dumps(baseline, indent=2, sort_keys=True))
    print(f"wrote baseline for {args.plugin} to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
