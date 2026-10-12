# PR Recovery Break Glass

- **Slug:** `pr-recovery-break-glass`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase reviewed PRs off `dev`
- **Created:** 2026-10-09
- **Status:** Done
- **Vision:** [pull-request capability](../../../../visions/plugins/agent-worktrees/pull-requests/README.md)
  `operator-authorized-pr-recovery` and `recovery-does-not-require-perfect-bookkeeping`
- **Coordination issue:** #5817

## Guiding Intent

Make guards assist deliberate PR recovery rather than turn imperfect historical
bookkeeping into an absolute veto. Prefer the source-owning worktree's CWD when
available, retain the ordinary supported publication path, and provide an
explicit, reasoned, auditable operator exception when that context or its
ownership/replay evidence cannot be recovered.

The exception is not a generic force push or a review bypass. It retains the
exact expected remote-head fence and normal repository publication/merge gates.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Operator | Authorizes recovery policy and consequential use | Interactive review |
| Recovery driver | Owns this plan, override implementation, validation and rollout | Normal repository worktree/PR flow |
| Existing publisher driver | Owns the explicit rewrite and compatible legacy-evidence work already underway | #5817 and PR #5835 |
| Backup recovery driver | Owns durable pre-sync recovery points and replay compatibility | #5990 and PR #5994 |

## Coordination

- **Topology:** sequential, independently reviewed PRs; one publisher path.
- **Host:** Recovery driver owns this effort's PRs.
- **Existing work:** coordinate with the publisher driver; extend its publication
  surface rather than build a parallel Git client or race its core changes.
  Reuse the backup recovery driver's helper rather than introduce another one.
  The recovery driver can develop disjoint confirmation/audit code while peer
  work is in review; shared publisher adapter changes wait for integration.
- **Handoff:** journal completed slices and carry the next unresolved Plan item.
  The broader automatic compatibility work under #5817 is not silently claimed
  or declared complete by this effort.

## Context

The supported source-owned rebase guard requires a worktree-associated private
head plus historical base, patch and Git replay evidence. Those are useful
normal-path protections, but they can reject a deliberately recovered existing
PR from another owned worktree after the original context is unavailable.

PR #5835 already develops an explicit exact-lease rewrite surface and
publication authority/serialization. Its #5817 continuation also tracks making
compatible legacy bookkeeping converge through one command. This effort is
the distinct operator-exception slice: do not duplicate that publisher or
mistake its still-strict normal ownership attestation for a break-glass path.

Relevant patterns are `docs/patterns/project-scoped-invocation.md` (explicit
addressing must resolve the same target as CWD discovery) and
`docs/patterns/README.md`'s attributable invocation and pipeline-deployment
invariants.

## Request

Operator, verbatim:

> We need to update the "rules and guards" here to be way less strict. While we
> should call the tool from the CWD of the other worktreee when possible, we need
> some "break-glass" way to overcome this, if we're trying to diagnose and unblock
> things.

The implementation decomposition and retained safety boundaries below are
agent-recommended details, not additional verbatim operator instructions.

## Plan

### Phase 1 - Reviewed intent and coordination
- [x] Land the vision extension and this plan through the normal review gate.
- [x] Coordinate the integration boundary with the existing publisher driver,
      preserving one publication surface and avoiding competing core edits.
      Scope recorded on #5817 and #5990; peer publisher and backup helpers stay
      with their named drivers, and shared adapter integration remains gated.

### Phase 2 - Explicit recovery exception
- [x] Extend the existing publication command with an explicit per-operation
      break-glass choice and a non-empty reason; no ambient/global bypass.
- [x] Use the existing `pr-abandon`-style confirmation contract: an unconfirmed
      call previews the exact PR, destination, source and expected remote head,
      refuses, and mutates neither Git refs nor tracking nor the remote. Only
      an explicitly confirmed call after real operator sign-off for that PR
      and proposed rewrite proceeds. A reason, discovered flag, or prior guard
      failure is not sign-off; agents must not add confirmation on their own.
- [x] Bind confirmation to the previewed PR/destination, immutable source
      commit and exact expected remote head. Before admitting publication,
      refuse a changed source or expected head and require a new preview and
      sign-off; do not silently re-plan the confirmed operation. Pin the
      approved source object for transport so later branch movement cannot
      substitute unapproved content.
