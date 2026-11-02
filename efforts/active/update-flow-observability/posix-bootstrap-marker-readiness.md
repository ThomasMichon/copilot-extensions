# POSIX marker-only bootstrap readiness

Parent: [Update Flow Observability](README.md).
Coordination: #7743. Existing #4195 admission, stale-process and lifetime work,
the #6812 repair pipeline, #8370 logging and held #6859 / #7833 remain separate.

## Request and bounded ownership

The operator requested a source-installed development loop:

> Work the problem to get the dispatch system allocating running tasks. Adjust variables and redeploy as needed in a tight loop.

`tinkling-capacitor` owns only the completed-runtime POSIX bootstrap no-op and
its directly dependent family coverage. This does not transfer another owner's
installer, broker, PID-reaping or publication authority.

## Established discrepancy

The ordinary POSIX bootstrap fast path still requires a legacy `.venv` or
`venv` link in addition to matching deployed and payload versions. Normal
marker-only installations intentionally have neither link. A completed,
unchanged runtime therefore enters reconciliation instead of the intended
no-op. A source-installed full-plugin worker exposed repeated bootstrap hook
timeouts while otherwise reaching real task execution.

This is a readiness predicate defect, not evidence that every timeout has the
same cause. Missing runtimes, changed content or versions, malformed completion,
and uncertain publication still require their existing handling.

## Plan

Implementation choices below are _(agent-recommended)_:

- Reuse the already-vendored canonical versioned-runtime resolver in its
  read-only mode. Do not add another interpreter resolver, repair authority,
  readiness cache or legacy runtime link.
- Permit the cheap no-op only for the expected selected completed generation
  and matching payload/deployment identity. A complete fallback generation
  alone must not make an invalid or different current selection look healthy.
- Preserve valid legacy behavior and all missing, changed, malformed,
  first-provision, refusal and reconciliation paths. Keep guidance writers and
  structured hook output intact.
- Update directly affected hook-family consumers through their existing
  synchronization conventions, with only necessary docs and changefiles.
  Review the PowerShell counterparts for parity rather than inventing a
  second platform's defect.
- Keep atomic admission and safe stale-owner recovery with #4195. Do not
  introduce or relax age-based process termination, publication consent,
  service lifetime, budgets or exception handling.

## Validation plan

- Exercise the actual hook and canonical resolver on owned temporary runtime
  files. A complete marker-only current runtime with matching identity invokes
  no installer or reconciliation child and creates no legacy links.
- Cover valid legacy installation, missing/current/fallback selection,
  incomplete or corrupt completion, mismatched expected generation and version,
  changed deployment identity and existing refusal paths.
- Retain exact hook output and guidance behavior, and measure actual before/after
  child count and elapsed cost. Do not call a source-shaped mock an installed
  or native startup proof.
- Run the focused contained tests and bootstrap synchronization/install-contract
  guards without increasing timeouts or growing required unit-only CI into an
  effectful matrix.
- Redeploy the source candidate into the disposable full-plugin integration
  fixture and execute real task allocation, submission and independent
  evaluation. Record process readiness separately from first-turn work.
- Obtain required review, CI and the normal containing release. Drive authorized
  installed rollout and running-state proof separately; source merge is not
  deployment or parent-effort completion.

## Journal

- Claimed the narrow marker-only no-op slice on #7743 after checking current
  source and existing bootstrap coordination. No implementation or host
  deployment has been performed by this slice.
- Implementation after reviewed plan #8714: the Bridge reference and directly
  dependent ordinary Dispatch/Logger/MCP predicates now use their already-vendored
  read-only resolver. The expected selected generation is pinned to deployment
  metadata and canonical source identity, including source payload-byte checking;
  valid package repair generations and pre-marker legacy installations remain
  supported. A healthy fallback cannot satisfy a broken selection. The sync
  checker now synchronizes and guards only this shared predicate, leaving each
  family's surrounding behavior intact.
