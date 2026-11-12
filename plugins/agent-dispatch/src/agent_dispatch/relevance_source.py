"""Relevance admission through the existing fresh registrar and merge owners."""

from __future__ import annotations

import json
import sqlite3
from threading import Lock

from .pool_source_snapshot import read_supervisor_declarations
from .queue_records import TaskError
from .registrar_discovery import RegistrarSources
from .registrar_reconcile import declared_registrations
from .supervisor_registration import merge_registration_sources


class RelevanceSource:
    def __init__(self, sources: RegistrarSources | None = None) -> None:
        self.sources = sources if sources is not None else RegistrarSources()
        self._lock = Lock()

    def require_effective_direct(self, conn: sqlite3.Connection, registration: dict) -> None:
        try:
            with self._lock:
                observation = read_supervisor_declarations(self.sources, legacy=True)
        except (OSError, ValueError, RuntimeError) as exc:
            raise TaskError("relevance effective registrar source is unavailable") from exc
        if not observation.complete:
            raise TaskError("relevance effective registrar source is incomplete")
        machine, env = registration["machine"], registration["env"]
        declared = declared_registrations(observation, machine=machine, env=env)
        direct = []
        for row in conn.execute(
            "SELECT * FROM registrations WHERE machine=? AND env=? AND status='active'",
            (machine, env),
        ).fetchall():
            if row["id"].startswith("declared:"):
                continue
            item = dict(row)
            item["spec"] = json.loads(row["spec"])
            direct.append(item)
        merged = merge_registration_sources(direct, declared)
        if registration["id"] not in merged.registrations:
            raise TaskError("relevance direct registration is superseded by its effective declaration")