- [x] Allow operator-authorized recovery despite missing original-worktree,
      suffix, ownership-attestation or replay bookkeeping. Prefer source CWD
      where available, but support an explicitly selected target from a
      recovery context. Do not automatically choose the exception after refusal.
- [x] Retain exact expected-head leases, pinned committed source objects,
      destination/provider authentication, publication serialization, real
      hooks, protected/default/release-history restrictions, and normal PR
      review/merge gates. Do not refresh a stale lease to manufacture permission.
- [x] Report what was overridden and preserve a durable local audit of reason,
      target, pinned source/expected head, confirmation basis and the available
      invocation/session attribution. Distinguish observed provenance from
      authenticated identity, and record missing attribution as unknown rather
      than inventing it. Do not store credential-bearing URLs or automatically
      publish private rationale or invoker details.
- [x] Update authoritative CLI/help and worktree recovery guidance together;
      keep normal incremental publication and compatible legacy recovery intact.
- [x] Add the agent-worktrees changefile and drive the implementation PR to merge.

### Phase 3 - Released and demonstrated recovery
- [x] Verify normal promotion and install the released change through the
      unified update flow; no edits to deployed files or unmerged installation.
- [x] Demonstrate a deliberately authorized existing-PR recovery through the
      supported command from a recovery context, verify its actual remote head,
      and preserve the fresh review/merge gate on the changed head.
- [x] Journal the result, resolve this effort's validation items, archive it,
      and leave unrelated #5817 work with its named publisher driver.

## Validation Plan

- [x] Normal incremental updates and source-owned rebase publication remain
      supported; a guard refusal never selects an exception on its own.
- [x] A break-glass request without per-PR confirmation changes no Git refs,
      tracking or remote state; its explicit preview precedes operator sign-off.
      Confirmed input must match the previewed source and expected remote head.
      Advancing or amending the local source after preview, even with an
      unchanged remote, requires a fresh preview/sign-off and cannot push
      unseen content. A changed target or expected head is likewise refused.
- [x] Real isolated Git transport proves reasoned recovery with incomplete
      historical source/ownership/replay evidence and an unchanged exact lease.
- [x] A moved or deleted remote head is refused even with break-glass selected;
      no plain force or opportunistic lease refresh occurs.
- [x] Wrong destination/provider, protected/default/release refs and uncommitted
      source are refused; real hooks still run and can reject the push.
- [x] CLI adapters select the same target from source CWD and explicit recovery
      context; empty reasons and inapplicable new-PR use fail before mutation.
- [x] Audit and diagnostics distinguish requested, attempted and successful
      overrides, preserve confirmation basis and available invoker/session
      attribution, and disclose unknown provenance honestly. Credential-bearing
      URLs, private reasons and invoker details are not published.
- [x] Targeted bounded tests, lint and source/install guards pass; required CI
      and release validation supply truthful platform coverage.
- [x] A released live recovery supplies provider-confirmed head evidence and
      a new review gate, not merely a local flag or successful mocked transport.

## Proposal

Extend the existing explicit rewrite/publication path with a reasoned recovery
option. Keep exact target and transport fencing separate from historical
ownership heuristics: the operator can waive the latter deliberately, not the
former accidentally. Final flag spelling and integration follow the current
publisher implementation rather than introduce a second command family.

The confirmation gate is the repository's existing procedural safeguard, not a
claim of cryptographic operator identity. A real invoker-identity authorization
primitive remains the separate #4411 work; this effort does not depend on it or
pretend a CLI flag implements it. Keep the exception low-friction and explicit,
without turning unavailable identity infrastructure into another recovery veto.
Confirmation carries the approved operation's immutable source and remote-head
binding; it does not authorize whatever the recovery worktree happens to contain
at a later invocation.

## Journal

### 2026-10-09 - Scope and coordination
- Captured the operator's request without replacing the source-CWD preference
  with an absolute requirement or omitting the requested escape hatch.
- Deduplicated against #5817 and its active publisher work in PR #5835.
  Recorded the distinct recovery-exception slice on the coordination issue;
  implementation has not started and awaits this plan's review gate.
- Plan review identified that a choice plus reason did not establish sign-off
  and that target-only audit data omitted the invoker. Specified the existing
  no-mutation confirmation pattern and truthful invocation/session attribution,
  without expanding this effort into the deferred identity service.
- Bound confirmation to the previewed source object as well as the remote-head
  lease, covering local source changes between preview and confirmed execution.

