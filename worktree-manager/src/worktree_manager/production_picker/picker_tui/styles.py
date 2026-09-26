"""Shared color palette + key-canonicalization for the Picker TUI (#88 module
split, module-size).

Carved out of ``engine.py`` (module-size guard) so both it and ``dialogs.py``
(the migrated modal screens) can depend on this leaf module without either
depending on the other.
"""
from __future__ import annotations

# ---- palette (highlight-and-invert; subtle borders) -------------------------
C_DIM = "grey42"          # subtle separators / borders
C_HEADER = "bold white"
C_BAND = "bold orange1"   # band headers (scope)
C_SECTION = "bold grey70"
# Tabs/pivots: ACTIVE-but-unfocused is SUBTLE (no invert) so it doesn't compete
# with the focus cursor; only the FOCUSED tab inverts.
C_TAB_ACTIVE = "bold white on grey23"   # selected pivot, zone not focused
C_TAB_FOCUS = "reverse bold"            # selected pivot, zone focused (cursor)
C_TAB_FOCUS_ON = "reverse bold orange1"  # focused AND active view tab / ⚙ chip
C_TABOFF = "grey58"
C_SEL = "reverse"         # focused row -> invert (the cursor)
# Worktrees multi-select highlight states (#2258 follow-up): the focus cursor
# inverts (reverse); a green invert means the cursor is ALSO in the selection,
# a plain (white) invert means the cursor sits on an UNselected row, and a grey
# background marks a selected row the cursor has moved off. Layered on top of the
# per-cell styles, so the whole row reads as one state.
C_SEL_ON = "reverse green3"     # focused AND selected -> green invert
C_SEL_BG = "on grey30"          # selected but not focused -> grey background
C_SPIN = "yellow"
C_WARN = "red"
C_PR_MERGED = "green"
C_HINT = "grey46"         # scroll-hint arrows (subtle)
C_HINT_ON = "orange1"     # scroll hint when there IS more content that way
# copilot-extensions#228: the live-pulse sub-line's "needs me" accent -- an amber
# highlight (vs. the dim ⟳ of a busy/idle pulse) for a session parked on the
# operator (``live_rest`` == awaiting-operator).
C_PULSE_AWAIT = "bold #d7af00"
# Secondary-text shades (de-emphasized foreground). Named so the many ad-hoc
# grey/style literals scattered through the render methods route through ONE
# semantic vocabulary instead of bare shade codes (#85 item E). Exact values
# preserved -- this is a naming pass, not a recolor.
C_META = "grey70"         # secondary / metadata / hint text
C_LABEL = "grey78"        # inline minor labels + counts
C_FAINT = "grey62"        # fainter descriptive / panel-body text
C_MUTED = "grey54"        # most de-emphasized (pending rows, ellipses, "…")
C_CAUTION = "yellow"      # inline caution / warning (⚠) inside dialogs
# Glowy pulse for live indicators (two phases, cycled by a timer).
C_PULSE = ["green", "bold bright_green"]
C_BTN = "bold white on grey27"   # button at rest
C_BTN_LAST = "bold grey85 on grey19"  # group's last-focused button (subtle)
C_BTN_SEL = "bold black on orange1"  # button focused (the cursor, but "glows")
C_STATE = {
    # Match the PSMux/TMux status segment (_SEGMENT_STYLE) so the picker and the
    # status bar use one vocabulary + palette (test-chamber #1290).
    "DIRTY": "#d70000",    # red (colour160)
    "WIP": "#d7af00",      # amber (colour178)
    "FINAL": "#00af00",    # green (colour034) -- COMPLETED, refreshed + settled
    "MERGED": "#ff8700",   # orange (colour208) -- COMPLETED but not yet
                           # provably settled (Phase 5 closure descriptor);
                           # distinct from WIP's amber so "landed but not
                           # closed out" never reads as "still being written".
    "UNUSED": "grey58",    # grey (colour244)
    "CONVO": "#00afaf",    # teal (colour037) -- UNUSED + conversation
    "ORPHAN": "#af00ff",   # magenta (colour129)
    "ACTIVE": "#00afff",   # blue (colour039)
    "GONE": "grey35",      # dark grey (colour238)
    # #3307 Phase 5: folded in from the retired "R" column's HANDOFF glyph --
    # a worktree whose reciprocal_relation reports "handed-off" and has no
    # live successor session yet.
    "HANDOFF": "#8787ff",  # soft blue-violet (colour111) -- distinct from
                           # ACTIVE's blue and WIP's amber
    "?": "grey35",
}

# Animated SSH-connect spinner (braille "dots going around").
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
C_READY = "green"
C_LOAD = "yellow"
C_DISABLED = "grey37"
# Environment colors, echoing the PSMux/TMux status region:
# Win = blue, WSL = purple, Linux = orange. Truecolor HEX (not ANSI names) so a
# terminal's palette theme can't remap blue->purple / magenta->red.
C_ENV = {"Win": "#4aa3ff", "WSL": "#b96bff", "Linux": "#ff9e3b"}
# Maintenance disposition (blended verdict+reason) -> colored chip.
# Green = positive/safe-to-go; yellow = needs review; red = broken/blocked.
C_DISPO = {"SAFE": "black on green", "REVIEW": "black on yellow",
           "UNSAFE": "white on red3"}
DISPO_MARK = {"SAFE": "✓", "REVIEW": "!", "UNSAFE": "✗"}


# ---- key canonicalization ---------------------------------------------------
# Textual delivers some keys under framework-specific names/aliases; fold them
# to ONE canonical token here so the render methods match keys declaratively and
# the framework's naming quirks stay localized (#88 F2). This is the seam a
# later move to Textual ``BINDINGS`` (F3) builds on.
KEY_ALIASES = {
    "left_square_bracket": "[",
    "right_square_bracket": "]",
    "slash": "/",  # Textual names the "/" key "slash" (#2228 Phase 4 command bar).
    "question_mark": "?",  # Textual names the "?" key "question_mark" (Phase 5
    # Legend screen).
    # Ctrl+Space is NUL, which Textual surfaces as "ctrl+at"; the picker treats
    # it as a Ctrl-held Space toggle, identical to "ctrl+space".
    "ctrl+at": "ctrl+space",
}


def canonical_key(key):
    """Fold a Textual key name to the picker's canonical token (identity for a
    key with no alias)."""
    return KEY_ALIASES.get(key, key)
