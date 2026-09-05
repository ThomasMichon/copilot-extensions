# Pattern: managed-companion-runtime

**Serves:** *Vision plugin-services*
§Features/`delegated-heavy-companion-runtime`; *Vision plugins/agent-dispatch*
registered supervision.
**Exemplar:** agent-dispatch `plugin-companion` declarations.

## Problem

Some optional companion capabilities have dependency footprints that are too
heavy for ordinary plugin first-use or session-start provisioning. Giving a
plugin command a package manager, arbitrary installer argv, or writable runtime
selection would make every invocation an installation authority and blur the
boundary between declarative contribution and host lifecycle ownership.

## Boundary

The plugin contributes **data** through attributed active-plugin discovery. The
already-running trusted supervisor contributes **authority**:

- the plugin declares logical runtime identities, portable versions and
  profiles, plugin-relative Python projects and extras, validation imports, and
  the environment variable that will receive each selected interpreter;
- the supervisor chooses physical roots, trusted toolchains, package sources,
  staging, publication, selection, rollback, and retention;
- plugin commands never receive package-manager credentials or a fallback
  self-provision path; and
- absent explicit capability configuration or supervision, the companion is
  inert and honestly unavailable.

This is a narrow exception to normal plugin self-provisioning. A routine runtime
plugin remains independently self-provisioning and must not adopt this pattern
merely to centralize installation.

## Declaration contract

A `plugin-companion` may include:

```json
{
  "managed_runtime": {
    "schema_version": 1,
    "runtimes": [
      {
        "name": "service",
        "version": "1.2.3",
        "profile": "host",
        "python_env": "EXAMPLE_MANAGED_PYTHON",
        "projects": [
          {"path": "libs/helper"},
          {"path": ".", "extras": ["service"]}
        ],
        "imports": ["example_service"]
      }
    ]
  }
}
```

The declaration accepts no physical runtime root, installer command,
package-manager argument, package index, credential, or arbitrary environment
value. Runtime name, version, profile, extras, and import names are bounded and
portable. Project paths remain beneath the authoritative plugin root and are
revalidated against links and reparse points when a later materializer consumes
them.

Managed-runtime metadata is part of the declaration's runtime authority
revision together with plugin root and plugin version. Provider uncertainty may
retain a prior desired state only while that complete authority remains
unchanged.

## Immutable materialization

The running dispatch supervisor resolves a machine-local runtime root and its
Python/package-manager toolchain from supervisor-owned policy. For an active,
attributed declaration it:

1. serializes builders with one crash-safe interprocess lock for the physical
   root;
2. copies every declared plugin-relative project into a root-contained snapshot,
   rejecting links, reparse points, special files, escapes, and source changes
   during the copy;
3. creates the environment in unique staging, installs only from that snapshot,
   and validates the declared imports;
4. atomically publishes a cell keyed by runtime version, profile, and snapshot
   content digest, with a versioned receipt binding source authority and
   supervisor toolchain identity; and
5. reuses only a cell whose complete receipt, interpreter trust, and imports
   still validate.

On Windows, the policy-selected base Python and the copied environment
interpreter must pass Authenticode verification before package installation.
Ambient `PIP_*`, `UV_*`, `PYTHONPATH`, indexes, credentials, and provider
environment are not inherited by build subprocesses.

Materialization is preparation only. It does not inject the selected Python into
a companion environment, stop or replace a process, publish a current pointer,
lease a generation, or delete a cell. Live selection belongs to the supervisor's
separate cutover path.

Version-2 cell receipts bind the complete declaration authority, physical root,
exact cell path, platform, content identity, and toolchain identity. Their
version participates in the cell key, so introducing retention never overwrites
a version-1 generation. Existing invalid cells are preserved in place rather
than quarantined or rebuilt beneath a potentially live selection. Version-1
cells remain valid recovery inputs but are never automatically reclaimed.

## Safe cutover

The supervisor captures one immutable launch snapshot containing complete
declaration authority, exact resolved run/stop/health argv, working directory,
timeouts, selected cell identities, and the **full effective environment**.
Declared Python bindings replace conflicting provider or inherited values
case-insensitively. Managed launches disable Python bytecode writes and user-site
imports; a managed declaration must provide a health probe before it can launch.
Provider results, subsequent declarations, and later materialization results
cannot mutate a captured snapshot.

An update prepares and validates its cells and constructs the replacement
snapshot **before** stopping a healthy predecessor. Run and readiness probes use
the replacement snapshot; stop uses the predecessor's snapshot. A replacement
that cannot become ready is retired before the prior still-published cells are
revalidated and restarted with their exact prior snapshot. Recovery validation
does not recopy the current payload, rebuild a runtime, or quarantine a cell.
Failed launches and builds back off rather than repeatedly stopping a rollback.

The existing process receipt records the launch snapshot before the containment
gate opens. A separate atomically written last-ready selection survives process
retirement. After a supervisor restart, an unchanged selected authority is
recovered before newer provider environment or materialization results are
considered. An interrupted first launch can recover from its gated process
receipt. Recovery is readiness-gated; failed recovery cannot permanently block a
prepared current configuration. POSIX adoption requires the exact process-start
identity. Windows retires the identified predecessor and reacquires Job
containment by launching again; unconfirmed retirement preserves its receipt and
blocks another launch.

Provider uncertainty permits only an existing live process under **unchanged
complete authority**. It does not authorize building, restarting a dead process,
applying a newer cached provider result, or selecting a different cell.
Disablement withdraws both live supervision and saved selection. A source,
owner, activation-scope, or supervision-scope change cannot inherit rollback
authority merely by retaining the same registration ID. Async work withdrawn or
superseded during preparation cannot reenter live desired state when it finishes.

