# Mux Companion — Vision

- **Subject:** the in-session control surface for a muxed worktree — a
  hotkey-summoned companion dialog, and the Mux-bind layer that delivers it and
  any future custom Mux-side command.
- **Scope:** leaf
- **Status:** Draft
- **Last revised:** 2026-09-14
- **Reality docs:** [plugins/agent-worktrees/docs/cli-reference.md](../../plugins/agent-worktrees/docs/cli-reference.md) (status-segment / status-updater), [plugins/agent-worktrees/docs/mux.md](../../plugins/agent-worktrees/docs/mux.md), [plugins/agent-worktrees/docs/worktree-lifecycle.md](../../plugins/agent-worktrees/docs/worktree-lifecycle.md)

## Purpose & Intent

A muxed worktree session's status bar answers "what state is this worktree in
*right now*" at a glance — but it cannot answer "*why*," it cannot show what
came before the session currently attached, and it offers no way to act on any
of that without leaving the pane for the full Worktree Picker. The operator is
left context-switching out of the exact terminal they are trying to stay in.

The Mux Companion closes that loop **from inside the session**: one hotkey
summons a small, disposable dialog that explains the current status in plain
language, shows the worktree's session lineage, offers to resume a prior
session, and — deliberately, as a distinct and blunter action — can force a
different session to become this worktree's head immediately, reclaiming the
pane. It is a narrow, single-worktree, single-purpose companion, not a
replacement for the Picker.

Delivering it durably requires separating two things the status-updater loop
currently does in one place: **computing** a worktree's status (which stays the
status core's job) and **pushing** that status into the terminal multiplexer
(which becomes the job of a new, narrow relay). That relay — Mux-bind — is also
the seam through which the Companion, and any future Mux-side custom command,
reaches the multiplexer. Going forward, the multiplexer relationship belongs to
the Worktree Manager, not to `agent-worktrees` directly.

## Concepts & Components

- **Status core** (unchanged ownership: `agent-worktrees`) — computes a
  worktree's segment text/style, closure descriptor, and session lineage. It
  remains the sole source of truth for *what* the status is; this vision adds
  no new computation here, only a new consumer of the existing output.
- **Mux-bind** — a narrow relay, owned by the Worktree Manager, that
  subscribes to the status core's already-computed values and pushes them into
  the running Mux session (the `set-option` leg the status-updater loop
  performs today). It has no opinion on *what* a status means; it only
  delivers it. It is also where a Mux-side custom command — a hotkey today,
  potentially other Mux-native triggers later — is registered and dispatched.
- **The Companion** — a small, separate program (a Worktree Manager
  subcommand), launched on demand inside a Mux popup pane by a Mux-bind
  hotkey. It reads the status core's data for the current worktree, renders
  the explainer/lineage view, and carries out the one action a companion
  offers: forcing a specific session to become head. It holds no persistent
  state and exists only for the lifetime of the popup.
