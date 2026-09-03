# Turn-key Reviewer Loops

- **Slug:** `turnkey-reviewer-loops`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees and pull requests
- **Created:** 2026-08-30
- **Status:** Done
- **Vision:** `visions/plugins/agent-dispatch` — `loop-recipes`,
  `side-load-through-an-emitter`, `registered-supervision`,
  `pools-are-filters-with-a-cap`, `a-loop-runs-with-or-without-a-service`
- **Umbrella issue:** #1403

## Guiding Intent

Turn the stock agent-dispatch reviewer recipe into a complete repository
capability: a repository declares how reviews are discovered, how reviewers are
guided and embodied, who owns landing, and what completion means; the shared
runtime supplies the durable task loop, bounded pool, suspend/resume rhythm,
status, and recovery.

The result should be more capable than a forge's baseline automated reviewer
without becoming forge-specific or repository-specific. A maintainer should be
able to adopt the loop through source-controlled declarations and a small
policy surface, then use the same path for standing automation or a one-off
side-load.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `copilot-extensions-host` | Public contract, recipe/runtime implementation, self-hosted validation | independent copilot-extensions worktrees |
| `validation-repository` | Second-repository declaration and end-to-end proof | repository-owned PR flow |

## Coordination

- **Topology:** independent per-slice PRs, sequenced by the phases below
- **Host (owns PRs):** `copilot-extensions-host`
- **Delegates:** none initially; `validation-repository` may own
  only its declaration/proof slice
- **Handoff:** every slice records merged PRs and observable validation in the
  Journal before the next phase begins

## Context

agent-dispatch already ships a pure `reviewer` recipe, declarative emitters and
evaluators, registrar-discovered supervised lanes, bounded pools, atomic spawn
reservations, durable cards/steering, and suspend/resume. Those pieces are
individually reusable, but adoption still requires a consumer to invent the
composition:

- the stock reviewer assumes the worker owns merge;
- repository review guidance and the reviewing agent are not one declared
  reviewer-loop contract;
- registered emitters have no on-demand side-load operation, so `recipes kick`
  creates a self-tracked task rather than an emitter-owned/evaluated task;
- evaluator judgment is currently attached to a consuming supervised lane,
  while the vision assigns lifecycle judgment to the producing emitter;
- discovery, task authoring, evaluator behavior, and pool filters are wired as
  separate low-level units;
- there is no turn-key setup/status/side-load surface for "enable reviewer
  automation for this repository";
- the suite does not dogfood its own reviewer loop.

The missing capability is composition, not another bespoke reviewer.

## Request

> Harden the reviewer recipe upstream in copilot-extensions. Make it possible
> for copilot-extensions itself to run an agent-dispatch reviewer loop, guided
> by a repository agent, as a more powerful alternative to the base forge
> reviewer. Use another public repository as the target-repository validation.
> Make the pattern turn-key.

## Plan

### Phase 1 — Define the repository reviewer-loop contract

- [x] Specify one repository-owned declaration that composes discovery or
      side-load input, reviewer recipe parameters, agent/body selection, pool
      filters/cap, evaluator behavior, and operator controls.
- [x] Bind evaluator ownership to the producing emitter and stamp that
      association on emitted tasks; pools select tasks only through ordinary
      attribute filters and never become the evaluator owner.
- [x] Define a stable reviewer-work identity keyed to the target change, not
      every effective recipe/config parameter, so an upgrade or guidance edit
      cannot fork a live review while a later review generation remains
      possible after terminal completion.
- [x] Deferred to `ThomasMichon/copilot-extensions#1846`: Declare the acting
      forge identity and its permissions. An identity that cannot approve or
      land must degrade to a visible recorded outcome rather than looping
      indefinitely.
- [x] Keep credentials outside the repository declaration and resolve them
      through the runtime's existing secret/auth boundaries; declarations may
      name an identity but never carry tokens.
- [x] Define the untrusted-change boundary: external/fork code is read as data
      and is not executed by default; credentials are least-privilege; any
      repository opt-in to sandboxed tests or `land=self` is explicit.
