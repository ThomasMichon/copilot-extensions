"""Indexed observations of current driver bindings, never execution or release authority."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

from .bridge_remote import normalize_host
from .queue_common import machine_matches

_RETAINED = (
    "(status IN ('claimed','started','suspended') OR "
    "(status='submitted' AND completed_by IS NOT NULL AND owner_session_id IS NOT NULL))"
)
_WORKER = "(CASE WHEN status='submitted' THEN completed_by ELSE owner END)"
_WAITER = "state IN ('preparing','active')"
_SPELLING_BUDGET = 32


class DriverMatch(Enum):
    CLEAR = "clear"
    MATCH = "match"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _Probe:
    name: str
    table: str
    predicate: str
    target: str
    host: str
    native: bool = False
    pin: bool = False

    @property
    def key(self) -> str:
        # ASCII printable spellings have SQLite/Python-compatible lower and strip.
        # Keep every other raw spelling intact for the actual Python comparator.
        return (
            f"(CASE WHEN {self.host} GLOB '*[^ -~]*' THEN 'u'||{self.host} "
            f"ELSE 'a'||lower(trim(COALESCE({self.host},''))) END)"
        )

    @property
    def index(self) -> str:
        return "idx_current_driver_v1_" + self.name


def _owner_probes(table: str, owner: str, retained: str) -> list[_Probe]:
    slash = f"instr({owner},'/')"
    qualified = f"({slash}>1 AND length({owner})>{slash})"
    tree = f"substr({owner},{slash}+1)"
    host = f"substr({owner},1,{slash}-1)"
    raw_host = f"(CASE WHEN {qualified} THEN {host} ELSE '' END)"
    fallback = f"{retained} AND NOT COALESCE({qualified},0)"
    probes = [
        _Probe(table + "_native", table, retained, "owner_session_id", raw_host, native=True),
        _Probe(table + "_tree", table, f"{retained} AND {qualified}", tree, host),
    ]
    if table == "tasks":
        # A pre-start pool's selected machine supersedes the original unqualified pin.
        fallback += " AND NOT (status='claimed' AND owner IS NULL)"
        probes.append(_Probe(
            "pin", table, fallback + " AND NULLIF(target_worktree,'') IS NOT NULL",
            "target_worktree", "target_machine", pin=True,
        ))
        fallback += " AND NULLIF(target_worktree,'') IS NULL"
    probes.append(_Probe(table + "_bare", table, fallback, owner,
                         "target_machine" if table == "tasks" else "''", pin=table == "tasks"))
    return probes


_PROBES = (
    *_owner_probes("tasks", _WORKER, _RETAINED),
    *_owner_probes("run_waiters", "owner", _WAITER),
    _Probe("pool", "current_driver_pool_v1", "placed=1", "tree", "machine"),
    _Probe("prestart_pin", "current_driver_pool_v1", "placed=0", "tree", "machine", pin=True),
)


def install_indexes(conn: sqlite3.Connection) -> None:
    for probe in _PROBES:
        task = "id" if probe.table == "tasks" else "task_id"
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {probe.index} ON {probe.table}"
            f"({probe.target},{probe.key},{task}) WHERE {probe.predicate}"
        )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_current_driver_v1_waiter_task "
        f"ON run_waiters(task_id) WHERE {_WAITER}"
    )


def _probe(conn: sqlite3.Connection, p: _Probe, target: str, machine: str,
           exclude: str) -> DriverMatch:
    task = "id" if p.table == "tasks" else "task_id"
    other = "" if p.table == "run_waiters" else f" AND {task}<>:excluded"
    verify = ""
    if p.table == "current_driver_pool_v1":
        # REPLACE can implicitly delete a task on a secondary uniqueness conflict
        # without firing its delete trigger on a legacy connection.
        verify = (" AND EXISTS (SELECT 1 FROM current_driver_pool_source_v1 s"
                  " WHERE s.task_id=current_driver_pool_v1.task_id"
                  " AND s.machine=current_driver_pool_v1.machine"
                  " AND s.tree=current_driver_pool_v1.tree"
                  " AND s.placed=current_driver_pool_v1.placed)")
    base = (f" FROM {p.table} INDEXED BY {p.index} WHERE {p.predicate}"
            f" AND {p.target}=:target{other}{verify} AND ")
    params = {"target": target, "excluded": exclude, "key": "a" + (
        machine.casefold() if p.pin else normalize_host(machine)
    )}
    exact = "SELECT 1" + base + p.key + "=:key LIMIT 1"
    if conn.execute(exact, params).fetchone():
        return DriverMatch.MATCH
    unknown = conn.execute(exact, {**params, "key": "a"}).fetchone() is not None

    # Seek the next distinct spelling, not the next holder: 10,000 rows owned by
    # one foreign host cost one comparison. Excessive alias fanout is not absence.
    seek = f"SELECT {p.key}" + base + f"{p.key}>:key ORDER BY {p.key} LIMIT 1"
    params["key"] = "u"
    for _ in range(_SPELLING_BUDGET):
        row = conn.execute(seek, params).fetchone()
        if row is None:
            return DriverMatch.UNKNOWN if unknown else DriverMatch.CLEAR
        params["key"] = row[0]
        raw = params["key"][1:].strip()
        if not raw:
            unknown = True
        elif (machine_matches(raw, machine) if p.pin
              else normalize_host(raw) == normalize_host(machine)):
            return DriverMatch.MATCH
    if unknown or conn.execute(seek, params).fetchone():
        return DriverMatch.UNKNOWN
    return DriverMatch.CLEAR


def current_driver_match(conn: sqlite3.Connection, *, task_id: str, machine: str,
                         worktree: str, native_id: str | None) -> DriverMatch:
    """Inspect other assignments/placements and all live waiters in the caller's snapshot."""
    if not conn.in_transaction:
        raise ValueError("current driver lookup requires a transaction")
    if not task_id or not machine.strip() or not worktree:
        raise ValueError("current driver lookup requires task and workspace identity")
    if conn.execute(
        f"SELECT 1 FROM run_waiters WHERE {_WAITER} AND task_id=? LIMIT 1", (task_id,),  # noqa: S608 -- fixed predicate
    ).fetchone():
        return DriverMatch.MATCH
    answer = DriverMatch.CLEAR
    for probe in _PROBES:
        target = native_id if probe.native else worktree
        if target:
            result = _probe(conn, probe, target, machine.strip(), task_id)
            if result is DriverMatch.MATCH:
                return result
            if result is DriverMatch.UNKNOWN:
                answer = result
    return answer
