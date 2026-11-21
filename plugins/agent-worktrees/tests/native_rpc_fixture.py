"""Bounded fixture RPC clocks without process queries or IO in the live interval."""

from __future__ import annotations

import re


RPC_PHASES = (
    "rpc-context",
    "rpc-dispatch",
    "rpc-entry",
    "rpc-payload",
    "rpc-elevation",
    "rpc-encoded",
    "rpc-socket-created",
    "rpc-connect-start",
    "rpc-connect-returned",
    "rpc-connected",
    "rpc-sent",
    "rpc-received",
    "rpc-result",
)
RPC_DETAIL_PHASES = (
    "rpc-connect-call-returned",
    "rpc-connect-catch",
    "rpc-connect-unwrapped",
)
_BUFFERED_PHASES = (
    RPC_PHASES
    + RPC_DETAIL_PHASES
    + (
        "json-entry",
        "json-utility-load",
        "json-utility-ready",
        "json-web-load",
        "json-resolver-type",
        "json-resolver-create",
        "json-serializer-create",
        "json-deserialize",
        "json-deserialized",
    )
)

PHASE_BUFFER_WRITER = """
function Add-NativeWriteFixturePhase([string]$Name) {
    $capture = $ExecutionContext.SessionState.PSVariable.GetValue('global:nativeWriteRpcCapture')
    if ($null -eq $capture) { return $false }
    if (-not $capture['Map'].ContainsKey($Name)) { $capture['Error'] = $true; return $true }
    $index = [int]$capture['Used']
    if ($index -ge $capture['Qpc'].Length) { $capture['Truncated'] = $true; return $true }
    $capture['PhaseIds'][$index] = [int]$capture['Map'][$Name]
    $capture['Qpc'][$index] = [Diagnostics.Stopwatch]::GetTimestamp()
    $capture['Ticks'][$index] = if ($capture['RelativeClock'] -is [Diagnostics.Stopwatch]) {
        $capture['RelativeClock'].ElapsedMilliseconds
    } else { [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
    $capture['Used'] = $index + 1
    return $true
}
"""


def _marker(phase: str) -> str:
    return f"    [void](Add-NativeWriteFixturePhase '{phase}')"


