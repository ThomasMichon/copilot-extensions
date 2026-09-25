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

import os

from rich.text import Text
from .styles import (
    C_BAND, C_BTN, C_BTN_LAST, C_BTN_SEL, C_CAUTION, C_DIM, C_DISABLED,
    C_DISPO, C_ENV, C_FAINT, C_HEADER, C_HINT, C_HINT_ON, C_LABEL, C_LOAD,
    C_META, C_MUTED, C_PR_MERGED, C_PULSE, C_PULSE_AWAIT, C_READY, C_SECTION,
    C_SEL, C_SEL_BG, C_SEL_ON, C_SPIN, C_STATE, C_TAB_ACTIVE,
    C_TAB_FOCUS_ON, C_TABOFF, C_WARN, DISPO_MARK, SPINNER,
    canonical_key,
)

__all__ = [
    "ACTIVE_SPECS",
    "ACTION_DESC",
    "BUILTIN_PIVOTS",
    "BUTTON_SETS",
    "CLEAN_SPECS",
    "C_BAND",
    "C_BTN",
    "C_BTN_LAST",
    "C_BTN_SEL",
    "C_CAUTION",
    "C_DIM",
    "C_DISABLED",
    "C_DISPO",
    "C_ENV",
    "C_FAINT",
    "C_HEADER",
    "C_HINT",
    "C_HINT_ON",
    "C_LABEL",
    "C_LOAD",
    "C_META",
    "C_MUTED",
    "C_PR_MERGED",
    "C_PULSE",
    "C_PULSE_AWAIT",
    "C_READY",
    "C_SECTION",
    "C_SEL",
    "C_SEL_BG",
    "C_SEL_ON",
    "C_SPIN",
    "C_STATE",
    "C_TAB_ACTIVE",
    "C_TAB_FOCUS_ON",
    "C_TABOFF",
    "C_WARN",
    "DISPO_MARK",
    "HTABS",
    "IDLE_TIMEOUT_SECS",
    "LIST_SPECS",
    "MAINT_ACTION_DESC",
    "MAINT_GROUP_ORDER",
    "PAD",
    "PIVOT_PLACEMENT",
    "POLL_SECS",
    "PROF_SPECS",
    "SPINNER",
    "VERSION",
    "VRow",
    "_DEFAULT_HOST_COLS",
    "_DEFAULT_TARGET_ENVS",
    "_NO_FLEX_COLUMN",
    "_clip",
    "_idle_timeout_secs",
    "_palette_style",
    "_poll_secs",
    "_register_shift_enter_key",
    "_resolve_mock_mode",
    "_resolve_version",
    "_size_mb",
    "canonical_key",
    "fit",
    "header_text",
    "row_text",
    "target_rows",
]


def _register_shift_enter_key() -> None:
    """Surface **Shift+Enter** as a distinct ``shift+enter`` key.

    Windows Terminal (and most terminals, by the classic meta-key convention)
    emits ``ESC`` + ``CR`` (``\\x1b\\r``) for Shift+Enter -- it does *not* emit
    the Kitty ``\\x1b[13;2u`` sequence, even with the Kitty protocol pushed
    (verified empirically: Shift+Enter -> ``\\x1b\\r`` under kitty flag 1, flag
    15, and xterm modifyOtherKeys alike). Textual's ``XTermParser`` then
    reissues that ``ESC``+``CR`` char-by-char, and because ``\\r`` is a tuple
    entry in ``ANSI_SEQUENCES_KEYS`` the ``alt``/meta flag is dropped -- so
    Shift+Enter collapses to a plain ``enter``, indistinguishable from Enter
    (which we bind to accept+advance). That is why Shift+Enter "just advanced".

    The parser looks up the *whole* ``\\x1b\\r`` sequence in
    ``ANSI_SEQUENCES_KEYS`` **before** the collapsing char-by-char reissue, so
    registering it there makes the atomic ESC+CR resolve to a real
    ``shift+enter`` key event (Textual already decodes the Kitty ``\\x1b[13;2u``
    form to the same key, so both encodings converge). This is the app-side
    equivalent of what Copilot-CLI does: accept meta+Enter as the newline. A
    lone ``ESC`` is untouched (it still resolves to ``escape``), so the form's
    Esc = save+close is preserved.
    """
    try:
        from types import SimpleNamespace

        from textual import _ansi_sequences as _seqmod

        # Value shape matches the map's tuple entries: objects exposing ``.value``
        # (the parser yields ``events.Key(key.value, ...)`` for tuple matches).
        _seqmod.ANSI_SEQUENCES_KEYS.setdefault(
            "\x1b\r", (SimpleNamespace(value="shift+enter"),)
        )
    except Exception:
        # Never let a Textual internal-layout change break the picker; the
        # Alt+Enter / Ctrl+J newline fallbacks still apply if this no-ops.
        pass


