---
name: agent-dispatch
description: >
  Coordinate agent work through the agent-dispatch task queue -- a portable,
  single-writer queue with a per-host coordinator. Use it to enqueue,
  browse/dedup, atomically claim, and drive tasks through their lifecycle so
  multiple worktree/session agents cooperate without racing through
  origin/master or needing an account per agent. Covers the CLI verbs, the
  seven-state model, worker identity (machine/worktree), capability + affinity
  routing, include/exclude selector matching, targeting, dedup-before-create,
  atomic create-and-claim, spawning workers via agent-bridge,
  and loopback-vs-remote coordinator config.
  Trigger phrases include:
  - 'agent-dispatch'
  - 'agent-dispatch task queue'
  - 'dispatch a task'
  - 'queue an agent-dispatch task'
  - 'claim an agent-dispatch task'
  - 'pick up an agent-dispatch task'
  - 'agent-dispatch inbox'
  - 'worktree-status'
  - 'agent-dispatch coordinator'
  - 'graduate a handoff'
  - 'dispatch work to an agent'
---

# agent-dispatch -- Agent Task Queue + Coordinator

> **Before you start — use the payload-local session command.**
> The agent-dispatch session command catalog supplies an exact `argv[0]` owned
> by this plugin payload. Replace `<agent-dispatch catalog argv[0]>` in every
> shell operation below with that path; never search `PATH` or substitute a
> same-named command from another payload. In PowerShell, invoke it as
> `& "<agent-dispatch catalog argv[0]>" <args>`. The payload shim provisions
> its own runtime on first use and works without agent-worktrees. The first call
> may take ~30–120s (watch for `::agent-provisioning::`); let it finish and
> surface any exact provisioning failure instead of improvising a toolchain
> install.
>
> If session-start hooks did not publish the catalog, enumerate installed
> payloads for this plugin and fail unless exactly one exists. Invoke that
> payload's `bin/agent-dispatch` on POSIX or `bin\agent-dispatch.cmd` on Windows
> directly; never stamp or choose a global wrapper just to recover an in-session
> command, and never choose the first match from multiple marketplaces.

`agent-dispatch` is a **portable agent task-queue**. A per-host **coordinator**
(a single-writer SQLite/WAL daemon) hands out an **atomic leased claim** over a
queue of *tasks*, so multiple agents coordinate without racing through
`origin/master` pushes or needing a dedicated account each.

A **task** is a graduated handoff: a title + `prompt` + optional Markdown
`payload`. It carries routing (`requires` / `affinity`), targeting
(`target_machine` / `target_worktree` / `target_repo`, `labels`), and moves
through a seven-state lifecycle.

## When to reach for it

- You want to **hand work to another agent** (same machine or another) without
  babysitting it -- enqueue a task, let a capable worker claim it.
- **Several agents** could do a piece of work and exactly one should -- the
  atomic claim guarantees a single winner.
- A **crashed/full-auto agent** must not hold work forever -- a liveness GC pass
  returns the task to the queue once its owner worktree is confirmed gone.
- You're **graduating a context-handoff** into durable, browsable, claimable
  work instead of a dead-end paste-prompt.

