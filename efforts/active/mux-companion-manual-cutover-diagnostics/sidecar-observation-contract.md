# Proposed Sidecar observation and history contract

Parent: [live Sidecar implementation plan](sidecar-live-timeline.md).
Canonical effort: [README.md](README.md), Step 7.

This is the reviewed-design target, not a description of implemented commands.
Providers expand and advertise actual support before independently installed
callers use new syntax. Existing roster, agenda-v1/v2 and raw Bridge event
contracts remain unchanged.

## Ownership and passive discovery

Worktree, agenda and Bridge owners supply their own source records. Manager's
normalized model is an ephemeral read projection, not a writer API or ledger.
Manager does not import independently installed sibling runtimes, copy their
credentials, accept publisher-provided executable arguments, or start a service
to inspect an optional capability.

Capability declarations and the selected executable's parser/read protocol must
agree. Package version alone is insufficient. Unsupported is explicit; malformed
qualified data is a source error, not an unsupported-looking success fallback.
Reuse the existing released discovery/handshake and cache its result for the
selected generation. This adds neither a universal prerequisite RPC nor a
preflight subprocess for every history page or update.

## New source surfaces

Names below are proposed v1 additions, not existing invocation examples:

| Owner | Capability | Read boundary |
| --- | --- | --- |
| agent-worktrees | `worktree-observations-feed-v1` | `worktree-observations`: one-shot selected snapshot; `--follow` and an opaque after cursor select held updates |
| agent-worktrees | `worktree-observations-history-v1` | `worktree-observation-history`: bounded page before an opaque source cursor |
| Bridge | `bridge-owner-membership-v1` | `observe-owner`: authenticated owner-scoped snapshot and optional follow/position |
| Bridge | `bridge-session-observations-v1` | `observe-session`: bounded semantic projection over an admitted known session stream |
| Bridge | `bridge-session-observation-history-v1` | `session-observation-history`: bounded supported retained history, not a raw-SSE query masquerading as a page |
| Bridge | `bridge-compaction-evidence-v1` | Actual start/target evidence with per-record qualification; definitive pre-state only where the source fulfills its relationship |
| efforts | `agenda-history-page-v1` | Rich `history-page` read through the owning payload command and an explicit allocator/registration locator; no new global binstub |

CLI handlers require the actual project/worktree or qualified owner reference,
selected session where applicable, contract version and JSON output. Follow and
history are distinct operations; an older daemon must not be allowed to interpret
a history request as an indefinitely held event stream.

The exact owning parser/HTTP/control schemas are implemented and tested under
these capability names. Any breaking change to this design receives an explicit
amendment, not an undocumented argument or snapshot-field overload.

## Source identity and positions

Each source has:

```text
provider
project / qualified owner reference
full worktree identity
selected canonical head identity or explicit unknown
source generation / journal incarnation
resident or hosting generation, when applicable
request epoch in the consumer
```

Native pane incarnation, durable Copilot session, Bridge handle, caller audience
and context epoch are different identities. A display name, latest-session
lookup, mtime or caller affinity cannot establish head or ownership.

Positions are opaque, source-qualified tokens. Membership sequences, roster
revisions, agenda revisions and session event IDs cannot be substituted for one
another. A replaced generation or retained-floor violation causes explicit
reconciliation. Paging/viewing does not consume the controller's cursor.

## Frames and history truth

Owner frames retain exact source identity, version and position:

| Frame | Meaning |
| --- | --- |
| `snapshot` | Atomic selected facts and replay barrier, availability and retained-range qualification |
| `delta` | Advancing cursor, required base cursor and bounded typed changes |
| `history-page` | Ordered identified entries, before boundary, next-before and completeness |
| `gap` | Retention/source-replacement/journal/cursor failure and an explicit reconciliation path |
| `heartbeat` | Connection evidence only; no history milestone or cursor advancement |
| `going-away` | Source lifecycle notification with its owner qualification |
| `error` | Typed source failure; preserves independent usable views |

The owner index may materialize existing records and mutation evidence. It is not
a replacement claim/effort store. YAML save and index append are not silently
advertised as one crash-atomic transaction.

Detach validated caller-owned capture data before waiting for a writer lock;
validation, content hashes, fences and publication must use that same capture.
An acknowledgement containing only already-accepted events cannot clear a
failed newer capture's invalidation. Recovery requires actual qualified
materialization; retained history gaps remain explicit.

