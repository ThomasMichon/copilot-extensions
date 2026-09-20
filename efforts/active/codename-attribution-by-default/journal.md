# Journal - codename-attribution-by-default

Dated, append-only running log of the effort.

Part of the [codename-attribution-by-default effort](README.md).

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


### 2026-09-20 — Plan-review round 17 fixes

- One finding on the round-16 head, verified against the actual
  `pr_ops.py` source: the plan's publish-time `codename_source` gate
  (round-8/9/12) and its Validation Plan integration test both only
  addressed `_open_via_provider`'s initial-PR-body marker path.
  `pr_ops.py` has a SECOND, independent marker publisher —
  `refresh_source_attribution`, which republishes the managed attribution
  comment on every later push to an already-open PR — whose `codename`
  branch currently checks only `is_valid_handle(record.codename)`, with no
  `codename_source` check at all. Left as specified, the initial-path fix
  alone would leave a legacy or `"custom"`-sourced record's codename
  publishing anyway on the PR's second and later pushes, defeating the
  fail-closed guarantee this effort exists to add. Fixed by requiring both
  marker-publishing call sites to share one `codename_source == "built-in"`
  helper rather than duplicating (and risking re-drifting) the condition,
  and adding a matching refresh-path Validation Plan test alongside the
  existing initial-path one.

### 2026-09-20 — Plan-review round 18 fixes

- Five findings on the round-17 head, each verified against actual
  source/repo convention before editing:
  1. **Typing gap:** `create_pr`, `_finish_auto_open`, and
     `_push_existing_feature` all still annotate their `attribution`
     override parameter `bool | None`, but `create_pr` already threads
     `prcfg.source_attribution` (typed `SourceAttribution`) through all
     three on the unconfigured/default path — now routinely a string.
     Added a Phase 1 item to widen all three to `SourceAttribution |
     None`, matching `PRConfig`'s own field type.
  2. **Live-validation gap:** the "this effort's own landing PR carries
     a marker" live-validation target would silently fail: verified this
     very worktree's `WorktreeRecord` predates `codename_source` and the
     plan's own fail-closed migration rule requires an unbackfilled
     record to suppress implicit publication permanently, so the
     CURRENT worktree can never satisfy this check. Narrowed the
     Validation Plan item to require either a NEW post-Phase-1 worktree
     or an explicit, manually-verified `codename_source: "built-in"`
     edit.
  3. **Malformed Markdown (previously missed):** the round-11
     concurrent-save-merge checklist item had lost its `- [ ] **Merge
     ...` opening line during an earlier edit, leaving an orphaned
     continuation paragraph starting mid-sentence with an unmatched
     closing `**` and no checklist marker. Restored the missing prefix.
  4. **Audit-remedy gap:** `audit_source_attribution_risk`'s `None`-branch
     remedy text tells an absent-key repo to "migrate to ... codename" —
     verified this is now a no-op once codename is the implicit default,
     and gave the `None` branch the same mode-aware remedy the existing
     `"codename"` branch already uses (drop the now-inapplicable clause).
  5. **Structural (previously missed):** the effort README had grown to
     1,137 lines, mixing detailed design rationale and a 12-round journal
     into the coordination document, against `efforts/README.md`'s
     "extract substantial phase designs/inventories into sibling
     documents" convention. Split the Custom-wordlists/Downstream-effects
     design rationale into **design.md** and the round-6 through round-16
     journal history into **journal.md**, leaving this README as a
     navigable summary with links — reduced from 1,137 to 746 lines.


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
