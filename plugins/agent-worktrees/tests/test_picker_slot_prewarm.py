"""Installer contract for warming a fresh Picker runtime slot before activation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent_worktrees.picker_tui import prewarm

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN / "scripts"


def test_prewarm_imports_the_pre_app_picker_modules(monkeypatch):
    imported: list[str] = []
    monkeypatch.setattr(prewarm.importlib, "import_module", imported.append)

    prewarm.main()

    assert imported == list(prewarm.PICKER_PREWARM_MODULES)
    assert imported == [
        "agent_worktrees.picker_tui.engine",
        "agent_worktrees.picker_tui.data_ssh",
        "agent_worktrees.picker_tui.frame_health",
    ]


def test_prewarm_does_not_load_runtime_config():
    script = """
import runpy
from agent_worktrees import config

def fail(*args, **kwargs):
    raise AssertionError("prewarm loaded runtime config")

config.load_config = fail
runpy.run_module("agent_worktrees.picker_tui.prewarm", run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _activation_body(installer: str, start: str, end: str) -> str:
    text = (_SCRIPTS / installer).read_text(encoding="utf-8")
    return text.split(start, 1)[1].split(end, 1)[0]


def test_installers_prewarm_new_slot_before_marking_it_complete():
    powershell = _activation_body(
        "install.ps1", "function Invoke-VersionedActivate {", "# === end install-contract"
    )
    posix = _activation_body(
        "install.sh", "_versioned_activate() {", "# === end install-contract"
    )

    ps_warm = "& $VenvPython -m agent_worktrees.picker_tui.prewarm"
    sh_warm = 'PYTHONPATH= "$VENV_PYTHON" -m agent_worktrees.picker_tui.prewarm'
    assert ps_warm in powershell
    assert sh_warm in posix
    assert powershell.index(ps_warm) < powershell.index("Invoke-VersionedMarkComplete")
    assert posix.index(sh_warm) < posix.index("_versioned_mark_complete")
    assert "Picker prewarm gate" in powershell
    assert "Picker prewarm gate" in posix
