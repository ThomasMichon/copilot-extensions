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

**Also needs resolution:** every existing test that relies on the bare
dataclass DEFAULT (constructs a `PRConfig()`/uses a fixture without an
explicit `source_attribution=` override, and asserts `.source_attribution
is False`) will need updating to assert `"codename"` instead. This is
narrower than "every test mentioning `source_attribution`" — most of the
call sites in `test_pr_ops.py` and `test_providers.py` already pass an
explicit `source_attribution=True` / `source_attribution="codename"` /
`source_attribution_configured=False` kwarg to construct their fixture
config, and those intentional explicit-opt-out/explicit-mode cases must
stay exactly as written; only the genuinely bare-default assertions (the
clearest examples live in `test_config.py`) change. Audit each call site
individually before starting Phase 1 rather than assuming a blanket
rewrite across all three test files.

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
contain identifying or private terms.

**Decision:** a repo with `codename.wordlist_path` configured is excluded
from the new implicit default entirely. For such a repo,
`pr.source_attribution` must be set explicitly (`codename`, `true`, or
`false`); the new default only auto-applies to a repo with no custom
wordlist configured. This config-shape check (does this repo have a custom
wordlist configured *right now*?) resolves both halves of the risk for the
common case where a repo's wordlist config never changes after
assignment — see the legacy-record gap and its provenance-tracking fix
below for the remaining case where it does:

- **Future allocations:** a repo with a custom wordlist never silently
  inherits `codename` mode — it must opt in explicitly, at which point the
  operator has necessarily reviewed the vocabulary's public-safety.
- **Pre-existing codenames:** because such a repo never receives the
  implicit default in the first place, an already-assigned codename drawn
  from that repo's custom wordlist never starts publishing merely because
  the global default changed — there is nothing to migrate or suppress
  retroactively, **provided the repo's wordlist config is unchanged since
  assignment.**

**Legacy-record gap (round-8 finding) — the config-shape check alone is
NOT sufficient:** the argument above silently assumes the repo's *current*
wordlist config accurately reflects what generated *every* codename it has
ever assigned. That assumption breaks the moment a repo's
`codename.wordlist_path` config **changes after** a codename was assigned:
a repo that had a custom wordlist configured, assigned a worktree a
codename from it, and *later* removes (or swaps) that config now reads as
"no custom wordlist" — and would incorrectly inherit the new implicit
default and publish that old, possibly-identifying codename, believing it
came from the safe built-in list. `WorktreeRecord` does not currently
persist which wordlist generated its codename, so there is no way to tell
the two cases apart from config state alone.

**Decisive fix:** persist provenance on the record itself, not just in
current config. At codename-assignment time, `WorktreeRecord` gains a
`codename_source` field (`"built-in"` or `"custom"`) recorded once and
never revisited afterward. Publish-time attribution resolution consults
**the record's own stored `codename_source`** for whether a given
already-assigned codename is safe to publish under the implicit default —
not the repo's current `codename.wordlist_path` config, which may have
drifted since assignment. A record with `codename_source: "custom"`
requires the same explicit `pr.source_attribution` opt-in as a repo
currently configured with a custom wordlist, regardless of what the repo's
config says today; a record with `codename_source: "built-in"` is safe
under the new implicit default unconditionally. New allocations still
consult the *current* config to decide `codename_source` at the moment of
assignment (the current-config check remains correct and sufficient there,
since assignment and config-read are contemporaneous) — this is a targeted
addition, not a replacement, of the config-shape check above.

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
- [ ] **Implement the custom-wordlist exclusion decision** from Context: a
  repo with `codename.wordlist_path` configured does not receive the new
  implicit default at all — `pr.source_attribution` must resolve to an
  explicitly-configured value (`codename`, `true`, or `false`) for such a
  repo, never the bare fallback. This covers future allocations (a
  custom-wordlist repo never silently starts publishing without an
  explicit opt-in). Add a test proving: a repo with a custom wordlist AND
  an absent/default `source_attribution` publishes nothing (fails closed,
  does not silently fall back to `false` either — surface this as a
  config validation error so the gap is caught at config-load time, not
  discovered via a leaked marker).
- [ ] **Implement per-record codename provenance** (the legacy-record gap
  from Context): add a `codename_source` field (`"built-in"` or
  `"custom"`) to `WorktreeRecord`, populated once at codename-assignment
  time from whether the repo has `codename.wordlist_path` configured at
  THAT moment. Publish-time attribution resolution must consult the
  record's own `codename_source`, not the repo's current wordlist config,
  before treating an already-assigned codename as safe under the implicit
  default — a record with `codename_source: "custom"` requires the same
  explicit `pr.source_attribution` opt-in as a currently-custom-wordlist
  repo, even if the repo's config has since reverted to no custom
  wordlist. Add tests proving: (a) a `WorktreeRecord` with
  `codename_source: "custom"`, in a repo whose config NOW has no custom
  wordlist, still fails closed under the implicit default (the exact
  legacy-drift scenario the round-8 finding raised); (b) a repo with a
  custom wordlist that HAS explicitly configured
  `source_attribution: codename` publishes normally regardless of any
  record's `codename_source`, including for a pre-existing
  `WorktreeRecord` assigned before this effort shipped; (c) a
  `WorktreeRecord` with `codename_source: "built-in"` publishes normally
  under the implicit default.
