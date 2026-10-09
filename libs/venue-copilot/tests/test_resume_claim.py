"""venue_copilot.resume_claim: a resumed session keeps its claim and venue."""

from __future__ import annotations

import types

import pytest
import venue_copilot
from venue_copilot.resume_claim import resume_target, settle_resumed_claim

_VENUE = {"kind": "codespace", "target": "cs-1", "mux_session_name": "wt-a",
          "supervisor_ref": "host/proj/wt-1"}


@pytest.mark.parametrize("args, want", [
    (["--resume=sid-9"], "sid-9"),
    (["--model=m", "--resume", "sid-9"], "sid-9"),
    (["-r", "sid-9"], "sid-9"),
    (["-r"], None),
    (["--resume", "--model=m"], None),
    (["--continue"], None),
    ([], None),
])
def test_resume_target_names_only_an_explicit_id(args, want):
    assert resume_target(args) == want


@pytest.fixture
def bridge(monkeypatch):
    """A fake host bridge: one reservation row and the live registrations."""
    b = types.SimpleNamespace(
        row={"reservation_id": "r1", "claimed_by_session_id": "placeholder"},
        live={}, released=[], reserved=[], reserve_error=False, renewal_claimant="sid-9", fail_reads=0,
        fail_lookups=False,
    )

    def _reserve(scope, ttl_seconds, venue):
        if b.reserve_error:
            raise venue_copilot.VenueCopilotError("reservation_active")
        b.reserved.append((scope, ttl_seconds, venue))
        b.row = {"reservation_id": "r2", "claimed_by_session_id": None}
        return {"reservation_id": "r2"}

    def _get(scope):
        if b.fail_reads:
            b.fail_reads -= 1
            raise venue_copilot.VenueCopilotError("bridge timed out")
        if b.row.get("reservation_id") == "r2" and b.renewal_claimant:
            b.row["claimed_by_session_id"] = b.renewal_claimant  # its next heartbeat
        return dict(b.row)

    monkeypatch.setattr(venue_copilot, "get_cli_mode_reservation", _get)
    def _lookup(h, strict=False):
        if b.fail_lookups:
            if strict:
                raise venue_copilot.VenueCopilotError("bridge timed out")
            return {}
        return b.live.get(h, {})

    monkeypatch.setattr(venue_copilot, "live_session_for", _lookup)
    monkeypatch.setattr(venue_copilot, "reserve_cli_mode", _reserve)
    monkeypatch.setattr(venue_copilot, "release_cli_mode",
                        lambda scope, reservation_id=None: b.released.append(reservation_id) or 1)
    monkeypatch.setattr(venue_copilot.time, "sleep", lambda s: None)
    return b


def _settle(expected="sid-9", claimed="placeholder", timeout=30.0):
    ticks = iter(range(10_000))
    return settle_resumed_claim(
        "wt-a", {"reservation_id": "r1"}, _VENUE, expected=expected, claimed=claimed,
        timeout=timeout, ttl_seconds=900.0, sleep=lambda s: None, clock=lambda: float(next(ticks)),
    )


def test_nothing_to_settle_without_a_named_resume_or_once_it_claimed_directly(bridge):
    assert _settle(expected=None) == ("placeholder", {"reservation_id": "r1"})
    assert _settle(claimed="sid-9") == ("sid-9", {"reservation_id": "r1"})
    assert bridge.released == [] and bridge.reserved == []


def test_a_same_process_resume_the_bridge_folded_in_is_left_alone(bridge):
    bridge.row["claimed_by_session_id"] = "sid-9"
    bridge.live = {"sid-9": {"session_id": "sid-9"}, "placeholder": {"session_id": "sid-9"}}
    assert _settle() == ("sid-9", {"reservation_id": "r1"})
    assert bridge.released == [] and bridge.reserved == []


def test_a_resume_from_a_new_process_renews_the_claim_with_the_venue(bridge):
    """The placeholder deregistered and the resumed id registered from another
    process: the launch reserves again, venue included, so the resumed session's
    heartbeat claims it, and reports the resumed id with the renewed reservation."""
    bridge.live = {"sid-9": {"session_id": "sid-9"}}
    assert _settle() == ("sid-9", {"reservation_id": "r2"})
    assert bridge.released == ["r1"]
    assert bridge.reserved == [("wt-a", 900.0, _VENUE)]
    assert bridge.row["claimed_by_session_id"] == "sid-9"


