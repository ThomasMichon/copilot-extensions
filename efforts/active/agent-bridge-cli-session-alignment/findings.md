# Findings — observable agent-bridge CLI sessions

Linked from [`README.md`](README.md)'s Plan/Proposal. Read this when working
Phase 1/2 of that effort; it is the detailed evidence the Proposal
summarizes.

Each finding cites exact files/lines from four bounded, independent
evidence passes (agent-bridge core + Picker UI; agent-codespaces;
agent-containers; cross-machine SSH + elevated bridging), reviewed against
the invariants named in the effort's Guiding Intent. Findings are organized
by invariant, not by contributor or PR, per this effort's product-focused
framing.

## 1. CLI-mode transport parity ("all should support CLI mode")

| Transport | Supports CLI-mode sessions today? | Evidence |
|---|---|---|
| `agent-containers` | Yes | Shares the Session Host dispatch primitive (`ContainerTransport` → `build_container_spawner()` → the same `CodeSpaceSpawner` class); `session-host-prepare`/`-state`/`-cleanup` verbs present (`plugins/agent-containers/src/agent_containers/__main__.py:202-224, 363-368`). |
| `agent-codespaces` | Yes | Native `copilot --detach --forward`, Connection Owner, model-launch parity (`plugins/agent-codespaces/src/agent_codespaces/copilot_detach.py`). |
| Cross-machine (SSH mesh) | Yes | The mesh `SshSpawner` is confirmed to be the *same* `CodeSpaceSpawner` class with an ssh-manager-backed transport, not a parallel implementation (`plugins/agent-bridge/src/agent_bridge/session_host/spawner.py:348-386`). |
| **Elevated bridging (Windows S4U/scheduled-task/WMI broker)** | **No** | `plugins/agent-bridge/src/agent_bridge/session_targeting_cli.py:732` explicitly scopes `--cli` to `codespace:<name>` and `container:<name>` targets only and rejects a bare/elevated target. `elevated.py` implements an isolated ACP-relay daemon lifecycle (`relay_spawn_command`, `relay_agent_for` at `:233-296`) with no Session Host launch, CLI reservation claim, or muxed CLI process anywhere in that path. |

**Finding:** elevated bridging is the one transport genuinely left out of
the CLI-mode session work — not a partial or inconsistent implementation,
but no wiring at all. This directly answers the operator's "ensure...
elevated bridging aren't left out" concern: it was.

## 2. Dynamic port reservation

- **Significant — hardcoded fallback ports in the CLI extension.**
  `plugins/agent-bridge/extensions/agent-bridge/extension.mjs:resolveBaseUrl`
  (lines 111-130) correctly reads the daemon's discovered port from
  `active.json` first, but falls back to **hardcoded** ports `9281`/`9280`
  when discovery files are absent. This conflicts with the documented
  contract that the daemon binds an OS-assigned ephemeral port and
  publishes it for clients (`plugins/agent-bridge/docs/architecture.md:5-6,
  448-460`). A fallback dial can hit an unrelated/stale listener or simply
  fail once a daemon starts on a different assigned port.

- **Significant — `agent-codespaces --forward` uses a caller-supplied fixed
  host port, not a daemon-reserved one.**
  `plugins/agent-codespaces/src/agent_codespaces/copilot_detach.py:177-197`
  (`parse_local_forwards`) takes the host port directly from the caller and
  stores it verbatim; `connection_owner.py:93-111` (`OwnerHold`) and
  `session_forwards.py:155-173` (`_reconcile_local`) both operate on that
  fixed value. The detached session's own CLI-mode scope *is*
  server-reserved (`copilot_detach.py:322-331`), but that reservation
  doesn't extend to the host-side forwarded ports, which can collide with
  an unrelated listener or a concurrent session. Documented and tested as
  "fixed host" behavior, so this is a deliberate design choice, not an
  oversight — but it's the one place the reviewed surface departs from the
  daemon-owns-allocation model.

- **Clean.** The core Session Host launch path (`session_host/spawner.py:
  327-344`) launches with `--port 0` and discovers the live port via
  `active.json` rather than a fixed value; the elevated daemon does the
  same (`elevated.py:58-67`, with a legacy `ELEVATED_PORT` kept only as a
  compatibility fallback, not a live default). `agent-containers`' detached
  path resolves the daemon port dynamically (`forward_keeper.py:109-139`,
  `resolve_daemon_port()`) rather than embedding one. The CLI-mode
  reservation route itself enforces one active reservation per
  `worktree_id` server-side (`routes/live_sessions.py:265-289`) with
  compare-and-delete release semantics (`:310-319`).

## 3. Process hygiene

- **Worth fixing — a failed CLI-mode launch leaves its reservation stale.**
  `plugins/agent-bridge/src/agent_bridge/inventory_cli.py:
  _launch_cli_mode_session` (lines 148-183) reserves the worktree-scoped
  CLI-mode slot *before* invoking `agent-worktrees embody`, but has no
  cleanup path if the launch raises, exits nonzero, or otherwise fails to
  produce a session. The reservation then sits until its TTL expires,
  blocking a subsequent launch attempt for that same worktree despite no
  real session existing.

