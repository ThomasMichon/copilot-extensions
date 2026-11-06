# Optional retained attention reader

Back to [attention](attention.md) and the [PR plugin](../README.md).
The reviewed [A3 contract](../../../efforts/active/durable-pr-watch-delegation/durable-attention-slice.md#a3---optional-read-only-servicecli-consumer)
owns the intended consumer. This is an optional source seam, not installed,
provider, Dispatch or live-shadow qualification.

## Executing owner and availability

`AttentionReader` uses an existing `WorkspaceOperations` with actual acquisition,
selector/policy and protected-feedback callbacks. Its retained `ReplayStore`
must already have the explicitly selected layout: v5 by default, or v6 for
the historical mode below. Construction and reads do not adopt,
provision or repair it. The service validates this distinct reader under its
existing writer before starting the connector. Default hosts remain unchanged.
Supplying a type or callback label is not authorization or an implementation of
the selector/feedback owner.

Each read reloads the complete envelope under its existing operation lock,
checks current embedding/request authority, and projects only the bound
consumer's retained interests for that exact provider/endpoint/project/repository/
account/namespace. Current acquisition completeness, tri-state selection,
observation freshness, original pending age and epoch remain meaningful.
No bodies, action payloads or native receipts are returned.

Attempted captured feedback with unknown/partial native outcomes is
`feedback-reconciliation-pending`. Confirmed feedback without captured handled
progress is `feedback-tracking-unavailable`. A later same-candidate position
not covered by the original handled baseline is `feedback-change-unclassified`,
not a guessed follow-up. Proven B3 coverage remains waiting. Other captured
interests/consumers are independent. An attempted operation with no capture has
no recoverable interest binding: its scoped projections conservatively report
`feedback-interest-unbound` rather than inventing which consumer handled it.
Missing original provenance reports `feedback-provenance-unavailable`.
Cursor order alone cannot prove another operation's readback. Every attempted
capture needs its own original operation/generation/receipt-bound handled
provenance; otherwise it is `feedback-operation-readback-unavailable`.
The default v5 layout retains only the current handled provenance, not a historical
per-operation readback ledger. A newer operation replacing that proof can leave
an older capture unavailable too; this reader reports that limitation rather
than inventing historical completion or expanding the disk layout silently.
The separately negotiated v6 reader below can instead use each operation's
retained original-readback proof; v1 does not acquire that guarantee.

This conservative reader does not prove which differences are independent
dialogue or unsupported self-effects. It does not silently cover them, resend
feedback, reset pending age or create a second reconciliation-status ledger.
Original captures, native outcomes, journal input and handled state remain
untouched. A genuine later candidate or reopen retains its existing semantics;
unknown native outcomes do not become confirmed merely because a candidate moved.
Unknown or partially confirmed attempts remain unavailable for their original
consumer/role after close/reopen too; only handled/echo qualification is scoped
to the current epoch. Independent consumers keep the genuine reopened work.

## Distinct optional wire contract

The existing authenticated Gateway handles `POST /v1/attention/<target>/capabilities`
and `/read`. Both use the existing `{binding,request}` envelope and the current
Gateway principal-to-target binding. Body labels cannot override consumer,
scope or authority. Support is `agent-pull-requests.attention-read/v1`,
owner-store versions `[5]`, at most 16 items and 24 KiB per response, with
explicit embedding freshness/aging intervals, `effects: none`, `adopts: false`
and `acknowledges: false`.

`read` accepts exactly `{limit,after}`. First use `after: null`. The response
contains scoped projections, a snapshot identity and optional next page
`{snapshot,offset}`. Actionable projections use the existing aging-aware `rank`;
other states follow in deterministic PR/role order. An observation, availability
or ordering change invalidates the captured page: restart reading, not resume
at an offset in different evidence. Reads never advance a watch ACK, handled
cursor, interest enrollment or Dispatch state. Over-limit responses refuse;
the caller may explicitly select a smaller page.

`AttentionClient` negotiates this exact optional contract before reading,
validates scope and bounded response fields, and performs no retry or legacy
fallback. Missing routes, invalid declarations and owner/data loss are explicit
unavailable outcomes. Existing watch and workspace wire contracts are unchanged.
An overlapping capability refresh explicitly supersedes an in-flight read;
the client does not validate that result against cleared or replaced negotiation.
In particular, v5 bytes are never advertised as owner-v3 conversation support;
that older conversation-serving contract continues to refuse expanded layouts.
Disk compatibility is separate from HTTP compatibility.

## Explicit historical v2 reader

The trusted embedding selects `historical=True` on both `AttentionReader` and
`AttentionClient`. Only this mode advertises
`agent-pull-requests.attention-read/v2`, owner-store versions `[6]` and
`feedback: retained_original_readback`. The complete v6 envelope must already
validate under the existing service writer. Default v1/v5 behavior and ordinary
watch/workspace operations remain unchanged; no setting or request adopts
storage, selects credentials or enrolls a host.

The v2 reader checks every attempted capture's own original-readback proof and
confirmed native identities through the current owner. An uncertified operation
cannot borrow a later handled cursor or another operation's proof. Once all
applicable captures qualify, the current handled frontier and its existing
provenance determine remaining input. An older certified capture therefore
does not make already-handled work unclassified merely because it precedes
the frontier. Unknown/partial native outcomes remain pending across reopen;
independent consumers retain their own work. Unsupported verdict attribution
and unclassified later input remain unavailable.

Only the protected feedback owner writes proof. Repeated reads neither repair
null proof nor change captures, receipts, pending age, handled state, watch ACK
or Dispatch. The same 16-item/24-KiB bounds, scope checks, freshness, ordering
and request-task drain apply. V2 page identities include the selected contract;
an earlier v1 page cannot resume through explicit historical adoption even
when its projected items happen to match.

The existing capabilities/read routes and strict CLI syntax are unchanged.
Negotiation selects only the embedding's exact contract, not an automatic
version fallback. A historical client receiving a v1 declaration reports
`unsupported_attention_contract` (CLI exit 3) without sending a read or
retrying another path. A prior v1 client retains its existing response
validation and does not accept a v2 declaration. Explicit v6 adoption thus
requires a capable reader/client; rollback needs the declared refusal or an
authorized compatible owner, never relabeling v6 bytes as v5.

## CLI embedding and qualification

The real PR parser accepts `attention capabilities` and
`attention read --request <JSON-file-or->`. The embedding supplies an actual
authenticated `AttentionClient`; the CLI does not load an arbitrary owner,
select credentials, start a service or enroll a host. Without one, capability
output names unavailable owner binding; reads exit 3. Refused authority exits 4,
invalid input/result exits 1 and unknown flags retain argparse exit 2.

Source fixtures exercise actual CLI/client/Gateway/retained-owner reads,
restart, captured feedback/proven echo, malformed/foreign input and output,
page changes, real operation locks and service-writer startup/drain. They do
not qualify installed hosts, real provider feedback, live transport placement,
complete B3/A2, full CLI parity or production activation. Historical v2 source
fixtures additionally exercise original proof after overlapping operations,
null proof, mixed-version refusal, reopen, protocol-bound paging and service
writer startup/drain; they are not installed or live-provider qualification.
No migration,
deployment, retained daemon/container or live effects are implied.
