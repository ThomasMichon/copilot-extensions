# agent-index — Vision

- **Subject:** The portable **indexing & semantic-search engine** — a plugin that
  gives a harness repo and its immediate ecosystem a self-hosted, meaning-based
  retrieval layer over its *own* corpus (code, docs, issues, pull requests,
  commits), ingested as a **good citizen** of the source systems it reads.
- **Scope:** leaf (per-plugin, under the [visions index](../../README.md); honors
  the [plugin-services](../../plugin-services/README.md) service model)
- **Status:** Draft
- **Last revised:** 2026-09-04
- **Reality docs:** [`docs/architecture.md`](../../../docs/architecture.md) ·
  the plugin's future `plugins/agent-index/docs/`

## Purpose & Intent

Agents working in a repo constantly need to answer *"has this come up before?"* —
was this bug already filed, was this decision already made, does a similar change
already exist. Answering it well needs **retrieval by meaning**, not just exact
string match, across the repo's own history: its code and docs, its issues and
pull requests, its commit trail. Standing that up today means either grepping
blind or hand-building a bespoke indexing service — and, worse, hammering the
hosted systems that own that history until they throttle or block you.

**agent-index** is the **reusable engine** that makes a repo findable by meaning,
packaged as a single installable plugin. It embeds a repo's corpus into a hybrid
semantic + full-text index, keeps that index fresh as the repo changes, and
answers meaning-scoped queries through a stable surface any agent or tool on the
machine can call. It is deliberately **tightly scoped** to a harness repo and its
close ecosystem — one repo's code/docs/issues/PRs/commits, plus optional
connectors to the **hosted work-tracking and code-review feeds** that repo's work
flows through — not a facility-wide, everything-source aggregator.

The load-bearing constraint is **good citizenship**. The systems agent-index
reads from are frequently **managed services with rate limits and abuse
controls** — a hosted git forge, an issue tracker, an enterprise backlog and
pull-request feed. An indexer that crawls them naively gets throttled, degraded,
or cut off, which poisons the very freshness it exists to provide. So agent-index
treats *not getting throttled* as a first-class design goal: it ingests
incrementally, prefers change events over re-scans, backs off under pressure, and
keeps its footprint on every upstream small and polite.

The north star: an agent in a harness repo can ask a meaning question about that
repo's own work and get a ranked set of confirmable leads — from a plugin it
simply installed, that stays welcome on every upstream it reads, and that a
richer, branded search **product can be built on top of** without forking the
engine.

## Concepts & Components

### The index & store
A single retrieval index that fuses **vector similarity** with **full-text
search**, so a query is answered by *meaning and lexical match together* rather
than forcing a choice between concept and exact term. The index is a
**materialized view** — derived from, and rebuildable from, the source systems it
mirrors. It lives in a **durable data location kept separate from the plugin's
immutable, versioned runtime**: the executable logic is swapped by version
cutover (per [`plugin-services`](../../plugin-services/README.md)
§immutable-versioned-runtime), while the index **persists across cutovers and
restarts** and is never at risk from a runtime swap — with source-backed rebuild
as the safety net beneath it.

### The embedding engine
The component that turns text into vectors, at both index time and query time.
It is **acceleration-optional**: it exploits a local accelerator (GPU) when one
is present for bulk indexing throughput, and falls back to a slower CPU path
otherwise — but it **never holds search hostage**, so a query is answerable
whether or not an accelerator is warm.

The engine is a **durable, persistent, warm runtime kept separate from the
plugin's versioned service runtime** — the same durable-vs-versioned split the
index & store use. Its model stack is expensive to install and slow to load, so
it lives **outside the swappable runtime** and is **provisioned only where
indexing is hosted**; a version cutover of the light service **never rebuilds the
model stack or restarts the warm engine**. **All embedding — index time and query
time — is served by this one engine**, so the service runtime it fronts stays
light and model-free and simply talks to it. A deployment that only *consumes*
search carries **no model stack at all** and reaches a hosted engine over the
trusted transport.

