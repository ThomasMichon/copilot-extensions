# Installation-Mode Governance

[Effort](README.md) · [Architecture](design.md) ·
[Normative install contract](../../../docs/install-contract.md#installation-mode-governance) ·
[Migration issue #1110](https://github.com/ThomasMichon/copilot-extensions/issues/1110)

## Status

**Specification only.** This document records rationale, rollout, acceptance,
and remaining implementation choices. It does not activate a plugin, migrate
state, change an installer, or claim that the governance resolver exists.
The exact policy, activation, tombstone, resolver, status-precedence, and
effective-mode contracts are normative only in
[`docs/install-contract.md`](../../../docs/install-contract.md#installation-mode-governance).

## Decision and rationale

Legacy, non-namespaced operation remains the implicit default. A user can opt
into namespaced installation globally and then experiment with an exact
source-derived marketplace or plugin. The policy remains one boolean decision;
shadow comparison is doctor-only, not a runtime mode.

The desired-mode file is
`~/.copilot-extensions/installation-mode.json`, where `~` is the canonical
operating-system account profile rather than ordinary `HOME`, Copilot home, or
a durable-home override. Repository config and payload content cannot authorize
activation. Test and explicit management-path injection is useful for fixtures
and repair, but is deliberately insufficient to turn a runtime namespaced.

Desired policy and actual ownership are separate because a file edit cannot
safely move durable state or stop live writers. A monotonic
`installation-activation.json` pins the actual root, environment, namespace
generation, and install generation. Removing the flag after activation
therefore requests deactivation; it never resurrects legacy operation.

Windows, native POSIX, and each WSL distribution are separate environments.
Receipts do not cross-validate and roots are not shared by path similarity.
This preserves the parity already established in the dependency-light
installation-context readers without inventing a Windows/WSL shared-home
contract.

## Legacy ownership and migration

Legacy state is safe to keep running but unsafe to claim automatically. Each
plugin declares an explicit path/service/task footprint probe in its
deploy/installation metadata. Missing probe declarations are treated as
possible legacy presence, not absence.

Migration writes a legacy-side `.installation-ownership.json` tombstone that
points back to the destination activation and generation. This makes transferred
legacy files visibly inert and attributable. A valid tombstone for another
marketplace cell does not prevent a new, distinct cell from activating; the new
cell simply has no claim on that footprint. A tombstone with missing,
unreadable, mismatched, or foreign activation evidence is an
`orphaned-transfer` and fails closed. Legacy must not resume merely because the
destination receipt disappeared.

The migration transaction holds two existing ownership boundaries at once:
the legacy plugin lock/lease and the destination cell install lock. Both remain
held across quiescence, state transfer, activation publication, and
verification. Failure to acquire either leaves legacy authoritative. Explicit
rollback publishes a later legacy/deactivated activation generation before
clearing the tombstone under both locks. Only cleanup may eventually remove
the activation record, after all companion evidence is cleared.

The intended migration sequence is:

1. enter applicable maintenance and acquire both locks;
2. inventory the declared legacy footprint and validate its ownership;
3. build and health-check the cell runtime without publishing activation;
4. drain legacy loops, refuse lease renewal, and stop legacy writers/services;
5. transfer or bind durable state and write the ownership tombstone;
6. publish the generation-pinned namespaced activation;
7. start and verify the namespaced runtime;
8. retain inert legacy and rollback evidence until explicit cleanup.

Any failure before publication leaves legacy authoritative. Any ambiguous
post-publication evidence fails closed for repair; it never guesses a root.

## Maintenance and active-machine surgery

Installation choice and machine activity are orthogonal. A parser-free
user-wide marker gates early bootstrap, while a strict sidecar records owner,
host, process, reason, entry time, and expected end. The same marker/sidecar pair
can be placed under one cell plugin root for scoped surgery.

Marker existence wins even when the sidecar cannot be parsed. Ownerless, dead,
or otherwise stale maintenance is reported and never automatically cleared.
Read-only status and doctor remain available. Mutation during maintenance
requires a management command's explicit authorization flag; an environment
variable or inherited process context is not authorization.

Hooks, installers, reconcilers, dispatchers, service ensure/start paths,
scheduled work, and long-running loops check maintenance before mutation.
Loops also revalidate the tombstone and activation/install generations at every
iteration boundary. Refusing lease renewal lets active work drain. A remote
dispatcher that cannot determine target maintenance state treats the target as
quiesced and does not provision.

This permits an active machine to be taken out of rotation, reached over SSH,
updated surgically, validated, and returned to service without a concurrent
bootstrap restoring the old runtime. Windows and WSL must each be quiesced
explicitly because their policy and maintenance homes are independent.

## Normative rollout gates

No exemplar becomes operative until all of these are true:

- The shared resolver returns the exact install-contract result on PowerShell,
  Python, and no-Python POSIX fixtures, including stable reason codes and status
  precedence.
- Every legacy installer and bootstrap entrypoint for that exemplar invokes the
  tiny shared probe before mutation and refuses `namespaced-active`,
  `orphaned-transfer`, and maintenance.
- Activation compare-and-swap pins activation, namespace, and install
  generations; long-lived callers revalidate before mutation.
- The plugin declares a complete legacy path/service/task footprint.
- Migration and rollback tests hold both locks through transfer/publication and
  prove that a failed lock acquisition or generation check leaves no mixed
  writer state.
- Windows, native POSIX, and WSL environment-mismatch fixtures fail closed.

The gate applies first to the command-only exemplar, then independently to the
service-bearing exemplar. Passing it for one plugin does not make another
plugin's legacy entrypoints safe.

## Diagnostics

Doctor/status reports policy source and winning scope, including explicit false
versus default false; marketplace and plugin identity; exact environment;
desired and actual mode; authoritative root; maintenance scope/state; activation
and install generations; legacy probe/disposition; and the stable resolver
status/reason. The exact result shape and precedence are in the
[install contract](../../../docs/install-contract.md#resolver-result-and-precedence).

Doctor may calculate proposed roots, collisions, footprint ownership, and
receipt readiness while legacy remains authoritative. It does not stamp a
receipt or create a runtime. Unsupported future policy versions still permit
doctor and explicit repair, and an already-activated runtime continues from its
pinned actual root rather than being stranded.

## Rollout

1. Land this specification and the normative install-contract additions with no
   operative callers.
2. Add shared fixtures for policy precedence, unknown-field preservation,
   environment binding, tombstones, footprint probes, maintenance, resolver
   precedence, and generation revalidation.
3. Add read-only doctor output on Windows and POSIX.
4. Add the shared legacy bootstrap probe to every command-only exemplar
   entrypoint before allowing namespaced first install.
5. Prove new install, legacy migration-required, rollback, orphaned transfer,
   and maintenance behavior for the command-only exemplar.
6. Add the probe and two-lock/lease behavior to the service exemplar, then prove
   scoped maintenance and draining loops.
7. Generalize through the remaining rollout issues; only then make isolation
   inventory enforcement blocking and retire legacy wrappers.

No rollout step changes the absent-file default.

## Acceptance

- Absent policy and every implicit default select legacy exactly as before.
- Explicit global, marketplace, and plugin true/false values follow the
  documented precedence and diagnostics preserve explicit false.
- A clean true case creates one namespaced installation; unattributed or
  unprobed legacy state reports migration-required without a parallel runtime.
- A valid legacy tombstone for another cell permits a distinct cell, while an
  orphaned or foreign tombstone fails closed.
- Removing policy from an active cell reports deactivation-required and leaves
  the namespaced root pinned.
- Malformed policy blocks mutation; an unsupported future version blocks policy
  changes without stranding valid active runtime and repair.
- Activation CAS rejects changed namespace, install, or activation generations.
- Global and plugin-scoped maintenance block new work; stale maintenance is
  visible and is not auto-cleared.
- A remote target with unknown maintenance state is not provisioned.
- Windows and WSL receipts never validate each other.
- Two marketplaces carrying the same plugin can be migrated, run, rolled back,
  repaired, and cleaned independently.

## Open implementation choices

- Public CLI command names and output formatting for policy editing, migration,
  deactivation, cleanup, and maintenance.
- Platform-specific owner-liveness checks used to classify a maintenance
  sidecar as stale.
- Retention duration for inert rollback material after deactivation.

These choices do not alter binary desired policy, sticky actual mode, ownership
tombstones, two-lock migration, or fail-closed status precedence.
