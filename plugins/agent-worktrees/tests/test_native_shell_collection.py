"""Collection and shell-admission policy, with every process boundary mocked."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import native_shell_support as shells
import pytest

pytestmark = [pytest.mark.portfolio_tier("T1"), pytest.mark.effect("filesystem")]


def _forbid_process(*args, **kwargs):
    pytest.fail("shell discovery/collection must not launch a process")


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize(
    "module",
    [
        "test_installer_copilot_resolution",
        "test_invoke_payload_runtime_posix",
        "test_installer_pipefail",
    ],
)
def test_native_shell_collection_is_process_free(monkeypatch, module, explicit):
    monkeypatch.setenv("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS", "1" if explicit else "0")
    monkeypatch.setattr(subprocess, "run", _forbid_process)
    monkeypatch.setattr(subprocess, "Popen", _forbid_process)
    monkeypatch.setattr(
        shells.shutil, "which", lambda *args, **kwargs: r"C:\Windows\System32\bash.exe"
    )
    spec = importlib.util.spec_from_file_location(
        f"collection_regression_{module}",
        Path(__file__).with_name(f"{module}.py"),
    )
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


@pytest.mark.parametrize(
    "resolver", [shells.resolve_native_bash, shells.resolve_native_powershell]
)
def test_native_shell_collection_default_admission_is_closed(monkeypatch, resolver):
    monkeypatch.delenv("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS", raising=False)
    monkeypatch.setattr(shells.shutil, "which", _forbid_process)
    monkeypatch.setattr(subprocess, "run", _forbid_process)
    with pytest.raises(shells.ShellUnavailable, match="allow-explicit-tiers"):
        resolver()


@pytest.fixture
def shell_policy(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS", "1")
    monkeypatch.setattr(shells.sys, "platform", "win32")
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setattr(shells, "no_window_kwargs", lambda: {"creationflags": 0x08000000})


@pytest.mark.parametrize(
    "candidate",
    [
        r"C:\Users\example\AppData\Local\microsoft\windowsapps\BASH.EXE",
        r"C:\WINDOWS\SYSTEM32\bash.exe",
        "C:/Windows/System32/bash.exe",
    ],
)
def test_native_shell_collection_rejects_wsl_before_probe(monkeypatch, shell_policy, candidate):
    monkeypatch.setattr(shells.shutil, "which", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(subprocess, "run", _forbid_process)
    with pytest.raises(shells.ShellUnavailable, match="WSL launcher"):
        shells.resolve_native_bash()


def test_native_shell_collection_unavailable(monkeypatch, shell_policy):
    monkeypatch.setattr(shells.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(subprocess, "run", _forbid_process)
    with pytest.raises(shells.ShellUnavailable, match="unavailable"):
        shells.resolve_native_bash()


@pytest.mark.parametrize("explicit", [False, True])
def test_native_shell_collection_success_headless_bounded(monkeypatch, shell_policy, explicit):
    if not explicit:
        monkeypatch.delenv("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS", raising=False)
    calls = []
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(shells.shutil, "which", _forbid_process)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 7 if len(calls) == 1 else 1 if len(calls) == 2 else 0, "/\n", ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    resolver = shells.resolve_native_bash if explicit else shells.admit_native_bash
    assert resolver() == r"C:\Program Files\Git\bin\bash.exe"
    assert len(calls) == 3
    assert all(
        kwargs
        == {
            "capture_output": True,
            "text": True,
            "timeout": 5,
            "creationflags": 0x08000000,
        }
        for _, kwargs in calls
    )


@pytest.mark.parametrize("probe", [0, 1, 2])
@pytest.mark.parametrize("failure", ["nonzero", "timeout", "oserror"])
def test_native_shell_collection_probe_failure(monkeypatch, shell_policy, probe, failure):
    calls = []
    monkeypatch.setattr(shells.shutil, "which", lambda *args, **kwargs: "native-bash")

    def run(argv, **kwargs):
        index = len(calls)
        calls.append(argv)
        if index == probe:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 5)
            if failure == "oserror":
                raise OSError("unavailable")
            return subprocess.CompletedProcess(argv, 0 if probe < 2 else 1, "REACHED\n", "")
        return subprocess.CompletedProcess(argv, 7 if index == 0 else 1, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(
        shells.ShellUnavailable,
        match=("non-conformant" if failure == "nonzero" else "probe failed"),
    ):
        shells.resolve_native_bash()
    assert len(calls) == probe + 1


def test_native_shell_collection_path_lookup_skips_wsl_shadows(monkeypatch, shell_policy):
    shadow_paths = [
        r"C:\WINDOWS\SYSTEM32\\",
        r"C:\Users\example\AppData\Local\microsoft\windowsapps",
    ]
    native_dir = r"C:\portable\Git\bin"
    other_dir = r"C:\tools"
    monkeypatch.setattr(
        shells.os, "get_exec_path", lambda: [*shadow_paths, native_dir, other_dir],
    )
    lookups = []
    probes = []

    def which(command, *, path):
        lookups.append((command, path))
        assert path == os.pathsep.join([native_dir, other_dir])
        return native_dir + r"\bash.exe"

    def run(argv, **kwargs):
        probes.append(argv)
        return subprocess.CompletedProcess(
            argv, 7 if len(probes) == 1 else 1 if len(probes) == 2 else 0, "/\n", ""
        )

    monkeypatch.setattr(shells.shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    assert shells.resolve_native_bash() == native_dir + r"\bash.exe"
    assert len(lookups) == 1
    assert len(probes) == 3
    assert all(argv[0] == native_dir + r"\bash.exe" for argv in probes)


@pytest.mark.parametrize("windows", [False, True])
def test_native_shell_collection_path_conversion(monkeypatch, windows):
    monkeypatch.setattr(shells.sys, "platform", "win32" if windows else "linux")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "/converted/path\n", "")

    monkeypatch.setattr(shells, "run_headless", run)
    path = Path("native path")
    assert shells.bash_path("bash", path) == ("/converted/path" if windows else str(path))
    assert calls == ([["bash", "-c", 'cygpath -u -- "$1"', "_", str(path)]] if windows else [])


@pytest.mark.parametrize("returncode,stdout", [(1, ""), (0, "")])
def test_native_shell_collection_path_conversion_failure(monkeypatch, returncode, stdout):
    monkeypatch.setattr(shells.sys, "platform", "win32")
    monkeypatch.setattr(
        shells,
        "run_headless",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, returncode, stdout, "conversion failed"
        ),
    )
    with pytest.raises(AssertionError):
        shells.bash_path("bash", Path("native path"))
