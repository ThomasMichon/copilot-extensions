# S3a/b implementation episode

[Parent effort](README.md) · [Reviewed design](codespaces.md)

- **Status:** Blocked
- **Tracker:** #7949
- **Participant:** placid-battery, source implementation and PR owner.
- **Scope:** qualified owner-wave inventory, batched mux checks, bounded venue
  concurrency, and compatible atomic heartbeat generation fencing.
- **Current boundary:** [exact authority proposal and counter evidence](s3ab-authority-proposal.md).
  Proposal/evidence only; shared functional API changes remain unadmitted.
- **Latest decision packet:** [boundary-only schedule and lower bound](s3ab-boundary-decision.md).
  Pure reads do not need repeated full revalidation; strict true-effect and
  publication admission still exceed the retained total-work baseline.

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
- [x] Implement immutable inventory and batch protocol with explicit wave adapter.
- [x] Implement bounded concurrent probes, cancellation and atomic renewal fence.
- [ ] Validate and independently review; publish and merge through normal gates.
- [ ] Reconcile source outcome and retained #7949 qualification after merge.

## Validation Plan

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

## Reproduction and remaining cells

Run from the source root with a supported Python and the existing bounded runner:

```text
python tools/run-plugin-tests.py agent-codespaces --test-file tests/test_owner_wave.py --test-file tests/test_owner_wave_native.py --test-file tests/test_session_forwards.py --test-file tests/test_lifecycle.py --test-file tests/test_transcript_mirror.py
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
