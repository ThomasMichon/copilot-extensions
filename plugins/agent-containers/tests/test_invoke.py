"""Tests for interpreter resolution in the container exec-wrapper spawn command.

Covers dotfiles #1631 (root cause): ``_venv_python`` must target the ACTIVE
versioned runtime (``versions/<current-version>``), not the legacy
``~/.agent-containers/.venv`` -- which the versioned-runtime migration stopped
updating, so preferring it made the daemon spawn the wrapper from stale code.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

from agent_containers import _invoke


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

    assert _invoke.payload_binstub() is None


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
