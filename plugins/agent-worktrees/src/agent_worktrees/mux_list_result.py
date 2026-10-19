"""Classify and parse mux listings without hiding unavailable evidence."""

from __future__ import annotations

import re
from subprocess import CompletedProcess


def is_empty_tmux_listing(binary: str, result: CompletedProcess[str]) -> bool:
    """Recognize only tmux's definitive no-server responses to list-sessions."""
    name = binary.replace("\\", "/").rsplit("/", 1)[-1].casefold().removesuffix(".exe")
    if name != "tmux" or result.returncode != 1 or (result.stdout or "").strip():
        return False
    message = (result.stderr or "").strip()
    return bool(
        re.fullmatch(r"no server running on [^\r\n]+", message)
        or re.fullmatch(
            r"error connecting to [^\r\n]+ \(No such file or directory\)",
            message,
        )
    )


def _attached_count(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def parse_session_counts(stdout: str | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in (stdout or "").strip().splitlines():
        if ":" in line:
            name, _, count = line.rpartition(":")
            result[name] = _attached_count(count)
    return result


def parse_monitor_sessions(stdout: str | None) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for line in (stdout or "").strip().splitlines():
        if line.count(":") < 3:
            continue
        name, _, created = line.rpartition(":")
        name, _, session_id = name.rpartition(":")
        name, _, count = name.rpartition(":")
        result[name] = (_attached_count(count), f"{session_id}:{created}")
    return result
