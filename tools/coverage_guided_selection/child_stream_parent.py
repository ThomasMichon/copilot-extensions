"""Parent-only manifest preparation, strict child evidence parsing and merging."""

import os
import sys
import time
import threading

if "_cgs_child_stream" in sys.modules:
    _runtime = sys.modules["_cgs_child_stream"]
else:
    from tools.coverage_guided_selection import child_stream as _runtime

ENV = _runtime.ENV
VERSION = _runtime.VERSION
MAX_RECORDS = _runtime.MAX_RECORDS
MAX_MANIFEST = _runtime.MAX_MANIFEST
_auditor = _runtime._auditor
_control = None
_contexts = {}
_inventory = {}
_nonce = None
_helpers = {}
_guard_active = False
_guard_arming = False
_tracing = False
_coverage_owner = None
_parent_trace = _parent_thread_trace = None


def source_inventory(mapping):
    """Parent-only helpers import heavier libraries lazily, never at child startup."""
    import hashlib
    from pathlib import Path

    if len(mapping) >= 65535 or len(set(mapping.values())) != len(mapping):
        raise ValueError("ambiguous or oversized child source inventory")
    result = {}
    for identifier, (installed, canonical) in enumerate(sorted(mapping.items()), 1):
        path = Path(installed)
        content = path.read_bytes()
        result[identifier] = {
            "installed": str(path), "canonical": canonical,
            "sha256": hashlib.sha256(content).hexdigest(),
            "lines": len(content.splitlines()),
        }
    return result


def manifest_text(*, nonce, context, directory, inventory):
    from pathlib import Path

    root = os.path.commonpath([str(Path(row["installed"]).parent) for row in inventory.values()])
    root = root + os.sep
    header = "\t".join([
        VERSION, nonce, context, str(directory).encode().hex(), root.encode().hex(),
    ])
    rows = [header] + [
        f"{identifier}\t{row['lines']}\t{row['installed'].encode().hex()}\t{row['sha256']}"
        for identifier, row in sorted(inventory.items(), key=lambda item: int(item[0]))
    ]
    return "\n".join(rows) + "\n"


def write_manifest(path, **options):
    content = manifest_text(**options).encode("utf-8")
    if len(content) > MAX_MANIFEST:
        raise ValueError("child manifest exceeds bound")
    path.write_bytes(content)


