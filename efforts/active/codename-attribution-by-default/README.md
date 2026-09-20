# Codename Attribution By Default

- **Slug:** `codename-attribution-by-default`
- **Repo:** copilot-extensions
- **Branch(es):** `pr/<slug>` per phase
- **Created:** 2026-09-20
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** vision-extending — `visions/plugins/agent-worktrees/pull-requests`
  now explicitly covers the PR-marker/codename mechanism (revised
  2026-09-20, round-23 finding: it previously described only the
  provider-neutral PR surface, not this mechanism or its public-safety
  guarantee — a genuine blind spot in the vision, folded back at the
  detail ceiling rather than cited as sufficient without revision). This
  effort does not introduce new architecture untethered from that vision,
  but it is MORE than a bare default-value flip (round-22 finding:
  `below-altitude` undersold the actual scope) — Phase 1 adds a new
  persisted `WorktreeRecord` field (`codename_source`), a new dedicated
  exception type (`CodenameAttributionPolicyError`), and new
  allocation-time/publish-time policy gates spanning multiple call sites,
  all extending that same covered capability's safety envelope. Extends,
  rather than closes, the vision: the vision now states the
  informationless-by-default/persistence-across-config-change guarantee
  at the intent level; this effort deepens the provenance/fail-closed
  mechanics that realize it for a config-default change, rather than
  adding an unrelated new capability. **Checked against
  `docs/patterns/README.md`'s binding design invariants:** none apply —
  this is a CLI/data-model change to an existing plugin's own tracking
  records, not a plugin SERVICE change (no new network endpoint, no
  installation-cell/marketplace-provenance surface, no shared-
  infrastructure dependency, no runtime-versioning concern); the
  invariants govern plugin-service topology and deployment, which this
  effort does not touch.
