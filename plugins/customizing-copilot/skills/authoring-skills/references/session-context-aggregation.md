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
  that owner or return the same owned aggregate without relying on ambient
  plugin order.

Do not use a plugin's name, its position in `enabledPlugins`, or current
inventory order to make an aggregator "last."

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
`order` field is composition order inside an owning aggregator; it says nothing
about host plugin execution order. `timeoutSeconds` is an integer from 1
through 10, and `maxBytes` is an integer from 1 through 65536. The Bash and
PowerShell command paths must remain within the payload, exist in an inspectable
payload, and end in `.sh` and `.ps1` respectively.

Split mixed hooks before declaring them. Bootstrap, registration,
reconciliation, and other side effects remain direct and use `complete: true`
with an empty `contributors` list when the plugin cannot emit context.

The declaration makes ownership and possible outputs inspectable. It does not
prove that an aggregator runs last, and it cannot turn ambient plugin order into
an arbitration mechanism.

## Review rule

Flag any customization that:

- treats `enabledPlugins` key order, lexical names, catalog order, or observed
  plugin order as output precedence;
- requires one plugin to be the last context emitter; or
- emits competing values without runtime-defined field semantics or one
  attributable composition owner.

Return to [`authoring-skills`](../SKILL.md#sessionstart-context-injection).