_register_shift_enter_key()


def start_loader(loader, *, focus_keys):
    """Start ``loader``, passing ``focus_keys`` only if its ``start()``
    actually accepts one.

    The Manager resolves ``agent_worktrees`` (and its ``LiveLoader``) as a
    separate, independently versioned runtime slot -- an older engine's
    ``LiveLoader.start()`` takes no argument at all. Checking the callable's
    own signature (rather than calling with the keyword and catching
    ``TypeError``) tells "this loader doesn't support focus_keys" apart from
    "the loader accepted the call and failed for its own reason" -- a broad
    ``except TypeError`` around the call would swallow (and silently retry,
    duplicating any threads already spawned) a real bug inside the CURRENT
    ``start()`` implementation, not just an old-engine signature mismatch.
    """
    import inspect

    try:
        accepts_focus = "focus_keys" in inspect.signature(loader.start).parameters
    except (TypeError, ValueError):
        accepts_focus = False
    if accepts_focus:
        loader.start(focus_keys=focus_keys)
    else:
        loader.start()


def _resolve_version() -> str:
    """Real package version for the picker banner.

    Mirrors ``agent-worktrees --version``: read the generated ``_build_info``
    module (regenerated by the installer with the true version/commit), falling
    back to installed-package metadata, then a ``dev`` marker. This is
    deliberately *not* a hand-maintained literal -- the old
    ``VERSION = "1.5.3-devNN"`` constant silently froze at dev69 while the
    package shipped dev97, so the banner lied about which code was running.
    """
    try:
        from .._build_info import BUILD_INFO

        v = BUILD_INFO.get("version")
        if v:
            return v
    except Exception:
        pass
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("agent-worktrees")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    return "dev"


VERSION = _resolve_version()

PAD = "   "  # inter-column padding (3 spaces -> info breathes)

# worktree-finality-and-obligations Phase 9 / iconify-relation: the RELATION
# column's enum vocabulary (reciprocal.short_label's output) collapsed to one
# scannable glyph each, so the column can shrink from 7 cells (still
# truncated to "RELATI…" even at that width) to 2 -- the freed width goes to
# the flex `title` column via `fit()`. The full word rides the row's second
# (detail) line instead, so no information is actually lost.
_RELATION_ICON = {
    "BOUND": "●",
    "CONTROL": "◐",
    "HANDOFF": "⇒",
    "TERM": "■",
    "AMBIG": "?",
    "": " ",
}
_RELATION_STYLE = {
    "BOUND": C_STATE["ACTIVE"],
    "CONTROL": "grey58",
    "HANDOFF": C_STATE["WIP"],
    "TERM": "grey35",
    "AMBIG": C_WARN,
    "": "",
}

