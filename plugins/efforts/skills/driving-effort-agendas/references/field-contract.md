# Agenda fields: types, enums, prose and Markdown

[Driving Effort Agendas](../SKILL.md) owns the procedure.
[Context-qualified commands](context-qualified-commands.md) owns operation shapes
and worked examples; [managed commands](managed-agenda.md) owns legacy/durability
rules. This reference describes field values, not new flags or execution authority.

## What “accepts Markdown” means here

**No agenda data field currently has a Markdown-rendering contract.** The source
validators do not generally reject Markdown punctuation in text. Acceptance of
`**bold**`, backticks, a heading, or `[label](target)` as string bytes is not
Markdown parsing, rendering, link resolution, or command execution.

| Surface | Actual treatment of agenda data |
| --- | --- |
| Managed `agenda_cli.py` output | JSON via `json.dumps`; strings remain strings, with JSON escaping. No Markdown parser. |
| Legacy `agenda.py next/check` without `--json` | Plain printed labels/text; with `--json`, JSON. No Markdown parser. |
| Manager Companion Agenda tab | Builds `rich.text.Text`, appending string values literally and non-string values through `json.dumps`, then places that Text in a Textual Static. Provider data is not parsed as Markdown or Rich markup. Manager-owned headings/styles are separate. |
| This documentation and its code fences | Markdown documentation; that formatting does not declare agenda fields to be Markdown. |

Use **plain prose** for descriptions/actions and **plain exact strings** for
identifiers, references, enum tokens and timestamps. Markdown-looking prose can
be stored where the text rules allow it, but will normally appear literally.
In an identifier/reference it can also change matching semantics. Do not wrap an
enum in backticks or replace a stored source reference with a formatted link.
There is no `format`, Markdown flag, automatic field-type inference, or universal
“strip formatting” normalization.

## Conventions and common limits

- **Text** below means a JSON string containing non-whitespace content and no
  NUL. The shared `_text` check returns the original string, not a trimmed copy.
  Newlines and Markdown punctuation are not a general validation error.
- **ID/reference** means text used as an exact identity, lookup or attribution,
  not free prose. Use a simple stable token or canonical reference; matching and
  source-lineage checks still apply. Most standalone reference fields do not
  validate a URL, fetch its target, or prove its existence/authority.
- **Enum** means an exact, case-sensitive token from the listed vocabulary.
  No whitespace trimming, aliases, Markdown decoration or case conversion is
  implied. JSON booleans are not integer versions, revisions or caps.
- Request JSON has exact required/optional object keys and rejects unknown keys,
  duplicate keys, nonfinite numbers and invalid JSON. It is not a Markdown
  document: do not submit surrounding code fences or commentary.
- Requests/boards are bounded to 128 KiB and combined managed documents to
  1 MiB. Individual field checks and generated-record/result bounds also apply.
  Optional delivery is stricter: a committed observation is bounded to 32 KiB,
  with provider wire string/container/depth limits independent of core acceptance.
  A valid durable operation is not a promise that an arbitrary large paragraph
  can be delivered or displayed.

## Common request and caller fields

| Field | Type/value contract | Formatting guidance |
| --- | --- | --- |
| `schema` | Exact literal `efforts.agenda-request`. | Enum-like literal, not prose. |
| `version` | New mode: integer `2`; legacy requests: integer `1`. | Number, not `"2"` or `true`. |
| `mode` | Request 2: exact `context-qualified`. CLI init selector: `--mode context-qualified`; CLI also knows `legacy` for the separate legacy path. | Enum; request 1 has no new-mode `mode` field. |
| `expected_revision` | Nonnegative integer through signed-63-bit maximum; fresh init requires `0`, update must match durable current revision. | Use the actual read value, never prose. |
| `operation_id` | Nonempty stable text, at most 1 KiB in new mode. Same accepted ID requires the identical request/caller assertions. | Exact opaque retry identity; formatting changes are not a retry. |
| `change` | Exact operation-specific object. | Structured JSON, not a prose instruction. |
| `change.operation` | New-mode enum: `init`, `assign-root`, `append`, `outcome`, `expand`, `return`, `revisit`, `refine`, `wait`, `defer`, `recover`. | Exact lowercase/hyphen spelling. |
| `change.worktree` at init | Nonempty lane identity. Optional delivery additionally qualifies its provider worktree/head identity. | Plain exact ID; keep private lane identity out of public artifacts. |
| CLI `--actor` / `--session` | Optional nonempty caller-asserted text, at most 1 KiB each in new mode; omitted assertions are recorded as unknown/null. | Labels, not authentication, permission or timestamps. |
| State/request/import paths | Explicit filesystem paths with the owning CLI's safety checks. New mode requires fresh owned state, not import/migration. | Real paths, not Markdown links or shell expressions embedded in data. |

