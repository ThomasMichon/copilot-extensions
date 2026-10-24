"""Registered roots are flattened by one producer, never by read handlers."""

import copy
import json
import threading

import pytest

from agent_worktrees import config as cfg, repository_map as maps, repository_map_client as client, repos


@pytest.fixture
def registry(monkeypatch, tmp_path):
    path = tmp_path / "repos.yaml"
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: path)
    return path


def write_registry(path, entries):
    path.write_text(json.dumps({"repos": entries}), encoding="utf-8")


def request(owner):
    return {"repository_map_version": 1, "scope": owner.scope}


def test_one_strict_read_flattens_all_classes_and_unavailable_roots(registry, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    platform = repos._current_platform()
    entries = {
        name: {"class": name, platform: str(root), "remote": "https://github.com/example/library"}
        for name in ("reference", "singleton", "worktree", "knowledge")
    }
    entries["missing"] = {"class": "worktree", platform: str(root / "absent")}
    entries["relative"] = {"class": "worktree", platform: "relative"}
    entries["remote-only"] = {"class": "reference"}
    write_registry(registry, entries)
    read = repos.read_registry
    calls = []

    def counted(**kwargs):
        calls.append(kwargs)
        return read(**kwargs)

    monkeypatch.setattr(repos, "read_registry", counted)
    monkeypatch.setattr(repos, "resolve_registered_repo", lambda *a: pytest.fail("point lookup"))
    owner = maps.RepositoryMapRuntime(tmp_path, now=lambda: 100)
    owner.activate()
    owner.refresh()
    result = owner.read(request(owner))
    assert calls == [{"strict": True}]
    assert [r["name"] for r in result["repos"]] == sorted(entries)
    ready = [r for r in result["repos"] if r["registration"] == "ready"]
    assert {r["class"] for r in ready} == {"reference", "singleton", "worktree", "knowledge"}
    assert all(r["path"] == str(root.resolve()) for r in ready)
    unavailable = [r for r in result["repos"] if r["registration"] == "unavailable"]
    assert len(unavailable) == 3 and all(r["path"] is None and r["reason"] for r in unavailable)
    assert client.validate_result(result, owner.scope, now=100) == result


@pytest.mark.parametrize("content", [
    "[]", "repos: []", "repos:\n  same: {}\n  same: {}", "repos: {bad: false}",
    "repos:\n  1: {}\n  good: {}", "repos:\n  good:\n    default_branch: 2026-10-23",
])
def test_invalid_registry_is_not_a_healthy_empty_map(registry, tmp_path, content):
    registry.write_text(content, encoding="utf-8")
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    owner.refresh()
    result = owner.read(request(owner))
    assert result["ok"] is False and result["error"]["code"] == "unavailable"
    assert "repos" not in result


def test_empty_registry_and_readers_do_not_recompute(registry, tmp_path, monkeypatch):
    owner = maps.RepositoryMapRuntime(tmp_path, now=lambda: 100)
    owner.activate()
    owner.refresh()
    monkeypatch.setattr(repos, "read_registry", lambda **kw: pytest.fail("reader read registry"))
    monkeypatch.setattr(repos, "registered_entry_root", lambda *a: pytest.fail("reader resolved root"))
    first = owner.read(request(owner))
    assert first["ok"] and first["repos"] == [] and first["inventory_authority"] == "complete"
    first["repos"].append({"name": "caller-mutation"})
    assert owner.read(request(owner))["repos"] == []


def test_source_changes_root_disappearance_and_last_good_staleness(registry, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    write_registry(registry, {"one": {"class": "reference", repos._current_platform(): str(root)}})
    clock = [100]
    owner = maps.RepositoryMapRuntime(tmp_path, now=lambda: clock[0])
    owner.activate()
    owner.refresh()
    first = owner.read(request(owner))
    root.rmdir()
    owner.refresh()
    disappeared = owner.read(request(owner))
    assert disappeared["repos"][0]["registration"] == "unavailable"
    assert disappeared["projection"]["source_revision"] == first["projection"]["source_revision"]
    write_registry(registry, {})
    owner.refresh()
    empty = owner.read(request(owner))
    assert empty["repos"] == []
    assert empty["projection"]["source_revision"] != first["projection"]["source_revision"]
    registry.write_text("repos: false", encoding="utf-8")
    owner.refresh()
    failed = owner.read(request(owner))
    assert failed["repos"] == [] and failed["inventory_authority"] == "advisory"
    assert failed["projection"]["quality"] == "stale"
    with pytest.raises(ValueError, match="stale"):
        client.validate_result(failed, owner.scope, now=100)
    write_registry(registry, {})
    owner.refresh()
    clock[0] += maps.MAX_AGE + 1
    assert owner.read(request(owner))["projection"]["quality"] == "stale"


def test_inflight_refresh_cannot_reopen_drained_or_replaced_owner(registry, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    build = maps.build_snapshot

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return build(*args, **kwargs)

    monkeypatch.setattr(maps, "build_snapshot", blocked)
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    worker = threading.Thread(target=owner.refresh)
    worker.start()
    try:
        assert entered.wait(2)
        owner.close_admission("draining")
        assert owner.read(request(owner))["error"]["code"] == "draining"
        owner.activate()
        release.set()
        worker.join(2)
        assert not worker.is_alive()
        assert owner.read(request(owner))["error"]["code"] == "unready"
    finally:
        release.set()
        worker.join(2)


@pytest.mark.parametrize("change", [
    {"repository_map_version": True}, {"repository_map_version": 2},
    {"scope": "foreign"}, {"extra": True},
])
def test_request_requires_exact_version_and_installation_scope(registry, tmp_path, change):
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    owner.refresh()
    assert owner.read({**request(owner), **change})["error"]["code"] == "invalid-request"


@pytest.mark.parametrize("change", [
    {"scope": "foreign"}, {"generation": "bad"}, {"revision": True},
    {"observed_at": float("nan")}, {"observed_at": 200}, {"observed_at": -1},
    {"source_revision": ""}, {"quality": "stale"},
])
def test_client_rejects_incompatible_or_stale_provenance(registry, tmp_path, change):
    owner = maps.RepositoryMapRuntime(tmp_path, now=lambda: 100)
    owner.activate()
    owner.refresh()
    result = copy.deepcopy(owner.read(request(owner)))
    result["projection"].update(change)
    with pytest.raises(ValueError):
        client.validate_result(result, owner.scope, now=100)


def test_client_unavailable_never_starts_or_computes(monkeypatch, tmp_path):
    monkeypatch.setattr(client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(client.routing, "read_active_endpoint", lambda *a, **kw: None)
    monkeypatch.setattr(client.monitor, "_ensure_status_monitor", lambda: pytest.fail("reader started service"))
    monkeypatch.setattr(repos, "read_registry", lambda **kw: pytest.fail("reader read registry"))
    with pytest.raises(ValueError, match="unavailable"):
        client.read_current()


def test_cli_shape_and_argument_refusal(monkeypatch, capfd, tmp_path):
    from agent_worktrees import __main__ as core

    owner = maps.RepositoryMapRuntime(tmp_path)
    monkeypatch.setattr(client, "read_current", lambda: {"repository_map_version": 1, "repos": []})
    monkeypatch.setattr(core, "_ensure_cluster_loaded", lambda: pytest.fail("map reader loaded eager cluster"))
    assert core.main(["repos", "map", "--json"]) == 0
    assert json.loads(capfd.readouterr().out)["repos"] == []
    assert client.command([]) == 2
    assert owner.health()["capabilities"] == ["repos-map-v1"]
    assert cfg.detect_platform() in {"windows", "linux", "wsl"}


def test_reader_demand_has_bounded_linger(registry, tmp_path):
    clock = [100]
    owner = maps.RepositoryMapRuntime(tmp_path, monotonic=lambda: clock[0])
    owner.activate()
    owner.refresh()
    assert owner.has_active_demand() is False
    owner.read(request(owner))
    assert owner.has_active_demand()
    clock[0] += 30
    assert owner.has_active_demand() is False


def test_registry_change_during_construction_is_not_published(registry, tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    write_registry(registry, {"one": {"class": "reference", repos._current_platform(): str(root)}})
    resolve = repos.registered_entry_root

    def changed(entry):
        write_registry(registry, {})
        return resolve(entry)

    monkeypatch.setattr(repos, "registered_entry_root", changed)
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    owner.refresh()
    result = owner.read(request(owner))
    assert result["ok"] is False
    assert "changed during" in result["error"]["message"]
