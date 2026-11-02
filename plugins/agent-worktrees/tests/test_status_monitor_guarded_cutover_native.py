"""Opt-in real installer/orchestrator/control pairing with owned fixture Jobs."""
from __future__ import annotations

import os
import json
import threading
from contextlib import nullcontext
import sys

import pytest
from zdd import breadcrumb, routing, routing_persistence
from zdd.cutover import CutoverError, CutoverOrchestrator

from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import status_monitor_reap_stale, status_monitor_runtime
from agent_worktrees import status_monitor_recovery_contract as recovery
import test_status_monitor_guarded_recovery_native as native

provider = native.provider

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows Job/native identity proof"),
    pytest.mark.skipif(os.environ.get("AGENT_WORKTREES_NATIVE_RECOVERY") != "1",
                       reason="opt in with AGENT_WORKTREES_NATIVE_RECOVERY=1"),
]


def installer_fixture(monkeypatch, provider):
    root, _, route, token_path = provider
    effects = []
    monkeypatch.setattr("agent_worktrees.runtime_monitor_demand.retain_transition_demand",
                        lambda *a, **k: nullcontext())
    monkeypatch.setattr(recovery, "enabled", lambda: True)
    monkeypatch.setattr(control, "routing_dir", lambda *a: root)
    monkeypatch.setattr(control, "control_token_path", lambda: token_path)
    monkeypatch.setattr(control, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(control, "_monitor_control_url_from_route", lambda: route.base_url)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(control, "_reap_abandoned_passive", lambda *a: effects.append("reap") or {})
    monkeypatch.setattr(status_monitor_reap_stale, "schedule_delayed_daemon_health_reap",
                        lambda: effects.append("backstop"))
    monkeypatch.setattr(control, "load_or_create_control_token", lambda: pytest.fail("minted token"))
    return effects


@pytest.mark.parametrize("provider", ["old-provider"], indirect=True)
def test_native_installer_capability_refusal_precedes_every_transition_effect(provider, monkeypatch):
    effects = installer_fixture(monkeypatch, provider)
    monkeypatch.setattr(control, "spawn_passive", lambda *a, **k: pytest.fail("spawned"))
    result = control.activate_after_update(health_timeout=2, drain_timeout=2)
    assert result["action"] == "refused" and result["result"]["ok"] is False
    assert effects == []
    root, data, route, _ = provider
    health = control.ControlClient(route.base_url, token=data["token"]).health()
    assert health["opened"] == 0 and health["health_calls"] == 2
    assert recovery.active_endpoint(root) == route


@pytest.mark.parametrize("provider", ["normal", "untyped-reply", "lost-reply"], indirect=True)
def test_native_installer_rollback_requires_typed_positive_ack(provider, tmp_path, monkeypatch, request):
    root, data, old, _ = provider
    effects = installer_fixture(monkeypatch, provider)
    original_drain = control.ControlClient.drain

    def lose_drain_receipt(client, **kwargs):
        reply = original_drain(client, **kwargs)
        assert reply["drained"] is True
        raise CutoverError("fixture failure after predecessor accepted drain")

    monkeypatch.setattr(control.ControlClient, "drain", lose_drain_receipt)
    with native.start_provider(tmp_path / "candidate", "candidate") as candidate:
        _, new_data, new_route, _ = candidate
        monkeypatch.setattr(control, "spawn_passive", lambda *a, **k: new_data["process"])
        monkeypatch.setattr(control, "pick_free_port", lambda: new_route.port)
        summary = control.activate_after_update(health_timeout=2, drain_timeout=2)
        result = summary["result"]
        assert summary["action"] == "cutover" and result["ok"] is False
        assert new_data["process"].poll() is not None
        restored = recovery.active_endpoint(root)
        assert restored.pid == old.pid and restored.generation > old.generation
        assert restored.process_start_time == data["start"]
        health = control.ControlClient(old.base_url, token=data["token"]).health()
        assert health["opened"] == 1
        acknowledged = request.node.callspec.params["provider"] == "normal"
        assert result["rolled_back"] is acknowledged
        assert (breadcrumb.read_breadcrumb(root) is None) is acknowledged
        assert effects == ["reap", "backstop"]


def test_native_rollback_keeps_predecessor_closed_until_successor_exit_confirmed(provider, tmp_path, monkeypatch):
    root, data, old, _ = provider
    retained = recovery.negotiate(root, provider[3], control.ControlClient, timeout=3)
    with native.start_provider(tmp_path / "candidate", "candidate") as candidate:
        _, new_data, new_route, _ = candidate
        process = new_data["process"]

        class UnconfirmedExit:
            pid = process.pid

            def terminate(self):
                pass

            def poll(self):
                return process.poll()

        def make_client(url):
            return control.ControlClient(url, token=data["token"], config_dir=root, timeout=3,
                                         guarded_predecessor=retained)

        def fail_after_publication(endpoint):
            raise CutoverError("fixture failure after successor publication")

        orch = CutoverOrchestrator(
            root, bind=old.bind, version="candidate", spawn_passive=lambda port: UnconfirmedExit(),
            health_check=lambda host, port: recovery.successor_healthy(host, port, make_client),
            predecessor_health_check=lambda host, port: retained.predecessor_healthy(host, port, make_client),
            make_client=make_client, pick_free_port=lambda: new_route.port,
            expected_old=old, refuse_old=retained.refuse_old, drain_before_publish=True,
            on_published=fail_after_publication,
        )
        result = orch.run(health_timeout=2, drain_timeout=2)
        assert not result.ok and not result.rolled_back and not result.committed
        assert process.poll() is None and recovery.active_endpoint(root).pid == process.pid
        assert any("exit unconfirmed" in step for step in result.steps)
        assert make_client(old.base_url).health()["opened"] == 0
        assert breadcrumb.read_breadcrumb(root) is not None


def test_native_uncertain_publication_retains_generations_without_undrain(provider, tmp_path, monkeypatch):
    root, data, old, _ = provider
    installer_fixture(monkeypatch, provider)
    publish = routing.publish_active_with_previous_guarded

    def unconfirmed_commit(*args, **kwargs):
        publish(*args, **kwargs)
        raise routing_persistence.PublicationCommitUncertain("fixture durability confirmation lost")

    with native.start_provider(tmp_path / "candidate", "candidate") as candidate:
        _, new_data, new_route, _ = candidate
        monkeypatch.setattr(control, "spawn_passive", lambda *a, **k: new_data["process"])
        monkeypatch.setattr(control, "pick_free_port", lambda: new_route.port)
        monkeypatch.setattr(routing, "publish_active_with_previous_guarded", unconfirmed_commit)
        result = control.activate_after_update(health_timeout=2, drain_timeout=2)["result"]
        assert result["ok"] is False and result["rolled_back"] is False
        assert result["publication_uncertain"] is True
        assert new_data["process"].poll() is None and data["process"].poll() is None
        assert recovery.active_endpoint(root).pid == new_data["pid"]
        assert control.ControlClient(old.base_url, token=data["token"]).health()["opened"] == 0
        assert breadcrumb.read_breadcrumb(root)["state"] == "publication-uncertain"


@pytest.mark.parametrize("provider", ["closed-listener"], indirect=True)
@pytest.mark.parametrize("was_live", [False, True])
def test_native_live_previous_without_listener_refuses_before_restart(provider, monkeypatch, was_live):
    root, data, route, _ = provider
    real_url = control._monitor_control_url_from_route
    effects = installer_fixture(monkeypatch, provider)
    native.wait_for(lambda: (root / "listener-closed").exists())
    (root / "active.json").write_text(json.dumps({"active": None, "previous": route.to_dict()}), encoding="utf-8")
    monkeypatch.setattr(control, "_monitor_lock_is_live", lambda: False)
    monkeypatch.setattr(control, "_monitor_control_url_from_route", real_url)
    monkeypatch.setattr(status_monitor_runtime, "_restart_status_monitor", lambda **k: effects.append("restart"))
    monkeypatch.setattr(control, "spawn_passive", lambda *a, **k: pytest.fail("spawned"))
    assert recovery.routed_owner_present(root) is True and real_url() is None
    result = control.activate_after_update(monitor_was_live=was_live)
    assert result["action"] == "refused" and result["result"]["ok"] is False
    assert effects == [] and data["process"].poll() is None
    assert routing.read_table(root)["previous"] == route.to_dict()


def candidate_spawn(monkeypatch, root, candidate):
    new_root, new_data, new_route, _ = candidate
    def spawn(*a, **k):
        (new_root / "route-root").write_text(str(root), encoding="utf-8")
        return new_data["process"]
    monkeypatch.setattr(control, "spawn_passive", spawn)
    monkeypatch.setattr(control, "pick_free_port", lambda: new_route.port)
    return new_data, new_route


@pytest.mark.parametrize("mode", ["candidate-refuse", "candidate-unready"])
def test_native_passive_health_cannot_retire_old_without_serving_promotion(
    provider, tmp_path, monkeypatch, mode,
):
    root, data, old, _ = provider
    installer_fixture(monkeypatch, provider)
    with native.start_provider(tmp_path / "candidate", mode) as candidate:
        new_data, new_route = candidate_spawn(monkeypatch, root, candidate)
        summary = control.activate_after_update(health_timeout=2, drain_timeout=2)
        result = summary["result"]
        assert result["ok"] is False and result["committed"] is False
        assert result["rolled_back"] is True
        assert (candidate[0] / "promote-requested").exists()
        assert not (root / "shutdown-requested").exists() and data["process"].poll() is None
        assert new_data["process"].poll() is not None
        restored = recovery.active_endpoint(root)
        assert restored.pid == old.pid and restored.generation > old.generation
        assert control.ControlClient(old.base_url, token=data["token"]).health()["opened"] == 1
        assert breadcrumb.read_breadcrumb(root) is None


@pytest.mark.parametrize("provider", ["retirement-gate"], indirect=True)
def test_native_successful_cutover_activates_then_waits_for_exact_owner_exit(
    provider, tmp_path, monkeypatch,
):
    from zdd import process_exit
    root, data, old, _ = provider
    installer_fixture(monkeypatch, provider)
    references, orchestrated = [], []
    run = CutoverOrchestrator.run
    def observed_run(orch, **kwargs):
        result = run(orch, **kwargs)
        orchestrated.append(result)
        return result
    monkeypatch.setattr(CutoverOrchestrator, "run", observed_run)
    open_process = process_exit.open_process
    def capture(pid, start):
        assert pid == old.pid and start == old.process_start_time
        assert not (root / "shutdown-requested").exists()
        ref = open_process(pid, start)
        assert ref.wait(0) is False
        references.append(ref)
        return ref
    monkeypatch.setattr(process_exit, "open_process", capture)
    with native.start_provider(tmp_path / "candidate", "candidate") as candidate:
        new_data, new_route = candidate_spawn(monkeypatch, root, candidate)
        summaries, errors = [], []
        def activate():
            try:
                summaries.append(control.activate_after_update(health_timeout=2, drain_timeout=2))
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=activate)
        worker.start()
        try:
            native.wait_for(lambda: (root / "shutdown-requested").exists() or errors or summaries)
            assert not errors and not summaries and worker.is_alive()
            assert len(references) == 1 and not references[0]._closed
            assert data["process"].poll() is None
            health = json.loads((root / "successor-at-shutdown.json").read_text(encoding="utf-8"))
            assert health["pid"] == new_route.pid and health["published"] is True
            assert health["worktrees"]["status"] == "ready" and health["repositories"]["admitting"] is True
            assert breadcrumb.read_breadcrumb(root) is not None
        finally:
            (root / "release").touch()
            worker.join(15)
        assert not worker.is_alive() and not errors
        result = summaries[0]["result"]
        assert result["ok"] is True and result["committed"] is True
        assert orchestrated[0].retirement_requested and orchestrated[0].retirement_confirmed
        assert references[0]._closed and (root / "retirement-settled").exists()
        assert data["process"].wait(timeout=5) == 0 and new_data["process"].poll() is None
        assert recovery.active_endpoint(root).pid == new_route.pid and breadcrumb.read_breadcrumb(root) is None


@pytest.mark.parametrize("provider", ["retirement-gate"], indirect=True)
def test_native_shutdown_acceptance_does_not_confirm_still_live_owner_exit(provider):
    _, client = native.retained_client(provider)
    root, data, _, _ = provider
    client.timeout = 0.1
    try:
        with pytest.raises(recovery.RecoveryUnavailable, match="retirement"):
            client.shutdown()
        assert (root / "shutdown-requested").exists() and data["process"].poll() is None
        assert not (root / "retirement-settled").exists()
    finally:
        (root / "release").touch()
