# Operator Attention Contract ("what needs you")

- **Slug:** `operator-attention-contract`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`, sources in `agent-bridge` and `agent-worktrees`)
- **Branch(es):** per-slice PRs off `dev`
- **Created:** 2026-10-07
- **Status:** Draft
- **Vision:** [agent-dispatch](../../../visions/plugins/agent-dispatch/README.md) §Behaviors *buildup-is-a-health-signal*, §Features *verify-the-completion-claim* (work "held for attention")

## Guiding Intent

An operator running many agents needs one answer to "what needs me right now,
and what first?" Today each client assembles that answer itself, from signals
with different shapes and no ordering contract:

- agent-dispatch marks a task blocked on steering (`awaiting_steer`) or held
  (`hold_reason`), and a completed-but-unconfirmed self-tracked task waits for
  review;
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
| Implementer | All phases | per-slice worktrees off `dev` |

## Coordination

- **Topology:** independent per-slice PRs (one per phase below).
- **Host (owns PRs):** the implementer.
- **Delegates:** none.
- **Handoff:** n/a.

## Context

### Today's signals (verified 2026-10-07 against `dev`)

| Source | Signal | Where it lives | CLI |
|---|---|---|---|
| agent-dispatch | task `awaiting_steer` (a posted card's `request_input`) | task record; board group "Blocked" (`board_cli.py`) | `agent-dispatch inbox --awaiting-steer`, `inbox --board` |
| agent-dispatch | task `hold_reason` (operator pause) | task record; board group "Paused" | `inbox --board` |
| agent-dispatch | completed, unconfirmed self-tracked task | lifecycle (`completed` vs confirmed) | `inbox --board` |
| agent-bridge | `attention.value == "input_required"`, `pending_input[0].message` | result snapshot (`result_snapshot.py`) | `agent-bridge result <s>`, `wait --attention input_required` |
| agent-bridge | `presence`: `awaiting_input` / `unknown` (+ confidence) | transcript (`peek_snapshot.py`) | `agent-bridge presence <s> --json` |
| agent-worktrees | merge bar `failed` / `unknown` | provider read (`pr_bar.py`, in review in #5566 — not yet on `dev`) | `agent-worktrees pr bar <repo> <n> --json` (exit 0 met/merged, 10 pending, 11 failed, 12 unknown) |

### Prior art

- [agent-bridge attention waits](../../2026/09/03%20agent-bridge-attention-waits/README.md): waiting *on one session* for an attention reason. This effort is the cross-entity *inbox* over many.
- [agent-dispatch monitor + confirmed state](../agent-dispatch-monitor-and-confirmed-state/README.md): produces the "unconfirmed completion" signal this effort surfaces.
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
  | `id` | stable per (source, entity), e.g. `dispatch:task:<id>`, `bridge:session:<id>`, `pr:<owner/name>#<n>` |
  | `entity` | `task` \| `session` \| `pr` \| `other` (a source-defined kind) |
  | `entity_ref` | the canonical reference within its kind: a task id, a bridge session id, a PR as `<owner/name>#<n>` (never a URL, so two spellings of one PR are one key) |
  | `lifecycle_state` | the owner's own state (`started`, `live`, `open`, ...) |
  | `display_state` | `failed` \| `stalled` \| `awaiting_input` \| `blocked` \| `review` |
  | `severity` | derived from `display_state`: `failed` > `stalled` > `awaiting_input` > `blocked` > `review` |
  | `reason` | one line, ≤ 200 chars |
  | `created_at`, `updated_at` | when the condition began / was last observed |
  | `confidence` | `reported` \| `scanned` \| `heuristic` (presence's vocabulary) |
  | `actions[]` | `{verb, argv}`: the sanctioned CLI that resolves it, complete and runnable (e.g. `agent-dispatch steer submit <task-id> --field <key>=<value>`, `agent-dispatch card show <task-id>`) |
  | `source` | the adapter that produced it |

- [ ] **Display, not lifecycle.** A `live`/`started` entity waiting on a human
  surfaces. `blocked` counts only when nothing inbound can still resolve it.
- [ ] **Order:** severity (worst first), then `created_at` (oldest first), then
  `id`, so the order is fully deterministic.
- [ ] **Dedupe** on `(entity, entity_ref)` — the canonical kind plus its canonical
  reference, never the bare reference (a task id and a session id can share a
  string): keep the highest severity; the others become `also[]` on the kept item,
  so nothing is silently dropped.
- [ ] **Source result:** each adapter returns `{items[], ok, error?, read_at}`. The
  aggregate carries `sources[]` with each one's status. Any `ok: false` makes the
  aggregate `degraded: true`, never an empty queue that reads as "all clear".
- [ ] **Only discovered sources take part** (standalone-first,
  [a-la-carte independence](../../../docs/patterns/a-la-carte-independence.md)):
  an optional sibling that isn't installed (agent-bridge, agent-worktrees) is
  listed `disabled` and stays dark — it never degrades the result. An
  agent-dispatch-only install is a complete, non-degraded queue of its own items.
- [ ] Unit tests: ordering, dedupe, degraded vs empty, schema round-trip.

### Phase 2 — Built-in adapters

- [ ] **dispatch**:
  - `awaiting_steer` → `awaiting_input`;
  - `hold_reason` → `blocked` (the operator's own hold: low severity, still listed);
  - unconfirmed self-tracked completion → `review`;
  - undraining buildup (vision *buildup-is-a-health-signal*) → `stalled`.
- [ ] **bridge**:
  - `input_required` → `awaiting_input` (`reported`);
  - presence `awaiting_input` → `awaiting_input` (`scanned`/`heuristic`);
  - presence `unknown` is **not** an item: it means the transcript couldn't be
    read or holds no presence signal, not that the session stalled. It counts
    toward the bridge source's status (`uncertain: n`). `stalled` needs independent
    evidence: a session the bridge calls busy whose transcript hasn't moved for
    longer than a configured threshold.

  Local reads only by default; remote venues opt in (`--include-remote`), since
  each is an SSH read.
- [ ] **pr** (needs `pr bar`, #5566): tracked open PRs whose `pr bar` exit is 11
  (`failed`) → `failed` (the author has something to do). Exit 12 (`unknown`)
  counts toward the source's status, not as an item. Until `pr bar` lands, the
  adapter is `disabled`.
- [ ] Each adapter is bounded by a per-source timeout. A timeout is that source's
  `ok: false`, not a hang.

### Phase 3 — CLI + pluggable sources

- [ ] `agent-dispatch attention [--json] [--source <name>...] [--include-remote]`:
  the ordered queue, with the degraded banner in text mode.
- [ ] `agent-dispatch attention next [--after <id>]`: the oldest worst item (a
  keyboard walk in a UI is this, repeated).
- [ ] **External adapters:** a host project registers a source as a command (an
  `argv` that prints `{items[]}` JSON) in config. Its own signals (sign-in
  expiry, coordination asks) then join the same queue with no code in this repo,
  under the same timeout and degraded rules.
- [ ] Docs: `plugins/agent-dispatch/docs/cli-reference.md`, the skill reference,
  and the attention item schema in the plugin docs.

### Phase 4 — Clients

- [ ] Tasks pane: a single "Needs you" rail rendered from `attention --json`.
- [ ] A downstream dashboard can drop its UI-side merge for this CLI (outside
  this repo; noted for adopters).

## Validation Plan

- [ ] Unit: contract, ordering, dedupe — cross-source dedupe of one entity (two
  sources naming the same PR) and cross-kind non-collision (a task and a session
  with the same id) — degraded vs empty vs disabled, each adapter on fixtures.
- [ ] Simple e2e: a local bridge session parked on `ask_user`, a task with
  `awaiting_steer`, and a tracked PR with a failing bar produce three items in the
  expected order. Kill one source and the result is `degraded: true` with the
  others intact.
- [ ] Live: an operator machine with real sessions and tasks; compare with what
  each owner's own CLI reports.

## Proposal

_Pending._ Open questions for review:

1. **Owner.** This draft puts the aggregator in agent-dispatch, which already owns
   the operator inbox. The alternative is agent-worktrees, which is closest to the
   PR and worktree signals. The contract module is owner-neutral either way.
2. **The `stalled` threshold.** How long a busy session's transcript may stay
   still before it's `stalled` (presence `unknown` alone never is).
3. **Hold as an item.** An operator's own hold is listed (low severity) so it
   isn't forgotten. It could instead be filtered out by default.

## Journal

### 2026-10-07 — Kickoff
- Effort created from a survey of today's signals (table above), and of how one
  dashboard client merges them in its UI today: no ordering contract, no CLI, and
  source failures read as "nothing needs you".
