# Worktree Launch Attempt Journal

- **Slug:** `worktree-launch-attempt-journal`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed proposal and implementation PRs against `dev`
- **Created:** 2026-10-23
- **Status:** Active
- **Vision:** `picker` / `cold-start-resume-prompt` and `durable-launch-attempts`
- **Umbrella issue:** #8375
- **Related:** #6057, #6176, #7073, #7962

## Guiding Intent

Every confirmed Worktree Manager launch action must leave a cheap, durable,
machine-local record before the UI dismisses or creation starts. An operator
can reconstruct what was requested, with which prompt and options, on which
venue, what actually happened, and whether a worktree and Copilot launch
resulted. A crash before worktree allocation must not erase the attempt.

Full prompts are protected private state retained for 90 days, never shared
activity-log fields, public reports or repository content. Read-only machine
sweeps return metadata by default and retrieve prompt text explicitly. An
attempt journal is evidence and recovery material, not a second launch queue
or permission to replay an ambiguous creation or prompt.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Journal driver | Contract, existing-owner integration, validation and PR stewardship | Managed source worktree |
| Validation venues | Isolated Windows and POSIX process-boundary checks | Existing resolved machine transports |
| Remote parity owner | Target-owned SSH prompt admission and delivered-source qualification | Existing #6057 effort |

## Coordination

- **Topology:** reviewed proposal, then cohesive bounded implementation slices.
- **Host (owns PRs):** Journal driver.
- **Delegates:** none assigned.
- **Handoff:** preserve request identity, private-state custody, exact source
  evidence and all unresolved Plan/Validation gates.
- Preserve #6057's original target-owned New/cold Resume contract and completion
  gates. #6176 supplies private submitted-prompt history, not complete attempt
  outcomes. Do not replace #7962's native mux ownership or claim its repair.
- Reuse actual provider/engine receipts. Existing prompt-consumption and
  first-turn receipt owners remain authoritative; a wrapper event is not proof
  that Copilot accepted a prompt.

## Context

At effort start, Manager saved prompt-only history from the confirmation widget.
Storage #8505 and confirmation #8542 now preserve known-target intent before
effects. Truthful unresolved-locality refusal recording merged in #8574 and Manager
boundary observations merged in #8588. Negotiated remote prompted admission and
target receipt binding merged in #8605. Local and seedless resolution receipts
and both-hop attempt propagation merged in #8615. Deferred process and
seed-claim/return observations merged in #8627. Qualified original Linux/WSL
native-session observations merged in #8641; accepted-turn correlation and
joined Manager inspection remain incomplete. Engine activity
has rolling lifecycle and preparation-phase events, but attempts before
allocation lack a worktree identity, prompt text is
deliberately absent there, and phase step IDs are not a whole-attempt ID.

The October 23 report is not resolved by the older source merges. Inspected
Manager and engine versions are newer than the initial parity release.
At investigation time Manager refused seeded remote requests and hid the remote
composer; the engine also rejected remote `--require-native-seed`. Source repair
#8605 restores that path through a negotiated target-native fence, not deleted
checks. Containing-release, authorized rollout and installed acceptance remain
part of #6057, with journal correlation integrated here.

## Request

The operator asked:

> Do we log each worktree-create attempt durably on each machine? Ideally you can sweep machines and their logs, and see which attempts I made, what the prompts where, and what the outcomes were. We need a quick path on each worktree-manager surface to record a log event that I attempted a "New worktree", "Resume", etc, what prompt and options I put in, what happened, and whether it successfully produced a Worktree and launched into Copilot.

The operator confirmed `worktree-launch-attempt-journal` and **90 days** of
full-prompt retention. The preceding report requires crash-resistant prompt
preservation and SSH creation delegated with the seed to the selected venue,
which owns execution beyond that boundary.

### Follow-up: active Open regression

The operator also reported:

> Bug to fix in current agent-worktres and worktree-manager while we are here: using Open on any active workreee in Worktree Manager yields this:

The reported parser error names `--require-agent-hosting`,
`--require-launch-attempt` and `--launch-attempt-id`, followed by the Manager's
resolution-receipt compatibility refusal. The long command listing is omitted.
The existing hosting, receipt-ownership and uncertain-launch contracts still
apply while restoring active Open through a coherent installed pair.

The following decomposition and detailed evidence rules are agent-recommended.

## Plan

### Phase 1 -- Reviewed attempt and privacy contract

- [x] Review and merge this proposal before executable changes.
- [x] Inventory all Manager New/Resume/Open entry surfaces, existing private
  history/restart stores, engine phase/lifecycle events and actual start/receipt
  owners. Define which events each can truthfully attest.
- [x] Specify versioned attempt/event identities and private prompt retention,
  read defaults, failure behavior, cross-machine correlation and migration.
  See [the implementation contract](attempt-contract.md) for boundary ownership,
  storage/expiry rules and qualification budgets; execution remains incomplete.

### Phase 2 -- Durable intent before effects

- [x] Implement the protected, versioned storage foundation independently of
  launch/replay authority; reviewed storage scope merged in #8505.
- [x] Wire the shared append path using existing private-state protections
  and transaction conventions. Persist action, exact submitted prompt/options,
  project, selected venue and attempt ID before dismissal or allocation for
  resolved targets; #8542 merged the responsive confirmation gate.
- [x] Cover empty prompts, rejected unsupported composer choices and cancellation without
  treating draft text or cancellation as accepted launch intent.
- [x] Represent confirmed refusals with unresolved target locality truthfully;
  never coerce an unknown target into a local or remote metadata claim.
- [x] Record failures explicitly. If required intent persistence fails, preserve
  the dialog/draft and refuse mutation rather than silently dropping the audit.
- [x] Keep audit history separate from daemon-owned pending intent; retain
  New/Resume provenance and never manufacture a replacement request on retry.

### Phase 3 -- Existing-owner outcome correlation

- [x] Retain the confirmed attempt through Manager resolution, launch, Restore,
  task embodiment and provider handoff, recording only Manager observations.
- [x] Carry the attempt ID through local and SSH boundaries, binding the
  target receipt and allocated worktree ID before later launch phases.
- [ ] Record per-owner progress, refusal, error and exit events without putting
  prompt contents in shared logs. Distinguish allocated, launcher started,
  Copilot process started and prompt handoff/acceptance where proven.
- [ ] Represent a missing terminal receipt after crash as interrupted or unknown,
  including allocated-but-not-launched cases; never infer success from a plan,
  a zero SSH exit, `copilot_invoked` alone or missing errors.
