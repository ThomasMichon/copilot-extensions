# Mux-bind Relay — Ctrl+K Keybind + Clickable Status Region

- **Slug:** `mux-bind-relay`
- **Repo:** copilot-extensions — `worktree-manager` (Mux Companion launch
  surface) + `agent-worktrees` (status segment this relay consumes)
- **Branch(es):** per-phase model
- **Created:** 2026-10-05
- **Status:** Active
- **Vision:** [`visions/mux-companion`](../../../visions/mux-companion/README.md)
  — delivers *hotkey-summoned-status-explainer*'s first concrete mechanism,
  plus the new *mux-bind-keybind-relay* / *mux-bind-clickable-status-region*
  Features

## Guiding Intent

`mux-companion-manual-cutover-diagnostics` (#4369) built the Companion dialog
itself — status explainer, session lineage, a "Cut over" button — and its
Validation Plan's two live-test items sat unchecked because there was no way
to actually SUMMON the Companion inside a real muxed session. Investigating
why surfaced that **no keybind wiring exists anywhere in the codebase**: the
vision's own "hotkey-summoned" framing was aspirational, never implemented.
This effort builds the actual relay: a Ctrl+K root-key-table binding (every
muxed worktree session, psmux today) that pops the Companion, plus a
clickable status-right region as a second, discoverable summon path.

## Context

### Prior art (reused, not rebuilt)

- `worktree_manager.mux_companion:run` — the Companion program itself,
  already built, tested, merged. This effort adds nothing to its own
  behavior; it only makes it reachable without a manual CLI invocation.
- `worktree-manager/bin/session-options.ps1`'s `Set-AwPsmuxSessionOptions` —
  the established per-session psmux option-stamping pattern (status bar,
  mouse behaviors) this effort's keybind function mirrors exactly.
- `worktree-manager/bin/psmux-passthrough.conf` + `Invoke-AwPsmuxPassthrough`
  — the established pattern for applying root-key-table directives via
  `source-file -t <session>` (confirmed: command-line `bind-key` silently
  no-ops on psmux; only `source-file` reliably applies key-table directives —
  verified on psmux 3.3.3/3.3.6 per that file's own comment, reconfirmed here
  on 3.3.5).
- `worktree-manager/bin/launch-session.ps1`'s `Invoke-ManagedMuxRegister` —
  the `uv run --quiet --project <managerRoot> -m worktree_manager ...`
  invocation pattern this effort's popup command reuses, avoiding any need
  for a separately-resolved `worktree-manager` binstub path.

### Platform asymmetry (load-bearing design constraint)

psmux runs **one server per session** — a worktree-scoped root-table binding
is inherently session-scoped and safe by construction (`source-file -t
<session>`). tmux runs **one shared server** for every session on the
machine — an unscoped worktree-specific binding would leak onto the
operator's own unrelated tmux sessions, which is exactly why
`apply-mux-keybinds.sh`'s server-global prefix/root-unbind is opt-in there,
and why `psmux-passthrough.conf` is explicitly marked "psmux-only." **This
effort's Phase 1 is psmux (Windows) only**, matching that existing
asymmetry. tmux/Linux parity (a session-name-conditioned bind, e.g. `if-shell
-F '#{m:wt-*,#{session_name}}' ...`, never an unconditional server-global
bind) is tracked as Phase 2, not blocking this effort's Done state unless the
operator says otherwise.

### Validation constraint (also load-bearing)

Neither a bound hotkey nor a clickable status-region click can be fired by
`tmux send-keys`/command-line automation — `send-keys` injects characters
directly into the pane's pty, bypassing the mux client's own key-table
dispatch entirely (confirmed empirically: a `send-keys "C-k"` against a
session with a real `source-file`-registered `C-k` root-table bind produces
no effect, while the identical bind fires correctly for a real attached
keyboard event). This effort can therefore prove the **registration** is
correct (`list-keys -T root` shows the bind; the underlying `uv run ...
companion` popup command works when invoked directly) but the **live
keypress-fires-popup** and **live-click-fires-popup** confirmations are the
operator's own to exercise, same as `mux-companion-manual-cutover-
diagnostics`' own Validation Plan pattern.

## Request

Operator ask, driving this effort directly out of the handoff-cutover
validation leg: "Continue driving. Mux-bind, then we test handoff cutover,
to deal with the prececessor-retire issue." Build the actual Ctrl+K relay
(and a clickable status region, "clickable_too") so the Companion is
genuinely summonable inside a live session, THEN use it to validate the
remaining predecessor-retire gap from `mux-companion-manual-cutover-
diagnostics`.

## Plan

### Step 1 — Ctrl+K root-table bind (psmux)
- [ ] `session-options.ps1`: new `Set-AwMuxCompanionKeybindSafe -Session
      <name> -ManagerRoot <path>` mirroring `Set-AwPsmuxSessionOptions`'s
      shape, applying `bind-key -T root C-k display-popup -E -w 80% -h 80%
      "uv run --quiet --project <ManagerRoot> -m worktree_manager
      companion"` via a generated, session-scoped `source-file`.
- [ ] `launch-session.ps1`: call the new Safe wrapper at both session
      create and join/attach points (same two call sites as
      `Invoke-AwPsmuxPassthroughSafe`), **after** passthrough (which
      unconditionally `unbind-key -a -T root`s) so the Companion bind is
      never wiped by it.
- [ ] Tests: assert the generated `source-file` fragment's exact content
      (the `bind-key -T root C-k ...` line, correct `ManagerRoot`
      interpolation) without requiring a live psmux server.

### Step 2 — Clickable status-right region (psmux)
- [ ] Extend the status-right format Mux-bind pushes with a named clickable
      range (`#[range=user|mux-companion]...#[norange]`) rendering a short,
      discoverable label (e.g. `⌘K` or `[K]`).
- [ ] Bind `MouseDown1Status` (session-scoped, same `source-file` fragment as
      Step 1) to an `if-shell -F '#{==:#{mouse_status_range},mux-companion}'`
      dispatch: the Companion popup command on a match, `select-window -t=`
      (today's default) otherwise.
- [ ] Tests: assert the generated fragment's exact content for both the
      format-string change and the conditional bind.

### Step 3 — Vision + effort documentation
- [x] Revise `visions/mux-companion`: add *mux-bind-keybind-relay* and
      *mux-bind-clickable-status-region* Features, revise the "Not a general
      status-bar click framework" Non-Goal, document the platform-asymmetry
      design constraint, promote Status Draft -> Active.
- [x] This effort doc.

## Validation Plan

- [x] Registration proof (automatable): `list-keys -T root` on a real psmux
      session shows the Ctrl+K bind after a fresh `agent-worktrees create`/
      `embody`; the popup command (`uv run ... companion`) runs cleanly when
      invoked directly via `run-shell` against that session. (Caveat: this
      psmux build's `list-keys` is itself an unreliable signal — see the
      2026-10-07 Journal entry; the bind's *registration* was instead
      confirmed by it actually firing live.)
- [ ] **Operator's own to exercise** (not automatable — see Validation
      constraint above): inside a real muxed worktree session, press Ctrl+K
      and confirm the Companion popup opens; click the status-right
      `mux-companion` region and confirm the same. **Blocked** — see
      2026-10-07 Journal entry: the keybind fires and the popup frame opens,
      but `display-popup`'s content area never renders on this machine's
      pinned psmux 3.3.5, independent of the Companion app itself. Needs the
      psmux upgrade (deferred, machine-pin change, operator's call) or an
      alternate summon mechanism before this item can pass.
- [ ] Full `worktree-manager` test suite stays green.

## Proposal

_Pending — submitted for review per the repo's `pr-self-merge` profile before
implementation begins._

## Journal

### 2026-10-05 — Kickoff
Surfaced while driving `mux-companion-manual-cutover-diagnostics`' own
still-open Validation Plan items at the operator's request: found that
`Ctrl+K` does nothing today — zero `bind-key`/`display-popup` wiring exists
anywhere in the codebase, despite the vision's "hotkey-summoned" framing.
Confirmed empirically (prototype psmux session, `source-file`-based
root-table binds): a plain `C-k` bind registers correctly and reliably once
PSMUX_SESSION/TMUX are cleared first (the standard nesting-gotcha this
codebase's own Journal entries flag repeatedly); `MouseDown1Status` and
related mouse-event key names do NOT appear in `list-keys -T root` output
after sourcing, even though the shipped `WheelUpPane`/`WheelDownPane` binds
(known-working, production) ALSO did not show up under the same clean-session
test — meaning `list-keys`-based confirmation is inconclusive for mouse-event
bindings specifically (either a display limitation or a genuine support gap
in this psmux build), unlike plain keys which registered and listed
reliably. Proceeding with Step 1 (Ctrl+K) as the high-confidence slice; Step
2 (clickable region) implemented per best-practice tmux/psmux syntax but
explicitly flagged as needing the operator's own live-click confirmation
given this inconclusive automated signal.

### 2026-10-07 — Operator report: Ctrl+K fires, popup content blank

Operator's session (`wt-tmichon-cloud1-win-20260916-233618-927b`) predated
the version that first stamped the Ctrl+K bind, so the automatic
`Invoke-AwMuxCompanionBind` call never applied to it (expected "old session,
new code" gap, not a code regression — see the companion handoff for that
half of the investigation). A live manual `source-file` re-apply against
that one session's psmux server fixed the missing bind: Ctrl+K now reliably
opens a popup frame.

**But the popup's content area stays blank** — the border/title renders,
the inner body never does. Isolated to a **psmux-level bug, not this
effort's code or the Companion app**, via three narrowing tests, each
confirmed live by the operator:

1. The Companion itself works correctly: run directly in a normal pane
   (`uv run --quiet --project <ManagerRoot> -m worktree_manager companion`,
   cwd'd into the actual worktree) renders the status/lineage/buttons view
   exactly as designed. (An earlier attempt from `$HOME` instead showed a
   correctly-rendered *error* box — also proof the app and its error path
   both render fine outside a popup.)
2. The same command via `Ctrl+K`'s `display-popup`, from the identical cwd,
   stays blank.
3. A throwaway `Ctrl+J` bind to a **trivial** popup command with zero
   Python/Textual involved — `cmd /c echo HELLO-SIMPLE && timeout /t 6` —
   was *also* blank: border shows, content area never paints a single
   character.

Conclusion: `display-popup`'s content area does not render output at all on
this machine's psmux build (pinned `3.3.5`), for any command, Companion or
otherwise. This is a `psmux`/`tmux`-compatibility-layer limitation external
to this repo, not a defect in `Get-AwMuxCompanionKeybindFragment`,
`Invoke-AwMuxCompanionBind`, or `mux_companion.py`. A `winget upgrade` to the
available `3.3.8` was attempted but could not complete live (the pinned
install directory's `psmux.exe` is locked by the running server(s) backing
every attached session on this machine, including the one driving this
investigation) — completing it would require stopping all psmux servers
first, which the operator deferred rather than disconnect every live session
mid-task. Re-pinned back to `3.3.5` (`winget pin add --id
marlocarlo.psmux --version 3.3.5`) to leave the machine exactly as found.

**Not yet done:** retry the upgrade (and retest `Ctrl+J`'s trivial-echo
popup, then `Ctrl+K`) during a deliberate maintenance window when no live
session needs to survive the psmux server restart; if `3.3.8` doesn't fix
it, check `psmux/psmux`'s own issue tracker for this specific blank-popup-
content symptom (the two closed issues found while researching this,
#507 "cursor invisible" and #537 "cannot attach to another session," are
adjacent but not an exact match) and consider filing a new one, or fall back
to a `new-window`-based summon mechanism instead of `display-popup` if the
upstream bug proves durable.