### Source connectors & ingest
The pluggable adapters that pull a repo's corpus into the index:

- **First-class, built-in:** the repo's own **files** (code + docs), **commit
  history**, **issues**, and **pull requests** from its hosting forge.
- **Optional, first-class connector class:** **hosted work-tracking and
  code-review feeds** — an enterprise backlog of work items and its associated
  pull-request/review stream — for teams whose real backlog lives in a managed
  system alongside the forge. Scoped to **operator-supplied query specifications**
  — the operator curates exactly which subsets are indexed (specific work-item
  queries for chosen team backlogs and areas; pull-request filters such as "PRs
  assigned to me" or a couple of key repositories), never a whole-project or
  whole-organization firehose. Absent an explicit query specification the
  connector indexes nothing, rather than defaulting to a broad crawl.

Every connector shares one **good-citizen ingest discipline** (see Behaviors):
incremental by default, event-driven where the source offers it, and rate-aware
everywhere.

### Metadata facets
The meaning-bearing structured attributes (source, repo, path, language, item
type, labels, state) that let a semantic query be **scoped precisely** rather
than searching the whole corpus by default.

### Similarity & clusters
Retrieval beyond one-off search: **find-similar** pivots from any indexed item to
its nearest neighbors, and clustered views surface near-duplicates — the
substrate for "is this already tracked?" and prior-art discovery.

### The query surface
A **stable retrieval API** — meaning + lexical, with source/facet scoping and
find-similar — that agents, tools, and other local services call directly. It is
the plugin's public contract; presentation on top of it is a consumer's concern,
not the engine's. Its **first-class, agent-facing form is a discoverable MCP
toolset** (`search`, `find_similar`, `status`, `reindex`, `clusters`) so an agent
finds semantic retrieval by tool discovery rather than by knowing an HTTP shape.
The toolset is the stable *interface*; the *transport* that reaches the backing
service is a per-consumer concern (direct local HTTP, an SSH-forwarded port, or a
gateway URL) wired through a **configurable endpoint**, so the same toolset drops
onto any consumer unchanged.

### The engine and product seam
agent-index is intentionally an **engine, not an end-user product**. Its index
model, connector interface, and query API are the **reusable core** that a
larger, richer, possibly branded search deployment consumes and **extends** —
adding more source domains, a human-facing web experience, and house styling —
**without forking or re-implementing** the indexing and retrieval core. The
engine stays generic and unopinionated about presentation so the product layer
can be opinionated freely.

## Features

### meaning-search-over-the-repo-ecosystem
One query answers by meaning across a repo's own code, docs, issues, pull
requests, and commits — the "where was this discussed / has this come up before?"
retrieval an agent needs, scoped to the repo it is actually working in.

### hybrid-vector-and-lexical-retrieval
Vector similarity and full-text search are fused so results are strong on both
concept and exact term — an issue number, a symbol, a filename, or a phrase still
lands precisely, while a conceptual query still finds related prior work.

### good-citizen-ingestion
Ingestion is designed to **stay welcome** on managed upstreams: incremental and
delta-driven by default, event/webhook-driven where the source offers it,
rate-limit-aware with polite backoff, and small-footprint per upstream. Not
getting throttled or blocked is treated as correctness, not a nicety.

### host-good-citizen-indexing
Just as ingestion stays welcome on its *upstreams*, background indexing stays
welcome on the *machine it runs on*: it **never drives the host to critical CPU
or thermal load**. Reindex work runs at lowered priority so it **yields to
foreground and interactive work** under contention while still using idle
capacity fully — search stays answerable and the host stays responsive whether or
not a reindex is in flight. Crucially, the **built-in indexer carries this
discipline itself** rather than depending on an external wrapper, so the same
politeness holds wherever it runs — a hosted, resource-capped container **or** the
standalone user-mode CPU fallback on someone's laptop.

### pluggable-source-connectors
Sources join through a **uniform connector interface** — the built-in repo
files/commits/issues/PRs, plus an optional **hosted work-tracking + pull-request
feed** connector — so a deployment adds a source domain without changing the
index or query core. Connectors are added, not forked in. A connector over a
managed backlog is **driven by operator-supplied query specifications** (curated
work-item queries and pull-request filters), so the operator indexes exactly the
subsets they care about and nothing more.

### source-and-facet-scoping
Queries scope by source and by meaning-bearing metadata facets (repo, path,
language, item type, label, state) to return the right slice of the corpus rather
than a coincidental match across everything.

### find-similar-and-clusters
From any indexed item, retrieve its nearest neighbors; browse clusters of
near-duplicates to spot redundancy and surface prior art before work is
duplicated or a bug is filed twice.

### continuous-delta-freshness
The index tracks the repo closely through **change-driven, incremental** updates
— new commits, edited issues, merged PRs — rather than periodic full re-crawls,
so hits stay current without repeatedly re-reading unchanged history. What it
tracks is the repo's **canonical default branch as fetched from its remote** (the
pushed/merged state the team shares) — not a local working tree that may sit on a
feature branch, carry uncommitted edits, or lag `origin`. Freshness means the
index reflects what has actually landed on the mainline, fetched fresh before it
reindexes. (A configured local-only repo that has opted into hosting still
indexes cleanly from its local history — the remote is the *default* source of
truth, not a requirement.)

