"""Sparse stdlib-only child line tracing; no subprocess interception.

The parent owns source equivalence, context identities, bounds and schema-v3
merging. This module grants no baseline publication or selection authority.
"""

import hashlib
import os
import sys
import _thread
import threading
import time
from types import CodeType

ENV = "_CGS_CHILD_MANIFEST"
VERSION = "CGS-LINES/3"
MAX_RECORDS = 100_000
MAX_MANIFEST = 65_536
_started = False
_failed = False
_fd = None
_seen = set()
_lock = threading.Lock()
_sources = {}
_code_sources = {}
_expected_codes = {}
_generator_codes = frozenset()
_generated_receipts = []
_generated_count = 0
_generated_state = threading.local()
_generated_lock = threading.RLock()
_generated_active = False
_thread_guard_active = False
_start_thread_original = _thread.start_new_thread
_start_thread_alias_original = _thread.start_new
_thread_start_code = threading.Thread.start.__code__
_thread_settrace_code = threading.settrace.__code__
_thread_setprofile_code = threading.setprofile.__code__
_thread_settrace_globals = threading.settrace.__globals__
_thread_setprofile_globals = threading.setprofile.__globals__
_profile_arming = False
_profile_owner = None
_prior_profile = _prior_thread_profile = None
_root = ""
_package = ""
_control = None
_arming = False
_phase_marker = None
_phase_reporter = None


def _phase(name):
    if _phase_marker is not None:
        _phase_marker(name)


def _auditor():
    value = sys.modules.get("_cgs_child_audit")
    if value is None:
        from tools.coverage_guided_selection import child_audit
        value = child_audit
    return value


def _write(value):
    while value:
        count = os.write(_fd, value)
        if count <= 0:
            raise OSError("short child coverage write")
        value = value[count:]


def _fail():
    global _failed
    _failed = True
    _auditor().error("child trace bounds or write failure")
    try:
        os.lseek(_fd, 0, os.SEEK_SET)
        os.write(_fd, b"E")
    except OSError:
        pass


def _source(filename):
    value = _sources.get(filename)
    if value is None:
        scoped = (filename.lower() if os.name == "nt" else filename).startswith(_root)
        if scoped:
            value = _sources.get(os.path.normcase(filename))
            if value is None and filename.endswith(".py"):
                _auditor().error("unmapped code filename inside verified source scope")
    return value


def _code_tree(code):
    yield code
    for value in code.co_consts:
        if isinstance(value, CodeType):
            yield from _code_tree(value)


def _prepare_sources(inventory, *, compile_sources=True):
    """Verify inventory; ordinary tracers also prepare executable references."""
    for filename, (identifier, maximum, digest) in inventory.items():
        with open(filename, "rb") as source:
            content = source.read()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("executable source differs from verified source inventory")
        key = os.path.normcase(filename)
        _sources[filename] = _sources[key] = (identifier, maximum)
        if compile_sources:
            reference = compile(content, filename, "exec", dont_inherit=True,
                                optimize=sys.flags.optimize)
            _expected_codes[key] = frozenset(_code_tree(reference))


def _install_generated_guard():
    global _generator_codes, _generated_active
    path = os.path.join(os.path.dirname(os.__file__), "dataclasses.py")
    with open(path, "rb") as source:
        content = source.read(1_048_577)
    if len(content) > 1_048_576:
        raise ValueError("standard-library generator source exceeds bound")
    reference = compile(content, path, "exec", dont_inherit=True, optimize=sys.flags.optimize)
    _generator_codes = frozenset(code for code in _code_tree(reference)
                                if code.co_name == "_create_fn")
    if not _generator_codes:
        raise ValueError("unsupported standard-library dataclass generator")
    _generated_active = True
    sys.addaudithook(_generated_event)


def _generated_event(event, args):
    global _generated_count
    if not _generated_active or getattr(_generated_state, "busy", False) or event not in {"compile", "exec"}:
        return
    emitter = sys._getframe(0).f_back
    if emitter is None or emitter.f_code not in _generator_codes:
        return
    owner = emitter.f_back
    while owner is not None:
        if owner.f_code in _expected_codes.get(os.path.normcase(owner.f_code.co_filename), ()):
            break
        owner = owner.f_back
    if owner is None:
        return
    if event == "compile":
        content, filename = args
        if not isinstance(content, (bytes, str)) or len(content) > MAX_MANIFEST:
            _auditor().error("generated source exceeds verified bounds")
        else:
            with _generated_lock:
                full = len(_generated_receipts) >= 512
                if not full:
                    _generated_receipts.append((content, filename, emitter.f_code))
            if full:
                _auditor().error("generated source receipt bound exceeded")
        return
    code = args[0]
    with _generated_lock:
        receipts = tuple(_generated_receipts)
    for content, filename, origin in reversed(receipts):
        if filename != code.co_filename or origin is not emitter.f_code:
            continue
        _generated_state.busy = True
        try:
            reference = compile(content, filename, "exec", flags=code.co_flags,
                                dont_inherit=True, optimize=sys.flags.optimize)
        finally:
            _generated_state.busy = False
        if reference != code:
            continue
        actual = tuple(_code_tree(code))
        with _generated_lock:
            unregistered = [value for value in actual
                            if _code_sources.get(id(value), (None,))[0] is not value]
            full = _generated_count + len(unregistered) > 4096
            if not full:
                _generated_count += len(unregistered)
                for value in unregistered:
                    # Proven generated methods have no fixed repository line coordinates.
                    _code_sources[id(value)] = (value, None)
        if full:
            _auditor().error("verified generated code-object bound exceeded")
        return


