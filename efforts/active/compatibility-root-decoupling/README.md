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

## Coordination

- **Topology:** independent per-slice PRs (no shared feature branch) --
  each migration slice (one or a small batch of names) is its own PR
  against `dev`, opened from its own worktree, reviewed and merged
  independently before the next slice starts.
- **Host (owns PRs):** lambda-core (sole participant; single-agent effort,
  no delegation currently in play).
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
      alias (`core = _core()` then `core.attr(...)`), and three
      independent test-side patch shapes --
      `monkeypatch.setattr(<alias>, "<name>", ...)` (single- or
      multi-line), `unittest.mock.patch("<pkg>.__main__.<name>")`, and
      `monkeypatch.setattr("<pkg>.__main__.<name>", ...)`. Whole-identifier
      matching (not a substring/suffix match) throughout. `--plugin` and
      `--name` both fail loud on an unresolvable plugin or a name with no
      definition/call/patch site anywhere, rather than reporting a vacuous
      success. Current baseline: **42 accessors, 290 call sites, 157
      distinct monkeypatched names, 1050 patch-site occurrences.** Covered
      by `tools/test_compat_root_migration.py`.

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
- [ ] `_resolve_worktree_id` (27 call sites / 48 monkeypatch sites) —
      next slice.
- [ ] `_infer_worktree_id`, `_self_override`, `_normalize_path`,
      `_find_repo_dir`, `_apply_tracking_override`, `_build_active_paths`
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
- Next slice: `_resolve_worktree_id` (27 call sites / 48 monkeypatch
  sites) -- re-run `--progress` first, since this slice's corrected
  baseline may have shifted the ranking.
