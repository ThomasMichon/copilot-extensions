#!/usr/bin/env python3
"""Turn-key plugin test runner (on-demand local development).

Runs a plugin's ``pytest`` suite in a managed, cached dev virtualenv so the
suites that guard the marketplace (the picker's overlay-registry / palette /
keyboard-harness guards, the shipped-manifest contract, each plugin's unit
tests) can be run with ONE command instead of a hand-rolled ``uv venv`` +
editable-install dance. There is intentionally no automatic push/PR gate wired
to it yet -- run it yourself before pushing a runtime change.

Usage::

    python tools/run-plugin-tests.py agent-worktrees        # one plugin
    python tools/run-plugin-tests.py --changed              # plugins touched vs origin/main
    python tools/run-plugin-tests.py --all                  # every plugin with a suite
    python tools/run-plugin-tests.py agent-worktrees -k picker   # filter
    python tools/run-plugin-tests.py --changed --pre-push   # hook mode (skip if uv absent)

Design notes:

* **uv-based.** Uses ``uv`` for the venv + editable install so plugins that
  vendor path dependencies via ``[tool.uv.sources]`` (agent-containers,
  agent-codespaces, ...) resolve correctly -- plain ``pip`` cannot.
* **Cached venvs** live under ``.test-venvs/<platform>/<plugin>`` (git-ignored)
  and are reused across runs; ``--reinstall`` rebuilds one.
* **Host admission.** Potentially heavy runs share one per-user host lease
  across every checkout/worktree, so concurrent agents fail fast or wait for a
  bounded interval instead of multiplying load.
* **State isolation.** Default suites receive runner-owned home, XDG, Copilot,
  plugin-state, and temporary roots. Credential-dependent end-to-end checks
  must opt into host state explicitly.
* **Windows-safe temp.** Passes a randomized ``--basetemp`` so pytest's tmp
  cleanup does not trip the ``pytest-current`` junction ``PermissionError`` on
  Windows (teardown noise that would otherwise mask a green run).
* **Time-bounded at every level.** Individual tests use ``pytest-timeout``;
  large suites run as sequential file groups with their own wall limit; and a
  plugin-wide deadline bounds the aggregate.
* **Fail-closed on test failures**, but in ``--pre-push`` mode it degrades
  gracefully (warn + skip, exit 0) only when the *tooling* (uv) is genuinely
  absent -- never blocking a push just because a dev box lacks uv, while still
  enforcing real failures wherever it can run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from plugin_test_containment import (
    ContainmentError,
    Limits,
    isolated_environment,
    partition,
    run_contained,
)

REPO = Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"
VENV_ROOT = REPO / ".test-venvs" / sys.platform
PORTFOLIO_PLUGIN = "pytest_portfolio_guard"
RUNNER_DEPENDENCIES = ("pytest-timeout>=2.3,<3",)
LEASE_LIB = REPO / "libs" / "single-instance-lease" / "src"

# The runner is a repository tool, so consume the canonical shared source
# directly rather than growing another lock implementation.
sys.path.insert(0, str(LEASE_LIB))
from single_instance_lease import AlreadyRunningError, SingleInstance  # noqa: E402

_ADMISSION_SERVICE = "copilot-extensions-test-runner"


def _admission_dir() -> Path:
    """Return a per-user, host-wide lock directory shared by all worktrees."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "copilot-extensions" / "test-runner"


def _acquire_admission(wait_seconds: float) -> SingleInstance:
    """Acquire the host test slot, waiting for at most ``wait_seconds``."""
    if wait_seconds < 0:
        raise ValueError("admission wait must be non-negative")
    lease = SingleInstance(_admission_dir(), service=_ADMISSION_SERVICE)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            lease.acquire()
            return lease
        except AlreadyRunningError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.25, remaining))


def _plugin_dir(name: str) -> Path:
    return PLUGINS / name


def _has_suite(name: str) -> bool:
    d = _plugin_dir(name)
    tests = d / "tests"
    return d.is_dir() and tests.is_dir() and any(tests.glob("test_*.py"))


def _has_pyproject(name: str) -> bool:
    return (_plugin_dir(name) / "pyproject.toml").is_file()


def _has_dev_extra(name: str) -> bool:
    pyproject = _plugin_dir(name) / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    extras = data.get("project", {}).get("optional-dependencies", {})
    return "dev" in extras


def all_plugins_with_suites() -> list[str]:
    if not PLUGINS.is_dir():
        return []
    return sorted(p.name for p in PLUGINS.iterdir()
                  if p.is_dir() and _has_suite(p.name))