### lightweight-client-and-declared-host-service
Formerly `self-contained-service`.

agent-index stays lightweight on every machine unless repository-scoped
configuration explicitly opts into search and designates a host role. Client
routing and configuration resolution carry no host store or model dependencies.
Without effective configuration the plugin is inert; without a reachable or
locally supervised host runtime the search capability is honestly unavailable.

The hosted service uses the suite's
[plugin-services](../../plugin-services/README.md)
`delegated-heavy-companion-runtime` boundary. agent-index contributes attributed
declarative runtime inputs and lifecycle adapters, while the already-running
dispatch supervisor alone installs, updates, rolls back, and retires the
versioned service dependencies. Agent-facing commands and ordinary direct CLI
calls never provision the host runtime. **Which role a machine takes** — hosting
the engine versus only consuming search — is resolved from **configuration**
(machine-local, or a source repo's own `.agent-index` config), never a machine
list baked into the plugin.

### reusable-engine-extension-seam
The connector interface and query API are a **stable extension surface**: a
downstream product layers additional sources, a human search experience, and
branding **on top of** the engine, consuming it rather than re-implementing it.

### recoverable-rebuildable-index
The index and any processing state are **derived** and reconstructable from the
source systems (the repo, the forge, the tracked feeds). Fast snapshots are
welcome; source-backed rebuildability is the safety net, so a corrupt or lost
index is a rebuild, never lost data.

## Behaviors

### good-citizen-under-managed-services
This is the behavior the plugin is built around. Against any upstream — a hosted
forge, an issue API, an enterprise backlog/PR feed — agent-index:

- **ingests incrementally**, reading only what changed since last time rather than
  re-scanning whole corpora;
- **prefers change events** (webhooks / delta APIs) over polling, and polls
  **gently** with a bounded cadence when events aren't available;
- **respects the server's own signals** — rate-limit headers, retry-after,
  quota — and **backs off exponentially** under pressure instead of retrying into
  a wall;
- **keeps its footprint small and bounded** (batched, concurrency-capped, scoped
  to declared areas) so it never looks like abuse.

A managed upstream should barely notice the indexer is there. Getting throttled,
degraded, or blocked is treated as a **defect**, not an acceptable cost.

### responsive-when-cold
Search stays usable when the embedding accelerator is cold or absent: query-time
embedding never depends on an always-warm GPU, and the surface degrades
progressively (fast lexical first, full semantic as the engine warms) rather than
hanging.

### research-aid-not-authority
An agent-index hit is a **lead to confirm**, not a verdict. The index can lag the
very latest change, so a result points back at the source of truth — the working
tree, the forge, the tracker API — which the caller confirms against before
acting. The engine points at truth; it is not truth.

### fresh-and-recoverable
Indexing preserves freshness through incremental updates and can be **rebuilt from
source** without data loss; similarity and cluster artifacts refresh as part of
indexing rather than drifting into staleness.

### observable-durable-indexing
Indexing work is **durable across restarts**, **deduplicated** against repeated
triggers (a redelivered webhook does not re-embed the world), and **visible
enough** that an agent or operator can tell what is queued, running, failed,
current, or stale — without spelunking logs.

### zero-downtime-cutover
Deploying a new version of the engine is **zero-downtime**, honoring the
[`plugin-services`](../../plugin-services/README.md) §zero-downtime-cutover
behavior: the new version is health-gated, the client-followed routing record
flips atomically, in-flight **searches** drain, and **scheduled/queued indexing
work is handed off** to the new version rather than dropped or run twice — then
the old version retires (reversible up to a commit point). The **durable index is
untouched** by the swap (it lives outside the versioned runtime), so an upgrade or
rollback never rebuilds it or interrupts search.

### warm-durable-engine
The embedding engine is a **durable, persistent, warm** runtime, decoupled from
the versioned service so the two evolve on **separate lifecycles**. A routine
service version cutover swaps only the light service and **leaves the engine — and
its loaded model — untouched**: no model-stack rebuild, no cold reload. The
engine's own runtime is updated **only by its own explicit path**, when the model
stack itself changes; its durable environment survives service updates and
rollbacks just as the durable index does. Because the model stack is provisioned
only where the engine is hosted, the cost of that stack is paid **once, on one
host**, not re-paid on every service update or by every consumer.

### local-first-standalone
The service is machine-local by default and reachable using only what its own
installer deployed — no external proxy, mesh, or registry required. When a client
is on **another host**, it reaches the service over an explicit, opt-in **trusted
transport** — an **SSH port-forward** of the service's own local endpoint (the
[`plugin-services`](../../plugin-services/README.md) minimal-network-exposure
posture, rung 4 of the service-transport ladder) — so the service still opens no
new inbound port of its own. **Adoption generates each client's routing
configuration** pointing at the designated indexer and reaching it over that
transport, so a client is *configured* to reach the one host, never hand-wired.
Fronting it with shared routing is likewise an explicit **consumer** choice, never
a prerequisite the plugin bakes in. A machine that only consumes search installs
**no model stack** and reaches the engine-hosting service over that transport, so
the heavy runtime lives on **one host**, not on every consumer.

