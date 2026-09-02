# Reciprocal Session-Worktree Metadata Design

Back to the [effort README](README.md).

## Design objective

Represent session/worktree relationships in both natural random-access
directions:

- given a worktree, find its bound sessions, asserted head, handoff lineage,
  and controller;
- given an exact Copilot session, find the worktrees it is bound to or
  controls, plus its predecessor and successor relationships.

The representation must preserve one authority, remain cheap at arbitrary
history size, survive session synchronization, and avoid treating an incidental
working directory as an ownership assertion.

## Vocabulary

| Term | Meaning |
|------|---------|
| **Bound session** | A session that explicitly declares the worktree as its own execution home. It participates in that worktree's head and liveness reduction. |
| **Controller session** | A session that deliberately operates another worktree or PR vessel without becoming bound to it. One controller may operate several worktrees. |
| **PR vessel** | A worktree created to carry isolated branch/PR state, possibly without a Copilot process whose startup CWD is inside it. |
| **Session projection** | A bounded agent-worktrees metadata file beneath one exact Copilot session-state directory. It is derived from authoritative records and can be rebuilt. |
| **Terminal successor** | The unique non-concluded end of an explicit predecessor/successor chain. It is never selected by timestamp alone. |

## Authority model

The worktree record remains authoritative for:

- worktree identity and path;
- bound-session registry and activation history;
- monotonic head transitions;
- numbered handoffs and predecessor/successor links;
- controller relationships;
- aggregate worktree status.

The session projection is authoritative for nothing outside its own successful
write receipt. It is a reciprocal index and recovery breadcrumb. A missing,
stale, corrupt, restored, or future-version projection may reduce convenience,
but it cannot change the worktree's lifecycle by itself.

This preserves **derive, don't duplicate**: there is one owned fact and a
versioned materialized projection carrying enough source revision data to prove
whether it is current.

## Proposed session projection

Store one file at:

```text
<session-state>/<session-id>/agent-worktrees.json
```

Illustrative schema:

```json
{
  "version": 1,
  "session_id": "<session-id>",
  "relations": [
    {
      "project": "<stable-project-key>",
      "worktree_id": "<worktree-id>",
      "role": "bound",
      "record_revision": 12,
      "head_revision": 7,
      "lineage": {
        "predecessor": "<session-id>",
        "successor": "<session-id>",
        "handoff_ordinal": 3
      }
    },
    {
      "project": "<stable-project-key>",
      "worktree_id": "<worktree-id>",
      "role": "controller",
      "record_revision": 4,
      "controller_revision": 2,
      "lineage": {
        "predecessor": null,
        "successor": "<session-id>",
        "handoff_ordinal": 1
      }
    }
  ],
  "overflow": false
}
```

The exact schema is a Phase 1 decision, but these constraints are binding:

- `relations` is bounded and de-duplicated by project, worktree, and role.
- Lineage is scoped to one project/worktree relation; a session can participate
  in different chains in different records without flattening them together.
- A session may have one bound relation and multiple controller relations.
- The projection retains at most 128 relations. The bound relation and
  nonterminal controller relations win retention; oldest terminal/finalized
  controller relations are evicted by authoritative revision. If protected
  relations alone exceed the cap, the writer sets `overflow: true`, preserves
  the newest representable subset, and leaves the complete truth in the
  worktree records.
- Raw handoff capabilities, credentials, prompts, transcript text, and
  unrestricted command arguments are never stored.
- Portable identity is separated from machine-local hints. Absolute paths, PIDs,
  pane IDs, and liveness belong to local records or optional short-lived fields,
  not durable synchronized identity.
- Unknown future fields are ignored; an unknown major schema version is
  report-only.
- An older writer never replaces, merges down, or normalizes an unsupported
  newer major schema.
- Every projection entry carries the authoritative revision that produced it.
- Volatile write timestamps are excluded from canonical content. Diagnostics
  may record an external write time, but semantic no-op comparison does not
  change the sidecar merely to refresh a clock.

