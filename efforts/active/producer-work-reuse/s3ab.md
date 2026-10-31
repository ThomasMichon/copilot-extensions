# S3a/b implementation episode

[Parent effort](README.md) · [Reviewed design](codespaces.md)

- **Status:** Active — bounded foundation publication; integration blocked
- **Tracker:** #7949
- **Participant:** placid-battery, source implementation and PR owner.
- **Scope:** qualified owner-wave inventory, batched mux checks, bounded venue
  concurrency, and compatible atomic heartbeat generation fencing.
- **Current contract:** [selected admission, full publication](selected-authority-contract.md),
  explicitly selected by the user and reviewed/merged in #8278.
- **Current slice:** new captured A/N1 correctness is accepted; selected status
  policy and full Option-F control publication are recorded. Exact R1b
  consumer/path/reconcile/owner admission remains pending. The private prototype
  stays unwired; N2/performance/activation/full-integration gates are not complete.
  #8143 now publishes only the separable source foundation, not the production
  owner-wave consumer. The existing production session/probe paths are retained.
  The additive candidate seam has narrow explicit user authorization, not an
  owner release. The previous multi-mux cost conflict is historical analysis,
  not the new representative profile's ceiling. No cache/push algorithm is
  implemented.
- **Historical evidence:** [authority accounting](s3ab-authority-proposal.md) and
  [strict-boundary lower bound](s3ab-boundary-decision.md) remain retained, not
  current-policy performance qualification.
- **R1a contract:** [storage and pure freshness](inventory-freshness-admission.md)
  records the exact pure-slice admission/API and subsequent policy selection.
- **R1b proposal:** [exact consumer/path/reconcile/owner
  hunks](inventory-r1b-consumer-proposal.md), including model refinements and
  the keep-healthy/defer-rebuild owner dependency. No implementation is implied.

## Request

Public-safe capture: implement the admitted S3a/b contract from merged #8055.
Keep the legacy two-argument SessionProbe and flattened lifecycle inventory API.
Zero due venues means zero listing calls. Ten venues across three accounts with
four muxes each must use at most three listing calls and ten mux executions in
the successful, below-page-limit, no-retry fixture. Count pages/retries separately.
Preserve unknown/no-wake, owner lifetime, tenant generations, local push debt,
and confirmed-only release. Add optional keyword-only generation to heartbeat,
comparing under its existing lock; preserve None semantics and lease behavior.
No borrowed transports, transcript catalog changes, other plugins, telemetry,
launch/recovery, production deployment or live venue probes.

## Plan

