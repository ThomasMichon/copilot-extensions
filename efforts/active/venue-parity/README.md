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

## Context

### Current divergence (evidence, 2026-08-22)

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

## Request

Realize the existing Guiding Intent through the following venue transport
contract and workstreams; this structural heading does not revise their scope.

### The venue transport contract (target)

A small, symmetric interface every venue provider implements; agent-bridge drives
the rest:

- **lifecycle** — provision / start / stop / remove.
- **trust posture** — trusted vs restricted (gates what the core projects).
- **ssh endpoint** — an address the core reaches over one SSH transport.
- **token bootstrap** — surface (codespace) or bootstrap (container) a `GITHUB_TOKEN`.
- **boot semantics** — the wait the core should tolerate (cold-boot vs. start).

## Plan

### Workstreams

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

### Ordered remaining execution (2026-08-25)

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

### Deferred Backlog Intake

- [ ] Accept venue-parity candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted technical scope against the current transport,
  lifecycle, and authentication contracts; return obsolete or unsafe candidates
  for explicit disposition.
- [ ] Place each accepted public tracker item in exactly one workstream,
  extending this plan before implementation when necessary.
- [ ] Use synthetic venue identities and redact environment-specific evidence.

### Trusted-container bootstrap and resumable initialization retained from #2476

<!-- backlog-reconciliation:7556:transfer:2476:to:efforts/active/venue-parity/README.md -->

This is the existing P0 credential-consumer and P3 provider-lifecycle intake,
through the migration-intake contract above, not plugin-staging-only work.
The following acceptance remains unfinished; this amendment is not an
implementation or native-completion receipt.

- [ ] Before dependent post-start setup, supply required relay-backed authentication
  OR explicitly model a recoverable initialization stage. Later dispatch-time
  helper injection alone is not bootstrap-order proof.
- [ ] Define one supported generic helper-discovery contract: explicit path
  override, PATH lookup OR owner-projected home path. Reconcile supported injected
  relay and home-path consumers without overwriting existing user helpers;
  preserve credential-relay ownership.
- [ ] Retain the created member's identity and explicit partial-initialization
  state when post-start authentication fails after build, clone and dependency
  installation; do not discard identity before surfacing the failure or report
  the member as ready.
- [ ] Provide an explicit retry/resume path that reuses the same owned member and
  expensive completed work, exposes remaining setup and recovery diagnostics, and
  does not create a duplicate or silently skip failed required initialization.
- [ ] Preserve explicit authentication requirements and trusted-only relay
  projection. Do not copy host credential profiles, require browser/device-code
  authentication inside the container, upgrade restricted trust posture, or treat
  this intake as permission to change credentials or running resources.

#### Validation retained from #2476

- [ ] Cover the original Windows-host/Linux-container fresh trusted-member setup
  and actual dependent post-start authentication boundary in approved disposable
  coverage; identify the tested platform/recipe and limits.
- [ ] Demonstrate helper discovery for the supported override/PATH/projected-home
  alternatives, respecting an existing user helper and the absence of an
  authenticated in-container credential profile.
- [ ] Force post-start/relay-auth failure after completed setup, observe the same
  retained member/partial state, resume it, and observe the actual required
  readiness transition without duplicate creation or rerunning completed
  expensive work unnecessarily.
- [ ] Cover unavailable relay/auth and restricted-trust cases with explicit
  failure/recovery, no false-ready result, no credential-profile copy and no
  interactive in-container authentication. Existing helper files, fleet result
  categories and unit/source inspection alone do not establish the lifecycle
  effect.

#### Continuing coordination for #2476

Preserve agent-bridge's shared authentication/relay authority,
agent-containers' lifecycle/readiness responsibility, existing participants,
open bounded source claims and the live #954 program token. This root's
Workstreams, Validation Plan and Journal retain this combined slice once,
through the existing migration-intake contract. No source/resource handoff,
generic ACK veto or unrelated whole-parent completion gate is introduced.
Normal review, actual merge and full current root readback precede
administrative supersession of #2476; afterward it is provenance, not a live
claim token or proof of completed acceptance.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Direct retained Transport/Bridge acceptance

