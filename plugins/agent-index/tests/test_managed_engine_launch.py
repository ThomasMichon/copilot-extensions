"""Real prepared UV and inert engine commands; no engine service or model starts."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import ModuleType
from typing import Any

import pytest

from agent_procutil import windowless_python_env
from agent_procutil import managed_python
from agent_procutil.managed_python import _SELECTION_ENV
from agent_procutil.owned_process import run_caller_owned

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def engine_daemon() -> Any:
    directory = ROOT / "plugins/agent-index/src/agent_index/engine"
    package = ModuleType("_managed_index_engine_source")
    package.__path__ = [str(directory)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(
        package.__name__ + ".daemon", directory / "daemon.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared_engine(tmp_path: Path, engine_daemon: Any) -> tuple[Path, Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("Prepare UV before running this acquisition-free control")
    env = {k: v for k, v in os.environ.items() if k.upper() not in _SELECTION_ENV}
    env.update(UV_NO_CONFIG="1", UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    if "UV_PYTHON_INSTALL_DIR" not in env and os.name != "nt":
        # The contained runner changes HOME after preparing its managed Python.
        env["UV_PYTHON_INSTALL_DIR"] = str(
            Path(getattr(sys, "_base_executable", sys.executable)).resolve().parents[2]
        )

    def run(command: Any) -> Any:
        return run_caller_owned(
            command, timeout=20, input_text="", env=env,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )

    found = run([uv, "python", "find", "--no-project", "--system",
                 "--managed-python", "--no-python-downloads", "cpython@3.10.20"])
    if found.returncode and "No interpreter found" in found.stderr:
        pytest.skip("Prepare managed CPython 3.10.20 before this offline control")
    assert found.returncode == 0, found.stderr
    home = tmp_path / "engine \u03bb's"
    home.mkdir()
    slot = home / "versions" / "1.0.0"
    built = run([uv, "venv", "--no-project", "--no-python-downloads", "--python",
                 found.stdout.strip(), str(slot)])
    assert built.returncode == 0, built.stderr
    for source in (ROOT / "libs/versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(source, home / source.name)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "fixture", "pid": 0, "payload_hash": "a" * 64,
    }), encoding="utf-8")
    (home / "current-version").write_text(slot.name, encoding="utf-8")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    app = site / "agent_index_engine"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "app.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args':sys.argv[1:],'prefix':sys.prefix,"
        "'version':list(sys.version_info[:2]),'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode,'cwd':os.getcwd()}))\n"
        "raise SystemExit(17)\n", encoding="utf-8",
    )
    (site / "uvicorn.py").write_text("def run(*a, **k): pass\n", encoding="utf-8")
    tools = tmp_path / "UV only"
    tools.mkdir()
    shutil.copy2(uv, tools / Path(uv).name)
    for name in ("python", "python3", "py"):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            "@exit /b 99\r\n" if os.name == "nt" else "#!/bin/sh\nexit 99\n",
            encoding="utf-8",
        )
        poison.chmod(0o700)
    return home, slot, python, tools


@pytest.mark.parametrize("scenario", ["current", "last-good", "legacy", "missing-uv"])
def test_real_prepared_engine_child(
    prepared_engine: tuple[Path, Path, Path, Path], engine_daemon: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str, record_property: Any,
) -> None:
    home, slot, python, tools = prepared_engine
    if scenario == "last-good":
        (home / "current-version").write_text("incomplete", encoding="utf-8")
        (home / "last-known-good").write_text(slot.name, encoding="utf-8")
    elif scenario == "legacy":
        (home / "current-version").unlink()
        legacy = home / ".venv"
        slot.rename(legacy)
        slot = legacy
        python = slot / python.relative_to(home / "versions/1.0.0")
    before = {str(p.relative_to(slot)): p.read_bytes() for p in slot.rglob("*") if p.is_file()}
    monkeypatch.chdir(tmp_path)
    for key in _SELECTION_ENV:
        monkeypatch.setenv(key, "foreign-selection")
    monkeypatch.setenv("PATH", str(tools) if scenario != "missing-uv" else str(tmp_path / "absent"))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "nondefault managed store"))
    monkeypatch.setenv("UV_NO_CONFIG", "false")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("AGENT_INDEX_ENGINE_HOST", "127.0.0.1")
    monkeypatch.setenv("AGENT_INDEX_ENGINE_PORT", "8421")
    inherited = dict(os.environ)
    samples = []
    lookups: list[list[str]] = []
    capture = run_caller_owned

    def counted_lookup(command: list[str], **kwargs: Any) -> Any:
        lookups.append(command)
        return capture(command, **kwargs)

    monkeypatch.setattr(managed_python, "run_caller_owned", counted_lookup)
    for cycle in range(2):
        started = time.monotonic()
        if scenario == "missing-uv":
            with pytest.raises(FileNotFoundError, match="UV is unavailable"):
                engine_daemon.engine_command(home)
            samples.append(time.monotonic() - started)
            continue
        command = engine_daemon.engine_command(home)
        command = engine_daemon._with_log_level_bootstrap(command)
        command += ["literal spaces", "", "\u03bb's"]
        result = run_caller_owned(
            command, timeout=10, input_text="",
            env={**os.environ, **windowless_python_env(str(python))},
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        assert result.returncode == 17, result.stderr
        assert not result.stderr
        payload = json.loads(result.stdout)
        assert Path(payload["prefix"]) == slot
        assert payload["version"] == [3, 10]
        assert payload["isolated"] == 1 and payload["bytecode"] is True
        assert Path(payload["cwd"]) == tmp_path
        assert payload["args"] == [
            "--host", "127.0.0.1", "--port", "8421", "literal spaces", "", "\u03bb's",
        ]
        samples.append(time.monotonic() - started)
    assert dict(os.environ) == inherited
    assert len(lookups) == (0 if scenario == "missing-uv" else 2)
    assert before == {str(p.relative_to(slot)): p.read_bytes() for p in slot.rglob("*") if p.is_file()}
    record_property("index_managed_engine_launch", json.dumps({
        "scenario": scenario, "cycles": 2, "samples_seconds": samples,
        "network": "disabled", "runtime_python": "3.10.20",
        "uv_lookups": len(lookups),
        "service_started": False, "cpu_and_tails": "unmeasured",
    }))
