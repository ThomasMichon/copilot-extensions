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

## Design decision — npm-workspace-style live reference over a `VENDOR_POINTER.json` stub (resolved 2026-09-26)

Operator directive, captured verbatim in a later Request round below:

> Your mission is to come up with a way that we can run tests out of
> `dev`, and partial tool calls out of `dev`, and let most static analysis
> work out of `dev`, but then support proper vendoring during the
> promotion.
>
> If this were NPM, A and B package.json would just have references to C
> via relative path, and after the vendoring, C, would be copied into A
> and B's node_modules folders directly.

This directly resolves Phase 1's originally-blocking "define the dev-time
resolver" item, and **supersedes the `VENDOR_POINTER.json` directory/lib
pointer kind for the libs surface specifically** (the design PR #3700-era
plan assumed before this directive landed). The file-pointer kind (docs)
is unaffected — a Markdown doc has no package-manager equivalent of a
live relative-path reference, so it still needs a physical (stub) file on
disk.

**The mechanism, validated with a real `uv` prototype (see Journal) before
committing to it:**

- **On `dev`, a consuming plugin's `pyproject.toml` references the
  canonical `libs/<lib>` directly**, via `[tool.uv.sources]`'s existing
  `path` key pointed *up* at the repo-root canonical location instead of a
  local vendored copy, with `editable = true` added:
  ```toml
  agent-ssh-manager = { path = "../../libs/ssh-manager", editable = true }
  ```
  (today's real entries read `{ path = "libs/ssh-manager" }`, resolving to
  a local vendored copy *inside* the plugin — this changes only the path
  target and adds `editable = true`, nothing else in the plugin's own
  `pyproject.toml` changes shape). **No local `plugins/<plugin>/libs/<lib>`
  directory exists in `dev` at all** for a lib using this reference form —
  not a physical copy, not an empty stub, not a symlink. This is the exact
  npm-workspace analogue: "A and B's package.json reference C via relative
  path."
  - **Validated live-edit behavior**: `uv pip install -e .` against this
    config resolves `demo_lib.__file__` to the *canonical* source path
    (confirmed: editing the canonical file after install, with no
    reinstall, is picked up immediately on next import). This satisfies
    "run tests out of `dev`" and "call across folders in `dev`" exactly,
    for both `run-plugin-tests.py`'s editable install (Phase 1's original
    blocking problem — a pointer-only directory wasn't installable; this
    design has no local directory to install *from* at all, so the
    problem doesn't arise) and any static-analysis tool that resolves
    imports via the installed environment.
  - **`editable = true` is REQUIRED, not optional**: a plain `{ path =
    "../../libs/ssh-manager" }` (no `editable`) installs a frozen,
    non-live copy into site-packages even when the *top-level* package
    itself is installed with `-e` — confirmed by prototype. `editable`
    is set **per source entry**, independent of the top-level install
    mode.
  - **Also confirmed (important for the promotion-side fix below)**:
    `editable = true` on a source entry forces an editable install of
    *that* dependency even when the top-level package is installed
    **non-editable** (`uv pip install .`, no `-e`) — exactly how this
    repo's real end-user installers invoke it (`_uv_pip_install_resilient
    ... "$PLUGIN_DIR"`, confirmed no `-e` flag anywhere in
    `install.sh`). If promotion shipped a `main` payload that still
    carried the `../../libs/<lib>` + `editable = true` reference
    unchanged, a real end-user install would create an editable link
    pointing at a path that doesn't exist on their machine (they only
    receive `plugins/<plugin>/`, never the whole monorepo `libs/` tree) —
    an immediate `ImportError` in production. Promotion **must** rewrite
    the reference, not just copy content alongside it.
- **At promotion (`materialize_main.py`), for every `[tool.uv.sources]`
  entry whose `path` escapes the plugin's own directory** (i.e. resolves
  outside it, up toward the shared canonical root — reuse `_escapes_root()`
  from the existing containment work to detect this):
  1. Physically copy the canonical `libs/<lib>` directory's `src/` (and
     sync the `pyproject.toml` version, exactly as the now-superseded
     directory-pointer expansion already did) into a freshly-created local
     `plugins/<plugin>/libs/<lib>/`.
  2. Rewrite the `pyproject.toml` line: `path` becomes the new local
     relative path (`libs/<lib>`), and `editable = true` is dropped —
     restoring exactly today's real shipped form (`{ path =
     "libs/ssh-manager" }`), a plain non-editable local-path dependency
     fully self-contained within the plugin's own shipped payload. A
     surgical regex rewrite (matching this repo's existing convention —
     see `_VERSION_RE.sub`'s version-string patch — not a full TOML
     parse/rewrite, to preserve every plugin's hand-authored comments
     documenting *why* each lib is vendored) is sufficient: the real
     format is consistently one `<dist-name> = { path = "..." }` line per
     dependency (confirmed by inspecting `agent-bridge`/`agent-worktrees`/
     `agent-mcp`'s real `[tool.uv.sources]` tables).
  3. The relative-path depth from a consuming `pyproject.toml` up to the
     canonical root differs by consumer location (`plugins/<plugin>/` is
     two levels down -> `../../libs/<lib>`; `worktree-manager/` is one
     level down -> `../libs/<lib>`) — the rewrite tool must compute this
     from the actual file location, not hardcode a depth.
- **The same pattern replaces Phase 2's planned executable pointer kind
  entirely** — no new pointer format is needed. A plugin's `install.sh`/
  `install.ps1` **directly `source`s/dot-sources the canonical
  `scripts/installer-engine.sh`/`.ps1` via a relative path** in `dev` (this
  needs no `uv`/Python mechanism at all — a shell `source`/PowerShell `.`
  with a relative path argument works natively, cross-platform, with no
  symlink and no new marker format). At promotion, `materialize_main.py`
  rewrites that `source`/`.` line to reference (or fully inlines) the
  freshly-copied-in local engine file, the same two-step copy-then-rewrite
  operation as the libs case above.

**Why not a filesystem symlink** (the more literal reading of "reference
via relative path," and how a real npm/yarn workspace actually implements
it under `node_modules/`)? Rejected: a symlink requires Windows Developer
Mode/admin and `git config core.symlinks=true` to survive a checkout
faithfully on this repo's own Windows CI/contributor matrix — the `uv`
`path`+`editable` mechanism achieves the identical live-reference behavior
with zero filesystem symlinks, fully portable, and needs no git/OS
configuration at all.

**Consequence for already-landed work (PR #3752):** the directory/lib
`VENDOR_POINTER.json` pointer kind's containment hardening
(`_resolve_within`, `_find_symlink`, `_escapes_root` on the *destination*
side) becomes dead code for the libs use case specifically — no real
plugin ever adopts it now. This is not wasted effort: `_escapes_root()` is
reused directly by this new mechanism's promotion-side containment check
above, and the file-pointer kind (docs) continues using
`_resolve_within()`/`_find_symlink()`-equivalent protections unchanged.
Phase 1's remaining plan below retires the directory-pointer *loop* in
`materialize()` (and its now-unexercised tests) rather than leave an
unused, security-hardened-for-nothing code path sitting in the tool
indefinitely — tracked as its own Plan item so the retirement is a
reviewed, deliberate removal (per this repo's subtractive-change
convention), not a silent deletion.

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

**Follow-up round, 2026-09-26** (after PR #3752 landed the containment/
fail-closed hardening, still blocked on the dev-time resolver decision):

> Your mission is to come up with a way that we can run tests out of
> `dev`, and partial tool calls out of `dev`, and let most static analysis
> work out of `dev`, but then support proper vendoring during the
> promotion.
>
> If this were NPM, A and B package.json would just have references to C
> via relative path, and after the vendoring, C, would be copied into A
> and B's node_modules folders directly.

See the Design Decision above for the resolution.

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

### Phase 1 — Convert real shared-lib copies to canonical-reference form
- [x] **Define the dev-time resolver before converting anything** —
      **Resolved via the Design Decision above.** `uv`'s
      `[tool.uv.sources]` `path` + `editable = true`, referencing the
      canonical `libs/<lib>` directly (no local directory in `dev` at
      all), validated with a real prototype (see Journal). Supersedes the
      `VENDOR_POINTER.json` directory-pointer options (a)/(b) originally
      considered here.
- [ ] **Convert every real vendored lib to the canonical-reference form**:
      for every `plugins/<plugin>/libs/<lib>` (and `worktree-manager/
      libs/<lib>`) copy that is byte-identical to its canonical
      `libs/<lib>` source, (1) delete the local copy entirely (no
      directory, no stub — nothing remains at that path in `dev`), (2)
      rewrite the consuming `pyproject.toml`'s `[tool.uv.sources]` entry
      from `{ path = "libs/<lib>" }` to `{ path =
      "<relative-to-repo-root>/libs/<lib>", editable = true }` (the
      relative depth depends on the consumer's own location — two levels
      up from `plugins/<plugin>/`, one level up from `worktree-manager/`).
      Covers both trees (`sync-vendored-libs.py`/`check-vendored-libs-
      sync.py` already scan both today).
- [ ] **Build the conversion tool**: a script performing the rewrite above
      across every real consumer of a given lib in one pass (a new tool,
      or a new mode on `sync-vendored-libs.py` — this repo currently has
      no "convert a real copy to a pointer/reference" tool at all, only
      `--check`/`--restore-canonical`/`--materialize`, confirmed while
      investigating this phase). Must refuse to convert a lib whose real
      copies have already drifted from canonical (reuse
      `_materialize_blocked()`'s existing drift check) — converting a
      drifted copy would silently discard whatever the copy had that
      canonical didn't.
- [ ] **Build the new drift/consistency guard for the reference form**:
      `check-vendored-libs-sync.py` has no concept of this new reference
      form at all (it hashes whatever `src/` exists locally and compares
      copies against each other) — a `dev`-tree with no local copy
      shouldn't read as "missing"/"drifted." Extend it (or build a
      dedicated new guard) to recognize a `[tool.uv.sources]` entry whose
      `path` escapes the plugin's own directory as a valid, intentional
      reference form, confirm `editable = true` is present (a forgotten
      `editable` would silently produce the frozen-copy bug found in the
      prototype), and confirm the referenced canonical `libs/<lib>`
      actually exists.
- [ ] **Extend `materialize_main.py`/`promote_release.py` for the
      reference-rewrite promotion step**: for every `[tool.uv.sources]`
      entry whose `path` escapes the plugin's own directory (reuse
      `_escapes_root()`), copy canonical's `src/` (+ sync the
      `pyproject.toml` version, exactly as the old directory-pointer
      expansion already did) into a freshly-created local
      `plugins/<plugin>/libs/<lib>/`, then surgically rewrite the
      `pyproject.toml` line to the local, non-editable form (see Design
      Decision for the exact rewrite and why `editable = true` must never
      reach a real shipped install). Reuse `promote_release.py`'s existing
      fail-closed-on-`SKIP` behavior (PR #3752) for an unresolvable
      reference.
- [ ] **Retire the now-superseded directory/lib `VENDOR_POINTER.json`
      pointer kind** (deliberate, reviewed removal — not a silent
      deletion, per this repo's subtractive-change convention): no real
      plugin ever adopted it (confirmed before and after PR #3752), and
      the new canonical-reference form replaces its purpose entirely.
      Remove the directory-pointer loop from `materialize()`, its
      containment tests (`_find_symlink`'s directory-pointer call site,
      the destination-`_escapes_root` check's directory-pointer test
      cases), and `sync-vendored-libs.py`'s pointer-aware code added for
      it in the earlier standalone trial — keep `_resolve_within()`/
      `_escapes_root()`/`_find_symlink()` themselves, since the file-
      pointer kind and the new reference-rewrite step both still need
      them. Note the removal's rationale in this effort's Journal (not
      just the commit message) so a future reader doesn't wonder why a
      "generalized" pointer kind never got used.
- [ ] Update `tools/preview_release.py` ("preview-promo") to perform the
      same copy-then-rewrite operation into its scratch preview copy
      (never the real tree) for a plugin using the reference form — its
      current `_materialize_into_preview()` has no concept of this form
      at all yet (it was written against the directory-pointer design).
- [ ] Confirm the live promotion pipeline (`promote_release.py` ->
      `materialize_main.py`) handles the converted real plugins correctly.
- [ ] Confirm every consuming plugin's own test suite
      (`tools/run-plugin-tests.py <plugin>`) still passes post-conversion
      against the live canonical reference (no local copy, no reinstall
      needed to pick up canonical edits — validated in the prototype;
      confirm it holds for the real venv-caching/fingerprint logic too).

### Phase 2 — Canonical-reference form for the shared installer engine
- [x] **Scope correction (from review, still holds): this applies only to
      `vendored-installer-engine`'s shared engine files**
      (`scripts/installer-engine.{sh,ps1}`), never to a whole plugin's
      `install.sh`/`install.ps1`/`init.*` entrypoint — that entrypoint stays
      a per-service wrapper/config (launch command, capability flags,
      sibling installs) that must keep varying by plugin, per that effort's
      own design.
- [x] **No new pointer kind needed — superseded by the same Design
      Decision above.** A plugin's own `install.sh`/`install.ps1` directly
      `source`s/dot-sources the canonical `scripts/installer-engine.sh`/
      `.ps1` via a relative path in `dev` — this needs no `uv`/Python
      mechanism, no marker file format, and no new "pointer kind" at all:
      a shell `source`/PowerShell `.` with a relative path argument
      already works natively and preserves the dot-source contract (the
      engine's functions land in the calling wrapper's own scope) by
      construction, since it *is* a real dot-source, not a stub simulating
      one. This resolves the original Plan's dot-source-preservation
      concern by making it structurally unavoidable rather than a
      requirement to test for.
- [ ] At promotion, `materialize_main.py` (extended the same way as the
      libs case) rewrites that `source`/`.` line to reference (or fully
      inline) a freshly-copied-in local `scripts/installer-engine.{sh,ps1}`
      copy — the same copy-then-rewrite operation as Phase 1's libs
      conversion, reusing `_escapes_root()` for containment and
      `promote_release.py`'s existing fail-closed-on-`SKIP` behavior.
      Regression coverage for a missing/escaping engine reference in both
      materializers (real + preview).
- [ ] Extend `tools/preview_release.py` to perform the same copy-then-
      rewrite for a plugin referencing the engine canonically.
- [ ] Coordinate with `vendored-installer-engine`'s own driver/Journal
      before adopting this for its canonical engine, once that engine
      exists to reference — this effort does not fork that effort's design
      or timeline; it only proposes a simpler mechanism than that effort's
      current `sync-installer-engine.py --check` byte-vendor-and-verify
      approach, for that effort's own driver to evaluate and decide
      whether/when to adopt.
- [ ] If adopted, resolve `tools/check-install-contract.py`'s existing call
      into `sync-installer-engine.py`'s `verify()` (which currently
      byte-compares every adopter) so it recognizes the canonical-reference
      form as in-sync rather than rejecting every converted adopter.

### Phase 3 — Document the pattern; sweep for further "and more" candidates
- [ ] Write `docs/patterns/vendor-pointer.md`: the file-pointer kind (docs,
      unchanged) and the canonical-reference form (libs, and the shared
      installer engine if Phase 2 lands) — their lifecycle (a live
      relative-path reference in `dev` vs. a physical stub file, ->
      `materialize_main.py` copy-and-rewrite/expansion -> shipped `main`
      content), which tool owns which invariant, and how a new plugin/
      lib/script opts in.
- [ ] Revisit whether any other currently-duplicated construct surfaced in
      Phase 0's audit ("and more") warrants conversion in this effort or a
      follow-on; file a tracked issue for anything deferred rather than
      dropping it silently.

## Validation Plan

- [ ] Every real lib copy converted in Phase 1 — every
      `plugins/<plugin>/libs/<lib>` copy **and every `worktree-manager/
      libs/*` copy** — round-trips losslessly: `materialize_main.py`'s
      promotion output is byte-identical to the pre-conversion copy for
      each (the same `git diff --no-index` check the isolated trial used,
      run against the real repo this time).
- [ ] A converted plugin's own test suite (`tools/run-plugin-tests.py
      <plugin>`) passes with **no local `libs/<lib>` directory present at
      all** in the `dev` checkout — proving the canonical reference alone
      (via `[tool.uv.sources]` `path` + `editable = true`) is sufficient
      for `uv pip install -e .` and subsequent test/static-analysis
      resolution, matching the Design Decision's validated prototype.
- [ ] Editing the canonical `libs/<lib>` source and re-running a
      converted plugin's tests **without reinstalling** picks up the edit
      — the "in-place test scripts in `dev`" / "call across folders"
      requirement, demonstrated against a real plugin (not just the
      isolated prototype).
- [ ] `tools/preview_release.py` ("preview-promo") correctly performs the
      copy-then-rewrite for a plugin using the canonical-reference form
      when building a scratch local-install preview.
- [x] `materialize_main.py`'s directory-pointer path refuses a `source`
      that escapes `canonical_root` (the same containment guarantee the
      file-pointer path already has via `_resolve_within()`). **Done, PR
      #3752** — also extended to reject a symlink inside/as the canonical
      `src/` tree and a destination pointer path escaping `dest`. Reused
      directly by the reference-rewrite containment check (`_escapes_root`)
      once Phase 1's conversion tool lands; the directory-pointer *loop*
      itself is retired per Phase 1's own retirement item.
- [x] A promotion run against a deliberately malformed/unresolvable pointer
      aborts the promotion rather than producing a `main` snapshot
      containing an unexpanded stub. **Done, PR #3752** — covered for a
      missing `source` at the promotion level
      (`test_promote_refuses_when_a_pointer_does_not_resolve`); extend the
      same fail-closed behavior to cover an unresolvable canonical
      reference (missing/escaping lib) once Phase 1's rewrite step lands.
- [ ] If Phase 2 lands: a canonically-referenced `scripts/installer-
      engine.{sh,ps1}` runs correctly **in dev**, unmaterialized,
      dot-sourced (`source` on POSIX, `.` on PowerShell — not merely
      executed as a child process) by a plugin's own wrapper across
      folders into the canonical engine, on both platforms — demonstrating
      the "in-place test scripts in `dev`" requirement without requiring
      `preview_release.py`/materialization first.
- [ ] A materialized `main` build of the same plugin is fully self-contained
      — no cross-folder reference remains in the shipped payload, and no
      `editable = true` reaches a real shipped install — per
      `docs/install-contract.md`.
- [ ] No existing plugin's test suite or live installer regresses from the
      Phase 1 lib-conversion.

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
  - 10 new test functions in the final diff (9 in
    `test_materialize_main.py`, 1 in `test_promote_release.py` — corrected
    in review from an earlier miscounted 21, which summed tests added
    across incremental review-round commits rather than the final diff).
    Merged after CI green
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

### 2026-09-26 — Resolver decision: canonical-reference form supersedes the directory pointer kind
- Operator gave the mission directly: model the dev-time resolver on npm
  workspaces (relative-path reference in dev, real vendoring only at
  publish/promotion time), captured verbatim in Request above.
- Validated feasibility with a standalone `uv` prototype
  (`/tmp/uv-workspace-proto`, not committed) before writing anything into
  this repo: a consuming package's `[tool.uv.sources]` entry pointing at a
  sibling-of-root canonical package via a relative path, with `editable =
  true`. Confirmed: (1) `demo_lib.__file__` resolves directly to the
  canonical source, not a copy; (2) editing canonical after install (no
  reinstall) is picked up immediately on next import; (3) `editable = true`
  is required per-entry — a plain `path` reference installs a frozen copy
  even when the top-level package is itself `-e`; (4) `editable = true`
  forces an editable install of *that* dependency even when the top-level
  install is non-editable (`uv pip install .`, no `-e`) — confirmed this is
  exactly how this repo's real `install.sh` invokes it for real end-user
  installs, which means promotion **must** rewrite the reference (drop
  `editable`, repoint at a newly-local copy) rather than just copy content
  alongside an unchanged reference, or a real production install would
  try to resolve a path that doesn't exist on the end-user's machine.
- Wrote up the full design as a new "Design Decision" section (this
  README, above Participants) and revised Phase 1/2/3 and the Validation
  Plan to match: the canonical-reference form (relative `path` +
  `editable = true`, no local directory of any kind in `dev`) replaces
  both the directory/lib `VENDOR_POINTER.json` pointer kind (libs) and the
  need for any new "executable pointer kind" at all (installer engine —
  a plugin's wrapper just dot-sources the canonical engine directly via a
  relative path in `dev`; no new marker format needed). The file-pointer
  kind (docs) is unaffected — a Markdown doc has no package-manager
  equivalent of a live reference.
- Explicitly decided **not** to use a filesystem symlink (the more literal
  reading of "reference via relative path," and how a real npm/yarn
  workspace implements it under `node_modules/`) — it would require
  Windows Developer Mode/admin and `git config core.symlinks=true` to
  survive a checkout faithfully on this repo's own Windows CI/contributor
  matrix. The `uv` `path`+`editable` mechanism gets the identical live-
  reference behavior with zero filesystem symlinks and no git/OS
  configuration.
- Marked Phase 1's "define the dev-time resolver" and Phase 2's two scope/
  design items `[x]` (resolved by this decision); added a new,
  deliberate-not-silent Phase 1 item to retire the now-superseded
  directory-pointer loop and its now-unexercised tests from PR #3752 (kept
  the shared `_resolve_within()`/`_escapes_root()`/`_find_symlink()`
  helpers, since the file-pointer kind and the new reference-rewrite step
  both still need them).
- **Not yet done**: the actual conversion tool (no "convert a real copy to
  a reference" tool exists yet — confirmed while writing this), the new
  drift/consistency guard recognizing the reference form, the
  `materialize_main.py`/`promote_release.py` copy-then-rewrite extension,
  the `preview_release.py` equivalent, the directory-pointer retirement
  itself, and the actual bulk conversion. Next session should start with
  the conversion tool + the materialize/promote extension together (they
  share the rewrite logic), proven against exactly one real plugin/lib
  before converting the rest, per this effort's own established pattern
  of landing one bounded, testable slice at a time.
