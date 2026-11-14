"""Native cache-GC fixtures; never inspect or clean an installed cache."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil import python_cache_collect as collector
from agent_procutil import python_cache_files as files
from agent_procutil import python_cache_store as store


@pytest.fixture
def local(tmp_path, monkeypatch):
    base = store.native(tmp_path / "cache")
    root = store.native(tmp_path / "runtime")
    monkeypatch.setattr(store, "user_cache", lambda: base)
    return root, base


def allocate(root, version="1.0", identity="fixture"):
    target = root / "versions" / version
    target.mkdir(parents=True, exist_ok=True)
    prefix = store.prepare(root, target, identity, read_record=lambda path: path.read_bytes())
    return target, prefix


@contextmanager
def absent(_slot, *, check):
    yield ""


def bounded(root, retirement=absent, **changes):
    return collector.collect(
        root, retirement=retirement,
        budget=collector.Budget(**({"seconds": 10} | changes)),
    )


def drain(root, **changes):
    reports = []
    for _ in range(100):
        report = bounded(root, **changes)
        reports.append(report)
        assert not report.reasons, report
        if not report.pending:
            return reports
    pytest.fail(f"cache collection did not converge: {reports[-1]}")


def test_large_tree_converges_with_bounded_discovery_and_exact_successful_bytes(local):
    root, _ = local
    target, prefix = allocate(root)
    expected = 0
    for directory in range(5):
        parent = prefix / str(directory) / "nested"
        parent.mkdir(parents=True)
        for index in range(9):
            payload = b"x" * (index + 1)
            (parent / f"module{index}.pyc").write_bytes(payload)
            expected += len(payload)
    shutil.rmtree(target)
    reports = drain(root, entries=7, namespaces=1, bytes=20)
    assert len(reports) > 5
    assert all(report.entries <= 7 and report.bytes <= 20 for report in reports)
    assert sum(report.bytes for report in reports) == expected
    assert sum(report.files for report in reports) == 45
    assert sum(report.completed for report in reports) == 1
    assert not prefix.parent.exists()
    with sqlite3.connect(prefix.parent.parent / "catalog.sqlite") as database:
        assert database.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM progress WHERE name LIKE 'tree:%'").fetchone()[0] == 0


def test_retained_namespace_does_not_starve_later_retirement(local):
    root, _ = local
    retained, first = allocate(root, "1.0")
    retired, second = allocate(root, "2.0")
    (first / "retained.pyc").write_bytes(b"keep")
    (second / "retired.pyc").write_bytes(b"remove")
    shutil.rmtree(retired)

    @contextmanager
    def eligibility(slot, *, check):
        yield "runtime-present" if slot == retained.name else ""

    reports = [bounded(root, eligibility, namespaces=1, entries=2) for _ in range(15)]
    assert all(item.namespaces <= 1 and item.entries <= 2 for item in reports)
    assert first.is_dir() and not second.parent.exists()
    assert sum(item.files for item in reports) == 1
    assert any("runtime-present" in item.reasons for item in reports)


@pytest.mark.parametrize("kind", ["text", "directory-link", "file-link", "hardlink"])
def test_unknown_or_linked_content_is_preserved(local, tmp_path, kind):
    root, _ = local
    target, prefix = allocate(root)
    outside = store.native(tmp_path / "outside")
    outside.mkdir()
    sentinel = outside / "sentinel.pyc"
    sentinel.write_bytes(b"retain")
    unwanted = prefix / ("evidence.txt" if kind == "text" else "linked.pyc")
    if kind == "text":
        unwanted.write_bytes(b"retain")
    elif kind == "hardlink":
        os.link(sentinel, unwanted)
    else:
        try:
            unwanted.symlink_to(outside if kind == "directory-link" else sentinel,
                                target_is_directory=kind == "directory-link")
        except OSError as error:
            pytest.skip(f"native symbolic links unavailable: {error}")
    shutil.rmtree(target)
    report = bounded(root)
    assert "unsafe-or-unavailable" in report.reasons
    assert report.files == report.bytes == 0
    assert os.path.lexists(unwanted) and sentinel.read_bytes() == b"retain"


def test_interruption_after_rmdir_resumes_missing_stack_frame(local, monkeypatch):
    root, _ = local
    target, prefix = allocate(root)
    directory = prefix / "nested"
    directory.mkdir()
    (directory / "module.pyc").write_bytes(b"cache")
    shutil.rmtree(target)
    # Save the deep continuation before any deletion.
    bounded(root, entries=2)
    original = files.Entry.remove

    def interrupted(entry):
        original(entry)
        if entry.path.name == "nested":
            raise OSError("process interrupted after filesystem progress")

    with monkeypatch.context() as patch:
        patch.setattr(files.Entry, "remove", interrupted)
        assert "unsafe-or-unavailable" in bounded(root).reasons
    assert not directory.exists()
    drain(root, entries=2)
    assert not prefix.parent.exists()


def test_finalization_recovers_after_owner_removal(local, monkeypatch):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    bounded(root)
    original = files.Entry.remove

    def interrupted(entry):
        original(entry)
        if entry.path.name == "owner.json":
            raise OSError("interrupted finalization")

    with monkeypatch.context() as patch:
        patch.setattr(files.Entry, "remove", interrupted)
        assert "unsafe-or-unavailable" in bounded(root).reasons
    assert not (prefix.parent / "owner.json").exists()
    drain(root)
    assert not prefix.parent.exists()


@pytest.mark.parametrize("phase", ["probe", "record-read", "owner-remove"])
def test_slow_finalization_stops_before_the_next_mutation(local, monkeypatch, phase):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    bounded(root)
    clock = [0.0]
    original_names, original_read, original_remove = files.Entry.names, files.Entry.read, files.Entry.remove

    @contextmanager
    def names(entry):
        with original_names(entry) as iterator:
            def observed():
                yield from iterator
                clock[0] = 1.1

            yield observed() if phase == "probe" and entry.path == prefix.parent else iterator

    def read(entry, limit):
        result = original_read(entry, limit)
        if phase == "record-read" and entry.path.name == "owner.json":
            clock[0] = 1.1
        return result

    def remove(entry):
        original_remove(entry)
        if phase == "owner-remove" and entry.path.name == "owner.json":
            clock[0] = 1.1

    with monkeypatch.context() as patch:
        patch.setattr(files.Entry, "names", names)
        patch.setattr(files.Entry, "read", read)
        patch.setattr(files.Entry, "remove", remove)
        patch.setattr(collector.time, "monotonic", lambda: clock[0])
        report = bounded(root, seconds=1)
    assert report.exhausted and report.pending and report.completed == 0
    assert prefix.parent.is_dir()
    assert (prefix.parent / "owner.json").exists() == (phase != "owner-remove")
    drain(root)
    assert not prefix.parent.exists()


def test_replaced_continuation_directory_is_not_adopted(local):
    root, _ = local
    target, prefix = allocate(root)
    nested = prefix / "nested"
    nested.mkdir()
    (nested / "module.pyc").write_bytes(b"original")
    shutil.rmtree(target)
    bounded(root, entries=2)
    nested.rename(prefix / "original")
    nested.mkdir()
    sentinel = nested / "replacement.pyc"
    sentinel.write_bytes(b"keep")
    report = bounded(root)
    assert "unsafe-or-unavailable" in report.reasons
    assert sentinel.read_bytes() == b"keep"


def test_tampered_owner_or_oversized_catalog_never_grants_deletion(local):
    root, _ = local
    target, prefix = allocate(root)
    sentinel = prefix / "module.pyc"
    sentinel.write_bytes(b"keep")
    shutil.rmtree(target)
    owner = prefix.parent / "owner.json"
    expected = owner.read_bytes()
    owner.write_bytes(expected.replace(b'"version": 1', b'"version": 9'))
    assert "unsafe-or-unavailable" in bounded(root).reasons
    owner.write_bytes(expected)
    with sqlite3.connect(prefix.parent.parent / "catalog.sqlite") as database:
        database.execute("UPDATE entries SET owner = ?", (b"x" * 17000,))
    assert "unsafe-or-unavailable" in bounded(root).reasons
    assert sentinel.read_bytes() == b"keep"


def test_extra_namespace_content_blocks_finalization(local):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    bounded(root)
    sentinel = prefix.parent / "evidence.txt"
    sentinel.write_bytes(b"keep")
    report = bounded(root)
    assert "unsafe-or-unavailable" in report.reasons
    assert sentinel.read_bytes() == b"keep"
    assert (prefix.parent / "owner.json").is_file()


def test_one_entry_budget_is_rejected_before_collection_and_minimum_converges(local):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    for _ in range(2):
        with pytest.raises(ValueError, match="at least two entry probes"):
            bounded(root, entries=1)
        assert (prefix.parent / "owner.json").is_file()
    drain(root, entries=2)
    assert not prefix.parent.exists()


def test_slow_continuation_rebuild_stops_between_ancestor_opens(local, monkeypatch):
    root, _ = local
    target, prefix = allocate(root)
    nested = prefix / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "module.pyc").write_bytes(b"cache")
    shutil.rmtree(target)
    bounded(root, entries=2)
    clock = [0.0]
    opened = []
    original = files.child

    @contextmanager
    def slow(parent, name, **options):
        with original(parent, name, **options) as entry:
            if parent.path.is_relative_to(prefix.parent) and name in ("bytecode", "a", "b"):
                clock[0] += 0.6
                opened.append(name)
            yield entry

    with monkeypatch.context() as patch:
        patch.setattr(files, "child", slow)
        patch.setattr(collector.time, "monotonic", lambda: clock[0])
        report = bounded(root, seconds=1)
    assert report.exhausted and report.pending
    assert opened == ["bytecode", "a"]
    assert (nested / "module.pyc").read_bytes() == b"cache"
    drain(root, entries=4)


@pytest.mark.parametrize("phase", ["ancestors", "private-root"])
def test_slow_cache_admission_stops_before_catalog_setup(local, monkeypatch, phase):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    clock = [0.0]
    checked = []
    original = collector.permissions.check

    def slow(path, **options):
        original(path, **options)
        checked.append(path)
        if phase == "ancestors":
            clock[0] += 0.6
        elif options.get("private"):
            clock[0] += 1.1

    def unexpected(*args, **kwargs):
        pytest.fail("expired ancestor admission must not start catalog setup")

    with monkeypatch.context() as patch:
        patch.setattr(collector.permissions, "check", slow)
        patch.setattr(collector.catalog, "connection", unexpected)
        patch.setattr(collector.time, "monotonic", lambda: clock[0])
        report = bounded(root, seconds=1)
    assert report.exhausted and report.pending and report.namespaces == 0
    if phase == "ancestors":
        assert len(checked) == 2
    assert prefix.is_dir()
    drain(root)


def test_slow_catalog_metadata_check_stops_before_sqlite_open(local, monkeypatch):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    clock = [0.0]
    checked = []
    original = collector.permissions.check_file

    def slow(path):
        original(path)
        checked.append(path.name)
        clock[0] += 1.1

    def unexpected(*args, **kwargs):
        pytest.fail("expired metadata admission must not open SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(collector.permissions, "check_file", slow)
        patch.setattr(collector.catalog.sqlite3, "connect", unexpected)
        patch.setattr(collector.time, "monotonic", lambda: clock[0])
        report = bounded(root, seconds=1)
    assert report.exhausted and report.pending and report.namespaces == 0
    assert checked == ["catalog.sqlite"]
    drain(root)


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_catalog_metadata_lookup_errors_are_not_absence(local, monkeypatch, error):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    path = prefix.parent.parent / "catalog.sqlite"
    original = Path.lstat

    def unavailable(entry, *args, **kwargs):
        if entry == path:
            raise error("metadata unavailable")
        return original(entry, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", unavailable)
        report = bounded(root)
    assert report.pending and report.namespaces == 0
    assert "catalog-unavailable" in report.reasons
    assert prefix.is_dir()
    drain(root)


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_namespace_lookup_errors_retain_committed_finalization(local, monkeypatch, error):
    root, _ = local
    target, prefix = allocate(root)
    shutil.rmtree(target)
    bounded(root)
    lstat = Path.lstat

    def unavailable(path, *args, **kwargs):
        if path == prefix.parent:
            raise error("metadata unavailable")
        return lstat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", unavailable)
        report = bounded(root)
        assert report.pending and report.completed == 0
        assert "unsafe-or-unavailable" in report.reasons
    assert prefix.parent.is_dir()
    drain(root)
    assert not prefix.parent.exists()


@pytest.mark.parametrize("location", ["owner", "continuation"])
def test_deep_json_refusal_advances_past_the_corrupt_namespace(local, location):
    root, _ = local
    targets = [allocate(root, version) for version in ("1.0", "2.0")]
    targets.sort(key=lambda pair: pair[1].parent.name)
    for target, prefix in targets:
        (prefix / "module.pyc").write_bytes(b"cache")
        shutil.rmtree(target)
    bad = targets[0][1]
    key = bad.parent.name.removeprefix("installed-")
    nested = "[" * 6000 + "0" + "]" * 6000
    with sqlite3.connect(bad.parent.parent / "catalog.sqlite") as database:
        if location == "owner":
            database.execute("UPDATE entries SET owner = ? WHERE key = ?", (nested.encode(), key))
        else:
            database.execute(
                "INSERT INTO progress(name, value) VALUES (?, ?)", ("tree:" + key, nested),
            )
    reports = [bounded(root, entries=2, namespaces=1) for _ in range(15)]
    assert bad.is_dir()
    assert not targets[1][1].parent.exists()
    assert any("unsafe-or-unavailable" in report.reasons for report in reports)


@pytest.mark.skipif(os.name == "nt", reason="POSIX private traversal and interpreter umask")
def test_actual_interpreter_group_writable_mirrors_remain_collectable(local):
    root, _ = local
    target, prefix = allocate(root)
    source = target / "fixture_module.py"
    source.write_text("answer = 42\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-X", "pycache_prefix=" + str(prefix), "-c",
         "import os,sys; os.umask(0o002); sys.path.insert(0,sys.argv[1]); import fixture_module",
         str(target)],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    compiled = list(prefix.rglob("fixture_module.*.pyc"))
    assert len(compiled) == 1
    assert compiled[0].parent.stat().st_mode & 0o020
    expected = sum(path.stat().st_size for path in prefix.rglob("*.pyc"))
    shutil.rmtree(target)
    reports = drain(root, entries=4)
    assert sum(report.bytes for report in reports) == expected


def test_native_anchor_removes_exact_bytecode_and_owner(local):
    root, _ = local
    _, prefix = allocate(root)
    (prefix / "module.pyc").write_bytes(b"cache")
    with files.installation(prefix.parent.parent) as anchor:
        with files.child(anchor, prefix.parent.name, delete=True) as namespace:
            collector._record(namespace, (prefix.parent / "owner.json").read_bytes())
            with files.child(namespace, "bytecode", delete=True) as bytecode:
                with bytecode.names() as iterator:
                    assert next(iterator).name == "module.pyc"
                with files.child(bytecode, "module.pyc", delete=True) as compiled:
                    compiled.remove()
                bytecode.remove()
            collector._record(namespace, (prefix.parent / "owner.json").read_bytes(), optional=True)
            namespace.remove()
    assert not prefix.parent.exists()


@pytest.fixture
def runtime():
    source = Path(__file__).resolve().parents[2] / "versioned-runtime" / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("_cache_runtime_fixture", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_actual_gc_reclaims_retired_cache_and_preserves_current_keep_and_references(
    local, runtime,
):
    root, _ = local
    prefixes = {}
    for version in ("1.0", "2.0", "3.0", "4.0"):
        _, prefix = allocate(root, version)
        (prefix / "module.pyc").write_bytes(b"cache")
        prefixes[version] = prefix
    runtime.activate(root, "4.0")
    runtime._leases.replace_runtime_references(
        root, "fixture/reference", ["2.0"], expected=[],
    )
    assert runtime.gc(root, keep=["3.0"]) == ["1.0"]
    for _ in range(5):
        assert runtime.gc(root, keep=["3.0"]) == []
    assert not prefixes["1.0"].parent.exists()
    assert all(prefixes[version].is_dir() for version in ("2.0", "3.0", "4.0"))


def test_gc_cache_cannot_race_absent_slot_construction(local, runtime):
    root, _ = local
    target, prefix = allocate(root)
    (prefix / "module.pyc").write_bytes(b"keep")
    shutil.rmtree(target)
    with runtime.construction_lease(root, target.name):
        runtime.gc(root)
        assert (prefix / "module.pyc").read_bytes() == b"keep"
    for _ in range(5):
        runtime.gc(root)
    assert not prefix.parent.exists()


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_runtime_lookup_errors_never_authorize_cache_removal(local, runtime, monkeypatch, error):
    root, _ = local
    target, prefix = allocate(root)
    sentinel = prefix / "module.pyc"
    sentinel.write_bytes(b"keep")
    lstat = Path.lstat

    def unavailable(path, *args, **kwargs):
        if path == target:
            raise error("metadata unavailable")
        return lstat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", unavailable)
        runtime._runtime_component("versioned_runtime_cache").maintain(
            root, cleanup_lease=runtime._cleanup_lease, protected=lambda **kwargs: set(),
            version_dir=runtime.version_dir, windows_owned_file=runtime._leases.windows_owned_file,
        )
    assert sentinel.read_bytes() == b"keep"


def test_maintenance_failure_does_not_change_successful_gc(local, runtime, monkeypatch, capsys):
    root, _ = local
    target, prefix = allocate(root)
    component = runtime._runtime_component("versioned_runtime_cache")

    def unavailable(**kwargs):
        raise OSError("private filesystem error")

    monkeypatch.setattr(component, "_collector", unavailable)
    assert runtime.gc(root) == [target.name]
    assert not target.exists() and prefix.is_dir()
    message = capsys.readouterr().err
    assert "maintenance unavailable (OSError)" in message
    assert "private filesystem error" not in message and str(root) not in message


@pytest.mark.parametrize("writable", ["archive", "ancestor"])
def test_writable_lifecycle_archive_is_refused_before_import(tmp_path, writable):
    if writable == "ancestor" and os.name == "nt":
        pytest.skip("POSIX archive ancestry; Windows retains the non-delete-shared file handle")
    repository = Path(__file__).resolve().parents[3]
    library = repository / "libs" / "versioned-runtime"
    code = store.native(tmp_path / "source")
    code.mkdir()
    for name in ("versioned_runtime_cache.py", "versioned_runtime_leases.py"):
        shutil.copyfile(library / name, code / name)
    sentinel = code / "unexpected-import.txt"
    archive = code / "daemon_instance_runtime.pyz"
    payload = (
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('unexpected import')\n"
        "python_cache_collect = object()\n"
    )
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("agent_procutil/__init__.py", payload)
    if os.name == "nt":
        grant = subprocess.run(
            ["icacls", str(archive), "/grant", "*S-1-1-0:(W)"],
            capture_output=True, timeout=15, **no_window_kwargs(),
        )
        assert grant.returncode == 0, grant.stderr
    elif writable == "archive":
        archive.chmod(0o666)
    else:
        archive.chmod(0o644)
        code.chmod(0o777)
    script = """
