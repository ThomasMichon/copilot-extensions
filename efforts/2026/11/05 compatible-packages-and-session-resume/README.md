# Compatible Packages and Session Resume

- **Slug:** `compatible-packages-and-session-resume`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed plan and source PRs against `dev`
- **Created:** 2026-11-05
- **Status:** Done
- **Vision:** `agent-fabric` / `discover-before-duplicate`, `recover-not-lose`,
  `single-current-session-per-worktree`; `installer` / authoritative runtime replacement
- **Umbrella issue:** #10657

## Guiding Intent

Accept compatible newer package versions without speculative upper bounds.
Bring existing work back into its original conversation unless the caller
explicitly requests a fresh one. Programmatic embodiment and attended terminal
connection select conversations consistently but have different attachment effects.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| source-driver | Package constraints, launch semantics, tests and PR stewardship | This effort and #10657 |

## Coordination

- **Topology:** A reviewed plan followed by bounded implementation PRs.
- **Host (owns PRs):** source-driver.
- **Delegates:** None.
- **Handoff:** Preserve this plan, validation outcomes and outstanding publication gates.
- Existing #9364 startup-prompt, #8669 installation-survival, #7694 launch-maintenance
  and #10637 Windows mux-startup work retain their owners. This effort changes
  conversation-selection policy, not their transport or lifetime contracts.

## Context

At inception, package resources supported an exact version or unspecified presence,
but could not express a minimum plus known-bad exclusions. Worktrees already accepts
PSMux >=3.3.5 except 3.3.6, while some consumers independently enforce exact pins.
Seeded embodiment interpreted a prompt as a new task unless
`--resume-head` was supplied. That could discard expected conversational continuity.

This closes the existing session-reuse vision rather than introducing another
session authority. Package admission remains with `agent-machines`; conversation
selection, process creation and attachment remain with `agent-worktrees`.
The patterns' primitives-below/orchestration-above boundary keeps host maintenance
policy separate from ordinary launch.

## Request

The operator's implementation direction, preserved verbatim:

> Yes let's do the minumum+deny-list.
> We should make the *default* behavior for embody to resume the existing head; forcing a new conversation should require "opt-in". We also need to disambiguate `agent-worktrees embody` (kicks the process programmatically) vs `agent-worktrees copilot` (connect TTY to the current terminal). But both should have similar conventions. `agent-worktrees embody` to an already-active worktree should basiclly no-op or return its existing mux process info.
>
> Agree that "put back" will require agents to do some self recovery of whatever was killed. And it's also better to allow active agents an opportunity to "gracefully stop" in advance of a maintenance window. Basically, this is just a "maintenance window" behavior, same as we might to if we needed to restart the whole machine.

The earlier requested configurable recovery default is:
`Terminal session restarted. Check status and resume.`

## Scope Approvals

The quoted request authorizes source implementation, bounded isolated validation
and normal repository review/publication. It does not authorize terminating
existing sessions, changing installed packages, restarting services, rebooting,
deploying new runtimes or activating a maintenance window.

The maintenance model is specified here as source design, not an operational
rollout. An actual disruptive executor needs separately admitted resource scope,
window, shutdown policy and recovery custody.

## Plan

### Phase 1 - Reviewed contract

- [x] Land this proposal through normal review before implementation.
- [x] Confirm package constraint compatibility and existing launch intent callers.

### Phase 2 - Compatible package admission

- [x] Add a minimum-plus-denylist package contract that preserves acceptable
  installed versions, rejects unknown/incompatible results, retains exact pins,
  and never bypasses process guards.
- [x] Keep old engines from silently ignoring a new compatibility declaration;
  document manifest compatibility and contributor merge behavior.
- [x] Publish focused validation and land the package source change.

### Phase 3 - Resume-by-default launches

- [x] Resume the existing head by default for ordinary seeded and unseeded
  embodiment and attended launch; preserve explicit new-task/fresh intent.
- [x] Announce the implicit-seeded-fresh to implicit-resume policy cutover.
  Migrate automated callers to explicit `--fresh` or `--resume-head` intent
  before relying on the new default; those existing embodiment flags retain
  their meaning across provider upgrade and rollback. Unknown external callers
  are not assumed to have migrated merely because this repository changed.
- [x] Keep live embodiment idempotent and report existing mux identity without
  creating a duplicate or injecting another prompt.
- [x] Update callers that intentionally require fresh conversations and document
  detached programmatic creation versus current-terminal attachment.
- [x] Publish focused validation and land the launch source change.

