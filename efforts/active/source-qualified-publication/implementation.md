# Implementation contract and ownership

Back to [the effort](README.md). **All new interfaces in this document are
agent-recommended proposals, not installed APIs.**

## Bounded source evidence

Paths below are repository-relative. `M` abbreviates
`worktree-manager/src/worktree_manager/`; `A` abbreviates
`plugins/agent-worktrees/src/agent_worktrees/`.

| Existing hotspot | Concrete behavior and consequence |
|---|---|
| `M production_picker/picker_tui/engine_maintenance_actions.py:146-220` | `_start_refresh` calls local `refresh_one` for any machine-SSH row, mutates `self.data` and `debug` on its worker thread, and `_replace_row` matches bare ID. A failed local lookup falls back to source reload. |
| `M production_picker/picker_tui/data_local.py:344-366` | `refresh_one` always invokes the local engine; supplied machine/environment only label the normalized row. |
| `M engine_client.py:722-818`, `worktree_rows_contract.py:54-125` | Current reads retain validated publication metadata as `_projection`; build on this rather than synthesizing client revisions. |
| `M production_picker/picker_tui/derive.py:641-642,726` | Existing canonical key is source identity plus the separator and raw worktree ID. Preserve it. |
| `M production_picker/picker_tui/data_ssh.py:1240-1307,1369-1391` | `LiveLoader` owns source records and a local reload epoch `_gen`. Reload retains records while invalidating older asynchronous attempts; `_gen` is not the producer generation. |
| `M production_picker/picker_tui/loader_current_feed.py:75-107,123-184` | Feed commits replace the loader's source records. Normalization reuse excludes only `_projection`, so provenance timestamp churn still defeats reuse. A new `CurrentFeed` is constructed for each connection. |
| `M worktree_feed_contract.py:15-169` | Feed receiver validates generation/cursor and complete begin/done or update/commit barriers, including full order. It cannot by itself order a separate point response against reconnects. |
| `M production_picker/picker_tui/engine_runtime.py:337-360`, `engine_loading.py:467-509` | Every live tick reapplies `loader.records()`, overwriting a point-only screen replacement. The latter is reserved to #7938. |
| `A list_cli.py:466-477`, `worktrees_client.py:172-203`, `worktrees_runtime.py:23-56` | Refresh-shaped JSON reads reach resident selection; `refresh` is validated locally but not forwarded. Runtime accepts only read selectors, not observation intent. |
| `A reclaim_cli.py:466-502`, `tracking.py:3462-3498` | Reclaim reports process success after best-effort liveness stamps; stamp contention silently skips. Neither provides durable publication acknowledgement. |
| `M production_picker/picker_tui/maintenance.py:72-76,134-148`, `engine_input.py:338-379` | Success maps to command `ok`; post-maintenance reload is delayed until progress dismissal. Reconnection is not read-your-write proof. |
| `A worktrees_current_rows.py:131-148` | Materialization stamps per-domain `observed_at` on every invocation. |
| `A worktrees_feed.py:18-55`, `worktrees_projection.py:265-329` | Whole-row equality emits timestamp-only updates; persistence deletes/reinserts all rows while only the selection index can be reused. |
| `A worktrees_producer.py:23-56`, `status_monitor_cli.py:829-849`, `__main__.py:3269-3277` | Existing owner publication is a project-wide producer callback, not an exact-ID observation primitive. Do not relabel this full gather as a bounded targeted operation. |

The implementation must retain #7933's elected-owner rollback recovery in
`worktrees_runtime.py` and the failure-to-stale path tested by
`test_worktrees_producer.py`. #3761's durable-before-ack authority remains the
direction; no caller writes the projection database. Do not replace #6748
publisher recovery, #2619 event detection, or #6086 workload measurement.

## Identity and ordering

Define consumer `RowKey = (source_id, project, worktree_id)`. `source_id` is
resolved from the selected `Source`, never inferred from display labels or
rewritten from a point reply. Provider-exec sources retain their current
capability boundary; unsupported point observation is explicit, not a local
fallback. The source adapter validates the returned project and requested ID.

Keep these dimensions distinct:

1. `source_epoch`: client attempt/reload cancellation token (existing `_gen`).
2. `generation`: opaque elected-producer generation, equality only, never
   lexicographic or timestamp ordering.
3. `revision`: committed publication cursor, increasing within a generation.
4. `semantic_revision`: per-row last semantic change within that generation.
5. `observation_revision`: last actual observation of a fact group, separate
   from the transport's heartbeat.

