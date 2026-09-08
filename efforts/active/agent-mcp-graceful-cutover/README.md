# agent-mcp — Zero-Downtime Serve Cutover

- **Slug:** `agent-mcp-graceful-cutover`
- **Repo:** copilot-extensions
- **Created:** 2026-09-07
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** closes [`visions/plugin-services`](../../../visions/plugin-services/README.md)
  §*zero-downtime-cutover* — **vision-closing**: the vision already states every
  running service must be zero-downtime-replaceable; `agent-mcp serve` did not
  yet do this. No new intent to write; implement the already-stated behavior.
- **Pattern:** [`docs/patterns/graceful-daemon-cutover.md`](../../../docs/patterns/graceful-daemon-cutover.md)
  — the shared `zdd` cutover primitive (`libs/zdd/`) this effort adopts.

## Guiding Intent

`agent-mcp materialize`/`call` and the multiplexed `bridge`/`forward` attach
path all resolve the resident `serve` daemon through a **fixed, client-facing
handle** (an AF_UNIX socket path on POSIX, a `<handle>.endpoint` sidecar on
Windows). Before this effort, replacing that daemon with a new version meant
stopping the old one and starting the new one — a window, however brief, where
the fixed handle resolves to nothing and any call/attach in that window has no
live instance to reach. (Concretely: `agent-mcp materialize`'s own hardening
against a *different* hang — aperture-labs #6601 / copilot-extensions#2184 —
established that a fresh materialize can be slow; a version cutover happening
at the same moment used to make that window a real "no instance answers"
outage, not just a slow one.)

Make replacing the daemon a **cutover** instead, per the already-adopted shape
in `docs/patterns/graceful-daemon-cutover.md`: stand the new version up beside
the old one, health-gate it, flip the fixed handle to it, drain the old one,
retire it — so the fixed handle always resolves to a live daemon.

## Design

Two design calls, made explicitly (not defaults):

