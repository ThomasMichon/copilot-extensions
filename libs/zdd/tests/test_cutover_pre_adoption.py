"""Serialized pre-adoption drain without changing ordinary cutover ordering."""

from __future__ import annotations

import json
import os
import threading

import pytest

from zdd import breadcrumb, routing
from zdd.cutover import CutoverOrchestrator
from zdd.cutover_lock import CutoverBorrowError, CutoverLock, CutoverLockedError


class Handle:
    pid = 4321

    def __init__(self, events, *, confirms_exit=True):
        self.events = events
        self.terminated = False
        self.confirms_exit = confirms_exit

    def terminate(self):
        self.events.append("terminate-new")
        self.terminated = True

    def poll(self):
        if self.terminated and self.confirms_exit:
            self.events.append("confirmed-new-exit")
            return 0
        return None


class Client:
    def __init__(self, port, events, *, drained=True):
        self.port = port
        self.events = events
        self.drained = drained

    def drain(self, **kwargs):
        self.events.append(f"drain-{self.port}")
        return {"drained": self.drained, "clean": self.drained, "forced": False}

    def undrain(self):
        self.events.append(f"undrain-{self.port}")
        return {"draining": False}

    def shutdown(self):
        self.events.append(f"shutdown-{self.port}")
        return {"shutting_down": True, "retired": True}

    def adopt_relay(self):
        return {"adopted": False}


def orchestrator(tmp_path, monkeypatch, *, confirms_exit=True, drained=True):
    monkeypatch.setattr(routing, "_listening", lambda *args, **kwargs: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    events = []
    handle = Handle(events, confirms_exit=confirms_exit)
    healthy = {9281, 9290}
    clock_value = 0.0
    publish = routing.publish_active
    publish_guarded = routing.publish_active_with_previous_guarded

    def publish_recorded(*args, **kwargs):
        events.append(f"publish-{kwargs['port']}")
        return publish(*args, **kwargs)

    monkeypatch.setattr(routing, "publish_active", publish_recorded)

    def restore_recorded(*args, **kwargs):
        events.append(f"publish-{kwargs['port']}")
        return publish_guarded(*args, **kwargs)

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", restore_recorded)

    def clock():
        nonlocal clock_value
        clock_value += 0.1
        return clock_value

    clients = {
        port: Client(port, events, drained=drained)
        for port in (9281, 9290)
    }
    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="new",
        spawn_passive=lambda port: handle,
        health_check=lambda host, port: port in healthy,
        make_client=lambda url: clients[int(url.rsplit(":", 1)[1])],
        pick_free_port=lambda: 9290,
        clock=clock, sleep=lambda seconds: None,
        drain_before_publish=True,
    )
    return orch, events, healthy


