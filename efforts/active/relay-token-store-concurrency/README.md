# Relay Token Store Concurrency

- **Slug:** `relay-token-store-concurrency`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase managed pull requests
- **Created:** 2026-11-04
- **Status:** Active - implementation validated; review and merge pending
- **Vision:** [agent-codespaces](../../../visions/plugins/agent-codespaces/README.md) - reliable authenticated venue access
- **Umbrella issue:** #10591

## Guiding Intent

Independent processes must agree on stable per-venue relay secrets without
losing unrelated entries or replacing unreadable authorization state. A
temporary file-access failure must not silently become an empty token store.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner | Reproduction, implementation, tests and PR stewardship | Managed source worktree |

## Coordination

One source owner, sequential planning and implementation PRs. This finite slice
does not take over connection ownership, listener readiness, transport recovery,
credential refresh, dispatch replay or deployment.

## Context

#10591 records a thread-only writer lock and one fixed temporary filename for
a store shared by independent CLI processes. Bridge also reads that file
through its file-backed validator/authorizer. Existing JSON entries include
legacy string tokens and structured tokens with resource allowlists.

## Request

Public-safe source scope from #10591: serialize relay-token read-modify-write
across processes, preserve stable tokens and the existing JSON format, tolerate
bounded transient file-access failures, and refuse to overwrite unreadable or
malformed authorization state. Prove behavior with synthetic independent
processes and Windows held-open-file tests, including Bridge consumers.

## Scope Approvals

Source changes, synthetic tests and normal issue/PR publication only. No live
credential inspection, service restart, token export, credential migration or
installed rollout is part of this effort.

## Plan

### Phase 1 - Reproduction and compatibility
- [x] Reproduce lost updates, temporary-file collision or Windows replacement failure with synthetic state; distinguish the actual failures observed.
- [x] Trace issuer, revocation and Bridge readers; retain scope-denial behavior and legacy entry compatibility.

### Phase 2 - Implementation
- [x] Use existing OS-held cross-process locking and unique atomic publication primitives for the complete mutation transaction.
- [x] Retry transient sharing/access failures within a bounded budget; preserve old bytes on persistent read/parse/write failure and clean only attempt-owned temporary files.
- [x] Keep normal readers lock-free where atomic publication permits; fail authorization closed on persistent unreadability without rewriting state.
- [x] Document compatibility, error behavior and the mixed-version limitation: old writers do not participate in the new lock.

### Phase 3 - Delivery
- [x] Add changefiles for affected plugins/shared-library consumers and pass focused tests, lint and required contract checks.
- [ ] Steward the implementation PR through review, required CI and merge; record release status without implying deployment.

## Validation Plan

- [x] Focused unit tests for transient/persistent access errors, malformed state, stable token reuse, revocation and allowed/denied scope.
- [x] Real independent-process first-touch and concurrent update/revoke tests on synthetic stores; no global/runtime paths.
- [x] Native Windows held-open-reader replacement failure/recovery and bounded timeout test with unchanged old bytes.
- [x] Issuer-to-Bridge validator/authorizer consumption of the actual written store.
- [x] Record all exercised tiers and explicitly explain external-venue exclusions: this slice changes only host file transactions; no live store or service is authorized.
- [ ] Resolve every Plan/Validation item before marking this finite effort Done; deployment remains outside its completion claim.

## Proposal

Reuse existing lock/publication libraries rather than introduce a second
coordination service. Preserve the file format so existing readers remain
compatible. Do not retry an uncertain dispatch or rotate existing secrets as a
substitute for repairing the store. Review this plan before implementation.

## Journal

### 2026-11-04 - Planning

- Claimed #10591 after searching relay-token issues and open PRs.
- Source inspection confirms the thread-only lock, fixed temporary filename
  and empty-state fallback. This supports a defect hypothesis, not proof of
  any particular field incident.
- Request, intent and plan compared against the bounded source scope; private
  motivation is intentionally absent.

### 2026-11-04 - Reviewed implementation and source validation

- Planning #10593 approved and merged before implementation.
- Seven new regression cases failed against the original code: malformed
  stores and persistent read errors did not refuse mutation, and a synthetic
  Windows replacement error was not retried. These reproduce specific file
  failure modes, not an entire field incident.
- Implemented the existing `OperationLock`/`atomic_write_text` transaction,
  unique temporary publication, strict shared reads and bounded Windows
  retries. No dependency, CLI, wire or JSON schema changes.
- Codespaces packet: 62 passed, including five native/independent-process
  cases. Bridge packet: 46 passed, including actual issuer-store consumption,
  scope denial and revocation. Test children and their containment Jobs settled.
- Ruff F/E9, install contract (13 plugins), documentation consistency and strict
  shared-library analysis (23 files, Linux and Windows targets) passed.
  Whole-portfolio `--enforced` analysis stopped at an unprepared unrelated
  project; only the changed admitted library is claimed checked.
- Validation tiers: focused contract/unit tests; real local issuer/reader and
  process integration; first-touch synthetic-store clean-room checks; native
  Windows held-open-file proof. External venue/live store/installed rollout
  intentionally excluded: this slice changes host file transactions only and
  grants no live credential/service effects. Linux native process execution
  was not available on this Windows worker; analysis is not native execution.
- Remaining gate: implementation review, required CI and merge, then close
  this source-only effort without implying release or installed acceptance.

### 2026-11-04 - Review correction

- Review found that the relay's synchronous authorization callback ran on its
  asyncio serving loop. The new retry budget would therefore stall unrelated
  requests. Both validator and authorizer callbacks now run off-loop, with
  copied request fields and unchanged scope-denial handling.
- Two file-contention/ping responsiveness regressions failed before correction
  and pass afterwards. Complete Bridge relay packet: 48 passed. Shared relay
  policy/server and token-gate packet: 65 passed. Strict library analysis and
  Ruff passed.
- CI found the changed Bridge reader's registered source fingerprint was stale.
  Refreshed only that implementation-only source record and its explanation;
  no historical fixture/capture was rewritten. Current contract validation
  passes for two contracts and 1,005 fixtures; committed-diff validation follows
  the correction commit.
