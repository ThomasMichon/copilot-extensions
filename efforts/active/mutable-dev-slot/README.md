# Mutable dev slot

- **Slug:** `mutable-dev-slot`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase PRs to `dev`; normal release promotion to `main`
- **Created:** 2026-09-23
- **Status:** Active
- **Coordination:** #5472
- **Vision:** [`docs/patterns/mutable-dev-slot.md`](../../../docs/patterns/mutable-dev-slot.md)
  (a deliberate, narrow exception to
  [`docs/patterns/durable-vs-versioned-runtime.md`](../../../docs/patterns/durable-vs-versioned-runtime.md)'s
  immutable-versioned-runtime invariant)

## Guiding Intent

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
[`docs/patterns/mutable-dev-slot.md`](../../../docs/patterns/mutable-dev-slot.md)
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

- [ ] Review the shared
      [runtime-admission/build-coalescing slice](phase-3-runtime-admission.md)
      before per-plugin code changes, then execute it through the shared core
      and phased adopter changes. This closes published-slot overwrite and
      transient first-builder contention at their owning seams, while preserving
      the full per-plugin guard/dev/distinct-generation repair gate below.

- [ ] Repeat Phase 2's pilot shape for the other 12 plugins currently
      vendoring `versioned_runtime.py` (see `tools/sync-versioned-runtime.py`'s
      target list; 13 vendoring plugins total, `agent-codespaces` is the
      one Phase 2 pilot) as their own installers need it -- not a mandatory
      blanket rollout on day one. This includes `budget-guidance`, the one
      vendoring plugin that isn't `agent-*` and so isn't named in the
      audit-prioritized list below -- it's still owed this same rollout
      under this generic bullet.
- [ ] **Prioritize the plugins a separate invariant audit
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

- [ ] **Phase 3, per adopting plugin, on both installer entrypoints
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
