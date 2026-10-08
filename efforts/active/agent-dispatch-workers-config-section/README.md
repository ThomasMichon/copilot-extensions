# agent-dispatch Workers configuration section

- **Slug:** `agent-dispatch-workers-config-section`
- **Repo:** `ThomasMichon/copilot-extensions` (`agent-dispatch` + `agent-worktrees`
  picker-support plugins)
- **Branch(es):** per-slice worktrees
- **Created:** 2026-10-06
- **Status:** Draft
- **Vision:** [`visions/picker`](../../../visions/picker/README.md)'s
  plugin-pivot-extensibility behavior (a plugin's own pivot manifest
  contributes Picker surface without Picker-side code) and
  [`visions/plugins/agent-dispatch`](../../../visions/plugins/agent-dispatch/README.md)'s
  overrides-take-precedence behavior (a user-local override always wins over
  a declaration, which is exactly why this section can be a thin override
  toggle rather than a repo edit).
- **Umbrella issue:** See effort
- **Sub-issues:** none yet
- **Related efforts:**
  [`agent-dispatch-recipe-library`](../../2026/10/08%20agent-dispatch-recipe-library/README.md)
  (shipped the `extends:`/recipe-template model this section surfaces),
  [`agent-dispatch-tasks-pane-ux-overhaul`](../agent-dispatch-tasks-pane-ux-overhaul/README.md)
  Phase 9 (the richer Configuration → Registrars viewer/editor; this effort's
  MVP is a narrower, pre-existing-primitive-only slice that does not block on
  or duplicate that phase). A private operator peer effort in a downstream
  consumer's own knowledge repo originated this ask (not linked here — see
  the *Cross-repo placement and sequencing* convention in `efforts/README.md`;
  this public effort is the canonical, organization-neutral one).

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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `agent-dispatch` | Owns the `supervise override` CLI mutation, the registrar/override query surface, and (Phase 2) the native `--machine` remote-mutation path | this repo's `plugins/agent-dispatch` |
| `agent-worktrees` | Owns the Picker's `config_sections` parsing/rendering and (Phase 3) any conditional-confirmation UI gap this effort finds | this repo's `worktree-manager` + `plugins/agent-worktrees` |

## Coordination

- **Topology:** independent per-phase PRs (each phase lands and is validated
  on its own; no shared long-lived feature branch).
- **Host (owns PRs):** whichever agent/session picks up a phase; this is a
  single-repo, solo-pace effort with no standing delegate split today.
- **Delegates:** none assigned yet.
- **Handoff:** a phase's own Journal entry plus its Plan checkboxes are the
  full resume contract for the next session/participant.

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

**Decision: (b).** `agent-dispatch` already has its own working SSH
remote-operation transport for a genuine *mutation* (not just read-only
peer-browse): `remote_dispatch.py`'s `dispatch_to_remote` (used by `agent-dispatch
create --machine`) opens a direct `ssh <alias> <remote argv>` connection to
create+embody a task on another machine — `agent-dispatch list --machine`'s
`browse_remote` is the read-only counterpart over the same module. (An
earlier draft of this section cited `force_stop.py` as the mutating
precedent; corrected 2026-10-06 — `force_stop`'s remote path actually goes
through `agent-bridge`'s own `LocalBridgeRemoteClient`/`agent-bridge end` SSH
fallback in `embody.py`, a different plugin's transport, not evidence for a
change inside `agent-dispatch` itself.) Adding a native `--machine` to
`supervise override enable|disable` reuses `dispatch_to_remote`'s
already-proven mutating-remote-op path rather than introducing a second,
parallel SSH transport in `agent-worktrees` for the same purpose. This also
keeps the override mutation and its cross-machine variant in the same CLI
surface a future non-Picker caller (a script, a different UI) can use
directly. Phase 2 below is scoped to this native-flag approach; Phase 3 (the
config-section wiring) depends on it only for the cross-machine case — the
local-machine toggle ships independently and first.

## Request

_Carried forward, generalized to remove a private downstream consumer's
repository name, from `agent-dispatch-recipe-generalization`'s 2026-10-06
Journal (a private peer effort in a downstream consumer's own knowledge
repo that originated this ask; see the *Cross-repo placement and
sequencing* convention in `efforts/README.md` for why that peer isn't
linked from here)._

> We should also look into having agent-dispatch inject the "configuration
> provider" hook for agent-worktrees, so that users see a "Workers" entry
> under "Configuration" and then can see the available upstream templates
> (global then a consuming repo's own) and "activate" them, which will stamp
> their local knowledge repo with a simple instantiation of the template.
>> Follow-up: agent-dispatch should ship with a default pool of "general task
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
      enable|disable`, reusing `remote_dispatch.py`'s existing
      `dispatch_to_remote`/`build_remote_create_argv` mutating SSH path
      (the same one `agent-dispatch create --machine` already uses) rather
      than a new transport.
