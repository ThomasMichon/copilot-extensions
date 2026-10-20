"""Unsupported native ACK sources must refuse before any POSIX receipt I/O."""

from __future__ import annotations

import argparse
import json

import pytest

from agent_worktrees import bridge_acquisition_launch, handoff_cli
from agent_worktrees import native_restart_launch, native_restart_receipt, native_source_context


@pytest.mark.parametrize("reason", [
    "Native lifecycle source is unqualified on this platform",
    "Container lifecycle sources are unqualified",
    "Native environment identity is unavailable",
])
def test_unqualified_ack_source_refuses_before_config_fences_or_receipts(monkeypatch, capfd, reason):
    def unavailable_epoch():
        raise ValueError(reason)

    def forbidden(*args, **kwargs):
        pytest.fail("Unqualified ACK reached configuration, fences or receipt I/O")

    monkeypatch.setattr(native_source_context, "epoch", unavailable_epoch)
    monkeypatch.setattr(handoff_cli.cfg, "tracking_dir", forbidden)
    monkeypatch.setattr(handoff_cli.cfg, "load_config", forbidden)
    monkeypatch.setattr(bridge_acquisition_launch, "launch_fence", forbidden)
    monkeypatch.setattr(native_restart_receipt, "read", forbidden)
    args = argparse.Namespace(
        restart_ack="00000000-0000-4000-8000-000000000005",
        worktree_id="exact-worktree", status_reconciled=True,
    )
    assert native_restart_launch.cmd_embody(args) == 3
    result = json.loads(capfd.readouterr().out)
    assert "native source is unqualified" in result["error"]
