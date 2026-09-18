"""Tests for agent_dispatch.reattach (Phase 9 / aperture-labs#7133)."""

from __future__ import annotations

import json

import pytest

from agent_dispatch import reattach
from agent_dispatch.__main__ import build_parser


def _args(argv):
    return build_parser().parse_args(argv)


def _task(
    *,
    task_id="t-1",
    status="abandoned",
    title="do the thing",
    repo="repo",
    prompt="do it",
    dedup_key="dk-1",
    session_handle=None,
    owner=None,
):
    reservation = {"session_handle": session_handle} if session_handle else None
    return {
        "id": task_id,
        "status": status,
        "title": title,
        "repo": repo,
        "prompt": prompt,
        "dedup_key": dedup_key,
        "labels": [],
        "goal": None,
        "done_criteria": None,
        "target_machine": None,
        "target_worktree": None,
        "target_repo": None,
        "spawn_reservation": reservation,
        "owner": owner,
    }


class _FakeClient:
    def __init__(self, *, get_task=None, create_task=None):
        self._get_task = get_task
        self._create_task = create_task
        self.start_calls = []
        self.bind_calls = []
        self.create_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, task_id):
        return self._get_task

    def create(self, title, **kwargs):
        self.create_calls.append((title, kwargs))
        return self._create_task

    def start(self, task_id, worker_id):
        self.start_calls.append((task_id, worker_id))
        return {"id": task_id, "status": "started"}

    def bind_owner_session(self, task_id, worker_id, session_id):
        self.bind_calls.append((task_id, worker_id, session_id))
        return {"id": task_id}

    def abandon(self, task_id, *, worker_id=None, permitted=False, reason=None):
        return {"id": task_id, "status": "abandoned"}


# -- guard_abandon_liveness ---------------------------------------------


def test_guard_allows_when_no_reservation():
    task = _task(session_handle=None)
    assert reattach.guard_abandon_liveness(task) is None


def test_guard_allows_when_session_gone():
    task = _task(session_handle="local-body:sess-1")
    msg = reattach.guard_abandon_liveness(task, local_verdict=lambda sid: "gone")
    assert msg is None


def test_guard_allows_when_session_unknown():
    task = _task(session_handle="local-body:sess-1")
    msg = reattach.guard_abandon_liveness(task, local_verdict=lambda sid: "unknown")
    assert msg is None


def test_guard_blocks_when_session_confirmed_live():
    task = _task(task_id="t-9", session_handle="local-body:sess-1")
    msg = reattach.guard_abandon_liveness(task, local_verdict=lambda sid: "live")
    assert msg is not None
    assert "t-9" in msg and "sess-1" in msg


def test_guard_reports_fleet_host_when_live():
    task = _task(session_handle="fleet-body:pool-a:sess-2")
    msg = reattach.guard_abandon_liveness(task, fleet_verdict=lambda host, sid: "live")
    assert "pool-a" in msg


# -- cli_abandon -----------------------------------------------------------


def test_cli_abandon_refuses_when_live_session(monkeypatch):
    monkeypatch.setattr(reattach, "_default_local_session_verdict", lambda sid: "live")
    task = _task(task_id="t-1", session_handle="local-body:sess-1")
    client = _FakeClient(get_task=task)
    args = _args(["abandon", "t-1", "--permit"])
    with pytest.raises(reattach.AbandonRefused):
        reattach.cli_abandon(client, args)


def test_cli_abandon_override_live_proceeds(monkeypatch):
    monkeypatch.setattr(reattach, "_default_local_session_verdict", lambda sid: "live")
    task = _task(task_id="t-1", session_handle="local-body:sess-1")
    client = _FakeClient(get_task=task)
    args = _args(["abandon", "t-1", "--permit", "--override-live"])
    result = reattach.cli_abandon(client, args)
    assert result == {"id": "t-1", "status": "abandoned"}


def test_cli_abandon_duplicate_of_implies_permit():
    task = _task(task_id="t-1", session_handle=None)
    client = _FakeClient(get_task=task)
    args = _args(["abandon", "t-1", "--duplicate-of", "pr/42"])
    result = reattach.cli_abandon(client, args)
    assert result == {"id": "t-1", "status": "abandoned"}


# -- reattach() --------------------------------------------------------


def test_reattach_refuses_when_session_not_live():
    client = _FakeClient(get_task=_task())
    with pytest.raises(reattach.ReattachError, match="not confirmed live"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "gone",
        )


