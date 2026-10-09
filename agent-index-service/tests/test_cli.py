from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from agent_index_service import __version__, composition
from agent_index_service.__main__ import main


@pytest.mark.parametrize("argv", [["--help"], ["--version"], ["serve", "--help"]])
def test_cli_without_site_packages(argv):
    source = Path(__file__).resolve().parents[1] / "src"
    script = (
        f"import sys; sys.path.insert(0, {str(source)!r}); "
        f"from agent_index_service.__main__ import main; main({argv!r})"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script], capture_output=True, text=True,
        timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert "agent-index-service" in result.stdout
    if argv == ["--version"]:
        assert __version__ in result.stdout


def test_import_lightness():
    source = Path(__file__).resolve().parents[1] / "src"
    script = f"""
import sys
sys.path.insert(0, {str(source)!r})
import agent_index_service.__main__, agent_index_service.composition
for name in ('agent_index', 'fastapi', 'uvicorn', 'pydantic', 'lancedb',
             'numpy', 'torch', 'sentence_transformers', 'yaml'):
    assert name not in sys.modules, name
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", script], capture_output=True, text=True,
        timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr


def test_config_does_not_load_core(config_file, monkeypatch, capsys):
    monkeypatch.setattr(composition, "load_core", lambda **kw: pytest.fail("core imported"))
    assert main(["config", "--config", str(config_file)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_missing_native_is_error(config_file, monkeypatch, capsys):
    real_import = importlib.import_module

    def missing(name):
        if name == "lancedb":
            raise ModuleNotFoundError("fixture missing lancedb", name="lancedb")
        return real_import(name)

    monkeypatch.setattr(composition, "importlib", SimpleNamespace(import_module=missing))
    assert main(["serve", "--config", str(config_file)]) == 2
    output = capsys.readouterr()
    assert "agent-index-service[native]" in output.err
    assert "lancedb" in output.err
    assert not output.out


def test_old_core_is_rejected(monkeypatch):
    monkeypatch.setattr(
        composition, "importlib", SimpleNamespace(import_module=lambda name: SimpleNamespace()),
    )
    with pytest.raises(composition.NativeRuntimeUnavailable, match="compatibility seam"):
        composition.load_core()


@pytest.mark.parametrize("verb", ["serve", "start"])
def test_serve_delegates(config_file, monkeypatch, verb):
    calls = []
    core = SimpleNamespace(serve=lambda cfg, **kw: calls.append((cfg, kw)))
    monkeypatch.setattr(composition, "load_core", lambda **kw: core)
    assert main([verb, "--config", str(config_file), "--passive"]) == 0
    assert calls[0][0].host == "127.0.0.1"
    assert calls[0][1] == {"passive": True}


def test_deploy_reuses_core_cutover(config_file, monkeypatch):
    calls = []
    core = SimpleNamespace(cmd_deploy=lambda args: calls.append(args) or 0)
    monkeypatch.setattr(composition, "load_core", lambda **kw: core)
    monkeypatch.setattr(composition, "require_installed_core", lambda: None)
    assert main(["deploy", "--config", str(config_file),
                 "--health-timeout", "7", "--drain-timeout", "9"]) == 0
    assert calls[0].health_timeout == 7
    assert calls[0].drain_timeout == 9
    assert calls[0].json is True
    assert calls[0].force is False


def test_deploy_requires_same_isolated_core(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout="",
    ))
    with pytest.raises(composition.NativeRuntimeUnavailable, match="installed"):
        composition.require_installed_core()


def test_isolated_core_probe_timeout_is_explicit(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("isolated-core-probe", 15)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(composition.NativeRuntimeUnavailable, match="timed out"):
        composition.require_installed_core()


@pytest.mark.parametrize("timeout", ["nan", "inf", "-1", "0"])
def test_bad_deploy_timeout(config_file, timeout):
    with pytest.raises(SystemExit) as exc:
        main(["deploy", "--config", str(config_file), "--health-timeout", timeout])
    assert exc.value.code == 2


def test_status_not_running_is_failure(config_file, capsys):
    assert main(["status", "--config", str(config_file)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "not_running"
    assert payload["invoked_version"] == __version__
