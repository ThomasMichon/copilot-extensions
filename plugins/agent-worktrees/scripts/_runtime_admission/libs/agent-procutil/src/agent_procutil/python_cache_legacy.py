"""Incremental, source-qualified legacy relief during normal runtime maintenance."""
from __future__ import annotations

from bisect import bisect_right
import contextlib
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import re
import stat

from . import _daemon_instance_owner as private_owner
from . import python_cache_catalog as catalog
from . import python_cache_collect as indexed
from . import python_cache_files as files
from . import python_cache_permissions as permissions
from . import python_cache_pyc as pyc
from . import python_cache_store as store

_NAMESPACE = re.compile(r"(?:unsealed-[0-9a-f]{32}|installed-[0-9a-f]{64})")
_MAX_STATE = 32768


@dataclass(frozen=True)
class Budget(indexed.Budget):
    namespaces: int = 128
    entries: int = 2048
    seconds: float = 2.0
    discovery: int = 100000
    names_bytes: int = 16 * 1024 * 1024

    def validate(self):
        super().validate()
        if any(type(value) is not int or value < 1 for value in (self.discovery, self.names_bytes)):
            raise ValueError("legacy inventory budgets must be finite and positive")


@dataclass
class Report(indexed.Report):
    discovered: int = 0
    names_bytes: int = 0
    inspected_bytes: int = 0


class _Pass(indexed._Pass):
    def __init__(self, budget, report):
        super().__init__(budget, report)
        self.inventories = {}

    def names(self, directory):
        """Only a complete bounded census may supply a lexical continuation."""
        key = (str(directory.path), files.identity(directory.info))
        if key in self.inventories:
            return self.inventories[key]
        self.check()
        before = directory.path.lstat()
        names = []
        with directory.names() as iterator:
            while True:
                self.check()
                if self.report.discovered >= self.budget.discovery:
                    self.report.retain("inventory-incomplete")
                    raise indexed._Exhausted
                entry = next(iterator, None)
                self.report.discovered += 1
                if entry is None:
                    break
                size = len(os.fsencode(entry.name))
                if size > self.budget.names_bytes - self.report.names_bytes:
                    self.report.retain("inventory-incomplete")
                    raise indexed._Exhausted
                self.report.names_bytes += size
                names.append(entry.name)
        self.check()
        directory.verify()
        after = directory.path.lstat()
        if before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns:
            raise ValueError("legacy inventory changed")
        names.sort()
        self.check()
        self.inventories[key] = names
        return names


def _mirror_parts(path):
    text = store.normalized(path)
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = text[2:]
    return Path(text.lstrip(os.sep + (os.altsep or ""))).parts


def _name(value):
    return (
        isinstance(value, str) and value not in ("", ".", "..")
        and Path(value).name == value and ":" not in value and "\0" not in value
    )


def _identity(value):
    return (
        isinstance(value, list) and len(value) == 2
        and all(type(number) is int and number >= 0 for number in value)
    )


def _state(database, name):
    row = database.execute(
        "SELECT length(value), CASE WHEN length(value) <= ? THEN value END "
        "FROM progress WHERE name = ?", (_MAX_STATE, "legacy:" + name),
    ).fetchone()
    if row is None:
        return None
    if row[0] > _MAX_STATE:
        raise ValueError("legacy continuation exceeds its byte budget")
    state = json.loads(row[1])
    if (
        not isinstance(state, dict) or set(state) != {"namespace", "versions", "after", "stack"}
        or not _identity(state["namespace"]) or not _identity(state["versions"])
        or not (state["after"] == "" or _name(state["after"]))
        or not isinstance(state["stack"], list) or len(state["stack"]) > 64
        or any(
            not isinstance(frame, list) or len(frame) != 3 or not _name(frame[0])
            or not _identity(frame[1]) or not (frame[2] == "" or _name(frame[2]))
            for frame in state["stack"]
        )
    ):
        raise ValueError("legacy continuation is invalid")
    return state


