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
