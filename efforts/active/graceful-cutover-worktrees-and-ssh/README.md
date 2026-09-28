# Graceful daemon cutover — close the agent-worktrees/worktree-manager/agent-ssh gap

- **Slug:** `graceful-cutover-worktrees-and-ssh`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase feature branches / independent per-slice PRs
- **Created:** 2026-09-28
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends [`visions/plugin-services`](../../../visions/plugin-services/README.md)
  §`zero-downtime-cutover` — applies its already-generalized zero-downtime
  service model to the three plugins not yet covered by
  `docs/patterns/graceful-daemon-cutover.md`'s own rollout.
- **Umbrella issue:** _pending_
- **Sub-issues:** _pending_

## Guiding Intent

Close the last gap in an already-proven pattern: `docs/patterns/graceful-daemon-cutover.md`
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
      concrete **safe cutover point** (the drain boundary):
      - `agent-worktrees status-monitor`: between sweep ticks (no in-flight
        non-resumable turn; each sweep is already a bounded, restartable unit).
      - `worktree-manager mux-daemon`: between mapping-registry mutations /
        republish cycles (mirrors the above).
      - `agent-ssh`: pending Phase 0's audit outcome above.

### Phase 1 — `agent-worktrees` `status-monitor`
- [ ] Vendor `zdd` into `plugins/agent-worktrees/libs/zdd/` (byte-identical
      sync from `libs/zdd/`, per the pattern's own sync convention).
- [ ] Implement the consumer contract: `spawn_passive`, `health_check`,
      `make_client` (drain/undrain/shutdown), `pick_free_port`.
- [ ] Wire `agent-worktrees update`'s activation path to invoke
      `CutoverOrchestrator` automatically whenever it detects a live
      `status-monitor` (no operator flag, no new CLI verb — Invariant #1).
- [ ] Add the **generation self-retire** loop (`self_retire.is_superseded`)
      inside the resident sweep loop itself, gated per the pattern's own
      "before any repeated loop, subscription connection, request" framing
      — checked at the top of every sweep iteration, not only at daemon
      startup, so a same-tick rapid-fire re-release is still caught.
- [ ] **Land `plugin.json`'s `"zeroDowntimeUpdate": true` atomically with
      `scripts/install.ps1`/`install.sh` actually accepting and implementing
      `-ZeroDowntime`, in the same PR — never as two separate steps.**
      (Copilot review, round 1): `agent_worktrees.reconcile.runtime_installer_argv`
      **already** reads this flag and appends `-ZeroDowntime` to a reconcile-
      driven `install.ps1 update` for any plugin that sets it, but
      agent-worktrees' own `scripts/install.ps1` does not yet declare that
      parameter at all — setting the flag first, before the installer
      supports it, would break reconcile-driven self-updates with a
      parameter-binding failure the moment anything reconciles
      agent-worktrees itself the same way it reconciles siblings.
- [ ] Define the **hand-off manifest**: what "outstanding work" a
      `status-monitor` generation must persist for its successor (per-session
      claim/observation state it would otherwise reconstruct from scratch —
      confirm whether this is already fully derivable from existing durable
      state, e.g. the tracking dir + managed-mux registry, or needs a new
      breadcrumb).

### Phase 2 — `worktree-manager` `mux-daemon`
- [ ] Same shape as Phase 1, scoped to the companion `mux-daemon` process
      specifically (not the Picker CLI's own one-shot invocations).
      Hand-off manifest: the `mux_mapping_registry`'s own on-disk state is
      already the durable source of truth (confirmed this session) — likely
      needs no *new* breadcrumb, only a successor that reads it on boot
      (already true) and a predecessor that stops writing before exiting.
- [ ] Apply Phase 1's same atomic-landing lesson here first: confirm whether
      `worktree-manager` has (or `agent-worktrees` reconcile has) an
      analogous flag/installer-argv coupling before setting any manifest
      flag ahead of real installer support.

### Phase 3 — `agent-ssh` (ssh-manager): audit first, cutover only if warranted
- [ ] Run the Phase 0 audit (`forward_keeper.py` + any other spawned
      children) to conclusion. **Do not assume a persistent daemon exists**
      — confirmed this round of review that it does not, by design, for the
      Windows proxy broker at least.
- [ ] If the audit finds a genuine long-lived/leak-prone process: scope a
      right-sized fix (full `zdd` cutover only if it is truly a persistent,
      stateful daemon; otherwise the lighter `ephemeral-process-reaping`
      pattern is more likely correct).
- [ ] If the audit finds nothing: close this phase as "no cutover needed,"
      not "done" — record the audit finding in the Journal so a later
      re-check isn't repeated from scratch, and drop `agent-ssh` from
      Phase 4's pattern-doc update (only real adopters belong in that
      table).

### Phase 4 — Close the loop in the pattern doc itself
- [ ] Add `agent-worktrees`, `worktree-manager`, `agent-ssh` rows to
      `docs/patterns/graceful-daemon-cutover.md`'s **Per-plugin adoption**
      table and **Rollout sequencing** list once each phase lands, so the
      pattern doc stays the single source of truth for adoption state
      (never let this effort's own README become a second, drifting copy of
      that table).

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

## Proposal

_Pending._

## Journal

### 2026-09-28 — Review round 1: three real corrections
Automated PR review (the effort's own mandatory review gate) caught three
real issues in the initial plan, all fixed before merge:
1. **Publication safety** — the plan named the private `aperture-labs` org
   and a path only that private repo has; genericized.
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
