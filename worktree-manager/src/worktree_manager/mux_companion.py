"""Mux Companion (visions/mux-companion).

A hotkey-summoned, read-only in-session view for the **current worktree** --
resolved from the process's own cwd, the same way the status bar / status
core resolve it (``agent-worktrees status-segment --json``, the same
non-daemon classify pass the mux bar itself uses). It explains the worktree's
status in plain language and lists its session lineage with the current head
clearly marked.

v1 is deliberately view-only: no session switching, resume, or head override
lives here yet. That is a distinct, later feature
(visions/mux-companion §Features/break-glass-head-override) and must not be
folded in silently -- see the vision's Non-Goals.

Reaches the ``agent-worktrees`` engine only through the same process-boundary
``engine_client`` module the Picker uses (subprocess + JSON, never an
``import`` of the plugin) -- see ``engine_client``'s own module docstring.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header, Static

from . import engine_client as ec

#: Blocker code (from the closure descriptor's ``blockers`` list) -> a short,
#: plain-language reason. Mirrors the vocabulary in
#: ``plugins/agent-worktrees/src/agent_worktrees/prune.py``'s
#: ``_BUCKET_TO_BLOCKER`` (kept independent, not imported, per the
#: process-boundary rule above).
_BLOCKER_REASONS: dict[str, str] = {
    "finalizing": "a finalize is currently in progress",
    "held-claims": "one or more held resource claims",
    "open-follow-ups": "one or more open follow-ups",
    "open-pr": "its pull request is still open",
    "closed-unmerged": "its pull request closed without merging",
    "unmerged": "commits not yet on the default branch",
    "dirty": "uncommitted changes",
    "wip": "commits ahead not yet pushed upstream",
    "claimed-live": "a live claimant process",
    "paired-pending": "a paired sibling worktree is not yet settled",
}

#: Raw ``state`` value (from ``status-segment --json``) -> a plain-language
#: sentence, for states the closure descriptor does not itself explain
#: (``completed`` is handled separately via FINAL/MERGED below).
_STATE_EXPLAINERS: dict[str, str] = {
    "dirty": "Uncommitted changes are present in the working tree.",
    "wip": "Clean; ahead with commits not yet on the default branch.",
    "unused": "Clean; no commits and no conversation since the fork point.",
    "convo": "Clean; no commits, but the session held conversation turns.",
    "orphan": "No merge base with the upstream default branch.",
    "gone": "The worktree directory is missing.",
    "active": "A live Copilot session currently owns this worktree.",
}

#: State/closure label -> display color. Mirrors the Picker's own
#: ``picker_tui.engine.C_STATE`` palette (and the status bar's
#: ``_SEGMENT_STYLE``/``_DESCRIPTOR_STYLE_BG``) so the Companion reads with
#: the same vocabulary+color the operator already sees in Mux and the
#: Worktree Manager table -- declared independently here (not imported) per
#: the process-boundary rule above; kept in sync by inspection, the same way
#: the Manager's transplanted ``picker_tui`` copies are.
_STATE_COLOR: dict[str, str] = {
    "DIRTY": "#d70000",
    "WIP": "#d7af00",
    "FINAL": "#00af00",
    "MERGED": "#ff8700",
    "UNUSED": "grey58",
    "CONVO": "#00afaf",
    "ORPHAN": "#af00ff",
    "ACTIVE": "#00afff",
    "GONE": "grey35",
}
_STATE_COLOR_DEFAULT = "grey35"


@dataclass
class _CompanionData:
    """Everything the view needs, fetched once at startup."""

    worktree: dict = field(default_factory=dict)
    sessions: list[dict] = field(default_factory=list)
    error: str | None = None


def _load_current_worktree(cwd: str | None = None) -> _CompanionData:
    """Resolve the worktree containing ``cwd`` (default: the real cwd) and
    fetch its status + session lineage.

    Uses the cheap, non-daemon ``status-segment --json`` snapshot (see
    :func:`engine_client.current_worktree_status`) rather than
    ``list --json --classify --worktree-id``, which still pays the resident
    classify daemon's whole-fleet negotiation cost even when scoped to one
    id. Degrades gracefully: a resolved worktree with no registered sessions
    (or an older engine that can't list them) still renders its status with
    an empty lineage section rather than failing outright.
    """
    target = cwd if cwd is not None else os.getcwd()
    try:
        payload = ec.current_worktree_status(path=target)
    except ec.EngineError as exc:
        return _CompanionData(error=str(exc))
    if payload.get("error"):
        return _CompanionData(error=str(payload["error"]))

    worktree_id = payload.get("id")
    sessions: list[dict] = []
    if worktree_id:
        try:
            sessions = ec.list_worktree_sessions(None, worktree_id)
        except ec.EngineError:
            pass  # lineage stays empty; the status section still renders

    return _CompanionData(worktree=payload, sessions=sessions)


def _fallback_label(row: dict) -> str:
    """A best-effort state label when no closure descriptor is available
    (an older engine, or an untracked/unregistered worktree)."""
    state = str(row.get("state") or "").lower()
    status = str(row.get("status") or "").lower()
    if state == "completed" or status == "finalized":
        return "MERGED"
    return (state or status or "unknown").upper()


def _closure_explanation(row: dict) -> list[str]:
    """Plain-language lines explaining the worktree's current status."""
    closure = row.get("closure") or {}
    state = str(row.get("state") or "").lower()
    label = closure.get("label") or _fallback_label(row)
    lines: list[str] = []

    if label == "FINAL":
        lines.append(
            "FINAL \u2014 refreshed evidence confirms this worktree is fully "
            "landed on the default branch, with no held claims or open "
            "follow-ups. Safe to clean up."
        )
    elif label == "MERGED":
        lines.append(
            "MERGED \u2014 its content has landed on the default branch, but "
            "it is not yet proven safe to clean up:"
        )
        if closure.get("evidence_mode") == "cached":
            lines.append(
                "  \u2022 the evidence is cached/fetch-free (a fresh --fetch "
                "is required to prove FINAL)"
            )
        blockers = closure.get("blockers")
        if isinstance(blockers, list):
            for blocker in blockers:
                if not isinstance(blocker, dict):
                    continue
                code = blocker.get("code")
                count = blocker.get("count")
                reason = _BLOCKER_REASONS.get(code, str(code or "an unrecognized blocker"))
                if isinstance(count, int) and count > 1:
                    lines.append(f"  \u2022 {reason} ({count})")
                else:
                    lines.append(f"  \u2022 {reason}")
    elif state in _STATE_EXPLAINERS:
        lines.append(_STATE_EXPLAINERS[state])
    else:
        lines.append(f"State: {label or state or 'unknown'}")

    ahead = row.get("ahead") or 0
    behind = row.get("behind") or 0
    if ahead or behind:
        tags = []
        if ahead:
            tags.append(f"{ahead} ahead")
        if behind:
            tags.append(f"{behind} behind")
        lines.append(f"Sync vs. upstream: {', '.join(tags)}.")
    return lines


class MuxCompanionApp(App):
    """Read-only status + session-lineage view for the CURRENT worktree.

    View-only by design (v1): explains status, lists lineage, marks the head
    -- no resume/switch/override action lives here yet. Styled to match the
    Worktree Manager's own Picker palette (grey/orange chrome, per-state
    colors) rather than Textual's default theme.
    """

    CSS = """
    Screen {
        background: #1a1a1a;
        border: round grey42;
    }

    Header {
        background: grey19;
        color: white;
        text-style: bold;
    }

    #status {
        height: auto;
        padding: 1 2;
    }

    #lineage-label {
        padding: 0 2;
        color: grey70;
        text-style: bold;
    }

    #lineage {
        height: 1fr;
        margin: 0 2;
        background: #1a1a1a;
    }

    #footer-row {
        height: 3;
        align: center middle;
    }

    #exit-btn {
        width: 24;
        background: grey27;
        color: white;
        border: none;
    }

    #exit-btn:focus {
        background: #d78700;
        color: black;
        text-style: bold;
    }
    """

    def __init__(self, cwd: str | None = None) -> None:
        super().__init__()
        self._data = _load_current_worktree(cwd)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(self._status_text(), id="status")
        if not self._data.error:
            yield Static(
                "Session lineage (\u25cf marks the current head):",
                id="lineage-label",
            )
            yield DataTable(id="lineage")
        with Horizontal(id="footer-row"):
            yield Button("Exit", id="exit-btn")
        yield Footer()

    def on_mount(self) -> None:
        row = self._data.worktree
        self.title = str(row.get("id") or "Mux Companion")
        repo = row.get("repo") or "?"
        branch = row.get("branch") or "?"
        self.sub_title = f"{repo} \u00b7 {branch}"
        if self._data.error:
            return
        table = self.query_one("#lineage", DataTable)
        table.cursor_type = "row"
        table.add_columns("", "Session", "State", "Turns", "Updated")
        for row in self._data.sessions:
            sid = str(row.get("id") or "")
            short_id = sid if len(sid) <= 12 else f"{sid[:10]}\u2026"
            table.add_row(
                "\u25cf" if row.get("is_head") else "",
                short_id,
                str(row.get("state") or "active"),
                str(row.get("turn_count") or 0),
                str(row.get("updated_at") or "")[:19],
                key=sid or None,
            )

    def _status_text(self) -> Text:
        text = Text()
        if self._data.error:
            text.append(self._data.error, style="bold red")
            return text
        row = self._data.worktree
        closure = row.get("closure") or {}
        label = closure.get("label") or _fallback_label(row)
        color = _STATE_COLOR.get(label, _STATE_COLOR_DEFAULT)
        text.append(f"{label}\n", style=f"bold {color}")
        for line in _closure_explanation(row):
            text.append(line + "\n")
        return text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "exit-btn":
            self.exit()


def run() -> int:
    MuxCompanionApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
