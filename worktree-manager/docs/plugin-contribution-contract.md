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
  target a selected row. Command view actions are displayed above the list,
  including an empty list; their `when` gates match the provider's current
  **summary**, and argv placeholders are substituted from that same summary.
  The provider must publish explicit status rather than using an empty array
  as a readiness signal. `progress: true` commands use a nonblocking modal with
  full multiline progress text and Cancel/Close; successful completion refreshes
  the originating provider. Listing, rendering and changing pivots never invoke
  these actions. Commands receive the same payload/provenance resolution as row
  actions. The CodeSpaces contribution uses this for Sign in / Authorize, while
  credential selection and vendor authentication remain provider-owned;
- the lightweight preview exposes only command view actions as buttons; forms,
  cards and internal argument arrays are never treated as subprocess commands.
  Preview discovery resolves commands against the contributing payload root just
  as production does. A declared but missing owned command remains unavailable,
  even if a same-named command from another installation is first on PATH;
- optional action `shortcut`, used by the generic interaction shell to expose
  the action's keyboard affordance without hard-coding a provider's keys;
- top-level `worktree_actions`;
- top-level `config_sections`;
- optional `config_sections[].configure_action`, with the same `label`, `key`,
  `fields`, `run`, and `confirm` schema as `create_action` below. The section's
  `run` remains a bounded read-only status query. After it succeeds, the Picker
  displays its output and description alongside the form, with **Apply/Cancel**
  controls. Apply substitutes `{field.<name>}`/`{fields}` and picker context in
  one pass; collected values are never reinterpreted as templates. With
  `confirm: true` on the form action, a second inline prompt shows the collected
  target/action values and defaults to Cancel. Opening, loading options, or
  cancelling never invokes the form's `run`. Dynamic `options_command` fields
  return a JSON array of non-blank strings; missing/failed/invalid/empty results
  stop this form with an explicit error, never an arbitrary free-text fallback.
  Unlike create forms, configuration forms do not force `allow_other: true`.
  When explicitly allowed, custom choice defaults restore the Other control.
  Configuration multichoice Other values use one custom value per line, so
  multiple saved custom members restore and remain independently editable.
  Ordinary create and steering forms retain their existing Other semantics.
  All nested commands receive the same attributable target resolution and
  validation as other contribution commands. The provider owns revalidation,
  local persistence and the meaning of removing an override; the Manager does
  not import its domain runtime. Without `configure_action`, existing one-shot
  sections behave unchanged;
- `confirm: true` on a pivot action, a `worktree_actions` entry, or a
  `config_sections` entry gates that action behind a native modal
  are-you-sure before it actually runs — Cancel is the gate's initial
  choice, so a reflexive Enter never runs the action;
- optional top-level `create_action` — a pivot-**level** "New …" affordance
  (no row selected), distinct from a row-scoped `kind:"form"` action:
  `{"label": <str>, "key"?: <str, default "create">,
  "fields"?: [{"name": <str>, "type"?: "text"|"textarea"|"choice"|
  "multichoice" (default "text"), "options"?: [<str>, ...] (required,
  non-empty, no blank entries, for choice/multichoice), "allow_other"?:
  <bool>, "show_when"?: {"field": <str>, "equals": <str>}}, ...],
  "run": [<str>, ...], "confirm"?: <bool>}`. Each field name must be unique
  after whitespace-trimming. A `show_when` must name a different,
  unconditional (no `show_when` of its own) `choice` field declared
  elsewhere in `fields`, whose `options` include the predicate's `equals`
  value — the runtime form evaluator can only resolve a `choice` field's
  current answer, so any other shape can never match. `run` is substituted
  via the same `{field.<name>}` token machinery a row-scoped form action
  uses (no row/entry tokens are available since there is no selected row)
  and is command-resolved/validated identically to every other `run` argv.
  **Live in the Picker UI**: a pivot declaring `create_action` gets a
  data-driven "New …" button (its label taken verbatim from the manifest)
  when its row list has focus but no row is meaningfully selected; Enter
  opens a modal built from `fields` (tabs when there is more than one,
  Enter/Ctrl+Left/Right to navigate, `show_when` hides/reveals dependent
  fields live) and Confirm substitutes/runs `run` via the same pivot
  runtime a row-scoped form action uses. `confirm: true` adds an inline
  are-you-sure before the command actually runs;
