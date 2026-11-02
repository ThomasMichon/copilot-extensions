"""Bridge preserves the Worktrees-owned setup boundary when adding ACP options."""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import _peer_launch, local_launch_arguments, session_lifecycle_cli, transport, venue_plugins
from agent_bridge.local_launch_arguments import _worktrees_setup_script

SETUP_SOURCE = Path(__file__).resolve().parents[2] / "agent-worktrees/scripts/default-setup.ps1"


@pytest.fixture(autouse=True)
def admitted_target(monkeypatch):
    # Owner admission is covered by the repository-hosting suites.
    monkeypatch.setattr("agent_bridge.hosting_policy.require_target_hosting", AsyncMock())
    monkeypatch.setattr(local_launch_arguments, "_worktrees_setup_script", lambda: SETUP_SOURCE)


def test_setup_identity_uses_legacy_owner_root(monkeypatch):
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_owner_root", lambda: Path.home() / ".agent-bridge")
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_installation_context", lambda: "")
    assert _worktrees_setup_script() == Path.home() / ".agent-worktrees/scripts/default-setup.ps1"


def test_setup_identity_uses_validated_same_cell_owner(monkeypatch, tmp_path):
    cell = tmp_path / "marketplaces/example"
    own = cell / "plugins/agent-bridge"
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_owner_root", lambda: own)
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_installation_context", lambda: "owner-receipt")

    def validate(owner, root, context):
        assert (owner, root, context) == ("agent-bridge", own, "owner-receipt")
        return {"cellRoot": str(cell)}

    monkeypatch.setattr(_peer_launch, "validate_owner", validate)
    assert _worktrees_setup_script() == cell / "plugins/agent-worktrees/scripts/default-setup.ps1"


def test_setup_identity_rejects_invalid_context(monkeypatch, tmp_path):
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_owner_root", lambda: tmp_path)
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_installation_context", lambda: "bad-receipt")

    def refuse(*_args):
        raise ValueError("invalid receipt")

    monkeypatch.setattr(_peer_launch, "validate_owner", refuse)
    with pytest.raises(_peer_launch.ContextRefused, match="installation context refused"):
        _worktrees_setup_script()


def test_setup_identity_does_not_guess_legacy_for_unbound_cell(monkeypatch, tmp_path):
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_owner_root", lambda: tmp_path / "agent-bridge")
    monkeypatch.setattr(session_lifecycle_cli, "_agent_bridge_installation_context", lambda: "")
    with pytest.raises(_peer_launch.ContextRefused, match="non-legacy bridge"):
        _worktrees_setup_script()