Launch snapshots freeze invocation data, not arbitrary files named in argv.
Lifecycle adapters remain attributed plugin commands; their referenced payload
files must remain available for those commands to execute. Runtime package inputs
and selected interpreters, by contrast, belong to the validated immutable cells.

## Identity-bound retention

The physical root contains a shared `.retention` registry, not a private
supervisor-environment cache. Its leases bind registration and declaration
authority, launch and command digests, exact cell paths and receipt hashes, and
the holder's OS PID authority, PID, and process-start token. Process-start tokens
come from the existing companion receipt implementation. Windows identifies the
machine's PID authority; Linux additionally identifies the boot and PID
namespace. Missing authority is unavailable, not an invitation to infer liveness
from a PID or a configured host alias.

Before stopping a predecessor, the supervisor leases the validated replacement.
Before a new child's containment gate opens, its process receipt and matching
runtime lease are durable. A surviving child therefore remains protected even
if its supervisor exits. Releasing a preparation lease addresses only that exact
supervisor/snapshot identity; unrelated corrupt records cannot tear down a
healthy replacement or its rollback. A failed redundant-pin release is logged
and retains the extra pin rather than retiring an already protected process.

Each supervisor environment also has a durable selected-generation pin and one
prior-ready rollback pin. Root pins normally precede last-ready selection
publication. After readiness, a root-lock timeout or pin-write I/O failure is
logged without retiring the healthy process: its gated receipt and process lease
already protect the cells, and last-ready publication still records the exact
ready snapshot for recovery. Later recovery republishes the redundant root pin.
Unsafe metadata errors and failures to establish the pre-gate process lease
remain fatal. External selection withdrawal and root-pin removal share the root
lock. A crash between publications conservatively protects both generations. Cleanup checks
the environment's exact process and last-ready receipts as well: an interrupted
first-launch receipt remains discoverable and protected until recovery retires
it or publishes selection. Every stale lease retained for an interrupted scope
also protects its own referenced cells, not just the cells in the current process
receipt. Neither selected nor rollback pins expire when the supervisor process
dies. A validated unmanaged successor does not inherit managed cells or prevent
unrelated retention.

The supervisor schedules cleanup on its runtime executor at most once every
five minutes. Cleanup, lease/pin mutation, validation, and materialization use
the same crash-safe root-wide interprocess lock. Cleanup preflights the complete
root tree and every lease, selection, owner, and cell before deleting anything.
It rejects links, junctions, reparse points, special files, duplicate/malformed
metadata, receipt/content/path mismatches, and ambiguous ownership. An otherwise
identity-valid stale lease with a dangling or malformed reference is instead
preserved and logged: its identifiable owners are excluded from reclamation,
or all cells are preserved if the reference is opaque. The same exclusion applies
when a process or last-ready launch receipt references a missing, quarantined,
or invalid cell: cleanup logs the failure and treats that scope as interrupted,
retaining its discovery leases even when a root selection pin exists. Excluded
owners remain untouched without requiring valid cell metadata; unrelated owners
still undergo full validation and reclamation. Structural filesystem and traversal
errors remain errors, never an empty inventory. Only exact version-2 cells beneath
`cells/<owner>/<generation>` can be deleted; legacy cells, failed builds, and
staging are not cleanup targets.

Deletion first atomically moves each revalidated eligible cell out of `cells/`
into a unique, root-contained `.deleting/` destination under the same root lock.
Only that unpublished tree is recursively removed, with three bounded attempts
for filesystem errors and link/reparse validation before each attempt. An
interruption or persistent antivirus lock can leave deletion staging residue,
but never a partially deleted published cell. Cleanup ignores existing residue
as a reclamation candidate and preserves it without receipt/content validation;
the root-wide structural safety preflight still rejects links, reparse points,
and special files there. Failed moves are logged and counted as preserved
published cells while unrelated eligible cells continue. `CleanupResult.deleted`
reports cells removed from the published namespace, not guaranteed disk-space
recovery; exhausted recursive deletion attempts log the retained staging path.

A lease is stale only when its own PID authority can prove that the recorded
process and process group are absent. A matching live identity protects the
generation. PID reuse, inaccessible identity, and leaderless live groups stop
cleanup without deleting data; a foreign PID authority always retains its
leases, regardless of what local PID inspection reports. There is no TTL-based
liveness or cross-environment lease takeover.

After these protections, the default policy retains the two newest unreferenced
generations per attributed owner/runtime/profile and every cell younger than
24 hours (using its immutable receipt's modification time). Supervisor-owned
policy bounds count to 0-100 and minimum age to 0-365 days; plugins cannot set
either. Protected generations do not consume the unreferenced allowance. This
is a bounded reclamation policy, not a disk quota: live/selected/rollback,
foreign, legacy, or ambiguous data may exceed it indefinitely. Conservative
retention is preferable to destroying an unreconstructable live runtime.

Receipts prevent accidental cross-environment adoption and stale-generation
cleanup; they are not cryptographic attestation against a malicious process
running as the same filesystem owner. Lifecycle writers must honor the root
lock. A foreign OS authority or malformed retained artifact requires explicit
ownership repair rather than an automated destructive fallback.

## Increment boundary

Unmanaged plugin companions retain their existing lifecycle. This increment
adds generic generation leases and bounded retention, but no specific-plugin
integration, independent engine lifecycle, host placement, or multi-host failover.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Supervision:
  [`service-lifecycle-supervision.md`](service-lifecycle-supervision.md)
- Installation identity:
  [`marketplace-installation-cells.md`](marketplace-installation-cells.md)
- Deploy contract: [`install-contract.md`](../install-contract.md)
