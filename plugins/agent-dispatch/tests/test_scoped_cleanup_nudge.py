"""A paused same-owner cleanup nudge is not a failed conclusion attempt."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.queue import TaskQueue
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.supervisor_conclusion import _CONCLUSION_MAX_ATTEMPTS
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import task
from tests.test_scoped_worker_holds import command

api, q = controls.api, controls.q


@pytest.mark.parametrize("settled", [False, True])
def test_cleanup_resumes_after_more_policy_deferrals_than_retry_budget(
    q: TaskQueue, api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settled: bool,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    clock = [200.0]
    monkeypatch.setattr("agent_dispatch.supervisor.time", SimpleNamespace(time=lambda: clock[0]))
    row = task(q, labels=["cleanup"], exclusive_key="fixture-resource")
    reservation, _ = q.reserve_spawn(row.id, reserved_by="lane")
    key = reservation.key
    q.record_spawn(key, session_handle="local-body:body-a", worktree="wt-a", startup_complete=True, startup_revision=1)
    q.claim_one("host-a/wt-a", task_id=row.id)
    q.start(row.id, "host-a/wt-a", owner_session_id="native-a")
    q.complete(row.id, "host-a/wt-a")
    before_attempts = 0 if settled else 3
    prior = json.dumps({"attempts": before_attempts, "next_attempt_at": 105, "acp_session_id": "native-a"})
    if settled:
        q.settle_spawn(key, conclusion_state="pending", conclusion_detail=prior)
    else:
        q.record_spawn_conclusion(key, conclusion_state="pending", conclusion_detail=prior)
    before = q.get_reservation(key)
    assert before
    nudged: list[str] = []
    def resume(sid: str, prompt: str) -> bool:
        assert row.id in prompt and "wt-a" in prompt
        nudged.append(sid)
        return True

    lost: list[str] = []
    def lose_committed_reply(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.url.path.endswith("/conclusion") and response.is_success:
            checkpoint = json.loads(response.json().get("conclusion_detail") or "{}")
            marker = checkpoint.get("same_owner_nudge")
            if marker in {"delivered", "policy-deferred"} and marker not in lost:
                lost.append(marker)
                return httpx.Response(503, json={"detail": "lost reply after commit"})
        return response

    with controls.connected(api, [], rewrite=lose_committed_reply) as client:
        lane = Supervisor(client, supervisor_id="lane", repo="example", disposable_cli_labels=["cleanup"],
                          spawn_fn=lambda _: pytest.fail("no replacement"), local_body_verdict_fn=lambda _: "live",
                          local_body_activity_fn=lambda _: "IDLE", local_acp_session_fn=lambda _: "native-a",
                          local_resume_fn=resume, conclusion_fn=lambda *_: {"action": "skipped", "reason": "live-session"})
        if not settled:
            # Exercise the pre-settlement branch after readiness, without preconcluding.
            monkeypatch.setattr(lane, "_exclusive_terminal_release_ready", lambda *a, **kw: (True, None))
        command("hold", "--pool", "lane", "--reason", "pause pool-issued resumes")
        for _ in range(_CONCLUSION_MAX_ATTEMPTS + 2):
            lane.reconcile()
            clock[0] += 350
        after = q.get_reservation(key)
        assert after and after.conclusion_state == "pending" and not nudged
        assert after.state == before.state and after.session_handle == before.session_handle
        checkpoint = json.loads(after.conclusion_detail or "{}")
        assert (checkpoint["attempts"], checkpoint["next_attempt_at"]) == (before_attempts, 105)
        assert not after.cleanup_claim_expires_at
        command("resume", "--pool", "lane")
        lane.reconcile()
        assert nudged == ["body-a"]
        monkeypatch.setattr(lane, "conclusion_fn", lambda *_: {"action": "primed"})
        clock[0] += 350
        lane.reconcile()
        lane.reconcile()
        final = q.get_reservation(key)
        assert final and final.conclusion_state == "complete" and not final.cleanup_claim_expires_at
        assert nudged == ["body-a"]
        assert "delivered" in lost


def test_release_with_unknown_body_stays_owned_and_proven_absence_still_retires(
    q: TaskQueue, api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    row = task(q, labels=["cleanup"])
    reservation, _ = q.reserve_spawn(row.id, reserved_by="lane")
    key = reservation.key
    q.record_spawn(key, session_handle="local-body:body-a", worktree="wt-a", startup_complete=True, startup_revision=1)
    releasing = q.request_spawn_release(key, disposition="settled")
    command("hold", "--pool", "lane", "--reason", "no new cleanup resume")
    with controls.connected(api, []) as client:
        lane = Supervisor(client, supervisor_id="lane", repo="example", disposable_cli_labels=["cleanup"],
                          spawn_fn=lambda _: pytest.fail("no replacement"), local_body_verdict_fn=lambda _: "unknown",
                          local_acp_session_fn=lambda _: "native-a", local_body_target_dir_fn=lambda _: str(tmp_path / "absent"),
                          local_end_fn=lambda _: True, local_resume_fn=lambda *_: pytest.fail("no resume"))
        for _ in range(_CONCLUSION_MAX_ATTEMPTS + 1):
            assert lane.release_requested_bodies() == 0
        assert q.get_reservation(key) == releasing
        monkeypatch.setattr(lane, "local_body_verdict_fn", lambda _: "gone")
        lane.release_requested_bodies()
        retired = q.get_reservation(key)
        assert retired and retired.state == "settled" and retired.conclusion_state == "complete"
        assert lane.release_requested_bodies() == 0 and q.get_reservation(key) == retired
