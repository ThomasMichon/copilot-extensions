"""Disposable prefix consumers; proof fixtures never inspect host processes."""
from contextlib import contextmanager
import importlib.util
import marshal
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import time
from typing import Any, Callable, Iterable, Iterator, cast

import pytest

from agent_procutil import python_cache_collect as indexed
from agent_procutil import python_cache_files as files
from agent_procutil import python_cache_legacy as legacy
from agent_procutil import python_cache_prefixes as prefixes
from agent_procutil import python_cache_store as store
from python_cache_pyc_fixtures import code_record, native_format


class Proof:
    mode = "process-scan"
    reason = ""

    def __init__(self, names: Iterable[str] = (), available: bool = True) -> None:
        self.names = set(names)
        self.available = available

    def verify(self) -> bool:
        return self.available


@contextmanager
def retired(slots: list[str], *, check: Callable[[], None]) -> Iterator[str]:
    check()
    assert slots == ["1.0"]
    yield ""


@pytest.fixture
def area(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(store, "user_cache", lambda: tmp_path / "os-cache")
    return tmp_path / "runtime"


def namespace(root: Path, *, installed: bool = False, number: int = 1) -> Path:
    name = f"installed-{number:064x}" if installed else f"unsealed-{number:032x}"
    target = root / "cache" / "python" / name
    directory = target.joinpath(*cast(Any, legacy)._mirror_parts(root), "versions", "1.0")
    directory.mkdir(parents=True)
    if cast(Any, native_format)():
        tag = sys.implementation.cache_tag
        data = importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(
            compile("value = 1", str(root / "versions" / "1.0" / "module.py"), "exec"),
        )
    else:
        tag, data = "cpython-313", cast(Any, code_record)(filename=str(root / "versions" / "1.0" / "module.py"))
    (directory / f"module.{tag}.pyc").write_bytes(data)
    base = target / "base-interpreter"
    base.mkdir()
    (base / f"stdlib.{tag}.pyc").write_bytes(data)
    old = time.time() - 10 * 86400
    for path in [*target.rglob("*"), target]:
        os.utime(path, (old, old))
    return target


def collect(root: Path, **kwargs: Any) -> prefixes.Report:
    return prefixes.collect(
        root, retirement=kwargs.pop("retirement", retired), proof=kwargs.pop("proof", Proof()),
        namespace_birth=lambda entry: [*files.identity(entry.info), "123"],
        budget=kwargs.pop("budget", prefixes.Budget(seconds=20)), **kwargs,
    )


@pytest.mark.parametrize("installed", [False, True])
def test_dry_run_measures_whole_prefix_without_creating_progress(area: Path, installed: bool) -> None:
    target = namespace(area, installed=installed)
    expected = sum(path.stat().st_size for path in target.rglob("*.pyc"))
    report = collect(area)
    assert report.eligible == 1 and report.eligible_bytes == expected
    assert report.bytes == 0 and report.held_bytes == 0
    assert target.is_dir()
    assert not store.installation_root(area).exists()


@pytest.mark.parametrize("installed", [False, True])
def test_apply_reclaims_base_and_retired_source_bytecode(area: Path, installed: bool) -> None:
    target = namespace(area, installed=installed)
    expected = sum(path.stat().st_size for path in target.rglob("*.pyc"))
    report = collect(area, apply=True)
    assert report.completed == 1 and report.bytes == expected and report.files == 2
    assert not target.exists()


@pytest.mark.parametrize("reason", ["runtime-protected", "runtime-busy", "runtime-present"])
def test_runtime_owner_refusal_preserves_entire_namespace(area: Path, reason: str) -> None:
    target = namespace(area)

    @contextmanager
    def refuse(slots: list[str], *, check: Callable[[], None]) -> Iterator[str]:
        check()
        yield reason

    report = collect(area, apply=True, retirement=refuse)
    assert report.bytes == 0 and report.completed == 0
    assert report.held_bytes > 0 and report.reasons[reason] == 1
    assert len(list(target.rglob("*.pyc"))) == 2


def test_live_and_unknown_references_preserve_whole_namespace(area: Path) -> None:
    target = namespace(area)
    for proof in (Proof([target.name]), Proof(available=False)):
        report = collect(area, apply=True, proof=proof)
        assert report.bytes == 0 and report.completed == 0
        assert report.held_bytes > 0 and target.is_dir()
    assert not store.installation_root(area).exists()


def test_unclassified_content_remains_unmeasured_and_untouched(area: Path) -> None:
    target = namespace(area)
    note = target / "evidence.txt"
    note.write_text("retain evidence")
    report = collect(area, apply=True)
    assert report.unmeasured == 1 and report.bytes == 0
    assert note.read_text() == "retain evidence"
    assert len(list(target.rglob("*.pyc"))) == 2


def test_hardlinked_bytecode_and_its_other_name_survive(area: Path) -> None:
    target = namespace(area)
    sample = next(target.rglob("*.pyc"))
    outside = area.parent / "retained-bytecode.pyc"
    os.link(sample, outside)
    result = collect(area, apply=True)
    assert result.bytes == 0 and result.unmeasured == 1
    assert sample.read_bytes() == outside.read_bytes()
    assert target.is_dir()


def test_redirected_subtree_never_reaches_external_evidence(area: Path) -> None:
    target = namespace(area)
    outside = area.parent / "external"
    outside.mkdir()
    sentinel = outside / "evidence.txt"
    sentinel.write_text("retain")
    try:
        (target / "redirect").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("directory symlink creation requires Windows privilege")
        raise
    result = collect(area, apply=True)
    assert result.bytes == 0 and result.unmeasured == 1
    assert sentinel.read_text() == "retain" and target.is_dir()


def test_interrupted_directory_finalization_resumes_without_losing_association(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = namespace(area)
    original = files.Entry.remove

    def interrupt(entry: files.Entry) -> None:
        if entry.path == store.native(target):
            raise indexed._Exhausted
        return original(entry)

    monkeypatch.setattr(files.Entry, "remove", interrupt)
    first = collect(area, apply=True)
    assert first.bytes > 0 and first.completed == 0 and first.exhausted
    assert target.is_dir() and not list(target.rglob("*.pyc"))
    monkeypatch.setattr(files.Entry, "remove", original)
    second = collect(area, apply=True)
    assert second.completed == 1 and second.bytes == 0
    assert not target.exists()


def test_generation_replacement_never_inherits_retirement_receipt(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = namespace(area)
    original = files.Entry.remove

    def interrupt(entry: files.Entry) -> None:
        if entry.path == store.native(target):
            raise indexed._Exhausted
        return original(entry)

    monkeypatch.setattr(files.Entry, "remove", interrupt)
    collect(area, apply=True)
    monkeypatch.setattr(files.Entry, "remove", original)
    target.rename(target.with_name("retained-old-object"))
    replacement = namespace(area)
    report = collect(area, apply=True)
    assert report.reasons["namespace-generation-changed"] == 1
    assert len(list(replacement.rglob("*.pyc"))) == 2


def test_fresh_bytecode_does_not_inherit_interrupted_prefix_grace(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = namespace(area)
    sample = next(target.rglob("*.pyc"))
    name, data = sample.name, sample.read_bytes()
    original = files.Entry.remove

    def interrupt(entry: files.Entry) -> None:
        if entry.path == store.native(target):
            raise indexed._Exhausted
        original(entry)

    monkeypatch.setattr(files.Entry, "remove", interrupt)
    first = collect(area, apply=True)
    assert first.bytes > 0 and target.is_dir()
    monkeypatch.setattr(files.Entry, "remove", original)
    fresh = target / name
    fresh.write_bytes(data)
    result = collect(area, apply=True)
    assert result.reasons["bytecode-changed-since-admission"] == 1
    assert result.bytes == 0 and fresh.read_bytes() == data
    later = collect(area, apply=True, now=time.time() + 8 * 86400)
    assert later.completed == 1 and later.bytes == len(data)
    assert not target.exists()


def test_resumed_association_includes_new_visible_slots(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = namespace(area)
    sample = next(target.rglob("*.pyc"))
    name, data = sample.name, sample.read_bytes()
    original = files.Entry.remove

    def interrupt(entry: files.Entry) -> None:
        if entry.path == store.native(target):
            raise indexed._Exhausted
        original(entry)

    monkeypatch.setattr(files.Entry, "remove", interrupt)
    assert collect(area, apply=True).bytes > 0
    monkeypatch.setattr(files.Entry, "remove", original)
    mirror = target.joinpath(*legacy._mirror_parts(area), "versions", "2.0")
    mirror.mkdir(parents=True)
    evidence = mirror / name
    evidence.write_bytes(data)
    old = time.time() - 10 * 86400
    os.utime(evidence, (old, old))

    @contextmanager
    def protected(slots: list[str], *, check: Callable[[], None]) -> Iterator[str]:
        check()
        assert slots == ["1.0", "2.0"]
        yield "runtime-protected"

    result = collect(area, apply=True, retirement=protected)
    assert result.bytes == 0 and result.reasons["runtime-protected"] == 1
    assert evidence.read_bytes() == data


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows disposable consumer")
def test_native_consumer_environment_blocks_and_exit_releases_disposable_prefix(
    area: Path, tmp_path: Path,
) -> None:
    from agent_procutil import no_window_kwargs
    from agent_procutil import python_cache_processes as processes
    from agent_procutil.python_cache_windows import Reader

    target = namespace(area)
    ready = tmp_path / "ready"
    code = "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('ready');sys.stdin.readline()"
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-X", "pycache_prefix=" + str(target),
         "-c", code, str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )

    class FixtureReader(Reader):
        # Only the owned child belongs to this fixture's process universe.
        # Host census admission is separately conservative and is not proved here.
        principal = "fixture"

        def snapshot(self) -> dict[int, tuple[int, str]]:
            self.principal = "fixture"
            births = self._births()
            return {child.pid: (births[child.pid], self.principal)} if child.poll() is None else {}

    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "owned consumer did not become ready"
        with processes.observe(reader=FixtureReader(check=lambda: None), seconds=20) as proof:
            assert proof.mode == "process-scan" and target.name in proof.names
            held = prefixes.collect(
                area, retirement=retired, proof=proof, apply=True,
                min_age_seconds=0, budget=prefixes.Budget(seconds=20),
            )
        assert held.reasons["referenced-by-live-process"] == 1
        assert held.bytes == 0 and target.is_dir()
        child.communicate(input=b"\n", timeout=10)
        assert child.returncode == 0
        with processes.observe(reader=FixtureReader(check=lambda: None), seconds=20) as proof:
            removed = prefixes.collect(
                area, retirement=retired, proof=proof, apply=True,
                min_age_seconds=0, budget=prefixes.Budget(seconds=20),
            )
        assert removed.completed == 1 and removed.bytes > 0
        assert not target.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_namespace_budget_progress_is_fair_across_held_prefix_and_multiple_passes(area: Path) -> None:
    targets = [namespace(area, number=number) for number in range(1, 13)]
    expected = sum(path.stat().st_size for target in targets[1:] for path in target.rglob("*.pyc"))
    proof = Proof([targets[0].name])
    cursor, removed, passes = "", 0, 0
    for _ in range(12):
        result = collect(
            area, apply=True, proof=proof, after=cursor,
            budget=prefixes.Budget(namespaces=3, entries=4096, seconds=20),
        )
        cursor = result.next_cursor
        removed += result.bytes
        passes += 1
        if all(not target.exists() for target in targets[1:]):
            break
    assert 4 <= passes <= 6
    assert removed == expected
    assert targets[0].is_dir()
    assert not any(target.exists() for target in targets[1:])


@pytest.mark.parametrize("kind", ["bytes", "entries", "time"])
def test_real_removal_budgets_retain_then_a_fresh_adequate_pass_reclaims(area: Path, kind: str) -> None:
    target = namespace(area)
    baseline = collect(area)
    if kind == "bytes":
        budget = prefixes.Budget(bytes=baseline.measured_bytes - 1, seconds=20)
        reason = "namespace-byte-budget"
    elif kind == "entries":
        budget = prefixes.Budget(entries=baseline.entries, seconds=20)
        reason = "namespace-entry-budget"
    else:
        budget = prefixes.Budget(seconds=1e-9)
        reason = ""
    held = collect(area, apply=True, budget=budget)
    assert held.bytes == 0 and target.is_dir()
    if reason:
        assert held.reasons[reason] == 1
    else:
        assert held.pending and held.exhausted
    result = collect(area, apply=True)
    assert result.completed == 1 and result.bytes == baseline.measured_bytes
    assert not target.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows automatic lane coordination")
def test_normal_gc_does_not_erase_association_before_catch_up(
    area: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(__file__).resolve().parents[2] / "versioned-runtime" / "versioned_runtime_cache.py"
    spec = importlib.util.spec_from_file_location("_cache_gc_sequence", source)
    assert spec is not None and spec.loader is not None
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    target = namespace(area)
    mirror = target.joinpath(*cast(Any, legacy)._mirror_parts(area), "versions", "1.0")

    @contextmanager
    def cleanup(root: Path, slot: str) -> Iterator[bool]:
        yield True

    provider = SimpleNamespace(collect=indexed.collect, collect_legacy=legacy.collect)
    monkeypatch.setattr(owner, "_collector", lambda **kwargs: provider)
    for _ in range(5):
        owner.gc(
            area, candidates=lambda root: [], cleanup_lease=cleanup, protected=lambda **kwargs: set(),
            version_dir=lambda root, slot: root / "versions" / slot,
            is_link=None, slot_age_days=None, remove_slot=None,
            min_age_days=0, automatic=lambda: None,
        )
        if not list(mirror.glob("*.pyc")):
            break
    assert not list(mirror.glob("*.pyc"))
    assert mirror.is_dir()
    result = collect(area, apply=True)
    assert result.completed == 1 and not target.exists()


def test_backdated_content_does_not_make_new_namespace_old(area: Path) -> None:
    target = namespace(area)
    created = time.time()
    identity = str(int((created + 11644473600) * 10000000)) if os.name == "nt" else str(created)
    result = prefixes.collect(
        area, retirement=retired, proof=Proof(),
        namespace_birth=lambda entry: [*files.identity(entry.info), identity],
        budget=prefixes.Budget(seconds=20),
    )
    assert result.reasons["younger-than-grace-period"] == 1
    assert result.eligible == 0 and target.is_dir()
