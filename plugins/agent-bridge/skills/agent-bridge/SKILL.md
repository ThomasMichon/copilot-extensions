---
name: agent-bridge
description: >
  Agent-bridge control plane -- send prompts to agents on OTHER MACHINES
  via CLI commands. Use this for cross-machine communication, NOT the
  Task tool.
  Trigger phrases include:
  - 'agent-bridge'
  - 'agent-bridge send'
  - 'remote agent'
  - 'send to agent'
  - 'bridge to'
  - 'cross-machine'
  - 'external agent'
  - 'inter-agent'
  - 'relay to'
  - 'talk to <machine>'
  - 'send to <machine>'
---

# Agent-Bridge Control Plane

## Agent-Bridge vs Internal Sub-Agents -- READ THIS FIRST

**agent-bridge is NOT the Task tool.** They solve completely different
problems:

| | agent-bridge | Task tool (sub-agents) |
|---|---|---|
| **What** | Communicates with Copilot sessions on **other physical machines** | Spawns local background agents in **this session** |
| **How** | `agent-bridge send <agent> "prompt"` CLI command | `task` function call in your response |
| **Transport** | SSH to remote machines | Local subprocess |
| **Scope** | Cross-machine, cross-network | Same machine only |

**Rule:** When asked to "talk to", "send to", "relay to", or
"communicate with" a named machine or agent from the topology, **ALWAYS
use `agent-bridge send <agent-name> "prompt"`**. Never use the Task
tool for cross-machine communication -- it cannot reach other machines.

Run `agent-bridge agents` to see which agent names are available. If
your deployment includes a facility-specific adapter skill (e.g.
`facility-agent-bridge`), it will list the concrete machine and agent
names for your environment.

### Relay Chain Pattern

When relaying a message through multiple machines (A -> B -> C), each
hop uses `agent-bridge send` on **its own local bridge** to reach the
next machine. The chain is:

```
Machine A: agent-bridge send agent-on-B "relay this to C"
Machine B: agent-bridge send agent-on-C "the message"
```

