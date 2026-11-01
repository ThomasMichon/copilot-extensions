# Standalone managed agenda commands

Companion to [driving-effort-agendas](../SKILL.md). The payload script
`skills/driving-effort-agendas/scripts/agenda_cli.py` uses Python's standard
library only. Resolve it from the enabled efforts payload, then invoke:

```text
python <efforts-payload>/skills/driving-effort-agendas/scripts/agenda_cli.py read --state-home <explicit-lane-home>
```

This is an owned payload invocation, **not a new global binstub**. CLI, App and
ACP sessions can use it only when their tool host can execute a process and
has Python available. No sibling plugin, installation, Manager, daemon or
agent-worktrees discovery is needed. An agenda is explicitly adopted; never
initialize or import another active agent's home automatically.

## Reading and initialization

The explicit state home must already be an adopter-owned directory. Symlink
roots, ancestors, board paths, database files and SQLite sidecars are refused,
as are traversal, noncanonical board paths and shared/hardlinked state files.
The home must be on a filesystem with SQLite locking and durability support.
This is cooperative local storage, not protection against a hostile owner
replacing directories concurrently; directory identity is checked across open.

`read` returns validated documents, generated metadata, a current Snapshot and
latest delivery status. `next` and `check` return the Snapshot alone.
`--expected-revision <n>` and `--worktree <identity>` are optional read guards.
Reads use a real SQLite transaction, not the imported JSON files.
`history --limit <1..100> --before-revision <n>` pages immutable accepted results
newest first. Accepted records and their complete state snapshots are never
silently pruned. History is not a canonical effort completion ledger.

`init` accepts either inline `change.documents` with `cursor` and `boards`
(a relative board-name-to-document mapping), or `--import-home <manual-home>`
with `change: {"operation":"init"}`. Imports read the original cursor and all
referenced active, queued, deferred and archived board files without rewriting
board files. **Same-home adoption deliberately replaces `cursor.json` with a
version-3 refusal marker**, after durably preserving its exact original bytes.
Already-installed pre-managed inspectors reject that unsupported version.
Importing a separate home is a read-only template copy, not migration of that
source lane; use the original lane as `--state-home` for actual adoption.
Additional unused
boards can be supplied through inline documents; no directory crawl is performed.
Importing another managed home's legacy JSON is refused because those files can
be stale; the managed database remains its authority.

Use this request shape for every mutation, from `--request <file>` or stdin:

```json
{
  "schema": "efforts.agenda-request",
  "version": 1,
  "expected_revision": 0,
  "operation_id": "stable-init-id",
  "change": {"operation": "init"}
}
```

```text
python <resolved-agenda-cli.py> init --state-home <lane-home> --import-home <manual-home> --request <request.json>
```

Init requires expected revision **0**, but its generated revision is the
imported cursor revision plus one, preserving older deferred `saved_revision`
relationships. Subsequent updates require the current store revision and a
new stable operation ID. Board/cursor versions 1/2 retain their existing format;
only the request and store are newly versioned contracts.
The marker's version 3 is deliberately unsupported execution state, not a third
cursor format; the validated cursor inside SQLite remains version 1/2.

Unknown legacy metadata, composite `source.ref` strings, original evidence,
visits, returns and tighter budgets are retained. Missing frame entry times and
occurrence IDs become null, not invented historical values. New node entries
get generated UTC times and UUID occurrences. The store gets one stable UUID
encoded as exactly 32 lowercase hexadecimal characters (`uuid4().hex`).
`--actor` and `--session` are optional caller assertions, **not authentication,
head identity, grants or proof of authority**. Missing identity remains unknown;
no environment variable or source-reference text authenticates a caller.
Actor/session labels and stable operation IDs are each bounded to 1 KiB.

## Semantic updates

Invoke `update` with the same strict envelope, replacing `change` with one
operation below. Unknown envelope/operation fields, unsupported versions,
duplicate JSON keys, nonfinite numbers (including overflow) and invalid state
are explicit errors. Requests and each board/cursor are bounded to 128 KiB;
the combined managed state is bounded to 1 MiB. Limits fail visibly rather than
discarding history or archived evidence to make room.

Evidence is a nonempty list of objects with exactly `criterion`, `ref`, `inputs`
and `kind` (`measured` or `judgment`), all nonempty text. Outcome/acceptance
evidence must cover every current exit criterion; this validates attribution
and shape, **not the truth of a test or an external approval**.

