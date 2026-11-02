"""Resident hook and cache publication races against retained native custody."""
from __future__ import annotations

import copy
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import test_windows_observation_custody as custody
from agent_worktrees import __main__ as core

owner = custody.owner


@pytest.mark.parametrize("phase", ["gather", "commit"])
def test_warm_list_cache_commit_is_atomic_with_new_custody(owner, monkeypatch, phase):
    from agent_worktrees import list_cli

    committing, release, attempted, acquired = (threading.Event() for _ in range(4))
    errors, results, writes = [], [], []
    monkeypatch.setattr(list_cli.cfg, "project_name", lambda: "A")
    monkeypatch.setattr(list_cli.cfg, "cached_load_config_scope", nullcontext)
    monkeypatch.setattr(list_cli.list_cache, "ttl_seconds", lambda: 10)
    monkeypatch.setattr(list_cli.list_cache, "recent_demands", lambda project: [{"args": {}, "key": "key"}])
    monkeypatch.setattr(list_cli.list_cache, "resident_fresh_for", lambda interval: 20)
    monkeypatch.setattr(list_cli, "_core_helper", lambda name, default: {
        "_list_records_for_args": lambda args: [],
        "_build_list_json_payload": gather,
    }[name])

    def gather(*args, **kwargs):
        if phase == "gather":
            custody.pending(owner)
        return {"worktrees": []}

    def write(*args, **kwargs):
        committing.set()
        assert release.wait(10)
        writes.append((args, kwargs))

    def warm():
        try:
            results.append(list_cli._warm_list_cache_for_active_project(cleanup_owner=owner))
        except BaseException as error:
            errors.append(error)

    def claim():
        try:
            attempted.set()
            custody.pending(owner)
            acquired.set()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(list_cli.list_cache, "write", write)
    worker, claimant = threading.Thread(target=warm), threading.Thread(target=claim)
    worker.start()
    try:
        if phase == "commit":
            assert committing.wait(10)
            claimant.start()
            assert attempted.wait(10)
            assert not acquired.wait(.1)
    finally:
        release.set()
        worker.join(10)
        if claimant.ident is not None:
            claimant.join(10)
    assert not worker.is_alive() and not claimant.is_alive() and not errors
    assert results == [0 if phase == "gather" else 1]
    assert len(writes) == (0 if phase == "gather" else 1)
    assert owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("kind", ["preToolUse", "postToolUse"])
def test_pending_non_snapshot_hook_keeps_policy_without_git_discovery(owner, monkeypatch, kind):
    custody.pending(owner)
    active = ["previous"]
    calls = []
    monkeypatch.setattr(core.cfg, "active_project", lambda: active[0])
    monkeypatch.setattr(core.cfg, "set_active_project", lambda value: active.__setitem__(0, value))
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail("pending Git discovery"))
    monkeypatch.setattr(core, "_bind_nudge_decision", lambda *a, **k: calls.append(("binding", active[0])) or {})
    monkeypatch.setattr(core.tracking, "find_worktree_id_by_cwd", lambda *a: None)
    policy = core._ResidentHookPolicy(None)

    def module(name):
        def decide(payload, **kwargs):
            calls.append((name, active[0]))
            return {"permissionDecision": "deny"} if name == "statelessness_guard.py" else "accounted"
        return SimpleNamespace(decide=decide)

    monkeypatch.setattr(policy, "_module", module)
    policy.hook_client = SimpleNamespace(_merge_pre_decisions=lambda a, b: b)
    monkeypatch.setattr(policy, "mutation_targets", lambda payload: calls.append(("mutation", active[0])) or [])
    result = core._resident_hook_decision(kind, {"cwd": "/repo/wt"}, segment_cache=owner, policy=policy)
    assert active == ["previous"]
    if kind == "preToolUse":
        assert result == {"permissionDecision": "deny"}
        assert calls == [("statelessness_guard.py", "A")]
    else:
        assert result == {"additionalContext": "accounted"}
        assert calls == [("mutation", "A"), ("nudge_status.py", "A"), ("binding", "A")]


