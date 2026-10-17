"""A selected immutable slot is not evidence of the version actually serving."""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, Mock

import pytest
from zdd import diagnostics, process_exit

from agent_bridge import serving_reconcile as reconcile


@pytest.fixture
def serving(tmp_path, monkeypatch):
    row = {
        "bind": "127.0.0.1", "port": 9011, "pid": 1234, "generation": 7,
        "version": "1.2.2", "process_start_time": "123456",
    }
    (tmp_path / "active.json").write_text(json.dumps({"active": row}))
    (tmp_path / "auth.yaml").write_text("token: existing-fixture-credential\n")
    health = {
        "status": "ok", "service": "agent-bridge", "ready": True,
        "version": row["version"], "draining": False,
        "slot": {"pid": row["pid"], "role": "active", "active": row.copy()},
    }
    core = Mock()
    core._service_health_on_port.return_value = health
    core._pid_is_agent_bridge.return_value = True
    monkeypatch.setattr(reconcile, "_core", lambda: core)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123456")
    reference = MagicMock(pid=row["pid"], start_time="123456")
    reference.__enter__.return_value = reference
    reference.close.return_value = None
    reference.__exit__.side_effect = lambda *_: reference.close()
    reference.wait.return_value = False
    monkeypatch.setattr(process_exit, "open_process", Mock(return_value=reference))
    return tmp_path, row, health, reference, core


def test_serving_reconcile_captures_exact_live_predecessor(serving):
    root, row, _, reference, _ = serving
    with reconcile.observe_serving(root) as observed:
        assert observed.route == row
        assert observed.version == "1.2.2"
        assert observed.refuse(row.copy()) is None
    reference.close.assert_called_once()
    process_exit.open_process.assert_called_once_with(1234, "123456")


@pytest.mark.parametrize("change", [
    {"pid": 1235}, {"generation": 8}, {"port": 9012},
    {"process_start_time": "654321"}, {"forwarded": True},
])
def test_serving_reconcile_refuses_changed_predecessor(serving, change):
    root, row, _, _, _ = serving
    with reconcile.observe_serving(root) as observed:
        assert observed.refuse({**row, **change})


@pytest.mark.parametrize("case", [
    "unhealthy", "not-ready", "unknown-version", "wrong-service", "wrong-pid",
    "passive", "wrong-slot", "draining", "unknown-start", "changed-start",
    "foreign-process", "exited",
])
def test_serving_reconcile_uncertainty_is_not_success(serving, monkeypatch, case):
    root, _, health, reference, core = serving
    if case == "unhealthy":
        core._service_health_on_port.return_value = None
    elif case == "not-ready":
        health["ready"] = False
    elif case == "unknown-version":
        health.pop("version")
    elif case == "wrong-service":
        health["service"] = "other"
    elif case == "wrong-pid":
        health["slot"]["pid"] = 9999
    elif case == "passive":
        health["slot"]["role"] = "passive"
    elif case == "wrong-slot":
        health["slot"]["active"]["generation"] = 8
    elif case == "draining":
        health["draining"] = True
    elif case in ("unknown-start", "changed-start"):
        monkeypatch.setattr(
            diagnostics, "process_start_time",
            lambda pid: None if case == "unknown-start" else "654321",
        )
    elif case == "foreign-process":
        core._pid_is_agent_bridge.return_value = False
    elif case == "exited":
        reference.wait.return_value = True
    with pytest.raises(reconcile.ServingReconcileError):
        with reconcile.observe_serving(root):
            pytest.fail("uncertain serving state was admitted")


@pytest.mark.parametrize("case", ["missing", "forward", "legacy-forward"])
def test_serving_reconcile_does_not_activate_nonlocal_or_inactive(serving, case):
    root, _, _, _, core = serving
    if case == "missing":
        (root / "active.json").unlink()
    else:
        row = {"port": 9011}
        if case == "forward":
            row["forwarded"] = True
        (root / "active.json").write_text(json.dumps({"active": row}))
    with reconcile.observe_serving(root) as observed:
        assert observed is None
    core._service_health_on_port.assert_not_called()
    process_exit.open_process.assert_not_called()


