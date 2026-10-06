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
  `visions/plugins/agent-dispatch` §Behaviors/inherits-runtime-service-invariants
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
  agent-mcp's unsafe races, agent-bridge/agent-index's lock-safe-but-still-
  non-conforming rewrite, and agent-ssh/agent-containers/agent-machines'
  unconditional in-place reinstall; filed and expanded this slice)

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
      | agent-ssh | **Violates** | N/A (no daemon) | Unconditionally reinstalls the package into the existing `$VenvDir` whenever the venv already exists, with no same-version-content-changed refusal (`install.ps1:872-949`). |
      | agent-containers | **Violates** | N/A (no daemon) | Same unconditional in-place reinstall shape (`init.ps1:648-758`). |
      | agent-machines | **Violates** | N/A (no daemon) | Same unconditional in-place reinstall shape (`init.ps1:2164-2251`). |
      | agent-bridge | **Violates** | **Violates** | `Test-SlotContentCurrent` only no-ops on an exact content match; a genuine same-version content change explicitly downgrades to "classic stop-and-rebuild" (`install.ps1:2813-2856`) — lock-safe (stops before rewriting, avoiding a crash/corruption race) but still an in-place rewrite of a built slot, and the stop-rebuild-start cycle is itself a no-service window. Not a reference implementation — a file-lock mitigation, not immutable-generation-plus-ZDD. |
      | agent-index | **Violates** | **Violates** | Same shape: removes/rebuilds the active slot when its content doesn't match, stopping the service first (`install.ps1:1418-1431`), then unconditionally installs packages into it (`install.ps1:1492-1551`) — lock-safe, still an in-place rewrite + a no-service window. |
      | agent-dispatch | **Violates** | **Violates** | `#5356` (pre-existing, linked to this vision item in the prior slice): `Invoke-Update` calls `Install-Runtime` *before* `Retire-SupervisorProcesses` — not even lock-safe, so this can crash/corrupt, not just interrupt. |
      | agent-vault | **Violates** | **Violates** | `Invoke-Update` calls `Install-Runtime` (can rebuild the active slot) *before* `Stop-VaultDaemonGraceful`, with no `agent-pull-requests`-style refusal (`install.ps1:1092-1105`, `737-822`). Same not-even-lock-safe shape as `#5356`. Filed as `#5472`. |
      | agent-codespaces | **Violates** | **Violates** | `Deploy-Venv`/`Deploy-Package` have no refusal or stop-if-active guard; the Connection Owner daemon is only synced via `Sync-ConnectionOwnerService` at the very end (`install.ps1:1507-1544`, `888-930`). Filed as `#5472`. |
      | agent-worktrees | **Violates** | **Violates** | `Test-SlotAlreadyComplete` (the #2174 fix itself) only short-circuits the hash-match (nothing-changed) case; genuinely changed same-version content falls through to `Deploy-Venv`/`Deploy-Package` with no refusal or stop guard, despite the function's own comment naming the status-monitor daemon as exactly this risk (`install.ps1:1331-1356, 3829-3866`). Filed as `#5472`. |
      | agent-mcp | **Violates** | **Violates** | Confirmed (not merely suspected): `init.ps1:612-708` unconditionally installs into the existing `$VenvDir`; the zdd cutover at `init.ps1:735-783` runs only afterward and explicitly skips an exact-version-match daemon, so a same-version content change mutates the active slot *before* any cutover runs at all. Filed as `#5472`. |
      | agent-logger | **Violates** | **Violates (partial fix tracked)** | `Install-Package` rewrites the already-built same-version venv regardless (`install.ps1:1019-1055`). The previously-filed `#5468` only proposed stopping/restarting the Scheduled Task — that closes the lock-safety gap, **not** the immutability violation; `#5468` is updated (see its own issue comment) to the refuse-or-new-generation fix, same as everything else in this table. |

      **Net finding:** almost the entire `agent-*` runtime-deploy ecosystem
      violates `immutable-versioned-runtime` for a same-version,
      content-changed update — differing only in whether the in-place
      rewrite also races a live daemon (agent-dispatch/vault/codespaces/
      worktrees/mcp: yes, unsafe) or is sequenced lock-safely first
      (bridge/index: safe from crashing, but still non-immutable and not
      zero-downtime). `agent-pull-requests` is the sole conforming
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
- Audited every `agent-*` plugin's runtime-deploy path against the
  cutover/immutable-runtime slice of `plugin-services`'s invariants
  (`immutable-versioned-runtime`, `register-once-cutover-on-update`,
  `zero-downtime-cutover`). Recorded the conformance table in Phase 3 above.
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
