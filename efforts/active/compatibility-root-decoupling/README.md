# Compatibility-Root Decoupling (core()/monkeypatch-on-root retirement)

- **Slug:** `compatibility-root-decoupling`
- **Repo:** copilot-extensions
- **Branch(es):** per-slice feature branches (independent per-slice PRs)
- **Created:** 2026-10-05
- **Status:** Draft
- **Umbrella issue:** #5293

## Guiding Intent

Retire the `core()`/`_core()` reverse-import accessor pattern and its
monkeypatch-on-root test dependency across the suite, starting with
`agent_worktrees/__main__.py` where it's most entangled. The pattern lets a
module extracted from a monolith call back into the root it was split from,
kept alive only so a test's `monkeypatch.setattr(m, "<name>", ...)` against
the root still takes effect. It is a trap, not a shortcut: every future split
has to preserve the indirection instead of actually decoupling anything, which
is why `agent_worktrees/__main__.py`'s own componentization campaign
(`module-componentization-discipline`) has stalled — its remaining,
not-yet-split code is exactly the tail still entangled this way.

The target end-state (see `docs/patterns/compatibility-root-decoupling.md`
for the full rationale): a genuinely shared, stateless helper gets a real
shared-module home that every caller imports directly; a test patches the
module where a name is actually defined, never a historical root it used to
live in; and a sibling module's `_core()` accessor retires once nothing calls
through it anymore.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| lambda-core | Drives the migration slices, opens/lands each per-name PR | local worktree, independent per-slice PRs |

## Context

- Surfaced while fixing two broken daily CI workflows (PR #5270) and a
  follow-up vision revision to `visions/plugin-services/README.md`'s
  `uniform-deploy-contract` feature, during a dev-branch module-size-cap
  investigation.
- New pattern doc: `docs/patterns/compatibility-root-decoupling.md`.
- Cross-referenced from the `customizing-copilot:componentizing-modules`
  skill's "Splitting a compatibility root" section (now states the design
  invariant: new code migrates *off* this idiom, the section's own mechanics
  are only for working safely with an existing, not-yet-decoupled root).
- Sibling effort this work directly unblocks:
  `efforts/active/module-componentization-discipline/` (the line-count
  campaign against `agent_worktrees/__main__.py`, currently stalled because
  its easy, fully-decoupled seams are exhausted).
- Measured scope in `agent-worktrees` as of 2026-10-05: 47 `core()`/`_core()`
  accessors, 317 `core().<name>(...)` call sites, 112 distinct names
  monkeypatched on the root module object across 704 patch-site occurrences.
  `_json_output` (66 calls / 29 monkeypatch sites) and
  `_resolve_worktree_id` (11 calls / 34 monkeypatch sites) are the two
  highest-traffic names.

## Request

Operator (verbatim, paraphrased only to merge two consecutive turns): "We
should make a coding guide based on this, codifying some of the intended
outcomes here as design invariants going forward. Then we'll need an effort
to track this. We'll work pieces at a time, so we don't do huge refactors.
Any change that makes these files slightly smaller is a win."

## Plan

### Phase 0 — codify the invariant (done)
- [x] Write `docs/patterns/compatibility-root-decoupling.md`.
- [x] Add a design-invariant bullet + Patterns table row in
      `docs/patterns/README.md`.
- [x] Cross-reference from the `componentizing-modules` skill's "Splitting a
      compatibility root" section.
