"""Keep control, native conversation and workspace identities independent."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from agent_bridge.bridge_inventory import project_bridges
from agent_bridge.bridge_merge import merge_rows
from agent_bridge.routes.live_sessions import _to_info
from test_bridge_inventory import registration, sources, workspace


def managed(native: str, *, worktree: str | None = None) -> dict[str, Any]:
    return {
        "kind": "managed",
        "handle": "control-" + native,
        "sources": ["managed"],
        "session": {
            "session_id": "control-" + native,
            "acp_session_id": native,
            "project": "demo",
            "machine": "host-a",
            "worktree_id": worktree,
            "target_type": "local",
            "target_dir": "/work/demo",
            "status": "idle",
            "session_live": True,
        },
    }


def test_host_control_id_does_not_conflict_with_controller_native_identity() -> None:
    item = workspace(live=None)
    host = {
        "kind": "session_host",
        "handle": "control-native-a",
        "sources": ["session_host"],
        "registration": {
            "session_id": "control-native-a",
            "cwd": item["worktree"]["path"],
            "machine": "host-a",
            "boundary": "local",
            "liveness": "observed",
        },
    }
    (execution,) = project_bridges([item, host], sources(), project="demo")["bridges"]
    assert execution["session_id"] is None
    controller = managed("native-a", worktree="workspace-a")
    controller["session"]["target_dir"] = item["worktree"]["path"]
    (control,) = project_bridges([controller], sources(), project="demo")["bridges"]
    for observations in ([execution, control], [control, execution]):
        merged, conflict = merge_rows(observations)
        assert not conflict
        assert merged["session_id"] == "native-a"
        assert merged["managed_session_id"] == "control-native-a"
        assert merged["control"] == "registered"


@pytest.mark.parametrize("unknown_host", [False, True])
def test_distinct_anchor_natives_do_not_join_through_a_shared_directory(
    unknown_host: bool,
) -> None:
    targets = [managed("native-a"), managed("native-b")]
    if unknown_host:
        targets.insert(
            0,
            {
                "kind": "session_host",
                "handle": "unknown-host-control",
                "registration": {
                    "session_id": "unknown-host-control",
                    "machine": "host-a",
                    "cwd": "/work/demo",
                    "liveness": "observed",
                },
            },
        )
    for observations in (targets, list(reversed(targets))):
        result = project_bridges(observations, sources(), project="demo", live=True)
        rows = [row for row in result["bridges"] if row["session_id"] is not None]
        assert {row["session_id"] for row in rows} == {"native-a", "native-b"}
        assert len(rows) == 2
        assert all(row["control"] == "registered" for row in rows)
        assert result["complete"] is True


@pytest.mark.parametrize("same_native", [False, True])
def test_unknown_host_cannot_bridge_distinct_project_scopes(same_native: bool) -> None:
    first, second = managed("native-a"), managed("native-a" if same_native else "native-b")
    second["session"]["project"] = "other-project"
    unknown = {
        "kind": "session_host",
        "handle": "unknown-control",
        "registration": {
            "session_id": "unknown-control",
            "machine": "host-a",
            "cwd": "/work/demo",
            "liveness": "observed",
        },
    }
    for targets in ([unknown, first, second], [unknown, second, first]):
        rows = [
            row
            for row in project_bridges(targets, sources())["bridges"]
            if row["session_id"] is not None
        ]
        assert {row["project"] for row in rows} == {"demo", "other-project"}
        assert len(rows) == 2
        assert all(row["control"] == "registered" for row in rows)


@pytest.mark.parametrize("worktree", [None, "workspace-a"])
@pytest.mark.parametrize("native_backed", [False, True])
def test_failed_discovery_retains_only_native_backed_managed_worktrees(
    worktree: str | None,
    native_backed: bool,
) -> None:
    target = managed("native-a", worktree=worktree)
    target["session"].update(status="stopped", session_live=None)
    if not native_backed:
        target["session"]["acp_session_id"] = None
        target["session"]["durable_session_id"] = target["session"]["session_id"]
    evidence = sources()
    evidence["worktrees"] = {"freshness": "unknown", "completeness": "incomplete"}
    result = project_bridges([target], evidence, project="demo")
    if not worktree or not native_backed:
        assert result["bridges"] == []
    else:
        (row,) = result["bridges"]
        assert row["session_id"] == "native-a"
        assert row["worktree_id"] == worktree
        assert row["status"] == "dormant"
        assert row["live"] is False
    assert result["complete"] is False


@pytest.mark.parametrize("provider", ["codespace", "container", "ssh"])
def test_registration_venue_supplies_namespace_without_an_agent_name(provider: str) -> None:
    target = registration(workspace())
    raw = deepcopy(target["registration"])
    raw.update(
        worktree_id=None,
        registered_at=1.0,
        updated_at=1.0,
        venue=json.dumps(
            {
                "kind": provider,
                "target": "example-venue",
                "mux_session_name": "example-mux",
            }
        ),
    )
    target["registration"] = _to_info(raw).model_dump(mode="json")
    (row,) = project_bridges([target], sources(), namespace=provider)["bridges"]
    assert row["namespace"] == provider
    assert row["session_id"] == "native-a"
    assert row["control"] == "registered"
