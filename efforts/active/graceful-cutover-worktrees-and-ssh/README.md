# Graceful daemon cutover — a binding design invariant for every agent-* daemon

- **Slug:** `graceful-cutover-worktrees-and-ssh`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase feature branches / independent per-slice PRs
- **Created:** 2026-09-28
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends [`visions/plugin-services`](../../../visions/plugin-services/README.md)
  §`zero-downtime-cutover` — applies its already-generalized zero-downtime
  service model to the three plugins not yet covered by
  `docs/patterns/graceful-daemon-cutover.md`'s own rollout, then elevates
  that pattern from a per-plugin convention to a binding design invariant
  for every `agent-*` daemon.
- **Umbrella issue:** _pending_
- **Sub-issues:** _pending_

## Guiding Intent

Close three of the remaining gaps in an already-proven pattern (not the
*last* gap overall — the pattern's own adoption table separately still
carries `agent-vault` at `❌ None` and an open outlive/reconnect item for
`agent-index`'s engine daemon; both are out of this effort's scope):
`docs/patterns/graceful-daemon-cutover.md`
and the shared `zdd` library have made agent-bridge, agent-dispatch, and
agent-index (and, per that doc's own rollout table, agent-mcp) update
without ever killing in-flight, non-resumable work. `agent-worktrees`,
`worktree-manager`, and `agent-ssh` (ssh-manager) are the pattern's own
"remaining plugins" — not yet in its Per-plugin adoption table at all. This
effort adopts the *existing* protocol for those three rather than inventing a
parallel one, so a daemon update on any host, for any of these systems, is
never a source of stacked stale processes, and never silently corrupts
in-flight state the way today's ad-hoc `status-monitor-restart`/`mux-daemon
ensure` reaping already does.

**Expanded charter (operator direction, 2026-09-28 — see Request below):**
beyond landing the three-plugin rollout, this pattern must become a
**binding design invariant** every `agent-*` plugin with a daemon is audited
against at design time, implementation time, and review time — not a
convention a plugin can simply not adopt — **and** the facility needs
**diagnostic + self-repair tooling** so an abnormality (a stray duplicate
daemon, a stranded passive, a stale generation) can be detected and safely
fixed up wherever it's found, not just hand-remediated per incident the way
today's `status-monitor` duplicate was.

## Coordination

Solo effort, single host, no delegates today.

- **Topology:** independent per-phase PRs (each phase lands as its own
  reviewable change; no shared feature branch needed since phases are
  sequential, not concurrent).
- **Host (owns PRs):** the driving agent/session, whichever is current.
- **Delegates:** none at present.
- **Handoff:** a resuming agent picks up from the last unchecked Plan item
  and this Journal's latest entry — no cross-participant handoff protocol
  needed until this effort actually gains a delegate.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent/session | Plans, implements, and drives every phase | Whichever worktree/session is currently head |

## Context

This effort's seed is the live incident this same session drove end-to-end:
diagnosing and fixing a facility-wide mux status-bar freeze traced through
three real bugs in `agent-worktrees`'/`worktree-manager`'s resident daemons
(PRs #4335, #4377, #4386), then finding — while deploying those fixes to a
second host — that duplicate `status-monitor` processes can accumulate with
no bounded, automatic cleanup at all. The operator's own request (below)
independently re-derived the shape of a protocol that **already exists**:

- **`docs/patterns/graceful-daemon-cutover.md`** — the canonical pattern.
  Read this in full before touching any phase below; it defines the
  consumer contract, the generation self-retire predicate (staleness-check-
  before-continuing, exactly what the operator asked for), the
  never-promoted-abandoned-passive fix, and the per-plugin adoption table
  this effort's job is to extend.
- **`libs/zdd/`** (package `agent-zdd`, import `zdd`) — the shared,
  consumer-agnostic primitive: `zdd.routing` (file-based `active.json`
  routing table, no front proxy), `zdd.cutover.CutoverOrchestrator` (spawn
  passive → health-gate → flip → drain → retire, with rollback and
  commit-forward), `zdd.breadcrumb` (stranded-survivor recovery and
  abandoned-passive reaping). Vendored byte-identically into each real
  consumer's own `libs/zdd/` — never a shared runtime import.
- **Confirmed today, by direct inspection, that the adoption gap is real**:
  - `agent-bridge`, `agent-dispatch` plugin.json: `"zeroDowntimeUpdate": true`.
    `agent-worktrees`, `worktree-manager`, `agent-ssh` plugin.json: **absent**.
  - `agent-ssh` has `zdd` vendored under `plugins/agent-ssh/libs/zdd/` but
    **zero** references to `CutoverOrchestrator`/`cutover` anywhere in its own
    `src/` — the library is present but entirely unwired.
  - `agent-worktrees`/`worktree-manager` have **no `zdd` presence at all**.
    They already use the sibling `durable-vs-versioned-runtime` shape
    (`versions/<N>/` slots + a `current-version` marker + rewritten
    binstubs — directly observed and manipulated this session via
    `agent-worktrees update`/`worktree-manager update`), but that shape only
    covers the *installed slot*; it says nothing about a *live daemon
    process* (the resident `status-monitor` / companion `mux-daemon`)
    noticing a newer slot exists and cutting over.
  - **Resolved (Copilot review, round 1):** the "who drives the installer"
    question is answered inside this repo, not an external `dotfiles`
    repo — `agent_worktrees.reconcile.runtime_installer_argv`
    (`plugins/agent-worktrees/src/agent_worktrees/reconcile.py:1298-1324`)
    already reads a **sibling** plugin's `plugin.json["zeroDowntimeUpdate"]`
    and appends `-ZeroDowntime` when reconcile-driving that plugin's own
    `install.ps1 update`. **But agent-worktrees' own installer
    (`plugins/agent-worktrees/scripts/install.ps1`) does not yet accept that
    switch at all** — so the flag and the installer support are not
    independent steps; see Phase 1's corrected ordering below.
- **Today's live incident, as the motivating validation case**: two
  `status-monitor` processes were found running simultaneously on one host,
  both already on the fixed version — not a version-supersession race
  `status-monitor-restart` reaps (it only reaps a version-*superseded*
  owner; a same-version duplicate is left alone). Remediated manually this
  session (confirmed the lock owner, killed the duplicate via
  `procs.terminate_pid_if_identity`) and documented as a stop-gap in this
  repo's own `agent-worktrees-authoritative-daemon` effort Journal, plus a
  generic operational-runbook entry in a downstream adopter's own error-
  response documentation — **this effort is the real fix that makes that
  manual remediation unnecessary.**

## Request

> We need to ensure that `agent-worktrees` and `worktree-manager` have their
> install/update flows handle this *automatically*. The result after an
> install/update flow must be that the old (now stale) processes terminate
> in a bounded amount of time. For `agent-worktrees` and `worktree-manager`,
> it's okay if a daemon has to finish one final sweep, to maintain
> transactional integrity, but we then need it to detect that it's no
> longer the current version and exit. It must also force-disconnect any
> "subscribers", which should nudge those subscribers to re-discover the
> correct, updated port from the new deployment. `agent-bridge`,
> `agent-dispatch`, `ssh-manager`, `agent-index` etc must all work this way:
> no long-lived daemons may stack up excessively.
>
> It's *possible*, during a rapid-fire release flow, that while we're busy
> performing one update, a new one will come in. So by the time an agent-*
> daemon process gets installed, it will no longer be the current version.
> We want to ensure that each daemons regularly checks its staleness,
> before any repeated loop, subscription connection, request, etc., so they
> terminate early. Outgoing processes don't need to take responsibility for
> booting their successors; that's the installer's job. The install/update
> must always launch the new version after doing the version-dance, booting
> the new version and ensuring it's running. The new version registers its
> port for discovery and handles new requests, and the old one gracefully
> exits and "hands off" any outstanding work (session hosts, index tasks,
> emitter/evaluator tracking, etc.) to the new process by leaving a
> manifest of process pointers and metadata for the new process to
> discover and pick up in its next sweep.

**Reconciling the request against the existing pattern (agent-recommended
framing, not a change of scope):** every element the operator described
already has a named counterpart in `graceful-daemon-cutover.md` —
"finish one final sweep, then detect staleness and exit" is *generation
self-retire*; "force-disconnect subscribers, nudge them to re-discover the
port" is the *routing-table flip* + *drain*; "the installer boots the new
version, not the old one" is *Invariant #1* (installer-driven, automatic,
no operator verb); "a manifest of process pointers for the new process to
pick up" is the *breadcrumb* + per-daemon *outlive-and-reconnect* story
(exactly what agent-index's engine daemon and agent-dispatch's
supervisor/workers already do via their own durable state). `agent-bridge`/
`agent-dispatch`/`agent-index` are **already done** per the pattern's own
table (only minor open items remain, e.g. agent-index's engine
outlive-and-reconnect proof, and are out of this effort's scope — track
them under the existing pattern doc / their own efforts). **This effort's
actual net-new scope is exactly three plugins the pattern doc does not yet
cover: `agent-worktrees`, `worktree-manager`, `agent-ssh`.**

**Round 2 (2026-09-28):**

> Drive this in a handoff. We need this to be a design invariant of all
> agent-* plugins and their daemons, thoroughly audited for during design,
> implementation, and review, and we need to have diagnostic and
> self-repair steps, to help detect abnormalities and allow careful fixup
> when this does occur.

_(agent-recommended structuring of the above, not the operator's own
phrasing)_: reconciled into two new phases below (Phase 5 — binding design
invariant; Phase 6 — diagnostic + self-repair tooling) plus a handoff to a
successor session, since Phases 1-4's own execution is unstarted and
substantial in its own right.

## Plan

### Phase 0 — Confirm the exact integration seam per plugin
- [ ] `agent-worktrees`: confirm the native `update` command's implementation
      path (already located this session: `update_cli.py` / the
      `_load_full_command_surface`/version-marker machinery); identify the
      exact point where it currently rewrites `current-version` and decide
      where a live-daemon-cutover check must be inserted.
- [ ] `worktree-manager`: same for its own `update` command
      (`worktree_manager/__main__.py`'s update path); note the *separate*
      resident `mux-daemon` (companion process, not the Picker CLI itself)
      needs its own cutover, distinct from the Picker/CLI's own
      self-versioning.
- [ ] `agent-ssh`: **corrected premise (Copilot review, round 1)** — this is
      NOT a persistent-daemon system. `plugins/agent-ssh/README.md:10`
      states the CLI "does not require a harness, daemon, or sibling
      plugin"; `libs/ssh-manager/README.md:39-51` states its Windows proxy
      broker explicitly "lives in the calling process and closes with its
      SSH root... No persistent broker service or cached loopback port is
      created." There is no long-lived resident process here for `zdd`
      cutover semantics to attach to. Audit instead whether
      `ssh_manager.forward_keeper` (`libs/ssh-manager/src/ssh_manager/forward_keeper.py`)
      or any other spawned child can genuinely outlive its parent/session
      across a release and accumulate — if nothing does, this phase's
      correct conclusion may be "no cutover needed here; the
      [`ephemeral-process-reaping`](../../../docs/patterns/ephemeral-process-reaping.md)
      pattern (if not already applied) is the right fix for any leak found,
      not `zdd`." Do not force this system into the cutover shape if the
      audit finds nothing that needs it.
- [ ] For each daemon confirmed to genuinely exist (not assumed), name the
      concrete **safe cutover point** (the drain boundary). **Correction
      (Copilot review, round 4):** a sweep-tick boundary alone is too weak
      for `status-monitor` — `status_monitor_cli.cmd_status_monitor` also
      serves concurrent hook/classify/tracking-write requests and tracks
      active handlers and in-flight writes *separately* from the sweep
      loop, so a generation could retire between sweeps while one of those
      is still non-resumably in flight. The real drain boundary is
      **admission closed (refuse new hook/classify/tracking-write
      connections) AND every already-active handler/write has drained** —
      not merely "between sweeps":
      - `agent-worktrees status-monitor`: admission closed + sweep between
        ticks + every active hook/classify/tracking-write handler drained.
      - `worktree-manager mux-daemon`: between mapping-registry mutations /
        republish cycles (confirm during Phase 2 whether it has an
        equivalent concurrent-handler surface needing the same admission-
        closed treatment, rather than assuming a simpler shape by default).
      - `agent-ssh`: pending Phase 0's audit outcome above.

### Phase 1 — `agent-worktrees` `status-monitor`
- [x] Vendor `zdd` into `plugins/agent-worktrees/libs/zdd/` (byte-identical
      sync from `libs/zdd/`, per the pattern's own sync convention).
- [x] Implement the consumer contract: `spawn_passive`, `health_check`,
      `make_client` (drain/undrain/shutdown), `pick_free_port`.
- [x] Wire `agent-worktrees update`'s activation path to invoke
      `CutoverOrchestrator` automatically whenever it detects a live
      `status-monitor` (no operator flag, no new CLI verb — Invariant #1).
- [x] Add the **generation self-retire** loop (`self_retire.is_superseded`)
      inside the resident sweep loop itself, gated per the pattern's own
      "before any repeated loop, subscription connection, request" framing
      — checked at the top of every sweep iteration, not only at daemon
      startup, so a same-tick rapid-fire re-release is still caught.
- [x] **Land `plugin.json`'s `"zeroDowntimeUpdate": true` atomically with
      `scripts/install.ps1`/`install.sh` actually accepting and implementing
      automatic cutover on BOTH platforms, in the same PR — never as
      separate steps, and never Windows-only.**
      (Copilot review, round 1): `agent_worktrees.reconcile.runtime_installer_argv`
      **already** reads this flag and appends `-ZeroDowntime` to a reconcile-
      driven `install.ps1 update` for any plugin that sets it, but
      agent-worktrees' own `scripts/install.ps1` does not yet declare that
      parameter at all — setting the flag first, before the installer
      supports it, would break reconcile-driven self-updates with a
      parameter-binding failure the moment anything reconciles
      agent-worktrees itself the same way it reconciles siblings.
      **Correction (Copilot review, round 4):** `runtime_installer_argv`
      appends `-ZeroDowntime` only on the `install.ps1` (Windows) branch;
      its `install.sh` (POSIX) branch receives no equivalent argument today.
      Define and implement the **POSIX activation path explicitly** (a
      matching `install.sh update` seam, or the SIGTERM-graceful-restart
      fallback the pattern doc's own Cross-platform-parity table already
      describes for other plugins) alongside the Windows switch — do not
      ship Windows-only and call the flag "done."
- [x] Define the **hand-off manifest**: what "outstanding work" a
      `status-monitor` generation must persist for its successor (per-session
      claim/observation state it would otherwise reconstruct from scratch —
      confirm whether this is already fully derivable from existing durable
      state, e.g. the tracking dir + managed-mux registry, or needs a new
      breadcrumb).

### Phase 2 — `worktree-manager` `mux-daemon`
- [x] Same shape as Phase 1, scoped to the companion `mux-daemon` process
      specifically (not the Picker CLI's own one-shot invocations).
      Hand-off manifest: the `mux_mapping_registry`'s own on-disk state is
      already the durable source of truth (confirmed this session) — likely
      needs no *new* breadcrumb, only a successor that reads it on boot
      (already true) and a predecessor that stops writing before exiting.
- [x] Apply Phase 1's same atomic-landing lesson here first: confirm whether
      `worktree-manager` has (or `agent-worktrees` reconcile has) an
      analogous flag/installer-argv coupling before setting any manifest
      flag ahead of real installer support.

### Phase 3 — `agent-ssh` (ssh-manager): audit first, cutover only if warranted
- [x] Run the Phase 0 audit (`forward_keeper.py` + any other spawned
      children) to conclusion. **Conclusion:** `agent-ssh` still has no
      persistent daemon/harness/broker service; the Windows proxy broker
      remains strictly in-process, matching the README contract.
- [x] If the audit finds a genuine long-lived/leak-prone process: scope a
      right-sized fix (full `zdd` cutover only if it is truly a persistent,
      stateful daemon; otherwise the lighter `ephemeral-process-reaping`
      pattern is more likely correct). **Applied narrowly:** the detached
      `forward_keeper` now records and reaps its owned reverse-forward child
      pid on stale-state replacement, closing the one real orphan/stacking
      seam the audit found without inventing daemon cutover machinery.
- [x] If the audit finds nothing: close this phase as "no cutover needed,"
      not "done" — record the audit finding in the Journal so a later
      re-check isn't repeated from scratch, and drop `agent-ssh` from
      Phase 4's pattern-doc update (only real adopters belong in that
      table). **Result:** no `agent-ssh` adoption row was added to
      `docs/patterns/graceful-daemon-cutover.md`; Phase 3 closed as audit +
      targeted ephemeral-process hygiene only.

### Phase 4 — Close the loop in the pattern doc itself
- [ ] Add `agent-worktrees` and `worktree-manager` rows to
      `docs/patterns/graceful-daemon-cutover.md`'s **Per-plugin adoption**
      table and **Rollout sequencing** list once Phases 1-2 land.
- [ ] Add an `agent-ssh` row **only if** Phase 3's audit finds a real daemon
      and lands actual cutover work; if the audit instead concludes "no
      cutover needed" (Phase 3's own stated possible outcome), do NOT add a
      row implying adoption — note the audit conclusion in this effort's
      Journal instead, so Phase 3 and Phase 4 never contradict each other.
      Keep the pattern doc as the single source of truth for adoption state
      either way (never let this effort's own README become a second,
      drifting copy of that table).

### Phase 5 — Elevate to a binding design invariant (operator round 2)
- [ ] Update `docs/patterns/graceful-daemon-cutover.md`'s own **Invariants**
      section: today it binds *how* a cutover behaves once a plugin adopts
      the pattern (no stop-then-start, never strand clients, drain-gated
      retirement, etc.) but does not yet bind *which* plugins must adopt it
      at all. Add an explicit invariant: **any `agent-*` plugin that owns a
      long-lived resident daemon (the `docs/patterns/README.md` "Runtime
      service" plugin shape, or any daemon meeting that description
      regardless of shape label) MUST implement this pattern** — not an
      opt-in convention.
- [ ] Update `docs/patterns/README.md`'s **Plugin shapes** table (or its
      accompanying **Design principles** list) so classifying a plugin as
      "Runtime service" (or adding a new resident daemon to any plugin)
      carries an explicit, visible pointer to this requirement — the
      classification step itself is the natural **design-time** audit
      point (a plugin design/effort that introduces a daemon must reconcile
      against this invariant the same way Design principle 0 already
      requires vision reconciliation).
- [ ] **Implementation-time audit**: add the requirement to `CONTRIBUTING.md`
      (near its existing "Documentation impact" / review-gate conventions)
      so a PR introducing or materially changing a resident daemon must
      state how it satisfies (or is exempted from, with justification) the
      graceful-cutover invariant — mirroring how `CONTRIBUTING.md` already
      makes "Documentation impact" a required PR-description statement.
- [ ] **Review-time audit**: investigate whether an automated guard is
      feasible (a script in the `check-*.py` family, e.g.
      `check-module-size.py`/`check-changefile-presence.py`'s own shape) that
      can detect a plugin newly introducing a long-lived resident process
      (a `serve`/daemon-style entry point, a `while True` service loop) with
      no corresponding `zdd` vendoring/consumer-contract implementation, and
      fails CI the way those other guards do. If a reliable automated
      signal isn't feasible, fall back to the CONTRIBUTING.md checklist
      item above as the review-time gate instead, and say so explicitly
      rather than leaving review-time enforcement silently unaddressed.

### Phase 6 — Diagnostic + self-repair tooling (operator round 2)
- [ ] Design a **generic, plugin-agnostic daemon-health audit** capability
      (natural home: `libs/zdd/` itself, e.g. a new `zdd.diagnostics`
      module, since it already owns the routing table + breadcrumb shapes
      every consumer's lock/generation state is built from) that can, for
      any consumer, detect the concrete abnormality classes this session's
      own incident and prior field sightings already named:
      - a **duplicate resident daemon** (more than one live process
        claiming the same lock/routing-table `active` slot) — this
        session's own `status-monitor` incident.
      - a **never-promoted abandoned passive** (already named and handled
        in the pattern doc's own § Never-Promoted Abandoned Passive —
        confirm the diagnostic surfaces this uniformly too, not just
        agent-bridge/agent-dispatch's own bespoke reap loops).
      - a **stranded survivor from an aborted cutover** (already named in
        `zdd.breadcrumb.recover_stale_cutover` — same ask: surface it
        uniformly, don't just rely on each consumer's own ad-hoc wiring).
      - a **stale (superseded) generation still running** past its own
        self-retire window (the generation self-retire predicate exists,
        but a diagnostic should be able to answer "is any daemon in this
        state right now?" without waiting for self-retire to notice on its
        own next poll).
    Each check should reuse (not re-derive) the exact validated-owner +
    identity-bound-termination discipline this session's own duplicate-
    `status-monitor` remediation established (`locks.read_lock`/
    `lock_is_live` for a confirmed live owner; `procs.terminate_pid_if_identity`
    for the actual fixup) — the design already proven correct through three
    rounds of review on that remediation recipe (see the
    `agent-worktrees-authoritative-daemon` effort Journal, 2026-09-28 entry).
- [ ] Expose it as a **consistent, per-plugin `doctor`-style surface**: each
      consuming plugin's own `doctor`/health-check command reports its
      daemon(s)' audit findings using the shared module, rather than every
      plugin growing its own bespoke detection logic (mirrors how `zdd`
      itself is vendored byte-identically rather than reinvented per
      plugin).
- [ ] **Self-repair, gated correctly**: an automatic fixup path must never
      run destructively without the same "validated live owner, or stop and
      re-enumerate" floor this session's remediation recipe established —
      distinguish a **report-only** mode (safe to run unattended, e.g. from
      a periodic sweep or `doctor`) from an **apply** mode (performs the
      actual identity-bound termination), and default to report-only.
- [ ] Validate against a reproduction of this session's own incident
      (two same-version resident daemons) before calling this phase done —
      the diagnostic must both detect that exact shape and fix it via the
      apply path without disturbing the genuine live owner.

## Validation Plan

- [ ] Per phase: a clean-room / isolated-HOME rehearsal of the plugin's
      `update` command against a live prior-version daemon, proving (a) the
      new daemon serves before the old one exits, (b) no in-flight
      operation is dropped, (c) the old process count converges to exactly
      one live daemon within a bounded time, (d) a rapid-fire second update
      arriving mid-cutover does not leave two live daemons stacked.
- [ ] Live-host proof (operator-gated, matching Invariant #7): reproduce
      today's exact incident shape (two resident `status-monitor`s on one
      host) is no longer possible after Phase 1 lands — an `update` run
      against a live prior daemon always converges to one.
- [ ] Regression: existing `status-monitor-restart`/`mux-daemon ensure`
      commands keep working for their own narrower cases (version-
      supersession reap) without behavior change for callers that don't hit
      the new automatic path.
- [ ] Phase 5: a new effort/design doc introducing a resident daemon in any
      `agent-*` plugin can be shown to reconcile against the invariant (a
      dry-run test case: draft a hypothetical new-daemon design and confirm
      the CONTRIBUTING.md checklist item actually surfaces the requirement).
- [ ] Phase 6: the diagnostic correctly identifies each of the four named
      abnormality classes in a synthetic reproduction (not just the one this
      session hit), reports report-only findings without side effects, and
      the apply path never terminates a confirmed-live, uniquely-legitimate
      owner in any of those synthetic cases.

## Proposal

_Pending._

## Journal

### 2026-09-29 — Phase 3 audited in `agent-ssh`; no daemon cutover adopted
Phase 3 closed with the audit-first conclusion the effort's review had already
pointed toward: `agent-ssh` still does **not** own a persistent daemon/harness/
broker service, so full `zdd` graceful-cutover adoption is not warranted here.
The architectural claim in `plugins/agent-ssh/README.md` and the shared-lib
claim in `libs/ssh-manager/README.md` both remain accurate after code audit:
the Windows proxy broker stays in-process with its SSH root, and the rest of
the plugin surface is one-shot CLI work rather than a resident service.

The audit did find one smaller leak-prone seam worth fixing under the lighter
`ephemeral-process-reaping` pattern: the detached `agent-ssh copilot`
`forward_keeper` persisted only **its own** pid in keeper state even though the
reverse-forward child it supervises is launched as an isolated SSH process
tree. If the keeper itself died abruptly, a subsequent relaunch could replace
the stale keeper record without ever knowing the old `ssh -N -R` child still
existed, letting reverse-forward helpers stack silently over time. The landed
fix now records the supervised child pid(s) alongside the keeper pid, refreshes
that state after the forward actually starts, and reaps those children when a
stale keeper record is replaced or explicitly stopped. Added regression
coverage in `libs/ssh-manager/tests/test_forward_keeper.py` for the new
child-pid reap path and in `plugins/agent-ssh/tests/test_copilot_detach.py`
for the post-start/restart state refresh path.

No `docs/patterns/graceful-daemon-cutover.md` adoption-table row was added for
`agent-ssh`; that table remains a list of real cutover adopters only. Phase 4
therefore stays scoped to `agent-worktrees` and `worktree-manager`, while Phase
3 is now complete as **audit + right-sized ephemeral-process hygiene**, not a
forced daemon-cutover implementation.

### 2026-09-29 — Phase 2 implemented in `worktree-manager`
Implemented the `mux-daemon` cutover slice. `zdd` is now vendored under
`worktree-manager/libs/zdd/`, the daemon publishes a routed active endpoint for
`mux-status-v1` clients, and `worktree-manager update`'s self-update path now
invokes a `CutoverOrchestrator` whenever a live resident `mux-daemon` is
present. The Worktree Manager is **not** a marketplace plugin and ships no
`plugin.json`, so Phase 2's "confirm the flag coupling first" audit closed with
an explicit **no-flag** conclusion: there is no `zeroDowntimeUpdate` /
`runtime_installer_argv` analogue to wire here, and the correct activation seam
is `self_install.self_update` itself, not a manifest bit.

The durable hand-off decision held: `mux_mapping_registry` remains the sole
mapping manifest, so no new successor-state breadcrumb was added beyond the
shared `zdd` cutover breadcrumb. The drain boundary also proved narrower than
Phase 1's monitor but broader than "between registry writes": the daemon now
closes `mux-status-v1` admissions and stops periodic live-mapping republishes,
then waits for every already-accepted status-apply handler **and** the current
republish cycle to drain before a superseded generation exits. Registry
register/remove calls remain durable, external CLI writes against the disk-backed
registry rather than in-memory daemon state, so they are not part of the
resident drain gate.

Validation added a real installer-seam rehearsal
(`worktree-manager/tests/test_mux_daemon_cutover_helper.py`) proving a live
daemon flips traffic to the successor before the predecessor exits, preserves
the on-disk mapping state across the hand-off, converges back to one live
generation, and survives a rapid second update arriving while the first cutover
is still draining. The Worktree Manager suite's targeted mux/update coverage,
the full `worktree-manager/tests` suite, the focused `agent-worktrees`
`mux_status_link`/reconcile coverage, and `tools/check-install-contract.py` all
passed in the implementation worktree.

### 2026-09-28 — Phase 1 merged (`agent-worktrees` / PR #4447)
Phase 1 is now merged to `dev` via PR #4447 (`agent-worktrees: add graceful
status-monitor cutover`). The landed slice vendors `zdd` under
`plugins/agent-worktrees/libs/zdd/`, wires the resident `status-monitor`
into the installer-driven active/passive cutover contract, and keeps the
old generation alive until the successor is both routed and promoted.

The final security-sensitive design detail is the new **owner-scoped
loopback control token** for the monitor control plane: the drain/promote/
shutdown surface now requires an unguessable bearer token loaded from a
machine-local `status-monitor-control.token` file under the monitor's own
runtime root, written best-effort `0600`, and used only on loopback by the
installer/cutover client. This closes the local-process drain/shutdown hole
the review found without inventing a second auth model distinct from the
suite's other token-protected local control surfaces.

The final drain-boundary shape is: **close admission first, keep the sweep
marked active through reconciliation/pane-reap mutations, wait for every
already-accepted hook/classify/tracking-write handler plus in-flight write
compute to drain, then retire**. Passive generations self-promote once they
observe themselves become the routed active pid, so an installer dying after
the route flip cannot strand a successor with no published surfaces.

Review history: the early rounds found real correctness gaps (drain races,
promotion timing, abandoned-passive recovery, PYTHONPATH isolation, Windows
spawn mode, and loopback auth), all of which were fixed before merge. Later
rounds began re-surfacing findings against older heads or already-landed
content (notably the documentation-impact statement and the shared-lib
preinstall ordering), so the final merge decision followed this repo's own
commented-verdict policy: once the current head had green checks, a plain
`COMMENTED` Copilot verdict remained advisory rather than merge-blocking.

### 2026-09-28 — Phase 1 implemented in `agent-worktrees`
Implemented the `status-monitor` cutover slice. `zdd` is now vendored under
`plugins/agent-worktrees/libs/zdd/`, the plugin declares
`"zeroDowntimeUpdate": true`, and both installer lanes now accept the
historical reconcile flags (`-ZeroDowntime` / `--zero-downtime`) while
driving the actual installer behavior automatically through a new
`status_monitor_cutover` helper instead of a hard restart. The resident
monitor now publishes a routed control endpoint, exposes the cutover
consumer contract (`spawn_passive`, `health_check`, `make_client`,
`pick_free_port`), and clears its routed claim on exit.

The drain boundary decision landed exactly as Phase 0 required: drain closes
admission to hook/classify/tracking-write requests first, then waits for the
current sweep plus every already-accepted handler/write to clear before the
old generation can retire. Generation self-retire now re-checks routed
supersession at the top of every sweep iteration and only exits at that same
safe point. For the hand-off manifest, no new successor breadcrumb was
needed: the durable tracking dir, the managed-mux cache/registry, and the
existing `zdd` routing + cutover breadcrumb are sufficient. The old
generation either drains already-accepted non-resumable work before exit or,
for work reconstructed on demand, leaves the successor to rebuild from those
existing durable stores rather than inventing a parallel manifest.

Validation added a new runnable `pytest` rehearsal around the installer seam
itself (`test_status_monitor_cutover_helper.py`) that proves the cutover
helper flips to a serving successor before the prior generation exits,
preserves an in-flight drain, converges back to one live generation, and
survives a second update arriving during the first cutover. The full
`agent-worktrees` suite and `tools/check-install-contract.py` both passed in
the implementation worktree. Review rounds: pending on the Phase 1 PR opened
from this slice; append the review/merge outcome here before closing Phase 1.

### 2026-09-28 — Scope expanded to a binding invariant + diagnostic tooling; handing off
Operator direction (Request § Round 2): elevate this pattern from a
three-plugin rollout to a **binding design invariant** for every `agent-*`
daemon, audited at design/implementation/review time, plus **generic
diagnostic + self-repair tooling** so an abnormality (duplicate daemon,
stranded passive, stale generation) can be detected and safely fixed up
anywhere it's found, not hand-remediated per incident. Added Phase 5
(invariant: `docs/patterns/graceful-daemon-cutover.md`'s own Invariants
section, the `docs/patterns/README.md` plugin-shapes classification point,
`CONTRIBUTING.md`'s PR-description requirements, and an automated-guard
feasibility check) and Phase 6 (a `zdd.diagnostics`-shaped generic audit +
report-only/apply self-repair, reusing this session's already-hardened
validated-owner + identity-bound-termination recipe rather than
re-deriving it). **Handing off here**: Phases 1-6 are all still unstarted
execution, substantial in their own right, and the operator explicitly
asked for a handoff rather than continued in-turn execution.

### 2026-09-28 — Review round 1: three real corrections
Automated PR review (the effort's own mandatory review gate) caught three
real issues in the initial plan, all fixed before merge:
1. **Publication safety** — the plan named a private downstream
   organization and a path only that private repo has; genericized.
2. **Ordering hazard in Phase 1/2** — `agent_worktrees.reconcile.
   runtime_installer_argv` (`plugins/agent-worktrees/src/agent_worktrees/
   reconcile.py:1298-1324`) **already** reads a plugin's
   `plugin.json["zeroDowntimeUpdate"]` and appends `-ZeroDowntime` to a
   reconcile-driven `install.ps1 update` for that plugin — but
   agent-worktrees' own `scripts/install.ps1` does not yet accept that
   switch. Setting the flag before the installer supports it would have
   broken reconcile-driven self-updates with a parameter-binding failure.
   Plan now requires the flag and the installer support to land in the same
   PR, never as separate steps. This also resolves Phase 0's original "where
   does the installer-driving mechanism live" open question: it's this
   repo's own `reconcile.py`, not an external `dotfiles` repo.
3. **Wrong premise for `agent-ssh`** — the plan assumed a persistent
   tunnel/session daemon needing cutover semantics. `plugins/agent-ssh/
   README.md` and `libs/ssh-manager/README.md` both explicitly document the
   opposite: no daemon/harness required, and the Windows proxy broker
   "lives in the calling process and closes with its SSH root... No
   persistent broker service." Phase 3 rewritten to **audit first**
   (starting with `forward_keeper.py`) and only add cutover machinery if the
   audit actually finds a genuine long-lived process — otherwise the
   correct fix is the lighter `ephemeral-process-reaping` pattern, or no fix
   at all.

### 2026-09-28 — Kickoff
- Effort created directly from the operator's own request, cross-referenced
  against the pre-existing `docs/patterns/graceful-daemon-cutover.md` +
  `libs/zdd/` (found already fully designed and proven in production for
  `agent-bridge`/`agent-dispatch`/`agent-index`/`agent-mcp`, per that doc's
  own Per-plugin adoption table). Confirmed by direct inspection that
  `agent-worktrees`, `worktree-manager`, and `agent-ssh` are the actual gap
  — absent from that table, no `"zeroDowntimeUpdate"` flag, and (for
  `agent-ssh`) `zdd` vendored but entirely unwired. Scoped this effort to
  exactly that gap rather than re-deriving a new protocol, per the pattern
  doc's own explicit instruction ("do not reinvent it per plugin").
