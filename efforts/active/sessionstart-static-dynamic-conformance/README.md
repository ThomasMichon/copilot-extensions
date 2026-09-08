# SessionStart Static/Dynamic Content Conformance

- **Slug:** `sessionstart-static-dynamic-conformance`
- **Repo:** copilot-extensions (primary) + aperture-labs (consumer-side audit)
- **Branch(es):** `worktree/lambda-core-win-20260906-234309-1aa0`
- **Created:** 2026-09-08
- **Status:** Active
- **Vision:** vision-closing on the `session-scoped-dynamic-guidance` pattern
  (`docs/patterns/session-scoped-dynamic-guidance.md`) and the harness-guidance
  intent it codifies: hookless-safe, budget-bounded, provenance-locked ambient
  guidance. This effort enforces a rule the pattern already implies but never
  mechanically checks.
- **Umbrella issue:** ThomasMichon/copilot-extensions#2256
- **Related work:** successor/sibling of the now-archived
  `custom-context-aggregator-retirement` (copilot-extensions, still **Active**
  with 2 remaining launch-path validation items — see correction note in the
  Journal below) and `copilot-context-injection-decontamination` (aperture-labs,
  private, archived Done) effort pair, which established the static-pointer +
  exact-session-guidance-file baseline this effort now audits for actual
  conformance.

## Guiding Intent

The `session-scoped-dynamic-guidance` pattern says a plugin's ambient guidance
should split into **two** pieces: a checked-in static instruction file
(reviewed once, versioned, provenance-locked) plus a `sessionStart` hook that
supplies **only the facts that cannot be known until the session exists** —
the session/repo/cwd-specific state. The prior decontamination effort proved
the pattern's *shape* is correct and retired the old custom aggregator, but it
never mechanically verified that every plugin's `sessionStart` hook actually
**obeys** the split. It checked hook *output-freedom classification*, budget,
and lock provenance — not whether a hook's live-emitted text is itself static
prose that should have been a checked-in file instead.

This effort closes that gap: sweep every plugin's `sessionStart` hook (and any
sibling hook that emits `additionalContext`), classify each emitted
line/paragraph/field as **STATIC** (identical text regardless of session,
cwd, repo, or worktree state) or **DYNAMIC** (a fact that genuinely varies —
current worktree id, active effort binding, related-repo resolution, resolved
binary path, etc.), and correct any hook found emitting static content on a
recurring basis. The target end state, per plugin: the hook's `additionalContext`
payload contains **only** dynamic facts plus the minimal explanatory
header/sub-text needed to frame them (e.g. "State-root: X" or a JSON schema
label) — never restatable prose, command syntax, or policy that could instead
live in a static, checked-in `instructions/*.instructions.md` file.

## Context

This surfaced mid-session while closing the `copilot-context-injection-decontamination`
effort (aperture-labs). Investigating "what happened to the worktree-conduct
instructions file" turned up a concrete counter-example:

- `agent-worktrees`'s `worktree-conduct.md` and `account-conduct.md`
  (`plugins/agent-worktrees/scripts/conduct/*.md`) are **100% static text** —
  literal command names (`agent-worktrees status`, `agent-worktrees finalize`,
  `agent-worktrees repos gh`), no placeholders, no session-specific
  substitution anywhere in either file.
- Yet both are delivered through the live `session-conduct` `sessionStart`
  hook (`agent_worktrees/conduct.py`, invoked via `hook_client.py sessionStart`)
  as `additionalContext`, re-emitted on **every single session**, rather than
  checked in as a static instructions projection the way `ai-attribution` and
  (per its own `session-context.json`, `sessionStart.context: "none"`)
  `agent-worktrees`'s own compatibility manifest already claim to prefer.
