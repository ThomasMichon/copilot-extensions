# Workspace Acquisition Continuity

- **Slug:** `workspace-acquisition-continuity`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase isolated worktrees and PRs to `dev`
- **Created:** 2026-10-12
- **Status:** Active
- **Vision:** worktrees `replay-safe-workspace-allocation`, bridge workspace
  allocation/embodiment, dispatch execution continuity and bounded refusal.
- **Umbrella issue:** #6440
- **Related issues:** #6076, #5511, #6239, #6366

## Guiding Intent

Resolve a worker's execution workspace once and retain that repository/worktree
identity through allocation, session acquisition, retry, recovery, and cleanup.
Ground-layer creation allocates space only. Bridge composes that allocation
with embodiment, including the first conversation in a positively sessionless
existing worktree. Stable logical creation identity prevents transport retries
from allocating another workspace or session.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation owner | Proposal, targeted fixes, validation, and PR stewardship | Source worktree codename `rusty-relay` |
| Runtime fix worker | Preserved Phase 2 implementation; failed before final handback | Bounded task worker `workspace-target-fix` |

## Coordination

- **Topology:** serial per-phase PRs; one implementation owner.
- **Host (owns PRs):** Implementation owner.
- **Delegates:** The runtime worker's source ownership is closed. The
  implementation owner continues its preserved changes, remaining validation,
  Phase 3, integration, and publication directly.
- **Handoff:** retain this reviewed plan, issue claim, current slice, and pending
  completion gate; never treat an opened PR as completion.
- Preserve separately owned acquisition, legacy-recovery, producer-ownership,
  and performance work. #5511 supplies the related persisted-project recovery
  requirement; coordinate rather than introduce a competing authority.

## Context

Headless allocation can select the registered worker agent's project while
acquisition independently selects the producing task lane. A deterministic
worktree lookup refusal can then be classified as an ambiguous/busy deferral,
outside the failure budget. A task without an exclusive reuse key can allocate
another worktree on each attempt.

Existing bridge acquisition journals and native-holder gates remain the
session authority. #6076 established that boundary. #6239 owns historical
first-conversation recovery, #6366 owns exact-record preflight performance,
and PR #6391 owns general disposable-producer obligations. This effort extends
those owners rather than replacing them.

## Request

> Great. Help correct or update any vision or arch docs, then work on targeted fixes

Settled operator contract, summarized with repository-specific examples
abstracted: worktree creation and bridge creation share one target workspace;
worktree creation starts no session, bridge creation composes allocation and
embodiment, and an existing sessionless worktree is an ordinary embodiment
input. Dispatch must never split execution repositories between those calls.
A caller creation key binds to the returned allocation/session rather than
becoming the worktree ID; retries return that result and logical revision
distinguishes deliberate replacement.

## Plan

### Phase 1 - Review intent and contract

- [x] Reconcile the three owning visions and shared architecture contract,
  keeping should-be intent separate from implemented creation interfaces.
- [x] Land this proposal through automated review before runtime execution.

### Phase 2 - Correct execution-target and refusal handling

- [x] Carry the resolved execution project and exact worktree/path from
  preparation to acquisition; reject incompatible owner-returned identity.
- [x] Preserve that identity for retries and restart recovery, coordinating
  the persisted-project requirement in #5511.
- [x] Classify positively proven pre-effect refusals as bounded failures while
  retaining ambiguous launch outcomes for same-allocation reconciliation.
- [x] Preserve native holder, mode, retirement, and first-conversation gates.

### Phase 3 - Replay-safe creation

- [x] Add optional caller creation identity through the existing worktree and
  bridge creation boundaries, with one owner of allocation facts.
- [x] Bind the key and effective target facts before effects; replay the
  original result or report explicit pending/recovery, conflict, or terminal
  state. Keep the key distinct from generated worktree/session IDs.
