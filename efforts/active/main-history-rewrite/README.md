# `main` History Rewrite — Purge Coverage-Baseline Blob Bloat

- **Slug:** `main-history-rewrite`
- **Repo:** copilot-extensions
- **Branch(es):** this plan PR against `dev`; execution is a direct,
  out-of-band operation against `main` (not a normal promotion — see
  Coordination)
- **Created:** 2026-10-04
- **Status:** Active
- **Related:** follows directly from
  [`efforts/active/coverage-guided-ci`](../coverage-guided-ci/README.md)'s
  pipeline-health work (the coverage-baseline design fixed in PRs #5078 /
  #5085 / #5097 / #5098, which stopped the bleeding but left the historical
  bloat those PRs' predecessors had already committed)
- **Umbrella issue:** none yet; this is a short, bounded, already-fully-planned
  operation rather than an open-ended effort

## Guiding Intent

`main`'s current tip is clean, but its **history** still carries roughly
150-300MB across 160+ oversized blob versions (`.github/coverage-baselines/*`
JSON files up to ~14MB each, committed before the coverage-baseline design
moved to GitHub Release assets). This effort purges that historical bloat
with a one-shot, deliberate `git filter-repo` rewrite of `main`'s history —
content-identical at every commit, but excising any blob over a fixed size
threshold. `dev` is never touched; only `main`'s own line is rewritten, and
only because `main`'s sole remaining consumers are **extension installers**
(`copilot plugin install/update`, `worktree-manager` self-update), which were
audited and confirmed to resolve by branch name / ref, never a pinned commit
SHA — see `CONTRIBUTING.md`'s "If main's history is force-rewritten" section
(added in PR #5110) for the full consumer-safety writeup and smoke-test
evidence.

## ⚠️ Pipeline hold — read this before touching `main` or the promotion flow

**While this effort is `Active`, the dev→main promotion pipeline is
deliberately paused** via the repo's own native mechanism:

```
python tools/rollback_release.py pause --reason "main-history-rewrite in progress (see efforts/active/main-history-rewrite)" --push
```

This lands a small, workflow-recognized pause marker commit directly on
`main` (per that tool's own documented contract — see its module docstring);
`tools/promote_release.py`'s own pause check means **no promotion will land
on `main` while this is in effect**, regardless of how much accumulates on
`dev`. This is the same mechanism the repo already uses for rollback
investigations — it is not a new control, just a new reason to use it.

- **If you are another agent and you see the pipeline paused with this
  effort cited as the reason:** this is expected and intentional. Do not
  unpause it, do not treat it as a stuck/broken pipeline, and do not escalate
  it as an error — see `docs/error-response-discipline.md`'s standing rule
  against diagnosing a system that is behaving exactly as designed. `dev`
  keeps accepting and merging PRs as normal; only the `main` promotion step
  is held. Everything will resume automatically once this effort's status
  below is updated to reflect the hold being lifted.
- **Status of the hold:** see the Journal below for the live state (paused /
  rewrite in progress / resumed).
- **The actual irreversible step** (the `git filter-repo` rewrite + force-push
  of `main` + its `promote-*` tags) happens from an isolated mirror clone,
  never this worktree or `dev` directly, per the execution plan this effort
  tracks.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Operator | Authorized the rewrite in principle; gives the final in-the-moment go-ahead immediately before the irreversible force-push | direct session |
| Executing agent | Runs Phase 0-6 below, verifies each step, reports back | isolated worktree + a dedicated mirror clone (never a tracked worktree) |

## Context

`main`'s HEAD is clean (a handful of small coverage-baseline correlation
pointer files, a few hundred bytes each), but its **history** still carries
the pre-fix blob versions of those same files from before the
coverage-baseline design moved to GitHub Release assets (PRs #5078 / #5085 /
#5097 / #5098, closing #5075). A full audit found 162 blobs over 1MB
(~300MB total, range ~1MB-14MB) across `main`'s own history — far above the
~270-byte pointer files that replaced them. `main`'s only remaining
consumers are extension installers (`copilot plugin install/update`,
`worktree-manager` self-update); both were confirmed, by live smoke test
against a throwaway repo and by reading `worktree-manager`'s own
`self_install.py`, to resolve `main` by branch name / ref rather than a
pinned commit SHA, so neither breaks on a history rewrite. See
`CONTRIBUTING.md`'s "If main's history is force-rewritten" section (added in
PR #5110) for the full consumer-safety writeup. `tools/rollback_release.py`
is the one real design constraint surfaced by the audit: it finds the
latest promotion by walking `main`'s history for the newest commit carrying
a `promote-*` tag, so the rewrite must preserve/remap tags by name
(`git filter-repo`, never a destructive squash/orphan-commit approach).

## Request

Operator-authorized: perform a one-shot, deliberate history rewrite of
`main` to purge the accumulated oversized blobs, with a prominent
in-repo banner recording the exact pre-rewrite and post-rewrite tip SHAs so
anyone diffing/bisecting/reading history later isn't confused by the
discontinuity. Full operator authority for the rewrite and cutover was
granted once this effort's prerequisites (this doc, the banner stub, the
pipeline hold) are in place — no further check-in is required before
executing Phases 0-6 below.

## Coordination

- **Topology:** this README + the banner-stub commit land via the normal
  `dev` → promotion → `main` path *before* the rewrite (so the announcement
  banner's scaffold is itself part of `main`'s pre-rewrite history, landing
  at the exact historical pivot point). The rewrite itself is **not** a
  promotion — it is a direct, scoped force-push
  (`git push origin +refs/heads/main refs/tags/promote-*:refs/tags/promote-*`)
  from an isolated mirror clone, executed only after an explicit operator
  go-ahead immediately beforehand.
- **Host:** the executing agent/session owns the whole operation end-to-end;
  no delegation planned (single, short, fully-sequenced operation).

## Plan

1. **Pre-flight** — confirm no promotion is in-flight, confirm branch
   protection bypass is live for the pushing identity, back up `main` via
   `git clone --mirror` to at least two locations, pause the pipeline (see
   above).
2. **Rewrite** — in an isolated mirror clone (never a tracked worktree):
   `git filter-repo --strip-blobs-bigger-than 1M --force`. Size-based, not
   path-based, so it is correct-by-construction across every oversized blob
   regardless of which file introduced it.
3. **Verify in isolation** — `git fsck --full`; confirm the new tip's tree is
   byte-identical to the old tip's tree; confirm all `promote-*` tags survive
   (remapped, not dropped); record the exact old-tip-SHA -> new-tip-SHA pair.
4. **Cutover** — scoped force-push of `main` + `promote-*` tags only, after an
   explicit in-the-moment operator go-ahead.
5. **Post-rewrite verification** — fresh fetch + tree comparison;
   `copilot plugin update` / `copilot plugin marketplace update`;
   `tools/rollback_release.py status`; resume the pipeline; wait for the next
   real promotion to go green end-to-end.
6. **Banner follow-up** — a tiny follow-up PR updates the banner stub (landed
   in this plan's own PR, see below) with the real new-tip SHA once known.
7. **Close out** — journal final before/after sizes here, move this effort to
   `efforts/done/`, resume normal promotion traffic.

## Validation Plan

- [ ] Pipeline pause commit landed and confirmed via
      `tools/rollback_release.py status`.
- [ ] Mirror backup exists in at least two locations.
- [ ] Rewritten mirror's `main` tree is byte-identical to pre-rewrite `main`
      tree (`git diff <old> <new>` empty).
- [ ] All pre-rewrite `promote-*` tags resolve post-rewrite.
- [ ] Real repo's `main` force-pushed; fresh fetch elsewhere confirms the same
      tree.
- [ ] `copilot plugin update` / `marketplace update` succeed against the
      rewritten `main` (real-world re-confirmation of the earlier smoke test).
- [ ] `tools/rollback_release.py status` still resolves the latest promotion
      correctly post-rewrite.
- [ ] Pipeline resumed; next real `dev`->`main` promotion goes green
      end-to-end.
- [ ] Banner follow-up PR lands with the real old->new SHA pair.
- [ ] This effort moved to `efforts/done/` with final sizes journaled below.

## Journal

- **2026-10-04** — Effort opened. Pre-rewrite audit (this session): `main`'s
  history carries 162 blobs over 1MB (~300MB) as of commit `0e5936ae`. 240
  `promote-*` tags exist. Branch-protection bypass confirmed live for the
  pushing identity (ruleset 18553911, `bypass_mode: always`). A promotion was
  in-flight at audit time (`release/promote-37195835136`); waiting for it to
  land naturally before pausing. `git-filter-repo` 2.38.0-2 installed.
