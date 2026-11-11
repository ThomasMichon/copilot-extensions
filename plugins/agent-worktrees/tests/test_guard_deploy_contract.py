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
        if event == "preToolUse":
            assert "pre-tool-hook.ps1" in command
            command += (_SCRIPTS / "pre-tool-hook.ps1").read_text("utf-8")
        assert "Set-Location -LiteralPath $env:USERPROFILE" in command
        assert "[IO.Path]::GetPathRoot(" in command
        assert "[IO.Directory]::SetCurrentDirectory((Get-Location).Path)" in command


def test_all_installers_deploy_every_py_hook():
    scripts = _py_hook_scripts() | {
        "statelessness_guard.py",
        "cross_repo_guard.py",
        "guarded_anchor_map.py",
        "anchor_write_guard.py",
        "anchor_shell_parser.py",
        "pr_supersede_guard.py",
        "nudge_status.py",
        "bind_nudge.py",
        "hook_runtime.py",
        "hook_process_identity.py",
        "hook_pre_relevance.py", "hook_pre_policy.py", "hook_policy_process.py",
        "hook_lifecycle_runtime.py",
        "native_source_context.py",
        "terminal_probe.py",
        "pretool_killswitch.py",
        "pre-tool-hook.ps1",
        "versioned_runtime_native.ps1",
        "hook-read-policy.json",
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


def test_legacy_python_installer_uses_manager_wrapper_manifest():
    manifest = json.loads((_PLUGIN / "launch-wrapper-assets.json").read_text("utf-8"))
    from agent_worktrees import launch_wrapper_assets

    assert tuple(manifest["files"]) == launch_wrapper_assets.WRAPPER_FILES
    python_installer = _INSTALLER_PY.read_text("utf-8")
    assert "load_manifest(" in python_installer
    assert "resolve_source_dir(" in python_installer
    assets = launch_wrapper_assets.load_manifest(_PLUGIN)
    source = launch_wrapper_assets.resolve_source_dir(_PLUGIN.parents[1], assets)
    for wrapper in (
        "pane-args.ps1", "pane-launch.ps1", "pane-wrapper.ps1", "pane-wrapper.sh",
        "terminal-launch.ps1",
    ):
        assert wrapper in manifest["files"]
        assert (source / wrapper).is_file()


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


def test_native_read_contract_matches_actual_guard_semantics(tmp_path, monkeypatch):
    import hashlib
    import importlib.util

    policy = json.loads((_SCRIPTS / "hook-read-policy.json").read_text("utf-8"))
    expected = {"statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py"}
    assert set(policy["guards"]) == expected
    spec = importlib.util.spec_from_file_location("native_contract_client", _SCRIPTS / "hook_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.syspath_prepend(str(_SCRIPTS))
    for name in expected:
        text = (_SCRIPTS / name).read_text(encoding="utf-8-sig")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == policy["guards"][name], "Run tools/render-hook-read-policy.py after guard changes"
        guard = client._load_sibling(name)
        assert guard is not None
        def forbidden(*args, **kwargs):
            pytest.fail("native read exemption would skip meaningful guard work")
        for helper in ("find_repo_root", "load_guarded_roots", "load_worktree_anchors", "_current_login"):
            if hasattr(guard, helper):
                monkeypatch.setattr(guard, helper, forbidden)
        for tool in ("view", "rg", "glob"):
            assert guard.decide({"toolName": tool, "cwd": str(tmp_path), "toolArgs": {"path": "test"}}, env={}, home=tmp_path) is None


def test_native_write_contract_matches_actual_target_and_map_readers():
    import hashlib

    policy = json.loads((_SCRIPTS / "hook-read-policy.json").read_text("utf-8"))
    assert set(policy["nativeWriteDependencies"]) == {"tool_call_forms.py", "guarded_anchor_map.py"}
    for name, digest in policy["nativeWriteDependencies"].items():
        text = (_SCRIPTS / name).read_text(encoding="utf-8-sig")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == digest, "Run tools/render-hook-read-policy.py after helper changes"


@pytest.mark.parametrize("argument_key,raw", [
    ("toolArgs", False), ("toolArgs", True),
    ("tool_input", False), ("tool_input", True),
])
def test_native_shell_contract_preserves_original_guard_decisions(tmp_path, monkeypatch, argument_key, raw):
    import hashlib
    import importlib.util
    from test_managed_session import fixture_linked_worktree, fixture_project

    anchor, _registry = fixture_project(tmp_path)
    workspace, _admin = fixture_linked_worktree(tmp_path, anchor)
    config = workspace / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("stateless: true\n", encoding="utf-8")
    policy = json.loads((_SCRIPTS / "hook-read-policy.json").read_text("utf-8"))
    spec = importlib.util.spec_from_file_location("native_shell_contract_client", _SCRIPTS / "hook_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.syspath_prepend(str(_SCRIPTS))
    relevance = client._load_sibling("hook_pre_relevance.py")
    assert tuple(policy["nativeShellReadCommands"]) == relevance.SHELL_READ_HEADS
    text = (_SCRIPTS / "hook_pre_relevance.py").read_text(encoding="utf-8-sig")
    assert policy["nativeShellReadDependencies"] == {
        "hook_pre_relevance.py": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    env = {"AGENT_HOME": str(tmp_path)}
    protected_command = f'Set-Content "{workspace / "efforts" / "private.md"}" -Value fixture'
    for command in relevance.SHELL_READ_HEADS:
        keys = policy["nativeShellReadCommandKeys"]
        for index, key in enumerate(keys[:1] if raw else keys):
            arguments = {earlier: None for earlier in keys[:index]}
            arguments.update({key: command, "description": "Fixture read"})
            if index + 1 < len(keys):
                arguments[keys[index + 1]] = protected_command
            event = {
                "toolName": "powershell", "cwd": str(workspace),
                argument_key: command if raw else arguments,
            }
            if argument_key == "toolArgs":
                event["tool_input"] = {"command": protected_command}
            assert relevance.is_irrelevant(event, False, client._load_sibling)
            for name in policy["guards"]:
                guard = client._load_sibling(name)
                assert tuple(keys) == guard.CMD_ARG_KEYS
                kwargs = {"anchors": [{"name": "example", "path": str(anchor)}]} if name == "anchor_write_guard.py" else {}
                assert guard.decide(event, env=env, home=tmp_path, **kwargs) is None
    guard = client._load_sibling("statelessness_guard.py")
    protected = {
        **event, argument_key: protected_command if raw else {"command": protected_command},
    }
    assert guard.decide(protected, env=env, home=tmp_path)["permissionDecision"] == "deny"


@pytest.mark.parametrize("declaration", [
    "()",
    "['Get-Location']",
    "('Get-Location', 'Get-Location')",
    "('Get-Location', False)",
    "tuple(['Get-Location'])",
    repr(tuple(f"Read-{index}-" + "a" * 80 for index in range(64))),
])
def test_native_shell_renderer_rejects_invalid_or_oversized_declarations(tmp_path, declaration):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "native_shell_contract_renderer", _PLUGIN.parents[1] / "tools" / "render-hook-read-policy.py",
    )
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    for name in (*renderer.GUARDS, *renderer.WRITE_DEPENDENCIES):
        (tmp_path / name).write_bytes((_SCRIPTS / name).read_bytes())
    (tmp_path / "hook_pre_relevance.py").write_text("SHELL_READ_HEADS = " + declaration + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        renderer.render(tmp_path)


@pytest.mark.parametrize("declaration", [
    None, "()", "('',)", "['command']", "('command', 'command')", "('command', False)",
    "tuple(['command'])", "('different',)", "('command',)\nCMD_ARG_KEYS = ('command',)",
    "reordered",
    repr(tuple(f"key-{index}" for index in range(129))),
])
def test_native_shell_renderer_rejects_invalid_original_key_selection(tmp_path, declaration):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "native_selection_renderer", _PLUGIN.parents[1] / "tools" / "render-hook-read-policy.py",
    )
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    for name in (*renderer.GUARDS, *renderer.WRITE_DEPENDENCIES, "hook_pre_relevance.py"):
        (tmp_path / name).write_bytes((_SCRIPTS / name).read_bytes())
    guard = tmp_path / renderer.GUARDS[-1]
    if declaration == "reordered":
        keys = renderer.literal_strings(guard.read_text("utf-8"), "CMD_ARG_KEYS")
        declaration = repr(keys[1:] + keys[:1])
    changed = "" if declaration is None else "CMD_ARG_KEYS = " + declaration
    source, count = re.subn(r"(?m)^CMD_ARG_KEYS = .*$", lambda _: changed, guard.read_text("utf-8"))
    assert count == 1
    guard.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError):
        renderer.render(tmp_path)
