# PR Attribution Codenames

- **Slug:** `pr-attribution-codenames`
- **Repo:** copilot-extensions (agent-worktrees plugin)
- **Branch(es):** `pr/<slug>` per phase
- **Created:** 2026-09-17
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** vision-extending — extends the existing `source_attribution`
  marker capability (today boolean: on/off) with a third, public-safe mode.
- **Umbrella issue:** [#2838](https://github.com/ThomasMichon/copilot-extensions/issues/2838)

## Guiding Intent

Give an author a way to trace a PR back to its originating worktree even on
a repo where raw machine/worktree/session identifiers must never be
published — without inventing a new private-identifier leak in the process.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions maintainer(s) | design + implementation | this worktree |

## Coordination

- **Topology:** independent per-phase PRs (small, reviewable increments)
- **Host (owns PRs):** this worktree/author
- **Delegates:** none
- **Handoff:** n/a

## Context

`agent-worktrees` already supports a `source_attribution` config flag: when
`true`, `create-pr` embeds a hidden PR-body marker carrying the source
machine/worktree/session/head. This is intentionally `false` by default and
documented as required-`false` for public repos, since raw
machine/worktree/session identifiers (and worktree-derived branch names) are
private identifiers that must never reach a public surface.

That leaves a real gap: on a public repo, an author has **no** way to trace
an open PR back to the worktree that opened it. In practice this shows up as
PRs that stall in review with no way to rehydrate the right context and push
them forward — and the absence of *any* marker has already let one leak slip
through a different path: a PR opened with `head_scheme: refspec` published
its head straight from `worktree/<id>`, putting the raw worktree id (and an
embedded timestamp) directly into a public branch name. That is exactly the
class of exposure `source_attribution: false` is meant to prevent, arriving
by a route the flag doesn't cover.

The fix is a form of attribution that is **informationless to an outside
reader** but a valid lookup key for the author: a random, themed codename
with no encoded machine, date, or sequence data — assigned once per worktree
and carried as the *only* thing a public marker (or, worse, a leaked branch
name) ever exposes.

## Request

> Design a codename system for worktrees so PR attribution can be posted
> publicly without exposing which machine or worktree actually produced the
> change, while still letting the author look a codename back up to resume
> the right context.

## Plan

### Phase 1 — Neutral codename generation (agent-worktrees-owned)
- [ ] Add a small, dependency-free "handle" generator to `agent-worktrees`
  itself: 1–2 word, lowercase, hyphen-joined, branch/filename-safe, backed
  by a **generic, organization-neutral** word list (no product theming —
  this plugin is general-purpose; themed vocabularies are an adopter-side
  concern, not a plugin default).
- [ ] Support an optional **external generator hook**: a config value naming
  a shell command that prints one handle to stdout. When set, `create` shells
  out to it instead of the built-in generator. This lets a private control
  repo plug in its own themed generator without that vocabulary ever living
  in this public plugin.
- [ ] Collision-avoid against the local tracking store (retry on collision,
  same spirit as the existing registry-checked mode of comparable
  generators) — no new persistence primitive.

### Phase 2 — Per-worktree codename assignment + local lookup
- [ ] Assign one codename per worktree at `create` time; store it on the
  worktree's local tracking record next to `id`.
- [ ] `list`/`resolve` accept `--codename <name>` as an alternate selector
  alongside the existing `--worktree-id`.
- [ ] `status`/picker surfaces the codename so an author can correlate a
  public PR's codename back to a visible worktree without extra lookup
  steps.

### Phase 3 — Cross-machine reverse lookup
- [ ] Mirror `codename -> {machine, worktree_id}` into the existing
  cross-machine discovery/claims store (the same plumbing `claims
  mirror-status` already writes), so a codename resolves from any machine.
- [ ] `embody --codename <name>` resolves and rehydrates the right worktree
  regardless of which machine currently holds it.

### Phase 4 — `source_attribution: codename` mode
- [ ] Extend `source_attribution` from boolean to accept `true | false |
  codename`. `codename` emits a hidden PR-body marker carrying **only** the
  codename — no machine, worktree id, session id, or timestamp.
- [ ] Document the three modes and when each is appropriate (private
  closed-circuit repo vs. public repo that still wants author-side
  traceability vs. fully anonymous).

### Phase 5 — Close the branch-name leak class
- [ ] When `source_attribution` is not `true`, default `head_scheme` to
  `snapshot` (the scheme that pushes a generated `pr/<slug>` branch, never
  the raw `worktree/<id>` branch) so a worktree identifier can never again
  ride onto a public PR head ref via the `refspec` scheme.
- [ ] Audit existing repo configs for `source_attribution: false` +
  `head_scheme: refspec` combinations and flag/migrate them.

## Validation Plan

- [ ] Unit tests: handle generator format (lowercase, hyphen-joined,
  branch-safe), collision retry, external-hook invocation and fallback.
- [ ] Unit tests: `source_attribution: codename` marker contains the
  codename and *no* machine/worktree/session/timestamp substrings.
- [ ] Integration test: `create` → codename assigned and persisted →
  `resolve --codename` / `embody --codename` round-trip on the same
  machine.
- [ ] Integration test: cross-machine mirror write + read (or a mocked
  discovery store) resolves a codename created on machine A from machine B.
- [ ] Regression: existing `source_attribution: true`/`false` behavior on
  private repos is unchanged (no marker content or format change for those
  modes).
- [ ] Config validation: `head_scheme: refspec` + `source_attribution` other
  than `true` is rejected or warned at config-load time, not silently
  allowed.

## Proposal

_Pending review._

## Journal

### 2026-09-17 — Kickoff
- Effort created from a sweep of stalled PRs on this repo: none carried any
  attribution marker, and one leaked a raw `worktree/<id>` branch name as its
  PR head. Filed as issue #2838; this effort captures the phased design.
