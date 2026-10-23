# Bridge-owned CLI startup loadout gate

This is a registered/enabled plugin-loadout check, not an eager MCP authentication
or component-attestation requirement. It never installs, enables or reloads
plugins and never changes HOME, account selection, preferences or repository settings.

## Explicit caller ownership

A typed CLI acquisition can supply `plugin_readiness`:

The following values illustrate a local marketplace's request fields, not a
generated projection output. Supply the compiled projected identity and only
the version and root expectations preparation actually provides.

```json
{
  "owner": "bridge",
  "plugins": [
    {
      "projected_source": "helper@tools-venue-example",
      "logical_source": "helper@tools",
      "version": "2.0.0",
      "live_marketplace_root": "/example/controller/.ai"
    }
  ],
  "timeout_seconds": 120
}
```

Readiness controls accept the validated effective requirement from preparation,
including projected alias identities and operator overrides. Automatic acquisition
uses the writer's compiled selection instead of caller-selected plugin guesses;
optional caller `cwd`, `expected_session_id` and timeout assertions still apply.
The field does not select or activate additional plugins. Omitted optional fields
remain omitted from serialized requests, preserving legacy journal identities.

The version-gated `acquisition_cli.acquire` requires HTTP protocol 44 for CLI
acquisition. Raw HTTP callers must enforce the same floor; older daemons ignore
unknown acquisition fields. ACP acquisition retains its prepared directories and
native handshake and does not require a joined CLI extension.
Readiness wire models likewise ignore additive unknown peer fields while
retaining required ownership, identity, uniqueness and result-state validation.
The default `acquisition-readiness` producer probe also requires protocol 44.
This is a capability floor, not the minimum-supported wire generation.
Ordinary messages and receipt delivery retain their existing compatibility
range; generation-41 peers can still serve remote ACP targets. Only a caller
requesting readiness-bound delivery requires generation 44. Managed remote ACP
ownership retains its separate generation-43 floor.
Existing ACP holders lacking the new optional metadata retain their established
prepared-directory/handshake path; no new transcript or CLI-extension requirement
is introduced to retrofit that metadata.

## Registration-owned local preparation

Ordinary local ACP acquisition enables preparation automatically, preserving
structured automatic/base argument provenance and every explicit plugin flag.
Preparation runs against the actual allocated checkout and merged child
environment, before the child handshake. Its metadata is compiled under the
canonical registration lock from the effective selection after operator
origins/disables and alias projection. It contains projected and logical
identities, local manifest versions, and known local marketplace roots.
Remote marketplace requirements omit both the isolated acquisition-cache
version and its private root: independent native startup may resolve a floating
Git declaration to a newer version, and that cache does not pin native
activation. Original Git origin/revision/location declarations remain intact
in target settings. Local version/root comparisons remain unchanged when
native observation advertises them. The directory return contract is unchanged.

Persisted targets retain static argument provenance and the local-preparation
flag inside the existing request-override metadata, not as new constructor
fields. The preceding target reader can hydrate and re-save those records
during rollback; the current reader restores preparation on a later resume.
Prepared loadouts and one-shot requests remain runtime-only.

The acquisition journal retains this semantic metadata across delivery/recovery,
not prepared argv or secret environment. Explicit caller worktree, qualified
owner and session context have separate optional request fields; opaque
acquisition affinity labels are never filesystem origins. Gitless legacy
launches remain explicitly not applicable. Existing wrapped and different-world
exclusions are not promoted to ordinary local preparation. Privileged execution
uses the separate receiver-owned contract below, not a weakened ordinary-loopback
identity check.