- Parity review: the existing PowerShell counterpart already checks selected
  completion rather than requiring a POSIX legacy link; it is unchanged. Its
  broader identity/validation differences are not claimed fixed here. Dedicated
  profile, Pythonless, driver-owned and lib-copy families are likewise unchanged.
  This is a below-altitude correction to the existing immutable-generation
  contract, not a new installation or service policy.
- Host validation (unchanged runner limits): `python -B
  tools/run-plugin-tests.py runtime-source --test-file
  tests/test_bootstrap_selected_readiness.py` passed **11 controls, 89 native
  POSIX skips** (37.06 seconds pytest; 49.42 seconds including runner preparation
  and cleanup). Identity controls execute the real embedded probe, not a mocked
  readiness result. Shell controls, before/after elapsed measurements, process
  witnesses and native refusal cases remain **pending**, not proven by skips.
- `uv tool run --from ruff ruff check --select F,E9
  tools/check-bootstrap-sync.py
  libs/versioned-runtime/tests/test_bootstrap_selected_readiness.py` passed.
  The initial `uv run --no-project ruff ...` reported missing Ruff; the isolated
  UV tool invocation then supplied it without a global Python/package change.
  `python -B tools/check-bootstrap-sync.py --sync-readiness` and the ordinary
  check passed (13 plugins/13 families). `python -B
  tools/check-install-contract.py` passed (13 plugins).
- Existing contained contracts: `python -B tools/run-plugin-tests.py agent-bridge
  --test-file tests/test_bootstrap_sync_guard.py --test-file
  tests/test_bootstrap_check_reconcile_always_on.py` passed **11 tests** (25.54
  seconds pytest; 416.42 seconds including first-time UV environment preparation
  and cleanup). The existing supported Windows shell simulation is not native
  POSIX or installed qualification. Runner-owned preparation files were removed
  by normal runner cleanup; no dependency manifest or global package changed.
- **Encountered blocking guard defect, not waived:** `python -B
  tools/sync-versioned-runtime.py --check` fails on both
  `installer_dependency_policy.py` and `installer_dependency_config.py` in
  Containers, Machines, MCP and Budget Guidance. The vendored copies lack the
  canonical source-compile package-omission contract (`_SourceCompileMetadata`,
  `source_compile_packages` and related handling). This is substantive byte/
  behavior drift, not line endings. None of these files was changed by this
  slice. The parent source-integration owner must reconcile the installer
  dependency-policy vendors with their owning change and rerun the guard;
  synchronizing those unrelated installer surfaces here would exceed this
  assignment. Publication is blocked until resolved.
- Performance impact: session-start frequency is unchanged. Matching ordinary
  installs add a bounded deployment-identity Python probe and read-only shell
  resolution instead of an installer/reconcile child; source installs retain
  canonical payload hashing proportional to that payload's file set. No network,
  history scan, cache, relaxed timeout or additional background owner is added.
  Actual elapsed/child-count comparisons are authored but await native Linux.
  The independent directory-provenance parsing work is untouched.
- Documentation impact: the install contract and focused testing recipe now
  describe exact selection, identity, fallback, legacy and qualification limits.
  Changefiles cover all four changed plugins. Native Linux/clean-room/full-plugin
  evidence, containing release, installed rollout and running-state proof remain
  with the parent; no deployment or parent-effort completion is asserted.
- Parent integration resolved the eight generated dependency-policy omissions
  above. Merged #8600 (`83e229b97`) updated the canonical source-compile contract
  without those copies; the copies had not changed since #8589. After checking
  that exact mismatch set and ownership, the existing canonical synchronizer
  updated only those eight files. All are byte-identical to their owners and
  `sync-versioned-runtime.py --check` passes. Patch changefiles additionally
  cover Containers, Machines and Budget Guidance; MCP is already covered.
  No dependency policy, source-compile behavior or guard was weakened.
