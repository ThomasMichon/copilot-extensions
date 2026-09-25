# Vendored Doc Pointers

- **Slug:** `vendored-doc-pointers`
- **Repo:** copilot-extensions
- **Branch(es):** per-slice worktree branch (not recorded here to keep this
  public artifact generic)
- **Created:** 2026-09-24
- **Status:** Active
- **Vision:** `visions/plugin-services` §Concepts & Components/`Entity-relationship
  diagnosability` (extends: the mirrored-doc duplication this effort removes was
  introduced to satisfy that same concept). **Pending:** this vision section is
  proposed by PR [#3554](https://github.com/ThomasMichon/copilot-extensions/pull/3554)
  and not yet present on `dev` — until that PR merges, resolve the reference
  against `docs/patterns/entity-relationship-model.md` in PR #3554's branch,
  not against `visions/plugin-services/README.md` on this checkout.
- **Umbrella issue:** [ThomasMichon/copilot-extensions#3565](https://github.com/ThomasMichon/copilot-extensions/issues/3565)
- **Sub-issues:** [#3561](https://github.com/ThomasMichon/copilot-extensions/issues/3561)
  (push-hook bypass, Phase 3 below — a distinct bug surfaced by the same PR,
  tracked in the same effort because both fixes were discovered while
  authoring/landing PR #3554 and the operator asked to track them together)

## Guiding Intent

PR #3554 (`docs/patterns/entity-relationship-model.md`, the cross-plugin
diagnostic playbook) had to hand-duplicate its one canonical file into three
plugins' own vendored `docs/` directories (`agent-worktrees`, `agent-bridge`,
`agent-dispatch`) so the content actually ships with each plugin's marketplace
payload — repo-root `docs/` is never vendored (each plugin's marketplace
`"source"` is scoped to `plugins/<name>` only, confirmed against a real
installed-plugins tree). Keeping three byte-identical copies in sync by hand
is exactly the class of problem `libs/ssh-manager` and its ~10 sibling shared
libraries already have, which the `dev-branch-release-pipeline` effort solved
via a `VENDOR_POINTER.json` sidecar: a lightweight
`{"source": "libs/<lib>"}` stub that `tools/materialize_main.py` expands into
a full copy at `dev` → `main` promotion time. That mechanism is proven
end-to-end (26+ tests, a standalone trial clone) but is scoped only to
`plugins/<plugin>/libs/<lib>/` and has not yet been adopted by any real
plugin.

This effort generalizes that mechanism beyond `libs/` to cover **any**
duplicated payload file — starting with the `entity-relationship-model.md`
mirrors as the first real conversion — so a contributor edits ONE canonical
copy in `dev` and the promotion pipeline expands it into every vendored
location in `main`, instead of hand-copying N times and hoping the copies
never drift (as already happened once this session: a review round caught
content that was fixed in the canonical file but not yet re-synced to all
three mirrors).

Separately, authoring and landing PR #3554 surfaced a real bug in the
mechanism that is supposed to catch exactly this kind of drift locally:
`agent-worktrees`' own `push()` silently disables **all** pre-push hooks
(including `check-changefile-presence.py`) on every real publish push, not
just its internal rebase/squash plumbing — so the release guard that is
documented as "pre-push + CI" only ever actually runs in CI for a
`push-changes`/`create-pr`-driven publish. This effort's Phase 3 fixes that,
tracked jointly here per the operator's explicit request to track both
findings together.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| the driving Copilot CLI session | sole driver | its own linked worktree checkout |

## Coordination

- **Topology:** single-driver, per-phase PRs (each phase lands independently
  once its own validation passes — do not bundle Phase 1/2 tooling changes
  with the Phase 3 hook fix in one PR).
- **Host (owns PRs):** the driving session.
- **Delegates:** none currently.
- **Handoff:** via this repo's own context-handoff mechanism; a successor
  picks up at whichever Phase's checklist is first incomplete.

## Context

- The validated prototype this effort generalizes:
  `efforts/active/dev-branch-release-pipeline/README.md` Phase 1 (the
  `VENDOR_POINTER.json` design + `tools/materialize_main.py` +
  `tools/sync-vendored-libs.py --materialize`).
- The concrete pain that motivated this effort:
  `docs/patterns/entity-relationship-model.md` and its three mirrors
  (`plugins/agent-worktrees/docs/`, `plugins/agent-bridge/docs/`,
  `plugins/agent-dispatch/docs/`), introduced by PR #3554 (still open as of
  this writing).
- The hook-bypass bug: `plugins/agent-worktrees/src/agent_worktrees/git_ops.py`
  `push()` — `no_hooks=True` unconditionally, on every push including the
  terminal publish push. See issue #3561 for the full reproduction (a raw
  `git push` missing a changefile is correctly blocked by
  `tools/hooks/pre-push`; the identical case via `agent-worktrees push-changes`
  / `create-pr` is not).

## Request