A point response for row A at revision 11 raises A's accepted floor to 11;
it **does not advance the full-feed cursor from 10** or imply other rows were
observed. Feed revision 10 may remain connected while subsequent ticks use A11.
A valid delta based on feed cursor 10 remains admissible in the feed receiver;
the unified source store compares resulting row/deletion evidence against A's
floor. A later authoritative A12, including a newly acquired owner, supersedes
A11. Equal revision with conflicting content is a protocol error, not last
arrival wins. Deleted rows have revision-qualified tombstones; missing from
an incomplete or point response never implies deletion.

Owner generations are admitted only through a complete fresh owner snapshot
on the current source epoch. A different-generation point reply requests that
bounded reset path; it does not elect a new owner by arrival order. Preserve
old rows as explicitly stale until reset commits, then atomically discard old
row floors/tombstones and accept revision 1 of the new generation. Delayed old
connection events fail their epoch fence. Within the same generation a
reconnected snapshot older than a point floor cannot regress that row.
Generation replacement invalidates receipts, which are reconciled by new
observation only, never by replaying a destructive command.

## F1: semantic storage and observation protocol

Retain existing v1 readers. Introduce a separately advertised
`worktrees-observations-v2` capability and an explicit v2 request contract.
An old strict `_FIELDS` parser must never receive new fields. Server and
consumer support land before any caller elects v2. A v1 client continues to
receive its existing truthful full representation; it may retain legacy
timestamp deltas. No silent reinterpretation of v1 timestamp semantics.
The zero-semantic-delta performance contract applies to the negotiated v2
path; document and test the compatibility cost rather than hiding it.

Proposed v2 snapshot envelope:

```json
{
  "ok": true,
  "contract_version": 2,
  "project": "sample",
  "projection": {
    "schema_version": 1,
    "generation": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "revision": 11,
    "roster_count": 1000,
    "selected_count": 1,
    "quality": "current"
  },
  "worktrees": [
    {"id": "tree", "title": "Example", "semantic_revision": 7}
  ],
  "observations": [
    {
      "id": "tree",
      "facts": {
        "session": {
          "quality": "current",
          "observation_revision": 11,
          "observed_at": 1000.0
        }
      }
    }
  ]
}
```

All existing bounded row facts remain available; this abbreviated example is
not a new minimal-row allowlist. Bound observation frames by the existing
collection and byte caps, with one entry per identity/fact group. Validate
fact-group names, numbers, source/project scope and duplicate identities.

Semantic comparison covers every rendered/domain fact, provenance **quality**,
owner identity and capability-relevant value. Only explicitly declared
observation timestamps/revisions move outside the semantic payload; never
strip arbitrary `*_at` fields (e.g. activity or lifecycle times are meaningful).
Retain stable semantic bytes/revisions for equal facts. Per-fact observations
advance only for groups actually sampled, not cached groups carried forward.
An unavailable observation changes its quality and cannot renew the previous
successful observation time.

Durable storage keeps semantic rows separate from observations and ordering.
Under the existing writer lock and elected writer fence, compute changed,
inserted and deleted identities; upsert only changed semantic rows, delete
only removed identities, and persist observation/order/head changes in the
**same SQLite transaction**. Do not publish the in-memory head or notify
feed waiters until commit succeeds. Rollback leaves both durable and live
publication unchanged. Changed selection columns update the index; unrelated
title edits do not rebuild selection indexes. Persist exact roster order,
including advisory restart reads; do not rely on SQLite incidental row order.

A real observation-only publication may advance the publication revision with
zero semantic writes. V2 feed carries bounded observation frames inside the
same update/commit barrier, alongside semantic worktree/delete frames.
Preserve current full order frames initially; suppressing order frames would
break `CurrentFeed` and is not required for zero semantic deltas.
Heartbeat remains generation/cursor liveness only and never advances
observation age or fact versions. Last-contact freshness is distinct from
last-fact-observation freshness. Reconnect returns a complete snapshot with
current semantic rows and observation metadata.

Mixed versions: v1 reconstructs its provenance timestamps from the same
observation store. V2 parsers stage semantic and observation changes together.
Unknown v2 frames fail explicitly, not silently. Keep v1 projection advisory
reads and older supported elected writers usable: F1 retains the schema-1
row/head/index tables and adds generation/revision-qualified observation tables.
An older writer's complete publication wins over stale extension metadata.
Wire version 2 is not a schema migration or an elected-writer incarnation.
An unavoidable incompatible migration requires separate admission.

