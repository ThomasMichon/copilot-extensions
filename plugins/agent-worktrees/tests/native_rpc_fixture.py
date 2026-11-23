"""Bounded numeric RPC clocks with opt-in parent-owned diagnostic retention."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable, Iterator
import json
import mmap
import re
import struct
import sys
import uuid


_REGION_BYTES = 4096
_CAPACITY = 64
_HEADER = struct.Struct("<8Q")
_ROW = struct.Struct("<5Q")
_MAGIC = 0x5250435048415345


RPC_PHASES = (
    "rpc-context",
    "rpc-dispatch",
    "rpc-entry",
    "rpc-payload",
    "rpc-elevation",
    "rpc-encode-start",
    "rpc-encoded",
    "rpc-socket-start",
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

SHARED_PHASE_BUFFER_WRITER = """
function Add-NativeWriteFixturePhase([string]$Name) {
    $capture = $ExecutionContext.SessionState.PSVariable.GetValue('global:nativeWriteRpcCapture')
    if ($null -eq $capture) { return $false }
    $view = $capture['Shared']
    if (-not $capture['Map'].ContainsKey($Name)) {
        $capture['Error'] = $true
        $view.Write(40, [long]1)
        return $true
    }
    $index = [int]$capture['Used']
    if ($index -ge 64) {
        $capture['Truncated'] = $true
        $view.Write(32, [long]1)
        return $true
    }
    $offset = 64 + 40 * $index
    $view.Write($offset, [long]$capture['Map'][$Name])
    $view.Write($offset + 8, [long][Diagnostics.Stopwatch]::GetTimestamp())
    $tick = if ($capture['RelativeClock'] -is [Diagnostics.Stopwatch]) {
        $capture['RelativeClock'].ElapsedMilliseconds
    } else { [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
    $view.Write($offset + 16, [long]$tick)
    $view.Write($offset + 24, [long]$capture['Frequency'])
    [Threading.Thread]::MemoryBarrier()
    $view.Write($offset + 32, [long]($index + 1))
    [Threading.Thread]::MemoryBarrier()
    $view.Write(24, [long]($index + 1))
    $capture['Used'] = $index + 1
    return $true
}
"""


def decode_retained_phases(data: bytes) -> dict[str, object]:
    """Decode only committed numeric rows; never return arbitrary region bytes."""
    unavailable: dict[str, object] = {"status": "unavailable", "records": []}
    if len(data) != _REGION_BYTES:
        return unavailable
    magic, version, capacity, used, truncated, error, reserved1, reserved2 = _HEADER.unpack_from(data)
    if (
        (magic, version, capacity) != (_MAGIC, 1, _CAPACITY)
        or used > _CAPACITY or truncated > 1 or error > 1 or reserved1 or reserved2
    ):
        return unavailable
    records: list[dict[str, int]] = []
    status = "complete"
    previous_qpc = 0
    frequency = 0
    for index in range(_CAPACITY):
        phase, qpc, ticks, current_frequency, commit = _ROW.unpack_from(
            data, _HEADER.size + index * _ROW.size,
        )
        if index >= used:
            if any((phase, qpc, ticks, current_frequency, commit)):
                status = "partial"
            continue
        if (
            commit != index + 1 or phase >= len(_BUFFERED_PHASES) or qpc <= 0
            or current_frequency <= 0 or qpc < previous_qpc
            or (frequency and current_frequency != frequency)
            or max(qpc, ticks, current_frequency) >= 1 << 63
        ):
            return unavailable
        records.append({"phase_id": phase, "qpc": qpc, "ticks": ticks, "frequency": current_frequency})
        previous_qpc, frequency = qpc, current_frequency
    return {"status": status, "records": records, "truncated": bool(truncated), "error": bool(error)}


def _emit_retained_report(
    report: dict[str, object], reporter: Callable[[dict[str, object]], None] | None,
) -> None:
    if reporter is not None:
        try:
            reporter(report)
        except Exception:
            pass
        else:
            return
    print("retained_rpc_phases=" + json.dumps(
        report, sort_keys=True,
    ), file=sys.stderr, flush=True)


@contextmanager
def retained_rpc_phases(
    *, enabled: bool, reporter: Callable[[dict[str, object]], None] | None = None,
) -> Iterator[str]:
    """Keep one region; an optional post-close reporter survives capture resets."""
    if not enabled:
        yield ""
        return
    if sys.platform != "win32":
        raise RuntimeError("Retained native phases require Windows")
    name = "Local\\fixture-rpc-" + uuid.uuid4().hex
    region = mmap.mmap(-1, _REGION_BYTES, tagname=name)
    report: dict[str, object] = {"status": "unavailable", "records": []}
    try:
        region[:_HEADER.size] = _HEADER.pack(_MAGIC, 1, _CAPACITY, 0, 0, 0, 0, 0)
        yield (
            "$fixturePhaseRegion = [IO.MemoryMappedFiles.MemoryMappedFile]::OpenExisting('"
            + name + "'); try { "
            "$global:nativeWriteRpcShared = $fixturePhaseRegion.CreateViewAccessor(0, 4096);\n"
            + SHARED_PHASE_BUFFER_WRITER
        )
    finally:
        try:
            report = decode_retained_phases(region[:])
        finally:
            region.close()
        _emit_retained_report(
            {**report, "region_closed": region.closed, "diagnostic_only": True}, reporter,
        )


def retained_rpc_command(prefix: str, command: str) -> str:
    if not prefix:
        return command
    return (
        prefix + command
        + "\n} finally { if ($null -ne $global:nativeWriteRpcShared) { $global:nativeWriteRpcShared.Dispose() }; "
        "$global:nativeWriteRpcShared = $null; $fixturePhaseRegion.Dispose() }\n"
    )


def _marker(phase: str) -> str:
    return f"    [void](Add-NativeWriteFixturePhase '{phase}')"


def instrument_native_transport(source: str, *, retain: bool = False) -> str:
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
            "    return Invoke-ResidentPostTool -Endpoint $endpoint -PayloadJson $payload -Kind $Kind -ValidatedContext @options",
            False,
        ),
        ("rpc-entry", timer, True),
        ("rpc-payload", "    if ($null -eq $payload -or", False),
        ("rpc-elevation", "        } finally { $identity.Dispose() }", True),
        (
            "rpc-encode-start",
            '    $bytes = $utf8.GetBytes((ConvertTo-PostToolJson $request) + "`n")',
            False,
        ),
        (
            "rpc-encoded",
            '    $bytes = $utf8.GetBytes((ConvertTo-PostToolJson $request) + "`n")',
            True,
        ),
        (
            "rpc-socket-start",
            "    $socket = [Net.Sockets.Socket]::new(",
            False,
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
    if retain:
        setup = setup.replace(
            "PhaseIds = [int[]]::new(64); Qpc = [long[]]::new(64); Ticks = [long[]]::new(64)",
            "Shared = $global:nativeWriteRpcShared",
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
    if retain:
        flush = flush.replace(
            "                    [Console]::Error.WriteLine",
            "                    $fixtureRpcView = $fixtureRpcCapture['Shared']\n"
            "                    $fixtureRpcOffset = 64 + 40 * $fixtureRpcIndex\n"
            "                    [Console]::Error.WriteLine", 1,
        )
        for expression, replacement in (
            ("$fixtureRpcCapture['Ticks'][$fixtureRpcIndex]", "($fixtureRpcView.ReadInt64($fixtureRpcOffset + 16))"),
            ("$fixtureRpcCapture['PhaseIds'][$fixtureRpcIndex]", "$fixtureRpcView.ReadInt64($fixtureRpcOffset)"),
            ("$fixtureRpcCapture['Qpc'][$fixtureRpcIndex]", "($fixtureRpcView.ReadInt64($fixtureRpcOffset + 8))"),
        ):
            flush = flush.replace(expression, replacement)
    boundary = "\n}\n\nfunction Invoke-SelectedResidentToolHook {"
    assert source.count(boundary) == 1
    return source.replace(boundary, flush + boundary, 1).replace("\n", newline)


def diagnostic_policy_transport(source: str, *, enabled: bool, retain: bool = False) -> str:
    """Reuse buffered RPC phases only in explicitly selected policy diagnostics."""
    if not enabled:
        return source
    writer = (SHARED_PHASE_BUFFER_WRITER if retain else PHASE_BUFFER_WRITER) + """
function Write-NativeWriteFixtureCpuPhase([string]$Name) {
    [void](Add-NativeWriteFixturePhase $Name)
}
"""
    return writer + (
        instrument_native_transport(source, retain=True) if retain
        else instrument_native_transport(source)
    )


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
