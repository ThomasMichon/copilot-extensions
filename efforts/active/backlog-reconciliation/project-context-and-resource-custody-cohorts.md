# Project-context and resource-custody cohorts

## Status and scope

This is the complete planned intake for two existing OPEN canonical bugs,
#4173 and #4619. Their actual expanded bodies were delivered and read back
in full; the original prefixes, prior acceptance and discussion remain.
Normal review of this intake precedes separate #4205/#5003 tracking
retirements. No product acceptance is checked by this document.

The families are separate. Within each family, independently progressable
lanes share an outcome without asserting a common incident or a hard
implementation dependency. Existing implementation, native, credential,
claim and resource custodians retain their work and every original limit.
No new parent issue, registry, policy service or operational authority is
created.

## #4173 - Authoritative project context and selected-policy delivery

The existing authoritative adopted-project/repository model and its received
#2095/#2562/#4089/#4228/#5299 consumers remain. Add the complete #4205
selection-to-consumer lane, including #4206–#4209 and #68. The already-closed
subordinate trackers receive no duplicate disposition credit.

### Direct Plan

- [ ] **A1 - Model:** Accept one authoritative model, not necessarily one
  physical file. Preserve registered-but-unadopted repositories,
  participation, platform paths, source/account/policy, default branch,
  base/launch facts, overlays and each current fact's writers/consumers.
  Do not silently adopt repositories or introduce a third registry.
