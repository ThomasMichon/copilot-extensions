# PR Attribution Codenames

- **Slug:** `pr-attribution-codenames`
- **Repo:** copilot-extensions (agent-worktrees plugin)
- **Branch(es):** `pr/<slug>` per phase
- **Created:** 2026-09-17
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
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
through a different path: a PR was observed with its head published as the
literal `worktree/<id>` branch name, putting the raw worktree id (and an
embedded timestamp) directly into a public branch name. That is exactly the
class of exposure `source_attribution: false` is meant to prevent, arriving
by a route the flag doesn't cover.

**Correcting this effort's first-draft mechanics** (per review): the two
`head_scheme` values do not work the way the first draft described.
`refspec` (the default) keeps the worktree on its local `worktree/<id>`
branch and pushes it via an explicit git refspec straight to a remote
`pr/<slug>-<suffix>` ref — the local `worktree/<id>` name is never itself
the published head. `snapshot` instead creates and pushes a local
`feature/<slug>-<suffix>` branch. Neither default path publishes a raw
`worktree/<id>` name; the observed leak happened through an **override** —
an explicit `--branch`, an existing-PR reuse, or a `head_pattern` containing
`{machine}`/`{worktree_id}` — not through `head_scheme` selection itself.
Phase 5 below is corrected to close the actual leak surface (the effective
published ref, however selected), not a mischaracterized scheme default.

**Threat model — accepted linkability, not full anonymity.** A persistent
per-worktree codename is public-safe against decoding (no machine, date, or
sequence information), but it is **not** unlinkable: the same codename
recurring across multiple public PRs/branches lets an outside reader
correlate them as coming from one actor over time, even without knowing who
or where that actor is. This effort accepts that tradeoff deliberately —
the goal is *decoding* prevention (machine/worktree/session/timestamp never
recoverable), not *correlation* prevention. A rotating/per-PR codename would
close the correlation gap but breaks author-side lookup utility (the
"nudge a stalled worktree" use case), which is the whole point; it is noted
as a possible future variant, not a Phase 1–5 requirement.

The fix is a form of attribution that is **informationless to an outside
reader about machine/worktree/session/timestamp** but a valid lookup key for
the author: a random, themed codename with no encoded machine, date, or
sequence data — assigned once per worktree and carried as the *only* thing a
public marker (or, worse, a leaked branch name) ever exposes.

**Design pivot: declarative wordlist file, not an executable hook.** The
first implementation attempt let an adopter configure an external *shell
command* hook to supply a themed vocabulary. That single design choice
required ten review rounds of subprocess-safety hardening (process-tree
cleanup on timeout, cross-platform kill semantics, bounded-memory reads,
truncation detection, timeout-value validation, encoding failures, a
reap-after-kill race) — none of it related to codenames at all, just the
generic cost of "run an adopter-supplied command with a timeout." The
operator's own steer settled it: this is **purely declarative data** —
words and, optionally, the permitted combinations between them — so Phase 1
now reads a JSON/YAML file instead of running anything. That removes the
entire subprocess-safety surface by construction: no timeout, no process to
kill, no encoding-from-a-subprocess concern, no zombie/leak risk.

## Request

