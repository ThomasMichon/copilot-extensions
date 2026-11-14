# Derived Disk Hygiene

## Goal

Implement and validate tracks A-D of #10968, including reviewed delivery and
explicit ownership of regenerable outputs. Reclamation is guided and bounded;
finalization never automatically deletes derived data.

- **Slug:** `derived-disk-hygiene`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-track PRs against `dev`
- **Created:** 2026-11-06
- **Status:** Active
- **Umbrella issue:** #10968
- **Sub-issues:** #10965 · #10966 · #10967

## Guiding Intent

Derived, regenerable state (test environments, bytecode caches, ad hoc
scratch) must have an owner, a bounded lifetime and a supported reclamation
path. A long-running developer machine accumulated hundreds of GiB of such
state because several producers create it without any matching cleanup,
and the documented guidance said legacy state is never reclaimed.

Automatic maintenance never deletes unclassified or retained evidence.
On-demand guided reclamation instead inventories a set, records explicit
target selections and requires a separate target-specific discard decision
for unclassified content. It never follows reparse points or deletes tracked
files. Known use or an unaccepted inspection failure refuses reclamation.
Writers must be quiesced first; process scans and rename probes are not an
exclusive mutation lock.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinating maintainer | Owns the overall plan, journal and remaining integration | #10968 |
| Track A PR driver | Legacy inventory/plans, typecheck cache placement and checker prerequisites | #11126 |
| Track B PR driver | Bytecode namespace reuse and explicit cache pruning | #11122 |
| Track C claim-engine driver | Derived claim engine; producer integration remains separate | #11103, #10967 |
| Track D PR driver | Scratch ownership, cleanup and lock compatibility | #11125 |

## Coordination

- **Topology:** independent per-track PRs against `dev`; no shared feature branch.
- **Host (owns PRs):** the coordinating maintainer retains the overall objective;
  each track's PR driver stewards its existing PR through review, CI and merge.
- **Delegates:** A, B and D own their named implementation slices. C's engine
  has merged; its producer-integration work remains on #10967.
- **Handoff:** drivers retain their PR identity and communicate source, tests
  and unresolved gates on that PR. The coordinator reconciles merged slices
  into this effort's Plan, Validation Plan and Journal.
- The source-bound cache placement and checkout-contained derived-claim
  contract must be reconciled before wiring producers. No completion or
  disposal authority is inferred from a finished sibling PR.

## Context

Measurements from one machine (sanitized):

- Legacy per-checkout `.test-venvs/` trees: ~12.7 GB across ~165 dirs,
  most in already-completed worktrees.
- `unsealed-<uuid>` Python bytecode namespaces: ~1,000–2,500 created per
  day at ~10 MB each. The dominant producer (the agent-worktrees binstub)
  was fixed by #10812; daily creation fell to near zero. Residual launchers
  still mint a random namespace per invocation.
- `%TEMP%`: ~2,600 unlabeled dirs (`agbridge-host-*`, `ce-*`, `tmp*`) and
  ~11,600 loose `.lock` files, none with ownership breadcrumbs.

## Request

Plan and implement fixes for the issues tracked in #10968, presenting the
architectural changes before implementation.

## Scope

In scope: tracks A–D below. Out of scope: track E (#2567 executor
unification evaluation), which stays on the tracker.

## Scope Approvals

The kickoff journal records the approved source-work scope. Disposable-fixture
validation does not authorize reclaiming real data or rolling out installed
runtimes. Those effects require their own bounded approval. Automatic deletion
during finalize is excluded.

## Plan

The next bounded runtime build-out follows the
[owned test-runner scratch pilot](scratch-pilot.md): scoped storage preferences,
external allocation custody, safe retained disposition, and a real Windows/Linux
runner flow. This proposal must clear source review before behavior changes; cache eviction,
installed rollout and legacy-data cleanup are outside the pilot.

