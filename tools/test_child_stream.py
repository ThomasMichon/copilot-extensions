"""Strict evidence parsing for the lightweight standalone-child prototype."""
import os
import threading
import time

import pytest

from tools.coverage_guided_selection import child_stream as runtime
from tools.coverage_guided_selection import child_stream_parent as stream
from tools.coverage_guided_selection import child_audit


def test_child_runtime_has_only_runtime_functions_and_parent_reuses_constants():
    import ast
    from pathlib import Path
    names={node.name for node in ast.parse(Path(runtime.__file__).read_text()).body
           if isinstance(node,ast.FunctionDef)}
    assert names=={"_phase","_auditor","_write","_fail","_source","_frame_source",
                   "_code_tree","_prepare_sources",
                   "_install_generated_guard","_generated_event","_start_thread","_install_thread_guard",
                   "_thread_profile","stop_import_profile",
                   "_line","_call","_after_fork","start"}
    assert stream._auditor is runtime._auditor
    assert (stream.ENV,stream.VERSION,stream.MAX_RECORDS,stream.MAX_MANIFEST)==(
        runtime.ENV,runtime.VERSION,runtime.MAX_RECORDS,runtime.MAX_MANIFEST)
    assert not any(name in runtime.__dict__ for name in
                   ("source_inventory","manifest_text","read_streams","configure_parent","_contexts","_helpers"))


def test_staged_parent_source_is_byte_exact_and_end_checked(tmp_path,monkeypatch):
    import hashlib
    from pathlib import Path
    from tools.coverage_guided_selection import standalone
    source=tmp_path/"source.py"
    source.write_text("pass\n")
    _,options=standalone.coverage_files(tmp_path,{str(source):"component/source.py"})
    assert len(options["child_stream_helpers"])==3
    for name in ("child_stream","child_stream_parent","child_audit"):
        staged=Path(options["_cgs_"+name])
        expected=Path(runtime.__file__).with_name(name+".py").read_bytes()
        assert staged.read_bytes()==expected
        assert options["child_stream_helpers"][str(staged)]==hashlib.sha256(expected).hexdigest()
    monkeypatch.setattr(stream,"_helpers",options["child_stream_helpers"])
    Path(options["_cgs_child_stream_parent"]).write_text("# changed parent evidence parser\n")
    with pytest.raises(ValueError,match="instrumentation source changed"):
        stream.finish_parent()


def test_sparse_seen_fast_path_only_publishes_committed_records(monkeypatch):
    stream = runtime
    import threading
    from types import SimpleNamespace
    frame=SimpleNamespace(f_lineno=2)
    monkeypatch.setattr(stream,"_frame_source",lambda frame:(1,3))
    monkeypatch.setattr(stream,"_failed",False)
    monkeypatch.setattr(stream,"_seen",set())
    monkeypatch.setattr(stream,"_lock",threading.Lock())
    writes=[]
    def write(record):
        assert (1,2) not in stream._seen
        writes.append(record)
    monkeypatch.setattr(stream,"_write",write)
    stream._line(frame,"line",None)
    stream._line(frame,"line",None)
    assert len(writes)==1 and stream._seen=={(1,2)}
    monkeypatch.setattr(stream,"_seen",set())
    failures=[]
    monkeypatch.setattr(stream,"_fail",lambda:failures.append(True))
    def failed(record):
        raise OSError("write failure")
    monkeypatch.setattr(stream,"_write",failed)
    stream._line(frame,"line",None)
    assert failures==[True] and not stream._seen


def test_distinct_actual_code_objects_do_not_alias_shared_string_filename(monkeypatch):
    from types import SimpleNamespace
    first=compile("first=1\n","<string>","exec",dont_inherit=True)
    second=compile("second=2\n","<string>","exec",dont_inherit=True)
    monkeypatch.setattr(runtime,"_code_sources",{
        id(first):(first,(500,1)),id(second):(second,(501,1)),
    })
    monkeypatch.setattr(runtime,"_sources",{})
    monkeypatch.setattr(runtime,"_root","owned-source-root/")
    assert runtime._frame_source(SimpleNamespace(f_code=first))==(500,1)
    assert runtime._frame_source(SimpleNamespace(f_code=second))==(501,1)
    unknown=compile("third=3\n","<string>","exec",dont_inherit=True)
    assert runtime._frame_source(SimpleNamespace(f_code=unknown)) is None


@pytest.mark.parametrize("module", ["fleet_manager.planning", "__main__"])
def test_owned_package_shadow_cannot_be_a_successful_zero_hit_producer(monkeypatch, module):
    from types import SimpleNamespace

    failures = []
    monkeypatch.setattr(runtime, "_package", "fleet_manager")
    monkeypatch.setattr(runtime, "_sources", {})
    monkeypatch.setattr(runtime, "_code_sources", {})
    monkeypatch.setattr(runtime, "_root", "verified-installed-root/")
    monkeypatch.setattr(child_audit, "error", failures.append)
    frame = SimpleNamespace(
        f_code=SimpleNamespace(co_filename="checkout/src/fleet_manager/planning.py"),
        f_globals={"__name__": module},
    )
    assert runtime._frame_source(frame) is None
    assert failures == ["owned package executed outside verified source inventory"]
    frame.f_globals["__name__"] = "unrelated"
    frame.f_code.co_filename = "unrelated/module.py"
    runtime._frame_source(frame)
    assert len(failures) == 1


def test_foreign_frame_paths_normalize_once_without_caching_module_identity(monkeypatch):
    from types import SimpleNamespace

    calls, failures = [], []
    original = os.path.normcase

    def normalized(value):
        calls.append(value)
        return original(value)

    for key, value in (("_sources", {}), ("_expected_codes", {}), ("_code_sources", {}), ("_path_keys", {}),
                       ("_root", "verified-installed-root/"), ("_package", "fixture_package"),
                       ("_package_path", ("fixture_package", original("fixture_package") + "/",
                                          "/" + original("fixture_package") + "/", "fixture_package."))):
        monkeypatch.setattr(runtime, key, value)
    monkeypatch.setattr(child_audit, "error", failures.append)
    monkeypatch.setattr(os.path, "normcase", normalized)
    for filename, owned in (
        ("outside/fixture_package/source.py", True),
        ("fixture_package/source.py", True),
        ("outside\\fixture_package\\source.py", True),
        ("outside/not_fixture_package/source.py", False),
        ("outside/source.py", False),
        ("outside/fixture_package", False),
    ):
        frame = SimpleNamespace(
            f_code=compile("pass\n", filename, "exec"),
            f_globals={"__name__": "unrelated"},
        )
        calls.clear()
        failures.clear()
        assert runtime._frame_source(frame) is None
        assert bool(failures) is owned, filename
        assert calls == [filename]
        failures.clear()
        frame.f_globals["__name__"] = "fixture_package.source"
        assert runtime._frame_source(frame) is None
        assert failures == ["owned package executed outside verified source inventory"]
        assert id(frame.f_code) not in runtime._code_sources
    monkeypatch.setattr(runtime, "_package", "outside")
    frame = SimpleNamespace(f_code=compile("pass\n", "outside/source.py", "exec"),
                            f_globals={"__name__": "unrelated"})
    calls.clear()
    failures.clear()
    assert runtime._frame_source(frame) is None
    assert calls == ["outside"]
    assert failures == ["owned package executed outside verified source inventory"]


