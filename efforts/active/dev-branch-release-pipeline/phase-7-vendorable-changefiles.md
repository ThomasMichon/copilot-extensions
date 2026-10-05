# Phase 7 — Vendorable-aware changefiles, auto-bump propagation, and placeholder versions on `dev`

Sibling design doc for `dev-branch-release-pipeline`'s Phase 7, extracted per
this repo's own `efforts/README.md:109-111` convention (substantial phase
designs/inventories belong in a sibling document, not the shared coordination
README). Linked from the effort README's Plan and Validation Plan.

**Request (verbatim, 2026-10-02):** "For changefiles, we should do some
hygiene. On both pre-push and CI, we want to check the diff against fresh
dev, and ensure that the PR adds new changefiles only for the versionable
things being impacted. There will need to be some cross-checking of
vendorables, but perhaps we can handle that by making vendorables have their
own "manifests" with need versioning, and then the aggregator will auto-bump
any downstream plugin whose upstream venforable got bumped, too. Next, we
need to remove the actual version values from al manifests in dev, or leave
placeholders (0.0.0). We should also shut off any hook- or CI-based
enforcement that would make agents keep editing those values. We only want
changefiles and auto-bumps."

## Context

- `check-version-bump.py` is **already** retired from enforcement (no
  pre-push/CI wiring); `check-changefile-presence.py` is the live guard, but
  today it only catches a touched plugin/vendorable **missing** a
  changefile entry (one direction) — it was never taught the reverse
  (an added changefile naming something the diff never touched).
- `check-version-consistency.py` is **still live** in both `tools/hooks/
  pre-push` and `ci.yml` — this is the concrete hook/CI enforcement that
  requires every version-bearing file to already agree, which in practice
  is the thing that pressures an agent/human to hand-edit version fields
  back into sync. This is the enforcement the Request's "shut off" asks to
  remove.
- A shared lib ("vendorable") has **no independent version/manifest/
  changefile identity today** — `CONTRIBUTING.md` currently documents that a
  vendorable change requires a changefile entry naming **every consuming
  plugin individually** (the fan-out rule `check-version-bump.py`'s
  `_vendored_consumers()` computes). `accumulate_bumps.py`'s ordinary
  `compute()` path still requires each consumer to be named explicitly;
  only its separate `--from-diff` mode does any fan-out, and that fan-out
  computes the SET of consumers to charge — it does not give the vendorable
  itself a bumped version other consumers derive from.
- `promote_release.py` does not regenerate `.github/plugin/marketplace.json`
  from scratch — it relies on `accumulate_bumps.py`'s incremental
  `_write_marketplace_entry()` patch. Phase 6's 2026-09-24 Journal entry
  already flagged `dev`'s `marketplace.json` as needing to become a
  deliberately non-functional placeholder, generated fresh at promotion
  time — **never implemented**, and this phase's placeholder-version work
  is the natural place to finish it rather than opening a third effort for
  the same category of change.

## Plan (confirmed — Phase 7 is approved to start)

