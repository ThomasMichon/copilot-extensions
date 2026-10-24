# Capacity-aware loadout design

The dependency-free installed client actualizes contributed templates through:

```text
fleet-manager design --config adopter.json --inventory capacity.json --catalog contributions.json
```

`python -I -m fleet_manager design` exposes the same command. `--catalog` is
optional; the package always includes the portable `actions.arc` template.
The command reads explicit, bounded non-secret snapshots and writes one JSON
result to stdout. It performs no network request, subprocess, publication,
credential resolution or deployment. Exit codes are 0 for a reviewable design,
2 for a blocked design and 1 for invalid input.

This implements the template/slot and resource-grid part of the
[loadout vision](../../visions/machine-fleet/loadouts/README.md). It is not a
Gateway deployment/confirmation endpoint or an ARC installer. Existing
[`plan` and role-provider compilation](role-deployment-plans.md) remain
unchanged; a loadout design does not imply that those providers implement its
selected adapters.

## Registrar and contribution contract

The existing `fleet-manager.config` owns adopter state. Add this namespace
under `extensions.loadouts`:

```json
{
  "schema": "fleet-manager.loadout-registrar",
  "version": 1,
  "revision": "r1",
  "allocations": {
    "runners": {
      "template": {"id": "actions.arc", "revision": "1", "source": "fleet-manager"},
      "target": "worker-a",
      "slots": {
        "scale_set": "example-runners",
        "forge_scope_ref": "selected-repo",
        "runner_group_ref": "restricted-group",
        "architecture": "x64",
        "artifact_ref": "pinned-runner-artifacts",
        "configuration_ref": "arc-values",
        "credential_ref": "github-app-binding",
        "trust_policy_ref": "isolated-jobs",
        "toolchain_ref": "ci-tools",
        "cluster": "support",
        "controller": "support",
        "credential_service": "support",
        "network": "support",
        "min_runners": 0,
        "max_runners": 2,
        "cpu_millis": 1000,
        "memory_bytes": 2147483648,
        "disk_bytes": 10737418240,
        "limit_cpu_millis": 1000,
        "limit_memory_bytes": 2147483648,
        "limit_disk_bytes": 10737418240
      }
    },
    "support": {
      "template": {"id": "support", "revision": "r1", "source": "example-repo"},
      "target": "control-a",
      "slots": {"config": "support-config"}
    }
  }
}
```

All references are bounded identifiers, not URLs, paths, tokens, commands or
artifact contents. Owners must subsequently resolve the selected forge scope,
pinned artifacts, Kubernetes membership, configuration and credential bindings.
An identifier's spelling or a template digest proves none of those things.
The ARC template records `helm.arc` as the intended adapter; ARC owns its pods.
Maximum runners multiplies the per-runner reservation/limit, not controller
replicas. Minimum must not exceed maximum; maximum is 1-256. Resource
reservations must be positive and limits cannot be lower than reservations.
There is no implicit hosted/self-hosted routing or fallback policy.

Plugins and repositories can contribute explicit data through a catalog:

```json
{
  "schema": "fleet-manager.loadout-catalog",
  "version": 1,
  "templates": [{
    "schema": "fleet-manager.loadout-template",
    "version": 1,
    "id": "support",
    "revision": "r1",
    "source": "example-repo",
    "slots": {"config": {"type": "reference"}},
    "provides": [
      "kubernetes.cluster", "arc.controller", "forge.credential", "runner.network"
    ],
    "requires": {},
    "resources": {
      "units": 1,
      "reservation": {
        "cpu_millis": 1000, "memory_bytes": 2147483648, "disk_bytes": 10737418240
      },
      "limit": {
        "cpu_millis": 1000, "memory_bytes": 2147483648, "disk_bytes": 10737418240
      }
    },
    "configuration": {
      "adapter": "managed.support",
      "configuration_ref": {"slot": "config"}
    }
  }]
}
```

This synthetic support template represents an explicitly sized enclosing
capability bundle, **not a delivered support-service installer**. Real
contributors declare their complete control, storage, auth, connectivity and
exposure closure, including allocations on other named machines. Distinct
support roles can use distinct templates/allocations. Multiple requirements
may name one allocation providing those capabilities; it is charged once.
Declaring a capability records intent, not observed readiness.

The contribution contract is deliberately data-only and explicitly selected;
there is no directory crawler, plugin import or template execution. Template
IDs cannot shadow bundled IDs or another contribution. Allocation provenance
must match the selected template's source and revision. Templates declare all
slots; missing/extra slots refuse. Supported types are `reference`,
`references` (unique reference list), and `integer` with optional nonnegative
`minimum`/`maximum`. There are no string expressions or partial interpolation:
`{"slot":"name"}` replaces an entire JSON value. The `slot` key is reserved for
that exact one-field form.

