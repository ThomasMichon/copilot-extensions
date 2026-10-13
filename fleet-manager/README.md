# fleet-manager

Standalone fleet system home and thin CLI. **Current scope: offline configuration/
capacity planning and explicit release-snapshot inspection.** No controller, provider invocation, credential
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

## Release roles and snapshot inspection

```text
fleet-manager inspect --release release.json --observation observation.json --role client --authority example --installation local-example
```

This is an **offline contract**, not an installer or live health probe. It never
downloads artifacts, queries a daemon/provider, finds ambient state, acquires
credentials or starts a service. `installed` and `ready` describe assertions in
the explicitly supplied observation; output always has
`evidence: supplied-snapshot-only`, `live_readiness_verified: false`, and
`mutations_performed: false`. Hash matching supplies integrity binding, not
signature verification, authentication or authorization.
Results preserve `inspected_at`, `observed_at` and `valid_until`. The latter two
are explicitly null when no observation was supplied. Retained results are
historical assertions: consumers must reject freshness at or after `valid_until`
and must not infer readiness from null, expired, future or incomplete evidence.
Preserved time bounds do not authenticate the supplied observation.

The strict schema-v1 `fleet-manager.release` has:

- `id`: bounded release display identity; inspection additionally binds the
  SHA-256 of canonical manifest content.
- `components`: nonempty object keyed by bounded IDs. Each component declares
  `kind` (`client`, `control`, or `executor`), lowercase 64-character
  `artifact_sha256`, `api: {min, max}` and nonempty unique `data_schemas`.
  API/schema values are integers 1-65535, never booleans.
- `roles`: exactly `client`, `gateway` and `node`, each with a nonempty unique
  list of declared component IDs. All components must be referenced.
  The client role cannot require a control/executor component. This is declared
  role isolation, not analysis of executable dependencies.

No actual released Gateway/node artifacts are claimed by these declarations.
A manifest is explicit input, not a generated product release or proof of
artifact availability. Role closures must be stated directly; there is no
dependency resolver, artifact URL, executable path or apply operation.

The strict schema-v1 `fleet-manager.installation-observation` declares
`authority`, `installation`, canonical `release_digest`, finite epoch
`observed_at`/`expires_at`, boolean `complete`, and `components`. Each component
observation has `artifact_sha256`, selected integer `api`/`data_schema`, and
`state` (`installed`, `ready`, `unknown`). Expected authority/installation are
mandatory CLI inputs; a mismatch is an error, not a different state source.

An omitted observation produces `unknown`; a supplied unreadable/malformed file
is an error. A mismatched release digest takes precedence and produces
`incompatible` even when the supplied observation is stale or incomplete; this
states a manifest binding mismatch, not the live host's state.
For a matching release, future, expired or incomplete observations produce `unknown`.
Only fresh complete observations can assert `missing`. A release/artifact/
API/data-schema mismatch produces `incompatible`. Required components alone
determine the selected role's result; observed unrelated components do not add
server dependencies to a client. Aggregate priority is incompatible, missing,
unknown, installed, then ready. Expiry at the inspection time is stale.

Exit 0 means compatible `installed`/`ready` snapshot assertions, **not live
readiness**; 2 means missing/incompatible/unknown, and 1 invalid input/I/O.
Input bounds, duplicate-key refusal and non-secret diagnostic rules are shared
with the existing planner: at most 256 components per manifest/observation and
256 entries in any role closure, 1 MiB input and depth 12. Exact-field diagnostics
name static expected keys, never unknown supplied keys or values.
A future trusted observer/installer must produce
attributable live evidence and verify actual dependencies; this slice does not
implement that authority or replace its acceptance gate.

Vision reconciliation: closes the first contract portion of machine-fleet
`unified-coordination-product` under [Fleet Control V2](../efforts/active/fleet-control-v2/README.md).
Ordinary standalone client planning remains unchanged. Live lifecycle, signed
release distribution and node/Gateway operation remain pending.

## Operation and receipt shapes

The first [operation contract](docs/operation-contract.md) adds immutable,
content-bound intent and target-receipt records as a Python API only. Strict
scope/generation/digest correlation distinguishes acceptance, effect outcome and
uncertainty. Parsing is not authentication, durable admission, execution,
monotonic receipt ordering or objective settlement. No CLI mutation or resident
control runtime is added; existing fixed-service routing contracts remain owned
by `agent-fleet-contracts`.

An explicit Python [local operation journal](docs/operation-ledger.md) now
persists admission/outbox/audit and correlated receipt updates transactionally.
Its combined allocation-admission API also persists one exclusive identified
resource claim with intent/outbox/audit. Claims survive every receipt outcome; explicit cooperative retirement evidence
can release a claim and permit a higher allocation generation. No expiry-based
release exists. This is local storage evidence,
not authentication, a signed grant or an effect-owner fence.
The journal is not automatically installed or activated and supplies no executor
or resource fencing. Generic admission does not claim allocations.
The offline CLI remains non-mutating.
Its explicit result-reconciliation API commits a node spool's receiver cursor
with receipt/outbox/audit updates, avoiding stale history replay after a lost
receiver acknowledgment. Node identity authentication and transport remain
the caller's responsibility.

An explicit [node journal](docs/node-journal.md) now persists local acceptance,
cooperative SQLite effects and correlated receipts with an append-only result
spool. External attempts conservatively persist UNKNOWN before execution and
cannot be retried blindly. Allocations instead require identified held ownership
at acceptance and within the cooperative SQLite effect transaction; generic
unbound effects and external allocation attempts are refused. Ownership survives
outcomes until a trusted SQLite-only retirement callback commits with permanent
release evidence and owner removal. Stale effects remain refused after
reassignment. This protects correctly implemented
local SQLite adapters, not external workspaces or resources shared across nodes.
This Python API is not a node service, remote
executor, authenticated reconnect protocol or distributed fence; no role is
installed or activated automatically.
