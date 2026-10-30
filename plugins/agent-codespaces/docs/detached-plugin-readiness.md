# Bridge-owned detached startup

Detached CodeSpace and trusted-container launches prepare repository plugin
registration before launching their native CLI. The provider transports the
canonical writer's version-1 `__VENUE_PLUGIN_CONTEXT__` record alongside the
unchanged `__VENUE_PLUGINS__` directory result. Metadata must belong to the final
directory frame and actual execution checkout. It carries effective projected
identities, logical identities, known versions and known local marketplace roots;
Git acquisition cache paths are not productive native marketplace roots.
Installed-only payloads marked `acp_only_sources` cannot claim settings-only CLI
registration without an original marketplace declaration.

## Guest tool readiness

After repository admission and plugin preparation, detached startup checks the
actual `agent-worktrees --help` command before trust-folder, project-registration
or native-launch effects. This loads Worktrees' full command surface; finding a
binstub or reading its version alone is not a runtime-health check.

Legacy guest preparation uses the owning payload's installer: `install` for a
missing command and `repair` for a present command that fails this check. Repair
keeps that installer's distinct-generation, health and publication rules; this
caller neither rebuilds a completed slot in place nor changes a version marker.
It checks the command again after preparation. Installer failure, missing
installer, failed post-check or failed Bridge payload installation stops startup
with a visible error. An explicit installation context refuses this legacy
preparation path before even invoking its health shim: a first-use shim can
provision state even for `--help`. Its selected installation must be prepared
by its owner. This is local command health, not serving or model readiness.

The actual constructed-command controls use private synthetic tools under the
contained runner and are opt-in T3, not automatic unit-only CI:
`python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_copilot_detach.py -k venue_tooling --allow-explicit-tiers`.
They exercise the shell consumer's allow/refuse ordering, not a real remote
installation, shared guest or installed-host rollout.

## No owned TTY seed

Repository hosting eligibility is checked inside the selected guest checkout,
including a resolved explicit worktree, before launch preparation. ACP commands
also carry a guest-side check before the native process. The receiver must
support `agent-worktrees agent-policy check --path <checkout> --operation launch
--json`; an unsupported receiver is a visible refusal, not permission
to fall back to an unchecked launch. On an unprovisioned legacy CodeSpace only,
absence of that command selects the canonical provisionless reader described
in the repository-policy library. This reads the actual guest checkout before
claims, credentials or plugin installation, using a temporary stdin bundle rather
than requiring Worktrees to be installed already. Both probes use the same guest
login environment; the isolated reader refuses any explicit installation context,
including one exported by login initialization. Existing receiver failures never
select this fallback. Only explicit preflight checks allow this reader; the
final launch check requires the installed receiver even when provisioning or
plugin preparation was skipped. This does not add a container fallback.
The host's venue-name launch policy is a
separate restriction, never a replacement for repository policy.
Direct managed `ssh --stdio` uses this read-only admission before taking a
venue claim, minting relay credentials, or provisioning hooks and plugins. It
retains the final guest-side check. Ordinary non-stdio source/diagnostic SSH
does not acquire an agent-hosting requirement.

A genuine live attachment preserves its session and custody. If that child
exits during rejoin, the receiving Worktrees launch boundary must check current
repository policy before creating a replacement. A refusal must not create a
child and then stop it. Saved launch history and pending delivery receipts
remain available. Policy semantics belong to the shared
[repository-policy library](../../../libs/repository-policy/README.md).

The existing Worktrees owner receives `--bridge-seed-operation`, not the task,
a task-file pointer, or its reference-file note through `--seed`.
`--bridge-seed-pending` preserves new-task conversation intent without exposing
the prompt to the native launcher. Explicit native arguments are preserved.
Attached/operator keystrokes and configured operator prompts remain outside this
Bridge-owned delivery contract.
An ordinary rejoin still does not replay the host launch task. Newly uploaded
references and an explicitly deferred creation receipt are delivered after
readiness; a transport retry of this new never-TTY launch can safely deliver its
held task once under the same operation.

A pending creation seed uses the existing launch-seed take/finish receipt.
Retries retain its original operation. Legacy handoffs that may already have
typed a seed are not reclaimed. The owner returns its deferred receipt; after
durable inbox admission, `bridge-seed-settle` finishes that exact receipt through
the existing Worktrees authority. An unconfirmed handoff is retained, never
silently considered delivered or retyped.

