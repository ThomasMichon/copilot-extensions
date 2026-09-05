# Agent Worktrees -- Architecture

## Two-Layer Design

```
Plugin layer (Copilot CLI)              Runtime layer (Python CLI)
  plugin.json                             ~/.agent-worktrees/
  hooks.json  -- preToolUse/sessionStart/     versions/<v>/    Python venv slots (immutable)
                 sessionEnd hooks             current-version  marker -> active slot
  skills/     -- skills loaded                payload-dir       installed payload pointer
                 into every session           bin/             launchers, hook shims, guards
  extensions/ -- live-pulse sidecar
                                              projects.yaml    registry of adopted repos
                                              repos.yaml       repos registry + source roots

                                            ~/.{project}/      per-project config + state
                                              config.yaml      repos, machine, launch commands
                                              worktrees/       per-worktree tracking YAML

                                            ~/.local/bin/
                                              {project}        binstub (Windows: .cmd)
                                              agent-worktrees  CLI tool
```

The **plugin** installs via `copilot plugin install` and provides skills, hooks,
and the live-pulse extension to Copilot CLI sessions. The **runtime** installs
via init/install scripts (`init.ps1`/`init.sh` → `install.{ps1,sh}`), or via the
global binstub's first-use `provision` fallback, and provides the
`agent-worktrees` CLI, session launchers, and per-project binstubs.

## Installed Layout

After full installation and project registration:

```
~/.agent-worktrees/                 # Shared runtime (one per machine)
  versions/<v>/                     #   Immutable per-version venv slots
  current-version                   #   Plain-text marker -> the active slot
  bin/                              #   Shell wrappers
    launch-session.{ps1,cmd,sh}     #     Session launcher
    bootstrap-check.{ps1,sh}        #     Session-start health check
    provision-check.{ps1,sh}        #     Repo-enabled plugin runtime provision
    *guard.py                       #     preToolUse guard scripts
  projects.yaml                     #   Registry of adopted projects
  repos.yaml                        #   Repos catalog + source roots
  pivots/                           #   Cross-plugin picker pivot manifests
  deploy-manifest.json              #   Provenance (commit, timestamp)

~/.{project}/                       # Per-project config + state
  config.yaml                       #   Machine, repos, launch commands
  worktrees/                        #   Per-worktree tracking
    {worktree-id}.yaml              #     status: active|pushed|complete|finalized|orphaned

~/.local/bin/                       # Binstubs on PATH
  agent-worktrees{.cmd}             #   CLI tool
  {project}{.cmd}                   #   Project launcher (one per registered repo)
```

### Registry paths -- home-relative resolution (invariant)

Anchor paths in `repos.yaml` are stored **per-platform** (`windows` / `wsl` /
`linux`) and **may be home-relative** -- the WSL test-chamber anchor, for
instance, is registered as `~/src/test-chamber`. Because `pathlib.Path` does
**not** treat a leading `~` as special, every consumer **must** read a registry
path through **`RepoEntry.local_path(plat)`**, which calls `os.path.expanduser`
(a no-op on already-absolute entries) -- never `entry.paths[plat]` raw.

This is load-bearing for **CWD->project discovery**: `_anchor_for_project`,
`config._resolve_anchor_from_registry`, and `_reverse_lookup_project`'s repos
fallback all compare a candidate anchor against the git top-level of CWD. A raw
`~/…` value fails `Path(...).is_dir()` and, once normalized, joins the literal
`~` onto CWD -- so the repo silently fails to resolve from its own checkout and
every command demands `--project`. Regression once shipped this way (#4190,
fixed in 1.5.3-dev352); guarded by `test_local_path_expands_home_relative`
(repos) and the home-relative reverse-lookup / anchor tests (context
resolution). Corollary: general `agent-*` commands resolve context **only** from
CWD / `--project`, never from environment variables -- only session
hooks/extensions consult the session-binding env (see
`project-scoped-invocation` pattern).

## The Worktree Record -- Single-Writer Contract (invariant)

