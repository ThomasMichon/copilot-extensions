---
name: auditing-worktree-conclusions
description: >-
  Prepare or dispatch a bounded, report-only audit of explicit local worktree
  conclusions. Use for "audit worktree conclusions", "report-only tidier",
  "worktree cleanup audit", or "pilot the worktree tidier". Never cleans up,
  finishes candidate work, or enables recurring execution.
---

# Audit worktree conclusions

Use the exact agent-dispatch and agent-worktrees session command catalogs;
never choose a global wrapper or source-checkout runtime.

## Approval before execution

An audit is not a cleanup authorization. Resolve one local project and select
1-5 exact candidate IDs with the operator. Exclude anchors, the worker's own
workspace, protected/system venues, and live or unknown owners. Oldest landed
idle worktrees are the initial preference, not proof of completed objectives.
Select an explicit 1-30 minute budget and an absolute private report directory
in the operator's writable state workspace. Never put personal reports in a
shared/stateless harness or a knowledge anchor. Resolve the correct private
workspace before creating report artifacts. Do not guess omitted parameters.

The first version produces reports with proposed steering questions, not
posted candidate cards. Candidate bookkeeping, source changes, handoffs,
finalization, removal, apply-mode and daily/default scheduling are excluded.
Do not allocate a pilot worker until the scope, roster, budget and destination
are explicitly approved. Check coordinator health, the current inbox, and
existing work/dedup before dispatch.

## Prepare, inspect, then dispatch

Use `recipes render worktree-audit` or `recipes kick ... --dry-run` first.
Pass these four explicit `--param KEY=VALUE` arguments:

| Parameter | Value |
|-----------|-------|
| `scope` | Exact registered local project name |
| `candidates` | Exact candidate IDs, comma-separated (or a JSON array) |
| `report_dir` | Approved absolute private report directory |
| `budget_minutes` | Approved integer, 1-30 |

The comma-list avoids quote-heavy JSON arguments through Windows `.cmd`
catalog shims; quote each complete parameter argument in PowerShell. Preserve
the returned canonical sorted roster and parameters for report validation.
The dispatch repo lane remains the producer's lane; `scope` names the audited
project, not a substitute queue lane.

Only after approval, `recipes kick worktree-audit` with the same parameters
creates the ordinary goal task. Without `--spawn` it merely enqueues; route it
to an explicitly configured headless lane or use the approved normal dispatch
spawn path. Do not add a registrar, emitter, timer, or daemon for a one-off
pilot. The recipe supplies private report-only policy, live-work dedup,
project-scoped spawn exclusivity within the coordinator, and mandatory
independent verification. Labels alone are not an OS permission sandbox.

## Review the report

The worker reads `charter show worktree-audit` and `operating-procedures`.
It accounts for every candidate, including budget-exhausted/unreadable ones.
It reports evidence for the objective, transcript, liveness, Git, claims,
pairing, handoffs, effort and forge state, plus remaining work and proposed
steering. An issue does not accept responsibility; a FINAL stamp does not
authorize cached removal.

Run `recipes check-report --report <private-json-path>` with the same four
parameters. This validates schema, roster and bounds only. Independently
corroborate evidence and read-only behavior before confirming the submitted
task. Missing/truncated evidence must remain a hold, not a claimed conclusion.
Budget exhaustion with explicit unreviewed receipts is a valid report outcome.
Do not confirm merely because this checker or the worker's prose says success.

Record pilot findings in the rightful private report/tracking home. Any
graduation to posted candidate steering, bookkeeping, safe cleanup, or
recurring execution requires separate approval and the corresponding safety
tests. No steering answer upgrades this recipe's report-only authority.
