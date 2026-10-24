"""Qualified templates materialize only through the existing instance lifecycle."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from dropin_registry import ScanAuthority
from plugin_activation import ActivationReport
import pytest

from agent_dispatch import registrar_discovery as rd, worker_templates as wt
from agent_dispatch.registrar import RegistrarError
from agent_dispatch.worker_instances import (
    ACTIVATION_CONSENT, active_dir, instance_dir, read_records, save_instance,
)
from agent_dispatch.workers_catalog import catalog


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(tmp_path / "registrar"))
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR", str(tmp_path / "dropins"))
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(tmp_path / "overrides.json"))
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", activation)
    monkeypatch.setattr("agent_dispatch.workers_catalog._observation", lambda: {"known": False})
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='{"inventory_authority":"complete","repos":[]}', stderr="",
    ))


def activation():
    return ActivationReport(authority=ScanAuthority.COMPLETE, decisions={})


def provider(document, *, kind=None):
    root = rd.registrar_dir() / "templates"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "fragment.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    index = root / "index.json"
    index.write_text(json.dumps({
        "schema": "agent-dispatch.template-index", "version": 1, "templates": [{
            "id": "fixture", "title": "Fixture", "description": "Fixture provider fragment",
            "kind": kind or document["kind"], "declaration": "fragment.json",
        }],
    }), encoding="utf-8")
    return path, next(row for row in catalog()["entries"] if row["id"].startswith("template:provider:"))


def save(row, *, name="local", document=None, root="", activate=False):
    return save_instance(row["id"], row["revision"], {
        "name": name, "declaration": document or {}, "repo_root": root,
    }, activate=activate, consent=ACTIVATION_CONSENT if activate else None)


def pool():
    return {"kind": "supervised-lane", "labels": ["{label}"], "body": {"type": "headless"}}


def test_parameterized_provider_save_retry_edit_and_publication_keep_source_immutable():
    path, row = provider(pool())
    before = path.read_bytes()
    assert row["instantiable"] == "true" and row["fields"][1]["default"] == "{}"
    assert "Does not edit" in row["save_card"]["body"]
    result = save(row, document={"params": {"label": "fixture"}, "concurrency": 2})
    assert result["persisted"] and not result["retry"] and result["activation"] == "not-requested"
    assert not active_dir().exists() and not rd.pointers_file().exists()
    record, = read_records()
    assert record["configuration"]["declaration"]["labels"] == ["fixture"]
    assert "extends" not in record["configuration"]["declaration"]
    assert record["source"]["id"] == row["id"] and record["source"]["revision"] == row["revision"]
    assert record["source"]["context"] == row["source"]
    assert save(row, document={"params": {"label": "fixture"}, "concurrency": 2})["retry"]
    with pytest.raises(RegistrarError, match="already exists"):
        save(row, document={"params": {"label": "other"}})
    changed = copy.deepcopy(record["configuration"]["declaration"])
    changed["concurrency"] = 3
    edited = save_instance(result["id"], result["revision"], {
        "name": "local", "declaration": changed, "repo_root": "",
    }, activate=True, consent=ACTIVATION_CONSENT)
    assert edited["activation"] == "pending"
    assert read_records()[0]["source"] == record["source"]
    declaration, = rd.read_declaration_file_set(active_dir() / "local.json")
    assert declaration.labels == ("fixture",) and declaration.concurrency == 3
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["fragment", "ancestor", "index", "removed"])
def test_source_and_recipe_closure_revisions_refuse_stale_save(change):
    path, row = provider({"extends": "./base.json"}, kind="supervised-lane")
    base = path.parent / "base.json"
    base.write_text(json.dumps(pool()), encoding="utf-8")
    source_id = row["id"]
    row = next(row for row in catalog()["entries"] if row["id"] == source_id)
    if change == "fragment":
        path.write_text('{"kind":"supervised-lane","labels":["changed"]}', encoding="utf-8")
    elif change == "ancestor":
        base.write_text('{"kind":"supervised-lane","labels":["changed"]}', encoding="utf-8")
    elif change == "index":
        index = path.parent / "index.json"
        index.write_text(index.read_text().replace("Fixture provider", "Changed provider"), encoding="utf-8")
    else:
        path.unlink()
    with pytest.raises(RegistrarError, match="absent or changed"):
        save(row, document={"params": {"label": "fixture"}})
    assert not instance_dir().exists()


def test_source_change_during_validation_refuses_before_record_write(monkeypatch):
    import agent_dispatch.worker_instances as wi
    path, row = provider(pool())
    original = wi._check_collisions
    def change(*args):
        original(*args)
        path.write_text('{"kind":"supervised-lane","labels":["changed"]}', encoding="utf-8")
    monkeypatch.setattr(wi, "_check_collisions", change)
    with pytest.raises(RegistrarError, match="absent or changed"):
        save(row, document={"params": {"label": "fixture"}})
    assert read_records() == [] and not rd.pointers_file().exists()


def test_source_relative_emitter_cwd_is_frozen_and_real_consumer_uses_it(tmp_path):
    path, row = provider({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": [sys.executable, "producer.py"],
            "cwd": ".", "interval_seconds": 60,
        },
    })
    (path.parent / "producer.py").write_text("import os; print(os.getcwd())", encoding="utf-8")
    result = save(row, activate=True)
    declaration, = rd.read_declaration_file_set(active_dir() / "local.json")
    assert result["activation"] == "pending"
    assert declaration.spec["cwd"] == str(path.parent)
    child = subprocess.run(
        declaration.spec["command"], cwd=declaration.spec["cwd"],
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert child.returncode == 0, child.stderr
    assert Path(child.stdout.strip()) == path.parent
    assert "producer.py" not in json.dumps(row["components"])


@pytest.mark.parametrize("override", [
    {"extends": "global:reviewer"}, {"name": "other"}, {"owner": "foreign"},
])
def test_source_identity_cannot_be_replaced_by_override(override):
    _, row = provider(pool())
    with pytest.raises(RegistrarError):
        save(row, document=override)
    assert not instance_dir().exists()


def test_unsupported_kind_and_missing_recipe_keep_healthy_builtin_actions():
    path, row = provider({"kind": "composite"}, kind="composite")
    assert row["instantiable"] == "false" and "fields" not in row
    with pytest.raises(RegistrarError, match="cannot be materialized"):
        save(row)
    path.write_text('{"extends":"missing.json"}', encoding="utf-8")
    data = catalog()
    row = next(r for r in data["entries"] if r["id"] == row["id"])
    assert row["instantiable"] == "false" and row["dependency_errors"]
    assert next(r for r in data["entries"] if r["id"] == "template:worker-pool")["instantiable"] == "true"
    assert data["summary"]["errors"] == []


def test_real_cli_first_touch_save_uses_selected_revision_and_stays_inert():
    _, row = provider(pool())
    code = """