@pytest.mark.parametrize("body", [
    "{broken", "[]", '{"active":{"pid":1234}}', '{"previous":{"pid":1234}}',
])
def test_serving_reconcile_malformed_or_unowned_route_refuses(serving, body):
    root, _, _, _, _ = serving
    (root / "active.json").write_text(body)
    with pytest.raises(reconcile.ServingReconcileError):
        with reconcile.observe_serving(root):
            pytest.fail("unowned route was admitted")


def test_serving_reconcile_missing_route_with_service_marker_refuses(serving):
    root, _, _, _, _ = serving
    (root / "active.json").unlink()
    (root / "agent-bridge.pid").write_text("1234")
    with pytest.raises(reconcile.ServingReconcileError):
        with reconcile.observe_serving(root):
            pytest.fail("marker without route was accepted")


def test_serving_reconcile_never_substitutes_a_fresh_native_token(serving):
    root, row, health, _, _ = serving
    row.pop("process_start_time")
    health["slot"]["active"] = row.copy()
    (root / "active.json").write_text(json.dumps({"active": row}))
    with pytest.raises(reconcile.ServingReconcileError):
        with reconcile.observe_serving(root):
            pytest.fail("legacy route acquired identity from a later observation")
    process_exit.open_process.assert_not_called()


def test_serving_reconcile_rechecks_identity_at_guard(serving, monkeypatch):
    root, row, _, _, _ = serving
    with reconcile.observe_serving(root) as observed:
        monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "654321")
        assert observed.refuse(row)


@pytest.mark.parametrize(("running", "target", "expected"), [
    ("1.2.3-dev4", "1.2.3.dev4", "current"),
    ("1.2.3.dev3", "1.2.3-dev4", "older"),
    ("1.2.3.dev5", "1.2.3-dev4", "newer"),
    ("1.2.3", "1.2.3-dev4", "newer"),
])
def test_serving_reconcile_uses_normalized_version_order(running, target, expected):
    assert reconcile.compare_serving_version(running, target) == expected


def test_serving_reconcile_invalid_version_refuses():
    with pytest.raises(reconcile.ServingReconcileError):
        reconcile.compare_serving_version("unknown", "1.2.3")


@pytest.mark.parametrize("state", [
    "exited", "absent-posix", "absent-windows", "live", "reused", "unknown", "route-changed",
])
def test_serving_reconcile_previous_only_requires_verified_exit(serving, monkeypatch, state):
    root, row, _, reference, core = serving
    table = {"previous": row}
    path = root / "active.json"
    path.write_text(json.dumps(table))

    def open_process(pid, start, *, allow_exited):
        import errno

        assert (pid, start) == (row["pid"], row["process_start_time"])
        assert allow_exited is True
        if state == "absent-posix":
            raise ProcessLookupError(errno.ESRCH, "native process is absent")
        if state == "absent-windows":
            class NativeAbsent(OSError):
                winerror = 87
            raise NativeAbsent("native process is absent")
        if state == "reused":
            raise ProcessLookupError("native process identity changed")
        if state == "unknown":
            raise PermissionError("native identity unavailable")
        if state == "route-changed":
            path.write_text(json.dumps({"active": row}))
        reference.wait.return_value = state != "live"
        return reference

    capture = Mock(side_effect=open_process)
    monkeypatch.setattr(process_exit, "open_process", capture)
    if state in ("exited", "absent-posix", "absent-windows"):
        before = path.read_bytes()
        with reconcile.observe_serving(root) as observed:
            assert observed is None
        assert path.read_bytes() == before
    else:
        with pytest.raises(reconcile.ServingReconcileError):
            with reconcile.observe_serving(root):
                pytest.fail("uncertain previous owner was admitted as inactive")
    core._service_health_on_port.assert_not_called()
    capture.assert_called_once_with(row["pid"], row["process_start_time"], allow_exited=True)


@pytest.mark.parametrize("bind", ["127.0.0.2", "localhost"])
def test_serving_reconcile_accepts_supported_local_bind(serving, bind):
    root, row, health, _, core = serving
    row["bind"] = bind
    health["slot"]["active"] = row.copy()
    (root / "active.json").write_text(json.dumps({"active": row}))
    with reconcile.observe_serving(root) as observed:
        assert observed.route["bind"] == bind
        observed.verify_control()
    core._service_health_on_port.assert_called_with(row["port"], host=bind)


