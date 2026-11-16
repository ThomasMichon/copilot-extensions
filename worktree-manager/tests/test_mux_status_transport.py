"""Manager publishes native status through the Core-owned presentation seam."""

import pytest
from agent_remote_driver import mux_status

from worktree_manager import mux_daemon


def test_native_status_uses_core_presentation_transport_and_exact_incarnation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mux_daemon, "run_mux_child", lambda *a, **k: pytest.fail("Periodic PSMux CLI"))
    calls: list[tuple[str, str, dict[str, str]]] = []

    def apply(session: str, incarnation: str, values: dict[str, str]) -> bool:
        calls.append((session, incarnation, values))
        return True

    monkeypatch.setattr(mux_status, "apply_psmux_status", apply)
    assert mux_daemon.apply_status_options({
        "mux_bin": "psmux.exe", "mux_session": "wt-fixture",
        "session_incarnation": "$7:100", "native_session_incarnation": "$7:100",
    }, {"@aw_ctx": "title", "@aw_seg": "status"})
    assert calls == [("wt-fixture", "$7:100", {
        "status": "on", "status-interval": "15",
        "status-left-length": "100", "status-left": "#{@aw_ctx} ",
        "status-right-length": "150", "status-right": "#{@aw_seg} %H:%M ",
        "@aw_ctx": "title", "@aw_seg": "status",
    })]
