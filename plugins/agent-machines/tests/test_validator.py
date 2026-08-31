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


def test_dotted_json_key_does_not_alias_nested_path(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"]["copilot.settings"]["values"] = {"a.b": 1}
    b_data = base_package("b/x", gate=["*"])
    b_data["manage"]["copilot.settings"]["values"] = {"a": {"b": 2}}
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    assert not any(f.code == "enforce-conflict" for f in findings)


def test_known_collection_map_stays_opaque_and_advisory(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"]["copilot.settings"]["values"]["enabledPlugins"] = {
        "optional@example": True
    }
    findings = validate([_pkg(tmp_path, "a", data)])
    advisory = next(f for f in findings if f.code == "shape-mismatch")
    assert "'copilot.settings.enabledPlugins'" in advisory.message
    assert not any(f.code == "enforce-conflict" for f in findings)


def test_bool_and_numeric_scalar_values_conflict(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"]["copilot.settings"]["values"]["enabled"] = True
    b_data = base_package("b/x", gate=["*"])
    b_data["manage"]["copilot.settings"]["values"]["enabled"] = 1
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    assert any(f.code == "enforce-conflict" for f in findings)


def test_malformed_bootstrap_collections_report_shape_without_crashing(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"]["copilot.settings"]["values"].update(
        {
            "enabledPlugins": ["agent-worktrees@copilot-extensions"],
            "extraKnownMarketplaces": ["copilot-extensions"],
        }
    )
    findings = validate([_pkg(tmp_path, "a", data)])
    shape_paths = {f.message.split("'", 2)[1] for f in findings if f.code == "shape-mismatch"}
    assert shape_paths == {
        "copilot.settings.enabledPlugins",
        "copilot.settings.extraKnownMarketplaces",
    }


def test_omitted_enforce_values_do_not_create_shape_conflict(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"] = {"copilot.settings.empty": {"disposition": "enforce"}}
    b_data = base_package("b/x", gate=["*"])
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    assert not any(f.code == "enforce-shape-conflict" for f in findings)


def test_unhandled_enforce_surface_is_not_settings_conflict_domain(tmp_path):
    a_data = base_package("a/x", gate=["*"])
    a_data["manage"] = {
        "custom.unhandled": {
            "disposition": "enforce",
            "values": {"nested": {"value": 1}},
        }
    }
    b_data = base_package("b/x", gate=["*"])
    b_data["manage"] = {
        "custom.unhandled": {
            "disposition": "enforce",
            "values": {"nested": {"value": 2}},
        }
    }
    findings = validate([_pkg(tmp_path, "a", a_data), _pkg(tmp_path, "b", b_data)])
    assert not any(f.code.startswith("enforce-") for f in findings)


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


def test_ignored_grouped_settings_do_not_affect_bootstrap_floor(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"] = {
        "copilot.settings.plugins": {
            "disposition": "ignore",
            "values": {"extraKnownMarketplaces": {"some-other-market": {}}},
        }
    }
    findings = validate([_pkg(tmp_path, "a", data)])
    assert not any(f.code == "bootstrap-floor" for f in findings)


def test_plan_and_drift_key_stable(tmp_path):
    a = _pkg(tmp_path, "a", base_package("a/x", gate=["*"]))
    p1 = plan([a], "box-1")
    p2 = plan([a], "box-1")
    assert p1.drift_key == p2.drift_key
    assert "copilot.settings" in {s.key for s in p1.surfaces}
    assert p1.package_sources == [{"package": "a/x", "source_repo": "a"}]


def test_drift_key_changes_with_content(tmp_path):
    a = _pkg(tmp_path, "a", base_package("a/x", gate=["*"]))
    bdata = base_package("a/x", gate=["*"])
    bdata["manage"]["copilot.settings"]["values"]["model"] = "different"
    b = _pkg(tmp_path, "b", bdata)
    assert manifest_hash(resolve_union([a], "box-1")) != manifest_hash(resolve_union([b], "box-1"))


def test_drift_key_includes_source_modules_and_resources(tmp_path):
    base = base_package("shared/package", gate=["*"])
    a = _pkg(tmp_path, "a", base)

    source_changed = _pkg(tmp_path, "b", base)
    assert manifest_hash([a]) != manifest_hash([source_changed])

    module_data = base_package("shared/package", gate=["*"])
    module_data["modules"] = [
        {
            "name": "probe",
            "windows": {"command": ["pwsh", "-File", "tools/probe.ps1"]},
        }
    ]
    module_changed = load_package(
        write_package(tmp_path / "a-module", "p.yaml", module_data),
        source_repo="a",
    )
    assert manifest_hash([a]) != manifest_hash([module_changed])

    resource_data = base_package("shared/package", gate=["*"])
    resource_data["resources"] = [
        {
            "type": "package",
            "manager": "winget",
            "id": "Example.Tool",
        }
    ]
    resource_changed = load_package(
        write_package(tmp_path / "a-resource", "p.yaml", resource_data),
        source_repo="a",
    )
    assert manifest_hash([a]) != manifest_hash([resource_changed])