Per operator direction (session that authored PR #3554):

1. Design and build a generalized vendor-pointer mechanism covering arbitrary
   duplicated files (not just `libs/<lib>/src`), with an "in-language" marker
   where practical (e.g. an HTML comment at the top of a Markdown mirror,
   understandable by an agent reading the file directly, without first
   reading the vendoring tooling's own docs) — see issue #3565 for the open
   design question of whether this fully replaces or supplements the
   JSON-sidecar approach for docs.
2. Convert the three `entity-relationship-model.md` mirrors from hand-copies
   to real pointers using the new mechanism, proving it end-to-end on a real
   file (not just a synthetic test fixture).
3. Fix `agent-worktrees`' `push()` so a real publish push cannot silently
   bypass the release-guard hooks — see issue #3561 for the suggested fix
   shape (distinguish "internal plumbing that re-arranges already-committed
   content" from "the final push that publishes to `origin`").

## Plan

### Phase 1 — Design the generalized pointer mechanism
- [x] Read `tools/materialize_main.py`, `tools/sync-vendored-libs.py`, and the
      `VENDOR_POINTER.json` schema in full; confirm exactly what would need to
      generalize (currently hardcoded to `libs/<lib>/src` + a version-line
      rewrite in `pyproject.toml` — neither applies to a plain doc file).
      **Confirmed:** neither script's directory-pointer logic (copytree +
      version-line rewrite) applies to a single file; the new file-pointer
      kind needed its own find/expand functions rather than reusing the lib
      case's internals.
- [x] Decide the pointer shape for a non-lib file: reuse
      `VENDOR_POINTER.json` with a generalized `source`/`kind` field, or a
      distinct format. Justify the choice against issue #3565's stated
      preference for an in-language marker. **Decided:** a distinct format --
      a single-line HTML-comment marker
      (`<!-- VENDOR_POINTER: source=<repo-relative-path> kind=file -->`) as
      the file's own first line, not a JSON sidecar. A directory/lib pointer
      can be an empty stub next to a real sibling file (`pyproject.toml`), but
      a single vendored file has no sibling to carry a sidecar without a
      second file appearing in the mirror location -- and issue #3565 asked
      specifically for content an agent can read directly, in the file's own
      language, without first discovering `VENDOR_POINTER.json`'s schema.
      See `tools/materialize_main.py`'s module docstring for the full
      rationale and both pointer kinds side by side.
- [x] Prototype the in-language marker for Markdown specifically (e.g. an
      HTML comment header) and confirm it round-trips: a human/agent reading
      the `dev`-branch file understands it's a mirror without external docs,
      and the promotion tooling can still parse it mechanically. **Done:**
      `materialize_main._file_pointer_source()` matches the marker line
      exactly (`kind=file`, `source=` required) via `_FILE_POINTER_RE`; an
      HTML comment is invisible when the Markdown renders but plainly visible
      in source, and materializing overwrites the stub's own content with the
      canonical file's bytes in place (no separate pointer file to delete,
      unlike the lib case).
- [x] Extend `tools/materialize_main.py` (and its test suite) to expand the
      new pointer kind, with the same non-regression guarantees the lib case
      has (never silently wipes canonical, refuses to materialize from a
      stale/missing source). **Done:** `find_file_pointers()` +
      `materialize_file_pointers()`, wired into `materialize()`/`build()`;
      6 new tests (byte-identical round-trip, missing-canonical SKIP leaves
      the stub untouched, multiple mirrors of the same doc, non-pointer files
      ignored, full `build()` end-to-end). Also extended
      `tools/preview_release.py`'s per-plugin materializer
      (`_materialize_file_pointers_into_preview`) to expand file pointers
      into a single-plugin preview copy, matching a gap the Copilot reviewer
      caught on PR #3566 (the per-plugin preview tool only expanded lib
      pointers, so Phase 2's own preview-validation step could not have
      passed without this).