def test_foreign_filename_metadata_is_cached_but_admission_stays_live(monkeypatch):
    from types import SimpleNamespace

    original = os.path.normcase
    calls, failures = [], []
    filename = "outside/source.py"
    frame = SimpleNamespace(f_code=compile("pass\n", filename, "exec"),
                            f_globals={"__name__": "unrelated"})
    for name, value in (
        ("_sources", {}), ("_expected_codes", {}), ("_code_sources", {}), ("_path_keys", {}),
        ("_root", "verified-installed-root/"), ("_package", "fixture_package"),
        ("_package_path", ("fixture_package", "fixture_package/", "/fixture_package/", "fixture_package.")),
    ):
        monkeypatch.setattr(runtime, name, value, raising=False)
    monkeypatch.setattr(child_audit, "error", failures.append)
    def normalized(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(os.path, "normcase", normalized)
    for _ in range(100):
        assert runtime._frame_source(frame) is None
    assert calls == [filename]
    assert runtime._path_keys == {filename: (original(filename), original(filename).replace("\\", "/"))}
    frame.f_globals["__name__"] = "fixture_package.source"
    for _ in range(2):
        assert runtime._frame_source(frame) is None
    assert failures == ["owned package executed outside verified source inventory"] * 2
    assert not runtime._code_sources
    frame.f_globals["__name__"] = "unrelated"
    monkeypatch.setattr(runtime, "_package", "outside")
    assert runtime._frame_source(frame) is None
    assert calls == [filename, "outside"]
    assert failures == ["owned package executed outside verified source inventory"] * 3


@pytest.mark.parametrize("full", [False, True, "during-lock"])
def test_filename_metadata_cache_bounds_never_skip_live_admission(monkeypatch, full):
    from types import SimpleNamespace

    contents = {str(index): (str(index), str(index)) for index in range(1024)}
    cache = dict(contents) if full is True else {}
    filename = "outside/source.py" if full else "outside/" + "x" * 4096 + ".py"
    frame = SimpleNamespace(f_code=compile("pass\n", filename, "exec"),
                            f_globals={"__name__": "fixture_package.source"})
    failures = []
    for name, value in (
        ("_sources", {}), ("_expected_codes", {}), ("_code_sources", {}), ("_path_keys", cache),
        ("_root", "verified-installed-root/"), ("_package", "fixture_package"),
        ("_package_path", ("fixture_package", "fixture_package/", "/fixture_package/", "fixture_package.")),
    ):
        monkeypatch.setattr(runtime, name, value, raising=False)
    if full == "during-lock":
        class FillAtLock:
            def __enter__(self):
                cache.update(contents)
            def __exit__(self, *_args):
                return False
        monkeypatch.setattr(runtime, "_generated_lock", FillAtLock())
    monkeypatch.setattr(child_audit, "error", failures.append)
    for _ in range(2):
        assert runtime._frame_source(frame) is None
    assert len(cache) == (1024 if full else 0)
    assert failures == ["owned package executed outside verified source inventory"] * 2
    assert not runtime._code_sources


def test_cached_filename_metadata_never_licenses_another_executable(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    source = tmp_path / "source.py"
    canonical = b"def value():\n    return 22\n"
    source.write_bytes(canonical)
    for name, value in (
        ("_sources", {}), ("_expected_codes", {}), ("_code_sources", {}), ("_path_keys", {}),
        ("_root", ""), ("_package", ""),
    ):
        monkeypatch.setattr(runtime, name, value, raising=False)
    runtime._prepare_sources({str(source): (1, 2, hashlib.sha256(canonical).hexdigest())})
    calls, failures = [], []
    original = os.path.normcase
    def normalized(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(os.path, "normcase", normalized)
    monkeypatch.setattr(child_audit, "error", failures.append)
    actual = compile(canonical, str(source), "exec", dont_inherit=True)
    changed = compile(canonical.replace(b"22", b"11"), str(source), "exec", dont_inherit=True)
    assert runtime._frame_source(SimpleNamespace(f_code=actual)) == (1, 2)
    assert runtime._frame_source(SimpleNamespace(f_code=changed)) is None
    assert calls == [str(source)]
    assert failures == ["executed code differs from verified source inventory"]
    assert runtime._code_sources[id(changed)] == (changed, None)


@pytest.mark.parametrize("guard", ["parent", "child"])
def test_guard_reads_immutable_frame_code_once(monkeypatch, guard):
    reads, observed = [], []
    code = (lambda: None).__code__
    class Frame:
        @property
        def f_code(self):
            reads.append(True)
            return code
    frame = Frame()
    monkeypatch.setattr(stream, "_tracing", True)
    monkeypatch.setattr(stream, "_guard_active", True)
    monkeypatch.setattr(stream, "_guard_arming", False)
    monkeypatch.setattr(runtime, "_frame_source", observed.append)
    monkeypatch.setattr(runtime, "_started", True)
    monkeypatch.setattr(runtime, "_arming", False)
    monkeypatch.setattr(runtime, "_profile_arming", False)
    monkeypatch.setattr(runtime, "_prior_profile", None)
    monkeypatch.setattr(runtime, "_prior_thread_profile", None)
    (stream._profile if guard == "parent" else runtime._thread_profile)(frame, "call", None)
    assert reads == [True]
    assert observed == ([frame] if guard == "parent" else [])


def test_hot_audit_generated_filter_skips_unrelated_thread_state(monkeypatch):
    reads = []
    class State:
        @property
        def busy(self):
            reads.append(True)
            return False
    monkeypatch.setattr(runtime, "_generated_active", True)
    monkeypatch.setattr(runtime, "_generated_state", State())
    runtime._generated_event("object.__getattr__", (object(), "f_code"))
    assert not reads


def test_hot_audit_parent_filter_skips_unrelated_guard_state(monkeypatch):
    reads = []
    class State:
        def __bool__(self):
            reads.append(True)
            return True
    for name in ("_guard_active", "_guard_arming", "_tracing"):
        monkeypatch.setattr(stream, name, State())
    stream._profile_event("object.__getattr__", (object(), "f_code"))
    assert not reads


def test_spawn_audit_fast_filter_skips_unrelated_event_comparisons():
    comparisons = []
    known = {"_cgs.audit_probe", "os.fork", "os.forkpty", "os.exec", "os.system",
             "sys.settrace", "sys.setprofile", "subprocess.Popen", "os.posix_spawn"}
    hashes = {hash(value) for value in known}
    unrelated_hash = next(value for value in range(10) if value not in hashes)
    class Event(str):
        def __hash__(self):
            return unrelated_hash
        def __eq__(self, other):
            comparisons.append(other)
            return super().__eq__(other)
    child_audit._event(Event("object.__getattr__"), ())
    assert not comparisons


def test_spawn_audit_fast_filter_covers_every_handled_event():
    import ast
    import inspect
    import textwrap

    function = ast.parse(textwrap.dedent(inspect.getsource(child_audit._event))).body[0]
    guarded = next(node for node in function.body if isinstance(node, ast.Try))
    gate = guarded.body[0].test
    assert isinstance(gate, ast.Compare) and isinstance(gate.ops[0], ast.NotIn)
    assert isinstance(gate.left, ast.Name) and gate.left.id == "event"
    filtered = ast.literal_eval(gate.comparators[0])
    handled = set()
    for comparison in ast.walk(guarded):
        if (isinstance(comparison, ast.Compare) and comparison is not gate
                and isinstance(comparison.left, ast.Name) and comparison.left.id == "event"):
            value = ast.literal_eval(comparison.comparators[0])
            handled.update({value} if isinstance(value, str) else value)
    assert filtered == handled


@pytest.mark.parametrize("stale", [False, True])
def test_executed_code_requires_verified_source_tree_and_strong_identity(tmp_path, monkeypatch, stale):
    import hashlib
    from types import SimpleNamespace

    source = tmp_path / "source.py"
    canonical = b"def value():\n    return 22\n"
    source.write_bytes(canonical)
    failures = []
    for key, value in (("_sources", {}), ("_expected_codes", {}), ("_code_sources", {}),
                       ("_root", ""), ("_package", "")):
        monkeypatch.setattr(runtime, key, value)
    monkeypatch.setattr(child_audit, "error", failures.append)
    runtime._prepare_sources({str(source): (1, 2, hashlib.sha256(canonical).hexdigest())})
    code = compile(canonical.replace(b"22", b"11") if stale else canonical,
                   str(source), "exec", dont_inherit=True)
    nested = next(value for value in code.co_consts if isinstance(value, type(code)))
    # A cache entry for another object cannot license this frame, even at its id.
    runtime._code_sources[id(nested)] = (code, (999, 999))
    result = runtime._frame_source(SimpleNamespace(f_code=nested))
    assert result == (None if stale else (1, 2))
    assert bool(failures) is stale
    assert runtime._code_sources[id(nested)][0] is nested
    assert runtime._frame_source(SimpleNamespace(f_code=nested)) == result
    assert len(failures) == int(stale)
    source.write_bytes(canonical.replace(b"22", b"11"))
    with pytest.raises(ValueError, match="source differs"):
        runtime._prepare_sources({str(source): (1, 2, hashlib.sha256(canonical).hexdigest())})


def test_parent_guard_replacement_is_reported_without_application_exception(monkeypatch):
    from types import SimpleNamespace

    failures = []
    monkeypatch.setattr(stream, "_guard_active", True)
    monkeypatch.setattr(stream, "_guard_arming", False)
    monkeypatch.setattr(stream, "_auditor", lambda: SimpleNamespace(error=failures.append))
    stream._profile_event("sys.setprofile", ())
    assert failures == ["parent executable-source guard was replaced"]
    monkeypatch.setattr(stream, "_guard_arming", True)
    stream._profile_event("sys.setprofile", ())
    assert len(failures) == 1
    monkeypatch.setattr(stream, "_tracing", True)
    monkeypatch.setattr(stream, "_coverage_owner", None)
    stream._profile_event("sys.settrace", ())
    assert len(failures) == 2 and failures[-1].startswith("parent line tracer was replaced")
    monkeypatch.setattr(stream, "_tracing", False)
    stream._profile_event("sys.settrace", ())
    assert len(failures) == 2


def test_generated_identity_requires_proven_emitter_owner_and_executed_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    owned = compile("owned=1\n", str(tmp_path / "source.py"), "exec", dont_inherit=True)
    emitter_code = compile("emitter=1\n", "dataclasses.py", "exec", dont_inherit=True)
    emitter = SimpleNamespace(f_code=emitter_code, f_back=SimpleNamespace(f_code=owned, f_back=None))
    failures = []
    for key, value in (
        ("_generated_active", True), ("_generated_state", threading.local()),
        ("_generated_lock", threading.RLock()), ("_generated_receipts", []),
        ("_generated_count", 0), ("_generator_codes", frozenset([emitter_code])),
        ("_expected_codes", {os.path.normcase(owned.co_filename): frozenset([owned])}),
        ("_code_sources", {}), ("_sources", {}), ("_root", ""), ("_package", "fixture_package"),
    ):
        monkeypatch.setattr(runtime, key, value)
    monkeypatch.setattr(child_audit, "error", failures.append)
    monkeypatch.setattr(runtime.sys, "_getframe", lambda depth: SimpleNamespace(f_back=emitter))
    content = "def method():\n    return 22\n"
    runtime._generated_event("compile", (content, "<string>"))
    actual = compile(content, "<string>", "exec", dont_inherit=True)
    runtime._generated_event("exec", (actual,))
    method = next(code for code in actual.co_consts if isinstance(code, type(actual)))
    frame = SimpleNamespace(f_code=method, f_globals={"__name__": "fixture_package.source"})
    assert runtime._frame_source(frame) is None and not failures
    assert runtime._code_sources[id(method)][0] is method
    forged = compile(content.replace("22", "11"), "<string>", "exec", dont_inherit=True)
    runtime._generated_event("exec", (forged,))
    assert id(forged) not in runtime._code_sources
    frame.f_code = forged
    assert runtime._frame_source(frame) is None and failures
    emitter.f_back = None
    other = compile(content.replace("22", "33"), "<string>", "exec", dont_inherit=True)
    runtime._generated_event("compile", (content.replace("22", "33"), "<string>"))
    runtime._generated_event("exec", (other,))
    assert id(other) not in runtime._code_sources
    monkeypatch.setattr(runtime.sys, "_getframe", lambda depth: SimpleNamespace(f_back=None))
    runtime._generated_event("exec", (other,))
    assert id(other) not in runtime._code_sources


@pytest.mark.parametrize("starter_name", ["_start_thread_original", "_start_thread_alias_original"])
def test_low_level_thread_refusal_preserves_call_and_catches_cached_parent_alias(monkeypatch, starter_name):
    from types import SimpleNamespace

    failures, calls = [], []
    monkeypatch.setattr(runtime, "_thread_guard_active", True)
    monkeypatch.setattr(runtime, "_auditor", lambda: SimpleNamespace(error=failures.append))
    monkeypatch.setattr(stream, "_auditor", lambda: SimpleNamespace(error=failures.append))
    monkeypatch.setattr(runtime, "_start_thread_original", lambda *args: calls.append(args) or 42)
    assert runtime._start_thread("target", ()) == 42
    assert calls == [("target", ())]
    assert failures == ["unsupported low-level thread coverage lifecycle"]
    starter = getattr(runtime, starter_name)
    frame = SimpleNamespace(f_code=compile("pass", "<caller>", "exec"))
    stream._profile(frame, "c_call", starter)
    assert len(failures) == 2
    runtime._thread_profile(frame, "c_call", starter)
    assert len(failures) == 3
    frame.f_code = runtime._thread_start_code
    stream._profile(frame, "c_call", starter)
    runtime._thread_profile(frame, "c_call", starter)
    assert len(failures) == 3


@pytest.mark.parametrize("profile", ["parent", "child"])
@pytest.mark.parametrize("starter_name", ["_start_thread_original", "_start_thread_alias_original"])
def test_cached_native_thread_starters_refuse_untraced_workers(monkeypatch, profile, starter_name):
    import sys
    from types import SimpleNamespace

    failures, observed = [], []
    done = threading.Event()
    monkeypatch.setattr(runtime, "_auditor", lambda: SimpleNamespace(error=failures.append))
    monkeypatch.setattr(stream, "_auditor", lambda: SimpleNamespace(error=failures.append))
    monkeypatch.setattr(runtime, "_frame_source", lambda frame: None)
    monkeypatch.setattr(runtime, "_prior_profile", None)
    monkeypatch.setattr(runtime, "_prior_thread_profile", None)
    starter = getattr(runtime, starter_name)
    assert type(starter) is type(os.getpid)
    def worker():
        try:
            observed.append((sys.gettrace(), sys.getprofile()))
        finally:
            done.set()
    previous = sys.getprofile()
    try:
        sys.setprofile(stream._profile if profile == "parent" else runtime._thread_profile)
        identity = starter(worker, ())
        finished = done.wait(5)
    finally:
        sys.setprofile(previous)
    deadline = time.monotonic() + 5
    while identity in sys._current_frames() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert finished and identity not in sys._current_frames()
    assert observed == [(None, None)]
    assert failures == ["unsupported low-level thread coverage lifecycle"]


@pytest.mark.parametrize("profile", ["parent", "child"])
@pytest.mark.parametrize("hook", ["trace", "profile"])
@pytest.mark.parametrize("foreign_globals", [False, True])
def test_cached_thread_setters_cannot_hide_remove_run_restore(monkeypatch, profile, hook, foreign_globals):
    import sys
    from types import FunctionType, SimpleNamespace

    failures, observed, refused_hooks = [], [], []
    guard = stream._profile if profile == "parent" else runtime._thread_profile
    originals = {"trace": threading.settrace, "profile": threading.setprofile}
    previous = {"trace": threading.gettrace(), "profile": threading.getprofile()}
    required = {"trace": runtime._call, "profile": guard}
    cached = originals[hook]
    assert cached.__code__ is getattr(runtime, "_thread_set" + hook + "_code")
    if foreign_globals:
        cached = FunctionType(cached.__code__, dict(cached.__globals__))
    for name, value in (
        ("_started", profile == "child"), ("_arming", False), ("_profile_arming", True),
        ("_prior_profile", None), ("_prior_thread_profile", None),
    ):
        monkeypatch.setattr(runtime, name, value)
    for name, value in (
        ("_guard_active", profile == "parent"), ("_guard_arming", True),
        ("_tracing", profile == "parent"), ("_parent_thread_trace", required["trace"]),
    ):
        monkeypatch.setattr(stream, name, value)
    def refused(reason):
        failures.append(reason)
        refused_hooks.append(getattr(threading, "get" + hook)())
    monkeypatch.setattr(runtime, "_auditor", lambda: SimpleNamespace(error=refused))
    monkeypatch.setattr(stream, "_auditor", lambda: SimpleNamespace(error=refused))
    monkeypatch.setattr(runtime, "_frame_source", lambda frame: None)
    def wrapped(function):
        pytest.fail("cached setter must bypass the replacement attribute")
    monkeypatch.setattr(threading, "set" + hook, wrapped)
    originals["trace"](required["trace"])
    originals["profile"](required["profile"])
    process_profile = sys.getprofile()
    worker = threading.Thread(target=lambda: observed.append((sys.gettrace(), sys.getprofile())))
    try:
        sys.setprofile(guard)
        monkeypatch.setattr(runtime, "_profile_arming", False)
        monkeypatch.setattr(stream, "_guard_arming", False)
        cached(None)
        worker.start()
        worker.join(5)
        cached(required[hook])
    finally:
        monkeypatch.setattr(runtime, "_profile_arming", True)
        monkeypatch.setattr(stream, "_guard_arming", True)
        sys.setprofile(process_profile)
        originals["trace"](previous["trace"])
        originals["profile"](previous["profile"])
        if worker.ident is not None:
            worker.join(5)
    assert not worker.is_alive()
    if foreign_globals:
        assert observed == [(required["trace"], guard)]
        assert not failures and not refused_hooks
        return
    assert observed == [(None, guard) if hook == "trace" else (required["trace"], None)]
    reason = {
        ("parent", "trace"): "parent thread line tracer was replaced",
        ("parent", "profile"): "parent thread executable-source guard was replaced",
        ("child", "trace"): "child thread tracer was replaced",
        ("child", "profile"): "child thread-start profile guard was replaced",
    }[profile, hook]
    assert failures == [reason]
    assert refused_hooks == [required[hook]]


def test_child_thread_profile_preserves_then_stops_only_prior_diagnostic_callbacks(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "_profile_owner", 1)
    monkeypatch.setattr(runtime.threading, "get_ident", lambda: 1)
    monkeypatch.setattr(runtime, "_prior_profile", lambda *args: calls.append("process"))
    monkeypatch.setattr(runtime, "_prior_thread_profile", lambda *args: calls.append("thread"))
    runtime._thread_profile(None, "call", None)
    monkeypatch.setattr(runtime.threading, "get_ident", lambda: 2)
    runtime._thread_profile(None, "call", None)
    assert calls == ["process", "thread"]
    runtime.stop_import_profile()
    runtime._thread_profile(None, "call", None)
    assert calls == ["process", "thread"]


def test_generated_guard_recursion_state_is_thread_local(monkeypatch):
    state = threading.local()
    state.busy = True
    monkeypatch.setattr(runtime, "_generated_state", state)
    observed = []
    thread = threading.Thread(target=lambda: observed.append(getattr(runtime._generated_state, "busy", False)))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and observed == [False]
    assert runtime._generated_state.busy


def test_prelude_endpoint_follows_tracer_setup_and_duration_precedes_lines(evidence, monkeypatch):
    from types import SimpleNamespace

    _, _, _, _, options = evidence
    control = options["audit_control"]
    (control / "run").write_text(options["nonce"], encoding="ascii")
    events = []
    monkeypatch.setenv(runtime.ENV, str(control / "manifests" / ("b" * 64 + ".manifest")))
    monkeypatch.setenv("_CGS_PRELUDE_NS", "1000")
    for key, value in (("_started", False), ("_sources", {}), ("_expected_codes", {}),
                       ("_package", ""), ("_root", ""), ("_fd", None)):
        monkeypatch.setattr(runtime, key, value)
    monkeypatch.setattr(runtime, "_write", lambda data: events.append(("write", data)))
    original_trace = lambda _function: events.append(("thread-trace", None))
    monkeypatch.setattr(runtime.threading, "settrace", original_trace)
    monkeypatch.setattr(runtime.sys, "settrace", lambda _function: events.append(("process-trace", None)))
    profiles = {"process": None, "thread": None}
    def set_profile(kind, function):
        profiles[kind] = function
        events.append((kind + "-profile", None))
    monkeypatch.setattr(runtime.sys, "getprofile", lambda: profiles["process"])
    monkeypatch.setattr(runtime.threading, "getprofile", lambda: profiles["thread"])
    monkeypatch.setattr(runtime.sys, "setprofile", lambda function: set_profile("process", function))
    monkeypatch.setattr(runtime.threading, "setprofile", lambda function: set_profile("thread", function))
    monkeypatch.setattr(runtime.os, "register_at_fork", lambda **_kwargs: events.append(("fork", None)), raising=False)
    monkeypatch.setattr(runtime.os, "open", lambda *_args: 123)
    auditor = SimpleNamespace(
        owner_pid=lambda: 1, install=lambda *_a, **_k: None,
        context=lambda _env: "b" * 64, argv_key=lambda _args: "d" * 64,
    )
    monkeypatch.setattr(runtime, "_auditor", lambda: auditor)
    monkeypatch.setattr(runtime, "_install_generated_guard", lambda: events.append(("generated-guard", None)))
    monkeypatch.setattr(runtime, "_install_thread_guard", lambda: events.append(("thread-guard", None)))

    def endpoint():
        assert runtime.threading.settrace is not original_trace
        events.append(("endpoint", None))
        return 11_000

    monkeypatch.setattr(runtime.time, "perf_counter_ns", endpoint)
    runtime.start(str(control))
    names = [name for name, _ in events]
    assert names.index("generated-guard") < names.index("endpoint")
    assert names.index("thread-guard") < names.index("endpoint")
    assert names.index("fork") < names.index("thread-trace") < names.index("process-trace")
    assert names.index("process-trace") < names.index("endpoint")
    assert names.index("thread-profile") < names.index("process-profile") < names.index("endpoint")
    writes = [data for name, data in events if name == "write"]
    assert writes[-1] == b"\0\0" + (10).to_bytes(4, "big")


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_STANDALONE_INTEGRATION") != "1",
    reason="real parent/child importer matrix belongs to deliberate native qualification",
)
def test_generated_audit_guard_preserves_native_first_main_execution(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    (tmp_path / "sitecustomize.py").write_text(
        "from tools.coverage_guided_selection import child_stream\n"
        "child_stream._install_generated_guard()\n",
        encoding="utf-8",
    )
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(root)]))
    for name in (runtime.ENV, child_audit.OWNER):
        environment.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", "print('native main reached')"], env=environment,
        capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0 and result.stdout.strip() == "native main reached", result.stderr


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_STANDALONE_INTEGRATION") != "1",
    reason="real parent/child importer matrix belongs to deliberate native qualification",
)
@pytest.mark.parametrize("location,stale,optimization,thread_mode", [
    (location, stale, optimization, "normal")
    for optimization in ("", "-O", "-OO")
    for location in ("parent", "child") for stale in (False, True)
] + [
    (location, mode == "temporary-removal", "", mode)
    for location in ("parent", "child") for mode in ("temporary-removal", "low-level")
] + [
    ("parent", False, "", "trace-removal"),
    ("parent", False, "", "shadow-script"),
    ("child", False, "", "shadow-script"),
    ("parent", False, "", "cached-low-level"),
    ("parent", False, "", "cached-low-level-alias"),
    ("child", False, "", "cached-low-level"),
    ("child", False, "", "cached-low-level-alias"),
    ("parent", False, "", "cached-trace-removal"),
    ("child", False, "", "cached-trace-removal"),
    ("parent", False, "", "cached-profile-removal"),
    ("child", False, "", "cached-profile-removal"),
    ("child", False, "", "profile-removal"),
    ("child", False, "", "thread-profile-removal"),
    ("parent", False, "", "concurrent-generation"),
    ("child", False, "", "concurrent-generation"),
    ("parent", False, "", "late-launch"),
])
def test_real_timestamp_valid_bytecode_is_bound_to_executed_source(
    tmp_path, stale, location, optimization, thread_mode,
):
    """Exercise the real driver and importer, not a synthetic filename-only frame."""
    import hashlib
    import json
    import struct
    import subprocess
    import sys
    import textwrap
    from pathlib import Path
    from tools.coverage_guided_selection import standalone

    package = tmp_path / "fixture_package"
    package.mkdir()
    (package / "__init__.py").write_bytes(b"")
    source = package / "source.py"
    canonical = (
        b"from dataclasses import dataclass\n"
        b"@dataclass\nclass Value:\n    result: int = 22\n"
        b"def value():\n    return Value().result\n"
        b"def generate(label):\n"
        b"    return dataclass(type(label, (), {'__module__': __name__, "
        b"'__annotations__': {'result': int}, 'result': 22}))\n"
    )
    old = canonical.replace(b"22", b"11")
    source.write_bytes(old if stale else canonical)
    timestamp = source.stat().st_mtime_ns
    level = {"": 0, "-O": 1, "-OO": 2}[optimization]
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    compiled = subprocess.run(
        [sys.executable, "-I", "-c",
         "import py_compile,sys;print(py_compile.compile(sys.argv[1],doraise=True,"
         "invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,optimize=int(sys.argv[2])))",
         str(source), str(level)],
        capture_output=True, text=True, timeout=20, **flags,
    )
    assert compiled.returncode == 0, compiled.stderr
    cache = Path(compiled.stdout.strip())
    source.write_bytes(canonical)
    os.utime(source, ns=(timestamp, timestamp))
    header = cache.read_bytes()[:16]
    assert struct.unpack("<III", header[4:]) == (0, int(source.stat().st_mtime), len(canonical))
    expected_hash = hashlib.sha256(canonical).hexdigest()
    shadow = tmp_path / "shadow" / "fixture_package" / "source.py"
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(canonical)
    sandbox = tmp_path / "collection"
    sandbox.mkdir()
    mapping = {
        str(path): "fixture_package/" + path.name for path in package.glob("*.py")
    }
    _, options = standalone.coverage_files(sandbox, mapping)
    driver = sandbox / "driver.py"
    native_starter = "start_new" if thread_mode == "cached-low-level-alias" else "start_new_thread"
    capture = (
        f"import _thread\n_thread._cgs_test_cached_start=_thread.{native_starter}\n"
        if location == "parent" and thread_mode in {"cached-low-level", "cached-low-level-alias"} else ""
    )
    if location == "parent" and thread_mode in {"cached-trace-removal", "cached-profile-removal"}:
        hook = "trace" if thread_mode == "cached-trace-removal" else "profile"
        capture = f"import threading\nthreading._cgs_test_cached_set{hook}=threading.set{hook}\n"
    driver.write_text(capture + standalone._DRIVER_SCRIPT, encoding="utf-8")
    if thread_mode == "normal":
        consumer = (
            "from fixture_package.source import value\nobserved=value()\n"
            "import threading\nthread=threading.Thread(target=value);thread.start();thread.join()\n"
        )
    elif thread_mode == "concurrent-generation":
        consumer = (
            "import builtins,threading\nfrom fixture_package.source import generate\n"
            "runtime=sys.modules['_cgs_child_stream']\n"
            "entered=threading.Event();second_done=threading.Event()\n"
            "classes={};errors=[]\noriginal_compile=builtins.compile\n"
            "def compiling(*args,**kwargs):\n"
            " if threading.current_thread().name=='generation-first' and not entered.is_set():\n"
            "  entered.set()\n"
            "  if not second_done.wait(5): raise RuntimeError('overlapping generation timed out')\n"
            " return original_compile(*args,**kwargs)\n"
            "def create(label):\n"
            " try:\n"
            "  model=generate(label)\n"
            "  assert model().result==22\n"
            "  classes[label]=model\n"
            " except Exception as error: errors.append(error)\n"
            " finally:\n"
            "  if label=='second': second_done.set()\n"
            "runtime.compile=compiling\n"
            "first=threading.Thread(target=lambda:create('first'),name='generation-first')\n"
            "second=threading.Thread(target=lambda:create('second'),name='generation-second')\n"
            "first.start()\n"
            "try:\n"
            " assert entered.wait(3)\n"
            " second.start();first.join(timeout=5);second.join(timeout=5)\n"
            "finally:\n"
            " second_done.set();first.join(timeout=5)\n"
            " if second.ident is not None: second.join(timeout=5)\n"
            " del runtime.compile\n"
            "assert not first.is_alive() and not second.is_alive() and not errors,errors\n"
            "assert set(classes)=={'first','second'}\n"
            "for model in classes.values():\n"
            " code=model.__init__.__code__\n"
            " assert runtime._code_sources[id(code)][0] is code\n"
            "observed=22\n"
        )
    elif thread_mode in {"temporary-removal", "cached-trace-removal", "cached-profile-removal"}:
        hook = ("profile" if location == "parent" else "trace") if thread_mode == "temporary-removal" else (
            "trace" if thread_mode == "cached-trace-removal" else "profile"
        )
        setter = f"threading.set{hook}" if thread_mode == "temporary-removal" else (
            f"threading._cgs_test_cached_set{hook}" if location == "parent" else f"pre_captured_set{hook}"
        )
        consumer = (
            "import importlib,threading\nvalues=[]\n"
            f"original=threading.get{hook}();{setter}(None)\n"
            "try:\n"
            " thread=threading.Thread(target=lambda: values.append("
            "importlib.import_module('fixture_package.source').value()))\n"
            " thread.start();thread.join()\n"
            f"finally: {setter}(original)\n"
            "observed=values[0]\n"
        )
    elif thread_mode in {
        "low-level", "cached-low-level", "cached-low-level-alias", "profile-removal", "thread-profile-removal",
    }:
        starter = ("_thread.start_new_thread" if thread_mode == "low-level" else
                   "_thread._cgs_test_cached_start" if location == "parent" else "pre_captured_start")
        consumer = (
            "import _thread,importlib\nvalues=[]\ngate=_thread.allocate_lock();gate.acquire()\n"
            "def consume():\n"
            " try: values.append(importlib.import_module('fixture_package.source').value())\n"
            " finally: gate.release()\n"
        )
        if thread_mode == "profile-removal":
            consumer += "original=sys.getprofile();sys.setprofile(None)\n"
        elif thread_mode == "thread-profile-removal":
            consumer += "original=threading.getprofile();threading.setprofile(None)\n"
        if thread_mode == "thread-profile-removal":
            consumer += f"thread=threading.Thread(target=lambda: {starter}(consume,()))\nthread.start();thread.join()\n"
        else:
            consumer += f"{starter}(consume,())\n"
        consumer += "assert gate.acquire(timeout=20)\nobserved=values[0]\n"
        if thread_mode == "profile-removal":
            consumer += "sys.setprofile(original)\n"
        elif thread_mode == "thread-profile-removal":
            consumer += "threading.setprofile(original)\n"
    elif thread_mode == "trace-removal":
        consumer = (
            "import sys\noriginal=sys.gettrace();sys.settrace(None)\n"
            "try:\n from fixture_package.source import value\n observed=value()\n"
            "finally: sys.settrace(original)\n"
        )
    elif thread_mode == "late-launch":
        late = (
            f"import sys;sys.path[:0]=[{str(tmp_path)!r},{str(sandbox)!r}];"
            "import _cgs_child_audit,_cgs_child_stream;"
            f"_cgs_child_stream.start({options['child_stream_control']!r});"
            "from fixture_package.source import value;"
            f"open({str(tmp_path / 'late-application.txt')!r},'w').write(str(value()))"
        )
        consumer = (
            "import atexit,subprocess\nfrom fixture_package.source import value\nobserved=value()\n"
            f"atexit.register(lambda: subprocess.run([sys.executable,'-I','-c',{late!r}],"
            f"check=True,timeout=20,**{flags!r}))\n"
        )
    else:
        consumer = f"import runpy\nobserved=runpy.run_path({str(shadow)!r},run_name='__main__')['value']()\n"
    child_program = (
        f"import sys,_thread,threading;pre_captured_start=_thread.{native_starter};"
        "pre_captured_settrace=threading.settrace;pre_captured_setprofile=threading.setprofile;"
        f"sys.path[:0]=[{str(tmp_path)!r},{str(sandbox)!r}];"
        "import _cgs_child_audit,_cgs_child_stream;"
        f"_cgs_child_stream.start({options['child_stream_control']!r});"
        f"exec({consumer!r});print(observed)"
    )
    test = sandbox / "test_consumer.py"
    prefix = f"import sys;sys.path.insert(0,{str(tmp_path)!r})\n"
    if location == "child":
        body = (
            " import subprocess\n"
            f" result=subprocess.run([sys.executable,'-I',*{[optimization] if optimization else []!r},"
            f"'-c',{child_program!r}],capture_output=True,text=True,timeout=20,"
            f"**{flags!r})\n"
            " assert result.returncode==0,result.stderr\n"
            f" assert result.stdout.strip()=={str(11 if stale else 22)!r}\n"
        )
    else:
        body = textwrap.indent(consumer + f"assert observed=={11 if stale else 22}\n", " ")
    test.write_text(prefix + "def test_consumer():\n" + body, encoding="utf-8")
    output = sandbox / "result.json"
    report = sandbox / "report.json"
    command = [
        sys.executable, "-I", *([optimization] if optimization else []),
        str(driver), json.dumps([str(test)]), str(package), str(tmp_path),
        str(sandbox / ".coverage"), str(report), str(output), str(sandbox / "p"),
        json.dumps({"installed_source": str(package), "source_mapping": mapping, **options}),
    ]
    environment = dict(os.environ)
    for name in ("PYTEST_ADDOPTS", "_CGS_CHILD_MANIFEST", "_CGS_CHILD_AUDITOR",
                 "COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG"):
        environment.pop(name, None)
    result = subprocess.run(command, env=environment, capture_output=True, text=True,
                            timeout=40, **flags)
    assert json.loads(report.read_text())["summary"].get("passed") == 1, result.stdout + result.stderr
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_hash
    if thread_mode == "late-launch":
        assert result.returncode == 0 and output.exists(), result.stdout + result.stderr
        assert (tmp_path / "late-application.txt").read_text() == "22"
        with pytest.raises(ValueError, match="after collection seal"):
            stream.verify_final(
                json.loads(output.read_text()), control=options["child_stream_control"],
                nonce=options["child_stream_nonce"], inventory=options["child_stream_inventory"],
                helpers=options["child_stream_helpers"],
            )
        return
    if stale or thread_mode not in {"normal", "concurrent-generation"}:
        assert result.returncode != 0 and not output.exists(), result.stdout + result.stderr
        errors = [json.loads(path.read_text()) for path in
                  (sandbox / "child-lines" / "audit").glob("*.error")]
        reason = ("executed code differs" if thread_mode == "normal" else
                  "tracer was replaced" if thread_mode == "cached-trace-removal" else
                  "guard was replaced" if thread_mode == "cached-profile-removal" else
                  "guard was replaced" if thread_mode == "temporary-removal" and location == "parent" else
                  "tracer was replaced" if thread_mode == "temporary-removal" else
                  "unsupported low-level thread" if thread_mode in {
                      "low-level", "cached-low-level", "cached-low-level-alias",
                  } else
                  "thread-start profile guard was replaced" if thread_mode in {"profile-removal", "thread-profile-removal"} else
                  "parent line tracer was replaced" if thread_mode == "trace-removal" else
                  "owned package executed outside")
        assert any(reason in row["reason"] for row in errors), errors
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(output.read_text())
        assert data["coverage"]["fixture_package/source.py"]["8" if thread_mode == "concurrent-generation" else "6"]

