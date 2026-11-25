"""Real prepared UV at the original foreground server handoff; inert child only."""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest
from agent_procutil import no_window_kwargs, python_cache_policy_args
from agent_procutil import managed_python
from agent_procutil.owned_process import OutputLimitExceeded, run_caller_owned

import test_managed_engine_launch as engine_controls

ROOT = Path(__file__).resolve().parents[3]
engine_daemon = engine_controls.engine_daemon
prepared_engine = engine_controls.prepared_engine


@pytest.mark.parametrize("scenario", ["query", "serve", "missing-bound-uv"])
def test_real_prepared_server_handoff(
    prepared_engine: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, scenario: str, record_property: Any,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "plugins/agent-index/src"))
    from agent_index import config

    home, slot, python, tools = prepared_engine
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    app = site / "agent_index"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    report = tmp_path / "hosted-report.json"
    (app / "__main__.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "payload={'args':args,'prefix':sys.prefix,'version':list(sys.version_info[:2]),"
        "'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,"
        "'cwd':os.getcwd(),'generation':os.environ.get('AGENT_INDEX_RUNTIME_VERSION')}\n"
        "if args[0]=='index': payload['hints']=json.loads(Path(args[-1]).read_text())\n"
        f"Path({str(report)!r}).write_text(json.dumps(payload),encoding='utf-8')\n"
        "raise SystemExit(17)\n", encoding="utf-8",
    )
    caller = tmp_path / "caller directory"
    caller.mkdir()
    (caller / "hints.json").write_text('{"origin":"caller"}', encoding="utf-8")
    monkeypatch.chdir(caller)
    uv = tools / ("uv.exe" if os.name == "nt" else "uv")
    monkeypatch.setenv(
        "AGENT_INDEX_UV", str(uv if scenario != "missing-bound-uv" else tmp_path / "absent"),
    )
    monkeypatch.setenv("PATH", str(tmp_path / "no ambient tools"))
    monkeypatch.setenv("PYTHONPATH", "foreign imports")
    monkeypatch.setenv("PYTHONHOME", "foreign home")
    monkeypatch.setenv("UV_PYTHON", "foreign selection")
    monkeypatch.setenv("UV_CONFIG_FILE", "absent-config.toml")
    monkeypatch.setenv("VIRTUAL_ENV", "foreign environment")
    monkeypatch.setenv("AGENT_INDEX_RUNTIME_VERSION", "selected-generation")
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(python))
    monkeypatch.delenv(config.SOURCE_MODE_ENV, raising=False)
    inherited = dict(os.environ)
    before = {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }

    # Execute the original function without importing the unrelated HTTP CLI graph.
    source = ROOT / "plugins/agent-index/src/agent_index/__main__.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    definition = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_dispatch_to_server_venv"
    )
    selections: list[Path] = []
    lookups: list[list[str]] = []
    launches: list[list[str]] = []

    def select() -> Path:
        selected = config.host_runtime_python()
        selections.append(selected)
        return selected

    def lookup(command: list[str], **kwargs: Any) -> Any:
        lookups.append(command)
        return run_caller_owned(command, **kwargs)

    def spawn(command: list[str], **kwargs: Any) -> Any:
        launches.append(command)
        assert command[0] == str(python)
        assert kwargs["cwd"] == str(home)
        assert kwargs["env"] == {
            key: value for key, value in inherited.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        assert not {"stdin", "stdout", "stderr", "start_new_session"} & kwargs.keys()
        if scenario == "serve" or os.name != "nt":
            assert "creationflags" not in kwargs
        return subprocess.Popen(command, **{**kwargs, **no_window_kwargs()})

    monkeypatch.setattr(managed_python, "run_caller_owned", lookup)
    namespace: dict[str, Any] = {
        "__package__": "agent_index", "argparse": argparse, "Any": Any,
        "contextlib": contextlib, "os": os, "signal": signal,
        "sys": SimpleNamespace(executable=sys.executable, stdin=SimpleNamespace(isatty=lambda: False)),
        "subprocess": SimpleNamespace(Popen=spawn, TimeoutExpired=subprocess.TimeoutExpired),
        "host_runtime_python": select, "install_dir": lambda: home,
        "resolve_runtime_python": managed_python.resolve_runtime_python,
        "python_cache_args": python_cache_policy_args, "OutputLimitExceeded": OutputLimitExceeded,
        "no_window_kwargs": no_window_kwargs, "_config_from_args": lambda args: args,
    }
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), "exec"), namespace)
    raw = ["index", "--source", "sample with spaces", "--change-hints-file", "hints.json"]
    args = argparse.Namespace(host="127.0.0.1", port=8123, passive=True)
    samples = []
    for _cycle in range(2):
        started = time.monotonic()
        if scenario == "missing-bound-uv":
            with pytest.raises(FileNotFoundError, match="Selected UV"):
                namespace["_dispatch_to_server_venv"](args, "index", raw_args=raw)
            assert not report.exists()
        else:
            assert namespace["_dispatch_to_server_venv"](
                args, "serve" if scenario == "serve" else "index",
                raw_args=None if scenario == "serve" else raw,
            ) == 17
            payload = json.loads(report.read_text(encoding="utf-8"))
            assert Path(payload["prefix"]) == slot and payload["version"] == [3, 10]
            assert payload["isolated"] == 1 and payload["bytecode"] is True
            assert Path(payload["cwd"]) == home
            assert payload["generation"] == "selected-generation"
            if scenario == "serve":
                assert payload["args"] == [
                    "serve", "--host", "127.0.0.1", "--port", "8123", "--passive",
                ]
            else:
                assert payload["args"] == [*raw[:-1], str(caller / "hints.json")]
                assert payload["hints"] == {"origin": "caller"}
        samples.append(time.monotonic() - started)
    assert selections == [python, python]
    assert len(lookups) == len(launches) == (0 if scenario == "missing-bound-uv" else 2)
    assert dict(os.environ) == inherited
    assert before == {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    record_property("index_managed_server_handoff", json.dumps({
        "scenario": scenario, "cycles": 2, "samples_seconds": samples,
        "uv_lookups": len(lookups), "bound_uv_on_path": False,
        "network": "disabled", "service_started": False,
    }))
