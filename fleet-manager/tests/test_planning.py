import copy
import json
import os
import subprocess
import sys
import time

import pytest

from fleet_manager import PlanError, plan, verify_plan
from fleet_manager.__main__ import main
from fleet_manager.planning import MAX_BYTES, MAX_ITEMS, MAX_PLAN_BYTES, canonical, decode


def inputs():
    template = {
        "schema": "fleet-manager.template", "version": 1, "id": "default", "revision": "r1",
        "execution_hosts": 4,
        "roles": {
            "gateway": {"kind": "service", "required": True, "available": True},
            "index": {"kind": "service", "required": True, "available": True},
            "agent-host": {"kind": "execution", "required": False, "available": True},
        },
    }
    live = {
        "schema": "fleet-manager.inventory", "version": 1, "provider": "test",
        "revision": "i1", "fleet_id": "example", "observed_at": 100, "expires_at": 200,
        "complete": True, "creation_allowed": True, "quota_remaining": 5, "hosts": [],
    }
    return template, None, live


def compute(template, config, live, **kwargs):
    return plan(template, config, live, fleet_id="example", machine_limit=5, now=150, **kwargs)


def host(name, *, owned=True, ready=True, roles=None):
    return {"id": name, "owned": owned, "ready": ready, "roles": roles or {}}


def test_empty_fleet_proposes_services_before_expansion_without_mutating_inputs():
    args = inputs()
    before = copy.deepcopy(args)
    result = compute(*args)
    assert args == before
    assert result["capacity"]["proposed_creations"] == 5
    assert result["capacity"]["service_hosts"] == 1
    assert result["capacity"]["budgeted_execution_hosts"] == 4
    assert result["status"] == "review-required"
    assert not result["mutations_performed"] and result["cleanup_actions"] == []
    assert all(role["state"] == "not-proven-ready" for role in result["roles"])


def test_existing_custom_capabilities_and_unmanaged_hosts_are_never_replaced_or_removed():
    template, _, live = inputs()
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "u1",
        "capacity": {"machine_limit": 2, "execution_hosts": 0},
        "roles": {"index": {"custom-storage": "keep"}, "novel": {"mode": "keep"}},
        "extensions": {"novel-feature": [1, 2]},
    }
    live["hosts"] = [host("owned", roles={"index": "ready"}), host("external", owned=False)]
    result = compute(template, config, live)
    assert result["suggested_config"]["roles"]["index"] == config["roles"]["index"]
    assert result["suggested_config"]["roles"]["novel"] == config["roles"]["novel"]
    assert result["suggested_config"]["extensions"] == config["extensions"]
    assert result["suggested_config"]["capacity"] == config["capacity"]
    assert result["retained_hosts"] == ["external", "owned"]
    assert result["capacity"]["proposed_creations"] == 0
    assert result["effective_machine_limit"] == 2


@pytest.mark.parametrize("quota,expected", [(None, "provider-quota-unknown"), (0, "capacity-unmet")])
def test_no_verified_quota_never_creates_hosts(quota, expected):
    template, config, live = inputs()
    live["quota_remaining"] = quota
    result = compute(template, config, live)
    assert result["capacity"]["proposed_creations"] == 0
    assert expected in result["blockers"]


def test_one_machine_budget_preserves_existing_allocations_and_reports_unmet_expansion():
    template, _, live = inputs()
    live["hosts"] = [host("busy", ready=False)]
    result = plan(template, None, live, fleet_id="example", machine_limit=1, now=150)
    assert result["capacity"]["budgeted_execution_hosts"] == 0
    assert result["capacity"]["proposed_creations"] == 0
    assert "capacity-unmet" in result["blockers"]
    assert result["retained_hosts"] == ["busy"]


@pytest.mark.parametrize("changes", [
    {"complete": False}, {"expires_at": 140}, {"creation_allowed": False},
])
def test_unfresh_or_unadmitted_live_state_cannot_create(changes):
    template, config, live = inputs()
    live.update(changes)
    assert compute(template, config, live)["capacity"]["proposed_creations"] == 0


