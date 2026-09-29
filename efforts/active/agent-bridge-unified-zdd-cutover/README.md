---
visions:
  - visions/plugins/agent-bridge
---

# agent-bridge — one canonical ZDD cutover, with a generation-scoped session-host handoff

- **Slug:** `agent-bridge-unified-zdd-cutover`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `dev`
- **Created:** 2026-09-28
- **Status:** In Progress (Phase 1 of 5 merged — [#4478](https://github.com/ThomasMichon/copilot-extensions/pull/4478); Phase 2 of 5 merged — [#4522](https://github.com/ThomasMichon/copilot-extensions/pull/4522))
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
