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
| Cross-machine (SSH mesh) | Yes | `plugins/agent-ssh/src/agent_ssh/copilot_detach.py:58-72` (`plan_for()`) implements the same detached/reserved CLI-mode launch shape as `agent-codespaces`/`agent-containers`. (Correction: an earlier pass of this review cited `session_host/spawner.py`'s `CodeSpaceSpawner`/`SshSpawner` framing — that's a *different*, generic headless/ACP agent-dispatch path, where `session_start.py:680-685` documents SSH-mesh/elevated targets as a known, separately-tracked gap (#566), unrelated to the CLI-mode session mechanism this effort reviews.) |
| **Elevated bridging (Windows S4U/scheduled-task/WMI broker)** | **No** | `plugins/agent-bridge/src/agent_bridge/session_targeting_cli.py:732` explicitly scopes `--cli` to `codespace:<name>` and `container:<name>` targets only and rejects a bare/elevated target. `elevated.py` implements an isolated ACP-relay daemon lifecycle (`relay_spawn_command`, `relay_agent_for` at `:233-296`) with no Session Host launch, CLI reservation claim, or muxed CLI process anywhere in that path. |

**Finding:** elevated bridging is the one transport genuinely left out of
the CLI-mode session work — not a partial or inconsistent implementation,
but no wiring at all. This directly answers the operator's "ensure...
elevated bridging aren't left out" concern: it was.

## 2. Dynamic port reservation

- **Worth fixing — the CLI extension's client-fallback logic is stale
  relative to the canonical Python client's own already-retired WSL port.**
  `plugins/agent-bridge/extensions/agent-bridge/extension.mjs:resolveBaseUrl`
  (lines 111-130) correctly reads the daemon's discovered port from
  `active.json` first, then a static `config.yaml` port, and only as a
  last resort falls back to a platform default. That fallback tier itself
  is legitimate and intentional — the canonical Python client keeps exactly
  this last-resort constant (`plugins/agent-bridge/src/agent_bridge/models.py:
  22-33`, `default_port()`), documented as surviving "only as the client's
  last-resort fallback when no routing table exists yet." The actual
  inconsistency: `models.py:29` explicitly says "the former WSL '+1' (9281)
  is retired with the fixed bind," but the JS extension's fallback
  (`extension.mjs:123-130`) still special-cases WSL to dial 9281. The two
  clients now disagree about a retired platform special-case. (Correction:
  an earlier pass of this review over-broadly characterized this as
  "hardcoded fallback ports conflicting with the dynamic-port contract" —
  the 9280 fallback itself is not a departure from that contract; only the
  stale 9281 WSL branch is.)

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

- **Worth fixing — a container's local-forward keeper is tracked by
  container name alone, not by the full venue-qualified session scope.**
  `plugins/agent-containers/src/agent_containers/forward_keeper.py:
  ensure_running()` keys existing keeper state only by container `name`
  and replaces it whenever the mux/venue port differs. Two different
  CLI-mode sessions hosted on the *same* container (e.g. two different
  worktrees, or the same worktree reused after a prior session ended) can
  therefore have one session's forwarding process silently replace
  another's, because the keeper's identity doesn't include the
  worktree/session scope the way the CLI-mode reservation itself does.
  (Correction: an earlier pass of this review additionally flagged
  `copilot_detach.py:plan_for()`'s `scope_id = f"{identity}@{args.name}"`
  as breaking CWD-uniqueness across containers — that's wrong. The
  `<worktree identity>@<venue>` qualifier is the standing, documented
  design (`visions/remote-interactive-sessions/README.md:116-123`):
  "One host coordination layer can see *many* venues of the same
  repository at once... so a venue launch qualifies the identity it
  reserves and registers under with the venue." Dropping that qualifier
  would be the actual regression. The real, narrower bug is only in the
  forward keeper's own tracking key, not the session scope.)

## 5. Decoupling (session-driving vs. providing local resources/tools)

No violation found here on a corrected reading. An earlier pass of this
review flagged `agent-containers`' detached launch
(`copilot_detach.py:_launch_env()`, which calls `ensure_agent_worktrees()`,
workspace registration, and — when relay is enabled —
`deploy_shims(ado=True)` plus git-credential-relay environment) as coupling
session dispatch to unrelated remote-resource provisioning. That doesn't
hold up: the standing vision makes exactly this kind of venue preparation
part of the single `copilot` verb's own contract —
"`agent-codespaces copilot <name>` / `agent-containers copilot <name>`
perform the *identical* action for a remote venue by preparing it (the
venue-specific 'setup' step: reverse forwards, **credentials**, the
reservation)" (`visions/remote-interactive-sessions/README.md:139-146`).
The credential-relay/GH-token/ADO-shim mechanism this launch path uses is
also the already-proven, standing pattern the repo's own
`visions/host-resource-providers` explicitly builds on rather than
supersedes — that vision generalizes *beyond* credentials to other
resource kinds; it doesn't recharacterize the existing credential relay as
out-of-scope. `agent-worktrees` presence on the venue is likewise a hard
prerequisite for the same verb ("agent-worktrees is also required to
execute that verb"), not an unrelated capability being bundled in.
Retracted; no adjustment proposed.

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

## 7. Adjacent, out-of-scope: an already-tracked pre-existing gap

`plugins/agent-containers/src/agent_containers/installer_readiness.py:
inspect_toolchain()` validates only host-side `docker`/`devcontainer`/`ssh`
tooling — never in-container `copilot`/`tmux`/`agent-worktrees` presence.
This is the same gap the origin effort's Phase 4 already identified on
2026-09-20 and explicitly left open; it predates the PRs this effort
reviews and isn't something any of them introduced or touched. Noted here
for continuity, but **excluded from the Proposal below** — this effort's
scope is the zero-review PRs, and this gap is neither one of them nor a
regression they caused. It remains the origin effort's own open item.
