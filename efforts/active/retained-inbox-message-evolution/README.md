# Retained Inbox Message Evolution

## Goal

Deliver versioned, migrate-first retained messages with a stable rejection
envelope, so supported legacy work reaches the authenticated current receiver
and unsupported work receives a correlated terminal explanation without
blocking valid receiver enrollment.

**Kind:** completion.
**Root source:** public-safe rendering of the requesting owner's instruction;
exact original wording remains with that owner.
**Acceptance:** both retained adapters and the real native consumer satisfy
versioned migrate-first delivery, terminal correlated pre-effect rejection,
nonblocking enrollment and unchanged resource/authentication/effect fences;
focused qualification, required CI/review and ordinary accepted release pass.
**Finalization criterion:** complete all Plan and Validation Plan items, verify
released source/artifact identity and settle only this source workspace's claims.
Installed rollout and production qualification are excluded.
**Now:** qualifying the review's expired-history fix with the expanded 688-case
portfolio. Focused API/extension and strict typing pass; complete prior 681-case
closed-Core capture is retained. Current full capture/replay, review, merge and
accepted release remain open.
**Return:** finish Phase 2 against the reviewed intent, qualify the actual
consumer and obtain accepted release. Installed rollout remains excluded.

- **Slug:** `retained-inbox-message-evolution`
- **Repo:** copilot-extensions
- **Branch(es):** serial reviewed intent and implementation PRs to `dev`
- **Created:** 2026-11-16
- **Status:** Active
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
- [x] Accept this intent, narrow ownership and migration/rejection design.

### Phase 2 — Deliver and qualify
- [x] Extend existing queue/receipt contracts with base/type versions and explicit rejection; declare negotiation independently of authentication.
- [x] Migrate known unversioned schemas transactionally, retaining originals and never starting effects; remove compatibility enrollment vetoes in both adapters.
- [x] Connect supported history reconstruction and receiver-side rejection through existing owners; producers never indefinitely queue retired controls.
- [x] Update authoritative docs and retirement contribution requirements; add changefiles for affected consumers.
- [ ] Pass focused source/API/SQLite/extension qualification and required checks; address review and merge to `dev`.
- [ ] Verify ordinary accepted Main promotion and released artifact identity; settle only this workspace.

## Validation Plan

- [x] Real isolated queue/API with 24 legacy replay controls: enroll, migrate and actually consume supported history reconstruction.
- [x] Unknown type/version/required capability: enroll, reject before effects, observe original correlated receipt and empty deliverable queue.
- [x] Repeated enrollment/migration, lost responses and CAS races: stable IDs/order/keys, one disposition and no duplicate effects.
- [x] Wrong authentication/session/resource and stale receiver remain refused.
- [x] Started/uncertain effects are never relabeled proved-not-delivered or replayed.
- [x] Old database first-touch, supported old/new peers and additive envelope fields; unsupported required semantics remain explicit.
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
- Intent merged in #12937 after Copilot's nonblocking review, addressed index
  feedback and green required CI. Implementation starts from the reviewed plan.
- Temporary operator pencils-down hold received during initial implementation.
  Additive metadata helpers and removal of the two compatibility enrollment
  vetoes are partial, unvalidated local WIP. API/model/producer/receipt/native
  consumer wiring, tests, documentation, changefiles and release remain open.
  No implementation PR, test execution, installed update or production mutation.

### 2026-11-16 — Resumption and functional integration
- Resumed the original source workspace through its existing owner and native
  Phase 2 effort focus, without allocating or launching a second author.
- Integrated original-row type/base revisions, transactional legacy migration,
  receiver-fenced terminal disposition and one-shot represented-history replay.
  Enrollment precedes backlog recovery and never depends on payload support.
- Kept payload capability observations out of immutable receiver identity;
  diagnostic PID refresh remains advisory on the compatible adapter.
- Added conservative legacy-reader fences without marking rejection delivered
  or consumed. Normal/keyed receipts and CLI outcomes expose rejection reasons.
- Qualified the actual extension with controlled SDK/transcript boundaries and
  real Node-to-ASGI/SQLite transport. Added old-database, CAS, authentication,
  uncertain-effect, supported-optional-field and provider-skew controls.
- The first broad local packet hit its original finite limit; narrower packets
  identified and fixed compatibility regressions without increasing budgets.
  Locked checker preparation timed out on one interpreter; the already available
  supported alternative prepared normally. No checker policy was weakened:
  the whole-file strict ratchet passes all changed files on both analysis targets.
- Final changed-source regression checks and ordinary publication remain next.
  No installed runtime, native session or production queue was changed.
- Final bounded residual: 40 controls passed; the complete producer/receiver/
  sender module passed 15 controls, including actual capability negotiation,
  admission, receiver rejection and original receipt observation without resend.
  The strict ratchet passed all 23 changed Python files on both analysis targets.
