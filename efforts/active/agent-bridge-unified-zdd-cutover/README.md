---
visions:
  - visions/plugins/agent-bridge
---

# agent-bridge — one canonical ZDD cutover, with a generation-scoped session-host handoff

- **Slug:** `agent-bridge-unified-zdd-cutover`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `dev`
- **Created:** 2026-09-28
- **Status:** Draft
- **Vision:** closes
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  with §Concepts/*the daemon generation and its session-host handoff*,
  §Features/*one-canonical-deploy-path*, and §Behaviors/*the next generation
  earns the handoff, never assumes it* and *the outgoing generation waits for
  confirmation, not for Copilot*
- **Umbrella issue:** _pending — claim/file before Phase 1 begins_
- **Sub-issues:** [#1362](https://github.com/ThomasMichon/copilot-extensions/issues/1362)
  (daemon flapping: stale `active.json` port mapping + failed auto-update
  cutovers + wedge on remote-session-host recovery) ·
  [#2041](https://github.com/ThomasMichon/copilot-extensions/issues/2041)
  (reattach surviving provider Session Hosts instead of recreating sessions)
- **Related:** [`efforts/active/agent-bridge-truthful-terminal-state`](../agent-bridge-truthful-terminal-state/README.md)
  (sibling effort — session terminal-state truthfulness; this effort is the
  daemon-generation cutover mechanism those sessions ride through) ·
  [`libs/zdd`](../../../libs/zdd/README.md) (the shared cutover/routing
  library this effort extends, used by 8 plugins)

## Guiding Intent

An install, a background reconcile, and an operator-invoked update must be
**one behavior**, not three. Today they are not: a background reconcile hook
is the only trigger for routine updates and is silently skippable, and the
CLI offers a real zero-downtime cutover (`deploy`) alongside a raw
stop-then-start (`service restart`) as if they were interchangeable — they
are not, and picking the wrong one turns a routine update into an outage.
Layered on top of that, the existing cutover orchestrator only drains the
daemon's own HTTP endpoint; it has no protocol for handing off the
individual `session-host` subprocesses a daemon generation actually owns,
so a session can survive a daemon restart today only by accident of timing,
not by design. This effort makes update behavior single, always-ZDD, and
extends the existing daemon-generation model with the piece it's missing:
a durable, per-session-host ownership claim that transfers deliberately from
one generation to the next.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Effort owner | Drives all phases, owns PRs | local worktree |

## Coordination

- **Topology:** shared, serial phase branches (no parallel slices needed at
  this size)
- **Host (owns PRs):** effort owner
- **Delegates:** none yet
- **Handoff:** n/a until a participant is added

## Context

### How this was found (public-safe account)

A downstream consumer's own production investigation found a daemon (this
plugin's) that had not picked up a needed fix in **18+ days** despite several
intervening `copilot plugin update` runs. Root cause: the only mechanism that
ever triggers an update reconcile is a `sessionStart` hook
(`hooks.json` → `scripts/bootstrap-check.sh`/`.ps1`), and that hook has been
made **opt-in per project** (`background_reconcile_<plugin>: true` in
`.copilot-extensions/config.yaml`) — a deliberate change from "every session
self-heals drift" to "nothing happens unless a project explicitly asks." A
project that never set the flag gets **silent, permanent, undetectable**
staleness: no error, no warning, just a daemon that never updates. The same
investigation, while manually forcing an update, found that `agent-bridge
deploy` and `agent-bridge service restart` are genuinely different code
paths — `deploy` builds a `zdd.cutover.CutoverOrchestrator` (spawn passive →
health-gate → flip routing → drain → retire); `service restart` is
`_service_stop()` followed by `_service_start()`, with no cutover,  no
draining, and no session-host awareness at all. An operator (or a script)
reaching for the "obvious" restart verb gets the unsafe path by default.

### What already exists (build on this, don't re-invent it)

- **`libs/zdd`** — the shared active/passive cutover library (`zdd.routing`'s
  file-based, client-read `active.json` table with self-healing readers and
  a `reap_stale_active` watchdog; `zdd.cutover.CutoverOrchestrator` driving
  spawn → health-gate → flip → drain → retire, with rollback and
  commit-forward). Used by 8 plugins today (agent-ssh, agent-vault,
  agent-dispatch, agent-containers, agent-index, agent-mcp, agent-bridge,
  agent-codespaces). **This effort extends `zdd`, it does not fork it** —
  every consumer benefits from a real session-host handoff primitive, not
  only agent-bridge.
- **`agent_bridge.session_host.host_index.HostIndex`** — already a durable,
  atomically-rewritten JSON map of session id → `HostRecord` (host port,
  PID, child PID, host/runtime/protocol versions, state-file path, auth
  nonce, resume-on-reattach state). This is the right place to *add* a
  generation-ownership claim — it is not a new manifest to invent.
- **`agent-bridge deploy`** (`venue_cli.py`) already does the real
  orchestrated cutover, including stale-cutover recovery and abandoned-
  passive reaping. The gap is narrower than "build ZDD from scratch": it is
  (a) make this the *only* reachable update path, (b) make the reconcile
  trigger reliable/observable, and (c) teach the orchestrator (or its
  `zdd`-level primitive) to hand off session-hosts by generation-scoped
  claim, not merely drain the daemon's own endpoint.
- **`session_core.py`'s redeploy policy** already defaults to
  detach-not-destroy for in-flight sessions during a redeploy — the
  session-level half of "graceful" already exists; this effort's job is the
  daemon-generation/session-host half.

## Request

_(operator, verbatim — genericized to the public design, no internal service
names)_

> Why do `agent-bridge service restart` and `agent-bridge deploy` both still
> exist? There should be only *one* canonical update behavior.
>
> We can improve upon the ZDD flow thanks to our port-discovery system and
> session-host model.
>
> Suppose we upgrade agent-bridge v1 to v2. When we create the v2 install
> slot, and run v2:install, we expect to create the v2 venv, and then update
> the binstub+redirect to be current. The running instance of v1 should see,
> at next opportunity, that it's no longer the current version. It should
> then immediately notify its connected `session-host` instances that a
> graceful disconnect is about to occur. Ideally, session-host instances
> will already be recorded in a manifest, with their locations, target info,
> port reservations, etc., so that they can be picked back up by anyone.
> However, there will be a lock from `v1` present on them. As `v1`
> disconnects, it will release its lock. We can probably add some backup
> handling that if `v1` terminates abruptly, future versions have a way to
> discover the stale lock and recover it, if we don't already. To ensure
> "ZDD", we can add a liveness check, where v2, after starting, and confirm
> via another "lock" file that it's up, and only then can v1 gracefully shut
> down. As it releases its session-host locks, v2 can pick them up,
> reconnect, and continue, without the remote session's Copilot even knowing
> anything went wrong. Upstream callers of agent-bridge will be routed using
> the ZDD mask layer, which simply proxies to the current process via
> discovery and handles minor disruptions via buffering, the same way
> session-host handles changeovers of agent-bridge.
>
> With this flow, `v1` may *always* terminate, as all it MUST do is wait for
> the v2 instance to actually spawn and register its port, send graceful
> disconnect notices to its session-hosts, ensure it has the manifest
> durably recorded, notify its ZDD coordinator, and then shut down. It never
> needs to wait on Copilot; at most it needs to finish sending a single
> in-transit ACP event to the coordinator.
>
> Let's figure out what went wrong, and improve the flow to be more durable,
> and avoid any "special variants" in the install/update flow. There must be
> only one standard behavior, which does ZDD and this drain/cutover scheme,
> every time.

## Plan

_(Phased breakdown below is agent-recommended structuring of the operator's
design above; the design itself — generation-scoped claims, the liveness
gate, the outgoing generation's exit contract, and the caller-facing mask
layer — is the operator's own, captured verbatim in Request.)_

### Phase 0 — Confirm the diagnosis, close the easy gaps first
- [x] Confirmed via code + a live install: the `background_reconcile_<plugin>`
  opt-in flag, when unset, causes `bootstrap-check.sh`/`.ps1` to silently
  skip every session, indefinitely — no log surfaced to an operator unless
  they already knew to check `~/.agent-bridge/reconcile.log`.
- [x] Confirmed via source review: `deploy` and `service restart` are
  genuinely different code paths (`venue_cli.py` vs. `service_process_cli.py`
  + `service_start_cli.py`), not a documentation-only distinction.
- [ ] Decide, with the operator, whether the opt-in reconcile gate itself
  survives this redesign — the design below makes an update **safe** to run
  automatically (always ZDD), which may remove the original justification
  for making it *opt-in* (avoiding unwanted background work) as long as the
  *frequency*/*trigger* is still deliberately bounded. This is a genuine
  design fork the vision doesn't resolve on its own — flag findings, don't
  silently pick one side.
