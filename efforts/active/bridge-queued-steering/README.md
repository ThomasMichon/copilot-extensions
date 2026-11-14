# Bridge Queued Steering

## Goal

Reproduce and fix messages that remain admitted or SDK-accepted without reaching
the running agent. Provide a post-tool pending-input notification, an exact
model-facing submission receipt, and a timed post-tool content fallback that
cannot duplicate a later SDK delivery. Drive the source fix through tests,
review and merge in the layer that actually owns delivery.

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
- [ ] Add a bounded, coalesced post-tool notification with mute and ordering
  preserved; no network or child process on an in-process hook hot path.
- [ ] Correlate actual root input to exact message IDs for consumption receipts;
  unrelated, nested, duplicate and earlier events cannot settle them.
- [ ] Add timed content fallback with one authoritative delivery arbitration,
  including late SDK completion and restart/unknown-outcome handling.
- [ ] Publish supported-runtime discovery and explicit refusal for unavailable
  semantics; preserve compatible prior operations and supported rollback.

### Phase 3 - Acceptance and closeout

- [ ] Pass focused source, process/transport, fresh-state, lint and contract
  checks; identify the tested runtime and unavailable native lanes honestly.
- [ ] Steward implementation through exact-source review, required CI and merge.
- [ ] Resolve every Plan/Validation item or transfer independent adoption to a
  named tracked objective, then archive; source merge is not deployment.

## Validation Plan

- [ ] Admitted/no-SDK and SDK-accepted/no-consumption controls fail before the
  relevant repair and progress afterward, including an idle receiver.
- [ ] A real supported post-tool hook supplies the end-turn notification,
  coalesces repeats and respects intentional mute.
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