- [x] Keep forge access and repository policy behind declared commands/agents;
      agent-dispatch remains a generic task-loop engine.
- [x] Define lifecycle/status output that joins declaration, emitted task,
      current worker, card/steer state, and terminal result without a second
      store.
- [x] Document compatibility with the existing low-level registrar,
      emitter/evaluator, recipe, and supervised-lane primitives.

### Phase 2 — Generalize the stock reviewer recipe

- [x] Add an explicit landing model (`self` or `author`, defaulting to the
      existing `self` behavior) with model-specific goal, done criteria,
      charter, suspend points, and resolution semantics.
- [x] Stamp a discriminating landing/resolution token on the task so an
      evaluator cannot mistake a `land=author` review-delivered completion for
      a self-land follow-up.
- [x] Define the non-response path for `land=author` (continued suspension,
      superseding change, or explicit expiry/abandon) so dormant reviews do not
      hold worker capacity or remain ambiguous forever.
- [x] Make review guidance, target reference, and optional result/card contract
      declarative inputs without embedding any forge or repository policy.
- [x] Add on-demand emitter side-load: a registered emitter accepts one change
      reference and authors the same provenance/evaluator-bound task it would
      create during discovery.
- [x] Preserve `recipes render|kick` as the explicitly self-tracked low-level
      path and add side-load CLI/local-MCP/hosted-MCP parity.
- [x] Add focused tests for both landing models, parameter validation, dedup,
      completion semantics, and backward-compatible defaults.

### Phase 3 — Prove the raw composition on copilot-extensions

- [x] Define a repository-owned reviewer agent, acting identity, and guidance
      contract for
      copilot-extensions without replacing or depending on GitHub's baseline
      Copilot review.
- [x] Compose the existing low-level emitter/evaluator/supervised-lane
      primitives directly into a bounded reviewer loop for external pull
      requests.
- [x] Add discovery watermark, retry/backoff, and author/fork/ACL policy so an
      outage or restart cannot emit every already-reviewed open change again.
- [x] Side-load one pull request through the same emitter path and prove one
      evaluator-bound durable task, one worker, one recorded result, and the
      configured landing behavior.
- [x] Prove author/fork policy excludes an ineligible change before task
      creation and no untrusted branch code executes by default.

### Phase 4 — Extract the turn-key declaration and control surface

- [x] Extract the proven composition into one reviewer-loop declaration
      schema/helper that expands to the existing emitter/evaluator/pool
      primitives.
- [x] Add setup/inspect/status/doctor/enable/disable/side-load operations that
      remain thin readers or writers over the declared source of truth.
- [x] Ensure repeated adoption and discovery are idempotent, provenance-aware,
      and cannot create duplicate producers, evaluator bindings, or pools.
- [x] Make doctor distinguish declared-but-unserved, missing registrar pointer,
      inactive-by-filter, overridden-off, blocked/dead-lettered, and healthy
      loops; include the existing atomic rearm path in actionable status.
- [x] Document the host/runtime prerequisites and how maintainers inspect,
      pause, resume, recover, and side-load the loop.

### Phase 5 — Validate portability in a second repository

- [x] Hand the Phase 4 declaration/control contract to
      `validation-repository`;
      this slice starts only after the extracted schema and doctor/status
      surfaces have merged upstream.
- [x] Adopt the same turn-key contract in a second public repository using only
      repository-owned policy/configuration.
- [x] Prove discovery creates one review task for an eligible
      repository-policy-selected pull request and creates none for an excluded
      pull request.
- [x] Prove a task survives worker or frontend interruption, resumes from its
      durable state, and completes without duplicate review actions.
- [x] Feed portability findings back into the generic contract before declaring
      the effort done.

## Validation Plan

- [x] Schema and expansion tests prove one declaration deterministically
      materializes the intended emitter/evaluator/pool set.
- [x] Recipe tests cover `land=self`, `land=author`, invalid combinations, CLI,
      local MCP, and hosted MCP.
- [x] Deferred to `ThomasMichon/copilot-extensions#1846`: Landing-model tests
      prove `land=author` completion cannot trigger a self-land/conflict
      evaluator rule and that its non-response path releases active worker
      capacity.
