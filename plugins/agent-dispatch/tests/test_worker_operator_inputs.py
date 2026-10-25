"""Operator controls cannot become arbitrary declaration or guidance editors."""

import copy
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_dispatch import worker_instances as wi
from agent_dispatch.registrar import RegistrarError
from agent_dispatch.worker_operator_inputs import CONTRACT, parse_fields, verify_record
from agent_dispatch.workers_catalog import catalog
from tests.test_worker_template_materialization import isolated, pool, provider  # noqa: F401
from tests.test_worker_templates import registered_root


def controlled(*, extra=None):
    path, row = provider(pool())
    index = path.parent / "index.json"
    data = json.loads(index.read_text())
    fields = [
        {"name": "label", "type": "choice", "label": "Watched task label",
         "description": "Select which source-owned task lane to watch.",
         "options": ["fixture", "other"], "default": "fixture"},
    ]
    data["templates"][0]["operator_fields"] = fields + (extra or [])
    index.write_text(json.dumps(data))
    row = next(item for item in catalog()["entries"] if item["id"] == row["id"])
    return path, index, row


def save(row, **values):
    return wi.save_instance(row["id"], row["revision"], {
        "name": "local", "repo_root": "", **values,
    }, operator_inputs=True)


def instance_row():
    return next(row for row in catalog()["entries"] if row["id"] == "instance:local")


def test_safe_controls_keep_source_guidance_and_save_only_lifecycle():
    path, index, row = controlled()
    original = path.read_bytes(), index.read_bytes()
    assert row["operator_instantiable"] == "true"
    assert row["operator_contract"] == {"id": CONTRACT, "version": 1}
    assert [field["name"] for field in row["operator_fields"]] == ["name", "repo_root", "param_label"]
    assert row["operator_fields"][-1]["label"] == "Watched task label"
    result = save(row, param_label="fixture")
    record, = wi.read_records()
    assert result["persisted"] and result["activation"] == "not-requested"
    assert record["configuration"]["declaration"]["labels"] == ["fixture"]
    assert record["operator_inputs"]["values"] == {"label": "fixture"}
    assert record["operator_inputs"]["configuration_revision"] == result["revision"]
    assert record["source"]["revision"] == row["revision"]
    assert not wi.active_dir().exists() and not wi.rd.pointers_file().exists()
    assert (path.read_bytes(), index.read_bytes()) == original
    assert instance_row()["operator_editable"] == "true"
    assert save(row, param_label="fixture")["retry"]
    with pytest.raises(RegistrarError, match="already exists"):
        save(row, param_label="other")
    edited = wi.save_instance(result["id"], result["revision"], {
        "name": "local", "repo_root": "", "param_label": "other",
    }, operator_inputs=True)
    assert wi.read_records()[0]["source"] == record["source"]
    assert wi.read_records()[0]["configuration"]["declaration"]["labels"] == ["other"]
    assert wi.save_instance(result["id"], result["revision"], {
        "name": "local", "repo_root": "", "param_label": "other",
    }, operator_inputs=True)["retry"]
    with pytest.raises(RegistrarError, match="stale"):
        wi.save_instance(result["id"], result["revision"], {
            "name": "local", "repo_root": "", "param_label": "fixture",
        }, operator_inputs=True)
    assert instance_row()["revision"] == edited["revision"]


@pytest.mark.parametrize("values", [
    {"declaration": "{}"}, {"param_unknown": "value"}, {"param_label": "not-declared"},
    {"param_label": []}, {"param_label": "fixture\0"},
])
def test_unsafe_or_invalid_inputs_refuse_before_any_write(values):
    _, _, row = controlled()
    with pytest.raises(RegistrarError):
        save(row, **values)
    assert not wi.instance_dir().exists() and not wi.active_dir().exists()


@pytest.mark.parametrize("change", ["index", "fragment", "absent"])
def test_changed_original_source_blocks_edit_without_changing_saved_or_published_copy(change):
    path, index, row = controlled()
    result = save(row, param_label="fixture")
    before = (wi.instance_dir() / "local.json").read_bytes()
    if change == "index":
        data = json.loads(index.read_text())
        data["templates"][0]["title"] = "Cosmetic change"
        index.write_text(json.dumps(data))
    elif change == "fragment":
        path.write_text(path.read_text().replace('"headless"', '"shell"'))
    else:
        index.unlink()
    current = instance_row()
    assert current["operator_editable"] == "false"
    assert "absent or changed" in current["operator_unsupported_reason"]
    with pytest.raises(RegistrarError, match="absent or changed"):
        wi.save_instance(result["id"], result["revision"], {
            "name": "local", "repo_root": "", "param_label": "other",
        }, operator_inputs=True)
    assert (wi.instance_dir() / "local.json").read_bytes() == before
    assert not wi.active_dir().exists()


