"""Real prepared UV and the shim's leased worker owner without a model or server."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Any

import pytest
from agent_procutil import python_cache_policy_args
from agent_procutil import managed_python
from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_test_lifecycle, spawn_instance
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

import test_managed_engine_launch as engine_controls

ROOT = Path(__file__).resolve().parents[3]
engine_daemon = engine_controls.engine_daemon
prepared_engine = engine_controls.prepared_engine


@pytest.mark.parametrize("scenario", ["prepared", "missing-bound-uv"])
def test_real_prepared_shim_worker(
    prepared_engine: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, scenario: str, record_property: Any,
) -> None:
    _, slot, python, tools = prepared_engine
    source = ROOT / "plugins/agent-index/src/agent_index/engine/shim.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    definitions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        and node.name in {"_worker_command", "WorkerSupervisor"}
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
    monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    monkeypatch.chdir(tmp_path)
    release = tmp_path / "release-worker"
    monkeypatch.setenv("INDEX_FIXTURE_RELEASE", str(release))
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    app = site / "agent_index_engine/app.py"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            "raise SystemExit(17)",
            "import pathlib, time\n"
            "release = pathlib.Path(os.environ['INDEX_FIXTURE_RELEASE'])\n"
            "deadline = time.monotonic() + 20\n"
            "while not release.exists():\n"
            "    if time.monotonic() >= deadline: raise RuntimeError('fixture release expired')\n"
            "    time.sleep(0.01)\n"
            "raise SystemExit(17)",
        ),
        encoding="utf-8",
    )
    before = {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    lookups: list[list[str]] = []
    launches: list[list[str]] = []
    children: list[Any] = []
    report = tmp_path / "worker-output.jsonl"

    def lookup(command: list[str], **kwargs: Any) -> Any:
        lookups.append(command)
        return run_caller_owned(command, **kwargs)

    def spawn(command: list[str], **kwargs: Any) -> Any:
        launches.append(command)
        assert command[:3] == [str(python), "-I", "-B"]
        assert kwargs["instance"].runtime_root == source.parent
        assert kwargs["instance"].source == source
        assert kwargs["env"]["AGENT_INDEX_ENGINE_IDLE_TIMEOUT"] == "0"
        with report.open("ab") as output:
            child = spawn_instance(command, stdout=output, stderr=subprocess.STDOUT, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(managed_python, "run_caller_owned", lookup)
    namespace: dict[str, Any] = {
        "__file__": str(source), "os": os, "Path": Path, "json": json,
        "sys": SimpleNamespace(executable=str(python)),
        "asyncio": asyncio, "subprocess": subprocess, "time": time,
        "logger": logging.getLogger("inert-shim"),
        "OwnedCleanupError": OwnedCleanupError,
        "resolve_runtime_python": managed_python.resolve_runtime_python,
        "python_cache_policy_args": python_cache_policy_args,
        "spawn_instance": spawn, "DaemonInstanceSpec": DaemonInstanceSpec,
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *definitions], type_ignores=[]))
    exec(compile(module, str(source), "exec"), namespace)
    samples = []

    async def healthy(*args: object, **kwargs: object) -> Any:
        return SimpleNamespace(status_code=200)

    async def run() -> None:
        supervisor = namespace["WorkerSupervisor"](worker_host="127.0.0.1", worker_port=4555)
        for cycle in range(2):
            started = time.monotonic()
            if scenario == "missing-bound-uv":
                with pytest.raises(FileNotFoundError, match="Selected UV"):
                    await supervisor.ensure(SimpleNamespace(get=healthy))
            else:
                release.unlink(missing_ok=True)
                try:
                    await supervisor.ensure(SimpleNamespace(get=healthy))
                    assert supervisor.running()
                finally:
                    release.touch()
                    try:
                        for proc in children[cycle:]:
                            try:
                                await asyncio.to_thread(proc.wait, 10)
                            finally:
                                if proc.poll() is None:
                                    proc.kill()
                                    await asyncio.to_thread(proc.wait, 5)
                    finally:
                        await supervisor.stop()
                assert children[-1].returncode == 17
                lines = report.read_text(encoding="utf-8").splitlines()
                reports = [json.loads(line) for line in lines if line.startswith("{")]
                assert len(reports) == cycle + 1, lines
                payload = reports[-1]
                assert Path(payload["prefix"]) == slot and payload["version"] == [3, 10]
                assert payload["isolated"] == 1 and payload["bytecode"] is True
                assert Path(payload["cwd"]) == tmp_path
                assert payload["args"] == ["--host", "127.0.0.1", "--port", "4555"]
            assert not supervisor.running()
            samples.append(time.monotonic() - started)

    with daemon_test_lifecycle(
        owner="index-inert-shim", registry_root=tmp_path / "registry",
    ) as context:
        for key, value in context.items():
            monkeypatch.setenv(key, value)
        inherited = dict(os.environ)
        asyncio.run(run())
        assert dict(os.environ) == inherited
    assert len(lookups) == len(launches) == (2 if scenario == "prepared" else 0)
    assert before == {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    record_property("index_managed_shim_launch", json.dumps({
        "scenario": scenario, "cycles": 2, "samples_seconds": samples,
        "uv_lookups": len(lookups), "bound_uv_on_path": False,
        "network": "disabled", "model_started": False, "lifetime": "leased-test",
    }))