def test_unsupported_essential_future_role_is_not_ready():
    template, config, live = inputs()
    template["roles"]["agent-chat"] = {"kind": "service", "required": True, "available": False}
    result = compute(template, config, live)
    assert "required-role-unsupported:agent-chat" in result["blockers"]


def test_plan_revalidation_binds_all_snapshots_policy_integrity_and_expiry():
    template, config, live = inputs()
    saved = compute(template, config, live)
    assert verify_plan(saved, template, config, live, fleet_id="example", machine_limit=5, now=160)
    for change in ("inventory", "template", "policy", "expiry", "tamper"):
        t, l, p = copy.deepcopy(template), copy.deepcopy(live), copy.deepcopy(saved)
        limit, now = 5, 160
        if change == "inventory":
            l["quota_remaining"] = 4
        elif change == "template":
            t["revision"] = "r2"
        elif change == "policy":
            limit = 4
        elif change == "expiry":
            now = 200
        else:
            p["capacity"]["proposed_creations"] = 6
        with pytest.raises(PlanError):
            verify_plan(p, t, config, l, fleet_id="example", machine_limit=limit, now=now)


@pytest.mark.parametrize("changes", [
    {"quota_remaining": True}, {"creation_allowed": 1}, {"complete": "yes"},
    {"fleet_id": "other"}, {"observed_at": 170}, {"hosts": [host("same"), host("same")]},
])
def test_invalid_inventory_cannot_become_a_proposal(changes):
    template, config, live = inputs()
    live.update(changes)
    with pytest.raises(PlanError):
        compute(template, config, live)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b" " * (MAX_BYTES + 1)],
                         ids=["duplicate", "nonfinite", "encoding", "oversized"])
def test_bounded_json_refuses_invalid_documents(raw):
    with pytest.raises(PlanError):
        decode(raw)


def test_cli_missing_existing_config_is_not_an_empty_fleet(tmp_path, capsys):
    template, _, live = inputs()
    for name, data in (("template", template), ("inventory", live)):
        (tmp_path / name).write_text(json.dumps(data))
    code = main([
        "plan", "--template", str(tmp_path / "template"), "--inventory", str(tmp_path / "inventory"),
        "--config", str(tmp_path / "missing"), "--fleet-id", "example", "--machine-limit", "5",
    ])
    assert code == 1
    assert capsys.readouterr().out == ""


def test_capacity_uses_real_epoch_times_and_retains_all_owned_hosts():
    template, config, live = inputs()
    live.update(observed_at=1_791_690_000, expires_at=1_791_690_600, quota_remaining=2)
    live["hosts"] = [host("ready"), host("offline", ready=False), host("foreign", owned=False)]
    result = plan(template, config, live, fleet_id="example", machine_limit=5, now=1_791_690_100)
    assert result["capacity"]["owned"] == 2
    assert result["capacity"]["proposed_creations"] == 2
    assert "capacity-unmet" in result["blockers"]
    assert result["retained_hosts"] == ["foreign", "offline", "ready"]


def test_planner_bounds_limits_nested_inputs_and_plan_bytes():
    template, config, live = inputs()
    for limit in (True, 0, 257):
        with pytest.raises(PlanError):
            plan(template, config, live, fleet_id="example", machine_limit=limit, now=150)
    nested = {}
    for _ in range(14):
        nested = {"nested": nested}
    with pytest.raises(PlanError):
        decode(json.dumps(nested).encode())


