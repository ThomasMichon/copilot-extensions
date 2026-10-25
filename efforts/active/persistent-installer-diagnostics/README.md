# Persistent Installer Diagnostics

- **Slug:** `persistent-installer-diagnostics`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed proposal, independent diagnostics and qualified broker slices against `dev`
- **Created:** 2026-10-23
- **Status:** Draft
- **Vision:** [Installer & Configurator](../../../visions/installer/README.md): independently installable plugins and truthful installation/update diagnostics
- **Umbrella issue:** #8370
- **Related issues:** #6812, #7743, #6859

## Guiding Intent

An installer must retain discoverable diagnostics even when its parent's
standard streams disappear. A failed, interrupted or timed-out orchestrated
attempt must identify its installer logs without depending on successful live
output forwarding. Logging must not introduce pipe deadlocks, alter native
results, disclose credentials or become another installation authority.

The diagnostics repair proceeds independently of the shared daemon-broker
replacement. An unavailable prior owner must not block necessary continuation,
but neither that unavailability nor diagnostic success waives native lifetime,
ownership, cleanup, review or deployment requirements.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `steady-anvil` | Integration, reviewed plan, diagnostics publication and completion | Managed source worktree |
| Logging worker | Native logging helpers, reached child boundaries and focused regressions after plan admission | Coordinator-owned source slice |
| Broker investigator | Reusable finite-broker evidence and a separately admitted continuation design | Coordinator-owned evidence slice |
| Existing observability and recovery owners | Retain unrelated #6812 and #7743 work | Existing issue threads |

## Coordination

- **Topology:** review this proposal first; diagnostics and broker qualification
  use independently reviewable slices. Shared consumer integration is serialized.
- **Host (owns PRs):** `steady-anvil`.
- **Delegates:** logging implementation is admitted only after this plan lands;
  broker investigation is read-only until its bounded continuation is admitted.
- **Handoff:** retain this complete objective, current slice, exact source/native
  evidence and every deployment obligation. One merged slice is not completion.
- **Unavailable owner:** the operator explicitly permits necessary continuation
  or reimplementation of #6859 without waiting for its prior owner. Held #7833
  remains held; its source is reference material, not permission to merge or
  deploy an unqualified candidate.

## Context

The reproduced Windows path loses output at both a Manager-to-engine handoff
and a retained runtime-publication child: a no-window child receives no
explicit stdout/stderr destinations. A parent request log alone therefore
cannot establish complete installer diagnostics.

The current native installation also encountered a distinct daemon lifetime
failure. Its WMI bootstrap authenticated successfully but remained in a Job,
so the shared broker refused durable success. A pre-spawn cutover breadcrumb
then left installer settlement waiting for recovery that requires an absent
child handle. These are separately recorded on #6859 and #7743; logging does
not itself repair lifetime or settle uncertain publication.

This effort closes installer observability intent and extends the existing
install contract with non-authoritative pre-admission diagnostics. It preserves
standalone installation, canonical native entrypoints, immutable runtime
publication and existing execution/cutover ownership.

## Request

The operator requested the following. The private control-repository identifier
is redacted; no surrounding requirement is omitted:

> Can we fix this by having the installers always log to some location? Then at
> least at the end of an orchestraed attempt, we can always find the logs to
> capture the error that way. Be careful around stdio piping that you don't
> cause deadlocks. Let's also avoid blocking in upstream work. Identify which
> [private control-repository] worktrees own those PRs; ignore 6859 as its owner
> isn't avaiable to drive it anymore; reimplement it if needed

The operator confirmed this effort's slug and these policy choices:

- "Use a private per-user bootstrap diagnostics area, marked unattributed;
  link it after installation validation"
- "Retain completed attempts for 30 days; never prune active/incomplete attempts"
- "Continue with an explicit warning and report missing diagnostics"

The explanation accepted for installation attribution was verification of the
specific installed runtime's identity, ownership and log path, not trust in a
source checkout.

## Plan

Implementation decomposition and mechanisms below are **agent-recommended**;
the operator requirements and selected policies above remain authoritative.

### Phase 1 - Reviewed contract and ownership reconciliation

- [ ] Land this proposal through normal current-head review and required CI
  before implementation.
- [ ] Trace relevant PR source attribution and creator relationships to their
  control worktrees; distinguish verified ownership from unavailable evidence.
- [ ] Record the independent diagnostics claim and bounded broker continuation,
  preserving unrelated active owners and existing qualification holds.

### Phase 2 - Installer-owned durable diagnostics

- [ ] Initialize private, unique attempt diagnostics before admission where the
  native entrypoint can execute; mark early records unattributed and associate
  them only after installation validation.
- [ ] Reuse canonical native packaging/materialization and diagnostic/redaction
  conventions rather than introducing a new service, broker or whole-script
  supervisor.
- [ ] Give staged and retained installer children their own discoverable writer
  records and explicit output destinations, correlated with the owning attempt.
- [ ] Preserve output independently of optional live forwarding. Drain both
  channels safely and bound post-child draining by written bytes, not EOF from
  retained descendants.
- [ ] Keep sanitized archival diagnostics distinct from access-restricted
  transient raw capture. Do not log environment, credentials or authority
  witnesses; preserve protocol stdout and exact native outcomes.
- [ ] Expose attempt/log references and missing-diagnostics status through
  existing orchestrated events/results, including failure and timeout.
