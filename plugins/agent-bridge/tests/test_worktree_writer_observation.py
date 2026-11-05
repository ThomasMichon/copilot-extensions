"""Cheap ASGI discovery observes selection, not the delegated serializer."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from zdd import routing

from agent_bridge import session_lifecycle_cli as lifecycle
from agent_bridge.routes import health


@pytest.mark.parametrize("selection", ["legacy", "cell", "path", "missing", "refused"])
def test_health_observes_canonical_writer_selection_without_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    own = tmp_path / ".agent-bridge"
    peer = tmp_path / ".agent-worktrees"
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: own)
    monkeypatch.setattr(
        lifecycle, "_legacy_worktrees_launch_prefix",
        lambda root: ["legacy-owner"] if selection == "legacy" else None,
    )
    monkeypatch.setattr(
        lifecycle.shutil, "which", lambda name: "/compat/owner" if selection == "path" else None,
    )
    if selection in {"cell", "refused"}:
        own = tmp_path / "marketplaces" / "fixture" / "plugins" / "agent-bridge"
        peer = own.parent / "agent-worktrees"
        peer.mkdir(parents=True)
        monkeypatch.setattr(lifecycle._peer_launch, "validate_owner", Mock(
            return_value={"cellRoot": str(own.parent.parent), "pluginRoot": str(own)},
            side_effect=ValueError("invalid owner") if selection == "refused" else None,
        ))
        monkeypatch.setattr(lifecycle._peer_launch, "launch_prefix", lambda *args: ["cell-owner"])
        monkeypatch.setattr(lifecycle.shutil, "which", lambda name: pytest.fail("cell used PATH"))
    monkeypatch.setattr(
        lifecycle.subprocess, "run", lambda *a, **k: pytest.fail("health launched a child"),
    )
    monkeypatch.setattr(health, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}),
    )
    routing.publish_active(tmp_path, bind="127.0.0.1", port=12345, pid=os.getpid(), version="fixture")
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as client:
        before = client.get("/health").json()
        assert before["status"] == "ok"
        assert before["capabilities"]["worktree_record_delegation"] == 1
        assert before["worktree_record_writer"] == {
            "installation_root": str(peer) if selection in {"legacy", "cell"} else None,
        }
        assert before["slot"]["role"] == "active"
        assert before["slot"]["pid"] == os.getpid()
        assert type(before["slot"]["active"]["generation"]) is int
        assert before["slot"]["active"]["generation"] > 0
        assert "writer_contract" not in before["worktree_record_writer"]
        routing.publish_active(tmp_path, bind="127.0.0.1", port=12346, pid=999999, version="fixture")
        after = client.get("/health").json()
        assert after["slot"]["role"] == "passive"
        assert after["slot"]["active"]["generation"] != before["slot"]["active"]["generation"]
        assert after["worktree_record_writer"] == before["worktree_record_writer"]
