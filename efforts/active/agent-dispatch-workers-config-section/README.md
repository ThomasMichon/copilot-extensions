# agent-dispatch Workers configuration section

- **Slug:** `agent-dispatch-workers-config-section`
- **Repo:** `ThomasMichon/copilot-extensions` (`agent-dispatch` + `agent-worktrees`
  picker-support plugins)
- **Branch(es):** per-slice worktrees
- **Created:** 2026-10-06
- **Status:** Active <!-- Phase 1 landed (status + local toggle glue); Phase 2 landed (native --machine); Phase 3 landed (status-only config_sections entry); the toggle-from-Picker follow-on is tracked in ThomasMichon/copilot-extensions#6044 -->
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
- [x] Confirm `agent-dispatch registrar discover --json` (or an equivalent
      narrower query) gives enough per-pool data — name, `max_active_processes`
      or lane count, current override state — to render a `ConfigSection`
      status line within the 200-char budget without a new query surface.
      **Confirmed, with a nuance:** `registrar discover`'s own
      `_declaration_summary` already reports `name`/`owner`/`concurrency`/
      `max_active_processes`/`body`/`filters` for a `supervised-lane`
      declaration, and discovery already rejects duplicate names across
      sources — a bare pool name is enough to identify one pool
      unambiguously. It carries **no** override state, though (discovery
      and the override store are genuinely separate concerns); that half
      comes from the override store directly (see next item).
- [x] Confirm `agent-dispatch supervise override list --json` reports enough
      to distinguish "active, not overridden" / "active, overridden off" /
      "no declaration found" for a given pool name. **Confirmed, with a
      correction:** `override list` alone reports only the raw override
      store (`path`/`overridden_off` ids/`overrides` records with reasons)
      — it has no notion of "active" or "declared" at all, since it never
      reads `registrar discover`. The glue therefore reads both: a pool's
      declared state from `registrar discover`, and its override state by
      checking whether `logical:<owner>:<name>` (the override store's own
      **logical** override token — `overrides.logical_override_id`, no
      concrete registration id needed) is in `overridden_off_ids`. This
      combination gives exactly the three states the item asks for.
- [x] Write the thin CLI glue (a small script or a new `agent-dispatch`
      subcommand, whichever this phase finds is the better fit) that
      `config_sections[].run` invokes: given a pool key, print a ≤200-char
      status line and accept a toggle argument. **Landed:** a new
      `agent-dispatch workers config-section <name> [--toggle
      enable|disable] [--reason R] [--owner O] [--json]` subcommand
      (`workers_config_cli.py`). Default output is a single ≤200-char line
      (`"<name>: <N> lane(s) declared -- active"` / `"-- overridden off
      (<reason>)"` / `"<name>: no declaration found"`, truncated with an
      ellipsis if a long reason would overflow the budget); `--json` emits
      the full structured detail for debugging/future callers. `--toggle`
      applies the override via the existing `set_override`/`clear_override`
      primitives *before* reporting status, addressed by the pool's logical
      override id — this works even for a not-yet-synced declaration (the
      override is independent of a live registration), and the command's
      own exit code (0 found / 1 not found) is consistent across both the
      text and `--json` output paths. 12 new unit tests
      (`tests/test_workers_config_cli.py`): parser shape, status line for
      singular/plural lane counts, not-found and non-`supervised-lane`-kind
      declarations, the toggle round-trip (disable→status reflects it,
      enable clears it, a toggle on an unknown pool still applies via its
      logical id), JSON shape (found and not-found), `--owner`
      disambiguation when two declarations share a name across owners, and
      the status-line truncation budget.

