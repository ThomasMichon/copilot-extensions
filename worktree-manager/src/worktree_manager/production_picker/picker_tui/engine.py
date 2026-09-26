#!/usr/bin/env python3
"""Textual rendering engine for the overhauled Worktree Picker.

Ported from the design prototype (test-chamber effort
``worktree-picker-tty-overhaul``). The engine is data-source agnostic: a
``PickerScreen`` is handed a *source* object exposing the same surface the
prototype's ``mockdata``/``livedata`` did (``LOCAL``, ``LOCAL_LABEL``,
``machines()``, ``load()``, ``bucket()``, ``for_machine()``). Production wires
a real source (``data_local`` / SSH); tests can pass a fixture source.

Keys:
  ↑/↓ move · ←/→ switch tab · Enter activate · Tab row sub-menu
  ⇧Tab cycle views · / filter (stub) · r refresh · ? help · q/Esc back-or-quit
"""
from __future__ import annotations

import threading
import time

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget

from .engine_dialogs import (
    CfgMenuScreen,
    MaintMenuScreen,
    ProfConfirmScreen,
    QuitConfirmScreen,
    ScopeDlgScreen,
    SubMenuScreen,
    TaskMenuScreen,
    WtDetailsScreen,
)
from .engine_focus import FocusGroup, _ScopeSelectionList
from .engine_helpers import (
    ACTION_DESC,
    ACTIVE_SPECS,
    BUILTIN_PIVOTS,
    BUTTON_SETS,
    C_STATE,
    CLEAN_SPECS,
    HTABS,
    IDLE_TIMEOUT_SECS,
    LIST_SPECS,
    MAINT_ACTION_DESC,
    MAINT_GROUP_ORDER,
    PAD,
    POLL_SECS,
    PIVOT_PLACEMENT,
    PROF_SPECS,
    VERSION,
    VRow,
    _DEFAULT_HOST_COLS,
    _DEFAULT_TARGET_ENVS,
    _idle_timeout_secs,
    _NO_FLEX_COLUMN,
    _palette_style,
    _poll_secs,
    _register_shift_enter_key,
    _resolve_mock_mode,
    _resolve_version,
    _size_mb,
    canonical_key,
    fit,
    header_text,
    row_text,
    target_rows,
)
from .engine_input import PickerScreenInputMixin
from .engine_live_screens import MsgViewScreen, ProgressScreen
from .engine_loading import PickerScreenLoadingMixin
from .engine_maintenance_actions import PickerScreenMaintenanceActionsMixin
from .engine_model import PickerScreenModelMixin
from .engine_pivot_actions import PickerScreenPivotActionsMixin
from .engine_pivots import PickerScreenPivotsMixin
from .engine_profiles_view import ProfilesView
from .engine_regions import (
    _FocusRegion,
    _PickerButtons,
    _PickerMachine,
    _PickerNativeData,
    _PickerPivots,
    _PickerSegment,
    _PickerStickyHeader,
)
from .engine_rendering import PickerScreenRenderingMixin
from .engine_runtime import PickerScreenRuntimeMixin
from .engine_selection import PickerScreenSelectionMixin
from .engine_views import MaintenanceView, TasksView, WorktreesView
from .engine_worker_actions import PickerScreenWorkerActionsMixin
from .engine_worktree_actions import PickerScreenWorktreeActionsMixin
from .listview import ListView
from .selection import ListSelection
from .steering import (
    PivotCardScreen,
    PivotFormScreen,
    ResetConfirmScreen,  # noqa: F401 -- re-export for tests
    SteerButtonRow,  # noqa: F401 -- re-export for tests
    SubmitErrorScreen,
    _AutoExpandTextArea,  # noqa: F401 -- re-export for tests
    _normalize_form_fields,
    _steer_draft_path,
    _STEER_DRAFTS_ENV,  # noqa: F401 -- re-export for tests
)

