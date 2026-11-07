"""Independent activation must not depend on historical recovery."""

from __future__ import annotations

import pytest
from zdd import breadcrumb, routing

from worktree_manager import mux_daemon_cutover as cutover


def test_same_version_activation_does_not_consult_historical_recovery(tmp_path, monkeypatch):
    endpoint = routing.Endpoint("127.0.0.1", 61001, pid=101, version="same")
    monkeypatch.setattr(cutover, "routed_endpoint", lambda *_: endpoint)
    monkeypatch.setattr(
        cutover, "_stale_cutover_in_progress", lambda *_: pytest.fail("unexpected recovery check"),
    )
    monkeypatch.setattr(
        breadcrumb, "recover_stale_cutover", lambda *_a, **_k: pytest.fail("unexpected recovery"),
    )
    monkeypatch.setattr(
        cutover, "_reap_abandoned_passive", lambda *_a, **_k: pytest.fail("unexpected reaping"),
    )
    monkeypatch.setattr(cutover, "_already_at_version", lambda *_a, **_k: True)
    result = cutover.activate_after_update(root=tmp_path, slot=tmp_path, version="same")
    assert result["action"] == "noop"
    assert result["reason"] == "already-active-version"