def test_it_waits_while_the_placeholder_is_still_alive(bridge):
    """Both registered: the placeholder may still rename in place, so nothing
    is renewed; once it goes, the claim is."""
    bridge.live = {"sid-9": {"session_id": "sid-9"}, "placeholder": {"session_id": "placeholder"}}
    calls = []

    def _sleep(s):
        calls.append(s)
        if len(calls) == 2:
            del bridge.live["placeholder"]

    ticks = iter(range(10_000))
    got = settle_resumed_claim("wt-a", {"reservation_id": "r1"}, _VENUE, expected="sid-9",
                               claimed="placeholder", timeout=30.0, ttl_seconds=900.0,
                               sleep=_sleep, clock=lambda: float(next(ticks)))
    assert got == ("sid-9", {"reservation_id": "r2"}) and len(calls) == 2


def test_a_resume_that_started_a_new_conversation_keeps_the_placeholder(bridge):
    """The named conversation never registers (it couldn't be resumed): after
    the bounded wait the placeholder is the session, its claim untouched."""
    bridge.live = {"placeholder": {"session_id": "placeholder"}}
    assert _settle(timeout=5.0) == ("placeholder", {"reservation_id": "r1"})
    assert bridge.released == [] and bridge.reserved == []


def test_a_refused_renewal_still_reports_the_resumed_session(bridge):
    bridge.live = {"sid-9": {"session_id": "sid-9"}}
    bridge.reserve_error = True
    assert _settle() == ("sid-9", {})
    assert bridge.released == ["r1"]


def test_a_failed_bridge_read_is_not_known_yet_never_an_error(bridge):
    """A transient status failure must not escape: the launcher would treat it
    as a failed launch and stop a healthy session."""
    bridge.live = {"sid-9": {"session_id": "sid-9"}}
    bridge.fail_reads = 1
    assert _settle() == ("sid-9", {"reservation_id": "r2"})


def test_a_dead_placeholder_row_counts_as_gone(bridge):
    """An unclean exit leaves the placeholder's row to expire rather than deleted."""
    bridge.live = {"sid-9": {"session_id": "sid-9", "status": "live"},
                   "placeholder": {"session_id": "placeholder", "status": "expired"}}
    assert _settle() == ("sid-9", {"reservation_id": "r2"})


def test_an_expired_resumed_row_is_not_live(bridge):
    bridge.live = {"sid-9": {"session_id": "sid-9", "status": "expired"},
                   "placeholder": {"session_id": "placeholder"}}
    assert _settle(timeout=5.0) == ("placeholder", {"reservation_id": "r1"})
    assert bridge.reserved == []


def test_neither_live_is_no_session_never_the_dead_placeholder(bridge):
    """The placeholder exited and the resumed id never registered: the launch
    has no session, and must not report the gone placeholder as one."""
    bridge.live = {"sid-9": {"session_id": "sid-9", "status": "expired"},
                   "placeholder": {"session_id": "placeholder", "status": "expired"}}
    assert _settle(timeout=5.0) == (None, {"reservation_id": "r1"})
    bridge.live = {}
    assert _settle(timeout=5.0) == (None, {"reservation_id": "r1"})


def test_an_unanswered_lookup_is_never_proof_the_placeholder_is_gone(bridge):
    """A bridge that can't answer at the deadline keeps the placeholder as the
    session: a transient outage must not fail (and kill) a healthy launch."""
    bridge.fail_lookups = True
    assert _settle(timeout=5.0) == ("placeholder", {"reservation_id": "r1"})
    assert bridge.reserved == []


@pytest.mark.parametrize("claimant", [None, "someone-else"])
def test_an_unconfirmed_renewal_is_left_for_the_next_heartbeat(bridge, monkeypatch, claimant):
    """Not claimed in time (or by another id): the caller must not release the
    renewal, or the resumed session's late heartbeat could never claim it."""
    bridge.live = {"sid-9": {"session_id": "sid-9"}}
    monkeypatch.setattr(venue_copilot, "await_claim", lambda *a, **k: claimant)
    assert _settle() == ("sid-9", {})
    assert bridge.released == ["r1"] and len(bridge.reserved) == 1


@pytest.mark.parametrize("call", ["get_cli_mode_reservation", "release_cli_mode", "reserve_cli_mode", "await_claim"])
def test_a_bridge_call_that_cannot_start_never_escapes(bridge, monkeypatch, call):
    """A spawn failure (OSError) in any settlement call is an unknown outcome,
    never a launch failure that would stop the healthy resumed session."""
    bridge.live = {"sid-9": {"session_id": "sid-9"}}
    real, raised = getattr(venue_copilot, call), []

    def _boom(*a, **k):
        if not raised:
            raised.append(call)
            raise OSError("agent-bridge: cannot start")
        return real(*a, **k)

    monkeypatch.setattr(venue_copilot, call, _boom)
    session_id, renewed = _settle()
    assert raised == [call] and session_id == "sid-9"
    # A failed first read is just retried; a failed renewal step leaves nothing to release.
    assert renewed == ({"reservation_id": "r2"} if call == "get_cli_mode_reservation" else {})
