# Identifier-Leak-Guard Scrub

- **Slug:** `identifier-leak-guard-scrub`
- **Repo:** copilot-extensions
- **Branch(es):** per-category `pr/<slug>` worktrees → landed to `dev`
- **Created:** 2026-10-01
- **Status:** Active
- **Source issue:** [#4834](https://github.com/ThomasMichon/copilot-extensions/issues/4834)
  — pre-existing identifier leaks discovered while expanding the
  identifier-leak-guard denylist (see the issue for the full canary list and
  per-category findings; this doc intentionally avoids repeating the literal
  flagged strings, since that would itself be a leak in this public tree).

## Guiding Intent

`tools/check-no-internal-identifiers.py` / the `identifier-leak-guard` workflow
only scans a PR's **changed files**, so these pre-existing matches don't fail
CI today — but the next PR touching any of these files will. Scrub every
cataloged match out of the tree so the denylist is clean going forward, without
losing the underlying lesson/explanation any prose occurrence was recording.

## Context

While assembling an expanded identifier-leak-guard denylist, the private
control-plane repo's own effort expanded the `FORBIDDEN_IDS_WORK` denylist
(sourced from its canonical cross-repo doc; not named here) with several new
canaries. Several of those newly-added canaries revealed pre-existing,
already-committed matches in this tree, cataloged in #4834. None of these are
secret from within their own originating context — they're secret only from
this public repo.

## Request

Resolve every category cataloged in #4834 (re-read it fresh for the exact
file/occurrence lists):

1. A private-repository-name canary and its case-sensitive acronym variant —
   the largest category, spanning runtime source, scripts, docs, and
   effort-journal narrative prose.
2. An internal-GitHub-organization canary — test fixtures, one runtime
   docstring citing a real issue number, and a doc narrating a real incident
   by name.
3. Two machine-name placeholders derived from an internal workflow-automation
   system's naming stem (the stem itself is an intentional canary and not a
   leak on its own; only the derived machine-name instances are) — purely
   mechanical test-fixture renames.
4. An EMU-style account-suffix convention — one already-anonymized test
   placeholder plus a few effort-doc mentions of a private companion repo.
5. An internal repository-name prefix — one test fixture, lower priority.
6. A whole-word service abbreviation — one placeholder string, cosmetic.

## Plan

- [x] Categories 3/4/5/6: mechanical test-fixture/placeholder renames (no
      narrative content to preserve).
- [x] Category 2: fixture renames + the runtime docstring + effort-doc
      mentions + the doc narrating a real incident (generalized to a
      fictional `fabrikam/fabrikam-harness` example repo, lesson kept intact).
- [x] Category 1: across runtime source, scripts, docs, and effort-journal
      narrative prose (generalized to "the Copilot CLI runtime" / "a private
      Copilot CLI runtime issue", never a resolvable link).
- [x] Re-validated with `tools/check-no-internal-identifiers.py --all --ci`
      against the current denylist (fetched fresh via `git show
      origin/main:docs/identifier-leak-guard/forbidden-identifiers-work.txt`
      from the private control-plane repo): all six cataloged categories now
      report zero matches.
- [ ] Land via PR(s) to `dev`, close #4834.

## Validation Plan

- [x] `python tools/check-no-internal-identifiers.py --all --ci` (with the
      current forbidden-ids list set via `COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI`)
      reports zero matches for every resolved category.
- [x] Existing plugin test suites touched by renames still pass
      (`agent-worktrees` full suite: 555 passed; targeted `-k` reruns of
      every touched test file in `agent-bridge`, `agent-dispatch`,
      `agent-index`, `agent-logger` all green; mechanically-edited files in
      `worktree-manager` and `agent-containers`/`agent-mcp` verified by
      `py_compile`/JSON-parse since their venvs hit a transient package-feed
      network outage unrelated to this change).

## Journal

- **2026-10-01** — Effort opened from a context-handoff continuing the
  harness-side identifier-leak-guard expansion work (a private control-plane
  repo's effort, not named here). Scope is purely mechanical/cosmetic
  scrubbing of already-cataloged matches (see #4834 for full findings); no
  new leaks discovered during this effort's own work are expected, but if the
  scrub itself needs to record an internal specific it will be abstracted per
  `planning-efforts`'s own "keep internal specifics out of a public-facing
  effort" guidance.
- **2026-10-01** — All six cataloged categories resolved across 46 files:
  mechanical test-fixture renames for the two lower-priority/cosmetic
  canaries and the derived-machine-name pair; the private-org canary's test
  fixtures, one runtime docstring (real issue citation generalized), and one
  doc's worked incident example (fictionalized while preserving the lesson);
  the private-repo-name canary and its case-sensitive acronym across runtime
  source comments, shell/PowerShell scripts, a skill reference doc, and
  several effort-journal narrative sections (all generalized to "the Copilot
  CLI runtime" / "a private Copilot CLI runtime issue", with every
  previously-resolvable private link replaced by unlinked prose per this
  effort's own one-way-linking rule). Full-tree rescan against the current
  denylist confirms zero remaining matches for all six categories. Next:
  land via PR(s) to `dev` and close #4834.
