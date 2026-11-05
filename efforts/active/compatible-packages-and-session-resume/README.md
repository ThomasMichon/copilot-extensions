# Compatible Packages and Session Resume

- **Slug:** `compatible-packages-and-session-resume`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed plan and source PRs against `dev`
- **Created:** 2026-11-05
- **Status:** Draft
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

Package resources currently support an exact version or unspecified presence,
but cannot express a minimum plus known-bad exclusions. Worktrees already accepts
PSMux >=3.3.5 except 3.3.6, while some consumers independently enforce exact pins.
Seeded embodiment currently interprets a prompt as a new task unless
`--resume-head` is supplied. That can discard expected conversational continuity.

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

- [ ] Land this proposal through normal review before implementation.
- [ ] Confirm package constraint compatibility and existing launch intent callers.

### Phase 2 - Compatible package admission

- [ ] Add a minimum-plus-denylist package contract that preserves acceptable
  installed versions, rejects unknown/incompatible results, retains exact pins,
  and never bypasses process guards.
- [ ] Keep old engines from silently ignoring a new compatibility declaration;
  document manifest compatibility and contributor merge behavior.
- [ ] Publish focused validation and land the package source change.

### Phase 3 - Resume-by-default launches

- [ ] Resume the existing head by default for ordinary seeded and unseeded
  embodiment and attended launch; preserve explicit new-task/fresh intent.
- [ ] Keep live embodiment idempotent and report existing mux identity without
  creating a duplicate or injecting another prompt.
- [ ] Update callers that intentionally require fresh conversations and document
  detached programmatic creation versus current-terminal attachment.
- [ ] Publish focused validation and land the launch source change.

### Phase 4 - Maintenance recovery contract and closeout

- [ ] Document advance notice, cooperative checkpoint/drain, approved bounded
  shutdown, saved original-conversation restoration and status-first recovery.
  Keep operation scope separate from package admission and ordinary launch.
- [ ] Define the configurable default recovery prompt and distinguish launch,
  prompt delivery and completed reconciliation. Reuse existing primitives.
- [ ] Record release versus rollout state, resolve all validation items and
  close the finite source effort without implying a host upgrade occurred.

## Validation Plan

- [ ] Package cases cover floor, denylist, newer/future versions, malformed or
  missing live versions, exact-pin compatibility, conflicts and process deferral.
- [ ] Exercise the real package resource/CLI consumer with synthetic package-manager
  responses; no actual host package installation or removal.
- [ ] Launch cases cover existing head with/without seed, explicit fresh,
  new worktree, explicit resume arguments, already-live targets and unknown liveness.
- [ ] Check intentional fresh callers and literal startup-prompt preservation.
- [ ] Run focused runtime tests, required unit/contract checks and touched-source lint.
- [ ] Record native/clean-room evidence and explicit skipped-lane reasons;
  source fixtures do not qualify installed rollout or disruptive maintenance.
- [ ] Re-read the operator capture against the original request before publication.

## Proposal

Add a versioned package compatibility contract rather than a PSMux-specific
installer escape hatch. Prefer the existing `--fresh` opt-in over another
conversation mode. Retain `--resume-head` as a compatibility spelling where useful.
An existing live session wins over a cold-launch prompt: callers needing to
message it use its established input owner.

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
