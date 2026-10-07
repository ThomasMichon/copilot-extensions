# Agent-Process Self-Report Registry

- **Slug:** `agent-process-self-report-registry`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-10-06
- **Status:** Draft
- **Umbrella issue:** #5559
- **Sub-issues:** #5557 (mux-daemon stale-retirement bug, found while auditing
  for this effort) · #5558 (agent-dispatch worker-pool version-skew bug, same)

**Documentation impact:** This effort extends, and must stay reconciled with,
[`visions/process-telemetry`](../../../visions/process-telemetry/README.md).
Landed work updates that vision's own reality-doc links (currently
`agent-dispatch`/`agent-bridge` `telemetry.py` only) as new plugins adopt the
seam, and `docs/patterns/graceful-daemon-cutover.md` if a new per-OS liveness
primitive is added.

## Guiding Intent

Make "what `agent-*` daemons are *currently* running, what are they, and is
anything orphaned or version-skewed" answerable from one inventory/liveness
query instead of manual ps-based archaeology — **without inventing a second
instrumentation mechanism**. `visions/process-telemetry` already defines the
standing emission seam (`agent-dispatch`/`agent-bridge`'s existing
fail-open `telemetry.py`, generalized) for a runtime plugin to say "this
process is mine" — but that vision is explicitly **emit-only**: no live
registry, no query surface, no aggregation (its own Non-Goals say so). This
effort is the **consumer** half that vision names and defers downstream: a
small, honest **inventory + liveness** query built by registering a sink
against the *existing* seam — scoped narrowly to current-state snapshotting,
not the broader "health" or APM-style monitoring that seam was never meant to
carry. Completion requires coverage of every daemon kind the Guiding Intent
names (not just a pilot subset) — see Plan.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Reconcile with `visions/process-telemetry`, close plugin-adoption gaps, build the inventory consumer, roll it out, pilot it | `copilot-extensions` worktree |
| [`libs/agent-procutil`](../../../libs/agent-procutil/README.md) | Not the implementation site — the repo's canonical source for headless-spawn kwargs, a different concern from process self-report; referenced only to avoid misdirecting future work there | reference only |
| `visions/process-telemetry`'s existing seam (`agent_dispatch.telemetry` / `agent_bridge.telemetry`) | The instrumentation this effort builds on, not replaces | shared seam, already shipped |

## Coordination

- **Topology:** independent per-slice worktrees, one PR per slice.
- **Host (owns PRs):** the driving agent for each slice; no shared feature
  branch — each phase below is small enough to land as its own independent
  PR against `dev`, so no cross-slice branch coordination is needed.
- **Slice ownership:** one slice per Plan phase (Phase 1 design, Phase 2
  sink+query implementation, Phase 3 pilot wiring + rollout, Phase 4 further
  adoption). A slice's own PR is the handoff point — the next slice starts
  once the prior one merges and this README's Plan/Journal are updated.
- **Handoff:** whoever starts the next unclaimed Plan phase reads this
  README's current Plan/Journal state first; no other coordination channel
  is needed for a single-driver effort like this one.

## Context

`agent-*` runtime plugins each spawn and supervise their own long-lived
daemons/singletons, but no shared, queryable inventory exists today — each
plugin's own on-disk state (`~/.agent-dispatch/run/supervisor/...`,
`~/.worktree-manager/mux-daemon-routing/...`, and so on) is siloed to that
plugin's own domain. Diagnosing a fleet-wide question ("how many of each
agent-* singleton do we have, and is anything orphaned or version-skewed")
currently requires manually enumerating the OS process table and
pattern-matching plugin names out of install paths by hand. Two real bugs
this effort tracks as motivating evidence, found exactly that way:
- **#5557** — a `worktree-manager` mux-daemon instance from 3+ versions back
  stayed alive and bound to its port long after the daemon's own cutover
  routing table had moved on several generations with no reference to it at
  all — both of the daemon's own designed retirement paths (the new daemon
  actively terminating the old one; the old daemon self-polling and
  retiring itself once superseded) apparently failed for this instance.
- **#5558** — an `agent_dispatch serve` coordinator and its own
  `supervise`/`emitter serve` worker pools drifted several minor versions
  apart, invisible without manually cross-referencing each process's own
  venv path segment.