An operation failing before its source/cursor/replacement checks establish
admission reports an error without invalidating existing facts. Failure-driven
invalidation requires fresh agreement with the admitted journal boundary or
the materialized target, not a project/worktree key or a lock-timeout exception.
If that agreement cannot be established, report the unavailable operation
without guessing that the current projection belongs to the failed mutation.
Pending invalidation belongs to the verified journal generation and source,
not every retained journal for the same project/worktree. A replacement's
failure cannot change its predecessor's healthy retained history; an existing
predecessor gap remains its own coverage qualification across replacement
failure and recovery.
Size-only failures retain detached validated evidence through admission.
Accepted batch/native identity conflicts are rejected before overflow can
invalidate current facts; oversized content cannot impersonate previously
accepted bounded evidence. Actual qualified new-batch delivery failure still
reports its source-local gap.

Native event identity is source, generation and record identity. A payload
locator participates in content integrity, not identity: changed content or
locator under one native identity conflicts rather than appending a new event.
Snapshot observation time and current qualification can refresh without an
event-cursor advance; deliver that snapshot without inventing a history event.

Capture completeness and retained-history coverage remain distinct: preserve
the former in the journal and qualify the latter in read frames. A successfully
materialized replacement journal can recover without clearing the old journal's
gap. Bounded change evidence explicitly marks omitted fact detail with capture
references, content hashes and sizes; current facts remain independently present
in the qualified snapshot.

Every history entry distinguishes:

- `persisted-mutation`: captured at an admitted owner mutation seam after
  canonical persistence, with its record fence;
- `retained-event`: identified existing journal evidence at its actual retained
  range and incarnation;
- `observed-change`: difference between complete owner observations, not a
  reconstruction of intervening events;
- `reported-lifecycle`: actor-reported session/tool contribution, not proof of
  current execution or canonical goal acceptance.

A claim-count change is not a claim-added event. Registration absence is not
relationship retirement or claim release. Source timestamps are labels and
evidence, not a fabricated total causal order across hosts.

## Manager's normalized event model

The internal model keeps these typed fields:

| Field group | Contract |
| --- | --- |
| Event/source/actor IDs, cursors, owner references, payload references | Exact opaque or qualified identifiers, not prose or Markdown links |
| `kind`, evidence kind, actor kind/qualification, availability/completeness | Closed typed vocabularies; unknown native event names remain raw identifiers, not inferred actions |
| `occurred_at`, `observed_at`, measurement times | Aware timestamps or explicitly permitted unknown; clocks retain source qualification |
| `summary`, `details`, `reason`, reported phase | Literal prose with bounds; phase is displayed only when reported |
| Relation references | Explicit parent, return, resource or succession evidence with qualification |
| Domain payload | Bounded finite JSON only where the owner schema declares it; not a command or style object |

The initial normalized vocabularies are explicit and case-sensitive:

| Selector | Values |
| --- | --- |
| Provider | `worktrees`, `agenda`, `bridge` |
| Availability | `observed`, `unsupported`, `unavailable`, `head-changed`, `unknown` |
| Completeness | `complete`, `partial`, `unknown` |
| Durability qualification | `committed`, `pending`, `unknown` |
| Actor kind | `worktree`, `session`, `subagent`, `bridge-session`, `resource`, `unknown` |
| Actor/relation qualification | `authoritative`, `reported`, `unknown` |
| Relation kind | `parent`, `return`, `resource`, `succession`, `unlinked` |
| Timeline kind | `worktree-created`, `purpose-changed`, `effort-bound`, `effort-released`, `claim-added`, `claim-released`, `claim-settled`, `agenda-transition`, `contribution-reported`, `bridge-member-changed`, `input-request`, `permission-request`, `compaction`, `context-pressure`, `source-gap`, `observed-change`, `source-event` |

These are Manager projection selectors, not replacements for each owner's wire
vocabulary. Native event/state names and error codes remain exact owner-defined
identifiers, not arbitrary narrative selectors. Unknown native event kinds can
be retained as `source-event`; they cannot be inferred from a prose substring.
Reported phase and summary/details/reasons are free literal prose. Timestamps,
numeric thresholds/counters and raw JSON are not prose or enum tokens.

Source prose is never evaluated as Markdown/Rich markup or executed. Terminal
control bytes are escaped for presentation without rewriting canonical values.
Heartbeats and model-only metadata updates do not become productive agenda
milestones.

Loaded pages are evictable; source high-water and continuity evidence are not.
Duplicate identities with conflicting content are errors. History can load older
records without allowing an old live delta to replace the current state.