- **Umbrella issue:** [#2977](https://github.com/ThomasMichon/copilot-extensions/issues/2977)

## Guiding Intent

Close the gap `pr-attribution-codenames` left open: the codename feature is
fully built and unit/integration-tested end-to-end (round-29 finding: this
is IMPLEMENTATION coverage, not live proof of the codename marker itself —
the only live-merged-PR evidence cited in Context is the RAW marker form
(`source_attribution: true`) on a private downstream repo; no repo has ever
actually published a real PR carrying the codename-form marker, and this
effort's own Phase 4 defers that live proof until after this PR lands), but
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

### Custom wordlists and downstream effects (design.md)

A repo's `codename.wordlist_path` is a separate, independently-configured
risk surface from `pr.source_attribution`: a custom wordlist could contain
identifying/private terms, so a repo with one configured is excluded from
the new implicit default at allocation time, and each `WorktreeRecord`
persists a `codename_source` (`"built-in"`/`"custom"`) at assignment time
so publish-time attribution resolution never has to trust a repo's
current (possibly since-changed) wordlist config. This effort also has
downstream effects on the `attribution-audit` risk model, several docs
describing the old default, and migration guidance for repos with an
explicit `false`. Full rationale, the legacy-record gap, and the
decisive per-record provenance fix live in **[design.md](design.md)** —
read it before starting Phase 1; the Phase 1 checklist below implements
these decisions directly and assumes this design is understood.

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
- [ ] **Widen `attribution` parameter typing across `pr_ops.py` to match
  `SourceAttribution` (round-18 finding)** — `create_pr`,
  `_finish_auto_open`, and `_push_existing_feature` all annotate their
  `attribution` override parameter `bool | None`, but `create_pr` already
  falls back to `prcfg.source_attribution` (typed `SourceAttribution =
  bool | Literal["codename"]`) whenever the caller passes `None`, and
  passes that same value on to the other two functions — with the default
  now `"codename"`, the normal/unconfigured path routinely carries a
  string through a parameter annotated `bool | None`: an inaccurate
  annotation, not a runtime crash (this repo's documented Python gate is
  `ruff check --select F,E9` plus pytest — no mypy/pyright gate exists;
  see `TESTING.md`, round-19 finding). Change all three parameters to
  `SourceAttribution | None`, matching `PRConfig`'s own field type, so the
  annotation is accurate and an explicit override can also legitimately be
  `codename` (not just `True`/`False`); the CLI's existing
  `--no-attribution` boolean opt-out flag's behavior is unchanged (it
  still passes `False` through the same parameter, just now correctly
  typed as one member of the wider union). **Acceptance criterion (must be
  an executable test, not a type-check claim this repo has no gate for):**
  add a runtime integration test that calls `create_pr` with no explicit
  `attribution` override against a repo whose `prcfg.source_attribution`
  resolves to `"codename"`, and asserts the string value propagates
  through `_finish_auto_open`/`_push_existing_feature` unchanged and
  produces a published codename marker — proving the annotation change
  didn't accompany a silent runtime behavior change, since ruff's F/E9
  selection does not itself catch a parameter-type mismatch.
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
  **Scope this gate to PR-active repos only (round-22 finding)** —
  `codename.wordlist_path` is documented as a purely local, declarative
  Picker-ergonomics feature, independent of whether the repo uses PR mode
  at all (`PRConfig.enabled: bool = False` by default — verified in
  source); a repo with `pr.enabled` false or unset never opens a PR and
  can never publish a marker, so gating ordinary `create` for such a repo
  is pure friction with no corresponding safety benefit — the persisted
  `codename_source` (below) already makes publish-time resolution
  fail-closed regardless, the moment (if ever) that repo later turns PR
  mode on. **Enforce this allocation-time gate only when `pr.enabled` is
  `true`** for the repo being allocated into; when `pr.enabled` is
  false/unset, allocation proceeds normally and simply records the
  correct `codename_source` for whenever PR mode might later be enabled.
  This covers future allocations only (a PR-active, custom-wordlist repo
  never silently starts allocating new implicit-default codenames without
  an explicit opt-in); it has no effect on *publishing* a codename that is
  already assigned — that decision is made exclusively by the record's own
  `codename_source` (see the publish-time gating bullets below), and a
  `codename_source: "built-in"` record publishes normally under the
  implicit default even in a repo that currently has this validation error
  active. Add tests proving: (a) a PR-ACTIVE (`pr.enabled: true`) repo
  with a custom wordlist AND an absent/default `source_attribution` fails
  an attempted **new** allocation closed (a config validation error at
  config-load/allocation time, not a silent `false`-fallback and not a
  leaked marker); (b) that same repo/config combination still successfully
  **publishes** a pre-existing `codename_source: "built-in"` record
  unaffected by this allocation-time error; (c) the IDENTICAL custom
  wordlist/omitted-`source_attribution` config combination on a repo with
  `pr.enabled` false/unset allocates a **new** codename successfully (no
  validation error), proving the gate is correctly scoped and does not
  regress ordinary local-only Picker usage — three tests, not two, so an
  overbroad implementation (blocking (c)) or an underbroad one (missing
  (a)) both fail.
- [ ] **Apply the identical, identically-scoped allocation-time preflight
  to the NORMAL `create` path, not just the paired-knowledge path
  (round-21 finding)** — the bullets above and below only specify this
  policy for `_carve_paired_knowledge` (the knowledge-repo carve);
  `__main__.py`'s `_create_worktree_core` — the ordinary harness-worktree
  creation path every `create` call goes through — independently calls
  `codename_tracking.assign_new_codename(tracking_path,
  codename_tracking.wordlist_for_repo(config))` (verified in source,
  ~line 2268) and currently receives only a resolved `Wordlist`, with no
  way to distinguish an omitted `source_attribution` key from an explicit
  one. Left unaddressed, an ordinary `create` against a PR-active repo
  with a custom wordlist and omitted `source_attribution` would silently
  allocate and set `codename_source: "built-in"` for the harness worktree
  itself, bypassing the fail-closed policy entirely through the single
  most common code path — a gap distinct from (and more severe than) the
  paired-knowledge gap the surrounding bullets close. Add the same
  preflight check `_create_worktree_core` runs BEFORE any create side
  effect (worktree, branch, or record), scoped by the same `pr.enabled`
  condition above: validate the *own* repo's config (not the knowledge
  project's) for a custom wordlist with an unresolved/omitted
  `source_attribution`, raising `CodenameAttributionPolicyError` (the same
  dedicated exception type introduced below) if violated **and
  `pr.enabled` is true** — mirroring the paired-knowledge preflight's
  transactionality treatment (preflight before any side effect, a second
  revalidation immediately before the first side effect, and the same
  documented residual-TOCTOU-window/no-rollback/named-
  orphan-path behavior). Add a regression test: `create` against a repo
  with a custom wordlist and omitted `source_attribution` fails outright,
  before any worktree/branch/record exists — the direct normal-path
  analog of the paired-knowledge preflight test below.
- [ ] **Centralize the allocation-time gate INSIDE `ensure_codename`
  itself, not just at `create`/paired-knowledge call sites (round-26
  finding)** — `ensure_codename` is the SHARED lazy-backfill function three
  more callers invoke directly to allocate a missing codename, none of
  which the bullets above protect: `resume` (`__main__.py:~6110-6113`) and
  `status --write` (`~7424-7427`) both call `ensure_codename` on a
  pre-Phase-2 record with no codename yet, and `create-pr`'s own backfill
  (round-16/22) is a fourth. Adding the preflight only at `create`'s two
  call sites leaves `resume`/`status --write` as an unprotected bypass: a
  PR-active repo with a custom wordlist and omitted `source_attribution`
  could still silently allocate a NEW `codename_source: "built-in"`
  codename simply by running `resume` or `status --write` on an
  old/unmigrated record. Fix: move the policy check INSIDE
  `ensure_codename` itself, on the actual-new-allocation branch only (not
  the "concurrent writer already assigned one, just copy it" branch,
  which must never re-validate policy for an already-decided codename).
  Extend `ensure_codename`'s signature to take the inputs the check needs
  (raw `wordlist_path_configured`/`codename_source` classification, the
  repo's `pr.enabled`, and `source_attribution_configured`) instead of
  requiring every caller to duplicate the preflight — this single change
  then automatically protects `resume`, `status --write`, AND
  `create-pr`'s backfill (the `create` and paired-knowledge preflights
  above remain as an early, side-effect-safe fail rather than relying
  solely on this backstop, per this plan's established "preflight over
  late-raise" preference, but this centralization ensures no caller can
  ever forget the check going forward). Add regression tests: `resume`
  and `status --write`, each independently, against a PR-active repo with
  a custom wordlist and omitted `source_attribution`, on a record with no
  codename yet, fail with the policy error rather than silently
  allocating one.
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
  custom-wordlist **PR-active** (`pr.enabled: true`, round-22 scoping —
  see the allocation-time gate bullet above) repo with an omitted
  `source_attribution`. That would let the paired-knowledge path allocate
  a codename (and set `codename_source: "built-in"`) for exactly the
  repo/config combination the validation error exists to block, defeating
  it entirely.
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
  fail-closed guarantee the paired-knowledge fix closes for `create`.
  **This re-raise point is too late to be transactional (round-22
  finding) — a preflight, not just a late re-raise, is required:** by the
  time `_open_via_provider` runs, `create_pr` has already squashed,
  force-pushed the feature branch, and `tracking.save_record`'d the PR
  entry (verified in source, `create_pr`'s flow ~lines 990-1007) —
  re-raising here would abort with a genuinely public branch and an open
  tracking record already left behind, unlike `_create_worktree_core`/
  `_carve_paired_knowledge`, for which this plan defines real
  preflight-based transactionality. Fix requires BOTH pieces, not just the
  re-raise: (1) **preflight** — `create_pr` must run the identical
  policy-and-`pr.enabled`-scoped preflight (same helper the allocation-time
  gate bullets above use) BEFORE the squash/push, for the record's own
  repo, whenever the effective attribution mode will need to lazy-backfill
  a codename (record has none yet and effective `attribution` resolves to
  `"codename"`) — failing `create_pr` outright at that point, before any
  branch is pushed or PR record saved, the same "fail before side effects"
  treatment every other allocation site in this plan uses; (2) **the
  re-raise stays too**, as a defense-in-depth backstop for the same class
  of narrow TOCTOU race the other preflights document (config changing
  between the preflight and the actual backfill) — this residual race is
  NOT expected to be closed further, matching this plan's established
  narrowed-transactionality posture elsewhere, and its already-pushed
  branch/record are left in place with no automatic rollback, consistent
  with the no-rollback decision documented for the other preflights. Fix
  the `except Exception` clause to catch `CodenameAttributionPolicyError`
  **before** the general `except Exception: pass`, and re-raise it
  (aborting `create-pr` outright) rather than falling through to the
  generic pass — every other exception type keeps today's
  skip-the-marker behavior unchanged. Add regression tests: (a)
  `create-pr` against a PR-active custom-wordlist repo with omitted
  `source_attribution` (an unmigrated/pre-existing worktree record with no
  codename yet) fails BEFORE any squash/push happens, via the new
  preflight; (b) simulating the residual race (policy becomes violated
  only after the preflight passes) still fails via the re-raise path, with
  the branch/record left in place and no automatic rollback attempted —
  the direct `create-pr` analog of the other preflights' race tests.
- [ ] **Merge `codename`/`codename_source` under the record lock during
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
  THAT moment. **Publish-time attribution resolution must consult the
  record's own `codename_source` at BOTH marker-publishing call sites, not
  just one (round-17 finding)** — `pr_ops.py` has two independent places
  that interpolate `record.codename` into a published marker:
  `_open_via_provider`'s initial-PR-body `codename` branch, AND
  `refresh_source_attribution`'s managed-comment `codename` branch (used
  on every later push to an already-open PR); today only `is_valid_handle`
  gates the latter, with no `codename_source` check at all, so a legacy
  record with no `codename_source` (which this plan requires to fail
  closed) would still publish its codename on refresh even after the
  initial-path gate is added. Both branches must apply the identical
  gating check (defined next) before treating an already-assigned
  codename as safe to publish — factor the check into one shared helper
  both call sites use, rather than duplicating the condition, so the two
  paths cannot drift out of sync again.
  **The shared helper must take `source_attribution_configured` as an
  explicit input, not just the resolved `attribution` value (round-22
  finding)** — `attribution == "codename"` is identical at runtime whether
  it arrived via an EXPLICIT `pr.source_attribution: codename` config key
  or via the bare, unconfigured implicit default (both produce the same
  Python string), yet the two cases require opposite `codename_source`
  handling: an explicit opt-in is the operator consciously reviewing and
  accepting this repo's vocabulary (publishes a KNOWN provenance
  regardless of whether it's `"built-in"` or `"custom"` — never a
  missing/unknown one; see the round-32 narrowing below, per case (b)
  below), while the bare implicit default
  is exactly the silent-leak scenario this whole effort exists to prevent
  (must still gate on `codename_source == "built-in"`). A helper keyed
  only on `attribution == "codename"` cannot distinguish these — it either
  blocks the safe implicit default's ordinary `"built-in"` case or leaks a
  `"custom"`-sourced codename under the implicit default, one or the
  other. Fix: thread `source_attribution_configured` (`PRConfig`'s
  existing field, `True` only when the raw key was literally present)
  into the shared helper alongside `attribution` and `codename_source`;
  the gating logic is: publish when EITHER (i) `source_attribution` is
  `True` (raw/full marker mode, unaffected by codenames), OR (ii)
  `attribution == "codename"` AND `codename_source == "built-in"` (always
  safe, implicit or explicit), OR (iii) `attribution == "codename"` AND
  `codename_source == "custom"` AND `source_attribution_configured` is
  `True` (a KNOWN custom-sourced codename publishes only under an
  EXPLICIT opt-in, never the bare implicit default). **A `codename_source`
  that is missing or an unrecognized/malformed value NEVER auto-publishes,
  even under an explicit opt-in (round-32 finding, narrows the original
  round-14/22 rule)** — the original rule read explicit opt-in
  (`source_attribution_configured`) as authorizing publication
  UNCONDITIONALLY regardless of `codename_source`, on the theory that an
  operator explicitly opting in has reviewed the repo's CURRENT
  vocabulary; but that reasoning only covers the repo's current config,
  not an individual record's actual provenance — an unbackfilled or
  malformed-`codename_source` record may have been assigned under a
  custom wordlist the repo's config has since dropped or swapped (the
  exact round-10 legacy-drift concern), so blanket-authorizing it under
  explicit opt-in would bypass the round-10 backfill migration's
  fail-closed guarantee entirely and could expose an identifier from a
  vocabulary the operator never actually reviewed. **Fix:** explicit
  opt-in only ever bypasses the ALLOCATION-time distinction between
  `"built-in"` and `"custom"` (i.e. it authorizes a KNOWN custom-sourced
  codename that would otherwise require an implicit default to reject
  it) — it never bypasses provenance itself; a record with missing/
  unknown `codename_source` requires the SAME manual, explicit,
  per-record operator verification the round-10 backfill migration
  already mandates (promoting it to a known `"built-in"`/`"custom"`
  value) before it can ever publish a **codename marker**, explicit or
  implicit opt-in alike (round-34 finding: scoped to codename markers
  only — `source_attribution: True`'s independent raw-marker path never
  consulted `codename_source` before this effort and must not start now;
  an unbackfilled record still publishes the raw marker unaffected). An
  IMPLICIT `codename` default only publishes a `"built-in"`-sourced
  codename, same as before. A record with `codename_source: "custom"`
  under an implicit (not explicit) `codename` default requires the repo
  to explicitly set `pr.source_attribution` (to `codename` or `true`) to
  publish, even if the repo's wordlist config has since reverted to no
  custom wordlist.
  **Any stored value other than the literal string `"built-in"` must be
  treated as unsafe/`"custom"` (round-12 finding)**: `tracking.load_record`
  accepts arbitrary YAML values for record fields with no schema
  enforcement, so the shared helper must check
  `codename_source == "built-in"` to treat a record as safe — never the
  inverted `codename_source != "custom"` shape, which would silently
  treat an unrecognized/malformed stored value (a typo, a future value
  this code doesn't know about, hand-edited YAML) as safe by default.
  Add tests proving, for BOTH `_open_via_provider` and
  `refresh_source_attribution`: (a) a `WorktreeRecord` with
  `codename_source: "custom"`, in a repo whose config NOW has no custom
  wordlist and an IMPLICIT (not explicit) `codename` default, still fails
  closed (the exact legacy-drift scenario the round-8 finding raised); (b)
  a repo with a custom wordlist that HAS EXPLICITLY configured
  `source_attribution: codename` (`source_attribution_configured` is
  `True`) publishes normally for a `WorktreeRecord` with a KNOWN
  `codename_source: "custom"`, including for a pre-existing
  `WorktreeRecord` assigned before this effort shipped and later
  backfilled to a known value; (b2) (round-32 finding) that SAME
  explicit-opt-in repo does NOT publish for a `WorktreeRecord` with a
  MISSING or unrecognized `codename_source` — explicit opt-in narrows the
  allocation-vs-publish distinction, it does not bypass provenance
  verification; (c) a `WorktreeRecord` with `codename_source:
  "built-in"` publishes normally under an IMPLICIT `codename` default; (d)
  a `WorktreeRecord` with an unrecognized stored `codename_source` value
  (neither `"built-in"` nor `"custom"`) fails closed exactly like
  `"custom"` would under an implicit default; (e) the case (a) scenario
  again, but this time also asserting explicitly that an implicit default
  by itself is never sufficient to publish a `"custom"`-sourced record —
  proving the helper reads `source_attribution_configured`, not just
  `attribution`'s value, to decide (a) vs. (b).
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
- [ ] **Freeze each PR's attribution decision (mode AND explicitness) at
  PRRecord-creation time, at EVERY creation site; strictly validate the
  persisted pair; protect it under concurrent-save merge (rounds
  26-32)** — full rationale, gap enumeration, and regression-test
  requirements in
  **[design.md § Per-PR attribution freeze](design.md#per-pr-attribution-freeze-rounds-26-32)**;
  read it before touching any of these sites:
  - Add `PRRecord.attribution_mode`/`PRRecord.attribution_explicit`,
    stamped TOGETHER, ONCE, by one shared helper called at the three
    FRESH-construction sites: `create_pr`'s own construction
    (`pr_ops.py:~868`), `_push_existing_feature`'s fresh-target
    construction (`~2090`), and manual `set-pr`'s bare construction
    (`~1676`). **`tracking.py`'s `_parse_pr_mapping` deserialization
    (`~1393`) must stay STRICT READ-ONLY (round-33 finding: an earlier
    condensed pass lumped it in as a fourth "stamp" site) — it parses an
    EXISTING `PRRecord`'s already-frozen pair back from YAML, never
    re-derives or re-stamps it from current config; calling the shared
    stamping helper there would overwrite a persisted decision on every
    load and reopen the exact retroactive-policy bug this freeze exists
    to close.**
  - The stamp must capture the caller's already-computed EFFECTIVE
    attribution (`want_attribution`), including any per-call
    `--attribution`/`--no-attribution` override, stamped VERBATIM
    (post-widening, an override may itself be `"codename"`, not just a
    bool) — never re-derive from `prcfg` directly (round-31 finding).
  - `tracking.py`'s `_pr_to_yaml_dict` (`~1422`) must ALSO emit both
    fields (round-28 finding) — parsing alone is not durable. **Emit
    `attribution_explicit` whenever `attribution_mode` is non-empty, not
    only when `attribution_explicit` is truthy (round-34 finding)** — a
    `False` explicitness is itself a legitimately-frozen state (e.g. an
    implicit `codename` decision), and omitting it would strand
    `attribution_mode` without its partner on reload, silently
    re-triggering the lazy-backfill freeze.
  - `refresh_source_attribution`/`_open_via_provider` must use the FROZEN
    pair, never live `prcfg.source_attribution`/
    `source_attribution_configured`, for every publish decision on that
    PR's life. **A legacy `PRRecord` predating these fields is lazily
    frozen on its FIRST post-migration touch, from whatever config is
    live at that single moment — never left to fall back to live config
    indefinitely (round-32 finding: the earlier "falls back to live
    config unchanged" framing conflicted with the vision's own
    persistence guarantee)** — see design.md for the full migration spec.
  - `_parse_pr_mapping` must STRICTLY validate the persisted pair (round-30
    finding): `attribution_explicit` via strict boolean check (never
    truthy-coerced); `attribution_mode` against the closed set
    `{"", "false", "true", "codename"}`; a partial pair (only one field
    set) falls back to the empty legacy sentinel — the same
    never-treat-unknown-as-safe discipline round-12 established for
    `codename_source`.
  - **An EXPLICIT `codename` opt-in only bypasses the built-in/custom
    ALLOCATION distinction, never provenance itself (round-32 finding,
    narrows the round-14/22 rule; scope corrected round-34)** — it
    publishes a KNOWN `codename_source: "custom"` record, but a record
    with a MISSING or unrecognized `codename_source` never auto-publishes
    a **codename marker** (`attribution == "codename"`), explicit or
    implicit, until the round-10 manual backfill promotes it to a known
    value. **This rule is scoped to codename markers only (round-34
    finding)** — `source_attribution: True` (the independent raw-marker
    path: worktree id, machine, session, head SHA) never consulted
    `codename_source` before this effort and must not start doing so now;
    an unbackfilled/unknown-provenance record still publishes the full
    raw marker exactly as it always has, unaffected by this codename-only
    fail-closed rule.
  - `_save_record_unlocked` must merge the frozen attribution fields
    PER-ENTRY across the full `WorktreeRecord.prs` list, keyed by a
    stable identity — **a dedicated `pr_id` field is the SOLE identity,
    not `branch` (round-36 finding, replaces the round-35 "branch is
    primary" rule)** — `branch` is not immutable either: the same manual
    `set-pr` correction path that can reassign `number` can ALSO reassign
    `branch` (`pr_ops.py`'s `_set_pr_locked`, no reset tied to either
    mutation), so a stale snapshot could fail to match its own on-disk
    counterpart by branch OR number after a rename. **Fix:** add
    `pr_id: str = ""` to `PRRecord` — a random UUID assigned ONCE at
    entry creation and never touched by any later `branch`/`number`/
    `provider`/`state` correction. Two entries are the SAME PR iff both
    have a NON-EMPTY `pr_id` and the values are equal; an entry with no
    `pr_id` on either side never matches anything (extends round-35's
    "no established identity" case from empty-branch-and-no-number to
    the general no-`pr_id` state). **Backfill `pr_id` INLINE in
    `_save_record_unlocked`'s existing merge step, not via a separate
    "shipping-time" migration pass (round-37 finding, corrects the
    round-36 text)** — this repo's config-migration framework
    (`config_migrations.py`) is scoped to machine-local config and
    explicitly excludes tracking YAML, so a claimed "one-time pass at
    shipping time" has no actual entry point to invoke it. Every
    `save_record` call already acquires the record lock and runs
    `_save_record_unlocked` against a freshly-loaded on-disk copy — add
    one step there, before the identity-matching/merge logic: for each
    `current.prs` entry lacking a `pr_id`, generate and persist one right
    then, as part of that same locked write. This piggybacks on code
    that already runs on every write, closing the race without a
    separate migration mechanism — never just the
    single `.pr` active-PR accessor (round-32 finding: `.prs` supports
    serial/parallel PRs, and a merge keyed on the active-PR property
    alone cannot protect a frozen pair on a non-active entry). Add a
    `pr_revision` counter PER entry (following the
    `profile_assignment_revision` pattern) with explicit YAML load/save
    wiring (round-31 finding: an in-memory-only counter reloads as `0`
    in another process and defeats the merge guard), bumped every time
    that entry's attribution pair is stamped, merging each `current.prs`
    entry into the matching `record.prs` entry (or appending it, if
    unmatched) whenever `current`'s `pr_revision` is strictly higher.
  - See the Validation Plan below for the full regression-test matrix
    (retroactive mode/explicitness change, manual `set-pr`, per-call
    override freeze including a `"codename"`-valued override,
    strict-parsing edge cases, explicit-opt-in provenance narrowing,
    `pr_revision` round-trip, per-entry stale-writer merge across
    parallel PRs, legacy-PR lazy-freeze-at-first-touch).
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
  correctly still checks regardless of marker mode). **The `None`-branch
  remedy string must also change, not just the label (round-18 finding):**
  it currently reads "migrate to `source_attribution: true` ... or
  `codename`," but an absent key already IS implicitly `codename` under
  the new default, so "migrate to codename" is now a no-op that leaves the
  risky `head_pattern` token in place while sounding like a fix. Give the
  `None` branch the identical mode-aware remedy the existing `"codename"`
  branch already uses: codename mode only protects the PR-body marker, not
  the branch name, so the only real remedies are `source_attribution:
  true` (if this repo accepts full exposure) or removing the risky token
  from `head_pattern` — drop the now-inapplicable "or codename" clause
  from the `None` branch entirely.
- [ ] **Preserve existing no-finding behavior for a safe `head_pattern`
  (round-20 finding)** — this is not an open design question:
  `audit_source_attribution_risk` already returns `[]` immediately
  whenever `head_pattern_leak_risk(head_pattern)` is empty, regardless of
  `source_attribution`'s value (`providers/attribution.py:275-279` —
  verified in source), so an absent-key repo with a SAFE `head_pattern` is
  already NOT flagged today and must continue not being flagged after this
  effort's label/remedy-text changes above. Add a regression test proving
  this: an absent-key repo with a safe (non-leaking) `head_pattern`
  produces zero findings both before and after this effort's changes.
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
- [ ] **This repo (`copilot-extensions`): REMOVE the explicit
  `source_attribution: false` key entirely from `.agent-worktrees/config.yaml`
  — this is the chosen migration, not "remove or flip to codename" (round-26
  finding: the two are NOT policy-equivalent under this plan's gates).**
  Removal lets the key resolve through the implicit default, which keeps
  every existing `WorktreeRecord` on this repo fail-closed exactly as
  before (an unbackfilled/legacy record — permanently `codename_source:
  "custom"` per the round-10 rule — still cannot publish under the
  implicit default). Setting an EXPLICIT `source_attribution: codename`
  instead would set `source_attribution_configured: True`, which (per the
  round-22 publish-time gate) authorizes ANY `codename_source` to publish,
  including an unknown/legacy one this repo has never manually verified —
  a strictly riskier choice with no corresponding benefit for a repo that
  has no custom wordlist to begin with. Update the adjacent comment
  (currently describes only the raw-marker risk: "Hidden source markers
  carry raw machine/worktree/session identifiers. This public repo must
  never publish them; closed-circuit repos may opt in.") to also explain
  the codename mode this repo now relies on via the implicit default, and
  why removal (not explicit `codename`) is this repo's own migration
  choice.
- [ ] Any other repo relying on the codename feature: re-evaluate its
  `source_attribution` setting against the new default (a currently-absent
  key silently changes behavior; an explicit `false` becomes a genuine,
  stronger opt-out statement rather than "restating the old default" --
  see Context). Apply the SAME removal-over-explicit-codename preference
  established above unless that repo has actually performed the
  per-record `codename_source` verification the explicit-`codename` choice
  would require to be safe. This is generic guidance any driver applies to
  their own fleet; the specific inventory is out of scope for this public
  record.

### Phase 4 — Live validation
- [ ] Open a real PR in this repo after Phase 3's config change and confirm
  it now carries the `<!-- agent-worktrees:source codename=... -->` marker
  (this effort's own landing PR is a natural candidate).
- [ ] Confirm the private downstream repo's next merged PR still carries
  the full raw marker unchanged (regression check, not a new test — just
  observe the next real merge).

## Validation Plan

- [ ] Unit (round-15 finding: allocation-time, not publish-time; scoped
  round-22): a **PR-active** (`pr.enabled: true`) repo with a custom
  `codename.wordlist_path` configured AND an absent/default
  `source_attribution` fails a **new codename allocation** closed (a
  config validation error at config-load/allocation time, not a silent
  `false`-fallback and not a leaked marker) — the single highest-priority
  test in this effort, since it's the one silent-leak scenario the default
  flip could introduce. This test must NOT touch an existing
  `WorktreeRecord`'s publish path — see the next bullet and the
  `codename_source: "built-in"` bullet below for that.
- [ ] Unit (round-22 finding): the IDENTICAL custom-wordlist/omitted-
  `source_attribution` config, but with `pr.enabled` false/unset, allocates
  a **new** codename successfully with no validation error — proving the
  allocation-time gate is scoped to PR-active repos only and does not
  regress ordinary local-only (`codename.wordlist_path` for Picker
  ergonomics, no PR features in use) `create` usage.
- [ ] Unit (round-21 finding): the SAME allocation-time policy violation,
  exercised through the **normal `create` path**
  (`_create_worktree_core`), not just the paired-knowledge path — a
  `create` against a repo with a custom wordlist and omitted
  `source_attribution` fails outright, before any harness worktree,
  branch, or tracking record exists. This is a distinct code path from the
  round-12 paired-knowledge preflight test and from the round-15 test
  above (which only exercises the shared validation helper directly, not
  `_create_worktree_core`'s own call site) — a regression in
  `_create_worktree_core`'s own preflight wiring must fail this test even
  if the shared helper itself is correct.
- [ ] Unit: a repo with a custom `codename.wordlist_path` configured AND an
  EXPLICIT `source_attribution: codename` (`source_attribution_configured`
  is `True`) publishes normally — including for a pre-existing
  `WorktreeRecord` whose codename was assigned before this effort shipped
  and carries `codename_source: "custom"` — proving the shared
  publish-time helper reads `source_attribution_configured`, not just
  `attribution == "codename"`, to grant this explicit-opt-in exception
  (round-22 finding: the two are otherwise indistinguishable at runtime).
- [ ] Unit (legacy-record gap, round-8 finding, sharpened round-22): a
  `WorktreeRecord` with `codename_source: "custom"`, in a repo whose
  CURRENT config has no custom wordlist configured (the config changed
  after assignment) AND whose `source_attribution` is the IMPLICIT
  (unconfigured) `codename` default — not an explicit one — still fails
  closed. This must be run alongside the previous bullet's EXPLICIT case
  to prove the helper actually branches on `source_attribution_configured`
  rather than coincidentally passing both by always requiring
  `codename_source: "built-in"`.
- [ ] Unit: a `WorktreeRecord` with `codename_source: "built-in"` publishes
  normally under an IMPLICIT `codename` default, regardless of the repo's
  current wordlist config.
- [ ] Unit: an existing `WorktreeRecord` with no `codename_source` recorded
  (predates this effort) is permanently treated as `"custom"` (fails
  closed) — never auto-promoted to `"built-in"` by any automated pass,
  regardless of the owning repo's current wordlist config (round-10
  finding: no config-inferred backfill).
- [ ] Unit (round-26 finding, extended round-27): open a PR under one
  `source_attribution` value (e.g. `false`), change the repo's config to
  a DIFFERENT value (e.g. `codename`), then push again — the marker
  published/refreshed on that second push still reflects the ORIGINAL,
  frozen `PRRecord.attribution_mode`, not the new live config. Cover both
  directions (an added marker on a config that used to publish nothing,
  and vice versa).
- [ ] Unit (round-32 finding, corrects the round-26 entry's "falls back
  to live-config unchanged" framing): a legacy `PRRecord` with no stored
  `attribution_mode`/`attribution_explicit` (predates these fields) is
  lazily frozen on its FIRST `refresh_source_attribution`/
  `_open_via_provider` touch, computed from whatever config is live at
  that single moment — then the repo's config changes to a DIFFERENT
  value before a SECOND refresh on that same PR — the second refresh
  still uses the value frozen at the FIRST touch, not the newly-changed
  config, proving the lazy migration itself does not reopen the
  retroactive-change window.
- [ ] Unit (round-27 finding): open a PR under an IMPLICIT `codename`
  default against a `"custom"`-sourced (or unknown-provenance) record —
  correctly suppressed at open per the round-22 gate — then add an
  EXPLICIT `source_attribution: codename` to the repo's config and push
  again: the marker STAYS suppressed on refresh, proving
  `attribution_explicit` (not just `attribution_mode`) is frozen at
  creation time and never re-derived from live
  `source_attribution_configured`.
- [ ] Unit (round-27 finding): attach a PR via manual `set-pr` (a path
  that never calls `_open_via_provider` at all) against a repo/record
  combination that would be suppressed under the implicit default, then
  run `push-changes` (which invokes `refresh_source_attribution`) —
  asserts the manual-attach `PRRecord` was ALSO stamped with a frozen
  `attribution_mode`/`attribution_explicit` pair by the shared helper
  (not left empty/falling back to live config), proving the freeze
  helper is wired into every `PRRecord` creation site, not only the
  auto-open path.
- [ ] Round-trip (round-28 finding): stamp a `PRRecord` with a known
  `attribution_mode`/`attribution_explicit` pair, `save_record` it,
  `load_record` it back, assert both fields are unchanged — proving
  `_pr_to_yaml_dict` actually emits them (not just `_parse_pr_mapping`
  parsing them) and a legacy record with neither field present still
  round-trips to empty/`False`, not a crash.
- [ ] Round-trip (round-34 finding): stamp a `PRRecord` with
  `attribution_mode="codename"`/`attribution_explicit=False` (an
  IMPLICIT codename decision — explicitness legitimately `False`),
  `save_record`/`load_record` it back — assert `attribution_explicit`
  round-trips as `False`, NOT absent, proving `_pr_to_yaml_dict` emits it
  whenever `attribution_mode` is set rather than omitting it because its
  own value is falsy (which would strand `attribution_mode` without its
  partner and silently re-trigger the lazy-backfill freeze on next
  load).
- [ ] Unit (round-34 finding, matcher corrected round-36): a `create_pr`
  flow saves a `PRRecord` with `number=None`/a set `branch`/an assigned
  `pr_id`; a concurrent process observes the provider assigning that PR
  a `number` and stamps the frozen attribution pair (bumping
  `pr_revision`) onto the now-numbered on-disk entry; a stale in-memory
  snapshot still holds the pre-number version of the same entry —
  `save_record`ing the stale snapshot must MERGE onto the numbered entry
  (not append a duplicate), proving the identity rule matches across the
  `number=None` → provider-assigned-`number` transition via the shared
  `pr_id`.
- [ ] Unit (round-35 finding, matcher corrected round-36): a stale
  in-memory snapshot holds an entry with `pr_id` set, `number=7`,
  `branch="feature-x"`; a concurrent manual `set-pr` correction changes
  the ON-DISK entry's `number` to `8` while its `branch` stays
  `"feature-x"` (leaving `pr_id` unchanged), then stamps the frozen
  attribution pair — the stale save must still MERGE onto the
  renumbered entry (matched via `pr_id`), proving a `number` correction
  alone never causes a false non-match.
- [ ] Unit (round-35 finding): TWO independent, freshly-created blank
  `PRRecord`s (both `number=None`/`branch=""`/no `pr_id` yet assigned) —
  saving a stale snapshot of one must NOT merge onto the other merely
  because they share the same empty `branch`/absent `number`/absent
  `pr_id`, proving entries with no established identity at all are
  never treated as a match.
- [ ] Unit (round-36 finding): a stale in-memory snapshot holds a
  post-migration entry with a real `pr_id` set, `number=7`,
  `branch="feature-x"`; a concurrent manual `set-pr` correction changes
  the ON-DISK entry's `branch` to `"feature-y"` AND its `number` to `8`
  (leaving `pr_id` untouched), then stamps the frozen attribution pair
  — the stale save must still MERGE onto the renamed-and-renumbered
  entry (matched via `pr_id`, not `branch`/`number`), proving `pr_id` —
  not `branch` — is the identity that survives a branch rename (the
  gap round-35's "branch is primary" rule left open).
- [ ] Unit (round-36 finding, mechanism corrected round-37): saving a
  `WorktreeRecord` whose on-disk `PRRecord` has no `pr_id` stamps a
  non-empty `pr_id` onto that entry, INLINE as part of
  `_save_record_unlocked`'s existing locked write (not a separate
  migration pass) — and a SECOND save afterward is idempotent: it does
  not overwrite the already-assigned `pr_id` with a new random value.
- [ ] Unit (round-37 finding): a legacy `PRRecord` (no `attribution_mode`
  stored) is touched by `refresh_source_attribution` while the repo's
  LIVE `source_attribution` is `False` — assert the pair is frozen to
  the false state on THAT touch (not left unmigrated because the
  function returned early), then the repo's config changes to
  `codename` and the same PR is touched again — the marker STAYS
  suppressed on the second touch, proving the freeze runs before the
  live-config early return rather than being deferred until whichever
  later touch happens to occur under a truthy config.
- [ ] Unit (round-34 finding): a `WorktreeRecord` with an unbackfilled
  (missing/unknown) `codename_source`, in a repo with
  `source_attribution: True` (the raw-marker mode) — the PR still
  publishes the full raw marker exactly as it always has, proving the
  round-32 provenance fail-closed rule is scoped to codename markers
  only and does not regress the independent raw-marker path.
- [ ] Unit (round-30 finding): a hand-edited/malformed
  `attribution_explicit: "false"` (a truthy string, not the boolean
  `false`) does NOT authorize publication — `_parse_pr_mapping` must
  reject it to the safe empty-legacy-sentinel fallback, not coerce it
  truthy.
- [ ] Unit (round-30 finding, corrected round-33): an unrecognized
  `attribution_mode` value (not one of `""`/`"false"`/`"true"`/
  `"codename"`) is migrated via the same one-time lazy-backfill freeze as
  a missing value (never perpetually re-derived from live config), not
  read as an authorized mode.
- [ ] Unit (round-30 finding, corrected round-33): a `PRRecord` with only
  ONE of `attribution_mode`/`attribution_explicit` set (the other
  absent/empty) is migrated the same one-time lazy-backfill way for BOTH
  fields together — a partial pair never authorizes publication using
  just the one field that is present, nor is it perpetually re-derived
  from live config.
- [ ] Unit (round-30 finding, corrected round-32): stamp a `PRRecord`
  entry's frozen `attribution_mode`/`attribution_explicit` pair under the
  record lock (bumping that entry's `pr_revision`), then `save_record` a
  STALE in-memory `WorktreeRecord` snapshot captured before that stamp —
  the stale save must not erase the freshly-frozen entry, proving
  `_save_record_unlocked` merges `WorktreeRecord.prs` per-entry, keyed by
  identity, from the current on-disk record by that entry's
  `pr_revision`, the same way it already merges `codename`/
  `codename_source` (round-11) and `profile_assignment_revision`.
- [ ] Unit (round-32 finding): a `WorktreeRecord` with TWO parallel `prs`
  entries — stamp the frozen attribution pair on entry B (bumping ONLY
  B's `pr_revision`) while a stale in-memory snapshot holds BOTH entries
  unfrozen, then `save_record` the stale snapshot — entry B's freeze
  survives even though the snapshot's `.pr` (active-PR) property
  resolves to entry A, proving the merge protects the full `prs` list by
  identity, not just the single active-PR accessor.
- [ ] Round-trip (round-31 finding): bump a `WorktreeRecord`'s
  `pr_revision` and `save_record` it, then `load_record` the same file
  back into a FRESH object (a different Python object, not a mutated
  reference) — assert `pr_revision` survives as the same non-zero value,
  proving the counter has explicit YAML load/save wiring and does not
  reload as `0`, which would silently defeat the round-30 stale-writer
  merge guard in any process other than the one that stamped it.
- [ ] Unit (round-31 finding): call `create_pr` with an explicit
  `attribution=False` override against a repo whose config would
  otherwise publish a `"codename"` marker — assert the stamped `PRRecord`
  records `attribution_mode="false"`/`attribution_explicit=True` (not the
  config's `"codename"`/live-configured value), then run a SUBSEQUENT
  `push-changes`/`refresh_source_attribution` on that same PR with no
  further override — the marker stays suppressed, proving a one-shot
  per-call override freezes exactly like a config-derived decision
  rather than reverting on the very next push.
- [ ] Unit (round-31 finding): call `create_pr` with an explicit
  `attribution="codename"` override (post-widening) against a repo whose
  config default is `False`/unconfigured — assert the stamped pair
  records `attribution_mode="codename"`/`attribution_explicit=True`,
  proving the override's own value is stamped verbatim rather than
  coerced to only `True`/`False`.
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
- [ ] Unit (round-26 finding): `resume` and `status --write`, each
  independently, against a PR-active repo with a custom wordlist and
  omitted `source_attribution`, run on a record with no codename yet —
  each fails with the policy error rather than silently allocating a
  `codename_source: "built-in"` codename, proving the gate centralized
  inside `ensure_codename` itself protects EVERY caller, not just
  `create`/paired-knowledge/`create-pr`.
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
- [ ] Unit (round-16 finding, preflight added round-22): `create-pr`
  against a PR-active custom-wordlist repo with an omitted
  `source_attribution`, run on a worktree record with no codename yet —
  asserts the whole `create-pr` command fails with the policy error
  **before any squash/push has happened** (proving the new preflight, not
  just the late re-raise, is what actually catches this — the ordinary
  case). Add a second case simulating the narrow residual race (policy
  becomes violated only after the preflight passes, so
  `_open_via_provider`'s lazy-backfill actually calls `ensure_codename`
  and hits the re-raise instead): asserts `create-pr` still fails via the
  `except Exception: pass` re-raising `CodenameAttributionPolicyError`
  instead of swallowing it into a silent skip-the-marker success, with the
  already-pushed branch/record left in place and no automatic rollback —
  the `create-pr` analog of the other preflights' race tests. Add a third
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
- [ ] **Unit (round-22 finding): `_parse_pr` with an EXPLICIT
  `source_attribution: codename` parses to `"codename"` AND
  `source_attribution_configured` is `True`** — this is the critical case
  the existing three bullets above miss entirely: an absent key ALSO
  parses to `"codename"` (the new implicit default), so a test suite that
  only distinguishes `false`/`true`/absent leaves the explicit-`codename`
  vs. implicit-`codename` boundary — the exact distinction the publish-time
  gate's `source_attribution_configured` check depends on — completely
  unverified. A regression collapsing this case into the absent-key case
  (or vice versa) would either silently block the main allowed opt-in
  (explicit `codename` on a custom-wordlist repo) or silently let an
  implicit default masquerade as an explicit one and leak a `"custom"`-
  sourced codename. Cover this at both the parser level AND with a real
  allocation: a repo with a custom wordlist and this EXPLICIT
  `source_attribution: codename` config successfully allocates a new
  codename (the allocation-time gate in Phase 1 does not block it, since
  the key is explicitly present, not omitted).
- [ ] Regression: `source_attribution_configured` is `False` for every
  absent-key case above and `True` for every explicit case (`false`,
  `true`, AND `codename`, all three count as "configured"), proving the
  fields never drift.
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
- [ ] **Integration, refresh path (round-17 finding):** the same
  omitted-`source_attribution` scenario, run through
  `refresh_source_attribution` (the later-push managed-comment path, a
  separate function from `_open_via_provider`) — asserts a marker IS
  published there too, and separately asserts that a `WorktreeRecord` with
  `codename_source: "custom"` (or unset) does NOT get its codename
  published on refresh, closing the exact gap the finding raised: an
  initial-path-only fix would leave the refresh path publishing a
  should-be-blocked codename on the PR's second and later pushes.
- [ ] Unit: `audit_source_attribution_risk`'s finding text for an absent
  key says "codename," not "false," AND its remedy text no longer suggests
  migrating to `codename` (round-18 finding) — asserting only `true` or
  dropping the risky `head_pattern` token are offered as remedies for the
  absent-key case.
- [ ] Full existing `test_config.py`/`test_pr_ops.py`/`test_providers.py`
  suite passes after the default-value test-fixture sweep (Phase 1's audit
  item) — no test silently still asserts the old default.
- [ ] Live: this effort's own landing PR in this repo (opened after Phase
  3's config change lands) carries a codename marker — the end-to-end
  proof the whole point of this effort actually works. **This validation
  target must NOT be the current worktree carrying this very plan
  (round-18 finding):** its `WorktreeRecord` predates `codename_source`
  existing, and the fail-closed migration rule above requires an
  unbackfilled record to permanently suppress implicit publication — so
  it would never carry a marker even after Phase 1 lands, making it
  useless as proof. Use either (a) a NEW worktree created after Phase 1
  ships (its record naturally gets `codename_source: "built-in"` at
  assignment time, the ordinary path), or (b) an explicit, manually
  verified per-record `codename_source: "built-in"` edit on an existing
  worktree whose entire codename-assignment history has been checked
  against the owning repo's wordlist-config git history — never an
  unverified existing record.
- [ ] Live (regression, observational): the next merge on the private
  downstream repo after this effort lands still carries the full raw
  marker unchanged.

## Proposal

_Pending._

## Journal

> Dated, append-only running log of the effort. Full round-6 through
> round-36 history lives in **[journal.md](journal.md)** to keep this
> README a navigable map.



### 2026-09-20 — Plan-review round 37 fixes

- Confirmed: round-36's `pr_id` identity fix verified correct — this
  round's review lists it under "Resolved since last review."
- Two new genuine findings, both exposing that round-36's design
  described mechanisms with no actual place to run:
  1. **No executable hook for the `pr_id` backfill:** round-36 called for
     a "one-time migration pass at shipping time," but this repo's
     config-migration framework (`config_migrations.py`) is scoped to
     machine-local config and explicitly excludes tracking YAML — the
     described pass had no entry point that would ever invoke it. Fixed:
     backfill `pr_id` INLINE inside `_save_record_unlocked`'s existing
     merge step, which already runs under the record lock on every
     single `save_record` call — no separate migration mechanism needed.
  2. **The legacy-freeze-on-first-touch design (round-32) can't fire
     for a repo whose live `source_attribution` starts `False`:**
     `refresh_source_attribution` returns immediately
     (`if not config.default_repo.pr.source_attribution: return ""`,
     `pr_ops.py:1199`) before ever reaching the freeze logic. A legacy
     PR first touched under a false config would stay unmigrated
     indefinitely; if the config later changes to `codename`, that LATER
     touch (not the true first one) would be lazily frozen instead,
     silently adopting the newer policy. Fixed: the freeze check must
     run BEFORE the live-config early return, unconditionally — freezing
     to the false state when that's what's live, so a later config
     change correctly finds the pair already frozen.
- One permanently-stale carryover persists (the documentation-impact
  statement finding, `#discussion_r4057190221`, unchanged at anchor
  `f2b538c47` for fourteen rounds straight — not re-edited again).


