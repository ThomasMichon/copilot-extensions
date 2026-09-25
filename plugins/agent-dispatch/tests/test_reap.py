"""Tests for the superseded-coordinator reaper (:mod:`agent_dispatch.reap`)."""

from __future__ import annotations

import signal

import pytest

from agent_dispatch.reap import (
    CoordProc,
    ReapResult,
    _confirmed_gone_or_reused,
    is_coordinator_cmdline,
    is_live_coordinator_pid,
    parse_ps_output,
    parse_win_output,
    reap_abandoned_passive_backstop,
    reap_superseded_coordinators,
    select_superseded_pids,
    terminate_pid,
)

# ---- is_coordinator_cmdline: precise coordinator matching -------------------


def test_matches_module_serve_forms():
    assert is_coordinator_cmdline("/x/.venv/bin/python -m agent_dispatch serve")
    assert is_coordinator_cmdline(
        "/x/.venv/bin/python -m agent_dispatch serve --host 127.0.0.1 "
        "--port 54975 --passive"
    )


def test_matches_binstub_serve_form():
    assert is_coordinator_cmdline("/home/u/.local/bin/agent-dispatch serve")


def test_rejects_supervisor_and_scheduler_serve():
    # 'serve' appears, but not as the subcommand right after agent_dispatch.
    assert not is_coordinator_cmdline(
        "/x/python -m agent_dispatch supervise serve --legacy-env"
    )
    assert not is_coordinator_cmdline(
        "/x/python -m agent_dispatch schedule serve /path/spec.json"
    )
    assert not is_coordinator_cmdline(
        "/x/python -m agent_dispatch supervise --all-repos --label general"
    )


def test_rejects_other_subcommands_and_noise():
    assert not is_coordinator_cmdline("/x/python -m agent_dispatch _cutover")
    assert not is_coordinator_cmdline("/x/python -m agent_dispatch mcp")
    assert not is_coordinator_cmdline("/x/python -m something_else serve")
    assert not is_coordinator_cmdline("")
    # 'agent_dispatch serve' buried inside a single quoted arg is not a match.
    assert not is_coordinator_cmdline("bash -lc 'echo agent_dispatch serve'")


def test_matcher_tolerates_unparseable_quotes():
    # A stray unbalanced quote must not raise -- falls back to whitespace split.
    assert is_coordinator_cmdline("python -m agent_dispatch serve --note it's")


# ---- output parsers ---------------------------------------------------------


def test_parse_ps_output_selects_only_coordinators():
    text = (
        "  817428 /x/versions/dev159/bin/python -m agent_dispatch serve --port 44199 --passive\n"
        " 1639638 /x/.venv/bin/python -m agent_dispatch serve\n"
        " 1219949 /x/.venv/bin/python -m agent_dispatch supervise serve --legacy-env\n"
        "     999 /usr/bin/python3 -m unrelated serve\n"
        "\n"
    )
    procs = parse_ps_output(text)
    assert {p.pid for p in procs} == {817428, 1639638}


def test_parse_win_output_tab_separated():
    text = (
        "43108\tC:\\py\\python.exe -m agent_dispatch serve --port 57461 --passive\n"
        "58612\tC:\\py\\python.exe -m agent_dispatch serve\n"
        "700\tC:\\py\\python.exe -m agent_dispatch supervise serve\n"
        "garbage line without tab\n"
    )
    procs = parse_win_output(text)
    assert {p.pid for p in procs} == {43108, 58612}


# ---- select_superseded_pids: pure filtering ---------------------------------


def test_select_excludes_keep_set():
    procs = [CoordProc(1, "a"), CoordProc(2, "b"), CoordProc(3, "c")]
    assert select_superseded_pids(procs, {2}) == [1, 3]
    assert select_superseded_pids(procs, {1, 2, 3}) == []
    assert select_superseded_pids([], {2}) == []


# ---- reap_superseded_coordinators: orchestration ----------------------------


def _fixed_list(pids):
    return lambda: [CoordProc(p, f"python -m agent_dispatch serve #{p}") for p in pids]


