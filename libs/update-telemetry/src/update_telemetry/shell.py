"""Bounded, launch-owned rendering for shells without portable native clocks.

The shell retains command execution and writes NUL-delimited record triples
to a private temporary spool. One renderer owns the phase's UTC/monotonic
clocks, log and gated terminal sink; it is stopped before session handoff.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import TextIO

from .human import HumanLineBuffer, HumanOperation
from .redact import redact


class ShellProgress:
    def __init__(self, log: TextIO, terminal: TextIO, *, interactive=False, wall=None, monotonic=None):
        self.log = log
        self.terminal = terminal
        self.interactive = interactive
        self.echo = False
        self.root = HumanOperation("launch", stream=self, wall=wall, monotonic=monotonic)
        self.action: HumanOperation | None = None
        self.failed = False
        self.lines = HumanLineBuffer(self._log_line)
        self.level = "INFO"

    def write(self, text: str) -> int:
        self.log.write(text)
        if self.interactive and self.echo:
            self.terminal.write(text)
        return len(text)

    def flush(self) -> None:
        self.log.flush()
        if self.interactive and self.echo:
            self.terminal.flush()

    def _log_line(self, text: str) -> None:
        state = {"WARN": "warning", "ERROR": "failed"}.get(self.level)
        (self.action or self.root).progress(text, state=state)

    def record(self, kind: str, value: str, message: str) -> bool:
        if kind == "log":
            if value != self.level:
                self.lines.flush()
                self.level = value
            self.echo = False
            self.failed |= value in {"WARN", "ERROR"}
            self.lines.feed(message)
            return True
        self.echo = False
        self.lines.flush()
        self.echo = True
        if kind == "status":
            state = {"WARN": "warning", "ERROR": "failed"}.get(value, "running")
            self.failed |= value in {"WARN", "ERROR"}
            (self.action or self.root).progress(message, state=state)
        elif kind == "begin":
            if self.action is not None:
                raise ValueError("launcher started an action before completing its predecessor")
            action = HumanOperation(message)
            action.__enter__()
            self.action = action
        elif kind == "end":
            if self.action is None or value not in {"done", "failed"}:
                raise ValueError("launcher completed an unknown action or outcome")
            self.failed |= value == "failed"
            self.action.complete(value, message)
            self.action.__exit__(None, None, None)
            self.action = None
        elif kind == "finish":
            if self.action is not None:
                raise ValueError("launcher stopped with an incomplete action")
            self.root.complete(
                "warning" if self.failed else "done",
                "maintenance finished with warnings; launch continues"
                if self.failed else "maintenance finished",
            )
            return False
        else:
            raise ValueError("unknown launcher progress record")
        return True


def render(events: Path, ready: Path, log: TextIO, terminal: TextIO, *, parent: int, interactive: bool) -> None:
    progress = ShellProgress(log, terminal, interactive=interactive)
    with progress.root:
        ready.write_text("0\n", encoding="ascii")
        try:
            with events.open("rb") as source:
                pending = b""
                while True:
                    if os.getppid() != parent:
                        raise RuntimeError("launch parent exited before stopping its formatter")
                    chunk = source.read(65536)
                    if not chunk:
                        time.sleep(0.01)
                        continue
                    pending += chunk
                    while pending.count(b"\0") >= 3:
                        kind, value, message, pending = pending.split(b"\0", 3)
                        fields = [part.decode("utf-8", errors="replace") for part in (kind, value, message)]
                        if not progress.record(*fields):
                            if pending:
                                raise ValueError("launcher wrote records after stopping its formatter")
                            return
                        if fields[0] == "begin":
                            # The native command starts only after its clock is live.
                            ready.write_text(fields[1] + "\n", encoding="ascii")
        except BaseException as exc:
            progress.echo = False
            progress.lines.flush()
            if progress.action is not None:
                progress.action.__exit__(type(exc), exc, exc.__traceback__)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--interactive", choices=("0", "1"), required=True)
    args = parser.parse_args()
    try:
        with args.log.open("a", encoding="utf-8") as log:
            render(
                args.events, args.ready, log, sys.stderr,
                parent=args.parent, interactive=args.interactive == "1",
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Launch progress formatter failed: {redact(str(exc))}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
