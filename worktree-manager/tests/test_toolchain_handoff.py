"""Configured routing crosses the actual selected-engine and custody boundaries."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from collections.abc import Iterator

import pytest
import update_driver

from worktree_manager import engine_client, self_install, toolchain_config, update_driver_cli


_ENGINE = """
import argparse,json,os,sys
from pathlib import Path
sys.path[:0]=json.loads(sys.argv[1])
from agent_worktrees import update_driver_cli as engine
if "--inventory" in sys.argv:
    print(json.dumps({"toolchain_request_context":engine.TOOLCHAIN_REQUEST_CONTEXT}
                     if sys.argv[3]=="current" else {}))
    raise SystemExit(0)
def consume(args):
    Path(sys.argv[2]).write_text(json.dumps(dict(os.environ)),encoding="utf-8")
    return 0
engine.update_cli.cmd_update=consume
raise SystemExit(engine.cmd_update_request(argparse.Namespace(no_manager=True)))
"""


@pytest.mark.parametrize("provider", ["current", "prior"])
def test_toolchain_context_negotiates_actual_queued_manager_to_engine_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    home, root = tmp_path / "home", tmp_path / "manager"
    home.mkdir()
    for key in (*update_driver_cli._SUPPORTED_CONTEXT_KEYS, *update_driver_cli._NPM_CONTEXT_ALIASES.values()):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "HOME": str(home), "USERPROFILE": str(home), "WORKTREE_MANAGER_ROOT": str(root),
        "PIP_CONFIG_FILE": str(home / "pip.ini"),
        "PIP_INDEX_URL": "https://caller.example.invalid/simple/",
        "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG": str(home / "toolchain.conf"),
    }.items():
        monkeypatch.setenv(key, value)
    mirror = "https://downloads.example.invalid/uv/"
    registry = "https://packages.example.invalid/npm/"
    (home / "pip.ini").write_text("[global]\ntimeout = 73\n", encoding="ascii")
    (home / "toolchain.conf").write_text(
        f"schema=1\nuv_install_mirror={mirror}\nnpm_registry={registry}\n",
        encoding="ascii",
    )
    context = update_driver_cli.installation_request_context()
    fixture, report = tmp_path / "engine.py", tmp_path / "result.json"
    fixture.write_text(_ENGINE, encoding="utf-8")
    repository = Path(__file__).resolve().parents[2]
    paths = [str(repository / "plugins" / "agent-worktrees" / "src"), *sys.path]
    command = [sys.executable, "-I", "-B", str(fixture), json.dumps(paths), str(report), provider]
    program = """
import json,os,sys,update_driver
from pathlib import Path
sys.path[:0]=json.loads(sys.argv[1])
from worktree_manager import engine_client,toolchain_config
try:
    with update_driver.borrowed_custody():
        toolchain_config.configure_selected(root=Path(os.environ["WORKTREE_MANAGER_ROOT"]),apply=True)
        result=engine_client.run_engine_passthrough(None,["update","--no-manager"],
                                                  engine_command=json.loads(sys.argv[2]))
except engine_client.EngineError as error:
    print(str(error))
    result=19
