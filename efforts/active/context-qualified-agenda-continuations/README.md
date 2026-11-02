# Context-Qualified Agenda Continuations

- **Slug:** `context-qualified-agenda-continuations`
- **Repo:** copilot-extensions
- **Branch(es):** Retained managed source workspace; reviewed PRs to `dev`
- **Created:** 2026-11-01
- **Status:** Active
- **Vision:** `visions/plugins/efforts` - queue-and-stacked-continuations,
  context-qualified-agenda-revisits, autonomous-no-progress-recovery,
  meaningful-context-before-retry and continuity-and-resource-limits
- **Umbrella issue:** [#9897](https://github.com/ThomasMichon/copilot-extensions/issues/9897)
- **Related:** completed [agenda CLI source effort](../../2026/11/01%20agenda-cli-live-updates/README.md);
  existing broader Companion acceptance #8811

## Guiding Intent

An agenda should support an ordered queue of accepted work and a stack of saved
continuations. Decomposing an item creates a subordinate queue ahead of its
parent continuation; returning preserves the original links and carries new
context into the next attempt.

Repeated visits are useful when genuinely new relevant knowledge or a materially
different plan changes the approach. Guard against unchanged, unproductive
repetition rather than using raw visit counts to demand Operator steering.
Keep permission, resource and canonical completion boundaries independent.

## Participants

| Participant | Scope | Reached via |
|-------------|-------|-------------|
| Source coordinator | Vision, cohesive design, owned agenda lane, implementation integration and reviewed completion | Retained source workspace under an eligible external harness |
| Core evidence and implementation worker | Existing validator/store/operation contracts and bounded continuation changes | Coordinator-scoped child; no independent Git or PR authority |
| Delivery compatibility worker | Existing agency/consumer wire admission and connected validation | Coordinator-scoped child; no new service or rollout authority |
| Guidance and consumer worker | Agent procedure, attributable invocation and presentation compatibility | Coordinator-scoped child; no independent Git or PR authority |

## Coordination

- The coordinator owns the canonical effort, source workspace and PRs. Workers
  receive bounded, non-overlapping evidence/edit scopes; shared Git mutations
  remain coordinator-owned.
- Planning and the vision extension clear normal review before implementation.
  The completed predecessor effort remains archived; this is a renewed scope,
  not a retroactive completion claim or a replacement of its working source.
- Use a separate explicitly owned agenda lane to guide this effort through the
  existing source CLI. Never migrate another active agent, guess private tracking
  paths, update installed payloads or treat the lane as the effort completion gate.
- Existing #8811 UI/native/relay/manual/head acceptance and the installation,
  release and service owners retain their responsibilities.

## Context

The completed source in #9869 provides strict commands, serialized state/history,
stable operation identities, shared frame/visit evidence, legacy import fencing
and optional bounded held Manager delivery. Its current node admission still
enforces numerical visit limits. Queue/push/return are explicit but subordinate
work must first be assigned into the existing queue.

The renewed delta is semantic continuation: dynamic sub-queues, preserved sibling
order and parent positions, attributable context changes, and autonomous recovery
when an approach has gained no useful information. The existing durable store,
source fences, compatibility negotiation and cancellation/process controls are
foundations to extend, not mechanisms to replace.

## Request

The operator's design clarification:

> Note that if the agent hits the limit, I'm not sure I want it to ask the Operator for steer.
>
> My mental map here is that of a queue/stack, where you can plan and append to the end of the queue normally, but as you get to items you can start pushing to the head of the queue, like it's a stack (think ECMAScript arrays). The tree-like pattern emerges because an agent will reach a step, realize it needs more planning, make a "sub-queue", and push it onto the stack. If you keep the original links around, the added nodes at the head resemble a tree branch. I'm not sure it's a "bad thing" if you keep arriving back at the same node, but what should happen is that each time we find ourselves at the same node, we need to have new context we didn't have before, to allow the problem to be tackled in a different way.

The execution request:

> Great. Please help augment the vision and build out this design with a renewed effort; consider using the agenda framework to guide the next steps.

These requests contain no private source identifiers; their intent and uncertainty
are preserved rather than replaced with a preselected implementation.

### Implementation clarification - complete raw inputs

The coordinator asked:

> A real design choice remains before merge: should a previously unseen complete input configuration count as new context even when it combines individually known values?

The choices were:

- `whole-input`: "Use complete input configurations (recommended); retain strict knowledge/prose guards"
- `leaf-knowledge`: "Require newly learned leaf facts even for input configurations; revisit the representation/design"

The operator selected:

> whole-input

This accepts complete raw-input comparison, including previously unobserved
configurations of known values. It does not declare causal relevance proven or
relax outer metadata, knowledge/prose, history, review or action-authority gates.

### Implementation clarification - field contracts

The operator additionally requested:

> We want to be clear about which fields accept Markdown and which don't. And also which fields expect enum-type values and which allow arbitrary prose

Document field-level enum vocabularies, identifiers/references, prose and raw
JSON separately. Distinguish a validator accepting Markdown characters as literal
text from a consumer actually interpreting/rendering Markdown; this request is
contract clarity, not implicit authorization to introduce a new formatting mode.

## Scope Approvals

- **Allowed:** generic vision/effort updates, source implementation, bounded
  synthetic validation, a separate coordinator-owned agenda lane and normal
  reviewed publication.
- **Excluded:** production services, installer/runtime rollout, active-agent
  migration, another agent's state/claims, uncertain external-effect replay,
  physical cleanup and unrelated lifecycle or review-system repair.
- **Preserve:** original roots, assignments, pending decisions, parent links,
  accepted operations, immutable history, truthful attribution and independent
  resource/safety/permission limits. New context is not new authority.
- **Compatibility:** preserve legacy policy explicitly. New required semantics
  must not be silently accepted by an old reader/writer or mistaken for the
  predecessor's version-3 migration refusal marker.
- **Judgment:** schema acceptance can validate structure, prior-attempt links and
  supplied evidence changes, not prove that a reasoning claim is meaningful.
  Measured inputs and agent judgment remain qualified rather than conflated.
- **Raw-input clarification:** compare each declared input's complete JSON value,
  including nulls, empty values, member names and membership. A never-observed
  configuration may qualify even when its individual leaves were known.
  Previously observed complete inputs and omitted/restored outer facts remain
  non-progress; knowledge/prose and caller metadata keep their anti-churn guards.

## Plan

### Phase 1 - Reviewed vision and continuation contract

- [x] Review and merge the vision extension and this renewed effort before code.
- [x] Reconcile bounded core, wire and guidance evidence; select the minimal
  existing-state extension with explicit policy and mixed-version behavior.
- [x] Adopt a separate owned agenda lane through the current source CLI and
  record root, stack, next action, exits and continuation across transitions.

### Phase 2 - Queue and context-qualified admission

- [x] Support ordinary tail append and in-scope decomposition into subordinate
  queues, preserving sibling order and saved parent continuations.
- [x] Record stable work/attempt lineage and prior-context relationships.
  Productive revisits can exceed a legacy numerical threshold under the new
  explicit policy without erasing visit history.
- [x] Require a relevant context change and changed approach for a productive
  revisit; distinguish structurally supplied evidence from semantic judgment.
- [x] Detect unchanged/cosmetic repetition and provide a concrete autonomous
  investigation/refinement/waiting path, not default Operator steering.
- [x] Preserve atomic acceptance, idempotent original results, roots, evidence,
  pending/deferred work and independent explicit bounds on all movement paths.

### Phase 3 - Guidance and optional observation

- [x] Update concise ambient and on-demand guidance, examples and owned CLI
  invocation so agents use the intended queue/continuation policy.
- [x] Document field-level value/format contracts, including exact enum
  vocabularies, free-form prose, raw JSON and Markdown acceptance/rendering.
- [x] Keep old-policy lanes readable and correctly enforced; gate new required
  policy/state against old writers and independently installed consumers.
- [x] Extend the existing projection only where the new state requires it,
  preserving separate capability negotiation, bounded frames and existing
  generation/head/store, retry, cancellation and retirement fences.

### Phase 4 - Measured validation and reviewed completion

- [x] Run bounded targeted and real connected tests for the exact acceptance
  matrix; obtain independent critique on the cohesive implementation.
- [ ] Add required changefiles and drive every implementation PR through
  current-head review, required CI and actual merge.
- [ ] Resolve every Plan and Validation Plan item, close the source issue,
  archive this effort through reviewed publication and release owned bindings
  and worktree obligations. Record release/adoption limits without rollout claims.

## Validation Plan

- [x] Tail-appended work retains order; multiple/nested sub-queues execute before
  the saved parent and unwind without losing siblings, roots or links.
- [x] An actual CLI/store sequence revisits the same stable item beyond the old
  threshold with distinct relevant context and a changed approach; history
  retains every occurrence and no Operator-steering result is generated solely
  from the count.
- [x] Identical/cosmetic evidence, timestamps, sessions, definition revisions and
  recreated node IDs do not manufacture progress or reset a stalled lineage.
- [x] Rejected/no-progress movement leaves state/history unchanged or records an
  explicitly accepted non-executing recovery decision; it never looks complete.
- [x] Autonomous diagnosis/refinement and waiting/other authorized work preserve
  the blocked continuation; real permission/human decisions remain separate gates.
- [x] Return, defer, recheck, restore, fork and interrupted mutation preserve
  context baselines, evidence, original idempotent results and all explicit bounds.
- [x] Old-policy readers/writers retain their behavior; unsupported new required
  state fails visibly across manual/managed and independently installed peers.
- [x] A real synthetic held consumer observes committed continuation state
  without agenda-file polling or mutation replay; prior source/sequence/retirement
  and process/cancellation controls continue to pass.
- [x] The coordinator's adopted lane demonstrates current-next-action and
  preserved continuation at meaningful boundaries without an automatic turn driver.
- [x] Field-contract guidance agrees with the actual validators and consumers;
  literal Markdown characters are not misrepresented as interpreted formatting.
- [ ] Public artifacts remain generic, review/CI pass and source completion is
  distinguished from promotion, installation and active-agent adoption.

## Proposal

Prefer extension of existing semantic operations and frame/history primitives.
The bounded evidence pass recommends a separately versioned execution policy,
frame-local ordered item instances and immutable attempt/context lineage.
Legacy count-limited lanes retain their existing admission. New required cursor
and SQLite generations must fail closed in old writers; the migration refusal
marker is not an executable cursor format.

A structured context delta should reference the prior attempt, new evidence or
judgment and the resulting approach, rather than making a changed identifier or
digest stand in for useful knowledge. Deterministic validation can reject
duplicate/cosmetic records and broken lineage; it cannot prove semantic
relevance. Keep that limitation visible and require attributable reasoning.

The live contract currently admits only cursor versions 1/2 and exact snapshot
fields. A truthful new cursor therefore requires provider-first, explicitly
negotiated delivery support, even if its coarse display retains the existing
field shape. Old requests retain the old contract or receive an explicit
unsupported response; never relabel new state as legacy, tunnel active queues
through deferred fields or replay a mutation after delivery rejection.

These are design recommendations subject to review, not additional operator
grants, native qualification or claims of implementation.

## Journal

### 2026-11-01 - Waiting outcome recommendation correction

- Current-head review found that an outcome mapped to `wait` closed its attempt
  but retained the old execution command in `next_action`. The durable result
  already prevents another outcome, so recommending the old command is stale
  and could suggest an unsafe replay.
- The outcome branch now recommends non-executing recheck/context qualification.
  Actual CLI regressions cover blocked/replan outcome-to-`next`, retain the
  original attempt/result and idempotent acceptance, reject repeated outcomes
  without mutation, and require a qualified revisit for a new attempt.
- The coordinator's combined packet passed 316 cases with two explicit
  dependency/platform skips; the actual connected/literal-widget packet passed
  six cases without skips. Both verified cleanup, and Ruff passed. Fresh review,
  required CI, merge and canonical completion remain open.

### 2026-11-01 - Whole-input and field-contract validation

- Implemented the operator-selected complete-input policy without new schema
  fields: raw nulls, empty values, membership and payload `id` keys remain data;
  whole-value history still refuses known returns. Prose retains conservative
  knowledge guards and outer fact omission cannot manufacture novelty.
- Added the linked field contract distinguishing exact enum vocabularies,
  author-defined outcome names, references, prose, numbers/timestamps and raw
  JSON. No agenda data field promises Markdown rendering. CLI output is
  JSON/plain text and Companion appends provider data as literal `Text`, not
  Markdown or Rich markup.
- Coordinator regressions exercise actual CLI prose preservation, author-defined
  outcomes and rejection of prose/Markdown-decorated enum tokens. The combined
  core/fault/store/guidance/producer packet passed 314 cases with two explicit
  dependency/platform skips.
- The joined run initially caught its own obsolete fixture: it changed raw
  payload UUID/time fields while expecting metadata-only refusal. The corrected
  fixture keeps the complete payload unchanged and varies outer provenance,
  preserving every original no-attempt/history assertion. The actual connected
  chain and a real Textual literal-Markdown/Rich-markup case then passed all six
  selected cases without skips. Both runs verified cleanup; Ruff passed.
- Independent field-guide inspection confirms the literal rendering path and
  shared text validation. The guide links to the operation/schema owners rather
  than introducing a new formatting flag. Fresh review, required CI, merge and
  canonical completion remain open.

### 2026-11-01 - Complete raw-input semantics decision

- The latest review has no open findings, but its summary flagged structured
  deletions/nulls. Actual store assessment confirmed the concern: a raw null
  replacement and an `id`-only payload can be rejected as empty, while a
  meaningful member deletion can be refused as previously known facts.
- The coordinator recommends completing the lineage-anchored distinction:
  treat declared raw input as the whole observed JSON value, including nulls,
  empty values, member names and membership; retain normalized knowledge
  comparison for prose and outer facts. Returning to a known whole input remains
  non-progress, and omitting/restoring an outer fact cannot manufacture novelty.
- The operator explicitly selected `whole-input` after the clarification
  recorded in Request. A never-observed raw configuration combining known leaf
  values counts as changed input, not newly learned individual facts. Source
  implementation and regressions must now follow that selected distinction.
  Causal relevance remains attributable judgment, and this decision grants no
  additional action authority.
- The operator also requested explicit Markdown and enum/prose field contracts.
  The guidance owner is tracing validator and presentation behavior and will
  publish a linked field reference rather than relying on ambiguous `string`
  descriptions. The completed clarification and validation are recorded above.

### 2026-11-01 - Structured whitespace value follow-up

- The next review found a tightly coupled raw-input edge: the nonempty/knowledge
  helper still stripped whitespace-only string leaves after semantic
  normalization preserved them. A structured delimiter change from comma to
  space could therefore be refused despite changing the actual split result.
- The shared predicate now retains nonempty raw strings, including whitespace
  leaves. Regressions exercise the actual split result, scalar/nested whitespace,
  historical reappearance refusal and prose-blank rejection without partial
  acceptance. Cosmetic prose is normalized upstream; empty content remains
  invalid.
- The coordinator's combined packet passed 293 cases with two explicit
  dependency/platform skips; the actual isolated connected sequence passed five
  cases without skips. Both verified cleanup, and Ruff passed. Fresh review,
  required CI, merge and canonical completion remain open.

### 2026-11-01 - Significant input content correction

- Current-head review accepted numeric normalization but identified another
  coupled context-admission defect: recursively collapsing raw source or
  configuration whitespace can erase behavior-changing edits. Input strings now
  retain exact content recursively; prose retains cosmetic normalization.
  The subject's first lineage occurrence fixes that policy across later kind
  churn and omission, without adding schema fields.
- Reproductions demonstrate an indentation edit changing the executed result
  from 2 to 0 and a significant config-string edit. Both now admit a revisit;
  prose whitespace, kind churn and restored known content retain no-progress
  guards. The coordinator packet passed 283 cases with two explicit
  dependency/platform skips; the actual isolated connected sequence passed five
  cases without skips. Cleanup and Ruff passed. Fresh review, required CI, merge
  and canonical completion remain open.
- The already-corrected policy thread now has an explicit resolution backed by
  its commit, test locations and passing producer packet; no redundant source
  change or waived review finding is implied.

### 2026-11-01 - Context novelty review follow-up

- The next current-head review exposed two additional novelty loopholes:
  equivalent numeric representations could create new signatures, and dropping
  then restoring an already-known fact could appear novel when whole bundles
  differed. Both need same-lineage regressions and corrections before merge.
- Exact integer-ratio comparison now normalizes numeric equivalence in equality
  and historical signatures without rounding large integers or conflating
  booleans. Novelty checks retain scoped known leaves/structure across the full
  item lineage, including nested facts; dropping and re-adding known information
  cannot create another attempt or reset decomposition bounds.
- Reproductions first failed as incorrectly admitted revisits. Corrected
  regressions preserve genuine changed-input positives, prior attempts/history
  and explicit counters. The coordinator's combined packet passed 274 cases with
  two explicit dependency/platform skips; the actual isolated connected sequence
  passed five cases without skips. Both bounded runs verified cleanup.
  Fresh review, required CI, merge and canonical completion remain open.
- The policy assertion finding was carried over despite its actual correction
  and passing existing producer packet. An inline response anchors the fix to
  its commit and test locations; no redundant producer rewrite is warranted.

### 2026-11-01 - First implementation review corrections

- Review identified four coupled defects: stale ambient-policy assertions,
  provider imports exposed to the invoking checkout, boolean/number equality
  erasing meaningful context changes, and oversized observations dispatched as
  legacy records before their pending-delivery reason was handled.
- The policy tests now exercise the intended semantics and the existing
  1,024-byte bound, including valid manifest-version extremes. The bounded
  producer packet passed 65 cases with one explicit PowerShell-unavailable skip;
  that run does not claim live cross-producer parity.
- Both publication subprocesses now use isolated Python startup. The outer
  adapter explicitly binds its trusted payload directory; the selected provider
  uses its interpreter's own installed context, not checkout imports or ambient
  `PYTHONPATH`. Shadow-module regressions verify no checkout side effects.
- Context qualification and integrity checks now compare canonical JSON values,
  preserving boolean/number distinctions inside nested containers without
  crediting metadata churn. Oversized new-mode observations retain the original
  `observation-too-large` pending reason without invoking a provider or replaying
  their committed mutation.
- The bounded core/fault/store packet passed 185 cases with one isolated
  provider-dependency skip. The combined coordinator packet then passed 259 cases
  with two explicit provider-dependency/PowerShell skips, including the existing
  producer tests and worked guidance.
- The connected consumer was rerun against the isolated provider invocation:
  five joined cases passed without skips, and the complete Manager packet
  passed 176 cases without skips. The coordinator independently repeated the
  actual five-case joined sequence. Every bounded run verified cleanup.
  Fresh review, required CI, merge, canonical completion and archival remain open.

### 2026-11-01 - Cohesive source validation

- The coordinator independently ran the final source packets: 179 passed with
  one dependency-based skip for core/guidance/fault/store, 202 passed for provider
  admission/runtime/CLI, and 176 passed for Manager contracts, held consumers and
  the real end-to-end sequence. All three bounded runs verified cleanup.
- The skipped isolated efforts test needs provider dependencies. The Manager
  packet independently executed the actual provider parser, source CLI/store,
  negotiated publication, resident, held relay and Textual consumer with no
  parser substitute or skipped positive witness.
- Both independent critique findings now have regressions: deferred append
  refuses without changing original state/history, and v2 rejects legacy
  `steering` while v1 retains it. Active-ancestor append and append after explicit
  recovery remain supported. Frozen deferred custody permits unrelated assigned
  work without letting that work mutate the preserved chain.
- Core tests preserve legacy restore/fork history; new-mode cloning is explicitly
  unsupported. Other new-mode work uses an explicit assignment, not a copied
  lineage or a reset of context/budgets.
- The coordinator's legacy-policy lane recorded implementation, validation and
  an evidence-backed correction as semantic transitions without resetting visits
  or misrepresenting itself as a migrated new-mode lane.
- Ruff, public-source identifier review, installation/documentation consistency
  and enforced typechecking passed. The enforced checker covers only its
  admitted projects; it does not qualify every changed module. A shared
  supervisor-use lock timeout was retried after the competing test completed;
  no lock, process or service was removed.
- Required PR review, CI, merge, canonical completion/archive and ownership
  release remain open. No source result implies promotion or installed rollout.

### 2026-11-01 - Integration corrections and revised validation

- Independent critique found two additional admission mismatches: appending
  into a deferred frame without recovery, and admitting the legacy `steering`
  status in a cursor-4 observation. Scoped corrections and focused regressions
  are required before the new mode clears integration. Active-ancestor tail
  append and legacy status behavior remain supported.
- Child acceptance alone intentionally does not reset no-parent-progress
  decomposition bounds. A qualified parent revisit must supply relevant new
  context; the revised regression and guidance make that distinction explicit.
- The first core/legacy packet passed 215 cases, and the delivery compatibility
  packets passed 195 provider and 153 consumer cases. These results cover their
  captured inputs, not the subsequent integration corrections.
- The real joined witness exposed a parser-only publication probe incorrectly
  receiving a global project option. The selected parser probe now excludes that
  global option while the actual control payload retains explicit project
  selection. Committed mutation state survived the delivery rejection without
  replay. The positive joined sequence must pass after this correction.
- Integration inspection required truthful deferred roots, decision/revisit
  fields, ancestor tails and saved wait/return actions. It also required alias
  lineage guards and structural record crosslinks. Those source corrections
  have entered the revised validation packet; their presence alone is not
  passing evidence.
- The explicit `assign-root` operation permits other accepted work while a
  complete original chain remains deferred. It preserves that chain and its
  attempts, rather than cloning it. New-mode cloning/forking is unsupported;
  the legacy fork operation retains its existing policy and validation gate.
- A guidance run captured a generated-state validator mismatch during those
  edits. The validator now admits its own generated next-action field; the
  request fixtures were not weakened. Unreached new-mode assertions remain
  uncredited until the revised packet passes.
- Repository installation and documentation consistency checks passed. No
  installer, installed payload, active lane or production service was changed.

### 2026-11-01 - Core contract synchronized

- Accepted an explicit fresh-lane contract: request 2, `context-qualified`
  execution policy, board 2, executable cursor 4 and SQLite generation 2.
  Legacy lanes retain their existing policy; cursor 3 remains a non-executing
  refusal marker. No active-lane migration or installed update is authorized.
- Corrected the projection inventory against the source: the coarse Snapshot
  contains 15 fields, not 17. Preserve that exact shape, report cursor 4
  truthfully and negotiate its delivery separately from the legacy contract.
- Frame-local ordered queues and immutable item, attempt, context and return
  links form the selected core extension. Relevant changed inputs may make the
  same command viable; changed evidence references or causal prose alone do
  not manufacture novelty. Semantic relevance remains attributable judgment,
  not a property proven by schema acceptance.
- Independent depth, pending/deferred state and no-parent-progress decomposition
  bounds remain separate from lifetime visit history. Unchanged revisits record
  a non-executing replanning decision and preserve the continuation for bounded
  investigation, refinement, waiting or other already-authorized work.
- Core implementation, independently negotiated observation and worked guidance
  are in progress under separate scopes. Concrete source-CLI examples, connected
  validation, review and completion remain open.

### 2026-11-01 - Reviewed implementation admission

- Vision and plan [#9898](https://github.com/ThomasMichon/copilot-extensions/pull/9898)
  received current-head Copilot approval with no findings, passed required CI and
  merged as `deb4c22fe9c4dfc722533529a2fb8c2a9c1b8050`.
- The same source workspace pulled forward onto that merge. The coordinator
  reread this complete contract and retained its scope, semantic-judgment limits,
  separate runtime authorization and all remaining validation/completion gates.
- The owned planning lane can now follow its declared implementation transition
  using actual review/merge evidence, rather than a cursor edit or inferred
  approval. Core, compatibility and guidance execution remain bounded separate
  scopes under one coordinator-owned publication flow.

### 2026-11-01 - Renewed source intake

- Retained the completed predecessor and reused its source workspace after
  resolving current source and explicit effort adoption. Claimed #9897.
- Extended the canonical vision with queue/stack continuity, productive revisits,
  autonomous no-progress recovery and separate resource/authority boundaries.
- Started three non-overlapping read-only evidence tracks for core state,
  delivery compatibility and guidance. Implementation remains behind review.
- Core evidence confirms FIFO root assignments and LIFO child execution already
  exist, but frame-local pending queues and context-qualified attempts do not.
  Stable frame/visit histories and transactional acceptance can be extended.
- Delivery evidence confirms both receivers enforce exact versioned snapshots;
  a new execution cursor cannot be silently passed through the v1 projection.
  Existing bounded held-stream owners and connected witnesses remain the seam.
- Guidance evidence confirms that existing wording invites steering on numerical
  exhaustion but does not explicitly require an Operator question. Update
  examples, semantic admission and guidance together; do not claim prose alone
  removes the hard guard. The initial coordinator lane uses the already-merged
  source CLI and its honest legacy policy while renewed execution is unreviewed.
- The coordinator initialized a separate private worktree-local lane through the
  existing transactional CLI. Its verified first snapshot holds the reviewed-plan
  gate; implementation is its declared next node only after actual review/merge
  and rereading the scope. Delivery is explicitly not configured. No active lane
  was imported, migrated or published.
- VEI transport was closed; direct current-source and public-tracker discovery
  supplied the fallback. No service restart, reindex or infrastructure repair
  was attempted.