## Controller relation

Controller identity must not be implemented by registering a foreign session as
the child worktree's bound head. Doing so makes one session appear bound to
multiple execution homes and can cause the Picker to resume the right
conversation in the wrong worktree.

Instead, the worktree record gains a controller slot with:

- controller project/worktree reference when one exists;
- exact controller session ID when known;
- controller relation revision and timestamp;
- source/reason class, such as explicit child creation, explicit controller
  declaration, or validated migration;
- no persisted terminal-successor cache in the authoritative child record;
  consumers derive it by exact read of the controller's succession ledger.

The aggregate reducer remains responsible for presentation. A child with no
bound head but a valid controller can render as **controlled elsewhere**, with
an action that opens or focuses the controller rather than resuming the child as
if it had its own session.

## Write model

One agent-worktrees writer owns projection updates. Lifecycle operations submit
small exact-ID update intents:

- `register-session` / `bind-session`;
- session end or conclusion;
- head transition;
- handoff open and link;
- controller assignment or release;
- worktree finalization where relationships become terminal.

The writer:

1. validates the exact session ID and contains the target beneath the configured
   session-state root;
2. refuses symlink/reparse escapes;
3. verifies local-origin session provenance before writing and treats restored
   or foreign session trees as read-only evidence;
4. refuses an unsupported newer schema rather than overwriting it;
5. acquires an exclusive lock scoped to the exact session sidecar, so
   concurrent bound-worktree and controller-worktree updates cannot lose one
   another's relations;
6. reads and merges only the bounded agent-worktrees projection;
7. skips the write when canonical semantic content is unchanged;
8. stages a private temporary file in a plugin-owned directory outside
   synchronized session trees on the same filesystem, then atomically replaces
   the destination;
9. fails open and records a bounded diagnostic if projection persistence is
   unavailable.

The worktree record is committed first. A projection therefore never advertises
a revision that the authority did not durably accept.

## Reconciliation model

### Immediate path

Successful lifecycle writes update the affected exact session projections.
Normal operation should rarely need repair.

### Resident bounded path

Extend the existing resident reconciler rather than creating a second
always-on owner. Per tick it consumes fixed record and projection budgets:

1. inspect only active records selected by the existing record cursor;
2. compare declared relationships with exact-ID session projections;
3. repair stale projections under nonblocking locks;
4. for dark worktrees with no usable bound head, resolve an explicit controller
   reference and follow its head-transition/handoff ledger;
5. return a unique terminal successor in the derived read result and warm cache,
   keyed by the controller record's head revision, or publish an explicit
   `controller_terminal` finding when a concluded controller has no successor,
   or an ambiguity finding.

The terminal successor is not persisted in the authoritative child record. A
session projection may carry the explicit per-relation successor link and source
revision already present in its lineage, but consumers recompute the terminal
chain when revisions diverge.

The resident path may use the existing fixed-budget session-state cursor for
discovery, but every known relationship is resolved by exact ID.

### Idle-machine backstop

The resident monitor intentionally exits when no consumer remains. Evaluate a
low-duty scheduled one-shot, defaulting to a cadence measured in tens of
minutes, that:

- reads a bounded number of worktree records from a persisted cursor;
- operates only on dark, inactive candidates;
- performs no full session-state sweep;
- exits after its budget;
- remains optional and non-load-bearing for correctness.

Picker launch, list demand, and lifecycle events should still trigger faster
convergence.

## Automatic repair gates

Automatic mutation requires all of the following:

- the target worktree has no live mux, bound process, or active session lock;
- the relation source is explicit and the terminal successor is unique;
- every referenced worktree/session record exists or has a validated restored
  projection;
- there is no cycle, fork, conflicting binding, or newer record revision;
- the nonblocking record lock and compare-before-write revision check succeed;
- the result changes only a projection or controller-derived slot, never an
  explicitly asserted binding.