### Phase 2 — Convert the entity-relationship-model.md mirrors
- [ ] Convert `plugins/agent-worktrees/docs/entity-relationship-model.md`,
      `plugins/agent-bridge/docs/entity-relationship-model.md`, and
      `plugins/agent-dispatch/docs/entity-relationship-model.md` (proposed by
      PR #3554, still open) from full hand-copies to real pointers
      referencing `docs/patterns/entity-relationship-model.md`.
- [ ] Confirm `tools/preview_release.py`/`tools/materialize_main.py` produce
      byte-identical output to the current hand-copies for all three (Phase 1
      already extends `preview_release.py`'s per-plugin materializer to
      expand the generalized file-pointer kind, not just the lib kind, so
      this validation can run per-plugin as documented).
- [ ] Update `docs/patterns/entity-relationship-model.md`'s own "See Also"
      section (currently plain-text repo references because the mirrors
      couldn't resolve a relative link) once the mirrors are pointers instead
      of static copies — revisit whether relative links become viable again,
      or whether the plain-text convention should stay regardless.

### Phase 3 — Fix the push() hook bypass
- [ ] Read `plugins/agent-worktrees/src/agent_worktrees/git_ops.py`'s `push()`
      and every call site to distinguish internal-plumbing pushes (rebase
      retries, mid-flow branch updates) from the terminal publish push.
- [ ] Land a fix scoped to the terminal publish push only — re-enable the
      release-guard hooks (or explicitly invoke the relevant checks, at
      minimum `check-changefile-presence.py`) for that call, while keeping
      `no_hooks=True`'s existing, still-valid rationale for internal
      rebase/squash plumbing.
- [ ] Add a regression test: a push through `push-changes`/`create-pr` with a
      missing required changefile must fail locally, matching the raw
      `git push` behavior already proven in issue #3561's reproduction.
- [ ] Confirm no existing `push-changes`/`create-pr`/`finalize` test suite
      relies on the current bypass behavior (e.g. tests that push
      intentionally-non-compliant content as part of a scratch/synthetic
      fixture) before landing.

## Validation Plan

- [ ] `tools/materialize_main.py`'s test suite covers the new pointer kind
      with the same rigor as the existing lib case (byte-identical
      round-trip, refuses on missing/stale source).
- [ ] A real `python tools/preview_release.py <plugin>` run for each of the
      three converted plugins shows no diff against the current hand-copy
      content.
- [ ] A reproduction of issue #3561's exact scenario (push a commit touching
      a plugin's payload with no changefile, through `push-changes`/
      `create-pr`, not raw `git push`) is now blocked locally, matching CI.
- [ ] `python tools/check-changefile-presence.py` and
      `python tools/check-docs-consistency.py` both pass after each phase's
      changes.

## Proposal

**File-pointer format (decided in Phase 1):** a vendored file's first line is
an HTML comment marker:

```
<!-- VENDOR_POINTER: source=<repo-relative-path> kind=file -->
```

This is distinct from the directory/lib pointer's `VENDOR_POINTER.json`
sidecar -- a single vendored file has no sibling location to carry a JSON
sidecar without introducing a second file at the mirror's path, and the
kickoff request specifically asked for a marker readable in the file's own
language, without needing to discover the JSON schema first. On `dev` the
mirror file's content is a short stub (the marker line plus a one-paragraph
human/agent-readable explanation); at promotion time
`tools/materialize_main.py`'s `materialize_file_pointers()` overwrites that
stub's content in place with the canonical file's bytes (no separate pointer
file to delete, unlike the lib case, since the pointer *is* the mirrored
file). `tools/preview_release.py`'s per-plugin preview materializer expands
the same pointer kind, scoped to the one plugin being previewed.

See `tools/materialize_main.py`'s module docstring for the full two-kind
comparison and `tools/test_materialize_main.py` /
`tools/test_preview_release.py` for the round-trip test coverage.

## Journal

### 2026-09-24 — Kickoff
- Effort created after landing PR #3554
  (`docs/patterns/entity-relationship-model.md`) surfaced two related
  findings: (1) the same doc-duplication problem `dev-branch-release-pipeline`
  already solved for shared libs, not yet generalized to docs, and (2) a real
  bug in `agent-worktrees push()` that silently bypasses the pre-push
  release-guard hooks documented as the local enforcement point for exactly
  this kind of drift. Filed the umbrella issue (#3565) and cross-referenced
  the pre-existing hook-bypass issue (#3561). Handed off immediately after
  kickoff per operator direction — no phase work started yet.

### 2026-09-24/25 — Phase 1 landed
- Designed and built the generalized file-pointer mechanism: an in-language
  HTML-comment marker (see Proposal above), `materialize_main.py`'s
  `find_file_pointers()`/`materialize_file_pointers()`, and
  `preview_release.py`'s per-plugin equivalent. 6 new tests in each of
  `test_materialize_main.py` and `test_preview_release.py`, all passing;
  `ruff check` clean; `check-docs-consistency.py` and
  `check-changefile-presence.py` both pass (no changefile needed -- these are
  repo-root `tools/` changes, not a plugin payload). Landed as its own PR per
  this effort's own per-phase-PR coordination rule, in a fresh worktree (not
  PR #3554's or #3566's) since neither of those branches was the right home
  for Phase 1 code.
- Two review rounds on PR #3554 and #3566 (the two prior open PRs, watched
  through to keep them from going stale) caught: a false-positive "ten
  durable" claim on #3554 (checked -- both the PR description and the doc
  already correctly said "nine durable, one transient"; replied and requested
  a fresh review rather than editing already-correct content); an
  unresolvable Vision reference on #3566 (the cited `visions/plugin-services`
  section doesn't exist on `dev` yet -- it's proposed by the still-open
  PR #3554 -- made the dependency explicit); and an inaccurate "landed via
  PR #3554" claim (that PR is still open) fixed to "introduced by"/"proposed
  by" in two places. A third, structurally important finding on #3566 (this
  effort's own Validation Plan calls for a per-plugin `preview_release.py`
  run to show no diff, but that tool's materializer only expanded lib
  pointers) became the extra `preview_release.py` extension folded into this
  same Phase 1 PR rather than deferred to Phase 2, since Phase 2's validation
  step cannot pass without it.
