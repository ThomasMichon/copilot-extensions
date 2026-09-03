# agent-logger — Vision

- **Subject:** The **chronicler** capability of agent-logger — a scheduled,
  fleet-wide background service that turns the synced corpus of Copilot sessions
  into an objective, retrievable **daily chronicle** of what the fleet actually
  did, landed as versioned logs in each session's own home repository.
- **Scope:** leaf (a per-plugin vision; a **consumer** that rides the
  [agent-fabric](../../agent-fabric/README.md) delegation layer, not a layer of it)
- **Status:** Draft
- **Last revised:** 2026-09-03
- **Reality docs:** [`plugins/agent-logger/docs/architecture.md`](../../../plugins/agent-logger/docs/architecture.md) ·
  [`plugins/agent-logger/docs/deployment-topologies.md`](../../../plugins/agent-logger/docs/deployment-topologies.md) ·
  [`plugins/agent-logger/docs/manifest-contract.md`](../../../plugins/agent-logger/docs/manifest-contract.md)

## Purpose & Intent

A fleet of Copilot agents produces an enormous amount of work that never earns
its own issue, effort, vision, or doc: an approach that was tried and abandoned,
a fix that turned out not to be worth a ticket, a dead end that a later agent
would waste hours rediscovering. That knowledge exists — it is sitting in the
session transcripts — but it is **unindexed and unfindable**, so it is
effectively lost the moment the session ends.

The **chronicler** exists to make that corpus **retrievable**. Its north star is
a passive, always-caught-up, **matter-of-fact daily record** of the whole
fleet's session activity, written into durable, versioned homes, so that a later
agent asking *"has anyone touched this before? what worked? what didn't?"* gets
**hits** — pointers back into real prior work — instead of silence. The value is
**retrieval, not narration**: the chronicle is a searchable substrate for a
later semantic index, not a story anyone reads front to back.

The chronicler is deliberately **objective by default**. Its job is to record
what happened plainly and completely; any editorial voice is a **consumer's**
choice layered on its own destination, never the baseline. And it is **automatic
and fleet-wide**: it draws from the already-synced session corpus without an
operator asking, on a schedule, from a single elected place — so the record keeps
itself current as a background fact of the fleet, not a chore anyone remembers to
run.

Crucially, the chronicler is **not a bespoke service**. It is the generic,
facility-neutral realization of a pattern several consumers already want; each
consumer supplies only *where its sessions are*, *where its logs go*, and *how
they land* — the machinery in between is shared.

Those consumer choices are **repository-owned declarations**, not one
machine-global winner-takes-all configuration. A machine compiles every
applicable declaration into one explainable execution plan before it acts.
Within a repository, its default, machine-specific policy, and machine-local
override form an explicit precedence chain. Across repositories there is no
implicit precedence: source claims must be provably disjoint or resolution
fails closed. This lets several repositories share one installed chronicler
without letting a broad default silently capture another repository's sessions
or destinations.

## Concepts & Components

The chronicler is an **orchestrator daemon** with two pluggable **seams** on
either side of a shared middle, driven by the fabric's delegation layer and
pinned to run in exactly one place at a time.

### The orchestrator daemon — the shared middle
A single pipeline: **scan** the synced corpus → **digest** each due session into
a compact daily record → **batch** the due work → hand each batch to the
**voice-neutral writer** → **land** the result through the sink's chosen policy.
The daemon owns only this flow and the guarantees around it (below); it owns
**neither** the source of sessions **nor** the destination of logs — those are
seams. The writer it delegates to is the existing personality-free
session-log-writer; the daemon adds the *scheduling, routing, and landing* the
writer never had.

### The session-source seam — where sessions come from, and when they're ready
An interface that enumerates candidate sessions from a corpus (a local store, a
synced fleet root, an ingest sink) and, critically, gates them on **readiness**:
a **settle window** so a session still being synced is never chronicled
mid-flight, and an **already-chronicled skip** so a session that has been
recorded is never recorded again. These two gates are what make the whole
pipeline **idempotent under catch-up** — the cornerstone that lets execution lag
for days and then replay cleanly.