### Phase 4 - Maintenance recovery contract and closeout

- [x] Document advance notice, cooperative checkpoint/drain, approved bounded
  shutdown, saved original-conversation restoration and status-first recovery.
  Keep operation scope separate from package admission and ordinary launch.
- [x] Define the configurable default recovery prompt and distinguish launch,
  prompt delivery and completed reconciliation. Reuse existing primitives.
- [x] Record release versus rollout state, resolve all validation items and
  close the finite source effort without implying a host upgrade occurred.

## Validation Plan

- [x] Package cases cover floor, denylist, newer/future versions, malformed or
  missing live versions, exact-pin compatibility, conflicts and process deferral.
- [x] Exercise the real package resource/CLI consumer with synthetic package-manager
  responses; no actual host package installation or removal.
- [x] Launch cases cover existing head with/without seed, explicit fresh,
  new worktree, explicit resume arguments, already-live targets and unknown liveness.
- [x] Check intentional fresh callers and literal startup-prompt preservation.
- [x] Cover old explicit-intent callers with the new provider, new
  explicit-intent callers with the supported prior provider and provider
  rollback. Record the intentional behavior change for unmigrated implicit
  `--seed` callers rather than claiming backward-compatible defaults.
- [x] Run focused runtime tests, required unit/contract checks and touched-source lint.
- [x] Record native/clean-room evidence and explicit skipped-lane reasons;
  source fixtures do not qualify installed rollout or disruptive maintenance.
- [x] Re-read the operator capture against the original request before publication.

## Proposal

Add a versioned package compatibility contract rather than a PSMux-specific
installer escape hatch. Prefer the existing `--fresh` opt-in over another
conversation mode. Retain `--resume-head` as a compatibility spelling where useful.
An existing live session wins over a cold-launch prompt: callers needing to
message it use its established input owner.

### Compatibility and cutover

The implicit meaning of an existing seeded launch cannot remain both fresh and
resume. The requested resume default is therefore an explicit policy cutover,
not an additive compatibility claim. Before switching it, identify in-tree
automated callers and give each an explicit conversation intent. Existing
`embody --fresh` and `embody --resume-head` form the mixed-version bridge:
they mean the same thing on the supported old and new providers. External
automation that requires fresh conversations must select `--fresh`; its old
implicit behavior is not silently promised after the announced cutover.
New attended-parser options are additive and must be documented as unavailable
on prior providers, without effectful probe-and-retry.

The package constraint declaration must carry a manifest contract that an
older engine rejects before mutation, rather than silently ignoring its floor
or exclusions. Publish provider support before dependent declarations; a
provider rollback reports unsupported schema rather than applying a weaker
policy. Retain legacy exact-pin declarations and their prior interpretation.

### Documentation impact

The reviewed proposal initially changed no runtime behavior. The delivered
implementation updates package schema/merge/application references, Worktrees
CLI/mux guidance and affected caller documentation. Recovery design remains
prospective rather than asserting a disruptive executor exists.

### Contract impact

The proposed boundaries are requirement-package parsing/application and the
`embody`/`copilot` command line. Package compatibility adds mandatory semantics
with explicit old-engine refusal; seeded-resume changes an existing default
through the announced explicit-intent migration above. Receipts continue to
distinguish process creation, prompt delivery and completed recovery. The
installer's authoritative-replacement goal remains unchanged: incoming
readiness and current selection define completion; preserved user state and
independently scoped retirement remain required. This plan neither introduces
an incumbent-provenance veto nor treats maintenance/source publication as
installed replacement authority. Mixed-version and rollback tests precede
claiming the new source contract supported.

Maintenance orchestration is a general window lifecycle shared by binary updates
and machine restarts, not a bespoke PSMux kill script. Persist the original
conversation/workspace inventory before shutdown, allow advance cooperative
checkpointing, and resume through normal owners afterward. Do not equate
recreated terminals with restored shell/process memory. A future executor must
survive outside affected sessions, fence competing launches, preserve pauses and
claims, and recover partial/unknown results without blindly replaying actions.

## Journal

### 2026-11-05 - Proposal

- Captured the requested minimum-plus-denylist policy, resume default, live
  embodiment no-op and maintenance-window model.
- Established #10657 and isolated source workspace. No implementation or rollout yet.
- Addressed plan review by naming the explicit launch-default cutover,
  mixed-version/rollback tests, provider-first package schema fence and
  documentation/contract impact assessments. Preserved the independently
  updated effort-index entry during rebase.
