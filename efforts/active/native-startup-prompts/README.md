# Native Startup Prompts

- **Slug:** `native-startup-prompts`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal and implementation PRs against `dev`
- **Created:** 2026-10-28
- **Status:** Draft
- **Vision:** `visions/native-convergence/README.md` (native substrate, feature-detected convergence and no capability regression); `visions/session-hosting/README.md`
- **Umbrella issue:** #9364
- **Sub-issues:** none yet

## Guiding Intent

Use the released CLI's initial-prompt mechanism as the primary delivery path
for an authorized managed cold launch. Terminal rendering must not be the
normal control-plane prerequisite for starting a task. Preserve conversation
identity, prompt custody, private recovery, truthful outcomes and supported
fallbacks. Subsequent messages use the existing Bridge inbox and receipts.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Startup owner | Proposal, bounded source implementation, qualification and PRs | Public coordination issue #9364; source worktree `buzzing-flywheel` |
| Existing lifecycle owners | Retain their distinct acceptance and implementation scopes | #7116, #8375, #7942, #8669, #8864, #9321 and #5579 |

## Coordination

- **Topology:** sequential proposal and implementation PRs; no source-hosted agent.
- **Host (owns PRs):** Startup owner.
- **Delegates:** none; existing lifecycle owners are not reassigned.
- **Handoff:** journal source and qualification outcomes here; transfer a remaining
  objective explicitly rather than infer acceptance from a launch or source merge.

## Context

The source already has `embody_resume.with_seed`, which appends the full
`--interactive` option through the native argument helper. `launch_seed_exec`
and native restart code use that primitive. Ordinary cold `embody`, however,
launches first and calls `mux_seed_pane` from `embody_completion`, coupling
initial delivery to rendered input readiness. Reuse existing primitives and
seed-state ownership rather than introducing a separate broker or journal.

The actual supported CLI help advertises `--interactive <prompt>` and
`--resume <id>`. This is discovery evidence, not fresh proof of their combined
behavior through every managed setup wrapper. Historical helper documentation
is not this effort's qualification receipt.

Ownership boundaries:

- #7116 retains its complete nine-case Windows acquisition report; this effort
  does not close it by solving a launch subset.
- #8375 retains private 90-day prompt recovery, launch-attempt correlation and
  inspection. Reuse its attempt identity; do not make a parallel outcome store.
- #7942 retains asynchronous, non-provisioning first-prompt receipt observation
  in context-handoff, including prompt-return latency and pending/unavailable
  reconciliation. Observation does not relax handoff ownership or consumption.
  Bridge's existing delivery implementation separately owns live readiness,
  SDK message consumption and its delivery receipts.
- #8669/#8864/#9321 retain installation-independent shells and the finite
  counted-NUL Bash seed reader; do not activate or replace that open work.
- #5579 retains broader wrapper-depth reduction. A native prompt change must
  compose with existing wrappers rather than turn into their redesign.

The proposal advances existing native-convergence intent, not a new vision.
Apply `docs/patterns/service-contract-evolution.md`: no lockstep upgrade
assumption, speculative effectful probe followed by replay, or new mandatory
peer flag merely to request behavior the launch owner should implement.

## Request

Operator-selected scope, verbatim:

> Implement and qualify the managed native-prompt path; no rollout or restart yet

Confirmed effort slug: `native-startup-prompts`.

The design and validation details below are **agent-recommended** ways to
realize that source-only request. They are not additional deployment authority.

## Plan

### Phase 1 - Reviewed contract and ownership

- [x] Deduplicate and claim #9364; preserve the neighboring owners above.
- [ ] Submit this proposal through automated PR review and merge before
  production source edits.
- [ ] Record the reviewed contract and select the smallest first consumer slice.

### Phase 2 - Managed cold-launch implementation (agent-recommended)

- [ ] Trace the existing cold-launch setup and final invocation boundary;
  select the existing native helper and pre-effect prompt claim seam.
- [ ] Deliver explicit and staged New/Resume prompts through the native startup
  mechanism on qualified cold-launch paths, retaining selected conversation
  identity, hosting policy, admission fences and exact literal input.
- [ ] Preserve deferred Bridge acquisition and its SDK inbox ownership; do not
  pass a Bridge-owned seed through two transports.
- [ ] Define truthful pre-effect refusal, launch handoff and unknown outcomes
  using existing seed/attempt receipts. A spawned process or forwarded argv
  does not prove a consumed prompt. Never automatically replay an ambiguous turn.
- [ ] Keep an explicitly supported terminal fallback where native support or
  the launch shape requires it; no implicit downgrade after possible delivery.
  Leave already-live delivery with its existing owner.
- [ ] Update affected help/docs and add the required plugin changefile without
  editing versions or installed payloads.

### Phase 3 - Qualification and source completion (agent-recommended)

- [ ] Complete the focused validation below and fix regressions before publication.
- [ ] Land the bounded implementation through normal CI, review and merge gates.
- [ ] Record exact source acceptance and any unsupported native/platform lane;
  transfer release/rollout or broader integration work to a named objective.
  Do not report a source merge as live deployment or close neighboring issues.

## Validation Plan

All implementation checks below are **agent-recommended**; they preserve the
operator's no-rollout/no-restart constraint.

- [ ] Focused unit regressions fail on the old ordinary cold-launch path and
  pass for native initial-prompt selection without a pane-readiness scrape.
- [ ] Exercise the real supported parser/setup/process boundary with literal
  quotes, whitespace, multiline, non-ASCII and option-shaped input; qualify
  size limits and invalid/NUL input with an explicit pre-effect outcome rather
  than truncation or silent fallback.
- [ ] Qualify fresh and resumed conversations with an isolated native CLI
  fixture: the resume-plus-prompt path keeps the exact conversation and executes
  the intended next turn. No retained operator conversation is used or restarted.
- [ ] Cover staged versus explicit intent, superseding revisions, no-mux where
  supported, already-live targets, refused launch and unknown post-launch
  outcomes without duplicate prompt delivery or consumption claims.
- [ ] Exercise current/prior supported provider behavior and rollback through
  existing contract seams. Missing support remains explicit; no blind replay.
- [ ] Qualify Windows and POSIX boundaries independently. Preserve the open
  Bash-reader owner's scope; unavailable lanes remain named, not presumed passed.
- [ ] Run affected lint, test portfolio and install-contract checks. Use bounded
  contained tests; no new heavy native matrix in required PR CI.
- [ ] Check public artifacts for private prompt/path leakage and verify there
  were no installed edits, service changes, retained-session restarts or rollout.

## Proposal

Pending review. Implementation is not yet admitted. An isolated CLI fixture
may establish native semantics without touching an existing agent; its launch,
capture and cleanup must remain caller-owned. A source change that cannot
preserve the existing custody or fallback contract returns here for an amendment
instead of weakening those guarantees.

## Journal

### 2026-10-28 - Kickoff

- Captured the confirmed source-only request and slug; no rollout or restart.
- Claimed #9364 after checking #7116/#8375 ownership and open native-prompt work.
- Found the existing native seed helper and its separate consumers; ordinary
  `embody_completion` still uses terminal seed injection. No production edit.
