"""Coupled callback contracts; compiled concurrency is explicitly opt-in."""

import ast
import ctypes
import hashlib
import json
import marshal
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from tools.coverage_guided_selection import observer_native_build as build
from tools.coverage_guided_selection import child_stream as stream


@pytest.fixture
def native_callbacks(tmp_path, monkeypatch):
    emitter_path = tmp_path / "emitter.py"
    emitter_bytes = b"def emit():\n    pass\n"
    emitter_path.write_bytes(emitter_bytes)
    emitter_module = compile(emitter_bytes, str(emitter_path), "exec", dont_inherit=True)
    emitter = next(code for code in emitter_module.co_consts if isinstance(code, type(emitter_module)))
    owner = compile("owned=1\n", str(tmp_path / "owner.py"), "exec", dont_inherit=True)
    frame = SimpleNamespace(f_code=emitter, f_back=SimpleNamespace(f_code=owner, f_back=None))
    for key, value in (
        ("_generated_active", True), ("_generated_state", threading.local()),
        ("_generated_lock", threading.RLock()), ("_generated_receipts", []),
        ("_generated_count", 0), ("_generator_codes", frozenset([emitter])),
        ("_expected_codes", {os.path.normcase(owner.co_filename): frozenset([owner])}),
        ("_code_sources", {}), ("_sources", {}), ("_root", ""), ("_package", "fixture_package"),
    ):
        monkeypatch.setattr(stream, key, value)
    (tmp_path / "generated").mkdir()
    entry = Path(build.__file__).with_name("native_observer_entry.py")
    functions = [node for node in ast.parse(entry.read_text()).body
                 if isinstance(node, ast.FunctionDef) and
                 node.name in {"code_digest", "origin_identity", "native_source_event"}]
    namespace = {
        "hashlib": hashlib, "marshal": marshal, "json": json, "Path": Path,
        "os": os, "sys": sys, "control": tmp_path,
        "stream": stream, "auditor": SimpleNamespace(error=pytest.fail), "code_tree": stream._code_tree,
        "compile_receipts": [], "generated_code_refs": {}, "generated_ids": {},
        "catalog": {"sources": {}, "origin_source_sha256": [hashlib.sha256(emitter_bytes).hexdigest()]},
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(entry), "exec"), namespace)
    return namespace, emitter, frame


def test_python_generated_guard_preserves_actual_native_dispatch_mapping(tmp_path, native_callbacks, monkeypatch):
    namespace, emitter, frame = native_callbacks
    monkeypatch.setattr(stream.sys, "_getframe", lambda depth: SimpleNamespace(f_back=frame))
    content = b"def generated():\n    return 22\n"
    actual = compile(content, "<string>", "exec", dont_inherit=True)
    with (tmp_path / "events.jsonl").open("wb") as events:
        namespace["source_event_fd"] = events.fileno()
        namespace["native_source_event"]("compile", (content, "<string>"), emitter, 0)
        namespace["native_source_event"]("exec", (actual,), emitter, 0)
        native = dict(stream._code_sources)
        assert native and all(row[1] is not None for row in native.values())
        stream._generated_event("compile", (content, "<string>"))
        stream._generated_event("exec", (actual,))
        assert stream._code_sources == native
        assert stream._generated_count == 0
    assert json.loads((tmp_path / "events.jsonl").read_text())["generated_source_admitted"]
    method = next(code for code in actual.co_consts if isinstance(code, type(actual)))
    assert stream._frame_source(SimpleNamespace(
        f_code=method, f_globals={"__name__": "fixture_package.source"},
    )) == native[id(method)][1]