__all__ = [
    "ACTION_DESC",
    "ACTIVE_SPECS",
    "BUILTIN_PIVOTS",
    "BUTTON_SETS",
    "CLEAN_SPECS",
    "C_STATE",
    "CfgMenuScreen",
    "FocusGroup",
    "HTABS",
    "IDLE_TIMEOUT_SECS",
    "LIST_SPECS",
    "MAINT_ACTION_DESC",
    "MAINT_GROUP_ORDER",
    "MaintMenuScreen",
    "MsgViewScreen",
    "PAD",
    "PIVOT_PLACEMENT",
    "POLL_SECS",
    "PROF_SPECS",
    "PickerApp",
    "PickerScreen",
    "PivotCardScreen",
    "PivotFormScreen",
    "ProfConfirmScreen",
    "ProgressScreen",
    "QuitConfirmScreen",
    "ResetConfirmScreen",
    "ScopeDlgScreen",
    "SteerButtonRow",
    "SubMenuScreen",
    "SubmitErrorScreen",
    "TaskMenuScreen",
    "TasksView",
    "VERSION",
    "VRow",
    "WorktreesView",
    "WtDetailsScreen",
    "_AutoExpandTextArea",
    "_DEFAULT_HOST_COLS",
    "_DEFAULT_TARGET_ENVS",
    "_FocusRegion",
    "_NO_FLEX_COLUMN",
    "_PickerButtons",
    "_PickerMachine",
    "_PickerNativeData",
    "_PickerPivots",
    "_PickerSegment",
    "_PickerStickyHeader",
    "_STEER_DRAFTS_ENV",
    "_ScopeSelectionList",
    "_idle_timeout_secs",
    "_normalize_form_fields",
    "_palette_style",
    "_poll_secs",
    "_register_shift_enter_key",
    "_resolve_mock_mode",
    "_resolve_version",
    "_size_mb",
    "_steer_draft_path",
    "canonical_key",
    "fit",
    "header_text",
    "row_text",
    "target_rows",
]


