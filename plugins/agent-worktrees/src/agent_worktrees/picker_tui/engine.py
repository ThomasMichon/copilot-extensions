#!/usr/bin/env python3
"""Textual rendering engine for the overhauled Worktree Picker.

Ported from the design prototype (aperture-labs effort
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
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widget import Widget

from . import derive

VERSION = "1.5.3-dev65"

# ---- palette (highlight-and-invert; subtle borders) -------------------------
C_DIM = "grey42"          # subtle separators / borders
C_HEADER = "bold white"
C_BAND = "bold orange1"   # band headers (scope)
C_SECTION = "bold grey70"
# Tabs/pivots: ACTIVE-but-unfocused is SUBTLE (no invert) so it doesn't compete
# with the focus cursor; only the FOCUSED tab inverts.
C_TAB_ACTIVE = "bold white on grey23"   # selected pivot, zone not focused
C_TAB_FOCUS = "reverse bold"            # selected pivot, zone focused (cursor)
C_TABOFF = "grey58"
C_SEL = "reverse"         # focused row -> invert (the cursor)
C_SPIN = "yellow"
C_WARN = "red"
C_PR_MERGED = "green"
C_HINT = "grey46"         # scroll-hint arrows (subtle)
C_HINT_ON = "orange1"     # scroll hint when there IS more content that way
# Glowy pulse for live indicators (two phases, cycled by a timer).
C_PULSE = ["green", "bold bright_green"]
C_BTN = "bold white on grey27"   # button at rest
C_BTN_LAST = "bold grey85 on grey19"  # group's last-focused button (subtle)
C_BTN_SEL = "bold black on orange1"  # button focused (the cursor, but "glows")
C_STATE = {
    # Match the PSMux/TMux status segment (_SEGMENT_STYLE) so the picker and the
    # status bar use one vocabulary + palette (aperture-labs #1290).
    "DIRTY": "#d70000",    # red (colour160)
    "WIP": "#d7af00",      # amber (colour178)
    "FINAL": "#00af00",    # green (colour034) -- COMPLETED
    "UNUSED": "grey58",    # grey (colour244)
    "CONVO": "#00afaf",    # teal (colour037) -- UNUSED + conversation
    "ORPHAN": "#af00ff",   # magenta (colour129)
    "ACTIVE": "#00afff",   # blue (colour039)
    "GONE": "grey35",      # dark grey (colour238)
    "?": "grey35",
}

PAD = "   "  # inter-column padding (3 spaces -> info breathes)

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


def row_text(rec, cols, width, selected, indent=1, pulse=0):
    t = Text(" " * indent)
    for i, (k, _h, w, a) in enumerate(cols):
        if i:
            t.append(PAD)
        val = str(rec.get(k, ""))
        if k == "machine_env":
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
        elif k == "env":
            style = C_ENV.get(rec.get("env", ""), "")
        elif k == "dispo":
            style = C_DISPO.get(rec.get("dispo_level", ""), "")
        elif k == "pr" and rec.get("pr", "").endswith("✓"):
            style = C_PR_MERGED
        elif k == "sess" and rec.get("sess", "").startswith("●"):
            style = C_PULSE[pulse]   # glowy pulse for live sessions
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
    ("machine_env", "machine env", 19, "l", 5),
    ("age", "age", 4, "l", 7), ("sess", "sess", 4, "l", 8),
    ("pr", "pr", 8, "l", 3), ("title", "title", 10, "l", 1),
]
LIST_SPECS = [
    ("id4", "id", 4, "l", 2), ("state", "state", 6, "l", 4),
    ("age", "age", 4, "l", 6), ("sess", "sess", 4, "l", 7),
    ("turns", "t", 3, "r", 8), ("pr", "pr", 8, "l", 3),
    ("title", "title", 10, "l", 1),
]
HTABS = ["Worktrees", "Maintenance", "Profiles"]
# Per-tab button sets — Tab/Shift+Tab rotate within these when focused.
BUTTON_SETS = {0: ["N", "NO"], 1: ["K", "SY"], 2: ["PA", "PReset"]}

# ---- Profiles matrix model ----------------------------------------------------
# Columns = valid HOST machines (a terminal app runs here). Windows or native
# Linux per machine -- never WSL.
HOST_COLS = [
    ("Lambda·Win", "Lambda-Core", "Win"),
    ("Borealis·Win", "Borealis", "Win"),
    ("Wheatley·Lx", "Wheatley", "Linux"),
    ("book2·Win", "tmichon-book2", "Win"),
]
# Rows = TARGETS a profile can launch: every machine + every environment
# (WSL included), each as an agent (worktree) launch or a plain shell.
_TARGET_ENVS = [
    ("Lambda-Core", "Win"), ("Lambda-Core", "WSL"),
    ("Borealis", "Win"), ("Borealis", "WSL"),
    ("Wheatley", "Linux"), ("tmichon-book2", "Win"),
]


def target_rows():
    rows = []
    for m, e in _TARGET_ENVS:
        for agent in (True, False):
            rows.append({
                "machine": m, "env": e, "agent": agent,
                "label": f"{m} · {e} · {'agent' if agent else 'shell'}",
            })
    return rows

# Clarify each worktree action in the sub-menu (addresses Open-vs-Resume).
ACTION_DESC = {
    "Open": "Attach the worktree's terminal (PSMux/TMux) session and bring it forward.",
    "Resume": "Re-enter the Copilot agent session in this worktree (continue its turns).",
    "Sync": "Pull/rebase onto base; surface conflicts.",
    "Create PR": "Squash commits → feature branch → open or update the PR.",
    "Kill session": "Terminate the wrapper PSMux/TMux session — use if it's broken/stuck.",
    "Reset env": "Re-export the worktree's environment variables (rarely needed).",
    "Cleanup": "Remove this worktree (PR-merged ones are safe to prune).",
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


class PickerScreen(Widget):
    can_focus = True

    def __init__(self, source, live=False):
        super().__init__()
        self.htab = 0                 # 0 Worktrees 1 Maint 2 Profiles
        self.machine_idx = 0          # selected machine sub-pivot (Worktrees/Maint)
        self.sel = ("N", 0)           # (zone, index) -> default New Worktree
        self.top = 0                  # scroll offset into body vrows
        self.submenu = None           # worktree action modal
        self.submenu_idx = 0
        self.cleanup = None           # cleanup-scope modal (Maintenance)
        self.optmenu = None           # "More options…" create modal
        self.progress = None          # cleanup/sync progress sub-dialog
        self.executor = None          # real maintenance executor (opt-in)
        # Real cleanup/sync ops are opt-in; the default is the safe in-TUI
        # simulation (mock walker) so the flow can be exercised without side
        # effects. AGENT_WORKTREES_PICKER_REAL_OPS=1 runs the real per-worktree
        # executor (local in-process + remote over SSH).
        self.real_ops = bool(os.environ.get("AGENT_WORKTREES_PICKER_REAL_OPS"))
        self.pcol = 0                 # Profiles matrix column cursor
        self.last_pr = 0              # remembered Profiles grid row (Tab in/out)
        self.grid = {}                # (target_idx, host_idx) -> bool present
        self.applied = {}             # last-applied snapshot of the grid
        self.btn_idx = 0              # active button within the current group
        self.targets = []
        self.debug = "ready"
        self.data = []
        self.machines = []
        self.pulse = 0
        self.frame = 0
        self.t0 = 0.0
        self.load_delay = {}
        # Injected data source (local / SSH / fixture). ``live`` enables the
        # async per-machine loader the source supplies via ``make_loader()``.
        self.src = source
        self.live = live
        self.loader = None            # async loader (live/multi-machine only)

    def on_mount(self):
        self.setup()
        self.sel = self.default_sel()
        self.focus()
        # ~10 fps drives the SSH spinner and the slower live-glyph pulse.
        self.set_interval(0.1, self._tick)

    def _tick(self):
        self.frame += 1
        self.pulse = (self.frame // 5) % 2
        # In live mode, stream in worktrees as each machine's load resolves.
        if self.live and self.loader is not None:
            self.data = self.loader.records()
        # Drive the mock cleanup/sync progress dialog forward.
        if self.progress and not self.progress["done"]:
            self._advance_progress()
        self.refresh()

    def _advance_progress(self):
        """Drive the progress sub-dialog forward.

        When the run is unarmed (awaiting the extra confirm) nothing advances.
        With the real executor active, mirror its per-item states; otherwise run
        the mock walker (the safe simulation): walk the selected worktrees one
        at a time (pending -> running -> done), paced by the render tick."""
        p = self.progress
        if not p.get("armed", True):
            return
        if self.executor is not None:
            self._poll_executor()
            return
        p["ticks"] += 1
        cur = p["ticks"] // p["steps"]   # index currently "running"
        items = p["items"]
        for j, it in enumerate(items):
            if it["state"] == "failed":
                continue
            if j < cur:
                it["state"] = "done"
            elif j == cur:
                it["state"] = "running"
            else:
                it["state"] = "pending"
        if cur >= len(items):
            p["done"] = True
            for it in items:
                if it["state"] != "failed":
                    it["state"] = "done"

    def setup(self):
        # Machine tabs gain a leading "All" entry that interleaves every machine.
        self.machines = [("All", None, None, True)] + self.src.machines()
        self.machine_idx = self.local_index()
        if self.live:
            # Real background SSH loads: one thread per machine, spinner -> ✓/✗.
            self.data = []
            self.loader = self.src.make_loader()
            self.loader.start()
        else:
            self.data = self.src.load()
            # Simulate background SSH status loads: local is instant, remotes
            # stagger in, the unreachable one (book2) is permanently disabled.
            self.t0 = time.monotonic()
            self.load_delay = {}
            d = 1.4
            for i, (label, m, e, ok) in enumerate(self.machines):
                if label == "All" or (m, e) == self.src.LOCAL or not ok:
                    self.load_delay[i] = 0.0
                else:
                    self.load_delay[i] = d
                    d += 1.1
        # Profiles matrix: seed a "self · agent" profile on each host.
        self.targets = target_rows()
        self.grid = {}
        for ti, t in enumerate(self.targets):
            for hi in range(len(HOST_COLS)):
                self.grid[(ti, hi)] = self.cell_locked(ti, hi)
        self.applied = dict(self.grid)   # everything starts "applied"

    def button_set(self):
        if self.htab == 2:
            return ["PA", "PReset"] if self.grid_dirty() else ["PA"]
        return BUTTON_SETS.get(self.htab, [])

    def active_button(self):
        bset = self.button_set()
        if not bset:
            return None
        return bset[self.btn_idx % len(bset)]

    def grid_dirty(self):
        return any(self.grid.get(k) != self.applied.get(k) for k in self.grid)

    def pending_count(self):
        return sum(1 for k in self.grid if self.grid.get(k) != self.applied.get(k))

    def cell_locked(self, ti, hi):
        """A machine always has a profile for THIS repo with itself as host
        (self · agent) -- force-checked, not editable."""
        t = self.targets[ti]
        _lbl, hm, he = HOST_COLS[hi]
        return t["machine"] == hm and t["env"] == he and t["agent"]

    def machine_state(self, i):
        label, m, e, ok = self.machines[i]
        if label == "All":
            return "ready"
        if not ok:
            return "disabled"
        if self.live:
            # Real per-machine load status: loading | ready | failed.
            return self.loader.state(m, e)
        if (m, e) == self.src.LOCAL:
            return "ready"
        return "ready" if (time.monotonic() - self.t0) >= self.load_delay[i] else "loading"

    def ready_envs(self):
        out = set()
        for i, (label, m, e, ok) in enumerate(self.machines):
            if label != "All" and self.machine_state(i) == "ready":
                out.add((m, e))
        return out

    def spin(self):
        return SPINNER[self.frame % len(SPINNER)]

    def local_index(self):
        for i, (label, m, e, ok) in enumerate(self.machines):
            if (m, e) == self.src.LOCAL:
                return i
        return 1 if len(self.machines) > 1 else 0

    # ---- model helpers ----
    def is_all(self):
        return self.machines[self.machine_idx][1] is None

    def cur_machine(self):
        label, m, e, ok = self.machines[self.machine_idx]
        return m, e, ok

    def current_list(self):
        """(cols, [(section_label, [records])]) for the selected machine tab.
        'All' interleaves every READY machine and shows the machine/env columns;
        a specific machine hides them (implied by the tab)."""
        if self.is_all():
            cols = ACTIVE_SPECS
            ready = self.ready_envs()
            data = [w for w in self.data if (w["machine"], w["env"]) in ready]
            a, r, c = self.src.bucket(data)
        else:
            cols = LIST_SPECS
            m, e, _ = self.cur_machine()
            a, r, c = self.src.for_machine(self.data, m, e)
        return cols, [("Active", a), ("Recent", r), ("Completed", c)]

    def list_records(self):
        _cols, secs = self.current_list()
        out = []
        for _label, rows in secs:
            out.extend(rows)
        return out

    def create_target(self):
        """Where '+ New Worktree' lands: the active machine, or LOCAL when on
        'All'."""
        if self.is_all():
            return self.src.LOCAL
        m, e, _ = self.cur_machine()
        return (m, e)

    def stops(self):
        """Vertical Up/Down flow. ("V",0)=View nav, ("M",0)=machine picker,
        ("BTN",0)=button row, then the table/grid rows."""
        if self.htab == 0:
            out = [("V", 0), ("M", 0), ("BTN", 0)]
            for i in range(len(self.list_records())):
                out.append(("L", i))
        elif self.htab == 1:
            out = [("V", 0), ("M", 0), ("BTN", 0)]
            for i in range(len(self.cleanup_rows())):
                out.append(("C", i))
        else:
            out = [("V", 0)]
            for i in range(len(self.targets)):
                out.append(("PR", i))
            out.append(("BTN", 0))   # Apply/Reset at the bottom
        return out

    def _pr_head(self):
        """Grid-region entry point, restoring the last-focused row (#1288).
        pcol (the host column) already persists across Tab in/out."""
        return ("PR", min(self.last_pr, max(0, len(self.targets) - 1)))

    def region_head(self, zone):
        if zone == "PR":
            return self._pr_head()
        return {"V": ("V", 0), "M": ("M", 0), "BTN": ("BTN", 0),
                "L": ("L", 0), "C": ("C", 0), "PR": ("PR", 0)}.get(zone, ("V", 0))

    def region_heads(self):
        """Tab/Shift+Tab jump targets — the entry point of each major region."""
        if self.htab == 0:
            heads = [("V", 0), ("M", 0), ("BTN", 0)]
            if self.list_records():
                heads.append(("L", 0))
        elif self.htab == 1:
            heads = [("V", 0), ("M", 0), ("BTN", 0)]
            if self.cleanup_rows():
                heads.append(("C", 0))
        else:
            heads = [("V", 0), self._pr_head(), ("BTN", 0)]
        return heads

    def default_sel(self):
        return {0: ("BTN", 0), 1: ("BTN", 0), 2: ("PR", 0)}[self.htab]

    def anchors(self):
        return self.region_heads()

    def cleanup_rows(self):
        """Maintenance worktree list, scoped to the active machine sub-pivot
        (All = every machine), each tagged with its prune disposition.

        Shows every in-scope worktree -- including in-use/work-bearing ones --
        so the disposition column is honest; the Cleanup dialog only *offers*
        the cleanable buckets, and the executor re-checks safety per worktree.
        """
        if self.is_all():
            rows = list(self.data)
        else:
            m, e, _ = self.cur_machine()
            rows = [w for w in self.data if w["machine"] == m and w["env"] == e]
        for w in rows:
            bucket = w.get("cleanup_bucket", "wip")
            lvl = derive.BUCKET_DISPO.get(bucket, "")
            txt = derive.BUCKET_REASON.get(bucket, "")
            # Blended disposition (verdict + reason in one colored chip).
            w["dispo_level"] = lvl
            w["dispo"] = f" {DISPO_MARK[lvl]} {txt}" if lvl else ""
        return rows

    # ---- Profiles matrix helpers ----
    def profile_col_widths(self):
        return [max(len(f"{m} {e}"), 3) + 2 for _lbl, m, e in HOST_COLS]

    def _host_header_cell(self, j, colw):
        """One Profiles host-column header: machine (config) name in the header
        slot color + env in its per-env color, centered to the column width.
        The active host (the column being edited) gets the SAME subtle shading
        the machine tabs use for an active-but-unfocused tab -- never the
        inversion cursor, since the real cursor lives in the grid cell."""
        _lbl, m, e = HOST_COLS[j]
        active = j == self.pcol
        name_base = "bold white" if active else C_TABOFF
        env_base = C_ENV.get(e, name_base)
        cell = Text()
        cell.append(m, style=self._hl(name_base, active, False))
        cell.append(" ", style=self._hl("", active, False))
        cell.append(e, style=self._hl(env_base, active, False))
        pad = max(0, colw - cell.cell_len)
        left = pad // 2
        out = Text(" " * left)
        out.append_text(cell)
        out.append(" " * (pad - left))
        return out

    def profiles_present(self):
        return sum(1 for v in self.grid.values() if v)

    def _btn_style(self, focus, is_active):
        """Focused active button glows; unfocused active button shows a subtle
        'last-focused' highlight; others are at rest."""
        if focus and is_active:
            return C_BTN_SEL
        if (not focus) and is_active:
            return C_BTN_LAST
        return C_BTN

    def two_button_row(self, l1, l2, focus, active_idx, suffix, width):
        t = Text("  ")
        t.append(f" {l1} ", style=self._btn_style(focus, active_idx == 0))
        t.append("   ")
        t.append(f" {l2} ", style=self._btn_style(focus, active_idx == 1))
        if suffix and t.cell_len + 4 + len(suffix) <= width:
            t.append("    " + suffix, style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def new_worktree_row(self, width, focus, active_idx):
        tm, te = self.create_target()
        t = Text("  ")
        t.append(" + New Worktree ", style=self._btn_style(focus, active_idx == 0))
        t.append("   ")
        t.append(" More options… ", style=self._btn_style(focus, active_idx == 1))
        suffix = f"    creates on {tm} {te}"
        host_tag = "  (this host)" if (tm, te) == self.src.LOCAL else ""
        if t.cell_len + len(suffix) + len(host_tag) <= width:
            t.append("    creates on ", style=C_DIM)
            t.append(f"{tm} ", style="grey70")
            t.append(te, style=C_ENV.get(te, "grey70"))
            if host_tag:
                t.append(host_tag, style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def button_row(self, label, suffix, selected, width):
        """A row that reads as a button: a filled chip + dim trailing context
        (the context is dropped when it doesn't fit)."""
        t = Text("  ")
        t.append(f" {label} ", style=C_BTN_SEL if selected else C_BTN)
        if suffix and t.cell_len + 3 + len(suffix) <= width:
            t.append("   " + suffix, style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t

    # ---- build the scrollable body as VRows ----
    def build_body(self, width):
        vrows = []
        cur_band = None
        cur_section = None

        def add(text, stop=None, kind=None, data=None):
            vr = VRow(text, stop, kind, data)
            vr.pin_band = cur_band
            vr.pin_section = cur_section
            vrows.append(vr)
            return vr

        sel = self.sel
        btn_focus = sel == ("BTN", 0)
        if self.htab == 0:
            add(self.tab_bar(width, sel == ("M", 0)))
            add(Text(""))  # breathing room above the buttons
            add(self.new_worktree_row(width, btn_focus, self.btn_idx),
                stop=("BTN", 0))
            add(Text(""))  # breathing room below the buttons
            cols, sections = self.current_list()
            lcols = fit(cols, width - 1, "title", 14)
            add(header_text(lcols, width), kind="colhdr")
            li = 0
            for label, rows in sections:
                sec = Text(f"  ── {label} ", style=C_SECTION)
                sec.append("─" * (width - sec.cell_len), style=C_DIM)
                cur_section = (label, len(vrows))
                add(sec, kind="section")
                if not rows:
                    add(Text("    (none)", style=C_DIM))
                for rec in rows:
                    add(row_text(rec, lcols, width, sel == ("L", li), pulse=self.pulse),
                        stop=("L", li), data=rec)
                    li += 1
        elif self.htab == 1:
            add(self.tab_bar(width, sel == ("M", 0)))
            rows = self.cleanup_rows()
            total = sum(_size_mb(w) for w in rows)
            add(Text(""))
            add(self.two_button_row(
                "Cleanup…", "Sync…", btn_focus, self.btn_idx,
                f"{len(rows)} candidates · ~{total} MiB", width),
                stop=("BTN", 0))
            add(Text(""))
            ccols = fit(CLEAN_SPECS, width - 1, "title", 12)
            add(header_text(ccols, width), kind="colhdr")
            for i, rec in enumerate(rows):
                d = dict(rec, mib=f"{_size_mb(rec)}M")
                add(row_text(d, ccols, width, self.sel == ("C", i), pulse=self.pulse),
                    stop=("C", i), data=rec)
        else:
            self._build_profiles(add, width, sel)
        return vrows

    def _visible_pcols(self, width, lblw):
        """Which host columns are visible. Returns (lo, hi, more_left, more_right)
        windowed around the cursor column; or None if not even one column fits
        (caller switches to transposed mode)."""
        colw = self.profile_col_widths()
        n = len(colw)
        avail = width - lblw - 4   # reserve for ‹ / › markers
        if avail < colw[self.pcol]:
            return None
        lo = hi = self.pcol
        used = colw[lo]
        while True:
            grew = False
            if hi + 1 < n and used + 1 + colw[hi + 1] <= avail:
                hi += 1
                used += 1 + colw[hi]
                grew = True
            if lo - 1 >= 0 and used + 1 + colw[lo - 1] <= avail:
                lo -= 1
                used += 1 + colw[lo]
                grew = True
            if not grew:
                break
        return lo, hi, lo > 0, hi < n - 1

    def _tlabel(self, t, w, base):
        """Target label: 'machine env · agent', env token colored, padded to w."""
        seg = Text()
        seg.append(f"{t['machine']} ", style=base)
        seg.append(t["env"], style=C_ENV.get(t["env"], base))
        seg.append(f" · {'agent' if t['agent'] else 'shell'}", style=base)
        if seg.cell_len < w:
            seg.append(" " * (w - seg.cell_len))
        return seg

    def _cell_visual(self, ti, j, locked):
        """(glyph_char, style) for a matrix cell, reflecting applied vs pending."""
        present = self.grid.get((ti, j), False)
        applied = self.applied.get((ti, j), False)
        agent = self.targets[ti]["agent"]
        if locked:
            return "✓", "grey50"
        if present and applied:
            return "✓", (C_PULSE[self.pulse] if agent else "#37b7ff")  # active
        if present and not applied:
            return "✓", "bold #ff9e3b"      # pending add (alternate highlight)
        if applied and not present:
            return "✗", "bold #ff5f5f"      # pending removal
        return "·", C_DIM                   # inactive

    def _profiles_button_row(self, width, focus, active_idx):
        dirty = self.grid_dirty()
        if not dirty:
            active_idx = 0          # Reset is unavailable -> not selectable
        n = self.pending_count()
        t = Text("  ")
        # Apply
        if dirty:
            alabel = f" ✓ Apply ({n}) "
            astyle = C_BTN_SEL if (focus and active_idx == 0) else "bold black on #ff9e3b"
        else:
            alabel = " ✓ Applied "
            astyle = C_BTN_SEL if (focus and active_idx == 0) else "bold black on green"
        t.append(alabel, style=astyle)
        t.append("   ")
        # Reset (only meaningful when dirty)
        if not dirty:
            rstyle = "grey42 on grey15"
        else:
            rstyle = self._btn_style(focus, active_idx == 1)
        t.append(" ↺ Reset ", style=rstyle)
        suffix = ("proposed changes are NOT active yet" if dirty
                  else "all profiles active · Enter applies from anywhere")
        if t.cell_len + 4 + len(suffix) <= width:
            t.append("    " + suffix, style=C_DIM)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def _build_profiles(self, add, width, sel):
        colw = self.profile_col_widths()
        lblw = 30
        vis = self._visible_pcols(width, lblw)
        if vis is None:
            return self._build_profiles_transposed(add, width, sel)
        lo, hi, ml, mr = vis
        hdr = Text(" " + "TARGET \\ HOST".ljust(lblw - 1), style=C_HEADER)
        hdr.append("‹" if ml else " ", style=C_HINT)
        for j in range(lo, hi + 1):
            if j > lo:
                hdr.append(" ")
            hdr.append_text(self._host_header_cell(j, colw[j]))
        hdr.append("›" if mr else " ", style=C_HINT)
        hdr.append(" " * max(0, width - hdr.cell_len))
        add(hdr, kind="colhdr")
        for ti, t in enumerate(self.targets):
            row_sel = sel == ("PR", ti)
            agent = t["agent"]
            base = "grey78" if agent else "grey54"
            r = Text(" ")
            # The active target row label gets the same subtle active shading the
            # active host column header uses, so both cursor coordinates read.
            lbl_text = self._tlabel(t, lblw - 1, base)
            if row_sel:
                lbl_text.stylize("on grey23")
            r.append_text(lbl_text)
            r.append(" ")
            for j in range(lo, hi + 1):
                if j > lo:
                    r.append(" ")
                locked = self.cell_locked(ti, j)
                ch, style = self._cell_visual(ti, j, locked)
                if row_sel and j == self.pcol:
                    style = "grey50 on grey23" if locked else C_SEL
                r.append(ch.center(colw[j]), style=style)
            r.append(" " * max(0, width - r.cell_len))
            add(r, stop=("PR", ti), data=t)
        add(Text(""))
        add(self._profiles_button_row(width, sel == ("BTN", 0), self.btn_idx), stop=("BTN", 0))

    def _build_profiles_transposed(self, add, width, sel):
        """Too narrow for a grid: one host is the header, each target a
        space-toggleable checkbox row. ◀▶ switches which host you're editing."""
        _lbl, hm, he = HOST_COLS[self.pcol]
        head = Text(" HOST  ", style=C_HEADER)
        head.append(hm, style=self._hl("bold white", True, False))
        head.append(" ", style=self._hl("", True, False))
        head.append(he, style=self._hl(C_ENV.get(he, "bold white"), True, False))
        head.append(f"   ‹ {self.pcol + 1}/{len(HOST_COLS)} ›  ◀▶ host",
                    style=C_HINT)
        head.append(" " * max(0, width - head.cell_len))
        add(head, kind="colhdr")
        for ti, t in enumerate(self.targets):
            agent = t["agent"]
            base = "grey78" if agent else "grey54"
            locked = self.cell_locked(ti, self.pcol)
            ch, cstyle = self._cell_visual(ti, self.pcol, locked)
            present = self.grid.get((ti, self.pcol), False)
            box = f"[{ch}]" if (present or ch == "✗") else "[ ]"
            r = Text(" ")
            r.append(" " + box + " ", style=cstyle)
            r.append_text(self._tlabel(t, width - 8, base))
            if sel == ("PR", ti):
                r.stylize("grey50 on grey23" if locked else C_SEL)
            r.append(" " * max(0, width - r.cell_len))
            add(r, stop=("PR", ti), data=t)
        add(Text(""))
        add(self._profiles_button_row(width, sel == ("BTN", 0), self.btn_idx), stop=("BTN", 0))

    def _hl(self, base, selected, focused):
        """Augment a base style with the selection highlight: the inversion
        cursor when the machine region is focused, a subtle bg when merely the
        active tab. Applied per-span so a highlight can be non-contiguous."""
        if not selected:
            return base
        extra = "reverse bold" if focused else "on grey23"
        return f"{base} {extra}".strip()

    def _machine_group(self, group, focused):
        """Render one machine's tab block: the machine (config) name once, then
        each of its environments as 'env marker', joined by ' · '. The name and
        the *selected* env+marker carry the highlight; sibling envs do not -- so
        selecting a secondary env shows a deliberate break in the highlight.
        Machine name takes the slot color (grey when idle, white when active);
        each env keeps its assigned per-env color."""
        first = self.machines[group[0]]
        label0, m0 = first[0], first[1]
        t = Text(" ")
        if label0 == "All":
            i = group[0]
            sel = i == self.machine_idx
            base = "bold white" if sel else C_TABOFF
            t.append("All", style=self._hl(base, sel, focused))
            t.append(" ")
            return t
        group_sel = any(i == self.machine_idx for i in group)
        all_disabled = all(self.machine_state(i) == "disabled" for i in group)
        name_base = (C_DISABLED if all_disabled
                     else "bold white" if group_sel else C_TABOFF)
        t.append(m0, style=self._hl(name_base, group_sel, focused))
        for n, i in enumerate(group):
            e = self.machines[i][2]
            state = self.machine_state(i)
            sel = i == self.machine_idx
            t.append(" ") if n == 0 else t.append(" · ", style=C_DIM)
            env_base = C_DISABLED if state == "disabled" else C_ENV.get(e, C_TABOFF)
            t.append(e, style=self._hl(env_base, sel, focused))
            t.append(" ", style=self._hl("", sel, focused))
            if state == "disabled":
                mk, mkb = "-", C_DISABLED
            elif state == "loading":
                mk, mkb = self.spin(), C_LOAD
            elif state == "failed":
                mk, mkb = "✗", C_WARN
            else:
                mk, mkb = "✓", C_READY
            t.append(mk, style=self._hl(mkb, sel, focused))
        t.append(" ")
        return t

    def tab_bar(self, width, focused):
        # Group consecutive flat entries by machine (the 'All' entry stands
        # alone) so a multi-env machine renders its name once.
        groups = []
        prev = None
        for i, (label, m, e, ok) in enumerate(self.machines):
            if label != "All" and m == prev:
                groups[-1].append(i)
            else:
                groups.append([i])
            prev = m if label != "All" else None
        blocks = [self._machine_group(g, focused) for g in groups]
        blen = [b.cell_len for b in blocks]
        ng = len(groups)
        selg = next((gi for gi, g in enumerate(groups) if self.machine_idx in g), 0)
        gap = 3
        budget = width - 6   # reserve for ‹ / › overflow markers
        lo = hi = selg
        used = blen[selg]
        while True:
            grew = False
            if hi + 1 < ng and used + gap + blen[hi + 1] <= budget:
                hi += 1
                used += gap + blen[hi]
                grew = True
            if lo - 1 >= 0 and used + gap + blen[lo - 1] <= budget:
                lo -= 1
                used += gap + blen[lo]
                grew = True
            if not grew:
                break
        t = Text(" ")
        t.append("‹ " if lo > 0 else "  ", style=C_HINT)
        for k in range(lo, hi + 1):
            if k > lo:
                t.append(" " * gap)
            t.append_text(blocks[k])
        t.append(" ›" if hi < ng - 1 else "  ", style=C_HINT)
        t.append(" " * max(0, width - t.cell_len))
        return t

    def status_text(self, compact=False):
        t = Text()
        if self.htab == 0:
            a, r, c = (len(sec[1]) for sec in self.current_list()[1])
            t.append("●", style=C_PULSE[self.pulse])
            if compact:
                t.append(f"{a} ", style="grey78")
                t.append(f"◷{r} ✓{c}", style="grey62")
            else:
                t.append(f" {a} active", style="grey78")
                t.append(" · ", style=C_DIM)
                t.append(f"{r} recent", style="grey70")
                t.append(" · ", style=C_DIM)
                t.append(f"{c} done", style="grey70")
        elif self.htab == 1:
            rows = self.cleanup_rows()
            mib = sum(_size_mb(w) for w in rows)
            if compact:
                t.append(f"⌫ {len(rows)}", style="grey70")
            else:
                t.append(f"{len(rows)} candidates · ~{mib} MiB", style="grey70")
        else:
            n = self.profiles_present()
            t.append("⚙ ", style="grey62")
            t.append(f"{n}{' set' if not compact else ''}", style="grey78")
        # Live mode: surface how many machines are still loading / failed so the
        # All view's streaming fill-in is legible.
        if self.live and self.loader is not None and self.htab == 0:
            _ready, loading, failed = self.loader.counts()
            if loading:
                t.append(f"  {self.spin()}{loading}", style=C_LOAD)
                if not compact:
                    t.append(" loading", style=C_LOAD)
            if failed:
                t.append(f"  ✗{failed}", style=C_WARN)
                if not compact:
                    t.append(" failed", style=C_WARN)
        return t

    def _top_pad(self, W, more_above, scrolled):
        left = f"▲ {scrolled} more above" if more_above else "▲"
        lstyle = C_HINT_ON if more_above else C_HINT
        use = self.status_text(False)
        if 1 + len(left) + 3 + use.cell_len + 1 > W:
            use = self.status_text(True)
        t = Text(" ")
        t.append(left, style=lstyle)
        gap = W - t.cell_len - use.cell_len - 1
        t.append(" " * max(1, gap))
        t.append_text(use)
        t.append(" " * max(0, W - t.cell_len))
        return t

    # ---- assemble full screen ----
    def render(self):
        W = self.size.width or 100
        H = self.size.height or 30
        top = self.topbar(W)              # [title, htabs]  (2 rows)
        foot = self.footer(W)
        # chrome rows: title, htabs, header-border, stats, bottom-border, footer
        body_h = max(1, H - len(top) - 4)
        vrows = self.build_body(W)
        self._ensure_visible(vrows, body_h)
        window = vrows[self.top: self.top + body_h]
        sticky = self._sticky(vrows, body_h)
        for i, s in enumerate(sticky):
            if i < len(window):
                window[i] = s
            else:
                window.append(s)
        body_lines = [vr.text if isinstance(vr, VRow) else vr for vr in window]
        while len(body_lines) < body_h:
            body_lines.append(Text(""))
        more_above = self.top > 0
        below = len(vrows) - (self.top + body_h)
        header_border = self._border_row(W, "▲", more_above)
        bottom_border = self._border_row(W, "▼", below > 0)
        stats = self._stats_row(W)
        lines = list(top) + [header_border, stats] + body_lines + [bottom_border, foot]
        # body offset = title+htabs+header_border+stats = len(top)+2
        modal = self.submenu or self.cleanup or self.optmenu or self.progress
        if modal:
            # Gray out ALL background content behind the dialog.
            lines = [Text((ln if isinstance(ln, Text) else Text(str(ln))).plain,
                          style="grey35") for ln in lines]
            off, bh = len(top) + 2, body_h
            if self.submenu:
                self._overlay_submenu(lines, W, off, bh)
            elif self.cleanup:
                self._overlay_scopedlg(self.cleanup, lines, W, off, bh, om=False)
            elif self.optmenu:
                self._overlay_scopedlg(self.optmenu, lines, W, off, bh, om=True)
            else:
                self._overlay_progress(lines, W, off, bh)
        out = Text()
        for i, ln in enumerate(lines):
            if i:
                out.append("\n")
            lt = ln if isinstance(ln, Text) else Text(str(ln))
            lt.truncate(W, overflow="crop")   # never let a line wrap
            out.append_text(lt)
        return out

    def _border_row(self, W, arrow, active):
        """A separator line carrying a centered scroll arrow with a blank space
        either side: ────── ▲ ──────. Arrow glows when there's more that way."""
        left = (W - 3) // 2
        rightn = W - 3 - left
        t = Text("─" * max(0, left), style=C_DIM)
        t.append(" ")
        t.append(arrow, style=C_HINT_ON if active else C_HINT)
        t.append(" ")
        t.append("─" * max(0, rightn), style=C_DIM)
        return t

    def _stats_row(self, W):
        """Left: the region's sub-pivot hint (◀ machine ▶ etc). Right: the
        section counts (with a glyph-compact fallback when narrow)."""
        hint = "Host  ◀▶" if self.htab == 2 else "Machine  Ctrl ◀▶"
        use = self.status_text(False)
        if 1 + len(hint) + 3 + use.cell_len + 1 > W:
            use = self.status_text(True)
        t = Text(" ")
        t.append(hint, style=C_DIM)
        gap = W - t.cell_len - use.cell_len - 1
        t.append(" " * max(1, gap))
        t.append_text(use)
        t.append(" " * max(0, W - t.cell_len))
        return t

    def _hint_row(self, W, direction, active, count):
        arrow = "▲" if direction == "up" else "▼"
        t = Text()
        if active:
            label = f"{arrow}  {count} more above" if direction == "up" \
                else f"{arrow}  {count} more below"
            pad = (W - len(label)) // 2
            t.append(" " * max(0, pad))
            t.append(label, style=C_HINT_ON)
        else:
            pad = (W - 1) // 2
            t.append(" " * max(0, pad))
            t.append(arrow, style=C_HINT)
        t.append(" " * max(0, W - t.cell_len))
        return t

    def topbar(self, W):
        # Right-side segments, dropped in this order as width shrinks:
        # version, branch, env, repo. Always kept: "Agent Worktrees" + machine.
        ver = f" · v{VERSION}"
        m, e = self.src.LOCAL
        host = f"{m.lower()}"
        present = {"version": True, "repo": True, "env": True, "branch": True}

        def build():
            left = Text(" Agent Worktrees", style="bold")
            if present["version"]:
                left.append(ver, style=C_DIM)
            right = Text("host ", style=C_DIM)
            right.append(host, style="grey70")
            if present["env"]:
                right.append(" · ", style=C_DIM)
                right.append(e, style=C_ENV.get(e, "grey70"))
            if present["repo"]:
                right.append("  ·  aperture-labs", style=C_DIM)
            if present["branch"]:
                right.append(" · master", style=C_DIM)
            return left, right

        for drop in ("version", "branch", "env", "repo"):
            left, right = build()
            if left.cell_len + 1 + right.cell_len + 1 <= W:
                break
            present[drop] = False
        left, right = build()
        l1 = left
        gap = W - left.cell_len - right.cell_len - 1
        l1.append(" " * max(1, gap))
        l1.append_text(right)
        l1.append(" " * max(0, W - l1.cell_len))
        l2 = Text("  ")
        v_focus = self.sel[0] == "V"
        for i, label in enumerate(HTABS):
            if i:
                l2.append("     ")
            if i == self.htab:
                l2.append(label.upper(),
                          style="reverse bold orange1" if v_focus else C_BAND)
            else:
                l2.append(label, style="white" if v_focus else C_TABOFF)
        hint = ("Tab/◀▶ switch · ↓ body " if v_focus else "[ ] view ")
        l2.append(hint.rjust(max(1, W - l2.cell_len)), style=C_DIM)
        return [l1, l2]

    def footer(self, W):
        dlg = self.cleanup or self.optmenu
        if self.progress:
            hints = ("Esc cancel" if not self.progress["done"]
                     else "Enter/Esc close")
        elif self.submenu:
            hints = "↑↓ choose · Enter run · Esc back"
        elif dlg:
            if dlg.get("section", 0) == 0:
                hints = "↑↓ move · Space toggle · Tab → buttons · Enter next · Esc cancel"
            else:
                hints = "◀▶ button · Enter select · ↑ back to options · Esc cancel"
        elif self.sel[0] == "V":
            hints = "◀▶ or Tab switch view · ↓ into body · ^⇧◀▶ view"
        elif self.sel[0] == "M":
            hints = "◀▶ switch machine · Tab region · ↑↓ move · ^◀▶ machine"
        elif self.sel[0] in ("L", "C"):
            hints = "Enter open · Space sub-menu · Tab region · ^◀▶ machine"
        elif self.sel[0] == "BTN":
            btn = self.active_button()
            if btn in ("N", "NO"):
                hints = "◀▶ button · Enter activate · Tab region · ^◀▶ machine"
            elif btn in ("K", "SY"):
                hints = "◀▶ button · Enter open dialog · Tab region"
            elif btn in ("PA", "PReset"):
                hints = "◀▶ Apply/Reset · Enter activate · ↑ grid · Tab region"
            else:
                hints = ""
        elif self.sel[0] == "PR":
            hints = "Space toggle · ◀▶ host · ↑↓ target · Tab → buttons · Enter → Apply"
        else:
            hints = ""
        f = Text(" " + hints, style="grey70")
        dbg = f"· {self.debug} "
        f.append(dbg.rjust(max(1, W - f.cell_len)), style=C_DIM)
        if f.cell_len > W:
            f = Text(f.plain[:W])
        return f

    # ---- scroll + sticky ----
    def _stop_line(self, vrows, stop):
        for i, vr in enumerate(vrows):
            if vr.stop == stop or (isinstance(vr.stop, list) and stop in vr.stop):
                return i
        return 0

    def _ensure_visible(self, vrows, body_h):
        line = self._stop_line(vrows, self.sel)
        if line < self.top:
            self.top = line
        elif line >= self.top + body_h:
            self.top = line - body_h + 1
        self.top = max(0, min(self.top, max(0, len(vrows) - body_h)))

    def _sticky(self, vrows, body_h):
        if self.top <= 0 or self.top >= len(vrows):
            return []
        first = vrows[self.top]
        pins = []
        pb = first.pin_band
        ps = first.pin_section
        if pb and pb[1] < self.top:
            pins.append(self._pin_line(pb[0], "band"))
        if ps and ps[1] < self.top:
            pins.append(self._pin_line(ps[0], "section"))
        return pins

    def _pin_line(self, label, kind):
        W = self.size.width or 100
        if kind == "band":
            t = Text(f" {label}", style=C_BAND)
        else:
            t = Text(f"  ── {label} ", style=C_SECTION)
        t.append(" " * max(0, W - t.cell_len))
        t.stylize("on grey15")  # subtle pinned background
        return t

    # ---- modal panels ----
    def _prow(self, content, pw, selected=False, style=None, border=True):
        s = content if isinstance(content, str) else str(content)
        if len(s) > pw - 2:
            s = s[: pw - 3] + "…"
        inner = Text(s.ljust(pw - 2), style=style or "white")
        if selected:
            inner.stylize(C_SEL)
        if not border:
            return inner
        row = Text("│", style=C_DIM)
        row.append_text(inner)
        row.append("│", style=C_DIM)
        return row

    def _blit_panel(self, lines, W, panel, top_off, body_h):
        pw = panel[0].cell_len
        x = max(0, (W - pw) // 2)
        y0 = top_off + max(0, (body_h - len(panel)) // 2)
        for j, prow in enumerate(panel):
            yi = y0 + j
            if 0 <= yi < len(lines):
                base = lines[yi]
                bp = (base if isinstance(base, Text) else Text(str(base))).plain
                left = bp[:x] if len(bp) >= x else bp + " " * (x - len(bp))
                newt = Text(left, style=C_DIM)
                newt.append_text(prow)
                lines[yi] = newt

    def _overlay_submenu(self, lines, W, top_off, body_h):
        rec = self.submenu["rec"]
        acts = self.submenu["actions"]
        idx = self.submenu_idx
        pw = min(W - 8, 72)
        title = f" {rec.get('title', '')}"
        meta1 = (f" {rec.get('id4')} · {rec.get('machine')} · {rec.get('env')}"
                 f" · {rec.get('state')}")
        meta2 = (f" age {rec.get('age')} · sess {rec.get('sess')}"
                 f" · turns {rec.get('turns')} · PR {rec.get('pr')}")
        panel = [Text("╭" + "─" * (pw - 2) + "╮", style=C_DIM)]
        panel.append(self._prow(title, pw, style="bold"))
        panel.append(self._prow(meta1, pw, style=C_DIM))
        panel.append(self._prow(meta2, pw, style=C_DIM))
        panel.append(self._prow("", pw))
        for i, a in enumerate(acts):
            mark = " ▸ " if i == idx else "   "
            panel.append(self._prow(mark + a, pw, selected=(i == idx)))
        panel.append(self._prow("", pw))
        desc = ACTION_DESC.get(acts[idx], "")
        panel.append(self._prow(" " + desc, pw, style="grey62"))
        panel.append(Text("╰" + "─" * (pw - 2) + "╯", style=C_DIM))
        self._blit_panel(lines, W, panel, top_off, body_h)

    def _overlay_scopedlg(self, dlg, lines, W, top_off, body_h, om=False):
        opts = dlg["opts"]
        idx = dlg["idx"]
        section = dlg.get("section", 0)
        bidx = dlg.get("bidx", 0)
        verb = dlg.get("verb", "Clean up")
        if om:
            tm, te = dlg["target"]
            scope = f"{tm} {te}"
        else:
            scope = dlg["scope"]
        pw = min(W - 8, 66)
        header = f"─ {verb} · {scope} "
        panel = [Text("╭" + header + "─" * max(0, pw - 2 - len(header)) + "╮",
                      style=C_BAND)]
        panel.append(self._prow(f" {dlg.get('prompt', 'Select:')}", pw,
                                style="bold white"))
        panel.append(self._prow("", pw))
        opt_focus = section == 0
        for i, o in enumerate(opts):
            box = "[x]" if o["on"] else "[ ]"
            mark = " ▸ " if (opt_focus and idx == i) else "   "
            boxc = "green" if o["on"] else "grey50"
            row = Text("│", style=C_DIM)
            inner = Text(mark)
            inner.append(box, style=boxc)
            inner.append(f" {o['label']:<12} ", style="white")
            inner.append(o["hint"], style="grey70")
            s = inner.plain
            if len(s) > pw - 2:
                inner = Text(s[:pw - 3] + "…", style="white")
            inner.append(" " * max(0, pw - 2 - inner.cell_len))
            if opt_focus and idx == i:
                inner.stylize(C_SEL)
            row.append_text(inner)
            row.append("│", style=C_DIM)
            panel.append(row)
        panel.append(self._prow("", pw))
        # button row: [Confirm/Create] [Cancel]
        clabel = dlg.get("confirm", "Confirm")
        if not om:
            clabel = f"{clabel} ({len(self._cleanup_union())})"
        brow = Text("│", style=C_DIM)
        inner = Text("   ")
        inner.append(f" {clabel} ",
                     style=C_BTN_SEL if (section == 1 and bidx == 0) else C_BTN)
        inner.append("   ")
        inner.append(" Cancel ",
                     style=C_BTN_SEL if (section == 1 and bidx == 1) else C_BTN)
        inner.append(" " * max(0, pw - 2 - inner.cell_len))
        brow.append_text(inner)
        brow.append("│", style=C_DIM)
        panel.append(brow)
        panel.append(Text("╰" + "─" * (pw - 2) + "╯", style=C_DIM))
        self._blit_panel(lines, W, panel, top_off, body_h)

    def _overlay_progress(self, lines, W, top_off, body_h):
        """Per-worktree progress for a cleanup/sync run: each selected worktree
        as a row that advances pending(·) -> running(spinner) -> done(✓)/✗."""
        p = self.progress
        items = p["items"]
        pw = min(W - 8, 66)
        done = sum(1 for it in items if it["state"] in ("done", "failed"))
        failed = sum(1 for it in items if it["state"] == "failed")
        verb = p["verb"]
        header = f"─ {verb} · {p['scope']} "
        panel = [Text("╭" + header + "─" * max(0, pw - 2 - len(header)) + "╮",
                      style=C_BAND)]
        if not p.get("armed", True):
            n = len(items)
            extra = []
            if p.get("include_unused"):
                extra.append("unused")
            if p.get("include_conversations"):
                extra.append("conversation")
            tail = f" incl. {'/'.join(extra)}" if extra else ""
            sub = f" ⚠ remove {n} worktree(s){tail}? Enter=proceed Esc=cancel"
            substyle = "bold yellow"
        elif p["done"]:
            sub = f" done · {done}/{len(items)}" + (f" · {failed} failed" if failed else "")
            substyle = "bold white"
        else:
            sub = f" {self.spin()} working… {done}/{len(items)}"
            substyle = "bold white"
        panel.append(self._prow(sub, pw, style=substyle))
        panel.append(self._prow("", pw))
        # Window the list around the currently-running item if it's long.
        maxr = max(3, body_h - 8)
        run = next((j for j, it in enumerate(items) if it["state"] == "running"),
                   len(items) - 1)
        lo = max(0, min(run - maxr // 2, max(0, len(items) - maxr)))
        for it in items[lo:lo + maxr]:
            st = it["state"]
            if st == "done":
                g, gc = "✓", C_READY
            elif st == "failed":
                g, gc = "✗", C_WARN
            elif st == "running":
                g, gc = self.spin(), C_LOAD
            else:
                g, gc = "·", C_DIM
            row = Text("│", style=C_DIM)
            inner = Text("  ")
            inner.append(g, style=gc)
            inner.append(f" {it['id4']} ", style="grey70")
            inner.append(it["title"], style="white" if st != "pending" else "grey54")
            s = inner.plain
            if len(s) > pw - 2:
                inner = Text(s[:pw - 3] + "…", style="white")
            inner.append(" " * max(0, pw - 2 - inner.cell_len))
            row.append_text(inner)
            row.append("│", style=C_DIM)
            panel.append(row)
        if len(items) > maxr:
            panel.append(self._prow(f" … {len(items)} total", pw, style="grey54"))
        panel.append(self._prow("", pw))
        brow = Text("│", style=C_DIM)
        inner = Text("   ")
        if not p.get("armed", True):
            inner.append(" Confirm ", style=C_BTN_SEL)
            inner.append("  Cancel ", style=C_BTN)
        elif p["done"]:
            inner.append(" Close ", style=C_BTN_SEL)
        else:
            inner.append(" Working… ", style=C_BTN)
        inner.append(" " * max(0, pw - 2 - inner.cell_len))
        brow.append_text(inner)
        brow.append("│", style=C_DIM)
        panel.append(brow)
        panel.append(Text("╰" + "─" * (pw - 2) + "╯", style=C_DIM))
        self._blit_panel(lines, W, panel, top_off, body_h)

    def handle_key(self, key):
        # Remember the grid row before any navigation, so Tab out/in restores it.
        if self.sel and self.sel[0] == "PR":
            self.last_pr = self.sel[1]
        if self.progress:
            return self._key_progress(key)
        if self.submenu:
            return self._key_submenu(key)
        if self.cleanup:
            return self._key_scopedlg(key)
        if self.optmenu:
            return self._key_scopedlg(key, om=True)

        # Global region shortcuts:
        #   Ctrl+Shift+←/→  -> rotate the top View pivot
        #   Ctrl+←/→ (and [ ]) -> rotate the machine picker
        if key in ("ctrl+shift+left", "ctrl+shift+right"):
            return self._switch_pivot(1 if key.endswith("right") else -1)
        if key in ("[", "left_square_bracket"):
            return self._switch_pivot(-1)
        if key in ("]", "right_square_bracket"):
            return self._switch_pivot(1)
        if key in ("ctrl+left", "ctrl+right"):
            return self._rotate_machine(1 if key.endswith("right") else -1)

        zone = self.sel[0]

        # Tab / Shift+Tab jump between major regions (View / Machine / Buttons /
        # the table or grid).
        if key in ("tab", "shift+tab"):
            heads = self.region_heads()
            cur = self.region_head(zone)
            i = heads.index(cur) if cur in heads else 0
            d = 1 if key == "tab" else -1
            self.sel = heads[(i + d) % len(heads)]
            return

        # Bare ←/→ move to the next item *within* the focused region.
        if key in ("left", "right"):
            d = 1 if key == "right" else -1
            if zone == "V":
                self._switch_pivot(d)            # stays in V
            elif zone == "M":
                self._rotate_machine(d)
            elif zone == "BTN":
                bset = self.button_set()
                if bset:
                    self.btn_idx = (self.btn_idx + d) % len(bset)
            elif zone == "PR":
                self.pcol = (self.pcol + d) % len(HOST_COLS)
            # L / C rows: bare ←/→ is a no-op (machine = Ctrl+←/→)
            return

        stops = self.stops()
        if self.sel not in stops:
            self.sel = self.default_sel()
        idx = stops.index(self.sel)
        zone = self.sel[0]

        if key == "down":
            self.sel = stops[min(idx + 1, len(stops) - 1)]
        elif key == "up":
            self.sel = stops[max(idx - 1, 0)]
        elif key in ("pagedown", "pageup"):
            self._page(stops, idx, forward=(key == "pagedown"))
        elif key == "enter":
            self._activate()
        elif key == "space":
            if zone in ("L", "C"):
                self._open_submenu()
            elif zone == "PR":
                self._toggle_cell()
            elif zone == "BTN":
                self._activate()
        elif key == "r":
            self.debug = "refresh (mock: re-loaded snapshots)"
            self.setup()
            self.sel = self.default_sel()
        elif key in ("q", "escape"):
            self.app.exit()

    def _rotate_machine(self, d):
        self.machine_idx = (self.machine_idx + d) % len(self.machines)
        if self.sel not in self.stops():
            self.sel = self.default_sel()

    def _switch_pivot(self, d):
        was_v = self.sel[0] == "V"
        self.htab = (self.htab + d) % 3
        self.btn_idx = 0
        self.top = 0
        # Stay in the View nav if that's where focus was; otherwise land on the
        # new pivot's default body stop.
        self.sel = ("V", 0) if was_v else self.default_sel()

    def _toggle_cell(self):
        ti = self.sel[1]
        if self.cell_locked(ti, self.pcol):
            self.debug = "self · agent profile is mandatory (locked)"
            return
        key = (ti, self.pcol)
        self.grid[key] = not self.grid.get(key, False)
        t = self.targets[ti]
        _lbl, hm, he = HOST_COLS[self.pcol]
        host = f"{hm} {he}"
        self.debug = (f"{'+' if self.grid[key] else '-'} {host} → {t['label']}"
                      " (pending Apply)")

    def _page(self, stops, idx, forward):
        anchors = [a for a in self.anchors() if a in stops]
        positions = sorted(stops.index(a) for a in anchors)
        if forward:
            nxt = next((p for p in positions if p > idx), positions[-1] if positions else idx)
        else:
            nxt = next((p for p in reversed(positions) if p < idx),
                       positions[0] if positions else idx)
        self.sel = stops[nxt]

    def _key_submenu(self, key):
        acts = self.submenu["actions"]
        if key == "down":
            self.submenu_idx = (self.submenu_idx + 1) % len(acts)
        elif key == "up":
            self.submenu_idx = (self.submenu_idx - 1) % len(acts)
        elif key == "enter":
            act = acts[self.submenu_idx]
            rec = self.submenu["rec"]
            self.submenu = None
            if act in ("Open", "Resume"):
                self._decide(self._resume_decision(rec))
            else:
                # Sync / Create PR / Kill session / Reset env / Cleanup are
                # deferred ops (full design pending) -- keep the mock note.
                self.debug = f"{act} -> {rec.get('id4')} (mock)"
        elif key in ("escape", "q", "space", "tab"):
            self.submenu = None

    def _key_scopedlg(self, key, om=False):
        dlg = self.optmenu if om else self.cleanup
        opts = dlg["opts"]
        n = len(opts)
        if key in ("escape", "q"):
            self._close_dlg(om)
            return
        if key in ("tab", "shift+tab"):
            dlg["section"] = 1 - dlg["section"]
            dlg["bidx"] = 0
            return
        if dlg["section"] == 0:           # the multi-choice menu
            if key == "down":
                dlg["idx"] = min(dlg["idx"] + 1, n - 1)
            elif key == "up":
                dlg["idx"] = max(dlg["idx"] - 1, 0)
            elif key == "space":
                opts[dlg["idx"]]["on"] = not opts[dlg["idx"]]["on"]
            elif key == "enter":
                dlg["section"] = 1        # progress to the button row
                dlg["bidx"] = 0
        else:                              # the [Confirm] [Cancel] button row
            if key in ("left", "right"):
                dlg["bidx"] = 1 - dlg["bidx"]
            elif key == "up":
                dlg["section"] = 0
            elif key == "enter":
                if dlg["bidx"] == 0:
                    self._dlg_confirm(om)
                else:
                    self._close_dlg(om)

    def _close_dlg(self, om):
        if om:
            self.optmenu = None
        else:
            self.cleanup = None

    def _key_progress(self, key):
        p = self.progress
        # Unarmed: the extra confirm gate (beyond-clean cleanup). Enter proceeds,
        # Esc cancels without touching anything.
        if not p.get("armed", True):
            if key in ("enter", "space"):
                p["armed"] = True
                self._start_progress()
            elif key in ("escape", "q"):
                self.progress = None
                self.executor = None
                self.debug = f"{p['verb'].lower()} cancelled · 0 worktrees"
            return
        if key in ("escape", "q") or (p["done"] and key in ("enter", "space")):
            verb = p["verb"].lower()
            n = len(p["items"])
            failed = sum(1 for it in p["items"] if it["state"] == "failed")
            state = "complete" if p["done"] else "cancelled"
            sim = "" if self.executor is not None else " (sim)"
            self.progress = None
            self.executor = None
            tail = f" · {failed} failed" if failed else ""
            self.debug = f"{verb} {state} · {n} worktrees{tail}{sim}"

    def _dlg_confirm(self, om):
        if om:
            dlg = self.optmenu
            on = {o["label"] for o in dlg["opts"] if o["on"]}
            tm, te = dlg["target"]
            self.optmenu = None
            self._decide({
                "action": "new", "machine": tm, "env": te,
                "is_local": (tm, te) == self.src.LOCAL,
                "options": {
                    "anchor": "Anchor repo" in on,
                    "bare": "Bare" in on,
                    "no_mux": "No Mux" in on,
                    "local_model": "Local model" in on,
                },
            })
        else:
            dlg = self.cleanup
            picked = [o["label"] for o in dlg["opts"] if o["on"]]
            verb = dlg.get("verb", "Clean up")
            op = "sync" if verb.lower().startswith("sync") else "cleanup"
            ids = self._cleanup_union()
            recs = [w for w in self.data if w["id4"] in ids]
            self.cleanup = None
            if not recs:
                self.debug = (f"{verb.lower()} {dlg['scope']}: "
                              f"{', '.join(picked) or 'nothing'} → 0 worktrees")
                return
            # Extra confirm when a cleanup scope reaches past 'clean'
            # (Unused / Conversation-only / All) -- removing idle/empty trees
            # or trees that held conversation is a bigger commitment.
            include_unused = any(
                p in ("Unused", "All eligible") for p in picked)
            include_conversations = any(
                p in ("Conversation-only", "All eligible") for p in picked)
            beyond_clean = op == "cleanup" and (
                include_unused or include_conversations)
            items = [{"id4": w["id4"], "title": w["title"],
                      "machine_env": w["machine_env"], "state": "pending"}
                     for w in recs]
            self.progress = {
                "verb": verb, "op": op, "scope": dlg["scope"], "items": items,
                "recs": recs, "picked": picked,
                "ticks": 0, "steps": 3, "done": False,
                "armed": not beyond_clean,
                "include_unused": include_unused,
                "include_conversations": include_conversations,
            }
            if self.progress["armed"]:
                self._start_progress()

    def _start_progress(self):
        """Begin executing the armed progress run (real executor or mock walk)."""
        p = self.progress
        if not self.real_ops:
            return  # mock walker advances via _advance_progress
        from . import maintenance
        tasks = maintenance.build_tasks(
            p["op"], p["recs"], self.src,
            include_unused=p["include_unused"],
            include_conversations=p["include_conversations"],
        )
        self.executor = maintenance.MaintenanceExecutor(p["op"], tasks)
        self.executor.start()

    def _poll_executor(self):
        """Pull real per-item states from the maintenance executor."""
        p = self.progress
        ex = self.executor
        for it in p["items"]:
            it["state"] = ex.state(it["id4"])
        if ex.is_done():
            p["done"] = True

    def _cleanup_union(self):
        s = set()
        for o in self.cleanup["opts"]:
            if o["on"]:
                s |= o["ids"]
        return s

    def _selected_record(self):
        zone, i = self.sel
        if zone == "L":
            arr = self.list_records()
        elif zone == "C":
            arr = self.cleanup_rows()
        else:
            return None
        return arr[i] if 0 <= i < len(arr) else None

    def _decide(self, decision):
        """Record a launch decision and exit the TUI so __main__ can act on it.

        ``PickerApp.result`` stays ``None`` on cancel (q/Esc); a dict here is a
        concrete instruction the caller maps onto resume/create/remote paths.
        """
        self.app.result = decision
        self.app.exit()

    def _resume_decision(self, rec):
        """Build the resume decision for a worktree row/submenu selection."""
        raw = rec.get("raw") or {}
        m, e = rec.get("machine"), rec.get("env")
        return {
            "action": "resume",
            "worktree_id": raw.get("id"),
            "id4": rec.get("id4"),
            "machine": m,
            "env": e,
            "title": rec.get("title"),
            "is_local": (m, e) == self.src.LOCAL,
        }

    def _activate(self):
        zone = self.sel[0]
        if zone == "V":
            self.sel = ("M", 0) if self.htab in (0, 1) else ("PR", 0)
        elif zone == "M":
            self.sel = ("BTN", 0)
        elif zone == "PR":
            self.btn_idx = 0            # progress to the Apply button
            self.sel = ("BTN", 0)
        elif zone == "BTN":
            btn = self.active_button()
            if btn == "N":
                tm, te = self.create_target()
                self._decide({
                    "action": "new", "machine": tm, "env": te,
                    "is_local": (tm, te) == self.src.LOCAL, "options": {},
                })
            elif btn == "NO":
                self._open_optmenu()
            elif btn == "K":
                self._open_cleanup()
            elif btn == "SY":
                self._open_sync()
            elif btn == "PReset":
                self.grid = dict(self.applied)
                self.debug = "reset · reverted to applied profiles (mock)"
            elif btn == "PA":
                self.applied = dict(self.grid)
                self.debug = f"Applied · {self.profiles_present()} profiles now active (mock)"
        else:
            rec = self._selected_record()
            if rec:
                self._decide(self._resume_decision(rec))

    def _local_model_hosts(self):
        """Machines that can host a local model (the agent runs on their GPU).

        Convention-driven from local-machine config -- intentionally NOT
        hardcoded. The data source may advertise the capability via
        ``src.local_model_hosts`` (a set of machine display names); until that
        convention is wired, this is empty, so the 'Local model' create option
        does not appear.
        """
        return set(getattr(self.src, "local_model_hosts", None) or ())

    def _open_optmenu(self):
        tm, te = self.create_target()
        opts = [
            {"label": "Anchor repo", "on": False,
             "hint": "launch in the main checkout, not a worktree"},
            {"label": "Bare", "on": False,
             "hint": "new worktree, no Copilot bootstrap"},
            {"label": "No Mux", "on": False, "hint": "skip PSMux/TMux wrapper"},
        ]
        if tm in self._local_model_hosts():
            opts.append({"label": "Local model", "on": False,
                         "hint": f"run the agent on {tm}'s GPU"})
        self.optmenu = {"target": (tm, te), "idx": 0, "section": 0, "bidx": 0,
                        "verb": "New worktree", "confirm": "Create", "opts": opts}

    def _open_submenu(self):
        rec = self._selected_record()
        if not rec:
            return
        acts = ["Open", "Resume", "Sync"]
        if not rec.get("pr", "").endswith("✓"):   # no merged PR yet
            acts.append("Create PR")
        if rec.get("sess", "").startswith("●"):
            acts.append("Kill session")
        acts += ["Reset env", "Cleanup"]
        self.submenu = {"rec": rec, "actions": acts}
        self.submenu_idx = 0

    def _scope_label(self):
        return "All machines" if self.is_all() else \
            "{} {}".format(*self.cur_machine()[:2])

    def _open_cleanup(self):
        scope = self._scope_label()
        rows = self.cleanup_rows()
        clean = {w["id4"] for w in rows if w["cleanup_bucket"] == "clean"}
        unused = {w["id4"] for w in rows if w["cleanup_bucket"] == "unused"}
        convo = {w["id4"] for w in rows if w["cleanup_bucket"] == "conversation"}
        gone = {w["id4"] for w in rows if w["cleanup_bucket"] == "gone"}
        all_eligible = clean | unused | convo | gone
        self.cleanup = {
            "verb": "Clean up", "prompt": "Select what to prune:",
            "confirm": "Confirm", "scope": scope, "idx": 0, "section": 0, "bidx": 0,
            "opts": [
                {"label": "Merged & finalized", "on": True, "ids": clean,
                 "hint": f"work is on the default branch · {len(clean)}"},
                {"label": "Unused", "on": False, "ids": unused,
                 "hint": f"no commits, no conversation · {len(unused)}"},
                {"label": "Conversation-only", "on": False, "ids": convo,
                 "hint": f"no commits, but the session has chat history "
                         f"· {len(convo)}"},
                {"label": "All eligible", "on": False, "ids": all_eligible,
                 "hint": f"every prunable worktree · {len(all_eligible)}"},
            ],
        }

    def _open_sync(self):
        scope = self._scope_label()
        if self.is_all():
            rows = list(self.data)
        else:
            m, e, _ = self.cur_machine()
            rows = [w for w in self.data if w["machine"] == m and w["env"] == e]
        eligible = {w["id4"] for w in rows if w.get("ff_eligible")}
        skipped = len(rows) - len(eligible)
        self.cleanup = {
            "verb": "Sync", "prompt": "Fast-forward worktrees onto the default "
            "branch (FF-only):",
            "confirm": "Confirm", "scope": scope, "idx": 0, "section": 0, "bidx": 0,
            "opts": [
                {"label": "Eligible", "on": True, "ids": eligible,
                 "hint": f"clean · behind · no local commits · {len(eligible)}"
                         + (f"  ({skipped} skipped: ahead/dirty/active)"
                            if skipped else "")},
            ],
        }

    # Textual entry point
    def on_key(self, event):
        event.stop()
        event.prevent_default()
        key = event.key
        if event.character in ("[", "]"):
            key = event.character
        self.handle_key(key)
        self.refresh()


def _size_mb(w):
    # deterministic pseudo-size from id for the cleanup demo
    return 120 + (int(w["id4"], 16) % 300)


CLEAN_SPECS = [
    ("id4", "id", 4, "l", 2), ("state", "state", 6, "l", 6),
    ("machine_env", "machine env", 19, "l", 7),
    ("dispo", "disposition", 18, "l", 3), ("pr", "pr", 8, "l", 5),
    ("age", "age", 4, "l", 9), ("mib", "size", 6, "r", 9),
    ("title", "title", 10, "l", 1),
]
PROF_SPECS = [
    ("name", "name", 22, "l", 1), ("app", "host app", 12, "l", 3),
    ("scope", "scope (machine · env)", 20, "l", 2), ("status", "status", 13, "l", 4),
]


class PickerApp(App):
    CSS = """
    Screen { background: $surface; }
    PickerScreen { width: 100%; height: 100%; }
    """

    def __init__(self, source, live=False):
        super().__init__()
        self._source = source
        self._live = live
        self.result = None            # set by the screen on a launch decision

    def compose(self) -> ComposeResult:
        yield PickerScreen(self._source, self._live)