Failure of any gate produces a machine-readable finding and no mutation.

## Restored and synchronized session-state

Session synchronization carries the session-root `agent-worktrees.json` with the
rest of a session. This enables:

- grouping synchronized sessions by stable worktree identity;
- visualizing multi-session handoff chains without transcript parsing;
- discovering that a restored session previously controlled another worktree;
- giving Copilot a precise pointer to its prior worktree and successor.

Restored metadata is untrusted evidence until reconciled locally. A consumer
must validate schema, session ID, project identity, revisions, and available
worktree records. Machine-local paths and liveness are never restored as current
facts. A projection from another machine cannot silently create a binding or
override a newer local record.

A **local-origin session tree** is one created by the current machine's own
Copilot session lifecycle, even when agent-logger subsequently mirrors it
outward. An **imported/restored tree** arrived from another machine, venue, or
archive and remains read-only evidence until an explicit local session adoption
establishes new provenance.

Restricted venue rescue does not currently preserve arbitrary session-root or
`files/` content. The implementation therefore includes an explicit
agent-containers allowlist change for `agent-worktrees.json`, with size/schema
validation before export and after import. Agent-logger synchronization should
copy the sidecar as ordinary session state, but agent-worktrees must avoid
volatile timestamps and semantic no-op rewrites so a late reconciliation does
not force repeated full-session replacement.

## Recovery context

When a session resumes with a missing or divergent local binding,
agent-worktrees may inject one compact pointer:

```text
This session previously belonged to or controlled <worktree>. Its recorded
successor is <session>. Verify the current worktree record before acting.
```

The pointer is bounded and references exact local resources. Detailed mechanics
stay in the worktree skill or a machine-readable command. Missing projection
data must not block the session.

## Data and visualization surfaces

Machine-readable output should expose:

- bound worktree relation;
- controller relations;
- predecessor/successor and terminal successor;
- authoritative and projection revisions;
- projection health: current, stale, missing, foreign, ambiguous, unsupported;
- recommended action: resume here, continue in controller, repair explicitly,
  or inspect ambiguity.

These fields allow worktree-centric history and session-lineage graphs without
joining on mutable CWD strings or reading transcript bodies.

Corpus-wide visualization operates over the synchronized archive or an index
produced during synchronization, not by enumerating the live session-state root.
The live agent-worktrees paths remain limited to exact-ID reads, explicit
backfill, and the existing fixed-budget resident cursor.

When `overflow: true`, the projection is explicitly incomplete and reports the
number of omitted relations. Reverse lookup then degrades to agent-worktrees'
record-side controller query or a prebuilt synchronized-corpus index; consumers
must not present the retained projection subset as the session's complete
controller set.

## Open design decisions

1. Whether controller relations live directly in each child record or in a
   separate bounded relation journal owned by agent-worktrees.
2. Whether the scheduled backstop belongs to the existing status-monitor
   lifecycle, a platform timer, or the optional Worktree Manager.
3. Whether 128 relations is the right default cap and whether it should be
   configurable under an operator-owned policy.
4. Whether a restored projection can bootstrap a missing record only after an
   explicit operator action, or can merely propose the repair.
5. Whether the first implementation should project only lifecycle identity, or
   also the bounded focus/handoff-memory entries already held by the worktree.
6. Whether a remote controller action should open an existing remote session,
   dispatch a message, or only present a copyable machine/worktree identity when
   interactive focus is unavailable.

## Phase 1 identity decision

The synchronized `project` key is the canonical, credential-free repository
identity already used by agent-worktrees' project registry, not a local checkout
name or absolute path. A restored relation whose canonical project identity
does not resolve locally remains foreign evidence. If one session ID appears
with conflicting canonical project identities or incompatible relation
revisions, the projection is `ambiguous`; automatic repair stops and reports
every candidate without choosing one.