@pytest.mark.parametrize("corruption",["collision","foreign-path","wrong-hash","fake-canonical"])
def test_generated_source_extension_identity_is_strict(evidence,corruption):
    import hashlib
    directory,_,_,_,options=evidence
    generated=options["audit_control"]/"generated"
    generated.mkdir()
    source=generated/"observed.py"
    content=b"actual_generated=1\n"
    source.write_bytes(content)
    digest=hashlib.sha256(content).hexdigest()
    row={"installed":str(source),"sha256":digest,"lines":1,
         "canonical":"generated-source:sha256:"+digest}
    identifier=500
    if corruption=="collision":identifier=next(iter(options["inventory"]))
    elif corruption=="foreign-path":row["installed"]=str(source.parent.parent/"foreign.py")
    elif corruption=="wrong-hash":row["sha256"]="0"*64
    else:row["canonical"]="component/pretend-fixed-source.py"
    with pytest.raises(ValueError,match="generated source"):
        stream.read_streams(directory,**options,generated_inventory={identifier:row})


@pytest.mark.parametrize("arguments,expected", [
    (["-I", "-S", "-c", "pass"], "site-disabled"),
    (["-IS", "-c", "pass"], "site-disabled"),
    (["-Scpass"], "site-disabled"),
    (["-S", "--version"], "information"),
    (["-IS", "--help"], "information"),
    (["-VV"], "information"),
    (["-X", "utf8", "-S", "-c", "pass"], "site-disabled"),
    (["-XS", "-c", "pass"], "instrumented"),
    (["-ISX", "--help", "-c", "pass"], "site-disabled"),
    (["-ISW", "--version", "-c", "pass"], "site-disabled"),
    (["-W", "--help", "-c", "pass"], "instrumented"),
    (["-c", "pass", "--help", "-S"], "instrumented"),
    (["-m", "module", "--version"], "instrumented"),
    (["script.py", "--help", "-S"], "instrumented"),
    (["--", "--version"], "instrumented"),
    (["-S", "-c", "pass", "--version"], "site-disabled"),
    (["-I", "-S", "-", "--help"], "site-disabled"),
    (["-I", "-", "--version"], "instrumented"),
])
def test_interpreter_options_do_not_exempt_site_disabled_code(arguments, expected):
    assert child_audit.python_mode(["python", *arguments]) == expected


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("def f():\n    return 3\n", encoding="utf-8")
    inventory = stream.source_inventory({str(source): "component/source.py"})
    directory = tmp_path / "hits"
    directory.mkdir()
    audit = tmp_path / "audit"
    audit.mkdir()
    (tmp_path / "status").write_bytes(b"R")
    nonce, context = "a" * 64, "b" * 64
    (tmp_path / "run").write_text(nonce, encoding="ascii")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    stream.write_manifest(
        manifests / (context + ".manifest"), nonce=nonce, context=context,
        directory=directory, inventory=inventory,
    )
    pid = 2147483647
    path = directory / f"{pid}-{'c' * 32}.hits"
    header = f"R {stream.VERSION} {nonce} {context} {pid} 1 {'d' * 64} 2\n".encode()
    (audit / ("1-" + "e" * 32 + ".intent")).write_text(
        f"{child_audit.VERSION} {nonce} {context} 1 {'d' * 64} 1\n",
    )
    payload = b"\0\0" + (100).to_bytes(4, "big") + b"\0\1" + (2).to_bytes(4, "big")
    path.write_bytes(header + payload)
    return directory, path, header, payload, dict(
        nonce=nonce, contexts={context: "tests/test_source.py::test_f"}, inventory=inventory,
        audit_control=tmp_path,
    )


