from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

from agent_codespaces import _invoke


def _make_runtime(root, version, *, win):
    scripts = "Scripts" if win else "bin"
    exe = "python.exe" if win else "python"
    d = root / "versions" / version / scripts
    d.mkdir(parents=True)
    py = d / exe
    py.write_text("", encoding="utf-8")
    (root / "current-version").write_text(version, encoding="utf-8")
    return py


def test_module_argv_prefers_active_versioned_runtime(tmp_path, monkeypatch):
    win = sys.platform == "win32"
    py = _make_runtime(tmp_path, "0.1.2-dev55", win=win)
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(tmp_path))

    assert _invoke.module_argv() == [str(py), "-m", "agent_codespaces"]


def test_dispatch_argv_prefers_payload_local_shim(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    shim_name = "agent-codespaces.cmd" if sys.platform == "win32" else "agent-codespaces"
    shim = payload / "bin" / shim_name
    shim.parent.mkdir(parents=True)
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(_invoke, "_payload_root", lambda: payload)
    monkeypatch.setattr(_invoke, "module_argv", lambda: ["py", "-m", "agent_codespaces"])

    assert _invoke.dispatch_argv() == [str(shim)]


def test_payload_root_prefers_env_payload_root(monkeypatch, tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))

    assert _invoke._payload_root() == payload.resolve()


def test_payload_root_uses_deploy_manifest_source_path(monkeypatch, tmp_path):
    installed = tmp_path / "site-packages" / "agent_codespaces" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    source_root = tmp_path / "src" / "agent-codespaces"
    source_root.mkdir(parents=True)
    (source_root / "plugin.json").write_text("{}", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "deploy-manifest.json").write_text(
        json.dumps({"source": {"path": str(source_root)}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(runtime))
    monkeypatch.setattr(_invoke, "__file__", str(installed))
    monkeypatch.setattr(
        _invoke,
        "distribution",
        lambda name: (_ for _ in ()).throw(PackageNotFoundError(name)),
    )

    assert _invoke._payload_root() == source_root.resolve()


def test_payload_root_uses_direct_url_source_path(monkeypatch, tmp_path):
    installed = tmp_path / "site-packages" / "agent_codespaces" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    stale_root = tmp_path / "src" / "stale-agent-codespaces"
    stale_root.mkdir(parents=True)
    (stale_root / "plugin.json").write_text("{}", encoding="utf-8")
    source_root = tmp_path / "src" / "agent-codespaces"
    source_root.mkdir(parents=True)
    (source_root / "plugin.json").write_text("{}", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "deploy-manifest.json").write_text(
        json.dumps({"source": {"path": str(stale_root)}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(runtime))
    monkeypatch.setattr(_invoke, "__file__", str(installed))

    class _FakeDist:
        @staticmethod
        def read_text(name):
            assert name == "direct_url.json"
            return json.dumps({"url": source_root.resolve().as_uri()})

    monkeypatch.setattr(_invoke, "distribution", lambda name: _FakeDist())

    assert _invoke._payload_root() == source_root.resolve()


def test_path_from_file_url_preserves_unc_authority():
    assert _invoke._path_from_file_url("file://server/share/agent-codespaces") == Path(
        "//server/share/agent-codespaces"
    )