1. **Routing/wire-shape: Option A (control-plane split), not extending
   `zdd.routing.Endpoint`.** `zdd`'s routing table models an endpoint as
   `bind:port` (HTTP-shaped); agent-mcp's *data-plane* transport is AF_UNIX on
   POSIX. Rather than extend the shared `zdd.routing.Endpoint` with a `path`
   field (touching every consumer's shared primitive), `agent-mcp serve` binds
   a small, always-on, loopback-TCP-only **control-plane** listener purely for
   lifecycle ops (`ping`/`drain`/`undrain`/`shutdown`), published to
   `zdd.routing`'s table unmodified. The **data plane** (real MCP traffic)
   never touches `zdd`; it keeps using agent-mcp's own existing fixed handle
   discovery (`agent_mcp.sockio`/`agent_mcp.ipc`, unchanged), which the cutover
   flips directly (a symlink swap on POSIX, an atomic endpoint-sidecar
   rewrite on Windows) — so **every existing data client needed zero code
   changes** to follow a cutover.
2. **Generation self-retire: deferred, not included in v1.** The pattern doc
   marks this optional/opt-in even for its reference implementation
   (agent-bridge). v1 relies on orchestrator-driven retire only (an explicit
   `agent-mcp cutover` invocation, not yet automatic on every version bump —
   see Phase 2 below).

**Drain semantics** (the safe-cutover-point definition this effort had to
invent, since agent-mcp serves two very different request shapes):
- One-shot `call`/`materialize`/`list` — short-lived; unaffected by drain,
  finishes normally (matches agent-vault's "finish the in-flight request").
- Attached multiplexer sessions (`bridge`/`forward`) — potentially
  hours-long, one per live Copilot session. Drain **refuses new `attach`
  requests** (the client's existing "live host refuses attach → direct
  in-process bridge, no respawn" fallback already covers this correctly) but
  lets already-attached sessions ride out to their natural close on the old
  generation. The old generation retires once its attached-session count
  reaches 0 (reusing the daemon's existing idle-refcount machinery almost
  as-is).

## Plan

### Phase 1 — Core cutover mechanism (this PR)
- [x] Vendor `zdd` into `plugins/agent-mcp/libs/zdd/` (byte-identical, matching
      agent-bridge's copy).
- [x] `Server` (`agent_mcp/serve.py`): `--passive` mode (skips the home-wide
      single-instance lease entirely — a passive instance binds its own
      distinct `socket_path`, so it never contends for it); the always-on
      control-plane listener (ping/drain/undrain/shutdown), published to
      `zdd.routing`; a pid-keyed control-token sidecar (`serve-control-
      <pid>.token`) so an orchestrator can authenticate to a daemon it did not
      spawn without a single shared file colliding across cutover attempts;
      `flip_data_handle()` (the POSIX symlink swap / Windows sidecar rewrite).
- [x] `agent_mcp/cutover.py`: the `zdd.cutover.CutoverOrchestrator` wiring
      (`spawn_passive`/`health_check`/`make_client`/`pick_free_port`), a
      `CutoverClient` implementing the (structural, non-HTTP) `_Client`
      protocol over agent-mcp's own control-channel line-JSON ops, and
      `run_cutover()`.
- [x] `agent-mcp cutover` CLI subcommand (manual trigger for v1 — see Phase 2).
- [x] Tests: control-op dispatch, passive lease bypass, pid-keyed token
      collision regression, `flip_data_handle` (POSIX + Windows), and one full
      subprocess end-to-end test (spawn a real old daemon, cut over, confirm
      it actually exits and a plain data client reaches the new generation
      through the unchanged fixed handle).
- [x] Add agent-mcp to `docs/patterns/graceful-daemon-cutover.md`'s adoption
      table.

### Phase 2 — Installer-driven automatic cutover (this PR)
Per the pattern doc's binding invariant 1 ("no externally-driven `deploy`
command"), the end state is that agent-mcp's own runtime update/activation path
detects a live `serve` daemon and invokes `cutover` in-process automatically —
`agent-mcp cutover` should become an internal seam, not something an operator
or the facility's `<repo> update` flow has to remember to call.

agent-mcp has no `install.ps1`/`install.sh` matching agent-bridge/agent-dispatch's
naming convention (its installer is named `init.ps1`/`init.sh`) -- but it
already had everything else that convention implies: versioned runtime slots,
an active-version marker, a deploy manifest, and (after Phase 1) the `zdd`
cutover primitive itself. It also already had a **reap step** (`reap_versions.py`)
that runs on every activation and hard-kills any process (including a live
`serve` daemon) still running from a stale slot -- so the real gap was not
"no update path exists", it was "the existing update path's only response to
a live stale-version `serve` daemon was to kill it outright, with no graceful
handoff first".

- [x] `run_cutover()` gained a `require_live_daemon: bool` kwarg (and the CLI a
      matching `--require-live` flag): with it set, a call that would otherwise
      take the "cold start" path (no live daemon at all) or would cut over a
      daemon already on the exact target version instead returns
      `{"ok": True, "skipped": "<reason>"}` without spawning anything. This is
      what makes it safe to invoke unconditionally from an installer/reconcile
      pass -- `serve` stays optional, on-demand warmth, never auto-promoted to
      an always-running daemon by an install/update pass that happens to run
      while nothing was serving.
- [x] `init.ps1`/`init.sh`: added a step, right before the existing
      `reap_versions.py` call, that invokes
      `<new-slot-python> -m agent_mcp cutover --require-live --force --json`.
      A live daemon on a genuinely different (or pre-feature) version still
      gets a real graceful cutover (or the existing bootstrap-boundary error);
      the unchanged reap step immediately after remains the safety net for
      anything the cutover step didn't handle (a failed cutover, a pre-feature
      daemon, or a leaked process that was never a `serve` daemon at all).
      Best-effort (never fails the install); opt out with `AGENT_MCP_NO_CUTOVER`.
      Uses short, install-appropriate health/drain timeouts (15s/30s, both
      overridable via `AGENT_MCP_CUTOVER_HEALTH_TIMEOUT`/
      `AGENT_MCP_CUTOVER_DRAIN_TIMEOUT`) rather than the CLI's own
      manual-operator defaults (60s/300s) -- an unattended activation pass
      must not silently block for minutes on a lightly-used bridge.
- [x] Tests: `require_live_daemon` skip-on-cold-start, skip-on-already-current-
      version, and still-reports-genuine-mismatch (the pre-feature-daemon
      case) added to `test_cutover.py`.
- Note: `plugin.json`'s `"zeroDowntimeUpdate": true` flag (set on
  agent-bridge/agent-dispatch/agent-index) is **not** applicable here yet --
  the launch-time reconciler (`agent_worktrees.reconcile`) only ever passes
  `-ZeroDowntime` to a script named `install.ps1`; a plugin with only
  `init.ps1` never receives it regardless of what the manifest declares, so
  setting the flag on agent-mcp today would be inert/misleading. Revisit if
  agent-mcp's installer is ever renamed to converge with that naming
  convention.

### Phase 3 — Generation self-retire backstop (follow-up)
The pattern doc's "generation self-retire" watchdog (a demoted daemon notices
on its own that a confirmed live successor has superseded it and exits, for
the case where the orchestrator itself dies mid-cutover) is explicitly out of
scope for v1, matching how the pattern doc treats it as optional even for the
reference implementation. Today, an orchestrator that crashes after the flip
but before retiring the old daemon leaves it running (harmless — it's demoted,
serving no new attaches once told to drain, but not self-terminating). Track
before relying on unattended repeated cutovers in production.

## Bootstrap boundary (accepted, not a bug)

The **first** cutover ever run against a daemon that predates this feature has
no control channel to dial — `agent-mcp cutover` detects this and reports a
clear error rather than attempting a doomed drain. That one daemon needs a
plain stop/start once; every cutover after that (both sides now control-
capable) works gracefully. Inherent to any such migration.