**This effort is the downstream consumer `visions/process-telemetry`
explicitly names and defers, not a second instrumentation mechanism.** That
vision (Active) already ships the emission seam this needs: a fail-open
`set_telemetry_sink`/`emit` hook, a built-in spool sink, env/config-file
wiring, and a `process_spawn` event kind (plugin, command/verb, a stable
low-cardinality source tag, parent lineage, best-effort resource figures),
generalized from `agent_dispatch.telemetry` and `agent_bridge.telemetry`.
Its own Non-Goals are explicit — "not a second telemetry mechanism," "not an
aggregation, storage, alerting, or dashboarding system" — emission stops at
the sink boundary, and a consuming control-plane (named, but out of that
vision's own scope) is what drains, stores, and queries. This effort builds
that consumer: an inventory sink plus a query command, registered against
the existing seam, never a parallel hook.

**`libs/agent-procutil` is not the right implementation site.** It's the
canonical source (not `plugins/agent-worktrees/libs/agent-procutil/`, which
is a per-consumer *materialized copy* produced by
`tools/materialize_main.py`) for Windows-headless/detached **spawn kwargs**
(console-window suppression, kill-on-close Job Objects) — a different,
narrower concern from process self-report.

**Record identity carries installation-cell/marketplace provenance, not
just plugin name + version.** Per the installation-cell invariant
(`docs/patterns/README.md`, `visions/plugin-services/installation-cells/README.md`):
plugin name/version alone never identifies mutable state, because the same
plugin name can be installed from different marketplaces — a record with
only `(plugin, version)` would conflate two genuinely different
installations.

**Liveness is named per OS, not left as "cross-platform."** Per
`docs/patterns/graceful-daemon-cutover.md`, a process-census/liveness
primitive states Windows, Linux, and macOS explicitly and whether each is
implemented or justifiably exempted — `/proc`-style liveness is not generic
POSIX support (macOS has no `/proc`).

**The stated goal is inventory + liveness + version visibility, not a
"health" verdict.** A self-reported version cannot by itself establish
which version is *current* without an external oracle (the fleet's own
install manifest); this effort surfaces the version so a human/tool can
judge skew against a known-current reference, rather than rendering its own
health judgment.

## Request

Operator (verbatim, from the diagnosis conversation): "We really need a way
to have our processes self-report: role, [h]ost plugin, Pid, owner
sessionid, cwd, worktree, etc. I wonder if there is a way or place do track
this reliably and with consistency" — followed by "All three" when offered
the choice to (a) investigate/fix the stale processes found, (b) file both
findings as tracked issues, and (c) scope this registry idea as a real
effort (this document is (c); (a) and (b) are already done — see Journal).

## Plan

### Phase 1 — Design the inventory-consumer contract (on the existing seam)
- [ ] Confirm the `process_spawn` event kind (plugin, command/verb, source
      tag, parent lineage, resource figures) carries, or is extended to
      carry, everything the operator asked for: role, **owning plugin +
      installation-cell/marketplace provenance** (not name+version alone —
      see Context), pid (+ start-time token to guard against PID reuse),
      owner session id (when known), cwd, worktree id (when known),
      started_at. Extend the event shape via the vision's own process, not a
      parallel schema, if a field is missing.
- [ ] Design the **inventory sink**: a consumer-side sink (registered via the
      seam's existing `set_telemetry_sink`/config-file wiring, nothing new
      on the producer side) that maintains a live "currently running" table
      from the start/end `process_spawn` pairs it observes, rather than a
      spool a human tails by hand.
- [ ] Name Windows, Linux, and macOS explicitly for the liveness check (pid
      still alive + start-time token match) this sink needs to reconcile a
      crashed/uncleanly-killed process's record — state what's implemented
      vs. justifiably exempted on each, per
      `docs/patterns/graceful-daemon-cutover.md`'s own established bar.
- [ ] Design observability for a **failed or dropped registration itself**:
      the query must be able to report "N processes observed, M
      registration failures seen" (or equivalent), never silently present a
      dropped record as "nothing running."
- [ ] State the goal as **inventory + liveness + version visibility** in the
      design doc and everywhere this effort is referenced — explicitly not a
      "health" verdict (which would need an external current-version oracle
      this effort does not build).

### Phase 2 — Build the inventory sink + query command
- [ ] Implement the sink against the existing seam (no new per-plugin hook).
- [ ] A query/list command that reads the live table and renders it — the
      direct fix for "how many of each agent-* singleton do we have" without
      hand ps-archaeology.

### Phase 3 — Wire every daemon kind this effort's own evidence names
- [ ] `agent-dispatch` and `agent-bridge` already carry the telemetry seam —
      add `process_spawn` emission at their actual daemon launch sites
      (`serve`, `supervise`, `emitter serve`, `start`), since neither
      currently emits spawn events for these (the reality-doc links in
      `visions/process-telemetry` cover the seam's existence, not that every
      launch site calls it yet).
- [ ] `worktree-manager`'s mux-daemon is not yet on the seam at all, and it
      is the process that motivated #5557 — adopt `process_spawn` emission
      there as part of this phase, not deferred rollout. Completion requires
      this (per the Guiding Intent's "each agent-* singleton" promise), not
      just the two plugins that happened to also trigger #5558.
- [ ] Run the inventory sink against these three; confirm it surfaces
      equivalent findings to #5557 (an orphaned daemon) and #5558 (version
      skew across a coordinator vs. its own worker pools) without manual
      cross-referencing.
- [ ] Journal what wiring these three surfaced.

### Phase 4 — Further plugin adoption _(agent-recommended; not yet operator-requested)_
- [ ] Beyond the three daemon kinds Phase 3 already covers (which satisfy
      this effort's own completion criteria), adopt `process_spawn` emission
      in plugins not yet on the seam at all and not named by this effort's
      own motivating evidence — `agent-mcp`, `agent-containers`, and others
      as found. Update `visions/process-telemetry`'s own reality-doc links
      as each lands, per that vision's own maintenance expectation.

## Validation Plan

- [ ] Confirm the inventory query correctly reports every live daemon
      `agent-dispatch`, `agent-bridge`, and `worktree-manager`'s mux-daemon
      spawn, with the fields the operator asked for (role, plugin +
      installation-cell provenance, pid, owner session id, cwd, worktree),
      cross-checked against a manual OS-process-table sweep like the one
      that found #5557/#5558.
- [ ] Confirm a killed-uncleanly process's stale record is detected and
      reconciled (not left as a permanent false "running" entry) within one
      reconciliation cycle, on every OS named in Phase 1's liveness design.
- [ ] Confirm a dropped/failed registration is itself surfaced by the query
      (distinguishable from "nothing running") — exercise this directly
      (e.g. a sink write forced to fail) rather than only reasoning about it.
- [ ] Confirm emission is fail-open per the seam's own existing guarantee:
      a sink failure never blocks, delays, or fails the daemon's own startup.
- [ ] Re-run the same kind of fleet-wide process sweep that originally found
      #5557/#5558 against the three Phase-3 plugins and confirm the
      inventory query alone (no manual command-line archaeology) surfaces an
      equivalent orphan/skew finding, if one exists at the time.

## Proposal

_Pending — Phase 1 design decisions above need to land here once settled._

## Journal

### 2026-10-06 — Kickoff
- Effort created from a live diagnosis session: auditing a reported
  process-accumulation concern surfaced two genuine bugs (#5557, #5558,
  investigated and filed in the same session; #5557's one still-orphaned
  instance was also manually killed) and the operator's own request for a
  unified self-report mechanism, captured above verbatim.

### 2026-10-06 — Two review rounds on the planning PR (#5562) reshaped the Plan
- **Round 1 (8 findings):** the first draft proposed a new instrumentation
  hook in `agent-procutil` without checking for prior art. Review caught
  that `visions/process-telemetry` (Active) already ships the exact
  emission seam needed and explicitly forbids a second mechanism, deferring
  the query/aggregation half to a downstream consumer — which is what this
  effort actually is. Reframed the Plan around consuming that seam;
  corrected the implementation-site pointer (wrong library, and a
  materialized copy rather than the canonical source); tightened record
  identity to include installation-cell provenance; named per-OS liveness
  explicitly; added dropped-registration observability; narrowed the
  overclaimed "healthy/current" goal to inventory+liveness+version
  visibility; generalized a host-specific identifier that had leaked in.
- **Round 2 (3 findings):** moved `worktree-manager` mux-daemon adoption
  (the actual #5557 trigger) out of agent-recommended Phase 4 and into
  Phase 3's own completion criteria, since leaving it optional would let
  this effort complete while the process that motivated it stayed
  invisible; added the required `## Coordination` section for the
  independent-per-slice-worktrees branch binding; rewrote Context from a
  review-response transcript into plain current-state prose (this Journal
  entry now carries that history instead).