- [ ] Make `check-changefile-presence.py` (and the pre-push hook invoking
      it) check the diff **both directions** against a freshly-fetched
      `origin/dev` tip (never a possibly-stale local tracking ref): every
      versionable thing the diff touches has a changefile naming it
      (today's behavior, kept), **and** every changefile the diff adds
      names only versionable things the SAME diff actually touches (new —
      rejects an added changefile that over-claims, e.g. naming an
      untouched plugin or stale vendorable).
- [ ] Give each vendorable its own version identity by treating the version
      ALREADY declared in its canonical `libs/<lib>/pyproject.toml` as
      authoritative — no new manifest file type. `tools/changefile.py add`
      accepts the vendorable name directly (e.g. `--plugin ssh-manager`) as
      the versioned thing a PR touches, instead of requiring every one of
      its consumers to be named individually. Not every vendorable
      `check-version-bump.py` already recognizes has a `pyproject.toml` —
      `installer-engine` and `peer-launch` (`tools/check-version-bump.py:
      222-237`) are non-Python vendorables with no such file at all. Define
      an identity/exception for this class (e.g. a small marker or a
      fallback to a version recorded elsewhere already) so reverse
      changefile-coverage has a valid target for them too, rather than
      silently excluding them from the new bidirectional guard.
- [ ] Teach the aggregator (`accumulate_bumps.py`) that bumping a
      vendorable's own manifest version auto-bumps every one of its
      registered consumers too (real vendored copies and `uv`-editable
      pointer consumers alike, per `check-vendored-libs-sync.py`'s existing
      consumer map **combined with** `check-version-bump.py`'s
      `_vendored_consumers()` map — the latter is the only place
      `installer-engine`/`peer-launch` consumers are registered at all, so
      the aggregator must use the union of both, not `check-vendored-libs-
      sync.py`'s map alone), AND keeps every one of the vendorable's own
      real vendored copies (e.g.
      `plugins/agent-worktrees/libs/zdd/pyproject.toml`) at the same
      version as its canonical manifest — preserving
      `lib_bumps_from_diff()`'s existing real-copy-plus-canonical rewrite
      (`tools/accumulate_bumps.py:480-520`), which `check-vendored-libs-sync.py`
      already requires to agree. A consumer no longer needs its own
      separate changefile entry for a vendorable-only change.
- [ ] Define and implement one explicit coalescing rule for a consumer
      reached by more than one bump request in the same release window —
      its own explicit changefile entry, and/or transitive propagation from
      one or more changed vendorables it consumes: gather every request for
      that consumer first, apply only the single highest-precedence bump
      type among them (reusing the existing `major > minor > patch > dev`
      order), exactly once. Never double-increment a consumer reached
      through two paths in the same run.
- [ ] Extend `promote_release.py`'s `_seed_versions_from_main()` to also
      seed every vendorable's version from its last-shipped value on `main`
      before applying its next changefile — today it seeds only plugins and
      standalone consumers (`tools/promote_release.py:135-191`). Without
      this, an unchanged vendorable would regress to the `0.0.0` placeholder
      every promotion and each bump would restart from scratch instead of
      continuing the vendorable's real version history.
- [ ] Replace real version values across `dev`'s per-plugin manifests
      (`plugin.json`, `pyproject.toml`, checked-in `__version__`/
      `_FALLBACK_VERSION` source assignments, AND every other hook-owned
      version literal — e.g. `plugins/ai-attribution/scripts/
      emit-policy.sh`/`emit-policy.ps1`, which `tests/test_emit_policy.py`
      requires to match `plugin.json`) with the literal string `"0.0.0"`.
      Inventory every hook/script that embeds a version literal anywhere in
      the repo before implementation starts, not just the surfaces
      `accumulate_bumps.py` already knows about today — a missed one leaves
      a stale marker that blocks every subsequent promotion via its own
      existing guard test.
- [ ] Extend `accumulate_bumps.py`'s generation path to write the real
      computed version into every one of those inventoried nonstandard
      surfaces too, not just the standard ones `apply()` already handles
      today (JSON/TOML fields, Python fallbacks, marketplace entries,
      instruction-projection owners). Without this, a hook-owned literal
      like `emit-policy.sh`/`emit-policy.ps1` stays at `"0.0.0"` after
      generation while `plugin.json` moves on, and that plugin's own
      existing guard test (`test_emit_policy.py`) fails every subsequent
      promotion — the retained post-generation structural invariant (above)
      only catches this if generation is actually taught to write the
      value first.
- [ ] `.github/plugin/marketplace.json` gets a genuinely **non-functional**
      placeholder on the CLI-facing install path, not just `"0.0.0"`
      version fields inside an otherwise valid catalog — a structurally
      valid marketplace with every version at `"0.0.0"` is still something
      a live Copilot CLI could point at as an install source, which does
      not satisfy Phase 6's own requirement (see the effort README's
      Journal) for a deliberately non-installable `dev` catalog. This must
      NOT simply delete the file's content, for two concrete reasons found
      while drafting this item:
      - **Catalog-only fields have no other source today.** At least one
        entry (`agent-pull-requests`'s behaviorful `defaultEnabled: false`
        in `.github/plugin/marketplace.json`) is not derivable from that
        plugin's own `plugin.json`, and some catalog descriptions already
        differ from their `plugin.json` counterpart. Define a canonical
        source for every such catalog-only field (migrating it out of the
        soon-to-be-sentinel file if needed) before the valid catalog is
        replaced, and confirm the generated `main` snapshot preserves it
        exactly.
      - **Existing dev-side consumers require a valid, parseable catalog.**
        `check-docs-consistency.py` and `check-runbook-references.py` both
        load `marketplace.json` in required pre-push/CI today; a sentinel
        with no `plugins` array produces an empty roster or an outright
        JSON-parse crash, breaking every push/PR on `dev`, not just live
        installs. The non-functional placeholder must be unusable
        specifically as a **live install source** (e.g. missing the schema
        fields the Copilot CLI's installer specifically requires) while
        still giving every dev-side catalog consumer (docs-consistency,
        runbook-references, and any other roster reader) a valid
        authoritative plugin roster to read.
      Make `promote_release.py` **generate** the real `marketplace.json`
      fresh at promotion time from every plugin's own manifest plus the
      preserved catalog-only fields, rather than incrementally patching an
      existing valid file — this finally closes Phase 6's still-open "`dev`
      marketplace placeholder" item instead of leaving it a separate loose
      end.
- [ ] Remove `check-version-consistency.py`'s pre-push + CI wiring against
      `dev` entirely — `dev` carries only `"0.0.0"` placeholders, so
      cross-file version agreement is meaningless there, closing the
      hook/CI pressure to hand-edit version fields the Request asks to
      remove. Retain and refactor the checker's structural validations
      (rejecting a non-literal/invalid/duplicate fallback assignment,
      detecting a missing version field or catalog entry, a real mismatch
      between surfaces) as a **post-generation promotion invariant** that
      runs against the materialized `main` snapshot before promotion
      completes — `accumulate_bumps.py`'s `apply()` does not currently
      guarantee every surface by construction (e.g. it ignores the return
      values of `_write_source_fallbacks()`/
      `_write_instruction_projection_owners()`), so this closes the actual
      gap rather than only inspecting write-call return values. `main`
      still needs a real, internally-consistent version even though `dev`
      no longer does. Two more CI call sites beyond `tools/hooks/pre-push`
      and `ci.yml`'s own direct invocation also need updating in the same
      pass: `.github/workflows/module-size-baseline-widen-verify.yml:30-31`
      and `.github/workflows/validate-and-promote.yml:328-331` both invoke
      `check-version-consistency.py` today — the former must stop calling a
      retired file, the latter must call the new post-generation invariant
      instead.
- [ ] Update `tools/preview_release.py` for the same seeding/propagation
      model as promotion: its `build()` currently reads the checked-out
      plugin's own version directly and filters pending changefiles to
      entries naming that plugin by name (`preview_release.py:151-158`), so
      once per-plugin manifests carry only the `"0.0.0"` placeholder, an
      unmodified preview would report a bogus `0.0.x` hypothetical version
      and never show a consumer's bump when it was reached only through a
      vendorable's propagated changefile. Preview must seed from the same
      last-shipped-on-`main` source and apply the same coalesced
      consumer/vendorable bump resolution promotion does, not recompute its
      own narrower approximation.
- [ ] Route **ordinary numbered `install`/`update` flows** (never the
      existing mutable `dev`-slot editable-install path, which must stay
      exactly as it is today — an editable install straight from the
      worktree so source edits take effect without rebuilding, per
      `docs/patterns/mutable-dev-slot.md:44-54,170-176` and the pilot
      implementation at
      `plugins/agent-codespaces/scripts/install.sh:635-715`) through a
      generated preview build instead of installing the raw repo-tree
      `plugin.json` version directly. A numbered install always
      materializes a real hypothetical version via `preview_release.py`
      (already being extended above for the same seeding/propagation model
      as promotion) first, then installs that generated slot.
      `agent-bridge`'s own install script derives its immutable slot from
      `pyproject.toml` and explicitly **rejects** a `plugin.json` version
      of `"0.0.0"` as a downgrade from any already-shipped install
      (`scripts/install.sh:242-250,1901-1932`), so installing straight off
      `dev`'s placeholder values would break that numbered-install path
      outright; routing it through a generated preview build sidesteps
      that entirely since the thing actually installed never carries the
      `"0.0.0"` placeholder itself. `preview_release.py` today only writes
      its computed hypothetical version into `PREVIEW.json`'s own
      metadata — the `plugin.json`/`pyproject.toml` copies it materializes
      for actual install are left unchanged
      (`tools/preview_release.py:151-172`), so this sidestep only works
      once the preview build is also taught to write the computed version
      into every one of those copied, generated files, not just report it.
      It must also persist the generated preview snapshot durably (not a
      scratch tree that may be cleaned up) and write it as `source.path` —
      the operative payload location runtime consumers actually
      dereference and execute from — while recording the originating
      checkout's own commit/branch/dirty-state **separately**, in the
      install contract's dedicated `commit`/`branch`/`dirty` fields
      (`docs/install-contract.md:1718-1723`), never by repointing
      `source.path` itself at the raw checkout. Durable snapshots need an
      owner and a reclamation rule: the existing runtime GC
      (`libs/versioned-runtime/versioned_runtime.py:924-972`) only reclaims
      `versions/*` slots, not this new persisted-snapshot tree, so repeated
      dirty numbered installs would otherwise leak one payload tree per
      build forever. Define a retention policy (deferred to
      implementation) that preserves every snapshot still referenced by a
      current/fallback/live slot and safely reclaims only unreferenced
      ones — never a time- or count-based heuristic that could evict a
      still-referenced snapshot.
- [ ] Give the numbered-install preview route a concrete enforcement seam,
      not just a stated intent — today's documented local-testing flow
      invokes `plugins/*/scripts/install.*` directly
      (`CONTRIBUTING.md:1012-1017`), and those installers infer their own
      local source from their own script path
      (`docs/install-contract.md:1729-1741`), so simply *having*
      `preview_release.py` changes nothing unless the documented commands
      themselves are updated to go through it. Either make
      `install.sh`/`install.ps1` detect a placeholder (`"0.0.0"`) source
      version and redirect to/require a preview build first, or make the
      preview-generated directory the one new documented numbered-install
      entry point and update `CONTRIBUTING.md`'s local-testing section
      accordingly — inventory every existing entry point this needs to
      change, not just the general intent to "route through preview." The
      mutable `dev`-slot editable-install command/entry point is unaffected
      and keeps installing straight from the worktree exactly as today.
- [ ] **Decided:** every numbered runtime slot is immutable by
      repo-wide invariant (`docs/patterns/README.md:120-130`,
      `visions/plugin-services/README.md:159-169`) — only `versions/dev`
      may ever be rebuilt in place (`docs/patterns/mutable-dev-slot.md:
      44-54`). A preview's hypothetical version is computed purely from the
      pending changefiles' bump *type*, not content, so two different dirty
      checkouts (or the same checkout before/after a further uncommitted
      edit) can compute the identical version while holding different
      bytes — installing both as the same numbered slot would silently
      overwrite an existing immutable slot and break rollback/concurrent-
      process safety, the exact thing numbered immutability exists to
      prevent. The preview route must therefore mint a **content-distinct**
      identity for every numbered-install preview build rather than reusing
      the bare computed version as-is; a rebuild against truly unchanged
      content (identical hash) may still reuse its own slot, anything else
      always mints a new one. Slot separation by itself is **not**
      sufficient: `agent_worktrees.reconcile` decides whether to deploy by
      comparing *reported version strings* for equality
      (`reconcile.py:1990-2004`'s `_versions_equal`), not by slot id, so a
      distinct slot carrying the SAME reported base version as a later real
      promoted build would still be skipped as "already equal" even though
      its bytes differ. The exact identity scheme is an implementation-time
      decision, not a plan-time one, but it must satisfy both of these
      together: the *reported version string itself* (not just the backing
      slot) must differ whenever content differs, so reconciliation's own
      equality check is never fooled; and it must stay precedence-safe
      against the existing comparators —
      `agent_worktrees.reconcile._version_lt`'s PEP 440 ordering sorts
      `1.2.3.dev1+abc` *after* bare `1.2.3.dev1`, which can make a later
      real promoted build look like a downgrade and get skipped, and the
      canonical runtime sorter (`libs/versioned-runtime/versioned_runtime.py:
      135-149`) only recognizes `X.Y.Z[-devN]`, dropping any suffixed form
      into its unsupported fallback bucket. Implementation must either pick
      a reported-version scheme proven both content-distinguishing and
      precedence-neutral against every one of those comparators, or extend
      `_versions_equal`/the runtime sorter alongside it — "keep the slot id
      distinct but leave the version string as-is" is explicitly ruled out,
      not an available option — and the Validation Plan item below requires
      proving the real promoted build supersedes an installed preview
      across reconciliation, downgrade guards, and runtime fallback/GC
      before this is considered done.
- [ ] Extend the placeholder-conversion inventory and migration to cover
      every vendorable's own `libs/<lib>/pyproject.toml` (canonical and
      every real copy), not just per-plugin manifests and hook-owned
      literals — the "every version field reads `0.0.0`" decision applies
      there too, and vendorable seeding from `main` (above) already assumes
      `dev` itself retains no real version to fall back on.
- [ ] Update `CONTRIBUTING.md`'s "Release & Versioning" section to describe
      the new model end to end: vendorable manifests, bidirectional
      changefile correctness against fresh `dev`, placeholder versions, and
      that only changefiles + the aggregator's auto-bump move a version
      number now — no hook/CI path should ever again describe hand-editing
      one.

## Design points — resolved

1. **Placeholder exact form:** literal `"0.0.0"` in every version field
   (not omitted/null) — simplest, keeps every field present and parseable.
2. **Vendorable manifest shape:** reuse the version already declared in
   `libs/<lib>/pyproject.toml` as authoritative — no new manifest file type.
3. **`check-version-consistency.py` fate:** its `dev`-side pre-push/CI
   wiring is removed entirely; its structural validations continue as a
   post-generation promotion invariant against the `main` snapshot (see the
   Plan item above) rather than surviving as an unchanged, separately-run
   file.
4. **Local install under placeholder versions:** scoped to ordinary
   numbered `install`/`update` flows only — route those through a generated
   preview build (`preview_release.py`) rather than giving a local `dev`
   checkout its own distinct non-release version identity, so
   `agent-bridge`'s downgrade rejection of `"0.0.0"` never comes into play.
   The existing mutable `dev`-slot editable-install path is explicitly
   **excluded** and keeps installing straight from the worktree exactly as
   today. Every numbered runtime slot stays immutable: a numbered-install
   preview build mints a content-distinct identity, so two builds with
   different content never collide on one slot even if their computed base
   version matches — only a byte-identical rebuild may reuse its own slot.
   The exact identity scheme is deferred to implementation (see the Plan
   item above and the precedence-safety requirement it carries).

## Validation Plan

- [ ] Bidirectional changefile-presence correctness: a PR touching a
      plugin/vendorable with no changefile still fails (existing behavior);
      a PR whose changefile names a plugin/vendorable the SAME diff never
      touched also fails (new behavior).
- [ ] The diff base is always freshly-fetched `origin/dev`, not a stale
      local tracking ref — simulate a local ref that lags behind the real
      `dev` tip and confirm the guard still diffs against the real tip.
- [ ] A changefile naming only a vendorable (no explicit per-consumer
      entries) satisfies the presence guard for every one of that
      vendorable's consumers — real vendored copies and `uv`-editable
      pointer consumers alike.
- [ ] `accumulate_bumps.py` auto-bumps every registered consumer of a
      vendorable whose own changefile bumped it, with no consumer-specific
      changefile entry required, AND keeps every one of that vendorable's
      own real vendored copies at the same version as its canonical
      manifest (not just the consumer plugins).
- [ ] A consumer reached by two bump paths in the same release window (its
      own explicit changefile entry plus transitive propagation from a
      changed vendorable; or propagation from two different changed
      vendorables it consumes) is bumped exactly once, at the single
      highest-precedence requested level — never double-incremented and
      never resolved to an arbitrary level.
- [ ] A non-Python vendorable with no `pyproject.toml` (`installer-engine`,
      `peer-launch`) has a working changefile target, participates in
      reverse (over-coverage) checking the same as a `pyproject.toml`-backed
      vendorable, AND is actually auto-bumped by the aggregator (not merely
      accepted as a valid changefile target).
- [ ] `module-size-baseline-widen-verify.yml` and `validate-and-promote.yml`
      both still pass after the `check-version-consistency.py` migration —
      the former no longer calls a retired file, the latter calls the new
      post-generation invariant at the right point in the promotion flow.
- [ ] `tools/preview_release.py` reports the real hypothetical version for
      a plugin reached only through a vendorable's propagated changefile
      (not just one with its own direct entry), and never reports a bogus
      `0.0.x` preview derived from the `"0.0.0"` placeholder.
- [ ] The generated preview tree's own `plugin.json`/`pyproject.toml`
      copies carry the computed hypothetical version, not the `"0.0.0"`
      placeholder `PREVIEW.json` alone would report it as — inspect the
      materialized files directly, not just the preview's reported summary.
- [ ] A numbered install/update from a `dev` checkout (never promoted)
      succeeds, both fresh and as a repeat install over an existing
      real-versioned release — confirm the install path actually goes
      through `preview_release.py`'s generated slot rather than
      `plugin.json` directly, so `agent-bridge`'s install script's downgrade
      rejection of `"0.0.0"` never fires.
- [ ] The mutable `dev`-slot editable-install path is unaffected by any of
      the above: it still installs straight from the worktree with live
      source edits taking effect without a rebuild, exactly as it does
      today.
- [ ] Running the documented numbered-install command as written (not a
      preview-aware variant) on a `dev` checkout either transparently
      routes through the preview build or is explicitly rejected/redirected
      — it never silently installs the raw `"0.0.0"` placeholder.
- [ ] After a preview-routed numbered install, the install contract's
      `source.path` (and `payload-dir`, where present) resolve to the
      **durable, persisted generated preview snapshot** — never a
      scratch/temp directory that may be cleaned up, and never back at the
      raw originating checkout. `source.path` is the operative payload
      location runtime consumers actually dereference and execute from
      (e.g. `agent_bridge/loop_governance.py:58-64`,
      `agent-bridge/scripts/install.sh:1307-1319`); pointing it at the raw
      checkout would let reconciliation/bootstrap execute the unrewritten
      `"0.0.0"` payload and silently bypass the preview. Origin
      traceability (which checkout this build came from) lives in the
      install contract's own dedicated `commit`/`branch`/`dirty` fields
      (`docs/install-contract.md:1718-1723`), not in `source.path` — those
      fields alone carry the originating-checkout identity; `source.path`
      stays on the persisted preview snapshot throughout. Today's
      installers derive `commit`/`branch`/`dirty` by running `git -C
      "$plugin_path/.."` against their own install location, which only
      works because `plugin_path` IS the source checkout today — once
      `source.path` is the generated snapshot instead, that same
      derivation would read the snapshot's own (non-)git state, not the
      originating checkout's. The preview-install path must therefore
      capture `commit`/`branch`/`dirty` from the originating checkout
      explicitly, at preview-generation time, and pass them through to the
      installed manifest rather than re-deriving them post-hoc from
      `source.path`. Use a **dirty branch** as the test case and assert all
      three installed manifest fields match the origin while `source.path`
      still points at the snapshot.
- [ ] Two numbered-install preview builds with the SAME computed base
      version but DIFFERENT content (e.g. two dirty checkouts sharing a
      pending changefile set) mint two distinct numbered slots AND report
      two distinct reported version identities — not slot separation
      alone; a byte-identical rebuild of the same content may reuse its
      own slot and identity.
- [ ] A later REAL promoted release supersedes a previously-installed
      preview build that reported the same base version: reconciliation
      (`agent_worktrees.reconcile`'s `_versions_equal`/`_version_lt`),
      downgrade guards, and runtime fallback/GC all correctly deploy the
      real release rather than treating it as already-equal or a
      downgrade -- exercise this transition explicitly, not just the
      preview-vs-preview distinct-slot case above.
- [ ] A removed/superseded numbered slot's persisted preview snapshot is
      reclaimed once no current/fallback/live slot references it, and a
      snapshot still referenced by any such slot survives a GC pass that
      also removes an unrelated unreferenced snapshot.
- [ ] Every vendorable's own `libs/<lib>/pyproject.toml` (canonical and
      real copies) reads `"0.0.0"` on `dev` alongside the per-plugin
      manifests, and vendorable seeding (above) still recovers its real
      last-shipped version from `main` with no local fallback needed.
- [ ] Every catalog-only field (e.g. `agent-pull-requests`'s
      `defaultEnabled: false`) survives unchanged in the generated `main`
      snapshot's `marketplace.json`, sourced from its new canonical location
      rather than lost when the old file's content is replaced.
- [ ] `check-docs-consistency.py` and `check-runbook-references.py` still
      load a valid, non-empty plugin roster from `dev`'s
      `marketplace.json` placeholder — confirm neither crashes nor silently
      validates against an empty catalog, even though the same file is
      simultaneously unusable as a live Copilot CLI install source.
- [ ] A vendorable's version correctly continues from its last value shipped
      on `main` across repeated promotions (not from the `0.0.0` placeholder)
      — run at least two sequential promotions in the validation harness and
      confirm the second's starting point is the first's real output, not
      `0.0.0`.
- [ ] A write dropped by `accumulate_bumps.py`'s `apply()` (simulate
      `_write_source_fallbacks()`/`_write_instruction_projection_owners()`
      failing) fails the promotion run closed, rather than silently
      producing an inconsistent generated snapshot on `main`.
- [ ] A real promotion of `ai-attribution` (or any plugin with a hook-owned
      nonstandard version literal) writes the real computed version into
      that literal too, and `test_emit_policy.py`'s own existing guard
      passes against the generated `main` snapshot — not just the standard
      `plugin.json`/`pyproject.toml` surfaces.
- [ ] The refactored post-generation structural validations (non-literal/
      invalid/duplicate fallback assignment, a missing version field or
      catalog entry, a real cross-surface mismatch) still fire against the
      materialized `main` snapshot, with the same fixture cases
      `check-version-consistency.py`'s own test suite already covers today
      — including every hook-owned version literal (e.g. `ai-attribution`'s
      `emit-policy.sh`/`emit-policy.ps1`), not just `plugin.json`/
      `pyproject.toml`/`marketplace.json`.
- [ ] `dev`'s own `plugin.json`/`pyproject.toml`/source `__version__`
      fields, AND every other hook-owned version literal discovered during
      the pre-implementation inventory, all read `"0.0.0"` after this phase
      lands, and nothing on `dev` (docs-consistency/runbook-reference
      checks, existing guard tests like `test_emit_policy.py`) misbehaves
      against that placeholder.
- [ ] `dev`'s `.github/plugin/marketplace.json` is genuinely non-functional
      — feed it to the real marketplace-reading path (or the closest
      available test double) and confirm it is rejected/fails to resolve
      any plugin, not merely parsed as a valid catalog whose versions
      happen to read `"0.0.0"`.
