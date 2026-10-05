"""Declarative provider (``providers.d``) manifest discovery for
:class:`agent_registry_resolver.AgentResolver` -- extracted to keep that
module under the repository's module-size cap (CONTRIBUTING.md's
componentization convention, mirroring the sibling ``agent_registry_cache.py``
/ ``agent_roster_discovery.py`` split).

Owns :class:`DiscoveryResult` and the scan/apply split
(:meth:`_ProviderDiscoveryMixin.refresh_provider_resolvers` /
``_scan_provider_report`` / ``_apply_provider_report``): the slow,
subprocess-reaching manifest scan (safe to run off the event loop, e.g. from
:class:`agent_registry_cache.AgentRosterCache`'s worker thread) versus the
fast, synchronous, event-loop-only registry reconciliation. Mixed into
``AgentResolver`` itself, which owns all the state these methods read and
write (``_provider_entries``, ``_apply_generation``, etc. -- see that
class's own ``__init__``) and the namespace-registration methods
(``register_namespace_resolver`` / ``unregister_namespace_resolver``) this
module's own docstrings cross-reference.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dropin_registry import Finding

from .agent_registry_namespace import (
    CliNamespaceResolver,
    RestrictedCliNamespaceResolver,
)
from .provider_sources import scan_provider_registry

if TYPE_CHECKING:
    from .provider_sources import ProviderRegistryReport

log = logging.getLogger("agent-bridge")


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of one :meth:`AgentResolver.refresh_provider_resolvers` scan.

    Distinguishes a scan-level exception (``raised``), a scan that completed
    but left one or more per-manifest construction failures
    (``failed_namespaces``), and a genuinely clean pass (``ok`` and no
    failures). :class:`AgentRosterCache` (pivot-streaming-transport Phase
    3b) only advances its own discovery-generation freshness on a clean
    pass -- a stale discovery generation (this scan never clean, or past its
    own freshness deadline) means additions/removals/replacements are
    unknown, independent of whether already-known namespaces still retain
    their own last-known-good cache entries.
    """

    ok: bool
    raised: bool = False
    failed_namespaces: list[str] = field(default_factory=list)


