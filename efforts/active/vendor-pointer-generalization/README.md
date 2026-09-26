# Vendor Pointer Generalization

- **Slug:** `vendor-pointer-generalization`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-09-26
- **Status:** Draft
- **Vision:** none yet — may fold into `dev-branch-release-pipeline`'s own
  eventual `visions/release-pipeline` entry rather than spawn a new one;
  revisit once Phase 1 firms up
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
**no real plugin in this repo carries a pointer yet**; the mechanism is built
and proven, but not applied.

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
a **script** pointer (`install.sh`/`install.ps1` and similar) must remain
**runnable in `dev`** without a materialize/preview step first, so in-place
testing keeps working. This likely needs a new pointer *kind* beyond the
existing directory/file forms — an executable stub that calls across
plugin-folder boundaries into the canonical engine at dev-time, expanded to a
fully self-contained inlined copy only when `materialize_main.py` builds the
release `main` sees (never resolved across folders in what ships, matching
`docs/install-contract.md`'s existing "no shared install module resolved at
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
  installer entrypoint (`install.ps1`/`.sh` or `init.ps1`/`.sh`) into one
  canonical engine. Explicitly **decided against** a git-fetch bootstrap in
  favor of byte-vendoring (see its own "Design decision" Journal entries),
  citing the same install-contract constraint this effort's script-pointer
  design must also respect — but its chosen *mechanism* for keeping vendored
  copies in sync is `tools/sync-installer-engine.py --check` (a drift
  detector: copies can go stale until CI catches it), not a pointer stub
  (drift structurally impossible on `dev`). This effort's Phase 2 evaluates
  whether that effort's canonical engine, once it lands, should be
  re-expressed as a pointer instead of a byte-copy — coordinate with that
  effort's own driver before changing its chosen mechanism out from under
  it; do not silently fork its design.
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
- [ ] Apply the already-validated, already-built mechanism
      (`sync-vendored-libs.py`'s pointer support, `materialize_main.py`'s
      expansion) to the real repo: convert every real
      `plugins/<plugin>/libs/<lib>/src` copy that is byte-identical to its
      canonical `libs/<lib>` source into a `VENDOR_POINTER.json` stub.
- [ ] Confirm `check-vendored-libs-sync.py` and the live promotion pipeline
      (`promote_release.py` -> `materialize_main.py`) both handle the
      converted real plugins correctly — not just the isolated trial clone.
- [ ] Confirm every consuming plugin's own test suite
      (`tools/run-plugin-tests.py <plugin>`) still passes post-conversion
      (a pointer stub changes what's on disk on `dev`; anything that
      imports the lib in a `dev`-mode test run must still resolve it
      correctly).

### Phase 2 — Executable (script) pointer kind for install.sh/install.ps1
- [ ] Design the new pointer kind: an executable stub that, invoked
      directly on `dev` (no materialize step), calls across the
      plugin-folder boundary into a canonical engine location (mirroring
      how a `dev`-mode Python import already resolves an unconverted lib
      directly from `libs/<lib>` with no local copy at all — confirm
      whether that's actually how Python resolves it today, or whether a
      `sys.path`/workspace config makes it work, before assuming the
      script-pointer case is analogous).
- [ ] Extend `materialize_main.py` to expand the new kind: replace the
      dev-time cross-folder-calling stub with the fully inlined canonical
      script content in the `main` snapshot, so what ships never resolves
      anything outside its own plugin's payload (per `docs/install-
      contract.md`).
- [ ] Coordinate with `vendored-installer-engine`'s own driver/Journal
      before changing its chosen byte-vendor mechanism — this may land as
      a phase *within* that effort instead of duplicated here, once its
      canonical engine exists to point at. Do not fork its design
      unilaterally.
- [ ] Extend `sync-vendored-libs.py`/`check-vendored-libs-sync.py`-equivalent
      guards (or a new dedicated one) to recognize the executable pointer
      kind for drift/consistency checking, matching the existing
      directory/file pointer guards.

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

- [ ] Every real `plugins/<plugin>/libs/<lib>` copy converted in Phase 1
      round-trips losslessly: `materialize_main.py`'s output for that
      plugin is byte-identical to the pre-conversion copy (the same
      `git diff --no-index` check the isolated trial used, run against the
      real repo this time).
- [ ] `tools/preview_release.py` ("preview-promo") correctly resolves every
      pointer kind — directory, file, and the new executable kind — when
      building a scratch local-install preview.
- [ ] A pointer-ized `install.sh`/`install.ps1` runs correctly **in dev**,
      unmaterialized, calling across folders into the canonical engine —
      demonstrating the "in-place test scripts in `dev`" requirement is met
      without requiring `preview_release.py`/materialization first.
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
- Not started: no implementation yet. Next session should begin Phase 0
  (confirm every real lib copy is genuinely byte-identical to canonical
  before Phase 1 converts it) or, if that's already implicitly proven by
  `check-vendored-libs-sync.py`'s existing clean state, skip straight to
  Phase 1.
