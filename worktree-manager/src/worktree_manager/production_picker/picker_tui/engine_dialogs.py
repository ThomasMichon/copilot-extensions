#!/usr/bin/env python3
"""Modal picker dialogs extracted from ``engine.py``."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, SelectionList, Static

from . import derive
from .engine_focus import FocusGroup, _ScopeSelectionList
from .engine_helpers import (
    ACTION_DESC,
    C_CAUTION,
    C_DIM,
    C_FAINT,
    C_HEADER,
    C_LABEL,
    C_META,
    C_MUTED,
    C_READY,
    SPINNER,
    C_WARN,
    MAINT_ACTION_DESC,
)

class QuitConfirmScreen(ModalScreen[bool]):
    """Native modal confirm for Esc/q on a top-level picker view (#88 F4;
    native-focus internals NF1).

    Textual owns the screen stack, the dim backdrop, and key routing; the
    screen returns its verdict via ``dismiss(bool)`` -- ``True`` quits,
    ``False`` stays.

    **Native-focus internals (#88 NF1):** the ``[Quit] [Stay]`` row is a
    :class:`FocusGroup` (one tab-stop; ◀▶ to choose, Enter/Space to
    activate). *Stay* is the group's initial choice, so a reflexive Enter
    never quits. The y/n/Esc/q shortcuts stay as screen ``BINDINGS`` so
    they fire regardless of child focus.
    """

    CSS = """
    QuitConfirmScreen { align: center middle; background: $background 55%; }
    QuitConfirmScreen > #quit-frame {
        width: 48; height: auto; border: round #ffaf00;
        background: $surface; padding: 1 2;
    }
    QuitConfirmScreen #quit-prompt { height: auto; padding: 0 0 1 0; }
    QuitConfirmScreen FocusGroup { height: auto; }
    QuitConfirmScreen #quit-hint { color: grey; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("y", "quit", show=False),
        Binding("n", "stay", show=False),
        Binding("escape", "stay", show=False),
        Binding("q", "stay", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="quit-frame"):
            yield Static(Text(" Leave the worktree picker?", style=C_HEADER),
                         id="quit-prompt")
            yield FocusGroup([("quit", "Quit"), ("stay", "Stay")], initial=1,
                             id="quit-buttons")
            yield Static("y quit · n/Esc stay · ←/→ choose", id="quit-hint")

    def on_mount(self) -> None:
        self.query_one("#quit-frame", Vertical).border_title = "Quit the Picker?"
        # The FocusGroup composes its own child, so it may not be mounted yet at
        # screen on_mount; defer the focus until the layout settles.
        self.call_after_refresh(
            lambda: self.query_one("#quit-buttons", FocusGroup).focus())

    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        self.dismiss(event.value == "quit")

    def action_quit(self) -> None:
        self.dismiss(True)

    def action_stay(self) -> None:
        self.dismiss(False)

