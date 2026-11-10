from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import project_state, repos, repos_configuration, repos_knowledge_configuration
from agent_worktrees import repos_knowledge_preferences as preferences
from agent_worktrees.repos_configuration_sources import ConfigurationError


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    registry = repos._repos_yaml_path()
    registry.parent.mkdir(parents=True)
    entries = {}
    for name in ("first", "second", "shared", "other"):
        root = tmp_path / name / "same-basename"
        root.mkdir(parents=True)
        entries[name] = {
            "class": "knowledge" if name in {"shared", "other"} else "worktree",
            repos._current_platform(): str(root), "remote": f"https://github.com/example/{name}.git",
            "default_branch": "main", "account": "selected-account",
        }
    registry.write_text(yaml.safe_dump({"repos": entries}), encoding="utf-8")
    config = SimpleNamespace(
        knowledge_repo="", machine="example-worker",
        default_repo=SimpleNamespace(anchor=entries["first"][repos._current_platform()]),
    )
    loader = preferences.cfg.load_project_config
    monkeypatch.setattr(preferences.cfg, "load_project_config", lambda *args, **kwargs: config)
    calls = []

    def invoke(project, name, operation, token="", **kwargs):
        calls.append((project, name, operation, token))
        if operation == "preview":
            return {"version": 1, "token": "a" * 64, "operation": "set", "target": {"resource": f"{project}/{name}"}}
        assert kwargs["portable_resolution"] == preferences.resolve(project, ids[project], ids[name])
        return {"status": "persisted", "pointer_committed": True}

    monkeypatch.setattr(repos_knowledge_configuration, "invoke", invoke)
    ids = {name: project_state.canonical_repository_id(name) for name in entries}
    return SimpleNamespace(
        root=tmp_path, registry=registry, config=config, calls=calls, ids=ids, entries=entries, loader=loader,
    )


def mutate(native, name, **changes):
    value = yaml.safe_load(native.registry.read_text())
    value["repos"][name].update(changes)
    native.registry.write_text(yaml.safe_dump(value), encoding="utf-8")


def resolve(native, project="first", knowledge="shared"):
    return preferences.resolve(project, native.ids[project], native.ids[knowledge])


def preview(native, project="first", knowledge="shared"):
    return preferences.preview(project, native.ids[project], native.ids[knowledge])


def apply(native, token, project="first", knowledge="shared"):
    return preferences.apply(project, native.ids[project], native.ids[knowledge], token)


def test_native_portable_ids_distinguish_same_basename_harnesses_and_share_one_knowledge(native):
    before = native.registry.read_bytes()
    first, second = resolve(native), resolve(native, project="second")
    assert first["harness"]["repository_id"] != second["harness"]["repository_id"]
    assert first["knowledge"] == second["knowledge"]
    assert first["knowledge"]["account"] == "selected-account"
    assert first["knowledge"]["class"] == "knowledge"
    assert first["knowledge"]["default_branch"] == "main"
    assert first["status"] == second["status"] == "suggested"
    assert native.calls == [] and native.registry.read_bytes() == before


def test_native_identity_codec_is_shared_by_legacy_context_and_namespaced_identity(native, monkeypatch):
    from agent_worktrees import config_sources

    config = preferences.cfg.Config(
        srcroot=str(native.root), repo_name="first", machine="example-worker",
        platform=repos._current_platform(),
        repos={"first": preferences.cfg.RepoConfig(
            anchor=native.entries["first"][repos._current_platform()],
            worktree_root=str(native.root / "trees"),
        )},
    )
    assert config_sources.configuration_context(config)["repository_id"] == native.ids["first"]
    monkeypatch.setattr(project_state.registry_paths, "installation_context", lambda: {
        "cellRoot": str(native.root / "cell"), "marketplaceId": "example-marketplace",
    })
    assert project_state._identity("first").repository_id == native.ids["first"]
    assert project_state.canonical_repository_id("first", remote="git@github.com:EXAMPLE/FIRST.git") == native.ids["first"]
    assert not (native.root / "cell").exists()