- [x] Integrate negotiated SSH prompt admission with #6057. The selected venue
  owns allocation, seed staging and launch after admission; the host journal is
  not a second execution authority and cannot automatically replay uncertainty.

### Phase 4 -- Inspect, sweep and retain

- [ ] Expose a discoverable Manager history/detail path and bounded structured
  local reader, filterable by time, action, project, venue, outcome and attempt.
  Prompt contents require explicit detail retrieval.
- [ ] Add a read-only sweep over explicitly selected configured reachable
  machines through existing transports, preserving per-source errors,
  unavailable machines and source identity rather than reporting empty success.
- [x] Enforce 90-day prompt retention, including a documented policy for legacy
  seed-history copies, SQLite sidecars, expiry and explicit deletion. Do not
  silently leave a second indefinite full-prompt copy or delete pending intent.

### Phase 5 -- Acceptance and original report reconciliation

- [ ] Land implementation through current-head review and required CI, then
  verify containing release and authorized installed source identities.
- [ ] Reconstruct recent real attempts using available logs without printing
  private prompts into public evidence; name historical gaps honestly.
- [ ] Exercise installed local and SSH New/cold Resume, including crash recovery,
  with exact attempt, worktree, conversation and prompt-handoff correlation.
  Return original prompt-loss and parity completion evidence to #6057.
- [ ] Settle owned validation resources, resolve every Plan/Validation item,
  mark Done, archive through review and release focus before finalization.

## Validation Plan

- [ ] Kill the process after confirmation, after journal commit, before/after
  target admission, after allocation, during setup and around native handoff.
  Retain the attempt and classify outcomes without duplicate worktrees/prompts.
- [ ] Test concurrent submissions, Unicode/multiline prompts, complete option
  fidelity, empty prompts, New/Resume provenance and prompt-free Open.
- [ ] Validate disk-full, permissions, corrupt/foreign schema, partial writes
  and lock contention with explicit failure and unchanged draft/mutation state.
- [ ] Prove protected Windows ACLs and POSIX permissions, linked-path refusal,
  prompt-free default output/errors and 90-day expiry including legacy copies.
- [ ] Use native Windows and POSIX subprocess boundaries and actual SQLite;
  establish a measured append-latency bound before claiming a quick path.
- [ ] Exercise lost SSH replies, unreachable targets, old capabilities and
  mismatched receipts. No automatic replay or host-side duplicate seed store.
- [ ] Verify cross-machine sweeps are read-only, bounded and explicit about
  unavailable/error sources. Prompt retrieval requires an explicit request.
- [ ] Require provider-specific start/receipt evidence where claiming launch or
  prompt acceptance; unsupported evidence remains unknown, not optimistic success.
- [ ] Run affected tests, lint and contracts, then record genuine installed
  local/remote proof separately from source and synthetic backend checks.

## Proposal

Use one machine-local append interface in the existing Manager private-state
ownership. Allocate a stable attempt identity at confirmation and persist intent
before effects. Existing receiving engine/daemon and launch owners append or
return correlated receipts rather than granting the journal new control powers.

Metadata readers omit prompts by default; explicit local/selected-machine
details expose protected prompt recovery. Do not provision a new always-on
service, silently collect a whole fleet, replay uncertain operations, or export
prompt text through general telemetry. Select the concrete schema and latency
budget after reviewing existing stores and consumer boundaries in Phase 1.

## Journal

### 2026-10-25 -- Bounded local positive-evidence summary prepared

- An explicit Manager `launch-attempts <id> --summary` joins up to ten existing
  v1 engine pages under one 120-second engine-read deadline. The default
  list/detail and shared history UI remain unchanged; no new engine flags,
  response fields, daemon, launch behavior or replay path are introduced.
- Allocation requires completed New admission evidence. Launcher-command
  spawn, backend-command spawn and the engine's qualified native-session join
  remain separate positive facts with their own request/operation identities.
  Returned plans, zero exits, seed claims and absent receipts never become
  Copilot acceptance or allocated-but-not-launched verdicts.
- Pages are independent, non-atomic observations. Partial failures retain safe
  positive facts with source errors, budgets and continuation visible; Manager
  references and events remain bounded. Remote targets are not contacted.
- The focused Windows and frozen-source POSIX packets each pass 45 cases, including four actual
  Manager/engine subprocess cases with and without explicit private input.
  Cross-page cases read 103 receipts in two engine calls and reach the native
  join beyond page one without changing fixture state. Measured summary reads
  took 4.806444 and 4.283359 seconds on Windows, and 0.407694 and 0.437641
  seconds on POSIX; these are source samples, not an installed performance or
  acceptance qualification. Frozen POSIX source and caches were removed after
  qualification. Touched Python lint, installation contracts and the scoped
  module-size guard pass; committed-source documentation checks pass.
- This closes only a bounded local projection gap. Complete per-owner terminal
  classification, selected-machine sweeps, containing release, authorized
  rollout, installed prompt/conversation proof and parent cleanup remain open.

### 2026-10-25 -- Manager-boundary outcome filtering merged

- #9001 merged as `69794c92b53ba40a5a170f6807eadb1e1516fc10` after genuine
  current-head Copilot approval, no review threads/findings and required CI
  passing. Both parent trackers remain open.
- Re-read both parent plans after merge. The new filters intentionally expose
  only Manager-boundary observations; they do not settle whole-attempt
  provider/native aggregation or selected-machine inspection. Continue those
  source slices before containing release, authorized rollout and installed
  New/cold Resume same-conversation/exactly-once proof.
- The managed post-merge sync pulled this worktree onto the provider-confirmed
  merge. Parent validation, release/rollout, resource cleanup and effort
  closure gates remain open; this worktree is not ready to finalize.

### 2026-10-25 -- Scoped Manager-outcome filter source

- Local list and detail now project the latest durable Manager observation
  independently of event-page cursors, retaining its sequence and exact
  timestamp. Indexed latest-event lookup and a shared deterministic SQLite
  function apply outcome filters before pagination without materializing
  private input, querying engines or requiring SQLite's JSON extension.
- CLI `--outcome` and both Pickers' shared history screen expose only this
  explicitly named `manager-boundary` scope. Refusal, nonzero launcher return
  and explicit interruption are distinguishable; a zero return is labeled
  `returned`, never successful Copilot launch or prompt acceptance. Missing
  events and ambiguous engine/transport failures remain unknown.
