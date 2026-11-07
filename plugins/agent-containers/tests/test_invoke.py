"""Tests for interpreter resolution in the container exec-wrapper spawn command.

Covers dotfiles #1631 (root cause): ``_venv_python`` must target the ACTIVE
versioned runtime (``versions/<current-version>``), not the legacy
``~/.agent-containers/.venv`` -- which the versioned-runtime migration stopped
updating, so preferring it made the daemon spawn the wrapper from stale code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from agent_containers import _invoke


@pytest.fixture(autouse=True)
def clear_declared_payload_root(monkeypatch):
    monkeypatch.delenv("AGENT_CONTAINERS_PAYLOAD_ROOT", raising=False)


@pytest.fixture
def payload_roots(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("declared payload", "build-stage", "generic payload")]
    shim_name = "agent-containers.cmd" if sys.platform == "win32" else "agent-containers"
    for root in roots:
        (root / "bin").mkdir(parents=True)
        (root / "plugin.json").write_text("{}", encoding="utf-8")
        shim = root / "bin" / shim_name
        if sys.platform == "win32":
            shim.write_bytes(b"@echo off\r\n@echo selected-payload\r\n")
        else:
            shim.write_bytes(b'#!/bin/sh\nprintf "selected-payload\\n"\n')
            shim.chmod(0o700)
    declared, staged, generic = roots
    installed = tmp_path / "site-packages" / "agent_containers" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(_invoke, "__file__", str(installed))
    monkeypatch.setenv("AGENT_CONTAINERS_PAYLOAD_ROOT", str(declared))
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)

    class Distribution:
        def read_text(self, name):
            assert name == "direct_url.json"
            return json.dumps({"url": staged.as_uri()})

    monkeypatch.setattr(_invoke, "distribution", lambda name: Distribution())
    return declared, staged, generic, shim_name


@pytest.mark.parametrize("generic_present", [False, True])
def test_declared_payload_root_precedes_generic_and_build_source(payload_roots, monkeypatch, generic_present):
    declared, _staged, generic, shim_name = payload_roots
    if generic_present:
        monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(generic))
    assert _invoke.payload_root() == declared.resolve()
    assert _invoke.payload_command_argv() == [str(declared / "bin" / shim_name)]


@pytest.mark.parametrize("invalid", ["missing", "not-a-directory", "no-manifest"])
def test_invalid_declared_payload_cannot_fall_back_to_build_source(payload_roots, monkeypatch, invalid):
    declared, _staged, _generic, _shim_name = payload_roots
    target = declared.parent / invalid
    if invalid == "not-a-directory":
        target.write_text("not a payload", encoding="utf-8")
    elif invalid == "no-manifest":
        target.mkdir()
    monkeypatch.setenv("AGENT_CONTAINERS_PAYLOAD_ROOT", str(target))
    with pytest.raises(RuntimeError, match="payload"):
        _invoke.payload_command_argv()


def test_missing_declared_command_cannot_select_another_runtime(payload_roots, monkeypatch):
    declared, _staged, _generic, shim_name = payload_roots
    (declared / "bin" / shim_name).unlink()
    monkeypatch.setattr(_invoke, "module_argv", lambda: pytest.fail("another runtime selected"))
    with pytest.raises(RuntimeError, match="command|launcher"):
        _invoke.payload_command_argv()


def test_recorded_provider_command_survives_build_source_cleanup(payload_roots):
    from agent_procutil import no_window_kwargs

    declared, staged, _generic, shim_name = payload_roots
    command = _invoke.payload_command_argv()
    shutil.rmtree(staged)
    assert command == [str(declared / "bin" / shim_name)]
    if sys.platform == "win32":
        command = [os.environ["COMSPEC"], "/d", "/c", "call", *command]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "selected-payload"


def _make_runtime(root, version, *, win):
    """Create a fake versions/<version>/{Scripts|bin}/python(.exe) under root."""
    scripts = "Scripts" if win else "bin"
    exe = "python.exe" if win else "python"
    d = root / "versions" / version / scripts
    d.mkdir(parents=True)
    py = d / exe
    py.write_text("", encoding="utf-8")
    (root / "current-version").write_text(version, encoding="utf-8")
    return py


def test_prefers_active_versioned_runtime(tmp_path, monkeypatch):
    win = sys.platform == "win32"
    py = _make_runtime(tmp_path, "0.1.2-dev55", win=win)
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    assert _invoke._venv_python() == str(py)
    assert _invoke.module_argv() == [str(py), "-m", "agent_containers"]


def test_payload_command_argv_prefers_payload_local_shim(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    shim_name = "agent-containers.cmd" if sys.platform == "win32" else "agent-containers"
    shim = payload / "bin" / shim_name
    shim.parent.mkdir(parents=True)
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(_invoke, "payload_root", lambda: payload)
    monkeypatch.setattr(_invoke, "module_argv", lambda: ["py", "-m", "agent_containers"])

    assert _invoke.payload_command_argv() == [str(shim)]


def test_payload_binstub_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_invoke, "payload_root", lambda: tmp_path / "payload")
    monkeypatch.setattr(_invoke, "module_argv", lambda: ["py", "-m", "agent_containers"])

    assert _invoke.payload_binstub() is None
    assert _invoke.payload_command_argv() == ["py", "-m", "agent_containers"]


def test_payload_root_prefers_env_payload_root(monkeypatch, tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))

    assert _invoke.payload_root() == payload.resolve()


def test_payload_root_uses_deploy_manifest_source_path(monkeypatch, tmp_path):
    installed = tmp_path / "site-packages" / "agent_containers" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    source_root = tmp_path / "src" / "agent-containers"
    source_root.mkdir(parents=True)
    (source_root / "plugin.json").write_text("{}", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "deploy-manifest.json").write_text(
        json.dumps({"source": {"path": str(source_root)}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(runtime))
    monkeypatch.setattr(_invoke, "__file__", str(installed))
    monkeypatch.setattr(
        _invoke,
        "distribution",
        lambda name: (_ for _ in ()).throw(PackageNotFoundError(name)),
    )

    assert _invoke.payload_root() == source_root.resolve()


def test_payload_root_uses_direct_url_source_path(monkeypatch, tmp_path):
    installed = tmp_path / "site-packages" / "agent_containers" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    stale_root = tmp_path / "src" / "stale-agent-containers"
    stale_root.mkdir(parents=True)
    (stale_root / "plugin.json").write_text("{}", encoding="utf-8")
    source_root = tmp_path / "src" / "agent-containers"
    source_root.mkdir(parents=True)
    (source_root / "plugin.json").write_text("{}", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "deploy-manifest.json").write_text(
        json.dumps({"source": {"path": str(stale_root)}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(runtime))
    monkeypatch.setattr(_invoke, "__file__", str(installed))

    class _FakeDist:
        @staticmethod
        def read_text(name):
            assert name == "direct_url.json"
            return json.dumps({"url": source_root.resolve().as_uri()})

    monkeypatch.setattr(_invoke, "distribution", lambda name: _FakeDist())

    assert _invoke.payload_root() == source_root.resolve()


def test_path_from_file_url_preserves_unc_authority():
    assert _invoke._path_from_file_url("file://server/share/agent-containers") == Path(
        "//server/share/agent-containers"
    )


def test_runtime_root_honors_agent_containers_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(tmp_path / "cell" / "agent-containers"))

    assert _invoke.runtime_root() == tmp_path / "cell" / "agent-containers"


def test_does_not_prefer_stale_legacy_venv(tmp_path, monkeypatch):
    # Both a legacy .venv AND an active versioned runtime exist; the versioned
    # runtime must win (the legacy .venv is the stale one).
    win = sys.platform == "win32"
    scripts = "Scripts" if win else "bin"
    exe = "python.exe" if win else "python"
    legacy = tmp_path / ".venv" / scripts
    legacy.mkdir(parents=True)
    (legacy / exe).write_text("", encoding="utf-8")
    py = _make_runtime(tmp_path, "0.1.2-dev55", win=win)
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    assert _invoke._venv_python() == str(py)


def test_falls_back_to_current_interpreter(tmp_path, monkeypatch):
    # No versioned runtime -> use the running interpreter (never stale).
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)  # no current-version / versions
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    assert _invoke._venv_python() == sys.executable


def test_ignores_current_version_pointing_at_missing_dir(tmp_path, monkeypatch):
    # A current-version pointer whose version dir doesn't exist must not be used.
    (tmp_path / "current-version").write_text("9.9.9-dev0", encoding="utf-8")
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    assert _invoke._venv_python() == sys.executable


def test_raises_when_nothing_resolvable(tmp_path, monkeypatch):
    # No versioned runtime, empty sys.executable, no legacy venv -> fail fast.
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(sys, "executable", "")
    import pytest

    with pytest.raises(RuntimeError):
        _invoke._venv_python()


def test_last_resort_legacy_venv_when_no_executable(tmp_path, monkeypatch):
    win = sys.platform == "win32"
    scripts = "Scripts" if win else "bin"
    exe = "python.exe" if win else "python"
    legacy = tmp_path / ".venv" / scripts
    legacy.mkdir(parents=True)
    (legacy / exe).write_text("", encoding="utf-8")
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)  # no versioned runtime
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(sys, "executable", "")
    assert _invoke._venv_python() == str(legacy / exe)
