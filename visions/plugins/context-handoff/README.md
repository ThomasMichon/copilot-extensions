# context-handoff — Vision

- **Subject:** the `context-handoff` plugin — the policy owner for continuing a
  Copilot agent's work across a context-window boundary
- **Scope:** leaf (child of [`agent-fabric`](../../agent-fabric/README.md))
- **Status:** Active
- **Last revised:** 2026-10-07
- **Reality docs:** [plugin README](../../../plugins/context-handoff/README.md),
  [continuation skill](../../../plugins/context-handoff/skills/context-handoff/SKILL.md),
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

- **Native-compatible continuity** — when context management is enabled in
  effective settings or otherwise detected, continuity policy enhances native
  checkpoint/recovery and keeps a stable session rather than creating another
  session solely for context refresh. Operational pressure handling backs off
  competing custom cutover when native support is viable; explicit handoff and
  unsupported-venue recovery remain available. Settings select behavior, not
  permission to invent unavailable tools or bypass policy. Compaction remains
  emergency recovery rather than the planned continuity path.

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
- **Durable baton and recovery locator** — the continuation content survives
  the predecessor independently of a live extension or reachable coordinator.
  A short recovery locator identifies that content; it never substitutes for
  loading the brief. Storing a baton preserves an option to continue, while
  requesting pickup is a separate act.
- **Objective and authority boundary** — the handoff preserves the original
  completion gate and the authority of the session transferring it. An
  objective owner continues the campaign; a bounded delegate continues only
  its assigned scope. Neither a finished phase nor a successful pickup silently
  expands that authority or declares the parent objective complete.

## Features

### Escalating pressure response

As a session's context utilization climbs, the agent receives a series of
escalating signals rather than a single trigger: an early advisory that
handoff preparation should begin, a firmer nudge to hand off at the next
natural break, and — because an agent cannot be relied on to always act on a
nudge in time — when automatic handoff is authorized, a final, non-negotiable
point past which the system forces the handoff itself rather than continuing
to ask. All three tiers are sensible defaults, never hard-coded assumptions
the operator cannot move (see
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

When a valid active effort already holds the objective, plan, and journal, the
baton references that durable authority and carries only the immediate relay
delta: the next slice, decisions, blockers, and in-flight obligations. Without
such an effort, a standalone brief carries the full continuation contract.
Compactness must remove duplication, never responsibility.

### Extension-independent recovery

An unavailable or disconnected session extension must not strand a saved
baton. Storage, explicit pickup, lineage inspection, and cancellation remain
reachable through a non-extension surface with the same ownership semantics.
Recovery is scoped to the identified worktree and baton, not a global guess at
which conversation to continue. Cancellation retires pending continuation
without consuming it or falsely declaring the original work complete.

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

Manual storage, triggering, and consumption remain available even when
automatic behavior is disabled. Recording a requested handoff for lineage
does not itself authorize an automatic successor: live cutover needs explicit
opt-in, while a deliberately manual continuation remains fully tracked.

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

### Safe transfer of background work

Background work has explicit ownership at the boundary. Agent-driven handoffs
capture useful results and quiesce owned writers and schedules before preparing
the successor's worktree and final brief; work requiring continuation is named
for deliberate successor-side re-arming. Unrelated work is not stopped.

Emergency capture must remain bounded so context loss cannot erase the baton.
When a last-chance handoff cannot establish that work has quiesced, it preserves
that uncertainty and the recovery obligation explicitly rather than claiming
an empty inventory. The transfer must prevent unresolved predecessor work from
silently racing a successor; meeting this guarantee requires cooperation with
the owners of background execution, not moving process hosting into
`context-handoff`.

### Evidence-faithful continuation

A brief is an evidence-bearing transfer, not an unquestioned completion claim.
Preparation reconciles self-identified open threads with later resolutions and
checks the session's owned obligations before saying nothing remains.
Pickup checks the inherited objective and outstanding work against available
predecessor and worktree evidence before accepting a claim of completion.
An unavailable or bounded history view is disclosed as such, never treated as
proof that no older responsibility exists.

Worktree preparation preserves unrelated changes and in-progress source-control
operations. A failed or unsafe refresh is carried as an explicit successor
obligation, not a reason to lose the baton or imply that the worktree is current.

### Exclusive pickup with attributable lineage

Claiming a baton grants continuation responsibility to one successor, with
retry-safe recovery for that same claimant. A competing pickup reports who
already holds it rather than replaying the work or interpreting failure as
completion. Where a durable worktree authority exists, pickup links the actual
baton, predecessor, and successor through that authority; a diverged head is
not overwritten by guessing. A failed lineage update remains visible with a
recovery obligation, distinct from successful delivery of the brief.

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
- Applicable service contracts:
  [`plugin-services`](../../plugin-services/README.md) — graceful composition,
  relationship diagnosability, and replaceable payloads. Service-runtime,
  listener, and resident-daemon lifecycle contracts belong to the providers
  supplying those capabilities, not to a second runtime in this policy plugin.
- Reality docs: [plugin README](../../../plugins/context-handoff/README.md),
  [continuation skill](../../../plugins/context-handoff/skills/context-handoff/SKILL.md),
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

- **2026-10-07** — Folded back durable storage distinct from pickup signaling,
  explicit manual operation independent of automatic policy, extension-free
  recovery, exclusive attributable pickup, and effort-backed compact
  continuation. Sharpened continuity into preservation of objective authority,
  evidence-faithful completeness checks, and safe transfer of background work.
  These guarantees preserve the policy/hosting boundary: runtime owners supply
  execution mechanics, while the baton carries responsibility and uncertainty.