## Readiness-bound inbox admission

After native registration and resume/alias settlement, the provider uses the
existing Bridge CLI send boundary. `--plugin-context-stdin --prompt-file -`
accepts a JSON envelope:

```json
{
  "prompt": "The owned task and any reference-file note",
  "plugin_context": {
    "version": 1,
    "cwd": "/workspaces/project",
    "plugins": [
      {"projected_source": "helper@tools", "logical_source": "helper@tools"}
    ],
    "logical_payloads": {"helper@tools": "/workspaces/project/plugins/helper"}
  }
}
```

The payload travels over stdin, not a large or credential-bearing argv.
The client maintains HTTP protocol 41 on every attempt. The actual messages
route resolves the native session, uses the supported joined-session plugin
observation, then admits a typed startup message. The extension checks the same
loadout and native context again immediately before SDK send. Hook-only and
MCP-only selections use native registration, never a skill-presence probe or
eager authentication of every backend.

The operation key binds the prompt and compiled selection. Its first readonly
readiness control retains a non-secret input digest; failed/cancelled retries
make fresh observations without queuing the task early. Changed operation input
is refused, while an admitted retry returns the original message. Ordinary
reference notes without prepared metadata retain their existing behavior.

Missing metadata, old peers, readiness errors and ambiguous legacy TTY receipts
never fall back to TTY or ordinary send. A new, unrepresented child is cleaned
up on failure; an already admitted task is not killed merely because creation
receipt settlement loses its reply. Inbox admission is durable ownership, not a
claim that the model has consumed or completed the task.

Confirmed refusal failures include an optional `refusal_reason` with the fixed
Bridge startup code, without copying raw error details. Older or unknown reasons
remain generic; admission and retry authority are unchanged. The shared
[owned-delivery diagnostic contract](../../../libs/venue-copilot/README.md#owned-delivery-refusal-diagnostics)
also governs the trusted-container and SSH detached consumers.

## Ambiguous admission is retained ownership

Prepared sends use the CLI's structured JSON admission receipt. A nonzero exit,
missing/malformed result, lost response or timeout is **unknown**, not evidence
that no task was queued. The launcher performs at most two sends with the same
native SID, operation key and byte-identical input; a duplicate admission returns
the original message. After any ambiguous attempt, a subsequent refusal cannot
erase that uncertainty. Only an authoritative admitted receipt resolves it.

Unresolved delivery returns a pending/busy result, never `seeded` or a
success-shaped “no task”. It does not authorize mux cleanup or release of the
delivery's supervisor/CLI ownership. Private pending records live independently
of expiring CodeSpace connection tenants and disposable container keepers. They hold
the exact envelope, native SID, operation, input digest and creation receipt.
They contain no authentication snapshot. Rejoining recovers that record without
starting another mux or changing its input. Unknown admission first restores and
authenticates the reverse forward at the retained native port/mux, then replays
the original operation. Failed forward restoration leaves the journal pending
without sending a task. Confirmed admission is never resent or downgraded after
native-session exit: only its original creation receipt needs settlement.
Successful recovery returns the existing message ID.

CodeSpace workflow journals survive stale heartbeats, the absolute session-lease
cap, and release of another tenant. Migrating the unreleased embedded format
persists the journal before tenant pruning. Journal retention does not renew a
connection lease. An explicit unknown-admission retry uses a separate,
per-attempt recovery tenant bounded to ten minutes and releases it on exit;
it does not restart the native session or extend the original session lease.

Only a structured, confirmed pre-admission refusal permits destructive cleanup.
Raw reference-note calls without prepared metadata keep their legacy Boolean
contract. Consumed per-launch authentication inputs retain their normal cleanup;
the container's outer generic launch inputs are retained while admission is
pending and cleaned only after reconciliation.

Container workflow journals are not disposable keeper process state. Keeper
replacement migrates the unreleased embedded format under the existing lock
before pruning or stopping the old keeper. A failed replacement, another scope's
forward change, or ordinary keeper-state removal cannot erase an unresolved
delivery. Public keeper-state reads still expose `pending_deliveries` for
recovery, even without a running keeper. Reconciliation removes an empty journal
only after confirmed admission and creation-receipt settlement; stopping a
forward is not an implicit abandonment of that workflow.
