# Phase 1 staged producer enrollment

Parent objective: [Per-worktree activity state](README.md).
This is an accepted agent-recommended rollout amendment. It realizes the
parent's existing single-write-authority and graceful-cutover intent; it
does not add a service, replace the monitor's control plane, or complete
Phase 1 by itself.

## Why enrollment is a prerequisite

The new global/trace/record lock order fences only code that participates
in that protocol. Already-running older producers can still append
journal-only facts after a backfill snapshot. The existing monitor drain
covers its sweep and accepted requests, not independent CLI processes.

There is also a pre-spawn window: a launcher can resolve an old runtime,
pause before spawning its child, and use the old interpreter after an
executable census reports no old child. Current launcher-root registration
occurs after initial resolution and does not stamp a generation.
Re-resolution on each call narrows that window but does not synchronize it.

Adding enrollment to new code cannot retroactively enroll a process already
running old code. The first enrollment deployment is therefore a bootstrap
stage, never proof that all producers are migrated.

## Ordered rollout

### 1. Review this amendment

Land this plan-only amendment before changing the producer boundary. Keep
the existing local lifecycle/backfill groundwork intact and unpublished.
The subsequent implementation must reconcile against current `dev` and
concurrent work before publication.

### 2. Ship enrollment with readiness disabled

- Inventory every supported lifecycle producer entry point, including
  one-shot CLI invocations, hooks, resident requests/sweeps, launcher
  helpers, and child invocations that retain a resolved interpreter.
  Scope enrollment to the owning installation cell, not an ambient global
  plugin name.
- Enroll each runtime-resolution root **before** it can read or cache a
  runtime generation. Hold a generation-scoped lease until every use of
  that resolution and its spawned producers has completed. A long-lived
  launcher must not release its lease while it can still use a cached
  interpreter; changing its generation stamp alone does not drain its
  outstanding children.
- Serialize enrollment/resolution with installer generation transitions.
  Registration after resolution, a PID-only marker, a process census
  alone, and a fixed grace-period sleep are not valid replacements.
  PID reuse must not inherit another process's enrollment or drain state.
- Retain the existing graceful monitor cutover for the responsibilities
  it already owns. Enrollment does not authorize forceful process reaping
  or a manual monitor restart as the migration mechanism.
- Preserve legacy readers and `lifecycle_slots_version == 0` throughout
  bootstrap. Do not ship Phase 2 slot-trusting readers yet.

### 3. Establish bootstrap coverage and wait for natural drain

- Account explicitly for pre-enrollment roots, including an old launcher
  paused before its existing `register-launch` call and a root holding
  an interpreter whose child does not yet exist. Absence from the new
  registry is **unknown coverage**, not evidence that it drained.
- Establish a complete, durable boundary for the supported producer
  inventory. Discoverable legacy roots keep their original process
  identity and remain blockers until they naturally exit or complete a
  demonstrated cooperative migration that drains their old uses.
- Any root class that cannot be accounted for, unreadable identity, or
  incomplete enumeration keeps activation blocked with a specific reason.
  Merely observing an empty executable census twice is not coverage.
  The implementation must prove how an old pre-resolution root cannot
  escape this bootstrap boundary; if no such proof is available, record
  the remaining blocker and seek an explicit boundary decision rather
  than silently substituting a restart or declaring enrollment complete.
- Persist bootstrap/generation progress across installer interruption and
  retry. Do not reinterpret an absent marker, daemon restart, registry
  cleanup, or changed current-version pointer as completed bootstrap.
  Waiting is non-destructive and observable; it must not block the
  installer's foreground command indefinitely.

### 4. Ship verified backfill activation

- Only after complete enrollment coverage and graceful legacy drain,
  replace the provisional `legacy_producers_drained` acknowledgement with
  validation of the actual generation-bound proof. Caller-supplied `True`
  is not an activation capability.
- Use the sanctioned installer activation seam and existing registered
  tracking-write implementation. Serialize competing activations and
  revalidate generation ownership at the snapshot/commit boundary.
  Backfill under global journal -> trace -> record lock ordering.
- Mark each handoff ready only after its complete replay/save succeeds.
  A stale generation, failed drain, rollback to journal-only producers,
  unknown readiness version, invalid history, or I/O failure must not
  expose ready slots to consumers. Define rollback before enabling
  readiness: prevent legacy writers from serving ready records, or
  atomically withdraw readiness before they can resume.
- Cover handoffs created after the initial pass and installations with
  no previous history through the same enrollment/readiness contract.
  Empty history alone does not prove that legacy producers cannot write.
- Ship slot-first writers and their diagnostic-mirror markers coherently.
  Verify the deployment, then complete the parent Phase 1 checklist;
  only afterward begin the parent Phase 2 consumer changes.

## Validation gates

- [ ] A supported-producer inventory maps every entry point to enrollment
      and states how its pre-enrollment incarnation is accounted for.
- [ ] Real-process pre-spawn rehearsal: pause an old launcher after
      resolving an interpreter but before spawning/registering; activate
      enrollment, then let it append a newer journal-only event. Readiness
      must remain false until that root naturally exits and backfill must
      retain the later event.
- [ ] Repeat with an old launcher paused before initial resolution and
      with an old independent CLI writer already mid-append. A new root
      concurrently entering resolution must be enrolled in exactly one
      generation before any child can mutate state.
- [ ] Prove unknown/missing registrations, failed identity reads,
      incomplete enumeration, PID reuse, and a live old root all refuse
      activation. The negative cases must use the production readiness
      path, not a test-only boolean.
- [ ] A cooperative root cannot acknowledge its new generation while
      an old-generation child/use remains outstanding. Natural drain
      does not terminate the launcher, CLI process, or shared service.
- [ ] Concurrent installers, crashes between enrollment/activation/save,
      stale proofs, retry, and rollback preserve the readiness invariant.
      A successful monitor drain without the producer proof is refused.
- [ ] Fresh install, no live monitor, monitor disabled, and handoffs
      created after the initial pass satisfy the same readiness contract.
- [ ] Real Windows and POSIX resolution/spawn boundaries are exercised;
      launcher/installer changes preserve headlessness and parity.
- [ ] Full changed-plugin suites and install-contract/documentation gates
      pass through supported runners with explicit adequate budgets.
- [ ] Live staged upgrade proves legacy-root blocking, natural drain,
      correct backfill, and no duplicate successor/retirement action.
      Record why any genuinely unavailable external lane was skipped.

## Completion boundary

An enrollment release, an empty registry, and a merged rollout plan are
not Phase 1 completion. Complete bootstrap coverage, verified producer
drain, activated backfill, reviewed implementation, promotion/deployment,
and live upgrade evidence remain required. The parent effort still owns
all six phases and sustained CPU proof.