## Board 2 and item definitions

| Field | Type/value contract | Prose versus exact value |
| --- | --- | --- |
| `board.version` | Integer `2`; source board is a structured object, not a filename string. | Fixed version. |
| `source.kind` | Enum `effort`, `task`, `aside`. | Exact token. |
| `source.ref` | Nonempty canonical assignment reference. | Literal attribution, not fetched or rendered Markdown. |
| `board.revision` | Nonnegative integer definition revision, bounded by the signed-63-bit storage contract. | Definition identity, not a date or README hash. |
| `items` | Nonempty object mapping definition IDs to definition objects. | Keys are stable nonempty text identifiers, not labels. |
| `order` | Nonempty ordered list of unique existing definition IDs. | Exact IDs; order is significant. |
| `source_ref`, `scope_ref` | Nonempty canonical criterion/scope references. | Literal attribution; preserve identity and scope across refinement. |
| `action` | Nonempty description of the work/approach. | Arbitrary prose subject to text/bounds; not an executed command. Markdown characters remain literal. |
| Optional `label` | Nonempty display label. | Prose/display text, not an enum or identity replacement. |
| `exit` | Nonempty list of unique nonempty criterion strings. | Prose, but outcome evidence must cover the stored exact criteria. Adding formatting can change that match. |
| `on` keys | Nonempty author-defined outcome names. | Not a fixed vocabulary such as mandatory `done`; select an actual declared key. |
| `on` values | Enum `advance`, `wait`, `replan`. | Unlike legacy boards, these are transition classes, not destination node IDs. |
| `relevant_subjects` | Nonempty list of unique nonempty subject names. | Exact scope/matching keys, not causal prose. |
| Optional `source_aliases` | List of nonempty source aliases. | Literal lineage references; not a means to recreate an old item with a new ID. |

Operation `items` is a different shape from `board.items`: a nonempty ordered
array of objects each containing exactly `{definition_ref}`. `definition_ref`
must name an actual board definition; no inline prose item or automatic lookup.

## Context facts, judgment and approach

`baseline_context` and revisit `context` contain exactly `facts`, a nonempty
array. Each fact contains exactly the four fields below:

| Field | Type/value contract | Interpretation |
| --- | --- | --- |
| `kind` | Enum `input`, `condition`, `constraint`, `prerequisite-result`, `observation`, `insight`. | Classification, not approval or a content renderer. |
| `subject` | Nonempty text in the definition's applicable `relevant_subjects`; once per context. | Exact scoped key. |
| `value` | Finite JSON: object, array, string, number, boolean, or null, subject to the comparison policy below. | Structured value, not required to be prose or an object wrapper. |
| `evidence_ref` | Nonempty attribution/reference text. | Outer provenance; changing it alone does not supply new facts. |

### Raw input versus knowledge/prose

For a subject first established as `input`, **the whole value is raw observed
input/configuration**. Member names such as `id`, nested members, array order,
membership, nulls, empty objects/arrays/strings and whitespace inside strings
remain data. For example, `{"id":null,"members":[]}` is a possible complete input,
not metadata to strip. A raw string containing Markdown is still an exact raw
string; it is not parsed or normalized as Markdown.

The entire configuration is compared against that subject's retained input
history, not only against the immediately preceding bundle. Previously unseen
membership/configuration can change an input even when individual values were
known. Reintroducing an already-observed complete configuration cannot.
Object key order is not meaningful; numeric equivalents such as `1`/`1.0` and
`0.0`/`-0.0` agree, but boolean `true` is distinct from numeric `1`.

For a subject first established as a non-input kind, knowledge/prose comparison
normalizes cosmetic Unicode/whitespace and ignores caller/generated metadata
keys for semantic novelty. A normalized-empty/metadata-only value is refused.
Nonempty facts learned previously remain known across outer omissions; removal,
reference churn, or a bundle of already-known facts is not new knowledge.
This is not a Markdown parser: punctuation is not automatically stripped merely
because it resembles formatting.

The input/knowledge policy is fixed by the subject's first lineage occurrence.
Changing `kind` later or dropping/readding the outer subject cannot switch the
comparison policy. Do not place caller metadata inside raw `input.value` and
expect it to be ignored: that location denotes observed payload, not provenance.
Structural comparison still does not establish semantic relevance or permission.

