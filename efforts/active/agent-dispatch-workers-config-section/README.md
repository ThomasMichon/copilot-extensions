# agent-dispatch Workers configuration section

- **Slug:** `agent-dispatch-workers-config-section`
- **Repo:** `ThomasMichon/copilot-extensions` (`agent-dispatch` + `agent-worktrees`
  picker-support plugins)
- **Branch(es):** per-slice worktrees
- **Created:** 2026-10-06
- **Status:** Draft
- **Umbrella issue:** none yet
- **Related efforts:**
  [`agent-dispatch-recipe-library`](../agent-dispatch-recipe-library/README.md)
  (shipped the `extends:`/recipe-template model this section surfaces),
  [`agent-dispatch-tasks-pane-ux-overhaul`](../agent-dispatch-tasks-pane-ux-overhaul/README.md)
  Phase 9 (the richer Configuration → Registrars viewer/editor; this effort's
  MVP is a narrower, pre-existing-primitive-only slice that does not block on
  or duplicate that phase),
  [`agent-dispatch-recipe-generalization`](dotfiles private peer, not
  mirrored here) — the private operator effort whose 2026-10-06 "worker-pool
  activation UX round" produced this effort's scope.

## Guiding Intent

Give an operator a discoverable, one-click way to see and toggle their
declarative task-worker pools (registrar declarations, most commonly a
`general-task-worker`/`goal-driven`/`repository-issue-loop` `extends:`
instantiation) from the Worktree Manager Picker's ⚙ **Configuration** menu,
without requiring them to know any `agent-dispatch` CLI invocation. This is
deliberately an MVP scoped to today's `ConfigSection` contract (one-shot,
non-interactive subprocess, ≤200-char status line) — report a pool's size and
active/overridden state, and flip `agent-dispatch supervise override
enable|disable` for it. A richer "browse global then repo templates and
activate one" experience is out of scope here; it depends on
`agent-dispatch-tasks-pane-ux-overhaul`'s Phase 9 (inline Registrars
viewer/editor, not started), which is new `engine.py` surface, not a
manifest-only addition.

## Context

### Why this is additive, not new engine work

`agent-worktrees`' `pivot_actions.py`/`pivot_manifest.py` already define and
parse a `config_sections` array on any plugin's pivot manifest
(`ConfigSection(key, label, run, source, confirm, description)` — see
`parse_config_sections`). No installed plugin currently populates it (a
repo-wide scan on 2026-10-06 found zero `pivots/*.json` files with a
`config_sections` key) — this is a real, working, currently-unused
mechanism, not something that needs new parsing/rendering support.

`agent-dispatch supervise override {enable,disable,list}` already exists and
already does exactly the mutation a Workers config entry needs to perform
(confirmed via `--help`, 2026-10-06) — it edits the invoking machine's own
`~/.agent-dispatch/overrides.json`. `agent-dispatch registrar discover`
already reports current pool size/state well enough to render a status line.
Nothing here requires a new mutation primitive for the **local-machine**
case; this effort wires the existing primitives into a `config_sections`
entry plus a thin "list candidate pools" helper the `run` command can shell
out to.

### Open design question, resolved: cross-machine reach

