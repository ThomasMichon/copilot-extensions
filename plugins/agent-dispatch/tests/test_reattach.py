"""Tests for agent_dispatch.reattach (Phase 9 / copilot-extensions#2884)."""

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
    requires=None,
    excludes=None,
    affinity=None,
    payload_ref=None,
    payload_inline=None,
    source=None,
    origin_ref=None,
    evaluator_ref=None,
    exclusive_key=None,
    producer_fence=None,
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
        "requires": requires or [],
        "excludes": excludes or [],
        "affinity": affinity or {},
        "payload_ref": payload_ref,
        "payload_inline": payload_inline,
        "source": source,
        "origin_ref": origin_ref,
        "evaluator_ref": evaluator_ref,
        "exclusive_key": exclusive_key,
        "producer_fence": producer_fence,
        "spawn_reservation": reservation,
        "owner": owner,
    }


class _FakeClient:
    """A fake ``DispatchClient`` covering the full create -> reserve_spawn ->
    record_spawn -> claim -> start -> bind_owner_session sequence."""

    def __init__(
        self,
        *,
        get_task=None,
        create_task=None,
        progress=None,
        steers=None,
        reserve_result=None,
        claim_result=...,
    ):
        self._get_task = get_task
        # A freshly created, unclaimed task by default -- the shape `create()`
        # returns when it does NOT collide with an existing dedup_key.
        self._create_task = create_task or {
            "id": "t-2", "status": "queued", "owner": None,
        }
        self._progress = progress or []
        self._steers = steers or []
        self._reserve_result = reserve_result or {
            "reserved": True, "reservation": {"key": "resv-1"},
        }
        # A claimed task by default; pass claim_result=None explicitly to
        # simulate a failed claim (the sentinel default distinguishes "no
        # override" from an explicit None result).
        self._claim_result_set = claim_result is not ...
        self._claim_result = None if claim_result is ... else claim_result
        self.start_calls = []
        self.bind_calls = []
        self.create_calls = []
        self.reserve_calls = []
        self.record_spawn_calls = []
        self.claim_calls = []
        self.fail_spawn_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, task_id):
        return self._get_task

    def progress_log(self, task_id):
        return self._progress

    def steer_log(self, task_id):
        return self._steers

    def create(self, title, **kwargs):
        self.create_calls.append((title, kwargs))
        return self._create_task

    def reserve_spawn(self, task_id, *, reserved_by=None):
        self.reserve_calls.append((task_id, reserved_by))
        return self._reserve_result

    def record_spawn(self, key, *, session_handle=None, worktree=None):
        self.record_spawn_calls.append((key, session_handle, worktree))
        return {"key": key, "state": "spawned"}

    def claim(self, *, worker_id=None, capabilities=(), repo=None, machine=None,
              worktree=None, task_id=None, **_kwargs):
        self.claim_calls.append(
            {"worker_id": worker_id, "capabilities": list(capabilities), "repo": repo,
             "machine": machine, "worktree": worktree, "task_id": task_id}
        )
        if self._claim_result_set:
            return self._claim_result
        return {"id": task_id, "status": "claimed", "owner": worker_id}

    def fail_spawn(self, key, *, detail=None, **_kwargs):
        self.fail_spawn_calls.append((key, detail))
        return {"state": "failed"}

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


def test_reattach_checks_validation_before_liveness_no_mutation_either_way():
    """The liveness probe is ordered right before the mutating sequence, not
    at entry -- but a non-terminal/no-dedup_key/producer-managed task is
    still rejected before ever probing liveness at all, since those checks
    are cheap and need no session I/O (#2889 review: liveness ordering)."""
    probed = []

    def _verdict(sid):
        probed.append(sid)
        return "gone"

    client = _FakeClient(get_task=_task(status="started"))
    with pytest.raises(reattach.ReattachError, match="not terminal"):
        reattach.reattach(client, "t-1", "sess-1", local_session_verdict=_verdict)
    assert probed == []  # never reached the liveness probe
    assert client.create_calls == []


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
        create_task={"id": "t-2", "status": "claimed", "owner": "someone-else"},
    )
    with pytest.raises(reattach.ReattachError, match="lost the dedup race"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        )
    # never reaches reserve_spawn once the race is lost
    assert client.reserve_calls == []