**Agent-recommended additive tracking amendment.** Preserve every incumbent
Request, status, checked credit, original row and first unresolved gate. The
direct rows below retain unfinished work at its original finite scope,
alternatives and evidence tiers. They do not authorize implementation,
native/installed trials, operational changes, source closure, reassignment
or claim/resource release. Normal review, actual merge and complete current
receiving readback precede any separate source-specific disposition.


#### Source #954

- [ ] **P.954.whole-programme:** Whole equivalent-quality TRUSTED GitHub CodeSpace AND
  trusted-container programme over one Bridge-owned dispatch/session/auth/launch core. Preserve
  actual lifecycle, token-bootstrap and cold-boot/idle differences; restricted profiles remain
  deny-by-construction, no projected host credentials/tools.

- [ ] **P.954.P0-exact-managed-credentials:** P0 credential-consumer parity remains, including
  ALL2094 exact managed worker Git-helper/SSH/reverse-forward path BEFORE product work,
  generation-bound success evidence/invalidation and five distinct token-safe failure classes. Relay
  ping/pong, REST, host token or another warmup is not that path.

- [ ] **P.954.P1-common-launch:** P1 shared launch/plugin/cwd/model/effort/context and actual
  provider executable/profile closure remain. Credit1114/7157/current shared preparation and native
  consumer progress; no duplicate source or obsolete class-name absence claim.

- [ ] **P.954.P2-native-matrix-and-bounds:** Keep the original P2 reusable container fault matrix
  AND scoped GitHub CodeSpace control: launch/auth/failed handshake/relay interruption/frontend
  restart/HostIndex loss/stop-recreate/resume-lock-end. Historical recreation exceeded15min and
  passed with40min: never relabel it an unchanged15min pass, nor invent a new universal40min bound.

- [ ] **P.954.P3-live-reservations:** P3 provider-declared
  lifecycle/resource/capacity/idle-restart/finalize/prune semantics and all actual P3 reservations,
  permission/session-rescue/state/resource holders stay. Generic Taking or age is not expiry;
  current explicit954 keep-OPEN condition is flagged separately.

- [ ] **P.954.P4-credit-and-reliability:** P4 original generic reliability failure shapes use the
  shared harness, with only irreducible CodeSpace/cloud-idle tails separated. Do not reopen
  already-delivered corpus/lifetime/recorder/plan foundations or infer whole programme completion
  from bookkeeping/model mocks.

- [ ] **P.954.P5-proven-abstraction:** P5 remote-core naming/provision-hook cleanup follows
  qualified shared behavior; provider boundaries supply genuine differences. No duplicate
  transport/lifetime owner or mandatory old symbol name is introduced.

- [ ] **P.954.retained-union:** Retain the complete CURRENT954 original, ALL18 current refinements
  and directly accepted2042/2094/1082/5926 clauses PLUS actual current ROOT2476
  Plan/Validation/Coordination. Every original AND/OR/native venue/finite
  bound/P0-P5/token/P3/resource condition survives. Broader566 SSH/elevated and independent109/4227
  conditions are NOT absorbed or reconstructed.

- [ ] **P.954.r0-check-35:** Windows self-provisioning launchers invoke absolute SystemRoot
  where.exe, capture an absolute pwsh executable and fall back only to the absolute Windows
  PowerShell path. Nested self-provisioning must never revert to an ambient executable name.

- [ ] **P.954.r0-check-36:** Execute registered CodeSpace and trusted-container provider manifests
  through the actual Bridge-daemon invocation boundary with minimal/empty PATH, proving their
  transitive executable closure rather than only validating the top-level command path.

- [ ] **P.954.r0-check-37:** Render every required trusted-container SSH field, including the
  returned identity file, quoting paths correctly and retaining trust/host-key restrictions.
  Session-host-prepare must read back the emitted configuration and verify it agrees with its JSON
  connection contract, not merely return a populated in-memory object.

- [ ] **P.954.r0-check-38:** Extend the existing provider doctor/readiness surfaces to diagnose
  executable-chain and SSH-profile incompleteness with bounded actionable results. Do not add a
  competing doctor/manifest authority or silently provision during an advertised read-only
  inspection.