> Design a codename system for worktrees so PR attribution can be posted
> publicly without exposing which machine or worktree actually produced the
> change, while still letting the author look a codename back up to resume
> the right context.
>
> (Follow-up direction, after the hook-based Phase 1 draft hit repeated
> subprocess-safety findings: "Can we make it purely declarative? No need to
> call out to a hook, just need a ref to a yaml or json full of words and
> permitted relationships.")

## Plan

### Phase 1 — Neutral codename generation (agent-worktrees-owned)
- [x] Add a small, dependency-free "handle" generator to `agent-worktrees`
  itself: 1–2 word, lowercase, hyphen-joined, branch/filename-safe, backed
  by a **generic, organization-neutral** word list (no product theming —
  this plugin is general-purpose; themed vocabularies are an adopter-side
  concern, not a plugin default). Landed in `agent_worktrees.codename`
  (mechanical/workshop-themed word list, `generate_handle`,
  `is_valid_handle`).
- [x] Support an optional **declarative wordlist file**: a config value
  (`codename.wordlist_path`) naming a JSON or YAML file the adopter
  maintains. This lets a private control repo plug in its own themed
  vocabulary (nouns, optionally adjectives, optionally an explicit list of
  *permitted* adjective-noun pairings to avoid unwanted combinations)
  without that vocabulary ever living in this public plugin — with **no
  executable-hook surface at all**. Landed in
  `agent_worktrees.codename.load_wordlist`/`load_wordlist_or_default`
  (strict structural validation: required non-empty `nouns`, optional
  `adjectives`, optional `pairs` overriding the full cross-product; every
  word individually validated lowercase-alnum, bounded length, no hyphens
  of its own) and `agent_worktrees.codename_config.CodenameConfig`/
  `parse_codename`, wired into `RepoConfig`/`config_dropins` alongside the
  existing `pr:` block. A missing/malformed file fails soft to the
  built-in wordlist (`load_wordlist_or_default`) rather than blocking
  worktree creation over a data-file typo.
- [x] Collision-avoid against the local tracking store (retry on collision,
  same spirit as the existing registry-checked mode of comparable
  generators) — no new persistence primitive for the *local* check (Phase 3
  covers cross-machine uniqueness, which this local check cannot guarantee
  alone). Landed as `agent_worktrees.codename.assign_codename(existing,
  ...)`, generic over any iterable of already-used handles and an optional
  `Wordlist`; Phase 2 wires it to the actual tracking-store read.

### Phase 2 — Per-worktree codename assignment + local lookup
- [ ] Assign one codename per worktree at `create` time; store it on the
  worktree's local tracking record next to `id`.
- [ ] **Backfill path:** a worktree record created before this feature (or
  before a repo opts into `source_attribution: codename`) has no codename.
  Add a lazy-assignment path — the first operation that needs one (`create-pr`
  under `codename` mode, or an explicit `resolve`/`status` touch) allocates
  and persists it then, rather than requiring a bulk migration or leaving
  `create-pr` with nothing to emit. Test the upgrade case explicitly: an
  old-format record, `source_attribution: codename` newly enabled, first
  `create-pr` call.
- [ ] `list`/`resolve` accept `--codename <name>` as an alternate selector
  alongside the existing `--worktree-id`.
- [ ] `status`/picker surfaces the codename so an author can correlate a
  public PR's codename back to a visible worktree without extra lookup
  steps.

### Phase 3 — Cross-machine reverse lookup
- [ ] Define a **typed codename registry** — lookup, atomic reservation, and
  retirement semantics — as its own small store. The existing `claims
  mirror-status` plumbing mirrors a claim's *disposition* into a lease; it
  has no key/value lookup or reservation schema, so it is not sufficient
  as-is. Either extend that store with a genuine reservation primitive or
  build a small dedicated one; do not assume the existing plumbing already
  provides this.
- [ ] **Atomic cross-machine reservation, not just a local check.** Phase 1's
  local-store collision check cannot prevent two machines allocating the
  same handle concurrently. A codename must be reserved through the shared
  registry (test-and-set or equivalent) before it is considered assigned,
  so a later cross-machine lookup always resolves to exactly one worktree.
- [ ] The mirrored value must carry enough to actually resolve remotely:
  `{machine, project/repo, worktree_id}` at minimum — `embody` operates
  against a specific project's tracking directory
  (`cfg.tracking_dir()/...`), so a bare `{machine, worktree_id}` pair omits
  the project needed to locate the record at all. Where the codename's
  worktree lives on a **different** machine than the one doing the lookup,
  `embody --codename <name>` cannot start a local mux directly — it must
  either delegate through the facility's existing remote handoff/dispatch
  path (agent-bridge) to that machine, or fail closed with a clear
  "resolve on machine X" message. Define which explicitly; do not leave it
  implicit.

### Phase 4 — `source_attribution: codename` mode
- [ ] Extend `source_attribution` from boolean to accept `true | false |
  codename`. `codename` emits a hidden PR-body marker carrying **only** the
  codename — no machine, worktree id, session id, or timestamp.
- [ ] **Cover both marker-writing paths, not just PR creation.** There are
  two places a marker is written: the initial `create-pr` body, and
  `refresh_source_attribution` (used when a later push updates an existing
  PR's head). Both must honor `codename` mode identically — an
  implementation that only updates the initial-body path would leave the
  refresh path free to write the full raw marker on the very next push,
  reintroducing the leak on an already-open PR. Test both paths under
  `codename` mode, not just PR creation.
- [ ] Document the three modes and when each is appropriate (private
  closed-circuit repo vs. public repo that still wants author-side
  traceability vs. fully anonymous).

### Phase 5 — Close the branch-name leak class
- [ ] **Validate the effective published ref, not just the `head_scheme`
  default.** Per the corrected Context above, neither `head_scheme` default
  publishes a raw `worktree/<id>` name — the observed leak came from an
  override (explicit `--branch`, existing-PR reuse, or a `head_pattern`
  containing `{machine}`/`{worktree_id}`). The real fix is to validate the
  **effective remote head** at the publish boundary: whatever `--branch`/
  `head_pattern`/existing-PR-reuse resolves to, when `source_attribution`
  isn't `true`, must not contain the raw worktree id, machine name, or a
  literal `{machine}`/`{worktree_id}` substitution. Reject at publish time
  if it does.
- [ ] **Hard error, not a warning.** A warning still lets the push proceed
  with an identifying ref. When `source_attribution` isn't `true` and the
  effective head would carry a private identifier, this must be a hard
  configuration/publish-time error that blocks the push — never a
  warn-and-continue.
- [ ] Audit existing repo configs for the actual risk surface: any repo
  where `source_attribution` is `false` **or absent** (it defaults to
  `false`, so an audit that only greps for the literal string `false` misses
  configs that omit the key entirely) combined with any path that could
  produce an identifying effective head (`head_scheme: refspec` with no
  further override, an explicit `--branch`, or a `head_pattern` using
  `{machine}`/`{worktree_id}`). Include the new `codename` mode as a target
  migration state in the same scan, not just a flag for `true`/`false`.

## Validation Plan

- [x] Unit tests: handle generator format (lowercase, hyphen-joined,
  branch-safe), collision retry, wordlist-file loading (valid YAML/JSON,
  missing file, malformed data, missing/empty/non-list/non-string/
  malformed-word `nouns`, explicit `pairs` overriding the cross-product,
  fail-soft fallback to the built-in wordlist on any load error). 52 tests
  landed in `test_codename.py`/`test_codename_config.py`.
- [ ] Unit tests: `source_attribution: codename` marker contains the
  codename and *no* machine/worktree/session/timestamp substrings, on
  **both** the initial `create-pr` body path and the
  `refresh_source_attribution` path.
- [ ] Integration test: `create` → codename assigned and persisted →
  `resolve --codename` / `embody --codename` round-trip on the same
  machine.
- [ ] Integration test: pre-existing (backfilled) worktree record with no
  codename → `source_attribution: codename` newly enabled → first
  `create-pr` allocates and persists a codename correctly.
- [ ] Integration test: concurrent codename reservation from two machines
  for the same generated candidate resolves to exactly one owner (the
  other retries/gets a different handle) — proves the atomic reservation,
  not just the local collision check.
- [ ] Integration test: cross-machine mirror write + read (or a mocked
  discovery store) resolves a codename created on machine A from machine B,
  including the qualified project reference needed to actually locate the
  record.
- [ ] Regression: existing `source_attribution: true`/`false` behavior on
  private repos is unchanged (no marker content or format change for those
  modes).
- [ ] Config/publish-time validation: when `source_attribution` isn't
  `true`, an effective published head containing the raw worktree id,
  machine name, or an unsubstituted `{machine}`/`{worktree_id}` pattern is a
  **hard error that blocks the push** (not a warning) — cover the default
  path, an explicit `--branch` override, and a `head_pattern` override.
- [ ] Migration-audit test: a repo config with `source_attribution` entirely
  absent (not just explicit `false`) is correctly flagged by the audit
  tooling from Phase 5.

## Proposal

_Pending review._

## Journal

### 2026-09-17 — Kickoff
- Effort created from a sweep of stalled PRs on this repo: none carried any
  attribution marker, and one leaked a raw `worktree/<id>` branch name as its
  PR head. Filed as issue #2838; this effort captures the phased design.

### 2026-09-17 — Review round: corrected mechanics + closed 12 design gaps
- Automated review (12 findings: 4 high / 6 medium / 2 low) caught that the
  first draft mischaracterized `head_scheme`'s actual behavior (neither
  `refspec` nor `snapshot` publishes a raw `worktree/<id>` name by default —
  the observed leak came from an override) and left several real gaps:
  codename linkability wasn't named as an accepted tradeoff; no backfill
  path for pre-existing worktree records; the external generator hook had
  no execution bounds or fail-closed behavior; the cross-machine registry
  was assumed reusable from `claims mirror-status` without checking it
  actually supports lookup/reservation; no atomic reservation to prevent
  two machines allocating the same handle; the mirrored value omitted the
  project reference `embody` actually needs; the marker-refresh path
  (`refresh_source_attribution`) wasn't covered alongside PR creation; and
  the leak-closing validation allowed a warning instead of a hard error.
- Revised Context (corrected `head_scheme` mechanics + explicit threat-model
  note) and every affected Plan phase; expanded the Validation Plan to
  match. No phase count changed, but Phases 1, 2, 3, 4, and 5 all gained
  concrete requirements they previously lacked.

### 2026-09-17 — Review round 2: hook-guarantee scoping fix
- Follow-up review (2 findings) caught one remaining real gap: syntax
  format-validation on external generator hook output does not prove the
  output is *semantically* non-identifying (a hook could emit a
  syntactically valid but identifying handle, e.g. `machine-20260917`).
  Scoped Phase 1's public-safety guarantee to hold unconditionally only for
  the built-in generator, and made using a non-default hook under
  `source_attribution: codename` an explicit, warned trust decision on the
  hook owner rather than an implicit guarantee. (The review's second
  finding — a missing Documentation impact statement — was a timing
  artifact: the PR body was updated with that statement in the same push
  cycle the review ran against, just after the review started; the live PR
  body already carries it.)

### 2026-09-18 — Design pivot: dropped the hook, went purely declarative
- The hook-based Phase 1 implementation went through **ten** review rounds,
  each closing a genuine subprocess-safety bug: process-group isolation on
  timeout, bounded-memory reads, truncation-then-strip validation, a
  Windows headless-launch guard violation, timeout-value validation
  (non-finite/oversized/boolean), a non-string `hook_command` becoming a
  real shell command via `str(None) == "None"`, a Windows-specific
  `taskkill` console-window leak, missing test coverage (non-UTF-8 output,
  the actual `load_config` wiring), and — twice — the process-group cleanup
  itself: first not sweeping a backgrounded descendant when the hook shell
  exited successfully rather than timing out, then a subtler bug in *that*
  very fix (`os.getpgid(pid)` fails once the process is already reaped, so
  the "fix" silently no-op'd on exactly the case it targeted).
- None of those ten findings were about codenames — every one was the
  generic cost of "run an adopter-supplied shell command with a timeout."
  Raised to the operator mid-review; the answer was direct: make it purely
  declarative. **Reset the branch to `main`** (discarding all ten
  hook-hardening commits — they were fixing a mechanism this pivot
  removes entirely, not preserving anything worth rebasing forward) and
  reimplemented Phase 1 around a JSON/YAML **wordlist file** instead of an
  executable hook: `codename.wordlist_path` names a file declaring `nouns`
  (required), optional `adjectives`, and an optional explicit `pairs` list
  for an adopter who wants to declare *permitted relationships* rather than
  a full cross-product. No subprocess, no timeout, no process to kill, no
  encoding-from-a-subprocess concern — the entire ten-round problem class
  is structurally impossible now, not just hardened against.
- Landed in one clean commit: `agent_worktrees.codename`
  (`Wordlist`/`load_wordlist`/`load_wordlist_or_default`/`generate_handle`/
  `assign_codename`) and `agent_worktrees.codename_config`
  (`CodenameConfig`/`parse_codename`), wired into `RepoConfig`/
  `config_dropins` exactly as the hook version was. 52 tests (down from the
  hook version's 65 — no subprocess tests needed), and markedly faster
  (~3s vs ~8s, no process spawning).
