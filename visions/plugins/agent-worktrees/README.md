# agent-worktrees — Worktree Lifetime & Agency State — Vision

- **Subject:** **agent-worktrees** as the durable authority for repository and
  worktree identity, worktree-lifetime agency state, relationships, claims,
  obligations, disposition, and source-control completion.
- **Scope:** leaf (concrete component; child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-09-20
- **Reality docs:** the agent-worktrees plugin `docs/`
- **Supersedes / superseded by:** none

## Purpose & Intent

agent-worktrees makes a repository worktree a durable, accountable **unit of
agency**. It answers which workspaces exist, what objective each worktree carries,
which sessions or controllers have acted for it, what resources it owns, whether
work remains, and whether its source-control lifecycle is safely complete.

Its north star is a passive, host-neutral state model. A worktree and its
responsibility survive terminals, processes, user interfaces, Copilot products,
and execution hosts. The same worktree record remains authoritative whether an
agent is driven through Copilot CLI in a multiplexer, an ordinary process, ACP,
the Copilot SDK, a graphical application, or a third-party rig.

agent-worktrees therefore owns **durable worktree-lifetime truth**, not the
interactive process that happens to animate it. Execution hosts publish bounded,
attributable observations and lifecycle assertions into the record; they do not
become competing owners of worktree identity or responsibility. Conversely,
agent-worktrees never needs to understand every way a Copilot process can be
launched, presented, prompted, reattached, or retired.

## Concepts & Components

### Repository and worktree identity

Repositories, source checkouts, worktrees, branches, remotes, contribution
contracts, and management classes form the stable spatial identity on which the
rest of the fabric coordinates. Paths vary by machine; identity and declared
relationships remain stable. agent-worktrees is the **canonical owner** of the
name-to-path mapping for a registered repo (its `repos.yaml`/`projects.yaml`):
every other component in the suite that needs a repo's current path resolves
it by name through agent-worktrees at the moment of use, rather than copying a
path into its own state — the suite-wide
[*identity-resolves-by-name-not-path*](../../plugin-services/README.md#identity-resolves-by-name-not-path)
guarantee, with agent-worktrees as its repo-identity anchor. A worktree
checkout's own directory name is a per-session, per-machine identifier that
must never be adopted downstream as if it were the repo's registered name.

A registered repo's identity extends beyond its path to its **contribution
posture**: the concrete answer to "how may I propose and land a change here,
and what is my standing to do so." agent-worktrees is the single durable
record of that posture for every repo a project relates to. A project never
needs a second, parallel catalog of the repos it works with, their
contribution rules, or an operator's standing in each — whatever such a
project previously tracked on its own converges into this one record instead.

### Related-repo relationship and contribution posture

Beyond a registered repo's own identity, agent-worktrees records how a project
**relates** to each other repo it touches: the relationship's nature (the
project owns it, contributes to it, merely consumes it, and so on), where work
on it happens, and who else mediates that work. A related repo's contribution
posture is a first-class part of this record, derived primarily from the
target repo's own authoritative signals (its stated branch/review contract,
required approvals, fork-vs-branch requirement, and similar facts it already
publishes about itself) rather than from a hand-maintained duplicate. Where a
repo's real contribution etiquette cannot be read off any signal — a courtesy
convention, an expected wait before self-merging, how to coordinate with other
concurrent contributors — that nuance is layered on as curated narrative
alongside the derived facts, never invented to fill a gap the signals leave
open.

### The worktree as a unit of agency

A worktree record carries the objective-facing state that should outlive any one
session: current focus, asserted disposition, participants, session/controller
relationships, succession lineage, claims, obligations, and completion state.
The worktree is not itself a process. It is the durable vessel to which one or
more execution legs may bind over time.

### Execution legs and observations

An execution leg is an externally hosted session acting for a worktree. The
record identifies the leg, its provider, its relation to earlier and later legs,
and the lifecycle assertions or observations the provider can honestly supply.
Provider-specific process, pane, window, connection, and protocol identities
remain opaque host evidence rather than becoming worktree semantics.

### Binding and control are distinct

A session may bind as the current execution leg of one worktree while controlling
other worktrees or pull-request vessels. Control never impersonates binding.
Both relations are explicit, reciprocal where possible, and durable enough for
recovery.

### Current head and succession

