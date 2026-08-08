"""Guard-deploy contract: every preToolUse guard wired in ``hooks.json`` MUST be
deployed to ``~/.agent-worktrees/bin/`` by ALL installer paths.

This pins the invariant whose violation silently disabled ``cross_repo_guard``:
the hook references ``bin/<guard>.py`` behind a ``Test-Path``/``-f`` existence
gate, so an installer that forgets to copy the script turns the guard into a
no-op with NO error. The three installers (the PowerShell + bash bootstrap
scripts and the Python ``installer.deploy_wrappers``) drifted apart; this test
derives the required guard set from ``hooks.json`` (the single source of truth)
and asserts each installer deploys every one of them, plus that the script
actually exists under ``scripts/``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1]
_HOOKS = _PLUGIN / "hooks.json"
_SCRIPTS = _PLUGIN / "scripts"
_INSTALL_PS1 = _SCRIPTS / "install.ps1"
_INSTALL_SH = _SCRIPTS / "install.sh"
_INSTALLER_PY = _PLUGIN / "src" / "agent_worktrees" / "installer.py"

# A guard is a preToolUse hook whose command runs a python script from
# ``~/.agent-worktrees/bin/<name>.py``.
_BIN_PY_RE = re.compile(r"bin[\\/]+([A-Za-z0-9_]+\.py)")


def _pretooluse_guard_scripts() -> set[str]:
    data = json.loads(_HOOKS.read_text("utf-8"))
    guards: set[str] = set()
    for hook in data.get("hooks", {}).get("preToolUse", []):
        for key in ("powershell", "bash"):
            cmd = hook.get(key) or ""
            guards.update(_BIN_PY_RE.findall(cmd))
    return guards


def test_pretooluse_guards_are_nonempty_and_exist():
    guards = _pretooluse_guard_scripts()
    # Sanity: we actually found the wired guards (not a parsing miss).
    assert {"statelessness_guard.py", "cross_repo_guard.py",
            "anchor_write_guard.py"} <= guards
    for g in guards:
        assert (_SCRIPTS / g).is_file(), f"hooks.json wires {g} but scripts/{g} is missing"


def test_all_installers_deploy_every_pretooluse_guard():
    guards = _pretooluse_guard_scripts()
    installers = {
        "install.ps1": _INSTALL_PS1.read_text("utf-8"),
        "install.sh": _INSTALL_SH.read_text("utf-8"),
        "installer.py": _INSTALLER_PY.read_text("utf-8"),
    }
    missing: list[str] = []
    for name, text in installers.items():
        for g in guards:
            if g not in text:
                missing.append(f"{name} does not deploy {g}")
    assert not missing, (
        "preToolUse guard(s) wired in hooks.json but not deployed by an "
        "installer (they would silently no-op):\n  " + "\n  ".join(missing))
