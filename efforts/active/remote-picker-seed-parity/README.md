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

- [ ] Reconcile the current native-seed transport fences: #6176 subsequently
  made remote composers and `--require-native-seed` unsupported. Restore the
  requested target-owned SSH contract through negotiated capability, not by
  removing safety checks; prove this against current installed sources.
- [ ] Integrate the separately tracked #8375 attempt journal so a failed New
  or Resume retains private prompt/options and correlated outcome evidence.
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

The operator's October 23 follow-up reports continuing crash-associated New
prompt loss and restates target-owned SSH creation with the seed. The linked
`worktree-launch-attempt-journal` effort (#8375) owns the newly requested
private 90-day attempt/outcome audit, not replacement execution authority.
Earlier checked implementation items remain historical source evidence; they
do not establish that later transport fences preserve installed parity.

## Journal

- **2026-10-25** -- #8990 merged bounded local Manager/engine attempt inspection
  with current-head approval and required CI. Saved intent and qualified
  receipts can be inspected without replaying prompts; acceptance remains
  unknown. Interactive history, remote inspection, retention, release and
  installed same-conversation New/cold Resume proof remain open.
- **2026-10-25** -- #8961 merged binding-free Bridge startup after current-head
  approval and required CI. Discovery no longer requires source-project
  machine binding; explicit selectors and effect-side ownership remain.
  This removes a source readiness prerequisite only. Containing release,
  authorized rollout, actual local/SSH conversation evidence and every
  remaining Phase 3 gate stay open.
- **2026-10-24** -- #8641 merged original native-session observations with
  current-head approval, no open findings and required CI. Qualified
  Linux/WSL source witnesses can join exact execution/incarnation/epoch and
  backend PID/birth evidence; prompt acceptance remains explicitly unknown.
  Native positive and unavailable-registration/observation controls passed.
  Re-read both canonical efforts after merge. Joined inspection, retention,
  containing release, authorized rollout, installed local/SSH same-conversation
  proof and cleanup remain open; no Phase 3 completion is claimed.
- **2026-10-24** -- #8627 merged prompt-free deferred execution receipts with
  current-head approval and required CI. Attempt/resolution/parent IDs now
  reach the cooperative backend boundary; OS spawn, seed claim and observed
  return remain distinct from native conversation/accepted-turn evidence.
  Review fixes preserve settlement and waits through interruption and avoid
  repeated boot waits on proven live incompatible owners. Re-read both plans
  after merge. Native association, containing release, authorized rollout,
  installed local/SSH same-conversation proof and cleanup remain open; the
  source process checks do not close those acceptance gates.
- **2026-10-24** -- #8615 merged local/seedless resolution receipts and saved
  attempt propagation through both launch hops, with current-head approval,
  no findings and required CI passing. Target prompted admission retains its
  own durable version-2 receipt; seedless SSH planning remains forwarding only.
  Re-read this plan and the journal effort after merge. Native process,
  conversation/handoff evidence, containing release, authorized rollout,
  installed same-conversation local/SSH acceptance and cleanup remain open.
- **2026-10-24** -- Linked attempt-journal proposal #8380 merged with current-head
  approval and required CI. The reviewed plan preserves target-owned execution,
  protected 90-day prompt retention and original installed acceptance gates.
  Begin the journal entry-surface/receipt inventory; no runtime repair or SSH
  acceptance is implied by the documentation merge.
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
- **2026-10-13** -- The packaged-resolver test repair merged as #6508 after
  an Approved review with zero findings and successful current-head CI and
  workflow guard. Shared the result with the existing service-alias driver.
  Its #6506 is still open with failing test-runner checks, so promotion and
  the authorized installed rollout remain blocked; no deployed behavior is
  claimed.
- **2026-10-13** -- The next full release gate exposed #6520: an older test
  expected the parser itself to default deferred ownership to True. Structured
  admission now needs an unset value to distinguish explicit non-protocol
  options; ordinary Resolve still normalizes that value to True before
  constructing command state. Updated coverage proves both option-presence
  semantics and the real handler default. The complete contained New-seed and
  launch-request portfolio passes 166 cases with one native POSIX skip.
  No production default, protocol refusal or containment limit changed.
- **2026-10-13** -- The deferred-ownership test repair merged as #6523 after
  its corrected head received an Approved review with zero findings, green
  CI and the required workflow guard. Both parser and handler contracts now
  carry the fast guard marker; a contained guard-only run selects and passes
  all four cases. The independent service-alias repair #6506 remains open
  under its existing owner, so release, rollout and delivered observation
  are still pending.
- **2026-10-13** -- Release progressed to managed-removal coverage and exposed
  #6528: its shared receiving-authority fixture rejected the config loader's
  scoped related-PR keyword. Both fixture loader signatures now accept and
  forward that keyword; production receiving authority and removal checks
  are unchanged. Updating only the ordinary loader was insufficient because
  cleanup uses the project loader. The final complete contained removal,
  launch-seed state and authority-budget portfolio passes all 63 cases.
  Promotion and delivered validation remain open.
- **2026-10-13** -- The scoped removal-fixture repair merged as #6530 after
  an Approved review with zero findings, green current-head CI and the
  required workflow guard. The shared service-alias repair #6506 is still
  open; wait for its owner and an actual promotion before beginning rollout.
  No release or installed-source evidence is inferred from these test repairs.
- **2026-10-13** -- The complete agent-worktrees release suite passed in
  validation run `38406702688`, confirming the accumulated test repairs in
  the real hosted full lane. Promotion then failed at first-party artifact
  validation with a candidate payload-hash mismatch, tracked and already
  claimed in #6535. Its owner is investigating Git checkout line-ending
  representation while preserving exact artifact validation. Do not duplicate
  that repair or claim the successful engine job establishes publication.
- **2026-10-13** -- Artifact repair #6537 is open under the existing driver.
  A newer full Manager gate also reported a routing-authority lock timeout
  during stale-breadcrumb recovery, extending the observed cases tracked by
  #6490. Recorded the exact job and failure without claiming its root cause
  or a same-snapshot rerun is proven. The failed gate remains failed; no
  authority check, timeout or promotion requirement is relaxed.
- **2026-10-13** -- The operator explicitly authorized the existing artifact
  driver to open the reviewed workflow-only main bootstrap, excluding direct
  pushes, admin merges and validation waivers. That bootstrap merged as #6558;
  the independent service-alias repair #6506 also merged. The artifact driver
  then repaired six incomplete launch-record fixtures in #6578 while retaining
  the production execution-space fence. A pending release still uses an older
  snapshot with those same six failures; do not duplicate the merged repair.
  Await promotion of a containing validated snapshot before rollout.
- **2026-10-13** -- The containing release snapshot exposed four additional
  incomplete child-record fixtures in obligation settlement, tracked in
  #6587. Replace their owner-only namespaces with real existing WorktreeRecord
  instances carrying child project, machine and platform identities; preserve
  the parent settlement and no-op assertions rather than bypassing the fence.
  The complete contained obligation-settlement and independent execution-space
  suites pass all 122 cases. The four settlement contracts also run in the
  fast guard lane; production code and fence behavior are unchanged.
- **2026-10-13** -- The obligation fixture repair merged as #6588 after an
  Approved current-head review with zero findings, successful CI and the
  workflow guard. Review correctly required ownership to be passed into
  record construction, not assigned afterward. The regression now reloads
  the child and asserts persisted ownership and derived controller provenance;
  all 122 contained settlement/execution-space cases pass with that correction.
  Await the containing full release; installed rollout and real Picker proof
  are not yet established.
- **2026-10-13** -- The containing release run `38479483177` passed all
  nine full plugin suites, including Agent Worktrees. Promotion then refused
  because the enrolled Agent Bridge coverage baseline was missing. That
  distinct evidence-transfer blocker is already routed through the existing
  #6535/#4876 release driver. Do not waive the coverage gate or treat the
  successful suites as a completed promotion; installed proof remains pending.
- **2026-10-13** -- The operator clarified that no release driver was active;
  this session took release stewardship instead of relying on stale claims.
  Prepared the exact reviewed #6611 workflow-only main bootstrap and exercised
  its ten adapter contracts against the actual main-based workflow. Publication
  found the pre-push hook's hardcoded dev comparison range; the operator
  explicitly authorized a temporary main-range correction retaining both
  unchanged identifier and pushed-blob checks. No review/CI bypass was allowed.
  Before publication completed, canonical #6619 merged that exact bootstrap.
  No duplicate PR or remote branch was opened. The unused bootstrap child was
  aligned and finalized through managed commands using the actual #6619 landing
  receipt; temporary tooling and hook overrides were removed. Continue driving
  the real containing release and the original rollout/Picker completion gates.
- **2026-10-14** -- Release candidate #6631 merged as
  `f5e4e30353a076aec74781e6ef5cfe5df474e939`, composed from validated dev
  `e22e05cc54be20f5438a460cb6b192de238e569c`. The engine admission/transport
  and both Manager prompt-action source blobs match that snapshot exactly.
  Artifact publication then failed in the content-only tree comparison,
  tracked and claimed in #6632. A real Git regression reproduces its failure
  when the merged tree's bookkeeping differs from the trusted checkout.
  A local index-plumbing candidate fixed the comparison without touching
  caller files or the real index and kept publisher errors explicit.
  Rollout stays blocked until actual artifact publication succeeds.
  The complete affected promotion/publisher portfolio passes all 212 cases.
  Initial whole-file native runs exceeded local budgets; reran the actual
  root-tool CI invocation in bounded eight-function groups, each retaining
  its 300-second containment limit, with no omitted cases or changed assertions.
  The original failure is reproduced before the index-plumbing correction.
- **2026-10-14** -- During local validation, the canonical publisher driver
  independently merged the same root-cause repair as #6633. No competing PR
  was opened. The operator explicitly chose the merged canonical repair;
  the alternative implementation and diagnostics remain on a local backup,
  not in the release path. Continue from the canonical source and drive
  actual immutable asset publication before installed adoption. Local
  validation of an unpublished alternative is not shipped evidence.
- **2026-10-14** -- Canonical #6633 reached main through promotion #6642,
  but that run's publisher still executed its older pinned trusted checkout
  and repeated the already-fixed refusal. The operator explicitly authorized
  one fresh normal validation/promotion run from repaired trusted main.
  Dispatched run `38524772148`, pinned to
  `f327cf44793c60321bc205eddadccaeda3d5ee43`; every normal validation and
  evidence gate remains enabled. No failed-job retry or force promotion was
  used. Await its actual artifact-publication receipt before rollout.
- **2026-10-14** -- GitHub coalesced the authorized pending manual run into
  automatic run `38525777621`, pinned to the same repaired trusted main;
  no second manual dispatch was requested. The older active run exposed six
  profile-assignment registration fixture failures, grouped in #6657 with
  signatures #6651 through #6656. The fixture now supplies the same test data
  to registration's dedicated config reader while retaining the real mutation
  fence and original lifecycle/call-count assertions. All 211 assignment,
  registration and execution-space cases pass; all six registration contracts
  are also selected and pass in the fast guard lane. One initial unchanged
  native contention case hit its existing lock deadline, then passed alone and
  in the complete rerun; #6663 records that intermittent evidence without a
  timeout increase or CI waiver. Actual artifact publication remains required.
- **2026-10-14** -- Registration fixture repair #6667 merged as
  `8bc763c230319e3b3e1e134fa7b8cefeac379f3c` after the exact head received
  an Approved review with zero findings, successful CI, workflow guard and
  source attribution. The failed preceding full release still used an older
  snapshot with the same six fixture signatures. Await a containing full run
  and actual immutable asset publication; source promotion alone is not
  installed or delivered Picker evidence.
- **2026-10-14** -- The next containing full run passed the assignment
  registration tests and exposed one separate exact-session scan fixture,
  tracked in #6675. It now reuses the existing shared authority setup and
  tracking directory rather than patching only the previous policy loader.
  The first integration attempt also tried to create that already-owned
  directory; reusing the supplied fixture resolves it. All 186 register,
  deregister and independent execution-space cases pass, with the exact
  deterministic-order test also selected in fast guards. Production
  registration, authority and scan behavior remain unchanged.
- **2026-10-14** -- Exact-session fixture repair #6682 merged as
  `be910de4f60b4e24a0c77e4d8971c46baa0d9330` after its exact head received
  an Approved review with zero findings and all CI, workflow and attribution
  gates succeeded. Continue through the containing full release and actual
  artifact publication; the original delivered Picker and resource-lifecycle
  completion gates remain open.
- **2026-10-14** -- Newer dev validation exposed a separate budget-guidance
  declaration/output-free-hook integration failure. Exact logs match #6699;
  its canonical repair #6701 is currently open under a verified active owner.
  Retain release stewardship here while following that bounded repair rather
  than opening a competing change. The prior exact-session failure was from
  an older snapshot and is already repaired. No validation gate or publication
  requirement is waived while the containing dev build is blocked.
- **2026-10-14** -- Budget-guidance repair #6701 merged with green checks.
  The next full engine gate passed the earlier registration groups and found
  three receiving-authority fixture mismatches in terminal conclusion and
  owning-directory lookup. Exact current ownership is confirmed in #6212 and
  #6693, with actively updated canonical PR #6696 covering that family.
  Follow its actual reviewed fix without duplicating or weakening the
  terminal-managed and cross-project assertions. Artifact publication and
  installed validation remain open.
- **2026-10-14** -- After the author-owned feature remained in review and
  its owner was not reachable through the local registries, the operator
  explicitly authorized a separate fixture-only break-fix. The original
  #6696 feature, branch, review and PR remain untouched. The bounded change
  supplies existing terminal and owning-directory test data to the dedicated
  registration authority reader and adds the three unchanged behavioral
  contracts to fast guards. Keep every normal review and CI gate.
  The complete affected terminal, tracking, registration-write and independent
  execution-space modules pass all 414 cases in bounded groups; the three
  targeted fast guards also pass. A stale negative-Resume seed mock was updated
  to accept the production verdict keyword, preserving its refusal/write
  assertions. An initial broad native run exceeded a GC-test deadline; that
  unchanged case and the complete grouped portfolio subsequently passed with
  the original deadlines. No production behavior or timeout was changed.
- **2026-10-14** -- While #6730 was reviewed, the standalone data-setup fix
  independently landed as #6726. Preserve its canonical setup and updated
  comments rather than reimplementing them. Narrow #6730 to the distinct
  remaining negative-Resume mock contract and fast-guard coverage, with its
  own refreshed review and checks required after rebasing. The unrelated
  #6696 feature and its open review remain intact.
  The narrowed current-base selection passes all five fast guard cases,
  including both real late-owner refusal branches; canonical fixture data
  and comments from #6726 are retained unchanged.
- **2026-10-14** -- Narrowed repair #6730 merged as
  `3fc5f5b1812c3cab05d26e33eb8d3551be30437c` after current-head Approved
  review with zero findings and successful CI, workflow and attribution gates.
  The separate first-install timeout #6727 already has an active diagnosis
  owner and merged fixture-isolation repair #6734; do not duplicate that work
  or infer Linux acceptance from its Windows checks. A newer containing full
  run is still active and has exposed an agent-logger failure requiring its
  own literal diagnosis. Immutable publication, authorized installed rollout,
  delivered Picker conversation evidence and managed cleanup remain open.
- **2026-10-14** -- Literal completed-job logs for full run `38633055933`
  pin its validation snapshot to `ee4e6b0063dff7eafed04942feec10974de4f52c`,
  before #6730. Its two negative-Resume mock failures therefore do not justify
  another Worktrees patch. The separate agent-logger PowerShell classifier
  timeout is already under active diagnosis in #6747; coordinated there and
  retained normal release stewardship without duplicating its repair or
  increasing a deadline. Preserve the original publication and delivered
  conversation gates while following that canonical repair.
- **2026-10-14** -- Logger harness isolation #6756 merged through its own
  review and checks. The next full run, `38637663769`, pins
  `c45b1430b53a387ddb588ab5c9708d35273393e5` and passes agent-logger, but
  exposes a distinct create-plan project-scoping fixture failure. Claimed
  #6759 after checking for an existing repair. Its record mock omitted fields
  now consumed by the real creation path; derive a real `WorktreeRecord` from
  the shared fixture and preserve the selected creation identity instead.
  The unchanged selected-project versus stale-ambient-project assertions pass
  in all four contained module cases. Mark the affected behavior as a fast
  guard; production creation, authority and deferred admission remain unchanged.
  This fixture-only repair does not exercise a new install or external venue;
  normal full release and delivered Picker evidence remain separate gates.
- **2026-10-14** -- Create-plan fixture repair #6763 merged as
  `95d9d6b92208fa79f49fc424cface20602deab57` after its exact head received
  Approved with zero findings and all required CI, workflow and attribution
  gates succeeded. All four affected module cases and the selected fast guard
  pass under the unchanged containment budgets. Resume normal containing
  release stewardship; the separate artifact-ordering PR #6697 remains with
  its active author and review. Neither fixture merge nor a source promotion
  proves immutable asset publication, installed source identity or the still
  outstanding delivered Picker New/cold Resume conversation contract.
- **2026-10-14** -- Containing full run `38645036098` pins
  `5d08c74d7185dcaf3b3f5c61af97c997576e427f` and passes the repaired
  create-plan fixture, but exposes #6769: Git index discovery can consult a
  FIFO `info/exclude` before the instruction publisher validates that input.
  Strengthened the existing portable refusal test and reproduced its ordering
  failure before the fix. Validate the Git-resolved exclude path before index
  queries, retaining the original fresh read immediately before appending the
  rule. No timeout, assertion or authority boundary is weakened.
  The complete copy module passes 90 Windows cases with five native POSIX
  skips and all 95 cases on native POSIX, including real FIFOs with no writer
  and a fresh-exclude regression preserving independent ignore-rule changes.
  The latter uses current-source dependencies after an older test cache proved
  incompatible; no installed payload or source candidate was modified. Promote
  the portable refusal cases and native FIFO cases to fast guards. Normal
  review, containing publication and delivered Picker proof remain open.
- **2026-10-14** -- FIFO ordering repair #6773 merged as
  `23367596f6caabeee879df9bfb1813cdc6ad895c` after current-head Approved
  with zero findings and successful full required CI, workflow and attribution
  gates. A lost review-request response was checked against the provider before
  any retry; the actual review ran on the unchanged candidate. Full run
  `38647658802` still validated the older source and repeated the same FIFO
  failure, so it is not evidence of a second regression. A newer normal full
  run is queued. The separate dispatch reconnect fixture #6771 is owned by
  active PR #6775; coordinated without copying its repair. Artifact-ordering
  #6697 also remains with its active author. Continue through actual immutable
  publication and the original installed Picker proof and cleanup gates.
- **2026-10-14** -- Full run `38652751643` validates exact dev
  `23367596f6caabeee879df9bfb1813cdc6ad895c` and clears the FIFO group,
  then fails the optional-profile creation fallback assertion in #6780.
  Canonical repair #6781 was already reserved and published by its existing
  owner: the test must follow both real admission and setup command layers
  before checking the unchanged Copilot model, environment and no-assignment
  assertions. Follow that current-head review and CI without a competing patch.
  The workflow's promotion job reports success only because its admission step
  exits cleanly with `proceed=false` on the failed full suite; build, attestation
  and publication are skipped. This is a correctly refused release, not a new
  source promotion or immutable-asset receipt.
- **2026-10-15** -- Canonical optional-profile fixture repair #6781 merged
  as `2e1d19f58789254307fafaf42afc47c4b2de00b2` after its exact head received
  Approved with zero findings and its checks completed successfully. A newer
  normal full run is queued. Continue tracking its actual validated source and
  publication result; dispatch reconnect #6775 and artifact ordering #6697
  remain with their active owners. No updater or delivered Picker validation
  has started, and the original completion gates remain open.
- **2026-10-15** -- Full run `38658267992` validates
  `2e1d19f58789254307fafaf42afc47c4b2de00b2`, clears the corrected fallback
  case, then exposes #6784: a repository-probe test still mocks headless options
  on the old direct caller rather than the shared `git_ops.git` owner.
  Correct the test's seam and assert the complete current capture/timeout
  options plus repository-identity environment isolation; production code
  remains unchanged. All 21 registered-resolution and real inherited-Git-context
  cases pass on Windows and native POSIX, including the native first-touch CLI
  contract. Add the corrected probe to fast guards and retain the ordinary
  review, full release, immutable publication and installed Picker proof gates.
- **2026-10-15** -- Repository-probe guard repair #6785 merged as
  `4f7b62facbe44df61f159dcde9b1afb11d34b3ea` after its exact head received
  Approved with zero findings and full required CI, workflow and attribution
  gates passed. Continue the normal release lane and inspect the remaining
  not-yet-cleared Worktrees groups locally under the unchanged containment
  budgets, rather than waiting to discover each later fixture mismatch in a
  separate full-release cycle. Local tail evidence is supplementary, not a
  replacement for a containing hosted release or immutable publication.
- **2026-10-24** -- Attempt-journal storage foundation #8505 merged after
  current-head approval with no findings and required CI. The protected store
  is independent of launch authority and is not yet wired to confirmation or
  target receipts. Continue the linked journal's responsive pre-effect
  integration; negotiated native SSH transport and all original installed
  conversation/exactly-once/cleanup gates remain open. No runtime rollout or
  delivered prompt-loss repair is implied by this source merge.
- **2026-10-24** -- Attempt-journal confirmation #8542 merged with current-head
  approval, resolved feedback and required CI. Known-target New/cold Resume
  intent now persists before dismissal/effects; explicit mock actions remain
  outside persistence. Unresolved-locality refusal recording is the next
  linked-journal item, followed by outcomes and target receipt correlation.
  The linked journal and this parent remain Active. No promotion/rollout,
  restored SSH native-seed transport, installed prompt-loss repair or original
  conversation/exactly-once acceptance is claimed by this source milestone.
- **2026-10-24** -- Prepared the linked journal's unknown-locality refusal slice:
  explicit unknown metadata, backward read compatibility and atomic validated
  upgrade; confirmed refusals preserve selection/options without launch or
  replacement intent. Focused action/storage checks and native Windows upgrade
  crash checks pass. This is pending review, not a release or installed proof.
  Continue outcome/target receipt correlation and negotiated target-owned SSH;
  both efforts and all original delivered acceptance gates remain Active.
- **2026-10-24** -- Linked-journal unknown-locality refusal slice #8574 merged
  after final-head approval and green required CI. Confirmed refusals now retain
  truthful unknown locality, target and options without launch or new replay
  authority. Continue Phase 3 outcome correlation through existing owners and
  restore the negotiated target-owned SSH native fence. No containing release,
  authorized rollout or installed conversation acceptance is implied.
- **2026-10-24** -- Linked-journal Manager observation slice #8588 merged after
  final-head approval, resolved AHP/Unicode findings and green required CI.
  Manager observations now retain resolution, launch, Restore, embodiment
  and provider handoff boundaries, without asserting allocation, Copilot start
  or prompt acceptance. Engine/target/native correlation and the negotiated
  SSH native-seed fence remain the next executable work. Both efforts stay
  Active; release/authorized rollout and original delivered conversation,
  exactly-once and cleanup gates remain open.
- **2026-10-24** -- Prepared negotiated target-native SSH admission: version-2
  envelopes require the matching native capability flag before configuration,
  bind the saved Manager attempt ID in the existing target receipt, and retain
  native fencing on the identity-only launch hop. Structured mutation handlers
  advance to protocol 8 rather than allowing an older daemon to drop the new
  metadata. Version-1 reads remain compatible. Remote New/cold Resume composers
  are restored through this contract, without host-local remote-leg lookup.
  Target daemon replay/provider checks, encoded Windows transport, Manager
  client/observation and UI checks pass; adjacent hosting fixtures now create
  the real workspace and pass mandatory project/hosting identity. Current-head
  review/CI, native POSIX transport qualification and containing-release/
  authorized installed conversation acceptance remain open. This is source
  progress, not a delivered repair or completion of either parent effort.
- **2026-10-24** -- Negotiated native SSH slice #8605 merged after current-head
  approval with no findings and green required CI. Source now restores remote
  New/cold Resume prompts and binds the linked journal's saved attempt to target
  admission without weakening native-provider or duplicate-delivery fences.
  Both canonical plans were re-read: installed-source qualification, full
  engine/native outcome evidence, original conversation/exactly-once delivery,
  release/authorized rollout and owned-resource cleanup remain open. Continue
  the linked journal's local/seedless and native-owner correlation next.
