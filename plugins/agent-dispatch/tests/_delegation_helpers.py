from __future__ import annotations

from contextlib import contextmanager

import httpx
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app


def identity(queue, suffix="one"):
    return {"coordinator_id": queue.run_waiter_coordinator_id(),
            "preparation_id": suffix, "subscriber_id": "dispatch:" + suffix,
            "repo": "example/project", "number": 7, "timeout": 300.0,
            "until": ["merged", "closed"], "shared": False}


def task(queue):
    created = queue.create("wait")
    queue.claim_one("worker", task_id=created.id)
    queue.start(created.id, "worker", owner_session_id="session")
    return created.id


def prepare(queue, task_id, spec, **kwargs):
    return queue.prepare_run_waiter(
        task_id, worker_id="worker", resume_worktree="host/wt", host="host",
        command=["agent-pull-requests", "wait", "example/project", "7"],
        reason="waiting", kind="delegated", delegation=spec, **kwargs,
    )


def event(spec, registration="registration", timed_out=False):
    payload = {"event_id": "event", "registration_id": registration,
               "notification_protocol": "acknowledged_notifications/v1",
               "repo": spec["repo"], "number": spec["number"],
               "subscriber_id": spec["subscriber_id"],
               "transitions": [] if timed_out else ["merged"], "timed_out": timed_out}
    if not timed_out:
        payload.update(pr_state="closed", merged=True, review_decision="",
                       mergeable="", checks_state="")
    return payload


def client_factory(queue):
    api = TestClient(create_app(queue, enable_mcp=False))

    def transport(request):
        response = api.request(request.method, request.url.path,
                               content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content,
                              headers=dict(response.headers))

    @contextmanager
    def factory(*_args, **_kwargs):
        with DispatchClient("http://isolated", transport=httpx.MockTransport(transport)) as client:
            yield client
    return factory
