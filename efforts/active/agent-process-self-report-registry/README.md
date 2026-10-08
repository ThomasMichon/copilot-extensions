# Agent-Process Self-Report Registry

- **Slug:** `agent-process-self-report-registry`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-10-06
- **Status:** Draft
- **Umbrella issue:** #5559
- **Sub-issues:** #5557 (mux-daemon stale-retirement bug, found while auditing
  for this effort) · #5558 (agent-dispatch worker-pool version-skew bug, same)

**Documentation impact:** This plan defines operational process registration,
not a telemetry consumer. The boundary in
[`visions/process-telemetry`](../../../visions/process-telemetry/README.md)
distinguishes these subjects explicitly. Implementation will document the
registry's lifecycle, query interface, coverage, and bounded snapshot journal.

## Guiding Intent

Provide a stateful process register, analogous to a port-reservation ledger:
processes announce themselves on startup and remove their registration on
exit. An operator can ask for the current state at any time, grouped by role,
owning plugin, installation, session, and worktree. The register journals a
couple of snapshots an hour into a folder for on-demand historical reporting.

This is an operational state service, **not telemetry**. It does not install
a telemetry sink, consume `process_spawn` telemetry events, or export to a
formal telemetry-reporting system. The precedent is a local state/log tool
such as agent-logger: retain inspectable records and answer questions on demand.
A small daemon is the operator's preferred direction, not yet a mandated
implementation; a folder or database remains a valid storage choice.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Design the registration contract, state service, snapshot journal, and adopter rollout | `copilot-extensions` worktree |

## Coordination

- **Topology:** independent per-slice worktrees, one PR per slice.
- **Host (owns PRs):** the driving agent for each slice; no shared feature
  branch — each phase below is small enough to land as its own independent
  PR against `dev`, so no cross-slice branch coordination is needed.
- **Slice ownership:** one slice per Plan phase (contract, implementation,
  adopter rollout). A slice's own PR is the handoff point — the next slice starts
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

**Operational registration and telemetry have different contracts.**
[`visions/process-telemetry`](../../../visions/process-telemetry/README.md)
governs optional provenance/resource events sent through its emission seam.
This effort governs current membership: register, unregister, reconcile,
query, and journal state snapshots. It neither implements that vision's
proposed `process_spawn` event nor consumes its sink. Existing telemetry can
continue independently; registry adoption must not require or configure it.

_(Agent-recommended design safeguards below implement the operator's intent;
they are not additional operator requirements.)_

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

Operator (verbatim, from the diagnosis conversation): "Determine how many of
each agent-* singleton we havew, determine how many agent-mcp shims we have,
etc. We really need a way to have our processes self-report: role, phost
plugin, Pid, owner sessionid, cwd, worktree, etc. I wonder if there is a way
or place do track this reliably and with consistency" — followed by "All three" when offered
the choice to (a) investigate/fix the stale processes found, (b) file both
findings as tracked issues, and (c) scope this registry idea as a real
effort (this document is (c); (a) and (b) are already done — see Journal).

Operator clarification (verbatim, 2026-10-07):

> Ah, let's try to differentiate this from proper "telemetry" and treat this
> more like a "stateful log", more akin to the port-reservation system. We want
> a folder, DB, or little daemon (most likely) where as processes start and
> exit, they announce and remove themselves from registration. This daemon
> can be *asked* at any time for a state snapshot, and it can journal a couple
> snapshots an hour into a folder. But what it *won't* do is hook up to any
> formal telemetry sink. Kind of like how agent-logger accumulates and provides
> on-demand reporting of copilot logs/ and session-state, but absolutely does
> not hook up to a formal telemetry-reporting sink system.

The earlier request's "(a) done" means the stale instance was cleaned up,
not that the retirement root cause was fixed. #5557 and #5558 remain separate
defect trackers until their own repairs are verified.

## Plan

### Phase 1 — Design the stateful registration contract
- [ ] Survey the existing port-reservation/lease and local-log systems; reuse
      identity, storage, rendezvous, and lifecycle primitives where suitable.
- [ ] Specify register, unregister, and snapshot operations with role, plugin,
      version, PID, owner session ID, cwd, and worktree. Unknown ownership is
      explicit; do not fabricate it for host-owned services.
- [ ] Choose the folder/database/service arrangement. Prefer a small daemon
      for coordination and periodic snapshots, while retaining the operator's
      latitude on storage. Name its source owner and lifecycle before coding.
- [ ] _(Agent-recommended)_ Define owner-scoped access to registration,
      unregister, query, and stored state/snapshots. Follow
      `docs/patterns/service-transport.md`: private UDS permissions or Windows
      named-pipe DACLs; authenticated owner-scoped access if loopback TCP is
      necessary. Protect files with equivalent permissions. Reject mutation
      of another registration without its ownership proof; PID knowledge or
      claimed session/cell strings alone are not authorization. State the
      same-user trust boundary and exclude credentials/raw command payloads
      from persisted records.
- [ ] Define a snapshot journal of roughly two snapshots per hour in a folder,
      plus on-demand snapshots. _(Agent-recommended)_ Bound retention/disk
      use, write snapshots atomically, and record timestamps and coverage.
- [ ] _(Agent-recommended)_ Resolve installation-cell isolation: cell-local
      writable state with read-only enumeration, or an explicitly reviewed
      host-owned federation service that validates each caller's cell identity.
      Qualified record keys alone are not a writable-state isolation policy.
