"""Tests for the supersession self-retire decision."""

from __future__ import annotations

from single_instance_lease import is_superseded


def _table(pid, generation, *, bind="127.0.0.1", port=9281, key="active"):
    return {key: {"pid": pid, "generation": generation, "bind": bind, "port": port}}


def _yes(_host, _port):
    return True


def _no(_host, _port):
    return False


def test_superseded_by_live_newer_generation():
    table = _table(pid=222, generation=8)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is True


def test_not_superseded_when_successor_not_listening():
    table = _table(pid=222, generation=8)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_no) is False


def test_own_pid_active_is_never_superseded():
    table = _table(pid=111, generation=9)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False


def test_equal_generation_is_not_supersession():
    table = _table(pid=222, generation=7)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False


def test_older_generation_is_not_supersession():
    table = _table(pid=222, generation=5)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False


def test_no_table_stays_alive():
    assert is_superseded(None, my_pid=1, my_generation=0, is_listening=_yes) is False
    assert is_superseded({}, my_pid=1, my_generation=0, is_listening=_yes) is False


def test_missing_or_broken_active_stays_alive():
    assert is_superseded({"active": None}, 1, 0, is_listening=_yes) is False
    assert is_superseded({"active": {"generation": 9}}, 1, 0, is_listening=_yes) is False
    assert is_superseded({"active": "x"}, 1, 0, is_listening=_yes) is False


def test_bad_generation_value_stays_alive():
    table = {"active": {"pid": 2, "generation": "NaN", "port": 1, "bind": "127.0.0.1"}}
    assert is_superseded(table, my_pid=1, my_generation=0, is_listening=_yes) is False


def test_wildcard_bind_maps_to_loopback():
    seen = {}

    def probe(host, port):
        seen["host"] = host
        return True

    table = _table(pid=222, generation=8, bind="0.0.0.0")
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=probe) is True
    assert seen["host"] == "127.0.0.1"


def test_ipv6_wildcard_bind_maps_to_loopback():
    seen = {}

    def probe(host, port):
        seen["host"] = host
        return True

    table = _table(pid=222, generation=8, bind="::")
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=probe) is True
    assert seen["host"] == "::1"


def test_zero_port_stays_alive():
    table = _table(pid=222, generation=8, port=0)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False
