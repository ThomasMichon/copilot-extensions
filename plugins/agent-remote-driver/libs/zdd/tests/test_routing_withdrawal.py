"""Exact withdrawal CAS retains native history without PID-only cleanup."""

from dataclasses import replace
import os
import time

import pytest

from zdd import publication_history, routing


def test_withdrawal_retains_history_and_next_generation(tmp_path):
    endpoint = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=1234, pid=os.getpid(),
    )
    before = routing.read_table(tmp_path, strict=True)
    observed = []
    after = routing.withdraw_active_guarded(
        tmp_path, expected_active=endpoint,
        refuse_current=lambda raw: observed.append(raw), lock_deadline=time.monotonic() + 1,
    )
    assert observed == [endpoint.to_dict()]
    assert "active" not in after and after["previous"] == endpoint.to_dict()
    assert publication_history.read(after) == publication_history.read(before)
    following = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=1235, pid=os.getpid(),
    )
    assert following.generation == endpoint.generation + 1


@pytest.mark.parametrize("changed", [
    {"process_start_time": "0"}, {"generation": 2}, {"port": 4321}, {"version": "other"},
])
def test_withdrawal_refuses_changed_identity_even_same_pid(tmp_path, changed):
    endpoint = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=1234, pid=os.getpid(),
    )
    before = routing.routing_table_path(tmp_path).read_bytes()
    with pytest.raises(routing.ActivePublicationRefused):
        routing.withdraw_active_guarded(
            tmp_path, expected_active=replace(endpoint, **changed),
            refuse_current=lambda _: None, lock_deadline=time.monotonic() + 1,
        )
    assert routing.routing_table_path(tmp_path).read_bytes() == before


def test_withdrawal_refusal_and_failed_write_preserve_custody(tmp_path, monkeypatch):
    endpoint = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=1234, pid=os.getpid(),
    )
    before = routing.routing_table_path(tmp_path).read_bytes()
    with pytest.raises(routing.ActivePublicationRefused, match="authority revoked"):
        routing.withdraw_active_guarded(
            tmp_path, expected_active=endpoint, refuse_current=lambda _: "authority revoked",
            lock_deadline=time.monotonic() + 1,
        )
    assert routing.routing_table_path(tmp_path).read_bytes() == before

    def fail(*args):
        raise OSError("fixture durable write failed")

    monkeypatch.setattr(routing.routing_persistence, "write", fail)
    with pytest.raises(OSError, match="durable write"):
        routing.withdraw_active_guarded(
            tmp_path, expected_active=endpoint, refuse_current=lambda _: None,
            lock_deadline=time.monotonic() + 1,
        )
    assert routing.routing_table_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("authority", ["missing-validator", "pid-only"])
def test_withdrawal_requires_native_identity_and_explicit_validator(tmp_path, authority):
    endpoint = routing.Endpoint("127.0.0.1", 1234, pid=42)
    if authority == "missing-validator":
        endpoint = replace(endpoint, process_start_time="1")
    with pytest.raises((routing.ActivePublicationRefused, TypeError)):
        routing.withdraw_active_guarded(
            tmp_path, expected_active=endpoint,
            refuse_current=None if authority == "missing-validator" else lambda _: None,
            lock_deadline=time.monotonic() + 1,
        )
    assert not routing.routing_table_path(tmp_path).exists()