import importlib.util, pathlib, sys
def load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(sys.argv[1]) / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
owner = load('versioned_runtime_leases')
helper = load('versioned_runtime_cache')
try:
    helper._collector(windows_owned_file=owner.windows_owned_file)
except PermissionError:
    print('refused')
else:
    raise SystemExit('unsafe archive was admitted')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(code)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused" and not sentinel.exists()


@pytest.mark.parametrize("probe", ["cmdline", "image", "recorded"])
def test_cache_retirement_stops_process_probes_at_its_deadline(local, runtime, monkeypatch, probe):
    root, _ = local
    target, prefix = allocate(root)
    sentinel = prefix / "module.pyc"
    sentinel.write_bytes(b"retain")
    shutil.rmtree(target)
    clock = [0.0]
    monkeypatch.setattr(collector.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime, "_iter_all_pids", lambda **kwargs: list(range(1, 101)))
    calls = []

    def slow(pid):
        calls.append(pid)
        clock[0] += 2
        return None

    monkeypatch.setattr(runtime, "_pid_cmdline_argv0", slow if probe == "cmdline" else lambda pid: None)
    monkeypatch.setattr(runtime, "_pid_image_path", slow if probe == "image" else lambda pid: None)
    if probe == "recorded":
        (root / runtime.RUNNING_VERSION_FILE).write_text(
            json.dumps([{"pid": pid, "version": target.name} for pid in range(1, 101)]),
            encoding="utf-8",
        )
        monkeypatch.setattr(runtime, "_pid_alive", slow)
    reports = []

    def collect(*args, **kwargs):
        result = collector.collect(*args, **kwargs)
        reports.append(result)
        return result

    component = runtime._runtime_component("versioned_runtime_cache")
    monkeypatch.setattr(component, "_collector", lambda **kwargs: SimpleNamespace(collect=collect))
    component.maintain(
        root, cleanup_lease=runtime._cleanup_lease,
        protected=lambda **kwargs: runtime._gc_protected_versions(root, None, True, "venv", **kwargs),
        version_dir=runtime.version_dir,
    )
    assert len(calls) == 1
    assert len(reports) == 1 and reports[0].exhausted and reports[0].pending
    assert reports[0].files == 0 and sentinel.read_bytes() == b"retain"