### The log-sink seam — where a session's record goes, in what voice, landed how
A composite of three consumer-supplied strategies:

- **Router** — decides, per session, **which repository** its record belongs to,
  keyed off the session's **own recorded origin** (the repository it was actually
  worked in), with a **machine-default fallback** when a session has no clear
  origin. This is what keeps a record filed in its rightful home and, in a mixed
  fleet, keeps one facility's sessions from ever being misfiled into another's.
- **Profile** — the **tone and shape** of the record. The default is
  **objective / matter-of-fact** with a compact daily form; a consumer may layer
  its own voice or thematic form on **its** destination without changing the
  baseline for anyone else.
- **Landing-policy** — **how** a finished record is committed to its home, chosen
  per sink (a governed review-gated queue, a scoped direct commit, a squashed
  pull request, …). The daemon **must not** hardcode a landing mechanism; landing
  is the consumer's to name.

### The aggregate configuration compiler — which seams are authorized here
Repositories publish portable declarations of the session populations they
claim, the corpus targets that collect those sessions, and the log sinks that
render and land their records. On each machine the chronicler discovers the
applicable declarations through stable repository identities, selects each
repository's default or matching machine-specific policy, applies any
machine-local override **only to that repository**, and normalizes the result
into one reproducible machine plan.

Collection ownership and rendered-log ownership are separate claim dimensions:
the raw corpus and the durable chronicle may have different owners and landing
semantics. Within either dimension, however, a source population has one
unambiguous owner unless an explicit future policy deliberately permits
fan-out. A wildcard claim and a more specific claim overlap unless the wildcard
explicitly excludes or cedes that population.

Compilation is a prerequisite for side effects. Ambiguous identities,
non-deterministic machine matches, overlapping cross-repository claims,
incompatible targets, unavailable sinks, or invalid landing policies make the
whole plan unauthorized. The chronicler does not schedule, copy, prune, render,
or land a valid-looking subset while the aggregate is unresolved.

### The execution pin — one elected chronicler, catching up
The daemon runs from **one elected place at a time** across the fleet (so the
same session is not chronicled by two machines), and does so as **first-class
work on the delegation layer's claimable mesh** — not through a private,
parallel locking scheme. Its recurring runs are **scheduled production**
(§agent-dispatch), and its per-session units are **ordinary claimable tasks**
whose single-winner guarantee and vanished-worker recovery come from the mesh
itself. Execution is **pinned, not hot-failover**: if the elected place is
asleep, the chronicle simply **waits and catches up** on its next run; it does
not thrash execution to a standby.

## Features

### fleet-wide-background-chronicle
The fleet's session activity is recorded **automatically, on a schedule, from one
elected place**, with no operator prompting. The record stays current as a
background fact; a gap in execution is closed by catch-up, not lost.

### two-pluggable-seams
The daemon is generic: a **session-source** seam supplies *which sessions and
when they're ready*, and a **log-sink** seam supplies *where each record goes, in
what voice, landed how*. A new consumer adopts the chronicler by supplying these
two seams — never by forking the pipeline.

### repository-owned-aggregate-configuration
Every participating repository owns its declaration; no repository can
silently override another's. Each machine compiles all applicable declarations
into one normalized plan, with same-repository precedence for defaults,
machine-specific policy, and a repository-scoped machine-local override.
Cross-repository overlap is a configuration error, not a precedence contest.

### explainable-machine-plan
The chronicler can expose the exact normalized plan it would execute: machine
identity, declaration provenance, selected and inactive policies, canonical
source claims, explicit exclusions, collection targets, rendered-log sinks,
profiles, landing policies, and any conflict witnesses. Operational diagnosis
uses this same compiler rather than a second interpretation of configuration.

### origin-routed-filing
Each session's record is filed into the **repository the session originated in**,
determined from the session's own recorded origin, with a machine-default
fallback. In a mixed fleet this guarantees a session is **never misfiled** into a
sibling facility's logs.

