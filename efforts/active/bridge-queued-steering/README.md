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
- **Status:** Draft
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
- [ ] Review this source-only plan before implementation.
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