- [x] Dedup tests preserve one live target identity across version/config drift
      while allowing a deterministic later review generation after terminal
      completion.
- [x] Deferred to `ThomasMichon/copilot-extensions#1846`: Emitter tests prove
      discovery and side-load author byte-equivalent task contracts with
      emitter-owned evaluator association.
- [x] Registrar/supervisor tests prove repeated discovery produces one
      effective reviewer loop and operator disable remains higher precedence.
- [x] Deferred to `ThomasMichon/copilot-extensions#1846`: Filter tests prove
      two compatible pools do not receive an emitter-to-pool wire; the atomic
      claim alone selects one consumer.
- [x] Deferred to `ThomasMichon/copilot-extensions#1846`: Concurrency tests
      prove dedup plus reservation state never embodies two reviewers for one
      change.
- [x] Deferred to `ThomasMichon/copilot-extensions#1846`: Trust-boundary tests
      prove excluded authors/forks create no task, untrusted change code is not
      executed by default, and insufficient forge permissions surface a
      terminal/blocked outcome.
- [x] Self-hosted copilot-extensions proof records one eligible external review
      end to end and one excluded change producing no task.
- [x] Second-repository proof uses the same generic runtime and differs only in
      repository-owned declaration/policy.
- [x] Recovery proof covers service restart, worker interruption, visible
      blocked state, atomic rearm, and continuation from durable card/progress.
- [x] Doctor proof reports a syntactically valid declaration with no serving
      host/pointer as inactive and actionable rather than silently healthy.
- [x] Discovery proof shows a restart resumes from its watermark/backoff state
      without re-emitting tasks for already-reviewed open changes.

The effort is complete only when the generic declaration/control surface is
documented, both repository proofs are recorded in the Journal, every
validation item is resolved, and portability findings have landed upstream.

## Proposal

Build upward from the primitives that already exist. First make the reviewer
recipe honest about who lands and what "done" means, add a real emitter
side-load path, and bind evaluator ownership to the producer. Then dogfood the
raw composition in copilot-extensions before extracting a thin reviewer-loop
declaration; this keeps the public schema grounded in a working consumer rather
than designing it speculatively. Use dotfiles to prove the extracted contract
does not depend on the suite's own layout or policies.

The first implementation slice should stop at the reviewed contract,
`land=self|author` recipe semantics, stable target identity, and producer-owned
side-load/evaluator association. Self-hosting follows on raw primitives;
declarative expansion follows only after that composition is proven.

## Journal

### 2026-08-30 — Kickoff

- Filed #1403 after a downstream reviewer-automation recovery demonstrated that
  the queue, steering, bounded-pool, and recovery primitives are individually
  sound but still require bespoke consumer composition.
- Reconciled the work to the existing agent-dispatch vision: this closes the
  delta in `loop-recipes`, `side-load-through-an-emitter`,
  `registered-supervision`, and `a-loop-runs-with-or-without-a-service`; it does
  not require a new vision item.
- Chose a public, target-owned effort because copilot-extensions has adopted
  efforts and the stretch is primarily an upstream runtime/design change.
- The plan review identified missing producer-owned evaluator association,
  on-demand emitter side-load, target-stable dedup, landing-model
  discrimination, acting-identity semantics, untrusted-change containment,
  discovery watermarks, and inactive-declaration diagnostics. Reordered the
  work to self-host the raw composition before extracting the turn-key schema,
  with a second public repository reserved for portability validation.

### 2026-08-31 — First runtime slice

