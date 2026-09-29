# Cross-Machine Claim-Handoff Acceptance

- **Slug:** `cross-machine-claim-handoff-acceptance`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase (this effort's own PR, then the implementation PR)
- **Created:** 2026-09-29
- **Status:** Done <!-- Draft | Active | Blocked | Done -->
- **Vision:** closes the remaining gap in #1090's claim-bundle handoff design (atomic ownership transfer across machines)
- **Umbrella issue:** #1090
- **Sub-issues:** #4529 <!-- filed on a premise this effort corrects; see Plan -->

## Guiding Intent

`claims handoff accept <bundle-id>` (shipped in #4527) already does a full
atomic transfer for a same-machine or cross-project consumer. Cross-machine
acceptance -- #1090's requirement 7 -- was left fail-closed because the
implementing agent concluded the suite has no synchronous remote-mutation
surface for another machine's `WorktreeRecord`. That conclusion was wrong: it
searched `agent-bridge`'s specialized session-management API instead of the
suite's already-established, simpler mechanism for reaching another machine's
own `agent-worktrees` installation -- **plain SSH to that machine's own
binstub**, the same pattern already documented and used for cross-machine
`cleanup`/`repos gh`/etc. This effort corrects the architecture record,
recharacterizes the issue filed on the wrong premise, and drives the actual
SSH-exec-based implementation to completion.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Host session | Plans, corrects docs/issues, dispatches and supervises implementation, drives PRs to merge | worktree `lambda-core-wsl-20260928-214255-064f` on `lambda-core` |

## Coordination

- **Topology:** single-participant; independent per-phase PRs from the host's own worktree.
- **Host (owns PRs):** Host session (above).
- **Delegates:** none formally tracked; the host may use in-session sub-agents for implementation legwork, but owns every PR itself.
- **Handoff:** n/a (single participant).

## Context

- #1090 ("Add atomic claim-bundle handoff to a consumer worktree") specs the
  full contract: offer / accept / decline / cancel, atomic all-or-nothing
  transfer, resource-side ownership following the ledger, the finalize
  invariant, and **cross-machine safety** ("a remote consumer must
  synchronously reserve ownership through the lease/bridge authority before
  acceptance succeeds; no best-effort local fallback").
- #4527 (merged) implemented offer/accept/decline/cancel with full atomic
  transfer for same-machine and cross-project consumers: `claim_handoffs.accept()`,
  a new `GitLeaseStore.transfer()` for lease-backed claim kinds, provenance-
  preserving ledger moves, idempotent recovery, and immediate finalize-gate
  visibility. Left cross-machine acceptance fail-closed with an explicit
  error rather than a fake partial implementation.
- The investigation for cross-machine acceptance searched `agent-bridge`'s
  `remote_operations.py` / `transport.py` / `target_exec.py` and found no
  "mutate worktree X's record on host Y" verb, and concluded a **new**
  synchronous remote-mutation primitive was required. That conclusion missed
  the suite's existing, simpler cross-machine execution pattern: SSH straight
  to the target machine's own binstub and let it run the *same local
  mutation code* that already works for same-machine accept -- e.g. the
  documented `ssh wheatley -- aperture-labs agent-worktrees cleanup --clean`
  pattern already used for remote worktree cleanup.
- #4529 ("agent-worktrees: no synchronous remote-mutation surface for
  another machine's WorktreeRecord") was filed on the above wrong premise.
  It should be closed/recharacterized once this effort's corrected design is
  merged, rather than pursued as a real gap.

## Request

Operator (verbatim, across the correcting exchange):

> Cross-machine should be handled via ssh
>
> Help update the necessary vision/architecture touch-updates and drive a
> mini-effort to resolve that

## Plan

### Phase 1 — Correct the record
- [x] Recharacterize/close #4529 as filed on a wrong premise, pointing at this
      effort and #1090 for the corrected direction.
- [x] Update #1090 with the corrected cross-machine design (SSH-exec, not a
      new bridge RPC) so its acceptance-criterion language matches reality.
- [x] Update the relevant vision/architecture doc(s) — check
      `visions/plugins/agent-worktrees` and `visions/plugins/agent-bridge` for
      where cross-machine worktree operations are described, and add/correct
      the "reach another machine's own binstub over SSH, don't build a new
      bridge RPC for local-mutation-shaped problems" principle so it isn't
      re-litigated by a future agent. _(agent-recommended: exact doc
      location to be confirmed against what's actually there before editing.)_

### Phase 2 — Implement cross-machine `accept` via SSH exec
- [x] Design the two-sided transaction: when `claims handoff accept
      <bundle-id>` is invoked and the bundle's source machine differs from
      the local machine, SSH to the source machine's own binstub to read the
      bundle and perform the source-side settle (mark `accepted`, release the
      source's claim) as one local mutation there; perform the consumer-side
      write (add the transferred claim, rewrite the child's `owner_ref`)
      locally, using the *existing* same-machine code path.
      _(agent-recommended shape, to be validated against the existing lease
      module's locking/fencing conventions before implementing.)_
- [x] Reuse the existing lease/fencing primitive (`lease_store.py`,
      `lease_protocol.py`) for the concurrency-safety half (so two competing
      accept/cancel attempts across the SSH round-trip can't double-transfer
      or lose ownership) -- do not invent a second locking mechanism.
- [x] Resolve the SSH target the same way the rest of the suite already does
      (facility SSH aliases / `machines.yaml`), not a hardcoded host.
- [x] Fail atomically and cleanly if the SSH round-trip fails partway (no
      partial cross-machine transfer; same invariant as the same-machine
      path).
- [x] Tests: successful cross-machine accept (faked SSH boundary, mirroring
      how existing tests fake cross-project), a failed SSH-side step leaving
      both sides unchanged, and a fencing conflict on a concurrent accept
      attempt rejected cleanly.
- [x] Update the PR body / docs (CLI help, `references/obligations.md`) to
      describe the real cross-machine mechanism.

### Phase 3 — Land and close out
- [x] Open the implementation PR, drive it through CI + review to merge.
      (#4544, merged.)
- [x] Comment on #1090 that requirement 7 is now actually satisfied; close it
      if every other requirement is also satisfied (re-check against #4527).
      (#1090 closed -- all 7 requirements satisfied across #4527 + #4544.)
- [x] Archive this effort.

## Validation Plan

- [x] Same-machine and cross-project acceptance (already covered by #4527)
      remain green -- this effort must not regress them.
- [x] New cross-machine accept path has direct test coverage (see Phase 2).
- [x] `tools/check-version-bump.py`, `tools/check-changefile-presence.py`,
      `tools/check-version-consistency.py` all pass on the implementation PR.
- [x] Full `agent-worktrees` plugin suite passes (matching #4527's own
      validation bar), modulo the two pre-existing `test_controller_relations.py`
      failures already confirmed unrelated and present on `origin/dev`.

## Proposal

_Pending — this effort itself goes through the review gate (PR + auto-merge)
before Phase 2 execution begins, per the `planning-efforts` skill._

## Journal

### 2026-09-29 — Kickoff
- Effort created after a peer aperture-labs session investigated #1090's
  cross-machine gap, over-concluded a new bridge RPC was required, and the
  operator corrected the direction toward the suite's existing SSH-exec
  pattern. Captures the correction and plans the actual fix.

### 2026-09-28 — Phase 2 implementation
- Implemented true cross-machine `claims handoff accept` in
  `claim_handoffs.py`: cross-machine acceptance now acquires a shared
  `claim-handoff` lease fence, SSHes to the source machine's own project
  binstub for the source-side `accept-source` settle, and finishes the
  consumer-side ledger/lease transfer locally. Same-machine accept now reuses
  the same source/consumer-half factoring, and decline/cancel honor the same
  fence on cross-machine bundles so an in-flight accept cannot race a terminal
  transition.
- Added focused coverage for the new remote leg, failure rollback, fence
  conflict, and the source-side plumbing verb itself in
  `tests/test_claim_handoffs_accept.py`.
- Updated the CLI reference and obligations guidance to document the SSH-exec
  cross-machine path and the shared lease fence.
- Validation to date: targeted bounded plugin tests passed
  (`-k "claim_handoffs or handoff or obligations_settled or lease"`), and
  `tools/check-version-bump.py` / `tools/check-changefile-presence.py` /
  `tools/check-version-consistency.py` passed. The full bounded
  `agent-worktrees` plugin suite later cleared on a subsequent retry after
  transient `test-supervisor` slot saturation.

### 2026-09-29 — Closed out
- #4544 merged: true cross-machine `accept` via SSH-exec, landed.
- #1090 closed: all 7 requirements now satisfied across #4527 + #4544.
- Effort archived.
