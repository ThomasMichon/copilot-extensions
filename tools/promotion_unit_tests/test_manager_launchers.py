"""Published historical launcher recognition without a host or installer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "promotion_manager_launcher_ownership",
    ROOT / "plugins/agent-worktrees/src/agent_worktrees/manager_launcher_ownership.py",
)
assert SPEC and SPEC.loader
ownership = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ownership)

LEGACY_STUBS = {
    "worktree-manager": (
        "#!/usr/bin/env bash\n"
        "# worktree-manager binstub (versioned) \u2014 resolves the current-version marker.\n"
        "set -euo pipefail\n"
        'root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"\n'
        'ver="$(cat "$root/current-version")"\n'
        'exec uv run --quiet --project "$root/versions/$ver" python -m worktree_manager "$@"\n'
    ),
    "worktree-manager.cmd": (
        "@echo off\r\nsetlocal\r\n"
        'set "PYTHONUTF8=1"\r\n'
        'if "%WORKTREE_MANAGER_ROOT%"=="" set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
        'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
        'uv run --quiet --project "%WORKTREE_MANAGER_ROOT%\\versions\\%VER%" '
        "python -m worktree_manager %*\r\n"
    ),
    "worktree-manager.ps1": (
        "# worktree-manager binstub (versioned) \u2014 resolves the current-version marker.\n"
        "$env:PYTHONUTF8 = '1'\n"
        '$root = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } '
        'else { Join-Path $env:USERPROFILE ".worktree-manager" }\n'
        '$ver = (Get-Content (Join-Path $root "current-version") -Raw).Trim()\n'
        '$slot = Join-Path (Join-Path $root "versions") $ver\n'
        "uv run --quiet --project $slot python -m worktree_manager @args\n"
    ),
}


@pytest.mark.parametrize("name", LEGACY_STUBS)
def test_exact_published_legacy_launcher_is_owned(name):
    assert ownership.owns_launcher(name, LEGACY_STUBS[name].encode("utf-8"))


@pytest.mark.parametrize("name", LEGACY_STUBS)
@pytest.mark.parametrize("change", ["append", "prepend", "replace", "truncate"])
def test_edited_legacy_launcher_is_not_owned(name, change):
    original = LEGACY_STUBS[name].encode("utf-8")
    changed = {
        "append": original + b"\necho edited\n",
        "prepend": b"echo edited\n" + original,
        "replace": original.replace(b"uv run", b"other run"),
        "truncate": original[:-1],
    }[change]
    assert not ownership.owns_launcher(name, changed)


@pytest.mark.parametrize("name", ["other", "", "WORKTREE-MANAGER", "subdir/worktree-manager"])
def test_a_known_body_does_not_authorize_an_unknown_command(name):
    assert not ownership.owns_launcher(name, LEGACY_STUBS["worktree-manager"].encode("utf-8"))


def test_missing_or_empty_launcher_is_not_owned():
    assert not ownership.owns_launcher("worktree-manager", None)
    assert not ownership.owns_launcher("worktree-manager", b"")
