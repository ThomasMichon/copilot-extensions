# agent-worktrees — Worktree Lifetime & Agency State — Vision

- **Subject:** **agent-worktrees** as the durable authority for repository and
  worktree identity, worktree-lifetime agency state, relationships, claims,
  obligations, disposition, and source-control completion.
- **Scope:** leaf (concrete component; child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-09-24
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

### Per-repo account identity resolution

Two related but independent identity systems must each resolve to the account
an operator actually intends for the repo in play: the `gh`/git identity
(which account pushes, opens PRs, and calls the `gh` API) and Copilot CLI's
own inference identity (the account Copilot itself authenticates its own
requests as, tracked independently of `gh`). agent-worktrees is the durable
owner of both resolutions. The `gh` identity resolves through an explicit
per-repo `account` override, or the decoupled owner-keyed `account_map`, so
git/PR operations for a repo never depend on whichever account happens to be
globally active. The Copilot identity resolves separately and is
**repo-keyed, not owner-keyed** — a repo has no GitHub owner to derive an
identity from at all when hosted somewhere other than GitHub, yet an operator
can still need a specific Copilot identity pinned for it — falling back to a
machine-level default when a repo declares no explicit preference. This is
an **opt-in personal-accountability capability**: an operator who never
declares a Copilot-identity preference sees no change in behavior.

Correcting a resolved Copilot-identity mismatch is inherently constrained by
one hazard: the identity file it corrects is shared machine-wide, not scoped
to the process about to launch, and an already-running Copilot CLI session
observably picks up a change to it live rather than only at its own next
startup. Switching it while another session is mid-flight risks splicing
that session's usage across two accounts, invalidating its prompt cache, and
provoking authentication errors. Correction therefore only proceeds
automatically when the machine looks otherwise idle for Copilot, and simply
surfaces the mismatch (never blocking the launch) the rest of the time,
unless an operator explicitly accepts the risk.

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

### Title and naming

A worktree's displayed title is durable identity, not a transcript of "what I'm
doing right now." When a worktree is bound to a canonical effort, the title is
**anchored to that effort's slug** for the binding's whole lifetime — a short
machine-relatable form (`slug:phase`) or a friendly-prose equivalent ("Improve
worktree tracking: Phase 4, slice A") — rather than a fresh, disconnected
headline each time an agent happens to describe its current activity. An
unbound worktree still carries a durable title, but nothing anchors it against
drift the way an effort slug does. This directly counters the churn where a
title mutates dramatically across a handoff because the successor summarized
only its own moment instead of the durable objective: a bound worktree's title
changes when the effort's phase changes, not when a new session merely
rephrases the same phase.

### Discovery for duplicate-effort detection

Before starting work that could already be underway elsewhere, an agent (or the
operator, through the agent) can cheaply enumerate the fleet's current worktrees
and their bound objectives to check for an existing claimant. Where a chain of
worktrees or repos relates to one activity (a driving worktree plus the repos it
touches), discovery surfaces the **root claimant** — the worktree actually
driving the effort — not merely the nearest or most-recently-touched member of
the chain, which may be a passive participant rather than an agent of its own.

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

Claiming the head is an **affirmative, exclusively-owned act**, never an
incidental side effect of an unrelated code path running first. Seating a head
where none has ever existed happens through exactly one authorized path; every
other observer of worktree state reads the current head rather than competing
to set it. Once a head exists, displacing it is never a two-phase "predecessor
clears, successor later claims": the predecessor remains authoritative until
the successor proves it can recover the baton, and that proof is what moves
authority — in one guaranteed step, not two independent ones a delayed or
failed launch could pull apart. A launch is provisional until that proof
lands; nothing is ever surrendered without it. (This generalizes the existing
`context-handoff-lifecycle` pattern's ownership invariant as the durable
head-succession guarantee, not an orchestration-layer-only rule — see that
pattern doc for the concrete mechanics.) A resuming session therefore always
lands as the *rightful* current leg — either the sole claimant of a truly
fresh head, or the acknowledged successor of a specific, still-identified
predecessor — never a second, uncoordinated voice re-entering a conversation
its predecessor already concluded, and never inheriting authority a
predecessor gave up before a successor was actually ready for it.

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

**This describes the accelerator's first-landed shape**
(`agent-worktrees-external-status-accelerator`, 2026-09-20): read-only warmth
over facts the durable YAML record (and direct git/session/claims
computation) already own, never a second writer, never authoritative over a
fresher direct read. *The resident daemon as the authoritative live-state
database* below asserts the deliberate long-term inversion of that stance —
read this section as Phase 1 of that trajectory, not as the ceiling.

### The resident daemon as the authoritative live-state database

The accelerator's long-term direction is a reversal of *Derived status*'s
"never a second writer": the resident daemon becomes worktree-lifetime
state's **one live authority** — an in-memory database every read **and**
every mutation is funneled through — rather than a read-only cache layered
over YAML files that remain the actual read/write surface. The per-worktree
YAML record does not disappear; it becomes the daemon's own **durable
persistence**, written only by the daemon itself as a best-effort sidecar
sync of its in-memory state, never independently opened, parsed, or
hand-written by a CLI command, a script, or a sibling plugin as a side door
around it.

This is a deliberate reversal, not an extension: today, a CLI invocation that
finds no daemon reachable degrades to direct file computation as a
**correct, coequal** path (*Derived status* above). Under this direction, a
read degrade to direct file access remains available for resilience — the
daemon being unreachable must never wedge worktree state shut — but it stops
being coequal: it is a **degraded, advisory-only** path, clearly marked as
such to whatever surfaced it. A *write* degrades differently, deliberately:
when no daemon is reachable, a mutation runs the **exact same underlying
write logic the daemon itself would have called** — imported and invoked
in-process, never a second, forked implementation that could quietly drift
from what the daemon enforces — and the fact that it bypassed the daemon is
always explicitly logged, never silent. This keeps a mutation available even
when the daemon cannot be reached, while keeping exactly one implementation
of what a write means and a durable, inspectable trail of every time that
implementation ran outside the daemon's own mediation. A CLI command's
steady-state job is composing a request to the daemon and rendering its
answer, not independently computing or writing the record itself; the
logged direct-call fallback exists for the daemon-unreachable edge, not as
an equally-preferred alternative.

The daemon's authority is scoped to **one host**, matching *Derived
status*'s existing "exactly one [accelerator] per host" guarantee exactly —
it is not a cross-machine authority, and this direction does not introduce
one. A worktree checked out on a different machine is reached the same way
any other cross-host agent-worktrees operation already is: through that
machine's own `agent-worktrees` CLI (over whatever transport reaches it),
which then talks to *its own* local daemon. There is no direct daemon-to-
daemon wire protocol between hosts; cross-machine reach is CLI-to-CLI,
recursing into the same single-authority-per-host pattern on the far side,
never a second, wider-scoped authority layered above it.

The daemon's in-memory state is what "current" means; its durable YAML
persistence is a recovery mechanism (warm-restore after a restart), never a
second copy another reader/writer could race the daemon over. Every fact this
vision already names — identity, claims, lineage, disposition, the derived
status reduction — moves under this same single authority; none of them
retain a separate, daemon-bypassing read or write path once this direction is
realized.

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

### single-authorized-head-claimant

A worktree's head-from-vacant claim (a fresh worktree, or a fully-recovered
abandoned one) has exactly **one** authoritative outcome, with no competing
path able to claim, overwrite, or race it once decided. Displacing an
*existing* head is a distinct, acknowledgement-gated transfer (see
*atomic-acknowledgement-transfer* below), never a second path to the same
vacant-slot claim.

### effort-anchored-title

A worktree bound to a canonical effort carries a title anchored to that
effort's slug and current phase, stable across handoffs and resumed sessions,
rather than a headline that reflects only the most recent session's framing of
"what we're doing now."

### duplicate-effort-discovery

A cheap, ambient path exists to check the fleet for a worktree already driving
a given effort or objective before starting parallel, duplicative work, and it
resolves to the **root claimant** in a related chain rather than a passive
downstream member.

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

*(Phase 1 shape — see* daemon-mediated-write-authority *and* single-write-
path-across-plugins *below for the asserted long-term inversion this is
expected to grow into.)*

Any other capability that needs a worktree's current status — a Tasks-board
status card, a dashboard, a notification — is the same kind of reader
*Derived status* above already describes: it boots the resident accelerator
on demand if none is reachable, subscribes, and reads the answer from its
fast, coalesced cache, rather than independently recomputing git state,
session lineage, liveness, or the claims graph, or spawning its own
subprocess on a hot per-render or per-click path. Per the suite-wide
work-coalescing-singleton pattern, this is **warmth, not truth**: the
accelerator owns no fact a consumer couldn't otherwise obtain, it only saves
everyone from separately paying to recompute the same shareable answer, and
a stale or unreachable accelerator is never treated as authoritative over a
fresher direct answer. The projection itself carries the same per-fact
freshness/unconfirmed markers *Derived status* already requires (never a
flattened, all-or-nothing "current" verdict); a consumer renders those
markers as given rather than presenting a successfully-fetched but stale or
unconfirmed fact as certain. Rich conversation/message history is explicitly
excluded from this cache (see *Not a transcript or event warehouse* below) —
a consumer that wants recent messages pulls them on demand from the owning
session host instead.

A consumer able to compute these facts itself (an in-process agent-worktrees
caller) retains *Derived status*'s existing direct-computation fallback when
no accelerator is reachable. A consumer that cannot — a separate plugin
operating under its own documented subprocess-free contract (a Tasks-board
render loop or card click forbidden from spawning work, for instance) — has
no such fallback available to it: this is the narrow, named exception
`docs/patterns/work-coalescing-singleton.md` records for exactly this
shape. Its boot-on-demand subscribe attempt either succeeds within its own
bounded wait, or it reports the requested facts as stale/unknown and moves
on, never blocking its own render or click path and never silently
recomputing or guessing at the answer.

### daemon-mediated-write-authority

Every mutation of worktree-lifetime state — a claim opened or settled, a
disposition asserted, a title set, a head transition acknowledged, a
follow-up dismissed, a session registered — is a request to the resident
daemon, which holds the current in-memory truth and is solely responsible
for persisting it to the durable YAML record. No command, script, or
sibling plugin opens, parses, or hand-writes a tracking YAML file directly;
doing so is a bypass of the one authority, not a second legitimate writer,
regardless of how carefully it locks the file.

### single-write-path-across-plugins

A sibling plugin that needs to read or change worktree state — agent-bridge,
agent-dispatch, agent-codespaces, agent-containers, agent-logger, or any
other — does so exclusively through agent-worktrees' own published surface
(its CLI, or the daemon's documented wire contract), never by importing
agent-worktrees' internals to read or write its tracking records directly.
This closes the specific atomicity/lock hazard of two independent writers
(agent-worktrees' own CLI and a sibling plugin) racing the same YAML file
through two different lock disciplines.

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

### atomic-acknowledgement-transfer

Moving the head from an existing predecessor to a successor is one atomic,
acknowledgement-gated step — never an independent "predecessor clears" action
followed later by "successor claims." The predecessor remains authoritative
until the successor proves it can recover the baton; that single verified
step both displaces the predecessor and seats the successor. A predecessor
that crashes or fails before acknowledgement leaves the head exactly where it
was — non-vacant, recoverable, and never falsely presumed conceded — rather
than opening a race window a delayed or failed launch could exploit.

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
is expected to leave every fact whose underlying observation **completed
successfully** confirmed fresh — not merely reachable — so a caller that
follows a health check with an ordinary read never needs its own
force-refresh to trust an answer the pass actually confirmed. This does not
override *observation-loss-degrades-honestly* or
*uncertainty-is-marked-not-multiplied* above: when a health check's own
fetch, provider probe, or git access fails or is unreachable, the affected
fact is reported as stale/unknown — its last-known value plus an explicit
unconfirmed marker — never silently claimed fresh just because a health
check ran.

### one-fetch-serves-every-sibling

A repo's upstream-containment evidence, once refreshed by any means, is
immediately available to every worktree of that repo — never re-fetched
independently per worktree for the same evidence.

### no-writer-bypasses-the-daemon

Every mutation runs through exactly one implementation of what that write
means — the daemon's own request handler in the steady state. When the
daemon cannot be reached, the identical implementation may run directly,
in-process, rather than forking a second write path — but that bypass is
always explicitly logged, never silent, and never treated as an equally-
preferred alternative to going through the daemon. A *second, independently
maintained* implementation of a write — one that could drift from what the
daemon enforces — is the thing this rules out, not a logged, same-code
emergency path.

### durable-files-are-persistence-not-a-side-door

The tracking YAML record exists so the daemon's in-memory state survives a
restart, and so a human or a break-glass script can read it when nothing
else is reachable — it is not a second, independently-writable copy of the
state a caller could reach around the daemon for convenience or perceived
speed.

### direct-read-is-a-degrade-not-a-peer

Direct file/computation access remains available for resilience — the
daemon being unreachable must never wedge worktree state shut — but it is
strictly a **degraded, read-only** path, not a coequal one: it never writes,
and whatever surfaced it marks the answer as degraded rather than presenting
it with the same confidence as a daemon-mediated read.

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
- **Not a Terminal Fragment owner.** Which launch targets a machine's terminal
  application (Windows Terminal, Tabby, ...) carries a profile for — the
  selection model, its persistence, and mirroring it into real terminal-app
  fragments — is being phased out of agent-worktrees into the Worktree Manager
  control-plane, the same relocation already applied to Mux presentation and
  the AHP session backend (#2062). Terminal-app *profile*/fragment handling of
  every kind is leaving this plugin; agent-worktrees ends up knowing nothing
  about how, or whether, a session is ever attached to a terminal-app profile
  at all. **This does not extend to per-project binstubs** (the PATH launcher
  scripts a registered project resolves to, e.g. `my-project` /
  `my-project.cmd`/`.ps1`): deploying, repairing, and reasoning about those
  remains squarely agent-worktrees' own responsibility — a different, unrelated
  artifact from a Terminal Fragment's WT/Tabby profile entry, and not part of
  this relocation.
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
- Reality docs: the agent-worktrees plugin `docs/`,
  [`docs/patterns/context-handoff-lifecycle.md`](../../../docs/patterns/context-handoff-lifecycle.md)
  (the ownership/retirement invariants this vision's head-succession guarantee
  generalizes)

## Provenance

- **2026-09-26** — Refined *The resident daemon as the authoritative
  live-state database* and *no-writer-bypasses-the-daemon* to resolve three
  open questions the prior same-day entry below left for operator input:
  (1) a write with no daemon reachable runs the identical write
  implementation directly, in-process (never a forked second
  implementation), with the bypass always explicitly logged — not the
  read-only-only degrade the prior entry had speculatively asserted; (2)
  the daemon's authority is strictly **per-host**, matching *Derived
  status*'s existing "exactly one per host" guarantee — a different
  machine's worktree is reached through *that* machine's own
  `agent-worktrees` CLI, which talks to its own local daemon, never a
  direct cross-host daemon protocol; (3) sequencing against concurrent
  effort work is tracked in the `agent-worktrees-authoritative-daemon`
  effort's own Context, not the vision. Operator answers captured verbatim
  in that effort's Journal.
- **2026-09-26** — Added *The resident daemon as the authoritative
  live-state database* (Concepts & Components), *daemon-mediated-write-
  authority* and *single-write-path-across-plugins* (Features), and
  *no-writer-bypasses-the-daemon*, *durable-files-are-persistence-not-a-
  side-door*, and *direct-read-is-a-degrade-not-a-peer* (Behaviors) —
  asserting the deliberate long-term reversal of *Derived status*'s "never
  a second writer" and *external-status-consumer-contract*'s "warmth, not
  truth": both are marked as this vision's already-landed Phase 1, not its
  ceiling. Mined from an operator directive after diagnosing
  copilot-extensions#3751 (the resident status-monitor pinning ~80-85% CPU
  from an uncached, fleet-wide YAML reparse) and a session survey of every
  sibling plugin's `agent_worktrees.tracking` usage that found only
  read-only accessors today (`load_record_by_id`, `find_worktree_id_by_cwd`,
  `find_worktree_id_by_session`) — no unauthorized direct writer yet exists,
  but nothing durable prevented one from appearing. The operator's own
  framing: "the first vision pass was just getting the daemon to exist and
  get callers using it" (the accelerator effort, read-only); the long-term
  goal is an authoritative in-memory database for live state, carefully
  wrapped by CLI commands and the daemon, with the durable YAML record
  demoted to the daemon's own persistence rather than a second write
  surface any command or sibling plugin could still reach around it. A
  dedicated follow-on effort (design-first, mirroring how the accelerator
  itself was run) is expected to carry this from asserted vision to landed
  code.
- **2026-09-25** — Renamed the *terminal-app profile owner* Non-Goal to
  *Terminal Fragment owner* and added an explicit carve-out: per-project
  binstub deployment/repair (the PATH launcher scripts a registered project
  resolves to) is a different, unrelated artifact from a Terminal Fragment's
  WT/Tabby profile entry and is **not** part of this relocation — it remains
  agent-worktrees' own responsibility. Operator direction while landing Phase
  3e Step 6 (copilot-extensions#3390): the code-level cutover (deleting
  agent-worktrees' `profiles`/`terminal-fragment` CLI verbs) surfaced real
  ambiguity between "terminal handling of every kind" (the prior wording) and
  the still-owned `repair --binstubs` surface, so the vision is tightened to
  match the intended, narrower boundary before the remaining fallout (a
  broken `install.ps1` local fallback, and a separate `worktree-manager`
  repo still calling the removed CLI verbs) is resolved.
- **2026-09-23** — Added the Non-Goals *terminal-app profile owner* bullet.
  Operator direction while scoping copilot-extensions#3360 (retiring the
  Worktree Manager's in-process `_engine_runtime.py` boundary): terminal
  handling of every kind — not just Mux/PSMux presentation and the AHP
  backend (already relocating per #2062) but also the machine-local
  terminal-**profile** selection model (`profiles.py`) and its mirroring
  into real terminal-app fragments (`terminal_fragment.py`), today still
  agent-worktrees' own `profiles`/`terminal-fragment`/`repair` CLI verbs —
  is leaving this plugin for the Worktree Manager control-plane, in phases,
  the same way `agent-worktrees update` is expected to eventually become
  `worktree-manager update`. See the mirrored provenance entry in
  [`visions/installer`](../../installer/README.md) for the control-plane
  side of this relocation, and the `worktree-manager-control-plane` effort's
  Phase 3e for the tracked, ordered plan.
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
  falls back to *Derived status*'s existing direct-computation path only
  for a consumer that can actually perform that computation itself (an
  in-process agent-worktrees caller); a separate-plugin consumer with no
  access to that logic reports the affected facts as stale/unknown instead,
  never silently recomputing or blocking. Force-refresh exists, but
  strictly at explicit user or agent discretion (queued to coalesce
  concurrent requests, never triggered by an ordinary read just to
  function); a full health/consistency pass is expected to leave every
  successfully-observed fact fresh, without overriding the existing
  observation-loss/unconfirmed-marker discipline. Message/conversation
  history is explicitly excluded (reaffirming the existing *Not a
  transcript or event warehouse* non-goal) — a consumer pulls recent
  messages on demand from whichever session host owns that session
  instead (e.g. agent-bridge for a bridge-hosted one; CLI/mux, ACP, SDK,
  and third-party hosts are peers, not a single fixed source).
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
