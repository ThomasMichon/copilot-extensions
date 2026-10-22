# fleet-manager

Standalone fleet system home and thin CLI. **Current scope: offline configuration/
capacity planning, explicit release-snapshot inspection, installed-client
resource diagnostics, explicitly authenticated artifact staging and an explicit
admitted-client construction/health and owned runtime-selector transitions, a local current-observation
storage API, and an opt-in authenticated NEW
Gateway/SDK/subscribed-controller boundary.**
The [resident boundary](docs/gateway-runtime.md) is a first bounded slice:
no production Gateway/index offload or managed install/update/uninstall,
telemetry sink or fleet resource mutation is claimed.
Broader fleet delivery remains the [canonical effort](../efforts/active/linux-role-fleet/README.md).

The opt-in [service Gateway SDK](docs/gateway-service-profile.md) accepts a
code-registered provider-neutral public-trust adapter. Vendor implementation,
login and trust acquisition stay downstream; the existing `jwt-pop-v1` request
verifier and ordinary CLI/default identity behavior remain unchanged.

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

The explicit installed-package [role/provider compiler](docs/role-deployment-plans.md)
joins selected role bindings with this same planner and code-registered provider
contracts. Its Komodo projection names resources and independent bootstrap
requirements without contacting Core, installing a manager or authorizing
effects. Default planning preserves its optional configuration namespace without
selecting it; actual managed lifecycle and production readiness remain separate.

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
Default planning and inspection use only the standard library and have no
server, Docker, Copilot, marketplace or private-provider dependency. The
explicit `release-verification` extra adds maintained cryptographic support
for the separate [pinned-key authentication API](docs/release-authentication.md);
it does not select a trust policy, install a service or authorize activation.
The [retained artifact-staging API](docs/artifact-staging.md) uses that explicit
verification capability and the existing preflight to freeze selected bytes
in a context-owned private spool. It does not execute artifacts or publish a
runtime selector. Consumers obtain bounded immutable byte snapshots with current
authentication, not reusable filesystem paths. Default planner/doctor commands
do not stage anything.

The [explicit selected-release intent](docs/release-selection.md) contract binds
an independently trusted current owner decision to exact manifest content,
sequence, scope and role closure. It does not choose or authenticate a decision,
persist admission, execute artifacts or grant activation. Signed or
higher-sequence releases are not automatically selected.

The separate [client admission](docs/client-admission.md) and
[construction](docs/client-construction.md) transitions persist an explicitly
selected release before building its client-only candidate and checking its
real installed console. Construction rechecks current policy and explicit
API/config/retained-data compatibility, preserves failed-candidate ownership for
retry, and never publishes an active selector. These prerequisites do not
complete managed install/update/uninstall or establish fleet readiness.
The separate [owned runtime activation](docs/client-runtime-activation.md)
transition freshly revalidates that candidate before publishing physically
attributed local selectors. It supports bounded, proven forward retries, not
frontdoor registration, rollback, removal or a live fleet rollout.

The [current-observation storage API](docs/current-observations.md) provides
separate explicit SQLite current slots, origin-generation comparison, atomic
accepted-update positions and bounded scoped replay/queries. It is a trusted
local prerequisite, not an authenticated push endpoint, complete fleet roster,
resident subscription service or a new domain writer. Default CLI commands do
not open the store, and no installed service or producer is enrolled by it.

Explicit `auth` and `server` extras supply maintained cryptographic dependencies for the
[new authenticated Gateway](docs/gateway-runtime.md).

The optional stdlib-only [desired manifest and publication primitives](docs/gateway-manifests.md)
validate role slots/namespaces and publish coherent process-local desired state.
They do not replace startup Gateway configuration, authenticate sources or
connectors, route service tunnels or establish restart-safe configuration custody.

The explicitly selected [outbound service profile](docs/gateway-service-profile.md)
adds coherent live service policy and actual TLS HTTP/UX/response-stream routing
over authenticated outbound connectors. Its optional `service-gateway` extra
does not change default client dependencies or the legacy serving profile.
An explicitly selected in-process handler can consume channel-derived caller
context without forwarding identity headers to an HTTP backend; account and
resource authorization remain the embedding service's responsibility.
Container, protected-proxy/provider and production adoption remain separate gates.

The explicit [runtime identity verifier](docs/runtime-authentication.md) adds
optional maintained-library JWT/DPoP checks and a bounded single-owner replay
fence. It is not enrollment, scoped policy authorization or a Gateway listener.
Its `runtime-authentication` extra does not change default client dependencies,
select a provider, acquire credentials or impose a human-presence timer.

The isolated [current-policy admission API](docs/runtime-admission.md) maps that
verified request evidence through an independently trusted, current enrollment
snapshot to exact observation branches or existing operation-intent grants.
Finite internal permits revalidate current policy before lookup/effect/emit;
they are not serialized backend credentials. The library does not authenticate
its snapshot source, wire a resident consumer or implement provider renewal.

The optional stdlib-only [reverse-listener coordinator](docs/reverse-listener-reservations.md)
binds a finite supplied-owner admission to retained candidate custody and
generation-checked publication, preserving unresolved effects under their
original correlation. It does not supply a production SSH/policy/discovery
adapter, choose candidates, change forwarding grants or select a reconnect
loop. Default commands do not activate it.

