"""MCP-owned retained receiving wiring, with unchanged ordinary launch semantics."""

import asyncio
import copy
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest
from agent_procutil import no_window_kwargs

from agent_mcp import owner_headed, owner_headed_bundle, owner_overlay_pipe
from agent_remote_driver.headed import launcher
from agent_remote_driver.session_host.launcher import main as host_main
from zdd.private_protocol import PrivateIPCError, Refusal


def _entry():
    return {
        "command": str(Path(sys.executable).absolute()),
        "args": ["-I", "-m", "agent_mcp", "forward", "--config", "literal"],
        "env": {"FIXTURE_PRIVATE": "not-in-the-client-command"}, "tools": ["*"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "native-cleanup", "cancelled"])
@pytest.mark.parametrize("finalizers_fail", [False, True])
async def test_selected_producer_publishes_actual_child_and_preserves_original_wait(
    monkeypatch, outcome, finalizers_fail,
):
    from agent_procutil.owned_process import OwnedCleanupError

    monkeypatch.setattr(owner_headed, "os", SimpleNamespace(name="nt"))
    section = Mock(binding="a" * 64, inherited_handle=77)
    import agent_mcp.owner_section as sections

    monkeypatch.setattr(sections, "OwnerSection", lambda: section)
    transferred = []
    transfer = Mock(path=r"\\.\pipe\fixture", inherited_handle=88)
    def send():
        transferred.append(True)
        if outcome != "success":
            raise OSError("fixture transfer cancellation")

    transfer.send.side_effect = send
    if finalizers_fail:
        transfer.close.side_effect = OSError("fixture transfer close failed")
        section.close.side_effect = OSError("fixture section close failed")
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
        assert native_handoff.inherited_handles == (77, 88)
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
    if outcome == "success" and not finalizers_fail:
        assert await result == 23
    else:
        expected = OwnedCleanupError if outcome == "success" else type(fault)
        with pytest.raises(expected) as caught:
            await result
        if outcome != "success":
            assert caught.value is fault
        if finalizers_fail:
            retained = caught.value if outcome == "success" else caught.value.mcp_cleanup
            assert isinstance(retained, OwnedCleanupError)
            assert retained.cleanup_resources == (
                ("configuration-transfer", transfer), ("owner-section", section),
            )
            transfer.close.side_effect = section.close.side_effect = None
            retained.retry_cleanup(deadline=time.monotonic() + 1)
            assert transfer.close.call_count == section.close.call_count == 2
    assert transferred == [True] and seen == [{"session_id": "selected"}]
    assert original == before
    assert transfer.close.call_count == section.close.call_count == (2 if finalizers_fail else 1)


@pytest.mark.asyncio
async def test_transfer_failure_is_redacted_before_original_native_wait_finishes(monkeypatch, capsys):
    import agent_mcp.owner_section as sections

    monkeypatch.setattr(owner_headed, "os", SimpleNamespace(name="nt"))
    section = Mock(binding="a" * 64, inherited_handle=77)
    monkeypatch.setattr(sections, "OwnerSection", lambda: section)
    transfer = Mock(path=r"\\.\pipe\fixture", inherited_handle=88)
    transfer.send.side_effect = OSError("sensitive-fixture-error")
    monkeypatch.setattr(owner_overlay_pipe, "OverlayPipe", lambda overlay: transfer)
    reported, release = asyncio.Event(), asyncio.Event()
    report = owner_headed._report

    def observe(message):
        report(message)
        reported.set()

    monkeypatch.setattr(owner_headed, "_report", observe)

    async def run(_argv, *, native_handoff, **_kwargs):
        native_handoff.on_suspended(SimpleNamespace(pid=1234, _handle=900))
        await release.wait()
        return 23

    monkeypatch.setattr(launcher, "run_headed", run)
    task = asyncio.create_task(owner_headed.run_headed(
        ["copilot.exe"], configuration={"mcpServers": {"chosen": _entry()}}, names=["chosen"],
    ))
    try:
        await asyncio.wait_for(reported.wait(), 2)
        assert not task.done()
        message = capsys.readouterr().err
        assert "configuration transfer failed" in message
        assert "sensitive-fixture-error" not in message
        transfer.close.assert_not_called()
    finally:
        release.set()
        with pytest.raises(OSError, match="sensitive-fixture-error"):
            await task


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


