"""Bounded adoption of derived cache state, without changing its Vault authority."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from . import config
from .cache import _path_exists
from .cache_dpapi import CacheProtectionError
from .identity_context import identity_cache_digest
from .ssh_key_files import (
    WindowsSecurity, _ordinary, _publication_lock, _read, _require, _sync, _write,
)

DIRECTORY = "preserved-cache"
ADOPTION_SECONDS = 10.0
MAX_CACHE_BYTES = 16 * 1024 * 1024
FILES = ("credential-cache.enc", "credential-cache.key")


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate recovery field")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _windows():
    return WindowsSecurity() if os.name == "nt" else None


def source_key(base: Path) -> str:
    context = config.resolve_context(strict=True)
    if not context.kpdb or not Path(context.kpdb).is_absolute():
        raise CacheProtectionError("cache recovery requires an absolute selected Vault database")
    identity = identity_cache_digest(
        context.kpdb, str(config.home_dir()), config.installation_id() or "",
    )
    value = json.dumps([
        identity, os.path.normcase(str(base.absolute())), context.group or "",
        context.vault_name or "", context.port,
    ], separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


def _private_directory(path: Path, windows) -> None:
    if windows:
        windows.create(path, directory=True)
    else:
        path.mkdir(mode=0o700, exist_ok=True)
    _require(path, windows, directory=True)


def _current_directory(path: Path, windows) -> None:
    _require(path, windows, directory=True)
    if _path_exists(path / DIRECTORY):
        raise CacheProtectionError("nested protected cache recovery selection")
    for name in (*FILES, "credential-cache.lock", "credential-replacement.lock"):
        artifact = path / name
        if _path_exists(artifact):
            _ordinary(artifact, directory=False)


def _paths(base: Path, source: str) -> tuple[Path, Path, Path]:
    parent = base / DIRECTORY
    compartment = parent / source
    return parent, compartment, compartment / "recovery.json"


def _validate_record(record, source: str) -> dict:
    if (
        not isinstance(record, dict)
        or set(record) != {"version", "source", "created_at", "original"}
        or type(record["version"]) is not int or record["version"] != 1
        or record["source"] != source
        or not isinstance(record["created_at"], str)
        or not isinstance(record["original"], dict)
    ):
        raise CacheProtectionError("invalid protected cache recovery selection")
    original = record["original"]
    if original.get("state") == "copied":
        if (
            set(original) != {"state", "snapshot", "files"}
            or not isinstance(original["snapshot"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", original["snapshot"])
            or not isinstance(original["files"], dict)
            or set(original["files"]) != set(FILES)
            or any(
                value is not None
                and (not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value))
                for value in original["files"].values()
            )
        ):
            raise CacheProtectionError("invalid protected cache preservation record")
    elif (
        original.get("state") != "preserved-in-place"
        or set(original) != {"state", "reason"}
        or original["reason"] not in {"legacy-read-timeout", "legacy-lock-busy", "legacy-io"}
    ):
        raise CacheProtectionError("invalid protected cache preservation record")
    return record


def selected_cache(base: Path) -> tuple[Path, dict | None]:
    parent = base / DIRECTORY
    if not _path_exists(parent):
        return base, None
    windows = _windows()
    _ordinary(base, directory=True)
    _require(parent, windows, directory=True)
    active = parent / "active.json"
    if not _path_exists(active):
        return base, None
    if _read(active, windows) != b'{"version":1}':
        raise CacheProtectionError("invalid protected cache recovery selection")
    source = source_key(base)
    _, compartment, marker = _paths(base, source)
    if not _path_exists(compartment):
        return base, None
    _require(compartment, windows, directory=True)
    if not _path_exists(marker):
        return base, None
    try:
        record = _validate_record(_json(_read(marker, windows)), source)
    except (ValueError, UnicodeError) as exc:
        raise CacheProtectionError("invalid protected cache recovery selection") from exc
    current = compartment / "current"
    _current_directory(current, windows)
    return current, record


def require_source(base: Path, record: dict | None) -> None:
    if record is None or record["source"] != source_key(base):
        raise CacheProtectionError("selected Vault changed during cache recovery")


def _bytes(path: Path, limit: int) -> tuple[bytes, tuple]:
    before = _ordinary(path, directory=False)
    if before.st_size > limit:
        raise OSError("legacy protected cache exceeds the adoption byte limit")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("legacy protected cache changed while opening")
        value = stream.read(limit + 1)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    stamp = tuple(getattr(before, field) for field in fields)
    if len(value) > limit or tuple(getattr(path.lstat(), field) for field in fields) != stamp:
        raise OSError("legacy protected cache changed while reading")
    return value, stamp


def _copy_originals(base: Path, compartment: Path, snapshot: str, windows) -> dict:
    values = {}
    stamps = {}
    for name in FILES:
        path = base / name
        if _path_exists(path):
            values[name], stamps[name] = _bytes(
                path, MAX_CACHE_BYTES if name.endswith(".enc") else 65536,
            )
        else:
            values[name], stamps[name] = None, None
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    for name, stamp in stamps.items():
        path = base / name
        actual = tuple(getattr(path.lstat(), field) for field in fields) if _path_exists(path) else None
        if actual != stamp:
            raise OSError("legacy protected cache pair changed while reading")
    originals = compartment / snapshot
    _private_directory(originals, windows)
    for name, value in values.items():
        if value is not None:
            _write(originals, originals / name, value, windows)
    _sync(compartment)
    return {
        "state": "copied", "snapshot": snapshot,
        "files": {
            name: hashlib.sha256(value).hexdigest() if value is not None else None
            for name, value in values.items()
        },
    }


def _inspect_original(base: Path, source: str | None, snapshot: str | None) -> dict:
    from .cache import PersistentCache

    if source is not None and source_key(base) != source:
        raise CacheProtectionError("selected Vault changed during cache recovery")
    _ordinary(base, directory=True)
    windows = _windows()
    if source is not None:
        _, compartment, _ = _paths(base, source)
        _require(compartment, windows, directory=True)
    cache = PersistentCache(base)
    try:
        with cache.replacement_lock(), cache._store_lock():
            try:
                # Bound original reads before handing bytes to an optional decoder.
                for name in FILES:
                    path = base / name
                    if _path_exists(path):
                        _bytes(path, MAX_CACHE_BYTES if name.endswith(".enc") else 65536)
                cache._read_store(readonly=True, strict=True)
                return {"state": "compatible"}
            except (OSError, RuntimeError, ValueError, ImportError):
                if source is None:
                    return {"state": "unreadable"}
                try:
                    original = _copy_originals(base, compartment, snapshot, windows)
                except OSError:
                    original = {"state": "preserved-in-place", "reason": "legacy-io"}
                return {"state": "recovering", "original": original}
    except TimeoutError:
        return {
            "state": "recovering",
            "original": {"state": "preserved-in-place", "reason": "legacy-lock-busy"},
        }


def _inspect_bounded(base: Path, source: str | None, snapshot: str | None) -> dict:
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import run_caller_owned

    try:
        result = run_caller_owned(
            [sys.executable, "-m", "agent_vault.cache_recovery"],
            input_text=json.dumps({"base": str(base), "source": source, "snapshot": snapshot}),
            timeout=ADOPTION_SECONDS, max_stdout_bytes=4096, max_stderr_bytes=4096,
            budget=OwnedExecutionBudget.from_timeout(ADOPTION_SECONDS + 2.0, cleanup_reserve=2.0),
        )
    except subprocess.TimeoutExpired:
        # The process owner confirms cleanup before returning this exception.
        return {
            "state": "recovering",
            "original": {"state": "preserved-in-place", "reason": "legacy-read-timeout"},
        }
    if result.returncode:
        raise CacheProtectionError("protected cache adoption helper failed; originals retained")
    try:
        report = _json(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise CacheProtectionError("invalid protected cache adoption response") from exc
    if report == {"state": "compatible"}:
        return report
    if source is None and report == {"state": "unreadable"}:
        return report
    if not isinstance(report, dict) or set(report) != {"state", "original"}:
        raise CacheProtectionError("invalid protected cache adoption response")
    _validate_record({
        "version": 1, "source": source, "created_at": "", "original": report["original"],
    }, source)
    if report["state"] != "recovering":
        raise CacheProtectionError("invalid protected cache adoption response")
    return report


def prepare_cache(base: Path) -> dict:
    from .cache import PersistentCache

    base = base.absolute()
    current, record = selected_cache(base)
    if record is not None:
        return {"state": "recovering", "original": record["original"]}
    try:
        if not any(_path_exists(base / name) for name in FILES):
            return {"state": "ready"}
    except OSError:
        pass  # The bounded reader owns inaccessible legacy state, not the incoming cache.
    try:
        source = source_key(base)
    except CacheProtectionError:
        # A healthy pre-existing cache does not need new recovery configuration.
        if _inspect_bounded(base, None, None) == {"state": "compatible"}:
            return {"state": "ready"}
        raise
    windows = _windows()
    _ordinary(base, directory=True)
    parent, compartment, marker = _paths(base, source)
    _private_directory(parent, windows)
    _private_directory(compartment, windows)
    with _publication_lock(compartment, windows):
        _, record = selected_cache(base)
        if record is not None:
            return {"state": "recovering", "original": record["original"]}
        report = _inspect_bounded(base, source, uuid.uuid4().hex)
        if source != source_key(base):
            raise CacheProtectionError("selected Vault changed during cache recovery")
        if report["state"] == "compatible":
            return {"state": "ready"}
        current = compartment / "current"
        _private_directory(current, windows)
        _current_directory(current, windows)
        cache = PersistentCache(current)
        if _path_exists(cache._cache_file):
            cache._read_store(readonly=True, strict=True)
        elif not cache._write_store({"v": 1, "entries": {}}):
            raise CacheProtectionError("fresh protected cache could not be initialized")
        record = {
            "version": 1, "source": source,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "original": report["original"],
        }
        require_source(base, record)
        _write(parent, parent / "active.json", b'{"version":1}', windows)
        _write(compartment, marker, json.dumps(record, sort_keys=True).encode(), windows)
        return {"state": "recovering", "original": record["original"]}


def status_command(args) -> int:
    from .cache import get_cache

    status = get_cache().status()
    if getattr(args, "json", False):
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    state = "enabled" if status["enabled"] else "disabled"
    if not status["available"]:
        state += " (cache encryption backend unavailable)"
    print(f"cache: {state}")
    print(f"dir: {status['cache_dir']}")
    print(f"entries: {status['entry_count']} ({status['field_count']} field(s))")
    if not status["readable"]:
        print("protected data: unreadable (originals retained; use cache-populate for live recovery)")
    if status["recovery"]:
        print(f"originals: {status['recovery']['original']['state']}")
        print("live recovery: required; current entries do not prove complete recovery")
    if status["oldest"]:
        print(f"oldest: {status['oldest']}")
    if status["newest"]:
        print(f"newest: {status['newest']}")
    return 0


def _main() -> int:
    try:
        request = _json(sys.stdin.buffer.read(16385))
        if (
            not isinstance(request, dict) or set(request) != {"base", "source", "snapshot"}
            or not isinstance(request["base"], str) or not Path(request["base"]).is_absolute()
            or not (
                request["source"] is None and request["snapshot"] is None
                or isinstance(request["source"], str)
                and re.fullmatch(r"[a-f0-9]{64}", request["source"])
                and isinstance(request["snapshot"], str)
                and re.fullmatch(r"[a-f0-9]{32}", request["snapshot"])
            )
        ):
            return 1
        result = _inspect_original(Path(request["base"]), request["source"], request["snapshot"])
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, ImportError):
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
