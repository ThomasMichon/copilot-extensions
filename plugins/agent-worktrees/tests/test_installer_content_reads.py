"""Bounded content verification without weakening retained-source admission."""
from __future__ import annotations

import hashlib
import sys
import threading
from contextlib import contextmanager
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import NoReturn, ParamSpec, TypeVar, cast

import pytest
from test_python_installer_engine import _package
from test_python_installer_engine import engine as engine


_P = ParamSpec("_P")
_T = TypeVar("_T")


@pytest.fixture
def policy(engine: ModuleType) -> ModuleType:
    return sys.modules[engine.retain_uv_install.__module__]


def test_content_map_bounds_pending_work_and_preserves_order(policy: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = maximum = pending = maximum_pending = 0

    class ObservedPool(ThreadPoolExecutor):
        def submit(self, fn: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs) -> Future[_T]:
            nonlocal pending, maximum_pending
            pending += 1
            maximum_pending = max(maximum_pending, pending)
            future = super().submit(fn, *args, **kwargs)
            result = future.result

            def consume(*args: float | None, **kwargs: float | None) -> _T:
                nonlocal pending
                try:
                    return result(*args, **kwargs)
                finally:
                    pending -= 1

            setattr(future, "result", consume)
            return future

    def read(value: int) -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            barrier.wait(timeout=5)
            return value * 2
        finally:
            with lock:
                active -= 1

    progress: list[int] = []
    caller = threading.get_ident()

    def completed(result: int) -> None:
        assert threading.get_ident() == caller
        progress.append(result)

    monkeypatch.setattr(policy._configuration._content, "ThreadPoolExecutor", ObservedPool)
    assert policy._bounded_content_map(
        read, iter(range(12)), on_result=completed,
    ) == list(range(0, 24, 2))
    assert progress == list(range(0, 24, 2))
    assert maximum == maximum_pending == 4
    assert active == pending == 0


def test_content_map_refills_before_waiting_for_the_previous_window(policy: ModuleType) -> None:
    next_started = threading.Event()
    reads: list[int] = []
    lock = threading.Lock()

    def read(value: int) -> int:
        if value == 4:
            next_started.set()
        if value == 3:
            assert next_started.wait(timeout=5)
        with lock:
            reads.append(value)
        return value

    progress: list[int] = []
    assert policy._bounded_content_map(read, range(9), on_result=progress.append) == list(range(9))
    assert progress == list(range(9))
    assert sorted(reads) == list(range(9))
    assert not any(t.name.startswith("retained-content") for t in threading.enumerate())


@pytest.mark.parametrize("failure_kind", ["reader", "enumerator", "cancel"])
def test_content_map_preserves_first_failure_and_settles_readers(policy: ModuleType, failure_kind: str) -> None:
    barrier = threading.Barrier(3 if failure_kind == "enumerator" else 4)
    failures: list[BaseException] = [OSError("first reader"), OSError("later reader")]
    if failure_kind == "cancel":
        failures[0] = KeyboardInterrupt("cancelled reader")
    settled: list[int] = []
    enumerated: list[int] = []

    def inputs() -> Iterator[int]:
        for value in range(8):
            enumerated.append(value)
            if failure_kind == "enumerator" and value == 3:
                raise ValueError("later enumeration")
            yield value

    def read(value: int) -> int:
        try:
            barrier.wait(timeout=5)
            if value < 2:
                raise failures[value]
            return value
        finally:
            settled.append(value)

    with pytest.raises(type(failures[0])) as caught:
        policy._bounded_content_map(read, inputs())
    assert caught.value is failures[0]
    assert sorted(settled) == ([0, 1, 2] if failure_kind == "enumerator" else [0, 1, 2, 3])
    assert enumerated == [0, 1, 2, 3]


def test_content_map_surfaces_enumeration_failure_after_prior_results(policy: ModuleType) -> None:
    failure = ValueError("input enumeration failed")
    settled: list[int] = []

    def inputs() -> Iterator[int]:
        yield 1
        raise failure

    def read(value: int) -> int:
        settled.append(value)
        return value

    with pytest.raises(ValueError) as caught:
        policy._bounded_content_map(read, inputs())
    assert caught.value is failure
    assert settled == [1]


def test_content_map_settles_readers_before_propagating_progress_failure(policy: ModuleType) -> None:
    failure = OSError("progress sink failed")
    barrier = threading.Barrier(4)
    settled: list[int] = []

    def read(value: int) -> int:
        try:
            barrier.wait(timeout=5)
            return value
        finally:
            settled.append(value)

    def progress(_result: int) -> NoReturn:
        raise failure

    with pytest.raises(OSError) as caught:
        policy._bounded_content_map(read, range(8), on_result=progress)
    assert caught.value is failure
    assert sorted(settled) == [0, 1, 2, 3]


@pytest.mark.parametrize("reader_failure", [False, True])
def test_content_map_defers_shutdown_cancellation_until_readers_settle(
    policy: ModuleType, monkeypatch: pytest.MonkeyPatch, reader_failure: bool,
) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    entered = threading.Event()
    released = threading.Event()
    settled = threading.Event()
    cancellation = KeyboardInterrupt("main-thread shutdown interruption")
    primary = OSError("original reader failure")
    pools: list[ThreadPoolExecutor] = []
    shutdowns: list[bool] = []

    class InterruptedPool(ThreadPoolExecutor):
        def __init__(self, *args: object, **kwargs: object) -> None:
            cast(Callable[..., None], super().__init__)(*args, **kwargs)
            pools.append(self)

        def shutdown(self, *args: object, **kwargs: object) -> None:
            shutdowns.append(settled.is_set())
            if len(shutdowns) == 1:
                raise cancellation
            return cast(Callable[..., None], super().shutdown)(*args, **kwargs)

    def read(value: int) -> int:
        if value == 0:
            assert entered.wait(timeout=5)
            if reader_failure:
                raise primary
        if value == 1:
            entered.set()
            try:
                assert released.wait(timeout=5)
            finally:
                settled.set()
        return value

    def release() -> None:
        assert entered.wait(timeout=5)
        time.sleep(0.1)
        released.set()

    releaser = threading.Thread(target=release)
    monkeypatch.setattr(policy._configuration._content, "ThreadPoolExecutor", InterruptedPool)
    releaser.start()
    caught: BaseException | None = None
    settled_at_exit = False
    try:
        policy._bounded_content_map(read, range(4))
    except BaseException as error:
        caught = error
        settled_at_exit = settled.is_set()
    finally:
        released.set()
        releaser.join(timeout=5)
        for pool in pools:
            ThreadPoolExecutor.shutdown(pool, wait=True)
    assert caught is (primary if reader_failure else cancellation)
    assert settled_at_exit
    assert shutdowns == [True, True]


def test_retained_case_distinct_files_are_both_verified(policy: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _package(tmp_path / "source", "fixture-source")
    upper, lower = source / "A.txt", source / "a.txt"
    upper.write_bytes(b"upper")
    lower.write_bytes(b"lower")
    if upper.samefile(lower):
        pytest.skip("the native fixture filesystem is case-insensitive")
    read_bytes = Path.read_bytes
    reads: list[str] = []

    def prepare(_uv: str, arguments: Iterable[str], **options: object) -> list[str]:
        cast(list[str], options["_projection"]).append("fixture resolver")
        return list(arguments)

    def observe(path: Path) -> bytes:
        if path.name in ("A.txt", "a.txt"):
            reads.append(str(path))
        return read_bytes(path)

    monkeypatch.setattr(policy, "_select_uv_executable", lambda value: value)
    monkeypatch.setattr(policy, "prepare_uv_install", prepare)
    monkeypatch.setattr(
        policy, "_resolver_configuration", lambda *_args, **_kwargs: "fixture resolver",
    )
    monkeypatch.setattr(Path, "read_bytes", observe)
    with policy.retain_uv_install(
        "fixture-uv", ["--no-config", str(source)], env={}, cwd=tmp_path,
        trusted_source_roots=(source,),
    ) as transaction:
        assert transaction.source_hashes[upper] == hashlib.sha256(b"upper").hexdigest()
        assert transaction.source_hashes[lower] == hashlib.sha256(b"lower").hexdigest()
    assert len(reads) == len(set(reads)) == 4


@pytest.mark.parametrize("outcome", ["accepted", "mutation", "deadline"])
def test_retained_content_reads_overlap_and_settle_before_cleanup(
    policy: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    source = _package(tmp_path / "source", "fixture-source")
    for index in range(10):
        (source / f"content-{index}.txt").write_text(str(index), encoding="utf-8")
    barrier = threading.Barrier(4)
    read_bytes = Path.read_bytes
    private_root = policy._private_retained_root
    lock = threading.Lock()
    active = maximum = reads = 0
    roots: list[Path] = []
    deadline_expired = threading.Event()
    failure = policy.DependencyPolicyError("injected content deadline")

    def prepare(_uv: str, arguments: Iterable[str], **options: object) -> list[str]:
        cast(list[str], options["_projection"]).append("fixture resolver")
        return list(arguments)

    def root(parent: Path) -> Path:
        result = private_root(parent)
        roots.append(result)
        return cast(Path, result)

    def timeout(*_args: object, **_kwargs: object) -> None:
        if deadline_expired.is_set():
            raise failure

    def observe(path: Path) -> bytes:
        nonlocal active, maximum, reads
        original = Path(str(path).removeprefix("\\\\?\\")).is_relative_to(source)
        with lock:
            active += 1
            reads += 1
            maximum = max(maximum, active)
        try:
            if original:
                barrier.wait(timeout=5)
                if outcome == "deadline":
                    deadline_expired.set()
            data = read_bytes(path)
            return data + b"changed" if outcome == "mutation" and original else data
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(policy, "_select_uv_executable", lambda value: value)
    monkeypatch.setattr(policy, "prepare_uv_install", prepare)
    monkeypatch.setattr(
        policy, "_resolver_configuration", lambda *_args, **_kwargs: "fixture resolver",
    )
    monkeypatch.setattr(policy, "_private_retained_root", root)
    monkeypatch.setattr(policy._configuration, "remaining_timeout", timeout)
    monkeypatch.setattr(Path, "read_bytes", observe)

    def retain() -> None:
        with policy.retain_uv_install(
            "fixture-uv", ["--no-config", str(source)], env={}, cwd=tmp_path,
            trusted_source_roots=(source,), deadline=123,
        ) as transaction:
            assert outcome == "accepted"
            assert len(transaction.source_hashes) == 12
            for path, digest in transaction.source_hashes.items():
                assert digest == hashlib.sha256(read_bytes(path)).hexdigest()
            assert active == 0

    if outcome == "accepted":
        retain()
        assert reads == 24
    else:
        with pytest.raises(policy.DependencyPolicyError) as caught:
            retain()
        if outcome == "deadline":
            assert caught.value is failure
        else:
            assert "source content changed" in str(caught.value)
    assert maximum == 4 and active == 0
    assert roots and all(not path.exists() for path in roots)


@pytest.mark.parametrize("changed", [False, True])
def test_engine_rechecks_every_admitted_source_with_bounded_reads(
    engine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool,
) -> None:
    source = _package(tmp_path / "source", "fixture-source")
    files = [source / f"content-{index}.txt" for index in range(12)]
    for index, path in enumerate(files):
        path.write_text(str(index), encoding="utf-8")
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    if changed:
        for path in files:
            path.write_bytes(b"changed")
    admitted = engine._construction_hash(tuple(files), ())
    read_bytes = Path.read_bytes
    barrier = threading.Barrier(4)
    reads: list[Path] = []

    def observe(path: Path) -> bytes:
        reads.append(path)
        barrier.wait(timeout=5)
        return read_bytes(path)

    @contextmanager
    def retained(*_args: object, **_kwargs: object) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(sources={source: source}, source_hashes=hashes)

    monkeypatch.setattr(engine, "_source_files", lambda _packages, **_kwargs: files)
    monkeypatch.setattr(engine, "_package_roots", lambda _plugin: (source,))
    monkeypatch.setattr(engine, "_select_uv_executable", lambda _uv: sys.executable)
    monkeypatch.setattr(engine, "retain_uv_install", retained)
    monkeypatch.setattr(Path, "read_bytes", observe)
    inputs = engine.FrozenInputs(source, (source,), admitted)
    if changed:
        with pytest.raises(RuntimeError) as caught:
            with engine._retained_inputs(
                inputs, Path(sys.executable), Path(sys.executable), refresh=False,
            ):
                pytest.fail("changed admitted content cannot reach construction")
        assert str(caught.value).endswith(", ".join(str(path) for path in files[:10]))
    else:
        with engine._retained_inputs(
            inputs, Path(sys.executable), Path(sys.executable), refresh=False,
        ):
            pass
    assert sorted(Path(engine._native_retained_path(path)) for path in reads) == sorted(
        Path(engine._native_retained_path(path)) for path in (files if changed else files * 2)
    )