def _start_thread(*args, **kwargs):
    if _thread_guard_active and sys._getframe(1).f_code is not _thread_start_code:
        _auditor().error("unsupported low-level thread coverage lifecycle")
    return _start_thread_original(*args, **kwargs)


def _install_thread_guard():
    global _thread_guard_active
    _thread_guard_active = True
    _thread.start_new_thread = _thread.start_new = threading._start_new_thread = _start_thread


def _thread_profile(frame, event, arg):
    if event == "c_call" and (arg is _start_thread_original or arg is _start_thread_alias_original):
        if frame.f_code not in (_thread_start_code, _start_thread.__code__):
            _auditor().error("unsupported low-level thread coverage lifecycle")
    elif event == "call" and _started:
        if (frame.f_code is _thread_settrace_code and not _arming
                and frame.f_globals is _thread_settrace_globals
                and frame.f_locals.get(_thread_settrace_code.co_varnames[0]) is not _call):
            _auditor().error("child thread tracer was replaced")
        if (frame.f_code is _thread_setprofile_code and not _profile_arming
                and frame.f_globals is _thread_setprofile_globals
                and frame.f_locals.get(_thread_setprofile_code.co_varnames[0]) is not _thread_profile):
            _auditor().error("child thread-start profile guard was replaced")
    if _prior_profile is not None or _prior_thread_profile is not None:
        previous = _prior_profile if threading.get_ident() == _profile_owner else _prior_thread_profile
        if previous is not None:
            previous(frame, event, arg)


def stop_import_profile():
    global _prior_profile, _prior_thread_profile
    _prior_profile = _prior_thread_profile = None


def _frame_source(frame):
    code = frame.f_code
    cached = _code_sources.get(id(code))
    if cached is not None and cached[0] is code:
        return cached[1]
    source = _source(code.co_filename)
    if source is not None and code not in _expected_codes.get(os.path.normcase(code.co_filename), ()):
        _auditor().error("executed code differs from verified source inventory")
        source = None
    if source is None and _package:
        module = getattr(frame, "f_globals", {}).get("__name__", "")
        package_path = os.path.normcase(code.co_filename).replace("\\", "/").split("/")[:-1]
        if (isinstance(module, str) and (module == _package or module.startswith(_package + "."))
                or os.path.normcase(_package) in package_path):
            _auditor().error("owned package executed outside verified source inventory")
    if source is not None or os.path.normcase(code.co_filename) in _expected_codes:
        # Retain the object itself: recycled ids must never inherit an attribution.
        _code_sources[id(code)] = (code, source)
    return source


def _line(frame, event, arg):
    if event == "line" and not _failed:
        source = _frame_source(frame)
        if source is not None:
            identifier, maximum = source
            line = frame.f_lineno
            if line <= 0:
                return _line
            key = (identifier, line)
            if key in _seen:
                return _line
            with _lock:
                if key not in _seen:
                    if not 0 < line <= maximum or len(_seen) >= MAX_RECORDS:
                        _fail()
                    else:
                        try:
                            _write(identifier.to_bytes(2, "big") + line.to_bytes(4, "big"))
                            # Publish only after the unbuffered record is committed.
                            _seen.add(key)
                        except OSError:
                            _fail()
    return _line


def _call(frame, event, arg):
    return _line if _frame_source(frame) is not None else None


def _after_fork():
    global _fd, _failed, _lock, _seen, _profile_arming
    global _generated_state, _generated_lock
    # Fork is explicitly refused by the audit contract. Never reuse its lock,
    # descriptor or sparse seen-set while the application finishes normally.
    if _fd is not None:
        os.close(_fd)
    _fd, _failed, _lock, _seen = None, True, threading.Lock(), set()
    _generated_state, _generated_lock = threading.local(), threading.RLock()
    sys.settrace(None)
    threading.settrace(None)
    _profile_arming = True
    try:
        sys.setprofile(None)
        threading.setprofile(None)
    finally:
        _profile_arming = False
    stop_import_profile()


