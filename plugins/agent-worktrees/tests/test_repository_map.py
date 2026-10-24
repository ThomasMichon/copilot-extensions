"""Registered roots are flattened by one producer, never by read handlers."""

import copy
import json
import threading
import time

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
    read = maps.read_registry
    calls = []

    def counted(path, **kwargs):
        calls.append((path, kwargs))
        return read(path, **kwargs)

    monkeypatch.setattr(maps, "read_registry", counted)
    monkeypatch.setattr(repos, "resolve_registered_repo", lambda *a: pytest.fail("point lookup"))
    owner = maps.RepositoryMapRuntime(tmp_path, now=lambda: 100)
    owner.activate()
    owner.refresh()
    result = owner.read(request(owner))
    assert calls == [(registry, {"strict": True})]
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
    monkeypatch.setattr(maps, "read_registry", lambda *a, **kw: pytest.fail("reader read registry"))
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


def test_map_preserves_registered_labels_without_consumer_resource_restrictions(registry, tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    names = [".github", "_tools", "healthy", "a" * 81, "space alias"]
    write_registry(registry, {
        name: {"class": "reference", repos._current_platform(): str(root)} for name in names
    })
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    owner.refresh()
    result = client.validate_result(owner.read(request(owner)), owner.scope)
    assert [r["name"] for r in result["repos"]] == sorted(names)
    assert all(r["registration"] == "ready" for r in result["repos"])


def test_producer_keeps_its_installation_registry_when_caller_context_changes(registry, tmp_path, monkeypatch):
    write_registry(registry, {})
    owner = maps.RepositoryMapRuntime(tmp_path)
    foreign = tmp_path / "foreign.yaml"
    foreign.write_text("repos: false", encoding="utf-8")
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: foreign)
    owner.activate()
    owner.refresh()
    result = owner.read(request(owner))
    assert result["ok"] and result["inventory_authority"] == "complete"
    assert result["repos"] == []


def test_periodic_producer_stays_current_during_a_long_sweep(registry, tmp_path):
    clock = [100.0]
    tick, waiting = threading.Event(), threading.Event()
    intervals = []

    def fake_wait(wake, interval):
        intervals.append(interval)
        waiting.set()
        while not wake.is_set():
            if tick.wait(.01):
                tick.clear()
                waiting.clear()
                return False
        return True

    owner = maps.RepositoryMapRuntime(tmp_path, now=lambda: clock[0], wait=fake_wait)
    owner.activate()
    owner.start(interval=120)

    def published_at_now():
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = owner.read(request(owner))
            if result.get("projection", {}).get("observed_at") == clock[0]:
                return client.validate_result(result, owner.scope, now=clock[0])
            time.sleep(.005)
        pytest.fail("owned producer did not publish after its scheduled tick")

    try:
        first = published_at_now()
        for _ in range(6):
            assert waiting.wait(2)
            clock[0] += 30
            tick.set()
            result = published_at_now()
            assert result["projection"]["revision"] > first["projection"]["revision"]
        assert clock[0] - first["projection"]["observed_at"] == 180
        assert intervals and max(intervals) <= maps.MAX_AGE / 2
    finally:
        assert owner.shutdown()
    owner.activate()
    assert owner.read(request(owner))["error"]["code"] == "shutdown"


def test_failed_producer_thread_start_keeps_shutdown_available(registry, tmp_path, monkeypatch):
    owner = maps.RepositoryMapRuntime(tmp_path)
    monkeypatch.setattr(
        threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )
    with pytest.raises(RuntimeError, match="thread unavailable"):
        owner.start()
    assert owner.shutdown()


def registry_link(path, target):
    try:
        path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def test_registry_symlink_replacement_refreshes_the_installation_filename(registry, tmp_path):
    old = tmp_path / "old.yaml"
    write_registry(old, {"old": {"class": "reference"}})
    registry_link(registry, old)
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    owner.refresh()
    assert [r["name"] for r in owner.read(request(owner))["repos"]] == ["old"]
    replacement = tmp_path / "replacement.yaml"
    write_registry(replacement, {"replacement": {"class": "reference"}})
    replacement.replace(registry)
    owner.refresh()
    result = client.validate_result(owner.read(request(owner)), owner.scope)
    assert [r["name"] for r in result["repos"]] == ["replacement"]
    assert json.loads(old.read_text(encoding="utf-8"))["repos"] == {"old": {"class": "reference"}}


def test_dangling_registry_symlink_is_unavailable_not_healthy_empty(registry, tmp_path):
    registry_link(registry, tmp_path / "absent.yaml")
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    owner.refresh()
    result = owner.read(request(owner))
    assert result["ok"] is False and result["error"]["code"] == "unavailable"
    assert "repos" not in result


def test_registry_symlink_change_during_construction_is_not_published(registry, tmp_path, monkeypatch):
    old, replacement = tmp_path / "old.yaml", tmp_path / "replacement.yaml"
    write_registry(old, {"old": {"class": "reference"}})
    write_registry(replacement, {})
    registry_link(registry, old)
    resolve_root = repos.registered_entry_root

    def replace(entry):
        replacement.replace(registry)
        return resolve_root(entry)

    monkeypatch.setattr(repos, "registered_entry_root", replace)
    owner = maps.RepositoryMapRuntime(tmp_path)
    owner.activate()
    owner.refresh()
    result = owner.read(request(owner))
    assert result["ok"] is False
    assert "changed during" in result["error"]["message"]
