"""Contracts for tools/run-python and its seasoned `--with` helper."""
from __future__ import annotations

import ast
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_python_with

TOOLS = Path(__file__).resolve().parent
PROBE = "import os, sys; print(repr((sys.version_info[:2], sys.argv[1:], os.getcwd())))"
TRICKY = ["", "a b", 'say "hi"', "tr\\", "--%", 'print("hello")']


def test_parse_splits_leading_requirements() -> None:
    assert run_python_with.parse(["--with", "pytest", "--with=ruff", "--", "-m", "x"]) == (
        ["pytest", "ruff"], ["-m", "x"],
    )
    assert run_python_with.parse(["--with", "pytest", "-m", "pytest"]) == (["pytest"], ["-m", "pytest"])
    assert run_python_with.parse(["script.py", "--with", "x"]) == ([], ["script.py", "--with", "x"])


def test_environment_key_ignores_order_and_binds_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    key = run_python_with.environment_key(["b", "a"])
    assert key == run_python_with.environment_key(["a", "b", "a"])
    monkeypatch.setattr(sys, "version_info", SimpleNamespace(major=3, minor=99))
    assert run_python_with.environment_key(["a", "b"]) != key


def test_requirements_install_through_the_seasoning_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import seasoned_uv

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        Path(command[-1], "Scripts" if os.name == "nt" else "bin").mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0)

    def fake_install(uv: str, arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(seasoned_uv, "run_uv_install", fake_install)
    python = run_python_with.prepare("uv", ["pytest"], cache=tmp_path)
    assert calls and calls[0][-1] == "pytest"
    assert python.parent.parent.parent == tmp_path
    # A prepared environment is reused without another install.
    assert run_python_with.prepare("uv", ["pytest"], cache=tmp_path) == python
    assert len(calls) == 1


def _entrypoints() -> list[list[str]]:
    if os.name == "nt":
        entries = [["cmd.exe", "/d", "/c", str(TOOLS / "run-python.cmd")]]
        for shell in ("powershell.exe", "pwsh"):
            if shutil.which(shell):
                entries.append([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", str(TOOLS / "run-python.ps1")])
        return entries
    return [["/bin/sh", str(TOOLS / "run-python")]]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")
@pytest.mark.parametrize("entry", _entrypoints(), ids=lambda entry: Path(entry[-1]).name + ":" + entry[0])
def test_arguments_and_working_directory_round_trip(entry: list[str], tmp_path: Path) -> None:
    arguments = TRICKY
    command: str | list[str]
    if entry[0] == "cmd.exe":
        command = subprocess.list2cmdline([*entry, "-c", PROBE, *arguments])
    elif entry[-1].endswith(".ps1"):
        # Call the script in-process, as PowerShell callers do, so arguments
        # (including a literal --%) reach it unchanged and must survive the
        # script's own native-command forwarding to uv.
        def literal(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"
        script = "& " + " ".join(literal(value) for value in [entry[-1], "-c", PROBE, *arguments])
        script += "; exit $LASTEXITCODE"
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        command = [*entry[:-1], "-EncodedCommand", encoded]
    else:
        command = [*entry, "-c", PROBE, *arguments]
    environment = {**os.environ, "UV_WORKING_DIRECTORY": str(tmp_path), "UV_WORKING_DIR": str(tmp_path)}
    result = subprocess.run(
        command, cwd=TOOLS.parent, env=environment, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    version, received, cwd = ast.literal_eval(result.stdout.strip().splitlines()[-1])
    pin = (TOOLS.parent / ".python-version").read_text(encoding="utf-8").strip()
    assert ".".join(map(str, version)) == pin
    assert received == arguments
    assert Path(cwd).resolve() == TOOLS.parent.resolve()
