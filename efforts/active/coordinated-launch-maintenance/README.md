# Coordinated Launch Maintenance

- **Slug:** `coordinated-launch-maintenance`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-slice PRs against `dev`
- **Created:** 2026-10-21
- **Status:** Active
- **Vision:** `visions/installer/README.md` (adopted out-of-band automatic maintenance); `visions/picker/README.md` (responsive session launch)
- **Umbrella issue:** #7694

## Guiding Intent

An already-healthy session must not wait for unrelated runtime installation.
Keep adopted automatic maintenance available, coordinated and observable rather
than disabling updates or starting an independent updater for every launch.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Launch driver | Design, implementation, review, release and qualified acceptance | Managed source `welded-circuit`; #7694 |
| Broker repair | Follow-up #6859 bug and native qualification prerequisite | Managed source `whirring-doohickey`; same launch driver |
| Broker design | Source-only lifecycle amendment and review | Managed source `tinkling-cylinder`; same launch driver |

## Coordination

- **Topology:** sequential source PRs against `dev`.
- **Host (owns PRs):** Launch driver.
- **Delegates:** none.
- **Handoff:** canonical effort and #7694; no silent ownership transfer.
- #6812 retains formatting, telemetry and operation-cost audit ownership.
- #6820 retains historical-command migration and its paused constructor handshake.
- #7667 retains its contributor-owned installer/context implementation.
- #6237/#3336 retain release control. This effort does not authorize retries,
  installed mutation, service restarts or another concurrent updater.

## Context

The launch scripts stage a marketplace download but join it before handoff,
then run bootstrap maintenance and two plugin-reconcile passes synchronously.
Only one core installer branch is detached. Several healthy launches can
therefore wait behind slow installation; a detached child alone does not prove
coalescence, distinct-project delivery or survival of caller Job cleanup.

This closes the installer's existing adopted, out-of-band maintenance intent,
using the process-lifetime and operation-custody patterns. The original
no-new-service mechanism proved insufficient for the Windows lifetime guarantee.
The source-only broker amendment below supersedes that mechanism without
changing dependency policy or authorizing installed mutation.

**Current prerequisite:** the launch driver owns the follow-up #6859 bug.
#6918 is merged, and historical authorship is not an active implementation
hold. Native qualification exposed a check that confused any ambient Windows
Job with the caller's cleanup Job. The candidate repair retains authenticated
identities, caller-membership refusal and confirmed cleanup. Its real consumer
survives closing two nested caller Jobs, but review identified an unproven
topology: a candidate can share an outer cleanup Job while having a different
immediate Job. The immediate-Job query cannot attest escape from that ancestor.
The repair is held as a draft; no lifetime-check waiver or successful adoption
is authorized. The operator approved source-level redesign around an
independently managed Windows launch broker, with deployment separately gated.
That supersedes the initial no-new-service mechanism, not the responsive-launch
intent. A reviewed design amendment precedes broker implementation. Review also
requires deadline checks during census,
retryable descendant-handle custody on failed cleanup, and deterministic PID
fixtures. Review, merge, containing-source requalification and rollout remain
open. No installed patch or host service change is implied.

## Request

Public-safe literal excerpt: "Installation is just dragging out and blocking
other worktree starts".

Settled direction (summary): use coordinated-background automatic maintenance,
not permanent update opt-outs; preserve the running installation and do not
start another updater as an investigative shortcut.

Confirmed effort slug: `coordinated-launch-maintenance`.

Subsequent operator direction (verbatim):

> 6918 is merged. 6859 is a bug; if we need it fixed, we should fix it

Design-scope form selection (verbatim): `source-redesign`.
The selected option was "Authorize the source-level broker redesign; keep
deployment separately gated". This authorizes source design and implementation,
not installation, task/service registration, a host update or live rollout.

## Plan

### Phase 1 - Reviewed contract
- [x] Land this plan through ordinary review before implementation.
- [x] Coordinate the bounded scope on #6812/#6820 without taking over their owners.

### Phase 2 - Coordinated maintenance

The following implementation choices are **agent-recommended** mechanisms for
the requested coordinated-background behavior.

- [ ] Review the [independent Windows broker amendment](windows-launch-broker.md)
  before implementing its lifecycle and consumer integration. No deployment is
  implied.
- [ ] Repair the #6859 follow-up in owned source, then requalify the containing
  launch change. _(Operator-directed prerequisite; implementation mechanism is
  agent-recommended.)_
- [ ] Move optional stage/bootstrap/reconcile work off the healthy handoff path
  on both PowerShell and Bash; remove the join and inline-download fallback
  from ordinary launches. Preserve explicit blocking recovery behavior.
- [ ] Use one bounded on-demand owner and durable pending requests. Coalesce
  equivalent requests without losing a different project, repository, source,
  installation context or stronger requested operation.
- [ ] Share operation custody with explicit update/reconcile entrypoints;
  retain canonical installer locks and execution admission. A busy owner is
  pending work, not permission for another installer.
