# Mux Companion — Vision

- **Subject:** the purpose-first Textual control surface for a muxed worktree:
  a hotkey-summoned dialog, an alternate summary pane, and the Mux-bind layer
  that delivers them.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-24
- **Reality docs:** [plugins/agent-worktrees/docs/cli-reference.md](../../plugins/agent-worktrees/docs/cli-reference.md) (status-segment / status-updater), [plugins/agent-worktrees/docs/mux.md](../../plugins/agent-worktrees/docs/mux.md), [plugins/agent-worktrees/docs/worktree-lifecycle.md](../../plugins/agent-worktrees/docs/worktree-lifecycle.md)

## Purpose & Intent

A muxed worktree session's status bar answers "what state is this worktree in
*right now*" at a glance — but it cannot answer "*why*," it cannot show what
came before the session currently attached, and it offers no way to act on any
of that without leaving the pane for the full Worktree Picker. The operator is
left context-switching out of the exact terminal they are trying to stay in.

The Mux Companion closes that loop **from inside the session**. Its primary
job is to make a worktree's purpose, current work and resource commitments
legible at a glance. One hotkey summons a compact Textual dialog; a switchable
summary pane offers a calmer alternative to the CLI's scrolling output.
Both consume the same authoritative sources as graphical companion clients,
not a second interpretation of sessions, claims or progress.

Summary and Claims take priority over session selection for handoff. Session
lineage, prior-session recovery and the deliberate break-glass head override
remain available as secondary capabilities, not removed or made automatic.
The Companion stays rooted in one worktree and its related work, rather than
becoming a replacement for the fleet-wide Picker.

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
  Its key-table registration mechanism is necessarily platform-asymmetric:
  psmux runs one server PER session, so a worktree-scoped root-table binding
  (`source-file -t <session>`) is inherently session-scoped and safe by
  construction; tmux runs ONE SHARED server for every session on the machine,
  so an unscoped worktree-specific binding would leak onto the operator's own
  unrelated tmux sessions too -- the existing opt-in
  `apply-mux-keybinds.sh`/`.ps1` precedent (and `psmux-passthrough.conf`'s own
  "psmux-only" note) already draws this boundary; Mux-bind's tmux delivery
  must honor it (e.g. a session-name-conditioned bind), not bypass it.
- **The Companion** — a Textual Worktree Manager surface with a disposable
  popup and an alternate summary-pane presentation. It shows purpose,
  Summary, Claims and work topology from their owning sources. Presentation
  state is not a rival worktree, claims, disposition or accounting ledger.
- **Work topology** — a compact graph of the current worktree's agents,
  bridges, downstream worktrees and pull requests, annotated with reported
  phases and high-level states. It distinguishes a dependency or allocation
  from evidence of progress or completion.
- **Mux views and attention** — the CLI and summary are views of the same
  worktree, not separate agent sessions. Clickable view tabs and keyboard
  switching preserve the CLI; question and idle indicators keep requests for
  operator attention visible even while the CLI is hidden.
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

### purpose-and-claims-at-a-glance
The default dialog and summary view lead with the worktree's purpose,
reported Summary and Claims. Held resources are identifiable, not merely a
count, and an absent or unavailable source is distinct from an empty ledger.

### worktree-summary-topology
A succinct graph-style view shows reported work phases, allocated sub-agents
and bridges, downstream worktrees and open pull requests, with accumulated
tool-call statistics. Users can understand overall work without reading a
raw conversation feed. Graphical and terminal companions share source
meaning, identity and freshness rather than growing competing backends.

### switchable-cli-and-summary-views
The operator can flip between the running Copilot CLI and an overall summary
pane in the same mux window, through keyboard controls and clickable view
tabs. Switching does not restart, replace, type into or take custody of the
CLI session. Returning to the CLI restores its existing interaction.

### permanent-claims-status-line
The mux status bar reserves a second permanent line for explicit Claims
information. View tabs and question/idle attention indicators remain
discoverable alongside that information, including while Summary is shown.

### cli-attention-while-summarizing
When the CLI asks a question or goes idle, the summary view or mux toolbar
shows that fact and offers an explicit return to CLI. Only the CLI's owning
interaction surface accepts answers; an indicator is not permission to
auto-answer or infer completion from idleness.

