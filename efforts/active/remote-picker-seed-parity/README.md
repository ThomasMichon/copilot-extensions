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
- [x] Resolve lost-response semantics before enabling the offering: New retry
  must identify the already-created worktree, not create another; Resume retry
  must not silently replace or duplicate an admitted intent.

### Phase 2 -- Equivalent local and SSH execution

- [x] Add safe transport of confirmed text to the attributed target engine,
  preserving project, machine/environment, New/Resume provenance and identity.
- [x] Reuse target daemon seed stage/reservation/release/finish operations;
  retain local behavior and avoid a host-side copy of the target's seed store.
- [x] Carry the exact target worktree and intent through the real remote launch
  and setup boundary, including both resolve hops and No Mux.
- [x] Offer New and stopped Resume prompts for supported remote sources;
  capability/version-skew errors remain visible and seedless launches unchanged.
- [x] Enforce target-authoritative cold checks after UI confirmation and again
  at backend handoff. Open never delivers a staged prompt.
- [x] Update engine-Picker contract, Manager documentation and changefiles for
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

- [x] Unit coverage: local/remote action eligibility, version skew, correct
  machine/environment routing, no-Mux, both kinds and identity forwarding.
- [x] Actual process-boundary checks through Windows and POSIX remote shell
  adapters: multiline and Unicode text, quotes, dollar signs, semicolons and
  shell metacharacters arrive unchanged and are never executed.
- [x] Failure matrix: disconnected/unavailable target before admission,
  response lost after admission, setup refusal, backend-start failure,
  replacement intent, stale acknowledgement and newly live/unknown target.
  Assert one New worktree, one admitted identity, retained state on known
  pre-handoff failure and no automatic replay of uncertain handoff.
- [x] Fresh-target/old-engine check: unsupported capability refuses before
  mutation, while ordinary seedless remote New/Resume still work.
- [ ] Live SSH validation in an isolated owned POSIX target worktree:
  actual Picker New first turn and cold Resume next turn, original Resume
  conversation preserved, exactly one prompt/answer, target state retained
  through preflight and discarded only at actual native Copilot handoff.
- [x] Deferred to `ThomasMichon/copilot-extensions#6370`: native Windows
  Picker New/cold Resume conversation validation and managed disposition of
  the isolated Windows source candidate, after its SSH route becomes reachable.
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
- **2026-10-10** -- Operator authorized isolated POSIX validation setup after
  the only reachable cell proved too old and unregistered. Used the existing
  project's unified update, repaired the stale Manager through its repository
  bootstrap, registered a source checkout and created an isolated managed
  candidate worktree. No installed payload was edited. Candidate tests in that
  cell pass 22 admission/transport cases (PowerShell unavailable there).
  Actual SSH admission staged New provenance and fixed allocation, followed by
  native Copilot first-turn receipt and the expected answer through the target's
  installed No-Mux launcher. The first detached-mux attempt could not switch a
  client that was not attached; its seed remained intact, and retrying the same
  worktree/intent with No Mux succeeded. This is target admission/backend
  evidence, not yet a delivered remote Picker observation.
  Cold Resume has been staged for that same conversation and is under
  observation. Windows routes remain unreachable. The expanded full guard run
  reached the runner's 300-second sub-suite wall limit with no assertion failure
  reported before termination; split/targeted validation remains rather than
  bypassing containment or treating the timeout as a pass.
- **2026-10-10** -- Real target-side Resume completed with the expected answer
  in the same conversation as the preceding New validation. Latest recovery
  revision also passes 22 POSIX candidate cases. Final draft review has only
  documentation/status findings; the request selector, shell, routing and
  incomplete-New recovery defects are addressed.
  Full local Manager validation exposed a minimal self-update fixture that
  relied on an editable-source layout for its provider manifest. Made the
  fixture carry the real required template and pinned its running payload;
  all six delivery cases pass with the installed-package runner.
  CI exposed a callback race test waiting for a transient total worker count:
  the replacement worker may finish before the old worker retires. Changed
  its wait to the exact old registration identity; the persisted replacement
  assertion remains. Added the required package changefile, with targeted
  regression passing. These fixes are owed forward to make the encountered
  validation gates honest, not dismissed as pre-existing failures.
- **2026-10-10** -- The contained Manager runner exposed two additional
  environment assumptions: its regular wheel install omitted the distribution's
  sibling launcher/reference layout, and explicit fixture homes were overridden
  by runner-injected plugin/pivot roots. Prepare the Manager editably from its
  real source payload and clear those overrides only in the fixture-home
  contract module. The affected layout/discovery/install/delivery portfolio
  passes 70 cases (two skips), and runner contracts pass 13 (one skip).
  Full-suite observations still include setup-epoch and process-probe timeouts;
  isolated revalidation is in progress. Later CI has another failed plugin
  fan-out awaiting its completed logs; no merge bypass is used.
