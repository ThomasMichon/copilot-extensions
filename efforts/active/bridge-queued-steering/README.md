# Bridge Queued Steering

## Goal

Reproduce and fix messages that remain admitted or SDK-accepted without reaching
the running agent. Provide a post-tool pending-input notification, an exact
model-facing submission receipt, and a timed post-tool content fallback that
cannot duplicate a later SDK delivery. Drive the source fix through tests,
review and merge in the layer that actually owns delivery.

The immediate assigned source scope is complete machine-wide registration and
session discovery: every worktree session, active anchor session across
registered repositories, mux session and ACP Session Host must remain
discoverable across Bridge restart/update and session resume. The ordinary
sessions listing must include the complete listed-worktree inventory without
inventing native conversations or liveness. Complete focused tests, a live
catalog/worktree comparison, normal review, required CI and the repository's
Maintainer self-merge flow, then return to the continuing Driver objective.
This priority does not grant rollout or complete the broader receipt/fallback
campaign.

- **Slug:** `bridge-queued-steering`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs targeting `dev`
- **Created:** 2026-11-13
- **Status:** Active
- **Vision:** [Bridge attention boundary](../../../visions/plugins/agent-bridge/README.md#delegation-control-and-attention-boundary)
- **Umbrella issue:** #6394, bounded queued-steering follow-up

## Guiding Intent

Queue admission, SDK acceptance, actual model-facing submission and work
completion are different facts. A healthy registered receiver must not conceal
a stalled consumer. Reuse the one existing inbox and receipt identity, not a
second queue, observer service or prompt-per-message acknowledgement loop.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driver delivery owner | Reproduction, delivery repair, tests and PR stewardship | This effort and #6394 |
| Existing messaging and lifecycle owners | Retain compatibility, routing, carrier, registration and resource custody | #12487, #11448, #11445, #10765 |

## Coordination

The finite #13734 idle-transition and truthful-hint slice, accepted in #13740,
belongs to
this existing Driver delivery owner and Phase 2, not a second inbox or
implementation appointment. Preserve the broader #6394 publisher decision,
its bounded queued-steering claim, and the independent routing, registration,
compatibility, receipt and resource custodians. This intake leaves #6394
OPEN and does not expand the continuing campaign's execution authority.
The source's later, possibly separate resumed-headless carrier handshake
failure remains at OPEN #13734 under the existing carrier/client-routing
owners; these five rows do not receive or complete that distinct observation.

Reviewed #13740 delivered the five planned P13734/V13734/G13734 clauses and
dated Journal/intake route. Current complete slice and source/discussion
readback accepted only that idle/hint scope; the separate carrier observation
remains unreceived. Keep the whole source OPEN while
its separate carrier observation lacks complete accepted handling; an idle
slice merge is not whole-source receiving. Source implementation and controlled
coverage from #13770 are recorded below; native qualification remains open.
Existing Request, Scope Approvals, checked source-notification
credit and provider-contract limits remain unchanged; no live prompt replay,
session replacement, new native probe, rollout or resource transfer is granted.

- **Topology:** one source writer; sequence providers before consumers.
- **Host (owns PRs):** Driver delivery owner.
- **Delegates:** none assigned.
- **Handoff:** preserve this objective, exact source state, pending gates and
  original-message uncertainty through the existing owner.
- Coordinate actual overlapping edits; do not supersede another contributor's
  open PR or take over an active workspace. Carrier acquisition and client
  routing changes remain with their current owner. Container authentication
  injection is a separate consumer objective, not part of this effort.
- #6394's broader target-resolution and acceptance items, #11448's subscription
  work and #12487's compatible-send contract retain their existing owners.
  This source repair does not close those parents.

## Context

Reported failures include both admitted messages with no SDK acceptance and
SDK-accepted messages without consumption, including idle receivers. These are
distinct boundaries; continuous agent activity alone cannot explain both.
Current source has periodic inbox draining, separate represented-event receipt
production, legacy and retained delivery, and optional Driver-backed sends.
Registration or fresh attention metadata does not prove consumer progress.

## Request

Public-safe capture of the relayed request, with deployment-specific examples
and identifying details omitted:

1. **NOTIFY:** when inbox messages are pending, a `postToolUse` hook tells the
   agent that messages are waiting and it must end its turn for steering.
2. **RECEIPT:** use the `userPromptSubmitted` boundary to confirm the steering
   message actually reached model-facing input before marking it consumed.
3. **BACKUP:** after a queued-prompt timeout, inject the message itself through
   post-tool additional context and complete its receipt without delivering it
   again if the SDK prompt later lands.

Own reproduction, root-cause analysis, the three mechanisms, tests and normal
review/CI through merge. Coordinate with the Bridge/Session Host/mux core so
delivery is implemented once in its owning layer.

## Scope Approvals

The request authorizes source diagnosis, implementation, bounded owned fixtures
and normal publication. It adds no installed-plugin update, shared service
restart, rollout, credential acquisition, live prompt replay, session replacement,
new spending or resource-custody authority. Existing review and CI gates remain.
An observed receipt or matching reference is not new effect authority.
The assigned local live catalog/worktree comparison is read-only validation;
it adds no installation, reload, service, Job, VM, credential or prompt action.

## Plan

The following sequencing and implementation controls are agent recommendations,
not additional operator requirements.

### Phase 1 - Reconcile and review

- [x] Separate the two reported failure boundaries and map the existing delivery
  owner without duplicating routing, carrier or container-auth work.
- [x] Review this source-only plan before implementation.
- [ ] Verify the actual supported hook, SDK send and model-facing event APIs;
  reproduce stalled drains and queued-but-unconsumed sends in owned fixtures.

### Phase 2 - Repair the complete delivery path

- [ ] Correct the confirmed admission-to-consumer failure without replacing a
  healthy session, replaying an uncertain effect or hiding unsupported state.
- [x] Add a bounded, coalesced post-tool notification with mute and ordering
  preserved; no network or child process on an in-process hook hot path.
- [ ] Correlate actual root input to exact message IDs for consumption receipts;
  unrelated, nested, duplicate and earlier events cannot settle them.
- [ ] Add timed content fallback with one authoritative delivery arbitration,
  including late SDK completion and restart/unknown-outcome handling.
- [ ] Publish supported-runtime discovery and explicit refusal for unavailable
  semantics; preserve compatible prior operations and supported rollback.

#### Finite idle transition and truthful notification from #13734

Agent-recommended complete receiving within this same Phase 2.

- [x] **P13734-1:** Preserve the reported Windows interactive registered-session
  case, including resume/re-registration: pending inbox work must reach the
  supported SDK submission path promptly on idle without waiting for another
  tool call, operator turn or scheduled prompt. Reconcile the actual idle/
  registration/delivery owner rather than assuming an idle state or heartbeat
  proves submission. The reported two-minute stall and later roughly
  twenty-to-forty-minute batches are incident observations, not a new timeout
  budget or proof of a shared cause with historical #12057. A later reported
  repeat kept the same input pending through about twenty tools after the
  next operator turn and delivered roughly thirty minutes after queuing;
  preserve it as corroborating incident evidence, not a new reproduction.
- [x] **P13734-2:** Make the post-tool hint truthful about that selected runtime.
  If ending the turn does not guarantee idle submission, do not instruct the
  agent to end its turn while promising that queued steering will be submitted.
  Keep the unsupported delivery gap explicit instead of treating a wording
  correction as an actual delivery repair. Preserve bounded/coalesced/muted
  notice behavior, original message IDs/order/sender provenance and the
  distinctions between admission, SDK acceptance, model input and consumption.

##### Local Validation for #13734

- [ ] **V13734-1:** Reproduce and qualify the exact pending-hint -> end-turn ->
  idle sequence through the actual supported owning hook/SDK boundary, for a
  normal registration AND resume/re-registration. Withhold unrelated tool,
  operator and scheduled turns; show the pending work progresses on the
  supported idle path rather than arriving attached to a later unrelated turn.
  Distinguish controlled source/API fixtures from actual selected Windows
  interactive qualification and honestly retain unavailable provider evidence.
- [x] **V13734-2:** Cover messages already pending at idle and multiple arrivals
  while idle, plus active-turn, intentional mute and unavailable/unsupported
  submission controls. Verify the hint cannot promise an unsupported wake,
  preserve ordering and exact receipt identity, and prevent duplicate or
  falsely consumed messages if late SDK progress races existing recovery.
  Retain original containment, deadlines and cleanup; no live inbox replay,
  native session, service or process action is authorized by this plan.

Source implementation and controlled API coverage above merged in #13770.
V13734-1 and G13734 retain actual selected-runtime and model-facing qualification;
the checked source items do not claim installed acceptance.

#### Machine-wide registration and complete catalog

- [ ] Reconcile the actual registration failure through the existing native
  producer, including missing startup identity, resume and service replacement.
  Preserve process/session identity, current ownership and explicit unavailable
  receiver state rather than treating a visible process as a functioning SDK.
- [ ] Cover worktree and active anchor sessions across registered repositories,
  all mux sessions and independently launched ACP Session Hosts. Keep original
  component and installer owners; inventory must not create another controller.
- [ ] Make ordinary session listing a superset of the listed-worktree inventory
  while retaining actual native IDs, source kinds, inactive inventory and unknown
  observations separately. Preserve supported legacy consumers and optional
  provider operation across version skew.
- [ ] Add focused restart/update/resume, multi-repository/anchor/mux/Host,
  identity, unavailable-source and ordering controls. Run a read-only live
  comparison on the selected machine: no listed worktree is omitted and the
  Bridge catalog count is at least the listed-worktree count. Record executing
  consumer/provider versions; no fabricated rows or model-acceptance claim.

### Phase 3 - Acceptance and closeout

- [ ] Pass focused source, process/transport, fresh-state, lint and contract
  checks; identify the tested runtime and unavailable native lanes honestly.
- [ ] Steward implementation through exact-source review, required CI and merge.
- [ ] Resolve every Plan/Validation item or transfer independent adoption to a
  named tracked objective, then archive; source merge is not deployment.

## Validation Plan

- [ ] **G13734:** Resolve every P13734/V13734 and continuing Coordination
  clause at the original finite idle/registered/resumed interactive boundary:
  no unrelated-turn dependency, truthful supported/unsupported hint behavior,
  multiple pending arrivals, preserved ordering/identity and no false
  consumption or duplicate delivery. Keep actual Driver/provider/registration/
  receipt/native/resource owners, original bounds and broader #6394 gates.
  Notification source credit, a send reply, idle metadata or a wording change
  alone is not model-input or installed qualification; unavailable delivery
  evidence remains unresolved.

- [ ] Admitted/no-SDK and SDK-accepted/no-consumption controls fail before the
  relevant repair and progress afterward, including an idle receiver.
- [ ] A real supported post-tool hook supplies a truthful pending-input notice,
  coalesces repeats and respects intentional mute without an unsupported
  end-turn promise.
- [ ] Exact root model-facing input settles only its own message IDs, including
  consume-before-send-return, unrelated input, nesting, duplicates and arrivals
  racing with a hook snapshot.
- [ ] Timed fallback and a late queued SDK prompt expose the body to the model
  once, not twice; receipt loss/restart preserves original uncertainty and IDs.
- [ ] Fresh database/extension state, re-registration, supported old/current
  peers and rollback retain ordering, attribution and truthful unsupported state.
- [ ] Contained process and transport checks, applicable native qualification,
  exact cleanup, source checks, final review and required CI are recorded.
- [ ] No actor-free or simulated result is reported as native model acceptance,
  semantic reading, deployment or resolution of the broader parent programs.

## Proposal

First prove the selected runtime's actual APIs and deduplication boundary.
The requested hook/event names are intent, not evidence that an API is present.
Do not silently reintroduce removed hooks or call an earlier notification a
model-submission receipt. A fallback needs an enforceable way to suppress its
late queued counterpart before model input; observation after duplicate input
is too late. Keep unavailable semantics explicit rather than claim exactly-once
delivery from an SDK promise or a timer.

## Journal

### 2026-11-21 - Degrade selected-launcher failures and reconcile upstream

- Assessed the next clean review's Windows-launcher caveat against the actual
  process owner. Job-assignment and suspended-resume failures raise
  `RuntimeError`; daemon startup and both synchronous catalog owner reads now
  report those inventories unavailable rather than aborting readiness or
  discarding independent targets. No alternate launcher or replay was added.
- Added controlled launcher-failure regressions for daemon startup, repository
  enumeration and one failed project alongside a healthy independent project.
  The expanded eight-file packet passed 214 cases; the five promotion units
  and 13-plugin install-contract check passed.
- Backed up the complete source before reconciling upstream. Only combined
  producer metadata conflicted; canonical source archives remain consistent,
  the regenerated producer retains contract revision 36, and all 33 owned
  Python files are byte-identical to the pre-rebase source. All 33 changed
  Python files passed whole-file strict typing on Linux and Windows.
  Publication, current-head review and required CI remain completion gates.

### 2026-11-21 - Keep repository status out of agent topology errors

- Addressed the next review's valid streaming regression: repository-inventory
  unavailability now has its own typed resolver status instead of entering
  fatal agent topology diagnostics. The existing private snapshot preserves
  that optional status, omits it for healthy legacy-compatible records and
  refuses noncanonical explicit false values.
- Worktree catalogs still report incomplete discovery while retaining healthy
  independent rows. Actual agent-route and CLI streaming/initial-subscription
  regressions prove a healthy configured agent remains listed when the
  repository owner is unavailable, including after snapshot restoration.
- The complete eight-file contained packet passed 211 cases, including resolver
  and worktree-runtime compatibility. The five changed Python files passed
  whole-file strict typing on Linux and Windows; both final stream fixtures,
  lint, module limits, source bindings, docs and install-contract checks passed.
  Current-head review and required CI remain completion gates; no installed
  runtime was changed.

### 2026-11-21 - Strict daemon startup inventory

- Assessed the clean review's startup-discovery caveat before merging and
  corrected an actual legacy fallback: daemon startup now shares the CLI's
  selected-owner, strict repository reader. Explicit prepared inputs preserve
  existing direct builder compatibility without invoking a provisioning shim
  or silently replacing registry failure with a successful empty inventory.
- An inventory-scoped unavailable status survives private resolver snapshots and qualifies
  all worktree-list render modes and the forwarded target catalog. Healthy
  independent workspace rows remain visible; a qualified empty registry is
  still successful. No registry schema, native identity, lifecycle ownership or
  activation boundary changed.
- The focused listing packet passed 122 cases, including real captured
  selected-owner processes, startup with and without topology, unavailable,
  timed-out, nonzero, partial and malformed inventories, and snapshot continuity.
  The four changed Python files passed whole-file strict typing on Linux and
  Windows; four final typed snapshot/provider compatibility controls, five
  promotion units and the 13-plugin install-contract check also passed.
  Exact-head review and required CI remain publication gates. Source fixtures
  do not establish installed or fleet-wide adoption.

### 2026-11-21 - Listing review edge cases

- Reconciled the candidate onto current `dev`, retaining upstream unit entries
  and regenerating combined source archives and producer hashes. The prior
  published tip remains backed up until managed publication is confirmed.
- Inventory-only service reads now use the attributable Worktrees owner and
  explicit project/passive arguments, with selected environment and bounded,
  owned process cleanup. Local catalog completion rechecks earlier source
  expiry after later repository reads, without exporting monotonic deadlines.
- Empty-CWD Host observations require a matching instance and exact process
  identity before coalescing; known differing scopes remain distinct.
  Explicit terminal-status listings recover historical managed records without
  restoring all history to the ordinary active catalog.
- All 110 focused source regressions passed, including a real selected-owner
  process and environment scrubbing. Strict typing passed all 33 changed Python
  files on Linux and Windows targets. A read-only source CLI comparison
  covered every one of 211 local workspace keys in both text and catalog
  JSON; legacy JSON retained its array and degraded sources stayed explicit.
  Final publication, fresh review and required CI remain completion gates.
  These corrections do not qualify installed recovery, native model input,
  activation or rollout, or settle any original uncertain message.

### 2026-11-21 - Direct Host locators and finite receiver settlement

- Caller-selected Host state files are attributed through neutral file
  observations in the existing process registration. Runtime/artifact ownership
  does not change; the compact dependency closure includes the observer.
  Verified Hosts without usable metadata remain unbound and unavailable rather
  than being omitted or acquiring a native conversation identity.
- The Windows command-telemetry reading fixture can finish producer timing
  before all receiver fsync work settles. Its finite receiver witness is awaited
  outside producer timing and reported separately. The full readback assertion,
  production budgets and negative modes remain unchanged; this bounded test
  correction stays coordinated under #13545.

### 2026-11-21 - Inventory continuity and schema-safe consumers

- Inventory-only sources participate in ordinary cache startup and private
  resolver/cache handoff without entering agent resolution. Actual local
  execution identity prevents duplicate discovery through machine-keyed agents;
  only enabled local agent discovery counts as existing inventory coverage.
- The default text listing includes every source. Default JSON retains the
  managed array; `--all-sources` explicitly selects catalog JSON. Dispatch's
  body/attention readers accept either shape without new CLI arguments,
  subprocesses or a lockstep peer upgrade.
- Strict repository enumeration requires complete inventory authority. Current
  owner status and mux fields replace stale observations while preserving Bridge
  linkage. Launch-produced Host records normalize verified scope before dedup.
- Refresh snapshots retain absolute expiry while deadline-free legacy records
  retain their shape. Both source ages and elapsed read time bound freshness.
  Focused continuity, catalog, consumer and actor-free checks pass. An actual
  source-CLI read included every local listed workspace in both default text and
  explicit catalog JSON, with default JSON compatibility preserved; installed
  receiver recovery and fleet activation remain unqualified.

### 2026-11-21 - Registration and complete-catalog source candidate

- Corrected a load-time registration dead end: missing Bridge credentials no
  longer prevent the extension from arming heartbeat and delivery timers.
  Heartbeat refreshes the selected endpoint and adopts the current SDK session
  identity even when an idle resume produces no event.
- Reused the typed target union for ordinary session listing, preserving the
  managed-only array through an explicit compatibility switch. Local passive
  inventory includes registered source-only repositories without exposing them
  as agents. Unavailable/stale rows remain observations, not live conversations.
- Standalone Host observations reuse existing daemon registrations and exact
  process identity, without controller adoption or a new registry. Matching
  managed/Host observations are deduplicated by actual process birth and scope.
- The SDK's complete package declaration includes the passive observer, and both
  canonical synchronizers refresh its dependent caller-lifetime source archives.
  Producer hashes change without changing uninstall contract revision 36.
- The contained compatibility packet passed 553 Python tests; the final Host
  deduplication packet passed 38 focused cases. Extension controls passed 156
  Node cases and two actor-free inventory units passed. Whole-file strict
  typing passed for all 18 changed Python files on Linux and Windows targets.
- A read-only source-CLI comparison against real local registered-repository
  inventories omitted no listed workspace. The executing consumer was the
  source candidate with existing installed providers, not an installed rollout.
  Native receiver registration, fleet restart/update and model acceptance
  remain unqualified; no rollout or activation authority is added.
- The optional frozen HTTP evidence checker reported stale source bindings.
  Its owning contract policy exempts ordinary source changes from recapture;
  no wire generation, fixture or historical provenance was rewritten.

### 2026-11-21 - Idle source repair merged; complete registry follow-up assigned

- #13770 merged after exact-head Copilot approval, empty inline findings and
  all current required CI. The normal Maintainer self-merge flow retained
  review and CI gates.
- All 153 focused controlled source/API tests pass. Root idle, actual joined
  session identity, resume/re-registration, missing-row heartbeat recovery,
  multiple idle arrivals, mute, shutdown and original-receipt fences are covered.
  Native model-facing qualification, installed adoption and the separate
  carrier observation remain open.
- The complete machine-wide registration/catalog repair continues in a
  separate source workspace, independently of the completed idle-source item.
  Existing registration failure evidence remains at #11800. This is not
  whole-campaign completion or rollout permission.

### [2026-11-20 - Proposed finite idle-transition and truthful-hint receiving](../backlog-reconciliation/journal/2026/11.20-idle-steering-and-existing-keepers.md)

### 2026-11-13 - Bounded source repair captured

- Preserved the separate active Core refactor and its recovery source work.
  Captured this delivery objective and its authority limits; source-only
  investigation and the plan review gate precede implementation.

### 2026-11-13 - Plan accepted

- #12525 merged at head `4acab2c35c25e703c6fc6f526718272308f02017`
  after genuine clean approval `5899739152`, complete empty review threads
  and green required CI `43530218200` plus trusted metadata checks
  `43530218235`. Managed completion verified the merge using the normal
  Maintainer additional-CODEOWNER exception, not a CI bypass.
- Immediately reread this Goal, Plan and full Validation Plan, together with
  the continuing Core parent's gates. Only the plan review item closes;
  actual API verification, reproduction, all three mechanisms and complete
  delivery qualification remain open. No native or installed effect is enabled.

### 2026-11-13 - Delivery boundaries reproduced

- Repaired the actual-extension VM fixture's stale metadata exports and missing
  machine-context dependency without changing production imports or relaxing
  the fixture's strict dependency checks.
- Three controlled cases establish the pre-repair boundaries: an unresolved
  exact-native metadata lookup prevents SDK submission while registration
  heartbeats continue; an unresolved SDK send holds the drain and ACK while
  heartbeats continue; a resolved SDK send yields an ACK but no represented
  model-input event until an actual root `user.message` is observed. These
  are source reproductions, not attribution of a particular live incident.
- The canonical contained `test_plugin_readiness_extension.py` packet passed
  with all embedded Node controls. The first dependency preparation failed
  without exposing its underlying error; the diagnostic retry succeeded
  without changing dependencies, containment or seasoning policy. This is not
  evidence of a resolved dependency defect.
- Public SDK snapshot `540dc4e1ccd4aa174e7602465099a2aa93fab176`
  identifies CLI `1.0.110-3` and declares extension hooks, post-tool additional
  context and pre-model prompt blocking. Its prompt-transformed hook precedes
  history/model input; `hook.end` records output/success, not model-input
  admission. Matching source is not qualification of the selected runtime.
- The full fallback remains blocked on a supported model-input witness for
  post-tool content and restart-safe pre-input suppression of the late SDK
  counterpart. Preserve the original message and uncertainty; do not emit a
  consumption receipt from a hook return, send reply or later assistant
  activity. A bounded provider-contract consultation is outstanding. No
  runtime behavior, installation, native session or retained fence changed.

### 2026-11-13 - Pending-input notice source candidate

- Added the bounded in-process notice through public success/failure post-tool
  callbacks. Existing off-loop delivery/recovery supplies only observed pending
  IDs; a stalled metadata/send promise cannot block the hook. Root/session and
  current-registration checks, mute, a 30-second repeat bound, consumption-race
  filtering and honest 1,000-ID overflow are covered. Clearing the last known
  pending ID re-arms a notice for newly arriving work.
- Retained delivery continues to own exact original-receipt consumption and
  its pending-native fence. Its existing history proof can clear the notice;
  no send, outcome, cursor, original identity or replay decision changed.
- The final contained Python/Node contract packet passed, as did documentation
  consistency and whitespace checks. These are controlled source results,
  not proof that an installed runtime accepts/invokes the hooks or that an idle
  receiver is awakened. The source candidate is not yet published or merged;
  its current-generation contract evidence and remaining publication gates
  are still owed.
- Matching public SDK source also carries these hook declarations in the
  CLI `1.0.106-9` snapshot. The `1.0.110-3` client passes hook registration
  through extension resume; the SDK's internal `registerHooks` method merely
  stores handlers and is not an alternative runtime-support probe. No internal
  method or policy bypass is used.
- The external provider-contract gap has been identified as Copilot CLI/SDK
  runtime-owned, not assigned to a fleet worker or the carrier/client owner.
  Existing coordination is routing that gap to the operator, without an
  upstream filing, replacement worker, resource creation or new native probe.
  The requested prompt-submission receipt and duplicate-safe timed fallback
  remain incomplete; no phase or parent objective is declared complete.

### 2026-11-13 - Root-input evidence transport candidate

- Corrected a separate loss path: the old best-effort event flusher discarded
  actual root-input evidence after a failed event POST. The existing bounded
  buffer now retries those native events, not a send/body or hook-generated
  substitute. An internal weak association retains each event's original native
  session; retries cannot retag it after registration changes.
- Ordinary representation remains best-effort and yields capacity before
  root-input evidence. An all-receipt overflow reports uncertainty off the
  event loop rather than inventing consumption. This in-memory retry is not
  durable restart recovery or duplicate-safe content fallback.
- The final contained selection passed three tests: the full Node contract
  wrapper, actual ASGI/SQLite consumption-before-ACK monotonicity and stable
  event-ID deduplication. VM regressions cover HTTP failure, lost replies,
  ordinary-event bursts and a session change during a failed POST. These
  controls use substituted SDK input and do not qualify native hook timing.
  Existing contract capture, remaining source gates, review/CI and all
  prompt-submission/fallback/provider requirements remain open.

- A coupled root-binding correction excludes sub-agent events from session-ID
  adoption, matching existing turn/receipt scope. A child event carrying its
  own session ID cannot rename the parent's registration or suppress its
  notice. The final actual-extension Node packet passed with that regression;
  native qualification and installed behavior remain unclaimed.

### 2026-11-14 - Shared Core extension extraction

- Carried the tested notification and original-event retry candidates into
  the Core extraction without dropping their uncertainty or no-replay limits.
  Core now owns all registration/delivery modules. Bridge retains one SDK
  adapter and the existing native plugin-root checks; packaged assets and the
  copied Core export use the same resource closure.
- The actual adapter passed source, materialized-payload and isolated
  copied-Core controls with one activation and no suite service. A partial
  payload refuses before activation even when canonical source is available.
  The controlled portable mux flow and moved Node helper contracts passed.
- A broader validation selection exposed a fixture that removed Python while
  selecting PowerShell 5.1. Restoring only that declared prerequisite preserves
  legacy-shell selection; all sixteen binary-frame variants passed. The final
  packaging/frame selection passed 37 cases without skips.
- Current-generation source ownership/capture, exact-source review, CI and
  publication remain pending. Native model-facing receipts, enforceable
  duplicate-safe content fallback and installed qualification remain open;
  this extraction does not complete the queued-steering objective.

### 2026-11-14 - Notification and evidence-retry source accepted

- #12571 merged at head `75f6889ee8341cfffef484e785b307663a04002d`
  after genuine approval `5902339573`, a full review with no open issues or
  limitations, complete empty threads and green current-head CI `43566831199`.
  Managed completion used the normal Maintainer additional-CODEOWNER
  exception, not a CI bypass or a scan-limitation waiver.
- Immediately reread this Goal, Plan and full Validation Plan after recording
  the continuing Core parent's acceptance. The bounded notification source
  item closes; actual native hook/model qualification, the complete admission
  repair, exact prompt-submission receipts and duplicate-safe timed fallback
  remain open. The effort stays Active.
- All five actual SDK facade layouts passed, including ordinary wheel-only
  resources after construction cleanup. Genuine HTTP-52 capture and a separate
  no-write verification each passed all 531 cases without skips at public
  source checkpoint `ef18c1f32585cb0199c381c620992c7d897adf19`.
  The 203 outputs matched the exact compiler plan; strict validation passed
  two contracts and 1,123 fixtures with historical evidence unchanged.
- Isolated wheel construction and the physical reporter interpreter preserve
  the strict contributing-source and parent-link proof. These are controlled
  source/SDK-model results, not native input acceptance or installed adoption.
  Continue through the existing native provider-contract boundary; no hook
  return, send reply or later assistant activity supplies the missing receipt
  or permission to replay an uncertain message. No rollout, new native probe,
  resource transfer or credential operation occurred.
