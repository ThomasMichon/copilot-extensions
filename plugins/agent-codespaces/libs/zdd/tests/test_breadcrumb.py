"""Tests for the stale-cutover breadcrumb + recovery path (#1756)."""

from __future__ import annotations

from pathlib import Path

from zdd import breadcrumb


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

    record = breadcrumb.write_breadcrumb(
        tmp_path, state=state, old=None, new_port=9290, new_pid=new_pid,
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
    assert "terminate failed" in result["reason"]
