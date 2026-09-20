"""Tests for the generation self-retire decision.

Exercises ``agent_bridge.self_retire.is_superseded`` -- the fail-safe predicate a
demoted daemon uses to decide it has been superseded by a live, strictly-newer
generation. The whole point is that it says "yes" in exactly one shape and "no"
(stay alive) for every ambiguous state, so the matrix below is deliberately
exhaustive on the "no" side.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

from agent_bridge.app import _count_active_sessions
from agent_bridge.self_retire import _is_listening, is_superseded, slot_descriptor

CONFIG_DIR = "/does/not/matter"  # read_table is injected in every case


def _table(active: dict | None = None, previous: dict | None = None) -> dict:
    t: dict = {}
    if active is not None:
        t["active"] = active
    if previous is not None:
        t["previous"] = previous
    return t


def _always_listening(host: str, port: int) -> bool:
    return True


def _never_listening(host: str, port: int) -> bool:
    return False


# -- the single "yes" shape --------------------------------------------------

def test_superseded_by_live_newer_generation():
    table = _table(active={"bind": "127.0.0.1", "port": 9300, "pid": 999,
                           "generation": 5})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is True


# -- every "no" (stay-alive) shape -------------------------------------------

def test_not_superseded_when_no_table():
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: None, is_listening=_always_listening,
    ) is False


def test_not_superseded_when_table_not_a_dict():
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: ["not", "a", "dict"], is_listening=_always_listening,
    ) is False


def test_not_superseded_when_no_active_entry():
    table = _table(previous={"bind": "127.0.0.1", "port": 9300, "pid": 999,
                             "generation": 5})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is False


def test_not_superseded_when_active_is_our_pid():
    # The genuinely-active daemon reads its own pid as active -> never retires,
    # even at a higher generation (e.g. it re-published itself).
    table = _table(active={"bind": "127.0.0.1", "port": 9300, "pid": 111,
                           "generation": 99})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is False


def test_not_superseded_when_active_pid_missing():
    table = _table(active={"bind": "127.0.0.1", "port": 9300, "generation": 5})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is False


def test_not_superseded_when_generation_equal():
    table = _table(active={"bind": "127.0.0.1", "port": 9300, "pid": 999,
                           "generation": 4})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is False


def test_not_superseded_when_generation_lower():
    table = _table(active={"bind": "127.0.0.1", "port": 9300, "pid": 999,
                           "generation": 2})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is False


def test_not_superseded_when_successor_not_listening():
    # A newer generation exists in the table but is not (yet) accepting
    # connections -> not a confirmed live successor -> stay alive.
    table = _table(active={"bind": "127.0.0.1", "port": 9300, "pid": 999,
                           "generation": 5})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_never_listening,
    ) is False


def test_not_superseded_when_active_entry_unparseable():
    # A structurally-broken active entry (missing port) -> Endpoint.from_dict
    # returns None -> stay alive.
    table = _table(active={"bind": "127.0.0.1", "pid": 999, "generation": 5})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=4,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is False


def test_default_generation_zero_never_supersedes():
    # An endpoint with no recorded generation defaults to 0, which is never
    # strictly greater than our generation.
    table = _table(active={"bind": "127.0.0.1", "port": 9300, "pid": 999})
    assert is_superseded(
        CONFIG_DIR, my_pid=111, my_generation=0,
        read_table=lambda _d: table, is_listening=_always_listening,
    ) is False


# -- the real listener probe -------------------------------------------------

def test_is_listening_true_against_a_real_bound_socket():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert _is_listening("127.0.0.1", port) is True


def test_is_listening_false_against_a_closed_port():
    # Bind then close to obtain a port nothing is listening on.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert _is_listening("127.0.0.1", port) is False


def test_end_to_end_supersession_with_real_listener():
    # Full path with the real _is_listening: a live successor on a bound port at
    # a strictly-higher generation supersedes us.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        table = _table(active={"bind": "127.0.0.1", "port": port, "pid": 999,
                               "generation": 7})
        assert is_superseded(
            CONFIG_DIR, my_pid=111, my_generation=6,
            read_table=lambda _d: table,
        ) is True


def test_active_count_includes_live_host_records_for_retire_gate():
    mgr = SimpleNamespace(list_sessions=lambda: [], _live_host_records=lambda: [object()])

    assert _count_active_sessions(mgr) == 1


def test_active_count_includes_fresh_live_session_registrations_for_retire_gate():
    mgr = SimpleNamespace(list_sessions=lambda: [], _live_host_records=lambda: [])
    db = SimpleNamespace(list_fresh_live_sessions=lambda *, now: [{"session_id": "s"}])

    assert _count_active_sessions(mgr, db) == 1


# -- slot_descriptor (process-slot-ownership Phase 5) ------------------------

def test_slot_descriptor_shape_with_no_status():
    # No status passed -> conservative defaults, never a KeyError.
    slot = slot_descriptor("/does/not/matter", read_table=lambda _d: {})
    assert set(slot) == {"pid", "role", "active", "previous", "self_retire"}
    assert isinstance(slot["pid"], int)
    assert slot["role"] == "unknown"
    assert slot["active"] is None
    assert slot["previous"] is None
    assert slot["self_retire"] == {
        "enabled": False, "armed": False, "generation": None,
        "superseded": False, "confirms": 0,
    }


def test_slot_descriptor_reports_active_role_for_own_pid():
    import os

    table = _table(active={"bind": "127.0.0.1", "port": 9280, "pid": os.getpid()})
    slot = slot_descriptor("/does/not/matter", read_table=lambda _d: table)
    assert slot["role"] == "active"
    assert slot["active"]["pid"] == os.getpid()


def test_slot_descriptor_reports_passive_role_for_other_active_pid():
    table = _table(active={"bind": "127.0.0.1", "port": 9280, "pid": 999999})
    slot = slot_descriptor("/does/not/matter", read_table=lambda _d: table)
    assert slot["role"] == "passive"
    assert slot["active"]["pid"] == 999999


def test_slot_descriptor_reports_unknown_role_when_active_pid_is_null():
    # A malformed/legacy routing entry with no recorded pid must never be
    # mistaken for "passive" -- there is nothing to compare against.
    table = _table(active={"bind": "127.0.0.1", "port": 9280, "pid": None})
    slot = slot_descriptor("/does/not/matter", read_table=lambda _d: table)
    assert slot["role"] == "unknown"
    assert slot["active"]["pid"] is None


def test_slot_descriptor_reports_unknown_role_when_active_pid_is_boolean():
    # bool is an int subclass in Python; pid: true must not compare as a real pid.
    table = _table(active={"bind": "127.0.0.1", "port": 9280, "pid": True})
    slot = slot_descriptor("/does/not/matter", read_table=lambda _d: table)
    assert slot["role"] == "unknown"


def test_slot_descriptor_degrades_when_read_table_raises():
    def _raise(_d):
        raise OSError("no routing dir")

    slot = slot_descriptor("/does/not/matter", read_table=_raise)
    assert slot["role"] == "unknown"
    assert slot["active"] is None
    assert slot["previous"] is None


def test_slot_descriptor_reflects_passed_self_retire_status():
    status = {
        "enabled": True, "armed": True, "generation": 3,
        "superseded": True, "confirms": 2,
    }
    slot = slot_descriptor(
        "/does/not/matter", read_table=lambda _d: {}, self_retire_status=status,
    )
    assert slot["self_retire"] == status
    # Defensive copy -- caller's live dict is not aliased.
    status["confirms"] = 99
    assert slot["self_retire"]["confirms"] == 2
