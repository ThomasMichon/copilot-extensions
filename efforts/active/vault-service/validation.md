# Vault Service - Proposed Validation and Acceptance

Back to [the effort](README.md) and [architecture](architecture.md).
**Agent-recommended validation design. Every runtime result is pending.**
This document defines proof obligations; writing it, passing documentation CI
or merging the plan does not satisfy them.

## Qualification levels

| Level | What can be established | What it cannot substitute for |
|-------|-------------------------|-------------------------------|
| Documentation/contract review | Intent capture, source references, ownership and complete acceptance design | Any running service or provider compatibility |
| Unit/simulated | Pure bindings, lifecycle transitions, policy, injected clock/provider faults, UI rendering | Actual persistence/process isolation, native login or real installed behavior |
| Isolated process | Real ledger/IPC, independent clients, crash/restart, coalescing and replacement with synthetic credentials | Vendor acceptance, WAM, issuer policy or production custody |
| Native platform | Actual Windows GCM with WAM selected and actual native Entra adapter, native stores/user sessions; real supported browser/device flows | Another platform/provider or live adopter qualification |
| Installed | Selected service distribution, native supervision, container/storage, standard loadout, Gateway route and rollback | Account access or consumer usability inferred from health alone |
| Authorized live adopter | Correct bound credential use/readiness and scoped migration/recovery in an explicitly admitted deployment | Broad rollout, other identities or unrelated consumers |

Native/account qualification requires explicit operator permission, appropriate
test accounts/resources and selected device/session. No browser, GCM, WAM,
Entra login, credential mutation or live cutover is permitted in planning.
Unsupported/unavailable platforms stay visibly unqualified, not mock-passed.
For GCM, a successful Git helper invocation or browser fallback does not prove
WAM was used; verify the actual broker-backed path without logging secrets.
Qualify Windows-native Entra separately rather than substituting GCM for it.

Future pytest jobs must use the executing harness's bounded test supervisor
and this repository's selected test runner/lane, never raw pytest. Keep fast
pure contracts in required unit CI; select process/native/live lanes explicitly
under the [test portfolio](../../../TESTING.md). Planning-only validation uses
existing structure/documentation/link/whitespace guards, not runtime suites.

## Acceptance obligations

Each V identifier is a completion gate corresponding to the README checklist.

| Gate | Phases and tests | Required observations, including negative cases |
|------|------------------|------------------------------------------------|
| V1 - Contracts and credential lifecycle | P1-P2; unit, isolated process and later provider qualification | Bind provider/account/tenant/resource/audience/scopes and consumer/device/session; acquire/cache/deliver-or-use/renew/expire/revoke; reject broader scope, wrong consumer, wrong provider, expired grants and unsupported issuer revoke. Denied/revoked access cannot survive cache reuse. Nonexportable use never fabricates exported tokens. |
| V2 - Durable coordination | P1-P2, P5; unit plus actual process/crash/replacement | Accept before acknowledgement; duplicate intake and lost reply reconcile one request. Race coalesced requests, per-consumer cancellation, attempt generation, callback replay, expiry, replacement and provider revocation. Restart at each effect boundary; old pollers/callbacks cannot reauthorize or report false success. Unknown effects are not replay permission. |
| V3 - Browser/device flow | P4; simulated adversarial cases plus real supported browser/PKCE and device-code tests after permission | Discovery/list/refresh never launches. Explicit action launches on selected host; wrong callback/attempt/device/account/tenant/resource/scopes and reused callback fail. Preserve PKCE/callback custody and cancellation uncertainty. Public device-code instructions can display; secrets/authentication codes cannot. |
| V4 - Actual Windows native | P4; real Windows WAM-backed GCM and native Entra, explicitly admitted accounts/session | Demonstrate correct provider and native user/session identity, actual broker use, account selection, denial, cancellation, expiry/renewal/revocation and consumer readiness. Wrong/logged-out/unavailable session and central-container desktop assumptions fail honestly. Prove nonexportable authorized use where applicable without token extraction; no native capability claimed from mocks. |
| V5 - Stateless presentation | P5; headless Manager tests, CLI process and native attended UX | Accumulated requests visible after UI replacement. Refresh, Dismiss, Escape, window close, runtime close and killed observation CLI preserve Vault work; explicit Cancel reaches only admitted request/attempt/consumer. Test two coalesced consumers and a superseded attempt. Transport/client exit or `done` never becomes authentication success. Existing non-Vault action cancellation still works. |
| V6 - Service construction | P3; isolated and installed local/container plus platform-native tests | Cold install/ready/update, compatible handover, bounded cold recovery, failed candidate, service loss, state-compatible rollback and owned uninstall preserve protected state and one writer. Windows, Linux/WSL and macOS local operation has no Fleet/Gateway/Manager/Copilot dependency. Container has persistent storage, qualified architecture and no assumed desktop credentials. |
| V7 - Fleet standard profile | P6; contract tests and separately authorized installed/live proof | Selected standard loadout actually includes the central Vault container, admitted route, storage and readiness. Existing deployment manager owns create/replace/stop, not Manager. Deny unregistered consumers, wrong service namespace, untrusted connectors and fleet-member-only credential access. Bootstrap/recovery works without calling the unavailable Vault being installed. |
| V8 - Trusted federation | P7; isolated multi-service and admitted live deployments | Local-independent, local-to-central and peer modes retain exactly one lifecycle/transaction owner. Partition, stale/delayed/replayed messages, offline budget exhaustion, revocation/trust expiry/withdrawal and reconnect cannot create competing writers or widen access. No automatic credential mirror; local-owned valid use differs from unreachable remote-owned work. |
| V9 - Adopter migration | P8; generic isolated migration fixture, then explicitly scoped adopter | Preserve pending/coalesced/cancelled requests, credential handles/expiry, history, consumer bindings and native execution authority. Fail preparation/transfer/activation, lose replies and restart both sides. Reversible cutover retains one writer and compatible history without resurrection or blind reauthentication; incompatible rollback stays blocked safely. |
| V10 - End-to-end and privacy | All phases; cross-level acceptance ledger | A real admitted consumer confirms correctly bound readiness; approval/provider success alone fails. Scan UX, CLI presentation, transaction history, diagnostics, telemetry, images and environment for synthetic secret/auth-code canaries. Record platform/provider coverage and source/release/installed/live evidence separately; unavailable evidence is not success. |

