# Hook Execution and Output Composition

Hook execution order and hook output composition are separate contracts. Use
only the guarantees below when authoring a repository hook or plugin.

## Stable execution rules

Copilot collects matching hooks through these publicly supported source tiers:

1. policy
2. user
3. repository or project
4. plugins

All matching entries run. Within one event array, entries retain their authored
order.

Those rules do not define a priority among plugins. Relative plugin order is not
alphabetical and is not an author-facing compatibility contract.
`enabledPlugins` is an enablement and precedence map, not a hook-order list.
Never use JSON key order, lexical plugin names, observed completion timing, or a
custom wrapper to choose a winner.

## Execution is not composition

All hooks running does not mean duplicate output fields all survive. Each event
and output field needs explicit composition semantics. A design that assumes
the last plugin result wins is a race even when one observed inventory appears
stable.

For `sessionStart` and `subagentStart`, the loss of independently correct
`additionalContext` values is tracked in
[github/copilot-cli#3589](https://github.com/github/copilot-cli/issues/3589).
[github/copilot-agent-runtime#17878](https://github.com/github/copilot-agent-runtime/pull/17878)
is implementation work toward preserving every start-hook value. Do not treat a
merged change, one development build, or one successful launch as the supported
contract.

## Current reliable pattern

Critical ambient guidance uses two plugin-owned pieces:

1. A checked-in static instruction projection tells the agent where the exact
   session's dynamic guidance file will appear and treats a missing file as a
   no-op.
2. An output-free `sessionStart` hook validates the runtime `sessionId`, writes
   only beneath that session's `instructions/<plugin>/` directory, and emits
   exactly `{}`.

The writer may call an underlying plugin-owned emitter to compute its content.
It must remain bounded, restart-safe, path-contained, and stale-replacing.
Different plugins write different files, so they compose through filesystem
ownership rather than competing hook output.

The suite scanner can prove an output-free hook through the payload-relative
manifest named in `plugin.json`:

```json
{
  "sessionContext": "session-context.json"
}
```

```json
{
  "schema": "copilot-extensions.session-context-contributors",
  "version": 1,
  "complete": true,
  "contributors": [],
  "sessionStart": {
    "sideEffects": "restart-safe-idempotent",
    "context": "none"
  }
}
```

The historical schema name is retained for compatibility. Its generally useful
role is now static output classification: an empty `contributors` list plus
`context: none` proves the hook cannot emit model context. Bootstrap,
registration, exact-session file writes, and other real side effects stay
direct and idempotent.

A complete declaration that identifies direct or legacy output remains a
possible non-empty result. A missing, malformed, or incomplete declaration does
not by itself prove output-free. The suite scanner also recognizes its bounded
standard exact-session writer, bootstrap, hook-client, and registration command
shapes when aggregate emitters and contributor wrappers are absent; other hook
commands are classified conservatively.

## Scanner rule

The scanner accepts:

- any number of complete-declared output-free hooks;
- a session-file-only or side-effect-only stack with no composition authority;
  and
- at most one possible non-empty `sessionStart` output.

It blocks more than one possible non-empty output unless the runtime version
floor has a separately proven native merge contract for that event and field.
An unavailable external payload is a warning by itself, but it joins collision
detection when another possible output is present. Reports expose identities
and roles only; they never print hook commands, contributor argv, or emitted
context.

## Future native-host seam

Direct plugin-owned `additionalContext` is the preferred fully dynamic design
after native host composition is proven at the supported version floor. The
proof must cover fresh, resume, non-interactive, and ACP launch paths with every
independent value preserved, not merely every hook process executed.

Only after that proof should a plugin replace its exact-session compatibility
path with direct output and declare the new output-capable contract. The host is
the only future composition authority. Do not add a cross-plugin discovery
plugin, source-qualified authority resolver, producer wrapper, rendezvous,
cache, spill file, or aggregate adoption configuration.

## Review rule

Flag any customization that:

- treats observed hook order as output ownership;
- claims an output-producing hook is side-effect-only;
- emits multiple possible non-empty values without proven native semantics; or
- recreates a custom cross-plugin composition authority.

Return to [`authoring-skills`](../SKILL.md#sessionstart-dynamic-guidance).
