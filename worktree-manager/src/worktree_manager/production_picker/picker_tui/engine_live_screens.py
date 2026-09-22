#!/usr/bin/env python3
"""Live modal picker screens extracted from ``engine.py``."""
from __future__ import annotations

from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

from .engine_helpers import (
    C_BAND,
    C_BTN,
    C_BTN_SEL,
    C_CAUTION,
    C_DIM,
    C_FAINT,
    C_HEADER,
    C_LABEL,
    C_LOAD,
    C_META,
    C_MUTED,
    C_READY,
    C_WARN,
    canonical_key,
)

class ProgressScreen(ModalScreen[None]):
    """Native modal maintenance/profiles progress run (#88 F4).

    Renders the engine's ``progress`` sub-dialog -- each selected worktree
    (or profiles host column) as a row that advances pending(·) ->
    running(spinner) -> done(✓)/failed(✗) -- and, unlike the other
    (static) migrated overlays, is **live**: an ``on_mount`` interval ticks
    the run forward (the mock walker, or a real ``MaintenanceExecutor``
    poll) and repaints. Mirrors the former ``_key_progress`` exactly: an
    unarmed run shows the beyond-clean confirm gate (Enter proceeds/arms,
    Esc cancels), a done run closes on Enter/Esc.

    The run's state lives on the engine (``eng.progress``/``eng.executor``),
    because several entry points build it (``_confirm_cleanup``,
    ``_run_op_progress``, ``_start_profiles_run``) and the state-transition
    core (``_advance_progress``/``_key_progress``) stays unit-tested there.
    This screen is the native shell that drives and renders that state,
    dismissing itself once the engine clears ``progress``.
    """

    CSS = """
    ProgressScreen { align: center middle; background: $background 55%; }
    ProgressScreen > #progress { width: auto; height: auto; max-height: 90%; }
    """

    def __init__(self, eng) -> None:
        super().__init__()
        self._eng = eng

    def compose(self) -> ComposeResult:
        yield Static(self._panel(), id="progress")

    def on_mount(self) -> None:
        # Drive the run forward on our own interval (~10 fps), matching the pace
        # the background tick used to advance the mock walker.
        self.set_interval(0.1, self._on_tick)

    def _on_tick(self) -> None:
        eng = self._eng
        if eng.progress is None:
            # A direct engine poke (e.g. a unit test) cleared the run out from
            # under us -- close if we're still the top screen.
            if self.app.screen is self:
                self.dismiss(None)
            return
        if not eng.progress["done"]:
            eng._advance_progress()
        self._refresh()

    def _panel(self) -> Panel:
        eng = self._eng
        p = eng.progress
        if p is None:
            # The run was cleared before this screen finished mounting (e.g. a
            # direct engine poke in a unit test). Render empty; the interval
            # dismisses us on its next tick.
            return Panel(Text(""), border_style=C_DIM, width=68)
        if p.get("kind") == "action-stream":
            return self._action_stream_panel(p)
        items = p["items"]
        done = sum(1 for it in items if it["state"] in ("done", "failed"))
        failed = sum(1 for it in items if it["state"] == "failed")
        verb = p["verb"]
        body = Text()
        # Status sub-line: confirm gate / done / working.
        if not p.get("armed", True):
            n = len(items)
            extra = []
            if p.get("include_unused"):
                extra.append("unused")
            if p.get("include_conversations"):
                extra.append("conversation")
            tail = f" incl. {'/'.join(extra)}" if extra else ""
            sub = (f" ⚠ {verb.lower()} {n} worktree(s){tail}? "
                   "Enter=proceed Esc=cancel")
            substyle = "bold yellow"
        elif p["done"]:
            sub = f" done · {done}/{len(items)}" + (
                f" · {failed} failed" if failed else "")
            substyle = C_HEADER
        else:
            sub = f" {eng.spin()} working… {done}/{len(items)}"
            substyle = C_HEADER
        body.append(sub + "\n\n", style=substyle)
        # Item rows, windowed around the running item when the list is long.
        maxr = 12
        run = next((j for j, it in enumerate(items)
                    if it["state"] == "running"), len(items) - 1)
        lo = max(0, min(run - maxr // 2, max(0, len(items) - maxr)))
        for it in items[lo:lo + maxr]:
            st = it["state"]
            if st == "done":
                g, gc = "✓", C_READY
            elif st == "failed":
                g, gc = "✗", C_WARN
            elif st == "running":
                g, gc = eng.spin(), C_LOAD
            else:
                g, gc = "·", C_DIM
            row = Text("  ")
            row.append(g, style=gc)
            row.append(f" {it['id4']} ", style=C_META)
            title = it["title"]
            if len(title) > 46:
                title = title[:45] + "…"
            row.append(title, style="white" if st != "pending" else C_MUTED)
            body.append_text(row)
            body.append("\n")
        if len(items) > maxr:
            body.append(f" … {len(items)} total\n", style=C_MUTED)
        if p.get("op") == "profiles" and p["done"]:
            # #1368: after Apply the fragment is regenerated, but the terminal
            # app only re-reads it on a FULL restart -- spell out what changed
            # and that a restart is required so a "no visible change" reads as
            # expected, not as a silent failure.
            na, nr = p.get("n_add", 0), p.get("n_rem", 0)
            body.append(f"\n +{na} added · -{nr} removed\n", style=C_LABEL)
            body.append(
                " ⚠ Fully restart the terminal app (close all its windows) to "
                "see the changes.\n", style=C_CAUTION)
        # Button row.
        body.append("\n")
        btns = Text(" ")
        if not p.get("armed", True):
            btns.append(" Confirm ", style=C_BTN_SEL)
            btns.append("  ")
            btns.append(" Cancel ", style=C_BTN)
        elif p["done"]:
            btns.append(" Close ", style=C_BTN_SEL)
        else:
            btns.append(" Working… ", style=C_BTN)
        body.append_text(btns)
        return Panel(body, title=f"{verb} · {p['scope']}",
                     border_style=C_BAND, width=68)

    def _action_stream_panel(self, p) -> Panel:
        """Render a D4 progress-reporting action's live state: verb, latest
        message, an optional pct bar, and a Working…/Close button."""
        eng = self._eng
        verb = p.get("verb", "Action")
        body = Text()
        if p.get("done"):
            if p.get("error"):
                body.append(f" ✗ failed · {str(p['error'])[:60]}\n\n", style=C_WARN)
            else:
                body.append(" ✓ done\n\n", style=C_READY)
        else:
            body.append(f" {eng.spin()} working…\n\n", style=C_HEADER)
        title = str(p.get("title") or "")
        if title:
            body.append(f" {title[:60]}\n", style=C_META)
        pct = p.get("pct")
        if isinstance(pct, (int, float)):
            filled = round(max(0.0, min(100.0, pct)) / 100 * 40)
            bar = Text(" [")
            bar.append("█" * filled, style=C_READY)
            bar.append("·" * (40 - filled), style=C_DIM)
            bar.append(f"] {pct:5.1f}%")
            body.append_text(bar)
            body.append("\n")
        msg = str(p.get("msg") or "")
        if msg:
            body.append(f" {msg[:62]}\n", style="white")
        body.append("\n")
        btn = Text(" ")
        btn.append(" Close " if p.get("done") else " Cancel ",
                   style=C_BTN_SEL if p.get("done") else C_BTN)
        body.append_text(btn)
        return Panel(body, title=verb, border_style=C_BAND, width=68)

    def _refresh(self) -> None:
        if self._eng.progress is not None:
            self.query_one("#progress", Static).update(self._panel())

    def on_key(self, event) -> None:
        event.stop()
        eng = self._eng
        # Delegate the state transition to the engine's tested core, then close
        # once it clears the run (unarmed-cancel / done-close) or refresh in
        # place (unarmed-arm keeps the dialog up, now working).
        eng._key_progress(canonical_key(event.key))
        if eng.progress is None:
            self.dismiss(None)
        else:
            self._refresh()

class MsgViewScreen(ModalScreen[None]):
    """Native modal recent-messages viewer (#88 F4) -- the last overlay migrated.

    A read-only peek at a worktree's latest-session conversation tail plus
    its session registry (every session's FULL id + title, so the operator
    can copy an id out for a manual ``copilot --resume <id>``). Like
    ``ProgressScreen`` it is **live**: the payload loads on a daemon thread
    (``_msgview_worker``) that populates the engine-owned ``self.msgview``
    dict under a lock, and an ``on_mount`` interval repaints while
    ``loading`` (plus once more on the loading -> loaded transition) so the
    result appears promptly. Mirrors the former ``_key_msgview`` exactly
    (↑/↓ scroll; Esc/q/Tab/Enter close).

    The state + loader stay on the engine because the worker references
    ``self.msgview`` by identity (a late result for a viewer the operator
    already closed/reopened is dropped). This screen is the native shell
    that renders and scrolls that state, dismissing itself once the engine
    clears it.
    """

    CSS = """
    MsgViewScreen { align: center middle; background: $background 55%; }
    MsgViewScreen > #msgview { width: auto; height: auto; max-height: 90%; }
    """

    def __init__(self, eng) -> None:
        super().__init__()
        self._eng = eng
        self._loading_last = True

    def compose(self) -> ComposeResult:
        yield Static(self._panel(), id="msgview")

    def on_mount(self) -> None:
        self.set_interval(0.1, self._on_tick)

    def _on_tick(self) -> None:
        mv = self._eng.msgview
        if mv is None:
            # A direct engine poke (e.g. a unit test) cleared the viewer out from
            # under us -- close if we're still the top screen.
            if self.app.screen is self:
                self.dismiss(None)
            return
        loading = bool(mv.get("loading"))
        # Repaint while the loader thread is resolving, plus exactly one final
        # repaint on the loading -> loaded transition so the result renders; a
        # settled viewer is static, so we then stop churning (scroll keys
        # repaint synchronously in on_key).
        if loading or self._loading_last:
            self._refresh()
        self._loading_last = loading

    def _refresh(self) -> None:
        if self._eng.msgview is not None:
            self.query_one("#msgview", Static).update(self._panel())

    def _panel(self) -> Panel:
        eng = self._eng
        mv = eng.msgview
        if mv is None:
            # Cleared before this screen finished mounting; render empty and let
            # the interval dismiss us on its next tick.
            return Panel(Text(""), border_style=C_DIM, width=92)
        rec = mv["rec"]
        pw = 88
        title = f" {rec.get('title', '')}"
        meta = (f" {rec.get('id4')} · {rec.get('machine')} · {rec.get('env')}"
                f" · {rec.get('state')}")

        # Assemble the scrollable body, then window it by the scroll offset.
        body: list[Text] = []
        # Sessions section (diagnostic): every session's FULL id + title so the
        # operator can copy an id out (terminal selection) to resume it by hand.
        # The head is marked ``● current``.
        sess = mv.get("sessions") or []
        if sess:
            body.append(Text(" Sessions (select an id to copy):", style=C_HEADER))
            for s in sess:
                sid = str(s.get("id", ""))
                is_head = bool(s.get("is_head"))
                marker = "●" if is_head else "○"
                mark_style = "bold #7ee787" if is_head else C_DIM
                tag = " current" if is_head else ""
                state = str(s.get("state", "") or "")
                if state and state != "active":
                    tag = f" {state}"
                body.append(Text(f" {marker} {sid}{tag}", style=mark_style))
                stitle = str(s.get("name", "") or "").strip()
                title_row = f"     {stitle}" if stitle else "     (untitled)"
                for seg in eng._wrap_text(title_row, pw - 2):
                    body.append(Text(seg, style="white" if stitle else C_FAINT))
            body.append(Text(""))
            body.append(Text(" Recent messages (current session):",
                             style=C_HEADER))

        if mv.get("loading"):
            spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[(eng.frame // 2) % 10]
            body.append(Text(f" {spin} Loading recent messages…", style=C_FAINT))
        elif mv.get("error"):
            body.append(Text(f" ⚠ {mv['error']}", style=C_CAUTION))
        elif not mv.get("messages"):
            body.append(Text(" (no conversation messages in the latest session)",
                             style=C_FAINT))
        else:
            for m in mv["messages"]:
                is_user = m.get("role") == "user"
                who = "you" if is_user else "agent"
                who_style = "bold #4aa3ff" if is_user else "bold #7ee787"
                body.append(Text(f" ▍ {who}", style=who_style))
                for seg in eng._wrap_text(m.get("text", ""), pw - 5):
                    body.append(Text("   " + seg, style="white"))
                body.append(Text(""))

        avail = 18
        total = len(body)
        max_scroll = max(0, total - avail)
        scroll = min(mv.get("scroll", 0), max_scroll)
        mv["scroll"] = scroll
        window = body[scroll:scroll + avail]

        out = Text()
        out.append(title + "\n", style=C_HEADER)
        out.append(meta + "\n\n", style=C_DIM)
        for ln in window:
            out.append_text(ln)
            out.append("\n")
        if total > avail:
            more = total - avail - scroll
            out.append("   … %d more line(s) below" % more if more > 0
                       else "   (end)", style=C_DIM)
            out.append("\n")
        out.append("\n")
        sid = mv.get("session_id") or ""
        foot = " Esc close · ↑/↓ scroll"
        if sid:
            # Full id (not truncated) so terminal selection copies a usable
            # `copilot --resume <id>` argument.
            foot = f" current session {sid} ·" + foot
        out.append(foot, style=C_FAINT)
        return Panel(out, title="Recent messages", border_style=C_BAND, width=92)

    def on_key(self, event) -> None:
        event.stop()
        eng = self._eng
        # Delegate the scroll/close transition to the engine's tested core, then
        # close once it clears the viewer or repaint the new scroll position.
        eng._key_msgview(canonical_key(event.key))
        if eng.msgview is None:
            self.dismiss(None)
        else:
            self._refresh()