- **Worth fixing — a container's local-forward keeper is tracked by
  container name, not by session/CWD identity.**
  `plugins/agent-containers/src/agent_containers/forward_keeper.py:
  ensure_running()` keys existing keeper state only by container `name`
  and replaces it whenever the mux/venue port differs. Because the scope
  construction issue below (§4) already makes container-hosted CLI-mode
  identity container-scoped rather than CWD-scoped, this compounds: one
  detached session on a container can silently replace another live
  session's forwarding process on the same container.

- **Clean elsewhere.** The CLI extension's own recurring work (heartbeat,
  flush, inbox poll) is timer-based, `unref()`'d, and cleared on shutdown
  (`extension.mjs:456-483`) — no held-open connection in the extension-host
  process. `agent-codespaces`' forward reconciliation removes stale/changed
  channels and its `shutdown()` stops daemon/reverse/local channels
  (`session_forwards.py:136-190, 257-267`); failed detached launches kill
  the newly-created tmux session and release the Owner hold
  (`copilot_detach.py:585-680`). The Session Host abstraction retains and
  closes its forward/relay handles symmetrically (`spawner.py:91-145`), and
  the elevated daemon has its own matched start/stop lifecycle
  (`elevated.py:180-230, 296-306`) — sound for what it covers, though it
  covers only the elevated ACP daemon, not a CLI-mode child (see §1).

## 4. CWD-keyed discovery / single-current-session-per-worktree

- **Significant — a container-hosted CLI-mode session's identity is salted
  with the container name, not purely CWD/worktree-keyed.**
  `plugins/agent-containers/src/agent_containers/copilot_detach.py:43-74`
  (`plan_for()`) builds `scope_id = f"{identity}@{args.name}"`. Two
  container-hosted sessions against the *same* working directory but
  different containers get different scopes — the mechanism doesn't
  actually enforce "at most one current session per working directory"
  across containers the way it does for the local and CodeSpace cases.

- **Related, softer observation.** At the core reservation layer,
  `plugins/agent-bridge/src/agent_bridge/inventory_cli.py:148-159` keys
  reservations by `worktree_id`, not literally by CWD — the CWD-uniqueness
  guarantee depends on the caller (and, per the finding above,
  `agent-containers`) consistently passing the same `worktree_id` for the
  same working directory. This isn't a demonstrated double-session bug on
  its own, but it's the structural reason the container-scoping issue above
  was possible to introduce without the reservation layer itself catching
  it.

## 5. Decoupling (session-driving vs. providing local resources/tools)

- **Worth fixing — `agent-containers`' detached launch bundles remote
  resource provisioning into what should be a thin dispatch path.**
  `plugins/agent-containers/src/agent_containers/copilot_detach.py:
  140-178` (`_launch_env()`) unconditionally calls
  `ensure_agent_worktrees()` and, when a workspace is present,
  `ensure_agent_worktrees_workspace_registered()`; with relay enabled it
  also calls `deploy_shims(args.name, ado=True)` and injects credential
  relay/git-credential environment. Tests encode this as expected behavior
  (`tests/test_copilot_detach.py:207-222, 285-303`), so it's deliberate, not
  accidental — but it's materially broader than "dispatch a session through
  the existing Session Host/reattach path," and it's exactly the kind of
  coupling the origin effort's Guiding Intent calls out as a separate,
  explicitly out-of-scope concern (`visions/host-resource-providers`,
  referenced from `efforts/active/agent-bridge-cli-mode-sessions/README.md`).

- **Clean elsewhere.** `agent-codespaces`' detached path adds only
  registration/reference-file handling and connection forwarding; no code
  there couples session driving to provisioning additional local
  tools/resources (`copilot_detach.py:442-449`, `session_forwards.py:1-18`,
  `connection_owner.py:1-40`).

## 6. Minimal opinionated UX / idiomatic parameter naming

The CLI surface is largely idiomatic and consistent
(`--worktree-id`, `--detach`, `--stop`, `--keep-claim`, `--json`,
`--seed-file`, `--ref-file`, `--register-timeout`, `--dry-run`, kebab-case
throughout) — no broad drift found. Two localized nits:

- **Nit — asymmetric forward-flag grammar in `agent-codespaces`.**
  `copilot_venue.py:127-145` / `copilot_detach.py:155-195` introduce
  `--reverse-forward VENUE_PORT:HOST_PORT` and `--forward
  PORT[:VENUE_PORT]` — two different port-pair orderings for what reads
  like the same kind of flag. Documented and tested, so not a functional
  defect, but more opinionated/error-prone than the otherwise thin surface
  around it.

- **Nit — mismatched lifecycle-parameter naming in `agent-containers`.**
  `copilot_venue.py:32-111` exposes `--ttl-seconds`, while the detached
  plan's own fields are named `reservation_ttl` and `register_timeout` —
  the CLI flag and its underlying concept aren't named to visibly
  correspond.

## 7. Carried-forward, not new — in-container precondition check

`plugins/agent-containers/src/agent_containers/installer_readiness.py:
inspect_toolchain()` validates only host-side `docker`/`devcontainer`/`ssh`
tooling — never in-container `copilot`/`tmux`/`agent-worktrees` presence.
This is the same gap the origin effort's Phase 4 already identified on
2026-09-20 ("must be an explicit precondition check, not an assumption, for
any container-venue launch verb") and explicitly left open. It still isn't
closed. Noting it here because it means a container missing these
prerequisites fails only after reservation/launch preparation rather than
at a clear precondition check — the same failure shape as finding §3's
stale-reservation issue, from a different cause.