def test_installed_cli_outputs_bounded_proposal_without_hosting_or_file_writes(tmp_path):
    template, _, live = inputs()
    now = int(time.time())
    live.update(observed_at=now - 1, expires_at=now + 60)
    for name, data in (("template.json", template), ("inventory.json", live)):
        (tmp_path / name).write_text(json.dumps(data))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-m", "fleet_manager", "plan",
         "--template", str(tmp_path / "template.json"),
         "--inventory", str(tmp_path / "inventory.json"),
         "--fleet-id", "example", "--machine-limit", "5"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, **flags,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["schema"] == "fleet-manager.plan"
    assert not output["mutations_performed"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_canonical_equivalent_object_order_produces_identical_plan_and_revalidation():
    template, _, live = inputs()
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "u1",
        "capacity": {"machine_limit": 5, "execution_hosts": 0},
        "roles": {"novel": {"mode": "keep"}, "index": {"custom": True}},
        "template": {"id": "default", "revision": "r1"},
    }
    saved = compute(template, config, live)
    reordered_template = dict(reversed(list(template.items())))
    reordered_template["roles"] = dict(reversed(list(template["roles"].items())))
    reordered_config = dict(reversed(list(config.items())))
    reordered_config["roles"] = dict(reversed(list(config["roles"].items())))
    current = compute(reordered_template, reordered_config, live)
    assert current == saved
    assert verify_plan(
        saved, reordered_template, reordered_config, live,
        fleet_id="example", machine_limit=5, now=160,
    ) == saved


@pytest.mark.parametrize("provenance", [
    None, "default", [], {}, {"id": "default"}, {"id": True, "revision": "r1"},
    {"id": "default", "revision": ""}, {"id": "default", "revision": "r1", "extra": True},
])
def test_known_template_provenance_is_strict_while_custom_roles_remain_preserved(provenance):
    template, _, live = inputs()
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "u1",
        "capacity": {"machine_limit": 5, "execution_hosts": 0},
        "roles": {"custom": {"novel-capability": True}}, "template": provenance,
    }
    with pytest.raises(PlanError):
        compute(template, config, live)


@pytest.mark.parametrize("provenance", [
    {"id": "default", "revision": "old"},
    {"id": "other-template", "revision": "r1"},
], ids=["stale-revision", "different-template"])
def test_mismatched_recorded_provenance_blocks_revalidation_without_overwriting_config(provenance):
    template, _, live = inputs()
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "u1",
        "capacity": {"machine_limit": 5, "execution_hosts": 0},
        "roles": {"custom": {"storage": "retain"}}, "template": provenance,
    }
    before = copy.deepcopy(config)
    saved = compute(template, config, live)
    assert config == before
    assert saved["suggested_config"]["template"] == provenance
    assert saved["suggested_config"]["roles"]["custom"] == config["roles"]["custom"]
    assert saved["status"] == "blocked"
    assert "template-provenance-mismatch" in saved["blockers"]
    assert {"change": "review-template-revision"} in saved["config_changes"]
    with pytest.raises(PlanError, match="unresolved blockers"):
        verify_plan(saved, template, config, live, fleet_id="example", machine_limit=5, now=160)


def test_optional_missing_provenance_retains_review_suggestion_without_blocking_revalidation():
    template, _, live = inputs()
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "u1",
        "capacity": {"machine_limit": 5, "execution_hosts": 0}, "roles": {},
    }
    saved = compute(template, config, live)
    assert "template" not in saved["suggested_config"]
    assert {"change": "review-template-revision"} in saved["config_changes"]
    assert not saved["blockers"]
    assert verify_plan(saved, template, config, live, fleet_id="example", machine_limit=5, now=160) == saved


@pytest.mark.parametrize("arguments,expected", [
    ([], 1), (["plan"], 1), (["unknown"], 1),
    (["plan", "--unknown"], 1),
    (["plan", "--template", "template", "--inventory", "inventory",
      "--fleet-id", "example", "--machine-limit", "invalid"], 1),
    (["--help"], 0), (["--version"], 0), (["plan", "--help"], 0),
], ids=["missing-command", "missing-options", "unknown-command", "unknown-option",
        "invalid-integer", "help", "version", "plan-help"])
