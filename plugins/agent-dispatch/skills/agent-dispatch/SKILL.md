---
name: agent-dispatch
description: >
  Coordinate agent work through the agent-dispatch task queue -- a portable,
  single-writer leased queue with a per-host coordinator. Use it to enqueue,
  browse/dedup, atomically claim, and drive tasks through their lifecycle so
  multiple worktree/session agents cooperate without racing through
  origin/master or needing an account per agent. Covers the CLI verbs, the
  six-state model, worker identity (machine/worktree), capability + affinity
  routing, targeting, dedup-before-create, spawning workers via agent-bridge,
  and loopback-vs-remote coordinator config.
  Trigger phrases include:
  - 'agent-dispatch'
  - 'task queue'
  - 'dispatch a task'
  - 'queue a task'
  - 'claim a task'
  - 'pick up a task'
  - 'my task inbox'
  - 'worktree-status'
  - 'coordinator'
  - 'graduate a handoff'
  - 'dispatch work to an agent'
---

# agent-dispatch -- Agent Task Queue + Coordinator

`agent-dispatch` is a **portable agent task-queue**. A per-host **coordinator**
(a single-writer SQLite/WAL daemon) hands out an **atomic leased claim** over a
queue of *tasks*, so multiple agents coordinate without racing through
`origin/master` pushes or needing a dedicated account each.

A **task** is a graduated handoff: a title + `prompt` + optional Markdown
`payload`. It carries routing (`requires` / `affinity`), targeting
(`target_machine` / `target_worktree` / `target_repo`, `labels`), and moves
through a six-state lifecycle.

## When to reach for it

- You want to **hand work to another agent** (same machine or another) without
  babysitting it -- enqueue a task, let a capable worker claim it.
- **Several agents** could do a piece of work and exactly one should -- the
  atomic claim guarantees a single winner.
- A **crashed/full-auto agent** must not hold work forever -- lease expiry
  returns the task to the queue automatically.
- You're **graduating a context-handoff** into durable, browsable, claimable
  work instead of a dead-end paste-prompt.

