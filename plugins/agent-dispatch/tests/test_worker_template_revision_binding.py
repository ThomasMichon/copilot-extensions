"""Review regressions for revision-bound reads and command dependency roles."""

import json
import subprocess
import sys

import pytest

from agent_dispatch import registrar_discovery as rd, repository_resources
from agent_dispatch.registrar import RegistrarError
from agent_dispatch.worker_instances import active_dir, instance_dir
from tests.test_worker_template_materialization import (
    isolated as isolated, pool, provider, save,
)


@pytest.mark.parametrize("source", ["fragment", "ancestor", "global"])
def test_expansion_reads_cannot_temporarily_substitute_selected_recipe(monkeypatch, source):
    from agent_dispatch import registrar_recipes

    path, _ = provider(
        {"extends": "global:fixture" if source == "global" else "./base.json"},
        kind="supervised-lane",
    )
    base = path.parent / "base.json"
    base.write_text(json.dumps(pool()), encoding="utf-8")
    monkeypatch.setitem(registrar_recipes.GLOBAL_RECIPES, "fixture", pool())
    from agent_dispatch import worker_templates
    from tests.test_worker_template_materialization import activation
    row = worker_templates.discover_templates(activation())[0][0]
    expand = rd.expand_declaration_document
    selected = path if source == "fragment" else base
    original = selected.read_bytes()
    def substitute(*args, **kwargs):
        if source == "global":
            monkeypatch.setitem(registrar_recipes.GLOBAL_RECIPES, "fixture", {
                **pool(), "labels": ["substituted"],
            })
        else:
            selected.write_text(json.dumps({**pool(), "labels": ["substituted"]}), encoding="utf-8")
        try:
            return expand(*args, **kwargs)
        finally:
            selected.write_bytes(original)
            monkeypatch.setitem(registrar_recipes.GLOBAL_RECIPES, "fixture", pool())
    monkeypatch.setattr(rd, "expand_declaration_document", substitute)
    with pytest.raises(RegistrarError, match="absent or changed"):
        save(row, document={"params": {"label": "fixture"}})
    assert not instance_dir().exists()


def test_non_json_recipe_change_is_a_revision_refusal(monkeypatch, tmp_path):
    from agent_dispatch import registrar_recipes
    from agent_dispatch.registrar_recipe_io import RecipeRevisionError, read_resolved_recipe

    ref = "global:repository-issue-loop"
    monkeypatch.setitem(registrar_recipes.GLOBAL_RECIPES, "repository-issue-loop", {
        "kind": "supervised-lane", "labels": {"not", "json"},
    })
    with pytest.raises(RecipeRevisionError, match="absent or changed"):
        read_resolved_recipe(
            ref, None, ref=ref, base_dir=tmp_path,
            expected_context={ref: {"document": pool()}},
        )


@pytest.mark.parametrize("kind", ["emitter", "side-load", "evaluator"])
def test_absent_supplier_output_argument_stays_opaque_and_real_consumer_creates_it(
    monkeypatch, tmp_path, kind,
):
    from agent_dispatch.producers import emitter
    from agent_dispatch.producers.evaluator import load_registration_evaluator
    from tests.test_producers_emitter import FakeClient

    supplier = tmp_path / "supplier"
    supplier.mkdir()
    script = supplier / "producer.py"
    output = supplier / "new-output.json"
    answer = '{"decision":"noop","reason":"output created"}' if kind == "evaluator" else "[]"
    script.write_text(
        f"import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('created'); print({answer!r})",
        encoding="utf-8",
    )
    command = [sys.executable, str(script), str(output)]
    if kind == "evaluator":
        document = {"kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "policy",
            "evaluator_spec": {"scripts": {"policy": command}, "cwd": str(supplier)},
        }}
    else:
        document = {"kind": "emitter", "spec": {
            "id": "{name}", "command": command, "cwd": str(supplier),
            "interval_seconds": 60, "task_output": "json",
        }}
        if kind == "side-load":
            document["spec"]["command"] = [sys.executable, "-c", "print('[]')"]
            document["spec"]["side_load"] = {"command": [*command, "{change_ref}"]}
    (supplier / "base.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        repository_resources, "_registered_repository", lambda *a, **k: (supplier, "worktree"),
    )
    _, row = provider({"extends": "repo:library:base.json"}, kind=document["kind"])
    assert save(row, activate=True)["activation"] == "pending"
    assert not output.exists(), "Save executed provider code"
    declaration, = rd.read_declaration_file_set(active_dir() / "local.json")
    spec = dict(declaration.spec)
    argv = (
        spec["evaluator_spec"]["scripts"]["policy"] if kind == "evaluator"
        else spec["side_load"]["command"] if kind == "side-load" else spec["command"]
    )
    assert argv[1] == "repo:library:producer.py" and argv[2] == str(output)
    if kind == "evaluator":
        assert load_registration_evaluator(
            spec, runner=subprocess.run,
        ).evaluate({})[0].reason == "output created"
    else:
        monkeypatch.setattr(emitter, "receipts_path", lambda *a: tmp_path / "receipts.json")
        client = FakeClient()
        if kind == "side-load":
            emitter.run_side_load(
                client, {"kind": "emitter", "spec": spec}, "fixture", runner=subprocess.run,
            )
        else:
            assert emitter.run_tick(
                client, spec, holder="fixture", runner=subprocess.run,
            )["returncode"] == 0
        assert not client.created
    assert output.read_text() == "created"