def test_verified_records_merge_without_replacing_parent_evidence(evidence):
    directory, _, _, _, options = evidence
    result = stream.read_streams(directory, **options)
    assert result["prelude_seconds"] == [0.0001]
    assert result["child_streams"] == 1
    merged = stream.merge_coverage(
        {"component/source.py": {"2": ["tests/test_other.py::test_other"]}}, result["coverage"],
    )
    assert merged["component/source.py"]["2"] == [
        "tests/test_other.py::test_other", "tests/test_source.py::test_f",
    ]


def test_seal_is_identity_bound_and_late_launch_does_not_block_application(evidence, monkeypatch):
    _, _, _, _, options = evidence
    control = options["audit_control"]
    child_audit.seal(control, nonce=options["nonce"])
    child_audit.seal(control, nonce=options["nonce"])
    token = next(iter(options["contexts"]))
    monkeypatch.setattr(child_audit, "_control", str(control))
    monkeypatch.setattr(child_audit, "_nonce", options["nonce"])
    monkeypatch.setattr(child_audit, "_python", child_audit.key(os.sys.executable))
    monkeypatch.setattr(child_audit, "_own_context", token)
    monkeypatch.setattr(child_audit, "_failed", False)
    environment = {
        runtime.ENV: str(control / "manifests" / (token + ".manifest")),
        child_audit.OWNER: str(os.getpid()),
    }
    child_audit._event("subprocess.Popen", (
        os.sys.executable, [os.sys.executable, "-c", "pass"], None, environment,
    ))
    assert (control / "status").read_bytes() == b"E"
    with pytest.raises(ValueError, match="after collection seal"):
        child_audit.verify(control, nonce=options["nonce"], contexts=options["contexts"], producers=[])
    (control / "sealed").write_text("wrong", encoding="ascii")
    with pytest.raises(ValueError, match="seal identity"):
        child_audit.check_seal(control, nonce=options["nonce"])


