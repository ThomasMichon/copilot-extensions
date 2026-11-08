# Owned Codespace Capacity

- **Slug:** `owned-codespace-capacity`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed proposal and implementation PRs
- **Created:** 2026-11-06
- **Status:** Draft
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

The existing pool exposes both member count and running-core headroom, but
allocation currently consults only the latter. Cross-account listing merges
members and can retain partial results after one account fails; that is not a
complete selected-account count. A stopped venue still consumes an owned slot.
The existing per-workstream reuse path does not cover sequential cross-effort
reuse. Existing reclaim paths protect live holders and rescue work; capacity
pressure must not relax those protections.

The proposal extends the vision's independent-capacity and sequential-reuse
intent. It follows the existing configuration layering and optional owner
composition contracts, not the unwired full-publication inventory foundation.
Runtime realization is pending the proposal's review and merge.

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
- [ ] Merge this vision clarification and proposal through the normal review/CI gate before runtime implementation.
- [ ] Confirm the existing selected-account/configuration and borrow/return seams; retain other owners and define actionable unknown-capacity outcomes.

### Phase 2 - Prove independent capacity admission
- [ ] Extend native configuration and the existing pool decision with a configurable owned-count policy (conservative default 30), independent running-core accounting and selected-account scope.
- [ ] Require complete selected-account inventory for creation admission; distinguish valid empty results, partial/truncated inventory, authentication failures and unknown machine cost.
- [ ] Keep `allocate`, `pool`, ordinary `create` and their presenters consistent: a full stopped pool cannot yield blind creation, and stopping alone cannot free an owned slot.
- [ ] Preserve explicit `--force-create` as an intentional policy override with truthful diagnostics; it never supplies access, changes provider limits or permits broader reclaim.
- [ ] Preserve provider-side quota refusal and bounded existing retry/reclaim semantics after concurrent capacity changes; no local snapshot promises a global reservation.
- [ ] Publish the first complete admission flow after focused checks, then steward it through merge.

### Phase 3 - Prove safe sequential reuse
- [ ] Extend existing suitability/admission and borrow/return paths to reuse compatible same-account, same-repository venues across efforts, including explicit read-only and no-build review use.
- [ ] Preserve retained files, branches, configuration and claims; reject incompatible active agents and live or unknown holders rather than inferring release from local absence.
- [ ] Account for stopped-venue wake cost separately from owned slots, and retain current workstream reuse when valid.
- [ ] Connect the normal creation/dispatch consumer to the same decision; a suggested reuse candidate is not a completed borrow or successful dispatch.
- [ ] Prefer backward-compatible owner-internal configuration; if new CLI or JSON semantics are needed, expand receiver support first, discover selected-runtime capability before caller migration, and test mixed-version refusal and rollback.
- [ ] Update the owning README, configuration reference and lifecycle/borrow skills, then publish and steward the complete reuse flow through merge.

### Phase 4 - Qualify release and selected consumers
- [ ] Verify promoted source inclusion without treating dev ancestry as release proof.
- [ ] Exercise the installed read-only admission consumer and a separately authorized existing-venue sequential-use flow; retain current owners, unknown effects and cleanup obligations.
- [ ] Resolve or transfer every validation gate to a named tracked objective before marking this effort Done.

## Validation Plan

- [ ] Focused existing-suite regressions: below limit, stopped pool at limit with spare cores, running-core pressure, selected-account isolation and configurable ceiling.
- [ ] Unknown/truncated/failed inventory and unknown wake/create cost cannot masquerade as free capacity; explicit policy override remains distinct from provider refusal.
- [ ] Ordinary create and allocate agree; a synthetic provider quota race preserves bounded retry and does not broaden cleanup.
- [ ] Sequential same-repository reuse preserves retained work and claims; read-only/no-build requests are supported, incompatible live agents and unknown ownership are refused.
- [ ] A stopped reusable venue is admitted only with sufficient known wake headroom; valid existing-workstream reuse remains compatible.
- [ ] Lightweight real CLI/subprocess consumer check with synthetic inventory and provider adapters proves admission/refusal without backend mutation.
- [ ] Fresh isolated configuration proves defaults and overrides without requiring Worktrees, installing a runtime or contacting a provider.
- [ ] Run the existing focused tests, touched-Python lint and install-contract/required CI checks; add one changefile per touched plugin, never hand-edit versions.
- [ ] Run Windows and POSIX consumer checks in available owned lanes; record concrete unavailable-lane reasons rather than claiming parity from unit tests alone.
- [ ] Record selected installed/live acceptance separately from source tests and release; obtain missing effect authority before any provider mutation.

## Proposal

The Plan and Validation Plan are agent-recommended implementation sequencing.
Review this contract first. Source work must preserve truthful uncertainty and
existing resource ownership, not solve quota pressure by destructive cleanup.

## Journal

### 2026-11-06 - Proposal
- Claimed the bounded admission/reuse slice of #4187 after issue/PR deduplication.
- Reconciled independent owned-count accounting and sequential reuse to the existing vision and source contracts; runtime implementation has not started.
