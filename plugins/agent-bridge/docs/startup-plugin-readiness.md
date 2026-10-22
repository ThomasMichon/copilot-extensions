# Bridge-owned CLI startup loadout gate

This is a registered/enabled plugin-loadout check, not an eager MCP authentication
or component-attestation requirement. It never installs, enables or reloads
plugins and never changes HOME, account selection, preferences or repository settings.

## Explicit caller ownership

A typed CLI acquisition can supply `plugin_readiness`:

The following values illustrate request fields, not a generated projection
output. Supply the exact prepared projected identity, version and root.

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
identities, available manifest versions, and known local marketplace roots;
private Git acquisition homes are deliberately not reported as productive
native marketplace roots. The directory return contract is unchanged.

Persisted targets retain static argument provenance and the local-preparation
flag inside the existing request-override metadata, not as new constructor
fields. The preceding target reader can hydrate and re-save those records
during rollback; the current reader restores preparation on a later resume.
Prepared loadouts and one-shot requests remain runtime-only.

The acquisition journal retains this semantic metadata across delivery/recovery,
not prepared argv or secret environment. Explicit caller worktree, qualified
owner and session context have separate optional request fields; opaque
acquisition affinity labels are never filesystem origins. Gitless legacy
launches remain explicitly not applicable. Existing privileged, wrapped and
different-world exclusions are not promoted to ordinary local preparation.

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