@pytest.mark.parametrize("protect_pids", [False, True])
@pytest.mark.parametrize("slow", ["references", "selector", "claim", "versions"])
def test_cache_protection_metadata_stops_before_the_next_operation(
    local, runtime, monkeypatch, protect_pids, slow,
):
    root, _ = local
    target, prefix = allocate(root)
    sentinel = prefix / "module.pyc"
    sentinel.write_bytes(b"retain")
    shutil.rmtree(target)
    clock, calls, reports = [0.0], [], []
    monkeypatch.setattr(collector.time, "monotonic", lambda: clock[0])

    def operation(name, result):
        def run(*args, **kwargs):
            assert clock[0] == 0.0, "another protection operation started after expiry"
            calls.append(name)
            if name == slow:
                clock[0] = 2.0
            return result
        return run

    monkeypatch.setattr(runtime._leases, "read_runtime_references", operation("references", {}))
    monkeypatch.setattr(runtime, "current_version", operation("selector", None))
    monkeypatch.setattr(runtime, "read_dev_claim", operation("claim", None))
    monkeypatch.setattr(runtime, "list_versions", operation("versions", []))
    monkeypatch.setattr(runtime, "_iter_all_pids", lambda **kwargs: [])

    def collect(*args, **kwargs):
        result = collector.collect(*args, **kwargs)
        reports.append(result)
        return result

    component = runtime._runtime_component("versioned_runtime_cache")
    monkeypatch.setattr(component, "_collector", lambda **kwargs: SimpleNamespace(collect=collect))
    component.maintain(
        root, cleanup_lease=runtime._cleanup_lease,
        protected=lambda **kwargs: runtime._gc_protected_versions(
            root, None, protect_pids, "venv", **kwargs,
        ),
        version_dir=runtime.version_dir,
    )
    expected_expiry = slow != "versions" or protect_pids
    assert len(reports) == 1 and reports[0].exhausted == expected_expiry
    if expected_expiry:
        assert calls[-1] == slow and reports[0].pending
        assert sentinel.read_bytes() == b"retain"