Each machine's bridge manages its own outbound connections. Do NOT
create all sessions from one machine (that's a star, not a chain).

Agent-bridge is the inter-agent communication service. It manages
persistent sessions with agents running on any configured machine
via local subprocess or SSH transport.

## Service Architecture

Each machine runs its own agent-bridge instance. The default port is
platform-specific: **9280 on Windows**, **9281 on Linux/WSL**. This avoids
TCP port collisions when both environments share the same host (WSL2 shares
the Windows TCP port space). The topology
is a mesh -- each instance manages outbound connections to other machines
via SSH. Sessions are persistent (SQLite-backed) and survive service
restarts.

Runs on **Windows** (scheduled task + PID file), **Linux/WSL** (systemd),
with macOS support planned.

**Installed as plugin:** Part of the `copilot-extensions` marketplace
plugin. Source code lives in the installed plugin directory at
`~/.copilot/installed-plugins/copilot-extensions/agent-bridge/`.

**Config lives at:** `~/.agent-bridge/config.yaml` (topology profiles
pointing to this repo's `machines.yaml` and `acp-agents.json`)

## CLI Commands

All commands connect to the local agent-bridge HTTP API. The service must
be running (`agent-bridge start`) for client commands to work.

### List Available Agents

```bash
agent-bridge agents
agent-bridge agents --json
```

Shows all registered agents from the topology config (name, type, host,
spawnable status).

### List Machines

```bash
agent-bridge machines
agent-bridge machines --json
```

Shows all machines in the topology with SSH readiness and environment
details.

### Send a Prompt to an Agent

```bash
# Reuse this caller's session for the agent (resumes it if stopped),
# or start one if none exists, then send a prompt (streams response)
agent-bridge send <agent-name> "your prompt here"

# Send to a specific existing session
agent-bridge send <session-id> "follow-up prompt"

# Fire-and-forget (don't wait for response)
agent-bridge send <agent-name> "do this" --no-wait
```

`send` auto-detects whether the target is an agent name or a session ID.
When given an **agent name**, it never starts a *fresh* session on top of an
existing one: it reuses this caller's session for that agent — keyed by
`(agent, caller)`, where the caller is `$WORKTREE_ID` (or `--caller`) — and
**resumes it if stopped**. Only when this caller has no session for the agent
is a new one started. Output streams in real-time: response text, thought
blocks, and tool call summaries.

> **There is no `send --new`.** It was removed because it silently reused a
> pre-existing (often stopped, stale) session instead of creating a fresh one.
> To force a brand-new session, use `agent-bridge create` (below).

### Create a Fresh Session

```bash
# Force a brand-new session for an agent (no reuse)
agent-bridge create <agent-name>

# ...and send a first prompt in one step
agent-bridge create <agent-name> "your first prompt"
```

`create` always spawns a fresh session, bypassing caller reuse. For agents
that allow only **one session at a time** — CodeSpaces share a single
checkout — `create` **refuses** if a session already exists rather than
silently latching onto it, and tells you to end the existing one first:

```bash
agent-bridge end <existing-session-id>   # free the CodeSpace
agent-bridge create <agent-name> "..."   # then start clean
```

### Choosing send vs create — check for an outstanding session first

Before dispatching work to an agent, **check whether it already has a
session and whether that session's state is relevant to the work**:

```bash
agent-bridge sessions          # is there a session for this agent/caller?
agent-bridge session-usage <session-id>   # how full is its context?
```

- **Relevant & healthy** (same effort, context well under ~70%) → `send`
  to continue it (it resumes automatically if stopped).
- **Stale / unrelated / context-heavy** (different effort, near the context
  limit, or known-bad state) → `agent-bridge end <session-id>` then
  `agent-bridge create` for a clean start.

`send` is the safe default (it reuses/resumes); reach for `create` only when
you have decided the existing session must be discarded.

### Session Management

```bash
# List all sessions (includes CONTEXT column showing usage %)
agent-bridge sessions
agent-bridge sessions --status idle

# Check context window usage for a session
agent-bridge session-usage <session-id>

# Compact one-screen status: state, in-flight tool + elapsed, and how far
# behind your delivery cursor is (head/acked) -- without dumping the feed.
agent-bridge status <session-id>
agent-bridge status <session-id> --steps 5   # also show the last 5 collapsed steps

# Wait for a running session's current turn
agent-bridge wait <session-id>

# Stop a session (preserves state for resume)
agent-bridge stop <session-id>

# Resume a stopped session
agent-bridge resume <session-id>

# End a session (full cleanup)
agent-bridge end <session-id>

# Garbage-collect aged terminal/disconnected sessions + compact the DB.
# Runs automatically (startup + periodic sweep); this forces it on demand.
agent-bridge gc
```

### Service Control

Use the `service` subcommands to control the long-running daemon. These
delegate to the platform service manager (Windows scheduled task / Linux
systemd user unit) that the installer registered, so they control the **same**
instance that auto-starts at logon -- and they fall back to a detached spawn if
no service manager is registered.

```bash
agent-bridge service start      # start the daemon (no-op if already running)
agent-bridge service stop       # stop the daemon (kills the worker + releases the port)
agent-bridge service restart    # stop, wait for the port to release, start
agent-bridge service status     # running state + bound port + PID
```

> **Note:** plain `agent-bridge stop <session-id>` stops a *session*, not the
> service. For the daemon, always use `agent-bridge service stop`.

`agent-bridge start` (no `service`) runs the server in the **foreground** -- it
is the entry point the service manager invokes, and is useful for debugging.
Add `--port` / `--bind` to override the platform default (9280 Windows / 9281
Linux/WSL).

```bash
# Foreground (debugging) -- blocks the terminal
agent-bridge start
agent-bridge start --port 9280 --bind 127.0.0.1

# Health check (also shows the bound URL)
agent-bridge status

# Version
agent-bridge version
```

### Zero-Downtime Redeploy (routing table + drain + cutover)

A redeploy no longer has to hard-kill live work. Clients resolve the daemon
through a **routing table** (`~/.agent-bridge/active.json`) instead of the
static config port: `BridgeClient.from_config()` reads the table first and falls
back to `config.yaml` when it is absent (so the table is inert until a daemon
publishes it). This lets a new daemon come up on a fresh port, the table flip to
it, and the old daemon retire -- with no client ever dialing a dead port.

```bash
# Stop accepting new sessions/turns and wait for in-flight work to settle
# (the busy oracle: streaming turns + active background sub-agents). Bounded by
# --timeout; --force proceeds anyway at timeout. Exit 0 = clean, 2 = still busy.
agent-bridge drain --timeout 300
agent-bridge undrain                 # release the gate (rollback)

# Active/passive cutover: spawn the new daemon on a free port, flip the routing
# table, drain + retire the old one. Rolls back on any pre-commit failure.
agent-bridge deploy --drain-timeout 300 [--force]
```

The installer `update` path drains before stopping by default (no hard-killed
turns up to `AGENT_BRIDGE_DRAIN_TIMEOUT`). Full zero-downtime cutover is opt-in
while service-manager reconciliation is validated:

```bash
AGENT_BRIDGE_ZERO_DOWNTIME=1 aperture-labs services agent-bridge update
```

> A passive instance (`agent-bridge start --passive`) does not self-publish the
> routing table or bind the credential relay (9857) -- the deploy orchestrator
> flips the table after a health check and calls `/api/v1/relay/adopt` once the
> old daemon releases the relay port. The port-keyed singleton lock lets the
> active and passive daemons coexist on one config dir during the overlap.



```bash
# Run as an ACP agent on stdio (for chat UIs / upstream ACP clients)
agent-bridge agent --agent my-agent
```

Presents agent-bridge as an ACP-compatible agent. Upstream ACP clients
connect via stdio and the bridge routes prompts to the named downstream
agent. Used by chat interfaces that speak ACP natively.

### Elevated Agents (Windows)

Some local agents must run **elevated** (admin) -- e.g. an enlistment-based
`base_repo` agent that needs admin plus a build environment. Such a project is
flagged once, at adoption time:

```bash
agent-worktrees register <Project> --base-repo --elevated   # writes elevated: true
```

After that, **just send to it by its bare name** -- no special prefix:

```bash
agent-bridge send <Project> "do the elevated work"
```

The (non-elevated) primary daemon cannot spawn an elevated Copilot directly, so
for a flagged agent it transparently:

1. **auto-ensures an elevated sub-daemon** -- a second agent-bridge on loopback
   `127.0.0.1:9281`, run elevated via a persistent `/RL HIGHEST` scheduled task,
   isolated under `<config>/elevated/`; and
2. **relays** the session to it over ACP-over-WebSocket (`agent-bridge
   acp-connect ws://127.0.0.1:9281/acp/<Project>`). Because the whole sub-daemon
   is elevated, the agent it spawns is elevated too.

This only triggers on Windows when the primary is **not already elevated**; an
already-elevated daemon (and the sub-daemon itself) spawns the agent locally, so
there is no recursion.

**Headless after first use.** The scheduled task is consented **once** (a single
UAC prompt the first time it is registered); every cold start afterwards runs it
with `schtasks /run` -- **no UAC**. The sub-daemon also **auto-shuts-down** after
~10 min with no active sessions (so it does not linger), and the persistent task
restarts it headlessly on the next request. Manage it directly when needed:

```bash
agent-bridge elevated start          # ensure up (headless once the task exists)
agent-bridge elevated status         # port / up / task-registered / agents
agent-bridge elevated stop           # stop now, headless (keeps task for restart)
agent-bridge elevated stop --deregister  # full teardown: delete the task (one UAC)
```

> **Security (v1):** the sub-daemon is loopback-only and bearer-token gated, but
> the token is in a user-readable file, so any same-user process could drive the
> elevated agent. Acceptable on a single-user dev box; hardening is tracked
> separately.

### Config Management

```bash
# Show current config
agent-bridge config show
agent-bridge config show --json

# Add/update a topology profile for a repo
agent-bridge config adopt --repo /path/to/repo --profile facility

# Remove a topology profile
agent-bridge config remove my-profile

# Validate config (checks file paths, topology completeness)
agent-bridge config validate
```

For first-time setup, see the `copilot-extensions-setup` skill. For
detailed topology configuration, see `plugins/agent-bridge/docs/machine-config.md`.

## Common Patterns

### Quick Remote Agent Interaction

```bash
# Ask a remote agent to check something
agent-bridge send server-wsl "Check disk space on /data"

# Ask another agent to run a command
agent-bridge send workstation-wsl "Run the test suite"
```

### Multi-Turn Conversation

```bash
# Start a session
agent-bridge send dev-wsl "Set up a new project" --no-wait

# Check sessions to get the ID
agent-bridge sessions --status running

# Send follow-up
agent-bridge send <session-id> "Now add the test framework"

# When done
agent-bridge end <session-id>
```

### Checking What's Running

```bash
# See all active sessions (CONTEXT column shows usage %)
agent-bridge sessions

# Get JSON for programmatic use
agent-bridge sessions --json
```

### Context Window Monitoring

The `CONTEXT` column in `agent-bridge sessions` shows token usage as a
fraction with percentage (e.g., `110k/200k (55%)`). Use this as a
progress indicator -- more tokens consumed generally means more work
completed.

For detailed usage on a specific session:

```bash
agent-bridge session-usage <session-id>
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
agent-bridge session-usage <session-id>

# 2. If context_pct >= 70, request a handoff from the remote agent
agent-bridge send <session-id> \
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
agent-bridge end <session-id>

# 5. Create a fresh session with the handoff as the first prompt. Use
#    `create` (not `send`) -- `send` would resume the old session instead
#    of giving the new context window a clean start.
agent-bridge create <agent-name> "Resume: <captured handoff payload>"
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
`agent-bridge sessions`, the CONTEXT column doubles as a rough progress
indicator. A session at 60% has done significant work. A session at 10%
is just getting started. Host agents can use this to prioritize which
sessions need attention, follow-up, or cycling.

## Dispatching Long Autonomous Work (build / test / PR)

When you hand a multi-step, long-running job (build → test → commit → PR) to a
remote or CodeSpace agent, the robust pattern is **fire a complete prompt,
monitor cheaply, resume on drop** — because you cannot steer a session mid-turn
and long sessions can drop.

### 1. Deliver the prompt intact

On **Windows**, the `.cmd` shim forwards args via `%*`, which re-tokenizes a
multi-line prompt and can deliver it **garbled** to the agent (silent
non-compliance — the agent acts on a partial instruction). Pass the prompt so it
survives by calling the venv module directly, so PowerShell preserves argv:

```powershell
$pyb = "$env:USERPROFILE\.agent-bridge\venv\Scripts\python.exe"
& $pyb -m agent_bridge create --no-wait <agent> @'
<your full multi-line prompt>
'@
```

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
  surfaces in `agent-bridge status <sid>` under **Progress:**, so you get
  ground-truth milestones (did it build? push? open a PR?) without grepping the
  feed or shelling into the host.

### 3. Monitor cheaply — through the bridge, at phase boundaries

- Prefer `agent-bridge status <sid>` — one compact screen with the session
  state, the **in-flight tool + elapsed** (so you can tell a busy agent from a
  hung one), and your cursor lag (`behind` N events). It surfaces the
  tool-progress liveness that a plain `read` cannot see.
- To peek at recent output without disturbing the live cursor, use a
  cursor-neutral incremental read: `agent-bridge read <sid> --tail N` (last N
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
`gh cs ssh` tunnel lifetime, relay credential TTL, CodeSpace idle timeout. When
`agent-bridge sessions` / `agent-bridge status <sid>` shows the session
`stopped`/gone:

```bash
agent-bridge end <sid>          # a daemon restart can also resurrect an old session as "active" — end that too
agent-bridge create <agent> "<same idempotent prompt>"
```

Because the prompt is idempotent and the agent pushed incrementally, the new
session continues from the remote with minimal rework.

## Delegating an Effort Slice (multi-agent coordination)

When an **effort** (see the `planning-efforts` skill) is worked by more than one
agent, agent-bridge is the dispatch layer and the **effort README's
`## Coordination` section** is the shared contract. The git mechanics are turn-key
helpers in the `agent-worktrees` **`git-collaboration`** skill -- this section is
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
   `agent-worktrees git feature-branch <name> --push`.
2. **Host** dispatches each slice with a complete, idempotent prompt (per
   *Dispatching Long Autonomous Work* above) that tells the delegate to:
   - sync to the branch -- `agent-worktrees git feature-branch <name> --sync`;
   - do its assigned `## Coordination` section, committing on its worktree branch;
   - **write back its slice of the effort README**;
   - hand off -- `agent-worktrees git merge-to-feature <name>` (ff-pushes).
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
  from the file. Batch effort edits (each costs a PR) per the `planning-efforts`
  in-flight discipline.
- Clean up dispatched worktrees afterward (see *Remote Worktree Lifecycle* below).

## Agent Names

Agent names come from `acp-agents.json` in your project repo. Use
`agent-bridge agents` to list available agents.

Run `agent-bridge agents` to see the full list for your deployment.

### CodeSpace agents — friendly names

CodeSpaces are exposed by the `codespace:` namespace resolver (auto-discovered;
no registration). You can address one by its **raw** name or its **friendly**
(display) name — the name stored in effort specs — and the `codespace:` prefix
is **optional**:

```bash
agent-bridge send codespace:my-feature "..."   # friendly, prefixed
agent-bridge send my-feature "..."             # friendly, bare
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
`agent-worktrees resolve --new`. The `agent-bridge end` command cleans up
the bridge session (subprocess, DB record) but does **not** finalize or
remove the spawned worktree. Without cleanup, these accumulate as orphaned
"unused" worktrees.

### Cleanup Responsibility

The **host agent** (the session that called `agent-bridge send`) is
responsible for cleaning up worktrees it caused to be created. During
the host session's wrap-up:

1. **End bridge sessions first.** Run `agent-bridge sessions` to find
   any active sessions. End each one with `agent-bridge end <id>`.

2. **Run worktree cleanup.** After ending bridge sessions, run:
   ```bash
   agent-worktrees worktrees cleanup
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

## Troubleshooting

- **"agent-bridge is not responding"** -- service isn't running. Start it
  with `agent-bridge start`.
- **"Agent not found"** -- check `agent-bridge agents` for available names.
  The topology config may not include the agent you're looking for.
- **Session stuck in RUNNING** -- the downstream agent may be waiting for
  permission or processing a long tool call. Check with
  `agent-bridge wait <session-id>` or `agent-bridge stop <session-id>`.
- **SSH connection failures** -- verify SSH aliases work:
  `ssh <machine-alias> echo ok`. Check `agent-bridge machines` for
  SSH readiness status.
