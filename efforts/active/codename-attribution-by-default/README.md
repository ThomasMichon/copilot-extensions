# Codename Attribution By Default

- **Slug:** `codename-attribution-by-default`
- **Repo:** copilot-extensions
- **Branch(es):** `pr/<slug>` per phase
- **Created:** 2026-09-20
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** vision-extending — flips the *policy default* for the
  `source_attribution` capability landed by `pr-attribution-codenames`
  (Done): public-safe traceability becomes the out-of-the-box behavior
  instead of an opt-in nobody actually opts into.
- **Umbrella issue:** [#2977](https://github.com/ThomasMichon/copilot-extensions/issues/2977)

## Guiding Intent

Close the gap `pr-attribution-codenames` left open: the codename feature
works end-to-end (proven live against real merged PRs — see Context), but
it requires an explicit `source_attribution: codename` opt-in, and in
practice **no repo has opted in**. `copilot-extensions` — the very repo the
feature was built in — still explicitly sets `source_attribution: false`,
so its own real PRs (including the two that built this feature, #2915 and
#2922) carry **zero** attribution marker at all. Flip the policy so
public-safe codename attribution is the default everywhere, and reserve the
full raw marker (`source_attribution: true`) for repos that have explicitly
decided they want it (private/closed-circuit ones, e.g. the facility's
internal Gitea instance).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| lambda-core (agent-worktrees maintainer) | design + implementation + facility rollout | this worktree |

## Coordination

- **Topology:** independent per-phase PRs (small, reviewable increments),
  matching `pr-attribution-codenames`'s own topology
- **Host (owns PRs):** this worktree/author
- **Delegates:** none
- **Handoff:** n/a

## Context

### What already exists (`pr-attribution-codenames`, Done)

`pr.source_attribution` accepts three values, parsed in
`agent_worktrees.config._parse_pr`/`_source_attribution` and consumed by
`pr_ops.py`/`providers/attribution.py`:

- `false` (current default) — no marker at all.
- `true` — the full raw marker (worktree id, machine, session, head SHA);
  closed-circuit repos only.
- `"codename"` — a public-safe marker carrying **only** the worktree's
  assigned codename (see `agent_worktrees.codename`); resolves back to a
  worktree via `resolve --codename` / `embody --codename`, including an
  automated cross-machine SSH scan (Phase 3, just landed) when the
  codename's worktree lives on a different machine.

### The gap this effort closes

Verified live, this session, against real merged PRs:

- **`copilot-extensions`** (`.agent-worktrees/config.yaml`) explicitly sets
  `source_attribution: false`. PRs #2915 and #2922 — the very PRs that
  built Phases 4 and 5 of the codename feature — carry **no marker
  whatsoever**, raw or codename. The feature exists but is never used on
  its own home repo.
- **`pr-practice-lab`** (public, GitHub) has `pr.enabled: true` and no
  `source_attribution` key at all — silently inherits the `false` default,
  same gap.
- **`aperture-labs`** (private, facility Gitea) explicitly sets
  `source_attribution: true` and DOES carry the raw marker on every recent
  merge (#7223–#7229 all verified to carry
  `<!-- agent-worktrees:source worktree=... machine=... session=... head=... -->`).
  This is correct today and should stay `true` — it's a private,
  closed-circuit repo, so full traceability is the right call, not a gap.
- **`test-chambers`** and **`r60v-home-assistant`** have no `pr:` block
  configured at all (still using direct-push finalize) — out of scope,
  no PRs are opened there via agent-worktrees today.
- **`llama.cpp`**, **`piper`**, **`borealis-bazzite`** are upstream
  reference/singleton repos (no agent-owned PR flow) — out of scope.

So the actual repo-config rollout surface, as of this writing, is exactly
two repos: `copilot-extensions` (flip from explicit `false`) and
`pr-practice-lab` (currently implicit `false` via the absent-key default).

### The design decision this effort must resolve

`source_attribution`'s effective value comes from two independent places
that must be reconciled, not just one:

1. **The config-parsing fallback** — `_parse_pr`'s
   `raw.get("source_attribution", False)` when the `pr:` block IS present
   but the key is omitted.
2. **The `PRConfig` dataclass field default** — `source_attribution: SourceAttribution = False`
   — which is what a repo gets when `_parse_pr`'s OWN early return fires
   (`if not isinstance(raw, dict): return PRConfig()`, i.e. the whole `pr:`
   block is absent) **and** what any test or caller gets from a bare
   `PRConfig()` construction.

Both must move together to `"codename"` or the two paths silently diverge
(a repo with an explicit-but-key-omitted `pr:` block would get one
behavior, a repo with no `pr:` block at all would get another) — this is
the same class of bug `pr-attribution-codenames` Phase 5 review round 4
caught for `source_attribution_configured`. `source_attribution_configured`
itself (added in that phase, tracks whether the raw key was literally
present) is independent of this and needs no change — it stays `False` by
dataclass default and is set `True` only when `_parse_pr` sees the actual
key, exactly as today.

**Also needs resolution:** every existing test that constructs a bare
`PRConfig()` and asserts `.source_attribution is False` (there are several
across `test_config.py`, `test_pr_ops.py`, `test_providers.py`) will need
updating to assert `"codename"` instead — this is a real, wide-reaching
test-suite touch, not a one-line change. Audit the full set before
starting Phase 1.

### Downstream effects to re-examine, not just the default itself

- **`attribution-audit` / `audit_source_attribution_risk`** (Phase 5) was
  built around "the default (`false`) is safe; only `true`-with-a-risky-
  `head_pattern` or an explicit leak is the danger." Its finding text for
  an absent key currently says `"absent (defaults to false)"` — this
  becomes **factually wrong** once the default flips, and must be
  corrected. Re-examine whether the audit's core risk model (branch-name
  leak class, Phase 5) still needs to flag an absent key at all once the
  new default is inherently safe by construction (codename mode can still
  leak via a risky `head_pattern` embedding `{machine}` in the BRANCH
  NAME, which is a separate, still-live risk from the *marker* choice —
  the audit's `head_pattern` check is unaffected by this policy change and
  should keep firing regardless of `source_attribution`'s value).
- **Docs** (`docs/config-reference.md`, `skills/worktree/references/pr-workflow.md`,
  `providers/attribution.py`'s module docstring, `docs/cli-reference.md`)
  all currently describe `false` as "the (safe) default" and `codename`/
  `true` as opt-in — every one of these needs the default's identity
  corrected.
- **Migration guidance**: any repo that explicitly sets `source_attribution: false`
  today (i.e. `copilot-extensions`) is making an **active, intentional**
  choice under the OLD default's framing ("public repos should set this
  false"). After the flip, an explicit `false` is no longer "the safe
  default restated" — it becomes a genuine **opt-out of traceability
  entirely**, which is a different, stronger statement. Each repo's
  explicit `false` (if any remain after rollout) should be re-reviewed for
  whether that's still actually wanted, not just left in place by inertia.

## Request

> [Operator, verbatim, following a live demonstration this session that
> `copilot-extensions`' own PRs carry zero attribution marker despite the
> codename feature being fully built and tested]: "Agh, that was the point
> of this worktree. In a handoff, let's tackle making the default behavior
> to be to use codeword attribution, with 'real' attribution being reserved
> for select repos only, like aperture-labs or our Gitea set."

## Plan

### Phase 1 — Flip the default, reconcile both code paths
- [ ] Change `_parse_pr`'s fallback: `raw.get("source_attribution", False)` →
  `raw.get("source_attribution", "codename")`.
- [ ] Change `PRConfig.source_attribution`'s dataclass default from `False`
  to `"codename"` (the missing-`pr`-block early return in `_parse_pr` and
  any bare `PRConfig()` construction must match the parsed-default path —
  see Context's "design decision" above; do not change only one of the two
  places).
- [ ] Audit and update every test that constructs a bare `PRConfig()` (or
  relies on the parsed absent-key default) and currently asserts
  `.source_attribution is False` — there are multiple across
  `test_config.py`/`test_pr_ops.py`/`test_providers.py`. Update each to the
  new default explicitly, with a comment noting WHY (this effort), so a
  future reader doesn't mistake it for an unrelated regression.
- [ ] Verify `source_attribution_configured` behavior is unaffected (still
  `False` by dataclass default, `True` only when the raw key is literally
  present) — no code change expected here, but add a regression test
  proving the two fields don't drift together.

### Phase 2 — Correct downstream messaging that assumed the old default
- [ ] Update `attribution-audit`/`audit_source_attribution_risk`'s finding
  text: `"absent (defaults to false)"` is now wrong; correct it to
  `"absent (defaults to codename)"` (still worth flagging when combined
  with a risky `head_pattern` — codename mode is public-safe for the
  *marker*, but a leaking *branch name* is a separate risk the audit
  correctly still checks regardless of marker mode).
- [ ] Re-examine whether an absent-key repo with a SAFE `head_pattern`
  should be flagged by the audit at all now that its default is inherently
  safe — decide explicitly and document the reasoning either way.
- [ ] Update `docs/config-reference.md`, `docs/cli-reference.md`,
  `skills/worktree/references/pr-workflow.md`, and
  `providers/attribution.py`'s module docstring: all describe `false` as
  "the (safe) default" today; correct every instance to describe
  `"codename"` as the default and `false`/`true` as the two opt-out
  directions (fully anonymous / fully raw).

### Phase 3 — Facility config rollout
- [ ] `copilot-extensions`: remove (or flip to `codename`, for
  explicitness) the explicit `source_attribution: false` in
  `.agent-worktrees/config.yaml` — this repo is the one that most visibly
  motivated this effort.
- [ ] `pr-practice-lab`: decide explicitly whether to rely on the new
  implicit default or set `source_attribution: codename` explicitly for
  clarity (this repo currently has no `source_attribution` key at all).
- [ ] `aperture-labs`: confirm its existing explicit `source_attribution: true`
  is untouched by this effort (already correct: private, closed-circuit,
  wants full traceability) — add a comment there (if not already present)
  noting it's an intentional opt-out from the new default, not a leftover.
- [ ] Sweep for any OTHER private/closed-circuit repo on the facility's
  Gitea instance (`gitea.michon.ski`) that has `pr.enabled: true` and would
  want the same `true` treatment as `aperture-labs` — as of this writing
  `test-chambers` has no `pr:` block at all (direct-push only, out of
  scope), but re-check at execution time in case that's changed.

### Phase 4 — Live validation
- [ ] Open a real PR in `copilot-extensions` after Phase 3's config change
  and confirm it now carries the `<!-- agent-worktrees:source codename=... -->`
  marker (this effort's own landing PR is a natural candidate).
- [ ] Confirm `aperture-labs`'s next merged PR still carries the full raw
  marker unchanged (regression check, not a new test — just observe the
  next real merge).

## Validation Plan

- [ ] Unit: `_parse_pr` with an absent `source_attribution` key (both
  "`pr:` block present, key omitted" and "`pr:` block entirely absent")
  parses to `"codename"`, not `False`.
- [ ] Unit: `_parse_pr` with an explicit `source_attribution: false` still
  parses to `False` (opt-out remains available and unchanged).
- [ ] Unit: `_parse_pr` with an explicit `source_attribution: true` still
  parses to `True` (closed-circuit opt-in remains unchanged).
- [ ] Regression: `source_attribution_configured` is `False` for every
  absent-key case above and `True` for every explicit case (both `false`
  and `true` count as "configured"), proving the two fields never drift.
- [ ] Unit: `audit_source_attribution_risk`'s finding text for an absent
  key says "codename," not "false."
- [ ] Full existing `test_config.py`/`test_pr_ops.py`/`test_providers.py`
  suite passes after the default-value test-fixture sweep (Phase 1's audit
  item) — no test silently still asserts the old default.
- [ ] Live: this effort's own landing PR in `copilot-extensions` (opened
  after Phase 3's config change lands) carries a codename marker — the
  end-to-end proof the whole point of this effort actually works.
- [ ] Live (regression, observational): the next `aperture-labs` merge
  after this effort lands still carries the full raw marker unchanged.

## Proposal

_Pending._

## Journal

### 2026-09-20 — Kickoff

- Effort created directly from a live demonstration: checked
  `copilot-extensions`' own recent PRs (#2915, #2922 — the PRs that BUILT
  the codename feature) and found neither carries any attribution marker,
  because the repo's config explicitly opts out (`source_attribution: false`,
  which was also the pre-existing global default). Cross-checked
  `aperture-labs` (private, Gitea) and confirmed its last 5 merges (#7223–
  #7229) all correctly carry the full raw marker, proving the mechanism
  itself works — the gap is purely a policy/default problem, not a broken
  feature.
- Enumerated the full facility repo registry (`~/.agent-worktrees/repos.yaml`):
  of 8 registered repos, only `copilot-extensions` and `pr-practice-lab`
  have PR mode enabled AND are public without an explicit
  `source_attribution` opt-in today; `aperture-labs` is private and already
  correctly opted into `true`; the remaining four (`borealis-bazzite`,
  `llama.cpp`, `piper`, `test-chambers`, `r60v-home-assistant`) either have
  no PR-mode config or are upstream reference/singleton repos out of scope.
- Closed the predecessor effort's umbrella issue (`pr-attribution-codenames`,
  #2838 — Done, all 5 phases merged) and opened this effort's own umbrella
  issue (#2977).
- Handed off for execution: this effort's plan has not yet been reviewed
  (per `planning-efforts`' review gate, submit this README as a PR and let
  the repo's non-blocking automated review clear it before starting Phase 1).
