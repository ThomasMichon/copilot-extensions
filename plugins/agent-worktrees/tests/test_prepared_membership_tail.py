"""Opt-in observations of the unchanged registered-write membership consumer."""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pytest
from agent_procutil import no_window_kwargs

Parser = Callable[[bytes], tuple[str, bytes]]
Observation = tuple[int, str, int]
BOUNDARIES = (
    "prepared-admission-entry", "prepared-scope-checked", "prepared-identity-checked",
    "prepared-installation-checked", "prepared-sources-checked", "prepared-inputs-checked",
)
FRAME = re.compile(
    rb"NATIVE_WRITE_FIXTURE_TAIL ([0-9]{1,20}) ([a-z-]+) cpu_ticks=([0-9]{1,20})\r?\n",
)


class Fixture(Protocol):
    NATIVE_WRITE_LOAD_WRITER: str
    NATIVE_WRITE_PHASE_WRITER: str
    instrument_prepared_admission: Callable[[str], str]
    native_write_fixture_stderr: Parser
    test_registered_file_local_write_reuses_original_guard_facts: Callable[
        [Path, pytest.MonkeyPatch, pytest.CaptureFixture[str], str, str], None
    ]


def fixture() -> Fixture:
    return cast(Fixture, importlib.import_module("test_managed_session"))


def writer(original: str) -> str:
    assert original.count("function Write-NativeWriteFixtureCpuPhase(") == 1
    assert original.count("NATIVE_WRITE_FIXTURE_PHASE ") == 1
    return original.replace(
        "Write-NativeWriteFixtureCpuPhase", "Write-NativeWriteFixtureTail",
    ).replace("NATIVE_WRITE_FIXTURE_PHASE ", "NATIVE_WRITE_FIXTURE_TAIL ")


def instrument(original: Callable[[str], str], source: str) -> str:
    source = original(source)
    prefix, declaration, prepared = source.partition("function Test-PreparedManagedWorktreesSession {")
    assert declaration and prepared.count("    $stream = $null") == 1
    prepared = prepared.replace(
        "    $stream = $null",
        "    Write-NativeWriteFixtureTail 'prepared-admission-entry'\n    $stream = $null", 1,
    )
    source = prefix + declaration + prepared
    for boundary, statement in (
        ("prepared-scope-checked", "        $identity = $value['identity']"),
        ("prepared-identity-checked", "        $inputs = $value['inputs']"),
        ("prepared-installation-checked", "        if ($sources.Count -ne $expectedSources.Count) { return $false }"),
        ("prepared-sources-checked", "        $hasGitMarker = $false"),
        ("prepared-inputs-checked", "        if ($hasGitMarker -and $null -ne $Facts) {"),
    ):
        assert source.count(statement) == 1
        source = source.replace(statement, f"Write-NativeWriteFixtureTail '{boundary}'\n{statement}", 1)
    return writer(fixture().NATIVE_WRITE_LOAD_WRITER) + source


def parse(stderr: bytes, original: Parser) -> tuple[list[Observation], str, bytes]:
    observations: list[Observation] = []
    phases: list[str] = []
    errors: list[bytes] = []
    for line in stderr.splitlines(keepends=True):
        match = FRAME.fullmatch(line)
        if match and match[2].decode("ascii") in BOUNDARIES:
            stamp, name, cpu = int(match[1]), match[2].decode("ascii"), int(match[3])
            observations.append((stamp, name, cpu))
            phases.append(f"{stamp} {name} cpu_ticks={cpu}")
        else:
            phase, error = original(line)
            if phase:
                phases.append(phase)
            errors.append(error)
    return observations, "\n".join(phases), b"".join(errors)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_tail_parser_preserves_original_phases_and_errors(newline: bytes) -> None:
    valid = b"NATIVE_WRITE_FIXTURE_TAIL 124 prepared-inputs-checked cpu_ticks=2" + newline
    errors = (
        b"real error\xff" + newline
        + valid.replace(b"cpu_ticks=2", b"cpu_ticks=NaN")
        + valid.replace(b"cpu_ticks=2", b"cpu_ticks=" + b"1" * 21)
        + valid.replace(b"prepared-inputs-checked", b"unknown")
        + valid.replace(b"cpu_ticks=2", b"cpu_ticks=2 utility=1")
        + valid.rstrip(b"\r\n")
    )
    rows, phases, remaining = parse(
        b"NATIVE_WRITE_FIXTURE_PHASE 123 command-entry" + newline + valid + errors,
        fixture().native_write_fixture_stderr,
    )
    assert rows == [(124, "prepared-inputs-checked", 2)]
    assert phases == "123 command-entry\n124 prepared-inputs-checked cpu_ticks=2"
    assert remaining == errors


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows membership observation")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_registered_patch_membership_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], shell: str,
) -> None:
    original = fixture()
    original_instrument = original.instrument_prepared_admission
    original_parser = original.native_write_fixture_stderr
    observed: list[Observation] = []
    traces: list[str] = []

    def observe(stderr: bytes) -> tuple[str, bytes]:
        rows, phases, errors = parse(stderr, original_parser)
        observed.extend(rows)
        traces.append(phases)
        return phases, errors

    monkeypatch.setattr(
        original, "instrument_prepared_admission",
        lambda source: instrument(original_instrument, source),
    )
    monkeypatch.setattr(original, "native_write_fixture_stderr", observe)
    original.test_registered_file_local_write_reuses_original_guard_facts(
        tmp_path, monkeypatch, capsys, shell, "patch-input",
    )
    assert [name for _, name, _ in observed] == list(BOUNDARIES), traces
    with capsys.disabled():
        print(json.dumps({"registered_patch_membership_tail": {
            "shell": shell, "observations": observed, "phases": traces,
            "limits": "Diagnostic fixture only; not a root cause or performance qualification.",
        }}), flush=True)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows timeout observation")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_tail_observation_survives_original_child_timeout(tmp_path: Path, shell: str) -> None:
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is unavailable")
    original = fixture()
    command = (
        original.NATIVE_WRITE_PHASE_WRITER + writer(original.NATIVE_WRITE_LOAD_WRITER)
        + "Write-NativeWriteFixtureTail 'prepared-sources-checked'; "
        + "[Console]::Error.WriteLine('real fixture error'); [Threading.Thread]::Sleep(60000)"
    )
    with pytest.raises(subprocess.TimeoutExpired) as captured:
        subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            input=b"", capture_output=True, timeout=10, cwd=tmp_path, **no_window_kwargs(),
        )
    rows, phases, errors = parse(captured.value.stderr or b"", original.native_write_fixture_stderr)
    assert [name for _, name, _ in rows] == ["prepared-sources-checked"], phases
    assert re.fullmatch(
        r"[0-9]+ command-entry\n[0-9]+ prepared-sources-checked cpu_ticks=[0-9]+", phases,
    ), phases
    assert errors == b"real fixture error\r\n"
    assert captured.value.stdout == b""