def _save(database, name, state):
    encoded = json.dumps(state, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_STATE:
        raise ValueError("legacy continuation exceeds its byte budget")
    database.execute(
        "INSERT OR REPLACE INTO progress(name, value) VALUES (?, ?)", ("legacy:" + name, encoded),
    )


def _checked(entry):
    entry.check_budget()
    if stat.S_ISDIR(entry.info.st_mode):
        permissions.check(entry.path)
    else:
        permissions.check_file(entry.path, private=False)
    entry.check_budget()
    entry.verify()


def _remove_bytecode(namespace, entry, root, slot, work):
    report = work.report
    size = entry.info.st_size
    if (
        entry.info.st_nlink != 1
        or not re.fullmatch(r".+\.cpython-[0-9]{2,3}(?:\.opt-[0-9]+)?\.pyc", entry.path.name)
    ):
        report.retain("legacy-not-bytecode")
        return
    if size > 8 * 1024 * 1024:
        report.retain("legacy-bytecode-oversized")
        return
    if size > work.budget.bytes:
        report.retain("legacy-bytecode-over-budget")
        return
    if size > work.budget.bytes - report.inspected_bytes or size > work.budget.bytes - report.bytes:
        raise indexed._Exhausted
    report.inspected_bytes += size
    data = entry.read(size)
    filename = pyc.source(data, check=work.check)
    tag = pyc.cache_tag(data)
    source = Path(filename)
    if not source.is_absolute() or source.suffix not in (".py", ".pyw"):
        raise ValueError("legacy source filename is not an absolute Python source")
    source = Path(store.normalized(source))
    target = Path(store.normalized(root / "versions" / slot))
    if not source.is_relative_to(target) or source == target:
        raise ValueError("legacy bytecode belongs to another runtime")
    mirror = namespace.path.joinpath(*_mirror_parts(source.parent))
    if (
        store.normalized(mirror) != store.normalized(entry.path.parent)
        or not re.fullmatch(
            re.escape(source.stem + "." + tag)
            + r"(?:\.opt-[0-9]+)?\.pyc", entry.path.name,
        )
    ):
        raise ValueError("legacy source and bytecode mirror differ")
    _checked(entry)
    current = entry.path.lstat()
    if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
        size, entry.info.st_mtime_ns, entry.info.st_ctime_ns,
    ):
        raise ValueError("legacy bytecode changed while inspected")
    entry.remove()
    report.files += 1
    report.bytes += size


def _tree(namespace, versions, state, root, slot, work):
    while state["stack"]:
        work.check()
        with contextlib.ExitStack() as opened:
            parent = versions
            for index, (name, expected, _after) in enumerate(state["stack"]):
                try:
                    parent = opened.enter_context(
                        files.child(parent, name, expected=expected, delete=True),
                    )
                except FileNotFoundError:
                    del state["stack"][index:]
                    break
                _checked(parent)
            else:
                frame = state["stack"][-1]
                names = work.names(parent)
                index = bisect_right(names, frame[2])
                if index == len(names):
                    work.entry()
                    try:
                        parent.remove()
                    except OSError as error:
                        if error.errno not in (errno.ENOTEMPTY, errno.EEXIST) and (
                            getattr(error, "winerror", None) != 145
                        ):
                            raise
                        work.report.retain("legacy-content-retained")
                    else:
                        if len(state["stack"]) == 1:
                            work.report.completed += 1
                    state["stack"].pop()
                    continue
                name = names[index]
                work.entry()
                try:
                    with files.child(parent, name, delete=True, read=True) as entry:
                        _checked(entry)
                        if stat.S_ISDIR(entry.info.st_mode):
                            if len(state["stack"]) >= 64:
                                raise ValueError("legacy tree exceeds its depth budget")
                            state["stack"].append([name, list(files.identity(entry.info)), ""])
                        else:
                            _remove_bytecode(namespace, entry, root, slot, work)
                except FileNotFoundError:
                    pass
                except (OSError, ValueError, UnicodeError) as error:
                    work.report.retain("legacy-unsafe-or-unavailable")
                    work.report.error(error)
                frame[2] = name


