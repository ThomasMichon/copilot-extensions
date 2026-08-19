"""Tests for the generation self-retire decision.

Exercises ``agent_bridge.self_retire.is_superseded`` -- the fail-safe predicate a
demoted daemon uses to decide it has been superseded by a live, strictly-newer
generation. The whole point is that it says "yes" in exactly one shape and "no"
(stay alive) for every ambiguous state, so the matrix below is deliberately
exhaustive on the "no" side.
"""

from __future__ import annotations

import socket

from agent_bridge.self_retire import _is_listening, is_superseded

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