For fresh/cold-resume CLI acquisition, Worktrees advertises preparation capability
in the existing `embody` preflight. A bounded producer request is consumed after
its real profile-env merge, before native mux creation. Canonical preparation
selects its installer from that environment, leaves productive HOME unchanged,
and returns the compiled context. Public packaging selectors are pinned on the
new child so a long-lived mux cannot substitute a stale HOME/cache/PATH. No
secret environment snapshot is persisted. The owner adds no automatic
`--plugin-dir` flags and preserves configured native arguments, with no Bridge
task/seed. Cancellation signals the operation and awaits quiescence;
this preparation call does not use the legacy owner-command timeout that can
leave a launch running. The operation's deadline and per-command budgets remain
bounded, and its private request files are removed only after the owner exits.
Executable lookup anchors relative PATH entries to the execution checkout.
Windows quoted PATH entries retain their normal lookup meaning; literal quote
characters in POSIX directory names remain unchanged.

Live reuse consumes only a receipt bound to that same native session; it never
reconstructs or changes a retained profile. Changed producer inputs require a
strict owner resume rather than reusing a stale loadout. A legacy live CLI without such a
receipt fails explicitly instead of guessing. An older Worktrees owner reports
`cli_capability_missing` before launch. Gitless legacy acquisition and ordinary
operator launches remain unchanged.

The CLI producer captures registered caller context for a new delivery and
recovers the original context on retries, preserving request identity across a
caller handoff. Explicit supplied fields remain authoritative; absent legacy
fields are not retroactively injected into an existing journal request.

Remote consumers can opt into `build_prepare_script(...,
include_prepared_context=True)` and parse the version-1
`__VENUE_PLUGIN_CONTEXT__` record with `parse_prepared_context`. It carries the
same effective metadata alongside the unchanged `__VENUE_PLUGINS__` directory
record. Optional `acp_only_sources` identifies verified installed-only payloads
without a native marketplace origin; CLI refuses these rather than inventing
one, while ACP retains the complete verified directory set.
Emitting that record does not by itself integrate a provider's seed
delivery; each owner must explicitly consume it and withhold its typed seed.

## Privileged ACP receiver

The existing Windows elevated sub-daemon advertises optional
`agent-bridge/privileged-launch` version-1 ACP initialization metadata only when
it is actually elevated. A primary Bridge using its existing authenticated
ACP-over-WebSocket relay requires that capability before `session/new`.
An older receiver is refused before productive session creation; this is not
a new global HTTP or ACP protocol floor.

The primary verifies the controller's filesystem source using its own caller
and qualified-owner context. Namespaced session metadata carries that verified
source separately from affinity and allocation ownership, plus only explicit
request arguments, environment, MCP and model/effort overrides and the existing
preference-source/context policy. It does not carry the primary's ambient
environment, home, credential stores or registry. A different receiver account need
not have the same Worktrees registry; the original same-filesystem local source
must still be accessible to it, or preparation fails explicitly.

The receiver resolves the named agent through its own registry. Only an actual
local elevated static target can consume this context; opaque wrappers, SSH,
containers and adoption are not promoted by a `requires_admin` label. Its
existing worktree owner allocates the checkout and merges the productive
profile before canonical local preparation. Child-native sources and explicit
disables retain precedence. Settings and every effective ACP directory are
prepared there before the fresh Session Host starts, and ACP uses that same
allocated cwd. No alternate launcher, account, elevation method, authentication
path or native reload is introduced.

The preparation receipt records execution cwd independently of the repository
root used for settings registration, so a configured subdirectory launch stays
in that subdirectory. In target-settings mode, new/load responses carry the
receiver's verified ACP config options and matching selected preferences. The
primary validates and preserves that selection through normal ACP readback;
it does not resolve unprivileged defaults again or infer preference readiness
from plugin preparation alone.

The session response confirms the exact launch context after preparation.
Load requires that same persisted context and an actual prepared launch;
explicit model/effort inputs survive frontend rehydration, and privileged
binding metadata does not turn a static target into a namespace-provider
target. Stopped sessions re-prepare through the existing owner. Legacy or mismatched
incumbents are not retrofitted, adopted or silently replaced by a new
conversation. This refusal is terminal even when ordinary resume allows a
fresh-conversation fallback. A persisted privileged binding also disables
receiver-side prompt auto-resume's recreation fallback, including when its
native conversation ID is missing; task admission must fail instead.
Old callers without the metadata keep their
existing ACP shape; already-elevated static launches can prepare their own
receiver-native selection.