# Named palettes for a registered pivot's declarative per-value cell colouring
# (Column.palette). The ``state`` palette REUSES the Worktrees state vocabulary
# (``C_STATE``) so a contributed pivot's status column reads with the same colours
# as the worktree list: a CodeSpace ``RUNNING`` shares the ACTIVE blue, ``STALE``
# the WIP amber, ``STOPPED`` the UNUSED grey, etc. Keys are upper-cased at lookup.
_STATE_PALETTE = {
    "RUNNING": C_STATE["ACTIVE"],   # live -> ACTIVE blue
    "STALE": C_STATE["WIP"],        # aged recycle candidate -> WIP amber
    "STOPPED": C_STATE["UNUSED"],   # dormant -> UNUSED grey
    "IN-USE": C_STATE["ACTIVE"],    # disposition axis (reused vocabulary)
    "IDLE": C_STATE["UNUSED"],
    "CLEAN": C_STATE["FINAL"],      # rescued/reusable -> FINAL green
    "PROVISIONING": "yellow",
    "FAILED": C_WARN,
}
_STATE_PALETTE.update(C_STATE)      # also honour raw worktree state names
# agent-dispatch-tasks-pane-ux-overhaul: the Tasks pivot's own phase vocabulary
# (agent_dispatch.task_state_machine.Status, projected through
# agent-dispatch-board's Blocked/Proposed/Queued/Started/Suspended/Completed/
# Abandoned grouping) mapped onto the SAME C_STATE colours Worktrees uses, so a
# task's phase reads with the same at-a-glance meaning as a worktree's state
# (blue = actively worked, amber = needs attention, green = done, grey = not
# yet started, dark grey = terminal/irrelevant) -- a distinct palette name
# (not "state") because the two vocabularies are not 1:1 and must not drift
# into each other if either changes independently.
_TASK_PHASE_PALETTE = {
    "PROPOSED": C_STATE["UNUSED"],   # not yet approved -> UNUSED grey
    "QUEUED": C_STATE["UNUSED"],     # approved, awaiting a worker -> same grey
    "STARTED": C_STATE["ACTIVE"],    # an agent is actively working it -> blue
    "BLOCKED": C_STATE["WIP"],       # awaiting operator steer -> WIP amber
    "SUSPENDED": C_STATE["CONVO"],   # user/system paused, mid-conversation -> teal
    "COMPLETED": C_STATE["FINAL"],   # done and settled -> FINAL green
    "ABANDONED": C_STATE["GONE"],    # terminal, no longer relevant -> dark grey
}
_PALETTES = {"state": _STATE_PALETTE, "task_phase": _TASK_PHASE_PALETTE}


def _palette_style(name, value):
    """The per-value style for palette ``name`` and cell ``value`` (upper-cased
    lookup), or ``""`` when the palette or value is unknown."""
    pal = _PALETTES.get(name or "")
    if not pal:
        return ""
    return pal.get(str(value).strip().upper(), "")


# ---- key canonicalization ---------------------------------------------------
# (KEY_ALIASES / canonical_key moved to styles.py, module-size)


# ---- column fitter ----------------------------------------------------------

def fit(specs, avail, flex_key, flex_min):
    """specs: (key, header, width, align, prio). Drop highest-prio-number cols
    until flex col can hold flex_min, then flex absorbs slack. Returns ordered
    (key, header, width, align)."""
    order = {c[0]: i for i, c in enumerate(specs)}
    keep = sorted(specs, key=lambda c: c[4])
    gap = len(PAD)

    def used(cols):
        return sum(c[2] for c in cols) + gap * max(0, len(cols) - 1)

    while True:
        cols = sorted(keep, key=lambda c: order[c[0]])
        u = used(cols)
        flex_w = next((c[2] for c in cols if c[0] == flex_key), 0)
        if u <= avail and (flex_w + (avail - u)) >= flex_min:
            break
        drop = [c for c in keep if c[0] != flex_key]
        if not drop:
            break
        keep.remove(max(drop, key=lambda c: c[4]))
    cols = sorted(keep, key=lambda c: order[c[0]])
    out = [[k, h, w, a] for k, h, w, a, _ in cols]
    slack = avail - used(cols)
    for c in out:
        if c[0] == flex_key:
            c[2] += slack
            break
    return out


def _clip(s, w, align):
    s = str(s)
    if len(s) > w:
        s = s[: max(0, w - 1)] + "…" if w > 1 else s[:w]
    return s.rjust(w) if align == "r" else s.ljust(w)


