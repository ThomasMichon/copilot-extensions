"""Tests for agent-bridge integration (spawn worker) and claim-by-id."""

from __future__ import annotations

import subprocess

import pytest

from agent_dispatch import bridge
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue

# -- claim by id -------------------------------------------------------------


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def test_claim_specific_task_by_id(q):
    a = q.create("a")
    b = q.create("b")
    # claim the *second*, older-ordering notwithstanding
    got = q.claim_one("w1", task_id=b.id)
    assert got is not None and got.id == b.id
    # a is still queued
    assert q.get(a.id).status == Status.QUEUED


def test_claim_by_id_respects_eligibility(q):
    t = q.create("needs-cap", requires=["review"])
    assert q.claim_one("w1", task_id=t.id) is None  # lacks capability
    assert q.claim_one("w1", ["review"], task_id=t.id).id == t.id


def test_claim_by_id_missing_returns_none(q):
    assert q.claim_one("w1", task_id="does-not-exist") is None


def test_claim_by_id_already_claimed_returns_none(q):
    t = q.create("x")
    q.claim_one("w1", task_id=t.id)
    assert q.claim_one("w2", task_id=t.id) is None  # no longer queued


# -- bridge spawn ------------------------------------------------------------


def test_worker_prompt_mentions_task_and_verbs():
    prompt = bridge.worker_prompt("abc123", coordinator_url="http://c", worker_id="w9")
    assert "abc123" in prompt
    assert "w9" in prompt
    assert "http://c" in prompt
    assert "agent-dispatch claim w9 --task abc123" in prompt


def test_spawn_worker_unavailable_when_no_bridge(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)
    assert bridge.bridge_available() is False
    with pytest.raises(bridge.BridgeUnavailable):
        bridge.spawn_worker("t1", coordinator_url="http://c", worker_id="w1")


def test_spawn_worker_invokes_agent_bridge_create(monkeypatch):
    calls = {}

    def fake_which(name):
        return "/usr/bin/agent-bridge" if name == "agent-bridge" else None

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(bridge.shutil, "which", fake_which)
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    result = bridge.spawn_worker(
        "task42", agent="task-worker", coordinator_url="http://c", worker_id="w1", wait=False
    )
    assert result.returncode == 0
    cmd = calls["cmd"]
    assert cmd[:3] == ["/usr/bin/agent-bridge", "create", "task-worker"]
    assert "task42" in cmd[3]  # the prompt carries the task id
    assert cmd[-1] == "--no-wait"  # wait=False -> --no-wait


def test_spawn_worker_wait_omits_no_wait(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda _n: "/usr/bin/agent-bridge")
    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    result = bridge.spawn_worker("t", coordinator_url="http://c", worker_id="w", wait=True)
    assert result.returncode == 0