## Thin client artifacts and local diagnostics

`fleet-manager` builds as a dependency-free runtime wheel. Its wheel carries the
byte-identical canonical `versioned-runtime` primitive as a package resource;
the sdist also carries that primitive and the same build helper, so rebuilding
outside the repository does not depend on a plugin or sibling checkout.
Canonical checkout resources are required when the shared library directory is
present; included sdist copies are optional comparison inputs in that case,
never a fallback for a missing canonical file. Outside a checkout, both included
sdist resources are required. Missing or conflicting inputs fail the build. The helper is shared
with `agent-index-service`, not a second maintained primitive or installer.

```text
fleet-manager doctor
fleet-manager doctor --role client
```

`doctor` checks the imported package/version against its installed distribution
and the package initializer/runtime resource and executable companions against their SHA-256 wheel RECORD
entries. It returns local installed-resource evidence, not a trusted release
attestation, whole-package integrity proof or controller/service health. A
missing, unreadable, linked, oversized or mismatched resource is an error,
including linked or non-directory ancestors;
there is no source-checkout, plugin or PATH fallback. No remote endpoint is
queried, no credentials are acquired and no hosting role is accepted.
Exit 0 means that local resource evidence passed; exit 1 means invalid or
unavailable evidence. The command creates no configuration, service or runtime.

Artifact staging rechecks every executable resource against those admitted
digests before execution and loads the primitive and its companions from
retained bytes. Later filesystem replacement cannot change a lazy component
import during the staging lifetime.

Ordinary explicit package-manager installation/removal remains supported.
Native first-touch tests install the built wheel into a fresh package-manager
target without dependencies/network access, run its installed console entry point
and diagnostics, and remove that distribution while preserving separate user
configuration. Isolated Python excludes ambient site packages, so the runner's
own client installation cannot supply a missing dependency or false pass.
Using a fresh target avoids manufacturing and deleting a temporary interpreter;
it does not stand in for a managed version-slot deployment.
This proves client artifact isolation, **not a managed
install/update/rollback/uninstall implementation**. Receipt-owned version-slot
selection, configuration custody and controller/node lifecycle remain open
under #6424/#6240. No marketplace hook installs or updates this standalone client.

Required changed-path native CI keeps real wheel/RECORD, sdist-to-wheel,
linked-helper refusal and first-touch install/removal proofs. The additional
native build/refusal matrix is marked `client_distribution_full` and runs in
unfiltered standalone validation and the existing manual native CI lane.
Local full validation uses `tools/run-standalone-tests.py fleet-manager`;
`PYTEST_ADDOPTS='-m "not client_distribution_full and not observation_retention_full"'`
selects the same fast
portfolio as required PR CI without removing the real installation boundary.
The required fleet selection retains its 120-second deadline; unfiltered local
and explicit manual runs retain the production-size replay window and full
artifact matrix under the existing 600-second budget. See
[TESTING.md](../TESTING.md) for the canonical portfolio and job budgets.
Each artifact subprocess retains its 30-second limit.

Vision reconciliation: this prerequisite closes part of machine-fleet
`unified-coordination-product` and the reviewed standalone-system distribution
boundary. It reuses immutable-runtime primitives without activating one.

## Release roles and snapshot inspection

`fleet-manager client-state` supplies separate explicit initialization,
read-only inspection, freshly authenticated admission, construction and owned
runtime-selector activation operations. None implicitly performs another
transition, selects a release, registers a frontdoor, installs a hosting role or
authorizes rollout. See [Client release admission](docs/client-admission.md) for
trusted inputs and monotonic state, [construction](docs/client-construction.md)
for candidate health, and [runtime activation](docs/client-runtime-activation.md)
for physical selector ownership, retries and uncertainty.

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

No actual released Gateway/node artifacts are claimed by these declarations;
the opt-in source Gateway runtime is not a released deployment.
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
release distribution and node/Gateway operation remain pending. The separate
[release authentication API](docs/release-authentication.md) implements explicit
pinned-key verification, not installation: default planning remains dependency-free,
and GitHub-attestation selection explicitly refuses until its own verifier lands.

### Actual artifact integrity

The separately invokable, read-only
[role-selected artifact preflight](docs/artifact-preflight.md) hashes actual
bounded local files against that same manifest:
`python -m fleet_manager.artifacts --release release.json --sources sources.json --role client`.
It reports `integrity-match`, not installed/active/ready or authenticated.
It never reads unselected server artifacts, installs packages or authorizes
activation. [Release authentication](docs/release-authentication.md),
[immutable staging](docs/artifact-staging.md) and explicit selection are
separate prerequisites, not installation or activation. The independently
owned client lifecycle/diagnostics lane remains #6424.

## Operation and receipt shapes

The first [operation contract](docs/operation-contract.md) adds immutable,
content-bound intent and target-receipt records as a Python API only. Strict
scope/generation/digest correlation distinguishes acceptance, effect outcome and
uncertainty. Parsing is not authentication, durable admission, execution,
monotonic receipt ordering or objective settlement. This operation contract
adds no CLI mutation or resident control runtime; the separate opt-in
[NEW read/credential Gateway](docs/gateway-runtime.md) does not execute these
operation commands. Existing fixed-service routing contracts remain owned by
`agent-fleet-contracts`.

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
