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
  `codename_source == "built-in"` check before treating an already-
  assigned codename as safe under the implicit default — factor the check
  into one shared helper both call sites use, rather than duplicating the
  condition, so the two paths cannot drift out of sync again. A record
  with `codename_source: "custom"` requires the same explicit
  `pr.source_attribution` opt-in as a currently-custom-wordlist repo, even
  if the repo's config has since reverted to no custom wordlist. **Any
  stored value other than the literal string `"built-in"` must be treated
  as unsafe/`"custom"` (round-12 finding)**: `tracking.load_record`
  accepts arbitrary YAML values for record fields with no schema
  enforcement, so the shared helper must check
  `codename_source == "built-in"` to treat a record as safe — never the
  inverted `codename_source != "custom"` shape, which would silently
  treat an unrecognized/malformed stored value (a typo, a future value
  this code doesn't know about, hand-edited YAML) as safe by default.
  Add tests proving, for BOTH `_open_via_provider` and
  `refresh_source_attribution`: (a) a `WorktreeRecord` with
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
> round-18 history lives in **[journal.md](journal.md)** to keep this
> README a navigable map.

### 2026-09-20 — Plan-review round 19 fixes

- Two findings on the round-18 head, verified against actual repo
  convention:
  1. The round-18 typing-fix bullet asked for a "type-check assertion,"
     but checked `TESTING.md`: this repo's documented Python validation is
     `ruff check --select F,E9` (pyflakes/syntax only) plus pytest — no
     mypy/pyright gate exists, so that acceptance criterion was
     unverifiable as written. Reworded to name the actual gate (and note
     it does NOT itself catch this class of mismatch) and replaced the
     criterion with a concrete runtime integration test: `create_pr` with
     no explicit override, against a `"codename"`-resolved repo, must
     propagate the string through `_finish_auto_open`/
     `_push_existing_feature` and produce a published marker.
  2. The round-18 journal entry's own finding count was wrong ("Four
     findings" against a five-item list, including the structural
     doc-split finding) — corrected to "Five." Also moved the round-18
     journal entry itself into **journal.md** at this pass (it had been
     left in the README as the "most recent" entry per the established
     one-entry-in-README pattern; this entry now takes that place).
