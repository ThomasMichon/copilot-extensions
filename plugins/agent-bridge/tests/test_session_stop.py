"""Cooperative ``agent-bridge stop``: phases, grace, ``--force`` and idempotence."""

from __future__ import annotations

import argparse
import json

import pytest

from agent_bridge import __main__ as m
from agent_bridge import session_stop
from agent_bridge.client import BridgeClientError


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Fake:
    """A session whose status evolves as the fake clock advances.

    ``timeline`` maps a time to the session record from then on; ``None`` is
    a session the bridge no longer knows."""

    def __init__(self, clock, timeline, *, queued=False, stop_error=None, withdraw_error=None):
        self.clock, self.timeline = clock, sorted(timeline.items())
        self.queued, self.stop_error, self.withdraw_error = queued, stop_error, withdraw_error
        self.calls: list = []
        self.pending: list = []
        self.stopped = False

    def get_session(self, sid):
        if self.stopped:
            return {"status": "stopped"}
        record = [rec for at, rec in self.timeline if at <= self.clock.t][-1]
        if record is None:
            raise BridgeClientError(404, "gone")
        return dict(record)

    def submit_prompt(self, sid, prompt, *, queue=False, **_kw):
        self.calls.append(("submit", prompt, queue))
        if self.queued:
            self.pending.append({"id": 9})
            return {"queued": True, "queue_id": 9, "position": 1}
        return {"turn_index": 0}

    supports_cooperative_stop = True

    def daemon_supports(self, _version):
        return self.supports_cooperative_stop

    def submit_stop_notice(self, sid, prompt):
        return self.submit_prompt(sid, prompt, queue=True)

    def list_pending_queue(self, sid):
        return [p for p in self.pending if not self._dispatched()]

    def _dispatched(self):
        record = [rec for at, rec in self.timeline if at <= self.clock.t][-1]
        return bool(record and record.get("dispatched"))

    def remove_pending_prompt(self, sid, qid):
        self.calls.append(("withdraw", qid))
        if self.withdraw_error:
            raise self.withdraw_error
        self.pending = [p for p in self.pending if p["id"] != qid]

    def stop_session(self, sid, *, force=False, reap_host=False):
        self.calls.append(("stop", force, reap_host))
        if self.stop_error:
            raise self.stop_error
        self.stopped = True


def _run(fake, clock, **kw):
    return session_stop.run_stop(fake, "s1", clock=clock, sleep=clock.sleep, poll=1.0, **kw)


def _phases(result):
    return [p["phase"] for p in result["phases"]]


def test_an_idle_agent_acknowledges_then_is_stopped():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 2},
                         1: {"status": "running", "turn_count": 3},
                         5: {"status": "idle", "turn_count": 3}})
    result = _run(fake, clock, grace=30)
    assert _phases(result) == ["requested", "acknowledged", "provider_stopped", "confirmed"]
    assert result["outcome"] == "stopped" and result["acknowledged"] is True
    assert result["notice"] == {"queued": False, "queue_id": None, "withdrawn": False}
    assert fake.calls[0] == ("submit", session_stop.STOP_NOTICE, True)
    assert fake.calls[-1] == ("stop", False, False)
    assert clock.t < 30  # stopped at the acknowledgement, not at the deadline


def test_a_busy_turn_is_never_interrupted_the_notice_waits_for_it():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 4},
                         # the running turn ends at 6; the queued notice is dispatched
                         6: {"status": "running", "turn_count": 5, "dispatched": True},
                         9: {"status": "idle", "turn_count": 5, "dispatched": True}}, queued=True)
    result = _run(fake, clock, grace=60)
    assert result["acknowledged"] is True and result["notice"]["queued"] is True
    assert ("stop", False, False) in fake.calls
    stop_at = fake.calls.index(("stop", False, False))
    assert stop_at == len(fake.calls) - 1 and clock.t >= 9  # only after the boundary


def test_idle_before_the_queued_notice_ran_is_not_an_acknowledgement():
    """The running turn ended but the notice is still queued: not acknowledged."""
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 4},
                         3: {"status": "idle", "turn_count": 4}}, queued=True)
    result = _run(fake, clock, grace=10)
    assert result["acknowledged"] is False


def test_another_prompt_queued_ahead_running_is_not_an_acknowledgement():
    """A turn ran (turn_count moved) and the session settled, but it was an
    earlier queued prompt: the notice is still pending."""
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 4},
                         3: {"status": "idle", "turn_count": 5}}, queued=True)
    result = _run(fake, clock, grace=10)
    assert result["acknowledged"] is False and result["notice"]["withdrawn"] is True


