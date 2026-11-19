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
    for key in update_driver_cli._SUPPORTED_CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "HOME": str(home), "USERPROFILE": str(home), "WORKTREE_MANAGER_ROOT": str(root),
        "PIP_CONFIG_FILE": str(home / "pip.ini"),
        "PIP_INDEX_URL": "https://caller.example.invalid/simple/",
        "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG": str(home / "toolchain.conf"),
    }.items():
        monkeypatch.setenv(key, value)
    (home / "toolchain.conf").write_text("schema=1\npython_index_url=https://caller.example.invalid/simple/\n")
    context = update_driver_cli.installation_request_context()
    fixture, report = tmp_path / "engine.py", tmp_path / "result.json"
    fixture.write_text(_ENGINE, encoding="utf-8")
    repository = Path(__file__).resolve().parents[2]
    paths = [str(repository / "plugins" / "agent-worktrees" / "src"), *sys.path]
    command = [sys.executable, "-I", "-B", str(fixture), json.dumps(paths), str(report), provider]
    program = """
import json,sys,update_driver
sys.path[:0]=json.loads(sys.argv[1])
from worktree_manager import engine_client
try:
    with update_driver.borrowed_custody():
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
        assert "UV_CONFIG_FILE" not in received
    else:
        assert result.exit_code == 19, log
        assert "lacks toolchain request context v1" in log
        assert not report.exists()
    assert os.environ["UV_CONFIG_FILE"] == "foreign-drainer-config"


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