### adoption-designates-ordered-indexers
Adopting agent-index into a harness repo is an explicit **onboarding act**, not an
implicit per-machine guess: it designates an **ordered set of indexer machines** —
a **primary** that hosts the service and engine, plus zero or more **secondaries**
that host their own parallel index — and every other machine installs as a **search
client** that reaches out to them. The **order is the failover preference**: a
client routes to the **first reachable** indexer, so a down primary or a broken SSH
hop transparently falls back to a secondary — robustness for an SSH-mesh where any
one host may be asleep or unreachable. The common cases are the degenerate ones:
**exactly one** indexer (a client dials the single host), and a **single-machine**
repo offered the **full local stack** (host and client on one box). Each indexer
carries its **own** SSH alias and endpoint, so *which machines index* and *which
server a given client dials* stay independent — a client can prefer a
topologically-closer indexer. The designation (the ordered list) and each machine's
role are written to **configuration** (the source repo's `.agent-index` and/or
machine-local), so role stays config-resolved with **no machine names baked into
the plugin** — adoption is simply what *writes* (or reads) that configuration.
Running install/setup **on a designated machine** configures and starts (or
restarts) its local service and engine; running it **elsewhere** installs the client
and its ordered routing to reach the designated indexers. Parallel indexers each
rebuild from source independently (per **recoverable-rebuildable-index**), so no
cross-host replication or consensus is assumed — redundancy comes from parallelism,
not coordination.

