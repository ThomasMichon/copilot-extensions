# Bounded conversation metadata and event cursors

This is an opt-in source extension, not installed service adoption. It closes
one prerequisite of the [Author/Reviewer plan](../../../efforts/active/durable-pr-watch-delegation/author-reviewer-perspectives.md),
not P1/P2, provider qualification or a complete conversation reader.

`ConversationStore` uses the existing ReplayStore file, operation lock,
whole-envelope atomic replacement, authority checkpoints and uncertain-write
semantics. It creates no database, watcher, service or forge client.
`ConversationJournal` is its pure transaction value object.

## Evidence and bounds

The trusted observation owner supplies `Observation`: a full provider/endpoint/
repository/PR/account/namespace key, exact head/base, original observation
interval, state/completeness, provider actor IDs, accumulated observed reviewer
and own-role relationships, normalized verdict, known review-round/count
statistics and current normalized section digests. Unknown statistics are null,
not zero. A known cumulative review-round count cannot disappear or decrease
within an observation epoch. Digests summarize current commits, comments, threads, checks, reviews
and participants; they are not message bodies or patches.

An observation is at most 4 KiB. The journal admits at most 256 PRs, retains
at most 64 metadata events per live PR, and reads at most 16 events per page.
The existing envelope reserves an explicit byte capacity, defaulting to 8 MiB;
capacity exhaustion refuses instead of dropping another PR or effect receipt.
There is no automatic terminal-record eviction.

The owner supplies the previously read cursor for compare-and-set. An exact
retry reconciles to the existing observation. Overlapping older observations
are stale; changed observations with another cursor conflict. Repeated polls
refresh observation evidence without allocating a meaningful event or promoting
attention. Same-head dialogue can allocate a new event through a changed
normalized section digest. Acquisition, provider ordering, self-feedback echo
normalization and feedback baselines remain with their current owners.

Reads report the current summary, high/low water, a bounded event page, next
position and continuation. The logical event window is 24 hours; old events
are inaccessible, and the next admitted observation removes aged metadata
from the persisted suffix. An unchanged poll also expires that suffix,
retaining only the most recent event as its durable cursor anchor when all
events have aged out. That expired anchor remains inaccessible to event reads.
Neither expiry nor evidence refresh allocates a new event or advances a cursor;
stale observations and cursor conflicts leave the journal unchanged.
A count/age gap, future cursor or different epoch
returns `resync_required` with no replacement `next` cursor. `after: null`
explicitly requests a current snapshot. Neither reading a position nor
receiving a snapshot acknowledges delivery, completes a review or advances
another consumer. Durable watch replay/ACK remains unchanged.

Only complete authoritative terminal evidence creates a journal tombstone.
It drops section digests and prior journal events, retains one terminal event
and compact identity/relationship/outcome/statistic metadata, and does not infer
abandonment from disappearance. An explicit complete reopen creates a new
epoch and retains one compact previous terminal summary. Old cursors require
resynchronization.

**The baseline journal is not global terminal compaction.** Existing body-bearing review
generations, materializations, submission intents, unknown receipts and pending
watch delivery are untouched. Coordinated cache/outbox/payload retention and
terminal notification transactions require the explicit
[terminal-intent embedding](terminal-retention.md); physical compaction remains
open work. No secure-erasure,
zero-fill or TRIM operation is introduced.

## Explicit storage and serving adoption

`ConversationStore.adopt(...)` upgrades an already adopted owner-v2 envelope
to owner-v3, preserving replay and review operations. Its embedding supplies the
existing lifecycle checkpoint that excludes incompatible file readers before
replacement. It is not a fabricated permission token. Adoption is explicit,
capacity-bound and atomic; it is never performed by an HTTP/CLI read.
Uncertain replacement is reconciled by inspecting/retrying that same adoption.