def instrument_native_transport(source: str) -> str:
    """Keep the original transport and emit saved phase clocks after its cleanup."""
    newline = "\r\n" if "\r\n" in source else "\n"
    source = source.replace("\r\n", "\n")
    assert "$fixtureRpcCapture" not in source
    timer = "    $timer = [Diagnostics.Stopwatch]::StartNew()"
    for phase, statement, after in (
        (
            "rpc-context",
            "    if (-not (Test-PostToolDiscoveryContext -Discovery $endpoint -RuntimeRoot $runtime)) {",
            False,
        ),
        (
            "rpc-dispatch",
            "    return Invoke-ResidentPostTool -Endpoint $endpoint -PayloadJson $payload -Kind $Kind -ValidatedContext",
            False,
        ),
        ("rpc-entry", timer, True),
        ("rpc-payload", "    $payload = ConvertFrom-PostToolJson $PayloadJson", True),
        ("rpc-elevation", "        } finally { $identity.Dispose() }", True),
        (
            "rpc-encoded",
            '    $bytes = $utf8.GetBytes((ConvertTo-PostToolJson $request) + "`n")',
            True,
        ),
        ("rpc-socket-created", "    $response = [IO.MemoryStream]::new()", False),
        (
            "rpc-connect-start",
            "            $socket.Connect([Net.IPAddress]::Loopback, $port)",
            False,
        ),
        (
            "rpc-connect-call-returned",
            "            $socket.Connect([Net.IPAddress]::Loopback, $port)",
            True,
        ),
        (
            "rpc-connect-catch",
            "            $failure = $_.Exception.GetBaseException()\n"
            "            if ($failure -isnot",
            False,
        ),
        (
            "rpc-connect-unwrapped",
            "            if ($failure -isnot [Net.Sockets.SocketException] -or",
            False,
        ),
        (
            "rpc-connect-returned",
            "        $remaining = 500 - [int]$timer.ElapsedMilliseconds\n"
            "        if ($remaining -le 0 -or -not $socket.Poll(",
            False,
        ),
        ("rpc-connected", "        $offset = 0", False),
        ("rpc-sent", "        $buffer = [byte[]]::new(4096)", False),
        ("rpc-received", "        if (-not $complete) { return $null }", True),
        ("rpc-result", "        $resultJson = ConvertTo-PostToolJson $reply.result", True),
    ):
        assert source.count(statement) == 1, phase
        marker = (
            f"Write-NativeWriteFixtureCpuPhase '{phase}'"
            if phase in RPC_PHASES[:2]
            else _marker(phase)
        )
        source = source.replace(
            statement,
            statement + "\n" + marker if after else marker + "\n" + statement,
            1,
        )
    names = ", ".join(f"'{phase}'" for phase in _BUFFERED_PHASES)
    setup = (
        """
    $fixtureRpcCapture = $ExecutionContext.SessionState.PSVariable.GetValue('global:nativeWriteRpcCapture')
    $fixtureRpcOwn = $null -eq $fixtureRpcCapture
    if ($fixtureRpcOwn) {
        $fixtureRpcNames = @(%s)
        $fixtureRpcMap = @{}
        for ($fixtureRpcIndex = 0; $fixtureRpcIndex -lt $fixtureRpcNames.Length; $fixtureRpcIndex++) {
            $fixtureRpcMap[$fixtureRpcNames[$fixtureRpcIndex]] = $fixtureRpcIndex
        }
        $fixtureRpcCapture = @{
            Names = $fixtureRpcNames; Map = $fixtureRpcMap
            PhaseIds = [int[]]::new(64); Qpc = [long[]]::new(64); Ticks = [long[]]::new(64)
            Used = 0; Truncated = $false; Error = $false
            Frequency = [Diagnostics.Stopwatch]::Frequency
            RelativeClock = $ExecutionContext.SessionState.PSVariable.GetValue('global:nativeEntryClock')
        }
        $global:nativeWriteRpcCapture = $fixtureRpcCapture
    }
    try {
"""
        % names
    )
    source = source.replace(timer, setup + timer, 1)
    flush = """
    } finally {
        if ($fixtureRpcOwn) {
            $global:nativeWriteRpcCapture = $null
            try {
                $fixtureRpcInvariant = [Globalization.CultureInfo]::InvariantCulture
                for ($fixtureRpcIndex = 0; $fixtureRpcIndex -lt $fixtureRpcCapture['Used']; $fixtureRpcIndex++) {
                    [Console]::Error.WriteLine('NATIVE_WRITE_FIXTURE_PHASE ' +
                        $fixtureRpcCapture['Ticks'][$fixtureRpcIndex].ToString($fixtureRpcInvariant) + ' ' +
                        $fixtureRpcCapture['Names'][$fixtureRpcCapture['PhaseIds'][$fixtureRpcIndex]] + ' qpc=' +
                        $fixtureRpcCapture['Qpc'][$fixtureRpcIndex].ToString($fixtureRpcInvariant) + ' frequency=' +
                        $fixtureRpcCapture['Frequency'].ToString($fixtureRpcInvariant))
                }
                if ($fixtureRpcCapture['Truncated']) {
                    [Console]::Error.WriteLine('NATIVE_WRITE_FIXTURE_RPC_TRUNCATED')
                }
                if ($fixtureRpcCapture['Error']) {
                    [Console]::Error.WriteLine('NATIVE_WRITE_FIXTURE_RPC_REPORT_ERROR')
                }
            } catch {
                try { [Console]::Error.WriteLine('NATIVE_WRITE_FIXTURE_RPC_REPORT_ERROR') } catch {}
            }
        }
    }
"""
    boundary = "\n}\n\nfunction Invoke-SelectedResidentToolHook {"
    assert source.count(boundary) == 1
    return source.replace(boundary, flush + boundary, 1).replace("\n", newline)


def rpc_clock_phase(line: bytes) -> str | None:
    """Recognize complete numeric RPC clocks without consuming malformed stderr."""
    match = re.fullmatch(
        rb"NATIVE_WRITE_FIXTURE_PHASE ([0-9]{1,20}) ([a-z-]+)"
        rb" qpc=([0-9]{1,20}) frequency=([0-9]{1,20})\r?\n",
        line,
    )
    if (
        not match
        or match[2].decode("ascii") not in _BUFFERED_PHASES
        or int(match[3]) <= 0
        or int(match[4]) <= 0
    ):
        return None
    return (
        f"{match[1].decode('ascii')} {match[2].decode('ascii')}"
        f" qpc={match[3].decode('ascii')} frequency={match[4].decode('ascii')}"
    )
