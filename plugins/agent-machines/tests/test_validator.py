from __future__ import annotations

from pathlib import Path

from agent_machines.manifest import load_package
from agent_machines.reconcile import manifest_hash, plan, resolve_union
from agent_machines.validator import has_errors, validate

from ._helpers import base_package, write_package


def _pkg(tmp_path: Path, name: str, data: dict):
    return load_package(write_package(tmp_path / name, "p.yaml", data), source_repo=name)


def test_no_conflict_when_scalars_agree(tmp_path):
    a = _pkg(tmp_path, "a", base_package("a/x", gate=["*"]))
    b = _pkg(tmp_path, "b", base_package("b/x", gate=["*"]))
    findings = validate([a, b])
    assert not has_errors(findings)


def test_scalar_conflict_is_error(tmp_path):
    a = _pkg(tmp_path, "a", base_package("a/x", gate=["*"]))
    bdata = base_package("b/x", gate=["*"])
    bdata["manage"]["copilot.settings"]["values"]["model"] = "sonnet"
    b = _pkg(tmp_path, "b", bdata)
    findings = validate([a, b])
    assert has_errors(findings)
    assert any(f.code == "enforce-conflict" for f in findings)


def test_list_enforced_is_advisory_not_error(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"]["copilot.settings"]["values"]["tabs"] = ["sessions", "agents"]
    a = _pkg(tmp_path, "a", data)
    findings = validate([a])
    assert any(f.code == "shape-mismatch" and f.level == "advisory" for f in findings)
    assert not any(f.code == "shape-mismatch" and f.level == "error" for f in findings)


def test_empty_enforced_map_has_no_leaves_or_advisory(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"]["copilot.settings"]["values"] = {}
    findings = validate([_pkg(tmp_path, "a", data)])
    assert not any(f.code == "shape-mismatch" for f in findings)


def test_nested_enforced_scalars_agree_without_shape_advisory(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"] = {
        "copilot.settings.sandbox": {
            "disposition": "enforce",
            "values": {"sandbox": {"enabled": False}},
        }
    }
    b_data = base_package("b/x", gate=["*"])
    b_data["manage"] = {
        "copilot.settings.sandbox": {
            "disposition": "enforce",
            "values": {"sandbox": {"enabled": False}},
        }
    }
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    assert not any(f.code == "shape-mismatch" for f in findings)
    assert not has_errors(findings)


def test_nested_enforced_scalar_conflict_is_error(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"] = {
        "copilot.settings.sandbox": {
            "disposition": "enforce",
            "values": {"sandbox": {"enabled": False}},
        }
    }
    b_data = base_package("b/x", gate=["*"])
    b_data["manage"] = {
        "copilot.settings.sandbox": {
            "disposition": "enforce",
            "values": {"sandbox": {"enabled": True}},
        }
    }
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    conflict = next(f for f in findings if f.code == "enforce-conflict")
    assert "'copilot.settings.sandbox.enabled'" in conflict.message
    assert has_errors(findings)


def test_grouped_and_root_settings_share_conflict_identity(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"] = {
        "copilot.settings": {
            "disposition": "enforce",
            "values": {"sandbox": {"enabled": False}},
        }
    }
    b_data = base_package("b/x", gate=["*"])
    b_data["manage"] = {
        "copilot.settings.sandbox": {
            "disposition": "enforce",
            "values": {"sandbox": {"enabled": True}},
        }
    }
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    conflict = next(f for f in findings if f.code == "enforce-conflict")
    assert "'copilot.settings.sandbox.enabled'" in conflict.message


def test_scalar_map_shape_conflict_is_error(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"] = {
        "copilot.settings": {
            "disposition": "enforce",
            "values": {"sandbox": False},
        }
    }
    b_data = base_package("b/x", gate=["*"])
    b_data["manage"] = {
        "copilot.settings.sandbox": {
            "disposition": "enforce",
            "values": {"sandbox": {"enabled": True}},
        }
    }
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    conflict = next(f for f in findings if f.code == "enforce-shape-conflict")
    assert "'copilot.settings.sandbox'" in conflict.message
    assert has_errors(findings)


def test_bootstrap_floor_disable_is_error(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"]["copilot.settings"]["values"]["enabledPlugins"] = {
        "agent-worktrees@copilot-extensions": False
    }
    a = _pkg(tmp_path, "a", data)
    findings = validate([a])
    assert has_errors(findings)
    assert any(f.code == "bootstrap-floor" for f in findings)


def test_bootstrap_floor_disable_in_grouped_settings_is_error(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"] = {
        "copilot.settings.plugins": {
            "disposition": "enforce",
            "values": {
                "enabledPlugins": {"agent-worktrees@copilot-extensions": False}
            },
        }
    }
    findings = validate([_pkg(tmp_path, "a", data)])
    assert has_errors(findings)
    assert any(f.code == "bootstrap-floor" for f in findings)


def test_bootstrap_floor_marketplace_union(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"]["copilot.settings"]["values"]["extraKnownMarketplaces"] = {
        "some-other-market": {}
    }
    a = _pkg(tmp_path, "a", data)
    findings = validate([a])
    assert any(f.code == "bootstrap-floor" for f in findings)


def test_plan_and_drift_key_stable(tmp_path):
    a = _pkg(tmp_path, "a", base_package("a/x", gate=["*"]))
    p1 = plan([a], "box-1")
    p2 = plan([a], "box-1")
    assert p1.drift_key == p2.drift_key
    assert "copilot.settings" in {s.key for s in p1.surfaces}


def test_drift_key_changes_with_content(tmp_path):
    a = _pkg(tmp_path, "a", base_package("a/x", gate=["*"]))
    bdata = base_package("a/x", gate=["*"])
    bdata["manage"]["copilot.settings"]["values"]["model"] = "different"
    b = _pkg(tmp_path, "b", bdata)
    assert manifest_hash(resolve_union([a], "box-1")) != manifest_hash(resolve_union([b], "box-1"))