| Operation | Required fields besides `operation` | Behavior |
|-----------|--------------------------------------|----------|
| `refine` | `agenda`, `board`, `evidence` | Refine a board in place; retain source/root entry, all original nodes, metadata, exits, edges and no looser visit limits. Reconcile live/deferred frame definition revisions without replacing their state. |
| `queue` | `agenda`, `board`, `assignment_ref`, `evidence` | Add a new already-assigned board and queue it. Existing names require refinement, not replacement. |
| `select` | `agenda`, `assignment_ref` | Select only the first queued assigned root with an empty active stack. |
| `outcome` | `outcome`, `evidence` | Follow a declared edge; append evidence and increment every destination visit, including failed/no-progress loops. |
| `link` | `executions`, `evidence` | Append execution references, each exactly `{kind, ref}`. Does not launch or claim them. |
| `block` / `wait` | `reason`, `next_action`, `evidence` | Retain the full active stack; set steering/waiting and a concrete bounded next action. |
| `push` | `agenda`, `assignment_ref`, `reason`, `return_node`, `return_action`, `evidence` | Push an already-queued necessary prerequisite/assigned interruption; save the immediate parent return, increment detours and guard depth. |
| `return` | `acceptance_ref`, `evidence` | Require a terminal child and its canonical acceptance; preserve child result, pop once, enter the saved parent return node/action and count that entry. |
| `complete-root` | `acceptance_ref`, `evidence` | Require a terminal root with no child; archive full root state. Queued work remains working, deferred work remains steering, otherwise only the agenda cursor becomes complete. Canonical acceptance and provider obligations remain independently verified. |
| `defer` | `id`, `reason`, `decision_ref`, `decision_packet_ref`, `revisit_at`, `recheck_action`, `next_action`, `evidence` | Save the entire chain, original returns, evidence and budgets. Generate actual attempt time; require a later revisit. Empty active stack enters steering, not completion. |
| `response` | `id`, `response_ref` | Preserve an actual response reference; make an unchecked response eligible for reinspection, never approval. |
| `recheck` | `id`, `revisit_at`, `evidence` | Require due time or an unchecked response; retain check/evidence, generate actual check time and require a later next revisit. |
| `restore` | `id`, `acceptance_ref`, `evidence` | Require an empty active stack and an eligible recheck accepted by this store (imported check text does not count). Deliberately restore the original chain, retain full branch history and tighter limits, and increment detours without resetting visits. |
| `progress` | `reason`, `evidence` | With the original root active and no child, record explicitly demonstrated parent progress and its previous detour count, then reset only the no-progress detour counter. Outcomes/returns never implicitly reset it. |
| `limits` | `limits`, `evidence` | Tighten one or more positive integer `max_depth`, `max_detours_without_progress`, `max_deferred` limits; never loosen them or discard work to fit. |
| `park` | `ref`, `reason` | Append a nonblocking tracked discovery; neither assign it nor interrupt active work. |

Every update validates the entire referenced board set, full active/deferred
chains, root/return relationships, visits, depth, detours and deferred limits
before committing. Reaching a limit requires steering/replanning, not replay
or automatic completion. A deliberately recorded `progress` decision never
resets historical visits or wider approval requirements.

Example movement after reading the current revision:

```json
{
  "schema": "efforts.agenda-request",
  "version": 1,
  "expected_revision": 1,
  "operation_id": "implementation-ready",
  "change": {
    "operation": "outcome",
    "outcome": "ready",
    "evidence": [{
      "criterion": "The intended change and focused regression tests are ready.",
      "ref": "focused regression result",
      "inputs": "the tested source revision",
      "kind": "measured"
    }]
  }
}
```

## Transaction and retry contract

The SQLite `agenda.sqlite3` store uses explicit transactions, `BEGIN IMMEDIATE`
for mutations, a five-second busy deadline, rollback journal `DELETE` and
`synchronous=FULL`. SQLite's VFS owns journal/data/commit fsync ordering; the
new store directory entry is additionally fsynced on POSIX. This promises the
SQLite durability boundary on a supporting filesystem, not hardware immunity
to lying storage or unsupported network locking.

One commit contains complete validated board/cursor state, generated
revision/actor/time/event metadata, the **original accepted operation result**,
its state history, exact original cursor recovery bytes and pending/latest
observation. No independent cached execution cursor is written. The unsupported
legacy refusal marker is installed before SQLite acceptance, not a JSON copy of
the managed cursor.

**Two resources cannot share one stdlib atomic commit.** Legacy adoption first
fsyncs `agenda.init-recovery.json` (original bytes, initial documents and saved
request), then atomically replaces `cursor.json` with the refusal marker and
fsyncs the directory, then commits SQLite's full state/metadata/history.
Before-commit failure leaves no accepted partial mutation, but can leave a
fenced, unavailable lane. Recovery never silently restores old successful work.
The recovery file is retained as owned evidence, not an independently active
cursor or accepted operation record.

This legacy-migration path requires POSIX directory fsync. An unqualified
platform refuses adoption of an existing legacy cursor before acceptance or
replacement; it does not pretend that an old installed reader became safe.
Fresh homes without legacy cursor files need no refusal replacement. No new
native/platform or rollout qualification is claimed.

Only adopt a **quiescent, explicitly owned lane**: replacement cannot revoke
bytes an old reader already opened before fencing. There is no automatic
active-agent migration, writer kill or service restart.

