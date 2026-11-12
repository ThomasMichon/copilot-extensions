"""Relevance admission through the existing fresh registrar and merge owners."""

from __future__ import annotations

import json
import sqlite3
from threading import Lock

from .pool_source_snapshot import read_supervisor_declarations
from .pool_source_snapshot import local_input_revision
from .queue_records import TaskError
from .queue_relevance_registration import EffectiveRelevanceSources, RelevanceRegistration
from .registrar_discovery import RegistrarSources
from .registrar_reconcile import declared_registrations
from .supervisor_registration import merge_registration_sources


class RelevanceSource:
    def __init__(self, sources: RegistrarSources | None = None) -> None:
        self.sources = sources if sources is not None else RegistrarSources()
        self._lock = Lock()

    def resolve(
        self, conn: sqlite3.Connection, selector: RelevanceRegistration,
    ) -> EffectiveRelevanceSources:
        try:
            with self._lock:
                observation = read_supervisor_declarations(self.sources, legacy=True)
        except (OSError, ValueError, RuntimeError) as exc:
            raise TaskError("relevance effective registrar source is unavailable") from exc
        if not observation.complete:
            raise TaskError("relevance effective registrar source is incomplete")
        machine, env = selector.machine, selector.env
        declared = declared_registrations(observation, machine=machine, env=env)
        direct, other = [], {}
        for row in conn.execute("SELECT * FROM registrations").fetchall():
            if row["id"].startswith("declared:"):
                continue
            item = dict(row)
            item["spec"] = json.loads(row["spec"])
            if (row["machine"], row["env"], row["status"]) == (machine, env, "active"):
                direct.append(item)
            else:
                other[item["id"]] = item
        merged = merge_registration_sources(direct, declared)
        if selector.registration_id in merged.replacements:
            raise TaskError("relevance direct registration is superseded by its effective declaration")
        registrations = {**other, **merged.registrations}
        revision = local_input_revision([
            {key: reg.get(key) for key in ("id", "kind", "spec", "machine", "env", "status", "owner")}
            for _, reg in sorted(registrations.items())
        ], [])
        return EffectiveRelevanceSources(registrations, revision)