@pytest.mark.parametrize("side_command", [
    [sys.executable, "side.py", "{change_ref}"],
    ["./side-command", "{change_ref}"],
])
def test_static_side_load_dependencies_need_cwd_not_coordinator_context(
    monkeypatch, tmp_path, side_command,
):
    coordinator = tmp_path / "coordinator"
    coordinator.mkdir()
    (coordinator / "side.py").write_text("raise AssertionError('never run during Save')", encoding="utf-8")
    (coordinator / "side-command").write_text("fixture executable", encoding="utf-8")
    monkeypatch.chdir(coordinator)
    _, row = provider({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": [sys.executable, "-c", "print('[]')"],
            "interval_seconds": 60, "side_load": {"command": side_command},
        },
    })
    with pytest.raises(RegistrarError, match="explicit working directory|missing .*dependency"):
        save(row)
    assert not instance_dir().exists()


@pytest.mark.parametrize("command", [
    ["{change_ref}", "data"],
    [sys.executable, "{change_ref}.py"],
    [sys.executable, "-m", "{change_ref}"],
    [sys.executable, "-c", "print('{change_ref}')"],
    ["{python}", "-c", "print('{change_ref}')"],
])
def test_side_load_dynamic_execution_dependencies_refuse_without_invented_input(tmp_path, command):
    _, row = provider({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": [sys.executable, "-c", "print('[]')"],
            "cwd": str(tmp_path), "interval_seconds": 60, "side_load": {"command": command},
        },
    })
    with pytest.raises(RegistrarError, match="dynamic side-load execution dependencies"):
        save(row)
    assert not instance_dir().exists()


def test_side_load_missing_static_script_refuses_even_with_explicit_cwd(tmp_path):
    _, row = provider({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": [sys.executable, "-c", "print('[]')"],
            "cwd": str(tmp_path), "interval_seconds": 60,
            "side_load": {"command": [sys.executable, "missing.py", "{change_ref}"]},
        },
    })
    with pytest.raises(RegistrarError, match="missing script dependency"):
        save(row)
    assert not instance_dir().exists()


@pytest.mark.parametrize("python", [sys.executable, "{python}", "repo:execution:python"])
def test_qualification_uses_operand_index_not_equal_option_values(tmp_path, python):
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrar_document import qualify_supplier_context

    directory = tmp_path / "directory-entrypoint"
    directory.mkdir()
    (directory / "__main__.py").write_text("print('[]')", encoding="utf-8")
    output = str(tmp_path / "absent-output.json")
    declaration = load_declaration({
        "kind": "emitter", "name": "fixture", "spec": {
            "id": "fixture", "command": [python, "-W", str(directory), str(directory), output],
            "interval_seconds": 60,
        },
    })
    qualified = qualify_supplier_context(declaration, "library", tmp_path)
    assert qualified.spec["command"] == [
        python, "-W", str(directory), "repo:library:directory-entrypoint", output,
    ]
