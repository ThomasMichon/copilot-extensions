# Repository issue loops

`repository-issue-loop` is the declarative composition of existing
agent-dispatch registrar, singleton-supervisor, emitter, supervised-lane,
headless ACP, exclusive-spawn, steering, and recorded-outcome primitives. It
realizes the agent-dispatch vision's recipe, scheduled-production,
pools-as-filters, headless-worker, steering, and recorded-outcome intent; it
does not add a second task or policy store.

## Declaration and occurrence model

The adopter owns the complete active declaration under
`.agent-dispatch/registrar/`. The runtime does not merge an overlay. Registrar
discovery expands it in memory into:

1. `<name>-source`, a lease-gated periodic emitter; and
2. `<name>-workers`, a concurrency-one headless supervised lane.

`cadence_seconds` defines Unix-epoch-anchored occurrences. The emitter's
`tick_interval_seconds` may be shorter so missed work and failures become
visible promptly. The occurrence identity is stable across supervisor restart
and redeploy. A task already recorded for the current occurrence suppresses a
replay; any nonterminal task carrying the loop-wide exclusive key suppresses
later occurrences until terminal resolution. These checks use repository,
exclusive key, and occurrence identity rather than the mutable task `source`;
changing `source` preserves the active worker and occurrence history.
Task state is checked before forge discovery. An active loop task or task
already recorded for the current occurrence returns immediately without any
forge API call.

## Eligibility and reservation

Open issues become eligible only after `quiet_period_seconds` has elapsed since
their last update. All `include_labels` must be present, any `exclude_labels`
rejects the issue, the conventional `bootstrap` label is always excluded, and
existing visible reservations reject it. Remaining
issues sort by the first matching `priority_labels` rank, then `created_at`,
then issue number. `batch_size` bounds the selected set.

The forge boundary is intentionally narrow: list open issues, reserve, promote
to claimed, and release an orphan. The initial `github` adapter uses `gh`.
Discovery uses a bounded GraphQL connection: at most ten 100-issue pages, with
the latest 100 comments and first 100 labels fetched inline per issue. This
keeps call count proportional to bounded pages rather than issue count; missing
cursors, GraphQL errors, or exceeding the 1,000-issue bound are observable
failures rather than an empty result. The declaration names the expected
`forge.producer_login`; every read or mutation verifies that `gh api user`
matches that login and that the authenticated identity resolves the configured
repository. Credentials remain outside the declaration and follow the caller's
ordinary authenticated CLI boundary. Reservation markers are accepted only
from that verified login and only after strict field, issue-number, state,
occurrence, and task-id validation.

Read discovery may reuse a verified repository identity within one provider
instance. Mutations may not: each comment or label mutation re-runs the
configured producer-login and repository checks immediately before invoking
`gh`, so a changed ambient credential cannot inherit an earlier verification.

Forge mutation and coordinator task creation cannot be one transaction. The
source therefore separates visible attribution from authoritative election:

1. adds the configured reservation label and ownership marker comment;
2. atomically reserves each canonical forge/repository/issue key in the
   coordinator;
3. visibly releases any provisional marker that lost that election;
4. creates one deterministic goal-bearing task for the elected bounded set;
5. binds the coordinator reservations to that task and appends claimed markers
   with its id; and
6. on retry, promotes a reservation whose task exists or releases its own stale
   unclaimed reservation whose task does not.

The coordinator's unique resource key makes overlapping declarations safe even
when both observe the issue before either forge comment is visible: exactly one
loop wins before task creation. Each acquisition returns an opaque token;
binding and releasing require the exact key, provenance owner, and token. A
stale same-owner process therefore cannot mutate a replacement reservation
after expiry and reacquisition. An unbound election expires after
`orphan_after_seconds` for crash recovery; a task-bound election persists until
terminal reconciliation.

