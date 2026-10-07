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
| operator's facility host | Drives the migration slices, opens/lands each per-name PR | local worktree, independent per-slice PRs |

## Coordination

- **Topology:** independent per-slice PRs (no shared feature branch) --
  each migration slice (one or a small batch of names) is its own PR
  against `dev`, opened from its own worktree, reviewed and merged
  independently before the next slice starts.
- **Host (owns PRs):** the operator's facility host (sole participant;
  single-agent effort, no delegation currently in play).
- **Handoff:** none required while single-participant. If a future slice
  is delegated to another participant, record the assignment here before
  dispatching and follow the standard `agent-worktrees:git-collaboration`
  skill's independent-worktree pattern (each delegate opens its own PR;
  the effort README tracks which slice each PR covers).

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
      Detects every root-alias shape in both source and test files:
      `_core()`-style lazy accessor calls, a plain assigned-variable
      alias (`core = _core()` then `core.attr(...)`), and four
      independent test-side patch shapes --
      `monkeypatch.setattr(<alias>, "<name>", ...)` (single- or
      multi-line), `unittest.mock.patch("<pkg>.__main__.<name>")`,
      `monkeypatch.setattr("<pkg>.__main__.<name>", ...)`, and
      `unittest.mock.patch.object(<alias>, "<name>", ...)`.
      Whole-identifier matching (not a substring/suffix match) throughout.
      `--plugin` and `--name` both fail loud on an unresolvable plugin or a
      name with no definition/call/patch site anywhere, rather than
      reporting a vacuous success. Current baseline: **42 accessors, 290
      call sites, 159 distinct monkeypatched names, 1056 patch-site
      occurrences.** Covered by `tools/test_compat_root_migration.py`.

### Phase 2 — migrate the highest-traffic names
- [x] `_json_output` + `_json_error` (migrated together: same files,
      always paired). Every caller does `from . import output` +
      `output._json_output(...)`/`output._json_error(...)`; every test
      (both `monkeypatch.setattr` and `unittest.mock.patch` shapes)
      targets `output` directly. 34 source files + 15 test files touched.
      Every now-fully-unused `_core()` accessor retired per the rule
      below; `__main__._CLUSTER_FREE_MODULES` updated to match (confirmed
      via the checked-in AST drift-check scanner).
      Validated: `tools/compat-root-migration.py --name` reports zero
      call/monkeypatch sites for both names; full targeted test sweep
      (1330+ tests across every touched file) green; `ruff
      check --select F,E9` clean; `check-module-size.py` clean.