### Track A — Reclaim legacy `.test-venvs` (#10965)
- [x] Standalone `tools/reclaim_legacy_test_dirs.py`: inventory root
      `.test-venvs/`/`.test-runs/` across registered worktrees, optionally
      narrowed with `--checkout`; record reviewed `--select` targets with
      `--plan-out`, requiring `--discard` for unclassified content; apply
      only that plan with `--apply-plan --confirm-writers-stopped`. Revalidate identity, metadata,
      ignored/untracked/link-free status, age and known use before removal.
      Report refusals and partial outcomes rather than claiming atomicity.
      The earlier 158-tree / 11.2 GiB inventory was not a discard decision.
- [x] Move `tools/typecheck.py` cache environments to
      `ce-tests-<key>/typecheck/<platform>/` so no current writer creates
      `.test-venvs` for environments.
- [x] Keep capture-report paths, but classify their contents as evidence
      requiring an explicit discard decision, including reports nested in
      an otherwise recognized environment.
- [x] Replace TESTING.md "neither migrated nor deleted" guidance with the
      supported reclamation command.

### Track B — Stop unsealed bytecode growth (#10966)
The [Python Bytecode Cache Lifecycle follow-on](<../../2026/11/14 python-bytecode-cache-lifecycle/README.md>)
delivered OS user-cache relocation, retirement-aware incremental collection
and normal-update legacy bytecode relief through #12145, #12506, #12569 and
#12585, following reviewed design #12131. The complete implementation is
released through #12613; that source effort is Done. The accepted #11122
delivery remains unchanged; source and fixture
qualification do not authorize real-cache cleanup or satisfy the parent's
installed-rollout and 24-hour observation gates.

- [x] Apply the #10812 selector pattern (call `cache_arguments`, reuse
      `installed-<digest>`) to remaining random-minting launchers:
      worktree-manager runtime launchers, payload-invocation shim
      templates, budget-guidance bin, agent-worktrees hook/payload
      runtime scripts, and installer-embedded launchers.
- [x] Where a launcher must still use an unsealed namespace, delete it
      after the child exits.
- [x] Supported `python-cache prune [--apply]` in `agent_procutil`
      (exposed via agent-worktrees CLI): removes `unsealed-*` dirs that
      contain only `.pyc`/`__pycache__`, are older than a grace period, and
      are not held by a live process; never touches `installed-*`.

### Track C — Derived outputs as open claims (#10967)
Finalize never deletes anything. Derived outputs are journaled as explicit
claims the worktree's agent must close out before finalize.
- [x] New `derived` claim kind on the existing outbound ledger. A producer
      that creates a derived dir records `claims add derived <path>` for its
      worktree and writes a `.derived-claim.json` marker (worktree id,
      nonce, creator, created). A pre-existing dir without a matching
      marker is never adopted.
- [x] The existing kind-agnostic finalize gate blocks while `derived`
      claims are open and lists them.
- [x] `claims settle <path> --reclaim` deletes exactly that one path, only
      when the marker's worktree id + nonce match the ledger entry, the path
      is inside this checkout, contains no tracked files or reparse points,
      and no live process holds it. Any mismatch refuses and leaves the
      claim open. `claims release <path>` keeps the dir.
- [x] No chaining: one ledger entry per settle, never another worktree's
      claims, and the reclaim sweep skips `derived` claims (like `pr`).
- [ ] First producer: `run-plugin-tests.py` (`.test-venvs` writers that
      remain after Track A). Legacy unmarked dirs stay Track A's job.

### Track D — Labeled scratch and lock hygiene (#10968)
- [x] Shared `labeled_scratch(prefix, purpose)` helper that creates a
      timestamped dir with `scratch-owner.json` (owner refs, created,
      purpose, retention) with link-safe, bounded cleanup.
- [x] Bridge Host fallback labeling and confirmed-refusal cleanup; preserve
      uncertain launches and unsettled lifecycle state for recovery.
- [ ] Mixed-version-safe projection-lock migration under #10968. Stable
      legacy locks remain under `copilot-instruction-projections`; relocating
      or unconditionally unlinking them would split waiter lock identities.
      The original unlink-after-release approach was rejected, not completed.