A task-create transport error is indeterminate rather than proof of failure.
The source re-queries by repository, loop exclusive key, occurrence origin, and
dedup identity. It binds a uniquely committed task and releases only after that
authoritative query confirms absence; an unavailable or ambiguous query leaves
the reservations intact for reconciliation.

Immediately before creation, the source renews every issue reservation using
its exact acquisition token. The task is first created as `proposed`, which is
not runnable. Only after every reservation binds successfully is it approved
into the worker queue. A post-create bind failure abandons that proposed task
with an explicit failed-reservation reason and releases only resources whose
exact tokens are still owned, so a short-TTL takeover cannot leave two runnable
tasks.

Subsequent ticks reconcile any task still in `proposed`. If every required
resource key is present under the expected occurrence owner and bound to that
task with its current token, the source retries approval. If the binding set is
missing or incomplete, it retries terminal abandonment instead. A failed or
lost abandonment response is followed by an authoritative task read;
reservations are retained while the task remains nonterminal and released by
exact token only after terminal state is confirmed.

Another loop's active marker or coordinator reservation is always a blocker.
Reconciliation never silently releases another loop's ownership. Forge labels
are tracked by exact label identity: loser cleanup retains a label only when
another active trusted reservation explicitly uses that same label, and removes
the loser's distinct label otherwise.

When a claimed task becomes terminal, its coordinator resource reservations
are released. An issue that is still open also receives a released marker and
has the claim label removed, so a later occurrence may reconsider it under the
same eligibility and retry policy. A completed task whose issue is already
closed keeps its historical forge marker but no longer holds the coordinator
resource.

## Worker charter

The generated task requires triage before implementation: duplicate,
already-done, vision/scope fit, and feasibility. Accepted issues follow the
repository's contribution flow through required checks, review, merge, and
issue closure. Unclear requests use a durable steering card. A blocked steering
task deliberately occupies the loop until explicit steer, release, or abandon.

Every turn ends terminal, with a steering card, or with a task-id waiter/resume
contract suitable for a cold headless body. Worktree-only nudges are forbidden.
Reusable headless workspaces are not deleted at completion; they must be clean
and synchronized.

The default blast-radius charter also forbids force-push, bypassing checks,
merging branches the worker did not create, selecting excluded/bootstrap
issues, and changing the active declaration. The adopter must explicitly set
`allow_self_config_changes: true` to relax only the last restriction.

## Operations

```bash
agent-dispatch repository-issue-loop setup <declaration>
agent-dispatch repository-issue-loop inspect <declaration>
agent-dispatch repository-issue-loop discover <declaration>
agent-dispatch repository-issue-loop status <declaration>
agent-dispatch repository-issue-loop doctor <declaration>
agent-dispatch repository-issue-loop disable <declaration> --reason <reason>
agent-dispatch repository-issue-loop enable <declaration>
```

`discover` performs forge discovery and deterministic selection without
reserving issues or creating a task. `doctor` is nonzero for an unavailable
forge, a failed or stale emitter, missing registrar pointer, unserved unit,
coordinator failure, kill switch, or blocked steering task.

## Host migration

Move producer authority deliberately; copying the declaration first can run two
eligible emitters.

1. Disable the loop on the old host and confirm no emitter command is in flight.
2. Move the adopter-owned declaration placement or its top-level
   `filters.permit.machine` authority to the new host.
3. Release the old emitter lease with
   `agent-dispatch schedule lease-release repository-issue-loop:<name>
   --holder <old-holder>` or allow the declared lease to expire.
4. Register/discover the declaration on the new host, enable it there, and
   confirm `status` shows the new source and worker lane served.
5. Run `discover`, then inspect visible reservations and the active occurrence
   before allowing a mutating tick.

If a separate producer previously selected these issues, disable that producer
before enabling this loop. Preserve its visible reservations until the new
producer can identify their live tasks or an operator explicitly releases
them; producer authority transitions must never manufacture overlapping claims.