### 2026-10-09 - Reviewed contract landed; implementation next
- PR #5991 merged after a current-head approval with zero open findings and
  green required checks. The worktree is reconciled onto the merged contract.
- Recorded the disjoint integration boundary on #5817 and #5990. PR #5835 owns
  explicit publication authority; PR #5994 owns durable backup/replay recovery.
  This effort owns confirmed operator exceptions and local invoker audit, not
  replacement implementations of those mechanisms.
- Begin disjoint confirmation/audit work; integrate the shared publication
  adapters only after peer interfaces are available for coordinated integration.

### 2026-10-09 - Local integration and bounded validation
- Added source-bound confirmation and private invoker audit helpers. Integrated
  the published publisher and backup-recovery dependency branches locally,
  without changing either peer's worktree or remote branch. The publisher
  remains an unmerged dependency; this local integration is not a deployed fix.
- Wired the existing rewrite adapter and `push-changes` CLI with a reasoned
  preview/confirmation path, retaining exact provider head/destination checks,
  saved remote leases, committed source pinning, protected refs, real hooks,
  and durable source/published recovery refs from the shared recovery helper.
- Bounded Windows selections passed: 26 confirmation/audit/CLI/real-transport
  cases and six moved/deleted-head, wrong-provider-head, protected-ref, dirty
  source and real-hook refusal cases. The earlier CLI capture failure was
  corrected and the whole affected selection rerun. Source ceilings pass
  without widening the baseline.
- Implementation remains local and incomplete: final provider-source parsing
  compatibility coverage, authority-race/refusal coverage, documentation and
  independent review/required CI are still owed. The published dependency
  changes must be reconciled before opening a non-competing implementation PR.
  No live recovery override has been executed.

### 2026-10-10 - Acceptance hardening and dependency coordination
- Exercised actual GitHub, Gitea and Azure `get_pull` source parsing through
  transport-only provider fixtures, including fork identity, nullable fields
  and malformed container/type responses. Added typed shared source guards;
  unknown Azure fork identity does not inherit the destination repository.
- Corrected historical-ledger inspection failures so deliberate recovery can
  audit unavailable non-selected ledgers while the selected ledger remains
  mandatory. Added same-PR versus known shared-other-PR destination coverage.
- Added real transport races after observation, immutable local-source pinning,
  pre-push audit refusal, post-push persistence/audit error reporting, explicit
  selected-project invocation from another CWD and frozen public-attribution
  coverage. Successful transport remains recorded as successful even if later
  tracking persistence fails; the command still reports reconciliation needed.
- The first combined bounded Windows selection passed 204 cases. A later
  expanded selection exceeded its 300-second sub-suite budget before reporting
  a fixture failure. A focused run identified an invalid test setup: a normal
  tracking save correctly preserved the already-frozen policy instead of
  replacing it. The fixture now creates the PR with the intended policy before
  recovery. The corrected combined selection passed 209 cases in 456.98 seconds
  within a deliberately bounded 540-second sub-suite and 600-second supervisor;
  Windows containment cleanup was verified. This includes both ordinary
  rewrite head schemes and subsequent incremental publication.
- Updated CLI and worktree guidance with the no-mutation preview, explicit
  per-PR sign-off, source/head-bound confirmation, private observed invoker
  audit, durable recovery refs and separately authorized rollback instructions.
- PR #5835 remains the publisher driver's open dependency. Its latest
  current-head review still reports a previously missed Medium legacy Azure
  state finding. Recorded non-competing coordination on that PR; no peer
  branch was changed, no competing implementation PR was opened and no
  unmerged implementation was installed. Release and live recovery remain
  unresolved.
- Source ceilings, Ruff F/E9, installation contract, documentation consistency,
  runbook references and headless-launch guards pass. The identifier guard had
  no configured denylist and therefore supplied no identifier-scan evidence.
- Kept required guard coverage focused on pure confirmation/audit contracts,
  one real no-mutation/confirmed-retention smoke and six provider-source
  parsing cases. The exhaustive transport race/refusal matrix remains in the
  normal full test portfolio instead of multiplying process startups in the
  required smoke lane.
- Integrated current `dev` and the final merged PR #5994 backup implementation,
  retaining its pre-rebase source fence and exact completed native-transition
  capture alongside the confirmed-rewrite retention helper. Resolved index
  conflicts without dropping concurrent effort rows. The integrated 20-case
  backup/transport selection passed in 378.98 seconds; the focused required
  recovery smoke passed 31 cases in 27.22 seconds. Both verified Windows cleanup.
