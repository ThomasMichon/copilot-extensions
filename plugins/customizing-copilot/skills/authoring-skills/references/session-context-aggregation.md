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
alphabetical and is not an author-facing compatibility contract. The effective
inventory may reflect persisted install or update history, directory
marketplace or catalog order, explicit repeated `--plugin-dir` order,
activation and deduplication, and implementation details. An explicitly
controlled `--plugin-dir` sequence may behave deterministically in a current
runtime, but that behavior is not a published compatibility guarantee.

`enabledPlugins` is an enablement and precedence map. It determines which
source-qualified plugin is enabled when settings layers or identities compete;
it is not a hook-order list. Never use JSON key order, lexical plugin names, or
observed completion timing to choose a winner.

## Execution is not composition

All hooks running does not mean duplicate output fields all survive. Each event
and output field needs explicit composition semantics. A design that assumes
the last plugin result wins is a race even when one observed inventory appears
stable.

For context output, prefer one of these ownership models:

- the runtime defines how every value for that event and field is merged; or
- one attributable owner performs composition, and producers route through
  that owner and emit no competing value after ownership is proven.

Repository settings enable the plugin; they do not own aggregation policy.
Repository adoption selects one exact source-qualified authority through
`.context-injection/config.yaml`. Adoption is data, not a boolean:

```yaml
schema: copilot-extensions.context-injection
version: 1
authority: context-injection@copilot-extensions
engine:
  schema: copilot-extensions.context-injection-engine
  version: 5
```

The authority must be that exact enabled marketplace plugin, and its adjacent
engine contract must match. The runtime reads the plugin-owned config only
after persisted repository-trust proof and rejects unknown keys, malformed or
unsupported YAML shapes, and path escape. Missing, malformed, ambiguous,
inactive, or incompatible proof disables aggregation and restores
producer-local direct emission.

For `sessionStart` and `subagentStart`, the loss of all but one
`additionalContext` value is tracked in
[github/copilot-cli#3589](https://github.com/github/copilot-cli/issues/3589).
[github/copilot-agent-runtime#17878](https://github.com/github/copilot-agent-runtime/pull/17878)
is ongoing implementation work to preserve context from every start hook. Do
not document that behavior as shipped until the change is merged and available
in the runtime version being targeted.

## Contributor declaration

The suite's session-context scanner can inventory a contributing plugin through
a payload-relative manifest named in `plugin.json`:

```json
{
  "sessionContext": "session-context.json"
}
```

The referenced file is data, not executable configuration:

```json
{
  "schema": "copilot-extensions.session-context-contributors",
  "version": 1,
  "complete": true,
  "sessionStart": {
    "sideEffects": "restart-safe-idempotent",
    "context": "authority-aware"
  },
  "contributors": [
    {
      "id": "ambient-policy",
      "pure": true,
      "order": 500,
      "timeoutSeconds": 5,
      "maxBytes": 8192,
      "bash": ["scripts/emit-context.sh"],
      "powershell": ["scripts/emit-context.ps1"]
    }
  ]
}
```

Every contributor is side-effect-free, read-only, re-entrant, and safe to run
in addition to its plugin's direct hook. Commands are payload-relative argv
arrays, never shell text, repository-provided commands, or `PATH` lookups. The
contributor `id` uses lowercase ASCII letters, digits, and interior hyphens,
with a maximum length of 64 characters. The
`order` field is composition order inside an owning aggregator; it says nothing
about host plugin execution order. `timeoutSeconds` is an integer from 1
through 10, and `maxBytes` is an integer from 1 through 65536. The Bash and
PowerShell command paths must remain within the payload, exist in an inspectable
payload, and end in `.sh` and `.ps1` respectively.

Split mixed hooks before declaring them. Bootstrap, registration, and other
side effects remain direct. A side-effect-only hook declares
`sideEffects: "restart-safe-idempotent"`, `context: "none"`, and an empty
`contributors` list. A context-only hook declares `sideEffects: "none"` and
`context: "authority-aware"`. A mixed plugin uses both declarations but keeps
the direct side-effect command separate from the pure contributor command.

An authority-aware producer asks the selected engine to rendezvous. It
suppresses its caller-specific direct `additionalContext` only when the exact
repository authority is proven active and compatible; every earlier failure
invokes its original pure contributor directly. After proof, every producer and
the selected authority emit the same cached aggregate. Post-proof contributor,
admission, or aggregate failures publish one shared cached `{}` instead of
returning to caller-specific fallback.

Rendezvous identity is the pair `(sessionId, canonical resolved cwd)`. Repeated
authority calls for that exact pair return byte-identical cached output, while
either component changing selects a different result. The authority may run
before, after, or concurrently with producers; every participating hook returns
the same aggregate bytes. Producer and authority hooks must set
their host-level `timeoutSec` to at least the engine's 25-second rendezvous
deadline; use 30 seconds to leave time for wrapper output. This host timeout is
separate from the pure contributor's `timeoutSeconds` field.

The declaration makes ownership and possible outputs inspectable. It does not
assign host execution order. The rendezvous and byte-identical shared output
make that order irrelevant even when a later empty result would otherwise erase
earlier context.

If repeating the full aggregate from every hook would exceed a host-wide budget,
the authority may atomically spill the complete attributable context to the
exact session's `files/` directory and return a byte-identical compact kernel
with an absolute load-before-action pointer. The pointer is immediate context;
the full file is deferred context. Session identity and path containment must
be validated before writing.

## Review rule

Flag any customization that:

- treats observed hook order as output ownership; or
- emits competing values without runtime-defined field semantics or one
  attributable composition owner.

Return to [`authoring-skills`](../SKILL.md#sessionstart-context-injection).