class ProfConfirmScreen(ModalScreen[bool]):
    """Native modal confirm for a Profiles *Apply* (#88 F4; native-focus
    internals NF1).

    Lists the exact terminal profiles each changed host column will gain
    (``+``) or lose (``-``) before anything is written, and returns its
    verdict via ``dismiss(True|False)`` -- ``True`` runs the per-host
    Apply, ``False`` cancels. The regeneration is destructive to the
    terminal app's profile list, so the diff is always shown first.

    **Native-focus internals (#88 NF1):** the ``[Apply] [Cancel]`` row is a
    :class:`FocusGroup` (one tab-stop; ◀▶ to choose, Enter/Space to
    activate). Esc/q cancel via ``BINDINGS``.
    """

    CSS = """
    ProfConfirmScreen { align: center middle; background: $background 55%; }
    ProfConfirmScreen > #prof-frame {
        width: 72; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    ProfConfirmScreen #prof-diff { height: auto; }
    ProfConfirmScreen #prof-warn { height: auto; padding: 1 0 0 0; }
    ProfConfirmScreen FocusGroup { height: auto; padding: 1 0 0 0; }
    ProfConfirmScreen #prof-hint { color: grey; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
    ]

    def __init__(self, cf, host_cols) -> None:
        super().__init__()
        self._cf = cf
        self._host_cols = host_cols

    def compose(self) -> ComposeResult:
        with Vertical(id="prof-frame"):
            yield Static(self._diff_body(), id="prof-diff")
            yield Static(Text(" ⚠ Regenerates terminal profiles · fully restart "
                              "the terminal to see changes.", style=C_CAUTION),
                         id="prof-warn")
            yield FocusGroup([("apply", "Apply"), ("cancel", "Cancel")],
                             initial=0, id="prof-buttons")
            yield Static("Enter apply · Esc cancel", id="prof-hint")

    def _diff_body(self) -> Text:
        cf = self._cf
        changed = cf["changed"]
        n_add = sum(len(cf["diffs"][hi][0]) for hi in changed)
        n_rem = sum(len(cf["diffs"][hi][1]) for hi in changed)

        def _t(s):
            return f"{s.machine} {s.env} · {s.kind}"

        # Cap the visible diff rows so a very large change set never overruns the
        # terminal (mirrors the former overlay's ``maxr`` guard); Textual sizes
        # the rest via ``max-height`` + auto.
        try:
            maxr = max(4, self.app.size.height - 12)
        except Exception:
            maxr = 24

        body = Text()
        body.append(f" Review: +{n_add} added, -{n_rem} removed\n\n",
                    style=C_HEADER)
        shown = 0
        truncated = False
        for hi in changed:
            if shown >= maxr:
                truncated = True
                break
            _lbl, hm, he = self._host_cols[hi]
            added, removed = cf["diffs"][hi]
            body.append(f" {hm} {he}\n", style="bold grey85")
            shown += 1
            for s in added:
                if shown >= maxr:
                    truncated = True
                    break
                body.append(f"   + {_t(s)}\n", style=C_READY)
                shown += 1
            for s in removed:
                if shown >= maxr:
                    truncated = True
                    break
                body.append(f"   - {_t(s)}\n", style=C_WARN)
                shown += 1
        if truncated:
            body.append("   …", style=C_MUTED)
        return body

    def on_mount(self) -> None:
        changed = self._cf["changed"]
        self.query_one("#prof-frame", Vertical).border_title = (
            f"Apply profiles · {len(changed)} host column(s)")
        # The FocusGroup composes its own child, so it may not be mounted yet at
        # screen on_mount; defer the focus until the layout settles.
        self.call_after_refresh(
            lambda: self.query_one("#prof-buttons", FocusGroup).focus())

    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        self.dismiss(event.value == "apply")

    def action_cancel(self) -> None:
        self.dismiss(False)

class TaskMenuScreen(ModalScreen[int]):
    """Native modal action sub-menu for a registered-pivot task (#88 F4;
    native-focus internals NF1).

    Lists the focused task's declared actions and returns the chosen action
    *index* via ``dismiss(int)``, or ``dismiss(None)`` on cancel; the
    caller runs the selected action.

    **Native-focus internals (#88 NF1):** the action list is a native
    Textual ``OptionList`` (framework owns focus/up-down/Enter) below a
    header (task title + subtitle) and above a description pane tracking
    the highlighted action. Esc/q cancel via ``BINDINGS``.
    """

    CSS = """
    TaskMenuScreen { align: center middle; background: $background 55%; }
    TaskMenuScreen > #task-frame {
        width: 72; height: auto; border: round #ffaf00;
        background: $surface; padding: 0 1;
    }
    TaskMenuScreen OptionList {
        height: auto; border: none; background: $surface; padding: 0;
    }
    TaskMenuScreen OptionList:focus { background-tint: $surface 0%; }
    TaskMenuScreen OptionList > .option-list--option-highlighted,
    TaskMenuScreen OptionList:focus > .option-list--option-highlighted {
        background: #ffaf00; color: black; text-style: bold;
    }
    TaskMenuScreen #task-head { height: auto; padding: 0 0 1 0; }
    TaskMenuScreen #task-desc { color: grey; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
    ]

    def __init__(self, reg, rec, actions) -> None:
        super().__init__()
        self._reg = reg
        self._rec = rec
        self._actions = actions

    def compose(self) -> ComposeResult:
        with Vertical(id="task-frame"):
            yield Static(self._header(), id="task-head")
            yield OptionList(*[a.label for a in self._actions], id="task-list")
            yield Static("", id="task-desc")

    def _header(self) -> Text:
        reg, rec = self._reg, self._rec
        title = rec.get(reg.title_field) or rec.get(reg.id_field) or ""
        sub_bits = []
        if reg.worktree_field and rec.get(reg.worktree_field):
            sub_bits.append(str(rec.get(reg.worktree_field)))
        if reg.subtitle_field and rec.get(reg.subtitle_field):
            sub_bits.append(str(rec.get(reg.subtitle_field)))
        t = Text()
        t.append(str(title), style=C_HEADER)
        if sub_bits:
            t.append("\n" + " · ".join(sub_bits), style=C_DIM)
        return t

    def on_mount(self) -> None:
        ol = self.query_one("#task-list", OptionList)
        self.query_one("#task-frame", Vertical).border_title = self._reg.label
        self._set_desc(0)
        ol.focus()

    def _set_desc(self, i) -> None:
        if self._actions and 0 <= i < len(self._actions):
            self.query_one("#task-desc", Static).update(
                self._actions[i].description or "")

    def on_option_list_option_highlighted(
            self, event: OptionList.OptionHighlighted) -> None:
        self._set_desc(event.option_index)

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)

    def action_cancel(self) -> None:
        self.dismiss(None)

