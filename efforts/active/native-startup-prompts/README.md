# Native Startup Prompts

- **Slug:** `native-startup-prompts`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal and implementation PRs against `dev`
- **Created:** 2026-10-28
- **Status:** Active
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
- [x] Submit this proposal through automated PR review and merge before
  production source edits.
- [x] Record the reviewed contract and select the smallest first consumer slice.

### Phase 2 - Managed cold-launch implementation (agent-recommended)

- [x] Trace the existing cold-launch setup and final invocation boundary;
  select the existing native helper and pre-effect prompt claim seam.
- [x] Deliver explicit and staged New/Resume prompts through the native startup
  mechanism on qualified cold-launch paths, retaining selected conversation
  identity, hosting policy, admission fences and exact literal input.
- [x] Preserve deferred Bridge acquisition and its SDK inbox ownership; do not
  pass a Bridge-owned seed through two transports.
- [x] Define truthful pre-effect refusal, launch handoff and unknown outcomes
  using existing seed/attempt receipts. A spawned process or forwarded argv
  does not prove a consumed prompt. Never automatically replay an ambiguous turn.
- [x] Keep an explicitly supported terminal fallback where native support or
  the launch shape requires it; no implicit downgrade after possible delivery.
  Leave already-live delivery with its existing owner.
- [x] Update affected help/docs and add the required plugin changefile without
  editing versions or installed payloads.

### Phase 3 - Qualification and source completion (agent-recommended)

- [ ] Complete the focused validation below and fix regressions before publication.
- [x] Land the bounded implementation through normal CI, review and merge gates.
- [x] Record exact source acceptance and any unsupported native/platform lane;
  transfer release/rollout or broader integration work to a named objective.
  Do not report a source merge as live deployment or close neighboring issues.

## Validation Plan

All implementation checks below are **agent-recommended**; they preserve the
operator's no-rollout/no-restart constraint.

- [x] Focused unit regressions fail on the old ordinary cold-launch path and
  pass for native initial-prompt selection without a pane-readiness scrape.
- [x] Exercise the real supported parser/setup/process boundary with literal
  quotes, whitespace, multiline, non-ASCII and option-shaped input; qualify
  size limits and invalid/NUL input with an explicit pre-effect outcome rather
  than truncation or silent fallback.
- [x] Qualify fresh and resumed conversations with an isolated native CLI
  fixture: the resume-plus-prompt path keeps the exact conversation and executes
  the intended next turn. No retained operator conversation is used or restarted.
- [x] Cover staged versus explicit intent, superseding revisions, no-mux where
  supported, already-live targets, refused launch and unknown post-launch
  outcomes without duplicate prompt delivery or consumption claims.
- [x] Exercise current/prior supported provider behavior and rollback through
  existing contract seams. Missing support remains explicit; no blind replay.
- [ ] Qualify Windows and POSIX boundaries independently. Preserve the open
  Bash-reader owner's scope; unavailable lanes remain named, not presumed passed.
- [x] Run affected lint, test portfolio and install-contract checks. Use bounded
  contained tests; no new heavy native matrix in required PR CI.
- [x] Check public artifacts for private prompt/path leakage and verify there
  were no installed edits, service changes, retained-session restarts or rollout.

## Proposal

Reviewed in #9365. The first consumer is ordinary cold `embody`; existing
native-seed consumers and already-live delivery retain their owners.
An isolated CLI fixture
may establish native semantics without touching an existing agent; its launch,
capture and cleanup must remain caller-owned. A source change that cannot
preserve the existing custody or fallback contract returns here for an amendment
instead of weakening those guarantees.

## Journal

### 2026-10-28,08:26 UTC - Cold-launch source merged; remaining qualification retained

- #9374 actually merged as `9dc3491fc527bbf5561a082341c2b12c44343087`.
  The exact final head `f97c39c6cd223744405bfb24db5f913c279df748` has a
  Copilot Comment with no open issues; its overview's concurrency/directory
  concerns were assessed against the intentional pre-effect refusals, real
  writer interleavings and actual directory binder controls. This is not a
  claim that the verdict was Approve.
- All four review threads are resolved, required CI passed, and the managed
  Maintainer merge omitted only the separately authorized additional vote.
  No blocking finding or CI requirement was bypassed.
- Re-read this plan after merge. The bounded source item is closed; complete
  native POSIX/full-mux and installed acceptance remain open under #9364 and
  their existing owners. No promotion, rollout, service change, retained-session
  restart or task acceptance follows from this merge.