### capability-matched-engine-runtime
The indexer's engine is matched to the host's **real capabilities** rather than
assuming an accelerator. Adoption **detects accelerator (CUDA) compatibility and
machine specs** (compute, memory) and selects the embedding **device** accordingly:
a compatible GPU when present, **CPU only when the host has enough compute and
memory** to serve embeddings acceptably. An underpowered indexer candidate is
**flagged** — not silently accepted — so the operator can pick a better host; and
the engine never wedges by insisting on an accelerator that isn't present, falling
back within its capability floor. Capability, like role, is resolved into
**configuration** at adoption, not hardcoded per machine.

### engine-stays-generic
The engine does not grow product opinions. Branding, a house web experience,
organization-specific source domains, and editorial presentation belong to the
**consumer** that builds on the seam — not to agent-index. Keeping the core
generic is what lets many different products reuse it.

## Non-Goals / Boundaries

- **Not the source of truth.** agent-index indexes and points; the working tree,
  the forge, and the trackers own the canonical content. It is a materialized
  view, never the record.
- **Not a general database.** It is a semantic-retrieval layer, not a
  transactional store other services write their state into.
- **Not keyword-only search.** Exact-symbol / known-string lookup in a checked-out
  tree is a grep / code-intelligence job; agent-index's value is *meaning* and
  cross-corpus prior art.
- **Not a facility-wide, all-source aggregator.** The engine is scoped to a
  harness repo and its close ecosystem. A comprehensive, many-source, branded
  search deployment is a **separate product built on this engine** (via the
  extension seam), not a responsibility of the engine itself.
- **Not an end-user product.** No mandated web UI, no house styling, no editorial
  voice live in the engine; those are the consuming product's concern.
- **Not a shared-infrastructure dependency.** It requires no external reverse
  proxy, tunnel broker, service mesh, or central registry to be installed or
  reached; a downstream may layer such routing on top, but the plugin never
  assumes it.
- **Not a cross-model blender.** Results within one embedding space are coherent;
  the engine does not silently mix incompatible vector spaces into one ranking.
- **Not spec-level here.** Embedding model choices, store engines, endpoint
  shapes, connector wire formats, batch sizes, and rate-limit constants live in
  the reality docs or a future `specifications` layer, not in this vision.

## See Also

- Parent: [visions index](../../README.md)
- Honors: [plugin-services](../../plugin-services/README.md) — the service model
  every service-bearing plugin obeys (self-contained runtime, discoverable local
  endpoint, à-la-carte, platform-native lifecycle, minimal network exposure).
- Reality docs: [`docs/architecture.md`](../../../docs/architecture.md) · the
  plugin's future `plugins/agent-index/docs/`.

## Provenance

- **2026-07-29** — Initial authoring as a per-plugin leaf. Intent mined from
  extracting the reusable **indexing and semantic-search core** out of a larger,
  comprehensive semantic-search deployment into a standalone, portable plugin —
  scoped tightly to a harness repo's own code, docs, issues, pull requests, and
  commits, with an optional first-class connector for **hosted work-tracking and
  pull-request feeds**. The load-bearing constraint, crystallized from the risk of
  being throttled by managed upstream services, is **good-citizen ingestion**
  (incremental, event-driven, rate-aware, small-footprint). The engine is framed
  as a reusable core a richer downstream product **consumes and extends** via a
  stable connector/query seam, rather than a product that replaces it.

- **2026-07-29** — Added the **zero-downtime-cutover** behavior, separated the
  **durable index** from the immutable/versioned runtime (§The index & store), and
  sharpened **local-first-standalone** to name **SSH port-forwarding** as the
  opt-in cross-host reach. Mined from an operator directive: the engine's
  executable logic installs as an immutable, junction-selected versioned runtime
  with ZDD/cutover for scheduled indexing and in-flight requests; the index lives
  in a durable location that survives cutovers; and in a multi-machine deployment
  other hosts reach the single service over SSH. Honors the `plugin-services`
  §zero-downtime-cutover / §minimal-network-exposure behaviors; realized at intent
  level by the shared `zdd` cutover library.

