# Findings — observable agent-bridge CLI sessions

Linked from [`README.md`](README.md)'s Plan/Proposal. Read this when working
Phase 1/2 of that effort; it is the detailed evidence the Proposal
summarizes. This is timeless, current-state documentation — the review
history that produced and corrected these findings lives in the README's
Journal, not here.

Each finding cites exact files/lines from four bounded, independent
evidence passes (agent-bridge core + Picker UI; agent-codespaces;
agent-containers; cross-machine SSH + elevated bridging), reviewed against
the invariants named in the effort's Guiding Intent. Findings are organized
by invariant, not by contributor or PR, per this effort's product-focused
framing.

## 1. CLI-mode transport parity ("all should support CLI mode")

| Transport | Implements CLI-mode sessions? | Reachable via `agent-bridge create --cli`? | Evidence |
|---|---|---|---|
| `agent-containers` | Yes | Yes | `copilot_venue.py:cmd_copilot` (lines 105-143) dispatches to `copilot_detach.cmd_detach` for `--detach` or `run_venue_copilot` otherwise — the actual CLI-mode launch path. (The `ContainerTransport`/`CodeSpaceSpawner` path cited in an earlier draft of this finding is the *headless* Session Host provider, a different mechanism.) Listed in `_CLI_MODE_VENUE_BINSTUBS` (below). |
| `agent-codespaces` | Yes | Yes | Native `copilot --detach --forward`, Connection Owner, model-launch parity (`plugins/agent-codespaces/src/agent_codespaces/copilot_detach.py`). Listed in `_CLI_MODE_VENUE_BINSTUBS` (below). |
| Cross-machine (SSH mesh) | Partially | **Significant, genuine functional/architectural gap** | `plugins/agent-ssh/src/agent_ssh/copilot_detach.py:58-72` (`plan_for()`) implements the same detached/reserved CLI-mode launch shape as `agent-codespaces`/`agent-containers`, but it is `agent-ssh`'s *only* `copilot` entry point (`__main__.py:322-324`, `add_copilot_subparser`) and its contract differs from the other two venues': `--workspace` is `required=True` and the launch mode is a `required=True` mutually-exclusive `--detach`/`--stop` group (`copilot_detach.py:425, 437-439`) — there's no simpler "just attach, anchor mode" verb the way `agent-codespaces copilot <name>` / `agent-containers copilot <name>` both have. Separately, `agent-bridge`'s central dispatch surface (`session_targeting_cli.py:481`, `_CLI_MODE_VENUE_BINSTUBS = {"codespace": "agent-codespaces", "container": "agent-containers"}`) has no `"ssh"` entry, and `--cli`'s help text (`:732`) documents only the other two. Fixing the missing map entry alone would not be sufficient: `_cmd_create_cli` (`:484-540`) invokes `<binstub> copilot <name>` with `--detach` only conditionally added, which satisfies neither `agent-ssh`'s required `--workspace` nor its required mode flag — both the default (attached) and `--detach` calls would fail against today's `agent-ssh` CLI as-is. |

**Elevated bridging is not a fourth CLI-mode venue, by design — this is a
scope question, not a parity gap.** `visions/remote-interactive-sessions/
README.md:1-9, 125-146` defines the supported venue set for symmetric
CLI-mode launch as `agent-codespaces`, `agent-containers`, and an
`agent-ssh`-reachable machine — three remote venues reached over one
SSH-based transport. Elevated bridging
(`plugins/agent-bridge/src/agent_bridge/elevated.py`) is architecturally
different: a local headless ACP relay (Windows S4U/scheduled-task/WMI
broker), not a remote venue a CLI-mode session launches into. A *local*
CLI session already goes directly through `agent-worktrees`
(`session_targeting_cli.py:500-504`), bypassing this mechanism entirely.
`session_targeting_cli.py:732` scoping `--cli` to `codespace:<name>`/
`container:<name>` targets therefore reflects the vision's own venue set,
not an oversight. Extending CLI-mode sessions to reach an elevated target
would be a genuine new capability — a privileged mux/launch/reattach
contract that doesn't exist today for *any* elevated interactive session,
CLI-mode or otherwise — not a wiring gap in the existing mechanism. Framed
as an open question for the Proposal, not a defect.

## 2. Dynamic port reservation

