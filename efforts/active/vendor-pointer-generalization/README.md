# Vendor Pointer Generalization

- **Slug:** `vendor-pointer-generalization`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-26
- **Status:** Draft
- **Vision:** none yet, deliberately — this effort does not introduce a new
  architectural direction; it extends `dev-branch-release-pipeline`'s own
  already-active, still-vision-less design (that umbrella effort's own
  header carries the identical "none yet" note for the same reason: the
  design itself predates any spawned vision doc). The governing pattern
  invariant this effort must keep satisfying is `docs/install-contract.md`'s
  self-contained-shipped-payload guarantee (see Context) — no new invariant
  is introduced, so no new pattern doc is required *before* Phase 1; Phase 3
  writes `docs/patterns/vendor-pointer.md` to record the (unchanged)
  invariant plus the (new) pointer-kind mechanics once they exist.
- **Umbrella issue:** _TBD — file once this effort's plan clears review_
- **Sub-issues:** _TBD_

## Guiding Intent

`vendored-doc-pointers` (Done, pending archive) proved the DRY vendor-pointer
pattern for one narrow surface (a single mirrored Markdown file) and the
`dev-branch-release-pipeline` umbrella built the general-purpose machinery
around it: `sync-vendored-libs.py` already understands a `VENDOR_POINTER.json`
stub as a valid vendored-copy form, and `materialize_main.py` already expands
both pointer kinds (directory/lib pointers, file pointers) from canonical at
promotion time. A standalone trial run converted all 56 real vendored-lib
copies to pointers and proved the round-trip byte-for-byte lossless — but
**no real plugin in this repo carries a directory/lib pointer yet** (three
real files already carry `kind=file` pointers, converted in
`vendored-doc-pointers`' own Phase 2 — the gap is specifically the
directory/lib kind); the mechanism is built and proven, but not applied to
that surface.

This effort's goal: close that gap, and generalize the *pattern itself* —
not just apply it once more — to every construct in this repo that is
duplicated byte-for-byte across plugins at authoring time: real shared-lib
copies (today: byte copies + a drift-detecting checker, `check-vendored-libs-
sync.py`), the shared installer-engine surface (`vendored-installer-engine`,
a separate Active effort currently designed around byte-vendor +
`sync-installer-engine.py --check` rather than a pointer stub), and whatever
else surfaces as "and more" during Phase 1's audit. The pointer pattern's
core promise — one canonical source, a thin stub on `dev`, full expansion
only in the materialized `main` release the install-contract already
requires to be self-contained — replaces "copy + a script that catches
drift after the fact" with "impossible to drift, by construction," for every
surface it's applied to.

A second, distinct requirement from the same request: unlike a Markdown doc
pointer (whose stub is inert, human-readable text — nobody executes a doc),
a **script** pointer must remain **runnable in `dev`** without a
materialize/preview step first, so in-place testing keeps working. This
likely needs a new pointer *kind* beyond the existing directory/file
forms — an executable stub that calls across plugin-folder boundaries into
the canonical engine at dev-time, expanded to a fully self-contained inlined
copy only when `materialize_main.py` builds the release `main` sees (never
resolved across folders in what ships, matching `docs/install-contract.md`'s
existing "no shared install module resolved at
install or runtime" constraint for the *shipped* artifact — this effort does
not relax that constraint, only what `dev`'s authoring-time form looks like
beneath it).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Designs and lands all phases | independent per-phase worktree |

## Coordination

- **Topology:** single driver, sequential phases (each phase's own worktree,
  its own PR, `pr-self-merge`).
- **Host (owns PRs):** the driving agent/machine.
- **Delegates:** none yet.
- **Handoff:** each phase closes with the relevant `tools/test_*.py` suite
  (and, once real plugins carry pointers, the full
  `python tools/run-plugin-tests.py` sweep for every touched plugin) green
  and its PR merged before the next phase's worktree opens.

## Context

Repo: `ThomasMichon/copilot-extensions`.

- **`efforts/active/vendored-doc-pointers`** (Done, pending archive) — the
  effort this one continues in spirit. Landed the generalized single-file
  pointer marker (`<!-- VENDOR_POINTER: source=... kind=file -->`) and
  converted `docs/patterns/entity-relationship-model.md`'s three plugin
  mirrors to real pointers. Its own `materialize_main.py` docstring already
  frames this as "the whole-repo counterpart," anticipating exactly this
  generalization.