def test_reattach_refuses_when_task_not_terminal():
    client = _FakeClient(get_task=_task(status="started"))
    with pytest.raises(reattach.ReattachError, match="not terminal"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        )


def test_reattach_refuses_when_no_dedup_key():
    client = _FakeClient(get_task=_task(status="abandoned", dedup_key=None))
    with pytest.raises(reattach.ReattachError, match="dedup_key"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        )


def test_reattach_refuses_when_dedup_race_lost():
    client = _FakeClient(
        get_task=_task(status="abandoned"),
        create_task={"id": "t-2", "owner": "someone-else"},
    )
    with pytest.raises(reattach.ReattachError, match="lost the dedup race"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        )


def test_reattach_happy_path_local():
    worker_id = "local-body:sess-1"
    client = _FakeClient(
        get_task=_task(status="abandoned", title="fix it", dedup_key="dk-9"),
        create_task={"id": "t-2", "owner": worker_id},
    )
    resumed = {}

    def _resume_fn(session_id, prompt, *, host=None):
        resumed.update(session_id=session_id, prompt=prompt, host=host)
        return True

    result = reattach.reattach(
        client,
        "t-1",
        "sess-1",
        local_session_verdict=lambda sid: "live",
        resume_fn=_resume_fn,
    )
    assert result.task_id == "t-2"
    assert result.worker_id == worker_id
    assert result.session_id == "sess-1"
    assert result.resumed is True
    assert client.create_calls[0][1]["dedup_key"] == "dk-9"
    assert client.create_calls[0][1]["claim_as"] == worker_id
    assert client.start_calls == [("t-2", worker_id)]
    assert client.bind_calls == [("t-2", worker_id, "sess-1")]
    assert resumed["session_id"] == "sess-1"
    assert resumed["host"] is None


def test_reattach_fleet_host_worker_id_and_resume():
    worker_id = "fleet-body:pool-a:sess-1"
    client = _FakeClient(
        get_task=_task(status="dead_letter"),
        create_task={"id": "t-2", "owner": worker_id},
    )
    resumed = {}

    def _resume_fn(session_id, prompt, *, host=None):
        resumed.update(host=host)
        return True

    result = reattach.reattach(
        client,
        "t-1",
        "sess-1",
        host="pool-a",
        fleet_session_verdict=lambda host, sid: "live",
        resume_fn=_resume_fn,
    )
    assert result.worker_id == worker_id
    assert resumed["host"] == "pool-a"


def test_reattach_no_resume_skips_delivery():
    client = _FakeClient(
        get_task=_task(status="abandoned"),
        create_task={"id": "t-2", "owner": "local-body:sess-1"},
    )
    called = []

    def _resume_fn(*a, **k):
        called.append(True)
        return True

    result = reattach.reattach(
        client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        resume=False, resume_fn=_resume_fn,
    )
    assert result.resumed is False
    assert not called


# -- CLI ---------------------------------------------------------------


def test_cli_reattach_success(monkeypatch, capsys):
    worker_id = "local-body:sess-1"
    client = _FakeClient(
        get_task=_task(status="abandoned"),
        create_task={"id": "t-2", "owner": worker_id},
    )
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: client)
    monkeypatch.setattr(reattach, "_default_local_session_verdict", lambda sid: "live")

    args = _args(["reattach", "t-1", "sess-1"])
    rc = args.func(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["task_id"] == "t-2"
    assert out["worker_id"] == worker_id
    assert out["resumed"] is False  # no real agent-bridge in tests -> resume_worker fails closed


def test_cli_reattach_error_prints_and_exits_nonzero(monkeypatch, capsys):
    client = _FakeClient(get_task=_task(status="started"))
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: client)
    monkeypatch.setattr(reattach, "_default_local_session_verdict", lambda sid: "live")

    args = _args(["reattach", "t-1", "sess-1"])
    rc = args.func(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "not terminal" in err


def test_cli_abandon_override_live_flag_parses():
    args = _args(["abandon", "t1", "--permit", "--override-live"])
    assert args.override_live is True


def test_cli_reattach_host_and_no_resume_flags_parse():
    args = _args(["reattach", "t1", "s1", "--host", "pool-a", "--no-resume"])
    assert args.host == "pool-a"
    assert args.no_resume is True
