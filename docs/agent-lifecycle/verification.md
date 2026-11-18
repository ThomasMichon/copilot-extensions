# How to test a complete run

[Run map](README.md) · [Task and reservation](dispatch.md) · [Session](bridge.md) · [Worktree](worktree.md)

**A test needs an observer outside the worker.** For a line-count job, compare
the submitted answer with the fixture file. Read the stored task and reservation.
Inspect the session and the checkout separately. Do not let the agent's final
message be the test oracle.

### What the existing fixtures establish

| Fixture | What it exercises | What it does not prove |
|---|---|---|
| [Dispatch simulation](../../plugins/agent-dispatch/tests/test_simulation.py) and [transition wiring](../../plugins/agent-dispatch/tests/test_task_transition_wiring.py) | Order synthetic events deterministically; separately change a declared transition and verify the queue obeys it. | A model passing does not prove every live writer uses it. |
| [Concurrent pool placement](../../plugins/agent-dispatch/tests/test_pool_placement.py) and [supervisor startup](../../plugins/agent-dispatch/tests/test_supervisor_pool_allocator.py) | Two allocators contend for one SQLite slot; a supervisor can record a claim before a workspace or native session exists, then require worker acceptance. | The integrated startup test substitutes workspace/native effects. |
| [Native startup prompt](../../plugins/agent-worktrees/tests/native/test_native_startup_prompt.py) | An explicitly selected Copilot executable uses an isolated synthetic provider for turns and resume. | Native protocol behavior is not a cloud-model evaluation or a complete Dispatch job. |
| [Dispatch solo worker evaluation](../../tools/clean-room/scenarios/agent-dispatch-worker-lifecycle-eval/expected.md) | A driven worker claims, starts, reports progress and submits a line count. The [post-check](../../tools/clean-room/scenarios/agent-dispatch-worker-lifecycle-eval/post_check.sh) reads the coordinator and compares the answer to the file. With no evaluator the expected state is `submitted`, **not** worker-confirmed `completed`. | No managed Worktrees checkout or automatic pool placement; the work directory is prepared for the worker. |
| [Coordinator unavailable](../../tools/clean-room/scenarios/agent-dispatch-worker-lifecycle-eval-cpfail/expected.md) | The same worker loses its route to Dispatch. The observer checks that the original task has only its creation event, not merely that the final state is `queued`. | No claim, startup, result or cleanup on the success path. |
| [Restricted Dispatch acceptance](../../tools/clean-room/restricted-dispatch/README.md) | A supervisor, central service, Bridge and separate worker container exercise completion and abandonment. The fixture checks task-scoped access, native identity, one prompt, terminal revocation and cleanup. | It does not claim model behavior or automatic pool placement. It is not proof of full Worktrees finalization. |

These are repeatable fixtures, not a report that a particular release or live
installation passed. The [test portfolio](../../TESTING.md) separates fast
model tests from opt-in real-process and container tests.

### Proposed simulation order

The following are acceptance tests to build or compose, not additional states
or a claim that one existing suite proves all of them.

| Priority | Scenario and injection | Independent pass condition |
|---|---|---|
| 1. Allocate and start | Queue two eligible jobs for one slot. Advance a fake clock through deferral. Replace the worker launcher with a recorder. | At most one allocation uses the slot at a time; the waiting job remains eligible later. A deferral does not invent a running worker. Starting requires the recorded owner. |
| 2. Resume without duplication | Deliver the first request, drop its reply, and restart the supervisor. Test separately with a surviving, exited, and unreachable process. | Match the original operation and conversation. Accept an acknowledged result once; keep unknown delivery distinct from confirmed absence. Do not count a second prompt or process as recovery. |
| 3. Finish without losing files | Submit a valid result, then interrupt each cleanup step. Repeat with an unpublished commit, modified file and outstanding claim. | The original result survives interruption. Owned cleanup resumes; an unsafe checkout remains present with an actionable reason. A terminal task alone never satisfies these checks. |
| 4. Run all three owners | In disposable containers, use a small fixture repository, one worker slot and one known file. First use a deterministic fake agent, then a separately authorized Copilot run with the same task. | Capture the task ID, reservation, workspace and native session before the first turn. Assert one start, one delivery, the exact answer, the configured evaluation outcome, and the final disposition of **each** resource. |

Use the fake version to place failures precisely; use the real version to catch
protocol, process-lifetime and environment differences the fake cannot model.
Give every case its own state directories, IPC endpoints, time budget and
cleanup owner. A skipped case, a missing observer, or a run that needed manual
repair is not an end-to-end pass.