def test_reap_terminates_all_but_kept():
    killed: list[int] = []
    res = reap_superseded_coordinators(
        keep_pids={100, 999},  # 100 = active, 999 = self
        list_procs=_fixed_list([100, 200, 300, 999]),
        terminate=lambda pid: killed.append(pid) or True,
    )
    # Candidates run concurrently now (bounded-cleanup fix), so side-effect
    # order across threads isn't guaranteed -- only the resulting sets are.
    assert sorted(killed) == [200, 300]
    assert res.reaped == [200, 300]  # result ordering is still deterministic
    assert res.ok


def test_reap_noop_when_keep_empty():
    killed: list[int] = []
    res = reap_superseded_coordinators(
        keep_pids=set(),
        list_procs=_fixed_list([1, 2, 3]),
        terminate=lambda pid: killed.append(pid) or True,
    )
    assert killed == []
    assert res.reaped == []
    assert not res.ok  # records the skip as an error, terminates nothing


def test_reap_drops_falsey_keep_but_uses_real_ones():
    killed: list[int] = []
    reap_superseded_coordinators(
        keep_pids={0, None, 100},  # 0/None are not valid pids
        list_procs=_fixed_list([100, 200]),
        terminate=lambda pid: killed.append(pid) or True,
    )
    assert killed == [200]


def test_reap_records_terminate_failures():
    res = reap_superseded_coordinators(
        keep_pids={100},
        list_procs=_fixed_list([100, 200, 300]),
        terminate=lambda pid: pid != 300,  # 300 fails
    )
    assert res.reaped == [200]
    assert not res.ok
    assert any("300" in e for e in res.errors)


def test_reap_fail_soft_on_enumeration_error():
    def _boom():
        raise RuntimeError("ps exploded")

    res = reap_superseded_coordinators(keep_pids={100}, list_procs=_boom)
    assert isinstance(res, ReapResult)
    assert res.reaped == []
    assert not res.ok
    assert any("enumeration failed" in e for e in res.errors)


# ---- terminate_pid: confirmed-death escalation (generalizes #3068) ---------

_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # SIGKILL is POSIX-only


def test_terminate_pid_confirms_death_no_escalation(monkeypatch):
    """A process that dies promptly after SIGTERM never gets SIGKILLed."""
    sent: list[int] = []
    monkeypatch.setattr("os.kill", lambda pid, sig: sent.append(sig))
    alive_calls = {"n": 0}

    def _pid_alive(pid):
        alive_calls["n"] += 1
        return alive_calls["n"] < 2  # alive once, then gone

    ok = terminate_pid(
        123,
        grace_seconds=5.0,
        poll_seconds=0.0,
        sleep=lambda _s: None,
        pid_alive=_pid_alive,
        is_windows=False,
    )
    assert ok is True
    assert sent == [signal.SIGTERM]  # never escalated


def test_terminate_pid_escalates_to_sigkill_when_stuck(monkeypatch):
    """A process that ignores SIGTERM, then dies once SIGKILLed."""
    if not hasattr(signal, "SIGKILL"):
        pytest.skip("SIGKILL is POSIX-only; no distinct escalation signal here")
    sent: list[int] = []
    sigkill_sent = {"v": False}

    def _kill(pid, sig):
        sent.append(sig)
        if sig == _SIGKILL:
            sigkill_sent["v"] = True

    monkeypatch.setattr("os.kill", _kill)

    def _pid_alive(pid):
        # Alive through the SIGTERM grace window; dies once SIGKILLed.
        return not sigkill_sent["v"]

    ok = terminate_pid(
        123,
        grace_seconds=1.0,
        poll_seconds=1.0,  # one poll tick exhausts the grace window
        kill_grace_seconds=1.0,
        sleep=lambda _s: None,
        pid_alive=_pid_alive,
        confirmed_gone_or_reused=lambda pid: False,  # still our coordinator, just stuck
        is_windows=False,
    )
    assert ok is True
    assert sent == [signal.SIGTERM, _SIGKILL]


