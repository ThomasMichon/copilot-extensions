# zz-context-injection

Compatibility aggregator for Copilot CLI hosts that retain only one
`sessionStart` `additionalContext` result.

The current implementation is a rollout scaffold. It resolves the effective
user plus trusted-repository plugin stack, verifies that the exact
`zz-context-injection@copilot-extensions` payload uniquely owns the final
lexical slot, and runs only pure, payload-declared context contributors. It
emits nothing unless every active plugin with a session-start hook provides a
complete declaration and the declared aggregate fits the plugin's intentional
64 KB and 20-second
admission budgets, preserving existing direct hook behavior during partial
rollout.

The `zz-` prefix is intentional compatibility behavior for affected host
versions. It is not a general plugin-priority mechanism. The aggregator verifies
that no active plugin sorts after it before emitting.

When a host orders a repository-local directory marketplace after installed
remote marketplaces, the repository may provide one thin tail adapter named
`zz-context-injection`. The adapter invokes this plugin's installed
`aggregate_context.py` directly, sets
`COPILOT_CONTEXT_INJECTION_AUTHORITY` to its own source-qualified identity, and
passes its own plugin root as `COPILOT_PLUGIN_ROOT`. The adapter contains no
composition logic; this payload remains the single engine and schema owner.

The payload currently requires an available Python interpreter. Native
dependency-free Bash and Windows PowerShell implementations remain required
before claiming fresh-machine parity.