- [x] Redirect UV's per-source `uv-setuptools-*.lock` producer to build-local
      temporary directories in `installer_source_build.py` and
      `tools/build_python_artifacts.py`, including vendored copies.
- [x] Report-only `scratch report` listing unlabeled/orphaned temp dirs
      (no automatic deletion).

## Validation Plan

- [ ] Unit tests per track: dry-run vs apply, tracked/ignored eligibility,
      reparse refusal, live-process refusal, installed namespace preserved.
- [ ] Simple end-to-end: run each dry-run against this machine and compare
      to measured inventory; apply on a bounded subset.
- [ ] Observe unsealed creation rate for 24 h after Track B lands.

## Proposal

Keep allocation, ownership and reclamation separate. Producers label retained
state; guided tools inventory and select explicit targets; derived claims make
unfinished ownership visible without turning finalize into a deletion sweep.
The per-track Plan and Validation Plan define implementation and acceptance.

## Journal

### 2026-11-06 — Kickoff
- Effort created from #10968 findings; architecture presented for approval.
- Operator approved A, B, D as proposed. Track C redesigned at operator
  request: no automatic finalize deletion (avoid mass-delete chain
  reactions); derived outputs become open claims closed out explicitly.
  Approved.

### 2026-11-06 — Track A implemented
- Added standalone `tools/reclaim_legacy_test_dirs.py` (dry-run default,
  guarded `--apply`) instead of a runner flag (runner at module-size cap).
- `tools/typecheck.py` environments now live under the ce-tests scratch
  root; typecheck and promotion unit tests isolate `AGENT_SCRATCH_ROOT`.
- Validation: reclaim tests + promotion typecheck tests green;
  `tools/test_typecheck.py` green with a mypy-enabled interpreter (host
  Python lacks mypy; one case needed a rerun after a 120 s emulation
  timeout). Local dry run: 158 trees / 11.2 GiB eligible; no `--apply` yet.

### 2026-11-08 — Guided cleanup review and merge stewardship
- Replaced broad apply with reviewed, target-specific plans and discard
  decisions; added mutation-boundary revalidation and honest partial reports.
- Covered nested evidence, non-ASCII worktree paths, unreadable Linux
  process metadata and live mappings after descriptor closure. Focused
  reclaim suites passed on Windows and Linux; no real-data apply ran.
- Track C's claim engine merged in #11103. Producer integration remains
  open: the runner's source-bound caches now live outside the checkout,
  while the current reclaim contract only admits checkout-contained paths.
  Do not silently adopt external or pre-existing unmarked directories.
- Driving #11126 (A), #11122 (B) and #11125 (D) through review and CI.
  Reconciled the existing Windows-wrapper emitted-source declaration as a
  checker prerequisite without relaxing producer/epoch admission.

### 2026-11-08 — Track B merged
- #11122 merged to `dev` as `fca1943b4788bcb6c7cf15343edf95f9fe9c9e52`
  after required CI, current-head approval and resolution of review threads.
  Its source worktree was finalized.
- Pruning/lazy-dispatch regressions: 67 passed on Windows; focused Linux
  coverage: 25 passed. Generated shim, Manager, Vault and Index adoption
  lanes passed, along with contract/generation/lint/size checks.
- No installed-runtime rollout or real-cache pruning ran. The 24-hour
  creation-rate observation is still pending an authorized rollout; a
  source merge does not start that measurement or prove deployment.

### 2026-11-08 — Track D merged
- #11125 merged to `dev` as `cf5fcfe008e7b694720c54f634f558b637a3b496`
  with exact-head approval, required CI green and no open findings. Its
  source worktree was finalized after native landing verification.
- Regressions: 69 passed / 3 platform skips, launcher lane 6 passed, and
  POSIX scratch lane 19 passed / 1 unsupported-FIFO skip.
- Labeling, safe fallback/lifecycle cleanup, UV lock placement and report-only
  inventory landed. Projection locks intentionally retain their stable
  legacy identity; mixed-version-safe migration remains tracked by #10968.
- No production cleanup or installed-runtime rollout ran.
