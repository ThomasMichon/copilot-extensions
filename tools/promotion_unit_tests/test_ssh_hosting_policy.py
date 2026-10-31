"""Cold SSH policy reads honor the connection owner's precondition."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_ssh_hosting"


class HostingRefused(RuntimeError):
    pass


class AsyncDeadlineError(Exception):
    pass


@dataclass
class ColdManager:
    connected: bool = False
    under_deadline: bool = False
    forwards: list[str] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    failure: BaseException | None = None
    result: Any = field(default_factory=lambda: SimpleNamespace(
        timed_out=False, exit_code=0, stdout='{"version":1,"allowed":true}', stderr="",
    ))

    async def ensure_connected(
        self, host: str, source: Any, port_forwards: list[str] | None = None,
        *, preserve_existing_forwards: bool = False,
    ) -> None:
        assert self.under_deadline, "connection setup escaped the shared query deadline"
        self.calls.append(("connect", (host, source, port_forwards, preserve_existing_forwards)))
        if self.failure is not None:
            raise self.failure
        if not preserve_existing_forwards:
            self.forwards = list(port_forwards or [])
        self.connected = True

    async def exec_command(self, host: str, command: str, *, timeout: float) -> Any:
        if not self.connected:
            raise RuntimeError(f"No connection to {host}. Call ensure_connected() first.")
        assert self.under_deadline, "policy execution escaped the shared query deadline"
        self.calls.append(("exec", (host, command, timeout)))
        return self.result


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, ColdManager, list]:
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("SSH policy query must not use local policy or agent preparation")

    def module(name: str, **attributes: Any) -> ModuleType:
        value = ModuleType(name)
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    root = ROOT / "plugins/agent-bridge/src/agent_bridge"
    module(PACKAGE, __path__=[str(root)])
    module("promotion_ssh_manager", __path__=[str(ROOT / "libs/ssh-manager/src/ssh_manager")])
    config_name = "promotion_ssh_manager.config_sources"
    config_spec = importlib.util.spec_from_file_location(
        config_name, ROOT / "libs/ssh-manager/src/ssh_manager/config_sources.py",
    )
    assert config_spec and config_spec.loader
    config = importlib.util.module_from_spec(config_spec)
    monkeypatch.setitem(sys.modules, config_name, config)
    config_spec.loader.exec_module(config)
    manager = ColdManager()
    module(
        "ssh_manager", get_default_manager=lambda: manager,
        SSHProfileSource=config.SSHProfileSource,
        SSHProfileResolutionError=config.SSHProfileResolutionError,
    )
    module(
        "repository_policy", AgentHostingError=HostingRefused,
        require_agent_hosting=unavailable, validate_hosting_result=unavailable,
    )
    commands: list[tuple[str, list[str], str | None]] = []

    def command(project: str, arguments: list[str], shell: str | None) -> str:
        commands.append((project, arguments, shell))
        return "fixed-owner-policy-query"

    module(f"{PACKAGE}.worktree_owner_operation", owner_ssh_command=command)
    name = f"{PACKAGE}.hosting_policy"
    spec = importlib.util.spec_from_file_location(name, root / "hosting_policy.py")
    assert spec and spec.loader
    policy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, policy)
    spec.loader.exec_module(policy)

    async def bounded(coroutine: Coroutine[Any, Any, Any], *, timeout: float) -> Any:
        assert timeout == 30
        manager.under_deadline = True
        try:
            return await coroutine
        finally:
            manager.under_deadline = False

    monkeypatch.setattr(
        policy, "asyncio", SimpleNamespace(wait_for=bounded, TimeoutError=AsyncDeadlineError),
        raising=False,
    )
    return policy, manager, commands


def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Drive immediate unit boundaries without creating an OS event loop."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("unit boundary unexpectedly suspended")


def target(**changes: Any) -> SimpleNamespace:
    values = dict(
        host="receiver", project="example-harness", cwd=None,
        explicit_cwd=False, worktree_id=None, ssh_shell=None,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_cold_policy_query_connects_before_exec(boundary: tuple) -> None:
    policy, manager, commands = boundary
    result = run(policy.query_ssh_policy(target(), operation="launch"))
    assert result == {"version": 1, "allowed": True}
    assert [name for name, _ in manager.calls] == ["connect", "exec"]
    host, profile, forwards, preserve = manager.calls[0][1]
    assert host == profile._host_alias == "receiver"
    assert profile._resolve_timeout == 30
    assert forwards is None and preserve is True
    assert manager.calls[1][1] == ("receiver", "fixed-owner-policy-query", 30)
    assert commands == [("example-harness", [
        "agent-policy", "check", "--project-root", "--operation", "launch", "--json",
    ], None)]


def test_policy_query_preserves_existing_connection_forwards(boundary: tuple) -> None:
    policy, manager, _commands = boundary
    manager.connected = True
    manager.forwards = ["-R 12345:127.0.0.1:23456"]
    run(policy.query_ssh_policy(target(), operation="resume"))
    assert manager.forwards == ["-R 12345:127.0.0.1:23456"]
    assert [name for name, _ in manager.calls] == ["connect", "exec"]


@pytest.mark.parametrize("failure", [
    OSError("connection unavailable"), ConnectionError("connection unavailable"),
    TimeoutError("connection unavailable"), AsyncDeadlineError("connection unavailable"),
], ids=["os-error", "connection-error", "timeout", "async-timeout"])
def test_connection_failure_is_explicit_before_policy_exec(boundary: tuple, failure: BaseException) -> None:
    policy, manager, _commands = boundary
    manager.failure = failure
    with pytest.raises(HostingRefused, match="SSH policy check") as refused:
        run(policy.query_ssh_policy(target(), operation="launch"))
    assert refused.value.__cause__ is failure
    assert [name for name, _ in manager.calls] == ["connect"]


@pytest.mark.parametrize("result", [
    SimpleNamespace(timed_out=True, exit_code=-1, stdout="", stderr=""),
    SimpleNamespace(timed_out=False, exit_code=2, stdout="", stderr="receiver refused"),
    SimpleNamespace(timed_out=False, exit_code=0, stdout="{invalid", stderr=""),
], ids=["timeout", "receiver-refused", "invalid-json"])
def test_connected_receiver_failure_never_returns_allow(boundary: tuple, result: Any) -> None:
    policy, manager, _commands = boundary
    manager.result = result
    with pytest.raises(HostingRefused):
        run(policy.query_ssh_policy(target(), operation="launch"))
    assert [name for name, _ in manager.calls] == ["connect", "exec"]


def test_unexpected_connection_bug_is_not_hidden_as_policy_refusal(boundary: tuple) -> None:
    policy, manager, _commands = boundary
    manager.failure = RuntimeError("unexpected connection-owner defect")
    with pytest.raises(RuntimeError, match="unexpected connection-owner defect"):
        run(policy.query_ssh_policy(target(), operation="launch"))
    assert [name for name, _ in manager.calls] == ["connect"]


@pytest.mark.parametrize("condition", [
    "missing-client", "invalid-config", "empty-config", "profile-timeout",
])
def test_real_profile_resolution_failure_is_a_hosting_refusal(
    boundary: tuple, monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    policy, manager, _commands = boundary
    config = sys.modules["promotion_ssh_manager.config_sources"]
    monkeypatch.setattr(
        config.shutil, "which", lambda _name: None if condition == "missing-client" else "ssh-fixture",
    )

    def resolve(arguments: list[str], **options: Any) -> SimpleNamespace:
        assert condition != "missing-client"
        assert arguments == ["ssh-fixture", "-G", "receiver"]
        assert options["timeout"] == 30
        if condition == "profile-timeout":
            raise config.subprocess.TimeoutExpired(arguments, 30)
        return SimpleNamespace(
            returncode=255 if condition == "invalid-config" else 0,
            stdout="", stderr="invalid SSH configuration" if condition == "invalid-config" else "",
        )

    monkeypatch.setattr(config.subprocess, "run", resolve)

    async def connect(host: str, source: Any, **options: Any) -> None:
        assert manager.under_deadline
        assert host == source._host_alias
        assert options == {"preserve_existing_forwards": True}
        manager.calls.append(("connect", None))
        source.get_ssh_config()

    monkeypatch.setattr(manager, "ensure_connected", connect)
    with pytest.raises(HostingRefused, match="SSH policy check"):
        run(policy.query_ssh_policy(target(), operation="launch"))
    assert [name for name, _ in manager.calls] == ["connect"]
