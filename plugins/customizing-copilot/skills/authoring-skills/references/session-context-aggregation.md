# Session Context Aggregation on Affected Hosts

Some Copilot CLI host versions run every plugin `sessionStart` hook but retain
only one non-empty `additionalContext` result. Suite plugins that emit context
must retain their ordinary direct `sessionStart` output as the standalone
backup. Publishing a contributor never authorizes removing that backup.

Exactly one source-qualified plugin, `zz-context-injection`, may act as the
late/final aggregate authority. It may supersede earlier direct backups only
after proving that the affected host orders it after every other active plugin.
For the current guaranteed-last mode:

- the authority plugin name is lexically after every other active plugin name;
- every active plugin with a command `sessionStart` hook has a complete
  declaration; and
- no second aggregate authority is active.

If any active plugin, declaration, ordering fact, or stack input is unknown or
incomplete, the authority emits `{}` and direct backups remain authoritative.
Observed completion timing is not an ordering proof.

## Contributor declaration

A contributing plugin names a payload-relative manifest in `plugin.json`:

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
`order` is an integer; `timeoutSeconds` is an integer from 1 through 10; and
`maxBytes` is an integer from 1 through 65536. The Bash and PowerShell command
paths must remain within the payload, exist in an inspectable payload, and end
in `.sh` and `.ps1` respectively.

Split mixed hooks before declaring them. Bootstrap, registration,
reconciliation, and other side effects remain direct and use `complete: true`
with an empty `contributors` list when the plugin cannot emit context.

## Broker alternative

When final ordering cannot be proven, deterministic aggregation requires every
possible producer to return byte-identical output from one compatible,
source-qualified broker. Until that broker contract is explicitly declared and
mechanically provable, contributors keep direct output and the aggregate
authority stands down.

Return to [`authoring-skills`](../SKILL.md#affected-host-aggregation-rule).
