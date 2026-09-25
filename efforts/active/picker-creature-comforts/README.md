# Picker Creature Comforts

- **Slug:** `picker-creature-comforts`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase model
- **Created:** 2026-09-24
- **Status:** Draft
- **Vision:** `picker` §Features/detachable-launch-into-a-new-window,
  §Features/worktree-search-and-filter · `session-hosting`
  §Features/host-native-title-projection
- **Umbrella issue:** #3586
- **Sub-issues:** #3587 · #3588

## Guiding Intent

Two small, high-frequency UX gaps make fleet recovery and navigation more
tedious than they need to be: launching always consumes the Picker's own
window, and there is no way to narrow a large worktree list by typing. A third,
smaller comfort projects a worktree's identity into its terminal tab so
scanning open tabs is enough to orient.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|--------------|
| Driving agent | Authors and drives all three phases | The effort's active worktree |

## Coordination

- **Topology:** independent per-phase PRs.
- **Host (owns PRs):** Driving agent.
- **Delegates:** none currently.
- **Handoff:** n/a (single participant today).

## Context

Sibling, already-in-flight work this effort deliberately does **not**
duplicate:
- **#3307** — Worktrees pivot UX overhaul: recency ordering, CLAIMS column,
  session/turn visibility, Mux Companion buildout. (Recency-of-use sort and
  the claims-column overhaul the operator separately flagged as already being
  worked.)
- **#3390** — Relocate terminal-profile handling (`profiles.py` /
  `terminal_fragment.py`) out of agent-worktrees into worktree-manager.
  (Adjacent infrastructure for Phase 1's new-terminal launch below.)

## Request

> "We probably need to add a couple creature comforts to Worktree Manager:
> Ability to open/resume/create a Worktree into a new terminal. Right now, we
> shut down the Picker, and start the worktree session-launch flow in the same
> tab. Nothing says we can't just spawn a new process, detached, and leave
> Worktree Manager running in the original tab. That way, should Mux die, I
> can re-open Worktree Manager, arrow down each previous session I had open,
> and open it into a new tab."
>
> "Worktree Search. At least a filter by substring match across the 4-digit
> id, the codename, the title, the activity slot, and the claim set. Should
> provide a Search box in the Worktree Manager somewhere. Perhaps Ctrl+F
> brings up a footer-overlay at the bottom, with a big search box. Typing
> filters, Escape clears, and after putting a filter in, I can always
> up-arrow or Shift-Tab out of the box, up into the list. Tab or
> arrow-all-the-way-down puts me back into Search."
>
> "Somehow, we should feed back worktree titles into the Terminal tab titles.
> I think Mux has a setting for this; we just need to figure out where to
> pipe the value. Mux lets us also read the title hint from the wrapped
> process, so there's got to be a way we can join our title as a prefix, and
> Copilot's process title as a suffix, and get them into the Windows Terminal
> titles."

## Plan

### Phase 1 — Detached launch into a new terminal window (#3586)
- [ ] Add a launch option (alongside the existing open/resume/create flow)
      that spawns the target detached into a new terminal window/tab instead
      of taking over the Picker's own window.
- [ ] Confirm interaction with #3390's terminal-profile relocation so this
      doesn't land against code about to move.
- [ ] The Picker keeps running in its original tab after a detached launch.

### Phase 2 — Ctrl+F worktree search/filter overlay (#3587)
- [ ] Footer search-box overlay summoned by Ctrl+F.
- [ ] Filters the worktree list live by substring match across: 4-digit id,
      codename, title, current-activity slot, claim set.
- [ ] Escape clears the filter and closes the overlay.
- [ ] Up-arrow / Shift-Tab from the search box moves focus into the filtered
      list; Tab or arrowing past the list's bottom returns focus to the box.

### Phase 3 — Terminal tab title projection (#3588)
- [ ] Research where Windows Terminal (and psmux/tmux, for parity) exposes a
      settable tab/pane title, and whether/how it can read the wrapped
      process's own OSC title hint.
- [ ] Compose the worktree's durable title as a prefix with the process's own
      title hint as a suffix, rather than replacing it outright.

## Validation Plan

- [ ] A detached launch leaves the Picker's own window/process alive and
      responsive; the new terminal window hosts the launched session
      correctly (verified against both a resume and a create path).
- [ ] Typing a substring in the search overlay narrows the list to matching
      rows only; clearing restores the full list; keyboard focus transitions
      (box <-> list) work as specified in both directions.
- [ ] A launched worktree's terminal tab visibly shows the worktree's title
      composed with the process's own title hint, verified on **every host
      Phase 3's Plan names** — Windows Terminal and psmux/tmux — not merely
      Windows Terminal; if research finds a host genuinely can't support
      this, narrow Phase 3's Plan item to name that host's exclusion
      explicitly rather than leaving an unvalidated gap.

## Proposal

_Pending — this effort's plan itself will be submitted for review per the
repo's `pr-self-merge` profile before Phase 1 implementation begins._

## Journal

### 2026-09-24 — Kickoff
- Effort created from a facility planning session. Cited #3307 and #3390 as
  in-flight sibling work rather than duplicating either. Filed #3586, #3587,
  #3588.

### 2026-09-25 — Validation gap: mux-host parity
- Review caught that Phase 3's Plan names both Windows Terminal and
  psmux/tmux for parity, but the Validation Plan only checked Windows
  Terminal -- letting an implementation skip the mux hosts and still pass.
  Widened the bullet to require validation on every host Phase 3 names,
  with an explicit narrowing instruction if research finds a host that
  can't support it.