Not for: cross-machine *conversation* (that's agent-bridge `send`), or spawning
a local sub-agent in *this* session (that's the Task tool). agent-dispatch is
the **queue**; agent-bridge is an optional producer/subscriber alongside it.

### Which coordination system? (sub-agent vs agent-bridge vs agent-dispatch)

There are **three** ways to coordinate agent work on top of agent-worktrees (the
durable worktree/session substrate), and the choice is easy to get wrong. Pick
by **what you're doing**:

| Scenario | Use |
|----------|-----|
| **In-worktree sub-task** — exploration, safely-parallel writes, MCP round-trips | **native sub-agent** (Task tool) |
| **Cross-repo / cross-machine sub-task you drive or converse with** — enforce the target repo's conventions, interactive back-and-forth | **agent-bridge** |
| **Fleet-wide chat / broadcast / status / negotiation** — reserve a resource or deploy slot, coordinate over a bug | **agent-bridge** |
| **Task claims / dedup of concurrent work** — claim an effort/item so two agents don't collide; release/complete when done | **agent-dispatch** |
| **Spin-off work you don't wait on** — optional follow-up, a tangential bug found mid-task; the outcome isn't essential | **agent-dispatch** |
| **Context handoff** — same worktree, next session | **agent-dispatch** task (complete-on-pickup) |

**The distinguishing axis** (the one agents get wrong): **agent-bridge = drive or
converse with a live agent you own or steer** (you wait on it, answer it, take it
over); **agent-dispatch = fire-and-forget queued/claimed work you don't own** —
you may poll its status or send steering messages, but it is *not* a controlled
sub-agent. Both cross machines; the axis is **ownership, not location**.

**agent-dispatch's four canonical use cases** (they line up with the *Producers*
below): (1) **handoff-prompt storage** for an in-place cutover (same worktree,
next session); (2) **fire-and-forget delegation** to another worktree/machine;
(3) **reactive tasks** on automated events (critical detections / posted reviews
/ outages — tightly scoped, since workers are expensive); (4) **scheduled tasks**
(recurring producers). These four are the standing intent recorded in the
plugin vision (`visions/plugins/agent-dispatch/` in the source repo).

## Prerequisite: a reachable coordinator

Every verb except `serve` is a thin client that talks to a coordinator over
HTTP. Point the CLI at one with `AGENT_DISPATCH_URL`; otherwise the client
discovers the local coordinator through the zdd routing table /
`~/.agent-dispatch/run/endpoint.json`, then falls back to legacy
`http://127.0.0.1:9847`. Add `AGENT_DISPATCH_TOKEN` if it requires bearer auth.

```bash
<agent-dispatch catalog argv[0]> health          # confirm a coordinator is reachable first
```

- **Lone dev box:** run a loopback coordinator locally:
  `agent-dispatch serve` <!-- marketplace-isolation: allow coordinator-management -->
  (or install it as a service -- see the plugin README). Local client verbs
  lazy-start a detached coordinator when none answers; `health` does not.
- **Shared network:** set `AGENT_DISPATCH_URL` to the designated coordinator
  host; don't run a local one.

If `health` fails, start/point at a coordinator before anything else -- don't
retry claims against a dead URL.

### Local + shared: the hybrid (cross-machine) topology

Two coordinators can coexist. Keep your **local** loopback one for same-machine
work (handoffs, local scheduling), and point at a **shared/elected coordinator**
only for **cross-machine** dispatch — the queue every machine claims from. A
client picks per command:

- Set `AGENT_DISPATCH_SHARED_URL` to the shared endpoint (in a multi-machine system, the
  always-on **hosted coordinator**; standalone, whichever mesh peer is elected to host it),
  and `AGENT_DISPATCH_SHARED_TOKEN` for its bearer (independent of the local
  token — the two authenticate separately).
- Add `--shared` to any client verb to target it:
  `<agent-dispatch catalog argv[0]> --shared create …`,
  `<agent-dispatch catalog argv[0]> --shared list`,
  `<agent-dispatch catalog argv[0]> --shared claim …`, and
  `agent-dispatch --shared supervise --pool …`. <!-- marketplace-isolation: allow supervisor-management -->
  Omit `--shared` for the local
  coordinator. An explicit `--url` always overrides both.
- The shared coordinator **binds loopback behind the secured mesh** and
  is reached by **client-initiated outbound** calls + bearer — never a raw LAN
  bind. A tunnel-only machine (reachable outbound only) therefore participates
  natively: it just calls out like everyone else.
- `--shared` with no `AGENT_DISPATCH_SHARED_URL` set **errors loudly** rather
  than silently using the local queue (which would strand a cross-machine task).

Dispatch-for-others and see-and-take-others' both ride this shared queue: enqueue
with `--shared create --target-machine <m>`, and the target machine runs
`--shared list` / `--shared claim` to pick up what's addressed to it.

## Worker identity -- resolved from your CWD

An agent's identity is the **`machine`/`worktree` pair** -- the only durable
agent id available. `claim` and `worktree-status` **auto-resolve it from the
current directory** by delegating to `agent-worktrees` (the same way git finds
its repo). So from inside your worktree you pass **no** identity flags:

```bash
<agent-dispatch catalog argv[0]> worktree-status     # my inbox: tasks targeted at + owned by me
<agent-dispatch catalog argv[0]> claim               # lease an eligible task; owner is auto-stamped
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
`agent-worktrees get repo-remote`, falling back to `git remote get-url origin`), <!-- marketplace-isolation: allow agent-worktrees-management -->
so you pass nothing:

```bash
<agent-dispatch catalog argv[0]> create "..."      # lane auto-stamped from the calling repo
<agent-dispatch catalog argv[0]> sweep             # dedup corpus for THIS repo only
<agent-dispatch catalog argv[0]> list --status queued
```

- **Cross-repo *code* work stays in the producing lane.** If a `webapp`
  agent wants a change made in a `shared-lib` repo, it files the task in the
  **webapp** lane (optionally tagging the code target with
  `--target-repo shared-lib`). Another **webapp** agent picks it
  up and does the cross-repo work via the **`working-cross-repo`** flow -- it
  does **not** spawn a `shared-lib` harness. (Some repos are edited only as a
  target, never run as a harness.)
- **Targeting another lane is explicit.** `--repo <name|remote>` scopes a command
  to a specific other lane (`--repo webapp` or a full remote URL). `list`,
  `find`, and `sweep` are lane-scoped by default; `inbox` is the intentional
  machine-scoped, cross-lane picker view, and `supervise --all-repos` is an
  explicit service opt-in.
- **Hybrid keys.** The wire/DB stores a device-independent **canonical remote**
  (so one shared coordinator keys every machine the same); the CLI lets you
  *type* and *reads back* the local repo **name** (resolved through the
  agent-worktrees registry). <!-- marketplace-isolation: allow agent-worktrees-management -->
  Output carries both `repo` (remote) and `repo_name`.

## The seven-state lifecycle

```
proposed -> queued -> claimed -> started -> completed        (terminal)
                ^         |          |
                +- decline/yield ----+
                ^         |          |
                +- owner-gone (liveness GC requeue, attempts++)
                          |          |
   (any non-terminal) --> abandoned (terminal, permission-gated)
   (owner-gone past the attempts cap) --> dead_letter (terminal)
```

| State | Meaning | Claimable? |
|-------|---------|-----------|
| **proposed** | drafted / wording still undecided | No |
| **queued** | ready to be picked up | Yes |
| **claimed** | held; worker may evaluate before committing | held |
| **started** | under active implementation | held |
| **completed** | driven to done | terminal |
| **abandoned** | discarded (duplicate / dropped priority) | terminal |
| **dead_letter** | requeued too many times (owner kept going gone) -- an actionable failure | terminal |

- **proposed** is a holding state for an idea not yet blessed; `approve` moves it
  to **queued**.
- **claimed -> started** when the worker commits; **claimed -> queued**
  (`yield`, a decline) if it evaluates and passes. The `claimed` window is the
  **evaluation window** (`claim --evaluation`) -- a semantic "evaluating, not yet
  committed" marker; elapsed time alone does **not** recover it (see below).
- **started -> queued** yields **with a note** on a recoverable snag (merge
  conflict, needs a later cycle); **started -> completed** on success.
- **abandon requires permission** (`--permit`, or `--duplicate-of <ref>` which
  self-permits and records the dedup reference) -- it's not a unilateral agent
  action; it's the discard path for duplicates / dropped priorities.
- **owner-gone -> queued** is automatic and internal (bumps `attempts`): a held
  task is requeued **only when its owner worktree is confirmed gone** by a
  **liveness** garbage-collection pass -- never on elapsed time, so a
  long-running live worker is never disturbed and a momentary bridge blip
  (verdict *unknown*) leaves the task alone. The coordinator runs GC on a timer
  (`AGENT_DISPATCH_GC_INTERVAL`, default 60s);
  `<agent-dispatch catalog argv[0]> recover` forces a
  pass on demand. (There is **no** lease TTL: recovery is reconciled against live
  workers, not a clock.)

## The everyday flow

### 1. Browse & dedup BEFORE creating

Always check for existing work before ideating a new task. The base dedup
mechanism is an **agent-driven sweep**: pull the corpus of live tasks, read
their descriptions, and verify with a normal *explore* pass whether the work
already exists. This is why every task must carry a **self-contained title +
prompt** -- enough for a sweeping agent to judge duplication without extra
context. The coordinator also backstops with a unique `dedup_key`.

```bash
<agent-dispatch catalog argv[0]> sweep       # the dedup corpus: every non-abandoned
                                             #   task (proposed/queued/claimed/started/
                                             #   completed), newest first -- read these,
                                             #   then explore/verify before creating
<agent-dispatch catalog argv[0]> find "narration track"        # quick substring probe over title/prompt
<agent-dispatch catalog argv[0]> list --status queued,started  # filter by status (comma-separate for several),
                                             #   --target-machine/--target-repo/--label
```

> **VEI is a future optimization, not a requirement.** Correctness rests on the
> agent-driven sweep over descriptive task text; a semantic index (VEI) is a
> pluggable *performance* layer over the same corpus that can be added later
> without changing the flow. Keep the plugin portable -- it must dedup fine on a
> lone box with no shared VEI.

### 2. Create a task

```bash
<agent-dispatch catalog argv[0]> create "Add narration track" \
  --prompt "segment 42 needs a narration pass" \
  --require logger \                 # hard selector: only a worker advertising 'logger' can claim
  --exclude machine:flaky-box \      # hard anti-selector: that machine can NOT claim
  --affinity worktree=same \         # soft: bias toward the same worktree, never exclude
  --label media \
  --target-repo copilot-extensions \ # OPTIONAL: the cross-repo *code* target (stays in THIS lane)
  --dedup-key narration-seg42 \      # makes create idempotent
  --goal "segment 42 has a merged narration track" \  # durable objective (see Goal-loop tasks)
  --done-criteria "track rendered, reviewed, merged"  # when --goal is met
```

The **lane** (`--repo`) defaults to the calling repo -- omit it inside your
worktree. `--target-repo` is different: it's metadata naming the *code* a
cross-repo task touches; the task still lives in **your** lane and a same-lane
agent does the cross-repo work via `agent-worktrees:working-cross-repo`.

**Selectors — include (`--require`) and exclude (`--exclude`).** Both take
tokens over an open namespace; at claim time a worker's identity is folded into
its advertised set (`machine:<m>`, `worktree:<w>`, `repo:<lane>`) alongside its
capabilities, so a selector can target or exclude by **capability *or*
machine/worktree/repo**. A task is claimable only when every `--require` token is
present in the worker's set **and** no `--exclude` token is. Excludes are hard
anti-affinity (unlike soft `--affinity`, which only orders). A declining worker
can **append its own "not me"** on the way back to the queue with
`<agent-dispatch catalog argv[0]> yield <id> --exclude-self {worktree,machine}`
(or `--exclude <token>`);
because excludes only grow, the candidate set shrinks monotonically to a taker or
to unclaimable.

**Atomic create-and-claim (`--claim`).** `create … --claim` inserts the task
**already claimed by your worktree in one transaction** (no queued gap). Combined
with `--dedup-key`, it is the safe **open-ended self-dispatch** primitive: on a
collision the existing (already-claimed) row is returned and `claimed_by_me` is
`false`, so you can tell you lost the race and re-pick. See the **`pick-and-claim`**
skill for the full sweep-then-claim protocol and the `dedup_key` conventions.

Write the title + prompt to be **self-describing** (see the sweep note above):
a producer scanning existing tasks should be able to tell yours apart from
theirs from the description alone.

Create a **draft** instead with `--proposed` (unclaimable until `approve`).
Defer with `--not-before <epoch>` (scheduled creation). Attach a payload with
`--payload-inline` (small), `--payload-file <path>` (reads a file; a large one
spills to a content-addressed blob automatically), or `--payload-ref` (an
external pointer like `pr/123`).

### Goal-loop tasks — a durable goal a worker loops toward *(the norm for delegated work)*

A dispatched task is **not** a fire-once prompt — it is, by default, a **durable
goal an agent loops toward across turns and across embodiments.** Reach for
`--goal` + `--done-criteria` on any delegation that is an *objective* rather than
a single mechanical step; a plain one-shot prompt (no goal) is the exception, for
genuinely atomic work.

```bash
<agent-dispatch catalog argv[0]> create "Drive PR #128 to ready" \
  --prompt "review, address feedback, and land the auth-hardening PR" \
  --goal "PR #128 is approved and merged" \
  --done-criteria "review approved, CI green, merged to master"
```

- **`--goal`** is the durable objective; **`--done-criteria`** is the explicit
  test for *done*. Both are stored on the task row and both are optional — omit
  them for a one-shot task.
- A worker treats a goal-bearing task as something to **pursue in a loop**: do one
  unit of work → record a **progress beat**
  (`<agent-dispatch catalog argv[0]> progress <id> …`,
  which *appends* to the task's durable `progress_log`) → re-check the
  done-criteria → repeat until they are genuinely met. Dispatched **autopilot**
  bodies (`embody` / fleet seeds) run exactly this loop; the seed prompt spells it
  out (read the goal + prior `progress_log`, resume, loop, complete only on
  done-criteria).
- **Resume, don't restart.** Because the goal *and* its accumulated progress are
  durable, a worker that vanishes mid-goal is replaced by one that **resumes from
  the recorded `progress_log`** — the fabric loses only the *remainder* of the
  work, never the whole of it. This is why a progress beat is worth emitting at
  every real transition: it is the resume point.
- **Completion is self-judged but corroborated.** The worker completes the task
  **explicitly**, only once it judges the done-criteria met (**deferred
  completion**). For a goal-bearing task the coordinator corroborates the claim
  against a recorded result + progress consistent with the done-criteria before
  treating the goal as closed — a `complete` with no result and no progress toward
  a real goal is **held for attention**, not silently accepted. (A plain one-shot
  task keeps the simple deferred-completion path.)
- **What the layer does *not* do:** it makes the goal durable and resumable; it
  does **not** drive the worker's loop turn-by-turn (fire-and-forget, not driven).
  The supervisor re-embodies a *confirmed-gone* goal to resume it, and nudges an
  *alive-but-quiet* worker rather than yanking its goal away.

### Producers -- scheduled + reactive task creation

The coordinator only owns the queue; anything that *creates* tasks is a
**producer**. Two ship in-box, each driven by a declarative JSON spec:

- **`agent-dispatch schedule tick <spec>`** <!-- marketplace-isolation: allow scheduler-management -->
  (and
  `schedule serve <spec>
  --interval N`) -- a scheduler/timer producer. Each tick creates one task per
  due occurrence of every schedule (`interval_seconds`, or daily `at: ["HH:MM"]`
  times), stamping `not_before` and a deterministic `dedup_key`
  (`sched:<id>:<epoch>`) so re-ticks are idempotent. Drive `tick` from cron / a
  systemd timer / `manage_schedule`, or run the built-in `serve` loop.
- **`agent-dispatch webhook --config <cfg>`** <!-- marketplace-isolation: allow webhook-management -->
  -- a reactive producer: an HTTP app
  with `POST /webhook/pr` (a **merged** PR -> follow-up task, `source=pr-webhook`,
  `origin_ref=pr/<n>`, lane from the payload's repo remote) and
  `POST /webhook/telemetry` (a **firing** alert -> remediation task,
  `source=telemetry`). Deterministic `dedup_key`s make redelivery safe.
- **`<agent-dispatch catalog argv[0]> evaluate --spec <cfg>`** -- the *evaluator*: pipe one task
  lifecycle event (stdin or `--event-file`) through a declarative rule set that
  decides what happens next (emit a follow-up task, or nothing). Hook-like; the
  judgment half of emitters-and-evaluators. `--dry-run` prints decisions only.
  For the **service-driven** loop,
  `agent-dispatch supervise --evaluator <cfg>` <!-- marketplace-isolation: allow supervisor-management -->
  runs the same rules each cycle over newly-terminal tasks (advancing the loop
  with no bespoke module; idempotent via the emit's `dedup_key`).

See the plugin README (**Producers**) for the spec/config shapes.

### Recipes -- kick a built-in loop archetype ad-hoc

A **recipe** is a packaged *shape* of long-running work -- **reviewer** (drive a
PR to merged-or-abandoned), **conflict-resolution** (unstick a stalled change), or
**goal-driven** (drive an arbitrary goal through PRs). A recipe is directly
kickable: no standing service, emitter, or evaluator is required -- just a
coordinator + a worker body.

```bash
<agent-dispatch catalog argv[0]> recipes list                                   # available recipes + params
<agent-dispatch catalog argv[0]> recipes render reviewer --param repo=o/n --param pr=42   # inspect the fields
<agent-dispatch catalog argv[0]> recipes kick reviewer --param repo=o/n --param pr=42 --repo o/n --spawn
```

`kick` reuses `create` (lane resolution, dedup, `--spawn`/`--spawn-backend`;
default `embody` so the worker gets a full checkout). A reserved-work `dedup_key`
is derived from the recipe + params, so re-kicking the same target collides rather
than forking. Use `--dry-run` to preview the create call. See the plugin README
(**Recipes**) and `visions/plugins/agent-dispatch` (§*The recipe*).

**Route a kicked loop onto a supervisor pool with `--label`.** `kick` accepts a
repeatable `--label` (merged after, and de-duplicated with, the recipe's own
labels). This is how you feed a **standing, label-gated supervisor pool** instead
of spawning a one-off body: enqueue the kicked task under the pool's opt-in label
and let a pool slot claim it -- no `--spawn` needed.

```bash
# a persistent pool watches one label, e.g. AGENT_DISPATCH_SUPERVISE_LABELS=general
<agent-dispatch catalog argv[0]> recipes kick goal-driven \
  --param goal="fix the flaky retry in the uploader" --param repos=o/n \
  --label general        # a 'general'-pool slot claims + drives it to a PR
```

Because a label-gated supervisor claims **only** tasks carrying its label, a pool
scoped to a dedicated label (`general`) is a clean **positive opt-in**: it never
picks up system tasks that carry their own labels (reviews, scheduled sweeps).

**Driving the loop** --
`<agent-dispatch catalog argv[0]> recipes drive <name> --signal <s>` maps a
recipe + what-just-happened to the next action: **work** (start / a `suspend_on`
event), **suspend** (`work-done`/`idle` -> hibernate the wait), or **resolve**
(`merged`/`abandoned` -> drive-to-resolution). `--execute` runs the suspend leg
(`--resume <wt> -- <wait-cmd>`, spawns the detached waiter) and the resolve leg
(`--base <b>`, runs the unwind); **work** stays the agent's to perform. This is
the seam that composes recipes + `run` + `resolve` into an executable loop.

### Resolve -- drive your worktree to a clean state when a loop ends

When a loop finishes, drive the worktree to its resolved final state -- landing
verifies clean; abandoning **unwinds to base** and reconciles the source. Run it
on your **own** worktree:

```bash
<agent-dispatch catalog argv[0]> resolve --outcome landed                                  # verify clean
<agent-dispatch catalog argv[0]> resolve --outcome abandoned --base main --source o/n#42   # preview the unwind
<agent-dispatch catalog argv[0]> resolve --outcome abandoned --base main --execute         # perform it (destructive)
```

Planning is pure and prints by default; `--execute` performs the (destructive)
unwind and a failed reset stops rather than pressing on.
`<agent-dispatch catalog argv[0]> abandon --resolve` surfaces the same plan
alongside the abandon. See the plugin README
(**Drive the worktree to resolution**) and `visions/plugins/agent-dispatch`
(§*drive-the-worktree-to-resolution*).

### Hibernate the wait -- hand a blocking wait to the layer

When a loop can only wait on a slow external condition, don't sit on a live
session. Hand the wait to `run`: it executes the blocking command and, when it
resolves, resumes the worktree-affinitied worker via an agent-bridge nudge.

```bash
<agent-dispatch catalog argv[0]> run --resume <machine/worktree> --task <id> -- agent-worktrees pr-watch 42 # marketplace-isolation: allow agent-worktrees-management
<agent-dispatch catalog argv[0]> run --detach --resume <machine/worktree> -- agent-worktrees pr-watch 42 # marketplace-isolation: allow agent-worktrees-management
```

Everything after `--` is the wait command. `--detach` runs it as a fully detached,
cheap OS-level waiter (no agent, no tokens) so the expensive worker session can be
torn down while it waits, then re-woken with its context intact. See the plugin
README (**Hibernate the wait**) and `visions/plugins/agent-dispatch`
(§*hibernate-the-wait*).

### 3. Claim, work, finish

```bash
<agent-dispatch catalog argv[0]> claim --capability logger     # atomically leases one eligible task
# note the returned task id + owner, then:
<agent-dispatch catalog argv[0]> start    <id> <owner>
<agent-dispatch catalog argv[0]> heartbeat <id> <owner>        # optional: refresh the last-seen beat
<agent-dispatch catalog argv[0]> complete <id> <owner> --result-ref pr/123
```

> **Owner is optional on `claim`/`start`/`complete`/`yield`/`progress`.** Omit it
> and the coordinator resolves your **worktree identity** (`<machine>/<worktree>`)
> from the CWD -- so an embodied/taken-over worker can drive its whole lifecycle
> (`<agent-dispatch catalog argv[0]> claim --task <id>` → `start <id>` →
> `complete <id>`) without
> ever typing an owner. This keeps the task's owner equal to its worktree, which
> is what lets live-session tracking join a CLI-embodied task to its session (the
> `embodiment` overlay above).

Report progress toward the goal so callers/operator watch the fleet at a glance
(this also refreshes the last-seen beat):

```bash
<agent-dispatch catalog argv[0]> progress <id> --phase implementing --summary "wired the verb; tests green"
<agent-dispatch catalog argv[0]> progress <id> --phase "PR open" --summary "opened the PR" --pr pr/2601
<agent-dispatch catalog argv[0]> progress <id> --summary "stuck on a flaky test" --blocker "CI timeout"
```

Set **this worktree's current focus** (for an operator or task-less worktree —
the cockpit shows what each worktree is working on). Post it when you *start
substantial work* and *change direction*, never on a timer:

```bash
<agent-dispatch catalog argv[0]> focus "driving live-session-messaging Phase 8 (multi-machine dispatch)"
<agent-dispatch catalog argv[0]> focus            # show this worktree's current focus
<agent-dispatch catalog argv[0]> focus --list     # every worktree's focus (this machine)
```

> `focus` resolves `machine/worktree` from the CWD (no id to type). It **is**
> the worktree record's status-core summary: a focus write forwards through the
> `agent-worktrees status` verb, and `--list` / show *derive* from <!-- marketplace-isolation: allow agent-worktrees-management -->
> `agent-worktrees list --json` — there is no separate focus store <!-- marketplace-isolation: allow agent-worktrees-management -->
> (single-owning-layer / derive-don't-duplicate). It is the operator/task-less
> analogue of a dispatched worker's `progress` (which is keyed to a task).
> The concise posting cadence is injected at session start when the repository
> opts into `.agent-dispatch/session-guidance.json` →
> `session_guidance.focus`; this
> section retains the detailed CLI mechanics.

> **`progress` is a *status beat*, not a chat log.** Emit **one** short line only
> at real transitions -- a plan settled, implementation done, a PR opened, a
> blocker hit -- never on a timer. The `--summary` is hard-capped and stored
> **latest-only** as `latest_progress` (a structured object surfaced by
> `show`/`list`/`inbox`) **and appended to the task's durable `progress_log`** --
> so a reader sees *how far toward the goal* without a transcript, **and a
> replacement worker can resume a goal from the recorded beats** rather than
> restarting it. Identity auto-resolves from CWD like the other lease verbs.

Recoverable snag -> return it for a later cycle (keep the note!):

```bash
<agent-dispatch catalog argv[0]> yield <id> <owner> --note "blocked on merge conflict; retry next cycle"
```

Discard a duplicate / dropped task (needs permission):

```bash
# a duplicate is self-justifying -- --duplicate-of implies permission and
# records the dedup reference in the audit trail (never a silent drop):
<agent-dispatch catalog argv[0]> abandon <id> --duplicate-of pr/123     # or task-id / issue ref
# any other discard still asserts permission explicitly:
<agent-dispatch catalog argv[0]> abandon <id> --worker-id <owner> --permit --reason "dropped priority"
```

### Evaluate before committing (the contract-net window)

`claim` and `start` are **two steps on purpose** — between them is an
**evaluation window** where you hold the task exclusively but haven't committed
to running it. `claim --evaluation` marks the window as an *evaluation* (a
worker signals "assessing, not yet committed"). The row carries lease timestamps
for heartbeats/observability, but recovery is **not** driven by elapsed time:
an evaluator is reclaimed only if its worker is confirmed gone (liveness GC), and
`start` commits the task to `started` while capturing the owner session id when
available.

```bash
<agent-dispatch catalog argv[0]> claim --task <id> --evaluation    # win a short exclusive eval window
# ...assess: dup-check (agent-dispatch list / sweep), feasibility, is-this-for-me...
<agent-dispatch catalog argv[0]> start   <id>                      # ACCEPT -> commit to doing the work
<agent-dispatch catalog argv[0]> yield   <id> --exclude-self worktree --note "not my capability"  # DECLINE
<agent-dispatch catalog argv[0]> abandon <id> --duplicate-of <ref>                                # DUPLICATE
```

Three ways out of the window: **accept** (`start`), **decline** (`yield
--exclude-self` — returns it and appends a scoped "not me" so you aren't
re-offered it), or **retire** (`abandon --duplicate-of` for a duplicate/obsolete
task). Dispatched **autopilot** workers (`embody`/fleet seeds) run exactly this
loop. Default the decline scope to the **narrowest** true one (`worktree`); widen
to `machine` only when the mismatch is machine-wide.

### Inspect

```bash
<agent-dispatch catalog argv[0]> show    <id>       # full task record
<agent-dispatch catalog argv[0]> events  <id>       # append-only audit trail of every transition
<agent-dispatch catalog argv[0]> payload <id>       # resolved payload (inline or blob); --raw prints content only
<agent-dispatch catalog argv[0]> consume <id>       # resume-and-consume: drive to completed (idempotent) + print payload
<agent-dispatch catalog argv[0]> consume <id> --defer-complete  # TAKEOVER pickup: approve->claim->start + print brief, NO complete
<agent-dispatch catalog argv[0]> watch              # stream task.* events (SSE) as JSON lines
```

> **`show`/`list` overlay live-session status for a CLI-embodied task.** A
> leased task's owner is `<machine>/<worktree>`, and agent-bridge's live-session
> registry is keyed by that worktree. So `show`/`list` join the two and add an
> `embodiment` block (`driven_by`, `status`, `turn_state`/`liveness`, heartbeat
> `updated_at`) for a leased task whose worktree is currently live — a
> CLI-embodied task is trackable like a headless one, including whether it is
> mid-turn (`active`), done (`idle`), or silently `stalled`. **Cross-machine
> (Phase 8 8b):** the overlay resolves against the *owner's* machine — a task
> whose owner is a remote machine (an SSH-pushed dispatch) resolves its live
> session on that machine over the SSH mesh
> (`ssh <machine> agent-bridge …`), <!-- marketplace-isolation: allow remote-management -->
> so
> a remote-dispatched task is trackable from the originator like a local one.
> Best-effort and read-only: with no `agent-bridge`/`ssh` on PATH (or no live
> session) the overlay is simply omitted and output is unchanged.

> **`consume` is the handoff-pickup shortcut -- in two flavors.**
>
> - **Baton (default `consume <id>`):** rolls the whole
>   approve → claim → start → complete lifecycle into one idempotent call and
>   then prints the payload, so a successor's *single* command loads the brief
>   **and** marks the baton spent -- a handoff is completed the moment it is
>   picked up. The continuation *work* is tracked by its effort/issue, not this
>   task. Use for a **human in-place resume** (`/resume-handoff`, a pasted seed).
> - **Deferred (`consume <id> --defer-complete`):** approve → claim → **start**
>   (take ownership, mark in-progress) + print the brief, but do **not**
>   complete. This is the **takeover** pickup for a *dispatched / embodied
>   successor*: it loads the brief, works the task, and runs
>   `<agent-dispatch catalog argv[0]> complete <id>` **explicitly** only when
>   it judges the goal reached -- so
>   `completed` means *the work is done*, not *the baton was handed over*.
>
> An already-terminal (or unclaimable) task just has its payload re-printed,
> never an error. Use plain `payload --raw` to read *without* any state change.
>
> **`complete <id>` needs no owner** when run inside the owning worktree: it
> resolves `machine/worktree` from the CWD (like `claim`), so a taken-over
> successor finishes with one clean command once the goal is met.

## Routing: `requires` (hard) vs `affinity` (soft)

- **`requires`** (repeatable `--require`) -- capability tokens (`logger`,
  `review`, `merge`) or identity tokens (`machine:<m>`, `worktree:<w>`,
  `repo:<lane>`, `agent:review-bot`). A task is claimable only when `requires`
  is a **subset** of the worker's advertised token set. Two machines advertising
  the same capability give
  **cooperative, redundant** coverage: first writer wins; if one worker goes
  away, a liveness GC pass requeues its task and the other reclaims it -- no
  leader election.
- **`affinity`** (repeatable `--affinity key=value`) -- soft *preferences*
  (preferred agent/worktree) that order candidates but **never exclude**.
- A **hard pin** is just a target promoted into `requires`; `detach <id>` demotes
  a hard worktree pin to a soft affinity (e.g. once local work is pushed, a bound
  handoff becomes portable).

## Spawning a worker: headless (bridge) vs CLI-backed autopilot (embody)

`create --spawn` spawns a worker that claims and executes the task by id. A
**spawn backend** chooses *how* the worker is embodied:

```bash
# Headless agent-bridge ACP worker (default backend)
<agent-dispatch catalog argv[0]> create "Summarize the PR" --require review --spawn              # managed (waits)
<agent-dispatch catalog argv[0]> create "Summarize the PR" --spawn --spawn-agent task-worker --async  # fire-and-forget

# CLI-backed AUTOPILOT session -- "dispatch an agent to do X"
<agent-dispatch catalog argv[0]> create "Refactor the auth module" \
  --prompt "extract JWT handling into src/auth/ …" \
  --spawn --spawn-backend embody
```

- **`--spawn-backend bridge`** (default) -- a **headless** agent-bridge ACP
  worker. Ephemeral; torn down with its caller.
- **`--spawn-backend embody`** -- a **durable, CLI-backed autopilot** session in
  a **fresh parallel worktree on the same machine**, via `agent-worktrees embody
  --new` <!-- marketplace-isolation: allow agent-worktrees-management -->
  (tools auto-approved with `--allow-all-tools`, stamped `--driver
  agent-dispatch` so it's viewable in Neuron Forge with a "driven by" banner).
  This is the **"dispatch an agent to do X"** path: the embodied session claims →
  starts → works the task autonomously → and **completes it explicitly** only
  when it judges the goal reached (**deferred completion** -- the task's
  `completed` state means the *work is done*, not that a baton was handed over).

**Same body choice in the supervisor, keyed by label.** A persistent
`agent-dispatch supervise` loop <!-- marketplace-isolation: allow supervisor-management -->
embodies via the **CLI autopilot** (`embody`) by
default, but `--headless-label L` (repeatable; `--headless-agent` names the
bridge agent) routes tasks carrying label `L` to the **headless bridge** body
instead -- for **self-contained sweeps** that need no human attach (and whose
seeded CLI session could otherwise race the input caret and never start). Only
listed labels go headless; the rest stay CLI-first. Service knobs:
`AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS` / `_HEADLESS_AGENT` in
`supervisor.env`. See the design doc's "Per-label embody body" section.

**Fleet mode (`--pool`) is fleet-wide, not per-label.** When the supervisor fans
bodies out across a **pool of remote hosts** (`--pool a,b [--origin <alias>]`), the
body choice is a single fleet-wide switch: default CLI/mux embody on the pool
host, or `--headless` (with `--headless-agent`) to embody **every** fleet body as
a headless agent-bridge ACP session there instead -- the reliable remote
embodiment (a kicked CLI/mux body can hit the "Loading…" startup-seed hang and
never claim). `--headless-label` is ignored in fleet mode. See the design doc's
"Headless-fleet body" section.

**Persistent supervisor profiles.** The installer manages a primary supervisor
from `~/.agent-dispatch/supervisor.env` plus named profiles in
`~/.agent-dispatch/supervisors/<name>.env` (safe names: letters, digits, `_`,
`-`). Each profile uses the same `AGENT_DISPATCH_SUPERVISE_*` schema and becomes
its own `agent-dispatch-supervisor-<name>` unit/task; `status`/`start`/`stop` and
`uninstall` iterate them, deleted env files remove orphaned profiles, and
`--no-supervisor` / client-only installs remove all supervisors. To run a fleet
headless supervisor persistently, put the watched labels in
`AGENT_DISPATCH_SUPERVISE_LABELS`, set any bridge agent with
`AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT`, and put
`--pool a,b --origin <alias> --headless` in
`AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS`. See `docs/spawn-supervisor.md` for depth.

> **Cross-machine dispatch (Phase 8, SSH-push).** Add `--target-machine <Y>` to an
> `embody` spawn to dispatch **on another machine**:
> `<agent-dispatch catalog argv[0]> create <task> --target-machine
> emancipation-cube --spawn --spawn-backend embody`. Because
> agent-dispatch is per-host (each machine owns a loopback coordinator + local
> embody), the whole create+embody is run **on Y** over the SSH mesh (Y's
> name is its SSH alias -- never a raw IP); the task lives on Y's coordinator and
> the autopilot session runs + completes there. Observe it with
> `ssh Y agent-dispatch show <id>`, <!-- marketplace-isolation: allow remote-management -->
> or — since **8b** — directly from the originator:
> `show`/`list` resolve a remote-owned task's `embodiment` overlay on the owner's
> machine over the mesh (turn-state/liveness included). Degrades cleanly: no
> `ssh` / unreachable host → nothing is queued, with a clear message.

> **Peer-queue browse (Phase 8 8c).** Add `--machine <Y>` to `list` or `inbox`
> to read **Y's own queue** over the SSH mesh instead of the local coordinator:
> `<agent-dispatch catalog argv[0]> list --machine emancipation-cube --status started` /
> `<agent-dispatch catalog argv[0]> inbox --machine emancipation-cube`. When `Y`
> is a remote peer, the read command is run **on Y**
> (`ssh Y agent-dispatch …`), <!-- marketplace-isolation: allow remote-management -->
> so it reads Y's loopback
> coordinator and — via 8b — enriches against Y's own bridge; the JSON streams
> straight back. `list` forwards the locally-resolved repo lane (+ any
> `--status`/`--label`/`--limit`/`--target-*` filters); the `--machine` selector
> itself is never re-forwarded, so there is no second hop. `Y` = local (or unset)
> keeps the existing local behavior. Degrades cleanly: no `ssh` / unreachable
> host → a clear message and non-zero exit.

Both backends **degrade gracefully**: if the chosen mechanism isn't on PATH
(no `agent-worktrees` for `embody`, no `agent-bridge` for `bridge`) the embody
backend falls back to bridge, and if neither is present the task is simply left
queued for any worker to claim. agent-dispatch stays fully usable standalone.

## MCP tools instead of the CLI

`<agent-dispatch catalog argv[0]> mcp` runs a local **stdio MCP server** exposing the same
operations as tools (`dispatch_create`, `dispatch_find`, `dispatch_sweep`,
`dispatch_claim`, `dispatch_start`, `dispatch_complete`, `dispatch_payload`,
`dispatch_worktree_status`, ...). It resolves your `machine`/`worktree` identity
from the working directory just like the CLI, so `dispatch_claim` /
`dispatch_worktree_status` are auto-scoped with no arguments. Point a sub-agent's
`.mcp.json` at
`{"command": "agent-dispatch", "args": ["mcp"]}` <!-- marketplace-isolation: allow mcp-server-startup -->
(needs the `mcp` extra). The coordinator also hosts the **same tools over HTTP at `/mcp`** for
remote clients that supply identity via `X-Agent-Machine`/`X-Agent-Worktree`
headers. The CLI and MCP tools are interchangeable — use whichever fits.

## Config quick reference

| Env var | Role |
|---------|------|
| `AGENT_DISPATCH_URL` | coordinator base URL the CLI talks to (point at a remote host) |
| `AGENT_DISPATCH_TOKEN` | bearer token (client sends, server validates) |
| `AGENT_DISPATCH_SHARED_URL` | shared/elected coordinator endpoint for cross-machine dispatch (the hosted coordinator); used only with `--shared` |
| `AGENT_DISPATCH_SHARED_TOKEN` | bearer for the shared coordinator (independent of `AGENT_DISPATCH_TOKEN`) |
| `AGENT_DISPATCH_HOST` / `AGENT_DISPATCH_PORT` | where the coordinator binds (server side) |
| `AGENT_DISPATCH_DB` | SQLite queue file (server side) |
| `AGENT_DISPATCH_GC_INTERVAL` | liveness garbage-collection cadence in seconds (server side; `0` disables). `AGENT_DISPATCH_SWEEP_INTERVAL` is a deprecated alias. |

All CLI output is JSON on stdout, so verbs compose with `jq` and other tooling.
Global flags `--url` / `--token` override the env per-invocation; `--shared`
routes the command at the shared/elected coordinator instead of the local one.

## Gotchas

- **Most reads are lane-scoped.** By default `list`/`find`/`sweep` show **your
  repo's** tasks. An "empty" sweep or "no claimable task" may just mean *your
  lane* is empty. Use `--repo <name>` to look at a specific other lane; use
  `inbox` for the intentional machine-scoped cross-lane picker view.
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
