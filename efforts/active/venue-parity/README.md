# Venue Parity — agent-bridge dispatch core + thin symmetric SSH venues

- **Slug:** `venue-parity`
- **Repo:** copilot-extensions (plugin code: agent-bridge, agent-codespaces, agent-containers; PR-required `main`, self-merge)
- **Branch(es):** per-slice `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-22
- **Status:** Active — **program/index effort** (steers the architecture + owns
  net-new symmetry pieces). WS-A's trusted-container dispatch/manual path now
  uses OpenSSH, WS-C's credential relay uses the same `-R` back-channel, and
  trusted containers now run under agent-bridge-owned durable Session Hosts.
- **Umbrella issue:** [#954](https://github.com/ThomasMichon/copilot-extensions/issues/954)
- **Vision:** [`visions/venue-parity`](../../../visions/venue-parity/README.md) (child of [`visions/agent-fabric`](../../../visions/agent-fabric/README.md))

## Guiding Intent

Realize the [venue-parity vision](../../../visions/venue-parity/README.md): a
dispatched agent is *the same agent* in a CodeSpace or a local container. Push
every venue-agnostic dispatch concern into **agent-bridge**; reduce
`agent-codespaces` and `agent-containers` to thin, symmetric transports that
differ only in lifecycle, GitHub-token bootstrap, and cold-boot/idle. Unify the
reach (**one SSH transport**) and the auth back-channel (**one SSH `-R` relay
path**). Use local containers as the cheap, controllable **repro/hardening
harness** for every venue flow.

## The only genuine venue differences

1. **Lifecycle** — `gh codespace` (codespaces) vs `docker` (containers).
2. **GitHub-token bootstrap** — codespaces get `GITHUB_TOKEN` for free; containers must bootstrap one.
3. **Boot/idle** — codespaces sleep and cold-boot; containers just start.

Everything else — model/effort/context propagation, in-repo `.ai` + `--plugin-dir`
resolution, concrete cwd, session/status/coordination, auth/relay bootstrap, the
relay back-channel — is **shared**, owned by agent-bridge.

## Current divergence (evidence, 2026-08-22)

- **Size:** agent-codespaces ≈ 14k LOC / 28 modules; agent-containers ≈ 2.5k / 11.
- **Shared today:** only `libs/credential-relay` + `ssh-manager` and a thin
  provider seam (`resolver`/`relay_provider`/`lifecycle`/`lease`).
- **Launch parity is codespace-only + package-local:** `model_launch`,
  `relay_launch`, `plugin_staging`, `codespace_plugins`, `codespace_register`.
  agent-containers' spawn propagates **only** the workspace folder — no model
  flags, no `--plugin-dir`, no `GITHUB_TOKEN`.
- **Relay reach diverges:** codespaces use SSH `-R`; containers use a host-gateway
  TCP hop + per-container token gate.
- **Consequence:** launch-parity fixes built inside agent-codespaces would be
  **rebuilt** for containers unless the logic is shared.

## Trust segmentation (scopes every workstream)

Container fleets are **trust-profiled** (owned by the agent-containers vision), and
parity targets the **trusted** class only:

- **Trusted fleets** — project **full harness capabilities** into the container
  (launch parity, the repo's own local-marketplace plugins, the credential relay,
  and eventually **container-local worktrees + multi-repo**) so a trusted
  container is a **seamless agent-bridge node** on par with a CodeSpace. All WS
  below apply here.
- **Untrusted/restricted fleets** — the provider mostly **wrangles the container
  runtime** (docker/nerdctl/…) and offers an **à-la-carte** tool surface; the host
  agent + scenario decide what to use. They receive **no** automatic launch/plugin
  projection, relay, or host identity. Out of parity scope by construction.

Every injection/projection this effort adds MUST be **gated on the fleet's trust
posture** (`observable-security-posture`) — a restricted fleet is never silently
upgraded.

## The venue transport contract (target)

A small, symmetric interface every venue provider implements; agent-bridge drives
the rest:

- **lifecycle** — provision / start / stop / remove.
- **trust posture** — trusted vs restricted (gates what the core projects).
- **ssh endpoint** — an address the core reaches over one SSH transport.
- **token bootstrap** — surface (codespace) or bootstrap (container) a `GITHUB_TOKEN`.
- **boot semantics** — the wait the core should tolerate (cold-boot vs. start).

## Workstreams

- **WS1 — launch-parity → agent-bridge core (trusted fleets).** Relocate the
  venue-agnostic launch concerns into agent-bridge's dispatch core so both venues
  inherit them: model/effort/context propagation (fail-loud), the repo's own
  local-marketplace (`.ai`/`.claude`/any `directory` marketplace) `--plugin-dir`
  resolution against the **venue's** repo cwd, concrete-cwd resolution. **Gated to
  trusted fleets** (a restricted fleet gets none of it). Venue providers keep only
  venue-in-context plugin injection. Validate via a **container repro**.
- **WS-A — single SSH transport for containers.** Give a fleet container an SSH
  endpoint; drive dispatch/staging/interactive reach over SSH exactly as
  codespaces do (retire bespoke `docker exec` dispatch paths where SSH suffices).
  **Dispatch/manual transport complete:** trusted fleets use OpenSSH with
  `docker exec ... sshd -i` only as the local `ProxyCommand`, so no host port is
  published. Restricted fleets retain their direct deny-by-construction path
  and receive no SSH key projection.
- **WS-B — GitHub-token bootstrap seam.** A uniform token-bootstrap interface;
  codespaces surface the issued token, containers bootstrap one; the core never
  branches on venue to obtain a token.
- ✅ **WS-C — unified auth-relay back-channel over SSH `-R`.** Trusted containers
  reach agent-bridge's published live relay through
  `-R 127.0.0.1:<container-port>:127.0.0.1:<host-port>` on the ACP SSH process.
  The host-gateway TCP hop is retired. The per-container token remains
  request-scoped authorization for the shared relay's Azure action, but no
  longer protects a host-network-exposed endpoint.
- **WS-D — session/status/coordination parity.** Container dispatch gets the same
  monitoring/coordination core codespaces use. **Failed-launch process hygiene
  complete:** a process-owned ACP launch failure now reaps its provider/SSH/
  remote-agent tree before terminal failure is recorded. ✅ **Remote Session Host
  authority:** far-side hosts publish secure per-session ownership
  records so a restarted frontend can reconstruct and reattach without spawning
  a duplicate child. ✅ **Trusted-container Session Host parity:** agent-bridge
  now owns bundle staging, Host/child launch, TCP + relay forwards, target
  ownership, authority recovery, resume/recreate, and confirmed-death cleanup
  for both venue types. `agent-containers` supplies only lifecycle/readiness,
  trust validation, the SSH endpoint, and launch-time auth preparation.
- **WS-E — container-as-parity-harness.** Codify "reproduce the venue flow in a
  container" as accepted evidence for a CodeSpace fix.

## Ordered remaining execution (2026-08-25)

| Order | Slice | Work | Exit gate |
|---|---|---|---|
| **P0** | **Credential-consumer parity** | Define one trusted-venue launch-auth contract. Containers bootstrap GitHub from the host token and ADO/Azure from the relay; CodeSpaces surface their ambient identity behind the same bridge inputs. Configure a non-interactive Git helper for the launched child instead of relying on ambient venue config. | A Session-Host turn proves `gh` identity, GitHub Git credentials, ADO Git credentials, and an Azure token request without printing tokens. Missing relay/token fails explicitly rather than prompting. |
| **P1** | **Shared launch policy (WS1)** | Map the remaining CodeSpace-only repo-local plugin/cwd seams; move remote workspace inspection, enabled `.ai` directory-marketplace resolution, and `--plugin-dir` assembly into agent-bridge. Keep model/effort on the already-shared ACP config-option path. | The same bridge policy produces equivalent cwd + repo-own plugin arguments for a trusted container and a CodeSpace; restricted containers receive none. |
| **P2** | **Formal parity harness** | Productize the manual container proof as a repeatable scenario matrix: normal launch, auth, failed handshake, relay interruption, frontend restart, HostIndex loss, stopped/recreated venue, resume/recreate, lock retention, and end cleanup. | One command emits redacted evidence and a pass/fail result; shared-flow fixes require the container matrix plus a narrow CodeSpace smoke. |
| **P3** | **Lifecycle/resource parity** | Generalize meaningful idle-stop, finalize/prune, capacity, claim, and restart semantics behind venue lifecycle capabilities. Preserve real differences: CodeSpace budget/cold boot versus local disk/RAM/container start. | Shared policy consumes declared capabilities; no `codespace` branch decides a behavior a container can support. |
| **P4** | **Reliability corpus burn-down** | Drive generic #1761/#1765/#145 failure shapes through P2, land fixes in shared process/session code, and isolate only the irreducible `gh codespace ssh`/cloud-idle tails. | Generic process and turn-continuity checks are green in containers; remaining issues name a CodeSpace-only primitive, not a shared failure. |
| **P5** | **Remote-core cleanup** | Rename `CodeSpaceSpawner` and related CodeSpace-shaped generic helpers to remote-venue terminology; move the remaining CodeSpace-only provisioning hook out of the generic spawner. | Shared remote Session Host modules contain no venue-name branch; providers supply all genuine venue-specific behavior through the transport contract. |

The order is intentional: P0 supplies the auth guarantee every later probe
needs; P1 establishes the final shared launch inputs; P2 freezes those contracts
as executable evidence before P3/P4 alter lifecycle and failure behavior; P5 is
last so naming follows the proven abstraction rather than predicting it.

## Validation Plan

- **Container repro is the acceptance gate.** For each shared-core change, a fleet
  container dispatch must show the venue-agnostic guarantee holding (model
  propagated, `.ai` skills loaded, concrete cwd, ADO/Azure/GitHub auth over the
  relay). A green container run is accepted as evidence the codespace path is
  fixed too.
- **No-regression on codespaces.** The same probe passes on a CodeSpace dispatch.
- **Fail-loud checks.** Force each guarantee to fail (wrong model, missing token,
  no relay) and assert the dispatch surfaces it rather than silently degrading.

## Notes / Gotchas

- Coordinate WS1 with whatever stream is building launch-parity fixes so the
  logic lands **once, in the core**, not twice.
- Respect the anchor-write / worktree rules: plugin work in worktrees,
  PR-required, self-merge.
- Vendored-lib sync guard applies if shared logic lands in a `libs/<lib>` used by
  ≥2 plugins (bump every consumer).

## Journal

- **2026-08-23 — WS-A trusted-container SSH dispatch.** Replaced the trusted
  fleet's raw ACP `docker exec` carrier with OpenSSH while preserving the
  provider process boundary: `agent-bridge` still persists only
  `agent-containers exec --stdio <name>`, and the wrapper creates a machine-local
  key/config then runs SSH through `docker exec -i <container> /usr/sbin/sshd -i
  -e` as its `ProxyCommand`. No container port is published. Launch-only
  credentials are staged through a mode-0700 user runtime directory over stdin,
  never argv. Restricted fleets remain on their original transport and receive
  no key. A live trusted dev-container dispatch completed an ACP turn through
  the new path. During validation, bootstrap `docker exec -i` was found to
  inherit and drain ACP stdin before SSH started; bootstrap subprocesses now use
  `DEVNULL` unless explicitly given staged input. **Next:** WS-C carries the
  bridge relay over SSH `-R`, then the container parity corpus can exercise the
  same relay/reaper failure modes as CodeSpaces.
- **2026-08-24 — WS-C SSH relay back-channel.** The trusted container's ACP SSH
  process now owns an explicit loopback-only `-R` from stable container port
  `9857` to agent-bridge's dynamically published host relay port. Launch refuses
  a missing/stale publication, verifies relay identity with `ping/pong`, and
  requires the far-side bind via `ExitOnForwardFailure=yes`. A namespaced
  lifetime target lock prevents concurrent bind collisions. Windows staging
  also moved to byte-exact stdin after validation exposed CRLF translation
  appending `\r` to relay tokens. A live trusted-container agent successfully
  fetched an Azure Storage token through container loopback without exposing
  the token. **Next:** exercise the #1763 forced-timeout/reaper case through this
  now-shared SSH process shape, then continue WS1 shared launch policy.
- **2026-08-24 — failed-launch timeout/reaper parity.** Forced the trusted
  container path to fail its ACP handshake at one second. The prior runtime left
  the host provider/SSH tree, remote sshd/Copilot tree, and a live target-lock
  holder; the new shared SessionManager cleanup shuts down the process-owned
  client and whole tree before recording `failed`, so an immediate retry
  reclaims only a dead lock tombstone and reaches launch. Portable cold-start
  defaults are now boot=300s, handshake=240s, session/new=1200s. Synchronous
  create, explicit resume, worktree resume, and prompt-triggered auto-resume use
  phase-aware HTTP budgets covering every retry/fallback round, and terminal CLI
  output includes the persisted connect stage/message. **Next:** WS1 shared
  launch-policy relocation and the broader container parity corpus.
- **2026-08-24 — remote Session Host authority.** Session Hosts now publish a
  stable, mode-0600 per-session record under the remote user's mode-0700
  catalogue: session identity, host/child PIDs, port, connect nonce, host/wire
  version, process boot/start identity, cwd/executable, and relay-forward
  descriptors. A frontend whose local HostIndex is missing reconstructs it from
  that far-side authority only for already-running CodeSpaces, with bounded
  per-CodeSpace recovery. Reconnect remains first priority: transient SSH/attach
  failure retains the authority record and credential-relay owner and blocks a
  duplicate Copilot spawn. A successfully executed liveness probe must prove
  boot/PID identity before pruning; if the Host is confirmed dead but its owned
  child survived PDEATHSIG, the far side reaps that process group explicitly.
  The structured CodeSpace provider metadata seam was restored in the same
  slice after live validation exposed that current namespace output had fallen
  back to process-owned `agent-codespaces ssh --stdio`. **Live proof:** a
  CodeSpace Session Host published host PID 8887 / child PID 8888; after the
  local HostIndex row was deliberately removed and agent-bridge restarted, the
  frontend reconstructed the record from the far side, rebuilt ACP + relay
  forwards, reattached to the same PIDs, and completed another Copilot turn.
- **2026-08-25 — trusted containers converged on Session Hosts.** Structured
  trusted-container provider metadata now supplies a non-secret SSH descriptor
  and narrow readiness/auth-preparation commands; restricted fleets still omit
  the projection and retain direct execution. Agent-bridge's generic remote
  spawner stages the Host bundle over SSH stdin, owns the `-L` ACP endpoint and
  supervised `-R` relay, persists the far-side authority endpoint, and routes
  initial launch, resume, recreate, and startup reconstruction through the same
  Host path as CodeSpaces. Container state is tri-state: stopped/recreated is
  authoritative death, while Docker/provider failure remains inconclusive and
  blocks duplicate spawn. Partial launch and explicit reap retain target
  ownership until identity-checked remote death is confirmed. Deployed
  agent-bridge **0.4.0-dev356** + agent-containers **0.1.2-dev79** on dev6.
  **Live proof:** session `dbbacaa8-823` published container Host PID 266 /
  child PID 268 with relay metadata; after deleting only the local HostIndex row
  and restarting agent-bridge, the frontend reconstructed the record, rebuilt
  forwards, reattached to the same PIDs, and completed a second turn. Ending
  the session removed both processes and released the container target lock.
  **Next:** WS1 shared launch-policy/plugin staging and a formal reusable parity
  probe.
- **2026-08-25 — P0 credential-consumer parity completed.** The earlier
  Session-Host proof had a serving relay but `git credential fill` failed
  because the trusted container had no configured Git helper and the optional
  `ado-auth-helper` was not deployed. Trusted launches now always deploy the
  relay helper and activate it through launch-only `GIT_CONFIG_*` entries: an
  empty helper resets ambient config, `/usr/local/bin/ado-auth-helper` is
  authoritative, and `GIT_TERMINAL_PROMPT=0` prevents a headless fallback.
  GitHub Git credentials come from the explicit launch `GH_TOKEN`; ADO Git
  requests proxy through the SSH-forwarded host relay. No persistent container
  Git config is modified. Deployed candidate agent-containers
  **0.1.2-dev80**. **Live gate:** Session Host `37006ac0-e06` proved nonempty
  GitHub and ADO credential fills, an authenticated ADO `git ls-remote` (exit
  0), and an Azure Storage token request (nonempty) without emitting credential
  values. **Next:** P1 shared launch policy.
- **2026-08-25 — P1 shared remote launch policy completed.** Repo-own plugin
  resolution now runs through the already-selected remote transport instead of
  a CodeSpace-shaped target-exec inference: agent-bridge ships and executes the
  same canonical `plugin_resolve` payload through either `CodeSpaceTransport`
  or `ContainerTransport`, then appends the resulting remote-local
  `--plugin-dir` paths to the child command. Container child argv is assembled
  from the provider's current raw ACP command plus its launch-only environment,
  so resume/recreate does not freeze stale fleet configuration. **Live gates:**
  a live container ran from `/workspaces/example-web` with repo-local
  capabilities loaded; a fresh CodeSpace ran from the same cwd with a
  repo-local capability loaded.
  Model/effort remain on the shared ACP config-option path.
- **2026-08-25 — P1 reliability detour: CodeSpace 500s traced to stale
  daemons.** The affected CodeSpace and remote Copilot process were healthy;
  the 500s were local `POST /live-sessions/.../events` failures. Five retired
  daemon generations (dev340/344/345/354/356) still listened and wrote the
  shared SQLite DB, producing `sqlite3.OperationalError: database is locked`.
  After retiring only those identity-checked stale process trees, ingestion
  recovered and the `194c` turn completed. Cutover now verifies the old
  supervisor exits after `/shutdown`, force-reaps its verified tree after a
  bound, and fails the deploy if split-brain remains; POSIX escalates to
  process-group `SIGKILL`. Three subsequent cutovers left one generation.
  The same detour fixed stopped zero-turn sessions: no-ACP incumbents are
  replaced atomically with freshly resolved provider metadata rather than
  revived through legacy raw stdio, while explicit recreate retains in-memory
  MCP configuration without persisting possible secrets. Candidate versions:
  agent-bridge **0.4.0-dev361**, agent-containers **0.1.2-dev81**. **Next:** P2
  formal parity harness.