def test_an_agent_that_ignores_the_notice_is_stopped_after_the_grace_and_the_notice_withdrawn():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 1}}, queued=True)
    result = _run(fake, clock, grace=10)
    assert result["outcome"] == "stopped" and result["acknowledged"] is False
    assert result["notice"]["withdrawn"] is True and ("withdraw", 9) in fake.calls
    assert _phases(result) == ["requested", "provider_stopped", "confirmed"]
    assert clock.t >= 10


def test_a_session_that_disappears_during_the_grace_is_still_stopped_cleanly():
    """The queue snapshot 404s before the status read sees the session gone."""
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 1}, 2: None}, queued=True,
                 withdraw_error=BridgeClientError(404, "gone"))

    def queue(sid):
        if clock.t >= 2:
            raise BridgeClientError(404, f"Session {sid} not found")
        return list(fake.pending)

    fake.list_pending_queue = queue
    fake.stop_error = BridgeClientError(404, "gone")
    result = _run(fake, clock, grace=60)
    assert result["outcome"] == "stopped" and result["acknowledged"] is False
    assert clock.t < 60  # it didn't wait out the grace for a session that is gone


def test_a_notice_dispatched_just_before_withdrawal_is_tolerated():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 1}}, queued=True,
                 withdraw_error=BridgeClientError(404, "gone"))
    result = _run(fake, clock, grace=5)
    assert result["outcome"] == "stopped" and result["notice"]["withdrawn"] is False


def test_an_archived_read_only_record_is_already_stopped():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "ended", "read_only": True}})
    result = _run(fake, clock, grace=30)
    assert result["outcome"] == "already_stopped" and fake.calls == []


def test_a_session_archived_after_the_stop_is_confirmed_promptly():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 0}, 1: {"status": "ended", "read_only": True}})

    def stop(sid, **_kw):
        clock.t = 1  # the bridge archives the session as it stops
        raise BridgeClientError(404, "gone")

    fake.stop_session = stop
    result = _run(fake, clock, confirm_timeout=30)
    assert result["outcome"] == "stopped" and clock.t < 5


def test_grace_against_a_daemon_without_cooperative_stop_refuses_before_any_notice():
    """An older daemon may hand the notice to a successor without naming it, or
    resume a just-stopped session: neither can be detected, so --grace refuses."""
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 1}})
    fake.supports_cooperative_stop = False
    result = _run(fake, clock, grace=30)
    assert result["outcome"] == "refused_unsupported" and "restart it" in result["error"]
    assert fake.calls == []  # neither a notice nor a stop


def test_a_concurrent_stop_makes_the_notice_a_no_op_not_a_resume():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 1}})

    def refused(sid, prompt):
        fake.calls.append(("submit", prompt, True))
        raise BridgeClientError(409, f"{session_stop.SESSION_STOPPED}: Session {sid} is stopped")

    fake.submit_stop_notice = refused
    result = _run(fake, clock, grace=30)
    assert result["outcome"] == "already_stopped" and [c[0] for c in fake.calls] == ["submit"]


def test_the_cli_exits_69_when_cooperative_stop_is_unsupported(monkeypatch, capsys):
    from agent_bridge import session_lifecycle_cli as lc

    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 1}})
    fake.supports_cooperative_stop = False
    with pytest.raises(SystemExit) as exc:
        lc._cmd_stop_phased(fake, argparse.Namespace(session_id="s1", grace=5.0, force=False,
                                                      reap_host=False, json=True))
    assert exc.value.code == session_stop.STOP_UNSUPPORTED_EXIT == 69
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused_unsupported"


def test_force_skips_the_notice_and_the_grace():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 1}})
    result = _run(fake, clock, grace=60, force=True)
    assert [c[0] for c in fake.calls] == ["stop"] and fake.calls[0] == ("stop", True, False)
    assert result["notice"] is None and result["acknowledged"] is None and clock.t == 0


def test_a_fractional_grace_is_never_overslept():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 1}}, queued=True)
    _run(fake, clock, grace=0.25)
    assert clock.t <= 0.25 + 1e-9  # a 1-second poll is capped to what's left


def test_a_zero_grace_still_sends_the_notice():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 1}}, queued=True)
    result = _run(fake, clock, grace=0)
    assert fake.calls[0] == ("submit", session_stop.STOP_NOTICE, True)
    assert result["acknowledged"] is False and result["notice"]["withdrawn"] is True
    assert result["outcome"] == "stopped"


