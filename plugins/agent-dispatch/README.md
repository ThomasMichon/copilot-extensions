# agent-dispatch

A **portable agent task-queue + per-host coordinator**. It lets multiple
Copilot CLI agents (worktree sessions, bridged sub-agents, scheduled/reactive
producers) coordinate work through a single, low-latency authority -- instead of
racing each other through `origin/master` pushes or needing a dedicated user
account per agent.

> **Status: installable runtime.** This ships the queue **engine**
> (`agent_dispatch.queue`), the per-host **coordinator daemon**
> (`agent-dispatch serve`), the **`agent-dispatch` CLI**, **local MCP tools**
> (`agent-dispatch mcp`), a **lifecycle installer** (marketplace-registered;
> `scripts/install.sh` / `scripts/install.ps1` --
> `stamp|provision|install|update|status|start|stop|uninstall` -- stamp a
> self-provisioning binstub, then deploy a versioned venv + binstub + deploy
> manifest), an **SSE event stream** (`GET /events` /
> `agent-dispatch watch`), **agent-bridge spawn** (`create --spawn`), and a
> label-gated **embody supervisor** that can run locally or fan bodies out to a
> remote host pool (`supervise --pool`, with `--headless` for headless
> agent-bridge ACP fleet bodies). On its
> deploy machines the coordinator installs by default and auto-starts as a
> service on both platforms: a **systemd user unit** (Linux) and a **Windows
> Scheduled Task**.

## Install

`agent-dispatch` is a standalone runtime plugin. Enabling the plugin installs the
payload; the session-start hook can **stamp** a binstub into `~/.local/bin` so
the first `agent-dispatch` invocation self-provisions the runtime. Repos that use
agent-worktrees may also machine-gate the runtime and reconcile it automatically,
but that is composition, not a requirement. To install/manage it directly:

```bash
# via the marketplace (once published):
copilot plugin install agent-dispatch@copilot-extensions
# then deploy the runtime (venv + binstub + coordinator service + picker pivot):
bash "$(copilot plugin path agent-dispatch)/scripts/install.sh" install    # Linux/WSL/macOS
# Windows:  pwsh -File <plugin>\scripts\install.ps1 -Action install
```

Agent-facing sessions receive an exact payload-local command through the
session command catalog. That command resolves and, when necessary, provisions
the runtime from its own payload without searching `PATH` for another
marketplace's same-named plugin. The existing `provision` lifecycle remains a
full first-use installation, including the legacy compatibility wrappers and
eligible local services; this adoption changes command selection, not that
installer behavior.

The legacy global wrappers remain explicit compatibility and management
boundaries for callers that do not inherit session catalogs: coordinator and
supervisor services, scheduler/webhook launchers, picker pivots, remote SSH
commands, startup-generated handoff and worker seeds, and committed static MCP launch
configuration. Catalog adoption does not make those callers payload-aware; they
retain their current commands until an attributable launcher contract reaches
each surface.

### Opt-in worktree focus guidance

Repositories can opt into a concise `sessionStart` context kernel that asks an
agent to record substantial operator-led or task-less work before another agent
chooses overlapping work. The project-owned configuration is
`.agent-dispatch/session-guidance.json` at the Git root:

```json
{
  "session_guidance": {
    "focus": true
  }
}
```

`session_guidance.focus` is repository-owned because collision posture is a
project choice. This exact object is the complete schema: unknown keys and
values other than the literal JSON boolean `true` disable the guidance. Missing,
malformed, oversized, non-UTF-8, NUL-containing, symlink/reparse-point, or
out-of-root configuration also fails open. The hook reads the authoritative
`cwd` from a bounded `sessionStart` payload, resolves its Git root in an isolated
Git environment, and emits only when `agent-worktrees` identifies a managed
project and exposes its status core. If agent-worktrees is absent, the plugin
remains fully standalone and the hook emits `{}`.

The guidance asks agents to check `agent-dispatch worktree-status` before
starting new work, resume or claim tasks explicitly assigned to their worktree,
then check `agent-dispatch focus --list` before choosing likely-overlapping
work and advertise their own focus early.
`agent-dispatch focus` is shorthand for writing the same agent-worktrees
status-core summary; agent-worktrees conduct and regular
`agent-worktrees status --summary` remain authoritative for ongoing disposition
and retain their normal cadence. Agent-dispatch maintains no parallel store.

When an adopting repository enables this opt-in, remove any superseded
hand-written **Worktree Focus** prose from its instructions. This coordination
hint is not a safety policy, so it needs no static fallback on launch paths that
do not load hooks, and adopters should not create a duplicate marked block.

`scripts/install.{sh,ps1}` is a lifecycle manager --
`stamp | provision | install | update | status | start | stop | uninstall`
(`init.{sh,ps1}` is a thin alias for `install`). `stamp` only writes the
self-provisioning binstub + payload marker; `provision`/`install`/`update` build
a versioned runtime under `~/.agent-dispatch/versions/<v>/` (published by the
`current-version` marker), an `agent-dispatch` binstub in `~/.local/bin`, a
deploy manifest, the **"Tasks" picker pivot** (see below), and -- unless
`--no-service` (`-NoService`) -- the coordinator service (a per-host local
coordinator, matching agent-bridge).
`update` is downgrade-guarded (a stale checkout won't silently roll back a newer
deployed runtime; override with `--force`).

