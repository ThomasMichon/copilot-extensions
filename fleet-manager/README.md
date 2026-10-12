# fleet-manager

Standalone fleet system home and thin CLI. **Initial slice: offline configuration
and capacity planning only.** No controller, provider invocation, credential
lookup, install/uninstall, telemetry sink or resource mutation is delivered yet.
Those remain the [canonical effort](../efforts/active/linux-role-fleet/README.md).

```text
fleet-manager plan --template recommended.json --config adopter.json --inventory live.json --fleet-id example --machine-limit 5
```

Omit `--config` only when there is no existing adopter configuration. A supplied
missing/unreadable file fails; it never silently becomes an empty fleet.
This slice accepts strict bounded JSON, not YAML yet. The harness's later
template/paired-repo adapter owns YAML loading and private provider resolution.
All input snapshots must be non-secret; suggested config preserves adopter
extensions and custom roles, so do not put credentials into those declarations.

## Schema-v1 inputs

Every input has `version: 1` and its stated `schema`:

- `fleet-manager.template`: `id`, `revision`, `execution_hosts` and a `roles`
  object. Each recommended role has `kind` (`service` or `execution`), `required`
  and `available` booleans. An essential future unsupported role blocks a complete
  proposal rather than claiming readiness.
- `fleet-manager.config`: `fleet_id`, `revision`, `capacity` (`machine_limit`,
  `execution_hosts`), `roles`, optional `template` provenance and `extensions`.
  Existing values and unknown role details are retained. New recommendations add
  absent roles, never delete or overwrite custom capabilities.
- `fleet-manager.inventory`: `provider`, `revision`, `fleet_id`, `observed_at`,
  `expires_at`, `complete`, `creation_allowed`, nullable `quota_remaining` and
  `hosts`. Each host has `id`, `owned`, `ready`, and observed role states (`ready`,
  `installed`, `unavailable`, `unknown`). `owned` means owned by this named fleet,
  not merely visible to the operator. Providers remain authoritative.

Epoch snapshot times are integer seconds. Unknown quota is `null`, not zero and
not the local configured limit. All retained owned hosts consume budget even
when offline. Unowned hosts are retained/observed but cannot satisfy managed
capacity. An incomplete or expired inventory cannot suggest host creation.

The recommendation reserves one service host before optional execution hosts.
This is an aggregate machine-capacity proposal, **not** service placement,
memory/CPU admission or proof all roles fit on one host. Deployment must still
resolve service budgets, storage, credentials and host roles before activation.
No host is retired to free capacity; insufficient capacity remains blocked.

## Output and safety

The JSON `fleet-manager.plan` includes preserved suggested config, per-role
observations, config suggestions, aggregate proposed creation count and explicit
blockers. `review-required` means a nonblocked proposal, **not a ready fleet**.
CLI exit 0 means the proposal was produced, 2 means blockers, and 1 means invalid
input/I/O. No apply command exists. `mutations_performed` is always false and
`cleanup_actions` is empty.

Each plan binds exact canonical input/policy digests and inventory expiry.
`verify_plan` rejects altered, expired, blocked or changed-assumption plans;
this integrity/freshness check is not authentication or permission to apply.
A future controller must independently authenticate and re-admit resources.
Explicit recorded template provenance that differs from the selected template
ID/revision adds `template-provenance-mismatch`, so saved-plan verification
rejects it until reconciled. The recorded provenance and custom config remain
preserved. Optional absent provenance still produces a review suggestion without
inventing or silently stamping a prior template identity.

Inputs are limited to 1 MiB, depth 12 and 256-object/collection items. Duplicate
JSON keys, invalid types, identity mismatches, unknown wire fields and nonfinite
numbers are rejected; planner and input-file diagnostics omit document values.
Command-line syntax diagnostics use argparse's standard messages, which can
include supplied arguments; never pass credentials or secrets as CLI values.
Output limits are separately
enforced: plans allow 4 MiB, depth 15 and 513-object/collection items to
accommodate preserved config plus joined recommendations and observations.
Output hashing, saved-plan verification and CLI serialization use these output
bounds and output-specific diagnostics, not the individual input limits.
The package uses only the standard library and has no server,
Docker, Copilot, marketplace or private-provider dependency.
