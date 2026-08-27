# Worktree Manager plugin contribution contract

**Contract version:** `1`

The Worktree Manager is the optional human control-plane. Each `agent-*` plugin
remains independently installable and contributes optional Picker surfaces by
shipping static JSON under its own payload:

```text
<installed-plugins>/<marketplace>/<plugin>/pivots/<name>.json
```

The Manager scans those payloads directly for plugins enabled user-global or for
the selected project. A plugin installer does not copy into Manager-owned state,
and neither side imports or declares a package dependency on the other.

## Manifest

Every new manifest declares:

```json
{
  "schema_version": 1,
  "label": "Tasks",
  "entity": "task",
  "after": "Worktrees",
  "home": false,
  "list": ["agent-example", "list", "--json"],
  "items_field": "entries",
  "entry": {
    "id": "id",
    "title": "title",
    "subtitle": "description",
    "worktree": "target_worktree",
    "group": "group",
    "badges": ["state"]
  }
}
```

Version-1 compatibility also covers the existing declarative fields:

- `columns`, `summary`, `scope`, `stream`, and `subscribe`;
- optional `ready_status`, formatted from the loaded summary plus generic
  `project`, `count`, and `label` values before declared shortcut hints;
- `items_field` when an object-shaped list envelope names its row array
  something other than `entries`;
- optional `entity`, a stable semantic concept such as `worktree`, `task`, or
  `venue` that lets generic Manager interactions recognize cross-cutting row
  semantics without selecting a provider-specific renderer;
- `home: true` to designate the ordinary initial pivot through the same generic
  contract as every peer (at most one enabled contribution should declare it);
- pivot `actions`, including command, `internal`, `form`, and `card` kinds;
- pivot `view_actions`, with the same action kinds, for operations that do not
  target a selected row;
- optional action `shortcut`, used by the generic interaction shell to expose
  the action's keyboard affordance without hard-coding a provider's keys;
- top-level `worktree_actions`;
- top-level `config_sections`.

The command arrays are process-boundary contracts. The Manager invokes the
contributing plugin's canonical CLI; it never imports the plugin runtime or
reads the plugin's private state.

## Compatibility

- Missing `schema_version` is accepted as legacy version 1 during migration and
  reported as a typed `legacy-schema` finding.
- New fields may be added within version 1. Removing or retyping an existing
  field requires a new contract version and a compatibility window.
- Action `kind` is a closed version-1 enum: `command`, `internal`, `form`, or
  `card`. Adding a new kind requires a new contract version or an additive
  representation through an existing kind.
- One malformed, disabled, missing-command, or duplicate contribution never
  prevents valid peers from loading. `worktree-manager contracts` reports typed
  findings for each inactive entry.
- Command readiness is per surface: a missing pivot list command disables that
  pivot, while a missing optional action/config command disables only that
  action or section.
- Multiple enabled `home: true` pivots remain loadable but produce a
  `duplicate-home-pivot` finding; the first available contribution in discovery
  order wins deterministically.
- A manifest requiring a newer schema is reported as
  `unsupported-schema-version`, with an update-the-Manager remediation rather
  than being classified as corrupt.
- During the migration window, the report also compares the old
  `~/.agent-worktrees/pivots` copy registry with enabled payloads and reports
  stale or orphaned legacy entries. The Manager never uses those copies as its
  source of truth.
- The contribution file is inert when Worktree Manager is absent. A plugin's
  own CLI/service behavior is unchanged.

`WORKTREE_MANAGER_PLUGINS_DIR` overrides the installed-payload root for tests and
recovery. During coexistence, the older `AGENT_WORKTREES_PLUGINS_DIR` spelling is
also honored as a fallback.

## Inspection

```bash
worktree-manager contracts
worktree-manager contracts --project <project>
worktree-manager contracts --project <project> --json
```

The report is the Manager's doctorable view of the contract registry. The
production Picker consumes the same parsed model as it migrates out of
`agent-worktrees`.