- [x] Derive a stable dispatch allocation identity from task deduplication
  identity (durable task ID when absent) and explicit logical revision,
  independently of reservation attempt and ordinary claim generation.
- [x] Keep deliberate unkeyed creation and separately owned producer
  obligations compatible; version safety-affecting wire behavior explicitly.

### Phase 4 - Validate, land, and close ownership

- [ ] Resolve targeted regression failures and update the reality docs to the
  implemented contract. Add per-plugin changefiles and steward PRs to merge.
- [ ] Record release/rollout state accurately; automatic registrars stay
  disabled unless separately authorized.
- [ ] Resolve every Plan/Validation item or explicitly transfer any remaining
  obligation to a named tracked objective, then archive and release this effort.

## Validation Plan

- [x] Connected queue-project versus execution-project regression proves one
  workspace from allocation through bridge/native acquisition.
- [x] Deterministic pre-effect refusal spends the bounded failure budget;
  an unknown effect retains the same allocation and does not create again.
- [x] Repeated and concurrent keyed creation, lost responses, and interrupted
  effects preserve exactly one allocated workspace and native conversation.
- [x] Changed facts and terminal-key replay refuse; deliberate logical revision
  permits a separately authorized allocation.
- [x] Existing positively sessionless worktree admits its first conversation;
  foreign/live holders, missing legacy proof, and uncertain launch still refuse.
- [ ] Existing unkeyed creation, CLI/ACP mode, task/lane authority, retirement,
  source-control ownership, and installation-cell isolation stay compatible.
- [x] Native Windows and portable contained tests exercise real CLI/process
  boundaries; clean-room first-touch checks cover new creation persistence.
- [x] Unit, simple end-to-end, clean-room, and available live-venue evidence
  are recorded with explicit reasons for skipped tiers.

## Proposal

Submit the vision, pattern, architecture correction, and effort before runtime
changes. Existing per-delivery bridge idempotency is reused, not mistaken for
cross-attempt workspace allocation identity. No timeout increase, forced
takeover, blanket freshness authorization, or parallel session owner is proposed.

## Journal

### 2026-10-12 - Proposal

- Operator confirmed the effort slug and docs-before-fixes sequencing.
- Public coordination was reserved under #6440 after checking adjacent
  acquisition, allocation, legacy-recovery, and producer-ownership work.
- Request capture retains the operator's scope; concrete downstream examples
  are abstracted without changing the single-workspace or stable-key contract.

### 2026-10-12 - Reviewed intent landed

- Proposal #6445 merged with a current-head clean Copilot Comment and all
  required CI green. The review was Lite; its full agentic runner did not
  start, so no full-agentic-review claim is made.
- Phase 2 proceeds under one exclusive runtime edit owner while the coordinator
  retains integration and the later stable creation-key contract.

### 2026-10-12 - Phase 2 execution identity trace

- The ground owner already returns `worktree.repo`, ID, and path. Dispatch
  currently drops the repository in create/resolve, prepared snapshots, and
  reservation persistence; factories and one-shot acquisition then rederive it.
- Phase 2 retains that owner-qualified tuple through supervised, one-shot, and
  interactive callers. Reservation project/path survive retries and restart;
  absence probes use persisted provenance, never current routing. Missing or
  conflicting provenance holds the allocation rather than authorizing create.
- Bridge's exact current refused/prepared journal receipt can prove a pre-effect
  refusal. Launching, historical, busy, and pending outcomes remain ambiguous.
  Creation keys and new allocation revisions remain Phase 3.

### 2026-10-12 - Phase 2 contained validation blocked

- Added synthetic owner-project, factory request, and unkeyed persisted retry
  regressions in `test_workspace_continuity.py`; staged only that new test so
  the supported runner includes it. No runtime implementation changed.
- Command: `python tools\run-plugin-tests.py agent-dispatch -k
  workspace_continuity --max-processes 32 --max-memory-mb 2048 --max-temp-mb 512`.
