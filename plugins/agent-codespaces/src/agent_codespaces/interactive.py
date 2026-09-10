"""Caller-owned terminal commands and strictly loopback SSH forwards."""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
from pathlib import Path


def normalize_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Validate before command-file normalization, claims, or connections."""
    if args.command != "ssh":
        return
    command = args.interactive_command
    path = args.interactive_command_file
    interactive = command is not None or path is not None
    forwards = args.local_forward or args.reverse_forward
    if (interactive or forwards) and (
        args.remote_cmd is not None or args.remote_cmd_file is not None or args.stdio
    ):
        parser.error(
            "interactive commands and forwards cannot be combined with "
            "--remote-cmd, --remote-cmd-file, or --stdio"
        )
    if command is not None and path is not None:
        parser.error("--interactive-command and --interactive-command-file are mutually exclusive")
    if path is not None:
        try:
            # Decode without universal-newline translation or trimming the payload.
            command = Path(path).read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeError) as exc:
            parser.error(f"--interactive-command-file could not be read as UTF-8: {exc}")
    if interactive and (not command or not command.strip() or "\x00" in command):
        parser.error("interactive command must be nonblank and contain no NUL bytes")
    args.interactive_command = command
    for option in ("local_forward", "reverse_forward"):
        listeners: set[int] = set()
        values: list[tuple[int, int]] = []
        for value in getattr(args, option):
            if not re.fullmatch(r"[0-9]{1,5}:[0-9]{1,5}", value):
                parser.error(f"--{option.replace('_', '-')} requires two ports: LISTEN:CONNECT")
            listen, connect = (int(port) for port in value.split(":"))
            if not 1 <= listen <= 65535 or not 1 <= connect <= 65535:
                parser.error("forward ports must be in 1..65535")
            if listen in listeners:
                parser.error(f"duplicate --{option.replace('_', '-')} listener port: {listen}")
            listeners.add(listen)
            values.append((listen, connect))
        setattr(args, option, values)


def ssh_options(
    local: list[tuple[int, int]], reverse: list[tuple[int, int]],
) -> list[str]:
    """Render options, never shell words or caller-supplied SSH switches."""
    if not local and not reverse:
        return []
    result = [
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
    ]
    for flag, forwards in (("-L", local), ("-R", reverse)):
        for listen, connect in forwards:
            result.extend([flag, f"127.0.0.1:{listen}:127.0.0.1:{connect}"])
    return result


def process_group_options() -> dict:
    """Keep console/stdio attached while owning a separate POSIX process group."""
    if sys.platform == "win32":
        return {}
    if sys.version_info >= (3, 11):
        return {"process_group": 0}
    # Python 3.10 lacks process_group; the child hook performs only this syscall.
    return {"preexec_fn": os.setpgrp}


def foreground_group(group: int) -> tuple[int, int] | None:
    """Lend the caller's controlling terminal to the owned group, when present."""
    if sys.platform == "win32":
        return None
    try:
        fd = sys.stdin.fileno()
        previous = os.tcgetpgrp(fd)
        if previous != os.getpgrp():
            return None
        os.tcsetpgrp(fd, group)
    except (OSError, ValueError):
        return None
    try:
        # The child may have read stdin before the foreground handoff.
        os.killpg(group, signal.SIGCONT)
    except ProcessLookupError:
        pass
    return fd, previous


def restore_foreground(previous: tuple[int, int] | None) -> None:
    if previous is None:
        return
    handler = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(*previous)
    except OSError:
        pass
    finally:
        signal.signal(signal.SIGTTOU, handler)
