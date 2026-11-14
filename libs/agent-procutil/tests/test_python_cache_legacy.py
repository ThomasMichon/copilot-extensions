"""Disposable legacy-cache fixtures; never collect a host installation."""
from __future__ import annotations

from contextlib import contextmanager
import base64
import importlib.util
import json
import marshal
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_procutil import python_cache_files as files
from agent_procutil import no_window_kwargs
from agent_procutil import python_cache as policy
from agent_procutil import python_cache_legacy as legacy
from agent_procutil import python_cache_store as store
from python_cache_pyc_fixtures import BYTECODE, FILENAME, code_record, native_format


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = store.native(tmp_path / "runtime")
    base = store.native(tmp_path / "os-cache")
    monkeypatch.setattr(store, "user_cache", lambda: base)
    return root, base


def prefix(root, number=1, *, installed=False):
    name = f"installed-{number:064x}" if installed else f"unsealed-{number:032x}"
    return root / "cache" / "python" / name


def compiled(root, slot="1.0", relative="module.py", *, number=1, filename=None, installed=False):
    namespace = prefix(root, number, installed=installed)
    source = root / "versions" / slot / relative
    directory = namespace.joinpath(*legacy._mirror_parts(source.parent))
    directory.mkdir(parents=True, exist_ok=True)
    version = native_format()
    tag = str(sys.implementation.cache_tag) if version else "cpython-313"
    path = directory / (source.stem + "." + tag + ".pyc")
    data = (
        importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(
            compile("def nested(): return 42\n", filename or str(source), "exec"),
        )
        if version else code_record(filename=filename or str(source))
    )
    path.write_bytes(data)
    return path


@contextmanager
def absent(slot, *, check):
    check()
    yield ""


def collect(root, retirement=absent, **changes):
    return legacy.collect(
        root, retirement=retirement, budget=legacy.Budget(**({"seconds": 20} | changes)),
    )


def test_exact_retired_source_relief_preserves_other_and_unclassified_content(local):
    root, _ = local
    retired = compiled(root, "1.0", "package/retired.py")
    expected = retired.stat().st_size
    current = compiled(root, "2.0", "current.py")
    dev = compiled(root, "dev", "development.py")
    other_source = root.parent / "other-runtime" / "versions" / "1.0" / "other.py"
    other = compiled(root, "1.0", "different.py", filename=str(other_source))
    evidence = retired.parent / "evidence.txt"
    evidence.write_bytes(b"retain evidence")
    base = prefix(root) / "base-interpreter.pyc"
    base.write_bytes(b"retain base interpreter")

    @contextmanager
    def eligibility(slot, *, check):
        yield "runtime-present" if slot == "2.0" else ""

    result = collect(root, eligibility)
    assert not retired.exists()
    assert all(path.is_file() for path in (current, dev, other, evidence, base))
    assert result.files == 1 and result.bytes == expected
    assert result.inspected_bytes >= expected
    assert result.reasons["runtime-present"] == 1
    assert result.reasons["legacy-unknown-slot"] == 1
    assert "legacy-unsafe-or-unavailable" in result.reasons
    assert prefix(root).is_dir()  # No whole-prefix process-visibility inference.


@pytest.mark.parametrize("installed", [False, True])
def test_large_tree_resumes_and_counts_successful_removals_only(local, installed):
    root, _ = local
    paths = [
        compiled(root, "1.0", f"package{index // 7}/module{index}.py",
                 installed=installed) for index in range(35)
    ]
    expected = sum(path.stat().st_size for path in paths)
    reports = []
    for _ in range(100):
        report = collect(root, entries=4, namespaces=1, bytes=2048)
        reports.append(report)
        assert report.entries <= 4 and report.bytes <= 2048 and report.inspected_bytes <= 2048
        assert report.discovered <= 100000 and not report.errors
        if not any(path.exists() for path in paths):
            break
    assert not any(path.exists() for path in paths)
    assert len(reports) > 5
    assert sum(report.files for report in reports) == 35
    assert sum(report.bytes for report in reports) == expected
    assert any(report.exhausted and report.pending for report in reports)
    assert prefix(root, installed=installed).is_dir()