@pytest.mark.skipif(sys.platform == "darwin", reason="this fixture uses the Windows/Linux cache override")
def test_gc_from_retained_helper_bundle_needs_no_acquisition_source(local, monkeypatch):
    root, base = local
    target, prefix = allocate(root)
    sentinel = prefix / "module.pyc"
    sentinel.write_bytes(b"retain")
    source = Path(__file__).resolve().parents[3] / "plugins/agent-worktrees/scripts"
    acquisition = base.parent / "acquisition"
    collector.permissions.mkdir(acquisition)
    for path in [*source.glob("versioned_runtime*.py"), source / "runtime_launcher.py",
                 source / "daemon_instance_runtime.pyz"]:
        shutil.copyfile(path, acquisition / path.name)
    spec = importlib.util.spec_from_file_location("_cache_retained_owner", acquisition / "versioned_runtime.py")
    owner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, owner)
    spec.loader.exec_module(owner)
    owner.retain_runtime_helpers(target, complete=False)
    owner.retain_runtime_helpers(target, complete=True)
    shutil.rmtree(acquisition)
    script = target / ".runtime-bootstrap" / "versioned_runtime.py"
    environment = dict(os.environ)
    environment["LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME"] = str(base)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(script), "--root", str(root), "--json",
         "gc", "--keep", target.name],
        env=environment, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"removed": []}
    assert "maintenance unavailable" not in result.stderr
    assert sentinel.read_bytes() == b"retain"