def row_text(rec, cols, width, selected, indent=1, pulse=0, mark=None):
    if mark is not None:
        # A left-side selection gutter (#2228): a checkbox glyph + one margin
        # space, so multi-select reads as an always-visible affordance. The
        # caller fits the columns two cells narrower to make room.
        glyph, gstyle = mark
        t = Text(glyph, style=gstyle)
        t.append(" ")
    else:
        t = Text(" " * indent)
    for i, (k, _h, w, a) in enumerate(cols):
        if i:
            t.append(PAD)
        val = str(rec.get(k, ""))
        if k == "relation":
            val = _RELATION_ICON.get(val, "?" if val else " ")
        if k == "machine_env":
            if rec.get("source_kind") != "machine-ssh":
                t.append(_clip(val, w, a))
                continue
            raw = val
            mach, env = (raw.rsplit(" ", 1) if " " in raw else (raw, ""))
            if env and len(mach) + 1 + len(env) > w:
                mach = mach[: max(0, w - len(env) - 1)]
            seg = Text(mach + (" " if env else ""))
            seg.append(env, style=C_ENV.get(env, ""))
            if seg.cell_len < w:
                seg.append(" " * (w - seg.cell_len))
            t.append_text(seg)
            continue
        cell = _clip(val, w, a)
        style = ""
        if k == "state":
            style = C_STATE.get(rec.get("state", ""), "")
        elif k == "relation":
            style = _RELATION_STYLE.get(rec.get("relation", ""), "")
        elif k == "env":
            style = C_ENV.get(rec.get("env", ""), "")
        elif k == "dispo":
            style = C_DISPO.get(rec.get("dispo_level", ""), "")
        elif k == "pr" and rec.get("pr", "").endswith("✓"):
            style = C_PR_MERGED
        elif k == "claims_summary" and rec.get("pr", "").endswith("✓"):
            # #3307 Phase 4: the Worktrees pivot's CLAIMS column shows the
            # shared claims_rank summary text, but keeps the merged-PR
            # green highlight keyed off the still-populated raw "pr" field
            # (claims_rank's own formatting carries no merged marker).
            style = C_PR_MERGED
        elif k == "sess":
            if rec.get("sess", "").startswith("●") or rec.get("sess") == "PROC":
                style = C_PULSE[pulse]
            elif rec.get("sess") == "LOCK":
                style = C_STATE["WIP"]
        t.append(cell, style=style)
    if t.cell_len < width:
        t.append(" " * (width - t.cell_len))
    if selected:
        t.stylize(C_SEL)
    return t


def header_text(cols, width, label_style=C_HEADER, indent=1):
    t = Text(" " * indent)
    for i, (_k, h, w, _a) in enumerate(cols):
        if i:
            t.append(PAD)
        t.append(_clip(h.upper(), w, "l"), style=label_style)
    if t.cell_len < width:
        t.append(" " * (width - t.cell_len))
    return t


ACTIVE_SPECS = [
    ("id4", "id", 4, "l", 2), ("state", "state", 6, "l", 4),
    ("relation", "r", 1, "l", 6),
    ("machine_env", "source", 19, "l", 5),
    ("age", "age", 4, "l", 7), ("sess", "live", 4, "l", 8),
    # #3307 Phase 4: standardized on "claims" (the shared claims_rank
    # summary), replacing the single-PR-only "pr" column -- matching the
    # Codespaces/Containers pivots' own column label.
    ("claims_summary", "claims", 12, "l", 3),
]
LIST_SPECS = [
    ("id4", "id", 4, "l", 2), ("state", "state", 6, "l", 4),
    ("relation", "r", 1, "l", 5),
    ("age", "age", 4, "l", 6), ("sess", "live", 4, "l", 7),
    ("turns", "t", 3, "r", 8),
    ("claims_summary", "claims", 12, "l", 3),
]
#: The Worktrees list's own row title now lives entirely on the detail line
#: (``WorktreesView._detail_line``, "Title: Activity") rather than as a
#: truncated flex column here, so ``fit()`` below is called with a flex key
#: that deliberately matches none of the specs above: every column keeps its
#: declared width and any leftover row width is left as blank trailing space
#: (via ``row_text``/``header_text``'s own tail-padding) instead of stretching
#: one column to soak it up.
_NO_FLEX_COLUMN = "__no_flex__"
HTABS = ["Worktrees", "Maintenance", "Profiles"]
#: The built-in pivots, in their canonical order. Registered pivots (contributed
#: by other plugins via ``picker_tui.pivots``) are woven into this order at
#: runtime by their ``after`` hint (see ``PickerScreen._load_pivots``). Built-in
#: dispatch keys off the pivot *kind* (the lowercased label) rather than a magic
#: index, so an inserted pivot never renumbers the built-ins.
BUILTIN_PIVOTS = list(HTABS)

