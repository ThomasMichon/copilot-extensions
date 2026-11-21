"""Opt-in observations of the unchanged registered-write membership consumer."""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast

import pytest
from agent_procutil import no_window_kwargs

Parser = Callable[[bytes], tuple[str, bytes]]
ResidentFactory = Callable[..., list[dict[str, object]]]
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
    registered_policy_resident: Callable[..., AbstractContextManager[list[dict[str, object]]]]
    test_registered_file_local_write_reuses_original_guard_facts: Callable[
        [Path, pytest.MonkeyPatch, pytest.CaptureFixture[str], str, str, ResidentFactory], None
    ]


class PolicyFixture(Protocol):
    test_registered_full_policy_composition: Callable[
        [Path, pytest.MonkeyPatch, pytest.CaptureFixture[str], str, bool, str], None
    ]


def policy_timeout_observation(error: subprocess.TimeoutExpired) -> dict[str, object]:
    def raw(value: object) -> bytes:
        if isinstance(value, bytes):
            return value
        return value.encode("utf-8", errors="replace") if isinstance(value, str) else b""

    stderr = raw(error.stderr)
    lines = stderr.decode("utf-8", errors="replace").splitlines()
    return {
        "shell_build_markers": sum(
            re.fullmatch(r"NATURAL_SHELL_BUILD [0-9]+(?:\.[0-9]+){1,3}", line) is not None
            for line in lines
        ),
        "policy_start_markers": lines.count("NATURAL_POLICY_START"),
        "policy_complete_markers": lines.count("NATURAL_POLICY_COMPLETE"),
        "stdout_bytes": len(raw(error.output)),
        "stderr_bytes": len(stderr),
    }


def observe_policy_timeout(
    call: Callable[[], subprocess.CompletedProcess[str]],
    emit: Callable[[dict[str, object]], None],
) -> subprocess.CompletedProcess[str]:
    try:
        return call()
    except subprocess.TimeoutExpired as error:
        try:
            emit(policy_timeout_observation(error))
        except Exception:
            pass
        raise


@pytest.mark.parametrize("observer_fails", [False, True])
def test_policy_timeout_observation_preserves_original_failure_and_redacts_output(
    observer_fails: bool,
) -> None:
    error = subprocess.TimeoutExpired(
        ["synthetic-private-command"], 10, output=b"synthetic-private-output",
        stderr=(
            b"NATURAL_SHELL_BUILD 5.1.123.4\r\nNATURAL_POLICY_START\r\n"
            b"synthetic-private-error\r\n"
        ),
    )
    observations: list[dict[str, object]] = []

    def call() -> subprocess.CompletedProcess[str]:
        raise error

    def emit(value: dict[str, object]) -> None:
        observations.append(value)
        if observer_fails:
            raise RuntimeError("synthetic observation failure")

    with pytest.raises(subprocess.TimeoutExpired) as captured:
        observe_policy_timeout(call, emit)
    assert captured.value is error
    assert error.output == b"synthetic-private-output"
    assert error.stderr is not None
    assert observations == [{
        "shell_build_markers": 1, "policy_start_markers": 1,
        "policy_complete_markers": 0, "stdout_bytes": len(error.output),
        "stderr_bytes": len(error.stderr),
    }]
    assert "synthetic-private" not in json.dumps(observations)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Native Windows policy timeout observation")
@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"])
def test_registered_policy_timeout_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], shell: str,
) -> None:
    original = cast(PolicyFixture, importlib.import_module("test_native_pre_policy"))
    native_run = cast(Callable[..., subprocess.CompletedProcess[str]], subprocess.run)
    selected_calls = 0

    def emit(value: dict[str, object]) -> None:
        with capsys.disabled():
            print(json.dumps({"registered_policy_timeout": {"shell": shell, **value}}), flush=True)

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal selected_calls
        command = args[0] if args else kwargs.get("args")
        selected = isinstance(command, list) and any(
            isinstance(value, str) and "NATURAL_SHELL_BUILD " in value for value in command
        )
        if not selected:
            return native_run(*args, **kwargs)
        assert kwargs.get("timeout") == 10
        selected_calls += 1
        return observe_policy_timeout(lambda: native_run(*args, **kwargs), emit)

    monkeypatch.setattr(original, "subprocess", SimpleNamespace(**(vars(subprocess) | {"run": run})))
    original.test_registered_full_policy_composition(
        tmp_path, monkeypatch, capsys, shell, True, "cross-modes",
    )
    assert selected_calls == 3


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
    with ExitStack() as stack:
        original.test_registered_file_local_write_reuses_original_guard_facts(
            tmp_path, monkeypatch, capsys, shell, "patch-input",
            lambda *args, **kwargs: stack.enter_context(original.registered_policy_resident(*args, **kwargs)),
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
