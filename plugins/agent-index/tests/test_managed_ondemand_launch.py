"""Real prepared UV and the leased on-demand engine owner, with an inert app."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
from typing import Any

import pytest
from agent_procutil import (
    python_cache_policy_args, spawn_daemon, windowless_python, windowless_python_env,
)
from agent_procutil import managed_python
from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_test_lifecycle
from agent_procutil.owned_process import run_caller_owned

import test_managed_engine_launch as engine_controls

ROOT = Path(__file__).resolve().parents[3]
engine_daemon = engine_controls.engine_daemon
prepared_engine = engine_controls.prepared_engine


@pytest.mark.parametrize("scenario", ["prepared", "missing-bound-uv"])
def test_real_prepared_ondemand_engine(
    prepared_engine: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, scenario: str, record_property: Any,
) -> None:
    _, slot, python, tools = prepared_engine
    root = tmp_path / "engine data"
    source = ROOT / "plugins/agent-index/src/agent_index/engine/lifecycle.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    definitions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef)
        and node.name in {"_anchor_relative_path_env", "_spawn_engine"}
    ]
    uv = tools / ("uv.exe" if os.name == "nt" else "uv")
    monkeypatch.setenv(
        "AGENT_INDEX_UV", str(uv if scenario == "prepared" else tmp_path / "absent"),
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no ambient tools"))
    monkeypatch.setenv("PYTHONHOME", "foreign home")
    monkeypatch.setenv("PYTHONPATH", "foreign imports")
    monkeypatch.setenv("UV_PYTHON", "foreign selection")
    monkeypatch.setenv("UV_CONFIG_FILE", "absent-config.toml")
    monkeypatch.setenv("VIRTUAL_ENV", "foreign environment")
    monkeypatch.setenv("AGENT_INDEX_CONFIG", "cfg/config.yaml")
    monkeypatch.delenv("AGENT_INDEX_ENGINE_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)
    before = {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    lookups: list[list[str]] = []
    launches: list[list[str]] = []

    def lookup(command: list[str], **kwargs: Any) -> Any:
        lookups.append(command)
        return run_caller_owned(command, **kwargs)

    def spawn(command: list[str], **kwargs: Any) -> Any:
        launches.append(command)
        assert command[0] == windowless_python(str(python))
        assert kwargs["cwd"] == kwargs["instance"].runtime_root == root
        assert kwargs["instance"].source == source
        assert kwargs["breakaway"] is False
        assert kwargs["extra_creationflags"] == 0x20
        assert kwargs["env"]["AGENT_INDEX_CONFIG"] == str(tmp_path / "cfg/config.yaml")
        assert kwargs["env"]["AGENT_INDEX_ENGINE_LOG_LEVEL"] == "warning"
        return spawn_daemon(command, **kwargs)

    monkeypatch.setattr(managed_python, "run_caller_owned", lookup)
    namespace: dict[str, Any] = {
        "__file__": str(source), "os": os, "Path": Path, "re": re,
        "sys": SimpleNamespace(executable=str(python)),
        "data_dir": lambda: root, "_ENGINE_PATH_ENV": ("AGENT_INDEX_CONFIG",),
        "ENGINE_LOG_LEVEL_ENV": "AGENT_INDEX_ENGINE_LOG_LEVEL", "_NORMAL_PRIORITY_CLASS": 0x20,
        "resolve_runtime_python": managed_python.resolve_runtime_python,
        "windowless_python": windowless_python, "windowless_python_env": windowless_python_env,
        "python_cache_policy_args": python_cache_policy_args,
        "spawn_daemon": spawn, "DaemonInstanceSpec": DaemonInstanceSpec,
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *definitions], type_ignores=[]))
    exec(compile(module, str(source), "exec"), namespace)
    samples = []
    with daemon_test_lifecycle(
        owner="index-inert-ondemand", registry_root=tmp_path / "registry",
    ) as context:
        for key, value in context.items():
            monkeypatch.setenv(key, value)
        inherited = dict(os.environ)
        for cycle in range(2):
            started = time.monotonic()
            if scenario == "missing-bound-uv":
                with pytest.raises(FileNotFoundError, match="Selected UV"):
                    namespace["_spawn_engine"](SimpleNamespace())
                assert not root.exists()
            else:
                proc = namespace["_spawn_engine"](SimpleNamespace(
                    model_id="code/model", engine_host="127.0.0.1", engine_port=4555,
                ))
                try:
                    assert proc.wait(timeout=10) == 17
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
                lines = (root / "logs/engine-code_model.log").read_text(encoding="utf-8").splitlines()
                reports = [json.loads(line) for line in lines if line.startswith("{")]
                assert len(reports) == cycle + 1, lines
                payload = reports[-1]
                assert Path(payload["prefix"]) == slot and payload["version"] == [3, 10]
                assert payload["isolated"] == 1 and payload["bytecode"] is True
                assert Path(payload["cwd"]) == root
                assert payload["args"] == ["--host", "127.0.0.1", "--port", "4555"]
            samples.append(time.monotonic() - started)
        assert dict(os.environ) == inherited
    assert len(lookups) == len(launches) == (2 if scenario == "prepared" else 0)
    assert before == {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    record_property("index_managed_ondemand_launch", json.dumps({
        "scenario": scenario, "cycles": 2, "samples_seconds": samples,
        "uv_lookups": len(lookups), "bound_uv_on_path": False,
        "network": "disabled", "model_started": False, "lifetime": "leased-test",
    }))
