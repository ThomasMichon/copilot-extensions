#!/usr/bin/env python3
"""The Worktrees list's read-only "Legend" reference card (worktree-
finality-and-obligations Phase 5), split into its own module to keep
``engine_dialogs.py`` under the repo's 1000-line module-size cap."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from . import derive
from .engine_helpers import C_DIM, C_DISPO, C_HEADER, C_LABEL, C_STATE, C_WARN, DISPO_MARK


class LegendScreen(ModalScreen[None]):
    """Read-only "Legend" reference card: explains the Worktrees list's state
    labels, closure-descriptor compact markers, and maintenance disposition
    chips in one place, reusing the SAME canonical vocabulary every row is
    actually rendered from (``styles.C_STATE``, ``derive._STATUS_MARKER_TEXT``/
    ``describe_status_marker``, ``styles.C_DISPO``/``DISPO_MARK``) --
    presentation only, no new classification logic. Opens instantly (static
    content, no gather/IO), mirroring ``engine_dialogs.WtDetailsScreen``."""

    CSS = """
    LegendScreen { align: center middle; background: $background 55%; }
    LegendScreen > #legend-frame {
        width: 84; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    LegendScreen #legend-body { height: auto; max-height: 100%; }
    LegendScreen #legend-foot { color: grey; height: 1; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    #: (label, one-line meaning) pairs, in the same order an operator meets
    #: them scanning the Worktrees list top to bottom (live first, then
    #: settled/blocked, then idle/gone) -- not `C_STATE`'s own dict order.
    _STATE_MEANINGS = [
        ("ACTIVE", "a live Copilot session (or attached mux) owns this worktree"),
        ("DIRTY", "uncommitted changes present"),
        ("WIP", "committed work not yet merged to the default branch"),
        ("FINAL", "completed, upstream-settled, and provably safe to prune"),
        ("MERGED", "completed but blocked -- see its compact markers below"),
        ("CONVO", "idle, no commits, but the session holds conversation turns"),
        ("UNUSED", "idle -- no commits, no conversation turns"),
        ("ORPHAN", "no merge base found against the default branch"),
        ("HANDOFF", "handed off; no live successor session yet"),
        ("GONE", "worktree directory no longer exists on disk"),
    ]

    #: (token pattern, meaning) pairs for the compact marker suffix
    #: (`status_markers` -- e.g. "C1 F2 U* OC*") every MERGED/blocked row
    #: renders after its base label.
    _MARKER_MEANINGS = [
        ("C<N>", "N held resource claim(s) pending (codespace, container, ...)"),
        ("F<N>", "N open follow-up(s) pending"),
        ("U*", derive._STATUS_MARKER_TEXT["U*"]
         + " -- evidence isn't a fresh fetch, never authorizes FINAL"),
        ("OC*", derive._STATUS_MARKER_TEXT["OC*"]
         + " -- claim/follow-up evidence isn't fresh, never authorizes FINAL"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="legend-frame"):
            with VerticalScroll(id="legend-body"):
                yield Static(self._body())
            yield Static(" Esc/Enter: close", id="legend-foot")

    def _body(self) -> Text:
        t = Text()
        t.append("Worktree state\n", style=C_HEADER)
        for label, meaning in self._STATE_MEANINGS:
            color = C_STATE.get(label, "")
            t.append(f"  {label:<8}", style=color or None)
            t.append(f" {meaning}\n", style=C_DIM)
        t.append("\nCompact markers (after the state label)\n", style=C_HEADER)
        for token, meaning in self._MARKER_MEANINGS:
            t.append(f"  {token:<8}", style=C_WARN if "*" in token else C_LABEL)
            t.append(f" {meaning}\n", style=C_DIM)
        t.append("\nMaintenance disposition\n", style=C_HEADER)
        for level in ("SAFE", "REVIEW", "UNSAFE"):
            t.append(f"  {DISPO_MARK[level]} {level:<8}", style=C_DISPO[level])
            t.append("\n")
        return t

    def action_close(self) -> None:
        self.dismiss(None)
