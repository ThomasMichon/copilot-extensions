"""Mux Companion prototype (visions/mux-companion).

A throwaway experiment, not the real Companion: launched inside a psmux
``display-popup -E`` pane to find out -- empirically -- how such a popup gets
exited (a rebound hotkey? Escape? Ctrl+C? psmux's own popup-close handling?)
and how keyboard focus behaves in that environment, before committing to a
design for the real hotkey-summoned status/lineage/override dialog.

Deliberately minimal: an obvious border and a single Exit button, no other
key bindings of its own -- so whatever closes it is either the Exit button or
default Textual/psmux behavior, not something this prototype pre-decided.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Center, Middle
from textual.widgets import Button, Footer, Static


class MuxCompanionPrototype(App):
    """Minimal shell: a thick border and one Exit button, nothing else."""

    CSS = """
    Screen {
        border: heavy red;
        background: $panel;
        align: center middle;
    }

    #exit-btn {
        width: 24;
    }
    """

    def compose(self) -> ComposeResult:
        with Center():
            with Middle():
                yield Static("Mux Companion prototype", id="title")
                yield Button("Exit", id="exit-btn", variant="error")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "exit-btn":
            self.exit()


def run() -> int:
    MuxCompanionPrototype().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