- [ ] **A2 - Late target (#2095):** Normalize and authoritatively resolve
  implementation targets first discovered or changed by research, triage
  or delegated results before source acquisition/repository operations.
  Preserve class/locus/delegation and provenance; do not guess a checkout,
  bypass the venue or confuse a downstream selector with this transition.
- [ ] **A3 - Contribution posture (#2562):** Derive target branch/review/
  fork/approval rules and actual contribution/landing standing primarily
  from authoritative target signals. Keep source/account/locus distinct
  from permission. Keep manual narrative to etiquette/coordination, without
  a competing manifest or compulsory schema. Deliver a concise current-work
  contribution brief through attributable session context, not only a file
  pointer or command.
- [ ] **A4 - Startup (#4089):** Deliver the addressed repository's branch
  naming, commit-message requirements AND push restrictions to its actual
  session at startup. Preserve target identity, authority and supported
  guidance delivery; neither a PR profile nor command-time notes supplies
  this whole timing/content outcome. Delivery does not grant permission.
- [ ] **A5 - Local-only sync (#4228):** Report a clean non-bare registered
  repository with confirmed missing remote as intentionally SKIPPED before
  generic fetch failure. Do not select/add a remote or suppress genuine
  authentication, transport, timeout and other repository failures.
- [ ] **A6 - Shadow state (#5299):** Preserve legacy-only entries when both
  configuration files exist through migration-on-write OR merged reads
  with canonical/new keys winning OR at minimum a loud add/doctor warning.
  Include already-thin canonical state, overlays and unrelated ownership.
  Do not delete files or assert an unproved historical first-write cause.
- [ ] **B1 - Common selection:** Resolve named projects through the router
  and all nine slugs: worktrees, bridge, ssh, dispatch, codespaces,
  containers, logger, vault and mcp. Calls from another/neutral CWD reach
  the intended target and policy; preserve valid no-selector behavior.
- [ ] **B2 - Surfaces/routing:** Preserve `<repo> <slug> ...` and direct
  `agent-<slug> --project <repo> ...` where promised, the canonical worktrees
  namespace and bare worktrees verbs. Retain one router, installed/enabled
  namespace composition, new slugs and reserved-slug/real-verb collision
  precedence. Preserve independently usable lower layers; no hidden CWD
  mutation or competing registry.
- [ ] **B3 - Semantics:** Define projectless/global verbs. Keep execution
  policy selection distinct from repository-valued record/query fields.
  Preserve aliases and reject conflicting/unresolvable explicit selection
  before governed effects, rather than silently using incidental CWD.
- [ ] **B4 - Logger (#4208):** Carry selection into actual configuration/
  policy, profile/style/output-root consumers independent of invoking CWD.
  Preserve existing machine/repository/environment precedence, provenance,
  output/landing authority and annotation/catalog `--repo` meanings.
- [ ] **B5 - Dispatch (#4209):** Carry selection into project/repository/
  task policy and existing lane/worker defaults. Keep the name/remote
  `--repo` compatibility alias functional across supported forms; define
  precedence or reject conflicts. Preserve no-selector/all-repository
  semantics, record/query fields, task references and writer/caller custody.
- [ ] **B6 - CodeSpaces (#4206):** Apply selected-host precedence through
  allocation AND reuse, connection/SSH, setup/provisioning and relays.
  Reconcile defaults, repository/Connection Owner and credential/provision
  unions. Keep host, product target and persisted venue/account separate.
  Preserve auth, claims, resource lifecycle, failure truth and direct/
  provider/router parity; parsing or config display alone is not delivery.
- [ ] **B7 - Containers (#4207):** Reach actual configuration, fleet/
  lifecycle, detached/ordinary execution, connection and login consumers.
  Reconcile explicit override, canonical/legacy CWD files, runtime fallback
  and knowledge overlays without overriding explicit config. Preserve
  account/provider choice, credentials, target identity, restricted/trusted
  admission, assignment and existing-container ownership. Selection cannot
  silently retarget a container, switch a global account or authorize it.
- [ ] **B8 - Whole parity (#68):** Preserve all nine slugs and both promised
  surfaces through actual consumers, not just parsing/help. Preserve
  standalone independence and any necessary lower-layer context handoff.
- [ ] **Vault - Bounded claim:** Keep #4205 comment6674720247 with its actual
  owner: neutral-CWD repository-scoped unlock, legacy worktree verbs,
  path-bearing operations and clear missing-context diagnostics. Its
  focused source/unit and isolated native-process tier does not become
  family-wide parity, #10668 startup configuration or a transferred claim.

### Actual global Validation Plan

- [ ] **V1:** Verify the accepted authority, reader/writer, compatibility
  and per-fact ownership decision. A joined display is not migration.
- [ ] **V2:** Reject late/changed delegated-target acquisition before
  resolution while preserving ordinary local workflows. Remote/delegated/
  unknown/refused targets cannot mutate another source or silently fall back.
- [ ] **V3:** Cover changed target policy, unknown/unavailable/ambiguous
  signals and actual actor standing, intentional narrative/compatibility,
  real current-work briefing and startup delivery of all three workflow
  rule families. Distinguish configured facts from actor-effective authority.
- [ ] **V4:** Cover local-only skip with bare/dirty/detached/diverged/
  existing-remote controls and truthful per-entry/aggregate status. Cover
  legacy-only, normal upsert, canonical-only, both-file conflicts and
  thin-canonical cases through actual list/resolve/doctor/context and
  existing writer paths; retain meaningful parse/write/refusal errors.
- [ ] **V5:** Compare named A from B/neutral CWD with target-context calls;
  cover defaults, aliases, conflicts, unknowns, global verbs, composition/
  collision and router forwarding, provenance and record/query semantics.
- [ ] **V6:** Exercise actual Logger and Dispatch selected-policy consumers,
  precedence, output/task ownership, functional aliases, unchanged defaults
  and truthful unsupported/unavailable selection.
- [ ] **V7:** Exercise CodeSpaces allocation/reuse and Containers ordinary/
  detached/fleet/lifecycle and connection/login paths. Preserve unions,
  overrides, account-target separation, restricted/trusted admission,
  assignment, credential and existing-resource boundaries and parity.
- [ ] **V8:** Validate only the bounded Vault route and diagnostic claim at
  its own stated tier; no service-startup or shared rollout gate.
- [ ] **V9:** Before #4205 tracking retirement, verify complete actual
  receiving delivery/readback against its whole source/refinements after
  this intake's normal reviewed merge. Preserve all prior #4173 rows,
  owners, permissions, source/account duties and original evidence tiers.
  No blanket ACK, all-host/native campaign or cross-lane blocking rule.

### Continuing coordination

Keep both lanes independently progressable and #4173 OPEN. A possible
#4205 NOT_PLANNED disposition retires tracking only, never the bounded
Vault claimant or any implementation/native/resource owner. No evidence
establishes a separate existing effort as whole-family owner; scoped
Configuration, progressive-disclosure and migration work is not silently
appointed. Original source descriptions/discussions remain unchanged.

## #4619 - Persistent custody and exact terminal settlement

Receive #5003's complete RC1–RC5/RCV1–RCV4, including already-received
#3003/#5772, alongside the incumbent both-store handoff atomicity and
#10372/#6751 settlement requirements. Preservation until settlement and
correct settlement are independent lanes of the same obligation lifecycle.

### Direct Plan

- [ ] **R1 - Abort/arm:** Atomically order abort and recovery-triggered arm
  for BOTH file/task stores across final check, session-state write,
  `beforeArmPickup` and note/arm. Use the reclaim-aware `.consume.lock`
  protocol or equivalent owner-correct file gate, and task-authority
  conditional admission/status CAS with conditional arm at its write.
  Abandon CAS alone or another read is insufficient. Preserve proposed/
  queued eligibility, token/candidate/consumer identity, pending claims,
  manual/no-replay policy, lock safety and truthful race outcomes. Never
  rearm a retired token, steal/expire a candidate or add parallel authority.
- [ ] **R2 - Task closeout (#10372):** After successful unconsumed task
  cancellation, settle only the same originating creator obligation after
  its Dispatch owner confirms terminal state. Preserve the observed
  manual-only/no-successor, abandoned/zero-attempt/no-owner/no-started-
  execution case; pending-ledger absence alone is insufficient. Recover
  idempotently from partial failure; claimed/running/unknown/contradictory/
  mismatched state retains protection. Preserve takeover, genuine task
  claims, PR checks and ordinary finalization; do not ignore task claims.
- [ ] **R3 - CodeSpace closeout (#6751):** Settle the originating owner-ref
  after successful deletion OR consume authoritative provider absence as
  gone AND safe through claims sweep. Either route suffices, not both.
  Preserve exact owner/ref, mutation/handoff/producer fences and safe
  recovery. Lease release/status clearing is not settlement; missing lease/
  mirror, account/network/provider errors and stopped/reusable venues are
  UNKNOWN. Credit the strict provider callback without claiming adoption.
- [ ] **R4 - Custody (#5003 RC1/#3003):** Retain substantive effort/worktree
  ownership until explicit authorized release, finalize, accepted handoff
  or verified settlement. Writer death, old/missing heartbeat, elapsed time,
  stale transport or another record's operation is not release. Preserve
  allocation/obligation identity, cross-host rescue and uncertain custody.
  Keep legitimate transient connection/admission budgets separate; the
  optional printed exact release command is not mandatory API acceptance.
- [ ] **R5 - Retained identity (RC2–RC3/#3003):** Preserve logical retained
  Containers job/admission/lifecycle identity across borrow/renewal-helper
  exit and process restart. Same-job renewal cannot reassign ownership to
  a transient PID; a heartbeat or continuously running wrapper does not
  define valid substantive custody. Prevent same-effort reborrow, elapsed
  time or local process absence from taking another job's lease. Preserve
  ordinary advisory/TTL borrowing and bounded positively-dead same-host/
  environment recovery, including delivered #2026 behavior, separately.
  Keep PID reuse/unknown/foreign liveness conservative and exact owner/
  generation fencing and explicit settlement.
- [ ] **R6 - Live use (RC4/#5772):** Preserve delivered #5776 actual target
  locks, detached ControlMasters, mux/forward and interactive users,
  `in-use`/JSON idle/busy/unknown, lease `in_use`/`live_users`, conservative
  pool/orphan overlay and BUSY diagnostics with PID/role/safe command/socket.
  Fix list/get pruning before live-user annotation; a later overlay cannot
  restore discarded lease identity.
- [ ] **R7 - Read/mutation (RC5):** Ordinary list/get cannot write away an
  unsettled retained owner. Explicit release/heartbeat or another record's
  operation must not passively settle a separate hold. Preserve accepted/
  uncertain operations, resource/lease/claim boundaries; no implicit
  takeover, expiry authority or new registry.
- [ ] **R8 - Custody:** Retain Worktrees/Dispatch/Handoff/CodeSpaces/
  Containers and detailed-effort owners, assignments, reservations, holds
  and source/resource duties. #6109 remains separately OPEN with its
  existing temporary-container owner and preallocation, exact Docker/volume
  identity, all producer paths, retained-volume, identity-conditional
  removal and original lifecycle/installed gates. Keep #5675 emitter-lease
  recovery and #3442 credential lifetime separate. No claim theft, force
  release, age expiry, token rearm, blind replay or physical/process/service
  action is authorized by this tracking intake.

### Actual global Validation Plan

- [ ] **RV1:** Race abort/recovery through both stores, immediately after
  the last check and during arm stages. Retired tokens cannot rearm and
  rightful candidates cannot be abandoned/expired/stolen; cover reclaim/
  conditional-write failures and truthful outcomes.
- [ ] **RV2:** Cover save/trigger, unconsumed manual-only abort, exact
  owner-terminal confirmation, creator reconciliation and normal finalize.
  Include partial failure/retry, genuine claims, running/claimed/unknown/
  contradictory state and PR checks; no successor/shared restart required.
- [ ] **RV3:** Through the chosen deletion OR provider-absence route,
  settle only the exact originating obligation. Cover forced/nonforced
  entry where applicable, missing mirror, account/network/provider failure,
  replaced owner/ref and producer/mutation/handoff refusal. Manual release
  or deletion of local lease state is not acceptance.
- [ ] **RV4:** At non-destructive list/get and mutation boundaries, retain
  logical custody despite dead writers or stale/missing heartbeat. Test
  explicit release, accepted handoff and confirmed settlement separately;
  do not run a live listing if it can mutate state.
- [ ] **RV5:** Cover fresh one-shot borrow/renewal exit, competing same-host
  acquisition, heartbeat, same-job restart, PID reuse, unknown/foreign
  environments, provider/session protections, concurrent owner changes and
  explicit release; preserve ordinary advisory recovery without giving it
  retained-job authority.
- [ ] **RV6:** Verify list/get ordering with continuing real or synthetic
  ControlMaster, target lock, mux/forward, interactive user or unknown
  census. Retain owner rows and existing pool/BUSY/refusal behavior. Credit
  #5776's bounded non-destructive witness without a new destructive demo.
- [ ] **RV7:** Distinguish source/synthetic races, lifecycle regressions,
  originally required native retained-workstream proof and real resource
  settlement. Read back the entire current OPEN receiver and complete
  source/refinement/custody before tracking retirement. Missing evidence is
  not gone; all technical acceptance stays unchecked. No blanket ACK,
  hash, fleet/native or unrelated all-parent gate is created.

### Continuing coordination

Keep #4619 OPEN and all source/resource duties with their actual owners.
The admitted evidence names no new whole-family effort or deadline; do not
invent one or reset any existing limit. A possible #5003 NOT_PLANNED
disposition is only tracking consolidation, not owner acceptance of an
implementation, resource release or permission to operate on a venue.

## Campaign validation and next action

Verify the reviewed merge and complete actual current receiving bodies
before separate source-specific decisions. Preserve original descriptions,
titles, assignments, discussions and every claim/hold/deadline. Journal
actual dispositions and refresh the full metadata-only census; neither
this document nor two retired trackers completes the campaign.
