from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "binding-knowledge" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bk = _load("bind_knowledge")
cp = _load("control_plane_binding")


@pytest.fixture
def pair(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    topology = knowledge / "machines.yaml"
    topology.write_text(
        "control_plane:\n"
        "  project: harness\n"
        "  harness: harness\n"
        "  knowledge: knowledge\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    roles = {
        "harness": "harness",
        "knowledge": "knowledge",
        "legacy_project": "harness",
        "paired": True,
        "legacy_compatible": True,
    }
    registry = {
        "repos": [
            {
                "name": name,
                "class": repo_class,
                "paths": {bk._current_platform_key(): str(path)},
                "remote": f"file:///example/{name}",
                "default_branch": "main",
            }
            for name, repo_class, path in (
                ("harness", "worktree", harness),
                ("knowledge", "knowledge", knowledge),
            )
        ]
    }
    calls = []

    def native(_command, args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["knowledge", "control-plane"]:
            value = {
                "version": 1, "source": str(topology), "control_plane": roles,
                "platform": bk._current_platform_key(),
            }
        elif args == ["repos", "list", "--json"]:
            value = registry
        elif args == ["-p", "harness", "config-root", "--json"]:
            value = {
                "repo": "harness",
                "stateless": True,
                "bound": True,
                "config_root": str(home / ".harness"),
            }
        elif args in (["state-root", "--json"], ["-p", "harness", "state-root", "--json"]):
            config = home / ".harness" / "config.yaml"
            if not config.is_file():
                return subprocess.CompletedProcess(
                    args, 3, json.dumps({
                        "source": "knowledge_repo", "requires_external": True,
                        "repo": "", "bound": False,
                    }), "",
                )
            assert "knowledge_repo: knowledge" in config.read_text(encoding="utf-8")
            value = {
                "source": "knowledge_repo",
                "bound": True,
                "requires_external": True,
                "repo": "knowledge",
                "state_root": str(knowledge),
            }
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    monkeypatch.setattr(bk, "knowledge_origin", lambda path: f"file:///example/{Path(path).name}")
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")
    monkeypatch.setattr(cp, "validate_checkout", lambda _path: None)
    return home, harness, knowledge, topology, roles, registry, calls


def _resolve(pair, **kwargs):
    home, _harness, _knowledge, topology, *_ = pair
    return cp.resolve_binding(bk, str(topology), "native-owner", home=home, **kwargs)


def test_fresh_pair_uses_normal_writer_without_prior_local_config(pair):
    home, harness, knowledge, topology, _roles, registry, calls = pair
    before = topology.read_bytes(), json.dumps(registry, sort_keys=True)
    selected = _resolve(pair)
    assert not home.exists()
    summary = bk.bind(
        selected["harness"],
        selected["knowledge"],
        selected["knowledge_path"],
        home=home,
        harness_path=selected["harness_path"],
        agent_worktrees_path="native-owner",
        assemble_plugins=False,
        preserve_binding=True,
    )
    assert summary["registration"]["status"] == "ready"
    assert summary["registration"]["class"] == "knowledge"
    assert summary["state_root"]["status"] == "ready"
    config = home / ".harness" / "config.yaml"
    assert "knowledge_repo: knowledge" in config.read_text(encoding="utf-8")
    assert not (home / ".knowledge").exists()
    assert (topology.read_bytes(), json.dumps(registry, sort_keys=True)) == before
    assert not any(args[:2] == ["repos", "add"] for args, _kwargs in calls)
    assert list(harness.iterdir()) == []
    assert list(knowledge.iterdir()) == [topology]


def test_matching_binding_preserves_existing_config_bytes_and_timestamp(pair):
    home, harness, knowledge, *_ = pair
    config = home / ".harness" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "# retained\nrepo_name: harness\nknowledge_repo: knowledge  # keep comment\nmachine: existing\n",
        encoding="utf-8",
    )
    os.utime(config, ns=(1_000_000_000, 1_000_000_000))
    before = config.read_bytes(), config.stat().st_mtime_ns
    _resolve(pair)
    bk.bind(
        "harness",
        "knowledge",
        str(knowledge),
        home=home,
        harness_path=str(harness),
        agent_worktrees_path="native-owner",
        assemble_plugins=False,
        preserve_binding=True,
    )
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before


def test_binding_changed_after_preflight_is_not_silently_repointed(pair):
    home, harness, knowledge, *_ = pair
    _resolve(pair)
    config = home / ".harness" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("knowledge_repo: another-companion\n", encoding="utf-8")
    before = config.read_bytes()
    with pytest.raises(RuntimeError, match="conflict"):
        bk.bind(
            "harness",
            "knowledge",
            str(knowledge),
            home=home,
            harness_path=str(harness),
            agent_worktrees_path="native-owner",
            assemble_plugins=False,
            preserve_binding=True,
        )
    assert config.read_bytes() == before


@pytest.mark.parametrize("binding", ["other-knowledge", "knowledge\nknowledge_repo: other"])
def test_conflicting_or_duplicate_binding_refuses_before_any_registry_write(pair, binding):
    home, *_ = pair
    config = home / ".harness" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(f"knowledge_repo: {binding}\n", encoding="utf-8")
    before = config.read_bytes()
    with pytest.raises(RuntimeError, match="conflict|duplicate"):
        _resolve(pair)
    assert config.read_bytes() == before
    assert len(pair[-1]) == 3


def test_native_configuration_namespace_is_used_without_touching_legacy_binding(
    pair, monkeypatch
):
    home, harness, knowledge, *_ = pair
    legacy = home / ".harness" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("knowledge_repo: other-legacy-companion\n", encoding="utf-8")
    root = home / "installation-cell" / "projects" / "harness"
    original = bk._run_agent_worktrees

    def native(command, args, **kwargs):
        if args == ["-p", "harness", "config-root", "--json"]:
            return subprocess.CompletedProcess(
                args, 0,
                json.dumps({
                    "repo": "harness", "stateless": True,
                    "bound": True, "config_root": str(root),
                }), "",
            )
        if args in (["state-root", "--json"], ["-p", "harness", "state-root", "--json"]):
            config = root / "config.yaml"
            if not config.is_file():
                return subprocess.CompletedProcess(
                    args, 3, json.dumps({
                        "source": "knowledge_repo", "requires_external": True,
                        "repo": "", "bound": False,
                    }), "",
                )
            assert "knowledge_repo: knowledge" in config.read_text()
            return subprocess.CompletedProcess(
                args, 0,
                json.dumps({
                    "bound": True, "repo": "knowledge", "state_root": str(knowledge),
                    "source": "knowledge_repo", "requires_external": True,
                }),
                "",
            )
        return original(command, args, **kwargs)

    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    selected = _resolve(pair)
    assert selected["config_root"] == str(root.resolve())
    bk.bind(
        "harness", "knowledge", str(knowledge),
        home=home, harness_path=str(harness),
        agent_worktrees_path="native-owner",
        config_root=Path(selected["config_root"]),
        assemble_plugins=False, preserve_binding=True,
    )
    assert legacy.read_text(encoding="utf-8") == "knowledge_repo: other-legacy-companion\n"
    assert "knowledge_repo: knowledge" in (root / "config.yaml").read_text()


def test_explicit_test_home_cannot_redirect_native_owner_into_another_home(pair, monkeypatch):
    original = bk._run_agent_worktrees

    def native(command, args, **kwargs):
        if args == ["-p", "harness", "config-root", "--json"]:
            return subprocess.CompletedProcess(
                args, 0,
                json.dumps({
                    "repo": "harness", "stateless": True, "bound": True,
                    "config_root": str(pair[0].parent / "another-home" / ".harness"),
                }), "",
            )
        return original(command, args, **kwargs)

    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    with pytest.raises(RuntimeError, match="--home override"):
        _resolve(pair)
    assert not pair[0].exists()


@pytest.mark.parametrize(
    "overrides",
    [{"harness": "other"}, {"knowledge": "other"}, {"knowledge_path": "/unregistered"}],
)
def test_explicit_override_cannot_change_declared_roles_or_registered_paths(pair, overrides):
    with pytest.raises(RuntimeError, match="conflict"):
        _resolve(pair, **overrides)
    assert not pair[0].exists()


def test_unregistered_companion_is_not_fallback_discovery(pair):
    pair[-2]["repos"].pop()
    with pytest.raises(RuntimeError, match="exact canonical registration"):
        _resolve(pair)
    assert not pair[0].exists()


def test_knowledge_repository_cannot_be_the_executable_harness(pair):
    pair[-2]["repos"][0]["class"] = "knowledge"
    with pytest.raises(RuntimeError, match="declared lifecycle"):
        _resolve(pair)
    assert not pair[0].exists()


def test_old_native_without_tuple_reader_is_explicitly_unsupported(pair, monkeypatch):
    def unsupported(_command, args, **_kwargs):
        return subprocess.CompletedProcess(args, 2, "", "unrecognized control-plane command")

    monkeypatch.setattr(bk, "_run_agent_worktrees", unsupported)
    with pytest.raises(RuntimeError, match="unrecognized control-plane"):
        _resolve(pair)
    assert not pair[0].exists()


def test_pair_without_explicit_companion_does_not_become_a_binding(pair):
    pair[4]["paired"] = False
    pair[4]["knowledge"] = None
    with pytest.raises(RuntimeError, match="explicit repository pair"):
        _resolve(pair)
    assert not pair[0].exists()


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_native_platform_drives_pair_paths_and_normal_inspection(pair, monkeypatch, platform):
    home, harness, knowledge, *_ = pair
    original = bk._run_agent_worktrees
    for entry, path in zip(pair[-2]["repos"], (harness, knowledge)):
        entry["paths"] = {platform: str(path)}

    def native(command, args, **kwargs):
        result = original(command, args, **kwargs)
        if args[:2] == ["knowledge", "control-plane"]:
            value = json.loads(result.stdout)
            value["platform"] = platform
            return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
        return result

    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(bk, "_current_platform_key", lambda: "legacy-incorrect")
    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    selected = _resolve(pair)
    assert selected["platform_key"] == platform
    summary = bk.bind(
        "harness", "knowledge", str(knowledge), home=home,
        harness_path=str(harness), agent_worktrees_path="native-owner",
        assemble_plugins=False, preserve_binding=True, platform_key=platform,
    )
    assert summary["registration"]["status"] == "ready"


@pytest.mark.parametrize("unset", ["null", "Null", "NULL", "~", ""])
def test_unquoted_null_binding_is_unconfigured_not_a_repository_name(unset):
    assert not bk._check_existing_binding(f"knowledge_repo: {unset}\n", "knowledge")


def test_quoted_null_repository_name_is_not_treated_as_unconfigured():
    assert bk._check_existing_binding('knowledge_repo: "null"\n', "null")
    with pytest.raises(RuntimeError, match="conflicts"):
        bk._check_existing_binding('knowledge_repo: "null"\n', "knowledge")


@pytest.mark.parametrize("name", ["true", "NO", "on", "123", "2026-10-18"])
def test_typed_nonstring_binding_cannot_masquerade_as_a_matching_name(name):
    with pytest.raises(RuntimeError, match="YAML string"):
        bk._check_existing_binding(f"knowledge_repo: {name}\n", name)
    assert bk._check_existing_binding(f'knowledge_repo: "{name}"\n', name)


@pytest.mark.parametrize(
    "value",
    [
        {"repo": "inherited-other", "bound": True},
        {"repo": True, "bound": True},
        {"repo": "", "bound": True},
        {"repo": "", "bound": False, "source": "self"},
        {"error": "reader failed"},
    ],
)
def test_effective_conflict_or_malformed_binding_refuses_before_write(pair, monkeypatch, value):
    original = bk._run_agent_worktrees

    def native(command, args, **kwargs):
        if args == ["-p", "harness", "state-root", "--json"]:
            effective = {"source": "knowledge_repo", "requires_external": True, **value}
            return subprocess.CompletedProcess(args, 3, json.dumps(effective), "")
        return original(command, args, **kwargs)

    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    with pytest.raises(RuntimeError, match="effective harness knowledge binding"):
        _resolve(pair)
    assert not pair[0].exists()


@pytest.mark.parametrize("bare", [False, True, "worktree-config"])
def test_control_plane_git_root_accepts_normal_and_bare_anchors(tmp_path, bare):
    root = tmp_path / "anchor"
    command = ["git", "init", "--quiet"]
    if bare is True:
        command.append("--bare")
    result = subprocess.run(
        [*command, str(root)], capture_output=True, text=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    if bare == "worktree-config":
        for args in (
            ["config", "extensions.worktreeConfig", "true"],
            ["config", "--worktree", "core.bare", "true"],
            ["config", "--unset", "core.bare"],
        ):
            result = subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, text=True,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
            assert result.returncode == 0, result.stderr
    cp.validate_checkout(str(root))
    if not bare:
        nested = root / "nested"
        nested.mkdir()
        with pytest.raises(RuntimeError, match="not its Git root"):
            cp.validate_checkout(str(nested))


def test_control_plane_git_root_rejects_stale_non_git_directory(tmp_path):
    with pytest.raises(RuntimeError, match="not a Git repository"):
        cp.validate_checkout(str(tmp_path))
