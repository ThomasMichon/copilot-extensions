"""One owner-backed bridge view, independent of registration and hosting mode."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge.bridge_inventory import inventory, project_bridges


def sources() -> dict[str, Any]:
    return {
        "managed": {"availability": "available"},
        "session_host": {"availability": "available"},
        "extension": {"availability": "available"},
        "worktrees": {"completeness": "complete", "freshness": "fresh"},
    }


def workspace(
    handle: str = "workspace-a",
    *,
    project: str = "demo",
    machine: str = "host-a",
    count: int = 1,
    live: bool | None = True,
) -> dict[str, Any]:
    return {
        "kind": "worktree",
        "handle": handle,
        "owner": f"{project}@{machine}",
        "project": project,
        "worktree": {
            "id": handle,
            "machine": machine,
            "path": f"/work/{machine}/{handle}",
            "kind": "worktree",
            "session_count": count,
            "mux_session": live is True,
            "session_bound_live": live,
            "interface": "cli",
            "status": "active",
            "title": "Finish the assigned goal",
            "summary": "Validation is pending",
            "activity": "Waiting for review",
            "follow_up": True,
        },
    }


def registration(item: dict[str, Any]) -> dict[str, Any]:
    row = item["worktree"]
    return {
        "kind": "live",
        "handle": "native-a",
        "sources": ["extension"],
        "registration": {
            "session_id": "native-a",
            "worktree_id": row["id"],
            "repo": item["project"],
            "machine": row["machine"],
            "cwd": row["path"],
            "status": "live",
            "turn_state": "idle",
            "driven_by": "controller-a",
        },
    }


def test_running_worktree_survives_missing_registration() -> None:
    report = project_bridges([workspace()], sources(), project="demo")
    (row,) = report["bridges"]
    assert row["handle"] == "workspace-a"
    assert row["status"] == "running"
    assert row["live"] is True
    assert row["control"] == "unavailable"
    assert row["control_reason"] == "registration_missing"
    assert row["hosting"] == "mux"
    assert row["title"] == "Finish the assigned goal"
    assert row["disposition"] == {
        "follow_up": True,
        "summary": "Validation is pending",
        "activity": "Waiting for review",
    }


def test_observed_copilot_survives_an_unindexed_first_session() -> None:
    (row,) = project_bridges([workspace(count=0)], sources(), project="demo")["bridges"]
    assert row["status"] == "running"
    assert row["session_count"] == 1
    assert row["session_id"] is None
    assert row["control"] == "unavailable"


def test_warm_mux_without_copilot_does_not_make_retained_history_running() -> None:
    item = workspace(live=False)
    item["worktree"]["mux_session"] = True
    (row,) = project_bridges([item], sources(), project="demo")["bridges"]
    assert row["status"] == "dormant"
    assert row["live"] is False
    assert row["hosting"] == "mux"


def test_dormant_session_history_is_a_bridge_but_empty_workspace_is_not() -> None:
    report = project_bridges(
        [workspace(count=2, live=False), workspace("empty", count=0, live=False)],
        sources(),
        project="demo",
    )
    (row,) = report["bridges"]
    assert row["session_count"] == 2
    assert row["status"] == "dormant"
    assert row["live"] is False
    assert row["control"] == "unavailable"


def test_live_registration_joins_its_workspace_instead_of_duplicating_it() -> None:
    item = workspace()
    report = project_bridges([registration(item), item], sources(), project="demo")
    (row,) = report["bridges"]
    assert row["handle"] == "workspace-a"
    assert row["session_id"] == "native-a"
    assert row["control"] == "registered"
    assert row["controller"] == "controller-a"
    assert set(row["sources"]) == {"worktrees", "extension"}


@pytest.mark.parametrize("different_owner", [False, True])
def test_workspace_project_is_not_replaced_by_a_member_repository_name(
    different_owner: bool,
) -> None:
    item = workspace()
    native = registration(item)
    native["registration"]["repo"] = "member-repository"
    if different_owner:
        native["registration"]["project"] = "another-owner"
    (row,) = project_bridges([item, native], sources(), project="demo")["bridges"]
    assert row["project"] == "demo"
    assert row["control"] == ("unavailable" if different_owner else "registered")
    assert row["session_id"] == (None if different_owner else "native-a")


def test_registration_can_prove_first_session_before_roster_count_advances() -> None:
    item = workspace(count=0)
    (row,) = project_bridges([item, registration(item)], sources(), project="demo")["bridges"]
    assert row["session_count"] == 1
    assert row["session_id"] == "native-a"


def test_failed_creation_without_native_history_remains_only_a_placement() -> None:
    item = workspace(count=0, live=False)
    failed = {
        "kind": "managed",
        "handle": "failed-control",
        "session": {
            "session_id": "failed-control",
            "project": "demo",
            "worktree_id": "workspace-a",
            "target_type": "local",
            "target_dir": item["worktree"]["path"],
            "status": "failed",
        },
    }
    assert project_bridges([item, failed], sources(), project="demo")["bridges"] == []


def test_equal_worktree_handles_on_different_machines_remain_distinct() -> None:
    first, second = workspace(), workspace(machine="host-b")
    rows = project_bridges(
        [first, second, registration(second)],
        sources(),
        project="demo",
    )["bridges"]
    assert len(rows) == 2
    assert {row["machine"] for row in rows} == {"host-a", "host-b"}
    assert next(row for row in rows if row["machine"] == "host-a")["control"] == "unavailable"
    assert next(row for row in rows if row["machine"] == "host-b")["control"] == "registered"


def test_caller_affinity_does_not_become_creator_provenance() -> None:
    item = workspace(live=None)
    managed = {
        "kind": "managed",
        "handle": "control-a",
        "session": {
            "session_id": "control-a",
            "durable_session_id": "native-a",
            "worktree_id": "workspace-a",
            "project": "demo",
            "target_dir": item["worktree"]["path"],
            "target_type": "local",
            "status": "running",
            "liveness": "active",
            "mode": "acp",
            "caller_id": "later-caller",
            "caller_session_id": "original-creator",
        },
    }
    (row,) = project_bridges([item, managed], sources(), project="demo")["bridges"]
    assert row["hosting"] == "acp"
    assert row["session_id"] == "native-a"
    assert row["caller_worktree"] == "later-caller"
    assert row["creator_session_id"] == "original-creator"
    assert row["creator_worktree"] is None


def test_only_live_anchor_conversations_appear_in_default_inventory() -> None:
    live = {
        "kind": "live",
        "handle": "anchor-live",
        "registration": {
            "session_id": "anchor-live",
            "repo": "demo",
            "machine": "host-a",
            "cwd": "/work/demo",
            "status": "live",
        },
    }
    dormant = {
        "kind": "managed",
        "handle": "anchor-old",
        "session": {
            "session_id": "anchor-old",
            "project": "demo",
            "target_dir": "/work/demo",
            "status": "stopped",
            "mode": "acp",
        },
    }
    (row,) = project_bridges([live, dormant], sources(), project="demo")["bridges"]
    assert row["handle"] == "anchor-live"
    assert row["location_kind"] == "anchor"


def test_project_machine_namespace_and_live_filters_share_the_same_view() -> None:
    external = {
        "kind": "managed",
        "handle": "remote-a",
        "session": {
            "session_id": "remote-a",
            "durable_session_id": "native-remote",
            "project": "demo",
            "worktree_id": "remote-workspace",
            "agent_name": "codespace:example",
            "target_type": "ssh",
            "target_host": "venue-a",
            "machine": "venue-a",
            "target_dir": "/work/demo",
            "status": "running",
            "liveness": "active",
            "mode": "acp",
        },
    }
    report = project_bridges(
        [workspace(), workspace(project="other"), external],
        sources(),
        project="demo",
        machine="venue-a",
        namespace="codespace",
        live=True,
    )
    (row,) = report["bridges"]
    assert row["handle"] == "remote-workspace"
    assert row["hosting"] == "acp"
    assert row["transport"] == "ssh"
    assert row["namespace"] == "codespace"


def test_unavailable_discovery_is_not_a_complete_empty_result() -> None:
    evidence = sources()
    evidence["worktrees"] = {"completeness": "incomplete", "freshness": "unknown"}
    report = project_bridges([], evidence, project="demo")
    assert report["bridges"] == []
    assert report["complete"] is False
    assert report["sources"]["worktrees"] == evidence["worktrees"]


def test_missing_liveness_evidence_stays_unknown_and_is_not_live() -> None:
    item = workspace(live=None)
    report = project_bridges([item], sources(), project="demo")
    assert report["bridges"][0]["live"] is None
    assert report["bridges"][0]["status"] == "retained"
    assert project_bridges([item], sources(), project="demo", live=True)["bridges"] == []


def test_unverified_remote_host_index_does_not_become_observed_running_execution() -> None:
    item = workspace(live=None)
    host = {
        "kind": "session_host",
        "handle": "native-host",
        "sources": ["session_host"],
        "registration": {
            "session_id": "native-host",
            "worktree_id": "workspace-a",
            "project": "demo",
            "machine": "host-a",
            "cwd": item["worktree"]["path"],
            "boundary": "remote",
            "liveness": "unverified",
        },
    }
    (row,) = project_bridges([item, host], sources(), project="demo")["bridges"]
    assert row["live"] is None
    assert row["status"] == "retained"
    assert row["transport"] == "remote"
    assert row["control_reason"] == "liveness_unknown"
    assert project_bridges([item, host], sources(), project="demo", live=True)["bridges"] == []


def test_registration_alone_does_not_invent_mux_or_transport_metadata() -> None:
    (row,) = project_bridges([registration(workspace())], sources(), project="demo")["bridges"]
    assert row["hosting"] == "cli"
    assert row["transport"] == "unknown"


def test_registration_only_conversation_keeps_its_cached_progress_without_hydration() -> None:
    native = registration(workspace())
    native["registration"]["latest_progress"] = {
        "phase": "validation",
        "summary": "Checking results",
    }
    (row,) = project_bridges([native], sources(), project="demo")["bridges"]
    assert row["disposition"]["activity"] == "Checking results"


def test_standalone_host_joins_its_observed_workspace_without_registration() -> None:
    item = workspace(live=None)
    host = {
        "kind": "session_host",
        "handle": "native-host",
        "sources": ["daemon-instance"],
        "registration": {
            "session_id": "native-host",
            "machine": "host-a",
            "cwd": item["worktree"]["path"],
            "boundary": "local",
            "liveness": "observed",
        },
    }
    (row,) = project_bridges([item, host], sources(), project="demo", live=True)["bridges"]
    assert row["handle"] == "workspace-a"
    assert row["hosting"] == "session_host"
    assert row["status"] == "running"
    assert row["control"] == "unavailable"
    assert set(row["sources"]) == {"worktrees", "daemon-instance"}


def test_shared_directory_without_a_unique_project_owner_does_not_guess_assignment() -> None:
    first, second = workspace(live=None), workspace(project="other", live=None)
    host = {
        "kind": "session_host",
        "handle": "native-host",
        "registration": {
            "session_id": "native-host",
            "machine": "host-a",
            "cwd": first["worktree"]["path"],
            "boundary": "local",
            "liveness": "observed",
        },
    }
    rows = project_bridges([first, second, host], sources())["bridges"]
    assert len(rows) == 3
    (unassigned,) = [row for row in rows if row["project"] is None]
    assert unassigned["handle"] == "native-host"


def test_projection_does_not_change_owner_records() -> None:
    targets = [workspace()]
    evidence = sources()
    original = deepcopy((targets, evidence))
    project_bridges(targets, evidence, project="demo")
    assert (targets, evidence) == original


def test_anchor_owner_history_is_not_a_dormant_worktree_bridge() -> None:
    item = workspace(live=False)
    item["worktree"]["kind"] = "anchor"
    assert project_bridges([item], sources(), project="demo")["bridges"] == []


def test_conflicting_native_observations_do_not_claim_one_controllable_head() -> None:
    item = workspace()
    first, second = registration(item), registration(item)
    second["registration"]["session_id"] = "different-native"
    result = project_bridges([item, first, second], sources(), project="demo")
    (row,) = result["bridges"]
    assert row["session_id"] is None
    assert row["control"] == "unavailable"
    assert row["control_reason"] == "identity_ambiguous"
    assert result["complete"] is False


def test_windows_scope_join_is_lexical_and_does_not_depend_on_the_reader_os() -> None:
    item = workspace()
    item["worktree"]["path"] = "C:\\Work\\Demo"
    native = registration(item)
    native["registration"]["cwd"] = "c:\\work\\demo\\"
    (row,) = project_bridges([item, native], sources(), project="demo")["bridges"]
    assert row["session_id"] == "native-a"


@pytest.mark.parametrize(
    "selector",
    ["workspace-a", "native-a", "control-a"],
)
def test_common_selector_joins_workspace_native_and_managed_aliases(selector: str) -> None:
    from agent_bridge.bridge_inventory import select_bridge

    item = workspace()
    managed = {
        "kind": "managed",
        "handle": "control-a",
        "session": {
            "session_id": "control-a",
            "durable_session_id": "native-a",
            "worktree_id": "workspace-a",
            "project": "demo",
            "target_dir": item["worktree"]["path"],
            "status": "idle",
            "mode": "acp",
        },
    }
    report = project_bridges([item, managed, registration(item)], sources(), project="demo")
    original = deepcopy(report)
    selected = select_bridge(report, selector)
    assert selected["bridge"]["session_id"] == "native-a"
    assert selected["bridge"]["worktree_id"] == "workspace-a"
    assert selected["complete"] is True
    assert report == original


@pytest.mark.parametrize("selector", ["workspace-a", "native-a", "different-native"])
def test_common_selector_rejects_conflicting_heads_even_by_observed_native_id(
    selector: str,
) -> None:
    from agent_bridge.bridge_inventory import BridgeSelectionError, select_bridge

    item = workspace()
    other = registration(item)
    other["registration"]["session_id"] = "different-native"
    report = project_bridges([item, registration(item), other], sources(), project="demo")
    with pytest.raises(BridgeSelectionError) as refused:
        select_bridge(report, selector)
    assert (refused.value.status, refused.value.reason) == (409, "ambiguous_bridge_selector")


def test_common_selector_never_guesses_between_projects_or_creates_a_potential_target() -> None:
    from agent_bridge.bridge_inventory import BridgeSelectionError, select_bridge

    first, second = workspace(), workspace(project="other")
    report = project_bridges([first, second], sources())
    with pytest.raises(BridgeSelectionError) as refused:
        select_bridge(report, "workspace-a")
    assert refused.value.status == 409
    scoped = project_bridges([first, second], sources(), project="demo")
    assert select_bridge(scoped, "workspace-a")["bridge"]["project"] == "demo"
    with pytest.raises(BridgeSelectionError) as absent:
        select_bridge(scoped, "workspace")
    assert absent.value.status == 404


@pytest.mark.parametrize("complete", [False, True])
def test_common_selector_distinguishes_unavailable_evidence_from_absence(complete: bool) -> None:
    from agent_bridge.bridge_inventory import BridgeSelectionError, select_bridge

    report = {"version": 1, "bridges": [], "sources": {}, "complete": complete}
    with pytest.raises(BridgeSelectionError) as refused:
        select_bridge(report, "empty-workspace")
    assert refused.value.status == (404 if complete else 503)


def test_common_selector_preserves_partial_positive_and_dormant_assignment() -> None:
    from agent_bridge.bridge_inventory import select_bridge

    item = workspace(live=False)
    item["worktree"]["last_session_id"] = "native-retained"
    evidence = sources()
    evidence["extension"] = {"availability": "unavailable"}
    report = project_bridges([item], evidence, project="demo")
    selected = select_bridge(report, "native-retained")
    assert selected["bridge"]["status"] == "dormant"
    assert selected["bridge"]["control"] == "unavailable"
    assert selected["complete"] is False
    assert selected["sources"] == evidence


@pytest.mark.parametrize("key", ["observed_session_ids", "identity_aliases"])
def test_common_selector_does_not_substring_match_a_malformed_alias_collection(key: str) -> None:
    from agent_bridge.bridge_inventory import BridgeSelectionError, select_bridge

    report = project_bridges([workspace()], sources(), project="demo")
    report["bridges"][0][key] = "native-a"
    with pytest.raises(BridgeSelectionError) as refused:
        select_bridge(report, "native")
    assert refused.value.status == 404


@pytest.mark.asyncio
async def test_backend_keeps_dormant_managed_metadata_without_hydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import target_inventory
    from agent_bridge.routes import sessions

    item = workspace(live=False)

    async def current(_request: Any, *, refresh: bool = False) -> dict[str, Any]:
        assert refresh is False
        return {"version": 1, "targets": [item], "sources": sources()}

    retained = SimpleNamespace(session_id="control-old")
    manager = SimpleNamespace(list_sessions=lambda: [retained])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=manager)))
    monkeypatch.setattr(target_inventory, "inventory", current)
    monkeypatch.setattr(
        sessions,
        "_session_info",
        lambda _s, _m: SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "session_id": "control-old",
                "durable_session_id": "native-old",
                "project": "demo",
                "worktree_id": "workspace-a",
                "target_dir": item["worktree"]["path"],
                "status": "stopped",
                "mode": "acp",
                "caller_id": "creator-worktree",
            },
        ),
    )
    (row,) = (await inventory(request, project="demo"))["bridges"]
    assert row["session_id"] == "native-old"
    assert row["status"] == "dormant"
    assert row["caller_worktree"] == "creator-worktree"


@pytest.mark.asyncio
@pytest.mark.parametrize("local_managed", [False, True])
async def test_machine_filter_uses_local_owner_evidence_not_the_observing_daemon(
    monkeypatch: pytest.MonkeyPatch,
    local_managed: bool,
) -> None:
    from agent_bridge import target_inventory

    item = {
        "kind": "managed" if local_managed else "live",
        "handle": "control-a" if local_managed else "native-a",
        "session" if local_managed else "registration": {
            "session_id": "control-a" if local_managed else "native-a",
            "durable_session_id": "native-a",
            "project": "demo",
            "repo": "demo",
            "target_type": "local",
            "target_dir": "/work/demo",
            "cwd": "/work/demo",
            "status": "running" if local_managed else "live",
            "liveness": "active",
            "mode": "acp",
        },
    }
    original = deepcopy(item)

    async def current(_request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"version": 1, "targets": [item], "sources": sources()}

    monkeypatch.setattr(target_inventory, "inventory", current)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_manager=SimpleNamespace(list_sessions=lambda: []),
                resolver=SimpleNamespace(_local_machine=SimpleNamespace(key="host-a")),
            )
        )
    )
    (row,) = (await inventory(request, project="demo"))["bridges"]
    assert row["machine"] == ("host-a" if local_managed else None)
    assert row["route"]["host"] == "host-a"
    selected = (await inventory(request, project="demo", machine="host-a"))["bridges"]
    assert len(selected) == int(local_managed)
    assert item == original