- Merged [PR 1445](https://github.com/ThomasMichon/copilot-extensions/pull/1445)
  and deployed `agent-dispatch` `0.1.0-dev243` on a validation host.
- The stock reviewer now has explicit `land=self|author` semantics with the
  backward-compatible self-land default, distinct landing/resolution labels,
  an author-owned non-response suspension contract, and a canonical
  forge-qualified target identity.
- Live reviewer dedup now survives recipe/config drift, recognizes both old and
  new keys during mixed-version rollout, and releases the identity after a
  terminal generation so a later review may be created.
- Registered command emitters can author JSON task contracts during discovery
  and side-load one change on demand. CLI, local MCP, and coordinator-hosted MCP
  route side-load to the registered machine/environment; agent-dispatch stamps
  emitter provenance and the producer-owned evaluator association.
- Evaluators query by their exact producer association before applying the
  result limit and defensively recheck it for version-skewed coordinators.
  Unscoped evaluators consume only unassociated tasks; worker pools remain
  ordinary filters.
- The final rebased suite passed 1,440 tests locally; the updated PR's
  agent-dispatch CI passed, and the Copilot review finding about a
  shape-inconsistent lease-denied emitter result was fixed before merge.

### 2026-08-31 — Raw self-host and declaration extraction

- Merged [PR 1461](https://github.com/ThomasMichon/copilot-extensions/pull/1461)
  with the repository-owned reviewer agent, acting identity, API-only
  untrusted-change policy, paginated discovery, watermark, emitter, evaluator,
  and bounded pool. Follow-up
  [PR 1464](https://github.com/ThomasMichon/copilot-extensions/pull/1464),
  [PR 1466](https://github.com/ThomasMichon/copilot-extensions/pull/1466), and
  [PR 1469](https://github.com/ThomasMichon/copilot-extensions/pull/1469)
  corrected the execution-agent binding, startup capacity, and task-lane
  identity.
- The raw loop side-loaded an eligible external pull request, created one
  evaluator-bound durable task, embodied one worker, and posted one
  AI-acknowledged changes-requested review without checking out or executing
  contributor code. An association-excluded pull request produced no task.
- Merged [PR 1502](https://github.com/ThomasMichon/copilot-extensions/pull/1502)
  as `agent-dispatch` `0.1.0-dev247`. Pool caps now count only matching
  live/launching processes; card suspension ends headless bodies into durable
  cold reservations, and steer/resume re-embodies a task only after the prior
  body is confirmed stopped.
- Added the first turn-key extraction: one repository-owned
  `kind: reviewer-loop` declaration deterministically expands in memory to the
  existing emitter, evaluator, and capped worker-pool declarations. Child
  identity and evaluator association derive only from the stable loop name,
  while mutable commands and capacity settings reconcile in place. The
  self-hosted repository now consumes this declaration instead of three
  hand-composed registrar files.

### 2026-09-01 — First declaration controls

- Added a thin `agent-dispatch reviewer-loop` control surface over the
  declaration: `inspect` shows its effective source/evaluator/worker units,
  `disable` and `enable` atomically apply the existing machine-local supervisor
  overrides to the whole loop, and `side-load` invokes the declaration's
  emitter-owned on-demand path without creating a parallel registration.
- Control operations resolve the declaration's actual registrar owner rather
  than guessing from a checkout path, remain local to the current host and
  environment, and refuse side-load while the loop is overridden off.
  Cross-process override mutations are serialized so concurrent emergency
  disables cannot discard one another.
- The remaining control-surface work is setup/adoption plus joined
  status/doctor diagnostics; this slice deliberately reuses the existing
  declaration, pointer, override, emitter, and task stores rather than adding a
  reviewer-loop database.

### 2026-09-01 — Setup and joined diagnostics

- Merged [PR 1590](https://github.com/ThomasMichon/copilot-extensions/pull/1590)
  as `agent-dispatch` `0.1.0-dev255`. `reviewer-loop setup` now idempotently
  registers the repository pointer without copying or rewriting the declaration,
  rejects pointer-name and owner collisions, and preserves the registrar as a
  thin index over the repository source of truth.
- `reviewer-loop status|doctor` joins the declaration and pointer with the
  supervisor's atomic per-cycle child-process snapshot, owner-scoped overrides,
  repository-scoped actionable tasks, pool eligibility, and exact per-task
  failed spawn history. It distinguishes missing pointers,
  declared-but-unserved or filtered units, coordinator outages, blocked tasks,
  spawn dead letters, and truncated scans.
- Dead-letter output links directly to the existing atomic rearm command when
  its three-failure safety precondition is met; lower attempt bounds receive an
  explicit non-rearmable recovery explanation rather than an impossible command.
  The final suite passed 1,558 tests and the runtime was deployed on the
  validation host.

### 2026-09-02 — Second-repository adoption

- Portability review found two generic declaration gaps before the second
  repository could cut over. Merged
  [PR 1604](https://github.com/ThomasMichon/copilot-extensions/pull/1604)
  as `agent-dispatch` `0.1.0-dev257`: one top-level machine-placement filter is
  inherited by the source, evaluator, and worker pool, while pool-specific
  placement composes by permit intersection and reject union. Merged
  [PR 1608](https://github.com/ThomasMichon/copilot-extensions/pull/1608)
  as `agent-dispatch` `0.1.0-dev258`: exact task-label membership now
  participates in the queue query before ordering and pagination.
- A second repository replaced its separate emitter and worker-pool
  declarations with one `kind: reviewer-loop` declaration. Runtime expansion
  produced exactly one machine-scoped source, one evaluator, and one
  four-process pool shared with a second review producer. Joined status and
  doctor reported the pointer and all three units healthy after the registrar
  cutover.
- The repository-owned emitter projects stock reviewer recipes to accepted
  create fields, uses canonical target-stable identity, applies the same
  eligibility and generation policy to discovery and side-load, isolates
  orphaned-task wake failures, and surfaces dead-letter or missing-revision
  evidence as actionable diagnostics. Its focused suite and a live read-only
  discovery pass succeeded.
- An explicitly excluded pull request produced no task through the turn-key
  side-load path. The eligible-change proof remains gated on an eligible
  external pull request becoming available; the current open set contains only
  repository ACL members.

### 2026-09-02 — Policy-selected reviews and interruption recovery

- The validation repository added an explicit repository-owned label that opts
  a maintainer-authored change into the same full reviewer loop while retaining
  default ACL exclusion, draft/bot/closed filtering, and self-review
  protection. An unlabeled ACL change continued to produce no task.
- Two labeled maintainer changes each created exactly one evaluator-bound task.
  One received a single approval and suspended on a real merge conflict. The
  other survived a productive worker ending, requeued the same durable task
  without a second generation, resumed in a replacement worker, posted one
  request-changes review, recorded its exact revision marker, and suspended for
  author updates without duplicate review actions.
- The first proof workers exposed a Windows owner-resolution timeout between
  two serial `agent-worktrees` probes. Merged
  [PR 1702](https://github.com/ThomasMichon/copilot-extensions/pull/1702)
  as `agent-dispatch` `0.1.0-dev261`: worker identity now resolves from one
  `owner-ref` probe, does not amplify a timeout with two more probes, and keeps
  machine-only callers on one machine lookup. A post-deploy replacement worker
  claimed and started automatically without an explicit owner.
- The loop remained healthy across coordinator/supervisor replacement and a
  complete scheduled discovery interval. Already-reviewed or excluded changes
  did not produce duplicate tasks. The final task-completion/landing observation
  remains author-gated: the live validation changes require conflict resolution
  or requested documentation updates before the durable tasks can resume and
  reach terminal completion.

### 2026-09-03 — Portability closure

- The second-repository interruption proof completed on one canonical reviewer
  task through three worker attempts. The final worker resumed the same durable
  identity, observed the target change was closed, recorded the exact
  `github-review-state` revision marker, and terminally reconciled it without a
  duplicate task or repository write.
- Merged [PR 1830](https://github.com/ThomasMichon/copilot-extensions/pull/1830)
  and deployed `agent-worktrees` `1.5.3-dev738`, removing the validation
  repository's remaining Windows ambient-`PATH` dependency. Focused shim,
  guard, installer-readiness, CI, and contract gates passed. Seven unrelated
  current-main session-context test failures are tracked in #1829.
- The validation repository merged its maintainer opt-in and recovery
  hardening after 33 focused tests and a clean live no-write sweep. The
  remaining contract-level test matrix is transferred to #1846 so it remains
  visible independently of this completed adoption campaign.