- The hook *does* legitimately inject real dynamic facts alongside those
  fragments — `AW_CONDUCT_DEFINITION` (state-root definition),
  `AW_CONDUCT_RELATED` (cross-repo related-guidance resolution), and
  `AW_CONDUCT_HISTORY` (this worktree's effort binding / succession chain) —
  but the static prose rides along with them for no architectural reason; it
  was migrated to the hook (per `dotfiles#1054`/`dotfiles#1053`) specifically to keep
  the *dynamic* additions fresh, and the static half came along by omission,
  not by design.
- By contrast, `plugins/agent-worktrees/scripts/emit-command-catalog.{sh,ps1}`
  is the pattern working *correctly*: it emits **only** a tiny JSON envelope
  resolving the live `argv[0]` path for the installed binary — nothing else —
  because that path genuinely cannot be known statically (it depends on the
  local install layout).

Given the marketplace has at least 19 plugins with a `hooks.json` +
`session-context.json` pair (`agent-bridge`, `agent-codespaces`,
`agent-containers`, `agent-dispatch`, `agent-index`, `agent-logger`,
`agent-machines`, `agent-mcp`, `agent-ssh`, `agent-vault`, `agent-worktrees`,
`ai-attribution`, `budget-guidance`, `context-handoff`,
`copilot-extensions-harness`, `customizing-copilot`, `delegation-guidance`,
`efforts`, `harness-knowledge`, `visions`, `wsl-setup` — some without hooks;
exact roster to be confirmed in Phase 0), the `agent-worktrees` conduct case is
unlikely to be the only instance. `ai-attribution` was spot-checked in this
same session and is clean (`sessionStart.context: "none"`, no
`additionalContext` emission at all — everything lives in its checked-in
`publication-safety.instructions.md` / `session-guidance.instructions.md`
projections).

The `customizing-copilot` skill's `reviewing-customizations` skill already
carries the methodology for this class of audit
(`session-guidance-conformance.md` runbook, **Mode B: Conform a Plugin
Suite**) — this effort is the first full execution of Mode B across the
*entire* marketplace roster, not just the plugins the decontamination effort
touched directly.

## Request

Operator's verbatim ask (this session):

> Do thorough sweeps for all sessionStart hooks and their injected contexts,
> and analyze how static vs dyanmic any part is. Static parts get checked in,
> only dynamic parts and their explanatory headers and sub-text get put in
> session state via sessionStart.

Followed by, after the operator noted the just-closed decontamination effort
was "the point of this effort, or so I thought":

> Make a new effort to re-guide it.

## Plan

### Phase 0 — Inventory and umbrella tracking
- [ ] Enumerate every plugin with a registered `sessionStart` hook (`hooks.json`)
  and/or a `session-context.json` classification manifest across the full
  marketplace catalog (`.github/plugin/marketplace.json`), not just the
  roster a given adopting repo happens to enable. Confirm the ~19-plugin list
  above and record the exact source (script vs. declared manifest) each hook
  resolves to.
- [x] File the umbrella GitHub issue on `ThomasMichon/copilot-extensions` (and
  link it here) before starting Phase 1 execution, per this repo's own
  `planning-efforts` tracking convention. **2026-09-08:** filed as
  ThomasMichon/copilot-extensions#2256 (see Umbrella issue field above).
- [ ] Land this effort README through copilot-extensions' own review/contribution
  gate before executing (see `contributing-to-copilot-extensions`), mirroring
  the "propose before you do" rule the decontamination effort's cross-repo work
  followed.

### Phase 1 — Sweep and classify every hook's emitted context
- [x] For each plugin from Phase 0, capture the hook's actual
  `additionalContext` output (or confirm `sessionStart.context: "none"`) across
  representative session states: fresh worktree, resumed worktree, no active
  effort, active effort bound, related-repo present/absent, hookless/App path.
  **2026-09-08:** completed for all 14 hook-registering plugins via three
  parallel read-only sweeps; see
  [sweep-findings.md](sweep-findings.md) for the full per-plugin breakdown.
  (Representative-session-state variation, e.g. active-effort-bound vs. not,
  was inferred from source tracing rather than literally re-run across live
  sessions for every plugin — flagged as a residual gap, not blocking.)
- [x] Classify every distinct line/paragraph/field of each capture as
  **STATIC** (byte-identical regardless of session/cwd/repo/state — a
  candidate for a checked-in instructions projection) or **DYNAMIC** (varies
  with session, cwd, repo, worktree, or install-layout state — a legitimate
  hook responsibility). Record the classification in a per-plugin table in a
  sibling sub-doc (`sweep-findings.md`) rather than inline here, per the
  effort schema's decompose-liberally bias. **Done, including a corrective
  follow-up pass** — see [sweep-findings.md](sweep-findings.md) for the final
  picture. Result: **2 confirmed high-severity violations**
  (`agent-worktrees` conduct fragments — unconditional every session;
  `agent-index` scope-binding usage essay — unconditional whenever the repo
  is opted in), **1 low-severity shared finding** (a conditional
  fallback-boilerplate duplication across all 11
  `write_session_guidance.py`-using plugins, only rendering when a plugin has
  no dynamic content to report), and **2 fully clean** (`budget-guidance`,
  `ai-attribution`). The initial sweep pass overclaimed a flat "5 confirmed +
  4 pending" split; a direct source-inspection follow-up corrected this to
  the severity-ranked picture above — see the findings doc's Revision
  history.
- [x] For any field that is dynamic **only** in the trivial "path differs per
  machine" sense (like `emit-command-catalog`'s `argv[0]`), confirm it is
  already minimal (schema envelope + the one dynamic value) and needs no
  further trimming — this is the reference-correct shape. **Confirmed done:**
  both the `emit-command-catalog` framing sentence and its per-command
  `purpose` strings are correctly static-and-checked-in already (the latter
  is baked into the generated, version-controlled script itself, not
  recomputed per session) — no Phase 2 action needed for either.

### Phase 2 — Correct the found violations
- [ ] **High priority:** migrate `agent-worktrees`'s
  `worktree-conduct.md`/`account-conduct.md` to a checked-in
  `instructions/*.instructions.md` projection; trim the `session-conduct`
  hook to emit only the dynamic definition/related/history remainder plus a
  minimal explanatory header.
- [ ] **High priority:** migrate `agent-index`'s `emit_scope_binding.py`
  static usage-guidance essay to a checked-in, repo-opt-in-gated projection;
  trim the hook's session-guidance-file write to the dynamic source-list
  `rows` plus a minimal header.
- [ ] **Low priority:** dedupe the shared `write_session_guidance.py`
  generator template's conditional fallback/size-limit boilerplate (11
  plugins) to one source location, since it is currently 11 near-identical
  hand-copies rather than a genuine session-materialization violation
  (it's conditional, not unconditional). Sequence after the two high-priority
  items.
- [ ] Verify no plugin regresses its budget, provenance lock, or
  `proven-output-free` classification as a result (rerun
  `scan-customizations.py --strict` and `manage-instruction-projections.py
  scan` after each correction).
- [ ] Land each corrected plugin as its own PR (one plugin, or one closely
  related group, per PR) rather than one monolithic diff, per this repo's
  "short PR cycles, follow-ups expected" norm.

### Phase 3 — Enforce the rule mechanically (prevent regression)
- [ ] Extend the plugin-suite guard (`scan-customizations.py` Mode B roster
  checks, or a dedicated new check) to flag a `sessionStart` hook whose
  emitted `additionalContext` contains a paragraph that is byte-identical
  across two or more independent invocations with different session/cwd/repo
  inputs — the mechanical signature of smuggled-in static content.
- [ ] Document the enforced rule in
  `docs/patterns/session-scoped-dynamic-guidance.md` and the
  `reviewing-customizations` skill's Mode B section, citing the
  `agent-worktrees` conduct case as the canonical example this effort fixed.

### Phase 4 — Facility-side (aperture-labs) audit
- [ ] Once the plugin-suite-side fixes land and reach the version floor this
  facility adopts, re-run the aperture-labs launch/budget matrix (the same
  `manage-instruction-projections.py sync` + `scan-customizations.py --strict`
  pairing used to close Phase 11 of the decontamination effort) to confirm
  the corrected plugins' new checked-in projections land cleanly and the
  aggregate budget stays within the facility's 32 KiB override.
- [ ] Record a short completion note in the (archived)
  `copilot-context-injection-decontamination` effort's journal pointing here,
  since this is the follow-through the operator expected that effort to have
  covered — without reopening the archived effort itself.

## Validation Plan

- [ ] Every plugin in the Phase 0 roster has a recorded classification in
  `sweep-findings.md` — no plugin left unaudited.
- [ ] Every STATIC finding either already lives in a checked-in instructions
  projection (no action needed) or has been migrated there in Phase 2 (a
  before/after diff exists in the corrected PR).
- [ ] Every DYNAMIC finding is justified: rerunning the hook with two
  different session/cwd/repo/worktree inputs produces two different payload
  values for that field.
- [ ] `scan-customizations.py --from-settings --strict` and
  `manage-instruction-projections.py scan --from-settings --json` report 0
  blocking findings across the full marketplace roster after Phase 2 lands.
- [ ] The Phase 3 mechanical guard exists, is tested (a fixture hook that
  smuggles static prose fails the check; a clean hook passes), and runs in the
  suite's normal CI/guard invocation.
- [ ] The aperture-labs launch/budget matrix (Phase 4) stays within budget and
  reports 0 blocking findings after adopting the corrected plugin versions.

## Proposal

_Pending — to be filled in during Phase 0/1 once the umbrella issue and
initial sweep exist._

## Journal

### 2026-09-08 — Kickoff

- Effort created after the operator flagged, while closing
  `copilot-context-injection-decontamination` (aperture-labs), that a full
  static-vs-dynamic sweep of every `sessionStart` hook was expected to be part
  of that effort's own validation and was not actually performed — that
  effort validated budget/lock/output-classification, not per-hook
  content-shape correctness.
- Confirmed one concrete violation during the triggering conversation:
  `agent-worktrees`'s `worktree-conduct.md` / `account-conduct.md` fragments
  are fully static text delivered through the live `session-conduct` hook on
  every session, rather than through a checked-in instructions projection.
  Spot-checked `ai-attribution` as a clean counter-example
  (`sessionStart.context: "none"`, fully static-projection-based).
- Scoped Phase 0-4 plan; did not yet execute the full marketplace sweep —
  that is Phase 1's job, gated on filing the umbrella issue and landing this
  README through review first (Phase 0), per the "propose before you do"
  cross-repo/effort convention this facility follows.
- **Correction:** while writing this effort's Related-work pointer, discovered
  `custom-context-aggregator-retirement`'s own README is still
  `Status: Active` with 2 unchecked Validation Plan items (fresh/resume/ACP
  launch-path proof; checked-in pointer + exact-session-writer completeness) —
  **not** Done as the aperture-labs `copilot-context-injection-decontamination`
  effort's Phase 11 closure (this same session) had stated. The upstream
  substantive fact that closure relied on — PR #2218 merged, `context-injection`
  plugin actually removed from `copilot-extensions` — is independently
  confirmed true and unaffected; only the "the public sibling effort is Done"
  characterization was inaccurate. Filed as a follow-up correction to the
  archived aperture-labs effort's journal rather than reopening it, since the
  Phase 11 gate itself did not depend on the sibling effort's own remaining
  (unrelated) launch-path items.
- Opened this effort as `copilot-extensions` PR #2236 (Draft-status plan) and
  landed the aperture-labs correction as PR #6680 (merged).

### 2026-09-08 — Phase 1 sweep executed, then corrected

- Ran the full marketplace sweep across all 14 `sessionStart`-registering
  plugins via three parallel read-only explore passes (4-5 plugins each),
  plus the 2 already classified pre-effort (`agent-worktrees`,
  `ai-attribution`).
- Surfaced a methodological split worth keeping: **stdout output-freedom**
  (what the existing `scan-customizations.py` `proven-output-free`
  classification checks) is a different, weaker test than this effort's
  **content-shape conformance** test. Every plugin passes test 1 (hook
  stdout is always `{}`; real content, when present, is written to a
  session-scoped guidance file instead) — but that alone doesn't prove test 2.
- The initial sweep pass's raw verdicts ("5 confirmed + 4 pending, largely on
  the strength of `write_session_guidance.py`'s fallback boilerplate")
  turned out to be imprecise. A direct source-inspection follow-up found: (a)
  that fallback boilerplate is shared identically across **all 11**
  `write_session_guidance.py`-using plugins, not just the 4-7 originally
  flagged, and (b) — the more important correction — it is **conditional**
  (only renders when a plugin's own dynamic content is absent), which is a
  materially weaker case than genuinely unconditional static prose.
  Restructured [sweep-findings.md](sweep-findings.md) around severity:
  **2 confirmed high-severity violations** (`agent-worktrees` conduct
  fragments; `agent-index`'s `emit_scope_binding.py` usage essay — both
  unconditional, every qualifying session), **1 low-severity shared finding**
  (the 11-plugin conditional fallback duplication — real, but lower priority),
  and **2 fully clean** (`budget-guidance`, `ai-attribution`). Also confirmed
  both `emit-command-catalog` open questions (the framing sentence, the
  per-command `purpose` strings) are non-issues — already correctly static
  and checked in.
- Re-scoped Phase 2 to two high-priority migration targets plus one
  low-priority generator-dedupe cleanup, in that order.

### 2026-09-08 — Reconciliation pass (coordination + status)

- Filed the umbrella GitHub issue,
  ThomasMichon/copilot-extensions#2256, closing the outstanding Phase 0 gap;
  the effort README's Umbrella issue field and the `efforts/README.md` index
  row now reference it in place of the prior `TBD` placeholder.
- Fully qualified the cross-repo `dotfiles` reference in the Context section
  (`dotfiles#1053` instead of a bare `#1053`, which would otherwise resolve to
  this repo per the fully-qualify-other-repos convention).
- Corrected **Status: Draft → Active** here and in the effort index: Phase 1's
  full marketplace sweep already executed and its findings
  (`sweep-findings.md`) are included in this PR, so `Draft` no longer
  accurately described the effort's state. Updated the PR description to
  match.
- Rebased onto current `main` to reconcile; no plugin payload changed, so no
  version-bump triplet applies.

