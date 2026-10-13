"""Named resources consume the registry owner, never a discovery fallback."""

from __future__ import annotations

import json
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


def test_reference_root_can_be_read_but_not_executed(tmp_path, monkeypatch):
    _registry(monkeypatch, tmp_path, repo_class="reference")
    assert resources.registered_repository_root("tools") == tmp_path
    with pytest.raises(RegistrarError, match="reference-only"):
        resources.registered_repository_root("tools", execution=True)


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


def test_explicit_same_root_pointer_migration_preserves_provenance(tmp_path, monkeypatch):
    from agent_dispatch import registrar_discovery as discovery

    root = tmp_path / "tools"
    root.mkdir()
    base = tmp_path / "pointers"
    discovery.add_pointer(
        "tools", root, kind="repo", owner="existing-owner",
        aliases=["example.test/team/tools", "team/tools"], base=base,
    )
    _registry(monkeypatch, root)
    migrated = discovery.add_pointer(
        "tools", kind="repo", repository="tools", base=base,
    )
    assert migrated.owner == "existing-owner"
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
