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
pointing to this repo's `machines.yaml`; the roster is derived from it)


## CLI Commands

All commands connect to the local agent-bridge HTTP API; the service must be
running (`agent-bridge start`). The essential one is **send**:

```bash
agent-bridge send <agent|machine|codespace:name|container:name> "<prompt>"
agent-bridge agents          # list registered agents (--json)
agent-bridge machines        # list machines + SSH readiness (--json)
```

The full command reference -- `send` (sync/async, sessions, timeouts), agent
and machine listing, session management, config adopt/show, and service
control -- is in [references/cli-commands.md](references/cli-commands.md).

For first-time setup, see the `copilot-extensions-setup` skill; for topology
configuration, see `plugins/agent-bridge/docs/machine-config.md`.

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

The agent roster is **derived from topology** — `machines.yaml`'s
`control_plane.project` (one control-plane agent per machine × SSH environment,
e.g. `dev6` / `dev6-wsl` / `cloud1`) plus `<repo>@<machine>` agents from each
repo's `.agent-worktrees/related.yaml`, and the local project agents
auto-discovered from `projects.yaml`. (`acp-agents.json` is retired; an explicit
`agents_config` is still honored as a deprecated override.) Use
`agent-bridge agents` to list available agents.

Run `agent-bridge agents` to see the full list for your deployment.

### Addressing: `<repo>@<venue>` (repo × venue)

An agent is a **(repo × venue)** pair — the repo dimension is orthogonal to the
venue (machine / codespace / container). Address them two ways:

- **Bare venue** — `dev6`, `<codespace>`: runs the venue's default repo (a
  machine's control-plane project; a CodeSpace's own workspace, e.g.
  `odsp-web`).
- **`<repo>@<venue>`** — bind an explicit repo to a venue:
  - `SPO.Core@dev6` → the SPO.Core binstub on dev6 (loopback; runs `<repo>`
    instead of the control-plane default).
  - `odsp-web@<codespace>` → the CodeSpace's own repo (same as bare).
  - `dotfiles@<odsp-web-codespace>` → **error**: launching a *different* repo's
    checkout on a CodeSpace is not yet supported (a CodeSpace hosts one repo).

With the inter-machine SSH mesh retired, machine venues are **loopback-only**
(same box); remote machine dispatch reappears if `ssh.ready` is restored.

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
agent-bridge send <reply-to> "your reply"
```

`agent-bridge send` recognizes a live-session target and delivers into it; your
own identity/session ride along so the peer can answer back. See
[references/agent-messages.md](references/agent-messages.md) for the full
convention. Delivery is on by default; `/peer` mutes a session.

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