### objective-by-default-profile
The baseline record is **matter-of-fact and compact** — built for retrieval, not
reading. Editorial voice or thematic shaping is available **per consumer** on its
own destination and is never imposed on the shared default.

### pluggable-landing-policy
Every sink names **how its records land** — a governed merge queue, a scoped
direct commit, a squashed PR — and the daemon core honors that choice rather than
dictating one. A consumer with strong landing guarantees keeps them; a consumer
that wants a single daily commit gets that.

### mesh-native-work-locking
The chronicler does its work **on the delegation layer's claimable mesh**, so its
"never double-log" and "recover a crashed run" guarantees are the mesh's
**atomic claim** and **liveness-reconciled recovery**, not a second lock layer.
One recurring schedule, ordinary claimable per-session tasks, one lifecycle.

### retrievable-corpus
The chronicle is a **searchable substrate** — structured so a later semantic
index can turn *"has this been tried?"* into concrete hits back into prior work.
Findability is the product; the prose is only its carrier.

## Behaviors

### idempotent-under-catch-up
Running the chronicler again after any gap **never double-files** and **never
skips** a due session. Re-processing the same corpus converges to the same logs.
This rests on the source seam's two gates — a **settle window** (never chronicle
a session still syncing) and an **already-chronicled skip** — plus the mesh's
single-winner claim. Multi-day execution gaps replay cleanly.

### fence-the-inputs-of-a-claimed-record
When a unit of chronicling is claimed, it **fences the exact session material it
will record**, so a concurrent scan cannot re-offer that same material and cause
a second unit to record it twice. A claimed record's **inputs are reserved for
it** for the life of the claim; a racing producer that re-scans finds those
inputs already spoken for and does not re-emit them. Where the mesh does not
natively fence a claimed task's *inputs* (as opposed to the task record itself),
the **source seam carries the reservation** so the guarantee holds regardless.
This is the highest-risk correctness property and is owned explicitly, not left
implicit.

### single-flight-governed-landing
A sink whose landing policy requires **serialized, governed** commits (a review
gate, a merge queue) is honored: records land **one at a time** through that
policy, never concurrently and never bypassing it. Because landing is a per-sink
strategy, a strict consumer's governance survives generalization intact while a
lax consumer's single daily commit is equally valid.

### pin-with-catch-up-not-failover
Execution stays pinned to **one** elected place. An unreachable or sleeping
elected host means the chronicle **pauses and later catches up**, not that a
standby seizes execution. Correctness never depends on the elected host being
always-on — only on catch-up being idempotent.

### objective-record-is-the-baseline
The shared default record is **neutral and complete**. Voice is never introduced
into the baseline; it is only ever added by a consumer, to that consumer's own
destination. Two consumers of the same daemon can differ entirely in tone while
sharing one pipeline.

### derive-the-origin-never-guess
A session's home repository is taken from its **own recorded origin**, not
inferred from which machine happened to run the chronicler. Only when a session
carries no resolvable origin does the **machine default** apply. Filing is a
derivation from recorded truth, with an explicit, bounded fallback — never a
guess.

### resolve-before-side-effects
Configuration resolution is deterministic and complete before the chronicler
touches session material or destinations. Repository identity is canonical, not
derived from a checkout basename; machine matching produces one selected policy
per repository; and local overrides cannot escape their owning repository.
The normalized output is reproducible from the same declarations and machine
identity.

### reject-cross-repository-collisions
Repositories do not form a hidden priority order. Two declarations that claim
the same source population in the same ownership dimension are a hard
collision, including a wildcard overlapping a specific claim unless an explicit
exclusion makes them disjoint. Diagnostics name both claimants and the
overlapping population so an operator can repair ownership rather than guess
which policy won.

### configuration-is-authorization
Installation and availability do not authorize collection. An installed,
enabled chronicler remains passive when no declaration applies and refuses to
operate when the aggregate plan is invalid. This keeps broad plugin
availability separate from permission to collect, retain, render, prune, or
publish session material.

## Non-Goals / Boundaries