def setup_command(native, *, script=SETUP_SOURCE):
    return [
        "pwsh", "-NoProfile", "-File", str(script),
        "-ExecutionDirectory", r"C:\workspace with spaces",
        "-NativeArgumentsJson", json.dumps({"args": native, "extra": "retained"}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("prepare", [False, True])
async def test_resolved_setup_keeps_final_native_arguments_in_envelope(
    tmp_path, monkeypatch, resume, prepare,
):
    native = ["--resume=original-head"] if resume else ["--model", "configured-model"]
    command = setup_command(native)
    target = transport.SpawnTarget(
        project="example", worktree_id="retained-workspace",
        native_resume_id="original-head" if resume else None,
        local_plugin_preparation=prepare,
        static_plugin_args={"base": ["--allow-all"], "automatic": ["--plugin-dir", "old"]},
        copilot_args=["--allow-all", "--plugin-dir", "old", "--name", "two words"],
    )
    plan = {"launch": {
        "cmd": command, "work_dir": str(tmp_path), "worktree_id": "retained-workspace",
        "env": {"PLAN_VALUE": "retained"},
    }}
    monkeypatch.setattr(transport, "_resolve_worktree", AsyncMock(return_value=plan))
    barrier = AsyncMock(return_value=["--plugin-dir", "prepared path"])
    monkeypatch.setattr(venue_plugins, "prepare_local_plugins", barrier)
    args, cwd, env = await transport.resolve_local_launch(target)
    index = args.index("-NativeArgumentsJson") + 1
    assert index == len(args) - 1
    assert Path(args[0]).stem.casefold() == "pwsh"
    assert args[1:index] == command[1:index]
    assert json.loads(args[index]) == {
        "args": [
            *native, "--acp", "--stdio", "--no-auto-update",
            "--allow-all", "--plugin-dir", "prepared path" if prepare else "old",
            "--name", "two words",
        ],
        "extra": "retained",
    }
    assert json.loads(command[-1])["args"] == native
    assert target.copilot_args == ["--allow-all", "--plugin-dir", "old", "--name", "two words"]
    assert target.worktree_id == "retained-workspace"
    assert cwd == str(tmp_path) and env["PLAN_VALUE"] == "retained"
    assert barrier.await_count == int(prepare)


@pytest.mark.skipif(os.name != "nt", reason="complete Windows Worktrees launch producer")
@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["wrapped", "deferred", "prepared"])
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("prepare_plugins", [False, True])
async def test_complete_worktrees_plan_preserves_final_native_boundary(
    tmp_path, monkeypatch, shape, resume, prepare_plugins,
):
    for key in ("HOME", "USERPROFILE", "AGENT_WORKTREES_CONFIG_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
    monkeypatch.syspath_prepend(str(SETUP_SOURCE.parents[1] / "src"))
    for library in ("lazy-cli-dispatch", "work-coalescing-singleton"):
        monkeypatch.syspath_prepend(str(SETUP_SOURCE.parents[3] / "libs" / library / "src"))
    from agent_worktrees import __main__ as owner
    from agent_worktrees import config, launch_seed_exec, manager_launch_cli

    root = SETUP_SOURCE.parents[1]
    monkeypatch.setattr(owner.inst, "install_dir", lambda: root)
    monkeypatch.setattr(owner, "resolve_launch_pref_flags", lambda *_: [])
    manager = tmp_path / "manager"
    manager.mkdir()
    (manager / "pane-wrapper.ps1").write_text(
        "# worktree-manager:native-console-owner-v1\n", encoding="utf-8",
    )
    monkeypatch.setattr(manager_launch_cli, "_worktree_manager_root", lambda: manager)
    monkeypatch.setattr(manager_launch_cli, "_usable_worktree_manager_launcher_dir", lambda: manager)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record_path = tmp_path / "test-worktree.yaml"
    # Static test input: constructing a launch plan must not create a managed worktree.
    record_path.write_text(
        json.dumps({
            "worktree_id": "test-worktree", "branch": "worktree/test",
            "worktree_path": str(workspace), "repo": "example",
            "machine": "test", "platform": "windows",
        }),
        encoding="utf-8",
    )
    settings = config.Config(
        srcroot=str(tmp_path), machine="test", platform="windows", repo_name="example",
        repos={"example": config.RepoConfig(
            anchor=str(tmp_path / "anchor"), worktree_root=str(tmp_path / "worktrees"),
        )},
    )
    command = owner._build_launch_cmd(
        settings,
        SimpleNamespace(
            recovery=True,
            copilot_args=["--model", "configured-model", "--name", "--", "--", "literal-tail"],
        ),
        str(workspace),
        preflight=SimpleNamespace(error=None, config_root_path=None),
    )
    if shape != "wrapped":
        command = launch_seed_exec.deferred_command(
            command, record_path, admission_only=True,
            native_resume_session="original-head" if resume else None,
            prepare_native=shape == "prepared",
        )
    original = list(command)
    payload_index = command.index("-NativeArgumentsJson") + 1
    before = json.loads(command[payload_index])
    target = transport.SpawnTarget(
        project="example", worktree_id="test-worktree",
        native_resume_id="original-head" if resume else None,
        local_plugin_preparation=prepare_plugins,
        static_plugin_args={"base": ["--allow-all"], "automatic": ["--plugin-dir", "old"]},
        copilot_args=["--allow-all", "--plugin-dir", "old", "--name", "two words"],
    )
    monkeypatch.setattr(transport, "_resolve_worktree", AsyncMock(return_value={"launch": {
        "cmd": command, "record_path": str(record_path), "work_dir": str(workspace),
        "worktree_id": "test-worktree", "env": {"PLAN_VALUE": "retained"},
    }}))
    barrier = AsyncMock(return_value=["--plugin-dir", "prepared path"])
    monkeypatch.setattr(venue_plugins, "prepare_local_plugins", barrier)
    args, cwd, env = await transport.resolve_local_launch(target)
    additions = [
        "--acp", "--stdio", "--no-auto-update", "--allow-all",
        "--plugin-dir", "prepared path" if prepare_plugins else "old", "--name", "two words",
    ]
    assert len(args) == len(command)
    assert Path(args[0]).stem.casefold() == Path(command[0]).stem.casefold()
    assert args[1:payload_index] == command[1:payload_index]
    assert args[payload_index + 1:] == command[payload_index + 1:]
    assert json.loads(args[payload_index])["args"] == [
        *before["args"][:-2], *additions, *before["args"][-2:],
    ]
    assert command == original
    assert cwd == str(workspace) and env["PLAN_VALUE"] == "retained"
    assert target.worktree_id == "test-worktree"
    assert target.native_resume_id == ("original-head" if resume else None)
    assert barrier.await_count == int(prepare_plugins)


@pytest.mark.parametrize("command", [
    ["copilot", "--resume=original-head"],
    ["bash", "/launch/default-setup.sh", "--recovery"],
    ["pwsh", "-File", r"C:\launch\default-setup.ps1", "-Recovery"],
    ["custom-launcher", "-NativeArgumentsJson", "opaque"],
    ["pwsh", "-File", r"C:\launch\custom.ps1", "-NativeArgumentsJson", "opaque"],
])
def test_legacy_and_custom_launch_commands_keep_append_semantics(command):
    additions = ["--acp", "--stdio", "--no-auto-update", "--name", "literal ' $value"]
    assert transport._append_local_launch_arguments(command, additions) == command + additions


@pytest.mark.parametrize("payload", ["not-json", "[]", "{}", '{"args":"text"}', '{"args":[1]}'])
def test_invalid_native_envelope_is_not_reinterpreted_as_legacy(payload):
    command = setup_command([])
    command[-1] = payload
    with pytest.raises(RuntimeError, match="Invalid worktree native argument envelope"):
        transport._append_local_launch_arguments(command, ["--acp"])


@pytest.mark.parametrize("suffix", [[], ["{}", "-NativeArgumentsJson", "{}"]])
def test_missing_or_duplicate_envelope_refuses(suffix):
    command = setup_command([])[:-1] + suffix
    with pytest.raises(RuntimeError, match="Invalid worktree native argument envelope"):
        transport._append_local_launch_arguments(command, ["--acp"])


def test_setup_parameter_names_are_case_insensitive():
    command = ["pwsh", "-FILE", str(SETUP_SOURCE), "-nativeargumentsjson", '{"args":[]}']
    args = transport._append_local_launch_arguments(command, ["--stdio"])
    assert json.loads(args[-1]) == {"args": ["--stdio"]}


@pytest.mark.parametrize("native, expected", [
    (["--model", "example", "--", "literal-tail"],
     ["--model", "example", "--acp", "--stdio", "--", "literal-tail"]),
    (["--name", "--", "--", "literal-tail"],
     ["--name", "--", "--acp", "--stdio", "--", "literal-tail"]),
    (["--name", "--"], ["--name", "--", "--acp", "--stdio"]),
])
def test_options_precede_real_terminator_but_not_option_values(native, expected):
    args = transport._append_local_launch_arguments(setup_command(native), ["--acp", "--stdio"])
    assert json.loads(args[-1])["args"] == expected


@pytest.mark.parametrize("copied", [False, True])
@pytest.mark.parametrize("payload", ["opaque", '{"args":["custom-data"]}'])
def test_same_basename_custom_setup_retains_opaque_append_contract(tmp_path, payload, copied):
    script = tmp_path / "default-setup.ps1"
    if copied:
        shutil.copy2(SETUP_SOURCE, script)
    else:
        script.write_text("param([string]$NativeArgumentsJson)\n", encoding="utf-8")
    command = setup_command([], script=script)
    command[-1] = payload
    additions = ["--acp", "--stdio"]
    assert transport._append_local_launch_arguments(command, additions) == command + additions


def test_missing_custom_setup_is_not_inspected_or_rewritten(tmp_path):
    command = setup_command([], script=tmp_path / "default-setup.ps1")
    assert transport._append_local_launch_arguments(command, ["--acp"]) == [*command, "--acp"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows PowerShell setup consumer")
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "wrapped"])
@pytest.mark.parametrize("prefix, tail", [
    (["--resume=original-head"], []),
    (["--resume=original-head"], ["--", "literal-tail"]),
    (["--resume=original-head", "--name", "--"], ["--", "literal-tail"]),
], ids=["resume", "literal-tail", "terminator-as-name"])
def test_real_setup_consumer_rejects_old_shape_and_preserves_fixed_arguments(
    tmp_path, monkeypatch, shell, wrapped, prefix, tail,
):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    script = tmp_path / "default-setup.ps1"
    shutil.copy2(SETUP_SOURCE, script)
    monkeypatch.setattr(local_launch_arguments, "_worktrees_setup_script", lambda: script)
    backend = tmp_path / "capture.ps1"
    backend.write_text(
        "[Console]::Out.Write((ConvertTo-Json -InputObject @($args) -Compress))\n"
        "exit 37\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ, "PATH": "", "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT": str(tmp_path / "absent-runtime"),
    }
    command = [
        executable, "-NoProfile", "-NoLogo", "-File", str(script), "-Recovery",
        "-CopilotPath", str(backend), "-ExecutionDirectory", str(tmp_path),
        "-NativeArgumentsJson", json.dumps({"args": [*prefix, *tail]}),
    ]
    if wrapped:
        wrapper = tmp_path / "launch-command.ps1"
        shutil.copy2(SETUP_SOURCE.with_name("launch-command.ps1"), wrapper)
        command = [
            executable, "-NoProfile", "-NoLogo", "-File", str(wrapper), "--recovery", "--",
            *command,
        ]
    additions = ["--acp", "--stdio", "--no-auto-update", "--name", "literal ' $value", "--plugin-dir", ""]
    old = subprocess.run(
        command + additions, cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert old.returncode != 0
    assert "Native argument envelope cannot be combined" in old.stderr
    assert not old.stdout

    target = transport.SpawnTarget(
        project="example", worktree_id="retained-workspace", native_resume_id="original-head",
        copilot_args=additions[3:],
    )
    monkeypatch.setattr(transport, "_resolve_worktree", AsyncMock(return_value={"launch": {
        "cmd": command, "work_dir": str(tmp_path), "worktree_id": "retained-workspace", "env": environment,
    }}))
    args, cwd, env = asyncio.run(transport.resolve_local_launch(target))
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 37, result.stderr
    assert json.loads(result.stdout) == ["-C", str(tmp_path), *prefix, *additions, *tail]
