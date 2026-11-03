"""Bounded foreign native-hold inspection, independent of asserted session state."""

from __future__ import annotations

import os
import re
import stat
from uuid import UUID

from . import native_source_context, session_liveness, sessions, tracking

MAX_SESSIONS = 256
MAX_FILES = 4096
MAX_HOLDS = 64


def require_no_foreign_hold(record: tracking.WorktreeRecord, native_id: str) -> None:
    """Refuse held or uninspectable foreign files, including concluded sessions."""
    indexed = record.sessions or []
    if len(indexed) > MAX_SESSIONS:
        raise ValueError("Own-worker foreign-session inspection exceeds its session budget")
    files = holds = 0
    for session in indexed:
        if session.session_id == native_id:
            continue
        try:
            if str(UUID(session.session_id)) != session.session_id:
                raise ValueError("Noncanonical native identity")
            directory = sessions._session_state_dir() / session.session_id
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise ValueError("Foreign session directory is not directly inspectable")
            with os.scandir(directory) as entries:
                for entry in entries:
                    files += 1
                    if files > MAX_FILES:
                        raise ValueError("Own-worker foreign-session inspection exceeds its file budget")
                    if not (entry.name.startswith("inuse.") and entry.name.endswith(".hold")):
                        continue
                    holds += 1
                    if holds > MAX_HOLDS:
                        raise ValueError("Own-worker foreign-session inspection exceeds its hold budget")
                    match = re.fullmatch(r"inuse\.([1-9][0-9]{0,19})\.hold", entry.name)
                    if match is None:
                        raise ValueError("Foreign native hold identity is unconfirmed")
                    pid = int(match[1])
                    try:
                        process = native_source_context.process(pid)
                    except FileNotFoundError:
                        continue
                    state = session_liveness.session_process_hold_state(
                        session.session_id, pid, process["start_time"],
                    )
                    if state != "absent":
                        raise ValueError("Own-worker closeout retains a held or unconfirmed foreign native session")
        except FileNotFoundError:
            continue
        except (OSError, ValueError, IndexError) as exc:
            raise ValueError(f"Own-worker foreign native inspection refused: {exc}") from exc