@pytest.mark.parametrize("change", ["status", "stream", "missing-seal"])
def test_final_control_changes_cannot_reuse_a_snapshot(evidence, change):
    directory, path, _, _, options = evidence
    final = stream.read_streams(directory, **options)
    result = {"coverage": final.pop("coverage"), "children": final,
              "durations": {node: 0.0 for node in options["contexts"].values()}}
    control = options["audit_control"]
    if change == "status":
        (control / "status").write_bytes(b"E")
    elif change == "stream":
        path.write_bytes(path.read_bytes() + b"\0\1" + (1).to_bytes(4, "big"))
    else:
        (control / "sealed").unlink()
    with pytest.raises(ValueError):
        stream.verify_final(
            result, control=control, nonce=options["nonce"], inventory=options["inventory"], helpers={},
        )


@pytest.mark.parametrize("corruption", [
    "truncated", "unknown-source", "line-bound", "unknown-context", "wrong-run",
    "wrong-version", "producer-error", "oversized", "wrong-pid-name", "source-changed",
    "duplicate", "missing-producer", "missing-intent", "unknown-audit-context",
    "manifest-changed", "manifest-missing",
    "future-birth",
])
def test_malformed_or_unearned_child_evidence_is_refused(evidence, corruption):
    directory, path, header, payload, options = evidence
    if corruption == "truncated":
        payload = payload[:-1]
    elif corruption == "unknown-source":
        payload = payload[:6] + b"\0\x02" + (2).to_bytes(4, "big")
    elif corruption == "line-bound":
        payload = payload[:6] + b"\0\x01" + (3).to_bytes(4, "big")
    elif corruption == "unknown-context":
        options["contexts"] = {}
    elif corruption == "wrong-run":
        options["nonce"] = "d" * 64
    elif corruption == "wrong-version":
        header = header.replace(stream.VERSION.encode(), b"INVALID/0")
    elif corruption == "future-birth":
        header = header.rstrip().rsplit(b" ", 1)[0] + b" 99999999999999999999999\n"
    elif corruption == "producer-error":
        header = b"E" + header[1:]
    elif corruption == "oversized":
        payload = b"\0" * (6 * (stream.MAX_RECORDS + 2))
    elif corruption == "wrong-pid-name":
        path = path.rename(directory / ("1-" + "c" * 32 + ".hits"))
    elif corruption == "duplicate":
        payload += payload[6:]
    elif corruption == "missing-producer":
        path.unlink()
        with pytest.raises(ValueError, match="missing"):
            stream.read_streams(directory, **options)
        return
    elif corruption == "missing-intent":
        next((options["audit_control"] / "audit").iterdir()).unlink()
    elif corruption == "unknown-audit-context":
        intent = next((options["audit_control"] / "audit").iterdir())
        intent.write_text(intent.read_text().replace("b" * 64, "f" * 64))
    elif corruption.startswith("manifest-"):
        manifest = next((options["audit_control"] / "manifests").iterdir())
        if corruption == "manifest-missing":
            manifest.unlink()
        else:
            manifest.write_text(manifest.read_text() + "2\t3\tffff\n")
    else:
        from pathlib import Path
        Path(options["inventory"][1]["installed"]).write_text("changed = True\n")
    path.write_bytes(header + payload)
    with pytest.raises(ValueError):
        stream.read_streams(directory, **options)


