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

## Increment boundary

Unmanaged plugin companions retain their existing lifecycle. This increment
adds no generation leases, retention/garbage collection, specific-plugin
integration, independent engine lifecycle, or multi-host failover.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Supervision:
  [`service-lifecycle-supervision.md`](service-lifecycle-supervision.md)
- Installation identity:
  [`marketplace-installation-cells.md`](marketplace-installation-cells.md)
- Deploy contract: [`install-contract.md`](../install-contract.md)