@pytest.mark.parametrize("body", [
    None, "token: [", "[]", "{}", "token: 123", "token: true", 'token: "   "',
])
def test_serving_reconcile_never_creates_or_repairs_credentials(tmp_path, body):
    auth = tmp_path / "auth.yaml"
    if body is not None:
        auth.write_text(body)
    before = auth.read_bytes() if auth.exists() else None
    with pytest.raises(reconcile.ServingReconcileError):
        reconcile.existing_auth_token(tmp_path)
    assert (auth.read_bytes() if auth.exists() else None) == before


def test_serving_reconcile_reads_existing_credential_without_writing(tmp_path):
    auth = tmp_path / "auth.yaml"
    auth.write_text("token: existing-fixture-credential\n")
    before = auth.read_bytes()
    assert reconcile.existing_auth_token(tmp_path) == "existing-fixture-credential"
    assert auth.read_bytes() == before


@pytest.mark.parametrize(("running", "expected", "deploys"), [
    ("1.2.2", 0, True), ("1.2.3", 0, False), ("1.2.4", 1, False),
])
def test_serving_reconcile_command_selects_only_older_daemon(
    serving, monkeypatch, running, expected, deploys,
):
    from agent_bridge import config, venue_cli
    import agent_bridge

    root, row, health, reference, _ = serving
    row["version"] = health["version"] = running
    health["slot"]["active"] = row.copy()
    (root / "active.json").write_text(json.dumps({"active": row}))
    monkeypatch.setattr(config, "config_dir", lambda: root)
    monkeypatch.setattr(agent_bridge, "__version__", "1.2.3")
    deploy = Mock(side_effect=SystemExit(0))
    monkeypatch.setattr(venue_cli, "_cmd_deploy", deploy)
    args = Namespace(force=False, recover=False, json=True)
    with pytest.raises(SystemExit) as exit:
        reconcile.reconcile_serving(args)
    assert exit.value.code == expected
    assert deploy.called is deploys
    if deploys:
        assert deploy.call_args.kwargs["serving"].route == row
        assert args.force is False
    reference.close.assert_called_once()


@pytest.mark.parametrize("flag", ["force", "recover"])
def test_serving_reconcile_rejects_force_and_recovery(serving, monkeypatch, flag):
    from agent_bridge import config

    root, _, _, _, core = serving
    monkeypatch.setattr(config, "config_dir", lambda: root)
    args = Namespace(force=False, recover=False, json=True)
    setattr(args, flag, True)
    with pytest.raises(SystemExit) as exit:
        reconcile.reconcile_serving(args)
    assert exit.value.code == 1
    core._service_health_on_port.assert_not_called()
    process_exit.open_process.assert_not_called()


