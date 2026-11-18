# Subscriber-owned callback working directory

Back to the [canonical effort](README.md#subscriber-callback-context-and-truthful-legacy-outcomes-from-5947).

**Status:** Proposed contract; review before runtime implementation.
**Scope:** P5947-3/4, V5947-3/4 and G5947-3/4. This does not complete those
items, expiry acceptance, M1, or the parent effort.
**Vision reconciliation:** Closes the existing
[centralized watcher and registrar](../../../visions/plugins/agent-pull-requests/README.md#centralized-watcher-and-registrar)
intent while preserving independent subscriber context. Applies the
[service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md).
No new supervisor, callback destination or remote-execution interface.

## Current boundary and the compatibility gap

The existing local callback owner is `WatchDaemon`, not the repository-cache
service's pull/replay API. The relevant source seams are:

| Seam | Current behavior |
|------|------------------|
| `watch_subscription.subscribe` | Negotiates acknowledged/native capabilities; does not capture caller cwd. |
| `watch_notification.validate_notify` | Acknowledged notify accepts only `argv` and `timeout`; legacy notify tolerates other keys. |
| `watch_notification.default_notify` | Neither callback launch path supplies cwd. An absolute executable does not fix a relative script argument. |
| `watch_state.RegistryState` | Serializes per-subscriber notify. Legacy restore can ignore unknown entry fields; adding a field is not a downgrade fence. |
| `watch_storage.read_subscriptions_state` | Requires a top-level JSON list. It rejects a versioned object before registry restoration. |
| `WatchDaemon._reattach_from_disk` / `reload_state` | Reads before resuming callbacks/pollers; lifecycle reload builds a separate strict registry before installing it. |
| `agent_procutil.owned_process.run_owned` | Already accepts explicit cwd and owns bounded child lifetime. No process-runner API extension is needed. |

This is source analysis of the current boundary, not a passed mixed-version
test or a claim that every historically released reader has the same behavior.
Implementation must enumerate and exercise the actually supported rollback
readers. Neither a package-version comparison nor an unknown legacy field
establishes enforcement.

## Selected contract

### Caller capture and legacy selection

Expand the provider first. After that expansion is qualified, migrate the
subscribe CLI in a separate source change: for callbacks with a nonempty argv,
capture the registering process's absolute current directory once, before
registration. Do not use the daemon's state directory, executable directory,
the daemon-starter's directory, or another subscriber's directory.

The migrated CLI defaults to caller context. A proposed strict
`--notify-context caller|legacy` selector makes the deliberate compatibility
choice explicit; these flags are **not implemented by this proposal**.
`legacy` preserves the old request/response and execution semantics and sends
no cwd fields or new protocol. It is suitable only when the caller's callback
is genuinely independent of cwd and its executable and file arguments use
absolute paths. Absolute paths alone do not prove that the callback does not
read relative configuration or data. No automatic legacy fallback is allowed
when caller context is required.

An empty/no-callback registration retains its current meaning and does not
acquire a cwd requirement. Existing callers, including existing Worktrees
callback producers, retain their wire contract until individually migrated.
Do not silently migrate an independent consumer as part of this lane.
Direct local registration callers may supply their own absolute cwd through
the same negotiated contract. The CLI's structured notify input must not
silently override its captured caller cwd; conflicting input is rejected.
No shell interpolation, environment capture or daemon-wide `chdir`.

### Runtime capability and wire admission

The actual running owner advertises `subscriber_cwd/v1` through existing
health only when it can validate, persist, restore and execute that contract.
The new CLI requires it before sending cwd. Missing support produces a
specific unsupported-subscriber-cwd error before registration, with the
explicit legacy option described as a semantic choice, not a retry.

For caller-context requests, `notify.cwd` is a nonempty absolute native path
without NUL. The receiver validates it and requires an existing directory at
registration. It does not create, repair or substitute a directory. Validation
uses the owner host's path rules; this remains a same-host local callback
contract, not a portable or remotely forwarded path.

Two wire forms retain the delivery distinction:

| Delivery | Required wire | Prior-owner protection |
|----------|---------------|------------------------|
| Legacy one-attempt | `notification_protocol: "legacy_callback_cwd/v1"` plus `notify.cwd` | The current prior owner rejects an unrecognized explicit notification protocol before registry mutation or provider fetch. |
| Acknowledged | Existing `notification_protocol: "acknowledged_notifications/v1"` plus `notify.cwd` | The current strict prior notify validator rejects cwd; ACK/event identity is not versioned or reinterpreted. |

`legacy_callback_cwd/v1` means best-effort delivery with required cwd, **not**
acknowledged delivery. The receiver must not infer `acknowledged=True` merely
from the presence of a protocol key, as the present restore path does.
Unrecognized explicit protocols and invalid combinations refuse before
registration, fetch, persistence or callback effects. There is no timeout-field
sentinel, ignored top-level extension or forced ACK migration.

Success echoes `callback_context_protocol: "subscriber_cwd/v1"` and the
accepted cwd. Acknowledged responses additionally retain their existing
notification protocol and nonempty registration identity; legacy responses
echo their new legacy protocol without inventing an ACK identity. A missing
or mismatched echo after a possibly accepted registration is ambiguous:
reconcile the original subscriber/registration through its owner. Do not
replay, cancel a replacement, or retry the legacy form automatically.
Health is discovery, not a lock on the executing generation; prior-owner
wire refusal and response validation must also cover owner replacement
between discovery and registration.

### Persistence and rollback

Use the existing `watch-subscriptions.json` and atomic-write owner. Readers
accept the existing list unchanged and an explicit envelope:

```json
{
  "schema": "agent-pull-requests.watch-subscriptions",
  "version": 2,
  "subscriptions": []
}
```

Version 2 contains the complete subscription collection, not a second store.
It is required whenever any retained subscription has caller-context
semantics, whether legacy, acknowledged, pending, or continuous. Each such
entry retains `notify.cwd` and the selected notification protocol. The
envelope and per-entry validation reject unknown versions and inconsistent
context/protocol combinations; do not skip a malformed entry and resume
others. Validate the entire candidate collection before installing it or
starting effects. The in-memory restore API must enforce the same context
rules, not merely rely on the storage wrapper.

Retain all existing baseline/deadline, original registration/event identity,
pending immutable payload, queued event, attempts and next-attempt metadata.
Cwd belongs to the subscription, not to an added field in the immutable
event payload. Restart/retry uses that same retained subscription context;
re-registration is not a mechanism for rewriting a pending event.

A writer may keep the old list format while no record requires context.
Writing a context-bearing collection must atomically replace the whole list
with version 2, using the existing definite-failure/uncertain-commit recovery
semantics. No context sidecar: an unaware writer could ignore it and overwrite
or deliver the associated subscription without context.

The current prior storage reader rejects a top-level object before returning
any entries. Thus the intended supported rollback result for context-bearing
state is **explicit pre-effect refusal**, not successful old-owner delivery.
Qualify that result through the real startup and governed reload boundaries,
not only by calling a JSON parser. Preserve the file bytes and pending event;
do not reset, rewrite to an empty list, downgrade a protocol, start a poller,
invoke a callback or activate an incompatible successor. Existing installation
custody/recovery owns a failed transition; this proposal does not claim that
reader refusal itself restores a route or permits restarting another owner.
This is refusal to consume incompatible callback state, not an installer
replacement veto: preserve
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement).
Incoming installation readiness and current selection remain distinct from
state activation and outgoing retirement. Retain the subscription state for
a compatible executor; do not make installation success depend on repairing
an incumbent or cleaning up its retained resources.

New readers continue to restore old lists with their original meanings. If
the last context-bearing subscription is explicitly removed through normal
admission, a complete remaining snapshot may again use the old list only if
no retained pending/queued/context-bearing record is lost. No lossy export or
automatic stripping to make a rollback pass. Test every schema family in the
actual supported upgrade/rollback window; if a supported reader does not
refuse safely, add a reviewed expansion/transition guard before enabling
context writes. Do not redefine that reader as unsupported to avoid the test.

### Execution and missing directories

Use the existing owned-child runner with explicit `cwd` for new
caller-context callbacks in both delivery modes. Never change the parent
process's cwd. Keep the existing timeout, headless launch, bounded cleanup
and callback concurrency owners. Do not add a subprocess per health check
or a cwd-resolution daemon.

Directory existence at admission is not a promise of future existence.
If it disappears or ceases to be a directory, refuse that launch with no
fallback or directory creation. Preserve the existing sanitized
`callback_error` outcome: legacy remains one attempt with an inspectable
unknown result, while acknowledged delivery retains its original durable
pending event and bounded-backoff retry. A callback may already have effects
on other launch failures; do not label every error effect-free. The executable
launch boundary must also handle a directory disappearing after validation.

Keep old no-context callback behavior unchanged. Path values are ordinary
private local configuration, not credentials: persist only the required
cwd with existing subscriber metadata, never an environment snapshot,
credential material, callback output or raw exceptions. Do not expand
public diagnostic/history fields to disclose callback paths.

## Serial realization and qualification

1. **Expand and qualify the owner.** Add strict context protocols, health
   declaration, envelope/list support and explicit-cwd execution under the
   existing callback/storage/lifecycle owners. Old callers remain unchanged.
   Gate capability advertisement on the complete support, not just parsing.
2. **Qualify current/prior transition behavior before client migration.**
   Record the supported reader versions/contract families and use their
   actual reader/startup code in isolated fixtures. A mock that rejects an
   object, or an arbitrarily selected old commit, is not the rollback proof.
   Exercise cold restore and governed reload, preserving existing
   writer/route custody on refusal. Fix any unsafe family before proceeding.
3. **Migrate the CLI conditionally.** Implement the proposed context selector,
   default caller capture, actual-health negotiation, strict response
   validation and ambiguity behavior. Publish the release that establishes
   provider support separately from the release that enables caller defaults.
   Keep the explicit legacy baseline; this lane retires no old wire form.
4. **Resolve the canonical gates only with consumer evidence.** Qualify two
   subscribers registering from different directories, with relative callback
   scripts, while the daemon uses a third directory. Prove actual child cwd,
   persistence/restart, missing-directory outcomes and cleanup for legacy and
   acknowledged modes. Then resolve P/V/G5947-3/4 as applicable. Installed
   rollout and unrelated parent/resource gates remain separately authorized.

The focused validation matrix must include:

- Old caller/new owner and explicit sufficient legacy baseline; unchanged
  legacy stdin/response, no fabricated ACK or new legacy retry.
- New caller/new owner for both delivery modes; capture once, strict path
  validation, conflicting structured input, exact response echo and no
  cross-subscriber cwd inheritance.
- New caller/prior owner and health/register generation change; required
  cwd refuses, no registration/fetch/write/callback on definite rejection,
  and ambiguous responses do not trigger fallback or blind replay.
- Actual current/prior saved-state restore with mixed old/context entries,
  pending acknowledged and continuous queued events; byte-for-byte unchanged
  state and zero effects on refusal, original identities on compatible
  restart, and unknown/malformed envelope refusal.
- Definite write failure before scheduling and uncertain post-replacement
  recovery, retaining the existing atomic persistence owner and no duplicate
  event or legacy replay.
- Real child cwd, missing/removed directory, callback timeout and descendant
  cleanup through existing opt-in contained Windows/Linux process lanes,
  including any focused smoke subset. Required PR/promotion CI stays unit-only
  with coverage and no real-child/process smoke fallback; no new fleet-wide
  or hosted-workflow prerequisite.

No live forge request is necessary to test this local callback contract.
Use temporary test-owned roots and synthetic snapshots. This proposal grants
no installation, service replacement, credential action, provider mutation,
resource cleanup, new hosted run or production consumer activation.