def test_remote_free_context_uses_registered_path_without_portable_identity(native, monkeypatch):
    from agent_worktrees import config_sources

    before = {}
    for name in ("first", "second"):
        mutate(native, name, remote="")
        before[name] = project_state.canonical_repository_id(name)
    config = preferences.cfg.Config(
        srcroot=str(native.root), repo_name="first", machine="example-worker",
        platform=repos._current_platform(),
        repos={"first": preferences.cfg.RepoConfig(
            anchor=native.entries["first"][repos._current_platform()],
            worktree_root=str(native.root / "trees"),
        )},
    )
    registry_bytes = native.registry.read_bytes()
    assert before["first"] != before["second"]
    assert config_sources.configuration_context(config)["repository_id"] == before["first"]
    rows = {row["repository"]: row for row in preferences.identities()["entries"]}
    assert rows["first"]["repository_id"] is None
    assert rows["first"]["status"] == "local-only"
    with pytest.raises(ValueError, match="not portable"):
        project_state.canonical_repository_id("first", portable=True)
    monkeypatch.setattr(project_state.registry_paths, "installation_context", lambda: {
        "cellRoot": str(native.root / "cell"), "marketplaceId": "example-marketplace",
    })
    assert project_state._identity("first").repository_id == before["first"]
    assert native.registry.read_bytes() == registry_bytes
    assert not (native.root / "cell").exists()


@pytest.mark.parametrize("path", ["", "relative-checkout"])
def test_remote_free_identity_requires_an_absolute_registered_path(native, path):
    mutate(native, "first", remote="", **{repos._current_platform(): path})
    with pytest.raises(ValueError, match="path"):
        project_state.canonical_repository_id("first")


