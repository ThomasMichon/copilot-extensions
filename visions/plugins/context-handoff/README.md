# context-handoff — Vision

- **Subject:** the `context-handoff` plugin — the policy owner for continuing a
  Copilot agent's work across a context-window boundary
- **Scope:** leaf (child of [`agent-fabric`](../../agent-fabric/README.md))
- **Status:** Active
- **Last revised:** 2026-09-13
- **Reality docs:** no dedicated architecture doc yet — see
  `plugins/context-handoff/skills/context-handoff/`,
  `plugins/context-handoff/skills/diagnosing-handoff-cutover/`, and the active
  efforts `efforts/active/handoff-live-cutover/`,
  `efforts/active/handoff-cutover-lifecycle-journal/`,
  `efforts/active/handoff-cutover-reload-robustness/`

## Purpose & Intent

A Copilot agent's usable context window is finite; a task worth doing is
frequently not. `context-handoff` exists so that boundary is invisible to the
work: an agent should be able to work as thoroughly and exhaustively as a task
warrants — never rushing, truncating, or cutting corners for fear of running
out of room — because it can always **hand off** to a successor that picks up
exactly where it left off. The human operator or automated caller experiences
one continuous, uninterrupted campaign of progress toward their goal, not a
session that silently stalls, degrades, or vanishes when its window fills.

`context-handoff` owns the **policy**: when a handoff should happen, what a
successor needs to inherit to continue faithfully, and how the human/caller
is kept informed throughout. It deliberately does not own process launch,
terminal/mux mechanics, or durable worktree/session lineage storage — those
belong to whichever execution-host provider is present
(see [`session-hosting`](../../session-hosting/README.md),
[`plugins/agent-worktrees`](../agent-worktrees/README.md), and
[`plugins/agent-bridge`](../agent-bridge/README.md)). `context-handoff` is the
one piece of this story that must work identically no matter which host — or
no host at all — is driving the process underneath it.

## Concepts & Components

- **Pressure monitor** — continuously tracks context utilization for the
  running session and classifies it into an escalating series of tiers as it
  climbs toward exhaustion.
- **Handoff content** — the package of continuity information a predecessor
  hands to its successor: open items and next steps, the overarching
  effort/goal if unfinished, outstanding background flows the predecessor was
  running, and awareness of related external state it held responsibility
  for. Distinct from *how* that package physically reaches the successor
  process (a hosting concern).
- **Handoff mandate** — the standing instruction, carried into every
  successor, that it too may hand off in turn under the same policy, as many
  times as an effort requires, and that a brand-new session starting fresh is
  told this mechanic exists from the start.
- **Host-agnostic trigger** — the boundary `context-handoff` crosses to ask
  *something* to realize a cutover, expressed so that a mux-centric host, a
  daemon-centric host, or no host at all can each act on it (or decline to,
  falling back to a manual recipe).
- **Lineage record** — the durable, auditable trail of predecessor→successor
  succession for a worktree's chain of sessions (owned jointly with
  `agent-worktrees`' durable agency state; see that vision for the storage
  guarantee).
- **Operator-facing policy** — the layered, overridable configuration that
  governs whether and how any of this happens automatically at all.

## Features

### Escalating pressure response

As a session's context utilization climbs, the agent receives a series of
escalating signals rather than a single trigger: an early advisory that
handoff preparation should begin, a firmer nudge to hand off at the next
natural break, and — because an agent cannot be relied on to always act on a
nudge in time — a final, non-negotiable point past which the system forces
the handoff itself rather than continuing to ask. All three tiers are
sensible defaults, never hard-coded assumptions the operator cannot move (see
Operator-Configurable Policy).

### Continuity of work

A handoff carries forward everything a successor needs to continue
faithfully without re-discovery: the predecessor's open items and next steps;
the overarching effort or the user's original stated goal, if not yet
complete; every outstanding background flow the predecessor was running
(long-lived watches, polling loops, scheduled/recurring prompts, and the
like) so the successor resumes monitoring them without a gap; and awareness
of external state the predecessor was responsible for (open pull requests,
held claims or leases, coordination with other worktrees or remote agents).
Nothing the predecessor was responsible for is silently dropped at the
boundary — where something genuinely cannot be mechanically resumed, it is
carried forward as an explicit, visible open item instead.

### Perpetuating mandate

The instruction to hand off, and the freedom it grants, survives its own
handoff: a successor inherits the same standing mandate its predecessor had,
including the expectation that it too hands off in turn if it hits the same
pressure — potentially many times across one effort, until the effort is
actually done. A session starting fresh work (no predecessor) is told this
mechanism exists from its very first turn, so it can be as thorough as the
task deserves without husbanding its own context out of fear.

### Seamless cutover