def read_streams(directory, *, nonce, contexts, inventory, audit_control, generated_inventory=None):
    """Parse source bounds; generated rows require caller-verified native provenance."""
    import hashlib
    import re
    from pathlib import Path

    _auditor().seal(audit_control, nonce=nonce)
    for row in inventory.values():
        if hashlib.sha256(Path(row["installed"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("installed source changed after tracing")
    manifests = Path(audit_control) / "manifests"
    if not manifests.is_dir() or {p.name for p in manifests.iterdir()} != {
        token + ".manifest" for token in contexts
    }:
        raise ValueError("unknown or missing test context manifest")
    for token in contexts:
        path = manifests / (token + ".manifest")
        if path.is_symlink():
            raise ValueError("linked test context manifest")
        with path.open("rb") as handle:
            content = handle.read(MAX_MANIFEST + 1)
        expected = manifest_text(
            nonce=nonce, context=token, directory=directory, inventory=inventory,
        ).encode("utf-8")
        if content != expected:
            raise ValueError("test context/source manifest changed")
    effective_inventory = dict(inventory)
    for identifier, row in (generated_inventory or {}).items():
        path = Path(row["installed"])
        if (
            type(identifier) is not int or identifier in effective_inventory or not 0 < identifier < 65535
            or path.is_symlink()
            or path.parent.resolve() != (Path(audit_control) / "generated").resolve()
        ):
            raise ValueError("generated source identity collision or unowned source")
        with path.open("rb") as source:
            content = source.read(MAX_MANIFEST + 1)
        actual = hashlib.sha256(content).hexdigest()
        if (
            len(content) > MAX_MANIFEST or actual != row["sha256"]
            or len(content.splitlines()) != row["lines"]
            or row["canonical"] != "generated-source:sha256:" + actual
        ):
            raise ValueError("generated source bytes/line identity changed")
        effective_inventory[identifier] = row
    coverage, preludes, producers, zero_hits = {}, [], [], 0
    files = sorted(directory.iterdir())
    if len(files) > 4096:
        raise ValueError("unexpected child stream count")
    for path in files:
        maximum = 512 + 6 * (MAX_RECORDS + 1)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            raise ValueError("unsafe or oversized child stream")
        identity = re.fullmatch(r"([1-9][0-9]{0,9})-[0-9a-f]{32}\.hits", path.name)
        if not identity or int(identity[1]) >= 2**32:
            raise ValueError("invalid child stream producer filename")
        if _auditor().live(int(identity[1])):
            raise ValueError("still-running native producer before child stream snapshot")
        with path.open("rb") as handle:
            data = handle.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("child stream grew beyond bound")
        header, separator, payload = data.partition(b"\n")
        try:
            status, version, run, context, pid, owner, arguments, born = header.decode("ascii").split(" ")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("malformed child stream header") from exc
        if (
            not separator or len(header) > 512 or status != "R" or version != VERSION or run != nonce
            or context not in contexts or not pid.isdigit() or int(pid) <= 0
            or not owner.isdigit() or not born.isdigit()
            or not 0 < int(pid) < 2**32 or not 0 < int(owner) < 2**32
            or not 0 < int(born) <= time.monotonic_ns()
            or not re.fullmatch("[0-9a-f]{64}", arguments)
            or not re.fullmatch(re.escape(pid) + r"-[0-9a-f]{32}\.hits", path.name)
            or len(payload) < 6 or len(payload) % 6 or len(payload) // 6 > MAX_RECORDS + 1
            or payload[:2] != b"\0\0"
        ):
            raise ValueError("invalid child stream identity/version/record framing")
        preludes.append(int.from_bytes(payload[2:6], "big") / 1_000_000)
        producers.append((context, int(pid), int(owner), arguments, int(born)))
        zero_hits += len(payload) == 6
        seen = set()
        for offset in range(6, len(payload), 6):
            identifier = int.from_bytes(payload[offset:offset + 2], "big")
            line = int.from_bytes(payload[offset + 2:offset + 6], "big")
            if identifier not in effective_inventory or not 0 < line <= effective_inventory[identifier]["lines"]:
                raise ValueError("child record outside verified source bounds")
            if (identifier, line) in seen:
                raise ValueError("duplicate sparse child record")
            seen.add((identifier, line))
            file = effective_inventory[identifier]["canonical"]
            coverage.setdefault(file, {}).setdefault(str(line), set()).add(contexts[context])
    count = _auditor().verify(audit_control, nonce=nonce, contexts=contexts, producers=producers)
    return {
        "coverage": {
            file: {line: sorted(nodes) for line, nodes in lines.items()}
            for file, lines in coverage.items()
        },
        "prelude_seconds": preludes, "child_streams": len(files),
        "audited_producers": count, "zero_hit_producers": zero_hits, "audit_complete": True,
        "control_sha256": control_digest(audit_control),
    }


def control_digest(control):
    import hashlib
    from pathlib import Path

    root = Path(control)
    digest = hashlib.sha256()
    maximum = 512 + 6 * (MAX_RECORDS + 1)
    def include(path):
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsafe final child evidence file")
        with path.open("rb") as source:
            content = source.read(maximum + 1)
        if len(content) > maximum:
            raise ValueError("oversized final child evidence file")
        name = path.relative_to(root).as_posix().encode("utf-8", "surrogatepass")
        digest.update(len(name).to_bytes(4, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    for name in ("run", "status", "sealed"):
        include(root / name)
    for name in ("audit", "hits", "manifests", "generated"):
        directory = root / name
        if directory.is_symlink():
            raise ValueError("linked final child evidence directory")
        digest.update(name.encode() + (b"\1" if directory.exists() else b"\0"))
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError("invalid final child evidence directory")
        paths = sorted(directory.iterdir())
        if len(paths) > (4096 if name in {"audit", "hits"} else MAX_RECORDS):
            raise ValueError("final child evidence file-count bound exceeded")
        for path in paths:
            include(path)
    return digest.hexdigest()


def verify_final(result, *, control, nonce, inventory, helpers):
    """Driver output alone cannot attest to launches during its shutdown."""
    import hashlib
    from pathlib import Path

    _auditor().check_seal(control, nonce=nonce)
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected
           for path, expected in helpers.items()):
        raise ValueError("child instrumentation source changed after driver exit")
    contexts = {hashlib.sha256(node.encode()).hexdigest(): node for node in result["durations"]}
    if len(contexts) != len(result["durations"]):
        raise ValueError("final test-context digest collision")
    final = read_streams(
        Path(control) / "hits", nonce=nonce, contexts=contexts, inventory=inventory,
        audit_control=control,
    )
    child_coverage = final.pop("coverage")
    if final != result["children"]:
        raise ValueError("child evidence changed after the sealed snapshot")
    for file, lines in child_coverage.items():
        for line, nodes in lines.items():
            if not set(nodes) <= set(result["coverage"].get(file, {}).get(line, ())):
                raise ValueError("final child coverage was omitted from the merged result")


def merge_coverage(parent, child):
    """Union into the existing file -> line -> test-node schema, never replace."""
    for file, lines in child.items():
        for line, nodes in lines.items():
            target = parent.setdefault(file, {})
            target[line] = sorted(set(target.get(line, ())) | set(nodes))
    return parent


def configure_parent(control, python, nonce, inventory, helpers):
    global _control, _nonce, _inventory, _helpers, _guard_active, _guard_arming
    _control, _nonce = control, nonce
    _helpers = helpers
    _inventory = {int(identifier): row for identifier, row in inventory.items()}
    for name in ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG"):
        os.environ.pop(name, None)
    _auditor().install(control, python, nonce)
    if sys.getprofile() is not None or threading.getprofile() is not None:
        raise ValueError("parent executable-source guard requires an unclaimed profile hook")
    _runtime._prepare_sources({
        row["installed"]: (identifier, row["lines"], row["sha256"])
        for identifier, row in _inventory.items()
    })
    root = os.path.commonpath([os.path.dirname(row["installed"]) for row in _inventory.values()])
    _runtime._root = os.path.normcase(root + os.sep)
    _runtime._package = os.path.basename(root)
    _runtime._install_generated_guard()
    _runtime._install_thread_guard()
    sys.addaudithook(_profile_event)
    _guard_active = _guard_arming = True
    original = threading.setprofile
    def set_thread_profile(function):
        if _guard_active and not _guard_arming and function is not _profile:
            _auditor().error("parent thread executable-source guard was replaced")
        return original(function)
    threading.setprofile = set_thread_profile
    try:
        threading.setprofile(_profile)
        sys.setprofile(_profile)
    finally:
        _guard_arming = False


def _profile(frame, event, arg):
    if event == "call":
        code = frame.f_code
        if (_tracing and code is _runtime._thread_settrace_code
                and frame.f_globals is _runtime._thread_settrace_globals
                and frame.f_locals.get(_runtime._thread_settrace_code.co_varnames[0]) is not _parent_thread_trace):
            _auditor().error("parent thread line tracer was replaced")
        if (_guard_active and not _guard_arming and code is _runtime._thread_setprofile_code
                and frame.f_globals is _runtime._thread_setprofile_globals
                and frame.f_locals.get(_runtime._thread_setprofile_code.co_varnames[0]) is not _profile):
            _auditor().error("parent thread executable-source guard was replaced")
        _runtime._frame_source(frame)
    elif event == "c_call" and (
        arg is _runtime._start_thread_original or arg is _runtime._start_thread_alias_original
    ):
        if frame.f_code not in (_runtime._thread_start_code, _runtime._start_thread.__code__):
            _auditor().error("unsupported low-level thread coverage lifecycle")


def _profile_event(event, args):
    if event == "sys.setprofile" and _guard_active and not _guard_arming:
        caller = sys._getframe(1)
        if (caller.f_code is not threading.Thread._bootstrap_inner.__code__
                or threading.getprofile() is not _profile):
            _auditor().error("parent executable-source guard was replaced")
    if event == "sys.settrace" and _tracing:
        caller = sys._getframe(1)
        installers = [getattr(_coverage_owner, name, None)
                      for name in ("_installation_trace", "_start_tracer")]
        if (caller.f_code is threading.Thread._bootstrap_inner.__code__
                and threading.gettrace() is _parent_thread_trace):
            return
        if (caller.f_locals.get("self") is _coverage_owner and any(
                method is not None and caller.f_code is method.__func__.__code__
                for method in installers)):
            return
        _auditor().error("parent line tracer was replaced: " + caller.f_code.co_name[:64])


def begin_collection(coverage):
    global _tracing, _coverage_owner, _parent_trace, _parent_thread_trace
    _coverage_owner = coverage._collector
    _parent_trace, _parent_thread_trace = sys.gettrace(), threading.gettrace()
    original = threading.settrace
    def set_thread_trace(function):
        if _tracing and function is not _parent_thread_trace:
            _auditor().error("parent thread line tracer was replaced")
        return original(function)
    threading.settrace = set_thread_trace
    _tracing = True


def end_collection():
    global _tracing
    if sys.gettrace() is not _parent_trace or threading.gettrace() is not _parent_thread_trace:
        _auditor().error("parent line tracer was replaced")
    _auditor().seal(_control, nonce=_nonce)
    _tracing = False


def register_context(node):
    import hashlib
    from pathlib import Path
    token = hashlib.sha256(node.encode()).hexdigest()
    if token in _contexts and _contexts[token] != node:
        raise ValueError("test-context digest collision")
    _contexts[token] = node
    _auditor()._own_context = token
    path = Path(_control) / "manifests" / (token + ".manifest")
    write_manifest(
        path, nonce=_nonce, context=token, directory=Path(_control) / "hits", inventory=_inventory,
    )
    os.environ[ENV] = str(path)


def finish_parent():
    global _guard_active
    import hashlib
    from pathlib import Path
    if _guard_active:
        if sys.getprofile() is not _profile or threading.getprofile() is not _profile:
            _auditor().error("parent executable-source guard was replaced")
        _guard_active = False
        _runtime._generated_active = _runtime._thread_guard_active = False
        sys.setprofile(None)
        threading.setprofile(None)
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected
           for path, expected in _helpers.items()):
        raise ValueError("child instrumentation source changed")
    return read_streams(
        Path(_control) / "hits", nonce=_nonce, contexts=_contexts, inventory=_inventory,
        audit_control=_control,
    )