def start(control, *, compile_sources=True):
    global _started, _fd, _root, _package, _control, _arming
    global _profile_arming, _profile_owner, _prior_profile, _prior_thread_profile
    value = os.environ.get(ENV)
    if _started or not value:
        return
    _control = control
    if (
        os.path.islink(value)
        or os.path.dirname(os.path.realpath(value))
        != os.path.realpath(os.path.join(control, "manifests"))
    ):
        raise ValueError("unowned child context manifest")
    start_marker = os.environ.get("_CGS_PRELUDE_NS")
    started = int(start_marker) if start_marker is not None else time.perf_counter_ns()
    with open(value, "rb") as stream:
        content = stream.read(MAX_MANIFEST + 1)
    _phase("manifest-read")
    if len(content) > MAX_MANIFEST:
        raise ValueError("child coverage manifest exceeds bound")
    rows = content.decode("utf-8").splitlines()
    version, nonce, context, directory, root = rows[0].split("\t")
    with open(os.path.join(control, "run"), encoding="ascii") as handle:
        run = handle.read(65)
    if version != VERSION or nonce != run or any(
        len(token) != 64 or any(c not in "0123456789abcdef" for c in token)
        for token in (nonce, context)
    ):
        raise ValueError("invalid child coverage manifest identity")
    directory, root = (bytes.fromhex(field).decode("utf-8") for field in (directory, root))
    if os.path.islink(directory) or os.path.realpath(directory) != os.path.realpath(
        os.path.join(control, "hits")
    ):
        raise ValueError("unowned child stream directory")
    _root = os.path.normcase(root)
    _package = os.path.basename(root.rstrip("/\\"))
    identifiers, filenames, inventory = set(), set(), {}
    for row in rows[1:]:
        identifier, maximum, filename, digest = row.split("\t")
        identifier, maximum = int(identifier), int(maximum)
        filename = bytes.fromhex(filename).decode("utf-8")
        if (
            not 0 < identifier < 65535 or maximum < 0 or not filename.startswith(root)
            or identifier in identifiers or os.path.normcase(filename) in filenames
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("invalid child source bounds")
        identifiers.add(identifier)
        filenames.add(os.path.normcase(filename))
        inventory[filename] = (identifier, maximum, digest)
    _prepare_sources(inventory, compile_sources=compile_sources)
    _phase("source-code-ready" if compile_sources else "source-inventory-ready")
    _install_generated_guard()
    _phase("generated-code-ready")
    _install_thread_guard()
    if not _sources:
        raise ValueError("empty child coverage source map")
    _phase("source-map-ready")
    auditor = _auditor()
    owner = auditor.owner_pid()
    _phase("owner-checked")
    auditor.install(control, sys.executable, nonce, child=True, token=context)
    _phase("audit-installed")
    if auditor.context(dict(os.environ)) != context:
        raise ValueError("child context differs from owned manifest")
    _phase("context-rechecked")
    arguments = auditor.argv_key(sys.orig_argv)
    _phase("argv-digest-ready")
    born = time.monotonic_ns()
    path = os.path.join(directory, f"{os.getpid()}-{os.urandom(16).hex()}.hits")
    _fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    _write(f"R {VERSION} {nonce} {context} {os.getpid()} {owner} {arguments} {born}\n".encode("ascii"))
    _started = True
    _phase("stream-header-written")
    with _lock:
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=_after_fork)
        _arming = True
        try:
            threading.settrace(_call)
            sys.settrace(_call)
        finally:
            _arming = False
        original = threading.settrace
        def set_thread_trace(function):
            if function is not _call:
                auditor.error("child thread tracer was replaced")
            return original(function)
        threading.settrace = set_thread_trace
        _prior_profile, _prior_thread_profile = sys.getprofile(), threading.getprofile()
        if any(value is not None and not callable(value)
               for value in (_prior_profile, _prior_thread_profile)):
            raise ValueError("child thread-start guard cannot preserve an opaque profiler")
        _profile_owner = threading.get_ident()
        _profile_arming = True
        try:
            threading.setprofile(_thread_profile)
            sys.setprofile(_thread_profile)
        finally:
            _profile_arming = False
        original_profile = threading.setprofile
        def set_thread_profile(function):
            if not _profile_arming and function is not _thread_profile:
                auditor.error("child thread-start profile guard was replaced")
            return original_profile(function)
        threading.setprofile = set_thread_profile
        if sys.getprofile() is not _thread_profile or threading.getprofile() is not _thread_profile:
            raise ValueError("child thread-start profile guard was not installed")
        # Publish timing after all required setup, before any line writer can run.
        finished = time.perf_counter_ns()
        finished_cpu = time.process_time_ns() if _phase_reporter is not None else None
        duration = (finished - started) // 1000
        _write(b"\0\0" + duration.to_bytes(4, "big"))
    if _phase_reporter is not None:
        _phase_reporter(control,start_ns=started,end_ns=finished,end_cpu_ns=finished_cpu,duration_us=duration)