@pytest.mark.parametrize(("running", "code"), [("1.2.2", 10), ("1.2.3", 0)])
def test_serving_probe_never_starts_a_transition(serving, monkeypatch, running, code):
    import agent_bridge
    from agent_bridge import config, venue_cli

    root, row, health, _, _ = serving
    row["version"] = health["version"] = running
    health["slot"]["active"] = row.copy()
    route = root / "active.json"
    route.write_text(json.dumps({"active": row}))
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    monkeypatch.setattr(config, "config_dir", lambda: root)
    monkeypatch.setattr(agent_bridge, "__version__", "1.2.3")
    deploy = Mock(side_effect=AssertionError("read-only probe attempted deployment"))
    monkeypatch.setattr(venue_cli, "_cmd_deploy", deploy)
    with pytest.raises(SystemExit) as result:
        reconcile.reconcile_serving(Namespace(
            force=False, recover=False, json=True, probe_serving=True,
        ))
    assert result.value.code == code
    deploy.assert_not_called()
    assert {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before


def test_serving_reconcile_rechecks_port_owner_before_control(serving, monkeypatch):
    from agent_bridge.client import BridgeClient

    root, row, health, _, _ = serving
    request = Mock(return_value={"draining": False})
    monkeypatch.setattr(BridgeClient, "_request", request)
    with reconcile.observe_serving(root) as observed:
        client = reconcile.guarded_client(
            observed, f"http://127.0.0.1:{row['port']}", "test-credential", 10,
        )
        assert client.undrain() == {"draining": False}
        request.assert_called_once()
        request.reset_mock()
        health["slot"]["pid"] = 9999
        with pytest.raises(reconcile.ServingReconcileError):
            client.undrain()
        request.assert_not_called()


def test_serving_reconcile_refuses_before_spawn_if_route_changed(serving, monkeypatch):
    from zdd.cutover import CutoverOrchestrator

    root, row, _, _, _ = serving
    spawn = Mock()
    with reconcile.observe_serving(root) as observed:
        (root / "active.json").write_text(json.dumps({
            "active": {**row, "generation": 8},
        }))
        result = CutoverOrchestrator(
            root, bind="127.0.0.1", version="1.2.3",
            spawn_passive=spawn, health_check=Mock(), make_client=Mock(),
            pick_free_port=Mock(return_value=9012), refuse_old=observed.refuse,
        ).run()
    assert result.ok is False
    assert "changed" in result.error
    spawn.assert_not_called()


def test_serving_reconcile_never_cold_starts_after_endpoint_probe_fails(serving, monkeypatch):
    from zdd.cutover import CutoverOrchestrator
    from zdd import routing

    root, row, _, _, _ = serving
    spawn = Mock()
    monkeypatch.setattr(routing, "read_active_endpoint", lambda *_: None)
    monkeypatch.setattr(routing, "reap_stale_active", Mock())
    with reconcile.observe_serving(root) as observed:
        result = CutoverOrchestrator(
            root, bind="127.0.0.1", version="1.2.3",
            spawn_passive=spawn, health_check=Mock(), make_client=Mock(),
            pick_free_port=Mock(return_value=9012), refuse_old=observed.refuse,
            expected_old=routing.Endpoint.from_dict(row),
        ).run()
    assert result.ok is False
    assert "selected predecessor differs" in result.error
    spawn.assert_not_called()


def test_predecessor_shutdown_requires_its_drain_gate(serving, monkeypatch):
    from agent_bridge.client import BridgeClient

    root, row, health, reference, _ = serving
    reference.wait.side_effect = lambda timeout: timeout == 15.0
    request = Mock(return_value={"shutting_down": True})
    monkeypatch.setattr(BridgeClient, "_request", request)
    with reconcile.observe_serving(root) as observed:
        client = reconcile.guarded_client(
            observed, f"http://127.0.0.1:{row['port']}", "test-credential", 1,
        )
        with pytest.raises(reconcile.ServingReconcileError, match="drain gate reopened"):
            client.shutdown()
        request.assert_not_called()
        health["draining"] = True
        assert client.shutdown() == {"shutting_down": True}
        request.assert_called_once()


@pytest.mark.parametrize("path", ["/api/v1/drain", "/api/v1/undrain", "/api/v1/shutdown"])
def test_serving_control_never_retries_into_a_replacement(serving, monkeypatch, path):
    import urllib.error
    import urllib.request

    from agent_bridge.client import BridgeClient, BridgeConnectionError, DEFAULT_RESTART_GRACE

    root, row, health, reference, _ = serving
    attempts = []

    def urlopen(request, **_kwargs):
        attempts.append(request)
        if len(attempts) == 1:
            reference.wait.return_value = True
            health["slot"]["pid"] = 9999
            raise urllib.error.URLError(ConnectionRefusedError("captured predecessor exited"))
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b'{"shutting_down":true}'
        return response

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with reconcile.observe_serving(root) as observed:
        if path == "/api/v1/shutdown":
            health["draining"] = True
        client = reconcile.guarded_client(
            observed, f"http://127.0.0.1:{row['port']}", "test-credential", 1,
        )
        with pytest.raises(BridgeConnectionError):
            client._request("POST", path)
        ordinary = reconcile.guarded_client(
            observed, "http://127.0.0.1:9012", "test-credential", 1,
        )
        assert type(ordinary) is BridgeClient
        assert ordinary._connect_grace == DEFAULT_RESTART_GRACE
    assert len(attempts) == 1
    scheme = "Be" + "arer "
    assert attempts[0].get_header("Authorization") == scheme + "test-credential"


def test_serving_reconcile_uses_guarded_deploy_without_legacy_recovery(serving, monkeypatch):
    from zdd import breadcrumb, cutover, routing
    from agent_bridge import config, venue_cli

    root, row, _, _, core = serving
    core._active_endpoint_is_forward.return_value = False
    core._reap_abandoned_passive.side_effect = AssertionError("legacy passive reaper ran")
    monkeypatch.setattr(venue_cli, "_core", lambda: core)
    monkeypatch.setattr(config, "config_dir", lambda: root)
    monkeypatch.setattr(config, "load_config", lambda: Namespace(bind="127.0.0.1"))
    monkeypatch.setattr(
        config, "load_or_create_auth_token",
        Mock(side_effect=AssertionError("credential creation path ran")),
    )
    monkeypatch.setattr(
        breadcrumb, "recover_stale_cutover",
        Mock(side_effect=AssertionError("legacy recovery ran")),
    )
    orchestrator = Mock()
    orchestrator.run.return_value = cutover.CutoverResult(ok=False, error="fixture refusal")
    factory = Mock(return_value=orchestrator)
    monkeypatch.setattr(cutover, "CutoverOrchestrator", factory)
    args = Namespace(
        force=False, recover=False, json=True, reconcile_serving=True,
        health_timeout=1, drain_timeout=1,
    )
    with reconcile.observe_serving(root) as observed, pytest.raises(SystemExit) as exit:
        venue_cli._cmd_deploy(args, serving=observed)
    assert exit.value.code == 1
    kwargs = factory.call_args.kwargs
    assert kwargs["expected_old"] == routing.Endpoint.from_dict(row)
    assert kwargs["refuse_old"](row) is None
    assert orchestrator.run.call_args.kwargs["force"] is False
    core._ensure_retired_daemon_exited.assert_not_called()


@pytest.mark.parametrize("scenario", [
    "success", "retirement-pending", "shutdown-error", "drain-refused", "successor-unhealthy",
    "rollback-owner-changed", "forward-race", "cleanup-drain-refused",
    "cleanup-successor-unhealthy", "cleanup-changed-previous", "cleanup-race-previous",
    "initial-health-failed", "same-version-shadow", "successor-drained", "busy-successor",
    "busy-successor-owner-changed",
    "predecessor-exits-during-successor-drain", "predecessor-exits-after-successor-stop",
    "predecessor-drain-reopened", "clean-rollback-retry",
])
def test_serving_reconcile_actual_cutover_requires_native_retirement(
    serving, monkeypatch, scenario,
):
    from urllib.parse import urlparse
    import agent_bridge
    from agent_bridge import config, venue_cli
    from agent_bridge.client import BridgeClient
    from zdd import cutover, routing

    root, row, _, old_reference, core = serving
    state = {
        "retired": False, "draining": False, "successor_stopped": False,
        "successor_draining": False,
    }
    old_reference.wait.side_effect = lambda _: state["retired"]
    new_reference = MagicMock(pid=2222, start_time="123456")
    new_reference.wait.side_effect = lambda _: state["successor_stopped"]
    new_reference.__enter__.return_value = new_reference
    new_reference.close.return_value = None
    new_reference.__exit__.side_effect = lambda *_: new_reference.close()
    references = {row["pid"]: old_reference, 2222: new_reference}
    monkeypatch.setattr(
        process_exit, "open_process", lambda pid, _start: references[pid],
    )
    monkeypatch.setattr(agent_bridge, "__version__", "1.2.3")
    monkeypatch.setattr(venue_cli, "_core", lambda: core)
    monkeypatch.setattr(config, "config_dir", lambda: root)
    monkeypatch.setattr(config, "load_config", lambda: Namespace(bind="127.0.0.1"))
    monkeypatch.setattr(
        config, "load_or_create_auth_token",
        Mock(side_effect=AssertionError("credential creation path ran")),
    )
    core._active_endpoint_is_forward.return_value = False
    if scenario == "forward-race":
        def forward_changed():
            (root / "active.json").write_text(json.dumps({"active": {
                **row, "forwarded": True,
            }}))
            return True
        core._active_endpoint_is_forward.side_effect = forward_changed
    # A broker/venv launcher is deliberately NOT the actual worker.
    handle = Mock(pid=9991)
    handle.poll.side_effect = lambda: 0 if state["successor_stopped"] else None
    def terminate():
        if scenario.startswith("cleanup-"):
            assert routing.clear_if_owner(root, 2222)
            if scenario == "cleanup-changed-previous":
                (root / "active.json").write_text(json.dumps({"previous": {
                    **row, "pid": 3333, "generation": 20, "process_start_time": "999999",
                }}))
        state["successor_stopped"] = True
    handle.terminate.side_effect = terminate
    launch_id = None

    def launch(command, **_kwargs):
        nonlocal launch_id

        launch_id = command[command.index("--serving-launch-id") + 1]
        if scenario == "initial-health-failed":
            state["draining"] = True  # Operator drains after observation, before health.
        return handle

    core._launch_bridge_daemon.side_effect = launch

    def health(port, **_kwargs):
        from agent_bridge.serving_candidate import receipt_path

        active = routing.read_table(root).get("active")
        pid = row["pid"] if port == row["port"] else 2222
        if pid == 2222 and launch_id is not None:
            path = receipt_path(root, launch_id)
            if not path.exists():
                # The broker returns before the worker reaches its startup
                # receipt; health must bind the actual worker later.
                path.write_text(json.dumps({
                    "launch_id": launch_id, "pid": 2222, "process_start_time": "123456",
                }))
        return {
            "status": (
                "failed" if pid == 2222 and state["draining"]
                and scenario.endswith("successor-unhealthy") else "ok"
            ),
            "service": "agent-bridge",
            "ready": not (pid == 2222 and scenario == "initial-health-failed"),
            "version": row["version"] if pid == row["pid"] else "1.2.3",
            "draining": (
                state["draining"] if pid == row["pid"] else
                state["successor_draining"] or (
                    state["draining"] and scenario == "successor-drained"
                )
            ),
            "slot": {"pid": 3333 if pid == 2222 and scenario == "same-version-shadow" else pid,
                     "role": "active" if active and active["pid"] == pid else "passive",
                     "active": active},
        }

    core._service_health_on_port.side_effect = health

    def urlopen(url, **_kwargs):
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(health(urlparse(url).port)).encode()
        return response

    monkeypatch.setattr(venue_cli.urllib.request, "urlopen", urlopen)
    if scenario == "initial-health-failed":
        from agent_bridge.service_process_state import _service_health_on_port

        core._service_health_on_port.side_effect = _service_health_on_port
    monkeypatch.setattr(routing, "_listening", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(routing, "_pid_alive", lambda _: True)
    calls = []
    old_shutdowns = []
    successor_drains = []
    successor_undrains = []

    def request(client, method, path, body=None, **_kwargs):
        calls.append((method, path, body))
        if path == "/api/v1/drain":
            assert body["force"] is False
            if urlparse(client._base).port != row["port"]:
                successor_drains.append(body)
                state["successor_draining"] = True
                if scenario == "predecessor-exits-during-successor-drain":
                    state["retired"] = True
                if scenario.startswith("busy-successor"):
                    return {
                        "drained": False, "clean": False, "forced": False,
                        "busy_sessions": ["non-preservable-command-turn"],
                    }
                return {"drained": True, "clean": True, "forced": False, "busy_sessions": []}
            state["draining"] = True
            if scenario in ("rollback-owner-changed", "busy-successor-owner-changed"):
                (root / "active.json").write_text(json.dumps({"active": {
                    **row, "pid": 3333, "generation": 20, "process_start_time": "999999",
                }}))
            if scenario.endswith("drain-refused") or scenario in (
                "rollback-owner-changed", "cleanup-changed-previous", "cleanup-race-previous",
                "busy-successor",
                "busy-successor-owner-changed",
                "predecessor-exits-during-successor-drain", "predecessor-exits-after-successor-stop",
                "clean-rollback-retry",
            ):
                return {"drained": False, "clean": False, "forced": False, "busy_sessions": ["busy"]}
            if scenario == "predecessor-drain-reopened":
                state["draining"] = False
            return {"drained": True, "clean": True, "forced": False, "busy_sessions": []}
        if path == "/api/v1/undrain":
            if urlparse(client._base).port != row["port"]:
                successor_undrains.append(path)
                state["successor_draining"] = False
                return {"draining": False}
            state["draining"] = False
            return {"draining": False}
        if path == "/api/v1/shutdown":
            if urlparse(client._base).port != row["port"]:
                terminate()
                if scenario == "predecessor-exits-after-successor-stop":
                    state["retired"] = True
                return {"shutting_down": True}
            old_shutdowns.append(path)
            pending = json.loads((root / "cutover.json").read_text())
            assert pending["requires_writer_reconciliation"] is True
            assert pending["old"] == routing.read_table(root)["previous"]
            if scenario == "shutdown-error":
                raise RuntimeError("fixture control transport failed")
            state["retired"] = scenario == "success"
            return {"shutting_down": True}
        return {}

    monkeypatch.setattr(BridgeClient, "_request", request)

    class Routing:
        ActivePublicationRefused = routing.ActivePublicationRefused

        def read_table(self, directory):
            return routing.read_table(directory)

        def read_active_endpoint(self, directory):
            active = self.read_table(directory).get("active")
            return routing.Endpoint.from_dict(active) if active else None

        def reap_stale_active(self, *_args, **_kwargs):
            pass

        def publish_active_with_previous_guarded(self, directory, **kwargs):
            table = self.read_table(directory)
            if (
                scenario == "cleanup-race-previous" and kwargs["pid"] == row["pid"]
                and table.get("active") is None
            ):
                table = {"previous": {
                    **row, "pid": 3333, "generation": 20, "process_start_time": "999999",
                }}
                (directory / "active.json").write_text(json.dumps(table))
            active = table.get("active")
            if kwargs.get("refuse_current") is not None:
                refusal = kwargs["refuse_current"](active)
                if refusal:
                    raise routing.ActivePublicationRefused(refusal)
            assert kwargs["expected_active"] == (
                routing.Endpoint.from_dict(active) if active else None
            )
            successor = routing.Endpoint(
                kwargs["bind"], kwargs["port"], kwargs["pid"], kwargs["version"],
                (active or table["previous"])["generation"] + 1,
                kwargs.get("process_start_time", "123456"),
            )
            (directory / "active.json").write_text(json.dumps({
                "active": successor.to_dict(), "previous": active,
            }))
            return successor, routing.Endpoint.from_dict(active) if active else None

    real_orchestrator = cutover.CutoverOrchestrator
    monkeypatch.setattr(
        cutover, "CutoverOrchestrator",
        lambda *args, **kwargs: real_orchestrator(
            *args, **kwargs, routing_mod=Routing(), sleep=lambda _: None,
        ),
    )
    args = Namespace(
        force=False, recover=False, json=True, reconcile_serving=True,
        health_timeout=1, drain_timeout=1,
    )
    with pytest.raises(SystemExit) as exit:
        reconcile.reconcile_serving(args)
    assert exit.value.code == (0 if scenario == "success" else 1)
    result = core._json_out.call_args.args[0]
    assert result["ok"] is (scenario == "success")
    if scenario != "forward-race":
        assert "spawned passive pid=? " in result["steps"][0]
    assert core._launch_bridge_daemon.call_count == (0 if scenario == "forward-race" else 1)
    never_published = scenario in ("forward-race", "initial-health-failed", "same-version-shadow")
    rollback = scenario in (
        "drain-refused", "successor-unhealthy", "successor-drained", "rollback-owner-changed",
        "cleanup-drain-refused", "cleanup-successor-unhealthy", "cleanup-changed-previous",
        "cleanup-race-previous", "busy-successor",
        "busy-successor-owner-changed",
        "predecessor-exits-during-successor-drain", "predecessor-exits-after-successor-stop",
        "clean-rollback-retry",
    )
    assert sum(path == "/api/v1/drain" for _, path, _ in calls) == (
        0 if never_published else 2 if rollback else 1
    )
    assert bool(successor_drains) is rollback
    assert sum(call.args == (15.0,) for call in old_reference.wait.call_args_list) == (
        1 if scenario in ("success", "retirement-pending") else 0
    )
    assert bool(old_shutdowns) is (
        scenario in ("success", "retirement-pending", "shutdown-error")
    )
    assert any(path == "/api/v1/session-hosts/reattach" for _, path, _ in calls) is (scenario == "success")
    core._ensure_retired_daemon_exited.assert_not_called()
    if scenario in (
        "drain-refused", "successor-unhealthy", "successor-drained",
        "cleanup-drain-refused", "cleanup-successor-unhealthy",
        "clean-rollback-retry",
    ):
        assert result["rolled_back"] is True
        assert routing.read_table(root)["active"]["pid"] == row["pid"]
        assert routing.read_table(root)["active"]["generation"] > row["generation"]
        assert state["draining"] is False
        assert state["successor_stopped"] is True
    elif scenario == "rollback-owner-changed":
        assert result["rolled_back"] is False
        assert routing.read_table(root)["active"]["pid"] == 3333
        assert not any(path == "/api/v1/undrain" for _, path, _ in calls)
        assert state["successor_stopped"] is True
    elif scenario in ("cleanup-changed-previous", "cleanup-race-previous"):
        assert result["rolled_back"] is False
        assert routing.read_table(root).get("active") is None
        assert routing.read_table(root)["previous"]["pid"] == 3333
        assert not any(path == "/api/v1/undrain" for _, path, _ in calls)
        assert state["successor_stopped"] is True
    else:
        handle.terminate.assert_not_called()
    handle.terminate.assert_not_called()  # Never signal a broker or its PID tree.
    if scenario == "initial-health-failed":
        assert result["rolled_back"] is True
        assert routing.read_table(root) == {"active": row}
        assert (root / "active.json").read_text() == json.dumps({"active": row})
        assert state["draining"] is True
        assert not any(path in ("/api/v1/drain", "/api/v1/undrain") for _, path, _ in calls)
        assert state["successor_stopped"] is True
    if scenario == "same-version-shadow":
        assert result["rolled_back"] is False
        assert not calls
        assert routing.read_table(root) == {"active": row}
        assert (root / "cutover.json").exists()
        assert state["successor_stopped"] is False
    if scenario.startswith("busy-successor"):
        assert result["rolled_back"] is False
        assert routing.read_table(root)["active"]["pid"] == (
            3333 if scenario.endswith("owner-changed") else 2222
        )
        assert (root / "cutover.json").exists()
        assert state["successor_stopped"] is False
        assert not any(path == "/api/v1/shutdown" for _, path, _ in calls)
        assert len(successor_undrains) == (0 if scenario.endswith("owner-changed") else 1)
        assert state["successor_draining"] is scenario.endswith("owner-changed")
        assert list(root.glob("serving-launch-*.json")), "busy worker lost recovery identity"
    if scenario == "predecessor-exits-during-successor-drain":
        assert result["rolled_back"] is False
        assert routing.read_table(root)["active"]["pid"] == 2222
        assert state["successor_stopped"] is False
        assert not any(path == "/api/v1/shutdown" for _, path, _ in calls)
        assert len(successor_undrains) == 1
        assert state["successor_draining"] is False, "only surviving daemon rejects admission"
        assert state["draining"] is True, "compensation reopened the exited predecessor"
        assert (root / "cutover.json").exists()
    if scenario == "predecessor-exits-after-successor-stop":
        assert result["rolled_back"] is False
        assert routing.read_table(root)["active"]["pid"] == 2222, "republished an exited predecessor"
        assert state["successor_stopped"] is True
        assert not any(path == "/api/v1/undrain" for _, path, _ in calls)
        assert (root / "cutover.json").exists()
    if scenario == "predecessor-drain-reopened":
        assert state["retired"] is False
        assert state["successor_stopped"] is False
        assert not any(path == "/api/v1/shutdown" for _, path, _ in calls)
        assert (root / "cutover.json").exists()
    if scenario == "clean-rollback-retry":
        assert not (root / "cutover.json").exists(), "clean rollback blocked the next update"
        launches = core._launch_bridge_daemon.call_count
        state.update(retired=False, draining=False, successor_stopped=False, successor_draining=False)
        scenario = "success"
        with pytest.raises(SystemExit) as retried:
            reconcile.reconcile_serving(args)
        assert retried.value.code == 0
        assert core._json_out.call_args.args[0]["ok"] is True
        assert core._launch_bridge_daemon.call_count == launches + 1
    if scenario == "forward-race":
        core._launch_bridge_daemon.assert_not_called()
        assert routing.read_table(root)["active"]["forwarded"] is True
    if scenario in ("retirement-pending", "shutdown-error"):
        pending = (root / "cutover.json").read_bytes()
        launches = core._launch_bridge_daemon.call_count
        controls = len(calls)
        with pytest.raises(SystemExit) as retry:
            reconcile.reconcile_serving(args)
        assert retry.value.code == 1
        assert core._json_out.call_args.args[0]["ok"] is False
        assert (root / "cutover.json").read_bytes() == pending
        assert core._launch_bridge_daemon.call_count == launches
        assert len(calls) == controls
    if scenario == "success":
        assert not (root / "cutover.json").exists()
        assert routing.read_table(root)["active"]["pid"] == 2222
        assert routing.read_table(root)["active"]["process_start_time"] == "123456"
