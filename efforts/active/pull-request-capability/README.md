# Pull-Request Capability

- **Slug:** `pull-request-capability`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-15
- **Status:** Draft
- **Vision:** [`plugins/agent-worktrees/pull-requests`](../../../visions/plugins/agent-worktrees/pull-requests/README.md)
  (all three Features: `conformance-verified-mock-provider`,
  `foreign-repo-pr-operations`, `reviewer-capable-provider`)
- **Umbrella issue:** none (three sibling issues below, no umbrella needed at
  this size)
- **Sub-issues:** [#2691](https://github.com/ThomasMichon/copilot-extensions/issues/2691)
  (conformance-verified mock provider),
  [#2700](https://github.com/ThomasMichon/copilot-extensions/issues/2700)
  (foreign-repo addressing),
  [#2699](https://github.com/ThomasMichon/copilot-extensions/issues/2699)
  (reviewer-capable provider)

## Guiding Intent

Close the delta between the `pull-requests` vision and today's `PRProvider`
reality: a PR capability that is author-side-only, CWD-bound to a local
checkout, and has no fabricated provider to test against. Land the three
Features in an order where each phase's foundation makes the next phase
safer and cheaper to build and test, rather than three independent patches
landed in any order.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Designs and lands all three phases | independent per-phase worktree |

## Coordination

- **Topology:** single driver, sequential phases (each phase's own worktree,
  its own PR, `pr-self-merge`).
- **Host (owns PRs):** the driving agent/machine.
- **Delegates:** none yet.
- **Handoff:** each phase closes with `python -m pytest` (or this repo's
  equivalent) green and its PR merged before the next phase's worktree opens;
  a fresh session may pick up at any phase boundary via a stored handoff.

## Context

Repo: `ThomasMichon/copilot-extensions`. Relevant source:
`plugins/agent-worktrees/src/agent_worktrees/providers/base.py` (the
`PRProvider` protocol and `_PROVIDERS` registry: `github`, `gitea`,
`azure-devops` today), its concrete provider modules, and the existing
`plugins/agent-worktrees/tests/test_pr_*.py` / `test_providers.py` suite.

The vision and all three issues were mined from a live odsp-web-harness
clean-room finding: a `code-review` scenario-eval run correctly reported
BLOCKED ("no PR available") rather than fabricate a review under literal
mode -- honest behavior that also surfaced these three gaps in the
underlying PR capability. See the vision's own Provenance for the full
trace; not repeated here (public-artifact rule: keep this effort generic).

## Request

> Carve the effort, then tackle in a handoff.

(Verbatim operator instruction following the vision's landing — see
`visions/plugins/agent-worktrees/pull-requests/README.md` PR #2692.)

## Plan

### Phase 1 — Provider conformance contract + mock provider
- [ ] Formalize the existing `test_pr_*.py` / `test_providers.py` suite (or
      a curated subset of it) into an explicit, named conformance contract
      that any `PRProvider` implementation — real or fabricated — is run
      against, per Vision §Features/`conformance-verified-mock-provider`.
- [ ] Decide the mock provider's fabrication strategy: an in-process fake
      store (simplest, fastest, no external process) vs. a purpose-built MCP
      sub-agent that fabricates PR details/diffs (richer, closer to what a
      driven agent would actually interact with, per the operator's original
      framing). Start with the in-process fake unless the conformance
      contract proves it insufficient — the simpler shape first, escalate
      only if needed.
- [ ] Implement `MockPRProvider` satisfying the `PRProvider` protocol;
      register it in `_PROVIDERS` as `mock`.
- [ ] Run the conformance contract against `mock` and confirm it passes the
      same assertions real providers do (adjusting only what is genuinely
      forge-specific, e.g. exact URL formats).

### Phase 2 — Foreign-repo addressing
- [ ] Design the addressing mechanism (e.g. a `--repo <name>` flag/config on
      the `pr-*` command family) per Vision
      §Features/`foreign-repo-pr-operations`. Resolve the named repo's own
      registered provider/policy from the registry; never fall back to the
      calling worktree's own repo/provider.
- [ ] Implement honest failure per Vision
      §Behaviors/`foreign-target-resolves-honestly`: an unregistered or
      unreachable target reports plainly, not silently.
- [ ] Wire foreign-repo addressing through the existing author-side
      operations first (create/watch/merge/status/complete/ready) so Phase 3
      inherits it rather than retrofitting.
- [ ] Extend the conformance contract (Phase 1) to run once against a local
      target and once against a foreign-addressed target, proving the two
      paths converge on the same provider dispatch.

### Phase 3 — Reviewer-capable provider
- [ ] Extend the `PRProvider` protocol with reviewer-side operations: read
      current diff/surrounding context, read/post comments, read/resolve
      review threads, publish a verdict — per Vision
      §Features/`reviewer-capable-provider`.
- [ ] Implement across `github`, `gitea`, `azure-devops`, and `mock`.
- [ ] Extend the conformance contract to cover the new operations for every
      provider, including `mock`.
- [ ] Expose the new operations through the CLI alongside the existing
      `pr-*` family.

### Phase 4 — Validate and land
- [ ] Full `plugins/agent-worktrees` test suite passes with all three
      Features implemented.
- [ ] Each phase already landed via its own PR (per-phase, not batched) —
      this phase is a final confirmation pass, not a fourth PR of its own
      unless cleanup is needed.
- [ ] Note whether `agent-dispatch/reviewer` should be updated to compose
      the new reviewer-side operations instead of any forge calls it makes
      today — file as a follow-on issue if it's out of this effort's scope
      rather than silently expanding Phase 3.

## Validation Plan

- [ ] `mock` is a registered `PRProvider` passing the same conformance
      contract as `github`/`gitea`/`azure-devops`.
- [ ] A `pr-*` operation against a named foreign repo (no local checkout)
      resolves that repo's own provider and succeeds or fails honestly —
      demonstrated against `mock` at minimum, ideally against one real
      provider too.
- [ ] Reviewer-side operations (diff, comments, threads, verdict) work
      end-to-end against `mock`, and against at least one real provider.
- [ ] No existing `pr-*` consumer or test regresses.

## Proposal

_Pending._

## Journal

### 2026-09-15 — Kickoff
- Effort created immediately after the `pull-requests` vision landed
  (PR #2692). Filed and linked the three sibling issues (#2691, #2699,
  #2700) that carve its delta. Ordered phases so the conformance
  contract/mock (Phase 1) and foreign-repo addressing (Phase 2) land before
  reviewer-side operations (Phase 3), so the newest surface is built with
  both already in place rather than retrofitted.
- Not started: no implementation yet. Next session should begin Phase 1
  (formalize the conformance contract, decide the mock's fabrication
  strategy, implement `MockPRProvider`).