- Native Linux qualification of the exact staged candidate passed **99 cases
  with one intentional Bridge outer-adapter skip** through the canonical
  isolated runner: 35.86 seconds pytest, 45.06 seconds including preparation
  and cleanup. All six changed hook/test/sync source files were independently
  hash-equal to the staged source before execution. Nested Docker was
  unavailable in the owned container, so the canonical contained runner was
  used directly rather than claiming a nested devcontainer boundary.
- The first native preparation failed on the public package host's TLS
  handshake before tests ran. Retained that failure, supplied the fixture's
  explicit governed feed, and reran without increasing any test or suite
  deadline. All actual hook, refusal, generation, fallback and read-only
  controls then executed. The marker-only before/after witness observed one
  installer child versus zero; hook elapsed times were 0.152s and 0.222s.
  This proves removal of unnecessary reconciliation, not a faster hook or
  ready-to-first-turn latency.
- Real source redeployment, fresh full-plugin allocation/evaluation, required
  implementation review and CI, containing promotion and authorized installed
  rollout remain open. Native fixture execution is not an all-up installation
  or parent-effort completion claim.
- Integration with current Dev found that independently merged #8716 moved
  Logger bootstrap into the packaged Driver family. Preserve that complete
  upstream implementation byte-for-byte: this slice no longer changes Logger
  or assigns the ordinary predicate to that family. The sync roster, regression
  matrix, contract and changefile now cover only Bridge, Dispatch and MCP.
  The four-adopter results above remain historical qualification of the earlier
  candidate, not proof of this three-adopter integration. Requalify the current
  native candidate before publication; Logger's Driver lifecycle stays with
  its existing owner.
- The integrated canonical owner also includes #8820's retained-input
  deadline and native-target binding. The same eight generated copies were
  synchronized to that newer owner before continuing; the canonical guard
  passes without retaining an older policy body.
- Exact integrated source `07b7a6eaa` passed **76 native cases with one
  intentional Bridge outer-adapter skip**, in 25.91 seconds pytest and 36.20
  seconds including canonical runner preparation/cleanup. The three-adopter
  matrix exercised the actual hooks, selected/fallback/source/repair/legacy
  contracts and refusal behavior. Its before/after witness observed one
  installer child versus zero, with hook times 0.165s and 0.266s. These are
  the current integrated results; no hook-speedup or full installation is
  inferred from them.
- The parent is exercising that exact integrated source in a fresh full-plugin
  consumer with the unchanged harness contract and existing deadlines.
  Source preparation passed; runtime installation is in progress. Do not
  turn that partial stage into a clean-room pass or production rollout claim.
- The corrected fresh trial subsequently passed **all six stages** and the
  required independent literal evaluation. Both original first-attempt tasks
  were performed by their own workers, confirmed by the trusted evaluator and
  retained through supported Bridge native-history reads. No setup recovery
  was required in this trial. Source/installed-body receipts, paired allocations
  and the immutable README reference corroborate what was exercised.
- Required review and CI then accepted published head
  `cd47ef39ba60837fee814666c90fb45a798c2ffe`; #8889 actually merged as
  `86c5e69c5e26412fbcce6c7a031061b4d75d0ae8`. That published head's complete
  native selection also passed 76 cases with one intentional skip, in 35.23s
  pytest and 43.47s including preparation/cleanup. Its before/after witness
  retained one versus zero installer children, at 0.164s versus 0.434s.
  These are no-op correctness results, not a foreground-hook speedup.
- The containing normal release and authorized installed rollout still gate
  adoption. The independent fresh verdict does not claim one-minute READY,
  full role/fleet activation or final retirement, and the broader effort is
  still Active.
- Normal promotion #8897 subsequently published Main
  `e4df117084aa50298efc3248357a6d831fa091d4` from Dev
  `a79749ee98e70da62919c0c8228c9a094506453e`, which contains #8889's exact
  merge. The annotated promotion tag agrees, and all three accepted readiness
  fragments are byte-identical in Main. The slice is released, not installed:
  qualified adopter rollout remains separately gated by native updater
  qualification and its existing owner, without another updater lane.