### 2026-10-28 - Cold-launch source and Windows qualification

- Ordinary cold `embody` now stages explicit intent or validates the existing
  New seed, then reuses `launch_seed_exec`'s final-invocation boundary. It does
  not claim or scrape after launching the mux. Typed Resume staging elsewhere,
  Bridge acquisition, already-live targets, anchor and cutover are unchanged.
- Existing setup markers and direct-backend support determine selection
  without adding a provider option or a per-launch probe. Unmarked/custom and
  intermediary commands keep their previous terminal fallback. The selected
  deferred runtime retains its existing invocation protocol and refusal checks;
  unsupported setup cannot silently become prompt consumption.
- Actual CLI 1.0.100-1, offline loopback provider, empty workspace and isolated
  configuration: one exact native conversation accepted a literal multiline,
  quoted, non-ASCII, option-leading first prompt and a 12,000-character second
  prompt through `--resume`; both turns completed and prior history reached the
  second request. No terminal input, authentication or external inference used.
- That real parser control exposed a pre-existing option-leading prompt bug:
  separate `--interactive`/`--resume=literal...` argv was rejected. The shared
  helper now uses `--interactive=<prompt>` only for option-leading input.
- Windows over-limit argv produced the actual OS error 206 before another
  turn. Ordinary NUL input refuses before staging. Existing definite-spawn
  restoration, uncertain handoff and finite NUL-frame semantics remain intact;
  a broader NUL-helper change was rejected by the existing regression packet
  and removed rather than weakening that owner's held/unknown contract.
- Focused contained packet: 449 passed, including real CLI qualification,
  staged/explicit/typed-Resume controls, current/legacy setup selection, marked
  PowerShell/Bash preparation, resume composition and Bridge exclusion. Touched
  Python lint and the thirteen-plugin install contract passed.
- Clean first-touch CLI configuration and the native parser were exercised.
  Actual POSIX CLI, full mux/installed-host acceptance and live external venue
  remain unqualified: this source-only run has no admitted native POSIX or
  external target, and rollout/restart is explicitly forbidden. Bash/setup
  framing controls are not native Linux evidence. Those gates remain open
  under #9364; no installation-independent POSIX consumer is activated.
- Review corrections keep snapshot validation and explicit replacement atomic
  inside the existing record writer. Conditional creation staging requires
  writer version 8; legacy calls retain the version-7 baseline and a guarded
  call never reaches an unaware resident. The existing canonical direct writer
  fallback retains ownership. Interleaving claim/New/Resume/absent-state controls
  and a real legacy-rendezvous refusal pass. Nested PowerShell/Bash wrappers
  now preserve intermediary exclusion recursively rather than selecting a
  startup path that will refuse `gh copilot`. The changed packet passed 266
  tests after both corrections.
- The next compatibility review exposed gh-only normalized setup and UTF-16
  custom setup. Selection now checks the existing explicit backend/session PATH
  against the actual launch environment, retaining terminal fallback when only
  an intermediary is available. Unsupported marker encoding is classified and
  logged before staging rather than aborting a valid custom launcher. The
  corresponding PowerShell/Bash and encoding packet passed 276 tests.
- Qualification now consumes the actual execution-directory fields injected
  by `bind_execution_directory`, plus setup switches and complete native option
  values. Prepared-command controls use that real binder before qualification;
  they preserve explicit-gh refusal and session-PATH selection under normal
  and recovery launches without misreading an option-shaped native value.
  The updated state/launch-argument packet passed 326 tests. The shared
  execution-directory validation contract is unchanged.
- Environment-priming scripts remain opaque even when an explicit backend is
  configured: preparation neither executes them as a probe nor pins their later
  lookup. Both setup forms retain their prior terminal fallback for that whole
  class. The prepared normal/recovery/backend packet passed 24 cases, including
  currently available standalone and explicitly selected executable controls.

### 2026-10-28 - Proposal reviewed

- #9365 merged after exact-head approval and required CI; the first-prompt
  observation ownership correction preserves #7942 separately from Bridge.
- Pulled the source worktree onto the reviewed proposal. The complete plan
  remains active; implementation and isolated qualification follow without
  installing anything or restarting a retained conversation.

### 2026-10-28 - Kickoff

- Captured the confirmed source-only request and slug; no rollout or restart.
- Claimed #9364 after checking #7116/#8375 ownership and open native-prompt work.
- Found the existing native seed helper and its separate consumers; ordinary
  `embody_completion` still uses terminal seed injection. No production edit.