import runpy, sys
from types import SimpleNamespace
from agent_dispatch import worker_templates
worker_templates.run_agent_worktrees_capture = lambda *a, **k: SimpleNamespace(
    returncode=0, stdout='{"inventory_authority":"complete","repos":[]}', stderr='')
sys.argv = ['agent-dispatch', 'workers', 'save', sys.argv[1], '--revision', sys.argv[2],
    '--field', 'name=from-cli', '--field', 'declaration={"params":{"label":"fixture"}}']
runpy.run_module('agent_dispatch', run_name='__main__')
"""
    child = subprocess.run(
        [sys.executable, "-c", code, row["id"], row["revision"]],
        env={**os.environ, "AGENT_DISPATCH_NO_AUTOSTART": "1"},
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)["activation"] == "not-requested"
    assert read_records()[0]["configuration"]["declaration"]["labels"] == ["fixture"]
    assert not rd.pointers_file().exists() and not active_dir().exists()


def test_reviewer_inherited_relative_command_and_cwd_keep_defining_directory():
    path, row = provider({
        "extends": "global:reviewer", "repo": "example/project", "task_label": "reviews",
        "emitter": {
            "command": [sys.executable, "reviews.py"], "cwd": ".", "interval_seconds": 60,
        },
        "evaluator": {"evaluator_spec": {"rules": []}},
    }, kind="reviewer-loop")
    (path.parent / "reviews.py").write_text("print('[]')", encoding="utf-8")
    assert save(row, activate=True)["activation"] == "pending"
    declarations = rd.read_declaration_file_set(active_dir() / "local.json")
    emitter = next(d for d in declarations if d.kind == "emitter")
    assert emitter.spec["cwd"] == str(path.parent)
    assert emitter.spec["command"] == [sys.executable, "reviews.py"]


@pytest.mark.parametrize("different_root", [False, True])
def test_identity_uses_defining_source_or_refuses_relocation(tmp_path, different_root):
    from tests.test_registrar_path_origins import _identity
    from tests.test_repository_issue_loops import _config
    from agent_dispatch.repository_issue_loops import validate_config

    source = rd.registrar_dir()
    source.mkdir()
    (source / ".git").mkdir()
    _identity(source, "SOURCE-RULES")
    target = tmp_path / "target"
    _identity(target, "WRONG-TARGET-RULES")
    _, row = provider(_config(worker_identity="worker"))
    if different_root:
        with pytest.raises(RegistrarError, match="source-relative dependencies"):
            save(row, root=str(target))
        assert not instance_dir().exists()
    else:
        assert save(row, root=str(source), activate=True)["activation"] == "pending"
        declaration = rd.read_declaration_file_set(active_dir() / "local.json")[0]
        config = validate_config(
            declaration.spec["repository_issue_loop"], cwd=declaration.spec["cwd"],
            worker_identity_cwd=declaration.spec.get("worker_identity_cwd"),
        )
        assert config["worker_guidance"] == "SOURCE-RULES"


def test_cross_repository_relative_script_refusal_is_not_bypassed(tmp_path):
    from tests.test_repository_issue_loops import _config

    source = rd.registrar_dir()
    source.mkdir()
    (source / ".git").mkdir()
    target = tmp_path / "target"
    target.mkdir()
    _, row = provider(_config(forge={
        "provider": "script", "producer_login": "fixture-bot",
        "command": [sys.executable, "adapter.py"], "cwd": ".",
    }))
    with pytest.raises(RegistrarError, match="relative"):
        save(row, root=str(target))
    assert not instance_dir().exists()


def test_missing_worker_identity_refuses_before_persistence():
    from tests.test_repository_issue_loops import _config

    _, row = provider(_config(worker_identity="unavailable-fixture-identity"))
    with pytest.raises(RegistrarError, match="no identity file"):
        save(row, root=str(rd.registrar_dir()))
    assert not instance_dir().exists()


def test_partial_publication_receipt_retries_same_provider_instance(monkeypatch):
    import agent_dispatch.worker_instances as wi

    _, row = provider(pool())
    original = wi._publish
    def fail(*args):
        raise OSError("fixture publication failure")
    monkeypatch.setattr(wi, "_publish", fail)
    receipt = save(row, document={"params": {"label": "fixture"}}, activate=True)
    assert receipt["persisted"] and receipt["activation"] == "failed"
    monkeypatch.setattr(wi, "_publish", original)
    retried = save(row, document={"params": {"label": "fixture"}}, activate=True)
    assert retried["id"] == receipt["id"] and retried["revision"] == receipt["revision"]
    assert retried["retry"] and retried["activation"] == "pending"
    assert len(read_records()) == 1


def test_inherited_dependency_change_does_not_rewrite_existing_draft():
    path, row = provider({"extends": "./base.json"}, kind="supervised-lane")
    base = path.parent / "base.json"
    base.write_text(json.dumps(pool()), encoding="utf-8")
    row = wt.discover_templates(activation())[0][0]
    receipt = save(row, document={"params": {"label": "fixture"}})
    stored = (instance_dir() / "local.json").read_bytes()
    base.write_text('{"kind":"supervised-lane","labels":["changed"]}', encoding="utf-8")
    updated = catalog()
    assert (instance_dir() / "local.json").read_bytes() == stored
    assert next(r for r in updated["entries"] if r["id"] == receipt["id"])["editable"] == "true"


def test_template_plugin_activation_is_fresh_not_process_cached(monkeypatch, tmp_path):
    from plugin_activation import ActivePluginRoot
    from tests.test_worker_templates import plugin_report, write_index

    root = tmp_path / "plugin"
    _, entry = write_index(root)
    (root / "recipes" / "source.json").write_text(json.dumps(pool()), encoding="utf-8")
    index = root / "dispatch-templates.json"
    data = json.loads(index.read_text())
    data["templates"][0]["kind"] = "supervised-lane"
    index.write_text(json.dumps(data), encoding="utf-8")
    report = plugin_report(ActivePluginRoot(root, ("global",), "installed"))
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", lambda: report)
    row = wt.discover_templates(report)[0][0]
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", activation)
    with pytest.raises(RegistrarError, match="absent or changed"):
        save(row, document={"params": {"label": "fixture"}})
    assert not instance_dir().exists()


@pytest.mark.parametrize("absolute_cwd", [False, True])
@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_registered_repository_context_stays_named_through_local_publication(
    monkeypatch, tmp_path, absolute_cwd, repo_class,
):
    from agent_dispatch import repository_resources

    root = tmp_path / "reference-library"
    index = root / ".copilot-extensions" / "agent-dispatch" / "templates" / "index.json"
    index.parent.mkdir(parents=True)
    (index.parent / "producer.py").write_text("print('[]')", encoding="utf-8")
    (index.parent / "source.json").write_text(json.dumps({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": [sys.executable, "producer.py"],
            "cwd": str(index.parent) if absolute_cwd else ".", "interval_seconds": 60,
        },
    }), encoding="utf-8")
    index.write_text(json.dumps({
        "schema": "agent-dispatch.template-index", "version": 1, "templates": [{
            "id": "named", "title": "Named source", "description": "Named reference source.",
            "kind": "emitter", "declaration": "source.json",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(wt, "run_agent_worktrees_capture", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='{"inventory_authority":"complete","repos":[{"name":"library"}]}', stderr="",
    ))
    monkeypatch.setattr(repository_resources, "_registered_repository", lambda *a, **k: (root, repo_class))
    row = wt.discover_templates(activation())[0][0]
    assert save(row, root=str(root), activate=True)["activation"] == "pending"
    declaration, = rd.read_declaration_file_set(active_dir() / "local.json")
    assert declaration.spec["cwd"] == "repo:library:.copilot-extensions/agent-dispatch/templates"
    assert declaration.spec["command"] == [sys.executable, "producer.py"]
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        repository_resources.resolve_execution_resources(
            declaration.spec["command"], declaration.spec["cwd"],
        )


@pytest.mark.parametrize("multiple_roots", [False, True])
def test_plugin_recipe_uses_current_activation_or_refuses_ambiguous_roots(monkeypatch, tmp_path, multiple_roots):
    from plugin_activation import ActivePluginRoot
    from tests.test_worker_templates import plugin_report
    from agent_dispatch import registrar_recipes

    root = tmp_path / "active-plugin"
    root.mkdir()
    (root / "base.json").write_text(json.dumps(pool()), encoding="utf-8")
    roots = [ActivePluginRoot(root, ("global",), "installed")]
    if multiple_roots:
        other = tmp_path / "other-plugin"
        other.mkdir()
        (other / "base.json").write_text(json.dumps(pool()), encoding="utf-8")
        roots.append(ActivePluginRoot(other, ("project:fixture",), "local"))
    report = plugin_report(*roots)
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", lambda: report)
    monkeypatch.setattr(registrar_recipes, "_ACTIVE_PLUGINS_CACHE", activation())
    _, row = provider(
        {"extends": "plugin:example-provider@example-market:base.json"},
        kind="supervised-lane",
    )
    if multiple_roots:
        assert row["instantiable"] == "false" and "ambiguous" in row["unsupported_reason"]
        with pytest.raises(RegistrarError, match="ambiguous"):
            save(row)
        assert not instance_dir().exists()
    else:
        assert row["instantiable"] == "true"
        assert save(row, document={"params": {"label": "fixture"}})["persisted"]
        monkeypatch.setattr("plugin_activation.resolve_active_plugins", activation)
        with pytest.raises(RegistrarError, match="absent or changed"):
            save(row, name="second")


def test_legacy_repository_pointer_is_browsable_but_not_instantiable(tmp_path):
    root = tmp_path / "unregistered"
    root.mkdir()
    index = root / ".copilot-extensions" / "agent-dispatch" / "templates" / "index.json"
    index.parent.mkdir(parents=True)
    (index.parent / "fragment.json").write_text(json.dumps(pool()), encoding="utf-8")
    index.write_text(json.dumps({
        "schema": "agent-dispatch.template-index", "version": 1, "templates": [{
            "id": "legacy", "title": "Legacy", "description": "Unregistered pointer.",
            "kind": "supervised-lane", "declaration": "fragment.json",
        }],
    }), encoding="utf-8")
    rd.add_pointer("legacy", root, kind="repo")
    row = wt.discover_templates(activation())[0][0]
    assert row["instantiable"] == "false" and "canonical registered" in row["unsupported_reason"]


@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_composed_repository_source_cannot_lose_implicit_supplier_context(monkeypatch, repo_class):
    from agent_dispatch import repository_resources
    from tests.test_repository_issue_loops import _config

    path, _ = provider(_config())
    root = rd.registrar_dir()
    monkeypatch.setattr(wt, "_repository_sources", lambda findings: [wt.TemplateSource(
        "repo:library", root, path.parent / "index.json", ("repo:library",),
        "repository-layer:templates",
    )])
    monkeypatch.setattr(repository_resources, "_registered_repository", lambda *a, **k: (root, repo_class))
    row = next(r for r in catalog()["entries"] if r["provenance"] == "repo:library")
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        save(row, root=str(root), activate=True)
    assert not instance_dir().exists() and not active_dir().exists()


@pytest.mark.parametrize("policy", ["./rules.json", "repo:library:rules.json"])
def test_relative_file_backed_evaluator_refuses_instead_of_rebasing(policy):
    _, row = provider({
        "kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "fixture", "evaluator": policy,
        },
    })
    with pytest.raises(RegistrarError, match="file-backed evaluator"):
        save(row)
    assert not instance_dir().exists()


def test_effort_identity_relocation_refuses_before_selecting_target_rules(tmp_path):
    from tests.test_registrar_path_origins import _identity

    source = rd.registrar_dir()
    source.mkdir()
    (source / ".git").mkdir()
    _identity(source, "SOURCE-RULES")
    target = tmp_path / "target"
    _identity(target, "WRONG-TARGET-RULES")
    _, row = provider({
        "kind": "effort-driver-loop", "repo": "example/project",
        "source": "fixture", "task_label": "fixture", "cadence_seconds": 60,
        "effort_slugs": ["fixture-effort"],
        "worker_identity": "worker", "state_root": str(tmp_path),
        "pool": {"body": {"type": "headless", "agent": "task-worker"}},
    })
    with pytest.raises(RegistrarError, match="effort worker identity source context"):
        save(row, root=str(target))
    assert not instance_dir().exists()
    assert save(row, root=str(source))["persisted"]


def test_emitter_runtime_tokens_validate_without_changing_persisted_argv():
    path, row = provider({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": ["{python}", "producer.py"],
            "cwd": ".", "interval_seconds": 60,
        },
    })
    (path.parent / "producer.py").write_text("print('[]')", encoding="utf-8")
    assert save(row)["persisted"]
    assert read_records()[0]["configuration"]["declaration"]["spec"]["command"] == [
        "{python}", "producer.py",
    ]


@pytest.mark.parametrize("policy", ["./rules.json", "repo:library:rules.json"])
def test_lane_file_backed_evaluator_rejects_unsupported_context(policy):
    _, row = provider({**pool(), "evaluator": policy})
    with pytest.raises(RegistrarError, match="source-relative evaluator"):
        save(row)
    assert not instance_dir().exists()


@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_inherited_absolute_supplier_cwd_cannot_bypass_named_admission(monkeypatch, tmp_path, repo_class):
    from agent_dispatch import repository_resources

    supplier = tmp_path / "supplier"
    supplier.mkdir()
    (supplier / "producer.py").write_text("print('[]')", encoding="utf-8")
    (supplier / "base.json").write_text(json.dumps({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": ["{python}", "producer.py"],
            "cwd": str(supplier), "interval_seconds": 60,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(repository_resources, "_registered_repository", lambda *a, **k: (supplier, repo_class))
    _, row = provider({"extends": "repo:library:base.json"}, kind="emitter")
    assert save(row, activate=True)["activation"] == "pending"
    declaration, = rd.read_declaration_file_set(active_dir() / "local.json")
    assert declaration.spec["cwd"] == "repo:library:."
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        repository_resources.resolve_execution_resources(
            [sys.executable, "producer.py"], declaration.spec["cwd"],
        )


def test_inherited_evaluator_supplier_cwd_retains_actual_runtime_admission(monkeypatch, tmp_path):
    from agent_dispatch import repository_resources
    from agent_dispatch.producers.evaluator import EvaluatorRuntimeError, load_registration_evaluator

    supplier = tmp_path / "supplier"
    supplier.mkdir()
    (supplier / "policy.py").write_text("print('never-executed')", encoding="utf-8")
    (supplier / "base.json").write_text(json.dumps({
        "kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "policy",
            "evaluator_spec": {
                "scripts": {"policy": [sys.executable, "policy.py"]}, "cwd": str(supplier),
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(repository_resources, "_registered_repository", lambda *a, **k: (supplier, "reference"))
    _, row = provider({"extends": "repo:library:base.json"}, kind="evaluator")
    assert save(row, activate=True)["activation"] == "pending"
    declaration, = rd.read_declaration_file_set(active_dir() / "local.json")
    assert declaration.spec["evaluator_spec"]["cwd"] == "repo:library:."
    evaluator = load_registration_evaluator(
        dict(declaration.spec), runner=lambda *a, **k: pytest.fail("reference-only script executed"),
    )
    with pytest.raises(EvaluatorRuntimeError, match="reference-only"):
        evaluator.evaluate({})


@pytest.mark.parametrize("kind", ["evaluator", "supervised-lane"])
def test_supplier_owned_absolute_file_policy_refuses_unrepresentable_named_context(
    monkeypatch, tmp_path, kind,
):
    from agent_dispatch import repository_resources

    supplier = tmp_path / "supplier"
    supplier.mkdir()
    policy = supplier / "rules.json"
    policy.write_text(json.dumps({
        "scripts": {"policy": [sys.executable, "-c", "print('never-executed')"]},
        "cwd": str(supplier),
    }), encoding="utf-8")
    document = (
        {"kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "policy", "evaluator": str(policy),
        }} if kind == "evaluator" else {**pool(), "evaluator": str(policy)}
    )
    (supplier / "base.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(repository_resources, "_registered_repository", lambda *a, **k: (supplier, "reference"))
    _, row = provider({"extends": "repo:library:base.json"}, kind=kind)
    with pytest.raises(RegistrarError, match="supplier-owned file-backed"):
        save(row, activate=True)
    assert not instance_dir().exists() and not active_dir().exists()


@pytest.mark.parametrize("kind", ["evaluator", "supervised-lane"])
def test_missing_file_policy_refuses_before_persistence(tmp_path, kind):
    policy = str(tmp_path / "missing-policy.json")
    document = (
        {"kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "policy", "evaluator": policy,
        }} if kind == "evaluator" else {**pool(), "evaluator": policy}
    )
    _, row = provider(document)
    with pytest.raises(RegistrarError, match="evaluator dependency"):
        save(row, activate=True)
    assert not instance_dir().exists() and not active_dir().exists()


@pytest.mark.parametrize("file_backed", [False, True])
@pytest.mark.parametrize("missing", ["executable", "script", "cwd"])
def test_selected_evaluator_script_dependencies_are_checked_read_only(tmp_path, file_backed, missing):
    command = (
        ["not-installed-evaluator-fixture"] if missing == "executable"
        else [sys.executable, str(tmp_path / "missing.py")] if missing == "script"
        else [sys.executable, "-c", "print('never-executed')"]
    )
    policy = {
        "scripts": {"policy": command},
        "cwd": str(tmp_path / "missing-directory" if missing == "cwd" else tmp_path),
    }
    if file_backed:
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        selected = {"evaluator": str(path)}
    else:
        selected = {"evaluator_spec": policy}
    _, row = provider({
        "kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "policy", **selected,
        },
    })
    with pytest.raises(RegistrarError, match="dependency"):
        save(row, activate=True)
    assert not instance_dir().exists() and not active_dir().exists()


@pytest.mark.parametrize("kind", ["evaluator", "supervised-lane"])
def test_valid_absolute_rule_policy_remains_supported(tmp_path, kind):
    policy = tmp_path / "rules.json"
    policy.write_text('{"rules":[]}', encoding="utf-8")
    document = (
        {"kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "policy", "evaluator": str(policy),
        }} if kind == "evaluator" else {**pool(), "evaluator": str(policy)}
    )
    _, row = provider(document)
    assert save(row)["persisted"]


def test_policy_inspection_checks_selected_script_without_running_it(tmp_path):
    marker = tmp_path / "executed"
    script = tmp_path / "policy.py"
    script.write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "scripts": {
            "policy": [sys.executable, str(script)], "unused": ["not-installed-unused-fixture"],
        },
        "cwd": str(tmp_path),
    }), encoding="utf-8")
    _, row = provider({
        "kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "policy", "evaluator": str(policy),
        },
    })
    assert save(row)["persisted"]
    assert not marker.exists() and not active_dir().exists()
