"""Fresh Manager bootstrap leaves Worktrees publication to its own installer."""
from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
HELPERS = (
    "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
    "agent-worktrees-update-json.awk",
)


@pytest.fixture
def admission(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", raising=False)
    monkeypatch.setenv(
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR",
        str(home / ".agent-worktrees/control-plane-providers.d"),
    )
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    api = runpy.run_path(str(REPO / "worktree-manager/scripts/update_entry.py"))
    return api["validate_manager_ownership"], home, local_bin


@pytest.mark.parametrize("name", HELPERS)
@pytest.mark.parametrize("crlf", [False, True])
def test_manager_admission_leaves_worktrees_helpers_untouched(admission, name, crlf):
    validate, home, local_bin = admission
    older = b"# old or damaged Worktrees helper\n"
    original = older.replace(b"\n", b"\r\n") if crlf else older
    command = local_bin / name
    command.write_bytes(original)
    validate(home / ".worktree-manager", REPO / "worktree-manager")
    assert command.read_bytes() == original


def test_manager_admission_uses_only_base_python(admission):
    _validate, home, local_bin = admission
    (local_bin / HELPERS[0]).write_bytes(b"damaged Worktrees helper\n")
    code = """
import pathlib, runpy, sys
home = pathlib.Path(sys.argv[2])
api = runpy.run_path(sys.argv[1])
api["validate_manager_ownership"](home / ".worktree-manager", pathlib.Path(sys.argv[3]))
"""
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B", "-c", code,
         str(REPO / "worktree-manager/scripts/update_entry.py"), str(home),
         str(REPO / "worktree-manager")],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", HELPERS)
def test_manager_admission_does_not_follow_worktrees_helper_links(admission, name, tmp_path):
    validate, home, local_bin = admission
    target = tmp_path / "unrelated"
    target.write_bytes(b"unrelated file\n")
    try:
        (local_bin / name).symlink_to(target)
    except OSError as error:
        pytest.skip(f"fixture symlink creation unavailable: {error}")
    validate(home / ".worktree-manager", REPO / "worktree-manager")
    assert (local_bin / name).is_symlink()
    assert target.read_bytes() == b"unrelated file\n"