- The runner failed during dependency preparation, before collecting tests:
  the manifest requires `fastapi>=0.143.0`, but the configured resolver reports
  only `fastapi<=0.142.2` available. No regression pass/fail is established.
- Dependency manifests and runner repair are outside this worker's edit
  ownership. Integration owner must restore supported contained validation
  before the runtime slice proceeds; no raw pytest or dependency substitution
  was used. Phase 2 remains unresolved.

### 2026-10-12 - Dependency floor compatibility repair

- The supported runner inherits uv configuration during preparation; it
  supplies no index override or constraints. Its configured default feed has
  no age/override constraint and currently exposes FastAPI through 0.142.2.
  No feed URL, authentication, or machine configuration is recorded here.
- The 0.143.0 floor came from dependency-only #6362, with no accompanying
  runtime feature change. Dispatch uses established FastAPI routing, response,
  security, and validation APIs. Restored the preceding `>=0.141.1` floor,
  which still permits 0.143.0 and later as they become available.
- This source-only correction is authorized by the integration owner; plugin
  release versions, user settings, and feed selection remain unchanged.
  Contained coordinator/API tests will supply compatibility evidence.

### 2026-10-12 - Phase 2 target and refusal milestone

- The four initial continuity regressions genuinely failed after supported
  dependency preparation was restored. They now pass with the runtime change.
- Owner-returned project/path flow through all three local callers; retries
  without exclusive keys retain that tuple. Missing/incompatible provenance
  records or holds the workspace and cannot authorize another allocation.
  Recovery absence probes use only persisted project, not changed routing.
- Acquisition request v2 and bridge protocol 33 fence the additional workspace
  facts. Legacy request digests remain stable; version-1 recovery, native holder,
  retirement, and first-conversation policy remain under their existing owners.
- Exact deterministic refused/prepared receipts classify as bounded failures;
  unavailable-owner, busy, pending, and launching outcomes remain ambiguous.
  Neither path treats a failure as permission to remove/recreate the workspace.
- Contained adapter/connected selection: 117 passed. Bridge acquisition/protocol
  selection: 248 passed, one platform skip. Dependent queue/supervisor and
  coordinator regression reconciliation remains in progress.

### 2026-10-12 - Ground creation replay milestone

- Added optional `create --creation-key` through the ground allocation owner.
  A project/installation-qualified SQLite journal commits intent before effects
  and the original result before returning. The key never becomes the worktree
  ID. Attempt/reservation and parent-session changes do not revise allocation.
- Exact completed replay verifies the owner record, path, branch, and creation
  nonce. Changed facts conflict. Pending/interrupted effects retain the reserved
  identity and refuse repeat-create. Observed terminal history stays terminal
  even if an old tracking record reappears.
- Keyed allocation returns space-only versioned metadata, never a session
  launch plan; unkeyed creation and existing ownership/seed behavior remain.
- Contained owner/seed compatibility selection: 40 passed. After additional
  terminal and native concurrency hardening, focused replay selection:
  17 passed. The native fresh-owner fixture exercises the actual create
  parser/handler, Git allocation, tracking writer, and durable journal across
  concurrent and restarted processes, asserting one directory, branch, and
  tracking record and zero conversations.
- This is native process/real-Git evidence, not a published-binstub rollout or a
  full disposable-host rig claim. Bridge and dispatch creation-key integration,
  connected native-conversation replay, and publication remain open.

### 2026-10-12 - Runtime worker recovery

- The runtime worker ended with a tool/model transport failure before final
  handback. Its source edits and validation milestones were preserved; no
  completion or publication is inferred from that failure.
- The implementation owner stopped delegated supervision and took over
  directly, without starting a competing worker or implementation.
- The last broad dispatch run had two contradictory spawn fixtures: each
  reported a different worktree after preparation or retained allocation.
  Corrected those fixtures to return the exact prepared/retained worktree,
  preserving the new identity guard rather than weakening it.