Not for: cross-machine *conversation* (that's agent-bridge `send`), or spawning
a local sub-agent in *this* session (that's the Task tool). agent-dispatch is
the **queue**; agent-bridge is an optional producer/subscriber alongside it.

## Prerequisite: a reachable coordinator

Every verb except `serve` is a thin client that talks to a coordinator over
HTTP. Point the CLI at one with `AGENT_DISPATCH_URL` (defaults to the loopback
`http://127.0.0.1:9330`); add `AGENT_DISPATCH_TOKEN` if it requires bearer auth.

```bash
agent-dispatch health          # confirm a coordinator is reachable first
```

- **Lone dev box:** run a loopback coordinator locally: `agent-dispatch serve`
  (or install it as a service -- see the plugin README).
- **Shared network:** set `AGENT_DISPATCH_URL` to the designated coordinator
  host; don't run a local one.

If `health` fails, start/point at a coordinator before anything else -- don't
retry claims against a dead URL.

## Worker identity -- resolved from your CWD

An agent's identity is the **`machine`/`worktree` pair** -- the only durable
agent id available. `claim` and `worktree-status` **auto-resolve it from the
current directory** by delegating to `agent-worktrees` (the same way git finds
its repo). So from inside your worktree you pass **no** identity flags:

```bash
agent-dispatch worktree-status     # my inbox: tasks targeted at + owned by me
agent-dispatch claim               # lease an eligible task; owner is auto-stamped
```

Override (or supply, when `agent-worktrees` is absent) with `--machine` /
`--worktree`. **Do not** invent an identity or type one by hand when the CWD can
resolve it -- let the resolution stand.

**Claim honors targeting:** a worker only leases tasks that are **untargeted**
or **targeted at its own** machine/worktree. That's what makes a bound handoff
stick to its worktree while a portable task floats to anyone.

## Repo lanes -- tasks stay in their own repo

Every task belongs to a **repo lane** -- the canonical remote of the *producing
agent's harness repo*. **Repos stay in their own lanes:** a task made by a
`webapp` agent is for `webapp` agents, and every subcommand is
**scoped to the calling repo by default**. You never see or claim another repo's
tasks. Like identity, the lane is **auto-resolved from your CWD** (via
`agent-worktrees get repo-remote`, falling back to `git remote get-url origin`),
so you pass nothing:

```bash
agent-dispatch create "..."      # lane auto-stamped from the calling repo
agent-dispatch sweep             # dedup corpus for THIS repo only
agent-dispatch list --status queued
```

- **Cross-repo *code* work stays in the producing lane.** If a `webapp`
  agent wants a change made in a `shared-lib` repo, it files the task in the
  **webapp** lane (optionally tagging the code target with
  `--target-repo shared-lib`). Another **webapp** agent picks it
  up and does the cross-repo work via the **`working-cross-repo`** flow -- it
  does **not** spawn a `shared-lib` harness. (Some repos are edited only as a
  target, never run as a harness.)
- **Targeting another lane is explicit.** `--repo <name|remote>` scopes a command
  to a specific other lane (`--repo webapp` or a full remote URL). There
  is **no** all-repos view -- the queue never exposes tasks globally.
- **Hybrid keys.** The wire/DB stores a device-independent **canonical remote**
  (so one shared coordinator keys every machine the same); the CLI lets you
  *type* and *reads back* the local repo **name** (resolved through the
  agent-worktrees registry). Output carries both `repo` (remote) and `repo_name`.

## The six-state lifecycle

```
proposed -> queued -> claimed -> started -> completed        (terminal)
                ^         |          |
                +- decline/yield ----+
                ^
                +- lease expiry (internal requeue, attempts++)
   (any non-terminal) -----------------------------> abandoned (terminal, permission-gated)
```

| State | Meaning | Claimable? |
|-------|---------|-----------|
| **proposed** | drafted / wording still undecided | No |
| **queued** | ready to be picked up | Yes |
| **claimed** | leased; worker may evaluate before committing | held |
| **started** | under active implementation | held |
| **completed** | driven to done | terminal |
| **abandoned** | discarded (duplicate / dropped priority) | terminal |

- **proposed** is a holding state for an idea not yet blessed; `approve` moves it
  to **queued**.
- **claimed -> started** when the worker commits; **claimed -> queued**
  (`yield`, a decline) if it evaluates and passes.
- **started -> queued** yields **with a note** on a recoverable snag (merge
  conflict, needs a later cycle); **started -> completed** on success.
- **abandon requires permission** (`--permit`) -- it's not a unilateral agent
  action; it's the discard path for duplicates / dropped priorities.
- **Lease expiry -> queued** is automatic and internal (bumps `attempts`): a
  dead worker's task resurfaces. The coordinator sweeps expired leases on a timer
  (`AGENT_DISPATCH_SWEEP_INTERVAL`, default 60s); `agent-dispatch recover` forces
  a sweep on demand.

## The everyday flow

### 1. Browse & dedup BEFORE creating

Always check for existing work before ideating a new task. The base dedup
mechanism is an **agent-driven sweep**: pull the corpus of live tasks, read
their descriptions, and verify with a normal *explore* pass whether the work
already exists. This is why every task must carry a **self-contained title +
prompt** -- enough for a sweeping agent to judge duplication without extra
context. The coordinator also backstops with a unique `dedup_key`.

```bash
agent-dispatch sweep                         # the dedup corpus: every non-abandoned
                                             #   task (proposed/queued/claimed/started/
                                             #   completed), newest first -- read these,
                                             #   then explore/verify before creating
agent-dispatch find "narration track"        # quick substring probe over title/prompt
agent-dispatch list --status queued,started  # filter by status (comma-separate for several),
                                             #   --target-machine/--target-repo/--label
```

> **VEI is a future optimization, not a requirement.** Correctness rests on the
> agent-driven sweep over descriptive task text; a semantic index (VEI) is a
> pluggable *performance* layer over the same corpus that can be added later
> without changing the flow. Keep the plugin portable -- it must dedup fine on a
> lone box with no facility VEI.

### 2. Create a task

```bash
agent-dispatch create "Add narration track" \
  --prompt "segment 42 needs a narration pass" \
  --require logger \                 # hard: only a worker advertising 'logger' can claim
  --affinity worktree=same \         # soft: bias toward the same worktree, never exclude
  --label media \
  --target-repo copilot-extensions \ # OPTIONAL: the cross-repo *code* target (stays in THIS lane)
  --dedup-key narration-seg42        # makes create idempotent
```

The **lane** (`--repo`) defaults to the calling repo -- omit it inside your
worktree. `--target-repo` is different: it's metadata naming the *code* a
cross-repo task touches; the task still lives in **your** lane and a same-lane
agent does the cross-repo work via `working-cross-repo`.

Write the title + prompt to be **self-describing** (see the sweep note above):
a producer scanning existing tasks should be able to tell yours apart from
theirs from the description alone.

Create a **draft** instead with `--proposed` (unclaimable until `approve`).
Defer with `--not-before <epoch>` (scheduled creation). Attach a payload with
`--payload-inline` (small), `--payload-file <path>` (reads a file; a large one
spills to a content-addressed blob automatically), or `--payload-ref` (an
external pointer like `pr/123`).

### Producers -- scheduled + reactive task creation

The coordinator only owns the queue; anything that *creates* tasks is a
**producer**. Two ship in-box, each driven by a declarative JSON spec:

- **`agent-dispatch schedule tick <spec>`** (and `schedule serve <spec>
  --interval N`) -- a scheduler/timer producer. Each tick creates one task per
  due occurrence of every schedule (`interval_seconds`, or daily `at: ["HH:MM"]`
  times), stamping `not_before` and a deterministic `dedup_key`
  (`sched:<id>:<epoch>`) so re-ticks are idempotent. Drive `tick` from cron / a
  systemd timer / `manage_schedule`, or run the built-in `serve` loop.
- **`agent-dispatch webhook --config <cfg>`** -- a reactive producer: an HTTP app
  with `POST /webhook/pr` (a **merged** PR -> follow-up task, `source=pr-webhook`,
  `origin_ref=pr/<n>`, lane from the payload's repo remote) and
  `POST /webhook/telemetry` (a **firing** alert -> remediation task,
  `source=telemetry`). Deterministic `dedup_key`s make redelivery safe.

See the plugin README (**Producers**) for the spec/config shapes.

### 3. Claim, work, finish

```bash
agent-dispatch claim --capability logger     # atomically leases one eligible task
# note the returned task id + owner, then:
agent-dispatch start    <id> <owner>
agent-dispatch heartbeat <id> <owner>        # extend the lease during long work
agent-dispatch complete <id> <owner> --result-ref pr/123
```

Recoverable snag -> return it for a later cycle (keep the note!):

```bash
agent-dispatch yield <id> <owner> --note "blocked on merge conflict; retry next cycle"
```

Discard a duplicate / dropped task (needs permission):

```bash
agent-dispatch abandon <id> --worker-id <owner> --permit --reason "duplicate of task X"
```

### Inspect

```bash
agent-dispatch show    <id>       # full task record
agent-dispatch events  <id>       # append-only audit trail of every transition
agent-dispatch payload <id>       # resolved payload (inline or blob); --raw prints content only
agent-dispatch consume <id>       # resume-and-consume: drive to completed (idempotent) + print payload
agent-dispatch watch              # stream task.* events (SSE) as JSON lines
```

> **`consume` is the handoff-pickup shortcut.** It rolls the whole
> approve → claim → start → complete lifecycle into one idempotent call and then
> prints the payload, so a successor's *single* command loads the brief **and**
> marks the baton spent -- a handoff is completed the moment it is picked up. An
> already-terminal (or unclaimable) task just has its payload re-printed, never
> an error. Use plain `payload --raw` when you want to read *without* consuming.

## Routing: `requires` (hard) vs `affinity` (soft)

- **`requires`** (repeatable `--require`) -- capability tokens (`logger`,
  `review`, `merge`) or an identity pin (`agent:review-bot`). A task is
  claimable only when `requires` is a **subset** of the worker's advertised
  `--capability` set. Two machines advertising the same capability give
  **cooperative, redundant** coverage: first writer wins; if one dies mid-lease,
  the other reclaims after expiry -- no leader election.
- **`affinity`** (repeatable `--affinity key=value`) -- soft *preferences*
  (preferred agent/worktree) that order candidates but **never exclude**.
- A **hard pin** is just a target promoted into `requires`; `detach <id>` demotes
  a hard worktree pin to a soft affinity (e.g. once local work is pushed, a bound
  handoff becomes portable).

## Spawning a worker via agent-bridge

`create --spawn` asks **agent-bridge** to spawn a worker that claims and executes
the task by id:

```bash
agent-dispatch create "Summarize the PR" --require review --spawn              # managed (waits)
agent-dispatch create "Summarize the PR" --spawn --spawn-agent task-worker --async  # fire-and-forget
```

If the `agent-bridge` CLI isn't on PATH, `--spawn` **degrades gracefully**: the
task is left queued for any worker to claim. agent-dispatch stays fully usable
without a bridge.

## MCP tools instead of the CLI

`agent-dispatch mcp` runs a local **stdio MCP server** exposing the same
operations as tools (`dispatch_create`, `dispatch_find`, `dispatch_claim`,
`dispatch_start`, `dispatch_complete`, `dispatch_payload`,
`dispatch_worktree_status`, ...). It resolves your `machine`/`worktree` identity
from the working directory just like the CLI, so `dispatch_claim` /
`dispatch_worktree_status` are auto-scoped with no arguments. Point a sub-agent's
`.mcp.json` at `{"command": "agent-dispatch", "args": ["mcp"]}` (needs the `mcp`
extra). The coordinator also hosts the **same tools over HTTP at `/mcp`** for
remote clients that supply identity via `X-Agent-Machine`/`X-Agent-Worktree`
headers. The CLI and MCP tools are interchangeable — use whichever fits.

## Config quick reference

| Env var | Role |
|---------|------|
| `AGENT_DISPATCH_URL` | coordinator base URL the CLI talks to (point at a remote host) |
| `AGENT_DISPATCH_TOKEN` | bearer token (client sends, server validates) |
| `AGENT_DISPATCH_HOST` / `AGENT_DISPATCH_PORT` | where the coordinator binds (server side) |
| `AGENT_DISPATCH_DB` | SQLite queue file (server side) |
| `AGENT_DISPATCH_SWEEP_INTERVAL` | auto lease-recovery cadence in seconds (server side; `0` disables) |

All CLI output is JSON on stdout, so verbs compose with `jq` and other tooling.
Global flags `--url` / `--token` override the env per-invocation.

## Gotchas

- **Everything is lane-scoped.** By default you only see/claim **your repo's**
  tasks. An "empty" sweep or "no claimable task" may just mean *your lane* is
  empty -- another repo's tasks are invisible by design. Use `--repo <name>` to
  look at a specific other lane; there is no all-repos view.
- **Lane != code target.** `--repo` is the owning lane (defaults to the calling
  repo). `--target-repo` is the cross-repo *code* a task touches -- the task
  still lives in the producing lane and a same-lane agent does the work via
  `working-cross-repo`. Don't file a task into another repo's lane to "send" it
  there.
- **Check `health` first.** Every non-`serve` verb needs a reachable coordinator;
  a failing claim usually means the URL is wrong or the daemon is down, not that
  the queue is empty (`claim` exits non-zero with "no claimable task" when the
  queue simply has nothing for you).
- **Dedup before create.** `sweep` (then explore/verify) is the primary check;
  `find` is a quick probe; rely on `--dedup-key` as the backstop, not the first
  line of defense. Write self-contained titles/prompts so the sweep can work.
- **Keep the yield note.** `started -> queued` is only useful to the next agent
  if you say *why* you yielded.
- **Don't fake identity.** Let `claim` / `worktree-status` resolve it from CWD;
  only pass `--machine` / `--worktree` to override or where agent-worktrees is
  absent.
