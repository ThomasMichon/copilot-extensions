"""Reporter names never select a fleet command destination."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import delegate_cli as delegates
from agent_worktrees import list_views_cli as fleet
from agent_worktrees import tracking


def reporter(name="current", environment="wsl"):
    return {
        "version": 1, "status": "resolved", "hostname": "observed-host",
        "machine": name, "environment": environment, "execution_machine": name + "-slot",
    }


def host(rows, *, context=None, machine="configured", environment="wsl"):
    value = {
        "machine": machine, "env": environment, "reachable": True,
        fleet.PROBE_STARTED_KEY: time.monotonic(),
        "projection": {
            "quality": "current",
            "freshness": {"refreshed_age_s": 0.0, "bound_s": 90.0},
            "roster_count": len(rows), "selected_count": len(rows),
        },
        "worktrees": rows,
    }
    if context is not None:
        value["reporting_context"] = context
    return value


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_probe_retains_reporter_without_changing_configured_source(monkeypatch, environment):
    rows = [{"id": "opaque-workspace", "machine": "old", "platform": "linux", "repo": "example"}]
    original = copy.deepcopy(rows)
    context = reporter(environment=environment)
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({"worktrees": rows, "reporting_context": context}), stderr="",
    ))
    result = fleet._probe_host(
        "configured", "linux", "configured-alias", "bash", True,
        project="example", extra_args=["--passive"], timeout=1,
    )
    delegates.annotate_fleet_hosts([result])
    assert result["reporting_context"] == context
    assert (result["machine"], result["env"]) == ("configured", "linux")
    row, = result["worktrees"]
    assert row["execution_machine"] == "current-slot" and row["platform"] == environment
    assert row["id"] == "opaque-workspace" and row["repo"] == "example"
    assert rows == original


@pytest.mark.parametrize("context", [
    None, {}, {"version": True, "status": "unavailable"},
    {**reporter(), "environment": []}, {**reporter(), "execution_machine": ""},
    {**reporter(), "hostname": "\ud800"},
])
def test_malformed_reporter_is_a_source_gap_not_an_authoritative_empty_roster(monkeypatch, context):
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({
            "worktrees": [{"id": "old"}], "reporting_context": context,
        }), stderr="",
    ))
    result = fleet._probe_host(
        "configured", "wsl", "alias", "bash", True,
        project="example", extra_args=[], timeout=1,
    )
    assert result["reachable"] is False
    assert "reporting context" in result["error"]
    assert not delegates.host_roster_is_current(result)


def test_actual_stdout_rename_and_withdrawal_leave_source_and_stored_rows_unchanged(monkeypatch):
    stored = [{"id": "opaque", "machine": "old", "platform": "linux", "repo": "example"}]
    original = copy.deepcopy(stored)
    for context, expected in (
        (reporter("first"), "first-slot"), (reporter("renamed"), "renamed-slot"),
        ({"version": 1, "status": "unavailable"}, ""),
    ):
        body = {"worktrees": stored, "reporting_context": context}
        monkeypatch.setattr(fleet, "_local_project_argv", lambda _: [
            sys.executable, "-c", "print(" + repr(json.dumps(body)) + ")",
        ])
        result = fleet._probe_host(
            "configured", "wsl", "alias", "bash", True,
            project="example", extra_args=["--passive"], timeout=5,
        )
        delegates.annotate_fleet_hosts([result])
        assert result["reachable"] is True
        assert result["worktrees"][0].get("execution_machine", "") == expected
        assert result["worktrees"][0]["machine"] == context.get("machine", "")
        assert (result["machine"], result["env"]) == ("configured", "wsl")
        assert stored == original


def test_missing_reporting_preserves_legacy_rows_but_unavailable_removes_the_entire_tuple():
    row = {
        "id": "opaque", "machine": "old", "platform": "wsl",
        "machine_identity_version": 1, "environment": "wsl", "execution_machine": "old-slot",
    }
    legacy = host([row])
    delegates.annotate_fleet_hosts([legacy])
    assert legacy["worktrees"][0] == row
    unavailable = host([row], context={"version": 1, "status": "unavailable"})
    delegates.annotate_fleet_hosts([unavailable])
    assert unavailable["worktrees"] == [{"id": "opaque", "machine": "", "platform": ""}]
    assert "availability" not in unavailable
    assert delegates.host_roster_is_current(unavailable)


@pytest.mark.parametrize("context", [reporter("renamed"), {"version": 1, "status": "unavailable"}])
def test_finalization_uses_containing_source_not_reported_names(monkeypatch, context):
    source = host([{"id": "opaque", "machine": "old", "platform": "linux"}], context=context)
    delegates.annotate_fleet_hosts([source])
    calls = []
    monkeypatch.setattr(delegates.subprocess, "run", lambda argv, **kwargs: (
        calls.append(argv) or SimpleNamespace(returncode=0, stdout='{"success":true}', stderr="")
    ))
    result = delegates._finalize_target(
        source["worktrees"][0], project="example", timeout=1, source_key=("configured", "wsl"),
        host_index={
            ("configured", "wsl"): ("original-alias", "bash", False),
            ("renamed-slot", "wsl"): ("wrong-alias", "bash", False),
        },
    )
    assert result["ok"] is True
    assert calls[0][5] == "original-alias"
    assert result["source"] == {"machine": "configured", "env": "wsl"}


def test_qualified_caller_never_falls_back_to_another_sources_matching_id():
    caller = {"id": "parent", "machine": "other", "repo": "example", "status": "finalized"}
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("intended", "example", "parent"),
    }
    delegates.annotate_delegate_graph([caller, child])
    assert child["caller_state"]["state"] == "unresolved"
    assert child["delegate_finalizable"] is False


def test_legacy_unusable_name_cannot_crash_or_resolve_a_qualified_caller():
    caller = {"id": "parent", "machine": [], "repo": "example", "status": "finalized"}
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("intended", "example", "parent"),
    }
    delegates.annotate_delegate_graph([caller, child])
    assert child["caller_state"]["state"] == "unresolved"
    assert child["delegate_finalizable"] is False


def test_duplicate_reporter_slots_cannot_prove_a_missing_qualified_caller_gone():
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("same-slot", "example", "parent"),
    }
    hosts = [
        host([], context=reporter("same"), machine="route-a"),
        host([], context=reporter("same"), machine="route-b"),
        host([child], machine="worker"),
    ]
    delegates.annotate_fleet_hosts(hosts)
    assert hosts[2]["worktrees"][0]["caller_state"]["state"] == "unknown"
    assert hosts[2]["worktrees"][0]["delegate_finalizable"] is False


def test_selected_project_inventory_cannot_prove_a_foreign_project_caller_gone():
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("parent-slot", "other-project", "parent"),
    }
    hosts = [host([], context=reporter("parent")), host([child], machine="worker")]
    delegates.annotate_fleet_hosts(hosts)
    assert hosts[1]["worktrees"][0]["caller_state"]["state"] == "unknown"
    assert hosts[1]["worktrees"][0]["delegate_finalizable"] is False


def test_freshness_recheck_cannot_substitute_a_different_sources_matching_worktree_id():
    parents = host([
        {"id": "done", "repo": "example", "status": "finalized"},
        {"id": "active", "repo": "example", "status": "active"},
    ], context=reporter("parent"))
    first = host([{"id": "same", "repo": "example", "caller_worktree": "done"}], machine="route-a")
    second = host([{"id": "same", "repo": "example", "caller_worktree": "active"}], machine="route-b")
    hosts = delegates.annotate_fleet_hosts([parents, first, second])
    assert delegates._still_finalizable(
        hosts, second["worktrees"][0], source_key=("route-b", "wsl"),
    ) is False
    assert delegates._still_finalizable(
        hosts, first["worktrees"][0], source_key=("route-a", "wsl"),
    ) is True


@pytest.mark.parametrize("context", [reporter("renamed"), {"version": 1, "status": "unavailable"}])
def test_execute_retains_the_original_source_after_reporter_rename_or_withdrawal(monkeypatch, capsys, context):
    hosts = [
        host([{"id": "parent", "repo": "example", "status": "finalized"}], context=reporter("parent")),
        host([{
            "id": "child", "repo": "example", "status": "active",
            "caller_worktree": tracking.format_claim_ref("parent-slot", "example", "parent"),
        }], context=context, machine="original"),
    ]
    delegates.annotate_fleet_hosts(hosts)
    monkeypatch.setattr(delegates, "_fleet_snapshot", lambda **kwargs: hosts)
    monkeypatch.setattr(delegates.cfg, "project_name", lambda: "example")
    monkeypatch.setattr(delegates.cfg, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(fleet, "_fleet_targets", lambda _: [
        ("original", "wsl", "original-alias", "bash", False),
        ("renamed-slot", "wsl", "wrong-alias", "bash", False),
    ])
    calls = []
    monkeypatch.setattr(delegates.subprocess, "run", lambda argv, **kwargs: (
        calls.append(argv) or SimpleNamespace(returncode=0, stdout='{"success":true}', stderr="")
    ))
    assert delegates.run_delegates(["--json", "--execute"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["changed"]) == 1
    assert calls[0][5] == "original-alias"
    assert result["changed"][0]["source"] == {"machine": "original", "env": "wsl"}


def test_qualified_physical_caller_resolves_an_actual_guest_row_but_not_ambiguous_absence():
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("physical", "example", "parent"),
    }
    native = reporter("physical", "windows")
    guest = {**reporter("physical", "wsl"), "execution_machine": "guest-slot"}
    hosts = [
        host([], context=native, machine="native-route"),
        host([{"id": "parent", "repo": "example", "status": "finalized"}],
             context=guest, machine="guest-route"),
        host([child], machine="worker"),
    ]
    delegates.annotate_fleet_hosts(hosts)
    assert hosts[2]["worktrees"][0]["caller_state"]["state"] == "resolved"
    assert hosts[2]["worktrees"][0]["caller_state"]["execution_machine"] == "guest-slot"
    hosts[1]["worktrees"] = []
    hosts[1]["projection"].update(roster_count=0, selected_count=0)
    delegates.annotate_fleet_hosts(hosts)
    assert hosts[2]["worktrees"][0]["caller_state"]["state"] == "unresolved"
    assert hosts[2]["worktrees"][0]["delegate_finalizable"] is False


@pytest.mark.parametrize("native_parent", [False, True])
def test_withdrawn_guest_cannot_hide_an_active_qualified_caller_during_execution_recheck(
    native_parent, monkeypatch, capsys,
):
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("physical", "example", "parent"),
    }
    native = host(
        [{"id": "parent", "repo": "example", "status": "finalized"}] if native_parent else [],
        context={**reporter("physical", "windows"), "execution_machine": "native-slot"},
        machine="native-route", environment="windows",
    )
    guest = host(
        [{"id": "parent", "repo": "example", "status": "active"}],
        context={"version": 1, "status": "unavailable"}, machine="guest-route",
    )
    hosts = delegates.annotate_fleet_hosts([native, guest, host([child], machine="worker")])
    target = hosts[2]["worktrees"][0]
    assert target["caller_state"]["state"] == "unknown"
    assert target["delegate_finalizable"] is False
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is False
    assert hosts[1]["worktrees"][0].get("machine") == ""
    assert "execution_machine" not in hosts[1]["worktrees"][0]
    monkeypatch.setattr(delegates, "_fleet_snapshot", lambda **kwargs: hosts)
    monkeypatch.setattr(delegates.cfg, "project_name", lambda: "example")
    monkeypatch.setattr(delegates.cfg, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(fleet, "_fleet_targets", lambda _: [])
    monkeypatch.setattr(delegates, "_finalize_target", lambda *a, **k: pytest.fail("finalized"))
    assert delegates.run_delegates(["--json", "--execute"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["eligible"] == [] and result["executed"] == []


@pytest.mark.parametrize("incomplete", [False, True])
def test_unnamed_source_absence_is_useful_only_when_its_inventory_is_current(incomplete):
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("known-slot", "example", "parent"),
    }
    unnamed = host(
        [{"id": "unrelated", "repo": "example", "status": "active"}],
        context={"version": 1, "status": "unavailable"}, machine="unnamed",
    )
    if incomplete:
        unnamed["availability"] = {"status": "advisory", "reason": "saved roster"}
    hosts = delegates.annotate_fleet_hosts([
        host([], context=reporter("known")), unnamed, host([child], machine="worker"),
    ])
    target = hosts[2]["worktrees"][0]
    assert target["caller_state"]["state"] == ("unknown" if incomplete else "gone")
    assert target["delegate_finalizable"] is (not incomplete)
    assert delegates._still_finalizable(
        hosts, target, source_key=("worker", "wsl"),
    ) is (not incomplete)


def test_malformed_reporter_gap_remains_unnamed_during_graph_and_execution_recheck(monkeypatch):
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({"worktrees": [], "reporting_context": None}), stderr="",
    ))
    malformed = fleet._probe_host(
        "bad-source", "wsl", "alias", "bash", True,
        project="example", extra_args=[], timeout=1,
    )
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("known-slot", "example", "parent"),
    }
    hosts = delegates.annotate_fleet_hosts([
        host([], context=reporter("known")), malformed, host([child], machine="worker"),
    ])
    target = hosts[2]["worktrees"][0]
    assert target["caller_state"]["state"] == "unknown"
    assert target["delegate_finalizable"] is False
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is False


@pytest.mark.parametrize("unnamed", [False, True])
def test_filtered_current_inventory_cannot_prove_an_unobserved_caller_gone(unnamed):
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("known-slot", "example", "parent"),
    }
    known = host([], context=reporter("known"))
    partial = host(
        [], context={"version": 1, "status": "unavailable"} if unnamed else reporter("known"),
        machine="partial",
    )
    partial["projection"].update(roster_count=2, selected_count=0)
    hosts = [known, partial] if unnamed else [partial]
    hosts = delegates.annotate_fleet_hosts([*hosts, host([child], machine="worker")])
    target = hosts[-1]["worktrees"][0]
    assert target["caller_state"]["state"] == "unknown"
    assert target["delegate_finalizable"] is False
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is False


@pytest.mark.parametrize("counts", [
    {"roster_count": None, "selected_count": 0},
    {"roster_count": False, "selected_count": 0},
    {"roster_count": 0, "selected_count": None},
])
def test_missing_or_malformed_cardinality_does_not_prove_a_caller_absent(counts):
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("known-slot", "example", "parent"),
    }
    known = host([], context=reporter("known"))
    known["projection"].update(counts)
    hosts = delegates.annotate_fleet_hosts([known, host([child], machine="worker")])
    assert hosts[1]["worktrees"][0]["delegate_finalizable"] is False


def test_filtered_current_inventory_can_still_prove_an_observed_caller_terminal():
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("known-slot", "example", "parent"),
    }
    known = host(
        [{"id": "parent", "repo": "example", "status": "finalized"}],
        context=reporter("known"),
    )
    known["projection"].update(roster_count=2, selected_count=1)
    hosts = delegates.annotate_fleet_hosts([known, host([child], machine="worker")])
    target = hosts[1]["worktrees"][0]
    assert target["caller_state"]["state"] == "resolved"
    assert target["delegate_finalizable"] is True
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is True


@pytest.mark.parametrize("coverage", ["current", "advisory", "expired", "partial"])
def test_competing_named_source_must_currently_exclude_a_qualified_caller(coverage):
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("physical", "example", "parent"),
    }
    native = host(
        [{"id": "parent", "repo": "example", "status": "finalized"}],
        context={**reporter("physical", "windows"), "execution_machine": "native-slot"},
        machine="native-route", environment="windows",
    )
    guest = host(
        [], context={**reporter("physical", "wsl"), "execution_machine": "guest-slot"},
        machine="guest-route",
    )
    if coverage == "advisory":
        guest["availability"] = {"status": "advisory", "reason": "saved roster"}
    elif coverage == "expired":
        guest["projection"]["freshness"] = {"refreshed_age_s": 91.0, "bound_s": 90.0}
    elif coverage == "partial":
        guest["projection"].update(roster_count=2, selected_count=0)
    hosts = delegates.annotate_fleet_hosts([native, guest, host([child], machine="worker")])
    target = hosts[2]["worktrees"][0]
    assert target["caller_state"]["state"] == ("resolved" if coverage == "current" else "unknown")
    assert target["delegate_finalizable"] is (coverage == "current")
    assert delegates._still_finalizable(
        hosts, target, source_key=("worker", "wsl"),
    ) is (coverage == "current")
    if coverage == "current":
        guest["projection"]["freshness"] = {"refreshed_age_s": 91.0, "bound_s": 90.0}
        assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is False


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
@pytest.mark.parametrize("selector", ["qualified-missing", "qualified-terminal", "bare-terminal"])
def test_failed_probe_is_unknown_coverage_not_a_known_legacy_reporter(monkeypatch, failure, selector):
    def failed(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 1)
        return SimpleNamespace(returncode=1, stdout="", stderr="fixture unavailable")

    monkeypatch.setattr(fleet.subprocess, "run", failed)
    guest = fleet._probe_host(
        "configured-guest", "wsl", "guest-alias", "bash", False,
        project="example", extra_args=["--passive"], timeout=1,
    )
    assert guest["reachable"] is False
    caller = "parent" if selector == "bare-terminal" else tracking.format_claim_ref(
        "physical", "example", "parent",
    )
    child = {"id": "child", "repo": "example", "caller_worktree": caller}
    native = host(
        [] if selector == "qualified-missing" else [
            {"id": "parent", "repo": "example", "status": "finalized"},
        ],
        context={**reporter("physical", "windows"), "execution_machine": "native-slot"},
        machine="native-route", environment="windows",
    )
    hosts = delegates.annotate_fleet_hosts([native, guest, host([child], machine="worker")])
    target = hosts[2]["worktrees"][0]
    assert target["caller_state"]["state"] == "unknown"
    assert target["delegate_finalizable"] is False
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is False


@pytest.mark.parametrize("configured,native_parent", [
    ("configured-guest", False), ("configured-guest", True), ("physical", True),
])
def test_legacy_labels_cannot_hide_a_conflicting_active_caller(configured, native_parent):
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("physical", "example", "parent"),
    }
    native = host(
        [{"id": "parent", "repo": "example", "status": "finalized"}] if native_parent else [],
        context={**reporter("physical", "windows"), "execution_machine": "native-slot"},
        machine="native-route", environment="windows",
    )
    guest = host(
        [{"id": "parent", "repo": "example", "status": "active", "machine": "obsolete-label"}],
        machine=configured,
    )
    hosts = delegates.annotate_fleet_hosts([native, guest, host([child], machine="worker")])
    target = hosts[2]["worktrees"][0]
    assert target["caller_state"]["state"] == "unknown"
    assert target["delegate_finalizable"] is False
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is False
    assert hosts[1]["worktrees"][0]["machine"] == "obsolete-label"


def test_legacy_tuple_cannot_bind_a_qualified_selector_as_current_identity():
    parent = {
        "id": "parent", "repo": "example", "status": "finalized", "machine": "old-physical",
        "platform": "wsl", "environment": "wsl", "execution_machine": "old-slot",
        "machine_identity_version": 1,
    }
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("old-slot", "example", "parent"),
    }
    hosts = delegates.annotate_fleet_hosts([
        host([parent], machine="configured"), host([child], machine="worker"),
    ])
    target = hosts[1]["worktrees"][0]
    assert target["caller_state"]["state"] == "unknown"
    assert target["delegate_finalizable"] is False
    assert hosts[0]["worktrees"][0] == parent


@pytest.mark.parametrize("native_parent", [False, True])
def test_renamed_live_caller_is_a_conflict_not_absence_from_its_old_named_source(native_parent):
    child = {
        "id": "child", "repo": "example",
        "caller_worktree": tracking.format_claim_ref("original", "example", "parent"),
    }
    original = host(
        [{"id": "parent", "repo": "example", "status": "finalized"}] if native_parent else [],
        context={**reporter("original"), "execution_machine": "original"},
        machine="original-route",
    )
    renamed = host(
        [{"id": "parent", "repo": "example", "status": "active"}],
        context=reporter("renamed"), machine="renamed-route",
    )
    hosts = delegates.annotate_fleet_hosts([original, renamed, host([child], machine="worker")])
    target = hosts[2]["worktrees"][0]
    assert target["caller_state"]["state"] == "unknown"
    assert target["delegate_finalizable"] is False
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is False


def test_successful_complete_legacy_inventory_still_supports_bare_id_status_proof():
    parent = {"id": "parent", "repo": "example", "status": "finalized", "machine": "old"}
    child = {"id": "child", "repo": "example", "caller_worktree": "parent"}
    hosts = delegates.annotate_fleet_hosts([
        host([parent], machine="configured"), host([child], machine="worker"),
    ])
    target = hosts[1]["worktrees"][0]
    assert target["caller_state"]["state"] == "resolved"
    assert target["delegate_finalizable"] is True
    assert delegates._still_finalizable(hosts, target, source_key=("worker", "wsl")) is True
    assert hosts[0]["worktrees"][0]["machine"] == "old"