### hotkey-summoned-status-explainer
From inside any muxed worktree session, one hotkey summons a compact view
showing the worktree's full id and a plain-language explanation of its
current status — the underlying sub-state facts (checkpoint activity,
upstream-containment, dirtiness, open claims, pending handoff — see
[plugins/agent-worktrees §Concepts & Components/Derived status](../plugins/agent-worktrees/README.md#derived-status))
and, individually, which of them are currently confirmed versus stale.

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

### mux-bind-keybind-relay
Mux-bind delivers the hotkey-summoned-status-explainer Feature concretely: a
session-scoped root-key-table binding (Ctrl+K) registered on every muxed
worktree session at launch/join, dispatching a Mux popup that runs the
Companion. Session-scoped means it is applied per-session (psmux: one server
per session, via `source-file`; tmux: the server-wide opt-in passthrough
already required for any worktree-specific root-table binding) and never
leaks onto a non-worktree Mux session.

### mux-bind-clickable-status-region
The status-right segment Mux-bind pushes carries a named, mouse-clickable
range (tmux/psmux `#[range=user|...]` + a conditional `MouseDown1Status`
dispatch keyed on `#{mouse_status_range}`) that launches the same Companion
popup the Ctrl+K hotkey does — an additional, discoverable summon path for an
operator who has not learned the hotkey, not a replacement for it. Revises the
prior "Not a general status-bar click framework" boundary (see Provenance):
the Companion itself stays a single, specific popup, not a click-framework for
arbitrary status-bar regions, but THIS one region's click behavior is now in
scope.

### manual-cutover-trigger
While `.context-handoff/config.yaml`'s `mode` is not `auto`, the automatic
live-cutover path (`handoff-live-cutover`) never arms — by design (see
Behaviors/companion-cutover-trigger-is-explicit-and-never-inferred). The
Companion offers an explicit "Cut over" action, shown only when a pending
handoff baton is detected for the current worktree, that runs the SAME
claim → spawn-successor → retire-predecessor machinery the automatic path
uses, on demand, for exactly one worktree, only on direct human command. This
exists for diagnosing and exercising the live-cutover mechanism while
`mode: auto` is not (yet) the configured default — never as a silent or
inferred substitute for that configuration.

### post-cutover-head-verification
The Companion's session-lineage view marks whether the durable, resolved head
(the session the Worktree Picker will resume) matches the session the
operator most recently cut over to or manually resumed — both the previous and
new session are listed, so a mismatch is visible immediately rather than
discovered later when Resume launches the wrong one. The view is refreshable
on demand (a key, not just at popup-open), so reopening the Companion after a
manual `/clear` + paste-`HANDOFF_SEED` resume shows current state without
exiting Mux. Detecting a mismatch is as far as the Companion goes — the
Companion does not repair a mismatched head or file a report about it; the
operator directs the CURRENT session to do that, per
Behaviors/companion-detects-never-repairs.

## Behaviors

### mux-bind-is-a-pure-relay
Mux-bind only relays status values the status core already computed, and
dispatches commands it did not itself decide the meaning of. It never computes
status, closure, or lineage data itself, and it never encodes handoff or
cutover policy — that remains owned above it, per
`agent-fabric` §Behaviors/handoff-orchestrated-across-ledger-and-host.

### companion-reads-handoff-schema-never-drives-it
The Companion may read and display a pending context-handoff baton's headline
for situational awareness, and — only on explicit human command, never
inferred or automatic — invoke the same graceful cutover machinery the
automatic path uses (see companion-cutover-trigger-is-explicit-and-never-
inferred). It never composes a handoff, decides on its own that one should
happen, or silently arms/consumes one. A graceful handoff remains exclusively
context-handoff's *authored* path; the Companion only ever offers an explicit
button for a baton that ALREADY exists, never originates one. The raw
force-head override remains the *other*, deliberately blunter path for when a
graceful cutover is not wanted or not possible — never a shortcut through the
first, and never confused for it in the lineage record.

### companion-cutover-trigger-is-explicit-and-never-inferred
The manual-cutover-trigger action requires a direct human button press every
time; it is never offered as a consequence of merely opening the Companion,
never auto-fires on a timer or on detecting a pending baton, and never
silently upgrades `mode: manual-only` to behave like `mode: auto` beyond that
one, explicit, single invocation. The underlying `force` bypass this relies on
(see `plugins/context-handoff` §trigger_handoff) is itself never inferred
from config; the Companion is one caller among possibly others (e.g. a plain
CLI invocation) that must each opt in explicitly.

### companion-detects-never-repairs
Post-cutover-head-verification is read-only: the Companion computes and shows
whether resolved head matches the expected session, but never writes a repair
itself and never files a report on the operator's behalf. A detected mismatch
is the operator's cue to direct the current session to patch it up and file
the diagnostic themselves, not an action surface the Companion owns.

### override-is-visible-and-attributable
A raw force-head override is never silent. The durable lineage records that it
happened, who forced it, and what session(s) it displaced, so a later viewer
of the lineage sees a break-glass override for exactly what it was rather than
an ordinary succession.

### popup-is-disposable
The Companion runs as an on-demand process in its own Mux popup, holding no
persistent state of its own; it costs nothing when it is not summoned and
leaves nothing behind when it closes.

### summary-is-an-observer
Summary rendering does not change claims, start or retire agents, advance
disposition, infer phases from prose, or replace accounting totals with
locally guessed counts. Optional unavailable sources remain visibly
unavailable without hiding independently usable Summary or Claims.
Refreshing a visible view is bounded and does not create a second fleet
poller when an owning producer already supplies the data. An alternate pane
has an explicit mux-owned lifetime, separate from the disposable popup.

### one-seam-many-commands
A new Mux-bound custom command is a Mux-bind registration, not a bespoke
wiring job repeated per command. The Companion is the seam's first consumer,
not its only possible one.

## Non-Goals / Boundaries

- **Not a handoff orchestrator.** The Companion does not replace or
  reimplement context-handoff's cutover choreography, and it never composes a
  baton or decides on its own that a handoff should happen — it only ever
  invokes the graceful cutover path for a baton that already exists, and only
  on an explicit human button press (manual-cutover-trigger). It is not a
  second way to author or arm a handoff; it is a second way to TRIGGER one
  someone (or something) else already prepared.
- **Not an arbitrary status-bar click framework.** Named Companion launch
  and view-tab regions are in scope; arbitrary third-party click targets or
  unrelated worktree bindings are not.
- **Not a replacement for the Worktree Picker.** The Companion explains one
  worktree and its related topology, not a second fleet-management surface.
- **Not a new session-hosting execution path.** The Companion acts on the same
  local session/process primitives the Picker's existing Stop/Reclaim actions
  already use; it introduces no new venue, provider, or execution mechanism.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Related visions: [picker](../picker/README.md), [plugins/agent-worktrees](../plugins/agent-worktrees/README.md), [plugins/context-handoff](../plugins/context-handoff/README.md) (schema-read + explicit-trigger relationship — see `mux-companion-manual-cutover-diagnostics`), [session-hosting](../session-hosting/README.md)
- Reality docs: [plugins/agent-worktrees/docs/cli-reference.md](../../plugins/agent-worktrees/docs/cli-reference.md), [plugins/agent-worktrees/docs/mux.md](../../plugins/agent-worktrees/docs/mux.md), [plugins/agent-worktrees/docs/worktree-lifecycle.md](../../plugins/agent-worktrees/docs/worktree-lifecycle.md)

## Provenance

- **2026-10-24** — Expanded from recovery-first popup to purpose-first
  Summary and Claims, a compact work-topology view, switchable CLI/summary
  panes, explicit attention, two-line status and view tabs. Session-selection
  and handoff recovery remain secondary rather than being removed.
- **2026-10-05** — Added *mux-bind-keybind-relay* and
  *mux-bind-clickable-status-region* Features (effort `mux-bind-relay`): the
  actual Ctrl+K root-key-table binding and the status-right clickable region,
  giving `hotkey-summoned-status-explainer` its first concrete delivery
  mechanism (previously specified but unimplemented -- no bind-key/
  display-popup wiring existed anywhere in the codebase). Revised the "Not a
  general status-bar click framework" Non-Goal: it previously read as
  forbidding ANY status-bar click behavior; the actual, narrower boundary is
  that the Companion's one clickable region is in scope, a general click
  framework across arbitrary segments is not. Status promoted Draft -> Active
  now that a real implementation effort is underway.
- **2026-09-27** — Added *manual-cutover-trigger* and
  *post-cutover-head-verification* (effort
  `mux-companion-manual-cutover-diagnostics`, #4369): an explicit,
  human-gated on-demand invocation of the SAME graceful cutover machinery
  `handoff-live-cutover` already runs automatically under `mode: auto`, for
  diagnosing/exercising it while that mode is not yet the operator's
  configured default. Revised the "Not a handoff orchestrator" Non-Goal
  accordingly — it was previously read as forbidding this entirely; the
  actual, narrower boundary this vision has always meant is that the
  Companion never *authors or decides* a handoff, only ever *triggers* one
  someone else already prepared, and only on direct human command.
- **2026-09-15** — Updated *hotkey-summoned-status-explainer* to point at the
  decomposed sub-state model (plugins/agent-worktrees §Derived status)
  instead of naming the FINAL/MERGED split directly, following that vision's
  refinement of a single completed/not-completed split into independently
  named, independently freshness-marked facts.
- **2026-09-14** — Conceived from an operator session exploring a Mux-summoned
  companion dialog (Ctrl+K) for worktree status/lineage and a raw,
  break-glass session-head override; mined into this vision, anchored on the
  existing `agent-fabric` break-glass-override behavior and the
  `worktree-finality-and-obligations` closure descriptor.
