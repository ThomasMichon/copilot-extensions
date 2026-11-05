"""Actual PS5 peer invocation; no daemon, credentials or provider is involved."""

import json
import sys

import pytest

from agent_codespaces import bridge_receiving


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell argv/stdin boundary")
def test_selected_powershell_peer_preserves_literal_arguments_and_stdin(tmp_path, monkeypatch):
    peer = tmp_path / "selected bridge.ps1"
    peer.write_text(
        r"""
param([Parameter(ValueFromRemainingArguments=$true)][string[]] $remaining)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$payload=[Console]::In.ReadToEnd()
if ($remaining[0] -eq 'receiving-support' -and $remaining[1] -eq 'capabilities') {
    @{
        schema='agent-bridge.receiving-support'; version=1; owner_mux=1
        mux=@{version=1; platform='posix'; transport='command-stdin'; registration=1}
        policy_context='{"schema":"agent-bridge.receiving-policy","version":1,"marketplaceId":""}'
        daemon_port=12001
    } | ConvertTo-Json -Compress -Depth 10
} elseif ($remaining[0] -eq 'receiving-support') {
    @{
        schema='agent-bridge.receiving-command'; version=1; command='selected receiver'
        stdin_base64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
    } | ConvertTo-Json -Compress
} else {
    @{arguments=@($remaining); input=$payload} | ConvertTo-Json -Compress
}
exit 0
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(bridge_receiving.worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(bridge_receiving.shutil, "which", lambda name: str(peer))
    bridge = bridge_receiving.BridgeReceiving()
    request = {"literal": 'spaces "quotes" $(not-a-command); ending\\'}
    command, payload = bridge.launch(request)
    assert command == "selected receiver"
    assert json.loads(payload) == request
    arguments = ["spaces here", 'a"quote', "$(not-a-command);", "ending\\"]
    result = bridge.run(
        ["bridge", "literal", *arguments], input="private stdin",
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"arguments": ["literal", *arguments], "input": "private stdin"}
