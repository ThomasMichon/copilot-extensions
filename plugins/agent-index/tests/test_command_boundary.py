"""Host lifecycle commands do not own dependency installation or launch."""

from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index import __main__ as cli, transport

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "arguments",
    [
        ["start"], ["serve"], ["restart"], ["deploy"],
        ["deploy", "--recover", "--force"], ["__cell-start"],
    ],
)
def test_public_cli_refuses_host_lifecycle_even_with_managed_python(
    monkeypatch, capsys, arguments
):
    monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", sys.executable)
    monkeypatch.setenv("AGENT_INDEX_ROLE", "host")
    monkeypatch.setattr(cli, "serve", lambda *_a, **_k: pytest.fail("host launched"))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_a, **_k: pytest.fail("spawned"))
    assert cli.main(arguments) == 2
    assert "managed by agent-dispatch" in capsys.readouterr().err


@pytest.mark.parametrize("role", ["host", "client", "unconfigured"])
def test_managed_start_requires_selected_python_and_host_scope(monkeypatch, role):
    monkeypatch.delenv("AGENT_INDEX_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(
        transport, "plan_route", lambda: (role, {"machine": "example-host"})
    )
    calls = []
    monkeypatch.setattr(cli, "serve", lambda *_a, **_k: calls.append(True))
    monkeypatch.delenv("AGENT_INDEX_MANAGED_PYTHON", raising=False)
    assert cli.main(["__managed-start"]) == 2
    monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", sys.executable)
    assert cli.main(["__managed-start"]) == (0 if role == "host" else 2)
    assert calls == ([True] if role == "host" else [])
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "install.json")
    assert cli.main(["__managed-start"]) == 2


@pytest.mark.parametrize("role", ["host", "client", "unconfigured"])
def test_managed_engine_start_requires_selected_python_and_host_scope(monkeypatch, role):
    from agent_index.engine import app as engine_app

    monkeypatch.delenv("AGENT_INDEX_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(
        transport, "plan_route", lambda: (role, {"machine": "example-host"})
    )
    calls = []
    monkeypatch.setattr(engine_app, "run_engine", lambda *_a, **_k: calls.append(True))
    monkeypatch.delenv("AGENT_INDEX_ENGINE_MANAGED_PYTHON", raising=False)
    assert cli.main(["__managed-engine-start"]) == 2
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MANAGED_PYTHON", sys.executable)
    assert cli.main(["__managed-engine-start"]) == (0 if role == "host" else 2)
    assert calls == ([True] if role == "host" else [])
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "install.json")
    assert cli.main(["__managed-engine-start"]) == 2


def test_managed_engine_health_requires_generation_and_dependency_match(
    monkeypatch, capsys
):
    monkeypatch.delenv("AGENT_INDEX_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MANAGED_PYTHON", sys.executable)
    monkeypatch.setattr(
        transport, "plan_route", lambda: ("host", {"machine": "example-host"})
    )

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_k: Response(
            {
                "status": "ok",
                "generation": "wrong-generation",
                "gpu_deps_installed": True,
                "cuda_available": True,
                "python_executable": sys.executable,
            }
        ),
    )
    assert cli.main(["__managed-engine-health"]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is False

    monkeypatch.setattr(
        "httpx.get",
        lambda *_a, **_k: Response(
            {
                "status": "ok",
                "generation": "engine-v1",
                "gpu_deps_installed": False,
                "cuda_available": False,
                "python_executable": sys.executable,
                "detail": "GPU runtime not installed. Run: agent-index bootstrap",
            }
        ),
    )
    assert cli.main(["__managed-engine-health"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["healthy"] is False
    assert "not installed" in second["detail"]


def test_managed_engine_health_accepts_cpu_dependency_state(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_INDEX_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MANAGED_PYTHON", sys.executable)
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cpu")
    monkeypatch.setattr(
        transport, "plan_route", lambda: ("host", {"machine": "example-host"})
    )

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "status": "ok",
                "generation": "engine-v1",
                "gpu_deps_installed": True,
                "cuda_available": False,
                "python_executable": sys.executable,
            }

    monkeypatch.setattr("httpx.get", lambda *_a, **_k: Response())

    assert cli.main(["__managed-engine-health"]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True


def test_installers_are_base_only_and_never_implicitly_start_engine():
    ps = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert '"$PluginDir[store]"' not in ps
    assert '"$PLUGIN_DIR[store]"' not in sh
    ps_actions = ps.split("switch ($Action) {", 1)[1]
    sh_actions = sh.split('case "$ACTION" in', 1)[1]
    for action, following in (("install", "update"), ("update", "ensure")):
        ps_branch = ps_actions.split(f"'{action}' {{", 1)[1].split(
            f"'{following}'", 1
        )[0]
        sh_branch = sh_actions.split(f"{action})", 1)[1].split(
            f"{following})", 1
        )[0]
        assert "Ensure-" not in ps_branch
        assert "Install-Engine" not in ps_branch
        assert "_install_service" not in sh_branch
        assert "_install_engine" not in sh_branch
        assert "_ensure_engine" not in sh_branch


def test_cell_host_build_refuses_before_creating_any_environment(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "index_boundary_cell", PLUGIN / "scripts" / "cell-runtime.py"
    )
    assert spec and spec.loader
    cell = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cell)
    monkeypatch.setenv("AGENT_INDEX_CELL_BUILD_SMOKE", "1")
    slot = tmp_path / "never-created"
    with pytest.raises(cell.CellError, match="dispatch-managed"):
        cell._build_runtime(
            tmp_path / "absent-payload", slot,
            marketplace_id="example", runtime_version="1.0.0+host", role="host",
        )
    assert not slot.exists()


def test_lightweight_mcp_reports_unavailable_without_installing(monkeypatch, capsys):
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    assert cli.main(["mcp"]) == 2
    assert "will not install host dependencies" in capsys.readouterr().err


@pytest.mark.parametrize("managed", [False, True])
def test_workers_preserve_immutable_python_and_managed_containment(
    tmp_path, monkeypatch, managed
):
    from agent_index.indexing import runner

    if managed:
        monkeypatch.setenv("AGENT_INDEX_MANAGED_PYTHON", sys.executable)
    else:
        monkeypatch.delenv("AGENT_INDEX_MANAGED_PYTHON", raising=False)
    monkeypatch.setattr(runner, "detached_kwargs", lambda: {"legacy_detach": True})
    monkeypatch.setattr(runner, "no_window_kwargs", lambda: {"managed_containment": True})
    calls = []
    instance = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    with monkeypatch.context() as process_patch:
        process_patch.setattr(
            runner.subprocess, "Popen",
            lambda argv, **kwargs: calls.append((argv, kwargs)),
        )
        runner.TaskRunner._spawn_worker(instance, "synthetic-task")
    argv, kwargs = calls[0]
    assert argv[1:5] == ["-I", "-B", "-X", "utf8"]
    assert kwargs.get("managed_containment", False) == managed
    assert kwargs.get("legacy_detach", False) != managed
    # Exercise the worker's exact Python flags, without running an indexing job.
    probe = runner.subprocess.run(
        [sys.executable, *argv[1:5], "-c", "import sys; assert sys.dont_write_bytecode"],
        check=False, capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr
