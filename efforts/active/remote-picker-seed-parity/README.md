# Remote Picker Seed Parity

- **Slug:** `remote-picker-seed-parity`
- **Repo:** copilot-extensions
- **Branch(es):** managed worktree, sequential reviewed PRs targeting `dev`
- **Created:** 2026-10-10
- **Status:** Active
- **Umbrella issue:** [#6057](https://github.com/ThomasMichon/copilot-extensions/issues/6057)
- **Vision:** `picker` / `cold-start-resume-prompt`

## Guiding Intent

Choosing another machine or execution environment must not change the meaning
of New-worktree and cold Resume prompts. The host Picker collects intent; the
selected target's engine and backing daemon own its worktree state, retry
identity, cold-start checks and actual Copilot handoff. Local and SSH invocation
are equivalent entry paths into that same lifecycle, not separate seed systems.

Preserve the completed local contract: New and Resume provenance remain
distinct, failed pre-handoff attempts retain intent, and only actual backend
handoff discards it. Live sessions offer Open without prompt injection. Never
lose or duplicate a confirmed prompt because a remote request, launch or
acknowledgement fails.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Planning, implementation, integration and PR stewardship | Managed source worktree |
| Validation targets | Windows and POSIX execution environments | Resolved SSH source and isolated owned test worktrees |

## Coordination

- **Topology:** sequential plan, implementation and closure PRs.
- **Host (owns PRs):** Driving agent.
- **Delegates:** none assigned; remote test commands remain bounded.
- **Handoff:** preserve this effort and its next incomplete gates. A merged
  phase is not effort completion. Never leave a validation process, test
  worktree, remote lease or PR implicit.

## Context

The local New/Resume state and backend boundary shipped in #5756; deployed
Picker-to-Copilot observations closed its effort in #6048. Reuse that contract.
This is a new parity effort, not a reopening of the completed local one.

Current remote worktree data is fetched over SSH, but the host constructs the
Worktrees action menu and dialogs. `engine_worktree_actions.py` explicitly gates
Resume prompt on `is_local`; `engine_maintenance_actions.py` similarly hides the
New prompt field for remote selections. Host launch confirmation calls local
`resolve` with a target machine, environment and seed. `resolve_cli.py` rejects
remote seeds before routing, and its remote launch arguments omit seed text and
staged identity. This is invocation wiring, not a missing pivot registration.

Vision reconciliation: extend `cold-start-resume-prompt` with execution-location
equivalence. Pattern reconciliation: terminal-neutral engine plans, one
attributable command owner, one target write authority, safe structured process
boundaries, explicit errors and Windows/POSIX parity. No new daemon, endpoint,
central broker or mandatory sibling plugin.

## Request

> Wait, how does the seed make it now? Doesn't the dialog in the first place
> get populated by a command called on the remote machine via SSH in the first
> place, which invokes that machine's backing daemon? Calling on the local
> machine and calling over SSH should use equivalent flows

> Great. Make or update an effort and track it, and let's get that built out
> properlyu

The operator confirmed the slug `remote-picker-seed-parity`.

## Plan

### Phase 1 -- Reviewed target-owned invocation contract

- [x] Review and land this plan and vision amendment before implementation.
- [x] Trace the complete New/Resume host-to-SSH launch path and prior safe
  structured request, remote capability and idempotency mechanisms.
- [x] Specify which target-owned boundary stages intent and returns its
  worktree/seed identity, and which later command launches that exact identity.
- [ ] Resolve lost-response semantics before enabling the offering: New retry
  must identify the already-created worktree, not create another; Resume retry
  must not silently replace or duplicate an admitted intent.

### Phase 2 -- Equivalent local and SSH execution

- [ ] Add safe transport of confirmed text to the attributed target engine,
  preserving project, machine/environment, New/Resume provenance and identity.
- [ ] Reuse target daemon seed stage/reservation/release/finish operations;
  retain local behavior and avoid a host-side copy of the target's seed store.
- [ ] Carry the exact target worktree and intent through the real remote launch
  and setup boundary, including both resolve hops and No Mux.
- [ ] Offer New and stopped Resume prompts for supported remote sources;
  capability/version-skew errors remain visible and seedless launches unchanged.
- [ ] Enforce target-authoritative cold checks after UI confirmation and again
  at backend handoff. Open never delivers a staged prompt.
- [ ] Update engine-Picker contract, Manager documentation and changefiles for
  touched packages; do not hand-edit versions.

### Phase 3 -- Validation, release and closure

- [ ] Complete the Validation Plan, fixing failures in the changed contract.
- [ ] Obtain passing current-head review and CI, merge implementation and
  confirm promotion and installed source identity on validation targets.
- [ ] Exercise the delivered real Picker over SSH, then journal evidence and
  explicitly dispose of any remaining tracked follow-up.
- [ ] Mark Done, archive through review, release focus and finalize only after
  owned validation resources and PR obligations are settled.

## Validation Plan

These concrete fault cases are agent-recommended checks of the requested
equivalent-flow contract, not additional product features.

- [ ] Unit coverage: local/remote action eligibility, version skew, correct
  machine/environment routing, no-Mux, both kinds and identity forwarding.
- [ ] Actual process-boundary checks through Windows and POSIX remote shell
  adapters: multiline and Unicode text, quotes, dollar signs, semicolons and
  shell metacharacters arrive unchanged and are never executed.
- [ ] Failure matrix: disconnected/unavailable target before admission,
  response lost after admission, setup refusal, backend-start failure,
  replacement intent, stale acknowledgement and newly live/unknown target.
  Assert one New worktree, one admitted identity, retained state on known
  pre-handoff failure and no automatic replay of uncertain handoff.
- [ ] Fresh-target/old-engine check: unsupported capability refuses before
  mutation, while ordinary seedless remote New/Resume still work.
- [ ] Live SSH validation in isolated owned Windows and POSIX target worktrees:
  actual Picker New first turn and cold Resume next turn, original Resume
  conversation preserved, exactly one prompt/answer, target state retained
  through preflight and discarded only at actual native Copilot handoff.
- [ ] Record each validation tier exercised, or a concrete reason and tracked
  disposition for an unavailable lane. Never count host plan JSON as remote
  conversation evidence.
- [ ] Confirm unchanged local New/Resume and live Open behavior; retire owned
  test sessions/worktrees and leave no untracked background work.

## Proposal

Use a pre-launch, non-interactive structured invocation of the remote engine to
stage accepted intent and return the exact target-owned identity. Its transport
must be bounded and shell-safe; the subsequent interactive SSH launch carries
identity rather than a second copy of prompt text. Select the existing shared
transport and admission primitives after tracing them; do not add a new service
or silently retry a mutation whose response was lost.

The host may render the composer locally. Location equivalence applies to
target-owned execution and persistence, not to requiring a second UI runtime.
An old or unavailable target reports why it cannot accept intent instead of
hiding the problem or silently dropping text.

## Journal

- **2026-10-10** -- Operator requested remote New/Resume prompt parity and
  confirmed the effort slug. Searched open remote-prompt issues and PRs; none
  covers this Worktrees flow. Created #6057 and verified compatible target
  effort adoption with the native read-only probe. Plan is Draft pending
  repository review; no implementation changes made.
- **2026-10-10** -- Plan approved and merged as #6059. Publication timed out
  after the branch reached the provider; verified that exact branch and opened
  its PR through the supported already-published-branch flow, without rewriting
  the remote. Synced the source worktree onto the reviewed plan.
  Implemented the initial target-local structured admission/status surface and
  existing-daemon admission/staging transactions. A text-free request receipt
  fences allocation and prompt identity; New creation receives its recorded
  allocation, and accepted/replaced/completed intents cannot be silently replayed.
  Host SSH launch carries an encoded request first and only the returned
  worktree/seed identity for interactive launch. Added remote composer parity,
  retained same-machine AHP restrictions and bounded shell command size.
  Initial regression portfolios pass: 78 engine admission/creation/seed/routing
  cases and 28 Manager UI/client cases. Ten focused admission/transport tests
  pass, including real PowerShell argv receipt (one native-POSIX check skipped
  on Windows and reserved for the SSH lane). A domain refusal initially closed
  the daemon socket instead of returning its cause; corrected that to structured
  error propagation without automatic mutation replay.
  The declared POSIX SSH route is reachable; the first Windows target probe
  failed. No remote state, install or live validation worktree has been created.
  Full fault-matrix, final safety/compatibility checks, real SSH validation,
  implementation review/CI and deployment remain; this is not a completion claim.
- **2026-10-10** -- Independent correctness review identified two crash fences
  and error precedence defects. Added durable creation-start fencing before New
  side effects, reloaded admission under the execution lock, and prevented
  recreation after incomplete/reaped attempts. Stale admission now compares the
  durable seed revision as well as the record revision, so an interrupted
  replacement cannot be overwritten. Remote failure reporting prioritizes the
  structured target error instead of earlier creation progress. Added regressions
  for each case. The expanded portfolio passes 107 engine cases (one native
  POSIX skip) and 55 Manager cases; the latter includes launch-script contracts.
  Remaining declared Windows routes also failed bounded reachability probes.
  The reachable POSIX target has an old engine without the new capability:
  its fresh/old-target refusal and eventual candidate/deployed live validation
  remain explicit next gates. No target provisioning or remote mutation occurred.
