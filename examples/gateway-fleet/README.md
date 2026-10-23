# Gateway and SSH mesh prototypes

Experimental reference source for an authenticated Gateway/token service and
an outbound-initiated SSH relay mesh. Published so the implementation and its
tests can be read, discussed and reused before the production integration is
complete.

**These are prototypes, not another supported Fleet Manager installation path.**
Nothing here is enabled, installed, registered or started by the marketplace.
Do not point them at an existing deployment. State formats, fixed sample peer
sets, enrollment, packaging and update contracts are experimental. Publication
does not grant access, authorize credential sharing or qualify a live migration.

## Contents

| Directory | Reference implementation |
|-----------|--------------------------|
| `token-gateway/` | Signature-verified Entra JWT Gateway with explicit tenant, audience and role checks; bounded index/Chat proxies; cached credential push/relay; producer/coordinator/consumer token flow |
| `ssh-mesh/` | Native OpenSSH forward-key enrollment and Windows non-admin reverse connector, plus the earlier Docker/Chisel relay experiment; owned process cleanup, startup and status primitives |

Names, identifiers, usernames and peer sets are synthetic examples. Azure,
Entra, OpenSSH, Chisel and their public APIs are public dependencies, not
organization-specific provisioning systems. Internal provisioning adapters,
operator loadouts, deployed host inventories, keys, state, private history,
cutover/restore automation and historical deployment reports are not included.

The sample mesh has five fixed peers (`node-a` through `node-e`), prefixed with
`operator-` in canonical aliases, and uses the sample user `operator`. These
remain deliberately constrained prototype policies, not a general fleet schema.
The `target_id` field is an example VM identifier, not an internal provisioning
API. Supply your own approved configuration and independently verified pins.
Do not reuse a sample key, assume a host is enrolled, widen forwarding grants or
disable host verification to make an example work.

## Configuration

The Gateway reads `HGF_TENANT_ID` and `HGF_APP_ID`. Their synthetic defaults
cannot describe a real configured service. It retains RS256 signature, issuer,
tenant, audience, expiry and dedicated `GatewayClient` role checks.
Its HTTP listener binds only `127.0.0.1`; remote use requires an explicitly
protected encrypted transport. A bearer token does not encrypt HTTP traffic.
`HGF_GATEWAY_RESOURCE` selects the producer's application resource;
`HGF_SSH_USER` and `HGF_SSH_IDENTITY_FILE` select an existing approved SSH
identity. No script supplies an application registration, role assignment,
interactive login, permission grant or credential value.

The coordinator's optional `HGF_CONFIG_PATH` selects a JSON file containing
`{"consumers": []}` or an explicitly approved consumer list. Missing
configuration does not discover or enroll hosts. Treat credential relay as a
sensitive, separately authorized effect; synthetic tests are not authorization.
The token service requires PyJWT and cryptography as listed in
`token-gateway/requirements-gateway.txt`. Its retained Vault-cache subset is
MIT-licensed reference code, not a new maintained fork.

The example stores token and expiry together in a single encrypted `credential`
field. It deliberately does not combine legacy `value`/`expires_epoch` fields
or claim cache compatibility with a deployed prototype. Newer expiry determines
the ordering value across coordinator restarts. The coordinator's existing
process owns one bounded delivery worker, retrying pending consumer deliveries
without minting a new token; it joins that worker on shutdown. Gateway request
validation refuses malformed fields before cache access. Both proxies interrupt
their exact owned socket at the absolute response deadline, including slow
status/header reads; these request-owned timers are cancelled and joined.

The Windows connector requires an explicit JSON-subset YAML configuration,
existing authenticated Azure CLI, approved Azure Bastion/VM resources and a
separately enrolled SSH identity. The checked-in `windows.yaml` and
`gateway.yaml` contain only synthetic example configuration. Pass your approved
file explicitly rather than attempting to deploy those sample identities.
`install.ps1` refuses elevation and session0. Inspect its effects and ownership
checks before any deliberate experiment. No provisioning or live installation
was exercised by publishing this source.

## Reading and testing

Start with `token-gateway/gateway_server.py`, `coordinator_daemon.py` and
`producer_push.py`; then `ssh-mesh/gateway_openssh.py` and `windows.py`.
Tests use generated test keys, synthetic identities, temporary directories and
local fixture servers. Run the suites separately because their script-loading
module names are intentionally local to each prototype.

Use the repository's contained-test machinery described in `TESTING.md`;
do not run tests with live credential homes or service directories. These tests
are not evidence of real OpenSSH authorization, Azure reachability, multi-host
availability, installed release compatibility or production-ready security.

## Relationship to the supported build-out

The canonical integration remains
[Gateway/SSH onboarding](../../efforts/active/gateway-ssh-fleet-onboarding/README.md)
under ThomasMichon/copilot-extensions#6681. Chat has its own
[graduation effort](../../efforts/active/agent-chat-graduation/README.md) and
`services/chat/` implementation. The supported Fleet Manager/agent-ssh,
credential, service-lifecycle and Chat owners remain authoritative.
This reference snapshot neither replaces them nor reserves their source.

The imported Windows connector remains one 1,820-line reference module. This
source-only import proposes that exact shrink-only ceiling in the repository's
module-size baseline for review; it does not relax the cap for other files.
Production adoption should componentize it through the owning integration
effort rather than expand this frozen example into another product.
