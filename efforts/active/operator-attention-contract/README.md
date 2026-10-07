# Operator Attention Contract ("what needs you")

- **Slug:** `operator-attention-contract`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`, sources in `agent-bridge` and `agent-worktrees`)
- **Branch(es):** one implementation PR off `dev` for Phases 1-3; clients (Phase 4) land with their own repos
- **Created:** 2026-10-07
- **Status:** Draft
- **Vision:** [agent-dispatch](../../../visions/plugins/agent-dispatch/README.md) §Behaviors *buildup-is-a-health-signal*, §Features *verify-the-completion-claim* (work "held for attention")

## Guiding Intent

An operator running many agents needs one answer to "what needs me right now,
and what first?" Today each client assembles that answer itself, from signals
with different shapes and no ordering contract:

- agent-dispatch marks a task blocked on steering (`awaiting_steer`) or held
  (`hold_reason`), and a self-tracked task whose completion claim is
  `submitted` (not yet confirmed to `completed`) waits for review;
- agent-bridge marks a session parked on a question (`state.attention.value ==
  "input_required"`, `pending_input[]`), and `presence` says `awaiting_input` /
  `unknown` from the transcript;
- agent-worktrees `pr bar` says a PR's merge bar `failed` (the author has
  something to do) or is `unknown`;
- a client's own sources (sign-in expiry, a host project's coordination asks)
  are merged only in its UI.

So every client re-derives the answer, none can be driven from the CLI, a source
that fails to read silently looks like "nothing needs you", and the same entity
can appear twice. This effort defines **one versioned attention item**, **one
aggregator** with pluggable sources, and a CLI any client renders, so "nothing
needs you" and "a source couldn't be read" are never confused.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementer | All phases | one worktree off `dev` for Phases 1-3 |

## Coordination

- **Topology:** one PR for Phases 1-3 (the contract, the adapters and the CLI
  are one coherent, testable surface); Phase 4 clients follow separately.
- **Host (owns PRs):** the implementer.
- **Delegates:** none.
- **Handoff:** n/a.

## Context

### Today's signals (verified 2026-10-07 against `dev`)

| Source | Signal | Where it lives | CLI |
|---|---|---|---|
| agent-dispatch | task `awaiting_steer` (a posted card's `request_input`) | task record; board group "Blocked" (`board_cli.py`) | `agent-dispatch inbox --awaiting-steer`, `inbox --board` |
| agent-dispatch | task `hold_reason` (operator pause) | task record; board group "Paused" | `inbox --board` |
| agent-dispatch | self-tracked task `submitted`: a completion claim awaiting confirmation (`confirm` moves it to the terminal `completed`; `task_state_machine.py`) | task status | `inbox --board` |
| agent-dispatch | backlog buildup: `oldest_queued_age`, `oldest_held_live_age` (raw numbers, no policy) | `TaskQueue.backlog_health()` (`queue_liveness.py`) | the coordinator's `GET /health` (`backlog`) |
| agent-bridge | `attention.value`: `input_required` (with `pending_input[0].message`), `permission_required`, `failed` | result snapshot (`result_snapshot.py`) | `agent-bridge result <s>`, `wait --attention input_required` |
| agent-bridge | `presence`: `awaiting_input` / `unknown` (+ confidence) | transcript (`peek_snapshot.py`) | `agent-bridge presence <s> --json` |
| agent-worktrees | merge bar `failed` / `unknown` | provider read through `PRProvider.get_bar_snapshot` (`pr_bar.py`; landed on `dev` in #5566) | `agent-worktrees pr bar <repo> <n> --json` (exit 0 met/merged, 10 pending, 11 failed, 12 unknown) |

### Prior art

- [agent-bridge attention waits](../../2026/09/03%20agent-bridge-attention-waits/README.md): waiting *on one session* for an attention reason. This effort is the cross-entity *inbox* over many.
- [agent-dispatch monitor + confirmed state](../agent-dispatch-monitor-and-confirmed-state/README.md): produces the `submitted` (unconfirmed completion) signal this effort surfaces.
- [agent-dispatch tasks-pane UX](../agent-dispatch-tasks-pane-ux-overhaul/README.md): a client that would render the queue.

## Request

Define a typed, versioned attention contract with CLI parity. It is ordered and
deduplicated in the core rather than in each UI, distinguishes lifecycle state
from display state, and reports a source failure as a degraded read rather than
an empty queue.

## Plan

### Phase 1 — The item contract + aggregator core (agent-dispatch)

- [ ] `attention_contract.py`: the item, versioned (`schema: 1`):

  | Field | Meaning |
  |---|---|
  | `schema` | the item's own version, `1`; carried on every item, separately from the envelope's |
  | `id` | stable per item: `<source>:<entity>:<entity_ref>`, e.g. `dispatch:task:<task-id>`, `bridge:session:<session-id>`, `pr:pr:<owner/name>#<n>` -- unique per entity, so it's a sound tie-breaker and `--after` cursor |
  | `entity` | a shared kind -- `task` \| `session` \| `pr` \| `queue`, dedupable across sources -- or a pluggable source's own kind, namespaced by that source as `x.<source>.<kind>` (two external adapters' `login` items never collide) |
  | `entity_ref` | the canonical reference within its kind: a task id, a bridge session id, a PR as `<owner/name>#<n>` (never a URL, so two spellings of one PR are one key), a queue as its canonical repo |
  | `lifecycle_state` | the owner's own state (`started`, `submitted`, `live`, `open`, ...); `null` for an entity with no owner lifecycle (a `queue`) |
  | `display_state` | `failed` \| `stalled` \| `awaiting_input` \| `blocked` \| `review` |
  | `severity` | derived from `display_state`: `failed` > `stalled` > `awaiting_input` > `blocked` > `review` |
  | `reason` | one line, ≤ 200 chars |
  | `created_at`, `updated_at` | when the condition began / was last observed. A source that only knows when it observed something (e.g. a `pr bar` read) gets `created_at` from the aggregator's persisted first-observed time, kept **per source** for `(source, entity, entity_ref, display_state)`, so repeated reads keep the same order. Each source's time is cleared only by proof from that same source that the condition ended: a read of it with `status: ok` that no longer contains the item. An item missing from a `failed` or `uncertain` read, or from a source that is now `disabled`, keeps its time -- an outage proves nothing, so recovery doesn't reorder an unchanged queue -- and one source's `ok` omission never clears another source's evidence for the same entity. A deduplicated item's `created_at` is the earliest of its contributing sources' times |
  | `confidence` | `reported` \| `scanned` \| `heuristic` (presence's vocabulary) |
  | `actions[]` | `{verb, argv}`: sanctioned commands that run **as-is**, with no placeholder to fill (e.g. `agent-dispatch card show <task-id>`, `agent-bridge result <session-id>`). An answer that needs operator input isn't an action: the item carries the card's own `request_input` form spec (`input`), and the client submits it with `agent-dispatch steer submit` once filled |
  | `source` | the adapter that produced it |
  | `input` | optional: the card's `request_input` form spec when resolving it needs an operator's answer (submitted with `agent-dispatch steer submit`) |
  | `also[]` | the lower-ranked items deduplicated into this one (each a full item, in queue order); empty when none |

- [ ] **Display, not lifecycle.** A `live`/`started` entity waiting on a human
  surfaces. `blocked` counts only when nothing inbound can still resolve it.
- [ ] **Order:** severity (worst first), then `created_at` (oldest first), then
  `id`, so the order is fully deterministic.
- [ ] **Dedupe** on `(entity, entity_ref)` — the canonical kind plus its canonical
  reference, never the bare reference (a task id and a session id can share a
  string; a source's own `x.<source>.<kind>` keeps its references to itself).
  The winner, and the order of `also[]`, use the queue's own precedence --
  severity, then `created_at`, then `id` -- so identical reads give identical
  results however the adapters' timeouts interleave: keep the highest severity; the others become `also[]` on the kept item,
  so nothing is silently dropped.
- [ ] **Source result:** each adapter returns `{items[], status, error?, uncertain, read_at}`
  with `status` one of `ok` (fully read), `failed` (couldn't be read), `uncertain`
  (read, but `uncertain` of its entities couldn't be classified -- e.g. presence
  `unknown`, a `pr bar` exit 12) or `disabled` (not installed). The aggregate's
  `status` is the first that applies, in this precedence: `degraded` when any
  enabled source `failed`; `partial` when any is `uncertain` (with the counts);
  `attention` when there are items; `clear` otherwise (every enabled source `ok`,
  no items). One read gives one status, however its sources fail -- so neither a failed nor a partly unreadable source can read
  as "all clear". `disabled` sources never change it.
- [ ] **Aggregate response** (`attention --json`), versioned on its own -- never a
  bare item list, so a client can always tell `clear` from `degraded`:

  ```json
  {
    "schema": 1,
    "status": "clear | attention | partial | degraded",
    "read_at": "<ISO-8601 UTC>",
    "sources": [
      {"name": "dispatch", "status": "ok | failed | uncertain | disabled",
       "error": "<one line; only when failed>", "uncertain": 0,
       "items": 0, "read_at": "<ISO-8601 UTC>"}
    ],
    "items": ["<item>, in queue order"]
  }
  ```

  `sources[]` lists every discovered or configured source, `disabled` ones
  included, sorted by `name`; `uncertain` and `items` are counts. `attention next`
  returns the same envelope with `item` (one item, or `null` when the queue is
  empty) and `cursor` in place of `items`. Adding a field is compatible; renaming,
  removing or retyping one bumps `schema`.
- [ ] **Only discovered sources take part** (standalone-first,
  [a-la-carte independence](../../../docs/patterns/a-la-carte-independence.md)):
  an optional sibling that isn't installed (agent-bridge, agent-worktrees) is
  listed `disabled` and stays dark — it never degrades the result. An
  agent-dispatch-only install is a complete, non-degraded queue of its own items.
- [ ] Unit tests: ordering, dedupe, degraded vs empty, item and envelope schema round-trip.

### Phase 2 — Built-in adapters

- [ ] **dispatch**:
  - `awaiting_steer` → `awaiting_input`;
  - `hold_reason` → `blocked` (the operator's own hold: low severity, still listed);
  - a self-tracked task in `submitted` (a completion claim awaiting
    confirmation) → `review`. `completed` is the confirmed terminal and is never
    an item;
  - undraining buildup (vision *buildup-is-a-health-signal*) → `stalled`, one
    item per repo (`entity: queue`, `entity_ref`: the canonical repo), built on
    `backlog_health(repo=...)`. The predicate is `oldest_queued_age >
    queued_after` **or** `oldest_held_live_age > held_live_after` (strictly
    greater, in seconds), with both thresholds in agent-dispatch config under
    `attention.stalled` (`queued_after_secs`, `held_live_after_secs`; default
    1800 each; `0` turns that half off). Held tasks whose owner's liveness is
    `unknown` or `gone` never count: only a live owner that stopped progressing
    is buildup. `reason` carries both ages and the `queued`/`held_live` counts;
    the item clears on an `ok` read where neither age exceeds its threshold.
- [ ] **bridge**:
  - `input_required` → `awaiting_input` (`reported`);
  - `permission_required` (a represented snapshot / `wait --attention`) →
    `awaiting_input` (`reported`);
  - `failed` (a local result snapshot) → `failed` (`reported`);
  - presence `awaiting_input` → `awaiting_input` (`scanned`/`heuristic`);
  - presence `unknown` is **not** an item: it means the transcript couldn't be
    read or holds no presence signal, not that the session stalled. It counts
    toward the bridge source's status (`uncertain: n`). `stalled` needs independent
    evidence: a session the bridge calls busy whose transcript hasn't moved for
    longer than a configured threshold (an open question below; no bridge
    `stalled` items until it's decided).

  Local reads only by default; remote venues opt in (`--include-remote`), since
  each is an SSH read.
- [ ] **pr** (`pr bar`, on `dev` since #5566): tracked open PRs whose `pr bar` exit is 11
  (`failed`) → `failed` (the author has something to do). Exit 12 (`unknown`)
  counts toward the source's status, not as an item. **Candidates:** the open
  PRs tracked by agent-worktrees across **every project registered on this
  machine**, not just the one the caller's CWD belongs to. They're enumerated
  through agent-worktrees' own CLI (never by reading its files), and each one is
  read with explicit project context (`agent-worktrees -p <project> pr bar
  <worktree-id> --json`), so the result is the same from any CWD. A project whose
  tracked PRs can't be enumerated makes the source `failed`: a partial list
  can't claim to be complete.
- [ ] Each adapter is bounded by a per-source timeout. A timeout is that source's
  `status: failed` (with the timeout as its `error`), not a hang.

### Phase 3 — CLI + pluggable sources

- [ ] `agent-dispatch attention [--json] [--source <name>...] [--include-remote]`:
  the ordered queue, with the degraded banner in text mode.
- [ ] `agent-dispatch attention next [--after <cursor>]`: the oldest worst item (a
  keyboard walk in a UI is this, repeated). The cursor is opaque but carries the
  queue position -- `(severity, created_at, id)` of the item last shown -- not just
  its id, so `next` returns the first item strictly after that position in the
  current read even when the item it names was resolved (gone) or deduped into
  another; it wraps to the top once nothing is after it.
- [ ] **External adapters:** a host project registers a source as a command (an
  `argv`) in config. The command prints the same source-result envelope a
  built-in adapter returns, `{"schema": 1, "items": [...], "status"?,
  "uncertain"?, "error"?, "read_at"?}`, so a partial read can say so: it reports
  `status: uncertain` with the count of entities it couldn't classify. Omitted
  fields are translated, never guessed: no `status` means `ok` when `uncertain`
  is absent or 0 and `uncertain` otherwise; no `read_at` means the aggregator's
  receipt time. `disabled` is the aggregator's to set, never a command's. Its own
  kinds are namespaced `x.<source>.<kind>` by the aggregator (it may also use the
  shared kinds). Its own signals (sign-in
  expiry, coordination asks) then join the same queue with no code in this repo,
  under the same timeout and degraded rules. Failure contract: a non-zero exit, a
  timeout, output that isn't JSON, a missing or unsupported `schema` (anything but
  `1`), a response without `items[]`, a `status`
  outside `ok | failed | uncertain`, a status that contradicts `uncertain`
  (`ok` with a count above 0, `uncertain` with 0), or any item that
  doesn't validate against the schema makes that source `failed` (with the reason
  as its `error`) -- never a crash of the aggregate, never an empty source. A
  command that reports `status: failed` itself is `failed` with its own `error`.
- [ ] Docs: `plugins/agent-dispatch/docs/cli-reference.md`, the skill reference,
  and the attention item schema in the plugin docs.

### Phase 4 — Clients

- [ ] Tasks pane: a single "Needs you" rail rendered from `attention --json`.
- [ ] A downstream dashboard can drop its UI-side merge for this CLI (outside
  this repo; noted for adopters).

## Validation Plan

- [ ] Unit: contract, ordering, dedupe — cross-source dedupe of one entity (two
  sources naming the same PR), cross-kind non-collision (a task and a session
  with the same id), and an equal-severity tie resolved the same way in any
  adapter order — repeated reads keep a source's first-observed `created_at`,
  and so does an outage and recovery (the source reads `failed`, then `uncertain`
  without the item, then `ok` with it: the same `created_at` throughout), while
  an `ok` read without the item followed by its return gives a new one; two
  sources naming one PR, where one source's `ok` read drops it while the other
  still reports it, keep the item's `created_at` — and
  each aggregate status (`clear`, `attention`, `degraded`, `partial`, and a
  mixed failed-plus-uncertain read resolving to `degraded`) from fixtures, with `disabled` sources leaving it unchanged — degraded vs empty vs disabled, each adapter on fixtures.
- [ ] Unit, the envelope: `attention --json` and `attention next --json` match
  the exact documented shape (keys, types, `sources[]` order) and round-trip,
  including an item with `lifecycle_state: null` (a queue), and every item
  carries its own `schema: 1`;
  `clear` and `degraded` with zero items stay distinguishable.
- [ ] Unit, the dispatch adapter: a `submitted` task is a `review` item and a
  `completed` one isn't; `stalled` at exactly the threshold isn't an item and one
  second over is; held tasks with an `unknown` or `gone` owner never count; a
  threshold of `0` turns its half off.
- [ ] Unit, command sources: a partial read (`status: uncertain`, `uncertain:
  2`) makes the aggregate `partial`; `{"schema": 1, "items": [...]}` alone reads
  as `ok`; a missing `schema` and `schema: 2` are each `failed`; each
  contradiction in the failure contract is `failed`.
- [ ] Unit, the pr adapter: from a CWD outside any project, two registered
  projects each tracking a PR with a failing bar give both items; a project
  whose tracked PRs can't be enumerated makes the source `failed`.
- [ ] Simple e2e: a local bridge session parked on `ask_user`, a task with
  `awaiting_steer`, and a tracked PR with a failing bar produce three items in the
  expected order. Kill one source and the result is `degraded` with the others
  intact. A command source that exits non-zero, prints non-JSON, or returns a
  malformed item is `failed`. Resolve the current item between two `next` calls
  (and have its entity deduped into another source's item) and the walk still
  advances deterministically.
- [ ] Live: an operator machine with real sessions and tasks; compare with what
  each owner's own CLI reports.

## Proposal

_Pending._ Decided:

1. **Owner: agent-dispatch**, which already owns the operator inbox. The contract
   module stays owner-neutral, so another plugin can produce or render items
   without depending on the aggregator.

Open questions for review:

1. **The bridge `stalled` threshold.** How long a busy session's transcript may
   stay still before it's `stalled` (presence `unknown` alone never is). The
   dispatch queue's threshold is specified above; until this one is, the bridge
   adapter emits no `stalled` items.
2. **Hold as an item.** An operator's own hold is listed (low severity) so it
   isn't forgotten. It could instead be filtered out by default.

## Journal

### 2026-10-07 — Review fixes
- Lifecycle names follow agent-dispatch's current states (`submitted` awaiting
  confirmation, `completed` terminal); the aggregate response envelope, the
  dispatch `stalled` predicate and the command-source envelope are specified;
  `created_at` survives a source outage, and is kept per source. Owner decided (agent-dispatch); Phases
  1-3 land as one PR.

### 2026-10-07 — Kickoff
- Effort created from a survey of today's signals (table above), and of how one
  dashboard client merges them in its UI today: no ordering contract, no CLI, and
  source failures read as "nothing needs you".
