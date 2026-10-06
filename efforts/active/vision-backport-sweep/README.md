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
  `ThomasMichon/copilot-extensions#5468` (agent-logger scheduled-task
  cutover gap, newly filed this slice)

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
- [x] Ran the cutover/immutable-runtime slice of the `plugin-services`
      invariant audit (`immutable-versioned-runtime`,
      `register-once-cutover-on-update`, `zero-downtime-cutover`) against
      every `agent-*` plugin's runtime-deploy path. Conformance table:

      | Plugin | Status | Evidence |
      |---|---|---|
      | agent-bridge | Conforms | `Invoke-Update` explicitly handles the same-version-refresh case (downgrades to stop-and-rebuild only then), strict content-match no-op, and drains/stops *before* touching the venv (`install.ps1:2734-2855`); documents having already fixed this exact bug class (dotfiles#1612). |
      | agent-worktrees | Conforms | Versioned-slot build + `Invoke-VersionedActivate` (`install.ps1:3829-3866`) — the originating fix pattern (#2174). |
      | agent-vault | Conforms | `Install-Runtime` builds the new slot first; the old daemon is gracefully drained+stopped only *after* (`install.ps1:1092-1105`), never racing the rebuild. |
      | agent-codespaces | Conforms | `Deploy-Venv`/`Deploy-Package` target a fresh versioned slot, then `Invoke-VersionedActivate` swaps the link (`install.ps1:1507-1524`). |
      | agent-index | Conforms | Explicit `Invoke-ServiceCutover`: zdd active/passive — new slot stood up passive, routing flipped, old drained + retired (`install.ps1:2319-2345`). |
      | agent-mcp | Conforms | Same zdd cutover shape for its `serve` daemon (`init.ps1:736-787`: "routing flipped; old drained + retired"). |
      | agent-dispatch | **Violates** | `#5356` (pre-existing, linked to this vision item in the prior slice): `Invoke-Update` calls `Install-Runtime` *before* `Retire-SupervisorProcesses`, so a same-version (dev-iteration) reinstall can collide with the live supervisor/coordinator's open file handles; plus an undetected stale `uv.exe` hazard. |
      | agent-logger | **Partial** | Versioned-slot build (`Invoke-VersionedSlotClean` + `New-SignedVenv`), but `update` never stops/restarts its registered Scheduled Task around a same-version rebuild — lower risk than agent-dispatch (task runs briefly/periodically, not continuously), but a real gap. Filed as `#5468`. |
      | agent-ssh, agent-pull-requests | N/A | Explicitly "CLI (no daemon)" — nothing to cut over. |
      | agent-containers, agent-machines | N/A | Explicitly no-daemon CLI plugins (`init.ps1` comments: "a CLI plugin has no daemon holding the link"). |

      No blind spot found requiring a fold-up into the `plugin-services`
      invariant vision itself — `self-provisioning-runtime`'s existing
      "idempotent, version-keyed (a no-op once already matched)" language
      already covers the same-content-no-op discipline several plugins
      (notably agent-bridge) implement explicitly.
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
- Result: 6 plugins conform (several — agent-bridge, agent-index, agent-mcp —
  already implement an explicit zdd active/passive cutover), 4 are N/A
  (no-daemon CLI plugins), `agent-dispatch`'s known `#5356` is the one real
  violation, and `agent-logger` has a lower-risk partial gap — filed as
  `#5468`.
- Checked for an invariant-vision blind spot (Direction 1, upward fold):
  none found — `self-provisioning-runtime`'s existing no-op-on-content-match
  language already covers what agent-bridge's explicit same-content check
  embodies.
- **Not yet done:** the rest of `plugin-services`'s ~20+ other behaviors
  (self-contained-runtime, single-instance-lease, work-coalescing-singleton,
  discoverable-local-endpoint, etc.) were not audited this slice — only the
  cutover/immutable-runtime angle `#5356` originally surfaced. A future
  slice should widen Phase 3 to the rest of the invariant list before
  calling it fully done.
