"""Bounded content verification without weakening retained-source admission."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from test_python_installer_engine import engine as engine, _package


@pytest.fixture
def policy(engine):
    return sys.modules[engine.retain_uv_install.__module__]


def test_content_map_bounds_pending_work_and_preserves_order(policy, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = maximum = pending = maximum_pending = 0

    class ObservedPool(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            nonlocal pending, maximum_pending
            pending += 1
            maximum_pending = max(maximum_pending, pending)
            future = super().submit(*args, **kwargs)
            result = future.result

            def consume(*args, **kwargs):
                nonlocal pending
                try:
                    return result(*args, **kwargs)
                finally:
                    pending -= 1

            future.result = consume
            return future

    def read(value):
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

    monkeypatch.setattr(policy._configuration, "ThreadPoolExecutor", ObservedPool)
    assert policy._bounded_content_map(read, iter(range(12))) == list(range(0, 24, 2))
    assert maximum == maximum_pending == 4
    assert active == pending == 0


@pytest.mark.parametrize("failure_kind", ["reader", "enumerator", "cancel"])
def test_content_map_preserves_first_failure_and_settles_readers(policy, failure_kind):
    barrier = threading.Barrier(3 if failure_kind == "enumerator" else 4)
    failures = [OSError("first reader"), OSError("later reader")]
    if failure_kind == "cancel":
        failures[0] = KeyboardInterrupt("cancelled reader")
    settled = []
    enumerated = []

    def inputs():
        for value in range(8):
            enumerated.append(value)
            if failure_kind == "enumerator" and value == 3:
                raise ValueError("later enumeration")
            yield value

    def read(value):
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


def test_content_map_surfaces_enumeration_failure_after_prior_results(policy):
    failure = ValueError("input enumeration failed")
    settled = []

    def inputs():
        yield 1
        raise failure

    def read(value):
        settled.append(value)
        return value

    with pytest.raises(ValueError) as caught:
        policy._bounded_content_map(read, inputs())
    assert caught.value is failure
    assert settled == [1]


def test_retained_case_distinct_files_are_both_verified(policy, tmp_path, monkeypatch):
    source = _package(tmp_path / "source", "fixture-source")
    upper, lower = source / "A.txt", source / "a.txt"
    upper.write_bytes(b"upper")
    lower.write_bytes(b"lower")
    if upper.samefile(lower):
        pytest.skip("the native fixture filesystem is case-insensitive")
    read_bytes = Path.read_bytes
    reads = []

    def prepare(_uv, arguments, **options):
        options["_projection"].append("fixture resolver")
        return list(arguments)

    def observe(path):
        if path.name in ("A.txt", "a.txt"):
            reads.append(str(path))
        return read_bytes(path)

    monkeypatch.setattr(policy, "_select_uv_executable", lambda value: value)
    monkeypatch.setattr(policy, "prepare_uv_install", prepare)
    monkeypatch.setattr(policy, "_resolver_configuration", lambda *_args, **_kwargs: "fixture resolver")
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
    policy, tmp_path, monkeypatch, outcome,
):
    source = _package(tmp_path / "source", "fixture-source")
    for index in range(10):
        (source / f"content-{index}.txt").write_text(str(index), encoding="utf-8")
    barrier = threading.Barrier(4)
    read_bytes = Path.read_bytes
    private_root = policy._private_retained_root
    lock = threading.Lock()
    active = maximum = reads = 0
    roots = []
    deadline_expired = threading.Event()
    failure = policy.DependencyPolicyError("injected content deadline")

    def prepare(_uv, arguments, **options):
        options["_projection"].append("fixture resolver")
        return list(arguments)

    def root(parent):
        result = private_root(parent)
        roots.append(result)
        return result

    def timeout(*_args, **_kwargs):
        if deadline_expired.is_set():
            raise failure

    def observe(path):
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
    monkeypatch.setattr(policy, "_resolver_configuration", lambda *_args, **_kwargs: "fixture resolver")
    monkeypatch.setattr(policy, "_private_retained_root", root)
    monkeypatch.setattr(policy._configuration, "remaining_timeout", timeout)
    monkeypatch.setattr(Path, "read_bytes", observe)

    def retain():
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
    engine, tmp_path, monkeypatch, changed,
):
    source = _package(tmp_path / "source", "fixture-source")
    files = [source / f"content-{index}.txt" for index in range(12)]
    for index, path in enumerate(files):
        path.write_text(str(index), encoding="utf-8")
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    if changed:
        for path in files:
            path.write_bytes(b"changed")
    read_bytes = Path.read_bytes
    barrier = threading.Barrier(4)
    reads = []

    def observe(path):
        reads.append(path)
        barrier.wait(timeout=5)
        return read_bytes(path)

    @contextmanager
    def retained(*_args, **_kwargs):
        yield SimpleNamespace(sources={source: source}, source_hashes=hashes)

    monkeypatch.setattr(engine, "_source_files", lambda _packages: files)
    monkeypatch.setattr(engine, "_package_roots", lambda _plugin: (source,))
    monkeypatch.setattr(engine, "_construction_hash", lambda *_args: "admitted")
    monkeypatch.setattr(engine, "_select_uv_executable", lambda _uv: sys.executable)
    monkeypatch.setattr(engine, "retain_uv_install", retained)
    monkeypatch.setattr(engine.runtime, "fingerprint_source", lambda _files: "retained")
    monkeypatch.setattr(Path, "read_bytes", observe)
    inputs = engine.FrozenInputs(source, (source,), "admitted")
    if changed:
        with pytest.raises(RuntimeError) as caught:
            with engine._retained_inputs(inputs, Path(sys.executable), Path(sys.executable), refresh=False):
                pytest.fail("changed admitted content cannot reach construction")
        assert str(caught.value).endswith(", ".join(str(path) for path in files[:10]))
    else:
        with engine._retained_inputs(inputs, Path(sys.executable), Path(sys.executable), refresh=False):
            pass
    assert sorted(reads) == sorted(files)
