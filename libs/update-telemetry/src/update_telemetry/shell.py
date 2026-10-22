"""Bounded, launch-owned rendering for shells without portable native clocks.

The shell retains command execution and writes NUL-delimited record triples
to a private temporary spool. One renderer owns the phase's UTC/monotonic
clocks, log and gated terminal sink; it is stopped before session handoff.
The separate ``observe`` mode follows one detached native invocation instead;
its installer retains deadline, staging and installation ownership.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .human import HumanLineBuffer, HumanOperation, native_output
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

    def record(self, kind: str, value: str, message: str, *, completed_at: float | None = None) -> bool:
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
            elapsed = None if completed_at is None else completed_at - self.action.started
            self.action.complete(value, message, elapsed=elapsed)
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


class _ParentExited(RuntimeError):
    pass


def _records(events: Path, parent: int, stopped: threading.Event):
    with events.open("rb") as source:
        pending = b""
        number = 0
        while not stopped.is_set():
            if os.getppid() != parent:
                raise _ParentExited("launch parent exited before stopping its formatter")
            chunk = source.read(65536)
            if not chunk:
                stopped.wait(0.01)
                continue
            pending += chunk
            while pending.count(b"\0") >= 3:
                kind, value, message, pending = pending.split(b"\0", 3)
                yield number, (kind, value, message), pending
                number += 1


def render(
    events: Path, ready: Path, log: TextIO, terminal: TextIO, *,
    parent: int, interactive: bool, _parent_loss: Callable[[], None] | None = None,
) -> None:
    progress = ShellProgress(log, terminal, interactive=interactive)
    stopped = threading.Event()
    changed = threading.Condition()
    started: set[int] = set()
    completed: dict[int, float] = {}
    clock_error: BaseException | None = None

    def sample():
        nonlocal clock_error
        try:
            for number, fields, _pending in _records(events, parent, stopped):
                if fields[0] == b"begin":
                    with changed:
                        while number not in started and not stopped.is_set():
                            changed.wait(0.1)
                            if not stopped.is_set() and os.getppid() != parent:
                                raise _ParentExited("launch parent exited before admitting its action")
                        started.discard(number)
                elif fields[0] == b"end":
                    boundary = progress.root.monotonic()
                    with changed:
                        completed[number] = boundary
                        changed.notify_all()
        except BaseException as exc:
            if isinstance(exc, _ParentExited) and _parent_loss is not None:
                _parent_loss()
            with changed:
                clock_error = exc
                changed.notify_all()

    # Start before any sink write, including the root's first line. Only
    # boundary clocks are retained; diagnostic bytes stay in the private spool.
    sampler = threading.Thread(target=sample, name="launch-action-clock")
    sampler.start()
    try:
        with progress.root:
            try:
                ready.write_text("0\n", encoding="ascii")
                for number, raw, pending in _records(events, parent, stopped):
                    fields = [part.decode("utf-8", errors="replace") for part in raw]
                    boundary = None
                    if fields[0] == "end":
                        with changed:
                            while number not in completed:
                                if clock_error is not None:
                                    raise clock_error
                                changed.wait()
                            boundary = completed.pop(number)
                    if not progress.record(*fields, completed_at=boundary):
                        if pending:
                            raise ValueError("launcher wrote records after stopping its formatter")
                        return
                    if fields[0] == "begin":
                        # Admit only after earlier output and the start line
                        # drain; end sampling never waits for that sink.
                        assert progress.action is not None
                        progress.action.started = progress.root.monotonic()
                        with changed:
                            started.add(number)
                            changed.notify_all()
                        ready.write_text(fields[1] + "\n", encoding="ascii")
            except BaseException as exc:
                progress.echo = False
                progress.lines.flush()
                if progress.action is not None:
                    progress.action.__exit__(type(exc), exc, exc.__traceback__)
                raise
    finally:
        stopped.set()
        with changed:
            changed.notify_all()
        sampler.join()


def observe_command(log: TextIO, argv: list[str]) -> int:
    """Observe one native invocation without acquiring installation ownership."""
    import subprocess

    with HumanOperation("launch/installer", stream=log) as progress:
        try:
            with native_output() as output:
                result = subprocess.run(
                    argv, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, check=False,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
        except OSError as exc:
            progress.complete("failed", f"Installer observation failed: {exc}")
            return 1
        code = 128 - result.returncode if result.returncode < 0 else result.returncode
        state = "failed" if code else "done"
        outcome = (
            "continuing with existing version" if code
            else "launcher changes apply on the next launch"
        )
        progress.complete(
            state, f"Installer update {state} (exit {code}); {outcome}",
        )
        return code


def _observe_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Persist one detached installer's human output.")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("an installer command is required")
    try:
        with args.log.open("a", encoding="utf-8") as log:
            return observe_command(log, command)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Installer output observer failed: {redact(str(exc))}", file=sys.stderr, flush=True)
        return 1


def main() -> int:
    if sys.argv[1:2] == ["observe"]:
        return _observe_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--parent", type=int, required=True)
    parser.add_argument("--interactive", choices=("0", "1"), required=True)
    args = parser.parse_args()

    def parent_loss():
        try:
            args.ready.with_name(args.ready.name + ".failure").write_text(
                "parent-exited\n", encoding="ascii",
            )
        finally:
            # This is the dedicated CLI child only: never wait for a blocked
            # terminal writer or its locks when the launch owner has gone.
            os._exit(1)

    try:
        with args.log.open("a", encoding="utf-8") as log:
            render(
                args.events, args.ready, log, sys.stderr,
                parent=args.parent, interactive=args.interactive == "1",
                _parent_loss=parent_loss,
            )
        args.ready.write_text("finished\n", encoding="ascii")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Launch progress formatter failed: {redact(str(exc))}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