#: Pivot *placement* -- where a pivot is reached from, keyed by pivot kind:
#: ``"left"`` (the default) rides the left pivot cycle (◀▶ / [ ]); ``"config"``
#: is hosted under the right-aligned ⚙ Configuration menu (#1426); ``"hidden"``
#: is an ordering anchor only (kept so registered ``after`` hints still weave,
#: but never shown as a tab). Anything unlisted defaults to ``"left"``.
PIVOT_PLACEMENT = {"profiles": "config", "maintenance": "hidden"}


def _poll_secs() -> float:
    """Continuous background-poll interval in seconds (#1421).

    Conservative default (45s) so an open picker stays fresh without hammering
    SSH -- ``list --classify`` runs git classification across a machine's
    worktrees, so it is not a free status ping. ``AGENT_WORKTREES_PICKER_POLL_SECS``
    overrides it, and ``<= 0`` disables background polling entirely.
    """
    try:
        return float(os.environ.get("AGENT_WORKTREES_PICKER_POLL_SECS", "45"))
    except (TypeError, ValueError):
        return 45.0


POLL_SECS = _poll_secs()


def _idle_timeout_secs() -> float:
    """Self-exit threshold (seconds) for the Picker's own idle-liveness check
    (copilot-extensions#2761 follow-up).

    An ``isatty()`` check at spawn time (``run_tui_picker``) only rejects a
    launch that never had a real terminal to begin with; it cannot detect a
    genuinely-interactive-at-spawn session whose owning terminal is later torn
    down without the child process noticing (observed on book2: a resident
    Picker process pair surviving for hours, discoverable only via a process
    census). Rather than attempt a platform-specific "is my controlling
    terminal still alive" probe -- there is no single reliable cross-platform
    signal for that short of a failed read/write, and Textual's own driver
    already surfaces those as a crash (logged by
    ``_write_picker_crash_log``) when they occur -- this bounds the exposure
    the same way the engine's resident status-monitor bounds its own orphaned
    instances: a generous, self-retiring idle timeout. A picker with zero
    real input events (keys/mouse/paste; background poll/render timers do NOT
    count) for this many seconds exits gracefully, same as the user
    confirming "quit" with no launch decision made.

    Conservative default (30 minutes) so normal interactive use -- reading a
    long worktree list, stepping away briefly -- is never affected.
    ``AGENT_WORKTREES_PICKER_IDLE_TIMEOUT_SECONDS`` overrides it, and ``<= 0``
    disables the check entirely (mirrors ``_poll_secs``'s own disable idiom).
    """
    try:
        return float(
            os.environ.get("AGENT_WORKTREES_PICKER_IDLE_TIMEOUT_SECONDS", "1800")
        )
    except (TypeError, ValueError):
        return 1800.0


IDLE_TIMEOUT_SECS = _idle_timeout_secs()

# Maintenance groups worktrees by state; this is the display order (#1345).
# Any state not listed is appended after these, in first-seen order.
MAINT_GROUP_ORDER = ["DIRTY", "WIP", "ACTIVE", "ORPHAN", "CONVO", "UNUSED",
                     "GONE", "CLEAN", "FINAL", "MERGED"]
