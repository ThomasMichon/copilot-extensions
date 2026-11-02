"""Caller-scoped, file-only cross-repository guard facts."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(path, *, bare=False):
    git = path if bare else path / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "objects").mkdir()
    (git / "refs").mkdir()
    return path


def _snapshot(reader, caller, registry, roots=()):
    return {
        "schema": "agent-worktrees.guarded-anchors", "version": 1,
        "source_root": reader.canonical(caller),
        "source_identity": reader.repository_identity(caller),
        "registry_root": reader.canonical(registry),
        "inventory_authority": "complete",
        "generation": "synthetic-generation",
        "roots": list(roots),
    }


def test_missing_map_is_unavailable_not_complete_empty(tmp_path):
    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    registry = tmp_path / "registry"
    assert reader.read_map(caller, registry).status == "missing"
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_snapshot(reader, caller, registry)))
    result = reader.read_map(caller, registry)
    assert result.status == "ready" and result.roots == []


def test_map_reader_and_bounded_writer_complete_during_publication(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    from agent_worktrees import tracking

    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    registry = tmp_path / "registry"
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    snapshot = _snapshot(reader, caller, registry)
    path.write_text(json.dumps(snapshot))
    original_open = reader._open_map
    original_replace = os.replace
    attempted = threading.Event()
    writes = []

    def observe_replace(source, destination):
        attempted.set()
        return original_replace(source, destination)

    def publish_while_open(selected):
        stream = original_open(selected)
        if selected == path:
            try:
                writes.append(workers.submit(
                    tracking._atomic_write, path,
                    json.dumps({**snapshot, "generation": "next"}),
                ))
                assert attempted.wait(timeout=5)
            except BaseException:
                stream.close()
                raise
        return stream

    monkeypatch.setattr(tracking.os, "replace", observe_replace)
    monkeypatch.setattr(reader, "_open_map", publish_while_open)
    with ThreadPoolExecutor(max_workers=1) as workers:
        result = reader.read_map(caller, registry)
        assert result.status == "ready", result.reason
        assert len(writes) == 1
        writes[0].result(timeout=5)
    assert json.loads(path.read_text())["generation"] == "next"


@pytest.mark.parametrize("damage", ["json", "scope", "version", "incomplete", "row"])
def test_unusable_map_fails_open_without_acquisition(tmp_path, monkeypatch, damage):
    guard = _load("cross_repo_guard")
    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    registry = tmp_path / ".agent-worktrees"
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    data = _snapshot(reader, caller, registry)
    if damage == "scope":
        data["source_root"] = reader.canonical(tmp_path / "foreign")
    elif damage == "version":
        data["version"] = 99
    elif damage == "incomplete":
        data["inventory_authority"] = "partial"
    elif damage == "row":
        data["roots"] = [{"name": "target"}]
    path.write_text("{" if damage == "json" else json.dumps(data))
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("hook spawned"))
    assert guard.decide({
        "toolName": "edit", "cwd": str(caller), "toolArgs": {"path": "new.txt"},
    }, env={}, home=tmp_path) is None
    assert reader.read_map(caller, registry).status == "unusable"


def test_repository_identity_is_nearest_and_resolves_common_directory(tmp_path):
    reader = _load("guarded_anchor_map")
    anchor = _repo(tmp_path / "anchor", bare=True)
    linked = tmp_path / "linked"
    linked.mkdir()
    gitdir = anchor / "worktrees" / "branch"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/branch\n")
    (gitdir / "commondir").write_text("../..\n")
    (linked / ".git").write_text(f"gitdir: {gitdir}\n")
    identity = reader.repository_identity(linked / "not-yet-created" / "new.txt")
    assert identity["root"] == reader.canonical(linked)
    assert identity["common_dir"] == reader.canonical(anchor)
    nested = _repo(linked / "nested")
    assert reader.repository_identity(nested / "file")["root"] == reader.canonical(nested)
    assert reader.repository_identity(anchor)["common_dir"] == reader.canonical(anchor)


def test_native_and_resident_share_file_only_decisions_and_live_controls(tmp_path, monkeypatch):
    from agent_worktrees import __main__ as main
    from agent_worktrees import related, registry_paths, repos

    guard = _load("cross_repo_guard")
    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    target = _repo(tmp_path / "target")
    registry = tmp_path / ".agent-worktrees"
    row = {
        "name": "target", "delegate": "agent-bridge", "path": str(target),
        "locus": {"preferred": "local"}, "identity": reader.repository_identity(target),
    }
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_snapshot(reader, caller, registry, [row])))
    payload = {"toolName": "edit", "cwd": str(caller), "toolArgs": {"path": str(target / "new")}}
    monkeypatch.setattr(registry_paths, "registry_root", lambda *a: registry)
    monkeypatch.setattr(repos, "read_registry", lambda **k: pytest.fail("hook read registry"))
    monkeypatch.setattr(related, "installed_plugin_related_anchors", lambda: pytest.fail("hook read plugins"))
    monkeypatch.setattr(related, "list_related_grafted", lambda *a: pytest.fail("hook read topology"))
    policy = main._ResidentHookPolicy(SimpleNamespace(
        _merge_pre_decisions=lambda before, decision: {**before, **decision},
    ))
    monkeypatch.setattr(policy, "_module", lambda name: guard if name == "cross_repo_guard.py" else None)
    assert guard.decide(payload, env={}, home=tmp_path)["permissionDecision"] == "deny"
    assert policy.guarded_roots(str(caller)) == [row]
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert policy.pre(payload)["permissionDecision"] == "deny"
    monkeypatch.setattr(main, "_activate_project_for_path", lambda *a, **k: pytest.fail("pre activated project"))
    assert main._resident_hook_decision(
        "preToolUse", payload, segment_cache=None, policy=policy,
    )["permissionDecision"] == "deny"
    monkeypatch.setenv("CROSS_REPO_GUARD", "off")
    assert policy.pre(payload) == {}
    monkeypatch.delenv("CROSS_REPO_GUARD")
    (registry / "allow-edits.json").write_text(json.dumps({
        "grants": {"target": {"expires_at_ms": 1e15}},
    }))
    assert policy.pre(payload) == {}
    (registry / "allow-edits.json").write_text(json.dumps({
        "grants": {"target": {"expires_at_ms": 1}},
    }))
    assert policy.pre(payload)["permissionDecision"] == "deny"


def test_nested_repository_does_not_inherit_parent_guard(tmp_path):
    reader = _load("guarded_anchor_map")
    guard = _load("cross_repo_guard")
    target = _repo(tmp_path / "target")
    nested = _repo(target / "nested")
    row = {
        "name": "target", "delegate": "agent-bridge", "path": str(target),
        "locus": None, "identity": reader.repository_identity(target),
    }
    for path, denied in [(target / "new", True), (nested / "new", False)]:
        result = guard.decide({
            "toolName": "edit", "cwd": str(tmp_path), "toolArgs": {"path": str(path)},
        }, home=tmp_path, env={}, guarded_roots=[row])
        assert bool(result) is denied


def test_map_scope_does_not_borrow_other_installation(tmp_path):
    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    first, second = tmp_path / "first", tmp_path / "second"
    path = reader.map_path(caller, first)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_snapshot(reader, caller, first)))
    assert reader.read_map(caller, second).status == "missing"
    foreign = reader.map_path(caller, second)
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(path.read_bytes())
    assert reader.read_map(caller, second).status == "unusable"


@pytest.fixture
def producer(tmp_path, monkeypatch):
    from dropin_registry import ScanAuthority
    from agent_worktrees import config, guarded_anchors as producer, related, related_acquisition, repos
    from agent_worktrees.acquisition_report import AcquisitionReport, SourceObservation
    from agent_worktrees.repos_registry_transaction import revision

    reader = _load("guarded_anchor_map")
    callers = [_repo(tmp_path / name) for name in ("first", "second")]
    target = _repo(tmp_path / "target")
    root = tmp_path / "registry"
    entries = {name: repos.RepoEntry(name=name) for name in ("first", "second", "target")}
    paths = dict(zip(entries, [*callers, target]))
    registry = repos.ReposRegistry(repos=entries, _baseline=(
        str((root / "repos.yaml").resolve()), revision(None),
    ))
    monkeypatch.setattr(producer, "read_registry", lambda *a, **k: registry)
    monkeypatch.setattr(repos, "registered_entry_root", lambda entry: paths[entry.name])
    configurations = []
    monkeypatch.setattr(config, "load_config", lambda *a, **k: configurations.append(k["project"]) or k)
    monkeypatch.setattr(config, "project_dir", lambda name: root / name)
    monkeypatch.setattr(producer, "resolve_active_plugins", lambda **k: k)
    observation = SourceObservation("synthetic", "related-file", ScanAuthority.COMPLETE)

    def sources(caller, **kwargs):
        assert kwargs["activation_report"]["project_root"] == Path(caller)
        assert kwargs["configuration"]["project"] == Path(caller).name
        return AcquisitionReport("sources", [caller], (observation,))

    monkeypatch.setattr(related_acquisition, "_related_config_source_anchors_report", sources)
    related_values = {"first": {"target": related.RelatedEntry(
        name="target", delegate="agent-bridge",
    )}, "second": {}}
    monkeypatch.setattr(related_acquisition, "read_related_grafted_report", lambda sources: AcquisitionReport(
        "related", related.RelatedConfig(related=related_values[Path(sources[0]).name]), (observation,),
    ))
    return SimpleNamespace(
        module=producer, root=root, callers=callers, target=target,
        reader=reader, related=related_values, configurations=configurations,
    )


def test_producer_keeps_directional_callers_and_withdraws_removed_policy(producer):
    p = producer
    assert p.module.refresh(p.callers, registry_root=p.root)["published"] == 2
    assert p.reader.read_map(p.callers[0], p.root).roots[0]["name"] == "target"
    assert p.reader.read_map(p.callers[1], p.root).roots == []
    p.related["first"].clear()
    p.module.refresh(registry_root=p.root)
    assert p.reader.read_map(p.callers[0], p.root).roots == []
    data = json.loads(p.reader.map_path(p.callers[0], p.root).read_bytes())
    assert data["qualification"] == "managed-observation"
    assert "source_revision" not in data  # No invented stable owner-input proof.


def test_preparation_does_not_recompute_other_prepared_callers(producer, monkeypatch):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda: p.root)
    p.module.refresh(p.callers, registry_root=p.root)
    other_path = p.reader.map_path(p.callers[1], p.root)
    other = other_path.read_bytes()
    p.configurations.clear()
    p.related["first"].clear()
    assert p.module.prepare(p.callers[0]) == ""
    assert p.configurations == ["first"]
    assert p.reader.read_map(p.callers[0], p.root).roots == []
    assert other_path.read_bytes() == other


def test_failed_producer_withdraws_old_view_and_reports_failure(producer, monkeypatch):
    p = producer
    p.module.refresh(p.callers, registry_root=p.root)

    def failed(*args):
        raise ValueError("synthetic acquisition failure")

    monkeypatch.setattr(p.module, "build_snapshot", failed)
    with pytest.raises(p.module.GuardPublicationError, match="views withdrawn"):
        p.module.refresh(registry_root=p.root)
    assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)


def test_managed_write_withdraws_before_mutation_and_retains_failed_scope(producer, monkeypatch):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda: p.root)
    p.module.refresh(p.callers, registry_root=p.root)

    def failed_write():
        assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)
        raise ValueError("synthetic partially completed mutation")

    with pytest.raises(ValueError, match="partially completed"):
        p.module.managed_write(failed_write)
    assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)
    assert p.module.refresh(registry_root=p.root)["published"] == 2


def test_managed_write_reports_completed_source_before_publication_failure(producer, monkeypatch):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda: p.root)
    p.module.refresh(p.callers, registry_root=p.root)
    effects = []

    def failed_publication(*args):
        raise p.module.GuardPublicationError("synthetic publisher failure")

    monkeypatch.setattr(p.module, "_refresh_locked", failed_publication)
    with pytest.raises(p.module.GuardPublicationError, match="source write completed"):
        p.module.managed_write(lambda: effects.append("source written"))
    assert effects == ["source written"]
    assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)


def test_indeterminate_acquisition_is_not_a_complete_empty_map(producer, monkeypatch):
    from dropin_registry import ScanAuthority
    from agent_worktrees import related_acquisition
    from agent_worktrees.acquisition_report import AcquisitionReport, SourceObservation
    from agent_worktrees.related import RelatedConfig

    p = producer
    monkeypatch.setattr(related_acquisition, "read_related_grafted_report", lambda *a: AcquisitionReport(
        "related", RelatedConfig(), (
            SourceObservation("synthetic", "related-file", ScanAuthority.INDETERMINATE),
        ),
    ))
    with pytest.raises(p.module.GuardPublicationError):
        p.module.refresh(p.callers, registry_root=p.root)
    assert p.reader.read_map(p.callers[0], p.root).status == "unusable"


def test_managed_related_mutations_publish_after_write(tmp_path, monkeypatch):
    from agent_worktrees import guarded_anchors as producer, registry_paths, related, repos

    caller = _repo(tmp_path / "caller")
    registry = tmp_path / "registry"
    monkeypatch.setattr(registry_paths, "registry_root", lambda *a: registry)
    observed = []
    monkeypatch.setattr(producer, "_refresh_locked", lambda root, callers: observed.append(
        (related.read_related(caller), callers),
    ))
    related.write_related(caller, related.RelatedConfig(
        related={"target": related.RelatedEntry(name="target", delegate="agent-bridge")},
    ))
    related.set_primary(caller, "target")
    assert related.remove_related(caller, "target")
    assert list(observed[0][0].related) == ["target"]
    assert observed[1][0].primary == "target"
    assert not observed[2][0].related
    assert all(row[1] == [_load("guarded_anchor_map").canonical(caller)] for row in observed)
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry / "repos.yaml")
    repos.write_registry(repos.ReposRegistry())
    assert observed[-1][1] == []


def test_plugin_related_mutation_refreshes_views_without_preparing_plugin_checkout(tmp_path, monkeypatch):
    from agent_worktrees import guarded_anchors as producer, registry_paths, related

    caller = _repo(tmp_path / "caller")
    plugin = caller / ".ai" / "sample"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"sample"}')
    monkeypatch.setattr(registry_paths, "registry_root", lambda *a: tmp_path / "registry")
    observed = []
    monkeypatch.setattr(producer, "_refresh_locked", lambda root, callers: observed.append(callers))
    related.write_related(plugin, related.RelatedConfig())
    assert observed == [[]]


def test_explicit_refresh_command_and_strict_argument_rejection(producer, monkeypatch, capsys):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda: p.root)
    assert p.module.main(["--repo", str(p.callers[0]), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["published"] == 1
    with pytest.raises(SystemExit) as exc:
        p.module.main(["--unknown"])
    assert exc.value.code == 2


def test_native_entry_cold_warm_missing_map_and_deployed_reader_closure(tmp_path, monkeypatch):
    from agent_worktrees import installer

    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    target = _repo(tmp_path / "target")
    home = tmp_path / "home"
    registry = home / ".agent-worktrees"
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(installer, "bin_dir", lambda: scripts)
    monkeypatch.setattr(installer, "install_dir", lambda: tmp_path / "installation")
    assert installer.deploy_wrappers(Path(installer.__file__).resolve().parents[4])
    path.write_text(json.dumps(_snapshot(reader, caller, registry, [{
        "name": "target", "delegate": "agent-bridge", "path": str(target),
        "locus": None, "identity": reader.repository_identity(target),
    }])))
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "CROSS_REPO_GUARD", "CROSS_REPO_GUARD_MODE"):
        environment.pop(key, None)
    payload = {"toolName": "edit", "cwd": str(caller), "toolArgs": {"path": str(target / "new")}}
    for exists, reader_available in ((True, True), (True, True), (False, True), (False, False)):
        if not exists:
            path.unlink(missing_ok=True)
        if not reader_available:
            (scripts / "guarded_anchor_map.py").unlink()
        completed = subprocess.run(
            [sys.executable, "-I", str(scripts / "cross_repo_guard.py")],
            input=json.dumps(payload), text=True, capture_output=True,
            cwd=caller, env=environment, timeout=10,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        assert completed.returncode == 0
        if exists:
            assert json.loads(completed.stdout)["permissionDecision"] == "deny"
        else:
            assert completed.stdout == "" and "fail-open" in completed.stderr
    assert not (target / "new").exists()


def test_concurrent_publication_exposes_only_whole_maps(producer):
    from concurrent.futures import ThreadPoolExecutor

    p = producer
    p.module.refresh(p.callers, registry_root=p.root)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(p.module.refresh, registry_root=p.root)
            for _ in range(2)
        ]
        for _ in range(20):
            for caller in p.callers:
                result = p.reader.read_map(caller, p.root)
                assert result.status in {"ready", "missing", "unusable"}
                if result.status == "ready" and caller == p.callers[0]:
                    assert len(result.roots) == 1
        assert all(future.result()["published"] == 2 for future in futures)
