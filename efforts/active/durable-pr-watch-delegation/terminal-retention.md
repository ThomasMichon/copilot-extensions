# Coordinated terminal retention

Back to the [canonical effort](README.md) and
[Author/Reviewer amendment](author-reviewer-perspectives.md#terminal-retention).
This is an **agent-recommended, review-first source refinement** of P1/P2,
not serving support or authority to delete retained state.
It closes the design gap between a metadata tombstone and coordinated
body/cache/outbox/effect retention.
The [internal source embedding](../../../plugins/agent-pull-requests/docs/terminal-retention.md)
adds adopted terminal intent, admission/drain, minimal replay notification,
compact recovery-only original operations with protected readback and scoped
cache/materialization retirement.
Executing-host reader/service and installed qualification remain open;
the complete delivery and validation gates below remain the acceptance contract.

## Existing seams and the unsafe shortcut

| Existing owner | Current behavior | Required change |
|---|---|---|
| `ConversationStore.observe_workspace` | Atomically projects admitted evidence into the ReplayStore journal; terminal metadata loses prior events/digests | Commit terminal retirement intent and applicable notification progress in that same envelope |
| `ReplayStore` / `ReviewSelectionStore` | Strict adopted layouts retain immutable complete selected bodies, attempts and receipts | Add explicit reader adoption, terminal admission fencing and recovery-only compact operation records |
| `WatchReplay` | Freezes each original registration/event payload until exact ACK/cancel/settlement | Preserve original identities, criteria, deadlines and pending payload semantics; do not silently rewrite an event |
| `GenerationCache` | Separately locked immutable objects, scope pointers and explicit whole-cache rebuild | Retire only the terminal scope through resumable, owner-coordinated progress |
| `WorkspaceOperations` | Bounded in-process materialization bytes and generation-to-cursor bindings | Invalidate the selected epoch and drain admitted readers before retirement completes |

`GenerationCache.rebuild()` is not terminal retention: it clears other PRs,
does not fence an already admitted submission, does not compact its original
intent and does not settle a pending notification. Sequentially reading
ReplayStore pins and then deleting cache objects is also unsafe: a concurrent
selection can admit between those operations. Adding a third ledger or
retention daemon would merely relocate that race.

The governing vision is
[bounded conversations and terminal tombstones](../../../visions/plugins/agent-pull-requests/README.md#bounded-conversation-and-terminal-tombstones).
Use the existing storage/lifecycle owners and
[contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md).
No vision change or new forge implementation is required.

## Admission, evidence and compatibility

The trusted acquisition owner supplies complete authoritative terminal
evidence under the full provider/endpoint/repository/PR/principal/namespace
key, exact candidate and current observation epoch/cursor. Missing access,
selector departure, a 404, partial evidence or an old terminal observation
cannot retire a live or reopened PR. A cache ID, body hash or role label is
not authority. Existing observation ordering/CAS remains mandatory.

Expand the existing ReplayStore envelope through an explicit optional
terminal-retention data contract, independently declared from package version.
Its exact schema and capacity reservation are implementation-review work.
Old strict file readers must refuse the expanded envelope without rewriting
it. The actual lifecycle owner excludes or transitions incompatible readers
before adoption; neither a read request nor a package update adopts state.
Existing non-adopted layouts and default-off hosts keep their present behavior.
No automatic downgrade or destruction-based rollback is supplied.

Every capable path that can admit a new selection, action attempt, capture,
cache publication or materialized read checks the selected scope/epoch's
retirement state at its existing mutation/admission boundary. Already admitted
work must drain under its original owner and deadline before its required
payload is compacted. A durable fence is necessary but does not prove that an
in-flight native sender, cache writer or body reader has finished. Failed drain
preserves custody and reports unfinished retirement, not success.

Support is internal source embedding first. Optional later serving discovery
must describe the actual executing/adopted receiver. Prior clients keep their
baseline operations against non-adopted owners; an incompatible adopted owner
reports unavailable before effects. New callers never probe an effectful form
and replay an older one. Installed adoption and provider rollback require their
own demonstrated reader/lifecycle compatibility.

## One durable decision, resumable physical work

Keep progress in ReplayStore's existing complete-envelope transaction, keyed
by the full scope and terminal epoch. It records compact terminal evidence,
original terminal cursor, captured delivery identities, retirement stage and
remaining recovery obligations. Use finite existing-owner capacity; full
admission refuses without evicting another PR, pending delivery or receipt.

1. **Commit terminal intent.** In one atomic envelope replacement, apply the
   authoritative journal tombstone, fence new content/effect admission and
   record notification/compaction progress. Project only body-free terminal
   facts through the existing replay core for registrations actually admitted
   to this target. Preserve their criteria, original deadlines and exact
   request/registration/event identities. Do not equate terminal observation
   with delivery, ACK, feedback completion or permission to cancel.
2. **Drain and compact owned payloads.** The current lifecycle/execution owner
   joins the admitted source/read/send work needed by this scope. Once safe,
   atomically replace body-bearing selections/captures and redundant payloads
   with compact recovery records in that same owner file. Keep original
   operation/intent/action/attempt IDs, candidate, scope, native receipt IDs,
   prepared comment/write IDs, necessary hashes, outcome and original recovery
   owner/deadline/escalation. Mark unattempted terminal actions retired, never
   newly sendable; preserve unknown and confirmed as distinct states.
3. **Retire scoped cache and memory.** After durable intent and required drain,
   durably clear that scope's current pointer before removing its validated
   immutable objects. Clear its server materialization bytes and cursor
   bindings through their existing owner. All still-needed recovery material
   must already have a bounded body-free representation. Preserve other scopes,
   replay files and unexpected/linked artifacts. Progress resumes the same
   terminal epoch after interruption; it is not whole-cache rebuild or repair.
4. **Complete only the proven stages.** Persist cache/payload completion and
   independent delivery/recovery disposition. Pending ACK or unknown effect
   recovery can remain after body removal; neither may be reported settled
   merely because physical retirement finished.

No disk lock is held across forge IO or waiting for drain. Coordinate the
existing lifecycle, ReplayStore, materialization and cache locks with one
documented ordering; test the actual callers rather than assuming a lock
hierarchy from class names. An uncertain replace requires readback of that
same scope/epoch/stage before advancing. An IO/permission error never means
the old bytes were absent or authorizes a forced rebuild.

Frozen pending events require particular care. Previously exposed bodies
cannot be silently stripped under their existing event ID, nor can a pending
event be discarded to satisfy retention. Use its existing exact ACK, explicit
cancel or recorded recovery settlement and original deadline. Until the
relevant body-bearing delivery is settled, report a bounded retention blocker
with its recovery owner/escalation; do not claim body-free completion. New
terminal deliveries use compact facts, not cached generation/native-snapshot
bodies. Any required new delivery contract is expanded before consumers use it.
Where a legacy delivery has no finite deadline, the existing owner must supply
an explicit bounded recovery/settlement policy before enabling retirement for
that scope. A new timeout must not be invented by the collector or masquerade
as consumer cancellation; expiry alone never acknowledges a delivery.

Recovery-only records are not a second archive or executable intent. They
cannot reconstruct a body or resend a write. Adapt original protected readback
to the minimal identity evidence it actually needs; equal current text, vote,
GUID or actor never confirms an unknown historical effect. Unsupported
attribution stays unknown and reaches the retained deadline/escalation path.
Do not retain a full body indefinitely because reconciliation is unavailable,
and do not delete the minimal recovery obligation when a timer expires.

An authoritative reopen creates a new live epoch with fresh acquisition.
Old retirement work is generation/epoch-fenced before each pointer/object or
memory mutation and cannot delete newly acquired content. Historical unknown
receipts stay recovery-only and cannot become sendable in the new epoch.
Retain only the prior compact terminal summary, not old conversation bodies.

## Delivery order and acceptance

- [x] T1: Expand adopted storage and validators; implement terminal intent,
  admission/drain integration and atomic minimal notification progress.
  Source #11605 is merged; physical retirement and executing-host adoption
  remain T2/T3, not implied by this finite source result.
- [x] T2: Implement compact recovery-only operations and actual protected
  readback, then resumable scoped cache/materialization retirement.
  T2a source #11614 and T2b source #11623 are merged. Original compact
  identities/deadlines, protected readback and scoped cache/memory retirement
  are source-qualified; executing-host and installed adoption remain T3.
- [ ] T3: Qualify the executing workspace/reader/service boundary and supported
  skew/restart/reopen paths; update actual-behavior docs only as leaves land.
  The first bounded source leaf, merged in #11625, selects an independently versioned read-only
  terminal workspace receiver through the existing Native/Fleet/Gateway
  negotiation and CLI facade. It does not relax conversation/v1 or enable
  submission, compact recovery reads, retirement requests or host adoption.
  Source fixtures qualify all declared layouts and actual service-writer
  validation/restart; installed/provider and full lifecycle qualification,
  feedback/write serving and retained-volume reader transition stay open.
  Strict explicit standalone selection and fresh-only provisioning merged
  in #11628, including same-PR terminal notification capture under the
  original request budget. The focused
  `test_terminal_service_lifecycle.py` source packet merged in #11631 exercises
  the real service writer and Gateway admission/drain with populated buffers,
  disconnected acquisition, failed/cancelled close, exact pending delivery
  across fresh-reader restart and reopen, and stale retirement refusal.
  Its connector/native source are synthetic; this finite qualification does
  not close executing-host, installed, provider or feedback/write acceptance.
  The bounded compatibility trace found that compact recovery preserves
  prepared IDs/receipts but not an operation's retained acquisition slots.
  The [mapping-preservation refinement](#retained-acquisition-mapping-composition)
  below addresses that storage gap before extending the terminal receiver.
  Existing serving refusal remains intentional until a separately reviewed
  compatible composition is supplied.
  No new remote retirement operation or automatic cleanup worker is implied;
  retained-reader transition and broader feedback/write, provider and
  installed gates remain separate.

Each runtime leaf needs normal review and focused supervised validation.
Source acceptance is not installed adoption, live provider qualification,
backup/log retention acceptance or permission to activate a production host.
Those broader P1/P2 and parent gates stay open.

## Retained acquisition mapping composition

This **agent-recommended, review-first** source refinement cleared review in
#11633. Its storage/library source leaf merged in #11634 and separately
negotiated workspace serving in #11636. Standalone selection and installed
support remain open. It composes the existing
[persisted acquisition mapping](comment-observation-correlation.md#persisted-acquisition-mapping-refinement)
with terminal compaction under their same ReplayStore owner. It closes the
bounded-conversation and original-comment correlation intent above without
adding another mapping database, body archive, acquisition loop or recovery
owner.

### Preserve evidence before expanding serving

Both current features are orthogonal extensions on owner-v3/v4/v5/v6, not
successive base-layout versions. `CommentMappingStore` retains one bounded
acquired-evidence slot per eligible prepared action. At design intake,
`terminal_recovery.compact` replaced those body-bearing operations with compact
original identities and receipts but did not transfer their mapping slots. The prior
`retained-comment` reader selects only active original operations, and the
terminal read-only receiver intentionally excludes prepared/mapping serving.
Do not remove that refusal first or claim that sharing a base version makes
these contracts composable.

Extend the existing compact record, transaction and review reservation through
an explicitly adopted, independently versioned optional storage contract.
The exact field layout and capacity arithmetic belong to the implementation
review. A selected capable reader must still preserve non-adopted layouts;
older strict readers refuse an expanded layout unchanged. The actual lifecycle
owner excludes incompatible readers before adoption. Reads, startup and normal
acquisition never adopt, downgrade or silently strip the extension.

Compaction must transfer every captured prepared action's existing nullable
mapping slot alongside its original compact IDs, content hashes, outcome and
receipt, in the same complete-envelope replacement. Retain the original
generation reference, discussion interval/completeness, status/reason and
available object digest only; no body, executable preparation, source manifest
or new observation is created. Original action/receipt custody remains
authoritative. The transferred slot is historical evidence at its recorded
interval, not current-content or original-native-version proof.

If existing mappings cannot be represented losslessly by the selected compact
contract, refuse compaction unchanged before cache retirement. A null slot is
also distinct from unsupported mapping retention; do not silently discard the
adopted per-action contract merely because it has no positive match. Explicit
adoption must fund existing active and compact metadata within the same bounded
review capacity or refuse atomically. Preserve the existing operation/action
and slot bounds and the native-outcome reservation; never borrow another
reservation, evict another operation or retain a second unbounded history.

Recovery readback can change a compact unknown action's receipt/state. It must
invalidate that action's transferred slot in the same transaction, as active
receipt updates already do. A later receipt cannot inherit an old acquired
correlation. Reopen does not recalculate a historical compact slot or promote
the old action into a live/executable operation. Fresh acquisition may update
eligible active operations only; current tag/body equality never reconstructs
a retired mapping or confirms an unknown historical effect.

### Scoped read and optional receiver sequence

First extend the existing scoped retained-evidence library read to select either
the original active action or its compact replacement, never both. Report the
record representation explicitly and retain its original scope, operation,
action, intent, prepared identity, body hash, outcome and receipt. Do not invent
retired preparation fields to satisfy an older response schema. Missing
preparation, a missing slot and unsupported historical representation stay
distinct. The read has no cache/body/native-IO dependency and changes no
receipt, event, attention, handled position, pending age or ACK.

Only after that storage/read provider is reviewed and qualified may a separate
optional terminal workspace contract expose `retained-comment`. Advertise the
actual executing/adopted receiver and its supported result representations
through existing discovery. Preserve terminal-read/v1 and retained-comment/v1
semantics; do not append an operation to their exact declarations or reuse an
old result shape with different identity guarantees. New consumers select
support before sending a request. Missing, malformed, withdrawn or rolled-back
support refuses only the optional composition before effects, without a GUID
search or mutation fallback. This does not enable preparation, submission,
feedback, attention-writer composition or remote retirement.

### Serial delivery and focused validation

1. Review this refinement before changing persisted or serving schemas.
2. Deliver the explicit existing-owner storage adoption, lossless compaction
   and scoped library read. Cover real-file restart, strict old-reader refusal,
   adoption/capacity refusal, null and divergent slots, create/reply/owned-edit,
   unknown/partial receipts, recovery receipt invalidation, same-epoch retry,
   interrupted replacement and reopen. Prove byte-identical refusal and
   original identity/receipt/deadline preservation with no new sends,
   acquisitions, false confirmation or feedback/ACK/event progress.
   The finite source leaf merged in #11634 with 19 new focused cases and a
   final 89-case layout/cache/owner packet; installed and provider acceptance
   is not implied.
3. Separately expand the optional executing receiver and consumers, with actual
   Native/Gateway/CLI negotiation, both mixed-version directions, withdrawal
   and rollback controls. Exercise retained reads after populated-cache
   retirement and fresh-reader restart, not just an empty fixture.
   The finite core source leaf merged in #11636; explicit standalone host
   selection/qualification and installed support remain separate.
4. Return to the full T3 and both parent acceptance plans. Source fixtures do
   not qualify installed hosts, retained-volume reader transition, providers,
   all publishers, whole-system retention or operational effects.

## Validation plan

- [ ] Atomic interruption before/after terminal replacement preserves either
  the old complete state or the same tombstone/fence/notification progress;
  restart never forgets terminal notification or creates a replacement event.
- [ ] Real admission races cover selection allocation, begin-action, capture,
  cache publication and materialized reads already in flight. Failed or expired
  drain leaves exact custody; no deadlock, resend or premature retirement.
- [ ] Minimal recovery survives restart with original unknown/confirmed IDs
  and deadlines, without body/source payloads. Changed intent, same-value
  readback and old-epoch retries cannot send or falsely confirm.
- [ ] Existing pending delivery retains its exact payload/identity through
  disconnect, timeout and lost ACK; new terminal events contain no bodies.
  Other registrations and independent ACK/cursors remain unchanged.
- [ ] Cache interruption covers uncertain pointer clearing, partial object
  removal and missing already-retired objects. Wrong scope, namespace,
  service, unsafe paths and corrupt state refuse without unrelated deletion.
- [ ] Populate the real server read buffer before retirement; after successful
  drain neither descriptor/chunk reads nor retained tool buffers serve old
  body bytes. Client-held bytes already delivered are not remotely erased.
- [ ] Reopen races and delayed old-epoch workers preserve new cache/current
  pointers, attention and feedback; old cursors require resynchronization.
- [ ] Both mixed-version directions, explicit adoption, unsupported optional
  discovery and rollback refusal preserve the documented baseline. Capacity
  refusal and permission loss never trigger deletion or another state owner.
