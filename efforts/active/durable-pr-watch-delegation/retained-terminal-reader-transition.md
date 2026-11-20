# Retained terminal-reader transition

Review-first T3 refinement of [coordinated terminal retention](terminal-retention.md),
originating in [#13487](https://github.com/ThomasMichon/copilot-extensions/issues/13487)
and coordinated under the [canonical effort](README.md). This proposes source behavior, not
permission to migrate an installed volume or stop its owner.

## Tracking and continuing custody

The original bounded transition driver retains design, implementation,
qualification and publication responsibility. The canonical Root
P13487/V13487/G13487 and existing detailed states below preserve the complete
finite source outcome; an administrative tracking change is not its completion.
Current bootstrap and metadata-only credit remains narrower than positive
generation-bearing or active-native-watch transition, as the unchecked
refinement below records. Full T3, providers and installed/whole-parent
acceptance remain separate.

The administrative consolidation of #13487 under #13680 did not complete
this transition or transfer its driver. P13487/V13487/G13487 remain the
canonical work and acceptance entries. Preserve any genuine intervening
source-OPEN condition and the actual driver/reader/writer/cache/ReplayStore/resource
owners; administrative closure creates no handoff, claim expiry, generic ACK, migration,
service-stop, forge request, protected send or execution grant.

## Gap and existing owners

The standalone workspace host already supports explicit fresh terminal-reader
provisioning. `serve` deliberately validates retained state without adopting
extensions, and `provision` refuses a populated root. Retained baseline volumes
therefore have no supported host-level transition to the terminal reader.
Removing either refusal would turn ordinary startup into implicit migration.

Reuse `HostConfig`, the service's `agent-pull-requests-watch` `SingleInstance`
lease and the existing ReplayStore adoption transactions. This closes the
[bounded terminal-conversation intent](../../../visions/plugins/agent-pull-requests/README.md#bounded-conversation-and-terminal-tombstones)
within the [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md).
It adds no migration daemon, second state ledger or remote operation.

## Explicit transition

Add a local `adopt-terminal --config <absolute-config>` command to the
standalone service CLI. It requires the already supported explicit terminal
workspace selection and an existing valid, service-bound conversation store.
It is not a substitute for fresh provisioning and does not create missing
replay/cache state, adopt a different namespace/service identity, change a
credential or reinterpret an unsupported layout.

The command acquires the same host-local writer lease used by `WatchService`.
A running or incompletely drained service retains that lease, so transition
refuses rather than stopping it, waiting indefinitely or treating passive
readiness as reader exclusion. Preserve the existing single-host storage
assumption: this is not a cross-host or arbitrary external-reader fence.
The deployment owner must separately exclude unsupported direct volume users.
No new source-side attestation service is required.

Before any durable adoption, validate the selected core declarations, original
service/namespace binding, supported retained envelope, existing cache and
selection compatibility. A terminal-only transition must not silently strip
prepared intents, acquired mappings, selections or any other adopted extension.
Unsupported combinations refuse before the first change. Existing terminal
mapping selection may compose only its already supported prerequisites.

The first finite implementation is a bootstrap-only retained-volume transition.
Source review showed that retaining generation or unknown-attempt bytes would
not preserve their original usable reader/recovery interfaces after selection.
Require the standalone version-three owner, empty generation/pointer directories,
empty active/compact operation and conversation journals, and empty
terminal-extension state. Refuse other volumes unchanged
and retain their original reader; positive populated/recovery-compatible
transition remains T3 work, not permission to settle or discard accepted work.
Also require a bijection between full workspace scopes and account/repository
targets, plus original consumer/account/repository reachability for every
retained replay record. Every retained watch receipt requires finite original deadlines and
terminal-compatible `merged`, `closed` or `head_changed` predicates. Other
predicates refuse without disabling their original native observer; richer
predicate preservation remains full T3 work. Apply that same policy to new
registrations on the selected terminal host, without changing strict legacy
watch capability/receipt shapes. Include acknowledged/cancelled receipts in
preflight so the original register retry is never stranded by the new reader.
Advertise the optional terminal admission profile through every watch binding's
capabilities media profile, not only workspace discovery. The capable consumer
checks criteria before suspension; settlement still reconciles original receipts.
It also rechecks existing preparations before emission after a policy change:
incompatible tasks wake for reinspection, while attempted/unknown requests
retain original reconciliation custody rather than being discarded or replayed.
Keep optional old-core refusal and baseline-host compatibility.
Validate plain lease files
before PID writes and reject native callback state. Use bounded cache
eligibility checks and the original monotonic budget, without scanning or
repeatedly decoding retained generations.

Use the original command's finite operation deadline and PID/held-lease
checkpoint at every existing adoption boundary. Select the same terminal
retention, recovery and cache-retirement contracts and bounded capacity used
by fresh provisioning, with optional terminal mappings when explicitly
selected. Preserve existing conversation records, replay registrations,
request/event IDs, deadlines, delivery/ACK positions, original prepared
operations/receipts and all cache pointers/objects.

The transition enables capable readers; it does not observe a provider,
create a terminal observation, compact an operation, retire cache content,
settle a delivery or send a write. In particular, adding terminal storage
cannot manufacture authoritative terminal evidence for an existing generation.
Target capture includes already frozen pending events, preserving their
original IDs/deadlines under the same request budget. Public load/sync output
projects only bounded workspace retirement metadata; cross-consumer delivery
and operation custody remains internal even for high fan-out.
The standalone profile does not synthesize reduced watch notifications from
workspace retirement. Its selected core declares native-watch preservation;
the existing native observer continues evaluating fresh predicates, including
after an unmerged workspace closure, and supplies the complete native snapshot
and triggering head. Retirement and native application resolve elapsed original
deadlines before success evidence. No retired workspace bodies are restored.
Native observation also retains its original pre-IO epoch fence: a delayed read
spanning workspace close/reopen refuses unchanged, while a freshly captured read
keeps its complete native payload.

## Interrupted adoption and skew

The existing adoption owners commit complete envelopes and recognize their
already-adopted contracts. A failure may leave a valid partially expanded
envelope; report that outcome, retain its bytes and release the lease only
after synchronous work is settled. Do not promise multi-step rollback.
An uncertain replace requires readback through that same owner before a later
step; a failed invocation is not proof of no change.

An explicit retry over the same selected volume revalidates the actual
persisted contracts and capacity and continues missing compatible adoption
steps under a newly admitted finite invocation. It does not reprovision,
reset identities, strip extensions or silently substitute another selection.
Unsupported/corrupt state remains an explicit refusal, not an invitation to
repair or delete it.

`serve` and `provision` retain their existing defaults and refusals. Older
strict readers must refuse expanded storage without rewriting it. A selected
new reader validates the adopted extensions before advertising optional
support. Removing the selection or rolling back to an incompatible reader
refuses rather than downgrading the volume. There is no effectful new-command
probe followed by a legacy provisioning fallback.

## Serial delivery and acceptance

- [ ] Complete the [populated live-cache refinement](#populated-live-cache-refinement)
  with durable generation association; the metadata-only leaf does not close it.
- [x] Review this contract before implementing the command or changing retained
  host adoption (#13488; current-head Copilot approval and required green CI).
- [x] Implement the local existing-owner transition without changing baseline
  provisioning, serving, remote workspace contracts or physical retirement.
- [ ] Qualify a populated baseline volume, preserving original replay/cache and
  unknown-effect identities through transition and fresh-reader restart.
  The finite bootstrap path qualifies an existing empty workspace cache and
  terminal-compatible finite pending replay. Populated cache/active or compact operation volumes
  refuse unchanged, with original inspection preserved. Positive populated
  generation, richer-predicate and recovery compatibility remain with full T3.
- [x] Cover actual competing service/transition lease ownership, failed drain,
  operation expiry, unsupported core/layout/binding/capacity, partial adoption,
  uncertain replacement and explicit retry. Refusal before adoption must
  preserve bytes; partial progress must remain complete and recoverable.
- [x] Exercise the actual CLI and old/new reader directions, optional mapping
  selection and rollback refusal. Confirm no native acquisition, protected
  send, cache deletion or delivery/ACK advancement.
- [ ] Return to full T3 and both parent acceptance plans. Source controls do not
  qualify installed migration, every provider, whole-system retention or a
  production operation.

## Populated live-cache refinement

The reviewed intent under #13487/#13560 remains open. The bootstrap
implementation merged in #13495. Implementation review proved that the baseline
journal cannot durably associate a legacy generation with an observation:
distinct same-second acquisitions can share its rounded normalized projection.
Unassociated legacy generation migration remains unsupported; the separately
selected recorded-association transition below does not infer bindings or
restore the unsafe projection-equality path.

The finite source leaf is narrowed to retained journal metadata and cache
misses, not generation content. This preserves useful original state without
claiming the positive populated-volume outcome. Active/compact-operation
recovery and richer predicates remain separate T3 work.

### Compatibility boundary

The current terminal workspace reader checks a generation's live admission and
epoch binding before serving `lookup`, `chunk` or a `sync` comparison. Baseline
cache objects have neither binding. Merely allowing nonempty cache directories
would strand an otherwise valid original reader. Do not remove those terminal
checks or label an old generation freshly acquired to make transition succeed.

Extend the existing local `adopt-terminal` operation, not startup, provisioning,
a remote mutation or a second migration command. Reuse its writer lease,
original finite operation budget and ReplayStore/cache owners. The selected
executing core must explicitly declare the metadata-only
`agent-pull-requests.retained-cache-metadata-adoption/v1` profile; it is not
generation-binding support. An older core keeps bootstrap-only refusal. Wire
workspace protocols, terminal-reader selection, baseline defaults and strict
old-file-reader refusal remain unchanged.

Initially admit only the standalone version-three baseline owner with no
active/compact review operations and no populated terminal-extension state.
Every retained conversation must have an existing complete, open, nonterminal first epoch
and no previous terminal summary. Every legacy generation remains with its
original reader. Future positive adoption requires a durable association between
the exact generation ID and its original observation/epoch, with normal contract
review before that storage or writer change. Neither timestamp bounds nor exact
normalized projection equality supplies that missing association.
Likewise, retained conversation metadata does not record its original native
watch association. If active legacy native waits coexist with that metadata,
refuse unchanged instead of assigning the current configuration's mapping:
cross-account/repository remapping can otherwise let a delayed read escape
close/reopen fencing. Keep the active watch with its original usable reader.
Completed frozen delivery and empty-journal bootstrap remain supported.
Refuse absent/ambiguous conversation evidence,
closed/reopened histories, incomplete or terminal generations, unsupported
extensions and unknown scopes before adoption, without changing original reads.
An empty-cache bootstrap remains supported.

Preflight uses the actual cache owner for plain paths, identities, scopes,
references and original byte/count limits. Generation objects refuse before
adoption, retaining their current/noncurrent original reads. No new capacity,
eviction, rebuild or orphan cleanup is inferred. Configured growth beyond the
terminal binding limit also refuses, rather than silently reducing capacity.
Malformed, dangling, staged or unsupported cache entries refuse unchanged.
A valid existing null rebuild pointer retains
its cache-miss meaning rather than being converted to a generation.
Enumeration, bounded reads, validation and final adoption share the original
deadline and held-lease checks; do not repeatedly decode all generations or
perform an unbounded inventory before checking the budget.

Under the existing exclusive owner and lock ordering, preserve original events,
sequences, intervals and metadata exactly while adopting empty terminal-reader
extensions. Create no live admission, generation binding, observation or watch
success. Later fresh acquisition retains its actual terminal-aware owner.
Keep every cache object and pointer byte-for-byte unchanged, including IDs,
revisions and retained freshness. Reserve complete metadata capacity before
committing adoption; do not publish a selected-reader-ready result until all
required reader extensions are present.
The initial-baseline eligibility rules do not reject this invocation's own
partially adopted compatible metadata on explicit retry; validate that metadata
against the same original journal and immutable objects instead.

Retain the existing complete-envelope interruption and uncertain-replacement
contract. Retry the same selected volume through the original operation,
reconcile any already-adopted metadata and continue missing compatible steps
without duplicating journal events or rebinding historical generations.
Partial adoption is reported and retained, never silently rolled back.
The capable reader must remain usable after fresh-process restart; a stale
pre-transition executable still refuses expanded state without rewriting it.

### Finite acceptance

- [x] Land this refinement through normal plan review before changing runtime
  eligibility or binding behavior (#13560; current-head approval and required CI).
- [x] Review the [durable generation association](#durable-generation-association)
  refinement before changing baseline writers, storage or populated transition
  (#13724; current-head approval and required green CI).
- [ ] Transition a durably associated populated baseline through the actual CLI, then serve current
  and explicit noncurrent `lookup`/all `chunk` reads after a fresh-reader restart.
  Compare original descriptors, reconstructed bytes, pointers, journal/events,
  replay identities/deadlines/delivery positions and cache freshness.
- [ ] Exercise the actual selected workspace `sync` comparison against a retained
  generation, with new synthetic acquisition and existing CAS/epoch fences.
  Transition itself performs no acquisition, write, ACK or cache retirement.
- [ ] Qualify active legacy native-watch migration with its original
  workspace/epoch association; the metadata-only leaf refuses that unqualified
  combination rather than inventing a mapping or canceling original work.
- [x] Qualify the narrower metadata-only transition through the actual CLI and
  fresh reader process, preserving original events, frozen replay and null
  misses. Fresh load/sync creates only newly acquired generation bindings.
- [x] Refuse every legacy generation while preserving original current/noncurrent
  reads, including failed initial journal write and same-second reopen ambiguity.
- [x] Qualify missing/skewed core declarations, absent/closed/reopened conversations,
  unsupported operations/extensions, corrupt/dangling/staged objects, scopes,
  null pointers, capacity and deadline refusal. A selected null rebuild pointer
  without generation content needs no journal or invented admission.
  Original readers and bytes remain
  usable on refusal; no predicate is silently dropped.
- [x] Interrupt before and after each durable boundary, lose a replace result and
  explicitly retry under the same lease/budget contract. Prove complete recoverable
  envelopes, no duplicate events, exact binding continuity, competing-owner
  exclusion and old/new-reader rollback refusal.
- [ ] Return to full T3 and both parent plans. Positive cache compatibility alone
  does not qualify active/compact unknown-effect recovery, prepared selections,
  specialized attention owners, historical terminal volumes, installed migration,
  provider conformance or whole-system retention.

### Durable generation association

The optional fresh-baseline writer records original associations. Source
controls qualify recorded-generation adoption and body reads. Active
native-watch and unknown-effect transition,
installed migration and full T3 remain unqualified. The unselected
baseline `WorkspaceOperations` path publishes through `GenerationCache` before
calling `ConversationStore.observe_workspace`. Its feedback-only
`_generation_positions` map is process-local. `project_generation` rounds native
observation intervals to integer seconds, and `ConversationJournal.observe`
can reuse a cursor for unchanged meaning. Neither that cursor alone, the
current journal projection nor process memory identifies the original immutable
generation after restart. The terminal-aware publication path already uses the
existing ReplayStore transaction and cache owner; extend those owners rather
than introducing a second journal or inferring historical associations.

**Expand the writer first.** The optional executing-core profile is
`agent-pull-requests.workspace-generation-associations/v1`, separately from
metadata-only adoption and terminal-reader support. An explicit baseline
workspace configuration selection enables it on fresh provisioning. Ordinary
`serve` validates the selected persisted contract and never adopts it.
Absent selection preserves baseline provisioning, publication and wire behavior;
missing support refuses the selected optional feature before durable changes.
Use the existing selected-core declaration, `HostConfig`, ReplayStore complete
envelope and cache descriptor owners, with a versioned extension that strict
older readers refuse unchanged. Do not select support from package versions,
an installed-but-not-executing core or a new prerequisite RPC.

The trusted acquisition/publication owner records the full workspace scope,
immutable generation ID and original observation cursor together with enough
original observation identity to distinguish acquisitions whose normalized
projections share a cursor. Record only bounded identity metadata, not another
copy of discussion, diff or message bodies. This association is not a terminal
live admission, a current-epoch label or evidence that a provider was observed
again. Callers cannot supply or repair it through HTTP. Preserve generation
bytes, digest inputs, cache pointers and journal event/cursor meanings.

Association must be durable before a generation is reported as successfully
published under this profile. Reuse the existing ReplayStore-before-cache lock
order, staged complete-envelope transaction and finite publication checkpoint;
do not hold the cache lock while acquiring the replay lock. Validate and reserve
bounded association capacity before cache writes, after the cache owner's
guarded CAS/capacity admission. Definite conflicts or full-cache outcomes
record no observation or new reservation. Failed, stale or conflicting
observations and failed cache CAS must not create usable associations. An
uncertain write remains uncertain until readback through those same owners;
retry reconciles its original generation and observation rather than appending
an event, fabricating success or guessing which replacement occurred. An
association without its confirmed cache object cannot make a missing generation
readable. Preserve unresolved publication custody without deleting its bytes.

Bound metadata by the selected cache's original generation/count limits and the
existing envelope byte limit. Exhaustion refuses the affected publication before
effects instead of growing capacity, evicting retained work or dropping an
association needed by a retained generation. Repeated observation of the same
immutable ID may reuse only a compatible original association; conflicting
epoch or scope evidence must remain explicitly ineligible, never overwrite the
original binding. This is especially important across close/reopen histories.

**Migrate only recorded generations.** After writer support is reviewed and
qualified, extend the existing `adopt-terminal` preflight for this profile.
Validate every retained generation, including noncurrent objects, against its
durable original association and the existing complete open first-epoch journal
requirements. Create terminal generation bindings from that recorded evidence
under the original exclusive lease and adoption transaction, not from today's
configuration or journal high water. Refuse missing, conflicting, stale,
closed/reopened or unsupported evidence before adoption. Unassociated legacy
objects remain readable through their original compatible baseline reader;
this proposal supplies no backfill, re-acquisition or timestamp/projection
matching shortcut. Empty-cache and metadata-only transitions remain supported.
Active native waits and unknown-effect operations keep their separate current
refusals and acceptance obligations.

**Validation before claiming positive compatibility:** exercise the actual
baseline CLI with the selected profile, multiple current/noncurrent generations
and a fresh writer restart, then the actual transition CLI and fresh terminal
reader `lookup`, every `chunk` and `sync` comparison. Include distinct native
intervals with identical rounded projections, unchanged cursors, repeated IDs,
scope/epoch conflicts and close/reopen. Interrupt each durable publication and
adoption boundary, lose a replace result, fail cache CAS and exhaust original
capacities/deadlines; reconcile retry without duplicate observations, false
bindings or changed original cache/replay bytes. Cover competing writers,
old/new selected cores, absent/malformed declarations and rollback refusal
through real parser/process fixtures. Run focused unit contracts in required
CI and the existing explicit process lane for restart/interruption controls.
No provider request, protected send, ACK, cache retirement, service stop or
installed migration is part of this source qualification. Keep P13487/V13487/
G13487 and both parents open until their own remaining gates are satisfied.

## See Also

- [Focused source qualification and retained boundaries](journal/2026/11.19%20Retained%20terminal%20reader%20transition.md)
- [Standalone terminal host](../../../services/pr/README.md#optional-terminal-read-hosting)
- [Coordinated terminal retention](terminal-retention.md)
- [Canonical effort](README.md)