def test_terminate_pid_never_escalates_against_a_reused_pid(monkeypatch):
    """If the pid was recycled to an unrelated process, don't SIGKILL it."""
    sent: list[int] = []
    monkeypatch.setattr("os.kill", lambda pid, sig: sent.append(sig))

    ok = terminate_pid(
        123,
        grace_seconds=1.0,
        poll_seconds=1.0,
        sleep=lambda _s: None,
        pid_alive=lambda pid: True,  # *something* alive at that number
        confirmed_gone_or_reused=lambda pid: True,  # ...but it's not our coordinator
        is_windows=False,
    )
    assert ok is True
    assert sent == [signal.SIGTERM]  # SIGKILL never sent to the stranger


def test_confirmed_gone_or_reused_treats_enumeration_failure_as_indeterminate():
    """An enumeration failure must never read as a confirmed pid reuse."""

    def _boom():
        raise RuntimeError("ps exploded")

    assert _confirmed_gone_or_reused(123, list_procs=_boom) is False


def test_terminate_pid_escalates_despite_indeterminate_enumeration(monkeypatch):
    """A transient enumeration failure must not spare a genuine straggler."""
    if not hasattr(signal, "SIGKILL"):
        pytest.skip("SIGKILL is POSIX-only; no distinct escalation signal here")
    sent: list[int] = []
    sigkill_sent = {"v": False}

    def _kill(pid, sig):
        sent.append(sig)
        if sig == _SIGKILL:
            sigkill_sent["v"] = True

    monkeypatch.setattr("os.kill", _kill)

    def _pid_alive(pid):
        return not sigkill_sent["v"]  # alive until actually SIGKILLed

    def _boom(pid):
        raise RuntimeError("ps exploded")

    ok = terminate_pid(
        123,
        grace_seconds=1.0,
        poll_seconds=1.0,
        kill_grace_seconds=1.0,
        sleep=lambda _s: None,
        pid_alive=_pid_alive,
        confirmed_gone_or_reused=lambda pid: _confirmed_gone_or_reused(pid, list_procs=_boom),
        is_windows=False,
    )
    assert ok is True
    assert sent == [signal.SIGTERM, _SIGKILL]  # escalated despite the failure


def test_terminate_pid_survives_sigkill_reports_failure(monkeypatch):
    """A process stuck in uninterruptible IO can outlive even SIGKILL."""
    sent: list[int] = []
    monkeypatch.setattr("os.kill", lambda pid, sig: sent.append(sig))

    ok = terminate_pid(
        123,
        grace_seconds=1.0,
        poll_seconds=1.0,
        kill_grace_seconds=1.0,
        sleep=lambda _s: None,
        pid_alive=lambda pid: True,  # never reports dead, even post-SIGKILL
        confirmed_gone_or_reused=lambda pid: False,
        is_windows=False,
    )
    assert ok is False
    assert sent == [signal.SIGTERM, _SIGKILL]