- [ ] Preserve exact action argv, scoped environment/unsets and project
  context. Re-plan against the current installed state rather than execute a
  stale or foreign action. Keep runtime replacement and retention safety.
- [ ] Use the canonical headless lifetime primitive, explicit logs and durable
  working directory. No inherited-Job fallback presented as durable success;
  preserve contained-test suppression and bounded child-tree cleanup.
- [ ] Make queued/running/failed/completed outcomes inspectable and truthful.
  Interrupted work must not be reported complete or retried over a possibly
  surviving child without ownership/completion evidence.
- [ ] Preserve `WORKTREE_NO_UPDATE` and `WORKTREE_NO_RECONCILE` independently,
  explicit update behavior, clean JSON/stdout and required first-use safety.
  Missing prerequisites cannot be called a healthy launch merely to avoid waiting.

### Phase 3 - Release and acceptance
- [ ] Land source, documentation and changefiles through review and required CI.
- [ ] Observe legitimate promotion, then use the authorized normal rollout
  owner; do not start a competing update to finish this effort.
- [ ] Record installed handoff, maintenance completion and failure evidence.
  Close this effort only when every validation item is resolved or explicitly
  transferred to a named tracked objective.

## Validation Plan

All detailed scenarios below are **agent-recommended** acceptance checks.

- [ ] Unit tests cover queue equivalence, distinct contexts, stronger requests,
  owner contention, crash/ambiguous-child handling and truthful terminal states.
- [ ] Real shell fixture holds an installer behind a gate while two healthy
  launches reach handoff before that gate opens; maintenance remains automatic
  and single-owned. Measure handoff independently of installer completion.
- [ ] Prove a different project's pending request is eventually processed,
  and manual update/reconcile cannot overlap its mutation boundary.
- [ ] Exercise exact argv/environment, admission refusal, malformed plans,
  failed spawn, failed installer, opt-outs and explicit blocking mode.
- [ ] Prove first-use/missing-prerequisite handling in an isolated fresh home.
- [ ] On Windows, exercise the actual production worker spawn from a
  kill-on-close Job, close caller containment after acknowledged dispatch,
  and independently observe continued maintenance. Verify bounded-child
  control reaping, no visible console/focus change, and owned-tree cleanup.
- [ ] Exercise native POSIX handoff, owner exit and pending-request delivery.
- [ ] Run targeted contained suites, lint, install-contract and source guards;
  record native/live lanes separately from mocked wiring.
- [ ] Verify a promoted installed release through the normal authorized flow;
  a payload version or queued receipt alone is not installed/running proof.

## Proposal

The initial no-new-service mechanism is superseded only for the Windows
independent-lifetime broker by the explicit source-redesign authorization.
No periodic updater or new maintenance scheduler is proposed. The launcher submits optional
work to its installed owner's bounded coordinated maintenance path and proceeds
on the healthy retained runtime. The owner drains admitted requests using the
existing planners/installers and then exits. Coordination must cover explicit
mutation entrypoints as well as launch requests; a lock around only the new
worker is insufficient. Machine-local request/status/log data never enters
repository history.

## Journal

### 2026-10-21 - Kickoff

- Traced foreground stage join, bootstrap and two-pass reconciliation on both
  shells. Existing progress work did not create the serial maintenance path.
- Claimed #7694 and created isolated source `welded-circuit`; no installed
  updater, running launch, service or registry was changed.
- Confirmed the effort slug. Compared the captured request and settled
  direction with the operator input; implementation/test mechanisms are
  explicitly marked agent-recommended.

### 2026-10-21 - Reviewed contract

- #7716 merged after exact-head approving review and successful required CI.
  Synced the managed source onto the reviewed plan before implementation.
- Posted the bounded ownership notice on #6812 and #6820. Their retained
  scopes and #7667's contributor-owned implementation remain unchanged.
- The initially assigned backend worker did not survive the session
  interruption and left no source changes; the launch driver retains and
  directly resumes that implementation. No production maintenance was started.

### 2026-10-21 - Source implementation and qualification blocker

- Implemented durable optional requests, equivalent-request coalescence,
  distinct project/selector delivery and operation-scoped mutation custody.
  Different inherited feed/auth/package-policy environments retain separate
  workers; only their digests enter the queue. Scope workers wait for shared
  custody and release their empty-queue ownership transactionally.
- Removed the shell stage join, inline-download retry and shell-owned
  detached installer. Both shells submit through the same engine owner;
  blocking recovery also uses that custody. Explicit update, marketplace
  stage and applied reconciliation share the mutation boundary.
- Preserved canonical planner/installer admission, action environment/unsets
  and installer deadlines. Nonzero acquisition, diagnostic-only refusal and
  failed actions cannot produce successful completion. An interrupted mutation
  remains blocked without a positive completion witness.
- Targeted contained backend and native shell contracts passed. The real
  process fixture proves two handoffs before a gated installer completes,
  single mutation ownership, eventual distinct-project delivery and separate
  inherited-environment delivery. Existing apply-plan consumers passed.
  Two unrelated smoke fixtures initially omitted required installer siblings;
  repaired only their fixture assembly and confirmed the native smoke paths.
