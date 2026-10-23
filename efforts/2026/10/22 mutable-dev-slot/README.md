# Mutable dev slot

- **Slug:** `mutable-dev-slot`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase PRs to `dev`; normal release promotion to `main`
- **Created:** 2026-09-23
- **Status:** Done
- **Disposition:** Abandoned as superseded on 2026-10-22; unfinished acceptance
  transfers to [Immutable Dev Slot](../../../active/immutable-dev-slot/README.md)
  under the existing #5472, not claimed delivered.
- **Coordination:** #5472
- **Vision:** [`docs/patterns/mutable-dev-slot.md`](../../../../docs/patterns/mutable-dev-slot.md)
  (a deliberate, narrow exception to
  [`docs/patterns/durable-vs-versioned-runtime.md`](../../../../docs/patterns/durable-vs-versioned-runtime.md)'s
  immutable-versioned-runtime invariant)

## Guiding Intent

**Historical premise, no longer execution authority.** The operator replaced
mutable/editable development deployment with immutable, source-hashed venvs.
The text and delivered Phase 1/2 credit below are preserved as history.
Follow the successor effort for all remaining work; clean merged Dev deployment
remains owned by Source-Managed Runtime Installs (#7028/#7029).

Give every plugin one first-class, protected, genuinely mutable install slot
(`versions/dev`) a worktree can claim, iterate against with the real deployed
CLI, and release -- replacing the throwaway hot-patch-a-deployed-venv
technique contributors keep re-inventing under time pressure (see
CONTRIBUTING.md's *Hot-patching a deployed venv for fast pre-merge
iteration* gotcha), and closing the "which install actually runs" confusion
that has repeatedly cost real diagnosis time this repo's own contributors
have hit (most recently: diagnosing/fixing
ThomasMichon/copilot-extensions#3323 -> #3340).

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Slot-admission coordinator | Owns the Phase 3 shared contract, reviewed implementation slices, and adopter sequencing | Normal managed worktree and PR flow |

## Coordination

The Slot-admission coordinator drives #5472 through the existing Phase 3,
starting with [runtime admission and build coalescing](phase-3-runtime-admission.md).
Runtime code stays installer-owned and independently vendored. Evidence tracks
are read-only; implementation slices receive exclusive file ownership, and the
coordinator integrates and publishes the combined change. Phased adopters do
not imply that the portfolio or another concurrently owned effort is complete.

The operator authorized the coordinator to take over the remaining first-adopter
lifecycle on 2026-10-13. Preserve existing implementation and open pull requests;
the transfer does not supersede adjacent dependency/identity or other adopter
work. Construction and the complete runtime lifecycle belong in the existing
installer-engine home, with Python owning actual construction/retention
descriptors and its subprocess tree through publication, promotion/drain or
rollback settlement. Native entrypoints initially continue to own pre-Python
bootstrap and OS integration. This changes ownership, not the full-adopter
completion gate.

## Context

This repo's runtime is deliberately **immutable and versioned**
(`docs/patterns/README.md` §Design invariants): a plugin's own installer
builds `versions/<version>/` once, never edits it in place, and switches an
atomic `current-version` marker to select the active build. That invariant
is exactly right for real deployments, but a contributor iterating on a live
bug from a worktree has repeatedly hit the same wall: `<repo> update`
follows the machine's installed `source.kind` (marketplace vs local) and
does not see an unmerged worktree's changes at all; running a plugin's own
installer directly from the worktree DOES validate the edit, but builds a
brand-new immutable slot every single iteration; and the fallback
contributors have reached for under time pressure -- hot-patching an
already-deployed slot's files directly -- is explicitly throwaway (see
CONTRIBUTING.md's *Hot-patching a deployed venv for fast pre-merge
iteration* gotcha, itself written up after diagnosing/fixing
ThomasMichon/copilot-extensions#3323 -> #3340 this same session). See
[`docs/patterns/mutable-dev-slot.md`](../../../../docs/patterns/mutable-dev-slot.md)
for the full design.

## Request

Operator ask, paraphrased: add a `dev` version slot per plugin with a
mutable venv; a config flag (or worktree-default detection) selects it; a
worktree "claims" dev mode for a plugin so two worktrees can't clobber each
other; `agent-worktrees finalize` must force worktrees to release/disable
dev mode on finalization; none of this may break real deployments.

Operator follow-on, verbatim (2026-10-08):
> Help build out durable fixes and guidance for the correct flow

The runtime-admission/coalescing execution detail below is agent-recommended
under that request and the existing immutability contract; it is not a new
operator directive to remove the explicit claimed-dev exception.

Operator follow-on, verbatim (2026-10-13):
> Focus on driving phase 3

When asked to choose between retaining the previous reservation and taking
ownership of the remaining first-adopter lifecycle, the operator selected,
verbatim:
> take-over

The selected option was "Authorize takeover of the remaining first-adopter
lifecycle"; the coordination prompt explicitly retained existing implementation
and open pull requests. The implementation sequence remains the reviewed
Phase 3 contract, not a new parallel lifecycle framework.

## Plan

### Phase 1 — Core primitive (DONE)

- [x] `libs/versioned-runtime/versioned_runtime.py`: `DEV_VERSION`,
      `DEV_CLAIM_FILE`/`DEV_CLAIM_SCHEMA`, `dev_claim_path`,
      `read_dev_claim`, `claim_dev`, `release_dev`, `DevClaimConflict`.
- [x] `gc()` protects the `dev` slot iff a live claim exists; reclaims it the
      instant the claim is released.
- [x] CLI verbs: `dev-claim --owner <path> [--previous-version V] [--host H]
      [--force]`, `dev-release --owner <path> [--force]`, `dev-status`.
- [x] Fanned out to all 13 vendoring plugins via
      `tools/sync-versioned-runtime.py`.
- [x] Regression tests (`plugins/agent-bridge/tests/test_versioned_runtime.py`):
      claim/release/conflict/force/idempotent-reclaim, GC protect/reclaim,
      malformed-file-reads-as-absent.
- [x] `agent-worktrees finalize`'s `_warn_of_dev_slot_claims_for_worktree`:
      warn-only safety net (never auto-releases), scanning every
      `~/.*/dev-claim.json` sidecar (no hardcoded plugin list), matching on
      the worktree's checkout path, mirroring
      `_warn_of_codespace_claims_for_worktree`'s exact posture.
- [x] `docs/patterns/mutable-dev-slot.md` design doc; linked from
      `docs/patterns/README.md`.

### Phase 2 — Runtime accessibility + one pilot plugin

- [x] Resolve the open design question `mutable-dev-slot.md` flags: make
      `versioned_runtime.py` reachable from a plugin's **deployed** CLI (not
      just its installer) so `dev-release`/`dev-status` are callable without
      a source checkout present -- resolved as Option 1: the installer copies
      `versioned_runtime.py` into the plugin's own root
      (`~/.agent-codespaces/versioned_runtime.py`) on every install/update/dev,
      and `agent-codespaces`' own deployed argparse CLI gained first-class
      `dev-release`/`dev-status` verbs that shell out to it via
      `sys.executable`.
- [x] Wire ONE pilot plugin's installer (`agent-codespaces`, both
      `install.ps1` and `install.sh`) with a `dev` verb: claims dev mode for
      the calling worktree (`agent-worktrees get worktree-dir` as `--owner`,
      falling back to the repo root), builds/rebuilds `versions/dev` in place
      (an editable/`-e` install against the worktree's own checkout for all
      4 path-dependencies, not a fresh venv every call), marks it complete,
      and activates it.
- [x] Wire the pilot's `dev-release` CLI verb (on the DEPLOYED CLI, per the
      resolved design): `release_dev`, then `activate(previous_version)` to
      restore the machine's real deployment. Refuses a conflicting owner
      without `--force` (exit 2).
- [x] Live-validate end to end (not just unit tests): claimed dev mode on this
      machine's real `~/.agent-codespaces` deployment, confirmed
      `agent-codespaces version`/`current-version` flipped to the editable
      `dev` build (picking up a live source edit -- the `--json` ordering fix
      below -- with zero rebuild), confirmed the deployed CLI's own
      `dev-status`/`dev-release` verbs work, released, confirmed
      `current-version` restored to the real prior deployment
      (`0.4.0-dev163`), and confirmed `gc()` reclaimed the now-unclaimed `dev`
      slot. Caught and fixed a real bug in the process: PowerShell's
      `$x = if (...) { @('one') } else { @('a','b') }` silently unwraps a
      single-element array branch to a scalar, so `@x` splatting then
      iterated the string CHARACTER BY CHARACTER (`uv pip install --python
      $py -e ...` became `-`, `-`, `e`, ... one arg at a time) -- fixed by
      wrapping the whole conditional in `@(...)`. Also caught that
      `versioned_runtime.py`'s `--json` is a GLOBAL flag that must precede
      the subcommand, not follow it.
- [x] Update CONTRIBUTING.md's hot-patch gotcha to point at the pilot
      plugin's `dev`/`dev-release` verbs as the preferred path once proven,
      keeping the hot-patch note only for plugins that haven't adopted the
      pattern yet.

### Phase 3 — Rollout to the remaining vendoring plugins

- [x] Finish publisher prerequisite #5817 before runtime-admission implementation:
      an updated, legitimately owned existing PR must publish through one
      supported command, including compatible legacy evidence reconciliation.
      Internal ownership bookkeeping must not require a manual old-tip,
      re-publish, restore-tip sequence. Preserve exact saved leases, destination
      and worktree/ref authority, real concurrency refusal, and normal hooks.
      Initial delivery verified on 2026-10-12: #5835 and publication repair #6256
      are merged; normal release promotions #6263/#6267 are merged; unified
      update selected agent-worktrees `1.24.50-dev1` and the installed CLI
      exposes `--rewrite-pr`. Independent promotion blockers #6244/#6262
      were resolved before those releases. Consumer guidance refresh cleared its
      reviewed flow; the merged plan's PR claim was reconciled.
      The in-scope revised-legacy-record correction #6299 subsequently cleared
      normal promotion #6358 and verified publisher activation. The selected installed
      agent-worktrees `1.24.52-dev1` passed all 21 legacy rewrite contracts,
      including revised records, exact live-authority refusals and real Git
      publication, without importing the source-checkout implementation.
      The 2026-10-12 revised-legacy delivery journal below records that proof.

- [x] Deferred to `efforts/active/immutable-dev-slot/README.md`: Finish executing the shared, reviewed
      [runtime-admission/build-coalescing slice](phase-3-runtime-admission.md)
      through the shared core
      and phased adopter changes. This closes published-slot overwrite and
      transient first-builder contention at their owning seams, while preserving
      the full per-plugin guard/dev/distinct-generation repair gate below.

