"""Contained worker catalog, persistence and registrar consumer contracts."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_dispatch import registrar_discovery as rd
from agent_dispatch.registrar import RegistrarError
from agent_dispatch.worker_instances import (
    ACTIVATION_CONSENT, OWNER, POINTER, active_dir, digest, instance_dir,
    read_records, save_instance, templates,
)
from agent_dispatch.workers_catalog import catalog, _observation as real_observation

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(tmp_path / "registrar"))
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(tmp_path / "overrides.json"))
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISE_MACHINE", "fixture-host")
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR", str(tmp_path / "dropins"))
    monkeypatch.setattr("agent_dispatch.workers_catalog._observation", lambda: {"known": False})
    monkeypatch.setattr(
        "agent_dispatch.worker_templates.run_agent_worktrees_capture",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"inventory_authority":"complete","repos":[]}', stderr=""),
    )


def fields(name="local-pool", **changes):
    declaration = copy.deepcopy(templates()["worker-pool"])
    declaration.update(changes)
    return {"name": name, "declaration": declaration, "repo_root": ""}


def create(name="local-pool", *, activate=False, **changes):
    return save_instance(
        "template:worker-pool", digest(templates()["worker-pool"]), fields(name, **changes),
        activate=activate, consent=ACTIVATION_CONSENT if activate else None,
    )


def edit(receipt, *, activate=False, **changes):
    record = read_records()[0]
    declaration = dict(record["configuration"]["declaration"])
    declaration.update(changes)
    return save_instance(
        receipt["id"], receipt["revision"],
        {"name": record["name"], "declaration": declaration, "repo_root": ""},
        activate=activate, consent=ACTIVATION_CONSENT if activate else None,
    )


@pytest.mark.guard
def test_empty_catalog_is_read_only_and_lists_fragments():
    before = templates().copy()
    data = catalog()
    assert data["summary"]["configured"] == 0
    assert {r["title"] for r in data["entries"]} == set(templates())
    assert all(r["activity"] == "not-applicable" for r in data["entries"])
    assert all(r["fields"][1]["default"] for r in data["entries"])
    assert not rd.registrar_dir().exists()
    assert templates() == before


def test_catalog_version_template_kinds_and_read_time_do_not_claim_activity():
    before = time.time()
    data = catalog()
    assert data["schema_version"] == 1
    assert before <= data["fetched_at"] <= time.time()
    assert data["summary"]["activity_updated_at"] is None
    for row in data["entries"]:
        assert row["kind"] == templates()[row["title"]]["kind"]
        assert row["components"] == [] and row["components_basis"] == "template"
        assert row["activity"] == "not-applicable"


def test_source_component_metadata_is_attributable_without_executable_fields(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    document = source / "emitter.json"
    data = {
        "name": "incoming", "kind": "emitter",
        "spec": {
            "id": "incoming", "command": [sys.executable, "-c", "print('[]')"],
            "interval_seconds": 60, "env": {"PRIVATE_VALUE": "not-component-metadata"},
        },
    }
    original = json.dumps(data).encode()
    document.write_bytes(original)
    rd.add_pointer("source", source, owner="repo:source")
    row = next(row for row in catalog()["entries"] if row["title"] == "incoming")
    assert row["kind"] == "emitter" and row["components_basis"] == "source"
    component, = row["components"]
    assert component["id"] == row["registration_ids"][0]
    assert component["name"] == "incoming" and component["owner"] == "repo:source"
    assert component["declaration_kind"] == component["kind"] == "emitter"
    assert component["repo_scope"] is None and len(component["revision"]) == 64
    assert component["repo_scopes"] == []
    assert component["source_path"] == str(document)
    assert row["source_path"] == str(document)
    assert "not-component-metadata" not in json.dumps(component)
    assert "command" not in component and "env" not in component and "spec" not in component
    assert "Components (source)" in row["card"]["body"]
    assert document.read_bytes() == original


def test_multiple_components_remain_composite_when_their_kinds_match():
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.workers_catalog import _component_metadata

    result = _component_metadata(tuple(
        load_declaration({"name": name, "labels": ["work"]})
        for name in ("first", "second")
    ), basis="source")
    assert result["kind"] == "composite"
    assert len(result["components"]) == 2
    assert all(component["kind"] == "supervised-lane" for component in result["components"])

def test_component_metadata_preserves_supported_lane_scope_and_authored_label_order():
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.workers_catalog import _component_metadata

    declaration = load_declaration({
        "name": "scoped", "repos": "example/requests", "labels": ["zeta", "alpha"],
    })
    assert isinstance(declaration.repos, str)
    assert declaration.labels == ("zeta", "alpha")
    first = _component_metadata((declaration,), basis="source")
    assert first == _component_metadata((declaration,), basis="source")
    component, = first["components"]
    assert component["repo_scope"] == "example/requests"
    assert component["repo_scopes"] == ["example/requests"]
    assert component["labels"] == ["zeta", "alpha"]
    for unsupported in (["example/requests"], ("example/requests",)):
        with pytest.raises(RegistrarError, match="lane string"):
            load_declaration({"name": "invalid", "repos": unsupported, "labels": ["work"]})


def test_unreadable_publication_never_projects_draft_components():
    receipt = create(activate=True)
    (active_dir() / "local-pool.json").write_text("not-json", encoding="utf-8")
    row = next(row for row in catalog()["entries"] if row["id"] == receipt["id"])
    assert row["publication"] == "error" and row["publication_errors"]
    assert row["kind"] == "unknown" and row["components"] == []
    assert row["components_basis"] == "unknown"


@pytest.mark.parametrize("all_repos", [False, True])
def test_direct_evaluator_metadata_retains_declared_scope_and_label_order(all_repos):
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.workers_catalog import _component_metadata

    declaration = load_declaration({
        "name": "review", "kind": "evaluator",
        "spec": {
            **({"all_repos": True} if all_repos else {"repo": "example/requests"}),
            "labels": ["zeta", "alpha"], "evaluator_ref": "fixture",
            "evaluator_spec": {"rules": []},
        },
    })
    component, = _component_metadata((declaration,), basis="source")["components"]
    assert component["repo_scope"] == ("all" if all_repos else "example/requests")
    assert component["repo_scopes"] == [component["repo_scope"]]
    assert component["labels"] == ["zeta", "alpha"]


@pytest.mark.parametrize("wrapped", [False, True])
def test_schedule_metadata_reads_bare_or_wrapped_entry_without_source_mutation(wrapped):
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.workers_catalog import _component_metadata

    entry = {"id": "tick", "repo": "example/requests", "labels": ["scheduled"], "interval_seconds": 60}
    spec = {"schedules": [entry]} if wrapped else entry
    before = copy.deepcopy(spec)
    declaration = load_declaration({"name": "tick", "kind": "schedule", "spec": spec})
    component, = _component_metadata((declaration,), basis="source")["components"]
    assert component["repo_scopes"] == ["example/requests"]
    assert component["labels"] == ["scheduled"]
    assert spec == before


def test_schedule_metadata_retains_all_scopes_defaults_and_labels_in_declaration_order():
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.workers_catalog import _component_metadata

    declaration = load_declaration({
        "name": "ticks", "kind": "schedule",
        "spec": {
            "default_repo": "example/default",
            "schedules": [
                {"id": "first", "repo": "example/first", "labels": ["first", "shared"], "interval_seconds": 60},
                {"id": "second", "repo": "example/second", "labels": ["shared", "second"], "interval_seconds": 60},
                {"id": "default", "labels": ["default"], "interval_seconds": 60},
            ],
        },
    })
    component, = _component_metadata((declaration,), basis="source")["components"]
    assert component["repo_scope"] == "example/first"
    assert component["repo_scopes"] == ["example/first", "example/second", "example/default"]
    assert component["labels"] == ["first", "shared", "second", "default"]


@pytest.mark.parametrize("schedules", [[], "not-a-list", {}])
def test_invalid_schedule_container_remains_a_discovery_error_not_routing_metadata(tmp_path, schedules):
    from agent_dispatch.registrar import load_declaration

    document = {
        "name": "invalid-ticks", "kind": "schedule",
        "spec": {"schedules": schedules, "default_repo": "example/default", "labels": ["not-an-entry"]},
    }
    with pytest.raises(RegistrarError, match="non-empty list of objects"):
        load_declaration(document)
    source = tmp_path / "source"
    source.mkdir()
    path = source / "invalid.json"
    original = json.dumps(document).encode()
    path.write_bytes(original)
    rd.add_pointer("invalid-schedule", source, owner="repo:source")
    data = catalog()
    assert any("non-empty list of objects" in error for error in data["summary"]["errors"])
    assert any(row["group"] == "Discovery errors" for row in data["entries"])
    assert all(row["title"] != "invalid-ticks" for row in data["entries"])
    assert any(row["id"] == "template:worker-pool" for row in data["entries"])
    assert path.read_bytes() == original


@pytest.mark.parametrize("location", ["source", "draft", "published"])
@pytest.mark.parametrize("spec", [
    {"schedules": [{"id": "ok", "repo": "example/project", "interval_seconds": 60}, None]},
    {"id": "ok", "repo": "example/project", "interval_seconds": 60, "labels": False},
])
def test_component_projection_errors_preserve_healthy_catalog_rows_and_source_bytes(tmp_path, location, spec):
    from agent_dispatch.registrar import load_declaration

    healthy = create("a-healthy", activate=True)
    document = {"name": "z-bad", "kind": "schedule", "spec": spec}
    load_declaration(document)  # These shapes currently pass the owning registrar.
    if location == "source":
        source = tmp_path / "source"
        source.mkdir()
        path = source / "schedule.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        rd.add_pointer("schedule-source", source, owner="repo:source")
        paths = [path]
    else:
        receipt = create("z-bad")
        save_instance(
            receipt["id"], receipt["revision"],
            {"name": "z-bad", "declaration": document, "repo_root": ""},
            activate=location == "published",
            consent=ACTIVATION_CONSENT if location == "published" else None,
        )
        paths = [instance_dir() / "z-bad.json"]
        if location == "published":
            paths.append(active_dir() / "z-bad.json")
    before = {path: path.read_bytes() for path in paths}
    data = catalog()
    bad, = [row for row in data["entries"] if row["title"] == "z-bad"]
    assert bad["validity"] == "metadata-error"
    assert bad["kind"] == bad["components_basis"] == "unknown" and bad["components"] == []
    assert bad["component_errors"] and all(error in data["summary"]["errors"] for error in bad["component_errors"])
    assert "Component metadata requires repair" in bad["card"]["body"]
    good = next(row for row in data["entries"] if row["id"] == healthy["id"])
    assert good["kind"] == "supervised-lane" and good["validity"] == "valid"
    assert good["pool_toggleable"] == "false", "late projection errors must disable earlier toggle affordances"
    assert any(row["id"] == "template:worker-pool" for row in data["entries"])
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("key", ["repository_issue_loop", "effort_driver_loop"])
def test_composed_emitter_metadata_reads_its_own_nested_routing(key):
    from agent_dispatch.registrar import ProfileDeclaration
    from agent_dispatch.workers_catalog import _component_metadata

    declaration = ProfileDeclaration(name="source", kind="emitter", spec={
        key: {"repo": "example/requests", "task_label": "routed-work"},
    })
    component, = _component_metadata((declaration,), basis="source")["components"]
    assert component["repo_scopes"] == ["example/requests"]
    assert component["labels"] == ["routed-work"]


@pytest.mark.parametrize("mode", ["command", "repository_issue_loop", "effort_driver_loop"])
def test_null_emitter_bindings_do_not_mask_the_selected_kind_or_break_catalog(tmp_path, mode):
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrar_reconcile import declaration_to_registration

    create("healthy", activate=True)
    spec = {
        "id": "nullable-source", "interval_seconds": 60, "cwd": str(tmp_path),
        "command": None, "ci_repair": None,
        "repository_issue_loop": None, "effort_driver_loop": None,
    }
    if mode == "command":
        spec.update(command=[sys.executable, "-c", "print('[]')"], repo="example/project", labels=["routed-work"])
    else:
        config = {
            "kind": mode.replace("_", "-"), "name": "nullable",
            "repo": "example/project", "source": "fixture-source",
            "task_label": "routed-work", "cadence_seconds": 60,
            "pool": {"body": {"type": "headless", "agent": "task-worker"}},
        }
        if mode == "repository_issue_loop":
            config.update(
                forge={"provider": "github", "producer_login": "fixture-bot"},
                reservation={"label": "fixture-reserved"},
            )
        else:
            config.update(effort_slugs=["fixture-effort"], state_root=str(tmp_path))
        spec[mode] = config
    document = {"name": "nullable-source", "kind": "emitter", "spec": spec}
    declaration = load_declaration(document)
    before = declaration_to_registration(declaration, machine=None)
    source = tmp_path / "source"
    source.mkdir()
    path = source / "emitter.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    original = path.read_bytes()
    rd.add_pointer("nullable-source", source, owner="repo:source")
    data = catalog()
    row = next(row for row in data["entries"] if row["title"] == "nullable-source")
    assert row["validity"] == "valid" and row["component_errors"] == []
    assert row["components"][0]["repo_scopes"] == ["example/project"]
    assert row["components"][0]["labels"] == ["routed-work"]
    assert data["summary"]["errors"] == []
    assert next(row for row in data["entries"] if row["id"] == "instance:healthy")["pool_toggleable"] == "true"
    assert declaration_to_registration(declaration, machine=None) == before
    assert path.read_bytes() == original


def test_non_emitter_prerequisites_do_not_select_opaque_emitter_binding_names():
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.worker_instances import _validate_dependencies

    declaration = load_declaration({
        "name": "review", "kind": "evaluator",
        "spec": {"all_repos": True, "evaluator_ref": "fixture", "evaluator_spec": {"rules": []},
                 "repository_issue_loop": False, "effort_driver_loop": False},
    })
    _validate_dependencies((declaration,), "", available_declarations=(declaration,))


def test_configuration_components_name_the_draft_or_published_file_without_guessing():
    receipt = create()
    row = next(row for row in catalog()["entries"] if row["id"] == receipt["id"])
    assert row["components"][0]["source_path"] == str(instance_dir() / "local-pool.json")
    receipt = edit(receipt, activate=True)
    row = next(row for row in catalog()["entries"] if row["id"] == receipt["id"])
    assert row["components"][0]["source_path"] == str(active_dir() / "local-pool.json")
    assert str(active_dir() / "local-pool.json") in row["card"]["body"]


def test_observational_source_path_does_not_change_a_lane_registration(tmp_path):
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrar_reconcile import declaration_to_registration

    document = {"name": "pool", "labels": ["work"], "owner": "fixture"}
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    declaration, = rd.read_declaration_file_set(path)
    assert declaration.source_path == str(path)
    assert declaration_to_registration(declaration, machine=None) == declaration_to_registration(
        load_declaration(document), machine=None,
    )


def test_component_cards_keep_literal_backslashes_and_backticks_in_source_paths():
    from agent_dispatch.registrar import ProfileDeclaration
    from agent_dispatch.workers_catalog import _cards, _component_metadata

    path = "C:\\source\\.config\\a``b`file.json"
    row = {"title": "fixture", **_component_metadata((ProfileDeclaration(name="fixture"),), basis="source", source_path=path)}
    card = _cards(row, "Fixture")["card"]["body"]
    assert f"Source document: ``` {path} ```" in card


def test_ordinary_plugin_components_retain_the_verified_source_document(monkeypatch, tmp_path):
    from dropin_registry import EntryDecision, ScanAuthority
    from plugin_activation import ActivationReport, ActivePlugin

    source = "example-producer@example-marketplace"
    root = tmp_path / "example-producer"
    directory = root / "registrar"
    directory.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"name": "example-producer", "version": "1.2.3"}), encoding="utf-8")
    document = directory / "pool.json"
    document.write_text(json.dumps({"name": "plugin-pool"}), encoding="utf-8")
    registry = Path(os.environ["AGENT_DISPATCH_REGISTRAR_DROPINS_DIR"])
    registry.mkdir()
    (registry / "producer.json").write_text(json.dumps({
        "schema_version": 1, "plugin": source, "plugin_root": str(root), "registrar": "registrar",
    }), encoding="utf-8")
    plugin = ActivePlugin(source=source, name="example-producer", marketplace="example-marketplace",
                          root=root.resolve(), scopes=("global",))
    report = ActivationReport(authority=ScanAuthority.COMPLETE, decisions={source: EntryDecision.active(plugin)})
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", lambda: report)
    data = catalog()
    row = next(row for row in data["entries"] if row["title"] == "plugin-pool")
    assert data["summary"]["errors"] == []
    assert row["source_path"] == str(document)
    assert row["components"][0]["source_path"] == str(document)


@pytest.mark.parametrize("spec", [
    {"repo": 42, "labels": ["work"]},
    {"repo": "example/requests", "labels": "not-a-list"},
    {"repo": "example/requests", "labels": False},
    {"repo": "example/requests", "labels": [42]},
])
def test_invalid_declared_routing_is_an_explicit_error_not_empty_metadata(spec):
    from agent_dispatch.registrar import ProfileDeclaration
    from agent_dispatch.workers_catalog import _component_metadata

    with pytest.raises(RegistrarError, match="declared"):
        _component_metadata((ProfileDeclaration(name="invalid", kind="evaluator", spec=spec),), basis="source")


def test_template_save_edit_and_publish_preserve_identity():
    receipt = create()
    assert receipt["persisted"] and receipt["activation"] == "not-requested"
    assert not rd.pointers_file().exists()
    assert rd.discover() == []
    edited = edit(receipt, concurrency=3)
    assert edited["id"] == receipt["id"] and edited["revision"] != receipt["revision"]
    published = edit(edited, activate=True)
    assert published["activation"] == "pending"
    declarations = rd.discover()
    assert len(declarations) == 1
    assert declarations[0].name == "local-pool" and declarations[0].owner == OWNER
    assert declarations[0].concurrency == 3
    assert len(rd.load_pointers()) == 1
    assert rd.load_pointers()[0].name == POINTER


def test_save_only_edit_does_not_change_activated_version():
    receipt = create(activate=True)
    before = (active_dir() / "local-pool.json").read_bytes()
    edit(receipt, concurrency=4)
    assert (active_dir() / "local-pool.json").read_bytes() == before
    assert rd.discover()[0].concurrency == 1
    row = next(r for r in catalog()["entries"] if r["id"] == receipt["id"])
    assert row["publication"] == "published" and row["draft_state"] == "modified"
    assert row["lanes"] == 1 and row["draft_statistics"]["lanes"] == 4


@pytest.mark.skipif(os.name != "nt", reason="real case-insensitive Windows path contract")
def test_case_variant_cannot_rename_existing_instance():
    receipt = create("CasePool", activate=True)
    draft = (instance_dir() / "CasePool.json").read_bytes()
    active = (active_dir() / "CasePool.json").read_bytes()
    with pytest.raises(RegistrarError, match="instance identity"):
        save_instance(
            "instance:casepool", receipt["revision"], fields("casepool"),
            activate=True, consent=ACTIVATION_CONSENT,
        )
    assert (instance_dir() / "CasePool.json").read_bytes() == draft
    assert (active_dir() / "CasePool.json").read_bytes() == active
    assert read_records()[0]["id"] == receipt["id"]


def test_published_runtime_and_controls_survive_differently_shaped_draft(tmp_path, monkeypatch):
    from agent_dispatch.config import overrides_path
    from agent_dispatch.overrides import logical_override_id, set_override
    from agent_dispatch.registrar_reconcile import declared_registration_id

    receipt = create(activate=True)
    rid = declared_registration_id(rd.discover()[0])
    monkeypatch.setattr(
        "agent_dispatch.workers_catalog._observation",
        lambda: {"known": True, "running": [rid]},
    )
    set_override(overrides_path(), logical_override_id(OWNER, "local-pool"), disabled=True)
    document = copy.deepcopy(templates()["reviewer"])
    document.update(
        repo="github.com/example/project", task_label="review-work",
        emitter={"command": [sys.executable, "-c", "print('[]')"], "interval_seconds": 60},
        evaluator={"evaluator_spec": {"rules": []}},
    )
    save_instance(
        receipt["id"], receipt["revision"],
        {"name": "local-pool", "declaration": document, "repo_root": str(tmp_path)},
    )
    data = catalog()
    row = next(r for r in data["entries"] if r["id"] == receipt["id"])
    assert row["draft_state"] == "modified" and row["publication"] == "published"
    assert row["published_revision"] == receipt["revision"]
    assert row["pool_toggleable"] == "true" and row["pool_name"] == "local-pool"
    assert row["lanes"] == 1 and row["statistics"]["declarations"] == 1
    assert row["draft_statistics"]["declarations"] == 3
    assert row["enabled"] == "disabled" and row["activity"] == "observed-running"
    assert row["kind"] == "supervised-lane" and row["components_basis"] == "published"
    assert len(row["components"]) == 1 and row["components"][0]["id"] == rid
    assert not any(r["group"] == "Source declarations" for r in data["entries"])


def test_create_and_activation_retries_are_idempotent():
    first = create(activate=True)
    original = (instance_dir() / "local-pool.json").read_bytes()
    second = create(activate=True)
    assert second["retry"] and second["id"] == first["id"]
    assert len(read_records()) == len(rd.discover()) == len(rd.load_pointers()) == 1
    assert (instance_dir() / "local-pool.json").read_bytes() == original


def test_duplicate_and_stale_edit_refused_without_configuration_changes():
    initial = create()
    with pytest.raises(RegistrarError, match="already exists"):
        create(concurrency=2)
    newest = edit(initial, concurrency=2)
    before = (instance_dir() / "local-pool.json").read_bytes()
    with pytest.raises(RegistrarError, match="stale"):
        edit(initial, concurrency=3)
    assert (instance_dir() / "local-pool.json").read_bytes() == before
    assert read_records()[0]["revision"] == newest["revision"]


@pytest.mark.parametrize("name", ["../escape", "two words", "", "name:owner"])
def test_invalid_names_no_effects(name):
    with pytest.raises(RegistrarError, match="instance name"):
        create(name)
    assert not instance_dir().exists()


def test_invalid_declaration_and_missing_template_prerequisites_no_effects():
    with pytest.raises(RegistrarError, match="concurrency"):
        create(concurrency=0)
    with pytest.raises(RegistrarError, match="requires repo_root"):
        save_instance(
            "template:repository-issue-loop", digest(templates()["repository-issue-loop"]),
            {"name": "issues", "declaration": {}, "repo_root": ""},
        )
    assert not instance_dir().exists()
    assert not rd.pointers_file().exists()


def test_activation_requires_explicit_consent_before_effects():
    with pytest.raises(RegistrarError, match="explicit"):
        save_instance(
            "template:worker-pool", digest(templates()["worker-pool"]), fields(),
            activate=True,
        )
    assert not instance_dir().exists()


def test_missing_fleet_transport_refused_before_persistence(monkeypatch):
    monkeypatch.setattr("agent_dispatch.worker_instances.shutil.which", lambda _: None)
    with pytest.raises(RegistrarError, match="missing fleet dependency"):
        create(fleet={"pool": ["fixture-host"]})
    assert not instance_dir().exists()
    assert not rd.pointers_file().exists()


def test_fleet_save_does_not_probe_sleeping_hosts_or_spawn(monkeypatch):
    monkeypatch.setattr("agent_dispatch.worker_instances.shutil.which", lambda _: "fixture-ssh")

    def unauthorized(*args, **kwargs):
        pytest.fail("saving/browsing must not probe or spawn fleet hosts")

    monkeypatch.setattr("agent_dispatch.fleet.host_can_embody", unauthorized)
    monkeypatch.setattr("agent_dispatch.fleet.host_can_bridge", unauthorized)
    receipt = create(activate=True, fleet={"pool": ["fixture-host"]})
    row = next(r for r in catalog()["entries"] if r["id"] == receipt["id"])
    assert row["dependency_status"] == "resolved"
    assert row["activity"] == "unknown"
    assert "unknown here" in row["card"]["body"]
    assert receipt["activation"] == "pending"
    monkeypatch.setattr("agent_dispatch.worker_instances.shutil.which", lambda _: None)
    row = next(r for r in catalog()["entries"] if r["id"] == receipt["id"])
    assert row["dependency_status"] == "invalid-or-missing"
    assert "ssh transport" in row["dependency_errors"][0]


def test_source_provenance_unchanged_and_collision_refused(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    document = source / "pool.json"
    original = b'{"name":"local-pool","labels":["x"],"description":"Source owned"}'
    document.write_bytes(original)
    rd.add_pointer("source", source, owner="repo:source")
    row = next(r for r in catalog()["entries"] if r["title"] == "local-pool")
    assert row["group"] == "Source declarations" and row["editable"] == "false"
    assert row["provenance"] == "repo:source" and row["activity"] == "unknown"
    with pytest.raises(RegistrarError, match="collision"):
        create()
    assert document.read_bytes() == original


def test_pointer_collision_never_retargets_other_source(tmp_path):
    rd.add_pointer(POINTER, tmp_path / "other", owner="other")
    before = rd.pointers_file().read_bytes()
    with pytest.raises(RegistrarError, match="collides"):
        create(activate=True)
    assert rd.pointers_file().read_bytes() == before
    assert not instance_dir().exists()


def test_registry_error_is_visible_and_mutations_refuse(tmp_path):
    rd.registrar_dir().mkdir()
    rd.pointers_file().write_text("not-json", encoding="utf-8")
    data = catalog()
    assert data["summary"]["errors"]
    assert any(row["group"] == "Discovery errors" for row in data["entries"])
    with pytest.raises(RegistrarError, match="invalid pointer registry"):
        create()
    assert not instance_dir().exists()


def test_disabled_enabled_and_observed_activity_are_separate(monkeypatch):
    from agent_dispatch.config import overrides_path
    from agent_dispatch.overrides import logical_override_id, set_override
    from agent_dispatch.registrar_reconcile import declared_registration_id
    receipt = create(activate=True)
    rid = declared_registration_id(rd.discover()[0])
    monkeypatch.setattr(
        "agent_dispatch.workers_catalog._observation",
        lambda: {"known": True, "running": [rid]},
    )
    set_override(overrides_path(), logical_override_id(OWNER, "local-pool"), disabled=True)
    row = next(r for r in catalog()["entries"] if r["id"] == receipt["id"])
    assert row["enabled"] == "disabled" and row["activity"] == "observed-running"
    assert row["statistics"]["observed_running"] == 1
    edit(receipt, activate=True)
    row = next(r for r in catalog()["entries"] if r["id"] == receipt["id"])
    assert row["enabled"] == "enabled" and row["activity"] == "observed-running"


def test_failed_publication_receipt_distinguishes_persisted(monkeypatch):
    def fail(*args):
        raise OSError("simulated publication failure")
    monkeypatch.setattr("agent_dispatch.worker_instances._publish", fail)
    receipt = create(activate=True)
    assert receipt["persisted"] and receipt["activation"] == "failed"
    assert receipt["activation_error"] == "simulated publication failure"
    assert len(read_records()) == 1 and rd.discover() == []


def issue_fields(repo_root, *, name="issue-loop"):
    return {
        "name": name, "repo_root": str(repo_root),
        "declaration": {
            "repo": "example/project", "source": "repository-backlog",
            "task_label": "issue-work", "cadence_seconds": 3600,
            "forge": {"provider": "github", "producer_login": "issue-bot"},
            "reservation": {"label": "agent-reserved", "comment": True},
        },
    }


def test_composed_template_discovers_with_preserved_repo_context(tmp_path):
    values = issue_fields(tmp_path)
    receipt = save_instance(
        "template:goal-driven", digest(templates()["goal-driven"]), values,
        activate=True, consent=ACTIVATION_CONSENT,
    )
    assert receipt["activation"] == "pending"
    declarations = rd.discover()
    assert len(declarations) == 2
    emitter = next(d for d in declarations if d.kind == "emitter")
    assert emitter.spec["cwd"] == str(tmp_path.resolve())
    assert all(d.owner == OWNER for d in declarations)
    row = next(r for r in catalog()["entries"] if r["id"] == receipt["id"])
    assert row["publication"] == "published" and row["statistics"]["declarations"] == 2
    assert row["kind"] == "composite" and row["components_basis"] == "published"
    assert {component["kind"] for component in row["components"]} == {"emitter", "supervised-lane"}
    assert {component["id"] for component in row["components"]} == set(row["registration_ids"])
    assert all(component["repo_scopes"] == ["example/project"] for component in row["components"])
    assert all(component["labels"] == ["issue-work"] for component in row["components"])
    assert all(component["source_path"] == str(active_dir() / "issue-loop.json") for component in row["components"])
    assert "example/project" in row["card"]["body"] and "issue-work" in row["card"]["body"]
    assert len(rd.discover()) == 2


def test_missing_evaluator_dependency_refused_before_effects(tmp_path):
    with pytest.raises(RegistrarError, match="missing evaluator dependency"):
        save_instance(
            "template:backlog-triager", digest(templates()["backlog-triager"]),
            issue_fields(tmp_path),
        )
    assert not instance_dir().exists()


@pytest.mark.parametrize("precomputed", [False, True])
def test_dependency_validation_reuses_one_discovery_snapshot(monkeypatch, precomputed):
    from agent_dispatch.worker_instances import _validate_dependencies

    evaluator = SimpleNamespace(kind="evaluator", name="fixture-evaluator", spec={})
    declaration = SimpleNamespace(
        kind="emitter", fleet=SimpleNamespace(enabled=False),
        spec={"evaluator_ref": evaluator.name},
    )
    available = (evaluator,)
    calls = []

    def refresh(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(combined=SimpleNamespace(declarations=available))

    monkeypatch.setattr(rd.RegistrarSources, "refresh", refresh)
    options = {"available_declarations": available} if precomputed else {}
    _validate_dependencies((declaration, declaration), "", **options)
    assert len(calls) == (0 if precomputed else 1)
    with pytest.raises(RegistrarError, match="missing evaluator dependency"):
        _validate_dependencies((declaration,), "", available_declarations=())
    assert len(calls) == (0 if precomputed else 1)


def test_catalog_shares_discovery_snapshot_across_instance_validation(tmp_path, monkeypatch):
    from agent_dispatch import workers_catalog

    create("first")
    create("second")
    source = tmp_path / "source"
    source.mkdir()
    (source / "pool.json").write_text(json.dumps({
        **templates()["worker-pool"], "name": "source-pool", "owner": "fixture",
    }), encoding="utf-8")
    rd.add_pointer("source", source, owner="fixture")
    refresh = rd.RegistrarSources.refresh
    validate = workers_catalog._validate_dependencies
    snapshots = []
    validations = []

    def record_refresh(*args, **kwargs):
        report = refresh(*args, **kwargs)
        snapshots.append(report.combined.declarations)
        return report

    def record_validation(*args, **kwargs):
        validations.append(kwargs["available_declarations"])
        return validate(*args, **kwargs)

    monkeypatch.setattr(rd.RegistrarSources, "refresh", record_refresh)
    monkeypatch.setattr(workers_catalog, "_validate_dependencies", record_validation)
    assert catalog()["summary"]["configured"] == 2
    assert len(snapshots) == 1 and len(validations) == 3
    assert all(snapshot is snapshots[0] for snapshot in validations)


@pytest.mark.parametrize("failure", ["json", "encoding", "invalid", "envelope", "read"])
def test_catalog_reports_bad_publication_without_hiding_other_resources(monkeypatch, failure):
    create("broken", activate=True)
    create("healthy")
    publication = active_dir() / "broken.json"
    if failure == "json":
        publication.write_text("not-json", encoding="utf-8")
    elif failure == "encoding":
        publication.write_bytes(b"\xff")
    elif failure == "invalid":
        publication.write_text(json.dumps({"kind": "invalid-fixture-kind"}), encoding="utf-8")
    elif failure == "envelope":
        publication.write_text(json.dumps({"kind": "user-worker-instance"}), encoding="utf-8")
    else:
        read_text = Path.read_text

        def refuse(self, *args, **kwargs):
            if self == publication:
                raise PermissionError("fixture permission denied")
            return read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", refuse)
    data = catalog()
    row = next(r for r in data["entries"] if r["id"] == "instance:broken")
    assert row["validity"] == "invalid" and row["publication"] == "error"
    assert row["enabled"] == row["activity"] == "unknown"
    assert row["pool_toggleable"] == "false" and row["statistics"]["lanes"] is None
    assert row["draft_statistics"]["lanes"] == 1
    assert row["publication_errors"] and row["publication_errors"][0] in data["summary"]["errors"]
    assert "requires repair" in row["card"]["body"]
    assert any(r["id"] == "instance:healthy" for r in data["entries"])
    assert any(r["id"] == "template:worker-pool" for r in data["entries"])


@pytest.mark.parametrize("failure", ["json", "encoding", "identity", "revision", "shape", "source"])
def test_corrupt_draft_is_visible_without_hiding_healthy_resources(failure):
    create("broken")
    create("healthy")
    path = instance_dir() / "broken.json"
    if failure == "json":
        path.write_text("not-json", encoding="utf-8")
    elif failure == "encoding":
        path.write_bytes(b"\xff")
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        if failure == "shape":
            record["configuration"] = []
            record["revision"] = digest(record["configuration"])
        elif failure == "source":
            record["source"] = "corrupt"
        else:
            record["name" if failure == "identity" else "revision"] = "corrupt"
        path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    data = catalog()
    assert any(str(path) in error for error in data["summary"]["errors"])
    assert any(
        str(path) in row["description"] and row["validity"] == "error"
        and row["editable"] == "false"
        for row in data["entries"] if row["group"] == "Discovery errors"
    )
    assert any(row["id"] == "instance:healthy" for row in data["entries"])
    assert any(row["id"] == "template:worker-pool" for row in data["entries"])
    with pytest.raises(RegistrarError, match="cannot read instance"):
        read_records()
    with pytest.raises(RegistrarError, match="cannot read instance"):
        create("third")
    assert path.read_bytes() == before
    assert not (instance_dir() / "third.json").exists()


def test_missing_emitter_command_refused_before_effects():
    with pytest.raises(RegistrarError, match="missing command dependency"):
        save_instance(
            "template:reviewer", digest(templates()["reviewer"]),
            {"name": "review", "declaration": {
                "repo": "github.com/example/project", "task_label": "review-work",
                "emitter": {"command": ["nonexistent-worker-fixture-command"], "interval_seconds": 60},
                "evaluator": {"evaluator_spec": {"rules": []}},
            }},
        )
    assert not instance_dir().exists()


def test_reviewer_script_and_relative_cwd_keep_repository_context(tmp_path):
    script = tmp_path / "reviews.py"
    script.write_text("print('fixture only')\n", encoding="utf-8")
    receipt = save_instance(
        "template:reviewer", digest(templates()["reviewer"]),
        {"name": "reviews", "repo_root": str(tmp_path), "declaration": {
            "repo": "github.com/example/project", "task_label": "review-work",
            "emitter": {"command": [sys.executable, "reviews.py"], "cwd": ".", "interval_seconds": 60},
            "evaluator": {"evaluator_spec": {"rules": []}},
        }}, activate=True, consent=ACTIVATION_CONSENT,
    )
    assert receipt["activation"] == "pending"
    emitter = next(d for d in rd.discover() if d.kind == "emitter")
    assert emitter.spec["cwd"] == str(tmp_path.resolve())


def test_saved_draft_cannot_release_another_instances_published_names(tmp_path):
    receipt = save_instance(
        "template:reviewer", digest(templates()["reviewer"]),
        {"name": "reviews", "repo_root": str(tmp_path), "declaration": {
            "repo": "github.com/example/project", "task_label": "review-work",
            "emitter": {"command": [sys.executable, "-c", "print('[]')"], "interval_seconds": 60},
            "evaluator": {"evaluator_spec": {"rules": []}},
        }}, activate=True, consent=ACTIVATION_CONSENT,
    )
    active = (active_dir() / "reviews.json").read_bytes()
    save_instance(
        receipt["id"], receipt["revision"],
        fields("reviews"),
    )
    with pytest.raises(RegistrarError, match="name collision"):
        create("reviews-source", activate=True)
    assert not (instance_dir() / "reviews-source.json").exists()
    assert (active_dir() / "reviews.json").read_bytes() == active
    assert not rd.RegistrarSources().refresh(emit_warnings=False).combined.findings
    # Editing the rightful instance can replace its own published expansion.
    current = next(r for r in read_records() if r["name"] == "reviews")
    published = save_instance(
        current["id"], current["revision"], fields("reviews"),
        activate=True, consent=ACTIVATION_CONSENT,
    )
    assert published["activation"] == "pending"
    assert len(rd.discover()) == 1


def test_catalog_enable_clears_all_applicable_disable_masks(capsys):
    from agent_dispatch.__main__ import build_parser
    from agent_dispatch.config import overrides_path
    from agent_dispatch.overrides import load_overrides, set_override, overridden_off_ids
    from agent_dispatch.registrar_reconcile import declaration_to_registration
    from agent_dispatch.supervisor_registration import registration_override_ids

    create(activate=True)
    declaration = rd.discover()[0]
    tokens = registration_override_ids(declaration_to_registration(declaration, machine=None))
    for token in tokens:
        set_override(overrides_path(), token, disabled=True)
    set_override(overrides_path(), "unrelated-worker", disabled=True)
    assert next(r for r in catalog()["entries"] if r["id"] == "instance:local-pool")["enabled"] == "disabled"
    args = build_parser().parse_args([
        "workers", "config-section", "local-pool", "--owner", OWNER,
        "--toggle", "enable", "--require-declared", "--clear-all-masks",
    ])
    assert args.func(args) == 0
    assert "enabled (running unknown)" in capsys.readouterr().out
    remaining = overridden_off_ids(load_overrides(overrides_path()))
    assert not remaining.intersection(tokens) and "unrelated-worker" in remaining
    row = next(r for r in catalog()["entries"] if r["id"] == "instance:local-pool")
    assert row["enabled"] == "enabled" and row["activity"] == "unknown"


def test_local_envelope_refused_outside_instance_active_directory(tmp_path):
    path = tmp_path / "foreign.json"
    path.write_text(json.dumps({
        "kind": "user-worker-instance", "configuration": templates()["worker-pool"],
        "repo_root": str(tmp_path),
    }), encoding="utf-8")
    with pytest.raises(RegistrarError, match="outside"):
        rd.read_declaration_file_set(path)


@pytest.mark.parametrize("error", [OSError("permission denied"), RuntimeError("symlink loop")])
@pytest.mark.parametrize("directory", ["source", "active"])
def test_local_envelope_resolution_failure_is_indeterminate(tmp_path, monkeypatch, error, directory):
    path = tmp_path / "source" / "instance.json"
    path.parent.mkdir()
    path.write_text(json.dumps({
        "kind": "user-worker-instance", "configuration": templates()["worker-pool"],
    }), encoding="utf-8")
    target = path.parent if directory == "source" else active_dir()
    resolve = Path.resolve

    def fail_target(self, *args, **kwargs):
        if self == target:
            raise error
        return resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_target)
    with pytest.raises(rd.RegistrarIndeterminateError, match="could not be resolved") as caught:
        rd.read_declaration_file_set(path)
    assert str(path) in str(caught.value)
    assert caught.value.__cause__ is error


def test_pointer_transaction_preserves_concurrent_distinct_sources(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    pointers = [rd.Pointer(f"source-{i}", str(tmp_path / f"source-{i}")) for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(rd.ensure_pointer, pointers)) == pointers
    assert set(rd.load_pointers()) == set(pointers)


def test_duplicate_discovery_source_error_refuses_mutation(tmp_path):
    for name in ("first", "second"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "pool.json").write_text('{"name":"same"}', encoding="utf-8")
        rd.add_pointer(name, directory, owner=name)
    assert catalog()["summary"]["errors"]
    with pytest.raises(RegistrarError, match="errors/ambiguities"):
        create()
    assert read_records() == []


def test_atomic_edit_failure_preserves_baseline(monkeypatch):
    receipt = create()
    path = instance_dir() / "local-pool.json"
    before = path.read_bytes()
    def fail(*args):
        raise OSError("simulated replace failure")
    monkeypatch.setattr("agent_dispatch.worker_instances.os.replace", fail)
    with pytest.raises(OSError, match="replace failure"):
        edit(receipt, concurrency=3)
    assert path.read_bytes() == before
    assert not list(instance_dir().glob("*.pending"))


def test_template_revision_and_renamed_edit_fail_before_effects():
    with pytest.raises(RegistrarError, match="changed"):
        save_instance("template:worker-pool", "old-template", fields())
    assert not instance_dir().exists()
    receipt = create()
    with pytest.raises(RegistrarError, match="identity changed"):
        save_instance(receipt["id"], receipt["revision"], fields("renamed"))


def test_cli_and_form_contract(capsys):
    from agent_dispatch.__main__ import build_parser
    row = next(r for r in catalog()["entries"] if r["id"] == "template:worker-pool")
    argv = ["workers", "save", row["id"], "--revision", row["revision"]]
    for key, value in fields().items():
        argv += ["--field", f"{key}={json.dumps(value) if isinstance(value, dict) else value}"]
    args = build_parser().parse_args(argv)
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["activation"] == "not-requested"
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "pivots" / "workers-config.json").read_text(encoding="utf-8"))
    assert manifest["placement"] == "config" and manifest["scope"] == "global"
    forms = [action for action in manifest["actions"] if action.get("kind") == "form"]
    assert len(forms) == 4 and all(action["confirm"] for action in forms)
    assert all(action["fields_from"] == "fields" for action in forms)
    assert "config_sections" not in json.loads((root / "pivots" / "agent-dispatch.json").read_text(encoding="utf-8"))


def test_real_observation_requires_fresh_receipt(monkeypatch):
    monkeypatch.setattr("agent_dispatch.remote_dispatch.local_machine", lambda: "fixture-host")
    monkeypatch.setattr(
        "agent_dispatch.supervise_cli._read_supervisor_runtime_status",
        lambda scope: ({"updated_at": time.time() - 1000, "running": []}, None),
    )
    assert not real_observation()["known"]
    monkeypatch.setattr(
        "agent_dispatch.supervise_cli._read_supervisor_runtime_status",
        lambda scope: ({"updated_at": time.time(), "running": []}, None),
    )
    assert real_observation()["known"]


def test_existing_daemon_reconciles_saved_activation_without_duplicate_work():
    from tests.test_supervisor_daemon import FakeClient, FakeLauncher, _daemon
    launcher = FakeLauncher()
    client = FakeClient([])
    daemon = _daemon(client, launcher, declared_source=rd.discover)
    create()
    assert daemon.reconcile_once().running == []
    receipt = create(activate=True)
    first = daemon.reconcile_once()
    assert len(first.running) == len(launcher.launched) == 1
    assert daemon.reconcile_once().running == first.running
    create(activate=True)
    daemon.reconcile_once()
    assert len(launcher.launched) == 1
    edit(receipt, activate=True, concurrency=2)
    daemon.reconcile_once()
    assert len(launcher.launched) == 2
    assert launcher.launched[0][0] == launcher.launched[1][0]
    daemon.shutdown()


def test_real_cli_first_touch_save_catalog_and_activation(tmp_path):
    def invoke(*args):
        result = subprocess.run(
            [sys.executable, "-m", "agent_dispatch", "workers", *args],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "AGENT_DISPATCH_NO_AUTOSTART": "1"},
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    initial = invoke("catalog")
    assert initial["schema_version"] == 1
    assert initial["summary"]["configured"] == 0
    row = next(r for r in initial["entries"] if r["id"] == "template:worker-pool")
    arguments = [
        "save", row["id"], "--revision", row["revision"],
        "--field", "name=cli-fixture", "--field", 'declaration={"labels":["work"]}',
    ]
    saved = invoke(*arguments)
    assert saved["persisted"] and saved["activation"] == "not-requested"
    assert not rd.pointers_file().exists()
    activated = invoke(*arguments, "--activate", "--consent", ACTIVATION_CONSENT)
    assert activated["id"] == saved["id"] and activated["activation"] == "pending"
    assert len(rd.discover()) == 1
    published = next(row for row in invoke("catalog")["entries"] if row["id"] == saved["id"])
    assert published["kind"] == "supervised-lane" and published["components_basis"] == "published"
    assert [component["id"] for component in published["components"]] == published["registration_ids"]
    assert invoke(*arguments, "--activate", "--consent", ACTIVATION_CONSENT)["retry"]
    source = tmp_path / "incomplete-routing-source"
    source.mkdir()
    path = source / "schedule.json"
    path.write_text(json.dumps({
        "name": "bad-routing", "kind": "schedule",
        "spec": {"id": "bad-routing", "repo": "example/project", "labels": False},
    }), encoding="utf-8")
    before = path.read_bytes()
    rd.add_pointer("incomplete-routing", source, owner="repo:source")
    degraded = invoke("catalog")
    bad = next(row for row in degraded["entries"] if row["title"] == "bad-routing")
    assert bad["validity"] == "metadata-error"
    assert bad["component_errors"][0] in degraded["summary"]["errors"]
    assert any(row["id"] == saved["id"] for row in degraded["entries"])
    assert any(row["id"] == "template:worker-pool" for row in degraded["entries"])
    assert path.read_bytes() == before
