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
flow on the retained connector. A lost or malformed unlock reply stops with
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
existing rendering.

This migration covers explicit unlock and its associated read-only status.
Implicit provider-triggered reads and other callers retain their baseline until
their own conditional migration; they are not claimed as cross-source acceptance.
Provider rollback retains ordinary unlock support with the unsupported warning,
not a claim that the requested source was warmed. No old form is retired here.

Focused source coverage lives in `tests/test_cache_sources.py`,
`tests/test_cache_warm.py`, `tests/test_cache_warm_contention.py` and
`tests/test_cache_warm_status.py`, with request-boundary coverage in
`tests/test_cache_warm_selection.py` and explicit-caller coverage in
`tests/test_unlock_client.py`.
The fresh-process native-protection fixture also
covers unlock-triggered warming before expiry and a synthetic unreadable
caller. See the repository [testing guide](../../../TESTING.md) for execution
and native qualification.

See also: [persistent cache and sealed values](../README.md#persistent-cache-and-sealed-values).