- [ ] **P.954.r0-check-39:** Cover Bridge-daemon and direct SSH reattachment for both providers,
  including Windows launchers and native key/profile use. Record native/installed evidence
  separately from constructed-command and mocked-broker tests.

- [ ] **P.954.r0-check-40:** Preserve provider-owned lifecycle/readiness responsibilities, existing
  exact-source/identity and generation gates, genuine Windows/POSIX differences and the
  restricted-container denial boundary.

- [ ] **P.954.r0-check-56:** Before the agent receives product work, run a bounded token-safe
  preflight for the configured organization-neutral ADO host through the exact managed SSH
  connection/session and reverse forward that the worker's Git credential helper uses.

- [ ] **P.954.r0-check-57:** Cache successful evidence only for that active connection/session
  generation and invalidate it after transport, relay, provider or session-generation changes. Do
  not reuse another session's or a standalone warmup probe's success.

- [ ] **P.954.r0-check-58:** Stop before turn admission on failure and classify missing host
  credential, host relay listener failure, reverse-forward failure, policy denial and remote helper
  timeout distinctly without printing credential values or auth-bearing payloads.

- [ ] **P.954.r0-check-59:** Reconcile/reconnect stale transport around surviving session authority
  rather than encouraging repeated unrelated auth strategies, mutating global auth or replacing the
  worker. Preserve no-ambiguous-replay and provider-generation rotation gates.

- [ ] **P.954.r0-check-60:** Cover configured-host success and every failure class, stale-generation
  invalidation, active-session reuse and exact-path proof with boolean/redacted evidence. Retain
  native/installed CodeSpace acceptance and corresponding shared trusted-container parity where
  applicable; a parity harness's post-launch credential check is not universal pre-turn enforcement.

- [ ] **P.954.r0-check-76:** Define a generic EXPLICIT persistence contract in agent-codespaces core
  OR a narrowly coupled auxiliary plugin. Do not restore a private side-load dependency, fork an
  installer or treat optional repository hooks/local trust helpers as the portable remote feature.

- [ ] **P.954.r0-check-77:** Before destructive retirement, export already-approved Copilot
  permissions and trusted-folder state from the REACHABLE CodeSpace. Store it in the user's
  configured state repository OR another configured durable store. Keep paths repo-neutral and
  resolve the actual state-root, including split harness/knowledge arrangements; never hardcode an
  adopter checkout.

- [ ] **P.954.r0-check-78:** Preserve ONLY the validated approval/trusted-folder allowlist. Exclude
  credentials, OAuth/token caches, keys, arbitrary settings and secret-bearing fields. Do not infer
  broader approval or credential grants from transport reachability, restored data or this tracking
  consolidation.

- [ ] **P.954.r0-check-79:** During provisioning/connect, safely restore the CONFIGURED template
  into the new CodeSpace using the existing lifecycle owner. Preserve intended existing approvals,
  supported native format and idempotence; malformed, unsupported or partially unavailable input
  must be explicit, not a successful partial restore or blind overwrite.

- [ ] **P.954.r0-check-80:** When export is configured as REQUIRED, integrate its actual result with
  finalize --delete: a failed/unavailable required permission export blocks deletion unless the user
  explicitly accepts THIS approval-state loss. That exception does not waive session rescue,
  workspace/claim/dirty-state or other independent deletion protections. No deletion or loss
  acceptance is granted by this checklist.

- [ ] **P.954.r0-check-81:** Cover idempotent export/restore, unavailable SSH, malformed templates,
  secret exclusion and split/state-root paths through the actual lifecycle/storage consumers. Verify
  required-export refusal and explicit-loss distinction; a transcript/session-sync success or
  constructed local permission file is not remote permission-persistence acceptance.

- [ ] **P.954.r0-check-82:** Keep restoration, native/installed qualification, source publication
  and authorized live adoption separate. Existing manual communication/venue use and normal reviewed
  source contributions do not wait for an unrelated whole-parent native portfolio or a new owner
  ACK. Actual overlaps/resource operations retain their real custody and authorization.