class PickerScreen(
    PickerScreenPivotsMixin,
    PickerScreenLoadingMixin,
    PickerScreenRuntimeMixin,
    PickerScreenModelMixin,
    PickerScreenSelectionMixin,
    PickerScreenRenderingMixin,
    PickerScreenInputMixin,
    PickerScreenMaintenanceActionsMixin,
    PickerScreenWorktreeActionsMixin,
    PickerScreenWorkerActionsMixin,
    PickerScreenPivotActionsMixin,
    Widget,
):
    can_focus = True
    BINDINGS = [
        Binding("ctrl+shift+right", "pivot_next", "next pivot", show=False),
        Binding("ctrl+shift+left", "pivot_prev", "prev pivot", show=False),
        Binding("ctrl+right", "machine_next", "next machine", show=False),
        Binding("ctrl+left", "machine_prev", "prev machine", show=False),
    ]
    BINDING_KEYS = frozenset({
        "ctrl+shift+right", "ctrl+shift+left", "ctrl+right", "ctrl+left",
    })
    _ZONE_WIDGET = {
        "V": "nf-pivots",
        "CFG": "nf-pivots",
        "UPD": "nf-pivots",
        "M": "nf-machine",
        "BTN": "nf-buttons",
    }

    def __init__(
        self,
        source,
        live=False,
        mock_mode=None,
        after_first_refresh=None,
    ):
        super().__init__()
        # NF5 (#88): the native compose tree is the *sole display path* -- the
        # env toggle and the render() fallback are retired. ``render()`` survives
        # only as the deterministic capture seam (see ``compose``).
        # NF3 (#88): scroll offset for the compose tree's data body widget --
        # data-relative (indexes the scrolling data rows only, since the M/BTN
        # chrome is fixed above it), distinct from the monolith's ``top``.
        self._data_top = 0
        # Cancellation for in-flight ``_run_bg`` pivot-action workers: set by
        # ``on_unmount`` when the picker itself is tearing down (a launch
        # decision, cancel, or quit) so a worker still running off-thread at that
        # moment knows the render flow it would marshal its outcome onto is gone
        # -- it drops quietly instead of racing ``app.call_from_thread`` and
        # logging a "could not marshal" warning for what is actually an expected,
        # intentional exit rather than an unforeseen failure. ``_bg_threads``
        # tracks the live worker threads so the teardown path (and any future
        # extension of it) has visibility into what is still outstanding.
        self._bg_cancel = threading.Event()
        self._bg_threads: set[threading.Thread] = set()
        # Per-refresh render caches (#169): in the NF compose tree every segment
        # widget (title / pivots / chrome / machine / buttons / footer) renders
        # from this one screen's derived frame in the SAME paint pass. Without a
        # cache each independently rebuilds the whole body, so build_body /
        # build_data run ~10x per keystroke -- a ~100ms/key stall that reads as a
        # hang under key-repeat. These memoize the derived frame / split keyed by
        # ``_render_sig`` (a fingerprint of every input), so the sibling widgets
        # in one pass share a single build while any state change rebuilds once;
        # ``refresh()`` also busts them as a belt-and-suspenders for the
        # interactive path.
        self._frame_cache = None   # (render_sig, segments-dict)
        self._split_cache = None   # ((render_sig, use_sel), (chrome, data))
        self._chrome_cache = None  # ((render_sig, use_sel), chrome-only vrows)
        # Held-arrow flood guard (dotfiles#948 follow-up): a cursor move only
        # needs the native OptionList to repaint its own highlight (instant); the
        # sel-dependent CHROME (topbar/footer) is refreshed by the render tick,
        # not synchronously per keystroke. Without this, every key fired a full
        # ~100ms screen re-composite, so a held arrow queued refreshes faster
        # than they drained and the picker froze. The tick coalesces them to
        # ~10fps regardless of key-repeat rate.
        self._nav_dirty = False
        # NF3 focus bridge (#88): guards the on_focus <-> sel mirror so focusing
        # a region widget to match ``sel`` doesn't recurse back into a sel write.
        self._nf_syncing = False
        # Set once the initial region focus is placed, so the framework's own
        # mount-time auto-focus can't stomp the default ``sel`` before then.
        self._nf_mounted = False
        # Profiles configurator -- an encapsulated sub-view component that OWNS
        # the Profiles grid-editing state (grid / applied / pcol / targets /
        # host_cols / _prof_unavailable) and behaviour; PickerScreen exposes
        # thin @property + method shims onto it (#88 F5 slices 1-3). Created
        # first, before any shim can be touched during construction.
        self.profiles_view = ProfilesView(self)
        # Maintenance pivot -- a second encapsulated body sub-view (#88 F5 slice
        # 5a). Owns the Maintenance render (the select-all / group-header / data
        # rows + column header); the selection state + grouping behaviour still
        # live on PickerScreen for now, reached from the component via
        # ``self._eng`` (moved onto the component in a follow-up slice).
        self.maintenance_view = MaintenanceView(self)
        # Registered/Tasks pivot -- a third encapsulated body sub-view (#88 F5
        # slice 6). Owns the registered-pivot body render (the status/count line
        # + grouped task rows); the read-only task data + pivot-scoping context
        # stay on PickerScreen, reached from the component via ``self._eng``.
        self.tasks_view = TasksView(self)
        # Worktrees list -- the picker's primary body, and the fourth/last body
        # sub-view componentized (#88 F5 slice 7). Owns the list render; the
        # multi-select state (wt_sel / wt_anchor) + its dispatch/range behaviour
        # stay on PickerScreen (shared with the focus machinery), read from the
        # component via ``self._eng``.
        self.worktrees_view = WorktreesView(self)
        self.htab = 0                 # index into self.pivots (built-ins + registered)
        # Cross-plugin pivot registry (Worktrees/Maintenance/Profiles + any
        # pivot a sibling plugin contributed via a manifest). Built here so the
        # tab bar and dispatch are index-agnostic; _load_pivots re-scans on 'r'.
        self.pivots = []              # list of {"label","kind","pivot"} descriptors
        self.htabs = list(BUILTIN_PIVOTS)   # display labels (rebuilt in _load_pivots)
        self.registered_pivots = []   # RegisteredPivot list from the manifest scan
        self.wt_actions = []          # contributed WorktreeAction list (#B)
        self.config_sections = []     # contributed ConfigSection list (#B slice 2)
        # ⚙ Configuration menu is a native Textual ModalScreen now (#88 F4):
        # no self.cfgmenu / cfgmenu_idx state attrs -- see CfgMenuScreen and
        # _open_cfgmenu. (The overlay left the manual registry entirely.)
        self._pivot_runtimes = {}     # pivot name -> RegisteredPivotRuntime (lazy)
        self._mkey_map = None         # machines.yaml display-name -> registry-key map (lazy/prewarmed)
        self._mkey_map_inflight = False  # guards against duplicate concurrent prewarms
        self._last_pivot_poll = 0.0   # registered-pivot repoll gate (#staleness)
        self._roster_ready = True     # False only during live chrome-first paint
        # Built-ins only here: scanning the pivot registry is I/O and must not
        # run before the first frame. ``setup()`` / live async fill scan later.
        self._load_pivots(scan=False)
        self.machine_idx = 0          # selected machine sub-pivot (Worktrees/Maint)
        self.sel = ("N", 0)           # (zone, index) -> default New Worktree
        self.top = 0                  # scroll offset into body vrows
        # Per-worktree action menu is a native Textual ModalScreen now (#88 F4):
        # no self.submenu / submenu_idx state attrs -- see SubMenuScreen and
        # _open_submenu. (The overlay left the manual registry entirely.)
        self.msgview = None           # recent-messages viewer overlay (#session-viewer)
        self._msgview_lock = threading.Lock()
        self._provider_loader = None
        self._provider_loader_lock = threading.Lock()
        self._provider_cancelled = False
        # Clean/Sync scope + New-worktree options are native ModalScreens now
        # (#88 F4): no self.cleanup / self.optmenu state attrs -- see
        # ScopeDlgScreen and _open_cleanup / _open_sync / _open_optmenu.
        # Maintenance multi-select (#1345) lives on ``self.maintenance_view``
        # now (#88 F5 slice 5b); the engine reaches it via a @property shim.
        self.wt_sel = ListSelection()      # Worktrees list multi-select (#2228 2b)
        self.wt_anchor = None         # Worktrees range-select anchor index (#2258 P3)
        self.list_view = ListView()   # Worktrees filter/sort state (#2228 Phase 4)
        self.cmd_mode = False          # composing "/" command-bar text (#2228 Phase 4)
        self.last_l = 0               # remembered Worktrees list focus (Tab memory, #2258 P3)
        self._wt_reconcile_after = None  # live: (m,e) targets whose reload must
                                         # settle before reconciling wt_sel (#2258 P3-7)
        # Maintenance actions menu is a native Textual ModalScreen now (#88 F4):
        # no self.maint_menu / maint_menu_idx state attrs -- see MaintMenuScreen
        # and _open_maint_menu. (The overlay left the manual registry entirely.)
        self.progress = None          # cleanup/sync progress sub-dialog
        self.executor = None          # real maintenance executor
        # Real cleanup/sync ops are the DEFAULT: the Maintenance actions
        # actually mutate worktrees (local in-process + remote over SSH), so a
        # Sync/Cleanup the operator runs takes effect (issue #1420). The mock
        # progress walker (safe in-TUI simulation, no side effects) runs ONLY in
        # explicit mock mode -- the dev sandbox for building UX/flows. It is
        # never triggered implicitly. See _resolve_mock_mode: entered via
        # `picker mock`, AGENT_WORKTREES_PICKER_MOCK=1, or the deprecated
        # AGENT_WORKTREES_PICKER_REAL_OPS=0.
        self.mock_mode = _resolve_mock_mode(mock_mode)
        self.real_ops = not self.mock_mode
        self.last_pr = 0              # remembered Profiles grid row (Tab in/out)
        self.btn_idx = 0              # active button within the current group
        self.show_hidden = False      # reveal bridge/system worktrees (#1422)
        # (Profiles grid state -- pcol / grid / applied / targets / host_cols /
        # _prof_unavailable -- AND the load/Apply plumbing -- _prof_load /
        # _prof_apply / _prof_loading / _prof_loaded -- live on
        # self.profiles_view now, reached via the @property + method shims
        # below; #88 F5 slices 3-4.)
        self.debug = "ready"
        # Busy indicator for the always-async control plane: while a background
        # action (``_run_bg``) is in flight, the footer shows the shared animated
        # spinner + this label instead of a static line (see ``footer``/``_tick``).
        self._busy_label = None
        # The cross-plugin verb map backing the OPEN worktree Actions menu; its
        # dispatch reads this so a live in-place verb refine stays consistent.
        self._wt_submenu_ext = {}
        self.data = []
        self.machines = []
        self.source_tabs = []
        self.pulse = 0
        self.frame = 0
        self.t0 = 0.0
        self.load_delay = {}
        self._frame_health = None
        self._source_local = None
        self._source_repo_branch = ("", "")
        self._after_first_refresh_callback = after_first_refresh
        # Injected data source (local / SSH / fixture). ``live`` enables the
        # async per-machine loader the source supplies via ``make_loader()``.
        self.src = source
        self.live = live
        self.loader = None            # async loader (live/multi-machine only)

    @property
    def maint_sel(self):
        return self.maintenance_view.maint_sel

    @maint_sel.setter
    def maint_sel(self, value):
        self.maintenance_view.maint_sel = value

    @property
    def grid(self):
        return self.profiles_view.grid

    @grid.setter
    def grid(self, value):
        self.profiles_view.grid = value

    @property
    def applied(self):
        return self.profiles_view.applied

    @applied.setter
    def applied(self, value):
        self.profiles_view.applied = value

    @property
    def pcol(self):
        return self.profiles_view.pcol

    @pcol.setter
    def pcol(self, value):
        self.profiles_view.pcol = value

    @property
    def targets(self):
        return self.profiles_view.targets

    @targets.setter
    def targets(self, value):
        self.profiles_view.targets = value

    @property
    def host_cols(self):
        return self.profiles_view.host_cols

    @host_cols.setter
    def host_cols(self, value):
        self.profiles_view.host_cols = value

    @property
    def _prof_unavailable(self):
        return self.profiles_view._prof_unavailable

    @_prof_unavailable.setter
    def _prof_unavailable(self, value):
        self.profiles_view._prof_unavailable = value

    @property
    def _prof_load(self):
        return self.profiles_view._prof_load

    @_prof_load.setter
    def _prof_load(self, value):
        self.profiles_view._prof_load = value

    @property
    def _prof_apply(self):
        return self.profiles_view._prof_apply

    @_prof_apply.setter
    def _prof_apply(self, value):
        self.profiles_view._prof_apply = value

    @property
    def _prof_loading(self):
        return self.profiles_view._prof_loading

    @_prof_loading.setter
    def _prof_loading(self, value):
        self.profiles_view._prof_loading = value

    @property
    def _prof_loaded(self):
        return self.profiles_view._prof_loaded

    @_prof_loaded.setter
    def _prof_loaded(self, value):
        self.profiles_view._prof_loaded = value

    def action_pivot_next(self):
        self._switch_pivot(1)
        self.refresh()
    def action_pivot_prev(self):
        self._switch_pivot(-1)
        self.refresh()
    def action_machine_next(self):
        self._rotate_machine(1)
        self.refresh()
    def action_machine_prev(self):
        self._rotate_machine(-1)
        self.refresh()
    def on_key(self, event):
        key = event.key
        # Truly-global shortcuts are owned by Textual BINDINGS (#88 F3): let them
        # bubble to the framework's binding system (do NOT stop the event) so the
        # matching action_* method fires. This ``on_key`` only ever runs for the
        # top-level views -- every modal is a native ModalScreen (#88 F4) that
        # sits above this widget on the screen stack and consumes keys itself --
        # so there is no overlay-active case to guard against. Everything else
        # goes to the manual dispatcher. Composing (#2228 Phase 4) owns
        # EVERY key -- checked first, same as the region widgets' own.
        if not self.cmd_mode and key in self.BINDING_KEYS:
            return
        event.stop()
        event.prevent_default()
        if event.character in ("[", "]"):
            key = event.character
        self._dispatch_key(key, event.character)
        self.refresh()