`requires` maps capability IDs to allocation references, optionally supplied
by slots. Each dependency must exist and its template must `provide` the named
capability. The output includes dependency order; cycles refuse. Two ARC
allocations cannot claim the same cluster-reference/scale-set pair. Logical
references do not rule out aliases in the external system: its actual owner
must verify physical identity before applying anything.

Resource declarations require `units`, `reservation`, and `limit`. Each
resource vector has exactly `cpu_millis`, `memory_bytes`, and `disk_bytes`;
numeric values may come from integer slots. Units is 1-256. All arithmetic
uses nonnegative bounded integers, never floating CPU fractions or implicit
unit conversion.
Expansion has one shared 1 MiB JSON-byte budget across allocations, consumed
before copying a slot value or building each container. Repeated references
cannot materialize an unbounded intermediate before final validation.

## Named-machine inventory and the grid

The capacity snapshot has this shape:

```json
{
  "schema": "fleet-manager.loadout-inventory",
  "version": 1,
  "fleet_id": "example",
  "revision": "i1",
  "observed_at": 1700000000,
  "expires_at": 1700000300,
  "complete": true,
  "baseline_excludes": ["runners", "support"],
  "hosts": [{
    "id": "worker-a",
    "owned": true,
    "ready": true,
    "capacity": {
      "cpu_millis": 4000, "memory_bytes": 8589934592, "disk_bytes": 42949672960
    },
    "headroom": {
      "cpu_millis": 1000, "memory_bytes": 2147483648, "disk_bytes": 10737418240
    },
    "reserved": {
      "cpu_millis": 1000, "memory_bytes": 2147483648, "disk_bytes": 10737418240
    },
    "observed": {
      "cpu_millis": 500, "memory_bytes": 1073741824, "disk_bytes": 5368709120
    }
  }]
}
```

This illustrates one row, not a complete runnable inventory for the registrar
above: that also needs `control-a`, and timestamps must describe the actual
current snapshot. The real provider supplies these observations; the client
does not probe or authenticate machines from their names.

`reserved` and `observed` describe the **background baseline**, excluding the
allocations being designed. The provider must explicitly identify that
partition: `baseline_excludes` must equal the complete registrar allocation
set. Never label an undifferentiated whole-machine sample as an excluded
baseline; doing so would double-count existing allocations on an update.
Unknown background usage is `null`, not a zero vector, and blocks selected
hosts. Persistent storage and retained data not included in the proposed
allocation must remain charged to the baseline.

For each dimension independently:

```text
baseline = max(reserved, observed)
available = capacity - headroom - baseline
remaining = available - sum(proposed reservations)
burst_remaining = available - sum(proposed limits)
```

Observations cannot cancel reservations, headroom or another dimension.
Reservations, limits, observations and headroom remain separate output fields.
This first contract does **not** authorize burst oversubscription: either a
negative remaining reservation or a negative burst remainder blocks.
Exactly zero fits. Shared support is counted once across scale sets.

Missing/unowned/unready targets, stale/incomplete inventory, unknown selected
host usage, and exceeding the existing configuration's machine budget block.
Targets are never silently substituted and the client never requests a new
machine. Extra inventory hosts and unrelated config roles/extensions are
retained, not removed to manufacture a fit.

## Result and authority boundary

The `fleet-manager.loadout-design` result carries `grid`, `blockers`,
`composition`, the inventory expiry, source digests and an overall digest.
`blocker_count` reports the total number of distinct diagnostics. The sorted
`blockers` summary is capped at the existing 513-item output collection bound;
`blockers_omitted` explicitly reports any remaining count. The full grid and
composition are retained even for a 256-machine blocked design: diagnostic
summary overflow cannot turn valid input into an invalid-input error.
Its `suggested_config` preserves the complete input configuration and adds
the resolved composition at `extensions.loadout_composition`. The knowledge
repository's existing publication owner can review and publish that desired
configuration; this command never guesses or writes that repository.
The suggested configuration must itself fit the normal bounded-input contract
(1 MiB, nesting depth 12), not merely the larger output envelope; otherwise
design refuses rather than offering configuration that subsequent reads reject.
Existing role IDs, role-object declarations and optional template provenance
use the same configuration validator as the normal planner.
Template provenance/content digests and exact named placements remain in the
composition. Neither a digest nor a saved reviewable result grants authority.

`activation_authorized`, `mutations_performed`, and
`live_readiness_verified` are always false. Prepared-plan source pull,
notification, exact-generation approval, authenticated owner execution,
bounded complete outfitting and actual Actions-job proof remain separate
integration obligations in the [runner loadout plan](../../efforts/active/linux-role-fleet/runner-loadouts.md).
Removing a registrar entry is never destructive consent.
