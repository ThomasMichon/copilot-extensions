# Agent-Process Self-Report Registry

- **Slug:** `agent-process-self-report-registry`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-10-06
- **Status:** Draft
- **Vision:** [Process Registry](../../../visions/process-registry/README.md)
- **Umbrella issue:** #5559
- **Sub-issues:** #5557 (mux-daemon stale-retirement bug, found while auditing
  for this effort) · #5558 (agent-dispatch worker-pool version-skew bug, same)

**Documentation impact:** This campaign derives from the new Process Registry
vision and its [architecture proposal](architecture.md), not a telemetry consumer. The boundary in
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
If the daemon model is adopted, there is **one host-owned broker**, shared
across plugins, marketplaces, versions, and worktrees, in the designated
operator account's security scope. Producers drop small files through
in-process facilities or existing invokers; no process exists just to report
another process. File-watch notifications accelerate ingestion but do not
replace periodic reconciliation. [Architecture](architecture.md) specifies
the proposed ownership, failure boundaries, identity, quotas, and recovery.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Draft and review the contract, implement it in later slices, and validate adopter coverage | `copilot-extensions` worktree |

## Coordination

- **Topology:** independent per-slice worktrees, one PR per slice.
- **Host (owns PRs):** the driving agent for each slice; no shared feature
  branch — each phase below is small enough to land as its own independent
  PR against `dev`, so no cross-slice branch coordination is needed.
- **Slice ownership:** one slice per Plan phase (contract, broker, clients,
  validation). A slice's own PR is the handoff point — the next slice starts
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

**Reusable building blocks, not a second process spawner.** Canonical
`libs/agent-procutil` supplies both pure flag helpers and actual spawn wrappers.
Only the latter know child PIDs and are candidates for check-in integration;
pure kwargs helpers remain side-effect-free. Consumer materialized copies
are not edit sites. `single-instance-lease` supplies an OS-exclusive role lease;
`endpoint-rendezvous` supplies atomic discovery records; `zdd` has native
Windows/Linux process-identity prior art; and `ssh-manager` has a bounded
background file writer suitable as a performance precedent. None is claimed
to implement this registry today. See [architecture](architecture.md).

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

**Summary of settled operator intent:** inventory all participating process
roles, including Python/shell/console-host children and MCP shims; attach
role/source/PID/session/cwd/worktree identity; use one optional global local
handler, file check-ins, on-demand views, and a couple of folder snapshots per
hour. No formal telemetry sink, no reporter subprocess, and no dependency of
useful work on the monitor. Worktree Manager is a proposed lifecycle owner,
not a mandatory dependency of every plugin.

The operator selected the **operator-account** scope: one host broker for the
designated operator, shared across installations/worktrees; other accounts
cannot bootstrap separate brokers for this capability.

Literal request and subsequent clarifications:
[inception transcript](inception-transcript.md). The detailed mechanisms and
initial resource budgets in [architecture](architecture.md) are explicitly
agent-recommended. One-time stale-process cleanup does not close the root-cause
defects #5557/#5558.

## Plan

### Phase 1 — Design the stateful registration contract
- [x] Capture the file-check-in, one-broker, no-helper, no-telemetry requirements
      and the designated-operator scope; derive the [vision](../../../visions/process-registry/README.md).
- [x] Draft the [architecture proposal](architecture.md), grounded in the
      existing lease, discovery, process-identity, and bounded-file-writer
      primitives; separate proposals from measured or implemented claims.
- [ ] Review and settle Worktree Manager's host ownership, canonical client
      home, operator-account federation/permissions, and native OS support.
- [ ] Review the strict-single-broker update exemption: no active/passive
      overlap; bounded file spooling and stale-query answers during replacement.
- [ ] Convert proposed latency/CPU/backlog/retention values into explicit,
      testable implementation acceptance budgets before runtime rollout.

### Phase 2 — Implement the one host broker
- [ ] Implement host discovery, the global role lease, authorized mailbox
      catalog, watcher hints plus bounded recovery scans, and private live state.
- [ ] Implement identity-safe start/end ingestion, deduplication, crash
      reconciliation, uncertainty, bounded enrichment, and quota diagnostics.
- [ ] Implement owner-scoped on-demand queries, approximately twice-hourly
      atomic snapshot files, bounded history, and safe one-broker upgrades.
