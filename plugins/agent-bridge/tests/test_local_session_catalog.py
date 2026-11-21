"""Local completion is passive and never substitutes another machine's roster."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import agent_registry, local_session_catalog as catalog
from agent_bridge import provider_process, session_lifecycle_cli
from agent_bridge.session_host import registered_inventory

pytestmark = pytest.mark.contract("agent_bridge.local_session_catalog")


@pytest.fixture
def owners(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "checkout"
    root.mkdir()
    repos = [
        {"name": name, "class": kind, "agent": False, "paths": {"test": str(root)}}
        for name, kind in [("source", "worktree"), ("notes", "knowledge")]
    ]
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        value: dict[str, Any]
        if argv[1:3] == ["repos", "list"]:
            assert argv == ["owner", "repos", "list", "--json", "--strict"]
            value = {"repos": repos, "inventory_authority": "complete"}
        else:
            assert argv[-3:] == ["list", "--json", "--passive"]
            project = argv[2]
            value = {
                "contract_version": 1,
                "projection": {
                    "quality": "current", "observed_at": time.time() - 40,
                    "freshness": {"bound_s": 90, "refreshed_age_s": 40},
                },
                "worktrees": [{
                    "id": "shared-handle", "machine": "local", "path": str(root / project),
                    "status": "active", "branch": "worktree/example",
                }],
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(catalog, "_locally_bound", lambda client: True)
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "test")
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda **kw: ["owner"])
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_environment", lambda: {"PRIVATE": "yes"})
    monkeypatch.setattr(provider_process, "run_captured", run)
    monkeypatch.setattr(registered_inventory, "registered_hosts", lambda: (
        [], {"availability": "available"},
    ))
    return SimpleNamespace(calls=calls, repos=repos, root=root, run=run)


def test_all_registered_repositories_without_agent_authority_or_synthetic_sessions(owners: Any) -> None:
    first = catalog.complete_catalog(object(), {"version": 1, "targets": [], "sources": {}})
    second = catalog.complete_catalog(object(), first)
    assert len(second["targets"]) == 2
    assert {row["project"] for row in second["targets"]} == {"source", "notes"}
    assert all(row["kind"] == "worktree" and row["owner"] is None for row in second["targets"])
    assert all(row["worktree"].get("session_id") is None for row in second["targets"])
    assert all(call[1]["env"] == {"PRIVATE": "yes"} for call in owners.calls)
    assert all(0 < call[1]["timeout"] <= 30 for call in owners.calls)
    assert second["sources"]["local_worktrees"]["availability"] == "available"


@pytest.mark.parametrize("kind", ["managed", "session_host"])
def test_local_completion_does_not_duplicate_a_birth_matched_host(
    owners: Any, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    registration = {
        "host_pid": 123, "host_process_start_time": "original-birth", "cwd": "original-cwd",
        "boundary": "local",
    }
    host = {"kind": "session_host", "registration": registration, "sources": ["daemon-instance"]}
    monkeypatch.setattr(registered_inventory, "registered_hosts", lambda: (
        [host], {"availability": "available"},
    ))
    original = {"kind": kind, "registration": registration, "sources": ["session_host"]}
    result = catalog.complete_catalog(object(), {"targets": [original], "sources": {}})
    assert [row for row in result["targets"] if row["kind"] != "worktree"] == [original]


@pytest.mark.parametrize("difference", [None, "instance_id", "host_pid", "host_process_start_time", "cwd"])
def test_unknown_host_scope_requires_the_same_instance_and_exact_process_identity(
    owners: Any, monkeypatch: pytest.MonkeyPatch, difference: str | None,
) -> None:
    registration = {
        "instance_id": "original-instance", "host_pid": 123,
        "host_process_start_time": "original-birth", "cwd": "", "boundary": "local",
    }
    original = {"kind": "session_host", "registration": registration}
    candidate = {**registration}
    if difference is not None:
        candidate[difference] = 456 if difference == "host_pid" else "different"
    if difference == "cwd":
        registration["cwd"] = "original-cwd"
    monkeypatch.setattr(registered_inventory, "registered_hosts", lambda: (
        [{"kind": "session_host", "registration": candidate}], {"availability": "available"},
    ))
    result = catalog.complete_catalog(object(), {"targets": [original], "sources": {}})
    assert len([row for row in result["targets"] if row["kind"] == "session_host"]) == (
        1 if difference is None else 2
    )


def test_later_repository_read_cannot_keep_an_expired_earlier_observation_current(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.routes import worktree_refresh

    clock = SimpleNamespace(now=1000.0, wall=2000.0)
    timers = SimpleNamespace(monotonic=lambda: clock.now, time=lambda: clock.wall)
    monkeypatch.setattr(catalog, "time", timers)
    monkeypatch.setattr(worktree_refresh, "time", timers)

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ["--project", "notes"]:
            clock.now += 10
            clock.wall += 10
        result: subprocess.CompletedProcess[str] = owners.run(argv, **kwargs)
        if argv[1] == "--project":
            value = json.loads(result.stdout)
            age = 85 if argv[2] == "source" else 40
            value["projection"].update(observed_at=clock.wall - age)
            value["projection"]["freshness"]["refreshed_age_s"] = age
            result.stdout = json.dumps(value)
        return result

    monkeypatch.setattr(provider_process, "run_captured", run)
    result = catalog.complete_catalog(object(), {"targets": [], "sources": {}})
    source, notes = result["targets"]
    assert source["project"] == "source" and notes["project"] == "notes"
    assert source["observation"] == {
        "availability": "unavailable", "reason": "source-stale", "observation_valid": False,
    }
    assert notes["observation"]["observation_valid"] is True
    evidence = result["sources"]["local_worktrees"]
    assert evidence["availability"] == "unavailable"
    assert evidence["projects"]["source"]["reason"] == "source-stale"
    assert evidence["projects"]["notes"]["availability"] == "available"
    assert "valid_until" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["stale", "partial", "timeout", "launcher"])
def test_failed_owner_is_visible_without_losing_other_repository(
    owners: Any, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result: subprocess.CompletedProcess[str] = owners.run(argv, **kwargs)
        if argv[1:3] == ["--project", "source"]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if failure == "launcher":
                raise RuntimeError("failed to resume suspended process")
            value = json.loads(result.stdout)
            if failure == "stale":
                value["projection"]["observed_at"] -= 100
            else:
                value["incomplete"] = True
            result.stdout = json.dumps(value)
        return result

    monkeypatch.setattr(provider_process, "run_captured", run)
    result = catalog.complete_catalog(object(), {"targets": [], "sources": {}})
    expected = ["notes"] if failure in {"timeout", "launcher"} else ["source", "notes"]
    assert [row["project"] for row in result["targets"]] == expected
    if failure not in {"timeout", "launcher"}:
        assert result["targets"][0]["observation"]["observation_valid"] is False
    evidence = result["sources"]["local_worktrees"]
    assert evidence["availability"] == "unavailable"
    assert evidence["projects"]["source"]["availability"] == "unavailable"
    assert evidence["projects"]["notes"]["availability"] == "available"


def test_degraded_owner_rows_remain_visible_without_claiming_completeness(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result: subprocess.CompletedProcess[str] = owners.run(argv, **kwargs)
        if argv[1] == "--project":
            value = json.loads(result.stdout)
            value["availability"] = {"status": "stale", "reason": "cleanup pending"}
            result.stdout = json.dumps(value)
        return result

    monkeypatch.setattr(provider_process, "run_captured", run)
    result = catalog.complete_catalog(object(), {"targets": [], "sources": {}})
    assert len(result["targets"]) == 2
    assert all(row["observation"]["availability"] == "unavailable" for row in result["targets"])
    assert result["sources"]["local_worktrees"]["availability"] == "unavailable"


@pytest.mark.parametrize("document", [
    {"version": 2, "worktrees": []},
    {"contract_version": True, "worktrees": []},
    {"version": 1, "worktrees": [{"path": "no-identity"}]},
    {"version": 1, "worktrees": None},
])
def test_incompatible_or_malformed_rows_are_not_rendered(document: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        catalog._inventory_observation(json.dumps(document))

def test_missing_attributable_owner_never_uses_path_fallback(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def prefix(**kwargs: Any) -> None:
        assert kwargs == {"allow_path_fallback": False}
        return None

    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", prefix)
    result = catalog.complete_catalog(object(), {"targets": [], "sources": {}})
    assert not owners.calls
    assert result["sources"]["local_worktrees"]["availability"] == "unavailable"


def test_registry_launcher_failure_retains_independent_catalog_sources(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv == ["owner", "repos", "list", "--json", "--strict"]
        raise RuntimeError("Windows descendant Job assignment failed before child resume")

    monkeypatch.setattr(provider_process, "run_captured", run)
    independent = {"kind": "managed", "handle": "existing-session", "sources": ["managed"]}
    result = catalog.complete_catalog(object(), {
        "targets": [independent], "sources": {"managed": {"availability": "available"}},
    })
    assert result["targets"] == [independent]
    assert result["sources"]["managed"] == {"availability": "available"}
    assert result["sources"]["local_worktrees"]["availability"] == "unavailable"


@pytest.mark.parametrize("boundary", ["explicit", "forwarded", "different", "missing", "local"])
def test_only_verified_local_endpoint_can_add_machine_local_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str,
) -> None:
    from zdd import routing
    from agent_bridge import config, routing_state

    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    if boundary == "explicit":
        monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://explicit")
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        routing_state, "forwarded_route_base_url",
        lambda root: "http://forwarded" if boundary == "forwarded" else None,
    )
    checks = []

    def endpoint(root: Path, **kwargs: Any) -> SimpleNamespace | None:
        checks.append(kwargs)
        if boundary == "missing":
            return None
        return SimpleNamespace(base_url="http://other" if boundary == "different" else "http://local")

    monkeypatch.setattr(routing, "read_active_endpoint", endpoint)
    client = SimpleNamespace(_base="http://local")
    assert catalog._locally_bound(client) is (boundary == "local")
    assert all(check == {"verify_listener": True} for check in checks)


def test_local_registered_inventory_is_independent_of_agent_enablement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.agent_registry_topology import local_inventory_sources

    monkeypatch.setenv("INVENTORY_CHECKOUT", str(tmp_path))
    repos = [
        {"name": name, "class": kind, "agent": False, "paths": {"test": "$INVENTORY_CHECKOUT"}}
        for name, kind in [
            ("source-only", "worktree"), ("private-state", "knowledge"),
            ("reference", "reference"), ("single", "singleton"),
        ]
    ]
    repos += [
        {"name": "elsewhere", "class": "worktree", "paths": {"other": str(tmp_path)}},
        {"name": "missing", "class": "worktree", "paths": {"test": str(tmp_path / "absent")}},
    ]
    sources = local_inventory_sources(repos, "test", {})
    assert {row.project for row in sources.values()} == {"source-only", "private-state"}
    assert all(row.worktree_discovery and row.host is None for row in sources.values())
    covered = {"real-agent": agent_registry.AgentConfig(name="real-agent", project="source-only")}
    assert {row.project for row in local_inventory_sources(repos, "test", covered).values()} == {
        "private-state",
    }
    covered["real-agent"].worktree_discovery = False
    assert {row.project for row in local_inventory_sources(repos, "test", covered).values()} == {
        "source-only", "private-state",
    }
    named = {
        "reader": agent_registry.AgentConfig(
            name="reader", project="other", aliases=["Inventory:Source-Only"],
        ),
    }
    inventory = local_inventory_sources(repos, "test", named)
    assert "inventory:source-only" not in inventory
    assert {row.project for row in inventory.values()} == {"source-only", "private-state"}


@pytest.mark.parametrize("authority", [None, "partial"])
def test_unqualified_empty_repository_inventory_is_never_an_available_source(
    owners: Any, monkeypatch: pytest.MonkeyPatch, authority: str | None,
) -> None:
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        value: dict[str, Any] = {"repos": []}
        if authority is not None:
            value["inventory_authority"] = authority
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(provider_process, "run_captured", run)
    result = catalog.complete_catalog(object(), {"targets": [], "sources": {}})
    assert calls == [["owner", "repos", "list", "--json", "--strict"]]
    assert result["sources"]["local_worktrees"]["availability"] == "unavailable"


def test_owner_observation_updates_status_and_mux_but_retains_bridge_linkage(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = catalog.complete_catalog(object(), {"targets": [], "sources": {}})["targets"][0]
    original["owner"] = "backend-owner"
    original["worktree"].update({
        "agent_name": "backend-owner", "status": "ended", "mux_session": False,
        "session_id": "managed", "acp_session_id": "native", "durable_session_id": "native",
        "session_status": "running", "session_turn_count": 7, "session_at_rest": False,
        "session_live": True,
    })

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result: subprocess.CompletedProcess[str] = owners.run(argv, **kwargs)
        if argv[1:3] == ["--project", "source"]:
            value = json.loads(result.stdout)
            value["worktrees"][0].update(status="active", mux_session=True)
            result.stdout = json.dumps(value)
        return result

    monkeypatch.setattr(provider_process, "run_captured", run)
    result = catalog.complete_catalog(object(), {"targets": [original], "sources": {}})
    (row,) = [item for item in result["targets"] if item.get("project") == "source"]
    assert row["owner"] == "backend-owner"
    assert row["worktree"]["status"] == "active" and row["worktree"]["mux_session"] is True
    assert row["worktree"]["agent_name"] == "backend-owner"
    assert row["worktree"]["session_id"] == "managed" and row["worktree"]["acp_session_id"] == "native"
    assert row["worktree"]["session_turn_count"] == 7 and row["worktree"]["session_live"] is True
    assert row["observation"]["observation_valid"] is True
