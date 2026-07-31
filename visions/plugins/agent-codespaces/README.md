# agent-codespaces — Vision

- **Subject:** The **CodeSpace venue provider** of the agent fabric — the plugin
  that provisions GitHub Codespaces for a repo, sets them up to **run agents
  headlessly**, reaches them over a single secured transport, lends them the
  **host's** credentials without minting secrets inside them, and presents those
  CodeSpace agents to the fabric under the **same coordination contract** as a
  local one.
- **Scope:** leaf (a per-plugin vision under the [agent-fabric](../../agent-fabric/README.md) branch)
- **Status:** Draft
- **Last revised:** 2026-07-30
- **Reality docs:** [`docs/architecture.md`](../../../docs/architecture.md) (install
  topology, the credential-relay path, the `codespace:` resolver) · the plugin's
  [`README`](../../../plugins/agent-codespaces/README.md) and its skills
  (`codespaces-lifecycle`, `codespaces-setup`, `borrowing-codespaces`)
- **Inherits:** [plugin-services](../../plugin-services/README.md) — the service
  model's invariants are design contracts this venue provider must honor.

## Purpose & Intent

A fabric agent should be able to run **wherever the work is cheapest and
safest** — and for a great deal of work that place is a **GitHub Codespace**: a
throwaway, cloud-hosted, repo-shaped machine that needs no local checkout and
disappears when it is done. agent-codespaces is the fabric's **venue provider**
for that world. It turns "a repo" into "a reachable, credentialed, agent-running
CodeSpace," and then hands that CodeSpace to the rest of the fabric as just
another **coordination-layer participant** — created, inspected, driven, and
recovered by the *same* contract a local worktree agent uses.

The north star is that **where an agent runs is a venue detail, not a different
interface.** A caller says "run this in a CodeSpace for `owner/repo`" and the
provider handles the rest: boot the machine (or wake a shut-down one), set it up
to host an agent, open one secured door to it, make the host's identity reachable
*through* that door so the CodeSpace can talk to GitHub and Azure DevOps **without
ever holding a token of its own**, and make sure that when the ephemeral machine
goes away its **work does not vanish with it**.

Two promises sit under everything:

- **Borrow identity, never bottle it.** A CodeSpace authenticates as *you* by
  reaching back to the host's credential store over the one trusted transport —
  so no personal access token, no long-lived secret, is ever written into a
  disposable cloud machine.
- **Ephemeral venue, durable work.** The machine is disposable; the *work* is
  not. Tearing down (or pausing) a CodeSpace first rescues its session state, so
  a successor agent can pick up what a vanished one was doing.

And one scarcity truth shapes the whole model: **CodeSpaces are a finite, shared
budget, not an infinite cloud.** An account has a bounded compute quota (a fixed
ceiling of concurrently-running cores), and every agent on every machine draws
from the **same** pool. So the provider is not only a *factory* of venues but a
**steward of a scarce resource**: it allocates within the budget without
exceeding it, **reuses** an existing suitable venue before paying to create
another, **cycles out** venues that have gone stale so their budget returns to
the pool, and keeps the whole pool **legible** — who holds what, where, and in
what state — so agents (and the operator) coordinate instead of colliding or
quietly exhausting the quota.

## Concepts & Components

### The venue — a provisioned, agent-ready CodeSpace
A **CodeSpace** is the venue this provider owns: created for a named
`owner/repo` from repo-level defaults, **booted on demand** (a shut-down machine
wakes when reached — no explicit start step), **set up to host an agent**
(the right plugins and environment injected so a Copilot agent runs headlessly),
and torn down or paused when the work is done. The provider governs the venue's
whole lifecycle — create, list, status, stop, finalize, cleanup — not just its
creation.

### The one door — a shared, multiplexed transport
All reach into a CodeSpace — an interactive shell, a one-shot command, or a
structured agent channel — goes through **one shared, multiplexed SSH
transport**. There is a **single door**, not a door per caller: connection state
(and the credential path layered on it) is shared, so a raw side-channel that
bypasses the provider's transport is a bug, not an alternative. The transport is
the seam every other capability rides on.

### The credential relay — the host's identity, lent over the door
The **credential relay** lets an agent inside a CodeSpace authenticate to
GitHub and Azure DevOps using the **host's** credentials, fetched **just in
time** back across the transport and **never stored** in the CodeSpace. The relay
is **policy-gated** (each request is checked against an allowed action + host/
resource allowlist), **pluggable** in what identity sources it draws on (git
credential, GitHub auth, Azure login), and **secret-silent** (a lent token is
never logged). It is the mechanism behind *borrow identity, never bottle it*.