raise SystemExit(result)
"""
    queue = home / ".copilot-extensions" / "update-driver"
    request = update_driver.enqueue(
        update_driver.RequestSpec(
            "manager", ("toolchain-handoff", provider), cwd=str(tmp_path),
            context=tuple(context.items()),
        ),
        runner=update_driver.TrustedRunner(
            (sys.executable, "-I", "-B", "-c", program, json.dumps(paths), json.dumps(command)),
            str(tmp_path), context_keys=update_driver_cli._runner_context_keys(context),
        ), root=queue,
    )
    monkeypatch.setenv("PIP_INDEX_URL", "https://drainer.example.invalid/simple/")
    monkeypatch.setenv("UV_CONFIG_FILE", "foreign-drainer-config")
    monkeypatch.setenv("UV_INSTALLER_GITHUB_BASE_URL", "https://drainer.example.invalid/uv/")
    monkeypatch.setenv("npm_config_registry", "https://drainer.example.invalid/npm/")
    assert update_driver.drain(root=queue) == 0
    result = update_driver.status(request.id, root=queue)
    assert not isinstance(result, tuple)
    log = result.log_path.read_text(encoding="utf-8")
    assert result.state == "completed", log
    if provider == "current":
        assert result.exit_code == 0, log
        received = json.loads(report.read_text(encoding="utf-8"))
        assert received["PIP_INDEX_URL"] == context["PIP_INDEX_URL"]
        assert received["PIP_CONFIG_FILE"] == context["PIP_CONFIG_FILE"]
        assert received["COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG"] == context["COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG"]
        assert received["UV_INSTALLER_GITHUB_BASE_URL"] == mirror
        assert received["NPM_CONFIG_REGISTRY" if os.name == "nt" else "npm_config_registry"] == registry
        assert "UV_CONFIG_FILE" not in received
    else:
        assert result.exit_code == 19, log
        assert "lacks toolchain request context v1" in log
        assert not report.exists()
    assert os.environ["UV_CONFIG_FILE"] == "foreign-drainer-config"
    assert os.environ["UV_INSTALLER_GITHUB_BASE_URL"] == "https://drainer.example.invalid/uv/"
    assert os.environ["npm_config_registry"] == "https://drainer.example.invalid/npm/"


@pytest.mark.parametrize("capability", [None, {}, {"schema": "wrong", "version": 1},
                                      {"schema": "copilot-extensions.toolchain-request-context", "version": True}])
def test_toolchain_handoff_refuses_missing_or_invalid_support_before_effect(
    monkeypatch: pytest.MonkeyPatch, capability: Any,
) -> None:
    from worktree_manager.engine_toolchain_context import require_context

    calls: list[Any] = []

    def inventory(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"toolchain_request_context": capability}

    monkeypatch.setattr(engine_client, "run_json", inventory)
    command = ["selected-engine"]
    environment = {
        "COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST": "1",
        "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG": "/selected/toolchain.conf",
    }
    with pytest.raises(engine_client.EngineError, match="lacks toolchain"):
        require_context(None, ["update", "--no-manager"], command, environment)
    assert calls[0][1]["engine_command"] == command
    calls.clear()
    require_context(None, ["update", "--no-manager"], command, {})
    assert not calls


@pytest.mark.parametrize("conflict", [False, True])
def test_queued_manager_update_applies_toolchain_before_first_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: bool,
) -> None:
    home, root = tmp_path / "home", tmp_path / "manager"
    home.mkdir()
    for key in (*update_driver_cli._SUPPORTED_CONTEXT_KEYS,
                *update_driver_cli._NPM_CONTEXT_ALIASES.values()):
        monkeypatch.delenv(key, raising=False)
    config = home / "selected.conf"
    index = "https://caller.example.invalid/simple/"
    config.write_text(f"schema=1\npython_index_url={index}\n", encoding="ascii")
    for key, value in {
        "HOME": str(home), "USERPROFILE": str(home), "WORKTREE_MANAGER_ROOT": str(root),
        "APPDATA": str(home / "appdata"), "PROGRAMDATA": str(home / "programdata"),
        "XDG_CONFIG_HOME": str(home / "config"), "XDG_CONFIG_DIRS": str(home / "system"),
        "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG": str(config),
    }.items():
        monkeypatch.setenv(key, value)
    context = update_driver_cli.installation_request_context()
    if conflict:
        config.write_text(
            "schema=1\npython_index_url=https://other.example.invalid/simple/\n", encoding="ascii",
        )
    report = tmp_path / "result.json"
    program = """
import json,os,sys
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=json.loads(sys.argv[1])
from worktree_manager import __main__ as manager, project_update_controller, update_driver_cli
update_driver_cli._current_authority=lambda *a,**k: SimpleNamespace(configured=False)
update_driver_cli._source_key=lambda: "fixture-source"
def effect(*args,**kwargs):
    root=Path(os.environ["WORKTREE_MANAGER_ROOT"])
    assert (root/"toolchain.conf").is_file(), "configuration not applied before update"
    assert os.environ["PIP_INDEX_URL"]=="https://caller.example.invalid/simple/"
    Path(sys.argv[2]).write_text(json.dumps({"applied":True}),encoding="utf-8")
    return 7