- The focused Windows packet passes 145 cases with four platform-specific
  skips, including actual Manager-to-engine subprocess detail, legacy reads,
  outcome pagination, timestamp fidelity and real Textual navigation.
  Frozen-source POSIX coverage passes 147 cases with two Windows-only skips
  plus 16 engine-reader cases; the actual cross-runtime subprocess cases run
  on both platforms. Rapid-click tests await Textual's real button-animation
  completion rather than assuming idle means click-ready; Windows UI coverage
  passes all 12 cases after that correction. No deadlines were widened.
  Source qualification is not release, rollout or installed launch acceptance.
- Whole-attempt provider/native evidence aggregation, selected-machine sweeps
  and every remaining parent acceptance/cleanup gate stay open. This scoped
  filter does not close the larger Phase 3 or Phase 4 inspection items.

### 2026-10-25 -- Retention source merged; outcome classification next

- #8999 merged as `1e3638ee4b257416e4d0f5eae6bb97f4f51247eb` after genuine
  current-head approval, no open findings, all three review threads resolved
  and required CI passing. Both parent trackers remain open.
- The bounded 90-day retention implementation is now complete in source,
  including legacy copies, crash-retry scrubbing, exact-deadline cached
  navigation, malformed timestamp refusal and incomplete sidecar preservation.
  Explicit expiry erases private text, not journal metadata or pending intent;
  no early per-attempt erasure or whole-store reset command is introduced.
- Current-source Windows failure/confirmation coverage passes 100 cases.
  Frozen final-source POSIX coverage passes 156 cases with two Windows-only
  skips, including native WAL/restart, composer expiry and maintenance-policy
  controls. The isolated source/caches were removed. These results do not
  establish rollout or installed prompt acceptance.
- Re-read both canonical plans after merge. Next, classify whole-attempt
  outcomes from the existing qualified owners and expose bounded outcome
  filtering without treating a plan, launcher return or missing event as
  accepted prompt delivery. Selected-machine sweeps, containing release,
  authorized rollout, real local/SSH conversation proof and cleanup remain
  open. This worktree must continue rather than finalize a completed slice.

### 2026-10-25 -- Retention and legacy history migration prepared

- Normal production Picker use runs local private-input maintenance after first
  paint, respecting the automatic-maintenance veto and read-only/mock modes.
  Explicit confirmation also runs it off-thread before saving, preserving the
  draft and refusing launch on a sanitized retention failure.
- Legacy prompt history now has the same 90-day bound, preserving original
  IDs, projects, exact text and submission timestamps without inventing launch
  evidence. Migration securely deletes expired rows, checkpoints recognized
  WAL through SQLite, scrubs old freelist copies and leaves a retryable marker
  if interrupted before the scrub finishes. New history is indexed and uses
  DELETE journaling and secure deletion. Orphan or unexpected sidecars refuse.
- The affected intent/history/confirmation/maintenance packet passes 213 cases
  with eight platform skips. Follow-up retention controls include the real
  production maintenance callback and native abrupt-exit WAL/restart proof.
  The final migration/database/native packet passes 44 cases, including a busy
  real WAL reader. Windows migration measured about 37.71ms for 101 legacy
  rows, with 20 warm maintenance samples at p50/p95/p99/max
  14.66/89.94/103.27/103.27ms and no failures. An earlier run measured
  49.61ms migration and 14.75/17.45/21.18/21.18ms warm samples; the later
  outlier remains evidence, not a discarded sample. This is storage work
  off-thread, not installed or key latency; neither small sample qualifies
  high-percentile tails.
- Frozen-source POSIX validation passes 128 cases with two Windows-only skips,
  including all three native migration/restart/measurement cases. The source
  archive and isolated caches were removed afterward. POSIX migration measured
  about 20.53ms for 101 legacy rows; 20 warm samples measured
  p50/p95/p99/max 0.87/2.79/2.84/2.84ms with no failures, still not tail or
  installed qualification.
- Pending daemon seed state is untouched. Review/merge, containing release and
  authorized rollout remain owed.
  Outcome filtering, selected-machine sweeps and installed local/SSH prompt
  acceptance are still parent completion gates.
- Review identified two real read-side gaps: malformed legacy timestamps could
  pass SQLite's numeric comparison, and already-loaded composer caches could
  outlive their deadline. Reads now validate legacy timestamps under one
  read-only snapshot. Both history loaders retain original deadlines; cached
  navigation drops expired text and edits without overwriting the current draft
  or reviving them after clock rollback. The review-fix packet passes 123 cases
  with four platform skips; the affected UI ownership/navigation packet passes
  104 cases with four skips. Real New and asynchronous Resume composer tests
  advance the clock after loading history and after physical deletion.
- The next review found an incomplete-store hole: an empty main file could
  allow SQLite to discard leftover WAL evidence. Every non-WAL header now
  refuses WAL/shared-memory sidecars before opening SQLite, including empty
  files and false WAL headers. All read/write/expiry operations preserve the
  exact fixture bytes on refusal. A separate startup correction reports a
  retention warning without suppressing independent anchor repair, housekeeping
  or heartbeat ownership; confirmation still refuses launch. The focused
  failure-path/confirmation packet passes 100 cases.

### 2026-10-25 -- Interactive local history merged; retention next

- #8994 merged both Picker entry points with current-head approval and required
  CI. The common read-only screen exposes local metadata, bounded owner detail
  and separately requested private input without launching or replaying.
- Review regressions prove history row messages cannot reach compact Resume,
  including queued selections during a read; explicit private-input recovery
  hints retain their flag; and project/action changes cannot reuse another
  query's pagination cursor. The focused packet passes 32 cases.
- Next, complete the reviewed 90-day retention contract across normal Manager
  use and legacy prompt-history copies, retaining original timestamps,
  protecting SQLite sidecars and never deleting pending daemon intent.
  Whole-attempt outcome filtering, selected-machine sweeps and installed
  local/SSH acceptance remain separate incomplete work.

### 2026-10-25 -- Bounded local reader merged; interactive history next

- #8990 merged with current-head approval and required CI. Manager metadata
  lists and explicit-input detail now join bounded local engine receipts
  without replay, provisioning or inferred prompt acceptance.
- Review corrections complete full/lazy command registration and prove actual
  cluster-free execution. The opt-in native consumer lane now strips Git
  overrides before fixture setup and tests deliberately redirecting values.
