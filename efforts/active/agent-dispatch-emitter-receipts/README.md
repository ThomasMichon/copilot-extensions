# agent-dispatch: durable receipts for command-emitter created tasks

- **Slug:** `agent-dispatch-emitter-receipts`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-phase PRs off `dev`
- **Created:** 2026-09-30
- **Status:** Draft
- **Vision:** `visions/plugins/agent-dispatch/README.md` — extends
  §Features/*emitters-and-evaluators*: a command-emitter can already author
  tasks via `task_output=json`, but has no durable way to learn the resulting
  dispatch task id(s) on a later tick. This effort adds that seam
  (*emitter-command-receipts*, proposed below) without changing the existing
  emitter/evaluator contract.
- **Umbrella issue:** #4774

## Guiding Intent

A declared `kind: emitter` command authors tasks by printing
`task_output=json`; `run_tick`'s `_author_tasks` already computes the exact
`created` list (dedup_key -> real dispatch task id) when it does. That list
is only ever consumed in-process by `serve()`'s observability callback — it
is never durably delivered back to the *domain command* on a subsequent
tick. A domain producer that needs to link its own local state (a carved
work item, a reservation row) to the dispatch task it just caused to exist
currently has no generic way to learn that id, and would otherwise have to
invent its own per-domain pull-reconciliation (e.g. polling
`agent-dispatch list --dedup-key ...`).

> (Operator, verbatim, from the motivating aperture-labs conversation:) "We
> probably do need a way that an emitter can get 'receipts' for its emitted
> tasks, and then hand those to its backing data source, to 'complete' the
> transaction." / "Emitter should probably get a way to do it."

This is a generic primitive — useful to any command-emitter, not specific to
the motivating consumer below.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| lambda-core | Design + implementation | `copilot-extensions.worktrees/lambda-core-wsl-20260930-210848-3397` |

## Coordination

- **Topology:** independent per-phase PRs off `dev`.
- **Host (owns PRs):** lambda-core.
- **Delegates:** none.
- **Handoff:** n/a (single participant for now).

## Context

Traced the exact mechanics in `plugins/agent-dispatch/src/agent_dispatch/producers/emitter.py`:

- `_author_tasks(client, spec, stdout)` parses the command's `task_output=json`
  stdout into task specs, calls `client.create(title, **fields)` per row, and
  returns the **created task objects** (real ids) as a list.
- `run_tick(...)` calls `_author_tasks` and returns
  `{"held", "lease", "scope", "returncode", "error", "created", "duration_seconds"}`
  — `created` is right there, computed, every tick.
- `serve()`'s `_default_on_tick` is the only consumer today: it logs/emits
  observability events. Nothing persists `created` keyed by the emitter's own
  `id` in a place the *next* invocation of the domain's own `command` can
  read.
- `run_side_load` has the identical shape for the on-demand path.

**Motivating consumer:** aperture-labs' Permanent Record is migrating off a
bespoke push+poll dispatch mechanism (`pr_owner.py`'s direct
`agent-dispatch create` plus a hand-rolled suspend/resume/complete
reconcile loop — the exact mechanism class behind a 2026-09-30 production
regression, a terminal `"submitted"` dispatch status its reconcile loop
didn't handle) onto this layer's generic emitter/evaluator primitives
(`efforts/active/permanent-record-logging-task-driver` Phase 1.5, in
aperture-labs). Its planned `permanent-record-emitter discover` command
needs to learn each created task's id to link it into its own
`writer_ranges` row — without this seam it would have to invent its own
pull-reconciliation, duplicating exactly the kind of bespoke per-domain
polling this layer exists to replace.

## Request

(Captured via the aperture-labs conversation that surfaced this gap; kept
public-safe here — no facility-specific names beyond the generic "domain
producer" shape.) A command-emitter should be able to get durable receipts
for the tasks it caused to be created, and relay those receipts to its own
backing data source to close its own transaction (e.g. link a dispatch task
id into a domain-local reservation row). Today it cannot: the created-task
list exists for exactly one tick and is then gone.

## Plan

### Phase 1 — Durable per-emitter receipts log

- [ ] **Receipts sink.** After `_author_tasks` creates tasks for a tick (or
      side-load), append one JSONL record per created task to a per-emitter
      receipts log, keyed by the emitter's declared `id`:
      `{"ts", "dedup_key", "task_id", "status", "tick_id"}`. Location
      mirrors this layer's existing per-emitter state conventions (next to
      the existing lease/schedule state, not a new top-level store).
      Bounded/rotated like other durable logs in this layer — never
      unbounded growth.
- [ ] **Cursor-based read.** `agent-dispatch emitter receipts <id> [--since
      <cursor>]` returns new receipts since a cursor and the next cursor to
      use — the same incremental-read shape this layer already uses
      elsewhere (e.g. schedule registry ticks), not a full-log dump every
      call.
- [ ] **Direct env exposure (no CLI round trip required).** The declared
      `command`'s own process receives the receipts file path (or an
      already-positioned cursor) via env, so a domain command can read
      receipts from its *own* prior tick directly without shelling back
      out to `agent-dispatch emitter receipts`.
- [ ] **`run_side_load` parity.** The on-demand side-load path writes to the
      same receipts sink as `run_tick`, so a domain relying on side-load
      gets the same guarantee.

### Phase 2 — Vision + docs

- [ ] Add *emitter-command-receipts* to
      `visions/plugins/agent-dispatch/README.md` §Features, cross-linked
      from *emitters-and-evaluators*.
- [ ] Document the receipts contract (schema, CLI, env var) in
      `plugins/agent-dispatch/README.md` alongside the existing emitter
      section.

## Validation Plan

- [ ] A declared command-emitter that emits two tasks (one new, one
      dedup-colliding with an existing task) gets exactly two receipts, each
      correctly keyed by its `dedup_key`, with the colliding one carrying
      the **existing** task's id (not a phantom new one).
- [ ] A domain command invoked on tick N+1 can read tick N's receipts via
      the env-exposed path with no CLI call.
- [ ] `agent-dispatch emitter receipts <id> --since <cursor>` returns only
      new receipts and a cursor that is stable across repeated calls with
      no new activity.
- [ ] `run_side_load`'s on-demand path writes to the identical sink/schema.
- [ ] The receipts log is bounded (rotated/truncated per this layer's
      existing durable-log convention) under sustained high-frequency
      emission.

## Proposal

_Pending review._

## Journal

### 2026-09-30 — Kickoff
- Carved from a live aperture-labs design conversation (Permanent Record's
  `permanent-record-logging-task-driver` effort, Phase 1.5): the operator
  identified that an emitter needs a way to get receipts for its emitted
  tasks, to hand to its backing data source to complete the transaction.
- Traced the exact gap in `producers/emitter.py`: `run_tick`/`run_side_load`
  already compute the created-task list every invocation; it is just never
  durably delivered back to the domain command on a later tick.
- Opened umbrella issue #4774. Plan is a design proposal only in this
  commit — no implementation yet; submitting for review per the
  propose-before-you-do cross-repo sequencing rule (aperture-labs' Phase
  1.5 depends on this landing first).

## See Also

- Vision: [`visions/plugins/agent-dispatch/README.md`](../../../visions/plugins/agent-dispatch/README.md)
- Motivating consumer (aperture-labs, cross-repo):
  `efforts/active/permanent-record-logging-task-driver` (Phase 1.5)
- Reality: `plugins/agent-dispatch/src/agent_dispatch/producers/emitter.py`