The installer also manages optional, label-gated **embody supervisor** services.
The primary supervisor reads `~/.agent-dispatch/supervisor.env` and installs as
`agent-dispatch-supervisor.service` on Linux or Scheduled Task
`agent-dispatch-supervisor` on Windows. Add named profiles under
`~/.agent-dispatch/supervisors/<name>.env` (safe names: letters, digits, `_`,
`-`) to get independent
`agent-dispatch-supervisor-<name>` units/tasks with the same env schema; deleted
profile env files are reconciled by removing their orphaned unit/task, and
`--no-service` / `--no-supervisor` removes all supervisors. Put advanced flags
such as `--pool host-a,host-b --origin origin --headless` in
`AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS`. See
[`docs/spawn-supervisor.md`](docs/spawn-supervisor.md#running-as-a-persistent-service-the-always-on-last-mile)
for the full supervisor/profile contract.

### Worktree-picker "Tasks" pivot

The installer drops a pivot manifest at
`~/.agent-worktrees/pivots/agent-dispatch.json` so the agent-worktrees Textual
picker grows a **Tasks** pivot (between Worktrees and Maintenance). It renders the
status-grouped board through the stdlib-only `agent-dispatch-board` API client
(Blocked / Proposed / Queued / Started / Suspended / recently terminal). A separate **ACTIVE** badge appears only
when embodiment tracking reports an assigned agent executing a turn; **STALLED**
marks a running turn with no recent activity. That execution badge is independent
from lifecycle phase -- `Started` alone never implies a live agent. The
background supervisor publishes this observation into coordinator-owned task
state; the board client is a sub-second coordinator API read and expires
observations older than 90 seconds, so the Picker never shells to
agent-worktrees or agent-bridge. Enter
opens a per-task action sub-menu. The seam is a filesystem manifest registry,
not a Python import -- the plugins live in separate venvs -- so a stale or absent
picker simply ignores it. Source: `pivots/agent-dispatch.json`.

### Running the coordinator as a service

On the host that *is* a coordinator, the service is installed by default
(`install`/`update` above). To manage it explicitly, or install only the client
on a machine that points at a remote coordinator:

```bash
# Linux/WSL -- a systemd user unit (installed by default; --no-service to skip):
bash "$(copilot plugin path agent-dispatch)/scripts/install.sh" status   # or start | stop
systemctl --user status agent-dispatch          # manage it directly
# edit ~/.agent-dispatch/service.env (host/port/token), then: systemctl --user restart agent-dispatch
```

```powershell
# Windows -- a Scheduled Task (starts at logon; installed by default):
pwsh -File <plugin>\scripts\install.ps1 -Action status   # or start | stop
Get-ScheduledTask -TaskName agent-dispatch | Get-ScheduledTaskInfo   # manage it
# edit %USERPROFILE%\.agent-dispatch\service.env, then: Start-ScheduledTask -TaskName agent-dispatch
```

Both read an editable `service.env` (host/port/db/token) beside the runtime. A
client-only machine installs with `--no-service` (`-NoService`) and points
`AGENT_DISPATCH_URL` at the coordinator host.

The service uses the local-endpoint-discovery pattern: by default `serve` binds
loopback on an OS-assigned port, writes `~/.agent-dispatch/run/endpoint.json`,
and publishes the active generation in the zdd routing table for graceful
cutover. Set `AGENT_DISPATCH_PORT` only when you deliberately need a fixed
legacy port; clients resolve `AGENT_DISPATCH_URL` first, then the routing table /
rendezvous file, then the legacy `127.0.0.1:9847` fallback.

## Why

A queue needs an **atomic leased claim** to be a correct coordinator: two agents
must never both "win" the same task, and a crashed agent must not hold work
forever. Git and issue trackers give neither cheaply. `agent-dispatch` provides
that primitive as a single-writer SQLite (WAL) queue, reachable over HTTP --
loopback on a lone dev box, one designated host in a multi-machine system. Same code, one
config switch.

## The engine (`agent_dispatch.queue`)

```python
from agent_dispatch import TaskQueue

q = TaskQueue("~/.agent-dispatch/tasks.db")

# Producer: enqueue a task (or propose a draft that isn't claimable yet)
t = q.create("Add narration track", prompt="segment 42", requires=["logger"])

# ...or a durable GOAL a worker loops toward and resumes rather than restarts:
g = q.create("Drive PR #128 to ready", goal="PR #128 is approved and merged",
             done_criteria="review approved, CI green, merged")

# Consumer: a worker advertises capabilities and atomically leases one task
task = q.claim_one("worker-1", capabilities=["logger"])
if task:
    q.start(task.id, "worker-1")
    # ... do the work ...
    q.complete(task.id, "worker-1", result_ref="pr/123")

# Crash recovery: return any task whose owner is confirmed gone to the queue
q.reconcile_liveness()
```

### State model

```
proposed -> queued -> claimed -> started -> completed        (terminal)
                ^         |          |
                +-- decline/yield ---+
                ^
                +-- owner-gone (liveness GC requeue, attempts++)
started -> suspended -> started                              (resume; same owner)
               |
               +-----------> queued                          (release; replacement)
               +-----------> completed                       (condition resolved)
   (any non-terminal) --------------------------> abandoned   (terminal, permission-gated)
```

- **proposed** -- written but not yet claimable (a draft handoff / undecided idea).
- **queued** -- claimable.
- **claimed** -- held by a worker (may evaluate before committing).
- **started** -- under active implementation.
- **suspended** -- previously started but dormant and non-claimable; retains the
  same owner/session, worktree identity, generation, progress, and card while
  clearing active lease/activity.
- **completed** / **abandoned** / **dead_letter** -- terminal (abandon requires
  permission; **dead_letter** is where a task lands when GC has requeued it past
  the attempts cap -- its owner kept going gone -- an actionable failure state).
- A **liveness** GC pass returns a held task to **queued** only when its owner's
  **session** is *confirmed gone* (keyed on the captured `owner_session_id`, not
  mere worktree occupancy) -- never on elapsed time, so a long-running live
  worker is never disturbed and a bridge blip (verdict *unknown*) leaves it
  alone. The requeue is **fenced** on (owner_session_id, generation) so a
  reused worktree or a resuming stale worker can't corrupt recovery. The
  coordinator runs GC automatically every `AGENT_DISPATCH_GC_INTERVAL` seconds
  (default 60; `0` disables); `recover` forces a pass on demand.
- `suspend <id> --reason <why>` is owner-gated and moves **started → suspended**.
  For an interactive owner, `resume <id>` restores **suspended → started** under
  the same owner and atomically enqueues its durable wake. A supervised owner
  without a captured interactive inbox instead releases to **queued** and
  settles its reservation for safe re-embodiment. `release <id>`
  explicitly clears ownership and moves **suspended → queued**. Suspended tasks
  do not participate in liveness GC, supervisor capacity/claiming, or retry
  accounting.
- `complete <id> <owner>` is also legal directly from **suspended**. An external
  resolver may therefore record a satisfied dormant goal atomically under the
  preserved owner without waking a process or fabricating an active turn.
- A steer submitted while suspended is stored and either resumes an interactive
  task to   **started** with a durable wake, or releases a task without a captured inbox to
  **queued** for re-embodiment, in the same transaction. A replacement body
  consumes pending steering immediately after it starts. The HTTP request never
  calls the bridge. The coordinator service drains interactive wakes
  asynchronously, retries with exponential backoff across restarts, and uses
  the stable wake id as the bridge idempotency key. Task
  generation, owner/session identity, lifecycle status, and latest-wake identity
  fence stale delivery after a requeue, completion, or newer wake. Delivery is
  additionally fenced by the bridge against the exact captured session. Only
  the active coordinator route claims wakes; short delivery leases let its
  promoted successor recover an interrupted claim without racing a live
  predecessor. Wakes are edge-triggered: the resumed/replacement worker runs
  `steer take <id> --all` to atomically drain every pending answer. Suspension
  is refused while an untaken steer exists, closing the response-before-park
  race. The response
  reports `steer_wake_status: pending`; `wakes <id>`, `/tasks/{id}/wakes`, task
  events, `task.wake` SSE events, and `/health` wake metrics expose
  pending/delivering/delivered/failed/stale state. The steer is durable even
  when all delivery attempts fail.

### Goal-bearing tasks -- a durable goal, not a fire-once prompt

A task may carry a durable **`goal`** (objective) + **`done_criteria`** (the test
for *done*) plus an append-only **`progress_log`**. A worker treats it as
something to **loop toward**: work a unit -> append a progress beat -> re-check
the done-criteria -> repeat, completing only once it judges them met (**deferred,
self-judged completion**, corroborated against a recorded result + progress for a
goal-bearing task). Because the goal *and* its accumulated progress are durable,
a worker confirmed gone mid-goal is replaced by one that **resumes from the
recorded `progress_log`** rather than restarting -- the fabric loses only the
*remainder*. Both fields are nullable: omit them and it is a plain one-shot task.
The supervisor re-embodies a confirmed-gone goal to resume it, and nudges an
alive-but-quiet worker rather than yanking its goal. See the **`agent-dispatch`**
skill § *Goal-loop tasks* and the vision leaf (*resumable-goal*,
*resume-the-goal-not-restart-it*) for the full contract.

### Routing: `requires` / `excludes` (hard) vs `affinity` (soft)

- **`requires`** -- a set of capability or identity tokens (e.g. `logger`,
  `review`, `machine:<m>`, `worktree:<w>`, `repo:<lane>`). A task is claimable
  only when `requires` is a subset of the worker's advertised token set. This is
  how the same capability on two machines gives **cooperative, redundant**
  coverage: first writer wins; when a worker goes away, a liveness GC pass
  requeues its task and the other reclaims it.
- **`excludes`** -- hard anti-affinity tokens. At claim time the worker's
  capability set is augmented with identity tokens (`machine:<m>`,
  `worktree:<w>`, `repo:<lane>`); any matching exclude makes that worker
  ineligible.
- **`affinity`** -- soft preferences (preferred agent/worktree) that order
  candidates but never exclude.

### Payloads (inline + content-addressed blobs)

A task carries a Markdown `payload` (the graduated handoff's asset). Small
payloads live **inline** in the row; a payload over `blob_threshold` bytes
(default 4 KiB) is spilled to a **content-addressed blob** under
`~/.agent-dispatch/payloads/<sha256>.md`, and the row keeps only a `blob:<hash>`
ref -- so `list`/`find` stay lean and identical payloads dedupe to one file (no
external deps). `read_payload()` (engine) / `GET /tasks/{id}/payload` /
`agent-dispatch payload <id> [--raw]` resolve either form transparently; an
external `payload_ref` (e.g. `pr/123`) is left opaque for the caller.
`agent-dispatch consume <id>` is the resume-and-consume shortcut: it idempotently
drives the task to `completed` (approve → claim → start → complete) and then
prints the payload, so a handoff successor's single command both loads the brief
and spends the baton.

### Dedup & scheduling

- `dedup_key` (unique) makes `create` idempotent -- a duplicate returns the
  existing task, so agents can browse/`find` before ideating.
- `not_before` defers a task until a wall-clock time (scheduled creation).

## Producers

The coordinator core only owns the queue -- it runs **no** scheduler and **no**
PR/alert logic. Anything that *creates* tasks is a **producer**: any client that
can POST. Two ship in-box (both driven by a declarative JSON spec, both talking
to the coordinator through the ordinary client):

### Scheduler / timer producer (`agent-dispatch schedule`)

Turns recurring task templates into deferred tasks. Each *tick* creates one task
per due occurrence, with `not_before` set to the occurrence time and a
deterministic `dedup_key` (`sched:<id>:<epoch>`) so re-ticks never double-create.

```jsonc
// schedules.json
{
  "default_repo": "example.com/acme/widget",   // lane fallback
  "schedules": [
    { "id": "hourly-sweep", "title": "Sweep service health", "interval_seconds": 3600 },
    { "id": "morning-digest", "title": "Morning digest", "at": ["09:00"],
      "require": ["logger"], "labels": ["scheduled"] }
  ]
}
```

A schedule uses **either** `interval_seconds` **or** `at` (a list of local
`"HH:MM"` times). Drive it one-shot from any external timer, or use the built-in
loop:

```bash
agent-dispatch schedule tick  schedules.json          # one pass (cron / systemd timer / manage_schedule)
agent-dispatch schedule serve schedules.json --interval 60   # built-in timer loop
```

#### Managed registry + single-producer job-lease

Instead of driving a hand-edited spec file, recurring schedules can be
**registered** with the coordinator as first-class objects, then listed,
inspected, paused, and removed. A **job-lease** elects a single producer for a
scope so one host (e.g. a fleet chronicler) runs the registry tick while others
idle -- *pin-not-failover*: a first writer wins the scope and renews it, a
different holder is refused and the lease is never auto-stolen (reassignment is
an explicit `lease-release --force`). This is distinct from the engine's
per-task claim/recovery; it only picks *which machine* ticks.

```bash
agent-dispatch schedule register schedules.json        # upsert every entry into the registry
agent-dispatch schedule list [--active]                # registered schedules
agent-dispatch schedule inspect <id>                   # entry + next occurrences + lease
agent-dispatch schedule pause|resume <id>
agent-dispatch schedule remove <id>

# tick / serve the *registry* (no spec file); serve is lease-gated:
agent-dispatch schedule tick  --registry
agent-dispatch schedule serve --registry --lease-scope chronicle --holder $(hostname) --interval 300

# manage the job-lease directly:
agent-dispatch schedule lease-list
agent-dispatch schedule lease-show    <scope>
agent-dispatch schedule lease-acquire <scope> --holder <machine> [--ttl N]
agent-dispatch schedule lease-release <scope> --holder <machine> [--force]
```

A registered entry is the same schedule dict, but self-contained (it carries its
own `repo`; `register` bakes in a spec's `default_repo`). Registry ticks reuse
the same `not_before` + `sched:<id>:<epoch>` idempotency, so a lease holder that
sleeps and wakes simply replays just-missed occurrences via each schedule's
lookback window without double-creating.

### Periodic command emitters (`agent-dispatch emitter`)

A domain producer that exposes an idempotent one-shot command can be declared as
an **emitter** and run on a cadence by the singleton supervisor. The supervisor
owns the process lifetime; the emitter loop owns the interval and a
pin-not-failover job lease, so multiple eligible hosts may discover the same
declaration but only the lease holder invokes the command.

```yaml
name: review-inbox
kind: emitter
spec:
  id: review-inbox
  command: [review-emitter, tick]
  interval_seconds: 3600
  timeout_seconds: 900
  cwd: /path/to/producer
filters:
  permit:
    machine: [host-a]
```

Place the declaration in a registrar-discovered directory and run the singleton
with `agent-dispatch supervise serve`. `supervise daemon-status` and registrar
listing inspect it; `supervise override disable|enable
declared:<owner>:review-inbox` pauses/resumes it immediately without editing the
declaration. The command is an argv list (never a shell string); optional `env`
adds string-valued environment variables. `lease_scope` defaults to
`emitter:<id>` and can be supplied explicitly when several declarations share
one producer election.

`agent-dispatch emitter tick|serve SPEC --holder HOST` is the diagnostic/direct
surface used by the supervised child. Normal deployments declare the emitter
rather than wiring cron, a Scheduled Task, or another external timer.


### Reactive webhook producer (`agent-dispatch webhook`)

A small HTTP app that maps two generic, forge-neutral event shapes onto tasks:

- `POST /webhook/pr` -- a git-forge PR event; when **merged**, creates a
  follow-up task (`source=pr-webhook`, `origin_ref=pr/<n>`) in the lane derived
  from the payload's repository remote. Handles the shape GitHub and Gitea share.
- `POST /webhook/telemetry` -- a monitoring alert; a **firing** alert creates a
  remediation task (`source=telemetry`). Accepts an Alertmanager-style
  `{"alerts": [...]}` batch or a single flat alert object.

Every task carries a deterministic `dedup_key`, so a redelivered webhook doesn't
double-enqueue. Behavior (templates, base-branch/severity allowlists, an optional
inbound bearer token, the coordinator URL) is set in an optional JSON config:

```bash
agent-dispatch webhook --config webhook.json --host 127.0.0.1 --port 9331
```

### Evaluator -- a producer's lifecycle handler (`agent-dispatch evaluate`)

A producer puts work on the queue; an **evaluator** decides what happens *next* as
that work progresses -- the *judgment* half of emitters-and-evaluators. It is
hook-like: it receives one task **lifecycle event** (the coordinator shape
`{"type": "task.completed", "task": {...}}`) and returns decisions -- emit a
follow-up task, or nothing. A declarative spec of rules matches on the event and
mints follow-ups from templates, so a standing domain automates a whole cycle
(reviewer done -> open a conflict-resolution follow-up; a goal met -> the next
goal) without a bespoke module.

```bash
# apply an evaluator to an event read from stdin (a hook/producer pipes it in):
echo '{"type":"task.completed","task":{"id":"t1","labels":["recipe:reviewer"],"status":"completed","origin_ref":"o/n#42"}}' \
  | agent-dispatch evaluate --spec evaluator.json --repo o/n
agent-dispatch evaluate --spec evaluator.json --event-file event.json --dry-run
```

Spec shape (JSON): a `rules` list, each with `on` (event type, or a list), an
optional `when` predicate (`labels_any` / `labels_all` / `status` / `source`), and
an `emit` block that templates the follow-up (`title_template`, `prompt_template`,
`labels`, `requires`, `dedup_template`, ...). The first matching rule wins; a
follow-up defaults `source=evaluator`. The **degenerate case is the ad-hoc kick**:
a one-off task with no evaluator still runs -- an evaluator is opt-in judgment,
never required. See
[`visions/plugins/agent-dispatch`](../../visions/plugins/agent-dispatch/README.md)
(§Concepts/*The evaluator*, §Features/*emitters-and-evaluators*).

## Recipes (loop archetypes)

A **recipe** is a packaged *shape* of long-running agentic work -- a charter
template plus the suspend/resume rhythm and the resolution it drives toward. Three
archetypes ship in-box: **reviewer** (drive a pull request to merged-or-abandoned),
**conflict-resolution** (take the last mile of a stalled change), and
**goal-driven** (drive an arbitrary goal through PRs). A recipe is a *first-class,
directly-invokable* capability: you can kick a one-off from the CLI with only a
coordinator + a worker body -- no standing service, emitter, or evaluator is
required (the "recipes run ad-hoc" path).

```bash
agent-dispatch recipes list                       # the available recipes + their params
agent-dispatch recipes describe reviewer          # full descriptor (templates, suspend-on, resolution)

# render a recipe's fields without creating anything (inspect / dry-read):
agent-dispatch recipes render reviewer --param repo=owner/name --param pr=42

# carve an ad-hoc task from a recipe (and, with --spawn, embody a worker to drive it):
agent-dispatch recipes kick reviewer --param repo=owner/name --param pr=42 --repo owner/name --spawn
agent-dispatch recipes kick reviewer --param repo=owner/name --param pr=42 --dry-run   # preview the create call
```

`kick` reuses the ordinary `create` path, so it inherits lane resolution, dedup,
and the `--spawn`/`--spawn-backend` embodiment (default `embody`: a CLI autopilot
in a fresh worktree with a full checkout -- the right body for a recipe). A
**reserved-work `dedup_key`** is derived from the recipe + its parameters, so
re-kicking the same recipe for the same target collides rather than forking the
work (override with `--dedup-key`). The rendered charter reaffirms the two safety
invariants -- *drive the worktree to a clean resolved state on abandon* and *report
what you did* -- so a worker carries them even on the ad-hoc, no-service path. See
[`visions/plugins/agent-dispatch`](../../visions/plugins/agent-dispatch/README.md)
(§Concepts/*The recipe*, §Features/*loop-recipes* + *recipes-run-ad-hoc*).

### Driving a recipe loop (`agent-dispatch recipes drive`)

A recipe declares the *shape* of a loop; the **driver** is the small state machine
that turns it into a rhythm. `drive` maps a recipe + a `--signal` (what just
happened) to the next action:

- **work** -- do a pass (`start`, or a `suspend_on` event like `change-updated`
  means the world moved and there's something to react to). The agent performs it.
- **suspend** -- nothing to do until the world moves (`work-done` / `idle`): hand
  the wait to *hibernate-the-wait* until a `suspend_on` event fires.
- **resolve** -- a terminal signal (`merged`/`landed`/`goal-met` -> landed;
  `abandoned`/`closed` -> abandoned): *drive the worktree to resolution* and finish.

```bash
agent-dispatch recipes drive reviewer --signal start        # -> work
agent-dispatch recipes drive reviewer --signal work-done    # -> suspend (wait on suspend_on)
agent-dispatch recipes drive reviewer --signal merged       # -> resolve (landed)

# --execute performs the non-work legs on the substrate:
agent-dispatch recipes drive reviewer --signal work-done --execute \
  --resume <machine/worktree> -- agent-worktrees pr-watch 42   # spawn the detached waiter
agent-dispatch recipes drive reviewer --signal abandoned --execute --base main   # run the unwind
```

The decision is pure; `--execute` runs the **suspend** leg (spawn the detached
hibernation waiter -- needs `--resume` + a `--` wait command) and the **resolve**
leg (the drive-to-resolution unwind). **work** stays the agent's to perform. This
is the executable seam that composes recipes + `run` + `resolve` into a loop. See
[`visions/plugins/agent-dispatch`](../../visions/plugins/agent-dispatch/README.md)
(§Concepts/*The recipe*, §Behaviors/*a-loop-runs-with-or-without-a-service*).

## Drive the worktree to resolution (`agent-dispatch resolve`)

Finishing a loop -- whether the work **landed** or the worker is **abandoning** it
-- must leave the worktree in a *clean, resolved final state*, never an orphan
branch half-done. `resolve` packages that mandate as an inspectable, executable
plan a worker runs on **its own** worktree:

```bash
# preview the plan (default -- runs nothing):
agent-dispatch resolve --outcome landed
agent-dispatch resolve --outcome abandoned --base main --source owner/name#42

# perform it (the abandon path's unwind is destructive):
agent-dispatch resolve --outcome abandoned --base main --execute
```

- **landed** -> a single *verify-clean* check (the merge already resolved it).
- **abandoned** -> *unwind to base* (`git reset --hard` to the tracked upstream, or
  `origin/<base>`), *drop untracked* cruft, then a *reconcile-source* instruction
  (notify the producing effort/issue/PR so nothing downstream believes the work
  landed). The reconcile step is **advisory**: agent-dispatch coordinates it, it
  doesn't post on your behalf.

Planning is pure; execution only runs with `--execute`, and a failed destructive
unwind stops rather than pressing on. `agent-dispatch abandon --resolve` surfaces
this same plan alongside the abandon so the required unwind is an explicit
expectation, not a silent one. See
[`visions/plugins/agent-dispatch`](../../visions/plugins/agent-dispatch/README.md)
(§Behaviors/*drive-the-worktree-to-resolution*).

## Hibernate the wait (`agent-dispatch run`)

A worker that can only wait on a slow external condition (a review, a build, a PR
becoming mergeable) shouldn't sit on a live session and its token budget. It hands
the wait to the layer: `run` executes the blocking command and, when it resolves,
resumes the worktree-affinitied worker via an agent-bridge nudge.

```bash
# foreground: run the wait, then nudge the worker to resume:
agent-dispatch run --resume <machine/worktree> --task <id> -- agent-worktrees pr-watch 42

# detached: the wait runs in a process that outlives this one, so the worker can
# be torn down (costing nothing) while it waits -- true hibernation:
agent-dispatch run --detach --resume <machine/worktree> -- agent-worktrees pr-watch 42
```

Everything after `--` is the blocking wait command (its own flags are never parsed
as `run` options). With `--detach` the wait is re-exec'd as a fully detached,
cheap OS-level waiter (no agent, no tokens); the expensive worker session is spun
down and re-woken with its context intact when the wait returns. The resume is a
best-effort bridge nudge -- a genuinely-gone worker is handled by liveness
recovery, not the nudge. See
[`visions/plugins/agent-dispatch`](../../visions/plugins/agent-dispatch/README.md)
(§Features/*hibernate-the-wait*).

## Steer a blocked worker (`agent-dispatch card` / `steer`)

*Hibernate-the-wait* handles a wait on a **machine** condition; **steering**
handles a wait on a **human** decision. When a goal-loop worker needs operator
input it can't make itself (screen a draft before it posts, choose between
options, confirm a risky step), it **posts a card** describing what it needs and
suspends; the operator answers later through any surface, and the coordinator
**wakes the worker** with the answer.

```bash
# The worker posts a card describing what it needs and suspends. A --request-input
# form marks the task "awaiting-steer" (blocked on the operator):
agent-dispatch card set <id> \
  --title "Confirm rollout plan" --status "Ready for operator direction" \
  --link "<url to the rich artifact>" --body @card.md \
  --request-input "decision:choice[Proceed,Revise],notes:textarea?decision=Revise"

# Anyone can see what's blocked on them and read a card + its steer inbox:
agent-dispatch list --status started        # awaiting_steer=true rows are "needs you"
agent-dispatch card show <id>

# The operator answers; --wake nudges the owning worktree to resume (default on):
agent-dispatch steer submit <id> --field decision=Proceed

# The resumed worker drains all answers and continues toward its goal:
agent-dispatch steer take <id> --all        # -> {"steers": [{"fields": {...}, "sender": ...}]}
```

- **`card`** is a latest-only object on the task (`title`/`status`/`link`/`body`/
  `request_input`); the rich artifact lives elsewhere (a doc/PR the `link` points
  at) -- the card is only the glanceable brief + the form. `--request-input` is a
  compact field spec (`name[:text|textarea|choice[a,b,...]]`, comma-separated).
  Append `?choice_field=value` to gate a follow-up field on a single-select
  answer (for example, `reason:textarea?feedback=Reject`). Choice fields select
  their first option by default, so a producer can put its recommendation first
  while still offering the alternatives. Conditions are one level deep: their
  source must be an unconditional choice, and the expected value must be one of
  that choice's declared options.
- **`steer submit`** appends the operator's answer to the task's append-only steer
  inbox and clears `awaiting_steer`; it is **not** worker-owned (the operator, or a
  surface acting for them, answers). After persistence, the coordinator asks
  agent-bridge to resume the owner immediately (and queues the prompt if that
  owner is busy), regardless of which surface submitted the answer. Bridge
  failure never loses the durable steer. `steer take --all` is the owner-gated
  wake-side read that atomically drains every pending answer for the resumed
  worker; plain `steer take` remains the one-at-a-time inspection form.
- **General, not domain-specific.** The coordinator stores card/steer objects
  opaquely, so any dispatched agent that must block on operator input uses this --
  the same transport a picker "form" surface or an `ask_user` skill writes through.
- **Never a verdict.** Steering carries operator *guidance*; there is no task
  state/verb/`result_ref` that sets an outcome from it. A card is posted on a
  **held** task and leaves it held (never a terminal transition).

## Development

```bash
cd plugins/agent-dispatch
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```

## Coordinator + CLI

Run the per-host coordinator (loopback by default), then drive it with the CLI:

```bash
agent-dispatch serve                     # binds loopback on an OS-assigned port
                                         # (AGENT_DISPATCH_PORT pins if needed)

# from any agent/producer (AGENT_DISPATCH_URL points at the coordinator):
agent-dispatch create "Add narration track" --require logger --dedup-key seg42
agent-dispatch worktree-status           # this worktree's inbox: tasks assigned to + owned by it
agent-dispatch inbox                      # machine-scoped, cross-lane pickable tasks (default: proposed)
agent-dispatch inbox --board              # lifecycle groups + independent ACTIVE/STALLED execution badge
agent-dispatch claim                     # lease my assigned/eligible task (identity auto-resolved)
agent-dispatch claim  <task-id>          # claim THAT specific task (positional = task id, like the verbs below)
agent-dispatch claim  <task-id> --worker <owner>   # ...as an explicit owner (rarely needed; default: CWD identity)
agent-dispatch start  <id>  <owner>
agent-dispatch suspend <id> <owner> --reason "waiting for an external result"
agent-dispatch resume <id> <owner>                 # same owner; durable async wake
agent-dispatch release <id> <owner> --reason "use a replacement"
agent-dispatch complete <id> <owner> --result-ref pr/123
agent-dispatch list --status queued
agent-dispatch recover                                 # requeue tasks whose owner is gone
agent-dispatch watch                                   # stream task events (SSE) as JSON lines
```

`inbox` complements the two lane-scoped reads: `worktree-status` is *this
worktree's* assigned/owned tasks, and `list` is scoped to the calling repo's
lane, but `inbox` spans **every** lane and returns the tasks *this machine* can
pick up — a matching `target_machine` plus machine-agnostic ones — defaulting to
the `proposed` state. Each entry carries `target_worktree`, `affinity`, `labels`
and `repo_name`, so a consumer (e.g. the worktree picker's task pivot) can group
by worktree and badge handoffs. The machine is resolved from the CWD via
`agent-worktrees`; pass `--machine <name>` to override.

### Worker identity

An agent's identity is the **`machine`/`worktree_id`** pair — the only durable
agent id a multi-machine system has. `claim` and `worktree-status` **resolve it from the
current directory** by delegating to `agent-worktrees` (the same CWD resolution
git uses), so an agent in its worktree just runs `agent-dispatch worktree-status`
/ `agent-dispatch claim` with no arguments. Claiming stamps that pair as the
task's `owner`, and **claim honors targeting**: an agent only leases tasks that
are untargeted or targeted at its own machine/worktree. Pass `--machine` /
`--worktree` to override the resolution (or where `agent-worktrees` is absent).

The coordinator publishes `task.created` / `.proposed` / `.approved` / `.claimed`
/ `.started` / `.suspended` / `.resumed` / `.released` / `.yielded` /
`.completed` / `.abandoned` / `.detached` events on
`GET /events` (Server-Sent Events) — the hook a subscriber (e.g. agent-bridge)
reacts to.

### Spawning a worker (agent-bridge)

`create --spawn` asks **agent-bridge** to spawn a worker agent that claims and
executes the task:

```bash
agent-dispatch create "Summarize PR 42" --require review --spawn            # managed (waits)
agent-dispatch create "Summarize PR 42" --spawn --spawn-agent task-worker --async  # fire-and-forget
```

The worker is instructed to claim the specific task by id
(`agent-dispatch claim <task> --worker <id>`). If the `agent-bridge` CLI isn't on
PATH, `--spawn` **degrades gracefully** — the task is simply left queued for any
worker to claim, so agent-dispatch stays usable without a bridge.

`--spawn` is guarded against **double-spawn** by an atomic **spawn reservation**
taken from the coordinator before anything is launched: a dedup collision
(`--spawn` on an existing `dedup_key`) or a racing second `--spawn` spawns the
worker **exactly once**; the rest skip. See
[`docs/spawn-supervisor.md`](docs/spawn-supervisor.md) for the reservation model.

### Supervising a lane (`agent-dispatch supervise`)

The supervisor turns **queued** tasks into host embody autopilots — **exactly
once each** — over the same reservation primitive. It's generic (no
producer-specific logic) and safety-first: a task is spawned only when a *fresh*
reservation is acquired, so a slow-but-alive embody with an active reservation is
never double-spawned.

```bash
agent-dispatch supervise --once                       # one cycle (this repo's lane)
agent-dispatch supervise --label autopilot            # loop; only spawn opted-in tasks
agent-dispatch supervise --all-repos --max-concurrent 3
agent-dispatch supervise --label sweep --headless-label sweep   # embody 'sweep' headless-ACP
agent-dispatch supervise --pool host-a,host-b --origin origin --headless --label sweep
agent-dispatch supervise --no-reactive --interval 30            # fixed polling only
agent-dispatch supervise --evaluator eval.json        # + advance loops across terminal events
agent-dispatch reservations list --state spawned      # what's in flight
agent-dispatch reservations fail <key>                # release a confirmed-dead spawn
```

Each cycle **reconciles** (settles reservations of terminal tasks), optionally runs
the **evaluator pass**, then **polls** (reserve → embody → record, up to
`--max-concurrent`). It also **heartbeats the lease of every confirmed-alive
worker** so a quiet-but-alive session is not wrongly recovered (disable with
`--no-heartbeat`), **releases and re-queues confirmed-gone bodies** for
re-embodiment, and can **nudge stalled-but-live workers** before recovery. An
`unknown` liveness probe is always left alone. Repeated spawn failures are
treated as a supervisor dead-letter condition (failed reservation history; no
more auto-retry until a human intervenes), not as a second task state change.
The serve loop is reactive by default: within the poll interval it wakes early
when an embodied worker's turn goes `running -> idle`; `--no-reactive` restores
plain fixed-interval polling.

The bare `supervise` above runs the loop in the foreground. The
`supervise register|status|list|remove` subcommands instead manage durable
**registrations** — a caller registers a unit (a lane, schedule, emitter, or
evaluator) with the host's singleton supervisor and gets back a handle, and the
call *returns* rather than becoming the loop. `supervise serve` runs the
**singleton daemon** that actually runs those registrations:

```bash
agent-dispatch supervise register --label autopilot   # register this lane; prints the handle
agent-dispatch supervise register --label autopilot --ensure   # + start the daemon if needed
agent-dispatch supervise list                         # what's registered here
agent-dispatch supervise status <id>                  # inspect one registration
agent-dispatch supervise remove <id>                  # drop one (the daemon winds it down)
agent-dispatch supervise serve                        # run the singleton daemon (foreground)
agent-dispatch supervise daemon-status                # is a daemon running here?
```

Exactly **one** daemon per machine-and-environment runs every registration, each
in its own subprocess, reconciling on change (start / restart-on-spec-change /
wind-down-on-remove / crash-revive) and single-instance-guarded by a crash-safe OS
lock (a second daemon stands down; a crashed one's lock is auto-released so a
restart reclaims it). All four kinds are daemon-run: **supervised-lane** and
**evaluator** drive the embody loop (the latter subsuming `supervise
--evaluator`), **schedule** runs the timer producer, while **emitter** runs either a periodic
lease-gated command or the webhook producer. Re-registering the
same unit is idempotent (the derived handle identifies it). See
[`docs/spawn-supervisor.md`](docs/spawn-supervisor.md#the-singleton-daemon-built--one-master-per-unit-subprocesses)
for the registration + daemon model.


**Evaluator pass — advance the loop (`--evaluator <spec>`).** With an evaluator
spec, each cycle feeds every **newly-terminal** task's lifecycle event
(`task.completed` / `task.abandoned`) to the evaluator (§ Evaluator) and applies
its decisions — emitting a follow-up task. This is the **service-driven** half of
*a-loop-runs-with-or-without-a-service*: a standing supervisor advances a domain's
loop (reviewer done → conflict-resolution follow-up; goal met → the next goal)
with no bespoke module. It's idempotent — each task fires once per process and the
emitted follow-up's `dedup_key` guards duplicates across restarts — and best-effort
(a bad evaluator or failed create is logged, never crashing the cycle).

Tasks embody as a mux-wrapped **CLI autopilot** by default. Mark **self-contained
sweep** labels with `--headless-label L` (repeatable, `--headless-agent` to name the
agent-bridge agent) to embody *those* labels as a **headless agent-bridge ACP** body
instead — no human attach, no CLI-start-prompt race — while other labels stay
CLI-first. See the design doc's "Per-label embody body" section.

For a remote host pool, `--pool host-a,host-b [--origin <alias>]` dispatches the
body to the first live pool host. The default body is still CLI/mux embody on
that host; add `--headless` to make **every** fleet body a headless agent-bridge
ACP session there (`ssh <host> agent-bridge create <agent> "<fleet seed>"
--no-wait`). Fleet headless mode uses the same Model-C seed and synthetic owner
as CLI fleet mode, records no worktree handle, and ignores `--headless-label`.
See the design doc's "Headless-fleet body" section.

## MCP tools (`agent-dispatch mcp`)

For agents that prefer **tools over a CLI**, `agent-dispatch mcp` runs a local
**stdio MCP server** — the per-agent interaction layer. It resolves the caller's
`machine`/`worktree` identity from the working directory (like the CLI) and
proxies each tool call to the coordinator, so `dispatch_claim` /
`dispatch_worktree_status` are auto-scoped to the agent's worktree with no
per-agent credential wiring. Requires the `mcp` extra
(`pip install 'agent-dispatch[mcp]'`).

Point a Copilot sub-agent (or any MCP client) at it:

```json
{
  "mcpServers": {
    "agent-dispatch": {
      "command": "agent-dispatch",
      "args": ["mcp"]
    }
  }
}
```

It exposes the queue as 24 tools: `dispatch_create` / `dispatch_approve` /
`dispatch_find` / `dispatch_sweep` / `dispatch_recipe_list` /
`dispatch_recipe_render` / `dispatch_recipe_kick` / `dispatch_list` /
`dispatch_show` / `dispatch_events` / `dispatch_wakes` / `dispatch_payload` /
`dispatch_worktree_status` / `dispatch_claim` / `dispatch_start` /
`dispatch_yield` / `dispatch_suspend` / `dispatch_resume` /
`dispatch_release` / `dispatch_complete` / `dispatch_abandon` /
`dispatch_heartbeat` / `dispatch_detach` / `dispatch_recover`. Wake-bearing
operations return after scheduling delivery; inspect the task audit trail for
the eventual result, or use `agent-dispatch wakes <id>`.
`dispatch_create` takes an inline `payload` the coordinator spills to a blob when
large.

### Two MCP surfaces

There are **two** ways to reach the tools — pick by where the client runs:

| Surface | Command / endpoint | Identity | Use when |
|---------|--------------------|----------|----------|
| **Local stdio shim** | `agent-dispatch mcp` | resolved from the caller's **CWD** (like the CLI) | the agent has `agent-dispatch` installed locally in its worktree |
| **Coordinator-hosted HTTP** | mounted at **`/mcp`** on the discovered coordinator endpoint | `X-Agent-Machine` / `X-Agent-Worktree` **request headers** (or explicit tool args) | a remote MCP client (e.g. an `agent-mcp` bridge on another host) that can't resolve local identity |

Both expose the same 20 `dispatch_*` tools and publish the same `task.*` events;
they only differ in how identity is supplied. The coordinator mounts `/mcp`
automatically when the `mcp` extra is installed (pass `enable_mcp=False` to
`create_app` to suppress it); if a bearer token is configured it also guards the
`/mcp` mount. A remote client points at, e.g.,
`http://<discovered-coordinator-endpoint>/mcp` and sets the identity headers per
agent.

Configuration (all optional): `AGENT_DISPATCH_HOST`, `AGENT_DISPATCH_PORT`
(server bind pin; omitted means OS-assigned port), `AGENT_DISPATCH_DB`,
`AGENT_DISPATCH_TOKEN` (bearer auth), `AGENT_DISPATCH_GC_INTERVAL` (liveness
garbage-collection cadence in seconds; `0` disables), `AGENT_DISPATCH_RUN_DIR` /
`AGENT_DISPATCH_ENDPOINT` (local endpoint discovery), `AGENT_DISPATCH_URL` (client
override), `AGENT_DISPATCH_SHARED_URL` / `AGENT_DISPATCH_SHARED_TOKEN` (opt-in
shared coordinator), and `AGENT_DISPATCH_NO_AUTOSTART` (disable lazy local
coordinator start).

## Troubleshooting

- `agent-dispatch health` checks the selected coordinator without lazy-starting
  one. Other local client verbs lazy-start a detached coordinator unless
  `AGENT_DISPATCH_NO_AUTOSTART` is set, a remote `--url`/`--shared` is used, or
  the caller is in WSL (the Windows host owns the coordinator).
- `scripts/install.{sh,ps1} status` reports the deployed version/manifest, the
  coordinator service, and every supervisor profile. Runtime logs live under
  `~/.agent-dispatch/` (`serve-service.log`, `reconcile.err.log`,
  `running-version.json`, `current-version`).
- Version drift is intentionally fail-loud: the session-start hook compares the
  installed payload version with `deploy-manifest.json` / `current-version` and
  reconciles in the background; a running coordinator writes
  `running-version.json` so the launcher can distinguish the live imported
  version from the on-disk slot.
- Wildcard binds (`0.0.0.0`, `::`) require `AGENT_DISPATCH_TOKEN`; otherwise the
  server refuses to start rather than expose the task-control API on the LAN.
