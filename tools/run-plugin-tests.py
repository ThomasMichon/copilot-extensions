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
* **Cached venvs** live under ``.test-venvs/<plugin>`` (git-ignored) and are
  reused across runs; ``--reinstall`` rebuilds one.
* **Windows-safe temp.** Passes a randomized ``--basetemp`` so pytest's tmp
  cleanup does not trip the ``pytest-current`` junction ``PermissionError`` on
  Windows (teardown noise that would otherwise mask a green run).
* **Fail-closed on test failures**, but in ``--pre-push`` mode it degrades
  gracefully (warn + skip, exit 0) only when the *tooling* (uv) is genuinely
  absent -- never blocking a push just because a dev box lacks uv, while still
  enforcing real failures wherever it can run.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGINS = REPO / "plugins"
VENV_ROOT = REPO / ".test-venvs"


def _plugin_dir(name: str) -> Path:
    return PLUGINS / name


def _has_suite(name: str) -> bool:
    d = _plugin_dir(name)
    tests = d / "tests"
    return d.is_dir() and tests.is_dir() and any(tests.glob("test_*.py"))


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


def _ensure_venv(name: str, uv: str, *, reinstall: bool) -> Path:
    """Create (or reuse) the cached dev venv for ``name`` and return its python."""
    venv = VENV_ROOT / name
    py = _venv_python(name)
    if reinstall and venv.exists():
        shutil.rmtree(venv, ignore_errors=True)
    fresh = not py.exists()
    if fresh:
        VENV_ROOT.mkdir(parents=True, exist_ok=True)
        subprocess.run([uv, "venv", str(venv)], check=True)
    if fresh or reinstall:
        # Install the plugin editable with its dev extras. cwd = the plugin dir
        # so uv reads that pyproject's [tool.uv.sources] (vendored path deps).
        spec = ".[dev]" if _has_dev_extra(name) else "."
        cmd = [uv, "pip", "install", "--python", str(py), "-e", spec]
        if spec == ".":
            cmd.append("pytest")   # no dev extra -> ensure a runner is present
        subprocess.run(cmd, cwd=str(_plugin_dir(name)), check=True)
    return py


def run_plugin(name: str, uv: str, *, reinstall: bool, kexpr: str | None,
               guards: bool = False) -> int:
    if not _has_suite(name):
        print(f"[SKIP] {name}: no test suite")
        return 0
    label = "guard tests" if guards else "pytest"
    print(f"[RUN ] {name}: preparing venv + {label} ...")
    py = _ensure_venv(name, uv, reinstall=reinstall)
    basetemp = Path(os.environ.get("TEMP", "/tmp")) / f"ce-bt-{name}-{random.randint(0, 1_000_000)}"
    cmd = [str(py), "-m", "pytest", "-q", f"--basetemp={basetemp}"]
    if guards:
        cmd += ["-m", "guard"]
    if kexpr:
        cmd += ["-k", kexpr]
    proc = subprocess.run(cmd, cwd=str(_plugin_dir(name)), check=False)
    # pytest exit code 5 == "no tests collected"; in --guards mode that just
    # means the plugin declares no guard-marked tests -- not a failure.
    if guards and proc.returncode == 5:
        print(f"[SKIP] {name}: no guard-marked tests")
        return 0
    status = "PASS" if proc.returncode == 0 else "FAIL"
    print(f"[{status}] {name} (exit {proc.returncode})")
    return proc.returncode


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
    ap.add_argument("--pre-push", action="store_true",
                    help="hook mode: skip (exit 0) if uv is absent instead of failing")
    args = ap.parse_args(argv)

    if args.all:
        targets = all_plugins_with_suites()
    elif args.plugins:
        targets = list(args.plugins)
    else:
        targets = changed_plugins(args.base)

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

    print(f"Test targets: {', '.join(targets)}")
    failed: list[str] = []
    for name in targets:
        try:
            rc = run_plugin(name, uv, reinstall=args.reinstall, kexpr=args.kexpr,
                            guards=args.guards)
        except subprocess.CalledProcessError as exc:
            print(f"[FAIL] {name}: venv/install failed ({exc})", file=sys.stderr)
            rc = 1
        if rc != 0:
            failed.append(name)

    if failed:
        print(f"\nFAILED plugins: {', '.join(failed)}")
        return 1
    print(f"\nAll {len(targets)} plugin suite(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