class PickerApp(App):
    CSS = """
    Screen { background: $surface; }
    PickerScreen { width: 100%; height: 100%; }
    /* NF compose tree (#88): fixed-height chrome, body fills the rest. */
    PickerScreen > #nf-title  { width: 100%; height: 1; }
    PickerScreen > #nf-pivots { width: 100%; height: 1; }
    PickerScreen > #nf-chrome { width: 100%; height: 2; }
    PickerScreen > #nf-machine { width: 100%; height: auto; }
    PickerScreen > #nf-buttons { width: 100%; height: auto; }
    PickerScreen > #nf-body-data   { width: 100%; height: 1fr; }
    PickerScreen > #nf-footer { width: 100%; height: 2; }
    /* NF5-5 native OptionList data body: picker palette + the amber cursor
       used by the native modals; matches the base type so it targets the
       _PickerNativeData subclass. */
    PickerScreen > OptionList#nf-body-data {
        border: none; background: $surface; padding: 0;
        /* #171: hide the native vertical scrollbar. It (a) is redundant -- the
           picker draws its own ▲/▼ "N more above/below" chrome borders (which
           track ``self.top`` via _ensure_visible, independent of this widget),
           and (b) is the ROOT of the description line-wrap jitter: a shown
           scrollbar shrinks the content region by its width, so rows padded to
           the full widget width overflow and wrap to 2 lines. Worse, the width
           flips as the scrollbar appears/disappears (or is re-measured during a
           single-row replace), so the wrap flickers in and out per keystroke.
           With no scrollbar the content width equals the widget width and is
           constant, so full-width rows fit exactly and never wrap -- fixing the
           jitter for BOTH the full-rebuild and the incremental repaint paths. */
        scrollbar-size-vertical: 0;
    }
    PickerScreen > OptionList#nf-body-data:focus {
        background-tint: $surface 0%;
    }
    PickerScreen > OptionList#nf-body-data > .option-list--option-highlighted,
    PickerScreen > OptionList#nf-body-data:focus
        > .option-list--option-highlighted {
        background: #ffaf00; color: black; text-style: bold;
    }
    /* NF5-5 pinned section header (hidden -> takes no space until scrolled). */
    PickerScreen > #nf-body-sticky { width: 100%; height: 1; }
    """

    def __init__(
        self,
        source,
        live=False,
        mock_mode=None,
        after_first_refresh=None,
    ):
        super().__init__()
        self._source = source
        self._live = live
        self._mock_mode = mock_mode
        self._after_first_refresh = after_first_refresh
        self.result = None            # set by the screen on a launch decision
        # Idle-liveness self-exit (copilot-extensions#2761 follow-up) -- see
        # `_idle_timeout_secs` for the full rationale. Tracked in monotonic
        # time so a system clock change never produces a spurious timeout.
        self._last_input_activity = time.monotonic()
        self._idle_check_timer = None

    def compose(self) -> ComposeResult:
        yield PickerScreen(
            self._source,
            self._live,
            mock_mode=self._mock_mode,
            after_first_refresh=self._after_first_refresh,
        )

    def on_mount(self) -> None:
        if IDLE_TIMEOUT_SECS > 0:
            # Check at 1/10th the timeout (bounded to a sane range) so the
            # actual exit lands within ~10% of the configured threshold
            # without polling needlessly often for a long timeout.
            check_interval = max(5.0, min(60.0, IDLE_TIMEOUT_SECS / 10))
            self._idle_check_timer = self.set_interval(
                check_interval, self._check_idle_timeout)

    async def on_event(self, event) -> None:
        # Only a real input event counts as activity -- the background
        # poll/render timers elsewhere in this module fire continuously and
        # must never reset the idle clock, or this check would never fire.
        if isinstance(event, (events.Key, events.MouseEvent, events.Paste)):
            self._last_input_activity = time.monotonic()
        await super().on_event(event)

    def _check_idle_timeout(self) -> None:
        if IDLE_TIMEOUT_SECS <= 0:
            return
        idle_for = time.monotonic() - self._last_input_activity
        if idle_for < IDLE_TIMEOUT_SECS:
            return
        try:
            from .frame_health import append_launch_event

            append_launch_event(
                "picker_idle_timeout_exit", idle_seconds=round(idle_for, 1))
        except Exception:
            pass
        # No launch decision was made -- self.result stays None, exactly like
        # the user confirming "quit" on QuitConfirmScreen with no selection.
        self.exit()