F1's real-producer test also identified `closure.computed_at` as derived
observation metadata. It moves to the bounded optional
`observations[].derivations.closure` object with an observation revision;
closure facts stay semantic. Activity and lifecycle timestamps remain semantic.
The exact implemented F1 shape is documented in
`plugins/agent-worktrees/docs/engine-picker-contract.md`.

## F2: targeted owner observation and publication receipts

Advertise a separate `worktrees-observe-v1` capability on the existing
authenticated owner endpoint. Proposed action `worktrees-observe`, not extra
fields smuggled into `worktrees-read`:

```json
{
  "contract_version": 1,
  "project": "sample",
  "request_id": "opaque-request",
  "worktree_ids": ["tree"],
  "reason": "refresh",
  "expected_generation": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "wait_ms": 250
}
```

Reasons are a finite enum (`refresh`, `post-reclaim` initially). Proposed
limits: 64 IDs, 2,000 ms maximum server wait, existing frame/identifier bounds;
these are containment defaults, not measured latency SLOs. Reject oversized,
ambiguous and cross-project selectors. The client already selects the source
endpoint; never accept a client-supplied filesystem path or alternate owner.
Request IDs deduplicate **observation** attempts only, with bounded retention;
they do not establish exactly-once destructive effects.

The request handler validates and enqueues/coalesces identity-scoped intent,
wakes the existing owner, and waits only on a bounded completion condition
outside owner state locks. It does not call the full producer. Add an owner
target queue and drain seam in the existing monitor loop. Exact-ID record,
process/session binding and mux observations reuse bounded producer indexes
or targeted APIs; any missing targeted capability returns explicit
pending/degraded coverage and must not trigger a hidden fleet gather.
Avoid calling `scan_sessions_fast(records)`, `bare_orphan_worktree_ids()` or
`live_bridge_worktrees()` unscoped for each request. Existing background
safety sweeps may independently satisfy queued intent.

Response is one bounded receipt with one outcome per requested ID:

```json
{
  "receipt_version": 1,
  "request_id": "opaque-request",
  "project": "sample",
  "generation": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "items": [
    {
      "worktree_id": "tree",
      "status": "published",
      "revision": 11,
      "semantic_revision": 7,
      "covered_facts": ["record", "session", "mux"],
      "reason": null
    }
  ]
}
```

Status enum: `published`, `pending`, `degraded`, `superseded`, `not-found`.
Only `published` includes a confirmed revision; pending/degraded carry a
reason and, separately, last-known cursor if available, never a fabricated
acceptance revision. `not-found` is confirmed absence only when the owner
observed the exact identity and committed its tombstone. Publication proof
comes after durable commit and includes the observation fence that fulfilled
this request; an unrelated larger project revision is insufficient.

Reclaim keeps existing effect fields (`ok`, targets/reaped and cleanup
outcomes) for compatibility and adds `publication_receipt`. Never redefine
`ok` to imply publication. Queue `post-reclaim` observation after effects,
including partial effects; distinguish effect failure from publication delay.
Do not make best-effort `stamp_*` into a receipt or clear LOCK optimistically.
The owner reobserves current facts; a concurrently acquired new owner wins
and may legitimately leave LOCK set despite successful reclaim.

If the connection is lost after reclaim, report effect outcome unknown.
Retry only read/observation receipt acquisition, never reclaim automatically.
For a committed process outcome plus an unavailable owner, retain successful
effect + degraded publication. Per-item bulk receipts must preserve successes,
failures and pending confirmations independently.

Receipt waiting is an optional bounded adapter operation. Normal
`worktrees-read` remains a read, and `--fresh` without exact IDs must not
acquire a scan side effect. `list --worktree-id ID --refresh` explicitly calls
the negotiated observation action and returns the resulting row plus receipt
or a truthful pending/degraded envelope. Capability absence is visible.

## F3/F4: one Manager store and UI-thread admission

Add `source_publications.py` as the loader-owned store/reducer, not a second
optimistic cache. It contains per-source epoch/owner-generation, the complete
feed cursor, per-row floors, committed order, observation metadata and receipt
state. Expose narrow methods conceptually:

```text
admit_snapshot(source, epoch, envelope)
admit_feed_commit(source, epoch, transaction)
admit_point(source, epoch, envelope, receipt)
note_receipt(source, epoch, receipt)
records(source?) -> committed normalized rows
```