def test_reattach_not_live_never_retires_exclusive_key_or_creates():
    """A dead/unknown session must abort before ANY mutation -- including
    the exclusive_key retirement, which runs right after the liveness check
    (#2889 review: liveness ordering)."""
    task = _task(status="abandoned", exclusive_key="ex-1", session_handle="local-body:x")
    task["spawn_reservation"]["key"] = "old-resv-key"
    client = _FakeClient(get_task=task)
    with pytest.raises(reattach.ReattachError, match="not confirmed live"):
        reattach.reattach(client, "t-1", "sess-1", local_session_verdict=lambda sid: "gone")
    assert client.fail_spawn_calls == []
    assert client.create_calls == []


def test_reattach_refuses_when_producer_managed():
    client = _FakeClient(
        get_task=_task(status="abandoned", producer_fence={"producer_id": "p1"}),
    )
    with pytest.raises(reattach.ReattachError, match="producer-managed"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        )
    assert client.create_calls == []


def test_reattach_refuses_when_reservation_not_reserved():
    client = _FakeClient(
        get_task=_task(status="abandoned"),
        reserve_result={"reserved": False, "reservation": {"key": "resv-1"}},
    )
    with pytest.raises(reattach.ReattachError, match="could not reserve"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        )
    assert client.record_spawn_calls == []


def test_reattach_fails_spawn_and_refuses_when_claim_fails():
    client = _FakeClient(
        get_task=_task(status="abandoned"),
        claim_result=None,
    )
    with pytest.raises(reattach.ReattachError, match="could not claim"):
        reattach.reattach(
            client, "t-1", "sess-1", local_session_verdict=lambda sid: "live",
        )
    assert client.fail_spawn_calls == [
        ("resv-1", "reattach: claim failed after spawn reservation")
    ]
    assert client.start_calls == []


