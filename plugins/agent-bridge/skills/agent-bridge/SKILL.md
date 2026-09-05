---
name: agent-bridge
description: >
  Persistent Agent-bridge control plane -- send prompts to persistent Copilot
  sessions
  over the local bridge service: local agents, SSH machines, CodeSpaces,
  containers, and live interactive sessions. Use this for live cross-boundary
  communication, not queued task-loop management or the Task tool.
  Trigger phrases include:
  - 'agent-bridge'
  - 'agent-bridge send'
  - 'agent-bridge create'
  - 'agent-bridge status'
  - 'remote agent'
  - 'local bridge agent'
  - 'send to agent'
  - 'bridge to'
  - 'cross-machine'
  - 'container agent'
  - 'external agent'
  - 'inter-agent'
  - 'relay to'
  - 'talk to <machine>'
  - 'send to <machine>'
---

# Agent-Bridge Control Plane

> **Before you start — use the payload-local session command.**
> The agent-bridge session command catalog supplies an exact `argv[0]` owned by
> this plugin payload. Replace `<agent-bridge catalog argv[0]>` in interactive
> bridge operations below with that path; never search `PATH` or substitute a
> same-named command from another payload. Commands explicitly labeled as
> management boundaries remain literal global-wrapper invocations. In
> PowerShell, invoke the catalog path as
> `& "<agent-bridge catalog argv[0]>" <args>`.
>
> The payload shim provisions its own runtime on first use and works without
> agent-worktrees. The first call may take ~30–120s (watch for
> `::agent-provisioning::`); let it finish and surface any exact provisioning
> failure instead of improvising a toolchain install.
>
> If session-start hooks did not publish the catalog, enumerate installed
> payloads for this plugin and fail unless exactly one exists. Invoke that
> payload's `bin/agent-bridge` on POSIX or `bin\agent-bridge.cmd` on Windows
> directly; never stamp or choose a global wrapper just to recover an in-session
> command, and never choose the first match from multiple marketplaces.

The session-start catalog is intentionally a **static breadcrumb**, not a
topology snapshot. It maps the logical command to its owning payload and may
eventually name stable machine/repository pivots, but it never enumerates
worktrees, sessions, or other fast-changing state. Query `agents`, `sessions`,
or the relevant repository at the point of use so ephemeral resources cannot
go stale in initial context.

## Unexpected behavior is a troubleshooting event, not repair authorization

An isolated disconnect caused by a network disruption or a daemon restart during
a plugin update is **expected**. Preserve and resume the **same** session. If its
state is unclear, use `<agent-bridge catalog argv[0]> peek <sid>` first; do not assume it is wedged
and do not clear, end, or replace it.

For genuinely **unexpected** bridge, provider, or session behavior -- a resume
that fails or does not settle promptly, a 409/500, repeated disconnects, an
apparently aborted turn, a relay/auth failure, or a session whose state does not
match the client result -- invoke and follow the
**`agent-bridge-troubleshooting` skill first**. It is the operational guidebook;
do not improvise recovery from this command overview.

The default response is **preserve and file**, not diagnose or repair:

1. Preserve the existing session and report/file the exact command, error, and
   already-visible state in the owning tracker. A client disconnect does not
   prove the remote turn stopped, and an isolated expected disconnect is not a
   bug by itself.
2. Unless the operator explicitly requested diagnosis or remediation, **stop
   there**.
3. When diagnosis is requested, use the guidebook's read-only sequence:
   `<agent-bridge catalog argv[0]> status <sid>`, a bounded
   `<agent-bridge catalog argv[0]> read <sid> --tail N`, then `peek` / persisted traces as
   applicable. Mutating steps still require explicit authorization.

Do **not** stop/end/recreate a session, start a replacement session, restart or
update the shared daemon, kill processes, stop/start a provider target, or edit
bridge state merely to clear an error. A daemon restart is not a session-repair
primitive and can affect unrelated work. Likewise, a CodeSpace resume/create
that takes multiple minutes is **abnormal evidence to diagnose**, not "known"
or expected latency to normalize.

## Agent-Bridge vs Internal Sub-Agents -- READ THIS FIRST

**agent-bridge is NOT the Task tool.** They solve completely different
problems:

| | agent-bridge | Task tool (sub-agents) |
|---|---|---|
| **What** | Communicates with persistent Copilot sessions outside this turn: local bridge agents, SSH machines, CodeSpaces, containers, or live interactive sessions | Spawns local background agents in **this session** |
| **How** | `<agent-bridge catalog argv[0]> send <agent> "prompt"` CLI command | `task` function call in your response |
| **Transport** | Local bridge service + local process / SSH / provider namespaces / live-session inbox | Local subprocess |
| **Scope** | Durable cross-session or cross-venue work | Same machine/session only |

