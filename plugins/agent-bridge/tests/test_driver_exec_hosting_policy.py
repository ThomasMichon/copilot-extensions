"""Driver-exec container members answer the portable receiving-policy check (#13366)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest
from repository_policy import AgentHostingError

from agent_bridge.provider_hosting import query_provider_policy
from agent_bridge.transport import SpawnTarget

INSTANCE = "0123456789ab"
PROVIDER = ["/payload/bin/agent-containers"]
INSTALLATION_ENV = (
    "AGENT_CONTAINERS_HOME", "AGENT_CONTAINERS_STATE_DIR",
    "AGENT_CONTAINERS_PAYLOAD_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
)
ALLOWED = {
    "version": 1, "allowed": True, "repository": "/workspaces/example",
    "expose_agent": None, "sources": [], "denied_by": [],
}


def _target(**venue: Any) -> SpawnTarget:
    # The venue agent-containers resolves for a driver-exec member: no container block.
    return SpawnTarget(
        type="command", user="vscode", cwd=".",
        spawn_command=[*PROVIDER, "exec", "--stdio", "example-1"],
        venue={
            "provider": "agent-containers", "target_id": "container:example-1",
            "instance_id": INSTANCE, "fleet": "example", "driver": "example-driver",
            "workspace_folder": "/workspaces/example", "transport": "driver-exec",
            "capabilities": {"session_host": False}, "provider_command": PROVIDER,
            **venue,
        },
    )


Call = tuple[list[str], dict[str, Any]]


def _fake_provider(
    monkeypatch: pytest.MonkeyPatch, calls: list[Call], exec_result: tuple[int, bytes, bytes],
) -> None:
    async def run(argv: list[str], **kwargs: Any) -> tuple[int, bytes, bytes]:
        calls.append((argv, kwargs))
        assert argv[:4] == [*PROVIDER, "runtime-proxy", "--fleet", "example"]
        if argv[4] == "inspect":
            assert argv[5:] == [INSTANCE]
            return 0, json.dumps([{"Id": INSTANCE, "State": {"Running": True}}]).encode(), b""
        return exec_result

    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", run)


@pytest.mark.asyncio
async def test_venue_declaration_runs_portable_check_without_guest_worktrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Call] = []
    _fake_provider(monkeypatch, calls, (0, json.dumps(ALLOWED).encode(), b""))

    result = await query_provider_policy(
        _target(portable_receiving_policy={"version": 1}), operation="launch",
    )

    assert result["allowed"] is True
    assert [argv[4] for argv, _ in calls] == ["inspect", "exec"]
    argv, kwargs = calls[1]
    assert argv[4:11] == ["exec", "-i", "-u", "vscode", INSTANCE, "bash", "-lc"]
    assert "cd -- /workspaces/example" in argv[-1] and "python3 -I -S" in argv[-1]
    assert kwargs["input_bytes"]
    assert not any("agent-worktrees" in " ".join(argv) for argv, _ in calls)


@pytest.mark.asyncio
async def test_undeclared_driver_venue_keeps_legacy_guest_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Call] = []
    _fake_provider(monkeypatch, calls, (1, b"", b""))

    with pytest.raises(AgentHostingError, match="no portable receiving handoff"):
        await query_provider_policy(_target(), operation="launch")
    argv, kwargs = calls[-1]
    assert "agent-policy check" in argv[-1] and "input_bytes" not in kwargs


@pytest.mark.asyncio
async def test_bound_hosting_reaches_real_proxy_without_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import hosting_policy
    from agent_bridge.session_host import container_transport

    root = Path(__file__).resolve().parents[2] / "agent-containers"
    # This fixture supplies Bridge's marketplace identity without installing a cell.
    monkeypatch.setattr(hosting_policy, "marketplace_id", lambda: "bridge-fixture")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, [
        root / "src", root.parents[1] / "libs" / "session-liveness-probe" / "src",
    ])))
    monkeypatch.setenv("PATH", "")  # no host Docker or guest tools
    provider_home = tmp_path / "provider"
    drivers = provider_home / "drivers.d"
    drivers.mkdir(parents=True)
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(provider_home))
    monkeypatch.setenv("AGENT_CONTAINERS_STATE_DIR", str(tmp_path / "provider-state"))
    monkeypatch.setenv("AGENT_CONTAINERS_PAYLOAD_ROOT", str(root))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONTAINERS_DRIVERS_DIR", raising=False)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    state_path = runtime / "state.json"
    slow = tmp_path / "slow"
    for driver, options in [
        ("example-driver", ["--root", str(runtime)]),
        ("slow-driver", ["--root", str(slow), "--delay", "list", "60"]),
    ]:
        (drivers / f"{driver}.json").write_text(json.dumps({
            "schema_version": 1, "name": driver,
            "command": [sys.executable, str(root / "tests" / "fake_runtime_driver.py"), *options],
        }))
    config = tmp_path / "containers.yaml"
    config.write_text(json.dumps({"fleets": {
        "example": {"driver": "example-driver", "workspace_folder": "/workspaces/example",
                    "relay_enabled": False},
        "slow": {"driver": "slow-driver", "relay_enabled": False},
    }}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(config))
    monkeypatch.setenv("FAKE_DRIVER_ROOT", str(runtime))
    monkeypatch.setenv("FAKE_DRIVER_EXEC_STDOUT", json.dumps(ALLOWED))
    state_path.write_text(json.dumps({"members": {
        f"example-{index}": {"id": f"{index:012x}", "state": "running",
                             "fleet": "example", "instance": f"example-{index}"}
        for index in range(1, 4)
    }, "calls": []}))

    provider = [sys.executable, "-m", "agent_containers"]
    run = container_transport._run_provider
    rc, out, err = await run([*provider, "namespace-resolve", "example-1"], timeout=30)
    assert rc == 0, err.decode()
    spec = json.loads(out)
    venue = {**spec["venue"], "provider_command": provider}
    binding = venue["runtime_proxy"]
    target = _target(**venue)
    assert target.venue is not None
    target.venue.pop("runtime_proxy")  # old caller/provider: genuinely bare ID
    before = len(json.loads(state_path.read_text())["calls"])
    assert (await query_provider_policy(target, operation="launch"))["allowed"]
    state = json.loads(state_path.read_text())
    assert [call["verb"] for call in state["calls"][before:]] == ["list", "inspect", "list", "exec"]

    target.venue["runtime_proxy"] = binding
    assert binding["provider_environment"] == {key: os.environ.get(key) for key in INSTALLATION_ENV}
    for key in INSTALLATION_ENV:
        monkeypatch.setenv(key, str(tmp_path / f"bridge-{key.lower()}"))
    before = len(state["calls"])
    assert (await query_provider_policy(target, operation="launch"))["allowed"]
    state = json.loads(state_path.read_text())
    calls = state["calls"][before:]
    assert [call["verb"] for call in calls] == ["inspect", "exec"]
    assert all(call["request"]["name"] == "example-1" and
               call["request"]["id"] == "000000000001" for call in calls)
    assert not slow.exists()
    assert os.environ["AGENT_CONTAINERS_HOME"] != str(provider_home)

    target.venue["runtime_proxy"] = {
        **binding, "provider_environment": {**binding["provider_environment"], "PYTHONPATH": "foreign"},
    }
    with pytest.raises(AgentHostingError, match="Invalid bound runtime proxy installation environment"):
        await query_provider_policy(target, operation="launch")
    assert json.loads(state_path.read_text())["calls"] == state["calls"]
    target.venue["runtime_proxy"] = binding

    async def replace_after_inspect(argv: list[str], **kwargs: Any) -> tuple[int, bytes, bytes]:
        result = await run(argv, **kwargs)
        if "inspect" in argv:
            state = json.loads(state_path.read_text())
            state["members"]["example-1"]["id"] = "000000000004"
            state_path.write_text(json.dumps(state))
        return result

    monkeypatch.setattr(container_transport, "_run_provider", replace_after_inspect)
    with pytest.raises(AgentHostingError):
        await query_provider_policy(target, operation="launch")
    assert not slow.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["Id", "Name", "fleet", "driver"])
async def test_bound_hosting_refuses_mismatched_response_before_exec(
    monkeypatch: pytest.MonkeyPatch, wrong: str,
) -> None:
    from agent_bridge.session_host import container_transport

    labels = {"agent-containers.fleet": "example", "agent-containers.driver": "example-driver"}
    actual = {
        "Id": INSTANCE, "Name": "/example-1", "State": {"Running": True},
        "Config": {"Labels": labels},
    }
    if wrong == "Id":
        actual["Id"] = INSTANCE + "0"  # not a Docker ID prefix match
    elif wrong == "Name":
        actual["Name"] = "/example-2"
    else:
        labels[f"agent-containers.{wrong}"] = "other"

    async def run(argv: list[str], **kwargs: Any) -> tuple[int, bytes, bytes]:
        assert argv[-2:] == ["inspect", INSTANCE]
        return 0, json.dumps([actual]).encode(), b""

    monkeypatch.setattr(container_transport, "_run_provider", run)
    with pytest.raises(AgentHostingError, match="Cannot establish"):
        await query_provider_policy(_target(runtime_proxy={
            "version": 1, "execution_runtime": "driver:example-driver/example@fixture",
            "provider_environment": dict.fromkeys(INSTALLATION_ENV),
            "provider_command": [sys.executable, "-m", "agent_containers"],
        }), operation="launch")


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False])
async def test_spawn_hands_exec_wrapper_its_bound_receiving_context(
    monkeypatch: pytest.MonkeyPatch, declared: bool,
) -> None:
    from agent_bridge import hosting_policy, transport

    seen: dict[str, str] = {}

    async def admitted(target: SpawnTarget, **kwargs: Any) -> None:
        return None

    async def spawn(*argv: str, env: dict[str, str], **kwargs: Any) -> object:
        seen.update(env)
        raise OSError("stop before a real child")

    monkeypatch.setattr(hosting_policy, "require_target_hosting", admitted)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("agent_bridge.portable_policy.selected_context", lambda: "bound-context")
    monkeypatch.setenv(transport._CONTAINERS_POLICY_CONTEXT_ENV, "inherited")
    target = _target(**({"portable_receiving_policy": {"version": 1}} if declared else {}))

    with pytest.raises(OSError, match="stop before"):
        await transport.spawn_raw(target)

    assert seen.get(transport._CONTAINERS_POLICY_CONTEXT_ENV) == ("bound-context" if declared else None)
