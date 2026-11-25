"""Runtime-owned cache maintenance, independent of successful selection."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable, ContextManager, Iterator


def _macos_archive_acl(path: Path) -> None:
    import ctypes
    import errno

    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    library.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    library.acl_get_file.restype = ctypes.c_void_p
    library.acl_get_entry.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
    ]
    library.acl_get_entry.restype = ctypes.c_int
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_free.restype = ctypes.c_int
    acl = library.acl_get_file(os.fsencode(path), 0x100)
    if not acl:
        raise PermissionError("cache maintenance archive ACL is unavailable")
    try:
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        if library.acl_get_entry(acl, 0, ctypes.byref(entry)) != -1 or ctypes.get_errno() != errno.EINVAL:
            raise PermissionError("cache maintenance archive ACL is unsupported")
    finally:
        library.acl_free(acl)


def _protected_archive_path(archive: Path) -> Path:
    if sys.platform == "win32":
        return archive
    # Import reopens the filename. A protected leaf alone does not prevent
    # another principal replacing it through a writable parent.
    archive = archive.resolve(strict=True)
    descriptor = os.open(archive.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent = Path(archive.anchor)
        for name in archive.parts[1:-1]:
            info = os.fstat(descriptor)
            if info.st_uid not in (0, os.geteuid()) or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise PermissionError("cache maintenance archive ancestry is not protected")
            if sys.platform == "darwin":
                _macos_archive_acl(parent)
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
            parent /= name
        info = os.fstat(descriptor)
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise PermissionError("cache maintenance archive ancestry is not protected")
        if sys.platform == "darwin":
            _macos_archive_acl(parent)
    finally:
        os.close(descriptor)
    return archive


def _collector(*, windows_owned_file: Callable[[int], bool] | None = None) -> Any:
    archive = Path(__file__).with_name("daemon_instance_runtime.pyz")
    try:
        info = archive.lstat()
    except FileNotFoundError:
        from agent_procutil import python_cache_collect
    else:
        archive = _protected_archive_path(archive)
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("cache maintenance source archive is redirected")
        descriptor = os.open(
            archive, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev, opened.st_ino,
            ) != (info.st_dev, info.st_ino):
                raise ValueError("cache maintenance source archive changed")
            if sys.platform == "win32":
                if windows_owned_file is None or not windows_owned_file(descriptor):
                    raise PermissionError("cache maintenance source archive is not protected")
            elif opened.st_uid not in (0, os.geteuid()) or opened.st_mode & 0o022:
                raise PermissionError("cache maintenance source archive is not protected")
            elif sys.platform == "darwin":
                _macos_archive_acl(archive)
            # Existing pre-install lifecycle distribution, not a retired runtime.
            sys.path.insert(0, str(archive))
            try:
                from agent_procutil import python_cache_collect
            finally:
                sys.path.pop(0)
        finally:
            os.close(descriptor)
    from agent_procutil import python_cache_prefixes, python_cache_legacy

    return SimpleNamespace(
        collect=python_cache_collect.collect, collect_legacy=python_cache_legacy.collect,
        collect_prefixes=python_cache_prefixes.collect_prefixes,
    )


def maintain(
    root: Path, *, cleanup_lease: Any, protected: Any, version_dir: Any,
    windows_owned_file: Callable[[int], bool] | None = None, preserve_association: bool = False,
) -> None:
    @contextmanager
    def retirement(slot: str, *, check: Callable[[], None]) -> Iterator[str]:
        check()
        with cleanup_lease(root, slot) as admitted:
            check()
            if not admitted:
                yield "runtime-busy"
            elif slot.lower() in protected(check=check):
                yield "runtime-protected"
            else:
                try:
                    version_dir(root, slot).lstat()
                except FileNotFoundError:
                    yield ""
                else:
                    yield "runtime-present"

    for layout, operation in (("python-cache", "collect"), ("python-cache-legacy", "collect_legacy")):
        try:
            provider = _collector(windows_owned_file=windows_owned_file)
            extra = {"preserve_association": True} if operation == "collect_legacy" and preserve_association else {}
            result = getattr(provider, operation)(root, retirement=retirement, **extra)
            if result.namespaces or result.reasons or result.pending:
                print("gc: " + layout + " " + json.dumps(asdict(result), sort_keys=True), file=sys.stderr)
        except Exception as error:
            # Maintenance cannot veto selection; filesystem errors can contain private paths.
            print(f"gc: {layout} maintenance unavailable ({type(error).__name__})", file=sys.stderr)


def collect_prefixes(
    root: Path, *, maintenance: Callable[[], ContextManager[Any]] | None = None, **options: Any,
) -> dict[str, Any]:
    with maintenance() if maintenance is not None and options.get("apply") else nullcontext():
        result = _collect_prefixes(root, **options)
        if options.get("apply"):
            from agent_procutil import python_cache_status

            python_cache_status.write(root, {**result, "last_worker_state": "finished"})
        return result


def _measurement(
    rows: dict[str, dict[str, Any]], observed: dict[Any, int], last: Any, removed: int,
) -> dict[str, Any]:
    known = last is not None and getattr(last, "inventory_complete", False)
    remaining = set(last.namespace_names) - {
        item["name"] for item in last.candidates if item["status"] == "removed"
    } if known else set(rows)
    active = {name: item for name, item in rows.items()
              if name in remaining and item["status"] != "removed"}
    unmeasured = (
        len(remaining - set(active)) + sum(
            item["bytes"] is None or item["status"] == "partial" for item in active.values()
        ) if known else None
    )
    by_reason: dict[str, dict[str, int]] = {}
    for item in active.values():
        if item.get("reason"):
            group = by_reason.setdefault(item["reason"], {"prefixes": 0, "bytes": 0, "unmeasured": 0})
            group["prefixes"] += 1
            group["bytes"] += item["bytes"] or 0
            group["unmeasured"] += item["bytes"] is None
    return {
        "inventory_complete": bool(known),
        "scan_complete": bool(known and unmeasured == 0),
        "bytes_by_reason": by_reason,
        "summary": {
            "measured_bytes": sum(observed.values()), "removed_bytes": removed,
            "eligible_bytes": sum(item["bytes"] or 0 for item in active.values()
                                  if item["status"] == "eligible"),
            "held_bytes": sum(item["bytes"] or 0 for item in active.values()
                              if item["status"] == "held"),
            "unmeasured_prefixes": unmeasured,
        },
    }


def recorded_consumers(root: Path, *, filename: str, check: Callable[[], None]) -> set[int]:
    from agent_procutil import python_cache_files, python_cache_permissions, python_cache_processes

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("duplicate runtime consumer field")
            values[key] = value
        return values

    try:
        with python_cache_files.installation(root, private=False, check_budget=check) as directory:
            with python_cache_files.child(directory, filename, read=True) as entry:
                python_cache_permissions.check_file(entry.path, private=False)
                if entry.info.st_nlink != 1:
                    raise ValueError("linked runtime consumer record")
                data = json.loads(entry.read(65536), object_pairs_hook=unique)
                current = entry.verify()
                if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
                    entry.info.st_size, entry.info.st_mtime_ns, entry.info.st_ctime_ns,
                ):
                    raise ValueError("runtime consumer record changed")
        rows = data if isinstance(data, list) else [data]
        if len(rows) > 32768 or any(
            not isinstance(row, dict) or type(row.get("pid")) is not int
            or not 0 < row["pid"] <= 0xFFFFFFFF or row["pid"] == 4 for row in rows
        ):
            raise ValueError("runtime consumer record is invalid")
        return {row["pid"] for row in rows}
    except FileNotFoundError:
        return set()
    except (OSError, ValueError, TypeError, RecursionError):
        raise python_cache_processes.Unavailable("runtime-consumers-unavailable") from None


def _collect_prefixes(
    root: Path, *, custody: Any, protected: Any, version_dir: Any,
    busy_errors: tuple[type[Exception], ...],
    windows_owned_file: Callable[[int], bool] | None = None,
    apply: bool = False, drain: bool = False, seconds: float | None = None,
    byte_limit: int | None = None, min_age_seconds: float = 7 * 86400,
    entries: int = 262144, namespaces: int = 512, details: bool = False, after: str = "",
    on_pass: Callable[[dict[str, Any]], None] | None = None,
    running_file: str | None = None,
) -> dict[str, Any]:
    seconds = (1800 if drain else 30) if seconds is None else seconds
    byte_limit = (512 * 1024**3 if drain else 4 * 1024**3) if byte_limit is None else byte_limit
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError("cache deadline must be between zero and 3600 seconds")
    for value, maximum in ((byte_limit, 512 * 1024**3), (entries, 1048576), (namespaces, 4096)):
        if type(value) is not int or not 0 < value <= maximum:
            raise ValueError("cache work limit is outside the supported bound")

    @contextmanager
    def retirement(slots: list[str], *, check: Callable[[], None]) -> Iterator[str]:
        check()
        with ExitStack() as held:
            try:
                held.enter_context(custody(root, slots))
            except busy_errors:
                yield "runtime-busy"
                return
            check()
            retained = protected(check=check)
            if any(slot.lower() in retained for slot in slots):
                yield "runtime-protected"
                return
            for slot in slots:
                check()
                try:
                    version_dir(root, slot).lstat()
                except FileNotFoundError:
                    continue
                yield "runtime-present"
                return
            yield ""

    provider = _collector(windows_owned_file=windows_owned_file)
    if not callable(getattr(provider, "collect_prefixes", None)):
        raise RuntimeError("The selected cache provider does not support whole-prefix collection")
    started = time.monotonic()
    deadline = started + seconds
    removed, files_removed, completed, passes, idle = 0, 0, 0, 0, 0
    rows: dict[str, dict[str, Any]] = {}
    observed: dict[Any, int] = {}
    reasons: dict[str, int] = {}
    errors: dict[str, int] = {}
    cursor, last = after, None
    while time.monotonic() < deadline and removed < byte_limit:
        last = provider.collect_prefixes(
            root, retirement=retirement, apply=apply, after=cursor,
            required_pids=(
                lambda *, check: recorded_consumers(root, filename=running_file or "", check=check)
            ) if running_file is not None else (),
            min_age_seconds=min_age_seconds, budget={
                "seconds": min(30, max(0.001, deadline - time.monotonic())),
                "bytes": min(4 * 1024**3, byte_limit - removed),
                "entries": entries, "namespaces": namespaces,
            },
        )
        passes += 1
        removed += last.bytes
        files_removed += last.files
        completed += last.completed
        for item in last.candidates:
            key = (item["name"], tuple(item.get("generation", ())))
            rows[item["name"]] = item
            if item["bytes"] is not None:
                observed[key] = max(observed.get(key, 0), item["bytes"])
        for source, target in ((last.reasons, reasons), (last.errors, errors)):
            for name, count in source.items():
                target[name] = target.get(name, 0) + count
        cursor = last.next_cursor
        idle = 0 if last.bytes or last.completed else idle + last.namespaces
        if on_pass is not None:
            on_pass({
                "schema": "agent-procutil.python-cache-reclamation", "version": 1,
                **_measurement(rows, observed, last, removed),
                "pending": last.pending, "next_cursor": cursor,
                "liveness": last.liveness, "last_worker_state": "in-progress-at-observation",
                "reasons": reasons,
            })
        if not drain or not last.pending or last.namespaces == 0 or idle >= last.namespace_total:
            break
    report: dict[str, Any] = {
        "schema": "agent-procutil.python-cache-reclamation", "version": 1,
        "apply": bool(apply), "root": str(root), "passes": passes, "next_cursor": cursor,
        "liveness": last.liveness if last is not None else "unavailable",
        "pending": last.pending if last is not None else True,
        "elapsed_seconds": time.monotonic() - started, "reasons": reasons, "errors": errors,
        **_measurement(rows, observed, last, removed),
        "limits": {"seconds": seconds, "bytes": byte_limit, "entries_per_pass": entries,
                   "namespaces_per_pass": namespaces},
    }
    report["summary"].update(removed_files=files_removed, completed_prefixes=completed)
    if details:
        report["candidates"] = list(rows.values())
    return report


def add_cli(sub: Any, default_age: float) -> None:
    parser = sub.add_parser("gc", help="remove unreferenced version dirs")
    parser.add_argument("--keep", action="append", default=[],
                        help="version(s) to preserve (repeatable)")
    parser.add_argument("--protect-pids", action="store_true",
                        help="also protect versions a live recorded pid may run from")
    parser.add_argument("--min-age-days", type=float, default=default_age,
                        help="optional recency backstop: minimum slot age (days) "
                             "before an unprotected version is eligible for removal. "
                             "The primary gate is live-process usage (--protect-pids); "
                             "this only additionally holds a just-superseded slot for a "
                             "STORED (not-running) pinned launch reference to age out "
                             "(default: %(default)s = off)")
    parser.add_argument("--toss-incomplete", action="store_true",
                        help="also remove non-current slots lacking a completion "
                             "marker (failed/partial builds; dotfiles #935)")
    parser = sub.add_parser("python-cache", help="inspect or collect qualified legacy bytecode")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--max-bytes", type=int)
    parser.add_argument("--max-entries", type=int, default=262144)
    parser.add_argument("--max-namespaces", type=int, default=512)
    parser.add_argument("--min-age-hours", type=float, default=168)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--automatic", action="store_true", help=argparse.SUPPRESS)


def run_cli(args: Any, runtime: Any) -> None:
    if args.cmd == "gc":
        removed = runtime.gc(
            args.root, keep=args.keep, protect_pids=args.protect_pids,
            link_name=args.link_name, min_age_days=args.min_age_days,
        )
        if args.toss_incomplete:
            removed = list(removed) + runtime.toss_incomplete(args.root, args.link_name)
        runtime._emit({"removed": removed} if args.json else removed, args.json)
    else:
        options: dict[str, Any] = dict(
            link_name=args.link_name, apply=args.apply, drain=args.drain,
            seconds=args.max_seconds, byte_limit=args.max_bytes, entries=args.max_entries,
            namespaces=args.max_namespaces, min_age_seconds=args.min_age_hours * 3600,
            details=args.details,
        )
        if args.automatic:
            from agent_procutil import python_cache_status

            try:
                previous = python_cache_status.read(args.root)
            except (OSError, ValueError, TypeError):
                previous = None
            if previous is not None:
                options["after"] = previous.get("next_cursor", "")
            options["on_pass"] = lambda value: python_cache_status.write(args.root, value)
        result = runtime.collect_python_cache(args.root, **options)
        runtime._emit(result, args.json)


def schedule(root: Path, runtime: Any) -> None:
    """Start only a retained, selected, finite worker; never a source installer."""
    if os.name != "nt":
        return
    from agent_procutil import daemon_instances, python_cache_status, python_cache_store

    version = runtime.current_version(root)
    if version is None or not runtime.is_complete(root, version):
        return
    slot = runtime.version_dir(root, version)
    bootstrap = slot / ".runtime-bootstrap"
    script = bootstrap / "versioned_runtime.py"
    launcher = bootstrap / "runtime_launcher.py"
    python = runtime.slot_python(root, version)
    if python is None or not script.is_file() or not launcher.is_file():
        return
    sdk = python.parent.parent / "Lib" / "site-packages"
    if not (sdk / "agent_procutil" / "_daemon_instance_child.py").is_file():
        raise RuntimeError("The selected runtime has no retained finite-worker SDK")
    # Older retained owners have no whole-prefix API. Do not select source or
    # incoming installer code as a substitute for their installed capability.
    with script.open("rb") as stream:
        content = stream.read(512 * 1024 + 1)
    if len(content) > 512 * 1024 or b"PYTHON_CACHE_RECLAMATION_CAPABILITY" not in content:
        return
    argv = [str(python), "-I", "-S", "-B", str(launcher), str(script),
            "--root", str(root), "--json", "python-cache", "--drain"]
    status = python_cache_status.describe(root, argv=argv)
    print("gc: legacy-bytecode " + json.dumps(status, sort_keys=True), file=sys.stderr)
    if status.get("observation_age_seconds", float("inf")) < 3600:
        return
    state = python_cache_store.installation_root(root, create=True)
    daemon_instances.spawn_finite_worker(
        [*argv, "--apply", "--automatic"],
        cwd=root, log_path=state / "legacy-prefix-maintenance.log",
        python_bootstrap_root=sdk,
        instance=daemon_instances.DaemonInstanceSpec(
            service="legacy-bytecode-maintenance", runtime_root=root, source=script,
        ),
    )


def gc(
    root: Path, *, candidates: Any, cleanup_lease: Any, protected: Any, version_dir: Any,
    is_link: Any, slot_age_days: Any, remove_slot: Any, min_age_days: float,
    windows_owned_file: Callable[[int], bool] | None = None,
    automatic: Callable[[], None] | None = None,
) -> list[str]:
    removed: list[str] = []
    for version in candidates(root):
        with cleanup_lease(root, version) as admitted:
            if not admitted or version.lower() in protected():
                continue
            directory = version_dir(root, version)
            if min_age_days > 0 and not is_link(directory) and slot_age_days(root, version) < min_age_days:
                continue
            if remove_slot(directory, label="gc"):
                removed.append(version)
    maintain(
        root, cleanup_lease=cleanup_lease, protected=protected, version_dir=version_dir,
        windows_owned_file=windows_owned_file,
        preserve_association=automatic is not None and sys.platform == "win32",
    )
    if automatic is not None:
        try:
            automatic()
        except Exception as error:
            print(f"gc: legacy-bytecode catch-up unavailable ({type(error).__name__})", file=sys.stderr)
    return removed
