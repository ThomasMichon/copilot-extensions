"""Selected legacy reconciliation preserves unverified history and preview custody."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError
from agent_dispatch.queue_pool_placement import pool_placement_contract
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_fence_legacy_history import ATTESTED, _accountable, _legacy


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def test_selected_apply_preserves_an_unverified_eligible_neighbor(queue):
    _, selected = _legacy(queue, "verified", finish="fail")
    _, untouched = _legacy(queue, "unverified", finish="fail")
    preview = queue.fence_legacy_history(before=2000, reservation_keys=[selected.key])
    assert preview["eligible"] == 1 and preview["sample"] == [selected.key]
    assert _accountable(queue, selected.key) and _accountable(queue, untouched.key)
    _, later = _legacy(queue, "later unverified", finish="fail", now=900.0)
    result = queue.fence_legacy_history(
        before=2000.0, reservation_keys=[selected.key],
        expected_preview=preview["selection"]["revision"], apply=True, **ATTESTED,
    )
    assert result["fenced"] == 1
    assert not _accountable(queue, selected.key) and _accountable(queue, untouched.key)
    assert _accountable(queue, later.key)
    assert not queue.get_reservation(untouched.key).startup_fenced
    with pytest.raises(TaskError, match="preview changed"):
        queue.fence_legacy_history(
            before=2000.0, reservation_keys=[selected.key],
            expected_preview=preview["selection"]["revision"], apply=True, **ATTESTED,
        )


def test_unselected_live_work_still_protects_the_selected_native(queue):
    _, selected = _legacy(queue, "selected", handle="shared-native")
    live = queue.create("live")
    active, _ = queue.reserve_spawn(live.id, now=1000.0)
    queue.record_spawn(active.key, session_handle="shared-native", now=1000.0)
    preview = queue.fence_legacy_history(before=2000.0, reservation_keys=[selected.key])
    assert preview["eligible"] == 0
    assert preview["excluded"] == {"shared_live_native": 1}
    result = queue.fence_legacy_history(
        before=2000.0, reservation_keys=[selected.key],
        expected_preview=preview["selection"]["revision"], apply=True, **ATTESTED,
    )
    assert result["fenced"] == 0 and _accountable(queue, selected.key)


def test_shared_native_change_invalidates_an_unchanged_selected_row(queue):
    _, selected = _legacy(queue, "selected", handle="shared-native")
    preview = queue.fence_legacy_history(before=2000.0, reservation_keys=[selected.key])
    live = queue.create("new live holder")
    active, _ = queue.reserve_spawn(live.id, now=1000.0)
    queue.record_spawn(active.key, session_handle="shared-native", now=1000.0)
    with pytest.raises(TaskError, match="preview changed"):
        queue.fence_legacy_history(
            before=2000.0, reservation_keys=[selected.key],
            expected_preview=preview["selection"]["revision"], apply=True, **ATTESTED,
        )
    assert not queue.get_reservation(selected.key).startup_fenced


def test_selected_startup_revision_change_refuses_the_original_preview(queue):
    _, selected = _legacy(queue, "selected")
    preview = queue.fence_legacy_history(before=2000.0, reservation_keys=[selected.key])
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET startup_revision=startup_revision+1 WHERE key=?",
            (selected.key,),
        )
    with pytest.raises(TaskError, match="preview changed"):
        queue.fence_legacy_history(
            before=2000.0, reservation_keys=[selected.key],
            expected_preview=preview["selection"]["revision"], apply=True, **ATTESTED,
        )
    assert not queue.get_reservation(selected.key).startup_fenced


@pytest.mark.parametrize("selection", [[], ["missing"], ["duplicate", "duplicate"], [" "]])
def test_invalid_or_missing_selection_cannot_widen_to_all_history(queue, selection):
    _, retained = _legacy(queue, "retained")
    with pytest.raises(TaskError):
        queue.fence_legacy_history(before=2000.0, reservation_keys=selection)
    assert _accountable(queue, retained.key)


def test_selected_apply_requires_its_preview_and_original_attestation(queue):
    _, selected = _legacy(queue, "selected")
    with pytest.raises(TaskError, match="expected preview"):
        queue.fence_legacy_history(
            before=2000.0, reservation_keys=[selected.key], apply=True, **ATTESTED,
        )
    preview = queue.fence_legacy_history(before=2000.0, reservation_keys=[selected.key])
    with pytest.raises(TaskError, match="attestation"):
        queue.fence_legacy_history(
            before=2000.0, reservation_keys=[selected.key],
            expected_preview=preview["selection"]["revision"], apply=True,
        )
    assert _accountable(queue, selected.key)


def test_selected_client_and_http_preserve_scope_control_and_cas(queue, monkeypatch):
    monkeypatch.setattr("agent_dispatch.acquisition_receipts.verify_recovery_bridge", lambda: None)
    _, selected = _legacy(queue, "selected")
    _, retained = _legacy(queue, "retained")
    api = TestClient(create_app(queue, token="worker-test", control_token="control-test"))

    def forward(request: httpx.Request) -> httpx.Response:
        response = api.request(
            request.method, request.url.path, content=request.content, headers=request.headers,
        )
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    with DispatchClient(
        "http://example.test", token="worker-test", control_token="control-test",
        transport=httpx.MockTransport(forward),
    ) as client:
        preview = client.fence_legacy_history(before=2000.0, reservation_keys=[selected.key])
        result = client.fence_legacy_history(
            before=2000.0, reservation_keys=[selected.key],
            expected_preview=preview["selection"]["revision"], apply=True, **ATTESTED,
        )
    assert result["fenced"] == 1 and _accountable(queue, retained.key)
    response = api.post(
        "/spawn-reservations/history/fence",
        json={"before": 2000.0, "reservation_keys": [retained.key]},
    )
    assert response.status_code in (401, 403)


def test_selected_client_refuses_an_old_provider_before_posting():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        contract = pool_placement_contract()
        contract.pop("selected_legacy_history_versions")
        return httpx.Response(200, json=contract)

    with DispatchClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError, match="selected-legacy-history"):
            client.fence_legacy_history(before=2000.0, reservation_keys=["selected"])
    assert seen and all(method == "GET" for method, _ in seen)


def test_selected_client_rejects_a_provider_that_drops_the_selection():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=pool_placement_contract())
        assert json.loads(request.content)["reservation_keys"] == ["selected"]
        return httpx.Response(200, json={"applied": False, "sample": ["unselected"]})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError, match="preserve"):
            client.fence_legacy_history(before=2000.0, reservation_keys=["selected"])


@pytest.mark.parametrize("malformed", ["missing_selection", "invalid_sample"])
def test_malformed_apply_response_preserves_the_unknown_committed_outcome(queue, monkeypatch, malformed):
    monkeypatch.setattr("agent_dispatch.acquisition_receipts.verify_recovery_bridge", lambda: None)
    _, selected = _legacy(queue, "selected")
    _, retained = _legacy(queue, "retained")
    api = TestClient(create_app(queue, token="worker-test", control_token="control-test"))
    apply_posts = []

    def forward(request: httpx.Request) -> httpx.Response:
        response = api.request(
            request.method, request.url.path, content=request.content, headers=request.headers,
        )
        body = response.json()
        if request.method == "POST" and json.loads(request.content).get("apply"):
            apply_posts.append(request.url.path)
            assert response.status_code == 200 and body["fenced"] == 1
            if malformed == "missing_selection":
                body.pop("selection")
            else:
                body["sample"] = None
        return httpx.Response(response.status_code, json=body)

    with DispatchClient(
        "http://example.test", token="worker-test", control_token="control-test",
        transport=httpx.MockTransport(forward),
    ) as client:
        preview = client.fence_legacy_history(before=2000.0, reservation_keys=[selected.key])
        with pytest.raises(DispatchError, match="outcome is unknown") as failure:
            client.fence_legacy_history(
                before=2000.0, reservation_keys=[selected.key],
                expected_preview=preview["selection"]["revision"], apply=True, **ATTESTED,
            )
    assert failure.value.status_code == 502
    assert apply_posts == ["/spawn-reservations/history/fence"]
    assert not _accountable(queue, selected.key) and _accountable(queue, retained.key)


def test_cli_carries_the_selected_keys_and_preview_to_its_original_client(monkeypatch, capsys):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from agent_dispatch import producers_cli
    from agent_dispatch.__main__ import build_parser

    seen = {}

    def fence(**kwargs):
        seen.update(kwargs)
        return {"applied": True}

    monkeypatch.setattr(
        producers_cli, "_client", lambda _args: nullcontext(SimpleNamespace(fence_legacy_history=fence)),
    )
    args = build_parser().parse_args([
        "reservations", "fence-history", "--before", "2000",
        "--reservation-key", "second", "--reservation-key", "first",
        "--expected-preview", "a" * 64, "--apply", "--permit",
        "--confirm-no-live-holder", "--reason", "verified subset",
    ])
    assert producers_cli._cmd_reservations(args) == 0
    assert seen == {
        "before": 2000.0, "apply": True, "permitted": True,
        "confirmed_no_live_holder": True, "reason": "verified subset",
        "reservation_keys": ["second", "first"], "expected_preview": "a" * 64,
    }
    assert json.loads(capsys.readouterr().out) == {"applied": True}