def test_cursor_advances_past_retained_namespace_and_unknown_leaf(local):
    root, _ = local
    first = compiled(root, "2.0", number=1)
    last = compiled(root, "1.0", "z_valid.py", number=2)
    unknown = compiled(root, "1.0", "a_unknown.py", number=2)
    unknown.write_bytes(b"not bytecode")

    @contextmanager
    def eligibility(slot, *, check):
        yield "runtime-present" if slot == "2.0" else ""

    reports = [collect(root, eligibility, entries=3, namespaces=1) for _ in range(20)]
    assert first.is_file() and unknown.read_bytes() == b"not bytecode"
    assert not last.exists() and sum(report.files for report in reports) == 1
    assert all(report.namespaces <= 1 and report.entries <= 3 for report in reports)


@pytest.mark.parametrize("budget", [{"discovery": 2}, {"names_bytes": 4}])
def test_incomplete_name_inventory_never_claims_fairness_or_deletes(local, budget):
    root, _ = local
    paths = [compiled(root, number=number) for number in range(3)]
    result = collect(root, **budget)
    assert all(path.is_file() for path in paths)
    assert result.exhausted and result.pending and result.files == 0
    assert "inventory-incomplete" in result.reasons or "inventory-oversized" in result.reasons
    assert result.discovered <= budget.get("discovery", 100000)
    assert result.names_bytes <= budget.get("names_bytes", 16 * 1024 * 1024)
    assert result.namespace_discovered <= budget.get("discovery", 100000)
    assert result.namespace_names_bytes <= budget.get("names_bytes", 16 * 1024 * 1024)
    assert not store.installation_root(root).exists()


@pytest.mark.parametrize("budget", [{"discovery": 64}, {"names_bytes": 128}])
def test_oversized_subtree_does_not_starve_eligible_sibling_across_updates(local, budget):
    root, _ = local
    valid = compiled(root, relative="z_valid.py")
    oversized = valid.parent / "a_unknown"
    oversized.mkdir()
    for number in range(40):
        (oversized / str(number)).write_bytes(b"retain")
    reports = [collect(root, entries=4, namespaces=1, **budget) for _ in range(12)]
    assert not valid.exists() and len(list(oversized.iterdir())) == 40
    assert sum(report.files for report in reports) == 1
    assert any("inventory-oversized" in report.reasons for report in reports)
    assert all(
        report.discovered <= budget.get("discovery", 100000)
        and report.namespace_discovered <= budget.get("discovery", 100000)
        for report in reports
    )