After uncertain commit/transport failure, retry the **same** operation ID,
expected revision, semantic request and explicit caller metadata. The store
returns its exact original accepted result even after newer updates or delivery
attempts, without rereading import inputs or reapplying movement. Reusing an ID
with different request/metadata is an error. A new operation with a stale
revision fails visibly; no automatic merge or overwrite occurs.

CLI output separates immutable `result` (`persisted: true`, generated revision,
metadata and delivery state at acceptance) from the latest attempted `delivery`.
A duplicate returns the original acceptance envelope and does not republish.
`read` shows current delivery, while `publish` explicitly resends the latest
observation **without replaying a mutation**. A postcommit publisher or
delivery-record failure still returns the durable accepted result and an
explicit pending error. Store integrity/read failures remain diagnostics.

Legacy [agenda.py](../scripts/agenda.py) `next`/`check` remain read-only for
unmanaged v1/v2 homes, but **refuse any managed database marker**, including an
empty failed-init database or dangling symlink. Use managed `read`/`check`
instead. Original JSON remains read-only recovery evidence, not a second live
writer, preserved in the recovery file and committed store metadata rather than
at its former active path. Already-installed pre-managed readers ignore the DB
but reject the version-3 marker. Never edit the marker expecting managed state
to change.

## Interrupted-adoption recovery

An interrupted same-home init can leave an empty/uninitialized SQLite database
and a version-3 `cursor.json`. Both old and new legacy readers must refuse it.
Do not remove the database, reset the marker, replay an uncertain external
effect or treat the preparation record as successful acceptance.

Preserve `cursor.json`, `agenda.init-recovery.json`, the original board files
and any interrupted private temporary files. Retry the **exact same init
request**, actor/session assertions and `--import-home` locator. The recovery
record's `request` text contains the saved canonical request envelope, identity
and import-home metadata; extract its nested request for CLI submission, not
the recovery record itself. This is recovery input, not authenticated identity.

The store verifies the saved request and original-byte digest, revalidates its
complete initial documents and finishes the same one accepted SQLite
transaction. It never reads the unsupported marker as an execution cursor.
A different request, missing/corrupt/linked recovery file or intervening legacy
cursor change refuses recovery and preserves evidence for explicit owner
reconciliation. After acceptance, original bytes also remain at
`read()["metadata"]["legacy_fence"]["original_cursor"]` as base64.

The recovery record is bounded to 2 MiB and committed metadata to 256 KiB;
original cursor bytes still obey the 128 KiB input bound. Digests detect data
corruption/retry mismatch, not permissions or caller authentication. Never
restore the original cursor to its active path after acceptance: it would be
stale. Duplicate accepted operation IDs still return their original result.

## Optional publication

Only an explicitly configured trusted, owned finite argv may be invoked.
For init/update or explicit publish, repeat `--publish-arg` for each token
(use `--publish-arg=--flag` for a token starting with a hyphen). No shell,
agenda-derived command, secret environment modification, daemon or file polling
is involved. The finite adapter owns any descendant lifecycle; this core
bounds its immediate process and reply, not a provider service lifecycle.

After commit, stdin carries one compact JSON observation followed by newline:

```text
{"schema":"efforts.agenda-observation","version":1,"source":{"worktree":<cursor worktree>,"session":<explicit committing caller session or null>,"store_id":<stable 32-lowercase-hex UUID>},"revision":<accepted revision>,"committed_at":<generated UTC ISO>,"snapshot":<validated Snapshot>}
```

The Snapshot includes root, stack, next action, status, exit criteria, declared
outcomes, queued/parked counts, deferred branches, due rechecks and next revisit.
Total input is bounded to **32 KiB**; oversized observations remain durably
pending without invoking the adapter. The delivery row retains an explicit
undeliverable marker, not a truncated observation; full accepted documents stay
in state/history. A later smaller commit is needed before such an observation
can be delivered. JSON replies on stdout are bounded to **35 KiB** (including
the newline); stderr remains bounded to 32 KiB.
The immediate process has a ten-second delivery deadline. Reply must be JSON
`{"ok":true,...}` or a structured `error` with nonempty `code`/`message`.

Delivery is `not-configured`, `delivered` (successful adapter acknowledgement),
or `pending` with an explicit error. Offline, malformed, timed-out, oversized
and unsupported responses do not falsely report a live UI. Pending/latest
delivery is persisted separately from immutable accepted operation history;
a late reply cannot overwrite a newer revision's delivery record.

`publish` reuses the source/session recorded at commit; it cannot replace that
identity with a retrying caller's claim. Provider source/head/runtime generation
fencing and actual subscribed presentation belong to the optional adapter,
not this standalone store. Adapter acknowledgement alone is not proof of
current head, permission, effects, canonical completion or Manager visibility.

## See also

- [Driving effort agendas](../SKILL.md)
- [Legacy board/cursor convention](agenda-format.md)
- [Deferred branch procedure](deferred-branches.md)
