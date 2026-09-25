# Session-Rescue Parity (Containers <-> CodeSpaces)

- **Slug:** `session-rescue-parity`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs (see Coordination)
- **Created:** 2026-09-25
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends `visions/plugins/agent-containers/README.md`
  §`rescue-before-destructive-replacement` (generalizing it to an
  on-demand, non-destructive trigger independent of replacement — see
  Context; periodic scheduling itself stays a consumer concern) and
  `visions/plugins/agent-codespaces/README.md` §`telemetry-grade-session-capture`
  (generalizing "captured on teardown/recycle" to "capturable on demand
  while leased/running"); touches `visions/venue-parity/README.md` only as a
  boundary note (see Context — this is deliberately NOT a venue-parity
  extension).
- **Umbrella issue:** [#3642](https://github.com/ThomasMichon/copilot-extensions/issues/3642)
- **Sub-issues:** _TBD_

## Guiding Intent

`agent-containers` just closed a real gap
(`ThomasMichon/copilot-extensions#3574`): its restricted-fleet Copilot
session-state rescue was only ever triggered by destructive replacement
(stop/remove), so a single shared, deliberately-never-recycled container
never surrendered its session evidence at all. The fix added a non-destructive
`agent-containers rescue-capture <fleet>` verb — reusing the existing
admission/liveness gating, callable on demand or on any caller-chosen
schedule. **Note:** `#3574` itself adds only the verb + gating + capture/
publish path in this repository; the periodic systemd timer that actually
calls it on a schedule is downstream consumer configuration (this repo has
no scheduling of its own for it) — see the same clarification repeated at
each Plan/Context reference below.

`agent-codespaces` has the **identical shape of gap**. Its
`sync_codespace_sessions()` (in `sessions.py`) already pulls
`~/.copilot/session-state` non-destructively over SSH and pushes it into the
same agent-logger hub — but every call site is tied to a lifecycle
transition (`stop`/`finalize`/`delete`, the reclaim callback). Since
`pool.py` treats CodeSpaces as a **long-lived, reusable, budget-bounded
pool** (leased, not necessarily recycled per task), a CodeSpace held by a
long session accumulates the exact same "no periodic evidence extraction
until teardown" exposure agent-containers just fixed.

This effort's goal: **align the two providers' rescue behavior** so a
periodic, non-destructive "capture without disturbing the venue" capability
exists in both, sharing what is genuinely common in source (vendored,
byte-identical, per the repo's existing `libs/<lib>` pattern — e.g.
`ssh-manager`, `credential-relay`, `config-migrate`, already vendored into
both `agent-containers` and `agent-codespaces`) rather than two independently
drifting implementations. Each provider keeps what is genuinely
venue-specific (containers: `docker exec` + the restricted fleet's
admission-hold/lease/policy gating; CodeSpaces: SSH + the lease pool's
in-use signal) — see Context for the concrete split.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Effort owner (rotates per phase) | Drives the active phase; see the effort's Journal for current owner and phase | This repo's normal worktree/PR flow — no fixed venue |

_Later phases (per-provider rollout, vision reconciliation) may be split
across further worktrees/sessions as independent per-plugin PRs; see
Coordination._

## Coordination

- **Topology:** independent per-slice PRs (one per phase below), not a shared
  feature branch — each phase is independently reviewable and mergeable.
- **Host (owns PRs):** effort owner for the phase in progress (see Journal).
- **Delegates:** none yet; a later phase may split containers-side vs.
  codespaces-side work across separate sessions once Phase 1's design is
  agreed.
- **Handoff:** each phase's PR merges before the next phase starts; the
  Journal records phase handoffs.

## Context

### Where this came from

Landed same-session as this effort's creation:
`ThomasMichon/copilot-extensions#3574` (`agent-containers rescue-capture
<fleet>`), driven by a downstream consuming project's own containerized
always-on reviewer service hitting exactly this gap. That work reused
`_restricted_member_action`'s exact admission/idleness/policy/liveness
gating with the destructive action step skipped (`action=None`) and added a
`captured: list[str]` result field — this repository's own scope. The
downstream consumer separately wired a periodic host-side timer plus a
paired publish step calling this new verb on a schedule; that scheduling
lives entirely in the consumer's own repo, not here. Full detail on the
`#3574` change itself lives in its own review history, which caught two
real correctness gaps worth re-checking against any codespaces port: never
unpause a paused venue just to capture it, and a non-running/absent venue
must defer with the same message every code path produces, not a second
ad-hoc one.

### What already exists on the codespaces side

`plugins/agent-codespaces/src/agent_codespaces/sessions.py`:
`sync_codespace_sessions()` — pulls `~/.copilot/session-state` (+
`session-store.db*`) over the multiplexed SSH connection via `tar czf |
base64` (sentinel-wrapped, robust to interleaved banner/log text), then
shells out to `session-sync push --source <staging> --machine
.codespaces/<name>` (agent-logger). It is already a clean, reusable,
non-destructive function — genuinely closer to done than agent-containers
was before Phase 3.

Every current call site (`__main__.py`'s `_cmd_delete`/`_cmd_finalize`/
`_cmd_stop` and their JSON-modal variants, `claim_provider_cli.py`'s reclaim
callback) is a lifecycle transition. There is no standalone "pull now, keep
the CodeSpace running" CLI verb, and no periodic timer.

### The one real safety gap codespaces has that containers doesn't

Containers' `rescue-capture` refuses to capture during an active session: it
probes `~/.copilot`'s `inuse.*.lock` marker files (a **generic Copilot CLI
session-state convention**, not container-specific — see
`agent_containers/replacement.py`'s `probe_session_liveness`, which greps
`inuse.*.lock` under each session's dir and backstops the PID against
`/proc/<pid>` + a process/cmdline check) before ever pulling/capturing.
`sync_codespace_sessions()` pulls unconditionally today — safe for a
destroy-time pull (nothing to preserve after), but risky for a periodic
capture that must not snapshot a session mid-write.

**The portable piece:** the `inuse.*.lock` + `/proc/<pid>` liveness-probe
technique is a property of the Copilot CLI's own session-state layout, not
of Docker. The same script (or a parameterized close relative) can run over
SSH inside a CodeSpace exactly as it runs over `docker exec` inside a
container. This is the concrete candidate for the shared vendored piece
(see Plan Phase 2).

### What is NOT shareable (stays venue-specific)

- **Transport.** `docker exec` (containers) vs. SSH via `ssh-manager`'s
  `ConnectionManager` (codespaces) — already abstracted at the venue-parity
  layer for *trusted* venues, but containers' `rescue-capture` targets the
  **restricted** fleet specifically, which venue-parity explicitly places
  out of its own scope (see `visions/venue-parity/README.md`: "Parity
  applies to *trusted* venues... An untrusted/restricted container is a
  different mode... out of the parity scope by design"). This effort is
  therefore a **peer of venue-parity for this one capability**, not an
  extension of it — noted structurally, not layered inside venue-parity's
  trusted-only contract.
- **Admission model.** Containers' restricted fleet uses a heavyweight
  deploy-hold + `active_session_admissions` + effort-lease + restricted-
  policy-validation gate (`_restricted_member_action`) before even probing
  liveness — necessary because a restricted container's identity/state can
  itself drift or be attacked. CodeSpaces have a much simpler single-tenant
  `lease.py` in-use signal (one effort holds one CodeSpace at a time; no
  restricted-policy surface to validate). Porting the *liveness probe* does
  not require porting the *admission-hold* machinery.
- **Destination push mechanism.** Containers publish via `agent-containers`'
  own rescue store (`$STATE_DIR/rescues/`) + `session-sync rescue-push`
  (hash-verified, allowlisted, capture-ID-pinned). CodeSpaces already push
  directly via `session-sync push --source <staging> --machine
  .codespaces/<name>` with no intermediate rescue-store layer. Whether to
  unify these two publish paths (both eventually go through agent-logger)
  is an open design question for Phase 1, not assumed here either way.

### The vendoring mechanism to reuse (not invent)

This repo already vendors shared code byte-identically per consuming
plugin — `plugins/<plugin>/libs/<lib>/`, auto-discovered (no registry file)
and guarded by `tools/check-vendored-libs-sync.py` (`--list` confirmed the
current map, 2026-09-25). Six libs are already vendored into **both**
`agent-containers` and `agent-codespaces` today: `ssh-manager`,
`credential-relay`, `config-migrate`, `agent-procutil`, `venue-copilot`, and
`zdd`. (Three more — `dropin-registry`, `plugin-activation`,
`plugin-resolve` — are vendored into `agent-codespaces` but NOT
`agent-containers`; don't assume they're shared without re-checking
`--list`.) Adding a new shared lib is: create
`plugins/agent-containers/libs/<new-lib>/` and
`plugins/agent-codespaces/libs/<new-lib>/` with identical `src/` trees and
matching `pyproject.toml` versions, then in **each** consuming plugin's own
`pyproject.toml` add **both** the distribution name to
`[project].dependencies` (e.g. `"agent-<new-lib>"`) **and** a matching
`[tool.uv.sources] agent-<new-lib> = { path = "libs/<new-lib>" }` entry --
`[tool.uv.sources]` only controls how an *already-declared* dependency
resolves; without the `[project].dependencies` entry too, the vendored
package is never installed into a standalone marketplace environment and
the new imports fail there even though a same-checkout dev run might not
catch it. The guard picks
it up automatically once both entries are present. `docs/patterns/README.md`'s `versioned-runtime`
paragraph documents the same fan-out pattern for a single canonical source
(`libs/versioned-runtime/versioned_runtime.py`) synced by a dedicated tool
(`tools/sync-versioned-runtime.py`) rather than the plain byte-identical
vendored-copy model most `libs/<lib>` use — worth deciding in Phase 1 which
shape fits a shared liveness-probe script better (a plain vendored lib is
likely the right fit; it is small, stable, and has no per-plugin
config surface, unlike `versioned_runtime.py`'s adapter role).

## Request

_(operator, verbatim, 2026-09-25)_ "Could we reuse any architecture in
common here for agent-codespaces? We could also benefit from a sync-as-we-go
pull model from Codespaces, to match Containers. Codespaces are
similarly-isolated, for the most part." Followed by, once the agent
presented the comparison above: "Yes, carve an effort to align these
behaviors to be vendorable (which can now dedupe in source via the
dev-branch vendoring flow) between containers and codespaces, sharing the
best learnings from each."

## Plan

### Phase 1 — Design: what's shared, what's venue-specific, and how it lands
_(agent-recommended breakdown of the operator's ask into phases; the request
itself did not specify phasing)_

- [ ] Confirm the liveness-probe technique (`inuse.*.lock` + `/proc/<pid>` +
      process/cmdline backstop) is genuinely portable as a vendored lib:
      read `agent_containers.replacement.probe_session_liveness` end to end,
      identify exactly what is Docker-`exec`-specific (the transport call)
      vs. generic (the shell script + parsing), and sketch the
      transport-injection seam (a small callable the vendored lib takes,
      rather than hardcoding `docker exec`). **The seam must be
      async-compatible from the start**: containers' probe and `_docker`
      call are synchronous, but codespaces' `exec_with_retry`/
      `ConnectionManager` are `async def` and the capture path already runs
      inside `asyncio.run(_run())` -- a directly-shared synchronous callable
      would either return an unawaited coroutine or fail with "asyncio.run()
      cannot be called from a running event loop." Design the vendored
      lib's contract so the pure probe/parser logic is transport-agnostic
      and callable from both a sync (containers) and an async (codespaces)
      caller (e.g. the lib owns only the shell script + output-parsing pure
      function, and each consumer supplies its own sync-or-async transport
      call around it) -- do not assume one shared function signature covers
      both without this split.
- [ ] Decide the vendored lib's shape and name (e.g.
      `libs/session-liveness-probe/`) and whether it fits the plain
      byte-identical vendored-copy pattern or the adapter/sync-tool pattern
      (see Context's vendoring-mechanism note) -- record the decision and
      why.
- [ ] Decide whether to unify the two publish paths (containers'
      rescue-store + `rescue-push` vs. codespaces' direct `session-sync
      push`) or deliberately keep them separate -- record the decision and
      why; do not assume unification.
- [ ] **Define CodeSpace lease ownership for capture, explicitly.**
      Containers' `rescue-capture` reuses `_restricted_member_action`'s
      full admission gating unconditionally, including deferring on any
      active effort lease (`get_lease(info.name) is not None`) -- even
      though a pure read-only capture destroys nothing. Decide, for
      CodeSpaces, whether the same "defer on any active lease regardless of
      holder" rule applies, or whether a read-only capture is safe to run
      against a leased CodeSpace regardless of who holds it (or only when
      the caller IS the lease holder) -- and who/what is authorized to
      *call* the capture verb in the first place (the lease holder only?
      any host process? a periodic sweep with no effort identity at all?).
      Record the decision and why; Phase 3's tests must cover both the
      owner and non-owner/no-lease cases per that decision, not just the
      happy path.
- [ ] **Evaluate this effort's design against `docs/patterns/README.md`'s
      architecture-pattern invariants before implementation begins** --
      this introduces a new shared runtime boundary across two
      independently installable plugins (à-la-carte independence: each
      plugin must remain fully functional if the other is absent/disabled,
      so the vendored lib itself must carry no cross-plugin runtime
      dependency, only a compile-time/vendored source dependency) and the
      vendored/versioned-install contract (`docs/install-contract.md`,
      the same-page `versioned-runtime` paragraph). Record which
      invariants apply, how the vendored-lib design satisfies each, and
      any invariant that constrains Phase 2's shape choice.
- [ ] **Decide, explicitly and once, whether periodic CodeSpaces scheduling
      is repository-owned or consumer-owned** -- this decision governs
      Phase 4 and must be made before it starts, not assumed by it.
      Candidates: (a) mirror the containers precedent exactly -- this repo
      ships only the on-demand verb + liveness gate, and any actual
      timer/cron/systemd-unit that calls it on a schedule is downstream
      consumer configuration (matching `#3574`, which added no scheduling
      of its own); or (b) hook it into an already-repo-owned periodic loop
      that exists today (e.g. the Connection Owner daemon's
      `run_owner_daemon` loop in `connection_owner.py`, or a pool-sweep
      cycle in `pool.py`, if one already runs unconditionally for every
      leased CodeSpace) -- only viable if such a loop is confirmed to exist
      and already covers every venue this capability needs to reach. Record
      the decision and why; then make Phase 4, the vision-reconciliation
      wording below, and the Validation Plan all agree with whichever is
      chosen -- an effort that says "consumer-owned" in one place and
      commits to "wire the periodic trigger" in another is broken, not
      merely incomplete.
- [ ] Reconcile the two visions this effort touches (per the "Documentation
      impact" / vision-reconciliation obligation): revise
      `visions/plugins/agent-containers/README.md`'s
      `rescue-before-destructive-replacement` behavior description to note
      the now-realized non-destructive capture trigger (independent of
      destructive replacement; whether/how it runs periodically remains a
      consumer scheduling concern, not something this repo provides) as a
      peer of the destructive-replacement trigger, and
      `visions/plugins/agent-codespaces/README.md`'s
      `telemetry-grade-session-capture` to note on-demand, non-destructive
      capture while leased/running as a target alongside teardown/recycle
      capture -- phrased to match whichever scheduling-ownership decision
      the item above settles on. Do **not** touch
      `visions/venue-parity/README.md`'s trusted-only scope boundary --
      this effort is a structural peer to it, not an extension (see
      Context).
- [ ] Submit this effort's plan as a PR (this repo's automated-review gate)
      before starting Phase 2.

### Phase 2 — Extract the shared liveness-probe as a vendored lib
- [ ] Extract the portable liveness-probe logic from
      `agent_containers.replacement` into the vendored lib decided in
      Phase 1, with the container-specific `docker exec` call factored out
      behind a small injected transport callable per Phase 1's
      sync/async-split decision. `agent-containers`
      itself switches to consuming the vendored copy (no behavior change;
      existing tests must still pass unchanged) -- this requires wiring
      **its own** `pyproject.toml`'s `[project].dependencies` **and**
      `[tool.uv.sources]` entries for the new lib too (both entries, per
      `CONTRIBUTING.md`'s vendoring guidance and this effort's own Context
      note -- this is not codespaces-only wiring).
- [ ] Vendor the identical copy into `plugins/agent-codespaces/libs/`, wire
      its `pyproject.toml`'s **both** `[project].dependencies` entry and
      matching `[tool.uv.sources]` entry (per Context's clarification --
      `[tool.uv.sources]` alone does not install the package into a
      standalone marketplace environment), and confirm
      `tools/check-vendored-libs-sync.py` passes.

### Phase 3 — CodeSpaces: non-destructive capture verb + liveness gate
- [ ] Add a CodeSpace-side liveness check (using the Phase 2 vendored lib,
      transport = the existing SSH `ConnectionManager`/`exec_with_retry`)
      that a new capture-only path calls before pulling, mirroring
      containers' "defer instead of capture mid-write" contract -- including
      the same corrected edge cases `ThomasMichon/copilot-extensions#3574`'s
      review caught: never disturb the CodeSpace's own state to get a
      liveness answer (containers' analog: never unpause a paused container
      just to capture it), and a single consistent deferral message/path
      for "not safely capturable right now" (containers' analog: the
      fleet-level check must delegate to the same per-member helper, not a
      second ad-hoc message).
- [ ] **This capture path must not simply call `sync_codespace_sessions()`
      with its existing defaults.** That function boots a `Shutdown`
      CodeSpace whenever `skip_if_shutdown` is false, and only special-cases
      the `Shutdown` state by name (`lifecycle._SHUTDOWN_STATE`) -- every
      other non-`Available` state (starting, provisioning, failed, or any
      future state) falls through to the same connect-and-maybe-boot path.
      A periodic non-destructive capture must never boot, connect to, or
      otherwise disturb a venue that isn't already `Available`/running: add
      an explicit preflight (list/inspect state only, no connection
      attempt) that defers immediately for any state other than the live,
      connected one -- matching containers' `rescue-capture`, which defers
      immediately on any non-`running` state rather than following
      `stop`/`rm`'s boot-tolerant or stopped-instance-evidence paths. Do not
      reuse `sync_codespace_sessions()`'s existing state handling as-is; it
      is destroy/finalize-shaped, not capture-only-shaped.
- [ ] Add a standalone, non-lifecycle-transition CLI verb (e.g.
      `agent-codespaces sync-sessions <name>`) that calls the gated capture
      without stopping/finalizing/deleting the CodeSpace and without ever
      booting it.
- [ ] Tests: CLI-dispatch coverage (text + `--json`, mirroring
      `test_rescue_capture_cli.py`'s shape), a liveness-gate regression test
      (mid-write session is deferred, not captured), a
      non-`Available`-state regression test (a `Shutdown`/`Starting`/
      unknown-state CodeSpace is deferred without a connection attempt),
      and lease-ownership tests covering both the owner and non-owner/
      no-lease cases per Phase 1's lease-ownership decision.

### Phase 4 — Periodic trigger for CodeSpaces
(scope set by Phase 1's scheduling-ownership decision)
- [ ] If Phase 1 decided **consumer-owned** (the containers-precedent
      default): this phase becomes documentation only -- record, in this
      repo's own docs (e.g. a short section in `agent-codespaces`'s README
      or the `codespaces-lifecycle` skill), how a consumer wires its own
      periodic trigger against the Phase 3 verb, mirroring how the
      containers-side downstream consumer did it. No new repository-owned
      scheduling code is written under this branch.
- [ ] If Phase 1 decided **repository-owned** (only viable if an existing
      always-running loop was confirmed to cover every relevant venue):
      wire the periodic capture into that already-existing loop; do not
      introduce a new standalone timer/daemon that duplicates a mechanism
      this repo already runs.
- [ ] Validate end-to-end against a real leased CodeSpace, using whichever
      trigger path Phase 1 chose: a capture picks up a real session,
      publishes it, and the CodeSpace's own state (lease, connection) is
      unaffected -- mirroring the container validation's proof that
      `docker ps` uptime was unaffected.

### Phase 5 — Close-out
- [ ] Confirm both providers' capture/publish result-shape fields are
      documented consistently (README/skill docs on both sides) so a
      consumer reading either doesn't need venue-specific tribal knowledge.
- [ ] Journal the final state; mark Status: Done once every Plan/Validation
      Plan item is resolved or transferred.

## Validation Plan

- [ ] Phase 2: `tools/check-vendored-libs-sync.py` passes with the new lib
      listed in both consumers; agent-containers' existing full test suite
      (`python tools/run-plugin-tests.py agent-containers`) still passes
      unchanged after the extraction.
- [ ] Phase 3: agent-codespaces' test suite
      (`python tools/run-plugin-tests.py agent-codespaces`) passes,
      including the new liveness-gate regression test, the
      non-`Available`-state regression test (no boot/connect attempt for a
      Shutdown/Starting/unknown-state CodeSpace), the lease-ownership
      owner/non-owner tests, and CLI-dispatch tests.
- [ ] Phase 4: a real leased CodeSpace is captured and published
      end-to-end (mirroring the container-side end-to-end validation
      already proven for `rescue-capture`) — published session readable
      from the same agent-logger hub tree the CodeSpace's own teardown-time
      capture already lands in, and the CodeSpace's lease/connection state
      unaffected before/after.
- [ ] Both providers' module-size guards (`tools/check-module-size.py`) and
      `ruff check` stay clean on every touched file.

## Proposal

_Pending._

## Journal

### 2026-09-25 — Kickoff
- Effort created directly following `ThomasMichon/copilot-extensions#3574`
  (agent-containers `rescue-capture`) landing and being deployed +
  validated end-to-end downstream. Operator asked whether the same
  architecture applies to `agent-codespaces`; comparison above
  (`sync_codespace_sessions()` already exists but is lifecycle-transition-
  only, exactly the gap containers just closed) confirmed it does, plus one
  real difference (codespaces currently has no liveness gate at all before
  pulling). Operator directed carving this effort to align the two
  behaviors and share what's genuinely common via the repo's existing
  vendored-lib mechanism.