def test_native_remote_free_configuration_context_reaches_the_shared_consumer(native):
    from configuration_context import read_owner_context

    root = Path(native.entries["first"][repos._current_platform()])
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(root)],
        check=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    mutate(native, "first", remote="")
    before = native.registry.read_bytes()
    report = read_owner_context(
        [sys.executable, "-m", "agent_worktrees", "-p", "first"],
        root, required=True,
    )
    context = report["context"]
    assert context["repository_id"] == project_state.canonical_repository_id("first")
    assert Path(context["base_root"]) == root
    assert context["binding"] == "unbound"
    assert native.registry.read_bytes() == before
    remotes = subprocess.run(
        ["git", "-C", str(root), "remote"], check=True,
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert not remotes.stdout.strip()


@pytest.mark.parametrize("kind", ["missing", "ambiguous", "unavailable", "reference", "local-only"])
def test_missing_ambiguous_stale_or_local_only_registration_never_falls_back_to_basename(native, kind):
    if kind == "missing":
        identity = "repo-" + "0" * 24
    elif kind == "ambiguous":
        document = yaml.safe_load(native.registry.read_text())
        document["repos"]["alias"] = dict(document["repos"]["shared"])
        native.registry.write_text(yaml.safe_dump(document), encoding="utf-8")
        identity = native.ids["shared"]
    elif kind == "unavailable":
        mutate(native, "shared", **{repos._current_platform(): str(native.root / "missing")})
        identity = native.ids["shared"]
    elif kind == "reference":
        mutate(native, "shared", **{"class": "reference"})
        identity = native.ids["shared"]
    else:
        mutate(native, "shared", remote=str(native.root / "local-only.git"))
        row = next(entry for entry in preferences.identities()["entries"] if entry["repository"] == "shared")
        assert row["repository_id"] is None and row["status"] == "local-only"
        identity = native.ids["shared"]
    with pytest.raises(ConfigurationError):
        preferences.preview("first", native.ids["first"], identity)
    assert native.calls == []


@pytest.mark.parametrize("binding", ["shared", "other", "unresolved", " shared ", " other "])
def test_existing_binding_is_authoritative_and_unresolved_binding_is_never_absence(native, monkeypatch, binding):
    native.config.knowledge_repo = binding

    def state_root(*args, **kwargs):
        assert kwargs["repo_override"] == binding.strip()
        return SimpleNamespace(
            bound=binding != "unresolved", path=str(native.root / binding.strip()),
            error="failure" if binding == "unresolved" else "",
        )

    monkeypatch.setattr(preferences.state_root, "resolve_state_root", state_root)
    if binding == "unresolved":
        with pytest.raises(ConfigurationError) as caught:
            preview(native)
        assert caught.value.code == "binding-unresolved"
    else:
        result = preview(native)
        assert result["status"] == ("already-bound" if binding.strip() == "shared" else "binding-preserved")
        assert result["binding"]["repository_id"] == native.ids[binding.strip()]
        assert result["token"] is None and result["native_preview"] is None
        assert not result["binding_changed"]
    assert native.calls == []


@pytest.mark.parametrize("timing", ["registry-wait", "pointer-precondition"])
def test_locked_worker_rechecks_portable_scope_at_the_pointer_boundary(native, monkeypatch, timing):
    from contextlib import contextmanager
    from agent_worktrees import repos_registry_transaction

    expected = resolve(native)
    script = native.root / "selected-binding.py"
    script.touch()
    monkeypatch.setattr(repos_knowledge_configuration, "owner_script", lambda _project: script)

    @contextmanager
    def lock(_path):
        if timing == "registry-wait":
            native.config.machine = "changed-worker"
        yield

    def selected(_path, *, run_name, init_globals):
        if timing == "pointer-precondition":
            init_globals["_NATIVE_BINDING_PRECONDITION"]()
            native.config.machine = "changed-worker"
        init_globals["_NATIVE_BINDING_PRECONDITION"]()
        pytest.fail("changed portable scope reached pointer commit")

    monkeypatch.setattr(repos_registry_transaction, "registry_lock", lock)
    monkeypatch.setattr(repos_knowledge_configuration.runpy, "run_path", selected)
    with pytest.raises(ConfigurationError) as caught:
        repos_knowledge_configuration._apply_worker("first", "shared", "a" * 64, str(script), expected)
    assert caught.value.code == "preview-conflict"


@pytest.mark.parametrize(("source", "value"), [
    ("global", False), ("global", 0), ("machine", False), ("machine", 0),
])
def test_real_loader_never_converts_falsy_non_string_binding_sources_to_absence(
    native, monkeypatch, source, value,
):
    monkeypatch.setattr(preferences.cfg, "load_project_config", native.loader)
    path = preferences.cfg.global_config_path() if source == "global" else (
        preferences.cfg.project_dir("first") / "config.yaml"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"knowledge_repo": value}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ConfigurationError) as caught:
        preview(native)
    assert caught.value.code == "binding-unresolved"
    assert path.read_bytes() == before and native.calls == []


@pytest.mark.parametrize("value", [None, ""])
def test_real_loader_keeps_unset_native_binding_sources_compatible(native, monkeypatch, value):
    monkeypatch.setattr(preferences.cfg, "load_project_config", native.loader)
    path = preferences.cfg.global_config_path()
    path.write_text(yaml.safe_dump({"knowledge_repo": value}), encoding="utf-8")
    assert resolve(native)["status"] == "suggested"
    assert native.calls == []


@pytest.mark.parametrize("source", ["global", "machine", "dropin"])
@pytest.mark.parametrize("failure", ["malformed", "unreadable", "invalid-shape"])
def test_real_loader_refuses_uncertain_binding_sources(native, monkeypatch, source, failure):
    import builtins
    import io

    monkeypatch.setattr(preferences.cfg, "load_project_config", native.loader)
    machine = preferences.cfg.project_dir("first") / "config.yaml"
    path = (
        preferences.cfg.global_config_path() if source == "global"
        else machine if source == "machine"
        else machine.parent / "config.d" / "binding.yaml"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "knowledge_repo: [\n" if failure == "malformed"
        else "- knowledge_repo: other\n" if failure == "invalid-shape"
        else "knowledge_repo: other\n",
        encoding="utf-8",
    )
    before = path.read_bytes(), native.registry.read_bytes()
    if failure == "unreadable":
        def guarded_open(original):
            def open_source(file, *args, **kwargs):
                if isinstance(file, (str, Path)) and Path(file) == path:
                    raise PermissionError("synthetic private source")
                return original(file, *args, **kwargs)
            return open_source

        with monkeypatch.context() as denied:
            denied.setattr(builtins, "open", guarded_open(builtins.open))
            denied.setattr(io, "open", guarded_open(io.open))
            with pytest.raises(ConfigurationError) as caught:
                preview(native)
    else:
        with pytest.raises(ConfigurationError) as caught:
            preview(native)
    assert caught.value.code == "binding-unresolved"
    assert (path.read_bytes(), native.registry.read_bytes()) == before
    assert native.calls == []


