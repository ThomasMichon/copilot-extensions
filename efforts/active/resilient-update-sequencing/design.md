# Resilient Update Design

[Effort, request and completion gate](README.md).

## Invariants

The existing install contract and installation ownership remain authoritative.
Use current lifecycle owners, immutable slots, governed dependencies and
health-gated activation. A source-path change alone is not an owner transfer,
but a familiar plugin name/header is not permission to replace another
installation's command. No new daemon, resolver or update scheduler is needed.

Fail-fast means fail an attempt promptly, not abandon convergence. Preserve
verified work and failure history. Transient contention/network interruptions
can retry with backoff; missing authority, integrity refusal or prohibited
dependencies remain explicit blockers requiring the relevant mitigation.

## Execution graph

| Stage | Contract |
|-------|----------|
| Preflight | Collect desired targets and input revisions once; inspect local prerequisites, permissions, space, explicit exclusions and migration eligibility before expensive work. |
| Acquisition | Refresh each distinct source/context once; select and verify payloads and acquire admissible inputs. Unavailable optional artifacts may use the existing authority-permitted source path; invalid bytes never authorize fallback. |
| Preparation | Build changed candidates at final immutable paths under existing claims. Schedule high-risk/long ready work first, respecting dependencies and resource budgets. Reuse only validated current candidates. |
| Readiness | Every required candidate/dependency is current or prepared and healthy; revalidate inputs and persist progress before activation. |
| Activation | Short dependency-ordered per-installation transactions publish selection, manifests, essential commands and service routes under existing health/rollback rules. |
| Local finishing | Reconcile convenience commands and render profiles/shortcuts once from stable local inputs. Compare content; avoid unchanged writes. |
| Verification/retention | Verify all required final postconditions, then reclaim only obsolete owned artifacts not needed for rollback or another live update. |

Cheap blockers come before expensive work. Cutover remains an inherently risky
late operation; preparation makes it short and recoverable, not infallible.
This design does not promise globally atomic multi-service activation.

A newer updater required to prepare peers is an explicit minimal bootstrap:
health-gated selection, re-exec and re-plan. It is not permission to activate
arbitrary peers before readiness. Existing adapters that combine construction
and activation cannot silently enter the prepare-only lane; add the split with
their canonical engine owners and retain an honest supported legacy lane.

### Timeout recovery admission

The first supported timeout-replay lane is Windows payload update, explicitly
selected before portable recovery. Use the existing process owner's required
non-breakaway Job and positive zero-active-process termination check. Reserve
its bounded cleanup grace inside the acquisition budget, reap/drain the root,
and retain refusal precedence before starting another writer. Cleanup failure
is terminal, not a confirmed timeout. A CLI exit code alone is not evidence of
an owner deadline.

POSIX session cleanup deliberately preserves detached daemons, so it cannot
license full-tree timeout replay. Install timeout replay also remains blocked
until interrupted restoration retains the original activation snapshot
durably. Those are explicit unsupported lanes, not completed convergence.
Ordinary runtime installers retain their existing daemon/breakaway behavior;
the strict process contract is opt-in for payload updates.

### Durable activation-restoration boundary

Extend the canonical `plugin-activation` state owner, not installer construction
or the preparation draft. This is a restoration protocol, not a new scheduler
or a second runtime transaction engine. The initial consumer is the Windows
updater's inventory-install path; ordinary standalone consumers retain their
current behavior until explicitly adopted and tested.

1. Serialize participating preservation operations for one resolved Copilot
   home through one canonical transaction owner. The settings and inventory
   documents are home-wide, so independent per-plugin journals cannot authorize
   overlapping recovery. An unresolved transaction blocks new participating
   installs, including another identity. Do not claim this lock excludes the
   Copilot CLI or unrelated writers that do not participate.
2. Before launching the command, durably retain the original activation
   snapshot, exact source-qualified identity, resolved state-home binding,
   transaction identity and schema/phase. Persist only restoration fields;
   no command output, credentials, environment or whole-document backup is
   needed. Reject corrupt, foreign, linked or ambiguous transaction artifacts;
   never replace them with a fresh capture of post-install activation.
3. The existing process owner retains child custody. Only positive
   non-breakaway Windows Job cleanup and root reap/drain may authorize a
   durable quiescent transition. A normal root exit, elapsed time, missing PID,
   released parent lock or CLI exit code alone is not that proof. Failure or
   interruption before publishing the witness retains the snapshot and an
   explicit unresolved-command blocker, even if cleanup probably happened.
4. After that witness is durably published, restore the original activation
   fields through the existing strict canonical restore operation. Re-read
   current documents and retain refreshed inventory and unrelated settings.
   The command must not run again while restoring either file. Keep the
   transaction until both postconditions are verified; partial writes,
   contention, cancellation and process death must retain the same snapshot.
5. A later invocation that acquires canonical custody may automatically finish
   only a validated quiescent transaction for the same state home. It must
   finish recovery before capturing another snapshot or launching a command,
   and never use PID death to promote an uncertain transaction. Unsupported
   schemas, uncertain command settlement and invalid current documents remain
   precise blockers with retained evidence, not success-shaped fallbacks.