## Bridge membership

Membership snapshots/deltas include the admitted owner, hosting generation,
position, observation time and completeness. Members preserve:

```text
member identity
qualified owner / relationship reference
hosting Bridge and Bridge-session handle, when known
durable session and worktree reference, when known
identity quality / registration state / admission phase
liveness and freshness qualification
session-event position and continuity, when available
```

Upsert/tombstone semantics distinguish registry disappearance, session retirement
and confirmed relationship retirement. No tombstone releases an owner claim.
Carrier loss changes freshness/coverage, not resource custody.

Known-session observers reuse current local, represented and remote transports.
Represented sessions remain read-only and retain their weaker tail/history
guarantees. Observer audiences are independently admitted under the existing
owner/authentication scope; a new caller label is not authorization.

## Pre-compaction evidence and viewer policy

Current usage translation alone is insufficient. A qualified producer record
must preserve:

```text
session identity: hosting source, durable session, context epoch
compaction reference: event identity, source position, occurrence
pre-usage reference: event/measurement identity, source position, context epoch
used tokens and capacity tokens, with valid numerical types
capacity basis: active-pre-window | target-window | unknown
target capacity and compaction attempt reference, when reported
source model / switch target / target prompt budget / trigger metadata
measurement kind: sdk-direct | estimated | unknown
relationship: same-event | producer-linked-pre-event | uncorrelated
freshness: current | stale | unknown
measurement time and qualification
```

Valid counts are nonnegative integer usage and positive active-pre-window capacity;
booleans, missing values and estimates do not qualify. The producer cannot create
a semantic pre-event guarantee from a timestamp, cursor adjacency or new UUID.
Model-only updates must not refresh an old token measurement.

The documented SDK start-event `currentTokens` can supply total same-event usage
when actually present. Its `tokenLimit` identifies the targeted window, so
`target-window` alone is not a qualified pre-window denominator. Preserve the
direct fields and attempt/model/switch/target metadata separately. The selected
source/runtime must establish active capacity; absence of an optional switch
field is not proof of unchanged capacity.

The source-schema reference is
[`CompactionStartData`](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts#L4931-L4977).
The inspected immutable event-type blob is
[`571a08c8d799c938d77dadd9202a1505e3f3e2f9`](https://api.github.com/repos/github/copilot-sdk/git/blobs/571a08c8d799c938d77dadd9202a1505e3f3e2f9).
Those declarations justify preserving optional start fields; they are not
selected-runtime qualification or an active-capacity guarantee. Capability support means the evidence format is
implemented; it is not a blanket qualification of every source measurement.
An adapter may honestly return unqualified/unknown start evidence while S3's
definitive-warning acceptance remains open.

The viewer policy has an explicitly configured finite percentage threshold and
policy reference. Its result vocabulary is:

- `premature`;
- `not-premature-by-threshold`;
- `unknown`, with a reason and retained observation/measurement references.

At configured 65, compare the unrounded ratio using integer arithmetic.
Unknown/stale/wrong-session/post-only/uncorrelated evidence never becomes zero
or normal. Actual compaction stays distinct from context rollover and handoff;
any exception requires explicit selected policy rather than an inferred manual
exemption.

Classification is local observer interpretation. It does not rewrite the
source event, change runtime thresholds, answer input, force cutover or repair
head state. An adapter without a documented measurement path exposes diagnostic
observations, not this capability's definitive qualification.

## Lifetime, limits and compatibility

Use the implementation plan's frame/page/window/cache/worker limits. Clip
display detail only with explicit omission and an owner payload reference;
do not lose source continuity or fake complete membership.

History/detail reads and source workers share caller-owned cancellation,
stdout/frame bounds and late-epoch rejection. Observer exit closes only its
subscriptions/processes. Existing Bridge watch/idle effects remain visible;
this interface does not silently redefine session lifetime.
Manager's internal adapters receive a request-local `stop` event. They must stop
new reads cooperatively and await bounded cleanup of their own handles.
Controller teardown signals that event and waits for the adapter task; it does
not cancel a task that may already be finalizing a read. Caller cancellation
cannot discard those handles, and cleanup failures remain explicit.

Old/unsupported peers retain current Summary/Claims/Agenda/Recovery behavior.
Optional unavailable sources and errors do not disable independent views.
No new endpoint, parser, stored schema or native capability is called deployed
merely because its plan or source PR merged.