def test_real_loader_keeps_missing_binding_sources_compatible(native, monkeypatch):
    monkeypatch.setattr(preferences.cfg, "load_project_config", native.loader)
    assert not preferences.cfg.global_config_path().exists()
    assert not (preferences.cfg.project_dir("first") / "config.yaml").exists()
    assert resolve(native)["status"] == "suggested"
    assert native.calls == []


def test_only_explicit_confirmed_adoption_calls_the_existing_native_binding_owner(native):
    result = preview(native)
    assert native.calls == [("first", "shared", "preview", "")]
    receipt = apply(native, result["token"])
    assert receipt["pointer_committed"]
    assert receipt["knowledge_repository_id"] == native.ids["shared"]
    assert native.calls[-1] == ("first", "shared", "apply", "a" * 64)
    assert receipt["preference_schema"] == preferences.SCHEMA


@pytest.mark.parametrize("change", ["account", "class", "remote", "binding", "machine", "target"])
def test_apply_rechecks_ids_registry_binding_execution_scope_and_target(native, monkeypatch, change):
    result = preview(native)
    if change == "account":
        mutate(native, "shared", account="changed-account")
    elif change == "class":
        mutate(native, "shared", **{"class": "reference"})
    elif change == "remote":
        mutate(native, "shared", remote="https://github.com/example/repointed.git")
    elif change == "binding":
        native.config.knowledge_repo = "other"
        monkeypatch.setattr(preferences.state_root, "resolve_state_root", lambda *args, **kwargs: SimpleNamespace(
            bound=True, path=str(native.root / "other"), error="",
        ))
    elif change == "machine":
        native.config.machine = "example-worker-wsl"
    with pytest.raises(ConfigurationError):
        apply(native, result["token"], knowledge="other" if change == "target" else "shared")
    assert not any(call[2] == "apply" for call in native.calls)


def test_registry_change_during_native_preview_refuses_instead_of_blessing_a_new_identity(native, monkeypatch):
    def changed(*args, **kwargs):
        mutate(native, "shared", account="changed")
        return {"token": "a" * 64}

    monkeypatch.setattr(repos_knowledge_configuration, "invoke", changed)
    with pytest.raises(ConfigurationError) as caught:
        preview(native)
    assert caught.value.code == "preview-conflict"


