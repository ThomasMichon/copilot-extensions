# Resident demand, safe cutover and independent polling single-flight

Canonical contract: [README.md](README.md).

## Qualification and outcome

Preserved #4268 through four whole original/comment texts, 12,795 characters,
after unchanged complete metadata/editor/history and collaborator roster
checks. Two whole messages and one actual merged description yield three
noncompletion relations. A related changed original was freshly qualified
but wholly withheld at transitive protected-reference preflight before model
rendering; no old body, linked source or candidate-only neighbor was used.

## Preserve all four outcomes

The original prevents per-cycle heavy engine-pair accumulation and indefinite
status hangs during many concurrent panes and version transitions. Lock
contention is a hypothesis, not a proven historical hang location.

1. Make thin launchers heartbeat/demand clients of persistent resident Manager
   and Worktrees engines, rather than starting heavy pairs each render tick.
2. Give residents shared mux mapping, status tracking and cache upkeep,
   singleton per machine OR per project; panes share the pair. Do not invent
   a stricter universal all-host condition.
3. Recover from version cutover without stranding owned helpers/locks. The
   incorporated orphan-reaping requirement is safe ownership-based recovery:
   version, age or unavailable age does not establish orphan/idle state or
   authorize terminating healthy work.
4. REGARDLESS of daemon refactoring, keep polling single-flight per project/
   worktree: never start another status/poll subprocess while its predecessor
   remains outstanding; skip OR queue. Preserve bounded truthful timeout and
   cancellation outcomes.

## Credit actual foundations without closing the residual

Resident monitoring, root/catalog reconciliation, list coalescing, resident
publication/held-feed consumption and Manager-owned mux/status routing are
real foundations. Reported merged reaping/launcher-ancestor protection and
resident/held-feed cutover cases are also credited. Do not present everything
as pending or infer a new live leak from the historical incident.

Finite engine-client timeouts, detached stdin and explicit error/timeout
results are useful partial delivery, not universal single-flight proof. The
complete four-outcome residual, safe cutover ownership and historical one-shot
hang site remain unestablished.

The existing consolidated-status-daemon association is planning, not completion.
The original remains OPEN and distinct under its owner correction; the
additive checklist is proposal-only and no canonical receiver has accepted
the whole residual. Its held roster/render slice and other architecture
responsibilities retain their independent owners.

Merged/released source is not installed/live adopter acceptance. Conversely,
unrelated installed-performance gates are not prerequisites for every finite
one-shot repair. Original commands remain historical data, not permission to
run diagnostics, reap processes, restart services or alter locks.

## Completed-work comparisons

| Whole candidate | Contract comparison |
|---|---|
| Commit `435a4eea5fff9f25359e90c20d87e47f0ca51d21` | Truthful monitor restart-spawn failure preserves existing ownership/cutover, not the complete resident-demand/single-flight contract. |
| PR #10257 actual merged description | Owner-specific working-directory tests behind a fake daemon boundary are not live daemon survival or this four-outcome acceptance. |
| Commit `99610ecd74ccdd45aa91e15befd152e595423a42` | Normal detached-spawn/explicit Job-ownership guidance is not implementation or native lifecycle acceptance. |

The working-directory description credits actual owner-function source/installed
launch-mode tests, mutation controls and actor-free observations while keeping
live native lifecycle qualification separate. No process, daemon, lock, source,
native venue or custody changed. See the [dated
journal](<journal/2026/11.04 resident-demand-singleflight-contract.md>).
