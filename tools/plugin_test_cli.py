"""Argument construction for the repository's contained plugin test runner."""
from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path


def build_argument_parser(
    add_owned_scratch_argument: Callable[[argparse.ArgumentParser], None],
) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run plugin pytest suites in managed venvs.")
    ap.add_argument("plugins", nargs="*", help="plugin names (default: --changed)")
    ap.add_argument("--all", action="store_true", help="every plugin with a suite")
    ap.add_argument("--changed", action="store_true", help="plugins changed vs --base")
    ap.add_argument("--base", default="origin/main", help="diff base for --changed")
    ap.add_argument("--reinstall", action="store_true", help="rebuild the venv(s)")
    add_owned_scratch_argument(ap)
    ap.add_argument("-k", dest="kexpr", default=None, help="pytest -k filter")
    ap.add_argument(
        "--selection-file", type=Path, help="validated exact-node JSON selection; "
        "union whole test files and guards (requires exactly one named plugin)",
    )
    ap.add_argument(
        "--junit-dir", type=Path, help="retain per-group JUnit XML under a fresh PLUGIN "
        "subdirectory; never changes selection, admission or time/resource limits",
    )
    ap.add_argument(
        "--test-file", action="append", default=[], metavar="PATH",
        help="collect only this plugin-relative tests/test_*.py file (repeatable; "
             "requires exactly one named plugin)",
    )
    ap.add_argument("--guards", action="store_true",
                    help="run only @pytest.mark.guard tests (fast structural/contract checks)")
    ap.add_argument("--collect-only", dest="collect_only", action="store_true",
                    help="build the venv and collect tests but do not run them "
                         "(cheap import/collection smoke)")
    ap.add_argument("--prepare-only", dest="prepare_only", action="store_true",
                    help="build/update the venv(s) ONLY -- never imports a single test "
                         "module or conftest.py (unlike --collect-only, which still runs "
                         "pytest's own collection); for a network-enabled dependency-"
                         "preparation pass ahead of a network-disconnected real run")
    ap.add_argument(
        "--admission-wait", type=float, default=0.0, metavar="SECONDS",
        help="wait up to SECONDS for each target's environment (default: fail fast)",
    )
    ap.add_argument(
        "--allow-host-state", action="store_true",
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
                    type=float, default=None, metavar="SECONDS",
                    help="wall-clock budget per file-group sub-suite (default: 300, "
                         "higher for certain plugins -- see _SUBSUITE_TIMEOUT_OVERRIDES; "
                         "an explicit value here always wins)")
    ap.add_argument("--plugin-timeout", type=float, default=None, metavar="SECONDS",
                    help="aggregate wall-clock budget per plugin (default: 900, higher "
                         "for certain plugins -- see _PLUGIN_TIMEOUT_OVERRIDES; an "
                         "explicit value here always wins)")
    ap.add_argument("--test-timeout", type=float, default=None, metavar="SECONDS",
                    help="timeout for each individual pytest item (default: 30, higher "
                         "for certain plugins -- see _TEST_TIMEOUT_OVERRIDES; an "
                         "explicit value here always wins)")
    ap.add_argument(
        "--test-profile", choices=("standard", "busy-host-functional"), default="standard",
        help="explicit Codespaces installer or SSH snapshot functional qualification profile; "
             "busy-host-functional is not original-budget or performance evidence",
    )
    ap.add_argument("--max-files-per-sub-suite", type=int, default=None,
                    metavar="COUNT", help="maximum test files per sequential sub-suite "
                         "(default: 25, or 10 for agent-pull-requests)")
    ap.add_argument("--max-processes", type=int, default=128, metavar="COUNT",
                    help="maximum contained processes per suite (default: 128)")
    ap.add_argument("--max-memory-mb", type=int, default=4096, metavar="MIB",
                    help="maximum process-tree memory per suite (default: 4096)")
    ap.add_argument("--max-temp-mb", type=int, default=2048, metavar="MIB",
                    help="maximum runner-owned temporary storage per suite (default: 2048)")
    ap.add_argument("--allow-explicit-tiers", action="store_true",
                    help="allow T3/T4 tests that are otherwise skipped by default")
    return ap