def test_installed_cli_syntax_errors_are_not_blocked_plan_exit_codes(tmp_path, arguments, expected):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-m", "fleet_manager", *arguments],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, **flags,
    )
    assert result.returncode == expected
    if expected:
        assert result.stdout == ""
        assert "error:" in result.stderr


def test_installed_cli_blocked_plan_returns_two_and_valid_nonsecret_json(tmp_path):
    template, _, live = inputs()
    now = int(time.time())
    live.update(observed_at=now - 1, expires_at=now + 60, quota_remaining=None)
    for name, data in (("template.json", template), ("inventory.json", live)):
        (tmp_path / name).write_text(json.dumps(data))
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-m", "fleet_manager", "plan",
         "--template", str(tmp_path / "template.json"),
         "--inventory", str(tmp_path / "inventory.json"),
         "--fleet-id", "example", "--machine-limit", "5"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, **flags,
    )
    assert result.returncode == 2, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "blocked"
    assert "provider-quota-unknown" in output["blockers"]
    assert output["capacity"]["proposed_creations"] == 0


@pytest.mark.parametrize("missing", ["template", "config", "inventory"])
def test_cli_read_error_identifies_input_kind_without_disclosing_path(tmp_path, capsys, missing):
    template, _, live = inputs()
    for name, data in (("template", template), ("inventory", live)):
        (tmp_path / name).write_text(json.dumps(data))
    paths = {name: tmp_path / name for name in ("template", "config", "inventory")}
    paths[missing] = tmp_path / "private-missing-name"
    arguments = ["plan", "--template", str(paths["template"]), "--inventory", str(paths["inventory"]),
                 "--fleet-id", "example", "--machine-limit", "5"]
    if missing == "config":
        arguments.extend(["--config", str(paths["config"])])
    assert main(arguments) == 1
    captured = capsys.readouterr()
    assert f"{missing} file could not be read" in captured.err
    assert "private-missing-name" not in captured.err
    assert captured.out == ""


def test_near_limit_config_has_room_for_plan_envelope_and_revalidation():
    template, _, live = inputs()
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "u1",
        "capacity": {"machine_limit": 5, "execution_hosts": 0}, "roles": {},
        "extensions": {"padding": ["x" * 65536 for _ in range(15)] + ["x" * 64500]},
    }
    assert len(canonical(config)) < MAX_BYTES
    saved = compute(template, config, live)
    assert MAX_BYTES < len(canonical(saved, output=True)) < MAX_PLAN_BYTES
    assert verify_plan(
        saved, template, config, live, fleet_id="example", machine_limit=5, now=160,
    ) == saved


def test_plan_bounds_allow_union_of_independently_bounded_roles_and_nested_config():
    template, _, live = inputs()
    template["roles"] = {
        f"service-{i}": {"kind": "service", "required": True, "available": True}
        for i in range(MAX_ITEMS)
    }
    nested = "preserve"
    for _ in range(11):
        nested = {"child": nested}
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "u1",
        "capacity": {"machine_limit": 5, "execution_hosts": 0},
        "roles": {f"custom-{i}": {} for i in range(MAX_ITEMS)}, "extensions": nested,
    }
    saved = compute(template, config, live)
    assert len(saved["roles"]) == 2 * MAX_ITEMS
    assert len(saved["config_changes"]) == MAX_ITEMS + 1
    assert saved["suggested_config"]["extensions"] == nested
    assert verify_plan(
        saved, template, config, live, fleet_id="example", machine_limit=5, now=160,
    ) == saved


def test_output_limit_diagnostic_and_saved_plan_refusal_are_output_specific():
    oversized = {"padding": ["x" * 65536 for _ in range(64)]}
    with pytest.raises(PlanError, match="plan output exceeds byte limit"):
        canonical(oversized, output=True)
    oversized["plan_digest"] = "untrusted"
    template, config, live = inputs()
    with pytest.raises(PlanError, match="plan output exceeds byte limit"):
        verify_plan(oversized, template, config, live, fleet_id="example", machine_limit=5, now=160)