The worktree carries one authoritative current head for its execution lineage.
Handoff records predecessor and successor relationships independently of the
mechanism that launched either session. Moving the head is a deliberate,
fenced state transition; a host reporting that it started a process is not by
itself proof of takeover.

### Claims, leases, and obligations

The worktree owns the ledger of resources it creates or adopts: related
worktrees, pull requests, environments, sessions, connections, and other
scarce resources. Exclusive access is fenced, ownership is answerable in both
directions, and finalization is gated on settlement or an explicit transfer.

### Pull-request capability

A pull request is more than a claimed resource on a worktree's ledger: it is
the subject of a provider-neutral capability in its own right, covering both
the author's and the reviewer's side of its life, addressable for a repo
regardless of local checkout, and verifiable against a fabricated provider
with the same confidence as a real one. See
[pull-requests](pull-requests/README.md) (child vision).

### Source-control completion

Creation, isolation, contribution-policy enforcement, publication, finalization,
and prune safety remain worktree-lifetime concerns. They do not depend on which
interactive host ran the agent that produced the change.

### Post-finalization archival

A finalized worktree whose on-disk checkout is reclaimed does not vanish from
the durable record — it transitions to a distinct **archived** state, strictly
after finalization, that tombstones its identity, lineage, and session history
rather than deleting them outright. Archival answers "what happened here" for a
worktree that no longer exists on disk, without pretending that worktree is
still live, resumable, or a member of the ordinary active set. It is the
natural terminus of the lifecycle — reap discards the checkout, never the
record of what the checkout was.

### Derived status

