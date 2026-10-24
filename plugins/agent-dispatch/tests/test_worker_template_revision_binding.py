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


@pytest.mark.parametrize("command", [
    ["sh", "{change_ref}"], ["sh", "-c", "{change_ref}"],
    ["sh", "-lc", "{change_ref}"],
    ["pwsh", "-File", "{change_ref}"], ["powershell", "-Command", "{change_ref}"],
    ["cmd", "/c", "echo", "{change_ref}"],
])
def test_side_load_dynamic_shell_execution_is_not_an_opaque_data_operand(
    monkeypatch, tmp_path, command,
):
    import shutil

    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda value: sys.executable if value == "sh" else which(value))
    _, row = provider({
        "kind": "emitter", "spec": {
            "id": "{name}", "command": [sys.executable, "-c", "print('[]')"],
            "cwd": str(tmp_path), "interval_seconds": 60, "side_load": {"command": command},
        },
    })
    with pytest.raises(RegistrarError, match="dynamic .*execution"):
        save(row)
    assert not instance_dir().exists()


def test_unused_evaluator_options_do_not_reject_selected_script_or_erase_supplier(monkeypatch, tmp_path):
    from agent_dispatch.producers.evaluator import load_registration_evaluator

    supplier, execution = tmp_path / "supplier", tmp_path / "execution"
    supplier.mkdir()
    execution.mkdir()
    selected = supplier / "selected.py"
    selected.write_text('print(\'{"decision":"noop","reason":"selected only"}\')', encoding="utf-8")
    unused = supplier / "unused.ps1"
    (supplier / "base.json").write_text(json.dumps({
        "kind": "evaluator", "spec": {
            "repo": "example/project", "evaluator_ref": "selected",
            "evaluator_spec": {
                "cwd": "repo:execution:.",
                "scripts": {
                    "selected": [sys.executable, str(selected)],
                    "unused": ["pwsh", "-File", str(unused)],
                },
            },
        },
    }), encoding="utf-8")
    roots = {"library": (supplier, "reference"), "execution": (execution, "worktree")}
    monkeypatch.setattr(repository_resources, "_registered_repository", lambda name, **k: roots[name])
    _, row = provider({"extends": "repo:library:base.json"}, kind="evaluator")
    assert save(row, activate=True)["activation"] == "pending"
    declaration, = rd.read_declaration_file_set(active_dir() / "local.json")
    spec = dict(declaration.spec)
    assert spec["evaluator_spec"]["scripts"]["unused"] == ["pwsh", "-File", "repo:library:unused.ps1"]
    assert not unused.exists()
    assert load_registration_evaluator(
        spec, runner=subprocess.run,
    ).evaluate({})[0].reason == "selected only"
    from agent_dispatch.producers.evaluator import EvaluatorRuntimeError
    changed = {
        **spec, "evaluator_ref": "unused",
        "evaluator_spec": {**spec["evaluator_spec"], "cwd": str(execution)},
    }
    with pytest.raises(EvaluatorRuntimeError, match="reference-only"):
        load_registration_evaluator(
            changed, runner=lambda *a, **k: pytest.fail("unselected source lost admission"),
        ).evaluate({})


@pytest.mark.parametrize("command", [
    ["sh", "-c", "printf '%s' \"$1\"", "fixture", "{change_ref}"],
    ["sh", "-lc", "printf '%s' \"$1\"", "fixture", "{change_ref}"],
    ["pwsh", "-File", "script.ps1", "{change_ref}"],
])
def test_shell_execution_roles_do_not_treat_later_script_arguments_as_code(command):
    from agent_dispatch.command_operands import execution_operand_indices

    assert all("{change_ref}" not in command[index] for index in execution_operand_indices(command))


@pytest.mark.parametrize("kind", ["emitter", "evaluator"])
@pytest.mark.parametrize("interpreter", ["powershell", "posix", "posix-option"])
def test_direct_read_only_pointer_discovery_does_not_apply_save_time_grammar(
    monkeypatch, tmp_path, kind, interpreter,
):
    from dropin_registry import ScanAuthority
    from agent_dispatch import repo_config
    from agent_dispatch.registrar_pointer import Pointer

    supplier, execution = tmp_path / "supplier", tmp_path / "execution"
    execution.mkdir()
    directory = supplier / repo_config.CANONICAL_REPO_CONFIG_DIR / "registrar"
    directory.mkdir(parents=True)
    prefix = {
        "powershell": ["pwsh", "-File"], "posix": ["sh", "-u"],
        "posix-option": ["bash", "-o", "nounset"],
    }[interpreter]
    script = supplier / ("existing.ps1" if interpreter == "powershell" else "existing.sh")
    script.write_text("Write-Output '[]'", encoding="utf-8")
    output = supplier / "optional.json"
    command = [*prefix, str(script), str(output)]
    spec = (
        {"id": "fixture", "command": command, "cwd": "repo:execution:.", "interval_seconds": 60}
        if kind == "emitter" else {
            "repo": "example/project", "evaluator_ref": "selected",
            "evaluator_spec": {"cwd": "repo:execution:.", "scripts": {"selected": command}},
        }
    )
    source = directory / "existing.json"
    source.write_text(json.dumps({"kind": kind, "name": "existing", "spec": spec}), encoding="utf-8")
    before = source.read_bytes()
    roots = {"library": (supplier, "reference"), "execution": (execution, "worktree")}
    monkeypatch.setattr(repository_resources, "_registered_repository", lambda name, **k: roots[name])
    authority, declarations = rd.discover_trusted([
        Pointer(name="library", kind="repo", repository="library"),
    ])
    assert authority == ScanAuthority.COMPLETE and len(declarations) == 1
    effective = declarations[0].spec
    argv = effective["command"] if kind == "emitter" else effective["evaluator_spec"]["scripts"]["selected"]
    assert argv == [*prefix, f"repo:library:{script.name}", str(output)]
    resolved, cwd = repository_resources.resolve_execution_resources(argv, "repo:execution:.")
    assert resolved == command and cwd == str(execution)
    assert not output.exists()
    assert source.read_bytes() == before
