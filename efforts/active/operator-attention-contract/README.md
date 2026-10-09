# Operator Attention Contract ("what needs you")

- **Slug:** `operator-attention-contract`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`, sources in `agent-bridge` and `agent-worktrees`)
- **Branch(es):** two implementation PRs off `dev` -- ThomasMichon/copilot-extensions#5668 (the contract, the aggregator, the CLI, command sources and the `dispatch` source), then one for the `bridge` and `pr` sources with their sibling commands; Phase 4 clients as separate PRs (the Tasks pane in this repository; a downstream dashboard in its own)
- **Created:** 2026-10-07
- **Status:** Active (Phases 1-3 built: ThomasMichon/copilot-extensions#5668 merged; the `bridge` and `pr` sources in review in ThomasMichon/copilot-extensions#5813)
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
| Implementer | All phases | a worktree off `dev` per implementation PR |

## Coordination

- **Topology:** Phases 1 and 3 with the `dispatch` source land first (ThomasMichon/copilot-extensions#5668,
  one coherent surface); the `bridge` and `pr` sources follow in one PR with
  their sibling commands (they touch agent-bridge and agent-worktrees); Phase 4
  clients follow separately.
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
| agent-bridge | `attention.value`: `input_required` (with `pending_input[0].message`), `permission_required`, `policy_required`, `failed` (the full `AttentionReason` set also has `unreachable`, `contract_changed` and settled reasons) | result snapshot (`result_snapshot.py`), `models.AttentionReason` | `agent-bridge result <s>`, `wait --attention <reason>` |
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

- [x] The versioned item, the order, deduplication, per-source results with first-observed times, and the aggregate envelope, as specified in [contract.md](contract.md). Only discovered sources take part. (ThomasMichon/copilot-extensions#5668)
- [x] Unit tests: ordering, dedupe, degraded vs empty, item and envelope round-trip.

### Phase 2 — Built-in adapters ([sources.md](sources.md#built-in-adapters))

- [x] `dispatch`: steering asks, holds, unconfirmed completion claims, and stalled queues. (ThomasMichon/copilot-extensions#5668)
- [x] `bridge`, with its sibling `agent-bridge --json attention <session>` read (HTTP protocol 29).
- [x] `pr`, with `agent-worktrees claims find pr` extended across every project and repo.
- [x] A per-source timeout bounds each adapter; a timeout is that source's `failed`.

### Phase 3 — CLI + pluggable sources ([sources.md](sources.md#cli-and-pluggable-sources))

- [x] `attention` and `attention next` with the position cursor. (ThomasMichon/copilot-extensions#5668)
- [x] Command sources registered through `attention source add`. (ThomasMichon/copilot-extensions#5668)
- [x] Docs: each plugin's CLI reference, the skill reference, and the item schema.

### Phase 4 — Clients

- [ ] Tasks pane: a single "Needs you" rail rendered from `attention --json`.
- [ ] A downstream dashboard can drop its UI-side merge for this CLI (outside
  this repo; noted for adopters).

## Validation Plan

Each tier's required cases are in [validation.md](validation.md).

- [x] Unit: contract, ordering, dedupe and first-observed times.
- [x] Unit: the envelope and selective reads.
- [x] Unit: the dispatch adapter.
- [x] Unit: command sources and external identity.
- [x] Unit: the bridge adapter.
- [x] Unit: the pr adapter.
- [ ] Simple e2e.
- [ ] Live.

## Proposal

_Pending._ Decided:

1. **Owner: agent-dispatch**, which already owns the operator inbox. The contract
   module stays owner-neutral, so another plugin can produce or render items
   without depending on the aggregator.

Open questions for review:

1. **The bridge `stalled` threshold.** How long a busy session's transcript may
   stay still before it's `stalled` (presence `unknown` alone never is). The dispatch queue's threshold is specified in [sources.md](sources.md); until this one is, the bridge
   adapter emits no `stalled` items.
2. **Hold as an item.** An operator's own hold is listed (low severity) so it
   isn't forgotten. It could instead be filtered out by default.

## Journal

### 2026-10-08 — The `bridge` and `pr` sources
- `agent-bridge --json attention <session>...` reads the bridge's current
  attention reason for an owned or a represented session (HTTP protocol 29,
  `CURRENT_ATTENTION_PROTOCOL_VERSION`; `unsupported` against an older daemon).
  The bridge source reads each candidate by its worktree handle, so the answer
  and the action follow the session heading the worktree now.
- `claims find pr` needs no project context, `--repo` is optional, and its
  `--json` carries the schema-1 per-project envelope; older records' bare repo
  names and missing numbers are recovered from the PR URL.
- Decided while building: `--include-remote` gates the transcript presence read
  of bridge sessions on a remote target (the SSH read); every registered
  session's attention is a local daemon read. A tracked PR whose record says
  `merged` is skipped unread (a merge is terminal on every provider); stopped
  and ended bridge sessions aren't candidates. A `pr bar` read makes several
  provider calls, so the pr source reads ten at a time under a 90 s deadline.

### 2026-10-08 — Two PRs; design in sibling docs
- Implementation lands in two PRs: ThomasMichon/copilot-extensions#5668 (the
  core, the CLI, command sources and the `dispatch` source), then the `bridge`
  and `pr` sources with their sibling commands. This supersedes the one-PR plan
  below. The design moved to [contract.md](contract.md),
  [sources.md](sources.md) and [validation.md](validation.md). A session no
  managed worktree hosts stays `uncertain` until agent-bridge exposes a logical
  reference; built-in actions carry the read's coordinator; the stalled
  thresholds are environment variables.

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