- [ ] Unit/contract tests: local (no `--machine`) behavior is byte-for-byte
      unchanged; remote behavior round-trips against a test double of the
      SSH transport the way `remote_dispatch.py`'s own existing tests do.

### Phase 3 — `config_sections` manifest entry
- [ ] Add a `config_sections` entry to `agent-dispatch`'s own pivot manifest:
      key `workers`, label "Workers", `run` invoking Phase 1's glue script,
      `description` naming the MVP scope plainly (status + enable/disable
      only, no template browsing yet).
- [ ] **Known picker-side gap, not yet buildable from existing primitives:**
      `engine_pivot_actions.py`'s `_run_config_section` invokes a selected
      `ConfigSection` immediately on Enter — neither it nor
      `tasks.run_config_section` reads a `confirm` field today, and a single
      static flag on the manifest entry can't distinguish a destructive
      "disable" invocation from a harmless "status" one in the first place
      (both go through the same `run` command). This phase must design and
      add real conditional-confirmation UI (likely: the `run` glue script's
      own argv carries an explicit action, and the Picker gains a
      confirm-before-run step keyed off that, or off a new per-invocation
      manifest field) before shipping a disable toggle from the Picker —
      do not treat this as a manifest-only addition.
- [ ] Render path: confirm the Picker's existing ⚙ Configuration menu
      surfaces a `config_sections` entry with no further picker-side code
      for the **status-only** path (per `pivot_manifest.py`'s parsing
      already being general) — the toggle/disable path is exactly the
      confirmation gap above, scoped narrowly to that one UI addition.
- [ ] Wire Phase 2's `--machine` support into the config section once a
      multi-machine affordance is in scope (may ship as a Phase 3 follow-on
      rather than blocking the single-machine MVP).

## Validation Plan

- [ ] An organization-neutral temporary or checked-in fixture pool (a
      throwaway `kind: supervised-lane` declaration registered against a
      scratch/test repo lane, not any particular downstream consumer's real
      harness or machine-local state) shows correct status text in the
      Picker's Configuration menu — this repeatable fixture is the
      acceptance test, not a dependency on a specific organization's
      pre-existing deployment.
- [ ] Toggling disable/enable from the Picker round-trips to
      `agent-dispatch supervise override list` reflecting the change.
- [ ] A `--machine`-targeted toggle round-trips against a second real or
      test machine over `remote_dispatch.py`'s SSH transport.
- [ ] Existing `supervise override` unit tests remain green; new tests cover
      the `--machine` path explicitly.

## Proposal

_Pending — Phase 1's exact query/glue shape firms this up._

## Journal

### 2026-10-06 — Kickoff
- Effort opened from a private operator peer effort's Phase 2 items 4-5,
  carrying forward a 2026-10-06 worker-pool activation UX round's Workers
  config-section ask and its open SSH/`--machine` design question.
- Confirmed via source inspection: `config_sections` parsing already exists
  in `agent-worktrees` (`pivot_actions.py`/`pivot_manifest.py`) with zero
  current adopters; `agent-dispatch supervise override {enable,disable,list}`
  already exists. Resolved the open design question in favor of a native
  `--machine` flag on `supervise override`, reusing `remote_dispatch.py`'s
  existing `dispatch_to_remote` mutating SSH path (the same one
  `agent-dispatch create --machine` already uses), rather than a new
  agent-worktrees-side wrapper.

### 2026-10-06 — Review round: corrected transport precedent + process gaps
- Copilot code review (PR #5536) found this effort's original design
  rationale cited `force_stop.py` as proof `agent-dispatch` has its own
  mutating SSH transport; traced the actual code and found `force_stop`'s
  remote path goes through `agent-bridge`'s `LocalBridgeRemoteClient`/
  `agent-bridge end` SSH fallback (`embody.py`'s `stop_fleet_body`) — a
  different plugin's transport, not `agent-dispatch`'s own. Re-verified the
  decision against `remote_dispatch.py` directly: `dispatch_to_remote`
  (backing `agent-dispatch create --machine`) is the real, already-shipped
  mutating precedent inside `agent-dispatch` itself. The **decision itself
  (native `--machine` flag, living in `agent-dispatch`) is unchanged**; only
  its cited evidence was wrong, now corrected throughout this file.
- Also fixed per review: removed a private downstream consumer's repository
  name from the quoted Request (generalized to "a consuming repo's own");
  added this effort to `efforts/README.md`'s active-effort index; added the
  required `Vision` field, `Participants`, and `Coordination` sections per
  the canonical template; corrected Phase 3's `confirm: true` claim — traced
  `engine_pivot_actions.py`/`_run_config_section` directly and confirmed no
  code path reads a `confirm` field today, so Phase 3 now carries an
  explicit "known picker-side gap" item for conditional-confirmation UI
  instead of asserting zero picker-side code; reworded the Validation Plan's
  first item to an organization-neutral fixture rather than a named
  downstream consumer's real harness/machine state.