### The coordination-layer face — a provider, not a new interface
The provider presents its CodeSpace agents to the fabric's **coordination
layer** so a remote CodeSpace agent is addressed by the fabric's **one contract**.
A CodeSpace is **resolvable by name** (raw or friendly), and resolving one
**wakes it, opens the credentialed door, and spawns the agent** — all beneath the
coordination layer, which neither knows nor cares that this participant lives in
the cloud. (This is the parent's *uniform-venue-reach*, realized for CodeSpaces.)

### Config by adoption — the repo owns its venue policy
A repo's CodeSpace policy — machine size, region, credential sources, and
setup hooks — lives **in the adopting repo** and is **read live**, with no
generated intermediate copy to drift. **Adoption** is the one act that wires a
repo to the provider; ordinary operation reads that policy without mutating the
repo. (The parent service model's *install/adopt boundary*, seen from the venue
side.)

### Per-repo identity — the right account for the target org
Provisioning and reaching a CodeSpace act under the identity that can **see the
target repo's org**, resolved per target rather than assumed from an ambient
default — with **discovery merged across accounts** so a CodeSpace is never
hidden just because a different account was active. This is **purely additive**:
with no multi-account policy configured it collapses to a single ambient identity.

### Session survival — rescue before teardown
Because a CodeSpace is **ephemeral**, closing one out (finalize) or pausing one
(stop) **rescues its session state first**, handing the raw material to the
fabric's **memory layer** so a later agent can digest what happened. The provider
*triggers and gates* recovery on teardown; it does not own the recovery/
compilation engine.

### Advisory borrow — cooperative sharing of a scarce venue
A CodeSpace is a **shared, scarce** resource; several agents can contend for
one. The provider offers an **advisory borrow/return** (a lease binding a venue
to the **worktree/effort** using it) so agents **cooperate rather than
collide**, plus **startup tolerance** that waits patiently for a slow-booting
machine instead of declaring it dead.

### The venue pool — a finite, budget-bounded, shared resource
The set of an account's CodeSpaces is a **pool** drawn against a **bounded core
budget**. The provider treats that pool as a resource to **allocate within, not
exceed**: it knows what the pool contains, what each member costs against the
budget, and how much headroom remains — so a request is satisfied by **reusing**
a suitable idle member when one exists, by **recycling** a stale member to free
budget, or by **creating** a new one only when the budget allows, and by
**surfacing pressure** when it does not. The pool is **shared across every agent
and every machine** on the account, so its accounting is a single shared truth,
not a per-box guess. (The per-machine advisory lease is today's partial
realization; the north star is a pool coordinated across the whole account.)

### CodeSpace state — the disposition every venue carries
Every venue in the pool has a **derivable disposition** that says whether it is
safe to reuse, idle enough to recycle, or actively held. The vision fixes the
*meanings*, not the encoding:

- **in-use** — actively held by a live worktree/agent (a current lease with a
  live holder); do not reallocate.
- **idle** — running and healthy but **not** currently held — a prime candidate
  for **reuse** before creating anything new.
- **clean / fresh** — provisioned and set up but never (or no longer) carrying
  unrescued work — safe to allocate immediately.
- **stale** — idle or held-by-a-dead-holder past a freshness threshold, or drifted
  from its repo's setup — a candidate to **recycle** (rescue-then-reclaim) so its
  budget returns to the pool.
- (plus the transient provisioning/failed states the lifecycle already exposes.)

Alongside the disposition, each venue carries its **allocation**: which
**repo** it serves, which **worktree/agent** holds it, and on which **machine**
— the facts a presenter needs to answer "what is available, and who has what?"

### Allocation legibility — the pool as a presented surface
The pool's membership, per-venue **state**, **allocation**, and **budget
headroom** are **externally derivable** so the fabric's front door — the
[Worktree Picker](../../picker/README.md)'s **CodeSpaces** pivot — can present,
across all machines from one place, what CodeSpaces exist, which are free vs.
allocated, to which repo/worktree/agent each is bound, and how much of the core
budget is spent. The provider **owns** this truth; the Picker **renders** it
(the fabric's *derive-don't-duplicate*), and never a second copy.

## Features

### venue-provisioning
Turn a named `owner/repo` into a running, agent-ready CodeSpace: create from
repo-level defaults, **boot on demand**, run the repo's declared setup hooks, and
manage the venue across its whole lifecycle (create / list / status / stop /
finalize / cleanup). Adding, moving, or losing a venue does not change how its
agents are addressed.

### headless-agent-hosting
A provisioned CodeSpace is **set up to run a Copilot agent headlessly** — the
right plugins and environment are injected so an agent launched there is a
first-class fabric participant, not a bare shell.

### single-transport-reach
Every form of reach into a CodeSpace flows through **one shared, multiplexed
transport**, so connection and credential state are established once and reused.
The provider's wrapped door is the *only* supported path; going around it is
unsupported by design.

### host-credential-relay
An agent in a CodeSpace authenticates to GitHub and Azure DevOps as the **host
user**, via a **just-in-time, policy-gated, secret-silent** relay — **no personal
access token or long-lived secret is ever stored in the CodeSpace**. The set of
identity sources is pluggable and each is admitted only for its allowed hosts/
resources.

### coordination-layer-provider
CodeSpace agents are presented to the fabric under its **one coordination
contract**: name-resolvable (raw or friendly), and resolving one **wakes the
machine, opens the credentialed door, and spawns the agent** transparently. A
CodeSpace agent is created, inspected, and reached exactly like a local one.

### config-by-adoption
A repo's venue policy lives **in that repo** and is **read live** (no generated
intermediate), with per-repo overrides. Provisioning and reach honor it without
copying or mutating it; only **adoption** wires a repo in.

