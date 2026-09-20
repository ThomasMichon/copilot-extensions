# Codename Attribution By Default

- **Slug:** `codename-attribution-by-default`
- **Repo:** copilot-extensions
- **Branch(es):** `pr/<slug>` per phase
- **Created:** 2026-09-20
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** below-altitude — no `visions/` item governs the specific
  default value of the `source_attribution` marker mode. The nearest
  candidates (`visions/agent-fabric`'s "worktree identity" section,
  `visions/plugins/agent-worktrees/pull-requests`'s PR-capability vision)
  describe the mechanism's existence and shape, not this policy/default
  choice within it; this effort changes a configuration default on an
  already-vision-covered capability (`pr-attribution-codenames`, Done),
  not new architecture. Proceeding without a vision revision per the
  below-altitude path.
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
decided they want it (private/closed-circuit repos, not open ones).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions maintainer(s) | design + implementation + repo rollout | this worktree |

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

- **This repo** (`copilot-extensions`, `.agent-worktrees/config.yaml`)
  explicitly sets `source_attribution: false`. Its own recent PRs that
  built Phases 4 and 5 of the codename feature carry **no marker
  whatsoever**, raw or codename. The feature exists but is never used on
  its own home repo.
- A private, closed-circuit downstream repo with `source_attribution: true`
  explicitly set DOES correctly carry the full raw marker
  (`<!-- agent-worktrees:source worktree=... machine=... session=... head=... -->`)
  on its recent merges, proving the mechanism itself is sound — this is
  purely a default/policy gap, not a broken feature.

Which specific repos are in scope for a config rollout, and how many, is
downstream-private operational detail that does not belong in this public
effort record (and would go stale here regardless) — that inventory and
rollout order live in the driver's own private planning. This effort's
public scope is the mechanism: the code-level default and its downstream
consumers (Phases 1–2 below); Phase 3 is a generic per-repo checklist any
driver can apply to their own fleet without this doc naming it.

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

### Custom wordlists are a separate, independently-configured risk surface

`codename.wordlist_path` (`wordlist_for_repo`/`CodenameConfig`) lets a repo
supply its own themed vocabulary for generated codenames, entirely
independent of `pr.source_attribution` — a repo might configure a custom
wordlist purely for local ergonomics (memorable names in the Picker UI),
with no intention of ever publishing them. Once `codename` becomes the
*default* `source_attribution` value, a repo that has a custom wordlist
configured but has never touched `source_attribution` would silently start
publishing codenames DRAWN FROM THAT WORDLIST in public PR markers —
defeating the "informationless to an outside reader" property the whole
codename feature exists to provide, if that custom vocabulary happens to
contain identifying or private terms. This must be resolved explicitly
before Phase 1 ships, not left implicit: options include forcing the
built-in neutral wordlist whenever `source_attribution` is at its DEFAULT
value (only honoring a custom wordlist when `codename` is explicitly
configured), or otherwise gating a custom wordlist's use behind an
explicit acknowledgment. Do not ship the default flip without picking one.

**A future allocation policy alone is not sufficient — pre-existing
assignments are a separate migration gap.** `WorktreeRecord` persists only
the final codename string, not which wordlist (built-in or custom)
produced it or when. A worktree created BEFORE this effort ships, under a
repo with a custom wordlist configured, may already carry a codename drawn
from that custom vocabulary in its tracking record. Changing only *future*
allocation (the item above) does nothing for that already-assigned
codename: the moment `source_attribution` starts defaulting to
`codename`, the very next `create-pr`/`refresh_source_attribution` call on
that pre-existing worktree would publish its already-assigned,
possibly-identifying codename, with no new allocation happening at all to
catch. This needs its own explicit migration/suppression decision (e.g.
detecting a pre-existing codename can't be proven safe and suppressing
publication for it, or another resolution) — do not assume the
forced-wordlist fix for new allocations also covers this case.

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
> this repo's own PRs carry zero attribution marker despite the codename
> feature being fully built and tested]: "Agh, that was the point of this
> worktree. In a handoff, let's tackle making the default behavior to be
> to use codeword attribution, with 'real' attribution being reserved for
> select repos only, like [a private downstream repo] or [this operator's]
> Gitea set."

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
- [ ] **Resolve the custom-wordlist risk** identified in Context before
  shipping this phase: decide and implement whether the DEFAULT
  `source_attribution` value forces the built-in neutral wordlist
  (ignoring any configured `codename.wordlist_path`) regardless of what
  `wordlist_for_repo` would otherwise resolve, honoring a custom wordlist
  only when `source_attribution: codename` is explicitly configured — or
  an equivalent explicit gate. Add a test proving a repo with BOTH a
  custom `codename.wordlist_path` AND an absent/default
  `source_attribution` never publishes a codename drawn from that custom
  list.
- [ ] **Resolve the pre-existing-codename migration gap** identified in
  Context: a forced-wordlist policy only affects NEW allocations, not a
  codename already persisted on a `WorktreeRecord` before this effort
  shipped. Decide and implement an explicit migration/suppression policy
  for a worktree whose codename cannot be proven to have come from the
  built-in wordlist (e.g. suppress publication for it, force a
  re-backfill, or another resolution) — do not assume Phase 1's
  future-allocation fix silently also covers this case. Add a regression
  test using a pre-existing record with a custom-wordlist-shaped codename
  already assigned.
- [ ] **Versioning gate (required for this phase's PR):** this phase
  changes `agent-worktrees` runtime source (`config.py`). Per
  `AGENTS.md`'s Version Bump section, bump `plugins/agent-worktrees/plugin.json`,
  `plugins/agent-worktrees/pyproject.toml`, the `agent-worktrees` entry in
  `.github/plugin/marketplace.json`, **and** that catalog's own top-level
  `metadata.version` (agent-worktrees changes bump both) — in the same
  commit as the code change, not a follow-up.

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
- [ ] Update the SOURCE-level comments/docstrings that make the same now-
  wrong claim, not just the standalone docs: `PRConfig`'s inline field
  comments in `config.py` describing `source_attribution`,
  `pr_ops.audit_attribution_risk`'s docstring,
  `providers/attribution.py`'s `audit_source_attribution_risk` docstring,
  and `pr_ops.create_pr`'s own docstring (currently says attribution must
  be explicitly enabled / is off by default) all currently say or imply
  `false` is the default -- an implementation that updates only the
  standalone docs would leave these behaviorally-adjacent comments
  actively misleading.
- [ ] Update this REPO'S OWN contributor/reviewer policy text, which
  currently instructs the OLD posture and would otherwise tell future
  contributors and automated reviewers to reject the very behavior this
  effort introduces: `AGENTS.md`'s "PR metadata is public too" bullet
  currently states "This repo keeps `pr.source_attribution: false`";
  `.github/copilot-instructions.md`'s "Public repo — stay
  identifier-neutral" bullet says the same; `REVIEW.md`'s "Identifier
  neutrality" bullet instructs the automated reviewer to "flag any attempt
  to enable `pr.source_attribution`" at all. Correct all three to
  distinguish the public-safe `codename` default (expected, not a
  violation) from the raw `true` mode (still correctly flagged for a
  public repo).
- [ ] **Versioning gate (required for this phase's PR):** this phase
  changes `agent-worktrees` runtime source (`providers/attribution.py`,
  `pr_ops.py`) even though most of the diff is documentation -- the same
  bump requirement as Phase 1 applies (`plugin.json`, `pyproject.toml`,
  the marketplace entry, and the catalog `metadata.version`).

### Phase 3 — Repo config rollout
- [ ] This repo (`copilot-extensions`): remove (or flip to `codename`, for
  explicitness) the explicit `source_attribution: false` in
  `.agent-worktrees/config.yaml` — this repo is the one that most visibly
  motivated this effort, and the only one this public doc names, since it
  is this same public repo.
- [ ] Any other repo relying on the codename feature: re-evaluate its
  `source_attribution` setting against the new default (a currently-absent
  key silently changes behavior; an explicit `false` becomes a genuine,
  stronger opt-out statement rather than "restating the old default" --
  see Context). This is generic guidance any driver applies to their own
  fleet; the specific inventory is out of scope for this public record.

### Phase 4 — Live validation
- [ ] Open a real PR in this repo after Phase 3's config change and confirm
  it now carries the `<!-- agent-worktrees:source codename=... -->` marker
  (this effort's own landing PR is a natural candidate).
- [ ] Confirm the private downstream repo's next merged PR still carries
  the full raw marker unchanged (regression check, not a new test — just
  observe the next real merge).

## Validation Plan

- [ ] Unit: a repo with a custom `codename.wordlist_path` configured AND an
  absent/default `source_attribution` never publishes a codename drawn
  from that custom wordlist (the resolved mechanism from Phase 1's
  wordlist-risk item) — the single highest-priority test in this effort,
  since it's the one silent-leak scenario the default flip could
  introduce.
- [ ] Unit/regression: a pre-existing `WorktreeRecord` created before this
  effort shipped, carrying a codename already assigned from a custom
  wordlist, is handled per the migration/suppression policy chosen in
  Phase 1 (e.g. publication suppressed, or re-backfilled) once
  `source_attribution` starts defaulting to `codename` — proving the
  future-allocation fix alone does not silently also cover this
  already-persisted case.
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
- [ ] **Integration (not just parser-level):** with `source_attribution`
  entirely omitted from a repo's config, `create_pr` actually publishes a
  codename marker (`_open_via_provider`'s marker-writing path, not just
  `_parse_pr`'s returned value) — a change that only touched the parser
  tests could otherwise leave `create_pr`'s old `false`-shaped behavior in
  place undetected. Update/extend the existing provider-fixture test that
  currently exercises this path with `source_attribution=False` explicitly
  set (`tests/test_providers.py`) to also cover the key OMITTED entirely,
  asserting a marker IS published; keep the explicit-`false` case (marker
  never published) as the still-required opt-out regression.
- [ ] Unit: `audit_source_attribution_risk`'s finding text for an absent
  key says "codename," not "false."
- [ ] Full existing `test_config.py`/`test_pr_ops.py`/`test_providers.py`
  suite passes after the default-value test-fixture sweep (Phase 1's audit
  item) — no test silently still asserts the old default.
- [ ] Live: this effort's own landing PR in this repo (opened after Phase
  3's config change lands) carries a codename marker — the end-to-end
  proof the whole point of this effort actually works.
- [ ] Live (regression, observational): the next merge on the private
  downstream repo after this effort lands still carries the full raw
  marker unchanged.

## Proposal

_Pending._

## Journal

### 2026-09-20 — Kickoff

- Effort created directly from a live demonstration: checked this repo's
  own recent PRs (#2915, #2922 — the PRs that BUILT the codename feature)
  and found neither carries any attribution marker, because the repo's
  config explicitly opts out (`source_attribution: false`, which was also
  the pre-existing global default). Cross-checked a private, closed-circuit
  downstream repo and confirmed its recent merges all correctly carry the
  full raw marker, proving the mechanism itself works — the gap is purely
  a policy/default problem, not a broken feature.
- Surveyed repos this codename feature already applies to and confirmed
  the pattern generalizes: some rely on the implicit `false` default with
  no attribution at all, while at least one private/closed-circuit repo
  already correctly uses `true`. Repo-specific inventory and rollout order
  are kept in private planning, not this public record (see Context).
- Closed the predecessor effort's umbrella issue (`pr-attribution-codenames`,
  #2838 — Done, all 5 phases merged) and opened this effort's own umbrella
  issue (#2977).
- Handed off for execution: this effort's plan has not yet been reviewed
  (per `planning-efforts`' review gate, submit this README as a PR and let
  the repo's non-blocking automated review clear it before starting Phase 1).

### 2026-09-20 — Plan-review round 6 fixes

- Automated review (PR #2978) flagged that `Vision: vision-extending` cited
  nothing. Surveyed `visions/plugins/agent-worktrees/README.md` and its
  `pull-requests/README.md` child vision; neither governs the specific
  *default value* of `source_attribution` — they describe the PR/codename
  mechanism's existence and shape, which `pr-attribution-codenames`
  (Done) already reconciled. Reclassified as **below-altitude**: this
  effort changes a configuration default on an already-vision-covered
  capability, not new architecture, so no vision revision is required.
- Also flagged: the custom-wordlist risk (Context, Phase 1) only covers
  *future* codename allocations. Added an explicit migration-gap
  subsection: a `WorktreeRecord` created before this effort ships, under a
  repo with a custom wordlist, may already carry a possibly-identifying
  codename with no provenance flag to detect it — added a matching Phase 1
  checklist item and Validation Plan item requiring an explicit
  migration/suppression decision, not just the forced-wordlist fix for new
  allocations.
