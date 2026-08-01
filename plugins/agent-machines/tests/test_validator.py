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


def test_map_enforced_is_advisory_not_error(tmp_path):
    data = base_package("a/x", gate=["*"])
    # A map leaf (enabledPlugins) under an enforce surface -> shape advisory.
    data["manage"]["copilot.settings"]["values"]["enabledPlugins"] = {
        "agent-worktrees@copilot-extensions": True,
        "agent-machines@copilot-extensions": True,
    }
    a = _pkg(tmp_path, "a", data)
    findings = validate([a])
    assert any(f.code == "shape-mismatch" and f.level == "advisory" for f in findings)
    assert not any(f.code == "shape-mismatch" and f.level == "error" for f in findings)


def test_bootstrap_floor_disable_is_error(tmp_path):
    data = base_package("a/x", gate=["*"])
    data["manage"]["copilot.settings"]["values"]["enabledPlugins"] = {
        "agent-worktrees@copilot-extensions": False
    }
    a = _pkg(tmp_path, "a", data)
    findings = validate([a])
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
