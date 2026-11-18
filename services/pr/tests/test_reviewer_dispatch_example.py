"""Cache-backed inert consumer with the Dispatch read/preparation wire shape."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from test_reviewer_feed_example import SERVICE, example, load, selection

load("reviewer_feed", SERVICE / "examples/reviewer_feed.py")
adapter = load("reviewer_dispatch_example", SERVICE / "examples/reviewer_dispatch.py")
LANE = "github.com/example/control"
KEY = "recipe:reviewer:target=forge.example/owner/repository#7"


class Client:
    def __init__(self, scope: Any) -> None:
        self.task = {
            "id": "task-a", "repo": LANE, "dedup_key": KEY,
            "draft_scope": dict(zip(("queue", "installation", "machine"), scope.task_scope)),
            "steering_revision": "revision-a", "status": "suspended", "kind": "finite",
            "hold_reason": None, "awaiting_steer": False,
        }
        self.rows = [deepcopy(self.task)]
        self.reads: list[tuple] = []
        self.prepared: list[dict] = []

    def lookup_dedup(self, key: str, *, repo: str) -> list[dict]:
        self.reads.append(("lookup", key, repo))
        return deepcopy(self.rows)

    def get(self, task_id: str) -> dict:
        self.reads.append(("get", task_id))
        return deepcopy(self.task)

    def prepare_steer(self, task_id: str, request_id: str, **kwargs: Any) -> dict:
        request = {"task_id": task_id, "request_id": request_id, **kwargs}
        self.prepared.append(deepcopy(request))
        return request


def propose(page: dict, scope: Any, client: Client, **kwargs: Any) -> Any:
    return example.propose(
        page, scope, lookup=lambda action: adapter.retained_task(
            action, scope, client=client, lane=LANE, task_key=lambda _number: KEY,
        ), notifications={}, now=1000, **kwargs,
    )


def test_cache_selection_uses_exact_lane_and_current_native_task_detail(tmp_path: Any) -> None:
    selected, scope, page = selection(tmp_path)
    before = selected[1].path.read_bytes()
    client = Client(scope)
    proposal = propose(page, scope, client)
    assert proposal.kind == "steer" and proposal.task_id == "task-a"
    assert client.reads == [("lookup", KEY, LANE), ("get", "task-a")]
    prepared = adapter.prepare_notification(
        proposal, scope, client=client, request_id="original-request",
        fields={"action": proposal.action_key}, sender="consumer", message="Review updated activity",
        lane=LANE, task_key=lambda _number: KEY,
    )
    assert prepared == {
        "task_id": "task-a", "request_id": "original-request",
        "expected_scope": client.task["draft_scope"], "fields": {"action": proposal.action_key},
        "sender": "consumer", "wake": True, "message": "Review updated activity",
        "expected_status": "suspended", "expected_revision": "revision-a",
    }
    assert selected[1].path.read_bytes() == before


def test_empty_ambiguous_foreign_and_unavailable_lookup_never_propose_creation(tmp_path: Any) -> None:
    _selected, scope, page = selection(tmp_path)
    for changed in (
        [], [Client(scope).task, Client(scope).task],
        [Client(scope).task | {"repo": "other-lane"}],
        [Client(scope).task | {"dedup_key": "other-key"}],
        [Client(scope).task | {"draft_scope": {}}],
    ):
        client = Client(scope)
        client.rows = changed
        assert propose(page, scope, client).kind == "reconcile"
        assert client.reads == [("lookup", KEY, LANE)] and not client.prepared

    class Failed(Client):
        def lookup_dedup(self, key: str, *, repo: str) -> list[dict]:
            raise TimeoutError("lookup unavailable")

    with pytest.raises(TimeoutError, match="unavailable"):
        propose(page, scope, Failed(scope))


def test_changed_detail_hold_form_and_terminal_task_preserve_owner(tmp_path: Any) -> None:
    _selected, scope, page = selection(tmp_path)
    for change, expected in (
        ({"dedup_key": "replacement"}, "reconcile"),
        ({"draft_scope": {"queue": "another"}}, "reconcile"),
        ({"kind": "standing-charter"}, "reconcile"),
        ({"hold_reason": "operator hold"}, "noop"),
        ({"awaiting_steer": True}, "noop"),
        ({"status": "completed"}, "noop"),
        ({"status": "queued"}, "noop"),
    ):
        client = Client(scope)
        client.task.update(change)
        assert propose(page, scope, client).kind == expected and not client.prepared


def test_prepare_refuses_task_changes_and_keeps_unknown_native_result(tmp_path: Any) -> None:
    _selected, scope, page = selection(tmp_path)
    client = Client(scope)
    proposal = propose(page, scope, client)
    for change in (
        {"steering_revision": "revision-b"}, {"status": "completed"},
        {"awaiting_steer": True}, {"hold_reason": "operator hold"},
        {"draft_scope": {}}, {"repo": "other-lane"}, {"dedup_key": "replacement"},
    ):
        changed = Client(scope)
        changed.task.update(change)
        with pytest.raises(ValueError, match="changed or is held"):
            adapter.prepare_notification(
                proposal, scope, client=changed, request_id="original-request",
                fields={}, sender=None, message="Review activity",
                lane=LANE, task_key=lambda _number: KEY,
            )
        assert not changed.prepared
    with pytest.raises(ValueError, match="proposal"):
        adapter.prepare_notification(
            replace(proposal, kind="noop"), scope, client=client,
            request_id="original-request", fields={}, sender=None, message="Review activity",
            lane=LANE, task_key=lambda _number: KEY,
        )

    class Refused(Client):
        def prepare_steer(self, task_id: str, request_id: str, **kwargs: Any) -> dict:
            raise ValueError("native preparation refused")

    with pytest.raises(ValueError, match="native preparation refused"):
        adapter.prepare_notification(
            proposal, scope, client=Refused(scope), request_id="original-request",
            fields={}, sender=None, message="Review activity",
            lane=LANE, task_key=lambda _number: KEY,
        )


def test_native_preparation_preserves_original_request_without_submission(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(SERVICE.parents[1] / "plugins/agent-dispatch/src"))
    from agent_dispatch.client_errors import DispatchUpgradeRequired
    from agent_dispatch.client_steering_requests import PreparedSteeringClientMixin
    from agent_dispatch.steering_requests import validate_prepared_request

    class Native(PreparedSteeringClientMixin, Client):
        support = 1

        def health(self) -> dict:
            return {"steering_request_context": self.support}

    selected, scope, page = selection(tmp_path)
    before = selected[1].path.read_bytes()
    client = Native(scope)
    proposal = propose(page, scope, client)
    fields = {"action": proposal.action_key}
    request = adapter.prepare_notification(
        proposal, scope, client=client, request_id="original-request",
        fields=fields, sender="consumer", message="Review activity",
        lane=LANE, task_key=lambda _number: KEY,
    )
    fields["action"] = "later-caller-edit"
    assert validate_prepared_request(request) == request
    assert request["fields"] == {"action": proposal.action_key}
    assert request["request_id"] == "original-request"
    assert request["scope"] == client.task["draft_scope"]
    assert request["expected_revision"] == proposal.revision
    assert not client.prepared and selected[1].path.read_bytes() == before
    client.support = 0
    with pytest.raises(DispatchUpgradeRequired, match="scope-bound"):
        adapter.prepare_notification(
            proposal, scope, client=client, request_id="unsubmitted-request",
            fields={}, sender=None, message="Review activity",
            lane=LANE, task_key=lambda _number: KEY,
        )
