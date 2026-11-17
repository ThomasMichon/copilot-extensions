# Configured Worktree Base Preparation

## Goal

**Kind:** completion. Prepare a new worktree from its explicitly refreshed
configured default branch without fetching unrelated branch refs, while
preserving committed-policy discovery, safe-anchor behavior and honest
offline fallback.

**Acceptance:** a fresh local-Git consumer proves exact configured-base
selection even when the ordinary remote refspec excludes that branch,
unrelated refs remain untouched, and existing preservation/failure controls
pass. The finite source correction clears required typing, tests, review and
CI and actually merges; canonical acceptance and its issue claim are settled.

**Finalization criterion:** every Plan/Validation item below is accepted or
explicitly transferred, the finite issue is resolved and its claim completed,
and the source worktree's independent obligations are preserved. A plan merge
is not implementation completion, and source acceptance is not rollout.

**Now:** republish the qualified rebased correction to the same implementation
PR through its recorded exact-head lease, then obtain genuine final-head review
plus required CI before actual merge. All four corrected threads have causal
proof and confirmed resolution. Negative and positive consumer qualification
are complete; source acceptance remains open. No production authority or
another contributor's seed-delivery files are changed.
**Return:** accept only the finite source scope, then record its actual
source result and return to the contributor's independent remaining work.

- **Slug:** `worktree-base-preparation`
- **Repo:** copilot-extensions
- **Branch(es):** separate managed plan and implementation PRs against `dev`
- **Created:** 2026-11-17
- **Status:** Active
- **Vision:** below-altitude correction consistent with
  [repository/worktree identity](../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity)
- **Umbrella issue:** #13013

## Guiding Intent

The configured creation base should determine this operation's fetch scope.
Reuse the existing explicit-branch fetch owner rather than introducing a
timeout knob, retry mechanism, service or second freshness policy.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| `patient-knob` | Finite planning, source delivery and publication owner | Assigned managed source worktree and #13013 |

## Coordination

- **Topology:** one owner, one managed source worktree, sequential plan/code PRs.
- **Host (owns PRs):** `patient-knob`; original issue/claim publisher ThomasMichon.
- **Delegates:** none.
- **Handoff:** preserve this finite gate and claim through native continuation;
  no independent contributor's work transfers through this plan.