- [ ] **P.954.r0-check-95:** D1. Normalize accepted input with the EXISTING shared seed-reader
  authority in the ACTUAL attached Container caller BEFORE run_venue_copilot. Preserve direct --seed
  OR UTF8 file OR '-' stdin. An already-resolved-string launcher cannot recover a filename its
  caller omitted.

- [ ] **P.954.r0-check-96:** D2. Preserve mutual-exclusion, existing shared size limits, explicit
  read/validation errors and genuinely empty-input None semantics. An unreadable file must not
  silently become an empty prompt. Keep exact attached argv/result construction and
  model/effort/context choices.

- [ ] **P.954.r0-check-97:** D3. Preserve target locks and reserve/connect/release, trust/permission
  policy and restricted-profile refusal. Detached Container and attached CodeSpace callers remain
  positive controls, not additional broken paths; no change to source/plugin preparation, discovery
  or P3 lifecycle/resource/idle-restart policy is implied.

- [ ] **P.954.r0-check-100:** DV1. Exercise actual attached parser-to-reader-to-launch argv/result
  behavior for direct, file/stdin, conflicting inputs, existing limit, unreadable and empty cases,
  plus detached Container/attached CodeSpace parity controls, using bounded fixtures without live
  SSH/provisioning as the original focused acceptance requires.

- [ ] **P.954.r0-check-101:** DV2. Retain original applicable fresh/live native/installed CLI-mode
  qualification with its existing owner. The historical source-pure accepted-input audit and5931
  docs-only record are not new execution or implemented fix;7157 plugin-preparation ACP/CLI proof is
  real but not this seed-file consumer. No every-venue/new whole-parent native matrix is added.

## Validation Plan

- [ ] Complete every directly retained #2476 Plan/Validation clause above at
  its original evidence tier and bounds, preserving current owners, alternatives
  and holds. Tracker consolidation is not bootstrap or lifecycle completion.

- **Container repro is the acceptance gate.** For each shared-core change, a fleet
  container dispatch must show the venue-agnostic guarantee holding (model
  propagated, `.ai` skills loaded, concrete cwd, ADO/Azure/GitHub auth over the
  relay). A green container run is accepted as evidence the codespace path is
  fixed too.
- **No-regression on codespaces.** The same probe passes on a CodeSpace dispatch.
- **Fail-loud checks.** Force each guarantee to fail (wrong model, missing token,
  no relay) and assert the dispatch surfaces it rather than silently degrading.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Global validation of retained Transport/Bridge acceptance

#### Source #954

- [ ] **V.954.whole-programme:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Whole equivalent-quality
  TRUSTED GitHub CodeSpace AND trusted-container programme over one Bridge-owned
  dispatch/session/auth/launch core. Preserve actual lifecycle, token-bootstrap and cold-boot/idle
  differences; restricted profiles remain deny-by-construction, no projected host credentials/tools.

- [ ] **V.954.P0-exact-managed-credentials:** Verify the COMPLETE original/refined clause at its
  ORIGINAL finite scope, alternatives, venue, metrics and evidence tier; preserve completed credit
  and SAME source/resource custodians, no new everyhost/whole-parent/ACK gate: P0
  credential-consumer parity remains, including ALL2094 exact managed worker
  Git-helper/SSH/reverse-forward path BEFORE product work, generation-bound success
  evidence/invalidation and five distinct token-safe failure classes. Relay ping/pong, REST, host
  token or another warmup is not that path.

- [ ] **V.954.P1-common-launch:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: P1 shared
  launch/plugin/cwd/model/effort/context and actual provider executable/profile closure remain.
  Credit1114/7157/current shared preparation and native consumer progress; no duplicate source or
  obsolete class-name absence claim.

- [ ] **V.954.P2-native-matrix-and-bounds:** Verify the COMPLETE original/refined clause at its
  ORIGINAL finite scope, alternatives, venue, metrics and evidence tier; preserve completed credit
  and SAME source/resource custodians, no new everyhost/whole-parent/ACK gate: Keep the original P2
  reusable container fault matrix AND scoped GitHub CodeSpace control: launch/auth/failed
  handshake/relay interruption/frontend restart/HostIndex loss/stop-recreate/resume-lock-end.
  Historical recreation exceeded15min and passed with40min: never relabel it an unchanged15min pass,
  nor invent a new universal40min bound.