def test_advanced_edit_clears_binding_even_when_execution_is_unchanged():
    _, _, row = controlled()
    result = save(row, param_label="fixture")
    record, = wi.read_records()
    wi.save_instance(result["id"], result["revision"], {
        "name": "local", "declaration": record["configuration"]["declaration"], "repo_root": "",
    })
    record, = wi.read_records()
    assert "operator_inputs" not in record
    assert instance_row()["operator_editable"] == "false"
    with pytest.raises(RegistrarError, match="no current operator-input binding"):
        wi.save_instance(result["id"], record["revision"], {
            "name": "local", "param_label": "fixture",
        }, operator_inputs=True)


def test_binding_does_not_let_safe_edit_clobber_a_manual_configuration_change():
    _, _, row = controlled()
    save(row, param_label="fixture")
    record, = wi.read_records()
    record["configuration"]["declaration"]["concurrency"] = 3
    record["revision"] = wi.digest(record["configuration"])
    record["operator_inputs"]["configuration_revision"] = record["revision"]
    with pytest.raises(RegistrarError, match="do not describe"):
        verify_record(record)


def test_exact_retry_fingerprint_includes_unused_safe_parameters():
    _, _, row = controlled(extra=[{
        "name": "unused", "type": "text", "label": "Optional marker",
        "description": "A declared fixture parameter with no executable use.", "default": "",
    }])
    save(row, param_label="fixture", param_unused="first")
    assert save(row, param_label="fixture", param_unused="first")["retry"]
    with pytest.raises(RegistrarError, match="already exists"):
        save(row, param_label="fixture", param_unused="different")


def test_safe_activation_keeps_explicit_consent_and_published_revision_semantics():
    _, _, row = controlled()
    fields = {"name": "local", "repo_root": "", "param_label": "fixture"}
    with pytest.raises(RegistrarError, match="explicit"):
        wi.save_instance(row["id"], row["revision"], fields, operator_inputs=True, activate=True)
    assert not wi.instance_dir().exists()
    receipt = wi.save_instance(
        row["id"], row["revision"], fields, operator_inputs=True,
        activate=True, consent=wi.ACTIVATION_CONSENT,
    )
    assert receipt["persisted"] and receipt["activation"] == "pending"
    current = instance_row()
    assert current["draft_state"] == "matches-published"
    assert current["revision"] == current["published_revision"]
    wi.save_instance(current["id"], current["revision"], {
        **fields, "param_label": "other",
    }, operator_inputs=True)
    current = instance_row()
    assert current["draft_state"] == "modified"
    assert current["published_revision"] == receipt["revision"]


def test_legacy_templates_and_instances_do_not_gain_a_human_json_editor():
    _, row = provider(pool())
    assert row["instantiable"] == "true" and row["operator_instantiable"] == "false"
    with pytest.raises(RegistrarError, match="not declared"):
        save(row)
    result = wi.save_instance(row["id"], row["revision"], {
        "name": "local", "declaration": {"params": {"label": "fixture"}}, "repo_root": "",
    })
    assert instance_row()["editable"] == "true"
    assert instance_row()["operator_editable"] == "false"
    assert result["persisted"]


def test_builtin_pool_controls_are_bounded_and_existing_template_identity_is_unchanged():
    row = next(row for row in catalog()["entries"] if row["id"] == "template:worker-pool")
    assert row["revision"] == wi.digest(wi.POOL_TEMPLATE)
    result = save(row, param_label="fixture", param_repos="all", param_concurrency="2")
    assert result["persisted"] and instance_row()["operator_editable"] == "true"
    declaration = wi.read_records()[0]["configuration"]["declaration"]
    assert declaration["body"] == wi.POOL_TEMPLATE["body"] and declaration["concurrency"] == 2
    for field in row["operator_fields"]:
        assert field["name"] not in {"declaration", "body", "prompt", "command"}
    with pytest.raises(RegistrarError, match="positive whole"):
        save(row, param_concurrency="1.5")


def test_partial_safe_edit_retains_existing_values_and_repository_context(tmp_path):
    row = next(row for row in catalog()["entries"] if row["id"] == "template:worker-pool")
    result = wi.save_instance(row["id"], row["revision"], {
        "name": "local", "repo_root": str(tmp_path), "param_label": "custom",
        "param_repos": "example/*", "param_concurrency": "2",
    }, operator_inputs=True)
    wi.save_instance(result["id"], result["revision"], {
        "name": "local", "param_concurrency": "3",
    }, operator_inputs=True)
    record, = wi.read_records()
    assert record["configuration"]["repo_root"] == str(tmp_path)
    assert record["configuration"]["declaration"]["labels"] == ["custom"]
    assert record["operator_inputs"]["values"] == {
        "label": "custom", "repos": "example/*", "concurrency": "3",
    }


