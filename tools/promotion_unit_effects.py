"""Execution observation for the explicitly admitted in-process unit packet."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from coverage import CoveragePlugin

from promotion_unit_contract import UnitEvidenceError


class UnitRun(CoveragePlugin):
    def __init__(self, coverage, root: Path, sources: dict, temporary: Path):
        self.coverage = coverage
        self.root = root
        self.sources = sources
        self.temporary = temporary
        self.opened_sources: set[str] = set()
        self.collected: list[str] = []
        self.reports: list[dict] = []
        self.effects: list[str] = []
        self.active = False
        self.call_code = None
        self.call_node = None
        self._os_open = os.open
        self._open_paths: list[tuple[str, Path]] = []

    def refuse(self, effect: str) -> None:
        self.effects.append(effect)
        raise UnitEvidenceError(f"not an eligible unit effect: {effect}")

    def mutation_path(self, name, directory_fd=None) -> Path:
        if not isinstance(name, (str, bytes, os.PathLike)):
            self.refuse("unattributable filesystem mutation")
        path = Path(os.fsdecode(name))
        if not path.is_absolute() and directory_fd not in (None, -1):
            if type(directory_fd) is not int or directory_fd < 0 or sys.platform != "linux":
                self.refuse("unattributable directory-relative mutation")
            try:
                directory = Path(os.readlink(f"/proc/self/fd/{directory_fd}"))
            except OSError:
                self.refuse("unavailable directory-relative mutation identity")
            path = directory / path
        return path.resolve()

    def guarded_open(self, name, flags, mode=0o777, *, dir_fd=None):
        if not self.active:
            return self._os_open(name, flags, mode, dir_fd=dir_fd)
        path = self.mutation_path(name, dir_fd)
        self.audit("open", (str(path), None, flags))
        self._open_paths.append((os.fsdecode(name), path))
        try:
            return self._os_open(name, flags, mode, dir_fd=dir_fd)
        finally:
            self._open_paths.pop()

    def audit(self, event: str, args: tuple) -> None:
        if not self.active:
            return
        if (
            event.startswith(("subprocess.", "socket.", "os.exec", "os.spawn", "os.fork"))
            or event in ("os.system", "os.posix_spawn", "pty.spawn", "ctypes.dlopen")
        ):
            self.refuse(event)
        mutations = {
            "os.mkdir": ((0, 2),), "os.remove": ((0, 1),), "os.rmdir": ((0, 1),),
            "os.rename": ((0, 2), (1, 3)), "os.chmod": ((0, 2),),
            "os.chown": ((0, 3),), "os.truncate": ((0, None),), "os.utime": ((0, 3),),
        }
        if event in ("os.link", "os.symlink"):
            if event == "os.symlink":
                destination = self.mutation_path(args[1], args[2] if len(args) > 2 else None)
                source = Path(os.fsdecode(args[0]))
                if not source.is_absolute():
                    source = destination.parent / source
            else:
                source = self.mutation_path(args[0], args[2] if len(args) > 2 else None)
                destination = self.mutation_path(args[1], args[3] if len(args) > 3 else None)
            if not all(path.resolve().is_relative_to(self.temporary) for path in (source, destination)):
                self.refuse("filesystem indirection outside synthetic temporary storage")
        if event in mutations:
            for index, directory_index in mutations[event]:
                directory_fd = args[directory_index] if directory_index is not None and len(args) > directory_index else None
                if not self.mutation_path(args[index], directory_fd).is_relative_to(self.temporary):
                    self.refuse("mutation outside synthetic unit temporary storage")
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            name = os.fsdecode(args[0])
            mode, flags = args[1:3]
            if self._open_paths and self._open_paths[-1][0] == name:
                path = self._open_paths[-1][1]
            else:
                path = Path(name)
                if not path.is_absolute() and mode is None:
                    self.refuse("unattributable descriptor-relative open")
                path = path.resolve()
            if ".git" in (part.casefold() for part in path.parts):
                self.refuse("actual Git storage access")
            writing = (
                isinstance(mode, str) and any(flag in mode for flag in "wax+")
                or isinstance(flags, int) and flags & (
                    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
                )
            )
            if writing and not path.is_relative_to(self.temporary):
                self.refuse("write outside synthetic unit temporary storage")
            if path.suffix == ".pyc":
                try:
                    path = Path(importlib.util.source_from_cache(str(path)))
                except ValueError:
                    self.refuse("unattributable Python bytecode")
            if path.suffix == ".py" and path.is_relative_to(self.root):
                relative = path.relative_to(self.root).as_posix()
                self.opened_sources.add(relative)
                if relative not in self.sources:
                    self.refuse("unadmitted source read: " + relative)

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_setup(self, item):
        self.call_code = None
        self.call_node = None

    def pytest_runtest_call(self, item):
        function = getattr(item.obj, "__func__", item.obj)
        self.call_code = getattr(function, "__code__", None)
        self.call_node = item.nodeid
        if self.call_code is None:
            self.refuse("unit case must be a direct Python test function")

    def pytest_runtest_teardown(self, item):
        self.call_code = None
        self.call_node = None

    def dynamic_context(self, frame):
        if frame.f_code is self.call_code:
            return self.call_node + "|call"
        return None

    def pytest_runtest_logreport(self, report):
        if hasattr(report, "wasxfail"):
            self.effects.append("xfail/xpass outcome is not positive unit evidence")
        self.reports.append({
            "node": report.nodeid, "phase": report.when, "outcome": report.outcome,
        })