- Native Windows and earlier frozen-source Linux consumer evidence remain
  separate from installed acceptance. The local reader does not query the
  selected remote machine or establish whole-attempt terminal success.
- Next, expose the existing protected metadata/detail reader from the
  interactive Manager without blocking navigation or revealing prompts by
  default. Outcome filtering, selected-machine sweeps, legacy retention,
  release and installed local/SSH proof remain incomplete.

### 2026-10-25 -- Interactive local history source

- Both interactive Pickers now expose the same local history screen through
  Ctrl+Shift+H. Metadata paging and project/action filters do not start an
  engine; explicit row detail reuses the versioned CLI reader boundary.
- Private input requires a separate exact-attempt reveal, respects expiry and
  is removed from the view on return to the list. The screen retains source
  errors, page cursors and unknown acceptance rather than inventing a terminal
  outcome. Long previews identify truncation and direct further paging to the
  CLI.
- Headless Textual navigation exercises both entry points, read-only actual
  SQLite data, privacy, paging, slow-read dismissal and preserved launch
  shortcuts. The actual native Windows Manager/engine consumer lane also
  passes after sharing the detail implementation.
- This source increment does not close the composite Phase 4 item: outcome
  filtering, selected-machine sweeps, retention, release and installed proof
  remain open.

### 2026-10-24 -- Bounded local inspection prepared

- Added a discoverable Manager CLI list/detail path. Lists query protected
  metadata without starting the engine; explicit detail joins the existing
  local engine receipt owners across their versioned CLI boundary.
- Time, action, project and venue filters stay in the journal query. Separate
  intent, event and receipt cursors retain incomplete-page evidence; equal-time
  intent rows use an ID tie-breaker. Private input still requires an exact-ID
  opt-in and respects its original expiry.
- The engine bounds one exact attempt directory and projects only validated
  resolution, execution, historical native-session and matching admission
  evidence. Missing, corrupt and incompatible sources remain explicit.
  Admission acceptance and process exit never become accepted-turn evidence.
- Focused source tests cover privacy, read-only missing/corrupt state, limits,
  pagination and receipt identity. The real Windows Manager-to-engine process
  lane passes metadata and explicit-input cases with unchanged fixture state.
  A frozen committed-source export on native Linux storage passes the engine
  packet and the Manager journal/CLI/cross-runtime packet. Earlier attempts
  stalled on mounted-source filesystem I/O before tests; they are not passes.
  No installed Copilot acceptance or latency qualification is claimed.
- This is not the whole Phase 4 item: interactive history, outcome aggregation,
  selected-machine sweeps, legacy retention and installed acceptance remain
  open. Both parent objectives remain Active.

### 2026-10-24 -- Active Open installation mismatch identified

- The current Manager selects an older complete engine runtime that lacks the
  launch-receipt arguments, while a newer installed payload contains them.
  The parser refusal therefore occurs before Open can return an attachment
  plan; it is not permission to strip the required receipt checks.
- The promoted release already makes hosting enforcement intrinsic in #8703
  and retains the launch-receipt contract. Drive the normal registered-source
  unified updater to reconcile the installed pair, then verify the actual
  Manager-to-engine Open boundary. Do not hand-edit deployed packages or
  mistake a refreshed payload for a selected, compatible runtime.
- The normal update advanced Manager but could not start its durable worker,
  so the older engine remains selected and the update stays pending. A bounded
  inert diagnostic authenticates the WMI bootstrap, then observes its real
  Job-membership check refuse startup; cleanup is confirmed. This matches
  the existing #6859 / held #7833 repair, not an argument-parsing workaround.
  The existing owner has also recorded that adding the WMI breakaway flag
  alone did not establish independent lifetime. Do not introduce a competing
  broker, deploy the held draft or relax the ownership proof.
- Keep all diagnostic roots and workers headless. No foreground updater
  fallback or deployed-code patch was used while diagnosing this refusal.
- Publication of this inspection slice also exposed the fixed short push
  budget: its two independent read-only pre-push guards exceed that budget
  when serialized. The operator selected configured-only identifier checks
  without automatic cross-repository discovery, and final-diff file-size
  enforcement in required CI because contributions are squash-merged.
  #8953 merged that policy and removed the expensive history scan without
  changing the publication deadline. The earlier parallel-guard experiment
  is superseded and is not part of this inspection slice.

### 2026-10-25 -- Binding-free Bridge prerequisite merged

- #8961 merged startup topology discovery without mandatory source-project
  machine binding, following current-head approval and required CI. Optional
  canonical selectors remain validated; effect-side ownership is unchanged.
- The affected source packet covers combined-profile locality, remote-only
  capture/restore and key/SSH-alias collisions. These are source contracts,
  not evidence that a running service or installed launch path changed.
- Promotion, authorized rollout, the held updater repair and actual active
  Open / New / cold Resume verification remain separate incomplete gates.

### 2026-10-24 -- Native observation slice merged; joined inspection next

- #8641 merged with current-head approval, no open findings and required CI
  passing. Both parent trackers remain open. Verified all 13 authored file
  blobs against the provider-confirmed merge before advancing the source.
- Review corrections preserve unknown outcomes for missing execution receipts
  while retaining corruption errors. The actual backend process now forbids
  direct writes in the native qualification lane; unavailable registration and
  unavailable observation each fail their negative control without a receipt.
- Re-read this effort and the remote parity effort after merge. No broad
  Phase 3 or validation checkbox is closed: native-session observation is not
  accepted-turn, installed same-conversation or end-to-end outcome proof.
- Next, expose bounded joined inspection using the existing protected journal
  and receipt owners. Preserve metadata-only defaults, explicit private-input
  retrieval, unknown/interrupted outcomes and per-source errors. Retention,
  legacy history integration, selected sweeps and installed acceptance remain
  open; neither rollout nor objective completion is implied by this merge.

### 2026-10-24 -- Original native-session association prepared

- Trace confirmed that the original hook already carries its captured
  environment and source witness through lifecycle registration. Reused that
  transport and the existing exact native UUID/hold authority, without a new
  hook, ambient current-attempt lookup or acceptance backend.
- Execution version 2 records the qualified physical epoch; an exact
  child-scoped backend hint selects the observation's receipt. An independent
  immutable witness survives observation-before-spawn-receipt ordering.
  The read-only join requires matching incarnation, epoch and backend ancestry;
  legacy, unsupported and missing evidence remains unknown.
