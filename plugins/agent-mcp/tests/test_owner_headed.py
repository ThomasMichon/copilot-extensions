"""MCP-owned retained receiving wiring, with unchanged ordinary launch semantics."""

import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest
from agent_procutil import no_window_kwargs

from agent_mcp import owner_headed, owner_headed_bundle, owner_overlay_pipe
from agent_remote_driver.headed import launcher
from agent_remote_driver.session_host.launcher import main as host_main


def _entry():
    return {
        "command": str(Path(sys.executable).absolute()),
        "args": ["-I", "-m", "agent_mcp", "forward", "--config", "literal"],
        "env": {"FIXTURE_PRIVATE": "not-in-the-client-command"}, "tools": ["*"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "native-cleanup", "cancelled"])
async def test_selected_producer_publishes_actual_child_and_preserves_original_wait(monkeypatch, outcome):
    from agent_procutil.owned_process import OwnedCleanupError

    monkeypatch.setattr(owner_headed, "os", SimpleNamespace(name="nt"))
    section = Mock(binding="a" * 64, inherited_handle=77)
    section.__enter__ = Mock(return_value=section)
    section.__exit__ = Mock(return_value=False)
    import agent_mcp.owner_section as sections

    monkeypatch.setattr(sections, "OwnerSection", lambda: section)
    transferred = []
    transfer = Mock(path=r"\\.\pipe\fixture")
    def send():
        transferred.append(True)
        if outcome != "success":
            raise OSError("fixture transfer cancellation")

    transfer.send.side_effect = send
    monkeypatch.setattr(owner_overlay_pipe, "OverlayPipe", lambda overlay: transfer)
    original = {"mcpServers": {"chosen": _entry(), "other": _entry()}}
    before = copy.deepcopy(original)
    child = SimpleNamespace(pid=1234, _handle=900)
    fault = asyncio.CancelledError() if outcome == "cancelled" else OwnedCleanupError(child, object())
    seen = []

    async def run(argv, *, native_handoff, **kwargs):
        assert not transferred
        assert "not-in-the-client-command" not in repr(argv)
        assert argv[-2:] == ["--additional-mcp-config", "@" + transfer.path]
        assert native_handoff.inherited_handles == (77,)
        native_handoff.on_suspended(child)
        section.publish.assert_called_once_with(900, 1234)
        transfer.bind.assert_called_once_with(900, 1234)
        await asyncio.sleep(0)
        seen.append(kwargs)
        if outcome != "success":
            raise fault
        return 23

    monkeypatch.setattr(launcher, "run_headed", run)
    result = owner_headed.run_headed(
        ["copilot.exe", "--session-id", "selected"], configuration=original,
        names=["chosen"], session_id="selected",
    )
    if outcome == "success":
        assert await result == 23
    else:
        with pytest.raises(type(fault)) as caught:
            await result
        assert caught.value is fault
    assert transferred == [True] and seen == [{"session_id": "selected"}]
    assert original == before
    transfer.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"auth_wait": {}}, {"native_handoff": object()},
])
async def test_unqualified_receiver_refuses_before_native_resources(monkeypatch, extra):
    monkeypatch.setattr(owner_headed, "os", SimpleNamespace(name="nt"))
    create = Mock(side_effect=AssertionError("not admitted"))
    import agent_mcp.owner_section as sections

    monkeypatch.setattr(sections, "OwnerSection", create)
    with pytest.raises(ValueError, match="non-auth"):
        await owner_headed.run_headed(
            ["copilot.exe"], configuration={}, names=["chosen"], **extra,
        )
    create.assert_not_called()


def test_retained_bundle_is_stdlib_loadable_and_contains_no_input_configuration(tmp_path):
    archive, digest = owner_headed_bundle.build_headed_bundle(tmp_path)
    copied = tmp_path / "copied.pyz"
    copied.write_bytes(archive.read_bytes())
    with zipfile.ZipFile(copied) as payload:
        assert "agent_mcp/owner_headed.py" in payload.namelist()
        assert "agent_remote_driver/headed/native_handoff.py" in payload.namelist()
        assert "zdd/private_windows_native.py" in payload.namelist()
        assert not any(name.endswith(".json") and "config" in name for name in payload.namelist())
    result = subprocess.run(
        [sys._base_executable, "-I", "-S", str(copied), "--support-json"],
        cwd=tmp_path, env={key: value for key, value in os.environ.items() if not key.startswith("PYTHON")},
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert "headed" in json.loads(result.stdout)["modes"]
    assert json.loads(result.stdout)["mcp_origin"] == ["windows-headed-native-v1"]
    assert owner_headed_bundle.build_headed_bundle(tmp_path) == (archive, digest)
    archive.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        owner_headed_bundle.build_headed_bundle(tmp_path)


def test_older_core_refuses_before_creating_a_retained_receiver(tmp_path, monkeypatch):
    import agent_remote_driver.session_host.launcher as core

    def support(_argv):
        print(json.dumps({"contract": "agent-remote-driver.session-host", "version": 1}))
        return 0

    monkeypatch.setattr(core, "main", support)
    with pytest.raises(ValueError, match="does not declare"):
        owner_headed_bundle.build_headed_bundle(tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_retained_command_carries_only_existing_configuration_path_and_selection(tmp_path):
    configuration = tmp_path / "entries.json"
    configuration.write_text(json.dumps({"mcpServers": {"chosen": _entry()}}), encoding="utf-8")
    command = owner_headed_bundle.prepare_headed_command(
        ["copilot.exe", "--session-id", "selected"], configuration_path=configuration,
        names=["chosen"], session_id="selected", attempt_directory=tmp_path / "attempt",
        state_file=tmp_path / "state.json", cwd=str(tmp_path),
    )
    assert "--mcp-config" in command and str(configuration) in command
    assert command[command.index("--server") + 1] == "chosen"
    assert "not-in-the-client-command" not in repr(command)
    assert configuration.read_text().find("not-in-the-client-command") != -1


def test_headless_mode_cannot_silently_discard_an_explicit_headed_receiver():
    receiver = Mock(side_effect=AssertionError("not a headed launch"))
    with pytest.raises(SystemExit) as result:
        host_main(["--", "copilot", "--acp"], headed_run=receiver)
    assert result.value.code == 2
    receiver.assert_not_called()


def test_fixed_receiver_uses_core_argument_admission_and_original_result(tmp_path):
    observed = []

    async def receive(argv, **kwargs):
        observed.append((argv, kwargs))
        return 23

    assert host_main([
        "--mode", "headed", "--session-id", "selected",
        "--attempt-directory", str(tmp_path / "attempt"),
        "--state-file", str(tmp_path / "state.json"),
        "--cwd", str(tmp_path), "--", "copilot", "--session-id", "selected",
    ], headed_run=receive) == 23
    assert observed[0][0] == ["copilot", "--session-id", "selected"]
    assert observed[0][1]["session_id"] == "selected"
