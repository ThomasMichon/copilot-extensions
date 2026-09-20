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
from the new implicit default entirely **for new codename allocations**.
For such a repo, `pr.source_attribution` must be set explicitly
(`codename`, `true`, or `false`) before a **new** codename may be assigned;
the new default only auto-applies to a repo with no custom wordlist
configured *at assignment time*. This config-shape check (does this repo
have a custom wordlist configured *right now*?) governs allocation-time
behavior only — it is superseded, for publish-time attribution decisions on
an **already-assigned** codename, by the per-record `codename_source` check
in the decisive fix below (round-14 finding: the two checks answer
different questions — "may a new codename be allocated implicitly?" vs.
"is this existing codename safe to publish implicitly?" — and must not be
conflated). This resolves both halves of the risk for the common case
where a repo's wordlist config never changes after assignment — see the
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
**only the record's own stored `codename_source`** for whether a given
already-assigned codename is safe to publish under the implicit default —
never the repo's *current* `codename.wordlist_path` config, which may have
drifted since assignment. A record with `codename_source: "custom"`
requires an explicit `pr.source_attribution` opt-in to publish, regardless
of what the repo's config says today; a record with `codename_source:
"built-in"` is safe under the new implicit default unconditionally, **even
if the repo's `codename.wordlist_path` is currently configured** — a
built-in-sourced codename was never drawn from that custom vocabulary, so
the repo-level allocation-time gate above (which governs whether a *new*
codename may be allocated implicitly) has no bearing on whether *this
already-assigned* codename is safe to publish (round-14 finding:
these are deliberately two independent gates, not one rule reapplied
twice — allocation-time gate decides `codename_source` for a new record and
whether the implicit default may assign one at all; publish-time gate
decides only from the resulting stored `codename_source`, forever fixed at
assignment). New allocations still consult the *current* config to decide
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
- [ ] **Implement the custom-wordlist exclusion decision** from Context —
  this is an **allocation-time** gate only, distinct from the **publish-
  time** `codename_source` gate below (round-15 finding: the two must be
  implemented as separate checks, not one shared condition, or an
  already-published `codename_source: "built-in"` record would be wrongly
  blocked by this repo-level check): a repo with `codename.wordlist_path`
  currently configured must not have a **new** codename allocated under
  the implicit default — `pr.source_attribution` must resolve to an
  explicitly-configured value (`codename`, `true`, or `false`) before a
  new codename may be assigned for such a repo, never the bare fallback.
  This covers future allocations only (a custom-wordlist repo never
  silently starts allocating new implicit-default codenames without an
  explicit opt-in); it has no effect on *publishing* a codename that is
  already assigned — that decision is made exclusively by the record's own
  `codename_source` (see the publish-time gating bullets below), and a
  `codename_source: "built-in"` record publishes normally under the
  implicit default even in a repo that currently has this validation error
  active. Add a test proving: a repo with a custom wordlist AND an
  absent/default `source_attribution` fails an attempted **new**
  allocation closed (a config validation error at config-load/allocation
  time, not a silent `false`-fallback and not a leaked marker) — and add a
  second test proving that same repo/config combination still successfully
  **publishes** a pre-existing `codename_source: "built-in"` record
  unaffected by this allocation-time error, so the two tests can't be
  satisfied by one shared, overbroad implementation.
- [ ] **Persist `codename_source` through serialization** (round-9
  finding): `WorktreeRecord` is manually round-tripped through YAML, not
  via a generic dataclass (de)serializer — `tracking.load_record` parses
  known keys explicitly (`tracking.py`'s `codename=(str(data["codename"])
  if data.get("codename") else None)`) and `save_record`'s content-builder
  emits each field explicitly (`if record.codename: content +=
  f"codename: ..."`). Adding the dataclass field alone does **not**
  persist it — add a matching explicit parse line in `load_record` and a
  matching explicit emit line in `save_record`'s content-builder,
  following the exact same only-emit-when-set pattern the `codename` field
  itself uses (so a legacy YAML file with no `codename_source` line
  parses to `None`, not a crash or a silently-wrong default).
- [ ] **Set `codename_source` at every codename-assignment call site**, not
  just one: (a) the normal `create` path (`__main__.py`'s
  `codename_tracking.assign_new_codename` call feeding
  `create_new_record`'s `codename=` kwarg, ~line 2268); (b) the paired
  knowledge-repo `create` path (`__main__.py`'s separate
  `knowledge_codename = codename_tracking.assign_new_codename(...)` call
  feeding its own `codename=` kwarg, ~line 1948 — a distinct code path
  from (a), easy to miss); (c) the lazy backfill path
  (`codename_tracking.ensure_codename`, called from `__main__.py`'s
  resume/status backfill and `pr_ops.py`'s create-PR path). **Classify
  using `CodenameConfig.wordlist_path_configured` (defined immediately
  below), never `wordlist_path`'s truthiness and never
  `wordlist_for_repo`'s resolved `Wordlist`** (round-11 + round-13 +
  round-14 findings, consolidated into one rule): all three assignment
  sites only ever have access to the already-PARSED `CodenameConfig`, not
  a raw config mapping, so classification must be driven entirely by a
  flag `parse_codename` sets while parsing, never by re-reading anything
  raw. Two independent failure modes justify this over checking
  `wordlist_path` or the resolved `Wordlist` directly: (1)
  `load_wordlist_or_default` (which `wordlist_for_repo` calls) fail-softs
  to `DEFAULT_WORDLIST` for a missing or malformed custom-path *file*
  (`codename.py`'s `load_wordlist_or_default`), so a configured-but-broken
  custom path would resolve to the exact same `Wordlist` object as "no
  custom path configured" — checking the resolved wordlist would
  misclassify it `"built-in"`; (2) `codename_config.parse_codename`
  (verified in source) silently normalizes ANY non-string `wordlist_path`
  *value* (e.g. `wordlist_path: []`, a number, a mapping) to the empty
  string — indistinguishable from "key never set" — so checking
  `wordlist_path`'s truthiness would misclassify that case too. Both
  failure modes are closed by the single flag defined next; a value's
  *validity* or a file's *loadability* are irrelevant to classification,
  only the raw key's *presence* matters.
- [ ] **Add a `wordlist_path_configured` flag to distinguish "absent" from
  "present but malformed" (round-13 finding)** — extend `CodenameConfig`
  with a `wordlist_path_configured: bool` field, set by `parse_codename` to
  `True` whenever the raw `codename:` block's `wordlist_path` key is
  present at all (regardless of whether its value parses to a valid
  string, or whether the file it names exists or loads) — mirroring the
  exact pattern `source_attribution_configured` already uses for the
  analogous "key present vs. absent" distinction on `pr.source_attribution`.
  Classification then checks THIS flag, so a malformed value or an
  unreadable file still classifies `"custom"` (fail closed). Add tests
  proving: `wordlist_path: []` (or any other non-string value) still
  classifies `"custom"`, not `"built-in"`, and a valid string path naming a
  missing/malformed file also classifies `"custom"`.
- [ ] **Fix `ensure_codename`'s signature and provenance-copy gap
  (round-12 finding):** the lazy-backfill function currently accepts only
  a resolved `wordlist: Wordlist | None` parameter (never the raw path,
  so it cannot classify `codename_source` itself per the rule above), AND
  when it discovers a concurrent writer already assigned a codename
  (`if current.codename: record.codename = current.codename; return
  record`) it copies only `.codename` from the freshly-reloaded on-disk
  record, never `.codename_source` — silently dropping the provenance a
  concurrent writer already recorded. Change `ensure_codename` to accept
  an explicit `codename_source` input (derived by the caller from the raw
  config path, per the classification rule above) instead of deriving
  anything from `wordlist`, set `current.codename_source` alongside
  `current.codename` in the actual-assignment branch, AND copy
  `current.codename_source` (not just `current.codename`) in the
  concurrent-writer-already-assigned branch. Add a regression test: two
  concurrent `ensure_codename` calls on the same record, where one wins
  the race — the LOSING caller's returned record still carries the
  correct `codename_source`, not `None`/a default.
- [ ] **Fix the paired-knowledge path's error-swallowing at BOTH layers
  (round-10 + round-11 findings):** `_carve_paired_knowledge` currently
  wraps `cfg.load_config(project=knowledge_name)` +
  `codename_tracking.wordlist_for_repo(...)` in a bare
  `except Exception: knowledge_wordlist = None`, which silently falls back
  to the built-in wordlist on ANY config-load failure — including the
  new fail-closed policy validation error this effort adds for a
  custom-wordlist repo with an omitted `source_attribution`. That would
  let the paired-knowledge path allocate a codename (and set
  `codename_source: "built-in"`) for exactly the repo/config combination
  the validation error exists to block, defeating it entirely.
  **Additionally**, even after fixing that inner handler, the caller in
  `__main__.py`'s `create` path (~line 2362-2375) wraps the entire
  `_carve_paired_knowledge(...)` call in its own
  `except Exception as exc: ... pair_stamp = None` — a deliberate,
  pre-existing "pairing failures never break the harness carve"
  fail-safe design for ordinary pairing glitches, but it would swallow
  the re-raised policy error at this OUTER boundary too, silently
  reducing "fail the whole create" back down to a logged warning. Fix
  requires BOTH layers: (1) introduce a dedicated exception type (e.g.
  `CodenameAttributionPolicyError`) that the inner config-load/validation
  code raises instead of a generic exception; (2) the inner
  `except Exception` in `_carve_paired_knowledge` explicitly re-raises
  this type rather than swallowing it into `knowledge_wordlist = None`;
  (3) the OUTER `except Exception as exc` around the
  `_carve_paired_knowledge(...)` call in the `create` path (~line 2372)
  must ALSO explicitly re-raise this same type rather than catching it
  into `pair_stamp = None` — only a generic/incidental pairing failure
  stays non-fatal there, never this specific policy violation.
  **Transactionality (round-12 finding, narrowed round-13):** the
  `create` flow persists the harness worktree, branch, and tracking
  record BEFORE it calls `_carve_paired_knowledge` — so simply re-raising
  the policy error at that point would fail the `create` command while
  leaving that harness worktree/branch/record behind as orphaned partial
  state. Add an explicit **preflight check**: validate the knowledge
  project's config (does it have a custom wordlist AND an
  unresolved/omitted `source_attribution`?) BEFORE any harness-side
  create side effect (worktree, branch, or record) happens, and fail the
  whole `create` command at that preflight point if the policy would be
  violated. **This narrows, but does not eliminate, the exposure — the
  transactionality claim covers only the preflight-detected case, never
  the full flow (round-13 finding, further resolved round-14):** a config
  edit landing in the window between the preflight check and the actual
  `_carve_paired_knowledge` call is a residual, accepted TOCTOU race no
  in-process check alone closes (closing it fully would need config-file
  locking, out of scope for this effort). Shrink that window as far as
  practical by **revalidating the identical preflight check a second time
  immediately before the first harness-side side effect** (as late as
  possible in the `create` path, not just once at the top), rather than
  relying on a single early check. **If the residual race is hit anyway
  (round-14 finding — specify the concrete behavior, do not leave it
  implicit):** the re-raised exception path (inner + outer handlers above)
  still fails the `create` command, and Phase 1 does **not** attempt to
  automatically roll back the already-created harness worktree, branch, or
  tracking record — no rollback protocol is implemented, an explicit scope
  decision consistent with the config-locking exclusion above (a correct
  rollback of a partially-created git worktree/branch is materially harder
  than the check it would be compensating for, and this race is narrow and
  rare). Instead, the error surfaced to the operator MUST name the exact
  orphaned worktree path and branch so it can be removed with the existing
  worktree-removal path (`git worktree remove`); this is a defense-in-depth
  backstop for the residual window, not a substitute for the preflight
  checks, which remain the primary enforcement mechanism. Add a regression
  test: the preflight check rejects the `create` command outright, before
  any worktree/branch/record exists, for a knowledge project with a custom
  wordlist and omitted `source_attribution`; add a second regression test
  simulating the race (policy becomes violated only after the second
  preflight check) asserting the failure message names the orphaned
  worktree path/branch and that no automatic rollback is attempted.
- [ ] **Propagate the policy exception at the `create-pr` boundary too
  (round-16 finding)** — the paired-knowledge fix above covers the
  `create` command's harness-carve path, but `ensure_codename`'s OTHER
  call site (`pr_ops.py`'s `_open_via_provider`, the `codename`-marker
  branch, ~lines 1108-1118) has its own, separate `try:
  codename_tracking.ensure_codename(...); except Exception: pass` —
  deliberately broad because a codename-backfill failure (e.g. a lock
  `TimeoutError`) must degrade to "skip the marker on this PR," never
  abort an otherwise-successful `create-pr`. Left as-is, this same handler
  would ALSO silently swallow the new `CodenameAttributionPolicyError`,
  letting `create-pr` succeed with no marker for exactly the
  custom-wordlist/omitted-`source_attribution` repo the policy exists to
  block — a *different*, `create-pr`-specific way to defeat the same
  fail-closed guarantee the paired-knowledge fix closes for `create`. Fix:
  this `except Exception` clause must catch `CodenameAttributionPolicyError`
  **before** the general `except Exception: pass`, and re-raise it
  (aborting `create-pr` outright) rather than falling through to the
  generic pass — every other exception type keeps today's
  skip-the-marker behavior unchanged. Add a regression test: `create-pr`
  against a custom-wordlist repo with omitted `source_attribution`
  (an unmigrated/pre-existing worktree record with no codename yet, so
  the lazy-backfill path is actually exercised) fails the whole
  `create-pr` command with the policy error, rather than succeeding with
  the marker silently omitted.
  concurrent saves (round-11 finding):** `_save_record_unlocked` already
  merges several fields (handoff reservations, lifecycle/session-backend/
  execution-leg state) from the current on-disk record into a stale
  in-memory snapshot before overwriting, specifically to prevent a
  stale concurrent writer from erasing a field set by another writer
  under the lock — but it does NOT currently merge `codename` or the new
  `codename_source`. Without this, a status/PR writer holding an
  in-memory record from BEFORE a concurrent lazy-backfill assigned a
  codename could save over it and erase the just-assigned
  `codename`/`codename_source`. Add `codename`/`codename_source` to the
  same merge-from-current-on-disk-record logic
  `_save_record_unlocked` already applies to those other fields. Add a
  regression test: a save from a stale in-memory record (predating a
  concurrent codename assignment) does not erase the codename or its
  provenance that a concurrent writer set under the lock.
- [ ] **Round-trip test**: assign a codename (setting `codename_source`),
  `save_record`, `load_record` the same file back, assert
  `codename_source` survives unchanged — proving the serialization wiring
  above actually persists the field rather than just existing on the
  in-memory dataclass.
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
  wordlist. **Any stored value other than the literal string
  `"built-in"` must be treated as unsafe/`"custom"` (round-12 finding)**:
  `tracking.load_record` accepts arbitrary YAML values for record fields
  with no schema enforcement, so publish-time gating must check
  `codename_source == "built-in"` to treat a record as safe — never the
  inverted `codename_source != "custom"` shape, which would silently
  treat an unrecognized/malformed stored value (a typo, a future value
  this code doesn't know about, hand-edited YAML) as safe by default.
  Add tests proving: (a) a `WorktreeRecord` with
  `codename_source: "custom"`, in a repo whose config NOW has no custom
  wordlist, still fails closed under the implicit default (the exact
  legacy-drift scenario the round-8 finding raised); (b) a repo with a
  custom wordlist that HAS explicitly configured
  `source_attribution: codename` publishes normally regardless of any
  record's `codename_source`, including for a pre-existing
  `WorktreeRecord` assigned before this effort shipped; (c) a
  `WorktreeRecord` with `codename_source: "built-in"` publishes normally
  under the implicit default; (d) a `WorktreeRecord` with an unrecognized
  stored `codename_source` value (neither `"built-in"` nor `"custom"`)
  fails closed exactly like `"custom"` would.
- [ ] **Backfill migration for existing `WorktreeRecord`s created before
  this field existed (round-10 finding: keep this fail-closed, never
  infer from current config):** a record with no `codename_source`
  recorded is permanently treated as `"custom"` (fail closed under the
  implicit default) — there is no automatic backfill pass that reads the
  owning repo's CURRENT wordlist config to guess a value, because that
  guess is unsound: a record's codename may have been assigned under a
  custom wordlist that the repo's config has since dropped or swapped,
  and inferring `"built-in"` from today's (changed) config would
  reintroduce the exact legacy-drift leak the round-8 fix exists to
  close. The only way an existing, unbackfilled record is ever promoted
  to `"built-in"` is a **manual, explicit, per-record** operator
  verification/edit (e.g. an operator who has checked the actual git
  history of that repo's wordlist config across the record's lifetime) —
  never an automated bulk migration. Add a test proving an unbackfilled
  record fails closed by default regardless of the owning repo's current
  config, and that no code path auto-promotes it without an explicit,
  individually-targeted operator edit.
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

- [ ] Unit (round-15 finding: allocation-time, not publish-time): a repo
  with a custom `codename.wordlist_path` configured AND an absent/default
  `source_attribution` fails a **new codename allocation** closed (a
  config validation error at config-load/allocation time, not a silent
  `false`-fallback and not a leaked marker) — the single highest-priority
  test in this effort, since it's the one silent-leak scenario the default
  flip could introduce. This test must NOT touch an existing
  `WorktreeRecord`'s publish path — see the next bullet and the
  `codename_source: "built-in"` bullet below for that.
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
  (predates this effort) is permanently treated as `"custom"` (fails
  closed) — never auto-promoted to `"built-in"` by any automated pass,
  regardless of the owning repo's current wordlist config (round-10
  finding: no config-inferred backfill).
- [ ] Round-trip (round-9 finding): assign a codename with a known
  `codename_source`, `save_record` it, `load_record` it back, assert
  `codename_source` is unchanged — proving the manual YAML
  serialize/deserialize wiring actually persists the field (a dataclass
  field alone is not sufficient given `WorktreeRecord`'s hand-rolled YAML
  round-trip).
- [ ] Unit (round-9 finding): each of the three codename-assignment call
  sites — normal `create`, paired knowledge-repo `create`, and lazy
  backfill (`ensure_codename`) — sets `codename_source` correctly from
  that call's own config read; a regression in any ONE site (e.g. the
  knowledge-repo path alone) is caught, not just the aggregate behavior.
- [ ] Unit (round-11 finding): a repo with `codename.wordlist_path` set to
  a path that does not exist (or is malformed) is still classified
  `codename_source: "custom"` — proving classification reads the raw
  config value, not `wordlist_for_repo`'s fail-soft-to-built-in resolved
  `Wordlist`.
- [ ] Unit (round-13 finding): a repo config with `wordlist_path` set to a
  non-string value (e.g. `[]`, a number, a mapping) still classifies
  `codename_source: "custom"` — proving classification consults
  `wordlist_path_configured` (set whenever the raw key is present,
  regardless of its value's validity), not `wordlist_path`'s own
  post-parse truthiness (which `parse_codename` silently coerces to
  empty for any non-string value).
- [ ] Unit (round-12 finding): a `WorktreeRecord` with an unrecognized
  stored `codename_source` value (neither `"built-in"` nor `"custom"` —
  e.g. hand-edited YAML, a typo, a future value) fails closed exactly
  like `"custom"` — proving publish-time gating checks
  `codename_source == "built-in"`, never the inverted
  `codename_source != "custom"` shape.
- [ ] Unit (round-12 finding): two concurrent `ensure_codename` calls race
  on the same record; the call that loses the race (finds
  `current.codename` already set) still returns a record whose
  `codename_source` matches what the winning call actually set — proving
  the losing branch copies `current.codename_source`, not just
  `current.codename`.
- [ ] Unit (round-10 finding): a paired knowledge-repo create against a
  knowledge project with a custom wordlist and an omitted
  `source_attribution` fails the whole create with the policy validation
  error — `_carve_paired_knowledge`'s config-load exception handling must
  NOT swallow this into a silent `knowledge_wordlist = None` / built-in
  fallback that would let the create proceed anyway.
- [ ] Unit (round-11 finding): the same paired knowledge-repo policy
  violation propagates past the `create` command's OUTER
  `except Exception` around the whole `_carve_paired_knowledge(...)` call
  too (not just the inner handler) — the whole `create` command fails,
  it is not caught, logged as non-fatal, and reduced to `pair_stamp =
  None` the way an ordinary/incidental pairing failure correctly is.
- [ ] Unit (round-12 finding): the preflight check rejects a `create`
  command for a paired-knowledge policy violation BEFORE any
  harness-side worktree, branch, or tracking record is created — proving
  the failure is transactional for the preflight-detected case (no
  orphaned partial state when the check catches the violation), not just
  a late re-raise after the harness side already exists.
- [ ] Unit (round-13 finding): the preflight check is revalidated a
  second time immediately before the first harness-side side effect (not
  just once at the top of `create`) — proving the residual TOCTOU window
  is minimized to the documented narrow case, not left at the width of
  the entire `create` command.
- [ ] Unit (round-14 finding): simulate the residual TOCTOU race directly
  (the knowledge project's config becomes policy-violating only AFTER the
  second preflight check passes, then `_carve_paired_knowledge` re-raises)
  — assert the `create` command fails, no automatic rollback of the
  already-created harness worktree/branch/record is attempted, and the
  surfaced error message names the exact orphaned worktree path and branch
  so an operator can remove it manually — proving the documented residual
  behavior is real and observable, not merely asserted in prose.
- [ ] Unit (round-16 finding): `create-pr` against a custom-wordlist repo
  with an omitted `source_attribution`, run on a worktree record with no
  codename yet (forcing `_open_via_provider`'s lazy-backfill branch to
  actually call `ensure_codename`) — asserts the whole `create-pr` command
  fails with the policy error, proving the `except Exception: pass` around
  that call site re-raises `CodenameAttributionPolicyError` instead of
  swallowing it into a silent skip-the-marker success, the same way the
  `create` command's paired-knowledge boundary already does. Add a second
  case proving every OTHER exception from `ensure_codename` (e.g. a lock
  `TimeoutError`) still degrades to skip-the-marker, unaffected by this
  fix.
- [ ] Unit (round-11 finding): a save from a stale in-memory
  `WorktreeRecord` (loaded before a concurrent lazy-backfill assigned a
  codename under the record lock) does not erase the `codename`/
  `codename_source` the concurrent writer set — proving
  `_save_record_unlocked`'s existing merge-from-current-on-disk-record
  logic now also covers these two fields, not just the fields it already
  protected (handoff reservations, lifecycle/session-backend/
  execution-leg state).
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

### 2026-09-20 — Plan-review round 9 fixes

- Round 8's `codename_source` fix was itself flagged as underspecified:
  `WorktreeRecord` is manually round-tripped through hand-rolled YAML
  parsing/emission (`tracking.load_record`/`save_record`'s explicit
  per-field content-builder), not a generic dataclass serializer — adding
  the field to the dataclass alone would not persist it across a
  save/load cycle, silently losing provenance on the very next write.
  Checked the actual source and confirmed three distinct codename-
  assignment call sites (normal `create`, a separate paired
  knowledge-repo `create` path, and lazy backfill via `ensure_codename`),
  each of which would need to set the new field independently.
- Added explicit plan items covering: (1) the matching manual
  parse/emit lines in `load_record`/`save_record`, following the exact
  only-emit-when-set pattern the `codename` field itself already uses;
  (2) setting `codename_source` at all three assignment call sites,
  named explicitly so none is missed; (3) a dedicated round-trip test
  (assign → save → load → assert unchanged) plus a per-call-site test,
  rather than relying on the existing provenance tests to catch a
  serialization gap they don't actually exercise.

### 2026-09-20 — Plan-review round 10 fixes

- Two new high-severity findings, both concrete real-code gaps in the
  round-8/9 provenance plan:
  1. Checked `_carve_paired_knowledge`'s actual source: it wraps
     `cfg.load_config`/`wordlist_for_repo` in a bare
     `except Exception: knowledge_wordlist = None`, which would silently
     swallow the new fail-closed policy-validation error (for a
     custom-wordlist repo with omitted `source_attribution`) and fall
     back to the built-in wordlist — letting the paired-knowledge path
     allocate exactly the codename the validation error exists to
     block. Added an explicit plan item requiring this path to
     distinguish and PROPAGATE the policy error rather than swallow it,
     plus a regression test.
  2. Round 8's backfill-migration item inferred `codename_source` for
     pre-existing records from the owning repo's CURRENT wordlist
     config — flagged as unsound for the same reason round 8 itself
     exists: a repo's config can drift after a codename was assigned, so
     "current config says no custom wordlist" doesn't prove the record's
     codename actually came from the built-in list. Removed the
     config-inferred backfill entirely; an unbackfilled record now stays
     permanently `"custom"` (fail-closed) unless an operator manually,
     explicitly verifies and edits that specific record — never an
     automated bulk-inference pass.

### 2026-09-20 — Plan-review round 11 fixes

- Three new high-severity findings, all concrete real-code gaps in the
  round-8/9/10 provenance plan, each verified against actual source:
  1. `_save_record_unlocked` (`tracking.py`) already merges several
     fields (handoff reservations, lifecycle/session-backend/
     execution-leg state) from the current on-disk record into a stale
     in-memory snapshot before overwriting — specifically to stop a
     stale concurrent writer from erasing a field another writer set
     under the lock — but does not cover `codename`/`codename_source`.
     Added a plan item to extend that same existing merge logic to these
     two fields, plus a concurrent-save regression test.
  2. `wordlist_for_repo` (via `load_wordlist_or_default`) fail-softs to
     `DEFAULT_WORDLIST` for a missing/malformed custom-path file
     (verified in `codename.py`) — so classifying provenance from the
     RESOLVED wordlist (as round-8/9's plan text implied) would
     misclassify a configured-but-broken custom path as `"built-in"`.
     Changed the classification rule to read the raw
     `codename.wordlist_path` config string directly (non-empty →
     `"custom"`) at every assignment site, never the resolved `Wordlist`.
  3. Round 10's paired-knowledge fix only addressed
     `_carve_paired_knowledge`'s own inner `except Exception` — but its
     caller in the `create` path wraps the ENTIRE
     `_carve_paired_knowledge(...)` call in its own outer
     `except Exception as exc: ... pair_stamp = None`, a deliberate
     pre-existing fail-safe for ordinary pairing glitches that would
     still swallow the re-raised policy error at that outer boundary.
     Added a plan item requiring a dedicated exception type
     (`CodenameAttributionPolicyError`) that both the inner AND outer
     handlers must explicitly re-raise rather than catch — only a
     genuine/incidental pairing failure stays non-fatal at either layer.

### 2026-09-20 — Plan-review round 12 fixes

- Three more findings against the round-8 through 11 provenance
  mechanism, again each verified against actual source:
  1. Publish-time gating must check `codename_source == "built-in"`, not
     the inverted `!= "custom"` — `tracking.load_record` enforces no
     schema on stored field values, so an unrecognized/malformed stored
     value must fail closed like `"custom"`, never be silently treated as
     safe by an inverted check.
  2. Verified `ensure_codename`'s actual signature and body: it takes
     only a resolved `Wordlist` (not the raw path needed to classify
     provenance), and its concurrent-writer-already-assigned branch
     copies `current.codename` but not `current.codename_source` —
     dropping provenance for the losing side of a race. Added a plan
     item to change the signature to accept an explicit
     `codename_source` and to copy it in that branch too.
  3. Verified the `create` flow's actual ordering: the harness worktree,
     branch, and tracking record are persisted BEFORE
     `_carve_paired_knowledge` runs, so round 10/11's "re-raise the
     policy error" fix would fail the command while leaving that
     already-created harness state behind — not transactional. Added an
     explicit preflight-check requirement: validate the knowledge
     project's policy BEFORE any harness-side side effect, making the
     re-raise path a defense-in-depth backstop rather than the primary
     enforcement point.

### 2026-09-20 — Plan-review round 13 fixes

- Two more findings, again each verified against actual source:
  1. Round-11's "read the raw `wordlist_path` string" rule was itself
     unimplementable as written: verified `codename_config.parse_codename`
     silently coerces ANY non-string `wordlist_path` value (a list,
     number, mapping) to the empty string, indistinguishable from "key
     never set" — the same class of gap round 11 found in
     `wordlist_for_repo`, one layer earlier in the parse pipeline. Fixed
     by adding a `wordlist_path_configured` boolean to `CodenameConfig`,
     set whenever the raw key is present regardless of value validity —
     mirroring the existing `source_attribution_configured` pattern for
     the identical "key present vs. absent" distinction on a sibling
     config field.
  2. The preflight fix from round 12 closes the common case but still
     leaves a real TOCTOU window (config changing between preflight and
     the actual carve) that a single early check can't fully close.
     Rather than overclaim full transactionality, narrowed the plan to
     say so explicitly: revalidate the same check a second time
     immediately before the first harness-side side effect (shrinking,
     not eliminating, the window), with the re-raise path as an accepted
     defense-in-depth backstop for the documented residual case.

### 2026-09-20 — Plan-review round 14 fixes

- Three findings on the round-13 head, each verified against actual
  source before editing:
  1. **Previously-missed, resolved:** the repo-level "custom wordlist
     configured → `source_attribution` must be explicit" gate (Decision,
     Context) and the round-8/9 per-record `codename_source` gate were
     both stated as unconditional rules, and conflicted for the case of a
     repo that currently has a custom wordlist configured but holds a
     `codename_source: "built-in"` record from before that config
     existed. Resolved by explicitly scoping the two gates to different
     questions: the repo-level check governs only whether a *new*
     codename may be allocated implicitly; the per-record check governs
     only whether an *already-assigned* codename is safe to *publish*
     implicitly, and always wins for that question regardless of the
     repo's current config. Edited both the "Decision" paragraph and the
     "Decisive fix" paragraph in Context to state this explicitly instead
     of leaving it implicit.
  2. Consolidated the round-11 "read the raw path" classification rule
     and the round-13 `wordlist_path_configured` flag into one coherent
     instruction — the round-11 bullet still told implementers to "read
     the raw `codename.wordlist_path` string," which is literally not
     possible from a call site holding only a parsed `CodenameConfig`
     (the very problem round-13 introduced the flag to fix). Rewrote the
     round-11 bullet to state the two failure modes it covers
     (unreadable/malformed custom-path *file*, and a non-string
     `wordlist_path` *value*) and point directly at the
     `wordlist_path_configured` flag as the single classification
     mechanism, removing the contradictory "read it raw" instruction.
  3. The transactionality narrowing from round 12/13 acknowledged the
     residual TOCTOU race exists but never specified what happens if it's
     actually hit — an implementer could reasonably guess anything from
     "silent orphan" to "crash." Specified the concrete behavior: no
     automatic rollback of the harness worktree/branch/record (explicit
     scope exclusion, same rationale as the config-locking exclusion),
     and the surfaced error must name the orphaned worktree path/branch
     for manual cleanup via the existing `git worktree remove` path.
     Added a matching Validation Plan test that simulates the race
     directly (not just the preflight-catches-it case) and asserts the
     message and no-rollback behavior.

### 2026-09-20 — Plan-review round 15 fixes

- One finding on the round-14 head, verified against the actual plan
  text: the Phase 1 "implement the custom-wordlist exclusion decision"
  bullet and its matching Validation Plan test were both phrased as "a
  repo with a custom wordlist and omitted `source_attribution` publishes
  nothing," stated without qualification — contradicting the Context
  decision (round-14 fix) that an already-assigned `codename_source:
  "built-in"` record still publishes normally under the implicit default
  even when the repo's current config has a custom wordlist. Split the
  bullet explicitly into two distinct gates: the repo-level check is
  **allocation-time only** (blocks assigning a *new* codename implicitly),
  never a publish-time check; a `codename_source: "built-in"` record's
  publish decision is untouched by it. Reworded the Phase 1 bullet and its
  Validation Plan test to require BOTH a test that the allocation-time
  error fires AND a test that a pre-existing built-in record still
  publishes in that same repo/config combination, so one overbroad
  implementation can't satisfy the requirement by accident.

### 2026-09-20 — Plan-review round 16 fixes

- One finding on the round-15 head, verified against the actual
  `pr_ops.py` source: the paired-knowledge fix (round-10/11) and the
  `create` command's preflight (round-12/13/14) only cover the `create`
  command's harness-carve path. `ensure_codename` has a SECOND, entirely
  separate call site — `pr_ops.py`'s `_open_via_provider`, in the
  `codename`-marker branch's lazy-backfill — wrapped in its own bare
  `try: ensure_codename(...); except Exception: pass`, deliberately broad
  so an ordinary backfill failure (e.g. a lock `TimeoutError`) degrades to
  "skip the marker" rather than aborting an otherwise-successful
  `create-pr`. Unaddressed, this same broad handler would also swallow the
  new `CodenameAttributionPolicyError`, letting `create-pr` succeed with
  no marker for exactly the repo/config combination the policy exists to
  block — fail-OPEN instead of fail-closed, and a different bypass route
  than the one the paired-knowledge fix closes. Fixed by requiring this
  handler to catch and re-raise `CodenameAttributionPolicyError`
  specifically, ahead of the generic `except Exception: pass`, while every
  other exception keeps today's skip-the-marker behavior. Added a matching
  Validation Plan test pair: one proving `create-pr` fails closed for the
  policy violation, one proving other exceptions are unaffected.