@pytest.mark.parametrize("name,session", [
    ("chosen", "selected"), ("--chosen", "selected"), ("chosen", "--selected"),
])
def test_retained_command_round_trips_configuration_path_and_literal_selection(
    tmp_path, monkeypatch, name, session,
):
    configuration = tmp_path / "entries.json"
    configuration.write_text(json.dumps({"mcpServers": {name: _entry()}}), encoding="utf-8")
    native = ["copilot.exe", "--session-id", session]
    command = owner_headed_bundle.prepare_headed_command(
        native, configuration_path=configuration,
        names=[name], session_id=session, attempt_directory=tmp_path / "attempt",
        state_file=tmp_path / "state.json", cwd=str(tmp_path),
    )
    assert "--mcp-config" in command and str(configuration) in command
    assert "not-in-the-client-command" not in repr(command)
    assert configuration.read_text().find("not-in-the-client-command") != -1
    observed = []

    async def receive(argv, **kwargs):
        observed.append((argv, kwargs))
        return 23

    monkeypatch.setattr(owner_headed, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(owner_headed, "run_headed", receive)
    assert owner_headed.main(command[4:]) == 23
    assert observed[0][0] == native
    assert observed[0][1]["names"] == [name]
    assert observed[0][1]["session_id"] == session


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


@pytest.mark.asyncio
@pytest.mark.parametrize("disappears", [True, False], ids=["natural-exit-race", "different-owner"])
async def test_native_mux_cleanup_requires_identity_or_confirmed_absence(monkeypatch, disappears):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    from test_owner_headed_windows import _settle_mux

    def result(code=0, output=""):
        return SimpleNamespace(returncode=code, stdout=output, stderr="")

    query = Mock(side_effect=[
        result(), result(output="" if disappears else "other-incarnation"), result(1),
    ])
    mux = SimpleNamespace(query=query, target="fixture")
    if disappears:
        await _settle_mux(mux, "original")
        assert query.call_count == 3
    else:
        with pytest.raises(AssertionError):
            await _settle_mux(mux, "original")
    assert not any(call.args[0] == "kill-session" for call in query.call_args_list)


@pytest.mark.parametrize("failed_handle", [11, 22], ids=["pipe-close", "process-close"])
def test_transferred_channel_retains_each_failed_handle_for_exact_retry(monkeypatch, failed_handle):
    closed = []
    listeners = iter([13, 11])
    failed = False

    def close(handle):
        nonlocal failed
        if handle is None:
            return
        closed.append(handle)
        if handle == failed_handle and not failed:
            failed = True
            raise PrivateIPCError(Refusal.IO)

    def duplicate(_current, handle, _target, result, access, inherit, options):
        assert (handle, access, inherit, options) == (13, 0x100000, True, 0)
        ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p)).contents.value = 12
        return True

    api = SimpleNamespace(
        listener=lambda *args, **kwargs: next(listeners),
        duplicate_process=lambda *args: 22,
        identity=lambda handle, pid: SimpleNamespace(pid=pid, principal="fixture"),
        current_sid=lambda: "fixture", alive=lambda handle: None,
        peer_pid=lambda *args, **kwargs: 42, close=close,
        k=SimpleNamespace(
            GetCurrentProcess=lambda: 1, DuplicateHandle=duplicate,
            CreateFileW=lambda *args: 14,
        ),
    )
    monkeypatch.setattr(owner_overlay_pipe, "Native", lambda: api)
    monkeypatch.setattr(
        owner_overlay_pipe.WindowsChannel, "_io",
        lambda self, kind, buffer, count, deadline: count,
    )
    transfer = owner_overlay_pipe.OverlayPipe({"synthetic": "configuration"})
    transfer.bind(900, 42)
    with pytest.raises(PrivateIPCError):
        transfer.send()
    assert transfer._channel is not None
    assert failed_handle in (transfer._channel._pipe, transfer._channel._process)
    transfer.close()
    assert closed.count(failed_handle) == 2
    assert set(closed) == {11, 12, 13, 14, 22}
    assert transfer._channel is None and transfer._process is None


def test_partial_reservation_preparation_retains_failed_release_for_retry(monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError

    api = SimpleNamespace(
        listener=Mock(side_effect=[13, PrivateIPCError(Refusal.IO)]),
        k=SimpleNamespace(CreateFileW=lambda *args: 14),
        close=Mock(side_effect=[PrivateIPCError(Refusal.IO), None, None]),
    )
    monkeypatch.setattr(owner_overlay_pipe, "Native", lambda: api)
    with pytest.raises(OwnedCleanupError) as failed:
        owner_overlay_pipe.OverlayPipe({"synthetic": "not-published"})
    name, transfer = failed.value.cleanup_resources[0]
    assert name == "configuration-transfer" and transfer._fence == 13
    assert [call.args[0] for call in api.close.call_args_list] == [13, 14]
    failed.value.retry_cleanup(deadline=time.monotonic() + 1)
    assert transfer._fence is None
    assert [call.args[0] for call in api.close.call_args_list] == [13, 14, 13]
