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

## Increment boundary

The declaration-contract increment is deliberately non-operative. Validation
and provenance propagation do not create directories, invoke an environment
builder or package manager, select a runtime, or change live companion launch.
Materialization, safe cutover, and retention land as separate reviewed
increments.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Supervision:
  [`service-lifecycle-supervision.md`](service-lifecycle-supervision.md)
- Installation identity:
  [`marketplace-installation-cells.md`](marketplace-installation-cells.md)
- Deploy contract: [`install-contract.md`](../install-contract.md)