- **`efforts/active/dev-branch-release-pipeline`** (Active) — the umbrella
  that built the actual pointer machinery: `tools/sync-vendored-libs.py`
  (`--check` / `--restore-canonical` / `--materialize`, pointer-aware since
  its 2026-09-23 trial-port-back), `tools/materialize_main.py` (whole-repo
  pointer expansion, wired into the live `promote_release.py` pipeline —
  every real promotion already runs this), and `tools/preview_release.py`
  (the "preview-promo" script referenced in the Request — builds a scratch,
  fully-materialized local copy of one plugin for local-install testing,
  read-only against the real repo). Its Journal (2026-09-23, "Standalone
  DRY-pointer trial + port-back") documents the validated trial: all 56 real
  vendored-lib copies across 10 shared libs converted to
  `VENDOR_POINTER.json` stubs in an isolated clone, materialized back
  byte-for-byte identical, ~73% dev-tree size reduction for that surface —
  then explicitly deferred applying it to the real repo ("that conversion is
  deliberately left for Phase 2, not bundled into this tooling PR"). That
  deferred conversion never got picked up as a tracked Phase 2 item and is
  this effort's Phase 1.
- **`efforts/active/vendored-installer-engine`** (Active, Draft) — a
  parallel, independently-scoped effort collapsing every `agent-*` plugin's
  installer entrypoint (`install.ps1`/`.sh` or `init.ps1`/`.sh`) into a
  **shared engine** (`scripts/installer-engine.{sh,ps1}` — the byte-
  identical, non-varying mechanics) plus a **thin per-service
  wrapper/config** that stays genuinely per-plugin (launch command,
  capability flags, sibling installs — never collapsed; see that effort's
  own README, "Scale" and Phase 0/1 sections). Explicitly **decided
  against** a git-fetch bootstrap in favor of byte-vendoring the shared
  engine specifically (see its own "Design decision" Journal entries),
  citing the same install-contract constraint this effort's script-pointer
  design must also respect — but its chosen *mechanism* for keeping the
  engine's vendored copies in sync is `tools/sync-installer-engine.py
  --check` (a drift detector: copies can go stale until CI catches it), not
  a pointer stub (drift structurally impossible on `dev`). This effort's
  Phase 2 scopes the new executable pointer kind to that same shared-engine
  surface only — never the per-plugin wrapper/config, which must keep
  varying by plugin — and evaluates whether the engine, once it lands,
  should be re-expressed as a pointer instead of a byte-copy; coordinate
  with that effort's own driver before changing its chosen mechanism out
  from under it, and do not silently fork its design.
- **`docs/install-contract.md`** — the governing constraint both this effort
  and `vendored-installer-engine` must keep satisfying: the *shipped*
  payload is completely self-contained, with nothing resolved from a
  sibling plugin or a git checkout at install/runtime. This effort's
  dev-time "call across folders" design is compatible with that constraint
  precisely because `dev` is never what ships — only `main`'s materialized
  output is, and materialization already fully inlines every pointer kind
  before a promotion commit is built.
- No `docs/patterns/vendor-pointer.md` (or equivalent) exists yet describing
  the pointer kinds, their lifecycle, and which tool owns which invariant —
  this effort's Phase 3 should write it, consolidating what is currently
  only documented piecemeal across several tools' docstrings and two
  efforts' Journals.

## Request

> We should continue this effort in spirit and broaden it to all file types
> and constructs for which we want to support vendoring in this repo. For
> example, the install sh and ps1 files, reused vendor libs, and more. Of
> course, for doing local installs, we want to run the "preview-promo"
> script, which puts a build into a scratch location locally, but being able
> to run in-place test scripts in `dev` is useful, so code-pointers should
> still work by calling across folders, even if the final version gets
> fully vendored together.

(Verbatim operator instruction, given after landing `agent-worktrees:
hard-block @copilot mentions in GitHubProvider PR ops` (#3700) and its
forward note in `pull-request-capability` (#3718) — a detour from this
worktree's original effort, `vendored-doc-pointers`, discovered mid-flight
while finishing that effort's Phase 3.)

## Plan

### Phase 0 — Audit the real remaining surface (no code changes)
_(agent-recommended, mirroring `vendored-installer-engine`'s own Phase 0
shape before committing to a design)_
- [ ] Enumerate every currently byte-duplicated-across-plugins construct in
      the repo (shared libs, installer engine, and any other "and more"
      candidates — e.g. shared CI guard scripts, shared skill fragments) and
      classify each as: already pointer-capable but unconverted (real libs),
      designed for byte-vendor+drift-checker today (installer engine), or
      not yet addressed by any mechanism.
- [ ] Confirm the existing `VENDOR_POINTER.json`/file-pointer forms are
      sufficient for the lib surface as-is, or whether real-world use
      surfaces a gap the isolated trial didn't (e.g. a lib with local,
      genuinely-per-plugin modifications layered on top of the canonical
      source — the trial's 56 copies were presumably byte-identical to
      canonical already; confirm that holds for every real copy before
      converting, since a pointer has no way to express a local diff).

### Phase 1 — Convert real shared-lib copies to pointers
- [ ] **Define the dev-time resolver before converting anything** (blocking
      design gap found in review): `tools/run-plugin-tests.py` installs each
      plugin's dependencies via an editable `uv pip install -e` (not
      `uv sync` — corrected in review), which resolves
      `plugins/<plugin>/libs/<lib>` as a real local path package
      (`[tool.uv.sources]`) — a directory containing only
      `VENDOR_POINTER.json` (no `src/`, no `pyproject.toml`) is not
      installable and the editable install fails outright. Decide and
      document one of: (a) the pointer directory keeps a real
      `pyproject.toml` so it's still a valid package, and only `src/`
      becomes a stub the resolver expands before install; or (b)
      `run-plugin-tests.py` gains a materialize-first step (mirroring
      `preview_release.py`'s "materialize into a scratch copy, never touch
      the real tree" pattern) that expands every pointer into its own
      temporary test tree before the editable install runs there. Do not
      convert a single real lib copy until this is resolved and proven
      against one real plugin.
- [ ] Apply the already-validated, already-built mechanism
      (`sync-vendored-libs.py`'s pointer support, `materialize_main.py`'s
      expansion) to the real repo: convert every real
      `plugins/<plugin>/libs/<lib>/src` copy that is byte-identical to its
      canonical `libs/<lib>` source into a `VENDOR_POINTER.json` stub,
      using whichever resolver design the item above settled on. **Also
      cover `worktree-manager/libs/*`** (confirmed in review — both
      `sync-vendored-libs.py` and `check-vendored-libs-sync.py` already
      scan this extra tree alongside `plugins/*/libs/*`; leaving it
      unconverted while extending `materialize_main.find_pointers()`
      elsewhere would leave those copies out of scope by omission, not
      decision). If any part of that surface is deliberately excluded,
      state that explicitly rather than leaving the gap implicit.
- [ ] **`tools/check-vendored-libs-sync.py` does not currently recognize
      pointers at all** (confirmed in review — it hashes whatever `src/`
      exists and checks versions; it has no `VENDOR_POINTER` awareness, and
      is a separate tool from `sync-vendored-libs.py`, which does). After
      conversion, an empty or malformed pointer directory could silently
      read as "synchronized" to this checker. Extend
      `check-vendored-libs-sync.py` (or name and build a new dedicated
      guard) to validate a pointer's schema and confirm its `source` target
      actually exists and matches canonical, before treating any real
      conversion as complete.
- [x] **`materialize_main.py`'s directory-pointer path has no root-
      containment check** — **Done, PR #3752.** `materialize()`'s
      directory-pointer path now routes through `_resolve_within()`
      identically to the file-pointer path. Review found this needed to go
      further: the resolved canonical directory's own `src/` tree could
      still contain (or itself be) a symlink escaping containment
      (`shutil.copytree` follows symlinks by default) — added
      `_find_symlink()`, refusing any symlink under a canonical lib's
      `src/` outright, and a destination-side check (`_escapes_root()`,
      shared with `_resolve_within()`) refusing a pointer whose own
      directory resolves outside `dest` via a symlinked ancestor.
      `build()`'s initial whole-tree snapshot copy also now preserves
      symlinks (`symlinks=True`) instead of dereferencing them, closing a
      gap where content could leak before `materialize()`'s own checks
      ever ran. `find_pointers()` also now covers `worktree-manager/
      libs/*` (see the item below, done as part of the same PR).
- [x] **`materialize_main.py`/`find_pointers()` tooling now covers
      `worktree-manager/libs/*`** — **Done, PR #3752** (the tooling gap;
      the actual real-copy conversion for that tree is still the
      "Apply the already-validated mechanism" item above, not yet done).
- [x] **Promotion must fail closed on any unresolved pointer, not just log
      it** — **Done, PR #3752.** `consume_pending_changes()` now inspects
      `materialize()`'s log for any `SKIP` entry and raises
      `PromotionError`, aborting the promotion.
- [ ] **`preview_release.py` cannot currently build a scratch install for a
      pointerized lib** (confirmed in review — `_materialize_into_preview()`
      calls `sync_vendored_libs._materialize_blocked()`, which treats a
      pointer directory with no `src/` as content drift and blocks it).
      Update `_materialize_into_preview()` (and its tests) to recognize and
      correctly expand a `VENDOR_POINTER.json` lib copy, matching how
      `materialize_main.py` already does — this is required before Phase 1
      can call itself complete, not just an assumption the Validation Plan
      hopes holds.
- [ ] Confirm the live promotion pipeline (`promote_release.py` ->
      `materialize_main.py`) handles the converted real plugins correctly —
      not just the isolated trial clone.
- [ ] Confirm every consuming plugin's own test suite
      (`tools/run-plugin-tests.py <plugin>`) still passes post-conversion,
      using the dev-time resolver design from the first item above.

### Phase 2 — Executable (script) pointer kind for the shared installer engine
- [ ] **Scope correction (from review): this kind applies only to
      `vendored-installer-engine`'s shared engine files**
      (`scripts/installer-engine.{sh,ps1}`), never to a whole plugin's
      `install.sh`/`install.ps1`/`init.*` entrypoint — that entrypoint stays
      a per-service wrapper/config (launch command, capability flags,
      sibling installs) that must keep varying by plugin, per that effort's
      own design. Pointerizing a whole entrypoint would discard exactly
      that per-plugin behavior.
- [ ] Design the new pointer kind: an executable stub that, invoked
      directly on `dev` (no materialize step), calls across the
      plugin-folder boundary into the canonical shared-engine location
      (mirroring how a `dev`-mode Python import already resolves an
      unconverted lib directly from `libs/<lib>` with no local copy at all
      — confirm whether that's actually how Python resolves it today, or
      whether a `sys.path`/workspace config makes it work, before assuming
      the script-pointer case is analogous). **Must preserve the existing
      dot-source contract** (confirmed in review — the POSIX and
      PowerShell installer wrappers dot-source `scripts/installer-engine.*`
      because the engine defines functions the caller consumes directly; a
      stub that merely invokes the canonical file as a child process cannot
      make those functions available in the wrapper's own scope). Test both
      the POSIX (`source`) and PowerShell (`.`) dot-source paths explicitly
      before adopting this pointer kind — a child-process-launching stub is
      not an acceptable substitute.
- [ ] Extend `materialize_main.py` to expand the new kind: replace the
      dev-time cross-folder-calling stub with the fully inlined canonical
      script content in the `main` snapshot, so what ships never resolves
      anything outside its own plugin's payload (per `docs/install-
      contract.md`). **Apply the same root-containment guarantee required
      for directory pointers** (confirmed in review — the executable
      kind's `source` is equally committed metadata; without rejecting an
      absolute path or a `../`/symlink escape before copying, promotion or
      preview could read arbitrary runner-local script content into the
      shipped plugin). Add regression coverage for a missing/escaping
      executable source in both materializers (real + preview).
- [ ] **`tools/preview_release.py` has no materialization path for an
      executable pointer** (confirmed in review — its scratch-preview build
      only calls `_materialize_into_preview()` for library copies and
      `materialize_file_pointers()` for file markers; a script pointer
      would remain an unexpanded dev stub in the preview, even though the
      Validation Plan requires previews to resolve it). Add the explicit
      preview-materialization change and regression tests for the new kind
      here, not just in `materialize_main.py`.
- [ ] Coordinate with `vendored-installer-engine`'s own driver/Journal
      before changing its chosen byte-vendor mechanism — this may land as
      a phase *within* that effort instead of duplicated here, once its
      canonical engine exists to point at. Do not fork its design
      unilaterally.
- [ ] Build drift/consistency checking for the executable pointer kind
      **without conflating vendoring surfaces** (corrected in review —
      `sync-vendored-libs.py` is scoped to `plugins/*/libs/*` and library
      `src/`/version materialization; the installer-engine surface already
      has its own `tools/sync-installer-engine.py` for
      `scripts/installer-engine.*` copies, with its own adopter map).
      Assign this to either a new, generic pointer validator/materializer
      shared across all pointer kinds, or to `sync-installer-engine.py`
      itself — not to the lib-scoped tool.
- [ ] **`tools/check-install-contract.py`'s existing install-contract guard
      calls `sync-installer-engine.py`'s `verify()` directly** (confirmed
      in review — it compares each adopter file byte-for-byte against the
      canonical engine). If a pointerized engine copy is no longer a byte
      copy, this call path will reject every converted adopter as
      out-of-sync. Update or replace this call path as part of the
      executable-pointer rollout — not merely add a new, unconnected
      validator alongside a guard that still fails.

### Phase 3 — Document the pattern; sweep for further "and more" candidates
- [ ] Write `docs/patterns/vendor-pointer.md`: the three (by then) pointer
      kinds, their lifecycle (dev stub -> `materialize_main.py` expansion ->
      shipped `main` content), which tool owns which invariant, and how a
      new plugin/lib/script opts in.
- [ ] Revisit whether any other currently-duplicated construct surfaced in
      Phase 0's audit ("and more") warrants conversion in this effort or a
      follow-on; file a tracked issue for anything deferred rather than
      dropping it silently.

## Validation Plan

- [ ] Every real lib copy converted in Phase 1 — every
      `plugins/<plugin>/libs/<lib>` copy **and every `worktree-manager/
      libs/*` copy** — round-trips losslessly: `materialize_main.py`'s
      output is byte-identical to the pre-conversion copy for each (the
      same `git diff --no-index` check the isolated trial used, run
      against the real repo this time).
- [ ] `tools/preview_release.py` ("preview-promo") correctly resolves every
      pointer kind — directory, file, and the new executable kind — when
      building a scratch local-install preview, using the
      `_materialize_into_preview()` fix from Phase 1 (not merely hoped to
      already work).
- [x] `materialize_main.py`'s directory-pointer path refuses a `source`
      that escapes `canonical_root` (the same containment guarantee the
      file-pointer path already has via `_resolve_within()`). **Done, PR
      #3752** — also extended to reject a symlink inside/as the canonical
      `src/` tree and a destination pointer path escaping `dest`.
- [x] A promotion run against a deliberately malformed/unresolvable pointer
      (missing `source`, escaping `source`) aborts the promotion rather
      than producing a `main` snapshot containing an unexpanded stub.
      **Done, PR #3752** (`test_promote_refuses_when_a_pointer_does_not_resolve`).
- [ ] A pointer-ized `scripts/installer-engine.{sh,ps1}` runs correctly
      **in dev**, unmaterialized, called from a plugin's own (never
      pointerized) `install.sh`/`install.ps1` wrapper across folders into
      the canonical engine — demonstrating the "in-place test scripts in
      `dev`" requirement is met without requiring
      `preview_release.py`/materialization first.
- [ ] The pointer-ized engine is correctly **dot-sourced** (`source` on
      POSIX, `.` on PowerShell) by the calling wrapper, not merely executed
      as a child process — functions the engine defines must be callable
      from the wrapper's own scope, on both platforms.
- [ ] A materialized `main` build of the same plugin is fully self-contained
      — no cross-folder reference remains in the shipped payload — per
      `docs/install-contract.md`.
- [ ] No existing plugin's test suite or live installer regresses from the
      Phase 1 lib-pointer conversion.

## Proposal

_Pending._

## Journal

### 2026-09-26 — Kickoff
- Effort created from an explicit operator request to broaden
  `vendored-doc-pointers` beyond docs, discovered as a detour while
  finalizing an unrelated `@copilot`-mention-guard hardening pass. Captured
  the request verbatim in Request; the Guiding Intent, Context, and Plan
  above summarize/organize the existing, already-substantial groundwork
  (`dev-branch-release-pipeline`'s pointer machinery, the isolated 56-copy
  trial, `vendored-installer-engine`'s parallel, differently-mechanized
  effort) rather than starting from a blank page — most of what this effort
  needs already exists; it was built, validated, and then not applied to
  the real repo (libs) or not yet designed for at all (executable
  scripts).
- Not started: no implementation yet. Next session should begin Phase 0, or
  if that's already implicitly proven, Phase 1's now-first item: define the
  dev-time resolver (test-runner installability of a pointer-only
  directory) before converting a single real lib copy.
- **Round 1 review (2026-09-26):** four findings, all addressed before
  merge: (1) Phase 1 didn't account for `tools/run-plugin-tests.py`
  installing lib copies as real `uv` path packages — a bare
  `VENDOR_POINTER.json` directory isn't installable; added a blocking
  "define the dev-time resolver first" item. (2) `check-vendored-libs-
  sync.py` was wrongly assumed pointer-aware (only `sync-vendored-libs.py`
  is, a separate tool) — corrected, and added a Phase 1 item to extend it
  with real schema/canonical-target validation. (3) Vision left unset with
  a deferred rationale — firmed up: this effort deliberately carries no new
  vision because it extends `dev-branch-release-pipeline`'s existing
  design under the same already-governing `install-contract.md` invariant,
  not a new architectural direction. (4) Guiding Intent's "no real plugin
  carries a pointer yet" wrongly read as covering all pointer kinds —
  qualified to the directory/lib kind specifically (three real files
  already carry `kind=file` pointers).
- **Round 2 review (2026-09-26):** two more findings, both confirmed
  against the real code and fixed: (1) `materialize_main.py`'s directory-
  pointer path resolves `canonical_root / source_rel` with no containment
  check, unlike the file-pointer path's `_resolve_within()` — a committed
  `../`-escaping `source` could make promotion copy an arbitrary path into
  the release snapshot. Added a Phase 1 item to fix `materialize()` before
  any real conversion. (2) `preview_release.py`'s
  `_materialize_into_preview()` calls `_materialize_blocked()`, which
  treats a pointer-only lib copy as content drift and blocks it today — a
  pointerized lib cannot yet produce a scratch preview. Added a Phase 1
  item to fix this directly (moved out of the Validation Plan, where it
  was only an assumption).
- **Round 3 review (2026-09-26):** two more findings on Phase 2's design
  completeness, both fixed: (1) `preview_release.py`'s scratch build has no
  materialization path for the (not-yet-designed) executable pointer kind
  — added an explicit Phase 2 item alongside the `materialize_main.py` one.
  (2) The drift/consistency-checking item wrongly assigned executable-
  pointer checking to the lib-scoped `sync-vendored-libs.py`, conflating it
  with the installer-engine surface's own `sync-installer-engine.py` —
  corrected to assign it to a new generic pointer validator or
  `sync-installer-engine.py` itself, never the lib tool.
- **Round 4 review (2026-09-26):** two more findings, both confirmed and
  fixed: (1) `promote_release.consume_pending_changes()` captures
  `materialize()`'s log but never inspects it for a `SKIP`/failure marker —
  a malformed or containment-rejected pointer would currently ride straight
  through into the shipped `main` snapshot as an unexpanded stub instead of
  blocking promotion. Added a Phase 1 item requiring promotion to fail
  closed on any unresolved pointer, plus a matching Validation Plan item.
  (2) Phase 2's scope was too broad: `vendored-installer-engine`'s own
  design keeps each plugin's `install.sh`/`install.ps1`/`init.*` as a
  per-service wrapper/config, with only `scripts/installer-engine.{sh,ps1}`
  as the byte-identical shared surface — pointerizing a whole entrypoint
  would discard per-plugin arguments and lifecycle behavior. Corrected the
  Phase 2 heading, design item, Context's description of that effort, and
  the Validation Plan's install-script item to scope the new pointer kind
  to the shared engine file only.
- **Round 5 review (2026-09-26):** one new finding, one previously-missed
  finding, both fixed: (1) the executable-pointer design didn't account for
  the existing installer wrappers **dot-sourcing** `scripts/installer-
  engine.*` (POSIX `source`, PowerShell `.`) so its functions land in the
  caller's own scope — a stub that just executes the engine as a child
  process can't provide that. Added an explicit dot-source-preservation
  requirement to the Phase 2 design item and a matching Validation Plan
  item testing both platforms. (2) Round-1's fix wrongly described the
  test runner's install command as `uv sync`; corrected throughout to the
  actual editable `uv pip install -e`.
- **Round 6 review (2026-09-26):** three more findings, all confirmed and
  fixed: (1) the executable pointer kind needed the same root-containment
  guarantee just added for directory pointers — added to the
  `materialize_main.py` item, with regression coverage required in both
  materializers. (2) Phase 1's real-conversion scope missed
  `worktree-manager/libs/*`, a tree both `sync-vendored-libs.py` and
  `check-vendored-libs-sync.py` already scan alongside `plugins/*/libs/*` —
  added explicit coverage (or an explicit exclusion decision) to that Plan
  item. (3) `check-install-contract.py`'s existing guard calls
  `sync-installer-engine.py`'s `verify()` directly, which byte-compares
  every adopter — a pointerized engine copy would fail that check even
  after a new pointer validator exists. Added a Phase 2 item to update or
  replace that call path.
- **Round 7 review (2026-09-26):** one small follow-on finding — the
  round-trip Validation Plan item only named `plugins/<plugin>/libs/<lib>`
  even though round 6 had already extended Phase 1's conversion scope to
  `worktree-manager/libs/*`. Fixed to cover both. **Concluding active
  review engagement here**: seven rounds have progressively narrowed from
  blocking design gaps (installability, containment, fail-closed
  promotion) to this kind of small cross-reference-consistency nit,
  consistent with CONTRIBUTING.md's own guidance against chasing a
  zero-finding pass that may never come. Merging once CI is green;
  further findings on execution (not on this plan document) belong in the
  phase PRs that actually implement it.

### 2026-09-26 — Phase 1 execution begins: safety-hardening slice landed (PR #3752)
- Started executing Phase 1. Rather than attempt the whole phase (dev-time
  resolver decision + bulk conversion + tooling extension) in one PR, split
  off the three findings that were already fully specified and independent
  of the still-undecided resolver design: directory-pointer containment,
  `worktree-manager/libs/*` tooling coverage, and promotion fail-closed
  behavior. Landed as `tools/materialize_main.py` + `tools/promote_release.py`
  changes, PR #3752, after 3 review rounds that found real, deeper gaps
  than the plan's own text anticipated:
  - Round 1: the containment fix itself (route directory pointers through
    `_resolve_within()`, extend `find_pointers()`).
  - Round 2 finding: `shutil.copytree` follows symlinks by default, so a
    canonical lib's `src/` containing (or itself being) a symlink would
    leak external content past the new containment check. Added
    `_find_symlink()`, refusing any symlink in a canonical lib source
    outright.
  - Round 3 findings (two): (a) `find_pointers()` follows a symlinked
    plugin/libs directory when globbing under `dest`, so a pointer whose
    own containing directory resolves outside `dest` would still be
    "found" and written to. Added `_escapes_root()` (shared with
    `_resolve_within()`) as a destination-side containment check. (b)
    `build()`'s initial whole-repo snapshot copy used
    `shutil.copytree(symlinks=False)`, dereferencing any symlink anywhere
    in the source tree *before* `materialize()`'s own checks ever ran —
    fixed by passing `symlinks=True` (confirmed safe: no tracked symlink
    exists in this repo today, via `git ls-files -s`).
  - 21 new tests total across the PR's four commits. Merged after CI green
    and merge state clean (a possible 4th review round was not observed
    within a reasonable wait after the 3rd fix; CI and merge-state were
    both clean, and this repo's review is advisory/non-blocking).
- Marked the containment, worktree-manager-tooling, and fail-closed Plan
  items `[x]` above; the Validation Plan's two matching items likewise.
- **Not yet done, still blocking the bulk conversion**: the dev-time
  resolver design decision (Plan's first Phase 1 item — how
  `run-plugin-tests.py`'s editable `uv pip install -e` handles a
  pointer-only lib directory), the `check-vendored-libs-sync.py` pointer-
  schema/canonical-target validation, the `preview_release.py` pointer-
  materialization fix, and the actual conversion of every real
  `plugins/*/libs/*` + `worktree-manager/libs/*` copy. Next session should
  pick up the dev-time resolver decision next, since the bulk conversion
  item is explicitly gated on it.
