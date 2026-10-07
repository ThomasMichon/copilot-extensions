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
[`visions/process-telemetry`](../../../visions/process-telemetry/README.md) —
see Context below. Any landed work updates that vision's own reality-doc
links (currently `agent-dispatch`/`agent-bridge` `telemetry.py` only) as new
plugins adopt the seam, and `docs/patterns/graceful-daemon-cutover.md` if a
new per-OS liveness primitive is added.

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
carry.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Reconcile with `visions/process-telemetry`, close plugin-adoption gaps, build the inventory consumer, pilot it | `copilot-extensions` worktree |
| [`libs/agent-procutil`](../../../libs/agent-procutil/README.md) | **Not** the implementation site (see Context) — the repo's canonical source for headless-spawn kwargs, a different concern from process self-report | reference only |
| `visions/process-telemetry`'s existing seam (`agent_dispatch.telemetry` / `agent_bridge.telemetry`) | The instrumentation this effort builds on, not replaces | shared seam, already shipped |

## Context

**The literal trigger:** while investigating a user-reported "we're
accumulating python.exe/pwsh/conhost processes" concern on a Windows
development host, diagnosing required manually dumping every `python.exe`/
`pwsh.exe`/`conhost.exe` command line via the OS process table and
pattern-matching plugin names out of install paths, by hand. That
investigation found two genuine, already-filed bugs it would otherwise have
taken far longer to notice:
- **#5557** — a `worktree-manager` mux-daemon instance from 3+ versions back
  was still alive and bound to its port, long after the daemon's own
  `mux-daemon-routing/active.json` had moved on several generations with no
  reference to it at all. Both of the daemon's own designed retirement paths
  (the new daemon actively killing the old one; the old daemon self-polling
  and retiring itself) apparently failed for this instance. Manually
  confirmed it was genuinely orphaned and killed it.
