"""Whole-prefix reclamation only after native reference and runtime custody."""
from __future__ import annotations

from bisect import bisect_right
from contextlib import nullcontext
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import time
import uuid
from typing import Any, Callable, ContextManager, Iterable, cast

from . import python_cache_collect as indexed
from . import python_cache_files as files
from . import python_cache_inventory as inventory
from . import python_cache_legacy as legacy
from . import python_cache_permissions as permissions
from . import python_cache_processes as processes
from . import python_cache_store as store

_UNSET = object()


@dataclass(frozen=True)
class Budget(legacy.Budget):
    namespaces: int = 512
    entries: int = 262144
    bytes: int = 4 * 1024**3
    seconds: float = 30


@dataclass
class Report(legacy.Report):
    measured_bytes: int = 0
    eligible_bytes: int = 0
    held_bytes: int = 0
    eligible: int = 0
    unmeasured: int = 0
    namespace_total: int = 0
    inventory_complete: bool = False
    namespace_names: tuple[str, ...] = ()
    next_cursor: str = ""
    liveness: str = "unavailable"
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _progress(
    root: Path, name: str, work: Any, value: Any = _UNSET, *,
    previous: dict[str, Any] | None = None,
) -> Any:
    location = store.installation_root(root, check_budget=work.check) / "legacy-prefix-progress"
    if value is not _UNSET and value is not None:
        store.installation_root(root, create=True, check_budget=work.check)
        permissions.mkdir(location)
    if not files.present(location):
        return None
    with files.installation(location, check_budget=work.check) as directory:
        key = name + ".json"
        if value is None:
            with files.child(directory, key, read=True, delete=True) as entry:
                permissions.check_file(entry.path)
                _record(entry.read(4096))
                entry.remove()
            return None
        if value is not _UNSET:
            encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
            if len(encoded) > 4096:
                raise ValueError("namespace continuation oversized")
            temporary = location / ("." + uuid.uuid4().hex + ".tmp")
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                directory.verify()
                if previous is None:
                    os.link(temporary, location / key)
                else:
                    with files.child(directory, key, read=True) as entry:
                        permissions.check_file(entry.path)
                        if _record(entry.read(4096)) != previous:
                            raise ValueError("namespace continuation changed")
                        entry.verify()
                    directory.verify()
                    os.replace(temporary, location / key)
            finally:
                temporary.unlink(missing_ok=True)
            return value
        try:
            with files.child(directory, key, read=True) as entry:
                permissions.check_file(entry.path)
                return _record(entry.read(4096))
        except FileNotFoundError:
            return None


