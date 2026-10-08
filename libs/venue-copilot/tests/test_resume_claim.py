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
        live={}, released=[], reserved=[], reserve_error=False, renewal_claimant="sid-9",
    )

    def _reserve(scope, ttl_seconds, venue):
        if b.reserve_error:
            raise venue_copilot.VenueCopilotError("reservation_active")
        b.reserved.append((scope, ttl_seconds, venue))
        b.row = {"reservation_id": "r2", "claimed_by_session_id": None}
        return {"reservation_id": "r2"}

    def _get(scope):
        if b.row.get("reservation_id") == "r2" and b.renewal_claimant:
            b.row["claimed_by_session_id"] = b.renewal_claimant  # its next heartbeat
        return dict(b.row)

    monkeypatch.setattr(venue_copilot, "get_cli_mode_reservation", _get)
    monkeypatch.setattr(venue_copilot, "live_session_for", lambda h: b.live.get(h, {}))
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
