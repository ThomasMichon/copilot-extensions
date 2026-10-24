---
visions:
  - visions/plugins/agent-worktrees
  - visions/plugins/agent-bridge
---

# Repository Agent Exposure Policy

- **Slug:** `repository-agent-exposure-policy`
- **Repo:** copilot-extensions
- **Branch(es):** source-only intent and implementation PRs to `dev`
- **Created:** 2026-10-23
- **Status:** Active; selected adopters enforced; intrinsic launch-command correction in progress
- **Vision:** extends [agent-worktrees](../../../visions/plugins/agent-worktrees/README.md)
  repository identity/contribution posture and
  [agent-bridge](../../../visions/plugins/agent-bridge/README.md)
  workspace allocation/session embodiment with a repository-owned hosting veto.
- **Umbrella issue:** [#8332](https://github.com/ThomasMichon/copilot-extensions/issues/8332)
- **Related work:** #5674 (machine identity/project enumeration), #7962 (native
  mux launch contract), #356 (project adoption); those owners retain their work.

## Guiding Intent

A repository may participate in the tool suite as a source repository without
being an agent-hosting harness. Reuse `expose_agent: false` as its explicit,
repository-owned negative policy. Managed callers must honor that boundary
rather than treating a registered checkout, an adopted project, or review-agent
instructions as permission to start an authoring agent there.

Keep source worktree allocation, editing, tests and PR operations usable from an
external harness. Preserve externally hosted Copilot Code Review. This is a
suite-managed hosting contract, not an OS-level prohibition on a human running a
vendor CLI.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation coordinator | Own effective policy, integration and completion | #8332 and source-only implementation PRs |
| Worktree boundary participant | Inventory adoption, Manager and native launch boundaries | #8332; coordinator-assigned scope |
| Bridge boundary participant | Inventory creation, resume and receiving host boundaries | #8332; coordinator-assigned scope |
| Autonomous/provider participant | Inventory dispatch, handoff and venue receivers | #8332; coordinator-assigned scope |

## Coordination

- **Topology:** independent, source-only worktree PRs; no agent hosted in this
  repository is needed to propose or implement the policy.
- **Host (owns PRs):** implementation coordinator, operating from an external
  eligible harness; the source checkout stays sessionless.
- **Delegates:** bounded, non-overlapping evidence and implementation slices;
  the coordinator owns shared interfaces and integration. Existing related-work
  owners are not redirected.
- **Handoff:** this effort and #8332 retain all implementation, release and
  adoption obligations across context boundaries. A merged slice does not close
  the umbrella; private elaboration review is not a prerequisite.

## Context

The current machine-local `projects.yaml` field `expose_agent` filters Bridge
discovery; `repos.yaml` uses `agent` when projecting local exposure into project
registration. These are not already a repository-owned, consistently enforced
hosting veto. The reviewed implementation and repository marker add that
contract; release and running adopter enforcement remain separate gates.

