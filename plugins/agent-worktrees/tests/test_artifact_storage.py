"""Storage composition keeps portable policy separate from machine paths."""

from pathlib import Path
import json

import pytest

from configuration_layers import ConfigurationError
from agent_worktrees.artifact_storage import DOCUMENT, load_storage, resolve_storage


@pytest.fixture
def context(tmp_path):
    base, knowledge = tmp_path / "source", tmp_path / "knowledge"
    base.mkdir()
    knowledge.mkdir()
    return {
        "repository_id": "repo-example", "execution_machine": "test-machine",
        "installation_cell": "test-cell",
        "binding": "bound", "base_root": str(base), "knowledge_root": str(knowledge),
    }


def layer(identity, tier, origin, values, **scope):
    return {"id": identity, "tier": tier, "origin": origin, "values": values, **scope}


def test_default_lookup_does_not_allocate_storage(context, tmp_path):
    temporary = tmp_path / "temporary"
    result = resolve_storage(context, [], environment={}, temporary_root=temporary)
    assert Path(result["scratch_root"]).parent == temporary
    assert result["failure_evidence"] == {"max_files": 16, "max_bytes": 8 * 1024 * 1024}
    assert result["root_admitted"] is False
    assert not temporary.exists()


def test_knowledge_policy_machine_paths_and_request_precedence(context, tmp_path):
    machine_root, emergency_root = tmp_path / "machine", tmp_path / "emergency"
    layers = [
        layer("base", "repository", "base", {"failure_evidence": {"max_files": 4}},
              repository_id="repo-example"),
        layer("knowledge", "repository", "knowledge", {"failure_evidence": {"max_files": 6}},
              repository_id="repo-example"),
        layer("named", "machine", "named_machine", {"scratch_root": str(machine_root)},
              execution_machine="test-machine"),
        layer("local", "repository_machine", "repository_machine",
              {"scratch_root": str(emergency_root)}, repository_id="repo-example",
              execution_machine="test-machine"),
    ]
    result = resolve_storage(context, layers, environment={})
    assert result["scratch_root"] == str(emergency_root)
    assert result["scratch_root_origin"] == "local"
    assert result["failure_evidence"]["max_files"] == 6
    assert result["provenance"]["/failure_evidence/max_files"]["id"] == "knowledge"
    result = resolve_storage(context, layers, environment={"AGENT_SCRATCH_ROOT": str(machine_root)})
    assert result["scratch_root"] == str(machine_root)
    explicit = tmp_path / "explicit"
    assert resolve_storage(
        context, layers, explicit_root=str(explicit),
        environment={"AGENT_SCRATCH_ROOT": str(machine_root)},
    )["scratch_root"] == str(explicit)
    assert not any(path.exists() for path in (machine_root, emergency_root, explicit))


@pytest.mark.parametrize("value", ["", "relative", None])
def test_invalid_explicit_environment_never_falls_back(context, value):
    if value is None:
        # A present non-text mapping entry is invalid, not missing configuration.
        value = 7
    with pytest.raises(ConfigurationError):
        resolve_storage(context, [], environment={"AGENT_SCRATCH_ROOT": value})


def test_portable_layer_cannot_supply_physical_machine_path(context, tmp_path):
    with pytest.raises(ConfigurationError, match="machine-scoped"):
        resolve_storage(context, [
            layer("portable", "repository", "knowledge", {"scratch_root": str(tmp_path / "scratch")},
                  repository_id="repo-example"),
        ], environment={})


@pytest.mark.parametrize("values", [
    {"unknown": True}, {"failure_evidence": {"unknown": 1}},
    {"failure_evidence": {"max_files": True}},
    {"failure_evidence": {"max_bytes": 0}},
])
def test_invalid_policy_is_not_hidden_by_later_layer(context, values):
    with pytest.raises(ConfigurationError):
        resolve_storage(context, [
            layer("bad", "repository", "base", values, repository_id="repo-example"),
            layer("later", "repository", "knowledge",
                  {"failure_evidence": {"max_files": 8, "max_bytes": 100}},
                  repository_id="repo-example"),
        ], environment={})