The 2026-10-06 operator round asked for the Workers entry to "edit the
user-local config across machines using SSH," but `agent-dispatch supervise
override` takes no `--machine`/SSH argument today (confirmed via `--help`).
Two options were on the table:

- **(a)** a thin wrapper fanning the same override command out over
  `agent-worktrees`' own SSH mesh/transport, living in `agent-worktrees`.
- **(b)** a native `--machine` option on `agent-dispatch supervise override`
  itself, reusing `agent-dispatch`'s **own** existing SSH mesh transport.

**Decision: (b).** `agent-dispatch` already has its own working SSH-mesh
remote-operation transport, and it is **not** read-only: `agent-dispatch
list --machine <name>` is read-only peer-browse, but `force_stop.py` already
performs a *mutating* remote operation (stopping a task on another machine)
over the same mesh (`remote_dispatch.py`/`ssh_tunnel.py`). Adding a native
`--machine` to `supervise override enable|disable` reuses that
already-proven mutating-remote-op path rather than introducing a second,
parallel SSH transport in `agent-worktrees` for the same purpose. This also
keeps the override mutation and its cross-machine variant in the same CLI
surface a future non-Picker caller (a script, a different UI) can use
directly. Phase 2 below is scoped to this native-flag approach; Phase 3 (the
config-section wiring) depends on it only for the cross-machine case — the
local-machine toggle ships independently and first.

## Request

_Carried forward verbatim from `agent-dispatch-recipe-generalization`'s
2026-10-06 Journal (the private dotfiles effort that originated this ask)._

> We should also look into having agent-dispatch inject the "configuration
> provider" hook for agent-worktrees, so that users see a "Workers" entry
> under "Configuration" and then can see the available upstream templates
> (global then odsp-web-harness) and "activate" them, which will stamp their
> local knowledge repo with a simple instantiation of the template.
>
> Follow-up: agent-dispatch should ship with a default pool of "general task
> worker", and then the harness should extend it and stamp it with the repo,
> then the knowledge repo can just enable/disable. The Configuration UX
> doesn't make repo edits; it edits the user-local config across machines
> using SSH. So we'll keep that flow, as user-level overrides and
> configuration has the final say, anyway.

This effort scopes that ask down to its MVP-feasible slice; the
browse-and-activate template picker is explicitly deferred (see Guiding
Intent).

## Plan

### Phase 1 — Confirm the exact status/toggle contract
- [ ] Confirm `agent-dispatch registrar discover --json` (or an equivalent
      narrower query) gives enough per-pool data — name, `max_active_processes`
      or lane count, current override state — to render a `ConfigSection`
      status line within the 200-char budget without a new query surface.
- [ ] Confirm `agent-dispatch supervise override list --json` reports enough
      to distinguish "active, not overridden" / "active, overridden off" /
      "no declaration found" for a given pool name.
- [ ] Write the thin CLI glue (a small script or a new `agent-dispatch`
      subcommand, whichever this phase finds is the better fit) that
      `config_sections[].run` invokes: given a pool key, print a ≤200-char
      status line and accept a toggle argument.

### Phase 2 — Native `--machine` on `supervise override`
- [ ] Add `--machine <name>` to `agent-dispatch supervise override
      enable|disable`, reusing the existing mutating SSH-mesh transport
      `force_stop.py` already proves out (`remote_dispatch.py`/
      `ssh_tunnel.py`) rather than a new transport.
- [ ] Unit/contract tests: local (no `--machine`) behavior is byte-for-byte
      unchanged; remote behavior round-trips against a test double of the
      mesh transport the way `force_stop`'s own tests do.

### Phase 3 — `config_sections` manifest entry
- [ ] Add a `config_sections` entry to `agent-dispatch`'s own pivot manifest:
      key `workers`, label "Workers", `run` invoking Phase 1's glue script,
      `confirm: true` for the disable path (a destructive-feeling toggle),
      `description` naming the MVP scope plainly (status + enable/disable
      only, no template browsing yet).
- [ ] Render path: confirm the Picker's existing ⚙ Configuration menu
      surfaces a `config_sections` entry with zero further picker-side code
      (per `pivot_manifest.py`'s parsing already being general) — if it does
      not, that gap belongs to this effort too, scoped narrowly.
- [ ] Wire Phase 2's `--machine` support into the config section once a
      multi-machine affordance is in scope (may ship as a Phase 3 follow-on
      rather than blocking the single-machine MVP).

## Validation Plan

- [ ] A real pool (e.g. this machine's `odsp-web-harness` general-task-worker
      drop-in, once it exists per `agent-worktrees` plugin
      `agent-worker-pools`) shows correct status text in the Picker's
      Configuration menu.
- [ ] Toggling disable/enable from the Picker round-trips to
      `agent-dispatch supervise override list` reflecting the change.
- [ ] A `--machine`-targeted toggle round-trips against a second real or
      test machine over the SSH mesh.
- [ ] Existing `supervise override` unit tests remain green; new tests cover
      the `--machine` path explicitly.

## Proposal

_Pending — Phase 1's exact query/glue shape firms this up._

## Journal

### 2026-10-06 — Kickoff
- Effort opened from `agent-dispatch-recipe-generalization`'s (dotfiles,
  private) Phase 2 items 4-5, carrying forward the 2026-10-06 worker-pool
  activation UX round's Workers config-section ask and its open SSH/
  `--machine` design question.
- Confirmed via source inspection: `config_sections` parsing already exists
  in `agent-worktrees` (`pivot_actions.py`/`pivot_manifest.py`) with zero
  current adopters; `agent-dispatch supervise override {enable,disable,list}`
  already exists; `agent-dispatch list --machine` is read-only SSH-mesh
  peer-browse, while `force_stop.py` already proves the same mesh transport
  supports a *mutating* remote operation. Resolved the open design question
  in favor of a native `--machine` flag on `supervise override` reusing that
  existing transport (Context above), rather than a new agent-worktrees-side
  wrapper.