def test_reattach_happy_path_local():
    worker_id = "local-body:sess-1"
    client = _FakeClient(
        get_task=_task(
            status="abandoned",
            title="fix it",
            dedup_key="dk-9",
            requires=["cap-a"],
            excludes=["cap-b"],
            affinity={"machine": "lambda-core"},
            payload_ref="ref-1",
            payload_inline="inline-payload",
            source="issue-loop",
            origin_ref="issue/1",
            evaluator_ref="eval/1",
            exclusive_key="ex-1",
        ),
        create_task={"id": "t-2", "status": "queued", "owner": None},
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
    create_kwargs = client.create_calls[0][1]
    assert create_kwargs["dedup_key"] == "dk-9"
    assert "claim_as" not in create_kwargs  # created unclaimed -- claimed via reserve+claim
    assert create_kwargs["requires"] == ["cap-a"]
    assert create_kwargs["excludes"] == ["cap-b"]
    assert create_kwargs["affinity"] == {"machine": "lambda-core"}
    assert create_kwargs["payload_ref"] == "ref-1"
    assert create_kwargs["payload_inline"] == "inline-payload"
    assert create_kwargs["exclusive_key"] == "ex-1"
    assert create_kwargs["source"] == "issue-loop"
    assert create_kwargs["origin_ref"] == "issue/1"
    assert create_kwargs["evaluator_ref"] == "eval/1"
    # Pinned to the recovery handle itself -- unclaimable by any ordinary
    # worker, closing the create->reserve_spawn->claim race window (#2889).
    assert create_kwargs["target_worktree"] == worker_id
    assert client.reserve_calls == [("t-2", worker_id)]
    assert client.record_spawn_calls == [("resv-1", worker_id, None)]
    assert client.claim_calls == [
        {
            "worker_id": worker_id, "capabilities": ["cap-a"], "repo": "repo",
            "machine": None, "worktree": worker_id, "task_id": "t-2",
        }
    ]
    assert client.start_calls == [("t-2", worker_id)]
    assert client.bind_calls == [("t-2", worker_id, "sess-1")]
    assert resumed["session_id"] == "sess-1"
    assert resumed["host"] is None


def test_reattach_retires_source_reservation_when_exclusive_key_shared():
    """`reserve_spawn` fences an `exclusive_key` across every task sharing it;
    a terminal source task's reservation is never auto-released, so reattach
    must retire it explicitly before minting the new one (#2889 review)."""
    client = _FakeClient(
        get_task=_task(
            status="abandoned", exclusive_key="ex-1",
            session_handle="local-body:old-sess",
        ),
    )
    client._get_task["spawn_reservation"]["key"] = "old-resv-key"
    reattach.reattach(client, "t-1", "sess-1", local_session_verdict=lambda sid: "live")
    assert client.fail_spawn_calls[0][0] == "old-resv-key"
    assert "ex-1" in client.fail_spawn_calls[0][1]


def test_reattach_no_exclusive_key_never_retires_anything():
    client = _FakeClient(get_task=_task(status="abandoned", exclusive_key=None))
    reattach.reattach(client, "t-1", "sess-1", local_session_verdict=lambda sid: "live")
    assert client.fail_spawn_calls == []


def test_reattach_normalizes_fleet_host_in_handle_and_resume():
    """An un-normalized `--host` must not leak into the persisted
    session_handle/owner or the resume delivery (#2889 review)."""
    resumed = {}

    def _resume_fn(session_id, prompt, *, host=None):
        resumed.update(host=host)
        return True

    result = reattach.reattach(
        _FakeClient(get_task=_task(status="abandoned")),
        "t-1", "sess-1", host=" Pool-A ",
        fleet_session_verdict=lambda host, sid: "live",
        resume_fn=_resume_fn,
    )
    assert result.worker_id == "fleet-body:pool-a:sess-1"
    assert resumed["host"] == "pool-a"


def test_reattach_folds_prior_progress_and_steer_into_new_prompt():
    """Neither `task_progress` nor `task_steer` rows transfer through
    `create()` (they're keyed to the old task_id) -- the new task's own
    `prompt` must carry them forward instead (#2889 review)."""
    client = _FakeClient(
        get_task=_task(status="abandoned", prompt="original prompt"),
        create_task={"id": "t-2", "status": "queued", "owner": None},
        progress=[{"phase": "impl", "summary": "wired the thing", "detail": "see pr/1"}],
        steers=[{"fields": {"answer": "yes"}}],
    )
    reattach.reattach(client, "t-1", "sess-1", local_session_verdict=lambda sid: "live")
    new_prompt = client.create_calls[0][1]["prompt"]
    assert "original prompt" in new_prompt
    assert "wired the thing" in new_prompt
    assert "see pr/1" in new_prompt
    assert "yes" in new_prompt


def test_reattach_recovered_context_absent_when_no_history():
    client = _FakeClient(
        get_task=_task(status="abandoned", prompt="original prompt"),
        create_task={"id": "t-2", "status": "queued", "owner": None},
    )
    reattach.reattach(client, "t-1", "sess-1", local_session_verdict=lambda sid: "live")
    assert client.create_calls[0][1]["prompt"] == "original prompt"


def test_recovered_context_swallows_dispatch_error(monkeypatch):
    """A best-effort enrichment fetch failure must not block the reattach."""
    from agent_dispatch.client import DispatchError

    class _Client:
        def progress_log(self, task_id):
            raise DispatchError(500, "boom")

        def steer_log(self, task_id):
            raise DispatchError(500, "boom")

    assert reattach._recovered_context(_Client(), "t-1") == ""


def test_reattach_fleet_host_worker_id_and_resume():
    worker_id = "fleet-body:pool-a:sess-1"
    client = _FakeClient(
        get_task=_task(status="dead_letter"),
        create_task={"id": "t-2", "status": "queued", "owner": None},
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
        create_task={"id": "t-2", "status": "queued", "owner": None},
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
        create_task={"id": "t-2", "status": "queued", "owner": None},
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
