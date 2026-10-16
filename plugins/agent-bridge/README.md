# Agent Bridge

Persistent inter-agent communication service for Copilot CLI. One instance per
machine, providing session management, SSE event streaming, live-session
messaging, and agent subprocess spawning across local, SSH, CodeSpace, and
container venues.

An explicitly configured, already-running remote ACP server is also a client
target: see [experimental remote ACP](docs/remote-acp.md) for the neutral
qualified HTTP/2/SSE + WebSocket profile, scoped authentication, headless
approval contract, and connection-only ownership boundary.

Supports **Windows** and **Linux/WSL** (macOS planned).

Both installers co-deploy the canonical Python runtime resolver and its lease
and construction companions into the Bridge installation's `scripts/` directory,
alongside the existing native-shell resolver deployment in `bin/`. Native
service worktree-owner launches therefore do not require an interactive `PATH`
or a helper inside the Worktrees installation. Legacy owner selection still
requires a completed runtime slot and refuses retired installation ownership;
installation-cell launches retain same-cell provenance validation. This does
not change runtime selection, provision a missing owner, or adopt another
marketplace's runtime.

Internal owner-driven deployment and service restart accept optional
`conditional`/`routing_dir` keyword arguments using the shared
[conditional cutover contract](../../libs/zdd/README.md#conditional-owner-operation-partial-lifecycle-support).
This bypasses caller configuration, legacy recovery and force-reaping, and
refuses legacy daemons without an authoritative effective-config snapshot.
Production capture/attestation is not yet wired; ordinary explicit cutover is
unchanged. Anonymous health and authenticated control remain distinct contracts.

An installer `update` reusing the selected completed slot separately checks the
daemon actually serving. Healthy/current serving is a no-op; a healthy, adopted
older daemon uses ordinary guarded cutover from the unchanged slot. The
installer-internal `deploy --reconcile-serving` mode retains the observed
PID/start reference, rechecks the predecessor at publication and authenticated
control, and confirms native exit without force-reaping. It requires the existing
control credential and never generates, repairs or rotates one. Missing, damaged,
unhealthy or changed ownership fails explicitly; an inactive installation or
forwarded route is not started locally. This mode neither repairs stale
cutovers nor substitutes for the effective-config conditional-repair contract.
A reconcile failure does not publish a success manifest or fall back to
stop/rebuild.
Rollback validates the captured older predecessor rather than requiring the
target version and permits undrain only after that same native process is
verified at its restored route. A local-to-forwarded ownership change is a
refusal, not the initial forwarded-installation no-op. Native predecessor exit
is confirmed before the cutover lock and durable breadcrumb are released;
unresolved retirement or retained cutover evidence also blocks a later
healthy/current shortcut without attempting automatic recovery.
The installers first use `--reconcile-serving --probe-serving` read-only:
exit 0 means no transition, exit 10 identifies an older serving daemon, and
other exits fail. Only exit 10 acquires the existing reference-counted
`update-in-progress` marker around a freshly guarded reconcile, with cleanup
on success or failure. Current, inactive and forwarded no-ops remain marker-free.
Health observation and control probe the routed client address, including IPv6;
the captured predecessor's client never retries a connection into a replacement.
A previous-only route after graceful shutdown is inactive only when its native
process has verifiably exited or is absent and the route stays unchanged.
Permission failures, reused/live PIDs and retained cutover evidence still refuse
without repair. Supported bind addresses are checked against local OS binding,
not a literal-address list; native process and health ownership checks remain.
The successor CLI worker writes a fresh per-launch PID/native-birth receipt.
Reconcile retains that worker's native reference, separately from a broker or
virtual-environment launch handle, and matches its existing health slot PID,
slot ownership and birth before publication and retirement. A same-version
unrelated responder is not a successful launch. Cleanup uses identity-verified
graceful control, never a broker-PID tree kill; uncertain cleanup retains its
receipt and writer-reconciliation breadcrumb. Failure before drain admission
leaves the predecessor's raw route and any operator-owned drain unchanged.
Promotion and verify-before-retire reject a draining successor. Cleanup's
identity probe retains unready health payloads without weakening ordinary health
readiness checks. Watchdog replacement strips the one-shot launch ID rather than
replaying a receipt identity into a replacement process.
The passive serving worker also reads only the existing credential: a missing,
empty or malformed file after the parent's check fails without regeneration.
Ordinary daemon startup retains its create-capable credential loader.
Publication is recorded separately from the current route. Rollback must
non-forcingly drain a successor that may have accepted work before requesting
its shutdown, even if another owner has since replaced its route. A busy or
unverified successor is retained with reconciliation evidence; never-published
passive cleanup does not acquire a drain gate.
If published cleanup fails after closing an initially open admission gate,
it reopens only that gate, after fresh native-worker and exact active-publication
checks. Both the undrain acknowledgement and fresh health must confirm admission
is open. A pre-existing or unknown drain is not reopened, and changed ownership
refuses compensation. The original cleanup failure and reconciliation evidence
remain even when admission is successfully restored.
After draining a published successor, rollback revalidates the captured
predecessor's native identity and health before successor shutdown, and probes
again before restoring routing. Predecessor shutdown also requires its own
verified drain gate to remain closed. Confirmed clean guarded rollback clears
its transient breadcrumb inside the transaction so a fresh update can retry;
uncertain cleanup or retirement retains reconciliation evidence.

Windows and WSL on one physical host are independent logical machines.
The shared machine owner projects a legacy Windows/WSL roster into `host`
and `host-wsl`, preserving their exact SSH profiles and physical association.
Bridge applies its missing SSH-alias default only after projection, using each
logical entry's own key. Explicit aliases (including an empty alias) are retained.
The bridge's topology loader consumes that projection; its raw parser retains
legacy parsing compatibility. Known-platform local detection selects a
projected legacy sibling, while explicitly declared execution spaces still
require their canonical `local_execution_space` selection. Physical grouping
is not transport, session or ownership identity.
The live-session extension obtains the machine key from Agent Worktrees.
A failed owner lookup remains incomplete and is retried with bounded backoff;
it never freezes a physical hostname as a guessed logical machine.
An incomplete lookup explicitly clears the registration's prior targeting
metadata atomically. Ordinary id-only heartbeats still retain metadata;
`clear_targeting_metadata: true` opts into session-only addressing until the
complete logical machine/project/worktree tuple is available again.
Exact registered session IDs retain lookup precedence, including legacy IDs
beginning with `live:`. Only a handle without an exact registered match is
interpreted as a qualified peer address; the prefix does not rename or reserve
previously accepted session IDs.
Explicit HTTP/client `qualified_peer: true` opts into tuple-only resolution
and the new protocol; qualified CLI forwarding supplies it. Prefix text alone
never upgrades a legacy exact-ID request to a newer carrier operation.

[Session preference authority](docs/session-preferences.md) documents optional
execution-user defaults, the unchanged caller-settings default, and the
execution/ACP capability limits.

## Responsibility boundary

agent-bridge owns **live cross-boundary agent communication**: starting or
resuming a persistent session, sending a turn, streaming its response, and
steering or taking over that live agent across repository, worktree, machine,
or venue boundaries. It does not own generic delegation policy or durable task
state. Use native Task sub-agents for bounded work inside the current session,
`delegation-guidance:delegating-work` for task decomposition, and
`agent-dispatch:agent-dispatch` for queued tasks, atomic claims, retries, and
supervision.

The current session-oriented controls and the target compact delegation
semantics are mapped in
[Delegated-agent contract](docs/delegation-contract.md). That baseline defines
logical identity, caller relationships, attention reasons, and message
idempotency without changing the existing CLI, HTTP/SSE, ACP, or Session Host
contracts.

Agent-facing sessions receive an exact payload-local command through the
session command catalog. That command resolves and, when necessary, provisions
the runtime from its own payload without searching `PATH` for another
marketplace's same-named plugin. On Windows the catalog publishes the native
`.cmd` entry so prompt bodies sent through stdin remain intact.

The legacy global wrappers remain explicit compatibility and management
boundaries for callers that do not inherit session catalogs: daemon and service
launchers, picker pivots, remote commands, provider manifests and provider
process boundaries, elevated launchers, and out-of-session management callers.
Catalog adoption does not make those callers payload-aware; they retain their
current commands until an attributable launcher contract reaches each surface.

### Worktree-picker "Bridges" pivot

The installer drops a pivot manifest at `~/.agent-worktrees/pivots/
agent-bridge.json` so the agent-worktrees Textual picker grows a **Bridges**
pivot, rendering the registered-agent roster through `agent-bridge --json
agents`. The manifest declares `"stream": true, "subscribe": true`
(pivot-streaming-transport Phase 2): the Picker runs `agents --stream`
instead of a plain one-shot call, consuming a line-delimited NDJSON envelope
(`begin`/`row`/`done`) so the roster paints progressively, then holds that
channel open -- every 45 seconds (`--interval`, matching the Picker's prior
one-shot repoll cadence) the roster is re-fetched
in-process and diffed against the last snapshot, emitting `delta`/`removed`
frames keyed by agent `name` so an open pivot updates live without
re-invoking the CLI per refresh. Falls back automatically to the plain
one-shot JSON call on an older `agent-bridge` that doesn't understand
`--stream`; a held channel that drops mid-session reconnects with bounded
backoff rather than freezing (the Picker-owned `subscribe` EOF/reconnect
contract -- see `worktree-manager`'s `tasks.py`). A topology-profile error on
the initial fetch surfaces as a stream `error` frame (same contract as a
bridge-connection failure) rather than being silently dropped.

## How It Works

Agent Bridge runs as a local HTTP service on an OS-assigned loopback port by
default. The daemon advertises its live endpoint in `~/.agent-bridge/active.json`
and the CLI discovers it there, so callers should use `agent-bridge status`
rather than hardcoding a port. Multiple Copilot CLI sessions can start, stop,
resume, and observe conversations with agents running locally, over SSH, or via
optional namespace providers such as `codespace:` and `container:`.

agent-bridge is a **standalone persistent daemon** -- enabling only this plugin
is enough for its core service, static/local agents, WebSocket ACP surface, and
live-session registry. Sibling plugins compose opportunistically: when a sibling
drops a provider manifest into `~/.agent-bridge/providers.d/`, its namespace
(for example `codespace:` or `container:`) appears; when it also exposes a
credential-relay profile, agent-bridge folds those sources into its relay. If a
sibling is absent, only that sibling's namespace/relay feature is absent.
Provider discovery is desired-set based: deleting, invalidating, or losing the
command target of a manifest withdraws that dynamic namespace without a daemon
restart. Bad entries warn but do not block valid peers. `agent-bridge doctor`
lists every provider finding with the exact entry, target, and cleanup or
re-registration remedy.

An exact ready provider-exec instance can opt into replay-safe native creation
with `create <agent> --creation-key <key> --provider-instance` (HTTP protocol
36). It retains one native result without allocating or fabricating a worktree.
Ordinary keyed creation remains local-worktree-only. See
[session creation contracts](docs/session-acquisition.md#exact-provider-instance-creation)
for pending/terminal recovery and the separate first-prompt boundary.

### Namespace reader observations

Only CLI `namespace-list` reads use optional procutil CPU metrics. Agent Bridge
opts into the metrics backend; other procutil consumers remain stdlib-only.
Each native read captures its owned PID and creation identity before waiting or
reaping. Sampling is provider-local, off-loop, at most once per second, with a
100ms best-effort join. A pending native sample prevents overlapping sampling
on later reads, independently of command completion or caller cancellation.
The roster cache retains those exact futures across provider replacement and
bounded shutdown.

The existing configured telemetry sink receives `reader_cpu_observation`
events: plugin, `namespace-list` role, bounded namespace key, root PID/creation
time, native wall duration, `observed_cpu_seconds`, `coverage`, `truncated` and
bounded PID/reason `issues`. No arguments, output or paths are emitted.
CPU values are observed lower bounds, not exhaustive tree totals. Unavailable,
failed or rate-limited measurements retain an earlier valid lower bound when
one exists; otherwise they remain `null`. Uncertainty is always explicit.
Wall duration remains `null` until the native command finishes; a cancelled
asyncio task does not establish thread/process completion.
Each event's `sample` is the bounded current native accounting record;
`observation_sample` identifies the successful sample that supplied the CPU
lower bound, which may precede a failed or pending attempt. The separate
`reader_cpu_sample_completed` event exports final accounting even after the
command or its waiter ends. It includes only the same role/namespace/root
identity and sample record, never backend exception text. Collector wall and
thread CPU costs remain separate from command wall duration and tree CPU.
Deduplicate accounting by `(instance, sequence)`, including cached reuse or a
record repeated in both event types. See
[`agent-procutil` accounting](../../libs/agent-procutil/README.md#optional-owned-process-cpu-observations)
for clock uncertainty and the pending/completion contract.

Both event types also carry an immutable `collector` snapshot for the
`(plugin, role, namespace)` measurement key. Its anonymous `epoch` lasts for
one provider observation scope; replacing/recreating that scope starts a
distinct epoch, while old native callbacks continue updating only their old
epoch. Epochs are not owner-session identities or slot-release authority.
`completed`, `succeeded` and `failed` count actual finished sampling attempts,
not cached reuse, command completion or cancelled waits. Completion callbacks
retain a bounded per-sampler cursor; the ledger retains no sampler/event history.
`wall_seconds` and `thread_cpu_seconds` are independent collector-cost lower
bounds. Their `wall_known`/`wall_unknown` and
`thread_cpu_known`/`thread_cpu_unknown` counts distinguish known zero from
missing cost; a total with no known contribution is `null`.
Failed attempts may contribute known cost. These totals never include command
wall time or process-tree CPU and must not be summed again across snapshots.
Unknown contributions and bounded `issues` make incompleteness explicit.
Sequence gaps/invalid provenance report lost continuity; count overflow freezes
the bounded counters without wrapping, and cost overflow preserves the previous
lower bound with an unknown contribution. None silently starts a fresh epoch.

Measurement failure does not discard valid enumeration. Resolve, ensure-ready
and target-repo calls are not instrumented. No CPU admission policy, process
registry or new termination behavior is implied; existing execution deadlines
and fallback/completeness semantics remain unchanged.

## Streaming & the delivery cursor

When a host agent delegates work with `send`, it gets a **continuous, low-noise
feed** of the remote agent's progress -- not a silent block that looks "stuck".

- **`send`** streams the remote turn live by default, then returns when the turn
  settles.
- **`wait`** streams the in-flight turn to completion.
- **`read`** resumes the feed from where the host last left off, or does
  random-access historical reads.
- **`start`** does not stream (no conversation yet).

### Collapsed feed (default)

To avoid polluting the host agent's context, the feed is **collapsed**:

- **agent messages** stream in full (the signal);
- **chain-of-thought** collapses to a single `▸ thinking…` marker per burst;
- **tool calls** collapse to one line: `▸ running: <title> … done`.

Expand on demand (rarely needed) with `--expand`:

```bash
agent-bridge read <session> --expand thoughts   # show full reasoning
agent-bridge read <session> --expand tools       # show tool output
agent-bridge read <session> --expand all
```

### Delivery cursor (exactly-once feed)

Each caller has a per-session **delivery cursor**. Commands stream from the
cursor and **ack only after the content is flushed** to the host, so the cursor
advances on *confirmed delivery* -- never on server-side production. This gives
one contiguous, gap-free, duplicate-free stream:

- Killing the consumer mid-stream (Ctrl-C / SIGKILL / terminal close) leaves the
  cursor where it was; the next `read` resumes **exactly** where the host left
  off -- nothing skipped.
- A service restart mid-workflow is survivable: the client reconnects and
  resumes from the acked cursor (state lives in SQLite).
- Random-access reads (`read --range A:B`, `read --event N`) are the **only**
  way to re-read consumed content and never move the cursor.

The caller identity keying the cursor comes from `--caller`, else the current
worktree (`agent-worktrees get worktree-dir`), else a shared per-session default.

### Remote Bridge carrier operations

Remote consumers use the local Agent Bridge daemon rather than constructing SSH
commands or learning carrier details. The initial surface is intentionally
narrow and read-oriented:

```bash
agent-bridge remote status example-host 11111111-1111-1111-1111-111111111111 \
  --caller-id supervisor.lane-a --json
agent-bridge remote live-session example-host 22222222-2222-2222-2222-222222222222 \
  --json
agent-bridge remote events example-host 11111111-1111-1111-1111-111111111111 \
  --caller-id supervisor.lane-a --json
```

`host` is a topology machine key for a single-environment machine, or an exact
SSH alias. Multi-environment machines require the alias so Windows, WSL, and
Linux Bridge daemons cannot be confused. Session arguments are exact
hosting-Bridge IDs. Status, live-session resolution, event IDs, event names,
payloads, and timestamps come unchanged from the hosting Bridge. The local
daemon owns one shared reconnecting carrier per normalized SSH identity and
does not create another session or event ledger.

Every event consumer supplies its own stable `caller_id`. Remote delivery uses
that identity's hosting-Bridge cursor and acknowledges only after local output
has been flushed. Carrier reconnect starts from the hosting Bridge's durable
acknowledged cursor; a local daemon cutover is likewise recoverable because a
replacement daemon reuses the same remote cursor authority. Event-log rebuild,
cursor invalidation, and non-contiguous replay produce a cursor-neutral
`bridge_control` envelope with `action=full_reconcile`; they are never reported
as a quiet empty stream. The HTTP equivalents live below
`/api/v1/remote/{host}/...` and require the ordinary local bearer token.

### Bounded result snapshots

`result` is the cursor-neutral answer to "what did this delegate produce?":

```bash
agent-bridge result <session-or-worktree>
agent-bridge result <session-or-worktree> --json
agent-bridge result <session-or-worktree> --position <opaque-position>
agent-bridge result <session-or-worktree> --expand <opaque-detail-ref>
```

The fixed-size response combines current lifecycle/attention state, bounded
active-work and pending-input fields, the latest completed turn, and a collapsed
increment of recent work. Reasoning and verbose tool output stay out of the
ordinary result path; each projected event and completed turn carries an opaque
detail reference for explicit expansion. Bridge-owned sessions recover durable
turns and event positions. Represented interactive sessions use the same shape
but explicitly report process-lifetime retention, read-only requests, and
unavailable durable recovery.

Positions are bridge-issued and must not be parsed or compared across targets.
They do not read or advance a delivery cursor. A resync rebuild invalidates an
older position explicitly instead of reusing its event number for different
history. A represented process replacement uses a new session ID and therefore
a new position scope; PID-mismatched re-registration is rejected. Empty logs
return no position until the first event establishes an observable origin.
Result detail expansion never returns reasoning or nested-agent events.

### Attention waits

Bare `wait` keeps its historical meaning: stream the current turn until it
settles. Explicit attention flags opt into the cursor-neutral attention
contract:

```bash
agent-bridge wait <session> --attention input_required
agent-bridge wait <session> --attention failed --attention stopped
agent-bridge wait <session> --all-attention
agent-bridge wait <session> --all-attention --json
agent-bridge wait <session> --all-attention --position <opaque-position>
```

The selected stable reasons are `turn_complete`, `turn_cancelled`, `failed`,
`input_required`, `permission_required`, `unreachable`, `policy_required`,
`contract_changed`, `stopped`, and `ended`. `ended` is reserved but does not
settle in the current production lifecycle because deliberate retirement
deletes the session and its history; callers that need a replayable terminal
boundary should select `stopped` until terminal-retention ownership lands.

Human mode retains one SSE delivery loop, renders and acknowledges through the
settlement boundary, then exits. JSON mode uses only the authenticated bounded
attention request and never reads or advances a delivery cursor. A service
restart is retried from the opaque attention position. A deliberate handoff
continues on a compatible successor; explicit protocol rejection settles as
`contract_changed`, while inconclusive discovery/reachability remains
recoverable. Timeout returns `settled: false` with the latest position rather
than inventing a timeout reason.

The authenticated API is
`GET /api/v1/sessions/{session-or-worktree}/attention` with repeatable `reason`,
optional `position`, and bounded `timeout_seconds` query parameters. A live
manual permission can be resolved through
`POST /api/v1/sessions/{session}/permission` using the correlated `request_id`
and one advertised `option_id`.

### Phased timeouts

`send` distinguishes phases so a slow codespace cold-start is not mistaken for a
hung turn. Configure in `~/.agent-bridge/config.yaml`:

```yaml
timeouts:
  codespace_boot: 300   # waiting for a Shutdown codespace to boot
  ssh_connect: 120      # establishing SSH (patient: wake-on-LAN / ProxyJump)
  session_start: 240    # ACP handshake (client start + initialize)
  session_new: 1200     # cold ACP session/new (large-workspace + skills load)
  command: 1800         # a single turn/command to complete
```

### Remote Session Host authority

A remote Session Host is the durable owner of its Copilot child. Each running
host publishes a mode-0600 record under the remote user's mode-0700
`~/.agent-bridge/session-hosts/` catalogue. The record names the bridge session,
host/child PIDs and process-start identities, host port, protocol/build version,
working directory, and credential-relay reverse forwards. It also carries the
private ATTACH nonce; diagnostics must redact that field.

After a frontend restart, agent-bridge first reattaches from its local HostIndex.
If that row was lost but the session DB survived, it inspects the far-side
record for an already-running CodeSpace, validates boot/PID identity, rebuilds
the ACP and relay forwards, and adopts the same child. A transport failure is
inconclusive and blocks duplicate spawn; only confirmed host death permits
pruning, with an explicit process-group reap if the owned child survived.
Startup inspection never wakes a stopped CodeSpace; explicit resume may wake
and revalidate it.

For a stopped trusted-container session, resume first re-resolves the provider's
current serving generation. If credential relay is enabled, it replaces the
session's reverse-forward against the daemon's current live relay and proves the
container-side loopback listener accepts before ACP is marked ready or a queued
prompt is delivered. Relay setup or verification failure aborts the resume
explicitly; relay-disabled fleets keep the auth-light path.

### Session retention & garbage collection

`sessions.db` is a *relay log* of cross-agent turns/events -- not the canonical
Copilot session history (that lives in each target's `~/.copilot/session-state`
and is archived separately). Left unbounded it grows monotonically: SQLite never
shrinks the file, so a large dispatch can leave **tens of GB** of freelist pages
behind even after the session ends.

The daemon garbage-collects automatically: it prunes the relay metadata for
**terminal** sessions (`ended`/`failed`/`stopped`) older than the retention
window, then VACUUMs to return freed pages to the OS. GC runs on **startup**, on
a periodic **sweep**, and on demand via `agent-bridge gc`. Live sessions (and any
with a still-running client) are never touched. Configure in
`~/.agent-bridge/config.yaml`:

```yaml
retention:
  enabled: true
  max_age_hours: 168       # prune terminal sessions older than this (7 days)
  statuses: [ended, failed, stopped]
  vacuum: true             # compact the DB after pruning
  vacuum_min_free_mb: 128  # only VACUUM when freelist exceeds this
  sweep_interval_hours: 12 # background sweep cadence (0 = startup + manual only)
```

## Connection pipeline & diagnostics

Bringing up a remote agent passes through seven distinct stages, each with its
own patience/fail-fast profile. agent-bridge records a `connect_checkpoint`
event (`started` / `reached` / `failed`, with `elapsed_ms`) at every stage —
into both the daemon log and the session's event feed — so a failure says
*exactly* which stage broke and whether a retry could help, instead of an opaque
"agent died, trying a new session".

| # | Stage | Behavior |
|---|-------|----------|
| 1 | connect-bridge | CLI → service. Transient on restart → short **grace + retry** (client side). |
| 2 | bridge-to-sshmgr | In-process hand-off. Reliable → **fail fast**. |
| 3 | ssh-to-target | ssh-manager → SSH. Boot / wake-on-LAN / ProxyJump → **patient retry** to `ssh_connect` deadline, then a staged retryable failure. |
| 4 | target-auth-env | Auth relay + env on target. Dead relay → **instant fail** (not retryable). |
| 5 | target-binstub | Binstub / folder present. **Instant fail** if missing. |
| 6 | worktree | Create/resume worktree. Failures **propagate**, no retries. |
| 7 | launch-acp | Launch Copilot ACP: handshake bounded by `session_start`, the cold `session/new` bounded by the larger `session_new`, then fail fast. |

On failure, a `connect_failed` event carries `{stage, stage_name, retryable,
message}`. A host agent can surface the connection checkpoints with
`agent-bridge read <session> --expand all`.

Worktree resolver failures retain their nonzero exit code and connection stage.
The bridge prefers a bounded `error` message from independently framed JSON
stdout, including a string `stage` or `code` when supplied, and appends a short
stderr diagnostic.
ANSI/control normalization precedes credential redaction. Launch-plan
environment dictionaries, nested fields from malformed JSON, and opaque stdout
are never echoed as failure diagnostics.
Known-value filtering also uses the supplied local resolver environment or
configured SSH target environment. Overlapping values are replaced longest-first
in one pass; this does not fetch the target's full remote environment.

Version-skew fallback never drops a supplied `--owner-ref` or
`--caller-worktree`. Such requests fail closed when the resolver rejects their
flags. Only an ownerless, callerless request rejected by the argument parser
for `--bridge` may retry using legacy flags. This protects ownership metadata;
it does not add cross-registry allocation authority or receipt reconciliation.

### On-device breadcrumb

Just before the remote binstub runs, agent-bridge writes a timestamped
"reached-device" line (with the session id) to
`$AGENT_BRIDGE_CONNECT_LOG` (default `~/.agent-bridge/connect.log`) on the
target. If a launch hangs or fails opaquely, SSH into the target and check that
log to confirm the connection actually reached the device (and roughly when) —
distinguishing an unreachable host from an on-device failure.

## External ACP clients (WebSocket) & status UX

agent-bridge speaks the [Agent Client Protocol](https://agentclientprotocol.com/)
to its downstream agents, and re-exposes that surface so **any ACP client** can
drive a remote agent through the bridge — for example
[acp-ui](https://acp-ui.github.io/), a browser ACP chat client.

**Endpoints** (JSON-RPC 2.0 over a WebSocket, newline-delimited frames):

| URL | Target |
|-----|--------|
| `ws://<host>:<port>/acp/<agent>` | spawn a fresh session for a registered agent |
| `ws://<host>:<port>/acp/session/<session-id>` | *adopt* an already-running bridge session (observe/steer) — it is **not** stopped when the client disconnects |

Use the port printed by `agent-bridge status`, `agent-bridge token --verbose`,
or `/ui`; the default service port is dynamic unless pinned in config.

**Auth.** Browsers cannot set WebSocket headers, so the bridge token is carried
as a `bearer.<token>` WebSocket subprotocol (acp-ui's convention); a plain
`Authorization: Bearer <token>` header is also accepted for non-browser clients.
The server negotiates the `acp.v1` subprotocol. Print the token with
`agent-bridge token` (it lives in `~/.agent-bridge/auth.yaml`).

**Control surface.** `GET /ui` (open it signed in with `agent-bridge ui`) is a
dependency-free task board for the sessions registered with this bridge: each
worktree task with its orchestrator and supervised venue workers, a session
viewer that collapses tool calls, new/resume/rename task verbs over the
`agent-worktrees` CLI (`/api/v1/ui/workspaces`, `/api/v1/ui/tasks`), and the
ACP agent/session URLs to paste into acp-ui. New task offers the task modes a
repository declares in `.copilot-extensions/agent-bridge/task-modes.yaml`
(`ui_task_modes.py`: an id, label, description, and first-message template the
bridge fills in; the page never sends a template). An earlier task shows its
last session's transcript read back through the cold-store provider seam
(`/api/v1/ui/sessions/{id}/history`, mapped to the live stream's kinds and
bounded) and its branch's own commits (`/api/v1/ui/tasks/{id}/commits`).
The page, scripts, and stylesheet
ship as package data (`ui_static/`) served from a fixed allowlist under a CSP
with no inline code; they carry no data. Data comes only from the
token-protected `/api/v1` routes, and the task-launching verbs additionally
require a same-origin request and are rate limited.

> Hosted acp-ui is served over HTTPS, which (per the browser mixed-content rule)
> can only dial `wss://` — not `ws://localhost`. For a local bridge, use the
> acp-ui desktop app / `npm run preview:web`, or expose the bridge via a `wss://`
> tunnel. acp-ui's "http (remote)" transport is not yet implemented upstream, so
> only the WebSocket transport is wired today.

> Enabled by the pure-Python `wsproto` dependency (uvicorn keeps its plain h11
> HTTP path; no native build, preserving win-arm64 support).

## Getting Started / Usage

Start with [Getting Started](docs/getting-started.md) for the standalone install
and first health check. Then use the [`agent-bridge` skill](skills/agent-bridge/SKILL.md)
and [CLI reference](skills/agent-bridge/references/cli-commands.md) for
day-to-day dispatch, live-session messaging, handoff, and service control.
`agent-bridge handoff-request` is an additional external control-plane seam: a
different extension-enabled session that already stored a durable baton can ask
the bridge to roll a worktree's current ACP session in place and seed the
successor with an exact supplied opening prompt. This does **not** replace or
power agent-bridge's own ACP auto-handoff path: ACP-hosted children still rely
on the bridge's internal context-pressure detection and self-authored brief
flow, independent of Copilot CLI extension activation.
Use [`agent-bridge-troubleshooting`](skills/agent-bridge-troubleshooting/SKILL.md)
for wedged dispatches, resume issues, relay/auth failures, and repair drills.

## Docs

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Install, configure, start the service |
| [Architecture](docs/architecture.md) | Service design, API reference, deployment |
| [Delegated-agent contract](docs/delegation-contract.md) | Current-to-target lifecycle, identity, attention, and idempotency semantics |
| [Machine Configuration](docs/machine-config.md) | Topology setup -- machines.yaml, agents config |

## Skills

| Skill | Description |
|-------|-------------|
| `agent-bridge` | CLI control plane -- send/create/read/wait, sessions, live sessions, service/config |
| `agent-bridge-troubleshooting` | Diagnose and recover stuck dispatches, resume hangs, relay/auth failures, and split-brain |
| `agent-worktrees:copilot-extensions-setup` | Marketplace/runtime setup when a host needs guided installation |

Ordinary `create <target> --charter <profile> --json` without a prompt returns
the bridge `session_id`, connection/worktree identity, native conversation ID,
and observed `ready`/`status`. An idle, zero-turn creation reports
`ready: true` and `status: awaiting_first_prompt`; creation itself sends no
model prompt. Consumers must persist this identity before using ordinary
`send` for the first assigned task. A false readiness result is not permission
to send or to create a replacement.

`send <session-id> --expected-native-session-id <uuid>` (HTTP protocol 33)
admits an immediate ordinary prompt only when that exact native conversation
is still idle with a running client. Identity and liveness are checked under
the turn-admission lock. It never resumes, recreates, queues, or hands off;
`--force` and `--queue` are incompatible. Resume a stopped conversation
explicitly with `resume --strict`, then use the same guarded send. Older
daemons refuse rather than silently ignore the fence: the conditional
`/sessions/{id}/turns/native-bound` route is absent there, including after a
daemon downgrade between capability check and POST. It shares ordinary turn
admission, not acquisition or reviewer-specific transport. The successful result
carries the native UUID that admitted the turn; admission does not prove the
model completed it.

## Platforms

| Platform | Service manager | Auto-start |
|----------|----------------|------------|
| Windows | Scheduled task | At-logon (15s delay) |
| Linux/WSL | systemd user unit | Enabled |
| macOS | Planned | -- |
