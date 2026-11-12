# Standalone PR watch service

For the explicit independent Unix HTTP repository-cache/continuing-event profile,
see [local operation](LOCAL.md). It uses the same foreground host and writer,
without requiring Core/Gateway ingress or service credential enrollment.
The connector profile below retains its existing defaults.

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

Only explicit **fresh** provisioning, under the existing host-local writer
lease and operation deadline, adopts the core's terminal retention, recovery
and cache-retirement extensions in the owner-store v3 envelope. It uses the
core's bounded 8 MiB terminal reservation. Partial provisioning retains its
progress and refuses blind reprovisioning. Serve validates the existing
envelope/cache before connector admission, never adopts or repairs either.
Existing volumes require their own reader/lifecycle transition. Removing the
selection or rolling back to a reader that cannot understand adopted storage
refuses without stripping extensions or rewriting the volume.

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
expiry and budget-aware checkpoint. Accepted terminal notifications therefore
retain their original delivery identity after reopen; a registration added
after capture is not retroactively included.

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
uses the cleared cache's actual current state. This qualifies source behavior,
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