- **Worth fixing — the CLI extension's WSL port-fallback is stale relative
  to the canonical Python client's own already-retired special case.**
  `plugins/agent-bridge/extensions/agent-bridge/extension.mjs:resolveBaseUrl`
  (lines 111-130) correctly reads the daemon's discovered port from
  `active.json` first, then a static `config.yaml` port, and only as a
  last resort falls back to a platform default. That fallback tier itself
  is legitimate and intentional — the canonical Python client keeps exactly
  this last-resort constant (`plugins/agent-bridge/src/agent_bridge/models.py:
  22-33`, `default_port()`), documented as surviving "only as the client's
  last-resort fallback when no routing table exists yet." The concrete
  inconsistency: `models.py:29` explicitly says "the former WSL '+1' (9281)
  is retired with the fixed bind," but the JS extension's fallback
  (`extension.mjs:123-130`) still special-cases WSL to dial 9281. The two
  clients now disagree about a retired platform special-case.

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

- **Worth fixing — `agent-codespaces --detach`'s reservation TTL ignores
  the caller's `--ttl-seconds` value.**
  `plugins/agent-codespaces/src/agent_codespaces/copilot_venue.py:138-148`
  dispatches `--detach` straight to `cmd_detach`, whose plan hard-codes
  `_RESERVATION_TTL` (`copilot_detach.py:49-64`) rather than threading
  through the parsed `--ttl-seconds`; only the *attached* (non-detached)
  path actually forwards `args.ttl_seconds` (`copilot_venue.py:228-237`).
  An operator who passes `--ttl-seconds` on a detached launch gets the
  hard-coded default silently instead.

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
  (`elevated.py:180-230, 296-306`).

## 4. CWD-keyed discovery / single-current-session-per-worktree

The `<worktree identity>@<venue>` scope qualifier
(`plugins/agent-containers/src/agent_containers/copilot_detach.py:43-74`,
`plan_for()`) is the standing, documented design
(`visions/remote-interactive-sessions/README.md:116-123`): one host can see
many venues of the same worktree at once, so the venue qualifier keeps
sibling venues distinct while the worktree stays the uniqueness unit within
a venue. That part is correct and not a finding.

- **Worth fixing — the container forward keeper's own tracking key doesn't
  include that same scope.**
  `plugins/agent-containers/src/agent_containers/forward_keeper.py:
  ensure_running()` keys existing keeper state only by container `name`
  and replaces it whenever the mux/venue port differs — dropping the
  worktree/session part of the scope the CLI-mode reservation itself
  already carries. Two different CLI-mode sessions hosted on the *same*
  container can therefore have one session's forwarding process silently
  replace another's, purely because the keeper's own storage key is
  narrower than the session scope it's supposed to track.

## 5. Decoupling (session-driving vs. providing local resources/tools)

No violation found here. `agent-containers`' detached launch
(`copilot_detach.py:_launch_env()`, which calls `ensure_agent_worktrees()`,
workspace registration, and — when relay is enabled —
`deploy_shims(ado=True)` plus git-credential-relay environment) is required
venue preparation, not unrelated resource provisioning: the standing vision
makes exactly this kind of setup part of the single `copilot` verb's own
contract — "`agent-codespaces copilot <name>` / `agent-containers copilot
<name>` perform the *identical* action for a remote venue by preparing it
(the venue-specific 'setup' step: reverse forwards, **credentials**, the
reservation)" (`visions/remote-interactive-sessions/README.md:139-146`).
The credential-relay/GH-token/ADO-shim mechanism this launch path uses is
also the already-proven, standing pattern `visions/host-resource-providers`
explicitly builds *on* (generalizing credentials to other resource kinds)
rather than recharacterizing as out-of-scope. `agent-worktrees` presence on
the venue is likewise a hard prerequisite for the same verb, not an
unrelated capability bundled in.

## 6. Minimal opinionated UX / idiomatic parameter naming

The CLI surface is idiomatic and consistent
(`--worktree-id`, `--detach`, `--stop`, `--keep-claim`, `--json`,
`--seed-file`, `--ref-file`, `--register-timeout`, `--dry-run`, kebab-case
throughout), including the forwarding flags: `--reverse-forward
VENUE_PORT:HOST_PORT` and `--forward PORT[:VENUE_PORT]`
(`copilot_venue.py:127-145`) both order the *listening*-side port first —
venue-side for the reverse forward, host-side for the local forward —
which is the more consistent convention, not an inconsistency. No naming
drift found in this pass.

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
