# Workers: catalog and user-local instances

The Manager's **Configuration → Workers** section is a resource table, not a
configuration dialog. Its standalone `pivots/workers-config.json` contribution
uses `placement: config`, `scope: global`, and the ordinary list/column/card/form
contract. The Tasks pivot and the legacy override CLI remain operational.

## Browse before acting

`agent-dispatch workers catalog` returns `{entries, summary}` without creating
directories, starting services, changing configuration, or querying the task
coordinator. The target is **the invoking CLI's local machine**, independent of
the operational machine selected elsewhere in the Manager.

The table distinguishes:

- **Configured**: user-local instance drafts, with **Edit** actions.
- **Source declarations**: discovered repository/plugin declarations, read-only
  here. Ownership/provenance identifies their configuration flow.
- **Available templates**: built-in recipes plus a bounded worker-pool template,
  with **Instantiate** actions. Recipe fragments are not complete instances.

### Shared catalog metadata

The JSON envelope identifies `schema_version: 1` and a `fetched_at` epoch
timestamp. The latter is a read time, not proof of recent worker activity.
`summary.activity_updated_at` separately carries the supervisor receipt time
when available; `activity_known` and `activity_reason` retain the existing
freshness judgment.

Each row has a `kind` and `components_basis`. For a concrete configuration,
`components` projects the effective registrations' IDs, names, runtime and
declaration kinds, owners, repo scope when declared, labels, source paths when
known, and content revisions. Executable specs, command argv and environment
values are not included in component metadata. A multi-component configuration
has kind `composite`, including when several components have the same kind.

`repo_scopes` preserves every declared scope in first-declared order, including
wrapped schedule entries and their `default_repo` fallback. The legacy
`repo_scope` string retains the first scope for compatible older consumers;
use the list for a complete view. Evaluator all-repository opt-in is `all`.
Labels come from each kind's actual routing configuration: lane labels,
evaluator watch labels, schedule task labels, or a composed emitter's nested
task label. No title/name classification is used.
The built-in CI-repair emitter projects its producer-owned lane (or repository
fallback) and fixed task label through the same routing helper used when
creating an admitted task.

File-backed declarations retain the observed leaf source document through
repository and verified-plugin discovery. Local instance components name the
draft or published file matching their basis. This attribution does not alter
the executable registration. The content revision fingerprints the complete
canonical registration, so an executable change remains observable without
returning its command, spec or environment values.

For a published local instance the component basis is `published`, even if
its Save-only draft has changed shape. An unpublished instance uses `draft`;
source-owned declarations use `source`. An unreadable publication uses
`unknown` and exposes no inferred components. Uninstantiated templates have
their declared kind, basis `template` and an empty component list, not fabricated
registration IDs. Discovery-error rows similarly contain no components.

These additive fields let independent terminal and graphical consumers
describe the same owning catalog without classifying sources from titles or
reconstructing composed registrations. The Manager table shows Kind and its
Details card lists component identity, scopes, labels, source documents and
content revisions. At standard terminal widths the secondary
Kind column yields to the existing status, capacity, source and description
columns; wider terminals show it alongside them. Existing IDs, edit revisions,
form fields and activation semantics are unchanged.

Projection failures are isolated per row, including registrar-supported
specifications whose full routing metadata is malformed. The row is marked
`metadata-error`, its `component_errors` also appear in `summary.errors`, and
its component basis is `unknown` with no invented components. Healthy sources,
instances and templates remain visible. Existing enablement/publication facts
are not replaced with a fabricated stopped state; details name the failed
projection. Any catalog error disables toggle affordances consistently, even
when the error is found after another row was projected.

Descriptions, source attribution, validation, publication, capacity, effective
disable overrides, and observed activity are separate facts. A recent local
supervisor receipt supplies `observed-running`; an absent/stale receipt means
`unknown`, not active. Statistics count expanded declarations, lane capacity,
and observed running registrations, not fabricated task-throughput metrics.
Registrar failures appear in `summary.errors`; unsafe mutations refuse them.
Corrupt draft files appear as repair/error rows without hiding healthy drafts
or available templates. Mutations still require every stored draft to be
readable and internally consistent; partial browse results never authorize a save.
For an instance with a newer Save-only draft, runtime state, capacity and
enable/disable controls come from its still-published snapshot. Details expose
`draft_state`, `draft_statistics`, the current edit revision and the published
revision separately; editing the draft does not relabel a running worker.
An unreadable or invalid published snapshot remains visible as a configuration
requiring repair, with unknown runtime state and disabled local toggle actions.
The catalog retains other instances and templates and includes the path and
reason in that row's `publication_errors` and `summary.errors`; it never
substitutes draft capacity for an unreadable publication.

