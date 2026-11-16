"""Bounded predecessor retirement must never become a session-tree kill."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol
import urllib.request

import pytest
from fastapi import FastAPI

from zdd import diagnostics, process_exit, routing
from zdd.routing import Endpoint


class FixtureClient(Protocol):
    def __init__(self, endpoint: Endpoint) -> None: ...
    def health(self) -> dict[str, Any]: ...
    def shutdown(self) -> dict[str, Any]: ...


Generations = tuple[
    Path, Endpoint, Endpoint, type[FixtureClient], list[str | tuple[str, float]],
]


@pytest.fixture
def generations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generations:
    from agent_bridge import __main__ as core, service_process_cli
    from agent_procutil import _daemon_instance_identity, process_retirement

    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: str(pid))
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda *_: False)
    old = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41001, pid=101, version="old",
        process_start_time="101",
    )
    new, _ = routing.publish_active_with_previous(
        tmp_path, bind="127.0.0.1", port=41002, pid=202, version="new",
        process_start_time="202", demote_existing=True,
    )
    calls: list[str | tuple[str, float]] = []

    class Pin:
        def __init__(self, pid: int, birth: str) -> None:
            assert pid in (101, 202) and birth == f"native:{pid}"

        def __enter__(self) -> Pin:
            calls.append("pin")
            return self

        def __exit__(self, *_: object) -> None:
            calls.append("close")

        def exited(self, timeout: float = 0) -> bool:
            calls.append(("wait", timeout))
            return False

        def terminate(self, timeout: float = 5) -> None:
            calls.append(("terminate-central", timeout))

    monkeypatch.setattr(process_retirement, "PinnedProcess", Pin)
    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", lambda pid: f"native:{pid}")
    monkeypatch.setattr(service_process_cli, "_pid_is_agent_bridge", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        core, "_force_kill_agent_bridge_tree",
        lambda *_: pytest.fail("session/transport trees are not retirement targets"),
    )

    class Client:
        def __init__(self, endpoint: Endpoint) -> None:
            self.endpoint = endpoint

        def health(self) -> dict[str, Any]:
            assert self.endpoint == new
            return {
                "status": "ok", "ready": True, "service": "agent-bridge",
                "slot": {"pid": new.pid, "role": "active", "active": new.to_dict()},
            }

        def shutdown(self) -> dict[str, Any]:
            assert self.endpoint == old
            calls.append("shutdown")
            raise OSError("old control endpoint is unresponsive")

    return tmp_path, old, new, Client, calls


def test_unresponsive_predecessor_is_forced_through_native_central_pin(generations: Generations) -> None:
    from agent_bridge.central_retirement import retire_predecessor

    root, old, new, client, calls = generations
    outcome = retire_predecessor(root, old, new, make_client=client)
    assert outcome == {"exit_confirmed": True, "forced": True}
    assert calls == [
        "pin", ("wait", 0), "shutdown", ("wait", 5.0),
        ("terminate-central", 5.0), "close",
    ]


@pytest.mark.parametrize("change", ["route", "birth", "role", "unhealthy"])
def test_changed_authority_never_terminates_predecessor(
    generations: Generations, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_bridge.central_retirement import retire_predecessor

    root, old, new, client, calls = generations
    if change == "route":
        new = replace(new, generation=new.generation + 1)
    elif change == "birth":
        monkeypatch.setattr(diagnostics, "process_start_time", lambda _pid: "reused")
    elif change == "role":
        from agent_bridge import service_process_cli
        monkeypatch.setattr(service_process_cli, "_pid_is_agent_bridge", lambda *_a, **_kw: False)
    else:
        monkeypatch.setattr(client, "health", lambda _: {"status": "ok", "ready": False})
    outcome = retire_predecessor(root, old, new, make_client=client)
    assert outcome["exit_confirmed"] is False
    assert not any(isinstance(c, tuple) and c[0] == "terminate-central" for c in calls)


def test_reselection_during_shutdown_preserves_predecessor(
    generations: Generations, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.central_retirement import retire_predecessor

    root, old, new, client, calls = generations

    def restore(_: FixtureClient) -> dict[str, bool]:
        assert new.pid is not None
        assert routing.restore_previous_if_owner(root, pid=new.pid, generation=new.generation)
        calls.append("restored")
        return {"shutting_down": True}

    monkeypatch.setattr(client, "shutdown", restore)
    outcome = retire_predecessor(root, old, new, make_client=client)
    assert outcome["exit_confirmed"] is False
    assert "restored" in calls and calls[-1] == "close"
    assert ("terminate-central", 5.0) not in calls


@pytest.mark.parametrize("settles", [True, False])
def test_native_exit_result_is_not_inferred_from_shutdown_reply(
    generations: Generations, monkeypatch: pytest.MonkeyPatch, settles: bool,
) -> None:
    from agent_bridge.central_retirement import retire_predecessor
    from agent_procutil import process_retirement

    root, old, new, client, calls = generations
    pin = process_retirement.PinnedProcess
    monkeypatch.setattr(pin, "exited", lambda _self, timeout=0: settles and timeout > 0)

    def failed_exit(_self: Any, **_: Any) -> None:
        raise process_retirement.RetirementRefused("native exit unconfirmed")

    monkeypatch.setattr(pin, "terminate", failed_exit)
    outcome = retire_predecessor(root, old, new, make_client=client)
    assert outcome["exit_confirmed"] is settles
    if settles:
        assert outcome["forced"] is False
    else:
        assert outcome["reason"] == "RetirementRefused"
    table = routing.read_table(root)
    assert table is not None and table["active"] == new.to_dict()
    assert calls[-1] == "close"


def test_ordinary_deploy_bounds_retirement_before_relay_and_host_adoption(
    generations: Generations, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_bridge import __main__ as core, config, venue_cli
    from agent_bridge import client as client_module

    root, _, _, _, calls = generations
    monkeypatch.setattr(config, "config_dir", lambda: root)
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(bind="127.0.0.1"))
    monkeypatch.setattr(config, "load_or_create_auth_token", lambda: "fixture-token")
    monkeypatch.setattr(core, "_active_endpoint_is_forward", lambda: False)
    monkeypatch.setattr(core, "_reap_abandoned_passive", lambda *_: {})
    monkeypatch.setattr(core, "_reconcile_service_marker", lambda *_: None)
    monkeypatch.setattr(
        core, "_ensure_retired_daemon_exited",
        lambda *_: pytest.fail("ordinary deploy cannot use legacy tree retirement"),
    )
    monkeypatch.setattr(
        core, "_launch_bridge_daemon",
        lambda *_a, **_kw: SimpleNamespace(pid=303, poll=lambda: None),
    )

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def read(self) -> bytes:
            return b'{"status": "ok", "ready": true}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: Response())

    class Client:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def health(self) -> dict[str, Any]:
            table = routing.read_table(root)
            assert table is not None
            active = table["active"]
            return {
                "status": "ok", "ready": True, "service": "agent-bridge",
                "slot": {"pid": 303, "role": "active", "active": active},
            }

        def shutdown(self) -> dict[str, Any]:
            calls.append("shutdown")
            raise OSError("nonsettling predecessor")

        def drain(self, **_: Any) -> None:
            pytest.fail("an unreliable predecessor cannot gate incoming readiness")

        def adopt_relay(self) -> dict[str, bool]:
            assert ("terminate-central", 5.0) in calls
            calls.append("relay")
            return {"adopted": True}

        def _request(self, method: str, path: str) -> dict[str, int]:
            assert "relay" in calls
            assert (method, path) == ("POST", "/api/v1/session-hosts/reattach")
            calls.append("reattach")
            return {"reattached": 1}

    monkeypatch.setattr(client_module, "BridgeClient", Client)
    args = core.build_parser().parse_args(["--json", "deploy"])
    with pytest.raises(SystemExit) as result:
        venue_cli._cmd_deploy(args)
    assert result.value.code == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["ok"]
    assert outcome["predecessor_cleanup"] == {"exit_confirmed": True, "forced": True}
    assert calls[-2:] == ["relay", "reattach"]
    table = routing.read_table(root)
    assert table is not None and table["active"]["pid"] == 303


@pytest.mark.asyncio
async def test_selected_start_does_not_wait_forever_for_predecessor(
    generations: Generations, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import app as app_module, central_retirement

    root, old, new, _, calls = generations
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))

    class Reference:
        def wait(self, timeout: float) -> bool:
            calls.append(("self-retire-wait", timeout))
            return False

        def close(self) -> None:
            calls.append("close-reference")

    monkeypatch.setattr(process_exit, "open_process", lambda *_a, **_kw: Reference())

    def retire(*args: Any, **kwargs: Any) -> dict[str, bool]:
        assert args == (root, old, new)
        calls.append("bounded-retirement")
        return {"exit_confirmed": True, "forced": True}

    async def recover(_app: FastAPI, owner: Endpoint, **kwargs: Any) -> int:
        assert owner == new and kwargs["predecessor_exited"] is True
        calls.append("recover")
        return 0

    monkeypatch.setattr(central_retirement, "retire_predecessor", retire)
    monkeypatch.setattr(app_module, "_recover_owned_cold", recover)
    app = FastAPI()
    app.state.independent_runtime_start = True
    app.state.auth_token = "fixture-token"
    await asyncio.wait_for(app_module._retire_previous_daemon(app, old, new), 2)
    assert calls == [
        ("self-retire-wait", 1.0), "bounded-retirement", "recover", "close-reference",
    ]


@pytest.mark.asyncio
async def test_selected_start_adopts_relay_before_slow_host_reattach(
    generations: Generations, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import app as app_module, lifecycle_hooks, routing_state
    from agent_bridge import runtime_version, source_start

    root, old, new, _, calls = generations
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))
    monkeypatch.setattr(source_start, "_check_selection", lambda *_: None)
    monkeypatch.setattr(
        routing_state, "publish_daemon_route_unless_forwarded", lambda *_a, **_kw: (new, old),
    )
    monkeypatch.setattr(lifecycle_hooks, "record_start", lambda *_: True)
    monkeypatch.setattr(runtime_version, "write_running_version", lambda **_: None)

    async def retired(app: FastAPI, *_a: Any, **_kw: Any) -> None:
        app.state.cold_recovery_ready = True

    async def relay() -> None:
        calls.append("relay")

    async def reattach(**_: Any) -> int:
        assert calls == ["relay"]
        calls.append("reattach")
        raise TimeoutError("slow remote host must not hold relay adoption")

    monkeypatch.setattr(app_module, "_retire_previous_daemon", retired)
    state = SimpleNamespace(
        config=SimpleNamespace(bind="127.0.0.1", enable_credential_relay=True),
        ready=True, shutting_down=False, bound_port=new.port,
        uvicorn_server=SimpleNamespace(started=True, should_exit=False),
        session_manager=SimpleNamespace(reattach_session_hosts=reattach),
        adopt_relay=relay,
    )
    published = asyncio.Event()
    app = FastAPI()
    for key, value in vars(state).items():
        setattr(app.state, key, value)
    await source_start.complete_source_start(app, published, None)
    assert published.is_set() and calls == ["relay", "reattach"]
    assert not state.uvicorn_server.should_exit
