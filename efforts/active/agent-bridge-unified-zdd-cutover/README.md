---
visions:
  - visions/plugins/agent-bridge
---

# agent-bridge — one canonical ZDD cutover, with a generation-scoped session-host handoff

- **Slug:** `agent-bridge-unified-zdd-cutover`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `dev`
- **Created:** 2026-09-28
- **Status:** In Progress (Phase 1 of 6 merged — [#4478](https://github.com/ThomasMichon/copilot-extensions/pull/4478); Phase 2 of 6 merged — [#4522](https://github.com/ThomasMichon/copilot-extensions/pull/4522); Phase 3 of 6 merged — [#4543](https://github.com/ThomasMichon/copilot-extensions/pull/4543); Phase 4 of 6 merged — [#4581](https://github.com/ThomasMichon/copilot-extensions/pull/4581); Phase 5 of 6 partially landed — [#4586](https://github.com/ThomasMichon/copilot-extensions/pull/4586), live-turn drill deferred to Phase 6; Phase 6 of 6 implemented, PR open — closes Phase 5's deferred live-turn Plan item as an opt-in `CR_LIVE_TURN_DRILL=1` extension of the Tier-P `agent-bridge-cutover` scenario, not a Tier-E harness)
- **Vision:** closes
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  with §Concepts/*the daemon generation and its session-host handoff*,
  §Features/*one-canonical-deploy-path*, and §Behaviors/*the next generation
  earns the handoff, never assumes it* and *the outgoing generation waits for
  confirmation, not for Copilot*
- **Umbrella issue:** [#4477](https://github.com/ThomasMichon/copilot-extensions/issues/4477)
- **Sub-issues:** [#1362](https://github.com/ThomasMichon/copilot-extensions/issues/1362)
  (daemon flapping: stale `active.json` port mapping + failed auto-update
  cutovers + wedge on remote-session-host recovery) ·
  [#2041](https://github.com/ThomasMichon/copilot-extensions/issues/2041)
  (reattach surviving provider Session Hosts instead of recreating sessions)
- **Related:** [`efforts/active/agent-bridge-truthful-terminal-state`](../agent-bridge-truthful-terminal-state/README.md)
  (sibling effort — session terminal-state truthfulness; this effort is the
  daemon-generation cutover mechanism those sessions ride through) ·
  [`libs/zdd`](../../../libs/zdd/README.md) (the shared cutover/routing
  library this effort extends, used by 9 plugins)

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
  commit-forward). Used by 9 plugins today (agent-ssh, agent-vault,
  agent-dispatch, agent-containers, agent-index, agent-mcp, agent-bridge,
  agent-codespaces, agent-worktrees — the last was undercounted in the
  original design notes, confirmed by directory listing during Phase 2).
  **This effort extends `zdd`, it does not fork it** —
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
- [x] `agent-bridge service restart` (and any other reachable stop+start
  affordance) routes through the same cutover the `deploy` verb already
  performs — either by making `restart` literally call the same code path,
  or by removing `restart` as a distinct verb entirely in favor of one name.
  No behavior change is acceptable that still allows a raw stop-then-start
  of a daemon carrying live session-hosts.
- [x] Audit every other caller of the raw stop/start path (installers,
  bootstrap-check scripts, any other plugin's activation hook) and route
  them onto the same one path too.

### Phase 2 — Generation-scoped session-host claims in `zdd`/`HostIndex` ✅
- [x] Extend `HostRecord` (or a sibling durable structure) with an explicit
  **owning generation** field (not just `host_version` as evidence) —
  something a new generation can query and a stale one can be proven not to
  hold anymore.
- [x] Add the **claim/release/recover** primitive: a new generation
  acquires a specific host's claim; the prior generation releases claims it
  actually held, one host at a time, as it hands off; a claim whose owning
  generation is provably dead (not just "the daemon restarted") is
  recoverable by whichever generation next queries it — no live handshake
  with the dead process required.
- [x] This belongs in `libs/zdd` (or a sibling shared primitive vendored the
  same way), not bolted onto agent-bridge alone — the same shape helps any
  of `zdd`'s other 8 consumers that host long-lived children across an
  update.
- [x] **Cutover-wide serialization** (surfaced by PR #4478's review, not a
  Phase 1 regression: `agent-bridge deploy` was already directly invocable
  and racy before this effort — Phase 1 only widens exposure by making
  `service restart`, an operator-facing routine command, funnel into the
  same path): the shared deploy/cutover path
  (`venue_cli._cmd_deploy`/`CutoverOrchestrator.run`) has no process-wide
  lock. Installer-driven deploys serialize via the installers' own
  `.install.lock`; a direct `agent-bridge deploy`/`service restart`
  invocation does not, so two concurrent invocations (two operators, or a
  restart racing an installer deploy) can both read the same predecessor
  breadcrumb/routing state and race — one can demote the other's new
  generation while both report success, or roll back against state the
  other invocation owns. Needs a dedicated cross-platform cutover lock
  (likely a `libs/zdd` primitive, same reasoning as the claim/release/
  recover primitive above) plus a process-level contention regression
  test.

### Phase 3 — The liveness gate and the outgoing generation's exit contract ✅
- [x] A new generation, after starting, durably marks itself live (the
  operator's "another lock file" idea, or an equivalent durable marker) —
  this is the signal the prior generation waits for before doing anything
  destructive.
- [x] Codify the outgoing generation's **exact** obligations before it may
  terminate: confirm the next generation is live; hand off (or durably
  mark stale-recoverable) every session-host claim it held; ensure the
  handoff/claim state itself is durably recorded; notify its own ZDD
  coordinator; finish any single in-transit event already crossing the
  wire. Nothing else blocks its exit — in particular, it never waits on a
  Copilot turn or a client.

### Phase 4 — The caller-facing mask/routing layer ✅
- [x] Confirm (or extend) that upstream callers of agent-bridge resolve
  through the existing `zdd.routing` `active.json` discovery the same way
  session-host clients already tolerate a session-host changeover — a
  cutover in flight should look like a brief, buffered pause to every
  caller, never a hard error.
- [x] Validate the buffering/retry behavior at the actual call sites that
  matter in practice (CLI `send`/`read`/`wait`, not only the HTTP layer).

### Phase 5 — Validation
- [ ] A real, driven cutover drill: start a session-host-carrying daemon,
  trigger the one canonical update path, and confirm the session's Copilot
  process never observes a disruption (no dropped turn, no lost event) while
  the daemon itself fully changes generation. **Partially covered** -- the
  pre-existing `agent-bridge-cutover` Tier-P `routing-flip-retire` check
  already proves the daemon-level mechanism a live-turn survival depends on
  (nothing hard-killed, clean beside-not-in-place handoff); a *fully live*
  assertion needs a real model/ACP child in the loop and remains Tier-E scope
  (tracked, not delivered here -- see Journal).
- [ ] A forced-abrupt-termination drill: kill the old generation before it
  releases its claims, and confirm a later generation recovers them cleanly.
  **Partially covered** -- proves the underlying dead-pid-recovery primitive
  against a genuinely killed real process; the claim is stamped with a
  test-chosen generation label, not the killed daemon's own real
  `_generation_id` (no API exposes it -- see Journal's honest scope note),
  so this does not yet demonstrate a claim *actually owned by that daemon
  generation* being interrupted and recovered.
- [x] Extend or add a clean-room scenario (Tier P, `agent-bridge-solo` or a
  new `agent-bridge-cutover` companion) that exercises this on a real fresh
  machine.

### Phase 6 — Tier-E live-turn-survival harness (implemented; PR open) ✅

Closes Phase 5's Plan item 1 for real: prove, with a genuinely live
Copilot/ACP turn in flight, that `agent-bridge deploy` does not disrupt it
while the daemon fully changes generation underneath it. Full design,
topology analysis (a plain `command`-registered Tier-E provider bypasses
the Session-Host path entirely -- a local target is required), concrete
sketch, feasibility notes, and acceptance criteria live in the sibling
design doc:
[`phase-6-tier-e-live-turn-harness.md`](phase-6-tier-e-live-turn-harness.md).

**Resolved design question:** this does NOT get a "Tier E" label or use
Tier-E's provider-registration machinery. It ships as an opt-in phase 4
(`CR_LIVE_TURN_DRILL=1`, default off) of the existing Tier-P
`agent-bridge-cutover` scenario, with the drill logic in a new sibling
fixture (`fixtures/live_turn_probe.py`) rather than another
`cutover_probe.py` check, because it needs a real Copilot auth context and
deliberately runs against the box's own real, already-provisioned install
instead of a throwaway sandbox (see the fixture's own module docstring for
the full reasoning).

- [x] A real live cutover drill shows a real Copilot turn completes with
  zero observed disruption while the daemon's generation actually changes
  underneath it (same session id, no dropped/duplicated event, confirmed
  generation change -- not a trivial/no-op cutover). Implemented in
  `fixtures/live_turn_probe.py`, wired as opt-in scenario phase 4. **Not yet
  executed for real** in this round (Docker + real Copilot auth + real
  credits are needed; see Journal) -- the assertions and bounded-poll
  mechanics were built and reviewed against the real `HostIndex`/session
  CLI contracts, matching the same rigor `abrupt-kill-recovery` (Phase 5)
  used, but a live run is the next session's/operator's job to trigger and
  observe.
- [x] The drill's verdict is programmatic/evidence-based (a dedicated
  comparator over `HostIndex` records, `sessions --json` status, `wait
  --attention turn_complete`, and an `events.jsonl` before/after diff) --
  no LLM judge involved, per the design doc's own conclusion.
- [x] The scenario is documented in the clean-room catalog
  (`tools/clean-room/README.md`) and in this scenario's own `manifest.json`/
  `scenario.sh` header comments; no new reusable Tier-E pattern was
  established (the opposite -- an existing Tier-P scenario grew one opt-in
  phase), so `ARCHITECTURE.md`/`TIER-E-EXECUTION.md` were left untouched.

## Validation Plan

- [ ] `agent-bridge service restart` (or its replacement) and `agent-bridge
  deploy` are provably the same code path (a shared test, or the removal of
  one verb).
- [x] A live cutover drill (Phase 5/6) shows zero session disruption across
  a real generation change. Implemented as Phase 6's opt-in
  `CR_LIVE_TURN_DRILL=1` drill; **not yet executed for real** this round
  (needs Docker + real Copilot auth + real credits -- see Phase 6's Journal
  entry).
- [ ] An abrupt-termination drill shows a stale claim is recovered by the
  next generation without manual intervention. **Partially covered** -- the
  claim is stamped with a test-chosen label, not the daemon's own real
  generation identity (see Journal).
- [ ] Reconcile staleness is observable via a status command, independent of
  whether Phase 0's opt-in-gate question is resolved to keep or remove it.
- [ ] Full plugin test suite (`python tools/run-plugin-tests.py agent-bridge`)
  and `libs/zdd`'s own suite stay green throughout.

## Proposal

**Phase 3's liveness gate — a design decision, not new code.** A dedicated
"another lock file" (the operator's own phrasing in Request) turned out to be
unnecessary: `zdd.routing`'s existing `active.json` table already *is* the
durable liveness marker the vision calls for, for two independent reasons
confirmed by reading `libs/zdd/src/zdd/routing.py` and `cutover.py`: (1)
`CutoverOrchestrator.run()` only calls `publish_active()` **after** its own
health gate passes -- so an entry in `active.json` already proves the new
generation started and answered a live health probe, not merely that its
process exists; (2) the same orchestrator re-confirms that health
immediately before retiring the old daemon (the "verify-before-retire" gate),
so the old generation never destroys itself on the strength of a
liveness signal that could have gone stale between the flip and the retire.
Introducing a second, parallel liveness file would only duplicate this
signal, not strengthen it. Phase 3 therefore reuses `active.json` as-is and
spends its own effort on the piece that genuinely didn't exist: the
session-host claim half of the exit contract (below).

**Phase 3's exit-contract sequencing.** Claim/release plugs into the
*existing* cutover sequence at two points, both agent-bridge-specific (never
touching `zdd`'s own generic surface, since not every `zdd` consumer has a
session-host concept):
1. **Release (outgoing generation, its own initiative)**: the `/api/v1/
   shutdown` handler -- called by `CutoverOrchestrator` only after flip +
   drain + the verify-before-retire gate all passed -- releases every claim
   this generation holds (`HostIndex.release_all(generation_id)`) before
   `should_exit=True` ever triggers lifespan teardown. This is the "it never
   waits on a Copilot turn or a client" half: releasing is synchronous,
   local, and never blocks on anything the new generation does.
2. **Claim (new generation, never assumes ownership)**: `reattach_session_hosts()`
   calls `HostIndex.claim(...)` for each live record before adopting it. A
   record still claimed by a *live* other generation is skipped (not
   stolen); one whose owning generation is dead is claimed silently (Phase
   2's claim/release/recover contract, no live handshake). A passive
   cutover instance never runs this scan at all -- ATTACHing to a Session
   Host unconditionally displaces whatever front already holds it (a
   real, review-caught hazard: a passive daemon reattaching would
   disconnect the truly active old generation before any cutover gate
   ever ran), so all claim/reattach work for it is deferred to the
   post-cutover retry below. The cutover CLI (`_cmd_deploy`) retries the
   scan via a new `/api/v1/session-hosts/reattach` endpoint once the old
   generation is *confirmed* exited -- mirroring the existing post-commit
   relay-adoption step's own shape, not a new mechanism.

**Phase 4 — validated, not built.** The caller-facing mask this phase's
checklist calls for already existed before this effort started, predating it
by years (`#23`/`#46.6`, `#893`, `#900`, `#3179` in the Journal below) --
Phase 4's real job turned out to be confirming that machinery is intact after
Phases 1-3's changes and closing the one genuine validation gap: proof at the
*CLI call site*, not only at the `BridgeClient` request level. See the Journal
entry below for what was inspected, what was already covered, and the one new
test that closes the gap.

## Journal

### 2026-09-29 — Phase 6 implemented (opt-in live-turn-survival drill, PR pending)
- Consumed the prior session's planning handoff (Phase 6 design doc,
  `phase-6-tier-e-live-turn-harness.md`, merged as PR #4626) and implemented
  the drill it specified.
- **Resolved the design doc's open question:** this is NOT a Tier-E
  scenario. Read `TIER-E-EXECUTION.md` in full -- its machinery
  (`bridge_register.py`, the literal-mode judge, `expected_outcome`
  rubrics) exists to audit whether a driven agent can *follow a plugin's
  docs*; this drill has no doc-compliance question, so none of that
  applies. Shipped instead as an opt-in phase 4 of the existing Tier-P
  `agent-bridge-cutover` scenario, gated behind `CR_LIVE_TURN_DRILL=1`
  (default off -- Docker-only, needs real Copilot auth, consumes real AI
  credits, never a routine CI gate).
- **New fixture, not another `cutover_probe.py` check.** Read
  `cutover_probe.py`'s own `Ctx` class closely: every existing check
  deliberately relocates `HOME`/`AGENT_BRIDGE_CONFIG_DIR` into a throwaway
  sandbox so it never touches a live daemon's real state. That is wrong
  for this drill: a throwaway `HOME` has no `~/.copilot` credentials, so
  every real model call would fail closed. Added
  `fixtures/live_turn_probe.py` instead, which deliberately runs against
  the box's REAL, already-provisioned install (the one `scenario.sh`
  phases 1/2 just built) -- safe only because the intended venue is a
  disposable clean-room container with nothing else concurrently relying
  on that state, which is also exactly why this drill must stay
  Docker-only and is never run against a real workstation's real
  `~/.agent-bridge`/`~/.copilot`.
- **Read the real source to ground every assertion, not just the design
  doc's sketch:** `agent_registry_resolver.py` (confirmed a `host`-less,
  `spawn_command`-less agent config resolves to `SpawnTarget(type="local",
  ...)`), `agent_registry_topology.py`'s `discover_local_agents` (confirmed
  a `~/.agent-worktrees/projects.yaml` entry with an `anchor` is exactly
  how to register a real local target for `agent-bridge create
  <name> --target-dir <repo>`), `session_host/host_index.py`'s
  `HostRecord` (confirmed the field set -- `owner_pid`/`owner_generation`
  only, no `acp_session_id`; that lives in the frontend's own
  `sessions --json`, not `HostIndex`), `zdd/claims.py`'s `generation_id`
  (confirmed it is genuinely not independently reproducible from outside
  the process -- version+pid+wall-clock -- so the drill compares real pids
  across the boundary instead of trying to recompute the generation
  string), and `session_streaming_cli.py`'s `wait --attention
  turn_complete` (the same caller-facing channel a real caller uses,
  giving a stronger continuity proof than internal bookkeeping alone).
- **Verdict mechanism (programmatic, per the design doc's own
  conclusion):** same session id + same `acp_session_id` throughout; the
  daemon generation genuinely changed (different real pid, old port
  retired); the `HostIndex` claim reattaches under the NEW generation's
  real pid (not merely "some record exists"); the `events.jsonl` snapshot
  taken the instant `deploy` fires is an exact prefix of the final
  snapshot (no truncation/mutation); exactly one `assistant.turn_end` and
  no duplicate `session.start`/`session.shutdown`; and the bridge's own
  `wait --attention turn_complete` settles cleanly across the boundary.
- **Honest scope note (same discipline Phase 5 used).** This round
  implemented and reviewed the drill's logic against the real APIs it
  calls, but did **not** execute it for real -- that needs a Docker
  clean-room box with real Copilot auth injected and spends real AI
  credits per run, which is out of scope to trigger interactively from an
  ordinary coding session on a real workstation (this effort's own safety
  rules: never risk a real machine's real `~/.agent-bridge`/`~/.copilot`).
  Triggering the first real run (and iterating on whatever it surfaces --
  Phase 5's own abrupt-kill-recovery check took nine review rounds to get
  honest and correct) is the next concrete step; recorded as a known gap
  rather than silently claimed done.
- Updated `manifest.json`, `scenario.sh`'s header comments, and
  `tools/clean-room/README.md`'s catalog entry to document the new opt-in
  phase/env vars.

### 2026-09-29 — Phase 6 planned (not started)
- Planned (operator request, after Phase 5 merged) rather than started
  immediately: a Tier-E live-turn-survival harness to close Phase 5's
  deferred Plan item 1 for real -- a genuinely live Copilot turn surviving
  a real `agent-bridge deploy` cutover, not just the Tier-P/stdlib-probe
  mechanism-level proof Phase 5 delivered.
- Read `tools/clean-room/TIER-E-EXECUTION.md` (partially) and the existing
  `agent-vault-eval`/`agent-dispatch-hibernate-eval` scenarios to ground the
  plan. Key finding: every existing Tier-E scenario judges *doc-compliance*
  (does a driven agent discover and follow a plugin's documented mechanism)
  via the `clean-room-judge` LLM judge -- this drill has no doc-compliance
  question at all (it's an infra-reliability assertion independent of what
  the agent does), so it needs Tier-E's container + real-ACP machinery but
  almost certainly a **programmatic** verdict, not an LLM judge. This is
  the central open design question the next session must resolve before
  committing to a scenario shape -- see the Plan's own Phase 6 section for
  the full sketch, feasibility notes, and acceptance criteria.
- No code changed this round; planning-only. Handing off for
  implementation in a fresh session/worktree per the operator's own
  request ("plan it out, then tackle it in a handoff").
- **Corrected after automated review (a plan-saving catch).** The initial
  design sketch proposed registering the box as a `command`-type Tier-E
  provider (`bridge_register.py`'s `docker exec ... copilot --acp --stdio`),
  mirroring the doc-audit scenarios. Review correctly caught that this
  transport **bypasses the Session-Host mechanism entirely**:
  `session_start.py` only routes a **`local`**-type target through
  `_connect_via_session_host` (the survivable-child path Phases 2/3
  actually built and durably tracks in `HostIndex`); `ssh`/`command`/spawn
  providers use a separate frontend-owned process path with no
  host-boundary spawner, never reattached across a cutover. A
  `command`-registered Tier-E session would have proven nothing about
  session-host reattachment -- worse, it risked a **false pass** (the
  session surviving because it drained normally, not because a real
  generation handoff reattached it). Corrected the design sketch to use a
  **local** `agent-bridge create <agent-name> --target-dir <local-repo-
  path>` session instead (the checkout path via `--target-dir`, never the
  positional `target`, which names an agent -- this is the only path that
  actually spawns and durably registers a real Session-Host
  child), which likely means this drill doesn't need Tier-E's
  provider-registration machinery at all -- it's closer in shape to
  extending the existing Tier-P `agent-bridge-cutover` probe with a real,
  credits-consuming session instead of a bare daemon. Re-flagged whether
  the "Tier E" label even applies as an open question for whoever
  implements this.

### 2026-09-29 — Phase 5 landed partially (abrupt-termination drill + clean-room extension; live-turn drill deferred) ([#4586](https://github.com/ThomasMichon/copilot-extensions/pull/4586))
- **What's real here (final implementation).** Extended the pre-existing
  `tools/clean-room/scenarios/agent-bridge-cutover` Tier-P scenario (found
  already covering the daemon-level routing-flip/drain-gate/breadcrumb-
  recovery mechanism, predating this effort) with a new `abrupt-kill-recovery`
  check in `fixtures/cutover_probe.py`:
  1. A disposable **sentinel** orphan record is seeded *before* a real
     daemon starts, then polled (via `HostIndex`'s own strict
     `_load_or_raise()` read path, never the lenient one the real daemon
     uses internally -- a read failure is a distinct, explicitly-raised
     outcome, never silently treated as "the record is gone") until the
     daemon's own ONE-SHOT startup reattach scan (`app.py`'s
     `_reattach_session_hosts_bg`, never periodic) reaps it -- an
     observable signal that scan has actually run, not a timing guess.
     Seeding *before* startup (not after) matters precisely because the
     scan is one-shot: seeding too late races it and can miss the one
     window it runs in.
  2. Only then is the real test record registered (with a *fresh* dummy
     process, never the sentinel's own -- reaping force-kills a record's
     `host_pid`/`child_pid`, so reusing it would mean the "real" record's
     host was already dead before the ownership contract is even exercised)
     and claimed under a test-chosen generation *label* (see the honest
     scope note below) and the live daemon's own real, live pid -- invoking
     the real `HostIndex.claim()` contract directly (the exact call
     `_claim_host_record` makes; a real daemon has no API surface to
     discover and claim an ad hoc record with no live session-host child of
     its own, so this is the honest boundary of what a stdlib-only probe
     can drive without building a full session-host implementation).
  3. That daemon is SIGKILLed -- no drain, no `/api/v1/shutdown` handshake,
     so the exit contract's own release path never runs and the claim is
     durably stranded exactly as recorded.
  4. A fresh daemon starts. Its OWN real startup reattach scan (the SAME
     production code path, not a synthetic stand-in) is polled via the
     on-disk index and required to fully reap the record -- this fixture
     creates no adoptable DB session, so a lingering claim under the fresh
     daemon (rather than a full reap) would itself be a bug, not an
     alternate valid outcome.
  **Honest scope note.** The claim is stamped with a test-chosen generation
  *label*, not the killed daemon's own real `_generation_id`
  (`session_core.py` computes that once per process from
  `version+pid+started_at` -- not independently reproducible from outside
  the process, and there is no API surface exposing it; adding one would be
  its own production change, and doing so ran straight into the same
  contract-registry self-reference wall Phase 3 hit for its
  `HTTP_PROTOCOL_VERSION` bump -- `/health` is a registered semantic contract
  source, so exposing a new field there needs a fixture whose
  `captured_from.commit` cannot reference this PR's own not-yet-existing
  merged commit; tried, hit exactly that wall, reverted rather than force
  it). So this drill demonstrates the underlying dead-pid-recovery
  PRIMITIVE against a genuinely killed real process -- not that the killed
  daemon's own real exit-contract release path
  (`release_all(self._generation_id)`) was interrupted, since that path
  would never have matched an arbitrary label even on a graceful exit. That
  narrower, already-thoroughly-unit-tested claim (`test_admin_routes_
  phase3.py`) is a smaller, orthogonal fact this Tier-P drill does not need
  to re-prove; what it adds is the *dead-pid detection* against a real OS
  process a unit test cannot exercise.
  Sanity-checked by fault-injecting an inverted `zdd.claims.is_recoverable`
  (with `PYTHONPATH` correctly forcing the daemon subprocess to import the
  edited source rather than its installed venv copy -- an earlier sanity
  run without this silently exercised the unmodified installed copy and
  gave a false PASS, caught before claiming verification) and confirming
  the check correctly FAILS; reverted immediately. Documented the new
  check in `tools/clean-room/README.md`'s catalog and the scenario
  manifest's own description. Ran the full 4-check probe stable across
  many runs.
- **Review history (six rounds, each catching a real gap -- kept brief; full
  detail in the PR/commit history).** (1) The first version seeded a
  synthetic placeholder claim *before* the daemon started, so the daemon's
  own one-shot startup scan reaped it before the simulated kill ever
  touched a claim that mattered, and trusted raw subprocess stdout without
  requiring the subprocess to have actually succeeded. (2) The fix's
  `time.sleep(1.5)` guessed at scan timing rather than observing it, and
  "recovery" was verified by a disconnected direct `HostIndex.claim()` call
  standing in for "a new generation," never by the real fresh daemon's own
  recovery path. (3) The sentinel and the real test record shared one dummy
  process, so the sentinel's own reap-triggered kill left the "real"
  record's host already dead before the contract was exercised. (4) The
  final assertion accepted "reclaimed under the fresh daemon" as an
  alternate valid outcome alongside "reaped" -- but this fixture has no
  adoptable session, so the *only* legitimate outcome is a full reap; the
  looser assertion could mask a real bug (a claim silently retained
  forever) as a pass. (5) `_index_get` conflated a genuine "not found" with
  a read failure (a corrupt/locked index, or a crashed subprocess), so
  either could misreport as successful recovery -- fixed via the strict
  `_load_or_raise()` path and a distinct, never-silently-swallowed
  `_IndexReadError`. (6) The claim was stamped with a synthetic literal
  rather than the daemon's own real generation id -- attempted a fix via a
  new `/health` field, hit the contract-registry self-reference wall (see
  the honest scope note above), reverted, and instead narrowed the check's
  own documented claim to match what it actually proves. Each round's fix
  is described in its own commit message; the final, current design (above)
  folds in all six corrections.
- **What's honestly NOT delivered.** The Plan's first bullet asks for a live
  session's Copilot *turn* to survive a cutover with zero observed
  disruption -- that needs a real model/ACP child in the loop synchronized
  with the cutover, which is a materially different (and much larger) build
  than a stdlib-only probe can provide. The pre-existing `routing-flip-retire`
  check's own FIDELITY NOTE already drew this exact line: Tier P proves the
  cutover *mechanism* the guarantee is built on; a fully live turn-survival
  assertion is Tier E. This PR does not build that Tier-E harness -- it is
  left as tracked, deliberately deferred scope, not silently dropped. A
  genuine "reconcile staleness is observable via a status command" surface
  (this effort's own Validation Plan item) also remains open -- the
  `/health` field attempt above would have closed it, but hit the
  contract-registry wall; a real fix needs either a dedicated follow-up PR
  referencing this PR's own real merged commit (Phase 3's established
  pattern for exactly this kind of contract-registry chicken-and-egg), or a
  different, non-`/health` surface.
- Full suite: `python3 tools/run-plugin-tests.py agent-bridge` and the
  4-check clean-room probe both green throughout.

### 2026-09-29 — Phase 4 landed ([#4581](https://github.com/ThomasMichon/copilot-extensions/pull/4581))
- **No new production mechanism was needed.** Reading `client.py`'s
  `BridgeClient._request()` and the CLI `send`/`read`/`wait` call sites
  confirmed the caller-facing mask Phase 4's checklist describes was already
  built and already thoroughly tested, well before this effort:
  - `_get_client()` (the *sole* client-construction path for every CLI
    command, including `send`/`read`/`wait`) always calls
    `BridgeClient.from_config()`, which installs a live `_reresolve` callback
    against `zdd.routing`'s `active.json` -- there is no CLI code path that
    constructs an un-reresolving client for ordinary use.
  - The **drain-refusal 503** (#3179) is real but narrower than this
    journal entry first assumed: only `POST /api/v1/sessions` (brand-new
    session creation, `routes/sessions.py`) checks `is_draining` and refuses
    with it. `post_live_message` (`routes/live_sessions.py`, the route
    `send` actually hits once a target already has a live session -- the
    common case) has **no draining gate at all**: delivering into an
    already-registered live session is cheap local-DB work, not new agent
    work, so the retiring generation keeps serving it normally throughout
    its own drain window. (Caught by automated PR review on the first
    version of this PR's test, which had wrongly generalized the
    session-creation drain-503 to the live-message delivery path -- see
    below.)
  - The real risk window for `send` mid-cutover is therefore not a graceful
    503 at all -- it's the retiring generation's HTTP listener actually
    closing post-shutdown. That produces a *plain connection refusal*
    (`ECONNREFUSED`, wrapped as `urllib.error.URLError`, not
    `ConnectionResetError`), which `_request()` follows to the routing
    table's successor and retries for **any** HTTP method, including this
    non-idempotent POST -- safe because a clean refusal proves the dead
    process never received the request, unlike an in-flight reset.
    Covered generically for GET by `TestReresolveOnRejection` in
    `test_client_connect.py`.
  - A genuine **connection reset** (the process accepted the connection,
    then died mid-request) during a non-idempotent method is deliberately
    **not** auto-retried (`BridgeConnectionError`) -- the correctness-safe
    boundary for the abrupt-kill case Phase 5's own drill exists to
    exercise, not a gap: an unacknowledged POST could double-deliver if
    blindly retried without the caller's own `--idempotency-key` opt-in
    (`send_live_message`/`submit_prompt` already thread one through when
    given).
  - `_stream_feed()` (the engine behind `read`/`wait`) already reconnects
    across a connection error and resumes from the caller's acked cursor
    (`#23`/`#46.6`, `#893`, `#900`), tested by `test_reconnect.py`'s eight
    scenarios including a worktree-handoff follow and a settled-404 report.
- **The one genuine gap**: every existing test above drives `BridgeClient`
  or the streaming engine directly -- none drove the actual CLI `_cmd_send`
  entry point end-to-end through a cutover. Added
  `test_send_transparent_cutover.py::test_send_cli_survives_connection_refused_mid_delivery`,
  which builds a real `BridgeClient` (mocked `urlopen`, not a fake
  in-memory client), makes the retiring generation's port answer with a
  clean connection refusal (matching the route's real behavior, not an
  invented 503), and asserts `agent-bridge send`'s real code path
  (`_cmd_send` -> `resolve_live_session` -> `send_live_message`) prints a
  normal delivery confirmation -- never a traceback or hard failure -- after
  transparently following the routing table to the successor.
- **Full suite**: `python3 tools/run-plugin-tests.py agent-bridge` stayed
  green throughout, confirming none of Phases 1-3's changes disturbed this
  pre-existing machinery.
- Test-only change; no production code touched. Phase 4's own checklist is
  now fully validated with concrete evidence, closing it without new
  runtime mechanism.

### 2026-09-28 — Phase 3 landed ([#4543](https://github.com/ThomasMichon/copilot-extensions/pull/4543))
- **Generation identity**: `SessionManager` (via `_SessionCoreMixin.__init__`)
  computes `self._generation_id = zdd.claims.generation_id(version=
  __version__, pid=os.getpid())` once per process lifetime -- never
  persisted, never reused across a restart, matching `zdd.claims`' own
  contract from Phase 2.
- **Claim wiring**: `_SessionHostRecoveryMixin._claim_host_record(rec)` (new)
  wraps `HostIndex.claim()` with the three outcomes Phase 2's primitive
  already defines -- already-owned (idempotent), a live other generation
  (skip, don't steal), a dead one (recover silently) -- plus a fourth,
  Phase-3-specific outcome for an untracked record (permit; keeps every
  pre-Phase-3 reattach test that fabricates a bare record without
  registering it in a real `HostIndex` working unchanged). Wired into
  `reattach_session_hosts()`'s per-record loop right after the version-mux
  plan is resolved and before the record is ever adopted.
- **Release wiring (the exit contract)**: `routes/admin.py`'s `/api/v1/
  shutdown` handler now calls `HostIndex.release_all(generation_id)` before
  setting `server.should_exit = True`, and returns the released session ids
  in its response. Best-effort (a release failure never blocks the shutdown
  it's guarding against outliving).
- **Post-cutover claim retry**: a new `/api/v1/session-hosts/reattach`
  endpoint re-runs `reattach_session_hosts()` on demand. `venue_cli.py`'s
  `_cmd_deploy` calls it on the newly-active daemon (via `BridgeClient
  ._request` directly, not a new named client method -- `client.py` was
  already sitting at its grandfathered 1686-line module-size ceiling; the
  pre-push module-size guard caught this on the first push attempt) once
  the old generation is *confirmed* exited (the same point that already
  reconciles the service marker and verifies the retired pid) -- see
  Proposal above for why this two-step claim dance (an early,
  mostly-contended pass at startup; a retry once the old generation is
  provably gone) is necessary rather than a single pass sufficing.
- New tests: `plugins/agent-bridge/tests/test_session_host_claims_phase3.py`
  (15 cases: generation-id computation/stability, `_claim_host_record`'s
  four outcomes, and two `reattach_session_hosts()` integration cases for
  the live-conflict-skips / dead-claim-recovers paths) and
  `plugins/agent-bridge/tests/test_admin_routes_phase3.py` (4 route-level
  cases for `/shutdown`'s release and `/session-hosts/reattach`). Two
  existing reattach tests initially regressed (`test_startup_reattach_
  resumes_session_stopped_while_starting`, `test_startup_reattach_leaves_
  prior_idle_session_idle`) because they fabricate a bare `SimpleNamespace`
  record without ever registering it in a real `HostIndex` -- fixed by
  `_claim_host_record` treating an untracked record as "claim not
  applicable" rather than a block (see above). Full `agent-bridge` suite
  (2766+ tests, 7 sub-suites): green on two full runs (one prior run hit a
  transient shared-host `[LIMIT] wall-clock limit exceeded` and a stale
  test-runner lock from an unrelated dead process on this same machine --
  both cleared on retry, neither related to this change).
- **PR #4543's automated review caught four real gaps, all fixed before
  merge:**
  1. HIGH: `HostIndex`'s mutating methods (`register`/`remove`/`claim`/
     `release_all`/...) wrote from each instance's own in-memory snapshot
     with no cross-process coordination -- the old and new generations
     each hold a long-lived `HostIndex` over the *same* file during a
     cutover, so the outgoing generation's own `release_all()` flush could
     silently drop a concurrent registration the new generation had just
     written. Fixed with a short-lived cross-process lock
     (`single_instance_lease.SingleInstance`, already a direct agent-bridge
     dependency) around a reload-latest-then-mutate-then-flush cycle in
     every mutating method -- a mutation now always applies against the
     freshest on-disk state, not a stale snapshot. New regression tests:
     `test_a_second_instances_write_is_not_lost_by_a_stale_first_instance`,
     `test_claim_reloads_latest_state_across_instances`.
  2. MEDIUM: a claim conflict during the startup reattach scan was recorded
     in `_remote_recovery_inconclusive` -- a set checked at the *top* of
     every later call to `reattach_session_hosts()`, so recording it there
     permanently blocked the post-cutover retry this whole mechanism exists
     to make succeed. Fixed by not recording claim contention in that set at
     all (it is retried for free on the next scan, unlike a genuine
     remote-recovery inconclusiveness). New test:
     `test_reattach_retries_successfully_once_a_live_claim_is_released`
     (first pass contended and skipped; the other generation releases;
     second pass succeeds).
  3. MEDIUM: `venue_cli.py`'s `old_confirmed_gone` treated `not old.pid`
     (an unknown pid, e.g. a legacy/partial routing record) the same as a
     *confirmed* retirement, which could trigger the post-cutover reattach
     retry while the old frontend might still actually be attached. Fixed
     to only treat `old is None` (cold start) or a `_ensure_retired_daemon_
     exited`-confirmed exit as confirmed-gone; an unknown pid is now
     conservatively left unconfirmed (the retry simply waits for a later
     opportunity).
  4. LOW: the new `/api/v1/shutdown` response shape and `/api/v1/
     session-hosts/reattach` endpoint were missing from `plugins/
     agent-bridge/docs/architecture.md`'s API list. Added there, plus a new
     "Session-host generation handoff" subsection summarizing the
     claim/release/retry flow and the `HostIndex` locking fix above.
- **A second automated review pass caught five more real gaps** (the fixture
  #1 fix above wasn't wrong, just incomplete -- the write path was safe, the
  ordering and the passive-startup window around it were not):
  1. HIGH: `/api/v1/shutdown` released every claim *before* confirming a
     server handle existed to actually shut down -- a still-live daemon
     (shutdown couldn't be initiated) would have abandoned ownership it
     still held. Fixed: check for the server handle first; release only on
     the path that actually shuts down.
  2. HIGH: on Phase 3's first rollout (and for any record its own active
     generation never itself claimed -- i.e. every normal `register()`),
     an unclaimed record is *correctly* "recoverable" by Phase 2's own
     contract, which let a still-**passive**, not-yet-promoted daemon's
     startup reattach scan claim (and reattach to) a Session Host the truly
     active old generation was still driving, before the verified-retirement
     gate ever ran. Fixed: `reattach_session_hosts(claim_hosts=...)` -- a
     passive instance (`publish_on_ready=False`) still warms up its ACP
     connections (unchanged, pre-Phase-3 behavior) but never touches claim
     state; claiming happens only via the post-cutover retry once the old
     generation is confirmed exited, or at a normal (non-passive) cold
     start, which has no other live generation to race.
  3. MEDIUM: query methods (`all()`/`live_records()`) still read each
     process's own in-memory cache, so the post-cutover reattach sweep could
     miss a session id registered by the other generation *after* this
     `HostIndex` was constructed -- a per-record `claim()` reload cannot
     discover an id absent from a stale enumeration in the first place.
     Fixed: new `HostIndex.refresh()`, called before every reattach scan.
  4. Contract: the new endpoint introduces a client-detectable capability
     with no matching protocol bump. **Attempted, then reverted**: bumping
     `HTTP_PROTOCOL_VERSION` requires new contract-registry evidence whose
     own `captured_from.commit` must reference a real, resolvable commit --
     but every commit on a PR branch here gets rewritten (squashed/rebased)
     by this repo's own `push-changes` flow before it ever reaches origin,
     so no commit on an in-flight PR branch can ever durably reference
     itself. The repo's own precedent (`registry.json`'s existing
     `a1612599f2...` provenance entries) confirms this: that commit is
     itself an *already-merged* PR, meaning the established pattern is a
     **separate follow-up PR** *after* the functional change merges, not
     doing both atomically. Reverted the version bump and the
     `daemon_supports()` gate; the post-cutover reattach call is
     unconditional best-effort instead, mirroring the existing relay-adopt
     post-cutover step (`/api/v1/relay/adopt`), which itself has never
     carried a protocol-version gate either. A follow-up PR bumping
     `HTTP_PROTOCOL_VERSION` and capturing the new evidence, once this PR's
     merge commit exists to reference, is a reasonable Phase 4/5 or
     standalone follow-up -- not a blocker for Phase 3 itself.
  5. Test quality: the original two-instance `HostIndex` tests were
     sequential, so they would still pass even with the cross-process lock
     removed entirely. Added a genuinely overlapping (thread + barrier
     synchronized) concurrent-writer test that only passes if the lock
     actually excludes one writer while the other is mid reload-mutate-flush.
- **A third automated review pass caught the deepest gap of all, plus three
  more real bugs and a policy violation:**
  1. **HIGH -- the actual architectural miss**: `claim_hosts=False` only
     suppressed the *durable claim*; it did **not** stop the passive
     instance's startup scan from reaching `_reattach_one()` and physically
     ATTACHing to each Session Host. `SessionHost._handle_front()`
     unconditionally displaces whatever front already holds a host the
     moment a new one connects -- so a still-**passive**, not-yet-promoted
     daemon's own "harmless" reattach-and-warm-up was actually
     **disconnecting the truly active old generation**, before the
     verified-retirement gate ever ran. This was strictly worse than the
     claim-only gap Phase 3 started with. Fixed properly this time: the
     entire startup reattach *task* is now skipped for a passive instance
     (a new explicit `app.state.passive` flag, distinct from
     `publish_on_ready` -- see finding 4 below for why); only the
     post-cutover retry ever reattaches for it.
  2. HIGH: newly-**spawned** hosts (`register()` at session-host launch, not
     via reattach) were never stamped with this generation's own ownership
     -- they looked "never claimed" (freely recoverable) to any other
     generation's reattach scan the moment they were created, and
     `release_all()` could never find them to release. Fixed: the spawn
     path now stamps `owner_generation`/`owner_pid` at registration time.
  3. HIGH: `_load()`'s reload-before-mutate rewrite reset `self._records`
     to `{}` *before* attempting the read, so a transient `OSError` or a
     momentarily-partial read on an *existing* index left the in-memory
     state empty -- and `_locked_reload()` would then flush that empty
     state, erasing every other host record on the very next mutation.
     Fixed: parse into a temporary map first; only replace `self._records`
     on a fully successful read, and a stricter `_load_or_raise()` variant
     used specifically inside `_locked_reload()` now **aborts the mutation
     entirely** (never reaches `_flush()`) on a read failure, rather than
     silently proceeding against stale state.
  4. HIGH: `register()` blindly overwrote a record's `owner_generation`/
     `owner_pid` with whatever the caller's copy carried -- and a caller
     refreshing a remote forward's port (`_ensure_forward()`) can easily be
     holding a copy from *before* a concurrent `claim()`, silently
     reverting a just-written claim. Fixed: `register()` now always
     preserves the durably-recorded ownership fields when updating an
     *existing* record -- it is a location/metadata update, never an
     ownership change; only `claim`/`release`/`release_all` mutate
     ownership.
  5. Fixing finding 1 broke `getattr(app.state, "publish_on_ready", False)`
     as a passive-detection signal for anything **other** than the real
     `agent-bridge start` CLI: `publish_on_ready` is only ever set there,
     so every test harness that builds `create_app()` directly (i.e. nearly
     every existing test) defaulted to "looks passive" and silently stopped
     reattaching at all -- three previously-green tests regressed. Fixed
     with a new, explicit `app.state.passive` flag (defaults `False`
     everywhere it isn't set, the safe default), and updated the two
     regressed tests (`test_reattach_session_hosts_on_restart`,
     `test_reattach_reaps_orphaned_host`) to explicitly simulate the
     outgoing generation's own exit-contract release
     (`HostIndex.release_all`) they'd been implicitly relying on skipping.
  6. Policy: widening `tools/module-size-baseline.json` is reserved for the
     scheduled/post-merge baseline workflow, not an ordinary feature PR.
     Reverted the earlier widening; trimmed `app.py`'s own net addition to
     exactly zero lines instead (compacted comments/log calls already in
     the touched function -- no unrelated code moved).
- **A fourth pass** found three small process nits (changefile `type` should
  default to `patch`, not `minor`, absent explicit maintainer direction;
  `routes/admin.py`'s reattach-endpoint docstring still described the
  pre-fix "runs while passive, contended" shape; the PR description needed
  this repo's specific **Graceful cutover impact** statement, distinct from
  the generic Documentation impact one already added) -- all fixed -- plus
  one genuine **known, deliberately scoped-out limitation**: a legacy/partial
  routing record with no recorded `pid` makes `old_confirmed_gone` stay
  `False` forever (correctly conservative -- see finding 3 in the second
  pass above), but nothing then *retries* the post-cutover reattach for that
  one case; a surviving Session Host under a legacy record can be left
  unattached until an unrelated recovery path eventually runs. Building a
  periodic/background retry specifically for this edge case is real work in
  its own right (this effort's Plan already earmarks Phase 5 for the two
  validation drills that would surface exactly this kind of gap) -- tracked
  here rather than rushed in.
- This effort's own umbrella issue's Phase 4/5 remain: the caller-facing
  mask/routing layer and the two validation drills.

### 2026-09-28 — Phase 2 landed ([#4522](https://github.com/ThomasMichon/copilot-extensions/pull/4522))
- **`zdd.claims`** (new, canonical `libs/zdd`): storage-agnostic
  claim/release/recover decision logic. `decide_acquire(record, key=...,
  generation=..., owner_pid=..., pid_alive=...)` is the one entry point:
  free record -> acquire; same generation -> idempotent no-op; a *live*
  different generation -> raises `ClaimConflict`; a *dead* owning
  generation -> recovers silently (no live handshake with the dead process,
  per the Plan's own wording). `is_recoverable()` and `generation_id()` are
  the two supporting helpers. Deliberately does not define its own durable
  manifest -- HostIndex already is one (see below) and the Plan says to use
  it, not invent a new one.
- **`agent_bridge.session_host.host_index.HostRecord`** gained
  `owner_generation: str` and `owner_pid: int` (the *daemon's* pid, never
  the session-host child's -- `host_pid`/`child_pid` already track the
  child). `HostIndex` gained `claim`, `release`, `release_all`,
  `claims_owned_by`, and `recoverable_claims`, all thin wrappers over
  `zdd.claims` that persist through the index's existing atomic-JSON
  storage -- no new manifest, no migration.
- **Cutover-wide serialization**: `zdd.cutover_lock.CutoverLock`, a
  stdlib-only (no new package dependency -- `zdd`'s own pyproject declares
  zero runtime deps and this keeps it that way) cross-platform
  (`fcntl.flock`/`msvcrt.locking`, mirroring `single_instance_lease`'s
  proven approach but scoped to one `run()` call rather than a whole daemon
  lifetime) exclusive lock. `CutoverOrchestrator.run()` now acquires it for
  the full cutover sequence and, on contention, returns a normal
  `CutoverResult(ok=False, error=...)` naming the holder's pid rather than
  raising -- so `_cmd_deploy`'s existing result-handling needed no changes.
  This closes the exact gap PR #4478's sixth review pass flagged: two
  concurrent `deploy`/`service restart` invocations can no longer race the
  same breadcrumb/routing state.
- **A ninth `zdd` consumer, previously uncounted**: `check-vendored-libs-
  sync.py` found `plugins/agent-worktrees/libs/zdd` alongside the 8 the
  effort's Guiding Intent and Context sections named -- corrected
  throughout (see Context above). **A tenth was found after that**:
  `worktree-manager` (a top-level project, not under `plugins/`) also
  vendors `libs/zdd` -- missed by an initial `find plugins -name zdd`
  sweep. All 10 copies (plus canonical `libs/zdd`) were bumped to
  `0.1.0-dev6` and re-synced byte-identical via `rsync`
  (`tools/sync-vendored-libs.py --materialize` was tried first but also
  touches unrelated pointer-vendored libs on a bare `--materialize` run
  with no per-lib filter; reverted that incidental side effect and did the
  zdd-only sync by hand instead). `check-vendored-libs-sync.py` confirms
  `OK` afterward.
- **A real bug caught by CI, not by any of the manual suite runs above**:
  `worktree-manager`'s own `mux_daemon_cutover.py` (PR #4497, independent
  of this effort) already ships a bespoke bounded-wait cutover lock
  (`_acquire_cutover_lock`/`_CutoverLease`) at
  `<routing_dir>/cutover.lock`. The new `zdd.cutover_lock.CutoverLock`
  originally used that same bare filename -- so the moment a consumer
  wraps `CutoverOrchestrator.run()` in its own outer serialization (exactly
  what `worktree-manager` does), the orchestrator's own inner lock
  acquisition opened a *second* file handle on the identical path and
  self-deadlocked against the lock its own caller already held (POSIX
  `flock` is per-open-file-description, so this doesn't self-resolve).
  CI's `worktree-manager (out-of-plugin)` job caught it;
  `test_activate_after_update_cuts_over_and_converges` hung/failed even on
  its *first* (uncontended by anything external) cutover. Fixed by
  namespacing the lock filename to `zdd-cutover.lock`, documented in the
  module docstring alongside the collision it avoids. A future phase may
  consolidate `worktree-manager` onto this shared primitive instead of its
  own hand-rolled copy; the two are independent and harmless together in
  the meantime.
- **A related design correction, caught by the same failure**: the first
  version of `CutoverLock.acquire()` refused outright on *any* contention.
  `worktree-manager`'s own convergence test deliberately fires a second
  cutover while a first is still draining, expecting the second to
  *succeed once the first finishes* -- exactly the shape a "cutover-wide
  serialization" primitive should support, not merely refuse. Redesigned
  `acquire()` to **wait** (bounded, poll-retrying) up to a `timeout`
  before raising `CutoverLockedError`; `CutoverOrchestrator.run()` now
  waits up to `health_timeout + drain_timeout + 60` by default rather than
  failing instantly on any contention.
- New tests: `libs/zdd/tests/test_claims.py` (10 cases),
  `libs/zdd/tests/test_cutover_lock.py` (10 cases, including the
  bounded-wait-then-succeed and wait-then-exhausted paths, POSIX-only
  where they rely on flock's per-open-file-description semantics,
  mirroring `single_instance_lease`'s own same-process test), `libs/zdd/
  tests/test_cutover.py` gained 2 lock-integration cases (refuse only
  after the wait budget, and wait-then-succeed once the holder releases),
  and `plugins/agent-bridge/tests/test_host_index_claims.py` (13 cases)
  for the `HostIndex` wrapper. `libs/zdd`'s own suite: 83 passed (60
  pre-existing + 23 new). `agent-bridge`'s full suite (2766 tests, 7
  sub-suites): all green. `worktree-manager`'s own suite (1560 tests, run
  via its real CI invocation `uv run --extra dev pytest -q`): all green
  after the lock-filename fix. All 8 other `zdd` consumers' own suites run
  individually: agent-worktrees (522 passed), agent-vault (359 passed),
  agent-mcp (258 passed, 6 skipped), agent-dispatch (634 passed, 1
  skipped), agent-codespaces (294 passed) all green. Two pre-existing,
  unrelated failures found and confirmed (by reproducing against
  unmodified `dev` with this change stashed) to predate this effort
  entirely: `agent-ssh`'s
  `test_dtssh_apply_updates_existing_binary_without_login` (a host-restore/
  subprocess-mocking assertion, nothing to do with `zdd`) and
  `agent-containers`'s `test_relay_profile_cannot_replace_refusal_with_
  default_allowlist` (a `credential_relay`/`shutil.which` PATH-selection
  assertion). `agent-index`'s suite hit the shared host's `[LIMIT]
  temporary-storage limit exceeded (2048 MiB)` containment ceiling on both
  the modified and the unmodified tree -- an environment/resource
  constraint, not a code regression.
- Phase 3's liveness gate and exit-contract sequencing were deliberately
  **not** started here: wiring `HostIndex.release_all`/`claim` into the
  real `_cmd_deploy`/`session_core.py` call sites needs the liveness gate
  (Phase 3) as a prerequisite -- the outgoing generation's exit contract is
  explicitly "confirm the next generation is live" *first*. Building a
  partial wire-up now would ship a mechanism that cannot actually complete
  a handoff yet. Phase 2's own checklist (claim/release/recover primitive +
  cutover-wide lock) is fully closed; Phase 3 picks up the call-site wiring.

### 2026-09-28 — Phase 1 landed
- `agent-bridge service restart` now routes through `_cmd_deploy` directly
  (`service_process_cli.py`'s `_cmd_service`) instead of a raw
  `_service_stop()` + `_service_start()` — same
  `zdd.cutover.CutoverOrchestrator` flow as `deploy` (spawn passive ->
  health-gate -> flip -> drain -> retire), including when no daemon is
  currently running (`CutoverOrchestrator.run` already tolerates
  `old_endpoint is None`). The `restart` subcommand gained the same
  `--health-timeout`/`--drain-timeout`/`--force`/`--json` flags `deploy`
  exposes so the shared code path has every attribute it reads.
- Audited every other reachable raw stop/start caller: installers
  (`scripts/install.sh`/`install.ps1`) and the shared
  `bootstrap-check.sh`/`.ps1` reconcile hooks already invoke
  `agent-bridge deploy` (or the installer, which itself deploys), never a
  raw restart. The only remaining `_service_stop`/`_service_start` pairing
  outside the CLI is `venue_cli.py`'s
  `_fault_frontend_restart_hostindex_loss` — a deliberate fault-injection
  harness that exercises the *old* dangerous path on purpose to prove
  HostIndex recovery works even without a ZDD handoff; left untouched.
  `routes/worktrees.py`'s `"restart"` is an unrelated verb (restarting a
  worktree's mux-launched Copilot session, not the agent-bridge daemon).
- Added `tests/test_service_restart_zdd.py` (grew to 7 tests across the
  five review passes below) locking in that `restart` calls `_cmd_deploy`
  and never the raw stop/start pair, that its argparse Namespace carries
  every attribute `_cmd_deploy` reads, that `--recover` stays deploy-only,
  that the shared `--json` flag never shadows the global one, and that
  `_cmd_deploy --json` output stays valid JSON even mid-recovery.
- Full `agent-bridge` suite green (`tools/run-plugin-tests.py agent-bridge`).
- Filed the umbrella issue,
  [#4477](https://github.com/ThomasMichon/copilot-extensions/issues/4477).
- PR #4478's automated review caught four real gaps, all fixed:
  - the regression test patched the unused module-level
    `_service_stop`/`_service_start` names instead of what `_cmd_service`
    actually calls (`core._service_stop`/`core._service_start` via
    `core = _core()`) — fixed so a regression back to the raw path would
    fail the test;
  - `service_process_cli.py` imported the private `_cmd_deploy` symbol
    directly instead of the `venue_cli` module — switched to
    `venue_cli._cmd_deploy(args)`;
  - `deploy` and `service restart`'s flags were duplicated — extracted
    `venue_cli.add_deploy_cutover_flags()` so both share one registration;
  - `docs/machine-config.md` and
    `skills/agent-bridge/references/cli-commands.md` still told operators
    `systemctl --user restart agent-bridge.service` (the unit's own
    `ExecStart` is `agent-bridge start`) was an interchangeable restart —
    it bypasses the CLI (and its ZDD cutover) entirely. Both now say never
    to call the platform service manager's restart directly, and the
    wording was later softened per a second review pass (below) once the
    unit's `KillMode=process` + the shutdown path's detach-for-reattach
    behavior were confirmed to make a manager restart *uncoordinated*, not
    a guaranteed session-host loss.
- A second review pass on that doc fix caught remaining rough edges, all
  fixed: an absolute "will drop every live session-host" claim overstated
  the actual risk (systemd's `KillMode=process` plus the shutdown path's
  detach-for-background-recovery mean sessions can often reattach; the
  manager path is uncoordinated/potentially disruptive, not a guaranteed
  loss); the CLI reference's Service Control intro paragraph said *all*
  `service` subcommands delegate to the platform manager, which now
  contradicts `restart`'s deploy-cutover behavior; and this Journal
  undercounted the fixes above as three instead of four.
- A third review pass caught one real bug and two smaller gaps, all fixed:
  the shared `add_deploy_cutover_flags()` helper also exposed `--recover`
  on `service restart`, but `--recover` is a deploy-only maintenance mode
  that exits without starting a new cutover -- `service restart --recover`
  would have returned success while never actually restarting the daemon;
  the helper now takes `include_recover=False` for `restart` (deploy still
  gets it, locked in by a new test). `tests/test_service_restart_zdd.py`
  gained `pytestmark = pytest.mark.guard` so this restart regression is
  covered by the fast contract lane (`--guards`), per `TESTING.md`. The CLI
  reference's Windows warning named `schtasks /Run` as the disruptive
  restart path, but the scheduled task is registered `-MultipleInstances
  IgnoreNew`, so a bare `/Run` while active is simply ignored -- reworded to
  name the actual equivalent (`schtasks /End` then `/Run`).
- A fourth review pass caught one real bug and one public-safety nit, both
  fixed: `add_deploy_cutover_flags()`'s `--json` used `default=False`,
  which — being a subparser flag sharing the top-level `--json`'s `json`
  dest — silently overwrote the canonical `agent-bridge --json service
  restart`/`--json deploy` invocation back to `False` (the exact
  argparse-Namespace-collision class already documented and guarded in
  `tests/test_session_selection.py`'s
  `test_global_json_flag_survives_into_resume_namespace`). Switched to
  `default=argparse.SUPPRESS`, matching the existing `parity_p` pattern in
  the same file; a new test parses through the real `build_parser()` to
  cover both invocation orders. Also removed an accidental downstream-private
  project name (a "dotfiles#1362" reference — this repo's own public
  `#1362`) from the new test file's docstring per this repo's
  public-artifact policy (`AGENTS.md`).
- A fifth review pass caught one real bug and a docstring nit, both fixed:
  `_cmd_deploy`'s recovery/passive-reap status messages
  (`"[>] Recovered a prior aborted cutover: ..."` /
  `"[>] Reaped an abandoned never-promoted passive ..."`) printed to
  stdout *unconditionally*, ahead of the single `core._json_out(res.to_dict())`
  call `--json` mode relies on -- corrupting the JSON payload whenever a
  restart/deploy happened to heal a stale cutover or reap an abandoned
  passive (a pre-existing `deploy --json` bug, newly exercisable through
  `service restart --json` after Phase 1). Both messages now become
  `steps` entries prepended to the `CutoverResult` before it's ever printed
  or serialized, so they show up in both text and `--json` output the same
  way every other step does. A new test drives `_cmd_deploy` end-to-end
  with a faked `CutoverOrchestrator` and asserts `capsys`' stdout parses as
  a single valid JSON object. Also corrected a test docstring that named a
  nonexistent `agent-bridge service deploy --json` command (`deploy` is a
  top-level verb, not a `service` action).
- A sixth review pass raised a real HIGH-severity architectural gap —
  the shared deploy/cutover path has no process-wide cutover lock, so two
  concurrent invocations (two operators, or a restart racing an installer
  deploy) can race the same breadcrumb/routing state — but assessed and
  scoped rather than folded into Phase 1: `agent-bridge deploy` was already
  directly invocable and exposed to this exact race before this effort;
  Phase 1 only widens exposure by routing `service restart` (a routine,
  actively-recommended operator command) onto the same path. Building a
  correct **cross-platform** lock (the installers' own `.install.lock` only
  covers installer-driven deploys, and Windows/POSIX file-locking semantics
  differ enough to need real design) plus a process-level contention
  regression test is squarely Phase 2/3's remit — the effort's own Plan
  already reserves that phase for the generation-scoped claim/release/
  recover primitive this concern is the same shape as. Captured as an
  explicit Phase 2 checklist item (see above) instead of rushed into this
  PR under review pressure.
- Phase 0's one open item (the opt-in reconcile-gate design fork) remains
  genuinely undecided — flagged for the operator, not resolved here.

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
- Phase 6 design: [`phase-6-tier-e-live-turn-harness.md`](phase-6-tier-e-live-turn-harness.md)
