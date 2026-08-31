# Turn-key Reviewer Loops

- **Slug:** `turnkey-reviewer-loops`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees and pull requests
- **Created:** 2026-08-30
- **Status:** Active
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

- [ ] Specify one repository-owned declaration that composes discovery or
      side-load input, reviewer recipe parameters, agent/body selection, pool
      filters/cap, evaluator behavior, and operator controls.
- [x] Bind evaluator ownership to the producing emitter and stamp that
      association on emitted tasks; pools select tasks only through ordinary
      attribute filters and never become the evaluator owner.
- [x] Define a stable reviewer-work identity keyed to the target change, not
      every effective recipe/config parameter, so an upgrade or guidance edit
      cannot fork a live review while a later review generation remains
      possible after terminal completion.
- [ ] Declare the acting forge identity and its permissions. An identity that
      cannot approve or land must degrade to a visible recorded outcome rather
      than looping indefinitely.
- [x] Keep credentials outside the repository declaration and resolve them
      through the runtime's existing secret/auth boundaries; declarations may
      name an identity but never carry tokens.
- [x] Define the untrusted-change boundary: external/fork code is read as data
      and is not executed by default; credentials are least-privilege; any
      repository opt-in to sandboxed tests or `land=self` is explicit.
- [x] Keep forge access and repository policy behind declared commands/agents;
      agent-dispatch remains a generic task-loop engine.
- [ ] Define lifecycle/status output that joins declaration, emitted task,
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
- [ ] Make review guidance, target reference, and optional result/card contract
      declarative inputs without embedding any forge or repository policy.
- [x] Add on-demand emitter side-load: a registered emitter accepts one change
      reference and authors the same provenance/evaluator-bound task it would
      create during discovery.
- [x] Preserve `recipes render|kick` as the explicitly self-tracked low-level
      path and add side-load CLI/local-MCP/hosted-MCP parity.
- [x] Add focused tests for both landing models, parameter validation, dedup,
      completion semantics, and backward-compatible defaults.

### Phase 3 — Prove the raw composition on copilot-extensions

- [ ] Define a repository-owned reviewer agent, acting identity, and guidance
      contract for
      copilot-extensions without replacing or depending on GitHub's baseline
      Copilot review.
- [ ] Compose the existing low-level emitter/evaluator/supervised-lane
      primitives directly into a bounded reviewer loop for external pull
      requests.
- [ ] Add discovery watermark, retry/backoff, and author/fork/ACL policy so an
      outage or restart cannot emit every already-reviewed open change again.
- [ ] Side-load one pull request through the same emitter path and prove one
      evaluator-bound durable task, one worker, one recorded result, and the
      configured landing behavior.
- [ ] Prove author/fork policy excludes an ineligible change before task
      creation and no untrusted branch code executes by default.

### Phase 4 — Extract the turn-key declaration and control surface

- [ ] Extract the proven composition into one reviewer-loop declaration
      schema/helper that expands to the existing emitter/evaluator/pool
      primitives.
- [ ] Add setup/inspect/status/doctor/enable/disable/side-load operations that
      remain thin readers or writers over the declared source of truth.
- [ ] Ensure repeated adoption and discovery are idempotent, provenance-aware,
      and cannot create duplicate producers, evaluator bindings, or pools.
- [ ] Make doctor distinguish declared-but-unserved, missing registrar pointer,
      inactive-by-filter, overridden-off, blocked/dead-lettered, and healthy
      loops; include the existing atomic rearm path in actionable status.
- [ ] Document the host/runtime prerequisites and how maintainers inspect,
      pause, resume, recover, and side-load the loop.

### Phase 5 — Validate portability in a second repository

- [ ] Hand the Phase 4 declaration/control contract to
      `validation-repository`;
      this slice starts only after the extracted schema and doctor/status
      surfaces have merged upstream.
- [ ] Adopt the same turn-key contract in a second public repository using only
      repository-owned policy/configuration.
- [ ] Prove discovery creates one review task for an eligible external pull
      request and creates none for an excluded pull request.
- [ ] Prove a task survives worker or frontend interruption, resumes from its
      durable state, and completes without duplicate review actions.
- [ ] Feed portability findings back into the generic contract before declaring
      the effort done.

## Validation Plan

- [ ] Schema and expansion tests prove one declaration deterministically
      materializes the intended emitter/evaluator/pool set.
- [x] Recipe tests cover `land=self`, `land=author`, invalid combinations, CLI,
      local MCP, and hosted MCP.
- [ ] Landing-model tests prove `land=author` completion cannot trigger a
      self-land/conflict evaluator rule and that its non-response path releases
      active worker capacity.
- [x] Dedup tests preserve one live target identity across version/config drift
      while allowing a deterministic later review generation after terminal
      completion.
- [ ] Emitter tests prove discovery and side-load author byte-equivalent task
      contracts with emitter-owned evaluator association.
- [ ] Registrar/supervisor tests prove repeated discovery produces one
      effective reviewer loop and operator disable remains higher precedence.
- [ ] Filter tests prove two compatible pools do not receive an emitter-to-pool
      wire; the atomic claim alone selects one consumer.
- [ ] Concurrency tests prove dedup plus reservation state never embodies two
      reviewers for one change.
- [ ] Trust-boundary tests prove excluded authors/forks create no task,
      untrusted change code is not executed by default, and insufficient forge
      permissions surface a terminal/blocked outcome.
- [ ] Self-hosted copilot-extensions proof records one eligible external review
      end to end and one excluded change producing no task.
- [ ] Second-repository proof uses the same generic runtime and differs only in
      repository-owned declaration/policy.
- [ ] Recovery proof covers service restart, worker interruption, visible
      blocked state, atomic rearm, and continuation from durable card/progress.
- [ ] Doctor proof reports a syntactically valid declaration with no serving
      host/pointer as inactive and actionable rather than silently healthy.
- [ ] Discovery proof shows a restart resumes from its watermark/backoff state
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
  and deployed `agent-dispatch` `0.1.0-dev243` on cloud1.
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