# Per-tab button sets — Tab/Shift+Tab rotate within these when focused.
# Worktrees has a single "New worktree…" entry that opens the options dialog
# directly (test-chamber #1346); the old separate "More options…" is gone.
# Per-pivot button sets, keyed by pivot *kind* (Tab/Shift+Tab rotate within
# these when focused). Worktrees and Profiles are handled inline in
# ``button_set`` (their sets are dynamic); registered pivots have none.
BUTTON_SETS = {"maintenance": ["K", "SY"]}

# ---- Profiles matrix model ----------------------------------------------------
# Axes are config-bound from machines.yaml at runtime (see picker_tui.roster and
# PickerScreen.setup): the real data sources (data_local/data_ssh) expose
# host_cols()/target_envs() derived from the roster. These fallbacks apply only
# when a source omits those hooks (e.g. a fixture source with no
# host_cols()/target_envs()). They are intentionally EMPTY so a missing roster
# degrades to an empty matrix instead of fabricating a machine list -- the
# picker ships in a shared marketplace plugin and must never hardcode one
# multi-machine system's roster.
_DEFAULT_HOST_COLS: list[tuple[str, str, str]] = []
_DEFAULT_TARGET_ENVS: list[tuple[str, str]] = []


def target_rows(target_envs):
    rows = []
    for m, e in target_envs:
        for agent in (True, False):
            rows.append({
                "machine": m, "env": e, "agent": agent,
                "label": f"{m} · {e} · {'agent' if agent else 'shell'}",
            })
    return rows

# Clarify each worktree action in the sub-menu (test-chamber #1343).
ACTION_DESC = {
    "Open": "Attach the worktree's live terminal (PSMux/TMux); launch one if "
            "none. Arrow to the No Mux row below to launch without the wrapper, "
            "for troubleshooting.",
    "Resume": "Relaunch this stopped worktree's Copilot, resuming its last "
              "session in a fresh TMux/PSMux.",
    "Messages": "Peek the last few messages of this worktree's latest session "
                "(read-only) -- see what it was doing without opening it.",
    "Sync": "Fast-forward this worktree onto the default branch (FF-only).",
    "Cleanup": "Remove this worktree (safe once merged/idle).",
    "Finalize": "Wrap up this conversation-only / unused worktree: verify "
                "nothing is unpushed (there is nothing) and remove it.",
    "Dispose hosted session": "Explicitly dispose this worktree's AHP-hosted "
                              "Copilot session and mark its execution leg "
                              "terminal so the worktree can be finalized.",
    "Stop": "Stop this worktree's Mux/Copilot wrapper now (graceful "
            "double-Ctrl-C, then a hard mux kill) so a following Open "
            "starts a fresh TMux/PSMux + Copilot.",
    "Bare resume": "Two-step restore: create this worktree's Mux, but launch "
                   "Copilot in HOME with no --resume (dodges a CLI bug that "
                   "fails to start in a repo/worktree cwd). Finish with a "
                   "manual /resume <id> (id shown above).",
    "Reclaim": "Kill the exact Copilot process(es) holding this session's lock "
               "(bare orphans a mux Stop cannot reach) AND delete any residual "
               "inuse.<pid>.lock, so it can be re-Opened or Bare-resumed "
               "cleanly.",
    "Restore": "Recover this unreachable active session in one step: use the "
               "platform remux primitive, then immediately resume/attach it "
               "through the normal TMux/PSMux launcher.",
    "Repair": "Reconcile an inconsistent worktree: reap the stray bare "
              "(un-muxed) orphan Copilot while PRESERVING the healthy mux "
              "session, clear stale locks, and re-derive tracked state.",
    "Jump to host": "Switch to this worktree's host machine tab and highlight "
                    "it (reveals hidden bridge/system worktrees).",
    "Jump to caller": "Jump to the worktree that requested this bridge worktree "
                      "(its caller) and highlight it.",
    "Refresh": "Gather this worktree's live state now (turns, session summary, "
               "git + session liveness) and write it back to the cache -- "
               "populates an Unknown row / re-syncs a stale one, without a "
               "full-fleet reload.",
    "View details": "Open a scrollable card with this worktree's full title, "
                    "path, id, status, held claims, and session detail -- "
                    "everything too long to fit in this menu's own header.",
}

