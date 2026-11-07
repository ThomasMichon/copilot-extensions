# Owned Codespace Capacity

- **Slug:** `owned-codespace-capacity`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed proposal and implementation PRs
- **Created:** 2026-11-06
- **Status:** Active
- **Vision:** [agent-codespaces](../../../visions/plugins/agent-codespaces/README.md), `bounded-shared-pool`, `reuse-over-recreate`, `cross-machine-allocation`
- **Umbrella issue:** #4187 (bounded admission/reuse slice only)

## Guiding Intent

Recognize owned-venue pressure independently from running-core headroom, and
reuse suitable existing Codespaces across sequential efforts without colliding
with active agents or disturbing retained work.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Capacity driver | Own proposal, source changes, validation and PR stewardship | Managed source worktree |

## Coordination

- **Topology:** One owner; dependency-ordered independent PRs.
- **Host (owns PRs):** Capacity driver.
- **Delegates:** None.
- **Handoff:** Preserve the canonical effort, remaining gates and outstanding PRs.

This is a finite slice of #4187, not ownership of its complete pool program.
Preserve periodic recycling and rescue ownership, #3698 live capture
qualification, #7949 owner-wave integration, #5761 broader discovery work, and
other contributors' open changes. Extend the existing configuration, inventory,
pool planner, borrow/return and creation owners; do not build another store,
allocator, daemon or cleanup loop.

## Context

At inception, allocation consulted running-core headroom but not owned-slot
pressure. The admission flow merged in #11228 now independently enforces both.
Cross-account discovery can retain partial results after one account fails;
that remains an observation, not complete selected-account admission evidence.
A stopped venue still consumes an owned slot.
The existing per-workstream reuse path does not cover sequential cross-effort
reuse. Existing reclaim paths protect live holders and rescue work; capacity
pressure must not relax those protections.

The proposal extends the vision's independent-capacity and sequential-reuse
intent. It follows the existing configuration layering and optional owner
composition contracts, not the unwired full-publication inventory foundation.
The reviewed proposal, admission source, released-only lease receiver and first
stopped-venue sequential-reuse consumer are merged. Release and selected
installed/live consumer qualification remain open, as does generation-qualified
recovery after an unpersisted shared-return acknowledgement.

## Request

Operator request, 2026-11-04, preserved verbatim:

> We apparently now blow out people's limits of max owned Codespaces, which turns out to be 30 per account. We should recognize when we are at the limit. We should also update strategy so we *are* willing to have multiple efforts allocated per Codespace, so long as we don't have two running agents driving two efforts in parallel that need the same Codespace. We can also reuse Codespaces for read-only work, and for Review work that doesn't involve builds.

The observed 30-venue ceiling motivates a configurable conservative admission
policy, not a claim that every provider account has an immutable limit of 30.
That policy distinction is agent-recommended; the independent accounting and
safe sharing requirements above remain unchanged.

## Scope Approvals

The source charter permits investigation, source fixes, bounded validation and
normal issue/PR publication for this slice. A reviewed plan does not authorize
deployment, new venue creation, deletion, stopping another agent, forced
takeover, interactive authentication, credential export or discarding work.
Any installed or live acceptance retains its separately applicable authority
and existing resource custody.

## Plan