- [x] Deferred to `efforts/active/immutable-dev-slot/README.md`: Repeat Phase 2's pilot shape for the other 12 plugins currently
      vendoring `versioned_runtime.py` (see `tools/sync-versioned-runtime.py`'s
      target list; 13 vendoring plugins total, `agent-codespaces` is the
      one Phase 2 pilot) as their own installers need it -- not a mandatory
      blanket rollout on day one. This includes `budget-guidance`, the one
      vendoring plugin that isn't `agent-*` and so isn't named in the
      audit-prioritized list below -- it's still owed this same rollout
      under this generic bullet.
- [x] Deferred to `efforts/active/immutable-dev-slot/README.md`: **Prioritize the plugins a separate invariant audit
      (`ThomasMichon/copilot-extensions#5472`, `#5468`,
      `efforts/2026/10/09 vision-backport-sweep/`) found still silently
      mutating a completed numbered slot in place for a same-version,
      changed-content update — exactly the anti-pattern this whole effort
      exists to replace.** These need BOTH the ordinary-installer refusal
      guard (see `docs/patterns/mutable-dev-slot.md`'s new *Ordinary
      installers* section) AND a wired `dev`/`dev-release` verb pair as the
      sanctioned alternative, not just one or the other:
      `agent-dispatch` (`#5356`), `agent-vault`, `agent-worktrees`,
      `agent-mcp`, `agent-index`, `agent-bridge`, `agent-logger`,
      `agent-ssh`, `agent-containers`, `agent-machines`. (`agent-codespaces`
      is the Phase 2 pilot, already wired for `dev`/`dev-release`, but per
      that same audit still needs the ordinary-installer refusal guard
      layered on top -- the two are independent, and wiring one does not
      imply the other.) `agent-pull-requests` already has the refusal
      **control flow** but needs its own `Get-PayloadHash`/`_payload_hash`
      widened to cover the full runtime install input (currently only
      `pyproject.toml` files, missing `src/` -- see
      `docs/patterns/mutable-dev-slot.md`'s *Ordinary installers* section)
      before it can be held up as done, and has not adopted
      `dev`/`dev-release`.

## Validation Plan

- [x] Deferred to `efforts/active/immutable-dev-slot/README.md`: **Phase 3, per adopting plugin, on both installer entrypoints
      (`install.ps1`/`install.sh` for most of the list; `init.ps1`/`init.sh`
      for `agent-mcp`, `agent-containers`, and `agent-machines`):** a
      matching-content same-version run is a true no-op (no rebuild, no
      reinstall, normal out-of-slot reconciliation still runs); a
      changed-content same-version run is refused even under
      `-Force`/`--force`; a direct-health-check failure (not merely a
      content mismatch) cuts over to a distinct, correctly-propagated
      generation per *Repairing a broken numbered slot*'s contract, rather
      than falling back to the old `--force` rebuild-in-place recovery; the
      `dev`/`dev-release` cycle builds, activates, and then correctly
      restores the prior `current-version`. Not satisfied by implementing
      only some of these for a given plugin -- the refusal guard, the
      distinct-generation repair path (and its operator-guidance
      reconciliation), and `dev`/`dev-release` are all required before
      checking that plugin off above.
- [x] `python tools/run-plugin-tests.py agent-bridge -k dev` -- new
      dev-slot primitive tests pass.
- [x] `python tools/run-plugin-tests.py agent-bridge` (full suite) -- no
      regressions beyond pre-existing, previously-documented unrelated
      failures.
- [x] Phase 2: a live pilot-plugin `dev`/`dev-release` cycle, observed
      directly (not just unit-tested) -- see Journal below.
- [x] `agent-worktrees finalize` on a worktree holding a live dev claim
      prints the warning with the correct plugin name and release command,
      and does NOT silently release it -- covered by Phase 1's own
      `test_warn_of_dev_slot_claims_for_worktree.py` (7 tests, already
      green); the warning fires only in the real (non-`--dry-run`) finalize
      path, so it was not re-exercised live here to avoid actually retiring
      this worktree mid-effort.

## Journal

### 2026-10-22 -- Abandon mutable deployment; transfer valid acceptance

The operator directed: "Great. Update relevant visions and efforts to align;
close out and abandon superseded efforts and off-course work." The complete
new direction is captured verbatim in the successor effort. Mutable/live
editable deployment is abandoned, not a required compatibility target for new
work. Delivered primitives, construction/retention custody, first-adopter work,
and claim restoration remain useful and are not reverted. Every unresolved
checklist transfers explicitly below; none is being reported as implemented.
The archived Phase 3 detail is historical evidence, not a competing plan.
Existing #5472, #6424/#6389 lifecycle custodians, #7028/#7029 source deployment,
and #7758 execution-driver owners retain their valid responsibilities.
No runtime, installed generation, live claim or worker is changed by this
documentation disposition.

### 2026-10-19 -- Integrate the canonical retained transaction and current source identities

The Python constructor now consumes `retain_uv_install()`'s admitted argv,
environment, working directory and retained graph directly, with one owner
through child settlement and installed health. It checks retained file coverage
and content against the admitted source identity; fresh origin receipts name
that same graph. Exact reuse remains independent of UV and build tools. The
production child deadline remains 180 seconds. The explicit copy utility is
not used to construct a second graph.

The first-adopter lifecycle also validates source receipts, publishes the
canonical source snapshot and selects its exact source-qualified generation.
Package and repair identities remain separate, and installed deployment
evidence retains the source document for later restoration. Immutable monitor
bootstraps now carry the newer split runtime modules, not just their importing
entrypoint. Configuration enumeration and source-generation ordering moved into
their existing owning modules; every unchanged module-size ceiling passes.

Focused policy/runner tests passed 284 cases with two platform skips before
the cohesive extraction; the affected configuration/retention selection passed
48 afterward. Source identity's full native suite passed 61 cases after the
ordering extraction. Fast lifecycle/mode/generation contracts passed 59 cases,
and all 32 constructor contracts passed. These are scoped results, not full
adopter acceptance. A 509.78-second whole runner still failed two native cases:
Git Bash's admission/re-exec path exposed a Store alias and Windows-path
conversion, and the fixed 120-second synthetic monitor request lost its socket.
Admission now probes working interpreters and normalizes MSYS absolute paths;
the retention fixture uses a controlled real-server request, released only after
both kernel-custody assertions, instead of spending 120 seconds asleep.
Native reproof now passes complete construction and tool-free reuse through
Windows PowerShell, PowerShell 7 and Git Bash, including unchanged-source
assertions. The controlled real-monitor retention/drain case passed separately
in 131.20 seconds. The subsequent two-mode gate failed during development
construction: nesting a control transaction under a drive-root working directory
retained the entire caller ancestry again, producing backend directories beyond
Windows process-creation limits (`os error 267`). Admission now runs from its
attributable package root; a nested control/dev regression and two real
construction/origin contracts pass with bounded retained paths. Native failure
capture also preserves non-UTF-8 diagnostics instead of masking the build error.
The corrected candidate `7264891db` passed both real Windows
source-removal/deployed-release modes: 2 passed in 1,229.15 seconds, with
1,245.70 seconds for the complete runner including preparation and settlement.
UV's 180-second child deadline and the existing per-case deadline were unchanged.
This explicit integration result does not satisfy the ordinary portfolio's
whole-run 600-second target.

Full CI on the preceding candidate completed 34 jobs successfully. Its two
runner failures shared the now-restored upstream worker-options contract.
Worktrees then reached three bootstrap fixture failures, while the foundation
lane exposed six Bash ownership mismatches. The stale project-stub assertion
duplicated and contradicted the newer installed-user-command contract and was
removed in favor of that complete check. The health injector now distinguishes
read-only UV settings probes from real installation. Early admission still
probes the selected Python command, but preserves a valid launcher rather than
unwrapping it and losing its environment.

Exact candidate `18a25bda5` then passed both native Windows modes in 1,127.40
seconds. Full CI completed 37 jobs successfully; Worktrees completed 12 of its
21 groups with 8,174 passing tests before the unchanged 900-second aggregate
budget terminated the next group. No test assertion failed in that run.
The operator selected only reuse of the existing, declared two-worker policy
for manual Dev/managed-PR candidate validation, not the backed-up portfolio
campaign. Legacy Main/tag/unqualified validation remains serial; all tests,
groups, item/group/plugin and resource limits remain unchanged. The focused
declaration and eligibility contracts pass before hosted qualification.
The shared CI owner's published macOS/gate hunks are disjoint and preserved;
the exact worker-hunk coordination is recorded on #5694.

Full exact-head CI and actual review remain required. The portfolio's whole-run 600-second and
daily-container obligations and every remaining adopter/release gate stay open.

The two real Windows lifecycle modes also passed on `1b83af85a` in 1,103.67
seconds. Its full CI finished 38 jobs successfully but exposed a stale
PowerShell deployment fixture and six newly registered Pull Requests context
copies that had not received the canonical update. The fixture now executes
the complete PowerShell block as a command argument rather than an incomplete
stdin statement; it also uses the existing native Bash resolver and path
conversion instead of a Windows WSL alias. Canonical synchronization updates
the six copies without changing their source contract. The review's remaining
worker assertion now calls the existing shared two-worker contract.

Native follow-up exposed an additional cold-hook stamp deadline failure:
nested bytecode caches were copied despite the stamp's top-level cache
exclusion. Recursive stamping now omits those caches, retaining every other
payload path and byte; the existing native copy regression proves both
properties. The cold hook completed in 6.36 seconds under its unchanged
10-second lifecycle deadline. The combined deployment, cold-hook and native
copy selection passed 15 cases in 14.62 seconds. These corrections require
fresh hosted qualification and exact-final-head lifecycle proof; no previous
pass is attributed to the corrected head.

The subsequent reconciliation incorporates the landed unit-only source CI
from #7269 without changing that workflow. Its removed full/native jobs
supersede this PR's worker-policy expression and workflow-only assertions;
those authored additions are withdrawn, not translated into a new integration
PR gate. Runtime tests and their existing deadlines remain intact. Remaining
source qualification uses the current required unit/guard pipeline, the
existing manual container integration route, and actual native Windows
acceptance. A green source-unit gate is not full/native validation or Main
activation evidence.

Exact candidate `ba4988f5b` passed both real Windows lifecycle modes in
1,283.70 seconds, with unchanged construction and case deadlines. All four
current source-CI jobs, including the selected unit/coverage packet, passed.
The subsequent review identified normalized `.devN` ordering and an escaping
monitor retention-validation exception; both are corrected at their owners.
Canonical and bespoke Python/shell comparators recognize both supported dev
spellings, and monitor spawn validation uses its existing visible failure
boundary so callers retain their fallback. Native resolver/monitor contracts
passed 48 cases; focused runtime ordering passed eight.

The exact-head manual container run exercised all nine Linux plugins: seven
passed. Worktrees' three cold installer cases could not fetch dependencies
inside the disconnected execution profile; Containers' two non-RAM negative
fixtures were created on that profile's tmpfs. These match the already recorded
`network-installers` and `non-ram-fixture-tier` residuals in
`tools/periodic-integration-inventory.json`, owned by #1303. The run remains
partial and failed, not full/native qualification. No network permission,
fixture exclusion, filesystem safeguard or deadline was relaxed. Those
capacity obligations remain with the portfolio owner; this component does not
claim to complete them.

### 2026-10-18 -- Recovery preserves WIP and proves both native dev lifecycles

The operator released the backlog-related waiting holds and authorized recovery
of existing PR #6707, including backed-up history reconciliation. The complete
unfinished portfolio wave was committed before touching history (`d1028d447`);
the original published candidate and the combined pre-rebase content also have
separate local backup refs. The portfolio wave remains owned by
`test-portfolio-rationalization` (#1303), not completed or silently discarded.
Its whole-run 600-second and daily-container acceptance gates remain open.

Executed the real Windows
`test_real_native_dev_refreshes_source_and_release_restores_original` matrix:
**2 passed in 982.48 seconds**, legacy 448.81 seconds and namespaced 533.31
seconds. Both cases constructed numbered and claimed-dev runtimes, observed
source refresh, removed the owning source, exercised deployed status and
conflicting-owner/bad-origin refusal, then restored the original deployment
graph and immutable marker. The production UV child deadline stayed **180
seconds**; these larger measurements cover each entire multi-step lifecycle,
not one UV invocation. This successful execution does not establish the cause
of the earlier timeout, a cold-cache performance fix, or acceptance of the
subsequent rebased candidate.

Current `dev` contains newer mandatory dependency seasoning/retained UV
transactions (#6598/#6886), immutable merged-source identities and native exact
selection (#7080/#7139), and source-owned artifact custody (#7005). A
squash-first rebase exposed real semantic overlap, not just generated-copy
drift. Reconciliation must retain those contracts across all fourteen
consumers. In particular, the Python constructor currently verifies origins
against its own frozen graph, whereas `run_uv_install` retains another graph
and frozen resolver configuration. Calling raw UV after preflight would bypass
the new transaction contract; merely substituting that helper would change the
origin/custody model. The operator explicitly selected **the canonical UV
transaction as the single retained-graph owner**, rather than extending it to
accept a separately owned graph. Continue by adapting Python construction,
installed-origin evidence and source-independent release to that owner; do not
add a second snapshot framework or restamp sources.
Exact-head native/full CI and review remain required after that reconciliation.

PR #6707 is still draft because implementation/evidence gates are unfinished,
not because of the released backlog hold. No host runtime update, service
restart, rollout, new PR, deadline waiver, or parent completion is claimed.

### 2026-10-14 -- Partition the measured native aggregate, not its deadlines

Exact-head full native CI run `38599001689` against
`b49e69a9261643e541d05149850bfb4b653b6f70` passed every other lane.
Worktrees' old 25-file group started at 17:07:49 UTC; both POSIX native dev
cycles and intervening contracts had passed by 17:11:05. Its unchanged
300-second aggregate expired during real monitor drain, leaving that item only
104 seconds, versus its independently observed 129-second passing call in the
previous matrix. This is now a measured grouping problem, not a reason to
increase a deadline or omit a test.

The runner partitions the two heavy native lifecycle files into independent
groups while preserving sorted full membership and all existing item, group
and plugin budgets. Filtered invocations keep their existing single-group
behavior; a missing declared independent file fails explicitly. Grouping
contracts cover both modes and exact membership.

Unmodified real construction under the nested Windows ownership boundaries
passed once on both registered Python 3.12 and cached Python 3.14, in 117 and
127 seconds. That counterprobe does not reproduce the native refresh path or
establish a Python-version fix. The uninstrumented native namespaced cycle
again passed the first dev install but expired the unchanged 180-second UV
deadline during its second dev refresh, before source-free release.

A separate copied-fixture-only newer-base diagnostic advanced through both
native dev installs, then exposed Windows read-only Git pack files during
fixture source deletion. Fixture cleanup now clears only a Windows
`PermissionError` beneath that copied owner's `.git` before retrying the exact
operation; other errors still propagate. Four profile/cleanup/budget contracts
pass. No installed source or host Python selection was changed, and this
diagnostic is not deployed-runtime acceptance.

After the fixture-only Git cleanup correction, the newer-base diagnostic
completed the entire namespaced native cycle, source deletion and deployed
release with original UUID, manifest, marker and origin restoration in
927 seconds. The temporary probe was removed without publication. This
exercises retained installed lifecycle code but bypasses normal signed-base
selection in the copied fixture; it does not close the unmodified Windows
deployment gate or establish the cause of the Python 3.12 backend timeout.
No host interpreter upgrade is authorized or performed.

The full local runner contracts initially reported a missing descendant
startup receipt in their separate five-second Windows timeout-tree fixture
while the native cycle was constructing packages. After all native workers
settled, the unchanged complete runner contracts passed 25 tests with one
POSIX-only skip. The earlier missing-receipt observation remains an
unclassified startup-boundary failure, not a claimed grouping regression fix.

### 2026-10-14 -- Native dev release passes; combined governance fixture corrected

Full native CI run
`38593292238` against exact candidate
`7b1e473a93e99e71c72e502a3e78afef12edc653` passed both native runner
contracts, all other plugin/service lanes, and Worktrees groups 1-12.
The POSIX legacy and namespaced native dev/source-free-release cases passed
in 41 and 61 seconds; real monitor-drain retention passed in 129 seconds.
The prior group-10 aggregate expiry did not recur. No grouping or budget
change is justified by that result.

Group 13 exposed the same account-home isolation omission in the separate
combined construction/repair/cutover/governance fixture. Its cutover worker
loaded the original source helper instead of the copied payload, while both
that helper and the installed baseline saw the real passwd home rather than
the isolated fixture profile. Governance correctly refused the foreign root
before taking cutover authority. The fixture now shares the native dev test's
copied OS-lookup isolation, applies it before real construction freezes either
graph, and launches workers against the copied payload's scripts. Production
account-home resolution and authority checks are unchanged.

Three focused fixture/budget contracts pass on Windows. The separate real
Windows governance-race attempt still expired at the unchanged 300-second
contained aggregate deadline; that is not a completed Windows proof.
The corrected full POSIX matrix, Windows deployed source-free release,
canonical portfolio adoption and normal release/installed proof remain open.

### 2026-10-14 -- Native aggregate timeout needs a current-item witness

Automatic PR run 38588429018 passed against the shell-fixture correction.
Full run 38588564136 passed every other lane, but Worktrees' tenth 25-file
group reached its unchanged 300-second aggregate deadline without an assertion
report. The native fixture, real builds and 120-second monitor drain share that
group. Quiet dots do not establish which call consumed the remaining time or
prove that the namespaced lifecycle completed.

The existing full Worktrees CI lane now reports its current pytest item and
completed-call durations; runner progress is unbuffered. The full nine-plugin
matrix, selection and all deadlines remain unchanged. Acceptance still owes
a terminal exact-candidate result, not an inferred pass or a timeout increase.

Public CPython history also identifies a distinct, later WMI worker-data race:
python/cpython#134313 copied the shared query-data structure in May 2025,
with 3.13/3.14 backports python/cpython#134396 and python/cpython#134397.
The 3.12 branch has no later WMI-module change after the query-string-only
python/cpython#126203 fix already present in 3.12.10. This is a concrete
version-specific hazard worth a controlled construction comparison, not proof
that it caused the sampled backend hang or authorization to force another
base, modify Python, or upgrade the operator's machine.

### 2026-10-14 -- Kernel exit fix passes; native shell profile fixture isolated

Exact-head full run 38584999329 passed both native runner jobs, repository
guards including the shared ZDD suite, all eight other full plugin suites,
standalone Worktree Manager and service jobs. Automatic PR run 38584869452
was also green. The Worktrees lifecycle group had 903 passes and one failure:
the real namespaced development fixture reached its first Bash entrypoint,
where the independent pure-shell validator still correctly consulted passwd.
The Python account-lookup isolation did not affect that native consumer.
The fixture now also pins only the copied shell's final account-home lookup,
before freezing inputs; production identity and authorization remain unchanged.
The earlier real monitor drain/retention failure did not recur with native
pidfd exit evidence. The legacy source-deletion/deployed-release case passed,
but namespaced and Windows deployed lifecycle acceptance remain incomplete.

A separate temporary nested-build probe exercised real UV/setuptools metadata
under both outer test containment and inner installer Job custody. Its explicit
early `platform.system()` calls all returned within 0.83 seconds; after
metadata and most wheel builds, one backend exited with `0xc0000005`, not the
prior WMI timeout. Sampled wheel frames progressed through copying and archive
writing. This altered call ordering is a counterprobe, not a baseline fix or
proof of why the original backend hangs. The diagnostic was removed and never
committed. No timeout, interpreter selection, system service or production
backend workaround was introduced.

### 2026-10-14 -- Full native matrix reaches the real POSIX adopter

Full candidate run 38576292156 reached the real Worktrees lifecycle group
after the prior bootstrap fixture fixes. Eight other full plugin suites,
standalone Worktree Manager, both service platforms, and repository guards
passed. Worktrees exposed two distinct failures: the namespaced fixture
declared an isolated profile while the POSIX child correctly used passwd
identity, and a committed, drained monitor transition retained installer
custody while its parent left the exited predecessor unreaped.

The fixture now isolates only its owned copies' operating-system profile
lookup, before freezing either input graph; installation authority and
production lookup rules remain unchanged. Shared Linux liveness now probes
the kernel's pidfd exit signal instead of treating an unreaped PID as live.
A regression deliberately leaves its child unreaped while asserting native
exit, and unit cases distinguish a live process from that terminal evidence.
The actual lifecycle and drain assertions still require exact-candidate CI.

Automatic PR run 38576262955 separately exposed one stale payload-scope
assertion of the Windows job's prior seven-minute budget. It now checks the
already-measured ten-minute job budget; Linux and production deadlines are
unchanged. The full native run also had one cold Windows read-only helper
exceed its existing fifteen-second deadline. All eighteen unchanged local
read-only/binding cases passed within that subprocess deadline, but local
collection plus calls required 186 seconds and did not fit the CI aggregate's
120 seconds. A single-compilation experiment did not improve cold helper
timing and was removed rather than shipped speculatively. The Windows
read-only timeout, nested backend blockage, full adopter acceptance,
portfolio rollout and authorized released-runtime proof remain open.

### 2026-10-14 -- Identify the unfinished Windows backend, not just its deadline

A temporary, isolated diagnostic forwarded each copied fixture's declared
setuptools backend while recording bounded `faulthandler` samples to files
outside the frozen inputs. The same native namespaced sequence passed original
construction and its first development iteration, then hit the unchanged
180-second build deadline in its PowerShell 5.1 refresh. Two unfinished
metadata processes repeatedly showed Python 3.12.10 `platform._wmi_query`,
called by `platform.system()` during setuptools' vendored
`jaraco.context.robust_remover` import. The copied diagnostic backend did not
waive fingerprint, origin or publication checks and is not a shipped fix.
Its source was removed after the contained process tree settled.

Standalone calls to that interpreter's `platform.system()` completed in
0.238 seconds ordinarily and 0.284 seconds under the installer's exact owned
child Job. Therefore the evidence does not establish a generally unhealthy
WMI service or justify an automatic service restart, interpreter replacement,
platform monkey-patch or deadline increase. The remaining diagnosis must
explain the nested backend context; the Windows deployed gate stays open.

The third native CI run,
[38568141163](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38568141163),
passed the full guards and bootstrap/watchdog sub-suite. Worktrees reached
sub-suite five, then exposed two more coupled fixtures: a blanket sibling-loader
mock omitted the real version-ordering primitive, and a stable-origin source
assertion still expected the pre-normalization shell variable. Both now retain
their actual contracts. The Windows diagnostic scope assertion was updated for
the measured enclosing ten-minute job while preserving its three- and
two-minute step bounds. None of these results closes the complete native
adopter or portfolio gates.

The fourth full run,
[38572893150](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38572893150),
passed every job except Worktrees, including the complete Windows runner/fleet
lane and the full guards. Worktrees reached sub-suite six, where four bootstrap
fixtures still returned a literal sentinel instead of the interpreter-resolution
probe's executable path, or allowed an ambient second Python candidate to
interfere with a deliberately chosen fallback. Their fake interpreters now
answer that probe, and the fallback fixture controls its candidate PATH while
retaining real path/symlink exclusion. The local selection passed nineteen
contracts with one platform skip. This does not substitute for the remaining
native sub-suites.

Twelve concurrent standalone platform queries also completed normally, both
without and with owned Jobs. Thus neither Job custody nor concurrent queries
alone reproduced the sampled nested backend blockage. The signed automatic
base selected by the native installer is the newest registered Python; its
selection was not changed merely to evade that unresolved diagnosis.

### 2026-10-14 -- Native bootstrap reached; remaining CI failures classified

The second full candidate run,
[38563025573](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38563025573),
exercised real lean provisioning and namespace-package rejection on Ubuntu with
the materialized payload and real UV construction. Both passed. The watchdog
fixture reached installed health and observed its bounded stage timeout, but
its final namespace assertion failed because the injected stall preceded the
fixture's corruption. The fault now precedes the stall; neither a missing
health invocation nor an earlier construction timeout can satisfy the test.
Worktrees still stopped in sub-suite four of sixteen, so the complete
dev/source-removal/deployed-release fixture remains unexercised by CI.

The remaining guard failures were two maintenance subprocess calls lacking the
shared headless helper and a minimal CWD fixture missing the newly separated
authority/input roots. Those calls now use `no_window_kwargs`; the generation
diagnostic formatter moved to its existing identity owner to preserve the
maintenance module's ceiling. Both minimal native staging fixtures initialize
the same roots as their real callers.

The Windows fleet lane passed its runner and provenance selections, then hit
its 120-second aggregate bound partway through the full fleet portfolio.
The same contained standalone suite passed locally: 604 passed, six skipped
in 114.44 seconds, leaving no credible hosted-runner headroom. Only that
Windows aggregate is now 300 seconds, with its enclosing job ten minutes;
Linux remains 120 seconds, individual test bounds and the nine-plugin full
matrix are unchanged. This is not a runtime construction deadline change.

The integrated local follow-up passed 256 contracts with six platform skips;
fleet-CI/headless guard tests passed 58. Full native CI must still validate the
next published head. Eight other full-plugin suites and the standalone
worktree-manager again passed, but neither their results nor the real bootstrap
slice completes the first adopter, the portfolio, or the active effort.

### 2026-10-14 -- Native CI candidate published as a draft

The Python-owned runtime-admission and deployed dev-lifecycle candidate is
published in #6707 as a functional draft. Publication reconciled current
command-lifecycle admission and resident-roster reads with the candidate's
source/authority separation and generation evidence. Both sides' behavior
is retained; the tightened Worktrees runtime and dispatcher module ceilings
remain unchanged. Release intent now includes the standalone worktree-manager
consumer as well as the thirteen runtime plugins.

After reconciliation, the exact non-adopter selection passed 425 contracts
with three skips, covering lifecycle, generation, reconciliation, monitor
cutover and resident-read behavior. Native deployed Windows restoration remains
unproved: refresh construction previously exceeded the unchanged UV deadline
before source deletion or deployed release. Isolated backend or stream-capture
success does not close that gate.

The existing full CI workflow was dispatched without inputs or matrix changes
as [run 38557786361](https://github.com/ThomasMichon/copilot-extensions/actions/runs/38557786361),
against candidate `22aa858bf36d2c908c162f274ec6de0838c7a7b8`. Its nine-plugin
Ubuntu matrix includes the full Worktrees suite. The run failed in the
foundation, Windows provenance, and Worktrees jobs; the other eight full-plugin
suites and worktree-manager passed. Worktrees stopped in sub-suite four of
sixteen, before the new complete native adopter fixtures.

The failures exposed coupled test seams left on the old shell builder:
command-lifecycle fixtures still mocked the removed construction function and
assumed `stamp` was the first dispatch arm; bootstrap guards still expected
shell-owned UV and activation. Their replacements follow the Python owner.
The installed-payload fixtures now use the release materializer to close
canonical dependency references, rather than staging a raw dev plugin without
its external libraries. Lean provisioning uses real package construction.
Namespace-health and watchdog fixtures inject their fault only at the actual
installed health invocation and assert that boundary was reached, so an earlier
construction failure cannot masquerade as a health or teardown pass.

Those fixture changes pass the local bootstrap selection (21 passed, four
platform skips) and the Windows native command-lifecycle selection (four passed,
four platform skips). Their POSIX behavior still requires the next native CI
head. The draft is not ready for review or merge; source-free deployed release,
the complete adopter portfolio, normal promotion and authorized installed proof
remain open.

### 2026-10-13 -- Codespaces module-launch health prerequisite merged

#6565 merged into `dev` as
`1ed68902d3b50999368c7e3f3eda0d14e1447330` after exact-head Copilot approval
with zero open findings and every required check successful. Both Codespaces
installers now gate ordinary and claimed-dev publication on the actual isolated
`python -I -m agent_codespaces --help` entrypoint. Successful package import is
not sufficient: a partial namespace package can import while its CLI entrypoint
is absent or broken. A completed numbered slot that fails launch is refused
before venv/package construction, including forced updates. Completion evidence
and selection remain unchanged; no published identity is repaired in place.

The relevant contained Windows installer/development lanes passed 65 tests with
21 platform skips. The final launch matrix passed six native cases with six
platform skips, covering healthy launch admission, namespace packages, broken
entrypoint imports, source/PYTHONPATH shadowing, invalid interpreters and
pre-construction refusal. All 13 install contracts and touched Python lint
passed. Two existing recovery fixtures now prove the already-landed canonical
selected-incomplete distinct-generation refusal and preservation, rather than
expecting a selected/rollback-pinned identity to be rebuilt in place.

The exact-candidate, agent-free Linux clean-room scenario passed all five
assertions: fresh real provisioning, installed module origin/content matching
the candidate, importable but unlaunchable namespace evidence, actual forced
update refusal, and byte-identical completion/selection evidence. The runner
removed the disposable container. Earlier attempts failed on CRLF fixture
bytes, public-index TLS and build-backend writes against a read-only development
mount; they are not passing evidence. The successful scenario uses LF, the
explicitly supplied configured index and the established writable,
byte-identical candidate-copy pattern, without dependency-floor overrides or
deployed-runtime patches. Exhaustive activation cases remain opt-in T3.

Normal promotion and authorized installed proof are still pending. The
separately driven #6454 update acceptance remains blocked on normal release
gates, with the latest observed receiving-side configuration repair already
owned under #6556. This journal does not authorize a second updater or
claim the merged health correction is deployed.

First-adopter publication-result propagation #6447 is also merged, but neither
that milestone nor the Codespaces health prerequisite completes an adopter.
Healthy completed-slot content admission, real construction/coalescing and
retention descriptor lifetimes, dev/release, distinct-generation repair and
generation propagation remain open. The Python-owned installer-engine lifecycle
and existing bridge refusal slice retain their current owners. The next stretch
is complete first-adopter integration and its actual lifecycle validation;
all full-adopter Plan/Validation checkboxes and the parent Status remain Active.

The operator subsequently authorized takeover of the remaining first-adopter
lifecycle after supported attribution lookups did not resolve its live owner.
The coordinator now retains that remaining implementation and validation
obligation directly, while preserving existing code, open PRs and adjacent
adopter ownership. This is not permission to overwrite another contributor's
active expression or declare the Phase 3 gate complete.

### 2026-10-14 -- Separate dev inputs from installation authority

The mode bootstrap now checks dev ownership against the actual input worktree,
not the unrelated authorized installation payload. Its installed control build
carries the captured source path, commit, branch, dirty state, and package
version into candidate construction. Native context staging preserves both
roots: construction uses the source adapter and inputs, while receipt validation
continues to use the authorized payload. PowerShell context-child output is
captured on both streams before waiting and forwarded after settlement.

The focused mode contracts passed (10 tests). A real namespaced attempt also
constructed the original and entered dev through PowerShell with the extra
original-only dependency absent from the dev graph. That attempt then rejected
a Git Bash namespace refresh: the POSIX validator cannot consume native Windows
receipt paths. The Windows namespace fixture now uses both PowerShell hosts;
the legacy fixture retains Git Bash, and actual POSIX namespace validation is
still owed on a native lane. Neither matrix substitution nor a partial run
closes the source-deletion/deployed-release gate.

Other attempts timed out in different UV backends or in the outer test budget.
An instrumented real build retained verbose backend diagnostics on failure.
Production logging was restored after capture; its UV-child and context-child limits remain
180 and 480 seconds. Namespace validation/staging occur before that context-child
clock, so the fixture's separate aggregate budget accounts for them. Profiling
the unchanged full graph found 957 files (about 16.6 MB) and a 108-second freeze
in one run, including two cold hash passes of about 43 and 50 seconds. A warm
hash took about 6 seconds; repeated parent resolution accounted for only about
0.3 seconds, disproving that proposed bottleneck. No hashing coverage or
production deadline was weakened.

The larger contract run exposed a stale native provision assertion, now updated
to require UV bootstrap before the Python-owned runtime call. Its independent
whole-graph freeze test now has a bounded 180-second aggregate budget, after
measuring a complete 108-second freeze rather than assuming the former
90-second test envelope covered it. This changes no production operation budget
or content coverage; neither earlier failure is treated as a passing gate.
The final namespace/legacy release rehearsal, combined adopter
gate, native POSIX/CI, portfolio adoption, normal release, and installed proof
remain open.

The latest exact non-adopter contract set passes (136 tests, one platform skip);
the final PowerShell guard set also passes (34 tests, one platform skip).
Both PowerShell hosts drained 100,000 characters from each redirected stream
in a real bounded capture check. These do not replace the separately open
actual installer/drain and full lifecycle tests.

With the corrected aggregate fixture budget, the real namespace run completed
its original construction and first PowerShell dev build, then timed out in
the second PowerShell host's UV refresh. Verbose evidence identifies `agent-zdd`
as the only unfinished package: its setuptools 84 metadata hook wrote
`SOURCES.txt` but did not advance to wheel construction before the unchanged
180-second limit. An isolated owned build of that backend completed in about
14 seconds; this is not an explanation of the integration failure. A supported
serial-build configuration also failed the real control build, so it is not a
workaround or adoption proof. Source deletion and deployed release have still
not been reached.

### Deployed lifecycle progress -- final live gate remains open

Worktrees now stages its Python lifecycle, engine, registry helper, and
installation-context validators into the constructed runtime. The deployed
`dev-status` and `dev-release` commands use those retained resources rather than
the claiming checkout. Release uses the existing whole-operation owner and
relinquishes the claim only after restoration settles; it does not rebuild a
control environment or require UV. Installed registry validation also uses the
retained validator, not today's payload code.

Original-target health validates that target's own recorded local installation
origins, without comparing its graph to the newer dev build. Targets predating
the receipt retain legacy health behavior. Conflicting ownership and failed
health remain refusals, not force takeovers or success-shaped fallbacks.
The focused deployed/registry/lazy/mode/lifecycle contracts pass (84 tests),
including a namespaced registry whose live payload helpers have been replaced
with deliberate failures while its installed validators remain usable.

The final real rehearsal is still open. It constructed the original with an
extra local package absent from the dev graph, and successfully entered dev
through PowerShell. The subsequent Git Bash refresh timed out in UV after
180 seconds, before reaching the new deployed-release assertions. The fixture's
outer budget now covers its two serial control/candidate builds; production
child defaults remain unchanged. A diagnostic follow-up preserves both backend
streams after actual descendant teardown before rethrowing a timeout. Both
focused timeout tests pass, including real descendant termination and preserved
stdout/stderr; the live retry must identify the actual stalled backend stage
rather than weakening the deadline or omitting refresh.

There is also a specific namespaced-adoption gap: dev owner containment must
describe the input worktree, independently of its stable authorized installed
payload, and candidate source provenance must come from the frozen control
build rather than that authorization payload. The legacy source-free rehearsal
does not close this separate-root case. It, the completed live release gate,
native POSIX/CI, the final combined adopter gate, portfolio waves, normal release,
and authorized installed proof remain required. No first-adopter or portfolio
completion checkbox is closed by this progress entry.

### 2026-10-13 -- Serving-build diagnostics and combined-gate progress

Worktrees monitor lock/control-health records now carry the startup-cached
package version and concrete slot alongside the build UUID. Reconciliation's
read-only diagnostic includes Worktrees without adding it to the ordinary
self-update plan, verifies its PID/start-time lock, and reads the selected
installation-cell root. Equal package versions with different known build
UUIDs report serving-build drift rather than silently declaring a repaired/dev
process current or reinstalling an unchanged package. Legacy unknown evidence
is not fabricated. The focused diagnostic/generation/monitor suite passes
332 tests with one platform skip.

The actual combined namespaced rehearsal uncovered more than unit lock-order
checks could: governance waits inherited the helper's five-second initialization
budget, Windows build-backend working directories exceeded CreateProcess's
limit, and private-control interpreter paths needed explicit application-path
launching. Installer governance now forwards its caller's declared budget.
Windows frozen inputs use a scope-owned OS temporary workspace without moving
the authorized installed slot; an immutable exact package-origin receipt
supports later health-checked reuse. A real UV fixture proves an interpreter
path longer than 260 characters, a shorter frozen input path, reuse after that
workspace is gone, and refusal of corrupted recorded origins. The broader
input/custody slice passes 27 checks; the focused long-path/diagnostic/budget
slice passes three.

A real directory-lock owner handoff also exposed a transient stable-read
failure that was being classified as corrupt ownership when the next owner
had already recreated the same path. Typed file-observation changes now retry
only during bounded acquisition; malformed owner data, permissions failures,
and release-time ownership uncertainty still fail closed. Eight focused
handoff/strict-release checks pass, including bounded observation retries
without releasing a live owner. Child failures print through stderr so a
Windows console encoding cannot mask the actual package-build error.

The combined live rehearsal passes in 682.92 seconds with an explicitly
declared test wait budget covering two serialized cold builds. It overlaps an
ordinary first build, whole-operation repair, a real already-held cutover,
subsequent real monitor cutovers, and a genesis/install governance contender.
It confirms construction release before settlement, unchanged original and
baseline completion evidence, and final agreement among the selected manifest,
live monitor lock, and control health for package, slot and build UUID.
Scoped process Jobs and attributable monitor identities are cleaned up.

No whole-adoption or release/installed completion box is checked by this slice.
Native POSIX/CI, deployed command UX, the remaining portfolio, normal release,
and authorized installed proof remain outstanding.

The final selected installer/identity/reconciliation contracts pass 243 checks
with one platform skip; the two publication interruption fixtures were then
updated for the explicit wait-budget argument and pass alongside the exact
shared-deadline contract. Canonical fan-out, the 13-plugin install-contract
check, and the full tracked module ceiling gate pass without widening a
baseline. The live combined run preceded the final fresh-origin-presence
assertion and shared-deadline arithmetic adjustment; those have targeted
regressions, and the complete adopter's final live gate still remains open.

### 2026-10-13 -- Generation discovery and restored deployment evidence

Ordinary Worktrees provisioning now discovers an exact-content repaired
generation from frozen inputs, retains and health-checks it, and hands its
concrete slot to publication separately from the requested package version.
The shared ordering seam and both native resolver families compare the package
release before the repair suffix. This change reaches all 13 canonical
primitive consumers and 12 canonical resolver consumers; it does not advertise
complete installer adoption for those consumers.

The installed build retains its original source footprint and frozen payload
fingerprint. Its Python lifecycle publishes that deployment manifest under
cutover/governance authority and restores exact prior manifest bytes on a
rollback; committed publication failures retain custody through retry. Native
writers do not recompute the deployed footprint from the current checkout.
Package version, concrete runtime slot, and generated build identity are
separate manifest fields. A startup-cached process generation, a slot-local
generation token, and monitor lock/control-health generation evidence prevent
equal-prefix dev rebuilds from being mistaken for an already-current process.
Legacy runtimes without saved evidence remain explicitly unknown.

All three actual Windows native repair lanes, each followed by ordinary
provisioning, passed together in 545.60 seconds. They reused the same healthy
repair while preserving broken original bytes, repaired completion bytes,
installed build identity, and original source evidence. The actual
PowerShell 7 dev / Git Bash source-only refresh / PowerShell 5.1 release lane
passed in 580.16 seconds with new assertions that the restored manifest's
complete source footprint and build UUID equal the original deployment,
not the edited dev source. The first Bash manifest assertion caught a success
flag mistakenly placed in an error branch; moving it to the successful
handover path fixed the overwrite before these passing runs.

Focused verification includes 280 monitor/updater/publication contracts and
99 final generation/engine/lifecycle/lazy-dispatch contracts, including six
real native resolver first-run comparisons and rejection of old lock or health
generations at an equal prefix and held-custody manifest restore/commit-forward
after a lost publication acknowledgement. The shared primitive adopter suite
also passed 124 contracts (four platform-specific skips). Tracked module
ceilings, both canonical sync checks, the install-contract guard, and new-module
lint passed without widening. These are Windows/Git Bash lanes, not native
Linux/macOS evidence. Combined real ordinary-build/repair/cutover/governance
contention, deployed dev-release/status accessibility, remaining running-version
diagnostics, canonical portfolio rollout, normal release, and authorized installed
proof remain open. The whole effort stays Active.

### 2026-10-13 -- Whole-operation repair and claimed-dev owner progress

Both Worktrees native entrypoints now route explicit `repair`, `dev`, and
`dev-release` to one installed Python lifecycle owner. A private frozen control
environment uses the existing construction engine and supplies its complete
dependency graph without selecting a replacement. The owner takes the existing
cutover guard before target allocation, then installation governance and the
target construction lease; it retains the guard through actual settlement.
Ordinary construction continues to release construction/governance before
joining cutover. No lock broker or cross-interpreter borrowed authority is added.

Actual cold repair against deliberately corrupt published completion evidence
preserved the original marker/sentinel bytes and allocated distinct generations
through PowerShell 5.1, PowerShell 7, and Git Bash on Windows. The latter needed
an explicit `.exe` suffix on its resolved uv path: MSYS's existence probe also
finds the suffixless alias, whereas native Python correctly rejects that path.
The final isolated Bash lane passed in 208.79 seconds. This is Windows Bash
evidence, not a Linux/macOS claim.

Focused tests also cover repair guard-before-allocation/governance, construction
failure release, retained broken predecessors, original-governance handover,
same-owner mutable refresh, competing-owner refusal, and claim retention until
original-runtime restoration actually settles. The dev implementation uses the
pattern's frozen non-editable rebuild form and reuses the mutable environment;
it does not stamp generated installed provenance into editable worktree code.
Same-prefix dev publication forces a monitor transition rather than claiming
that an old dev process is the freshly rebuilt generation.

The actual cross-entrypoint source-edit/restore lane passed in 766.87 seconds:
PowerShell 7 claimed and built dev, Git Bash refreshed it with a source-only
edit, and PowerShell 5.1 restored the original deployment before removing the
claim. The installed CLI observed the new source value and a new generated build
identity; the original numbered completion bytes and installed build/source
remained unchanged throughout and after restoration. These were isolated roots,
not a modification of the operator's deployed Worktrees runtime.

Full generation propagation (including ordinary reuse of repaired identities and release ordering),
exact restored-manifest provenance, combined concurrent guard/governance evidence,
deployed release accessibility, portfolio adoption, native POSIX evidence, and
normal release plus authorized installed proof remain open. This progress does
not complete the Worktrees adoption, Phase 3, or the parent effort.

### 2026-10-13 -- Real monitor drain and native exit evidence

The isolated frozen-runtime monitor lane now passes actual cutover, not a
no-live-monitor shortcut. A real classify request remains in flight across the
route flip; exclusive kernel probes refuse both serving and candidate slots.
The installer waits for the request to finish and predecessor exit, then
reattests the published successor's PID and slot before releasing custody.
The successor's existing worktree-status subscription is renewed under the
owning transition stack through drain and retirement. Rendezvous reads tolerate
the bounded publication window only while the successor remains the routed PID.

This exposed a Windows native-exit bug: opening a process object does not mean
the process is running when another owner retains an exited process handle.
ZDD now uses its existing native exit-wait helper on a synchronizable handle.
A real retained-handle regression and eight transition-demand contracts pass;
the integrated 45-contract lifecycle/monitor selection also passed before that
native fix. The subsequent frozen slot-local bootstrap now acquires process
retention before site/application imports. Four bootstrap contracts and the
bootstrapped real-monitor cutover pass together; a separate Python 3.12 lane
proves older venv prefix initialization, owner-death survival and default-GC
protection alongside the real cold/reuse PowerShell 5.1, PowerShell 7 and
Windows-kernel Bash entrypoints. The broader engine/settlement selection passes
84 cases. Full modes,
generation propagation, portfolio and release/installed gates remain open.

### 2026-10-13 -- Exception-safe publication and same-owner recovery progress

Selector publication now shares the owning monitor exception boundary. Failures
before, between, or after selector writes retain the candidate and rollback
slots through observed rollback or commit-forward; transient settlement writes
retry under custody before the original exception is rethrown. Native LF/CRLF
selector bytes are handled explicitly without accepting unrelated selections.
The actual serving monitor's slot is pinned even when it differs from the
previous selector.

The focused lifecycle suite passes twenty contracts, including nine publication
failure/outcome combinations. Nine additional recovery contracts pass actual
native transition-authority checks, both recovery outcomes, durable-write
failure, process-identity refusal, corrupt/unknown evidence and predecessor
drain gating. The same owning receiver retries attributable publication rather
than waiting for an external installer blocked by its retained guards.

The isolated real-monitor lane is still under investigation: its first fixture
incorrectly required the optional hook surface instead of the resident classify
surface, then did not observe a successor within its twenty-second window.
The transition is being retried with full diagnostics and a retained in-flight
request; these failures are not credited as live proof. Abrupt receiver
death also requires a slot-local retention bootstrap for surviving passive
children, before their installed imports; process enumeration is not a
replacement for that ownership. Full modes, generation propagation, portfolio,
release and installed-state gates remain open and unchecked.

### 2026-10-13 -- Worktrees Python entrypoint and candidate preparation progress

Both native ordinary install/update/provision paths now call the vendored Python
lifecycle driver in the active implementation worktree. Candidate preparation
stamps the installed copy before full command-surface/origin health and immutable
completion; version comparison honors normalized distribution metadata, retains
the declared version spelling, and assigns a separate runtime generation.
Windows unsigned agent console trampolines are removed without touching the
signed interpreter or POSIX scripts. Exact reuse does not prepare or restamp.

The focused eight-contract run exercised a real cold frozen build, missing-uv
reuse, installed build-info immutability, and real PowerShell 5.1, PowerShell 7
and Git Bash provision entrypoints. It caught and fixed Bash bootstrap selection
of WindowsApps aliases, MSYS executable suffix/path conversion, CRLF output and
native manifest path attribution. This is Windows-kernel Bash evidence, not a
native POSIX construction/owner-death proof.

The subsequent settlement work adds restart process-handle custody and preserves
the legacy asynchronous backstop unless a caller owns both the cutover guard
and child settlement. The integrated sixty-contract run passes frozen
construction, child/owner-death custody, native provision, interrupted
rollback/commit-forward selection, observed failed-child exit, uncertain-witness
retention and guard ownership. Small owning-package preparation and spawn leaves
keep the existing module ceilings unchanged. Actual live monitor settlement/recovery, claimed
dev/release, distinct-generation repair, generation propagation, portfolio
adoption and normal release/installed proof remain open. No full-adopter or
parent checklist item is checked by this progress entry.

### 2026-10-13 - Finite-provider deadline prerequisite delivered

The finite-profile probe correction #6404 merged as
`c6e35fdd` and cleared normal release promotion #6453, whose generated
`main` snapshot is `8db6b8dc`. The authorized unified update selected and
activated agent-bridge `0.9.55`. Three native deadline regressions passed
using that installed generation's interpreter, with import origins asserted
against installed site-packages rather than the source checkout. Serving
health reports readiness, topology readiness and credential-relay readiness;
the actual cutover reports successful relay adoption. This closes #6399's
provider deadline and live acceptance obligation.

The same whole updater finished with explicit exit `1`, not successful
convergence: agent-dispatch `0.17.1` required FastAPI `>=0.143.0`, while the
configured package index exposed only versions through `0.142.2`. The
separately owned #6454 compatibility correction merged in #6458, but its
normal promotion, unified update and selected/running coordinator proof remain
with that submitting worker. No index override, dependency-floor override,
runtime hotpatch or additional updater retry is implied by the provider proof.

The existing shared-core and first-adopter owner retains construction/
retention descriptor integration and the Python-owned runtime lifecycle in
the existing installer-engine home. Explicit dev/release,
distinct-generation repair, generation propagation, complete installer
adoption on both entrypoints, and portfolio validation remain open. No Plan
or Validation Plan checkbox is completed by this prerequisite; this effort
remains Active.

### 2026-10-13 -- Retention released and installed primitive verified

Normal promotion #6441 merged as
`9539eda8f9b34003a3ddcfcc59d09f02bafa5eb6`. Its pinned dev snapshot
`a27d57dc2ddc83fc1a8e92a0a026e5f64b5cb7b2` contains retention #6381, and the
released Worktrees helper is byte-identical to that retention commit's
canonical helper. This promotion does not yet contain publication #6425.

The authorized ordinary unified updater selected agent-worktrees
`1.24.56-dev1`. An isolated probe used that installed interpreter and helper,
verified installed module provenance and the exact released Git blob, and
proved native GC/toss/slot exclusion, unchanged slot mtime, normal retention
release and process-death release, construction-to-retention handoff, and dev
rollback-reference transfer. Temporary fixture slots and child processes were
cleaned up; no source-checkout helper substituted for the installed one.

Whole-update convergence is not successful: the updater exited 1 when the
configured index could not resolve agent-dispatch's released FastAPI minimum.
That distinct blocker is tracked in #6454. Successful payload and other runtime
phases do not erase it. No alternate index, dependency override, installed-file
patch, or service restart was used to bypass the refusal.

Shared retention release/installed primitive proof is complete; the
retention-only workspace is finalized. First-adopter and portfolio gates
remain unchecked. The operator selected Python ownership of construction and
the full runtime lifecycle for the complete Worktrees adopter, using the
existing installer-engine home rather than a parallel framework. Native
pre-Python bootstrap and OS integration remain initially; signed-Python policy,
configured indexes, frozen inputs, installation-cell authority, subprocess
ownership and actual cutover/drain or rollback lifetimes must be preserved and
validated. This decision is not an implementation or deployment claim.

### 2026-10-13 -- Create-once completion publication merged

#6425 merged into `dev` as
`84d42eb1c6dfd690d49d46458fe19cc0d3b452b4` after current-head approval with
zero open findings and all 30 applicable CI checks successful. Numbered
completion publication now preserves exact existing evidence and refuses
conflicting or malformed evidence. Only explicitly claimed dev slots permit
replacement. The canonical helper and all 13 copies preserve the standalone
offline contract. The contained native Windows selection passed 223 tests
with five platform skips; the executable contracts passed for all 13 plugins.

This remains a shared-core milestone, not completed installer adoption.
Normal promotion and authorized installed proof remain pending. The first
complete adopter remains agent-worktrees on both entrypoints. Its publication
callers currently discard helper failure, so propagation of publication and
prerequisite failures is the next integration prerequisite, before descriptor
lifetime, dev/release, and distinct-generation repair integration. Existing
host/WSL source ownership and release-pipeline recovery ownership are preserved.
The effort and all full-adopter validation checkboxes remain Active.

### 2026-10-13 -- Worktrees activation publication failures now propagate

#6447 merged into `dev` as
`9a62459856ff1d44c1b62c545a5a2434952a2d9d`. Both native entrypoints now stop
before selector publication, last-known-good, monitor cutover, cleanup or
mtime changes when completion or bootstrap prerequisites fail. Healthy
same-content publication preserves the existing evidence instead of refreshing
its timestamp or helper PID. The final current-head owner review had no open
Medium/High findings; the remaining Low overview observation was addressed
with the required status-monitor impact assessment. All required checks
passed before the managed merge.

The final native Windows publication/GC/health selection passed 57 cases;
earlier PowerShell 5.1, PowerShell 7 and Git Bash lanes also exercised the
failure paths. Native Linux/macOS execution was not claimed. Normal promotion
and authorized installed proof of publication and these activation checks
remain outstanding. This closes the native caller error-propagation
prerequisite, not Python descriptor integration, full installer adoption,
dev/release, repair generation propagation or portfolio validation.

### 2026-10-13 -- Python construction custody foundation (not adoption)

The existing installer-engine home now has an ordinary-numbered Python
construction context over the complete frozen local dependency graph. It
uses the explicitly supplied base interpreter's copied venv, installs all
local packages non-editably through one real uv invocation with source
overrides disabled and copy link mode, verifies installed origins and the
isolated health module, then publishes completion. Exact reuse requires no
construction tools or exclusive construction lease. Candidate retention is
acquired before construction release and survives through caller settlement.

Bounded child ownership uses the frozen canonical strict Windows Job helper,
not native build callbacks or a duplicate Job implementation. The POSIX
owner-death guardian holds an inherited construction descriptor while
terminating and verifying its owned writer group. The canonical POSIX lease
therefore releases on final descriptor close, not an explicit unlock that
could revoke a still-live guardian's custody.

Native Windows validation exercised actual offline uv construction from
fresh fixture inputs, installed imports after source mutation, two-process
first-build coalescing, exact marker/payload/inode/mtime reuse, candidate
retention against GC, failed isolated health, child timeout, and abrupt owner
death. Nineteen construction/frozen-input cases passed, including the actual
Worktrees dependency graph; the existing construction/retention selection
passed 74 cases with one platform skip. The executable install contracts
passed for all 13 copies.

These are foundation proofs, not the deployment acceptance lane. Neither
native entrypoint is wired to the Python lifecycle. Installation-cell
authority and combined lock ordering, actual monitor cutover/drain/rollback,
claimed dev/release, distinct repair generation identity, self-contained
release materialization, equivalent POSIX execution and live installed proof
remain open. No first-adopter or portfolio checkbox is completed; the effort
remains Active.

The subsequent teardown-failure audit closed two custody gaps before adoption:
an unavailable Job/group attestation now logs and retries without unwinding
construction or frozen inputs, then reports the original failure after verified
settlement; POSIX retains the unreaped leader identity until descendant writers
have exited. Native Windows regressions check frozen-input survival, held
construction ownership and absent publication during failed Job accounting.
The complete foundation selection passed 23 cases. A separate group-ordering
test verifies the POSIX algorithm's reaping order, not native POSIX execution.
These fixes do not complete any entrypoint or deployment acceptance gate.

The next local integration prerequisites expose borrowed retention custody
through the same canonical lease and add isolated health checks for reuse,
including lazy command-surface validation before fresh publication. Matching
reuse still needs neither construction tools nor an exclusive construction
lease. A failed direct health probe leaves published payload, completion
evidence and selectors unchanged. The full foundation selection passed 25
native Windows cases; the canonical retention selection passed 51 with one
platform skip. Sync, focused lint and module-size gates passed. The explicit
single-file helper ceiling change is 2,362 to 2,376 lines in the canonical file
and all 13 vendored copies, retaining standalone artifact consumption.

The existing monitor API can consume its exact, already-held cutover guard
without recursive acquisition or premature release. Native kernel-lock tests
exercise both successful and exceptional settlement, foreign/unheld refusal,
and explicit candidate-interpreter propagation through the legacy restart
path. Fourteen monitor cases passed with one platform skip; the actual restart
interpreter regression also passed. Native POSIX execution, combined
governance/repair ordering, entrypoint adoption and actual deployment remain
open. No first-adopter checkbox is completed by these local prerequisites.

The unpublished adopter workspace was subsequently pulled forward while
preserving #6164's owner-checked conditional recovery and all incoming baseline
shrinks. The current monitor/restart selection passed 43 native Windows cases
with one platform skip; standalone contracts, sync, lint and module ceilings
passed. Initial validation exposed an old generated ZDD build whose bytes
matched the installed package and lacked the newly required recovery API.
Removing that exact generated build and using the runner's supported
environment refresh with cache disabled loaded current source without changing
dependency feeds, production code or a selected runtime. The construction
engine and kernel helper were byte-identical across the rebase, so their
settled proofs were not repeated.

Normal publication release verification remains blocked by the independently
owned artifact-attestation failure #6535. Run `38444006215` failed literally
with `manifest payload hash differs from candidate` in trusted-default-branch
validation. The source repair #6537 is merged, but its owner's separately
reviewed workflow-only bootstrap still requires explicit operator
authorization. Earlier release failures already have source repairs #6508
and #6522; this effort does not duplicate them, patch the generated release
candidate, bypass attestation or claim a successful installed update.

### 2026-10-13 — Canonical candidate and rollback retention merged

#6381 merged into `dev` as
`234da41ffb66374060a78e76711864f619fb0110`. The current-head Copilot review
reported zero open findings after both high-severity claim-evidence findings
were fixed, and every required PR check passed. The shared primitive and all
13 exact vendor copies now provide descriptor-owned shared retention,
matching-content admission under retention, and exclusive cleanup/selector
rechecks. The primitive supports transferring a completed candidate to retention
before construction release; native fixtures exercise that handoff and retained
candidate/rollback lifetimes. Real adopting entrypoints must still hold those
descriptors through their actual cutover/drain or rollback outcome. Canonical
GC, incomplete sweeping, and explicit slot cleanup respect holders of these
leases. Selected incomplete numbered identities refuse ordinary rebuild
with distinct-generation repair guidance; the mutable dev exception remains.

Claim mutation and cleanup read dev ownership and rollback evidence strictly:
only a missing file is absent. Invalid new/forced owner or rollback input is
rejected before publication, leaving an existing claim unchanged and releasable.
Public status reads preserve their compatibility behavior. Dev claim/release
shares selector-publication coordination and protects the recorded previous
version until that claim is settled or transferred to retention.

The contained Windows matrix passed 197 tests with four platform skips,
including native process ownership/death/cancellation, construction handoff,
shared reuse, each cleanup path, selector races, promotion/rollback, and actual
CLI GC. The executable install-contract probe passed for all 13 plugins;
eight guard regressions and three standalone wheel/sdist resource checks
passed. Five focused retention smoke cases join the existing PR guard lane,
with the broader native matrix remaining in the full/manual portfolio.
The explicit, reviewable single-file module-ceiling adjustment preserves the
existing offline bootstrap and standalone artifact resource ABI.

Normal post-merge CI runs `38325174861` and `38325580916` were cancelled with
no failed jobs as newer `dev` commits superseded them; the continuing gate
follows the newest normal run containing this change. Promotion and authorized
installed verification remain pending. This is a merged shared-core milestone,
not a deployed installer-adoption claim. No Phase 3 adoption or validation
checkbox is completed by it. The effort remains Active: create-once completion
publication, the complete first adopter on both entrypoints, correct
generation propagation, dev/release and distinct-generation repair, and the
remaining runtime portfolio still require their own implementation and proof.

### 2026-10-12 — Revised-legacy publisher prerequisite delivered

Normal validation run `38305079883` completed successfully and promotion #6358
merged into `main` as `67661d853e8f5de7a11c078dbc6dcf50cbce06be`.
The gate's pinned dev snapshot `1fe40334e3e0e75208b1a11191a629101d81dd2a`
contains #6299, the source-layout/staging correction #6337, and stale-publication
diagnostic correction #6343. Released source bytes independently confirm both
revised-legacy admission and fresh numbered-provider/ref/destination/head
revalidation. The release materializes the three activation libraries as
complete local dependencies rather than escaping canonical references.

The authorized unified updater activated agent-worktrees `1.24.52-dev1`.
A contained probe used that installed generation's interpreter
and explicitly verified the loaded publication, tracking and CLI modules came
from its installed site-packages, not the source checkout. All 21 existing
legacy rewrite contracts passed against that runtime: real local Git
publication after rebase, revised records, native lifecycle state, and refusal
of mismatched or shared live authority. Provider observations were controlled
fixtures; this did not rewrite an unrelated live hosted PR or grant new
publication authority. The selected payload command independently reports the
same installed version.

This proves publisher deployment, not complete portfolio rollout. The separate
#5817 handback records an overall update exit 124 and an ordinary retry exit 1
with completed-slot refusal. Those reconciliation outcomes remain a Phase 3
discrepancy; selected-generation activation and passing publication contracts
must not turn a timed-out or refused whole-update attempt into a success claim.

Coordinator health reports `ok` on agent-dispatch `0.16.9-dev1`, with no current
loop failures. The updater preserved an unowned project-command receipt and
left a dirty consumer anchor untouched. Its supervisor inventory/CIM-fallback
warning remains tracked in #6218; a successful overall update and empty
diagnostic error array do not prove that inventory path succeeded. No ad hoc
service restart, process termination, or claim cleanup was performed.

Existing source-layout PR ownership was retained. The overlapping unpublished
local candidate was preserved without opening a competing PR; #6337 supplied
the merged repair, while #6335's owner was asked to reconcile its distinct
exclusivity guard. #6320's required baseline is now backed by successful normal
promotion, not merely a green full-test job. The separate vault-timeout tracker
#6330 is not claimed repaired by this milestone.

Publisher prerequisite #5817 is complete. The effort and Phase 3 remain Active:
the next reviewed shared slice is candidate/rollback retention, followed by
construction coalescing and complete adopter integration. The existing
shared-core worker's retention reservation under #5472 is preserved; the
publisher worker posted the verified release/deployment handback and makes no
competing retention edits. Immutable-slot
refusal, explicit dev/release, distinct-generation repair, portfolio rollout
and their full validation remain unchecked. The parked authoritative-daemon
work is still separate; this delivery does not complete or transfer it.

### 2026-10-12 — Promotion baseline diagnostics preserve both streams

The reopened publisher gate remains blocked on normal promotion and installed
verification. Promotion run `38250657887` passed its test jobs but could not
produce the required agent-worktrees coverage baseline: collection exited 2,
and its coverage warning on stderr hid pytest's stdout diagnostics. The bounded
fix-forward slice landed independently in #6321, which labels and retains both
streams in `BaselineCollectionError`; it does not accept partial coverage or
relax the full-pass baseline requirement. The overlapping #6326 was explicitly
closed as superseded at the operator's direction. This journal preserves the
effort's state without carrying a competing collector implementation.

The synthetic coverage-selection suite passed 120 tests with 7 opt-in skips,
including the stdout/stderr and timeout regression matrix. A real captured
subprocess smoke preserved both streams and exit 2. Two filename-chunking test
expectations now use native `Path` separators, resolving the Windows failures
encountered in this run without changing the POSIX-only project-venv contract;
#6321 independently includes those path corrections. Touched Python lint
passed. These checks describe the superseded local candidate, not a new
validation claim for #6321. Clean-room install validation is inapplicable to
this repository-tool diagnostic change. Linux collection and its underlying
failure still require the normal promotion lane; these checks do not prove a
baseline was earned. No runtime-retention implementation or deployment gate is
checked off by this diagnostic slice.

Subsequent live evidence supersedes the collection blocker: normal run
`38289187893` earned and uploaded the required agent-worktrees baseline
(collection step succeeded; artifact `11766276761`). Promotion then correctly
refused three existing vendor destinations after canonical-source migration:
`plugin-resolve`, `dropin-registry`, and `plugin-activation`. The source-layout
repair is already claimed under #6331/#6334 with #6335 open; this effort does
not duplicate that owner's implementation or bypass the no-overwrite gate.
Normal promotion and installed verification remain the publisher prerequisite.

### 2026-10-12 — Shared construction ownership merged

The bounded, dependency-free Python `construction_lease()` API landed through
#6290 after current-head Copilot approval with zero findings and required CI
success. Its canonical helper and all 13 vendored copies use persistent
installation/version-scoped kernel locks, recheck lock-file identity, distinguish
bounded contention from storage failures, and release ownership on normal,
error, cancellation, and process-death paths. Native competing-process tests
exercise one construction among matching callers and admission revalidation
after the winner publishes, including refusal of different content.

This is a shared prerequisite, not installer adoption or parent completion.
Candidate/rollback retention, create-once immutable publication, the complete
agent-worktrees adopter, portfolio rollout, and their validation remain open.
The next shared retention implementation stays behind the reopened publisher
prerequisite's normal promotion and installed-verification gate above; it must
coordinate with the separately continuing publisher worker rather than duplicate
that worker's changes.

### 2026-10-12 — Revised legacy compatibility follow-up reopens the gate

The #6297 completion review identified #6299, which closes an additional
compatible-legacy case under #5817: a nonzero metadata revision must not alone
block an intentional owned rewrite when fresh numbered-provider, source-branch,
source-repository and exact-head evidence match. #6299 merged after the
`1.24.50-dev1` deployment recorded below. The prerequisite is reopened pending
normal promotion and installed verification of that follow-up; the earlier
deployment evidence remains true for the narrower candidate, not proof that
the revised-record compatibility requirement is already installed.

### 2026-10-12 — Publisher prerequisite deployed and consumer refreshed

Normal promotion cleared after the owned release repairs and scoped standalone
materialization repair #6262. The promoted `main` source contains the
one-command legacy-update implementation. Unified update completed with
agent-worktrees `1.24.50-dev1` active and the installed `--rewrite-pr` flag
available; agent-dispatch health reported `ok` on `0.16.7-dev1`.
The updater reported a supervisor-retirement warning, but the named diagnostic
PID was subsequently absent; no manual process termination was performed.
An existing unowned project-command receipt was preserved rather than silently
transferred.

Consumer refresh exposed a missing Windows LF attribute for reviewed fallback
bodies. Byte-equivalent normalization and the generated sync landed through
the consumer's reviewed flow after clean automated review and required
validation. The old merged-plan #5788 obligation is now released. Publisher
prerequisite completion does not close Phase 3. Remaining work includes
construction coalescing, candidate/rollback retention, per-adopter immutable-slot
refusal, claimed dev/release, distinct-generation health repair, and the
remaining guidance and validation.

### 2026-10-11 — Publisher prerequisite merged; release gate remains

This historical entry records the state at merge. The 2026-10-12 entry above
records the subsequent verified release, deployment and consumer refresh.

The one-command compatible-legacy update and exact-lease publication fixes
merged as #5835 (`2724ce210191de4b66700a4ecd526e0368abaf31`).
Current-head review cleared all Medium/High findings; the remaining Low
installer-validation observation was addressed with corrected scope and a
contained registration/prerequisite/manifest/binstub selection (52 passed,
2 platform-skipped). Required CI completed successfully before merge.
Additional completed selections exercised legacy migration, actual rebase and
follow-up transport, authority/interleaving/publication state, and unregister
lock ordering; no failing or timed-out broad selection was called green.

Recovery-authored fixes were preserved, and updates used supported publication
with exact saved leases and normal hooks. The parent prerequisite remains
unchecked until normal release promotion, unified deployment, consumer refresh
and old-plan claim reconciliation are verified. Runtime Phase 3 and the parked
daemon-only effort remain open. Shared runtime admission code also advanced
upstream while this prerequisite was in review; compare the reviewed plan
against current source before beginning another implementation slice.

The first post-merge full release gate exposed nine publication regressions
and two stale bridge authority-fixture bindings. Existing repair ownership
was honored: #6244 and #6256 landed through their own reviewed fixes.
Validation run `38184375719` then passed every full test and guard job for
`35eddcaaf`, but its promotion job refused the new standalone dispatch
distribution's plugin-backed editable reference under the libs-only
materialization contract. That separate release blocker is reported under
#6177 and coordinated with the live distribution owner. No bypass, direct
main mutation, duplicate repair PR or deployment occurred. The prerequisite
therefore remains release-gated, not checked off by the publisher merge alone.

### 2026-10-09 — Reviewed runtime plan and publisher continuation

The Phase 3 execution plan landed through #5788. Its approval is planning
progress, not runtime implementation or parent completion. Publisher prerequisite
#5835 remains open and unmerged at this journal entry. Current review corrections
cover project-local legacy destination resolution, provider credential isolation,
generation-protected provider identity, attribution-only revision races,
provisional first-push retry, native provider lifecycle states, and authority
acquired before every external publication and retained through fresh tracking
persistence. The combined contained authority/provider/publication selection
passed 60 cases, including real child-process contention and I/O probes.

An explicitly authorized source-CLI bootstrap exercised a real exact-lease rewrite
of the existing publisher PR and incremental follow-ups without editing or
installing a deployed runtime. That bootstrap exposed an additional requirement:
valid existing-PR publication must be a straightforward one-command operation,
not a manual ownership-migration obstacle course. #5817 remains open for that
usability continuation. The prior planned live validation target #5788 is already
merged and is no longer a pending rewrite target. Current-head review, required
checks, merge, release promotion and unified deployment still precede the runtime
implementation stretch.

### 2026-10-08 — Durable admission and coalesced-build continuation

The operator requested durable fixes and guidance after a concurrent initial
runtime build exposed fail-fast creation ownership and a source trace exposed
same-version in-place package deployment. The existing #5472 and this effort's
Phase 3 are the canonical home, not a new overlapping campaign. Added the
agent-recommended execution decomposition covering shared admission, bounded
builder joining, immutable publication, complete adopter rollout, and honest
operator/contributor guidance. Runtime implementation awaits its review gate;
the existing immutable-versioned-runtime and explicit-dev exception remain
unchanged.

### 2026-10-06 — Cross-linked from a separate invariant audit
- A separate `vision-backport-sweep` effort's design/service-invariant
  audit (`ThomasMichon/copilot-extensions#5472`, `#5468`) independently
  found that almost every `agent-*` plugin's ordinary `install`/`update`
  path still silently mutates a completed numbered slot in place for a
  same-version, changed-content update -- the exact anti-pattern this
  effort's `dev`/`dev-release` mechanism exists to replace, still reachable
  because most plugins' ordinary paths don't *refuse* it (`agent-pull-requests`
  is the one exception already carrying the refusal control flow, though
  review on that same PR found its own payload-hash scope too narrow --
  `pyproject.toml` only, missing `src/` -- so it still needs a fix before
  being complete, and it hasn't adopted the `dev`/`dev-release` redirect
  either). Added two new sections to
  `docs/patterns/mutable-dev-slot.md` (*Ordinary installers: refuse, never
  silently mutate a numbered slot*, and *Repairing a broken numbered slot is
  a cutover, not a delete-and-rebuild* -- reworked after review correctly
  flagged that an earlier delete-and-rebuild-same-version draft of the
  latter still violated `immutable-versioned-runtime`'s rollback guarantee;
  it now reuses `graceful-daemon-cutover`'s existing serialize/promote-
  before-retire/drain contract (plus its commit-forward fallback for a
  repair target too unhealthy to drain cleanly) under a distinct
  generation identity, with that identity required to propagate through
  any running-version comparison too) and prioritized Phase 3's rollout
  list with the specific plugins that audit identified, plus a new Phase 3
  validation item requiring all three pieces (refusal guard,
  distinct-generation repair path with its operator-guidance
  reconciliation, and `dev`/`dev-release`) per plugin before checking it
  off.

### 2026-09-23 — Phase 1 landed
- Core primitive, tests, GC protection, finalize warning hook, and design
  doc landed. Runtime-accessibility question and pilot-plugin wiring
  deliberately deferred to Phase 2 rather than rushed -- see the design
  doc's own "open design question" section for why.

### 2026-09-23 — Phase 2 landed (pilot: agent-codespaces)
- Resolved runtime accessibility as Option 1: `versioned_runtime.py` is
  copied to `~/.agent-codespaces/versioned_runtime.py` on every
  install/update/dev; `dev-release`/`dev-status` are first-class verbs on
  the deployed `agent_codespaces` CLI (`__main__.py`), shelling out via
  `sys.executable`. `dev-claim`/`slot`/`activate` stay installer-only.
- `install.ps1`/`install.sh` gained a `dev` verb: resolves the owner via
  `agent-worktrees get worktree-dir` (falls back to the repo root),
  claims, builds `versions/dev` as an editable install of all 4 path deps
  (ssh-manager, credential-relay, config-migrate, agent-codespaces itself),
  health-gates, marks complete, activates. Idempotent/mutable: a second
  `dev` run reuses the venv and only refreshes the editable links.
- 17 new tests (`tests/test_dev_slot_cli.py`) for `_resolve_dev_slot_owner`,
  `_run_versioned_runtime`, `_cmd_dev_release`, `_cmd_dev_status`, and
  `main()` dispatch. Full `agent-codespaces` suite: 591 passed, only the
  2 pre-existing/documented Windows bash-path-quoting failures in
  `test_bootstrap_check_reconcile_opt_in.py` (unrelated).
- **Live-validated the full cycle on this machine's real deployment**
  (not just unit tests): claimed dev, confirmed `agent-codespaces version`
  flipped to the editable `dev` build, edited `__main__.py` and watched the
  deployed CLI reflect it with ZERO rebuild, exercised the deployed CLI's
  own `dev-status`/`dev-release`, confirmed `current-version` restored to
  the real prior deployment (`0.4.0-dev163`), and confirmed `gc()`
  reclaimed the released `dev` slot.
- Found and fixed two real bugs only visible under live validation (neither
  caught by the unit tests, which mocked `subprocess.run`):
  1. **PowerShell array-unwrapping**: `$modeArgs = if ($Editable) { @('--editable') } else { @('--reinstall-package', 'x') }`
     silently unwraps the single-element true-branch array to a bare
     string, so the later `@modeArgs` splat iterated it CHARACTER BY
     CHARACTER (`uv pip install --python $py -e ...` arrived as `-`, `-`,
     `e`, `d`, ... one arg at a time) -- `uv` rejected it as an invalid
     package name. Fixed by wrapping the whole conditional:
     `$modeArgs = @(if ($Editable) { '--editable' } else { ... })`.
  2. `versioned_runtime.py`'s `--json` is a GLOBAL argparse flag and must
     precede the subcommand token, not follow it -- `_run_versioned_runtime`
     now assembles `--root/--link-name/--json` before the subcommand args
     rather than leaving ordering to each caller.
- CONTRIBUTING.md's hot-patch gotcha now points at `agent-codespaces dev` /
  `agent-codespaces dev-release` as the preferred iteration path, keeping
  the raw hot-patch note for plugins that haven't adopted the pattern yet.
- Not yet exercised live: `agent-worktrees finalize`'s dev-slot warning in
  its real (non-dry-run) path -- Phase 1's dedicated unit suite already
  covers it; a live finalize would have retired this worktree mid-effort.
- Version bumped: `agent-codespaces` `0.4.0-dev164` -> `0.4.0-dev165`
  (this plugin only; no shared-lib touch this round).