# Per-action descriptions for the Maintenance actions menu (#1345).
MAINT_ACTION_DESC = {
    "Sync": "Fast-forward the FF-eligible selected worktrees onto the default "
            "branch.",
    "Cleanup": "Remove the cleanable selected worktrees (re-checked per worktree).",
    "Finalize": "Wrap up the conversation-only / unused selected worktrees "
                "(verify nothing unpushed, then remove).",
    "Stop": "Stop the Mux/Copilot wrapper of the live selected worktrees "
            "(graceful double-Ctrl-C, then a hard mux kill).",
    "Reclaim": "Reap the bare (un-muxed) orphan Copilots bound to the selected "
               "worktrees (the ones a mux Stop cannot reach).",
}


class VRow:
    __slots__ = ("data", "kind", "pin_band", "pin_section", "stop", "text")

    def __init__(self, text, stop=None, kind=None, data=None):
        self.text = text
        self.stop = stop          # selectable stop id or None
        self.kind = kind          # 'band' | 'section' | 'colhdr' | None
        self.pin_band = None
        self.pin_section = None
        self.data = data


def _resolve_mock_mode(explicit=None):
    """Whether the picker runs in explicit **mock mode** (a safe dev sandbox).

    Mock mode is the ONLY thing that enables the picker's simulated behaviors:
    the in-TUI progress *walker* (instead of the real Cleanup/Sync executor, so
    no worktree is mutated) and the no-op profiles *apply*. It never turns on
    implicitly -- a normal launch always runs real ops, and a real launch that
    is somehow missing an IO hook fails honestly rather than silently mocking.

    Precedence:
      1. an explicit ``mock_mode`` argument (tests / ``picker mock``),
      2. the canonical env ``AGENT_WORKTREES_PICKER_MOCK`` (truthy),
      3. the deprecated env ``AGENT_WORKTREES_PICKER_REAL_OPS=0`` (back-compat).
    Default: ``False`` (real).
    """
    if explicit is not None:
        return bool(explicit)
    val = os.environ.get("AGENT_WORKTREES_PICKER_MOCK")
    if val and val.strip().lower() not in ("0", "false", "no", "off"):
        return True
    # Deprecated alias: REAL_OPS=0 used to force the mock walker.
    if os.environ.get("AGENT_WORKTREES_PICKER_REAL_OPS", "1") == "0":
        return True
    return False

def _size_mb(w):
    # deterministic pseudo-size from id for the cleanup demo. id4 is normally a
    # 4-hex-char worktree-id suffix; fall back to a char-sum for any non-hex id
    # (e.g. test fixtures) so this pseudo-size never raises -- the compose/NF
    # path renders the maintenance size counter eagerly, on any id.
    id4 = w.get("id4", "") if hasattr(w, "get") else w["id4"]
    try:
        n = int(id4, 16)
    except (ValueError, TypeError):
        n = sum(ord(c) for c in str(id4))
    return 120 + (n % 300)


CLEAN_SPECS = [
    ("id4", "id", 4, "l", 2), ("state", "state", 6, "l", 6),
    ("machine_env", "source", 19, "l", 7),
    ("dispo", "disposition", 18, "l", 3), ("pr", "pr", 8, "l", 5),
    ("age", "age", 4, "l", 9), ("mib", "size", 6, "r", 9),
    ("title", "title", 10, "l", 1),
]
PROF_SPECS = [
    ("name", "name", 22, "l", 1), ("app", "host app", 12, "l", 3),
    ("scope", "scope (machine · env)", 20, "l", 2), ("status", "status", 13, "l", 4),
]