**Rule:** When asked to "talk to", "send to", "relay to", or "communicate with"
a named bridge agent/venue (topology agent, `codespace:...`, `container:...`, or
live session), **use `<agent-bridge catalog argv[0]> send <agent-name> "prompt"`**. Never use the
Task tool for live cross-boundary communication -- it cannot
reach those bridge venues.

Run `<agent-bridge catalog argv[0]> agents` to see which agent names are available. If
your deployment includes a deployment-specific adapter skill (e.g.
`multi-machine system-agent-bridge`), it will list the concrete machine and agent
names for your environment.

### Responsibility boundary

- Use a native Task sub-agent for bounded work inside the current session.
- Use agent-bridge when the caller must converse with, steer, wait on, or take
  over a live agent across a repository, worktree, machine, or venue boundary.
- Use **`agent-dispatch:agent-dispatch`** when the durable task record, atomic
  claim, retry/supervision state, or eight-state task lifecycle is the product.
  A dispatched worker may be embodied through agent-bridge, but the queue owns
  the task and agent-bridge owns only the live conversation transport.

For generic task decomposition and the decision to delegate at all, use
**`delegation-guidance:delegating-work`**. For dedup-safe open-ended task
selection, use **`agent-dispatch:pick-and-claim`**.

### Relay Chain Pattern

When relaying a message through multiple machines (A -> B -> C), each
hop uses the payload-local `send` operation on **its own local bridge** to reach the
next machine. The chain is:

```
Machine A: <agent-bridge catalog argv[0]> send agent-on-B "relay this to C"
Machine B: <agent-bridge catalog argv[0]> send agent-on-C "the message"
```