def test_normal_configuration_parser_preserves_name_baseline_and_accepts_explicit_native_ids(native, capsys):
    args = [
        "--context-project", "first", "--harness-id", native.ids["first"],
        "--knowledge-id", native.ids["shared"],
    ]
    assert repos_configuration.command(["knowledge-resolve", *args]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "suggested"
    assert native.calls == []
    assert repos_configuration.command(["knowledge-preview", *args]) == 0
    token = json.loads(capsys.readouterr().out)["token"]
    assert repos_configuration.command(["knowledge-apply", *args, "--preview-token", token]) == 0
    assert json.loads(capsys.readouterr().out)["pointer_committed"]
    native.calls.clear()
    assert repos_configuration.command([
        "knowledge-preview", "--context-project", "first", "--repository", "shared",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["token"] == "a" * 64
    assert native.calls == [("first", "shared", "preview", "")]
    assert repos_configuration.command(["catalog", *args]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-request"


@pytest.mark.parametrize("command", ["knowledge-preview", "knowledge-apply"])
@pytest.mark.parametrize("selection", ["empty-ids", "one-empty-id", "empty-repository"])
def test_supplied_empty_selectors_never_fall_back_to_name_binding(native, capsys, command, selection):
    ids = (
        ["--harness-id", native.ids["first"], "--knowledge-id", native.ids["shared"]]
        if selection == "empty-repository"
        else ["--harness-id", "", "--knowledge-id", native.ids["shared"]]
        if selection == "one-empty-id"
        else ["--harness-id", "", "--knowledge-id", ""]
    )
    assert repos_configuration.command([
        command, "--context-project", "first",
        "--repository", "" if selection == "empty-repository" else "shared",
        *ids, "--preview-token", "a" * 64,
    ]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-request"
    assert native.calls == []


def test_context_mismatch_and_unqualified_identity_refuse_before_native_preview(native):
    with pytest.raises(ConfigurationError) as caught:
        preferences.preview("second", native.ids["first"], native.ids["shared"])
    assert caught.value.code == "context-mismatch"
    with pytest.raises(ConfigurationError):
        preferences.preview("first", "same-basename", native.ids["shared"])
    assert native.calls == []


@pytest.mark.parametrize("operation", ["registry", "registry-json", "config-root", "state-root"])
def test_locked_worker_read_adapter_uses_existing_handlers_and_restores_scope(native, monkeypatch, operation):
    from agent_worktrees import context_cli, output, repos_cli

    entry = Path(sysconfig.get_path("scripts")) / ("agent-worktrees.exe" if sys.platform == "win32" else "agent-worktrees")
    previous, directory, real_stdout = preferences.cfg.active_project(), Path.cwd(), sys.__stdout__
    root = native.root / "first" / "same-basename"
    registry = operation.startswith("registry")
    args = ["repos", "list", "--json"] if registry else ["-p", "first", operation, "--json"]

    def dispatch(arguments):
        assert preferences.cfg.active_project() == "first" and Path.cwd() == root
        assert arguments == (["list", "--json"] if registry else ["--json"])
        if operation == "registry-json":
            output._json_output({"native": True})
        else:
            print('{"native":true}')
        return 3

    module, name = (repos_cli, "cmd_repos_dispatch") if registry else (
        context_cli, "cmd_config_root_dispatch" if operation == "config-root" else "cmd_state_root_dispatch",
    )
    monkeypatch.setattr(module, name, dispatch)
    result = repos_knowledge_configuration._native_read_runner("first")(str(entry), args, cwd=str(root))
    expected = {"version": 1, "native": True} if operation == "registry-json" else {"native": True}
    assert result.returncode == 3 and json.loads(result.stdout) == expected
    assert preferences.cfg.active_project() == previous and Path.cwd() == directory
    assert sys.__stdout__ is real_stdout


@pytest.mark.parametrize("args", [
    ["repos", "add", "other"], ["repos", "gh", "example/other", "--", "api", "user"],
    ["-p", "second", "state-root", "--json"], ["knowledge", "compose-plugins"],
])
def test_locked_worker_read_adapter_never_intercepts_write_auth_or_other_project_verbs(native, args):
    entry = Path(sysconfig.get_path("scripts")) / ("agent-worktrees.exe" if sys.platform == "win32" else "agent-worktrees")
    reader = repos_knowledge_configuration._native_read_runner("first")
    assert reader(str(entry), args) is None
    assert reader(str(native.root / "foreign-owner"), ["repos", "list", "--json"]) is None


def test_locked_worker_read_adapter_restores_scope_after_handler_failure(native, monkeypatch):
    from agent_worktrees import repos_cli

    def failed(_args):
        raise ValueError("synthetic native reader failure")

    monkeypatch.setattr(repos_cli, "cmd_repos_dispatch", failed)
    previous, directory = preferences.cfg.active_project(), Path.cwd()
    entry = Path(sysconfig.get_path("scripts")) / ("agent-worktrees.exe" if sys.platform == "win32" else "agent-worktrees")
    with pytest.raises(ValueError):
        repos_knowledge_configuration._native_read_runner("first")(
            str(entry), ["repos", "list", "--json"], cwd=str(native.root),
        )
    assert preferences.cfg.active_project() == previous and Path.cwd() == directory