Overall status is a reduction over independently owned facts: source-control
state, claims and obligations, asserted disposition, effort focus, relationships,
and fresh provider observations. No observer writes the aggregate verdict.
Stale or absent execution observations reduce fidelity without erasing durable
responsibility. Computing this reduction is always available **on demand** —
direct, in-process computation is a correct degrade path, never a failure — but
it is not the steady-state target: whenever a resident accelerator is
reachable, a reader is a **thin, ref-counted subscriber** of it rather than an
independent computer of the same facts. A reader that finds none running boots
one on demand, waits for it to publish its reachable address, subscribes, reads
the answer, and exits; the accelerator's own lifetime is governed by its
subscriber count plus a bounded linger (not a fixed idle timer alone), so a
burst of callers shares one warm computation instead of each paying the cost —
and racing each other — independently. It remains an accelerator over the same
durable facts, never a second writer of the aggregate and never new
process-management authority. Where one runs, it is exactly **one** per host
regardless of how many worktrees, sessions, or CLI invocations reach it — the
suite-wide
[*process-count-scales-with-services-not-sessions*](../../plugin-services/README.md#process-count-scales-with-services-not-sessions)
guarantee, generalized here from session-lifecycle hooks to every ordinary
reader.

The reduction's inputs are a fixed, small set of independently named facts —
whether the session has held activity since its last checkpoint, whether the
worktree's content is contained in its upstream default branch, whether the
working tree is locally dirty, whether any claim against the worktree remains
open, and whether a handoff is pending pickup — never a hand-authored combined
enum that grows a new case per situation. The costliest of these,
upstream-containment, depends on evidence (the repo's remote-tracking refs)
shared by every sibling worktree of the same repo; the resident accelerator
keeps that evidence current through its own periodic sweep and through
prompt, operation-triggered recomputation (a merge landing, a push, a sync, a
claim settling) — rather than trusting whichever individual caller happened
to request a fetch on its own call. Any fact whose current truth cannot be
confirmed is marked as such in the rendered result — never silently reported
as certain, and never given a separate, whole state of its own standing in
for "unverified."

### Declarative presentation contribution

agent-worktrees contributes machine-readable worktree semantics and actions to
optional human or agent control planes. Presentation clients render those
semantics and may invoke a selected execution host, but do not acquire ownership
of the worktree record.

## Features

### durable-worktree-agency-record

Each managed worktree carries a durable, bounded record of its identity,
objective-facing status, execution lineage, claims, obligations, and
source-control completion.

### host-neutral-execution-binding

Execution legs from different Copilot products and hosting technologies can bind
to the same worktree model without agent-worktrees learning their launch or
interaction mechanics.

### authoritative-head-and-lineage

The current execution head and reciprocal predecessor/successor lineage are
durable, explicit, and independent of process timestamps or UI attachment.

### asserted-disposition

The acting agent deliberately states whether the worktree is resolved or still
has actionable follow-up. Git cleanliness, process exit, and session quietness
never manufacture that semantic conclusion.

### accountable-resource-ledger

Every resource a worktree creates or adopts remains attributable, fenced where
exclusive, and visible until settled or explicitly transferred.

### contribution-aware-lifecycle

Worktree publication and completion honor each repository's own contribution
contract, preserve isolated editing, and prove content safe before cleanup.

### auto-discovered-contribution-posture

For each repo a project relates to, agent-worktrees derives its contribution
posture — how a change may be proposed there, and what standing the operator
has to land it — primarily from that repo's own authoritative signals rather
than a hand-maintained duplicate a project curates separately. Curated
narrative supplements only the etiquette a signal cannot express; it never
substitutes for a derivable fact.

### ambient-cross-repo-contribution-guidance

A session working across related repos receives, unprompted, a concise brief
of its role and contribution posture for every repo relevant to its current
work — through the same ambient guidance channel that already carries other
session-scoped context — rather than requiring an explicit lookup before the
operator or agent can act correctly.

### provider-observation-ingestion

Execution hosts may publish bounded, attributable lifecycle and activity
observations. Each provider owns only its observation slot; the worktree state
owner derives the aggregate.

### provider-owned-worktrees-surface

The Worktrees presentation surface is described through machine-readable
semantics that any compatible control plane can render without importing the
engine or persisting a second copy of its state.

### external-status-consumer-contract

Any other capability that needs a worktree's current status — a Tasks-board
status card, a dashboard, a notification — reads the resident accelerator's
fast, coalesced cache as its preferred read path, rather than independently
recomputing git state, session lineage, liveness, or the claims graph, or
polling agent-worktrees with its own subprocess on a hot per-render path.
Per the suite-wide work-coalescing-singleton pattern, this is **warmth, not
truth**: the accelerator owns no fact a consumer couldn't otherwise derive
itself, it only saves everyone from separately paying to recompute the same
shareable answer. A consumer's read is answered from state the
accelerator's own sweep and operation-triggered recompute already keep
current, not freshly (re)derived per caller — but a stale or unreachable
accelerator is never treated as authoritative over a fresher direct answer.
Rich conversation/message history is explicitly excluded from this cache
(see *Not a transcript or event warehouse* below) — a consumer that wants
recent messages pulls them on demand from the owning session host instead.
When no accelerator is reachable, this contract degrades the same way
*Derived status* already does for any other reader: direct, in-process
computation of the same facts is a correct fallback, never a hard failure.
When even that direct computation cannot confirm a fact (its own source is
unreachable too), the consumer reports that fact as unconfirmed rather than
silently omitting it or guessing — the same *uncertainty-is-marked-not-
multiplied* discipline the accelerator itself follows, never a distinct
"cache-miss" state of its own.

### registered-by-default-listing

Every enumeration of worktrees — a listing command, a session-to-worktree
lookup, a fleet-wide catalog — defaults to the **registered** set: worktrees
agent-worktrees actively tracks, excluding those that have transitioned to
**archived**. An archived worktree's durable record remains queryable by its
own identity (it is never deleted), but it never appears in a default listing
alongside active work — surfacing it requires an explicit ask.

### decomposed-status-facts

The status a worktree renders is a reduction over a fixed, small set of
independently named facts — activity since the last checkpoint,
upstream-containment, local dirtiness, open claims, and pending handoff —
never a hand-authored combined enum that grows a new case per situation.

### continuously-revalidated-freshness

The costliest fact — whether a worktree's content is contained in its
upstream default branch — is kept current by the resident accelerator itself:
a periodic background sweep (on the order of once a minute, not once per
render) plus prompt, operation-triggered recomputation, rather than trusting
whichever caller happened to request a fetch on its own call.

### repo-scoped-freshness

Sibling worktrees of one repo share the same remote-tracking refs, so
upstream-containment freshness is tracked once per repo, not once per
worktree. Any fetch — a background sweep, a finalize, a merge — refreshes
every sibling worktree's evidence at once.

### operation-triggered-recompute

Operations that plausibly change a sub-state's truth — a merge landing, a
push, a sync or rebase, a claim settling or releasing, a handoff resolving —
signal the resident accelerator to recompute promptly, rather than leaving
the affected worktrees to wait out the next periodic sweep.

### marked-not-multiplied-uncertainty

When a specific fact's truth cannot currently be confirmed, the rendered
status marks that one fact as unconfirmed rather than inventing a separate
whole state for the unverified case — the vocabulary of possible statuses
stays fixed size regardless of how many facts happen to be stale at any
moment.

## Behaviors

### durable-state-outlives-execution

Closing a terminal, replacing a session host, changing Copilot products, or
losing a provider does not erase the worktree's objective, claims, lineage, or
disposition.

### explicit-relations-never-sniffed-ownership

Binding, control, succession, and claim ownership are explicit state transitions.
Incidental cwd, process ancestry, pane membership, or connection presence may be
evidence supplied by a host, but never silently creates responsibility.

### launch-is-not-takeover

A newly launched process or newly observed session does not become the worktree
head until the governing lifecycle transition acknowledges it. Failed or
duplicate launches therefore cannot steal authority.

### derive-dont-duplicate

Each durable fact has one owner. Execution providers own their runtime-specific
evidence; agent-worktrees owns worktree-lifetime state; presentation and
coordination layers derive over both rather than copying either.

### observation-loss-degrades-honestly

When a provider is unreachable, agent-worktrees reports stale or unknown live
state while preserving durable state. It does not infer that an objective is
resolved, a session is dead, or a claim is abandoned from missing telemetry.

### uncertainty-is-marked-not-multiplied

An unconfirmed fact renders as that fact's real value plus an explicit
marker, never as a different, separately-named state standing in for
"unverified." A consumer sees one fixed vocabulary of facts and,
independently, which of them are currently confirmed.

### freshness-is-pursued-not-assumed

No caller may treat a fact as confirmed merely because its own request
happened to include a fetch. The system actively keeps shared evidence
current — a periodic background sweep plus operation-triggered
recomputation — so an ordinary reader benefits from freshness without
personally requesting it.

### force-refresh-is-opt-in-not-implicit

A caller may force the resident accelerator to recompute a specific fact
immediately, funneled through the accelerator's own queue so concurrent
force-refresh requests coalesce rather than each triggering independent,
thrashing recomputation. This exists purely at explicit user or agent
discretion — no ordinary read path triggers a force-refresh merely to
produce an answer; the periodic sweep and operation-triggered recompute are
what keep an ordinary reader's answer current without it.

### a-full-health-check-leaves-nothing-stale

Running a full consistency/health pass over the accelerator's tracked state
is expected to leave every fact it owns **confirmed fresh wherever that
confirmation is actually obtainable** — not merely reachable — so a caller
that follows a health check with an ordinary read never needs its own
force-refresh to trust the answer. This does not override
*uncertainty-is-marked-not-multiplied* above: a fact a health check genuinely
could not confirm (its source was unreachable, absent, or ambiguous even
after the pass) is reported as that fact's real value plus an explicit
unconfirmed marker, never silently claimed fresh just because a health check
ran (see *uncertainty-is-marked-not-multiplied* above).

### one-fetch-serves-every-sibling

A repo's upstream-containment evidence, once refreshed by any means, is
immediately available to every worktree of that repo — never re-fetched
independently per worktree for the same evidence.

### contribution-posture-degrades-honestly

When a related repo's contribution posture cannot be discovered from its own
signals — the signal is unreachable, ambiguous, or simply doesn't exist —
agent-worktrees says so rather than guessing a posture or silently omitting
guidance. A gap in discovered fact is never quietly papered over with an
invented default.

### finalization-joins-durable-obligations

A worktree may complete only when its source-control content is safe and every
durable obligation is settled or transferred. Interactive process exit is
neither necessary nor sufficient evidence of completion.

### finalization-is-reversible-under-live-resume

A worktree's completed/finalized state is not a one-way trap for a session
still actively resumed inside it. Finalization freezing the worktree's capacity
to originate new claims, obligations, or child worktrees — while continuing to
let the same live session read, write, and resume inside it — is an
inconsistent middle state, not a safety boundary: nothing protected by refusing
a new obligation is also protected by allowing the resumed session to keep
acting otherwise. The owning live session can reactivate a finalized worktree,
lifting exactly that frozen-ownership restriction, without discarding its
existing durable record, lineage, claims, or history. A worktree that is
genuinely done accepting new work is retired from active resumption entirely,
not left resumable-but-silently-crippled.

### archival-is-a-terminus-not-a-deletion

Reclaiming a finalized worktree's on-disk checkout retires it to **archived**,
strictly after finalization — it never deletes the durable record outright.
An archived worktree is unambiguously not resumable and not a member of any
default listing, but its identity, lineage, and session history remain
queryable by direct reference indefinitely. Nothing that already depended on a
worktree's past existence — a session's recorded binding, a lineage graph, an
audit trail — silently loses its anchor the moment the checkout is reclaimed.

### provider-replacement-preserves-agency

Changing the preferred session host affects future execution legs, not the
identity or meaning of the worktree. Existing legs retain their recorded
provider and semantics until they conclude or hand off.

### presentation-is-process-boundary-only

Control planes consume worktree state and actions through attributable
machine-readable boundaries. agent-worktrees never imports a TUI, terminal
manager, or session-host implementation.

## Non-Goals / Boundaries

- **Not a Copilot process manager.** agent-worktrees does not launch, wrap,
  reattach, prompt, interrupt, or terminate Copilot processes.
- **Not a terminal or multiplexer owner.** TMux, PSMux, terminal windows, panes,
  and console choreography belong to an execution-host provider.
- **Not a universal session host.** Copilot CLI, ACP, SDK, App, and third-party
  rigs retain their own hosting and interaction semantics.
- **Not a home for a provider-specific config union.** agent-worktrees does not
  carry a typed "session backend" field set with one branch per hosting
  technology (e.g. Mux fields beside AHP fields in the same record or config
  schema). Each execution leg it records is a provider id plus an opaque,
  provider-owned blob; the mechanics that establish and present a session —
  currently Mux and AHP, both driven by the Worktree Manager control-plane —
  live outside agent-worktrees entirely.
- **Not the handoff transport.** It records lineage, head transitions, and
  durable responsibility; context transfer and live cutover are orchestrated
  above it through the selected execution host.
- **Not a transcript or event warehouse.** The worktree record remains bounded
  and objective-facing. Rich conversation history belongs to the session host or
  session archive.
- **Not the presentation host.** It contributes worktree semantics but does not
  render the operator experience.
- **Not a second background service.** The periodic freshness sweep and
  operation-triggered recomputation extend the existing resident accelerator
  (already required to exist as a single process per host); they do not
  introduce a second daemon.
- **Not a specification.** This vision fixes ownership boundaries and durable
  guarantees, not schemas, commands, endpoints, file layouts, or provider APIs.

## See Also

- Parent vision: [agent-fabric](../../agent-fabric/README.md)
- Cross-cutting sibling:
  [session-hosting](../../session-hosting/README.md) — pluggable ownership of
  user-interactive and headless Copilot execution.
- Presentation sibling: [picker](../../picker/README.md)
- Coordination sibling:
  [plugins/agent-bridge](../agent-bridge/README.md)
- Reality docs: the agent-worktrees plugin `docs/`

## Provenance

- **2026-09-20** — Added *external-status-consumer-contract* (Features),
  *force-refresh-is-opt-in-not-implicit* and *a-full-health-check-leaves-
  nothing-stale* (Behaviors). Mined from an operator directive during the
  `agent-dispatch-tasks-pane-ux-overhaul` effort's Phase 8 design pass: a
  worktree-status card (or any other external status consumer) needs a
  fast, coalesced read against the resident accelerator's own tracked
  state — worktree/session mapping, session lineage and lifecycle event
  history, last-known liveness, last-known git state, and the claims graph
  — never its own independent git/session polling on a hot per-render
  path. This is the same *warmth, not truth* accelerator already
  established elsewhere in this vision (*Derived status*) and in
  `docs/patterns/work-coalescing-singleton.md`: an unreachable accelerator
  falls back to correct direct computation, never a hard failure, and a
  fact even direct computation cannot confirm is marked unconfirmed rather
  than silently omitted or claimed fresh. Force-refresh exists, but
  strictly at explicit user or agent discretion (queued to coalesce
  concurrent requests, never triggered by an ordinary read just to
  function); a full health/consistency pass is expected to leave every
  confirmable fact fresh, without overriding the same unconfirmed-marker
  discipline. Message/conversation history is explicitly excluded
  (reaffirming the existing *Not a transcript or event warehouse*
  non-goal) — a consumer pulls recent messages on demand from the owning
  session host (agent-bridge) instead.
- **2026-09-20** — Added *Post-finalization archival* (Concepts & Components),
  *registered-by-default-listing* (Features), and
  *archival-is-a-terminus-not-a-deletion* (Behaviors): a reaped, unpaired
  worktree's tracking record is currently deleted outright
  (`retire_record`), leaving nothing for a consumer to answer "what
  happened here" beyond hand-reconstructing from an archived session corpus
  elsewhere. Mined from an operator directive during the `aperture-labs`
  `session-worktree-archive-linkout` effort's Phase 2b: agent-worktrees
  should itself be the durable authority for a worktree's post-life
  identity (a new **archived** state, strictly after finalized), every
  listing surface should default to the registered (non-archived) set, and
  a consumer (agent-bridge) should mirror that default rather than
  inventing its own archival reconstruction.
- **2026-09-15** — Refined *Derived status* into a decomposed sub-state model:
  a fixed set of independently named facts (checkpoint activity,
  upstream-containment, dirtiness, open claims, pending handoff), freshness
  actively and continuously maintained by the resident accelerator (a
  periodic sweep plus operation-triggered recomputation) rather than trusted
  from whichever caller's own request happened to pass a fetch flag, and
  unconfirmed facts marked individually rather than each spawning a separate
  whole state. Mined from an operator observation that every worktree read as
  `MERGED`, never `FINAL`, because the closure descriptor's freshness signal
  was scoped to a single call's own fetch rather than to the repo-wide
  remote-tracking refs every sibling worktree actually shares — refined via
  discussion into treating uncertainty as a per-fact marker instead of a
  parallel state space, and freshness as an actively pursued, resident-owned
  property instead of a passively hoped-for one.
- **2026-09-14** — Added *pull-request capability* (Concepts & Components),
  linking a new child leaf vision,
  [`pull-requests`](pull-requests/README.md), that generalizes the PR concept
  beyond "a claimed resource on the worktree's ledger" into its own
  provider-neutral capability (author+reviewer symmetric, foreign-repo
  addressable, mock-provider verifiable). Mined from a live odsp-web-harness
  clean-room finding: a scenario-eval correctly reported BLOCKED for "no PR
  available" rather than fabricate a review, surfacing that reviewer-side PR
  operations, foreign-repo addressing, and a conformance-verified mock
  provider have no first-class home today.
- **2026-09-12** — Added *related-repo relationship and contribution posture*
  (Concepts & Components), *auto-discovered-contribution-posture* and
  *ambient-cross-repo-contribution-guidance* (Features), and
  *contribution-posture-degrades-honestly* (Behaviors). Mined from an operator
  observation that a project relating to several external repos had drifted
  into hand-curating a second, parallel catalog of those repos' contribution
  rules alongside agent-worktrees' own related-repo index — duplicating facts
  a target repo already publishes about itself, and going stale as those rules
  changed. Filed as
  [#2562](https://github.com/ThomasMichon/copilot-extensions/issues/2562).
- **2026-09-11** — Added *finalization-is-reversible-under-live-resume* after
  a live-reproduced defect: an actively resumed worktree (session count 2,
  resume count 6, still hosting the current session) was marked finalized —
  apparently by a stale or premature completion signal, not by any deliberate
  operator action — and this then blocked the same live session from creating
  a new child worktree ("creator ownership is frozen"), with no supported
  reversal short of abandoning the worktree entirely. The finalized state was
  otherwise transparent: reads, writes, and resumption all still worked. Filed
  as [#2467](https://github.com/ThomasMichon/copilot-extensions/issues/2467).
- **2026-09-09** — Strengthened "Derived status" from an optional accelerator
  to an explicit thin-client/ref-counted-subscriber expectation: an ordinary
  reader (CLI invocation or Picker), not only a session-lifecycle hook, should
  reach a reachable resident accelerator rather than independently recompute,
  boot one on demand when absent, and let the accelerator's own lifetime be
  governed by subscriber count plus a bounded linger. Direct computation
  remains the correct degrade path, never the steady-state target. Mined from
  a live-reproduced race: two independently-launched agent-worktrees
  invocations recomputing the same project's classification concurrently,
  racing each other, with no shared accelerator either could have deferred to.
- **2026-09-04** — Reframed agent-worktrees around durable worktree-lifetime
  agency state rather than Copilot process ownership. The revision separates
  repository/worktree identity, claims, relationships, status, and completion
  from the interchangeable technologies that host an interactive agent. It was
  mined from the requirement that the same agency model work across multiplexed
  Copilot CLI, plain CLI, ACP, SDK, graphical, and third-party hosting rigs.