- Plan #10659 received an approving review and merged with green required CI.
  Continued in the same source workspace after dropping only the confirmed
  squash-merged planning commits. Package baseline: 185 passed; launch baseline:
  107 passed through the contained repository runner.
- Implemented schema-5 numeric floors, accumulated release exclusions and
  explicit scoped WinGet pin removal, preserving compatible installed versions
  and all existing process/maintenance gates. Legacy schemas remain readable.
  The package handler also refuses an unsatisfiable merged exact pin before
  probing or mutating through direct resource application.
- Package validation: 333 passed before the final direct-conflict regression;
  includes a real isolated package-manager shim lookup/upgrade/idempotence
  witness. Lint, module-size, install-contract and docs-consistency checks passed.
  Real host package replacement and full fresh-machine provisioning are not
  authorized by this source scope; those installed lanes remain unexercised.
- Package review identified generic detector admission gaps. Exact-entry parsing
  now preserves complete version suffixes and distinguishes confirmed apt/pip
  absence from probe failure. The expanded package packet passed 346 tests.
- Implemented the requested resume default and shared attended/detached flags.
  Ordinary live embodiment does not consume any startup text. The existing
  externally-created-pane fallback explicitly opts into staged-New settlement;
  its new provider/adapter pairing is an announced cutover, not silently
  claimed compatible with old callbacks. Dispatch explicitly chooses fresh
  versus continuation, retaining old/new-provider argument meaning.
- Launch validation: 126 passed; 105 Linux/WSL restart-qualification cases
  skipped on Windows. Dispatch's explicit-intent packet passed 67; Manager's
  startup/guard packet passed 87 with four unavailable platform cases skipped.
  A misplaced new-worktree assertion was corrected before the passing rerun.
- Recorded the general maintenance-window intent and the existing status-first
  resume command/default wording. No disruptive executor, host package update,
  active-session stop, reboot or installed runtime rollout was performed.
- Final live-no-op audit included anchor joins and dry-run previews: neither
  now claims Bridge-deferred startup text. The expanded Worktrees packet passed
  129 tests, with touched-source lint and module-size checks green.
- Further review covered a false-to-true mux transition during preflight.
  Bridge claims and native startup staging now occur only after the
  lifecycle-locked live check. The seed writer acquires its own admission
  lease; process creation takes admission afterward. A real non-reentrant
  lease control verifies both boundaries without a running model.
- Exact tool-list detection now handles a distribution literally named
  `package`, including verbose output. Package regression total: 349 passed.
  Ordinary resumed heads also select their saved launch profile rather than
  allocating a different context; a profile-bound head change refuses before
  startup-text custody changes. Expanded Worktrees total: 169 passed.
- Admission-only native setup can advance the head without the outer lifecycle
  lock. Added a final record/head/liveness/mux revalidation under the acquired
  admission lease before spawn. Late wins never duplicate a process or replay
  prepared text; native seed custody is reported unconfirmed. The expanded
  Worktrees/profile packet passed 255 tests.
- CI run 42005541498 on the prior source head timed out in the six-minute
  admitted-type-contract step after successful target output, without type
  diagnostics. The corrected head must pass fresh required CI; no bypass or
  untracked flake exemption is assumed.

### 2026-11-05 - Source completion

- Implementation #10673 merged at `2d913c1364f5b85afb896c43f8059f78cbf6aee3`.
  The exact published head received an approving Copilot review, all required
  CI passed, and all five substantive review findings were fixed and resolved.
  The earlier typecheck timeout was superseded by a real passing gate; no
  CI bypass or flake exception was used.
- Final focused evidence: 349 package tests, 255 Worktrees/profile tests,
  67 Dispatch tests and 87 Manager tests passed. Real isolated package-manager
  processes and non-reentrant native leases exercised the consumer boundaries.
  The separate Linux/WSL restart packet skipped 105 cases on Windows; four
  Manager native POSIX/Bash cases were unavailable on Windows. Those are not
  native-platform passes.
- Full fresh-machine provisioning, actual package replacement, headed model
  startup, machine reboot and installed rollout were not performed. They are
  outside this finite source scope and require their own admitted operational
  venue and authority. The maintenance-window executor remains future work,
  not an unacknowledged feature delivered by this effort.
- Source is merged to `dev`, pending normal release under #3336 at this
  closeout; merged source is not installed behavior. No local or fleet update
  was authorized. The finite source objective is Done, with release and any
  later authorized adoption retaining their existing owners.
