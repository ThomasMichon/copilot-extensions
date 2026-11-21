"""Native byte bounds at the original pre-tool file-read boundary."""

from __future__ import annotations

import base64
from collections.abc import Callable
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, cast

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows pre-tool reads")


def _original_cases() -> tuple[str, ...]:
    original = importlib.import_module("test_pre_tool_native_entry")
    call = original.test_native_entry_keeps_uncertain_input_off_python
    for mark in getattr(call, "pytestmark", ()):
        if mark.name == "parametrize" and mark.args[0] == "case":
            values = mark.args[1]
            assert isinstance(values, (tuple, list)) and all(isinstance(value, str) for value in values)
            return tuple(values)
    raise AssertionError("Original native-entry cases are unavailable")


@pytest.mark.parametrize("case", _original_cases())
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_registered_pretool_reader_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: str, case: str,
) -> None:
    monkeypatch.setenv("COPILOT_TEST_HOOK_PRE_PYTHON", sys.executable)
    original = importlib.import_module("test_pre_tool_native_entry")
    call = cast(
        Callable[[str, str, Path], None],
        original.test_native_entry_keeps_uncertain_input_off_python,
    )
    call(case, shell, tmp_path)


def _literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _fixture(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    policy = json.loads((SCRIPTS / "hook-read-policy.json").read_text("utf-8"))
    for name in ("managed-session.ps1", "hook-read-policy.json", *policy["guards"]):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    source = (SCRIPTS / "pre-tool-hook.ps1").read_text("utf-8")
    source, separator, _ = source.rpartition("\ntry {\n")
    assert separator
    mutation = """
    if ($script:fixtureGrow -and $Path -ceq $script:fixtureTarget) {
        $writer = [IO.FileStream]::new(
            $Path, [IO.FileMode]::Append, [IO.FileAccess]::Write,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
        try { $writer.WriteByte(32) } finally { $writer.Dispose() }
    }
    if ($script:fixtureShrink -and $Path -ceq $script:fixtureTarget) {
        $writer = [IO.FileStream]::new(
            $Path, [IO.FileMode]::Open, [IO.FileAccess]::Write,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
        try { $writer.SetLength(2) } finally { $writer.Dispose() }
    }
    if ($script:fixtureReplace -and $Path -ceq $script:fixtureTarget) {
        [IO.File]::WriteAllText($Path + '.replacement', '{"changed":true}', $utf8)
        [IO.File]::Replace($Path + '.replacement', $Path, $Path + '.backup')
    }
"""
    opened = "        $openedLength = $stream.Length"
    if opened in source:
        assert source.count(opened) == 1
        source = source.replace(
            opened,
            opened + "\n"
            "        $script:fixtureReads.Add(@{path=$Path; length=$openedLength; limit=$Limit})\n"
            + mutation,
            1,
        )
    else:
        for statement in (
            "    return ConvertFrom-PreToolJson ([IO.File]::ReadAllText($Path, $utf8))",
            '            $text = [IO.File]::ReadAllText($path, $utf8).Replace("`r`n", "`n")',
            "        $bytes = [byte[]]::new($Limit + 1)",
        ):
            assert source.count(statement) == 1
            source = source.replace(statement, mutation + statement, 1)
    allocation = next(line for line in source.splitlines() if "$bytes = [byte[]]::new(" in line)
    source = source.replace(
        allocation, allocation + "\n        $script:fixtureAllocation = $bytes.Length", 1,
    )
    fixture = scripts / "reader.ps1"
    fixture.write_text(
        ". " + _literal(scripts / "managed-session.ps1") + "\n" + source + "\n",
        encoding="utf-8",
    )
    return fixture


def _run(shell: str, fixture: Path, body: str) -> Any:
    executable = shutil.which(shell)
    if executable is None:
        if os.environ.get("AGENT_WORKTREES_REQUIRE_NATIVE_MEMBERSHIP") == "1":
            pytest.fail(f"{shell} is required")
        pytest.skip(f"{shell} is unavailable")
    env = os.environ.copy()
    env.pop("AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION", None)
    command = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "$script:fixtureReads=[Collections.Generic.List[object]]::new(); "
        ". " + _literal(fixture) + "\n" + body
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    return json.loads(result.stdout)


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_pretool_bounded_read_inputs(tmp_path: Path, shell: str) -> None:
    fixture = _fixture(tmp_path)
    cases: list[dict[str, Any]] = []
    for reader in ("json", "native"):
        for name, raw, limit, mutation, accepted in (
            ("small", b"{}", 1048576, "", True),
            ("exact", b"{}  ", 4, "", True),
            ("oversized", b"{}   ", 4, "", False),
            ("growth", b"{}", 1048576, "grow", False),
            ("short-read", b"{}  ", 16, "shrink", True),
            ("replacement", b"{}", 16, "replace", True),
            ("invalid-utf8", b"\xff{}", 16, "", False),
            ("utf8-bom", b"\xef\xbb\xbf{}", 16, "", True),
            ("utf16-bom", b"\xff\xfe{\x00}\x00", 16, "", reader == "json"),
        ):
            cases.append({
                "name": reader + ":" + name, "reader": reader,
                "raw": base64.b64encode(raw).decode("ascii"),
                "limit": limit, "mutation": mutation, "accepted": accepted,
            })
    inputs = tmp_path / "cases.json"
    inputs.write_text(json.dumps(cases), encoding="utf-8")
    results = _run(shell, fixture, """
$cases = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText(""" + _literal(inputs) + """))
$results = foreach ($case in $cases) {
    $script:fixtureTarget = """ + _literal(tmp_path / "input") + """
    [IO.File]::WriteAllBytes($script:fixtureTarget, [Convert]::FromBase64String($case.raw))
    $script:fixtureGrow = $case.mutation -ceq 'grow'
    $script:fixtureShrink = $case.mutation -ceq 'shrink'
    $script:fixtureReplace = $case.mutation -ceq 'replace'
    $script:fixtureAllocation = $null
    $accepted = $false
    $value = $null
    $failure = $null
    try {
        $value = if ($case.reader -ceq 'json') {
            Read-PreToolJson $script:fixtureTarget $case.limit
        } else {
            Read-NativePreToolText $script:fixtureTarget $case.limit
        }
        $accepted = $true
    } catch { $failure = $_.Exception.Message }
    @{name=$case.name; accepted=$accepted; value=$value; allocation=$script:fixtureAllocation; failure=$failure}
}
[Console]::Out.Write((ConvertTo-Json -InputObject @($results) -Depth 8 -Compress))
""")
    assert len(results) == len(cases)
    for expected, observed in zip(cases, results):
        assert observed["name"] == expected["name"]
        assert observed["accepted"] is expected["accepted"], observed
    for expected, observed in zip(cases, results):
        if expected["name"].endswith(":small"):
            assert observed["allocation"] == 3, observed
        if expected["accepted"]:
            raw = base64.b64decode(expected["raw"])
            if expected["reader"] == "json":
                assert observed["value"] == {}
            else:
                assert observed["value"] == (
                    "{}" if expected["mutation"] == "shrink" else raw.decode("utf-8")
                )


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_pretool_bounded_source_inputs(tmp_path: Path, shell: str) -> None:
    fixture = _fixture(tmp_path)
    result = _run(shell, fixture, """
$script:fixtureGrow = $false
$ordinary = Test-NativePreToolSources
$reads = @($script:fixtureReads.ToArray())
$script:fixtureReads.Clear()
$script:fixtureTarget = """ + _literal(fixture.parent / "anchor_write_guard.py") + """
$script:fixtureGrow = $true
$grown = Test-NativePreToolSources
[Console]::Out.Write((ConvertTo-Json -Depth 8 -Compress @{
    ordinary=$ordinary; grown=$grown; reads=$reads; growthReads=@($script:fixtureReads.ToArray())
}))
""")
    assert result["ordinary"] is True, result
    assert result["grown"] is False, result
    assert len(result["reads"]) == 5, result
    assert len(result["growthReads"]) == 4, result
    assert all(item["length"] <= item["limit"] for item in result["reads"])