@pytest.mark.parametrize("invalidate", ["path", "alias", "all"])
def test_real_cached_sibling_segments_retain_project_scope(owner, monkeypatch, invalidate):
    active = ["A"]
    monkeypatch.setattr(core.cfg, "project_name", lambda: active[0])
    monkeypatch.setattr(core.cfg, "load_config", lambda **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo" if active[0] == "A" else "/independent"),
    ))
    monkeypatch.setattr(core, "_find_record_for_path", lambda path: SimpleNamespace(
        worktree_path=path.removesuffix("/nested"),
    ))
    monkeypatch.setattr(core, "_render_status_segment", lambda *a, **k: "last-" + active[0])
    path_a = "/repo.worktrees/wt2"
    path_b = "/independent.worktrees/other"
    assert owner.get(path_a + "/nested") == "last-A"
    active[0] = "B"
    assert owner.get(path_b) == "last-B"
    key_a, key_b = owner._key(path_a), owner._key(path_b)
    previous = owner._entries[key_a]
    custody.pending(owner, path="/repo.worktrees/wt1")
    if invalidate == "all":
        owner.invalidate_all()
    else:
        owner.invalidate(path_a + ("/nested" if invalidate == "alias" else ""))
        owner.invalidate(path_b)
    assert owner._entries[key_a] == previous
    assert owner._aliases[owner._key(path_a + "/nested")] == key_a
    assert key_b not in owner._entries
    assert key_a in owner._invalidated
    active[0] = "A"
    assert owner.get(path_a) == owner.pending_segment()
    next(iter(owner._observations.values())).cleanup.try_settle = lambda: True
    assert owner.settle_pending_once() == 0
    assert owner.get(path_a) == "last-A"
    assert key_a not in owner._invalidated


@pytest.mark.parametrize("sweep", [False, True])
def test_pending_after_bundle_ticket_release_cannot_publish(owner, monkeypatch, sweep):
    cache = custody._status_cache(owner, monkeypatch)
    old = (custody._cached_bundle(), 1.0, 10.0)
    cache._entries[("A", "wt")] = copy.deepcopy(old)
    saved, release = threading.Event(), threading.Event()
    persisted, results, errors = [], [], []
    monkeypatch.setattr(cache, "_persist", lambda *a: persisted.append(a))

    def compute(*args):
        value = owner.run_observation("A", "/repo", "/repo/wt", lambda: custody._cached_bundle())
        value["as_of"] = 10.0
        saved.set()
        assert release.wait(10)
        return value

    def refresh():
        try:
            results.append(cache.sweep_due(refresh=compute) if sweep else cache.get_or_refresh(
                "A", "wt", force=True, compute=compute,
            ))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=refresh)
    worker.start()
    try:
        assert saved.wait(10)
        custody.pending(owner)
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and not errors
    assert cache._entries[("A", "wt")] == old and persisted == []
    assert results == [0] if sweep else results[0]["facts"]["git_state"]["confirmed"] is False


def test_pending_after_expiry_sampling_cannot_evict(owner, monkeypatch):
    cache = custody._status_cache(owner, monkeypatch)
    old = (custody._cached_bundle(), 1.0, 1.0)
    cache._entries[("A", "wt")] = old
    cache._demand_ttl = 1
    sampled, release = threading.Event(), threading.Event()
    deleted, errors = [], []
    monkeypatch.setattr(cache, "_delete_row", lambda *a, **k: deleted.append(a))
    lock = cache._lock

    class SampleBarrier:
        first = True

        def __enter__(self):
            lock.acquire()

        def __exit__(self, *args):
            lock.release()
            if self.first:
                self.first = False
                sampled.set()
                assert release.wait(10)

    cache._lock = SampleBarrier()

    def sweep():
        try:
            assert cache.sweep_due(refresh=lambda *a: pytest.fail("expired refresh")) == 0
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=sweep)
    worker.start()
    try:
        assert sampled.wait(10)
        custody.pending(owner)
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and not errors
    assert cache._entries[("A", "wt")] == old and deleted == []


