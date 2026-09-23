# Draft: CONTRIBUTING.md / AGENTS.md rewrite for the dev/main release pipeline

> **Status: DRAFT — not yet in effect.** This is Phase 1 content for the
> `dev-branch-release-pipeline` effort
> (`efforts/active/dev-branch-release-pipeline/README.md`). Today's actual
> contribution flow is still CONTRIBUTING.md's current
> § "Release & Versioning" (manual three-file version bump, no `dev` branch,
> `main` is both trunk and release). **Nothing here is authoritative until
> Phase 2 (cutting the `dev` fork) lands** — at which point this content (or
> its evolution) replaces CONTRIBUTING.md § "Release & Versioning" and
> AGENTS.md § "Version Bump — Required For Every Plugin Change" wholesale,
> not as an addition alongside them.

## What changes for a contributor

| Today (single-branch `main`) | After Phase 2 (dev/main split) |
|---|---|
| PR targets `main` directly. | PR targets `dev`. Never open a PR against `main` — branch protection bounces it (Phase 2 item). |
| Contributor hand-edits `plugin.json` + `pyproject.toml` + `marketplace.json` versions in the same commit. | Contributor runs `python tools/changefile.py add --plugin <name> --type <major\|minor\|patch\|dev> --comment "<summary>"` once per touched plugin. No version numbers are hand-typed, ever — this is the structural fix for ThomasMichon/copilot-extensions#182's parallel-PR `-devN` collisions. |
| A shared `libs/<lib>` change means hand-copying it into every `plugins/<plugin>/libs/<lib>` and bumping every consumer's version. | Contributor edits canonical `libs/<lib>/` only. **Depends on Phase 2 first restoring canonical for any currently-drifted lib** — see ThomasMichon/copilot-extensions#3361; until that lands, treat `plugins/*/libs/<lib>` copies as the real source, same as today. |
| `check-version-bump.py` fails a PR that touched a plugin without a matching version bump. | An equivalent guard fails a PR that touched a plugin without a matching **changefile** — the check moves from "did you bump three files correctly" to "did you declare a changefile at all." |
| The change is live for every consumer the moment the PR merges. | The change reaches `dev` immediately, but the CI promotion pipeline is what actually ships it to `main` (Phase 3) — there is a real wait, not instant. See "The wait, and how to preview past it" below. |

## Adding a changefile

```bash
python tools/changefile.py add --plugin agent-worktrees --type patch --comment "Fix X"
# a PR touching two plugins with one shared reason:
python tools/changefile.py add \
  --plugin agent-worktrees --type patch \
  --plugin agent-bridge --type dev \
  --comment "Shared fix for Y"
python tools/changefile.py list   # see what's pending
```

Bump-type guidance carries over unchanged from today's convention: **default
to `patch`** (or `dev` for an iterative fixup within an already-in-flight
patch); only use `minor`/`major` when the maintainer says so. Multiple
changefiles may target the same plugin (e.g. two different PRs); whichever
carries the biggest bump type wins when they're all consumed together
(`tools/accumulate_bumps.py`'s `highest_bump`) — you never need to coordinate
with another PR author over the exact number.

## The wait, and how to preview past it

Merging to `dev` is not the same as shipping. Every consumer still only ever
polls `main`. Until Phase 3's CI promotion pipeline exists, there is no
committed cadence yet for how often `dev` promotes to `main` — the design
intent (see the effort's Journal) is "triggered by a green `dev` build, not a
schedule," but this isn't implemented yet.

Two tools close the impatience gap without waiting on a real promotion:

- **`python tools/preview_release.py <plugin>`** builds a scratch copy of
  that plugin's payload — with its vendored `libs/<lib>` materialized from
  canonical, and the version it would get if its pending changefiles were
  consumed right now — entirely read-only against your real checkout. Good
  for "what would ship" without touching anything.
- **For actually running your own uncommitted/unmerged code against the real
  deployed CLI**, use the **mutable-dev-slot** pattern instead of anything in
  this effort: `docs/patterns/mutable-dev-slot.md` /
  `efforts/active/mutable-dev-slot/README.md`
  (ThomasMichon/copilot-extensions#3376). It gives each plugin a claimed,
  first-class `versions/dev/` runtime slot rebuilt in place, GC-protected by
  a `dev-claim.json` sidecar. This effort's own `tools/dev_slot.py` prototype
  was **withdrawn** once that pattern was discovered mid-session — don't
  resurrect it; this is the canonical mechanism for "preview my live code
  against the real CLI now."

## What stays the same

- The PR flow itself (worktree, push-changes, create-pr, address review,
  self-merge once checks pass) is unchanged — only the target branch and the
  version/vendoring mechanics change.
- CONTRIBUTING.md's other sections (Code Style, Git Hooks, Test Portfolio
  Discipline, per-plugin Deployment Pipelines, Gotchas) are untouched by this
  effort.

## Open items before this can go live (tracked in the effort Plan)

- Phase 2: cut `dev`, retire `check-version-bump.py`'s manual-bump
  requirement for a changefile-presence check, add branch protection on
  `main`.
- Phase 3: the actual CI promotion pipeline (validation gate, materialize,
  wholesale-replace `main`, tag).
- Restore canonical `libs/ssh-manager` and `libs/credential-relay`
  (ThomasMichon/copilot-extensions#3361) before the "edit canonical only"
  row above becomes literally true.