def test_identical_generated_callbacks_publish_only_complete_files(tmp_path, native_callbacks):
    namespace, emitter, _ = native_callbacks
    first_write, second_done = threading.Event(), threading.Event()
    failures, errors = [], []
    namespace["auditor"] = SimpleNamespace(error=errors.append)
    content = b"def generated():\n    return 22\n"
    actual = compile(content, "<string>", "exec", dont_inherit=True)
    original_open = open

    class DelayedWrite:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, value):
            first_write.set()
            if not second_done.wait(5):
                raise TimeoutError("second callback did not finish")
            return self.handle.write(value)

    def opened(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if (mode == "xb" and os.path.dirname(path) == str(tmp_path / "generated")
                and threading.current_thread().name == "first-publication"):
            return DelayedWrite(handle)
        return handle

    def invoke(second=False):
        try:
            namespace["native_source_event"]("exec", (actual,), emitter, 0)
        except Exception as error:
            failures.append(error)
        finally:
            if second:
                second_done.set()

    namespace["open"] = opened
    with (tmp_path / "events.jsonl").open("wb") as events:
        namespace["source_event_fd"] = events.fileno()
        namespace["native_source_event"]("compile", (content, "<string>"), emitter, 0)
        first = threading.Thread(target=invoke, name="first-publication")
        second = threading.Thread(target=invoke, kwargs={"second": True})
        try:
            first.start()
            assert first_write.wait(5)
            second.start()
            first.join(5)
            second.join(5)
        finally:
            second_done.set()
            first.join(5)
            if second.ident is not None:
                second.join(5)
        assert not first.is_alive() and not second.is_alive()
    assert not failures and not errors
    published = tmp_path / "generated" / (hashlib.sha256(content).hexdigest() + ".py")
    assert list((tmp_path / "generated").iterdir()) == [published]
    assert published.read_bytes() == content
    records = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len(records) == 2 and all(row["generated_source_admitted"] for row in records)


def test_generated_publication_keeps_mismatched_existing_bytes(tmp_path, native_callbacks):
    namespace, emitter, _ = native_callbacks
    errors = []
    namespace["auditor"] = SimpleNamespace(error=errors.append)
    content = b"def generated():\n    return 22\n"
    actual = compile(content, "<string>", "exec", dont_inherit=True)
    published = tmp_path / "generated" / (hashlib.sha256(content).hexdigest() + ".py")
    published.write_bytes(b"unexpected existing bytes")
    with (tmp_path / "events.jsonl").open("wb") as events:
        namespace["source_event_fd"] = events.fileno()
        namespace["native_source_event"]("compile", (content, "<string>"), emitter, 0)
        namespace["native_source_event"]("exec", (actual,), emitter, 0)
    assert errors == ["actual generated source changed"]
    assert published.read_bytes() == b"unexpected existing bytes"
    assert list((tmp_path / "generated").iterdir()) == [published]
    assert not namespace["generated_code_refs"]
    assert not json.loads((tmp_path / "events.jsonl").read_text())["generated_source_admitted"]


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("CGS_RUN_OBSERVER_CONCURRENCY") != "1",
    reason="deliberate Linux native callback build with the governed pinned toolchain",
)
def test_actual_native_dispatch_suppresses_only_same_thread_recursion(tmp_path):
    source = Path(build.__file__).with_name("native_observer_entry.c")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == build.C_SHA256
    wheel = Path(os.environ["CGS_OBSERVER_TOOLCHAIN_WHEEL"])
    toolchain = tmp_path / "toolchain"
    result = build.extract_toolchain(wheel, toolchain)
    try:
        # Keep the canonical callback body; disable only automatic registration.
        harness = tmp_path / "callback.c"
        harness.write_text(
            "#define constructor unused\n"
            f'#include "{source.as_posix()}"\n'
            "void cgs_test_dispatch(PyObject *callback) { source_dispatch=callback; handled=1; }\n"
            'int cgs_test_event(PyObject *args) { return audit("compile",args,NULL); }\n',
            encoding="utf-8",
        )
        library_path = tmp_path / "callback.so"
        compiled = subprocess.run(
            [result["compiler"], "cc", "-shared", "-fPIC", "-O1",
             '-DENTRY_SHA="' + "1" * 64 + '"', str(harness), "-o", str(library_path), "-ldl"],
            capture_output=True, text=True, timeout=60,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        library = ctypes.PyDLL(str(library_path))
        library.cgs_test_dispatch.argtypes = [ctypes.py_object]
        library.cgs_test_dispatch.restype = None
        library.cgs_test_event.argtypes = [ctypes.py_object]
        library.cgs_test_event.restype = ctypes.c_int
        entered, second_entered, release = (threading.Event() for _ in range(3))
        calls, failures = [], []

        def callback(event, arguments, origin, offset):
            label, = arguments
            calls.append(label)
            if event != "compile" or not isinstance(origin, type(callback.__code__)):
                failures.append("invalid actual callback origin")
            if label == "first":
                library.cgs_test_event(("recursive",))
                entered.set()
                if not release.wait(10):
                    failures.append("first callback release timeout")
            elif label == "second":
                second_entered.set()

        library.cgs_test_dispatch(callback)
        first = threading.Thread(target=lambda: library.cgs_test_event(("first",)))
        second = threading.Thread(target=lambda: library.cgs_test_event(("second",)))
        first.start()
        try:
            assert entered.wait(5), "first actual callback never entered"
            second.start()
            assert second_entered.wait(3), "another thread's actual callback was suppressed"
        finally:
            release.set()
            first.join(timeout=5)
            if second.ident is not None:
                second.join(timeout=5)
        assert not first.is_alive() and not second.is_alive()
        assert sorted(calls) == ["first", "second"] and not failures
    finally:
        shutil.rmtree(toolchain)