- A bounded 600-second publisher wait observed no new review/merge transition.
  Its canonical final read reports the unchanged head and an actual merge
  conflict, in addition to the latest review's Medium legacy-state finding.
  The publisher driver still owns that PR; this effort is not released or
  authorized to force it through.

### 2026-10-10 - Authorized dependency recovery; implementation still local
- The operator explicitly authorized isolated takeover of the publisher
  dependency and repository-source CLI bootstrap without installing unmerged
  code. A separate no-mutation preview named PR #5835, its exact destination,
  immutable proposed source and saved remote head. Only after separate approval
  of those exact values did the confirmed command publish the same PR.
- Retained both source and previously published commits and verified the
  private audit records successful transport with observed, not authenticated,
  invocation provenance. Initial provider observation lagged; subsequent Git
  and provider reads agreed on the actual head and mergeability.
- Addressed the persisted native `active` state through consistent live
  classification and ordinary rewrite admission, not a parser-only migration
  that disagreed with the tracking save cache. Six affected Windows cases
  passed after correcting that incomplete first attempt.
- The provider initially reported an obsolete base and an oversized diff.
  An ordinary incremental update on current `dev` corrected it to the
  publisher-only scope. Current-head review then identified real admission
  defects: configured waits, exact selected sibling revalidation, registry
  read/mutate/write serialization and verified empty-provider backfill.
  Corrected those in the dependency PR; 29 admission/deadline and 23
  registry/publication/legacy-rerun cases passed. Real pre-push hooks also
  required shared headless handling for installer provenance probes; the
  correction was validated and published without bypassing hooks.
- Integrated the publisher follow-ups locally without including this effort's
  implementation in that dependency PR. The coupled 35-case admission/recovery
  selection passed with verified Windows cleanup. Removed the already-consumed
  backup dependency changefile with explicit approval, retaining this effort's
  own pending release entry.
- The publisher's fresh review and required gates remain pending. This
  implementation is still unpublished and uninstalled; the authorized
  bootstrap demonstration does not close the released live-recovery validation.

### 2026-10-10 - Independent recovery review and Azure transport compatibility
- Independent review of the recovery-only delta found that Azure's provider
  HTTPS source observation did not match the equivalent supported SSH remote.
  Normalize only that comparison's secure Azure transport layout; preserve
  organization, project, repository, host and non-default-port boundaries and
  leave the saved publication identity and confirmation fingerprint unchanged.
- Added secure-alias and wrong-destination regressions plus actual recovery
  adapter previews proving both acceptance and no-mutation refusal. The combined
  affected selection passed 71 cases with verified Windows containment cleanup;
  Ruff F/E9 passed. Initial regression fixtures incorrectly changed a frozen
  publication policy; they now establish the Azure provider before publication.
- The full two-file selection passed 191 cases and exposed one coupled fixture
  regression: a truthiness fallback replaced explicit false attribution with
  the configured default. Preserve false with an explicit `is not None` check.
  All four attribution/Azure adapter cases then passed with verified cleanup;
  this is targeted correction evidence, not a claim that the failed full run
  passed.
- The original publisher contributor retains the dependency PR. This recovery
  implementation remains local; independent review is not required CI, release,
  rollout or operator consent for a live rewrite.

### 2026-10-11 - Landed publisher integration and bounded recovery fetch
- The publisher dependency merged in PR #5835. Integrated its landed authority
  projection, revision-aware tracking and compatible legacy publication without
  publishing peer commits as a separate contribution. The bounded integration
  selection passed 288 cases; the callback selection passed 86.
- Independent integrated-delta review found an unbounded exact-ref fetch when
  the saved published tip is absent locally. Bound it to the standard fetch
  timeout, require process-tree containment and report timeout through the
  handled failed-audit path before any push. Five affected refusal/audit cases
  passed with verified cleanup after restoring the current source dependency
  API in the cached test environment.
- Required PR CI, reviewed publication, promotion and released live recovery
  remain open.

### 2026-10-12 - Reviewed release and separately authorized live recovery
- PR #6257 merged as `88dbb8f4fd9e9378d9a3613451f77ac22e5fc095` after a
  clean current-head review and passing required CI. Corrected unavailable
  passwd-user attribution, canonical `azure-devops` source comparison and
  ambiguous transport outcomes: exact remote observation distinguishes accepted,
  unchanged and unknown results, with explicit reconciliation rather than
  success-shaped failure or blind retry. The affected selection passed 72 cases.