- Targeted pair: 2 passed. Recovered continuity, acquisition, embody,
  reservation, supervisor, and coordinator selection: 915 passed, 2 platform
  skips. Bridge acquisition/protocol milestone remains 248 passed with one
  platform skip. Stable creation-key integration and publication remain open.

### 2026-10-13 - Integrated creation and continuity

- Dispatch now persists the selected execution project before ground effects.
  Its stable key includes durable queue/lane/logical-task identity and explicit
  allocation revision. Lost responses retain the original allocation
  reservation/attempt provenance; claim generation and retry do not revise it.
  Explicit revision requests use a distinct endpoint so older coordinators
  cannot silently ignore the field. Replacement requires positive disposal,
  not priming or a pending cleanup. Independent logical requests retain their
  existing exclusive-lane behavior without inheriting a cleaning allocation.
- Local keyed bridge creation composes the ground journal with one durable
  native embodiment and optional first-turn admission, without fabricating
  dispatch authority. Existing positive-sessionless worktrees are supported.
  Protocol 34 and a separate endpoint fence downlevel capability; SSH/provider
  and CLI-mode keyed venues refuse explicitly. Completed replay does not
  rebind or redeliver, missing completed-turn proof refuses, and established
  cold recovery resumes strictly with native recreation disabled.
- Connected native-owner tests exercise real create/list/head/bind CLI
  subprocesses, Git allocation, tracking, SQLite and SessionManager. A real
  queue with a different execution project loses its allocation response,
  restarts and changes routing, then retains one checkout and native identity.
  The same fresh-owner fixture covers direct ground-to-bridge composition.
  ACP/provider I/O is simulated; these are not deployed-binstub or live-model
  transcript claims.
- Independent pre-submit review found two replacement-revision bugs: carrying
  an already-disposed older revision after a lost new response, and rejecting
  the supervisor's nested disposal receipt. Both were corrected with targeted
  regression coverage, including the real cleanup-envelope producer.
- Cohesive client/model, caller-selection, disposal-proof and response-reader
  extractions preserve public import paths and shrink-only module ceilings.
  Baseline changes cover only changed source; no ceiling was widened.
- Final focused validation includes dispatch 454 passed, bridge 244 passed/
  one platform skip, and ground 39 passed with the native owner lane enabled.
  Earlier client/model compatibility selection passed 394/one platform skip.
  Lint, installation-contract and module-size checks are green. Required
  publication CI and the actual PR merge remain open.
- Validation tiers: unit/connected contracts and simple owner-CLI end-to-end
  checks exercised; fresh synthetic owner roots directly prove first-touch
  journal initialization. A full disposable-host install rig is not exercised
  because this change does not alter installer/bootstrap code. No live external
  venue or production runtime was changed: the resolved development locus is
  local, and production rollout is not authorized. Promotion and later
  authorized installation must be reported separately from a dev merge.

### 2026-10-13 - Publication integration with current dev