- **2026-10-12** -- Resumed after an interrupted publication. The target branch
  had not received the last fixture commit; published that commit and the
  subsequent selector/budget regressions. Malformed Base64 already falls under
  ValueError because binascii.Error subclasses it; a CLI regression proves JSON
  refusal. Also normalize that decode error explicitly and normalize shell
  casing, avoiding ambiguous diagnostics.
  Full POSIX Manager validation passed 1797 cases (47 skips). Full Windows
  validation still exposed native process-start/probe timeouts and a contributed
  pivot refresh test; do not count it as passing. Reconciled with newer upstream
  machine canonicalization, contributed-action confirmation, native census and
  environment-scoped runner lease fixes. Preserved the stronger already-landed
  callback-generation fix rather than adding a competing repair.
  Remote request staging now enforces receiving-side project authority and
  requires version-2 daemon verbs so an older resident cannot bypass it.
  Rebased boundary tests pass 64 cases (one native POSIX skip); runner contracts
  pass 36 (two skips). Corrected the POSIX symlink fixture to declare pyvenv.cfg
  and use the current lease interface.
  With operator-authorized temporary candidate wiring, drove actual New and
  Resume composer screens with a controlled Picker source, then the real host
  resolver using a captured authoritative machine roster, actual SSH target
  admission and an identity-only native No-Mux launch. Both prompts appeared
  once and received the expected answers in the same target conversation.
  This is candidate process-boundary evidence, not a delivered-install claim;
  the controlled UI source and isolated candidate launcher are explicitly
  distinct from the normal installed source discovery path. State was discarded
  at handoff. Removed temporary wiring and finalized both completed POSIX
  conversation-validation worktrees; the isolated source candidate remains for
  final release validation.
  Current CI also encountered the four partial-publication fixture failures
  owned by #6336. Posted evidence there and await its already-claimed repair;
  no duplicate implementation or merge bypass.
  One Windows SSH target briefly became reachable. The operator authorized an
  isolated managed candidate there; candidate checkout succeeded, but test
  preparation reported missing uv. The approved normal Manager bootstrap could
  not run because the route then failed banner exchange and a bounded
  reachability recheck. Keep that source worktree accounted for until the route
  returns; no test result or completed Manager update is claimed for it.
  Remaining gates are native Windows/lifecycle disposition, current-head green
  CI and review, promotion/deployment, delivered Picker observation and closure.
- **2026-10-12** -- Current-head required CI is now green; the independently
  claimed publication-fixture repairs landed through their own PRs. The draft
  remains mergeable without another history rewrite. Review found two remaining
  remote-boundary gaps: invalid or mismatched zero-exit receipts also represent
  uncertain admission, and an 8 KiB envelope exceeds the PowerShell command
  budget after wrapping. All uncertain receipts now name the original request,
  prohibit resubmission and give its target status command without automatic
  replay. Reduced the shared envelope ceiling to 7 KiB including metadata and
  documented the pre-admission command-size check.
  Contained admission/transport regressions pass 47 cases (one native POSIX
  skip), including exact-limit and one-byte-over checks for both kinds and shell
  families. The native PowerShell process round-trip also passes at the full
  envelope limit with Unicode and shell metacharacters. Removed a duplicate
  Manager fixture while retaining its canonical root isolation; its 20 contract
  cases pass. Changed Python lint passes. The authorized Windows target remains
  unreachable on a bounded recheck, so its native conversation lane and source
  worktree disposition remain open. Candidate proof is still not delivered
  Picker proof; release, rollout and effort closure are outstanding.
- **2026-10-12** -- Operator authorized transferring the unreachable native
  Windows conversation lane and its managed source-worktree disposition to
  #6370. This is an explicit tracked deferral, not a Windows SSH pass or
  permission to clear another session's ownership. The latest POSIX candidate
  admission/transport portfolio passes 47 cases (one PowerShell skip), including
  the full 7 KiB native Bash process round-trip.
  Refreshed review exposed the crash between durable seed staging and acceptance
  receipt publication. Read-only recovery now recognizes the matching durable
  seed for Resume or a fully created New allocation even before that final
  receipt write, while preserving handoff uncertainty and refusing unfinished
  New creation. Eight focused daemon recovery/supersession cases pass, including
  injected receipt-write failure for both kinds; unstaged Resume is explicitly
  `admission-incomplete`. The test fixture uses its actual registered project,
  retaining receiving-side authority rather than mocking it away. Lint passes.
- **2026-10-12** -- Current-head CI passed, but review correctly found remaining
  non-protocol resolve modifiers bypassed ordinary validation. Structured
  admission/status now rejects every populated non-protocol option before
  reading or mutating target state; ordinary resolves retain their default
  deferred-New behavior. The 48-case focused CLI portfolio passes, including
  both request modes across 21 modifiers. Added the remote timeout-floor
  matrix; all 31 Manager resolve compatibility cases pass, preserving local,
  seedless and larger caller timeout values.
  Native FD capture teardown caused contained CLI runs to time out. Switched
  these JSON assertions to the repository's real-output capture helper rather
  than raising containment budgets. Expanded admission and ordinary delivery
  checks passed 159 cases (one native POSIX skip), with one native PowerShell
  consumer timing out. That fixture omitted the launch plan's working-directory
  contract and ran setup in the source checkout. It now launches in its isolated
  worktree directory, and its exact prompt/provenance/discard assertions pass
  through real PowerShell. No cold-check or receiving-authority guard was
  weakened. Required current-head review/CI and delivered rollout remain open.