- [ ] **V.954.P3-live-reservations:** Verify the COMPLETE original/refined clause at its ORIGINAL
  finite scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: P3 provider-declared
  lifecycle/resource/capacity/idle-restart/finalize/prune semantics and all actual P3 reservations,
  permission/session-rescue/state/resource holders stay. Generic Taking or age is not expiry;
  current explicit954 keep-OPEN condition is flagged separately.

- [ ] **V.954.P4-credit-and-reliability:** Verify the COMPLETE original/refined clause at its
  ORIGINAL finite scope, alternatives, venue, metrics and evidence tier; preserve completed credit
  and SAME source/resource custodians, no new everyhost/whole-parent/ACK gate: P4 original generic
  reliability failure shapes use the shared harness, with only irreducible CodeSpace/cloud-idle
  tails separated. Do not reopen already-delivered corpus/lifetime/recorder/plan foundations or
  infer whole programme completion from bookkeeping/model mocks.

- [ ] **V.954.P5-proven-abstraction:** Verify the COMPLETE original/refined clause at its ORIGINAL
  finite scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: P5 remote-core
  naming/provision-hook cleanup follows qualified shared behavior; provider boundaries supply
  genuine differences. No duplicate transport/lifetime owner or mandatory old symbol name is
  introduced.

- [ ] **V.954.retained-union:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Retain the complete CURRENT954
  original, ALL18 current refinements and directly accepted2042/2094/1082/5926 clauses PLUS actual
  current ROOT2476 Plan/Validation/Coordination. Every original AND/OR/native venue/finite
  bound/P0-P5/token/P3/resource condition survives. Broader566 SSH/elevated and independent109/4227
  conditions are NOT absorbed or reconstructed.

- [ ] **V.954.r0-check-35:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Windows self-provisioning
  launchers invoke absolute SystemRoot where.exe, capture an absolute pwsh executable and fall back
  only to the absolute Windows PowerShell path. Nested self-provisioning must never revert to an
  ambient executable name.

- [ ] **V.954.r0-check-36:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Execute registered CodeSpace
  and trusted-container provider manifests through the actual Bridge-daemon invocation boundary with
  minimal/empty PATH, proving their transitive executable closure rather than only validating the
  top-level command path.

- [ ] **V.954.r0-check-37:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Render every required
  trusted-container SSH field, including the returned identity file, quoting paths correctly and
  retaining trust/host-key restrictions. Session-host-prepare must read back the emitted
  configuration and verify it agrees with its JSON connection contract, not merely return a
  populated in-memory object.

- [ ] **V.954.r0-check-38:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Extend the existing provider
  doctor/readiness surfaces to diagnose executable-chain and SSH-profile incompleteness with bounded
  actionable results. Do not add a competing doctor/manifest authority or silently provision during
  an advertised read-only inspection.

- [ ] **V.954.r0-check-39:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Cover Bridge-daemon and direct
  SSH reattachment for both providers, including Windows launchers and native key/profile use.
  Record native/installed evidence separately from constructed-command and mocked-broker tests.

- [ ] **V.954.r0-check-40:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Preserve provider-owned
  lifecycle/readiness responsibilities, existing exact-source/identity and generation gates, genuine
  Windows/POSIX differences and the restricted-container denial boundary.

- [ ] **V.954.r0-check-56:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Before the agent receives
  product work, run a bounded token-safe preflight for the configured organization-neutral ADO host
  through the exact managed SSH connection/session and reverse forward that the worker's Git
  credential helper uses.

- [ ] **V.954.r0-check-57:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Cache successful evidence only
  for that active connection/session generation and invalidate it after transport, relay, provider
  or session-generation changes. Do not reuse another session's or a standalone warmup probe's
  success.

- [ ] **V.954.r0-check-58:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Stop before turn admission on
  failure and classify missing host credential, host relay listener failure, reverse-forward
  failure, policy denial and remote helper timeout distinctly without printing credential values or
  auth-bearing payloads.

