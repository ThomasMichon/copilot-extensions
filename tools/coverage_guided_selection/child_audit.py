"""Nonblocking, collector-owned CPython native-spawn completeness accounting."""

import hashlib
import os
import sys
import time

ENV = "_CGS_CHILD_MANIFEST"
OWNER = "_CGS_CHILD_AUDITOR"
VERSION = "CGS-AUDIT/1"
_control = None
_python = None
_nonce = None
_installed = False
_failed = False
_child = False
_own_context = None
_probe = None


def key(value):
    return os.path.normcase(os.path.abspath(os.fsdecode(value)))


def check_seal(control, *, nonce):
    path = os.path.join(control, "sealed")
    if os.path.islink(path):
        raise ValueError("linked collection seal")
    try:
        with open(path, "rb") as source:
            content = source.read(65)
    except FileNotFoundError as exc:
        raise ValueError("missing collection seal") from exc
    if content != nonce.encode("ascii"):
        raise ValueError("collection seal identity changed")


def seal(control, *, nonce):
    path = os.path.join(control, "run")
    if os.path.islink(path):
        raise ValueError("linked collection run identity")
    with open(path, "rb") as source:
        content = source.read(65)
    if content != nonce.encode("ascii"):
        raise ValueError("collection run identity changed before sealing")
    try:
        with open(os.path.join(control, "sealed"), "xb") as output:
            output.write(content)
    except FileExistsError:
        check_seal(control, nonce=nonce)


def argv_bytes(arguments):
    return b"\0".join(os.fsencode(value) for value in arguments[1:])


def argv_key(arguments):
    if not isinstance(arguments, (list, tuple)):
        raise ValueError("native argv must be a sequence")
    data = argv_bytes(arguments)
    if len(data) > 65536:
        raise ValueError("native argv exceeds bound")
    return hashlib.sha256(data).hexdigest()