- **2026-10-13** -- Review found JSON's byte-input parser also accepts UTF-16
  and UTF-32, contradicting the specified UTF-8 envelope. Decode UTF-8 strictly
  before JSON parsing. Five focused regressions pass, including rejection of
  both alternate encodings, structured invalid-UTF-8 refusal and unchanged
  arbitrary-text round-trip. The rebased POSIX admission/recovery portfolio
  passed all 95 applicable cases (one PowerShell skip). No new backend or
  transport path was introduced; current-head review/CI remain required.
- **2026-10-13** -- Review identified two remaining malformed/recovery boundaries.
  Explicitly empty protocol options now select structured mode and refuse,
  including a valid request paired with an empty status option; none can fall
  into ordinary New creation. Unsupported empty-string modifiers also refuse.
  Recovery validates the exact persisted New identity against the creator's
  machine/platform prefix before recording creation start or invoking side
  effects. A changed identity refuses rather than creating an orphan under a
  different prefix. The complete contained admission portfolio passes 116
  cases (one native POSIX skip), including lost-admission-response recovery,
  machine/platform drift and empty-option dispatch guards.
- **2026-10-13** -- Current-head CI completed successfully. A later fallback
  review, whose full agentic portion did not start before its runner deadline,
  found a valid corrupt-admission-file robustness gap. Centralized validation
  of every stored receipt read and new receipt write, returning domain errors
  for missing/wrong fields and unsafe identities rather than leaking KeyError
  or TypeError from the daemon handler. The exhaustive 28-case registered-
  handler matrix and a separate real-wire malformed-response/liveness check
  pass; the complete contained admission portfolio passes 145 cases (one
  native POSIX skip). RPC and containment deadlines are unchanged.
  The first repeated network matrix encountered intermittent healthy-follow-up
  response deadlines; separated exhaustive handler-shape checks from the
  focused socket contract instead of increasing budgets or claiming those runs
  passed. Receiving-side authority remains real. Manager harness-state fixtures
  now use explicit UTF-8; all 19 cases pass. Platform detection is sampled once.
  The fallback import finding is incorrect: machine_transport is the declared
  top-level shared distribution, not an agent_worktrees sibling, and the
  actual SSH/PowerShell process checks exercise that existing dependency.
- **2026-10-13** -- Implementation-head CI and the workflow guard passed.
  Documented and resolved fallback findings contradicted by the existing
  default-launch router, OSError handling and shared unsupported-verb recovery.
  The operator authorized further rebases of this same PR. Reconciled the next
  base advance with upstream foreground progress streaming and its unbounded
  preparation default, preserving both instead of applying a fixed limit to
  every launch. Explicitly bounded remote prompts retain the admission timeout
  floor; default None remains unbounded. The 102-case contained Manager client
  and harness-state portfolio passes, including the expanded timeout matrix.
  Retained the upstream parameterized WSL fixture rather than replaying an
  obsolete registry rewrite. Current rebased-head CI/review, promotion,
  installed rollout and normal-source Picker conversation proof remain open.
- **2026-10-13** -- Rebased-head CI passed. Its refreshed review found a valid
  cross-field corruption gap: a structurally valid receipt could retain its
  fingerprint while changing prompt kind or the Resume worktree. Replay now
  compares those fields with the request before returning an admission and
  again after the locked reload. Six regression cases cover safe-but-mismatched
  identities, both provenance directions and corruption between the two reads,
  asserting no prompt reaches the other existing worktree and no damaged
  receipt is rewritten. The complete contained admission portfolio passes
  151 cases (one native POSIX skip); lint passes. Current-head review and CI
  must rerun before merge; release and delivered validation remain open.
- **2026-10-13** -- Implementation #6073 merged after the exact head received
  a clean Balanced review with zero open findings, successful CI and the
  required workflow guard. The normal merge command initially refused a stale
  recorded push head following authorized native publication. Reconciled it
  with the existing guarded adoption primitive, which checks the provider head
  against the sole tracked worktree's actual HEAD before recording it; the
  normal role-scoped merge then succeeded. No expected-head check was bypassed.
  Pulled forward onto the confirmed squash merge. Fault-matrix and old-engine
  compatibility coverage are resolved; Windows conversation validation and
  candidate cleanup remain explicitly transferred to #6370, not claimed as
  passing. Continue through promotion, authorized installed rollout and the
  normal-source POSIX Picker New/cold Resume observation before closure.
- **2026-10-13** -- Release remains blocked. The service-name compatibility
  repair is owned by #6506; do not duplicate it. A later gate exposed two
  stale local-cache resolver tests after #6429 replaced sibling CLI discovery
  with this payload's packaged engine. Claimed #6507 and updated the tests to
  exercise supported packaged-engine exceptions and success without external
  resolver execution. The contained diagnostics and materialized standalone
  engine portfolio passes all ten cases, including real rendering without a
  sibling plugin. This narrow test repair does not establish promotion or
  delivered Picker behavior; those gates remain open.
