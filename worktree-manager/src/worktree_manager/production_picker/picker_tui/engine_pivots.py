#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import threading

from .engine_helpers import BUILTIN_PIVOTS, PIVOT_PLACEMENT

_UNTRACKED_CONFIG_REVISION = object()

class PickerScreenPivotsMixin:
    def _load_pivots(self, *, scan: bool = True):
        """(Re)scan the manifest registry and rebuild the ordered pivot list.

        Defensive: any discovery failure degrades to the built-ins alone, so a
        bad manifest can never keep the picker from opening. Clamps ``htab`` in
        case a rescan shrank the list.

        ``scan=False`` installs built-ins only (no plugin-dir I/O) so first
        paint is not blocked on the registry.
        """
        selected_key = self._pivot_descriptor_key()
        if not scan:
            registered, descriptors, wt_actions = [], [
                {"label": b, "kind": b.lower(), "pivot": None} for b in BUILTIN_PIVOTS
            ], []
            config_sections = []
        else:
            try:
                from . import pivots as pivots_mod

                report = pivots_mod.scan_pivot_registry()
                pivots_mod.warn_pivot_findings(report)
                registered = report.pivots
                descriptors = pivots_mod.order_pivots(BUILTIN_PIVOTS, registered)
                wt_actions = report.worktree_actions
                config_sections = report.config_sections
            except Exception:
                registered, descriptors, wt_actions = [], [
                    {"label": b, "kind": b.lower(), "pivot": None} for b in BUILTIN_PIVOTS
                ], []
                config_sections = []
        self.registered_pivots = registered
        self.pivots = descriptors
        # Cross-plugin worktree-row actions (a layer augmenting the Worktrees
        # view's Enter sub-menu, e.g. a bridge's "Send message"): discovered
        # from the same manifests, independent of any list pivot (#B).
        self.wt_actions = wt_actions
        # Cross-plugin Configuration sections (a layer augmenting the ⚙
        # Configuration menu, e.g. an SSH or MCP settings home): discovered from
        # the same manifests, independent of any list pivot (#B slice 2).
        self.config_sections = config_sections
        # Tag each pivot with its placement (left cycle / config menu / hidden
        # anchor) so nav machinery can partition them without disturbing the
        # ``order_pivots`` weave that registered ``after`` hints rely on (#1426).
        self._install_config_descriptors()
        self.htabs = [d["label"] for d in self.pivots]
        self._restore_pivot_descriptor(selected_key)
        if self.htab >= len(self.pivots):
            self.htab = 0
        if self._kind() == "config_section" and getattr(self, "_nf_mounted", False):
            self.call_after_refresh(self._load_current_config_section)
    @staticmethod
    def _scan_pivot_payload():
        """Filesystem pivot scan with no widget mutation (safe off the UI thread)."""
        try:
            from . import pivots as pivots_mod

            report = pivots_mod.scan_pivot_registry()
            pivots_mod.warn_pivot_findings(report)
            return (
                report.pivots,
                pivots_mod.order_pivots(BUILTIN_PIVOTS, report.pivots),
                report.worktree_actions,
                report.config_sections,
            )
        except Exception:
            return None
    def _install_pivot_payload(self, payload):
        selected_key = self._pivot_descriptor_key()
        if payload is None:
            self._load_pivots(scan=False)
            return
        registered, descriptors, wt_actions, config_sections = payload
        self.registered_pivots = registered
        self.pivots = descriptors
        self.wt_actions = wt_actions
        self.config_sections = config_sections
        self._install_config_descriptors()
        self.htabs = [d["label"] for d in self.pivots]
        self._restore_pivot_descriptor(selected_key)
        if self.htab >= len(self.pivots):
            self.htab = 0
        if self._kind() == "config_section" and getattr(self, "_nf_mounted", False):
            self.call_after_refresh(self._load_current_config_section)
    def _install_config_descriptors(self):
        self._config_section_states = {}
        for descriptor in self.pivots:
            reg = descriptor.get("pivot")
            descriptor["placement"] = (
                reg.placement if reg is not None
                else PIVOT_PLACEMENT.get(descriptor["kind"], "left")
            )
        for section in self.config_sections:
            self.pivots.append({
                "label": section.label, "kind": "config_section", "pivot": None,
                "placement": "config", "section": section,
            })
    def _pivot_descriptor_key(self, index=None):
        descriptors = getattr(self, "pivots", ())
        index = getattr(self, "htab", 0) if index is None else index
        if not 0 <= index < len(descriptors):
            return None
        descriptor = descriptors[index]
        reg = descriptor.get("pivot")
        section = descriptor.get("section")
        return (
            descriptor["kind"],
            reg.name if reg is not None else (
                (section.source, section.key) if section else descriptor["label"]
            ),
        )
    def _restore_pivot_descriptor(self, key):
        if key is None:
            return
        match = next(
            (index for index in range(len(self.pivots))
             if self._pivot_descriptor_key(index) == key), None,
        )
        if match is not None:
            self.htab = match
        else:
            self.htab = next(
                (index for index, descriptor in enumerate(self.pivots)
                 if descriptor["kind"] == "profiles"), 0,
            )
            self.debug = f"Configuration/navigation target unavailable: {key[1]}"
    def _left_pivots(self):
        """Indices of pivots on the left cycle (◀▶ / [ ])."""
        return [i for i, d in enumerate(self.pivots)
                if d.get("placement", "left") == "left"]
    def _config_pivots(self):
        """Indices of pivots hosted under the ⚙ Configuration menu (#1426)."""
        return sorted(
            (i for i, d in enumerate(self.pivots) if d.get("placement") == "config"),
            key=lambda index: self.pivots[index]["kind"] != "profiles",
        )
    def _kind(self, idx=None):
        """The current pivot's kind: 'worktrees' | 'maintenance' | 'profiles' |
        'registered'. Built-in dispatch keys off this, never a magic index."""
        i = self.htab if idx is None else idx
        if 0 <= i < len(self.pivots):
            return self.pivots[i]["kind"]
        return "worktrees"
    def _reg_pivot(self):
        """The RegisteredPivot for the current pivot, or None for a built-in."""
        if 0 <= self.htab < len(self.pivots):
            return self.pivots[self.htab]["pivot"]
        return None
    def _pivot_machine(self):
        """The machine name the registered pivot's ``list``/actions run against:
        the selected machine sub-tab, or the local machine when 'All' is
        selected. Provider-backed tabs return ``None`` because a registered
        machine pivot cannot be routed through a provider source.

        This returns the tab's *display* name (``machines.yaml`` ``display_name``)
        -- use it for human-facing status lines. For the value handed to a
        contributed pivot's CLI, use :meth:`_pivot_machine_id`."""
        label, m, _e, _ok = self.machines[self.machine_idx]
        tab = self.source_tabs[self.machine_idx] if self.source_tabs else {}
        if m is not None and tab.get("source_kind") in (None, "machine-ssh"):
            return m
        if tab.get("source_kind") not in (None, "machine-ssh"):
            return None
        loc = self.machines[self.local_index()] if self.machines else (None, None, None, None)
        return loc[1]
    def _machine_key_map(self):
        """Cached ``machines.yaml`` ``display_name -> registry key`` map.

        The registry key is a machine's canonical identity (lowercase; it doubles
        as the SSH-alias base) -- the value ``agent-worktrees get machine`` returns
        and that other multi-machine system tools (agent-dispatch, agent-bridge) match against.
        Tab labels carry the *display* name, so registered-pivot commands need
        this translation.

        NEVER blocks the calling thread. Once :meth:`_prewarm_machine_key_map`
        (started from ``_prime_setup_reload()``/``_setup_live_pivots``) has resolved
        ``self._mkey_map``, this returns that cached map. Until then it
        degrades to ``{}`` -- harmless, since every caller (``_pivot_machine_id``
        etc.) already tolerates an empty/unresolved map by falling back to the
        display name -- rather than calling the underlying
        :func:`data_ssh.machine_key_map` (and its ``load_config()``) directly:
        that call is UNCACHED and, on a machine with many registered repos, was
        profiled well into multiple seconds -- a real, reproduced UI freeze the
        first time an operator switched onto any registered pivot, even with the
        background prewarm already given a head start. Also kicks the prewarm
        here as a safety net (idempotent/no-op if already running or resolved),
        in case something calls this before the setup-reload path ever ran it."""
        cached = getattr(self, "_mkey_map", None)
        revision = self._machine_key_map_revision()
        if cached is not None and self._machine_key_map_current(revision):
            return cached
        self._prewarm_machine_key_map()
        if getattr(self, "_mkey_map_error", None) and not getattr(self, "_busy_label", None):
            self.debug = f"machine mapping unavailable: {self._mkey_map_error}"
        return {}
    def _machine_key_map_revision(self):
        session = getattr(self, "_config_cache_session", None)
        return getattr(session, "revision", _UNTRACKED_CONFIG_REVISION)
    def _machine_key_map_current(self, revision):
        return revision is _UNTRACKED_CONFIG_REVISION or (
            revision is not None and revision is getattr(self, "_mkey_map_revision", None)
        )
    def _invalidate_machine_key_map(self):
        self._mkey_map = None
        self._mkey_map_revision = None
        self._mkey_map_failed = False
        self._mkey_map_error = None
        self._mkey_map_epoch = getattr(self, "_mkey_map_epoch", 0) + 1
    def _prewarm_machine_key_map(self):
        """Compute ``machines.yaml``'s display-name -> registry-key map on a
        background thread and cache it into ``self._mkey_map`` ahead of time.

        ``_machine_key_map()``'s first real call -- triggered synchronously on
        the render thread the first time an operator switches onto ANY
        registered pivot -- used to call ``agent_worktrees.config.load_config()``
        directly, which is UNCACHED at that layer: on a machine with many
        registered repos (the control-plane related-PR discovery
        ``load_config()`` performs walks every repo's anchor), this profiled
        at several seconds on EVERY call, not just the first -- on top of the
        ~100ms ``data_ssh`` import cost ``prewarm_optional_modules`` already
        avoids. That import prewarm alone does not help here: it only avoids
        re-paying the module IMPORT, not the (uncached) function CALL.
        ``_machine_key_map()`` itself now never blocks on this call at all
        (see its own docstring); this method is what eventually fills in the
        real, canonical map once it lands, so a pivot's ``{machine}`` value
        upgrades from the display-name fallback to the registry key in place.
        A no-op if the map is already resolved, or a previous call to this
        method is still in flight (guarded by ``self._mkey_map_inflight``) --
        callable repeatedly and cheaply from ``_machine_key_map()`` itself.

        Uses raw ``threading.Thread`` + :meth:`_apply_from_worker` rather
        than :meth:`_run_bg`: this is a quiet infra prewarm with no
        user-facing label/spinner, and (unlike ``_run_bg``, which resolves
        ``self.app`` eagerly) ``_apply_from_worker`` degrades safely when
        called outside a mounted app -- e.g. a unit test that constructs a
        bare ``PickerScreen`` and calls ``setup_sync_for_tests()`` directly."""
        revision = self._machine_key_map_revision()
        if getattr(self, "_mkey_map", None) is not None and self._machine_key_map_current(revision):
            return
        self._mkey_map = None
        if getattr(self, "_mkey_map_failed", False) and revision is getattr(
            self, "_mkey_map_failed_revision", None,
        ):
            return
        if getattr(self, "_mkey_map_inflight", False):
            return
        self._mkey_map_inflight = True
        source = self.src
        setup_epoch = getattr(self, "_setup_epoch", None)
        map_epoch = getattr(self, "_mkey_map_epoch", 0)

        def _work():
            prepared_revision = _UNTRACKED_CONFIG_REVISION
            try:
                from . import data_ssh

                with self._load_config_cache_scope() as session:
                    prepared_revision = getattr(session, "revision", _UNTRACKED_CONFIG_REVISION)
                    mapping = data_ssh.machine_key_map()
                    check = getattr(session, "raise_for_errors", None)
                    if callable(check):
                        check()
                    return mapping, prepared_revision, None
            except Exception as exc:
                return None, prepared_revision, str(exc)

        def _apply(result):
            self._mkey_map_inflight = False
            mapping, prepared_revision, error = result
            if (
                self.src is not source
                or getattr(self, "_setup_epoch", None) != setup_epoch
                or getattr(self, "_mkey_map_epoch", 0) != map_epoch
                or prepared_revision is not self._machine_key_map_revision()
            ):
                return
            self._mkey_map_failed = error is not None
            self._mkey_map_failed_revision = prepared_revision if error else None
            self._mkey_map_error = error
            if error is None:
                self._mkey_map = mapping
                self._mkey_map_revision = prepared_revision
            self.refresh()


        def _worker():
            mapping = _work()
            self._apply_from_worker(lambda: _apply(mapping))

        threading.Thread(
            target=_worker, name="machine-key-map-prewarm", daemon=True,
        ).start()
    def _pivot_machine_id(self):
        """The **canonical machine identity** (``machines.yaml`` registry key) for
        the current registered-pivot scope -- the value a contributed pivot's CLI
        should receive as ``{machine}``.

        A registered pivot's ``{machine}`` names a *machine identity*, not a tab
        label: agent-dispatch resolves the local machine and its SSH aliases by
        the registry key, so handing it the display name (``Anomalous-Potato`` vs the
        identity ``anomalous-potato``) would make it treat the local machine as a
        remote peer or miss a peer's lowercase ``Host`` block. Falls back to the
        display name when the roster can't be read (harmless: a downstream that
        casefolds still matches)."""
        display = self._pivot_machine()
        if not display:
            return display
        return self._machine_key_map().get(display, display)
    def _pivot_scope_key(self):
        """The runtime cache key + ``{machine}`` value for the current registered
        pivot. Machine-scoped pivots key off the selected machine identity; an
        **account-scoped** pivot (a cross-machine shared resource like CodeSpaces)
        keys off a single constant so its ``list`` runs once, not per machine."""
        reg = self._reg_pivot()
        if reg is not None and getattr(reg, "account_scoped", False):
            return ""
        return self._pivot_machine_id()
    def _pivot_runtime(self, reg):
        """Lazily build (and cache) the background runtime for a registered
        pivot -- it shells out to the pivot's ``list`` CLI off the render path."""
        rt = self._pivot_runtimes.get(reg.name)
        if rt is None:
            from . import tasks

            rt = tasks.RegisteredPivotRuntime(reg)
            self._pivot_runtimes[reg.name] = rt
        return rt