| Revisit field | Type/value contract | Formatting |
| --- | --- | --- |
| `judgment.qualification` | Exact `agent-judgment`. | Required qualification, not free prose. |
| `judgment.changed_subjects` | Unique subject-name array, within scope, exactly covering semantically added/changed relevant subjects. | Exact subject names; not Markdown descriptions. |
| `judgment.causal_change` | Nonempty explanation of why the changed context supports a viable new attempt. | Arbitrary attributed prose; literal presentation, not causal truth proved by parsing. |
| `judgment.evidence_refs` | Nonempty list of nonempty reference strings. | Literal supporting references; not automatically dereferenced or verified. |
| `approach.action` | Nonempty action string; `approach` has no other fields. | Prose/literal command description. Same command can be viable with genuinely changed inputs. |

## Evidence and remaining operation fields

Every evidence entry contains exactly `criterion`, `kind`, `ref`, `inputs`.

| Field | Type/value contract | Formatting |
| --- | --- | --- |
| Evidence `kind` | Enum `measured`, `judgment`. | Distinct from context fact `kind`; do not interchange their vocabularies. |
| Evidence `criterion` | Nonempty criterion text; outcome entries cover every current `exit` criterion. | Prose with exact-match obligations, not automatically Markdown-normalized. |
| Evidence `ref` | Nonempty result/source reference. | Plain literal reference, not fetched/rendered. |
| Evidence `inputs` | Nonempty text naming covered inputs/revision. | A description/reference, **not** arbitrary raw JSON. Raw payload belongs in context fact `value`. |
| `frame_id`, `item_id`, `attempt_id`, `predecessor_attempt_id`, `expansion_id`, `deferred_id` | Nonempty opaque identities with live/immutable crosslink checks. | Copy actual IDs from rich reads/results; not user-created prose labels. |
| `assignment_ref`, `acceptance_ref`, `decision_ref`, `decision_packet_ref`, `reason_ref`, `recheck_ref` | Nonempty attribution/decision references appropriate to that operation. | Literal references; a reference is not approval, actuality or acceptance by itself. |
| `outcome` | Must match a declared `on` key for the current attempt's definition. | Author-defined token, not arbitrary success prose. |
| `continuation.parent_item_id` | Must equal the current parent item. | Exact ID. |
| `continuation.next_action`, wait `next_action` | Nonempty concrete return/recheck action. | Prose, stored and projected literally; never automatically executed. |
| Wait `reason_kind` | Enum `permission`, `human-decision`, `external`, `bounded-replan`. | Distinguishes actual human/permission gates from autonomous replanning. |
| `revisit_at` | Timezone-aware ISO timestamp later than the actual deferral attempt. Non-UTC offsets are accepted/preserved. | Timestamp, not prose; not a scheduled wake or authority renewal. |
| Recover `attempt_policy` | Exact `resume-only`. | No fresh attempt, effect replay or implicit approval. |
| Refine `board_ref` | Exact `root`. | Board key, not an arbitrary path or Markdown reference. |
| Refine `expected_definition_revision` | Nonnegative integer matching current board revision. | No string/boolean substitution. |
| Refine `patches` | Nonempty array; each patch has `definition_id` plus explicit supported changes. | Structured JSON, not a free-form patch or diff string. |

A refine patch permits `action`, `label`, `exit_append`,
`relevant_subjects_append`, `source_aliases_append`, or `add_definition`.
The append fields are nonempty text arrays with their corresponding definition
semantics; `add_definition` supplies a complete valid definition and cannot
replace an original one. There is no arbitrary mutation path or free enum field.

## Caps, generated state and the coarse output

`resource_caps` has exactly four positive integer fields:
`max_depth`, `max_pending_items`, `max_deferred_frames`, `max_no_parent_progress`.
They are independent limits, not prose, revisit scores or Operator decisions.
The storage contract bounds them by signed-63-bit maximum. Request identifiers
and counters are not formats from which permission is inferred.

Rich `read` includes structured `documents`/`records` and opaque generated IDs.
Do not edit generated records as if they were request templates:

