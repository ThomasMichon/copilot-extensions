"""The optional catalog projection never selects, starts or authenticates a resident."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_receipt_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def binding(tmp_path):
    return {
        "status": "selected",
        "source": {
            "fingerprint": "sha256:source",
            "marketplaceId": "marketplace-fixture",
            "pluginId": "agent-worktrees",
        },
        "installation": {
            "installationId": "sha256:installation",
            "sourceFingerprint": "sha256:source",
            "pluginId": "agent-worktrees",
            "mode": "legacy",
            "runtimeRoot": str(tmp_path / "unprovisioned"),
        },
    }


def test_candidate_reuses_binding_without_files_or_processes(binding, monkeypatch):
    import socket
    import subprocess

    projection = load_script("resident_control_projection")
    before = copy.deepcopy(binding)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("helper started"))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("probe attempted"))
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("state read or created"))
    result = projection.project_candidate(binding)
    assert binding == before
    assert result["state"] == "candidate"
    assert result["runtimeRoot"] == binding["installation"]["runtimeRoot"]
    assert result["activeRoutePath"].endswith(str(Path("status-monitor-routing") / "active.json"))
    assert "token" not in result


def test_references_match_existing_control_owner(binding):
    from agent_worktrees import status_monitor_cutover as control

    result = load_script("resident_control_projection").project_candidate(binding)
    root = Path(result["runtimeRoot"])
    assert Path(result["activeRoutePath"]) == control.routing_dir(root) / "active.json"
    assert Path(result["tokenPath"]) == control.control_token_path(root)
    assert not root.exists()


@pytest.mark.parametrize("change", [
    {"status": "unresolved"}, {"status": "blocked"}, {"source": None},
    {"installation": {}},
])
def test_missing_selection_has_no_projection(binding, change):
    binding.update(change)
    assert load_script("resident_control_projection").project_candidate(binding) is None


@pytest.mark.parametrize("key,value", [
    ("sourceFingerprint", "foreign"), ("pluginId", "foreign"),
    ("mode", "unknown"), ("runtimeRoot", "relative"),
    ("installGeneration", True), ("activationGeneration", 0),
])
def test_conflicting_installation_is_not_a_candidate(binding, key, value):
    binding["installation"][key] = value
    assert load_script("resident_control_projection").project_candidate(binding) is None


def test_namespaced_generation_fields_remain_distinct(binding):
    project = load_script("resident_control_projection").project_candidate
    binding["installation"].update(mode="namespaced", installGeneration=3)
    assert project(binding) is None
    binding["installation"]["activationGeneration"] = 7
    result = project(binding)
    assert (result["installGeneration"], result["activationGeneration"]) == (3, 7)


def test_real_catalog_reuses_one_binding_and_keeps_shim_availability(binding, monkeypatch):
    hook = load_script("hook_client")
    original = hook._load_sibling
    calls = []

    def resolve(*args):
        calls.append(args)
        return binding

    monkeypatch.setattr(hook, "_load_sibling", lambda name: (
        SimpleNamespace(binding_for_payload=resolve) if name == "command_binding.py"
        else original(name)
    ))
    monkeypatch.setattr(hook.subprocess, "Popen", lambda *a, **k: pytest.fail("child started"))
    text = hook._command_catalog_context()
    catalog = json.loads(text.split("```json\n")[1].split("\n```")[0])
    assert len(calls) == 1
    assert catalog["binding"] == binding
    assert catalog["commands"][0]["availability"] == "ready"
    assert catalog["residentControl"]["state"] == "candidate"
    binding["status"] = "unresolved"
    assert "residentControl" not in hook._command_catalog_context()