6. This first lane does not enable install-timeout replay. A settled timeout
   may permit original-state restoration but still returns the timeout failure;
   the existing two-attempt acquisition policy must not interpret restoration
   success as installation success. POSIX cleanup and pre-witness crash
   recovery remain unsupported until their owning process contract proves
   writer settlement. No manual deletion, unconditional stale-lock reclaim or
   silent journal discard is a recovery procedure.

Before implementation, review this boundary and confirm that the selected
canonical locking/custody primitive can express it without repurposing a
runtime-installation lease for user-global Copilot state. Reuse an applicable
existing primitive; if none is applicable, surface that design decision rather
than embedding an ad hoc lock or PID-reaper in the updater.

The validation gate includes real independent processes contending for the same
home, native Job-owned child and grandchild settlement, and forced parent death
before witness publication and between the two restoration writes. A fresh
interpreter must retain the original activation tri-state, refuse uncertain
command recovery, finish witnessed restoration without replay, and preserve
refreshed inventory plus unrelated concurrent changes. Corrupt/foreign/symlinked
records, unsupported schema, denied writes, failed cleanup, repeated recovery
and transaction-clear failures must remain explicit. Unit-only simulated
exceptions cannot substitute for the cross-process interruption tests.

## First slice: historical project commands

Keep the current command owner until #1238's separate ownership transition.
Extend its existing reconciliation, locks and receipts rather than adding a
second command arbiter.

Classify each discovered command as current, attributable historical,
modified/foreign/ambiguous, or obsolete attributable historical. Establish
lineage from the existing installation/source identity, project registration,
receipt and generated-content evidence. Recognize actual historical template
families and receipt generations through regression fixtures. Match all
existing Windows siblings; never let a good `.cmd` authorize clobbering an
edited `.ps1`.

For a same-lineage predecessor, stage the intended bytes, retain recoverable
prior command/receipt state and revalidate under the existing locks before
publication. Retrying an interrupted transition must either finish the
attributable replacement or restore the prior usable command. Remove only the
exact proven obsolete files after replacement verification; knowledge-only or
unregistered projects need ownership-proven retirement without creating a new
launcher. A historical physical path is not itself durable ownership identity.

Foreign/cross-cell, edited, corrupt and ambiguous artifacts remain unchanged
and explicitly blocked. Do not solve routine historical upgrades with blanket
`--transfer`, receipt deletion or last-writer-wins. Reconciliation results
include unresolved targets; update status and exit behavior consume them.
"No files changed" is not synonymous with "in sync."

## First slice: Terminal profiles

Manager already owns `terminal_fragment.build_fragment()` and
`deploy_fragment()`. Update must use that narrow owner boundary, not invoke
`install.ps1 refresh-profiles`, copy a whole plugin payload or bounce through
multiple Manager version probes.

Preserve the saved selection, stable GUIDs, user-owned settings, generated
state reconciliation and project shortcuts. Pass one collected local input
snapshot where the existing boundary supports it. Run finishing once after
runtime work and command migration, including when versions were already
current but local integration is missing or stale. Manager absence remains an
explicit supported outcome, not an invisible success.

Avoid writes when the desired owned output already matches. A content/readiness
check remains necessary: a version match does not prove a missing fragment
exists. Native Windows tests measure the real process boundary and a second
unchanged pass. Set an explicit measured latency budget in the implementation
slice rather than claiming a speculative speedup from the enclosing 21.7-second
baseline. `terminal-fragment --live` means apply writes, not remote probing.

## Incremental rollout and coordination

1. Repair attributable command migration and accurate reconciliation outcomes.
2. Remove the profile installer detour and defer/deduplicate local finishing.
3. Introduce one collected plan and early blockers/acquisition with existing
   adapters; keep real mutation boundaries explicit.
4. Integrate prepare/validate/activate with the canonical installer owners,
   then readiness, resumability and controlled independent preparation.

#6812 owns operation/attempt timestamps, nested output framing and telemetry
folding. This effort consumes that telemetry, not a parallel event store.
#6234/#4876 own dependency reduction, admitted artifacts and reuse; #6476 and
the installer-engine effort own shared constructor/activation mechanics.
Coordinate overlap before changing shared update entry points.

## Acceptance matrix

| Scenario | Required result |
|----------|-----------------|
| Healthy unchanged installation | No rebuild; one cheap finishing pass; no unchanged output writes. |
| Known historical commands | Automatic same-lineage migration/retirement; no recurring refusal. |
| Foreign or edited command | Preserve bytes; explicit target/cause/mitigation; never "in sync." |
| Missing profile with current runtime | Repair via narrow Manager path without an installer or venv build. |
| Dependency or candidate failure | Prior runtime remains healthy; failed candidate is nonselectable; verified peers are reusable. |
| Interrupt command publication | Retry/recovery preserves receipt ownership and a usable launcher. |
| Transient acquisition/lock failure | Backoff, fresh inspection and unfinished-target retry without duplicating successful work. |
| Policy/auth/integrity refusal | Durable precise blocker; no package-source escape or false success. |
| Input drift before activation | Revalidate/re-plan instead of publishing stale candidates. |
| Late local finishing failure | Retain actual activated state and resume local work without rebuilding candidates. |
| Fresh/changed-version path | Report separate acquisition, build, activation and finishing timings. |