Use the existing repository configuration and resolution rules documented in
[configuration](../../../docs/configuration.md#in-repo-committed), including
canonical `.copilot-extensions/agent-worktrees/config.yaml`, supported legacy
paths and marketplace scope. Do not create a competing Bridge policy file or
infer hosting permission from the presence of agent definitions.

This is a **vision extension**, not merely a visibility fix. Before implementing
the stronger behavior, review the corresponding standing-intent amendment.
Existing as-is documentation remains accurate until runtime behavior changes.

## Request

The operator's relevant instructions, verbatim:

> Never create agent worktrees under copilot-extensions; it doesn't support an agent

> worktree-manager should have refused that, as copilot-extensions should not be registered as a project, only a worktree-class repo

> Let's consider providing a config marker that can be applied to a repo (that is aware of copilot-extensions) that specifically blocks bridging into it. Basically, a `.agent-worktrees/` or `.agent-bridge/` value that callers can check for which indicates that it doesn't bring its own agents.
> To be fair, copilot-extensions *does* have somewhat of an agent, used by Copilot Code Review, but that is its only purpose. copilot-extensions is not intended to be a harness of its own, or used to author itself.

> In the projects.yaml, we support an expose_agent field, I believe. Can we match that naming/convention for consistency?

> Great. Make an effort and add this

The operator confirmed the slug `repository-agent-exposure-policy`. The initial
assignment was planning-only. Subsequent execution authorization, verbatim:

> Great. Drive the implementation

The reviewed plan now governs execution. Private elaboration review is not a
dependency. Implementation sequencing and additional safeguards/tests below
remain visibly agent-recommended; the operator authorized driving this effort,
not destructive live reconciliation or unrelated release-repair work.

The operator clarified the discovery defaults during implementation, verbatim:

> Yeah keep in mind this is specific repos opting *out* of agent discovery; by default assume all repos could have an agent when checking them directly. That said, repos.yaml and related.yaml need to opt *in* to declaring an agent, and projects.yaml of course defines only repos expected to launch into Copilot and use Worktree Manager. copilot-extensions is a worktree-class related repo only, no agent

The operator corrected the implementation interface, verbatim:

> So, we introduced a bug, via a `--require-agent-hosting` flag on agent-worktrees; that shouldn't exist. Instead, only the commands which normally would launch copilot should internally enforce this, no flags necessary

## Plan

### Phase 0 - Reviewed proposal

- [x] Capture the request and accepted `expose_agent` naming; claim #8332.
- [x] Land the proposal through the repository's review gate. The planning
  assignment ended there; subsequent execution authorization is recorded above.

### Phase 1 - Intent and effective policy

_Agent-recommended implementation sequence; execution authorized._

- [x] Review a vision amendment distinguishing source repositories from
  agent-hosting projects, before implementing the stronger hosting contract.
- [x] Define one effective repository-policy resolver, consumed through existing
  plugin boundaries without introducing a second registry or mandatory runtime
  dependency between otherwise independent plugins.
- [x] Add strict boolean `expose_agent` repository configuration using existing
  canonical/legacy path and marketplace resolution. An explicit repository
  `false` is a veto over stale local `true` and explicit named targets; a `true`
  or absent key does not independently grant adoption or launch authority.
- [x] Preserve existing machine-local exposure controls and absent-key
  compatibility. Do not silently reinterpret every historical
  `projects.yaml expose_agent: false` as the new repository-owned veto.
- [x] Preserve the operator's distinct discovery defaults: direct repository
  checks have no implicit veto; `repos.yaml` and `related.yaml` require explicit
  agent opt-in; `projects.yaml` contains Copilot/Manager launch projects.
  The repository's own source-only posture and live catalog acceptance remain
  Phase 3 obligations.
- [x] Refuse malformed explicit policy or an unreadable required policy source
  with an actionable error rather than treating it as permission to host.

### Phase 2 - Managed adoption and launch boundaries

_Agent-recommended implementation sequence; requires Phase 1._

- [x] Apply the effective veto before project-adoption side effects and before
  managed authoring launch effects: Manager/picker, Bridge creation/embodiment,
  resume/re-embodiment, dispatch/autopilot and managed handoff successor paths.
  Inventory actual entry points during implementation; do not assume one front
  covers the others.
- [x] Check caller planning and the receiving launch boundary, including
  supported remote/provider routes. A stale discovery result or an explicit
  target must not bypass receiver enforcement; fail visibly when a required
  receiver cannot uphold the policy.
- [x] Preserve source-only workspace allocation and source-control lifecycle
  operations. Separate `create worktree` from `launch agent` even when a caller
  currently composes them.
- [x] Preserve external Copilot Code Review and review-only instructions without
  a forgeable generic `purpose=review` exemption to managed authoring launch.
- [x] Return an actionable refusal identifying the policy and suggesting an
  eligible external harness, without silently selecting or spawning one.

### Phase 3 - Adoption and state-preserving reconciliation

_Agent-recommended follow-through; live changes require separate authorization._

- [x] Add the source-only repository's explicit denial after generic enforcement
  and source-operation compatibility are qualified. Do not add an inert marker
  and claim that installed hosting is already prevented.
- [ ] Complete the worktree-class, related-only posture with no advertised agent
  on authorized adopters. Preserve existing registrations and other state until
  necessary reconciliation is explicitly approved.
- [x] Provide a supported preview/reconciliation path for conflicting existing
  project registrations. Preserve sessions, history, tracking and claims; no
  automatic termination, deletion or unregistration.
- [ ] Update authoritative configuration, adoption and launch documentation;
  publish through the normal release flow and validate authorized adoption.
  Keep release, installation and actual enforced behavior distinct.

The earlier coverage-packet release blocker (#8500/#8516) cleared through fix
#8508 and its governed Main helper adoption. The original enforcement is
released and selected installed negative checks pass. Final discovery
acceptance and the corrected Worktrees launch interface remain open; a release
of the earlier implementation does not close those gates.

### Phase 4 - Intrinsic Worktrees launch enforcement

_Operator-requested correction; no new hosting-policy option._

- [x] Remove the Worktrees hosting flag and its Manager, remote and Dispatch
  caller plumbing. Keep policy enforcement intrinsic to the actual Copilot
  launch/resume paths; source and inspection operations receive no launch flag.
- [x] Prove unflagged launch denial, ordinary remote inspection and automatic
  selected-receiver compatibility checks without a user-controlled policy flag.
- [ ] Land the correction through normal review/release and verify authorized
  installed acceptance before declaring this effort complete.

## Validation Plan

_Agent-recommended proofs; record results as implementation proceeds._

- [x] Exercise strict policy parsing and canonical/legacy/marketplace resolution:
  absent, true, false, malformed and unreadable configurations; no
  success-shaped permission fallback.
- [x] Prove repository `false` defeats stale local `true`, explicit targets and
  cached discovery, while local exposure restrictions still apply and absent
  policy preserves compatibility without auto-adopting a project.
- [x] Prove an absent repository veto permits direct agent eligibility checks
  without advertising ordinary `repos.yaml` or `related.yaml` entries as agents;
  explicit catalog opt-in remains subject to the repository veto.
- [x] Use isolated fixtures to assert denial occurs before adoption writes,
  session/process/mux creation or seed submission for each inventoried managed
  entry point. Test caller and receiver boundaries, including remote/provider
  routing and unsupported enforcing receivers.
- [x] Prove source worktree allocation, edits, bounded tests, PR creation and
  completion remain usable under an external harness; externally hosted
  Copilot Code Review remains usable. Agent/review files confer no hosting
  permission and a generic review-purpose parameter cannot bypass denial.
- [x] Prove a policy introduced after a session exists does not kill it,
  discard its state or erase custody. Newly attempted managed
  resume/re-embodiment is refused; read-only inspection and deliberate
  state-preserving migration remain available.
- [x] Run targeted bounded suites and the applicable clean-room/native
  Windows/Linux entry-point proofs before release/adoption; no production
  agent launch as an unapproved test.
- [ ] Verify authorized installed policy enforcement and unchanged source/review
  flows before declaring implementation complete. Every Plan and Validation
  item must be resolved or explicitly transferred; a merged plan is not delivery.

## Proposal

Merged repository configuration; **not yet released or installed**:

```yaml
expose_agent: false
```

The new work is consistent resolution and enforcement, not another flag name.
The implementation must not special-case a repository name or confuse
machine metadata/project enumeration with hosting eligibility.

Mixed-version enforcement is part of that contract. Managed producers require
a receiving CLI which recognizes hosting enforcement, and Bridge additionally
checks the running daemon's independent versioned capability before every
request attempt. Installing a current caller is not evidence that its receiving
daemon or separately resolved Worktrees owner has upgraded.

## Journal

### 2026-10-23 - Planning intake

- Confirmed the existing exposure vocabulary and captured the operator's naming
  correction. Rejected introducing a separate `agent_hosting` key.
- Opened #8332 as the planning coordination token. Prepared this proposal and
  its validation obligations; no runtime, config, registry or agent changed.
- Runtime implementation, vision amendment, deployment and live reconciliation
  remain unassigned and require execution authorization.
- Proposal #8335 merged after a current-head Copilot approval with no findings
  and passing checks. Phase 0 is complete; #8332 and the effort remain open.
  The next slice is the separately authorized Phase 1 vision/policy work, not an
  implicit runtime rollout.

### 2026-10-23 - Execution authorization and boundary inventory

- Execution authorized after proposal #8335 and planning-status #8338 merged.
  Claimed the implementation stretch on #8332; private elaboration review is not
  a dependency.
- Inventoried adoption, Manager/picker, native/remote launch, Bridge receivers,
  dispatch, handoff and venue-provider paths. Existing resume seed preparation,
  recovery setup and provider rejoin can precede ordinary launch checks; those
  paths require explicit pre-effect admission, not discovery filtering alone.
- Preserve plain source allocation separately from launch-plan construction,
  and preserve saved handoffs independently of successor arming.
- Standing-intent amendment is the first review gate. Effective policy and
  boundary implementation follow only after that amendment lands; no live
  session, registry or installed runtime changed during inventory.
- Standing-intent #8350 merged after current-head Copilot approval with no
  findings and passing required checks. Implementation now follows those
  reviewed boundaries; the umbrella remains open.
- Added one independent, vendored `repository-policy` library for fresh
  canonical/legacy/selected-marketplace resolution. Its 33 focused tests pass
  through the bounded runner in a disconnected disposable container. Caller and
  receiving-boundary integration, release and installed enforcement remain open.
- Clarified repository opt-out versus catalog opt-in during implementation.
  Direct eligibility checks do not require an affirmative repository marker;
  catalog membership alone does not advertise an agent. Compatibility corrections
  and their regression proofs remain in progress; nothing is installed yet.

### 2026-10-23 - Boundary compatibility qualification

- The shared resolver and complete success-response schema pass 67 isolated
  tests. Source-independent wheel/import qualification also passed.
- Focused container admission and existing launch coverage pass 97 tests;
  CodeSpaces admission, exact worktree selection and venue coverage pass 204.
  Managed handoff coverage previously passed 108 tests.
- Expanded Bridge coverage now passes 567 tests, including privileged receiver
  capability and request-provenance regressions. Dispatch passes 266 tests,
  including interrupted-startup custody preservation. Corrections preserve the
  substantive refusal, validation-order and custody assertions.
- Worktrees/Manager now distinguish direct opt-out eligibility, explicit
  repository-agent declarations and launch-project adoption. Qualification is
  still pending, including an identified Manager pre-admission preparation path.
- Prepared a credential-free installed-CLI scenario covering fresh denial,
  review-label refusal, adoption-state preservation and real source-only Git
  worktree allocation. It has not yet run against a stable integrated candidate.
- No implementation has been published, installed or declared enforced. Native
  Windows proof, release/adoption and state-preserving registration review remain
  open; no live registry, session or custody state has been changed.

### 2026-10-23 - Integrated continuation qualification

- Continued the existing source candidate under the same reviewed effort and
  completion gate. Source work remains controlled from an eligible external
  harness; the source checkout remains sessionless.
- Completed the prior Worktrees and Manager compatibility corrections, retaining
  the recorded 432 passing Worktrees cases with five platform skips and 261
  passing Manager cases with 44 platform skips. These Linux results do not
  replace native Windows qualification.
- Independent source review found and corrected three integration defects:
  project-only Bridge admission now uses read-only owner metadata rather than
  launch resolution; direct CodeSpaces stdio admission precedes claims, relay
  tokens and provisioning; and local topology advertisements honor current
  repository denial without changing catalog opt-in. The named correction
  follow-up found no remaining high-confidence defects.
- The additional disconnected correction cohorts pass 199 Worktrees/record
  cases, 261 Bridge/discovery cases, and 96 CodeSpaces cases with three platform
  skips. Five focused test-runner contracts pass. Ruff, module-size,
  install/version, generated payload, shared-vendor and documentation gates pass.
- The credential-free clean-room scenario now passes all three phases, including
  actual installed owner-prefixed project and exact-worktree policy queries,
  denied adoption with unchanged registration state, and real source-only Git
  worktree allocation. This is an isolated candidate installation, not adopter
  rollout or permission to change live registrations.
- Native qualification, reviewed implementation publication, the repository's
  own denial marker, release and authorized adoption remain open. Existing
  sessions, claims and registration state remain unchanged.
- Integrated the current upstream cold-recovery custody changes without moving
  repository denial behind recovery writes. An independent integration review
  identified a stop arriving during the new policy await; capture and
  revalidation now preserve the original stop generation across that await and
  lifecycle-lock acquisition. Five deterministic delayed-admission regressions
  pass without a native launch, reconnect prompt, cleared stop or re-enabled
  recovery.
- Qualified the coupled cold-drain/recovery and hosting cohort. A replacement
  exiting between liveness observation and native reference acquisition now
  produces the existing explicit refusal while retaining custody; a
  deterministic regression preserves the unknown reconnect and stop token.
  The cohort passed 249 cases before the additional five stop-generation
  controls. A cohesive preflight extraction also preserves the module cap
  without widening its baseline; 71 creation/policy cases pass.
- Four additional stopped-handoff controls preserve successful consumption of
  the originally authorized stop token while refusing a newer stop during brief
  generation. The fence keeps the original session and notice generation rather
  than recapturing authority after resume.

### 2026-10-23 - Native qualification and public capture checkpoint

- Native Windows source qualification passes the shared resolver (67 cases),
  Worktrees policy/owner boundaries (90), Manager (82 with 18 platform skips),
  Bridge receivers (93), Dispatch (18), and container admission (6). The selected
  source boundary code, not a deployed runtime or real model session, was tested.
- Repaired the Manager fixture's Windows Python forwarding: CRT `execv` lost
  multiline code/argument quoting and returned success before its failing child.
  Explicit bounded subprocess forwarding now preserves code, stdin and exit
  status; the actual PowerShell wrapper refuses malformed policy before any
  diagnostic pruning or automatic preparation.
- Direct CodeSpace admission now checks an exact available venue before SSH
  configuration can wake it, using its discovered raw identity/account without
  projecting account bindings. Six new controls plus existing attached,
  detached and SSH compatibility pass 195 disconnected cases; the final native
  policy cohort passes 21 cases.
- The public source checkpoint passes all 1,274 currently admitted unit cases.
  Canonical HTTP-47 capture and no-write verification each pass all 117 required
  model-free probes. Only owned current-generation evidence and registry metadata
  are refreshed; older generations, protocol floors and independent expectations
  remain unchanged.
- Project-root admission also refuses CWD-relative owner anchors before policy
  inspection. The stricter read applies only to the selected project; unrelated
  legacy source metadata and ordinary record readers retain their existing
  behavior. The final Worktrees/record cohort passes 202 cases.
- Phases 1 and 2 are source-complete, not merged/released/installed claims.
  The repository marker, contribution/review acceptance under it, release and
  separately authorized live adoption/reconciliation remain the next gates.
- Provider review identified two outer resume admission reads that preceded
  capture of the retained session's stop generation. Implicit prompt recovery
  and native worktree loading now use the same pre-await session fence before
  status changes, acquisition gates or downstream recovery. Four deterministic
  controls complete a newer stop during admission, with and without an older
  stop token, and prove the newer authority and dormant state remain intact.
- The coupled hosting/native-owner/cold-stop cohort passes 307 disconnected
  cases. Native-owner fixtures now provide actual existing workspaces and
  authoritative explicit execution paths; the Windows transport assertion
  checks both the outer refusal gate and the nested encoded native-resume
  command. The final Worktrees native admission cohort passes 93 cases.
- The review correction and native-owner compatibility cohort also passes 275
  cases on native Windows. Canonical HTTP-47 capture and no-write replay each
  pass 117 probes against the published correction source. Historical evidence,
  independent expectations and protocol floors remain unchanged.
- Reconciled the implementation with the upstream immutable-provenance sidecar
  migration without reverting its storage layout or historical evidence.
  Current-generation evidence is recaptured through the integrated canonical
  producer, not hand-merged across the layout change.
- Updated the existing CLI embodiment and remote-machine routing fixtures to
  model the new read-only owner preflights and mandatory hosting flag. All
  reservation cleanup controls remain; the two disconnected cohorts pass 11
  and 28 cases, including actual subprocess plan serialization and decoded
  Windows argument/exit propagation.
- The integrated sidecar-aware source passes native Worktrees (121 cases) and
  Bridge (286 cases) cohorts. Its newly published source checkpoint passes all
  117 canonical capture probes and the no-write replay; only current-generation
  owned evidence changes, retaining the upstream immutable provenance chunks.
- Trusted container ACP launch now validates and wraps the original native
  command for process-only authentication before applying the repository guard.
  The real authentication validator remains strict; the restricted transport
  retains its separate guard. The coupled container cohort passes 134 cases.
- Retained container and CodeSpace delivery journals reconcile their original
  idempotent operation before new-launch admission. A vanished journal must
  pass fresh admission before any new launch. This preserves receipts and
  custody without permitting a new managed embodiment in a denied repository;
  the strict Worktrees denial before mux probing remains unchanged.
  The CodeSpace cohort passes 146 cases; both venue cohorts include a
  journal-disappearance race proving recovery cannot become launch authority.

### 2026-10-23 - Mixed-version receiving-owner compatibility

- Provider feedback identified independently resolved legacy Bridge and
  Worktrees executables which could ignore caller-side admission. Managed
  Dispatch create, resume and potentially cold-resuming send now require
  `--require-agent-hosting`; legacy parsers refuse before launch rather than
  falling back. The ordinary managed creation/resume route is covered too.
- A current Bridge CLI also requires the actual running daemon's independent
  `repository_agent_hosting: 1` capability. The existing per-attempt protocol
  fence composes both requirements and rechecks each safe retry after endpoint
  re-resolution. Old daemons cannot inherit admission from a newer CLI.
  Historical receipt-only reads remain state-preserving.
- The disconnected correction cohorts pass 153 Bridge capability/retry cases,
  223 Dispatch command/reclaim cases, 93 owner/registry cases, and 360
  launch/parser cases with 31 platform skips. Literal-path launch fixtures use
  a real policy-backed workspace without writing to operating-system roots;
  registry adoption fixtures now provide an actual checkout. Existing command,
  reclaim, identity and exit assertions remain strict.
- Extracted the shared Worktrees launch-argument parser to its existing focused
  module rather than widening the 1,000-line cap. The source remains a
  qualification candidate; updated native proof, current semantic capture,
  accepted review/merge, the repository marker, release and authorized adoption
  remain distinct completion gates.
- Qualified that exact committed source on native Windows: Worktrees 153,
  Bridge 439 and Dispatch 223 cases pass, including actual legacy-parser
  refusal and current-client/old-daemon refusal. Each bounded job confirms
  cleanup. Canonical HTTP-47 capture and no-write replay each pass 117 probes
  against the published correction checkpoint; only the 66 owned current
  outputs change. Both contracts and all 583 fixtures validate with unchanged
  historical packets, independent expectations and protocol floors.
- A clean provider review reported no open findings. A subsequent base-branch
  conflict required integrating the upstream database schema lock before merge.
  No runtime policy code conflicted. Reconciled the registry losslessly,
  retaining both append-only histories and unchanged immutable chunks, then
  recaptured the genuine public rebased checkpoint. Canonical write and no-write
  runs each pass all 117 probes; this remains source evidence, not rollout.

### 2026-10-24 - Enforcement merged; source marker acceptance

- Implementation #8468 merged at `3c42fee5f95c56acfc1ffe2b72f1fb9cfc356f33`
  after current-head clean Copilot review and passing required CI. Read the
  canonical Plan and Validation Plan again; release, the source marker and
  installed adoption were not inferred complete from that merge.
- The supported read-only registration audit and state-preserving reconciliation
  procedure landed with enforcement. They do not mutate registrations, stop
  sessions, delete state or settle another owner's claims.
- Added the repository's negative marker in its existing
  `.agent-worktrees/config.yaml`, not a competing configuration path. The source
  guide now states the boundary. Qualification and a source-only contribution
  through external review under that marker follow before claiming acceptance.
  No live runtime, project registration or session is changed.
- The marker, policy, source-allocation and read-only audit cohort passes 88
  disconnected cases. Both contracts and all 583 fixtures still validate
  without recapture because no runtime producer changed. The marker's own
  external-harness contribution and review remain the next acceptance witness;
  the older clean room already proves real source allocation under denial.
- Source marker #8520 merged at `8647ec5cd5` after current-head Copilot approval
  with no findings and green required CI. Its source edits, bounded tests,
  managed publication, externally hosted review and normal merge all ran under
  the actual denial marker from an external harness. Five additional
  actual-checkout marker cases pass on native Windows with verified cleanup.
- Read-only adopter previews remain previews: existing registered anchor
  checkouts can still lack the new marker, so eligibility there is not proof
  of installed enforcement. Existing registrations, visibility settings,
  sessions and custody remain unchanged pending release and explicit adoption.
- Natural promotion run
  [40075117367](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40075117367)
  executed the merged implementation source. All 1,414 admitted units passed
  in 31.37 seconds, but packet admission refused `coverage.json` at 66,304,392
  bytes against the unchanged 16,777,216-byte cap. The only failed job was
  source-unit evidence; downstream qualification/publication was skipped.
  This matches existing #8500/#8516 and active fix #8508. Attached the current
  evidence to that owner rather than creating a competing fix or bypassing
  the cap. Release, installed enforcement and approved catalog reconciliation
  remain unresolved completion gates.

### 2026-10-24 - Released adoption and intrinsic-command correction

- The earlier release blocker cleared through its existing owner. Qualified
  promotion run 40130392044 and merged release #8677 publish the implementation
  and actual repository marker. No parallel repair or Main pin bypass was used.
- Authorized installed checks in selected Linux/WSL and native Windows
  environments reject managed source hosting at the actual registered checkout.
  Running Bridge receivers advertise the hosting capability. Approved
  project-only deregistration preserves the worktree/no-agent repository
  entries, state directories, all inventoried worktree identities, recorded
  sessions and claim links. A remaining cached discovery advertisement is not
  called reconciled merely because registration removal succeeded.
- The operator rejected the Worktrees hosting flag. It was an unnecessary
  receiver-acknowledgment mechanism, not permission to weaken intrinsic
  enforcement. Its unconditional remote injection also reached ordinary
  `list --json` commands, which do not launch Copilot.
- Removed that parser option and its Worktrees-targeting Manager, remote,
  structured-request and Dispatch argument plumbing. Existing launch and
  receiving guards remain unconditional. Dispatch uses the selected receiver's
  existing read-only policy contract before ordinary unflagged embodiment;
  unsupported or malformed receivers still refuse without a launch.
- The disconnected correction cohorts pass 528 Worktrees cases with 35
  platform skips, 162 Manager cases with 12 platform skips, and 111 Dispatch
  cases. They retain negative launch/seed/custody assertions, reject the obsolete
  option, and exercise real remote source-inspection command serialization.
  Native qualification, correction review/release and final discovery
  acceptance remain open; no corrective runtime was deployed from WIP source.
- Native Windows qualification of the clean correction candidate passes 561
  Worktrees cases with two platform skips, 162 Manager cases with 12 platform
  skips, and 111 Dispatch cases, with verified cleanup for every bounded job.
  Committed-diff module ceilings pass without widening; installation and both
  Bridge contracts with all 814 fixtures pass without recapturing unrelated
  evidence. Correction review, release and installed acceptance remain open.

### 2026-10-24 - Internal compatibility before launch effects

- Provider review identified two real ordering gaps after removing the option:
  an older remote receiver could accept a launch or structured prompt before
  establishing hosting support, and Manager could resolve New through an older
  engine before its later launch guard. Both are addressed internally, without
  restoring the option or adding another user-controlled hosting switch.
- Unseeded remote handoff validates the receiving owner's existing read-only
  policy answer before executing the normal command. Structured prompt
  preparation performs a separate read-only probe before the original
  admission call; the returned launcher checks again before execution.
  Inspection, status, help, plain source allocation and retirement remain
  ungated by this launch mechanism.
- Manager validates the selected engine's owner-policy contract before
  effectful resolution and rechecks before compatibility retries. For remote
  targets a local namesake's denial establishes capability, not remote
  permission; the receiving host remains authoritative for admission.
- Retained the full 7 KiB request-envelope and 28,000-character command bounds.
  The separate preflight avoids inflating the encoded admission payload.
  Explicitly bounded Manager remote prompt preparation now allows at least
  300 seconds for the 60-second policy query, 180-second admission and transport
  margin; unbounded preparation and larger explicit budgets remain unchanged.
- Linux correction cohorts pass 415 Worktrees cases with six platform skips
  and 172 Manager cases with 12 platform skips, with verified bounded cleanup.
  Real legacy-receiver probes are witnessed before assertions of zero launch,
  allocation or seed effects. Native qualification and provider re-review
  remain required; the existing Dispatch qualification is unchanged.
- Native qualification of that review correction passes 577 Worktrees cases
  with eight platform skips and 172 Manager cases with 12 skips. The real
  PowerShell receiver matrix covers allowed, unsupported, malformed and denied
  answers before a fake launch effect; all bounded jobs verify cleanup.
  Review, release and installed correction remain separate gates.