def test_zero_source_hit_producer_is_not_missing(evidence):
    directory, path, header, payload, options = evidence
    path.write_bytes(header + payload[:6])
    result = stream.read_streams(directory, **options)
    assert result["coverage"] == {}
    assert result["zero_hit_producers"] == result["audited_producers"] == 1


def test_no_opted_in_spawns_requires_no_producer(evidence):
    directory, path, _, _, options = evidence
    path.unlink()
    next((options["audit_control"] / "audit").iterdir()).unlink()
    result = stream.read_streams(directory, **options)
    assert result["audit_complete"] and result["audited_producers"] == 0


def test_manifest_and_inventory_bounds_are_explicit(evidence):
    directory, _, _, _, options = evidence
    row = dict(options["inventory"][1], installed="x" * 40000)
    with pytest.raises(ValueError, match="manifest exceeds"):
        stream.write_manifest(
            directory / "oversized", nonce=options["nonce"], context="b" * 64,
            directory=directory, inventory={1: row},
        )
    installed = options["inventory"][1]["installed"]
    with pytest.raises(ValueError, match="ambiguous"):
        stream.source_inventory({installed: "same.py", installed + ".other": "same.py"})


def test_running_producer_is_never_a_complete_prefix(evidence):
    directory, path, header, payload, options = evidence
    path.unlink()
    path = directory / f"{os.getpid()}-{'c' * 32}.hits"
    path.write_bytes(header.replace(b"2147483647", str(os.getpid()).encode()) + payload)
    with pytest.raises(ValueError, match="still-running"):
        stream.read_streams(directory, **options)


def test_live_producer_cannot_append_and_exit_between_snapshot_and_verification(evidence, monkeypatch):
    from pathlib import Path

    directory, path, _, payload, options = evidence
    state = {"alive": True, "snapshot_taken": False}
    original_open = Path.open

    class Snapshot:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.handle.close()

        def read(self, size):
            prefix = self.handle.read(size)
            state["snapshot_taken"] = True
            with original_open(path, "ab") as producer:
                producer.write(payload[6:8] + (1).to_bytes(4, "big"))
            state["alive"] = False
            return prefix

    def open_path(candidate, mode="r", *args, **kwargs):
        handle = original_open(candidate, mode, *args, **kwargs)
        return Snapshot(handle) if candidate == path and mode == "rb" else handle

    monkeypatch.setattr(child_audit, "live", lambda _pid: state["alive"])
    monkeypatch.setattr(Path, "open", open_path)
    with pytest.raises(ValueError, match="still-running"):
        stream.read_streams(directory, **options)
    assert not state["snapshot_taken"]
    assert state["alive"]


def test_timing_must_precede_line_records(evidence):
    directory, path, header, payload, options = evidence
    path.write_bytes(header + payload[6:] + payload[:6])
    with pytest.raises(ValueError, match="framing"):
        stream.read_streams(directory, **options)