| Output | Known type/vocabulary |
| --- | --- |
| Cursor 4 `status` | `working`, `waiting`, `replanning`, `complete`. |
| Item state `status` | `pending`, `running`, `replanning`, `waiting`, `completed`. Note `completed` is not cursor `complete`. |
| Record `schema` / `version` | `efforts.agenda-record` / integer `2`; the `body` shape depends on its record kind. |
| Record `kind` | `context`, `frame`, `item`, `attempt`, `result`, `expansion`, `return`, `assignment`, `decision`, `context-proposal`, `wait`, `deferred`, `recovery`, `refinement`. |
| Actor qualification | `caller-asserted` or `unknown`; missing actor/session can be null. |
| Delivery `state` | `not-configured`, `pending`, `delivered`; successful persistence is separate from delivery. |
| Error/replanning `reason_code` | Machine-readable generated code, not prose or an editable enum selector. Preserve the actual returned code/message; do not infer a productive attempt from `persisted: true`. |
| `committed_at`, record creation time | Generated UTC ISO timestamps; distinct from caller-supplied revisit timestamps. |

The **exact 15-field Snapshot** is a coarse derived view, not a replacement cursor:

| Field(s) | Type and formatting |
| --- | --- |
| `worktree` | Literal lane ID. |
| `revision` | Nonnegative managed integer revision. |
| `status` | Cursor-4 enum above; legacy has the separate vocabulary below. |
| `root` | Derived source label as literal text, including a preserved deferred root when applicable. |
| `stack` | Array of derived literal labels, not nested Markdown or full frame state. |
| `next_action` | Literal current action/replanning/wait/return guidance, not an executable instruction to the renderer. |
| `exit_criteria` | Text array. |
| `declared_outcomes` | Text map of author-defined outcome keys to transition classes for new mode; not proof those outcomes happened. |
| `queued` | Text array of projected pending tails; consult rich state for exact per-frame positions. |
| `parked` | Nonnegative integer count, not a prose list or a 128-item scalar ceiling. |
| `notes` | Text array, literal in the Agenda panel. |
| `cursor_version` | New mode `4`; legacy `1`/`2`. Version `3` remains a refusal marker, not a Snapshot execution mode. |
| `deferred` | Array of structured branch summaries, not free-form narrative. |
| `due_rechecks` | Branch-ID array. |
| `next_revisit_at` | Timezone-aware timestamp text or null; no scheduler implied. |

A deferred summary's `resume_action`, `reason`, and `recheck_action` are literal
text; `id` and decision/packet references are exact references; `stack` is a text
array; attempt/revisit times are timestamp strings; revision/budget/count fields
are integers; `due` is boolean. Cursor-4 projection currently emits `trigger:
"time"` for due branches, otherwise null. Legacy inspection may also emit
`"response"`. These output summaries are not accepted as replacement request data.

## Legacy v1/v2 is a separate field contract

Request 1 uses its existing exact envelope without request-2 `mode`. Its semantic
operations are `init`, `refine`, `queue`, `select`, `outcome`, `link`, `block`,
`wait`, `push`, `return`, `complete-root`, `defer`, `response`, `recheck`,
`restore`, `progress`, `limits`, `park`; see [managed operations](managed-agenda.md#semantic-updates).
Do not submit new-mode operation names to a legacy lane.

Legacy board 1 uses `nodes`/`entry`, positive finite `max_visits`, declared outcome
keys mapping to existing **node IDs**, and optional boolean `terminal`. Cursor
1/2 status is `working`, `waiting`, `steering`, or `complete`, not new-mode
`replanning`. The same plain-text/opaque-reference distinction applies; numeric
visits/resource checks stay strict. Legacy managed `link.executions[].kind` and
`ref` are both nonempty text, **not** a closed kind enum. A suggested label such
as `session` or `task` does not restrict accepted values or invoke that provider.

## Source of truth and scope

This guide follows the current source, not a claim about older installed copies:

- Shared text/time/JSON checks and Snapshot: [`agenda.py`](../scripts/agenda.py).
- Request/caller/revision checks: [`agenda_store_v2.py`](../scripts/agenda_store_v2.py).
- Definitions/state enums: [`agenda_v2_schema.py`](../scripts/agenda_v2_schema.py).
- Context raw-input/knowledge comparison: [`agenda_context.py`](../scripts/agenda_context.py).
- Operation fields and enums: [`agenda_v2_operations.py`](../scripts/agenda_v2_operations.py).
- Evidence and legacy operations: [`agenda_operations.py`](../scripts/agenda_operations.py).
- CLI serialization: [`agenda_cli.py`](../scripts/agenda_cli.py).
- Generated projection: [`agenda_v2_projection.py`](../scripts/agenda_v2_projection.py).

Manager rendering is independently owned in
`worktree-manager/src/worktree_manager/companion_agenda.py` (`agenda_text`).
Neither text formatting nor JSON acceptance proves causal relevance, current
authority, external completion, source freshness or actual live delivery.