@pytest.mark.parametrize("pre_adoption", [False, True])
def test_opt_in_drains_before_promotion_and_preserves_default_order(
    tmp_path, monkeypatch, pre_adoption,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    orch.drain_before_publish = pre_adoption
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed
    assert events.count("drain-9281") == 1
    assert (
        events.index("drain-9281") < events.index("publish-9290")
    ) is pre_adoption
    assert events.index("publish-9290") < events.index("shutdown-9281")
    assert breadcrumb.read_breadcrumb(tmp_path) is None


@pytest.mark.parametrize("confirms_exit", [False, True])
def test_failed_promotion_never_restores_writer_before_successor_exit(
    tmp_path, monkeypatch, confirms_exit,
):
    orch, events, _ = orchestrator(
        tmp_path, monkeypatch, confirms_exit=confirms_exit,
    )
    original = routing.publish_active_with_previous_guarded

    def fail_new(*args, **kwargs):
        if kwargs["port"] == 9290:
            raise OSError("promotion publication failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", fail_new)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.committed
    assert "shutdown-9281" not in events
    assert result.rolled_back is confirms_exit
    if confirms_exit:
        assert events.index("confirmed-new-exit") < events.index("publish-9281")
        assert events.index("confirmed-new-exit") < events.index("undrain-9281")
        assert events.count("terminate-new") == 1
    else:
        assert "publish-9281" not in events
        assert "undrain-9281" not in events
        assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "draining"
        assert not breadcrumb.recover_stale_cutover(tmp_path, orch.make_client)["recovered"]
        assert "undrain-9281" not in events


def test_incomplete_drain_does_not_publish_successor(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch, drained=False)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok
    assert "publish-9290" not in events
    assert "shutdown-9281" not in events
    assert events.index("confirmed-new-exit") < events.index("undrain-9281")

def test_failed_passive_health_keeps_undrained_predecessor_and_started_phase(
    tmp_path, monkeypatch,
):
    orch, events, healthy = orchestrator(tmp_path, monkeypatch, confirms_exit=False)
    healthy.remove(9290)
    result = orch.run(health_timeout=0.05, drain_timeout=1)
    assert not result.ok
    assert "drain-9281" not in events and "undrain-9281" not in events
    assert "publish-9281" not in events and "shutdown-9281" not in events
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "started"


@pytest.mark.parametrize("failure", ["health", "drain-journal", "drain-client", "drain-method"])
@pytest.mark.parametrize("confirms_exit", [False, True])
@pytest.mark.parametrize("independently_drained", [False, True])
def test_early_failure_preserves_predecessor_route_and_admission(
    tmp_path, monkeypatch, failure, confirms_exit, independently_drained,
):
    orch, events, healthy = orchestrator(
        tmp_path, monkeypatch, confirms_exit=confirms_exit,
    )
    original_route = routing.read_table(tmp_path)
    admission_open = not independently_drained
    client = orch.make_client("http://127.0.0.1:9281")
    undrain = client.undrain

    def reopen():
        nonlocal admission_open
        admission_open = True
        return undrain()

    monkeypatch.setattr(client, "undrain", reopen)
    if failure == "health":
        healthy.remove(9290)
    elif failure in {"drain-client", "drain-method"}:
        make_client = orch.make_client
        failed = False

        class UnavailableDrain:
            @property
            def drain(self):
                raise OSError("drain method unavailable")

        def fail_before_client_drain(url):
            nonlocal failed
            if url == "http://127.0.0.1:9281" and not failed:
                failed = True
                if failure == "drain-method":
                    return UnavailableDrain()
                raise OSError("drain client unavailable")
            return make_client(url)

        orch.make_client = fail_before_client_drain
    else:
        write = breadcrumb.write_breadcrumb
        failed = False

        def fail_before_drain(config_dir, **kwargs):
            nonlocal failed
            if kwargs["state"] == "draining" and not failed:
                failed = True
                raise OSError("drain journal write failed")
            return write(config_dir, **kwargs)

        monkeypatch.setattr(breadcrumb, "write_breadcrumb", fail_before_drain)

    result = orch.run(health_timeout=0.05, drain_timeout=1)
    assert not result.ok and not result.committed
    assert result.rolled_back is confirms_exit
    assert routing.read_table(tmp_path) == original_route
    assert admission_open is (not independently_drained)
    assert events == (
        ["terminate-new", "confirmed-new-exit"] if confirms_exit
        else ["terminate-new"]
    )


@pytest.mark.parametrize("failure", ["incomplete", "raises"])
def test_attempted_drain_retains_witness_when_call_does_not_complete(
    tmp_path, monkeypatch, failure,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    client = orch.make_client("http://127.0.0.1:9281")

    def drain(**kwargs):
        events.append("drain-9281")
        if failure == "raises":
            raise OSError("drain response lost")
        return {"drained": False, "clean": False, "forced": False}

    monkeypatch.setattr(client, "drain", drain)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.committed and result.rolled_back
    assert events.index("drain-9281") < events.index("confirmed-new-exit")
    assert events.index("confirmed-new-exit") < events.index("undrain-9281")
    assert "publish-9290" not in events and "shutdown-9281" not in events


def test_failed_post_publication_health_keeps_flipped_phase_when_exit_is_uncertain(
    tmp_path, monkeypatch,
):
    orch, events, healthy = orchestrator(tmp_path, monkeypatch, confirms_exit=False)
    publish = routing.publish_active_with_previous_guarded

    def publish_then_fail_health(*args, **kwargs):
        result = publish(*args, **kwargs)
        if kwargs["port"] == 9290:
            healthy.remove(9290)
        return result

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", publish_then_fail_health)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.rolled_back
    assert "undrain-9281" not in events and "publish-9281" not in events
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "flipped"


@pytest.mark.parametrize("failure", ["before-publication", "healthy", "unhealthy"])
def test_cold_start_rollback_preserves_a_published_successor(
    tmp_path, monkeypatch, failure,
):
    orch, events, healthy = orchestrator(tmp_path, monkeypatch)
    routing.routing_table_path(tmp_path).unlink()
    handle = orch.spawn_passive(9290)
    orch.health_check = lambda host, port: port in healthy and not handle.terminated
    write = breadcrumb.write_breadcrumb
    failed = False

    def fail_after_publication(config_dir, **kwargs):
        nonlocal failed
        if kwargs["state"] == "flipped" and not failed:
            failed = True
            if failure == "unhealthy":
                healthy.remove(9290)
            raise OSError("flipped breadcrumb write failed")
        return write(config_dir, **kwargs)

    if failure == "before-publication":
        healthy.remove(9290)
    else:
        monkeypatch.setattr(breadcrumb, "write_breadcrumb", fail_after_publication)

    result = orch.run(health_timeout=0.05, drain_timeout=1)
    if failure == "before-publication":
        assert not result.ok and result.rolled_back and not result.committed
        assert events.count("terminate-new") == 1
        assert "confirmed-new-exit" in events
        assert routing.read_table(tmp_path).get("active") is None
    else:
        assert "terminate-new" not in events
        active = routing.read_active_endpoint(tmp_path)
        assert active.port == 9290 and active.pid == handle.pid
        assert not result.rolled_back
        assert result.ok is (failure == "healthy")
        assert result.committed is (failure == "healthy")
        assert orch.health_check(active.client_host, active.port) is (failure == "healthy")
        record = breadcrumb.read_breadcrumb(tmp_path)
        assert record["state"] == ("committed" if failure == "healthy" else "flipped")
        assert breadcrumb.requires_owner_reconciliation(record)


def test_pre_adoption_cannot_force_unfinished_work(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    result = orch.run(force=True)
    assert not result.ok and "cannot force" in result.error
    assert events == []


def test_uncertain_publication_preserves_stateful_custody(tmp_path, monkeypatch):
    from zdd import routing_persistence

    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    publish = routing.publish_active_with_previous_guarded

    def uncertain(*args, **kwargs):
        publish(*args, **kwargs)
        raise routing_persistence.PublicationCommitUncertain("fixture flush failure")

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", uncertain)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.rolled_back and result.publication_uncertain
    assert "terminate-new" not in events and "undrain-9281" not in events
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record["state"] == "publication-uncertain"
    assert record["requires_writer_reconciliation"] is True
    assert record["publication_witness"]
    assert routing.read_table(tmp_path)["active"]["port"] == 9290
    with CutoverLock(tmp_path) as lease:
        refused = orch.run(held_lock=lease)
        assert lease.held
    assert not refused.ok and "installation-owner reconciliation" in refused.error

@pytest.mark.parametrize("report", [
    {"drained": True, "clean": False, "forced": False},
    {"drained": True, "clean": True, "forced": True},
    {"drained": "true", "clean": True, "forced": False},
])
def test_pre_adoption_requires_explicit_clean_unforced_drain(
    tmp_path, monkeypatch, report,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    client = orch.make_client("http://127.0.0.1:9281")
    monkeypatch.setattr(client, "drain", lambda **kwargs: report)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok
    assert "publish-9290" not in events
    assert "shutdown-9281" not in events


@pytest.mark.parametrize("lock_kind", ["absent", "foreign", "same"])
def test_caller_transaction_reuses_only_its_held_installation_guard(
    tmp_path, monkeypatch, lock_kind,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    lease = CutoverLock(tmp_path if lock_kind != "foreign" else tmp_path / "other")
    if lock_kind != "absent":
        lease.acquire()
    if lock_kind == "same":
        spawn = orch.spawn_passive

        def spawn_under_guard(port):
            assert lease.held
            with pytest.raises(CutoverLockedError):
                CutoverLock(tmp_path).acquire()
            return spawn(port)

        orch.spawn_passive = spawn_under_guard
    try:
        result = orch.run(health_timeout=1, drain_timeout=1, held_lock=lease)
        assert result.ok is (lock_kind == "same")
        assert lease.held is (lock_kind != "absent")
        if lock_kind == "same":
            with pytest.raises(CutoverLockedError):
                CutoverLock(tmp_path).acquire()
        else:
            assert events == []
    finally:
        lease.release()


@pytest.mark.parametrize("confirms_exit", [False, True])
def test_promotion_refusal_undrains_only_after_confirmed_successor_exit(
    tmp_path, monkeypatch, confirms_exit,
):
    orch, events, _ = orchestrator(
        tmp_path, monkeypatch, confirms_exit=confirms_exit,
    )
    orch.refuse_old = lambda current: None

    def refuse(*args, **kwargs):
        raise routing.ActivePublicationRefused("receipt changed")

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", refuse)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok
    if confirms_exit:
        assert events.index("confirmed-new-exit") < events.index("undrain-9281")
    else:
        assert "undrain-9281" not in events
        assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "draining"
    assert "shutdown-9281" not in events


@pytest.mark.parametrize("phase", ["started", "draining", "flipped", "committed"])
def test_legacy_repair_and_reentry_cannot_resume_unreconciled_writer(
    tmp_path, monkeypatch, phase,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    record = breadcrumb.write_breadcrumb(
        tmp_path, state=phase, old={"bind": "127.0.0.1", "port": 9281},
        new_pid=4321, new_port=9290, requires_writer_reconciliation=True,
    )
    recovery = breadcrumb.recover_stale_cutover(tmp_path, orch.make_client)
    assert not recovery["recovered"] and "installation-owner" in recovery["reason"]
    reaping = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: pytest.fail("legacy reaper probed stateful owner"),
        terminate=lambda pid: pytest.fail("legacy reaper signaled stateful owner"),
    )
    assert not reaping["reaped"] and "installation-owner" in reaping["reason"]
    assert breadcrumb.read_breadcrumb(tmp_path) == record
    assert not orch.run().ok
    assert breadcrumb.read_breadcrumb(tmp_path) == record
    assert events == []


@pytest.mark.parametrize("failure", ["crash", "error", "unconfirmed"])
def test_unfinished_retirement_preserves_committed_evidence_and_blocks_reentry(
    tmp_path, monkeypatch, failure,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    client = orch.make_client("http://127.0.0.1:9281")

    def shutdown():
        assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "committed"
        if failure == "crash":
            raise SystemExit("installer crash before retirement")
        if failure == "error":
            raise OSError("shutdown response lost")
        return {"shutting_down": True}

    monkeypatch.setattr(client, "shutdown", shutdown)
    if failure == "crash":
        with pytest.raises(SystemExit):
            orch.run(health_timeout=1, drain_timeout=1)
    else:
        result = orch.run(health_timeout=1, drain_timeout=1)
        assert result.ok and result.committed and result.error
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record["state"] == "committed"
    assert record["requires_writer_reconciliation"] is True
    assert routing.read_table(tmp_path)["active"]["port"] == 9290
    prior_events = list(events)
    assert not orch.run().ok
    assert breadcrumb.read_breadcrumb(tmp_path) == record
    assert events == prior_events


@pytest.mark.parametrize("flipped", [False, True])
@pytest.mark.parametrize("failure", ["publish", "undrain", "undrain-unconfirmed"])
def test_restoration_failure_preserves_nonterminal_recovery_evidence(
    tmp_path, monkeypatch, flipped, failure,
):
    orch, events, healthy = orchestrator(tmp_path, monkeypatch)
    if flipped:
        publish = routing.publish_active_with_previous_guarded

        def publish_then_fail_health(*args, **kwargs):
            result = publish(*args, **kwargs)
            if kwargs["port"] == 9290:
                healthy.remove(9290)
            return result

        monkeypatch.setattr(
            routing, "publish_active_with_previous_guarded", publish_then_fail_health,
        )
    else:
        publish = routing.publish_active_with_previous_guarded

        def fail_publish(*args, **kwargs):
            if kwargs["port"] == 9290:
                raise OSError("publication failed")
            return publish(*args, **kwargs)

        monkeypatch.setattr(routing, "publish_active_with_previous_guarded", fail_publish)

    def fail(*args, **kwargs):
        raise OSError("restoration failed")

    if failure == "publish":
        promotion = routing.publish_active_with_previous_guarded

        def fail_restore(*args, **kwargs):
            if kwargs["port"] == 9281:
                return fail()
            return promotion(*args, **kwargs)

        monkeypatch.setattr(routing, "publish_active_with_previous_guarded", fail_restore)
    else:
        client = orch.make_client("http://127.0.0.1:9281")
        monkeypatch.setattr(
            client, "undrain",
            fail if failure == "undrain" else lambda: {"draining": True},
        )
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.rolled_back and not result.committed
    assert "confirmed-new-exit" in events
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record["state"] == ("flipped" if flipped else "draining")
    assert record["requires_writer_reconciliation"] is True
    assert not breadcrumb.recover_stale_cutover(tmp_path, orch.make_client)["recovered"]
    assert breadcrumb.read_breadcrumb(tmp_path) == record


def test_refusal_with_displaced_predecessor_never_undrains_or_erases_evidence(
    tmp_path, monkeypatch,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    orch.refuse_old = lambda current: None

    def displace_and_refuse(*args, **kwargs):
        routing.publish_active(tmp_path, bind="127.0.0.1", port=9300, pid=555)
        raise routing.ActivePublicationRefused("active changed")

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", displace_and_refuse)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok
    assert "confirmed-new-exit" in events and "undrain-9281" not in events
    assert routing.read_table(tmp_path)["active"]["port"] == 9300
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "draining"


def test_refusal_with_failed_undrain_preserves_evidence(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    orch.refuse_old = lambda current: None

    def refuse(*args, **kwargs):
        raise routing.ActivePublicationRefused("receipt changed")

    def fail():
        raise OSError("undrain unavailable")

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", refuse)
    monkeypatch.setattr(orch.make_client("http://127.0.0.1:9281"), "undrain", fail)
    assert not orch.run(health_timeout=1, drain_timeout=1).ok
    assert "confirmed-new-exit" in events
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "draining"


def test_disagreeing_predecessor_probes_cannot_restore_live_successor(
    tmp_path, monkeypatch,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    old_probes = iter([False, True])
    new_probes = iter([True, False, True])
    orch.health_check = lambda host, port: next(
        old_probes if port == 9281 else new_probes,
    )
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed and not result.rolled_back
    assert "undrain-9281" not in events
    assert "publish-9281" not in events
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record["state"] == "committed"
    assert breadcrumb.requires_owner_reconciliation(record)
    prior_events = list(events)
    assert not orch.run().ok
    assert events == prior_events
    assert breadcrumb.read_breadcrumb(tmp_path) == record


def test_both_unhealthy_is_not_a_terminal_stateful_rollback(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    new_probes = iter([True, False, False])
    orch.health_check = lambda host, port: False if port == 9281 else next(new_probes)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.rolled_back and not result.committed
    assert "undrain-9281" not in events and "terminate-new" not in events
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "flipped"


def test_pre_adoption_publication_is_guarded_without_a_refusal_callback(
    tmp_path, monkeypatch,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    client = orch.make_client("http://127.0.0.1:9281")
    drain = client.drain

    def displace_during_drain(**kwargs):
        result = drain(**kwargs)
        routing.publish_active(tmp_path, bind="127.0.0.1", port=9300, pid=555)
        return result

    monkeypatch.setattr(client, "drain", displace_during_drain)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and "changed before publication" in result.error
    assert "undrain-9281" not in events and "shutdown-9281" not in events
    assert routing.read_table(tmp_path)["active"]["port"] == 9300
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "draining"


@pytest.mark.parametrize("restore_snapshot", [False, True])
def test_predecessor_selection_race_refuses_before_any_writer_is_drained(
    tmp_path, monkeypatch, restore_snapshot,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    original_table = routing.read_table(tmp_path)
    resolve = routing.read_active_endpoint

    def change_during_selection(config_dir):
        routing.publish_active(config_dir, bind="127.0.0.1", port=9300, pid=555)
        selected = resolve(config_dir)
        if restore_snapshot:
            routing._atomic_write(routing.routing_table_path(config_dir), original_table)
        return selected

    monkeypatch.setattr(routing, "read_active_endpoint", change_during_selection)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and "changed during selection" in result.error
    assert events == ["publish-9300"]
    assert breadcrumb.read_breadcrumb(tmp_path) is None
    assert routing.read_table(tmp_path)["active"]["port"] == (
        9281 if restore_snapshot else 9300
    )


@pytest.mark.parametrize("phase", ["port", "spawn", "health", "client"])
@pytest.mark.parametrize("confirms_exit", [False, True])
def test_route_change_after_selection_never_drains_stale_predecessor(
    tmp_path, monkeypatch, phase, confirms_exit,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch, confirms_exit=confirms_exit)
    changed = None

    def replace_once():
        nonlocal changed
        if changed is None:
            routing.publish_active(tmp_path, bind="127.0.0.1", port=9300, pid=555)
            changed = routing.read_table(tmp_path)

    if phase == "port":
        orch.pick_free_port = lambda: replace_once() or 9290
    elif phase == "spawn":
        spawn = orch.spawn_passive
        orch.spawn_passive = lambda port: replace_once() or spawn(port)
    elif phase == "health":
        healthy = orch.health_check
        orch.health_check = lambda host, port: replace_once() or healthy(host, port)
    else:
        make_client = orch.make_client
        orch.make_client = lambda url: replace_once() or make_client(url)

    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.committed
    assert "drain-9281" not in events and "undrain-9281" not in events
    assert "before drain" in result.error
    assert result.rolled_back is confirms_exit
    assert routing.read_table(tmp_path) == changed
    assert events == [
        "publish-9300", "terminate-new",
        *(["confirmed-new-exit"] if confirms_exit else []),
    ]


def test_pre_adoption_accepts_consistent_previous_only_selection(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    routing.clear_if_owner(tmp_path, pid=111)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed
    assert events.index("drain-9281") < events.index("publish-9290")


def test_pre_adoption_initializes_legacy_history_before_selection(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    table = routing.read_table(tmp_path)
    del table["publication_history"]
    routing.routing_table_path(tmp_path).write_text(json.dumps(table), encoding="utf-8")
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed
    assert events.index("drain-9281") < events.index("publish-9290")


@pytest.mark.parametrize("row", ["active", "previous"])
@pytest.mark.parametrize("value", [
    {}, [], False, "", {"bind": "127.0.0.1", "port": "invalid"},
    {"bind": "127.0.0.1", "port": 9281, "process_start_time": "invalid"},
    {"bind": "127.0.0.1", "port": float("inf")},
    *[
        {"bind": "127.0.0.1", "port": 9281, "pid": 111, **{field: value}}
        for field, value in [
            ("port", 0), ("port", 70000), ("port", True), ("port", "9281"),
            ("port", 9281.0), ("pid", True), ("pid", 0), ("pid", -1),
            ("pid", "111"), ("pid", 111.0), ("pid", 2**32),
            ("generation", -1), ("generation", True), ("generation", "1"),
            ("generation", 1.0), ("generation", 2**63),
            ("bind", None), ("bind", 123), ("bind", "x" * 256),
            ("version", 123), ("version", False),
            ("process_start_time", "\u0661"), ("process_start_time", "1" * 65),
        ]
    ],
])
def test_pre_adoption_refuses_malformed_endpoint_rows_before_any_mutation(
    tmp_path, monkeypatch, row, value,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    monkeypatch.setattr(routing, "_pid_alive", lambda pid: True)
    table = routing.read_table(tmp_path)
    table["previous"] = table["active"].copy()
    table[row] = value
    path = routing.routing_table_path(tmp_path)
    original = json.dumps(table).encode()
    path.write_bytes(original)
    spawned = []
    spawn = orch.spawn_passive
    orch.spawn_passive = lambda port: spawned.append(port) or spawn(port)

    result = orch.run(health_timeout=1, drain_timeout=1)

    assert not result.ok and result.error
    assert not spawned and events == []
    assert path.read_bytes() == original
    assert breadcrumb.read_breadcrumb(tmp_path) is None


@pytest.mark.parametrize("row", ["active", "previous"])
def test_pre_adoption_refuses_endpoint_damage_introduced_during_drain(
    tmp_path, monkeypatch, row,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    client = orch.make_client("http://127.0.0.1:9281")
    drain = client.drain
    damaged = None
    path = routing.routing_table_path(tmp_path)

    def damage_during_drain(**kwargs):
        nonlocal damaged
        report = drain(**kwargs)
        table = routing.read_table(tmp_path)
        table[row] = {}
        damaged = json.dumps(table).encode()
        path.write_bytes(damaged)
        return report

    monkeypatch.setattr(client, "drain", damage_during_drain)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and "endpoint unverifiable" in result.error
    assert not result.committed and "shutdown-9281" not in events
    assert path.read_bytes() == damaged
    assert "confirmed-new-exit" in events


def test_pre_adoption_refuses_a_routing_adapter_without_a_guarded_publication(
    tmp_path, monkeypatch,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)

    class Unguarded:
        def __getattr__(self, name):
            return getattr(routing, name)

    orch.routing = Unguarded()
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and "can't publish guarded" in result.error
    assert "publish-9290" not in events and "shutdown-9281" not in events
    assert routing.read_table(tmp_path)["active"]["port"] == 9281


def test_pre_adoption_accepts_an_exact_endpoint_publication_result(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    publish = routing.publish_active_with_previous_guarded

    def publish_endpoint(*args, **kwargs):
        return publish(*args, **kwargs)[0]

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", publish_endpoint)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed
    assert events.index("drain-9281") < events.index("publish-9290")


def test_unknown_publication_result_preserves_uncertain_route_and_writer_evidence(
    tmp_path, monkeypatch,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    publish = routing.publish_active_with_previous_guarded

    def publish_unknown(*args, **kwargs):
        publish(*args, **kwargs)
        return None

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", publish_unknown)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.committed and not result.rolled_back
    assert "exact successor endpoint" in result.error
    assert "shutdown-9281" not in events and "undrain-9281" not in events
    assert routing.read_table(tmp_path)["active"]["port"] == 9290
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "flipped"


def test_closed_transaction_guard_is_refused_before_any_cutover_effect(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    lease = CutoverLock(tmp_path)
    lease.acquire()
    lease._fh.close()
    try:
        assert not lease.held
        result = orch.run(held_lock=lease)
        assert not result.ok and "held cutover lock" in result.error
        assert events == []
    finally:
        lease.release()
    assert not lease.held


@pytest.mark.parametrize("changed_field", ["process_start_time", "version", "generation"])
def test_rollback_never_replaces_a_republished_same_pid_and_port(
    tmp_path, monkeypatch, changed_field,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    handle = orch.spawn_passive(9290)
    terminate = handle.terminate
    new_probes = iter([True, False])
    orch.health_check = lambda host, port: True if port == 9281 else next(new_probes)

    def recycle_successor_identity():
        terminate()
        table = routing.read_table(tmp_path)
        row = table["active"]
        row[changed_field] = (
            row["generation"] + 1 if changed_field == "generation"
            else "999999" if changed_field == "process_start_time" else "foreign"
        )
        routing._atomic_write(routing.routing_table_path(tmp_path), table)

    monkeypatch.setattr(handle, "terminate", recycle_successor_identity)
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.rolled_back and not result.committed
    assert "confirmed-new-exit" in events
    assert "publish-9281" not in events and "undrain-9281" not in events
    assert routing.read_table(tmp_path)["active"]["port"] == 9290
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "flipped"


def test_commit_forward_cannot_overwrite_another_published_owner(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    new_probes = 0

    def health(host, port):
        nonlocal new_probes
        if port == 9281:
            return False
        new_probes += 1
        if new_probes == 3:
            routing.publish_active(tmp_path, bind="127.0.0.1", port=9300, pid=555)
        return new_probes != 2

    orch.health_check = health
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.committed and not result.rolled_back
    assert routing.read_table(tmp_path)["active"]["port"] == 9300
    assert "terminate-new" not in events and "undrain-9281" not in events
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "flipped"


def test_same_guard_cannot_be_shared_with_another_thread(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    results = []
    with CutoverLock(tmp_path) as lease:
        thread = threading.Thread(
            target=lambda: results.append(orch.run(held_lock=lease)),
        )
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert len(results) == 1 and not results[0].ok
        assert "acquiring process and thread" in results[0].error
        assert events == [] and lease.held


@pytest.mark.parametrize("borrowed_once", [False, True])
def test_foreign_release_cannot_open_transaction_guard(tmp_path, borrowed_once):
    errors = []
    with CutoverLock(tmp_path) as lease:
        if borrowed_once:
            with lease.borrow():
                pass

        def release():
            try:
                lease.release()
            except CutoverBorrowError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=release)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert lease.held
        with pytest.raises(CutoverLockedError):
            CutoverLock(tmp_path).acquire()


def test_foreign_release_refuses_before_accessing_inherited_thread_lock(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from zdd import cutover_lock

    class InheritedLock:
        def __enter__(self):
            pytest.fail("foreign process accessed an inherited execution lock")

        def __exit__(self, *args):
            pass

    process_id = os.getpid()
    with CutoverLock(tmp_path) as lease:
        with monkeypatch.context() as patch:
            patch.setattr(cutover_lock, "os", SimpleNamespace(
                getpid=lambda: process_id + 1,
            ))
            patch.setattr(lease, "_state_lock", InheritedLock())
            with pytest.raises(CutoverBorrowError, match="another process"):
                lease.release()
        assert lease.held
        with pytest.raises(CutoverLockedError):
            CutoverLock(tmp_path).acquire()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires native fork")
def test_foreign_release_after_fork_preserves_parent_native_lock(tmp_path):
    import select
    import signal

    with CutoverLock(tmp_path) as lease:
        reader, writer = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(reader)
            try:
                try:
                    lease.release()
                except CutoverBorrowError:
                    os.write(writer, b"refused")
                else:
                    os.write(writer, b"released")
            finally:
                os._exit(0)
        os.close(writer)
        reaped = False
        try:
            ready, _, _ = select.select([reader], [], [], 3)
            assert ready, "forked release hung on an inherited execution lock"
            assert os.read(reader, 16) == b"refused"
            _, status = os.waitpid(child, 0)
            reaped = True
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
            with pytest.raises(CutoverLockedError):
                CutoverLock(tmp_path).acquire()
        finally:
            os.close(reader)
            if not reaped:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(child, 0)


def test_inherited_guard_cannot_be_borrowed_by_another_process(tmp_path, monkeypatch):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    process_id = os.getpid()
    with CutoverLock(tmp_path) as lease:
        with monkeypatch.context() as patch:
            patch.setattr(os, "getpid", lambda: process_id + 1)
            result = orch.run(held_lock=lease)
        assert not result.ok and "another process" in result.error
        assert events == [] and lease.held


def test_recycled_thread_identifier_cannot_borrow_abandoned_guard(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from zdd import cutover_lock

    acquirer = threading.Thread(name="acquirer")
    unrelated = threading.Thread(name="unrelated")
    current = acquirer
    monkeypatch.setattr(cutover_lock, "threading", SimpleNamespace(
        RLock=threading.RLock,
        get_ident=lambda: 123,
        current_thread=lambda: current,
    ))
    with CutoverLock(tmp_path) as lease:
        with lease.borrow():
            assert lease.held
        current = unrelated
        with pytest.raises(CutoverBorrowError, match="acquiring process and thread"):
            with lease.borrow():
                pytest.fail("recycled thread identifier granted writer admission")
        assert lease.held
        with pytest.raises(CutoverLockedError):
            CutoverLock(tmp_path).acquire()
        current = acquirer
        with lease.borrow():
            assert lease.held


def test_nested_borrow_and_early_release_cannot_open_the_transaction_guard(
    tmp_path, monkeypatch,
):
    orch, events, _ = orchestrator(tmp_path, monkeypatch)
    spawn = orch.spawn_passive
    with CutoverLock(tmp_path) as lease:
        def spawn_under_guard(port):
            nested = orch.run(held_lock=lease)
            assert not nested.ok and "active borrower" in nested.error
            with pytest.raises(CutoverBorrowError, match="cannot release"):
                lease.release()
            with pytest.raises(CutoverLockedError):
                CutoverLock(tmp_path).acquire()
            return spawn(port)

        orch.spawn_passive = spawn_under_guard
        assert orch.run(held_lock=lease).ok
        assert lease.held
        with lease.borrow():
            assert lease.held
        assert events.count("publish-9290") == 1