- Canonical registration and seed settlement are unchanged. No session-start,
  source join or process event is an accepted-turn verdict. Broader readers,
  retention, selected sweeps, release/rollout and installed local/SSH proof
  remain open.
- Qualification: 70 focused execution/observation checks, 114 registration/source
  checks (one Linux-only skip on Windows), 128 seed-authority checks and 52
  selected lifecycle/transport checks passed. Seven Windows process/IPC cases
  include two consoleless cycles with no visible windows or focus changes.
- The explicit Linux/WSL lane passed on native Python 3.11 through an actual
  original-hook subprocess, open native hold and resident write endpoint, with
  direct fallback forbidden. First-touch mounted-filesystem test-cache builds
  failed before test execution; the same canonical runner built and passed with
  a private native-filesystem cache. Temporary and failed caches were cleaned.
- These are synthetic source-contract/process checks, not installed Copilot
  acceptance or a composed hook latency qualification. The latter measurements
  remain with #7935 and this effort's installed acceptance gates.
- Review follow-through covers entirely missing execution/attempt receipts as
  unknown without hiding corruption. The native fixture now forbids direct
  fallback in the backend subprocess as well as the driver, with separate
  unavailable-registration and unavailable-observation negative controls.
  The expanded focused packet passed 73 checks and all three native Linux/WSL
  controls passed. No accepted-turn or installed-acceptance gate is closed.

### 2026-10-24 -- Deferred execution slice merged; native association next

- #8627 merged with current-head approval, no remaining findings and required
  CI passing. Published commit and authored-file content were verified against
  the provider-confirmed merge before pulling the source forward.
- Review follow-through fixed interrupted post-effect receipt/identity/seed
  cleanup so child waiting and seed-settlement ownership continue without
  replay. The shared writer also skips unnecessary boot/polling for a complete
  incompatible publisher only when its exact PID/birth owner is confirmed
  live; missing, dead, reused and unconfirmed owners retain ordinary startup.
- Source qualification includes real daemon/backend handoffs and interrupted
  receipts, crash-after-claim preservation, actual PowerShell/Bash forwarding,
  and two consoleless Windows cycles. These are synthetic process checks,
  not installed Copilot first-turn or same-conversation proof.
- Re-read both canonical efforts after merge. Continue native observation
  association through the original source-aware owner; preserve unsupported
  evidence as unknown. Joined readers, retention/legacy cleanup, selected
  sweeps, containing release, authorized rollout and installed acceptance are
  still open. Neither parent effort nor any broad Plan gate is complete.

### 2026-10-24 -- Deferred execution observations prepared

- Added bounded prompt-free execution receipts to the existing tracking-write
  owner. Explicit wrapper arguments bind the confirmed attempt, exact
  resolution, unique execution operation and parent operation; no ambient
  current-attempt pointer or new acceptance backend was introduced.
- Retain requested, seed-claimed, actual OS spawn and observed launch/child
  return evidence. Missing or unconfirmed receipts remain unknown. A process
  start is not a Copilot conversation or accepted turn, and post-effect audit
  uncertainty never repeats the command or skips seed settlement.
- Standard PowerShell/Bash setup propagates the additive attempt boundary;
  legacy seed-only setup must opt in rather than receive unsupported flags.
  Source checks include real daemon/backend processes, loss after seed claim,
  native PowerShell/Bash argument forwarding and two consoleless Windows
  cycles. The latter exposed Windows NUL reporting a TTY without a console;
  launch now also checks actual console attachment.
- [Native receipt measurements](execution-receipts-windows.json) retain all
  102 successful metadata-write samples across 51 operations against an
  already-running resident. No per-write child was allowed. These synthetic
  Windows results do not qualify composed launch/UI latency, native POSIX
  engine behavior, a live installed Copilot conversation or tail performance.
- This is a source candidate, not yet a merged or deployed completion.
  Native conversation/acceptance, joined readers, retention/legacy cleanup,
  sweeps and the original installed local/SSH same-conversation gates remain
  open; no Plan checkbox was closed by this partial outcome slice.

### 2026-10-23 -- Confirmed operator intent and current gap

- Confirmed the effort slug and 90-day prompt retention; captured the request
  verbatim without private host, repository or actual prompt contents.
- Verified existing prompt-only recovery and engine phase logs do not provide
  the requested complete attempt-to-outcome audit. Found the current remote
  native-seed refusal; no executable safety fence was removed.
- Claimed #8375 and prepared reviewed intent before implementation. Original
  crash-loss cause and installed SSH acceptance are still open, not solved.

### 2026-10-24 -- Documentation CI detour

- The operator requested that Markdown-only changes select no executable
  coverage tests or code-contract scans, while retaining lint. Reviewed fixes
  #8406 and #8439 preserve release changefile enforcement and required outcomes,
  and use the pinned PR merge-base change set so advancing upstream code cannot
  create false overlap.
- This proposal remains documentation-only. Its next normal CI execution is
  the live check of explicit unit/source-scan skips and retained Markdown lint;
  no journal implementation, crash-loss repair or SSH capability is implied.

### 2026-10-24 -- Reviewed proposal admitted

- Proposal #8380 merged after a current-head approving review with no findings
  and all required checks passing. Preserved both effort-index entries while
  reconciling upstream changes; the working source now includes the merged plan.
- Begin Phase 1's entry-surface and receipt-owner inventory before choosing the
  concrete journal schema. The original prompt-loss cause, target-native SSH
  negotiation and installed acceptance gates remain open.

### 2026-10-24 -- Protected storage foundation

- Added a separate versioned private SQLite journal with atomic initialized-file
  publication, one-transaction confirmed intent/first event, immutable attempt
  identity, structured observations and bounded metadata-first readers. Exact
  prompt/options stay in an explicitly retrieved, expirable input document.
- Added read-only private-permission verification: evidence readers do not
  repair permissions or create absent storage. Expiry securely erases the
  journal's own private input; legacy seed-history migration remains pending.
- Windows qualification covers real process exit/independent read, interrupted
  transactions, owner/SYSTEM ACLs, broad-ACL refusal, concurrency, schema errors,
  sidecars, lock failure and physical expiry. The focused journal/history/native
  selection passes 101 cases with seven platform/privilege skips. Subsequent
  metadata/input corruption coverage passes 103 ordinary journal/history cases
  with seven skips; the four explicit Windows native checks pass separately.