- [ ] **V.954.r0-check-59:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Reconcile/reconnect stale
  transport around surviving session authority rather than encouraging repeated unrelated auth
  strategies, mutating global auth or replacing the worker. Preserve no-ambiguous-replay and
  provider-generation rotation gates.

- [ ] **V.954.r0-check-60:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Cover configured-host success
  and every failure class, stale-generation invalidation, active-session reuse and exact-path proof
  with boolean/redacted evidence. Retain native/installed CodeSpace acceptance and corresponding
  shared trusted-container parity where applicable; a parity harness's post-launch credential check
  is not universal pre-turn enforcement.

- [ ] **V.954.r0-check-76:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Define a generic EXPLICIT
  persistence contract in agent-codespaces core OR a narrowly coupled auxiliary plugin. Do not
  restore a private side-load dependency, fork an installer or treat optional repository hooks/local
  trust helpers as the portable remote feature.

- [ ] **V.954.r0-check-77:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Before destructive retirement,
  export already-approved Copilot permissions and trusted-folder state from the REACHABLE CodeSpace.
  Store it in the user's configured state repository OR another configured durable store. Keep paths
  repo-neutral and resolve the actual state-root, including split harness/knowledge arrangements;
  never hardcode an adopter checkout.

- [ ] **V.954.r0-check-78:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Preserve ONLY the validated
  approval/trusted-folder allowlist. Exclude credentials, OAuth/token caches, keys, arbitrary
  settings and secret-bearing fields. Do not infer broader approval or credential grants from
  transport reachability, restored data or this tracking consolidation.

- [ ] **V.954.r0-check-79:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: During provisioning/connect,
  safely restore the CONFIGURED template into the new CodeSpace using the existing lifecycle owner.
  Preserve intended existing approvals, supported native format and idempotence; malformed,
  unsupported or partially unavailable input must be explicit, not a successful partial restore or
  blind overwrite.

- [ ] **V.954.r0-check-80:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: When export is configured as
  REQUIRED, integrate its actual result with finalize --delete: a failed/unavailable required
  permission export blocks deletion unless the user explicitly accepts THIS approval-state loss.
  That exception does not waive session rescue, workspace/claim/dirty-state or other independent
  deletion protections. No deletion or loss acceptance is granted by this checklist.

- [ ] **V.954.r0-check-81:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Cover idempotent
  export/restore, unavailable SSH, malformed templates, secret exclusion and split/state-root paths
  through the actual lifecycle/storage consumers. Verify required-export refusal and explicit-loss
  distinction; a transcript/session-sync success or constructed local permission file is not remote
  permission-persistence acceptance.

- [ ] **V.954.r0-check-82:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: Keep restoration,
  native/installed qualification, source publication and authorized live adoption separate. Existing
  manual communication/venue use and normal reviewed source contributions do not wait for an
  unrelated whole-parent native portfolio or a new owner ACK. Actual overlaps/resource operations
  retain their real custody and authorization.

- [ ] **V.954.r0-check-95:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: D1. Normalize accepted input
  with the EXISTING shared seed-reader authority in the ACTUAL attached Container caller BEFORE
  run_venue_copilot. Preserve direct --seed OR UTF8 file OR '-' stdin. An already-resolved-string
  launcher cannot recover a filename its caller omitted.

- [ ] **V.954.r0-check-96:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: D2. Preserve mutual-exclusion,
  existing shared size limits, explicit read/validation errors and genuinely empty-input None
  semantics. An unreadable file must not silently become an empty prompt. Keep exact attached
  argv/result construction and model/effort/context choices.

- [ ] **V.954.r0-check-97:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: D3. Preserve target locks and
  reserve/connect/release, trust/permission policy and restricted-profile refusal. Detached
  Container and attached CodeSpace callers remain positive controls, not additional broken paths; no
  change to source/plugin preparation, discovery or P3 lifecycle/resource/idle-restart policy is
  implied.

- [ ] **V.954.r0-check-100:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: DV1. Exercise actual attached
  parser-to-reader-to-launch argv/result behavior for direct, file/stdin, conflicting inputs,
  existing limit, unreadable and empty cases, plus detached Container/attached CodeSpace parity
  controls, using bounded fixtures without live SSH/provisioning as the original focused acceptance
  requires.

