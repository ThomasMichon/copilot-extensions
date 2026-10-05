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

### Phase 1 — tooling to make migration mechanical
- [ ] Small script/check: given one name, resolve (a) its current definition
      site, (b) every `core().<name>` call site, (c) every
      `monkeypatch.setattr(m, "<name>"` test site — and report the three as
      one unit, so a migration slice is verifiably complete rather than
      eyeballed.
- [ ] _(agent-recommended)_ Consider a `--progress` mode reporting the live
      47/317/112 counts, so each slice's reduction is measurable the same way
      `rank-module-size.py` measures the line-count campaign.

### Phase 2 — migrate the highest-traffic names
- [ ] `_json_output` (66 call sites / 29 monkeypatch sites) — give it a real
      shared-module home; repoint tests; convert call sites; drop the root
      re-export.
- [ ] `_resolve_worktree_id` (11 call sites / 34 monkeypatch sites).
- [ ] `_json_error`, `_find_repo_dir`, `_normalize_path` (next-highest
      traffic; re-measure before picking exact order).
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