class SubMenuScreen(ModalScreen[tuple]):
    """Native modal per-worktree action menu (#88 F4; native-focus internals NF1).

    Renders the focused worktree's header (title + meta) and its available
    verbs (Open/Resume, Messages, Sync, Cleanup, Finalize, Stop, Jump to
    host/caller, plus contributed actions), and returns the chosen
    ``(action_label, no_mux, ahp)`` via ``dismiss(tuple)`` -- or
    ``dismiss(None)`` on cancel; the caller dispatches the verb.

    **Native-focus internals (#88 NF1):** verbs are a native Textual
    ``OptionList`` (framework owns focus/up-down/Enter) below a header and
    above a description pane tracking the highlight. **No-mux** is an
    arrow-reachable toggle row at the bottom of that list (shown when a
    primary launch verb -- Open/Resume -- is offered, since it modifies the
    launch, #4043): ↓ onto ``☐ No Mux``, Enter/Space flips it and stays
    open; Enter on a verb dismisses. ``no_mux`` is plain screen state.
    Esc/q cancel via ``BINDINGS``.
    """

    CSS = """
    SubMenuScreen { align: center middle; background: $background 55%; }
    SubMenuScreen > #sub-frame {
        width: 72; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    SubMenuScreen #sub-head { height: auto; padding: 0 0 1 0; }
    SubMenuScreen OptionList {
        height: auto; border: none; background: $surface; padding: 0;
    }
    SubMenuScreen OptionList:focus { background-tint: $surface 0%; }
    SubMenuScreen OptionList > .option-list--option-highlighted,
    SubMenuScreen OptionList:focus > .option-list--option-highlighted {
        background: #ffaf00; color: black; text-style: bold;
    }
    SubMenuScreen #sub-desc { color: grey; height: auto; padding: 1 0 0 0; }
    SubMenuScreen #sub-foot { color: grey; height: 1; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
        Binding("space", "toggle_modifier", show=False),
    ]

    _NOMUX_DESC = ("Launch (Open/Resume) WITHOUT the PSMux/TMux wrapper (for "
                   "troubleshooting). Enter/Space toggles this row.")
    _AHP_DESC = ("Launch (Open/Resume) through the configured same-machine "
                 "Agent Host Protocol endpoint. Enter/Space toggles this row.")

    def __init__(self, rec, actions, *, loading=False, engine=None) -> None:
        super().__init__()
        self._rec = rec
        self._actions = actions
        self._no_mux = False
        self._ahp = False
        explicit_local = rec.get("is_local")
        self._is_local = (
            bool(explicit_local)
            if explicit_local is not None
            else (
                True
                if engine is None
                else (
                    rec.get("source_kind", "machine-ssh") == "machine-ssh"
                    and (rec.get("machine"), rec.get("env")) == engine.src.LOCAL
                )
            )
        )
        # Always-async: the menu opens IMMEDIATELY from cached liveness; when an
        # authoritative re-verify is in flight, ``loading`` shows the shared
        # animated spinner in the footer and the caller calls ``refresh_actions``
        # to refine the verbs in place when it lands. ``engine`` supplies the
        # shared ``spin()`` frame so the spinner is consistent across the app.
        self._loading = loading
        self._engine = engine
        self._foot_frame = 0
        self._spin_timer = None
        # The No-mux modifier rides the primary launch verb -- Open (live mux /
        # sessionless) OR Resume (stopped session) -- so the toggle row is offered
        # whenever either is available (#4043). It sits at the end of the
        # OptionList. Bare resume is deliberately excluded (it already implies a
        # mux-in-HOME, which no-mux would contradict).
        self._has_nomux = ("Open" in actions) or ("Resume" in actions)
        self._nomux_index = len(actions) if self._has_nomux else None
        self._has_ahp = self._has_nomux and self._is_local
        self._ahp_index = len(actions) + 1 if self._has_ahp else None

    @property
    def no_mux(self) -> bool:
        """The live No-mux toggle -- plain screen state, flipped from the
        arrow-reachable ``No Mux`` row."""
        return self._no_mux

    @property
    def ahp(self) -> bool:
        return self._ahp

    def _nomux_prompt(self) -> Text:
        glyph, gstyle = ("☑", "green") if self._no_mux else ("☐", "#5f5f5f")
        t = Text()
        t.append(glyph + " ", style=gstyle)
        t.append("No Mux")
        return t

    def _ahp_prompt(self) -> Text:
        glyph, gstyle = ("☑", "green") if self._ahp else ("☐", "#5f5f5f")
        t = Text()
        t.append(glyph + " ", style=gstyle)
        t.append("AHP")
        return t

    def _prompts(self):
        prompts = [Text(a) for a in self._actions]
        if self._has_nomux:
            prompts.append(self._nomux_prompt())
        if self._has_ahp:
            prompts.append(self._ahp_prompt())
        return prompts

    def compose(self) -> ComposeResult:
        with Vertical(id="sub-frame"):
            yield Static(self._header(), id="sub-head")
            yield OptionList(*self._prompts(), id="sub-list")
            yield Static("", id="sub-desc")
            yield Static("", id="sub-foot")

    def _header(self) -> Text:
        rec = self._rec
        meta1 = (f" {rec.get('id4')} · {rec.get('machine')} · {rec.get('env')}"
                 f" · {rec.get('state')}")
        meta2 = (f" age {rec.get('age')} · sess {rec.get('sess')}"
                 f" · turns {rec.get('turns')} · PR {rec.get('pr')}")
        t = Text()
        t.append(f" {rec.get('title', '')}\n", style="bold")
        t.append(meta1 + "\n", style=C_DIM)
        t.append(meta2, style=C_DIM)
        relation = (rec.get("reciprocal_relation") or {}).get("state")
        if relation and relation != "unbound":
            t.append(f"\n relation {relation}", style=C_DIM)
        # two-step-restore: show the full session id so the operator can type
        # ``/resume <id>`` after a Bare resume; flag a live bound lock / residue.
        sid = rec.get("last_session_id")
        if sid:
            if rec.get("mux_live") and rec.get("session_bare_orphan"):
                lock = " · ⚠ inconsistent (mux + stray orphan — Repair fixes it)"
            elif rec.get("session_bare_orphan"):
                lock = " · bound (⚠ bare orphan — Reclaim frees it)"
            elif rec.get("session_lock_live") and not rec.get("mux_live"):
                lock = " · bound (lock live — Reclaim frees it)"
            elif rec.get("session_lock_live"):
                lock = " · bound (lock live)"
            elif rec.get("session_lock_stale") and not rec.get("mux_live"):
                lock = " · stale lock (residue — Reclaim clears it)"
            else:
                lock = ""
            t.append(f"\n session {sid}{lock}", style=C_DIM)
        # The full per-claim breakdown (kind/ref/note/state) used to render
        # here inline, unbounded -- a worktree carrying many claims could push
        # this header (and the always-needed verb list below it) past the
        # modal's max-height with no scrollbar to recover the hidden verbs.
        # Bounded to a single count line instead; "View details" (always the
        # last verb) opens the full breakdown in its own scrollable card.
        details = list((rec.get("asset_hints") or {}).get("details") or [])
        if details:
            t.append(
                f"\n {len(details)} held claim{'s' if len(details) != 1 else ''}"
                " — see View details", style=C_DIM)
        return t

    def on_mount(self) -> None:
        ol = self.query_one("#sub-list", OptionList)
        self.query_one("#sub-frame", Vertical).border_title = "Worktree actions"
        if self._actions:
            ol.highlighted = 0
        self._set_desc(0)
        ol.focus()
        if self._loading:
            # Show the shared animated spinner while the liveness re-verify runs;
            # the verbs above are already actionable from cached state.
            self._paint_foot()
            self._spin_timer = self.set_interval(0.1, self._paint_foot)

    def _spin_char(self) -> str:
        """The current spinner glyph -- the engine's shared frame when available
        (so it animates in lockstep with the rest of the app), else a local one."""
        eng = self._engine
        if eng is not None and hasattr(eng, "spin"):
            try:
                return eng.spin()
            except Exception:
                pass
        self._foot_frame += 1
        return SPINNER[self._foot_frame % len(SPINNER)]

    def _paint_foot(self) -> None:
        if not self._loading:
            return
        t = Text()
        t.append(f" {self._spin_char()} ", style="#ffaf00")
        t.append("refreshing worktree liveness…", style=C_DIM)
        self.query_one("#sub-foot", Static).update(t)

    def refresh_actions(self, new_actions) -> None:
        """Refine the verb list IN PLACE after the async liveness verify lands and
        drop the footer spinner (always-async: the menu opened immediately from
        cached state; this corrects the verbs without a re-open). Preserves the
        No-mux toggle and re-anchors the highlight."""
        self._loading = False
        if self._spin_timer is not None:
            try:
                self._spin_timer.stop()
            except Exception:
                pass
            self._spin_timer = None
        try:
            self.query_one("#sub-foot", Static).update("")
        except Exception:
            pass
        self._actions = list(new_actions)
        self._has_nomux = ("Open" in self._actions) or ("Resume" in self._actions)
        self._nomux_index = len(self._actions) if self._has_nomux else None
        self._has_ahp = self._has_nomux and self._is_local
        self._ahp_index = len(self._actions) + 1 if self._has_ahp else None
        try:
            ol = self.query_one("#sub-list", OptionList)
        except Exception:
            return
        prev = ol.highlighted or 0
        ol.clear_options()
        ol.add_options(self._prompts())
        count = ol.option_count
        if count:
            ol.highlighted = min(prev, count - 1)
        self._set_desc(ol.highlighted or 0)
        ol.focus()

    def _set_desc(self, i) -> None:
        if i == self._nomux_index:
            self.query_one("#sub-desc", Static).update(self._NOMUX_DESC)
        elif i == self._ahp_index:
            self.query_one("#sub-desc", Static).update(self._AHP_DESC)
        elif self._actions and 0 <= i < len(self._actions):
            self.query_one("#sub-desc", Static).update(
                ACTION_DESC.get(self._actions[i], ""))

    def _toggle_nomux(self) -> None:
        self._no_mux = not self._no_mux
        ol = self.query_one("#sub-list", OptionList)
        ol.replace_option_prompt_at_index(self._nomux_index, self._nomux_prompt())
        self._set_desc(self._nomux_index)

    def _toggle_ahp(self) -> None:
        self._ahp = not self._ahp
        ol = self.query_one("#sub-list", OptionList)
        ol.replace_option_prompt_at_index(self._ahp_index, self._ahp_prompt())
        self._set_desc(self._ahp_index)

    def on_option_list_option_highlighted(
            self, event: OptionList.OptionHighlighted) -> None:
        self._set_desc(event.option_index)

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        # Enter on the No-mux row toggles it and stays open; Enter on a verb
        # dismisses with the chosen action + the current No-mux state.
        if event.option_index == self._nomux_index:
            self._toggle_nomux()
            return
        if event.option_index == self._ahp_index:
            self._toggle_ahp()
            return
        self.dismiss((self._actions[event.option_index], self._no_mux, self._ahp))

    def action_toggle_modifier(self) -> None:
        ol = self.query_one("#sub-list", OptionList)
        if ol.highlighted == self._nomux_index:
            self._toggle_nomux()
        elif ol.highlighted == self._ahp_index:
            self._toggle_ahp()

    def action_toggle_nomux(self) -> None:
        self.action_toggle_modifier()

    def action_cancel(self) -> None:
        self.dismiss(None)

class WtDetailsScreen(ModalScreen[None]):
    """Read-only, scrollable "View details" card for one worktree
    (fold-the-claims-list follow-up).

    The Actions menu's own header (:meth:`SubMenuScreen._header`) is bounded
    to a handful of fixed lines so the always-needed verb list never gets
    pushed past the modal's max-height with no way to scroll back to it. This
    screen is the escape hatch for everything that used to render there
    unbounded (the full held-claims/asset breakdown) plus the rest of the
    record's identity/status detail an operator might want: title, path, id,
    status, the full claim list, and session detail. Built entirely from the
    ``rec`` dict already held by the picker -- no new gather/IO, so it opens
    instantly like every other menu here.
    """

    CSS = """
    WtDetailsScreen { align: center middle; background: $background 55%; }
    WtDetailsScreen > #details-frame {
        width: 84; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    WtDetailsScreen #details-body { height: auto; max-height: 100%; }
    WtDetailsScreen #details-foot { color: grey; height: 1; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, rec) -> None:
        super().__init__()
        self._rec = rec

    def compose(self) -> ComposeResult:
        with Vertical(id="details-frame"):
            with VerticalScroll(id="details-body"):
                yield Static(self._body())
            yield Static(" Esc/Enter: close", id="details-foot")

    @staticmethod
    def _asset_label(c) -> str:
        ref, note = (c.get("ref") or "").strip(), (c.get("note") or "").strip()
        return f"{ref} — {note}" if ref and note else ref or note or "(unlabeled)"

    def _body(self) -> Text:
        rec = self._rec
        raw = rec.get("raw") or {}
        t = Text()
        t.append(f"{rec.get('title') or '(untitled)'}\n", style="bold")
        path = raw.get("path") or raw.get("worktree_path") or ""
        if path:
            t.append(f"path    {path}\n", style=C_DIM)
        t.append(f"id      {raw.get('id') or rec.get('id4') or ''}\n", style=C_DIM)
        t.append(
            f"status  {rec.get('state')} · {rec.get('machine')} · "
            f"{rec.get('env')}\n", style=C_DIM)
        t.append(
            f"age {rec.get('age')} · sess {rec.get('sess')} · "
            f"turns {rec.get('turns')} · PR {rec.get('pr')}\n", style=C_DIM)
        markers = (rec.get("status_markers") or "").strip()
        if markers:
            t.append("\nstatus markers:\n", style="bold")
            for text, is_warn in derive.status_marker_segments(markers, 999):
                t.append(text, style=C_WARN if is_warn else C_DIM)
            t.append("\n")
        relation = (rec.get("reciprocal_relation") or {}).get("state")
        if relation and relation != "unbound":
            t.append(f"\nrelation  {relation}\n", style=C_DIM)
        sid = rec.get("last_session_id")
        if sid:
            if rec.get("mux_live") and rec.get("session_bare_orphan"):
                lock = " · ⚠ inconsistent (mux + stray orphan — Repair fixes it)"
            elif rec.get("session_bare_orphan"):
                lock = " · bound (⚠ bare orphan — Reclaim frees it)"
            elif rec.get("session_lock_live") and not rec.get("mux_live"):
                lock = " · bound (lock live — Reclaim frees it)"
            elif rec.get("session_lock_live"):
                lock = " · bound (lock live)"
            elif rec.get("session_lock_stale") and not rec.get("mux_live"):
                lock = " · stale lock (residue — Reclaim clears it)"
            else:
                lock = ""
            t.append(f"\nsession   {sid}{lock}\n", style=C_DIM)
        details = list((rec.get("asset_hints") or {}).get("details") or [])
        if details:
            t.append(f"\nheld claims ({len(details)}):\n", style="bold")
            for c in details:
                t.append(
                    f"  {c.get('kind', 'resource')} [{c.get('state') or 'active'}]"
                    f": {self._asset_label(c)}\n", style=C_DIM)
        else:
            t.append("\nheld claims: none\n", style=C_DIM)
        t.append(
            "\nUse the Messages action (from the Actions menu) to peek this "
            "worktree's recent conversation.", style=C_DIM)
        return t

    def action_close(self) -> None:
        self.dismiss(None)