def test_idle_in_the_gap_between_dequeue_and_turn_start_is_not_an_acknowledgement():
    """The notice is popped before its turn is marked running: a read in that gap
    sees an idle session (an earlier prompt moved turn_count) and no queued
    notice. Only a later poll that finds the notice's turn settled counts."""
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "running", "turn_count": 4},
                         3: {"status": "idle", "turn_count": 5, "dispatched": True},  # the gap
                         4: {"status": "running", "turn_count": 6, "dispatched": True},
                         8: {"status": "idle", "turn_count": 6, "dispatched": True}}, queued=True)
    result = _run(fake, clock, grace=60)
    assert result["acknowledged"] is True and clock.t >= 8


@pytest.mark.parametrize("record", [None, {"status": "stopped"}])
def test_repeating_a_stop_is_an_idempotent_no_op(record):
    clock = _Clock()
    fake = _Fake(clock, {0: record})
    result = _run(fake, clock, grace=60)
    assert result["outcome"] == "already_stopped" and _phases(result) == ["confirmed"]
    assert fake.calls == []


def test_a_background_task_blocks_a_non_forced_stop():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 0}},
                 stop_error=BridgeClientError(409, "background tasks are running"))
    result = _run(fake, clock)
    assert result["outcome"] == "refused_busy" and "background" in result["error"]


def test_without_grace_no_notice_is_sent():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 0}})
    result = _run(fake, clock)
    assert [c[0] for c in fake.calls] == ["stop"]
    assert _phases(result) == ["requested", "provider_stopped", "confirmed"]


def test_a_stop_that_never_takes_is_unconfirmed():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 0}})
    fake.stop_session = lambda sid, **kw: None  # the provider stop is a no-op
    result = _run(fake, clock, confirm_timeout=5)
    assert result["outcome"] == "unconfirmed"


# -- the CLI -----------------------------------------------------------------


def _cli(monkeypatch, capsys, fake, argv):
    monkeypatch.setattr(m, "_get_client", lambda: fake)
    monkeypatch.setattr(session_stop.time, "sleep", fake.clock.sleep)
    monkeypatch.setattr(session_stop.time, "monotonic", fake.clock)
    args = m.build_parser().parse_args(argv)
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return code, capsys.readouterr()


def test_cli_json_prints_one_phased_document(monkeypatch, capsys):
    fake = _Fake(_Clock(), {0: {"status": "idle", "turn_count": 0}})
    code, out = _cli(monkeypatch, capsys, fake, ["--json", "stop", "s1"])
    doc = json.loads(out.out)
    assert code == 0 and doc["outcome"] == "stopped" and _phases(doc)[-1] == "confirmed"


def test_cli_busy_stop_exits_75(monkeypatch, capsys):
    fake = _Fake(_Clock(), {0: {"status": "idle", "turn_count": 0}},
                 stop_error=BridgeClientError(409, "background tasks are running"))
    code, out = _cli(monkeypatch, capsys, fake, ["stop", "s1", "--grace", "0"])
    assert code == 75 and "background tasks" in out.err


def test_cli_grace_reports_phases_in_text(monkeypatch, capsys):
    fake = _Fake(_Clock(), {0: {"status": "running", "turn_count": 1}}, queued=True)
    code, out = _cli(monkeypatch, capsys, fake, ["stop", "s1", "--grace", "3"])
    assert code == 0
    assert "requested" in out.out and "stopped anyway" in out.out and "[OK] Session s1 stopped" in out.out


@pytest.mark.parametrize("grace", ["nan", "inf", "-1", "soon"])
def test_cli_rejects_a_grace_that_could_never_time_out(grace, capsys):
    with pytest.raises(SystemExit) as exc:
        m.build_parser().parse_args(["stop", "s1", "--grace", grace])
    assert exc.value.code == 2


def test_a_session_gone_before_the_notice_is_still_stopped():
    clock = _Clock()
    fake = _Fake(clock, {0: {"status": "idle", "turn_count": 0}, 1: None},
                 stop_error=BridgeClientError(404, "gone"))

    def gone(sid, prompt, **_kw):
        clock.t = 1
        raise BridgeClientError(404, f"Session {sid} not found")

    fake.submit_prompt = gone
    result = _run(fake, clock, grace=30)
    assert result["outcome"] == "stopped" and result["acknowledged"] is False


def test_plain_stop_is_unchanged(monkeypatch, capsys):
    calls = []

    class Bare:  # the legacy path needs only stop_session
        def stop_session(self, sid, *, force=False, reap_host=False):
            calls.append((sid, force, reap_host))

    monkeypatch.setattr(m, "_get_client", lambda: Bare())
    m._cmd_stop(argparse.Namespace(session_id="abc", force=False, reap_host=False))
    assert calls == [("abc", False, False)] and capsys.readouterr().out == "[OK] Session abc stopped\n"