@pytest.mark.parametrize("owner", ["base_root", "knowledge_root", "session"])
def test_scratch_is_not_placed_in_history_or_source(context, tmp_path, owner):
    sessions = tmp_path / "session-state"
    root = sessions if owner == "session" else Path(context[owner])
    with pytest.raises(ConfigurationError, match="outside"):
        resolve_storage(context, [], environment={}, explicit_root=str(root / "scratch"),
                        session_root=sessions)


def test_foreign_machine_root_is_not_selected(context, tmp_path):
    result = resolve_storage(context, [
        layer("foreign", "machine", "named_machine", {"scratch_root": str(tmp_path / "foreign")},
              execution_machine="different-machine"),
    ], environment={}, temporary_root=tmp_path / "temp")
    assert result["scratch_root_origin"] == "private-os-temp"


def test_unresolved_named_binding_remains_an_error(context):
    broken = {**context, "binding": "unresolved"}
    with pytest.raises(ConfigurationError):
        resolve_storage(broken, [], environment={})


def write_document(root, relative, body):
    target = Path(root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body), encoding="utf-8")
    return target


def test_real_selected_documents_resolve_knowledge_machine_and_local_sources(context, tmp_path):
    machine_root = tmp_path / "machine-config"
    machine_root.mkdir()
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": context, "repository_machine_root": str(machine_root),
    }
    write_document(context["base_root"], DOCUMENT,
                   {"version": 1, "portable": {"failure_evidence": {"max_files": 2}}})
    write_document(context["knowledge_root"], DOCUMENT, {
        "version": 1, "portable": {"failure_evidence": {"max_files": 3}},
        "machines": {"test-machine": {"scratch_root": str(tmp_path / "scratch")}},
    })
    result = load_storage(report, environment={})
    assert result["failure_evidence"]["max_files"] == 3
    assert result["scratch_root"] == str(tmp_path / "scratch")
    assert not (tmp_path / "scratch").exists()
    write_document(machine_root, "artifact-storage.json",
                   {"version": 1, "values": {"scratch_root": str(tmp_path / "local")}})
    result = load_storage(report, environment={})
    assert result["scratch_root"] == str(tmp_path / "local")
    assert result["scratch_root_origin"] == "artifact-storage-local"


def test_missing_document_is_optional_but_present_malformed_document_is_not(context):
    report = {"schema": "agent-worktrees.configuration", "version": 1, "context": context}
    assert load_storage(report, environment={})["scratch_root_origin"] == "private-os-temp"
    path = write_document(context["knowledge_root"], DOCUMENT, {"version": 2})
    with pytest.raises(ConfigurationError, match="unsupported"):
        load_storage(report, environment={})
    path.write_text('{"version":1,"version":1}', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_storage(report, environment={})


def test_repository_cannot_smuggle_machine_paths_into_storage_policy(context, tmp_path):
    write_document(context["base_root"], DOCUMENT, {
        "version": 1, "machines": {"test-machine": {"scratch_root": str(tmp_path / "scratch")}},
    })
    with pytest.raises(ConfigurationError):
        load_storage({"schema": "agent-worktrees.configuration", "version": 1, "context": context},
                     environment={})


def test_shared_machine_document_accepts_foreign_platform_paths(context, tmp_path):
    import os
    foreign = "/var/tmp/private-scratch" if os.name == "nt" else r"C:\Scratch\private"
    selected = str(tmp_path / "native-scratch")
    write_document(context["knowledge_root"], DOCUMENT, {
        "version": 1, "machines": {
            "other-machine": {"scratch_root": foreign},
            "test-machine": {"scratch_root": selected},
        },
    })
    result = load_storage({"schema": "agent-worktrees.configuration", "version": 1, "context": context},
                          environment={})
    assert result["scratch_root"] == selected
    assert not Path(selected).exists()