### per-repo-identity
Host-side operations run under the account that can access the **target repo's
org**, resolved per target, with **cross-account discovery** so no CodeSpace is
hidden by the active-account default. Fully **additive**: unconfigured, it is a
single ambient identity.

### session-survival
Teardown and pause **rescue session state first**, so an ephemeral CodeSpace's
work is recoverable and digestible by a successor even though the machine is
gone. Stale local reach-state (orphaned SSH config, multiplex sockets) is
reclaimable via an explicit cleanup. Rescue is not only a graceful-teardown
courtesy — see *telemetry-grade-session-capture*.

### advisory-borrow
A cooperative **borrow/return** lets agents share a scarce CodeSpace without a
hard lock, and **startup tolerance** waits out a slow boot rather than failing
early. The borrow binds a venue to the **worktree/effort** using it, so a second
agent knows it is taken.

### bounded-shared-pool
The account's CodeSpaces are managed as a **finite pool against a bounded core
budget**, shared across every agent and machine. The provider **accounts for**
what the pool holds and how much budget remains, and satisfies a venue request
**within** that budget — never blindly creating past the quota. When the pool is
saturated it **reuses, recycles, or reports the pressure** rather than failing
opaquely or over-provisioning.

### reuse-over-recreate
A venue request prefers **reusing an existing suitable idle CodeSpace** (right
repo, healthy, unheld) over paying to **create** a fresh one. Creation is the
fallback when nothing fits, not the default — so the pool's scarce budget is
spent on genuinely new need, not on redundant duplicates of a venue that already
exists.

### cross-machine-allocation
An allocation (lease) is coordinated across **all machines on the account**, not
just the local box — because the pool is cloud-global. Two agents on two
different machines cannot unknowingly drive the **same** CodeSpace; the holder,
its worktree/agent, and its machine are discoverable from any machine, and a
takeover is a **deliberate, visible** act. (Reality coordinates the same-machine
case with a host-local lease and *plans* a cloud-global beacon; this is the north
star that generalizes it.)

### codespace-state-model
Every venue carries a **derivable disposition** — *in-use / idle / clean / stale*
(plus the transient provisioning/failed states) — and its **allocation** (repo,
worktree/agent, machine). This is the shared vocabulary agents and presenters use
to decide what to reuse, what to recycle, and what is safe to touch. The vision
fixes the **meanings**; the encoding is spec-level.

### staleness-recycling
CodeSpaces **go stale** — abandoned by a dead holder, idle past a freshness
threshold, or drifted from their repo's setup — and are **cycled out
periodically** so their budget returns to the pool. Recycling is **safe by
construction**: it rescues any unsaved session data **before** reclaiming
(*recycle-rescues-first*), and never reclaims a venue with a **live** holder.
Budget is reclaimed from what is genuinely idle/stale, never stolen from active
work.

