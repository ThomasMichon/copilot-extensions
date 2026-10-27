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

import pytest

pytestmark = pytest.mark.guard

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


def test_python_hook_runners_recover_from_invalid_windows_cwd():
    data = json.loads(_HOOKS.read_text("utf-8"))
    for event in _PY_HOOK_EVENTS:
        hooks = data["hooks"][event]
        assert len(hooks) == 1
        command = hooks[0]["powershell"]
        assert "Set-Location -LiteralPath $env:USERPROFILE" in command
        assert "[IO.Path]::GetPathRoot((Get-Location).Path)" in command
        assert "[IO.Directory]::SetCurrentDirectory((Get-Location).Path)" in command


def test_all_installers_deploy_every_py_hook():
    scripts = _py_hook_scripts() | {
        "statelessness_guard.py",
        "cross_repo_guard.py",
        "anchor_write_guard.py",
        "anchor_shell_parser.py",
        "pr_supersede_guard.py",
        "nudge_status.py",
        "bind_nudge.py",
        "hook_runtime.py",
        "hook_pre_relevance.py",
        "hook_lifecycle_runtime.py",
        "native_source_context.py",
        "terminal_probe.py",
        "pretool_killswitch.py",
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
        if text.find("anchor_shell_parser.py") > text.find("anchor_write_guard.py"):
            missing.append(
                f"{name} deploys anchor_shell_parser.py after its importer"
            )
        if text.find("hook_runtime.py") > text.find("hook_client.py"):
            missing.append(
                f"{name} deploys hook_runtime.py after its importer"
            )
    assert not missing, (
        "python hook(s) wired in hooks.json but not deployed by an "
        "installer (they would silently no-op):\n  " + "\n  ".join(missing))


def test_wrappers_deploy_before_monitor_activation():
    ps1 = _INSTALL_PS1.read_text("utf-8")
    sh = _INSTALL_SH.read_text("utf-8")
    lifecycle = (_PLUGIN / "scripts" / "runtime_install.py").read_text("utf-8")
    settle = lifecycle.split("def settle_candidate(", 1)[1].split("\ndef main(", 1)[0]
    assert (
        settle.index("_load_installed(") < settle.index("_publish_helpers(")
        < settle.index("runtime.activate(root, version")
        < settle.index("cutover.activate_after_update(")
    )
    assert "Invoke-VersionedActivate" not in ps1.rsplit("switch ($Action) {", 1)[1]
    assert "_versioned_activate" not in sh.rsplit('case "$ACTION" in', 1)[1]
    assert "$scope = if ($Action -in @('install', 'update')) { 'wrappers' } else { 'resolvers' }" in ps1
    assert '[[ "$ACTION" != install && "$ACTION" != update ]] || scope=wrappers' in sh


def test_all_installers_deploy_platform_pane_wrapper():
    installers = {
        "install.ps1": _INSTALL_PS1.read_text("utf-8"),
        "install.sh": _INSTALL_SH.read_text("utf-8"),
        "installer.py": _INSTALLER_PY.read_text("utf-8"),
    }
    manifest = json.loads((_PLUGIN / "launch-wrapper-assets.json").read_text("utf-8"))
    from agent_worktrees import launch_wrapper_assets

    assert tuple(manifest["files"]) == launch_wrapper_assets.WRAPPER_FILES
    required = {
        "install.ps1": "pane-wrapper.ps1",
        "install.sh": "pane-wrapper.sh",
        "installer.py": ("load_manifest(", "resolve_source_dir("),
    }
    missing: list[str] = []
    for name, expected in required.items():
        for wrapper in (
            (expected,) if isinstance(expected, str) else expected
        ):
            if wrapper not in installers[name]:
                missing.append(f"{name} does not deploy {wrapper}")
    for wrapper in ("pane-launch.ps1", "pane-wrapper.ps1", "pane-wrapper.sh"):
        if wrapper not in manifest["files"]:
            missing.append(f"launch-wrapper-assets.json does not list {wrapper}")
    assert not missing, "\n".join(missing)


@pytest.mark.guard
def test_bootstrap_state_guards_are_deployed_before_every_bootstrap_caller():
    import ast
    sources = {
        "install.ps1": _INSTALL_PS1.read_text("utf-8"),
        "install.sh": _INSTALL_SH.read_text("utf-8"),
    }
    tree = ast.parse(_INSTALLER_PY.read_text("utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "deploy_wrappers")
    inventory = next(node.iter for node in ast.walk(owner) if isinstance(node, ast.For)
                     and isinstance(node.iter, ast.Tuple)
                     and any(isinstance(item, ast.Constant) and item.value == "bootstrap-check.ps1"
                             for item in node.iter.elts))
    python_names = ast.literal_eval(inventory)
    publication = ast.parse((_SCRIPTS / "runtime_install_publication.py").read_text("utf-8"))
    publication_names = next(ast.literal_eval(node.value) for node in publication.body
                             if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == "HOOKS"
                                     for target in node.targets))
    for suffix in ("ps1", "sh"):
        guard = f"bootstrap-killswitch-guard.{suffix}"
        caller = f"bootstrap-check.{suffix}"
        assert python_names.index(guard) < python_names.index(caller)
        assert publication_names.index(guard) < publication_names.index(caller)
        for name, source in sources.items():
            lines = [line for line in source.splitlines() if guard in line and caller in line]
            assert len(lines) == 1, name
            assert lines[0].index(guard) < lines[0].index(caller), name
