"""Bounded, sticky promotion evidence owned by the native routing table.

Capacity exhaustion retains every remembered publication and disables negative
proofs. An epoch witness must be captured before spawning a passive candidate;
history initialized after its birth cannot establish that it never served.
"""

from __future__ import annotations

import uuid

KEY = "publication_history"
MAX_ENTRIES = 2048


def _endpoint(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    pid, port = raw.get("pid"), raw.get("port")
    start, bind = raw.get("process_start_time"), raw.get("bind")
    generation = raw.get("generation", 0)
    if (
        not isinstance(bind, str) or len(bind) > 255
        or type(port) is not int or not 0 < port < 65536
        or (pid is not None and (type(pid) is not int or not 0 < pid < 2**32))
        or (start is not None and (
            not isinstance(start, str) or len(start) > 64 or not start.isascii() or not start.isdigit()
        ))
        or type(generation) is not int or not 0 <= generation < 2**63
    ):
        return None
    return {
        "bind": bind, "port": port, "pid": pid,
        "process_start_time": start, "generation": generation,
    }


def read(table: dict) -> dict | None:
    history = table.get(KEY)
    if not isinstance(history, dict):
        return None
    entries = history.get("entries")
    epoch = history.get("epoch")
    if (
        type(history.get("version")) is not int or history["version"] != 1
        or not isinstance(epoch, str) or len(epoch) != 32
        or any(char not in "0123456789abcdef" for char in epoch)
        or type(history.get("saturated")) is not bool
        or not isinstance(entries, list) or len(entries) > MAX_ENTRIES
        or any(not isinstance(entry, dict) or _endpoint(entry) != entry for entry in entries)
    ):
        return None
    return history


def updated(previous: dict, following: dict) -> dict:
    old = read(previous)
    history = (
        {**old, "entries": list(old["entries"])}
        if old is not None else
        {"version": 1, "epoch": uuid.uuid4().hex, "saturated": False, "entries": []}
    )
    # Preserve observed claims even on clear/restore/stale-route healing.
    for table in (previous, following):
        for slot in ("active", "previous"):
            raw = table.get(slot)
            if raw is None:
                continue
            entry = _endpoint(raw)
            if entry is None:
                history["saturated"] = True
            elif entry not in history["entries"]:
                if len(history["entries"]) == MAX_ENTRIES:
                    history["saturated"] = True
                else:
                    history["entries"].append(entry)
    return history


def never_published(table: dict, witness: object, *, pid: object, port: object) -> bool:
    """A conservative negative proof, not permission to kill by PID."""
    history = read(table)
    if (
        history is None or history["saturated"]
        or witness != history["epoch"]
        or type(pid) is not int or pid <= 0
        or type(port) is not int or not 0 < port < 65536
    ):
        return False
    # PID or port reuse may block cleanup; it must never erase custody.
    return not any(
        entry["pid"] == pid or entry["port"] == port
        for entry in history["entries"]
    )


def candidate_never_published(table: dict, record: dict, pid: int, start: str | None) -> bool:
    return (
        isinstance(start, str) and bool(start)
        and record.get("new_pid") == pid
        and record.get("new_process_start_time") == start
        and never_published(
            table, record.get("publication_witness"), pid=pid, port=record.get("new_port"),
        )
    )
