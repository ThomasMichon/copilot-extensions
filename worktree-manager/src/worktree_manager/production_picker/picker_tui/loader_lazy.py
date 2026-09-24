#!/usr/bin/env python3
"""Ping-first, load-on-nav machine tabs for :class:`LiveLoader`
(picker-lazy-per-machine-loading).

Split out of ``data_ssh.py`` (module-size guard) as a mixin: ``LiveLoader``
composes :class:`LazyLoadMixin` alongside its own core load machinery,
following this package's existing ``PickerScreen*Mixin`` convention (see
``engine.py``'s composition of the ``engine_*.py`` mixins) rather than
absorbing this feature's growth into an already-baselined module.

A machine tab the operator hasn't actually looked at yet is deferred behind a
cheap ``ssh ... exit 0`` connectivity probe instead of the full
``list --classify`` listing (and the git-classification load that drives on
the remote): :meth:`LazyLoadMixin.ensure_loaded` / :meth:`ensure_all_loaded`
promote one out of that ping-only set the moment its tab (or "All") becomes
current, and :meth:`cancel_source` walks it back when the operator navigates
away again.
"""
from __future__ import annotations

import os
import threading

_PING_TIMEOUT_SECS = 5
# A bare reachability probe -- deliberately NOT the full list/classify hardening
# ``data_ssh._SSH_HARDENING`` uses: the picker fans a probe out to every
# not-yet-viewed machine tab on every launch, so it needs to fail fast on an
# offline/hibernating box rather than waiting out the full ConnectTimeout.
# ``exit 0`` never touches the remote agent-worktrees at all -- this is pure
# SSH-layer connectivity, cheaper than even the fastest real list call.
_PING_HARDENING = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=4",
    "-o", "ServerAliveInterval=4",
    "-o", "ServerAliveCountMax=1",
)


def _ping_argv(alias: str) -> list[str]:
    """``ssh`` argv for a fast connectivity-only probe (no listing)."""
    return ["ssh", *_PING_HARDENING, alias, "exit", "0"]


def _lazy_loading_enabled() -> bool:
    """Whether `start()` defers a non-focused machine's full listing behind a
    cheap connectivity ping instead of loading it immediately (picker-lazy-
    per-machine-loading).

    On by default -- unlike ``data_ssh._stream_enabled``'s fleet-rollout gate,
    this is pure picker-local behavior with no remote-version dependency (the
    ping is a bare ``ssh ... exit 0``, and `ensure_loaded` reuses the exact
    existing load path). Kept behind an escape hatch anyway, matching this
    module's own rollback-switch convention: set
    ``AGENT_WORKTREES_PICKER_LAZY_LOAD=0`` (any of 0/false/no/off) to load
    every ready machine up front again."""
    return os.environ.get(
        "AGENT_WORKTREES_PICKER_LAZY_LOAD", "").strip().lower() not in (
        "0", "false", "no", "off")