def test_audited_spawn_time_must_precede_producer_birth(evidence):
    directory, _, _, _, options = evidence
    intent = next((options["audit_control"] / "audit").iterdir())
    text = intent.read_text().rstrip()
    intent.write_text(text.rsplit(" ", 1)[0] + f" {time.monotonic_ns()}\n")
    with pytest.raises(ValueError, match="freshness"):
        stream.read_streams(directory, **options)


@pytest.mark.parametrize("event", ["os.fork", "os.forkpty", "os.exec"])
def test_untraceable_native_lifecycle_fails_closed(tmp_path, monkeypatch, event):
    (tmp_path / "audit").mkdir()
    (tmp_path / "status").write_bytes(b"R")
    monkeypatch.setattr(child_audit, "_control", str(tmp_path))
    monkeypatch.setattr(child_audit, "_failed", False)
    monkeypatch.setenv(stream.ENV, "opted-in")
    child_audit._event(event, ())
    assert (tmp_path / "status").read_bytes() == b"E"


@pytest.mark.parametrize("event", ["subprocess.Popen", "os.fork", "os.forkpty", "os.exec", "os.system"])
def test_active_parent_context_survives_environment_marker_removal(tmp_path, monkeypatch, event):
    from tools.coverage_guided_selection import child_stream_parent

    (tmp_path / "audit").mkdir()
    (tmp_path / "manifests").mkdir()
    (tmp_path / "hits").mkdir()
    (tmp_path / "status").write_bytes(b"R")
    source = tmp_path / "source.py"
    source.write_text("pass\n")
    monkeypatch.setattr(child_stream_parent, "_control", str(tmp_path))
    monkeypatch.setattr(child_stream_parent, "_nonce", "a" * 64)
    monkeypatch.setattr(child_stream_parent, "_contexts", {})
    monkeypatch.setattr(child_stream_parent, "_inventory", stream.source_inventory({str(source): "source.py"}))
    monkeypatch.setattr(child_audit, "_control", str(tmp_path))
    monkeypatch.setattr(child_audit, "_nonce", "a" * 64)
    monkeypatch.setattr(child_audit, "_python", child_audit.key(os.sys.executable))
    monkeypatch.setattr(child_audit, "_failed", False)
    monkeypatch.setattr(child_audit, "_child", False)
    monkeypatch.setattr(child_audit, "_own_context", None)
    monkeypatch.setenv(stream.ENV, "temporary")
    child_stream_parent.register_context("tests/test_source.py::test_parent")
    monkeypatch.delenv(stream.ENV)
    arguments = (os.sys.executable, [os.sys.executable, "-I", "-S", "-", "--help"], None, {})
    child_audit._event(event, arguments if event == "subprocess.Popen" else ())
    assert (tmp_path / "status").read_bytes() == b"E"
    assert child_audit._own_context
    with pytest.raises(ValueError, match="audit failure"):
        child_audit.verify(tmp_path, nonce="a" * 64, contexts={}, producers=[])