- [ ] Make the current staleness **observable** regardless of the outcome
  above: `service status` (or an equivalent) should be able to say plainly
  "N days since last successful reconcile, background reconcile is
  {enabled,disabled} for this project" rather than requiring an operator to
  reconstruct that from a raw log file.

### Phase 1 — Retire the second deploy behavior
- [ ] `agent-bridge service restart` (and any other reachable stop+start
  affordance) routes through the same cutover the `deploy` verb already
  performs — either by making `restart` literally call the same code path,
  or by removing `restart` as a distinct verb entirely in favor of one name.
  No behavior change is acceptable that still allows a raw stop-then-start
  of a daemon carrying live session-hosts.
- [ ] Audit every other caller of the raw stop/start path (installers,
  bootstrap-check scripts, any other plugin's activation hook) and route
  them onto the same one path too.

### Phase 2 — Generation-scoped session-host claims in `zdd`/`HostIndex`
- [ ] Extend `HostRecord` (or a sibling durable structure) with an explicit
  **owning generation** field (not just `host_version` as evidence) —
  something a new generation can query and a stale one can be proven not to
  hold anymore.
- [ ] Add the **claim/release/recover** primitive: a new generation
  acquires a specific host's claim; the prior generation releases claims it
  actually held, one host at a time, as it hands off; a claim whose owning
  generation is provably dead (not just "the daemon restarted") is
  recoverable by whichever generation next queries it — no live handshake
  with the dead process required.
- [ ] This belongs in `libs/zdd` (or a sibling shared primitive vendored the
  same way), not bolted onto agent-bridge alone — the same shape helps any
  of `zdd`'s other 7 consumers that host long-lived children across an
  update.

### Phase 3 — The liveness gate and the outgoing generation's exit contract
- [ ] A new generation, after starting, durably marks itself live (the
  operator's "another lock file" idea, or an equivalent durable marker) —
  this is the signal the prior generation waits for before doing anything
  destructive.
- [ ] Codify the outgoing generation's **exact** obligations before it may
  terminate: confirm the next generation is live; hand off (or durably
  mark stale-recoverable) every session-host claim it held; ensure the
  handoff/claim state itself is durably recorded; notify its own ZDD
  coordinator; finish any single in-transit event already crossing the
  wire. Nothing else blocks its exit — in particular, it never waits on a
  Copilot turn or a client.

### Phase 4 — The caller-facing mask/routing layer
- [ ] Confirm (or extend) that upstream callers of agent-bridge resolve
  through the existing `zdd.routing` `active.json` discovery the same way
  session-host clients already tolerate a session-host changeover — a
  cutover in flight should look like a brief, buffered pause to every
  caller, never a hard error.
- [ ] Validate the buffering/retry behavior at the actual call sites that
  matter in practice (CLI `send`/`read`/`wait`, not only the HTTP layer).

### Phase 5 — Validation
- [ ] A real, driven cutover drill: start a session-host-carrying daemon,
  trigger the one canonical update path, and confirm the session's Copilot
  process never observes a disruption (no dropped turn, no lost event) while
  the daemon itself fully changes generation.
- [ ] A forced-abrupt-termination drill: kill the old generation before it
  releases its claims, and confirm a later generation recovers them cleanly.
- [ ] Extend or add a clean-room scenario (Tier P, `agent-bridge-solo` or a
  new `agent-bridge-cutover` companion) that exercises this on a real fresh
  machine.

## Validation Plan

- [ ] `agent-bridge service restart` (or its replacement) and `agent-bridge
  deploy` are provably the same code path (a shared test, or the removal of
  one verb).
- [ ] A live cutover drill (Phase 5) shows zero session disruption across a
  real generation change.
- [ ] An abrupt-termination drill shows a stale claim is recovered by the
  next generation without manual intervention.
- [ ] Reconcile staleness is observable via a status command, independent of
  whether Phase 0's opt-in-gate question is resolved to keep or remove it.
- [ ] Full plugin test suite (`python tools/run-plugin-tests.py agent-bridge`)
  and `libs/zdd`'s own suite stay green throughout.

## Proposal

_Pending — Phase 2's claim/release/recover schema and Phase 3's exit-contract
sequencing will be drafted here once the design is reviewed._

## Journal

### 2026-09-28 — Kickoff
- Carved after a live production incident (root-caused in a downstream
  consumer's own effort) surfaced both the silent opt-in-reconcile gap and
  the `deploy`/`service restart` behavior split. Vision revised first
  (`visions/plugins/agent-bridge`) to state the should-be intent; this
  effort carries the concrete redesign.
- Confirmed via source review (not assumption): `bootstrap-check.sh`/`.ps1`
  gate every reconcile on a per-project opt-in flag
  (`hooks.json` → `scripts/bootstrap-check.{sh,ps1}`); `venue_cli.py`'s
  `_cmd_deploy` builds a real `zdd.cutover.CutoverOrchestrator`;
  `service_process_cli.py`'s `restart` is a raw `_service_stop()` +
  `_service_start()` with no orchestrator involved; `HostIndex` durably
  records session-host location/port/pid but has no generation-ownership
  field today.

## See Also

- Vision: [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
- Extends: [`libs/zdd`](../../../libs/zdd/README.md)
- Sibling effort: [`agent-bridge-truthful-terminal-state`](../agent-bridge-truthful-terminal-state/README.md)
