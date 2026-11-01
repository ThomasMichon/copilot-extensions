# Context-qualified source CLI and worked continuations

[Driving Effort Agendas](../SKILL.md) owns the action loop.
[Context qualification](context-qualified-continuations.md) explains relevance
and authority; the [managed command guide](managed-agenda.md) covers durability,
legacy compatibility and optional delivery.

## Explicit fresh adoption

Use the resolved current efforts source/payload script and an existing Python
interpreter. Initialize a fresh, explicitly owned home:

```text
python <resolved-efforts>/skills/driving-effort-agendas/scripts/agenda_cli.py init --mode context-qualified --state-home <fresh-owned-home> --request <init-request.json> --session <explicit-caller-session>
python <resolved-efforts>/skills/driving-effort-agendas/scripts/agenda_cli.py read --state-home <owned-home>
python <resolved-efforts>/skills/driving-effort-agendas/scripts/agenda_cli.py update --state-home <owned-home> --request <next-request.json> --session <explicit-caller-session>
python <resolved-efforts>/skills/driving-effort-agendas/scripts/agenda_cli.py history --state-home <owned-home>
```

The source CLI implements explicit `context-qualified` mode with cursor 4,
store generation 2 and request 2. Existing installed payloads may predate it;
do not infer their support from this guide or relabel old state. Cursor 3 is
still a refusal marker. New-mode init does not import or migrate an active lane.
Actor/session assertions are not authentication or approval.

The complete [initialization fixture](../assets/init.context-qualified.example.json)
has the exact request envelope: `schema`, `version: 2`, `mode:
"context-qualified"`, `expected_revision`, `operation_id`, and `change`.
Init's change contains exactly `operation`, `worktree`, `board`,
`baseline_context`, and `resource_caps`. All four caps are explicit positive
integers: `max_depth`, `max_pending_items`, `max_deferred_frames`,
`max_no_parent_progress`.

Board 2 contains exactly `version`, `source`, `revision`, `items`, and `order`.
`items` maps stable definition names to `source_ref`, `scope_ref`, `action`,
`exit`, `on`, and `relevant_subjects`, with optional `label`/`source_aliases`.
`order` is the ordered initial root queue. An operation's `items` is instead an
ordered list of `{definition_ref}` entries. Outcome targets are only `advance`,
`wait`, or `replan`; they are not executable commands.

The fixture uses public-safe synthetic identities and evidence. Adapt them to
actual accepted work before adoption; keep the real lane identity private.
Data acceptance never proves the described external work or its approval.

## Discover identities; never invent them

`read` returns `store_id`, `revision`, `committed_at`, `documents`, `metadata`,
`records`, `snapshot`, and `delivery`. The Snapshot has exactly 15 fields; its
cursor version/status do not replace the rich continuation or record data.

For a nonempty active queue, obtain the current position from the actual read:

```python
cursor = state["documents"]["cursor"]
frame_id = cursor["frames"][-1]
item_id = cursor["frame_state"][frame_id]["pending"][0]
attempt_id = cursor["item_state"][item_id]["current_attempt_id"]
```

An empty child queue means inspect its saved `parent_link` and acceptance before
return, not manufacture another item/attempt. Immutable records are indexed by
ID in `records`; `history` preserves original operation results and the complete
records they created.

Use the currently read revision for each new update. Preserve the operation ID,
request and caller assertions for an uncertain-acceptance retry. Init's accepted
`result.transition` supplies `frame_id`, `item_ids`, `item_id`, `attempt_id`,
`context_id`; later transitions supply new expansion/attempt/context/deferred
IDs. Read their complete bodies before the next semantic decision.

## Operation fields

Every update uses the same exact request-2 envelope. `change` requires these
fields besides `operation`:

| Operation | Required fields |
| --- | --- |
| `assign-root` | ordered `items`, `assignment_ref`, scoped `baseline_context`, `evidence` |
| `append` | `frame_id`, ordered `items`, `assignment_ref`, `evidence` |
| `outcome` | `frame_id`, `item_id`, `attempt_id`, `outcome`, `evidence` |
| `expand` | `frame_id`, `item_id`, `attempt_id`, ordered `items`, `continuation`, `evidence` |
| `return` | `frame_id`, `expansion_id`, `acceptance_ref`, `evidence` |
| `revisit` | `frame_id`, `item_id`, `predecessor_attempt_id`, `context`, `judgment`, `approach` |
| `refine` | `board_ref`, `expected_definition_revision`, `patches`, `evidence` |
| `wait` | `frame_id`, `item_id`, `reason_kind`, `reason_ref`, `next_action`, `evidence` |
| `defer` | `frame_id`, `decision_ref`, `decision_packet_ref`, `revisit_at`, `evidence` |
| `recover` | `deferred_id`, `recheck_ref`, `acceptance_ref`, `attempt_policy`, `evidence` |

Evidence entries contain exactly nonempty `criterion`, `kind`, `ref`, `inputs`;
`kind` is `measured` or `judgment`. Outcome evidence covers every current exit
criterion. References and supplied judgments are not automatically true.
`continuation` is exactly `{parent_item_id, next_action}`. Return names the
original expansion and actual child acceptance. It restores the parent attempt,
not a new attempt or permission to replay its effect.

