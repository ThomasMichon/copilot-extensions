"""Non-gating desktop diagnostics; unattended scheduler controls live separately."""

import json

import pytest

from test_health_windows_scheduler import REPO, _exercise_cycles
from test_health_windows_scheduler import pytestmark as pytestmark


@pytest.mark.timeout(240)
def test_two_calendar_cycles_without_windows_or_focus_changes(
    tmp_path, monkeypatch, record_testsuite_property,
):
    monkeypatch.syspath_prepend(str(REPO / "libs/agent-procutil/tests"))
    from sdk_desktop_observer import DesktopObserver, ObservationBlocked
    from health_task_desktop import make_api

    summary = {"status": "unobserved", "reason": "not-admitted"}
    observer = None
    try:
        try:
            api = make_api()
            facts = api.require_desktop()
            observer = DesktopObserver(api, "health-native-two-cycles")
        except (ObservationBlocked, OSError) as error:
            summary["reason"] = str(error)
            pytest.skip(f"optional desktop observation unavailable: {error}")
        try:
            packet = _exercise_cycles(tmp_path, observer, observed_session=facts["session"])
        except ObservationBlocked as error:
            summary["reason"] = str(error)
            pytest.skip(f"optional desktop observation unqualified: {error}")
        summary = {
            "status": "observed" if packet["passed"] else "unqualified",
            "session_kind": facts["session_kind"], "samples": packet["samples"],
            "maximum_gap": packet["maximum_gap"], "hook_count": packet["hook_count"],
            "violations": len(packet["violations"]), "sealed": packet["sealed"],
            "hooks_closed": packet["hooks_closed"],
        }
        if not packet["passed"]:
            pytest.skip("optional desktop observation unqualified; see recorded coverage/events")
    finally:
        record_testsuite_property("desktop_observation", json.dumps(summary, sort_keys=True))
        if observer is not None and observer.thread.ident is not None:
            assert not observer.thread.is_alive(), "owned observer did not quiesce"
            assert observer.hooks_closed and not observer.cleanup_error, "owned hooks did not settle"
