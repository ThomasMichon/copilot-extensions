# Global validation plan

Canonical component of [this same Active effort](README.md). This is not a child effort, transfer or independent completion gate.

## Validation Plan

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