- **Session lineage** — the worktree's durable head/succession chain
  (`agent-worktrees`' authoritative head-and-lineage). The Companion presents
  it read-only, plus — for situational awareness only — the headline of any
  pending context-handoff baton, read by schema, never acted on.
- **Break-glass head override** — the fabric already envisions a deliberate
  override of its "one current session" rule for the exceptional case (see
  `agent-fabric` §Behaviors/single-current-session-per-worktree). The
  Companion's force-head action *is* that override, given a concrete, in-pane
  surface: it reassigns the durable head directly and terminates the sessions
  it displaces, raw — it does not compose, signal, or wait on a graceful
  handoff.

## Features

### hotkey-summoned-status-explainer
From inside any muxed worktree session, one hotkey summons a compact view
showing the worktree's full id and a plain-language explanation of its current
status — including, for a completed worktree, *why* it reads `FINAL` versus
`MERGED` (which evidence is stale, which claims or follow-ups are still held).

### session-lineage-visibility
The same view lists the worktree's session lineage — the durable chain of
sessions that have held this worktree's head — so the operator can see what
preceded the one currently attached without leaving the pane.

### in-pane-session-recovery
From the lineage view, an operator can resume a specific prior session
directly, without opening the full Picker.

### break-glass-head-override
The Companion can force a specific session to become the worktree's current
head immediately and terminate the session(s) it displaces. This is a
deliberate, raw override for when a graceful handoff is not wanted, not
possible, or beside the point — never the default or only path, and never
silent (see Behaviors/override-is-visible-and-attributable).

### injectable-mux-commands
Mux-bind's command-registration seam is general, not a one-off wire for the
Companion. Any current or future custom Mux-side trigger is registered through
the same seam and dispatched the same way, so adding another is a
registration, not a bespoke integration.

## Behaviors

### mux-bind-is-a-pure-relay
Mux-bind only relays status values the status core already computed, and
dispatches commands it did not itself decide the meaning of. It never computes
status, closure, or lineage data itself, and it never encodes handoff or
cutover policy — that remains owned above it, per
`agent-fabric` §Behaviors/handoff-orchestrated-across-ledger-and-host.

### companion-reads-handoff-schema-never-drives-it
The Companion may read and display a pending context-handoff baton's headline
for situational awareness. It never composes, signals, or consumes one, and
never triggers context-handoff's own cutover machinery. A graceful handoff
remains exclusively context-handoff's path; the Companion's override is the
*other*, deliberately blunter path — never a shortcut through the first, and
never confused for it in the lineage record.

### override-is-visible-and-attributable
A raw force-head override is never silent. The durable lineage records that it
happened, who forced it, and what session(s) it displaced, so a later viewer
of the lineage sees a break-glass override for exactly what it was rather than
an ordinary succession.

### popup-is-disposable
The Companion runs as an on-demand process in its own Mux popup, holding no
persistent state of its own; it costs nothing when it is not summoned and
leaves nothing behind when it closes.

### one-seam-many-commands
A new Mux-bound custom command is a Mux-bind registration, not a bespoke
wiring job repeated per command. The Companion is the seam's first consumer,
not its only possible one.

## Non-Goals / Boundaries

- **Not a handoff orchestrator.** The Companion does not replace, reimplement,
  or invoke context-handoff's cutover, baton composition, or pending-pickup
  signal. It only reads that baton's schema for display.
- **Not a general status-bar click framework.** The Companion is
  hotkey-summoned. Whether Mux-bind's command seam ever extends to clickable
  status-bar regions is future scope for Mux-bind, not a requirement this
  vision depends on.
- **Not a replacement for the Worktree Picker.** The Companion is a narrow,
  single-worktree, single-purpose view — explain, show lineage, recover, or
  break-glass override — not a multi-worktree management surface.
- **Not a new session-hosting execution path.** The Companion acts on the same
  local session/process primitives the Picker's existing Stop/Reclaim actions
  already use; it introduces no new venue, provider, or execution mechanism.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Related visions: [picker](../picker/README.md), [plugins/agent-worktrees](../plugins/agent-worktrees/README.md), [plugins/context-handoff](../plugins/context-handoff/README.md) (schema-read relationship only), [session-hosting](../session-hosting/README.md)
- Reality docs: [plugins/agent-worktrees/docs/cli-reference.md](../../plugins/agent-worktrees/docs/cli-reference.md), [plugins/agent-worktrees/docs/mux.md](../../plugins/agent-worktrees/docs/mux.md), [plugins/agent-worktrees/docs/worktree-lifecycle.md](../../plugins/agent-worktrees/docs/worktree-lifecycle.md)

## Provenance

- **2026-09-14** — Conceived from an operator session exploring a Mux-summoned
  companion dialog (Ctrl+K) for worktree status/lineage and a raw,
  break-glass session-head override; mined into this vision, anchored on the
  existing `agent-fabric` break-glass-override behavior and the
  `worktree-finality-and-obligations` closure descriptor.
