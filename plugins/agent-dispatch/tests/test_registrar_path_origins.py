"""Supported declaration kinds retain each path field's defining hop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_dispatch import registrar_recipes, repository_resources
from agent_dispatch.producers import emitter
from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_discovery import read_declaration_file_set
from agent_dispatch.repository_issue_loops import validate_config
from tests.test_registrar_recipes import _activation
from tests.test_repository_issue_loops import FakeClient, _config


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _identity(root, rules, *, name="worker"):
    path = root / ".copilot-extensions" / "agent-dispatch" / "identities" / f"{name}.identity.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Fixture identity\n---\n{rules}\n",
        encoding="utf-8",
    )


def _reviewer(cwd):
    return {
        "kind": "reviewer-loop", "name": "reviews", "repo": "example/project",
        "task_label": "review",
        "emitter": {"command": ["reviews", "discover"], "cwd": cwd, "interval_seconds": 60},
        "evaluator": {"evaluator_spec": {"rules": []}},
        "pool": {"body": {"agent": "reviewer"}},
    }


@pytest.mark.parametrize("nearest", ["base", "middle", "leaf"])
@pytest.mark.parametrize("absolute", [False, True])
def test_reviewer_expansion_preserves_nearest_cwd_after_deferred_fill(tmp_path, nearest, absolute):
    base = tmp_path / "base" / "recipes" / "base.json"
    middle = tmp_path / "middle" / "recipes" / "middle.json"
    leaf = tmp_path / "leaf" / "registrar" / "leaf.json"
    _write(base, _reviewer("{workdir}"))
    middle_data = {"extends": str(base)}
    leaf_data = {"extends": str(middle), "params": {"workdir": "work"}}
    if nearest == "middle":
        middle_data["emitter"] = {"cwd": "{workdir}"}
    if nearest == "leaf":
        leaf_data["emitter"] = {"cwd": "work"}
    if absolute:
        work = str(tmp_path / "absolute-work")
        leaf_data["params"]["workdir"] = work
        if nearest == "leaf":
            leaf_data["emitter"]["cwd"] = work
    _write(middle, middle_data)
    _write(leaf, leaf_data)

    source, _, _ = read_declaration_file_set(leaf, repo_root=leaf.parent.parent)
    origin = {"base": base.parent, "middle": middle.parent, "leaf": leaf.parent}[nearest]
    expected = tmp_path / "absolute-work" if absolute else origin / "work"
    assert source.spec["cwd"] == str(expected.resolve())
    emitter.validate_spec(source.spec)


@pytest.mark.parametrize("nearest", ["base", "middle", "leaf"])
def test_worker_identity_context_survives_expansion_and_resource_admission(
    tmp_path, monkeypatch, nearest,
):
    roots = {name: tmp_path / name for name in ("base", "middle", "leaf")}
    for name, root in roots.items():
        _identity(root, f"RULES-{name}")
    base = roots["base"] / ".copilot-extensions" / "agent-dispatch" / "registrar" / "base.json"
    middle = roots["middle"] / ".copilot-extensions" / "agent-dispatch" / "registrar" / "middle.json"
    leaf = roots["leaf"] / ".copilot-extensions" / "agent-dispatch" / "registrar" / "leaf.json"
    _write(base, _config(worker_identity="{identity}"))
    mid_data = {"extends": str(base)}
    leaf_data = {"extends": str(middle), "params": {"identity": "worker"}}
    if nearest == "middle":
        mid_data["worker_identity"] = "{identity}"
    if nearest == "leaf":
        leaf_data["worker_identity"] = "worker"
    _write(middle, mid_data)
    _write(leaf, leaf_data)
    with monkeypatch.context() as shape:
        shape.setattr(
            "agent_dispatch.repository_issue_loops.load_worker_identity",
            lambda *a, **k: pytest.fail("shape validation read a worker identity"),
        )
        source, _ = read_declaration_file_set(leaf, repo_root=roots["leaf"])
        emitter.validate_spec(source.spec)
    assert source.spec["cwd"] == str(roots["leaf"])
    if nearest == "leaf":
        assert "worker_identity_cwd" not in source.spec
    else:
        assert source.spec["worker_identity_cwd"] == str(roots[nearest])
    config = validate_config(
        source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
        worker_identity_cwd=source.spec.get("worker_identity_cwd"),
    )
    assert config["worker_guidance"] == f"RULES-{nearest}"
    emitter.validate_spec(source.spec, resolve_resources=True)


def test_direct_declarations_keep_existing_context_and_mapping_shape(tmp_path):
    _identity(tmp_path, "DIRECT")
    direct = _config(worker_identity="worker")
    path = _write(tmp_path / "loop.json", direct)
    source, _ = read_declaration_file_set(path, repo_root=tmp_path)
    assert source.spec["repository_issue_loop"] == direct
    assert "worker_identity_cwd" not in source.spec
    assert validate_config(direct, cwd=tmp_path)["worker_guidance"] == "DIRECT"
    reviewer = _write(tmp_path / "reviews.json", _reviewer("work"))
    source, _, _ = read_declaration_file_set(reviewer, repo_root=tmp_path)
    assert source.spec["cwd"] == str(tmp_path / "work")


@pytest.mark.parametrize("command", ["{script}", ["{script}", "--mode", "fixture"]])
def test_origin_map_keeps_script_forms_raw_and_resets_only_explicit_fields(tmp_path, command):
    base = _write(tmp_path / "base" / "base.json", {
        "worker_identity": "{identity}", "forge": {"command": command, "cwd": "{workdir}"},
    })
    middle = _write(tmp_path / "middle" / "middle.json", {
        "extends": str(base), "forge": {"cwd": "{workdir}"},
    })
    origins = {}
    result = registrar_recipes.resolve_extends({
        "extends": str(middle),
        "params": {"script": "scripts/backlog.py", "workdir": "work", "identity": "worker"},
    }, base_dir=tmp_path, path_origins=origins)
    expected_command = (
        "scripts/backlog.py" if isinstance(command, str)
        else ["scripts/backlog.py", "--mode", "fixture"]
    )
    assert result["forge"] == {"command": expected_command, "cwd": "work"}
    assert origins == {
        "worker_identity": base.parent, "forge.command": base.parent, "forge.cwd": middle.parent,
    }


@pytest.mark.parametrize("kind", ["reviewer", "identity"])
def test_named_resource_origins_stay_named_until_use(tmp_path, monkeypatch, kind):
    supplier = tmp_path / "supplier"
    leaf_root = tmp_path / "leaf"
    _identity(supplier, "SUPPLIER")
    _identity(leaf_root, "WRONG-LEAF")
    _write(supplier / "recipes" / "base.json", (
        _reviewer("{workdir}") if kind == "reviewer" else _config(worker_identity="{identity}")
    ))
    _write(supplier / "recipes" / "middle.json", {"extends": "./base.json"})
    calls = []

    def registered(name, *, timeout):
        assert name == "supplier"
        calls.append(name)
        return supplier, "reference"

    monkeypatch.setattr(repository_resources, "_registered_repository", registered)
    leaf = _write(leaf_root / "loop.json", {
        "extends": "repo:supplier:recipes/middle.json",
        "params": {"workdir": "work", "identity": "worker"},
    })
    source, *_ = read_declaration_file_set(leaf, repo_root=leaf_root)
    if kind == "reviewer":
        assert source.spec["cwd"] == "repo:supplier:recipes/work"
        emitter.validate_spec(source.spec)
        with pytest.raises(RegistrarError, match="reference-only"):
            repository_resources.resolve_execution_resources(
                source.spec["command"], source.spec["cwd"],
            )
    else:
        assert source.spec["worker_identity_cwd"] == "repo:supplier:."
        config = validate_config(
            source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
            worker_identity_cwd=source.spec["worker_identity_cwd"],
        )
        assert config["worker_guidance"] == "SUPPLIER"
        assert source.spec["cwd"] == str(leaf_root)
    assert calls


@pytest.mark.parametrize("source_root", ["relative", "", 1, "repo:supplier:../escape"])
def test_identity_context_refuses_ambient_or_invalid_roots_before_use(tmp_path, source_root):
    with pytest.raises(RegistrarError, match="source root|registered root"):
        validate_config(
            _config(worker_identity="worker"), cwd=tmp_path,
            worker_identity_cwd=source_root, resolve_resources=False,
        )


def test_supplier_qualification_retains_identity_context_resource_authority(tmp_path):
    from agent_dispatch.registrar_document import qualify_supplier_context

    base = _write(
        tmp_path / ".copilot-extensions" / "agent-dispatch" / "registrar" / "base.json",
        _config(worker_identity="worker"),
    )
    leaf = _write(tmp_path / "leaf.json", {"extends": str(base)})
    source, _ = read_declaration_file_set(leaf, repo_root=tmp_path)
    qualified = qualify_supplier_context(source, "supplier", tmp_path)
    assert qualified.spec["worker_identity_cwd"] == "repo:supplier:."
    assert qualified.spec["cwd"] == "repo:supplier:."


@pytest.mark.parametrize("kind", ["identity", "reviewer"])
def test_plugin_chain_retains_field_context(tmp_path, monkeypatch, kind):
    payload = tmp_path / "payload"
    _write(payload / "plugin.json", {"name": "fixture"})
    _identity(payload, "PLUGIN")
    _write(payload / "recipes" / "base.json", (
        _config(worker_identity="worker") if kind == "identity" else _reviewer("{workdir}")
    ))
    _write(payload / "recipes" / "middle.json", {"extends": "./base.json"})
    monkeypatch.setattr(
        registrar_recipes, "resolve_active_plugins",
        lambda **k: _activation({"fixture@local": payload}),
    )
    registrar_recipes._reset_active_plugins_cache()
    try:
        leaf_root = tmp_path / "leaf"
        leaf = _write(leaf_root / "loop.json", {
            "extends": "plugin:fixture@local:recipes/middle.json",
            "params": {"workdir": "work"},
        })
        source, *_ = read_declaration_file_set(leaf, repo_root=leaf_root)
        if kind == "identity":
            assert source.spec["worker_identity_cwd"] == str(payload)
            assert validate_config(
                source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
                worker_identity_cwd=source.spec["worker_identity_cwd"],
            )["worker_guidance"] == "PLUGIN"
        else:
            assert source.spec["cwd"] == str(payload / "recipes" / "work")
    finally:
        registrar_recipes._reset_active_plugins_cache()


@pytest.mark.parametrize("chained", [False, True])
def test_global_identity_default_keeps_adopting_repository_override(tmp_path, chained):
    supplier = tmp_path / "supplier"
    leaf_root = tmp_path / "leaf"
    _identity(supplier, "SUPPLIER-DEFAULT", name="goal-driven")
    _identity(leaf_root, "LEAF-DEFAULT", name="goal-driven")
    data = {"extends": "global:goal-driven", **_config()}
    if chained:
        base = _write(
            supplier / ".copilot-extensions" / "agent-dispatch" / "registrar" / "base.json",
            data,
        )
        data = {"extends": str(base)}
    leaf = _write(leaf_root / "loop.json", data)
    source, _ = read_declaration_file_set(leaf, repo_root=leaf_root)
    config = validate_config(
        source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
        worker_identity_cwd=source.spec.get("worker_identity_cwd"),
    )
    assert config["worker_guidance"] == ("SUPPLIER-DEFAULT" if chained else "LEAF-DEFAULT")


def test_global_identity_accepts_relative_repository_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _identity(tmp_path, "RELATIVE-ROOT", name="goal-driven")
    leaf = _write(tmp_path / "loop.json", {"extends": "global:goal-driven", **_config()})
    source, _ = read_declaration_file_set(leaf, repo_root=".")
    assert source.spec["worker_identity_cwd"] == str(tmp_path)
    emitter.validate_spec(source.spec, resolve_resources=True)


def test_global_identity_needs_no_attributed_payload_in_a_wheel(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(
        registrar_recipes, "__file__",
        str(tmp_path / "wheel" / "site-packages" / "agent_dispatch" / "registrar_recipes.py"),
    )
    _identity(tmp_path, "WHEEL-ROOT", name="goal-driven")
    leaf = _write(tmp_path / "loop.json", {"extends": "global:goal-driven", **_config()})
    source, _ = read_declaration_file_set(leaf, repo_root=tmp_path)
    emitter.validate_spec(source.spec, resolve_resources=True)
    assert source.spec["worker_identity_cwd"] == str(tmp_path)


@pytest.mark.parametrize("execution", [False, True])
def test_shipped_goal_driven_alias_keeps_direct_adoption_identity_context(
    tmp_path, monkeypatch, execution,
):
    leaf_root = tmp_path / "leaf"
    execution_root = tmp_path / "execution"
    _identity(leaf_root, "LEAF-OVERRIDE", name="goal-driven")
    _identity(execution_root, "EXECUTION-OVERRIDE", name="goal-driven")
    monkeypatch.setattr(
        repository_resources, "_registered_repository",
        lambda name, **kwargs: (execution_root, "worktree"),
    )
    shipped_alias = (
        Path(__file__).resolve().parents[1]
        / ".agent-dispatch" / "registrar" / "templates" / "goal-driven.json"
    )
    assert json.loads(shipped_alias.read_text(encoding="utf-8")) == {
        "extends": "global:goal-driven",
    }
    middle_alias = _write(
        tmp_path / "middle" / "alias.json",
        {"extends": str(shipped_alias), "params": {"unused": "not-an-adopter"}},
    )
    contexts = []
    for index, ref in enumerate(("global:goal-driven", str(shipped_alias), str(middle_alias))):
        data = {"extends": ref, **_config(name=f"fixture-{index}")}
        if execution:
            data["execution_repo"] = "execution"
        leaf = _write(leaf_root / f"loop-{index}.json", data)
        source, _ = read_declaration_file_set(leaf, repo_root=leaf_root)
        contexts.append(source.spec["worker_identity_cwd"])
        config = validate_config(
            source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
            worker_identity_cwd=source.spec["worker_identity_cwd"],
        )
        assert config["worker_guidance"] == (
            "EXECUTION-OVERRIDE" if execution else "LEAF-OVERRIDE"
        )
    assert contexts == [contexts[0]] * 3


@pytest.mark.parametrize(("authored", "deferred", "leaf_override"), [
    (False, False, False), (False, True, False), (False, True, True),
    (True, False, True), (True, True, False),
])
def test_inherited_identity_retains_first_adopters_effective_execution_context(
    tmp_path, monkeypatch, authored, deferred, leaf_override,
):
    roots = {name: tmp_path / name for name in ("supplier", "leaf", "tools", "other")}
    name = "worker" if authored else "goal-driven"
    for root_name, root in roots.items():
        _identity(root, f"RULES-{root_name}", name=name)

    def registered(repository, *, timeout):
        assert repository in {"tools", "other"}
        return roots[repository], "worktree"

    monkeypatch.setattr(repository_resources, "_registered_repository", registered)
    base_data = _config(execution_repo="{target}" if deferred else "tools")
    if authored:
        base_data["worker_identity"] = name
    else:
        base_data["extends"] = "global:goal-driven"
    base = _write(
        roots["supplier"] / ".copilot-extensions" / "agent-dispatch" / "registrar" / "base.json",
        base_data,
    )
    leaf_data = {"extends": str(base), "params": {"target": "tools"}}
    if leaf_override:
        leaf_data["execution_repo"] = "other"
    leaf = _write(roots["leaf"] / "loop.json", leaf_data)
    source, _ = read_declaration_file_set(leaf, repo_root=roots["leaf"])
    assert source.spec["worker_identity_cwd"] == "repo:tools:."
    assert source.spec["cwd"] == f"repo:{'other' if leaf_override else 'tools'}:."
    config = validate_config(
        source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
        worker_identity_cwd=source.spec["worker_identity_cwd"],
    )
    assert config["worker_guidance"] == "RULES-tools"
    emitter.validate_spec(source.spec, resolve_resources=True)


def test_direct_global_identity_default_honors_execution_repository(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    leaf_root = tmp_path / "leaf"
    _identity(tools, "TOOLS-DEFAULT", name="goal-driven")
    _identity(leaf_root, "WRONG-DECLARING-DEFAULT", name="goal-driven")
    monkeypatch.setattr(
        repository_resources, "_registered_repository",
        lambda name, **kwargs: (tools, "worktree"),
    )
    leaf = _write(leaf_root / "loop.json", {
        "extends": "global:goal-driven", **_config(execution_repo="tools"),
    })
    source, _ = read_declaration_file_set(leaf, repo_root=leaf_root)
    assert source.spec["worker_identity_cwd"] == "repo:tools:."
    config = validate_config(
        source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
        worker_identity_cwd=source.spec["worker_identity_cwd"],
    )
    assert config["worker_guidance"] == "TOOLS-DEFAULT"


@pytest.mark.parametrize("authored", [False, True])
@pytest.mark.parametrize("leaf_override", [False, True])
def test_identity_origin_substitutes_at_the_same_hop_as_its_execution_field(
    tmp_path, monkeypatch, authored, leaf_override,
):
    roots = {name: tmp_path / name for name in ("supplier", "leaf", "early", "final", "other")}
    selector = "worker" if authored else "goal-driven"
    for name, root in roots.items():
        _identity(root, f"RULES-{name}", name=selector)
    monkeypatch.setattr(
        repository_resources, "_registered_repository",
        lambda name, **kwargs: (roots[name], "worktree"),
    )
    base_data = {
        **_config(execution_repo="{target}"),
        "params": {"target": "early"},
    }
    if authored:
        base_data["extends"] = "global:repository-issue-loop"
        base_data["worker_identity"] = selector
    else:
        base_data["extends"] = "global:goal-driven"
    base = _write(
        roots["supplier"] / ".copilot-extensions" / "agent-dispatch" / "registrar" / "base.json",
        base_data,
    )
    leaf_data = {"extends": str(base), "params": {"target": "final"}}
    if leaf_override:
        leaf_data["execution_repo"] = "other"
    leaf = _write(roots["leaf"] / "loop.json", leaf_data)
    source, _ = read_declaration_file_set(leaf, repo_root=roots["leaf"])
    assert source.spec["worker_identity_cwd"] == "repo:final:."
    assert source.spec["cwd"] == f"repo:{'other' if leaf_override else 'final'}:."
    config = validate_config(
        source.spec["repository_issue_loop"], cwd=source.spec["cwd"],
        worker_identity_cwd=source.spec["worker_identity_cwd"],
    )
    assert config["worker_guidance"] == "RULES-final"


def test_cli_discovery_reads_supplier_only_identity_without_live_effects(
    tmp_path, monkeypatch, capsys,
):
    from agent_dispatch import __main__ as cli, repository_issue_loops as loops
    from tests.test_repository_issue_loop_cli import FakeClient as DiscoveryClient

    supplier = tmp_path / "supplier"
    _identity(supplier, "SUPPLIER-ONLY")
    base = _write(
        supplier / ".copilot-extensions" / "agent-dispatch" / "registrar" / "base.json",
        _config(worker_identity="worker"),
    )
    leaf = _write(
        tmp_path / "leaf" / ".copilot-extensions" / "agent-dispatch" / "registrar" / "loop.json",
        {"extends": str(base)},
    )
    monkeypatch.setattr(cli, "_client", lambda args: DiscoveryClient())
    monkeypatch.setattr(loops.GitHubProvider, "list_open_issues", lambda *a: [])
    assert cli.main(["repository-issue-loop", "discover", str(leaf)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["created"] == output["reserved"] == output["eligible"] == []


@pytest.mark.parametrize("field", ["command", "cwd"])
def test_deferred_cross_repo_relative_script_field_is_still_refused(tmp_path, field):
    forge = {
        "provider": "script", "producer_login": "fixture",
        "command": [str(tmp_path / "backlog.py")], "cwd": str(tmp_path),
    }
    forge[field] = ["{relative}"] if field == "command" else "{relative}"
    base = _write(tmp_path / "supplier" / "base.json", _config(forge=forge))
    middle = _write(tmp_path / "middle" / "middle.json", {"extends": str(base)})
    leaf_root = tmp_path / "leaf"
    leaf = _write(leaf_root / "loop.json", {
        "extends": str(middle), "params": {"relative": "work"},
    })
    with pytest.raises(RegistrarError, match=rf"forge\.{field}: inherited via"):
        read_declaration_file_set(leaf, repo_root=leaf_root)


def test_native_generated_emitter_uses_inherited_identity_and_original_script_provider(tmp_path):
    supplier = tmp_path / "supplier"
    leaf_root = tmp_path / "leaf"
    _identity(supplier, "SOURCE-IDENTITY-RULES")
    _identity(leaf_root, "WRONG-LEAF-RULES")
    operations = supplier / "operations.jsonl"
    script = supplier / "backlog.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "op = sys.argv[sys.argv.index('--op') + 1]\n"
        "request = json.load(sys.stdin)\n"
        f"with open({str(operations)!r}, 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps({'op': op, 'cwd': str(pathlib.Path.cwd())}) + '\\n')\n"
        "issues = [{'number': 1, 'title': 'Fixture', 'url': 'https://example.com/issues/1',\n"
        "           'labels': ['ready'], 'created_at': 10, 'updated_at': 10}]\n"
        "print(json.dumps({'issues': issues} if op == 'list_open_issues' else {}))\n",
        encoding="utf-8",
    )
    base = _write(
        supplier / ".copilot-extensions" / "agent-dispatch" / "registrar" / "base.json",
        _config(
            repo="fixture-backlog", worker_identity="worker",
            forge={"provider": "script", "producer_login": "fixture",
                   "command": [str(script)], "cwd": str(supplier)},
        ),
    )
    leaf = _write(leaf_root / "loop.json", {"extends": str(base)})
    source, _ = read_declaration_file_set(leaf, repo_root=leaf_root)
    # Exercise the serialized registration contract, not resolver-only objects.
    spec = json.loads(json.dumps(source.spec))

    class Client(FakeClient):
        def acquire_schedule_lease(self, scope, holder, **kwargs):
            return {"granted": True, "lease": {"scope": scope, "holder": holder}}

    client = Client()
    result = emitter.run_tick(client, spec, holder="fixture", clock=lambda: 10_000)
    assert result["returncode"] == 0
    assert len(client.created) == 1
    assert "SOURCE-IDENTITY-RULES" in client.created[0]["prompt"]
    assert "WRONG-LEAF-RULES" not in client.created[0]["prompt"]
    observed = [json.loads(line) for line in operations.read_text(encoding="utf-8").splitlines()]
    assert [row["op"] for row in observed] == ["list_open_issues", "reserve", "claim"]
    assert all(row["cwd"] == str(supplier) for row in observed)