- An existing history ACL unit test exposed a legacy shell-start timeout.
  Replaced that test's shell startup with independent raw Windows DACL/ACE/SID
  inspection, preserving and strengthening its actual permission assertions.
  Explicit native journal qualification retains independent PowerShell ACL
  inspection; ordinary permission units no longer depend on shell startup.
- Actual POSIX process-boundary smoke verifies committed input survives an
  abrupt writer exit, owner-only permissions and physical expiry. The full
  local POSIX runner did not finish preparation within its bounded attempts;
  it is not counted as a passing suite. Its owned helpers were checked absent.
- Native commit measurements are not UI qualification. An initial Windows
  sample had a warm p95 near 27ms, but a later loaded sample reached about
  407ms p95 and 552ms maximum; POSIX smoke measured about 14ms p95. Each warm
  sample has 100 observations, insufficient for tail qualification. Therefore
  confirmation integration must await durability without blocking the UI or
  dismissing first; a synchronous callback would not meet the 100ms target.
- This module is not yet called by confirmation, engine or launch owners.
  No installed prompt-loss repair, remote parity, complete retention feature
  or inspection/sweep surface is claimed by this foundation.
- Foundation review exposed a wildcard schema-prefix error and a stale
  existence observation during concurrent publication. Literal-prefix matching
  now refuses lookalike tables, views, indexes and triggers, including an expiry
  trigger that would otherwise retain private input. The sidecar check refreshes
  main-file existence before declaring an orphan. Deterministic regressions
  cover both cases.
- A loaded concurrent-write check also showed that the initial 100ms SQLite
  contention deadline could reject legitimate first writers. Reuse the existing
  private-history five-second contention convention instead; the required UI
  acknowledgement remains asynchronous and separate. The focused storage
  portfolio passes 46 cases with three platform/privilege skips.
- A further review found schema-name collisions, safe inherited Windows sidecar
  ACLs and WAL-reader initialization effects. Schema comparison now retains
  every type/name/definition row; read-only verification accepts private
  inherited sidecar ACEs only while directory/main requirements remain strict;
  a pre-connection header check refuses WAL without creating sidecars.
  Regressions include the actual colliding expiry trigger, a concurrent Windows
  journal read without ACL repair and whole-directory comparison on refused WAL
  reads. The focused storage portfolio passes 49 cases with three skips.
- POSIX special-file review found that a FIFO could pass the old non-directory
  check and block before SQLite's deadline. The shared private-state helper now
  requires a regular file (or a real directory when requested) before protection,
  verification or header access. All existing private-state consumers pass
  together: 199 focused cases with ten platform/privilege skips. Actual POSIX
  FIFO checks refuse all five read/write operations without opening or waiting
  for the FIFO; the bounded native alarm is never reached.
- Bounded-reader review found that accepted histories could outgrow the detail
  cap and hide still-unexpired input. Detail now pages observations with an
  explicit continuation sequence and more-events flag, independently of private
  input recovery. A 1002-event regression checks exact two-page coverage and
  retained input; invalid/unbounded page arguments refuse. The focused storage
  portfolio passes 56 cases with four platform/privilege skips.

### 2026-10-24 -- Foundation merged; confirmation integration next

- Storage-only #8505 merged as `3f93390187a00e538f5ae69f3ecef466015924ee`
  after the final head received approval with no unresolved findings, all review
  threads were resolved and every required CI/attribution/workflow check passed.
- The source worktree is pulled forward onto the actual merged foundation.
  The protected append/read/expiry API is available, but no production caller
  uses it yet; this is not a delivered prompt-loss repair or retention feature.
- Next, wire responsive pre-effect confirmation across the inventoried Manager
  entry surfaces, preserving drafts and refusing effects on persistence failure.
  Keep legacy-history expiry, target/native receipt correlation, negotiated SSH
  admission, selected-machine inspection and installed acceptance gates open.

### 2026-10-24 -- Responsive confirmation implementation prepared for review

- Added a shared saving modal using the existing Inbox/background-worker path.
  Exact original text, options, project and selected venue commit before the
  original composer dismisses or launch effects begin. Failure preserves the
  draft; an identical failed save reuses its identity on explicit retry, while
  separate successfully confirmed actions receive separate attempt IDs.
- Wired New/cold Resume, ordinary Open/Resume/Bare Resume/Restore, new-window,
  compact Picker, task Open before embodiment and provider Open for targets
  whose locality is already resolved. Manager decisions and launch requests
  carry the ID; no engine/native consumption evidence is inferred.
- Unsupported prompt/option confirmations retain a refusal without submitting
  or dismissing. Cancelled drafts record nothing. Unresolved-locality refusals
  still precede persistence: the current storage contract requires a boolean.
  Their truthful representation remains an explicit Phase 2 follow-up.
- The affected Picker/composer/runner/task portfolio passes 489 cases with four
  platform/privilege skips. Updated legacy test doubles to supply the actual
  maintenance-decision mixin and explicitly fake/assert AHP hosting admission,
  rather than accidentally reaching a missing engine in routing-only tests.
- Native Windows and POSIX controls both pass: terminate the actual composer
  process before save or after commit but before dismissal, then use an
  independent reader and prove no launch effect occurred. With storage delayed
  by 200ms, 20 confirmation-handler samples per platform remain below the
  100ms acknowledgement budget. Windows p95 is about 2.69ms (maximum 20.84ms);
  POSIX p95 is about 1.05ms (maximum 8.60ms). These are handler measurements,
  not physical-key latency or sufficient high-percentile tail qualification.
- The POSIX leaf controls ran against byte-checked source copies on a native
  filesystem with fresh, offline-installed test dependencies. A partially
  prepared cross-filesystem test environment first lacked packages and then
  exceeded its bounded lane deadline; neither attempt counts as a passing
  full POSIX suite. Owned test processes and disposable native files were
  removed. No installed Manager/runtime rollout occurred.
- Ruff and the 13-plugin installation contract pass. A clean current-source
  snapshot passes documentation consistency; the checkout-wide scan cannot
  traverse an ignored cross-platform test-venv link on Windows.
- Legacy history copies, comprehensive outcome correlation, selected-machine
  readers/sweeps, negotiated SSH prompts and installed acceptance stay open.
  This implementation is not yet merged and is not parent-effort completion.

### 2026-10-24 -- Confirmation gate merged; unresolved targets next

- #8542 merged as `e049c2d2114e5e80f3a414bdd3f77cd53a7da833` after current-head
  approval with no unresolved findings and all required CI, attribution and
  workflow checks passing. Parent issues #8375 and #6057 remain open.