- **Not a per-session live logger.** Writing the *current* session on demand, and
  clearing a local backlog by hand, are the existing interactive/backlog flows.
  This vision is the **scheduled, fleet-wide, background** chronicler on top of
  the same writer.
- **Not a second work-locking scheme.** The chronicler does **not** invent its
  own queue, lease, or claim primitive. It rides the delegation layer's claimable
  mesh; its single-winner and recovery guarantees are the mesh's, not a parallel
  lock. (A consumer's *landing* policy may hold its own governed queue — that is a
  sink concern, not the daemon's execution lock.)
- **Not a facility-bound service.** No corpus path, hostname, elected-host
  identity, persona, or landing mechanism is baked in. Every such choice is a
  seam or a schedule the consumer supplies; the daemon is generic.
- **Not the writer's replacement.** The voice-neutral writer still turns a
  manifest of sessions into Markdown. The chronicler adds **scheduling, routing,
  readiness gating, and landing** around it; it does not re-implement it.
- **Not a specification.** This fixes the *role, seams, guarantees, and
  behaviors* of the chronicler — not the storage engine, declaration schema,
  on-disk shapes, config keys, command grammar, repository-discovery mechanism,
  elected-host mechanism, or the settle window's length. Binding detail of that
  kind belongs to the reality docs.
- **Not cross-repository precedence.** A machine-local override can specialize
  its owning repository's declaration; it cannot seize another repository's
  claims. Cross-repository ownership is made disjoint explicitly or rejected.
- **Not partial execution of an invalid aggregate.** The chronicler does not
  proceed with unaffected-looking routes when another route is ambiguous or
  unsafe. Repairing the aggregate plan precedes all collection and publication.

## See Also

- Depends on: [agent-dispatch](../agent-dispatch/README.md) — the delegation
  layer whose **scheduled-production** mode drives the chronicler's recurring
  runs and whose **claimable mesh** (atomic claim, liveness-reconciled recovery)
  provides its work-locking. The chronicler is a **consumer** of that layer, not
  a layer of the fabric.
- Rides: [agent-fabric](../../agent-fabric/README.md) — the fabric the delegation
  layer belongs to.
- Reality docs:
  [`plugins/agent-logger/docs/architecture.md`](../../../plugins/agent-logger/docs/architecture.md)
  (§ *Coming soon* — the orchestrator daemon this vision envisions) ·
  [`deployment-topologies.md`](../../../plugins/agent-logger/docs/deployment-topologies.md) ·
  [`manifest-contract.md`](../../../plugins/agent-logger/docs/manifest-contract.md)
- Consumers (downstream, out of repo): a facility **permanent-record** service
  (governed merge-queue landing + character-voice profile) and the **dotfiles**
  control harness (scoped daily direct-commit landing to its `logs/` tree) are
  the first two consumers driving these seams.

## Provenance

- **2026-09-03** — Extended the vision from consumer-supplied source and sink
  seams to repository-owned declarations compiled across a machine. Established
  same-repository precedence, repository-scoped machine-local overrides,
  separate collection and rendering claim dimensions, collision-free
  cross-repository composition, explainable normalized plans, and fail-closed
  authorization before side effects.
- **2026-07-29** — Initial authoring as the shared, upstream home for the
  background-chronicling capability, so all consumers build to **one** source of
  truth rather than duplicating a per-harness vision. Intent mined from an
  operator direction to elevate agent-logger to baseline background chronicling,
  and from a two-party design alignment between the **dotfiles** control harness
  (new stakeholder / operator of an elected chronicler) and a facility
  **permanent-record** service (the current working implementation and
  compatibility steward). The daemon's guarantees — idempotent catch-up, input
  fencing of a claimed record, single-flight governed landing, and pin-with-
  catch-up execution — were crystallized from permanent-record's existing
  work-locking invariants, generalized to ride agent-dispatch's claimable mesh
  rather than a bespoke lock. The *retrieval-not-narration* purpose and the
  *objective-by-default, voice-per-consumer* split are operator framing.