- Verified normal promotion's critical source blobs against the merged
  implementation, then completed the separately authorized unified Windows
  update after rechecking status. The actual installed `1.24.50-dev1` package
  matches those four recovery/provider/CLI source files; no installed edits
  or unmerged runtime installation occurred.
- A released no-mutation preview identified PR #5567, its exact destination,
  immutable residual source and expected remote head. Only after separate
  approval of that specific preview did confirmed publication proceed. Git and
  provider evidence agree on the proposed source; retained source/published refs
  and private successful-transport audit match the approved commits. The fresh
  review and required CI gate remains open, so released demonstration is not
  declared complete merely because transport succeeded.
- The recovered PR's follow-up corrected the POSIX audit fixture and made the
  pure restart regressions participate in the guard lane. Current-head approval,
  passing required CI and resolved proven fixes preceded PR #5567's actual
  merge as `06762279c8aa92a8a38ccaec61b41ca513c988c6`. The live released
  recovery therefore retained its fresh review/merge gate through completion.
- All Plan and Validation Plan items are resolved. Archive this completed
  recovery-exception effort; compatible publisher work stays with its existing
  driver. Native Linux systemd rollout is not claimed by the Windows recovery
  demonstration or by its structural tests.

### 2026-10-11 - Merged publisher dependency and current development integration
- Integrated current `dev` at `acd5401c42031e9d73aab708a3d607e3f69c5fd1`,
  including the actually merged publisher PR #5835 at
  `2724ce210191de4b66700a4ecd526e0368abaf31`, with the recovery implementation.
  Preserved peer history rather than replaying it as recovery-authored work.
- Resolved twelve conflicted paths against the prior publisher baseline.
  Retained loaded-authority revision compatibility, owning branch/path
  revalidation, installation-wide serialization, ordinary one-command legacy
  reconciliation, and the current callback-worker race proof. Recovery retains
  immutable confirmation, exact provider/source/lease fencing, secure Azure
  source equivalence, private observed-provenance audit and retained commits.
- The five-file Windows recovery/source/authority/interleaving/rewrite selection
  passed all 288 cases in 467.21 seconds under a 600-second supervisor.
  The complete callback-delivery file passed 86 cases in 12.75 seconds under
  a separate 300-second supervisor. Both reported verified containment cleanup.
  Earlier attempts exposed a stale cached vendored process dependency and
  Windows fixture-path length limits; the passing runs used repository vendored
  source and a fresh shorter fixture root, without installing or deploying code.
- Ruff F/E9, installation contract, changed-source ceilings, documentation
  consistency, runbook references, version consistency and headless-launch
  guards passed. No source ceiling was widened by this integration. The direct
  test invocation reports unregistered contract-marker warnings; these are not
  test failures. Linux/macOS and required CI remain unverified here.
- This is local integration only. The recovery changefile remains pending,
  implementation review/publication and release remain unresolved, and no live
  recovery confirmation or remote mutation was performed by this integration.

### 2026-10-11 - Authoring rebase onto current development
- Rebased the recovery-specific commits onto current `dev` at
  `182c5a3a5ba182949e1cc6061306f5cf4c81f592`. Omitted duplicate publisher and
  backup patches already represented by their landed upstream contributions,
  not recovery intent. Preserved current peer source and effort index entries.
- Reconciled the actual recovery authority/rewrite conflicts with current
  legacy publication and revision semantics. Kept the bounded missing-tip fetch,
  failed-audit evidence and secure Azure equivalence unchanged. Restored the
  focused guard-marker selection rather than marking the entire expensive
  recovery portfolio as required smoke coverage.
- The rebased five-file recovery/source/authority/interleaving/rewrite selection
  passed all 289 Windows cases in 515.49 seconds under a 600-second supervisor,
  with verified containment cleanup and the current cached dependency API.
  Ruff F/E9, install contract, changed-source ceilings, documentation consistency
  and runbook references passed. Linux/macOS and required PR CI remain unverified.
- The net contribution remains the recovery-only nineteen-file delta with its
  one pending changefile. Stopped before publication; the parent driver retains
  implementation review, PR publication, release and live consent gates.