### Phase 2 — Native `--machine` on `supervise override`
- [x] Add `--machine <name>` to `agent-dispatch supervise override
      enable|disable`, reusing `remote_dispatch.py`'s existing
      `dispatch_to_remote`/`build_remote_create_argv` mutating SSH path
      (the same one `agent-dispatch create --machine` already uses) rather
      than a new transport. **Landed, with a refinement:** rather than
      reusing `build_remote_create_argv` (tightly coupled to `create`'s own
      argument model), added a new `build_remote_override_argv(action,
      unit_id, *, reason=None)` builder and reused the already-generic
      `browse_remote`/`diagnose_remote_failure` transport
      `list`/`inbox --machine`'s own peer-queue-browse path already uses
      (`task_query_cli.py`'s `_browse_peer`) -- the SSH mechanics
      (`run_ssh_command`, `BatchMode`, alias lowercasing) were already
      shared infrastructure; only the argv shape is new. `supervise
      override`'s own `_cmd_supervise_override` checks
      `remote_dispatch.is_peer_machine(machine)` and dispatches remotely
      for `disable`/`enable` only (`list` has no `--machine`, unchanged).
- [x] Unit/contract tests: local (no `--machine`) behavior is byte-for-byte
      unchanged; remote behavior round-trips against a test double of the
      SSH transport the way `remote_dispatch.py`'s own existing tests do.
      **Landed:** 12 new tests -- 2 in `test_remote_dispatch.py`
      (`build_remote_override_argv` with/without `--reason`) and 10 in
      `test_cli.py` (parser shape including `list`'s own missing
      `--machine` flag; `--machine` naming the local machine is a no-op
      change in behavior; `--machine <peer>` dispatches `disable`/`enable`
      remotely and streams the peer's JSON through unchanged; a failed
      remote mutation surfaces the same diagnosed error peer-queue browse
      already produces; an unavailable `ssh` client is reported the same
      way). Full `test_cli.py` (259 passed, 1 skipped) and
      `test_remote_dispatch.py` re-confirmed green;
      `check-module-size.py` clean.

### Phase 3 — `config_sections` manifest entry
- [x] Add a `config_sections` entry to `agent-dispatch`'s own pivot manifest:
      key `workers`, label "Workers", `run` invoking Phase 1's glue script,
      `description` naming the MVP scope plainly (status + enable/disable
      only, no template browsing yet). **Landed, with a necessary design
      change:** a `config_sections[].run` argv is static -- it cannot carry
      a consuming repo's own chosen pool name (unknowable ahead of time by
      a plugin-shipped manifest). Rather than invent and hardcode an
      unverified naming convention (e.g. a presumed "general-task-worker"
      pool), `workers config-section` gained a **no-name summary mode**:
      invoked with no positional name, it reports every declared
      `supervised-lane` pool's status in one joined, budget-truncated line
      (`"pool-a: 2 -- active; pool-b: 1 -- overridden off (reason)"`,
      collapsing into a trailing `"+N more"` marker rather than mid-name
      truncation once the 200-char budget would overflow). The manifest
      entry's `run` is `["agent-dispatch", "workers", "config-section"]`
      (no name) -- this is what actually ships generically, for any
      consuming repo's own pool(s), without presuming a naming scheme this
      effort never actually committed to shipping.
