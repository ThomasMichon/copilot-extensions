# Standalone PR watch service

For the explicit independent Unix HTTP repository-cache/continuing-event profile,
see [local operation](LOCAL.md). It uses the same foreground host and writer,
without requiring Core/Gateway ingress or service credential enrollment.
The connector profile below retains its existing defaults.
The optional [cache-only reviewer query](REVIEWER.md) shares that local host and
requires explicit activity selection; it adds no protected write owner.
The [finite comparison CLI](EVALUATION.md) checks those cached facts/actions
against independently supplied native evidence without provider acquisition.

For explicitly selected generation-three filtered populations, inert named sets,
startup sponsorship and owned dynamic subscriptions, see
[filtered populations](../../plugins/agent-pull-requests/docs/filtered-populations.md).
This adds `population-*` client forms and a fresh `repository-filtered/v1`
profile; it neither migrates retained state nor enables a provider by default.

`agent-pull-requests-service` hosts the existing PR replay store, Gateway handler,
connector and native observation cycle. It runs without Copilot, marketplace
hooks or an interactive client session. It is not a deployment controller,
credential issuer, second registrar, arbitrary callback runner or native-daemon
installer. Native PR commands remain independent of this distribution.

Install its wheel and explicit dependencies through the normal seven-day
dependency policy. The native snapshot executable is a separately selected
Worktrees installation/artifact, addressed by the trusted host configuration;
this service's Python dependency resolver must not select its installation or
substitute an unrelated published package. Released wheels have ordinary versioned
dependencies, not paths escaping into a plugin checkout. Repository development
tests select the same checkout's PR and Fleet sources explicitly:

```text
python tools/run-standalone-tests.py services/pr --prepare --timeout 180
```

The Python source/test files are admitted to strict MyPy on the Linux and
Windows analysis targets at the declared Python 3.10 minimum:
`python tools/typecheck.py service-pr --prepare`, then
`python tools/typecheck.py service-pr --strict`. JSON documents, runtime
serialization and dynamically imported Gateway fixtures retain their actual
validation and dependency boundaries. The project-specific checker hash lock
adds the existing cryptography SDK and its bundled types; no shared checker lock
or runtime dependency is changed. Static admission does not qualify an installed
process, image or deployment.

Distribution fixtures retain immutable snapshot inputs and make only their
owned disposable build directories writable for backend-generated metadata.

### Canonical attribution helpers

The wheel includes the payload-only attribution plugin's canonical profile and
compact-comment preparation helpers under
`agent_pull_requests_service._attribution`. Direct checkout builds, archived
sdist rebuilds, retained development snapshots and release materialization use
the same bytes. The existing MIT notice ships with the distribution. No
attribution runtime, Worktrees Python import or global import-path change is
required; direct-script attribution callers retain their existing behavior.
Missing canonical inputs or mismatched included helpers refuse construction.