- [ ] _(Agent-recommended)_ Define PID+start-time identity, conditional removal,
      crash reconciliation, Windows/Linux/macOS support, restart recovery,
      and retry/re-registration when the registry was unavailable at startup.
- [ ] _(Agent-recommended)_ Distinguish confirmed live, stale, unknown, and
      unregistered/uncovered processes. A failed query or partial adopter
      coverage must never be reported as a complete empty process inventory.
- [ ] Require no telemetry-sink integration, telemetry configuration, or
      dependency on `process_spawn` events. This is a state protocol.

### Phase 2 — Implement registration, queries, and the snapshot journal
- [ ] Implement the reviewed state service/storage and a small registration
      client; startup/exit announce membership without blocking useful work.
      _(Agent-recommended)_ Registration failure emits a bounded local
      diagnostic and permits later retry rather than silently disappearing.
- [ ] Implement an on-demand state query and counts by role/plugin/session/
      worktree, distinguishing logical registrations from OS launcher processes.
- [ ] Implement periodic snapshot files, on-demand historical reporting,
      bounded retention, and crash/restart reconciliation. No telemetry export.

### Phase 3 — Adopt and verify the required process roles
- [ ] Register `agent-dispatch` serve, supervise, and emitter processes,
      `agent-bridge` services, and `worktree-manager` mux-daemons.
- [ ] Register `agent-mcp` shims, explicitly included in the operator's
      original census request; report their owning session and config identity
      without exposing credentials or full command-line payloads.
- [ ] Verify counts and identity against an independent OS census; mark
      uncovered roles honestly. Registration/liveness is not a health verdict
      or permission to terminate a process.
- [ ] Update runtime documentation and journal results. Further adopters such
      as `agent-containers` are deferred to a named follow-up, not an unchecked
      optional phase that prevents this effort's own completion.

## Validation Plan

- [ ] Register/unregister real test processes; queries show exactly the live
      membership with required fields and distinguish launcher/interpreter pairs.
- [ ] Exercise hard exit, PID reuse, delayed unregister, unavailable registry,
      restart, and denied OS inspection; no stale record removes a new process.
- [ ] Verify installation-cell isolation and concurrent registration/query
      behavior on the explicitly supported Windows/Linux/macOS paths.
- [ ] Attempt unauthorized register/unregister/query and state/snapshot-file
      access; all are denied under the reviewed owner scope. Test forged
      registration ownership and conditional removal without disclosing
      private session/cwd/worktree data to untrusted callers.
- [ ] Verify periodic snapshots at the chosen approximately twice-hourly cadence,
      folder output, retention bounds, atomic writes, and on-demand reports.
- [ ] Disable/remove all telemetry configuration: registration, query, and
      snapshot journaling still work. No call reaches a formal telemetry sink.
- [ ] Force registration/storage/enumeration failures and partial adopter
      coverage: queries show uncertainty, and consumer startup remains bounded.
- [ ] Cross-check all Phase 3 roles, including MCP shims, against an independent
      OS census. Use controlled stale/version-skew examples rather than depending
      on a production fault happening during validation.

## Proposal

_Pending — Phase 1 design decisions above need to land here once settled._

## Journal

### 2026-10-06 — Kickoff
- Effort created from a live diagnosis session: auditing a reported
  process-accumulation concern surfaced two genuine bugs (#5557, #5558,
  investigated and filed in the same session; #5557's one still-orphaned
  instance was also manually killed) and the operator's own request for a
  unified self-report mechanism, captured above verbatim.

### 2026-10-06 — Review rounds on the planning PR (#5562) reshaped the Plan
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
- **Round 3 (2 findings):** corrected a factual error — the `process_spawn`
  event kind `visions/process-telemetry` *names* does not exist in either
  `agent_dispatch.telemetry` or `agent_bridge.telemetry` yet (only the
  generic sink/`emit` seam and each plugin's own `task_lifecycle_event`/
  `producer_fence_event` builders are built); added implementing that event
  kind's producer-side helper as an explicit Phase 1 prerequisite, before
  Phase 3 wires any launch site to it. Also updated the umbrella issue
  (#5559), which still described the superseded "new agent-procutil hook" +
  "healthy/current" proposal, to match this reconciled plan.
- **Round 4 (1 new finding, medium severity):** the writable live-process
  table lacked installation-cell ownership isolation — qualifying each
  record with marketplace provenance (Phase 1's record-identity item) is not
  itself an isolation guarantee for a *shared* writable table, per the
  installation-cell invariant. Added an explicit Phase 1 design item: pick
  either a cell-local table per installation with read-only host-level
  enumeration, or a deliberate documented cross-cell federation contract —
  never default to an unexamined shared table.

### 2026-10-07 — Operator separates operational registration from telemetry
- The operator explicitly rejected the telemetry-consumer design: this is a
  stateful log/registration service like a port-reservation ledger, with
  startup/exit membership, on-demand state, and a couple of folder snapshots
  per hour; no formal telemetry sink. Replaced the superseded Plan and
  Validation Plan accordingly, keeping prior review history here only.
- A daemon is preferred, not mandated; folder/database choices remain open.
  Agent-recommended identity, crash recovery, cell isolation, coverage, and
  retention safeguards are labeled separately from the literal request.
- Restored MCP shim adoption to required scope because the original operator
  request expressly asked to count them. Root-cause repairs for #5557/#5558
  remain separate from earlier one-time process cleanup.
- Review of the revised stateful design required explicit owner-scoped
  transport/storage access and anti-spoofing validation. Added these as
  agent-recommended safety requirements without introducing telemetry export.