- optional top-level `worker` — `{"worktree": <field>, "label"?: <field>,
  "live"?: <field>, "activity"?: <field>}` — marking the pivot's rows as remote
  workers a worktree supervises. The Manager joins cached rows to Worktrees rows
  by the full driving-worktree id in `worktree`, renders a
  `→ <label> <live> <activity>` line under the supervising worktree, keeps the
  pivot loaded in the background, and never treats such a pivot as a claiming
  task (no phase badge). `label` defaults to `entry.id`. Each supervised
  worker also appears as a `Worker: <label>` entry in that worktree's Actions
  menu, which opens the venue row's own actions. Venue rows can use the
  internal verbs `open-venue-window` (attach with the provider's
  `copilot <id>` in a new tmux window or Windows Terminal tab, passing the
  row's `effort` as `--effort` when present), `open-bridge-ui` (the
  agent-bridge live-session web view), and `send-worker-message` (a small
  modal that sends typed text to the row's live `session_id` — Ctrl+S steers
  the running turn, Ctrl+X interrupts it — via `agent-bridge send <sid> -`
  with the text on stdin).

The command arrays are process-boundary contracts. The Manager invokes the
contributing plugin's canonical CLI; it never imports the plugin runtime or
reads the plugin's private state.

## Configuration backstage contributions

A list contribution may declare `"placement": "config"` (the default is
`"left"`). It becomes a full-content section of Configuration, alongside
Profiles, using the same `list`, `items_field`, `entry`, `columns`, `summary`,
`actions`, and `view_actions` vocabulary as an operational pivot. Configuration
uses horizontal section navigation instead of the Machines band. Section
navigation and loading content mount before any provider command completes;
failed reads remain visible and retryable. Machine-local sections declare
`"scope": "global"` and resolve their local target in their own CLI, never from
the previously selected operational machine.
Configuration placement requires explicit `"scope": "account"` or `"global"`;
omitted/default machine scope is rejected because there is no Machines band.
Its row-action `{machine}` context is empty rather than inheriting a hidden
operational target. Legacy `config_sections` receive the local identity in
their compatibility context; they never inherit a previously selected peer.

Configuration `list` argv may explicitly request `{repo}` (the Manager's bound
selected project) and `{configuration_workspace}` (its explicit bootstrap
`workspace`, or empty when unavailable). The engine resolves this tracked
worktree root separately from bootstrap `cwd`, which is only an optional anchor
switch destination. The Manager never substitutes that destination or ambient
CWD when the workspace field is absent. An unbound requested project is a
visible section error before the provider runs. Legacy lists that request
neither placeholder keep their existing context. Providers must pass these
bindings explicitly to their owner APIs, treat an absent workspace as
unsupported inspection, and return exact resource bindings in action rows;
they must not substitute an inherited process CWD or hidden operational machine.

Configuration row forms use the existing `kind: "form"`, `fields_from`,
`title_from`, `body_from`, `{field.<name>}` and `{fields}` contract. The selected
row supplies fields, initial values, and effects prose through those paths.
The host renders an editor in the Configuration workspace, not a task-steering
dialog: Cancel performs no write or draft save. `confirm: true` shows the
collected values and provider effects before submission, with Cancel selected
initially. Providers must revalidate values and target identity on submission;
displaying a preview is not authorization to skip validation.

### Optional provider-computed preview

A Configuration **row form** may add a `preview` argv alongside `run`, with
`confirm: true`. This is additive within version 1; existing forms keep their
static confirmation. Preview is not supported on operational forms, create
actions, view/worktree actions, or legacy `config_sections` forms.