def test_terminate_pid_already_gone_short_circuits(monkeypatch):
    def _kill(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr("os.kill", _kill)
    assert terminate_pid(123) is True


def test_terminate_pid_returns_false_when_signal_delivery_fails(monkeypatch):
    def _kill(pid, sig):
        raise PermissionError()

    monkeypatch.setattr("os.kill", _kill)
    assert terminate_pid(123) is False


def test_terminate_pid_sigkill_failure_reports_false(monkeypatch):
    if not hasattr(signal, "SIGKILL"):
        pytest.skip("SIGKILL is POSIX-only; no distinct escalation signal here")
    calls: list[int] = []

    def _kill(pid, sig):
        calls.append(sig)
        if sig == _SIGKILL:
            raise PermissionError()

    monkeypatch.setattr("os.kill", _kill)
    ok = terminate_pid(
        123,
        grace_seconds=0.1,
        poll_seconds=0.1,
        sleep=lambda _s: None,
        pid_alive=lambda pid: True,
        confirmed_gone_or_reused=lambda pid: False,
        is_windows=False,
    )
    assert ok is False
    assert calls == [signal.SIGTERM, _SIGKILL]


def test_terminate_pid_windows_path_never_escalates(monkeypatch):
    """On Windows, os.kill already maps to TerminateProcess -- no polling/escalation."""
    sent: list[int] = []
    monkeypatch.setattr("os.kill", lambda pid, sig: sent.append(sig))
    pid_alive_calls = []
    ok = terminate_pid(
        123, pid_alive=lambda pid: pid_alive_calls.append(pid) or True,
        is_windows=True,
    )
    assert ok is True
    assert sent == [signal.SIGTERM]
    assert pid_alive_calls == []  # never even consulted on the Windows path


# ---- is_live_coordinator_pid: liveness + identity fused ---------------------


def test_is_live_coordinator_pid_true_for_enumerated_pid():
    assert is_live_coordinator_pid(200, list_procs=_fixed_list([100, 200]))


def test_is_live_coordinator_pid_false_when_absent():
    assert not is_live_coordinator_pid(999, list_procs=_fixed_list([100, 200]))


def test_is_live_coordinator_pid_fail_soft_on_enumeration_error():
    def _boom():
        raise RuntimeError("enumeration exploded")

    assert not is_live_coordinator_pid(100, list_procs=_boom)


# ---- reap_abandoned_passive_backstop (#5195) --------------------------------


def test_reap_abandoned_passive_backstop_reaps_stranded_pid(tmp_path, monkeypatch):
    from zdd import breadcrumb

    breadcrumb.write_breadcrumb(
        tmp_path, state="started", old=None, new_port=9290, new_pid=4321,
    )
    # Age the breadcrumb well past the grace window without waiting.
    import json
    from datetime import datetime, timedelta, timezone

    record = breadcrumb.read_breadcrumb(tmp_path)
    record["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=9999)
    ).isoformat()
    (tmp_path / "cutover.json").write_text(json.dumps(record), encoding="utf-8")

    from zdd import routing

    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111,
                           version="1.0")

    terminated = []
    # is_live_coordinator_pid / terminate_pid are the real (module-default)
    # implementations here; monkeypatch them to keep this test hermetic.
    import agent_dispatch.reap as reap_mod

    monkeypatch.setattr(reap_mod, "is_live_coordinator_pid", lambda pid: True)
    monkeypatch.setattr(
        reap_mod, "terminate_pid", lambda pid: terminated.append(pid) or True,
    )
    result = reap_abandoned_passive_backstop(
        tmp_path, record=record, grace_seconds=1.0,
    )
    assert result == {"reaped": True, "reason": "terminated", "pid": 4321}
    assert terminated == [4321]


def test_reap_abandoned_passive_backstop_never_touches_promoted_pid(
    tmp_path, monkeypatch,
):
    from zdd import breadcrumb, routing

    # The recorded new_pid IS the confirmed-live active -- it was promoted.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9290, pid=4321,
                           version="1.0")
    breadcrumb.write_breadcrumb(
        tmp_path, state="flipped", old=None, new_port=9290, new_pid=4321,
    )
    import json
    from datetime import datetime, timedelta, timezone

    record = breadcrumb.read_breadcrumb(tmp_path)
    record["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=9999)
    ).isoformat()
    (tmp_path / "cutover.json").write_text(json.dumps(record), encoding="utf-8")

    terminated = []
    import agent_dispatch.reap as reap_mod

    monkeypatch.setattr(reap_mod, "is_live_coordinator_pid", lambda pid: True)
    monkeypatch.setattr(
        reap_mod, "terminate_pid", lambda pid: terminated.append(pid) or True,
    )
    result = reap_abandoned_passive_backstop(
        tmp_path, record=record, grace_seconds=1.0,
    )
    assert result["reaped"] is False
    assert terminated == []


def test_reap_abandoned_passive_backstop_is_fail_soft(tmp_path):
    # No breadcrumb at all -> no-op, never raises.
    result = reap_abandoned_passive_backstop(tmp_path, record=None)
    assert result["reaped"] is False