Each machine's bridge manages its own outbound connections. Do NOT
create all sessions from one machine (that's a star, not a chain).

The bridge is the inter-agent communication service. It manages
persistent sessions with agents running on any configured machine
via local subprocess or SSH transport.

## Service Architecture

Each machine runs its own agent-bridge instance. By default it binds an
**OS-assigned ephemeral** loopback port and advertises the actual port via its
routing table (`active.json`), so nothing well-known is reserved and there is no
Windows/WSL port collision to design around (dotfiles #694); clients discover
the port (`<agent-bridge catalog argv[0]> status` prints it). The topology
is a mesh -- each instance manages outbound connections to other machines
via SSH. Sessions are persistent (SQLite-backed) and survive service
restarts.

Runs on **Windows** (scheduled task + PID file), **Linux/WSL** (systemd),
with macOS support planned.

**Installed as plugin:** Part of the `copilot-extensions` marketplace
plugin. Source code lives in the installed plugin directory at
`~/.copilot/installed-plugins/copilot-extensions/agent-bridge/`.

**Config lives at:** `~/.agent-bridge/config.yaml` (topology profiles
pointing to optional `machines.yaml`; the roster is derived from it when present.
Provider namespaces from `~/.agent-bridge/providers.d/` work without a topology.)

### Repository edits vs configuration adoption

The payload-local `config adopt` operation is a **machine-local projection
command**. It reads
repository topology and writes the current user's `~/.agent-bridge/config.yaml`;
it never edits, publishes, or deploys repository files.

When topology or purpose-built agent definitions must change:

1. use `config show` to identify the actual `machines_yaml` / `agents_config`
   source paths;
2. edit that repository source in its normal isolated worktree;
3. publish and merge through that repository's contribution flow;
4. deploy or sync the canonical checkout on the target machine;
5. run `config adopt` only if the user-level projection needs to be created or
   refreshed, then restart the service when instructed.

Persistent profiles must point at a canonical checkout, not a disposable
worktree. In-repo topology auto-discovered from `--repo <linked-worktree>` is
canonicalized to the anchor. A stateless harness may instead inherit
`machines.yaml` from its bound knowledge/state root, and explicit
`--machines-yaml` / `--agents-config` paths always remain exact. Any of those
external paths can become invalid if it names a disposable worktree; `config
validate` reports the missing file.

Before repairing a profile with `config adopt`, back up its topology-profile
stanza in `~/.agent-bridge/config.yaml`, including `default_copilot_args` and
`default_env`: adoption replaces the named profile rather than merging those
spawn defaults. Re-adopt against canonical source paths, then restore any
recorded defaults.


## CLI Commands

All commands connect to the local agent-bridge HTTP API; the service must be
running (`agent-bridge start`). <!-- marketplace-isolation: allow service-management -->
The essential one is **send**:

```bash
<agent-bridge catalog argv[0]> send <agent|machine|codespace:name|container:name> "<prompt>"
<agent-bridge catalog argv[0]> agents          # list cwd-project agents (--json)
<agent-bridge catalog argv[0]> machines        # list cwd-project machines + SSH readiness (--json)
<agent-bridge catalog argv[0]> --project <repo> agents
<agent-bridge catalog argv[0]> agents --all-projects
```

Core service setup is standalone. Optional sibling providers compose through
their own manifests/CLIs; if `agent-codespaces` or `agent-containers` is absent,
the corresponding namespace is simply absent.

The full command reference -- `send` (sync/async, sessions, timeouts), agent
and machine listing, session management, config adopt/show, and service
control -- is in [references/cli-commands.md](references/cli-commands.md).

For first-time setup, see the `agent-worktrees:copilot-extensions-setup` skill; for topology
configuration, see `plugins/agent-bridge/docs/machine-config.md`.

## Common Patterns

### Quick Remote Agent Interaction

```bash
# Ask a remote agent to check something
<agent-bridge catalog argv[0]> send server-wsl "Check disk space on /data"

# Ask another agent to run a command
<agent-bridge catalog argv[0]> send workstation-wsl "Run the test suite"
```

### Multi-Turn Conversation

```bash
# Start a session
<agent-bridge catalog argv[0]> send dev-wsl "Set up a new project" --no-wait

# Check sessions to get the ID
<agent-bridge catalog argv[0]> sessions --status running

# Send follow-up
<agent-bridge catalog argv[0]> send <session-id> "Now add the test framework"

# When done
<agent-bridge catalog argv[0]> end <session-id>
```

### Checking What's Running

```bash
# See all active sessions (CONTEXT column shows usage %)
<agent-bridge catalog argv[0]> sessions

# Get JSON for programmatic use
<agent-bridge catalog argv[0]> --json sessions
```

### Reading liveness -- `stalled` is usually deep thinking, NOT a wedge

`status`/`sessions` may show a `Liveness: stalled` (or a `[stalled]`-ish
"no output for a while") signal on a RUNNING session. **`stalled` means "no ACP
frame for `_STALL_AFTER_S` (5 min)", not "dead".** Modern models think silently
(no frame, no tool call) for **3-4 minutes** on a hard step -- live traces show
single reasoning turns of 191-223s. So a `stalled` session is *most often a
healthy deep-reasoning turn*, not a wedge.

- **Do NOT reflexively `end`+`create` (or `send` a fresh prompt) on `stalled`.**
  That is the "host got impatient and recreated a working session" anti-pattern.
- **First, give it time and re-check** -- a deep think resolves on its own; the
  daemon only *acts* on a stall after the far larger `live_stall_interrupt_after_s`
  (default 900s / 15 min), and even then it gracefully interrupts the turn
  (keeping the session), never respawns. A **reattached** still-thinking turn is
  likewise left alone until 900s (dotfiles#1276), so the bridge will not land it
  IDLE under you.
- Only treat it as a genuine wedge if it stays silent well past 900s, or the
  transport is actually gone (`disconnected` / `stopped` from connection loss --
  and even then, **`send` to reattach**, don't recreate; see below).

### Answering a dispatched agent's questions (elicitation backstop)

A dispatched agent can **reach for help** mid-turn via `ask_user` (a form
question). The bridge does **not** auto-answer -- it *parks* the turn and waits,
exactly as an interactive Copilot waits at its terminal. **You (the host) are
the human it reached for.** A parked question sits forever until you answer, so
watch for it and unblock it:

```bash
# `status` surfaces a parked question as an ASK: line with its fields + the
# exact command to answer:
<agent-bridge catalog argv[0]> status <sid>
#   ASK:     Which database engine should I use?
#            fields: db*=postgres|mysql|sqlite
#            answer: `<agent-bridge catalog argv[0]> answer <sid> --field <key>=<value> …`

# Answer it -- the agent's turn then continues:
<agent-bridge catalog argv[0]> answer <sid> --field db=postgres
# multiple fields: repeat --field; complex/typed values: --json '{"port": 5432}'
# not going to answer: --decline (agent proceeds without) or --cancel
```

- `--tool-call-id` is only needed when **several** questions are pending at once
  (status lists each id); with one parked question it defaults automatically.
- Answering is the RIGHT move -- prefer it over `end`+`create`. A parked
  `ask_user` is the agent asking for a decision, not a wedge; recreating the
  session throws away its in-progress work.

**Guidance for the remote/dispatched agent (fold into its prompt for
consequential work):** *"You are running dispatched via agent-bridge with a host
watching. When you hit a genuine decision point or are blocked on missing
info/permissions, use `ask_user` to reach for the host rather than guessing or
autopiloting down a risky path -- the host will answer. Do NOT use `ask_user` for
routine choices you can make yourself; reserve it for decisions that are
expensive to get wrong."*

### Context Window Monitoring

The `CONTEXT` column in the payload-local `sessions` output shows token usage as a
fraction with percentage (e.g., `110k/200k (55%)`). Use this as a
progress indicator -- more tokens consumed generally means more work
completed.

For detailed usage on a specific session:

```bash
<agent-bridge catalog argv[0]> session-usage <session-id>
```

This shows the full usage snapshot: context size/used/percentage, model,
turn count, and a visual bar.

The REST API equivalent is `GET /api/v1/sessions/{id}/usage`.

### Context-Aware Handoff (Long-Running Sessions)

When managing a remote agent across many turns, the host agent should
monitor context usage and **proactively cycle the session** before the
remote agent exhausts its context window. This is the host's
responsibility -- the remote agent does not manage its own context
lifecycle.

**When to bail: ~70% context usage.** This leaves room for the handoff
prompt itself (which consumes context) and a safety margin before the
75%/90% warning thresholds fire.

**The handoff cycle:**

```bash
# 1. Check usage (do this every 2-3 turns on long-running sessions)
<agent-bridge catalog argv[0]> session-usage <session-id>

# 2. If context_pct >= 70, request a handoff from the remote agent
<agent-bridge catalog argv[0]> send <session-id> \
  "Your context window is filling up. Generate a continuation prompt
   for a fresh session to resume this work. Include:
   - Original objective
   - Progress so far (with file paths)
   - Remaining work
   - Key decisions and their rationale
   - Gotchas or failed approaches
   Keep it under 250 words. The new session will have full tool access."

# 3. Capture the response -- that IS the handoff payload

# 4. End the old session (its handoff payload is captured). Ending it also
#    frees a one-session-per-CodeSpace agent so a fresh one can be created.
<agent-bridge catalog argv[0]> end <session-id>

# 5. Create a fresh session with the handoff as the first prompt. Use
#    `create` (not `send`) -- `send` would resume the old session instead
#    of giving the new context window a clean start.
<agent-bridge catalog argv[0]> create <agent-name> "Resume: <captured handoff payload>"
```

**Key points:**

- **No hooks or extensions required.** The host checks usage, makes the
  decision, sends the handoff request, and manages the session roll.
  The remote agent just answers a prompt.
- **The remote agent doesn't need to know** about context limits. It
  receives a normal prompt asking for a summary and responds normally.
- **Session roll preserves the worktree.** When starting the new session
  with the same agent name (and optionally the same `worktree_id` via
  the API), the new session lands in the same checkout with all prior
  commits available.
- **70% is the bail point, not 75%.** The 75% `context_warning` and
  90% `context_critical` SSE events are safety nets. If those fire,
  the handoff should already be in progress.

**Threshold reference:**

| Context % | Signal | Host action |
|-----------|--------|-------------|
| 0-50% | Normal | Continue sending work |
| 50-70% | Elevated | Monitor more frequently |
| 70% | **Bail point** | Request handoff, stop sending new work |
| 75% | `context_warning` SSE | Handoff should be in progress |
| 90% | `context_critical` SSE | Emergency -- do not send more prompts |

**Context % as a progress signal:** When listing sessions with
the payload-local `sessions` output, the CONTEXT column doubles as a rough progress
indicator. A session at 60% has done significant work. A session at 10%
is just getting started. Host agents can use this to prioritize which
sessions need attention, follow-up, or cycling.

## Dispatching Long Autonomous Work (build / test / PR)

When you hand a multi-step, long-running job (build → test → commit → PR) to a
remote or CodeSpace agent, the robust pattern is **fire a complete prompt,
monitor cheaply, resume on drop** — because you cannot steer a session mid-turn
and long sessions can drop.

### 1. Deliver the prompt intact

A multi-line prompt passed **on the command line** can be mangled before the
agent ever sees it (silent non-compliance — the agent acts on a partial
instruction). Two failure modes on Windows:

- The `.cmd`/`.ps1` shim forwards args via `%*`/`$args`, which re-tokenizes a
  multi-line prompt.
- Even calling the venv module directly with a here-string, PowerShell's
  native-argument construction **breaks at the first embedded double-quote**
  (`"`) in the prompt: the quote closes the wrapping and the remainder
  word-splits into stray argv tokens (you get `unrecognized arguments: …`).

**Most robust — pass the prompt via `--prompt-file` so it never transits argv.**
Write the prompt to a file (or pipe it on stdin) and hand `send`/`create` a path;
`-` reads stdin. Quotes, newlines, em-dashes, backticks — all preserved verbatim:

```powershell
# From a file:
Set-Content -Path .\dispatch.md -Value $prompt -Encoding UTF8
& "<agent-bridge catalog argv[0]>" create --no-wait <agent> --prompt-file .\dispatch.md

# ...or straight from stdin:
$prompt | & "<agent-bridge catalog argv[0]>" create --no-wait <agent> --prompt-file -
```

Do not bypass the payload command with a legacy venv path for inline prompts.
Use `--prompt-file` with a file or stdin so payload ownership and argument
boundaries are both preserved.

### 2. You cannot steer a running session — front-load everything

- `send` to a **running** session is **rejected**; the only way to end a stuck
  turn is to kill its tool call, which wedges/collapses the session. So the
  *initial* prompt must be **complete and autonomous**: all rules, env caveats,
  and the finish line (commit / push / PR). Don't plan to "correct it later".
- Make the prompt **idempotent / resumable**: "inspect git state and any existing
  PR first and continue from there; don't redo finished steps."
- Tell the agent to **push early and often** (after build, after tests) so
  progress survives a drop, and to emit **structured progress markers** —
  `PROGRESS build=ok`, `PROGRESS tests=ok n=<count>`, `PROGRESS commit=<sha>`,
  `PROGRESS pr=<id>` — which the bridge captures (latest value per key) and
  surfaces in `<agent-bridge catalog argv[0]> status <sid>` under **Progress:**, so you get
  ground-truth milestones (did it build? push? open a PR?) without grepping the
  feed or shelling into the host.

### 3. Monitor cheaply — through the bridge, at phase boundaries

- Prefer `<agent-bridge catalog argv[0]> status <sid>` — one compact screen with the session
  state, the **in-flight tool + elapsed** (so you can tell a busy agent from a
  hung one), and your cursor lag (`behind` N events). It surfaces the
  tool-progress liveness that a plain `read` cannot see.
- To peek at recent output without disturbing the live cursor, use a
  cursor-neutral incremental read:
  `<agent-bridge catalog argv[0]> read <sid> --tail N` (last N
  events) or `--since <id>` (only-new after an id). These replace the old
  `--range A:B | tail` slice-the-whole-feed workaround.
- Do this at the *expected* phase boundaries (after setup, build ETA, test ETA) —
  **not** continuously, and **never** dump the whole feed into your context.
- The `CONTEXT` % column is a coarse progress signal (see Context Window
  Monitoring).
- **Get durable ground truth from the work's source of truth** (the git remote /
  PR API), **not by shelling into the agent's host.** SSHing a CodeSpace that has
  an active dispatch competes with the dispatch's own SSH/ControlMaster
  connection and can collapse the session — reserve host SSH for a *stopped*
  agent.

### 4. Resume on drop is routine, not exceptional

A **service (daemon) restart mid-dispatch is now survivable**: a streaming
`send`/`read`/`wait` detects the disconnect and **reconnects automatically**,
resuming from the caller's acked delivery cursor — it no longer hard-fails with
`Cannot connect`. On the bridge side, the session is rehydrated from SQLite
(an interrupted turn is marked as such), and the next `send` to that session
**auto-resumes** the remote agent (`load_session` re-attaches to the persisted
ACP/Copilot session) before delivering the prompt.

Longer/other drops (especially CodeSpace) can still strand a session —
`gh cs ssh` tunnel lifetime, relay credential TTL, CodeSpace idle timeout. A
session the bridge marks `stopped` because it **lost the connection** to the
child is **not** gone: its session host keeps the child alive. **Resume it with
`send`** — the bridge reattaches to the surviving child (adopts the same ACP
session id — no respawn) and delivers the prompt. Do **not** reflexively
`end`+`create` a connection-loss stop; that throws away a live, resumable child
and its in-flight work. If the state is unclear, run
`<agent-bridge catalog argv[0]> peek <sid>`;
then resume the same session and verify the resumed turn did real work.

**Stale-cancel on the first reattached turn.** If a session was `stop`ped (or
severed) *mid-turn*, that turn may have been cancelled with an ACP
`session/cancel` (an explicit stop, a stall-heal, or a redeploy). On reattach
the child can surface that **stale cancel** as the first turn's result —
`Operation cancelled by user` + an empty `end_turn` — even though your new prompt
was accepted. This is a **host/bridge-injected cancel draining**, not agent
misbehavior and not a categorical "stopped resume does no work". The cancel
drains after one turn, so the fix is still `send` — **`send` again**, and the
now-idle session continues normally:

```bash
<agent-bridge catalog argv[0]> send <sid> "<same idempotent prompt>"   # first turn ate the stale cancel; this one runs
```

Only if it **keeps** cancelling (or the session is genuinely gone) and the
operator authorizes context loss, discard and recreate:

```bash
<agent-bridge catalog argv[0]> end <sid>          # a daemon restart can also resurrect an old session as "active" — end that too
<agent-bridge catalog argv[0]> create <agent> "<same idempotent prompt>"
```

> **Fixed in 0.4.0-dev206 — the idle-gap variant.** A distinct root cause used to
> produce this exact symptom *without any stop, reattach, or redeploy*: the first
> resend on a session that had simply **been idle longer than
> `live_stall_interrupt_after_s`** (default 900s) phantom-cancelled, and every
> resend re-cancelled until one happened to land. The live-stall watchdog
> (`reconcile_wedged_running`) measured the brand-new turn's silence from the
> *previous* turn's last frame — the whole idle gap — and interrupted it before
> it emitted anything. `submit_prompt` now resets the stall clock at turn start,
> so a fresh turn is measured from its own start. If you are on **dev206 or
> later**, a resend after a long idle gap just runs; the "send again" dance below
> is only for the genuine mid-turn reattach-drain case above.
> (test-chamber #4122 / #2817.)

> A **distinct** cause of the same `Operation cancelled by user` string is a
> permission/`ask_user` request the headless client can't get answered: the
> parked request resolves to `cancelled` at teardown. That path shows a
> permission/`ask_user` event *before* the cancel (the reattach case shows none)
> and is avoided by dispatching with `--allow-all`. End+create won't help it;
> re-dispatch with tools pre-authorized.

Because the prompt is idempotent and the agent pushed incrementally, the new
session continues from the remote with minimal rework.

## Delegating an Effort Slice (multi-agent coordination)

When an **effort** (see the `efforts:planning-efforts` skill) is worked by more than one
agent, agent-bridge is the dispatch layer and the **effort README's
`## Coordination` section** is the shared contract. The git mechanics are turn-key
helpers in the **`agent-worktrees:git-collaboration`** skill -- this section is
only the *choreography*; it adds no new mechanics.

> **A delegate is a real agent-bridge session, not a Copilot sub-agent.** Each
> delegate is a separate Copilot CLI session (local or SSH) with **its own
> worktree** that can `git commit` and ff-push. In-process sub-agents (the Task
> tool) share your context, have no branch of their own, and cannot take a slice.

Two topologies -- pick per how interdependent the slices are:

### A. Shared feature branch (interdependent slices)

The slices must integrate before any can merge, so they share one branch and the
**host owns the PR**.

1. **Host** publishes the shared branch from its worktree:
   `agent-worktrees git feature-branch <name> --push`. <!-- marketplace-isolation: allow agent-worktrees-management -->
2. **Host** dispatches each slice with a complete, idempotent prompt (per
   *Dispatching Long Autonomous Work* above) that tells the delegate to:
   - sync to the branch -- `agent-worktrees git feature-branch <name> --sync`; <!-- marketplace-isolation: allow agent-worktrees-management -->
   - do its assigned `## Coordination` section, committing on its worktree branch;
   - **write back its slice of the effort README**;
   - hand off -- `agent-worktrees git merge-to-feature <name>` (ff-pushes). <!-- marketplace-isolation: allow agent-worktrees-management -->
3. **Host** syncs forward as slices land (`git feature-branch <name> --sync`),
   journaling each dispatch + landing in the effort.
4. When coordination is done, **only the host** opens the PR(s) from the shared
   branch. Delegates never open or merge PRs, and never force-push it.

### B. Independent worktrees, per-slice PRs (well-componentized work)

When each slice leaves the default branch **green on its own**, skip the shared
branch: each delegate works in its **own** worktree and opens its **own** PR
(its repo's normal `create-pr` flow). The host watches remote PR state to
sequence follow-ups -- it sees the merge land and moves to the next task. Use
this only when the pieces are truly independent; otherwise use topology A.

### Either way

- Keep the effort README **ahead of the conversation** -- dispatches, landings,
  and blockers are journaled there so a fresh host (or a recovering one) resumes
  from the file. Batch effort edits (each costs a PR) per the `efforts:planning-efforts`
  in-flight discipline.
- Clean up dispatched worktrees afterward (see *Remote Worktree Lifecycle* below).

## Agent Names

The agent roster is **derived from topology** — `machines.yaml`'s
`control_plane.project` (one control-plane agent per machine × SSH environment,
e.g. `dev6` / `dev6-wsl` / `cloud1`) plus `<repo>@<machine>` agents from each
repo's `.agent-worktrees/related.yaml`, and the local project agents
auto-discovered from `projects.yaml`. (`acp-agents.json` is retired; an explicit
`agents_config` is still honored as a deprecated override.) Use
`<agent-bridge catalog argv[0]> agents` to list available agents.

Inside an adopted repo, the payload-local `agents` and `machines` operations show
that CWD project's catalog. Use top-level `--project <repo>` to address another
project without changing directories, or `--all-projects` for the full
deployment. Derived machine agents also advertise stable SSH aliases; alias
matching is case-insensitive, so presentation-oriented `display_name` casing is
not part of the addressing contract.

### Addressing: `<repo>@<venue>` (repo × venue)

An agent is a **(repo × venue)** pair — the repo dimension is orthogonal to the
venue (machine / codespace / container). Address them two ways:

- **Bare venue** — `dev6`, `<codespace>`: runs the venue's default repo (a
  machine's control-plane project; a CodeSpace's own workspace, e.g.
  `example-web`).
- **`<repo>@<venue>`** — bind an explicit repo to a venue:
  - `SPO.Core@dev6` → the SPO.Core binstub on dev6 (loopback; runs `<repo>`
    instead of the control-plane default).
  - `example-web@<codespace>` → the CodeSpace's own repo (same as bare).
  - `dotfiles@<example-web-codespace>` → **error**: launching a *different* repo's
    checkout on a CodeSpace is not yet supported (a CodeSpace hosts one repo).

Machine venues dispatch locally when they resolve to the current host/environment
(loopback detection), and over SSH when the target machine has `ssh.ready: true`
and a matching SSH environment.

### CodeSpace agents — friendly names

CodeSpaces are exposed by the `codespace:` namespace resolver (auto-discovered;
no registration). You can address one by its **raw** name or its **friendly**
(display) name — the name stored in effort specs — and the `codespace:` prefix
is **optional**:

```bash
<agent-bridge catalog argv[0]> send codespace:my-feature "..."   # friendly, prefixed
<agent-bridge catalog argv[0]> send my-feature "..."             # friendly, bare
```

The bridge resolves the friendly name to the underlying raw CodeSpace and keys
the one-session-per-CodeSpace guard by the raw name, so all three forms address
the same session. A **bare** name that matches more than one agent (across
namespaces) makes the bridge **balk** and enumerate the candidates with their
namespaces — qualify it (`codespace:<name>`) or use the exact name to
disambiguate.

## Remote Worktree Lifecycle

When agent-bridge spawns a session for an agent with `project` configured,
it creates a **new git worktree** on the target machine via
`agent-worktrees resolve --new`. <!-- marketplace-isolation: allow agent-worktrees-management -->
The payload-local `end` operation cleans up
the bridge session (subprocess, DB record) but does **not** finalize or
remove the spawned worktree. Without cleanup, these accumulate as orphaned
"unused" worktrees.

### Cleanup Responsibility

The **host agent** (the session that called the payload-local `send` operation) is
responsible for cleaning up worktrees it caused to be created. During
the host session's wrap-up:

1. **End bridge sessions first.** Run
   `<agent-bridge catalog argv[0]> sessions` to find any active sessions. End
   each one with `<agent-bridge catalog argv[0]> end <id>`.

2. **Run worktree cleanup.** After ending bridge sessions, run:
   ```bash
   agent-worktrees worktrees cleanup # marketplace-isolation: allow agent-worktrees-management
   ```
   This lists worktrees eligible for removal. The default (no flags)
   only removes worktrees that went through proper finalization --
   this is always safe to run with `--clean`.

3. **Report unused worktrees -- do not auto-purge.** The cleanup output
   may show "unused" worktrees (no commits, no uncommitted changes).
   Some of these may be bridge-spawned orphans; others may be
   intentional. **Do not run `--include-unused` automatically.**
   Instead, note any unused worktrees that appeared during this
   session's lifetime and ask the user whether to remove them.

4. **Proceed with host finalization.** After bridge cleanup, continue
   with the host session's own worktree finalization / sign-off flow.

### Remote (SSH) Agents

For worktrees spawned on a remote machine via SSH transport, cleanup
must run **on the target machine** where the worktree was created:

```bash
ssh <machine-alias> "agent-worktrees worktrees cleanup"
```

Use the same SSH alias that agent-bridge used for the session.

### Worktrees With Commits

If the remote agent made commits or has uncommitted changes, the
worktree is **not** unused -- it contains real work. Do not remove it.
Report the worktree path, branch, and status to the user for manual
review or normal worktree finalization.

### Future: Surgical Cleanup

Currently, worktree cleanup operates at the project level -- it cannot
distinguish bridge-spawned worktrees from user-created ones. A future
improvement will track the worktree ID in the bridge session metadata,
enabling targeted cleanup of only bridge-spawned orphans.

## Receiving and answering agent messages

The fabric can deliver a message **into a live interactive session** (yours or a
peer's). It arrives as a user turn wrapped in a structured envelope:

```
<agent-message from="cjohnson@orchestrator" reply-to="81ec1b77-…" msg-id="2">
…body…
</agent-message>
```

This marker (same family as `<system_reminder>`) means the turn came from
**another agent via the bridge**, not the operator. To answer, reply to the
`reply-to` address with the same verb you use for any agent:

```bash
<agent-bridge catalog argv[0]> send <reply-to> "your reply"
```

The payload-local `send` operation recognizes a live-session target and delivers into it; your
own identity/session ride along so the peer can answer back. See
[references/agent-messages.md](references/agent-messages.md) for the full
convention. Delivery is on by default; `/peer` mutes a session.

## Live interactive sessions — a distinct surface (represent + message)

Beyond the **ACP-owned** sessions the bridge spawns and drives (`send` /
`create` / `wait` / `stop`), the bridge also tracks **live interactive Copilot
CLI sessions it does *not* own** — the picker-launched or `embody`-spawned
sessions a bundled extension auto-registers. These live under the
`/api/v1/live-sessions/*` surface (never the ACP `sessions` table) and enable
two capabilities without the destructive take-over:

- **Representation (read).** A registered session pushes its event stream to the
  bridge, which re-exposes it over the ordinary SSE machinery so **Neuron Forge
  views it live, read-only** (seed cold history from the transcript, splice the
  live tail). A represented `permission_request` is **read-only** (no correlation
  id — unanswerable by a viewer; approval stays at the operator's terminal), and
  a **`driven_by`** field names the steering agent for the "driven by `<agent>`"
  banner (null = operator-launched).
- **Messaging (write).** The inbox above
  (`<agent-bridge catalog argv[0]> send <live-session>`)
  delivers an attributed turn *into* a live interactive session — the mirror of
  the read path.
- **Addressing by worktree handle.** `resolve` maps a worktree handle → its
  currently-live session, so `reply-to` survives a session handoff (an agent is
  a *series of sessions in one worktree*).
- **Reading the registry (CLI).**
  `<agent-bridge catalog argv[0]> live-sessions list
  [--worktree-id <id>]` and
  `<agent-bridge catalog argv[0]> live-sessions resolve --handle
  <session-id|worktree-handle>` expose the registry from the shell (add global
  `--json` for machine-readable output). Beyond registration/liveness the view
  carries **turn-state** derived from the represented event tail --
  `turn_state` (`running`/`idle`) plus a computed `liveness` label
  (`active`/`stalled`/`idle`) -- and an operator session's **`latest_progress`**
  beat (see below). This is the surface **agent-dispatch** joins against to track
  a CLI-embodied task: a leased task's owner is `<machine>/<worktree>`, so it
  resolves the worktree to its live session and overlays
  liveness/turn-state/`driven_by` on `agent-dispatch show`/`list` <!-- marketplace-isolation: allow agent-dispatch-management -->
  (best-effort;
  degrades to status+lease when the bridge is absent).
- **Progress beat for an operator session (Phase 7 7c).**
  `<agent-bridge catalog argv[0]> live-sessions progress --handle
  <session-id|worktree-handle> --summary "<one
  line>" [--phase <p> --pr <ref> --blocker <why>]` records a **bounded,
  latest-only** status beat on the live-session record -- the operator-session
  analogue of a dispatched task's `latest_progress` (a dispatched worker uses
  `agent-dispatch progress` against its task instead). <!-- marketplace-isolation: allow agent-dispatch-management -->
  Every field is hard-capped
  so the beat stays a status line, never a chat log. The bundled extension nudges
  an operator-driven session to emit one at a gentle cadence.

**Durable work gets a CLI body, not a headless one.** When you *dispatch* work
meant to outlive its caller, prefer a **CLI-backed autopilot session** (via
`agent-dispatch create --spawn --spawn-backend embody` <!-- marketplace-isolation: allow agent-dispatch-management -->
→ `agent-worktrees embody --new`, <!-- marketplace-isolation: allow agent-worktrees-management -->
`driven_by=agent-dispatch`) over a headless ACP worker — it is durable,
NF-viewable, and completes its task explicitly. Ephemeral, caller-bounded helpers
still use headless bridge agents (`send`/`create`). A **handoff** is the in-place
variant: a live cutover replaces the current CLI in its mux with a successor that
takes the work over (see the `context-handoff:context-handoff` and `agent-dispatch:agent-dispatch` skills).

## Troubleshooting

- **Start with the `agent-bridge-troubleshooting` skill.** Its read-only
  decision tree and persisted traces are authoritative; this short list is only
  a symptom index.
- **"agent-bridge is not responding"** -- run
  `<agent-bridge catalog argv[0]> status`. Normal
  daemon-touching commands self-heal a down service. If that fails, capture the
  routing/log evidence from the guidebook and file a bug; do not manually
  restart the shared daemon unless the operator directs it.
- **"Agent not found"** -- check `<agent-bridge catalog argv[0]> agents` for available names.
  Use `<agent-bridge catalog argv[0]> agents --all-projects` if the target belongs to another
  topology profile.
  The topology config may not include the agent you're looking for.
- **Session stuck in RUNNING** -- the downstream agent may be waiting for
  permission or processing a long tool call. Inspect with
  `<agent-bridge catalog argv[0]> status <session-id>` and a bounded
  `<agent-bridge catalog argv[0]> read <session-id> --tail N`. Do not stop or replace it merely
  because the client timed out.
- **SSH connection failures** -- verify SSH aliases work:
  `ssh <machine-alias> echo ok`. Check
  `<agent-bridge catalog argv[0]> machines` for
  SSH readiness status, then follow the guidebook without changing provider
  state.