def test_fresh_pass_time_unavailable_inventory_does_not_starve_a_sibling(local, monkeypatch):
    root, _ = local
    valid = compiled(root, relative="z_valid.py")
    slow = valid.parent / "a_slow"
    slow.mkdir()
    for number in range(4):
        (slow / str(number)).write_bytes(b"retain")
    clock = [0.0]
    original = files.Entry.names

    @contextmanager
    def names(entry):
        with original(entry) as iterator:
            def delayed():
                for value in iterator:
                    yield value
                    clock[0] = 2.0

            yield delayed() if entry.path == slow else iterator

    monkeypatch.setattr(files.Entry, "names", names)
    monkeypatch.setattr(legacy.indexed, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    reports = []
    for _ in range(4):
        clock[0] = 0.0
        reports.append(collect(root, seconds=1, namespaces=1))
    assert not valid.exists() and len(list(slow.iterdir())) == 4
    assert any("inventory-time-unavailable" in report.reasons for report in reports)


def test_aggregate_inventory_exhaustion_resumes_a_subtree_that_fits_a_fresh_pass(local):
    root, _ = local
    paths = [compiled(root, relative=f"package{group}/module{number}.py")
             for group in range(4) for number in range(8)]
    reports = []
    for _ in range(40):
        reports.append(collect(root, entries=100, discovery=40, namespaces=1))
        if not any(path.exists() for path in paths):
            break
    assert not any(path.exists() for path in paths)
    assert sum(report.files for report in reports) == 32
    assert not any("inventory-oversized" in report.reasons for report in reports)
    assert any("inventory-incomplete" in report.reasons for report in reports)


def test_oversized_slot_inventory_does_not_starve_a_later_namespace(local):
    root, _ = local
    retained = compiled(root, number=1)
    valid = compiled(root, number=2)
    versions = retained.parent.parent
    for number in range(8):
        (versions / f"slot{number}").mkdir()
    reports = [collect(root, entries=8, discovery=16, namespaces=1) for _ in range(4)]
    assert retained.is_file() and not valid.exists()
    assert sum(report.files for report in reports) == 1
    assert any("inventory-oversized" in report.reasons for report in reports)


@pytest.mark.skipif(os.name == "nt", reason="POSIX comparison positions can contain colons")
@pytest.mark.parametrize("location", ["leaf", "slot"])
def test_refused_names_remain_comparison_only_cursors_after_interruption(local, location):
    root, _ = local
    valid = compiled(root, "1.0" if location == "leaf" else "z.0", "z_valid.py")
    parent = valid.parent if location == "leaf" else valid.parent.parent
    unwanted = parent / "a:unknown"
    if location == "leaf":
        unwanted.write_bytes(b"retain")
    else:
        unwanted.mkdir()
    first = collect(root, entries=3, namespaces=1)
    assert first.exhausted and valid.exists()
    reports = [collect(root, entries=3, namespaces=1) for _ in range(8)]
    assert not valid.exists() and unwanted.exists()
    assert sum(report.files for report in reports) == 1


@pytest.mark.parametrize("case", ["original", "swapped"])
def test_platform_case_rules_apply_to_the_complete_cache_basename(local, case):
    root, _ = local
    victim = compiled(root, relative="CamelCase.PY" if os.name == "nt" else "CamelCase.py")
    if case == "swapped":
        candidate = victim.with_name(victim.name.swapcase())
        victim.rename(candidate)
        victim = candidate
    report = collect(root)
    removed = case == "original" or os.name == "nt"
    assert victim.exists() != removed
    assert report.files == int(removed)


def test_unknown_names_count_toward_namespace_budget_without_starvation(local):
    root, _ = local
    valid = compiled(root)
    unknown = root / "cache" / "python" / "a_unknown"
    unknown.mkdir()
    sentinel = unknown / "evidence.pyc"
    sentinel.write_bytes(b"retain")
    reports = [collect(root, namespaces=1) for _ in range(4)]
    assert not valid.exists() and sentinel.read_bytes() == b"retain"
    assert all(report.namespaces == 1 for report in reports)
    assert any("legacy-unknown-namespace" in report.reasons for report in reports)


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem names may contain non-UTF-8 bytes")
def test_non_utf8_namespace_cursor_round_trips_without_starving_later_prefix(local):
    root, _ = local
    valid = compiled(root)
    unknown = root / "cache" / "python" / os.fsdecode(b"a\xff")
    unknown.mkdir()
    sentinel = unknown / "evidence.txt"
    sentinel.write_bytes(b"retain")
    first = collect(root, namespaces=1)
    assert first.files == 0 and not first.errors
    with sqlite3.connect(store.installation_root(root) / "catalog.sqlite") as database:
        encoded = database.execute(
            "SELECT value FROM progress WHERE name = ?", (legacy._CURSOR_KEY,),
        ).fetchone()[0]
        assert encoded.isascii() and "\\udcff" in encoded
    reports = [collect(root, namespaces=1) for _ in range(4)]
    assert not valid.exists() and sentinel.read_bytes() == b"retain"
    assert sum(report.files for report in reports) == 1
    assert not any(report.errors for report in reports)


@pytest.mark.parametrize("record", ["legacy", "corrupt", "oversized"])
def test_cursor_compatibility_and_corruption_do_not_block_progress(local, record):
    root, _ = local
    retained = compiled(root, "1.0", number=1)
    valid = compiled(root, "2.0", number=2)

    @contextmanager
    def eligibility(slot, *, check):
        yield "runtime-present" if slot == "1.0" else ""

    collect(root, eligibility, namespaces=1)
    with sqlite3.connect(store.installation_root(root) / "catalog.sqlite") as database:
        database.execute("DELETE FROM progress WHERE name = ?", (legacy._CURSOR_KEY,))
        if record == "legacy":
            database.execute(
                "INSERT OR REPLACE INTO progress(name, value) VALUES ('legacy-collector', ?)",
                (prefix(root).name,),
            )
        else:
            data = "[" * 1000 + "0" + "]" * 1000 if record == "corrupt" else "x" * 9000
            database.execute(
                "INSERT OR REPLACE INTO progress(name, value) VALUES (?, ?)",
                (legacy._CURSOR_KEY, data),
            )
    reports = [collect(root, eligibility, namespaces=1) for _ in range(5)]
    assert retained.is_file() and not valid.exists()
    if record != "legacy":
        assert any("legacy-cursor-reset" in report.reasons for report in reports)


def test_serialized_state_limit_backs_out_to_a_persistable_ancestor():
    names = [str(number) + "\u754c" * 80 for number in range(40)]
    stack = [["1.0", [5, 6], names[0]]]
    stack.extend([
        name, [number + 10, number + 20], names[number + 1] if number + 1 < len(names) else "",
    ] for number, name in enumerate(names))
    state = {"namespace": [1, 2], "versions": [3, 4], "after": "", "stack": stack}
    original_depth = len(stack)
    assert len(json.dumps(state).encode("utf-8")) > legacy._MAX_STATE
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE progress(name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        assert legacy._save(database, "fixture", state)
        encoded = database.execute("SELECT value FROM progress").fetchone()[0]
        restored = legacy._state(database, "fixture")
    assert len(encoded.encode("utf-8")) <= legacy._MAX_STATE
    assert 0 < len(restored["stack"]) < original_depth
    assert restored["stack"][-1][2]  # The refused child remains the parent's comparison position.


def test_oversized_interrupted_continuation_commits_rotation_to_later_prefix(local, monkeypatch):
    root, _ = local
    relative = "/".join(str(number) + "\u00e9" * 30 for number in range(10)) + "/deep.py"
    first = compiled(root, "1.0", relative, number=1)
    evidence = first.with_name("evidence.txt")
    first.rename(evidence)
    valid = compiled(root, "2.0", number=2)
    protected = [True]
    monkeypatch.setattr(legacy, "_MAX_STATE", 1024)

    @contextmanager
    def eligibility(slot, *, check):
        yield "runtime-present" if slot == "2.0" and protected[0] else ""

    for _ in range(40):
        report = collect(root, eligibility, entries=2, namespaces=1)
        if "legacy-continuation-oversized" in report.reasons:
            break
    else:
        pytest.fail("fixture did not reach its bounded continuation refusal")
    assert valid.is_file() and evidence.is_file()
    protected[0] = False
    reports = [collect(root, eligibility, entries=2, namespaces=1) for _ in range(12)]
    assert not valid.exists() and evidence.is_file()
    assert sum(report.files for report in reports) == 1


@pytest.mark.parametrize("kind", ["file-link", "directory-link", "hardlink"])
def test_links_are_preserved_and_do_not_block_later_bytecode(local, tmp_path, kind):
    root, _ = local
    victim = compiled(root, relative="a_linked.py")
    later = compiled(root, relative="z_valid.py")
    outside = store.native(tmp_path / "outside")
    outside.mkdir()
    target = outside / "sentinel.pyc"
    victim.rename(target)
    try:
        if kind == "hardlink":
            os.link(target, victim)
        else:
            victim.symlink_to(outside if kind == "directory-link" else target,
                              target_is_directory=kind == "directory-link")
    except OSError as error:
        pytest.skip(f"native symbolic links unavailable: {error}")
    result = collect(root)
    assert os.path.lexists(victim) and target.is_file() and not later.exists()
    assert result.files == 1 and result.reasons


@pytest.mark.skipif(os.name == "nt", reason="native POSIX non-private legacy permissions")
@pytest.mark.parametrize("kind", ["file", "namespace"])
def test_group_writable_legacy_content_is_not_adopted(local, kind):
    root, _ = local
    victim = compiled(root)
    path = victim if kind == "file" else prefix(root)
    path.chmod(0o664 if kind == "file" else 0o775)
    result = collect(root)
    assert victim.is_file() and result.files == 0 and result.reasons
    assert path.stat().st_mode & 0o020  # Maintenance does not repair permissions.


@pytest.mark.skipif(os.name != "nt", reason="native Windows legacy cleanup DACL")
@pytest.mark.parametrize("kind", ["file", "namespace"])
@pytest.mark.parametrize("grant", ["FR", "FA"])
def test_native_windows_readable_legacy_is_allowed_but_foreign_writes_are_refused(local, kind, grant):
    import ctypes
    from ctypes import wintypes

    root, _ = local
    victim = compiled(root)
    changed = victim if kind == "file" else prefix(root)
    owner = legacy.permissions._owner()
    user = owner._current_sids(os.getpid())[0]
    advapi, kernel = owner._apis()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{user}D:P(A;;FA;;;{user})(A;;{grant};;;AU)",
        1, ctypes.byref(descriptor), None,
    )
    try:
        assert advapi.SetFileSecurityW(str(changed), 0x80000004, descriptor)
        result = collect(root)
        assert victim.exists() == (grant == "FA")
        assert result.files == (0 if grant == "FA" else 1)
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.parametrize("private", [False, True])
def test_macos_file_only_acl_is_checked_without_weakening_mode_requirements(monkeypatch, private):
    permissions = legacy.permissions
    info = SimpleNamespace(st_mode=0o100600 if private else 0o100644, st_uid=42)
    candidate = SimpleNamespace(lstat=lambda: info)
    monkeypatch.setattr(permissions, "os", SimpleNamespace(name="posix", getuid=lambda: 42))
    monkeypatch.setattr(permissions, "sys", SimpleNamespace(platform="darwin"))
    seen = []

    def shared_file(path):
        seen.append(path)
        raise PermissionError("fixture-only extended ACL grant")

    monkeypatch.setattr(permissions, "_macos_check", shared_file)
    with pytest.raises(PermissionError, match="extended ACL"):
        permissions.check_file(candidate, private=private)
    assert seen == [candidate]


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS file-only extended ACL")
def test_native_macos_file_acl_preserves_otherwise_ordinary_legacy_bytecode(local):
    root, _ = local
    victim = compiled(root)
    victim.chmod(0o644)
    result = subprocess.run(
        ["/bin/chmod", "+a", "user:nobody allow write", str(victim)],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = collect(root)
    assert victim.is_file() and report.files == 0 and report.reasons


def test_default_budget_reclaims_material_fixture_bytes_with_finite_progress(local, record_property):
    root, _ = local
    paths = []
    for number in range(4):
        path = compiled(root, number=number)
        source = root / "versions" / "1.0" / "module.py"
        path.write_bytes(code_record(filename=str(source), consts=("x" * (1024 * 1024),)))
        paths.append(path)
    expected = sum(path.stat().st_size for path in paths)
    reports = []
    for _ in range(6):
        report = legacy.collect(root, retirement=absent)
        reports.append(report)
        if not any(path.exists() for path in paths):
            break
    assert not any(path.exists() for path in paths), reports
    assert sum(report.bytes for report in reports) == expected
    assert all(
        report.namespaces <= 128 and report.entries <= 2048 and report.discovered <= 100000
        and report.bytes <= 32 * 1024 * 1024 and report.inspected_bytes <= 32 * 1024 * 1024
        for report in reports
    )
    record_property("legacy_fixture_reclaimed_logical_bytes", expected)
    record_property("legacy_fixture_default_passes", len(reports))


def test_owner_custody_is_shared_once_per_slot_and_held_until_pass_finishes(local, monkeypatch):
    root, _ = local
    paths = [compiled(root, number=number) for number in range(4)]
    held, calls = set(), []

    @contextmanager
    def retirement(slot, *, check):
        assert slot not in held
        calls.append(slot)
        held.add(slot)
        try:
            yield ""
        finally:
            held.remove(slot)

    original = files.Entry.remove

    def remove(entry):
        assert held == {"1.0"}
        original(entry)

    monkeypatch.setattr(files.Entry, "remove", remove)
    report = collect(root, retirement)
    assert report.files == 4 and calls == ["1.0"] and not held
    assert not any(path.exists() for path in paths)


@pytest.mark.parametrize("mutation", ["write", "timestamp"])
def test_changed_bytecode_is_refused_at_mutation_boundary(local, monkeypatch, mutation):
    root, _ = local
    victim = compiled(root)
    original = files.Entry.read
    observed = []

    def changed(entry, limit):
        result = original(entry, limit)
        if entry.path == victim:
            observed.append(True)
            if mutation == "timestamp":
                os.utime(victim, ns=(entry.info.st_atime_ns, entry.info.st_mtime_ns + 10**9))
            else:
                # Windows can reject the writer while the delete-capable pin is held.
                victim.write_bytes(result + b"changed")
        return result

    monkeypatch.setattr(files.Entry, "read", changed)
    result = collect(root)
    assert observed and victim.is_file()
    if mutation == "write":
        assert victim.read_bytes().endswith(b"changed") or (
            os.name == "nt" and any(key.startswith("PermissionError:") for key in result.errors)
        )
    assert result.files == result.bytes == 0 and result.reasons


def test_deadline_after_source_read_prevents_the_next_mutation(local, monkeypatch):
    root, _ = local
    victim = compiled(root)
    clock = [0.0]
    original = files.Entry.read

    def slow(entry, limit):
        result = original(entry, limit)
        if entry.path == victim:
            clock[0] = 2.0
        return result

    monkeypatch.setattr(files.Entry, "read", slow)
    monkeypatch.setattr(legacy.indexed.time, "monotonic", lambda: clock[0])
    result = collect(root, seconds=1)
    assert victim.is_file() and result.files == 0 and result.exhausted and result.pending
    monkeypatch.setattr(legacy.indexed.time, "monotonic", lambda: 0.0)
    assert collect(root).files == 1


def test_corrupt_continuation_does_not_authorize_or_starve_other_namespace(local):
    root, _ = local
    victim = compiled(root)
    later = compiled(root, number=2)
    collect(root, entries=2, namespaces=1)
    with sqlite3.connect(store.installation_root(root) / "catalog.sqlite") as database:
        database.execute(
            "INSERT OR REPLACE INTO progress(name, value) VALUES (?, ?)",
            ("legacy:" + prefix(root).name, "[" * 1000 + "0" + "]" * 1000),
        )
    reports = [collect(root, namespaces=1) for _ in range(3)]
    assert victim.is_file() and not later.exists()
    assert any("legacy-unsafe-or-unavailable" in report.reasons for report in reports)


def test_replaced_namespace_is_not_adopted_from_saved_progress(local):
    root, _ = local
    compiled(root, relative="a_first.py")
    victim = compiled(root, relative="z_valid.py")
    collect(root, entries=2)
    namespace = prefix(root)
    namespace.rename(namespace.with_name("retained-original"))
    replacement = compiled(root, relative="z_valid.py")
    result = collect(root)
    assert replacement == victim and replacement.is_file() and result.files == 0
    assert "legacy-unsafe-or-unavailable" in result.reasons


@pytest.mark.skipif(os.name != "nt", reason="native CPython Windows drive-mirror collision")
def test_drive_alias_does_not_turn_other_runtime_bytecode_into_retired_evidence(local):
    root, _ = local
    source = root / "versions" / "1.0" / "module.py"
    drive = "Z:" if source.drive.lower() != "z:" else "Y:"
    other = drive + str(source)[len(source.drive):]
    assert legacy._mirror_parts(source) == legacy._mirror_parts(Path(other))
    victim = compiled(root, filename=other)
    result = collect(root)
    assert victim.is_file() and result.files == 0 and result.reasons


@pytest.mark.parametrize("version", sorted(BYTECODE))
def test_historical_format_reclamation_is_not_tied_to_the_updater_interpreter(local, version):
    root, _ = local
    initial = compiled(root)
    candidate = initial.with_name("module.cpython-" + version + ".pyc")
    data = base64.b64decode(BYTECODE[version])
    old = FILENAME.encode("utf-8")
    index = data.index(old)
    assert data.count(old) == 1 and data[index - 1] == len(old)
    assert chr(data[index - 2] & 0x7f) in ("z", "Z")
    new = store.normalized(root / "versions" / "1.0" / "module.py").encode("utf-8")
    # Relocate the captured fixture's one referenced filename, never deserialize it.
    replacement = bytes([ord("u") | (data[index - 2] & 0x80)]) + struct.pack("<i", len(new)) + new
    initial.unlink()
    candidate.write_bytes(data[:index - 2] + replacement + data[index + len(old):])
    report = collect(root)
    assert not candidate.exists() and report.files == 1 and not report.errors


def test_known_header_cannot_claim_a_different_interpreter_cache_tag(local):
    root, _ = local
    initial = compiled(root)
    candidate = initial.with_name("module.cpython-399.pyc")
    initial.rename(candidate)
    report = collect(root)
    assert candidate.is_file() and report.files == 0 and report.reasons


def test_unqualified_native_format_is_retained_while_historical_fixture_still_reclaims(
    local, monkeypatch,
):
    root, _ = local
    monkeypatch.setattr(importlib.util, "MAGIC_NUMBER", b"nope")
    qualified = compiled(root)
    unknown = qualified.with_name("unknown.cpython-399.pyc")
    unknown.write_bytes(
        b"nope" + b"\0" * 12
        + marshal.dumps(compile("answer = 42", str(root / "versions" / "1.0" / "unknown.py"), "exec")),
    )
    report = collect(root)
    assert not qualified.exists() and unknown.is_file() and report.files == 1
    assert report.reasons["legacy-unsafe-or-unavailable"] == 1


def test_relative_runtime_root_is_rejected_without_using_cwd():
    with pytest.raises(ValueError, match="absolute"):
        collect(Path("relative-runtime"))


@pytest.fixture
def runtime():
    source = Path(__file__).resolve().parents[2] / "versioned-runtime" / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("_legacy_cache_runtime_fixture", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_normal_gc_reclaims_legacy_retired_sources_and_keeps_runtime_barriers(local, runtime):
    root, _ = local
    paths = {}
    for slot in ("1.0", "2.0", "3.0", "4.0"):
        (root / "versions" / slot).mkdir(parents=True)
        paths[slot] = compiled(root, slot)
    runtime.activate(root, "4.0")
    runtime._leases.replace_runtime_references(root, "fixture/reference", ["2.0"], expected=[])
    assert runtime.gc(root, keep=["3.0"]) == ["1.0"]
    for _ in range(4):
        assert runtime.gc(root, keep=["3.0"]) == []
    assert not paths["1.0"].exists()
    assert all(paths[slot].is_file() for slot in ("2.0", "3.0", "4.0"))


def test_absent_slot_construction_protects_legacy_bytecode(local, runtime):
    root, _ = local
    victim = compiled(root)
    with runtime.construction_lease(root, "1.0"):
        runtime.gc(root)
        assert victim.is_file()
    runtime.gc(root)
    assert not victim.exists()


@pytest.mark.parametrize("first", ["protected", "unknown"])
def test_real_publication_lease_does_not_starve_later_retired_slots(local, runtime, first):
    root, _ = local
    paths = {}
    for slot in ("1.0", "2.0", "3.0"):
        (root / "versions" / slot).mkdir(parents=True)
        paths[slot] = compiled(root, slot)
    runtime.activate(root, "1.0" if first == "protected" else "3.0")
    if first == "unknown":
        paths["1.0"].write_bytes(b"retain unknown content")
    for _ in range(5):
        runtime.gc(root)
    assert paths["1.0"].is_file() and not paths["2.0"].exists()
    assert paths["3.0"].exists() == (first == "unknown")


def test_unknown_whole_prefix_has_no_automatic_age_or_process_scan_path(local):
    root, _ = local
    namespace = prefix(root)
    namespace.mkdir(parents=True)
    sentinel = namespace / "base.pyc"
    sentinel.write_bytes(b"retain")
    os.utime(namespace, (0, 0))
    result = collect(root)
    assert result.files == 0 and sentinel.read_bytes() == b"retain"
    assert result.reasons == {
        "legacy-no-runtime-mirror": 1, "legacy-whole-prefix-no-writer-custody": 1,
    }


def _seal(root, slot):
    target = root / "versions" / slot
    target.mkdir(parents=True, exist_ok=True)
    (target / "fixture_module.py").write_text("answer = 42\n", encoding="utf-8")
    record = target / "Lib" / "site-packages" / "fixture.dist-info" / "RECORD"
    record.parent.mkdir(parents=True)
    record.write_text("fixture.py,sha256=" + "A" * 43 + ",12\n", encoding="utf-8")
    (target / ".install-complete.json").write_text(json.dumps({
        "version": slot, "completed_at": "fixture", "pid": 1, "payload_hash": "a" * 64,
    }), encoding="utf-8")
    return target


def _import_command(target, arguments, *, wait=False):
    program = (
        "import sys,collections; sys.path.insert(0,sys.argv[1]); "
        "import fixture_module; print(fixture_module.answer,flush=True)"
        + ("; sys.stdin.read(1)" if wait else "")
    )
    return [
        sys.executable, "-I", "-S", *arguments, "-c", program, store.normalized(target),
    ]


def test_native_repeated_upgrade_flow_reclaims_old_leaves_under_a_live_shared_prefix(local, runtime):
    root, _ = local
    first = root / "versions" / "1.0"
    first.mkdir(parents=True)
    (first / "fixture_module.py").write_text("answer = 42\n", encoding="utf-8")
    # Historical first-touch calls, before completion evidence, made fresh prefixes.
    legacy_prefixes, old_leaves = [], []
    for _ in range(3):
        arguments = policy.cache_arguments(root, runtime=first)
        result = subprocess.run(
            _import_command(first, arguments), capture_output=True, text=True, timeout=15,
            **no_window_kwargs(),
        )
        assert result.returncode == 0 and result.stdout.strip() == "42", result.stderr
        location = Path(arguments[1].removeprefix("pycache_prefix="))
        leaves = list(location.rglob("fixture_module.*.pyc"))
        assert len(leaves) == 1
        old_leaves.extend(leaves)
        legacy_prefixes.append(location)
    assert len(set(legacy_prefixes)) == 3
    _seal(root, "1.0")
    runtime.activate(root, "1.0")
    vectors = []
    for slot in ("1.0", "2.0", "3.0"):
        target = first if slot == "1.0" else _seal(root, slot)
        arguments = policy.policy_arguments(root, runtime=target)
        assert arguments[0] == "-X" and "cache/python/unsealed-" not in arguments[1].replace("\\", "/")
        vectors.append(arguments)
        result = subprocess.run(
            _import_command(target, arguments), capture_output=True, text=True, timeout=15,
            **no_window_kwargs(),
        )
        assert result.returncode == 0 and result.stdout.strip() == "42", result.stderr
        runtime.activate(root, slot)
        if slot != "3.0":
            runtime.gc(root, keep=["1.0"])
            assert all(path.is_file() for path in old_leaves)
    newest = root / "versions" / "3.0"
    live = subprocess.Popen(
        _import_command(newest, ["-X", "pycache_prefix=" + str(legacy_prefixes[0])], wait=True),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **no_window_kwargs(),
    )
    try:
        assert live.stdout.readline().strip() == "42"
        live_leaves = list(legacy_prefixes[0].rglob("fixture_module.*.pyc"))
        live_leaves = [path for path in live_leaves if path not in old_leaves]
        assert len(live_leaves) == 1 and live.poll() is None
        removed = runtime.gc(root, keep=["2.0"])
        assert "1.0" in removed and not first.exists()
        for _ in range(4):
            runtime.gc(root, keep=["2.0"])
        assert all(path.exists() == (native_format() is None) for path in old_leaves)
        assert live.poll() is None and live_leaves[0].is_file()
        assert all(path.is_dir() for path in legacy_prefixes)
        assert not Path(vectors[0][1].removeprefix("pycache_prefix=")).parent.exists()
        current_prefix = Path(vectors[-1][1].removeprefix("pycache_prefix="))
        before = {path.relative_to(current_prefix) for path in current_prefix.rglob("*.pyc")}
        for _ in range(4):
            arguments = policy.policy_arguments(root, runtime=newest)
            assert arguments in (vectors[-1], ["-B"])
            result = subprocess.run(
                _import_command(newest, arguments), capture_output=True, text=True, timeout=15,
                **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
        after = {path.relative_to(current_prefix) for path in current_prefix.rglob("*.pyc")}
        assert after == before
        assert len(list((root / "cache" / "python").iterdir())) == 3
    finally:
        try:
            _stdout, stderr = live.communicate(input="x", timeout=10)
        except subprocess.TimeoutExpired:
            live.kill()
            live.communicate(timeout=10)
            raise
        assert live.returncode == 0, stderr