- Review found two genuine persistence-boundary omissions. Compact-Picker tests
  now force per-test private roots even under direct pytest and explicitly
  await saving. Mock cold Resume now forwards the same no-persistence boundary
  as mock New, through one shared gate; neither creates history/journal entries.
- Final affected coverage passes 493 cases with four platform/privilege skips.
  Current-source Windows and POSIX native crash/acknowledgement controls each
  pass three cases. Both 20-sample handler checks remain below 100ms with 200ms
  injected storage delay; this is still not physical-key or installed evidence.
- Pulled the continuing source worktree forward without discarding unmerged
  work. The three replay candidates were independently listed by the merged
  provider PR, and the whole source patch matched the merge patch before a
  zero-replay rebase. Native recovery refs remain intact.
- Next, represent unresolved target locality without inventing a local/remote
  value and journal confirmed refusals before proceeding to owner outcome
  correlation. Keep all retention, inspection/sweep, negotiated SSH, release,
  installed-conversation and cleanup gates open; do not finalize this worktree.

### 2026-10-24 -- Unknown-locality refusal slice prepared for review

- Journal version 2 exposes locality as true, false or unknown. Its internal
  sentinel preserves the existing table/index shape without copying private
  inputs. Readers retain version-1 compatibility without migration; the first
  writer validates original metadata under the old rules and advances the
  version atomically with its mutation. Invalid old values cannot gain meaning.
- Confirmed ordinary Open/Resume, Bare Resume, new-window and task Open retain
  selected target, title and options before a structured locality refusal.
  They do not exit, embody or launch. Unavailable Resume prompt editor entry is
  not confirmation; mock mode and already-correlated deferred continuations
  never create a new intent.
- Focused storage, confirmation and existing action regressions pass 130 cases
  with four platform/privilege skips. Two native Windows process-crash cases
  cover upgrade before the first event and after commit, preserving original
  evidence and refusing partial intent. Ruff and the 13-plugin install contract
  pass. Current-version POSIX and installed-local/SSH acceptance remain open;
  previous source qualification is not silently treated as version-2 proof.
- This closes the remaining Phase 2 implementation item, not the parent gate.
  Review/merge, existing-owner outcome correlation, retention/history migration,
  selected-machine readers/sweeps, negotiated SSH admission and delivered
  conversation evidence remain owed.
- Required CI on the first published head found three stale installer guardian
  copies inherited during the managed publication rebase. The canonical owner
  had already gained zombie-only process-group handling, but the vault,
  pull-request runtime and shared updater copies had not been synchronized.
  Ran the existing generator, retained the exact canonical bytes, added release
  changefiles for every affected updater consumer and revalidated the install
  contract. Both canonical zombie/live-member refusal controls pass; this is a
  generated-copy consistency repair, not a new guardian design or native macOS
  qualification. No failing CI gate was bypassed.

### 2026-10-24 -- Phase 2 refusal recording merged; outcome correlation next

- #8574 merged as `09bce158f38c22020443cf2b6fa459a937181599` after the final
  head received approval with no findings and required CI, attribution,
  workflow and identifier checks passed. Both parent issues remain open.
- The continuing source is pulled forward onto the confirmed merge. The
  provider's complete commit roster matched the local roster and every touched
  path matched merged content before discarding any already-merged replay.
  A native routine-sync guard still refuses the deleted remote PR ref; it did
  not change source. Recovery backups remain intact.
- Phase 2 is implemented, not the parent objective. Start Phase 3 with the
  Manager's actual resolution and launch boundaries, then carry correlation
  through existing engine/target owners. Preserve unknown/interrupted evidence
  and distinguish returned plans, allocation, launcher return and actual native
  handoff. Keep the negotiated SSH fence, retention/migration, inspection,
  current-version portable proof and installed conversation gates open.

### 2026-10-24 -- Manager outcome-observation slice prepared for review

- The confirmed attempt now follows actual Manager resolution and launch
  boundaries, Restore preparation, provider handoff and task embodiment.
  Returned plan identities are correlated as Manager `resolved` evidence,
  never relabeled allocation, Copilot start or prompt acceptance.
- Requested boundaries commit before the next effect. Local refusals are
  explicit; engine failures after possible mutation and interrupted calls
  remain unknown. Post-effect observation failures log a sanitized warning
  while preserving the actual result, so a completed handoff is not disguised
  as permission to retry. Task observations stay on tracked background work.
- Source unit controls cover real protected SQLite state through Manager
  seams, required-write failures, optional outcome-write failures, identity
  correlation and private-error exclusion. Native Windows writer/reader
  controls prove that abrupt exit during resolution or launcher execution
  retains the last committed boundary and exact private input without an
  invented completion or replay.
- The explicit native measurement isolates four Manager observations from
  synthetic engine/launcher work. The final enabled-versus-unjournaled
  101-attempt / 505-event comparison measured 98.4027ms for the first
  enabled chain and warm nearest-rank p50/p95/p99/max of
  93.4584/174.6267/296.4259/609.5926ms with no failures. The sequential
  unjournaled baseline measured p50/p95/p99/max of
  0.0126/0.0159/0.0450/0.0502ms; samples are insufficient for tail
  qualification and the observed outlier is retained, not discarded.
  This is off the UI confirmation thread, not a passing 100ms physical-key
  budget or representative installed launch qualification. The reproducible
  lane also compares unjournaled execution with the same synthetic workload.
- Engine/target transport, native-owner receipt correlation, negotiated SSH
  seeds, POSIX/current-installed qualification, retention/migration, inspection
  and original same-conversation New/cold Resume gates remain open. This
  slice is not completion of either parent issue.
- The first review identified AHP session preparation as an earlier mutation
  than final launcher invocation. Moved the required requested boundary ahead
  of `ensure_session`, retained one requested event, and added direct controls
  for write refusal before preparation, preparation failure and a normal
  hosted launch. The affected AHP/launch/admission portfolio passes 112 cases
  with 10 skips. Current-head review and CI remain publication gates.
- The next review found that the journal's ASCII observation-ID restriction
  was narrower than supported engine worktree names. Worktree observations
  now preserve composed/decomposed Unicode and non-Latin/non-BMP characters
  verbatim, with the existing bound and ASCII whitespace/control rejection;
  request/seed/conversation ID validation remains restricted. New and Resume
  seam regressions prove that valid Unicode resolution does not block launch.
  The affected storage/outcome/AHP/production portfolio passes 188 cases with
  four skips; current-head review and CI still apply.

