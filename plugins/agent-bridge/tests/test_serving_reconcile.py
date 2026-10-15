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


def test_serving_reconcile_rechecks_port_owner_before_control(serving, monkeypatch):
    from agent_bridge.client import BridgeClient

    root, row, health, _, _ = serving
    request = Mock(return_value={"retired": True})
    monkeypatch.setattr(BridgeClient, "_request", request)
    with reconcile.observe_serving(root) as observed:
        client = reconcile.guarded_client(
            observed, f"http://127.0.0.1:{row['port']}", "test-credential", 10,
        )
        assert client.shutdown() == {"retired": True}
        request.assert_called_once()
        request.reset_mock()
        health["slot"]["pid"] = 9999
        with pytest.raises(reconcile.ServingReconcileError):
            client.shutdown()
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


def test_serving_reconcile_uses_guarded_deploy_without_legacy_recovery(serving, monkeypatch):
    from zdd import breadcrumb, cutover, routing
    from agent_bridge import config, venue_cli

    root, row, _, _, core = serving
    core._active_endpoint_is_forward.return_value = False
    core._reap_abandoned_passive.side_effect = AssertionError("legacy passive reaper ran")
    monkeypatch.setattr(venue_cli, "_core", lambda: core)
    monkeypatch.setattr(config, "config_dir", lambda: root)
    monkeypatch.setattr(config, "load_config", lambda: Namespace(bind="127.0.0.1"))
    monkeypatch.setattr(config, "load_or_create_auth_token", lambda: "test-credential")
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
    assert kwargs["refuse_old"].__self__ is observed
    assert orchestrator.run.call_args.kwargs["force"] is False
    core._ensure_retired_daemon_exited.assert_not_called()