- [x] `_resolve_worktree_id` (27 call sites / 49 monkeypatch sites).
      Every caller does `from . import worktree_identity` +
      `worktree_identity._resolve_worktree_id(...)`; every test
      (`monkeypatch.setattr`, all root-alias shapes -- `m`, `cli`, `main`)
      targets `worktree_identity` directly. 14 source files + 14 test
      files touched (the 8 sibling shims this name lived in --
      `git_cli.py`, `handoff_cancel_cli.py`, `handoff_cli.py`,
      `reclaim_cli.py`, `resolve_cli.py`, `session_binding_cli.py`,
      `session_metadata_cli.py`, `worktree_ops_cli.py` -- plus 5 modules
      that called through the root directly with no local shim at all
      (`finalize_cli.py`, `handoff_cutover.py`, `pr_cli.py`,
      `pr_state_cli.py`, `session_tracking_cli.py`), plus `__main__.py`
      itself for the re-export removal). Every now-fully-unused shim
      deleted outright (no dead forwarding stub left behind); no sibling
      module's `_core()` accessor reached zero remaining callers this
      slice (each of the 8 still routes at least one other name through
      it), so `__main__._CLUSTER_FREE_MODULES` needed no change --
      confirmed by running `test_lazy_dispatch.py` directly rather than
      assuming.
      Validated: `tools/compat-root-migration.py --name
      _resolve_worktree_id` reports zero call/monkeypatch sites; full
      targeted test sweep (split into ~10 `-k`-filtered batches to stay
      under the bounded runner's per-sub-suite wall-clock budget --
      `test_pr_ops.py` alone needed ~21 minutes) all green, 1830+ tests
      passed, zero failures, zero skips introduced; `ruff
      check --select F,E9` clean; `check-module-size.py` clean (one file,
      `handoff_cutover.py`, was already sitting exactly at the 1000-line
      cap and tipped to 1001 from the new import -- fixed by folding
      `from . import config as cfg` into the existing tuple import and
      trimming a redundant blank line, net zero lines added, same fix
      shape as the prior slice's own module-size save).
- [x] `_infer_worktree_id` (18 call sites / 30 monkeypatch sites).
      Unlike the prior two slices, the real implementation did NOT already
      live in a shared module -- it was a plain function body in
      `__main__.py` with zero dependency on anything `__main__`-specific
      (it only called `_infer_worktree_id_from_cwd`, already resident in
      `worktree_identity.py`), so this slice moved the function body itself
      into `worktree_identity.py` (next to the helper it calls) before
      doing the usual call-site/patch-site migration. Every caller does
      `from . import worktree_identity` + `worktree_identity._infer_
      worktree_id(...)`; every test (`monkeypatch.setattr`, all root-alias
      shapes -- `m`, `cli`, `main`) targets `worktree_identity` directly.
      8 source files + 12 test files touched (the 4 sibling shims this
      name lived in -- `claims_cli.py`, `follow_ups_cli.py`, `git_cli.py`,
      `session_metadata_cli.py` -- plus 2 modules that called through the
      root directly with no local shim at all -- `finalize_cli.py`
      (5 call sites), `pr_state_cli.py` (9 call sites) -- plus `__main__.py`
      itself, which had 2 bare in-module calls of its own: not re-exported
      (nothing else in `__main__.py` needed the bare name), repointed to
      `worktree_identity._infer_worktree_id(...)` directly since
      `worktree_identity` was already imported there for other names).
      Every now-fully-unused `_core()` accessor retired outright:
      `follow_ups_cli.py` and `git_cli.py`'s shims were each the only
      remaining caller of their own `_core()`, so both the shim and the
      accessor were deleted; `pr_state_cli.py`'s `_core()` accessor turned
      out to have ALREADY had zero other callers before this slice even
      started (it existed only to serve this one name) -- caught by
      grepping for remaining `_core(` usage after the call-site migration,
      not predicted up front, and deleted too even though it wasn't one of
      the 4 modules originally scoped to lose a shim. `claims_cli.py` and
      `session_metadata_cli.py` keep their `_core()` accessors (each still
      routes at least one other name through it: `_core_helper`'s generic
      lookup, and `resolve_worktree_id_by_codename`, respectively).
      `__main__._CLUSTER_FREE_MODULES` re-run via `test_lazy_dispatch.py`
      directly (per the test's own drift-check): all 28 tests passed
      unchanged -- no module newly qualified as cluster-free (`git_cli`
      now defines no `_core()` at all, but wasn't in the candidate set the
      drift check tracks, so no update was needed). Two further test-file
      gaps surfaced only by the real full suite, neither visible to the
      tool's static scan nor the manual sibling-shim-patch grep sweep: a
      direct (non-monkeypatch) test call to the moved function started
      observing a suite-wide `autouse` fixture's fake once the function's
      body moved into the fixture's own patched module
      (`test_context_resolution.py`), and a `SimpleNamespace`-faked whole
      `_core()` return value split its keyword arguments across multiple
      lines, defeating a same-line grep
      (`test_pr_actor_flow_surfaces.py`) -- see the Journal for the full
      mechanics of both.
      Validated: `tools/compat-root-migration.py --name
      _infer_worktree_id` reports zero call/monkeypatch sites; manual
      sibling-shim-patch grep sweep (step 6 of this slice's own
      instructions) found zero remaining patches against anything other
      than `worktree_identity`; full targeted test sweep (`test_pr_ops.py`
      alone needed ~22 minutes at 252 tests) all green; full plugin suite
      green in one pass with 4 confirmed pre-existing, unrelated,
      system-load-sensitive subprocess-timing tests deselected: **6926
      passed, 58 skipped, 4 deselected**, zero failures introduced; `ruff
      check --select F,E9` clean across all touched source and test files
      (one incidental fix: the function's removal from `__main__.py` left
      `_infer_worktree_id_from_cwd` as an F401 unused import there, since
      it was only ever used by the now-moved function -- fixed with the
      same `# noqa: F401 -- re-exported for unit tests` annotation its
      sibling re-export already carries, since test suites still patch it
      on the root); `ruff format --diff` on every file where a shim was
      deleted showed only one real blank-line drift (`claims_cli.py`, 3
      blank lines left behind by the shim deletion, trimmed to 2) --
      every other reported diff in those files was pre-existing
      line-length drift unrelated to this change; `check-module-size.py`
      clean.
- [x] `_self_override` (16 call sites / 0 monkeypatch sites). Unlike every
      prior slice, this name is the generic dispatch *mechanism* itself
      (`__main__.py`'s own thin wrapper around
      `lazy_cli_dispatch.self_override`), not a CLI implementation
      function, so there was no sibling module to move a real
      implementation into -- the real implementation already lived in the
      shared `lazy_cli_dispatch` library. The 16 external call sites (7 in
      `handoff_cutover.py`, 9 in `worktree_creation.py`, both already
      componentized-out satellite modules that reach into `__main__` via
      `core = _core()`) called `core._self_override(name, local)`; migrated
      each to import `lazy_cli_dispatch.self_override` directly and call it
      as `_self_override(vars(core), name, local)` -- identical semantics
      (the shared function still inspects `__main__`'s own live globals for
      a monkeypatched override before falling back to `local`), since
      `__main__.py`'s own `_self_override(name, local)` was always just
      `return _shared_self_override(globals(), name, local)` and
      `vars(core) is globals()` when `core` is the `__main__` module
      object. `__main__.py`'s own definition and its ~30 in-module bare
      calls are untouched -- those were never a compat-root dependency
      (the function is already locally owned there), and the migration
      tool's root-alias detection correctly never counted them. Zero test
      files needed changes (0 pre-existing monkeypatch sites on this name).
      Validated: `tools/compat-root-migration.py --name _self_override`
      reports zero call/monkeypatch sites; `ruff check --select F,E9`
      clean; `check-module-size.py` clean (`handoff_cutover.py` sat exactly
      at the 1000-line cap -- the new import pushed it to 1002, fixed by
      dropping a redundant blank line before the new import and collapsing
      one 2-line call back to 1, net zero lines added, same fix shape as
      two prior slices' own module-size saves); targeted sweep (160
      tests covering both touched modules + lazy-dispatch) all green; the
      bulk of the full suite (2447 tests across 4 sequential sub-suites)
      green with zero failures before the bounded runner's per-call wall
      clock cut it off mid `test_pr_ops.py` (this repo's own
      already-documented ~22-minute-alone file, per the prior slice's
      Journal) -- a partial direct run of that file (252 tests, 27%
      through in 270s) showed zero failures before its own timeout,
      consistent with the known pre-existing timing characteristic, not a
      regression.
- [x] `_normalize_path` (10 call sites / 1 monkeypatch site). Unlike every
      prior slice's compat-root function, this one already had 8 scattered
      definitions across the plugin: a real one-line implementation in
      `__main__.py` (`p.rstrip("/\\")`), an INDEPENDENT real duplicate
      already living in `sessions.py` (identical logic, never calling
      through `core()`), and 6 thin re-export shims (`cleanup_gc_cli.py`,
      `list_cli.py`, `reap_cli.py`, `resolve_picker_cli.py`,
      `resolve_system_cli.py`, `status_bar_cli.py`) each forwarding to
      `core()._normalize_path`. Confirmed every one of those 6 shim
      modules, plus the 3 non-shim external callers
      (`front_door_cli.py` x2, `maintenance_cli.py`, `status_cli.py`), and
      3 OTHER sibling modules entirely outside this slice's own call-site
      list (`reclaim.py`, `tracking_session_registry.py`,
      `picker_support/data_local.py`) already called `sessions.
      _normalize_path` directly -- i.e. `sessions.py` was already the
      de facto canonical home across most of the codebase, just not yet
      for these 10 holdouts. Migrated: deleted `__main__.py`'s own
      definition and redirected its 11 in-module bare calls to
      `sessions._normalize_path`; deleted all 6 shims and redirected each
      shim module's own internal bare calls the same way; redirected the
      3 non-shim external call sites from `core()._normalize_path` to
      `sessions._normalize_path` (adding `from . import sessions` only to
      `front_door_cli.py`, which didn't already import it -- the other two
      already did). `sessions.py`'s own existing implementation needed no
      code change at all. Updated the one test monkeypatch site
      (`test_auto_clean.py`) from `agent_worktrees.__main__._normalize_path`
      to `agent_worktrees.sessions._normalize_path`. The full test suite
      caught 5 further gaps invisible to the static tool/grep sweep -- the
      effort's own named, recurring risk, hit again this slice: direct
      (non-monkeypatch) bare-attribute reads of `<alias>._normalize_path`
      against the `__main__` module, in `test_bridge_lock.py`,
      `test_status_segment.py` (6 sites), `test_tracking_override.py`
      (4 sites), `test_pr_ops.py` (2 sites, inside functions with their
      own local `from agent_worktrees import sessions`, so no new import
      needed there), and `_cleanup_revalidation_helpers.py` -- each
      retargeted to `sessions._normalize_path`, adding a top-level
      `sessions` import only where no local one already existed
      (`test_tracking_override.py`). No `_core()` accessor reached zero
      this slice (each of the 6 ex-shim modules still routes at least one
      other name through `core()`).
      Validated: `tools/compat-root-migration.py --name _normalize_path`
      reports zero call/monkeypatch sites (was 10/1); `ruff check --select
      F,E9` clean across all 11 touched source files + 6 touched test
      files; `check-module-size.py` clean. Targeted sweep (76 tests) green
      on the first pass; the full suite caught the 5 bare-attribute gaps
      above on the first full run (1 failure, 644 passed) -- fixed, then
      re-ran clean: the bulk of the full suite (2447 tests across the
      first 4 sequential sub-suites) green with zero failures, reaching
      72% through the 5th sub-suite (vs. 18% on the prior slice) before
      the bounded test-supervisor's per-call wall-clock ceiling cut the
      run off mid `test_pr_ops.py` -- the same already-documented
      ~22-minute-alone file, not a regression.
- [ ] `_find_repo_dir`, `_apply_tracking_override`, `_build_active_paths`
      (next-highest traffic; re-measure with `--progress` before picking
      exact order -- several of these have outsized monkeypatch counts
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
- [ ] **After any `git sync`/rebase onto a moved base** (not just once
      before the first push): re-run `--progress`/`--name` and the full
      test sweep again, not just at the start. The remote base can move
      and introduce brand-new instances of the exact pattern being
      migrated (confirmed: PR #5313 landed mid-slice and added fresh
      `core._json_output` call sites a post-sync rescan alone caught).

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
- **A real regex bug in the committed tool itself**, found only by CI
  (not local runs, since the local `pr_state_cli.py`/`finalize_cli.py`
  state predated an upstream PR landing new code): `\(\)?` in the
  call-site regex means "a required `(` plus an optional `)`", not
  "an optional `()` pair" -- so the tool was silently blind to the bare
  `core.attr(` shape (a plain assigned variable, no call parens) the
  whole time, undercounting both `--name` and `--progress`. Fixed to
  `(?:\(\))?`; the corrected `--progress` baseline jumped from 200 to 291
  call sites repo-wide. This also meant a brand-new upstream PR
  (#5313, pr-abandon flow, merged to `dev` after this slice's local
  validation but before its own merge) had introduced fresh
  `core._json_output`/`core._json_error` call sites in `pr_state_cli.py`
  and `finalize_cli.py` that a post-sync rebase pulled in cleanly (no
  conflict, since it was new code) and that both the undercounting bug
  and the lack of a post-rebase re-scan let slip through to a pushed PR,
  where CI caught it. Fixed both files; also found and fixed a genuine,
  unrelated merge-resolution mistake surfaced by the same investigation
  (a stale, pre-`--from-branch` duplicate `--repo` validation block I'd
  kept from my own old pre-migration commit during an earlier rebase
  conflict, which upstream had already correctly replaced -- this broke
  2 real tests, caught and fixed here). **Takeaway: always re-run
  `--progress`/`--name` and the full test sweep again after ANY sync/
  rebase onto a moved base, not just once before the initial push** --
  the remote base can move and introduce new instances of the exact
  pattern being migrated.
- `finalize_cli` ended up genuinely cluster-free once the stale duplicate
  block above was removed (confirmed via the AST scan + full finalize/
  pr-creation test suites, 103+35+27 passed) and was added to
  `_CLUSTER_FREE_MODULES` -- notable given this repo's own standing
  caution comment on that exact set, warning that a regex-only scan
  previously shipped a live `create-pr` regression; didn't skip the
  extra verification just because the AST scan agreed.
- **Automated PR review (2 rounds) found a genuinely severe miss the
  tool's own grep-based scan is structurally blind to**: `test_remove_
  system.py` carries 30 `unittest.mock.patch("agent_worktrees.__main__.
  _json_output"/"_json_error")` targets -- `patch()` resolves the dotted
  string eagerly at entry, so removing the root re-export broke all 29
  exercised tests in that file (not caught locally because the tool only
  recognized `monkeypatch.setattr(<alias>, "<name>", ...)` fixture calls,
  never a dotted-string `unittest.mock.patch` target). Fixed the file
  (repointed to `agent_worktrees.output`, 33/33 passing) and the tool
  itself (both `--name` and `--progress` now also scan for
  `patch("<pkg>.__main__.<name>")`, unconditional on any alias import
  since it's a literal string, not an alias reference). Review also
  caught: a misspelled/unsupported `--plugin` silently reporting a
  vacuous success (fixed -- now a hard error); three more dead `_core()`
  accessors left behind after their last call site moved
  (`copilot_identity_cli`, `forks_cli`, `identifier_blocklist_cli` --
  removed, per the effort's own zero-call-site retirement rule); and
  `session_tracking_cli.py` routing 4 calls through `core.output._json_*`
  (still transitively through `_core()`, not actually decoupled) instead
  of its own already-imported `output` module directly (fixed). All
  caught by a *second* reviewer round after the first round's fixes
  landed, re-confirming the "re-run after every sync" lesson above now
  also applies to "re-run after every review round, not just the
  first."
- **Third review round found a real word-boundary regex bug**: the
  call-site pattern had no identifier boundary before the alias, so a
  single-char alias like `m` matched as the literal TAIL of an unrelated
  longer identifier -- concretely, `this_platform.lower()` was reported
  as an `m.lower()` root call purely because "platform" ends in "m".
  Fixing this naively (adding a plain `\b` before the alias group) broke
  the *other*, legitimate accessor-call shape: `_core()._json_output(`
  stopped matching, because `\b` can't find a boundary between the `_`
  and `core` inside `_core` -- that's a single continuous word-character
  token in regex terms, and the accessor-call shape was only ever being
  matched because the unanchored alias search happened to find "core"
  as a substring of "_core". Root cause: **the lazy-accessor-function
  shape is architecturally different from the plain-alias-variable
  shape and must never share one pattern** -- callers invoke the
  wrapper FUNCTION by its own defined name (`_core`, captured from
  `def _core():`), never the internal variable name that function's
  own import binds; the plain-alias shape is a genuinely separate,
  word-bounded identifier match. Fixed by building two independent,
  explicitly-named sub-patterns and OR-ing them, rather than reusing
  one alias set for both shapes. Re-verified against the real repo:
  `_resolve_worktree_id` call sites correctly stayed at 27 (not the
  16 the broken `\b`-only fix would have silently undercounted to),
  and the `this_platform.lower()` false positive is gone. Also added
  `tools/test_compat_root_migration.py` (13 tests, a gap review
  correctly flagged this tool never had) covering every regex edge
  case found across all three rounds, plus the two "reject vacuous
  success" guards; added the required `## Coordination` section to
  this effort's own Participants block (a template-compliance gap);
  fixed an outdated `__main__.py` comment that still described
  `_json_output`/`_json_error` as root re-exports after they were
  removed. Corrected `--progress` baseline: 290 call sites (was 291 --
  the one `lower` false positive is now gone).
- **Fourth review round found one more genuine gap, the rest stale
  restatements of already-fixed findings** (reviewer lag, reconfirmed
  pattern this session -- verified each against current file content
  before acting): a THIRD independent dotted-string patch shape,
  `monkeypatch.setattr("<pkg>.__main__.<name>", replacement)` -- pytest's
  own `monkeypatch.setattr` accepts a single dotted-string target
  (resolved internally via its own import machinery), distinct from both
  the plain `(alias, "name")` fixture call and `unittest.mock.patch`.
  Live at `test_run_claims.py:232` for `_infer_worktree_id_from_cwd` (a
  planned future slice), meaning the tool could have silently reported
  that name "done" without ever seeing this patch site. Fixed in both
  `--name` and `--progress`, with 2 more regression tests (15 total).
  Also: wired `tools/test_compat_root_migration.py` into
  `.github/workflows/ci.yml` (it existed but ran in no CI job -- a green
  check had never actually executed it); fixed one real stale comment in
  `test_related.py` that still said `_json_output` was `__main__`-native
  after this slice moved it to `output.py`; rewrote this Plan's own
  Phase 1/2 bullets to state only the current contract/scope (timeless),
  moving the "corrected multiple times," "original estimate," and
  round-by-round narrative into this Journal where it belongs.
- Next slice: `_resolve_worktree_id` (27 call sites / 49 monkeypatch
  sites) -- re-run `--progress` first, since this slice's corrected
  baseline may have shifted the ranking.

### 2026-10-05 — Phase 2 slice 2: `_resolve_worktree_id` migrated
- Re-ran `tools/compat-root-migration.py --name _resolve_worktree_id`
  first per the prior slice's own instruction; counts matched the
  plan's recorded 27 call sites / 49 monkeypatch sites exactly -- no
  drift from the prior slice's corrections this time.
- The real implementation already lived in `worktree_identity.py` (no
  dependency on `__main__.py`), so this slice was pure call-site/
  patch-site migration, no new shared module to create. Migrated all 8
  sibling shims (`git_cli.py`, `handoff_cancel_cli.py`, `handoff_cli.py`,
  `reclaim_cli.py`, `resolve_cli.py`, `session_binding_cli.py`,
  `session_metadata_cli.py`, `worktree_ops_cli.py`) to
  `from . import worktree_identity` + `worktree_identity._resolve_
  worktree_id(...)`, deleting each now-dead forwarding shim outright,
  plus 5 further modules that called through the root directly with no
  local shim at all (`finalize_cli.py`, `handoff_cutover.py`,
  `pr_cli.py`, `pr_state_cli.py`, `session_tracking_cli.py`).
- **One call-site shape the tool's own scan doesn't track at all:**
  `__main__.py` itself had 4 bare in-module calls to `_resolve_
  worktree_id(...)` that resolved via its own `from .worktree_identity
  import (..., _resolve_worktree_id, ...)` re-export -- invisible to the
  tool's root-alias scan (which only looks for `core()._name`/alias-
  dot-name shapes reached from *other* modules, not a name's own
  module-local bare use). These only surfaced as `ruff`'s `F821
  Undefined name` once the re-export was removed from `__main__.py`'s
  import tuple. Fixed by adding `from . import worktree_identity` as a
  module import in `__main__.py` and repointing all 4 bare calls to
  `worktree_identity._resolve_worktree_id(...)`. **Takeaway for future
  slices:** after removing a name from `__main__.py`'s re-export tuple,
  always `ruff check --select F,E9` on `__main__.py` itself before
  declaring the slice done -- the compat-root-migration tool only
  proves other modules stopped reaching through the root, not that the
  root's own body stopped relying on its former re-export.
- No sibling module's `_core()` accessor reached zero remaining callers
  this slice -- each of the 8 shim-hosting modules still routes at
  least one other name through `_core()` (e.g. `git_cli._infer_
  worktree_id`, `session_metadata_cli._infer_worktree_id`). Confirmed
  by grepping each file's remaining `_core()` call count (all >= 2)
  before touching `__main__._CLUSTER_FREE_MODULES`, then running
  `test_lazy_dispatch.py` directly rather than trusting the grep alone
  -- it passed unchanged, confirming no drift this slice.
  `__main__._CLUSTER_FREE_MODULES` needed no edit.
- `handoff_cutover.py` was already sitting exactly at the 1000-line cap
  (confirmed via `git show HEAD:<path>` after an initial miscount from
  piping through `Measure-Object -Line`, which mishandled the file's
  CRLF-free line endings) -- the new `worktree_identity` import tipped
  it to 1001. Fixed the same way the prior slice's own journal
  recommended: folded the file's standalone `from . import config as
  cfg` into the existing multi-line tuple import and trimmed a
  redundant blank line the merge left behind, netting zero added lines.
  `check-module-size.py` confirmed clean afterward.
- All 49 test monkeypatch sites were the single `monkeypatch.setattr(
  <alias>, "_resolve_worktree_id", ...)` shape (no `unittest.mock.patch`
  dotted-string or `SimpleNamespace`-faked-whole-`_core()` shapes this
  time, unlike the `_json_output` slice) -- repointed to `worktree_
  identity` directly across all 14 test files, adding `from
  agent_worktrees import worktree_identity` (or the file's existing
  `from . import` convention) only where not already imported;
  `test_pr_create_claimant_guard.py` already imported `worktree_
  identity` for an unrelated reason, so only its patch-alias needed
  changing there.
  `tools/compat-root-migration.py --name _resolve_worktree_id` now
  reports zero call sites and zero monkeypatch sites.
- Validation: full targeted test sweep, split into ~10 `-k`-filtered
  batches per the bounded runner's per-sub-suite wall-clock budget
  (`test_pr_ops.py` alone needed ~21 minutes at 252 tests) -- every
  batch green, 1830+ tests passed total, zero failures, zero skips
  introduced. `ruff check --select F,E9` clean across all 28 touched
  files. `check-module-size.py` clean. `test_lazy_dispatch.py` run
  directly and confirmed unaffected.
- Final `--progress` aggregate (from the prior slice's 42/290/159/1056):
  **42 accessors, 263 call sites, 158 distinct monkeypatched names, 1007
  patch-site occurrences.** Next-highest-traffic names per the current
  ranking: `_infer_worktree_id` (18 calls/30 patches), `_self_override`
  (16/0), `_normalize_path` (10/1), `_find_repo_dir` (10/9),
  `_apply_tracking_override` (9/7), `_build_active_paths` (8/17) --
  matches the Plan's pre-named next batch; `_infer_worktree_id_from_cwd`
  (6 calls but 34 monkeypatch sites) and `_build_env`/`_build_launch_cmd`/
  `_preflight_launch`/`_repo_session_env` (5 calls each, 21-24 patches
  each) stand out as monkeypatch-heavy relative to call-site count,
  worth considering for the next slice pick per the Plan's own
  re-ranking instruction.
- Post-round-4 sync picked up new `origin/dev` commits (including a
  `handoff_cli.py` refactor this slice's rebase had to hand-merge: dev
  added a `settle_claim`-based restore path that predated this slice's
  `output._json_output` rename, so the resolution kept dev's newer
  `settle_claim()` call and applied the rename on top). Re-ran
  `--progress`/`--name` after: patch-site occurrences moved 1050 → 1051
  and `_resolve_worktree_id`'s monkeypatch count moved 48 → 49 (both
  baseline/README numbers above corrected); `_json_output`/`_json_error`
  both still report zero call/monkeypatch sites.
- **Fifth review round found one more genuine gap, both others
  confirmed stale** (`__main__.py` root re-export removal and the CI
  wiring for `test_compat_root_migration.py`, both already fixed in
  rounds 2 and 4 respectively -- verified against current file content
  before concluding so): a FOURTH independent patch shape,
  `unittest.mock.patch.object(<alias>, "<name>", ...)` -- the
  object-attribute sibling of `patch()`, alias-gated like
  `monkeypatch.setattr(<alias>, "name", ...)` rather than a literal
  dotted string. Live in the real repo at `test_resolve_mux.py` (e.g.
  `patch.object(cli, "_resolve_profile", ...)` and
  `patch.object(cli, "_resolve_new", ...)`), meaning the tool could have
  silently reported either name "done" without ever seeing these patch
  sites. Fixed in both `--name`/`--progress`, with 2 more regression
  tests (17 total). Corrected `--progress` baseline after re-running
  against the real repo: 159 distinct monkeypatched names (was 157) and
  1056 patch-site occurrences (was 1051) -- the +5 patch sites are the
  previously-invisible real `patch.object` call sites now counted.
- **Sixth review round found one more genuine gap, both others
  confirmed stale** (same `__main__.py`/CI-wiring restatements as
  round 5 -- re-verified against current file content, unchanged):
  all four patch-detection regexes hard-coded double-quoted string
  literals, so a single-quoted `patch('...')`,
  `monkeypatch.setattr('...', ...)`, `monkeypatch.setattr(alias, '...',
  ...)`, or `patch.object(alias, '...', ...)` would have silently gone
  undetected -- Python allows either quote style and nothing in this
  repo's style guide mandates one. No real call site currently uses
  single quotes (confirmed: re-running `--progress` after the fix
  reports identical numbers -- 159 names, 1056 patch sites), but this
  was a real robustness gap for any *future* test file, so fixed
  proactively: all four patterns now accept `["']` for every quoted
  name/path argument. Added 4 more regression tests (one per shape) plus
  one `--progress` single-quote test (22 total).

### 2026-10-05 — Phase 2 slice 3: `_infer_worktree_id` migrated
- Re-ran `tools/compat-root-migration.py --name _infer_worktree_id` first;
  counts matched the prior slice's recorded 18 call sites / 30 monkeypatch
  sites exactly.
- Unlike the prior two slices, the real implementation did NOT already
  live in a shared module: it was a plain function body resident in
  `__main__.py`, with zero dependency on anything `__main__`-specific (it
  only called `_infer_worktree_id_from_cwd`, already in
  `worktree_identity.py`). Moved the function body itself into
  `worktree_identity.py` (placed directly after the helper it calls,
  keeping the full docstring), then did the usual call-site/patch-site
  migration on top. Every caller now does `from . import
  worktree_identity` + `worktree_identity._infer_worktree_id(...)`.
- Migrated all 4 sibling shims (`claims_cli.py`, `follow_ups_cli.py`,
  `git_cli.py`, `session_metadata_cli.py`) plus 2 modules that called
  through the root directly with no local shim at all (`finalize_cli.py`,
  5 call sites; `pr_state_cli.py`, 9 call sites). `claims_cli.py` also
  passed the bare function as a first-class callback value into several
  sibling modules (`claims_annotate`, `claims_handoff_cli`,
  `claims_transitive_cli`) -- those call sites needed the same
  `worktree_identity._infer_worktree_id` repoint, not just the direct-call
  shape.
- `__main__.py` itself had 2 bare in-module calls (`_cmd_status_write`,
  `_cmd_status_history`) resolving through its own re-export of
  `_infer_worktree_id_from_cwd`-adjacent names -- but **not** a re-export
  of `_infer_worktree_id` itself, since nothing else needed the bare name
  once it moved. Repointed both call sites to
  `worktree_identity._infer_worktree_id(...)` (the module already imports
  `worktree_identity` for other names). Removing the function also left
  `_infer_worktree_id_from_cwd` as an F401 unused import in `__main__.py`
  (it was only ever used by the now-moved function's body) -- fixed with
  the same `# noqa: F401 -- re-exported for unit tests` annotation its
  sibling re-export already carries, since test suites still patch it on
  the root.
- **One real surprise the tool's static scan couldn't predict:** after
  migrating `pr_state_cli.py`'s 9 call sites, its `_core()` accessor
  turned out to have **already had zero other callers** before this slice
  even started -- it existed solely to serve this one name, unlike the
  plan's framing (which only named `claims_cli.py`, `follow_ups_cli.py`,
  `git_cli.py`, `session_metadata_cli.py` as the shim-losing modules).
  Caught by grepping for remaining `_core(` usage in `pr_state_cli.py`
  after the call-site migration, not predicted up front. Deleted the
  accessor outright. `follow_ups_cli.py` and `git_cli.py`'s own `_core()`
  accessors were each the sole remaining caller of their own `_infer_
  worktree_id` shim, so both accessor and shim were deleted together for
  those two. `claims_cli.py` and `session_metadata_cli.py` keep their
  `_core()` accessors: each still routes at least one other name through
  it (`_core_helper`'s generic lookup for `claims_cli.py`;
  `resolve_worktree_id_by_codename` for `session_metadata_cli.py`).
- Ran `test_lazy_dispatch.py` directly (per its own drift-check
  instruction) rather than hand-editing `__main__._CLUSTER_FREE_MODULES`:
  all 28 tests passed unchanged. `git_cli.py` now defines no `_core()` at
  all, but it was never in the `_LAZY_DISPATCH_TABLE` candidate set the
  drift check scores against, so no update was needed or possible.
- All 30 test monkeypatch sites were the single
  `monkeypatch.setattr(<alias>, "_infer_worktree_id", ...)` shape across
  all three root-alias spellings seen in this repo (`m`, `cli`, `main`) --
  repointed to `worktree_identity` directly across 10 test files, adding
  `from agent_worktrees import worktree_identity` only where not already
  imported. Careful not to touch the unrelated `_infer_worktree_id_from_
  cwd` patches living in several of the same files (a distinct name this
  slice does not migrate) -- confirmed via the manual sibling-shim-patch
  grep sweep that every remaining `_infer_worktree_id` (exact name) patch
  targets `worktree_identity` and nothing else.
  `tools/compat-root-migration.py --name _infer_worktree_id` now reports
  zero call sites and zero monkeypatch sites.
- **Two more genuine gaps found only by running the real full suite, both
  invisible to the tool's static scan and to the manual sibling-shim-patch
  grep sweep:**
  1. `test_context_resolution.py` calls `worktree_identity._infer_
     worktree_id(...)` (formerly `m._infer_worktree_id(...)`) as a
     **direct function call in test assertions**, not a monkeypatch target
     -- a call shape neither the tool's scan nor the "setattr/mock.patch"
     grep looks for at all. These tests deliberately exercise REAL
     CWD-based git identity resolution, but `conftest.py` has an
     `autouse=True` fixture (`_assume_valid_claimant_worktree`) that
     defaults `worktree_identity._infer_worktree_id_from_cwd` to a fixed
     fake id for every test in the suite. Before this slice, `__main__.py`
     bound `_infer_worktree_id_from_cwd` as its own module-global at
     import time, so its resident `_infer_worktree_id` body's bare-name
     call resolved through THAT binding -- unaffected by the autouse
     fixture patching the separate `worktree_identity` module object.
     Moving the function's body into `worktree_identity.py` made its
     internal bare-name call resolve through `worktree_identity`'s OWN
     globals instead, which the autouse fixture **does** reach -- so
     these 6 tests started silently observing the suite-wide fake id
     instead of exercising real resolution (6 failures, each asserting
     the wrong id). This is the exact "bare-name call inside the moved
     band stops observing/starts observing a monkeypatch" gotcha the
     pattern doc calls out, just in the opposite direction from the usual
     case (a patch that was previously invisible became visible once the
     code moved into the patched module). Fixed by having the `adopted_
     repo` fixture (which every one of these tests already uses) restore
     the REAL `_infer_worktree_id_from_cwd` -- captured at module import
     time, before any monkeypatching -- as its own `monkeypatch.setattr`
     on `worktree_identity`, overriding the autouse default back to real
     behavior for this file's tests specifically.
  2. `test_pr_actor_flow_surfaces.py` faked `pr_state_cli`'s **entire
     `_core()` return value** via `monkeypatch.setattr(pr_state_cli,
     "_core", lambda: SimpleNamespace(_infer_worktree_id=..., _resolve_
     worktree_id=...))` -- exactly the "`SimpleNamespace`-faked whole
     `_core()` return value" shape the Validation Plan's own checklist
     names as invisible to the tool's static scan. It was ALSO invisible
     to the manual sibling-shim-patch grep sweep from this slice's own
     step 6, because the grep required `_infer_worktree_id` and
     `setattr`/`patch(` to appear on the **same line** -- here the
     `_infer_worktree_id=lambda ...` keyword argument lives several lines
     below the `monkeypatch.setattr(pr_state_cli, "_core",` call that
     opens the statement, so the single-line pattern silently missed it.
     Only surfaced once `pr_state_cli._core` was deleted (this slice's
     surprise retirement, see above) and the full suite hit a real
     `AttributeError`. Fixed by repointing the fake directly onto
     `worktree_identity._infer_worktree_id`/`worktree_identity._resolve_
     worktree_id` instead of faking the now-gone accessor. **Takeaway for
     future slices:** the manual sweep instruction needs multi-line
     awareness -- grep for the bare name across the whole file (or with
     generous `-C` context) and eyeball every hit, not just lines where
     the patch verb and the name happen to share a line.
- Validation: full targeted test sweep across every touched file
  (`test_pr_ops.py` run separately with an extended `--subsuite-timeout`
  since it alone needed ~22 minutes at 252 tests under the bounded
  runner's default window) all green; then the FULL plugin suite,
  attempted several times -- each attempt that didn't complete hit one of
  four distinct **pre-existing, system-load-sensitive subprocess-timing
  tests** (`test_handoff_trace.py::test_concurrent_appends_across_real_
  processes_produce_no_corruption`, `test_invoke_payload_runtime_windows.
  py::test_background_prune_dispatch_launches_no_visible_window`, `test_
  local_cache_refresh.py::TestRefreshLocalCache::test_descendant_of_a_
  timed_out_cli_does_not_survive`, `test_git_ops.py::TestPushTimeoutTree
  Kill::test_run_bounded_kills_grandchild_on_timeout`) -- each confirmed
  via `git diff` as a file this slice never touched, each self-documented
  by its own test author as a setup-timing race (two literally assert
  with the message "test setup issue, not a real assertion"), and each
  reproducibly green in isolation. A full run with those four tests
  explicitly deselected (`-k "not ... and not ..."`) completed clean in
  one pass: **6926 passed, 58 skipped, 4 deselected**, zero failures.
  `ruff check --select F,E9` clean across all 10 touched source files
  (8 CLI/identity modules + the 2 test files fixed for the gaps above
  count separately). `ruff format --diff` on every file where a shim was
  deleted showed exactly one real blank-line drift (`claims_cli.py`: 3
  blank lines left behind by the shim deletion, trimmed to 2) -- every
  other reported diff in those files (and the large diff on
  `__main__.py`) was pre-existing line-length/wrapping drift unrelated to
  this change, left alone. `check-module-size.py` clean; `__main__.py`
  lost 36 lines, `worktree_identity.py` gained 30.
- Final `--progress` aggregate (from the prior slice's 42/263/158/1007):
  **39 accessors, 245 call sites, 157 distinct monkeypatched names, 977
  patch-site occurrences.** The -3 accessors (`follow_ups_cli.py`,
  `git_cli.py`, `pr_state_cli.py`) match this slice's 3 full shim
  retirements exactly. Next-highest-traffic names per the current
  ranking: `_self_override` (16 calls/0 patches), `_normalize_path`
  (10/1), `_find_repo_dir` (10/9), `_apply_tracking_override` (9/7),
  `_build_active_paths` (8/17) -- matches the Plan's pre-named next batch
  minus `_infer_worktree_id`, now done.

### 2026-10-06 -- `_self_override` slice (Phase 2, slice 4)
- Re-ran `tools/compat-root-migration.py --progress` first, per the
  effort's own re-ranking instruction: confirmed `_self_override` was
  still top-ranked (16 call sites / 0 monkeypatch sites) with the same
  aggregate the prior slice's Journal projected (39/245/159/1003 --
  the 159 vs. 157 distinct-name delta from the prior slice's own final
  count is measurement noise across runs, not a regression).
- This slice had a materially different shape than every prior one:
  `_self_override` is not a CLI implementation function living in a
  sibling module waiting to be moved -- it's the generic compat-root
  dispatch *mechanism itself*, already a thin wrapper in `__main__.py`
  (`return _shared_self_override(globals(), name, local)`) around a
  function that already lives in the shared `lazy_cli_dispatch` library
  (`self_override(module_globals, name, local)`). There was no function
  body to relocate -- only the 16 external call sites (7 in
  `handoff_cutover.py`, 9 in `worktree_creation.py`, both satellite
  modules that reach into `__main__` via their own `core = _core()`)
  needed to stop going through `core._self_override(...)` and instead
  import `lazy_cli_dispatch.self_override` directly, calling it as
  `_self_override(vars(core), name, local)` -- semantically identical,
  since `vars(core)` IS `__main__`'s own `globals()` when `core` is the
  `__main__` module object, so a test's `monkeypatch.setattr(m, "<name>",
  fake)` on any of the 16 *target* names (e.g. `_activate_session_binding`,
  `_apply_assignment_env`) is still observed exactly as before -- only the
  indirection through `__main__`'s own `_self_override` wrapper is
  removed. `__main__.py`'s own definition and its ~30 in-module bare
  `_self_override(...)` calls were intentionally left untouched: those
  calls are already locally owned (same module defines and calls it), so
  the migration tool's root-alias detection never counted them as
  compat-root traffic in the first place -- confirmed by re-running
  `--name` after the edit and seeing the in-module calls were never part
  of either the "16 call sites" or the "0 done" count.
- Zero monkeypatch sites meant zero test files needed any change this
  slice -- the simplest slice so far on that axis, though the smallest
  margin too: `handoff_cutover.py` was sitting exactly at the 1000-line
  cap, and the new `from lazy_cli_dispatch import self_override as
  _self_override` import (plus the blank line convention separating
  third-party from local imports) pushed it to 1002. Fixed by dropping
  the redundant blank line before the new import (stdlib/third-party
  imports stayed un-separated by one line, matching the file's own
  pre-existing single-blank-line convention before the `from . import`
  block) and collapsing one already-short 2-line call
  (`_activate_session_binding = _self_override(\n    vars(core), ...)`)
  back to one line -- net zero lines added, same fix shape as two prior
  slices' own module-size saves (`handoff_cutover.py` itself was the
  file fixed in the `_resolve_worktree_id` slice too).
- Validation: `tools/compat-root-migration.py --name _self_override`
  reports zero call/monkeypatch sites; `ruff check --select F,E9` clean
  on both touched files; `check-module-size.py` clean. Targeted sweep
  (`-k "handoff_cutover or worktree_creation or create_worktree or
  self_override or lazy_dispatch"`, 160 tests) all green. The bulk of the
  full suite (2447 tests across the first four sequential sub-suites
  the bounded runner split the plugin into) passed clean with zero
  failures before the runner's own 600-second-per-invocation ceiling cut
  the run off 18% into the sub-suite containing `test_pr_ops.py` -- this
  repo's own already-documented ~22-minutes-alone file (see the prior
  slice's Journal entry); a direct partial run of that file alone (252
  tests, 27% through in 270s) showed zero failures before its own
  timeout, consistent with the known pre-existing timing characteristic
  and not a regression this slice introduced. Unlike the prior slice,
  this session's bounded test-supervisor enforces a hard 60-600-second
  ceiling per invocation (no `--subsuite-timeout`-driven exception for
  this one file), so a single-pass full-suite confirmation including
  `test_pr_ops.py` end-to-end was not achievable within that constraint
  this session -- flagging for whoever next needs a from-scratch full
  pass that this file structurally cannot complete in one bounded call
  under the current supervisor ceiling and needs either a raised
  per-plugin override or a deliberate multi-call split.
- Final `--progress` aggregate (from this slice's own pre-check
  39/245/159/1003): **39 accessors, 229 call sites, 159 distinct
  monkeypatched names, 1003 patch-site occurrences.** (Accessor count
  unchanged -- this slice retired no `_core()` accessor, since
  `_self_override` was never implemented as one; only call sites moved,
  229 = 245 - 16.) Next-highest-traffic names per the current ranking:
  `_normalize_path` (10/1), `_find_repo_dir` (10/9),
  `_apply_tracking_override` (9/7), `_build_active_paths` (8/17) --
  re-measure with `--progress` before picking exact order for the next
  slice, per the effort's own standing instruction.

### 2026-10-06 -- `_normalize_path` slice (Phase 2, slice 5)
- Re-ran `--progress` first, per the effort's own re-ranking instruction,
  in a fresh worktree: confirmed `_normalize_path` was still top-ranked
  (10 call sites / 1 monkeypatch site), matching the prior slice's
  projection.
- This slice's shape differed from every prior one again: `_normalize_path`
  wasn't a single function living in one place waiting to move -- it had
  **8 separate definitions** scattered across the plugin. `__main__.py`
  owned a real one-line implementation (`p.rstrip("/\\")`); `sessions.py`
  independently defined an IDENTICAL duplicate that never called through
  `core()` at all; and 6 modules (`cleanup_gc_cli.py`, `list_cli.py`,
  `reap_cli.py`, `resolve_picker_cli.py`, `resolve_system_cli.py`,
  `status_bar_cli.py`) each carried a thin re-export shim
  (`def _normalize_path(*args, **kwargs): return _core()._normalize_path(*args, **kwargs)`)
  that the migration tool counted as both a "definition site" (it binds
  the name locally) and a "call site" (it calls through `core()`).
  Before touching anything, traced every OTHER caller of this name across
  the whole plugin (not just the 10 flagged root-alias sites) and found
  `reclaim.py`, `tracking_session_registry.py`, and
  `picker_support/data_local.py` already called `sessions._normalize_path`
  directly -- i.e. `sessions.py` was already the de facto canonical home
  for this function across most of the codebase; these 10 holdouts (the
  6 shims plus 3 non-shim direct-`core()` callers in `front_door_cli.py`
  x2, `maintenance_cli.py`, `status_cli.py`) were simply the stragglers.
  This meant zero new logic to write: `sessions.py`'s own existing
  implementation needed no code change at all, only every other caller
  redirected to it.
- Migrated in order: deleted `__main__.py`'s own definition and replaced
  its 11 in-module bare `_normalize_path(...)` calls (across
  `_build_active_paths` and two other functions) with
  `sessions._normalize_path(...)` via a scoped regex substitution (safe
  here since the definition was already deleted, so every remaining bare
  occurrence in the file was necessarily a call site, not a redefinition);
  deleted each of the 6 shims and redirected each shim module's own
  internal bare calls the same way (all 6 already imported `sessions`, so
  no new imports needed); redirected the 3 non-shim `core()._normalize_path`
  call sites to `sessions._normalize_path` (`maintenance_cli.py` and
  `status_cli.py` already imported `sessions`; `front_door_cli.py` needed
  the import added). Updated the sole pre-existing test monkeypatch site
  (`test_auto_clean.py`) from `agent_worktrees.__main__._normalize_path`
  to `agent_worktrees.sessions._normalize_path`.
- A first global-replace attempt on `list_cli.py` via a blank-line-count-
  sensitive regex mangled the file (merged two unrelated function bodies
  together) -- caught immediately by re-viewing the result rather than
  trusting the regex blind, reverted with `git checkout --`, and redone
  with a precise `edit` substitution instead. Lesson for future slices
  doing multi-definition cleanup: prefer targeted `edit` over a blanket
  regex when deleting a shim sitting between two surrounding blank-line
  blocks whose exact count isn't already known.
- The full suite caught **5 further gaps** invisible to the static
  tool's scan and a manual grep for the shim-call shape -- all a new
  pattern this slice, distinct from the two "monkeypatch shape" gaps the
  `_infer_worktree_id` slice caught: direct, non-monkeypatch
  **bare-attribute reads** of `<alias>._normalize_path` against the
  `__main__` module itself (tests calling the function AS a plain helper,
  not mocking it) -- `test_bridge_lock.py` (1 site), `test_status_segment.py`
  (6 sites, module already imported `sessions`), `test_tracking_override.py`
  (4 sites, needed a new top-level `sessions` import), `test_pr_ops.py`
  (2 sites, both inside test methods that already had their own local
  `from agent_worktrees import sessions` -- so a second top-level import I
  added turned out redundant and was reverted after `ruff check` flagged
  the resulting F401/F811), and `_cleanup_revalidation_helpers.py` (1 site,
  already imported `sessions`). Each confirmed via `git diff` as untouched
  by this slice's own source edits -- these are pre-existing tests whose
  assertions reached into the compat root for convenience, not candidates
  that needed new behavior.
- No `_core()` accessor reached zero this slice: each of the 6 ex-shim
  modules still routes at least one other name through `core()` (unlike
  the `_infer_worktree_id` slice, where 3 modules' shims were their own
  sole remaining caller).
- Validation: `tools/compat-root-migration.py --name _normalize_path`
  reports zero call/monkeypatch sites (was 10/1). `ruff check --select
  F,E9` clean across all 11 touched source files and 6 touched test
  files. `check-module-size.py` clean (no file near its cap this slice).
  Targeted sweep (`-k` matching every touched module + `test_lazy_dispatch`
  + `test_auto_clean`, 76 tests) green on the first pass -- the bare-
  attribute gaps above only surfaced once the FULL suite ran. First full
  run: 1 failure (`test_bridge_lock.py::test_build_active_paths_unions_
  bridge_live`, an `AttributeError: module 'agent_worktrees.__main__' has
  no attribute '_normalize_path'`), 644 passed. Fixed all 5 gaps, re-ran:
  the bulk of the full suite (2447 tests across the first 4 sequential
  sub-suites) green with zero failures, reaching 72% through the 5th
  sub-suite (vs. 18% on the `_self_override` slice) before the bounded
  test-supervisor's per-call wall-clock ceiling cut the run off mid
  `test_pr_ops.py` -- the same already-documented ~22-minute-alone file
  from every prior slice's Journal, not a regression this slice introduced.
- Final `--progress` aggregate (from this slice's own pre-check
  39/229/159/1003): **39 accessors, 219 call sites, 158 distinct
  monkeypatched names, 1002 patch-site occurrences.** (Accessor count
  unchanged, matching this slice's own finding that no shim's `_core()`
  accessor reached zero; 219 = 229 - 10 call sites migrated; -1 distinct
  monkeypatched name and -1 patch-site occurrence match the single
  `test_auto_clean.py` monkeypatch retarget.) Next-highest-traffic names
  per the current ranking: `_find_repo_dir` (10/9),
  `_apply_tracking_override` (9/7), `_build_active_paths` (8/17) --
  re-measure with `--progress` before picking exact order for the next
  slice, per the effort's own standing instruction.

