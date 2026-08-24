# Venue Parity — agent-bridge dispatch core + thin symmetric SSH venues

- **Slug:** `venue-parity`
- **Repo:** copilot-extensions (plugin code: agent-bridge, agent-codespaces, agent-containers; PR-required `main`, self-merge)
- **Branch(es):** per-slice `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-08-22
- **Status:** Active — **program/index effort** (steers the architecture + owns
  net-new symmetry pieces). WS-A's trusted-container dispatch/manual path now
  uses OpenSSH; the shared `-R` relay path remains WS-C.
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
- **WS-C — unified auth-relay back-channel over SSH `-R`.** Reach the host relay
  over the SSH reverse-forward for containers too; retire the host-gateway TCP hop
  + per-container network token gate. (Gated on WS-A.)
- **WS-D — session/status/coordination parity.** Container dispatch gets the same
  monitoring/coordination core codespaces use.
- **WS-E — container-as-parity-harness.** Codify "reproduce the venue flow in a
  container" as accepted evidence for a CodeSpace fix.

## Plan (first slice)

1. **Map the launch seams** in agent-codespaces (`model_launch` / `plugin_staging`
   / `codespace_plugins` / `codespace_register` / cwd-resolution) and where
   `__main__` / `resolver` call them.
2. **Define the venue interface** the core needs to run that logic against the
   venue's repo cwd (remote FS read for `.ai` resolution; the concrete workspace
   path; the model-flag application point).
3. **Move the logic into agent-bridge** (a dispatch-core module, or a shared lib
   if it must run in the provider venv), leaving venue providers to supply the
   interface + venue-in-context plugins.
4. **Wire agent-containers** to the same core path (it currently does none of it).
5. **Validate in a container:** dispatch into a fleet container and confirm the
   agent reports the host model, loads the repo's `.ai` skills, and runs from the
   concrete repo cwd — the same probe used for the codespace diagnostic.

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
