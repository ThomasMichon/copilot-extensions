# Phase 3b, Slice 2, Sub-slice 3 — Split the resident status-monitor's push/observe legs into Worktree Manager

- **Parent effort:** [`README.md`](README.md) § Phase 3b
- **Tracks:** [#2062](https://github.com/ThomasMichon/copilot-extensions/issues/2062)
- **Governing visions:** [`visions/session-hosting`](../../../visions/session-hosting/README.md)
  Concepts/*Session-host provider*, *Host-owned execution identity*,
  Behaviors/*host-owns-mechanics-agency-layer-owns-meaning*,
  Non-Goals/*Not a configuration mode of agent-worktrees*;
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
  Behaviors/*presentation-is-process-boundary-only* and
  Non-Goals/*Not a terminal or multiplexer owner*,
  *Not a home for a provider-specific config union*.
- **Scope of this doc:** the **resident** Mux status-bar path only: moving
  (a) Mux-liveness observation for Worktree-Manager-managed sessions and
  (b) Mux `set-option` status-bar writes out of `agent-worktrees` and into a
  new Worktree Manager companion daemon, while keeping `agent-worktrees`
  as the sole owner/accumulator of worktree status data.
- **Status:** In progress — Step 1 (of 6) landed 2026-09-25; Steps 2-6 not
  yet implemented.

## Why this needs its own ordered plan

Sub-slices 2a/2b could use a straightforward resolve/execute split because the
boundary was a one-shot action: agent-worktrees planned, Worktree Manager
executed. Sub-slice 3 is different. The operator's direction is explicitly a
**two-daemon**, **bidirectional** control-plane shape:

- `agent-worktrees` keeps the resident status-monitor daemon and remains the
  **sole authority** for worktree status data (git/PR/session-catalog state).
- `worktree-manager` gains its own **mux companion daemon** and becomes the
  **sole owner** of Worktree-Manager-managed worktree ⇄ mux-session/pane
  mapping.
- The daemons talk in both directions:
  1. **Worktree Manager → agent-worktrees:** "this worktree now has / no longer
     has a live mux pane."
  2. **agent-worktrees → Worktree Manager:** "apply this already-rendered
     status payload to the live mux session(s) for worktree X."

That is still the same ownership split the visions require — host-specific
mechanics move out, agency status meaning stays put — but it is a live IPC
channel, not just a new CLI verb. It therefore needs its own explicit contract,
cutover sequence, and validation plan before code.

## Current-state inventory (`main` as of 2026-09-17)

| File / lines | What lives there today |
|---|---|
| `plugins/agent-worktrees/src/agent_worktrees/classify_daemon.py:1-186` | Proven resident-daemon rendezvous precedent. `start_server()` (`51-62`), `rendezvous_fields()` (`65-75`), `classify_with_boot()` (`100-164`), and `classify_via_daemon()` (`167-186`) publish a dynamic loopback endpoint in the resident lock file and let a caller boot-wait against it. This is the closest existing daemon↔daemon IPC pattern in-tree. |
| `plugins/agent-worktrees/src/agent_worktrees/hook_ipc.py:1-89` | The concrete wire format precedent: loopback-only `ThreadingTCPServer`, token-authenticated single-line JSON request/response, dynamic port, rendezvous published by `HookIpcServer.rendezvous()` (`72-89`). |
| `plugins/agent-worktrees/src/agent_worktrees/__main__.py:8638-8715` (`_spawn_status_updater`) | Detached per-session updater spawn. Still launched for a live mux session and still shells into `agent_worktrees status-updater`; this is the legacy bar-writing fallback Sub-slice 3 must retire or confine to zero-provider mode. |
| `plugins/agent-worktrees/src/agent_worktrees/__main__.py:8718-8902` (`cmd_status_updater`) | The old per-session loop: writes `@aw_updater`, `@aw_updater_prefix`, `@aw_ctx`, and `@aw_seg` directly via `set-option` (`8800-8902`). It first tries to hand off to the resident monitor via `_register_session_for_monitor()` / `_ensure_status_monitor()` (`8751-8758`), then falls back to the per-session loop. |
| `plugins/agent-worktrees/src/agent_worktrees/__main__.py:8927-9437` | Resident monitor lifecycle helpers: `_status_monitor_enabled()` (`8927-8938`), `_monitor_lock_path()` (`8948-8950`), `_monitor_registry_dir()` (`8952-8953`), `_register_session_for_monitor()` (`9262-9279`), `_ensure_status_monitor()` (`9355-9376`), `_restart_status_monitor()` (`9379-9437`). These establish the existing host-wide singleton and the `status-monitor.d/<sess>` registry. |
| `plugins/agent-worktrees/src/agent_worktrees/__main__.py:9485-9530` | The resident monitor's direct Mux mechanics today: `_monitor_mux_set()` (`9485-9496`) calls `set-option` itself, and `_monitor_list_sessions()` (`9499-9530`) directly enumerates mux sessions with attached counts and an incarnation token. |
| `plugins/agent-worktrees/src/agent_worktrees/__main__.py:10098-10297` (`_monitor_sweep`) | The current coalescing sweep. It directly enumerates live mux sessions, prunes stale registry entries, feeds `session_catalog.observe_mux()`, notifies `pane_reaper.observe()`, and publishes `@aw_updater` / `@aw_updater_prefix` / `@aw_ctx` / `@aw_seg` itself (`10124-10238`). This is the heart of the push/observe leg that moves. |
| `plugins/agent-worktrees/src/agent_worktrees/__main__.py:11288-11535` (`cmd_status_monitor`) | The resident daemon loop: owns the lock, starts the hook/classify side servers, and on every iteration calls `_monitor_sweep(...)` with `catalog_observer=reconciler.observe_mux` and `pane_observer=pane_reconciler.observe` (`11468-11479`). |
| `plugins/agent-worktrees/src/agent_worktrees/session_catalog.py:107-162, 500-533` | `ResidentSessionReconciler.observe_mux()` (`148-151`) and `has_live_worktree_mux` / `has_mux_observation` (`154-162`) consume the monitor's live mux snapshot. Record stamping also re-registers live mux sessions for service via `tracking.stamp_mux_live(...); register_monitor_session(...)` (`523-532`). |
| `plugins/agent-worktrees/src/agent_worktrees/pane_reaper.py:222-300` | `ResidentPaneReconciler.observe()` (`246-248`) depends on the monitor seeing live mux sessions and paths, then later inspects panes in `step()`. Manager-owned live-pane observations must keep this reconciler fed without making agent-worktrees re-own mux mapping. |
| `plugins/agent-worktrees/src/agent_worktrees/monitor_roots.py:19-98` and `__main__.py:5781-5796` | The non-session liveness-root pattern. `PickerHeartbeat` keeps the resident monitor alive while a Picker is open, and `_start_picker_monitor_root()` in `agent-worktrees` (called by both Picker implementations) is the current root-registration seam. |
| `plugins/agent-worktrees/src/agent_worktrees/sessions.py:1189-1452, 1669-1708, 2308-...` | The liveness helpers that **stay** in `agent-worktrees`: `LiveVerdict` / `verify_worktree_active()` (`1189-1272`), `mux_session_name()` (`1275-1302`), `has_mux_session()` (`1332-1352`), `_list_mux_sessions()` / `mux_status_many()` (`1356-1452`), `current_mux_session()` / `has_mux_session_named()` (`1669-1708`), and the broader session-binding helpers such as `mux_binding_for_session()` (`2308+`). Sub-slice 3 only moves the resident daemon's push/observe leg, not these generic read-side probes. |
| `worktree-manager/bin/launch-session.ps1:1342-1450,1741` and `worktree-manager/bin/launch-session.sh:947-968` | Worktree Manager's relocated launcher scripts still spawn the legacy `status-updater` directly on create/join. That means Manager-managed sessions still currently rely on `agent-worktrees`' per-session or resident writer path. |
| `worktree-manager/bin/session-options.ps1:42-56` and `worktree-manager/bin/session-options.sh:40-48` | The mux status bar itself still reads `#{@aw_ctx}` and `#{@aw_seg}`. Sub-slice 3 moves **who writes those options**, not the option names or bar template. |
| `worktree-manager/src/worktree_manager/production_picker/runner.py:62-83` | No Manager-owned resident daemon exists yet. The production Picker still starts `agent-worktrees`' `_start_picker_monitor_root()` (`79`) and background housekeeping threads only; this slice is therefore greenfield on the Manager side. |
| `worktree-manager/src/worktree_manager/mux_companion.py:1-302` | Existing Manager-side mux work is read-only UI (`Mux Companion`), not a daemon or mux-mapping owner. Useful evidence that mux-facing UX already lives here, but it does not provide the resident scaffold this slice needs. |

## Target end-state

### 1. Two resident daemons, with cleanly separated authority

#### `agent-worktrees` status-monitor (unchanged owner)

The existing host-wide resident `status-monitor` daemon remains:

- the **sole accumulator** of worktree status data;
- the only place that renders the `@aw_ctx` / `@aw_seg` payloads from
  worktree state;
- the owner of record-stamping, session-catalog reconciliation, and generic
  lifecycle/handoff governance.

What changes is only its **egress** (no more direct `set-option` for
Manager-owned sessions) and its **provider observation source** (no more direct
whole-mux observation for Manager-owned sessions).

#### `worktree-manager` mux companion daemon (new owner)

`worktree-manager` gains one new host-wide resident daemon, one instance per
machine / installation cell (not per project), because mux session/pane mapping
and status-bar writes are host-global just like the existing `status-monitor`.

It owns:

- the authoritative mapping for **Worktree-Manager-managed**
  `worktree_id ⇄ mux session / pane(s)` relationships;
- creation-time and teardown-time mux registration for those sessions;
- bounded validation of those mappings against the real mux server;
- all `set-option` writes for those mapped sessions.

Lifecycle:

- **Start:** lazily ensured by Worktree Manager the first time a Manager-owned
  mux launch / join / restore / remux action needs it, and also by the
  production Picker before opening a mux-managing UI lane.
- **Keep-alive:** while it has at least one live mapping or an active Manager
  client that may create one shortly.
- **Idle-exit:** after a short linger with no live mappings and no active
  demand, mirroring the resident-monitor pattern.
- **Update/install cutover:** the daemon publishes its runtime prefix/generation
  in its own lock file and self-retires when superseded, exactly the same
  single-current-runtime rule the resident monitor already follows. A new
  Manager action or explicit restart command re-ensures the current runtime.

This keeps the same host-level singleton discipline on both sides and avoids a
third long-lived writer hidden inside launch scripts.

### 2. IPC transport: reuse the existing lockfile-rendezvous + loopback-JSON pattern

#### Recommendation

Use the **same transport shape already proven by `hook_ipc.py` and
`classify_daemon.py`**:

- each daemon owns a **loopback-only**, dynamic-port TCP server;
- each server advertises its endpoint/token/generation in its existing
  lock/rendezvous file;
- requests are **single-line JSON**, token-authenticated, deadline-bounded,
  and receive a small JSON acknowledgement.

This is the recommended transport over named pipes or a file-queue because:

1. it already works cross-platform in this repo on Windows and POSIX;
2. it already has a lockfile rendezvous convention the status-monitor uses;
3. this traffic is **latest-wins, low-volume control-plane traffic**, not an
   append-only durable log:
   - status pushes are periodic and naturally self-healing on the next sweep;
   - live-pane notifications are idempotent edge updates that can be retried;
4. it avoids inventing a second Windows-only vs POSIX-only IPC stack for a
   slice whose value is ownership separation, not transport novelty.

#### Concrete shape

- `agent-worktrees status-monitor.lock` publishes one additional endpoint for
  **manager-originated mux observation events**, alongside the existing hook and
  classify rendezvous.
- `worktree-manager` publishes a new `mux-daemon.lock` for its own **status-sink
  endpoint** and any daemon metadata (`prefix`, `generation`, mapping-registry
  root, last-seen stamp).

The request envelope mirrors `HookIpcServer`:

```json
{
  "version": 1,
  "token": "<shared-secret-from-lockfile>",
  "kind": "<message-kind>",
  "deadline": 1760000000.123,
  "payload": { "...": "..." }
}
```

Response:

```json
{
  "version": 1,
  "ok": true,
  "result": { "...": "..." }
}
```

If the callee is missing:

- **Worktree Manager may boot-wait `agent-worktrees`** using the same
  `classify_with_boot` pattern, because the status authority must exist before
  Manager publishes first observation for a managed session.
- **`agent-worktrees` does not become responsible for booting Worktree
  Manager.** If a Manager-owned mapping exists but the Manager daemon endpoint is
  absent, the resident monitor logs and retries on the next sweep rather than
  reclaiming the mux writer role. That preserves the ownership boundary and
  avoids reintroducing a hidden dual path.

### 3. Message contracts

#### Worktree Manager → agent-worktrees: live-pane observation

Message kind: `mux-live-v1`

Purpose: tell the `agent-worktrees` status authority that a
Worktree-Manager-managed worktree now has, changed, or lost its live mux
embodiment.

Payload:

```json
{
  "project": "<project-name>",
  "worktree_id": "<id>",
  "worktree_path": "D:\\Src\\...\\worktree",
  "mux_session": "wt-<id>",
  "session_incarnation": "<session_id>:<created>",
  "panes": [
    {
      "pane_id": "%12",
      "role": "head",
      "live": true
    }
  ],
  "attached_clients": 1,
  "live": true,
  "mapping_revision": 7,
  "observed_at": "2026-09-17T08:00:00Z"
}
```

Rules:

- `live=true` is an upsert/update; `live=false` is a removal/tombstone.
- `mapping_revision` is monotonically increasing per worktree mapping so
  out-of-order events can be ignored.
- `worktree_path` is included because the resident monitor currently renders by
  path and feeds `pane_reaper.observe(session_name, path)`; Manager already
  knows the launch path and should supply it rather than making
  `agent-worktrees` rediscover provider-owned mapping.
- `panes` is a list, not a scalar, because live cutover can legitimately create
  multiple panes in one session before the predecessor retires.

`agent-worktrees` records this in a monitor-owned **managed mux cache** (memory
plus a small best-effort runtime snapshot for daemon restart recovery). That
cache becomes the observe input for Manager-owned sessions when feeding:

- the monitor's served-session set,
- `session_catalog.observe_mux()` / mux-live stamps,
- `pane_reaper.observe()`,
- any daemon-only handoff/pane-retirement sweep that must know whether a live
  Manager-owned mux embodiment exists.

#### `agent-worktrees` → Worktree Manager: apply rendered status

Message kind: `mux-status-v1`

Purpose: carry the already-rendered status-bar values to the Manager-owned mux
daemon, which then applies them to the mapped session(s).

Payload:

```json
{
  "project": "<project-name>",
  "worktree_id": "<id>",
  "values": {
    "@aw_updater": "12345",
    "@aw_updater_prefix": "C:\\Users\\...\\Python",
    "@aw_ctx": "tmichon-book2 | repo:1c4f ",
    "@aw_seg": "WIP ..."
  },
  "rendered_at": "2026-09-17T08:00:15Z",
  "monitor_generation": "<status-monitor-generation>"
}
```

Rules:

- The payload is deliberately **option-oriented**, not a new typed status
  schema. `agent-worktrees` already knows how to render the options; Worktree
  Manager only needs to apply them.
- Worktree Manager resolves the mux session/pane target from **its own mapping
  source of truth**, not from caller-supplied pane ids.
- The daemon may coalesce/rewrite multiple `mux-status-v1` updates, but it must
  preserve last-write-wins semantics per worktree.
- If no live mapping currently exists for the worktree, the daemon simply
  acknowledges `{applied:false, reason:"not-live"}`; it does not invent or
  resurrect one.

### 4. Ownership boundary: what changes hands, what stays put

#### Moves to Worktree Manager

- resident observation of live mux session/pane presence for
  **Manager-owned sessions only**;
- the canonical `worktree_id ⇄ mux session / pane(s)` mapping for those
  sessions;
- all `set-option` calls that paint `@aw_updater`, `@aw_updater_prefix`,
  `@aw_ctx`, and `@aw_seg` into those sessions;
- the manager-owned mapping registry/runtime state that lets the daemon recover
  across restart/update.

#### Stays in agent-worktrees

- all worktree status computation and rendering;
- record stamping, PR/git/session-catalog aggregation, handoff governance;
- `sessions.py` read-side liveness helpers used by direct commands and the
  zero-provider fallback;
- direct mux observation and direct status-updater fallback for **unmanaged**
  sessions where Worktree Manager is absent or deliberately not in the path.

This is the same authority split as Sub-slices 2a/2b, but expressed as
**accumulate/render vs host-execute/observe**, not as a new query verb.

### 5. Status-updater retirement / reconciliation

Sub-slice 3 must not leave three parallel writers (`status-updater`,
`status-monitor`, and a new Manager daemon) active for the same session.

Target state:

- **Manager-owned launches do not spawn the per-session updater loop at all.**
  The launcher registers/ensures the mux daemon instead.
- `cmd_register_session` / `bind-session` stop using `status-updater` as an
  implicit registration shim for Manager-owned sessions. They directly register
  the session with the resident monitor (or call a tiny no-loop helper) and let
  the resident monitor talk to Worktree Manager.
- `status-updater` remains only as the **zero-provider / monitor-disabled**
  fallback:
  - when the resident monitor is disabled;
  - or when a live mux session is not Manager-owned and no Manager daemon is in
    the path.

That keeps the a-la-carte fallback the existing design depends on, without
leaving Worktree Manager-managed sessions half-owned by three different writers.

## Ordered implementation steps

Each step lands as its own PR. The sequence deliberately keeps earlier steps
**additive/off-path**, then performs one clear cutover for Manager-owned
sessions, then deletes the redundant path. No step should leave a session in a
state where two long-lived writers are both intended to own it.

1. [x] **Add the daemon-link contract and resident cache seam to
       `agent-worktrees`, additive only.** Landed: `mux_link.py` (rendezvous-
       parseable ``managed_mux_*`` fields in `status-monitor.lock`, mirroring
       `hook_ipc`/`classify_daemon`/`worktree_status_daemon`), a thread-safe
       `ManagedMuxCache` (monotonic `mapping_revision` guard rejects a
       stale/out-of-order observation), and `InProcessRuntime` wired into
       `cmd_status_monitor` (lock-extra publication, idle-strike
       `has_active_demand()`, shutdown). `_monitor_sweep` merges the cache's
       currently-live session names into its existing `catalog_observer` call
       only -- never into `served`, never triggering a `set-option` write of
       its own. Client-side `mux_live_via_daemon`/`mux_live_with_boot` push
       helpers are pinned but not yet called by anything (Step 2's Worktree
       Manager mux-companion daemon is the first real caller). No behavior
       change to ordinary sessions: the cache starts (and stays) empty until
       a Manager daemon exists to push into it.
   - Add a dedicated manager-observation IPC surface to `status-monitor.lock`
     (same loopback/token envelope as `HookIpcServer`).
   - Add a monitor-owned managed-mux cache abstraction that can store Manager
     live-session observations (`worktree_id`, path, session, panes,
     incarnation, attached clients, mapping revision).
   - Teach the resident monitor internals to consume that cache **in parallel
     with** the current direct mux snapshot, but do not change the launch path
     or writer ownership yet.
   - No behavior change to ordinary sessions; this step only creates the seam.

2. [ ] **Add the Worktree Manager mux companion daemon and its own runtime
       registry, still off the main launch path.**
   - Add a host-wide Manager daemon with its own lock file and status-sink
     endpoint.
   - Add a Manager-owned per-worktree mapping registry and recovery-on-restart
     read path.
   - Add internal commands/helpers for "ensure daemon", "register/update
     mapping", and "remove mapping", but do not yet cut normal launches over.
   - This step proves lifecycle/install/update behavior without changing who
     writes the bar in production.

3. [ ] **Perform the managed-session cutover atomically: launch path,
       observe path, and push path together.**
   - Worktree Manager's mux launch/join/restore/remux actions start/update the
     mapping registry and ensure the companion daemon.
   - Those actions immediately publish `mux-live-v1` observation events to
     `agent-worktrees`.
   - `agent-worktrees status-monitor` begins routing `mux-status-v1` updates to
     Worktree Manager **for mappings present in the managed-mux cache** instead
     of calling `_monitor_mux_set()` directly.
   - Worktree Manager launch scripts stop spawning `status-updater` for those
     Manager-owned sessions.
   - Unmanaged sessions remain on the old resident/direct path.

4. [ ] **Cut the resident monitor's serve/reconcile loop over to the new
       Manager-fed observation source for Manager-owned sessions.**
   - Refactor `_monitor_sweep()` so its served-session set is a union of:
     - unmanaged sessions from the legacy `status-monitor.d` + direct mux scan;
     - Manager-owned sessions from the managed-mux cache.
   - Feed `session_catalog.observe_mux()`, `tracking.stamp_mux_live(...)`, and
     `pane_reaper.observe()` from that union instead of assuming every served
     session came from `_monitor_list_sessions()`.
   - Preserve direct `_monitor_list_sessions()` only for the unmanaged /
     zero-provider lane and for any daemon logic still explicitly scoped to that
     lane.

5. [ ] **Retire the per-session updater as a registration shim for
       Manager-owned sessions; keep it only as the zero-provider fallback.**
   - Replace unconditional `_spawn_status_updater(...)` reseeds from
     `register-session` / `bind-session` with a direct monitor-registration path
     for Manager-owned sessions.
   - Keep `status-updater` reachable when the resident monitor is disabled or
     when the session is not Manager-owned.
   - This is the step that removes the last accidental "third path."

6. [ ] **Delete redundant manager-owned direct mux writes/assumptions from
       `agent-worktrees`, then clean up docs/tests.**
   - Remove any remaining `status-monitor` branch that still calls
     `_monitor_mux_set()` for Manager-owned sessions.
   - Restrict `status-monitor.d/<sess>` and direct monitor-side session pruning
     to unmanaged sessions.
   - Update the Phase 3b docs to state plainly that Manager-owned mux mapping
     and status-bar writes are now fully outside `agent-worktrees`, while its
     data authority remains unchanged.

This follows the same discipline as the AHP relocation plan: additive seam
first, one crisp ownership cutover, then cleanup/deletion — never an indefinite
"both are canonical" state.

## Validation

### Contract-level validation

- A daemon-link transport test proving both sides reject a bad/missing token and
  honor bounded deadlines.
- A boot-wait test proving Worktree Manager can ensure the resident
  `status-monitor`, wait for its observation endpoint to appear, then deliver a
  `mux-live-v1` request without racing startup.
- An out-of-order observation test proving `mapping_revision` prevents an older
  `live=false` or stale-pane event from clobbering a newer live mapping.
- A status-sink test proving Worktree Manager applies the **caller's values**
  to the mapped session, never recomputes them itself, and returns
  `{applied:false}` when no live mapping exists.

### Step-specific validation

1. **Step 1**
   - resident monitor lock file publishes the new observation endpoint;
   - managed-mux cache survives one daemon restart via its runtime snapshot;
   - no existing `status-monitor` / `status-updater` tests regress.
2. **Step 2**
   - Manager daemon singleton/idle-exit/superseded-runtime behavior mirrors the
     existing monitor expectations;
   - a stored mapping is recovered on daemon restart and revalidated against the
     real mux before being treated as live.
3. **Step 3**
   - a Manager-launched mux session paints a live bar with **no**
     `status-updater` loop left behind;
   - a legacy/unmanaged launch still paints via the old path unchanged;
   - a Manager-owned session create/destroy immediately produces the matching
     `mux-live-v1` upsert/removal at `agent-worktrees`.
4. **Step 4**
   - `session_catalog` and `pane_reaper` continue to see Manager-owned live mux
     sessions even though `_monitor_list_sessions()` is no longer their source
     for that lane;
   - direct `sessions.py` liveness helpers still behave unchanged for
     user-facing commands.
5. **Step 5**
   - `register-session` / `bind-session` in a Manager-owned session no longer
     spawn the updater loop;
   - monitor-disabled mode (`AGENT_WORKTREES_STATUS_MONITOR=0`) still falls back
     to the per-session updater for unmanaged sessions.
6. **Step 6**
   - grep-proof: no manager-owned resident `set-option` path remains in
     `agent-worktrees`;
   - docs/tests describe exactly one canonical writer per lane.

### End-to-end scenarios that must pass before the final cleanup PR lands

- **Manager-owned launch:** create a mux session through Worktree Manager, see a
  live bar, watch status updates flow, tear the session down, and confirm
  `agent-worktrees` records it as no longer live.
- **Manager daemon restart:** with a live Manager-owned session already open,
  restart/update the Manager daemon and confirm the mapping recovers and bar
  updates resume without starting a per-session updater.
- **Resident monitor restart:** with a live Manager-owned session already open,
  restart the `status-monitor` and confirm Worktree Manager can republish live
  mapping plus resume status updates without relaunching the session.
- **Zero-provider fallback:** create/observe a non-Manager-owned session and
  confirm the existing direct/status-updater path still works.

## Non-Goals of this slice

- **Not moving status-data authority.** Git disposition, PR state, session
  catalog data, handoff state, and status rendering remain in
  `agent-worktrees`.
- **Not deleting `sessions.py` liveness helpers.** `has_mux_session`,
  `verify_worktree_active`, `mux_binding_for_session`, and related direct read
  paths remain valid tools of the durable agency layer.
- **Not redesigning the status bar vocabulary.** `@aw_ctx`, `@aw_seg`,
  `@aw_updater`, and `@aw_updater_prefix` remain the payload and bar-template
  surface; only the writer ownership moves.
- **Not a general process-manager abstraction.** This slice is about the Mux
  provider path Worktree Manager already owns, not arbitrary future providers.
- **Not AHP work.** AHP ownership already moved in Slice 1; this slice only
  handles the resident Mux observe/push leg.
- **Not bundled-Picker retirement.** The broader Picker parity/removal work
  stays in Phase 3/6's separate plan.