- **2026-07-31** — Sharpened the **hosted work-tracking connector** scoping intent:
  ingestion is driven by **operator-supplied query specifications** (curated
  work-item queries and pull-request filters — chosen team backlogs, key areas,
  "PRs assigned to me"), and the connector indexes **nothing** absent an explicit
  query, rather than defaulting to a whole-project crawl. Mined from an operator
  clarification that they will supply the exact PR/work-item subsets to index.
  Reinforces good-citizen ingestion and "never a firehose."

- **2026-08-03** — Extended **The embedding engine** into a **durable, persistent,
  warm runtime decoupled from the versioned service** (the same durable-vs-versioned
  split as the index & store), added the **warm-durable-engine** behavior, and
  sharpened the then-current **self-contained-service** /
  **local-first-standalone** intent: the model stack is expensive, so it is
  provisioned **only where indexing is hosted** and outside the swappable runtime
  — a routine service cutover **never rebuilds the model stack or restarts the
  warm engine**; **all embedding (index + query)** is served by that one engine
  so the service runtime stays light and model-free; a search-only consumer
  carries **no model stack** and reaches the host over the trusted transport; and
  a machine's **role** (engine host vs consumer) is resolved from
  **configuration** (machine-local or a source repo's `.agent-index`), never a
  machine list baked into the plugin. Mined from an operator directive that torch
  belongs only on the indexing host, as a persistent daemon (a session-host
  analogue) that survives plugin updates. Drives the `agent-index-engine-daemon`
  effort; `dev16`'s configurable engine-separation modes are the foundation.

- **2026-08-03** — Added **adoption-designates-one-indexer** and
  **capability-matched-engine-runtime**, and sharpened **local-first-standalone**
  so **adoption generates each client's routing** to the designated host. Mined
  from an operator directive on the intended onboarding model: adopting agent-index
  into a harness repo **designates exactly one machine as the indexer** (a
  single-machine repo is offered the full local stack); adoption **detects CUDA
  compatibility and machine specs** and selects the engine **device** — GPU when
  compatible, CPU only above a capability floor, flagging an underpowered host
  rather than degrading silently; running install/setup **on the designated
  machine** configures and (re)starts the local service+engine, while **every other
  machine** installs the client with routing to reach the designated host. Role and
  capability both resolve into **configuration** at adoption — still no machine
  names in the plugin. Extends the `agent-index-engine-daemon` effort (its
  role-aware install and external-seam defaults are the foundation this builds on).

- **2026-08-14** — Evolved **adoption-designates-one-indexer** →
  **adoption-designates-ordered-indexers**: the designation is now an **ordered set**
  (a primary + optional secondaries), each indexer carrying its **own** SSH alias and
  endpoint, and a client routes to the **first reachable** one — **failover** for
  SSH-mesh robustness where a host may be asleep or unreachable. Mined from an
  operator directive while bringing up a **second, parallel indexer** (dev6 alongside
  cloud1): *every client must know which server(s) to reach, and one hard-coded target
  is too brittle for an SSH mesh; the routing target should be independent of which
  machine holds the indexer role.* Parallel indexers each rebuild from source
  independently (per **recoverable-rebuildable-index**) — redundancy through
  parallelism, not cross-host replication or consensus. The single-indexer and
  single-machine cases remain the degenerate forms (back-compatible: a singular
  `indexer:` block and a lone `endpoint:` still work). Realized in config
  (`indexers:` list, `read_indexers`, ordered-failover `client_url`), adoption
  (`setup` resolves role/routing from an authored list), and tests.

- **2026-09-04** — Replaced **self-contained-service** with
  **lightweight-client-and-declared-host-service**. Repository-scoped
  configuration remains the opt-in and role authority, but heavyweight host
  service dependencies move behind the shared
  `delegated-heavy-companion-runtime` boundary: agent-index declares inputs and
  lifecycle adapters, while the running dispatch supervisor alone provisions
  and selects immutable service runtimes. Direct plugin commands cannot install
  the host stack, and missing configuration or supervision remains inert rather
  than triggering a fallback installer.
