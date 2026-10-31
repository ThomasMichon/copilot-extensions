"""File freshness contracts; these do not qualify native UI latency."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import gc
from pathlib import Path
import threading
import weakref

import pytest
import yaml

from worktree_manager import harness_state
from worktree_manager.production_picker import context, project_config as cfg


@pytest.fixture
def files(tmp_path, monkeypatch):
    project, repo = tmp_path / "project", tmp_path / "repo"
    project.mkdir()
    (repo / ".agent-worktrees").mkdir(parents=True)
    path = project / "config.yaml"
    path.write_text("repo_name: old\n", encoding="utf-8")
    (repo / ".agent-worktrees" / "config.yaml").write_text("default_branch: main\n")
    now = [0.0]
    monkeypatch.setattr(cfg, "_file_clock", lambda: now[0])
    monkeypatch.setattr(context, "_project", "example")
    monkeypatch.setattr(context, "_bootstrap", None)
    cfg.bind_bootstrap("example", {
        "config_dir": str(project), "repo_dir": str(repo), "machine": "host",
    })
    yield path, repo, now
    cfg.clear_caches(reset_binding=True)


def test_ttl_boundary_and_same_metadata_replacement(files):
    import os

    path, _, now = files
    assert cfg.load_config().repo_name == "old"
    stamp = path.stat()
    replacement = path.with_suffix(".replacement")
    replacement.write_text("repo_name: new\n")
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    replacement.replace(path)
    now[0] = 59.999
    assert cfg.load_config().repo_name == "old"
    now[0] = 60.0
    assert cfg.load_config().repo_name == "new"


def test_unchanged_expired_bytes_reuse_yaml_parse(files, monkeypatch):
    _, _, now = files
    parse, count = cfg.yaml.safe_load, []

    def counted(value):
        count.append(value)
        return parse(value)

    monkeypatch.setattr(cfg.yaml, "safe_load", counted)
    cfg.load_config()
    first = len(count)
    now[0] = 60.0
    cfg.load_config()
    assert len(count) == first


def test_warm_worker_and_ui_reads_do_zero_io_and_ui_never_waits(files, monkeypatch):
    expected = cfg.load_config()

    def unexpected(*a, **kw):
        pytest.fail("warm configuration performed discovery")

    monkeypatch.setattr(Path, "read_bytes", unexpected)
    monkeypatch.setattr(Path, "stat", unexpected)
    monkeypatch.setattr(cfg.engine_client, "get_value", unexpected)
    assert cfg.load_config() == expected
    generation = cfg._file_generation
    generation.lock.acquire()
    try:
        async def read():
            assert cfg.load_config() == expected
        asyncio.run(read())
    finally:
        generation.lock.release()


@pytest.mark.parametrize("reason", ["cold", "expired", "invalidated"])
def test_ui_unready_file_snapshot_refuses_without_io(files, monkeypatch, reason):
    _, _, now = files
    if reason != "cold":
        cfg.load_config()
    if reason == "expired":
        now[0] = 60
    if reason == "invalidated":
        cfg.clear_caches()
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("UI file read"))

    async def read():
        with pytest.raises(RuntimeError, match="not ready"):
            cfg.load_config()
        assert cfg._machine_name("example") == "host"

    asyncio.run(read())


def test_zero_ttl_nested_scopes_and_midpass_expiry(files):
    path, _, now = files
    session = cfg.ConfigCacheSession(ttl=0)
    with session.scope():
        generation = cfg._active_files.get()
        assert cfg._read_yaml(path)["repo_name"] == "old"
        path.write_text("repo_name: new\n")
        with cfg.ConfigCacheSession(ttl=60).scope():
            assert cfg._active_files.get() is generation
            assert cfg._read_yaml(path)["repo_name"] == "old"
        assert cfg._active_files.get() is generation
    assert cfg._active_files.get() is None
    with session.scope():
        assert cfg._active_files.get() is not generation
        assert cfg._read_yaml(path)["repo_name"] == "new"
    with cfg.ConfigCacheSession(ttl=60).scope():
        assert cfg._read_yaml(path)["repo_name"] == "new"
        path.write_text("repo_name: end\n")
        now[0] = 60
        assert cfg._read_yaml(path)["repo_name"] == "new"
    assert cfg._read_yaml(path)["repo_name"] == "end"


def test_scope_exception_restores_context(files):
    with cfg.ConfigCacheSession(ttl=60).scope():
        parent = cfg._active_files.get()
        with pytest.raises(ValueError):
            with cfg.ConfigCacheSession(ttl=0).scope():
                raise ValueError("cancel nested pass")
        assert cfg._active_files.get() is parent
    assert cfg._active_files.get() is None


@pytest.mark.parametrize("cancel", [False, True])
def test_concurrent_read_is_single_flight_and_cancellation_releases_waiters(files, monkeypatch, cancel):
    path, _, _ = files
    entered, release = threading.Event(), threading.Event()
    start = threading.Barrier(4)
    original, calls = Path.read_bytes, []

    def blocked(p):
        if p == path:
            calls.append(p)
            entered.set()
            assert release.wait(5)
            if cancel:
                raise asyncio.CancelledError()
        return original(p)

    monkeypatch.setattr(Path, "read_bytes", blocked)

    def read():
        start.wait(5)
        return cfg._read_yaml(path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(read) for _ in range(3)]
        try:
            start.wait(5)
            assert entered.wait(5)
        finally:
            release.set()
        for future in futures:
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    future.result(5)
            else:
                assert future.result(5) == {"repo_name": "old"}
    assert calls == [path]


@pytest.mark.parametrize("rebind", [False, True])
def test_invalidation_during_blocked_read_rejects_old_completion(files, monkeypatch, rebind):
    path, repo, _ = files
    entered, release = threading.Event(), threading.Event()
    original = Path.read_bytes
    old_binding = cfg._scalar_cache

    def blocked(p):
        content = original(p)
        if p == path and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return content

    monkeypatch.setattr(Path, "read_bytes", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(cfg._read_yaml, path)
        try:
            assert entered.wait(5)
            retired = cfg._file_generation
            assert retired.binding is old_binding
            path.write_text("repo_name: new\n")
            if rebind:
                cfg.bind_bootstrap("example", {
                    "config_dir": str(path.parent), "repo_dir": str(repo), "machine": "host",
                })
            else:
                cfg.clear_caches()
            assert cfg._read_yaml(path) == {"repo_name": "new"}
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="superseded"):
            old.result(5)
    assert cfg._file_generation is not retired
    assert retired.binding is old_binding


@pytest.mark.parametrize("bad", ["scalar", "[broken", None])
def test_error_is_not_empty_authority_and_retry_uses_next_generation(files, monkeypatch, bad):
    path, _, now = files
    original = Path.read_bytes
    if bad is None:
        monkeypatch.setattr(Path, "read_bytes", lambda p: (_ for _ in ()).throw(
            PermissionError("denied")) if p == path else original(p))
    else:
        path.write_text(bad)
    for _ in range(2):
        with pytest.raises(ValueError):
            cfg._read_yaml(path)
    monkeypatch.setattr(Path, "read_bytes", original)
    path.write_text("repo_name: fixed\n")
    now[0] = 60
    assert cfg._read_yaml(path) == {"repo_name": "fixed"}


def test_preferred_file_creation_is_visible_after_ttl(files):
    _, repo, now = files
    preferred = repo / ".agent-worktrees" / "config.yaml"
    preferred.unlink()
    (repo / "config.yaml").write_text("default_branch: legacy\n")
    assert cfg.load_config().default_repo.default_branch == "legacy"
    preferred.write_text("default_branch: preferred\n")
    now[0] = 60
    assert cfg.load_config().default_repo.default_branch == "preferred"


@pytest.mark.parametrize("value", [[], "", False, 0, None])
def test_falsey_repository_container_refuses_config_and_retries(files, monkeypatch, value):
    path, repo, now = files
    registry = path.parent / "repos.yaml"
    registry.write_text(yaml.safe_dump({"repos": value}))
    (repo / ".agent-worktrees" / "config.yaml").write_text("{}\n")
    monkeypatch.setattr(harness_state, "_registry_path", lambda *a: registry)
    for _ in range(2):
        with pytest.raises(ValueError, match="entries must be a mapping"):
            cfg.load_config()
    registry.write_text("repos:\n  example:\n    default_branch: repaired\n")
    now[0] = 60
    assert cfg.load_config().default_repo.default_branch == "repaired"


@pytest.mark.parametrize("document", ["{}\n", "repos: {}\n", "repos:\n  example: null\n"])
def test_missing_repository_or_null_selected_entry_retains_empty_branch(files, monkeypatch, document):
    path, repo, _ = files
    registry = path.parent / "repos.yaml"
    registry.write_text(document)
    (repo / ".agent-worktrees" / "config.yaml").write_text("{}\n")
    monkeypatch.setattr(harness_state, "_registry_path", lambda *a: registry)
    assert cfg.load_config().default_repo.default_branch == ""


def test_entries_are_bounded_and_retired_generation_has_no_chain(files, monkeypatch):
    path, _, now = files
    monkeypatch.setattr(cfg._FileGeneration, "capacity", 3)
    with cfg.ConfigCacheSession(ttl=60).scope():
        for n in range(3):
            cfg._read_yaml(path.parent / f"{n}.yaml")
        with pytest.raises(RuntimeError, match="capacity"):
            cfg._read_yaml(path.parent / "extra.yaml")
    old = weakref.ref(cfg._file_generation)
    now[0] = 60
    cfg._read_yaml(path)
    gc.collect()
    assert old() is None
    assert len(cfg._file_generation.entries) <= 3
    assert len(cfg._file_generation.seeds) <= 3


@pytest.mark.parametrize("ttl", [-1, float("nan"), float("inf")])
def test_ttl_must_be_finite_nonnegative(ttl):
    with pytest.raises(ValueError):
        cfg.ConfigCacheSession(ttl=ttl)


def test_invalidated_config_never_reclassifies_configured_local_alias_as_remote(files, monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_local

    _, repo, _ = files
    (repo / ".agent-worktrees" / "machines.yaml").write_text(
        "machines:\n  host:\n    hostname: raw-host\n"
        "    ssh:\n      environments:\n        - name: Win\n          alias: host\n",
    )
    monkeypatch.setattr(data_local, "LOCAL", ("raw-host", "Win"))
    monkeypatch.setattr(data_local, "local_execution_platform", lambda: "windows")
    assert data_local.is_local("host", "Win") is True
    cfg.clear_caches()

    async def read():
        try:
            local = data_local.is_local("host", "Win")
        except RuntimeError:
            return
        assert local is True, "not-ready file state must not become remote locality"

    asyncio.run(read())


def test_supplier_content_registry_and_name_changes_use_real_selected_paths(files, monkeypatch):
    path, repo, now = files
    home = path.parent.parent
    registry = home / ".agent-worktrees"
    registry.mkdir()
    suppliers = [home / f"supplier-{n}" for n in range(3)]
    for index, supplier in enumerate(suppliers):
        (supplier / ".agent-worktrees").mkdir(parents=True)
        (supplier / ".agent-worktrees" / "machines.yaml").write_text(
            yaml.safe_dump({"machines": {f"host{index}": {"display_name": f"Host {index}"}}}),
        )
    (registry / "projects.yaml").write_text("projects:\n  one: {}\n  two: {}\n")
    repos = {"one": {"windows": str(suppliers[0])}, "two": {"windows": str(suppliers[1])}}
    (registry / "repos.yaml").write_text(yaml.safe_dump({"repos": repos}))
    path.write_text("repo_name: example\nknowledge_repo: one\n")
    monkeypatch.setattr(harness_state, "home", lambda: home)
    monkeypatch.setattr(harness_state, "build_projects", lambda *a: pytest.fail("all-project scan"))
    monkeypatch.setattr(harness_state, "build_repos", lambda *a: pytest.fail("all-repository scan"))
    assert set(cfg.load_machines_yaml(repo)) == {"host0"}
    (suppliers[0] / ".agent-worktrees" / "machines.yaml").write_text(
        "machines:\n  changed:\n    display_name: Changed\n",
    )
    now[0] = 60
    assert set(cfg.load_machines_yaml(repo)) == {"changed"}
    path.write_text("repo_name: example\nknowledge_repo: two\n")
    now[0] = 120
    assert set(cfg.load_machines_yaml(repo)) == {"host1"}
    repos["two"]["windows"] = str(suppliers[2])
    (registry / "repos.yaml").write_text(yaml.safe_dump({"repos": repos}))
    now[0] = 180
    assert set(cfg.load_machines_yaml(repo)) == {"host2"}


def test_supplier_legacy_precedence_and_new_primary_registry(files, monkeypatch):
    path, repo, now = files
    home = path.parent.parent
    registry = home / ".agent-worktrees"
    registry.mkdir()
    supplier = home / "supplier"
    supplier.mkdir()
    legacy = supplier / "machines.yaml"
    legacy.write_text("machines:\n  legacy: {}\n")
    (registry / "projects.yaml").write_text(yaml.safe_dump({
        "projects": {"supplier": {"anchor": str(supplier)}},
    }))
    path.write_text("knowledge_repo: supplier\n")
    monkeypatch.setattr(harness_state, "home", lambda: home)
    assert set(cfg.load_machines_yaml(repo)) == {"legacy"}
    canonical = supplier / ".agent-worktrees" / "machines.yaml"
    canonical.parent.mkdir()
    canonical.write_text("machines:\n  canonical: {}\n")
    now[0] = 60
    assert set(cfg.load_machines_yaml(repo)) == {"canonical"}
    (repo / ".agent-worktrees" / "machines.yaml").write_text("machines:\n  primary: {}\n")
    now[0] = 120
    assert set(cfg.load_machines_yaml(repo)) == {"primary"}


def test_relative_roster_paths_do_not_share_another_working_directory_snapshot(files, monkeypatch):
    path, _, _ = files
    roots = [path.parent.parent / name for name in ("one", "two")]
    for index, root in enumerate(roots):
        roster = root / "repo" / ".agent-worktrees" / "machines.yaml"
        roster.parent.mkdir(parents=True)
        roster.write_text(f"machines:\n  host{index}: {{}}\n")
    for index, root in enumerate(roots):
        monkeypatch.chdir(root)
        assert set(cfg.load_machines_yaml("repo")) == {f"host{index}"}


def test_invalid_registry_failure_survives_a_tolerant_mapping_consumer(files):
    from worktree_manager.production_picker.picker_tui import data_ssh

    _, repo, _ = files
    (repo / ".agent-worktrees" / "machines.yaml").write_text("not-machines: {}\n")
    with cfg.ConfigCacheSession(ttl=60).scope() as session:
        assert data_ssh.machine_key_map() == {}
        with pytest.raises(ValueError, match="machines mapping"):
            session.raise_for_errors()


@pytest.mark.parametrize("action", ["resume", "restore"])
def test_immediate_decide_refuses_unknown_locality_without_exit_or_result(files, monkeypatch, action):
    from types import SimpleNamespace
    from worktree_manager.production_picker.picker_tui import data_local
    from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
        PickerScreenMaintenanceActionsMixin,
    )

    monkeypatch.setattr(data_local, "LOCAL", ("raw-host", "Win"))
    cfg.load_config()
    cfg.clear_caches()
    screen = PickerScreenMaintenanceActionsMixin()
    screen.src = data_local
    screen.app = SimpleNamespace(result=None, exit=lambda: pytest.fail("refused action exited"))
    screen.refresh = lambda: None
    rec = {"raw": {"id": "host-row"}, "machine": "host", "env": "Win"}

    async def run():
        decision = screen._resume_decision(rec)
        assert decision["deferred"] and decision["is_local"] is None
        decision["action"] = action
        assert screen._decide(decision) is False
        assert screen.app.result is None
        assert "not ready" in screen.debug

    asyncio.run(run())


@pytest.mark.parametrize("local", [True, False])
def test_known_locality_retains_normal_decision_and_exit(files, local):
    from types import SimpleNamespace
    from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
        PickerScreenMaintenanceActionsMixin,
    )

    screen = PickerScreenMaintenanceActionsMixin()
    screen.src = SimpleNamespace(is_local=lambda *a: local)
    exits = []
    screen.app = SimpleNamespace(result=None, exit=lambda: exits.append(True))
    decision = screen._resume_decision({"raw": {"id": "row"}, "machine": "host", "env": "Win"})
    assert decision["is_local"] is local and "deferred" not in decision
    assert screen._decide(decision) is True
    assert exits == [True] and screen.app.result is decision


@pytest.fixture
def mapping_screen(files, monkeypatch):
    import queue
    from worktree_manager.production_picker.picker_tui import data_ssh, engine_pivots

    class Screen(engine_pivots.PickerScreenPivotsMixin):
        def __init__(self):
            self.src = data_ssh
            self._setup_epoch = 1
            self._config_cache_session = cfg.ConfigCacheSession(ttl=60)
            self.callbacks = queue.Queue()
            self._pivot_runtimes = {}
            self.selection = {"preserve-this-row"}
            self.debug = ""

        def _load_config_cache_scope(self):
            return self._config_cache_session.scope()

        def _apply_from_worker(self, callback):
            self.callbacks.put(callback)

        def refresh(self):
            pass

        def complete(self):
            self.callbacks.get(timeout=5)()

    threads = []
    original = threading.Thread

    def track(*args, **kwargs):
        thread = original(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(engine_pivots.threading, "Thread", track)
    yield Screen(), threads
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()


def test_real_derived_mapping_expires_and_manual_reload_preserves_selection(files, mapping_screen):
    from worktree_manager.production_picker.picker_tui.engine_runtime import PickerScreenRuntimeMixin

    _, repo, now = files
    screen, threads = mapping_screen
    path = repo / ".agent-worktrees" / "machines.yaml"
    path.write_text("machines:\n  host:\n    display_name: Before\n")
    assert screen._machine_key_map() == {}
    screen.complete()
    assert screen._machine_key_map() == {"Before": "host"}
    assert len(threads) == 1
    path.write_text("machines:\n  host:\n    display_name: After\n")
    now[0] = 60
    assert screen._machine_key_map() == {}
    screen.complete()
    assert screen._machine_key_map() == {"After": "host"}
    PickerScreenRuntimeMixin._invalidate_setup_reload_caches(screen)
    path.write_text("machines:\n  host:\n    display_name: Reloaded\n")
    assert screen._machine_key_map() == {}
    screen.complete()
    assert screen._machine_key_map() == {"Reloaded": "host"}
    assert len(threads) == 3
    assert screen.selection == {"preserve-this-row"}


@pytest.mark.parametrize("supersede", ["source", "setup", "manual"])
def test_derived_mapping_drops_obsolete_completion(files, mapping_screen, monkeypatch, supersede):
    from worktree_manager.production_picker.picker_tui.engine_runtime import PickerScreenRuntimeMixin

    _, repo, _ = files
    screen, _ = mapping_screen
    path = repo / ".agent-worktrees" / "machines.yaml"
    path.write_text("machines:\n  host:\n    display_name: Obsolete\n")
    entered, release = threading.Event(), threading.Event()
    read_bytes = Path.read_bytes

    def blocked(p):
        if p == path:
            entered.set()
            assert release.wait(5)
        return read_bytes(p)

    monkeypatch.setattr(Path, "read_bytes", blocked)
    screen._machine_key_map()
    try:
        assert entered.wait(5)
        if supersede == "source":
            screen.src = object()
        elif supersede == "setup":
            screen._setup_epoch += 1
        else:
            PickerScreenRuntimeMixin._invalidate_setup_reload_caches(screen)
    finally:
        release.set()
    screen.complete()
    assert screen._mkey_map is None
    assert not screen._mkey_map_inflight
    assert screen.selection == {"preserve-this-row"}


def test_mapping_error_is_unknown_not_valid_empty_and_retries_next_generation(files, mapping_screen):
    _, repo, now = files
    screen, threads = mapping_screen
    path = repo / ".agent-worktrees" / "machines.yaml"
    path.write_text("broken: mapping\n")
    screen._machine_key_map()
    screen.complete()
    assert screen._mkey_map is None and screen._mkey_map_failed
    assert screen._machine_key_map() == {}
    assert "unavailable" in screen.debug
    assert len(threads) == 1
    path.write_text("machines:\n  host:\n    display_name: Repaired\n")
    now[0] = 60
    screen._machine_key_map()
    screen.complete()
    assert screen._machine_key_map() == {"Repaired": "host"}
    assert len(threads) == 2


def test_mapping_error_does_not_replace_in_progress_setup_status(files, mapping_screen):
    _, repo, _ = files
    screen, _ = mapping_screen
    screen._busy_label, screen.debug = "Loading", "loading"
    (repo / ".agent-worktrees" / "machines.yaml").write_text("broken: mapping\n")
    screen._machine_key_map()
    screen.complete()
    screen._machine_key_map()
    assert screen.debug == "loading" and screen._busy_label == "Loading"
    screen._busy_label = None
    screen._machine_key_map()
    assert "unavailable" in screen.debug


def refusal_screen():
    from types import SimpleNamespace
    from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
        PickerScreenMaintenanceActionsMixin,
    )
    from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
        PickerScreenWorktreeActionsMixin,
    )

    class Screen(PickerScreenWorktreeActionsMixin, PickerScreenMaintenanceActionsMixin):
        def refresh(self):
            pass

        def _src_local(self):
            return ("host", "Win")

    def unready(*args):
        raise RuntimeError("Locality is not ready")

    screen = Screen()
    screen.src = SimpleNamespace(LOCAL=("host", "Win"), is_local=unready)
    screen.app = SimpleNamespace(result=None, exit=lambda: pytest.fail("refusal exited"))
    screen.debug = ""
    screen.workers = []
    screen._run_bg = lambda *a, **kw: screen.workers.append(a)
    return screen


def test_refusal_guard_headed_launch_does_not_start_worker(files):
    from worktree_manager.production_picker.picker_tui.headed_actions import open_worktree_cli_headed

    screen = refusal_screen()
    open_worktree_cli_headed(screen, {"raw": {"id": "row"}, "machine": "host", "env": "Win"})
    assert screen.workers == []
    assert screen.app.result is None
    assert "not ready" in screen.debug


@pytest.mark.parametrize("loaded", [False, True])
@pytest.mark.parametrize("project", [None, "example"])
def test_refusal_guard_internal_resume_never_mutates_result_or_reports_success(files, loaded, project):
    screen = refusal_screen()
    row = {"raw": {"id": "row"}, "machine": "host", "env": "Win", "source_kind": "machine-ssh"}
    screen._find_internal_worktree = lambda *a: (row if loaded else None, None)
    success, message = screen._resume_embodied_worktree_cli(
        "row", machine="host", env="Win", source_kind="machine-ssh", project=project,
    )
    assert success is False and "not ready" in message
    assert screen.app.result is None and screen.workers == []


def test_refusal_guard_pre_embodiment_does_not_dispatch(files):
    screen = refusal_screen()
    screen._current_tab = lambda: {"source_kind": "machine-ssh", "env": "Win"}
    success, message = screen._embody_task_cli({
        "id": "task", "machine": "host", "env": "Win", "source_kind": "machine-ssh",
    })
    assert success is False and "not ready" in message
    assert screen.app.result is None and screen.workers == []


@pytest.mark.parametrize("action", ["headed", "resume", "embody", "decide"])
def test_warm_config_cold_roster_refuses_real_launch(files, monkeypatch, action):
    import subprocess
    from worktree_manager.production_picker.picker_tui import data_local
    from worktree_manager.production_picker.picker_tui.headed_actions import open_worktree_cli_headed

    _, repo, _ = files
    (repo / ".agent-worktrees" / "machines.yaml").write_text(
        "machines:\n  remote:\n    hostname: Other-Host\n    execution_platform: windows\n"
    )
    monkeypatch.setattr(data_local, "LOCAL", ("Generated-Host", "Win"))
    monkeypatch.setattr(data_local, "local_execution_platform", lambda: "windows")
    assert cfg.load_config().machine == "host"
    screen = refusal_screen()
    screen.src = data_local
    row = {"raw": {"id": "row"}, "machine": "host", "env": "Win", "source_kind": "machine-ssh"}
    screen._find_internal_worktree = lambda *a: (row, None)
    screen._current_tab = lambda: {"source_kind": "machine-ssh", "env": "Win"}

    def unexpected(*args, **kwargs):
        pytest.fail("unknown locality performed I/O, dispatch or launched a subprocess")

    monkeypatch.setattr(Path, "read_bytes", unexpected)
    monkeypatch.setattr(Path, "stat", unexpected)
    monkeypatch.setattr(cfg.engine_client, "get_value", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    generation = cfg._file_generation
    generation.lock.acquire()
    try:
        async def run():
            if action == "headed":
                open_worktree_cli_headed(screen, row)
                assert "not ready" in screen.debug
            elif action == "resume":
                ok, detail = screen._resume_embodied_worktree_cli(
                    "row", machine="host", env="Win", source_kind="machine-ssh",
                )
                assert not ok and "not ready" in detail
            elif action == "embody":
                ok, detail = screen._embody_task_cli({
                    "id": "task", "machine": "host", "env": "Win", "source_kind": "machine-ssh",
                })
                assert not ok and "not ready" in detail
            else:
                decision = screen._resume_decision(row)
                assert decision.get("deferred") and decision["is_local"] is None
                assert screen._decide(decision) is False
            assert screen.workers == [] and screen.app.result is None

        asyncio.run(run())
    finally:
        generation.lock.release()
        monkeypatch.undo()


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("actual,env", [("windows", "Win"), ("linux", "Linux"), ("wsl", "WSL")])
def test_existing_mapping_worker_prepares_locality_roster(
    files, mapping_screen, monkeypatch, scoped, actual, env,
):
    from worktree_manager.production_picker.picker_tui import data_local

    _, repo, _ = files
    screen, _ = mapping_screen
    (repo / ".agent-worktrees" / "machines.yaml").write_text(
        "machines:\n  remote:\n    hostname: Other-Host\n"
        + (f"    execution_platform: {actual}\n" if scoped else "")
    )
    monkeypatch.setattr(data_local, "LOCAL", ("Generated-Host", env))
    monkeypatch.setattr(data_local, "local_execution_platform", lambda: actual)
    cfg.load_config()

    async def unready():
        with pytest.raises(RuntimeError, match="not ready"):
            data_local.is_local("host", env)

    asyncio.run(unready())
    screen._prewarm_machine_key_map()
    screen.complete()
    assert screen._mkey_map_error is None

    async def ready():
        assert data_local.is_local("host", env) is (not scoped and actual != "wsl")
        assert data_local.is_local("remote", env) is False

    asyncio.run(ready())