class LazyLoadMixin:
    """Ping-then-promote machine-tab loading, mixed into ``LiveLoader``.

    Relies on the host class's own core state (``self._lock``,
    ``self._sources``, ``self._state``, ``self._records``, ``self._error``,
    ``self._gen``, ``self._pinged_only``, ``self._procs_by_source``,
    ``self._procs_lock``, ``self._active_source``, ``self._cancelled``) and
    core methods (``self._spawn``, ``self._load_one``).
    """

    @staticmethod
    def _matches_focus(source, focus_keys) -> bool:
        return (
            source.key in focus_keys
            or source.cache_key in focus_keys
            or (source.source_id and source.source_id in focus_keys)
            or (source.machine, source.env) in focus_keys
        )

    def _ping_one(self, source, gen: int | None = None):
        """Cheap connectivity-only probe standing in for a deferred full load.

        Sets the SAME ``ready``/``failed`` state the operator already reads as
        the tab's spinner -> checkmark/X (``ready`` with empty records renders
        identically to a machine that genuinely has no worktrees) -- but never
        runs the remote's ``list``/git-classification. ``ensure_loaded``
        replaces this with the real listing once the tab is actually viewed.

        ``gen`` is the generation captured by the caller (``start()``) when
        THIS ping was dispatched. ``cancel_source`` bumps the generation when
        it re-arms a source into ``_pinged_only`` (nav-away then back before
        the original probe returned); without this check a canceled probe
        that later comes back nonzero would find its (now stale) key still
        a member of ``_pinged_only`` and wrongly mark the re-armed source
        failed, silently defeating the next nav-back's retry. Defaults to the
        source's current generation when omitted (a direct/test call with no
        cancellation in play)."""
        if gen is None:
            gen = self._gen.get(source.cache_key, 0)
        try:
            if self._cancelled.is_set():
                return
            self._active_source.key = source.cache_key
            try:
                proc = self._spawn(_ping_argv(source.alias), _PING_TIMEOUT_SECS)
            finally:
                self._active_source.key = None
        except Exception as exc:
            with self._lock:
                if (
                    source.cache_key in self._pinged_only
                    and self._gen.get(source.cache_key, 0) == gen
                ):
                    # A failed probe must NOT stay eligible for automatic
                    # promotion: ensure_loaded()/ensure_all_loaded() only
                    # check set membership, so leaving the key here would
                    # have the operator's very next nav onto (or "All" past)
                    # this X-marked tab silently kick off the full,
                    # expensive listing against a host we just confirmed is
                    # unreachable -- contradicting the connect-spinner ✗'s
                    # whole point. `reload_source`/an explicit 'r' remains
                    # the escape hatch if the operator believes it's back.
                    self._pinged_only.discard(source.cache_key)
                    self._state[source.cache_key] = "failed"
                    self._error[source.cache_key] = (
                        str(exc).strip() or type(exc).__name__
                    )
            return
        with self._lock:
            # A nav-driven ensure_loaded() may have already promoted this
            # source (and possibly completed a real load) while the ping was
            # in flight -- never clobber that with a stale ping result. Same
            # for a cancel_source() that re-armed it under a NEW generation
            # (the gen check) while this now-superseded probe was in flight.
            if (
                source.cache_key not in self._pinged_only
                or self._gen.get(source.cache_key, 0) != gen
            ):
                return
            if proc.returncode == 0:
                self._records[source.cache_key] = []
                self._state[source.cache_key] = "ready"
                self._error[source.cache_key] = ""
            else:
                # Same reasoning as the exception branch above: an
                # unreachable ping must not stay promotable.
                self._pinged_only.discard(source.cache_key)
                self._state[source.cache_key] = "failed"
                self._error[source.cache_key] = (
                    (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
                    or ["unreachable"]
                )[0]

    def ensure_loaded(self, key) -> bool:
        """Promote one ping-only source to a real listing (picker-lazy-
        per-machine-loading): the operator just navigated onto its tab.

        No-op (returns ``False``) if ``key`` doesn't match a ping-only
        source -- already loaded, still loading, unreachable (a failed ping
        already removed itself from consideration -- see `_ping_one`), or
        unknown are all left exactly as they are. Flips the tab back to its
        connect spinner while the real fetch runs, exactly like a fresh
        `start()`.
        """
        with self._lock:
            src = next(
                (s for s in self._sources if self._matches_focus(s, {key})),
                None,
            )
            if src is None or src.cache_key not in self._pinged_only:
                return False
            self._pinged_only.discard(src.cache_key)
            self._state[src.cache_key] = "loading"
            self._records[src.cache_key] = []
            gen = self._gen.get(src.cache_key, 0)
        threading.Thread(
            target=self._load_one, args=(src, gen),
            name=f"lazyload-{src.machine}-{src.env}", daemon=True,
        ).start()
        return True

    def ensure_all_loaded(self) -> int:
        """``ensure_loaded`` every currently ping-only source (the "All" tab
        needs every machine's real rows, not just the one(s) in single-machine
        focus). Returns the number of loads actually started."""
        with self._lock:
            keys = list(self._pinged_only)
        return sum(1 for k in keys if self.ensure_loaded(k))

    def cancel_source(self, key) -> bool:
        """Stop this source's in-flight fetch (nav-away) without disturbing
        an already-resolved source's last-good rows.

        Unlike :meth:`cancel` (whole-picker teardown), this targets one
        source, and does two DIFFERENT things depending on whether it has
        committed a real result yet -- **not** on how it got started (a bare
        `ensure_loaded` promotion vs. one of `start()`'s eagerly (focus_keys)
        loaded sources vs. a background repoll/reconcile of an
        already-resolved source all funnel through the same one check,
        ``self._records`` itself, so a remote's own two-phase load can never
        be caught mid-flight with its already-committed phase-1 fast rows
        wrongly discarded just because its phase-2 classify pass is still
        running):

        - **No records committed yet, and not a settled failure** (initial
          connect, or a bare promotion that hasn't resolved): fully reset it
          -- kill any spawned child, bump the generation (so a result the
          killed attempt eventually produces can never land), and put it
          back in ``_pinged_only`` with a ``ready``/no-records placeholder,
          exactly like a successful ping -- so navigating back onto the tab
          (`ensure_loaded`) starts a genuinely fresh load instead of finding
          nothing to promote and leaving the tab stuck on its cancelled,
          pre-first-result state. Acts even when no child has spawned yet (a
          race between the promoting/starting call releasing its lock and
          the new thread reaching `_spawn`): the generation bump alone
          discards whatever that orphaned attempt eventually produces, and
          the actual SSH connection -- already unavoidable once dispatched
          -- simply becomes wasted, unobserved work rather than a
          correctness risk.
        - **A settled failure** (a failed ping -- `_ping_one` already
          confirmed the machine unreachable and pulled it out of
          ``_pinged_only`` -- or a fully-failed real load, neither of which
          ever got any rows): state/records are left exactly as they are.
          Re-arming this into ``_pinged_only`` would let the operator's mere
          nav-away-then-back silently re-promote a confirmed-unreachable (or
          just-failed) source into another full listing attempt, defeating
          the X marker; an explicit `reload_source`/'r' remains the escape
          hatch to actually retry.
        - **Already has committed rows** (a remote's two-phase fast rows
          already landed, or any prior successful/last-good load) and a
          further refresh (phase 2, `repoll_silent`, `reconcile_remote_prs`,
          `reload_source`) is in flight: only bump the generation and kill
          its child (stopping the wasted remote work); state/records are
          left exactly as they are, so the tab keeps showing its last-good
          rows, not a reset connect spinner.

        Returns ``False`` (a harmless no-op) when there is neither
        resettable in-flight work nor a tracked child process for this
        source -- nothing to stop.
        """
        # Deferred import: `data_ssh` composes this mixin into `LiveLoader`,
        # so a top-level import here would be circular (see this package's
        # existing `from . import data_ssh` deferred-import convention).
        from .data_ssh import _kill_proc_tree
        with self._lock:
            src = next(
                (s for s in self._sources if self._matches_focus(s, {key})),
                None,
            )
            if src is None:
                return False
            with self._procs_lock:
                procs = list(self._procs_by_source.get(src.cache_key, ()))
            resettable = (
                not self._records.get(src.cache_key)
                and self._state.get(src.cache_key) != "failed"
            )
            if not procs and not resettable:
                return False
            self._gen[src.cache_key] = self._gen.get(src.cache_key, 0) + 1
            if resettable:
                self._pinged_only.add(src.cache_key)
                self._state[src.cache_key] = "ready"
                self._records[src.cache_key] = []
        for p in procs:
            _kill_proc_tree(p)
        return True