New code continues reading/writing existing replay/service envelopes and owner
v2. Older strict owner-v2 file readers refuse v3 rather than losing its journal.
The separately reviewed [attention data expansion](attention.md#explicit-data-owner-expansion)
adds explicit v4 adoption without changing ordinary v3 serving. Internal
conversation transactions preserve that expanded envelope, but the current
v3 serving descriptor refuses v4 until its compatible reader is implemented;
it never advertises adopted v4 bytes as v3. No default host adopts that layout.
Consequently, a runtime rollback after explicit v3 adoption needs a capable
reader or an authorized, separately reviewed downgrade; replacing the executable
does not authorize rewriting the state. No automatic downgrade is supplied.
Existing independently deployed HTTP clients do not open this file.

An existing ADO workspace embedding can explicitly construct
`WorkspaceOperations(..., conversations=True)` after adoption. It then
advertises the optional `agent-pull-requests.conversation/v1` extension,
with owner-store version 3, and enables read-only `events` on the existing
authenticated `/v2/review/<target>/events` route. The workspace-v1 baseline
capability fields remain unchanged; the extension separately describes its
required persisted layout. Discovery verifies the executing owner's adopted
state, not merely a newer package version. Default embeddings remain disabled.

The [CLI/tool facade](workspace-tools.md) exposes the same optional operation:

```text
agent-pull-requests workspace events --request request.json
```

```json
{"number":42,"after":{"epoch":1,"sequence":3},"limit":16}
```

Use `after: null` for the first snapshot. An actual existing host must supply
the bound client; an ordinary unbound invocation remains unavailable. Requests
cannot override provider, project/repository, account, namespace or storage root.
The current ADO workspace adapter derives those from its existing authorized
scope; the generic journal's provider tag does not qualify GitHub/Gitea adapters.
There is no caller-supplied HTTP ingest operation.

For explicitly enabled embeddings, successful existing-owner `load` and `sync`
now project their acquired normalized ADO generation into the journal.
Capabilities separately advertise `conversation_acquisition:
workspace-projection/v1`; an older conversation reader need not support that
optional acquisition feature to keep reading events. Disabled embeddings retain
their prior behavior. The result adds `conversation` with its observation cursor
or an explicit unavailable/stale/not-observed outcome. Cache CAS conflicts,
capacity refusal and uncertain publication do not feed a generation into the
journal. There is still no additional acquisition call, watcher or HTTP ingest.

Cache publication and the journal transaction are distinct commits. Journal
failure cannot roll back a confirmed cache publication or imply the load had no
local effects. Read the cache's current generation before retrying with its
current CAS value. The journal's own uncertain atomic replacement retains the
existing same-observation reconciliation; do not reset its cursor or replay a
forge operation.

The projection hashes semantic section data, completeness and errors, never
capture timestamps or the timestamp-dependent generation ID. Comment edits,
deletions, thread status, policy/reviewer changes and target-only movement remain
visible without a source push. `commits` summarizes PR/revision, iteration and
changed-path evidence; it does not pretend the workspace enumerates all commits.
Current comment/thread/policy and participant counts are not lifetime totals.
Commit totals, cumulative review rounds and candidate-bound verdicts remain
unknown unless independently known: an iteration is not a review round, and a
current ADO reviewer vote does not identify a reviewed candidate. Previously
known cumulative rounds and observed relationships cannot disappear.

Native fractional observation intervals are conservatively widened to the
journal's integer-second contract (floor earliest start, ceil latest end).
Overlapping widened intervals can explicitly return `stale_observation` even
when the native captures were ordered within one second; a later nonoverlapping
refresh can reconcile current state. No inward truncation invents ordering.
Missing exact head/target, status or supplied-section intervals is unavailable.
Partial evidence cannot compact a terminal PR or reopen a tombstone; a fresh
complete active observation explicitly starts the new epoch.

New callers against prior servers do not emit `events` without discovery.
An unknown optional conversation contract withdraws only `events`, leaving
the older workspace operations usable. Older clients can continue ignoring the
additive optional operation/descriptor on a capable server. Legacy native CLI
verbs, review submission, replay registration and watch ACK retain their meaning.

## Qualification and remaining adoption

The focused container packet exercises synthetic observations, real atomic
storage/restart/uncertain replacement, count/age/epoch gaps, terminal/reopen
metadata, replay continuity and retained unknown effects. A contained flow also
executes the real CLI parser/run facade, WorkspaceClient, ServiceCaller, TLS,
proof-of-possession Gateway/channel and journal reader, with synthetic issuer
identities and an explicitly supplied test host. It checks read-only storage,
same-head dialogue, restart and policy withdrawal with zero forge writes.

This is not executable bootstrap, installed host registration, autonomous
acquisition, shared selection intake, a full body reader, terminal outbox
compaction, three live providers or live shadow evidence. Production deployment,
retained-service activation and independent source/effect ownership stay gated.

The existing native snapshot bridge now preserves an optional `base_sha`
alongside `head_sha` in its detached native metadata. A prior snapshot without
the field is still accepted; an explicitly malformed nonempty value refuses.
This closes one metadata prerequisite for future native acquisition, not that
adapter itself: the standalone observer still does not feed these snapshots
into the conversation journal. Native target evidence is not a review verdict,
credential or permission to compact another owner's state.