The per-worktree tracking YAML (`~/.{project}/worktrees/{id}.yaml`) is the
**ground-layer state** of the agent fabric (see the `agent-fabric` vision in
this repo's `visions/`). Two invariants keep the layers composable -- *enhancing,
never overriding* one another -- as new orthogonal fields accrue on the record
(the `interface`/`origin` marks, the `follow_up`/`summary` disposition, the
optional `active_effort` focus, ...):

1. **Single writer, load-then-save only.** Only **agent-worktrees** writes the
   record, and only via `tracking.load_record()` -> mutate -> `tracking.save_record()`
   (or `create_new_record()` for a *brand-new* worktree). `save_record()`
   serializes **every** field the dataclass carries -- including ones the calling
   code never set or doesn't know about -- so a writer that only cares about, say,
   `resume_count` (`mark_resumed`) or `status` (`update_status`) **preserves the
   disposition and marks untouched**. A writer must **never** rebuild a fresh
   `WorktreeRecord` over an existing file, nor raw-rewrite the YAML: either would
   silently drop fields it doesn't model.

2. **Higher layers read, derive, and coordinate -- they do not write it.**
   agent-bridge (coordination) and agent-dispatch (delegation) reach the record
   **read-only**, through `agent-worktrees list --json` / `get` / `resolve --json`;
   they never open the YAML for writing. agent-dispatch keeps its *own* state
   (`worktree_focus`, `latest_progress`) in its *own* store
   (`~/.agent-dispatch/tasks.db`) and **derives** cross-layer answers at read time
   rather than storing a second copy on the record. This is the vision's
   *derive-don't-duplicate / single-owning-layer* rule made concrete.

**Enforcement.** Any new writer -- in this plugin or a converging higher layer
(e.g. a future agent-dispatch `status` write-through) -- must go through the
`load_record -> save_record` path (or, better, the `agent-worktrees status`
verb), so the round-trip preserves every overlay. A forward-compat guard test
(`tests/test_tracking.py::TestForwardCompatContract`) asserts that a load->save
by a writer that touches only one field leaves all other overlays intact; if you
add a field, extend that test.

### Profile assignment state

Balanced profile assignment follows the same single-owner rule with two
coordinated, bounded views:

- `~/.<project>/profile-assignments.json` is the atomic per-project allocator
  ledger. It owns the installation seed, current shuffled bag,
  generation/position, token-keyed pending assignments, one-shot token
  retirement, eventual `bound|abandoned` disposition, and terminal-history
  compaction. Live pending assignments are never evicted, so the ledger may
  temporarily exceed its history limit until bind or expiry makes entries
  terminal.
- `WorktreeRecord.profile_assignments[]` is the record-local status view. It
  carries the neutral assignment identity needed by `list --json` and
  `list-sessions --json`; a monotonic `profile_assignment_revision` prevents an
  unrelated stale record writer from rolling it back. Reflection is also
  disposition-monotonic for one assignment identity: once the record mirrors
  `bound` or `abandoned`, a delayed pending retry cannot replace it. Ordinary
  worktree rows expose only the current bound head-session assignment. The
  bounded history and latest terminal/pending entry are available on the explicit
  `list --json --profile-assignment-history` detail surface, keeping Picker
  polling constant-size. The record never stores the launch token.

Allocation is locked across processes and persisted before launch. The selected
profile is then passed to the ordinary launch planner as a `CopilotProfile`;
there is no model/backend branch in the allocator. The launch environment
carries only the opaque assignment token. `register-session` binds that token to
the actual Copilot session id, retires the capability, and updates both stores.
A retry finds the same pending token/generation key; expiry records `abandoned`
without returning the bag position to the pool. Handoff assignments retain the
neutral predecessor session id, while the eventual `session_id` is the
successor link. Lazy maintenance returns before locking when no ledger exists
and persists only actual expiry or terminal-history compaction. Cache-only and
coalesced-cache-hit list paths skip maintenance entirely.

Assignment state is an optional lifecycle enrichment, not a prerequisite for
worktree creation, launch, session registration, activity logging, context
emission, or status setup. Unsupported/corrupt state and lock contention warn
and retain the concrete ordinary default/manual profile already selected by the
launch path. The same fallback applies to assignment-excluded launches,
unassigned pre-feature sessions, and persisted assignments whose profile is
unavailable on the current machine. Invalid armed user configuration remains
fail-closed before any worktree mutation, including explicit-profile, recovery,
and other assignment-excluded launch classes; malformed repository-only
default-off templates remain non-load-bearing.

### Two status registers: asserted disposition vs. derived pulse

The status core carries **two complementary registers**, deliberately kept in
**separate homes** so they can never be faked from each other (the `agent-fabric`
vision's *disposition-is-asserted / pulse-is-derived* behavior):

1. **Durable disposition (asserted).** The `follow_up` / `summary` overlay on the
   worktree **record** (above). High-signal, slow-moving: the agent *asserts* it
   via `agent-worktrees status --follow-up|--resolved`. It is the single-writer
   YAML, and it is the *only* register that feeds the prune verdict.
2. **Live pulse (derived).** A per-session **sidecar** (`substatus.json` in the
   Copilot `session-state/{id}/` dir, beside context-handoff's `context.json`),
   written by the agent-worktrees **live-pulse extension**
   (`extensions/agent-worktrees/extension.mjs`) from the ephemeral
   `assistant.intent` event stream (root agent only). Low-signal, fast-moving,
   **zero agent effort**. The picker maps it to a worktree by session cwd and
   renders it as a dim, expiring line; it is dropped once stale.

**The pulse is never written to the record and never sets `follow_up`.** It is a
read-time display signal only (`sessions.SessionContext.live_intent`), so it
cannot corrupt the durable disposition or the single-writer contract. A stale or
idle pulse greys and then disappears; only the asserted disposition persists.

`active_effort` is an optional identity input to the durable disposition, not a
new register. It carries one repository-relative README path plus a declared
participant/slice. The `effort-focus` CLI is the sole writer: it validates the
authoritative worktree checkout, containment and reparse safety, effort
shape/status, declared slice, and cross-worktree uniqueness under locks. An open
binding persists `follow_up=true`; reads additionally derive the same
`follow_up`/`summary` surface and contribute one bounded pointer to the existing
record-first history digest. Bind and completed release verify the authoritative
Git root, while read paths re-inspect containment under the recorded worktree
path and stay silent on failure. A closed, stale, or unavailable pointer
contributes no session orientation.

## The Picker render flow -- never block on cross-process/IO (invariant)

The Picker is a Textual TUI: a **single event-loop thread** drives every repaint,
key handler, modal-dismiss callback, and `set_interval` tick. **No cross-process
call (a subprocess: `agent-dispatch`, `git`, `ssh`, an `agent-worktrees --json`
verb, a mux/session liveness probe) and no blocking IO may run *on* that thread.**
A single blocking call there freezes the **entire** UI for its full duration
(actions carry a 30s timeout), which reads to the operator as a crash -- e.g. a
`steer submit` on Confirm, or the authoritative mux-liveness re-verify when the
Actions menu opens.

The rule is therefore **always-async from the render flow** -- and, on top of it, a
UX rule: **always transition to the next component immediately; show cached content
with the shared spinner while the background load completes; then update in place.**
Never block, and never leave a control that looks like it did nothing.

- **Data plane -- already async.** Fleet enumeration and per-machine reads run on
  the `LiveLoader`'s background daemon threads (and `RegisteredPivotRuntime.ensure`
  for pivot lists); the loop only ever reads the *current snapshot*
  (`loader.records()`) and repaints, so a slow or hung remote never stalls a
  keystroke. The `_tick` (0.1s) pulls the latest snapshot; the loader resolves
  underneath.
- **Control plane -- route through `_run_bg`.** Every action that shells out --
  the pivot/form **submit** (`_run_pivot_form_submit`), pivot verbs
  (`_run_task_action`), contributed **worktree** actions (`_run_wt_action`) and
  **config** sections (`_run_config_section`) -- hands the blocking call to
  **`PickerScreen._run_bg(label, work, done, *, quiet=False)`**. `work()` runs on a
  daemon worker thread; the UI update `done(result)` is marshalled back onto the
  event loop via Textual **`call_from_thread`** (so no widget is mutated
  off-thread). The handler returns instantly; while it runs the **footer shows the
  shared animated spinner** (`SPINNER` braille, driven by `frame`) + the action
  label via `_busy_label` -- never a static line, so no action looks inert. Pass
  `quiet=True` when a *different* surface already shows the load state (see below).
  Progress-reporting actions use the analogous streaming worker (`run_action_stream`
  + the ProgressScreen).
- **Open-first, spinner, refine (menus & any load-gated component).** A component
  that needs a fresh cross-process probe **opens immediately from cached state**,
  shows the **same spinner** while the probe runs off-thread, and **updates in
  place** when it lands -- it never waits (frozen or blank) for the probe first.
  The worktree **Actions menu** is the reference: `_open_submenu` opens the
  `SubMenuScreen` at once from the record's current (bulk-derived) liveness (the
  verbs are already actionable); a cheap `stat` decides whether an authoritative
  `verify_worktree_active` re-verify is warranted; if so it runs via
  `_run_bg(..., quiet=True)` while the modal shows its **footer spinner**
  (`SubMenuScreen(loading=True)`), and `_refresh_wt_submenu` -> `refresh_actions`
  **refines the verb set in place** (dropping the spinner) when it completes. Cached
  verbs are correct in the overwhelming majority of cases; the refine only corrects
  edge liveness (e.g. a bare orphan), so nothing the operator can pick is a dead
  button.
- **Cheap in-process work stays inline.** Pure, record-driven computation (verb-set
  gating via `_session_action_verbs` / `_wt_submenu_verbs`, menu assembly,
  formatting) and a single `stat` (e.g. "does this worktree have a tracking
  record?") are fine on the loop -- only *cross-process* and *blocking IO* must be
  offloaded.

**Enforcement / when you add a feature.** Any new key handler, action, or
menu-open that reaches a subprocess or blocking IO must go through `_run_bg` (or a
dedicated daemon worker), never call it inline -- and any load-gated *component*
should open-first + spinner + refine rather than wait. Two regression tests gate a
runtime/probe that blocks on an `Event`:
`test_steer_submit_is_offloaded_off_the_render_flow` (Confirm returns without running
the submit inline) and `test_actions_menu_liveness_verify_is_offloaded` (the Actions
menu is open + `loading` immediately, then refines in place when the gate releases).
If you add a blocking edge, add the equivalent offload + assertion.

## Session Lifecycle

```
{project}                         # launch binstub
  |
  v
launch-session.{ps1,sh}           # pre-flight update, venv activation
  |
  v
agent-worktrees resolve           # picker UI, worktree creation
  |                                 emits JSON launch plan, exits
  v
Setup script runs                  # tools/setup/setup.{ps1,sh} or config-driven
  |
  v
Copilot CLI session                # your work happens here
  |
  v
Post-exit checks                   # detect completion markers
  |
  +-- status: pushed  --> finalize (validate content on default branch, cleanup)
  +-- status: active  --> preserve worktree for later resume
```

A config-declared normalized `setup_hook` receives one guarded writer root in
`AGENT_WORKTREES_CONFIG_ROOT`. The launcher resolves the default to the
per-project machine-local config directory and validates any explicit
destination before invoking the hook. This is an enforceable cooperative
boundary, not universal filesystem interception: custom launch commands and
legacy setup scripts remain responsible for entering through
`agent-worktrees config-root` before writing concrete setup configuration.

### Current session, conclusion, and succession (the head pointer)

A worktree is a *series of sessions*, not a single one. Its record carries an
append-only lifecycle ledger; timestamps describe observations, while monotonic
integers allocated under the record lock determine ordering:

- **`SessionEntry.activations[]`** — every start/resume-to-end association
  interval, each with an ordinal, event timestamp, owner-recorded timestamp, and
  source. Duplicate start/end hook delivery is idempotent. The legacy
  `started_at`/`ended_at` pair remains as a compatibility summary, but a resume
  never overwrites the original start.
- **`SessionEntry.state`** — `active` (default; a stopped/ended session is still
  *active* i.e. resumable until concluded), `handed-off` (concluded into a
  successor), or `concluded` (deliberately finished / sunset). **Conclusion is an
  asserted act, never inferred from liveness.**
- **`SessionEntry.successor` / `predecessor`** — the durable **two-way chain**, so
  the lineage of sessions in a worktree is traversable in both directions.
- **`SessionEntry.relation_revision`** — the last lifecycle revision that
  changed this session's own binding, conclusion, head role, or lineage. It lets
  reciprocal session projections reject stale out-of-order writers without
  rewriting every historical session whenever an unrelated relation changes.
- **`handoffs[]` / `handoff_counter`** — an incrementing ledger of handoff
  intents. Each entry has a stable external token, one predecessor, and an
  eventual exact successor. A new session can claim only the token it was given;
  it never adopts "the newest pending handoff."
- **`head_transitions[]` / `lifecycle_revision`** — the authoritative,
  replayable changes to the current session. `head_session` and `head_revision`
  are materialized caches repaired from the highest valid transition revision.
  Legacy records with no transition ledger retain their historical fallback
  until the next lifecycle write seeds a `legacy-import` transition.

Ground-layer transition primitives (in `tracking.py`) — higher layers call these
and **derive** from `resolved_head_session` rather than keeping a rival "current"
notion (per the vision's *derive-dont-duplicate*):

- `set_head_session(record, sid)` — assert a tracked session as the head (used
  when a caller adopts / takes over a worktree).
- `conclude_session(record, sid, state=…)` — mark `concluded`/`handed-off`;
  clears the head when that session was current. It never guesses a replacement
  from session order.
- `open_handoff(record, old, token)` / `link_handoff(record, token, new)` —
  number a handoff intent, then atomically link its exact successor and append
  the new head transition.
- `link_succession(record, old, new)` — write the two-way link, conclude the
  predecessor (`handed-off`), and move the head to the successor through the
  same numbered ledger.

`register_session` (the sessionStart hook) **initializes** the head for a
worktree that has no current session yet, but **never moves an existing active
head** — a second session arriving while one is still current is the contested
case the agent-bridge creation guard prevents upstream; the ground layer only
records it. A `bind-session --handoff-token <token>` call claims one exact
pending handoff and performs the predecessor conclusion, two-way link, handoff
state change, and head transition atomically.

When a launch carries an opt-in profile-assignment token, the same registration
also binds the pending assignment to the actual session id. Ordinary resume
looks up that bound assignment and rebuilds the launch with the same ordinary
profile without advancing the bag. A handoff cutover deliberately requests the
`handoff-cutover` lane and therefore draws a successor generation only while the
policy is armed.

`deregister_session` (the sessionEnd hook) consumes the hook payload just like
sessionStart. It resolves by payload cwd, then by exact previously registered
session id across projects, and closes the latest open activation interval. It
does **not** conclude the session or move the head: an exited session remains
resumable until an explicit lifecycle transition says otherwise.

### Reciprocal bound and controller projections

Each session with a changed bound relation receives a versioned
`agent-worktrees.json` sidecar in its exact Copilot session-state directory.
The sidecar is a **rebuildable projection**, not another lifecycle authority. It
contains the project/worktree identity, per-session relation revision, asserted
lifecycle state, head role/revision, and predecessor/successor lineage already
owned by the worktree record.

Lifecycle changes mark only the sessions whose relation changed. After the
authoritative YAML record is persisted, `save_record` flushes those exact
session IDs through a sidecar-scoped cross-process lock and atomic replacement.
An unrelated historical session is not rewritten when another session starts
or hands off, which keeps hook cost and synchronized-session churn bounded by
the changed relations rather than by worktree age.

Projection persistence is fail-open: a missing session directory, unsafe
link/reparse target, restored rescue marker, corrupt path, lock failure, or I/O
error cannot roll back the authoritative lifecycle operation. Corrupt
same-version JSON can be rebuilt from the record; an unsupported newer schema
is preserved untouched. The current reader release accepts schema v2
completeness metadata and compact tombstones while continuing to emit schema
v1; encountering v2 on a write path is an explicit blocked projection update,
never a downgrade. Exact session-directory identity rejects case-folded
or short-name aliases as well as link/reparse escapes while accepting canonical
extended Windows paths. Reads are capped before allocation, writes are
deterministic and skip semantic no-ops, POSIX files and runtime directories are
private, and temporary staging lives outside synchronized session directories.
Additive unknown fields survive same-version relation updates. The writer reports
`written`, `current`, `blocked`, or `deferred`; only deferred relations remain
dirty for a later save retry, while a newer unsupported schema is deliberately
blocked without repeated write attempts.

Schema v2 requires explicit `history_complete`, `overflow`,
`omitted_relations`, `tombstone_overflow`, `tombstone_sequence`, and
`relation_tombstones` fields. Overflow uses `omitted_relations: null`; complete
relation sets use zero. V2 tombstones are opaque
`{key_sha256, relation_revision, sequence}` records. `key_sha256` is SHA-256 of
the UTF-8 compact JSON array `[project,worktree_id,role]`, encoded with
`ensure_ascii=true`, separators `(",", ":")`, and no trailing newline.
`tombstone_sequence` is at least the maximum retained tombstone sequence.
Readers validate these fields and expose relation-set and tombstone-fence
completeness separately.

Controller identity is a separate, bounded authority on `WorktreeRecord`.
`controllers[]` holds at most 32 typed relations; each carries a worktree or
session kind, source (`owner-ref`, `caller-worktree`, `parent-session`, or
`explicit`), canonical ClaimRef when available, exact controller session ID
when known, active/ended state, created/ended timestamps, and a per-relation
revision allocated from the monotonic `controller_revision` counter. Active
relations are protected by the bound; older ended history is displaced first.
An explicit repair may remove a relation, but the nonzero record revision keeps
retained legacy creation fields from recreating it on the next load.
Malformed or future declared controller state is preserved opaquely across
ordinary saves. Valid relations remain readable, but controller mutation is
refused until an explicit repair can replace the unsupported authority.

New records derive initial controller identity from the creation metadata they
already receive. A qualified `owner_ref` is preferred, a same-worktree
`caller_worktree` enriches rather than duplicates it, and `parent_session`
supplies the exact session or stands alone for a caller outside any worktree.
Legacy records retain their existing creation fields without deriving or
persisting controller relations during ordinary reads or saves; explicit
`backfill-sessions` or `doctor --fix` owns that later migration under the
record lock. The migration derives only from the existing `owner_ref`,
`caller_worktree`, and `parent_session` authority, leaves opaque or invalid
controller metadata report-only, and retains the legacy fields for older
readers.
An empty controller model emits no new YAML and therefore preserves the legacy
common-case bytes.

When an exact controller session is known, only that session is marked dirty.
After the child record persists, the writer upserts a `role=controller`
relation into that exact session's sidecar, or retracts the relation after an
explicit authoritative removal. The projection key includes the role, so one
session can be bound to its own worktree while controlling several child
worktrees without either relation replacing another. Ending a controller
projects terminal state rather than changing binding. Removal leaves a bounded
per-key revision tombstone in the sidecar, preventing a delayed older upsert
from resurrecting the relation.

Controller mutation helpers acquire the worktree record lock, reload the full
authoritative record, allocate the next revision, and save that fresh object.
This serializes concurrent controller changes without rolling back unrelated
newer worktree state. The `save=False` form exists only for callers that already
hold the same record lock through the final save.

Controller metadata is additive on worktree JSON rows, `head-session`, and a
worktree-scoped `list-sessions` envelope. Picker normalization passes it through
but does not consult it for ACTIVE classification, resume targeting, occupancy,
liveness, or the asserted head. Those surfaces also carry derived
`controller_findings`: an exact controller session follows only explicit
successor and handoff links to a unique active terminal session. Forks, cycles,
missing records or session trees, concluded controllers without successors,
unsupported schemas, and remote controllers remain explicit findings rather
than guessed targets. A restored projection is usable only as a read-only hint:
its exact session ID, unique bound project/worktree identity, relation revision,
head revision, and known bound fields must match the current authoritative
record. Foreign, stale, newer, colliding, or multiply-bound restored state stays
an explicit report-only finding.

Worktree JSON additionally carries one normalized `reciprocal_relation`
presentation object. Its `binding` and `control` members remain orthogonal, so a
worktree can be both locally bound and controlled from elsewhere without
turning the controller into its head. The top-level presentation `state`
summarizes `bound-here`, `controlled-elsewhere`, `handed-off`, `terminal`, or
`ambiguous` (`unbound` is the legacy/no-relation baseline). Controller
navigation actions name an exact project/worktree/machine target only when one
active target is unambiguous; restored, unsupported, incomplete, stale, or
unknown findings are inspect-only. This object is advisory display data: it
does not change classification, liveness, occupancy, cleanup, or resume
authority.

Two detail surfaces expose the same authority for graph and visualization
consumers without enlarging the Picker's hot list payload:

- `worktree-lineage --worktree <id> --json` renders one bounded authoritative
  record as sessions, head transitions, handoffs, controller relations,
  normalized reciprocal state, exact-session projection health, and a graph.
  Explicit forks, cycles, missing referenced sessions, and concluded terminal
  chains remain findings rather than guessed edges.
- `session-lineage --session-id <id> --json` reads only that exact session's
  `agent-worktrees.json`, validates each retained relation against its
  authoritative worktree record, and preserves restored provenance, tombstones,
  unsupported/invalid state, and projection overflow. An incomplete projection
  is never presented as the session's complete controller set. Embedded
  worktree summaries mark presentation unevaluated rather than reading any
  other session projection or misclassifying missing evaluation as ambiguity.

Neither command enumerates the live session-state root. Corpus-wide graphing
continues to consume synchronized session archives or an index produced during
synchronization.

The explicit `backfill-sessions` and `doctor` paths also audit known bound and
controller relations by exact session ID. A per-run projection budget bounds
the work. Local missing, stale, or corrupt same-version projections can be
rebuilt with `--fix`; restored trees remain read-only even when their hints
validate. Unsupported newer schemas and incomplete/overflowed, ambiguous,
foreign, colliding, or newer projection state are never rewritten.

When `sessionStart` cannot establish an authoritative binding from the launch
binding, payload cwd, or mux ancestry, the existing registration context
producer reads only that exact session's projection. `session-recovery` exposes
the same bounded machine-readable report. Each projected relation must match a
current authoritative record and its role-specific revision vector before it
can produce a pointer. The result distinguishes bound-here, bound-elsewhere,
handed-off, local/remote controller, terminal controller, ambiguous, foreign,
stale, newer, invalid, and unsupported states. Recovery context never binds or
mutates: it tells the session what to verify and which explicit action is
appropriate.

The resident session reconciler repairs missed sidecar writes through a
separate fixed-budget queue. It acts only after a fresh mux catalog proves the
child worktree has no mux and exact session-lock reads prove no bound Copilot is
live. Each repair takes nonblocking record and sidecar locks, reloads the record,
and compares lifecycle, head, and controller revisions before writing. Verified
revision triples are remembered in a bounded cache, so current or permanently
blocked projections quiesce instead of consuming every later tick. Immediate
lifecycle writes remain the primary path; Picker/list demand starts the same
resident monitor before its derived data is needed, so there is no independent
scheduled reconciler competing for ownership while the machine is otherwise
idle. Operators that need a low-duty backstop can schedule the bounded
`reconcile-sessions` one-shot; it shares the resident reconciler and exits after
one configured record/session/projection budget.

Beyond the head bookkeeping, `register_session` also **re-seeds this session's
mux status-bar updater** (`_spawn_status_updater` → a detached
`status-updater` for `wt-<id>`). This is a best-effort, off-mux-safe side
effect that lets an attached long-lived session recover its status bar after a
deploy retires the prior updater, independent of a psmux/tmux attach/join — see
[cli-reference.md § Off the paint path](cli-reference.md#off-the-paint-path-status-updater-psmux--tmux)
(dotfiles #915). It is idempotent via the `@aw_updater` token guard.

**Cross-layer write interface — `conclude-session` / `link-succession`.** A
higher layer in its own venv cannot import `tracking.py`, so the two writes a
handoff needs are exposed as CLIs alongside the `head-session` read:

```bash
# Assert a session concluded; never guesses a replacement head.
agent-worktrees conclude-session --worktree <id> --session <sid> \
    --state handed-off --handoff-token <token>
# {"worktree_id": "...", "session": "<sid>", "state": "handed-off",
#  "head_session": "<sid>"|null}

# Write the two-way link explicitly (both ids known); concludes the predecessor
# and moves the head to the successor.
agent-worktrees link-succession --worktree <id> --handoff-token <token> \
    --predecessor <sid> --successor <sid>
```

Both resolve the worktree across **all** projects (a higher-layer caller's CWD
is unrelated to the worktree it acts on) and persist to that resolved record.
Unlike the fail-open `head-session` read, an unknown worktree/session is a real
error here — a mutation must not silently no-op. The live cutover opens the
numbered handoff when the brief is stored, then the successor claims that exact
token through `bind-session`; `link-succession` remains the explicit form for a
caller that already holds both ids.

**Safe terminal worker interface — `conclude-disposable`.** A higher layer that
explicitly owns a disposable CLI worker class can conclude the exact recorded
allocation and optionally request exact-id managed teardown:

```bash
agent-worktrees conclude-disposable \
    --worktree <exact-id> \
    --session <exact-session-id> \
    --policy disposable-cli \
    --owner <allocator> \
    --remove \
    --json
```

The command accepts only an exact worktree id and the explicit
`disposable-cli` policy. It first preserves any live mux or bound Copilot
session. Once the worker is gone, it requires the supplied session to match the
worktree's asserted lifecycle head when one exists. Preservation gates run
before that exact session is concluded, so a skipped worktree remains fully
resumable. The checkout is then inspected under the shared worktree lifecycle
lock. The short acquisition wait is separate
from the stale-lock age, so contention skips rather than breaking a healthy
longer-running lifecycle operation. Pending handoffs, follow-ups, resource
obligations, pairs, open pull requests, branch drift, arbitrary dirty paths,
and local commits all produce structured skip reasons and remain untouched,
including generated overlays. A clean branch with zero commits ahead of the
configured upstream may remain behind without being rewritten.
Repository resolution uses the record and the legacy/default fallback; a truly
unknown repository is held. If the checkout directory is
already absent, any surviving local branch is still compared with its configured
upstream and preserved when it contains unique commits.

A successful conclusion converts the record to a managed, final CLI worker.
Without `--remove`, it returns for a later managed-GC pass. With `--remove`, it
invokes that same managed-worktree sweep for only the exact id with zero idle
grace. The sweep independently re-checks final/unused state, live
session/mux/attachment, follow-up, activity knowledge, and idle grace. CLI
embodiment and final managed removal share the repository lifecycle fence. The
managed terminal record rejects late session registration, disposition changes,
and new resource claims before removal; Picker reconciliation also leaves that
terminal tombstone intact.
The final decision holds the record lock only for its metadata recheck, then uses
non-forced Git removal so a concurrently dirtied checkout is preserved. Lock
wait diagnostics use stderr and therefore never corrupt JSON command output.
Managed removal resolves each record's own repository, verifies the observed
branch and HEAD before reconciliation, and deletes the final branch ref only
with its expected old object id. It retains the tracking record if Git worktree
or branch removal fails, so cleanup remains retryable rather than converting a
failed removal into an invisible orphan.

**Cross-layer read interface — `agent-worktrees head-session`.** Because a
higher layer (agent-bridge, context-handoff) runs in its *own* venv and cannot
import `tracking.py`, the ground layer exposes its head derivation as a
read-only CLI:

```bash
agent-worktrees head-session --worktree <id> --json
# {"worktree_id": "...", "tracked": true, "head_session": "<sid>"|null,
#  "head_revision": 7, "pending_handoffs": [...],
#  "active": <bool>, "occupied": <bool>, ...}
```

`active` retains its compatibility meaning: a current session exists.
`occupied` additionally includes a pending handoff, so newer consumers can
distinguish an in-flight cutover from a live head. An **unknown/untracked**
worktree is not an error (`tracked:
false`, exit 0): a guard that cannot find a record must **fail open** and permit
the create, never refuse it. This is the sole sanctioned way for another layer
to learn "which session is current here" — it derives, it does not keep a rival
pointer (*derive-dont-duplicate*).

**Enriched session listing — `agent-worktrees list-sessions`.** The session
registry a consumer already reads to enumerate a worktree's sessions now carries
the same derived lifecycle, so a top consumer (agent-bridge → Neuron Forge) can
render a worktree **head-first** and badge the rest "no longer current" without a
second call:

```bash
agent-worktrees list-sessions --worktree <id> --json
# {"head_session": "<sid>"|null, "head_revision": 7, "handoffs": [...],
#  "sessions": [{"id": "<sid>", "state": "active"|"handed-off"|"concluded",
#                "is_head": <bool>, "activations": [...],
#                "profile_assignment": {...},
#                "interface": "cli"|"acp",
#                "origin": "user"|"system"|"delegate", ...}, ...]}
```

For machine-level consumers, `list-sessions --all-projects --json` reads every
adopted project's tracking directory and emits one row per session. Duplicate
registrations with conflicting provenance fail closed to
`interface=unknown`, `origin=unknown`, and `provenance_conflict=true`.

Each session entry gains `state` (the asserted `SessionEntry.state`; `active`
for legacy/backfill entries with no stamp) and `is_head` (marks the one session
`resolved_head_session` derives as current). When scoped to a single worktree,
`head_session` is also surfaced on the envelope. These are **derived** from the
same ground-layer record — a consumer reads them, it never recomputes a head of
its own.

Worktree completion is split into two explicit steps:

**Step 1 -- push-changes** (run by the agent during the session):
1. Squashes commits on the worktree branch
2. Rebases onto `origin/{default_branch}`
3. Validates core files
4. Fast-forward merges into local `{default_branch}`
5. Pushes to origin (with retry on rejection)
6. Updates tracking YAML to `status: pushed`

**Step 2 -- finalize** (run by the agent or post-exit hook):
1. Non-mutating validation that branch content is on upstream
2. Removes the worktree directory and branch
3. Updates tracking YAML to `status: finalized`

On push failure, the worktree is preserved and marked `status: orphaned`.

### Resource obligations -- the finalize accountability gate

Before finalize's content validation runs, an **obligation gate**
(`finalize.validate_and_finalize` → `_assert_obligations_settled`) asserts that
every outbound resource the worktree still owns is settled. This holds a worktree
**accountable** for what it allocated (cross-repo worktrees, borrowed CodeSpaces,
containers, bridge sessions) so finalizing never orphans unfinished work. (Effort
`resource-obligation-settlement`; the fabric-wide overview is in
[`../../../docs/architecture.md`](../../../docs/architecture.md#resource-obligations--accountability).)

- **Disposition on the claim ledger.** Each `tracking.ResourceClaim.state` is a
  disposition `∈ {active, at-rest, released, abandoned}` (`agent_worktrees.obligations`).
  `active` (and any missing/unknown value) **blocks**; `at-rest` (resource work
  safe), `released` (claim torn down), and `abandoned` (reclaimed by the sweep) do
  not. `at-rest ≠ released` — a resource can be safe yet its claim still held, or
  released without the resource being destroyed. For a leaseable resource the
  disposition mirrors onto the lease record's `context` (`disposition` key) for
  cross-machine visibility — populated by the resource plugin at settle/release
  (agent-codespaces at clean disconnect → `at-rest`) and read back by the reclaim
  sweep.
- **The gate is cheap + local + enforcing by default.** It reads only the owner's
  own `record.resources` for `is_unsettled` claims — O(claims), no traversal — and
  runs **before any destructive step**. `obligations.gate_mode()`
  (`AGENT_WORKTREES_OBLIGATION_GATE`) is `block` by default (refuse unless
  `--abandon`, which re-homes via `release_all_resources`); `warn` relaxes it to
  surface + proceed; `off` skips. (A value the operator *set* but we don't
  recognize degrades to `warn`, never enforcing on a typo.)
- **Incremental settlement (recursion collapse).**
  `tracking.settle_resource_claim` flips one claim's disposition; the
  cross-repo-worktree hook `_settle_parent_obligation` runs on a child's finalize
  and flips the claim its **parent** holds on it to `at-rest` (same-machine parent
  via `owner_claim_ref` → `project_dir(project)/worktrees/…`), so the parent's
  gate trusts the recorded verdict instead of recursing.
- **Ledger CRUD + reclaim.** `agent-worktrees claims {show,add,settle,release}`.
  `claims add --owner-ref <machine/project/worktree_id>` journals onto a
  **cross-project** owner resolved by qualified ref (not the caller's cwd) — for a
  call-site whose cwd is not the owning worktree (e.g. agent-codespaces journaling
  a CodeSpace claim from the daemon's cwd). `claims sweep [--apply]` runs the
  never-wedge reclaim on demand; `claims orphans` / `claims cleanup [--apply]`
  list and act on the durable orphanage.
- **Never-wedge (`agent_worktrees.sweep`).** The reclaim sweep flips a
  provably-gone + provably-safe `active` obligation to `abandoned` (never
  `at-rest`), so a crashed/missed holder cannot freeze its parent. Per-kind
  verdicts: a **worktree** proves gone+safe from the child's record/branch
  (same-machine `claimant` liveness + squash-aware branch-merged check); a
  **leaseable** kind (codespace/container) reads the **disposition mirror** off
  the shared exclusion lease (`sweep.lease_disposition_of` →
  `obligations.from_context`), which resolves both the **missed-settle** and the
  **cross-machine** cases (the shared lease is the source of truth, so any
  machine's sweep reclaims its own stale claim). Reclaim is **explicit only** via
  `claims sweep`; finalize never auto-reclaims creator ownership. A
  `finalize --abandon --handoff-to <recipient-or-flow>` re-homes
  still-unsettled obligations to a durable **orphanage** (not dropped);
  selective `claims cleanup <ref-or-source-worktree>` accepts/reclaims them.
  *(Phases 4–6 complete; effort `resource-obligation-settlement` closed.)*

### Recovery Mode

```bash
my-project -Recovery    # Windows
my-project --recovery   # Linux/WSL (also accepted on Windows)
```

Skips vault credential loading for debugging broken bootstrap
infrastructure.

## Terminal Integration

| File | Platform | Description |
|------|----------|-------------|
| `session-options.sh` | Linux/WSL | Per-session tmux options the launcher stamps onto each session (status bar + behaviors); replaces a global `~/.tmux.conf` |
| `apply-mux-keybinds.sh` | Linux/WSL | **Opt-in** server-global tmux tuning (keystroke passthrough + `escape-time`); run by the user or a machine-restore flow |
| `session-options.ps1` | Windows | Per-session psmux options the launcher stamps onto each session (status bar + behaviors); replaces a global `~/.psmux.conf` |
| `apply-mux-keybinds.ps1` | Windows | **Opt-in** server-global psmux tuning (keystroke passthrough); run by the user or a machine-restore flow |
| `tabby-template.yaml` | Linux | Tabby terminal profile template |

The Windows installer generates **Windows Terminal fragments** at
`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\AgentWorktrees\`
with profiles for each registered project (local + remote SSH machines).

## Multiple Projects

Register multiple repos on the same machine. Each gets its own config
directory (`~/.{project}/`) and binstub. The shared runtime is installed
once:

```bash
agent-worktrees register my-app --repo-dir ~/src/my-app
agent-worktrees register dotfiles --repo-dir ~/src/dotfiles
```

## Update Mechanisms

### Pre-Flight Auto-Update (opportunistic)

On **every** session launch the wrapper stages an agent-worktrees marketplace
pull in the background (`stage-update`) and, once the picker closes, joins and
applies it (`Invoke-UpdateApply` / `invoke_update_apply`): it runs the runtime
installer **iff** the staged download changed the payload (fingerprint diff) or
the deployed runtime version drifted from the payload, then the pre-launch
self-update and a plugin reconcile. This path is deliberately lightweight -- it
only touches agent-worktrees -- so it is **not** relied on to fully update
sibling plugins or modules.

Skip with `--no-update` or `WORKTREE_NO_UPDATE=1`.

### Optional Machine Settings Reconciliation

Before a fresh Copilot process starts, the launch wrapper opportunistically
resolves the public `agent-machines` command. When available, it runs:

```text
agent-machines restore --all-projects --only copilot.settings --apply --json
```

This restores the machine-wide union of adopted Copilot settings immediately
before Copilot reads them, including model, effort, and context preferences
that the CLI may periodically clear. The sibling plugin remains optional:
launch proceeds unchanged when `agent-machines` is not installed. A present
provider that fails aborts launch rather than silently starting with drifted
settings.

Every resolved command is prefixed by an installed pre-exec wrapper, so
explicit templates, legacy setup scripts, normalized launches, interactive
sessions, and direct agent-bridge launches share the same seam. The normalized
default setup also sources the helper for callers that invoke it directly. A
process-scoped marker inherited by child processes prevents duplicate
reconciliation. Reattaching to an existing mux session does not execute the
command and therefore does not reconcile. Recovery mode bypasses
reconciliation so a broken sibling provider cannot lock out repair sessions.

During an interactive launch the join + apply prints a **status line at each
waiting step** (joining the background download — the up-to-90s step most
likely to look "stuck" — inline re-download, installer, bootstrap-service
self-update, plugin reconcile) so the operator understands the otherwise-silent
post-picker/pre-mux pause instead of staring at a frozen screen. The lines are
gated to the interactive exec/refresh paths only: direct-dispatch subcommands
and `--stdio`/`--json` callers stay quiet (in `--stdio` mode the launcher's
`Write-Host` is already routed to stderr, off the ACP channel). Every step is
still written to the per-PID setup log under `$TMPDIR/worktree-setup-logs`
regardless.

### "Update available" -> the full update (picker refresh)

When the picker's version indicator shows **Update available** and you press
enter, the launcher exits with `action=refresh` and runs the **full**
`agent-worktrees update` (below) before re-execing the now-updated launcher --
*not* just the opportunistic apply above. This closes the gap where an
already-pulled-but-not-yet-deployed payload, or a sibling plugin/module, could
relaunch stale (dotfiles#443).

### Plugin Marketplace Update / `update` command

```bash
copilot plugin update agent-worktrees@copilot-extensions   # payload only
agent-worktrees update                                     # full, comprehensive
```

`agent-worktrees update` is the authoritative, comprehensive update. It:

When the standalone Worktree Manager is installed and passes its `--version`
health check, `agent-worktrees update` hands off to `worktree-manager update`;
the manager re-enters the in-plugin flow with `--no-manager`. If the manager is
absent or unhealthy, the in-plugin flow runs directly, so update never
dead-ends.

The in-plugin flow:

1. Pulls the agent-worktrees marketplace payload.
2. Refreshes **every** registered plugin payload (incl. payload-only plugins).
   After an authoritative catalog refresh, it uninstalls inactive installed
   identities that no longer exist in that marketplace. Active or
   activation-unknown identities remain fail-closed and are never purged.
3. Deploys the agent-worktrees runtime installer.
4. Updates sibling modules listed in `modules.json` (`agent-bridge` today).
5. Reconciles registered runtime plugins not covered by the module/self steps.
6. Fast-forwards the managed repo anchor(s).

**Quick skip (version-gated).** Runtime deploy steps skip a runtime whose **deployed
version already equals its (freshly-pulled) payload version** -- the `devN`
version tracks commit content, so an equal version means the runtime is already
current, and the (slow) re-deploy is skipped. The skip is conservative: an
unknown deployed version (no `deploy-manifest.json`) always re-deploys, so a
stale runtime is never left behind. Pass `--force` to re-deploy every runtime
unconditionally.

### Version Checking

All three version sources must agree:

| File | Purpose |
|------|---------|
| `plugin.json` | Marketplace version detection |
| `pyproject.toml` | Runtime `--version` output |
| `.github/plugin/marketplace.json` | GitHub-hosted marketplace catalog |

See [CONTRIBUTING.md](../../../CONTRIBUTING.md) for versioning details.

## Picker Pivot Registry (Cross-Plugin)

The interactive Textual picker (`picker_tui/engine.py`) shows top-level
**pivots**. **Worktrees** is the home view; **Profiles** is hosted under the
right-aligned ⚙ Configuration menu (#1426); the old standalone **Maintenance**
view is eliminated -- its bulk Clean/Sync live as buttons on the Worktrees row
(#1427). Another plugin, installed in
its **own separate venv**, can contribute an additional pivot without
agent-worktrees importing its Python. Because each plugin installs standalone,
setuptools entry-points do not cross venvs; a **filesystem manifest registry**
does.

```
~/.agent-worktrees/pivots/<name>.json     # one manifest per contributed pivot
    { "label": "Tasks", "after": "Worktrees",
      "list": ["agent-dispatch", "inbox", "--machine", "{machine}"],
      "entry":   { "id": "id", "title": "title",
                   "worktree": "target_worktree", "badges": ["labels"] },
      "actions": [ { "label": "Abandon", "run": ["agent-dispatch", "abandon",
                     "{task_id}", "--permit"] }, ... ] }
```

- **Discovery and reconciliation** (`picker_tui/pivots.py`): one classifier
  scans the directory at startup (and on `r`-refresh), validates every external
  command, and returns both active contributions and exhaustive findings.
  Entries are independent fault boundaries. A complete/absent scan withdraws a
  removed, malformed, disabled, or stale contribution; registry or entry I/O
  uncertainty retains only last-known state and never activates a fresh entry.
  `AGENT_WORKTREES_PIVOTS_DIR` overrides the location (tests, escape hatch).
- **Attributed materialization** (`ensure_pivots`, #2180): before runtime
  discovery, agent-worktrees resolves plugins that are currently enabled
  globally or in an adopted project, verifies one current root, and reads each
  root's shipped `pivots/*.json` template. It publishes a schema-v2 runtime
  manifest containing `plugin`, `plugin_root`, and `template`, with command
  targets resolved to canonical absolute paths. Cached installed payloads alone
  are never authority. Publication is append-only and exclusive-create: an
  existing file is never replaced, and a changed template gets a deterministic
  fingerprinted sibling. Routine discovery never deletes registry files.
- **Compatibility and diagnostics.** Known schema-v1 suite manifests remain
  active only while their contributing plugin is enabled and identity-verified,
  with a `legacy-unattributed` advisory. Unknown schema-v1 manifests retain
  compatibility as report-only unknown legacy entries; unversioned manifests
  are operator-owned. Operational warnings are capped and fingerprint-
  deduplicated. `agent-worktrees doctor [--json]` reports the same classifier's
  findings exhaustively with exact report-only remedies.
- **Shipped list pivots (this repo).** Several plugins ship a manifest in their
  own `pivots/` dir (materialized from its verified active root by
  `ensure_pivots`), each a **zero-engine-change** `list` pivot that appears only
  while that layer is currently enabled and its command target is usable:
  `agent-dispatch` -> **Tasks** (`agent-dispatch inbox --machine {machine}`),
  `agent-bridge` -> **Bridges** (`agent-bridge agents --json`),
  `agent-codespaces` -> **CodeSpaces** (`agent-codespaces list --json`),
  `agent-containers` -> **Containers** (`agent-containers fleet --json`). Each
  CLI must print a **bare JSON array** of objects; the manifest's `entry` map
  pulls id/title/subtitle/badges out of each. This is graceful-capability-scaling
  in practice: adopt more of the fabric, get more pivots; adopt less, and the
  picker is never burdened by a pivot for a layer you don't have.
- **Data + actions** (`picker_tui/tasks.py`): the validated `list` command is run
  as a **subprocess** on a background thread, cached per
  machine, and expected to print a JSON array. `actions` argv templates are run
  the same way. Placeholders (`{machine}`, `{worktree}`, `{id}`/`{task_id}`,
  `{title}`, plus any entry field) are substituted at activation time. Data
  flows **only** through the contributing plugin's CLI -- never a cross-venv
  import -- so the seam stays generic for future pivots (Bridges, Containers, ...).
- **Worktree-row actions (cross-plugin, #B).** Beyond contributing a *pivot*, a
  manifest may declare `worktree_actions` -- a list of `{label, run, when?}` that
  augment a **worktree's** Enter sub-menu on the built-in Worktrees view. This is
  how a layer reaches *into* the core view (a bridge's "Send message", a
  dispatcher's "Dispatch task here") without owning a pivot. `run` is an argv
  template substituted from the worktree's context (`{worktree}`/`{machine}`/
  `{env}`/`{repo}`/`{id4}` plus the record's fields); `when` (optional) gates the
  action to worktrees whose normalized record matches every field. Discovered by
  `discover_worktree_actions` from the **same** manifest dir -- **independent of
  `list`**, so a manifest may contribute only worktree actions (no pivot). A
  contributed label never shadows a built-in verb; the action runs as a
  subprocess (`tasks.run_worktree_action`) and the picker rescans after.
- **Configuration sections (cross-plugin, #B slice 2).** A manifest may also
  declare `config_sections` -- a list of `{label, run, confirm?}` that augment
  the right-aligned **⚙ Configuration** menu (which hosts built-in Profiles).
  This gives a settings-oriented layer a *home* under Configuration (an SSH
  layer an "SSH" entry, an MCP layer an "MCP" entry) without owning a pivot or
  touching a worktree row. `run` is an argv template substituted from **global**
  picker context (`{machine}`/`{repo}` only -- config sections are not
  per-worktree, so there is no `when` gate). Discovered by
  `discover_config_sections` from the **same** manifest dir -- **independent of
  `list`**, so a manifest may contribute only config sections. Built-in Profiles
  is always listed first; contributed sections follow in stable
  (filename, declared) order. Selecting a section runs it as a subprocess
  (`tasks.run_config_section`) and the picker rescans after; selecting Profiles
  still switches to that pivot as before.
- **Dispatch is kind-keyed, not index-keyed.** Built-in pivot logic switches on
  the pivot *kind* (`worktrees`/`maintenance`/`profiles`/`registered`), so an
  inserted pivot never renumbers the built-ins.
- **Placement** (`PIVOT_PLACEMENT`, keyed by kind) decides *where* a pivot is
  reached from: `left` rides the left ◀▶/`[ ]` cycle (the default); `config` is
  hosted under the right-aligned **⚙ Configuration** menu (Profiles lives here --
  user-local settings only, never repo-managed, #1426); `hidden` is an ordering
  anchor kept only so registered `after` hints still weave. Placement partitions
  the tabs **without** touching the `order_pivots` weave, so a registered pivot's
  `after: "Profiles"` keeps working even though Profiles left the left rail.

### Action kinds: external (CLI) vs internal (navigation)

An `actions` entry is one of two shapes:

- **External (default)** -- `{"label": …, "run": [argv…], "confirm": false}`.
  The `run` template is spawned as a subprocess (as above). This is the right
  choice for anything that *does work* (open, abandon, retry, …).
- **Internal (picker navigation)** --
  `{"label": …, "kind": "internal", "verb": "jump-host", "args": ["{worktree}"]}`.
  No subprocess is spawned; the picker handles the `verb` itself against its own
  state. `args` (optional) become the template the handler substitutes. This
  exists because a subprocess **cannot** move the picker's cursor, switch a
  machine tab, or reveal hidden rows -- state a CLI has no handle on.

  Handlers live in `engine.PickerScreen._internal_pivot_action`; the registry is
  intentionally tiny and defensive (an unknown `verb` is a reported failure,
  never a raise). The first verb is:

  - **`jump-host`** -- navigate to the Worktrees view, switch to the host machine
    tab of the worktree named by `args`/`worktree`, reveal hidden if it is a
    bridge/system row, and highlight it (matched by **stable worktree id**, never
    a live list index). The same primitive backs the built-in *Jump to host*
    per-worktree action for bridge/system worktrees (#1424).

**Boundary (deliberate).** Modules contribute a *generic task-list* pivot plus
external/internal actions -- **not** arbitrary custom render surfaces or
in-process Python. The CLI-over-manifest seam is the cross-venv-correct answer to
"each plugin installs in its own venv"; richer per-module rendering is explicitly
out of scope (#1425).

### Modal-overlay registry → native `ModalScreen`s (the F4 migration, now complete)

The picker's dialogs (quit-confirm, the per-worktree submenu, the message peek,
the registered-pivot action menu, the ⚙ Configuration menu, the maintenance
menu, the Clean/Sync scope dialog, the options menu, the profile-apply confirm,
the progress spinner) *were* hand-rolled **modal overlays**: mutually exclusive,
each stored as a truthy instance attribute, and each with a key handler
(`_key_*`) and a render handler (`_overlay_*`). `PickerScreen._overlay_registry()`
was the **single ordered table** of `(state_attr, key_handler, render_handler)`,
and `_active_overlay()` returned the first spec whose state was set. Three call
sites derived from it -- `_dispatch_key` (dispatch), the background-dim decision,
and the render dispatch -- where each had previously hand-maintained its **own**
parallel list that could silently drift when an overlay was added. Consolidating
that behind one registry was the first slice of the incremental migration toward
Textual-native focus (#85 F): a single seam *before* converting individual
overlays to Textual `ModalScreen`s -- **extend, not rewrite**, per
`visions/picker`'s stability bias.

**That migration is now complete: all nine overlays are native `ModalScreen`s,
and the manual seam has been retired.** `_overlay_registry()` / `_active_overlay()`
are gone; `_dispatch_key` only ever runs for the top-level views (a native modal
sits above `PickerScreen` on Textual's screen stack and consumes keys itself),
`render` no longer dims/blits a manual overlay, and `on_key` lets global BINDING
keys bubble unconditionally. The dead manual-panel helpers (`_prow`,
`_blit_panel`) went with it. A guard test (`test_manual_overlay_seam_is_retired`)
asserts the seam methods no longer exist and that a nav key drives the main-view
selection directly. The slice-by-slice history below records how each overlay was
converted.

**First overlay migrated to a native `ModalScreen` (F4).** The quit-confirm
dialog is no longer a manual render/dispatch overlay: `_open_quit_confirm`
`push_screen`s a `QuitConfirmScreen(ModalScreen[bool])`, so Textual owns the
screen stack, the dim backdrop, focus, and key routing; the screen returns its
verdict via `dismiss(True|False)` and a callback runs `app.exit()` on quit. It
is therefore *absent* from the overlay registry (Textual, not `_dispatch_key`,
dispatches its keys). The remaining overlays stay on the manual model for now;
the registry is the seam that lets them convert one at a time. The real-framework
`pilot.press` harness validates the modal end-to-end (open on Esc/q, Stay/Quit,
`app.exit` only on confirm).

**Second overlay migrated: the Profiles Apply confirm (F4).** The
`ProfConfirmScreen(ModalScreen[bool])` replaces the `prof_confirm` manual
overlay by the same pattern: `_apply_profiles` computes the add/remove diff and
`push_screen`s the modal with the diff payload; the screen renders the
per-host `+`/`-` review and the destructive-regeneration caution, and returns
via `dismiss(True|False)`. On `True` the callback runs `_start_profiles_run(cf)`
(the diff is now passed as an argument rather than read back off picker state);
on `False` it is a no-op. It too is *absent* from the overlay registry. The same
`pilot.press` harness validates it end-to-end (Apply confirms and runs the
per-host progress, Esc cancels without writing).

**Third overlay migrated: the registered-pivot action menu (F4).** The
`TaskMenuScreen(ModalScreen[int])` replaces the `task_menu` manual overlay and
introduces the *list-menu* variant of the pattern: `_open_task_menu`
`push_screen`s it with the focused task's declared actions and returns the
chosen action *index* via `dismiss(int|None)`; the callback runs the selected
action (`None` cancels). Navigation (`up`/`down`, wrapping) updates the
highlight and the per-action description in place. It too is *absent* from the
overlay registry, and the `pilot.press` harness drives it end-to-end (open,
arrow to an action, Enter runs it; Esc/q/Tab cancel).

**Fourth overlay migrated: the ⚙ Configuration menu (F4).** The
`CfgMenuScreen(ModalScreen[int])` replaces the `cfgmenu` manual overlay by the
same *list-menu* pattern: `_open_cfgmenu` builds the item list (config-hosted
pivots first, then contributed Configuration sections), pre-selects the current
config-hosted pivot, and `push_screen`s the modal, which returns the chosen item
*index* via `dismiss(int|None)`; the callback switches to the selected pivot or
runs the selected section (`None` cancels). It too is *absent* from the overlay
registry, and the `pilot.press` harness drives it end-to-end (open, Enter selects
Profiles / arrow to a contributed section and run it; Esc/q/Tab cancel).

**Fifth overlay migrated: the Maintenance actions menu (`maint_menu`) → native
`ModalScreen` (F4).** The `MaintMenuScreen(ModalScreen[int])` completes the trio
of *list-menus*: it lists the actions available for the selected worktree set
(Sync / Cleanup / Finalize / Stop) with a live per-action description and returns
the chosen action *index* via `dismiss(int|None)`. Its **two** openers
(`_open_maint_menu` for the maintenance selection, `_open_wt_action_menu` for the
Worktrees-list bulk action) now share `_push_maint_menu` (pushes the modal) and
`_run_maint_action` (runs the chosen action) -- they build the id set / action
list differently but dispatch identically. Removed `self.maint_menu`/
`maint_menu_idx`, its registry entry, footer branch, `_key_maint_menu` and
`_overlay_maint_menu`. It too is *absent* from the overlay registry, and the
`pilot.press` harness drives it end-to-end (open, arrow to an action, Enter runs
it; Esc/q/Tab cancel).

**Sixth overlay migrated: the per-worktree action menu (`submenu`) → native
`ModalScreen` (F4).** The `SubMenuScreen(ModalScreen[tuple])` is the last of the
menu overlays. It renders the focused worktree's header (title + meta) and its
state-driven verbs (Open/Resume, Messages, Sync, Cleanup, Finalize, Stop, Jump to
host/caller, plus any contributed actions), tracks the highlight **and the No-mux
toggle** (Space, only while *Open* is focused -- the one in-place mutation among
the menus), and returns the chosen `(action_label, no_mux)` via `dismiss(tuple)`
-- or `dismiss(None)` on cancel; `_open_submenu`'s callback dispatches the verb
(built-in or contributed). Removed `self.submenu`/`submenu_idx`, its registry
entry, footer branch, `_key_submenu`, `_overlay_submenu`, and the now-defunct
`self.submenu = None` reset in `_run_wt_action`. With every menu overlay now
native, the overlay-registry guard's routing proof moved to the still-manual
`cleanup` (scopedlg) overlay. The `pilot.press` harness drives it end-to-end
(open on Enter, Space toggles No-mux, arrow + Enter runs a verb, Esc/q/Tab
cancel).

**Seventh migration -- the Clean/Sync + New-worktree scope dialog (`scopedlg`) →
native `ModalScreen`, and a *redesign* (F4).** The `ScopeDlgScreen(ModalScreen
[bool])` replaces the manual `scopedlg` overlay that both `cleanup` (Clean/Sync)
and `optmenu` (New-worktree options) shared. It renders the option toggles, a
`[Confirm] [Cancel]` button row, and -- for Clean/Sync -- a **read-only impact
list** naming exactly which worktrees the current toggle selection will act on
(the count also rides the Confirm label). The option toggles are mutated in place
on the passed `dlg` dict; the screen returns `dismiss(True|False)` and the opener's
callback runs `_confirm_cleanup(dlg)` / `_confirm_new_worktree(dlg)` on confirm.
This slice is more than a port: the old dialog was a **live filter** that dimmed
the main worktree list in place, docked its toggles at the bottom, and carried a
third focus section for **per-row exclusion** (#2179). That model existed because
the dialog's bucket toggles *were* the scope mechanism; now that scope comes from
the **main-list selection before the dialog opens**, the dialog is a normal
centered modal whose nested impact list is *more* legible (self-contained -- it
advances `visions/picker` §Features/decision-support-before-cost and
consequential-vs-browsing-clarity). Retired with the live-filter model:
`self.cleanup`/`self.optmenu`, `_key_scopedlg`, `_overlay_scopedlg`,
`_enter_cleanup_list`, `_key_cleanup_list`, `_toggle_cleanup_exclude`,
`_cleanup_raw_union`, per-row exclusion (`excluded`), the main-list preview
dimming, the `dock_bottom` render path, and the footer branch; `_cleanup_union`
became `_scope_union(dlg)` (plain bucket union). With `cleanup`/`optmenu` gone the
overlay registry holds only the two remaining live overlays -- `progress` and
`msgview` -- and the overlay-registry guard's routing proof now uses `msgview`.
The `pilot.press` harness drives the modal end-to-end (open, Space toggles a
bucket and the impact list/count narrow live, Tab to Confirm runs the scoped
maintenance progress; Esc cancels).

**Eighth migration -- the live maintenance/profiles progress run (`progress`) →
native `ModalScreen` (F4).** The `ProgressScreen(ModalScreen[None])` replaces the
manual `progress` overlay -- the last *live* one. Unlike the other (static)
migrated overlays it ticks: an `on_mount` interval advances the run (the mock
walker, or a real `MaintenanceExecutor` poll) and repaints, so the background
`_tick` no longer drives it (that would double-step the walker). The run's state
stays on the engine (`self.progress` / `self.executor`) because several entry
points build it -- `_confirm_cleanup` (Clean/Sync), `_run_op_progress` (Stop /
Reclaim / Finalize) and `_start_profiles_run` (Apply) -- and its
state-transition core (`_advance_progress` / `_key_progress`) stays unit-tested
there; each entry point now ends with `_open_progress()`, which
`push_screen`s the `ProgressScreen`. The screen delegates keys to
`_key_progress` (mirroring the old handler exactly -- an unarmed beyond-clean run
shows a confirm gate where Enter proceeds/arms and Esc cancels; a done run closes
on Enter/Esc) and dismisses itself once the engine clears `progress`. With
`progress` gone the overlay registry holds only `msgview`; the overlay-registry
guard's precedence proof (two overlays needed) is skipped while the table is down
to one, and its routing proof still exercises `msgview`. The `pilot.press`
harness drives the modal end-to-end (a gated run's Esc cancels without executing;
an armed run advances to done and Enter closes it).

**Ninth migration -- the recent-messages viewer (`msgview`) → native
`ModalScreen`, and the registry becomes an empty vestige (F4).** The
`MsgViewScreen(ModalScreen[None])` replaces the manual `msgview` overlay -- the
**last** one. Like `ProgressScreen` it is live: the payload loads on a daemon
thread (`_msgview_worker`) that populates the engine-owned `self.msgview` dict
under `_msgview_lock` (a late result for a closed/reopened viewer is dropped by
`rec` identity), and an `on_mount` interval repaints while `loading` -- plus once
more on the loading→loaded transition -- so the result appears without churning a
settled viewer. `_open_msgview` builds the dict, starts the thread, then
`push_screen`s the `MsgViewScreen`; the screen delegates keys to `_key_msgview`
(↑/↓ scroll, Esc/q/Tab/Enter close, mirroring it exactly) and dismisses itself
once the engine clears `msgview`. **With every overlay now native, the manual
overlay registry became empty** -- and a follow-up slice then **retired it**
(next paragraph). The `pilot.press` harness drives the viewer end-to-end (Enter
on *Messages* opens it, the loader thread resolves, Esc closes).

**Tenth slice -- retire the now-empty manual overlay seam (F4 cleanup).** With
all nine overlays native, `_overlay_registry()` returned `[]` and
`_active_overlay()` was always `None` -- pure dead weight. Both methods are now
deleted, along with their three consumers' vestigial branches: `render` drops the
dim/`ov[2]` blit block (Textual's screen stack draws + dims modals above the
widget), `_dispatch_key` drops its `_active_overlay()` route (it only ever runs
for the top-level views now, since a native modal above `PickerScreen` consumes
keys itself), and `on_key`'s binding gate simplifies from
`key in BINDING_KEYS and self._active_overlay() is None` to just
`key in BINDING_KEYS`. The dead manual-panel helpers `_prow` / `_blit_panel`
(used only by the deleted `_overlay_*` renderers) go too. The guard test was
rewritten as `test_manual_overlay_seam_is_retired`: asserts the seam methods no
longer exist and that a nav key drives the main-view selection directly (the F3
keyboard guards already cover binding bubble + overlay swallowing). Behaviour is
unchanged -- the overlay precedence the registry once enforced is now enforced by
Textual's screen stack.

**Global shortcuts are Textual `BINDINGS` (F3).** The truly-global pivot/machine
shortcuts (`ctrl+shift+left/right`, `ctrl+left/right`) are owned by the
framework's binding system, not the manual dispatcher: `PickerScreen.BINDINGS`
maps them to `action_pivot_*` / `action_machine_*`, and `on_key` lets those keys
**bubble** to the binding system (returns without `event.stop()`) -- it now does
so unconditionally, because a native modal on the screen stack consumes keys
itself, so `on_key` only ever runs for the top-level views (the old
`_active_overlay()` guard is gone). Key names are folded through `canonical_key`
(F2) first. A real-framework keyboard harness (`pilot.press`) validates the whole
path end-to-end -- the binding fires the rotation, and is correctly suppressed
while a modal owns the keyboard (Textual's screen stack, not a manual gate).

### Body componentization (F5) -- carving sub-views out of `build_body`

With every dialog native (F4) and the manual overlay seam retired, F5 tackles the
picker *body*. The original F5 framing -- convert the `sel=(zone,index)` +
`stops()` focus model to focusable widgets -- turned out to be a rewrite trap, not
an incremental slice: the picker paints its whole body as **one monolithic
`render()` / `build_body()` Rich-`Text` blob** with `sel == ("ZONE", i)` compared
inline for every tab/button/row (87 engine refs, 121 test couplings, a golden
snapshot). Textual focus is container-level, so the body can't be flipped to
widgets region-by-region -- there's no per-region seam the way F4 had the overlay
registry.

So F5 is **incremental componentization under a moratorium on new
full-screen-at-once renders**: peel cohesive sub-views off the God-object one at a
time, each into a component that owns its own render (and, over successive slices,
its state + focus), until the shared `sel`/`stops` model shrinks to just the
chrome -- at which point converting *that* to widgets is a small, bounded final
step.

**First component -- the Profiles configurator (`ProfilesView`).** The Profiles
pivot body (the host×target grid + its narrow-terminal transposed fallback) is the
most self-contained sub-view (its confirm dialog was already a native
`ProfConfirmScreen`), so it goes first. `PickerScreen.build_body`'s profiles branch
now calls `self.profiles_view.build(add, width, sel)` on a `ProfilesView` component
(instantiated first in `__init__`) instead of the inline `_build_profiles` /
`_build_profiles_transposed` (both deleted). Slice 1 moved the two body-render
*entry* methods behind the component boundary; slice 2 moved every Profiles render
*helper* too (column widths, the visible-column window, host-header / target-label
/ grid-cell visuals, the Apply/Reset button row, the legend); **slice 3 moved the
grid-editing *model* -- the state (`grid` / `applied` / `pcol` / `targets` /
`host_cols` / `_prof_unavailable`) and the pure grid behaviour (`grid_dirty` /
`pending_count` / `cell_locked` / `profiles_present` / `_column_sels` /
`toggle_cell`) -- onto the component.** `PickerScreen` keeps every existing call
site (`setup`, `_dispatch_key`, the Apply/progress plumbing) and the test suite
working via thin **shims**: `@property` pass-throughs for the state fields and
one-line delegating methods for the behaviour (so `self.grid` / `self.pcol` /
`self._toggle_cell()` still resolve, now against `self.profiles_view`). **Slice 4
moved the last piece -- the Apply/load *plumbing*** (`start_load` + the background
per-host column loader, `apply` → the `ProfConfirmScreen` confirm, `_start_run`,
`_make_apply_task`, `commit_applied`, `_target_sel`, and the `_prof_load` /
`_prof_apply` / `_prof_loading` / `_prof_loaded` handles) onto the component. The
apply flow reaches back through `self._eng` only for the genuinely engine-level
*shared* infrastructure it drives -- `mock_mode`, the status line, the screen
stack (`ProfConfirmScreen`), and the maintenance `executor` / `progress` /
`_open_progress` that every maintenance op uses (the same `ProgressScreen`). The
engine keeps thin shims (`_apply_profiles` → `profiles_view.apply()`,
`_prof_*` `@property`s) so `_activate`, `setup`, `_key_progress`'s profiles-commit
branch, and the test suite address them unchanged. **`ProfilesView` is now a fully
self-contained Profiles configurator** -- rendering, grid state, behaviour, and
IO -- ready to become a focusable Textual widget in a later slice. Behaviour is
unchanged -- the same VRows with the same `("PR", i)` / `("BTN", 0)` stops are
emitted; a guard test (`test_profiles_view_component_renders_body`) asserts the
component renders the body, owns the state (`scr.grid is scr.profiles_view.grid`,
`grid` is not a plain engine attribute) and the plumbing (`apply` / `start_load` /
`commit_applied` live on the component; `_prof_load` is not an engine attribute).

**Second component -- the Maintenance pivot body (`MaintenanceView`).** With the
Profiles pattern proven, the Maintenance pivot is carved out next. **Slice 5a moves
the *rendering***: `PickerScreen.build_body`'s maintenance branch now calls
`self.maintenance_view.build(add, width, sel)` on a `MaintenanceView` component
(instantiated in `__init__` right after `profiles_view`) instead of the inline
branch, and the four Maintenance row helpers (`_maint_selectall_row` / `_maint_header`
/ `_maint_group_row` / `_maint_row`) move onto the component as `_selectall_row` /
`_header` / `_group_row` / `_row` (all deleted from `PickerScreen`). One small
enabling change: the `build_body` `add` closure gained an optional `new_section=`
argument so an extracted component can open a grouped section (pinning the section
label + the vrow index the row will occupy) without reaching into the closure's
`cur_section` / `vrows` -- exactly reproducing the inline
`cur_section = (label, len(vrows))` the branch used. **Slice 5b then moves the
selection *model*** -- the state (`maint_sel`) and the grouping / multi-select
*behaviour* (`maint_groups` / `maint_records` / `_maint_ids` / `_toggle_maint` /
`_toggle_maint_all` / `_toggle_group`) -- onto the component. `PickerScreen` keeps a
`maint_sel` `@property` shim (get+set, so `scr.maint_sel = ListSelection(...)` still
resolves) and one-line delegating methods, so its call sites (`_open_maint_menu`,
`_dispatch_key`, `_activate`, the executor poll) and the test suite address them
unchanged. The component reaches back through `self._eng` only for genuinely
engine-level shared infrastructure: the scoped `cleanup_rows` data layer (used by
non-Maintenance code too), the `_checkbox` glyph helper (shared with the Worktrees
multi-select gutter), and the status line (`debug`) -- a clean boundary, Maintenance
selection logic on the component, generic data-scoping + chrome on the engine.
Behaviour is unchanged -- the same VRows with the same `("SA", 0)` / `("GH", gi)` /
`("C", i)` stops and pinned group sections, and the same toggle/select-all/group
semantics; the guard test (`test_maintenance_view_component_renders_body`) asserts
the component renders the body, the inline row helpers are gone from the engine, and
the component owns the selection state (`scr.maint_sel is
scr.maintenance_view.maint_sel`; `maint_sel` is not a plain engine attribute).

**Third component -- the registered-pivot (Tasks) body (`TasksView`).** The
registered/Tasks pivot body is carved out next (#88 F5 slice 6). `build_body`'s
`registered` branch now calls `self.tasks_view.build(add, width, sel)` on a
`TasksView` component (instantiated in `__init__` after `maintenance_view`), and the
two Tasks row helpers (`_task_status_row` for the load/count/empty header line,
`_task_row` for one task entry) move onto the component as `_status_row` / `_row`
(both deleted from `PickerScreen`). Unlike Profiles/Maintenance there is **no
editable state to move**: the registered pivot is read-only, its task list
background-loaded by a `RegisteredPivotRuntime`, and its "selection" is just the
shared `sel` cursor. So the data helpers (`_task_state` / `_task_rows` /
`_task_groups`) and the pivot-scoping context (`_reg_pivot` / `_pivot_machine` /
`_pivot_machine_id` / `_pivot_runtime`) stay on the engine -- they are shared with
`stops` / `region_heads`, the internal-navigation dispatch, and the task action
sub-menu -- and the component reads them via `self._eng`. Group sections are opened
with the same `add(new_section=...)` mechanism. Behaviour is unchanged -- the same
VRows with the same `("T", i)` task stops and pinned worktree-group sections; a guard
test (`test_tasks_view_component_renders_body`) asserts the component renders the
body, the inline row helpers are gone from the engine, and `build_body` routes the
Tasks body through the component with its group sections pinned and the task titles
rendered.

**Fourth (and last) component -- the Worktrees list body (`WorktreesView`).** The
picker's **primary** body -- the machine's worktree list -- is componentized last
(#88 F5 slice 7), once the pattern was proven three times over. `build_body`'s
`worktrees` branch now calls `self.worktrees_view.build(add, width, sel)` on a
`WorktreesView` component (instantiated in `__init__` after `tasks_view`). It's the
largest and most-coupled body: the New-worktree button row, the Active / Recent /
Completed / Unowned section grouping, the multi-select checkbox gutter (glyph shown
only in multi-select mode, the 2-cell gutter always reserved so the table never
shifts), the Clean/Sync focus preview dimming, the layered focus + selection
highlight (green-invert = focused *and* selected, plain invert = focused, grey bg =
selected-but-cursor-moved), and the decorative live worktree-status-core pulse
sub-lines. Like Tasks, this slice moves the **rendering** only: the multi-select
*state* (`wt_sel` / `wt_anchor`) and its range/toggle behaviour stay on the engine
because they thread deeply through the shared key-dispatch + focus machinery
(`_dispatch_key`, `_reconcile_wt_sel`, range-select, focus tracking) -- which is
exactly the `sel`/`stops` chrome the *final* native-focus step addresses -- so the
component reads them (and the list data `current_list` / `list_records`, the
predicates `_wt_multiselect_active` / `_cleanable`, the shared `_checkbox` glyph, and
the chrome rows `tab_bar` / `new_worktree_row` / `active_button`) via `self._eng`.
Behaviour is byte-for-byte unchanged -- the golden snapshot
`worktree-manager/tests/production_picker/goldens/picker/worktrees_list.txt`
matches without modification, and a guard
test (`test_worktrees_view_component_renders_body`) asserts the component renders the
body with the `("BTN", 0)` button row + one `("L", i)` stop per worktree and pinned
group sections.

**Body componentization complete (slices 1-7).** All four pivot bodies are now
self-contained components -- `ProfilesView` (fully: render + state + behaviour + IO),
`MaintenanceView` (render + selection state/behaviour), `TasksView` (render;
read-only, no state), and `WorktreesView` (render; multi-select state kept on the
engine). `build_body` is now just its `add` scaffold plus a four-way `_kind()`
dispatch to `self.<view>.build(add, width, sel)` -- the monolithic
render-everything-at-once blob is gone, and the moratorium held. What remains on the
shared `sel`/`stops` model is the **chrome** (the top pivot tabs / machine row /
button row) and the multi-select state that rides the key-dispatch machinery. Turning
*that* residue into native Textual focus -- making each `build` a focusable widget --
is the small final step, and may still be deferred as a judgement call per the
vision's stability bias.

### Native focus (NF) -- leveraging the framework's focus system

With the overlays native (F4) and the bodies componentized (F5), the picker's
last hand-rolled subsystem is **focus itself**: `PickerApp` composes a single
`PickerScreen(Widget)` that blits the whole screen in one `render()` and drives
focus manually (`sel=(zone,index)` + `stops()` + `on_key` -> `_dispatch_key`).
NF migrates that toward Textual's **native focus** -- real focusable widgets the
framework moves focus between and styles (`:focus`) -- rather than an immediate-mode
monolith. This is the deliberate application of a standing goal: *leverage the
native capabilities of the chosen framework* instead of reimplementing them. Like
F4/F5 it is a **sequence of independently-shippable, behaviour-preserving slices**,
never a big-bang; the main-screen `sel`/`stops` model (87 engine refs, ~93 test
couplings, the capture/golden pipeline) is migrated last, incrementally.

**NF1 -- native focus *inside* the modals (the beachhead).** The F4
`ModalScreen`s made the *overlays* native, but each still navigated its own
**internals** by hand: a `self.idx` cursor, an `on_key` if/elif, and a re-rendered
static `Panel`. NF1 replaces those internals with real focusable Textual widgets --
the natural first slice, fully isolated from the main-screen focus model. **First
slice:** the two simple index-menu modals, `CfgMenuScreen` (⚙ Configuration) and
`MaintMenuScreen` (Maintenance actions), now compose a native
[`OptionList`](https://textual.textualize.io/widgets/option_list/): the framework
owns focus, up/down movement, and Enter-to-select, and the menu returns its choice
via `on_option_list_option_selected` -> `dismiss(event.option_index)`. The manual
`idx`/`on_key`/`_panel`/`_refresh` are gone; the title/hint ride the widget border
(Cfg) or a description pane that tracks the highlighted action via
`on_option_list_option_highlighted` (Maint); Esc/q cancel through `BINDINGS`
actions. Modest, cleaner look; behaviour-preserving -- every existing menu test
(which drives `pilot.press("down"/"enter")` and reads `menu._items`/`_actions`)
passes unchanged, and a guard test (`test_index_menus_use_native_optionlist`)
asserts each modal composes a focused `OptionList` whose option count mirrors the
menu items. Later NF1 slices give the remaining modals (the confirms -> `Button`s,
the other menus -> `OptionList`) the same treatment; NF2+ then tackle the main
screen (compose skeleton -> chrome widgets -> body list widgets -> retire
`sel`/`stops`).

**NF1 slice 2 -- `TaskMenuScreen` -> native `OptionList`.** The registered-pivot
task action sub-menu gets the same single-select treatment as Cfg/Maint: a native
`OptionList` (framework-owned focus + up/down + Enter) below a task-title/subtitle
header and above a description pane that tracks the highlighted action via
`OptionHighlighted`; `dismiss(event.option_index)` returns the choice, Esc/q cancel
via `BINDINGS`. The hand-rolled `idx`/`on_key`/`_panel`/`_refresh` are gone.
Behaviour-preserving (the real-pipeline task-menu tests drive `pilot.press` and read
`menu._actions` unchanged; the test now also asserts the composed `OptionList` is
focused).

**A guiding rule for the rest of NF (the tab-group principle).** Each interactive
*group* becomes **one** native container widget = **one tab-stop**, with the arrow
keys moving *within* it -- never one tab-stop per item. Textual's list widgets are
each a tab-group by construction: `OptionList` (single-select menus), `SelectionList`
(multi-select **checkbox lists** -- the New-worktree options `Bare`/`No Mux`/`Local
model` and the Clean/Sync scope buckets, both hand-rolled in `ScopeDlgScreen`
today), and `RadioSet` (pick-exactly-one). Only a *heterogeneous* group the native
lists don't cover (a `Confirm`/`Cancel` button pair; later, the main-screen chrome)
needs a small reusable **`FocusGroup`** primitive -- a focusable container whose
children are not individually focusable, arrow moves an internal highlight, Enter
activates -- added when the first such case lands, not speculatively.

**NF1 addendum -- modal A/B capture + palette consistency.** Native widgets adopt
Textual's default theme tokens unless told otherwise, and the first ones did:
`OptionList`'s default `background` is `$panel` (a bluer grey than the picker's
`$surface` base) and its cursor uses `$primary` (blue) -- so the converted menus
read as "dark blue", inconsistent with the rest of the picker. Fixed by pinning the
modal widgets to the picker palette: `background: $surface` (matching the main
screen behind the scrim) and an orange highlight
(`.option-list--option-highlighted { background: #ffaf00; color: black }`, echoing
`C_BTN_SEL`). To *catch* this class of regression -- and to A/B a modal's look
across the migration -- two things landed: (1) a `capture.capture_modal(source,
opener)` seam that exports the **composited app** (picker + the open `ModalScreen`)
as an SVG via Textual's app-level screenshot, since a native modal lives on the
screen stack and is invisible to the `PickerScreen.render()` capture seams (this
also advances vision item A -- modals become auditable/shareable); and (2) an
automated guard asserting each native modal's list `background` resolves to the same
`Color` as the base picker screen (i.e. `$surface`, not the bluer `$panel`). The
before/after for any slice is a `capture_modal` at `HEAD` vs the pre-NF commit --
Git is the "fork", so no old-vs-new component duplication is kept in the tree.

**NF1 addendum 2 -- native-widget polish to match the picker's craft.** An A/B
screenshot pass (rasterized from `capture_modal` SVGs) surfaced two more
regressions the palette fix alone didn't cover: the native menus were vertically
*cramped* (no breathing room) and used a *loud* full-width solid-orange selection
bar, versus the old menus' subtle grey. Fixed by adopting the picker's own idiom:
the highlighted option uses `text-style: bold reverse` (a subtle reverse bar, like
the worktree list's focused row) rather than a saturated block, `padding: 1 2`
restores the breathing room, and the key-hint moved from an orange
`border_subtitle` to a **muted-grey line inside** the frame (so `CfgMenuScreen` now
shares the framed `Vertical` + inside-hint shape of `Maint`/`Task`). Net: the native
`OptionList` menus now read like the hand-drawn `Panel`s they replaced -- same
craft, native focus. (The A/B rasterizer detail that bit twice: Rich lays out the
SVG by placing every glyph at an x-position computed from **Fira Code**'s metrics
and names it via a CDN `@font-face`. An offline rasterizer must supply the *actual*
Fira Code font file -- substituting another monospace (e.g. Consolas) renders the
text but leaves the **box-drawing borders choppy**, because its glyph advance
doesn't match Rich's cell grid; and letting it fall back to a proportional serif
misrepresents the layout entirely. The picker and the `capture` SVG are unaffected
either way -- a real terminal always paints on its own cell grid. This flow is
codified as a reusable tool -- `scripts/picker-snapshot/` (capture -> SVG ->
`svg2png.mjs` with Fira Code @ 3x) -- so demos/A-B renders are reproducible and the
choppy-substitute mistake isn't re-derivable.)

**NF1 addendum 3 -- prominent focus highlight (operator soak feedback).**
Addendum 2's move to a *subtle* `text-style: bold reverse` bar was the wrong call:
an operator soak of the toggle-ON picker found the focused option in the action
dialogs *too subtle to read at a glance* -- and `TaskMenuScreen` (slice 2) had never
actually adopted the reverse idiom, so the modals were already inconsistent (amber in
Task, faint-reverse everywhere else). Reversed here: every native menu's focused
option now uses the **prominent amber bar** (`.option-list--option-highlighted {
background: #ffaf00; color: black; text-style: bold }`, echoing `C_BTN_SEL` and the
`+ New worktree...` accent), standardized across `SubMenuScreen`, `CfgMenuScreen`,
`MaintMenuScreen`, and `ScopeDlgScreen` to match `TaskMenuScreen`. For
`ScopeDlgScreen`'s `SelectionList`, the checkbox-gutter highlighted states
(`selection-list--button-highlighted` / `--button-selected-highlighted`) are pinned
to the same amber so the highlighted row renders as a single uniform bar rather than
a surface-coloured patch inside the amber. Advances `visions/picker` Behaviors
(keyboard-first -- "focus always visually clear"); A/B-confirmed via `capture_modal`
before/after on `submenu`/`cfg`/`maint`/`clean`. The subtle-vs-loud tension is a
genuine judgement call; live operator feedback on real data is the tie-breaker.

**NF1 slice 3 -- the checkbox dialogs -> `SelectionList` + the `FocusGroup`
primitive.** `ScopeDlgScreen` (shared by Clean/Sync scope and the New-worktree
options `Bare`/`No Mux`/`Local model`/`Anchor repo`) was the picker's genuine
**multi-select checkbox list**, hand-rolled as a `section`/`idx`/`bidx` cursor over
a static `Panel`. It now composes a native Textual **`SelectionList`** for the
toggles (one tab-stop; arrow to move, Space to toggle -- the framework owns focus +
checkbox state) plus the migration's first reusable **`FocusGroup`** for the
`[Confirm] [Cancel]` row. `FocusGroup` is the native answer to the *tab-group*
requirement: a single focusable tab-stop whose children are **not** individually
focusable -- arrow moves an internal highlight, Enter/Space activates (posting a
`FocusGroup.Activated` message) -- for the heterogeneous rows the list widgets
don't cover. Tab moves between the two, giving the old two-section model natively.
The `dlg["opts"][i]["on"]` toggles are mirrored back from the `SelectionList`
selection on every `SelectedChanged`, so `_union()`, the live impact list, and the
callers (`_confirm_new_worktree` / `_confirm_cleanup`) read the confirmed selection
unchanged. A guard (`test_scope_dialog_uses_native_selectionlist_and_focusgroup`)
asserts the native widgets + palette; the two tests that drove the old
`section`/`idx` model were updated to the native flow (Tab between widgets, focus
assertions).

Three `FocusGroup`/`SelectionList` implementation gotchas the A/B render caught that
the unit tests did **not** (all four are invisible to behaviour tests -- the value
of the `capture_modal` A/B habit): (1) a bare `Widget.render()` returning a `Text`
has **no content-width measurement**, so Textual clips the row to a few columns and
the second button vanished -- fixed by composing a child `Static` (which measures
its content); (2) `height: 1` on the `FocusGroup` clipped that child (it laid out
one row *below* the parent's single-row region) -- fixed with `height: auto` so it
sizes to the child; (3) `has_focus` on the container read unreliably at
screenshot time, so the focused button lost its highlight -- fixed with an explicit
`_focused` flag toggled in `on_focus`/`on_blur`; and (4) `SelectionList`'s checkbox
inherits Textual's `$panel`/`$primary` (blue) tokens -- repinned via the
`selection-list--button*` component classes to a dim grey for unchecked and green
for checked (the picker's ☐/☑ idiom), on `$surface`. Same `$surface` +
reverse-highlight + framed-layout craft as the menu slices.

**NF1 slice 4 -- `SubMenuScreen` (the per-worktree action menu) -> native
`OptionList` + a native `Checkbox` for No-mux.** The trickiest NF1 modal: a
single-select verb menu (Open/Resume, Messages, Sync, Cleanup, Finalize, Stop,
Reclaim, Jump to host/caller, plus contributed actions) *and* a boolean modifier
(No-mux) that previously rode on the Open verb -- a hand-rolled `idx` + `on_key`
over a static `Panel`, where **Space toggled No-mux only while Open was
highlighted** and the label mutated to "Open · no-mux". It now composes a native
Textual **`OptionList`** for the verbs (the framework owns focus, up/down, and
Enter-to-select; a description pane tracks the highlight via `OptionHighlighted`)
inside the same orange-framed `Vertical` as the other menus, above a description
pane, with the header (title + meta + session id) on top. Per an explicit operator
steer -- *"remove any specific keyboarding requirement for no-mux"* -- No-mux
became a native toggle control. Its **first** form was a separate `Checkbox`,
which shipped a usability bug: the checkbox was only reachable by **Tab**, and the
operator (naturally arrow-driving the menu) could not reach it. The **fix** folds
No-mux into the OptionList itself as an arrow-reachable **toggle row** at the
bottom of the verb list (`☐ No Mux` / `☑ No Mux`, shown only when *Open* is
offered, since it modifies only Open): ↓ onto the row, Enter or Space flips it and
stays open; Enter on a verb dismisses with `(verb, no_mux)`. No Tab, no separate
widget -- pure arrow navigation. `no_mux` is plain screen state (`_no_mux`), the
toggle row's prompt is swapped via `replace_option_prompt_at_index`, and a
`space` screen binding toggles it only while that row is highlighted (a stray
Space on a verb is inert). `dismiss((verb, no_mux))` and every caller (`_after`,
`_resume_decision`) are unchanged; `_open_submenu` and the ~20 tests reading
`menu._actions` / `menu.no_mux` still hold (the toggle test drives ↓ + Space, no
Tab). The glyph uses the picker's dim-grey (unchecked) / green (checked) idiom,
matching the `SelectionList` treatment.


A palette gotcha this slice surfaced (caught by the A/B render, invisible to the
all-green behaviour tests): Textual's `OptionList` applies `&:focus {
background-tint: $foreground 5% }` to the *whole* focused list, and its
`OptionList:focus > .option-list--option-highlighted` rule (higher specificity than
a plain `.option-list--option-highlighted` override) repaints the highlighted row
with `$block-cursor-background`. Net effect on a multi-option focused menu: the
selected row looked **recessed** while the *non*-selected rows carried a distracting
grey band -- an inverted focus affordance. Fixed by (a) neutralising the tint with
`OptionList:focus { background-tint: $surface 0% }` and (b) adding the `:focus >
.option-list--option-highlighted` variant to the highlight override so the picker's
reverse-bar wins. The same two lines were applied to `CfgMenuScreen`,
`MaintMenuScreen`, and `TaskMenuScreen` (identical idiom, same Textual default) so
every native menu renders its focused option consistently whenever it lists 2+
options. `render.py` gained `--modal submenu` (and `--modal maint`) openers for the
standing A/B habit.

**NF1 slice 5 -- the confirm modals (`QuitConfirmScreen` / `ProfConfirmScreen`) ->
the `FocusGroup` primitive.** The two remaining hand-rolled modals -- both an
`idx` + `on_key` (Quit) / `on_key` (Prof) over a static `Panel` -- convert their
button rows to the reusable `FocusGroup` introduced in slice 3. `QuitConfirmScreen`
composes the prompt + a `[Quit] [Stay]` FocusGroup (`initial=1`, so *Stay* is the
safe default a reflexive Enter can't override) + a muted key-hint, all in the
orange-framed `Vertical`; y / n / Esc / q remain screen `BINDINGS` so they fire
regardless of child focus. `ProfConfirmScreen` keeps its verbatim add/remove diff
body (now a `Static` via `_diff_body()`) and destructive-change warning, and swaps
its Apply/Cancel row for a `[Apply] [Cancel]` FocusGroup (`initial=0`); Esc/q cancel
via `BINDINGS`. Both return their verdict from `on_focus_group_activated` ->
`dismiss(bool)`; `_cf`/`_host_cols` and every caller/test contract are unchanged
(`test_escape_on_main_view_confirms_before_quit`, the profiles-apply confirm/cancel
tests). That completes NF1 -- **all nine picker modals now navigate via native
Textual widgets** (`OptionList` single-select, `SelectionList` multi-select,
`FocusGroup` heterogeneous rows), one tab-stop per group, arrow-within, no
hand-rolled `idx`/`on_key`/`_panel`.

Two gotchas this slice surfaced (both caught by the A/B render, invisible to the
green tests): (1) a `FocusGroup` composes its own child `Static`, so at *screen*
`on_mount` the grandchild may not be mounted yet and `query_one("#…buttons",
FocusGroup).focus()` raised `NoMatches` -- fixed by deferring the focus with
`call_after_refresh` (ScopeDlg dodged this only because its default path focuses the
`SelectionList`, not the FocusGroup); (2) a `width: auto` framed `Vertical` with a
border collapsed to an empty box -- the confirm frames need a **fixed** width like
every other menu (`QuitConfirmScreen` -> `width: 48`; `ProfConfirmScreen` keeps
`width: 72`). `render.py` gained `--modal quit` and `--modal prof` openers (the
latter pushes the confirm directly with a synthetic add/remove diff, avoiding the
whole profiles-grid drive).

#### NF2 -- the main-screen compose skeleton

With NF1 done (every modal native), NF2 begins migrating the **main screen** off
its single `render()` leaf toward a Textual container composing child leaf
widgets. This is the highest-risk stretch: 93 tests `query_one(PickerScreen)` and
drive its manual `sel`/`on_key` model, the `capture` module and the golden
(`worktree-manager/tests/production_picker/goldens/picker/worktrees_list.txt`)
ride `render()`, so the monolith is
retired **last**, incrementally, keeping the golden byte-identical at every step.

**NF2 slice 1 -- the segment seam + the `AGENT_WORKTREES_PICKER_NF` live toggle.**
`render()` was refactored to build its output from a single
`_frame_segments()` producing the four screen segments -- **header** (title +
htabs), **chrome** (scroll-border + stats), **body** (the windowed
`build_body` rows), **footer** (scroll-border + footer line) -- as lists of `Text`
rows, which `render()` flattens in order via a shared `_join_lines()`. This is a
pure refactor: the monolith output is byte-identical (golden unchanged). Behind the
`AGENT_WORKTREES_PICKER_NF` env toggle (default OFF), `PickerScreen.compose()` now
yields four leaf `_PickerSegment` widgets (`#nf-header/#nf-chrome/#nf-body/#nf-footer`,
laid out `height: 2 / 2 / 1fr / 2`), each rendering its named slice of
`_frame_segments()`. Because the body's `body_h` derives from the screen height
(`H - len(header) - 4`) and the body widget's `1fr` slot resolves to the same
`H - 6`, the composed tree is pixel-identical to the monolith -- verified by a live
`export_screenshot` and by `test_nf_compose_skeleton_mounts_identical_segments`,
which asserts each segment's `render()` equals its slice and that the flattened
segments equal the whole-screen `render()`.

The toggle is **default OFF**, so `compose()` yields nothing -> `PickerScreen` stays
a render-leaf and `render()`, the `capture` seams, the golden, and all 93 couplings
are untouched (`test_nf_compose_skeleton_disabled_by_default`). A `refresh()`
override propagates state changes to the segment widgets when the skeleton is live
(a no-op when disabled). The segments are **not focusable** -- NF2 is the *visual*
compose skeleton only; region/row focus stays on the manual model until NF3/NF4
migrate the chrome regions and body rows to real focusable widgets, and NF5 retires
`sel`/`stops`/`_dispatch_key`.

**NF3 slice 1 -- chrome region decomposition (header -> title + pivots).** NF3's
job is to turn the chrome regions into real focusable widgets with native Tab
between them. Its first step is *decomposition*: the chrome regions (pivot tabs,
machine scope, buttons) are `sel`-coupled and rendered across `topbar()` and
`build_body()`, so they don't map 1:1 to NF2's four segments -- making them
focusable together needs each region to be its own widget first. This slice splits
the **header** segment into its two conceptual region rows: `#nf-title` (the
identity row -- "Worktree Manager", version, update indicator, host) and `#nf-pivots`
(the WORKTREES/Tasks/Bridges pivot tabs + the ⚙ Configuration entry). `_PickerSegment`
gained an optional `rows` slice so one segment can back several row-widgets; the two
render header rows `0:1` and `1:2` and recompose the whole header byte-identically
(`test_nf_compose_skeleton_mounts_identical_segments`). Still toggle-gated, still
non-focusable -- the pivots are now their *own* region widget, the granularity the
focusable-chrome slices (extracting the machine-scope + button regions from
`build_body`, then wiring native Tab across all chrome regions) build on next.

**NF3 slice 2 -- untangling the body's chrome from its data (source split).**
The machine-scope (`M`) and button (`BTN`) regions are emitted *interleaved* with
the scrolling data inside each pivot's view component (`WorktreesView.build`,
`MaintenanceView.build`, `TasksView.build`), which is exactly what blocks rendering
them as separate fixed/focusable widgets. This slice untangles that at the source:
each view's `build()` splits into `build_chrome()` (the leading machine-scope row +
top New/Clean/Sync button region) and `build_data()` (the column header + the
scrolling section/row list), with `build()` emitting both in order so the
monolithic `build_body()` -- and thus `render()` and the golden -- are byte-identical
(`ProfilesView`, which lives under Configuration with its own host-column axes and
no top chrome, gets a no-op `build_chrome` for a uniform interface). A new
`PickerScreen._build_body_split(width)` drives the two emitters into separate VRow
sinks and returns `(chrome_vrows, data_vrows)`; `test_build_body_split_recomposes_monolith`
asserts, for **every** pivot, that the concatenated rows equal `build_body()`
byte-for-byte. Nothing in the live render path changes yet -- this is the pure
decomposition the next slice needs to render the chrome fixed (and focusable) while
only the data scrolls. The monolith stays authoritative; the split is consumed by
the compose tree (behind the toggle) in the following slice.

**NF3 slice 3 -- the compose tree renders the chrome fixed, scrolls only the
data.** The slice-2 split is now wired into the compose tree: the single
`#nf-body` widget is replaced by `#nf-body-chrome` (a `_PickerBodyChrome`,
`height: auto`, rendering the fixed machine-scope + button rows) above
`#nf-body-data` (a `_PickerBodyData`, `height: 1fr`, windowing only the data
rows). The data widget scrolls independently via a data-relative offset
`_data_top` (distinct from the monolith's `top`): `_ensure_data_visible` scrolls
it to keep the selected data row in view, `_data_sticky` pins the current section
header, and `_data_lines` windows + pads to the widget's own height. Because the
body's height budget is the same either way, the composed chrome (N rows) + data
(`body_h - N` rows) is **byte-identical to the monolith at the top of an unscrolled
list** (`test_nf_compose_skeleton_mounts_identical_segments`, comparing chrome +
data against `frame["body"]`). When the list is long enough to scroll, the two
paths *intentionally* diverge: the monolith scrolls the chrome off with the data,
while the compose tree keeps the machine-scope + New/Clean/Sync buttons **fixed**
and scrolls only the rows beneath them (verified by a live `export_screenshot` --
chrome pinned, `— Recent` section header sticky, scroll arrows lit). That
divergence is the NF end-state and is fully contained to the opt-in toggle: with
`AGENT_WORKTREES_PICKER_NF` unset, `render()` and the golden are untouched. The two
body widgets are still `can_focus = False` -- the fixed chrome they establish is
what the focusable-region wiring (native Tab across machine-scope / buttons, `sel`
sync) lands on next.

**NF3 slice 4 -- the chrome + data regions become focusable widgets (the focus
bridge).** With the fixed-chrome layout in place, the region views become real
focusable widgets: `_PickerPivots` (zone `V`), `_PickerMachine` (`M`),
`_PickerButtons` (`BTN`), and `_PickerBodyData` (the data region), all subclassing
`_FocusRegion`. The body's `#nf-body-chrome` splits into `#nf-machine` +
`#nf-buttons` (via `_chrome_split`, at the button row) so the machine-scope and
button regions are independently focusable. `_FocusRegion` is the **bridge** that
lets native Tab move between regions while the manual dispatcher still owns
navigation (retired only at NF5):

* `on_focus` -> point `sel` at this region's head (unless it already sits here);
* `on_key` -> forward every key to `_dispatch_key` (mirroring `PickerScreen.on_key`
  exactly, incl. letting the global pivot/machine `BINDINGS` bubble and the `[`/`]`
  character shortcut), then `_sync_focus_to_sel` mirrors native focus back onto the
  region `sel` now names, and repaint. Consuming the key suppresses Textual's own
  Tab so region movement runs through `region_heads` unchanged.

`_sync_focus_to_sel` maps `sel[0]` -> region widget (`_ZONE_WIDGET`), and on mount
`_nf_initial_focus` places focus on the default `sel`'s region (guarded by
`_nf_mounted` so the framework's mount-time auto-focus can't stomp the default sel).
Net effect (verified live and by `test_nf_focus_bridge_tab_moves_between_regions`):
Tab cycles pivots -> machine -> buttons -> data and back, the focused region
carries the picker's own `sel`-driven highlight, and the footer hint tracks it --
all still contained to the `AGENT_WORKTREES_PICKER_NF` toggle (default OFF =
untouched monolith). NF5 will invert the bridge (native focus becomes the source of
truth and `sel`/`stops`/`_dispatch_key` retire), and NF4 makes the data rows
individually focusable within `_PickerBodyData`.

**NF4 (in progress) -- pointer parity for the focusable regions.** The first NF4
step leverages a native capability the hand-rolled `sel` model never had: the
mouse. `_FocusRegion.on_click` focuses the clicked region (Textual focuses a
focusable widget on press; `on_focus` then points `sel` at the region head).
`_PickerBodyData` overrides `on_click` to address a finer target -- it maps the
click's row offset onto the drawn window (`_data_stop_at`, which shares
`_data_window` with the renderer so a click hits exactly the row that was drawn)
and, when it lands on a real data row, points `sel` there and lets single-select
track focus (`_wt_track_focus`); a **double-click** activates the row (opens its
action -- the submenu for a worktree -- the pointer parallel to Enter). Scroll-wheel
over the data body moves the selection (`on_mouse_scroll_down`/`up` ->
`_dispatch_key`). Verified by `test_nf_pointer_click_selects_data_row` and
`test_nf_pointer_double_click_opens_row`. Still toggle-gated; the full native list
widget (rows as individual native options) and NF5's retirement of the manual model
remain.

**NF5 (the cutover, in progress) -- native the default.** NF5 makes the compose/
native path the shipping default and retires the byte-identical `render()` fallback.
A structural fact shapes it: the manual `sel`/`stops`/`_dispatch_key` navigation
model is **shared** by both toggle states (OFF `render()` and ON `compose()` both
read `sel` and route keys through `_dispatch_key`) -- it is *not* toggle-gated. So
"inverting the bridge / retiring `sel`" is entangled with the default-flip; there is
no byte-identical-safe way to invert a shared model. The chosen path (Strategy A) is
a **pragmatic cutover**: flip the default, keep `sel`/`stops`/`_dispatch_key` as the
*internal* nav model the native widgets drive (so the ~100 `sel`/`stops`-reading
tests keep passing -- no mass rewrite), retire the env toggle + `render()` *display*
fallback (keeping `render()` as the deterministic *capture* seam), then let native
widgets own keys and migrate tests as low-risk follow-up cleanup.

**NF5 slice 1 -- parity-harden the toggle-ON path.** Before flipping, the whole
suite was run with `AGENT_WORKTREES_PICKER_NF=1` forced on -- simulating the
post-flip world. The result was near-total parity: the only real gap was the
Maintenance pivot, where the "~N MiB" size counter (`status_text` -> `_size_mb`)
crashed on the test fixtures' **non-hex ids** (`_size_mb` did `int(id4, 16)`). The
compose/segment path renders that counter eagerly (on any id, during mount/refresh),
where the monolith tests had wrapped their `build_body` calls in a `_size_mb`-
neutralizing monkeypatch. Fixed by hardening `_size_mb` to fall back to a char-sum
for any non-hex id (the hex path -- real/demo worktree suffixes -- stays
byte-identical). Guarded by `test_size_mb_handles_non_hex_id` (unit) and
`test_nf_maintenance_pivot_renders_under_toggle` (end-to-end under the toggle). With
that fix, forced-ON is fully green except `test_nf_compose_skeleton_disabled_by_default`
(which asserts OFF-is-default and is rewritten by the flip itself).

**NF5 slice 2 -- flip the default to native.** `_nf_compose_enabled()` now defaults
**ON**: with `AGENT_WORKTREES_PICKER_NF` unset, `PickerScreen` composes its segment/
region widgets and native focus drives the picker. Setting the var to a falsey value
(`0`/`false`/`no`/`off`) forces the legacy monolithic `render()` path -- a temporary
**rollback escape hatch** kept until NF5-3 retires the monolith (mirroring the
picker's own `AGENT_WORKTREES_LEGACY_PICKER` rollback switch). Because "env unset ->
ON" is behaviourally identical to the forced-ON parity run from slice 1, the flip
lands exactly that validated world: the full suite is green in the default (now ON)
mode (1674 passed), and the opt-out (`=0`) still drives the golden/monolith path
green. `test_nf_compose_skeleton_disabled_by_default` is replaced by
`test_nf_compose_default_on_with_opt_out`, which asserts the default composes the
widget tree and each falsey opt-out value forces the render-leaf. The `sel`/`stops`/
`_dispatch_key` model remains as the internal navigation the native widgets drive, so
the ~100 `sel`/`stops`-reading tests keep passing unchanged.

**NF5 slice 3 -- retire the toggle; `render()` becomes the capture seam.** The env
toggle and the OFF fallback are removed: `_nf_compose_enabled()` is gone,
`PickerScreen._nf_enabled` is gone, `compose()` unconditionally yields the segment/
region tree, and the `on_mount` / `_refresh_nf_segments` / `_sync_focus_to_sel`
guards are dropped. **`render()` is deliberately NOT deleted** -- it is not dead
code. It is the picker's *deterministic reference renderer*, on which the whole
capture/audit stack rides: `capture.py`'s `screen_to_text` / `_ansi` / `_svg` -- and
thus the golden tests, the `picker-snapshot` A/B tool, and the picker vision's
`Features/auditable-testable-rendering` + `Behaviors/renderable-and-assertable-headless`
-- all call `scr.render()`. Deleting it would break capture and regress a stated
vision behaviour. So `render()` is *reframed*, not removed: no longer the **display**
path (the composed widgets are), but retained as the **capture** path. Display and
capture both derive from `_frame_segments` / `_build_body_split`, so they cannot
drift, and the golden still meaningfully guards that shared source. The `=0` opt-out
test is replaced by `test_nf_compose_is_the_sole_path` (the tree always composes; the
`_nf_enabled` attribute is gone). Full suite green (1674 passed). Next: **NF5-4**
migrates capture off `render()` so the monolith can be deleted for real.

**NF5 slice 4 -- capture from the compositor; delete `render()`.** Slice 3 kept
`render()` because the capture/audit stack rode on it. Slice 4 removes that last
dependency: capture now sources the character grid from **Textual's live compositor**
(the composed segment/region widget tree that actually paints), not a parallel
whole-screen render. `capture.py` reads `scr.screen._compositor.render_strips()`,
flattens the per-row strips into one newline-separated `Segment` stream, and feeds it
to the *same* recording Rich `Console` as before -- so `text` / `ansi` / `svg` are
byte-identical to the former `render()`-based seam (verified: ansi + svg identical,
text differs only in blank-line trailing spaces that the golden's `_normalize`
rstrips), and the `picker-snapshot` A/B tool is unaffected. Capturing what is
*actually displayed* is also strictly more correct than a second renderer. With
capture off `render()`, the last direct callers were six test sites (`str(scr.render())`
/ `scr.render().plain` text dumps); those move to `capture.screen_to_text(scr)`, and
**`PickerScreen.render()` is deleted**. The base `Widget.render()` blank layer sits
harmlessly behind the children (which fill the screen), so the display is unchanged --
proven by the golden staying byte-identical. `_frame_segments` / `_join_lines` /
`_build_body_split` remain (the composed widgets derive from them). Full suite green
(1686 passed). This completes the NF cutover: the native compose tree is the sole
display path *and* the sole render path; the monolith is gone; the picker vision's
auditable-rendering is preserved (now sourced from the real display). What remains is
purely optional: excising the internal `sel`/`stops`/`_dispatch_key` model (which
still works fine as the widgets' navigation backing) and migrating its ~100 tests --
deferred as low-value churn under Strategy A.

**NF5-5 -- swappable native `OptionList` data body (in progress, opt-in).** The
operator's call for the "native furniture": the composed widgets gave native focus/
Tab/click/scroll/modals, but the data body still *painted styled text lines* and
tracked the manual `sel` cursor. NF5-5 replaces it with a genuine native
`OptionList` -- built the same swap-behind-a-toggle way NF1-NF5 were, so it can be
soaked before it becomes the default.

- **Toggle.** `AGENT_WORKTREES_PICKER_NATIVE_LIST` (default OFF). `compose()` yields
  `_PickerNativeData` (an `OptionList` subclass) in place of the text-line
  `_PickerBodyData` for the `nf-body-data` region; default OFF keeps the text-line
  body authoritative (golden byte-identical, full suite green).
- **Options from the same rows.** `_PickerNativeData` builds its options from a new
  `_build_data_vrows(width, sel)` (data-only -- it renders no chrome, so it never
  drives `build_chrome`/`tab_bar` before `setup()`), passing a *sentinel* sel so the
  focus-cursor highlight is **not** baked into the row text (the native widget owns
  the cursor via `.option-list--option-highlighted`, styled amber to match the
  modals). `wt_sel` background + dimming stay baked. Non-selectable rows (column
  header, section headers, live-pulse sublines) become **disabled** options -- the
  native cursor skips them, exactly as the manual `stops` did.
- **Bridged to `sel` both ways.** `OptionHighlighted` mirrors the native cursor into
  `sel`; external `sel` changes mirror onto `.highlighted`. Options rebuild only when
  a coarse data **signature** changes (kind/machine/records/`wt_sel`/pulse), so plain
  cursor moves stay smooth and native. `on_key` lets `OptionList` own up/down/enter/
  home/end/page and routes everything else (Tab region cycle, `[`/`]`, machine/pivot
  shortcuts) through the manual model, so region navigation is unchanged. Enter/click
  -> `_activate()`.
- **Slice 1 status.** Mounts, renders every pivot's rows as native options, Tab in/
  out works, native up/down navigate and mirror into `sel`; validated by
  `test_native_list_body_mounts_and_navigates` (+ `_disabled_by_default`), full suite
  green (1690), A/B render clean. Known rough edges for the soak (later slices):
  section headers render as plain disabled rows (no sticky-pin yet), the multi-select
  gutter + shift-range and the live-pulse sublines are baked-text best-effort (not yet
  native), single-click activates (native `OptionList` select) rather than select-then-
  double-click, and up from the top row doesn't cross into chrome (Tab does). Once
  soaked, later slices port sections/multiselect/pulse and flip the default.

**NF5-5 slice 2 -- byte-identical grid parity + width fix.** A parity pass
(`test_native_list_grid_parity`) captures the home screen with the native list OFF
(text-line) and ON (OptionList) and asserts the normalized character grids are
**identical** -- and equal to the golden. It surfaced one real bug: the native
options were first built at the `size.width or 100` *fallback* (the screen wasn't
sized yet) and never rebuilt at the real width, so the full-width section rules were
100 cols vs 118. Fixed by adding `size.width` to the rebuild **signature** and
rebuilding `on_resize`. With that, native-ON and text-line-OFF render byte-for-byte
the same grid (the cursor style differs -- amber vs reverse -- but that is style, not
characters). The parity harness is the operator's requested guard: every later
native-list slice re-runs it, so the swap stays a true drop-in. Multi-select
(Space toggle, Shift+Down range) and activation (Enter -> submenu) work under the
native list unchanged -- they route through the manual model via `on_key`, and the
gutter renders in byte-identical parity (`test_native_list_multiselect_and_activation`,
`test_native_list_multiselect_grid_parity`). Parity holds across pivots -- the
Maintenance pivot (group sections + select-all) is byte-identical too
(`test_native_list_maintenance_grid_parity`). The scrolled-state divergence --
the text-line body pins the current **section header** at the top and a native
`OptionList` scrolls it away -- is **resolved** by `_PickerStickyHeader`: a 1-row
widget above the list (`display: none` until scrolled) that pins the current
section's header while its own row is off-screen, tracked from the OptionList's
`scroll_offset` on refresh/highlight (`test_native_list_sticky_header`). It is
hidden at the top, so the unscrolled grid parity is unchanged. The other deliberate
native behaviours to weigh at the flip: single-click **activates**
(native select) vs the text body's select-then-double-click, and arrow-up from the
top data row stays in the list (Tab reaches the chrome) rather than crossing up. Per-row **checkboxes are now always shown** (`WorktreesView.build_data` no longer hides the glyph until multi-select is active) -- with mouse support the box is a discoverable, clickable multi-select affordance at rest; the golden was regenerated and parity holds (both bodies share `build_data`). In the native list those checkboxes are also **clickable** (#88 NF5-5): a mouse press in the 2-cell gutter toggles that row's multi-select (`_on_mouse_down`) and suppresses the ensuing activation, while a click on the row body activates -- so single-click still opens a row, and the gutter is the mouse multi-select affordance (`test_native_list_checkbox_click_toggles`).

**NF5-5 flip -- native list is the default.** `_native_list_enabled()` now defaults
**ON**: `PickerScreen` composes `_PickerNativeData` (native focus/cursor/scroll/click,
sticky section header, clickable checkbox gutter) for the data region unless
`AGENT_WORKTREES_PICKER_NATIVE_LIST` is falsey (the rollback hatch -> the legacy
text-line `_PickerBodyData`). Because the native list was built to byte-identical grid
parity, the flip is seamless: the full suite is green in the default (native) mode
(1719). The forced-ON parity run surfaced the exact test migration -- eight tests: the
text-line-specific NF3/NF4/compose tests pin `NATIVE_LIST=0` (they still validate the
opt-out body), the tasks/registered tests gained an explicit `refresh()` (the native
list rebuilds on refresh, where the text-line body always re-rendered) and the rebuild
signature became pivot-aware (tasks/maintenance row counts), the "disabled by default"
test became `test_native_list_default_with_opt_out`, and `on_option_list_option_highlighted`
now ignores highlight events fired while the list isn't focused (so a modal close can't
clobber a programmatic `sel`). The text-line body remains as the opt-out until it is
retired.
