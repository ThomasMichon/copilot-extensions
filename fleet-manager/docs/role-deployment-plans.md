# Explicit role/provider deployment plans

Back to [fleet-manager](../README.md) and the existing
[Linux role fleet effort](../../efforts/active/linux-role-fleet/README.md).

For contributed slot templates and a named-machine CPU/memory/disk grid, use
the separate [`fleet-manager design` command](loadouts.md). It does not change
the role-provider compiler's contracts or add deployment authority.

The installed SDK supplies `plan_role_deployment` and `verify_role_plan` in
`fleet_manager.role_deployment`, plus the explicit module CLI:

```text
python -I -m fleet_manager.role_plan --template recommended.json --config adopter.json --inventory observed.json --fleet-id example --machine-limit 2
```

This composes the existing offline planner with configuration-selected role
bindings and a code-registered provider contract. It makes no HTTP request,
starts no process, resolves no credential and performs no installation,
bootstrap, manager write, activation or cleanup. There is no new default
`fleet-manager` command or automatic hosting dependency.

## Selected configuration namespace

Use the existing `fleet-manager.config`, with the optional namespace
`extensions.role_bindings`. The ordinary `fleet-manager plan` preserves it
without selecting a provider; the explicit role compiler requires it.
The template, capacity, inventory and freshness contracts remain those of the
existing planner.

Example namespace within an adopter configuration:

```json
{
  "schema": "fleet-manager.role-bindings",
  "version": 1,
  "providers": {
    "manager": {
      "implementation": "komodo",
      "instance_ref": "core-a",
      "target": "host-a",
      "configuration_ref": "core-config",
      "bootstrap_ref": "local-bootstrap"
    }
  },
  "bindings": {
    "gateway": {
      "implementation": "fleet-manager.gateway",
      "provider": "manager",
      "target": "host-a",
      "resource": "gateway-stack",
      "artifact_ref": "gateway-image",
      "configuration_ref": "gateway-config",
      "key_refs": ["gateway-identity", "tls-key"],
      "requires": ["container-role", "named-resource"],
      "depends_on": []
    }
  }
}
```

The role must exist in the preserved/proposed fleet configuration. All values
above are bounded non-secret identifiers, not artifact bytes, paths, endpoints
or credentials. Configuration/key/artifact/bootstrap references require
independent resolution by their existing owners. Constructing a
`ProviderInstance` here records a configured reference; it is not an
authenticated fleet-contracts provider-instance registration.

Provider fields are exactly those shown. Binding fields require
`implementation`, `provider`, `target`, `resource`, `artifact_ref` and
`configuration_ref`; `key_refs`, `requires` and `depends_on` are optional
reference collections. Unknown fields, raw commands/environment/mounts/URLs,
undeclared providers, unknown role dependencies, dependency cycles, repeated
references and multiple writers for one provider resource refuse.
One instance reference cannot be aliased by different provider declarations,
including declarations selecting different adapters.

Multiple distinct manager instances may use the same resource name. This is
logical configuration validation, not proof that two different references
resolve to physically independent managers. The actual effect owner must
verify that identity and ownership before any mutation.

Custom role details and unrelated extensions remain intact. Unbound roles
are reported separately, not deleted. Unbound provider declarations remain
in configuration but produce no bootstrap requirements, provider lookup or
managed-resource projection.

## Provider contract and Komodo projection

`ProviderContract` is immutable version-1 metadata: explicit implementation,
canonical supported role-implementation tuple and canonical capability tuple.
Trusted in-process callers supply a mapping of implementation names to
`RoleProvider` objects. No configuration field loads a Python module,
executable, URL or remote code. The compiler captures each selected provider
and its immutable contract once; those exact contracts participate in the
saved plan's digest.

Provider `project(instance, binding)` callbacks are trusted **pure planning
code**, not effect executors or a security sandbox. They return a typed
`RoleResource`, not arbitrary payloads. The compiler rejects changed target,
instance, resource, artifact, configuration or key references.
Unsupported implementations/capabilities block without selecting a fallback.
Stale/incomplete inventory, unadmitted manager/role targets or blocked
dependencies do not reach projection.

The bundled `KomodoRoleProvider` supports `container.service` and
`fleet-manager.gateway`, with `bootstrap-reference`, `container-role` and
`named-resource` planning capabilities. It projects the selected references
to a named `Stack`; it does **not** create that Stack, render/submit arbitrary
Compose contents or claim Core accepted it. Existing artifact/configuration
owners and the selected manager adapter still supply the actual deployment.
The explicit module CLI registers only this bundled provider.

## Bootstrap is a requirement, not a fabricated receipt

Each selected manager retains its explicit `bootstrap_ref` and target, with
`state: owner-evidence-required`, required independent substrate authority,
`independence_verified: false`, `installed: false` and
`live_readiness_verified: false`.

The reference must be satisfied before that manager can execute role changes.
Its supported installation/recovery path must work without the Gateway or
Core API. This compiler does not resolve that reference or invent an installer.
`KomodoRoleProvider` does not support hosting `komodo.core` through that same
Core; selecting it blocks instead of hiding a bootstrap cycle. An independent
bootstrap executor and real proof remain prerequisites under #6030/#6240/#6424.

## Output, revalidation and limits

The `fleet-manager.role-plan` preserves suggested configuration and aggregate
capacity proposals, orders selected roles by declared dependencies, and reports
selected manager requirements, named-resource projections, unbound declarations
and blockers. A target must be explicitly owned/ready in the supplied inventory;
the compiler neither creates a missing host nor treats template capacity as
placement admission.

Blockers are grouped by logical subject: each entry has `scope` (`fleet`,
`provider` or `role`), nullable role/provider references and a `codes` list.
All distinct diagnostics are preserved. At most one fleet group, 256 provider
groups and 256 role groups fit the existing 513-item output collection bound;
multiple failures on one subject do not overflow that bound or become invalid
input. Compilation, saved-plan verification and CLI serialization use the same
unchanged shared limits, without truncating diagnoses.

Nonblocked status is `review-required`, never ready/installed. Evidence is
`supplied-snapshots-and-provider-contracts-only`; activation, installed,
live-readiness and mutation flags remain false, and cleanup is empty.
Even an inventory asserting a running role does not authenticate that assertion
or prove the selected manager instance/role binding healthy.

`verify_role_plan` recompiles exact template/config/inventory/policy and selected
provider contracts, rejects tampering/expired/changed assumptions and refuses
blocked plans. This is integrity and freshness, not source authentication,
operator approval, current enrollment, external fencing or an effect grant.
An uncertain manager request cannot be retried merely because this check passes.

The existing input/output byte, depth, collection and duplicate-key limits
apply; the compiler does not nest the preserved configuration inside another
plan and thereby consume its existing depth budget. CLI input uses the existing
bounded ordinary-file reader. Exit 0 means a nonblocked proposal, 2 means a
blocked valid proposal, and 1 means invalid input or unavailable file evidence.
Library diagnostics omit supplied values; standard argparse syntax diagnostics
may include arguments. Never put credentials in CLI arguments or input files.

Actual installed Core/Gateway control, artifact construction/activation,
provider credentials, manager mutation, runtime readiness and production
adoption remain open. This compiler closes a configuration/pluggability seam,
not those lifecycle or parent-effort gates.
