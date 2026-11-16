"""Bounded predecessor retirement must never become a session-tree kill."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import os
import sys
import textwrap
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
            pytest.fail("unfenced predecessor HTTP shutdown is not permitted")

    return tmp_path, old, new, Client, calls


def test_unresponsive_predecessor_is_forced_through_native_central_pin(generations: Generations) -> None:
    from agent_bridge.central_retirement import retire_predecessor

    root, old, new, client, calls = generations
    outcome = retire_predecessor(root, old, new, make_client=client)
    assert outcome == {"exit_confirmed": True, "forced": True}
    assert calls == [
        "pin", ("wait", 0), ("wait", 5.0),
        ("terminate-central", 5.0), "close",
    ]


@pytest.mark.parametrize("old_exited", [False, True])
def test_missing_previous_row_only_allows_already_confirmed_native_exit(
    generations: Generations, monkeypatch: pytest.MonkeyPatch, old_exited: bool,
) -> None:
    from agent_bridge.central_retirement import retire_predecessor

    root, old, new, client, calls = generations
    monkeypatch.setattr(
        routing, "read_table", lambda *_a, **_kw: {"active": new.to_dict(), "previous": None},
    )
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda *_: old_exited)
    result = retire_predecessor(root, old, new, make_client=client)
    assert result["exit_confirmed"] is old_exited
    assert ("terminate-central", 5.0) not in calls


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


def test_reselection_during_native_exit_observation_preserves_predecessor(
    generations: Generations, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.central_retirement import retire_predecessor
    from agent_procutil import process_retirement

    root, old, new, client, calls = generations

    def restore(_: Any, timeout: float = 0) -> bool:
        if timeout:
            assert new.pid is not None
            assert routing.restore_previous_if_owner(root, pid=new.pid, generation=new.generation)
            calls.append("restored")
        return False

    monkeypatch.setattr(process_retirement.PinnedProcess, "exited", restore)
    outcome = retire_predecessor(root, old, new, make_client=client)
    assert outcome["exit_confirmed"] is False
    assert "restored" in calls and calls[-1] == "close"
    assert ("terminate-central", 5.0) not in calls


def test_reselection_after_pin_cannot_send_shutdown_to_active_owner(
    generations: Generations, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.central_retirement import retire_predecessor
    from agent_procutil import process_retirement

    root, old, new, client, calls = generations
    assert new.pid is not None
    restored = False

    def wait(_self: Any, timeout: float = 0) -> bool:
        nonlocal restored
        if not restored:
            assert new.pid is not None
            assert routing.restore_previous_if_owner(root, pid=new.pid, generation=new.generation)
            restored = True
        return False

    def shutdown(_self: FixtureClient) -> dict[str, bool]:
        calls.append("wrong-owner-shutdown")
        return {"shutting_down": True}

    monkeypatch.setattr(process_retirement.PinnedProcess, "exited", wait)
    monkeypatch.setattr(client, "shutdown", shutdown)
    result = retire_predecessor(root, old, new, make_client=client)
    assert result["exit_confirmed"] is False
    assert "wrong-owner-shutdown" not in calls
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


@pytest.mark.parametrize("failure", ["unready", "draining", "unreachable"])
def test_successor_health_lost_during_exit_wait_preserves_predecessor(
    generations: Generations, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_bridge.central_retirement import retire_predecessor
    from agent_procutil import process_retirement

    root, old, new, client, calls = generations
    healthy = True
    original_health = client.health

    def wait(_self: Any, timeout: float = 0) -> bool:
        nonlocal healthy
        if timeout:
            healthy = False
        return False

    def health(self: FixtureClient) -> dict[str, Any]:
        body = original_health(self)
        if not healthy:
            if failure == "unreachable":
                raise OSError("successor stopped responding during predecessor wait")
            body["ready" if failure == "unready" else "draining"] = failure != "unready"
        return body

    monkeypatch.setattr(process_retirement.PinnedProcess, "exited", wait)
    monkeypatch.setattr(client, "health", health)
    result = retire_predecessor(root, old, new, make_client=client)
    assert result["exit_confirmed"] is False
    assert ("terminate-central", 5.0) not in calls


@pytest.mark.parametrize("case", [
    "normal",
    "stale-active",
    "slow-reattach",
    pytest.param(
        "interrupted",
        marks=[pytest.mark.portfolio_tier("T2"), pytest.mark.effect("filesystem"), pytest.mark.effect("process")],
    ),
])
def test_ordinary_deploy_bounds_retirement_before_relay_and_host_adoption(
    generations: Generations, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    from agent_bridge import __main__ as core, config, venue_cli
    from agent_bridge import client as client_module

    root, _, _, _, calls = generations
    monkeypatch.setattr(routing, "_listening", lambda _host, port: port != 41003)
    monkeypatch.setattr(routing, "_pid_alive", lambda pid: pid in (101, 202, 303))
    if case == "stale-active":
        routing.publish_active_with_previous(
            root, bind="127.0.0.1", port=41003, pid=404, version="dead",
            process_start_time="404", demote_existing=True,
        )
        monkeypatch.setattr(process_exit, "observe_original_exit", lambda pid, _birth: pid == 404)
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
            pytest.fail("ordinary deploy cannot issue an unfenced shutdown request")

        def drain(self, **_: Any) -> None:
            pytest.fail("an unreliable predecessor cannot gate incoming readiness")

        def undrain(self) -> None:
            pytest.fail("an interrupted no-drain deploy cannot release another drain gate")

        def adopt_relay(self) -> dict[str, bool]:
            assert ("terminate-central", 5.0) in calls
            calls.append("relay")
            return {"adopted": True}

        def _request(
            self, method: str, path: str, *, request_timeout: float | None = None,
            allow_retry: bool = True,
        ) -> dict[str, int]:
            assert "relay" in calls
            assert (method, path) == ("POST", "/api/v1/session-hosts/reattach")
            assert request_timeout is not None and 0 < request_timeout <= 5
            assert allow_retry is False
            calls.append("reattach")
            if case == "slow-reattach":
                raise TimeoutError("Host reattachment may still complete after client timeout")
            return {"reattached": 1}

    monkeypatch.setattr(client_module, "BridgeClient", Client)
    args = core.build_parser().parse_args(["--json", "deploy"])
    if case == "interrupted":
        from agent_procutil import spawn_sync_in_kill_on_close_job
        from zdd import breadcrumb

        script = textwrap.dedent("""
            import os, sys
            from pathlib import Path
            from types import SimpleNamespace
            from agent_bridge import __main__ as core, config, venue_cli
            from zdd import diagnostics, routing
            root = Path(sys.argv[1])
            original_start = diagnostics.process_start_time
            diagnostics.process_start_time = lambda pid: (
                original_start(pid) if pid == os.getpid() else str(pid)
            )
            config.config_dir = lambda: root
            config.load_config = lambda: SimpleNamespace(bind="127.0.0.1")
            config.load_or_create_auth_token = lambda: "fixture-token"
            routing._listening = lambda *_: True
            routing._pid_alive = lambda *_: True
            core._active_endpoint_is_forward = lambda: False
            core._reap_abandoned_passive = lambda *_: {}
            core._launch_bridge_daemon = lambda *_a, **_kw: os._exit(73)
            venue_cli._cmd_deploy(core.build_parser().parse_args(["--json", "deploy"]))
        """)
        process, job = spawn_sync_in_kill_on_close_job(
            [sys.executable, "-c", script, str(root)],
            require_job=os.name == "nt", allow_breakaway=False,
        )
        try:
            assert process.wait(timeout=20) == 73
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if job is not None:
                job.terminate_and_wait(5)
        record = breadcrumb.read_breadcrumb(root)
        assert record is not None and record["state"] == "started"
        assert record.get("requires_writer_reconciliation") is not True
        with pytest.raises(SystemExit) as recovered:
            venue_cli._cmd_deploy(core.build_parser().parse_args(["--json", "deploy", "--recover"]))
        assert recovered.value.code == 0
        assert not json.loads(capsys.readouterr().out).get("refused")
    with pytest.raises(SystemExit) as result:
        venue_cli._cmd_deploy(args)
    assert result.value.code == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["ok"]
    assert outcome["old_port"] == 41002
    assert outcome["predecessor_cleanup"] == {"exit_confirmed": True, "forced": True}
    assert calls[-2:] == ["relay", "reattach"]
    assert calls.count("reattach") == 1
    if case == "slow-reattach":
        assert any("post-cutover reattach failed (non-fatal)" in step for step in outcome["steps"])
    table = routing.read_table(root)
    assert table is not None and table["active"]["pid"] == 303


@pytest.mark.parametrize("independent", [False, True])
def test_exact_no_drain_cutover_preserves_writer_recovery_by_default(
    generations: Generations, independent: bool,
) -> None:
    from zdd import breadcrumb
    from zdd.cutover import CutoverOrchestrator

    root, _, predecessor, _, _ = generations

    def interrupted(_port: int) -> Any:
        raise RuntimeError("interrupted before passive creation")

    orchestrator = CutoverOrchestrator(
        root, bind="127.0.0.1", version="new", spawn_passive=interrupted,
        health_check=lambda *_: True, make_client=lambda _: pytest.fail("no control before spawn"),
        pick_free_port=lambda: 41008, expected_old=predecessor, refuse_old=lambda _: None,
        retire_failed_predecessor=lambda _: {}, drain_predecessor=False,
        writer_reconciliation=False if independent else None,
    )
    with pytest.raises(RuntimeError, match="interrupted before passive"):
        orchestrator.run()
    record = breadcrumb.read_breadcrumb(root)
    assert record is not None
    refusal = breadcrumb.recovery_refusal(root, record)
    assert (refusal is None) is independent


@pytest.mark.parametrize("invalid", [0, "false"])
def test_invalid_no_drain_marker_cannot_change_recovery_admission(
    generations: Generations, invalid: Any,
) -> None:
    from zdd import breadcrumb

    root, _, predecessor, _, _ = generations
    with pytest.raises(ValueError, match="drain marker"):
        breadcrumb.write_breadcrumb(
            root, state="started", old=predecessor.to_dict(),
            predecessor_drain_requested=invalid,
        )
    assert not breadcrumb.breadcrumb_path(root).exists()
    record = breadcrumb.write_breadcrumb(root, state="started", predecessor_drain_requested=False)
    record["predecessor_drain_requested"] = invalid
    assert breadcrumb.recovery_refusal(root, record) is not None


def test_incoming_readiness_failure_is_not_reported_as_successful_cleanup(
    generations: Generations,
) -> None:
    from zdd.cutover import CutoverOrchestrator

    root, _, predecessor, _, _ = generations
    healthy = True

    class Client:
        def health(self) -> dict[str, Any]:
            return {"status": "ok"}

        def drain(self, *, timeout: float, poll: float, force: bool) -> dict[str, Any]:
            pytest.fail("no drain requested")

        def undrain(self) -> dict[str, Any]:
            pytest.fail("committed attempt cannot undrain")

        def shutdown(self) -> dict[str, Any]:
            pytest.fail("no unfenced shutdown")

        def adopt_relay(self) -> dict[str, Any]:
            pytest.fail("unready successor cannot adopt resources")

    def cleanup(_endpoint: Endpoint) -> dict[str, bool]:
        nonlocal healthy
        healthy = False
        return {"exit_confirmed": False}

    orchestrator = CutoverOrchestrator(
        root, bind="127.0.0.1", version="new",
        spawn_passive=lambda _: SimpleNamespace(pid=303, poll=lambda: None),
        health_check=lambda *_: healthy, make_client=lambda _: Client(),
        pick_free_port=lambda: 41008, expected_old=predecessor, refuse_old=lambda _: None,
        retire_failed_predecessor=cleanup, drain_predecessor=False, writer_reconciliation=False,
    )
    result = orchestrator.run()
    assert not result.ok and result.committed
    assert result.error and "lost readiness" in result.error
    assert result.predecessor_cleanup == {"exit_confirmed": False}


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
@pytest.mark.parametrize("reselected", [False, True])
async def test_selected_start_adopts_relay_before_slow_host_reattach(
    generations: Generations, monkeypatch: pytest.MonkeyPatch, reselected: bool,
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
        if reselected:
            assert new.pid is not None
            assert routing.restore_previous_if_owner(root, pid=new.pid, generation=new.generation)

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
    assert published.is_set()
    assert calls == ([] if reselected else ["relay", "reattach"])
    assert not state.uvicorn_server.should_exit