### Phase 1 - Review intent and admission contract
- [x] Merge this vision clarification and proposal through the normal review/CI gate before runtime implementation. (#11223)
- [x] Confirm the existing selected-account/configuration and borrow/return seams; retain other owners and define actionable unknown-capacity outcomes.

### Phase 2 - Prove independent capacity admission
- [x] Extend native configuration and the existing pool decision with a configurable owned-count policy (conservative default 30), independent running-core accounting and selected-account scope.
- [x] Require complete selected-account inventory for creation admission; distinguish valid empty results, partial/truncated inventory, authentication failures and unknown machine cost.
- [x] Keep `allocate`, `pool`, ordinary `create` and their presenters consistent: a full stopped pool cannot yield blind creation, and stopping alone cannot free an owned slot.
- [x] Preserve explicit `--force-create` as an intentional policy override with truthful diagnostics; it never supplies access, changes provider limits or permits broader reclaim.
- [x] Preserve provider-side quota refusal and bounded existing retry/reclaim semantics after concurrent capacity changes; no local snapshot promises a global reservation.
- [x] Publish the first complete admission flow after focused checks, then steward it through merge. (#11228)

### Phase 3 - Prove safe sequential reuse
- [x] Expand the native lease receiver with capability-advertised acquisition that refuses expired-but-unreleased holders. (#11306)
- [x] Migrate reuse callers only when the selected receiver supports released-only acquisition; missing support or rollback must never silently enable stale takeover. (#11319)
- [x] Extend existing suitability/admission and borrow/return paths to reuse compatible same-account, same-repository venues across efforts, including explicit read-only and no-build review use. (#11319; stopped venues, source/synthetic consumer evidence)
- [x] Preserve retained files, branches, configuration and claims; reject incompatible active agents and live or unknown holders rather than inferring release from local absence. (#11319; source controls, live qualification remains Phase 4)
- [x] Account for stopped-venue wake cost separately from owned slots, and retain current workstream reuse when valid. (#11319)
- [x] Connect the normal creation/dispatch consumer to the same decision; a suggested reuse candidate is not a completed borrow or successful dispatch. (#11319; ordinary create reserves, re-entrant connection/return exercised with synthetic adapters)
- [x] Prefer backward-compatible owner-internal configuration; if new CLI or JSON semantics are needed, expand receiver support first, discover selected-runtime capability before caller migration, and test mixed-version refusal and rollback. (#11306, #11319)
- [x] Update the owning README, configuration reference and lifecycle/borrow skills, then publish and steward the complete reuse flow through merge. (#11319; installed/live acceptance remains Phase 4)

### Phase 4 - Qualify release and selected consumers
- [ ] Verify promoted source inclusion without treating dev ancestry as release proof.
- [ ] Qualify or explicitly transfer generation-qualified recovery after an unpersisted shared-return ACK (#11343); native ledger retirement alone never closes this gate.
- [ ] Exercise the installed read-only admission consumer and a separately authorized existing-venue sequential-use flow; retain current owners, unknown effects and cleanup obligations.
- [ ] Resolve or transfer every validation gate to a named tracked objective before marking this effort Done.

### Phase 4a - Review then prove generation-qualified return recovery
- [x] Review the receiver-first recovery proposal below before implementation. (#11355; #11343 remains the recovery objective)
- [ ] Expand only the existing native lease receiver, advertise selected-runtime support, and preserve the legacy baseline; merge its focused parser/store/compaction proof before migrating callers.
- [ ] Capture the original generation before return and conditionally consume receiver-owned evidence through the existing return operation; unknown or unsupported evidence retains custody.
- [ ] Prove interrupted return, successor preservation, bounded evidence eviction, compaction/GC and mixed-version refusal through the real native CLI and Codespaces consumer.

## Validation Plan

- [x] Focused existing-suite regressions: below limit, stopped pool at limit with spare cores, running-core pressure, selected-account isolation and configurable ceiling.
- [x] Unknown/truncated/failed inventory and unknown wake/create cost cannot masquerade as free capacity; explicit policy override remains distinct from provider refusal.
- [x] Ordinary create and allocate agree; a synthetic provider quota race preserves bounded retry and does not broaden cleanup.
- [x] Sequential same-repository reuse preserves retained work and claims; read-only/no-build requests are supported, incompatible live agents and unknown ownership are refused. (#11319; source/synthetic evidence, not installed/live acceptance)
- [x] A stopped reusable venue is admitted only with sufficient known wake headroom; valid existing-workstream reuse remains compatible. (#11319)
- [x] Lightweight real CLI/subprocess consumer check with synthetic inventory and provider adapters proves admission/refusal without backend mutation.
- [x] Fresh isolated configuration proves defaults and overrides without requiring Worktrees, installing a runtime or contacting a provider.
- [x] Admission flow: run the existing focused tests, touched-Python lint and install-contract/required CI checks; add one changefile per touched plugin, never hand-edit versions. (#11228)
- [x] Sequential reuse flow: repeat focused tests, touched-Python lint, install-contract/required CI and the same changefile discipline before publication. (#11319)
- [ ] Run Windows and POSIX consumer checks in available owned lanes; record concrete unavailable-lane reasons rather than claiming parity from unit tests alone.
- [ ] Record selected installed/live acceptance separately from source tests and release; obtain missing effect authority before any provider mutation.

## Proposal

The Plan and Validation Plan are agent-recommended implementation sequencing.
Review this contract first. Source work must preserve truthful uncertainty and
existing resource ownership, not solve quota pressure by destructive cleanup.

### Recovery proposal for #11343

This is an agent-recommended realization of the existing safe-return intent,
not new allocation, deployment or cleanup authority. A fencing token changes
on renewal; the native record's existing `lease_id` identifies the generation
that an explicit release terminates. Persist that generation and its qualified
holder in prepared local progress before sending the return.

Expand the existing receiver first with opt-in `lease release
--retain-return-proof`, advertised as `lease-return-proof-v1` through the
selected runtime's existing version-1 readiness response. The normal syntax and
version-1 lease records remain supported. Opt-in release upgrades only that
resource to a strict version-2 lease payload containing at most 16 returned
generation/holder/time entries. Acquisition, renewal, transfer and release
preserve the entries; only a successful release appends evidence. Keep it in
the current lease payload, not a new ref, receipt store, ledger or daemon.

Existing `lease inspect` exposes the validated current payload; no new query
verb or Git-history scan is needed. Native compaction preserves the bounded
evidence in its minimal equivalent chain, so it survives intermediate-history
GC. Eviction, absence, malformed evidence and legacy records mean unknown,
never release. A successor's current state, expiry or native ledger retirement
cannot manufacture a receipt for the original generation.

Old callers keep their syntax against the expanded receiver. Older receivers
that cannot parse an opted-in version-2 resource must refuse that resource
truthfully on rollback, not overwrite or downgrade its evidence; unaffected
version-1 resources retain their baseline. Migrate Codespaces only after the
receiver merge, discover support before emitting the new flag, and retain the
existing acknowledged-return path when optional recovery support is absent.
Test both peer directions, strict typo rejection, opt-in and ordinary writes,
compaction, eviction and successor noninterference before caller publication.

## Journal

- [2026-11-07 - Admission review and consumer continuity](journal/2026/11.07%20admission-review.md)
- [2026-11-06 - Reviewed intent and initial admission](journal/2026/11.06%20initial-admission.md)
