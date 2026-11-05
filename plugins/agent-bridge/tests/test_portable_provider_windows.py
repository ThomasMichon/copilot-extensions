"""Actual Windows provider boundary, with an inert PowerShell peer."""

import json
import sys

import pytest

from agent_bridge.session_targeting_cli import _portable_provider_run


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell argv/stdin boundary")
def test_portable_provider_preserves_literal_plugin_metadata_and_seed_stdin(tmp_path):
    peer = tmp_path / "provider with spaces.ps1"
    peer.write_text(
        """
param([Parameter(ValueFromRemainingArguments=$true)][string[]] $remaining)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
@{arguments=@($remaining); input=[Console]::In.ReadToEnd()} | ConvertTo-Json -Compress
exit 0
""",
        encoding="utf-8",
    )
    arguments = [
        "copilot", "fixture", "--receiving-support", "portable",
        "--stage-plugin-spec", '{"source":"fixture@marketplace","enable":true}',
        "--plugin-root", 'C:\\path with spaces\\literal"quote', "--seed-file", "-",
    ]
    result = _portable_provider_run(
        [str(peer), *arguments], input="private seed\nsecond line",
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"arguments": arguments, "input": "private seed\nsecond line"}
