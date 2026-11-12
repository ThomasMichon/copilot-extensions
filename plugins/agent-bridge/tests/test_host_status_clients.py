"""Actual HTTP shape, selected-provider rollback and enrolled inbox consumers."""

import pytest

from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.status_store import Selection, StatusStore
import test_live_inbox_runtime as inbox

installation = inbox.installation
serving = inbox.serving


def test_notice_uses_existing_enrolled_consumer_and_retains_original_epoch(serving):
    app, api, db, consumer = serving
    enrollment = inbox.enroll(api, consumer)
    selected = StatusStore(db)
    selected.subscribe("watch", Selection(
        watcher=consumer.session_id, agents=[{"session_id": "agent-a"}], events=["idle"],
    ))
    selected.publish("agent-a", "idle", "boundary-1")
    app.state.status_runtime.dispatch()
    message = inbox.poll(api, enrollment)
    assert message["kind"] == "notify"
    assert "/api/v1/status/subscriptions/watch/feed" in message["body"]
    assert message["handoff_id"]
    selected.publish("agent-a", "idle", "boundary-2")
    app.state.status_runtime.dispatch()
    assert len(db.execute_read("SELECT id FROM live_messages WHERE sender='bridge-status'")) == 1
    assert selected.page(after=0, epoch=None, subscription_id="watch")["subscription"]["acked"] == 0


@pytest.mark.parametrize("capability", [None, False, 0, 2, "1"])
def test_client_refuses_prior_unknown_and_rolled_back_provider_before_effect(capability):
    client = BridgeClient("http://127.0.0.1:12345", "synthetic")
    calls = []

    def request(method, path, *args, **kwargs):
        calls.append((method, path, kwargs))
        return {"capabilities": {"host_status_feed": capability}}

    client._request = request
    with pytest.raises(BridgeClientError) as exc:
        client.status_subscribe("watch", {"watcher": "native", "agents": [], "events": []})
    assert exc.value.status == 426
    assert len(calls) == 1 and calls[0][1] == "/health"
    assert calls[0][2]["_endpoint"] == "http://127.0.0.1:12345"


def test_client_current_provider_pins_all_mutations_and_preserves_old_health(serving):
    app, api, db, consumer = serving
    client = BridgeClient("http://127.0.0.1:12345", "synthetic")
    calls = []

    def request(method, path, body=None, *, params=None, _endpoint=None, allow_retry=True):
        calls.append((method, path, _endpoint, allow_retry))
        if path == "/health":
            # Existing health path is additive; old callers still receive it.
            return {"capabilities": {"host_status_feed": 1}}
        params = {k: v for k, v in (params or {}).items() if v is not None}
        response = api.request(method, path, json=body, params=params)
        if response.status_code >= 400:
            raise BridgeClientError(response.status_code, response.json()["detail"])
        return response.json()

    client._request = request
    client.status_subscribe("watch", {
        "watcher": consumer.session_id, "agents": [{"session_id": "agent-a"}], "events": ["idle"],
    })
    StatusStore(db).publish("agent-a", "idle", "first")
    page = client.status_feed("watch")
    assert page["events"][0]["kind"] == "idle"
    client.status_acknowledge("watch", through=page["through"], epoch=page["epoch"])
    client.status_unsubscribe("watch")
    assert not db.execute_read("SELECT id FROM bridge_status_subscriptions")
    assert all(endpoint == client._base and not retry for _, _, endpoint, retry in calls)
    assert client.health()["capabilities"]["host_status_feed"] == 1


def test_revoked_frontend_cannot_bypass_retained_notice_admission(serving):
    app, api, db, consumer = serving
    inbox.enroll(api, consumer)
    selected = StatusStore(db)
    selected.subscribe("watch", Selection(
        watcher=consumer.session_id, agents=[{"session_id": "agent-a"}], events=["idle"],
    ))
    selected.publish("agent-a", "idle", "first")
    app.state.loop_governance = None
    app.state.status_runtime.dispatch()
    assert not db.execute_read("SELECT id FROM live_messages WHERE sender='bridge-status'")
    assert selected.pending()[0]["notice_error"] == "inbox_unavailable"
