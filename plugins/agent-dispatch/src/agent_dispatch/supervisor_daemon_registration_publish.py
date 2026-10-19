"""Publish/withdraw declared registrations to the coordinator's store.

Extracted from ``supervisor_daemon.py`` purely to keep that module under its
module-size baseline as the repo grows (the same rationale
``coordinator_registries.py`` documents for its own extraction). This module
owns exactly one responsibility: mirroring the registrar/YAML **declared**
profile set into the coordinator's own ``registrations`` table, so a
coordinator-side policy consumer (e.g. ``TaskQueue.set_card``'s
``steering_disallowed_labels`` gate) can see a field a declaration sets --
the local declared-set merge in ``SupervisorDaemon._declared()`` only ever
fed that daemon's own subprocess-desired-state computation, never the
coordinator's ``GET /registrations`` surface (confirmed missing in PR
review, copilot-extensions#3731).
"""

from __future__ import annotations

import logging
import copy
import time
from concurrent.futures import CancelledError, Executor, Future, TimeoutError

import httpx

from .registrations import RegistrationKind

log = logging.getLogger("agent-dispatch.supervisor-daemon")


def _environment(registration: dict) -> str:
    value = registration.get("env")
    return "default" if value is None else value


class BoundedDeclarationPublisher:
    """Single tracked metadata batch; uncertain writes are read back before retry."""

    def __init__(self):
        self.future: Future | None = None
        self.records: dict[str, dict] = {}
        self.inflight_ids: set[str] = set()
        self.seen_ids: set[str] = set()
        self.withdrawn_ids: set[str] = set()
        self.withdrawal_epoch = 0

    def _harvest(self, outcome: tuple[dict[str, dict], Exception | None]) -> None:
        records, error = outcome
        withdrawn = self.inflight_ids - set(records)
        if withdrawn - self.withdrawn_ids:
            self.withdrawal_epoch += 1
        self.withdrawn_ids.update(withdrawn)
        self.records = records
        if error is not None:
            raise error

    def observe_registry(self, ids: set[str], started_epoch: int) -> set[str] | None:
        if started_epoch != self.withdrawal_epoch:
            return None
        cleared = self.withdrawn_ids - ids
        self.withdrawn_ids.difference_update(cleared)
        self.seen_ids.difference_update(cleared)
        return cleared

    def poll(self, client, registrations: list[dict], *, executor: Executor, deadline: float) -> set[str]:
        wanted = {reg["id"]: reg for reg in registrations if reg.get("kind") in RegistrationKind.DIRECT}
        self.seen_ids.update(wanted)
        self.withdrawn_ids.difference_update(wanted)
        if self.future is not None and self.future.done():
            try:
                self._harvest(self.future.result())
            except CancelledError:
                pass  # queued batch performed no write; retain the prior ledger
            finally:
                self.future = None
                self.inflight_ids = set()
        if self.future is None and time.monotonic() < deadline:
            for rid in self.seen_ids - self.withdrawn_ids:
                self.records.setdefault(rid, {"target": wanted.get(rid), "state": "pending"})
            self.inflight_ids = set(wanted) | set(self.records)
            self.future = executor.submit(self._advance, client, wanted, self.records, deadline)
        if self.future is not None:
            try:
                records = self.future.result(timeout=max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                if self.future.done():
                    raise
            else:
                try:
                    self._harvest(records)
                finally:
                    self.future = None
                    self.inflight_ids = set()
        return set(wanted) | set(self.records) | self.inflight_ids | self.seen_ids

    @staticmethod
    def _matches(row: dict | None, target: dict | None) -> bool:
        if target is None:
            return row is None
        return (row is not None and _environment(row) == _environment(target)
                and all(row.get(key) == target.get(key) for key in ("kind", "spec", "machine")))

    @classmethod
    def _advance(cls, client, wanted: dict[str, dict], previous: dict[str, dict],
                 deadline: float) -> tuple[dict[str, dict], Exception | None]:
        wanted, records = copy.deepcopy(wanted), copy.deepcopy(previous)
        try:
            return cls._advance_batch(client, wanted, records, deadline), None
        except Exception as exc:
            # The caller harvests partial custody before re-raising the failure.
            return records, exc

    @classmethod
    def _advance_batch(cls, client, wanted: dict[str, dict], records: dict[str, dict], deadline: float) -> dict:
        if time.monotonic() >= deadline:
            return records
        targets = {
            rid: prior["target"] if (prior := records.get(rid)) is not None and prior["state"] == "unknown"
            else wanted.get(rid)
            for rid in set(wanted) | set(records)
        }
        for rid, target in targets.items():
            prior = records.setdefault(rid, {"target": target, "state": "pending"})
            if "readback" in prior and prior["readback"]["target"] != target:
                prior.pop("readback")
        # Consume completed observations before another potentially slow RPC.
        if not any("readback" in prior for prior in records.values()):
            try:
                rows = {row["id"]: row for row in client.list_registrations(include_paused=True)}
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                log.warning("declaration publication readback unavailable: error_type=%s", type(exc).__name__)
                return records
            for rid, target in targets.items():
                records[rid]["readback"] = {"target": target, "row": rows.get(rid)}
        for rid, target in sorted(targets.items()):
            if time.monotonic() >= deadline:
                break
            observation = records[rid].pop("readback", None)
            if observation is None:
                continue
            if cls._matches(observation["row"], target):
                if target is None:
                    records.pop(rid, None)
                else:
                    records[rid] = {"target": target, "state": "confirmed"}
                continue
            records[rid] = {"target": target, "state": "unknown"}
            try:
                if target is None:
                    client.remove_registration(rid)
                else:
                    client.register_registration(
                        target["kind"], target["spec"], reg_id=rid,
                        machine=target.get("machine"), env=_environment(target),
                    )
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                log.warning("declaration publication outcome unknown: error_type=%s", type(exc).__name__)
            else:
                if target is not None:
                    records[rid] = {"target": target, "state": "confirmed"}
                # Keep withdrawal fenced until a subsequent read confirms absence.
        return records


def publish_declared_registrations(
    client, registrations: list[dict], *, published_ids: set[str]
) -> set[str]:
    """Upsert each declared registration into the coordinator's store, and
    withdraw any previously-published id no longer declared. Returns the new
    published-id set for the caller to persist across reconcile ticks.

    Best-effort and idempotent (``register_registration`` upserts by the
    declared id, preserving ``created_at``/``status``): a publish failure,
    or the coordinator being briefly unreachable, is logged and skipped --
    the caller's own local subprocess-desired-state merge has its own
    independent source of truth and must keep working even if the
    coordinator is down. Only ``RegistrationKind.DIRECT`` kinds are
    published; a ``plugin-companion`` declaration has no coordinator-side
    registration counterpart and is skipped (never published, never tracked
    for withdrawal).

    A dropped declaration is actively removed from the coordinator's store,
    not merely left behind: a stale ``declared:...`` row would otherwise
    remain visible to ``list_registrations()`` forever, which would make a
    store-backed desired-set computation keep treating a withdrawn unit as
    desired after its registrar/YAML source no longer lists it (a
    regression this same fix introduced and a PR review caught).
    """
    new_published: set[str] = set()
    for reg in registrations:
        kind = reg.get("kind")
        if kind not in RegistrationKind.DIRECT:
            continue
        rid = reg.get("id")
        new_published.add(rid)
        try:
            client.register_registration(
                kind,
                reg["spec"],
                reg_id=rid,
                machine=reg.get("machine"),
                env=_environment(reg),
            )
        except Exception:  # pragma: no cover - coordinator reachability varies
            log.exception("failed to publish declared registration %s to the coordinator", rid)
    for stale_id in published_ids - new_published:
        try:
            client.remove_registration(stale_id)
        except Exception:  # pragma: no cover - coordinator reachability varies
            log.exception(
                "failed to withdraw stale declared registration %s from the coordinator",
                stale_id,
            )
    return new_published