### allocation-legibility
The pool's membership, per-venue **state**, **allocation**, and **budget
headroom** are **externally derivable** through the same programmatic surface the
fabric renders elsewhere, so the [Worktree Picker](../../picker/README.md)'s
**CodeSpaces** pivot can present — across all machines, from one place — what
exists, what is free vs. allocated, to which repo/worktree/agent, and how much of
the core budget is spent. The provider **owns** this truth; presenters derive it,
never duplicate it.

### telemetry-grade-session-capture
**All** agent session data produced on a CodeSpace is **captured and rescued** —
not only on a graceful finalize, but including from a **stale or abruptly
recycled** venue — so it can be **mined later for logging and usage telemetry**.
The provider's job is to make the capture **comprehensive** (nothing an ephemeral
venue produced is silently lost to teardown) and hand it to the fabric's memory
layer; that layer owns compiling and analyzing it (see Non-Goals). Ephemerality
of the machine never means loss of the record.

### discoverable-relay-endpoint
The credential relay is reached at an endpoint **discovered from the service's
own runtime state**, **collision-free by construction**, and **minimal-exposure**
— preferring an OS-native local endpoint or an OS-assigned ephemeral port
advertised through discovery over a **fixed, well-known** one, and crossing the
host↔CodeSpace boundary only through the **already-trusted transport tunnel**.
(The parent service model's *discoverable-local-endpoint* / *collision-free-
endpoints* / *minimal-network-exposure*, applied to the relay.)