The human or caller experiences continuation, not a hard seam. In an
interactive setting, the successor visibly takes focus while the predecessor
recedes without further acting — not merely disappearing, but retiring
cleanly. In an automated/headless setting, whatever is consuming the
predecessor's event stream transparently follows the successor instead,
perceiving one continuous logical agent scoped to the *worktree*, not a
single ephemeral session. The predecessor makes no further changes once a
handoff is underway.

### Operator-configurable policy

An operator can fully disable automatic handoff, force manual-only cutovers,
move the escalation thresholds, or express them as absolute usage counts
instead of proportions. Policy resolves in layers — a broad default that a
more specific scope can override — so a single operator preference need not
be repeated everywhere it applies.

### Always-visible operator communication

The agent always tells its human or caller that a handoff is coming, before
or as it happens — never as a silent background event. A plain manual
recipe — something a human can copy and act on themselves in a fresh session
— is always produced alongside any automatic attempt, so a broken or absent
automatic path never leaves the operator without a way forward.

### Auditable succession

The full chain of which session handed off to which successor, for a given
worktree or effort, is reconstructable — both while work is actively
in-flight and long after every process in that chain has exited and been
cleaned up. This is what makes the handoff mechanism's own health legible:
whether a chain is stuck, whether a successor never appeared, whether a
predecessor died before ever finding one.

### Single-successor discipline with recovery

A session produces at most one legitimate successor. The moment a successor
exists, getting it in front of the human or caller is the system's top
priority — outranking any other pending concern. Because a real system has
many ways for this to go wrong (a successor that never starts, a host that
never notices the request, a predecessor left indefinitely believing no one
picked up, two would-be successors racing), the mechanism includes ongoing
health-checking and recovery, not just a happy-path handoff — a stuck or
failed handoff is something the system can detect and someone can diagnose,
not a silent dead end.

## Behaviors

### Handoff reaches a successor independent of hosting

Whether an interactive host, an automated host, or no host at all is present,
a triggered handoff either reaches a running successor or resolves into an
unambiguous, actionable manual fallback. The absence of a host is a degraded
mode with a clear guarantee, never an unhandled case.

### Clean exit, not a race with orphaned state

A predecessor's retirement always ends in one of two clean outcomes: a
graceful exit that leaves no residue, or a recognized forced exit whose
residue (if any) is someone's explicit, known responsibility to clear —
never a coin flip that leaves stale state for the next session to trip over.

### The mandate is never lost to drift

A successor several handoffs deep in the same effort is exactly as aware of
its ability to hand off, and its obligation to carry the effort's goal
forward, as the very first session was. Chain depth does not erode fidelity
to the original goal or to the handoff mandate itself.

### Nothing about a handoff is a surprise to the operator

At every point where policy would trigger a handoff — advisory, nudge, or
forced — the operator-visible surface says so plainly, in language a human
reading the transcript live would understand without needing to know the
mechanism's internals.

## Non-Goals / Boundaries

- `context-handoff` does not launch, terminate, or otherwise manage Copilot
  processes; it requests a cutover and observes outcomes, but the mechanics of
  making one happen belong to whichever hosting layer is present.
- `context-handoff` does not own the durable worktree-agency state (current
  head, succession storage, claims) — that ownership sits with
  `agent-worktrees` per [`plugins/agent-worktrees`](../agent-worktrees/README.md);
  `context-handoff` is a client of that durable state, not a second copy of it.
- `context-handoff` does not require any particular hosting mechanism to exist
  or be reachable — it degrades to a manual recipe rather than hard-depending
  on one.
- `context-handoff` does not decide *for* the operator whether automatic
  handoff is desired at all — full manual-only operation is always a
  legitimate, fully supported configuration, not a degraded fallback.

## See Also

- Parent vision: [`agent-fabric`](../../agent-fabric/README.md)
- Child visions: none (leaf)
- Related visions: [`session-hosting`](../../session-hosting/README.md),
  [`plugins/agent-bridge`](../agent-bridge/README.md),
  [`plugins/agent-worktrees`](../agent-worktrees/README.md),
  [`picker`](../../picker/README.md)
- Reality docs: `plugins/context-handoff/skills/context-handoff/`,
  `plugins/context-handoff/skills/diagnosing-handoff-cutover/`,
  `efforts/active/handoff-live-cutover/`,
  `efforts/active/handoff-cutover-lifecycle-journal/`,
  `efforts/active/handoff-cutover-reload-robustness/`

## Provenance

- **2026-09-13** — First authored, kicking off a context-handoff overhaul
  effort. Derived through an isolated adversarial re-embodiment (a blind
  design derivation from stated goals/constraints alone, judged against the
  plugin's real current implementation, the existing `agent-fabric` /
  `session-hosting` / `plugins/agent-bridge` / `plugins/agent-worktrees`
  visions, and three in-flight efforts) to separate genuine should-be intent
  from spec-level mechanism and from gaps already tracked as pending work.
