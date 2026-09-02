"""Guard-deploy contract: every python hook wired in ``hooks.json`` MUST be
deployed to ``~/.agent-worktrees/bin/`` by ALL installer paths.

This pins the invariant whose violation silently disabled ``cross_repo_guard``:
the hook references ``bin/<script>.py`` behind a ``Test-Path``/``-f`` existence
gate, so an installer that forgets to copy the script turns the hook into a
no-op with NO error. The three installers (the PowerShell + bash bootstrap
scripts and the Python ``installer.deploy_wrappers``) drifted apart; this test
derives the required script set from ``hooks.json`` (the single source of truth)
-- across the ``preToolUse`` guards AND the ``postToolUse`` disposition nudge --
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

# A python hook runs a script from ``~/.agent-worktrees/bin/<name>.py``. Both the
# preToolUse guards and the postToolUse nudge use this deploy contract.
_BIN_PY_RE = re.compile(r"bin[\\/]+([A-Za-z0-9_]+\.py)")
_PY_HOOK_EVENTS = ("preToolUse", "postToolUse")


def _py_hook_scripts() -> set[str]:
    data = json.loads(_HOOKS.read_text("utf-8"))
    scripts: set[str] = set()
    for event in _PY_HOOK_EVENTS:
        for hook in data.get("hooks", {}).get(event, []):
            for key in ("powershell", "bash"):
                cmd = hook.get(key) or ""
                scripts.update(_BIN_PY_RE.findall(cmd))
    return scripts


def test_py_hooks_are_nonempty_and_exist():
    scripts = _py_hook_scripts()
    # Sanity: we actually found the wired hooks (not a parsing miss).
    assert scripts == {"hook_client.py"}
    for g in scripts:
        assert (_SCRIPTS / g).is_file(), f"hooks.json wires {g} but scripts/{g} is missing"


def test_all_installers_deploy_every_py_hook():
    scripts = _py_hook_scripts() | {
        "statelessness_guard.py",
        "cross_repo_guard.py",
        "anchor_write_guard.py",
        "nudge_status.py",
        "bind_nudge.py",
    }
    installers = {
        "install.ps1": _INSTALL_PS1.read_text("utf-8"),
        "install.sh": _INSTALL_SH.read_text("utf-8"),
        "installer.py": _INSTALLER_PY.read_text("utf-8"),
    }
    missing: list[str] = []
    for name, text in installers.items():
        for g in scripts:
            if g not in text:
                missing.append(f"{name} does not deploy {g}")
    assert not missing, (
        "python hook(s) wired in hooks.json but not deployed by an "
        "installer (they would silently no-op):\n  " + "\n  ".join(missing))


def test_wrappers_deploy_before_monitor_activation():
    ps1 = _INSTALL_PS1.read_text("utf-8")
    sh = _INSTALL_SH.read_text("utf-8")
    assert not re.search(
        r"Invoke-VersionedActivate\)\s*\{ exit 1 \}.{0,200}Deploy-Wrappers",
        ps1,
        re.DOTALL,
    )
    assert not re.search(
        r"_versioned_activate \|\| exit 1.{0,200}deploy_wrappers",
        sh,
        re.DOTALL,
    )


def test_all_installers_deploy_platform_pane_wrapper():
    installers = {
        "install.ps1": _INSTALL_PS1.read_text("utf-8"),
        "install.sh": _INSTALL_SH.read_text("utf-8"),
        "installer.py": _INSTALLER_PY.read_text("utf-8"),
    }
    required = {
        "install.ps1": "pane-wrapper.ps1",
        "install.sh": "pane-wrapper.sh",
        "installer.py": ("pane-wrapper.ps1", "pane-wrapper.sh"),
    }
    missing: list[str] = []
    for name, expected in required.items():
        for wrapper in (
            (expected,) if isinstance(expected, str) else expected
        ):
            if wrapper not in installers[name]:
                missing.append(f"{name} does not deploy {wrapper}")
    assert not missing, "\n".join(missing)
