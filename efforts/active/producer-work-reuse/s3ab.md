# S3a/b implementation episode

[Parent effort](README.md) · [Reviewed design](codespaces.md)

- **Status:** Blocked
- **Tracker:** #7949
- **Participant:** placid-battery, source implementation and PR owner.
- **Scope:** qualified owner-wave inventory, batched mux checks, bounded venue
  concurrency, and compatible atomic heartbeat generation fencing.
- **Current contract:** [selected admission, full publication](selected-authority-contract.md),
  explicitly selected by the user and reviewed/merged in #8278.
- **Current slice:** CodeSpaces R1a private JSON storage/pure model is admitted
  with explicit limits/policy inputs and injected-OS unit validation only.
  Production grace/action/publication decisions and R1b consumer/transport
  admission remain pending.
  The additive candidate seam has narrow explicit user authorization, not an
  owner release. The previous multi-mux cost conflict is historical analysis,
  not the new representative profile's ceiling. No cache/push algorithm is
  implemented.
- **Historical evidence:** [authority accounting](s3ab-authority-proposal.md) and
  [strict-boundary lower bound](s3ab-boundary-decision.md) remain retained, not
  current-policy performance qualification.
- **R1a contract:** [storage and pure freshness](inventory-freshness-admission.md)
  records the exact pure-slice admission/API. Production defaults and F/S
  control-authority options remain unselected.

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
- [x] Implement immutable inventory and batch protocol with explicit wave adapter.
- [x] Implement bounded concurrent probes, cancellation and atomic renewal fence.
- [ ] Validate and independently review; publish and merge through normal gates.
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
  Final targeted selection: **91 passed**. Ruff F/E9, install contract, source
  module-size and documentation-consistency guards pass. Fresh independent
  source review found no significant issues.
- No actual OS lock/multiprocess/crash/Windows-sharing fixture, native
  performance sample, provider call or production activation ran. F1's native
  slot and all previous S3/performance/consumer gates remain separate.
- Published the R1a implementation checkpoint to the existing draft #8143;
  its description explicitly preserves earlier performance/native findings.
  Current-head CI remains a separate gate, not a performance receipt. No new
  competing PR, ready transition or merge was attempted.
- Next: retain R1b policy and exact consumer/transport-owner admission.
  No storage checkpoint constitutes
  a whole-cache, source-merge, performance, release or installed completion.

## Reproduction and remaining cells

Run from the source root with a supported Python and the existing bounded runner:

```text
python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_owner_wave.py --test-file tests/test_owner_wave_native.py --test-file tests/test_session_forwards.py --test-file tests/test_lifecycle.py --test-file tests/test_transcript_mirror.py
```

R1a's admitted pure/injected-OS selection is separate:

```text
python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_inventory_store.py
```

The native baseline lane needs the pinned commit available in the local Git
object database and Bash (Git Bash on Windows) for its explicit shell control.
It uses only synthetic credentials and owned local children. It is an explicit
development/integration lane, not an addition to required unit-only CI.

| Cell | State |
|------|-------|
| Source tests | 223 scoped tests pass; CI passed, but review blocks total authority-process fanout and merge remains pending |
| Release | Pending source merge and ordinary promotion; no release claim |
| Installed/native venue | Unavailable by scope; no updater or live CodeSpace probe |
| CPU/whole-wave metadata/latency tails | CPU/tails unmeasured; repeated authority CLI fanout is a known blocking source defect retained by #7949 |
| S3c transport reuse | Excluded and retained by #7949 |