def _record(encoded: bytes) -> dict[str, Any]:
    def unique(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("namespace continuation has duplicate fields")
            result[key] = value
        return result

    record = json.loads(encoded, object_pairs_hook=unique)
    if (
        not isinstance(record, dict) or set(record) != {"version", "identity", "slots", "cutoff"}
        or type(record["version"]) is not int or record["version"] != 1
        or not isinstance(record["identity"], list) or len(record["identity"]) != 3
        or not all(type(number) is int and number >= 0 for number in record["identity"][:2])
        or not isinstance(record["identity"][2], str) or len(record["identity"][2]) > 64
        or not isinstance(record["slots"], list) or not 1 <= len(record["slots"]) <= 64
        or any(not isinstance(slot, str) or not indexed._SLOT.fullmatch(slot)
               or slot.endswith(".") or slot.lower() == "dev" for slot in record["slots"])
        or type(record["cutoff"]) not in (int, float) or not math.isfinite(record["cutoff"])
    ):
        raise ValueError("namespace continuation invalid")
    return cast(dict[str, Any], record)


def _slots(tree: inventory.Tree, root: Path, *, allow_missing: bool = False) -> list[str]:
    relative = (*cast(Any, legacy)._mirror_parts(root), "versions")
    actual = next((
        parts for parts in tree.directories
        if tuple(os.path.normcase(part) for part in parts)
        == tuple(os.path.normcase(part) for part in relative)
    ), None)
    if actual is None:
        if allow_missing:
            return []
        raise ValueError("runtime-association-unavailable")
    record = tree.directories[actual]
    slots = [name for name in record[1] if (*actual, name) in tree.directories]
    if not slots and allow_missing:
        return []
    if not 1 <= len(slots) <= 64 or any(
        not indexed._SLOT.fullmatch(slot) or slot.endswith(".") or slot.lower() == "dev"
        for slot in slots
    ):
        raise ValueError("runtime-association-unavailable")
    return slots


def collect(
    root: Path, *, retirement: Callable[..., ContextManager[str]], apply: bool = False,
    budget: Budget | None = None, after: str = "", min_age_seconds: float = 7 * 86400,
    proof: Any = None, required_pids: Any = (),
    namespace_birth: Callable[[files.Entry], list[int | str]] = inventory.birth,
    now: float | None = None,
) -> Report:
    """Inspect or reclaim recognized namespaces; callbacks own runtime authority.

    The private continuation records association, never authority. Every resumed
    effect rechecks birth identity, live references and runtime retention.
    Dry runs do not create or update continuation records.
    """
    budget = budget or Budget()
    cast(Any, budget).validate()
    if not Path(root).is_absolute() or not cast(Any, legacy)._cursor(after) or not 0 <= min_age_seconds < float("inf"):
        raise ValueError("invalid legacy collection cursor or grace period")
    root = store.native(Path(store.normalized(root)))
    report = Report(next_cursor=after)
    work: Any = cast(Any, legacy)._Pass(budget, report)
    current_time = time.time() if now is None else now
    cache = root / "cache" / "python"
    try:
        if not files.present(cache):
            report.inventory_complete = True
            return report
        with files.installation(cache, private=False, check_budget=work.check) as anchor:
            names = work.names(anchor, namespace=True)
            report.namespace_total = len(names)
            report.namespace_names = tuple(names)
            report.inventory_complete = True
            if not names:
                return report
            context = nullcontext(proof) if proof is not None else processes.observe(
                seconds=budget.seconds, check=work.check,
                required_pids=(lambda: required_pids(check=work.check)) if callable(required_pids)
                else required_pids,
            )
            with context as references:
                report.liveness = references.mode
                start = bisect_right(names, after)
                for offset in range(min(len(names), budget.namespaces)):
                    name = names[(start + offset) % len(names)]
                    work.entry()
                    report.namespaces += 1
                    report.next_cursor = name
                    if not legacy._NAMESPACE.fullmatch(name):
                        cast(Any, report).retain("legacy-unknown-namespace")
                        continue
                    item: dict[str, Any] = {"name": name, "status": "held", "bytes": None, "reason": ""}
                    report.candidates.append(item)
                    try:
                        with files.child(anchor, name, delete=apply) as namespace:
                            tree = inventory.inspect(namespace, work)
                            item["bytes"] = tree.bytes
                            report.measured_bytes += tree.bytes
                            record = _progress(root, name, work)
                            identity = namespace_birth(namespace)
                            item["generation"] = identity
                            if record is not None and record["identity"] != identity:
                                item["reason"] = "namespace-generation-changed"
                                continue
                            created = float(identity[2])
                            if os.name == "nt":
                                created = created / 10000000 - 11644473600
                            if not math.isfinite(created):
                                raise ValueError("namespace creation time unavailable")
                            young = current_time - max(created, tree.content_newest) < min_age_seconds
                            renewing = record is not None and tree.content_newest > record["cutoff"]
                            if record is None and young:
                                item["reason"] = "younger-than-grace-period"
                                continue
                            if renewing and young:
                                item["reason"] = "bytecode-changed-since-admission"
                                continue
                            if not references.verify():
                                item["reason"] = references.reason or "liveness-unproven"
                                continue
                            if name.lower() in references.names:
                                item["reason"] = "referenced-by-live-process"
                                continue
                            slots = _slots(tree, root, allow_missing=record is not None)
                            if record is not None:
                                slots = sorted({
                                    slot.lower(): slot for slot in [*record["slots"], *slots]
                                }.values(), key=str.lower)
                            if len(slots) > 64:
                                raise ValueError("runtime association exceeds its bound")
                            with retirement(slots, check=work.check) as reason:
                                if reason:
                                    item["reason"] = reason
                                    continue
                                if not references.verify():
                                    item["reason"] = references.reason or "liveness-unproven"
                                    continue
                                if name.lower() in references.names:
                                    item["reason"] = "referenced-by-live-process"
                                    continue
                                if tree.bytes > budget.bytes - report.bytes:
                                    item["reason"] = "namespace-byte-budget"
                                    continue
                                needed = len(tree.leaves) + 2 * len(tree.directories) - 1
                                if apply and needed > budget.entries - report.entries:
                                    item["reason"] = "namespace-entry-budget"
                                    continue
                                if not apply:
                                    item["status"] = "eligible"
                                    report.eligible += 1
                                    report.eligible_bytes += tree.bytes
                                    continue
                                if record is None or renewing or slots != record["slots"]:
                                    _progress(root, name, work, {
                                        "version": 1, "identity": identity, "slots": slots,
                                        "cutoff": current_time - min_age_seconds
                                        if record is None or renewing else record["cutoff"],
                                    }, previous=record)
                                item["status"] = "partial"
                                inventory.remove(namespace, tree, work)
                                item["status"] = "removed"
                                report.completed += 1
                                _progress(root, name, work, None)
                    except indexed._Exhausted:
                        item["reason"] = "pass-budget"
                        report.exhausted = True
                        break
                    except (OSError, ValueError, TypeError, RecursionError) as error:
                        item["reason"] = "namespace-unsafe-or-unavailable"
                        cast(Any, report).error(error)
                    finally:
                        if item["status"] == "held" and item["bytes"] is not None:
                            report.held_bytes += item["bytes"]
                        if item["bytes"] is None or item["status"] == "partial":
                            report.unmeasured += 1
                        if item["reason"]:
                            cast(Any, report).retain(item["reason"])
                report.liveness = references.mode
                report.pending = report.completed < len(names)
                report.exhausted |= report.namespaces < len(names)
    except (indexed._Exhausted, legacy._InventoryUnavailable):
        report.pending = report.exhausted = True
    except (OSError, ValueError, TypeError, RecursionError) as error:
        cast(Any, report).retain("legacy-inventory-unavailable")
        cast(Any, report).error(error)
        report.pending = True
    return report


def collect_prefixes(
    root: Path, *, retirement: Callable[..., ContextManager[str]],
    budget: dict[str, Any] | None = None, **options: Any,
) -> Report:
    return collect(root, retirement=retirement, budget=Budget(**budget) if budget else None, **options)
