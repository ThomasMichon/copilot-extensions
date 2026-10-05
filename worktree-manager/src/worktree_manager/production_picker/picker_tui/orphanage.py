#!/usr/bin/env python3
"""Read-only "Orphanage" screen for the Worktrees list (worktree-claims-
transitive-finalization Phase 4 item 2), split into its own module per the
same 1000-line module-size cap ``engine_legend.py`` is split out for.

Surfaces the local machine's durable claims-orphanage -- obligations
re-homed by an ``--abandon`` finalize, awaiting ``agent-worktrees claims
cleanup``. A re-homed obligation has no worktree row of its own to render
on (its source worktree is already gone), which is exactly why this is a
fleet-wide/screen-level surface rather than a per-row marker -- see
``PickerScreenRuntimeMixin._poll_orphan_state`` for how ``self._orphans``
is populated and why it is deliberately local-machine-only."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .engine_helpers import C_DIM, C_HEADER, C_LABEL, C_WARN


class OrphanageScreen(ModalScreen[None]):
    """Lists every entry in ``screen._orphans`` (the cached local
    claims-orphanage). Read-only, like ``LegendScreen`` -- cleanup itself
    stays a deliberate CLI action (``claims cleanup``), never a Picker
    one-keystroke shortcut, since it can release a claim another process
    still depends on and the CLI's own review/``--apply`` split exists for
    exactly that reason."""

    CSS = """
    OrphanageScreen { align: center middle; background: $background; }
    OrphanageScreen > #orphanage-frame {
        width: 92; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    OrphanageScreen #orphanage-body { height: auto; max-height: 100%; }
    OrphanageScreen #orphanage-foot { color: grey; height: 1; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, orphans: list[dict]) -> None:
        super().__init__()
        self._orphans = list(orphans)

    def compose(self) -> ComposeResult:
        with Vertical(id="orphanage-frame"):
            with VerticalScroll(id="orphanage-body"):
                yield Static(self._body())
            yield Static(" Esc/Enter: close", id="orphanage-foot")

    def _body(self) -> Text:
        t = Text()
        t.append("Local claims-orphanage\n", style=C_HEADER)
        if not self._orphans:
            t.append("  (none)\n", style=C_DIM)
            return t
        t.append(
            f"  {len(self._orphans)} obligation(s) re-homed by an "
            "--abandon finalize, awaiting cleanup:\n",
            style=C_DIM,
        )
        for entry in self._orphans:
            kind = entry.get("kind") or "?"
            ref = entry.get("ref") or "?"
            t.append(f"\n  · {kind}: ", style=C_WARN)
            t.append(f"{ref}\n", style=C_LABEL)
            src = entry.get("source_worktree")
            when = entry.get("abandoned_at")
            meta = ", ".join(
                x for x in (f"from {src}" if src else "",
                            f"@ {when}" if when else "") if x
            )
            if meta:
                t.append(f"    {meta}\n", style=C_DIM)
            handoff = entry.get("handoff_to")
            if handoff:
                t.append(f"    -> handoff: {handoff}\n", style=C_DIM)
            note = entry.get("note")
            if note:
                t.append(f"    {note}\n", style=C_DIM)
        t.append(
            "\n  `agent-worktrees claims orphans` / `claims cleanup` to act.\n",
            style=C_DIM,
        )
        return t

    def action_close(self) -> None:
        self.dismiss(None)