- [x] **Known picker-side gap, not yet buildable from existing
      primitives -- investigated to ground truth, not just suspected, and
      filed as its own tracked issue:**
      [`ThomasMichon/copilot-extensions#6044`](https://github.com/ThomasMichon/copilot-extensions/issues/6044).
      Traced both dispatch paths directly: `engine_pivot_actions.py`'s
      `_run_config_section` invokes a selected `ConfigSection` immediately
      on Enter, never reading `section.confirm`; and (a broader version of
      this gap than originally suspected) `_open_task_menu`'s
      `TaskMenuScreen` dismisses straight to `_run_task_action` with **no
      confirm step at all** for an ordinary `WorktreeAction` either --
      `confirm: true` is parsed and validated for both action kinds, but
      genuinely enforced only for the unrelated "New task…"
      `create_action.confirm` path (`CreateActionScreen`). This means
      every `confirm: true` entry already shipped today (including this
      very plugin's own `pause`/`force-stop`/`abandon`/... actions) runs
      immediately with no confirmation dialog -- a pre-existing,
      cross-cutting Picker gap, not something to redesign inside this
      narrow effort. The filed issue also notes the scope wrinkle a single
      static `confirm: true` can't resolve alone: one config-section
      `run` command can't distinguish "status" from "disable" by itself,
      so any real fix needs either a per-invocation argv-carried action or
      a separate always-confirmed entry for the destructive operation.
      Shipping the toggle from the Picker stays blocked on that issue; the
      terminal `--toggle enable|disable` flag (Phase 1) remains the
      supported path until it closes.
- [x] Render path: confirm the Picker's existing ⚙ Configuration menu
      surfaces a `config_sections` entry with no further picker-side code
      for the **status-only** path. **Confirmed, not just asserted:** the
      manifest change passes `worktree-manager`'s own
      `test_real_checkout_manifests_match_contract` (schema validation
      against every real `plugins/*/pivots/*.json`) and its dedicated
      `test_pivots.py`/`test_picker_tui.py` `config_sections` coverage
      (19 + 11 tests, all green) with zero Picker-side code changes --
      `pivot_manifest.py`'s parsing was already fully general, exactly as
      this item predicted.
- [ ] Wire Phase 2's `--machine` support into the config section once a
      multi-machine affordance is in scope (may ship as a Phase 3 follow-on
      rather than blocking the single-machine MVP). Deferred -- the
      no-name summary mode is inherently single-machine (it reports this
      machine's own discovered pools); a cross-machine summary view is a
      genuinely separate UI shape, not a small addition to this one.

## Validation Plan

- [x] An organization-neutral temporary or checked-in fixture pool (a
      throwaway `kind: supervised-lane` declaration registered against a
      scratch/test repo lane, not any particular downstream consumer's real
      harness or machine-local state) shows correct status text in the
      Picker's Configuration menu — this repeatable fixture is the
      acceptance test, not a dependency on a specific organization's
      pre-existing deployment. **Landed, via unit coverage (not a live
      Picker session):** `test_workers_config_cli.py`'s no-name summary
      tests build fixture `supervised-lane` declarations and assert the
      exact rendered status line; `worktree-manager`'s own
      `test_real_checkout_manifests_match_contract` proves the manifest
      entry itself is schema-valid and discoverable. A live, driven Picker
      session was not run this session (no interactive TUI harness
      available in this environment); deferred to whoever next drives one.
- [ ] Toggling disable/enable from the Picker round-trips to
      `agent-dispatch supervise override list` reflecting the change.
      Blocked on `#6044` (the toggle is not yet wired into the Picker at
      all, by design -- see Phase 3's own items above).
- [ ] A `--machine`-targeted toggle round-trips against a second real or
      test machine over `remote_dispatch.py`'s SSH transport. **Partially
      covered:** proven against a mocked SSH transport (the same style
      `remote_dispatch.py`'s own existing tests use) -- a live second
      machine was not available this session; deferred to whoever next
      has one, or to the Phase 3 config-section's own live validation.
- [x] Existing `supervise override` unit tests remain green; new tests cover
      the `--machine` path explicitly. **Landed:** 12 new tests (2 in
      `test_remote_dispatch.py`, 10 in `test_cli.py`); full `test_cli.py`
      (259 passed, 1 skipped) and `test_remote_dispatch.py` green.

## Proposal

_Pending — Phase 1's exact query/glue shape firms this up. (Phase 3's own
no-name summary mode is proposal-level work in its own right; see that
phase's first Plan item for the rationale.)_

## Journal

### 2026-10-10 — Phase 3 landed: status-only `config_sections` Workers entry
- Resolved a prior handoff's own closing claim ("needs the separate
  Worktree Manager app repo's own Picker-side confirmation-UI gap closed")
  -- this turned out to be **incorrect**: `worktree-manager/` is an
  out-of-plugin directory inside *this same* `ThomasMichon/copilot-extensions`
  checkout (confirmed via `git ls-files worktree-manager`, its own CI job
  `worktree-manager (out-of-plugin)` in `.github/workflows/ci.yml`), not a
  separate repository. Phase 3 was fully drivable from here all along.
- Investigated the confirm-gap claim to ground truth rather than taking it
  at face value: traced `engine_pivot_actions.py`'s `_run_config_section`
  and `_open_task_menu`/`TaskMenuScreen`/`_run_task_action` directly.
  Confirmed the gap is real and **broader** than originally scoped -- it
  affects every `WorktreeAction`, not just `ConfigSection` -- and filed it
  as its own tracked issue,
  [`ThomasMichon/copilot-extensions#6044`](https://github.com/ThomasMichon/copilot-extensions/issues/6044),
  rather than attempting a cross-cutting Picker confirm-UI redesign inside
  this narrow effort.
- Discovered a second, independent gap while designing the actual manifest
  entry: a `config_sections[].run` argv is static per-plugin, so it cannot
  target a *specific* consuming repo's own pool name (unknowable ahead of
  time). Rather than hardcode an unverified "general-task-worker" naming
  convention the Request only ever floated as a follow-up idea, extended
  `workers config-section` with a **no-name summary mode**: omit the pool
  name to get one joined, budget-truncated status line across every
  declared pool (`all_supervised_lanes()`/`all_pools_status_line()`), with
  a `"+N more"` collapse once the 200-char contract would overflow mid-pool
  rather than mid-name. `--toggle` without a name is rejected with a clear
  error (exit 2) -- toggling always requires naming the one pool to act on.
- Added the manifest entry itself: `plugins/agent-dispatch/pivots/agent-dispatch.json`
  gained `config_sections: [{key: "workers", label: "Workers", run:
  ["agent-dispatch", "workers", "config-section"], description: ...}]`
  (status-only, `confirm` omitted since this path performs no mutation).
  Verified against `worktree-manager`'s own `test_real_checkout_manifests_match_contract`
  (schema-validates every real `plugins/*/pivots/*.json`) and its
  `config_sections`-specific `test_pivots.py`/`test_picker_tui.py`
  coverage (19 + 11 tests) -- all green, zero Picker-side code changed, as
  Phase 3's own original "render path" Plan item predicted.
- 9 new tests in `test_workers_config_cli.py` (parser's now-optional name,
  the summary line for multiple/zero/non-supervised-lane pools, override
  reflection in the summary, the toggle-without-name rejection, JSON shape,
  and the overflow-collapse budget behavior). Full `test_cli.py` (239
  passed, 1 skipped combined with `test_workers_config_cli.py`),
  `check-module-size.py` all green.
- Next: the toggle-from-Picker UI wiring is blocked on `#6044` closing;
  the terminal `--toggle enable|disable` flag (Phase 1) remains the
  supported mutation path meanwhile. A live, driven Picker session to
  visually confirm the Configuration menu entry was not run this session
  (no interactive TUI harness available here) -- deferred to whoever next
  drives one, per the Validation Plan's own note.

### 2026-10-08 — Phase 2 landed: native `--machine` on `supervise override`
- Added `--machine <name>` to `supervise override disable|enable` (not
  `list`, unchanged per scope). Reused the already-generic
  `browse_remote`/`diagnose_remote_failure` SSH transport
  `list`/`inbox --machine`'s own peer-queue-browse path
  (`task_query_cli.py`'s `_browse_peer`) already provides, adding only a
  new `build_remote_override_argv(action, unit_id, *, reason=None)` argv
  builder -- `dispatch_to_remote`/`build_remote_create_argv` turned out to
  be too tightly coupled to `create`'s own argument model to reuse
  directly, so this phase reused the transport layer underneath both
  (`run_ssh_command`, `BatchMode`, alias lowercasing) instead of literally
  calling the `create`-specific builder the Plan item named.
- `_cmd_supervise_override` checks `remote_dispatch.is_peer_machine(machine)`
  up front: unset, or naming this machine, falls through to the existing
  local code path completely unchanged (byte-for-byte, confirmed by the
  pre-existing tests still passing verbatim); naming a different machine
  builds the remote argv, runs it over SSH, and streams the peer's JSON
  straight through, mirroring `_browse_peer`'s own shape for errors
  (unavailable `ssh` client, a failed remote exit) exactly.
- 12 new tests (2 in `test_remote_dispatch.py`, 10 in `test_cli.py`):
  parser shape (including confirming `list` carries no `--machine` flag at
  all), the local-machine-named-explicitly no-op case, remote dispatch for
  both `disable` and `enable` (argv shape + JSON passthrough), remote
  failure diagnosis, and an unavailable-SSH report. Full `test_cli.py`
  (259 passed, 1 skipped), `test_remote_dispatch.py`, and
  `check-module-size.py` all green.
- Docs: `plugins/agent-dispatch/README.md`'s emitter-override paragraph
  gained a `--machine` explainer.
- A live second-machine round-trip (this effort's own Validation Plan)
  was not available this session -- deferred, proven against a mocked SSH
  transport instead (the same style `remote_dispatch.py`'s own tests use).
- Next: Phase 3 (the `config_sections` pivot-manifest entry itself, which
  needs the separate Worktree Manager app repo's own Picker-side
  conditional-confirmation gap closed first -- not buildable from existing
  primitives alone, per that phase's own Plan item).

### 2026-10-08 — Phase 1 landed: status/toggle CLI glue
- Investigated the exact contract (see Phase 1's own checked-off items for
  the detailed findings): `registrar discover` supplies declared per-pool
  data (name/owner/concurrency), `supervise override`'s store supplies
  override state keyed by a **logical** override id
  (`logical:<owner>:<name>`) independent of any live registration; neither
  alone is sufficient, so the new glue reads both.
- New `agent-dispatch workers config-section <name>` subcommand
  (`workers_config_cli.py`, wired into `__main__.py` alongside the other
  command-family registrations): default output is a single ≤200-char
  status line; `--toggle enable|disable` applies the local override first;
  `--json` emits full structured detail. 12 new unit tests, all green;
  full `test_cli.py` (210 passed) and `check-module-size.py` re-confirmed
  clean.
- Next: Phase 2 (native `--machine` on `supervise override`), then Phase 3
  (the actual `config_sections` pivot-manifest entry + the Picker-side
  conditional-confirmation gap, which lives in the separate Worktree
  Manager app repo, not this one — Phase 3's own Plan item already flags
  this as "not yet buildable from existing primitives" until that UI gap
  closes).

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
