"""Real prepared UV and the existing leased passive-daemon owner; inert app only."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
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


@pytest.mark.parametrize("scenario", ["legacy", "cell", "missing-bound-uv"])
def test_real_prepared_passive_daemon(
    prepared_engine: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, scenario: str, record_property: Any,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "plugins/agent-index/src"))
    from agent_index import config

    home, slot, python, tools = prepared_engine
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    shutil.copytree(
        ROOT / "libs/agent-procutil/src/agent_procutil", site / "agent_procutil",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    app = site / "agent_index"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    report = tmp_path / "passive-report.json"
    (app / "__main__.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "payload={'args':sys.argv[1:],'prefix':sys.prefix,'version':list(sys.version_info[:2]),"
        "'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,"
        "'cwd':os.getcwd(),'generation':os.environ.get('AGENT_INDEX_RUNTIME_VERSION'),"
        "'installation':os.environ.get('AGENT_INDEX_INSTALLATION_ID'),"
        "'level':os.environ.get('AGENT_INDEX_LOG_LEVEL')}\n"
        f"Path({str(report)!r}).write_text(json.dumps(payload),encoding='utf-8')\n"
        "raise SystemExit(17)\n", encoding="utf-8",
    )
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
    if scenario == "cell":
        monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "test-cell/agent-index")
        monkeypatch.setenv("AGENT_INDEX_LOG_LEVEL", "debug")
    else:
        monkeypatch.delenv("AGENT_INDEX_INSTALLATION_ID", raising=False)
        monkeypatch.delenv("AGENT_INDEX_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)
    before = {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    source = ROOT / "plugins/agent-index/src/agent_index/__main__.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    deploy = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_deploy"
    )
    definition = next(
        node for node in deploy.body
        if isinstance(node, ast.FunctionDef) and node.name == "spawn_passive"
    )
    selections: list[Path] = []
    lookups: list[list[str]] = []
    launches: list[list[str]] = []
    passive: dict[str, Any] = {}

    def select() -> Path:
        selected = config.host_runtime_python()
        selections.append(selected)
        return selected

    def lookup(command: list[str], **kwargs: Any) -> Any:
        lookups.append(command)
        return run_caller_owned(command, **kwargs)

    def spawn(command: list[str], **kwargs: Any) -> Any:
        launches.append(command)
        assert command[0] == windowless_python(str(python))
        assert kwargs["cwd"] == home
        assert kwargs["log_path"] == home / "service-stdio.log"
        assert kwargs["instance"].runtime_root == home
        assert kwargs["instance"].source == source
        assert "PYTHONPATH" not in kwargs["env"] and "PYTHONHOME" not in kwargs["env"]
        return spawn_daemon(command, **kwargs)

    monkeypatch.setattr(managed_python, "run_caller_owned", lookup)
    namespace: dict[str, Any] = {
        "__file__": str(source), "os": os, "Path": Path, "Any": Any,
        "cfg": SimpleNamespace(host="127.0.0.1"), "host_runtime_python": select,
        "install_dir": lambda: home, "resolve_runtime_python": managed_python.resolve_runtime_python,
        "windowless_python": windowless_python, "windowless_python_env": windowless_python_env,
        "python_cache_args": python_cache_policy_args, "spawn_daemon": spawn,
        "DaemonInstanceSpec": DaemonInstanceSpec, "passive_instance": passive,
    }
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), "exec"), namespace)
    samples = []
    with daemon_test_lifecycle(
        owner="index-inert-passive", registry_root=tmp_path / "registry",
    ) as context:
        for key, value in context.items():
            monkeypatch.setenv(key, value)
        inherited = dict(os.environ)
        for _cycle in range(2):
            started = time.monotonic()
            if scenario == "missing-bound-uv":
                with pytest.raises(FileNotFoundError, match="Selected UV"):
                    namespace["spawn_passive"](4555)
                assert passive == {} and not report.exists()
                assert not (home / "service-stdio.log").exists()
            else:
                handle = namespace["spawn_passive"](4555)
                try:
                    assert handle.wait(timeout=10) == 17
                    assert passive == {"port": 4555, "pid": handle.pid}
                finally:
                    if handle.poll() is None:
                        handle.kill()
                        handle.wait(timeout=5)
                payload = json.loads(report.read_text(encoding="utf-8"))
                assert Path(payload["prefix"]) == slot and payload["version"] == [3, 10]
                assert payload["isolated"] == 1 and payload["bytecode"] is True
                assert Path(payload["cwd"]) == home
                assert payload["generation"] == "selected-generation"
                assert payload["installation"] == ("test-cell/agent-index" if scenario == "cell" else None)
                assert payload["level"] == ("debug" if scenario == "cell" else "warning")
                assert payload["args"] == [
                    "__cell-start" if scenario == "cell" else "start",
                    "--host", "127.0.0.1", "--port", "4555", "--passive",
                ]
            samples.append(time.monotonic() - started)
        assert dict(os.environ) == inherited
    assert selections == [python, python]
    assert len(lookups) == len(launches) == (0 if scenario == "missing-bound-uv" else 2)
    assert before == {
        str(path.relative_to(slot)): path.read_bytes()
        for path in slot.rglob("*") if path.is_file()
    }
    record_property("index_managed_passive_launch", json.dumps({
        "scenario": scenario, "cycles": 2, "samples_seconds": samples,
        "uv_lookups": len(lookups), "bound_uv_on_path": False,
        "network": "disabled", "service_started": False, "lifetime": "leased-test",
    }))
