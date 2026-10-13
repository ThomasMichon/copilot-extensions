# Work and maintenance task categories

Category describes a task's purpose, not its execution kind or routing.
`work` is the creation default; producers must deliberately request
`maintenance`. A script, schedule, label, or title alone does not classify a
task. An additive SQLite column gives existing records the `work` default.
No records are pruned, and category does not alter status, claims, reservations,
deduplication, task identity, original timestamps, results or attachment history.

## Creation and reads

```console
agent-dispatch create "Check service health" --repo example/harness --category maintenance
agent-dispatch list --repo example/harness --category work
agent-dispatch list --repo example/harness --category maintenance
agent-dispatch list --repo example/harness --category all --status completed
agent-dispatch find health --repo example/harness --category all
agent-dispatch sweep --repo example/harness --category all
```

| Surface | Omitted category |
|---------|------------------|
| Creation, including schedule templates | `work` |
| CLI `list`, `inbox`, and `agent-dispatch-board` | `work` |
| CLI `find` and `sweep` | `all` (preserves cross-category dedup inspection) |
| Library, HTTP and MCP list/find/sweep | all categories |

All read surfaces accept `work`, `maintenance`, or `all`. The HTTP query is
`GET /tasks?category=maintenance`; it also applies to `q` and `sweep` requests.
Category is filtered by the queue **before** its SQL limit, not by hiding rows
from a bounded mixed-category response. Local and delegated peer boards pass
the same selector. `get`, results, events, progress and session reverse lookups
remain category-independent.

Clients require `/health`'s `task_categories: 1` before explicit category
reads, maintenance creation, or classification.
The check runs for each category-sensitive operation using the client's existing
bounded transport/error flow, including category-bearing schedule registration.
A positive result is not cached for the client lifetime: reconnect or rollback
can replace the coordinator at the same URL with an older generation.
An older coordinator that
silently ignores unknown query parameters is rejected as upgrade-required.
Remote CLI forwarding preserves the flag; an older peer CLI refuses the unknown
flag instead of displaying a misleading unfiltered view. Omitted/work creation
keeps the old request shape, including managed-create hashes; explicit maintenance
is part of that request's replay identity. Reclassification does not rewrite the
accepted request ledger, so an original retry still returns its accepted task.

## Independent Picker contributions

`pivots/agent-dispatch.json` owns **Tasks** and requests `work`.
`pivots/agent-dispatch-maintenance.json` independently owns **Maintenance tasks** and requests
`maintenance`. Both use the same board implementation, columns, phase palette,
streaming, progress and worktree/session enrichment.

Both expose **Inspect evidence**, a read-only `inspect-task` command that reads
the task, statistics, result/log references, events, accumulated progress and
full attachment lineage from its owning machine. Maintenance also exposes
charter, worktree-status, steering and pause/unpause actions. Board recency and
limit controls are presentation only; use explicit all-category historical
queries to reach older records. Loading a new contribution still requires the
normal payload publication and Picker contribution reconciliation; backend
metadata alone does not change an already-installed Tasks contribution.

The Picker's existing activation-aware materializer publishes attributed
schema-v3 pointers for both templates under the same `agent-dispatch` provider.
Each pointer carries only its provider, verified payload root and template name;
discovery rereads the live template after checking enabled state and root identity.
The plugin installers do not copy schema-v1 payload manifests into the shared
runtime registry. An absent Picker remains an optional capability, and disabled,
stale-root or malformed/tampered contributions do not become unowned legacy
entries. Registry reconciliation and diagnostic cleanup stay consumer-owned.
The compatibility implementation extends the canonical Picker materializer's
owned-publication seam with provider-scoped legacy recognition; it does change
that registry implementation, without adding another registry or alias provider.

Existing canonical schema-v1 Tasks registrations are upgraded by that same
materializer only when the complete manifest matches the trusted current
template or an exact recognized prior canonical template digest for its known
provider. Ownership is rechecked immediately before atomic replacement.
Unknown names, foreign providers, customized/tampered manifests and future
schemas are preserved rather than overwritten; they require explicit diagnosis.
This migration restores Tasks alongside Maintenance tasks on upgrade without
publishing raw manifests or changing activation/root-identity checks.

The Worktree Manager's built-in **Maintenance** view continues to own worktree
maintenance and its existing controls. The task contribution's distinct
**Maintenance tasks** caption avoids colliding with or taking over that view.
Both task templates anchor to Worktrees. The consumer discovers the maintenance
filename first, then inserts Tasks at that same existing anchor, producing
`Worktrees → Tasks → Maintenance tasks → Maintenance → Profiles`.
Filenames and logical IDs do not change, so existing owned v3 pointers remain
valid without leaving a stale renamed entry or a duplicate task view.

## Schedule producers

A schedule entry's optional `category` is copied to every occurrence. Omit it
for ordinary work; do not blanket-classify schedules or script workers.
Schedule registration validates an explicitly supplied category before storing
or updating the entry. Only `work` and `maintenance` are valid; null and other
values are rejected without changing an existing schedule. Omission preserves
the compatible ordinary-work default.
Category-bearing schedule adoption first verifies the running coordinator's
category capability before sending a registration POST, including explicit
ordinary-work declarations. This prevents older peers from silently accepting
an unsupported template. No-category registration retains its legacy wire flow.
`agent-index/scripts/maintenance_tick.py --schedule-template --repo <lane>
--machine <host>` prints that producer's explicitly maintenance-category
schedule entry, including its deployed script reference. It does **not**
register the schedule, run maintenance, or classify historical tasks.
Adoption owns placing the printed entry in the appropriate schedule declaration.

## Historical classification

Classification is an explicit control-authorized operation, not a migration or
startup sweep. It requires an exact repo lane, source and origin reference,
corroborated by every supplied label. Unmatched or malformed/ambiguous records
remain unchanged. Worker kind, titles and lack of a session are never evidence.

```console
agent-dispatch classify-maintenance --repo example/harness --source schedule --origin-ref schedule/health --label health
```

Inspect the returned task IDs and `preview_token`. Apply the same selector with
`--apply --preview-token <token>` only after reviewing the preview. The HTTP
equivalent is `POST /task-categories/classify`, with `repo`, `source`,
`origin_ref`, `labels`, optional `limit`, `apply`, `preview_token` and `actor`.
The endpoint uses the coordinator's control credential when configured.
Unavailable/forbidden control errors identify `category_classify` and emit
`task.categories_classify_rejected`, not producer-transition events.

Terminal-task reattach forwards the source category unchanged; only legacy
records with no category field default to work. Original task history and
attachments remain with that record while the new task binds its recovery
session. Remote evidence inspection reports unavailable SSH as a concise
nonzero failure, never a local fallback or an all-clear result.
Selection beyond the default limit of 500 refuses rather than applying a
truncated preview; narrow the provenance or deliberately raise the limit.

Preview uses a deferred read transaction and a consistent WAL snapshot, so a
slow provenance scan does not reserve the queue writer or block create, claim
or heartbeat commits. Apply uses an immediate writer transaction and rechecks
the current selection/token under that serialized authority. A changed selected
ID set rejects the token. Already-classified rows are no-ops; repeating the same
apply neither adds duplicate audit events nor changes lifecycle data. Each
changed row appends a category-classification event with the actor, provenance
and preview token. Original audit records, timestamps and accepted managed-create
request hashes are preserved.