- Linux dependency preparation did not finish within its unchanged bound;
  the new test environment had no pytest or runtime packages. The stdlib-only
  native fixture then ran successfully under the existing bounded supervisor
  from a disposable native-filesystem source snapshot: both real process
  contracts passed. This does not qualify the unfinished full dependency graph.
- Windows caller-Job qualification exposed and corrected a local integration
  issue: use canonical `direct_python` plus its venv environment before
  `spawn_daemon`, rather than hand the broker a GUI interpreter or redirector.
- The strict no-breakaway caller still fails at shared broker
  `bootstrap-identity`. Bounded diagnostic instrumentation proves PID and
  cookie match, but `_outside_job(bootstrap)` is false. The shared primitive
  refuses startup with confirmed cleanup; no inherited-lifetime fallback,
  policy weakening or host remedy was attempted. #6859 retains this
  prerequisite's original owner. The effort remains Blocked, not Done.
- Reported the failed predicate to #6859's retained owner. Native fixture
  cleanup now waits on each observed worker's exit, not merely the last
  database completion row; that closes a real Windows log-handle cleanup race.
- Final contained backend execution passed with the production-survival lane
  explicitly suppressed under containment. Native Linux Python fixtures and
  eight native Bash handoff subcases passed from disposable native-filesystem
  snapshots using the existing bounded supervisor and standard-library runners.
  Native Windows handoff exercised sixteen subcases with the production log
  helpers. One combined progress group encountered an unexplained twenty-second
  formatter pipe-completion timeout; both exact formatter nodes passed in
  isolation at the unchanged budget. This is not an accepted flake exemption
  or a claim that the entire combined group passed.
- Reconciled the moving Dev base with a backup ref, preserving #7727's Bash
  fallback diagnostics and #7736's marker normalization plus retained formatter
  tests. The exact rebased Windows group then passed: nineteen tests and
  sixteen handoff subcases, with seven native-POSIX skips. Native Bash's eight
  subcases also passed against the rebased source. No deadline was raised or
  failed check bypassed. The shared broker blob is unchanged; #6859 remains
  the substantive native acceptance blocker.

### 2026-10-21 - Own the follow-up bug

- Captured and read back the operator's explicit direction above: merged
  implementation #6918 does not create a standing ownership hold on bug #6859.
  No open broker PR was found. Claimed the follow-up under source
  `whirring-doohickey` rather than waiting for an unverified active owner.
- Native observation found ambient Jobs distinct from the caller's cleanup
  scope. Merely adding breakaway flags did not fix the failure; those
  speculative flag changes were removed. The candidate instead queries complete
  caller-Job membership and retains both pre-staging bootstrap and pre-resume
  resident checks, identity admission and fail-closed query handling.
- Focused protocol/error tests passed, and the actual containing consumer
  completed two strict caller-Job cleanup cycles with the candidate library.
  Shared-consumer validation, review and publication still remain open.

### 2026-10-22 - Review exposes the ancestor-Job proof boundary

- Published the broker candidate for review without installing or activating it.
  The original contained shared suite passed 416 tests; the separate production
  Windows lane passed eight tests, and the real maintenance consumer completed
  two cycles after closing both nested caller Jobs.
- Pulled the repair forward after #7811 merged. Retained its canonical process
  library consolidation rather than restoring the removed Worktrees vendor.
  The composed shared suite passed 452 tests with 30 platform/lane skips.
  Two runs encountered fixture-image deletion errors after passing assertions;
  exact retained fixture roots were subsequently retired without killing
  unrelated processes. A focused native run and an unchanged full canonical
  runner then passed including sandbox retirement. No failure waiver was used.
- Review found that the passing nested-Job fixture proves the exercised
  topology, not every ancestor-sharing topology. Windows documents that
  `QueryInformationJobObject(NULL)` queries only the immediate Job. A candidate
  sharing an outer cleanup Job but not the caller's inner Job could pass the
  candidate check and still die when the outer Job closes.
- Held the repair as a draft and kept this effort blocked rather than weaken
  the lifetime contract. An independently managed Windows launch broker would
  change the no-new-service design and requires operator direction. The other
  review findings remain owned with the same repair, not waived.
- The merged execution driver under #7758 now owns installation execution.
  The launch slice must integrate with that canonical owner instead of retain
  its own competing mutation custody. Posted coordination on the driver change;
  no rollout or additional updater was requested.

### 2026-10-22 - Source-only broker redesign authorized

- Asked for a design-scope decision rather than infer service authority from the
  original bug-fix request. The operator selected `source-redesign`, approving
  the source-level independent broker option with deployment separately gated.
  Read the selection back against the offered option and preserved that limit.
- Resume design work: reconcile the lifecycle patterns and standing service
  vision, then review the amended contract before implementation. All broker
  topology, lifecycle and transport choices remain agent-recommended mechanisms.
- Do not register or start a host task/service, install a candidate, restart
  shared services, or launch a competing updater under this authorization.
