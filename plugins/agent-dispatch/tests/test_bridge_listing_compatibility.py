"""Managed-session consumers preserve old arrays and accept selected catalogs."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from agent_dispatch import attention_siblings, tracking


def listing(catalog: bool) -> tuple[dict[str, Any], object]:
    row = {
        "session_id": "owned-control", "worktree_id": "workspace", "project": "example",
        "status": "running", "target_type": "local",
    }
    value: object = [row]
    if catalog:
        value = {
            "version": 1, "sources": {"managed": {"availability": "available"}},
            "targets": [
                {"kind": "managed", "handle": "owned-control", "session": row},
                {"kind": "worktree", "handle": "cold-workspace"},
                {"kind": "live", "handle": "interactive-native"},
                {"kind": "session_host", "handle": "host-instance:original"},
            ],
        }
    return row, value


@pytest.mark.parametrize("catalog", [False, True])
def test_tracking_reads_only_managed_rows_without_new_flags_or_ensure(
    monkeypatch: pytest.MonkeyPatch, catalog: bool,
) -> None:
    row, value = listing(catalog)
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(tracking, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(tracking, "_run_capture", run)
    assert tracking.list_local_body_sessions(ensure_daemon=False) == [row]
    assert len(calls) == 1 and calls[0][0] == ["bridge", "--json", "sessions"]
    assert calls[0][1]["env"]["AGENT_BRIDGE_NO_ENSURE"] == "1"


@pytest.mark.parametrize("catalog", [False, True])
def test_attention_keeps_managed_operator_decisions_across_both_listing_shapes(catalog: bool) -> None:
    _, value = listing(catalog)
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[2] == "sessions":
            body = value
        elif argv[2] == "live-sessions":
            body = []
        elif argv[2] == "attention":
            body = {"schema": 1, "sessions": [{
                "status": "ok", "reason": "input_required", "session_id": "owned-control",
                "registry": "bridge", "availability": "available", "detail": "choose",
            }]}
        elif argv[2] == "presence":
            body = {"presence": {"state": "idle", "confidence": "scanned"}}
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    result = attention_siblings.read_bridge(
        "2026-01-01T00:00:00Z", prefix=["bridge"], machine="local", run=run,
    )
    (item,) = result["items"]
    assert result["status"] == "ok" and item["display_state"] == "awaiting_input"
    assert item["entity_ref"] == "wt:local/example/workspace"
    assert calls[0] == ["bridge", "--json", "sessions"]


@pytest.mark.parametrize("value", [
    {"version": 2, "targets": [], "sources": {"managed": {"availability": "available"}}},
    {"version": 1, "targets": [], "sources": {"managed": {"availability": "unavailable"}}},
])
def test_unsupported_or_unavailable_managed_source_does_not_become_a_successful_empty_read(
    value: dict[str, Any],
) -> None:
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    result = attention_siblings.read_bridge(
        "2026-01-01T00:00:00Z", prefix=["bridge"], machine="local", run=run,
    )
    assert result["status"] == "failed"
    assert calls == [["bridge", "--json", "sessions"]]
