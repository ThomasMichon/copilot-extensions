"""Source-aware suite caller: qualify the actual provider before any installation."""
from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import engine_client as ec
from worktree_manager import runtime_source_config as config
from worktree_manager import runtime_source_update as update
from worktree_manager import self_install, update_orchestration
from worktree_manager.runtime_source import ResolvedSource


def inventory(*names: str) -> dict:
    return {
        "contract": 1,
        "runtime_sources": {"schema": update.SCHEMA, "version": update.CONTRACT},
        "entries": [{
            "id": name, "title": name, "kind": "universal", "enabled": "active",
            "installed": True, "payload": "1", "selected": "1", "running": "1",
            "source": "marketplace", "updatable": True, "limitation": "",
        } for name in names],
    }


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


def enable() -> None:
    config.set_source(config.RuntimeSource("dev", allow_dev=True))


def test_marketplace_uses_no_capability_or_source_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ec, "engine_base_command", lambda: pytest.fail("default path is unchanged"))
    monkeypatch.setattr(update, "acquire", lambda *_: pytest.fail("no source acquisition"))
    arguments = ["--force"]
    assert update.prepare_suite(None, arguments) == update.PreparedSuite(arguments)
    assert update.prepare_suite(None, arguments).arguments is arguments


@pytest.mark.parametrize("capability", [None, {}, {"schema": update.SCHEMA, "version": True},
                                           {"schema": update.SCHEMA, "version": 2}])
def test_unsupported_provider_refused_before_acquisition(
    monkeypatch: pytest.MonkeyPatch, capability: object,
) -> None:
    enable()
    document = inventory("agent-worktrees")
    document["runtime_sources"] = capability
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["exact-provider"])
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: document)
    monkeypatch.setattr(update, "acquire", lambda *_: pytest.fail("unsupported engine cannot acquire"))
    with pytest.raises(ValueError, match="No update was run"):
        update.prepare_suite(None, [])


@pytest.mark.parametrize(("arguments", "expected"), [
    ([], ["agent-bridge", "agent-worktrees"]),
    (["--skip-modules"], ["agent-worktrees"]),
    (["--skip-modules", "agent-bridge", "--force"], ["agent-worktrees"]),
    (["--skip-modules=agent-bridge"], ["agent-worktrees"]),
])
def test_exact_inventory_eligibility_and_skip_semantics(arguments: list[str], expected: list[str]) -> None:
    document = inventory("agent-worktrees", "agent-bridge", "disabled", "payload-only")
    document["entries"][2]["updatable"] = False
    document["entries"][3]["kind"] = "none"
    assert update._targets(document, arguments) == expected


def test_refuses_second_packet_and_missing_engine() -> None:
    with pytest.raises(ValueError, match="second source packet"):
        update._targets(inventory("agent-worktrees"), ["--runtime-sources", "{}"])
    with pytest.raises(ValueError, match="no eligible engine"):
        update._targets(inventory("agent-bridge"), [])
    with pytest.raises(ValueError, match="Invalid source-backed update"):
        update._targets(inventory("agent-worktrees"), ["--runtime-sources"])
    document = inventory("agent-worktrees")
    document["entries"][0]["kind"] = "unsupported"
    with pytest.raises(ValueError, match="unsupported runtime scope"):
        update._targets(document, [])


@pytest.mark.parametrize("scoped", [False, True])
def test_one_selection_frozen_provider_and_persistent_packet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scoped: bool,
) -> None:
    enable()
    calls = []
    command = ["selected-python", "-I", "selected-engine"]
    environment = {"HOME": str(tmp_path), "FIXTURE": "selected"} if scoped else None
    monkeypatch.setattr(ec, "engine_base_command", lambda: command if not scoped else pytest.fail("stale handle"))
    monkeypatch.setattr(ec, "installed_engine_command", lambda env: command if env is environment else None)

    def read(project: str | None, arguments: list[str], **kwargs: object) -> dict:
        assert project == "example"
        assert arguments == ["update", "--inventory", "--json"]
        assert kwargs == {"engine_command": command, **({"environment": environment} if scoped else {})}
        calls.append("capability")
        return inventory("agent-worktrees", "agent-bridge")

    @contextmanager
    def acquire(selection: config.RuntimeSource):
        calls.append("acquire")
        yield ResolvedSource(selection, tmp_path, "a" * 40, "b" * 40, "a" * 40)
        calls.append("released")

    def prepare(tool: Path, resolved: ResolvedSource, name: str, output: Path) -> dict:
        assert calls[0:2] == ["capability", "acquire"]
        calls.append(name)
        assert resolved.commit == "a" * 40
        assert output == tmp_path / "manager/runtime-sources/prepared"
        return {"payload": str(output / name), "identity": {"commit": resolved.commit}}

    monkeypatch.setattr(ec, "run_json", read)
    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "_preparer", lambda: tmp_path / "trusted-preparer")
    monkeypatch.setattr(update, "_prepare", prepare)
    prepared = update.prepare_suite("example", ["--force"], environment=environment)
    command.append("changed-after-probe")
    assert prepared.engine_command == command[:-1]
    assert calls == ["capability", "acquire", "agent-bridge", "agent-worktrees", "released"]
    assert prepared.arguments[:2] == ["--force", "--runtime-sources"]
    packet = json.loads(prepared.arguments[2])
    assert packet["schema"] == update.SCHEMA and packet["version"] == 1
    assert set(packet["sources"]) == {"agent-bridge", "agent-worktrees"}