project_update_controller.handoff=effect
manager._cmd_update=effect
raise SystemExit(update_driver_cli.cmd_update_request([]))
"""
    queue = home / ".copilot-extensions" / "update-driver"
    request = update_driver.enqueue(
        update_driver.RequestSpec(
            "manager", (), cwd=str(tmp_path), source="fixture-source",
            context=tuple(context.items()),
        ),
        runner=update_driver.TrustedRunner(
            (sys.executable, "-I", "-B", "-c", program, json.dumps(sys.path), str(report)),
            str(tmp_path), context_keys=update_driver_cli._runner_context_keys(context),
        ), root=queue,
    )
    assert update_driver.drain(root=queue) == 0
    result = update_driver.status(request.id, root=queue)
    assert not isinstance(result, tuple)
    assert result.state == "completed", result.log_path.read_text(encoding="utf-8")
    assert result.exit_code == (1 if conflict else 7), result.log_path.read_text(encoding="utf-8")
    assert report.exists() is not conflict
    if conflict:
        assert "conflicts with toolchain configuration" in result.log_path.read_text(encoding="utf-8")
        assert not (root / "toolchain.conf").exists()


@pytest.mark.parametrize("automatic", [False, True])
def test_configured_target_is_enqueued_before_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    automatic: bool,
) -> None:
    from worktree_manager import project_update_controller

    for key in update_driver_cli._SUPPORTED_CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / "selected.conf"
    config.write_text("schema=1\npython_index_url=https://caller.example.invalid/simple/\n", encoding="ascii")
    monkeypatch.setenv("COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG", str(config))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.setenv("PIP_INDEX_URL", "https://caller.example.invalid/simple/")
    monkeypatch.delenv(update_driver_cli._UNATTENDED, raising=False)
    for key in update_driver_cli._TELEMETRY_KEYS:
        monkeypatch.setenv(key, "fixture-telemetry")
    monkeypatch.setattr(update_driver_cli, "request_source_anchor", lambda: str(tmp_path))
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "fixture-source")
    monkeypatch.setattr(update_driver_cli, "_current_authority",
                        lambda *_a, **_k: SimpleNamespace(configured=False))
    requests: list[update_driver.RequestSpec] = []
    runners: list[update_driver.TrustedRunner] = []

    def enqueue(
        spec: update_driver.RequestSpec, *, runner: update_driver.TrustedRunner,
        **_kwargs: Any,
    ) -> Any:
        requests.append(spec)
        runners.append(runner)
        raise update_driver.StateError("fixture stops before execution")

    def premature_handoff(*_args: Any, **_kwargs: Any) -> int:
        pytest.fail("configured target bypassed its Manager participant")

    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(project_update_controller, "handoff", premature_handoff)
    monkeypatch.setattr(engine_client, "run_engine_passthrough", premature_handoff)
    arguments = ["--target", "agent-index"]
    if automatic:
        arguments.extend(("--automatic-trigger", "manager"))
    assert update_driver_cli.cmd_update_request(arguments) == 1
    assert len(requests) == 1 and requests[0].backend == "manager"
    assert requests[0].argv == (*arguments, *(["--auto"] if automatic else []))
    retained = dict(requests[0].context)
    assert retained["PIP_INDEX_URL"] == "https://caller.example.invalid/simple/"
    assert retained[update_driver_cli._TOOLCHAIN_REQUEST_KEY] == "1"
    assert set(retained) <= set(runners[0].context_keys)
    assert not set(update_driver_cli._TELEMETRY_KEYS) & retained.keys()
    if automatic:
        assert update_driver_cli._UNATTENDED in runners[0].context_keys
        assert set(update_driver_cli._TELEMETRY_KEYS) <= set(runners[0].context_keys)
    else:
        assert not set(update_driver_cli._TELEMETRY_KEYS) & set(runners[0].context_keys)
    assert "fixture stops before execution" in capsys.readouterr().err
    assert not (tmp_path / "manager" / "toolchain.conf").exists()


def test_configured_manager_target_remains_visible_in_durable_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import agent_plugin_runtime, installation_updates

    log = tmp_path / "request.log"
    log.write_bytes(b"configured target\n")
    engine = tmp_path / "selected-engine"
    context = {"COPILOT_EXTENSIONS_CONTEXT": str(tmp_path / "engine-install.json")}
    request = SimpleNamespace(
        spec=SimpleNamespace(
            backend="manager", source="fixture-source", argv=("--target", "agent-index"),
            context=tuple(context.items()),
        ),
        id="fixture-request", state="completed", error=None, exit_code=0, log_path=log,
    )

    def resolve(plugin: str, *, environment: dict[str, str]) -> tuple[list[str], Path]:
        assert plugin == "agent-worktrees" and environment == context
        return (["/fixture/engine"], engine)

    monkeypatch.setattr(update_driver, "status", lambda: (request,))
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "fixture-source")
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_invocation", resolve)
    operation = installation_updates._operations(engine_source=str(engine))["agent-index"]
    assert operation["state"] == "succeeded"
    assert operation["request_id"] == request.id
    assert operation["progress"] == "configured target\n"


@pytest.mark.parametrize("conflict", [False, True])
def test_self_install_participant_applies_toolchain_before_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: bool,
) -> None:
    root = tmp_path / "manager"
    root.mkdir()
    config = root / "toolchain.conf"
    index = "https://caller.example.invalid/simple/"
    config.write_text(f"schema=1\npython_index_url={index}\n", encoding="ascii")
    for key in (*update_driver_cli._TOOLCHAIN_CONTEXT_KEYS, *update_driver_cli._NPM_CONTEXT_ALIASES.values()):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setenv("COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG", str(config))
    pip, uv = tmp_path / "pip.ini", tmp_path / "uv.toml"
    monkeypatch.setattr(toolchain_config, "_native_paths", lambda *_args: ([pip], uv, pip))
    monkeypatch.setattr(toolchain_config, "_system_uv_path", lambda *_args, **_kwargs: None)
    held: list[bool] = []

    @contextmanager
    def custody() -> Iterator[object]:
        held.append(True)
        try:
            yield object()
        finally:
            held.pop()

    monkeypatch.setattr(self_install, "_runtime_installer", lambda: SimpleNamespace(_installation_custody=custody))
    if conflict:
        monkeypatch.setenv("PIP_INDEX_URL", "https://other.example.invalid/simple/")
    calls: list[str] = []

    def candidate() -> int:
        assert held and index in pip.read_text(encoding="utf-8")
        assert os.environ["PIP_INDEX_URL"] == index
        calls.append("candidate")
        return 7

    assert update_driver_cli.self_install_request(candidate) == (1 if conflict else 7)
    assert calls == ([] if conflict else ["candidate"])
    if conflict:
        assert not pip.exists() and not uv.exists()