### 2026-10-24 -- Manager observations merged; engine/target correlation next

- #8588 merged as `2a0bbfb2e653223c694b493713c2f261fc90db3c` after the final
  head received approval with no open issues and required CI, attribution and
  identifier checks passed. Both parent issues remain open. Review corrected
  AHP pre-effect ordering and engine-supported Unicode worktree identities.
- Re-read both canonical plans after merge. Only the bounded Manager
  observation item is complete; target receipt binding, native acceptance,
  negotiated SSH seeds, retention/migration, inspection and installed
  same-conversation validation remain open.
- The source is forwarded onto the confirmed merged history with a recovery
  backup. All eleven non-index changed blobs matched the merge exactly.
  The owned effort-index row also matched; its only additional difference
  was an unrelated contributor's index update, retained rather than replayed
  over. The guarded routine sync still refused its deleted remote PR ref
  before changing source.
- Continue correlation through existing engine/target owners without giving
  the journal replay authority. Keep the native transport fence atomic and
  negotiate receiver capability before enabling remote prompt delivery.

### 2026-10-24 -- Negotiated remote prompted correlation prepared

- Added version-2 structured admission for non-preview remote New/cold Resume
  prompts. The selected target checks the matching native capability before
  configuration, persists the attempt ID with its existing request/worktree/seed
  receipt, and checks that binding on replay. Mutation protocol 8 excludes older
  daemon handlers; legacy version-1 reads remain supported. Target request ID,
  not journal attempt ID, continues to own recovery/idempotency.
- Restored remote prompt composition only through that negotiated path. The
  Manager passes the saved attempt ID, verifies capability and all returned
  identities before launch, and never queries a remote execution leg locally.
  The second SSH hop keeps the native requirement and carries identities, not
  another prompt copy. Open and native cold-start/duplicate-delivery fences are
  unchanged. Local and seedless engine/native correlation remain incomplete.
- Target protocol/daemon/replay/native-fence checks pass, including real Windows
  encoded-shell transport with version-1/version-2 decoding. Manager client,
  observations and UI checks pass. Corrected adjacent hosting fixtures without
  weakening admission. Source module-size, lint and install-contract gates pass.
  Native POSIX transport is not qualified by the Windows run; actual installed
  local/SSH conversation, prompt acceptance, release/rollout, retention,
  inspection and sweep gates remain open. Current-head review and required CI
  are still owed before merge.

### 2026-10-24 -- Negotiated remote prompted slice merged

- #8605 merged as `958165f6e81c06d3d184ab0c48a9d95a39ad7c92` after
  current-head approval with no findings and green required CI. Final contained
  engine packet: 410 passed, two native POSIX skips on Windows. Manager packet:
  190 passed, four skips; remote prompt UI packet: seven passed.
- Re-read both canonical plans. Only the bounded negotiated SSH integration
  item is newly complete. Full local/seedless attempt transport and existing-owner
  native start/handoff evidence are the next correlation stretch. No plan,
  wrapper return or SSH success is promoted into Copilot acceptance.
- The managed sync refused the deleted PR ref before mutation. All 30 authored
  path blobs matched the provider-confirmed merge exactly; retained a recovery
  backup and forwarded the clean source without replay. Both parents remain
  open. No containing release, authorized rollout or delivered conversation
  proof is implied; retention/migration, readers, sweeps and acceptance remain.

### 2026-10-24 -- Local and seedless resolution correlation prepared

- Journaled New/Resume/Open/Bare Resume/base resolution now requires the saved
  attempt ID and a parser-level receipt capability. Both local re-resolution
  and SSH launch forwarding retain them. Structured prompted admission keeps
  its existing durable version-2 target receipt and requires a non-null attempt
  before configuration; old receivers cannot silently discard the new fence.
- Added prompt-free per-resolution metadata through the existing tracking-write
  owner. An initial event commits before resolver effects, and a terminal event
  binds the returned worktree/request/seed IDs before plan publication. Separate
  operation IDs distinguish hops without turning the attempt into an allocation
  retry key. Seedless SSH planning is only forwarding evidence, never target
  admission. Missing terminal evidence remains unconfirmed.
- The contained engine packet passes 400 cases with two platform skips. Its
  explicitly selected native lane proves actual daemon transport and process
  loss after the initial receipt; that lane is excluded from default unit CI.
  The Manager packet passes 240 cases with twelve platform/privilege skips.
  Adjacent launch/seed fixtures now create their declared checkouts and enter
  admitted logging before checking logs; production safety fences are unchanged.
- Native Windows measurement of 51 synthetic resolver callbacks through an
  already-running real writer retains 102 events with zero failures. Added
  receipt work has nearest-rank p50/p95/p99/max of about
  60.10/134.34/154.45/154.45ms; the no-journal callback baseline is about
  0.006/0.016/0.166/0.166ms. This measures fresh receipt directories, not monitor
  bootstrap, complete launch, physical-key latency or installed acceptance;
  the small sample is not tail qualification.
- This is source preparation, not a merged or installed repair. Native process,
  source-aware conversation, handoff/acceptance, retention/migration,
  inspection/sweeps and same-conversation local/SSH acceptance remain open.

### 2026-10-24 -- Local and seedless resolution slice merged

- #8615 merged as `4a2aca5ee437d1c4e004c58c0bcaa6bf65fb133c` after
  current-head approval with no findings, green required CI and empty closing
  references. Both parent issues remain open. The final source packet passes
  400 engine cases with two platform skips and 240 Manager cases with twelve
  skips; native process/IPC tests are explicitly selected, not default unit CI.
- Re-read both canonical plans. Only the bounded local/SSH resolution identity
  item is newly complete. Resolution, forwarding and target admission remain
  distinct from native process start, conversation identity and prompt uptake.
  No installed prompt-loss or same-conversation repair is asserted.
- All 27 authored path blobs matched the provider-confirmed merge exactly.
  Retained a recovery backup and advanced the clean source without replay.
  The next slice connects the saved attempt to the existing deferred/backend
  execution owner, preserving source-aware native ownership and treating process
  spawn or argv handoff as weaker evidence than accepted prompt delivery.
- Retention/legacy migration, readers/sweeps, containing release, authorized
  rollout and complete installed local/SSH acceptance remain active. Keep this
  worktree and both efforts open rather than finalizing a completed phase.
