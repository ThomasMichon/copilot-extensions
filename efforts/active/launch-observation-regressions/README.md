# Launch Observation Regressions

## Goal

"We need to undo whatever change is causing us to show \"MUX(?)\" in
worktree-manager. It really feels like the `has-session` bug may be
self-inflicted. We need to sweep how we interface with PSMux to ensure this
isn't a \"self-own\", and restore the correct mux status reporting"

Also investigate the reported `embody` regression and New's required-application
observation failure without bypassing admission or affecting existing sessions.

- **Slug:** `launch-observation-regressions`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed source slices targeting `dev`
- **Created:** 2026-11-09
- **Status:** Draft
- **Vision:** [Picker](../../../visions/picker/README.md);
  [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11722
- **Related owners:** #11564 finite mux observation; #11718 legacy PID-sentinel compatibility

## Guiding Intent

Audit this suite's own behavior before attributing failures to PSMux. Restore
actual observations, not optimistic badges. Keep New's required readiness,
selected-runtime compatibility and finite admission budget intact.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source maintainer | Audit, bounded correction, validation and PR stewardship | This effort's source worktree and reviewed PRs |

## Coordination

- **Topology:** One owner, sequential reviewed source slices.
- **Host (owns PRs):** Source maintainer.
- **Delegates:** None.
- **Handoff:** This canonical effort and its remaining acceptance items.
- Do not duplicate #11718's independently owned legacy registration correction.

## Context

- #9155 changed architecture documentation, not executable `embody` behavior.
- An installed Worktrees generation explicitly returns no Windows listing;
  its display adapter therefore reports unknown. #11564 already replaces that
  producer with finite native observation. A merged fix is not installed proof.
- The selected Machines runtime declares required read-only observation support,
  and profile selection succeeds. Discovery alone exceeded a bounded 50-second
  diagnostic; a separate bounded stack capture located full-payload hashing in
  `installation_repair_admission.payload_identity`. Application checks had not run.
- The New caller currently conflates timeouts, selection failures and other
  observation errors into an unavailable-or-unsupported message.

## Request

The Goal preserves the mux request verbatim. The other requests were to
investigate `embody` and this New failure:

> New authoring requires application readiness before allocation. Required
> application observation is unavailable or unsupported by the selected runtime.

Private invocation paths and an unrelated quoted task seed are omitted. The
seed is diagnostic input, not authorization for the action it describes.

## Scope Approvals

Source diagnosis, bounded isolated regression tests and ordinary reviewed
publication are in scope. Existing operational holds remain: no shared-host
PSMux probes, live embodiment/launch, update/install, restart, teardown,
registration reconstruction, service changes, access changes or release of
retained resources. Source completion does not claim installed recovery.

## Plan

### Phase 1 - Review the correction
- [ ] Land this proposal through the normal review and CI gate.

### Phase 2 - Audit and repair
- [ ] Audit mux callers' targets, arguments, environment/CWD, observation provenance, budgets and cleanup; identify installed-versus-source differences and preserve independent owners.
- [ ] Remove redundant readiness-discovery filesystem work without weakening payload identity, linked-path refusal, selected-owner qualification or the existing admission deadline.
- [ ] Make readiness failures identify their stage and timeout versus unsupported selection without exposing raw output or changing refusal semantics.

### Phase 3 - Deliver and close
- [ ] Land the bounded correction after passing review and required CI.
- [ ] Record source results and any separately owned operational acceptance; archive this effort only after every item is resolved or transferred to a named tracker.

## Validation Plan

- [ ] Prove the payload digest remains content-sensitive and deterministic, and linked/reparse/nonregular/oversized inputs still refuse.
- [ ] Add a deterministic work-count regression for repeated ancestor inspection; use representative real-filesystem evidence to show discovery fits the original budget.
- [ ] Exercise the actual readiness caller boundary with synthetic, isolated owners: supported success, timeout and unsupported selection all preserve admission.
- [ ] Verify mux consumer truthfulness and caller contracts with focused existing tests, without shared-host probes or pretending a source test proves rollout.
- [ ] Record unit, simple integration, clean-room and available external/native tiers, with explicit skipped-tier reasons; preserve unrelated sessions and resources.

## Proposal

Agent-recommended implementation direction: avoid repeated ancestor traversal
within one bounded payload walk, retaining content hashing and existing safety
properties. Do not solve latency by increasing the admission timeout, disabling
required readiness, adding a resident cache or silently trusting a newer payload
instead of the selected runtime.

This closes existing responsiveness and truthful-observation intent rather
than extending a vision. The service-contract and performance-measurement
patterns govern the implementation. No new CLI or wire generation is proposed;
current/prior providers retain the same required observation contract. Runtime
replacement and rollout remain with their existing owners.

## Journal

### 2026-11-09 - Diagnosis and proposal
- Separated intentional installed mux-listing suppression from a PSMux failure.
- Isolated profile selection and selected runtime support successfully.
- Discovery-only timeout and stack capture identify the next correction stage;
  no application checks, live mux probes or installation effects were performed.