- [ ] **Backfill migration for existing `WorktreeRecord`s created before
  this field existed:** a record with no `codename_source` recorded is
  ambiguous (predates this effort) and must be treated as `"custom"` (fail
  closed) until backfilled, never defaulted to `"built-in"`. Add a
  one-time backfill pass: for each existing record, set `codename_source`
  from the OWNING repo's current wordlist config at backfill time (the
  best available signal, though imperfect for a repo whose config already
  drifted before backfill runs — document this residual limitation rather
  than silently treating it as fully solved). Add a test proving an
  unbackfilled record fails closed by default.
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
  absent/default `source_attribution` fails closed (a config validation
  error at load time, not a silent `false`-fallback and not a leaked
  marker) — the single highest-priority test in this effort, since it's
  the one silent-leak scenario the default flip could introduce.
- [ ] Unit: a repo with a custom `codename.wordlist_path` configured AND an
  explicit `source_attribution: codename` publishes normally — including
  for a pre-existing `WorktreeRecord` whose codename was assigned before
  this effort shipped, proving the exclusion decision is scoped to
  "no explicit config," not to "has ever had a custom wordlist."
- [ ] Unit (legacy-record gap, round-8 finding): a `WorktreeRecord` with
  `codename_source: "custom"`, in a repo whose CURRENT config has no
  custom wordlist configured (the config changed after assignment), still
  fails closed under the implicit default — proving publish-time
  resolution consults the record's own provenance, not just current
  config.
- [ ] Unit: a `WorktreeRecord` with `codename_source: "built-in"` publishes
  normally under the implicit default regardless of the repo's current
  wordlist config.
- [ ] Unit: an existing `WorktreeRecord` with no `codename_source` recorded
  (predates this effort) is treated as `"custom"` (fails closed) until the
  backfill migration runs — never silently treated as `"built-in"`.
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

### 2026-09-20 — Plan-review round 7 fixes

- Round 6's two custom-wordlist findings were both still open after the
  round-6 fix landed. Replaced the hedged "options include X or Y, pick
  one" language with a single decisive rule: a repo with
  `codename.wordlist_path` configured is excluded from the new implicit
  default entirely and must set `source_attribution` explicitly. This one
  config-shape check resolves both the future-allocation risk and the
  pre-existing-codename migration gap at once (a custom-wordlist repo
  never silently inherits the default, so nothing already-assigned starts
  publishing merely because the global default changed) — no separate
  migration/provenance-tracking mechanism is needed after all.
- Fixed an overstated claim: the plan previously implied the wide test
  sweep spans `test_config.py`, `test_pr_ops.py`, and `test_providers.py`
  uniformly. Verified the actual fixtures — `test_pr_ops.py` call sites
  already pass explicit `source_attribution=`/`source_attribution_configured=`
  kwargs (intentional opt-out/explicit-mode cases that must not change);
  only genuinely bare-default assertions (concentrated in `test_config.py`)
  are affected by the dataclass-default flip. Narrowed the plan text and
  added an explicit per-call-site-audit instruction instead of a blanket
  three-file rewrite.
- Resolved a stale "unrelated baseline exceeds module size cap" finding:
  confirmed via `git diff origin/main..HEAD --stat` that this PR's actual
  diff no longer touches `tools/module-size-baseline.json` at all (that
  fix landed separately in PR #2980, merged before this branch's last
  rebase); manually resolved the review thread and posted an explanatory
  comment rather than re-editing unrelated plan content.

### 2026-09-20 — Plan-review round 8 fixes

- Round 7's decisive fix ("a repo with a custom wordlist config is
  excluded from the implicit default") was itself flagged as
  insufficient: it silently assumes a repo's *current* wordlist config
  accurately reflects what generated *every* codename it has ever
  assigned. That assumption breaks the moment a repo's
  `codename.wordlist_path` config changes AFTER a codename was assigned
  (added, used, then later removed or swapped) — the repo would then read
  as "no custom wordlist" and incorrectly inherit the new default,
  publishing an old codename that may not actually be drawn from the safe
  built-in list. Round 7's claim that "no provenance-tracking mechanism is
  needed after all" was wrong for this legacy-drift case specifically.
- Fixed with the mechanism round 6 originally floated and round 7
  mistakenly ruled out: added a per-record `codename_source` field on
  `WorktreeRecord`, set once at assignment time from the repo's config AT
  THAT MOMENT, consulted at publish time instead of (not in addition to)
  the repo's current config. This correctly handles both the common case
  (config never changes — current-config and record-provenance agree) and
  the drift case the round-8 finding raised (config changes after
  assignment — the record's own stored provenance still gates
  publication correctly). Added a matching backfill-migration item: a
  record with no `codename_source` (predates this field) must be treated
  as `"custom"` (fail closed) by default, never silently treated as safe.
