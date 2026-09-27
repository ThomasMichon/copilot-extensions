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
- **Mechanism status (2026-09-27, corrected AGAIN):** the "npm-workspace-style
  live reference" decision below was **superseded** the same day it was
  written (2026-09-26), by a **different, unrelated effort**
  (`agent-cli-lazy-dispatch`) that independently hit the same
  dev-time-resolver problem, drew a different conclusion from an
  incomplete trial, and shipped a working alternative
  (`VENDOR_POINTER.json` `kind=src-passthrough`) before this effort's own
  Phase 1 execution caught up to it. Phase 1 then formally adopted
  `src-passthrough` and converted 7 real libs with it: `lazy-cli-dispatch`
  (PR #3790, from the unrelated `agent-cli-lazy-dispatch` effort that
  originated the mechanism, adopted by this effort's own Phase 1 rather
  than reconverted) plus 6 more converted directly by this effort's own
  Phase 1 execution (PRs #3810, #3904, #3917, #3929, #3971, #4004) — see
  the 2026-09-26 "Course correction" Journal entry for that decision's
  own rationale.
  **That decision is now ALSO superseded.** A second, deeper empirical
  test (see the 2026-09-27 "Second course correction" Journal entry)
  found `src-passthrough` has a real STRUCTURAL weakness the first
  analysis under-weighted: it breaks on ANY non-editable install (even
  from a live dev checkout — confirmed 7 times over as issue #3905),
  because the generated stub FILE itself gets copied into
  `site-packages`, severing it from the monorepo root. The `uv`-editable
  form does NOT have this weakness — verified directly this time (not
  taken on the original agent's word): a dependency's own
  `editable = true` forces a live reference back to its canonical source
  **independent of the top-level package's own editable-ness**, so even a
  fully non-editable top-level install leaves the shared-lib dependency
  correctly resolving to canonical, un-copied. **Phase 1 is reverting
  course to the `uv`-editable / canonical-reference form** — the
  originally-decided mechanism, now properly re-validated rather than
  assumed broken. This requires: building the still-missing tooling (a
  TOML-aware drift guard, a promotion-time reference-rewriter, a
  conversion tool), and re-converting the 7 already-shipped
  `src-passthrough` libs to the new form. The file-pointer kind (docs) is
  unaffected either way. See the Journal for the full analysis and
  decision record.

## Guiding Intent

`vendored-doc-pointers` (Done, pending archive) proved the DRY vendor-pointer
pattern for one narrow surface (a single mirrored Markdown file): one
canonical source, a thin marker on `dev`, full expansion only in the
materialized `main` release the install-contract already requires to be
self-contained. This effort generalizes that same underlying promise —
"impossible to drift, by construction," replacing "copy + a script that
catches drift after the fact" — to every other construct in this repo that
is duplicated byte-for-byte across plugins at authoring time: real
shared-lib copies (`plugins/*/libs/*`, `worktree-manager/libs/*`) and,
if `vendored-installer-engine` adopts it, the shared installer-engine
surface.

**The concrete mechanism differs by construct** (see the header's
"Mechanism status" note; the design has now been resolved THREE times for
the libs surface — the original decision below, then the 2026-09-26
"Course correction" (to `src-passthrough`), then the 2026-09-27 "Second
course correction" (back to this design) — see both Journal entries for
the full history):

- **Libs**: the `uv [tool.uv.sources]` `path`+`editable = true` live
  reference, as originally decided (see the Design Decision section
  immediately below, no longer marked superseded) — no local directory
  of any kind in `dev`. **Not** `VENDOR_POINTER.json`
  `kind=src-passthrough` (the effort's second, now-also-superseded
  resolution — see the "Second course correction" Journal entry for why:
  `src-passthrough` breaks on any non-editable install, even from a live
  dev checkout, because the generated stub FILE itself is what gets
  copied into `site-packages`; the `uv`-editable form doesn't have this
  weakness, since a dependency's own `editable = true` forces a live
  canonical reference independent of the top-level package's own
  editable-ness). 7 real libs were converted to `src-passthrough` before
  this reversal and now need re-converting to this form.
- **The shared installer engine** (if `vendored-installer-engine` adopts
  this pattern): still an open question for that effort to resolve on its
  own terms; the live-reference idea sketched in the Design Decision below
  remains a candidate, but Phase 2 hasn't reached this yet.
- **Docs** (`vendored-doc-pointers`, already shipped): unchanged — a
  physical stub file (`kind=file`) expanded at promotion, same as always.

## Design decision — npm-workspace-style live reference over a `VENDOR_POINTER.json` stub (resolved 2026-09-26, superseded 2026-09-26, RE-ADOPTED 2026-09-27)

> **Status: current and actionable again**, after two reversals — read
> this note in full before the section below, since the section itself
> still contains phrases from when it was marked historical.
>
> This design was the effort's *first* resolution of the dev-time-resolver
> question (2026-09-26). It was superseded the SAME DAY, before Phase 1
> execution ever implemented it, by a different effort's independently-
> shipped alternative (`VENDOR_POINTER.json` `kind=src-passthrough`) — see
> the "Course correction" Journal entry for that investigation, which
> included empirical proof (a live trial) that the mechanism described
> below *does* actually work when implemented correctly, even though
> `src-passthrough` was adopted anyway. 7 real libs were then converted to
> `src-passthrough`.
>
> **That second decision has ITSELF now been superseded** (2026-09-27) —
> see the "Second course correction" Journal entry: a deeper, properly-
> verified test found `src-passthrough` has a real structural weakness
> (breaks on any non-editable install, confirmed 7 times as issue #3905)
> that this `uv`-editable mechanism does not share.
>
> **Net result: this design IS the effort's current, active Plan again.**
> Every part of the section below — the mechanism, the promotion-time
> rewrite requirement, the symlink-rejection rationale — is accurate and
> actionable, not merely historical, DESPITE any "superseded"/"historical
> record only" phrasing that may still appear below this note (an
> artifact of the double-reversal that individual sentences further down
> were not all re-swept for) — this status block is the authoritative,
> up-to-date summary.

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
  entry whose `path` escapes the CONSUMING PROJECT's own root** (a
  plugin's `plugins/<plugin>/`, or `worktree-manager/` for that
  extra top-level consumer — not just "the plugin's own directory": a
  trigger scoped to plugins alone would skip `worktree-manager`'s own
  `../libs/<lib>` reference entirely, i.e. resolves outside that root, up
  toward the shared canonical root — reuse `_escapes_root()` from the
  existing containment work to detect this):
  1. Physically copy the canonical `libs/<lib>` directory's **complete
     tree** — `src/`, `README.md`, `tests/` (when the shipped payload is
     expected to carry one), AND `pyproject.toml` itself — into a
     freshly-created local `<consumer-root>/libs/<lib>/` (computed from
     the consuming project's own root — `plugins/<plugin>/` or
     `worktree-manager/`, not hardcoded to one layout). This must copy
     `pyproject.toml` explicitly, not merely "sync its version": the
     conversion step deletes the consumer's local `libs/<lib>/` directory
     entirely (no local directory of any kind remains in `dev`), so at
     promotion time there is no local `pyproject.toml` for a version-sync
     to update in the first place — the file has to be copied in from
     canonical before its version can be synced (or copied already
     carrying the right version, skipping a separate sync step).
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
  citing the same install-contract constraint this effort's canonical-
  reference design (see Design Decision below) must also respect — but its
  chosen *mechanism* for keeping the engine's vendored copies in sync is
  `tools/sync-installer-engine.py --check` (a drift detector: copies can go
  stale until CI catches it), not a live reference (drift structurally
  impossible on `dev`). This effort's Phase 2 scopes the canonical-
  reference idea to that same shared-engine surface only — never the
  per-plugin wrapper/config, which must keep varying by plugin — and
  evaluates whether the engine, once it lands, should be re-expressed that
  way instead of byte-vendored; coordinate
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

See the 2026-09-27 "Second course correction" Journal entry for the
resolution now in effect: the Design Decision above IS the effort's
current, active plan again (after a temporary detour to `src-passthrough`,
documented in the 2026-09-26 "Course correction" entry — that detour is
itself now superseded, not the design above).

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
      **Resolved three times; the third resolution is current (2026-09-27,
      reverting to the first).** Originally resolved via the Design
      Decision above: `uv`'s `[tool.uv.sources]` `path` + `editable =
      true`. **Superseded 2026-09-26** by `VENDOR_POINTER.json`
      `kind=src-passthrough` (see the "Course correction" Journal entry)
      — 7 real libs were converted to it (PRs #3810, #3904, #3917, #3929,
      #3971, #4004) before a **second course correction (2026-09-27)**
      reverted back to the `uv`-editable form: a deeper, properly-verified
      test (not merely taken on faith this time) found `src-passthrough`
      has a real structural weakness the first analysis under-weighted —
      it breaks on ANY non-editable install, even from a live dev
      checkout, confirmed 7 times over as issue #3905, because the
      generated stub FILE is what gets copied into `site-packages`,
      severing it from the monorepo root. `uv`-editable does not share
      this weakness: a dependency's own `editable = true` forces a live
      canonical reference independent of the top-level package's own
      editable-ness, verified directly (see the "Second course
      correction" Journal entry for the exact reproduction). Every item
      below that was marked superseded in the second resolution is now
      un-struck and active again; the src-passthrough-specific notes are
      kept as historical record of what was tried and why it's being
      undone.
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
      - [ ] **Re-convert the 7 libs already converted to `src-passthrough`**
            back to this form first, one at a time, using the exact same
            bounded-slice pattern already proven for the forward
            conversions (smallest blast radius first): `lazy-cli-dispatch`
            (1 consumer, `agent-worktrees`), `work-coalescing-singleton`
            (2: `agent-worktrees`, `worktree-manager`), `credential-relay`
            (4), `ssh-manager` (4), `single-instance-lease` (5),
            `config-migrate` (5), `plugin-activation` (8). Each
            re-conversion also closes out its own instance of issue #3905
            (the non-editable-install gap no longer applies once that lib
            is back on the `uv`-editable form).
            - [ ] **`plugin-activation`'s re-conversion needs an explicit
                  consumer-side follow-up, not just a pointer-directory
                  swap** (found in review): `plugins/customizing-copilot/
                  skills/installing-plugins/scripts/plugin-activation.py`'s
                  `_resolve_state_py()` (added during the forward
                  `src-passthrough` conversion, PR #4004) loads `state.py`
                  by filesystem path and reaches canonical ONLY by reading
                  a local `VENDOR_POINTER.json` marker's `source` field.
                  The `uv`-editable form deletes that local directory
                  entirely and leaves NO marker of any kind — this script
                  would resolve a nonexistent `<plugin>/libs/
                  plugin-activation/src/plugin_activation/state.py` and
                  break outright. Must be updated ALONGSIDE this specific
                  re-conversion (not treated as a trailing cleanup) to
                  resolve `state.py` via the `[tool.uv.sources]` entry in
                  the consumer's own `pyproject.toml` instead (parse the
                  TOML, find the `agent-plugin-activation` entry's `path`,
                  resolve `state.py` under it) — still never importing the
                  `plugin_activation` package itself, preserving the
                  original PyYAML-avoidance design.
      - [ ] Remaining real lib copies never yet converted at all
            (`agent-procutil`, `dropin-registry`, `plugin-resolve`,
            `session-liveness-probe`, `venue-copilot`, `zdd`) — convert
            directly to this form, skipping `src-passthrough` entirely.
            `session-liveness-probe`/`venue-copilot` have no top-level
            canonical `libs/<lib>/` yet (confirmed via `sync-vendored-libs
            .py --check`'s advisory drift note) — needs a canonical-
            promotion step before either is eligible for any conversion.
- [x] **Build the conversion tool**: a script performing the rewrite above
      across every real consumer of a given lib in one pass (a new tool,
      or a new mode on `sync-vendored-libs.py` — this repo currently has
      no "convert a real copy to a pointer/reference" tool FOR THIS FORM;
      `--pointerize` exists but only writes the `src-passthrough` form).
      Must refuse to convert a lib whose real copies have already drifted
      from canonical (reuse `_materialize_blocked()`'s existing drift
      check) — converting a drifted copy would silently discard whatever
      the copy had that canonical didn't. Must ALSO provide the reverse
      operation (re-convert an already-`src-passthrough` copy back to this
      form) for the 7-lib re-conversion above.
- [x] **Build the new drift/consistency guard for the reference form**:
      `check-vendored-libs-sync.py` has no concept of this reference form
      at all (it hashes whatever `src/` exists locally and compares copies
      against each other) — a `dev`-tree with no local copy shouldn't read
      as "missing"/"drifted." Extend it (or build a dedicated new guard)
      to recognize a `[tool.uv.sources]` entry whose `path` escapes the
      **consuming project's own root** (not just "the plugin's own
      directory" — Phase 1 explicitly includes `worktree-manager`
      consumers too, a top-level tree whose `../libs/<lib>` reference
      escapes `worktree-manager/` the same way a plugin's `../../libs
      /<lib>` escapes `plugins/<plugin>/`; a check scoped only to
      "plugin directory" would misreport a valid `worktree-manager`
      reference as missing/drifted) as a valid, intentional reference
      form, confirm `editable = true` is present (a forgotten `editable`
      would silently produce a frozen, non-live copy), and confirm the
      referenced canonical `libs/<lib>` actually exists.
- [x] **Extend `materialize_main.py`/`promote_release.py` for the
      reference-rewrite promotion step**: for every `[tool.uv.sources]`
      entry whose `path` escapes the **consuming project's own root**
      (same predicate as the drift guard above, not "the plugin's own
      directory" alone — a trigger condition scoped only to plugins would
      leave a `worktree-manager/pyproject.toml`'s `../libs/<lib>`
      reference untouched at promotion, shipping it with no materialized
      `worktree-manager/libs/<lib>` copy at all; reuse `_escapes_root()`
      from the existing containment work), copy
      canonical's **complete lib tree** — `src/`, `README.md`, `tests/`
      (when the shipped payload is expected to carry one), AND
      `pyproject.toml` itself — into a freshly-created local
      `<consumer-root>/libs/<lib>/`, so the materialized copy is a
      genuinely complete, byte-identical restoration of what a real
      vendored copy looks like today (the Validation Plan below requires
      a lossless round-trip; copying only `src/` + a version sync would
      silently drop `README.md`/`tests/` from every promoted payload,
      AND leave nothing to sync a version into in the first place — the
      conversion step deletes the consumer's local `libs/<lib>/`
      entirely, so no local `pyproject.toml` exists at promotion time
      until canonical's own is copied in). Compute the
      destination from the CONSUMING project's own root, not hardcoded to
      `plugins/<plugin>/`: Phase 1 explicitly includes `worktree-manager`
      consumers too (a top-level tree, not under `plugins/`), and a
      promotion step that only ever writes to `plugins/<plugin>/libs/<lib>`
      would rewrite a converted `worktree-manager/pyproject.toml`'s
      reference without ever materializing the dependency under
      `worktree-manager/libs/<lib>`, shipping a broken package. Reuse
      `sync-vendored-libs.py`'s existing `_consumer_dir()`/
      `_EXTRA_CONSUMER_DIRS` resolution (already built for `--pointerize`,
      PR #3810) to compute the correct destination for either layout —
      then surgically rewrite the `pyproject.toml` line to the local,
      non-editable form (`{ path = "libs/<lib>" }`) — restoring exactly
      today's real shipped form. A surgical regex rewrite (matching this
      repo's existing convention — see `_VERSION_RE.sub`) is sufficient,
      to preserve every plugin's hand-authored comments. Reuse
      `promote_release.py`'s existing fail-closed-on-`SKIP` behavior (PR
      #3752) for an unresolvable reference. This is the step that makes
      the `uv`-editable form safe in production (a shipped `main` release
      never carries a live reference to a path that won't exist on the
      end-user's machine) — it does NOT yet exist, and blocks any real
      lib from safely completing this form's conversion until built.
- [ ] **Retire the `src-passthrough` pointer kind** once all 7 already-
      converted libs are re-converted back to this form (deliberate,
      reviewed removal — not a silent deletion, per this repo's
      subtractive-change convention, and only once nothing uses it
      anymore): remove `--pointerize`'s src-passthrough writer, the
      generated-stub template, and its containment tests from
      `sync-vendored-libs.py`; remove the pointer-expansion loop
      `materialize_main.py`/`preview_release.py` added for it. Keep
      `_resolve_within()`/`_escapes_root()`/`_find_symlink()` themselves
      (the file-pointer kind still needs them, and the reference-rewrite
      step above reuses `_escapes_root()` directly). Note the removal's
      rationale in this effort's Journal (not just the commit message).
- [x] Update `tools/preview_release.py` ("preview-promo") to perform the
      same copy-then-rewrite operation into its scratch preview copy
      (never the real tree) for a plugin using the reference form — its
      current `_materialize_into_preview()` only handles the
      `src-passthrough` form (built for that mechanism by PR #3803/
      #3810) and has no concept of the `uv`-editable reference form at
      all yet.
- [ ] Confirm the live promotion pipeline (`promote_release.py` ->
      `materialize_main.py`) handles the converted real plugins correctly.
- [ ] Confirm every consuming plugin's own test suite
      (`tools/run-plugin-tests.py <plugin>`) passes with **no local
      `libs/<lib>` directory present at all** in the `dev` checkout —
      proving the canonical reference alone (via `[tool.uv.sources]`
      `path` + `editable = true`) is sufficient for `uv pip install -e .`
      and subsequent test/static-analysis resolution. Additionally
      confirm (this is the whole point of the second course correction) a
      NON-editable `uv pip install <plugin-dir>` still resolves the
      shared-lib dependency to canonical live (not copied) — the property
      `src-passthrough` lacked.

### Phase 2 — Canonical-reference form for the shared installer engine
> **Note (2026-09-27, updated):** libs' mechanism pivoted away to
> `src-passthrough` on 2026-09-26, then reverted back to `uv`-editable on
> 2026-09-27 (see both "course correction" Journal entries) — Phase 2's
> live-reference idea (this phase's own corollary of the SAME mechanism)
> is therefore valid again, though it still hasn't been formally executed
> or re-confirmed against the current libs design. Revisit and confirm
> before executing this phase, rather than assuming it's settled purely
> because libs' mechanism happens to match again.
- [x] **Scope correction (from review, still holds): this applies only to
      `vendored-installer-engine`'s shared engine files**
      (`scripts/installer-engine.{sh,ps1}`), never to a whole plugin's
      `install.sh`/`install.ps1`/`init.*` entrypoint — that entrypoint stays
      a per-service wrapper/config (launch command, capability flags,
      sibling installs) that must keep varying by plugin, per that effort's
      own design.
- [x] **No new pointer kind needed** — the live-reference idea (a plugin's
      `install.sh`/`install.ps1` directly `source`s/dot-sources the
      canonical `scripts/installer-engine.sh`/`.ps1` via a relative path
      in `dev`) matches libs' own current mechanism again as of the second
      course correction above. Left `[x]` for the underlying dot-source-
      preservation ANALYSIS (still holds regardless of which pointer
      mechanism is in play) — but this phase itself has NOT been formally
      executed or re-confirmed; do not treat this as "Phase 2 is done."
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
      unchanged) and the `uv`-editable canonical-reference form (libs,
      and the shared installer engine if Phase 2 lands — see both
      Course-correction Journal entries for the mechanism's full history;
      `src-passthrough` was a temporary detour, now being reverted, and
      must NOT be what this doc describes) — their lifecycle (a live
      relative-path reference in `dev`, -> `materialize_main.py`
      copy-and-rewrite at promotion -> shipped `main` content), which
      tool owns which invariant, and how a new plugin/lib/script opts in.
- [ ] Revisit whether any other currently-duplicated construct surfaced in
      Phase 0's audit ("and more") warrants conversion in this effort or a
      follow-on; file a tracked issue for anything deferred rather than
      dropping it silently.

## Validation Plan

> **Note (2026-09-27):** this section documents validation criteria that
> have flipped TWICE now (uv-editable -> src-passthrough -> uv-editable
> again — see the Journal's two course-correction entries). Items below
> reflect the CURRENT (uv-editable) criteria; where a src-passthrough-era
> criterion was substituted in, that's flagged and reverted, not silently
> replaced again, so a future reader can see the full history.

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
      (Briefly marked superseded during the `src-passthrough` era, since
      that form keeps a local pointer directory — reverted now that
      `uv`-editable is current again; a `src-passthrough` copy's own
      "local pointer directory present, `src/` replaced by a stub"
      criterion no longer applies once each lib is re-converted.)
- [ ] **NEW criterion, added by the second course correction**
      (confirmed for `lazy-cli-dispatch` specifically, PR #4245 -- not
      yet for every lib, since only that one is converted so far): a
      converted plugin's own test suite ALSO passes when the plugin
      itself (not just the dependency) is installed **non-editably**
      (`uv pip install <plugin-dir>`, no `-e`) from a live `dev` checkout
      — the shared-lib dependency must still resolve live to canonical
      (confirmed via a direct scratch-dir reproduction during the second
      course correction: `agent_procutil.__file__` resolved to canonical
      even with the top-level package installed non-editably, because
      `editable = true` on the dependency's own `[tool.uv.sources]` entry
      is independent of the top-level's install mode). This is the
      property `src-passthrough` structurally lacked (issue #3905); it is
      the primary reason for reverting to this mechanism, so it must be
      demonstrated directly, not merely inferred from the earlier
      editable-install prototype.
- [ ] Editing the canonical `libs/<lib>` source and re-running a
      converted plugin's tests **without reinstalling** picks up the edit
      — the "in-place test scripts in `dev`" / "call across folders"
      requirement, demonstrated against a real plugin (not just the
      isolated prototype).
- [x] `tools/preview_release.py` ("preview-promo") correctly performs the
      copy-then-rewrite for a plugin using the canonical-reference form
      when building a scratch local-install preview. **Done, PR #4245** --
      confirmed end-to-end against `lazy-cli-dispatch` (not mocked).
- [x] `materialize_main.py`'s directory-pointer path refuses a `source`
      that escapes `canonical_root` (the same containment guarantee the
      file-pointer path already has via `_resolve_within()`). **Done, PR
      #3752** — also extended to reject a symlink inside/as the canonical
      `src/` tree and a destination pointer path escaping `dest`.
      (Briefly described as "the permanent, active mechanism, not slated
      for retirement" during the `src-passthrough` era — reverted: this
      directory-pointer path IS slated for retirement again, once all 7
      libs are re-converted back to the `uv`-editable form and no
      consumer uses `src-passthrough` anymore. `_escapes_root()` itself
      is reused directly by the reference-rewrite containment check
      below and by the file-pointer kind, so it is never removed.)
- [x] The reference-rewrite promotion step's OWN containment check —
      **done, PR #4245**: `materialize_main.py`'s
      `materialize_uv_editable_ref_into()` refuses a `[tool.uv.sources]`
      `path` that escapes `canonical_root`, the same way the (soon-to-be-
      retired) directory-pointer path above already does (its own
      `_escapes_root()` copy, mirroring rather than importing across the
      hyphenated/non-hyphenated filename boundary).
- [ ] A promotion run against a deliberately malformed/unresolvable pointer
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

### 2026-09-26 — Course correction: `src-passthrough` formally adopted, superseding the resolver decision above

- **How this was caught**: after driving PR #3810 (this effort's own
  Phase 1 execution — converting `work-coalescing-singleton` — through 23
  automated review rounds and merging it) and going to update this
  README's Plan/Journal to reflect that merge, the merged PR turned out to
  use `VENDOR_POINTER.json` `kind=src-passthrough` (a real local stub
  directory) — the *opposite* of the "Resolver decision" entry directly
  above, which explicitly retires the directory/lib pointer kind in favor
  of a `uv [tool.uv.sources]` `path`+`editable` live reference with NO
  local directory at all. This effort's own README had never been updated
  to record the pivot; it was flagged to the operator as a genuine
  design-crossroads blocker rather than silently reconciled either way.
- **Root cause traced**: `src-passthrough` was invented by a *different*,
  unrelated effort — `agent-cli-lazy-dispatch` (PR #3790, commit
  `106bc125c`) — which independently hit the same "how does a lib under
  active dev-time development resolve without a real local copy" problem
  a bit later the same day, and drew a different conclusion: its own
  commit message states a "scratch trial converting agent-worktrees' own
  agent-procutil copy to a bare pointer and running its real `uv pip
  install -e .` failed outright -- `does not appear to be a Python
  project`." That trial converted the LOCAL pointer directory to a bare
  marker with no `src/`, while leaving `pyproject.toml`'s
  `[tool.uv.sources]` entry **unchanged** — still pointing at that
  now-empty local directory, never rewritten to reference canonical
  directly. That is a different (and incomplete) thing than this effort's
  actual "Resolver decision": rewrite the reference itself to point
  straight at canonical, with no local directory of any kind, not even an
  empty marker.
- **Empirically re-tested the ACTUAL documented mechanism** (not the
  incomplete variant `agent-cli-lazy-dispatch` tried) in an isolated
  scratch copy (`/tmp/uv-editable-trial`, not committed): copied a real
  consumer (`plugins/agent-mcp`) plus its 4 canonical libs, deleted the
  consumer's local `libs/` copies entirely, rewrote `pyproject.toml`'s
  `[tool.uv.sources]` entries to `{ path = "../../libs/<lib>", editable =
  true }` pointing directly at canonical, then ran the EXACT command
  `tools/run-plugin-tests.py` uses (`uv pip install --python <py> -e
  <spec>`). **Result: it works.** Install succeeded, `agent_procutil
  .__file__` resolved directly to the canonical path (no local copy
  anywhere), editing canonical was picked up on the next import with no
  reinstall, and 414 of agent-mcp's real tests passed (all 195 failures
  were an unrelated missing `pytest-asyncio` in the minimal scratch venv,
  confirmed by inspecting one failure's traceback — nothing to do with
  the reference mechanism). This confirms the original "Resolver decision"
  above was sound and would have worked; `agent-cli-lazy-dispatch`'s
  rejection was based on an incomplete trial, not a real flaw in the
  design.
- **Decision, presented to the operator with both findings** (uv-editable
  demonstrably works when implemented correctly; `src-passthrough` is
  fully built, 23-review-round-hardened, and already has 2 real
  `dev`-branch adopters): **formally adopt `src-passthrough` as this
  effort's actual Phase 1 mechanism**, rather than switch course to the
  uv-editable form despite the sunk cost of re-verifying it works.
  Rationale for the choice, not just the sunk cost: `src-passthrough`
  reuses the *already-existing* `VENDOR_POINTER.json` marker/materialize/
  self-install machinery as-is (one generated stub file per conversion),
  where uv-editable would require *net-new* tooling this repo doesn't have
  yet — a TOML-aware drift/consistency guard, a promotion-time reference-
  rewriter, and per-lib edits to every consuming `pyproject.toml` (not
  just one file). Operator confirmed: adopt `src-passthrough`.
- **What this changes in the Plan above**: the Design Decision section is
  kept verbatim as the historical record of the first (superseded)
  resolution — struck through nowhere, just flagged at its own heading —
  so a future reader can see exactly what was decided and why it changed,
  rather than have it silently vanish. Phase 1's checklist items that
  assumed the uv-editable form (the conversion tool, the TOML drift guard,
  the materialize/promote reference-rewrite, the directory-pointer
  retirement) are marked `[x]` with a `~~struck-through~~` superseded
  description and a plain-text note pointing at what's ALREADY built and
  used instead (`--pointerize`, the existing `--check`/`materialize_main
  .py`/`preview_release.py` src-passthrough support) — none of that
  tooling needs building; it already exists. Real conversion progress
  (`lazy-cli-dispatch`, `work-coalescing-singleton`) is now tracked
  directly under the "convert every real vendored lib" item as sub-
  checkboxes. Phase 2 (installer engine) got a note flagging that its own
  live-reference design was a libs-mechanism corollary never
  re-evaluated against this pivot — still an open question for that
  phase, not resolved here.
- **Not yet done**: the remaining ~11 real lib copies still need
  conversion to `src-passthrough` (one bounded PR at a time, per this
  effort's own established pattern); Phase 2's installer-engine mechanism
  question is still open; Phase 3's pattern doc
  (`docs/patterns/vendor-pointer.md`) still needs writing, and must now
  describe `src-passthrough`, not the uv-editable form.

### 2026-09-26 — Phase 1: converted `credential-relay` (PR #3904)

- Picked `credential-relay` as the next bounded slice: 4 consumers
  (`agent-containers`, `agent-mcp`, `agent-bridge`, `agent-codespaces`) —
  tied with `ssh-manager` for the smallest remaining blast radius.
  Confirmed byte-identical across all 4 copies beforehand via
  `sync-vendored-libs.py --check` (no local per-plugin diffs to lose).
  Converted all 4 atomically in one commit via `--pointerize` (a partial
  conversion breaks `--check`'s copy-agreement comparison, per PR #3810's
  own earlier finding).
- Validated: `--check` reports all 4 as DRY pointer copies (clean); full
  `run-plugin-tests.py --reinstall` for all 4 affected plugins.
  `agent-containers` showed 1 failure — confirmed via `git stash`/rerun to
  fail IDENTICALLY with or without this conversion (a pre-existing,
  already-tracked flake, issue #3570, unrelated). `agent-bridge` appeared
  to hang under `--reinstall` on the first attempt; re-tested with more
  patience and confirmed it was just a slow venv rebuild, not a real hang
  (passed cleanly, twice, once without `--reinstall` and once with).
- **Round-1 review found a real, new-to-this-effort concern**: a plain
  non-editable `uv pip install <plugin-dir>` run directly against a live
  `dev` checkout raises `ImportError` for a src-passthrough-vendored lib
  (the installed copy is disconnected from the monorepo root the stub's
  `_find_repo_root()` walk needs). Reproduced empirically. Also reproduced
  the IDENTICAL failure for `lazy_cli_dispatch` (already-merged, PR #3790)
  — confirming this is a pre-existing gap in the mechanism itself, not
  something this PR introduced, and not something a single-lib conversion
  PR should fix ad-hoc. Filed **issue #3905** to track the real fix
  (teach `install.sh`/`install.ps1` to detect/refuse-or-materialize an
  unmaterialized pointer, or document it as a permanent dev-branch-install
  limitation) across all 3 current adopters uniformly. Confirmed this
  risk is currently DORMANT (no CI workflow does a non-editable plugin
  install today; `run-plugin-tests.py` always uses `-e`) — real exposure
  is a contributor manually running `install.sh` against their own `dev`
  checkout, a plausible but not currently-exercised workflow.
- Marked `credential-relay` `[x]` in Phase 1's conversion sub-checklist;
  noted `session-liveness-probe`/`venue-copilot` are NOT eligible for this
  conversion yet (no top-level canonical `libs/<lib>/` exists for either
  — `--pointerize` requires canonical first).
- **Not yet done**: ~10 remaining real lib copies (`ssh-manager` next,
  tied for smallest blast radius); issue #3905's actual fix; Phase 2;
  Phase 3's pattern doc.

### 2026-09-27 — Phase 1: ssh-manager, config-migrate, single-instance-lease (PRs #3917, #3929, #3971)

- **`ssh-manager` (PR #3917)**: converted all 4 consumers. Found and
  fixed a second real, pre-existing bug: canonical `pyproject.toml`'s
  `name`/`build-system`/test-runner-constraint fields disagreed with
  every real copy (all 4 already agreed on `agent-ssh-manager`, a
  deliberate PyPI anti-squatting rename canonical never picked up) --
  `--pointerize`'s byte-for-byte copy surfaced this immediately as a real
  `uv pip install -e .` failure. Root cause: `--restore-canonical` only
  ever syncs `src/` and the version field, never these other fields --
  confirmed all 4 copies agreed with each other before fixing canonical
  to match. Also filed **issue #3915** for a second, unrelated
  pre-existing `agent-ssh` test flake found while validating.
- **`config-migrate` (PR #3929)**: converted all 5 consumers, including
  `agent-worktrees` itself -- the largest/most-critical consumer
  converted so far (full suite: 630+ passed, clean). Review re-surfaced
  the same non-editable-install gap (issue #3905) for these 5 new
  instances; extended that issue rather than re-litigating per-PR.
- **`single-instance-lease` (PR #3971)**: converted all 5 consumers
  (`agent-vault`, `agent-worktrees`, `agent-dispatch`, `agent-mcp`,
  `agent-bridge`). Clean review (zero findings, approval recommended).
  Merge was briefly blocked by an unrelated, already-tracked `dev`-wide
  `guards + lint` break (issue #3967, a `customizing-copilot` skill-doc
  false positive) -- confirmed pre-existing/unrelated via `git stash`
  before investigating a fix; another session/process fixed it upstream
  during this PR's review window, so a rebase-and-repush was all that was
  needed once confirmed.
- **Running total this session**: 4 more real libs converted
  (`credential-relay`, `ssh-manager`, `config-migrate`,
  `single-instance-lease`), on top of `lazy-cli-dispatch` and
  `work-coalescing-singleton` from earlier -- **6 of 13 total real libs
  now converted**. Two genuine pre-existing tool/canonical bugs found and
  fixed along the way (this entry's `ssh-manager` note, plus PR #3810's
  nested-`__pycache__` fix). Two pre-existing test flakes confirmed
  unrelated and tracked (issue #3570, already tracked; issue #3915,
  newly filed). Separately, an unrelated `dev`-wide `guards + lint` CI
  failure (issue #3967 -- a `check-marketplace-isolation.py` false
  positive, not a test flake) briefly blocked #3971's merge; resolved
  upstream by another session during that PR's review window.
- **Not yet done**: 5 real lib copies remain eligible for conversion
  (`agent-procutil` — 11 consumers, the largest; `dropin-registry` — 9;
  `plugin-activation`/`plugin-resolve` — 7 each, tied smallest remaining;
  `zdd` — 8); `session-liveness-probe`/`venue-copilot` still need a
  canonical-promotion step first; issue #3905's actual cross-adopter fix;
  Phase 2 (installer engine, still an open design question); Phase 3's
  pattern doc. Next session should continue with `plugin-activation` or
  `plugin-resolve` (tied smallest), one bounded PR at a time, per this
  effort's own established pattern.

### 2026-09-27 — Phase 1: plugin-activation (PR #4004)

- Converted all 8 real consumers (`customizing-copilot`, `agent-worktrees`,
  `agent-dispatch`, `agent-logger`, `agent-bridge`, `agent-machines`,
  `agent-codespaces`, `worktree-manager`). Found and fixed a THIRD real,
  pre-existing bug this effort has now surfaced: `customizing-copilot`'s
  `installing-plugins` skill script (`plugin-activation.py`) loaded
  `plugin_activation/state.py` by hardcoded file path, deliberately
  bypassing the package's own `__init__.py` to avoid a PyYAML dependency
  (`state.py` itself is stdlib-only, used standalone outside any plugin's
  installed venv) -- broke once the local copy became a pointer stub with
  no physical `state.py`. Fixed by teaching the loader to check for the
  local copy's own `VENDOR_POINTER.json` and resolve `state.py` through
  its `source` field when present, still never importing the package
  itself (preserving the original yaml-avoidance design). Verified via a
  repo-wide grep that no OTHER script has this same hardcoded-path-load
  pattern for any of this effort's converted libs.
- Review re-surfaced the same non-editable-install gap (issue #3905) for
  6 new consumer instances, several with specific `install.sh`/`init.sh`
  line citations from the reviewer -- extended that issue again rather
  than fixing ad-hoc; the accumulating line citations are useful grounding
  for whoever eventually picks up the real cross-adopter fix.
- **Running total, this whole session**: 5 more real libs converted
  (`credential-relay`, `ssh-manager`, `config-migrate`,
  `single-instance-lease`, `plugin-activation`), on top of
  `lazy-cli-dispatch` and `work-coalescing-singleton` from earlier --
  **7 of 13 total real libs now converted, just over half**. Three
  genuine pre-existing bugs found and fixed along the way (two canonical/
  tool bugs: PR #3810's nested-`__pycache__` fix and this session's
  `ssh-manager` canonical-drift fix; one consumer-side bug: this entry's
  `plugin-activation.py` hardcoded-path fix). Also resolved a genuine
  design-decision conflict discovered mid-session (this effort's README
  had documented a superseded `uv`-editable mechanism; formally adopted
  `src-passthrough` instead, per the "Course correction" entry above).
- **Not yet done**: 4 real lib copies remain eligible for conversion
  (`agent-procutil` — 11 consumers, the largest remaining; `dropin-
  registry` — 9; `plugin-resolve` — 7, now the smallest remaining; `zdd`
  — 8); `session-liveness-probe`/`venue-copilot` still need a canonical-
  promotion step first; issue #3905's actual cross-adopter fix (now
  spanning 7 adopters' worth of evidence); Phase 2 (installer engine,
  still an open design question, unrelated to libs' pivot); Phase 3's
  pattern doc (`docs/patterns/vendor-pointer.md`, still unwritten and
  must describe `src-passthrough`). Next session should continue with
  `plugin-resolve` (now smallest remaining at 7 consumers), one bounded
  PR at a time, per this effort's own established pattern -- the
  workflow is now well-proven: pick smallest remaining consumer count,
  confirm `--check` shows no drift, `--pointerize` every consumer
  atomically, validate with `run-plugin-tests.py` per affected plugin
  (watch for pre-existing flakes via `git stash`/rerun before assuming a
  failure is caused by the conversion), fix any real regressions found
  (usually a hardcoded direct-file-path load bypassing the package
  `__init__.py`, per this session's two tool/consumer-side bugs), journal,
  commit, open PR, address review (expect the same non-editable-install
  finding every time -- defer to #3905), merge.

### 2026-09-27 — Second course correction: reverting to `uv`-editable, superseding `src-passthrough`

- **Why this was revisited**: the operator had originally recommended the
  npm-workspace `node_modules`-swap idea (a different, independent agent
  turned that into the `uv`-editable Design Decision above); after 7 libs
  were converted to `src-passthrough` instead (this effort's own prior
  course correction), the operator asked for the `uv` pathway to be
  properly analyzed rather than left un-re-examined just because
  `src-passthrough` was already shipped -- a legitimate concern given the
  first "adopt src-passthrough" decision leaned heavily on sunk cost
  (tooling already built) without properly re-weighing `src-passthrough`'s
  own structural risk (issue #3905), which by this point had recurred on
  every single one of the 7 conversions.
- **Re-examined the exact claim the original `uv`-editable investigation
  asserted but never verified under the RIGHT conditions**: the
  Design Decision above already noted "`editable = true` forces an
  editable install of that dependency even when the top-level package is
  installed non-editable" -- but neither that original investigation NOR
  this effort's later validation trial (round 1 of the first course
  correction, which only tested `-e` on BOTH the top-level package and
  its dependencies) had actually verified this specific combination: a
  NON-editable top-level install with an `editable = true` dependency.
  Ran the precise reproduction this time: copied `agent-mcp` + 4 canonical
  libs to an isolated scratch dir, rewrote `pyproject.toml`'s
  `[tool.uv.sources]` to reference canonical directly with `editable =
  true`, then ran `uv pip install --python <py> plugins/agent-mcp` --
  deliberately WITHOUT `-e` this time, matching exactly what
  `install.sh`'s real invocation does for the top-level package.
  **Result: `agent_mcp.__file__` resolved into `site-packages` (a real,
  disconnected copy, as expected for a non-editable install), but
  `agent_procutil.__file__` (the dependency, `editable = true`) still
  resolved DIRECTLY to the canonical source path, live, un-copied.**
  `uv`'s per-dependency `editable` flag is independent of the top-level
  package's own install mode -- confirmed, not assumed.
- **This is the property `src-passthrough` structurally lacks**: a
  non-editable install of a `src-passthrough`-vendored plugin copies the
  generated STUB FILE itself into `site-packages`, severing its
  `__file__`-based repo-root walk from the monorepo entirely (confirmed
  independently across all 7 conversions as issue #3905). `uv`-editable's
  dependency reference never gets copied at all regardless of the
  top-level's install mode, so it doesn't share this failure mode for a
  live dev-checkout install.
- **The scoped limit of this advantage, stated honestly**: it only helps
  the "install directly against a live monorepo checkout" scenario (a
  contributor running `install.sh` locally). It does NOT help production:
  a real end-user install happens from a materialized `main` release with
  no monorepo `libs/` sibling at all, where an un-rewritten `editable =
  true` reference would point at a path that simply doesn't exist --
  which is exactly why the ORIGINAL Design Decision already mandated a
  promotion-time rewrite step (drop `editable`, repoint locally) before
  ANY of this ships. That rewrite step, the TOML drift/consistency guard,
  and the conversion tool were never built in the earlier attempt (Phase 1
  got redirected to `src-passthrough` before reaching them) -- they still
  don't exist and must be built before this mechanism is production-ready
  for even one real lib.
- **Presented the full tradeoff to the operator** (structural robustness
  advantage vs. real rework cost: reverting/re-converting the 7 already-
  shipped libs, building the still-missing promotion rewriter/drift-guard/
  conversion-tool, editing every consuming `pyproject.toml` per lib
  instead of one stub file) and asked how to proceed. **Operator decided:
  switch course to `uv`-editable, accept the rework cost.**
- **What this changes**: the Design Decision section is restored to
  "current" (no longer marked superseded) with a new status note
  explaining the double-reversal; Phase 1's Plan items are un-struck back
  to active, with the src-passthrough-specific sub-items preserved as
  historical notes (what was tried, why it's being undone) rather than
  deleted; a new Plan sub-item tracks re-converting the 7 already-shipped
  libs back to this form, smallest-blast-radius first, same as the
  original forward-conversion order; the Validation Plan's superseded
  criteria are un-struck, plus a NEW criterion added (the non-editable
  top-level install property that motivated this reversal in the first
  place) so it's demonstrated directly per conversion, not just inferred.
- **Not yet done**: none of the actually-new tooling exists yet --
  the conversion tool (bidirectional: convert AND re-convert), the TOML
  drift/consistency guard, the promotion-time reference-rewriter in
  `materialize_main.py`/`promote_release.py`, and the `preview_release.py`
  equivalent. Next session should build these first, proven against
  exactly one already-`src-passthrough` lib (smallest: `lazy-cli-dispatch`,
  1 consumer) before doing the bulk re-conversion of the other 6 -- per
  this effort's own established "prove on one before bulk" pattern.

### 2026-09-27 — Phase 1: uv-editable tooling built and proved on lazy-cli-dispatch (PR #4245)

- **Built the three previously-missing pieces of tooling** the second
  course correction left open: `tools/sync-vendored-libs.py --uv-editable
  CONSUMER LIB` (bidirectional conversion tool -- real copy or
  `src-passthrough` pointer copy, either direction, into the `uv`-editable
  form; refuses to discard a drifted real copy), a new drift/consistency
  guard folded into `--check` (recognizes an escaping `[tool.uv.sources]`
  entry, verifies `editable = true` and canonical existence, across both
  `plugins/<plugin>` and `worktree-manager` consumers), and a promotion-
  time reference-rewriter in both `materialize_main.py` and
  `preview_release.py` (copies canonical's complete lib tree -- `src/`,
  `README.md`, `tests/`, `pyproject.toml` -- into the consumer's own
  `libs/<lib>/` and rewrites the entry back to the plain local form).
- **`sync-vendored-libs.py` crossed the repo's 1000-line module-size cap**
  once the new tooling landed -- split two purely-additive pieces into
  their own modules (`tools/uv_editable_ref.py`, a normal importable
  module with no logic risk from the extraction; `tools/
  passthrough_pointer_template.py`, a plain string-constant move with zero
  logic change) rather than touching the pre-existing `src-passthrough`
  writer itself, keeping the componentization scoped to only the code this
  session actually added.
- **Proved the whole pipeline on `lazy-cli-dispatch`** (smallest, 1
  consumer) exactly as planned: converted `plugins/agent-worktrees/libs/
  lazy-cli-dispatch` from `src-passthrough` to `uv`-editable -- no local
  directory remains. Validated end-to-end, not just unit-tested:
  `sync-vendored-libs.py --check`/`check-vendored-libs-sync.py`/`check-
  install-contract.py` all pass; `run-plugin-tests.py agent-worktrees
  --reinstall`'s full suite (all 10 sub-suites) passes with no local copy
  present; a **non-editable** `uv pip install <plugin-dir>` in a fresh
  venv (no `-e`) still resolved `agent-lazy-cli-dispatch` live from
  canonical -- the exact property this effort's second course correction
  was staked on, confirmed for a real lib, not just the earlier isolated
  `agent-mcp` reproduction; `materialize_main.py` expands the reference
  into a byte-for-byte lossless copy at promotion time, confirmed against
  the real repo tree.
- **Filed as PR #4245**, targeting `dev`. Journaled here per this effort's
  own "even for pure documentation" convention, but this entry is itself
  part of that same PR (not a separate follow-up commit).
- **Still open for the next session**: re-convert the remaining 6
  `src-passthrough` libs (`work-coalescing-singleton` next by blast-radius,
  then the tied pairs), convert the 4 never-yet-`src-passthrough` real
  libs directly, retire the `src-passthrough` pointer kind once all 7 are
  re-converted, then Phase 2 (installer engine) and Phase 3 (pattern doc)
  -- see the Plan section above for the full ordering, unchanged by this
  session.
