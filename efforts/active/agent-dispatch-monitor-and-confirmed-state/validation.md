# Global validation plan

Canonical component of [this same Active effort](README.md). This is not a child effort, transfer or independent completion gate.

## Validation Plan

### Coordination-claim acceptance

- [x] Resolve every #14943
      [Plan and local validation item](plan-assignment-startup.md#coordination-claims-and-explicit-execution)
      at its declared tier. Source tests and a merged intent amendment are not
      evidence of installed behavior.
- [x] In an authorized isolated running setup, record/claim/requeue/recover a
      coordination-only task and verify zero allocation effects, alongside a
      positively opted-in execution job that still starts normally. Preserve
      real task, owner, review and capacity gates.
      The live HTTP/scheduler control retains coordination intent through
      owner-loss recovery and durable reopen while a delegated inert worker
      reaches `STARTED`. This is not real-model or production-cohort qualification.
- [x] Reconcile any affected legacy registration against its real producer and
      current custody/drain conditions, recording the supported disposition
      without activating optional pools or disrupting existing execution.
- [x] Verify the promoted release and authorized installed-provider behavior
      without activating optional pools. Source merge alone does not close this
      bounded acceptance item.

Release content at `b7a5bda2caa91831b2af2e41d37ef636362fa6d9` matches all
20 released implementation files from #14947. Two independently updated
Linux providers serve `0.21.79-dev1` and advertise `task_execution_intent: 1`.
On each, an existing owner atomically claimed a coordination record, received
the explicit coordination refusal from reservation admission, started/yielded
it, observed queued coordination intent with zero reservations, manually
reclaimed it and recorded the passing result as `COMPLETED`. No worker backend
was invoked. The obsolete lane's persistent override survived update; its
process stayed absent while its unrelated sibling remained running.
These results close this bounded intent slice, not the retained parent
lifecycle, real-model or complete-cohort acceptance.

### Retained global acceptance

- [ ] **G13203:** Resolve P13203 and both local V13203 clauses in the
  [captured-state liveness acceptance](plan-core-lifecycle.md#finite-captured-state-liveness-acceptance-from-13203)
  at their original deterministic queue/resolver tier, including unchanged
  generation/owner-session, attempt-cap, stale-stamp and actual-source
  audit controls. Preserve every incumbent Plan/Validation and custody
  gate. This finite slice requires neither live process-killing nor
  completion of unrelated parent work; documentary intake is not a repair,
  native/installed proof or resource release.

- [ ] **G3966:** Resolve every
  [captured-owner/handoff Plan and local Validation clause](plan-core-lifecycle.md#finite-captured-owner-and-handoff-gc-acceptance-from-3966)
  at its retained evidence tier, preserving all incumbent checked credit,
  global gates and actual owner/claim conditions. A planned intake or source
  merge is not native/installed proof, whole-effort completion or resource
  release; unrelated parent work is not a new prerequisite for this finite slice.

- [ ] Complete the [assignment/startup validation matrix](assignment-startup-amendment.md#validation-plan);
      documentation approval alone does not establish runtime conformance.

- [ ] GLOBAL ROOT GATE: resolve EVERY incumbent ROOT Plan AND own Validation item, ALL
  direct acceptance above and original amendment matrix at their retained evidence tiers
  OR explicitly transfer discrete remaining work to named accepted objectives before
  Status Done/archive. Preserve already-delivered checked credit. A phase, receipt,
  source merge or administrative intake is not parent completion. This global rule does
  not make every unrelated parent item a prerequisite for implementing/qualifying a
  finite slice.

- [ ] Complete the [amendment validation matrix](task-conversation-amendment.md#validation-matrix)
      across queue, HTTP, CLI, MCP, evaluator, service restart, and real worker
      boundaries, including the manual `2 + 2` scenario.

- [x] `task_state_machine.py`'s own shape checks (`reachable_states`,
      `states_without_exit`, `terminal_states_with_exit`) pass with
      `CONFIRMED` included -- confirmed via the existing
      `test_task_state_machine.py` suite, unmodified except for the one
      predicted hardcoded-state-list assertion.
- [x] Full `plugins/agent-dispatch` test suite green: **3434 passed, 19
      skipped** (run twice, before and after the module-size mixin
      extraction), the same single pre-existing failure both times
      (confirmed via `git stash` against the unmodified baseline).
- [ ] A hand-run scenario end-to-end: propose → queue → claim → start →
      suspend (no explicit monitor) → (fake-clock-advance) → auto-resume →
      submit → confirm; and the sibling rejection path: submit → reject →
      queue → claim → ... → confirm. Re-check the shipped monitor path
      alongside the new policy; no completed task can originate rejection.
- [ ] A hand-run crash scenario: suspend with a cooldown pending → kill the
      supervisor process → restart it → assert the honest recovery note
      lands, per Phase 4.
- [ ] Cite this effort's Phase 2/3/4 landings back into the parent vision's
      Provenance-adjacent reality docs once code lands (per envisioning's
      "close the loop" step) — no vision *edit* needed unless Phase 2's
      risk-audit surfaces a genuine blind spot the vision failed to state.