def native_argv(arguments):
    if isinstance(arguments, (list, tuple)):
        return arguments
    if os.name != "nt" or not isinstance(arguments, str) or len(arguments) > 32768:
        raise ValueError("opaque native command line")
    import ctypes
    from ctypes import wintypes
    count = ctypes.c_int()
    shell = ctypes.WinDLL("shell32")
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    value = shell.CommandLineToArgvW(arguments, ctypes.byref(count))
    if not value:
        raise ValueError("invalid native command line")
    kernel = ctypes.WinDLL("kernel32")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        return [value[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(value)


def _context_digest():
    if _own_context:
        return _own_context
    name = os.path.basename(os.environ.get(ENV, ""))
    token = name.removesuffix(".manifest")
    return token if len(token) == 64 and all(c in "0123456789abcdef" for c in token) else "unknown"


def _safe_lineage(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("expected_auditor_pid", "producer_pid"):
        pid = value.get(key)
        if type(pid) is int and 0 < pid < 2**32:
            result[key] = pid
    chain = value.get("actual_parent_chain", [])
    result["actual_parent_chain"] = [
        pid for pid in (chain[:8] if isinstance(chain, (list, tuple)) else [])
        if type(pid) is int and 0 < pid < 2**32
    ]
    images = value.get("actual_parent_images", [])
    allowed = {"python.exe", "python3.exe", "python", "python3", "uv.exe", "uv", "unknown"}
    result["actual_parent_images"] = [
        image if image in allowed else "other"
        for image in (images[:8] if isinstance(images, (list, tuple)) else [])
    ]
    return result


def error(reason, *, lineage=None):
    global _failed
    if _failed:
        return
    _failed = True
    if _control:
        try:
            fd = os.open(os.path.join(_control, "status"), os.O_WRONLY)
            try:
                os.write(fd, b"E")
            finally:
                os.close(fd)
            path = os.path.join(_control, "audit", f"{os.getpid()}-{os.urandom(16).hex()}.error")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                import json
                record = {
                    "schema": "CGS-ERROR/1", "context_digest": _context_digest(),
                    "producer_pid": os.getpid(), "observed_ns": time.monotonic_ns(),
                    "reason": reason[:512], "lineage": _safe_lineage(lineage),
                }
                os.write(fd, json.dumps(record, separators=(",", ":")).encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass


def report_bootstrap_error(control, exception):
    global _control
    _control = str(control)
    reason = "child bootstrap failed: " + type(exception).__name__
    if isinstance(exception, ValueError):
        reason += ": " + str(exception)[:256]
    error(reason, lineage=getattr(exception, "lineage", None))


def context(environment):
    path = environment.get(ENV)
    if not path:
        return None
    root = os.path.join(_control, "manifests")
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(root) or os.path.islink(path):
        raise ValueError("unknown child context manifest")
    with open(path, "rb") as handle:
        header = handle.readline(65537)
    if len(header) > 65536:
        raise ValueError("child manifest header exceeds bound")
    fields = header.decode("utf-8").rstrip("\n").split("\t")
    if len(fields) != 5 or fields[1] != _nonce or len(fields[2]) != 64:
        raise ValueError("unknown child context identity")
    if os.path.basename(path) != fields[2] + ".manifest":
        raise ValueError("context filename mismatch")
    return fields[2]


def python_mode(arguments):
    """Classify interpreter options only; never treat program arguments as flags."""
    disabled = False
    arguments = iter(arguments[1:])
    for argument in arguments:
        argument = os.fsdecode(argument)
        if argument in {"-", "-c", "-m", "--"} or not argument.startswith("-"):
            break
        if argument in {"-X", "-W", "--check-hash-based-pycs"}:
            next(arguments, None)
            continue
        if argument.startswith(("-X", "-W")):
            continue
        if argument in {"--help", "--help-env", "--help-xoptions", "--help-all", "--version"}:
            return "information"
        if not argument.startswith("--"):
            for position, flag in enumerate(argument[1:], 1):
                if flag in "cm":
                    return "site-disabled" if disabled else "instrumented"
                if flag in "XW":
                    if position == len(argument) - 1:
                        next(arguments, None)
                    break
                if flag in "Vh?":
                    return "information"
                if flag == "S":
                    disabled = True
    return "site-disabled" if disabled else "instrumented"


def _event(event, arguments):
    global _probe
    try:
        if event == "_cgs.audit_probe":
            _probe = arguments[0]
            return
        if event in {"os.fork", "os.forkpty", "os.exec"}:
            if _child or _own_context or os.environ.get(ENV):
                error("unsupported fork/exec coverage lifecycle")
            return
        if event == "os.system":
            if _child or _own_context or os.environ.get(ENV):
                error("unsupported opaque system command coverage lifecycle")
            return
        if event == "sys.settrace" and _child:
            stream = sys.modules.get("_cgs_child_stream")
            if stream is not None and not stream._arming:
                import threading
                caller = sys._getframe(1)
                if (
                    caller.f_code is not threading.Thread._bootstrap_inner.__code__
                    or threading.gettrace() is not stream._call
                ):
                    error("child line tracer was replaced")
            return
        if event == "sys.setprofile" and _child:
            stream = sys.modules.get("_cgs_child_stream")
            if stream is not None and not stream._profile_arming:
                import threading
                caller = sys._getframe(1)
                if (
                    caller.f_code is not threading.Thread._bootstrap_inner.__code__
                    or threading.getprofile() is not stream._thread_profile
                ):
                    error("child thread-start profile guard was replaced")
            return
        if event not in {"subprocess.Popen", "os.posix_spawn"}:
            return
        if event == "os.posix_spawn":
            # Popen already emitted its own audited intent for this operation.
            if sys._getframe(1).f_globals.get("__name__") == "subprocess":
                return
            executable, argv, environment = arguments
        else:
            executable, argv, _, environment = arguments
        environment = os.environ if environment is None else environment
        if not environment.get(ENV):
            if _child or _own_context or os.environ.get(ENV):
                argv = native_argv(argv)
                if key(executable or argv[0]) == _python and python_mode(argv) == "information":
                    return
                error("instrumented process stripped native child coverage context")
            return
        argv = native_argv(argv)
        executable_key = key(executable or argv[0])
        is_python = executable_key == _python
        if not is_python:
            error("unmanaged native executable inherited child instrumentation")
            return
        mode = python_mode(argv)
        if mode == "information":
            return
        if mode == "site-disabled":
            error("site-disabled Python code execution context=" + context(environment))
            return
        token = context(environment)
        if os.path.lexists(os.path.join(_control, "sealed")):
            error("native child launch after collection seal")
        if _own_context and token != _own_context:
            error("instrumented process attempted to change its test context")
            return
        if environment.get(OWNER) != str(os.getpid()):
            error("child auditor ownership marker missing or stale")
            return
        path = os.path.join(_control, "audit", f"{os.getpid()}-{os.urandom(16).hex()}.intent")
        data = f"{VERSION} {_nonce} {token} {os.getpid()} {argv_key(argv)} {time.monotonic_ns()}\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if os.write(fd, data.encode("ascii")) != len(data):
                raise OSError("short audit intent write")
        finally:
            os.close(fd)
        if os.path.lexists(os.path.join(_control, "sealed")):
            error("native child launch after collection seal")
    except Exception as exc:
        error("native spawn audit failed: " + type(exc).__name__ + ": " + str(exc)[:256])


def install(control, python, nonce, *, child=False, token=None):
    global _control, _python, _nonce, _installed, _child, _own_context
    _control, _python, _nonce, _child = str(control), key(python), nonce, child
    _own_context = token
    os.environ[OWNER] = str(os.getpid())
    if not _installed:
        sys.addaudithook(_event)
        marker = os.urandom(16).hex()
        sys.audit("_cgs.audit_probe", marker)
        if _probe != marker:
            error("native audit hook registration was refused")
            raise ValueError("native audit hook registration was refused")
        _installed = True
    if child and os.path.lexists(os.path.join(_control, "sealed")):
        error("native child started after collection seal")


def _windows_parent(pid):
    import ctypes
    from ctypes import wintypes

    class BasicInformation(ctypes.Structure):
        _fields_ = [("reserved", ctypes.c_void_p), ("peb", ctypes.c_void_p),
                    ("reserved2", ctypes.c_void_p * 2), ("pid", ctypes.c_void_p),
                    ("parent", ctypes.c_void_p)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        query = ctypes.WinDLL("ntdll").NtQueryInformationProcess
        query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p,
                          wintypes.ULONG, ctypes.c_void_p]
        query.restype = wintypes.LONG
        value = BasicInformation()
        if query(handle, 0, ctypes.byref(value), ctypes.sizeof(value), None) == 0:
            return value.parent
        return None
    finally:
        kernel.CloseHandle(handle)


class LineageError(ValueError):
    def __init__(self, lineage):
        super().__init__("native producer has no matching audited parent")
        self.lineage = lineage


def _windows_image(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return "unknown"
    try:
        size = wintypes.DWORD(32768)
        value = ctypes.create_unicode_buffer(size.value)
        if kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)):
            name = os.path.basename(value.value).lower()
            return name if name in {"python.exe", "python3.exe", "uv.exe"} else "other"
        return "unknown"
    finally:
        kernel.CloseHandle(handle)


def owner_pid():
    """Validate the inherited auditor, including a Windows venv redirector."""
    expected = int(os.environ[OWNER])
    parent = os.getppid()
    redirector_parent = _windows_parent(parent) if os.name == "nt" and expected != parent else None
    if expected == parent or redirector_parent == expected:
        return expected
    chain = [os.getpid(), parent]
    if redirector_parent:
        chain.append(redirector_parent)
    while os.name == "nt" and len(chain) < 8 and chain[-1] != expected:
        following = _windows_parent(chain[-1])
        if not following or following in chain:
            break
        chain.append(following)
    raise LineageError({
        "expected_auditor_pid": expected, "producer_pid": os.getpid(),
        "actual_parent_chain": chain,
        "actual_parent_images": [_windows_image(pid) for pid in chain] if os.name == "nt" else [],
    })


def live(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError as exc:
            if isinstance(exc, ProcessLookupError):
                return False
            return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() not in (87, 1168)
    try:
        code = wintypes.DWORD()
        return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
    finally:
        kernel.CloseHandle(handle)


def verify(control, *, nonce, contexts, producers):
    import re
    from collections import defaultdict
    from pathlib import Path
    if _failed or (Path(control) / "status").read_bytes() != b"R":
        paths = sorted(
            (Path(control) / "audit").glob("*.error"), key=lambda path: path.stat().st_mtime_ns,
        )
        import json
        reasons = []
        for path in paths[:8]:
            try:
                with path.open("rb") as handle:
                    raw = handle.read(4097)
                if path.is_symlink() or len(raw) > 4096:
                    raise ValueError("invalid child error size/path")
                record = json.loads(raw)
                if not isinstance(record, dict) or record.get("schema") != "CGS-ERROR/1":
                    raise ValueError("invalid child error schema")
                token = record["context_digest"]
                record["pytest_node"] = contexts.get(token, "unknown-context")
                reasons.append(json.dumps(record, sort_keys=True))
            except (OSError, ValueError, KeyError, TypeError):
                reasons.append("malformed child failure diagnostic")
        raise ValueError("child bootstrap/tracing/audit failure: " + "; ".join(reasons))
    seal(control, nonce=nonce)
    intents = defaultdict(list)
    paths = list((Path(control) / "audit").iterdir())
    if len(paths) > 4096:
        raise ValueError("audit intent bound exceeded")
    for path in paths:
        if path.is_symlink() or path.suffix != ".intent" or path.stat().st_size > 512:
            raise ValueError("unknown or failed child audit record")
        with path.open("rb") as handle:
            raw = handle.read(513)
        try:
            version, run, token, owner, argv, created = raw.decode("ascii").split()
            owner, created = int(owner), int(created)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("malformed native audit record") from exc
        if version != VERSION or run != nonce or token not in contexts or owner <= 0:
            raise ValueError("unknown native audit context/version/owner")
        if (
            not re.fullmatch(f"{owner}-[0-9a-f]{{32}}.intent", path.name)
            or not re.fullmatch("[0-9a-f]{64}", argv)
            or not 0 < created <= time.monotonic_ns()
        ):
            raise ValueError("invalid audit argv/filename/freshness")
        intents[(token, owner, argv)].append(created)
    births = defaultdict(list)
    identities = set()
    for producer in producers:
        token, pid, owner, argv, born = producer
        if pid in identities or live(pid):
            raise ValueError("duplicate or still-running native producer")
        identities.add(pid)
        births[(token, owner, argv)].append(born)
    if intents.keys() != births.keys():
        raise ValueError("missing or unaudited native producer")
    for identity, expected in intents.items():
        actual = sorted(births[identity])
        expected = sorted(expected)
        if len(actual) != len(expected) or any(b < a for a, b in zip(expected, actual)):
            raise ValueError("native producer completeness/freshness mismatch")
    return len(producers)
