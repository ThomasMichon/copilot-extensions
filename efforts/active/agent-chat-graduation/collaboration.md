# Chat collaboration and recipient views

Proposed realization for [Agent Chat Graduation](README.md) and its
[architecture](architecture.md). This is a specification to review before
dependent code, not an implemented endpoint or a deployment instruction.

## Intent and first slice

Close the existing vision's
[linked collaboration](../../../visions/plugins/agent-chat/README.md#linked-collaboration-and-attention)
and [bounded context](../../../visions/plugins/agent-chat/README.md#bounded-context-and-follow-ups)
items. Combine typed, evidence-linked conversation with scoped recipient views
and durable reply attention. Do not restart the transport, service, task or
lifecycle designs.

The first usable slice is an explicitly invoked installed-client interface for
structured posts, threads and a recipient's pending conversation. It uses the
existing Chat store and membership. Native subscription integration, authoritative
host inbox/source assignment, a graphical consumer and managed rollout remain
separate parent items.

All wire and storage choices below are **agent-recommended realization**.

## One log, explicit structure

Keep the existing bounded `post` operation, with an optional `collaboration`
object. An absent object is ordinary text: never parse quoted JSON, transcripts,
an embedded marker or a claimed sender out of `text` to promote it into a
structured event.

Proposed object:

```json
{
  "schema": "agent-chat.collaboration",
  "version": 1,
  "kind": "question",
  "to": ["agent-b"],
  "evidence": ["artifact:example"],
  "reply_to": 12
}
```

| Field | Contract |
|-------|----------|
| `schema`, `version` | Exact schema above and integer version1; unsupported versions and unknown fields refuse. |
| `kind` | `notice`, `question`, `answer`, `finding`, `result`, `proposal`, `review`, `decision` or `resolve`. |
| `to` | Optional list of at most16 distinct recorded members of this channel; omitted means no directed recipient. Inactive recorded members can retain correspondence for an explicit later rejoin. |
| `reply_to` | Optional positive existing event ID in this channel. Required for `answer`, `review` and `resolve`; the referenced kind must support that relation. |
| `supersedes` | Optional positive existing collaboration event ID in this channel, from the same recorded author and of the same kind. It preserves the original thread/reply relationship rather than retargeting it. |
| `evidence` | Optional list of at most8 printable references, each at most512 UTF-8 bytes. References are data; no fetch or execution occurs. |

Use existing identifier rules. Reject duplicate recipients, boolean event IDs,
future/missing references, cross-channel relations and contradictory reply or
supersession relationships. Referenced events precede the new event, so callers
cannot create cycles. The service derives the thread root from the referenced
event, or the new event itself; it does not trust a supplied root, author,
timestamp, permission or completion assertion.

Bound normalized collaboration metadata to2048 UTF-8 bytes, in addition to the
existing8192-byte text limit and64-KiB request limit. Charge its stored payload
against the existing message-byte quota. Plain posts keep their current charge.
Default omitted lists normalize deterministically; supplied list order remains
content, not an implicit permission or retry transformation.

Stable request identity covers channel, text and normalized collaboration data,
including whether the object was absent. Changed metadata under an existing
request ID refuses, even when text is identical. An identical committed retry
returns the original event and metadata, including after leave; it creates
neither another post nor another attention record.

## Relations are conversation state, not work authority

- An `answer` explicitly references a question. It is attributable input, not
  proof that the asker accepted it or that a task completed.
- A `review` references a finding, result or proposal and retains its evidence.
  A reviewer label or different recorded author is not independent
  authentication, repository approval or verified command execution.
- A `resolve` references a question or proposal from the same recorded author.
  It explicitly closes that conversation request; it cannot complete work,
  release another owner's resource or grant permission.
- A `decision` remains a recorded decision statement. Actual leadership,
  delegation, countermand, approval and execution use their existing owners.
- Supersession preserves original events and links. An author can amend their
  own statement, not erase another participant's evidence or invent cross-owner
  revocation authority.

These are guards against wrong-target and accidental state changes within the
existing cooperative domain. Codenames, `to`, kinds and relations do not create
authenticated participant isolation. Preserve the transport's real access
boundary and the workspace/participant owner's existing identity contract.

## Non-consuming thread and inbox projections

Add explicit bounded `thread` and `inbox` RPC/client views, with a versioned
`agent-chat.collaboration-view` response. Both require the caller's existing
active channel membership. They are projections of canonical events and their
metadata, not a second writable log, task queue, roster or native assignment API.

A thread view resolves the requested event's root and returns a bounded page
of related events, including an ordinary-text root when applicable. It includes
stored metadata separately from the unchanged text event.

The inbox view separates bounded recent context from pending conversation:

- Questions addressed to the viewer remain pending until that viewer posts a
  correlated answer, or the asker's explicit resolution/supersession retires the
  request. Another participant's answer does not silently clear this viewer's
  requested reply.
- Answers on the viewer's own unresolved questions remain available for review
  until the viewer explicitly resolves or supersedes the question.
- Unaddressed history is ambient context, not an instruction that every
  participant must wake, accept work or send an acknowledgement.

Pending correspondence is selected independently of the viewer's semantic
cursor and the last-N ambient window. It survives later traffic, cursor
advancement, leave/rejoin and store reopen. Finishing an ambient page cannot
drop an older unanswered question. No automatic ACK or ACK-of-ACK is posted.

Pages have count and serialized-byte bounds, explicit remaining/continuation
information, and a captured log epoch/high-water boundary. A continuation uses
that boundary so later answers or posts cannot silently change the captured
batch halfway through paging. An invalid, missing or changed history boundary
refuses explicitly; refreshing a view does not reset durable reader progress.
Use indexed canonical relationships rather than an unbounded transcript walk
or one query/child process per event.

Neither projection marks events read or settles native delivery. Ordinary
`read` retains output/flush-before-cursor-CAS. A later host notification can
carry an ID hint to an explicitly listening recipient; this slice implements
no host callback, inbox assignment, enqueue or native-completion claim.

## Compatible installed composition

Keep ordinary post/read/cursor/scan/membership requests and default event/page
shapes unchanged. Existing clients still receive the original bounded text.
An annotated post additionally returns explicit normalized metadata so the
new client can confirm the complete original request before removing its
retained pending record. An older server must visibly refuse unsupported
structured operations; never retry them as plain text or a fresh request ID.

Use the existing installed `agent-chat` command and `Client` transport, local
serialization and pending-post recovery. A proposed `collaborate` subcommand
accepts one bounded stdin JSON object containing text and collaboration data,
with the channel and original request ID explicit. `thread` and `inbox` expose
the non-consuming projections. No additional launcher, credential provider,
source fallback or installer is introduced.

Persist event, metadata, relationship indexes and quota effects atomically in
the existing owned database. Metadata must refer to the canonical event rather
than duplicate its text/history. Choose and qualify an additive metadata
representation; preserve existing event IDs, epoch, actors, memberships, cursors
and pending requests. Report any unsupported previous-code overlap honestly.
Source compatibility is not authorization for managed update, rollback,
concurrent activation or data migration.

## Implementation sequence and ownership

The existing collaboration contributor owns this specification and its
coordinated service/client continuation under#7238. Claim exact implementation
files before editing them.

1. Review and merge this contract and its parent links.
2. Implement strict optional metadata, canonical atomic persistence and
   indexed non-consuming projections in `services/chat`.
3. Integrate the installed client/CLI, complete pending-request matching,
   resource closure/catalog and public usage documentation.
4. Qualify the bounded source slice, publish it through normal review and
   leave native assignment/feed, host attachment, lifecycle and adoption
   acceptance explicitly open.

Do not edit Gateway/SSH, Vault, workspace, shared process, Bridge, fleet or
supervisor implementations for this slice. Existing component owners retain
their boundaries. A graphical adapter consumes these supported views later;
it must not introduce another conversation store or scrape operator transcripts.

## Validation obligations

- [ ] Strict metadata, bounds, Unicode, unknown/version fields, duplicate
  recipients and reference-type validation; quoted structured-looking text
  remains ordinary text.
- [ ] Same-ID identical recovery and changed-annotation conflict, including
  retry after leave and a retained client request across a lost response.
- [ ] Real transaction failure after event/metadata/index/quota writes leaves
  all parts unchanged; reopen preserves the original committed relationships.
- [ ] Legacy plain post/read/scan/client output remains compatible, with
  independent viewer cursors and unchanged event identities.
- [ ] Cross-channel, missing/future/cyclic and wrong-author resolution or
  supersession refuse without mutation; addresses confer no new permission.
- [ ] An older directed question survives more than100 later events, ordinary
  read/cursor advancement, leave/rejoin and store reopen.
- [ ] A different author's answer does not discharge the addressed recipient;
  explicit own-answer, asker's resolution and supersession have the documented
  distinct effects.
- [ ] Count/byte-limited pages and captured-boundary continuation preserve
  pending questions and later arrivals without changing semantic cursors.
- [ ] Actual installed source-removed command exercises structured post,
  thread/inbox, unknown-response recovery and unchanged ordinary reading.
- [ ] Catalog/resource closure includes every changed import; unsupported
  older service behavior is explicit and never triggers plaintext fallback.
- [ ] Fast pure contracts remain in the admitted unit packet; installed,
  database/process and platform cases use the existing appropriate lanes.
  No new exhaustive PR matrix, environment provisioning or runtime adoption
  is inferred from this proposal.

These are required tests to implement, not assertions that they have run.
The parent [Validation Plan](README.md#validation-plan) continues to own the
complete service/plugin/native/lifecycle/release objective.
