# Design - codename-attribution-by-default

Detailed design/decision material for the [codename-attribution-by-default effort](README.md).
Referenced by the Context section and by the Phase 1 checklist items that
implement these decisions (`codename_source`, `wordlist_path_configured`,
`CodenameAttributionPolicyError`, the allocation-time vs. publish-time gates).

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
from the new implicit default entirely **for new codename allocations,
scoped to PR-active repos only (round-22 refinement)**. This gate applies
only when `pr.enabled` is `true` for the repo being allocated into —
`codename.wordlist_path` is a purely local, declarative Picker-ergonomics
feature independent of whether the repo ever opens a PR at all, and a
repo with `pr.enabled` false/unset can never publish a marker, so blocking
its ordinary `create` is pure friction with no safety benefit (the
publish-time `codename_source` gate below already makes this safe
regardless, the moment PR mode is later enabled). For a PR-active repo
with a custom wordlist, `pr.source_attribution` must be set explicitly
(`codename`, `true`, or `false`) before a **new** codename may be assigned;
the new default only auto-applies to a PR-active repo with no custom
wordlist configured *at assignment time*, or to any repo with `pr.enabled`
false/unset regardless of wordlist. This config-shape check (does this
repo have a custom wordlist configured *right now*, and is it PR-active?)
governs allocation-time behavior only — it is superseded, for publish-time
attribution decisions on an **already-assigned** codename, by the
per-record `codename_source` check in the decisive fix below (round-14
finding: the two checks answer different questions — "may a new codename
be allocated implicitly?" vs. "is this existing codename safe to publish
implicitly?" — and must not be conflated). This resolves both halves of
the risk for the common case where a repo's wordlist config never changes
after assignment — see the
legacy-record gap and its provenance-tracking fix below for the remaining
case where it does:

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
**the record's own stored `codename_source` together with whether
`pr.source_attribution` was EXPLICITLY configured (round-22 refinement)**
for whether a given already-assigned codename is safe to publish under the
implicit default — never the repo's *current* `codename.wordlist_path`
config, which may have drifted since assignment. `attribution ==
"codename"` alone cannot distinguish an explicit opt-in from the bare
implicit default (both produce the identical string), so the gate reads
`PRConfig.source_attribution_configured` too: an EXPLICIT `codename`
opt-in publishes any codename regardless of `codename_source` (the
operator has reviewed this repo's vocabulary), while an IMPLICIT
`codename` default requires `codename_source == "built-in"`. A record
with `codename_source: "custom"` under an implicit default requires an
explicit `pr.source_attribution` opt-in to publish, regardless of what the
repo's config says today; a record with `codename_source: "built-in"` is
safe under the new implicit default unconditionally, **even if the repo's
`codename.wordlist_path` is currently configured** — a built-in-sourced
codename was never drawn from that custom vocabulary, so the repo-level
allocation-time gate above (which governs whether a *new* codename may be
allocated implicitly) has no bearing on whether *this already-assigned*
codename is safe to publish (round-14 finding:
these are deliberately two independent gates, not one rule reapplied
twice — allocation-time gate decides `codename_source` for a new record and
whether the implicit default may assign one at all; publish-time gate
decides only from the resulting stored `codename_source` plus explicitness,
both forever fixed **not** at codename-assignment time but at the owning
`PRRecord`'s own creation time (round-27 refinement): `codename_source` is
per-`WorktreeRecord` and assignment-time-fixed as described above, but the
explicitness input to the publish gate (`source_attribution_configured`)
is per-`PRRecord` and freezes at THAT record's creation, via the same
`attribution_mode`/`attribution_explicit` mechanism the Plan's freeze
bullet defines — never read live from current config at publish/refresh
time, which would silently reverse an already-open PR's publish
authorization if the repo's config changed mid-PR-life).
New allocations still consult the *current* config to decide
`codename_source` at the moment of assignment (the current-config check
remains correct and sufficient there, since assignment and config-read are
contemporaneous) — this is a targeted addition, not a replacement, of the
config-shape check above.

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


## Per-PR attribution freeze (rounds 26-31)

Full rationale, gap enumeration, fix specification, and regression-test
requirements for freezing each PR's attribution decision (mode AND
explicitness) at `PRRecord`-creation time, strictly validating the
persisted pair, and protecting it under concurrent-save merge. The Plan
checklist in README.md implements these decisions directly; read this
section before touching any of the four `PRRecord` construction/parse
sites, `tracking.py`'s YAML read/write wiring, or `_save_record_unlocked`.

**Freeze each PR's attribution decision (mode AND explicitness) at
  PRRecord-creation time — at EVERY creation site, not just auto-open;
  never re-derive either live on later pushes (round-26 finding, extended
  round-27)** — the vision's `unconfigured-attribution-never-leaks`
  behavior (round-23) promises a config change won't retroactively expose
  or hide a marker already published under a prior policy, but
  `refresh_source_attribution` currently re-reads `prcfg.source_attribution`
  AND `prcfg.source_attribution_configured` LIVE on every later push
  (verified in source: it has no persisted memory of what mode the PR was
  actually opened under, or whether that mode was explicit). Two distinct
  gaps, both must close together:
  1. **Retroactive mode change:** a repo that flips `source_attribution`
     from `false`/`true` to `codename` (or vice versa) partway through an
     already-open PR's life would have `refresh_source_attribution` start
     publishing (or stop publishing, or swap marker shape for) that SAME
     PR on its very next push.
  2. **Retroactive explicitness change (round-27 finding):**
     `attribution_mode` alone is insufficient even once frozen — an
     implicit-`codename` PR opened against a `"custom"`/unknown-provenance
     record is correctly suppressed at open time (round-22 gate), but if
     the repo LATER adds an explicit `source_attribution: codename` key
     (or removes one), a helper that re-reads live
     `source_attribution_configured` would silently flip that SAME PR's
     publish authorization on its next refresh — the identical class of
     retroactive-change bug, one level deeper (explicitness, not mode).
  **Fix:** add BOTH `PRRecord.attribution_mode: str = ""` (the resolved
  effective mode: `"codename"` / `"true"` / `"false"`) AND
  `PRRecord.attribution_explicit: bool = False`
  (`source_attribution_configured`'s value at that same moment), stamped
  TOGETHER, ONCE, by one shared helper — never separately, so they can
  never drift out of sync with each other. **Call this helper at EVERY
  `PRRecord` creation site, not only `_open_via_provider` (round-27
  finding: stamping only there misses every path that constructs a
  `PRRecord` before `_open_via_provider` ever runs)** — verified four
  distinct construction sites: (a) `create_pr`'s own `target_pr =
  PRRecord(...)` (`pr_ops.py:~868`, BEFORE `_open_via_provider` is called
  later in the same flow); (b) `_push_existing_feature`'s fresh-target
  construction for a new push to a terminal/absent PR (`~2090`); (c)
  manual `set-pr`'s bare `pr = PRRecord()` when no active PR exists yet
  (`~1676`) — the round-27 finding's specific example: a manual
  attach-PR flow that never goes through `_open_via_provider` at all,
  which would otherwise leave both fields permanently empty and silently
  fall back to always-live behavior; (d) `tracking.py`'s
  `_parse_pr_mapping` deserialization (`~1393`) must parse both fields
  back from YAML (the round-9 serialization pattern) — this is a READ,
  not a fresh stamp, for a `PRRecord` reloaded from disk. **The WRITE
  side must be specified too, not just the read (round-28 finding)** —
  `tracking.py`'s `_pr_to_yaml_dict` (`~1422`, verified in source: a
  lean, omit-empties dict-builder distinct from `WorktreeRecord`'s
  hand-rolled string-content builder used for `codename_source`) must
  ALSO emit `attribution_mode`/`attribution_explicit` (following the
  exact same `if pr.attribution_head: d["attribution_head"] = ...`
  only-emit-when-set pattern the function already uses for its other
  optional fields) — parsing alone is not durable: without this write
  side, both fields silently vanish on the very next `tracking.save_record`
  after being stamped, since `_pr_to_yaml_dict` builds the dict every
  `PRRecord` is actually persisted through. `refresh_source_attribution` (and
  `_open_via_provider`'s own initial-publish decision) must use these
  FROZEN `attribution_mode`/`attribution_explicit` pair, never live
  `prcfg.source_attribution`/`source_attribution_configured`, for every
  publish decision on that PR's life — the live config is consulted only
  once, at the PRRecord's creation moment, never again for that PR.
  **The stamp must capture the EFFECTIVE attribution, including any
  per-call override, not the raw config value (round-31 finding)** —
  `create_pr` accepts its own `attribution` override parameter
  (`--attribution`/`--no-attribution`) — typed `bool | None` today, but
  widened to `SourceAttribution | None` by this SAME Phase 1's round-18
  fix (so an explicit override can legitimately be `True`/`False`/
  `"codename"`, not just a bool, once that widening lands) — and every
  attribution-consuming call site already computes `want_attribution =
  (prcfg.source_attribution if attribution is None else attribution)`
  (verified in source: `_finish_auto_open`, `create_pr` itself, and
  `_push_existing_feature` each derive this same effective value) BEFORE
  deciding whether to publish — if the freeze stamp instead reads
  `prcfg.source_attribution` directly, a PR opened with an explicit
  `--no-attribution` override (a one-shot operator decision to suppress
  the marker for THIS PR only) would still stamp `attribution_mode` from
  live config as if no override had been given, and a later
  `refresh_source_attribution` would start publishing a marker the
  operator explicitly suppressed at open — the exact retroactive-change
  bug this freeze exists to prevent, now triggered by the freeze's OWN
  construction rather than a later config edit. **Fix:** the shared
  stamping helper must take the caller's already-computed effective
  attribution value (the same `want_attribution` each site derives) as
  its input, not re-derive it from `prcfg` itself; when a per-call
  override was given (`attribution is not None`), stamp `attribution_mode`
  from the override's own resolved value directly (`"true"` / `"false"` /
  `"codename"` — whatever the (post-widening) override itself carries,
  never re-mapped through `prcfg`) and set `attribution_explicit = True`
  unconditionally, since an explicit per-call override is by definition a
  stronger, more explicit signal than any config-level
  `source_attribution_configured` flag it may override. Add regression
  tests: (a) `create_pr` with `attribution=False` against a repo whose
  config would otherwise publish a `"codename"` marker — the stamped
  `PRRecord` records `attribution_mode="false"`/`attribution_explicit=True`,
  and a SUBSEQUENT `push-changes`/`refresh_source_attribution` on that
  same PR (with no further override argument) still suppresses the
  marker, proving the one-shot override is frozen exactly like a
  config-derived decision, not silently reverted on the very next push;
  (b) `create_pr` with an explicit `attribution="codename"` override
  against a repo whose config default is `False`/unconfigured — the
  stamped pair still records `attribution_mode="codename"`/
  `attribution_explicit=True` (proving the override's OWN value is
  stamped verbatim, not coerced to only `True`/`False`).
  **Migration for a PR opened before these fields existed:** an
  empty/unset `attribution_mode` on an in-flight legacy `PRRecord` falls
  back to today's existing live-config behavior for BOTH fields together
  (no regression for a PR already mid-life when this ships) — the freeze
  guarantee applies prospectively, to every `PRRecord` created after these
  fields exist, not retroactively to one already open without stored
  values. Add regression tests: (i) open a PR under one
  `source_attribution` value, change the repo's config to a DIFFERENT
  value, push again — the marker published/refreshed on that push still
  reflects the ORIGINAL frozen mode, not the new config; (ii) open a PR
  under an implicit `codename` default against a `"custom"`-sourced
  record (correctly suppressed at open), then add an explicit
  `source_attribution: codename` to the repo's config and push again —
  the marker STAYS suppressed, proving `attribution_explicit` is frozen
  too, not just `attribution_mode`; (iii) attach a PR via manual `set-pr`
  (never touching `_open_via_provider`) and push via `push-changes` —
  `refresh_source_attribution` still uses a properly-stamped frozen
  pair, not the legacy-fallback path, proving the shared helper is
  actually wired into the manual-attach site.
**Strictly validate the persisted `attribution_mode`/
  `attribution_explicit` pair, not just round-trip it (round-30
  finding):** `_parse_pr_mapping` deserializes hand-rolled YAML with no
  schema enforcement — the same class of gap the round-12 fix already
  closed for `codename_source` (treat anything except the one known-safe
  literal as unsafe, never the inverted "anything except the one known-
  unsafe literal" shape). Apply the identical discipline here: (a)
  `attribution_explicit` must be checked with `is True` (or an equivalent
  strict boolean check), never a truthy/falsy coercion — a stored string
  `"false"` is truthy in a loose check and must NOT be treated as
  explicit; (b) `attribution_mode` must be validated against the closed
  set `{"", "false", "true", "codename"}` — any other stored value
  (a typo, a future mode this code doesn't know about, hand-edited YAML)
  must be treated as the safe **empty legacy sentinel** (falls back to
  live config, today's existing behavior), never silently accepted as if
  it authorized publication; (c) a partial pair (`attribution_mode` set
  but `attribution_explicit` absent, or vice versa) must ALSO be treated
  as the empty legacy sentinel — never let a half-written record produce
  a mode without its matching explicitness, or an explicitness without
  its matching mode. Add regression tests for all three: a stored
  `attribution_explicit: "false"` string does not authorize publication;
  an unrecognized `attribution_mode` value falls back to legacy live-
  config behavior rather than being read as one of the three known
  modes; a record with only one of the two fields set falls back to
  legacy behavior rather than using the one field that IS present.
**Merge the frozen `pr.attribution_mode`/`pr.attribution_explicit`
  pair under the record lock during concurrent saves, the same way
  round-11 protects `codename`/`codename_source` (round-30 finding):**
  `_save_record_unlocked` currently has NO merge protection for the `pr`
  field at all (verified in source: no revision counter or merge branch
  covers it), so a status/finalize writer holding an in-memory
  `WorktreeRecord` snapshot from BEFORE a concurrent `create-pr`/`set-pr`
  stamped the frozen attribution pair onto that same record's `pr` can
  save over it and silently erase the freeze — the next
  `refresh_source_attribution` then sees the empty legacy `pr` and falls
  back to live config, defeating the entire round-26/27/28 guarantee.
  Add a `pr_revision` counter (following the existing
  `profile_assignment_revision`/`lifecycle_revision` pattern already in
  `_save_record_unlocked`), bumped every time `pr.attribution_mode`/
  `pr.attribution_explicit` are stamped, and merge `record.pr` from the
  current on-disk record whenever `current.pr_revision >
  record.pr_revision`. **The counter must be wired into `WorktreeRecord`'s
  hand-written YAML load/save, not held in memory only (round-31
  finding)** — `WorktreeRecord` is hand-serialized (verified: the same
  reason `codename_source`/`profile_assignment_revision` each need their
  own explicit parse line and emit line, per the round-9/round-13
  serialization pattern), so a `pr_revision` that exists only as a
  dataclass field with no read/write wiring reloads as `0` in every other
  process — defeating the `current.pr_revision > record.pr_revision`
  comparison entirely, since a freshly-loaded `current` record would
  never show a revision higher than an in-memory stale snapshot. Add: a
  parse line reading `data.get("pr_revision", 0)` (bounded
  non-negative, following `profile_assignment_revision`'s own parsing
  exactly) at record-load time, and an emit line
  (`if record.pr_revision: content += f"pr_revision: {record.pr_revision}\n"`,
  following the exact `profile_assignment_revision` emit pattern) at
  record-save time. Add a regression test: stamp a PRRecord's frozen
  attribution pair under the lock (bumping `pr_revision`),
  `save_record`/`load_record` the same file back in a FRESH in-memory
  object (not the same Python object), confirm `pr_revision` survives
  round-trip as a non-zero value, THEN save a stale in-memory
  `WorktreeRecord` snapshot captured BEFORE that stamp — the stale save
  must not erase the freshly-frozen pair.