def _namespace(namespace, root, state, work, retired):
    _checked(namespace)
    work.report.retain("legacy-whole-prefix-no-writer-custody")
    with contextlib.ExitStack() as opened:
        versions = namespace
        try:
            for part in (*_mirror_parts(root), "versions"):
                versions = opened.enter_context(files.child(versions, part))
                _checked(versions)
        except FileNotFoundError:
            work.report.retain("legacy-no-runtime-mirror")
            return None
        if state is None:
            state = {
                "namespace": list(files.identity(namespace.info)),
                "versions": list(files.identity(versions.info)), "after": "", "stack": [],
            }
        elif state["versions"] != list(files.identity(versions.info)):
            raise ValueError("legacy runtime mirror was replaced")
        try:
            while True:
                work.check()
                if state["stack"]:
                    slot = state["stack"][0][0]
                else:
                    names = work.names(versions)
                    index = bisect_right(names, state["after"])
                    if index == len(names):
                        return None
                    slot = names[index]
                    work.entry()
                if not indexed._SLOT.fullmatch(slot) or slot.endswith(".") or slot.lower() == "dev":
                    work.report.retain("legacy-unknown-slot")
                    state["after"], state["stack"] = slot, []
                    continue
                try:
                    reason = retired(slot)
                    if reason:
                        work.report.retain(reason)
                    else:
                        if not state["stack"]:
                            with files.child(versions, slot) as entry:
                                _checked(entry)
                                state["stack"] = [[slot, list(files.identity(entry.info)), ""]]
                        _tree(namespace, versions, state, root, slot, work)
                except (OSError, ValueError) as error:
                    work.report.retain("legacy-unsafe-or-unavailable")
                    work.report.error(error)
                state["after"], state["stack"] = slot, []
        finally:
            # The caller saves the same object even when a budget interrupts it.
            if state is not None:
                work.state = state


def collect(root: Path, *, retirement, budget: Budget | None = None) -> Report:
    """Reclaim exact retired-source leaves, never whole unsealed namespaces."""
    budget = budget or Budget()
    budget.validate()
    report = Report()
    work = _Pass(budget, report)
    if not Path(root).is_absolute():
        raise ValueError("legacy maintenance requires an absolute runtime root")
    root = store.native(Path(store.normalized(root)))
    legacy = root / "cache" / "python"
    try:
        work.check()
        if not files.present(legacy):
            return report
        with files.installation(legacy, private=False, check_budget=work.check) as anchor:
            names = work.names(anchor)
            if not names:
                return report
            location = store.installation_root(root, create=True, check_budget=work.check)
            work.check()
            with files.installation(location, check_budget=work.check), catalog.connection(
                location, private_owner=private_owner, check_budget=work.check,
            ) as database, contextlib.ExitStack() as leases:
                held = {}

                def retired(slot):
                    if slot not in held:
                        held[slot] = leases.enter_context(retirement(slot, check=work.check))
                    work.check()
                    return held[slot]

                row = database.execute(
                    "SELECT CASE WHEN length(value) <= 256 THEN value ELSE '' END "
                    "FROM progress WHERE name = 'legacy-collector'",
                ).fetchone()
                after = row[0] if row else ""
                start = bisect_right(names, after)
                count = min(len(names), budget.namespaces)
                for offset in range(count):
                    try:
                        work.entry()
                    except indexed._Exhausted:
                        report.exhausted = True
                        break
                    name = names[(start + offset) % len(names)]
                    report.namespaces += 1
                    database.execute(
                        "INSERT OR REPLACE INTO progress(name, value) VALUES ('legacy-collector', ?)",
                        (name,),
                    )
                    if not _NAMESPACE.fullmatch(name):
                        report.retain("legacy-unknown-namespace")
                        continue
                    work.state = None
                    try:
                        state = _state(database, name)
                        with files.child(
                            anchor, name, expected=state["namespace"] if state else None,
                        ) as namespace:
                            work.state = state
                            result = _namespace(namespace, root, state, work, retired)
                        if result is None:
                            database.execute("DELETE FROM progress WHERE name = ?", ("legacy:" + name,))
                            work.state = None
                    except indexed._Exhausted:
                        report.exhausted = True
                        break
                    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                        report.retain("legacy-unsafe-or-unavailable")
                        report.error(error)
                    finally:
                        if work.state is not None:
                            _save(database, name, work.state)
                report.pending = bool(names)
                if count < len(names):
                    report.exhausted = True
    except indexed._Exhausted:
        report.exhausted = report.pending = True
    except (OSError, ValueError, TypeError, RecursionError) as error:
        report.retain("legacy-inventory-unavailable")
        report.error(error)
        report.pending = True
    return report