Workers produce immutable validated events; Inbox applies state/presentation
changes on the UI thread. The loader's existing record access becomes a view
of this store, so `_apply_loader_records` sees the same A11 the Refresh path
admitted. Do not repair the race by repeatedly patching `self.data`.
Preserve headless loader testability with an explicit injected event sink;
there must not be a production direct-worker-mutation fallback. Feed parsing
may remain worker-local until commit, but source-store adoption is serialized.

Dispatch Refresh through the resolved source's local or remote adapter **before**
any command executes. Local calls keep the existing tracked runner. Remote
calls use the selected source's attributed SSH/project command and parse the
same envelope; they never probe the local engine for that worktree ID.
Capture epoch and source identity when starting, validate again on admission.
Read-only unsupported provider paths report unsupported without changing rows.

Maintenance item state needs separate `effect_state` and
`publication_state`, with labels such as “reclaimed; confirming status”,
“reclaimed; confirmation unavailable”, and “status confirmed; new owner”.
Progress may close while confirmations continue as bounded store state;
closing it never requires reconnect. Admit the receipt immediately through
Inbox, settle only from its correlated committed observation/fence, and allow
normal feed publication to complete it. A wait deadline yields pending, not
false settled success; no indefinite higher-priority optimistic overlay.

## Staged PR graph and exact ownership

No participant is authorized to implement merely by being listed here.

| PR slice | Owned functional files (all relative to prefixes above) | Dependency / collision |
|---|---|---|
| P0 | Only this effort's three Markdown files | Review and merge before implementation |
| F1 | `A worktrees_projection.py`, `worktrees_projection_index.py`, `worktrees_current_rows.py`, `worktrees_feed.py`; new `worktrees_observation_contract.py`; supporting v1/v2 runtime/client capability/dispatch adapters; `M worktree_rows_contract.py`, `worktree_feed_contract.py` | One contract owner edits shared runtime/client protocol seams first; no producer activation until both parser variants exist |
| F2 | `A worktrees_runtime.py`, `worktrees_client.py`, `worktrees_producer.py`, new `worktrees_observe.py`, `status_monitor_cli.py` narrow queue integration, `reclaim_cli.py`, `list_cli.py`; `tracking.py` only if exact-read result evidence is indispensable | F1 first to avoid shared wire edits. Preserve existing recovery owners; collision check at admission. No broad `__main__.py` rework or caller scans |
| F3 | `M production_picker/picker_tui/source_publications.py` (new), `loader_current_feed.py`, `data_ssh.py`, `data_local.py`, `engine_maintenance_actions.py`; `M engine_client.py` new bounded response adapter | Pure reducer/tests can proceed after P0 using the agreed schema. Production integration after F1/F2; coordinate new adapter file extraction if engine-client changes overlap other work |
| F4 | `M production_picker/picker_tui/maintenance.py`, `engine_input.py`, `engine_live_screens.py`, receipt portions of `engine_maintenance_actions.py` | F2 + F3; same Manager implementer or explicit file transfer |
| F5 | New focused tests and existing tests named in validation; affected public API docs | F1–F4 integrated; separate runtime changefiles for Worktrees and Manager |

F1 producer work and a **pure, new-file-only** F3 reducer/test slice are the
next independent implementation candidates after review/admission. F2 and F1
must not concurrently edit runtime/client/contract files. F4 does not overlap
F3 production edits without an explicit transfer.

**Reserved #7938 overlap:** constructor, compose, runner, `engine_loading.py`
and project configuration remain exclusively with the pending-binding owner.
`engine_loading._apply_loader_records` is a read-only integration contract for
this plan. Any proposed Inbox/store construction hook there must be admitted
by the coordinator after that owner's change lands, never added in parallel.
`engine_runtime._tick` is inspected but need not change: it already drains
Inbox before applying loader rows. If a later implementation requires such a
change, split it explicitly rather than assuming adjacency grants ownership.

Do not touch #7974's Worktrees `context_cli.py`, `manager_launch_cli.py`,
`worktree_identity.py`; #7788's native launch/update-stage files; guidance
refresh worker files; or telemetry adapters/reporting. No telemetry expansion
is needed to count fixture writes, frames or normalization calls.

## Documentation and completion

Update the Worktrees current-roster protocol docs and Manager current-feed/
maintenance docs alongside implementation, including v1 fallback cost and
the distinction between effect success and publication confirmation. Plan-only
changes do not alter runtime docs as if these APIs already existed, nor require
runtime version changes. Keep all implementation/qualification boxes open
until their own evidence is recorded. A merged plan is not campaign completion.
