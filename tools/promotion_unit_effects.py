"""Execution observation for the explicitly admitted in-process unit packet."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from promotion_unit_contract import UnitEvidenceError


class UnitRun:
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

    def refuse(self, effect: str) -> None:
        self.effects.append(effect)
        raise UnitEvidenceError(f"not an eligible unit effect: {effect}")

    def audit(self, event: str, args: tuple) -> None:
        if not self.active:
            return
        if (
            event.startswith(("subprocess.", "socket.", "os.exec", "os.spawn", "os.fork"))
            or event in ("os.system", "os.posix_spawn", "pty.spawn", "ctypes.dlopen")
        ):
            self.refuse(event)
        mutations = {
            "os.mkdir": 1, "os.remove": 1, "os.rmdir": 1, "os.rename": 2,
            "os.chmod": 1, "os.chown": 1, "os.truncate": 1, "os.utime": 1,
        }
        if event in ("os.link", "os.symlink"):
            source, destination = (Path(os.fsdecode(value)) for value in args[:2])
            destination = destination.resolve()
            if not source.is_absolute():
                source = destination.parent / source
            if not all(path.resolve().is_relative_to(self.temporary) for path in (source, destination)):
                self.refuse("filesystem indirection outside synthetic temporary storage")
        if event in mutations:
            for name in args[:mutations[event]]:
                if not isinstance(name, (str, bytes, os.PathLike)):
                    self.refuse("unattributable filesystem mutation")
                if not Path(os.fsdecode(name)).resolve().is_relative_to(self.temporary):
                    self.refuse("mutation outside synthetic unit temporary storage")
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if ".git" in (part.casefold() for part in path.parts):
                self.refuse("actual Git storage access")
            mode, flags = args[1:3]
            writing = (
                isinstance(mode, str) and any(flag in mode for flag in "wax+")
                or isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
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
        self.coverage.switch_context(item.nodeid + "|setup")

    def pytest_runtest_call(self, item):
        self.coverage.switch_context(item.nodeid + "|call")

    def pytest_runtest_teardown(self, item):
        self.coverage.switch_context(item.nodeid + "|teardown")

    def pytest_runtest_logreport(self, report):
        if getattr(report, "wasxfail", False):
            self.effects.append("xfail/xpass outcome is not positive unit evidence")
        self.reports.append({
            "node": report.nodeid, "phase": report.when, "outcome": report.outcome,
        })
