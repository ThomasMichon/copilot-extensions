"""Finite indexed cache maintenance; runtime owners supply deletion custody."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import _daemon_instance_owner as private_owner
from . import python_cache_catalog as catalog
from . import python_cache_files as files
from . import python_cache_permissions as permissions
from . import python_cache_store as store

_KEY = re.compile(r"[0-9a-f]{64}")
_SLOT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_BYTECODE = re.compile(r".+\.pyc(?:\.[0-9]+)?")
_MAX_RECORD = 16384
_MAX_STATE = 32768


@dataclass(frozen=True)
class Budget:
    namespaces: int = 8
    entries: int = 256
    bytes: int = 32 * 1024 * 1024
    seconds: float = 1.0

    def validate(self):
        if any(type(value) is not int or value < 1 for value in (
            self.namespaces, self.entries, self.bytes,
        )) or not math.isfinite(self.seconds) or self.seconds <= 0:
            raise ValueError("cache collection budgets must be finite and positive")
        if self.entries < 2:
            raise ValueError("cache collection requires at least two entry probes")


@dataclass
class Report:
    namespaces: int = 0
    entries: int = 0
    files: int = 0
    bytes: int = 0
    completed: int = 0
    exhausted: bool = False
    pending: bool = False
    reasons: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)

    def retain(self, reason):
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def error(self, error):
        code = f"{type(error).__name__}:{getattr(error, 'winerror', None) or getattr(error, 'errno', None) or 0}"
        self.errors[code] = self.errors.get(code, 0) + 1


class _Exhausted(Exception):
    pass


class _Pass:
    def __init__(self, budget, report):
        self.budget, self.report = budget, report
        self.deadline = time.monotonic() + budget.seconds

    def check(self):
        if time.monotonic() >= self.deadline:
            raise _Exhausted

    def entry(self):
        self.check()
        if self.report.entries >= self.budget.entries:
            raise _Exhausted
        self.report.entries += 1

    def next(self, iterator):
        self.entry()
        return next(iterator, None)


def _owner(encoded, key, root):
    if not isinstance(encoded, bytes) or len(encoded) > _MAX_RECORD:
        raise ValueError("cache owner is oversized or invalid")
    owner = json.loads(encoded)
    if (
        set(owner) != {"schema", "version", "runtime_root", "runtime", "installed_identity"}
        or owner["schema"] != store.SCHEMA or type(owner["version"]) is not int
        or owner["version"] != 1 or owner["runtime_root"] != store.normalized(root)
        or not isinstance(owner["runtime"], str)
        or not isinstance(owner["installed_identity"], str)
        or not _KEY.fullmatch(key)
    ):
        raise ValueError("cache ownership is unsupported")
    target = Path(owner["runtime"])
    slot = target.name
    if (
        not _SLOT.fullmatch(slot) or slot.endswith(".") or slot.lower() == "dev"
        or store.normalized(target) != store.normalized(root / "versions" / slot)
        or owner["runtime"] != store.normalized(target)
    ):
        raise ValueError("cache target is not an immutable runtime slot")
    digest = hashlib.sha256(
        json.dumps(owner, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    if digest != key or encoded != (json.dumps(owner, sort_keys=True) + "\n").encode("utf-8"):
        raise ValueError("cache ownership differs")
    return slot


def _state(database, key):
    row = database.execute(
        "SELECT length(value), CASE WHEN length(value) <= ? THEN value END "
        "FROM progress WHERE name = ?", (_MAX_STATE, "tree:" + key),
    ).fetchone()
    if row is None:
        return None
    if row[0] > _MAX_STATE:
        raise ValueError("cache continuation is oversized")
    state = json.loads(row[1])
    if (
        not isinstance(state, dict) or set(state) != {"namespace", "stack", "finalizing"}
        or not isinstance(state["stack"], list) or len(state["stack"]) > 64
        or type(state["finalizing"]) is not bool
    ):
        raise ValueError("cache continuation is invalid")
    identities = [state["namespace"]]
    for frame in state["stack"]:
        if (
            not isinstance(frame, list) or len(frame) != 2 or not isinstance(frame[0], str)
            or frame[0] in ("", ".", "..") or Path(frame[0]).name != frame[0]
            or ":" in frame[0]
        ):
            raise ValueError("cache continuation is invalid")
        identities.append(frame[1])
    if any(
        not isinstance(value, list) or len(value) != 2
        or any(type(number) is not int or number < 0 for number in value)
        for value in identities
    ) or state["stack"] and state["stack"][0][0] != "bytecode":
        raise ValueError("cache continuation identity is invalid")
    return state


def _save(database, key, state):
    encoded = json.dumps(state, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_STATE:
        raise ValueError("cache continuation exceeds its byte budget")
    database.execute(
        "INSERT OR REPLACE INTO progress(name, value) VALUES (?, ?)", ("tree:" + key, encoded),
    )


def _record(namespace, encoded, *, optional=False):
    try:
        with files.child(namespace, "owner.json", delete=optional, read=True) as record:
            record.check_budget()
            permissions.check_file(record.path)
            record.check_budget()
            if record.read(_MAX_RECORD) != encoded:
                raise ValueError("cache ownership differs")
            if optional:
                record.remove()
    except FileNotFoundError:
        if not optional:
            raise


def _descendant(entry):
    entry.check_budget()
    if os.name == "nt":
        if stat.S_ISDIR(entry.info.st_mode):
            permissions.check(entry.path, private=True)
        else:
            permissions.check_file(entry.path)
    elif entry.info.st_uid != os.getuid():
        raise ValueError("cache descendant has a foreign owner")


def _finish(namespace, encoded, work, report):
    # This phase was committed on an earlier pass. Its identity permits
    # recovery if a process died after removing owner.json but before rmdir.
    with namespace.names() as iterator:
        first = work.next(iterator)
        if first is not None and first.name != "owner.json":
            raise ValueError("cache namespace contains unexpected content")
        if first is not None and work.next(iterator) is not None:
            raise ValueError("cache namespace contains unexpected content")
    work.check()
    _record(namespace, encoded, optional=True)
    work.check()
    namespace.remove()
    report.completed += 1


def _tree(namespace, encoded, state, work, database, key):
    report = work.report
    _record(namespace, encoded)
    if state is None:
        state = {"namespace": list(files.identity(namespace.info)), "stack": [], "finalizing": False}
        try:
            with files.child(namespace, "bytecode") as bytecode:
                work.check()
                permissions.check(bytecode.path, private=True)
                state["stack"] = [["bytecode", list(files.identity(bytecode.info))]]
        except FileNotFoundError:
            state["finalizing"] = True
        _save(database, key, state)
    try:
        while state["stack"]:
            work.check()
            with contextlib.ExitStack() as opened:
                parent = namespace
                interrupted = False
                for index, (name, expected) in enumerate(state["stack"]):
                    work.check()
                    try:
                        parent = opened.enter_context(
                            files.child(parent, name, expected=expected, delete=True),
                        )
                    except FileNotFoundError:
                        # A previous pass can die after rmdir but before its
                        # catalog commit. Absence is progress, not replacement.
                        del state["stack"][index:]
                        interrupted = True
                        break
                    if index == 0:
                        work.check()
                        permissions.check(parent.path, private=True)
                    else:
                        _descendant(parent)
                if interrupted:
                    continue
                with parent.names() as iterator:
                    entry = work.next(iterator)
                if entry is None:
                    parent.remove()
                    state["stack"].pop()
                else:
                    with files.child(parent, entry.name, delete=True) as candidate:
                        if stat.S_ISDIR(candidate.info.st_mode):
                            _descendant(candidate)
                            if len(state["stack"]) >= 64:
                                raise ValueError("cache tree exceeds its depth budget")
                            state["stack"].append([
                                entry.name, list(files.identity(candidate.info)),
                            ])
                        else:
                            _descendant(candidate)
                            if not _BYTECODE.fullmatch(entry.name) or candidate.info.st_nlink != 1:
                                raise ValueError("cache contains non-bytecode or linked content")
                            if report.bytes + candidate.info.st_size > work.budget.bytes:
                                raise _Exhausted
                            candidate.remove()
                            report.files += 1
                            report.bytes += candidate.info.st_size
        state["finalizing"] = True
    finally:
        _save(database, key, state)


def collect(root: Path, *, retirement, budget: Budget | None = None) -> Report:
    """Collect only indexed namespaces under the caller's lexical slot custody.

    ``retirement(slot, check=...)`` is supplied by the versioned-runtime
    owner. It yields an empty string only while canonical cleanup leases are
    held, all protection checks pass and that exact slot is absent. It checks
    the supplied deadline between native process probes.
    """
    budget = budget or Budget()
    budget.validate()
    report = Report()
    work = _Pass(budget, report)
    root = Path(store.normalized(root))
    installation = store.installation_root(root)
    try:
        work.check()
        if not files.present(installation):
            return report
        with files.installation(installation, check_budget=work.check) as anchor, catalog.connection(
            installation, private_owner=private_owner, check_budget=work.check,
        ) as database:
            cursor = database.execute(
                "SELECT CASE WHEN length(value) = 64 THEN value ELSE '' END "
                "FROM progress WHERE name = 'collector'",
            ).fetchone()
            after = cursor[0] if cursor and _KEY.fullmatch(cursor[0]) else ""
            wrapped = False
            first = None
            for _ in range(budget.namespaces):
                try:
                    work.check()
                except _Exhausted:
                    report.exhausted = True
                    break
                row = database.execute(
                    "SELECT key, length(owner), CASE WHEN length(owner) <= ? THEN owner END "
                    "FROM entries WHERE key > ? ORDER BY key LIMIT 1", (_MAX_RECORD, after),
                ).fetchone()
                if row is None and not wrapped and after:
                    after, wrapped = "", True
                    row = database.execute(
                        "SELECT key, length(owner), CASE WHEN length(owner) <= ? THEN owner END "
                        "FROM entries WHERE key > '' ORDER BY key LIMIT 1", (_MAX_RECORD,),
                    ).fetchone()
                if row is None:
                    break
                key, size, encoded = row
                if first is None:
                    first = key
                elif wrapped and key >= first:
                    break
                report.namespaces += 1
                after = key
                database.execute(
                    "INSERT OR REPLACE INTO progress(name, value) VALUES ('collector', ?)", (key,),
                )
                try:
                    if size > _MAX_RECORD:
                        raise ValueError("cache owner exceeds its byte budget")
                    slot = _owner(encoded, key, root)
                    with retirement(slot, check=work.check) as reason:
                        if reason:
                            report.retain(reason)
                            continue
                        work.check()
                        state = _state(database, key)
                        prefix = installation / ("installed-" + key)
                        work.check()
                        if not files.present(prefix):
                            if state is None or not state["finalizing"]:
                                report.retain("missing-namespace")
                                continue
                            report.completed += 1
                        else:
                            with files.child(
                                anchor, prefix.name,
                                expected=state["namespace"] if state else None, delete=True,
                            ) as namespace:
                                work.check()
                                permissions.check(namespace.path, private=True)
                                if state and state["finalizing"]:
                                    _finish(namespace, encoded, work, report)
                                else:
                                    _tree(namespace, encoded, state, work, database, key)
                                    continue
                        database.execute("DELETE FROM entries WHERE key = ?", (key,))
                        database.execute("DELETE FROM progress WHERE name = ?", ("tree:" + key,))
                except _Exhausted:
                    report.exhausted = True
                    break
                except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                    report.retain("unsafe-or-unavailable")
                    report.error(error)
            else:
                report.exhausted = True
            report.pending = database.execute("SELECT 1 FROM entries LIMIT 1").fetchone() is not None
    except _Exhausted:
        report.exhausted = True
        report.pending = True
    except (OSError, ValueError) as error:
        report.retain("catalog-unavailable")
        report.error(error)
        report.pending = True
    return report


def collect_legacy(root: Path, *, retirement, budget=None):
    """Reconcile historical prefixes through the same runtime-owner custody."""
    from . import python_cache_legacy

    return python_cache_legacy.collect(root, retirement=retirement, budget=budget)
