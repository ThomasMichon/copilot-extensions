# Vision Backport Sweep

- **Slug:** `vision-backport-sweep`
- **Repo:** copilot-extensions
- **Branch(es):** `effort/vision-backport-sweep`
- **Created:** 2026-10-05
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** `visions/README.md` (the whole index — a repo-wide sweep,
  not one item); scoped so far:
  `visions/plugins/agent-dispatch/tasks-pane-ux` §Concepts/the-suspended-task-waiter,
  `visions/plugins/agent-dispatch/reviewer` §Behaviors/stagnation-escalates,
  `visions/plugin-services` §Behaviors/register-once-cutover-on-update
- **Umbrella issue:** none yet filed in this repo (see Context — this effort
  itself is the tracker for the sweep; per-gap issues are filed individually
  and linked below as they're carved; `efforts/README.md`'s index Coordination
  column lists this as "See effort" accordingly)
- **Sub-issues:** `ThomasMichon/copilot-extensions#5356` (plugin-services
  conformance gap, pre-existing, now vision-linked)

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
29-vision index is too large for one sitting. Land one coherent slice per
session (one vision reconciled + its deltas carved) and hand off the rest.

A companion, independent stream — refreshing stale user-facing docs,
README capability claims, and Picker preview screenshots/images — is tracked
separately (see `user-facing-material-refresh` effort, or TBD if it folds
in here instead; not yet decided, see Open Questions).

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
      Components subsection ("The suspended-task waiter"), naming the
      documented delegated-cross-machine/relay-subscribe gap as the open
      additive edge. Provenance entry added.
- [x] `visions/plugins/agent-dispatch/reviewer/README.md` — fold back the
      recipe's standing `_STAGNATION_CLAUSE` charter behavior (already
      shipped, governs both `land=self`/`land=author`) as a new Behavior,
      `stagnation-escalates`, distinct from `bounded-verdict-reliability`'s
      render-attempt budget. Provenance entry added.
- [x] `visions/plugin-services/README.md` — scoped first pass: confirmed the
      vision already states the applicable invariants
      (`register-once-cutover-on-update`, `immutable-versioned-runtime`) at
      the right altitude, so no vision edit was needed (Direction 1 of the
      audit: the invariants already guide the leaf correctly — the gap is
      conformance, not vision coverage). Linked `#5356`
      (agent-dispatch's `install.ps1` retires supervisor/coordinator
      processes *after* the in-place reinstall rather than before, plus an
      undetected stale `uv.exe` hazard) to this vision item via issue
      comment — Direction 2 of the audit (does the subject conform?).
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
- [ ] Run the complete `plugin-services` invariant audit (not just the
      zero-downtime-cutover angle `#5356` surfaced) against every `agent-*`
      plugin's `install.ps1` / runtime-deploy path: self-contained-runtime,
      immutable-versioned-runtime, self-provisioning-runtime,
      register-once-cutover-on-update, single-instance-lease,
      work-coalescing-singleton, and the rest of the behaviors list. Produce
      a conformance table (invariant × plugin × status × evidence) as a
      review artifact in this effort (not in any vision file), then file one
      issue per genuine nonconformance, citing the specific invariant item.

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
  material-refresh handoff directive.
- Landed Phase 1's three session-evidenced targets in one PR: folded back
  `#5400`'s run-waiter surfacing into the tasks-pane-ux vision, folded back
  the reviewer recipe's standing stagnation-escalation behavior into the
  reviewer vision, and scoped a first pass of the plugin-services invariant
  audit (confirmed the vision already covers the applicable invariants;
  linked the pre-existing `#5356` to it via issue comment rather than editing
  the vision).
- The full invariant audit (Phase 3) and the wider vision sweep (Phase 2) are
  explicitly deferred to future sessions per the handoff's own sequencing
  note — this effort stays open across many slices.
