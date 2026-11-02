"""Caller-scoped, file-only cross-repository guard facts."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import stat
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


def test_git_marker_probe_failure_cannot_fall_back_to_parent_repository(tmp_path, monkeypatch):
    reader = _load("guarded_anchor_map")
    parent = _repo(tmp_path / "parent")
    nested = _repo(parent / "nested")
    marker = nested / ".git"
    original_stat, original_lstat = Path.stat, Path.lstat

    def unavailable(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("synthetic unreadable Git marker")
        return original_stat(path, *args, **kwargs)

    def unavailable_lstat(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("synthetic unreadable Git marker")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unavailable)
    monkeypatch.setattr(Path, "lstat", unavailable_lstat)
    assert reader.repository_identity(nested / "new.txt") is None
    monkeypatch.setattr(Path, "stat", original_stat)
    monkeypatch.setattr(Path, "lstat", original_lstat)
    assert reader.repository_identity(nested / "new.txt")["root"] == reader.canonical(nested)


@pytest.mark.parametrize("missing", ["objects", "refs"])
def test_repository_identity_requires_common_git_metadata(tmp_path, missing):
    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    (caller / ".git" / missing).rmdir()
    assert reader.repository_identity(caller / "new.txt") is None
    (caller / ".git" / missing).mkdir()
    assert reader.repository_identity(caller / "new.txt")["root"] == reader.canonical(caller)


def test_map_validation_does_not_probe_inventory_paths(tmp_path, monkeypatch):
    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    registry = tmp_path / "registry"
    roots = []
    for index in range(20):
        target = os.path.normcase(os.path.abspath(tmp_path / f"unrelated-{index}"))
        roots.append({
            "name": f"target-{index}", "delegate": "agent-bridge", "path": target,
            "registered_path": target + "-alias", "locus": None,
            "identity": {"root": target, "git_dir": target + os.sep + ".git", "common_dir": target + os.sep + ".git"},
        })
    path = reader.map_path(caller, registry)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_snapshot(reader, caller, registry, roots)))
    original = os.path.realpath

    def no_inventory_probe(value, *args, **kwargs):
        assert not any(os.fspath(value).startswith(row["path"]) for row in roots), "inventory filesystem traversal"
        return original(value, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", no_inventory_probe)
    assert reader.read_map(caller, registry).status == "ready"


@pytest.mark.parametrize("damage", ["unreadable", "nonregular"])
def test_linked_common_directory_probe_failure_cannot_publish_local_git_identity(tmp_path, monkeypatch, damage):
    reader = _load("guarded_anchor_map")
    anchor = _repo(tmp_path / "anchor", bare=True)
    gitdir = anchor / "worktrees" / "linked"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    common = gitdir / "commondir"
    common.write_text("../..\n")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {gitdir}\n")
    original_stat, original_lstat = Path.stat, Path.lstat

    def unavailable(path, *args, **kwargs):
        if path == common:
            raise PermissionError("synthetic unreadable common-directory marker")
        return original_stat(path, *args, **kwargs)

    def unavailable_lstat(path, *args, **kwargs):
        if path == common:
            raise PermissionError("synthetic unreadable common-directory marker")
        return original_lstat(path, *args, **kwargs)

    if damage == "unreadable":
        monkeypatch.setattr(Path, "stat", unavailable)
        monkeypatch.setattr(Path, "lstat", unavailable_lstat)
    else:
        common.unlink()
        common.mkdir()
    assert reader.repository_identity(linked / "new.txt") is None
    monkeypatch.setattr(Path, "stat", original_stat)
    monkeypatch.setattr(Path, "lstat", original_lstat)
    if damage == "nonregular":
        common.rmdir()
        common.write_text("../..\n")
    assert reader.repository_identity(linked / "new.txt")["common_dir"] == reader.canonical(anchor)


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


def test_git_pointer_path_spaces_are_preserved(tmp_path):
    reader = _load("guarded_anchor_map")
    base = Path("\\\\?\\" + str(tmp_path)) if os.name == "nt" else tmp_path
    anchor = _repo(base / "anchor with suffix ", bare=True)
    linked = base / "linked"
    linked.mkdir()
    gitdir = anchor / "worktrees" / "branch with suffix "
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/branch\n")
    (gitdir / "commondir").write_text(str(anchor) + "\n")
    marker = linked / ".git"
    marker.write_text(f"gitdir: {gitdir}\n")
    assert reader._text(marker) == f"gitdir: {gitdir}"
    identity = reader.repository_identity(linked / "new")
    assert identity is not None
    assert identity["git_dir"] == reader.canonical(gitdir)
    assert identity["common_dir"] == reader.canonical(anchor)


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
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
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


@pytest.mark.parametrize("tool,args", [
    ("edit", {"path": "new.txt"}),
    ("powershell", {"command": "git commit -m first; git commit -m second"}),
    ("powershell", {"command": "git -C . commit -m first; git -C . commit -m second"}),
])
def test_target_identity_resolution_is_event_local_not_inventory_sized(tmp_path, monkeypatch, tool, args):
    guard = _load("cross_repo_guard")
    target = _repo(tmp_path / "guarded")
    outside = _repo(tmp_path / "outside")
    row = {
        "name": "target", "delegate": "agent-bridge", "path": str(target),
        "identity": guard._guard_map.repository_identity(target),
    }
    original = guard._guard_map.repository_identity
    observed = []

    def observe(path):
        observed.append(path)
        return original(path)

    monkeypatch.setattr(guard._guard_map, "repository_identity", observe)
    roots = [{**row, "name": f"target-{index}"} for index in range(20)]
    for event in range(2):
        assert guard.evaluate(tool, args, str(outside), roots) is None
        assert len(observed) == event + 1


@pytest.mark.parametrize("kind", ["anchor", "linked", "nested"])
@pytest.mark.parametrize("relative", [False, True])
def test_literal_git_directory_uses_repository_identity(tmp_path, kind, relative):
    guard = _load("cross_repo_guard")
    anchor = _repo(tmp_path / "guarded space")
    target = anchor
    if kind == "nested":
        target = _repo(anchor / "nested space")
    elif kind == "linked":
        target = tmp_path / "linked space"
        target.mkdir()
        gitdir = anchor / ".git" / "worktrees" / "linked"
        gitdir.mkdir(parents=True)
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
        (gitdir / "commondir").write_text("../..\n")
        (target / ".git").write_text(f"gitdir: {gitdir}\n")
    row = {
        "name": "target", "delegate": "agent-bridge", "path": str(anchor),
        "identity": guard._guard_map.repository_identity(anchor),
    }
    raw = os.path.relpath(target, tmp_path) if relative else str(target)

    result = guard.decide({
        "toolName": "powershell", "cwd": str(tmp_path),
        "toolArgs": {"command": f"git -C '{raw}' commit -m new"},
    }, home=tmp_path, env={}, guarded_roots=[row])

    assert bool(result) is (kind != "nested")


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


def test_legacy_agent_home_selects_map_and_grants_without_borrowing_real_home(tmp_path):
    guard = _load("cross_repo_guard")
    reader = _load("guarded_anchor_map")
    caller = _repo(tmp_path / "caller")
    target = _repo(tmp_path / "target")
    real_home, sandbox = tmp_path / "real", tmp_path / "sandbox"
    row = {
        "name": "target", "delegate": "agent-bridge", "path": str(target),
        "identity": reader.repository_identity(target), "locus": None,
    }
    for home in (real_home, sandbox):
        registry = home / ".agent-worktrees"
        path = reader.map_path(caller, registry)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(_snapshot(reader, caller, registry, [row])))
    payload = {"toolName": "edit", "cwd": str(caller), "toolArgs": {"path": str(target / "new")}}
    environment = {"AGENT_HOME": str(sandbox)}
    grant = json.dumps({"grants": {"target": {"expires_at_ms": 1e15}}})
    (real_home / ".agent-worktrees" / "allow-edits.json").write_text(grant)
    assert guard.decide(payload, env=environment, home=real_home)["permissionDecision"] == "deny"
    (sandbox / ".agent-worktrees" / "allow-edits.json").write_text(grant)
    assert guard.decide(payload, env=environment, home=real_home) is None
    (sandbox / ".agent-worktrees" / "allow-edits.json").unlink()
    reader.map_path(caller, sandbox / ".agent-worktrees").unlink()
    assert guard.decide(payload, env=environment, home=real_home) is None


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
    paths = dict(zip(("first", "second", "target"), [*callers, target]))
    entries = {
        name: repos.RepoEntry(name=name, paths={repos._current_platform(): str(path)})
        for name, path in paths.items()
    }
    registry = repos.ReposRegistry(repos=entries, _baseline=(
        str((root / "repos.yaml").resolve()), revision(None),
    ))
    monkeypatch.setattr(producer, "read_registry", lambda *a, **k: registry)
    registered_entry_root = repos.registered_entry_root
    monkeypatch.setattr(repos, "registered_entry_root", lambda entry: paths[entry.name])
    configurations = []
    monkeypatch.setattr(config, "load_config", lambda *a, **k: configurations.append(k["project"]) or k)
    monkeypatch.setattr(config, "project_dir", lambda name: root / name)
    monkeypatch.setattr(producer, "resolve_active_plugins", lambda **k: k)
    observation = SourceObservation("synthetic", "related-file", ScanAuthority.COMPLETE)

    def sources(caller, **kwargs):
        assert kwargs["activation_report"]["project_root"] == Path(caller)
        assert kwargs["activation_report"]["home"] == config._home()
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
        reader=reader, related=related_values, configurations=configurations, registry=registry,
        registered_entry_root=registered_entry_root,
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
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    p.module.refresh(p.callers, registry_root=p.root)
    other_path = p.reader.map_path(p.callers[1], p.root)
    other = other_path.read_bytes()
    p.configurations.clear()
    p.related["first"].clear()
    assert p.module.prepare(p.callers[0]) == ""
    assert p.configurations == ["first"]
    assert p.reader.read_map(p.callers[0], p.root).roots == []
    assert other_path.read_bytes() == other


def test_registered_alias_and_physical_shell_targets_remain_guarded(producer, monkeypatch):
    import shutil

    from agent_worktrees import repos

    p = producer
    alias = p.target.parent / "registered-alias"
    if os.name == "nt":
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            pytest.skip("native junction creator unavailable")
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:GUARD_LINK -Target $env:GUARD_TARGET -ErrorAction Stop | Out-Null"],
            env={**os.environ, "GUARD_LINK": str(alias), "GUARD_TARGET": str(p.target)},
            capture_output=True, text=True, timeout=15, creationflags=0x08000000,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        alias.symlink_to(p.target, target_is_directory=True)
    original_root = repos.registered_entry_root
    p.registry.repos["target"].paths[repos._current_platform()] = str(alias)
    monkeypatch.setattr(repos, "registered_entry_root", lambda entry: (
        p.registered_entry_root(entry) if entry.name == "target" else original_root(entry)
    ))
    p.module.refresh(p.callers, registry_root=p.root)
    rows = p.reader.read_map(p.callers[0], p.root).roots
    assert rows[0]["path"] == p.reader.canonical(p.target)
    assert rows[0]["registered_path"] == os.path.abspath(alias)
    assert rows[0]["identity"]["root"] == p.reader.canonical(p.target)
    guard = _load("cross_repo_guard")
    for root in (alias, p.target):
        for command in (
            f"Set-Content -Path '{root / 'new.txt'}' -Value new",
            f"echo new > '{root / 'new.txt'}'",
            f"git -C '{root}' commit -m new",
        ):
            hit = guard.evaluate("powershell", {"command": command}, str(p.callers[0]), rows)
            assert hit is not None and hit["name"] == "target", command
        assert guard.evaluate(
            "powershell", {"command": f"Get-Content '{root / 'existing.txt'}'"},
            str(p.callers[0]), rows,
        ) is None


def test_failed_producer_withdraws_old_view_and_reports_failure(producer, monkeypatch):
    p = producer
    p.module.refresh(p.callers, registry_root=p.root)

    def failed(*args):
        raise ValueError("synthetic acquisition failure")

    monkeypatch.setattr(p.module, "build_snapshot", failed)
    with pytest.raises(p.module.GuardPublicationError, match="views withdrawn"):
        p.module.refresh(registry_root=p.root)
    assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)


@pytest.mark.parametrize("disabled", [False, True])
def test_resident_cross_repo_policy_skips_git_for_irrelevant_or_disabled_events(tmp_path, monkeypatch, disabled):
    from agent_worktrees.__main__ import _ResidentHookPolicy

    guard = _load("cross_repo_guard")
    policy = _ResidentHookPolicy(None, ttl=30)
    monkeypatch.setattr(policy, "_module", lambda name: guard if name == "cross_repo_guard.py" else None)
    monkeypatch.setattr(guard._guard_map, "repository_identity", lambda *args: pytest.fail("unrelated Git traversal"))
    if disabled:
        monkeypatch.setenv("CROSS_REPO_GUARD", "off")
    else:
        monkeypatch.delenv("CROSS_REPO_GUARD", raising=False)
        monkeypatch.delenv("CROSS_REPO_GUARD_MODE", raising=False)
    command = "Set-Content new.txt -Value new" if disabled else "Get-Content existing.txt"
    assert policy.pre({
        "toolName": "powershell", "toolArgs": {"command": command}, "cwd": str(tmp_path),
    }) == {}


def test_binding_load_failure_is_observed_despite_an_outer_warm_config_cache(producer, monkeypatch):
    from agent_worktrees import config, config_acquisition
    from agent_worktrees.config_cache import ConfigCacheSession, memoize_in_scope

    p = producer
    binding = p.root.parent / "binding.yaml"
    binding.write_text("knowledge_repo: knowledge\n")
    original = config.load_config

    def compute(*args, **kwargs):
        config_acquisition.full_load_started()
        config._load_yaml_safe(binding)
        return original(*args, **kwargs)

    monkeypatch.setattr(config, "load_config", lambda *args, **kwargs: (
        memoize_in_scope(compute, *args, **kwargs)
    ))
    with ConfigCacheSession(ttl=None).scope():
        config.load_config(
            p.root / "first" / "config.yaml", project="first",
            include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
        p.module.refresh(p.callers, registry_root=p.root)
        binding.write_text("knowledge_repo: [\n")
        with pytest.raises(p.module.GuardPublicationError, match="configuration acquisition unavailable"):
            p.module.refresh(registry_root=p.root)
        assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)
        binding.write_text("knowledge_repo: knowledge\n")
        assert p.module.refresh(registry_root=p.root)["published"] == 2


def test_activation_acquisition_uses_the_configuration_agent_home(producer, monkeypatch):
    p = producer
    home = p.root.parent / "selected-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    selected = []

    def activation(**kwargs):
        selected.append(kwargs["home"])
        return kwargs

    monkeypatch.setattr(p.module, "resolve_active_plugins", activation)
    assert p.module.refresh(p.callers, registry_root=p.root)["published"] == 2
    assert selected == [home, home]


@pytest.mark.parametrize("state", ["absent", "appeared", "unreadable"])
def test_optional_presence_observation_is_qualified_before_publication(producer, monkeypatch, state):
    from agent_worktrees import config, config_acquisition

    p = producer
    source = p.root.parent / "optional.yaml"
    original = config.load_config
    original_exists = Path.exists
    original_stat = os.stat
    if state == "unreadable":
        monkeypatch.setattr(Path, "exists", lambda path, *args, **kwargs: (
            False if path == source else original_exists(path, *args, **kwargs)
        ))

        def unreadable(path, *args, **kwargs):
            if isinstance(path, (str, Path)) and Path(path) == source:
                raise PermissionError("synthetic inaccessible optional source")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", unreadable)

    def load(*args, **kwargs):
        present = config_acquisition.source_exists(source, "optional-source")
        if state == "appeared" and not present:
            source.write_text("value: changed\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(config, "load_config", load)
    if state == "absent":
        assert p.module.refresh(p.callers, registry_root=p.root)["published"] == 2
    else:
        with pytest.raises(p.module.GuardPublicationError, match="configuration acquisition unavailable"):
            p.module.refresh(p.callers, registry_root=p.root)
        assert p.reader.read_map(p.callers[0], p.root).status == "unusable"


def test_unreadable_declared_guarded_checkout_is_not_complete_empty(producer, monkeypatch):
    from agent_worktrees import repos

    p = producer
    p.module.refresh(p.callers, registry_root=p.root)
    original_root = repos.registered_entry_root
    original_local = repos.RepoEntry.local_path
    monkeypatch.setattr(repos.RepoEntry, "local_path", lambda entry: (
        str(p.target) if entry.name == "target" else original_local(entry)
    ))

    def unavailable(entry):
        if entry.name == "target":
            raise repos.RepositoryResolutionError("synthetic unreadable checkout") from PermissionError()
        return original_root(entry)

    monkeypatch.setattr(repos, "registered_entry_root", unavailable)
    with pytest.raises(p.module.GuardPublicationError, match="guarded checkout .* unavailable"):
        p.module.refresh(registry_root=p.root)
    assert p.reader.read_map(p.callers[0], p.root).status == "unusable"
    assert p.reader.read_map(p.callers[1], p.root).status == "ready"
    monkeypatch.setattr(repos, "registered_entry_root", original_root)
    assert p.module.refresh(registry_root=p.root)["published"] == 2
    assert p.reader.read_map(p.callers[0], p.root).roots[0]["name"] == "target"


@pytest.mark.parametrize("consumer", ["hook", "withdraw", "refresh"])
def test_nonregular_map_is_rejected_by_every_reader(producer, monkeypatch, consumer):
    p = producer
    p.module.refresh(p.callers, registry_root=p.root)
    path = p.reader.map_path(p.callers[0], p.root)
    original_lstat = Path.lstat

    def nonregular(selected, *args, **kwargs):
        if selected == path:
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0)
        return original_lstat(selected, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", nonregular)
    monkeypatch.setattr(p.module, "_reader", lambda: p.reader)
    if consumer == "hook":
        assert p.reader.read_map(p.callers[0], p.root).status == "unusable"
    elif consumer == "withdraw":
        p.module._withdraw_views(path.parent, paths=[path])
        data = json.loads(path.read_text())
        assert data["inventory_authority"] == "unavailable"
        assert "source_root" not in data
    else:
        assert p.module.refresh(registry_root=p.root)["published"] == 1


def test_missing_reader_refuses_source_mutation_without_destroying_retry_scopes(producer, monkeypatch):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    p.module.refresh(p.callers)
    paths = [p.reader.map_path(caller, p.root) for caller in p.callers]
    before = [path.read_bytes() for path in paths]
    effects = []

    def missing():
        raise p.module.GuardPublicationError("guard-map reader is unavailable")

    monkeypatch.setattr(p.module, "_reader", missing)
    with pytest.raises(p.module.GuardPublicationError, match="reader is unavailable"):
        p.module.managed_write(lambda: effects.append("source written"))
    assert effects == []
    assert [path.read_bytes() for path in paths] == before


@pytest.mark.parametrize("operation", ["refresh", "managed_write"])
def test_failed_map_listing_cannot_mutate_sources_or_report_empty_success(producer, monkeypatch, operation):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *args: p.root)
    p.module.refresh(p.callers)
    directory = p.root / "guarded-anchors"
    paths = [p.reader.map_path(caller, p.root) for caller in p.callers]
    before = [path.read_bytes() for path in paths]
    original = os.scandir
    effects = []

    def unavailable(path):
        if isinstance(path, (str, Path)) and Path(path) == directory:
            raise PermissionError("synthetic unreadable map inventory")
        return original(path)

    monkeypatch.setattr(p.module.os, "scandir", unavailable)
    with pytest.raises(PermissionError, match="unreadable map inventory"):
        if operation == "refresh":
            p.module.refresh()
        else:
            p.module.managed_write(lambda: effects.append("source written"))
    assert effects == []
    assert [path.read_bytes() for path in paths] == before


def test_indirected_map_store_cannot_read_or_mutate_another_store(producer, monkeypatch):
    import shutil

    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    directory = p.root / "guarded-anchors"
    directory.parent.mkdir(parents=True)
    foreign = p.root.parent / "foreign" / "guarded-anchors"
    foreign.mkdir(parents=True)
    path = foreign / p.reader.map_path(p.callers[0], p.root).name
    path.write_text(json.dumps(_snapshot(p.reader, p.callers[0], p.root)))
    before = path.read_bytes()
    if os.name == "nt":
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            pytest.skip("native junction creator unavailable")
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:GUARD_LINK -Target $env:GUARD_TARGET -ErrorAction Stop | Out-Null"],
            env={**os.environ, "GUARD_LINK": str(directory), "GUARD_TARGET": str(foreign)},
            capture_output=True, text=True, timeout=15, creationflags=0x08000000,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        directory.symlink_to(foreign, target_is_directory=True)
    assert p.reader.read_map(p.callers[0], p.root).status == "unusable"
    with pytest.raises(ValueError, match="indirected"):
        p.module.refresh()
    effects = []
    with pytest.raises(ValueError, match="indirected"):
        p.module.managed_write(lambda: effects.append("source written"))
    assert effects == []
    assert path.read_bytes() == before
    assert list(foreign.iterdir()) == [path]


@pytest.mark.parametrize("operation,failed_read", [
    ("refresh", 1), ("refresh", 2),
    ("prepare", 1), ("prepare", 2),
    ("managed_write", 1), ("managed_write", 2), ("managed_write", 3),
])
def test_transient_map_read_failure_preserves_retry_scope(producer, monkeypatch, operation, failed_read):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *args: p.root)
    p.module.refresh(p.callers)
    path = p.reader.map_path(p.callers[0], p.root)
    original = p.module._view_data
    reads = 0
    effects = []

    def unavailable(selected, reader):
        nonlocal reads
        if selected == path:
            reads += 1
            if reads == failed_read:
                raise PermissionError("synthetic transient map read failure")
        return original(selected, reader)

    monkeypatch.setattr(p.module, "_view_data", unavailable)
    if operation == "prepare":
        assert "transient map read failure" in p.module.prepare(p.callers[0])
    else:
        with pytest.raises((PermissionError, p.module.GuardPublicationError), match="transient map read failure"):
            if operation == "refresh":
                p.module.refresh()
            else:
                p.module.managed_write(lambda: effects.append("source written"))
    assert reads == failed_read
    assert effects == (["source written"] if operation == "managed_write" and failed_read > 1 else [])
    for caller, project in zip(p.callers, ("first", "second")):
        data = json.loads(p.reader.map_path(caller, p.root).read_bytes())
        assert data["source_root"] == p.reader.canonical(caller)
        assert data["source_project"] == project
    if effects:
        assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)
    monkeypatch.setattr(p.module, "_view_data", original)
    assert p.module.refresh()["published"] == 2
    assert p.reader.read_map(p.callers[0], p.root).roots[0]["name"] == "target"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="native FIFO unavailable")
def test_native_fifo_does_not_block_hook_withdrawal_or_prepared_scan(producer):
    p = producer
    p.module.refresh(p.callers, registry_root=p.root)
    path = p.reader.map_path(p.callers[0], p.root)

    def fifo():
        path.unlink(missing_ok=True)
        os.mkfifo(path)

    fifo()
    assert p.reader.read_map(p.callers[0], p.root).status == "unusable"
    p.module._withdraw_views(path.parent, paths=[path])
    assert json.loads(path.read_text())["inventory_authority"] == "unavailable"
    fifo()
    assert p.module.refresh(registry_root=p.root)["published"] == 1
    assert stat.S_ISREG(path.lstat().st_mode)


def test_interrupted_refresh_retains_all_prepared_caller_retry_scopes(producer, monkeypatch):
    p = producer
    p.module.refresh(p.callers, registry_root=p.root)
    original_write = p.module._atomic_write

    def interrupt_publication(path, content):
        if json.loads(content).get("inventory_authority") == "complete":
            raise KeyboardInterrupt("synthetic interrupted acquisition")
        original_write(path, content)

    monkeypatch.setattr(p.module, "_atomic_write", interrupt_publication)
    with pytest.raises(KeyboardInterrupt):
        p.module.refresh(registry_root=p.root)
    assert all(p.reader.map_path(caller, p.root).is_file() for caller in p.callers)
    assert [
        json.loads(p.reader.map_path(caller, p.root).read_text())["source_project"]
        for caller in p.callers
    ] == ["first", "second"]
    monkeypatch.setattr(p.module, "_atomic_write", original_write)
    assert p.module.refresh(registry_root=p.root)["published"] == 2
    assert all(p.reader.read_map(caller, p.root).status == "ready" for caller in p.callers)


def test_transient_checkout_identity_failure_retains_scope_for_later_refresh(producer, monkeypatch):
    p = producer
    p.module.refresh(p.callers, registry_root=p.root)
    monkeypatch.setattr(p.module, "_reader", lambda: p.reader)
    original = p.reader.repository_identity
    selected = p.reader.canonical(p.callers[0])
    monkeypatch.setattr(p.reader, "repository_identity", lambda path: (
        None if p.reader.canonical(path) == selected else original(path)
    ))
    with pytest.raises(p.module.GuardPublicationError, match="checkout identity is unavailable"):
        p.module.refresh(registry_root=p.root)
    assert p.reader.map_path(p.callers[0], p.root).is_file()
    monkeypatch.setattr(p.reader, "repository_identity", original)
    assert p.module.refresh(registry_root=p.root)["published"] == 2
    assert p.reader.read_map(p.callers[0], p.root).status == "ready"


def test_unavailable_registered_identity_is_not_a_removed_caller(producer, monkeypatch):
    from agent_worktrees import repos

    p = producer
    p.module.refresh(p.callers, registry_root=p.root)
    original_root = repos.registered_entry_root
    original_local = repos.RepoEntry.local_path
    monkeypatch.setattr(repos.RepoEntry, "local_path", lambda entry: (
        str(p.callers[0]) if entry.name == "first" else original_local(entry)
    ))

    def unavailable(entry):
        if entry.name == "first":
            raise repos.RepositoryResolutionError("synthetic unavailable registered path")
        return original_root(entry)

    monkeypatch.setattr(repos, "registered_entry_root", unavailable)
    with pytest.raises(p.module.GuardPublicationError, match="registration identity is unavailable"):
        p.module.refresh(registry_root=p.root)
    assert p.reader.map_path(p.callers[0], p.root).is_file()
    monkeypatch.setattr(repos, "registered_entry_root", original_root)
    assert p.module.refresh(registry_root=p.root)["published"] == 2


@pytest.mark.parametrize("operation", ["refresh", "managed_write"])
def test_publication_lock_order_allows_registry_owned_nested_work(producer, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import threading

    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    original_lock = p.module.registry_lock
    requested = threading.Event()
    effects = []

    @contextmanager
    def observe_registry_lock(path):
        requested.set()
        with original_lock(path):
            yield

    monkeypatch.setattr(p.module, "registry_lock", observe_registry_lock)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with original_lock(p.root / "repos.yaml"):
            call = p.module.refresh if operation == "refresh" else (
                lambda: p.module.managed_write(lambda: effects.append("parallel"))
            )
            future = workers.submit(call)
            assert requested.wait(timeout=5)
            p.module.managed_write(lambda: effects.append("nested"))
            assert effects == ["nested"]
        future.result(timeout=5)
    assert effects == (["nested"] if operation == "refresh" else ["nested", "parallel"])


def test_removed_registered_caller_is_retired_but_explicit_request_errors(producer, monkeypatch):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    p.module.refresh(p.callers)
    p.module.managed_write(lambda: p.registry.repos.pop("first"))
    assert p.reader.read_map(p.callers[0], p.root).status == "missing"
    assert p.reader.read_map(p.callers[1], p.root).status == "ready"
    assert p.module.refresh()["published"] == 1
    with pytest.raises(p.module.GuardPublicationError, match="no available selected-registry registration"):
        p.module.refresh([p.callers[0]])
    assert p.reader.read_map(p.callers[0], p.root).status == "unusable"
    assert p.module.refresh()["published"] == 1
    assert p.reader.read_map(p.callers[0], p.root).status == "missing"


def test_removed_caller_retires_even_when_unrelated_registration_is_unavailable(producer, monkeypatch):
    from agent_worktrees import repos

    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    p.module.refresh(p.callers)
    original_root = repos.registered_entry_root
    original_local = repos.RepoEntry.local_path
    monkeypatch.setattr(repos.RepoEntry, "local_path", lambda entry: (
        str(p.target) if entry.name == "target" else original_local(entry)
    ))

    def unavailable(entry):
        if entry.name == "target":
            raise repos.RepositoryResolutionError("synthetic unrelated unavailable path")
        return original_root(entry)

    monkeypatch.setattr(repos, "registered_entry_root", unavailable)
    p.module.managed_write(lambda: p.registry.repos.pop("first"))
    assert p.reader.read_map(p.callers[0], p.root).status == "missing"
    assert p.reader.read_map(p.callers[1], p.root).status == "ready"
    assert p.module.refresh()["published"] == 1


def test_initial_resident_preparation_refreshes_views_and_reports_unavailability(producer, monkeypatch, capsys):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    p.module.refresh(p.callers)
    p.related["first"].clear()
    p.module.prepare_resident()
    assert p.reader.read_map(p.callers[0], p.root).roots == []
    assert capsys.readouterr().err == ""

    def failed(*args):
        raise p.module.GuardPublicationError("synthetic acquisition unavailable")

    monkeypatch.setattr(p.module, "build_snapshot", failed)
    p.module.prepare_resident()
    assert "initial preparation failed (fail-open)" in capsys.readouterr().err
    assert all(p.reader.read_map(caller, p.root).status == "unusable" for caller in p.callers)


def test_managed_write_withdraws_before_mutation_and_retains_failed_scope(producer, monkeypatch):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
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
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    p.module.refresh(p.callers, registry_root=p.root)
    effects = []

    def failed_publication(*args, **kwargs):
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
    monkeypatch.setattr(producer, "_refresh_locked", lambda root, callers, **kwargs: observed.append(
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
    monkeypatch.setattr(producer, "_refresh_locked", lambda root, callers, **kwargs: observed.append(callers))
    related.write_related(plugin, related.RelatedConfig())
    assert observed == [[]]


@pytest.mark.parametrize("unrelated_unavailable", [False, True])
def test_unregistered_related_write_refreshes_existing_callers_without_requiring_registration(
    producer, monkeypatch, capsys, unrelated_unavailable,
):
    from agent_worktrees import related, repos

    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
    p.module.refresh(p.callers)
    caller = _repo(p.root.parent / "unregistered")
    if unrelated_unavailable:
        p.registry.repos["unrelated"] = repos.RepoEntry(name="unrelated")
        original_root = repos.registered_entry_root
        original_local = repos.RepoEntry.local_path
        monkeypatch.setattr(repos.RepoEntry, "local_path", lambda entry: (
            str(p.root.parent / "unavailable") if entry.name == "unrelated" else original_local(entry)
        ))

        def unavailable(entry):
            if entry.name == "unrelated":
                raise repos.RepositoryResolutionError("synthetic unrelated unavailable path")
            return original_root(entry)

        monkeypatch.setattr(repos, "registered_entry_root", unavailable)
    related.write_related(caller, related.RelatedConfig(primary="target"))
    assert related.read_related(caller).primary == "target"
    assert p.reader.read_map(caller, p.root).status == "missing"
    assert all(p.reader.read_map(path, p.root).status == "ready" for path in p.callers)
    if unrelated_unavailable:
        assert "new caller registration unavailable; not prepared" in capsys.readouterr().err


def test_explicit_refresh_command_and_strict_argument_rejection(producer, monkeypatch, capsys):
    p = producer
    monkeypatch.setattr(p.module.registry_paths, "registry_root", lambda *a: p.root)
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
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
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


def test_producer_uses_real_caller_overlay_precedence_and_managed_removal(tmp_path, monkeypatch):
    import yaml

    from dropin_registry import ScanAuthority
    from agent_worktrees import config, guarded_anchors, machine_config, registry_paths, related, repos, state_root
    from agent_worktrees.acquisition_report import AcquisitionReport, SourceObservation
    from agent_worktrees.repos_registry_transaction import revision

    reader = _load("guarded_anchor_map")
    root = tmp_path / "registry"
    home = tmp_path / "home"
    callers = {name: _repo(tmp_path / name) for name in ("first", "second")}
    targets = {name: _repo(tmp_path / name) for name in ("target", "knowledge-only", "plugin-only")}
    paths = {**callers, **targets}
    registry = repos.ReposRegistry(
        repos={
            name: repos.RepoEntry(name=name, paths={repos._current_platform(): str(path)})
            for name, path in paths.items()
        },
        _baseline=(str((root / "repos.yaml").resolve()), revision(None)),
    )
    knowledge = {name: tmp_path / f"knowledge-{name}" for name in callers}
    local = {name: root / name for name in callers}
    plugin = home / ".copilot" / "installed-plugins" / "fixture" / "sample"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"sample"}')

    def entry(delegate, location):
        return {"delegate": {"via": delegate}, "locus": {"preferred": f"machine:{location}"}}

    def layer(anchor, entries, *, direct=False, plugin_owned=False):
        directory = (
            Path() if direct else Path(related.INREPO_DIRNAME)
            if plugin_owned else related.CANONICAL_RELATED_DIR
        )
        path = anchor / directory / related.RELATED_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"related": entries}))

    layer(plugin, {
        "target": entry("agent-bridge", "plugin"),
        "plugin-only": entry("agent-bridge", "plugin"),
    }, plugin_owned=True)
    for name, caller in callers.items():
        layer(caller, {"target": entry("agent-codespaces", f"base-{name}")})
        layer(knowledge[name], {
            "target": entry("agent-containers", f"knowledge-{name}"),
            "knowledge-only": entry("agent-codespaces" if name == "first" else "none", "knowledge"),
        })
    layer(root, {"target": entry("agent-bridge", "global")}, direct=True)
    layer(local["first"], {"target": entry("agent-codespaces", "exact-first")})
    layer(local["second"], {})

    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv(related.INSTALLED_PLUGINS_ENV, raising=False)
    monkeypatch.setattr(registry_paths, "registry_root", lambda *args: root)
    monkeypatch.setattr(config, "install_dir", lambda: root)
    monkeypatch.setattr(config, "project_dir", lambda name: local[name])
    monkeypatch.setattr(guarded_anchors, "read_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(repos, "registered_entry_root", lambda item: paths[item.name])

    def configuration(*args, project, **kwargs):
        return SimpleNamespace(
            repo_name=project, knowledge_repo=project,
            default_repo=SimpleNamespace(anchor=str(callers[project])),
        )

    def machine_source(base, *, project):
        assert Path(base) == callers[project]
        return AcquisitionReport("native-machine", str(local[project]), (
            SourceObservation(str(local[project]), "native-machine", ScanAuthority.COMPLETE),
        ))

    def activation(*, home, project_root):
        assert home == config._home() and project_root in callers.values()
        return SimpleNamespace(
            authority=ScanAuthority.COMPLETE, findings=(), plugin_names=None, decisions={},
            active={"sample@fixture": SimpleNamespace(
                name="sample", source="sample@fixture",
                live_roots=(SimpleNamespace(root=plugin),),
            )},
        )

    monkeypatch.setattr(config, "load_config", configuration)
    monkeypatch.setattr(machine_config, "machine_config_root_report", machine_source)
    monkeypatch.setattr(state_root, "resolve_state_root", lambda cfg, **kwargs: (
        SimpleNamespace(bound=True, path=str(knowledge[cfg.knowledge_repo]))
    ))
    monkeypatch.setattr(guarded_anchors, "resolve_active_plugins", activation)
    assert guarded_anchors.refresh(callers.values())["published"] == 2

    def rows(name):
        result = reader.read_map(callers[name], root)
        assert result.status == "ready", result.reason
        return {row["name"]: row for row in result.roots}

    first, second = rows("first"), rows("second")
    assert first["target"]["delegate"] == "agent-codespaces"
    assert first["target"]["locus"]["preferred"] == "machine:exact-first"
    assert second["target"]["delegate"] == "agent-bridge"
    assert second["target"]["locus"]["preferred"] == "machine:global"
    assert "knowledge-only" in first and "knowledge-only" not in second
    assert "plugin-only" in first and "plugin-only" in second
    assert related.remove_related(knowledge["first"], "knowledge-only")
    assert "knowledge-only" not in rows("first")
    assert rows("second") == second
    assert related.remove_related(local["first"], "target")
    assert rows("first")["target"]["delegate"] == "agent-bridge"
    assert rows("first")["target"]["locus"]["preferred"] == "machine:global"
    assert "plugin-only" in rows("first")
    original_stat = os.stat

    def unreadable_global(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path) == root / "related.yaml":
            raise PermissionError("synthetic unreadable machine-wide policy")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", unreadable_global)
    with pytest.raises(guarded_anchors.GuardPublicationError, match="unreadable-source"):
        guarded_anchors.refresh()
    assert all(reader.read_map(caller, root).status == "unusable" for caller in callers.values())
    monkeypatch.setattr(os, "stat", original_stat)
    assert guarded_anchors.refresh()["published"] == 2
    assert rows("second") == second
    assert rows("first")["target"]["locus"]["preferred"] == "machine:global"
