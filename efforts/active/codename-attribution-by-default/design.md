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
forever fixed at assignment except for the repo's live
`source_attribution_configured` state, which is read at publish time).
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