class _ProviderDiscoveryMixin:
    """Declarative-provider-discovery half of :class:`AgentResolver`."""

    def refresh_provider_resolvers(self, *, force: bool = False) -> "DiscoveryResult":
        """Register namespace resolvers from the ``providers.d`` manifest registry.

        Returns a :class:`DiscoveryResult` distinguishing a scan-level
        exception, a partial per-manifest construction failure, and a
        genuinely clean pass -- :class:`AgentRosterCache` (3b) uses this to
        decide whether its own discovery-generation freshness should
        advance. A throttled, TTL-skipped call (``force=False`` within the
        internal scan TTL) reports ``ok=True`` with no failures: nothing was
        attempted, so nothing can have failed.

        Split into :meth:`_scan_provider_report` (the slow, subprocess-
        reaching half -- safe to run off the event loop) and
        :meth:`_apply_provider_report` (the fast in-memory reconciliation,
        which must stay on the event loop) so :class:`AgentRosterCache` can
        run the former on a worker thread. Both this synchronous entry
        point and the cache's own off-thread scan share the
        ``_apply_generation`` guard (see :meth:`_apply_provider_report`), so
        a stale report is rejected regardless of which caller's scan it
        came from."""
        outcome = self._scan_provider_report(force=force)
        if isinstance(outcome, DiscoveryResult):
            return outcome
        report, start_generation = outcome
        return self._apply_provider_report(report, start_generation)

    def _scan_provider_report(
        self, *, force: bool,
    ) -> "DiscoveryResult | tuple[ProviderRegistryReport, int]":
        """Run (or TTL-skip) the manifest scan itself. Returns a
        :class:`DiscoveryResult` directly for the two cases needing no
        further reconciliation (throttled/TTL-skipped, or the scan itself
        raised); otherwise the raw scan report paired with the
        ``_apply_generation`` value captured *before* the scan began, for
        :meth:`_apply_provider_report` to reconcile against the live
        registry and gate against a newer apply having superseded it.
        Touches only ``_provider_entries`` -- never ``_namespace_
        resolvers`` -- so it is safe to run from a worker thread.

        Does *not* advance ``_provider_scan_ts`` on a genuine attempt
        (only on an already-throttled read, or a raised scan with nothing
        left to apply): stamping early would let a concurrent, non-forced
        caller treat an in-progress scan as complete and read stale data.
        A raised scan only stamps once the generation hasn't moved *and*
        no sibling attempt remains in flight (``_scan_attempts_in_flight``)
        -- distinct from "nothing else ran," which would otherwise mask a
        pending sibling's result behind a falsely-fresh stamp."""
        now = time.monotonic()
        if not force and (now - self._provider_scan_ts) < self._provider_scan_ttl:
            return DiscoveryResult(ok=True)
        with self._scan_lock:
            start_generation = self._apply_generation
            self._scan_attempts_in_flight += 1

        try:
            report = scan_provider_registry(previous=self._provider_entries)
        except Exception:
            log.warning("Provider manifest discovery failed", exc_info=True)
            with self._scan_lock:
                self._scan_attempts_in_flight -= 1
                should_stamp = (
                    start_generation == self._apply_generation
                    and self._scan_attempts_in_flight == 0
                )
            if should_stamp:
                self._provider_scan_ts = time.monotonic()
            return DiscoveryResult(ok=False, raised=True)
        with self._scan_lock:
            self._scan_attempts_in_flight -= 1
        return report, start_generation

    def _apply_provider_report(
        self, report: "ProviderRegistryReport", start_generation: int,
    ) -> "DiscoveryResult":
        """Reconcile the live namespace-resolver registry against a report
        already produced by :meth:`_scan_provider_report`. Always
        synchronous and event-loop-only: every mutation happens in one
        uninterrupted (no ``await``) pass, so a concurrent async reader can
        only ever observe the registry fully before or fully after this
        call.

        ``start_generation`` is the ``_apply_generation`` captured when
        this report's scan began -- if another caller's apply has already
        advanced ``_apply_generation`` past it (their scan started later
        but finished first), this report is stale and would roll the
        registry back; it is rejected outright, never partially applied,
        and never advances ``_apply_generation``/``_provider_scan_ts`` --
        freshness is governed by whichever other apply already won."""
        if start_generation != self._apply_generation:
            log.warning(
                "providers.d: discarding a superseded scan (started at "
                "apply-generation %d, now %d) -- a newer report already "
                "applied while this one was still in flight",
                start_generation, self._apply_generation,
            )
            return DiscoveryResult(ok=False)
        findings = list(report.findings)
        for manifest in report.manifests.values():
            if (
                manifest.namespace in self._namespace_resolvers
                and manifest.namespace not in self._provider_namespaces
            ):
                findings.append(
                    Finding(
                        registry="providers.d",
                        entry=manifest.source_path,
                        status="inactive",
                        reason="duplicate",
                        target=manifest.namespace,
                        owner=manifest.plugin,
                        remedy="Remove the provider entry or rename its namespace.",
                        detail="namespace conflicts with a built-in resolver",
                    )
                )
        warning_batch = self._provider_warning_tracker.select(findings)
        for finding in warning_batch.emitted:
            target = f" target={finding.target}" if finding.target else ""
            log.warning(
                "%s: %s (%s)%s; run `agent-bridge doctor`",
                finding.registry,
                finding.entry,
                finding.reason,
                target,
            )
        if warning_batch.suppressed:
            log.warning(
                "providers.d: %d additional finding(s) suppressed; "
                "run `agent-bridge doctor`",
                warning_batch.suppressed,
            )
        if warning_batch.recovered:
            log.info(
                "providers.d: %d prior finding(s) recovered",
                warning_batch.recovered,
            )

        desired = dict(report.manifests)
        desired_namespaces = set(desired)
        for namespace in sorted(self._provider_namespaces - desired_namespaces):
            self.unregister_namespace_resolver(namespace)
            self._provider_manifests.pop(namespace, None)

        failed_namespaces: list[str] = []
        for manifest in desired.values():
            namespace = manifest.namespace
            current = self._provider_manifests.get(namespace)
            if current == manifest and namespace in self._provider_namespaces:
                continue
            if namespace not in self._provider_namespaces and namespace in self._namespace_resolvers:
                continue
            cls = RestrictedCliNamespaceResolver if manifest.restricted else CliNamespaceResolver
            try:
                new_resolver = cls(
                    manifest.namespace,
                    manifest.command[0],
                    command=list(manifest.command),
                )
            except Exception:
                # Construction failed -- a same-namespace *replacement*
                # leaves the previous resolver (and whatever cache entry it
                # backs) registered and authoritative rather than creating
                # an immediate gap (3b design: "transactional replacement").
                log.warning(
                    "Failed to construct '%s:' resolver from %s",
                    manifest.namespace,
                    manifest.source_path,
                    exc_info=True,
                )
                failed_namespaces.append(namespace)
                continue
            # Construction already succeeded above -- only now unregister any
            # prior resolver for this namespace, so a replacement never has a
            # window where the namespace is registered to neither resolver.
            if namespace in self._provider_namespaces:
                self.unregister_namespace_resolver(namespace)
                self._provider_namespaces.discard(namespace)
                self._provider_manifests.pop(namespace, None)
            try:
                self.register_namespace_resolver(new_resolver)
            except ValueError:
                log.warning(
                    "Namespace '%s:' already registered; skipping provider "
                    "manifest swap from %s",
                    namespace,
                    manifest.source_path,
                )
                failed_namespaces.append(namespace)
                continue
            self._provider_namespaces.add(namespace)
            self._provider_manifests[namespace] = manifest
            log.info(
                "Registered %s: namespace resolver from %s",
                namespace,
                manifest.source_path,
            )
        self._provider_entries = dict(report.entries)
        # An "indeterminate" finding (scan_provider_registry() deliberately
        # retaining a previous manifest across a transient activation/
        # command-access evidence gap, provider_sources.py's own
        # ScanAuthority.INDETERMINATE path) means this scan cannot confirm
        # the resolved entry set reflects the true current intent -- a
        # resolver set that's actually gone stale must never be reported as
        # a clean pass just because nothing outright failed or raised.
        indeterminate = any(finding.status == "indeterminate" for finding in report.findings)
        # Only now -- actually applied -- is it safe to advance both the
        # generation (so a stale, still-in-flight scan is rejected rather
        # than rolling this state back) and the scan-TTL timestamp (so a
        # concurrent, non-forced caller's throttle check reflects reality).
        self._apply_generation += 1
        self._provider_scan_ts = time.monotonic()
        return DiscoveryResult(
            ok=not failed_namespaces and not indeterminate,
            failed_namespaces=failed_namespaces,
        )
