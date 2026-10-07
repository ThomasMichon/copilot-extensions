# Vision Backport Sweep

- **Slug:** `vision-backport-sweep`
- **Repo:** copilot-extensions
- **Branch(es):** per-slice (each session/slice opens its own PR branch; no
  shared long-lived branch — see Coordination)
- **Created:** 2026-10-05
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** `visions/README.md` (the whole index — a repo-wide sweep,
  not one item); scoped so far:
  `visions/plugins/agent-dispatch/tasks-pane-ux` §Concepts/the-suspended-task-waiter,
  `visions/plugins/agent-dispatch/reviewer` §Behaviors/stagnation-escalates,
  `visions/plugins/agent-dispatch` §Behaviors/inherits-runtime-service-invariants,
  `visions/plugins/agent-codespaces` (repo-sourced-provenance,
  in-venue-plugin-injection, dual-mode-session-reach),
  `visions/remote-interactive-sessions` §Purpose & Intent (stale-framing
  correction, carried by the same slice's PR review)
- **Umbrella issue:** `ThomasMichon/copilot-extensions#5456`
- **Sub-issues:** `ThomasMichon/copilot-extensions#5356` (plugin-services
  conformance gap, pre-existing, now vision-linked) ·
  `ThomasMichon/copilot-extensions#5452` (tasks-pane-ux delegated/relay
  waiter-surfacing gap) ·
  `ThomasMichon/copilot-extensions#5468` (agent-logger: in-place
  same-version rewrite, needs the refuse-or-new-generation fix) ·
  `ThomasMichon/copilot-extensions#5472` (consolidated: every `agent-*`
  plugin's immutable-versioned-runtime conformance gap for a same-version,
  content-changed update — agent-vault/agent-codespaces/agent-worktrees/
  agent-mcp/agent-index's unsafe races, agent-bridge's lock-safe-but-still-
  non-conforming rewrite (the only plugin that's actually lock-safe), and
  agent-ssh/agent-containers/agent-machines' unconditional in-place
  reinstall; filed and expanded this slice)

## Guiding Intent

A lot of real capability has landed across many plugins without matching
vision buildout, and without strong adherence to the visions that already
exist. Run the `backporting-visions` skill (specializes `envisioning`)
repo-wide: for each vision in `visions/README.md`'s index, reverse-engineer
embodied intent from reality (code + reality docs) and fold it back into the
vision as a superset — additive only, never scaling back — then run the
design/service-invariant audit against `visions/plugin-services/README.md`
for any subject that ships an installable runtime. Carve every genuine
vision→reality delta (a north-star-ahead item, or an invariant
nonconformance) into a GitHub issue citing the vision item, grouped here.

This is explicitly a **multi-session, multi-stretch** effort — this repo's
vision index (`visions/README.md`) is too large for one sitting. Land one
coherent slice per session (one vision reconciled + its deltas carved) and
hand off the rest.

A companion, independent concern — refreshing stale user-facing docs,
README capability claims, and Picker preview screenshots/images — is not yet
tracked anywhere; whether it becomes its own effort or a later phase of this
one is still undecided (see Phase 4 and Proposal below).

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary (operator's agent) | Owns the whole sweep: reconciles visions, runs the invariant audit, carves issues | A `copilot-extensions` worktree, one per session/slice |

## Coordination

- **Topology:** independent per-slice PRs — each session lands one coherent
  slice (one or a few related vision files + any issues/comments it carves)
  through its own PR rather than a shared long-lived feature branch.
- **Host (owns PRs):** whichever session/worktree is actively working a
  slice; no standing owner beyond that.
- **Delegates:** none yet — single-repo, single-agent effort so far (see
  Guiding Intent: no cross-machine dispatch needed for a docs-only sweep).
- **Handoff:** a session that can't finish a slice hands off via the normal
  `context-handoff` mechanism, naming which Plan items are done and which
  vision files/issues are mid-flight, per this effort's own Journal.

## Context

- **Origin:** operator handoff directive, end of a session that landed
  `#5400` (agent-dispatch run-waiter surfacing) and `#5421` (masked-output PR
  guidance), plus two still-open bug issues (`#5356`, `#5410`) filed in the
  same session. The operator flagged these as concrete, session-evidenced
  vision-backport candidates and asked for the broader sweep to be scoped and
  sequenced across sessions rather than attempted at once.
- **Governing skill:** [`backporting-visions`](../../../plugins/visions/skills/backporting-visions/SKILL.md)
  (specializes `envisioning`) — read its superset discipline and
  design/service-invariant audit sections before reconciling any further
  vision.
- **Vision index:** [`visions/README.md`](../../../visions/README.md) — the
  full standing list this sweep works against, prioritizing branch visions
  and any leaf whose plugin had heavy recent PR traffic.

## Request

Operator, end of a long multi-repo session:
> "In a handoff, we probably need to do some 'vision backporting in
> copilot-extensions'. I worry a lot has gone in without vision buildout or
> strong adherence, and so we'll need to do some adaptation. The docs,
> preview images, and other user-facing materials are also getting out of
> date with all the new improvements, enforcements, and capabilities."

## Plan

### Phase 1 — The three session-evidenced targets (cheapest/most-certain first)
- [x] `visions/plugins/agent-dispatch/tasks-pane-ux/README.md` — fold back the
      run-waiter Tasks-board/Claims surfacing from `#5400` as a new Concepts &
      Components subsection ("The suspended-task waiter"), stated as pure
      should-be (every board path surfaces the waiter). The documented
      delegated-cross-machine/relay-subscribe implementation gap is **not**
      enumerated in the vision; it is tracked as
      `ThomasMichon/copilot-extensions#5452`, filed against this vision item.
      Provenance entry added.
- [x] `visions/plugins/agent-dispatch/reviewer/README.md` — fold back the
      recipe's standing `_STAGNATION_CLAUSE` charter behavior (already
      shipped, governs both `land=self`/`land=author`) as a new Behavior,
      `stagnation-escalates`, distinct from `bounded-verdict-reliability`'s
      render-attempt budget. Provenance entry added.
- [x] `visions/plugin-services/README.md` + `visions/plugins/agent-dispatch/README.md` —
      ran the Direction-1/Direction-2 invariant audit for agent-dispatch's
      runtime: the invariant vision already stated
      `register-once-cutover-on-update`/`immutable-versioned-runtime`
      correctly, but the agent-dispatch (branch) vision did not cite or
      restate them — added `inherits-runtime-service-invariants` there to
      fix that (Direction 1; its own Provenance entry records why, with no
      issue reference, per the vision-history-only convention). Direction 2
      (does the subject conform?) is `#5356` (agent-dispatch's `install.ps1`
      retires supervisor/coordinator processes *after* the in-place
      reinstall rather than before, plus an undetected stale `uv.exe`
      hazard) — linked to the `plugin-services` invariants directly via an
      issue comment, since that's the vision layer the conformance check is
      against.
      **Not yet done:** the *full* invariant audit this vision calls for
      ("every `agent-*` plugin's `install.ps1`, not just the one subject you
      happened to find a bug in") — tracked as Phase 3 below, not assumed
      complete from this one subject.

### Phase 2 — Widen the vision sweep
- [ ] Read every remaining vision in `visions/README.md`'s index against its
      subject's current reality docs + code, prioritizing branch visions
      (`agent-fabric`, `native-convergence`, `plugin-services` itself) and any
      leaf whose plugin had heavy recent PR traffic. Apply the three-bin sort
      per vision (fold-back / north-star-ahead / omit); run the superset
      check before committing any single file. Track progress as a
      vision-by-vision checklist here once Phase 1 closes out and this phase
      starts in earnest (deliberately not pre-enumerated now — scope it from
      the actual state of each vision at reconciliation time, not guessed
      upfront).
      - [x] `visions/plugins/agent-codespaces/README.md` — reconciled against
            313 commits of drift since its 2026-07-31 authoring. Folded back
            *repo-sourced-provenance* (active-plugin-declared venue policy,
            lower precedence than adoption) and *in-venue-plugin-injection*
            (the governing harness plugin injecting plugins into the venue
            itself), plus *dual-mode-session-reach* (interactive `copilot
            <name>` vs. headless `--detach`, orchestrator-blind `--ref-file`
            hand-off, `--reverse-forward`/`--forward` port bridging) under
            `coordination-layer-provider`. Corrected a stale "agent-containers
            when authored" sibling reference and widened the reality-docs
            skill list (`recovering-codespaces`, `cleaning-codespaces`). No
            conformance gap found — all fold-back, no issue carved.

### Phase 3 — Full design/service-invariant audit
- [x] Ran a slice of the `plugin-services` invariant audit against every
      `agent-*` plugin's runtime-deploy path, scored against **two separate
      questions per plugin**: does a same-version, content-changed update
      (a) ever mutate the already-built slot in place
      (`immutable-versioned-runtime`), and (b) interrupt a live daemon doing
      so (`zero-downtime-cutover`, applicable only where a daemon exists)?
      `register-once-cutover-on-update`'s own distinct requirement (the
      supervisor registration staying bound to a stable launcher across an
      update) is **not** evaluated here — narrowing this completed slice to
      the two invariants the table actually scores, rather than claiming
      broader coverage.

      | Plugin | Immutable-versioned-runtime | Zero-downtime-cutover | Evidence |
      |---|---|---|---|
      | **agent-pull-requests** | **Conforms** | N/A (no daemon) | The one plugin in the repo doing this correctly: refuses to rebuild a completed same-version slot whose content changed — "bump the plugin version instead of rebuilding an immutable slot in place" — and exits 1 even under `-Force` (`install.ps1:524-535`). **Reference pattern for every other plugin to port.** |
      | agent-bridge | **Violates** | **Violates** | `Test-SlotContentCurrent` only no-ops on an exact content match; a genuine same-version content change explicitly downgrades to "classic stop-and-rebuild" (`install.ps1:2813-2856`) — lock-safe (stops before rewriting, avoiding a crash/corruption race) but still an in-place rewrite of a built slot, and the stop-rebuild-start cycle is itself a no-service window. Not a reference implementation — a file-lock mitigation, not immutable-generation-plus-ZDD. **The only plugin in the unsafe/lock-safe split that is actually lock-safe.** |
      | agent-dispatch | **Violates** | **Violates** | `#5356` (pre-existing, linked to this vision item in the prior slice): `Invoke-Update` calls `Install-Runtime` *before* `Retire-SupervisorProcesses` — not lock-safe, so this can crash/corrupt, not just interrupt. |
      | agent-vault | **Violates** | **Violates** | `Invoke-Update` calls `Install-Runtime` (can rebuild the active slot) *before* `Stop-VaultDaemonGraceful`, with no `agent-pull-requests`-style refusal (`install.ps1:1092-1105`, `737-822`). Same not-lock-safe shape as `#5356`. Filed as `#5472`. |
      | agent-codespaces | **Violates** | **Violates** | `Deploy-Venv`/`Deploy-Package` have no refusal or stop-if-active guard; the Connection Owner daemon is only synced via `Sync-ConnectionOwnerService` at the very end (`install.ps1:1507-1544`, `888-930`). Filed as `#5472`. |
      | agent-worktrees | **Violates** | **Violates** | `Test-SlotAlreadyComplete` (the #2174 fix itself) only short-circuits the hash-match (nothing-changed) case; genuinely changed same-version content falls through to `Deploy-Venv`/`Deploy-Package` with no refusal or stop guard, despite the function's own comment naming the status-monitor daemon as exactly this risk (`install.ps1:1331-1356, 3829-3866`). Filed as `#5472`. |
      | agent-mcp | **Violates** | **Violates** | Confirmed: `init.ps1:612-708` unconditionally installs into the existing `$VenvDir`; the zdd cutover at `init.ps1:735-783` runs only afterward and explicitly skips an exact-version-match daemon, so a same-version content change mutates the active slot *before* any cutover runs at all. Filed as `#5472`. |
      | agent-index | **Violates** | **Violates** | **Not lock-safe, despite appearing to stop-before-rebuild at first read:** the `$targetWasActive`-guarded stop/remove branch (`install.ps1:1418-1431`) only runs when `$slotReady` is false (stale/incomplete). When the slot is healthy and marked complete, that branch is *skipped* — but the unconditional `uv pip install --reinstall-package`/`--refresh-package` steps right after it (`install.ps1:1492-1551`) still run into that same live, active venv regardless, with `Invoke-ServiceCutover` called only afterward (`2498-2499`). Filed as `#5472`. |
      | agent-logger | **Violates** | **Violates (partial fix tracked)** | `Install-Package` rewrites the already-built same-version venv regardless (`install.ps1:1019-1055`). The previously-filed `#5468` only proposed stopping/restarting the Scheduled Task — that closes the lock-safety gap, **not** the immutability violation; `#5468` is updated (see its own issue comment) to the refuse-or-new-generation fix, same as everything else in this table. |
      | agent-ssh | **Violates** | N/A (no daemon) | Unconditionally reinstalls the package into the existing `$VenvDir` whenever the venv already exists, with no same-version-content-changed refusal (`install.ps1:872-949`). |
      | agent-containers | **Violates** | N/A (no daemon) | Same unconditional in-place reinstall shape (`init.ps1:648-758`). |
      | agent-machines | **Violates** | N/A (no daemon) | Same unconditional in-place reinstall shape (`init.ps1:2164-2251`). |

      **Net finding:** almost the entire `agent-*` runtime-deploy ecosystem
      violates `immutable-versioned-runtime` for a same-version,
      content-changed update. Of the daemon-bearing plugins, only
      `agent-bridge` sequences the rewrite lock-safely (stop-before-rewrite,
      still non-conforming); `agent-dispatch`, `agent-vault`,
      `agent-codespaces`, `agent-worktrees`, `agent-mcp`, **and**
      `agent-index` (whose apparent stop-guard doesn't actually cover the
      "slot healthy, content changed" path) all mutate a live, active slot
      with no stop at all. `agent-pull-requests` is the sole conforming
      reference: refuse the rebuild and require a real version bump (or,
      equivalently, build a genuinely distinct generation) rather than ever
      rewriting a completed slot.

      No blind spot found requiring a fold-up into the `plugin-services`
      invariant vision itself — the vision's existing wording already states
      the correct contract precisely ("once a version's venv is built it is
      never edited in place"); the gap is conformance across nearly every
      plugin, not a missing or imprecise invariant.
- [ ] The *rest* of the `plugin-services` behaviors list (self-contained-
      runtime, single-instance-lease, work-coalescing-singleton, discoverable-
      local-endpoint, and the remaining ~20 invariants) is **not yet audited**
      — this slice covered only the cutover/immutable-runtime angle `#5356`
      originally surfaced. Tracked as a follow-up stretch of this same Phase,
      not assumed complete.

### Phase 4 — Decide the material-refresh relationship
- [ ] Decide whether user-facing material refresh (docs, Picker preview
      screenshots, README capability claims) becomes a sub-stream of this
      effort or its own sibling effort. Not yet decided — see Open Questions.

## Validation Plan
- [ ] Every vision file touched passes the superset check (no unintended
      Non-Goal/negative the subject violates; every folded-back capability
      traced to a real PR/commit/reality doc).
- [ ] Every carved issue cites its vision item per `visions/README.md`'s
      convention and is deduped against open issues before filing.
- [ ] No vision file records conformance/gap-list prose — that output lives
      here or in linked issues only.

## Proposal

**Open question — not yet decided:** should the user-facing material refresh
(docs, Picker preview screenshots, README capability claims) fold into this
effort as a later phase, or become its own sibling effort? Both streams are
"reality has outpaced the standing record," but they have different
audiences (vision = contributor-facing design intent; materials =
adopter-facing). Deferred to Phase 4 — ask the operator if it comes up before
then rather than assuming either answer.

## Journal

### 2026-10-05 — Kickoff + Phase 1 slice
- Effort created as the tracker for the operator's vision-backport +
  material-refresh handoff directive; filed and claimed the umbrella issue
  `#5456` for the sweep.
- Opened a PR for Phase 1's three session-evidenced targets: folded back
  `#5400`'s run-waiter surfacing into the tasks-pane-ux vision (filing
  `#5452` for the documented delegated/relay implementation gap rather than
  describing it in the vision), folded back the reviewer recipe's standing
  stagnation-escalation behavior into the reviewer vision, and ran the
  Direction-1/Direction-2 invariant audit for agent-dispatch's runtime
  (added `inherits-runtime-service-invariants` to the agent-dispatch vision,
  since it hadn't cited the applicable `plugin-services` invariants; linked
  the pre-existing `#5356` as the Direction-2 conformance gap).
- The full invariant audit (Phase 3) and the wider vision sweep (Phase 2) are
  explicitly deferred to future sessions per the handoff's own sequencing
  note — this effort stays open across many slices.

### 2026-10-05 — Phase 3 slice (cutover/immutable-runtime audit)
- Audited every `agent-*` plugin's runtime-deploy path against two
  `plugin-services` invariants — `immutable-versioned-runtime` and
  `zero-downtime-cutover` — scoped to one specific case: a same-version
  update whose content changed. (`register-once-cutover-on-update`'s own
  distinct registration-stability requirement was **not** evaluated.)
  Recorded the conformance table in Phase 3 above.
- **First-pass result (superseded below):** opened PR #5469 classifying 6
  plugins as conforming based on their use of a versioned-slot build path,
  without checking the specific case that actually matters — a same-version
  update whose content changed, while a live daemon is running out of that
  exact slot.
- **Review caught the over-generous classification** (PR #5469 review,
  "Reclassify same-version rebuilds that violate immutable runtime"): the
  shared `versioned_runtime.py` primitive's incomplete-slot cleanup does
  *not* stop a live daemon before a same-version rebuild — that guard is
  per-plugin opt-in, and most plugins hadn't added it. Re-audited each
  plugin's actual same-version-changed-content path and corrected the
  table: only `agent-bridge` and `agent-index` actually implement a
  stop-before-rebuild guard; `agent-vault`, `agent-codespaces`, and
  `agent-worktrees` (including the very `#2174` fix that originated this
  whole pattern) are missing it — filed as a consolidated `#5472`, initially
  (and, per the next round, wrongly) citing agent-bridge/agent-index as the
  reference-correct pattern to port. `agent-mcp` was flagged as needing
  confirmation rather than asserted, since its earlier venv-build step
  wasn't fully traced.
- **Review went a round deeper and corrected a second over-generous call**
  ("Stop-before-rebuild still violates immutable runtime and zero-downtime
  rules"): stopping a daemon before rewriting its active slot makes the
  rewrite *lock-safe*, but the vision's `immutable-versioned-runtime`
  invariant is "once a version's venv is built it is never edited in
  place" — full stop. Lock-safe is not the same claim as immutable, and the
  stop-rebuild-start cycle is itself a no-service window, so it ALSO
  violates `zero-downtime-cutover`. Re-audited once more and found the
  actual reference-correct plugin: **`agent-pull-requests`**, which refuses
  to rebuild a completed same-version slot with changed content at all
  ("bump the plugin version instead... exits 1 even under `-Force`") —
  confirmed `agent-mcp` as a violation (not "needs confirmation") per the
  reviewer's precise citations, and reclassified `agent-ssh`,
  `agent-containers`, and `agent-machines` from blanket "N/A" to
  **violates immutable-versioned-runtime** (no-daemon only makes the
  *cutover* invariants N/A, not immutability itself — they unconditionally
  reinstall into an existing slot with no refusal). Net: almost every
  `agent-*` plugin violates `immutable-versioned-runtime` for this case;
  `agent-pull-requests` is the one plugin to actually copy from. Updated
  `#5472`'s framing to match (dropped the "port agent-bridge/agent-index"
  fix suggestion, which review correctly identified as prescribing a
  non-conforming pattern) and flagged that `#5468` (agent-logger) needs a
  similar correction — its tracked fix only closes lock-safety, not
  immutability.
- **Review caught a third, more subtle misclassification** ("agent-index
  mutates live slots before cutover"): agent-index's `$targetWasActive`
  stop-then-remove-and-rebuild branch only runs when the existing slot is
  *stale/incomplete* (`$slotReady` false); when the slot is healthy and
  marked complete — exactly the "nothing to rebuild" case — that branch is
  skipped, but the subsequent `uv pip install --reinstall-package`/
  `--refresh-package` steps run **unconditionally** into that same live
  venv regardless, with `Invoke-ServiceCutover` called only afterward.
  agent-index was never actually lock-safe at all — reclassified alongside
  the unsafe-race group (agent-dispatch/vault/codespaces/worktrees/mcp),
  leaving `agent-bridge` as the *only* plugin that is lock-safe (while
  still non-conforming); updated the net finding, `#5472`'s issue body, and
  this Journal's own prior entries accordingly. Also narrowed an entry
  above and the PR description that still implied
  `register-once-cutover-on-update` was evaluated — it was not; only
  `immutable-versioned-runtime` and `zero-downtime-cutover` were scored.
  This is now the third round of review catching a genuine scoping or
  classification error in this same audit — a useful reminder that
  "appears to stop before rebuilding" needs to be traced all the way
  through the function, not inferred from the presence of a stop call
  somewhere in it.
- Checked for an invariant-vision blind spot (Direction 1, upward fold):
  none found — the vision's existing wording already states the correct
  contract precisely (a built slot is never edited in place, full stop,
  not "edited in place unless lock-safe"); the gap is conformance across
  nearly the entire ecosystem, not an imprecise or missing invariant.
- **Not yet done:** the rest of `plugin-services`'s ~20+ other behaviors
  (self-contained-runtime, single-instance-lease, work-coalescing-singleton,
  discoverable-local-endpoint, etc.) were not audited this slice — only the
  cutover/immutable-runtime angle `#5356` originally surfaced. A future
  slice should widen Phase 3 to the rest of the invariant list before
  calling it fully done.

### 2026-10-06 — Fix-direction correction: point at the existing `dev` slot
- The operator flagged, after PR #5469 merged, that the fix direction in
  `#5472`/`#5468` ("port `agent-pull-requests`' refusal pattern") was
  incomplete: a refusal alone leaves no sanctioned fast inner loop for
  genuine same-version iteration, and a separately-corrupted (not just
  content-mismatched) slot needs its own repair story.
- This repo already has exactly the mechanism the operator described for
  the iteration side: `efforts/active/mutable-dev-slot/` (design doc
  `docs/patterns/mutable-dev-slot.md`) built a protected, claim-gated,
  genuinely mutable `versions/dev` slot per plugin — piloted on
  `agent-codespaces`, not yet rolled out to the other 12 vendoring plugins
  (13 total, including the non-`agent-*` `budget-guidance`; its own
  Phase 3, previously unprioritized).
- Added the operator's corruption-repair instinct as a new doc section —
  but a first draft of it (stop, delete the whole slot, rebuild fresh at
  the *same* version) was itself flagged by that PR's own review as
  violating `immutable-versioned-runtime`'s rollback guarantee (reusing a
  retired identity for a new build is still a rewrite of what that identity
  means). Reworked to the correct shape: a broken-slot repair is an
  ordinary generation cutover — build the replacement under a **distinct**
  generation identity, promote it only once health-gated, then drain and
  retire the broken slot through the exact same serialize/promote-before-
  retire/drain discipline `graceful-daemon-cutover` already requires for
  every other cutover path — not a delete-and-reuse shortcut.
- Updated `#5472`/`#5468` to point at both pieces; prioritized
  `mutable-dev-slot`'s Phase 3 rollout list with the specific plugins this
  audit found. This is a fix-direction correction only — the Phase 3
  conformance table and classifications above are unchanged.

### 2026-10-06 — Phase 2 slice: `agent-codespaces` vision reconciliation
- Picked Phase 2 (widen the vision sweep) off the four-option Next Slice
  menu from the prior handoff. Ranked candidate visions by staleness ×
  commit traffic since each one's own last-revised date (`git log
  --since=<last-revised-date> --oneline -- plugins/<name>` counts, a
  single consistent metric); `agent-codespaces` (last revised 2026-07-31,
  313 commits since) ranked above the other stale candidates (`agent-ssh`,
  last revised 2026-07-22, 139 commits since; `agent-containers`, last
  revised 2026-08-27, 123 commits since — both legitimate follow-ups for a
  future slice).
- Read the vision in full against the plugin's current `README.md` and
  `docs/patterns/codespace-repo-provenance.md`. Found two substantial,
  already-shipped capabilities the vision never stated at all: the
  active-plugin-sourced venue-policy seam (`codespaceConfig` declaration,
  lower precedence than adoption, identity-verified root resolution) and
  in-venue plugin injection (the governing harness plugin's
  `codespacePlugins`, staged from a local marketplace when needed). Folded
  both back as new Concepts + Features (*repo-sourced-provenance*,
  *in-venue-plugin-injection*). Also found the already-shipped interactive/
  headless dual-mode CLI sessions (`copilot <name>` / `--detach`), the
  orchestrator-blind `--ref-file` hand-off, and the `--reverse-forward`/
  `--forward` port bridging entirely unstated under
  *coordination-layer-provider* — folded back as *dual-mode-session-reach*.
  Ran the superset check on each addition: all are reality already doing
  this; nothing contradicts an existing Non-Goal or scales the vision back.
- Opportunistically corrected a now-stale "agent-containers (when
  authored)" sibling-leaf reference (that vision has existed since before
  this slice) and widened the Reality-docs skill list to include
  `recovering-codespaces` and `cleaning-codespaces`, both of which already
  exist and already operationalize behaviors the vision states
  (*recover-not-lose*, *credential-readiness-verified-end-to-end*).
- No conformance gap was found requiring a new issue — this slice is
  fold-back only, distinct from Phase 3's separate `install.ps1`
  conformance audit (`#5472` already covers agent-codespaces there and is
  unaffected).
- **PR #5575 review, round 1:** caught two real contradictions in the first
  draft: *dual-mode-session-reach* over-claimed "never two divergent code
  paths" when reality actually routes a detached session through
  `copilot_detach.cmd_detach` and an attached one through a separate
  `interactive_ssh` path; and *repo-sourced-provenance*'s "no adoption
  step" directly contradicted *config-by-adoption*'s pre-existing "adoption
  is the one act" wording. Fixed by scoping the adoption claim to the
  repo-owned policy lane specifically (the two provenance lanes compose,
  they don't compete) and by narrowing the dual-mode-reach claim to the
  shared contract rather than a shared implementation.
- **PR #5575 review, round 2:** the round-1 fix for the code-path finding
  overcorrected — it added a "not yet fully realized... north-star-ahead
  refinement" sentence that (a) pinned implementation wiring the vision's
  own "Not a specification" boundary explicitly excludes, and (b) quietly
  turned a no-gap fold-back slice into one with an untracked vision→reality
  delta, contradicting the PR's own no-gap claim. Also flagged the PR
  description was missing this repo's required **Documentation impact**
  statement (`CONTRIBUTING.md`'s pre-PR checklist). Fixed by dropping the
  offending sentence entirely (the behavior now states only the should-be
  contract guarantee, leaving the implementation-path question genuinely
  unpinned) and by adding the statement to the PR description.
- **PR #5575 review, round 3:** caught a genuine cross-vision contradiction
  the first two rounds missed: `visions/remote-interactive-sessions/
  README.md`'s own Purpose & Intent framing still claimed (stale against
  its *own* later provenance) that a remote venue "is reached only through
  venue-parity's headless... dispatch core" and an operator "has no
  first-class path" to an attended remote session — directly contradicting
  *dual-mode-session-reach*'s now-documented `copilot <name>` /
  `--detach` venue-launch primitive. Scoped *dual-mode-session-reach*
  explicitly to the venue-launch layer, deferring the deeper
  coordination-layer integration (reservation, `live_sessions`
  discoverability, honest marking) to `remote-interactive-sessions`. Also
  reconciled that vision's own stale framing in place (its Purpose & Intent
  had not caught up with its own 2026-09-20/09-22 provenance entries that
  already added the venue-launch primitive) and added a two-way See Also
  cross-link naming the ownership split. This is itself a small, in-scope
  Phase 2 fold-back on a second vision, carried by the same review round
  rather than deferred to a separate slice.
- PR #5575 merged; worktree finalized.

### 2026-10-07 — Operator follow-up: attended sessions must stay bridge-driven
- Operator confirmed, after reviewing this slice's summary, that attended
  (human-interactive) remote CodeSpace sessions should still be driven via
  agent-bridge, not treated as a bridge-invisible raw SSH mux escape hatch
  — exactly the coordination-layer gap `remote-interactive-sessions`
  already defers to from `agent-codespaces`' `dual-mode-session-reach`.
- Searched for an existing tracked issue before filing (dedup discipline):
  found `#3346` ("Detached (agent-facing) CLI-mode venue sessions..."),
  which proposes the matching daemon-port-reverse-forward +
  `live_sessions` registration machinery but is explicitly scoped to the
  **detached**/agent-facing launch only — no issue covered the
  **attended**/human-interactive counterpart.
- Filed `#5614` for the attended case, cross-referencing `#3346` and the
  two visions (`remote-interactive-sessions`'s stated gap,
  `agent-codespaces`'s `dual-mode-session-reach` deferral). Left a
  cross-link comment on `#3346` pointing back.
- No vision text changed this entry — the visions already correctly state
  this as the should-be contract; only the tracked-issue gap needed
  closing.

### 2026-10-07 — Self-correction: #5614 was a phantom gap, both visions were stale
- While scoping the next Phase 2 vision (`agent-ssh`), its plugin README's
  `copilot <ssh-target>` description ("if another process already holds the
  bridge reverse-forward route, the attached command reuses that route")
  read as inconsistent with the "daemon port isn't reverse-forwarded yet"
  claim just relied on to file `#5614`. Checked directly against
  `copilot_venue.py` (`cmd_copilot`/`_cmd_copilot_connect`) and the owning
  effort, `efforts/active/agent-bridge-cli-mode-sessions/README.md`: the
  attended `copilot <name>` path already places a worktree-keyed
  reservation, a Connection Owner tenant hold carrying **both** the
  credential-relay **and** a daemon-port reverse-forward, and
  self-registers into `live_sessions` marked `driven_by: cli-mode` —
  landed and **live-clean-room-validated end to end on 2026-09-20/22**,
  weeks before this slice's PR #5575 was opened.
- This means the prior entry's premise was wrong on two counts: (1) `#5614`
  described a gap that doesn't exist — **closed it** with the evidence
  above, and corrected the misleading cross-link comment left on `#3346`.
  (2) The `remote-interactive-sessions` and `agent-codespaces` vision text
  PR #5575 shipped was *also* wrong — it preserved/extended a stale
  "coordination-layer integration still missing" claim instead of
  recognizing it as already realized. Root cause: round 3's correction
  trusted `remote-interactive-sessions`' own existing prose as ground
  truth instead of checking it against the owning effort/code, the same
  verification gap that produced `#5614`.
- Corrected both vision files in place (not a revert — a second, verified
  correction): `remote-interactive-sessions`' Purpose & Intent and "CLI
  mode needs no Session Host" concept section now state the integration as
  realized, citing the owning effort's Phase 4/Validation Plan; its
  Provenance records this as superseding the 10-06 entry rather than
  silently overwriting it. `agent-codespaces`' `dual-mode-session-reach`
  no longer claims the integration is "deferred" to a sibling vision.
  Checked off that owning effort's own Phase 5 "confirm realized behavior
  against the vision" item, since this sweep is exactly that confirmation
  (its sibling Phase 5 item, plugin docs/architecture sync, is unaffected
  and still open).
- **Lesson for the rest of this sweep:** a vision's own prose is not
  sufficient evidence for "is this still true" — verify against the owning
  effort's journal/validation-plan and, when in doubt, the actual code,
  before either folding something back *or* preserving an existing gap
  claim. Apply this going forward, not only when a reviewer or a sibling
  plugin's README happens to surface the inconsistency.
- Next: continue Phase 2 by picking the next stale/high-traffic vision
  (candidates from the prior ranking: `agent-ssh` 139 commits since
  2026-07-22, `agent-containers` 123 commits since 2026-08-27) — with the
  verification lesson above applied from the start this time.
