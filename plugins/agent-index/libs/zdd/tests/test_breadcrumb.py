"""Tests for the stale-cutover breadcrumb + recovery path (#1756)."""

from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from zdd import breadcrumb, routing


@pytest.fixture(autouse=True)
def process_identities(monkeypatch):
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: str(pid * 10))


def test_recover_stale_cutover_brackets_ipv6_old_endpoint(tmp_path: Path):
    """Regression test: recover_stale_cutover() must bracket an IPv6 old
    endpoint when forming make_client()'s base_url -- unbracketed
    ("http://::1:1234") is not a valid URL and breaks make_client/urlparse on
    the host's own colons."""
    breadcrumb.write_breadcrumb(
        tmp_path, state="draining", old={"bind": "::", "port": 9281},
        new_port=9282, error=None, started_at="2026-07-02T22:40:00Z",
    )

    seen_urls: list[str] = []

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            seen_urls.append(base_url)

        def undrain(self):
            return {"draining": False}

    result = breadcrumb.recover_stale_cutover(
        tmp_path, make_client=FakeClient, health_check=lambda host, port: True,
    )

    assert result["recovered"] is True
    assert seen_urls == ["http://[::1]:9281"]


def test_recover_stale_cutover_no_breadcrumb_is_noop(tmp_path: Path):
    result = breadcrumb.recover_stale_cutover(
        tmp_path, make_client=lambda base_url: None,
    )
    assert result["recovered"] is False


# -- reap_abandoned_passive (#5195) ------------------------------------------


def _write_aged_breadcrumb(tmp_path: Path, *, new_pid: int | None, age_s: float,
                           state: str = "started") -> dict:
    """Write a breadcrumb whose ``updated_at`` is ``age_s`` seconds in the past."""
    from datetime import datetime, timedelta, timezone
    import time

    record = breadcrumb.write_breadcrumb(
        tmp_path, state=state, old=None, new_port=9290, new_pid=new_pid,
        publication_witness=routing.publication_witness(
            tmp_path, deadline=time.monotonic() + 1,
        ),
        new_process_start_time=str(new_pid * 10) if new_pid is not None else None,
    )
    record["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=age_s)
    ).isoformat()
    import json

    (tmp_path / "cutover.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def test_reap_abandoned_passive_no_breadcrumb_is_noop(tmp_path: Path):
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: True, terminate=lambda pid: True,
    )
    assert result == {"reaped": False, "reason": "no stale cutover breadcrumb", "pid": None}


def test_reap_returns_refusal_when_routing_lock_is_contended(tmp_path):
    _write_aged_breadcrumb(tmp_path, new_pid=202, age_s=3600)
    before = breadcrumb.breadcrumb_path(tmp_path).read_bytes()
    ready = threading.Event()
    release = threading.Event()

    def hold_routing_lock():
        with routing.routing_lock(tmp_path, deadline=time.monotonic() + 2):
            ready.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_routing_lock)
    holder.start()
    try:
        assert ready.wait(timeout=2)
        result = breadcrumb.reap_abandoned_passive(
            tmp_path, pid_alive=lambda _: True,
            terminate=lambda _: pytest.fail("termination attempted without routing custody"),
            grace_seconds=0,
        )
    finally:
        release.set()
        holder.join(timeout=2)
    assert not holder.is_alive()
    assert issubclass(routing.RoutingLockTimeout, OSError)
    assert result["reaped"] is False and result["refused"] is True
    assert "RoutingLockTimeout" in result["reason"]
    assert breadcrumb.breadcrumb_path(tmp_path).read_bytes() == before


def test_reap_abandoned_passive_terminal_breadcrumb_is_noop(tmp_path: Path):
    breadcrumb.write_breadcrumb(
        tmp_path, state="committed", old=None, new_port=9290, new_pid=4321,
    )
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: True, terminate=lambda pid: True,
    )
    assert result["reaped"] is False


def test_reap_abandoned_passive_no_new_pid_is_noop(tmp_path: Path):
    _write_aged_breadcrumb(tmp_path, new_pid=None, age_s=9999)
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: True, terminate=lambda pid: True,
    )
    assert result == {
        "reaped": False,
        "reason": "breadcrumb has no recorded passive pid",
        "pid": None,
    }