@pytest.mark.parametrize("identical", [False, True])
def test_locked_binding_drift_refuses_partial_edit_but_preserves_exact_concurrent_retry(monkeypatch, identical):
    _, _, row = controlled(extra=[{
        "name": "unused", "type": "text", "label": "Optional marker",
        "description": "A fixture parameter whose changes leave execution unchanged.", "default": "original",
    }])
    result = save(row, param_label="fixture")
    intended = {"name": "local", "param_label": "other"}
    acquire = wi.SingleInstance.acquire
    changed = False

    def concurrent(lock):
        nonlocal changed
        if not changed:
            changed = True
            fields = intended if identical else {"name": "local", "param_unused": "concurrent"}
            wi.save_instance(result["id"], result["revision"], fields, operator_inputs=True)
        return acquire(lock)

    monkeypatch.setattr(wi.SingleInstance, "acquire", concurrent)
    if identical:
        assert wi.save_instance(
            result["id"], result["revision"], intended, operator_inputs=True,
        )["retry"]
    else:
        with pytest.raises(RegistrarError, match="operator inputs changed"):
            wi.save_instance(result["id"], result["revision"], intended, operator_inputs=True)
    record, = wi.read_records()
    assert record["operator_inputs"]["values"] == {
        "label": "other" if identical else "fixture",
        "unused": "original" if identical else "concurrent",
    }


@pytest.mark.parametrize("kind", ["supervised-lane", "emitter"])
@pytest.mark.parametrize("unavailable", [False, "fast", "timeout"])
def test_catalog_shares_supplier_lookup_and_deadline_across_bound_instances(monkeypatch, tmp_path, kind, unavailable):
    from agent_dispatch import repository_resources

    supplier = tmp_path / "supplier"
    supplier.mkdir()
    document = pool() if kind == "supervised-lane" else {
        "kind": "emitter",
        "spec": {"id": "{name}", "command": [sys.executable, "producer.py"],
                 "cwd": ".", "interval_seconds": 60},
    }
    (supplier / "base.json").write_text(json.dumps(document))
    (supplier / "producer.py").write_text("print('[]')", encoding="utf-8")
    clock, calls, failing = [0.0], [], [False]
    monkeypatch.setattr(repository_resources, "monotonic", lambda: clock[0])

    def lookup(*args, **kwargs):
        assert args == ("repos", "find", "library", "--registered-only", "--json")
        assert 0 < kwargs["timeout"] <= 15
        calls.append("library")
        if failing[0]:
            if failing[0] == "timeout":
                clock[0] += 16
            return None
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(registered_root("library", supplier)), stderr="",
        )

    monkeypatch.setattr(repository_resources, "run_agent_worktrees_capture", lookup)
    path, row = provider({"extends": "repo:library:base.json"}, kind=kind)
    index = path.parent / "index.json"
    data = json.loads(index.read_text())
    data["templates"][0]["operator_fields"] = [{
        "name": "label", "type": "text", "label": "Watched label",
        "description": "Choose the source-owned lane.", "default": "fixture",
    }]
    data["templates"].append({**data["templates"][0], "id": "second"})
    index.write_text(json.dumps(data))
    rows = [entry for entry in catalog()["entries"] if entry["id"].startswith("template:provider:")]
    assert len(rows) == 2
    for number in range(4):
        selected = rows[number % 2]
        before = len(calls)
        wi.save_instance(selected["id"], selected["revision"], {
            "name": f"local-{number}", "param_label": "fixture",
        }, operator_inputs=True)
        assert len(calls) > before, "Save reused a completed catalog's resource snapshot"
    calls.clear()
    failing[0] = unavailable
    for expected in (1, 2):
        configured = [entry for entry in catalog()["entries"] if entry["group"] == "Configured"]
        assert len(configured) == 4
        assert all(entry["operator_editable"] == str(not unavailable).lower() for entry in configured)
        assert calls == ["library"] * expected


