from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "binding-knowledge" / "scripts"
NativeCall = tuple[list[str], dict[str, object]]
PairFixture = tuple[
    Path, Path, Path, Path, dict[str, Any], dict[str, Any], list[NativeCall],
]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {SCRIPTS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bk = _load("bind_knowledge")
cp = _load("control_plane_binding")
configuration = _load("binding_configuration")


def test_private_locked_native_reader_is_optional_and_preserves_return_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = subprocess.CompletedProcess(["native"], 3, '{"bound":false}', "")
    calls: list[tuple[str, list[str], dict[str, Any]]] = []

    def read(command: str, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, args, kwargs))
        return expected

    monkeypatch.setattr(bk, "_NATIVE_BINDING_READ_RUNNER", read, raising=False)
    monkeypatch.setattr(bk, "_agent_worktrees_command", lambda *_args: pytest.fail("native read spawned"))
    assert bk._run_agent_worktrees(
        "native", ["-p", "harness", "state-root", "--json"], cwd="selected", noninteractive=True,
    ) is expected
    assert calls == [("native", ["-p", "harness", "state-root", "--json"], {
        "cwd": "selected", "noninteractive": True,
    })]


@pytest.mark.parametrize("reader", [None, lambda *_args, **_kwargs: None])
def test_private_native_read_adapter_absence_or_unsupported_operation_keeps_subprocess_baseline(
    monkeypatch: pytest.MonkeyPatch, reader: Callable[..., subprocess.CompletedProcess[str] | None] | None,
) -> None:
    monkeypatch.setattr(bk, "_NATIVE_BINDING_READ_RUNNER", reader, raising=False)
    monkeypatch.setattr(bk, "_agent_worktrees_command", lambda command, args: [command, *args])
    expected = subprocess.CompletedProcess(["native"], 0, "{}", "")
    calls: list[list[str]] = []

    def execute(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return expected

    monkeypatch.setattr(bk.subprocess, "run", execute)
    assert bk._run_agent_worktrees("native", ["knowledge", "compose-plugins"]) is expected
    assert calls == [["native", "knowledge", "compose-plugins"]]


@pytest.fixture
def pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PairFixture:
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
    roles: dict[str, Any] = {
        "harness": "harness",
        "knowledge": "knowledge",
        "legacy_project": "harness",
        "paired": True,
        "legacy_compatible": True,
    }
    registry: dict[str, Any] = {
        "platform": bk._current_platform_key(),
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
    calls: list[NativeCall] = []

    def native(_command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        if args[:2] == ["knowledge", "control-plane"]:
            value = {
                "version": 1, "source": str(topology), "control_plane": roles,
                "platform": bk._current_platform_key(),
            }
        elif args == ["repos", "list", "--json"]:
            value = registry
        elif args == ["repos", "find", "knowledge", "--json"]:
            return subprocess.CompletedProcess(args, 1, "", "not found")
        elif args == ["-p", "harness", "config-root", "--json"]:
            value = {
                "repo": "harness",
                "stateless": True,
                "bound": True,
                "config_root": str(home / ".harness"),
            }
        elif args in (["state-root", "--json"], ["-p", "harness", "state-root", "--json"]):
            config = home / ".harness" / "config.yaml"
            if not config.is_file() or "knowledge_repo:" not in config.read_text(encoding="utf-8"):
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
    monkeypatch.setattr(configuration, "control_plane_binding", cp)
    return home, harness, knowledge, topology, roles, registry, calls


def _resolve(pair: PairFixture, **kwargs: object) -> dict[str, Any]:
    home, _harness, _knowledge, topology, *_ = pair
    resolved: dict[str, Any] = cp.resolve_binding(bk, str(topology), "native-owner", home=home, **kwargs)
    return resolved


def test_fresh_pair_uses_normal_writer_without_prior_local_config(pair: PairFixture) -> None:
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


def test_preflight_confirms_inventory_after_probes_and_writer_reads_again(pair: PairFixture) -> None:
    home, harness, knowledge, _topology, _roles, _registry, calls = pair
    selected = _resolve(pair)
    assert sum(args == ["repos", "list", "--json"] for args, _ in calls) == 2

    bk.bind(
        selected["harness"], selected["knowledge"], str(knowledge),
        home=home, harness_path=str(harness), agent_worktrees_path="native-owner",
        assemble_plugins=False, preserve_binding=True,
    )

    assert sum(args == ["repos", "list", "--json"] for args, _ in calls) == 3


def test_existing_binding_preview_apply_uses_real_preserving_writer(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, harness, knowledge, topology, _roles, registry, calls = pair
    original_bind = bk.bind
    monkeypatch.setattr(bk, "bind", lambda *args, **kwargs: original_bind(
        *args, **{**kwargs, "assemble_plugins": False},
    ))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    before = topology.read_bytes(), json.dumps(registry, sort_keys=True)
    proposed = configuration.preview(bk, "native-owner", "harness", "knowledge")
    assert proposed["operation"] == "set" and proposed["target"]["resource"] == "harness/knowledge"
    assert not home.exists()
    result = configuration.apply(bk, "native-owner", "harness", "knowledge", proposed["token"])
    assert result["status"] == "persisted" and result["pointer_committed"] is True
    config = home / ".harness" / "config.yaml"
    assert "knowledge_repo: knowledge" in config.read_text(encoding="utf-8")
    assert (topology.read_bytes(), json.dumps(registry, sort_keys=True)) == before
    assert not any(args[:2] == ["repos", "add"] for args, _ in calls)
    assert list(harness.iterdir()) == []
    matching = config.read_bytes(), config.stat().st_mtime_ns
    again = configuration.preview(bk, "native-owner", "harness", "knowledge")
    configuration.apply(bk, "native-owner", "harness", "knowledge", again["token"])
    assert (config.read_bytes(), config.stat().st_mtime_ns) == matching


@pytest.mark.parametrize("changed", ["registration", "configuration", "late-configuration"])
def test_existing_binding_rechecks_preview_without_overwriting_new_state(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    home, _harness, _knowledge, _topology, _roles, registry, _calls = pair
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    config = home / ".harness" / "config.yaml"
    proposed = configuration.preview(bk, "native-owner", "harness", "knowledge")
    if changed == "registration":
        registry["repos"][-1]["default_branch"] = "changed"
    else:
        config.parent.mkdir(parents=True)
        config.write_text("# another writer\nrepo_name: harness\n", encoding="utf-8")
    before = config.read_bytes() if config.exists() else None
    if changed == "late-configuration":
        # The first read succeeds; the only write boundary sees the changed baseline.
        proposed = configuration.preview(bk, "native-owner", "harness", "knowledge")
        original = bk._binding_yaml.write_binding

        def write(*args: Any, **kwargs: Any) -> None:
            config.write_bytes(b"# newer winning writer\nrepo_name: harness\n")
            original(*args, **kwargs)

        monkeypatch.setattr(bk._binding_yaml, "write_binding", write)
        before = b"# newer winning writer\nrepo_name: harness\n"
    with pytest.raises(RuntimeError, match="preview changed|registration is not ready"):
        configuration.apply(bk, "native-owner", "harness", "knowledge", proposed["token"])
    assert (config.read_bytes() if config.exists() else None) == before


def test_existing_binding_runs_portable_precondition_inside_pointer_lock(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, *_ = pair
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    proposed = configuration.preview(bk, "native-owner", "harness", "knowledge")
    config = home / ".harness" / "config.yaml"
    checks = []

    def changed() -> None:
        checks.append(True)
        assert config.with_name(".knowledge-binding.lock").exists()
        assert not config.exists()
        raise RuntimeError("portable machine scope changed")

    monkeypatch.setattr(bk, "_NATIVE_BINDING_PRECONDITION", changed, raising=False)
    with pytest.raises(RuntimeError, match="portable machine scope changed"):
        configuration.apply(bk, "native-owner", "harness", "knowledge", proposed["token"])
    assert checks == [True] and not config.exists()


def test_existing_binding_refuses_registration_changed_during_final_preflight(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, _harness, _knowledge, _topology, _roles, registry, _calls = pair
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    proposed = configuration.preview(bk, "native-owner", "harness", "knowledge")
    original = bk._run_agent_worktrees
    observations = 0

    def native(command: str, args: list[str], **kwargs: Any) -> Any:
        nonlocal observations
        result = original(command, args, **kwargs)
        if args == ["-p", "harness", "state-root", "--json"]:
            observations += 1
            if observations == 2:
                registry["repos"][-1]["default_branch"] = "changed"
        return result

    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    with pytest.raises(RuntimeError, match="registration changed during binding preflight"):
        configuration.apply(bk, "native-owner", "harness", "knowledge", proposed["token"])
    assert not (home / ".harness" / "config.yaml").exists()


def test_existing_binding_preserves_unrelated_edit_during_portable_callback(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, *_ = pair
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    config = home / ".harness" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"repo_name: harness\n")
    proposed = configuration.preview(bk, "native-owner", "harness", "knowledge")
    changed = config.read_bytes() + b"headless: true\n"
    monkeypatch.setattr(
        bk, "_NATIVE_BINDING_PRECONDITION", lambda: config.write_bytes(changed), raising=False,
    )
    with pytest.raises(RuntimeError, match="configuration changed"):
        configuration.apply(bk, "native-owner", "harness", "knowledge", proposed["token"])
    assert config.read_bytes() == changed


def test_existing_binding_retains_pointer_receipt_after_legacy_cleanup_failure(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, *_ = pair
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    legacy = home / ".harness" / "knowledge-binding.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"\xff")
    proposed = configuration.preview(bk, "native-owner", "harness", "knowledge")
    result = configuration.apply(bk, "native-owner", "harness", "knowledge", proposed["token"])
    assert result["status"] == "incomplete" and result["pointer_committed"] is True
    assert "knowledge_repo: knowledge" in legacy.with_name("config.yaml").read_text("utf-8")
    assert legacy.read_bytes() == b"\xff"


def test_direct_configuration_apply_uses_native_registry_transaction(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []

    def native(command: str, args: list[str], **kwargs: Any) -> Any:
        calls.append((command, args, kwargs))
        return subprocess.CompletedProcess(args, 1, json.dumps({
            "status": "incomplete", "pointer_committed": True,
        }), "")

    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    assert bk.main([
        "--harness", "harness", "--knowledge", "knowledge",
        "--agent-worktrees-path", "selected-owner", "--configuration", "apply",
        "--preview-token", "a" * 64, "--json",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["pointer_committed"] is True
    assert calls == [("selected-owner", [
        "repos", "configuration", "knowledge-apply", "--context-project", "harness",
        "--repository", "knowledge", "--preview-token", "a" * 64,
    ], {"noninteractive": True})]
    assert not pair[0].exists()


@pytest.mark.parametrize("change", ["removed", "repointed"])
@pytest.mark.parametrize("existing", [False, True])
def test_changed_registration_after_preflight_never_writes_binding(
    pair: PairFixture, change: str, existing: bool,
) -> None:
    home, harness, knowledge, topology, _roles, registry, _calls = pair
    config = home / ".harness" / "config.yaml"
    if existing:
        config.parent.mkdir(parents=True)
        config.write_text(
            "# preserved settings\nrepo_name: harness\nknowledge_repo: knowledge\n",
            encoding="utf-8",
        )
    selected = _resolve(pair)
    before = (config.read_bytes(), config.stat().st_mtime_ns) if existing else None
    topology_before = topology.read_bytes()
    if change == "removed":
        registry["repos"].pop()
    else:
        registry["repos"][-1]["paths"] = {
            bk._current_platform_key(): str(knowledge.with_name("repointed")),
        }
    with pytest.raises(RuntimeError, match="companion registration changed"):
        bk.bind(
            selected["harness"], selected["knowledge"], selected["knowledge_path"],
            home=home, harness_path=str(harness), agent_worktrees_path="native-owner",
            assemble_plugins=False, preserve_binding=True,
        )
    if existing:
        assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    else:
        assert not home.exists()
    assert topology.read_bytes() == topology_before


def test_matching_binding_preserves_existing_config_bytes_and_timestamp(pair: PairFixture) -> None:
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


def test_binding_changed_after_preflight_is_not_silently_repointed(pair: PairFixture) -> None:
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
def test_conflicting_or_duplicate_binding_refuses_before_any_registry_write(
    pair: PairFixture, binding: str,
) -> None:
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
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, harness, knowledge, *_ = pair
    legacy = home / ".harness" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("knowledge_repo: other-legacy-companion\n", encoding="utf-8")
    root = home / "installation-cell" / "projects" / "harness"
    original: Callable[..., subprocess.CompletedProcess[str]] = bk._run_agent_worktrees

    def native(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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


def test_explicit_test_home_cannot_redirect_native_owner_into_another_home(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original: Callable[..., subprocess.CompletedProcess[str]] = bk._run_agent_worktrees

    def native(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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
def test_explicit_override_cannot_change_declared_roles_or_registered_paths(
    pair: PairFixture, overrides: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError, match="conflict"):
        _resolve(pair, **overrides)
    assert not pair[0].exists()


def test_unregistered_companion_is_not_fallback_discovery(pair: PairFixture) -> None:
    pair[-2]["repos"].pop()
    with pytest.raises(RuntimeError, match="exact canonical registration"):
        _resolve(pair)
    assert not pair[0].exists()


def test_knowledge_repository_cannot_be_the_executable_harness(pair: PairFixture) -> None:
    pair[-2]["repos"][0]["class"] = "knowledge"
    with pytest.raises(RuntimeError, match="declared lifecycle"):
        _resolve(pair)
    assert not pair[0].exists()


def test_old_native_without_tuple_reader_is_explicitly_unsupported(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported(_command: str, args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 2, "", "unrecognized control-plane command")

    monkeypatch.setattr(bk, "_run_agent_worktrees", unsupported)
    with pytest.raises(RuntimeError, match="unrecognized control-plane"):
        _resolve(pair)
    assert not pair[0].exists()


def test_pair_without_explicit_companion_does_not_become_a_binding(pair: PairFixture) -> None:
    pair[4]["paired"] = False
    pair[4]["knowledge"] = None
    with pytest.raises(RuntimeError, match="explicit repository pair"):
        _resolve(pair)
    assert not pair[0].exists()


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_native_platform_drives_pair_paths_and_normal_inspection(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    home, harness, knowledge, *_ = pair
    original: Callable[..., subprocess.CompletedProcess[str]] = bk._run_agent_worktrees
    pair[-2]["platform"] = platform
    for entry, path in zip(pair[-2]["repos"], (harness, knowledge)):
        entry["paths"] = {platform: str(path)}

    def native(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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
def test_unquoted_null_binding_is_unconfigured_not_a_repository_name(unset: str) -> None:
    assert not bk._check_existing_binding(f"knowledge_repo: {unset}\n", "knowledge")


def test_quoted_null_repository_name_is_not_treated_as_unconfigured() -> None:
    assert bk._check_existing_binding('knowledge_repo: "null"\n', "null")
    with pytest.raises(RuntimeError, match="conflicts"):
        bk._check_existing_binding('knowledge_repo: "null"\n', "knowledge")


@pytest.mark.parametrize("name", ["true", "NO", "on", "123", "2026-10-18"])
def test_typed_nonstring_binding_cannot_masquerade_as_a_matching_name(name: str) -> None:
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
def test_effective_conflict_or_malformed_binding_refuses_before_write(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch, value: dict[str, Any],
) -> None:
    original: Callable[..., subprocess.CompletedProcess[str]] = bk._run_agent_worktrees

    def native(command: str, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args == ["-p", "harness", "state-root", "--json"]:
            effective = {"source": "knowledge_repo", "requires_external": True, **value}
            return subprocess.CompletedProcess(args, 3, json.dumps(effective), "")
        return original(command, args, **kwargs)

    monkeypatch.setattr(bk, "_run_agent_worktrees", native)
    with pytest.raises(RuntimeError, match="effective harness knowledge binding"):
        _resolve(pair)
    assert not pair[0].exists()


@pytest.mark.parametrize("bare", [False, True, "worktree-config"])
def test_control_plane_git_root_accepts_normal_and_bare_anchors(tmp_path: Path, bare: bool | str) -> None:
    root = tmp_path / "anchor"
    command = ["git", "init", "--quiet"]
    if bare is True:
        command.append("--bare")
    result = subprocess.run(
        [*command, str(root)], capture_output=True, text=True,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
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
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
            )
            assert result.returncode == 0, result.stderr
    cp.validate_checkout(str(root))
    if not bare:
        nested = root / "nested"
        nested.mkdir()
        with pytest.raises(RuntimeError, match="not its Git root"):
            cp.validate_checkout(str(nested))


def test_control_plane_git_root_rejects_stale_non_git_directory(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not a Git repository"):
        cp.validate_checkout(str(tmp_path))


@pytest.mark.parametrize("name", ["123-knowledge", "123knowledge", "001-project", "1e3"])
def test_matching_plain_digit_prefixed_yaml_strings_are_preserved(name: str) -> None:
    assert bk._check_existing_binding(f"knowledge_repo: {name}\n", name)


@pytest.mark.parametrize(
    "text",
    [
        "{repo_name: harness, knowledge_repo: knowledge}\n",
        "---\nknowledge_repo: knowledge\n",
        "  repo_name: harness\n  knowledge_repo: knowledge\n",
        "'knowledge_repo': knowledge\n",
    ],
)
def test_unsupported_native_yaml_shapes_refuse_without_changing_configuration(
    pair: PairFixture, text: str,
) -> None:
    home = pair[0]
    config = home / ".harness" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(text, encoding="utf-8")
    before = config.read_bytes(), config.stat().st_mtime_ns
    with pytest.raises(RuntimeError, match="unsupported binding configuration shape"):
        _resolve(pair)
    with pytest.raises(RuntimeError, match="unsupported binding configuration shape"):
        bk._binding_yaml.write_binding(config, "harness", "knowledge", preserve=True)
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before


def test_differently_named_registrations_cannot_share_a_physical_checkout(pair: PairFixture) -> None:
    pair[-2]["repos"][1]["paths"] = dict(pair[-2]["repos"][0]["paths"])
    with pytest.raises(RuntimeError, match="same physical checkout"):
        _resolve(pair)
    assert not pair[0].exists()


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")


def test_symlinked_topology_uses_the_same_native_resolved_source(pair: PairFixture) -> None:
    link = pair[3].parent / "selected.yaml"
    _symlink(link, pair[3])
    selected = cp.resolve_binding(bk, str(link), "native-owner", home=pair[0])
    assert selected["source"] == str(pair[3])
    assert not pair[0].exists()


def test_symlinked_registration_retains_its_canonical_spelling(
    pair: PairFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = pair[2].parent / "registered-knowledge"
    _symlink(link, pair[2], directory=True)
    pair[-2]["repos"][1]["paths"] = {bk._current_platform_key(): str(link)}
    monkeypatch.setattr(
        bk, "knowledge_origin", lambda path: f"file:///example/{Path(path).resolve().name}"
    )
    selected = _resolve(pair)
    assert selected["knowledge_path"] == str(link)
    assert not pair[0].exists()


@pytest.mark.parametrize("layout", ["bare", "dot-git-bare"])
@pytest.mark.parametrize("matches", [False, True])
def test_bare_registration_checks_actual_origin_before_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str, matches: bool,
) -> None:
    root = tmp_path / "anchor"
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    init = ["git", "init", "--quiet"]
    if layout == "bare":
        init.append("--bare")
    result = subprocess.run([*init, str(root)], capture_output=True, text=True, creationflags=creationflags)
    assert result.returncode == 0, result.stderr
    git = ["git", "--git-dir", str(root)] if layout == "bare" else ["git", "-C", str(root)]
    if layout == "dot-git-bare":
        result = subprocess.run(
            [*git, "config", "core.bare", "true"], capture_output=True, text=True, creationflags=creationflags,
        )
        assert result.returncode == 0, result.stderr
    registered = "file:///expected"
    actual = registered if matches else "file:///different"
    result = subprocess.run(
        [*git, "remote", "add", "origin", actual], capture_output=True, text=True, creationflags=creationflags,
    )
    assert result.returncode == 0, result.stderr
    monkeypatch.setattr(bk, "knowledge_default_branch", lambda _path: "main")
    monkeypatch.setattr(
        bk, "_run_agent_worktrees",
        lambda _command, args, **_kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps({"repos": [{
                "name": "knowledge", "class": "knowledge",
                "paths": {bk._current_platform_key(): str(root)},
                "remote": registered, "default_branch": "main",
            }]}), "",
        ),
    )
    assert bk.knowledge_origin(str(root)) == actual
    ready = bk.inspect_registration("knowledge", str(root), "native-owner")
    assert ready["status"] == ("ready" if matches else "mismatch")
    if not matches:
        assert "registered remote" in ready["reason"]


@pytest.mark.parametrize(
    "text",
    [
        "knowledge_repo:\n  knowledge\n",
        "knowledge_repo: >-\n  knowledge\n",
        "knowledge_repo: |-\n  knowledge\n",
    ],
)
def test_multiline_binding_is_preserved_without_partial_replacement(tmp_path: Path, text: str) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("repo_name: harness\n" + text, encoding="utf-8")
    before = config.read_bytes(), config.stat().st_mtime_ns
    for preserve in (True, False):
        with pytest.raises(RuntimeError, match="multiline knowledge binding"):
            bk._binding_yaml.write_binding(config, "harness", "knowledge", preserve=preserve)
        assert (config.read_bytes(), config.stat().st_mtime_ns) == before


@pytest.mark.parametrize("existing_binding", [False, True])
def test_unrelated_indentless_sequence_is_supported_for_matching_and_first_touch(
    tmp_path: Path, existing_binding: bool
) -> None:
    config = tmp_path / "config.yaml"
    existing = "repo_name: harness\ncopilot_profiles:\n- name: cloud\n  model: example\n"
    if existing_binding:
        existing += "knowledge_repo: knowledge\n"
    config.write_text(existing, encoding="utf-8")
    before = config.read_bytes(), config.stat().st_mtime_ns
    bk._binding_yaml.write_binding(config, "harness", "knowledge", preserve=True)
    if existing_binding:
        assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    else:
        assert config.read_text() == "knowledge_repo: knowledge\n" + existing


def test_git_identity_and_branch_probes_ignore_inherited_repository_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["git", *args], capture_output=True, text=True, creationflags=creationflags)
        assert result.returncode == 0, result.stderr
        return result

    checkout, foreign, remote = tmp_path / "checkout", tmp_path / "foreign", tmp_path / "origin.git"
    for root in (checkout, foreign):
        git("init", "--quiet", "--initial-branch=main", str(root))
    (checkout / "README").write_text("synthetic\n")
    git("-C", str(checkout), "add", ".")
    git("-C", str(checkout), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "-m", "Synthetic source")
    git("clone", "--quiet", "--bare", str(checkout), str(remote))
    git("-C", str(checkout), "remote", "add", "origin", str(remote))
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign / "foreign.index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(foreign))
    monkeypatch.setenv("BINDING_UNRELATED_ENV", "preserved")
    cp.validate_checkout(str(checkout))
    assert bk.knowledge_origin(str(checkout)) == str(remote)
    assert bk.knowledge_default_branch(str(checkout)) == "main"
    cleaned = bk._binding_git.repository_env()
    assert "GIT_DIR" not in cleaned
    assert "GIT_CONFIG_VALUE_0" not in cleaned
    assert cleaned["BINDING_UNRELATED_ENV"] == "preserved"