- [ ] Document source ownership and native Windows/Linux/macOS behavior,
      including explicitly unverified foreign PID namespaces.

### Phase 3 — Integrate clients and the required process roles
- [ ] Add optional in-process check-in support to real shared process invokers
      and safe self-announcement points. No subprocess/daemon per report,
      no side effects in flag helpers, and no reporting I/O on critical paths.
- [ ] Cover `agent-dispatch` serve/supervise/emitter roles, `agent-bridge`,
      Worktree Manager mux-daemons, MCP shims, and their managed script/native
      children. Prove or label console-host attribution; separate logical and
      physical counts.
- [ ] Preserve independent plugin behavior when the handler is absent. Test
      managed `.py`/`.ps1`/`.sh` and actual invoker paths, not just a fake API.

### Phase 4 — Validate, document, and transfer further adoption
- [ ] Execute the validation matrix below and benchmark the reviewed budgets.
- [ ] Validate controlled stale/version-skew examples and a real OS census;
      partial coverage is explicit, never a false "nothing running" result.
- [ ] Update runtime docs and journal landed work; transfer any further
      adopters such as `agent-containers` to a named tracker before completion.

## Validation Plan

- [ ] Verify zero reporting subprocesses and no competing brokers under
      multi-plugin, multi-installation, concurrent-start, and upgrade scenarios.
- [ ] Exercise absent/offline/frozen handler, saturated queues, stuck producer
      file writes, disk-full/access failures, and enabled-spool quotas; useful
      work and exit never wait for registration or unbounded retries.
- [ ] Reorder/duplicate starts and ends, miss all watcher notifications,
      overflow the watcher, replace directories, and crash between ingestion
      commit and ticket cleanup; replay does not lose identity or resurrect exits.
- [ ] Reuse PIDs, restart the host/broker, deny OS inspection, and provide
      foreign-domain PIDs; unknown never becomes dead or misattributed current.
- [ ] Test native Windows/Linux/macOS identity and filesystem paths, exact
      ticket deletion, permission/reparse/symlink boundaries, and cross-account
      access denial. Same-user claimed tags remain labeled rather than trusted.
- [ ] Verify snapshots twice hourly, atomic publication, history/count/byte
      retention caps, timestamp freshness, pagination, and on-demand queries.
- [ ] Remove all telemetry configuration; registration, queries, and snapshots
      still work with no telemetry-sink call or telemetry event prerequisite.
- [ ] Cross-check required physical/logical roles against an independent census,
      including wrappers, MCP shims, short-lived shells and console-host inference.
- [ ] Benchmark producer latency, daemon CPU, queue/thread growth, watcher storms,
      and disk growth at the [architecture's proposed scales](architecture.md#8-validation-and-acceptance-gates);
      qualify actual release budgets from measurements, not reasoning alone.
- [ ] Exercise strict-singleton replacement, failed activation, rollback, and
      crash recovery; no two broker writers/instances coexist, and staged
      check-ins survive the agreed brief service gap.

## Proposal

[File-check-in architecture proposal](architecture.md) is the reviewable design.
It does not claim an implemented service, validated performance, or repaired
retirement/version-skew defects. The literal inception record is
[here](inception-transcript.md).

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
- Added the repo's conditional graceful-cutover design/validation obligation
  if a resident daemon is chosen, and preserved independent plugin operation
  when this optional registration service is absent.

### 2026-10-07 — File-check-in architecture and separate vision drafted
- PR #5562 landed the prior non-telemetry planning contract; no implementation
  followed it. The operator then required one shared broker and no reporting
  subprocess, selecting the operator-account security scope when asked.
- Drafted a separate Process Registry vision and linked architecture: immutable
  file tickets, a host-owned single writer, watcher hints plus recovery scans,
  exact OS identity, a bounded snapshot journal, and explicit uncertainty.
- The architecture documents the unavoidable filesystem-stall caveat and
  chooses off-critical-path writers/parent registration rather than promising
  that synchronous shell file writes can have a hard timeout.
- Worktree Manager ownership, private SQLite, mailbox quotas, staggered native
  inspection, and strict-singleton replacement are labeled recommendations for
  review. The process-telemetry emission mechanism remains untouched.