`context` contains exactly `facts`; each fact is `{kind, subject, value,
evidence_ref}`. Kinds are `input`, `condition`, `constraint`,
`prerequisite-result`, `observation`, `insight`; each subject belongs to the
current definition's `relevant_subjects` and appears once. Values are finite,
substantive JSON, not metadata-only records. `judgment` contains exactly
`qualification: "agent-judgment"`, `changed_subjects`, `causal_change`,
`evidence_refs`. Explain exactly the added/changed relevant subjects and retain
the actual evidence references. `approach` is exactly `{action}`; an unchanged
command is viable when relevant inputs genuinely changed. Typed facts or a
novelty comparison are not an affidavit of semantic truth or fresh permission.

`wait.reason_kind` is `permission`, `human-decision`, `external`, or
`bounded-replan`; only actual decision/permission/safety conditions involve the
Operator. `recover.attempt_policy` is `resume-only`; recovery requires an empty
active stack and requalification, preserving the existing attempt and history.
When the active stack is empty, `assign-root` activates a genuinely different,
already-authorized root while saved deferred chains remain intact. Its context
contains full scoped facts, and its accepted transition supplies the new root's
frame/item/attempt/context IDs. It is not cloning, forking, reopening or migration:
an original definition or semantic alias cannot be reused under a new root.
Legacy v1 fork behavior remains separate; there is no new-mode clone/fork path.
`refine.board_ref` is `root`; use its current definition revision. Patches name
`definition_id` and may change `action`/`label`, append exits/relevant subjects/
source aliases, or add a new definition with `add_definition`. Refinement does
not recreate an existing semantic item or erase its origin.

## Worked queue, nested planning and revisit

The initialization fixture starts `parent, sibling-a`. Its remaining definitions
are available for already-authorized append or necessary decomposition:

1. Append `sibling-b` to the actual root frame. The pending order remains
   `parent, sibling-a, sibling-b`; appending does not replace the head.
2. Record the parent's truthful unsuccessful `needs-context` outcome. Expand
   its current attempt into ordered `inspect-input, compare-results`, preserving
   `{parent_item_id, next_action}` before focus changes.
3. If inspection itself needs planning, expand its current attempt into
   `probe-fixture`. Record that synthetic queue's outcomes and return using its
   actual `expansion_id` and acceptance. Inspection's original attempt resumes.
4. Finish the two child items in order, then return their original outer
   expansion. The parent, both siblings, source links and original parent
   attempt remain. Because that attempt already has an unsuccessful result,
   the parent is replanning; child acceptance did not create a new attempt.
5. Submit the [productive proposal](../assets/revisit.context-qualified.example.json)
   inside `revisit`, using the actual predecessor ID. The input content changes
   from A to B while the validation command stays the same. The qualified
   explanation describes why the corrected input changes what that command can
   establish. Inspect the returned attempt/context records and their predecessor
   links. This is structurally admitted agent judgment, not programmatic proof
   of causal relevance or external success.

The [metadata-only contrast](../assets/revisit.metadata-only.example.json) keeps
input A and changes UUID/time/session labels, evidence reference and causal
wording. It cannot manufacture new context. The CLI can return a persisted
`not-admitted` decision with `reason_code`, null `attempt_id`, and a concrete
replanning action. That is accepted decision history, not an accepted attempt or
successful work. Remain at the continuation and investigate/refine/wait or choose
other authorized work. Independent resource bounds can likewise record a
replanning decision without spawning a child.

## Work while preserving a deferred root

The source CLI also supports this explicit continuation-preserving sequence:

1. Record a `wait` with the actual head IDs, condition/decision reference,
   persistent concrete `next_action` and evidence. Do not replay its command.
2. `defer` the actual top frame with the prepared decision packet and a qualified
   revisit time. Rich state retains the full saved stack, items and attempts.
   The same 15-field Snapshot preserves the deferred root, decision/recheck
   information, next revisit and pending tails; it is not an empty successful
   lane. Tails from active ancestors and deferred frames remain visible.
   A projected deferred branch names its saved action `resume_action`; the
   active Snapshot's action is `next_action`. Keep those fields distinct.
3. With no active stack, `assign-root` an unused, already-assigned definition
   such as the fixture's `sibling-b`, supplying its actual assignment/evidence
   and full relevant baseline facts. Never re-record the deferred parent or an
   alias. The original records and saved continuation remain unchanged.
4. Finish that separate accepted queue. Its completion cannot close the
   unresolved deferred root.
5. Requalify the original condition and authority, then `recover` the saved
   deferred ID with `resume-only`, actual recheck/acceptance references and
   evidence. The original parent item/attempt returns; persistent wait/return
   actions remain truthful. Recovery is not a new attempt or approval.

## Delivery and validation boundary

Standalone adoption needs no publisher. Optional new-mode publication uses
`--publisher-python <absolute-owned-provider-interpreter>` and
`--publisher-project <project>` together on init/update/publish. Never mix them
with legacy `--publish-arg`. Provider declaration/health/parser compatibility
and actual subscribed presentation remain the delivery owners' responsibility.
Retry only the committed body through `publish`, not the mutation.

The dedicated `tests/test_agenda_guidance_context.py` source-CLI packet owns
these worked fixtures, nested return/order checks, productive revisits beyond
the old threshold, no-context decisions, independent depth limits, and legacy
asset compatibility, including separate assigned work during deferral and
populated coarse projection. Connected provider/Manager and native rollout qualification
are separate. No global binstub, new poller or automatic active-agent migration
is implied by this guide.
