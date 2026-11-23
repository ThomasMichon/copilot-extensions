# Unlock-triggered cache warming

An opted-in protected cache and declared targets or `cache_source` extensions allow the
selected service to warm configured credentials after a successful unlock.
Provider-based, explicit-password and attended unlocks use the same path.
Nothing enables the cache, unlocks a database or registers targets implicitly.

The [configuration owner](configuration.md#declared-cache-targets) supplies the
declared allowlist, followed by the existing extension registry. Sources receive the
ordinary optional machine hint as `None`; each source remains responsible for
returning the targets appropriate to that service. Warming never lists or scans
the database, starts a session ceremony, or adds a timer. Manual population
retains its existing uncapped collection behavior through the same collector.
Enqueue captures only the discovery root and unlock identity. Coalescing precedes
policy discovery; the existing worker resolves fresh policy and default groups
outside credential-lock custody, then snapshots the sources for that work.
Invalid declarations or manifests fail queued work before credential reads rather
than falling through to extensions. Non-comment rows need a nonempty entry.
Declared entries, every raw manifest row (including comments and blanks), and
extension items share the unchanged raw-work budget. Scanning checks the deadline
and cancellation between rows. Manual collection remains uncapped.

## Work and lifetime

Verified unlocks enqueue a database/group context without waiting for backend
reads or configuration-owner discovery. Repeated already-unlocked requests coalesce with that unlock's work,
including completed or partial work; they do not retry failed targets or renew
the master password. A genuinely new verified unlock gets a new identity even
when the wall-clock timestamp is unchanged.

The serving generation's existing housekeeping loop starts at most one worker.
It admits at most 32 pending/retained contexts, 256 configured targets, 1024 raw
source items and 32 sources per context. The 60-second work deadline starts at
enqueue, including queue wait. Reaching a bound is a truthful partial outcome,
not evidence that unexamined targets were cached.
Default-group scope remains unresolved until the worker binds it; status never
guesses that an explicitly requested foreign group is the native default.

Source callbacks and their iterators run off the event loop and must return
cooperatively. The deadline prevents further admission; it does not forcibly
interrupt arbitrary trusted Python callbacks or an already admitted native
operation. Such an operation retains its existing bound and must actually
settle. No detached worker or timeout-based resource abandonment is used.

Each target uses the existing credential and backend custody. Warming requires
the already-unlocked master password: it never invokes another provider or
dialog if the password expires, including expiry while waiting for backend
custody or a protected-store lookup. It does not extend password lifetime or an
inherited handoff deadline.
The same internal deadline reaches the protected read owner, which checks it
again immediately before admitting a KeePass entry read after custody waits.
Waiting or active foreground requests take priority before the next warm target
is admitted, rather than relying on the operating system lock's fairness.
Foreground completion or failure wakes the worker. Its existing two-second
custody wait and total admission deadline still apply; sustained foreground
work produces a partial `credential-custody-busy` outcome instead of starvation.

## Protected results and failures

`cached` counts only records read back from the existing service-owned protected
store with the expected binding, value and generation. A successful backend read
or a successful-looking cache write alone is insufficient. Standard field and
backend-equivalent entry aliases are deduplicated without merging root and
group-relative subjects.

Unknown rotation journals remain intact. Missing values, failed protection,
source errors and unverified writes produce failed or partial outcomes without
credential-bearing diagnostics. Warming does not invoke ordinary client
authoritative reconciliation or replace missing-only recovery state. Existing
protection, freshness, selection, generation and revocation rules still govern
later reads; a completed batch is not permanent readiness.

Shutdown closes the pending queue. An admitted worker belongs to both the
connection owner's credential-work and worker sets, so cutover cannot release
writer custody while it is still running. Cancelled closure still waits for
actual work settlement before executor and service cleanup.

## Status and compatibility

The existing full `ping` response adds `cache_warm` using contract
`agent-vault.cache-warm`, version `1`. It reports only state, a fixed reason,
`selected`, `cached`, `failed`, `remaining`, `failed_sources` and `truncated`.
The additive `target_policy: "repository-cache-targets-v1"` identifies support
in the running generation, separately from an installed CLI's capability.
Counts and status are scoped to the selected database/group and request source; no entry names,
database paths, credential values or callback exception messages are included.
Full ping never reloads configuration to select warming status. An omitted group
uses the retained default-warming context; if none is known, status reports
`context-unavailable` rather than guessing from another group's work.

`agent-vault ping` renders this status conservatively. Unsupported, malformed or
inconsistent metadata is unknown, never a successful warming claim. Older
providers omit it, and older clients ignore the additive field. Health-only ping remains
independent of credential work. Ordinary client `--cache-only` still never uses
IPC or an unlock.

### Request-source provider expansion

The running service advertises `cache_warm_selection_versions: [1]` on its
existing ping (including health-only ping). This is provider support, not an
installed-client version claim. Version 1 accepts an optional
`cache_warm_selection` object on unlock and full ping:

```json
{
  "contract": "agent-vault.cache-warm-selection",
  "version": 1,
  "root": "<absolute native configuration discovery directory>",
  "native_default": false
}
```

The object has exactly these four fields; the root is at most 4096 characters
with no control characters. Relative and non-native guest paths are refused.
It carries discovery provenance, not database, cache placement, credentials,
executable code or authority to unlock. Database/group custody stays with the
existing request. The worker rechecks directory availability and resolves policy
through the existing invocation/configuration owners. A native-default selection
must still match the native owner's current configured root; it cannot relabel
an arbitrary repository as that default. The existing native-default strict
source validation remains active.

When that field is handled, unlock acceptance and scoped ping status add
`request_selection_version: 1` inside `cache_warm`, without echoing paths.
This receipt distinguishes a processed selection from resident-policy
metadata if an intermediate adapter drops the input field. It acknowledges
request handling, not successful target population. Legacy requests keep their
old response shape, and older clients ignore this additive metadata.

Explicit selections coalesce only within the same database/group/root/provenance
and unlock generation. Different sources sharing one database do not replace
each other's queued work or status. They share the existing capacity and
per-database unlock invalidation, not additional workers or renewed deadlines.
Callbacks also run under the captured configuration scope. The service never
falls back to resident targets for malformed, removed or invalid selections.
An unlock carrying this field returns additive `cache_warm` acceptance status;
`ok: true` still describes the unlock, not completion of automatic warming.
Invalid selection can therefore leave a successful unlock with `not-queued`.

### Implicit-read provider support

The additional `cache_warm_read_selection_versions: [1]` ping advertisement
qualifies handling receipts for `get` and `has`. Their optional selection uses
the same version-1 object and existing request context. If a provider unlocks
during that read, its warming queue retains the caller's source rather than the
resident source. The response adds source-scoped `cache_warm` status and its
`request_selection_version: 1` receipt, without changing the credential,
existence, generation or failure outcome.

The receipt is not a request for another unlock, warming attempt or cache write.
A read satisfied without an unlock can report `idle`; disabled or rejected
warming remains separate from a successful read. Malformed selections report
`not-queued` without resident-source fallback. Legacy reads retain their exact
response shape. Advertising unlock selection alone does not establish support
for these read receipts; callers also negotiate the read capability on their
actual retained connector.

The additive `cache_warm_read_selection_actions` list explicitly names actions
whose replies support this receipt. It includes `get`, `has`, `search`, `list`,
`ls` and `show`. Read generation 1 without this list still establishes the
original `get`/`has` support only; it must not be interpreted as query support.
Query callers must qualify both version-1 advertisements and their action in
this list before sending a selection. Query responses retain their original
results, entries, output or refusal unchanged, with source-scoped warming added
only when the selection field was supplied. Older callers retain their baseline;
current query callers negotiate this declaration before sending a selection.

### Ordinary mutation provider support

The additive `cache_warm_selection_actions` list explicitly names actions whose
responses acknowledge the same source-selection contract. In addition to the
read actions, it includes `add`, `set-username`, `remove`/`rm`, `move`/`mv` and
`copy`/`cp`, plus the legacy `import-key` and `export-key` actions. A future caller
must qualify version 1 and its action on the actual
serving peer before sending the existing selection field; read-version support
alone does not qualify mutations. The existing base-version advertisement
continues to qualify explicit unlock independently of this list.

A provider-triggered unlock retains the request source. The operation's
message, destination, refusal and success outcome are unchanged, with a handled
warming receipt added only when requested. Invalid selection refuses warming,
not an otherwise valid operation, and does not fall back to resident targets.
The response wrapper only reads retained status; it never enqueues another
attempt or renews a password lifetime.

The attachment actions retain their original import/export payloads and
provider-unlock behavior. Create-only import, exclusive export and SSH contracts
are not covered. Password transaction receipts are declared separately below.
Explicit bindings and non-activating
requirements remain with their existing owners. Legacy requests retain their
original response shape, and no existing form is retired.

### Ordinary mutation callers

The `add`, `set-username`, `remove`, `move` and `copy` commands retain one
operation scope across their existing readiness checks, implicit unlock/prompt
flow and final unbound mutation. They reuse the existing read-session connector
and unlock sender; no extra readiness ping or warming attempt is added. Only
base-version-1 support plus the specific action in the serving peer's general
action list enables the mutation's selection field.

Known provider misses can continue the existing terminal/GUI flow on the retained
connector. Unknown or malformed unlock replies stop before another prompt;
unknown or malformed mutation outcomes stop without success output or replay.
Missing or malformed optional warming receipts do not repeat or fail an
otherwise successful mutation. Command flags, messages and exit behavior remain
separate from warming acceptance.

Older or unsupported providers retain the ordinary baseline without the new
mutation field, with an unsupported warning. A legacy unbound transport remains
usable for an already-unlocked ordinary operation, but cannot receive a master
password because an unlock recipient cannot be retained. Pinned bootstrap
without source provenance retains the bound legacy unlock without inventing a
configuration owner. All scopes restore their prior values when a command exits.

This migration never intercepts explicit contexts, retained routes or
`MutationBinding` operations. `set-password` cache transactions,
Git-credential, KEK and identity/SSH operations retain their existing specialized
paths; they are not claimed as migrated by this slice.

### Legacy attachment callers

Ordinary `import-key` and `export-key` validate their existing input and
destination paths before entering the same full-command scope. The existing
unlock helper, attachment request and local file operation retain the selected
source and connector. Only base-version-1 support and the corresponding
attachment action in the serving peer's general list enable source projection.
Messages, payloads, permissions and command options retain their ordinary
behavior; warming acceptance remains separate from operation success.

Unknown unlock outcomes stop before another prompt or attachment operation.
Unknown attachment outcomes cannot produce success output or write exported
files, and are not replayed. Optional receipt failures do not repeat or fail an
otherwise successful attachment operation. Old/pinned/unbound peers retain the
same explicit compatibility rules as ordinary mutations.

Create-only import and exclusive export branch before this scope. Their strict
already-running health qualification, explicit `MutationBinding`, non-prompt
behavior and file-custody requirements remain unchanged.

### Password transaction provider support

The general action list separately advertises `set-password` source receipts.
A caller must qualify base version 1 and this specific action; ordinary read,
mutation or attachment support alone does not qualify it. Provider support is
separate from the caller migration below and does not add password operations
to the ordinary mutation interceptor.

The password owner retains its internal credential lock, backend/cache custody,
queue deadline and replacement journal. The admitted response is wrapped only
after that owner returns, without adding an outer credential lock or another
mutation, unlock or enqueue. Success, refusal, `committed`, `cache_updated`,
`cache_pending`, generation and error fields retain their original meaning.
Warming status is separate from transaction success or cache settlement.
Invalid source selections refuse warming without changing the transaction
outcome; requests without a selection keep their exact prior reply shape.

### Password transaction caller

The password command uses its own operation scope around the existing local
replacement transaction. The original captured vault/group determines both the
RPC recipient and local cache entry; pending recovery, mutation and any
authoritative refresh stay on that connector and source. Existing journal
locking, migration, generations, rollback, bounded RPC timeouts and committed
or pending outcomes retain their meaning. No cache root, protection policy or
enablement setting changes.

The actual peer must advertise base and read version 1 plus `set-password` in
the general action list before password source projection is qualified. A
partial/mixed advertisement cannot imply full transaction support. The existing
readiness pings are reused; recovery/confirmation reads occur only under their
original transaction conditions.
The combined capability also gates source projection on those reads, so a
partial peer cannot erase an unsupported transaction warning. A captured absent
group remains explicit during local cache normalization, without reloading a
new default. Recovery, mutation and confirmation send the captured canonical
entry as an absolute path, together with the captured group (or an explicit
empty group). A different service default cannot redirect the operation while
the caller settles another entry's journal.

Unknown mutation replies leave the journal pending without replay or alternate
transport. Malformed consumed result flags or recovery values cannot become
confirmed local cache data. Confirmed refusal and committed-but-cache-incomplete outcomes keep
their original rollback/exit semantics, separate from optional warming receipts.
Consumed generations must be nonnegative exact integers, not booleans, floats
or coerced strings. A missing mutation generation still uses the original
authoritative confirmation; invalid metadata leaves the journal pending with
the cache-incomplete exit.
Unsupported peers with a retained connector retain the baseline with a warning;
pinned contexts do not invent a configuration owner. An unbound peer is refused
during readiness even when already unlocked, before input or journal access:
a lost mutation reply must not fall through to another transport. A
Windows-sourced host-owned route is also refused before unlock or journal
access. Its effective database/group is not the guest's cache identity; run
password replacement on that host instead of projecting guest defaults or
settling a guest journal from the host's reply. Other host-routed commands keep
their existing selection behavior. Explicit contexts, retained routes and
`MutationBinding` operations remain outside this scope. Scope restoration also
covers input cancellation.

### Ordinary read callers

The service path of `get` (including batch and authenticated-HTTP reads), `has`,
`search`, `list` and `show` reuses its existing readiness ping to qualify both
version-1 capabilities. Queries additionally require their action in the serving
peer's supported-action list; missing or invalid lists do not imply support.
Once ready, that operation retains the resolved database/group, caller root,
native-default provenance and connector through the read. A transitioning
service is not retained before readiness. Only a capable, bound peer receives
the selection field; no extra unlock or warming request is added.

Credential cache hits and cache-only misses return before root selection,
discovery or service contact. `--require-running` keeps its advertised-endpoint requirement
and never activates a service. An immutable pinned bootstrap without a declared
configuration root does not discover an unrelated owner merely to enable
warming; its ordinary read remains available with unsupported warming.

Older peers, missing capability metadata, unavailable source provenance and
legacy unbound extensions retain the ordinary one-shot read without the new
field, with a fixed unsupported warning. A missing, rejected or malformed
warming receipt is unconfirmed, not a reason to repeat the read. An acknowledged
`idle` receipt is valid when no unlock was required; it does not claim queued
work. Lost replies on a retained connector never reselect a peer or replay.
Malformed credential/existence results are not accepted as values.

Warnings use the existing credential-read warning channel, so batch and HTTP
consumers keep their output boundaries. A successful read remains successful
when optional warming is unsupported or unconfirmed. Protected-cache
decryption refusal, service-only fallback, authoritative refresh reconciliation
and credential stdout remain separate and unchanged.

### Explicit unlock caller

The explicit `unlock` command qualifies version 1 through its selected service's
existing ping, not a package version, and retains that connector and resolved
context through the existing provider/terminal/GUI prompt paths. The ordinary
path reuses its existing ping; `unlock --terminal` first checks the existing
service before reading a password and does not start one. Neither path changes
cwd or enables a cache.

An already-unlocked capable service still receives one passwordless unlock so
the current source can be queued without a new ceremony or password-lifetime
renewal. Old or unsupported already-unlocked services retain the original
no-unlock-request behavior. For an older locked service, ordinary unlock remains
available with an explicit warning that caller-scoped warming is unsupported
and its legacy warming policy remains. No new field is sent without capability
support. An older reader ignoring unknown JSON is not negotiation.

A known provider-only `needs_unlock` response can continue the existing prompt
flow on the retained connector only after an actual boolean `ok` outcome.
A lost or malformed unlock reply stops with
unknown outcome: no fallback prompt, transport reselection or automatic replay.
A successful unlock remains exit zero even when its warming receipt is missing,
disabled or refused; a fixed, credential-free warning separates that failure
from unlock success. Guest paths are not translated into host paths. A capable
provider refusing the selected source must not fall back to resident targets.
Context-aware older providers that predate the receipt also remain usable for
ordinary unlock, but their warming is unconfirmed rather than retried.
Use `cache-verify` for actual cached-target acceptance, not the unlock exit code.

The read-only `ping` command also qualifies the selected service, then requests
the current source's status over the retained connector. This adds one ping for
a capable peer, not an unlock or a new connection-selection attempt. Old,
unacknowledged, malformed or rolled-back peers render warming as `unknown`, never as a successful
resident-source batch for the caller. Other service/lifetime fields retain their
existing rendering. A reachable non-accepting service remains visible with
warming unknown; unlock still requires readiness. A transition observed by the
second ping does not turn a successful liveness response into `not running`.

The read migration covers explicit unlock, its associated read-only status and
ordinary `get`/`has`/`search`/`list`/`show`. Ordinary mutations use their separate
named contract above. Remaining specialized operations retain their baseline
until their own migration; they are not claimed as cross-source acceptance.
Provider rollback retains ordinary unlock support with the unsupported warning,
not a claim that the requested source was warmed. No old form is retired here.

### Transport owner binding

A transport callback can rediscover its peer on every invocation, so retaining
the callback alone does not pin the endpoint that answered qualification.
Extension owners provide `register_transport(..., bind=binder)`: the read-only
`binder(ctx)` resolves and captures the connector and returns the transport to
use for that operation, or `None` when it does not apply. The returned callable
must not select another peer. A callback already bound to a fixed connector can
return itself from the binder.

The shipped `core_ext.bind_core_transport` captures the selected core endpoint,
token and finite timeout cap before ping. Later sends reuse that endpoint through
the existing delegation override, not environment or rendezvous rediscovery.
No second credential/delegation engine or vendored-library patch is involved.
Preferred/fallback ordering and ordinary one-shot transport calls are unchanged.

Without a binder, a successful health response can still serve read-only status,
but warming is unknown. An ordinary already-unlocked command can remain a no-op;
otherwise explicit unlock refuses before reading or sending a password because
its selected endpoint cannot be retained. Create-only attachment mutations
additionally retain their separate `single_attempt=True` requirement. Neither
registration option grants access or waives service-side admission.

Focused source coverage lives in `tests/test_cache_sources.py`,
`tests/test_cache_warm.py`, `tests/test_cache_warm_contention.py` and
`tests/test_cache_warm_status.py`, with request-boundary coverage in
`tests/test_cache_warm_selection.py` and explicit-caller coverage in
`tests/test_unlock_client.py`. Actor-free implicit-read controls are admitted in
`tools/promotion_unit_tests/test_vault_read_selection.py`; full-command ordinary
mutation controls are in `tools/promotion_unit_tests/test_vault_mutation_selection.py`.
The fresh-process native-protection fixture also
covers actual ordinary reads, protected-show queries and synthetic entry
creation over private TCP,
unlock-triggered warming before expiry and a synthetic unreadable caller.
See the repository [testing guide](../../../TESTING.md) for execution
and native qualification.

See also: [persistent cache and sealed values](../README.md#persistent-cache-and-sealed-values).