def test_reap_abandoned_passive_too_fresh_is_noop(tmp_path: Path):
    """A cutover that started moments ago may still be genuinely in flight
    (e.g. mid-drain) -- must never be touched."""
    record = _write_aged_breadcrumb(tmp_path, new_pid=4321, age_s=5)
    terminated = []
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: True,
        terminate=lambda pid: terminated.append(pid) or True,
        record=record,
    )
    assert result["reaped"] is False
    assert "fresh" in result["reason"]
    assert terminated == []


def test_reap_abandoned_passive_matches_current_active_is_noop(tmp_path: Path):
    """A recorded passive pid that IS the confirmed-live active was promoted
    -- never abandoned -- and must never be terminated."""
    record = _write_aged_breadcrumb(tmp_path, new_pid=4321, age_s=9999)
    terminated = []
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: True,
        terminate=lambda pid: terminated.append(pid) or True,
        active_pid=4321,
        record=record,
    )
    assert result["reaped"] is False
    assert "promoted" in result["reason"]
    assert terminated == []


def test_reap_abandoned_passive_dead_pid_is_noop(tmp_path: Path):
    record = _write_aged_breadcrumb(tmp_path, new_pid=4321, age_s=9999)
    terminated = []
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: False,
        terminate=lambda pid: terminated.append(pid) or True,
        record=record,
    )
    assert result["reaped"] is False
    assert terminated == []


def test_reap_abandoned_passive_terminates_stranded_pid(tmp_path: Path):
    """The exact #5195 scenario: an aged, non-terminal breadcrumb naming a
    live pid that never became the routing table's active."""
    record = _write_aged_breadcrumb(tmp_path, new_pid=4321, age_s=9999)
    terminated = []
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: True,
        terminate=lambda pid: terminated.append(pid) or True,
        active_pid=111,  # a different, confirmed-live lineage
        record=record,
    )
    assert result == {"reaped": True, "reason": "terminated", "pid": 4321}
    assert terminated == [4321]


def test_reap_abandoned_passive_terminate_failure_is_reported(tmp_path: Path):
    record = _write_aged_breadcrumb(tmp_path, new_pid=4321, age_s=9999)

    def boom(pid):
        raise OSError("no such process")

    result = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda pid: True, terminate=boom, record=record,
    )
    assert result["reaped"] is False
    assert "termination failed" in result["reason"] and result["refused"]


def test_recovery_preserves_original_transition_identity_for_fresh_guarded_reap(tmp_path):
    import time

    original = breadcrumb.write_breadcrumb(
        tmp_path, state="draining", pid=987654321,
        old={"bind": "127.0.0.1", "port": 61001}, new_pid=202, new_port=61002,
        publication_witness=routing.publication_witness(
            tmp_path, deadline=time.monotonic() + 1,
        ),
        new_process_start_time="2020",
    )

    class Client:
        def undrain(self):
            return {}

    recovery = breadcrumb.recover_stale_cutover(tmp_path, lambda _: Client())
    assert recovery["recovered"]
    assert breadcrumb.read_breadcrumb(tmp_path)["pid"] == original["pid"]
    assert breadcrumb.read_breadcrumb(tmp_path)["publication_witness"] == original["publication_witness"]
    terminated = []
    reap = breadcrumb.reap_abandoned_passive(
        tmp_path, record=original, active_pid=101, grace_seconds=0,
        pid_alive=lambda _: True, terminate=lambda pid: terminated.append(pid) or True,
    )
    assert reap["reaped"] and terminated == [202]


@pytest.mark.parametrize("content", ["{", "{}", '{"state":"unknown"}'])
def test_legacy_recovery_and_reap_preserve_unverifiable_custody(tmp_path, content):
    path = breadcrumb.breadcrumb_path(tmp_path)
    path.write_text(content)
    recovered = breadcrumb.recover_stale_cutover(
        tmp_path, lambda _: pytest.fail("unverifiable recovery must not contact a daemon"),
    )
    reaped = breadcrumb.reap_abandoned_passive(
        tmp_path, pid_alive=lambda _: True,
        terminate=lambda _: pytest.fail("unverifiable recovery must not terminate"),
    )
    assert recovered["refused"] and reaped["refused"]
    assert path.read_text() == content


def test_reap_refuses_truthy_nonboolean_exit_evidence(tmp_path):
    record = _write_aged_breadcrumb(tmp_path, new_pid=4321, age_s=9999)
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, record=record, pid_alive=lambda _: True, terminate=lambda _: "false",
    )
    assert not result["reaped"] and result["refused"]
