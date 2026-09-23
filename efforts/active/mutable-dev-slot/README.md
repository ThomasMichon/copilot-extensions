# Mutable dev slot

- **Slug:** `mutable-dev-slot`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees -> landed to `main`
- **Created:** 2026-09-23
- **Status:** Active
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

## Request

Operator ask, paraphrased: add a `dev` version slot per plugin with a
mutable venv; a config flag (or worktree-default detection) selects it; a
worktree "claims" dev mode for a plugin so two worktrees can't clobber each
other; `agent-worktrees finalize` must force worktrees to release/disable
dev mode on finalization; none of this may break real deployments.

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

- [ ] Resolve the open design question `mutable-dev-slot.md` flags: make
      `versioned_runtime.py` reachable from a plugin's **deployed** CLI (not
      just its installer) so `dev-release`/`dev-status` are callable without
      a source checkout present -- the leading option is copying the script
      into the plugin's own root (`~/.<plugin>/versioned_runtime.py`) at
      install time and adding first-class `dev-release`/`dev-status`/
      `dev-claim` verbs to the plugin's own argparse CLI that shell out to it.
- [ ] Wire ONE pilot plugin's installer (`agent-codespaces` proposed, since
      its `install.ps1`/`install.sh` are already well understood from #3340)
      with a `dev` verb: claim dev mode for the calling worktree
      (`agent-worktrees get worktree-dir` as `--owner`), build/rebuild
      `versions/dev` in place (an editable/`-e` install against the
      worktree's own checkout, not a fresh venv every call), mark it
      complete, and activate it.
- [ ] Wire the pilot's `dev-release` CLI verb: `release_dev`, then
      `activate(previous_version)` to restore the machine's real deployment.
- [ ] Live-validate end to end: claim, edit the worktree's source, observe
      the deployed CLI reflect it without a rebuild (where the plugin's own
      build step supports live edits, e.g. an editable install), release,
      confirm the machine is back on its real version and `dev` becomes
      GC-eligible.
- [ ] Update CONTRIBUTING.md's hot-patch gotcha to point at the pilot
      plugin's `dev`/`dev-release` verbs as the preferred path once proven,
      keeping the hot-patch note only for plugins that haven't adopted the
      pattern yet.

### Phase 3 — Rollout to the remaining vendoring plugins

- [ ] Repeat Phase 2's pilot shape for the other 11 plugins currently
      vendoring `versioned_runtime.py` (see `tools/sync-versioned-runtime.py`'s
      target list) as their own installers need it -- not a mandatory
      blanket rollout on day one.

## Validation Plan

- [x] `python tools/run-plugin-tests.py agent-bridge -k dev` -- new
      dev-slot primitive tests pass.
- [x] `python tools/run-plugin-tests.py agent-bridge` (full suite) -- no
      regressions beyond pre-existing, previously-documented unrelated
      failures.
- [ ] Phase 2: a live pilot-plugin `dev`/`dev-release` cycle, observed
      directly (not just unit-tested).
- [ ] `agent-worktrees finalize` on a worktree holding a live dev claim
      prints the warning with the correct plugin name and release command,
      and does NOT silently release it.

## Journal

### 2026-09-23 — Phase 1 landed
- Core primitive, tests, GC protection, finalize warning hook, and design
  doc landed. Runtime-accessibility question and pilot-plugin wiring
  deliberately deferred to Phase 2 rather than rushed -- see the design
  doc's own "open design question" section for why.