- [ ] **V.954.r0-check-101:** Verify the COMPLETE original/refined clause at its ORIGINAL finite
  scope, alternatives, venue, metrics and evidence tier; preserve completed credit and SAME
  source/resource custodians, no new everyhost/whole-parent/ACK gate: DV2. Retain original
  applicable fresh/live native/installed CLI-mode qualification with its existing owner. The
  historical source-pure accepted-input audit and5931 docs-only record are not new execution or
  implemented fix;7157 plugin-preparation ACP/CLI proof is real but not this seed-file consumer. No
  every-venue/new whole-parent native matrix is added.

- [ ] **G954:** Resolve every directly retained #954 Plan and Validation clause at its original
  finite evidence tier, preserving alternatives, completed credit and existing custodians.
  Administrative tracking is not technical completion or claim release.

## Notes / Gotchas

- Coordinate WS1 with whatever stream is building launch-parity fixes so the
  logic lands **once, in the core**, not twice.
- Respect the anchor-write / worktree rules: plugin work in worktrees,
  PR-required, self-merge.
- Vendored-lib sync guard applies if shared logic lands in a `libs/<lib>` used by
  ≥2 plugins (bump every consumer).

## Coordination

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Continuing source-specific Transport/Bridge custody

#### Source #954

#954 remains OPEN as the live whole-programme token, with P0-P5,
all inherited #2042/#2094/#1082/#5926 and existing #2476 acceptance and
actual P3 reservations intact. Preserve the existing migration-intake
deduplication/ownership contract. #4227 remains independently OPEN under its
specific affected-scope mapping condition; #566 SSH/elevated host survival
and #109 credential-disconnect acceptance are not absorbed. Shared
Bridge/core, provider/relay, permission/session-rescue, CLI seed-reader and
native custodians remain unchanged. The literal keep-open condition is not
a generic Taking/ACK rule, and document intake cannot remove it.



## Journal

- **2026-10-22 — Retained trusted-container initialization acceptance.**
  Materialized #2476's complete bootstrap-order, helper-discovery, partial-state,
  same-member retry and native negative coverage in this root. Existing P0/P3,
  relay/lifecycle owners, migration intake and #954 coordination continue.
  Normal reviewed merge and current full readback precede source tracker
  supersession; implementation and qualification remain unfinished.

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
- **2026-08-26 — P2 executable parity harness foundation.** Agent-bridge dev363
  adds `agent-bridge parity <container:|codespace:>`: one isolated session runs
  a shell-side redacted cwd/auth probe, reports already-loaded repo capabilities,
  waits for a real `turn_complete`, verifies ACP continuity and a live child
  across explicit stop/resume, completes another turn, and cleans up in
  `finally`. Credential subprocesses are captured inside Python and emit
  booleans only; the harness rejects credential-shaped durable event content.
  Expectations remain host-side, streamed agent chunks are accumulated, busy
  one-session venues fail cleanly, and failed starts are cleaned by a unique
  caller id. **Live gates:** a trusted container passed workspace, repo
  capability, GitHub, ADO Git, Azure, resume, and second-turn checks; a fresh
  CodeSpace passed workspace, repo capability, resume, and second-turn checks.
  Both emitted structured redacted JSON and left no session behind. Explicit
  idle stop may recreate the child while preserving ACP identity, so same-child
  PID is correctly reserved for the forthcoming frontend-loss/HostIndex
  scenario. **Next:** add destructive P2 scenarios for frontend restart +
  HostIndex loss, relay interruption, failed handshake rollback, and venue
  stop/recreate.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### 2026-10-28 - Additive Transport/Bridge tracking contracts

Authored direct finite Plan and actual global Validation with continuing

custody after complete current receiving-document and source-contract

research. Normal review, actual merge and complete current readback remain

separate publication gates; no future delivery or disposition is claimed.

Preserved recorded SSH/relay/Host, P0/P1 and finite P2 source/live credit. The historical recreation failure at 15 minutes and success at a changed 40-minute ceiling remain distinct, not an unchanged-bound pass or a new universal timeout.