For V1/V4, issuer revocation may invalidate access without a Vault request.
Exercise reconciliation of that event as well as explicit revocation requests.
For V2/V5, cancellation of one shared consumer must leave a continuing consumer's
need alive; cancellation acknowledgement cannot falsely prove an external
browser/native effect stopped.

## Complete vision coverage

The canonical [vision](../../../visions/vault/README.md) remains unchanged by
this plan. These links cover every stable feature and behavior heading.

| Vision item | Plan | Validation |
|-------------|------|------------|
| [credential-lifecycle-authority](../../../visions/vault/README.md#credential-lifecycle-authority) | P1-P2, P4 | V1, V4, V10 |
| [durable-client-independent-transactions](../../../visions/vault/README.md#durable-client-independent-transactions) | P1-P2 | V2, V5 |
| [explicit-browser-and-native-interaction](../../../visions/vault/README.md#explicit-browser-and-native-interaction) | P4 | V3, V4 |
| [stateless-manager-surface](../../../visions/vault/README.md#stateless-manager-surface) | P5 | V5, V10 |
| [central-fleet-and-standalone-local](../../../visions/vault/README.md#central-fleet-and-standalone-local) | P3, P6 | V6, V7 |
| [explicitly-trusted-federation](../../../visions/vault/README.md#explicitly-trusted-federation) | P7 | V8 |
| [discovery-never-starts-a-ceremony](../../../visions/vault/README.md#discovery-never-starts-a-ceremony) | P1, P4-P5 | V2-V5 |
| [preserve-target-and-access-bindings](../../../visions/vault/README.md#preserve-target-and-access-bindings) | P1-P2, P4, P7 | V1-V4, V8 |
| [readiness-is-not-approval](../../../visions/vault/README.md#readiness-is-not-approval) | P1-P2, P4-P6 | V1, V3-V5, V7, V10 |
| [dismiss-is-not-cancel](../../../visions/vault/README.md#dismiss-is-not-cancel) | P1-P2, P5 | V2, V5 |
| [secrets-stay-out-of-presentation](../../../visions/vault/README.md#secrets-stay-out-of-presentation) | P1-P8 | V1, V3-V5, V9-V10 |
| [preserve-authority-through-replacement](../../../visions/vault/README.md#preserve-authority-through-replacement) | P2-P3, P7-P8 | V2, V6, V8-V9 |

Non-Goals are cross-cutting acceptance constraints: no general password-manager
UI, mandatory central dependency, Manager-owned authentication/lifecycle,
automatic ceremony, arbitrary execution, duplicate authority or implicit
redistribution. V1/V3-V9 include corresponding negative cases.

## Evidence and completion

Implementation owners record selected source/release identities, reproducible
safe test flows and actual level/platform/provider results in the effort's
Journal or linked owner records. Public records stay generic and content-free;
private account/custody details and raw secrets never become public artifacts.
Do not require publication of sensitive logs or evidence as an approval gate.

Documentation review can clear the planning publication, not V1-V10.
After explicit implementation admission, each phase must name its remaining
acceptance level. A mocked provider, approved PR, compiled image, connected
Gateway slot or healthy service is not consumer readiness. Whole-effort Done
requires all Plan/Validation items resolved or explicitly transferred to a named
tracked objective, with existing adopters and remaining owners preserved.
