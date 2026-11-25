"""Prepared UV admits the actual FTS command against inert database modules."""

from __future__ import annotations

import ast
import contextlib
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from threading import RLock
from typing import Any

import pytest
from agent_procutil import managed_python, no_window_kwargs
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

import test_managed_engine_launch as engine_controls

ROOT = Path(__file__).resolve().parents[3]
engine_daemon = engine_controls.engine_daemon
prepared_engine = engine_controls.prepared_engine


class Coverage:
    def fts_current(self) -> bool:
        return False


@pytest.mark.parametrize("scenario", [
    "legacy-full", "legacy-incremental", "source-full", "source-incremental", "missing-bound-uv",
])
def test_real_prepared_fts_child(
    prepared_engine: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, scenario: str, record_property: Any,
) -> None:
    _, slot, python, tools = prepared_engine
    source_aware = scenario.startswith("source")
    full = scenario.endswith("full")
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    (site / "lancedb.py").write_text(
        "import json, sys\n"
        "def record(event):\n"
        " print(json.dumps({'event':event,'prefix':sys.prefix,'args':sys.argv[1:],"
        "'version':list(sys.version_info[:2]),'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode}))\n"
        "class Table:\n"
        " def optimize(self): record('optimize')\n"
        " def create_fts_index(self,*a,**k): record('create')\n"
        "class Database:\n"
        " def open_table(self,name): return Table()\n"
        "def connect(path): return Database()\n",
        encoding="utf-8",
    )
    store = site / "agent_index/store"
    store.mkdir(parents=True)
    (store.parent / "__init__.py").write_text("", encoding="utf-8")
    (store / "__init__.py").write_text("", encoding="utf-8")
    (store / "source_layout.py").write_text(
        "from contextlib import contextmanager\n"
        "from lancedb import record\n"
        "@contextmanager\n"
        "def child_fts_owner(path,nonce):\n"
        " record('child-owner')\n"
        " yield\n",
        encoding="utf-8",
    )
    (store / "fts_index_update.py").write_text(
        "from lancedb import record\n"
        "def update_fts_index(table): record('update')\n",
        encoding="utf-8",
    )
    before = {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    uv = tools / ("uv.exe" if os.name == "nt" else "uv")
    monkeypatch.setenv(
        "AGENT_INDEX_UV", str(uv if scenario != "missing-bound-uv" else tmp_path / "absent"),
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no ambient tools"))
    monkeypatch.setenv("UV_PYTHON", "foreign selection")
    monkeypatch.setenv("UV_CONFIG_FILE", "absent-config.toml")
    monkeypatch.setenv("VIRTUAL_ENV", "foreign environment")
    if source_aware:
        monkeypatch.setenv("PYTHONHOME", "foreign home")
        monkeypatch.setenv("PYTHONPATH", "foreign imports")
    monkeypatch.chdir(tmp_path)
    source = ROOT / "plugins/agent-index/src/agent_index/store/content_fts_worker.py"
    definitions = [
        node for node in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        and node.name in {"fts_retryable", "FtsRuntimeAdmissionError", "_ContentFtsWorkerMixin"}
    ]
    lookups: list[list[str]] = []
    children: list[list[dict[str, Any]]] = []
    handoffs: list[str] = []
    native_run = subprocess.run

    def lookup(command: list[str], **kwargs: Any) -> Any:
        lookups.append(command)
        return run_caller_owned(command, **kwargs)

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command[0] == str(python)
        result = native_run(command, **kwargs)
        assert result.returncode == 0, result.stderr
        children.append([json.loads(line) for line in result.stdout.splitlines()])
        return result

    def handoff(*, table_name: str) -> Any:
        assert table_name == "chunks" and len(lookups) == len(handoffs) + 1
        handoffs.append("nonce")
        return contextlib.nullcontext("nonce")

    monkeypatch.setattr(managed_python, "run_caller_owned", lookup)
    namespace: dict[str, Any] = {
        "_ContentFtsCoverageMixin": Coverage, "contextlib": contextlib,
        "os": os, "sys": SimpleNamespace(executable=str(python)),
        "subprocess": SimpleNamespace(run=run), "no_window_kwargs": no_window_kwargs,
        "resolve_runtime_python": managed_python.resolve_runtime_python,
        "OwnedCleanupError": OwnedCleanupError, "SourceLayoutError": RuntimeError,
        "_admitted_runtime": ContextVar("fixture_fts_runtime", default=None),
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *definitions], type_ignores=[]))
    exec(compile(module, str(source), "exec"), namespace)
    worker = namespace["_ContentFtsWorkerMixin"]()
    worker._db_path = str(tmp_path / "db \u03bb's")
    worker._table_name = "chunks"
    worker._fts_admission_lock = RLock()
    worker._source_domain = SimpleNamespace(fts_handoff=handoff) if source_aware else None
    inherited = dict(os.environ)
    samples = []
    for cycle in range(2):
        started = time.monotonic()
        if scenario == "missing-bound-uv":
            with pytest.raises(namespace["FtsRuntimeAdmissionError"], match="runtime admission failed"):
                worker._run_fts_build(full=True, timeout=10)
        else:
            worker._run_fts_build(full=full, timeout=10)
            assert len(children) == cycle + 1
            reports = children[-1]
            assert [report["event"] for report in reports] == (
                (["child-owner"] if source_aware else [])
                + (["optimize", "create"] if full else ["update" if source_aware else "optimize"])
            )
            for report in reports:
                assert Path(report["prefix"]) == slot and report["version"] == [3, 10]
                assert report["isolated"] == int(source_aware) and report["bytecode"] is True
                assert report["args"] == (
                    [worker._db_path, "chunks", "nonce", "1" if full else "0"]
                    if source_aware else [worker._db_path, "chunks"]
                )
        samples.append(time.monotonic() - started)
    assert dict(os.environ) == inherited
    assert len(lookups) == len(children) == (0 if scenario == "missing-bound-uv" else 2)
    assert len(handoffs) == (2 if source_aware else 0)
    assert before == {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    record_property("index_managed_fts_launch", json.dumps({
        "scenario": scenario, "cycles": 2, "samples_seconds": samples,
        "uv_lookups": len(lookups), "bound_uv_on_path": False,
        "network": "disabled", "database_started": False, "lifetime": "finite-child",
    }))