def test_orchestration_preparation_failure_precedes_independent_self_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable()
    from worktree_manager import update_events

    monkeypatch.setattr(update_events, "_ut", None)
    monkeypatch.setattr(self_install, "self_update", lambda **kw: pytest.fail("no Manager mutation"))
    monkeypatch.setattr(ec, "run_engine_passthrough", lambda *a, **kw: pytest.fail("no suite mutation"))

    def refuse(*args: object, **kwargs: object) -> None:
        raise ValueError("selected provider unsupported")

    monkeypatch.setattr(update, "prepare_suite", refuse)
    run = update_events.UpdateRun()
    assert update_orchestration.run_update_steps([], run, version="1") == 1
    assert run.finish(1) == 1


def test_orchestration_passes_same_provider_and_source_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import __main__ as manager, update_events

    enable()
    calls = []
    selected = ["exact", "provider"]
    environment = {"FIXTURE": "selected"}
    monkeypatch.setattr(update_events, "_ut", None)

    def prepare(project: str | None, arguments: list[str], **kwargs: object) -> update.PreparedSuite:
        assert project == "sample" and arguments == ["--force"]
        assert kwargs == {"environment": environment}
        calls.append("prepare")
        return update.PreparedSuite(["--force", "--runtime-sources", "packet"], selected)

    def self_update(**kwargs: object) -> self_install.SelfUpdateResult:
        calls.append("manager")
        assert kwargs == {"dry_run": False}
        return self_install.SelfUpdateResult(action="already-current", version="1")

    def execute(project: str | None, arguments: list[str], **kwargs: object) -> int:
        calls.append("engine")
        assert project == "sample"
        assert arguments == ["update", "--no-manager", "--force", "--runtime-sources", "packet"]
        assert kwargs == {"environment": environment, "engine_command": selected}
        return 17

    monkeypatch.setattr(update, "prepare_suite", prepare)
    monkeypatch.setattr(self_install, "self_update", self_update)
    monkeypatch.setattr(ec, "run_engine_passthrough", execute)
    monkeypatch.setattr("worktree_manager.update_bootstrap.prepare_update", lambda arguments: SimpleNamespace(
        arguments=arguments, fresh_manager=False, exit_code=0, environment=environment,
    ))
    assert manager._cmd_update(["--project", "sample", "--force"]) == 17
    assert calls == ["prepare", "manager", "engine"]


@pytest.mark.parametrize("fault", ["", "exit", "timeout", "commit", "outside", "bytes"])
def test_preparer_process_is_own_isolated_code_and_receipt_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    source = self_install._runtime_installer().runtime_source
    output = tmp_path / "prepared"
    digest = "c" * 64
    version = source.runtime_version("1.0.0", "a" * 40, digest)
    identity = source.SourceIdentity("agent-worktrees", "1.0.0", "a" * 40, "b" * 40, digest, version)
    document = identity.document()
    payload = output / "agent-worktrees" / version
    resolved = ResolvedSource(config.RuntimeSource("dev", allow_dev=True), tmp_path / "untrusted", "a" * 40, "b" * 40, "a" * 40)
    tool = tmp_path / "trusted/preparer.py"

    def execute(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert command == [
            sys.executable, "-I", "-B", "-X", "utf8", str(tool),
            "--source", str(resolved.checkout), "--commit", "a" * 40,
            "--branch", "dev", "--plugin", "agent-worktrees", "--output", str(output),
        ]
        assert kwargs["timeout"] == 600 and kwargs["stdin"] == subprocess.DEVNULL
        assert "PYTHONPATH" not in kwargs["env"]
        if fault == "timeout":
            raise subprocess.TimeoutExpired(command, 600)
        if fault == "commit":
            document["source_commit"] = "d" * 40
        return SimpleNamespace(returncode=23 if fault == "exit" else 0, stderr="refused", stdout=json.dumps({
            "payload": str(tmp_path / version if fault == "outside" else payload), "identity": document,
        }))

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hostile"))
    monkeypatch.setattr(update.subprocess, "run", execute)
    monkeypatch.setattr(source, "read_source_identity", lambda *a, **kw: None if fault == "bytes" else identity)
    if fault:
        with pytest.raises(ValueError):
            update._prepare(tool, resolved, "agent-worktrees", output)
    else:
        record = update._prepare(tool, resolved, "agent-worktrees", output)
        assert record == {"payload": str(payload), "identity": {"runtime_source": document, "runtime_version": version}}


def test_json_and_passthrough_share_exact_scoped_command_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = ["selected", "engine"]
    environment = {"SELECTED": "1", "PYTHONPATH": "parent-must-not-leak"}
    calls = []
    monkeypatch.setattr(ec, "engine_base_command", lambda: pytest.fail("no stale engine handle"))
    monkeypatch.setattr(ec, "installed_engine_command", lambda env: command)
    monkeypatch.setattr("worktree_manager.update_driver_cli.handoff_environment", lambda args: {})
    monkeypatch.setattr("worktree_manager.update_driver_cli.handoff_launch_options", lambda env, handoff: {"env": env})

    def execute(argv: list[str], **kwargs: object) -> SimpleNamespace:
        assert argv[:2] == command
        assert kwargs["env"] == {"SELECTED": "1", "PYTHONUTF8": "1", "PYTHONSAFEPATH": "1"}
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"contract": 1}')

    monkeypatch.setattr(ec.subprocess, "run", execute)
    assert ec.run_json(None, ["update", "--inventory", "--json"], environment=environment) == {"contract": 1}
    assert ec.run_engine_passthrough(None, ["update", "--no-manager"], environment=environment, engine_command=command) == 0
    assert len(calls) == 2
