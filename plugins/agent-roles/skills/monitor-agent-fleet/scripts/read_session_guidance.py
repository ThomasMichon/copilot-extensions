"""Finite selected-session inspection. Standard library only; no child commands."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from typing import NoReturn

MAX_SECONDS = 5.0
MAX_ENTRIES = 256
MAX_DIRECTORY_ENTRIES = 64
MAX_DIRECTORIES = 32
MAX_FILE_BYTES = 8192
MAX_TOTAL_BYTES = 24576
MAX_OUTPUT_BYTES = 32768
MAX_PROVIDERS = 8
GUIDANCE = "session-guidance.instructions.md"
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
SESSION = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)


class Refusal(Exception):
    """A public-safe reason code, never a filesystem exception message."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Refusal("invalid-arguments")


def linked(info: os.stat_result) -> bool:
    """Recognize POSIX links and every Windows reparse-point kind."""
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def checked_path(value: str) -> Path:
    """Reject unsupported paths and links in every existing ancestor."""
    if len(value) > 4096:
        raise Refusal("invalid-path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or len(path.parts) > 32:
        raise Refusal("invalid-path")
    for ancestor in (*reversed(path.parents), path):
        if linked(ancestor.lstat()):
            raise Refusal("link-or-reparse-point")
    return path


class Report:
    """Accumulate serialized primitives within a fixed wire budget."""

    def __init__(self) -> None:
        self.rows: list[str] = []
        self.used = 0
        self.gaps: set[str] = set()
        self.entries = 0
        self.directories = 0
        self.bytes_read = 0
        self.full = False

    def add(self, **row: str) -> None:
        if self.full:
            return
        encoded = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
        size = len(encoded) + 1
        # Leave room for the fixed envelope, counters and bounded reason set.
        if self.used + size > MAX_OUTPUT_BYTES - 2048:
            self.gaps.add("output-budget")
            self.full = True
            return
        self.rows.append(encoded)
        self.used += size

    def gap(self, code: str, label: str) -> None:
        self.gaps.add(code)
        self.add(path=label, status=code)

    def listing(self, directory: Path, label: str) -> dict[str, str]:
        """Stream a single directory; never sort/materialize an unbounded iterator."""
        found: dict[str, str] = {}
        if self.full:
            return found
        if self.entries >= MAX_ENTRIES:
            self.gap("entry-budget", label)
            return found
        if self.directories >= MAX_DIRECTORIES:
            self.gap("directory-budget", label)
            return found
        self.directories += 1
        if linked(directory.lstat()) or not stat.S_ISDIR(directory.lstat().st_mode):
            self.gap("unsupported-directory", label)
            return found
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if self.full:
                    break
                if self.entries >= MAX_ENTRIES:
                    self.gap("entry-budget", label)
                    break
                self.entries += 1
                if len(entry.name) > 128 or any(ord(c) < 32 for c in entry.name):
                    self.gap("unsupported-name", label)
                else:
                    relative = f"{label}/{entry.name}" if label else entry.name
                    info = entry.stat(follow_symlinks=False)
                    kind = (
                        "link" if linked(info) else
                        "directory" if stat.S_ISDIR(info.st_mode) else
                        "file" if stat.S_ISREG(info.st_mode) else "unsupported"
                    )
                    found[entry.name] = kind
                    self.add(path=relative, kind=kind)
                    if kind in {"link", "unsupported"}:
                        self.gaps.add("unsupported-entry")
                if index + 1 >= MAX_DIRECTORY_ENTRIES or self.entries >= MAX_ENTRIES:
                    # Conservative at the boundary: no extra next() to prove EOF.
                    self.gap("entry-budget", label)
                    break
        return found

    def read(self, path: Path, label: str) -> None:
        """Bound actual bytes, including any growth-detection lookahead."""
        if self.full:
            return
        info = path.lstat()
        if linked(info) or not stat.S_ISREG(info.st_mode):
            self.gap("unsupported-file", label)
            return
        budget = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - self.bytes_read)
        if info.st_size > budget:
            self.gap("file-budget" if info.st_size > MAX_FILE_BYTES else "total-budget", label)
            return
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode) or linked(opened)
                or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                self.gap("changed-or-unsupported-file", label)
                return
            data = stream.read(min(budget, info.st_size + 1))
            after = os.fstat(stream.fileno())
        self.bytes_read += len(data)
        if len(data) != info.st_size or after.st_size != info.st_size:
            self.gap("file-changed", label)
            return
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            self.gap("invalid-utf8", label)
            return
        self.add(path=label, status="read", text=text)

    def render(self) -> str:
        """Return bounded ASCII JSON, including explicit partial coverage."""
        summary = json.dumps({
            "contract": "agent-roles.selected-session-guidance/v1",
            "version": 1,
            "coverage": "partial" if self.gaps else "selected-scope",
            "gaps": sorted(self.gaps),
            "entries": self.entries,
            "directories": self.directories,
            "bytes_read": self.bytes_read,
        }, separators=(",", ":"))
        return summary[:-1] + ',"records":[' + ",".join(self.rows) + "]}\n"


