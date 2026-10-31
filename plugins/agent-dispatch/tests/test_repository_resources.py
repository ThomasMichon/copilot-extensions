"""Named resources consume the registry owner, never a discovery fallback."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_dispatch import repository_resources as resources
from agent_dispatch.registrar import RegistrarError


def _registry(monkeypatch, root, *, name="tools", repo_class="worktree"):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "registration": "ready", "name": name, "class": repo_class,
            "path": str(root),
        }), "")

    monkeypatch.setattr(resources, "run_agent_worktrees_capture", run)
    return calls


def test_same_named_resource_uses_changed_registered_root(tmp_path, monkeypatch):
    for directory in ("first", "second"):
        root = tmp_path / directory
        root.mkdir()
        script = root / "worker.py"
        script.write_text("print('ok')", encoding="utf-8")
        calls = _registry(monkeypatch, root)
        assert resources.resolve_repository_resource("repo:tools:worker.py") == script
        assert calls == [(("repos", "find", "tools", "--registered-only", "--json"),
                          {"timeout": 15})]


@pytest.mark.parametrize("value", [
    "repo:tools:", "repo:tools:/worker.py", "repo:tools:C:\\worker.py",
    "repo:tools:C:worker.py", "repo:tools:..\\worker.py",
    "repo:tools:folder/../worker.py", "repo:../tools:worker.py",
    "repo:tools:\\\\server\\share\\worker.py", "repo:tools:bad\0path",
])
def test_invalid_resource_is_rejected_before_registry_probe(value, monkeypatch):
    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("invalid resource probed registry"),
    )
    with pytest.raises(RegistrarError):
        resources.resolve_repository_resource(value)


@pytest.mark.parametrize("response", [
    None,
    subprocess.CompletedProcess([], 1, '{"error":"not registered"}', ""),
    subprocess.CompletedProcess([], 0, "not JSON", ""),
    subprocess.CompletedProcess([], 0, '{"name":"tools","path":"/guessed"}', ""),
    subprocess.CompletedProcess([], 0, '{"registration":"ready","name":"other"}', ""),
    subprocess.CompletedProcess([], 0,
                                '{"registration":"ready","name":"tools","class":[]}', ""),
])
def test_missing_old_or_invalid_owner_contract_fails_explicitly(response, monkeypatch):
    monkeypatch.setattr(resources, "run_agent_worktrees_capture", lambda *a, **k: response)
    with pytest.raises(RegistrarError):
        resources.registered_repository_root("tools")


def test_invalid_path_in_advertised_owner_contract_fails_explicitly(tmp_path, monkeypatch):
    _registry(monkeypatch, str(tmp_path) + "\0bad")
    with pytest.raises(RegistrarError, match="unavailable"):
        resources.registered_repository_root("tools")


@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_read_only_root_can_be_read_but_not_executed(tmp_path, monkeypatch, repo_class):
    _registry(monkeypatch, tmp_path, repo_class=repo_class)
    assert resources.registered_repository_root("tools") == tmp_path
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        resources.registered_repository_root("tools", execution=True)


@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_read_only_pointer_cannot_execute_relative_executable_declarations(
    tmp_path, monkeypatch, repo_class,
):
    from agent_dispatch import registrar_discovery as discovery, repo_config
    from agent_dispatch.registrar_pointer import Pointer
    from agent_dispatch.producers.emitter import run_tick
    from tests.test_producers_emitter import FakeClient
    from tests.test_repository_issue_loops import _config

    directory = tmp_path / repo_config.CANONICAL_REPO_CONFIG_DIR / "registrar"
    directory.mkdir(parents=True)
    (tmp_path / "backlog.py").write_text("print('[]')", encoding="utf-8")
    (directory / "backlog.json").write_text(json.dumps(_config(
        forge={"provider": "script", "producer_login": "fixture",
               "command": [sys.executable, "backlog.py"], "cwd": "."},
    )), encoding="utf-8")
    _registry(monkeypatch, tmp_path, repo_class=repo_class)
    _, declarations = discovery.discover_trusted([
        Pointer(name="tools", kind="repo", repository="tools"),
    ])
    source, _ = declarations
    assert source.spec["cwd"] == "repo:tools:."
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "dispatch"))
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        run_tick(
            FakeClient(), dict(source.spec), holder="fixture",
            runner=lambda *a, **k: pytest.fail("read-only supplier reached subprocess"),
        )


def test_missing_file_is_not_invented(tmp_path, monkeypatch):
    _registry(monkeypatch, tmp_path)
    with pytest.raises(RegistrarError, match="unavailable"):
        resources.resolve_repository_resource("repo:tools:missing.py")


def test_named_file_cannot_be_used_as_execution_cwd_or_state_root(tmp_path, monkeypatch):
    from agent_dispatch.effort_driver_loops import _resolve_state_root

    (tmp_path / "file.py").write_text("print('ok')", encoding="utf-8")
    _registry(monkeypatch, tmp_path)
    with pytest.raises(RegistrarError, match="must be a directory"):
        resources.resolve_execution_resources(["python"], "repo:tools:file.py")
    with pytest.raises(RegistrarError, match="must be a directory"):
        _resolve_state_root("repo:tools:file.py", cwd=None)


def test_symlink_resource_cannot_escape_registered_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')", encoding="utf-8")
    try:
        (root / "escape.py").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"native symlink creation is unavailable: {exc}")
    _registry(monkeypatch, root)
    with pytest.raises(RegistrarError, match="escapes"):
        resources.resolve_repository_resource("repo:tools:escape.py")


def test_one_invocation_uses_one_root_snapshot(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        (root / "worker.py").write_text("print('ok')", encoding="utf-8")
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        root = first if len(calls) == 1 else second
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "registration": "ready", "name": "tools", "class": "worktree",
            "path": str(root),
        }), "")

    monkeypatch.setattr(resources, "run_agent_worktrees_capture", run)
    argv, cwd = resources.resolve_execution_resources(
        ["python", "repo:tools:worker.py"], "repo:tools:.",
    )
    assert argv == ["python", str(first / "worker.py")]
    assert cwd == str(first)
    assert len(calls) == 1


def test_relative_executable_binds_to_the_explicit_directory(tmp_path, monkeypatch):
    directory = tmp_path / "policy cwd"
    directory.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("plain execution context probed registry"),
    )
    argv, cwd = resources.resolve_execution_resources(
        [f".{os.sep}worker", "--mode"], str(directory), bind_relative_executable=True,
    )
    assert argv == [str(directory / "worker"), "--mode"] and cwd == str(directory)
    assert resources.resolve_execution_resources(["python", "worker.py"], str(directory))[0] == [
        "python", "worker.py",
    ]
    assert resources.resolve_execution_resources([f".{os.sep}worker"], None) == (
        [f".{os.sep}worker"], None,
    )


def test_relative_executable_keeps_parent_components_for_native_resolution(tmp_path):
    argv, cwd = resources.resolve_execution_resources(
        [os.path.join("nested", "..", "worker")], str(tmp_path), bind_relative_executable=True,
    )
    assert argv == [str(tmp_path / "nested" / ".." / "worker")] and cwd == str(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/parent traversal semantics")
def test_relative_executable_preserves_symlink_parent_traversal(tmp_path):
    directory = tmp_path / "policy"
    other = tmp_path / "other"
    directory.mkdir()
    (other / "nested").mkdir(parents=True)
    expected = other / "worker"
    expected.write_text("#!/bin/sh\nprintf intended", encoding="utf-8")
    (directory / "worker").write_text("#!/bin/sh\nprintf wrong", encoding="utf-8")
    try:
        (directory / "link").symlink_to(other / "nested", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    argv, cwd = resources.resolve_execution_resources(
        [os.path.join("link", "..", "worker")], str(directory), bind_relative_executable=True,
    )
    assert os.path.samefile(argv[0], expected)
    if os.name != "nt":
        expected.chmod(0o700)
        assert subprocess.check_output(argv, cwd=cwd, text=True) == "intended"


def test_relative_executable_keeps_named_cwd_admission(tmp_path, monkeypatch):
    calls = _registry(monkeypatch, tmp_path)
    argv, cwd = resources.resolve_execution_resources(
        [f".{os.sep}worker"], "repo:tools:.", bind_relative_executable=True,
    )
    assert argv == [str(tmp_path / "worker")] and cwd == str(tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_relative_executable_cannot_normalize_away_read_only_admission(tmp_path, monkeypatch, repo_class):
    _registry(monkeypatch, tmp_path, repo_class=repo_class)
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        resources.resolve_execution_resources([f".{os.sep}worker"], "repo:tools:.")


def test_distinct_repository_lookups_share_one_bounded_budget(tmp_path, monkeypatch):
    (tmp_path / "worker.py").write_text("print('ok')", encoding="utf-8")
    calls = _registry(monkeypatch, tmp_path)
    times = iter([0, 0, 16])
    monkeypatch.setattr(resources, "monotonic", lambda: next(times))
    with pytest.raises(RegistrarError, match="shared 15s budget"):
        resources.resolve_execution_resources(
            ["repo:tools:worker.py", "repo:other:worker.py"], None,
        )
    assert len(calls) == 1
    assert calls[0][1] == {"timeout": 15}


def test_ordinary_argv_does_not_require_registry(monkeypatch):
    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("standalone argv probed registry"),
    )
    assert resources.resolve_resource_argument("python") == "python"


def test_named_pointer_persists_identity_only_and_follows_registered_move(tmp_path, monkeypatch):
    from agent_dispatch import registrar_discovery as discovery

    root = tmp_path / "first"
    root.mkdir()
    _registry(monkeypatch, root)
    monkeypatch.setattr(discovery, "_derive_git_remote_alias", lambda root: "example.test/team/tools")
    base = tmp_path / "registry"
    pointer = discovery.add_pointer(
        "tools", kind="repo", repository="tools", owner="repo:tools", base=base,
    )
    stored = json.loads(discovery.pointers_file(base).read_text(encoding="utf-8"))
    assert stored == [{
        "name": "tools", "kind": "repo", "owner": "repo:tools",
        "aliases": ["example.test/team/tools"], "repository": "tools",
    }]
    assert discovery.Pointer.from_dict(stored[0]) == pointer
    relocated = tmp_path / "second"
    relocated.mkdir()
    _registry(monkeypatch, relocated)
    assert pointer.repo_root() == relocated
    assert discovery.add_pointer(
        "tools", kind="repo", repository="tools", base=base,
    ) == pointer
    assert discovery.pointers_file(base).read_text(encoding="utf-8") == json.dumps(stored, indent=2) + "\n"


@pytest.mark.parametrize("data", [
    {"name": "tools", "kind": "dir", "repository": "tools"},
    {"name": "tools", "kind": "repo", "repository": "tools", "location": "/other"},
    {"name": "tools", "kind": "repo", "repository": "../tools"},
])
def test_named_pointer_refuses_mixed_or_invalid_authority(data):
    from agent_dispatch.registrar_discovery import Pointer

    with pytest.raises(RegistrarError):
        Pointer.from_dict(data)


def test_evaluator_executes_unchanged_named_spec_in_two_registered_roots(tmp_path, monkeypatch):
    from agent_dispatch.producers.evaluator import NoOp, load_evaluator

    spec = {
        "scripts": {"verify": [sys.executable, "repo:tools:verify.py"]},
        "cwd": "repo:tools:.", "timeout_seconds": 10,
    }
    original = json.dumps(spec, sort_keys=True)
    evaluator = load_evaluator(spec, evaluator_ref="verify")
    for directory in ("first", "second"):
        root = tmp_path / directory
        root.mkdir()
        (root / "verify.py").write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "event=json.load(sys.stdin)\n"
            "assert event['type']=='task.submitted'\n"
            "print(json.dumps({'decision':'noop','reason':Path.cwd().name}))\n",
            encoding="utf-8",
        )
        _registry(monkeypatch, root)
        assert evaluator.evaluate({"type": "task.submitted"}) == [NoOp(reason=directory)]
    assert json.dumps(spec, sort_keys=True) == original


def test_emitter_retains_named_spec_and_executes_with_registered_cwd(tmp_path, monkeypatch):
    from agent_dispatch.producers.emitter import run_tick
    from tests.test_producers_emitter import FakeClient

    script = tmp_path / "emit.py"
    script.write_text(
        "import json\nfrom pathlib import Path\n"
        "assert Path.cwd().joinpath('emit.py').is_file()\n"
        "print(json.dumps({'title':'work','dedup_key':'named-smoke'}))\n",
        encoding="utf-8",
    )
    _registry(monkeypatch, tmp_path)
    spec = {
        "id": "named-emitter", "interval_seconds": 60, "timeout_seconds": 10,
        "command": ["{python}", "repo:tools:emit.py"],
        "cwd": "repo:tools:.", "task_output": "json",
    }
    original = json.dumps(spec, sort_keys=True)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "dispatch"))
    result = run_tick(FakeClient(), spec, holder="test")
    assert result["returncode"] == 0
    assert len(result["created"]) == 1
    assert json.dumps(spec, sort_keys=True) == original


def test_loop_expansion_separates_registered_execution_repo_from_lane(tmp_path, monkeypatch):
    from agent_dispatch.repository_issue_loops import expand_repository_issue_loop
    from agent_dispatch.effort_driver_loops import expand_effort_driver_loop
    from tests.test_repository_issue_loops import _config as issue_config
    from tests.test_effort_driver_loops import _config as effort_config

    _registry(monkeypatch, tmp_path)
    for data, expand in (
        (issue_config(execution_repo="tools"), expand_repository_issue_loop),
        (effort_config(tmp_path, execution_repo="tools", state_root="repo:tools:.",
                       effort_slugs=["unfinished"]),
         expand_effort_driver_loop),
    ):
        declarations = expand(data, repo_root=tmp_path / "unrelated")
        source, workers = declarations
        assert source.spec["cwd"] == "repo:tools:."
        assert workers.repos == "example/project"
        assert str(tmp_path) not in json.dumps(dict(source.spec))


@pytest.mark.parametrize("kind", ["effort", "issue", "script"])
@pytest.mark.parametrize("caller", ["expansion", "document", "discovery"])
def test_loop_expansion_defers_host_resources_and_worker_identity(
    tmp_path, monkeypatch, kind, caller,
):
    from agent_dispatch import registrar_discovery as discovery
    from agent_dispatch import repo_config
    from agent_dispatch.effort_driver_loops import expand_effort_driver_loop
    from agent_dispatch.registrar_document import expand_declaration_document
    from agent_dispatch.registrar_pointer import Pointer
    from agent_dispatch.repository_issue_loops import expand_repository_issue_loop
    from tests.test_effort_driver_loops import _config as effort_config
    from tests.test_repository_issue_loops import _config as issue_config

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("expansion resolved a host resource before lease"),
    )
    if kind == "effort":
        data = effort_config(
            tmp_path, execution_repo="tools", state_root="repo:state:.",
            effort_slugs=["unfinished"], worker_identity="missing-until-held",
        )
        expand = expand_effort_driver_loop
        key = "effort_driver_loop"
    else:
        data = issue_config(execution_repo="tools", worker_identity="missing-until-held")
        if kind == "script":
            data["forge"] = {
                "provider": "script", "producer_login": "fixture",
                "command": ["repo:tools:backlog.py"], "cwd": "repo:tools:.",
            }
        expand = expand_repository_issue_loop
        key = "repository_issue_loop"
    if caller == "expansion":
        declarations = expand(data, repo_root=tmp_path)
    elif caller == "document":
        declarations = expand_declaration_document(
            data, directory=tmp_path, repo_root=tmp_path,
        )
    else:
        directory = tmp_path / repo_config.CANONICAL_REPO_CONFIG_DIR / "registrar"
        directory.mkdir(parents=True)
        (directory / "loop.json").write_text(json.dumps(data), encoding="utf-8")
        authority, declarations = discovery.discover_trusted([
            Pointer(name="fixture", kind="repo", location=str(tmp_path)),
        ])
        assert authority is discovery.ScanAuthority.COMPLETE
    source, workers = declarations
    assert source.spec["cwd"] == "repo:tools:."
    assert source.spec[key] == data
    assert workers.repos == "example/project"


def test_script_forge_named_fields_survive_cross_repo_inheritance(tmp_path, monkeypatch):
    from agent_dispatch.script_provider import validate_script_forge_config

    script = tmp_path / "backlog.py"
    script.write_text("print('ok')", encoding="utf-8")
    _registry(monkeypatch, tmp_path)
    result = validate_script_forge_config(
        {"command": ["repo:tools:backlog.py"], "cwd": "repo:tools:."},
        provider="script", repo_root=tmp_path,
        inherited_script_fields=frozenset({"command", "cwd"}),
    )
    assert result["command"] == [sys.executable, str(script)]
    assert result["cwd"] == str(tmp_path)
    assert result["_declared_command"] == ["repo:tools:backlog.py"]
    assert result["_declared_cwd"] == "repo:tools:."


@pytest.mark.parametrize("known_root", [False, True])
@pytest.mark.parametrize("absolute_cwd", [False, True])
def test_script_forge_relative_command_keeps_declaring_root_contract(
    tmp_path, monkeypatch, known_root, absolute_cwd,
):
    from agent_dispatch.script_provider import validate_script_forge_config

    root, daemon = tmp_path / "declaring", tmp_path / "daemon"
    for directory in (root, daemon):
        (directory / "tools").mkdir(parents=True)
        (directory / "tools" / "poller.py").write_text("print('[]')", encoding="utf-8")
    monkeypatch.chdir(daemon)
    forge = {
        "command": [f".{os.sep}tools{os.sep}poller.py"],
        "cwd": str(root) if absolute_cwd else ".",
    }
    if known_root:
        result = validate_script_forge_config(forge, provider="script", repo_root=root)
        assert result["command"] == [sys.executable, str(root / "tools" / "poller.py")]
        assert result["cwd"] == str(root)
    else:
        with pytest.raises(RegistrarError, match="relative script path.*known declaring repo root"):
            validate_script_forge_config(forge, provider="script", repo_root=None)


def test_named_recipe_ref_keeps_nested_resource_identity(tmp_path, monkeypatch):
    from agent_dispatch.registrar_recipes import resolve_extends

    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({
        "name": "template", "kind": "emitter",
        "spec": {"command": ["python", "repo:tools:tick.py"],
                 "cwd": "repo:tools:.", "interval_seconds": 60},
    }), encoding="utf-8")
    _registry(monkeypatch, tmp_path)
    result = resolve_extends(
        {"extends": "repo:tools:recipe.json", "name": "custom"},
        base_dir=tmp_path / "not-the-root",
    )
    assert result["spec"]["cwd"] == "repo:tools:."
    assert result["spec"]["command"][1] == "repo:tools:tick.py"


@pytest.mark.parametrize("owner", ["existing-owner", None])
def test_explicit_same_root_pointer_migration_preserves_provenance(tmp_path, monkeypatch, owner):
    from agent_dispatch import registrar_discovery as discovery

    root = tmp_path / "checkout-name"
    root.mkdir()
    base = tmp_path / "pointers"
    original = discovery.add_pointer(
        "tools", root, kind="repo", owner=owner,
        aliases=["example.test/team/tools", "team/tools"], base=base,
    )
    _registry(monkeypatch, root)
    migrated = discovery.add_pointer(
        "tools", kind="repo", repository="tools", base=base,
    )
    assert migrated.owner == original.effective_owner()
    assert migrated.effective_owner() == original.effective_owner()
    assert migrated.aliases == ("example.test/team/tools", "team/tools")
    assert "location" not in migrated.to_dict()
    assert discovery.add_pointer("tools", root, kind="repo", base=base) == migrated
    assert "location" not in discovery.load_pointers(base)[0].to_dict()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    before = discovery.pointers_file(base).read_bytes()
    with pytest.raises(RegistrarError, match="repository name"):
        discovery.add_pointer("tools", unrelated, kind="repo", base=base)
    assert discovery.pointers_file(base).read_bytes() == before


def _moving_registry(monkeypatch, tmp_path):
    roots = [tmp_path / name for name in ("first", "second")]
    for root in roots:
        root.mkdir()
        (root / "backlog.py").write_text("print('ok')", encoding="utf-8")
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "registration": "ready", "name": "tools", "class": "worktree",
            "path": str(roots[min(len(calls) - 1, 1)]),
        }), "")

    monkeypatch.setattr(resources, "run_agent_worktrees_capture", run)
    return roots, calls


def test_effort_validation_shares_execution_and_state_snapshot(tmp_path, monkeypatch):
    from agent_dispatch.effort_driver_loops import validate_config
    from tests.test_effort_driver_loops import _config

    roots, calls = _moving_registry(monkeypatch, tmp_path)
    config = validate_config(_config(
        tmp_path, execution_repo="tools", state_root="repo:tools:.",
        effort_slugs=["unfinished"],
    ))
    assert config["state_root"] == str(roots[0])
    assert len(calls) == 1


def test_issue_validation_shares_execution_and_script_snapshot(tmp_path, monkeypatch):
    from agent_dispatch.repository_issue_loops import validate_config
    from tests.test_repository_issue_loops import _config

    roots, calls = _moving_registry(monkeypatch, tmp_path)
    config = validate_config(_config(
        execution_repo="tools",
        forge={"provider": "script", "producer_login": "fixture",
               "command": ["repo:tools:backlog.py"], "cwd": "repo:tools:."},
    ))
    assert config["forge"]["cwd"] == str(roots[0])
    assert config["forge"]["command"] == [sys.executable, str(roots[0] / "backlog.py")]
    assert len(calls) == 1


def test_standby_emitter_does_not_resolve_registered_cwd(tmp_path, monkeypatch):
    from agent_dispatch.producers.emitter import run_tick
    from tests.test_producers_emitter import FakeClient

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("standby performed registry lookup"),
    )
    client = FakeClient()
    monkeypatch.setattr(client, "acquire_schedule_lease", lambda *a, **k: {"granted": False})
    result = run_tick(client, {
        "id": "standby", "interval_seconds": 60, "command": ["python"],
        "cwd": "repo:tools:.",
    }, holder="fixture")
    assert result["held"] is False


def test_command_emitter_uses_one_snapshot_after_lease(tmp_path, monkeypatch):
    from agent_dispatch.producers.emitter import run_tick
    from tests.test_producers_emitter import FakeClient

    roots, calls = _moving_registry(monkeypatch, tmp_path)
    executed = []
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "dispatch"))

    def runner(argv, **kwargs):
        executed.append((argv, kwargs["cwd"]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = run_tick(FakeClient(), {
        "id": "snapshot", "interval_seconds": 60,
        "command": ["{python}", "repo:tools:backlog.py"], "cwd": "repo:tools:.",
    }, holder="fixture", runner=runner)
    assert result["returncode"] == 0
    assert executed == [([sys.executable, str(roots[0] / "backlog.py")], str(roots[0]))]
    assert len(calls) == 1


def test_named_recipe_reads_same_snapshot_as_nested_base(tmp_path, monkeypatch):
    from agent_dispatch.registrar_recipes import resolve_extends

    roots, calls = _moving_registry(monkeypatch, tmp_path)
    for root in roots:
        (root / "recipe.json").write_text(json.dumps({
            "extends": "base.json", "description": root.name,
        }), encoding="utf-8")
        (root / "base.json").write_text(json.dumps({
            "kind": "emitter", "name": "fixture",
            "spec": {"command": ["python"], "cwd": ".", "interval_seconds": 60},
        }), encoding="utf-8")
    result = resolve_extends({"extends": "repo:tools:recipe.json"}, base_dir=tmp_path)
    assert result["description"] == "first"
    assert result["spec"]["cwd"] == "repo:tools:."
    assert len(calls) == 1


def test_named_nested_recipe_chain_shares_registry_snapshot(tmp_path, monkeypatch):
    from agent_dispatch.registrar_recipes import resolve_extends

    roots, calls = _moving_registry(monkeypatch, tmp_path)
    for root in roots:
        (root / "recipe.json").write_text(json.dumps({
            "extends": "repo:tools:base.json",
        }), encoding="utf-8")
        (root / "base.json").write_text(json.dumps({
            "kind": "emitter", "name": "fixture", "description": root.name,
            "spec": {"command": ["python"], "interval_seconds": 60},
        }), encoding="utf-8")
    result = resolve_extends({"extends": "repo:tools:recipe.json"}, base_dir=tmp_path)
    assert result["description"] == "first"
    assert len(calls) == 1


@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_snapshot_allows_read_only_reads_but_refuses_cached_execution(
    tmp_path, monkeypatch, repo_class,
):
    calls = _registry(monkeypatch, tmp_path, repo_class=repo_class)
    snapshot = resources.RepositoryResourceSnapshot()
    assert snapshot.resolve("repo:tools:.", execution=False, directory=True) == str(tmp_path)
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        snapshot.resolve("repo:tools:.", execution=True, directory=True)
    assert len(calls) == 1


@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_effort_state_root_is_a_read_of_read_only_repo(tmp_path, monkeypatch, repo_class):
    from agent_dispatch.effort_driver_loops import _resolve_state_root

    calls = _registry(monkeypatch, tmp_path, repo_class=repo_class)
    assert _resolve_state_root(
        "repo:tools:.", cwd=None, snapshot=resources.RepositoryResourceSnapshot(),
    ) == str(tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("repo_class", ["reference", "knowledge"])
def test_named_recipe_relative_cwd_keeps_read_only_provenance(
    tmp_path, monkeypatch, pending, repo_class,
):
    from agent_dispatch.registrar_recipes import resolve_extends

    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "base.json").write_text(json.dumps({
        "kind": "emitter", "name": "fixture",
        "spec": {"command": ["python"], "cwd": "{workdir}" if pending else ".",
                 "interval_seconds": 60},
    }), encoding="utf-8")
    (recipes / "mid.json").write_text(json.dumps({"extends": "base.json"}), encoding="utf-8")
    calls = _registry(monkeypatch, tmp_path, repo_class=repo_class)
    result = resolve_extends({
        "extends": "repo:tools:recipes/mid.json", "params": {"workdir": "."},
    }, base_dir=tmp_path / "leaf")
    assert result["spec"]["cwd"] == "repo:tools:recipes"
    assert len(calls) == 1
    with pytest.raises(RegistrarError, match=f"{repo_class}-only"):
        resources.resolve_execution_resources(["python"], result["spec"]["cwd"])


@pytest.mark.parametrize("kind", ["issue", "effort", "script"])
@pytest.mark.parametrize("caller", ["validator", "registration", "declaration"])
def test_builtin_standby_performs_only_shape_validation(tmp_path, monkeypatch, kind, caller):
    from agent_dispatch.producers.emitter import load_spec, run_tick, validate_spec
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrations import validate_registration
    from tests.test_producers_emitter import FakeClient
    from tests.test_repository_issue_loops import _config as issue_config
    from tests.test_effort_driver_loops import _config as effort_config

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("builtin standby performed registry lookup"),
    )
    client = FakeClient()
    monkeypatch.setattr(client, "acquire_schedule_lease", lambda *a, **k: {"granted": False})
    spec = {"id": "standby", "interval_seconds": 60, "cwd": "repo:tools:."}
    if kind == "effort":
        spec["effort_driver_loop"] = effort_config(
            tmp_path, execution_repo="tools", state_root="repo:tools:.",
            effort_slugs=["unfinished"], worker_identity="missing-until-held",
        )
    else:
        config = issue_config(execution_repo="tools", worker_identity="missing-until-held")
        if kind == "script":
            config["forge"] = {
                "provider": "script", "producer_login": "fixture",
                "command": ["repo:tools:backlog.py"], "cwd": "repo:tools:.",
            }
        spec["repository_issue_loop"] = config
    if caller == "validator":
        validate_spec(spec)
    elif caller == "registration":
        validate_registration("emitter", spec)
    else:
        declaration = load_declaration({"name": "standby", "kind": "emitter", "spec": spec})
        assert declaration.spec["id"] == "standby"
    path = tmp_path / "emitter.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert run_tick(client, load_spec(path), holder="fixture")["held"] is False


def test_builtin_standby_still_refuses_malformed_script_config(tmp_path, monkeypatch):
    from agent_dispatch.producers.emitter import EmitterError, run_tick
    from tests.test_producers_emitter import FakeClient
    from tests.test_repository_issue_loops import _config

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("malformed standby performed registry lookup"),
    )
    client = FakeClient()
    monkeypatch.setattr(
        client, "acquire_schedule_lease", lambda *a, **k: pytest.fail("invalid spec took lease"),
    )
    with pytest.raises(EmitterError, match="timeout_seconds"):
        run_tick(client, {
            "id": "standby", "interval_seconds": 60, "cwd": "repo:tools:.",
            "repository_issue_loop": _config(
                execution_repo="tools",
                forge={"provider": "script", "producer_login": "fixture",
                       "command": ["repo:tools:backlog.py"], "timeout_seconds": -1},
            ),
        }, holder="fixture")


@pytest.mark.parametrize("field", ["command", "cwd", "side_load"])
@pytest.mark.parametrize("caller", ["validator", "registration", "declaration", "loader"])
def test_direct_emitter_resource_grammar_is_rejected_before_admission(
    tmp_path, monkeypatch, field, caller,
):
    from agent_dispatch.producers.emitter import load_spec, validate_spec
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrations import validate_registration

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("shape validation attempted resource resolution"),
    )
    invalid = "repo:tools:../escape"
    spec = {"id": "fixture", "command": ["python"], "interval_seconds": 60}
    if field == "command":
        spec["command"] = [invalid]
    elif field == "cwd":
        spec["cwd"] = invalid
    else:
        spec["side_load"] = {"command": [invalid, "{change_ref}"]}
    with pytest.raises(ValueError, match="registered root"):
        if caller == "validator":
            validate_spec(spec)
        elif caller == "registration":
            validate_registration("emitter", spec)
        elif caller == "declaration":
            load_declaration({"name": "fixture", "kind": "emitter", "spec": spec})
        else:
            path = tmp_path / "emitter.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            load_spec(path)


@pytest.mark.parametrize("field", ["argv", "cwd"])
@pytest.mark.parametrize("caller", ["registry", "loading", "registration", "declaration"])
def test_evaluator_resource_grammar_is_rejected_before_admission(monkeypatch, field, caller):
    from agent_dispatch.producers.evaluator import ScriptEvaluatorRegistry, load_evaluator
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrations import validate_registration

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("shape validation attempted resource resolution"),
    )
    invalid = "repo:tools:../escape"
    spec = {"scripts": {"verify": ["python"]}, "cwd": "repo:tools:."}
    if field == "argv":
        spec["scripts"]["verify"] = [invalid]
    else:
        spec["cwd"] = invalid
    registration = {
        "repo": "example/project", "evaluator_ref": "verify", "evaluator_spec": spec,
    }
    with pytest.raises(ValueError, match="registered root"):
        if caller == "registry":
            ScriptEvaluatorRegistry.from_spec(spec)
        elif caller == "loading":
            load_evaluator(spec, evaluator_ref="verify")
        elif caller == "registration":
            validate_registration("evaluator", registration)
        else:
            load_declaration({"name": "fixture", "kind": "evaluator", "spec": registration})


@pytest.mark.parametrize("kind", ["emitter", "evaluator"])
def test_valid_direct_resources_are_admitted_without_resolution(monkeypatch, kind):
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.registrations import validate_registration

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("admission attempted resource resolution"),
    )
    if kind == "emitter":
        spec = {
            "id": "fixture", "command": ["repo:tools:scripts/tick.py"],
            "cwd": "repo:tools:.", "interval_seconds": 60,
            "side_load": {"command": ["repo:tools:scripts/side.py", "{change_ref}"]},
        }
    else:
        spec = {
            "repo": "example/project", "evaluator_ref": "verify",
            "evaluator_spec": {
                "scripts": {"verify": ["repo:tools:scripts/verify.py"]},
                "cwd": "repo:tools:.",
            },
        }
    validate_registration(kind, spec)
    assert load_declaration({"name": "fixture", "kind": kind, "spec": spec}).kind == kind


def test_field_provenance_and_merge_share_named_snapshot(tmp_path, monkeypatch):
    from agent_dispatch.registrar_document import expand_declaration_document
    from tests.test_repository_issue_loops import _config

    roots, calls = _moving_registry(monkeypatch, tmp_path)
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    for root in roots:
        (root / "recipe.json").write_text(json.dumps(_config(
            forge={"provider": "script", "producer_login": "fixture",
                   "command": ["relative.py"], "cwd": "."},
        )), encoding="utf-8")
    with pytest.raises(RegistrarError, match="inherited"):
        expand_declaration_document(
            {"extends": "repo:tools:recipe.json"}, directory=leaf, repo_root=leaf,
        )
    assert len(calls) == 1


@pytest.mark.parametrize("cwd", [".", "working"])
@pytest.mark.parametrize("caller", ["validator", "expansion", "document"])
def test_shape_admission_refuses_inherited_relative_script_cwd(tmp_path, monkeypatch, cwd, caller):
    from agent_dispatch.registrar_document import expand_declaration_document
    from agent_dispatch.repository_issue_loops import expand_repository_issue_loop
    from agent_dispatch.script_provider import validate_script_forge_config
    from tests.test_repository_issue_loops import _config

    forge = {
        "provider": "script", "producer_login": "fixture",
        "command": ["repo:tools:backlog.py"], "cwd": cwd,
    }
    if caller == "document":
        root = tmp_path / "tools"
        root.mkdir()
        (root / "recipe.json").write_text(json.dumps(_config(forge=forge)), encoding="utf-8")
        _registry(monkeypatch, root)
    else:
        monkeypatch.setattr(
            resources, "run_agent_worktrees_capture",
            lambda *a, **k: pytest.fail("shape admission resolved execution resources"),
        )
    with pytest.raises(RegistrarError, match="forge.cwd: inherited"):
        if caller == "validator":
            validate_script_forge_config(
                forge, provider="script", repo_root=tmp_path,
                inherited_script_fields=frozenset({"cwd"}), resolve_resources=False,
            )
        elif caller == "expansion":
            expand_repository_issue_loop(
                _config(forge=forge), repo_root=tmp_path,
                inherited_script_fields=frozenset({"cwd"}),
            )
        else:
            expand_declaration_document(
                {"extends": "repo:tools:recipe.json"},
                directory=tmp_path / "leaf", repo_root=tmp_path / "leaf",
            )


@pytest.mark.parametrize("cwd,inherited", [
    ("repo:tools:.", frozenset({"cwd"})),
    (".", frozenset()),
])
def test_shape_admission_preserves_valid_script_cwd_without_resolution(monkeypatch, cwd, inherited):
    from agent_dispatch.script_provider import validate_script_forge_config

    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("shape admission resolved execution resources"),
    )
    result = validate_script_forge_config(
        {"command": ["repo:tools:backlog.py"], "cwd": cwd},
        provider="script", inherited_script_fields=inherited, resolve_resources=False,
    )
    assert result["cwd"] == cwd
    assert result["command"] == ["repo:tools:backlog.py"]


def test_documented_named_evaluator_is_a_valid_registration():
    from pathlib import Path

    import yaml

    from agent_dispatch.registrar import load_declaration

    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Registered repository resources", 1)[1]
    example = section.split("```yaml\n", 1)[1].split("```", 1)[0]
    declaration = load_declaration(yaml.safe_load(example))
    assert declaration.spec["evaluator_ref"] == "verify-work"
    assert declaration.spec["evaluator_spec"]["cwd"] == "repo:tools:."
