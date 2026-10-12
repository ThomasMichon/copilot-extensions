"""Recovery refusal must precede the same-version activation shortcut."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from zdd import breadcrumb, routing

from worktree_manager import mux_daemon, mux_daemon_cutover as cutover


@pytest.mark.parametrize("phase", ["recovery", "reap"])
def test_activation_reports_refusal_without_same_version_success(tmp_path, monkeypatch, phase):
    endpoint = routing.Endpoint("127.0.0.1", 61001, pid=101, version="same")
    released = []
    calls = []
    monkeypatch.setattr(cutover, "routed_endpoint", lambda *_: endpoint)
    monkeypatch.setattr(cutover, "_stale_cutover_in_progress", lambda *_: True)
    monkeypatch.setattr(cutover, "load_or_create_control_token", lambda *_: "fixture-token")
    monkeypatch.setattr(mux_daemon, "read_lock_data", lambda *_: {})
    monkeypatch.setattr(
        cutover, "_acquire_cutover_lock",
        lambda *_: SimpleNamespace(release=lambda: released.append(True)),
    )
    refusal = {"refused": True, "reason": "conditional owner recovery required"}
    monkeypatch.setattr(
        breadcrumb, "recover_stale_cutover",
        lambda *_a, **_k: refusal if phase == "recovery" else {"recovered": False},
    )

    def reap(*args):
        calls.append("reap")
        return refusal

    monkeypatch.setattr(cutover, "_reap_abandoned_passive", reap)
    monkeypatch.setattr(
        cutover, "_already_at_version",
        lambda *_a, **_k: pytest.fail("refusal cannot become already-active-version"),
    )
    result = cutover.activate_after_update(root=tmp_path, slot=tmp_path, version="same")
    assert result["action"] == "refused"
    assert result["result"]["ok"] is False
    assert calls == ([] if phase == "recovery" else ["reap"])
    assert released == [True]