@pytest.mark.parametrize("operation", ["request", "sweep", "expire"])
def test_committing_cache_mutation_excludes_new_custody_until_done(owner, monkeypatch, operation):
    cache = custody._status_cache(owner, monkeypatch)
    old = (custody._cached_bundle(), 1.0, 10.0 if operation != "expire" else 1.0)
    cache._entries[("A", "wt")] = old
    if operation == "expire":
        cache._demand_ttl = 1
    committing, release, attempted, acquired = (threading.Event() for _ in range(4))
    errors = []

    def persist(*args, **kwargs):
        committing.set()
        assert release.wait(10)

    monkeypatch.setattr(cache, "_persist" if operation != "expire" else "_delete_row", persist)

    def mutate():
        try:
            if operation == "request":
                cache.get_or_refresh("A", "wt", force=True, compute=custody._cached_bundle)
            else:
                cache.sweep_due(refresh=lambda *a: custody._cached_bundle())
        except BaseException as error:
            errors.append(error)

    def pending():
        try:
            attempted.set()
            custody.pending(owner)
            acquired.set()
        except BaseException as error:
            errors.append(error)

    mutation = threading.Thread(target=mutate)
    claimant = threading.Thread(target=pending)
    mutation.start()
    try:
        assert committing.wait(10)
        claimant.start()
        assert attempted.wait(10)
        assert not acquired.wait(.1)
    finally:
        release.set()
        mutation.join(10)
        if claimant.ident is not None:
            claimant.join(10)
    assert not mutation.is_alive() and not claimant.is_alive() and not errors
    assert acquired.is_set() and owner.observation_counts() == (0, 1)
    if operation == "expire":
        assert ("A", "wt") not in cache._entries
    else:
        assert cache._entries[("A", "wt")][1] == 10.0
        value = cache.get_or_refresh("A", "wt", force=False, compute=lambda: pytest.fail("pending refresh"))
        assert value["facts"]["git_state"]["confirmed"] is False


@pytest.mark.parametrize("operation", ["request", "sweep", "expire"])
def test_pending_preserves_actual_sqlite_bundle_and_timestamps(owner, monkeypatch, tmp_path, operation):
    from agent_worktrees.worktree_status_cache import WorktreeStatusCache

    now = [10.0]
    cache = WorktreeStatusCache(tmp_path / "status.sqlite", now=lambda: now[0], ttl_seconds=1,
                                demand_ttl_seconds=50, cleanup_owner=owner)
    try:
        cache.get_or_refresh("A", "wt", force=True, compute=custody._cached_bundle)
        old = copy.deepcopy(cache._entries[("A", "wt")])
        row = tuple(cache._conn.execute("SELECT * FROM worktree_status_cache").fetchone())
        now[0] = 20 if operation != "expire" else 100

        def compute(*args):
            value = owner.run_observation("A", "/repo", "/repo/wt", custody._cached_bundle)
            custody.pending(owner)
            return value

        if operation == "request":
            result = cache.get_or_refresh("A", "wt", force=True, compute=compute)
            assert result["facts"]["git_state"]["confirmed"] is False
        elif operation == "sweep":
            assert cache.sweep_due(refresh=compute) == 0
        else:
            custody.pending(owner)
            assert cache.sweep_due(refresh=lambda *a: pytest.fail("expired pending refresh")) == 0
        assert cache._entries[("A", "wt")] == old
        assert tuple(cache._conn.execute("SELECT * FROM worktree_status_cache").fetchone()) == row
    finally:
        cache.close()


@pytest.mark.parametrize("known", [False, True])
def test_windows_hook_project_discovery_is_metadata_only_even_before_custody(owner, monkeypatch, known):
    from agent_worktrees import installer

    active = ["previous"]
    monkeypatch.setattr(core.cfg, "set_active_project", lambda value: active.__setitem__(0, value))
    monkeypatch.setattr(installer, "read_projects_registry", lambda: {"projects": {
        "A": {"anchor": "/repo"}, "B": {"anchor": "/independent", "worktree_root": "/custom"},
    }} if known else {"projects": {}})
    owner.activate_hook_project("/repo.worktrees/wt2", lambda *a, **k: pytest.fail("Git discovery"))
    assert active == ["A" if known else None]
    owner.activate_hook_project("/custom/wt", lambda *a, **k: pytest.fail("Git discovery"))
    assert active == ["B" if known else None]