- [ ] Retain completed attempts for 30 days, never prune active/incomplete
  attempts, and warn explicitly rather than blocking installation when logging
  is unavailable.

### Phase 3 - Independent source delivery and exact consumer acceptance

- [ ] Update authoritative install/diagnostic documentation, declarations,
  generated consumers and every affected plugin's changefile.
- [ ] Exercise the focused unit/native validation below and record exact tiers,
  skipped lanes and reasons.
- [ ] Land diagnostics source through current-head review and required CI
  without waiting for the broker replacement.
- [ ] Observe actual promotion and use the supported, separately authorized
  update flow for installed diagnostics acceptance. A payload refresh, selected
  candidate or source merge is not serving convergence.

### Phase 4 - Necessary broker and settlement continuation

- [ ] Independently review the reusable #7833 finite-broker components and
  isolate the actual current native activation failure before implementation.
  Do not import immediate-Job-only lifetime checks or historical-pass claims.
- [ ] If required for the authorized update/monitor acceptance, continue or
  reimplement the shared repair without waiting for the unavailable owner,
  first qualifying the Worktrees status-monitor consumer through its canonical
  installer.
- [ ] Preserve and report the primary spawn/publication error; distinguish
  positively confirmed no-launch from uncertain partial effects and settle
  through the existing owner with fresh routing/predecessor evidence.
- [ ] Require real native lifetime, cleanup, adoption, rollback and interrupted
  publication proof before source completion or rollout. Do not clear a
  breadcrumb, weaken a hold or substitute a contained daemon.

### Phase 5 - Conclusion

- [ ] Resolve every Plan and Validation item, or transfer it explicitly to a
  named tracked objective without silently declaring the parent work complete.
- [ ] Journal actual source/release/installed outcomes, archive the effort and
  finalize its source worktree through the native owner.

## Validation Plan

- [ ] Early admission/refusal and standalone cold paths produce discoverable
  logs without a Manager, service or newly installed Python prerequisite.
- [ ] Missing parent streams, staging and retained publication children retain
  their own diagnostics and exact nonzero exit results.
- [ ] Real stdout/stderr-heavy children and retained-descriptor descendants do
  not deadlock or require an unbounded EOF wait.
- [ ] Split credentials, Unicode and protocol JSON controls preserve redaction,
  useful error text and clean stdout.
- [ ] Concurrent attempts, ordinary-file/private-path checks, storage failure
  and missing announcements expose truthful discovery/degraded status.
- [ ] Timeout, interruption and abrupt-exit evidence survives; retention removes
  only completed attempts older than 30 days, never active/incomplete attempts.
- [ ] Canonical generators, materialization/declaration checks, focused tests,
  lint and install/resource contracts pass without expanding required CI into a
  subprocess-heavy matrix.
- [ ] Exercise supported native Windows and POSIX consumer boundaries, with
  genuine first-touch/external lanes where available and explicit reasons for
  unavailable tiers.
- [ ] Broker continuation, when necessary, proves two resident cycles survive
  caller/outer-Job closure and finite broker exit, rejects shared-outer negative
  controls, retains ambiguous cleanup and exercises actual installer adoption,
  recovery, rollback and removal.
- [ ] Record exact installed generation and serving/cutover evidence separately
  from source fixtures, source merge, release and human terminal acceptance.

## Proposal

Use the existing native installer entrypoints and supervisors to establish
installer-owned attempt records, private capture and sanitized archival
diagnostics. Make live presentation a secondary consumer, not the only
retention path. Extend existing orchestrated results with durable references.
Keep necessary lifetime/publication repair as an independently qualified slice.

## Journal

### 2026-10-25 - Complementary registered-origin timeout capture

- Admitted a bounded consumer repair under the existing diagnostics intent and
  #8370/#8826/#6812 coordination. It does not replace the canonical installer
  attempt producer, shared formatter or native watchdog owners.
- A contained Windows reproduction established that registered-origin acquisition
  settled its owned Job but closed its temporary stdout/stderr captures before
  attaching them to the timeout. Error formatting then discarded the child-stage
  evidence by rendering only the exception's command/timeout representation.
- Retain bounded capture only after confirmed settlement and pass both channels
  through the existing diagnostic redactor. Preserve timeout classification,
  cleanup-failure custody, source revalidation, retry ceilings and deadlines.
- Focused Windows qualification passed 92 tests with four POSIX-only skips,
  including actual native capture, Unicode/byte limits, delayed descendants,
  cleanup-failure precedence and the registered-origin formatter consumer.
  An earlier run detected machine-environment drift and failed its host-state
  audit; no rollback or guard suppression was applied, and guarded revalidation
  passed. The unrelated installer fixture mismatches remain with #8826.
- Source/native qualification, review, merge, release and separately authorized
  rollout remain distinct gates. This slice does not claim to repair an underlying
  native CLI stall or complete a whole-suite update.

### 2026-10-23 - Request, policy and bounded source setup

- Confirmed the slug and the operator's early-location, retention and degraded
  logging policies.
- Created the isolated source workspace and #8370 coordination issue.
- Source investigation identified canonical telemetry, native installer assets
  and existing private-capture contracts; implementation has not started.
- The previous broker owner is explicitly not a dependency. Native lifetime
  correctness and the held candidate's unresolved qualification remain gates.
- Merged #8373 independently supplied the historical Git-evidence preparation
  needed by required CI. The operator approved dropping this proposal's
  redundant CI repair and building on that upstream fix instead.