class ScopeDlgScreen(ModalScreen[bool]):
    """Native modal scope dialog for Clean/Sync and New-worktree options (#88 F4;
    native-focus internals #88 NF1).

    Shared by ``cleanup`` (Clean/Sync) and ``optmenu`` (New-worktree
    options): a multi-select list of option toggles, a ``[Confirm]
    [Cancel]`` row, and -- for Clean/Sync -- a read-only impact list naming
    exactly which worktrees the current selection will act on. Returns
    ``dismiss(True)`` on Confirm / ``dismiss(False)`` on Cancel or Esc; the
    toggles are mirrored back onto the passed ``dlg`` dict
    (``opts[i]["on"]``) so the caller reads the confirmed selection
    straight off it, and ``_union()``/``_impact_fn`` stay valid live.

    **Native-focus internals (#88 NF1):** the toggles are a native Textual
    ``SelectionList`` (one tab-stop; arrow to move, Space to toggle) and
    the button row is a :class:`FocusGroup` (one tab-stop; ◀▶ to choose,
    Enter to activate). Tab moves between the two. Esc/q cancel via
    ``BINDINGS``.
    """

    CSS = """
    ScopeDlgScreen { align: center middle; background: $background 55%; }
    ScopeDlgScreen > #scope-frame {
        width: 68; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    ScopeDlgScreen SelectionList {
        height: auto; border: none; background: $surface; padding: 0;
    }
    /* Cursor highlight (row + checkbox) paints only while the list actually
       holds focus. An unfocused options list must not highlight its "current
       target" at all, or a mere cursor position reads as a selection -- the
       root of #121 (operator mistakes the highlighted Anchor-repo row for a
       checked one). :blur explicitly neutralizes the framework's dimmed
       block-cursor so nothing lingers. */
    ScopeDlgScreen SelectionList:focus > .option-list--option-highlighted {
        background: #ffaf00; color: black; text-style: bold;
    }
    ScopeDlgScreen SelectionList:blur > .option-list--option-highlighted {
        background: $surface; color: $foreground; text-style: none;
    }
    ScopeDlgScreen SelectionList > .selection-list--button {
        background: $surface; color: #5f5f5f;
    }
    ScopeDlgScreen SelectionList:focus > .selection-list--button-highlighted {
        background: #ffaf00; color: black;
    }
    ScopeDlgScreen SelectionList:blur > .selection-list--button-highlighted {
        background: $surface; color: #5f5f5f;
    }
    /* Checked state stays visible regardless of focus -- it is real selection,
       not cursor position, so it must survive blur. */
    ScopeDlgScreen SelectionList > .selection-list--button-selected {
        background: green; color: white; text-style: bold;
    }
    ScopeDlgScreen SelectionList > .selection-list--button-selected-highlighted {
        background: green; color: white; text-style: bold;
    }
    ScopeDlgScreen #scope-prompt { height: auto; padding: 0 0 1 0; }
    ScopeDlgScreen #scope-impact { height: auto; padding: 1 0 0 0; }
    ScopeDlgScreen #scope-buttons { width: 1fr; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
    ]

    def __init__(self, dlg, impact_fn=None) -> None:
        super().__init__()
        self._dlg = dlg
        self._impact_fn = impact_fn      # (ids:set) -> list[(id4, machine_env, title)]

    def _union(self) -> set:
        s: set = set()
        for o in self._dlg["opts"]:
            if o["on"]:
                s |= set(o.get("ids", ()))
        return s

    def _opt_prompt(self, o) -> Text:
        t = Text(f"{o['label']:<14}", style=C_LABEL)
        if o.get("hint"):
            t.append("  " + o["hint"], style=C_META)
        return t

    def compose(self) -> ComposeResult:
        dlg = self._dlg
        opts = dlg["opts"]
        with Vertical(id="scope-frame"):
            yield Static(Text(dlg.get("prompt", "Select:"), style=C_HEADER),
                         id="scope-prompt")
            yield _ScopeSelectionList(
                *[(self._opt_prompt(o), i, o["on"]) for i, o in enumerate(opts)],
                id="scope-opts")
            if self._impact_fn is not None:
                yield Static("", id="scope-impact")
            yield FocusGroup(
                [("confirm", dlg.get("confirm", "Confirm")), ("cancel", "Cancel")],
                id="scope-buttons")

    def on_mount(self) -> None:
        dlg = self._dlg
        verb = dlg.get("verb", "Clean up")
        scope = ("{} {}".format(*dlg["target"]) if "target" in dlg
                 else dlg.get("scope", ""))
        self.query_one("#scope-frame", Vertical).border_title = f"{verb} · {scope}"
        self._sync_from_selection()
        # optmenu opens straight on the Create button (section 1); Clean/Sync
        # opens on the toggle list.
        if dlg.get("section", 0) == 1:
            self.query_one("#scope-buttons", FocusGroup).focus()
        else:
            self.query_one("#scope-opts", SelectionList).focus()

    def _sync_from_selection(self) -> None:
        """Mirror the SelectionList's checked state back onto ``dlg['opts']`` so
        the caller (and ``_union`` / the impact list) reads the live selection."""
        chosen = set(self.query_one("#scope-opts", SelectionList).selected)
        for i, o in enumerate(self._dlg["opts"]):
            o["on"] = i in chosen
        if "target" in self._dlg:
            selected = [o["label"] for o in self._dlg["opts"] if o["on"]]
            prompt = Text(self._dlg.get("prompt", "Select:"), style=C_HEADER)
            prompt.append(
                "\nSelected: " + (", ".join(selected) if selected else "none"),
                style=C_READY if selected else C_MUTED,
            )
            self.query_one("#scope-prompt", Static).update(prompt)
        if self._impact_fn is not None:
            self._update_impact()

    def _update_impact(self) -> None:
        verb = self._dlg.get("verb", "Clean up")
        rows = self._impact_fn(self._union())
        body = Text()
        body.append(f"{verb}s {len(rows)} worktree(s):\n", style=C_HEADER)
        maxr = 10
        for id4, machine_env, title in rows[:maxr]:
            body.append(f"  {id4} · {machine_env} · {title}\n", style=C_FAINT)
        if len(rows) > maxr:
            body.append(f"  … +{len(rows) - maxr} more\n", style=C_MUTED)
        if not rows:
            body.append("  (nothing selected)\n", style=C_MUTED)
        self.query_one("#scope-impact", Static).update(body)

    def on_selection_list_selected_changed(
            self, event: SelectionList.SelectedChanged) -> None:
        self._sync_from_selection()

    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        self.dismiss(event.value == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)

class CfgMenuScreen(ModalScreen[int]):
    """Native modal ⚙ Configuration menu (#88 F4; native-focus internals #88 NF1).

    Lists the config-hosted pivots (Profiles) plus any contributed
    Configuration sections and returns the chosen item *index* via
    ``dismiss(int)``, or ``dismiss(None)`` on cancel; the caller acts on
    the selection (switch to that pivot / run that section).

    **Native-focus internals (#88 NF1):** the menu list is a native
    Textual ``OptionList`` (framework owns focus/up-down/Enter). Esc/q
    cancel via ``BINDINGS``. ``dismiss(event.option_index)`` returns the
    choice.
    """

    CSS = """
    CfgMenuScreen { align: center middle; background: $background 55%; }
    CfgMenuScreen > #cfg-frame {
        width: 56; height: auto; max-height: 80%;
        border: round #ffaf00; background: $surface; padding: 1 2;
    }
    CfgMenuScreen OptionList {
        height: auto; border: none; background: $surface; padding: 0;
    }
    CfgMenuScreen OptionList:focus { background-tint: $surface 0%; }
    CfgMenuScreen OptionList > .option-list--option-highlighted,
    CfgMenuScreen OptionList:focus > .option-list--option-highlighted {
        background: #ffaf00; color: black; text-style: bold;
    }
    CfgMenuScreen #cfg-hint { color: grey; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
    ]

    def __init__(self, items, idx=0) -> None:
        super().__init__()
        self._items = items
        self.idx = idx

    def compose(self) -> ComposeResult:
        with Vertical(id="cfg-frame"):
            yield OptionList(*[it["label"] for it in self._items], id="cfg-list")
            yield Static("↑↓ choose · Enter open · Esc back", id="cfg-hint")

    def on_mount(self) -> None:
        ol = self.query_one("#cfg-list", OptionList)
        self.query_one("#cfg-frame", Vertical).border_title = (
            "⚙ Configuration · user-local settings")
        if 0 <= self.idx < len(self._items):
            ol.highlighted = self.idx
        ol.focus()

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)

    def action_cancel(self) -> None:
        self.dismiss(None)

