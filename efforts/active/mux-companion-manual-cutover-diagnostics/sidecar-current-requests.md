# Proposed live input and permission observations

Parent: [Step 7, S3](sidecar-live-timeline.md#s3---bridge-relationships-and-problem-evidence).
Shared boundary: [Sidecar observation contract](sidecar-observation-contract.md).
Canonical effort: [README.md](README.md).

**Status: reviewed amendment in #10545; local producer merged in #10551;
optional provider expansion in progress; no installed support claimed.** This is the
coordinator's implementation proposal within the accepted input-observability
scope, not a new operator request or rollout grant. It targets the existing
[compaction-and-input-observability](../../../visions/mux-companion/README.md#compaction-and-input-observability)
vision delta for qualified live requests only. Definitive pre-compaction
measurement and every original parent gate remain independent.

## Separate live state from retained occurrences

The existing semantic v1 feed deliberately reports `current_requests: unknown`
and per-entry `answerability: unknown`. A retained request can outlive its actual
waiter; retention can also omit a still-pending request. Replaying a bounded tail,
matching a newest event or copying the existing attention summary cannot produce
a complete live inventory.

Keep those v1 fields, immutable entries, hashes, positions and history semantics
unchanged. Add an explicitly negotiated current-state projection to the existing
selected-session observation, not another event store or answering surface.
Observation reports a parked waiter, not whether the entire actor is blocked or
whether any particular caller may answer it.

## Provider-first compatibility

Apply [service contract evolution](../../../docs/patterns/service-contract-evolution.md).
Propose optional `bridge-session-current-requests-v1`, version 1:

- The running daemon declares support through its existing `/health`
  capabilities. Its existing observation route accepts opt-in
  `current_requests=true|false`; absence preserves the existing response.
  The history route rejects this live-state option.
- The selected owning CLI accepts `observe-session --current-requests` only
  after its parser implements it, and negotiates the running provider before
  sending the query. An explicit request against an unsupported daemon reports
  the optional contract gap; it does not pretend to have observed an empty set.
- Manager uses the flag only when both the selected executable's passive
  capability declaration and the running peer support it. Use the existing
  discovery seams once per selected generation, not a new preflight process per
  frame. Missing/malformed support never guesses new syntax.
- Without support, Manager retains ordinary v1 observation/history and presents
  current input as unknown. Do not retry an unrecognized new invocation as if
  its effects or outcome were known. Old callers to new providers retain their
  exact baseline; provider rollback returns to that baseline with the optional
  feature explicitly unknown.

The provider/parser expansion precedes conditional Manager migration. No old
form is retired by this amendment; any future removal needs a separately
declared upgrade and provider-rollback window. Package versions do not select
this contract. No installer, authentication or service-start behavior changes.

## Producer-owned, bounded snapshot

First qualify only an already-owned, connected local ACP client's actual
manual permission and form-elicitation waiters. The producer owns the snapshot
and reads it synchronously on the same event loop as callback mutations, without
awaiting, reconstructing retained history or copying messages and schemas.
Qualification requires the exact admitted session, its currently attached client,
live connection and supported local transport. A host-mode relay, remote client,
represented registration, missing/replaced client, disconnected transport or
terminal session remains unknown until its own producer is qualified.
The source helper binds successful connection initialization to the actual SDK
callback object and owned process, then separately admits the ACP session.
Replacing that connection creates a new observation incarnation. Pending or late
callbacks from the former producer, or waiters from a prior session-admission
epoch, cannot become current merely because the same client object reconnects.
The existing permission answer slot can be overwritten or reset while an earlier
callback still awaits. If weak producer admissions exceed the bounded candidates
actually found in the owner maps, coverage is partial (`waiter-coverage`), never
complete absence. The helper does not create another answer inventory or settle
those callbacks; their actual completion removes that coverage limitation.

Each waiter occurrence has a producer-owned opaque identity scoped to that
client incarnation. A repeated tool-call ID must produce a different occurrence;
do not use Python object addresses or an observer-generated UUID as source proof.
Occurrence IDs are view-only, distinct from tool-call IDs, permission request
IDs and remote decision tokens. Do not copy those answering selectors into
this new block or accept its occurrence IDs through an answer API.
Forwarded and automatically resolved permission requests are not local manual
waiters. Done, cancelled, withdrawn and superseded futures are not pending.

The opt-in snapshot/delta adds `current_request_state`, a version-1 object with:

| Field | Meaning |
| --- | --- |
| `availability`, `completeness`, `reason` | Explicit observed/unknown qualification and complete/partial/unknown coverage; fixed reason codes, never prose inference |
| `client_incarnation`, `revision` | Producer-owned process-live identity and monotonic current-state revision; distinct from event positions and durable history |
| `observed_at` | Observation time, not an event time or continuity guarantee |
| `requests` | Bounded pending occurrences: view-only occurrence identity and input/permission kind |

Unknown qualification has no positive pending assertions. Only a complete,
qualified empty set means no supported local waiter at that observation.
Partial coverage can establish its listed pending occurrences, never absence,
total pending count or that an omitted occurrence was cleared.
No question, schema, offered options, response, decision token, executable
command or credential crosses this projection.

Bound both work and output: inspect at most 64 candidate waiter slots and emit
at most 32 pending occurrences in at most 8 KiB. Exhaustion is explicit partial
coverage, not a scan of all retained/live entries to obtain a reassuring count.
Reserve that byte budget before selecting the event tail so the unchanged
32 KiB body/35 KiB frame limits still hold. Identifier validation/overflow cannot
truncate a source identity into a different one; malformed producer data reports
a typed source error rather than complete emptiness.

## Delivery, invalidation and ownership

Use the existing selected-session held reader and EventLog wake channel.
Producer changes wake observers without appending fabricated history events.
Admission/clearing, withdrawal, re-ask, future cancellation/completion, client
replacement and connection/terminal changes must update or invalidate the live
projection. Capture-before-wait races cannot lose a changed revision; retain
the existing bounded lifecycle backstop for changes without an immediate wake.
There is no fourth permanent worker, per-panel poller or new endpoint.

Deliver changed current state even when no retained event was appended.
Such a snapshot or empty delta preserves the actual event position/base;
its current-state revision never advances the history cursor. Heartbeats and
observation-time-only changes do not create milestones or repeated state updates.
An empty log may carry a qualified current snapshot without inventing a journal
incarnation or event position.

Manager holds current input independently of the evictable event window and
history paging. Reconnection, selection/head/request-epoch change, client/source
replacement, error, gap or going-away invalidates the corresponding live
qualification until a fresh admitted snapshot. Loaded old occurrences remain
historical, not answerable. Unchanged event positions must not suppress a newer
current-state revision; late replies from an old selection cannot restore it.
Do not compare revisions across client incarnations.

The UI shows qualified pending input/permission and explicit partial/unknown
coverage without stealing focus. An explicit return to an already-admitted
interaction surface remains the answer boundary; this amendment does not
submit answers, copy resolution tokens, grant permission, switch sessions,
repair head, cut over, stop an actor or release resources.

## Implementation and validation gate

Before calling this slice delivered, exercise the actual owner callbacks and
futures without starting a model/SDK actor, then the private HTTP/owning CLI and
Manager consumer seams:

- Request, response, decline/cancel, withdrawal, same-tool re-ask, done-future
  filtering and forwarded/automatic permission exclusion.
- Empty and absent history, retained request eviction, overflow of scan/count/
  bytes, malformed identifiers and qualification loss. No full-history scan.
- Stop/disconnect/reconnect/client replacement and callback/wait registration
  races; no stale pending state after current qualification is lost.
- State-only changes at an unchanged event position, independent backward
  paging, selected-head/request-epoch replacement and complete owned-reader
  cancellation. Existing subscriber/idle effects stay documented and unchanged.
- Real strict parser and transport coverage for old/new caller/provider,
  supported rollback, missing/malformed declarations and unknown flags.
- Real Textual display of pending, cleared, partial and unknown states without
  answers or focus transfer. Retain dated synthetic progress captures and
  curated visual comparisons under the existing visual-evidence request.

Use bounded isolated tests and the existing test portfolio; no native/effectful
matrix enters required unit-only CI. Source qualification is not installed
or native-client acceptance. #10545 merged as
`d4340930de9179f8e83a9726a3a873689f7b2114`, satisfying review before new wire
implementation. Record the actual closed source items under S3 as they land.

The bounded producer helper and actual callback/Future controls are the first
implementation slice. No optional HTTP block, CLI flag, capability advertisement
or Manager display is implemented by that helper alone; existing external reads
still report current requests unknown.

The follow-on provider source adds the optional block, strict opt-in parser,
selected-CLI declaration through existing `version --contracts-json`, actual
provider health negotiation and state-only delivery on the same held reader.
Its focused producer/HTTP/CLI packet passes 216 cases; the expanded current
HTTP-52 probe/guard packet passes 69. Source-bound declaration preparation,
published capture, independent review and required CI remain pending; Manager
consumption and rendered attention validation have not started.
