# Retained Inbox Message Evolution

## Goal

Deliver versioned, migrate-first retained messages with a stable rejection
envelope, so supported legacy work reaches the authenticated current receiver
and unsupported work receives a correlated terminal explanation without
blocking valid receiver enrollment.

- **Slug:** `retained-inbox-message-evolution`
- **Repo:** copilot-extensions
- **Branch(es):** serial reviewed intent and implementation PRs to `dev`
- **Created:** 2026-11-16
- **Status:** Draft
- **Vision:** extends [negotiated contract envelope](../../../visions/plugins/agent-bridge/README.md#negotiated-contract-envelope)
- **Umbrella issue:** #1460 (bounded retained-message slice only)

## Guiding Intent

Type compatibility is independent of transport authentication and receiver
ownership. Preserve original IDs, ordering, idempotency keys and effect receipts.
Use the existing queue, admission transaction and receipt owner, not a second bus.
Known legacy controls migrate first. Unsupported pre-effect messages terminate
as rejected, not consumed. Previously started or uncertain work remains honestly
uncertain, with no control execution or SDK resend during conversion.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Retained message driver | Intent, implementation, focused validation and release | Serial isolated source worktree PRs |

## Coordination

- **Topology:** one writer for this bounded slice.
- **Host (owns PRs):** Retained message driver.
- **Delegates:** none; broader #1460, #6394 and #10765 owners remain unchanged.
- **Handoff:** retain source, validation, review and release evidence.
- This is not completion or takeover of the broader contract-evolution effort.

## Context

Both native-compatible and strict retained adapters currently refuse enrollment
when legacy controls remain pending. The retained consumer does not execute the
legacy control poller. History reconstruction requests can therefore prevent
the only current receiver from enrolling. History replay reconstructs the
bounded represented event view; it must not rerun model prompts or commands.

## Request

Public-safe capture: provide a future-compatible minimal base envelope, explicit
per-type versions and supported legacy migration; reject unsupported work with
original correlation and an intelligible reason, clearing deliverable pending
work while retaining audit. Registration must progress independently of message
compatibility. Preserve authentication, unique worktree/current-receiver
binding and unknown-effect history. Exact request wording is retained by the
requesting owner, not reproduced in public source.

## Scope Approvals

Source changes, isolated synthetic tests, ordinary reviewed PRs and accepted
release only. No installed runtime update, service restart, production queue
mutation, session restart or live extension reload is authorized here.

## Plan

### Phase 1 — Review the contract
- [ ] Accept this intent, narrow ownership and migration/rejection design.

### Phase 2 — Deliver and qualify
- [ ] Extend existing queue/receipt contracts with base/type versions and explicit rejection; declare negotiation independently of authentication.
- [ ] Migrate known unversioned schemas transactionally, retaining originals and never starting effects; remove compatibility enrollment vetoes in both adapters.
- [ ] Connect supported history reconstruction and receiver-side rejection through existing owners; producers never indefinitely queue retired controls.
- [ ] Update authoritative docs and retirement contribution requirements; add changefiles for affected consumers.
- [ ] Pass focused source/API/SQLite/extension qualification and required checks; address review and merge to `dev`.
- [ ] Verify ordinary accepted Main promotion and released artifact identity; settle only this workspace.

## Validation Plan

- [ ] Real isolated queue/API with 24 legacy replay controls: enroll, migrate and actually consume supported history reconstruction.
- [ ] Unknown type/version/required capability: enroll, reject before effects, observe original correlated receipt and empty deliverable queue.
- [ ] Repeated enrollment/migration, lost responses and CAS races: stable IDs/order/keys, one disposition and no duplicate effects.
- [ ] Wrong authentication/session/resource and stale receiver remain refused.
- [ ] Started/uncertain effects are never relabeled proved-not-delivered or replayed.
- [ ] Old database first-touch, supported old/new peers and additive envelope fields; unsupported required semantics remain explicit.
- [ ] Canonical contained Python and actual Node consumer tests, lint/types/contracts, exact-source CI/review and release proof. Live installed qualification remains outside this source-only authority.

## Proposal

Reuse `live_messages`, current consumer CAS and original receipt serialization.
Stamp the minimal base separately from each type revision. Legacy conversion is
an explicit registry keyed by known stored kind/schema, not a guess about future
messages. Keep disposition separate from `delivered_at`/`consumed_at`.
Validate the stable correlated envelope before interpreting payloads.

## Journal

### 2026-11-16 — Intent
- Captured migrate-first and stable terminal rejection requirements before code.
- Compatible target adoption verified through the efforts owner's native probe.
