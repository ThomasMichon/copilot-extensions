# Fast incoming construction and outgoing-service cutoff

[Canonical effort](README.md) |
[Incoming installation path](installation-convergence.md) |
[Update and service lifetimes](update-service-lifetimes.md)

## Current direction

The latest relayed operator direction identifies two causes of installation
failure: construction is not using the fastest existing path, and outgoing
services prevent incoming startup. This record captures that amendment; it is
not evidence that the implementation or native qualification already satisfies
it. The original source, application, execution and deployment owners remain.

The requested incoming path is quick file copying, a pure UV-based,
wheel-backed physical environment, and each application's simple setup command
before the cutover attempt. Extra fingerprint validations must not reject
installation start or success, or consume its budget in repeated checks.
Do not introduce rejection/admission gates in this construction/setup phase.
Do not introduce a second constructor or silently retain the rejected
installation-phase gates under a different name.

Service activation is **kill by default; hand off only when reliable**.
Allow one bounded safe-handoff attempt where supported, then force-kill the
outgoing service and its daemons. Without reliable handoff, force-kill the
outgoing version first. Runtime-state locks, handoff failures, schema conflicts and
drain delays must not prevent incoming startup.

The direction explicitly identifies MCP, Vault and Index as not needing
handoff, and Worktrees and Bridge as able to survive abrupt cutoff.
This supersedes conflicting older requirements to wait for outgoing drain,
cooperation or exit before permitting replacement. It does not turn a process
name into an outgoing-service identity or reassign unrelated resources.

## Intake and ownership

The direction arrived as a fleet-monitor relay, described as lightly condensed,
not as a verbatim transcript. The paragraphs above preserve its requested
behavior without private transport identities. They are an intent capture,
not an agent-authored claim that the behavior is safe or implemented already.

The existing coordinator remains responsible for #7028/#7758. A separate
existing contributor owns read-only plugin payloads, the harness application
readiness/New-gate correction and the per-application kill-by-default audit.
Consume that audit and coordinate shared interfaces; do not start a competing
per-app audit or copy its fixes into another PR.

The relay also states:

> Release/install is allowed, as with host updates.

Use the existing release, installation and resource owners for those actions.
Reconcile the addressed target and unresolved effects at that boundary rather
than treating this sentence as either a universal resource transfer or an
automatic prohibition on otherwise authorized host updates.

## Receiving work

The existing constructor and ordinary update owners receive the fast
copy/UV/wheel/setup correction. The existing execution and app lifecycle
owners receive the bounded handoff/forced outgoing-service cutoff contract.
The separate per-app audit supplies its application-specific findings.
Update the standing vision and affected source contracts through normal
review before their realization; retain original evidence and record which
older acceptance language this amendment supersedes.

## Receiving plan and validation - agent-recommended

- [ ] C1: Review the revised Installer, Plugin Service and Runtime Assembly
  intent plus the shared install/application/cutover contracts. This replaces
  preferred start-first, mandatory graceful drain and delayed-force-only policy,
  not completed evidence, real input authentication or data preservation.
- [ ] C2: Through the existing construction and ordinary-caller owners,
  consume selected copyable inputs once, construct the physical environment
  with the canonical pure UV/wheel path and run simple app setup before cutover.
  Remove repeated fingerprint/rejection gates from construction/setup; do not
  add a second builder or bypass governed source/dependency selection.
- [ ] C3: Through the existing execution/lifecycle owners, apply the per-app
  audit's reliable-handoff classification. Share one existing deadline across
  the bounded attempt and forced cutoff; without reliable handoff, cut off the
  outgoing service tree before incoming startup. Preserve protected sessions
  and record unresolved effects without replaying them.
- [ ] C4: Qualify the real ordinary caller under its existing fixture approval:
  no repeated construction admission, one-attempt cutoff or immediate cutoff,
  lock release before incoming state access, actual incoming readiness/current
  selection, preserved fixture data and no protected-process termination.
  Retain negative controls for actual copy/setup/health failure. Unit, real
  consumer, clean-room/native and separately authorized installed evidence stay
  distinct; source merge or a synthetic artifact does not pass them.

C2-C4 depend on C1 review and the relevant receiving interface, not a generic
new acknowledgment gate. The original qualification allocations remain fixed;
do not increase callback/drain timeouts to accommodate obsolete policy.
Keep fixture scope separate from later addressed release/install operations.

The [Manager/Worktrees fixture slice](manager-worktrees-native-consumer.md)
continues under its explicit bounded approval. Its implementation and native
evidence must be reconciled with this current direction rather than qualifying
obsolete fingerprint, mandatory handoff or drain behavior. No completed parent
row, live installation, cleanup or native result is claimed by this record.
