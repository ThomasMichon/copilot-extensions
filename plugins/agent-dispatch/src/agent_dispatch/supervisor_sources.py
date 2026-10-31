"""The existing supervisor's declaration/direct/override reconciliation owner."""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import CancelledError, Executor, Future, TimeoutError
from typing import TYPE_CHECKING, Any

from .companion import CompanionIndeterminate
from .pool_source_snapshot import (
    DeclarationRead, PoolSourceSnapshot, SourceObservation, SourceState, collect_pool_sources,
)
from .supervisor_registration import merge_registration_sources, registration_override_ids

if TYPE_CHECKING:
    from .supervisor_daemon_registration_publish import BoundedDeclarationPublisher

log = logging.getLogger("agent-dispatch.supervisor-daemon")


class SupervisorSources:
    """Internal mixin; snapshots add no publisher, allocator or execution owner."""

    if TYPE_CHECKING:
        client: Any
        machine: str | None
        env: str
        clock: Callable[[], float]
        declared_source: Callable[[], Iterable[Any]] | None
        overrides_source: Callable[[], Mapping[str, dict]] | None
        _reconcile_deadline: float | None
        _discovery_futures: dict[str, Future]
        _probe_inflight: set[Future]
        _declaration_publisher: BoundedDeclarationPublisher | None
        _last_declared: list[dict] | None
        _published_declared_ids: set[str]
        _last_merge_diagnostics: tuple[tuple[str, ...], tuple[str, ...]]
        _read_pool: Callable[[], Executor]
        _publish_declarations: Callable[[list[dict]], set[str]]

    def _init_source_collection(self) -> None:
        self._last_store_registrations: list[dict] | None = None
        self._source_attempt = 0
        self._source_observations: dict[str, SourceObservation] = {}
        self._last_pool_source_reasons: tuple[str, ...] | None = None
        self._pool_source_snapshot = collect_pool_sources(
            {}, machine=self.machine, env=self.env, attempt=0, observed_at=self.clock(),
            observations=(), reasons=("collection-pending",),
        )

    def pool_source_snapshot(self) -> PoolSourceSnapshot:
        """Inspect the latest collection without refreshing it or doing I/O."""
        return self._pool_source_snapshot

    def _observe_source(self, source: str, state: SourceState, reason: str = "") -> None:
        self._source_observations[source] = SourceObservation(source, state, reason)

    def _bounded_read(self, key: str, read: Callable[[], Any]) -> Any:
        self._observe_source(key, "pending")
        if self._reconcile_deadline is None:
            try:
                result = read()
            except Exception:
                self._observe_source(key, "unavailable")
                raise
            self._observe_source(key, "fresh")
            return result
        future = self._discovery_futures.get(key)
        delayed = future is not None
        if future is None:
            future = self._read_pool().submit(read)
            self._discovery_futures[key] = future
            self._probe_inflight.add(future)
        try:
            result = future.result(timeout=max(0.0, self._reconcile_deadline - time.monotonic()))
        except Exception:
            self._observe_source(key, "unavailable")
            raise
        else:
            self._observe_source(key, "delayed" if delayed else "fresh")
            return result
        finally:
            if future.done():
                self._discovery_futures.pop(key, None)

    def _store_registrations(self) -> list[dict]:
        publisher = self._declaration_publisher
        epoch = publisher.withdrawal_epoch if publisher is not None else 0

        def read() -> tuple[int, list[dict], list[dict]]:
            registrations = self.client.list_registrations(
                machine=self.machine, env=self.env, include_paused=False,
            )
            return epoch, registrations, copy.deepcopy(registrations)

        try:
            started_epoch, registrations, snapshot = self._bounded_read("registrations", read)
        except (CancelledError, TimeoutError) as exc:
            if self._last_store_registrations is None:
                raise CompanionIndeterminate(
                    "registration discovery pending; no confirmed registry snapshot"
                ) from exc
            self._observe_source("registrations", "cached")
            log.warning(
                "registration discovery unavailable: error_type=%s; retaining confirmed registrations",
                type(exc).__name__,
            )
            return self._last_store_registrations
        if publisher is not None:
            cleared = publisher.observe_registry({reg["id"] for reg in registrations}, started_epoch)
            if cleared is None:
                self._observe_source("registrations", "cached", "prewithdrawal-observation")
                log.warning("registration discovery predates confirmed withdrawal; discarding stale snapshot")
                if self._last_store_registrations is None:
                    raise CompanionIndeterminate("registration discovery has no confirmed current snapshot")
                return self._last_store_registrations
            self._published_declared_ids.difference_update(cleared)
        self._last_store_registrations = snapshot
        return registrations

    def _declared(self) -> list[dict]:
        declared_source = self.declared_source
        if declared_source is None:
            self._observe_source("declarations", "fresh")
            return []
        from .registrar_reconcile import declared_registrations

        def read() -> tuple[list[dict], bool, str]:
            source = declared_source()
            registrations = declared_registrations(list(source), machine=self.machine, env=self.env)
            return (
                registrations, isinstance(source, DeclarationRead) and source.complete,
                source.reason if isinstance(source, DeclarationRead) else "source-completeness-unconfirmed",
            )

        try:
            registrations, complete, reason = self._bounded_read("declarations", read)
        except TimeoutError as exc:
            self._observe_source("declarations", "cached" if self._last_declared is not None else "pending")
            if self._last_declared is None:
                raise CompanionIndeterminate(
                    "declaration discovery pending; no confirmed declaration snapshot"
                ) from exc
            log.warning("declaration discovery remains pending; retaining confirmed declarations")
            return self._last_declared
        except Exception as exc:
            self._observe_source("declarations", "cached" if self._last_declared is not None else "unavailable")
            log.warning(
                "failed to read declared profile set: error_type=%s; retaining confirmed declarations",
                type(exc).__name__,
            )
            if self._last_declared is None:
                raise CompanionIndeterminate(
                    "declaration discovery unavailable; no confirmed declaration snapshot"
                ) from exc
            return self._last_declared
        if not complete:
            self._observe_source("declarations", "unconfirmed", reason)
        self._last_declared = registrations
        self._published_declared_ids = self._publish_declarations(registrations)
        return registrations

    def _overridden_off(self) -> set[str]:
        """Read the same veto once; preserve legacy behavior on failed reads."""
        from . import overrides as ov

        try:
            if self.overrides_source is not None:
                data = self.overrides_source()
                ov.validate_overrides(data)
            else:
                from .config import overrides_path

                data = ov.load_overrides(overrides_path(), strict=True)
        except ov.OverrideObservationError as exc:
            self._observe_source("overrides", "unavailable")
            log.warning("operator override observation malformed; retaining valid legacy vetoes")
            return ov.overridden_off_ids(exc.overrides)
        except Exception as exc:
            self._observe_source("overrides", "unavailable")
            log.warning("failed to read operator overrides; treating as none: error_type=%s", type(exc).__name__)
            return set()
        self._observe_source("overrides", "fresh")
        return ov.overridden_off_ids(data)

    def _effective_registrations(self) -> dict[str, dict]:
        self._source_attempt += 1
        observed_at = self.clock()
        self._source_observations = {
            key: SourceObservation(key, "pending")
            for key in ("declarations", "registrations", "overrides")
        }
        desired: dict[str, dict] = {}
        reasons = ["collection-incomplete"]
        try:
            declared = self._declared()
            regs = self._store_registrations()
            regs = [r for r in regs if r.get("id") not in self._published_declared_ids]
            merged = merge_registration_sources(regs, declared)
            diagnostics = (tuple(merged.deduplicated), tuple(merged.conflicts))
            if diagnostics != self._last_merge_diagnostics:
                for rid in merged.deduplicated:
                    log.info(
                        "equivalent declaration supersedes direct registration %s; "
                        "keeping the direct row dormant for reversible migration", rid,
                    )
                for conflict in merged.conflicts:
                    log.warning("direct/declaration registration conflict: %s; preserving both", conflict)
                self._last_merge_diagnostics = diagnostics
            desired = merged.registrations
            overridden = self._overridden_off()
            for rid in overridden:
                desired.pop(rid, None)
                replacement = merged.replacements.get(rid)
                if replacement:
                    desired.pop(replacement, None)
            for rid, registration in list(desired.items()):
                if registration_override_ids(registration) & overridden:
                    desired.pop(rid, None)
            self._deduplicated = merged.deduplicated
            self._conflicts = merged.conflicts
            reasons = []
            if any(len({r["id"] for r in values}) != len(values) for values in (regs, declared)):
                reasons.append("duplicate-source-registration")
            publisher = self._declaration_publisher
            if publisher is not None and (
                publisher.future is not None or publisher.withdrawn_ids
                or any(record["state"] != "confirmed" for record in publisher.records.values())
            ):
                reasons.append("declaration-publication-unconfirmed")
            return desired
        finally:
            self._pool_source_snapshot = collect_pool_sources(
                desired, machine=self.machine, env=self.env, attempt=self._source_attempt,
                observed_at=observed_at, observations=self._source_observations.values(), reasons=reasons,
            )
            if (not self._pool_source_snapshot.complete
                    and self._pool_source_snapshot.reasons != self._last_pool_source_reasons):
                log.warning("effective pool collection unavailable: reasons=%s", self._pool_source_snapshot.reasons)
            self._last_pool_source_reasons = self._pool_source_snapshot.reasons
