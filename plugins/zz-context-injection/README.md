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

The payload currently requires an available Python interpreter. Native
dependency-free Bash and Windows PowerShell implementations remain required
before claiming fresh-machine parity.
