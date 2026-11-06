# Inactive runtimes during suite updates

Plugin capability enablement and runtime activation are separate. A suite
update preserves the enabled payload while allowing its owner to report that
no runtime is configured.

The registered-runtime and module planners require an explicit
`runtimeActivation` declaration. General service readiness is not runtime
participation: `configuration-empty` can describe an enabled, healthy Vault
without a database or a configured Containers fleet without members. Those
runtimes still require normal update and receipt verification.

The version-1 declaration in `plugin.json` is:

```json
{
  "runtimeActivation": {
    "schema": "copilot-extensions.runtime-activation",
    "version": 1,
    "module": "agent-example/runtime",
    "path": "scripts/runtime-activation.py"
  }
}
```

The named module must be the sole module in the existing `installerReadiness`
declaration and declare `configurationEmpty: satisfied`. The reader is a
contained, non-link Python script invoked with the updater's interpreter,
`-I -B`, `--cwd <configuration-directory>`, `--runtime-root <selected-root>` and
`--context <selected-receipt-or-empty>`. It must read configuration only:
no provisioning, service start, database access or credential reads. It uses
the version-1 `copilot-extensions.module-readiness` envelope, but its decisions
are **exit 0 / `configured`** or **exit 2 / `configuration-empty`**.
Other exits, mismatched module identities, malformed envelopes and inconsistent
exit/state pairs refuse the decision. An ordinary readiness command returning
exit 0 / `configuration-empty` does not qualify.

Only the explicit inactive decision yields `SKIPPED (configuration-empty)`.
Probes run from the invoking configuration directory, with the selected
installation environment, a thirty-second deadline and 64 KiB limits per
output stream. They do not invoke an installer or a platform shell. Vault
declares its existing configuration-only reader; Containers does not opt in.
Older updaters ignore the additive plugin field. New updaters retain ordinary
receipt verification for payloads without it; declaration and consumer rollout
must both reach the installation before inactive skipping becomes available.
Owners without this declaration retain their selected repository and role:
the updater does not replace a gate-excluded host context with an allowed peer.

All collected enabling-repository contexts are retained: a shared runtime is
inactive only when every context reports inactivity. A context that needs the
runtime supplies the installer's configuration directory. Prepared source and
target admission are revalidated before execution-time probes, not just before
the installer. Duplicate module identities refuse before any probe.

Suite, targeted and inventory paths retain the same context selection.
The updater rechecks inactivity at execution. If an inactive plan has become
active, it refuses the stale plan and requests another update. Active, unready,
unsupported and legacy owners retain the ordinary installer/version-receipt
requirements. Invalid declarations or malformed successful probe output are
errors, never evidence of inactivity.
Execution refreshes the machine gate even for a single context, retaining the
original explicit repository or native anchor fallback rather than substituting
the probe's working directory as policy authority.

This is not a claim that a retained runtime is current or healthy. No version
receipt, service, configuration or capability setting is changed to make the
skip succeed. In particular, an old Vault runtime can remain installed while
its current configuration explicitly opts out. Removal and service retirement
remain independent owner operations.

The behavior implements the existing
[authoritative replacement contract](../../../docs/install-contract.md#authoritative-replacement-and-completion):
only configured requested runtimes require incoming readiness and selection.
It does not relax active-runtime receipt, publication or serving requirements.