def test_unavailable_posix_process_query_is_not_confirmed_exit(monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX process-query error boundary")

    def denied(*_args):
        raise PermissionError("process visibility denied")

    monkeypatch.setattr(os, "kill", denied)
    assert child_audit.live(1) is True


def test_fork_reset_never_reuses_inherited_fd_seen_set_or_locked_mutex(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = (
        f"import os,sys;sys.path.insert(0,{str(root)!r});"
        "from tools.coverage_guided_selection import child_stream as s;"
        f"fd=os.open({str(tmp_path / 'owned')!r},os.O_CREAT|os.O_WRONLY,0o600);"
        "s._fd=fd;s._lock.acquire();s._seen.add((1,1));s._after_fork();"
        "assert s._fd is None and s._failed and not s._seen and not s._lock.locked();"
        "\n"
        "try:os.fstat(fd)\n"
        "except OSError:pass\n"
        "else:raise AssertionError('inherited descriptor remains open')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr


def test_bootstrap_failure_maps_immutable_context_and_sanitized_lineage(tmp_path, monkeypatch):
    import json
    (tmp_path / "audit").mkdir()
    (tmp_path / "status").write_bytes(b"R")
    token = "b" * 64
    monkeypatch.setenv(stream.ENV, str(tmp_path / "manifests" / (token + ".manifest")))
    monkeypatch.setattr(child_audit, "_control", None)
    monkeypatch.setattr(child_audit, "_failed", False)
    error = child_audit.LineageError({
        "expected_auditor_pid": 101, "producer_pid": 202,
        "actual_parent_chain": [202, 303, 404, 101], "path": "must-not-be-retained",
        "actual_parent_images": ["python.exe", "python.exe", "uv.exe", "python.exe", "private-name.exe"],
    })
    child_audit.report_bootstrap_error(tmp_path, error)
    path = next((tmp_path / "audit").iterdir())
    record = json.loads(path.read_text())
    assert record["context_digest"] == token
    assert record["lineage"]["actual_parent_chain"] == [202, 303, 404, 101]
    assert "path" not in record["lineage"]
    assert record["lineage"]["actual_parent_images"][-1] == "other"
    assert str(tmp_path) not in path.read_text()
    with pytest.raises(ValueError, match="tests/test_sdk.py::test_consumer"):
        child_audit.verify(
            tmp_path, nonce="a" * 64, contexts={token: "tests/test_sdk.py::test_consumer"},
            producers=[],
        )


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_STANDALONE_INTEGRATION") != "1",
    reason="requires deliberately prepared native installed fleet environment",
)
class TestChildStreamNative:
    """Persisted real-process regressions; never invokes full baseline collection."""

    def _run(self, source=None, *, pair=False, ordinary=False, selection=None, reference_code=None):
        import hashlib
        import json
        import subprocess
        import tempfile
        from pathlib import Path
        from tools.coverage_guided_selection import standalone as s

        repo = s.REPO
        python = s.runner.default_python("fleet-manager")
        environment = python.parent.parent
        lease = s.runner.acquire(60, environment)
        temporary = tempfile.TemporaryDirectory(prefix="c", dir=repo / ".pytmp")
        hook = None
        try:
            sandbox = Path(temporary.name)
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            probe = subprocess.run(
                [str(python), "-I", "-c", "import importlib.util;from pathlib import Path;"
                 "print(Path(importlib.util.find_spec('fleet_manager').origin).parent)"],
                check=True, capture_output=True, text=True, **flags,
            )
            installed = Path(probe.stdout.strip())
            mapping, hashes = s.verified_source_mapping(repo, installed, environment)
            self._last_source_mapping = mapping
            if reference_code is not None:
                reference_sandbox = sandbox / "reference-run"
                reference_env = s.isolated_environment(os.environ, reference_sandbox)
                for name in (
                    "PYTEST_ADDOPTS", "PYTHONPATH", "COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG",
                    "_CGS_CHILD_MANIFEST", "_CGS_CHILD_AUDITOR",
                ):
                    reference_env.pop(name, None)
                reference_driver = sandbox / "reference.py"
                reference_driver.write_text(
                    "import coverage,json\nfrom pathlib import Path\n"
                    f"c=coverage.Coverage(config_file=False,data_file={str(sandbox / 'reference.coverage')!r},"
                    f"source=[{str(installed)!r}]);c.start()\n"
                    + reference_code + "\nc.stop();d=c.get_data()\n"
                    f"Path({str(sandbox / 'reference.json')!r}).write_text(json.dumps("
                    "{f:sorted(d.lines(f)) for f in d.measured_files() if d.lines(f)}))\n",
                    encoding="utf-8",
                )
                assert s.run_contained(
                    [str(python), "-I", str(reference_driver)], cwd=repo / "fleet-manager",
                    env=reference_env, sandbox=reference_sandbox, limits=s.Limits(wall_seconds=90),
                ) == 0
            text, options = s.coverage_files(sandbox, mapping)
            path = installed.parent / "_cgs_standalone.pth"
            with path.open("x", encoding="utf-8") as file:
                hook = path
                file.write(text)
            env = s.isolated_environment(os.environ, sandbox)
            for name in (
                "PYTEST_ADDOPTS", "PYTHONPATH", "COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG",
                "_CGS_CHILD_MANIFEST", "_CGS_CHILD_AUDITOR",
            ):
                env.pop(name, None)
            env["CGS_TEST_HOOK"] = str(hook)
            env["CGS_TEST_STREAM"] = options["_cgs_child_stream"]
            env["CGS_TEST_AUDIT"] = options["_cgs_child_audit"]
            env["CGS_TEST_CONTROL"] = options["child_stream_control"]
            env["CGS_TEST_INSTALLED"] = str(installed)
            env["CGS_TEST_SOURCE"] = str(repo / "fleet-manager/src")
            test = sandbox / "test_native_stream.py"
            if source is not None:
                test.write_text(source, encoding="utf-8")
            selected = selection or (["tests/test_two_node_recovery.py"] if pair else [str(test)])
            driver = sandbox / "driver.py"
            driver.write_text(s._DRIVER_SCRIPT, encoding="utf-8")
            out = sandbox / "result.json"
            command = (
                [str(python), "-I", "-m", "pytest", "-q", *selected, "--basetemp", str(sandbox / "p")]
                if ordinary else [
                    str(python), "-I", str(driver), json.dumps(selected), s.SOURCE, str(repo),
                    str(sandbox / ".coverage"), str(sandbox / "report.json"), str(out),
                    str(sandbox / "p"), json.dumps({
                        "installed_source": str(installed), "source_mapping": mapping,
                        "compact_tmp_paths": os.name == "nt", **options,
                    }),
                ]
            )
            started = time.perf_counter()
            status = s.run_contained(
                command, cwd=repo / "fleet-manager", env=env, sandbox=sandbox,
                limits=s.Limits(wall_seconds=90),
            )
            elapsed = time.perf_counter() - started
            data = json.loads(out.read_text()) if out.exists() else None
            report = sandbox / "report.json"
            self._last_report = json.loads(report.read_text()) if report.exists() else None
            nodes = {
                hashlib.sha256(row["nodeid"].encode()).hexdigest(): row["nodeid"]
                for row in (self._last_report or {}).get("tests", [])
            }
            self._last_errors = []
            for error in sorted(
                (sandbox / "child-lines" / "audit").glob("*.error"),
                key=lambda path: path.stat().st_mtime_ns,
            ):
                record = json.loads(error.read_text())
                record["pytest_node"] = nodes.get(record.get("context_digest"), "unknown-context")
                self._last_errors.append(record)
            self._last_sdk = []
            if selection:
                for target in (sandbox / "p").rglob("installed-sdk"):
                    _, sdk_hashes = s.verified_source_mapping(repo, target / "fleet_manager", sandbox)
                    self._last_sdk.append({
                        "copied_wheel_modules": len(sdk_hashes), "canonical_sha256": sdk_hashes,
                        "source_equivalent": True,
                    })
            reference = sandbox / "reference.json"
            reference = json.loads(reference.read_text()) if reference.exists() else None
            assert s.verified_source_mapping(repo, installed, environment) == (mapping, hashes)
            return status, data, reference, elapsed
        finally:
            if hook is not None:
                hook.unlink(missing_ok=True)
            s.runner.cleanup_sandbox(temporary)
            lease.release()

    def test_exact_line_parity_threads_crashes_union_and_selection(self):
        import json
        from tools.coverage_guided_selection.selection import select_tests
        code = (
            "import os,sys,threading;from fleet_manager.planning import canonical;"
            "canonical({'child':True});"
            "t=threading.Thread(target=lambda:canonical('thread-only'));t.start();t.join();"
        )
        source = f"CODE={code!r}\n" + r'''
import json,os,signal,subprocess,sys,threading
from pathlib import Path
import pytest
RUN = "assert 'coverage' not in __import__('sys').modules;" + CODE
FLAGS={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
def test_normal():
    assert subprocess.run([sys.executable,'-I','-c',RUN],**FLAGS).returncode==0
def test_abrupt():
    assert subprocess.run([sys.executable,'-I','-c',RUN+"os._exit(23)"],**FLAGS).returncode==23
def test_forced():
    p=subprocess.Popen([sys.executable,'-I','-c',RUN+
        "print('READY',os.getpid(),flush=True);sys.stdin.read()"],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**FLAGS)
    line=p.stdout.readline().decode()
    assert line.startswith('READY ')
    os.kill(int(line.split()[1]),signal.SIGTERM)
    p.communicate(timeout=10)
    assert p.returncode!=0
def test_zero():
    assert subprocess.run([sys.executable,'-I','-c','pass'],**FLAGS).returncode==0
'''
        status, data, reference, _ = self._run(source, reference_code=code)
        assert status == 0
        children = data["children"]
        assert children["audited_producers"] == children["child_streams"] == 4
        assert children["zero_hit_producers"] == 1
        names = {node.rsplit("::", 1)[-1]: node for node in data["durations"]}
        for name in ("test_normal", "test_abrupt", "test_forced"):
            actual = {
                file: sorted(int(line) for line, nodes in lines.items() if names[name] in nodes)
                for file, lines in data["coverage"].items()
            }
            actual = {file: lines for file, lines in actual.items() if lines}
            expected = {}
            for file, lines in reference.items():
                canonical = self._last_source_mapping[str(__import__("pathlib").Path(file).resolve())]
                expected[canonical] = lines
            assert actual == expected
        key = "fleet-manager/src/fleet_manager/planning.py"
        nodes = {names[name] for name in ("test_normal", "test_abrupt", "test_forced")}
        line = next(int(line) for line, covered in data["coverage"][key].items() if set(covered) == nodes)
        selected = select_tests({"coverage": data["coverage"]}, {key: [line]})
        assert set(selected.selected_tests) == nodes and not selected.fallback_triggered
        assert stream.merge_coverage({"parent.py": {"1": ["parent-test"]}}, data["coverage"])["parent.py"]
        print("INTEGRATED_NATIVE", json.dumps(children))

    @pytest.mark.parametrize("failure", [
        "bootstrap", "audit-disabled", "missing", "unknown", "write", "trace", "thread-trace", "live",
        "site-disabled", "site-disabled-bundled",
        "parent-context-stripped", "site-disabled-stdin",
        "source-shadow",
    ])
    def test_opted_in_failure_never_emits_successful_result(self, failure):
        source = r'''
import os,subprocess,sys
from pathlib import Path
FLAGS={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
def test_expected_application_behavior():
    CASE=CASE_VALUE
    environment=dict(os.environ)
    code="from fleet_manager.planning import canonical;canonical({'x':1})"
    arguments=[sys.executable,'-I','-c']
    if CASE=='bootstrap':
        Path(os.environ['CGS_TEST_STREAM']).write_text("raise RuntimeError('bootstrap injection')\n")
    elif CASE=='audit-disabled':
        path=Path(os.environ['CGS_TEST_AUDIT'])
        path.write_text(path.read_text().replace('sys.addaudithook(_event)', 'pass # refused hook'))
    elif CASE=='missing':
        Path(os.environ['CGS_TEST_HOOK']).unlink()
    elif CASE=='unknown':
        original=Path(environment['_CGS_CHILD_MANIFEST'])
        token='f'*64
        path=original.with_name(token+'.manifest')
        content=original.read_text().splitlines()
        fields=content[0].split('\t');fields[2]=token;content[0]='\t'.join(fields)
        path.write_text('\n'.join(content)+'\n')
        environment['_CGS_CHILD_MANIFEST']=str(path)
    elif CASE=='write':
        code=("import _cgs_child_stream as s;"
              "s._write=lambda v:(_ for _ in ()).throw(OSError('write injection'));"+code)
    elif CASE=='trace':
        code="import sys;sys.settrace(None);"+code
    elif CASE=='thread-trace':
        code="import threading;threading.settrace(None);"+code
    elif CASE=='live':
        code+=";import time;print('READY',flush=True);time.sleep(30)"
        p=subprocess.Popen([sys.executable,'-I','-c',code],env=environment,
                           stdout=subprocess.PIPE,**FLAGS)
        assert p.stdout.readline().strip()==b'READY'
        return
    elif CASE=='parent-context-stripped':
        os.environ.pop('_CGS_CHILD_MANIFEST')
        environment.pop('_CGS_CHILD_MANIFEST')
    elif CASE=='source-shadow':
        environment['PYTHONPATH']=os.environ['CGS_TEST_SOURCE']
        arguments=[sys.executable,'-c']
    elif CASE.startswith('site-disabled'):
        package=str(Path(os.environ['CGS_TEST_INSTALLED']).parent)
        code="import sys;sys.path.insert(0,"+repr(package)+");"+code
        arguments=[sys.executable,*(['-IS'] if CASE.endswith('bundled') else ['-I','-S']),'-c']
        if CASE=='site-disabled-stdin':
            assert subprocess.run([sys.executable,'-I','-S','-','--help'],
                                  input=code,text=True,env=environment,**FLAGS).returncode==0
            return
    assert subprocess.run([*arguments,code],env=environment,**FLAGS).returncode==0
'''
        status, data, _, _ = self._run(source.replace("CASE_VALUE", repr(failure)))
        assert status != 0 and data is None
        assert self._last_report["summary"]["passed"] == 1

    def test_real_recovery_pair_original_deadlines(self):
        import json
        ordinary, _, _, ordinary_wall = self._run(pair=True, ordinary=True)
        assert ordinary == 0
        status, data, _, wall = self._run(pair=True)
        assert status == 0 and data["summary"]["passed"] == 2
        assert data["children"]["audited_producers"] == 12
        print("REAL_PAIR", json.dumps({
            "ordinary_wall_s": ordinary_wall, "integrated_wall_s": wall,
            "wall_ratio": wall / ordinary_wall, "children": data["children"],
        }))

    def test_sdk_isolation_reports_exact_consumer_and_lineage(self):
        import json
        node = (
            "tests/test_artifact_staging.py::"
            "test_actual_installed_authenticated_artifact_staging_has_no_execution_or_network"
        )
        status, data, _, elapsed = self._run(selection=[node])
        assert status != 0 and data is None and self._last_report["summary"]["passed"] == 1
        assert self._last_sdk and self._last_sdk[0]["source_equivalent"]
        assert all(row["pytest_node"] == node for row in self._last_errors)
        print("SDK_BOUNDARY", json.dumps({
            "application_passed": 1, "collector_status": status, "wall_s": elapsed,
            "failures": self._last_errors, "copied_sdk": self._last_sdk,
            "owner_rejection_reproduced": any(row.get("lineage") for row in self._last_errors),
        }, sort_keys=True))
