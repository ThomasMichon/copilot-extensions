# Ordinary Dispatch usability: remaining work and acceptance

Parent: [monitor and confirmed-state effort](README.md).
Lifecycle contract: [assignment/startup amendment](assignment-startup-amendment.md).
Reality: [pool claim/start](../../../plugins/agent-dispatch/docs/pool-claim-start.md).
Coordination: [#9709](https://github.com/ThomasMichon/copilot-extensions/issues/9709).

## Current progress

Atomic pool-owned claim/start, negotiated callers, bounded startup budgets,
configured-source publication, ordinary first-prompt admission and actual
allocator-instance binding have merged source coverage through #10182.
They are foundations, not proof that the ordinary running service is usable.

Ordinary ACP retry/decision integration merged in #10191 and retains the original Bridge/native
binding and routing/charter seed. It accepts the real idle `at_rest` projection,
negotiates receiver-side no-resume enforcement under the turn lock, and keeps
ordinary receipt observation compatible with the previous strict receiver.
Unknown outcomes permit receipt observation, not replay or replacement.
The [managed CLI integration journal](journal/2026/11.03-managed-cli-startup.md)
records the source slice merged in #10214: explicit CLI placement, post-claim
generation refresh and a first prompt that accepts its existing assignment. Contained
tests are not native qualification. A live ordinary baseline reached worker
acceptance, but used legacy claim-after-prompt ordering, so allocator-before-
spawn and whole-cohort adoption remain unproven.
Installed/native acceptance, complete cohort adoption and the remaining
worker-side lifecycle behavior are still unqualified.

The independent resident-lifetime prerequisite merged in #10234. Actual
service-context follow-through then exposed #10267: managed lanes ask a
project-bound host lookup from a neutral runtime directory. The bounded source
repair reuses Dispatch's installed machine binding and configured supervisor
file, with Worktrees fallback only when neither exists. Ambient machine values,
detected hostnames and physical/guest-name equivalence do not admit an instance.
An unreadable or conflicting installed binding refuses. The next acceptance
step remains actual selected-runtime lane admission, then complete configured
cohort and allocator-before-spawn proof; no lifecycle acceptance row is closed.

A separate controlled local check now demonstrates native managed-lane admission
and completed reconciliation cycles while the cohort remains unprojected.
Whole configured intent, allocator ordering and the remaining acceptance matrix
are still open. The isolated whole-workload path first needs the existing
clean-room runner's optional resource/lifetime controls, tracked by
[#10274](https://github.com/ThomasMichon/copilot-extensions/issues/10274);
a host command timeout alone cannot bound its persistent container.
The bounded runner source passes 127 wrapper/provider controls, including
fresh-process physical/legacy cleanup, unrelated-registration no-ops,
unreadable ownership and literal dotted-name filters. Exact-head approval and
green CI preceded the normal merge of
[#10278](https://github.com/ThomasMichon/copilot-extensions/pull/10278)
as `32140ada10f2650d3fb15e2ddb2a82897a433f70`.
A separate
Linux witness confirms kernel resource settings and autonomous expiry; its
short deadline interrupted shared metadata reporting, so it is not a complete
scenario PASS. The actual configured-cohort/allocator/worker path and
whole-workload scenario remain separate gates; the next integration leg must
exercise the real master and worker CLI, not a controller-side manual claim.

A subsequent isolated ordinary case reaches actual complete publication and
allocator-owned `CLAIMED` before any workspace/native. It retains that claim
at the existing unsupported ordinary Bridge no-pair override, before worker
creation or delivery. The next supported-default case must prove affirmative
worker start; no-pair consumer qualification remains open with complete caller
adoption. This partial ordering result does not close the acceptance matrix.

A supported-default fresh case confirms the same pre-spawn claim, then exposes
[#10289](https://github.com/ThomasMichon/copilot-extensions/issues/10289):
ordinary initial startup still asks project-bound host discovery from its
neutral service directory. The bounded correction reuses strict installed
machine admission, with project fallback only when no binding exists.
The coupled ordinary-startup/lane packet passes 118 contained controls,
including actual binding-file and unreadable/mismatched negative cases.
[#10290](https://github.com/ThomasMichon/copilot-extensions/pull/10290)
merged as `fe099a79dc4a5ea1030db2f93df732890904b1b9` after the current-head
review and successful CI. A distinct whole-stack case now passes
machine admission and creates its real workspace/native after the claim.
Its synthetic project omitted committed receiving-side authority configuration,
so Bridge correctly refuses binding before a prompt. Correct that fixture
through normal committed project setup, preserving the original native and
failed attempt; affirmative worker start and complete lifecycle remain open.

After committing the normal receiving-side configuration in a distinct fresh
fixture, the five-stage ordinary path passes. The real master publishes its
complete isolated pool, the allocator claims before workspace/native creation,
and the synthetic ACP worker independently accepts through the actual worker
CLI in its created worktree under the same retained native identity. The
controller only creates and reads; no model, ID review, decline, recovery,
submission or complete-workload acceptance is credited. This qualifies the
supported-default ordinary Bridge thin path in isolation, not managed CLI,
no-pair consumers, production cohort activation or the full usability matrix.
The next source/integration work is worker decline and owned-worktree closeout,
then the remaining recovery and submission contracts.
The [decline preparation source](journal/2026/11.03-worker-decline-preparation.md)
now retains a current worker's scoped decision and failed-closeout blocker,
with 648 contained coupled controls. It does not finalize a workspace, apply
exclusions or requeue, and ordinary prompts are not migrated to it. Complete
decline closeout remains unqualified.

The following is an **agent-recommended execution/validation sequence** under
the existing reviewed five-outcome contract. It does not create an operational
grant, a second effort or a requirement to wait for a marketplace release
before an otherwise authorized source-backed test.

## Ordered remaining work

1. **Close the known ordinary startup defects and prove a thin live path.**
   Preserve CLI compatibility, exact task/generation/revision and distinct
   Bridge/native/workspace identity. Prove the actual idle projection,
   receiver-side no-resume under its turn lock and self-contained routed
   retries. Then exercise real registration -> pool allocation -> first prompt
   -> affirmative start on an authorized dev runtime, before treating further
   source-only accumulation as operational progress.
2. **Finish worker-side lifecycle behavior.** Structured decline must retain
   objective exclusions and owned-worktree closeout. Suspended recovery must
   affirm `STARTED` or fence exhausted recovery back to `QUEUED` with the selected
   attempts/backoff. Worktree finalization must precede `SUBMITTED` and evaluator
   execution. Failures remain visible without surrendering unknown capacity.
3. **Complete ordinary/mixed-driver integration.** Reconcile remaining direct
   legacy definitions, qualify managed CLI first-prompt ordering on a real
   native, preserve restricted and legacy compatibility, and enable only the intended complete cohort through
   existing source/registration owners. No independent registry or bypass worker
   launch may substitute for the ordinary path.
4. **Exercise a complete real workload in isolation.** Reuse the existing
   whole-repository lab and actual coordinator, supervisor, Bridge, Worktrees,
   producer, worker and evaluator. A synthetic reviewer/forge workload must
   complete a candidate and continue its original task/native for a changed
   candidate. Fake successful API replies alone are not integration evidence.
5. **Qualify authorized local use and persist the fix.** Run real tasks on the
   actual selected runtime, including failure/restart cases below. Source-backed
   dev overrides can shorten the fix/test loop when authorized; record their
   owner, selected source and managed return path. Continue normal source
   review/CI/merge and eventual release adoption independently. Installation,
   source merge and actual running acceptance are different results.

## Usable-again acceptance matrix

| Boundary | Required evidence |
|---|---|
| Ordinary startup | Correct effective pool/capacity; allocator `CLAIMED` before native creation; actual first prompt and affirmative worker `STARTED` |
| Allocation integrity | Concurrent placement, full pool, retained assignment affinity and no duplicate replacement capacity |
| Startup/decision | Three completed failures/refusals or deadline exhaustion; five-minute same-native prods; thirty-minute decision clock survives restart |
| Unknown/race | Lost admission/send reply, stopped/changed native and provider rollback never cause blind replay, forbidden resume or wrong-queue acceptance |
| Decline | Objective filters and note; worker-owned workspace closeout before relinquishment |
| Suspended recovery | Affirmative current-native `STARTED` or fenced `QUEUED` exhaustion; preserved task-wide cooldown and late-result fencing |
| Submission | Actual owner finalization before `SUBMITTED`; failed closeout prevents evaluation/false completion |
| Workload continuity | Independent candidate result and changed-candidate continuation in the same task/native, without duplicate output |
| Operational control | Actual selected runtime, pause/hold/drain and intended cohort activation; original/foreign effects remain accountable |

Keep source, whole-stack simulation and actual local evidence distinct. Capture
the first failed boundary, repair it through its existing owner and rerun that
case; do not require speculative prerequisites or infer recovery from elapsed
time. Local usable service is an intermediate milestone. The parent stays Active
until all its Plan/Validation items are resolved or explicitly transferred.
