import threading
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace

import pytest

from agent_worktrees import runtime_monitor_demand as demand
from agent_worktrees import status_monitor_cutover as cutover


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setattr(demand.locks, "read_lock", lambda _: {
        "pid": 202, "worktree_status_endpoint": "127.0.0.1:1234",
        "worktree_status_token": "fixture",
    })
    return ("127.0.0.1", 1234, "fixture")


def test_transition_demand_renews_and_joins_before_releasing(tmp_path, monkeypatch, endpoint):
    events = []
    renewed = threading.Event()
    monkeypatch.setattr(demand, "_RENEW_INTERVAL_S", 0.001)
    monkeypatch.setattr(demand.client, "new_client_id", lambda: "owner")

    def subscribe(*args):
        assert args == (*endpoint, "owner")
        events.append("subscribe")
        if len(events) > 1:
            renewed.set()

    def release(*args):
        assert args == (*endpoint, "owner")
        assert not any(
            t.name == "runtime-monitor-transition-demand" for t in threading.enumerate()
        )
        events.append("release")

    monkeypatch.setattr(demand.client, "subscribe", subscribe)
    monkeypatch.setattr(demand.client, "release", release)
    with demand.retain_transition_demand(tmp_path, 202):
        assert renewed.wait(2)
        assert "release" not in events
    assert events[-1] == "release"


@pytest.mark.parametrize("original_error", [False, True])
def test_renewal_failure_surfaces_without_masking_original_error(
    tmp_path, monkeypatch, endpoint, original_error, capsys,
):
    failed = threading.Event()
    subscriptions = []
    releases = []
    monkeypatch.setattr(demand, "_RENEW_INTERVAL_S", 0.001)

    def subscribe(*args):
        subscriptions.append(args)
        if len(subscriptions) > 1:
            failed.set()
            raise demand.client.DaemonUnavailable("renew failed")

    monkeypatch.setattr(demand.client, "subscribe", subscribe)
    monkeypatch.setattr(demand.client, "release", lambda *args: releases.append(args))
    error = RuntimeError if original_error else demand.client.DaemonUnavailable
    with pytest.raises(error, match="original" if original_error else "renew failed"):
        with demand.retain_transition_demand(tmp_path, 202):
            assert failed.wait(2)
            if original_error:
                raise RuntimeError("original")
    assert len(releases) == 1
    if original_error:
        assert "renew failed" in capsys.readouterr().err


def test_wrong_published_pid_refuses_before_subscribing(tmp_path, monkeypatch, endpoint):
    monkeypatch.setattr(
        demand.client, "subscribe", lambda *args: pytest.fail("foreign subscription"),
    )
    with pytest.raises(demand.client.DaemonUnavailable, match="does not match"):
        with demand.retain_transition_demand(tmp_path, 303, rendezvous_timeout=0):
            pytest.fail("admitted foreign monitor")


def test_rendezvous_wait_does_not_subscribe_to_a_displaced_predecessor(
    tmp_path, monkeypatch, endpoint,
):
    records = iter([{"pid": 101}, {
        "pid": 202, "worktree_status_endpoint": "127.0.0.1:1234",
        "worktree_status_token": "fixture",
    }])
    subscriptions = []
    monkeypatch.setattr(demand.locks, "read_lock", lambda _: next(records))
    monkeypatch.setattr(demand.routing, "read_active_endpoint", lambda *a, **k: SimpleNamespace(
        pid=202,
    ))
    monkeypatch.setattr(demand.client, "subscribe", lambda *args: subscriptions.append(args))
    monkeypatch.setattr(demand.client, "release", lambda *args: None)
    with demand.retain_transition_demand(tmp_path, 202):
        assert len(subscriptions) == 1
        assert subscriptions[0][:3] == endpoint


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_drain_retains_demand_until_its_actual_owner_finishes(
    tmp_path, monkeypatch, owned, failure,
):
    events = []
    route = tmp_path / "status-monitor-routing"
    monkeypatch.setattr(cutover.routing, "read_active_endpoint", lambda *a, **k: SimpleNamespace(
        pid=202, base_url="http://127.0.0.1:2222",
    ))

    @contextmanager
    def retain(root, pid):
        assert root == tmp_path and pid == 202
        events.append("hold")
        try:
            yield
        finally:
            events.append("release")

    def request(self, action, payload=None, **kwargs):
        if action == "promote":
            events.append("promote")
            return {"adopted": True}
        assert action == "drain" and events == ["promote", "hold"]
        assert kwargs["read_timeout"] == 305
        events.append("drain")
        if failure:
            raise OSError("drain interrupted")
        return {"clean": True}

    monkeypatch.setattr(demand, "retain_transition_demand", retain)
    monkeypatch.setattr(cutover.ControlClient, "_request", request)
    with ExitStack() as custody:
        client = cutover.ControlClient(
            "http://127.0.0.1:1111", token="fixture", config_dir=route,
            transition_custody=custody if owned else None,
        )
        if failure:
            with pytest.raises(OSError, match="interrupted"):
                client.drain(timeout=300, poll=0.1, force=False)
        else:
            assert client.drain(timeout=300, poll=0.1, force=False)["clean"]
        assert ("release" not in events) == owned
        events.append("settled")
    assert events == (
        ["promote", "hold", "drain", "settled", "release"] if owned
        else ["promote", "hold", "drain", "release", "settled"]
    )