- The initial runtime commit hit real publication rebase conflicts. Preserved a
  local backup and integrated current `dev` rather than overwriting upstream
  ordinary ACP create/store/send (#6469), disposable producer ownership (#6391),
  remote seed admission (#6073), or dependency repair (#6458).
- Ordinary ACP startup now freezes its execution project before bridge effects
  and passes the same durable creation key on lost-response/restart retries.
  Bridge's keyed no-prompt CLI returns the existing ready connection shape;
  dispatch retains repository/worktree/path with the created-agent receipt.
  Unrecorded keyed creation can reconcile by replay without guessing liveness;
  replacement revisions never carry a disposed earlier ordinary agent.
- Existing CLI acquisition remains independent. Native-bound ordinary send
  keeps its protocol-33 capability floor; owner-qualified acquisition and keyed
  creation require protocol 34, preventing a daemon with only the former
  capability from accepting the latter.
- Retained upstream FastAPI `>=0.142.2`; the earlier local floor restoration is
  superseded by that already-merged available-feed fix. No user/feed edits.
- Updated two lifecycle fixtures that had allowed automatic replacement during
  pending cleanup. They now assert the retained identity fence and same
  allocation after completed priming; replacement still requires explicit
  revision and positive disposal.
- Rebased broad dispatch pass: 1,028 passed, two platform skips, with nine
  integration failures corrected rather than ignored. Final affected selection:
  66 passed. Bridge native/keyed/acquisition/native-bound selection: 157 passed,
  one platform skip. Ground native keyed creation: 17 passed. Module-size guard
  remains green; required CI/review and actual runtime merge are still pending.

### 2026-10-13 - Runtime PR review and CI follow-through

- Opened runtime PR #6517. Required CI exposed stale generation evidence and
  legacy/parser fixtures that omitted the newly mandatory workspace facts.
  Added generation-34 source-pinned wire evidence while preserving earlier
  fixtures, and updated the synthetic consumers rather than relaxing runtime
  provenance checks.
- The current-head automated review identified wall-clock ordering as a real
  revision-regression risk. Allocation selection now orders by logical revision
  before timestamp, with a clock-rollback regression. Corrected two remaining
  protocol-33 documentation references to the acquisition floor of 34.
- An integrated ordinary-start recovery check also exposed completed but cold
  unprompted keyed creation. Replay now strictly resumes the original native
  conversation only while it still has zero turns; it never recreates or
  redelivers. The focused native-owner replay lane passed 23/one platform skip.
- Independent registry and parser/creation contracts passed 47/one platform
  skip; affected dispatch replay/revision selection passed 65. PR updates,
  subsequent current-head review, required CI, and actual merge remain open.

### 2026-10-13 - Completed replay cleanup and route compatibility

- The subsequent Copilot review returned a current-head Comment with zero open
  findings, while its overview called out a possible stranded completed gate.
  Confirmed and fixed that interruption window: exact completed replay now
  settles its own launch gate without rebinding or resubmitting. A foreign gate
  is preserved. Both own/foreign gate cases have regression coverage.
- The sole remaining full-suite CI failure was a direct `start_session`
  consumer without HTTP URL metadata. Restored the existing helper contract
  by placing keyed-endpoint validation in its own route wrapper, not by
  weakening validation or repeatedly expanding unrelated test stubs.
- Connected bridge/keyed/registry/readiness/preference selection passed
  115/one platform skip; module-size guard remains green. The required keyed
  endpoint still refuses missing keys before effects.

### 2026-10-13 - Preserve pending allocation outcomes through adapters

- The next current-head review identified a High-severity adapter gap: ground
  keyed creation correctly reported exit-3 pending, but dispatch preparation
  classified every nonzero exit as unavailable and spent its failed budget.
- Preserved that typed pending outcome through the ground result decoder,
  supervisor and one-shot CLI. Both defer the original creation key; positive
  conflicts/terminal errors and malformed responses retain their own behavior.
  Bridge's ground-owner adapter likewise reports pending owner operation,
  rather than disguising the same response as a deterministic refusal.
- Real SQLite/supervisor and one-shot regressions each replay six pending
  attempts against a three-attempt failure budget, assert zero failed budget,
  one unchanged creation key and no premature spawn; the supervisor then
  admits exactly one worker when that same allocation completes.
- Extracted cohesive ground-result decoding into existing workspace binding
  helpers to retain shrink-only module ceilings. Dispatch affected selection:
  32 passed. Bridge owner/creation selection: 42 passed, three opt-in/platform
  skips. Lint and module-size guards are green; updated-head review and required
  CI remain publication gates.

### 2026-10-13 - Repairable pre-effect refusal without phantom allocation

- A later review of the same published head made the pre-effect refusal concern
  concrete: allocation identity was reserved before profile/readiness/pairing
  validation, leaving a permanently pending tuple when those checks refused.
- Moved reservation to the actual effects boundary, after those validations and
  before directory/source/ownership allocation. Narrow validation/IO exceptions
  before reservation preserve facts as a known refusal; only the same facts
  can retry after repair. Unknown interruption and any failure after reservation
  remain pending and never authorize another allocation.
- Unit regressions cover three pre-effect exception classes, changed-facts
  refusal, real profile validation, and refusal after reservation. A fresh real
  owner subprocess refuses profile validation with no directory or tuple, then
  repairs and replays the same key to exactly one checkout and tracking record.
- The initial native fixture patched a lazy façade rather than its preflight
  owner, so it did not inject the refusal. Corrected that fixture to patch the
  actual `resolve_launch_cli` owner; no runtime validation was weakened.
  Owner/native/seed selection: 30 passed. Lint and module-size guards are green.

### 2026-10-13 - Ground provenance across deduplicated task generations

- A subsequent review identified that the stable deduplication creation key
  could cross task IDs while ground allocation facts still used the current
  concrete task ID. That mismatch conflicted on replay despite an unchanged
  logical allocation.
- Ground creation and carried-worktree attribution now use the original
  allocation task/reservation/attempt from the immutable allocation reservation
  key. Current task generation/reservation remain the distinct admission and
  delivery authority. Malformed original provenance refuses before effects.
- Recorded and lost-response SQLite regressions close one task and emit another
  with the same typed dedup identity, preserving one target and original ground
  provenance. The real fresh-owner/bridge companion now covers both same-task
  restart and re-emitted-task restart, proving one checkout/native identity with
  simulated ACP/provider I/O.
- Dispatch affected selection: 34 passed. Native connected companion: two
  passed/one platform skip. Promoted cheap pre-effect refusal regressions into
  the worktrees guard lane so required CI exercises that safety boundary.
  Publication review, required CI, and actual merge remain open.

### 2026-10-13 - Complete deterministic allocation and drain refusal recovery

- The next review identified codename-policy validation and a drain race as two
  remaining deterministic refusal paths. Ground reservation now occurs after
  codename selection and under-lock policy validation, immediately before the
  first reciprocal claim/checkout/tracking effect. Source/root preparation may
  precede that point; it is not an allocated execution workspace.
- Added real codename-policy refusal/repair coverage alongside profile refusal,
  including the fresh owner subprocess. Existing concurrent native tests now
  pause at the actual checkout boundary, after reservation, preserving the
  distinction between recoverable validation and ambiguous workspace effects.
- Typed `DaemonDrainingError` from the session manager's pre-effect start guard
  restores `allocated` and releases only this request's gate. Retry preserves the
  original workspace and planned session ID; unknown native-start exceptions
  still hold for reconciliation.
- Initial new codename fixtures attempted to mutate frozen configuration
  values. Corrected them using nested dataclass replacement, not by weakening
  configuration immutability. Ground/native/seed selection: 32 passed. Connected
  bridge creation/drain selection: 28 passed/one platform skip. Lint, module-size
  and patch guards remain green.

### 2026-10-13 - Full holder revalidation before keyed native start

- Current-head CI passed, but the next review requested the complete holder
  proof after awaited owner revalidation. Reused one sessionless predicate for
  both admission checks, retaining ground history, live sessions, DB ownership,
  and in-process sessions before synchronous local-start registration.
- Connected tests show the existing SQLite gate already rejects ordinary
  bridge/live-session inserts during that await. No second ordinary native
  start was reproduced through those real registration paths. Added explicit
  fence regressions plus late in-process/ownership-holder refusal regressions,
  without weakening or bypassing the gate to manufacture a race.
- Bridge creation/acquisition selection: 143 passed, three opt-in/platform
  skips. Native-owner coverage from the prior milestone remains applicable:
  this correction changes neither owner subprocess nor installation behavior.
  Current-head review and actual merge remain open; no rollout is authorized.