- [x] Recheck issue/task/file claims and verify the merged design.
- [x] Obtain admission for the narrow shared heartbeat signature.
- [x] Review and merge the explicit selected-authority semantic amendment (#8278).
- [x] Obtain narrow explicit authorization for the candidate read-result seam;
  preserve every other #5761 reservation.
- [x] Obtain exact R1a pure JSON storage/model admission, with no runtime
  activation or existing caller/shared-helper edits.
- [x] Implement the unwired R1a model/store with pure injected-OS unit coverage;
  no production limits, policy, path resolution or control permissions.
- [x] Independently review R1a and pass its targeted units and existing fast
  guards; no native qualification is implied.
- [x] Draft-publish R1a after pure units/fast guards;
  retain all earlier runtime/performance/native gates.
- [x] Capture a new 92-case A run and real-primitives N1 correctness; receive
  independent acceptance without reconstructing the original missing receipts.
- [x] Record selected status policy and Option F, preserving all source-owner
  boundaries and no speculative SSH.
- [ ] Obtain exact R1b hunk/path/provider admission; N2 remains separately gated,
  not a prerequisite for this design work.
- [x] Implement immutable inventory and batch protocol with explicit wave adapter.
- [x] Implement bounded concurrent probes, cancellation and atomic renewal fence.
- [ ] Validate and independently review the unwired foundation; publish and merge
  existing #8143 through normal gates without claiming production integration.
- [ ] Reconcile source outcome and retained #7949 qualification after merge.

## Validation Plan

- [ ] Requalify the amended selected/publication implementation with complete
  matched total-work cold/warm/not-due 0/1/10 CLI/keyring and bridge/no-bridge cases.
- [x] Pure inventory/protocol/error/age/generation tests.
- [x] Actual SessionForwards consumer with owned executable/transport counters,
  identical baseline/candidate workload, 0/1/10 due and unchanged second poll.
- [x] Stopped/unknown/partial/limit/error, slow venue, cancellation, replacement
  and push-debt controls; current/stale/missing/expired heartbeat and concurrent
  replacement under the existing owner lock.
- [ ] Focused existing suites, touched-source Ruff, install-contract and CI.
- [ ] Record source/release/installed cells separately. Live remote and CPU
  qualification remain unavailable in this episode, retained by #7949.

## Implementation notes

No durable cache or new authority is introduced. Inventory records carry source,
effective configuration, candidate accounts and credential digests, never tokens.
Admission revalidates those inputs without listing again. This intentionally
retains metadata work until its owners supply a cheaper trustworthy revision.
A truncated/failed account also cannot exclude duplicate ownership, so it
conservatively withholds connection authority for the wave. Expired evidence
defers to the next due wave, with no hidden per-venue refresh.

## Journal

### 2026-10-23 — Narrow additive reader checkpoint, total-work conflict retained

- Accepted committed source registration/head evidence after an ambiguous client
  timeout; no force, controller repair or second owner was introduced. Missing
  native child hosting projections remain qualification/cleanup limitations,
  not a substitute editing guard.
- Resumed the same retained task with its verified owner and no wake. Fresh
  open-PR subject/file checks found only #8143 on the candidate/owner-wave files.
  The user's narrow authorization, not absence of a conflicting PR, admitted
  `CandidateRead`/`read_candidates`. All other namespace reservations remain.
- Implemented the invocation-local seam and its unchanged tuple projection.
  Owner scope capture consumes actual already-read effective config/routing/
  provider reports and fails closed on indeterminate provider authority.
  No selected-effect, transport, retry or publication algorithm change is kept.
- Reader/ordering/concurrency/account/authentication selection: **83 passed,
  1 skipped**. Non-native owner safety and new scope controls: **228 passed**.
  Complete accounting/scope selection: **27 passed**, retaining **72 complete
  raw frames** across 0/1/10 venues, both credential backends, bridge/no-bridge,
  cold/warm/not-due. Touched-source Ruff and install contract pass.
- The matrix is intercepted production-callgraph evidence, not native process,
  CPU, internal GH HTTP or saturated-host wall-clock qualification. An initial
  native binding-lock fixture run failed/returned partial observations; it is
  retained. The final matched count fixture uses the same synthetic lock
  boundary for baseline/candidate while retaining actual binding-file and
  account/config/credential reads. It cannot qualify native lock behavior.
- A broader native regression selection encountered an assertion failure and
  then its unchanged 30-second watchdog while reading native event evidence.
  No deadline was increased and no blind timing retry was run. The separate
  non-native selection above is not a pass for that failed native lane.
- A fresh independent reviewer returned “Review paused before reading the
  source delta. No findings or approval issued.” This is not a review receipt;
  no replacement reviewer was launched.
- The retained draft still costs **596 vs 103** CLI requests for ten mux venues,
  **926 vs 123** with bridge. More importantly, the amended contract's optimistic
  one-venue minimum is **17 vs 13 cold / 10 warm**, even before selected metadata
  and retry overhead. See the exact [lower bound](selected-readresult-dependency.md).
  That conflict requires campaign adjudication; no freshness, total-work,
  deadline, review or publication gate is waived.
- Documentation impact: documented the additive source API, actual-report
  qualification and the unresolved acceptance conflict. This closes a narrow
  implementation prerequisite beneath existing vision/pattern intent; it does
  not revise the selected-authority policy. No new daemon/cutover or deployment
  behavior is introduced. #8143 stays draft/unmerged; source merge, release,
  installed qualification and remaining campaign work are not complete.

### 2026-10-23 — Selected-authority amendment merged; ownership dependency retained

- Explicit user decision `selected_then_full_publication` is now the reviewed
  contract. Docs-only #8278 merged at
  `c6c117c03cc75790023c7db1ffa15e2d59a589ea` after independent review, provider
  approval with no findings, and required green CI.
- Read the canonical effort after merge; this completed only the contract
  amendment, not runtime implementation, aggregate qualification or rollout.
- Fresh #5761 forge comments and its active source effort still name
  `rattling-circuit`; no release or narrow seam agreement was found. Returned
  the exact [candidate read-result dependency](selected-readresult-dependency.md)
  before shared edits rather than duplicating its algorithm or taking ownership.
- Pulled the merged contract into this retained implementation branch. The
  rebase resolved only adjacent effort-index text, preserving other owners'
  updates; product source and test bytes are unchanged from the saved branch.
- Draft #8143 remains unmerged. Original raw evidence is untouched. No amended
  implementation measurement, live CodeSpace probe or installed rollout occurred.

### 2026-10-23 — Admission

- Verified the exact #8055 merged files and fresh open-PR file claims.
- Narrow heartbeat API extension separately admitted before any source edits.
- Existing plan remains canonical; this episode executes only S3a/b.
- No performance receipt, rollout or remote observation exists yet.

### 2026-10-23 — Local consumer validation

- Pinned baseline `35872e7ad67e1c0ef182a1f4401a49af69c4afb6`, same synthetic
  0/1/10-venue, three-account, four-mux workload through actual
  `SessionForwards.probe`, lifecycle parsing and owned executable/manager
  boundaries. The native lane uses identical in-memory registry backing for
  both versions; separate tests exercise real owner-file locking/replacement.
- Actual listing/mux invocations: zero venues **0/0 -> 0/0**; one venue
  **3/4 -> 3/1**; ten venues **30/40 -> 3/10**. Each listing is one below-limit
  page; zero retries. Opens/disconnects remain **10/10** in both ten-venue
  samples. Unchanged second polls issue zero additional commands.
- Latest count-fixture elapsed samples for ten venues: baseline 13.094s,
  candidate 0.969s (monotonic clock, one sample each). These exclude real
  authority-resolution latency and do not qualify CPU, tails, GitHub or SSH.
  No telemetry adapter, installed runtime or production collector was used.
- The initial Python-child fixture exceeded the unchanged 30-second case
  deadline. Native command fixtures, separately bounded baseline/candidate
  cases and synthetic registry backing isolate the intended executable
  boundary without raising deadlines or weakening product health.
- Independent review identified config-worker cancellation, stale retry
  admission and post-shutdown push admission. Fixed all three classes:
  prefetch/drain the bounded config child, gate every connection/exec attempt,
  and prevent new owed work after closure. Real child-drain and Bash
  batch/missing-tmux controls pass; 46 focused new tests passed.
- Follow-up review found duplicate cancellation and timeout-then-shutdown
  cleanup gaps. Wave cancellation now has one owner; each bounded observation
  is explicitly retained and drained, including on Python 3.10/3.11's
  interruptible timeout-cleanup path. Unequal sibling cleanup and timeout-first
  replacement controls pass. Final scoped run: **190 tests passed**; independent
  verification confirmed the named cancellation defects resolved.
- Provider review tightened post-exec/disconnect scope validation, credential
  rotation, pre-config admission and listing-only compatibility. Default bridge
  proof now uses the same inventory with a separately owned manager and bounded
  config cleanup; this is not S3c transport borrowing. Registry read/renew/release
  calls run in joined workers rather than blocking the owner's event loop.
- CI's module cap prompted extraction of owner qualification/parsing into the
  new helper rather than widening any baseline or unrelated module.
- Provider-review regression run: **198 tests passed**, including credential
  rotation, scope changes during execution/disconnect/credential acquisition,
  listing-only provider compatibility, contended-registry sibling progress,
  same-inventory/separate-manager bridge proof and actual bridge config-child
  cancellation. All product and test deadlines remain unchanged.
- Second provider review corrected cached mux authority after awaited bridge
  work and the successful-ControlMaster handoff cancellation race; retained the
  existing redacted config-fetch dial diagnostics and updated the legacy opener's
  off-loop test. Extended final selection: **217 tests passed**, including the
  existing `test_owner_never_blocks.py` suite. No shared telemetry adapter or
  transport borrowing was introduced.
- The next review's unthreaded negative-verdict finding is fixed: every
  actionable tenant verdict rechecks inventory qualification after registry
  awaits; negative results allow fresh stopped evidence without requiring
  Available. Scope-change, expiry and fresh negative controls cover both stopped
  rows and completed mux negatives. Final scoped run: **223 tests passed**.
- Subsequent review identified a material performance regression outside the
  existing counter fixture: each production scope capture performs account
  discovery and fresh credential CLI calls, repeated at per-venue/per-tenant
  admission. The constant-scope fixture proves listing/mux reductions, not
  reduced total subprocess work. This source must not merge in that state.
- Execution is blocked pending admission of a bounded authority-revision
  contract with the account/configuration/credential owners. Cheap revisions
  must actually invalidate on selection/config/provider/credential replacement;
  neither a guessed filesystem watch list nor stale-token TTL reuse supplies
  that contract. The proposed seam is an account-authority snapshot plus a
  cheap current-revision check, with production-path counters covering discovery
  and auth as well as listing and mux work. No shared source signature has been
  changed for this follow-up.
- The follow-up proposal traces existing owner APIs and identifies the opaque
  keyring counterexample rather than inventing a revision provider. New
  production-callgraph accounting proves a complete mux-only cold fixture
  regresses from **103 to 596** process requests; mux-plus-bridge complete
  frames regress **123 to 926**. These are intercepted synthetic boundaries,
  not native process/CPU or hidden GH HTTP measurements. Prior real-child
  listing/mux counts remain explicitly subwork-only.
- Preserved all six accounting runs and their original public-safe counter
  frames, including a failed recorder run and partial observations; full local
  raw logs remain retained. The original older disposable event files were
  already removed before the preservation request; retained tool stdout is
  transcribed separately, never presented as recovered raw files.
- The evidence recorder passes 27 tests covering actual resolution,
  account/namespace/config/credential replacement, provider failure,
  partial/limit inventory, expiry, retries, stale heartbeat and cancellation.
  This is evidence collection, not a causal runtime fix or authority admission.

### 2026-10-23 — Revised profile and durable-inventory design direction

- Committed the coherent candidate-read/source-evidence checkpoint before
  changing the planning scope. Retained its complete 72-frame packet and all
  prior stress/raw evidence unchanged; no implementation PR update or merge.
- New public-safe design summary: retain all active/inactive inventory locally,
  use existing-owned-transport state notifications where supported, and bound
  stale/disconnected/out-of-band reconciliation. Keep credentials and atomic
  owner mutation separate from status caching. Storage, grace/action policy,
  ACP and SSH-event ownership require plan review before functional changes.
- Characterized two accounts and one total Copilot session per active venue:
  0/1/4/8 examples, balanced/single-account placement, retained inventory 0/12,
  CLI/keyring, cold/warm/not-due and optional bridge-forward health effects.
  These are fixture parameters, not product limits or a new quota policy.
- **96 profile tests passed / 288 complete Mux-route frames**. Mixed-fleet
  evidence covers only four Mux venues with the other four represented in
  inventory; ACP effects and full mixed-fleet cost remain unmeasured. A bridge
  health probe is not an additional Copilot session.
- **10 focused control/compatibility tests passed**, retaining four additional
  stopped/page-limit control frames. Two-account retained inventory at the
  page limit cannot authorize an SSH effect; known stopped entries remain
  non-waking. No deadline or product health behavior changed.
- One/four/eight Mux venues without configured bridge health: original CLI
  cold/warm requests **8/6, 26/24, 50/48**; retained draft **47/47, 152/152,
  292/292**. These characterize current unqualified algorithms, not a cache fix.
  Local cache-hit/push-delta/successor due-action/expiry/out-of-band cells are
  absent, not zero-cost measurements.
- The revised uncached selected/full optimistic CLI formula is
  `6 + 4B + 4C` for positive venue count C and completed-result batches B.
  It is derived only and does not define the proposed cache contract or its
  acceptance bar. The older 17/13/10 result is not reused as a new-profile fact.
- Source trace found a prune-eligibility JSON store, in-memory availability
  cache, existing owner lifecycle, and framed carrier subscription primitives;
  no CodeSpace-status push operation. Bridge owns ACP transport liveness.
  Returned those exact contracts as plan prerequisites, not permission to
  edit other owners or deploy a new remote stack. No existing agent was
  contacted or inspected.

### 2026-10-23 — Successor plan merged after current-head review

- #8418 merged at `4a6d2d31aa9837ec63268ebd1fbeea70fa41f536`. The current
  published head `9bc98296892784fe1a4d6c252c02ae64d23aef3e` received a genuine
  provider Approve with no open issues, the artifact-integrity thread was
  resolved, and all required checks passed. The normal authorized Maintainer
  completion profile was used; no CI or native-qualification waiver.
- Fixed the actual review finding for both profile artifacts: manifest hashes
  and byte lengths now describe UTF-8/LF repository blobs rather than CRLF
  capture terminators. Verified committed bytes and normalized checkouts;
  observation records were not rewritten and no timing fixture was rerun.
- Re-read the effort after merge and pulled the retained implementation forward
  onto the approved plan with a committed backup. Resolved only the adjacent
  effort-index replay context (retaining other efforts' current rows) and the
  already-reviewed LF metadata. Product source, tests and evidence bytes match
  the saved source checkpoint; no reset, stash, force push or ref deletion.
- Plan review/publication is complete, not the S3 objective. Storage and
  freshness/action/publication decisions, exact discovery/SSH/ACP seam
  admission, functional implementation and source/native qualification remain.
  Plan publication does not select the pending policy or expand source
  ownership. #8143 remains draft at its previous
  published head with its authority-fanout review finding still open.

### 2026-10-23 — Admitted R1a pure storage/model checkpoint

- Implemented only `inventory_store.py` and `test_inventory_store.py`, with
  directly related docs/changefile updates. Fresh open-PR checks found no
  competing change to those exact files. No existing lifecycle, availability,
  forward, config/auth, picker/resolver, SSH/Bridge or shared helper changed.
- Dedicated JSON remains an unwired internal prototype. Limits/policy have no
  defaults; caller-supplied paths must have existing plain parents. The unit
  calibration is not a production cap or policy selection.
- Added explicit source + cache-creation epoch + revision CAS. Initial writes
  require actual absence/revision 1; updates require exactly one increment and
  the same source/epoch. Corrupt/wrong-source state is never silently repaired.
  Busy is nonblocking; postcommit uncertainty is unknown without rollback.
- Existing `OperationLock` and `atomic_write_text` are reused unchanged.
  Precommit parent/target/custody checks and postcommit verification do not add
  a directory-fsync/power-loss or fully hostile filesystem-race guarantee.
- Bounded max+1 reads precede parsing. Strict schema/duplicates/types/finite
  values, independent limits, streamed serialization, frozen/deep-copied input
  handling and conservative observation merges have injected-OS controls.
  Freshness returns status labels/reasons only, never control authority.
- Initial unit run had three fixture tuple/list representation failures;
  corrected the fixture to represent JSON arrays, without relaxing the codec.
  A follow-up corrected account-header counting so the venue-row limit is
  independent of account count; header/whole-output work remains byte-bounded.
  Final targeted selection: **92 passed**. Ruff F/E9, install contract, source
  module-size and documentation-consistency guards pass. Fresh independent
  source review and the named limit-correction follow-up found no significant
  issues.
- No actual OS lock/multiprocess/crash/Windows-sharing fixture, native
  performance sample, provider call or production activation ran. F1's native
  slot and all previous S3/performance/consumer gates remain separate.
- Evidence-capture limitation: the final local runner output was delivered
  through the tool channel without a durable stdout file; no JUnit output was
  requested or reported. Independent-review results likewise have no original
  disk receipt. No original file hash can be supplied for those missing
  artifacts. Existing current-head CI logs verify their own gates, not the
  identity of the local 92-test execution. No rerun/reviewer was used to fill
  that gap; later qualification must configure capture before execution.
- Prepared a separate [real-OS N1/N2 proposal](inventory-storage-native-plan.md)
  for actual primitive custody/CAS, Windows sharing and explicitly bounded
  fault/crash controls. It is not native execution admission.
- Published the R1a implementation checkpoint to the existing draft #8143;
  its description explicitly preserves earlier performance/native findings.
  Current-head CI remains a separate gate, not a performance receipt. No new
  competing PR, ready transition or merge was attempted.
- Next: retain R1b policy and exact consumer/transport-owner admission.
  No storage checkpoint constitutes
  a whole-cache, source-merge, performance, release or installed completion.

### 2026-10-24 — New A/N1 acceptance and selected status policy

- The coordinator independently verified the 21 closed artifact hashes,
  92-case A JUnit, one N1 harness JUnit with 38 actual-equals-expected cases,
  ten source pins, 90 ledger events and both writer identity/Job/reader/reaping
  receipts. Final real operation custody was reacquired/released. No forced
  cleanup, unavailable case, source bug or source/test change was observed.
- Accepted canonical 22-member archive SHA-256:
  `6ca14e177b173cd8f5a1a54fa56546be2310cc33f9e758e0f44968dd6724251d`.
  This is **new captured A/N1 correctness only**, not the original missing
  unit/review artifacts, N2, performance, activation or whole-PR completion.
  Original run files remain retained in the owning worktree; no arbitrary
  pruning is authorized by this journal/reference.
- The user selected: fresh badge 60 s; inactive out-of-band reconcile 300 s
  only on demand/already-running owner; known-disconnect labelled last-known
  30 s then unknown with history retained; immediate lost-channel control
  invalidation and one qualified provider recheck next existing 15 s cycle or
  explicit action, no speculative SSH; source/account failure coalescing and
  60 s cooldown, explicit refresh one coalesced attempt; unchanged 30 s effect
  proof. Silence is not disconnect; cache work does not start/keep alive a daemon.
- **Option F remains selected:** full LIVE candidate/config/credential
  validation once per completed control-publication batch, not per status
  read, push delta or tenant. This continues `selected_then_full_publication`,
  not a new Option-S cross-account revocation boundary.
- Returned the exact R1b hunk proposal. Model badge/refresh separation and
  disconnect-label ordering need explicit refinement; filtering unknown holds
  alone would tear down healthy forwards, so owner keep/drop/defer-rebuild
  semantics must be admitted together. Canonical page completeness and
  same-boundary fresh selected-auth plumbing are separate exact provider seams.
- No caller/shared-credential/transport/push/path-provisioning code or new
  private default factory was implemented. The additive CandidateRead grant is
  unchanged and does not release the retained #5761 namespace/discovery owner.
  N2/sharing/crash/fault-tracing gates remain separate and do not stall design.

## Reproduction and remaining cells

Current source-only foundation and legacy-consumer regression selection:

```text
python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_inventory_store.py --test-file tests/test_candidate_read.py --test-file tests/test_owner_scope_read.py --test-file tests/test_owner_wave_foundation.py --test-file tests/test_session_forwards.py --test-file tests/test_owner_never_blocks.py --test-file tests/test_lifecycle.py
```

R1a's admitted pure/injected-OS selection is separate:

```text
python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_inventory_store.py
```

The earlier owner-wave native/accounting/profile tests belong to the deferred
integration and are preserved in the pre-slice source history. Their evidence
files remain unchanged here. They are not tests of the current unwired candidate
and no historical count is reused as current-source performance qualification.

| Cell | State |
|------|-------|
| Source tests | Historical 223-case integration and 128-case model receipts retained; fresh foundation/legacy-consumer review and CI pending |
| Release | Pending source merge and ordinary promotion; no release claim |
| Installed/native venue | Unavailable by scope; no updater or live CodeSpace probe |
| CPU/whole-wave metadata/latency tails | CPU/tails unmeasured; repeated authority CLI fanout blocks deferred production integration retained by #7949/#5761 |
| S3c transport reuse | Excluded and retained by #7949 |

### Pure policy-model correction checkpoint

- Admitted only `inventory_store.py`, its existing test file, and direct
  docs/changefile. This implements the already-selected status policy, below
  the reviewed inventory architecture; it does not activate R1b.
- Separate universal badge freshness from the inactive age-due hint. Passive
  classification performs no I/O or remote effect; demand/owner gating stays
  caller-owned. Explicit limits and policy remain mandatory.
- Require current-owner `known_disconnect_epoch` explicitly. A matching actual
  loss receives the selected display grace before old fact age, without
  refreshing history, suppressing incomplete/error provenance or granting
  control. Future clocks and persisted timestamps alone cannot create grace.
- Retain first enumerated Unknown identities and error metadata uncertain;
  preserve older positive facts unchanged. No fabricated fresh success or
  global absence.
- Storage/codec/CAS/custody bodies are unchanged. Prior captured A/N1 originals
  and the accepted archive remain retained unchanged. Fresh pure-unit capture
  and fast guards are pending below; no N1/N2 rerun or performance claim.
- Tightened the proposal: cache construction must bypass legacy provider
  resolution; local reads have one owned single-flight task per binding/cohort,
  registered and drained through existing owner helpers. Canonical source/account
  producer owns reconcile/coalescing, never a per-Gate poller. All such consumer
  hunks remain unimplemented/unadmitted; Option F/#5761 boundaries unchanged.

Fresh correction validation (not reconstruction of the original 92, and not
another A/N1 native run):

- Tested implementation head `64bd8343e541809215c941d89d2bfd8c28c828dd`,
  clean tracked tree. Existing bounded runner with
  `--test-file tests/test_inventory_store.py`; explicit `PYTEST_ADDOPTS`
  supplied real JUnit without changing the shared runner.
- **128 passed; zero failures/errors/skips.** Ruff F/E9, install-contract,
  changed-file module-size and documentation consistency each exit 0.
- Retained root: `.test-venvs/r1b-policy-captured-20261024-151913/`.
  Each stage's `*.stdout.log`/`*.stderr.log` was opened before execution.
  `manifest.json` records exact physical argv, source hashes before/after,
  real JUnit case totals and strict owned-Job cleanup; all pins unchanged.
- Closed `receipt-index.json` SHA-256:
  `d02c24f3835526acc7faac2c9b7369e96977aeeb1e920f711cb33c403a4ad3f1`.
  `manifest.json`:
  `e03244508c77bea3dd82c6abff9f4c85a96d2b6fbc6efdf485017fdeda15237e`.
  `junit.xml`:
  `401e7bd31b17e9157ad717f60b1fe1c0779b1f48c6b107c5740429f7431d4a83`.

### 2026-10-31 — Explicit incremental source-foundation disposition

- Admitted history-preserving removal of this PR's production wave wiring only.
  Restored the production `session_forwards.py` and its directly changed legacy
  tests to the PR base before reconciling current Dev. No already-shipped
  capability, credential behavior, cleanup or probe schedule is removed.
- Retain the explicitly invoked inventory/protocol/authority foundations,
  CandidateRead's narrowly admitted additive seam, optional heartbeat generation
  fence, and the private JSON model/policy correction. None is a cache activation
  or a default factory/consumer integration.
- Retained the complete pre-slice implementation in a local backup before
  editing. Its integration-specific test bodies remain in source history;
  retained raw manifests/frames and accepted A/N1 originals are unchanged.
  Focused foundation tests retain qualification/unknown/no-wake/protocol and
  heartbeat controls, plus a real default-factory/consumer regression against
  accidentally re-entering the deferred wave authority.
- The production fanout review thread is addressed by removing its caller path,
  not by claiming cheaper authority or waiving performance. Exact R1b
  keep/drop/defer-rebuild, path/limits, page completeness, fresh selected auth,
  Option-F publication, producer exclusion and transport admission still need
  their existing owners. #5761 is not released; #7949 and this effort stay open.
- This increment may clear source review/CI and merge independently. It does
  not complete R1b, N2/sharing/crash/native performance, release, installed
  readiness or activation. Fresh targeted validation and publication are pending.
  `units.stdout.log`:
  `1280256ba9c23c5b470ebc28f71ce94be687935270e393e0ae73d34bcfa5bf21`.
  Unit stderr is empty; individual guard hashes are in the closed index.
- AST comparison against accepted R1a confirms every store definition except
  `apply_account_observation` and `classify_status` unchanged. This is not a
  new native proof at the correction head. All prior originals/archive are
  retained; no N1/N2 execution, timing qualification, consumer activation,
  independent-review reconstruction, ready or merge claim.
- All owned test/guard children reaped; strict Jobs confirmed empty and closed.
  No owned helper remains. Draft publication is a separate normal identity-gated
  operation; this checkpoint does not close the source effort or old gates.