### standalone-and-composable
The provider's **own** installer deploys everything its **core venue function**
(provision, reach, relay the host's identity) needs to stand up on its **own** —
a lone install is a first-class configuration. When the coordination and memory
layers are also present, the provider **composes** with them (agents become
fabric-addressable; teardown rescues sessions); when they are absent, those
cross-layer features **degrade gracefully** rather than taking the core venue
function down with them. (The parent's *a-la-carte-installability* /
*graceful-composition* / *degrade-gracefully*.)

### version-skew-tolerant-reach
The host↔CodeSpace contracts — the relay protocol, the resolver handshake, the
setup the venue expects — **tolerate version skew** within a declared window: a
host and a CodeSpace (or the provider and the coordination layer it registers
with) may be at **different versions** and still interoperate by additive,
backwards-compatible evolution, rather than demanding a lockstep match. (The
parent's *version-skew-tolerant-contracts*.)

## Behaviors

### boots-on-connect
Reaching a shut-down CodeSpace **wakes it**; there is no separate start step and
no error for "it was asleep." First reach may be slow, and *advisory-borrow*'s
startup tolerance covers that patiently.

### credentials-borrowed-not-bottled
A CodeSpace never holds a credential of its own. It authenticates by reaching the
**host's** store over the transport at the moment of need; the token is used and
**not persisted, not logged**. Removing the CodeSpace leaks nothing.

### fail-loud-not-hang
When a credential cannot be resolved, the relay makes the request **fail fast
with its real cause** rather than dropping the agent into an interactive prompt or
an indefinite hang, and **verifies the workspace's remote-domain auth up front on
connect** so a missing sign-in is reported *before* work begins, not discovered
mid-fetch. (The parent's *fail-loud-on-endpoint-error*, applied to credentials.)

### local-first-relay-exposure
The relay is **host-local by default** — reachable by a CodeSpace **only through
the trusted transport tunnel**, never by an ambient network listener open to the
wider machine or network. Crossing the host↔CodeSpace boundary is an **explicit,
opt-in tunnel over the already-trusted transport**, and the steady-state ideal is
that lending identity to a CodeSpace adds **no new externally-reachable listening
port** to the host.

### adoption-only-mutates-owned-repos
Installing or running the provider **never alters a repo**. The venue policy is
read live from the adopting repo; only the explicit **adopt** act writes anything
into a repo, and you only adopt a repo you own. (The parent's
*install-leaves-repos-unaltered* / *install-adopt-boundary*.)

### recover-not-lose
A torn-down, paused, **or recycled** CodeSpace **does not silently lose its
work**: session state is rescued and handed to the memory layer **before** the
machine is deleted, stopped, or reclaimed, and a teardown/recycle that *cannot*
rescue is surfaced rather than proceeding blindly. (The parent fabric's
*recover-not-lose*, for the CodeSpace venue.)

### borrow-is-advisory-not-locking
The borrow is a **cooperation signal, not a mutex**: it tells other agents
"someone is using this" so they choose another venue or coordinate, but it never
hard-locks a CodeSpace or blocks a deliberate, **visible** override.

### budget-not-exceeded
The provider **never over-provisions past the account's core budget**. Facing a
venue request with no headroom, it **reuses an idle member, recycles a stale one,
or surfaces the pressure** — an explicit, diagnosable "the pool is full" — rather
than silently minting a CodeSpace that breaches the quota or opaquely failing.
The scarce resource is respected by construction, not by operator vigilance.

### coordinated-allocation-across-machines
Allocation is a **single shared truth** across the account's machines: an agent
learns whether a CodeSpace is already held — and by which worktree/agent, on
which machine — **before** driving it, so two agents never unknowingly share one
venue. Conflict is **explicit** (it names the live holder) and takeover is a
**deliberate** act, exactly as the same-machine advisory lease behaves today,
generalized to the whole pool.

### recycle-rescues-first
Cycling a stale CodeSpace out **rescues its session data first, then reclaims**
— never the reverse. A recycle checks the venue is genuinely idle/stale (no live
holder) and captures anything unsaved before the machine is reclaimed, so freeing
budget can never destroy work. Recycling is a *rescue-then-reclaim*, not a delete.

### capture-is-comprehensive
Session capture aims at **completeness**, not best-effort: data an ephemeral
venue produced is captured even when the venue ends **abruptly** or is recycled,
so the later logging/telemetry record has no silent holes. Where a venue vanishes
before capture completes, that gap is **surfaced** rather than masked.

### derive-allocation-not-duplicate
The pool's membership, state, allocation, and budget accounting are **owned by
this provider** and **derived** by every presenter (chiefly the Picker's
CodeSpaces pivot) at read time. Presenters and peers read this truth; they do not
persist a competing copy of it. (The fabric's *derive-don't-duplicate*, applied
to the venue pool.)

### degrade-not-fail-without-peers
Absent the coordination or memory layer, the provider still performs its **own**
venue function (provision, reach, relay); the cross-layer features (fabric
addressing, session rescue on teardown) simply **stay dark** until the peer is
present. A missing sibling degrades a feature, never the core venue.

### public-artifact-clean
Because the provider's own source is world-readable, its shipped artifacts,
scaffolds, and generated config **never carry internal org/account/repo names or
personal aliases**; that separation is enforced structurally, not left to
reviewer vigilance. (The repo's public-artifact rule, owned by this venue's
tooling.)

## Non-Goals / Boundaries

- **Not the coordination layer.** The provider **presents** CodeSpace agents to
  the fabric's one contract, but driving/messaging a running agent turn-by-turn
  is the coordination layer (agent-bridge), not this vision.
- **Not the memory layer.** The provider **triggers, gates, and makes
  comprehensive** the capture/rescue of session data on teardown or recycle, but
  **recovering, compiling, segmenting, and mining** that data into logs and usage
  telemetry is the memory layer's job (agent-logger). This vision owns *that
  capture happens and loses nothing*, not *how the record is analyzed*.
- **Not a general compute scheduler or quota system.** The pool this provider
  stewards is the **account's GitHub Codespaces budget** — it accounts for and
  allocates *that* scarce resource. It is **not** a general-purpose cloud-compute
  scheduler, a cross-account quota broker, or a fair-share/priority engine; how
  competing demands are prioritized is a consumer concern layered on top.
- **Not the connectivity substrate.** Provisioning and maintaining the underlying
  SSH substrate (keys, host-key trust, per-machine transport) is the fabric's
  connectivity concern; this provider **consumes** a trusted transport to reach a
  CodeSpace, it does not own the mesh.
- **Not a container or other-machine venue.** Sibling venue providers own local
  containers and cross-machine reach; this vision is scoped to the **GitHub
  Codespace** venue.
- **Not a secret store or account minter.** The provider **borrows** the host's
  identity per request; it never mints a per-agent account and never persists a
  credential inside a CodeSpace.
- **Not a specification.** This vision fixes the venue provider's **role,
  guarantees, and behaviors**, not the wiring — it does not pin the transport
  mechanism, the relay's port or protocol, the resolver's namespace grammar, the
  config file's schema, or the CLI surface. Binding detail of that kind belongs to
  the reality docs or a future `specifications` layer.

## See Also

- Parent vision: [agent-fabric](../../agent-fabric/README.md) — §Concepts/
  *agent-codespaces — a venue provider*; the fabric behaviors *uniform-venue-reach*,
  *recover-not-lose*, *one-fabric-many-venues* this leaf realizes.
- Inherited invariants: [plugin-services](../../plugin-services/README.md) —
  *discoverable-local-endpoint*, *collision-free-endpoints*,
  *minimal-network-exposure*, *a-la-carte-installability*, *graceful-composition*,
  *degrade-gracefully*, *install-adopt-boundary*, *version-skew-tolerant-contracts*.
- Sibling leaves: **agent-containers** *(local-container venue, when authored)* ·
  [agent-ssh](../agent-ssh/README.md) *(the connectivity substrate this venue's
  reach rides on)*.
- Presenter: [picker](../../picker/README.md) — the Worktree Picker's **CodeSpaces**
  pivot *renders* this venue's pool membership, per-venue state, allocation, and
  budget headroom (owned here; the Picker never redefines or re-stores them).
- Consumer: [agent-logger](../agent-logger/README.md) — the fabric's **memory layer**
  that compiles and mines the rescued CodeSpace session data into logs and usage
  telemetry (this vision guarantees the *capture*; agent-logger owns the analysis).
- Reality docs: [`docs/architecture.md`](../../../docs/architecture.md) · the
  `plugins/agent-codespaces/` skills (`codespaces-lifecycle`, `codespaces-setup`,
  `borrowing-codespaces`).

## Provenance

- **2026-07-30** — Initial authoring as a **backport** (per the `backporting-visions`
  skill): the intent was reverse-engineered from agent-codespaces' current reality
  (README, the three skills, `docs/architecture.md`) and written as a deliberate
  **superset** of it, so every vision→reality delta is additive (build-out). The
  fold-back captured the venue lifecycle, the single multiplexed transport, the
  host-credential relay (borrow-not-bottle), the coordination-layer provider face,
  config-by-adoption, per-repo identity, session survival, and advisory borrow. The
  **north-star-ahead** additions state the parent [plugin-services](../../plugin-services/README.md)
  invariants in the venue's own terms — chiefly *discoverable-relay-endpoint*
  (reality's **default/CLI path** reaches the relay at a **fixed** loopback port
  over a reverse tunnel, while an **OS-ephemeral bind + a rendezvous portmap** are
  already emerging on the daemon path — so the invariant is met only *partially*)
  and *standalone-and-composable* (reality hosts the relay and resolver inside the
  coordination layer's process, versus the à-la-carte independence the service
  model wants) — so closing those deltas **builds the endpoint-discovery and
  standalone-relay machinery out**, never scales the plugin back. No Non-Goal was
  written that reality violates. The endpoint-discovery delta is already tracked by
  [issue #54](https://github.com/ThomasMichon/copilot-extensions/issues/54)
  (transport hardening → OS-native endpoints + rendezvous discovery); it is cited,
  not refiled.

- **2026-07-30** — Extended (vision-extending, in place) with the **venue-pool /
  resource-stewardship** intent, mined from operator direction that CodeSpaces are
  a **finite, shared budget** (an account's bounded concurrent-core quota) that
  every agent on every machine draws from, so they must be leased, reused,
  cycled, and shared diligently. Added: the *bounded-shared-pool* /
  *reuse-over-recreate* / *budget-not-exceeded* economics; *cross-machine-
  allocation* + *coordinated-allocation-across-machines* (generalizing today's
  host-local advisory lease — and its *planned* cloud-global display-name beacon —
  to an account-wide shared allocation truth); the *codespace-state-model*
  (in-use / idle / clean / stale + allocation) and *allocation-legibility* so the
  Worktree Picker's **CodeSpaces** pivot can present what exists, what's free vs.
  allocated (to which repo/worktree/agent), and remaining budget across all
  machines — owned here, rendered there (*derive-allocation-not-duplicate*);
  *staleness-recycling* + *recycle-rescues-first* (stale venues cycled out to
  reclaim budget, always rescue-then-reclaim, never touching a live holder); and
  *telemetry-grade-session-capture* + *capture-is-comprehensive* (all CodeSpace
  session data captured — even from an abruptly-recycled venue — for later
  logging/usage-telemetry mining by the memory layer). All additions are
  north-star-ahead of reality (which today has only a per-machine advisory lease,
  startup tolerance, and graceful-teardown session rescue), so the deltas are
  additive build-out; no Non-Goal reality violates was introduced.
