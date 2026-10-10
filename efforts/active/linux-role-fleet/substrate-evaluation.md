# Linux role fleet substrate evaluation

Back to [the effort](README.md). **Proposal and evidence, not an installed fleet.**
Evidence reviewed 2026-10-10. Operator selected Komodo/Periphery first,
SSH/Compose as the static alternative, and Swarm deferred.

## Criteria

Compare repeatable first adoption, footprint, placement, credentials, existing
service lifecycle, outage recovery and portability. A low container count is not
proof of low operator effort. Scriptable API names are not a fresh-install receipt.

| Substrate | First adoption | Placement and operation | Main proof obligation |
|-----------|----------------|-------------------------|-----------------------|
| Komodo Core + Periphery | Compose deployment, configured initial admin, onboarding API and Periphery setup appear scriptable | Explicit server/stack placement, resource synchronization and external deployment control plane | Prove first admin to authenticated onboarding without browser steps; verify rerun, custody and supported release-specific APIs |
| SSH + Docker Compose | Docker/SSH host prerequisites and explicit per-command contexts; no added central manager | Per-host desired deployment on invocation; no cross-host scheduler or manager failover | Define adapter-owned receipts/reconciliation without inventing a scheduler or claiming native rollback |
| Docker Swarm | Scriptable manager init/worker join, but selected addresses, trusted network, quorum and credentials remain inputs | Native scheduling, replicas/global tasks and rolling update/rollback | Quorum/network/legacy stack-schema compatibility and stateful storage/fencing; not required for the initial pinned fleet |

## Komodo evidence

- [Official setup](https://komo.do/docs/setup/mongo) describes Compose-based Core
  deployment with a database. The [official environment template](https://github.com/moghtech/komodo/blob/main/compose/compose.env)
  exposes `KOMODO_INIT_ADMIN_USERNAME` and `KOMODO_INIT_ADMIN_PASSWORD`. This
  suggests first-admin setup need not be browser-only; verify against the pinned
  release rather than treating mutable `main` as a deployment artifact.
- [Server connection](https://komo.do/docs/setup/connect-servers) documents
  Periphery setup with explicit Core address and onboarding credentials.
  [CreateOnboardingKey](https://docs.rs/komodo_client/latest/komodo_client/api/write/struct.CreateOnboardingKey.html)
  is a supported API surface. Exact local-login/API-key acquisition payloads
  still need release-specific verification and a real fresh-install trace.
- [Resource sync](https://komo.do/docs/automate/sync-resources) describes declared
  resource synchronization. Trigger/apply behavior must be configured explicitly;
  a Git declaration alone is not proof of active convergence.
- [Auto update](https://komo.do/docs/deploy/auto-update) is container update
  behavior, not a guarantee of the hosted service's safe drain or data rollback.
  Prefer pinned artifacts and explicit lifecycle admission for stateful roles.
- [Secrets/variables](https://komo.do/docs/configuration/variables) distinguish
  Core and host-local secret scopes. Do not turn its variable store into the
  fleet's credential issuer. [Backups](https://komo.do/docs/setup/backup) require
  explicit confidentiality, consistency and restore validation; exported database
  content cannot be assumed encrypted or safe to publish.
- No verified multi-Core HA or generic rollback guarantee is relied on here.
  Manager outage must leave running roles independent and outcomes stale, not
  successful. These are explicit experiment questions, not claims of absence.

## Static SSH/Compose evidence

- [Docker contexts](https://docs.docker.com/engine/manage-resources/contexts/)
  select one daemon. Use explicit per-operation context/endpoint selection, never
  mutate the operator's global current context to target a fleet.
- [SSH daemon access](https://docs.docker.com/engine/security/protect-access/)
  supplies authenticated host transport. Ordinary Docker socket access is highly
  privileged; [rootless mode](https://docs.docker.com/engine/security/rootless/)
  genuinely runs daemon and containers without root, with separate prerequisites.
  Do not describe all Docker deployments as either unprivileged or always-root.
- [Production Compose](https://docs.docker.com/compose/how-tos/production/)
  describes single-server deployment. Multi-host role mapping, reconciliation
  receipts and upgrade sequencing remain explicit adapter work.
- [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) describe
  file-backed delivery. Live rotation/reload behavior depends on file replacement
  and application semantics and needs a real test; no blanket automatic rotation
  or mandatory-recreation claim is made.
- Rollback means reapplying a verified compatible prior role artifact under the
  service's lifecycle/data contract, not a presumed Compose rollback primitive.

## Swarm evidence and deferral

[Swarm](https://docs.docker.com/engine/swarm/) remains documented Docker Engine
functionality. [Service operations](https://docs.docker.com/engine/swarm/services/)
provide placement and reconciliation; [service create](https://docs.docker.com/reference/cli/docker/service/create/)
documents update/rollback controls. [Manager quorum](https://docs.docker.com/engine/swarm/how-swarm-mode-works/nodes/)
and [network setup](https://docs.docker.com/engine/swarm/swarm-tutorial/) are
real operating obligations. [Stack deployment](https://docs.docker.com/engine/swarm/stack-deploy/)
uses the legacy Compose v3 stack format, not every current Compose feature.
Local volumes are not automatically portable across nodes. Swarm is deferred
because the approved initial experiment is repeatable, pinned role deployment,
not automatic cross-host scheduling or storage migration.

## Proposed integration boundary

1. Inspect the explicitly selected existing manager/hosts and report capabilities.
2. Plan named, trusted role deployments with budgets, storage, credentials and
   activation prerequisites. Do not accept arbitrary remotely authored commands.
3. Apply only after approval, through supported manager/host operations; retain
   per-target receipts and reconcile uncertain replies against that authority.
4. Read installed/ready/converged states from the role's real process and service
   contract. A container exists or manager accepted a request is insufficient.
5. Keep capacity/private provisioning and source/credential trust downstream.
   Keep Gateway route/enrollment implementation in #5789 and desired deployment
   intent in #5861/#6030. Existing services own their work, data and shutdown.

The first experiment must falsify the claimed headless Komodo path on a clean
environment: approved capacity, pinned Core/database/Periphery artifacts,
provider-injected initial credentials, authenticated API access, one enrolled
target, repeat without duplication, failure/recovery and owned teardown.
Missing credential acquisition or an unsupported automation API is an explicit
blocker, not permission to use browser automation or expose secrets.