- **#5558** — all `agent_dispatch supervise` worker pools and `agent_dispatch
  emitter serve` watchers were running several minor versions behind the
  main `agent_dispatch serve` coordinator — invisible without manually
  cross-referencing each process's own venv path segment.

**Real review finding on this effort's own planning PR (#5562) — reconciled
with existing infrastructure this first draft missed:**
- **`visions/process-telemetry`** (Active, last revised 2026-09-12) already
  defines exactly the standing capability this effort's first draft proposed
  re-inventing: a fail-open emission seam (`set_telemetry_sink`/`emit`, a
  built-in spool sink, env/config-file wiring) a runtime plugin calls at its
  own spawn sites, generalized from `agent_dispatch.telemetry` and
  `agent_bridge.telemetry`, adding a `process_spawn` event kind (plugin,
  command/verb, a stable low-cardinality source tag, parent lineage,
  best-effort resource figures). Its own Non-Goals are explicit: **"Not a
  second telemetry mechanism"** and **"Not an aggregation, storage, alerting,
  or dashboarding system"** — emission stops at the sink boundary; a
  **consuming control-plane** (named, but explicitly out of that vision's
  scope) is what drains, stores, and queries. **This effort IS that
  consumer** — it must not add a parallel hook.
- **`libs/agent-procutil` is the wrong implementation site regardless.** It's
  the canonical source for Windows-headless/detached **spawn kwargs**
  (console-window suppression, kill-on-close Job Objects) — a different,
  narrower concern from process self-report. The first draft also pointed at
  `plugins/agent-worktrees/libs/agent-procutil/`, a per-consumer *materialized
  copy* (`tools/materialize_main.py`'s output for that one consumer), not the
  canonical source — editing a materialized copy directly would silently
  diverge from `libs/agent-procutil/` and fail `tools/sync-vendored-libs.py
  --check`. Corrected throughout.
- **Record identity needs installation-cell/marketplace provenance, not just
  plugin name + version.** Per the installation-cell invariant
  (`docs/patterns/README.md`, `visions/plugin-services/installation-cells/README.md`):
  plugin name/version alone never identifies mutable state, because the same
  plugin name can be installed from different marketplaces. A record that
  only carried `(plugin, version)` would conflate two genuinely different
  installations. Added to the Phase 1 record-shape design item below.
- **Per-OS liveness must be named explicitly, not left as "cross-platform."**
  Per `docs/patterns/graceful-daemon-cutover.md`, a process-census/liveness
  primitive must state Windows, Linux, and macOS by name and whether each is
  implemented or justifiably exempted — `/proc`-style liveness is not generic
  POSIX support (macOS has no `/proc`). Added explicitly below.
- **A failed/dropped self-report must remain observable, not silently
  degrade the query into a false-complete inventory.** If a daemon's
  registration write is dropped (disk full, permissions, a bug), the query
  must distinguish "nothing is running" from "something didn't report" —
  otherwise the exact orphan/skew class this effort exists to catch could
  itself go unreported by the tool meant to catch it. Added to Phase 1 and
  the Validation Plan.
- **"Healthy/current" was overclaimed.** A self-reported version cannot by
  itself establish which version is *current* without an external oracle
  (the fleet's own install manifest). Narrowed the stated goal to
  **inventory + liveness** (is it running, what is it, who owns it) plus
  **version visibility** (surface the version so a human/tool can judge
  skew against a known-current reference) — not a self-contained "health"
  verdict.
- **Phase 4 scope was ambiguous against the stated Guiding Intent.**
  Clarified below: the two plugins that triggered this effort
  (`agent-dispatch`, `agent-bridge`) **already carry the telemetry seam** —
  wiring their spawn sites (`serve`, `supervise`, `emitter serve`,
  `start`) to actually call it is Phase 3 pilot-adjacent work, not deferred
  rollout. Only *new* plugin adoption (`worktree-manager`, `agent-mcp`,
  `agent-containers`, anything else not yet on the seam at all) is the
  agent-recommended Phase 4 stretch goal.

## Request

Operator (verbatim, from the diagnosis conversation): "We really need a way
to have our processes self-report: role, [h]ost plugin, Pid, owner
sessionid, cwd, worktree, etc. I wonder if there is a way or place do track
this reliably and with consistency" — followed by "All three" when offered
the choice to (a) investigate/fix the stale processes found, (b) file both
findings as tracked issues, and (c) scope this registry idea as a real
effort (this document is (c); (a) and (b) are already done — see Context).

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
- [ ] Narrow the stated goal, in the design doc and anywhere this effort is
      referenced elsewhere, to **inventory + liveness + version visibility**
      — explicitly not a "health" verdict (which would need an external
      current-version oracle this effort does not build).

### Phase 2 — Build the inventory sink + query command
- [ ] Implement the sink against the existing seam (no new per-plugin hook).
- [ ] A query/list command that reads the live table and renders it — the
      direct fix for "how many of each agent-* singleton do we have" without
      hand ps-archaeology.

### Phase 3 — Wire the two triggering plugins + pilot
- [ ] `agent-dispatch` and `agent-bridge` already carry the telemetry seam —
      confirm/add `process_spawn` emission at their actual daemon launch
      sites (`serve`, `supervise`, `emitter serve`, `start`), since neither
      currently emits spawn events for these (the reality-doc links in
      `visions/process-telemetry` cover the seam's existence, not that every
      launch site calls it yet).
- [ ] Run the inventory sink against these two as the pilot; confirm it
      surfaces an equivalent finding to #5558 (version skew across a
      coordinator vs. its own worker pools) without manual cross-referencing.
- [ ] Journal what the pilot surfaced.

### Phase 4 — Wider plugin adoption _(agent-recommended; not yet operator-requested)_
- [ ] Adopt the `process_spawn` emission in plugins not yet on the seam at
      all — starting with `worktree-manager`'s mux-daemon (the #5557
      trigger), then `agent-mcp`, `agent-containers`, and others as found.
      Update `visions/process-telemetry`'s own reality-doc links as each
      lands, per that vision's own maintenance expectation.

## Validation Plan

- [ ] Confirm the inventory query correctly reports every live daemon the
      pilot plugins (`agent-dispatch`, `agent-bridge`) spawn, with the
      fields the operator asked for (role, plugin + installation-cell
      provenance, pid, owner session id, cwd, worktree), cross-checked
      against a manual OS-process-table sweep like the one that found
      #5557/#5558.
- [ ] Confirm a killed-uncleanly process's stale record is detected and
      reconciled (not left as a permanent false "running" entry) within one
      reconciliation cycle, on every OS named in Phase 1's liveness design.
- [ ] Confirm a dropped/failed registration is itself surfaced by the query
      (distinguishable from "nothing running") — exercise this directly
      (e.g. a sink write forced to fail) rather than only reasoning about it.
- [ ] Confirm emission is fail-open per the seam's own existing guarantee:
      a sink failure never blocks, delays, or fails the daemon's own startup.
- [ ] Re-run the same kind of fleet-wide process sweep that originally found
      #5557/#5558 against the two pilot plugins and confirm the inventory
      query alone (no manual command-line archaeology) surfaces an
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

### 2026-10-06 — Reconciled with `visions/process-telemetry` after review (PR #5562)
- The first draft proposed a new hook in `agent-procutil` without checking
  for prior art. Real review (PR #5562, 8 findings) caught that an **Active**
  vision, `visions/process-telemetry`, already defines the exact emission
  seam this needs, explicitly forbids a second mechanism, and explicitly
  defers the query/aggregation half to a downstream consumer — which is what
  this effort actually is. Reframed the whole Plan around consuming that
  seam rather than inventing one; corrected the implementation-site pointer
  from a materialized `agent-procutil` copy to recognizing it's the wrong
  library entirely (headless-spawn-kwargs, not process self-report);
  tightened record identity (installation-cell provenance, not just
  plugin+version), named per-OS liveness explicitly, added dropped-
  registration observability, narrowed "healthy/current" to "inventory +
  liveness + version visibility," and resolved the Phase 4 scope ambiguity
  (the two triggering plugins already have the seam — wiring them is Phase
  3, not deferred rollout).