- Resolved canonical and transitive Core release consumers before publication:
  Bridge, MCP, Driver, Worktrees and Manager, plus the library's own release
  intent. No library version or dependency floor was hand-edited.
- Current HTTP-52 declares 681 cases, retaining all prior cases and adding 43
  focused controls. Declaration preparation, actual capture, independent replay,
  required review/CI and accepted release remain explicit unfinished gates.
- Opened draft implementation #12998 through the native owner and audited its
  public source attribution/authorship. Full source-bound capture remains pending.
- A focused audit check exposed the existing prohibition on attaching SDK
  custody to controls. Preserved that guard; added disposition-specific retention,
  input/terminal/SDK-fact guards instead, validated at writable adoption.
  Current alias handling and original receipt/key recovery preserve terminal
  controls without blocking a legitimate same-process native rename.
  Fourteen focused audit/upgrade/real-Node outcome controls passed.
- Captured all 681 source-bound HTTP-52 cases successfully, then independently
  executed the same complete portfolio without rewriting evidence. Both stayed
  inside the original finite budgets; no case was skipped or assertion relaxed.
  The contract guard passes both contracts and all 1,143 fixtures. Audited all
  220 generated artifacts for task-private identifiers with zero findings.
  Historical HTTP-51 and earlier evidence remains unchanged.
- Reconciled a registry-only upstream conflict through the canonical codec,
  retaining all genuine captures and three reviewed upstream implementation-only
  classifications. All owned code/tests/evidence remained byte-identical.
- Required CI caught the schema owner at 1,004 lines after the final audit guard
  integration. Centralized its existing metadata/guard preparation in the owning
  message-contract module, leaving the schema owner at 999 lines without changing
  the cap. Six focused fresh/legacy/audit/real-Node controls pass.
  The critical-source guard requires a fresh complete code-bound capture for
  this refactor; no semantic classification or provenance bypass is accepted.
- The fresh capture passed 678 cases but exposed three real copied-Core startup
  failures: the compact export omitted the current daemon-instance IO module.
  Preserved every assertion and deadline, exposed child stderr without masking
  cleanup errors, and fixed the export through its Core owner. All three focused
  copied-Core/portable regressions now pass; all 27 changed Python files pass
  strict analysis. A fresh complete capture/replay remains required.
- The corrected closed-Core source passed all 681 cases and produced 221 genuine
  execution artifacts. Retained that packet before addressing review. Copilot
  identified a real Medium false-completion bug: expired history was discarded
  with HTTP success. Added explicit atomic backfill acceptance and required it
  in the actual retained callback, including zero-count success and conservative
  missing/malformed/expired acknowledgements. Focused and complete updated-source
  validation remain pending; no installed or production effects are authorized.
- Focused API/owner qualification passes eight controls, including current,
  empty, expired, repeated and concurrent acceptance. The actual extension VM
  suite passes with explicit/missing/malformed/wrong-context acknowledgements
  and a second poll proving no repeated history POST. Its endpoint fixture now
  preserves the real server's one-shot grant instead of inventing a second
  grant. The updated complete portfolio declares 688 cases; strict typing,
  capture/replay and current-head review/CI remain open.
- All 31 changed Python files pass whole-file strict Linux/Windows analysis.
  The existing route/startup callback seams now carry explicit types and public
  model reexports; no ignores or admission exceptions were added. Ruff, the
  unchanged module-size gate and the 13-plugin install contract pass. Canonical
  declarations for all 688 cases are prepared, not execution evidence.
- Reconciled a real upstream update before final execution: three serving
  capabilities and their four declared source owners. Its Core IO export fix
  was identical. Consolidated owned history without changing its tree, then
  resolved only current generated HTTP-52 conflicts through the canonical
  registry/publication owners. Preserved immutable provenance plus 440 genuine
  incoming records and verified every other owned code/test/doc/release byte
  unchanged. The superseded partial run is not execution evidence. Refreshed
  declarations and all 30 now-differing Python files pass strict analysis on
  both targets; Core's identical upstream Python fix no longer belongs to this
  diff. Full combined-source capture/replay and review/CI remain required.
- Combined source passed all 688 cases in the genuine full write capture and
  produced 225 artifacts; the complete changed-output privacy audit found zero
  private markers. Preserved that execution before integrating a subsequent
  upstream SDK namespace-loader update and its actual VM/doc adjustments.
  Final-source independent replay, exact-source checks and release remain open.
- Integrated that SDK update through one owned rebase. Canonical metadata
  reconciliation retained another 220 genuine incoming records; staged-byte
  verification allowed only the exact SDK spelling updates in owned VM/docs.
  The six actual SDK-facade/extension controls pass. Final combined-source
  declarations are prepared; complete current execution/replay remains open.