- [x] File umbrella issue (#5293) and this effort.

### Phase 1 — tooling to make migration mechanical (done)
- [x] `tools/compat-root-migration.py`: given one name, resolves (a) its
      current definition site(s), (b) every `core().<name>(...)` call site,
      (c) every `monkeypatch.setattr(<root alias>, "<name>"` test site
      (detects every alias a test file binds `__main__` to -- `m`, `main`,
      `cli` all observed in this plugin's own suite, not hardcoded to one) —
      reported as one unit (`--name <name>`), so a migration slice is
      verifiably complete (zero call sites, zero monkeypatch sites) rather
      than eyeballed.
- [x] `--progress` mode reports live aggregate counts, ranked by traffic.
      Corrected baseline (multi-alias-aware, supersedes the manual-grep
      numbers in Context above): **47 accessors, 308 call sites, 123
      distinct monkeypatched names, 727 patch-site occurrences.**

### Phase 2 — migrate the highest-traffic names
- [x] `_json_output` + `_json_error` (migrated together: same files, always
      paired). Final corrected shape (tool's multi-line-aware pass found
      more than the original estimate): 32 source files touched (22 with a
      `_core()`/`core.` shim or direct call, plus `__main__.py` itself, plus
      9 more files reaching through a bare `core`-as-import-alias or a
      second local shim variant the first measurement pass missed) and 14
      test files (10 originally identified + 4 more found only by a
      multi-line-aware `monkeypatch.setattr(\n    <alias>,\n    "<name>"`
      scan — single-line regexes silently missed these). Every caller now
      does `from . import output` + `output._json_output(...)`/
      `output._json_error(...)`; every test monkeypatches `output` directly.
      Validated: `tools/compat-root-migration.py --name` reports zero call
      sites and zero monkeypatch sites for both names; full targeted test
      sweep (1330+ tests across every touched file) green; `ruff
      check --select F,E9` clean; `check-module-size.py` clean (one file
      tipped 1 line over its cap from the added import -- fixed by merging
      it into an existing `from . import` line instead of a new one, net
      zero added lines).
- [ ] `_resolve_worktree_id` (11 call sites / 40 monkeypatch sites) —
      next slice.
- [ ] `_find_repo_dir`, `_normalize_path`, `_infer_worktree_id_from_cwd`,
      `_infer_worktree_id`, `_resolve_active_project`, `_build_env`
      (next-highest traffic; re-measure with `--progress` before picking
      exact order — several of these have outsized monkeypatch counts
      relative to call-site counts, which may make them higher-value than
      raw call-site ranking suggests).
- [ ] Re-measure scope after each name lands; update this Plan with the next
      batch rather than pre-committing to a fixed list up front.

### Phase 3 — long tail, per sibling module
- [ ] Work remaining names module-by-module, retiring each sibling's local
      `_core()` accessor once its call count reaches zero.
- [ ] Once every accessor is retired, remove the "Splitting a compatibility
      root" section's now-unneeded legacy-support guidance from the
      `componentizing-modules` skill (keep only the design invariant).

## Validation Plan

- [ ] Each migration slice: `python tools/run-plugin-tests.py agent-worktrees`
      (or a `-k`-filtered targeted run per the `componentizing-modules`
      skill's Step 3 guidance) fully green — not just "sub-suite 1 passed."
- [ ] After each slice, re-run the Phase 1 counting tool and confirm the
      migrated name's call/monkeypatch counts are now zero against the root.
- [ ] `ruff check --select F,E9` on every touched file.
- [ ] `python tools/check-module-size.py` — confirm no regression (this
      effort is about decoupling, not primarily line count, but a slice
      should never make the cap situation worse).
- [ ] CI green on each slice's PR before merging (this repo's authoritative
      full-matrix confirmation per the `componentizing-modules` skill).
- [ ] If a slice retires a now-fully-unused `_core()` accessor, re-run
      `test_lazy_dispatch.py`'s `_CLUSTER_FREE_MODULES` drift check (that
      module may now be newly cluster-free) and grep the test suite for a
      `SimpleNamespace`-faked whole `_core()` return value carrying the
      migrated name (a shape the Phase 1 tool's static scan cannot see --
      only running the real test suite catches it).

## Proposal

_Pending._

## Journal

### 2026-10-05 — Kickoff
- Effort created from a design-invariant discussion that started as a
  dev-branch module-size-cap investigation (agent-worktrees `__main__.py`
  and `front_door_cli.py` both over cap). Measured the compatibility-root
  pattern's actual scope, wrote the pattern doc + design invariant, filed
  the umbrella issue, and opened this effort. No migration work started yet
  — Phase 1 tooling is the next slice.

### 2026-10-05 — Phase 1 landed
- Wrote `tools/compat-root-migration.py` (`--progress` and `--name` modes).
  Its multi-alias-aware monkeypatch detection found a slightly larger true
  baseline than the manual grep in Context above: 123 distinct monkeypatched
  names and 727 patch sites (vs. 112/704), because some test files bind the
  root module as `main` or `cli` instead of `m`. Confirmed `_json_output`'s
  exact shape with `--name`: one real implementation in `output.py`, 20
  duplicate shim wrappers across sibling `*_cli.py` files that each just
  forward through `core()`. Next slice: migrate `_json_output` for real.

### 2026-10-05 — Phase 2 slice 1: `_json_output`/`_json_error` migrated
- PR opened in a separate worktree (Phase 1 tooling PR #5311, merged); this
  slice is tracked in its own PR against the tool's corrected numbers.
- The tool's own single-line regex undercounted real scope on this first
  real attempt: a second finder pass that tolerates a multi-line
  `monkeypatch.setattr(\n    <alias>,\n    "<name>"` call found 4 more test
  files and corrected the aggregate baseline upward again (155 distinct
  monkeypatched names / 1023 patch sites, from 123/727) -- fixed in the
  committed tool itself, not just the one-off driver scripts, so future
  `--name`/`--progress` runs stay accurate.
- Also found call sites the first source-side pass missed: a second local
  shim variant (`def _json_output(value): _core()._json_output(value)` with
  a differently-shaped signature) and several files reaching through a bare
  `core`-as-import-alias (not the `core()`-call-site shape) rather than a
  local shim at all. Both are now folded into the tool's own detection.
- Net: 32 source files + 14 test files touched. Every caller imports
  `output` directly and calls `output._json_output(...)`/
  `output._json_error(...)`; every test monkeypatches `output` itself.
  `tools/compat-root-migration.py --name _json_output` (and `_json_error`)
  both report zero remaining call/monkeypatch sites.
- Validation: full targeted test sweep (1330+ tests across every touched
  file, run in slices due to the bounded test-supervisor's wall-clock
  limit) all green, zero failures; `ruff check --select F,E9` clean;
  `check-module-size.py` clean (one file transiently tipped 1 line over its
  cap from a new import line -- fixed by folding it into an existing `from
  . import` line instead, net zero lines added).
- **Two further gotchas found only by actually running the real test
  suite** (neither detectable by the tool's static scan, both now fixed):
  (1) retiring a now-fully-unused `_core()` accessor (zero remaining call
  sites) is itself a drift source for `__main__._CLUSTER_FREE_MODULES`, a
  hand-maintained set backed by a dedicated AST drift-check test
  (`test_lazy_dispatch.py` + `_core_cluster_scan.py`) -- a module that
  becomes genuinely cluster-free needs adding, and this pass also caught
  one pre-existing, unrelated drift (`finalize_cli` was wrongly listed as
  cluster-free already, since it still reaches `_core()` for unrelated
  names); (2) a different compatibility shape entirely -- two test files
  faked the *whole* `_core()` return value as a `SimpleNamespace` carrying
  its own `_json_output`, which isn't a `monkeypatch.setattr(<root alias>,
  "_json_output", ...)` call at all and so is invisible to the tool's
  monkeypatch scan. Both fixed by patching `output._json_output` directly
  (keeping the `SimpleNamespace` fake only for names still legitimately
  routed through `_core()`). **Takeaway for future slices:** always run the
  real test suite per name, not just the tool's zero/zero report -- the
  tool proves no *known-shape* reference remains, not that nothing
  depended on the old behavior.
- Next slice: `_resolve_worktree_id` (11 call sites / 40 monkeypatch
  sites) -- re-run `--progress` first, since this slice's corrected
  baseline may have shifted the ranking.