## Edit or instantiate

Select **Edit — Save only**, **Edit — Save and Activate**, or the corresponding
**Instantiate** action. The full-content form supplies:

| Field | Meaning |
|---|---|
| `name` | Unique local instance name; immutable after creation |
| `declaration` | Complete JSON declaration, prefilled with current values or a recipe scaffold |
| `repo_root` | Existing absolute repository root, required by issue/effort loops |

The declaration uses the existing registrar's schema, filters, capacities, body,
recipe composition, and loop expansion. The pool template watches `work` with
one headless lane; customize labels and placement before activating.
Repository issue loops additionally need repo/source/task label/cadence,
forge producer identity, reservation policy, and any referenced evaluator.
Reviewer recipes need discovery commands, evaluator policy, and target inputs.
Issue loops have the existing concurrency-one contract. Named worker identities,
evaluator references and executable dependencies must resolve before persistence.
Fleet configurations additionally require the local SSH transport. Catalog
details expose missing dependencies without probing hosts or starting services.
Host reachability and remote runtime readiness remain unknown here:
FleetSpawner owns the existing dispatch-time probe and defers an all-asleep
fleet without reserving tasks. A sleeping host is not an invalid declaration.

Inputs are validated before publication. Duplicate names, changed templates,
stale edits, redirected files, ambiguous discovery and missing dependencies fail
explicitly. Cancel never executes a provider command or writes task-steering drafts.
The stored name and ID must match the exact edit target, including case;
Windows case-insensitive file lookup cannot rename an existing instance.

### Effects require confirmation

Both action families use `confirm: true` and show submitted values plus provider
effects prose:

- **Save only** writes a user-local draft. It does not register discovery,
  launch agents, create tasks, or change an already published version.
- **Save and Activate** saves the draft, publishes that exact version to
  registrar discovery and clears that instance's disable masks. The existing
  supervisor can start/restart workers, bind queued work, and, for loop emitters,
  create agentic tasks. No second supervisor is started.

The CLI independently requires `--activate --consent activate-local-worker`.
Publication returns **pending**, never a running claim. If publication fails,
the receipt separately reports `persisted: true`, `activation: failed`, and the
error; retrying the same submission uses the same instance and registration IDs.

## Storage, identity and receipts

Roots come from the active installation's `registrar_dir()` resolver, including
`AGENT_DISPATCH_REGISTRAR_DIR`; no operational machine or legacy home is baked in.

- `worker-instances/<name>.json`: atomic draft record, immutable source ID and
  template revision, current configuration revision, and last submission baseline.
- `worker-instances-active/<name>.json`: atomically published declaration snapshot.
- `pointers.json`: one exact `dir` pointer named `user-worker-instances`, owned by
  `user-local:workers`. A conflicting pointer is never silently retargeted.

Composed issue loops preserve their repository-root context in a local-only
document envelope; discovery expands it through the same registrar validation
authority. This envelope is refused outside the user-local active directory.
Pointer writers serialize read-modify-write operations, preserving other pointers.
Draft writes serialize separately and use content revisions as optimistic edit
baselines. A crash between save and publication can leave a saved draft, not an
implicitly activated half-configuration.
Declaration names remain reserved by both other instances' saved drafts and
their still-published snapshots; Save-only cannot release a published loop's
emitter/evaluator/worker names for an unrelated instance.

The supervisor still owns `declared:<owner>:<name>` reconciliation: identical
specifications are no-ops, changed specifications restart in place. The lifecycle
does not invent another runtime, direct task creation, or synchronous activation API.
Repository declarations, installed payloads, and built-in templates are never edited.
The catalog's Enable action clears the target's concrete and logical local
disable masks with `--require-declared --clear-all-masks`; unrelated masks stay
intact. Its receipt reports enabled with unknown running state, not active.

## CLI

Get the template or instance `id`, `revision`, and field defaults from
`workers catalog`, then submit the values:

```sh
agent-dispatch workers save template:worker-pool --revision <catalog-revision> \
  --field name=my-worker --field 'declaration={"labels":["work"],"concurrency":1}'
```

Add `--activate --consent activate-local-worker` only after reviewing the effects.
Edit with the returned `instance:my-worker` ID and current revision.
Commands return complete JSON persistence/activation receipts. No GUI is required.
