"""Resident entry consumes one scoped handoff without another bootstrap CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest

from worktree_manager import engine_client, process_priority, resident_picker
from worktree_manager.production_picker import engine_group_b, runner

if TYPE_CHECKING:
    from worktree_manager import picker_app
    from worktree_manager.production_picker.picker_tui import data_ssh

Handoff = dict[str, Any]


class SourceFactory(Protocol):
    def __call__(
        self, machine: str, env: str, argv: list[str] | None, *, alias: str,
    ) -> data_ssh.Source: ...


@pytest.fixture
def handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Handoff:
    monkeypatch.setattr(resident_picker, "_binding", None)
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", None)
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", None)
    monkeypatch.delenv(engine_client.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(["qualified-engine"]))
    monkeypatch.setattr(process_priority, "raise_current_process_priority", lambda: None)
    monkeypatch.setattr(process_priority, "restore_normal_process_priority", lambda: None)
    payload = {
        "ok": True, "contract_version": 1, "runtime_root": str(tmp_path),
        "generation": "a" * 32,
        "bootstrap": {
            "version": 1, "project": "sample", "should_switch_cwd": False,
            "cwd": None, "workspace": None, "default_live": True, "headless": False,
            "project_config": {
                "config_dir": str(tmp_path / "sample"), "repo_dir": str(tmp_path),
                "machine": "fixture-host",
                "install_dir": str(tmp_path),
            },
        },
    }
    return payload


def test_entry_consumes_a_scoped_bootstrap_and_preserves_engine_selection(
    handoff: Handoff, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import warm_picker_client

    monkeypatch.setattr(
        warm_picker_client, "run",
        lambda *_a: pytest.fail("app entry must not wait on the Manager daemon"),
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(handoff))
    monkeypatch.setattr(runner, "run", lambda project, **kwargs: calls.append((project, kwargs)))
    monkeypatch.setattr(
        engine_group_b, "picker_bootstrap",
        lambda *_args: pytest.fail("resident entry repeated the bootstrap CLI"),
    )
    assert resident_picker.main(["--project", "sample"]) == 0
    assert calls == [("sample", {"bootstrap_payload": handoff["bootstrap"]})]
    assert resident_picker.root_for("sample") == tmp_path
    assert resident_picker.root_for("other") is None
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", ["different-engine"])
    assert resident_picker.root_for("sample") is None


@pytest.mark.parametrize("invalid", ["headless", "project", "root", "payload", "version", "live"])
def test_invalid_or_foreign_handoff_never_opens_the_ui(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    if invalid == "headless":
        handoff["bootstrap"]["headless"] = True
    elif invalid == "project":
        handoff["bootstrap"]["project"] = "other"
    elif invalid == "root":
        handoff["runtime_root"] = "relative"
    elif invalid == "payload":
        handoff["bootstrap"] = None
    elif invalid == "version":
        handoff["bootstrap"]["version"] = True
    else:
        handoff["bootstrap"]["default_live"] = "true"
    monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(handoff))
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: pytest.fail("invalid handoff opened the UI"))
    assert resident_picker.main(["--project", "sample"]) == 2
    assert resident_picker.root_for("sample") is None


def test_first_decision_is_executed_without_reopening_the_picker(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import __main__ as cli

    monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(handoff))
    decision = {"action": "new"}
    monkeypatch.setattr(runner, "run", lambda *_args, **_kwargs: decision)
    calls: list[tuple[str, dict[str, object]]] = []

    def run_production(project: str, **kwargs: object) -> int:
        calls.append((project, kwargs))
        return 17

    monkeypatch.setattr(cli, "_run_production_picker", run_production)
    assert resident_picker.main(["--project", "sample"]) == 17
    assert calls == [("sample", {"initial_decision": decision})]


@pytest.mark.parametrize("switch_to_anchor", [False, True])
def test_runner_binds_scalars_without_any_bootstrap_or_scalar_cli(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, switch_to_anchor: bool,
) -> None:
    from worktree_manager.production_picker import project_config

    caller = tmp_path / "caller"
    anchor = tmp_path / "anchor"
    caller.mkdir()
    anchor.mkdir()
    monkeypatch.chdir(caller)
    handoff["bootstrap"].update(should_switch_cwd=switch_to_anchor, cwd=str(anchor))
    monkeypatch.setattr(engine_client, "engine_available", lambda: pytest.fail("repeated engine resolution"))
    monkeypatch.setattr(engine_group_b, "picker_bootstrap", lambda *_args: pytest.fail("bootstrap CLI"))
    monkeypatch.setattr(engine_client, "get_value", lambda *_args: pytest.fail("scalar CLI"))

    def run_picker(**kwargs: object) -> None:
        assert os.getcwd() == str(anchor if switch_to_anchor else caller)
        assert kwargs["live"] is True
        assert "prepare" not in kwargs
        assert project_config._machine_name("sample") == "fixture-host"
        assert project_config._repo_dir("sample") == handoff["runtime_root"]
        assert str(project_config.install_dir("sample")) == handoff["runtime_root"]

    monkeypatch.setattr(runner, "run_tui_picker", run_picker)
    try:
        assert runner.run("sample", bootstrap_payload=handoff["bootstrap"]) is None
    finally:
        project_config.clear_caches(reset_binding=True)


@pytest.mark.parametrize("action", ["cancel", "new", "attach-failed", "foreign", "replay"])
def test_retained_result_is_scoped_and_consumed_once_before_actions(
    handoff: Handoff, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    from worktree_manager import __main__ as cli

    handoff["manager_session"] = {"version": 1, "root": str(tmp_path)}
    directory = tmp_path / "picker-sessions" / "generation" / "session"
    directory.mkdir(parents=True)
    request = {"handoff": handoff, "engine": ["qualified-engine"]}
    if action == "foreign":
        request = {**request, "engine": ["foreign-engine"]}
    (directory / "request.json").write_text(json.dumps(request))
    decision = {"action": "new"} if action in {"new", "replay"} else None
    (directory / "result.json").write_text(json.dumps({"ok": True, "decision": decision}))
    calls: list[tuple[str, dict[str, object]]] = []

    def run_production(project: str, **kwargs: object) -> int:
        calls.append((project, kwargs))
        return 17

    monkeypatch.setattr(cli, "_run_production_picker", run_production)
    args = [
        "--project", "sample", "--directory", str(directory),
        "--manager-root", str(tmp_path), "--attach-exit", "130" if action == "attach-failed" else "0",
    ]

    def finish() -> int:
        monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(handoff))
        monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(["qualified-engine"]))
        return resident_picker.finish(args)

    expected = {"cancel": 0, "new": 17, "attach-failed": 130, "foreign": 1, "replay": 17}
    assert finish() == expected[action]
    assert len(calls) == (1 if action in {"new", "replay"} else 0)
    assert (directory / "consumed").exists() is (action != "foreign")
    if action == "replay":
        assert finish() == 1
        assert len(calls) == 1


@pytest.mark.parametrize("entry", ["direct", "retained"])
@pytest.mark.parametrize("switch_to_anchor", [False, True])
def test_remote_restore_keeps_bootstrap_project_and_scalars(
    handoff: Handoff, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    entry: str, switch_to_anchor: bool,
) -> None:
    from worktree_manager import __main__ as cli
    from worktree_manager.production_picker import context, project_config
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    caller, anchor = tmp_path / "caller", tmp_path / "anchor"
    caller.mkdir()
    anchor.mkdir()
    monkeypatch.chdir(caller)
    handoff["bootstrap"].update(should_switch_cwd=switch_to_anchor, cwd=str(anchor))
    monkeypatch.setattr(context, "_project", None)
    monkeypatch.setattr(context, "_bootstrap", None)
    monkeypatch.setattr(project_config, "_scalar_cache", project_config._ScalarCache())
    monkeypatch.setattr(
        engine_client, "get_value",
        lambda *_a, **_k: pytest.fail("completion repeated a bootstrap scalar query"),
    )
    decision = {
        "action": "restore", "worktree_id": "fixture-worktree", "is_local": False,
        "machine": "fixture-peer", "env": "Linux",
    }
    projects: list[str] = []
    commands: list[list[str]] = []
    launches: list[picker_app.LaunchRequest] = []
    source_factory = cast(SourceFactory, data_ssh.Source)

    def sources() -> list[data_ssh.Source]:
        projects.append(data_ssh._project())
        if projects[-1] != "sample":
            return []
        return [source_factory("fixture-peer", "Linux", None, alias="fixture-ssh")]

    def remux(argv: list[str]) -> dict[str, object]:
        assert project_config._machine_name("sample") == "fixture-host"
        assert project_config._repo_dir("sample") == handoff["runtime_root"]
        assert str(project_config.install_dir("sample")) == handoff["runtime_root"]
        assert os.getcwd() == str(anchor if switch_to_anchor else caller)
        commands.append(argv)
        return {"ok": True, "action": "reclaimed"}

    monkeypatch.setattr(data_ssh, "_build_sources", sources)
    monkeypatch.setattr(maintenance, "_ssh_json", remux)
    def launch(request: picker_app.LaunchRequest) -> int:
        launches.append(request)
        return 17

    monkeypatch.setattr(cli, "_run_launch", launch)
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: pytest.fail("completion reopened the UI"))
    if entry == "direct":
        monkeypatch.setattr(runner, "run", lambda *_a, **_k: decision)
        args = ["--project", "sample"]
        invoke = resident_picker.main
    else:
        handoff["manager_session"] = {"version": 1, "root": str(tmp_path)}
        directory = tmp_path / "picker-sessions" / "generation" / "session"
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps({
            "handoff": handoff, "engine": ["qualified-engine"],
        }))
        (directory / "result.json").write_text(json.dumps({"ok": True, "decision": decision}))
        args = [
            "--project", "sample", "--directory", str(directory),
            "--manager-root", str(tmp_path), "--attach-exit", "0",
        ]
        invoke = resident_picker.finish
    monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(handoff))
    try:
        assert invoke(args) == 17
        assert projects == ["sample"]
        assert len(commands) == len(launches) == 1
        assert "sample remux --worktree-id fixture-worktree --yes --json" in commands[0][-1]
        assert launches[0].project == "sample"
    finally:
        project_config.clear_caches(reset_binding=True)


def test_optional_startup_waits_only_for_its_own_local_feed_and_honors_cancellation(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(handoff))
    resident_picker.consume(["--project", "sample"])
    finished = threading.Event()
    cancelled = threading.Event()
    result: list[bool] = []

    def wait() -> None:
        result.append(resident_picker.wait_for_local("sample", cancelled.is_set))
        finished.set()

    thread = threading.Thread(target=wait)
    thread.start()
    try:
        assert not finished.wait(0.02)
        resident_picker.settle_local("foreign")
        assert not finished.wait(0.02)
        resident_picker.settle_local("sample")
        assert finished.wait(1)
        assert result == [True]
        cancelled.set()
        assert resident_picker.wait_for_local("sample", cancelled.is_set) is False
    finally:
        cancelled.set()
        thread.join(timeout=1)
    assert not thread.is_alive()


def test_optional_startup_has_a_bounded_escape_when_local_feed_never_settles(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(handoff))
    resident_picker.consume(["--project", "sample"])
    times = iter([0.0, 2.1])
    monkeypatch.setattr(resident_picker, "time", SimpleNamespace(monotonic=lambda: next(times)))
    assert resident_picker.wait_for_local("sample", lambda: False)