```json
{
  "key": "set-local",
  "label": "Set local preference",
  "kind": "form",
  "fields_from": "card.request_input",
  "confirm": true,
  "preview": ["agent-example", "preference", "preview", "{id}", "{field.value}"],
  "run": ["agent-example", "preference", "apply", "{id}", "{field.value}", "--preview", "{configuration_preview}"]
}
```

The selected row supplies `configuration.target` with nonempty string
`installation`, `machine`, and `resource` identities. After collecting fields,
the host immediately shows a pending screen and runs the bounded read-only
preview off the render flow. Apply remains disabled until stdout is one strict
JSON object with exactly these fields:

```json
{
  "version": 1,
  "token": "opaque-owner-computed-proposal",
  "target": {"installation": "example", "machine": "local", "resource": "sample"},
  "scope": "machine-local",
  "operation": "set",
  "summary": "Set a preference for future launches",
  "effects": ["Only the supported machine-local override changes."],
  "findings": [{"code": "future-only", "severity": "warning", "message": "Existing sessions are unchanged."}]
}
```

`version` must be integer 1, `token` a nonempty string, `summary` a string,
`effects` an array of strings, and `findings` an array of code/severity/message
string records. Findings use `error`, `warning`, or `info`. The returned target
must equal the row target, scope must be `machine-local`, and operation must
be `set` or `reset`. Malformed/unsupported output, a target mismatch, nonzero
exit, timeout, or error finding blocks Apply. Failure is visible and retryable.
Warnings, target, effects, and collected non-secret values are displayed before
consent; Cancel is focused initially. Edit preserves in-memory values but
requires a new preview. Cancel saves no draft. A new interaction or changed
section/project/action/row invalidates an earlier response.

Only explicit Apply substitutes the received token into the reserved
`{configuration_preview}` context token. A row or editable field cannot provide
it; `{field.configuration_preview}` remains an ordinary field, not the host
token. Substitution is single-pass: braces in values are literal. The owner
must reject missing/empty/invalid tokens before writing, including when an old
Manager ignores `preview` and substitutes the unknown token with empty text.

The token is a stale-proposal check, not a credential. Under its shared mutation
serialization, the provider must revalidate authority, exact target, operation,
normalized values and all relevant source/local baselines before writing.
Conflicts require fresh preview and consent, never an automatic broadened retry.
Reset removes the supported local override and restores current inheritance;
it never persists a copy of an inherited value. Provider code owns these writes,
not the Manager. Browse, refresh, preview, edit and cancel must not create state,
ensure a daemon, reserve work or launch agents. The provider must redact secrets
before emitting rows, fields, previews, diagnostics, receipts, or argv values;
this is not a secret editor. Apply uses the existing full stdout/stderr/exit
receipt and refreshes the originating scope. Missing preview commands block
only the affected action, leaving its read-only section and peers usable.

Workers owns its catalog, local-instance lifecycle, and activation receipts.
Catalog rows distinguish configured instances from templates and separately
expose provenance, prerequisites, desired enablement, observed activity, and
statistics. Source-owned rows are read-only apart from supported local controls.
Template instantiation writes a new, stable user-local identity; Edit updates
that identity against an explicit revision baseline. Save-only never activates
or creates agentic work. Save and Activate requires explicit effects consent:
it may make an existing supervisor reconcile the declaration and subsequently
create tasks or launch agents. A receipt distinguishes persistence success from
activation pending, acknowledged, or failed, and never equates enabled with
running. Providers own collision refusal, stale-edit refusal, and retry-safe
submission; the Manager does not create another registrar or supervisor.

Legacy `config_sections` remain supported. Their destination/loading content
opens immediately; status and strict option reads fill that workspace
asynchronously. A one-shot command remains explicitly action-triggered rather
than becoming a mutation performed merely by browsing Configuration.

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