@pytest.mark.skipif(sys.platform == "darwin", reason="this fixture uses the Windows/Linux cache override")
def test_standalone_gc_imports_its_shipped_archive_without_site_packages(local):
    root, base = local
    target, prefix = allocate(root)
    (prefix / "module.pyc").write_bytes(b"cache")
    repository = Path(__file__).resolve().parents[3]
    sources = repository / "plugins" / "agent-worktrees" / "scripts"
    selected = base.parent / "shipped"
    collector.permissions.mkdir(selected)
    # Qualify shipped bytes with fixture-owned ACLs, not an ambient checkout DACL.
    for source in sources.glob("versioned_runtime*.py"):
        shutil.copyfile(source, selected / source.name)
    shutil.copyfile(sources / "daemon_instance_runtime.pyz", selected / "daemon_instance_runtime.pyz")
    script = selected / "versioned_runtime.py"
    environment = dict(os.environ)
    environment["LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME"] = str(base)
    reports = []
    for index in range(4):
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(script), "--root", str(root), "--json", "gc"],
            env=environment, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"removed": [target.name] if index == 0 else []}
        assert "maintenance unavailable" not in result.stderr
        reports.extend(
            json.loads(line.split("gc: python-cache ", 1)[1])
            for line in result.stderr.splitlines() if line.startswith("gc: python-cache ")
        )
    assert sum(report["files"] for report in reports) == 1
    assert sum(report["bytes"] for report in reports) == 5
    assert not prefix.parent.exists()