def collect(args: argparse.Namespace, report: Report) -> None:
    """List before reading only the explicitly selected session and providers."""
    root = checked_path(args.session_state_root)
    session = checked_path(str(root / args.session_id))
    top = report.listing(session, "")
    if top.get("instructions") != "directory":
        report.gap("instructions-unavailable", "instructions")
    else:
        instructions = session / "instructions"
        providers = report.listing(instructions, "instructions")
        for provider in args.guidance:
            label = f"instructions/{provider}"
            if providers.get(provider) != "directory":
                report.gap("provider-unavailable", label)
                continue
            files = report.listing(instructions / provider, label)
            # Depth two is the final directory: report, never descend further.
            if "directory" in files.values():
                report.gap("depth-budget", label)
            if files.get(GUIDANCE) != "file":
                report.gap("guidance-unavailable", f"{label}/{GUIDANCE}")
                continue
            report.read(instructions / provider / GUIDANCE, f"{label}/{GUIDANCE}")
    if args.maintenance_file is not None:
        maintenance = checked_path(args.maintenance_file)
        if maintenance.name != "maintenance.json":
            raise Refusal("invalid-maintenance-name")
        listing = report.listing(maintenance.parent, "maintenance-parent")
        if listing.get("maintenance.json") != "file":
            report.gap("maintenance-unavailable", "maintenance.json")
        else:
            # Deliberately text, not an arbitrary-depth JSON object graph.
            report.read(maintenance, "maintenance.json")


def requested_timeout(argv: list[str]) -> float:
    """Find the smallest valid explicit deadline before strict parsing can fail."""
    seconds = MAX_SECONDS
    for index, argument in enumerate(argv):
        if argument == "--":
            break
        if argument.startswith("--timeout="):
            value = argument.partition("=")[2]
        elif argument == "--timeout" and index + 1 < len(argv):
            value = argv[index + 1]
        else:
            continue
        try:
            candidate = float(value)
        except ValueError:
            continue
        if 0.001 <= candidate <= MAX_SECONDS:
            seconds = min(seconds, candidate)
    return seconds


def main(argv: list[str] | None = None) -> int:
    """Run a bounded read; exit 0 selected scope, 2 gap/refusal, 124 deadline."""
    started = time.monotonic()
    # No subprocesses are ever started. _exit ends this sole collector even
    # while its main thread is blocked in filesystem or stdout I/O.
    watchdog = threading.Timer(MAX_SECONDS, os._exit, args=(124,))
    watchdog.daemon = True
    watchdog.start()
    report = Report()
    try:
        argv = sys.argv[1:] if argv is None else argv
        if len(argv) > 32 or sum(len(a) for a in argv) > 8192:
            raise Refusal("argument-budget")
        remaining = requested_timeout(argv) - (time.monotonic() - started)
        if remaining <= 0:
            os._exit(124)
        shorter = threading.Timer(remaining, os._exit, args=(124,))
        shorter.daemon = True
        shorter.start()
        parser = Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--session-state-root", required=True)
        parser.add_argument("--session-id", required=True)
        parser.add_argument("--guidance", action="append", default=[])
        parser.add_argument("--maintenance-file")
        parser.add_argument("--timeout", type=float, default=MAX_SECONDS)
        args = parser.parse_args(argv)
        if not 0.001 <= args.timeout <= MAX_SECONDS:
            raise Refusal("invalid-timeout")
        if not SESSION.fullmatch(args.session_id):
            raise Refusal("invalid-session-id")
        if len(args.guidance) > MAX_PROVIDERS or any(
            not IDENTIFIER.fullmatch(p) for p in args.guidance
        ):
            raise Refusal("invalid-provider")
        args.guidance = list(dict.fromkeys(args.guidance))
        collect(args, report)
    except Refusal as exc:
        report.gap(str(exc), "")
    except (OSError, ValueError):
        report.gap("filesystem-unavailable", "")
    except Exception:
        report.gap("reader-failed", "")
    try:
        sys.stdout.write(report.render())
        sys.stdout.flush()
        return 2 if report.gaps else 0
    except (OSError, UnicodeError):
        os._exit(2)
    finally:
        watchdog.cancel()
        if "shorter" in locals():
            shorter.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
