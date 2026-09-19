"""Diagnostic stdin is validated locally and retained by the owned SSH lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_codespaces import __main__ as cli


@pytest.mark.parametrize("options", [
    [],
    ["--remote-cmd", ""],
    ["--remote-cmd", " \t"],
    ["--stdio", "--remote-cmd", "cat"],
    ["--interactive-command", "cat"],
    ["--interactive-command-file", "missing-command"],
])
def test_incompatible_modes_precede_claims(ssh_runtime, tmp_path, options):
    events, manager = ssh_runtime
    payload = tmp_path / "input"
    payload.write_bytes(b"data")
    with pytest.raises(SystemExit) as exc:
        cli.main(["ssh", "example-space", "--stdin-file", str(payload), *options])
    assert exc.value.code == 2
    assert events == []
    manager.ensure_connected.assert_not_awaited()


@pytest.mark.parametrize("kind", ["missing", "directory", "empty-path", "too-large", "unreadable"])
def test_invalid_file_precedes_claims(ssh_runtime, tmp_path, monkeypatch, kind):
    events, manager = ssh_runtime
    payload = tmp_path / "input"
    if kind == "directory":
        payload.mkdir()
    elif kind in ("too-large", "unreadable"):
        payload.write_bytes(b"12345")
        if kind == "too-large":
            monkeypatch.setattr(cli, "_STDIN_FILE_MAX_BYTES", 4)
        else:
            def denied(*a, **k):
                raise PermissionError("read denied")

            monkeypatch.setattr(cli.Path, "open", denied)
    with pytest.raises(SystemExit) as exc:
        cli.main([
            "ssh", "example-space", "--remote-cmd", "cat",
            "--stdin-file", "" if kind == "empty-path" else str(payload),
        ])
    assert exc.value.code == 2
    assert events == []
    manager.ensure_connected.assert_not_awaited()


@pytest.mark.parametrize("file_command", [False, True])
@pytest.mark.parametrize("payload", [b"", b"\xef\xbb\xbf\x00\xff\r\n  $HOME %PATH% &\n", bytes(range(256))])
def test_binary_snapshot_and_owned_lifecycle(ssh_runtime, tmp_path, monkeypatch, file_command, payload):
    events, manager = ssh_runtime
    path = tmp_path / "input with spaces"
    path.write_bytes(payload)
    before = path.stat().st_mtime_ns
    captured = []

    async def execute(name, command, **kwargs):
        assert name == "example-space"
        assert command.endswith("cat'")
        assert "relay-start" in events and "relay-stop" not in events
        captured.append(kwargs)
        events.append("execute")
        assert path.stat().st_mtime_ns == before
        path.write_bytes(b"changed after validation")
        await asyncio.sleep(.025)
        return SimpleNamespace(stdout="output", stderr="", exit_code=23, timed_out=False)

    manager.exec_command = AsyncMock(side_effect=execute)
    options = ["--remote-cmd", "cat"]
    if file_command:
        command = tmp_path / "command"
        command.write_text("cat", encoding="utf-8")
        options = ["--remote-cmd-file", str(command)]
    assert cli.main([
        "ssh", "example-space", *options, "--stdin-file", str(path),
        "--timeout", "7", "--no-plugin-staging",
    ]) == 23
    assert captured == [{"timeout": 7.0, "input_bytes": payload}]
    assert events.index("claim") < events.index("lock") < events.index("execute")
    assert events.index("execute") < events.index("relay-stop") < events.index("unlock")
    manager.disconnect.assert_awaited_once_with("example-space")


@pytest.mark.parametrize("outcome", ["timeout", "exception", "cancelled"])
def test_cleanup_after_failed_stdin_command(ssh_runtime, tmp_path, monkeypatch, outcome):
    events, manager = ssh_runtime
    payload = tmp_path / "input"
    payload.write_bytes(b"payload")
    if outcome == "timeout":
        manager.exec_command = AsyncMock(return_value=SimpleNamespace(
            stdout="partial", stderr="", exit_code=-1, timed_out=True,
        ))
    elif outcome == "exception":
        manager.exec_command = AsyncMock(side_effect=OSError("channel failed"))
    else:
        async def blocked(*a, **k):
            await asyncio.sleep(30)

        manager.exec_command = AsyncMock(side_effect=blocked)
    argv = [
        "ssh", "example-space", "--remote-cmd", "cat", "--stdin-file", str(payload),
        "--connect-timeout", ".05" if outcome == "cancelled" else "10",
    ]
    if outcome == "exception":
        with pytest.raises(OSError, match="channel failed"):
            cli.main(argv)
    else:
        assert cli.main(argv) == 124
    assert events[-2:] == ["relay-stop", "unlock"]
    manager.disconnect.assert_awaited_once_with("example-space")


def test_without_stdin_file_preserves_existing_call(ssh_runtime):
    _, manager = ssh_runtime
    manager.exec_command = AsyncMock(return_value=SimpleNamespace(
        stdout="", stderr="", exit_code=0, timed_out=False,
    ))
    assert cli.main(["ssh", "example-space", "--remote-cmd", "true", "--no-relay"]) == 0
    assert manager.exec_command.call_args.kwargs == {"timeout": 60.0}


def test_bounded_read_rejects_growth_after_stat(tmp_path, monkeypatch):
    import argparse
    import io
    from pathlib import Path

    path = tmp_path / "input"
    path.write_bytes(b"1234")
    monkeypatch.setattr(cli, "_STDIN_FILE_MAX_BYTES", 4)

    class GrowingFile(io.BytesIO):
        def fileno(self):
            return 123

    monkeypatch.setattr(Path, "open", lambda *a, **k: GrowingFile(b"12345"))
    monkeypatch.setattr(cli.os, "fstat", lambda _: path.stat())
    args = argparse.Namespace(stdin_file=str(path), remote_cmd="cat")
    with pytest.raises(SystemExit) as exc:
        cli._normalize_stdin_file(argparse.ArgumentParser(), args)
    assert exc.value.code == 2
    assert args.stdin_bytes is None