class MaintMenuScreen(ModalScreen[int]):
    """Native modal Maintenance actions menu (#88 F4; native-focus internals NF1).

    Lists the maintenance actions available for the selected worktree set
    (Sync/Cleanup/Finalize/Stop) and returns the chosen action *index* via
    ``dismiss(int)``, or ``dismiss(None)`` on cancel; the caller runs the
    selection.

    **Native-focus internals (#88 NF1):** the action list is a native
    Textual ``OptionList`` (framework owns focus/up-down/Enter). A
    description pane below the list tracks the highlighted action;
    Esc/q cancel via ``BINDINGS``.
    """

    CSS = """
    MaintMenuScreen { align: center middle; background: $background 55%; }
    MaintMenuScreen > #maint-frame {
        width: 64; height: auto; border: round #ffaf00;
        background: $surface; padding: 1 2;
    }
    MaintMenuScreen OptionList {
        height: auto; border: none; background: $surface; padding: 0;
    }
    MaintMenuScreen OptionList:focus { background-tint: $surface 0%; }
    MaintMenuScreen OptionList > .option-list--option-highlighted,
    MaintMenuScreen OptionList:focus > .option-list--option-highlighted {
        background: #ffaf00; color: black; text-style: bold;
    }
    MaintMenuScreen #maint-desc { color: grey; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
    ]

    def __init__(self, actions, count) -> None:
        super().__init__()
        self._actions = actions
        self._count = count

    def compose(self) -> ComposeResult:
        with Vertical(id="maint-frame"):
            yield OptionList(*self._actions, id="maint-list")
            yield Static("", id="maint-desc")

    def on_mount(self) -> None:
        ol = self.query_one("#maint-list", OptionList)
        self.query_one("#maint-frame", Vertical).border_title = (
            f"Maintenance · {self._count} selected")
        self._set_desc(0)
        ol.focus()

    def _set_desc(self, i) -> None:
        if self._actions and 0 <= i < len(self._actions):
            self.query_one("#maint-desc", Static).update(
                MAINT_ACTION_DESC.get(self._actions[i], ""))

    def on_option_list_option_highlighted(
            self, event: OptionList.OptionHighlighted) -> None:
        self._set_desc(event.option_index)

    def on_option_list_option_selected(
            self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)

    def action_cancel(self) -> None:
        self.dismiss(None)