These are preparation-only package assets. The optional
[preparation host](#optional-preparation-only-hosting) binds them to an explicitly
selected native producer; their presence alone enables no route, credential
authority or publication.

## Explicit host inputs

The trusted launch owner supplies an absolute JSON configuration reference:

```json
{
  "schema": "agent-pull-requests.service",
  "version": 1,
  "root": "/data/pr-watch",
  "namespace": "installation-a",
  "fleet": "fleet-a",
  "gateway": "gateway-a",
  "slot": "pr-watch",
  "gateway_origin": "https://gateway.example.test",
  "proof_key_file": "/run/secrets/service-proof.pem",
  "access_token_file": "/run/secrets/service-access",
  "gateway_ca": "/run/config/gateway-ca.pem",
  "bindings": [
    {
      "principal": "caller-a",
      "target": "repository-a",
      "consumer": "dispatch-a",
      "account": "account-a",
      "repo": "example/repository"
    }
  ],
  "observations": [
    {
      "account": "account-a",
      "repo": "example/repository",
      "command": "/usr/local/bin/agent-worktrees",
      "credential_command": [
        "/usr/local/bin/fleet-manager",
        "credential",
        "--config", "/run/config/credential-worker.json",
        "--source", "forge-reader",
        "--account", "account-a",
        "--forge", "{\"provider\":\"github\",\"api_base\":\"https://api.github.com\",\"repository\":\"example/repository\",\"permission\":\"observe\",\"authentication\":\"bearer\"}"
      ]
    }
  ],
  "interval": 5,
  "operation_seconds": 60,
  "drain_timeout": 60
}
```

Use the credential worker's actual
[`ForgeScope` contract](../../fleet-manager/docs/gateway-runtime.md) for the
selected provider; the native snapshot and worker must agree exactly. The
example identifiers are not enrollment, provider-policy or placement grants.
All executables and credential/configuration references are explicit absolute
paths. An optional immutable `prefix` argv selects an attributable interpreter
invocation instead of the native executable. `credential_command` is required:
this host never falls back to a logged-in CLI or another account.
These are trusted deployment inputs, never fields accepted from subscribers.
Do not put tokens or arbitrary secrets in command arguments or configuration.

Configuration is bounded to 1 MiB and 128 entries per binding collection.
Unknown/duplicate fields, ambiguous scope mappings and missing observation
scopes refuse. Interval, operation and drain budgets are integer seconds in
1..300. The operation budget is a local transaction deadline, not token expiry
or a fabricated placement lease.

The explicit launch authorizes only this foreground process's local work.
SIGINT/SIGTERM withdraw later work and signal the existing bounded drain.
If that drain times out, the foreground retains its daemon admission and the
same writer/service instance while retrying its bounded `close()` to actual
settlement. A completed or cancelled owner task is not proof that disk,
connector or Gateway work stopped. Repeated caller cancellation cannot cancel
this retained cleanup or mark the instance finished early; the original outcome
is reported only after the writer releases. This does not change production
expiry or grant replacement, cutover or recovery authority.
Real Gateway TLS/PoP admission authorizes service attachment and each remote
caller's lifetime; the existing typed credential worker authorizes forge reads.
Neither labels nor decoded JWT claims grant these permissions. Service access
tokens are reread on every admission; the external P-256 proof key is loaded
once, so key rotation requires a new connector/process. No credential enters
durable replay state or readiness diagnostics.

## Provision, serve and observe

```text
agent-pull-requests-service provision --config /run/config/pr-service.json
agent-pull-requests-service serve --config /run/config/pr-service.json
agent-pull-requests-service ready --status /data/pr-watch/service-status.json
```

Provision is an explicit first-creation operation and never repairs/replaces
existing state. Serve adopts existing valid state; it does not provision it.
Use a dedicated root, never the native watch owner's state. One host-local
writer lease covers the actual observation/serving work. A failed/timed-out
drain does not release ongoing in-process custody or promise rollback.
Provision binds the entire replay envelope to the configured namespace and
exact fleet/Gateway/slot identity. Every transaction rechecks that binding;
changing those identifiers cannot adopt the same retained volume. Ordinary
restart, credential-reference rotation and compatible code updates preserve
the binding and original registration/event identities. These local labels
prevent wrong-volume adoption; they do not authenticate a caller or fence a
different host.

The standalone host requires the versioned
`agent-pull-requests.watch-service-store/v1` disk envelope. Earlier unbound
replay stores refuse without rewriting them; there is no automatic migration,
identity rebinding or downgrade fallback. Older readers also refuse the bound
envelope. Preserve an existing volume for a separately admitted migration
rather than provisioning over it or stripping its binding to make an old image
start. Remote request/replay/ACK payloads and the native callback store are
unchanged. A supported image update must retain this storage contract as well
as separately proving writer transfer and data compatibility.

### Optional native reports

The [reviewed report-host contract](../../efforts/active/durable-pr-watch-delegation/standalone-report-host.md)
adds a strict optional `reports` object to the existing version-1 host config:

```json
{
  "reports": {
    "response_bytes": 8388608,
    "bindings": [
      {
        "principal": "caller-a",
        "target": "repository-a",
        "source_config": "/run/config/worktrees.json",
        "scope": {
          "provider": "github",
          "endpoint": "github.com",
          "account": "account-a",
          "repo": "example/repository"
        },
        "context_id": "selected-native-report"
      }
    ]
  }
}
```

This partial example grants no enrollment or provider access. Each entry must
match one existing admitted caller/target and its exact account/repository.
Only the existing GitHub.com adapter is selected. Unknown/duplicate fields,
unsupported scopes and non-absolute native configuration references refuse.
The explicit integer response ceiling is 32768..8388608 bytes. It reaches the
actual connector handler; [bounded v2 reports](../../plugins/agent-pull-requests/docs/report-bounded.md)
also require the executing SDK request's selected allowance. V1 and ordinary
watch/workspace responses retain their existing bounds.

The launch owner must explicitly supply compatible Worktrees native APIs in
the selected service process through its existing distribution mechanism.
Missing optional support refuses before connector admission, without probing
credentials. The service does not install Worktrees or select an executable,
token command or import path from this object. Default hosts require no
Worktrees Python import or dependency and keep their 32 KiB handler ceiling.
Loading, provisioning and starting the host do not resolve report credentials.
Each admitted request uses the native owner's current policy and authenticated
selected account under the existing request, operation budget and drain.
Observation credentials are not report credentials.

Reports are transient: no report cache, replay event or disk migration is added.
Removing the optional selection restores the baseline without rewriting state.
Source fixtures qualify fresh isolated hosting, not live providers, native
Windows execution, retained hosts, installed activation or rollout.

### Optional native read-only workspaces

The [reviewed native-host slice](../../efforts/active/durable-pr-watch-delegation/native-workspace-host-slice.md)
adds one explicit `workspaces` configuration object. Its absence preserves the
watch-only configuration and volume. For an already authorized ADO observation
binding, the launch owner may add:

```json
{
  "workspaces": {
    "bindings": [
      {
        "principal": "caller-a",
        "target": "repository-a",
        "command": [
          "/usr/local/bin/python", "-I", "-m",
          "agent_worktrees.pr_review_evidence_cli"
        ],
        "scope": {
          "provider": "azure-devops",
          "endpoint": "https://dev.azure.com/example",
          "project_id": "project-id",
          "repository_id": "repository-id",
          "principal_id": "provider-authorized-user-id"
        }
      }
    ],
    "cache_bytes": 67108864,
    "cache_generations": 128
  }
}
```

This is a partial configuration example, not a credential or enrollment grant.
The principal/target must already select an admitted `bindings` entry. Its
account/repository select the existing observation credential command, which
must actually return an ADO `observe` binding for that endpoint/repository.
Canonical project/repository IDs and `connectionData.authorizedUser.id` must
match actual native evidence. Configuring another principal cannot relabel a
credential. Endpoint, commands, credentials and state roots are never remote
request inputs. Unknown/duplicate entries refuse.

The absolute immutable command prefix must select an existing native
[review-evidence exporter](../../plugins/agent-worktrees/docs/pr-review-evidence.md),
not a shell/batch shim, another installation or ambient CLI auth. Executing
capability discovery is shared once per selected host reader; each explicit
load/sync performs one finite export with the remaining operation budget.
Missing/incompatible support makes this optional read unavailable, not a
fallback or a reason to disable legacy watches. Acquisition stays
`configured_native_provider`, separately advertised as `read_acquisition`;
it is never relabeled `owner_executed_review_evidence/v1`.
The selected exporter must additionally advertise `enclosing_scope_credentials`.
The host selects its contained-worker form so credential children remain in the
same actually confirmed native scope, not a nested independent POSIX session
whose caller-exit watcher might finish after Gateway drain.

The existing authenticated `/v2/review/<target>/...` route provides load, sync,
lookup/chunk materialization, inspect and body-free conversation events. No
write-context factory is configured: submission, feedback/handled writes,
merge/rebase/CI actions and Dispatch emission remain unavailable.
Workspace/conversation wire-v1 readers and the strict watch capability response
remain compatible. An older host rejects this optional configuration rather
than silently enforcing it; no old command is deprecated.

For this opt-in configuration, **fresh** `provision` selects the existing
owner-store v3 envelope atomically and creates its bounded `review-cache` child.
The root must be dedicated and empty. Cache capacity is explicit: 64 KiB..1 GiB
and 1..10,000 retained generations. A partial provisioning failure retains its
files and refuses blind reprovisioning. `serve` checks the existing v3 journal
and cache descriptor under its writer before connecting; it never creates,
adopts, repairs or downgrades either. Existing watch-only volumes require the
separately admitted storage/lifecycle migration, not merely this new config.
Old readers must refuse unsupported layouts without rewriting them.

One request/service deadline covers discovery, export, decoding, cache CAS and
journal commit. Withdrawal closes the request and prevents subsequent work.
The existing synchronous native primitive may still need its bounded execution
and actual cleanup; the original request/drain thread and service writer remain
held throughout, including retry of an unconfirmed cleanup. A bounded drain
timeout is not completed child cleanup or proof that a disk commit failed.
Cache publication and journal commit remain separate: after an ambiguous error,
lookup/materialize the retained generation and read the journal's existing
cursor before deciding what remains. Neither a cache conflict nor a lost
response authorizes an automatic re-read or a new operation identity.
Canonical POSIX cleanup errors retain an identity-safe retry over the original
unreaped leader and remaining streams; retry never rediscovers a process by PID
after reaping or delivers settlement before complete cleanup.

Focused source tests exercise real native/exporter/credential processes and
the existing Gateway/disk boundary using synthetic provider inputs. Those are
not installed/live-provider, retained-container or shadow acceptance. Source
availability does not authorize configuration, provisioning or service rollout.
The standalone foreground-process fixture also opts into the actual strict
workspace configuration and fresh provisioning. Its CLI facade and supplied
client cross real Gateway TLS/PoP to the foreground entry and a synthetic
exporter subprocess, exercising load/sync, metadata materialization, same-head
events, nonevent polls and retained-volume restart without another export.
The fixture verifies forwarding of the credential-command reference, not a
live exchange, and does not qualify executable client binding discovery.

An ambiguous durable request reconciles its original request/registration/event
identity; disconnect or revocation is not proof that an in-flight commit failed.
The native snapshot command retains its existing bounded execution contract.

Readiness is a passive local snapshot with at most three seconds' freshness.
Only the held writer publishes it. Missing/stale/future/corrupt snapshots fail
the probe; readiness requires connected serving and a successful observation
cycle with no unavailable/unbound targets. This is not a placement, promotion,
safe-update or cross-host fencing receipt. The host opens no additional
unauthenticated HTTP listener. Its supervisor owns restart and escalation.
CLI failures name the operation and exception class without echoing external
references, provider error details or credential material.

### Optional fresh-baseline generation associations

An unextended native workspace may explicitly set
`workspaces.generation_associations` to `true` before fresh provisioning.
The selected executing core must declare
`agent-pull-requests.workspace-generation-associations/v1`; an absent or
incompatible declaration refuses this selection before provisioning.
Omitting the field retains the existing workspace behavior and wire contract.
This writer profile cannot compose with preparation or comment mappings.
Terminal reads require the separate retained transition below.

The existing cache owner rejects definite CAS conflicts and full-cache outcomes
before recording an observation or reservation. After that guarded admission,
the service writer and ReplayStore reserve body-free original
generation/scope/observation/cursor identities before cache writes.
Only confirmed publication marks an association confirmed. Distinct immutable
generations retain separate identities even when their rounded observations
share a journal cursor. Cache objects, digest inputs and pointer formats do
not change. Metadata is bounded by the original cache generation limit and
an envelope reservation of 1 KiB plus 4 KiB per configured generation, within
the existing owner byte ceiling. Exhaustion refuses publication, not eviction
or an automatic capacity increase.

Interrupted publication retains its original pending association. The trusted
owner can reconcile that generation and its original cache CAS without another
provider acquisition or journal event; a different CAS or epoch refuses.
Missing or newer cache state is not permission to promote an old pointer.
Pending and confirmed associations both survive process restart; neither is
a terminal live admission or evidence of a fresh provider read.

`serve` validates the selected existing storage, never adopts it. Adding this
selection to a previously provisioned baseline refuses instead of backfilling
associations. Removing it or selecting a strict older reader also refuses the
expanded envelope without rewriting it. This profile authorizes no installed migration, native
watch reassignment, unknown-effect recovery, provider access or service stop.

#### Original native-target capture

A fresh baseline may additionally select
`workspaces.native_generation_associations: true`. It requires
`generation_associations: true` and the executing-core declaration
`agent-pull-requests.workspace-native-generation-associations/v1`.
The original v1 selection and default publication remain unchanged.

The host captures its admitted native account/repository/PR, full workspace
scope, service binding and journal epoch/absence before native discovery or
acquisition. This request-local capture creates no observation or storage write.
Publication rejects an intervening closure, reopen, newly created conversation
or mismatched capture before recording a new observation or touching the cache.
The existing guarded publication then reserves original target/epoch metadata
in generation-association storage version 2, confirming it only after cache
publication. Original pending CAS and uncertain-result reconciliation remain
with the same owner.

Only fresh provisioning selects this version; adding the flag to a v1 volume
does not migrate it. Removing the flag or choosing an older strict reader
refuses expanded storage unchanged. The retained transition below separately
declares support for combining this profile with `terminal_reads`; the fresh
writer alone is insufficient. It does not backfill targets, change watch
receipts or deadlines, or create terminal admissions. Existing count/byte
limits and pending/confirmed metadata custody remain intact.

#### Recorded-generation terminal transition

For an existing volume provisioned with the v1 association writer, retaining
`generation_associations: true` and selecting `terminal_reads: true` enables
the recorded-cache variant of the existing `adopt-terminal` command. The
executing core must separately declare
`agent-pull-requests.recorded-terminal-cache-adoption/v1`.
Fresh provisioning with both selections refuses; `serve` only validates
already-adopted state. Missing support never falls back to another transition.

Preflight requires confirmed original associations for every current and
noncurrent generation, complete open first-epoch conversations and unchanged
cache limits. Any pending publication, unassociated object, closed/reopened
history, active legacy native observation or unsupported recovery state
refuses unchanged. The cache owner validates each object once. Adoption uses
the existing writer lease, finite budget, replay-before-cache lock order and
complete-envelope capacity reservation; interruption retains valid progress
for explicit retry.

The versioned `recorded_terminal_cache_version: 1` marker selects workspace-only
generation/epoch bindings in the existing terminal ledger. It does not create
native target admissions, replay observations, journal events or cache writes.
Current/noncurrent lookup, chunks and sync comparisons retain the original
bytes and cursors. Partial same-epoch refreshes do not withdraw those recorded
body bindings; authoritative terminal/reopen evidence still fences them.
Fresh validated acquisition establishes the actual native
target through the existing terminal publication path, not the baseline
association writer. Delayed native samples captured before that binding
remain fenced. Retry of a complete adoption validates current terminal-owned
generations without importing them into the old baseline association history.
Removing either selection or rolling back to a strict older reader refuses
the expanded envelope unchanged.

For an existing volume originally provisioned with the native-target writer,
also retain `native_generation_associations: true`. The executing core must
declare `agent-pull-requests.recorded-native-cache-adoption/v1` independently of
the writer and workspace-only reader. This selects
`recorded_terminal_cache_version: 2`, not an upgrade of a v1 association volume.
Fresh provisioning still refuses the combined selection.

The same transition validates every historical target association, including
noncurrent entries, against the selected full scopes and native targets.
It imports generation bindings and native admissions together, using only
confirmed complete open first-epoch associations with actual validated retained
cache objects. The admission cursor is an original association cursor, not the
current journal high-water mark. Every actually waiting coalesced native target
must have such evidence. Unmatched old targets, pending publication, conflicting
history, ambiguous scopes or changed configuration refuse before adoption;
today's target B cannot justify an old wait for target A.

Original watch receipts, predicates, deadlines, frozen events and ACK positions
are unchanged. Native capture after adoption carries the imported epoch before
IO; a sample captured without it, or across close/reopen, refuses unchanged.
Fresh native observations keep their complete native snapshot and triggering
head. No observation, successful watch event, cache rewrite or delivery
settlement is created by importing admissions. Installed migration and
unsupported operation representations remain separate, unqualified outcomes.

#### Original active-operation inspection

The recorded-generation transition can additionally select
`workspaces.retained_active_reviews: true`. This requires `terminal_reads` and
`generation_associations`; either original association version
is supported. The executing core must separately declare
`agent-pull-requests.retained-active-review-adoption/v1`, the original
active-operation recovery library and the
[negotiated inspection receiver](../../plugins/agent-pull-requests/docs/workspace-tools.md#original-active-operation-inspection).
Unselected hosts do not pass new constructor options to prior cores.

The existing `adopt-terminal --config <absolute-config>` command validates
original v1 operation scopes, the complete cache/replay layout and all eventual
extension capacity before its first commit. Prepared intents, selections,
comment mappings and existing compact recovery operations refuse unchanged.
Successful adoption adds `retained_active_review_version: 1` and marks each
imported operation `recovery_only: true` in the same ReplayStore envelope.
Intent, source/generation/revision bindings, attempt identities, native receipts,
unattempted actions and original receipt reservations are unchanged.
Older or unselected readers refuse the expanded envelope without rewriting it.

Imported operations remain permanently recovery-only even in an open first
epoch, after native admissions are imported, and after close/reopen. They
cannot be resubmitted or acquire another attempt. The selected host offers
scoped `inspect` through a separately negotiated read-only capability, not a
new operation in the base terminal body-reading contract. Native and Gateway
clients, including `pr_workspace_inspect`, retain the original inspection
result. Partial adoption remains readable through the original library
inspector; explicit retry validates the same persisted state and completes
missing compatible extensions under its new invocation budget.
The host qualifies a one-MiB response budget through its actual existing
Gateway service binding; an unqualified smaller binding omits the optional
inspection capability instead of advertising an unusable reader. Other
workspace operations retain their existing response bounds.

This observer configures no protected reconciliation factory and advertises
`reconciliation: false`. Its observation credential cannot confirm a native
effect. Actual readback remains with the separately supplied protected owner;
no adoption-created deadline, receipt, acquisition, send, ACK or compact record
is produced. Ordinary provisioning and service startup remain non-migrating.

## Optional preparation-only hosting

An existing workspace binding may additionally select this trusted
`preparation` object (never remote request fields):

```json
{
  "command": ["/usr/local/bin/agent-worktrees"],
  "source_root": "/work/producer",
  "source_config": "/run/config/producer-worktrees.yaml",
  "profile_home": "/home/producer",
  "visibility": "public",
  "allow_anonymous": false
}
```

All six fields are required. Paths are absolute, the command is an immutable
explicit executable/interpreter prefix, visibility is `public` or `internal`,
and anonymity is an explicit boolean. The source must have a unique exact-root
managed mapping in the selected native Worktrees installation. Neither the
service checkout, PR author nor remote labels can supply that identity.
Profiles use only the selected source's repository defaults and explicit
`profile_home` personal/config defaults; ambient instruction directories,
`XDG_CONFIG_HOME`, `APPDATA` and the service account's home are not substituted.
Passive command validation checks structure, not executable availability:
a missing optional preparation executable is handled by discovery, without
preventing ordinary watches or retained inspection from starting.

After passive config loading, the host discovers
`agent-worktrees.comment-origin-capabilities` version 1 once for that selected
invocation before advertising allocation. It requires the
`agent-worktrees.comment-origin/v1` protocol, `explicit_source_root` support and
`effects: none`. Missing, old, failing or incompatible providers disable only
new preparation and produce a generic warning; no alternative command or
identity is tried. Discovery reads no source, profile, credentials or forge.
Native provider delivery precedes this optional host consumer. Older strict
host parsers refuse the new object; existing configurations remain unchanged.
Withdrawal handlers are installed before discovery, which runs off the asyncio
loop under an owned worker. A stop request prevents serving; cancellation,
including repeated cancellation, retains that worker until actual native-child
cleanup finishes. The same ReplayStore instance remains the owner throughout
discovery and Gateway construction.

Only explicit **fresh** provisioning adopts the existing prepared-intent and
selection extensions, while holding the same host-local writer lease used by
the watch service. It never migrates an existing volume or repairs interrupted
provisioning. Serve validates retained storage; it does not adopt it.
New allocation resolves native origin and the canonical profile before the
replay transaction, then the existing selection owner allocates identities and
retains pure composition. The budget-aware native preparer receives the
enclosing allocation deadline; its subprocess timeout uses the remaining caller
budget, capped by the host limit. Legacy checkpoint-only callbacks retain their
existing call shape. Fresh comments and replies are supported, not
automatic owned edits. No write context is installed: `submit` stays unavailable.

The original allocation key recovers its exact retained preview before touching
source, profile or cache. After a provider rollback disables allocation, the
existing `inspect` operation on an opted-in receiver still reads the original
prepared intent and its unadmitted selection stage. This is inspection, not
execution admission; default receivers and protected mutation checks remain
strict. Restoring provider support permits original-key allocation recovery
without recomposition. Storage/identity retention does not authorize rollout,
credentials, protected submission or any live forge effect.

The installed foreground fixture crosses real Gateway TLS/PoP and the public
workspace CLI for allocation, exact recovery after profile/origin withdrawal,
and retained inspection after restart with an unsupported native provider.
Native fixtures are synthetic no-effect providers, not live managed-source or
forge enrollment. The standalone packet separately verifies strict passive
configuration, actual helper composition outside the replay lock, fresh-only
adoption, scope/privacy/profile refusal and independent submission refusal.

### Optional retained comment mappings

A workspace binding with the existing `preparation` configuration may additionally
set `"comment_mappings": true` alongside `principal`, `target`, `command`, `scope`
and `preparation`. Absence or explicit `false` preserves prior behavior.
Only a JSON boolean is accepted; enabling mappings without preparation hosting
refuses passively. This is a trusted host selection, never a remote request field.
The host resolves the executing PR core's existing retained-reader declaration
before selecting its new constructor input. A missing or incompatible optional
core leaves baseline serving intact and receives no new keyword. Explicit fresh
mapping provisioning instead refuses before creating state; it cannot claim to
adopt an unavailable contract. Expected optional import/declaration failures
also report unavailable support, without exposing dependency diagnostics.

Explicit **fresh** provisioning adopts the existing bounded
`review_comment_mapping_version:1` extension under the same writer exclusion
and deadline as prepared-intent/selection adoption. Serving never adopts or
migrates an existing volume. If a selected existing prepared volume lacks the
extension, baseline operations remain available but mapping support is absent;
a configuration change cannot replace the storage/lifecycle owner's migration.
Older strict hosts reject the new field. Binary rollback must preserve a reader
that supports the adopted extension; stripping metadata or downgrading the
retained envelope is not supported.

The actual selected `WorkspaceOperations` binding advertises the existing
[retained-comment contract](../../plugins/agent-pull-requests/docs/workspace-tools.md#retained-acquired-comment-reader)
only while its storage owner supports it. Successful explicit load/sync uses its
already acquired, confirmed cache generation to update bounded body-free mapping
metadata under the original intent/receipt owner. No extra exporter invocation,
credential lookup, watcher or mapping file is added.
`retained-comment` reads that metadata through the normal authenticated Gateway
route without a cache body, native read or protected writer. It stays available
when optional origin/preparation discovery is unavailable; new allocation does
not. Removing the host selection withdraws serving and later ingestion without
deleting original mappings.

The host remains read-only: it does not submit comments or manufacture confirmed
receipts. Existing confirmed and unknown outcomes retain their original meaning.
Current content correlation is neither original-version proof nor feedback,
handled, ACK or echo progress. The foreground TLS/PoP fixture seeds one synthetic
original receipt **before** starting the writer, then crosses the real native
exporter subprocess and workspace CLI to qualify load/sync, mapping retention,
and restart after optional origin-provider withdrawal. It does not qualify a
live native send, provider enrollment, production migration or rollout.

## Optional terminal read hosting

The trusted `workspaces` object may additionally set `"terminal_reads": true`
alongside its `bindings`, `cache_bytes` and `cache_generations`. Absence or
explicit `false` preserves baseline behavior; other JSON types refuse. This
selects all workspace bindings on that storage owner and cannot be combined
with preparation or retained comment mappings. An older strict host rejects
the field. The selected executing PR core must declare the exact
`agent-pull-requests.terminal-workspace-read/v1` contract before the host
constructs its terminal-capable store. Missing or incompatible support refuses
the explicit selection before provisioning; an unselected baseline host does
not require the optional declaration.
It additionally requires the core's separate
`agent-pull-requests.terminal-target-capture/v1` declaration before selecting
the budget-aware target callback. The existing remote terminal-read descriptor
and legacy single-argument callbacks remain unchanged.
The selected core must also declare
`agent-pull-requests.terminal-native-watches/v1`: this standalone profile
retains the native watch observer rather than synthesizing a reduced
notification from workspace evidence.

Explicit **fresh** provisioning, under the existing host-local writer
lease and operation deadline, adopts the core's terminal retention, recovery
and cache-retirement extensions in the owner-store v3 envelope. It uses the
core's bounded 8 MiB terminal reservation. Partial provisioning retains its
progress and refuses blind reprovisioning. Serve validates the existing
envelope/cache before connector admission, never adopts or repairs either.
Existing volumes require their own reader/lifecycle transition. Removing the
selection or rolling back to a reader that cannot understand adopted storage
refuses without stripping extensions or rewriting the volume.

An explicitly stopped standalone workspace owner can instead run
`agent-pull-requests-service adopt-terminal --config /run/config/pr-service.json`
against an eligible existing version-three conversation volume and empty workspace cache.
With the executing core's optional
`agent-pull-requests.retained-cache-metadata-adoption/v1` declaration, that same
command also preserves an existing complete open first-epoch conversation
journal and valid null rebuild pointers when no generation objects remain.
Populated journal metadata with active legacy native waits refuses unchanged:
the old envelope cannot prove its original watch-to-workspace association,
and current account/repository remapping must not defeat reopen fencing.
Completed frozen delivery remains eligible; empty-journal bootstrap is unchanged.
It creates no observation, live admission or generation binding. Original
metadata/events, replay positions and cache misses survive reader restart;
later fresh acquisition uses the ordinary terminal-aware load/sync owner.
A selected null pointer needs no journal and remains a cache miss.
Legacy generation objects still refuse unchanged: their original observation
association cannot be recovered from rounded journal timestamps or normalized
projection equality, including same-second close/reopen cases. Their original
readers remain usable. Positive generation migration needs a separately
reviewed durable association contract.
Missing/incompatible optional core
support retains bootstrap-only eligibility and its first-entry empty-cache
check; baseline hosting is unaffected.

Active/compact review operations, closed/reopened or incomplete conversation histories,
all legacy generations, unknown scopes, unsupported extension state
and corrupt/dangling/staged cache objects refuse before adoption. Keep those
volumes with their original usable owner pending a compatible transition.
Selected workspace scopes and account/repository targets must form
a one-to-one mapping in both directions. Every retained replay scope must remain
reachable through the selected consumer/account/repository bindings, including
frozen pending events and completed receipts. Every retained watch receipt requires
finite original delivery deadlines and predicates supported by the terminal
projection (`merged`, `closed`, `head_changed`); the command never invents
deadlines or reinterprets richer predicates. A refused volume retains its
original native review/checks watcher and usable pending/ACK reader.
This includes incompatible acknowledged/cancelled receipts: refuse before
expansion rather than making an exact original registration retry unusable.
This separate command
requires the terminal-reader selection and the same available service writer
lease; a running or unconfirmed drain refuses without stopping the service.
It preserves the original service/namespace binding, eligible replay/delivery
positions and cache scaffold, and performs no acquisition, compaction
or cache retirement. It neither provisions missing state nor repairs it.
Native callback state, unsafe linked lease files and incompatible cache
descriptors also refuse before adoption. Configured cache growth must fit the
core's terminal binding capacity. Request-local cache validation checks both the
original cache checkpoint and owner expiry during enumeration, without mutating
a shared checkpoint. It does not repair or remove unsupported objects.
The exact monotonic operation deadline remains enforced; integer storage
expiry is rounded up so a short budget is not lost at a wall-clock boundary.
The host-local lease does not fence another host or unsupported direct volume
users; their exclusion remains with the deployment owner.
The command returns zero after adoption and one with a sanitized unavailable
diagnostic on failure. Interrupted adoption may leave complete partial extension
progress; an explicit retry revalidates that same volume and continues compatible
steps under its finite invocation budget, without rollback or reprovisioning.
Old readers or selection rollback refuse expanded storage unchanged.
Specialized attention/feedback owner layouts are not standalone bootstrap
volumes and retain their original lifecycle/readers unchanged.

The actual workspace receiver advertises
[workspace/v2 terminal reads](../../plugins/agent-pull-requests/docs/workspace-tools.md)
through the existing authenticated Gateway route. Native acquisition remains
the existing no-effect exporter. Terminal observations use the configured
admitted account/repository target, not caller-supplied identity. Events remain
body-free; terminal admission fences stale materialization, and authoritative
reopen creates a new epoch. No protected writer, feedback/preparation owner,
remote retirement operation, compact recovery read, deletion worker or new
service is installed. Actual physical retirement still belongs to the existing
explicit lifecycle owner; this selection does not execute it automatically.
Before native acquisition, the target callback captures matching existing
watch registrations through the original ReplayStore using that request's
expiry and budget-aware checkpoint. The retirement record retains original
delivery custody; a registration added after capture is not retroactively included.
Capture includes frozen pending registrations, not just subscriptions awaiting
another provider observation. Their original event IDs and deadlines remain
in the internal retirement delivery ledger; acquisition does not replace them.
Workspace retirement sweeps elapsed original watch deadlines under that same
custody, but does not fire a synthetic watch success. Success still comes from
the existing native observer, with its complete native notification and head
SHA. Its actual pre-IO captured retention epoch must still match the current
workspace epoch; a delayed read spanning close/reopen refuses unchanged.
Fresh native observations remain usable after an unmerged workspace
closure, without reopening or restoring retired workspace content. Native
application also resolves elapsed deadlines before applying success evidence.

The standalone terminal profile applies the same admission policy to **new**
watch registrations, before any commit. Requests without an original finite
deadline or with richer predicates receive an explicit invalid-request refusal;
they never create an incompatible registration or fall back to another owner.
The optional workspace capability response advertises
`agent-pull-requests.terminal-watch-admission/v1` and its accepted predicates.
Every affected watch binding also advertises that policy through the
[capabilities media profile](../../plugins/agent-pull-requests/docs/watch-gateway.md#optional-terminal-admission-profile),
including watch-only callers without a workspace route. The current Dispatch
consumer checks it before durable suspension. An older or incompatible
optional PR core refuses terminal selection before state creation; baseline
hosting remains independent of that declaration.
Each bounded consumer cycle also rechecks already-prepared waiters against a
changed policy. Incompatible live waiters wake for external-state reinspection
before another registration attempt. Unattempted requests settle locally;
attempted or uncertain requests retain their original identity and reconcile
receipts or cancellation, without retransmitting an incompatible registration.
Legacy watch/v1-v2 capability and receipt shapes remain unchanged, and finite
legacy requests using those predicates keep their original semantics. Clients
needing unbounded or richer watches must retain the baseline host until a
predicate-preserving transition is qualified; this opt-in profile does not
silently convert them. Removing terminal selection is not a storage downgrade.
Deployment must select a capability-aware delegation consumer before enabling
the restricted profile; source publication does not update or activate one.

Public terminal load/sync results contain only the selected workspace's compact
retirement cursor, drain status and no-effect marker. Cross-consumer request,
registration and event IDs, full delivery inventories and operation custody
remain internal. Thus high-fan-out retirement does not expand the public result
beyond the existing 32 KiB Gateway response boundary.

The fresh foreground-process fixture qualifies CLI discovery, native export,
terminal fencing, retained restart, reopen and cursor resynchronization over
real Gateway TLS/PoP with synthetic provider inputs. This is source
qualification, not retained-host adoption, provider enrollment, production
cleanup, configuration authority or rollout.

### Optional terminal comment mapping reader

The same trusted `workspaces` object may additionally select
`"terminal_mapping_reads": true`. It requires `"terminal_reads": true` and
selects all workspace bindings on that owner. Absence or explicit `false`
preserves the prior terminal host; other JSON types refuse. The host checks
the executing core's separate
`agent-pull-requests.terminal-comment-mapping-reader/v1` declaration before
passing any new constructor input. Missing, malformed or incompatible support
refuses the explicit selection without creating state; baseline hosts do not
require it. Older strict host parsers reject the new field.

Fresh provisioning adopts prepared-intent storage, acquired comment mappings
and terminal comment mappings under the existing writer lease and deadline,
after terminal retention/recovery adoption. Prepared-intent storage preserves
original identities; it does not select preparation hosting or a protected
writer. Serve only validates existing storage. Selecting this reader on a
retained volume without its extension does not adopt it or advertise support.
Interrupted provisioning retains progress and refuses blind reprovisioning.
Removing the selection or rolling back to a reader that cannot understand the
adopted extension refuses the volume without rewriting it.

For retained `adopt-terminal`, terminal mapping selection additionally requires
prepared-review and acquired-comment mapping storage to be already adopted.
It does not allocate new mapping slots on that path; a missing prerequisite
refuses before terminal adoption. Fresh provisioning retains its existing
prerequisite construction.

The selected receiver separately advertises scoped `retained-comment` reads
for original active and compact records. Terminal-read/v1 and the prior
retained-comment/v1 declaration remain unchanged. The read needs neither a
cached body nor fresh native acquisition, and preserves original preparation,
receipt, nullable mapping and correlation semantics. Selection enables no new
mapping ingestion, preparation, submission, feedback, ACK, attention writer or
remote retirement operation. Existing lifecycle owners still own compaction
and physical retirement.

The foreground fixture seeds synthetic original receipt/acquired evidence
before its first writer, crosses real Gateway TLS/PoP and the workspace CLI,
then fully drains that writer before isolated owner-side compaction and
populated-cache retirement. A fresh foreground reader recovers the exact compact
metadata without another export or replay-store write; authoritative reopen
uses the cleared cache's actual current state. The merged-PR case also retains
an original unknown verdict attempt beside the confirmed comment: compaction
must preserve its identity and unknown outcome without retaining message
bodies or treating cache retirement as effect or delivery settlement. Restart
and reopen preserve that compact recovery record and the original terminal
event until its exact ACK. Closed-unmerged remains a distinct fixture outcome.
This qualifies source behavior,
not retained-host adoption, live provider enrollment, activation or rollout.

## Independently constructed container

`container/Dockerfile` is an offline wheel-only recipe, not an image published
or activated by this repository. A trusted builder supplies:

- An explicitly approved, digest-pinned Python Linux base.
- `wheels/`, containing the service and complete platform-matching dependency
  closure, built from the approved release and seasoned external artifacts.
- `requirements.lock`, the complete exact-version, SHA-256-hashed closure,
  including `agent-pull-requests-service` and the separately approved
  `agent-worktrees` artifact supplying the configured snapshot command.

Use a **dedicated context containing only those inputs and this Dockerfile**.
The repository-only `tools/pr_service_image_inputs.py` consumer prepares that
disposable context from an exact Linux `RuntimeProfile` and an already retained
`WheelSnapshot`. It reuses canonical complete-wheel inspection, requires the
service, PR core, Fleet and native Worktrees distributions, and writes an exact
version/hash requirements lock. The context contains only the existing recipe,
lock and inspected wheels and is removed when its context manager exits.
Keep both retained-wheel custody and the context open through the builder's
input consumption, and pass the returned `python_base` as `PYTHON_BASE`.

When the service and native executable have independently admitted application
profiles, pass their additional `(RuntimeProfile, WheelSnapshot)` pairs through
`additional_inputs`. The same owner verifies every original closure and retains
its identity in `profile_lock_ids`; no merged approval/profile is manufactured.
All selected targets and backends must match. Shared distributions must have
identical inspected metadata and bytes, apart from the union of selected extras,
and consume the aggregate archive/expanded-byte budgets once. The combined
`lock_id` identifies the selected profile set, not a separately approved runtime.
Canonical payload ownership is checked across the deduplicated union in the
shared environment, not independently reset for each profile.
Keep every selected snapshot open through context consumption. The existing
single-profile call and its original lock identity remain unchanged.

This is byte construction only, not an installer or approval adapter. The
trusted construction caller must independently approve the exact profile,
first-party release, target/base image and governed feed acquisition. A profile
record or returned context is not permission, and this helper never resolves,
downloads, invokes Docker or activates a service. Its synthetic offline-install
test does not establish a production dependency closure or an image build.

Never use a developer checkout, home, credential directory or replay volume as
the context. Installation is network-free, wheel-only and hash-required; image
construction never reads host configuration or credentials. The resulting
image runs as UID/GID 65532, with a read-only image filesystem and one
explicitly provisioned writable persistent volume at `/data`. Mount service
configuration and credential references separately, read-only. No Docker socket
or host source mount is required.

The deployment owner supplies the image digest and configuration/secret/volume
references using the existing
[`container.service` role contract](../../fleet-manager/docs/role-deployment-plans.md);
`container/role-bindings.example.json` shows that namespace, not a new adapter.
The provider projects references only: it neither grants placement nor proves
installation. Supply a stop grace period greater than `drain_timeout`, and
verify actual work settlement before transferring the volume. Never run two
writers or move a live local volume to another host on the strength of its
host-local lock.

Image construction, fresh-container restart/drain, fleet placement,
cross-host fencing, safe update/rollback/interrupted cutover and owned uninstall
require their own real proofs. A wheel install, source fixture, passive status
file or role plan does not satisfy them. The observer's bounded four-read
window applies completed targets without waiting for an earlier slow read;
this source behavior does not qualify representative fleet performance.

On Windows a concurrent passive health reader can prevent atomic status-file
replacement. That sharing refusal skips one publication; it does not stop the
watch owner or extend the prior snapshot's expiry. A prolonged refusal therefore
becomes unready, never fabricated healthy evidence.
