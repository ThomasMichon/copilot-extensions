# Derived Disk Hygiene

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

Every track follows the same safety shape: dry-run by default, explicit
`--apply`, never follow reparse points, never touch tracked or non-derived
content, refuse paths with a live process, and fail closed on uncertainty.

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

## Plan

### Track A — Reclaim legacy `.test-venvs` (#10965)
- [x] `tools/reclaim_legacy_test_dirs.py [--apply]` (standalone: the runner
      is at the module-size cap): enumerate root `.test-venvs/`/`.test-runs/`
      across every registered worktree, report size/age; delete only with
      `--apply`, only when ignored by Git, untracked, link-free, older than
      `--min-age-hours` (24) and not in use (Windows rename probe, Linux
      `/proc` scan, otherwise refuse). Local dry run: 158 trees / 11.2 GiB
      eligible, 15 refused (links, recent).
- [x] Move `tools/typecheck.py` cache environments to
      `ce-tests-<key>/typecheck/<platform>/` so no current writer creates
      `.test-venvs` for environments.
- [ ] Evidence-capture tools: keep their report path but document it as
      discardable evidence (not a venv). (Deferred; reports only.)
- [x] Replace TESTING.md "neither migrated nor deleted" guidance with the
      supported reclamation command.

### Track B — Stop unsealed bytecode growth (#10966)
- [ ] Apply the #10812 selector pattern (call `cache_arguments`, reuse
      `installed-<digest>`) to remaining random-minting launchers:
      worktree-manager runtime launchers, payload-invocation shim
      templates, budget-guidance bin, agent-worktrees hook/payload
      runtime scripts, and installer-embedded launchers.
- [ ] Where a launcher must still use an unsealed namespace, delete it
      after the child exits.
- [ ] Supported `python-cache prune [--apply]` in `agent_procutil`
      (exposed via agent-worktrees CLI): removes `unsealed-*` dirs that
      contain only `.pyc`/`__pycache__`, are older than a grace period, and
      are not held by a live process; never touches `installed-*`.

### Track C — Derived outputs as open claims (#10967)
Finalize never deletes anything. Derived outputs are journaled as explicit
claims the worktree's agent must close out before finalize.
- [ ] New `derived` claim kind on the existing outbound ledger. A producer
      that creates a derived dir records `claims add derived <path>` for its
      worktree and writes a `.derived-claim.json` marker (worktree id,
      nonce, creator, created). A pre-existing dir without a matching
      marker is never adopted.
- [ ] The existing kind-agnostic finalize gate blocks while `derived`
      claims are open and lists them.
- [ ] `claims settle <path> --reclaim` deletes exactly that one path, only
      when the marker's worktree id + nonce match the ledger entry, the path
      is inside this checkout, contains no tracked files or reparse points,
      and no live process holds it. Any mismatch refuses and leaves the
      claim open. `claims release <path>` keeps the dir.
- [ ] No chaining: one ledger entry per settle, never another worktree's
      claims, and the reclaim sweep skips `derived` claims (like `pr`).
- [ ] First producer: `run-plugin-tests.py` (`.test-venvs` writers that
      remain after Track A). Legacy unmarked dirs stay Track A's job.

### Track D — Labeled scratch and lock hygiene (#10968)
- [ ] Shared `labeled_scratch(prefix, purpose)` helper that creates a
      timestamped dir with `scratch-owner.json` (owner refs, created,
      purpose, retention) and removes it on context exit.
- [ ] `agbridge-host-*` mkdtemp fallback in copilot-driver-core uses it and
      cleans up on host exit.
- [ ] Instruction-projection lock files move under owner state and are
      unlinked after release.
- [ ] Identify the root-level `.lock` producer by name pattern and fix it.
- [ ] Report-only `scratch report` listing unlabeled/orphaned temp dirs
      (no automatic deletion).

## Validation Plan

- [ ] Unit tests per track: dry-run vs apply, tracked/ignored eligibility,
      reparse refusal, live-process refusal, installed namespace preserved.
- [ ] Simple end-to-end: run each dry-run against this machine and compare
      to measured inventory; apply on a bounded subset.
- [ ] Observe unsealed creation rate for 24 h after Track B lands.

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