After successful Session Host launch, the receiver persists preparation and
verified preference evidence in its session's extensible binding metadata,
paired with that exact host/child identity, nonce fingerprint and native
conversation ID. Verified selection updates refresh the same witness.
Following frontend rehydration, authenticated reattachment to that same
surviving child restores the witness without re-preparing, reloading or
replacing the running child. Missing or mismatched evidence refuses rather
than falling through to another launch. The host transport index retains its
existing addressing-only contract. Startup and post-cutover batch recovery
report a refused privileged record and retain its host, then continue with
other healthy records; an individual resume still propagates that refusal.

Source tests exercise SDK metadata and real registration with a simulated
elevation predicate. They do not prove a live Windows admin token, a second
account's access, installed rollout or native privileged skill acceptance.
Those remain separate authorized validation lanes.

## Productive joined-session observation

The existing extension control poll handles `control:plugin-readiness` separately
from prompts. It uses the public `session.rpc.metadata.snapshot()` before and
after `session.rpc.plugins.list()`. Session ID, physical cwd and the current
registered session must remain consistent. Native rows match projected
`name@marketplace`; they must be enabled and must not advertise `installed:false`.
Version is compared when advertised and expected; `installedFrom`, when present,
is checked against a known live **marketplace root**, not a plugin payload path.
Optional absent metadata does not reject otherwise valid native registration.
Generic `source` provenance is never interpreted as a structured Git origin.

The control waits on actual observations, not elapsed time, process liveness,
session/new acknowledgment, a skill-only probe or a model's readiness text.
Missing/disabled/incomplete rows remain pending until ready or timeout. Malformed,
unsupported, rejected and stale-context observations fail closed.

## Task and mode delivery

CLI acquisition waits before binding/admitting its owned task. Its existing
worktree/dispatch/head checks still run after the observation. The durable task
retains a readiness-control reference. The extension rechecks the joined native
loadout, retained message and cwd immediately before SDK send; an unready message
is left unacknowledged, with an explicit held reason, while ordinary inbox traffic
continues. Only a readiness-capable inbox poll receives these typed startup rows.
Admission and SDK acceptance remain distinct; a held row is not task execution.

`POST /live-sessions/{id}/plugin-readiness` exposes the readonly wait.
`SetModeRequest.plugin_readiness` and `BridgeClient.set_live_mode_after_plugins`
hold an explicitly owned autopilot/mode request before queuing a readiness-bound
mode control. The extension rechecks the selected loadout and native cwd before
applying that control, so a successful admission observation cannot authorize a
later mode change after the context drifts. Older extensions reject the new
control rather than treating it as an ordinary mode change. Mode requests without
the field retain their existing control shape; operator-native prompts are not gated.
Cancelling a readiness waiter settles its control; a late readonly acknowledgment
cannot authorize later task or mode admission. Retrying creates a fresh observation,
while existing task idempotency still prevents duplicate prompt admission.

Legacy provider/mux seed forwarding is not silently rewritten by this feature.
Those launch owners must carry typed ownership plus the compiled expectation and
withhold their seed before claiming this startup gate. No opaque CLI flags are
stripped and no additional launcher, profile or service is introduced.

The integrated detached Codespace/container owners consume that compiled context
and deliver owned seeds through the version-gated Bridge queue, not terminal
input. Admission can be admitted, refused or unknown; unknown delivery retains
the original operation, native identity and recovery ownership rather than
stopping a possibly productive session. Pending container delivery journals are
independent of disposable forwarding-keeper state. See
[detached provider readiness](../../agent-codespaces/docs/detached-plugin-readiness.md)
for retry, reconciliation and creation-receipt settlement.