- The [discussion-only reservation](https://github.com/ThomasMichon/copilot-extensions/issues/13013#issuecomment-6937621536)
  remains active until its original publisher records completion or release.
  No native queue reservation is claimed.

## Context

`worktree_creation._prepare_worktree_source` delegates to
`git_ops.prepare_worktree_base`. The latter currently calls `fetch` without
the configured default branch, although `fetch(branch=...)` already supports
an explicit remote-tracking refspec that overrides an excluding ordinary
remote fetch configuration.

#4093 accepted reporting failed-fetch/last-known freshness while retaining
offline creation. Do not silently replace that minimum-warning alternative
with a new fail-closed policy. #4303 concerns an existing requested branch's
resume, not new default-base preparation.

The current contributor changes in #7178 concern failed-create branch cleanup
and a start-point docstring. #11828 changes encoded URL resolution and installer
recovery. Their inspected runtime hunks do not change this fetch call.
Preserve their implementation and shared tests; reconcile actual drift before
publication rather than pre-patching or replacing either PR.

## Request

Agent-derived finite request, captured publicly in #13013:

> Refresh the configured remote/default branch explicitly for new-worktree base preparation. Select that fetched head through the existing start-point owner without refreshing unrelated branch refs merely for this operation.
>
> Preserve the accepted warning/offline fallback from #4093, safe eligible-anchor advancement, local-work protection, authentication and the existing network deadline. No new CLI, schema, timeout policy or dependency is needed.

## Scope Approvals

Source-only planning, focused synthetic validation and the normal contribution
flow are in scope. This plan grants no installed rollout, service intervention,
live repository experiment, cleanup, credential change or other-owner takeover.
Existing-branch resume, finalize/GC fetch policy and cross-machine allocation
custody remain independent.

## Plan

### Phase 1 - Reviewed contract

- [x] Land this finite proposal through the repository's actual review/CI gate.
- [x] Reconcile the current default-base helper and independent contributor
  hunks without reopening their scopes or changing original acceptance.

### Phase 2 - Source delivery and finite acceptance

- [x] Add a few fail-first, fully typed controls using disposable local Git.
- [x] Explicitly refresh the configured creation base and its related policy
  bootstrap through the existing fetch implementation; preserve all subsequent
  start-point and safe-anchor decisions.
- [x] Satisfy whole-file strict changed-code checks without suppressions or
  pending-project admission. Limit necessary typing corrections to unchanged
  behavior; reconcile any material contributor overlap before proceeding.
- [x] Resolve the initial review's actual refmap, policy-discovery,
  fixture-isolation and moved-source-closure findings. Prove the new behavioral
  negatives before revising the fetch, then repeat impacted validation.
- [x] Resolve the subsequent policy-head, source-namespace, independent-ref
  failure and stale-declared-hop findings with real-Git regressions.
- [x] Resolve the later deadline/authentication, unavailable-policy-probe,
  aliased-tracking-source and truthful caller-warning findings.
- [ ] Pass affected tests, lint, install contract, changefile/version/size and
  exact-head required review/CI, then actually merge the source correction.
- [ ] Record the finite accepted result, settle #13013 and its original claim,
  and preserve every independent workspace and deployment obligation.

## Validation Plan

- [x] **Exact base, fail first:** use a nonstandard configured default branch
  omitted by the ordinary fetch refspec; advance it remotely and prove the real
  helper refreshes/selects its exact head.
- [x] **Ref scope:** advance an unrelated branch too and prove this operation
  does not refresh its remote-tracking ref, including alternate and mirror
  configured mappings.
- [x] **Policy discovery:** the actual offline committed-config reader sees a
  changed bootstrap declaration and the following preparation selects that
  declared branch, without refreshing unrelated refs.
- [x] **Preservation:** retain eligible-anchor fast-forward and existing
  dirty/ahead/diverged/detached/non-default/no-remote controls.
- [x] **Offline reporting:** the actual preparation caller reports the literal
  fetch failure and selected last-known base, preserving the existing fallback.
- [x] **Fresh consumer:** local bare repositories and isolated Git configuration
  exercise actual subprocess/ref behavior without network credentials, operator
  state, installed runtimes or product checkouts.
- [x] **Policy edge cases:** refresh the configured base with a non-symbolic
  optional HEAD; refresh surviving refs in either deletion direction; align a
  non-origin creation remote with the reader's policy namespace; refresh a
  newly declared but already tracked policy source before consuming it.
- [x] **Remaining boundaries:** delayed authentication cannot launch a fetch
  after the shared deadline; unavailable policy probes are not certified
  absent; aliased policy tracking refs retain their configured sources; the
  actual caller reports a refreshed base accurately after policy-only failure.
- [ ] **Publication:** whole-file typing and all applicable exact-source gates
  pass; genuine final-head review and actual merge precede acceptance.
- [x] **Evidence tiers:** document why the local-Git consumer is the relevant
  end-to-end/first-touch proof; use genuinely available supported lanes without
  treating source/CI evidence as installed or live-venue qualification.

## Proposal

Share the existing fetch implementation with `fetch(branch=...)` and its
default-base preparation caller. Keep the network bound and literal failure
path unchanged. Introduce no public API or cross-runtime contract. The
architecture-pattern principles of right-sized surfaces, cross-platform parity
and truthful failure apply; this below-altitude correction does not revise a
vision or create a new service/lifecycle owner.

### Review refinement - required policy refs are not unrelated refs

The initial implementation review exposed two incomplete assumptions. An
explicit refspec can still apply matching configured refmaps, and the offline
committed-config reader starts from the recorded remote-HEAD branch before
hopping to a declared contribution branch. Narrowing creation to that
contribution branch alone can leave a later policy declaration unseen.

Suppress configured refmaps for explicit refreshes. Prepare the configured
creation base and, when distinct and present, the existing reader's recorded
remote-HEAD policy branch in one explicit-refspec fetch under the same
authentication and network deadline. The policy bootstrap ref is a related
input, not permission to refresh all ordinary refspecs. Reuse the existing
offline branch reader and fetch implementation rather than change policy
precedence, add network discovery, duplicate authentication or add a public
API. Preserve warning/last-known fallback when preparation fails.

Focused controls must cover alternate tracking destinations, a mirror mapping
to an unchecked-out local branch, and a policy declaration changing from
`dev` to `stable` while `dev` remains unchanged. Keep Git templates fixture-owned
and assertions locale-independent. This refinement remains subject to the
implementation PR's genuine review and required CI; the initial plan approval
does not claim these newly exposed cases already passed.

### Subsequent refinement - independent required refs

The later review supersedes the one-fetch assumption, not the related-ref
boundary. Attempt each required source/destination ref once within one shared
existing fetch deadline, continuing surviving inputs after a failure and
retaining the first literal error for the accepted fallback.

The configured creation remote keeps its own tracking namespace. Committed
policy still uses the reader's `origin` namespace: fetch from `origin` when
configured, otherwise from the configured creation remote into that namespace.
Refresh the recorded bootstrap and its first declared policy branch, without
following additional declarations or invoking a broad configured refmap.
Keep the reader offline and its candidate precedence unchanged; capture its
bootstrap parse and refresh the declared source before the caller's next
ordinary policy read. An optional non-symbolic HEAD cannot block base refresh.

The following review requires carrying that deadline through the existing
remote/authentication subprocesses and recomputing the remaining timeout at
launch. Related policy sources must be resolved from their positive configured
tracking mappings without re-enabling unrelated refmaps. Preserve unknown
versus confirmed-absent policy observations. Keep the result model's existing
field layout, but distinguish whether the creation base refreshed from a
supplemental policy failure so the actual caller does not label a fresh base
last-known. The existing base-failure warning stays byte-for-byte compatible.
Any necessary caller edit owes the same whole-file strict ratchet, not a
typing exemption or pending-project admission.

## Journal

### 2026-11-17 - Initial review and expanded fail-first controls

- Review 5933811209 against the initial implementation head reports five
  blocking findings. The complete review and all inline comments were read.
  The moved state module must join the actor-free source closure in addition
  to updating the changed original source digest; current CI confirms that
  missing import explicitly. Preserve exact test membership and budgets.
- Expanded typed local-Git controls are staged against unchanged runtime
  behavior. Local preparation again failed before collection, so no expanded
  behavioral result is claimed. Use the supported disposable Windows source
  transport for these negatives rather than alter host tools or containment.
- That supported negative run then exercised exact test-only candidate
  `7114f4797f665998f9fa9db045f9049c889be2f4` in
  [run 44031576866](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44031576866).
  All six cases executed: the original three passed, while alternate tracking,
  mirror-local-head protection and policy `dev` to `stable` discovery failed
  at their intended behavior assertions. This is measured fail-first evidence,
  not a setup failure or a qualified positive result.
- Only afterward, factored the existing authenticated/deadline-bound fetch
  body into a private shared owner. Explicit refreshes use `--refmap=`;
  preparation adds the distinct recorded policy-bootstrap branch to the same
  fetch. The public single-branch fetch interface, unscoped fetch behavior,
  offline reader precedence and safe-anchor decisions remain unchanged.
- Bound the moved declaration-only module in the existing unit source closure
  and refreshed the changed original digest, without adding unit nodes or
  coverage targets or changing budgets. Positive qualification and genuine
  final-head review/CI remain required.
- The complete selected six-file packet then qualified source
  `134f7afcdaa7dfce76a7532d2a61f1e6bcccbe21` in disposable Windows
  [run 44037449068](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44037449068):
  **96 passed, zero failed or skipped**, including all six preparation cases,
  binary/text consumers and prior preservation controls. Source/selection,
  canonical test and runtime-verification phases all passed. Subsequent
  journal-only updates do not change those tested runtime/test bytes.
  Final-head review, required CI, actual merge and finite acceptance remain open.

### 2026-11-17 - Policy refresh edge cases

- Review 5934856436 against `b6a4adb4545789a07fdb7cbe12c887e4ddbd7467`
  recognizes all six prior threads as resolved, but reports four new Medium
  findings. Required CI is green; the review gate is not satisfied.
- Added focused real-Git negatives for direct/non-symbolic optional remote
  HEAD, deleted bootstrap, deleted configured base, a non-origin creation
  remote whose policy tracking namespace remains `origin`, and an already
  tracked but stale newly declared policy source. Runtime was unchanged;
  [run 44045519307](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44045519307)
  executed all11 cases: the original six passed and four new cases failed
  at their intended behavior assertions. The non-origin case instead selected
  its removed remote because of a fixture mistake; that is not behavioral
  proof. Corrected its selector and submitted only that case again before
  changing the runtime.
- Refine the previous one-fetch assumption: related refs must be independently
  refreshable when one has disappeared. Attempt each required source/destination
  ref once within one shared existing fetch deadline, preserve the first
  literal failure, and still refresh surviving related inputs. This is not a
  retry policy or permission for an ordinary broad fetch.
- Keep the committed reader's existing `origin` source, candidate precedence
  and single policy hop. Reuse its parser/reader to refresh that declared hop
  before consuming an already tracked stale copy; do not duplicate policy
  resolution or discover remote HEAD over the network. A non-symbolic optional
  bootstrap must not prevent the configured base's refresh.
- The corrected selector reached a policy assertion, but that assertion
  incorrectly expected bootstrap metadata after the reader's final hop.
  Corrected it to inspect the actual bootstrap Git blob. Only then did
  [run 44048586294](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44048586294)
  prove the non-origin namespace regression at source
  `5f7d60c7d980cc941917728011d83cb0251dc23e`: the new bootstrap marker was
  absent from `origin/trunk`. Neither fixture mistake is counted as a valid
  negative. Together with the four earlier negatives, all new cases now have
  meaningful fail-first evidence.
- Implemented the narrow refresh owner only after those negatives. It shares
  the existing authenticated explicit fetch, deduplicates required refs,
  shares the existing deadline, and preserves failures without blocking
  surviving refs. The reader remains unchanged and offline. Whole-file strict
  checking passes all four changed Python files on Linux and Windows/Python3.10.
  Positive consumer qualification and exact-source review/CI remain required.
- Disposable Windows
  [run 44051416434](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44051416434)
  then qualified exact source `177e4b72a27511a156577d7d3805991b1b44b09a`:
  **101 passed, zero failed or skipped**, including all11 preparation cases
  and the binary/text, committed-reader and safe-anchor preservation packet.
  Source/selection, canonical tests and runtime verification all passed.
  This journal update changes no qualified runtime/test inputs. Final-head
  required review/CI and actual merge remain mandatory.

### 2026-11-17 - Remaining deadline and diagnostic boundaries

- Genuine final-head review5936165877 at
  `0dd24b860b6383ba401e68e240f943ea40d006f8` recognizes the prior four findings
  as resolved, but reports four new Medium findings: authentication can consume
  a previously computed fetch timeout, unavailable policy reads can masquerade
  as absent, recorded HEAD tracking names can alias a different source branch,
  and a policy-only failure mislabels the refreshed base as last-known.
  The complete review and all four inline comments were read.
- All actual source CI is green. The earlier reviewer run was cancelled during
  request processing; a normal explicit re-request produced the current genuine
  verdict. Neither the old cancellation nor green CI authorizes ignoring its
  findings. No source merge or finite acceptance is claimed.
- Added focused negative controls for delayed authentication on a distinct
  policy remote, timed-out HEAD/bootstrap probes, an aliased recorded HEAD
  with a real decoy branch, and the actual caller's deleted-policy warning.
  Runtime remains unchanged pending meaningful fail-first results.
- Those controls pass whole-file strict typing and lint and are retained at
  immutable source `9796a1ab84` against unchanged production behavior. The
  qualification client's read-only preflight then timed out before creating
  a request: twice on repository metadata and once on owner identity.
  An explicit native project selector made the metadata read work, but did
  not clear the later identity timeout. No request or behavioral result is
  claimed, no timeout/containment was widened, and no host repair was attempted.
  Continue local source work while preserving that negative source; actual
  negative and positive qualification still precede publication of the next
  runtime correction. A preflight failure is not a test result.

### 2026-11-17 - Remaining boundaries reproduced and corrected locally

- The same pinned negative source `9796a1ab84151faf8303dbed268e0e34c4aeb466`
  reached the disposable Windows consumer in
  [run 44074796247](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44074796247).
  It produced the expected five behavior failures and ten passing controls:
  delayed authentication launched a late policy fetch, aliased policy HEAD
  consumed the decoy branch, unavailable HEAD/bootstrap reads hid degradation,
  and a policy-only failure marked the fresh base unfetched. No skip or
  preparation failure is counted as a behavioral negative.
- The native result lookup initially timed out. A scoped read subsequently
  confirmed the same request completed; the original qualification client
  then validated its bound result. No request was replayed, journal rewritten,
  timeout widened or containment relaxed.
- Local corrections share one transient absolute deadline through Git and
  credential acquisition, recomputing its remainder at each child launch.
  Preserve the selected base's successful refresh separately from policy
  failures. Use the existing committed batch/parser observations to report
  unavailable policy, and invert only the selected tracking destination's
  configured source mapping. Ambiguous mappings or collisions cannot overwrite
  the refreshed creation base.
- The actual caller now distinguishes policy degradation with a refreshed base
  from the unchanged literal base-fetch/last-known warning. Necessary whole-file
  typing retains its live module/facade replacement seams and omitted optional
  keywords; no project admission or suppression is introduced. Neither #7178
  nor #11828 has a current hunk in this caller file.
- Positive controls now also cover wildcard aliases, valid source-only fetch
  mappings, ambiguity and a collision with the creation base. Strict checking
  has passed all eight changed Python files on Linux and Windows/Python3.10;
  final exact-source gates and the expanded positive consumer packet remain
  pending. The implementation is not yet republished or accepted.

### 2026-11-17 - Expanded caller qualification exposes fixture drift

- Candidate `525298862a91ce4e8efd21acfa37eccced9221db` is not qualified:
  [run 44077125552](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44077125552)
  reports147 passed,15 failed and three existing opt-in native skips.
  All19 preparation cases passed, including every reproduced boundary and
  added mapping control. The creation files expose receiving-ledger mismatches
  and consequent missing-seed assertions; their failures are not waived.
- `test_worktree_creation_seed.py` and three core cases in
  `test_creation_requests.py` selected an arbitrary tracking directory rather
  than the named receiving project's ledger. The seed fixture also omitted
  its declared worktree root/platform from saved configuration. Correct those
  disposable inputs, retain the actual guards and all failing assertions, and
  meet the complete touched-file strict ratchet.
- Existing contributor #12880 owns the different resolver/native seed-delivery
  fixture files under #12879. Its full body and file inventory were read.
  Neither of those files, its shared imports, native acceptance nor its
  expression is changed or superseded here. Exact creation-file ownership
  searches found no competing correction for these two files.
- The two creation fixture files now pass whole-file strict checks and fatal
  lint; all ten changed Python files pass both Python3.10 target platforms.
  Canonical same-object module imports, typed callbacks/documents and positive
  optional-receipt assertions preserve the existing test contracts.
- The three opt-in fresh-owner native cases require their independent
  enrollment and were already skipped, not failed. They are outside the
  intended non-native caller-preservation selection; qualify the explicit
  non-native packet without dropping any of the15 failures or claiming those
  native contracts ran. Source consumer proof, exact-head review/CI and actual
  merge remain open.

### 2026-11-17 - Expanded caller packet qualifies

- [Run 44081145187](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44081145187)
  qualifies exact source `07089125344a35ed0b7a49dae9139536a9cbd423`:
  **162 passed, zero failed/skipped**, with source/selection, canonical tests
  and runtime verification all passed. This includes all19 preparation cases,
  30 keyed-creation controls and23 creation-seed controls. Every previously
  failing creation case remains selected and now passes.
- The three opt-in fresh-owner native cases remain unchanged and outside this
  explicit non-native packet; no native enrollment or acceptance is invented.
  Their literal inputs, decorators and assertions are preserved.
- Self-review then identified a valid Git fetch mapping whose source is the
  short name `production`, not only `refs/heads/production`. Retain that native
  source syntax and add a real alias regression instead of rejecting an
  otherwise valid mapping. Empty sources, ambiguous mappings and collisions
  retain their existing error/preservation behavior. That final compatibility
  refinement still requires exact-source positive qualification before the
  pending PR update.

### 2026-11-17 - Final compatibility candidate qualifies

- [Run 44083131020](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44083131020)
  qualifies exact source `eb34a1fc53c9676b0cd81035b57fc23b33b57ff1`:
  **163 passed, zero failed/skipped**, all three phases passed. All20 preparation
  cases include the actual short-source alias, delayed authentication,
  unavailable reads and truthful caller-warning controls; all53 selected
  creation controls still pass.
- All ten changed Python files pass whole-file strict checks on Linux and
  Windows/Python3.10, with fatal lint,821 source bindings and unchanged exact
  unit nodes/coverage/budgets, installation contract, version and effort
  structure checks. The final size gate caught two excess caller lines;
  removing only redundant comments restores1000 lines with its AST unchanged.
  The original Git module remains under its1747-line ceiling.
- This journal changes no qualified runtime or test input. Publish through the
  existing managed PR, preserve source attribution and verify tested blobs
  through reconciliation. Fresh final-head review, required CI, actual merge
  and finite claim/issue acceptance remain open.

### 2026-11-17 - Rebased integration qualifies

- The qualified correction was published at `fabc9a855a`; all four corrected
  threads received specific causal-proof replies and confirmed resolution.
  Complete head/body/source/label/closing-reference readback passed. Own inline
  reply reviews are not a genuine Copilot verdict.
- The provider then reported a conflict with current `dev`. Managed sync
  auto-aborted safely. With the original tip retained, the owned private PR
  history was reduced to one commit and rebased onto pinned
  `b97e7f8318988b90830b8fcecb6cbf2cb1c2de61`. The only conflict was an effort-index
  adjacency: preserve the other effort's current status and reconcile this
  effort's row. No runtime/test conflict or other-owner source was changed.
- All 13 qualified code/test/doc/reader/config blobs remain identical. The
  automatically merged manifest verifies 832 bindings, retaining the pinned
  upstream exact nodes, coverage and budgets. All ten whole Python files pass
  strict checking on both Python 3.10 targets against that pinned base.
- Because upstream also changed shared process code, repeat the actual
  integrated packet rather than treating unchanged authored blobs as proof.
  [Run 44086365363](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44086365363)
  qualifies exact `dae69ef649b72a109501afd241802e14d8173a93`:
  **163 passed, zero failed/skipped**, all three phases passed, including all
  20 preparation cases.
- This entry changes only the result journal. Republish through the normal
  managed `--rewrite-pr` exact-head lease, not a raw force-push or break-glass.
  Refresh the live description and exact-head review/CI before merge.

### 2026-11-17 - Finite correction validated locally

- Implementation #13033 is open, not merged. The managed publication rebased
  the candidate onto current `dev`; all seven authored files survived
  byte-for-byte unchanged.
- Disposable hosted Windows qualification passed all selected cases at
  `8b5d6154493783062a4a04cfd19812a7495964a5` in
  [run 44027139733](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44027139733).
  This includes the new preparation cases, safe-anchor controls, the actual
  binary/text Git consumer and binary-observation refusals. The source,
  canonical test and runtime-verification phases all passed.
- Required CI then correctly refused the changed `git_ops.py` source against
  its old actor-free packet digest. Rebound only that existing source entry:
  the original verifier passes all 810 source bindings, with exact node
  membership, coverage targets and budgets unchanged. No source or project
  admission was widened. Required CI must execute the corrected packet.
- The new local-Git controls failed first on unchanged production code:
  the excluded configured base remained stale, and an absent tracking base
  selected the local fallback. The actual caller's offline-warning control
  passed. The correction passes `branch=default_branch` to the existing owner.
- The focused contained packet passed 52 cases, including all three new
  cases plus selected wrapper, classification and anchor-preservation controls.
  Whole-file strict checking passed all three changed Python files for both
  Linux and Windows targets. Lint, install contract and version consistency
  passed; a patch changefile is present.
- Required typing exposed the existing wrapper's text/binary union and
  dynamically assembled options. Precise overloads, typed option dictionaries
  and equivalent explicit I/O branches retain those behaviors. Unchanged
  state/result models moved to `git_state_types.py` with explicit `git_ops`
  re-exports, keeping the original module below its unchanged size ceiling.
- Fresh disposable bare repositories exercise the actual native Git subprocess
  and ref contract, providing the focused end-to-end and first-touch evidence.
  No installer/provisioning behavior changed, so a fresh-OS install scenario is
  not applicable; no installed runtime or product-venue outcome is claimed.
- Initial local preparation on Windows ARM64 could not build the optional
  authentication dependency without a Rust toolchain. The existing x64
  interpreter completed the contained packet without changing host setup.
  A supplementary local binary-consumer packet then stopped before collection
  on a cached-build-file access error. The supported disposable hosted Windows
  lane supplied that source coverage instead; setup failure is not counted as
  a test pass and containment was not relaxed.
- Source publication and hosted qualification are complete. Review, required
  CI, actual merge
  and finite issue/claim settlement remain open.

### 2026-11-17 - Plan accepted

- #13017 actually merged at04:51:42 UTC as
  `956c703a73192bbbeb8b38053f240e727f8836e0`, after genuine current-head
  Copilot approval with no findings and all actual required checks passed.
- The index-only base conflict preserved both independent effort entries.
  The plan body and selected implementation files were unchanged through
  reconciliation. #13013 stays Open for implementation and finite acceptance.
- The retained source workspace synchronized onto the accepted plan. Begin
  the real local-Git regression controls; no installed operation is admitted.

### 2026-11-17 - Proposed finite correction

- Read the current creation helper/caller, #4093/#4303 discussions and actual
  independent contributor hunks. Public #13013 and its finite reservation are
  published and audited. No implementation or installed operation has begun.
