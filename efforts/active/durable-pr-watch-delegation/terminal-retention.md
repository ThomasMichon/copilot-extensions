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
  The first bounded source leaf selects an independently versioned read-only
  terminal workspace receiver through the existing Native/Fleet/Gateway
  negotiation and CLI facade. It does not relax conversation/v1 or enable
  submission, compact recovery reads, retirement requests or host adoption.
  Source fixtures qualify all declared layouts and actual service-writer
  validation/restart; installed/provider and full lifecycle qualification,
  feedback/write serving and retained-volume reader transition stay open.

Each runtime leaf needs normal review and focused supervised validation.
Source acceptance is not installed adoption, live provider qualification,
backup/log retention acceptance or permission to activate a production host.
Those broader P1/P2 and parent gates stay open.

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
