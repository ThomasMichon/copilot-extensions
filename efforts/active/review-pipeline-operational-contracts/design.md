# Operational Contract Proposal

Back to the [effort](README.md). This document proposes changes; it does not
assert implementation or activation.

## Installation versus activation

Reuse the existing cutover machine result and installer status boundaries.
Do not introduce another lifecycle state store or infer success from logs.
Installed payload/runtime, serving version/protocol/readiness, and rollback
outcome are separate facts. A completed rollout requires an actual read of the
active serving generation after the existing cutover commit boundary.

A pre-commit rollback retaining the old healthy daemon is safe but is not
successful activation of the requested runtime. Return a nonzero installer
outcome and explicit partial/failure diagnostic; the parent updater must
preserve that result. An absent daemon/client-only deployment remains governed
by its declared installation mode, not a newly universal daemon requirement.

Preserve exact generation/identity fencing and existing commit-forward behavior.
Routine updates must not force a busy turn, cancel work, or convert an unknown
drain into success. Explicit user force semantics remain separate and must not
be invoked implicitly by ordinary installer update.

Use bounded fixture-driven shell and parent tests: health refusal, busy drain,
rollback, old version, wrong protocol, stale generation, success and post-commit
retirement outcomes. Prefer existing return codes/result fields over a broad
new abstraction.

## Control readiness

The explicit CLI is `agent-dispatch control-readiness`. Its dedicated read-only
control-guarded endpoint proves the same configured authority used by privileged
operations. Do not call a mutation endpoint to test authentication.

Output contains redacted configuration source (`direct`, `command`, `missing`),
presence/readiness, authorization and a bounded safe diagnostic. Unavailable
server control authority differs from wrong/missing client authority.
The CLI fetches a command-backed credential only when the operator invokes
this probe. It must not read service-env files, infer a secret from process
state, overlay configuration, export raw tokens or elevate automatically.

Existing direct/command precedence remains unchanged. Command failure, timeout,
malformed/empty output and an ordinary worker bearer must not appear authorized.
Neither CLI nor endpoint mutates tasks, generations, reservations, acquisitions,
configuration or persisted credentials. Tests assert safe stdout/stderr/logs and
the actual endpoint guard, not a mocked-away header construction.

## Windows process proof

Keep the existing production shared spawn helper and its lifetime/visibility
separation. Determine whether distinct terminal-host PIDs belong to the exact
identity-pinned fixture tree and are created by the repeated child launch, or
whether the observation contract is demonstrably wrong.

Do not weaken the one-hidden-tree assertion, skip the live test, or increase
supervisor bounds as a remedy. Retain zero new visible windows and terminal
foreground transitions, repeated cycles, ownership-limited teardown, and
contained-test suppression of production Job breakaway.

Unreproducibility is evidence to document on #6266, not proof of no defect.
Missing native prerequisites remain Blocked until independently satisfied or
explicitly transferred to a named owner through the effort completion protocol.

## Publication and activation

Normal target review, required checks, changefiles and release promotion apply.
Before publication, audit actual diff/examples/commit/issue/PR content for
private identifiers; final provider review is not a substitute for that audit.
Merge is pending release, and release is pending authorized running-state
verification. Deployment authority is distinct from source implementation.

## See Also

- [Parent effort](README.md)
- [Graceful cutover pattern](../../../docs/patterns/graceful-daemon-cutover.md)
- [Windows launch pattern](../../../docs/patterns/windows-background-process-launch.md)
