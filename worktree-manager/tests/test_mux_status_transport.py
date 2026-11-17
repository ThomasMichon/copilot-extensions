"""Manager publishes native status through the Core-owned presentation seam."""

import pytest
from agent_remote_driver import mux_status

from worktree_manager import mux_daemon


@pytest.mark.parametrize("previous_render", [None, "2026-01-01T00:00:00Z"])
def test_native_status_uses_core_presentation_transport_and_exact_incarnation(
    monkeypatch: pytest.MonkeyPatch, previous_render: str | None,
) -> None:
    monkeypatch.setattr(mux_daemon, "run_mux_child", lambda *a, **k: pytest.fail("Periodic PSMux CLI"))
    calls: list[tuple[str, str, dict[str, str]]] = []

    def apply(session: str, incarnation: str, values: dict[str, str]) -> bool:
        calls.append((session, incarnation, values))
        return True

    monkeypatch.setattr(mux_status, "apply_psmux_status", apply)
    assert mux_daemon.apply_status_options({
        "mux_bin": "psmux.exe", "mux_session": "wt-fixture",
        "session_incarnation": "$7:100", "native_session_incarnation": None,
        "last_status_rendered_at": previous_render,
    }, {
        "@aw_ctx": "title", "@aw_seg": "status",
        "@aw_updater": "monitor", "@aw_updater_prefix": "fixture",
    })
    assert calls == [("wt-fixture", "$7:100", {
        "status": "on", "status-interval": "15",
        "status-left-length": "100", "status-left": "#{@aw_ctx} ",
        "status-right-length": "150", "status-right": "#{@aw_seg} %H:%M ",
        "@aw_ctx": "title", "@aw_seg": "status",
    })]


def test_native_ownership_only_delta_does_not_change_native_election(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mux_status, "apply_psmux_status", lambda *a, **k: pytest.fail("no presentation"))
    assert mux_daemon.apply_status_options({
        "mux_bin": "psmux.exe", "mux_session": "wt-fixture", "session_incarnation": "$7:100",
    }, {"@aw_updater": "replacement-monitor", "@aw_updater_prefix": "fixture"})


def test_native_unknown_option_does_not_become_ignored_ownership_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("unknown option"))
    assert not mux_daemon.apply_status_options({
        "mux_bin": "psmux.exe", "mux_session": "wt-fixture", "session_incarnation": "$7:100",
    }, {"@foreign_owner": "forbidden"})