def changed_plugins(base: str) -> list[str]:
    """Plugins whose files changed vs ``base`` (default origin/main)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True, text=True, check=False,
        )
        names = set()
        for line in out.stdout.splitlines():
            parts = line.replace("\\", "/").split("/")
            if len(parts) >= 2 and parts[0] == "plugins":
                names.add(parts[1])
        # Also include un-committed changes (staged + working tree).
        out2 = subprocess.run(
            ["git", "-C", str(REPO), "status", "--porcelain"],
            capture_output=True, text=True, check=False,
        )
        for line in out2.stdout.splitlines():
            path = line[3:].replace("\\", "/").split(" -> ")[-1]
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] == "plugins":
                names.add(parts[1])
    except OSError:
        return []
    return sorted(n for n in names if _has_suite(n))


def _venv_python(name: str) -> Path:
    base = VENV_ROOT / name
    if os.name == "nt":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def _dep_fingerprint(name: str) -> str:
    """Hash the plugin's ``pyproject.toml`` plus any vendored lib pyprojects, so
    a dependency change forces a venv rebuild. Without this, ``_ensure_venv``
    reuses a cached venv whose installed deps have drifted -- e.g. a path dep
    added across a branch rebase is silently absent, masking/introducing
    failures (a stale venv once produced 28 phantom ``ModuleNotFoundError``s)."""
    import hashlib
    pdir = _plugin_dir(name)
    parts = [pdir / "pyproject.toml"]
    libs = pdir / "libs"
    if libs.is_dir():
        parts += sorted(libs.glob("*/pyproject.toml"))
    h = hashlib.sha256()
    for dependency in RUNNER_DEPENDENCIES:
        h.update(f"runner:{dependency}\n".encode())
    for p in parts:
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def _ensure_venv(name: str, uv: str, *, reinstall: bool) -> Path:
    """Create (or reuse) the cached dev venv for ``name`` and return its python."""
    venv = VENV_ROOT / name
    py = _venv_python(name)
    stamp = venv / ".dep-fingerprint"
    fingerprint = _dep_fingerprint(name)
    if reinstall and venv.exists():
        shutil.rmtree(venv, ignore_errors=True)
    fresh = not py.exists()
    # Rebuild a cached venv whose dependency fingerprint has drifted (e.g. a
    # dependency added across a rebase) -- otherwise the stale venv silently
    # masks or introduces failures instead of testing the current deps.
    if not fresh:
        try:
            drifted = stamp.read_text(encoding="utf-8").strip() != fingerprint
        except OSError:
            drifted = True
        if drifted:
            shutil.rmtree(venv, ignore_errors=True)
            py = _venv_python(name)
            fresh = True
    if fresh:
        VENV_ROOT.mkdir(parents=True, exist_ok=True)
        subprocess.run([uv, "venv", str(venv)], check=True)
    if fresh or reinstall:
        if _has_pyproject(name):
            # Install the plugin editable with its dev extras. cwd = the plugin
            # dir so uv reads that pyproject's [tool.uv.sources] (vendored path
            # deps).
            spec = ".[dev]" if _has_dev_extra(name) else "."
            cmd = [uv, "pip", "install", "--python", str(py), "-e", spec]
            if spec == ".":
                cmd.append("pytest")   # no dev extra -> ensure a runner is present
            cmd.extend(RUNNER_DEPENDENCIES)
            subprocess.run(cmd, cwd=str(_plugin_dir(name)), check=True)
        else:
            # A pyproject-less plugin (e.g. a skill-script plugin like
            # harness-knowledge): there is nothing to install editable -- its
            # tests import the scripts directly (importlib). Just ensure a
            # pytest runner is present in the venv.
            subprocess.run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(py),
                    "pytest",
                    *RUNNER_DEPENDENCIES,
                ],
                cwd=str(_plugin_dir(name)), check=True,
            )
        stamp.write_text(fingerprint, encoding="utf-8")
    return py


def _test_file_groups(
    name: str, max_files: int, *, filtered: bool = False
) -> list[list[Path]]:
    tests = _plugin_dir(name) / "tests"
    files = sorted(tests.rglob("test_*.py"))
    if filtered and files:
        return [files]
    return partition(files, max_files) or [[]]


def run_plugin(
    name: str,
    uv: str,
    *,
    reinstall: bool,
    kexpr: str | None,
    limits: Limits,
    plugin_timeout: float,
    test_timeout: float,
    max_files_per_subsuite: int,
    guards: bool = False,
    collect_only: bool = False,
    allow_explicit_tiers: bool = False,
    allow_host_state: bool = False,
) -> int:
    if not _has_suite(name):
        print(f"[SKIP] {name}: no test suite")
        return 0
    label = "collect-only" if collect_only else ("guard tests" if guards else "pytest")
    state_mode = "host state (explicit opt-in)" if allow_host_state else "isolated state"
    print(f"[RUN ] {name}: preparing venv + {label} [{state_mode}] ...")
    py = _ensure_venv(name, uv, reinstall=reinstall)
    sandbox_parent = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    sandbox_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"ce-{name[:12]}-",
        dir=sandbox_parent,
        ignore_cleanup_errors=True,
    ) as raw_sandbox:
        sandbox = Path(raw_sandbox)
        basetemp = sandbox / "pytest"
        env = isolated_environment(
            os.environ,
            sandbox,
            allow_explicit_tiers=allow_explicit_tiers,
            allow_host_state=allow_host_state,
        )
        tools_path = str(REPO / "tools")
        env["PYTHONPATH"] = tools_path
        groups = _test_file_groups(
            name,
            max_files_per_subsuite,
            filtered=bool(guards or kexpr),
        )
        plugin_started = time.monotonic()
        for index, group in enumerate(groups, start=1):
            remaining = plugin_timeout - (time.monotonic() - plugin_started)
            if remaining <= 0:
                print(
                    f"[LIMIT] {name}: plugin aggregate timeout exceeded "
                    f"({plugin_timeout:g}s)",
                    file=sys.stderr,
                )
                return 124
            group_limits = Limits(
                wall_seconds=min(limits.wall_seconds, remaining),
                max_processes=limits.max_processes,
                max_memory_mb=limits.max_memory_mb,
                max_temp_mb=limits.max_temp_mb,
                poll_seconds=limits.poll_seconds,
            )
            group_temp = basetemp / f"group-{index}"
            group_temp.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                str(py),
                "-m",
                "pytest",
                "-q",
                f"--basetemp={group_temp}",
                "-p",
                PORTFOLIO_PLUGIN,
                f"--timeout={test_timeout:g}",
            ]
            if collect_only:
                cmd.append("--collect-only")
            if guards:
                cmd += ["-m", "guard"]
            if kexpr:
                cmd += ["-k", kexpr]
            cmd.extend(str(path.relative_to(_plugin_dir(name))) for path in group)
            if len(groups) > 1:
                print(
                    f"[RUN ] {name}: sub-suite {index}/{len(groups)} "
                    f"({len(group)} files)"
                )
            returncode = run_contained(
                cmd,
                cwd=_plugin_dir(name),
                env=env,
                sandbox=sandbox,
                limits=group_limits,
            )
            if returncode == 5 and (guards or kexpr):
                continue
            if returncode != 0:
                break
    # pytest exit code 5 == "no tests collected"; in --guards mode that just
    # means the plugin declares no guard-marked tests -- not a failure.
    if guards and returncode == 5:
        print(f"[SKIP] {name}: no guard-marked tests")
        return 0
    status = "PASS" if returncode == 0 else "FAIL"
    print(f"[{status}] {name} (exit {returncode})")
    return returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run plugin pytest suites in managed venvs.")
    ap.add_argument("plugins", nargs="*", help="plugin names (default: --changed)")
    ap.add_argument("--all", action="store_true", help="every plugin with a suite")
    ap.add_argument("--changed", action="store_true", help="plugins changed vs --base")
    ap.add_argument("--base", default="origin/main", help="diff base for --changed")
    ap.add_argument("--reinstall", action="store_true", help="rebuild the venv(s)")
    ap.add_argument("-k", dest="kexpr", default=None, help="pytest -k filter")
    ap.add_argument("--guards", action="store_true",
                    help="run only @pytest.mark.guard tests (fast structural/contract checks)")
    ap.add_argument("--collect-only", dest="collect_only", action="store_true",
                    help="build the venv and collect tests but do not run them "
                         "(cheap import/collection smoke)")
    ap.add_argument(
        "--admission-wait",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="wait up to SECONDS for the host-wide heavy-test slot (default: fail fast)",
    )
    ap.add_argument(
        "--allow-host-state",
        action="store_true",
        help="explicitly let tests use the caller's HOME/config/state; intended only "
             "for opt-in end-to-end checks that require host credentials",
    )
    ap.add_argument("--exclude", action="append", default=[], metavar="PLUGIN",
                    help="drop a plugin from the resolved set (repeatable)")
    ap.add_argument("--list", dest="list_only", action="store_true",
                    help="print the resolved target plugins as a JSON array and exit "
                         "(feeds a CI matrix); honors --all/--changed/names and --exclude")
    ap.add_argument("--pre-push", action="store_true",
                    help="hook mode: skip (exit 0) if uv is absent instead of failing")
    ap.add_argument("--timeout", "--subsuite-timeout", dest="subsuite_timeout",
                    type=float, default=300.0, metavar="SECONDS",
                    help="wall-clock budget per file-group sub-suite (default: 300)")
    ap.add_argument("--plugin-timeout", type=float, default=900.0, metavar="SECONDS",
                    help="aggregate wall-clock budget per plugin (default: 900)")
    ap.add_argument("--test-timeout", type=float, default=30.0, metavar="SECONDS",
                    help="timeout for each individual pytest item (default: 30)")
    ap.add_argument("--max-files-per-sub-suite", type=int, default=25,
                    metavar="COUNT",
                    help="maximum test files per sequential sub-suite (default: 25)")
    ap.add_argument("--max-processes", type=int, default=128, metavar="COUNT",
                    help="maximum contained processes per suite (default: 128)")
    ap.add_argument("--max-memory-mb", type=int, default=4096, metavar="MIB",
                    help="maximum process-tree memory per suite (default: 4096)")
    ap.add_argument("--max-temp-mb", type=int, default=2048, metavar="MIB",
                    help="maximum runner-owned temporary storage per suite (default: 2048)")
    ap.add_argument("--allow-explicit-tiers", action="store_true",
                    help="allow T3/T4 tests that are otherwise skipped by default")
    args = ap.parse_args(argv)
    limits = Limits(
        wall_seconds=args.subsuite_timeout,
        max_processes=args.max_processes,
        max_memory_mb=args.max_memory_mb,
        max_temp_mb=args.max_temp_mb,
    )
    try:
        limits.validate()
        if args.plugin_timeout <= 0:
            raise ValueError("plugin_timeout must be positive")
        if args.test_timeout <= 0:
            raise ValueError("test_timeout must be positive")
        if args.max_files_per_sub_suite <= 0:
            raise ValueError("max_files_per_sub_suite must be positive")
        if args.admission_wait < 0:
            raise ValueError("admission_wait must be non-negative")
        if args.allow_host_state and not args.allow_explicit_tiers:
            raise ValueError(
                "allow_host_state requires --allow-explicit-tiers"
            )
    except ValueError as exc:
        ap.error(str(exc))

    if args.all:
        targets = all_plugins_with_suites()
    elif args.plugins:
        targets = list(args.plugins)
    else:
        targets = changed_plugins(args.base)

    excluded = set(args.exclude)
    if excluded:
        targets = [t for t in targets if t not in excluded]

    if args.list_only:
        print(json.dumps(targets))
        return 0

    if not targets:
        print("No plugin suites to run.")
        return 0

    uv = shutil.which("uv")
    if not uv:
        msg = "uv not found on PATH -- cannot manage test venvs."
        if args.pre_push:
            print(f"[SKIP] {msg} (pre-push: not blocking the push)")
            return 0
        print(f"[ERROR] {msg} Install uv: https://docs.astral.sh/uv/", file=sys.stderr)
        return 2

    lease: SingleInstance | None = None
    needs_admission = not args.guards and not args.collect_only
    if needs_admission:
        if args.admission_wait:
            print(f"Waiting up to {args.admission_wait:g}s for the host test slot ...")
        try:
            lease = _acquire_admission(args.admission_wait)
        except AlreadyRunningError as exc:
            print(
                f"[BUSY] Another heavy plugin test run is active: {exc}. "
                "Use --admission-wait SECONDS to wait for it.",
                file=sys.stderr,
            )
            return 3

    try:
        print(f"Test targets: {', '.join(targets)}")
        failed: list[str] = []
        for name in targets:
            try:
                rc = run_plugin(
                    name,
                    uv,
                    reinstall=args.reinstall,
                    kexpr=args.kexpr,
                    limits=limits,
                    plugin_timeout=args.plugin_timeout,
                    test_timeout=args.test_timeout,
                    max_files_per_subsuite=args.max_files_per_sub_suite,
                    guards=args.guards,
                    collect_only=args.collect_only,
                    allow_explicit_tiers=args.allow_explicit_tiers,
                    allow_host_state=args.allow_host_state,
                )
            except (ContainmentError, subprocess.CalledProcessError) as exc:
                print(f"[FAIL] {name}: runner setup failed ({exc})", file=sys.stderr)
                rc = 1
            if rc != 0:
                failed.append(name)

        if failed:
            print(f"\nFAILED plugins: {', '.join(failed)}")
            return 1
        print(f"\nAll {len(targets)} plugin suite(s) passed.")
        return 0
    finally:
        if lease is not None:
            lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
