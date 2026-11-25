"""Windows container provider policy transport."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from agent_bridge import portable_policy
from agent_bridge.provider_hosting import _provider_command
from agent_bridge.session_host.container_transport import _run_provider


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows provider shims")
@pytest.mark.parametrize("literal", ["one line", "one line\nsecond line", None], ids=["control", "multiline", "policy"])
async def test_container_policy_windows_provider_roundtrip(
    tmp_path: Path, literal: str | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = tmp_path / "agent-containers.cmd"
    batch.write_text('@echo off\npwsh -NoProfile -File "%~dpn0.ps1" %*\n', encoding="ascii")
    batch.with_suffix(".ps1").write_text(
        "[Console]::WriteLine((ConvertTo-Json -Compress -InputObject @($args)))\n"
        "[Console]::OpenStandardInput().CopyTo([Console]::OpenStandardOutput())\n",
        encoding="ascii",
    )
    with monkeypatch.context() as patch:
        if literal is None:
            patch.setattr("shutil.which", lambda _: None)
        prefix = _provider_command({"provider_command": [str(batch), "runtime-proxy"]})
    assert prefix is not None
    if literal is None:
        assert Path(prefix[0]) == Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    monkeypatch.setattr("agent_bridge.hosting_policy.marketplace_id", lambda: "selected-source")
    command, payload = (
        portable_policy.guest_policy_check("/actual checkout", "launch", text_transport=True)
        if literal is None else (f"echo '{literal}'", b"portable policy archive\n")
    )
    guest = ["exec", "-i", "-u", "guest", "instance", "bash", "-lc", command]
    rc, output, error = await _run_provider([*prefix, *guest], timeout=15, input_bytes=payload)
    assert rc == 0, error
    argv, _, received = output.partition(b"\n")
    assert json.loads(argv) == ["runtime-proxy", *guest]
    assert received == payload