def test_shared_snapshot_views_preserve_admission_and_lookup_deadline(monkeypatch, tmp_path):
    from agent_dispatch import repository_resources

    clock, calls = [0.0], []
    monkeypatch.setattr(repository_resources, "monotonic", lambda: clock[0])

    def lookup(name, **kwargs):
        calls.append(name)
        return tmp_path, "reference"

    monkeypatch.setattr(repository_resources, "_registered_repository", lookup)
    snapshot = repository_resources.RepositoryResourceSnapshot(execution=False)
    assert snapshot.root("library") == tmp_path
    clock[0] = 14
    executable = snapshot.with_execution(execution=True)
    with pytest.raises(RegistrarError, match="execution is refused"):
        executable.root("library")
    assert snapshot.root("library") == tmp_path
    clock[0] = 16
    with pytest.raises(RegistrarError, match="shared 15s budget"):
        executable.root("another")
    assert calls == ["library"]
    assert repository_resources.RepositoryResourceSnapshot(execution=False).root("library") == tmp_path
    assert calls == ["library", "library"]


@pytest.mark.parametrize("builtin", ["repository_issue_loop", "effort_driver_loop"])
def test_emitter_shared_snapshot_preserves_loop_execution_admission(monkeypatch, tmp_path, builtin):
    from agent_dispatch import effort_driver_loops, repository_issue_loops, repository_resources
    from agent_dispatch.producers.emitter import validate_spec

    calls = []
    monkeypatch.setattr(
        repository_resources, "_registered_repository",
        lambda name, **kwargs: calls.append(name) or (tmp_path, "reference"),
    )
    snapshot = repository_resources.RepositoryResourceSnapshot(execution=False)
    assert snapshot.root("library") == tmp_path
    received = []

    def validate(data, *, snapshot, **kwargs):
        received.append(snapshot)
        with pytest.raises(RegistrarError, match="execution is refused"):
            snapshot.root("library")

    module = repository_issue_loops if builtin == "repository_issue_loop" else effort_driver_loops
    monkeypatch.setattr(module, "validate_config", validate)
    validate_spec({"id": "fixture", builtin: {}, "interval_seconds": 60}, resolve_resources=True, snapshot=snapshot)
    assert len(received) == 1 and calls == ["library"]
    assert snapshot.root("library") == tmp_path


@pytest.mark.parametrize("stage", ["catalog", "save", "retry", "edit", "refuse"])
def test_actual_cli_first_touch_safe_save_edit_retry_and_capability(capsys, stage):
    from agent_dispatch.__main__ import build_parser

    def seed(*args):
        parsed = build_parser().parse_args(["workers", *args])
        assert parsed.func(parsed) == 0
        return json.loads(capsys.readouterr().out)

    def invoke(*args, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "agent_dispatch", "workers", *args],
            capture_output=True, text=True, timeout=40 if os.name == "nt" else 20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "AGENT_DISPATCH_NO_AUTOSTART": "1"},
        )
        assert result.returncode == expected, result.stderr
        return json.loads(result.stdout) if expected == 0 else result.stderr

    initial = invoke("catalog") if stage == "catalog" else catalog()
    row = next(row for row in initial["entries"] if row["id"] == "template:worker-pool")
    assert row["operator_contract"] == {"id": CONTRACT, "version": 1}
    assert not wi.instance_dir().exists()
    if stage == "catalog":
        return
    fields = ["--field", "name=cli-safe", "--field", "param_label=fixture",
              "--field", "param_repos=all", "--field", "param_concurrency=2"]
    args = ("save", row["id"], "--revision", row["revision"], "--operator-inputs", *fields)
    saved = invoke(*args) if stage == "save" else seed(*args)
    assert saved["activation"] == "not-requested"
    assert not wi.active_dir().exists() and not wi.rd.pointers_file().exists()
    if stage == "retry":
        assert invoke(*args)["retry"]
    elif stage == "refuse":
        bad = invoke("save", saved["id"], "--revision", saved["revision"], "--operator-inputs",
                     "--field", "name=cli-safe", "--field", "declaration={}", expected=2)
        assert "unknown configuration fields" in bad
    elif stage == "edit":
        fields[-1] = "param_concurrency=3"
        edited = invoke("save", saved["id"], "--revision", saved["revision"], "--operator-inputs", *fields)
        assert edited["revision"] != saved["revision"] and edited["activation"] == "not-requested"
        record, = wi.read_records()
        assert record["configuration"]["declaration"]["concurrency"] == 3


@pytest.mark.parametrize("change", [
    {"type": "textarea"}, {"type": "multichoice"}, {"name": "name"}, {"name": "a.b"},
    {"label": ""}, {"label": "two\nlines"}, {"description": None}, {"options_command": ["unsafe"]},
    {"default": "missing"}, {"options": ["same", "same"]},
])
def test_field_schema_rejects_non_native_or_unsafe_contracts(change):
    field = {"name": "label", "type": "choice", "label": "Task label",
             "description": "Watch the selected task label.", "options": ["fixture"], "default": "fixture"}
    with pytest.raises(RegistrarError):
        parse_fields([{**copy.deepcopy(field), **change}])
