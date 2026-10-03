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
      consumer map), AND keeps every one of the vendorable's own real
      vendored copies (e.g. `plugins/agent-worktrees/libs/zdd/pyproject.toml`)
      at the same version as its canonical manifest — preserving
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
      no longer does.
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
      `peer-launch`) has a working changefile target and participates in
      reverse (over-coverage) checking the same as a `pyproject.toml`-backed
      vendorable.
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
