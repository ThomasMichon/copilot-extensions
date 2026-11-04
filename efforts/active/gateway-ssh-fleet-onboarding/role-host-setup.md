# Ordinary selected-role Gateway SSH setup

Parent: [Gateway and SSH Fleet Onboarding](README.md), #6681.
This is an **agent-recommended source proposal**, not an executable interface,
source allocation, installation or deployment grant.

## Intent and existing authorities

Realize [thin hosting wrappers](../../../visions/machine-fleet/gateway/README.md#thin-hosting-wrappers)
and the existing Machine Fleet role-selective lifecycle through one normal
setup path. An adopter explicitly selects Gateway or SSH participant roles;
ordinary client installation does not enroll a machine or host a service.
Provider acquisition and identity bindings remain external adapters.

Retain #6030/#6240/#6424 for substrate, release, construction, admission and
lifecycle; routing/authentication/enrollment and the operation journal keep
their existing authorities. Agent-ssh remains the profile and target-login
writer. Existing first-touch enrollment and Manager projection delivery under
#9047/#9053, the continuity plan and #8860 substrate work are dependencies, not
reimplementation assignments. No shared source path is reassigned by this plan.

The [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
contract governs explicit installation and update: incoming readiness and
complete current selections define success. Best-effort incumbent recovery,
handoff and cleanup are not permission prerequisites for the incoming runtime.
State/data compatibility is required before admitting mutable serving work;
user configuration, credentials, sessions and accepted work survive.

## Actual seams and missing composition

Source inspection distinguishes `plan_role_deployment` and `verify_role_plan`
from installation; their configured projection supplies no activation grant.
`construct_client_release` and `activate_client_runtime` operate on the client
closure, not arbitrary hosted roles. `serve --role gateway|ssh-client`,
`SSHDirectoryClient.watch`, `ssh_machine.run` and the SSH service provide
foreground execution/cancellation behavior, not an installed host lifecycle.
Their source/API presence is not an admitted role artifact or release.

The missing composition must extend the existing role construction/lifecycle
owner. Do not copy client selectors into a second host installer, turn a
plan into effects, add a per-session watcher, or invent an available generic
constructor that the implementation does not supply.

## Inputs and ownership contract

Before effects, the owning flow must resolve:

- The selected installation and authority, logical machine/environment,
  Gateway/controller, role and generation; client-only and disabled roles are
  explicit negative selections.
- The exact admitted release manifest and complete role/platform/interpreter
  closure, integrity/provenance, compatible API/config/data generations, and
  the owner-authorized construction and activation decision.
- Validated owner-controlled configuration and credential references; source
  parsing, a configured endpoint or fleet membership grants no enrollment,
  issuance, signing or deployment authority.
- A named operation under the existing journal, its target/generation/input
  binding, current approval and original uncertain-effect recovery correlation.
- The installed runtime, durable state, stable launch/discovery and registration
  ownership required by the existing lifecycle. No registration or resident
  descendant may depend on a source checkout, plugin payload or session folder.

These are required semantic inputs, not new DTO/schema names or a proposed
second authority store. Secrets remain with the credential owner and do not
enter argv, logs, receipts, desired state or the role artifact.

## Lifecycle behavior

`install` and `update` converge the explicitly selected configuration through
the same lifecycle. They construct an independently healthy incoming role
generation and establish its complete current selections. Compatible handoff
and data-safe rollback use the existing owner; unsupported old handoff cannot
block authoritative replacement. Report genuine incoming construction, state
compatibility or readiness failure without calling it a completed install.

The lifetime owner starts the installed foreground consumer with attributable
instance/process custody and explicit availability policy. It supplies stop
and cancellation, waits for the watch and owned callbacks/workers to finish
before tearing down their dependencies, and reports a failed or uncertain
stop rather than false shutdown success. Synchronous directory acceptance
finishes the existing local writer before advancing its acknowledged cursor.
There is no new SSH controller, arbitrary executor or secondary supervisor.

Repeat unchanged setup reuses verified state without duplicate registrations,
enrollment or watchers. Interrupted effects reconcile through the original
operation and their actual owner before reissue. Installation, connected,
enrolled, profile-converged and authenticated route-ready remain distinct.
The installed consumer recovers on startup/reconnect; a departing interactive
client neither terminates its adopted role nor acknowledges unfinished work.

Explicit role withdrawal and uninstall use their existing owners and protect
then-current selections and real machine-management connectivity. Old
central-runtime retirement is independently identity-scoped. Configuration
removal, expiry or a new version marker does not authorize deleting foreign
resources, user state or a still-required access route.

## Ordered implementation

1. Review this amendment and reconcile the current selected artifact and shared
   host-construction APIs with their owning plans; record the exact missing
   executable seam without taking another author's open change.
2. Extend the existing constructor/lifecycle for one explicit Gateway-SSH role
   closure and its inert client-only negative case. This is the first bounded
   executable slice, not complete provider provisioning or fleet enrollment.
3. Compose the installed SSH directory consumer and target-local writer through
   that lifetime, including startup catch-up, stop/join and precise failures.
4. Wire normal setup to the already delivered first-touch enrollment and
   configured provider adapters; separate attended authentication from
   noninteractive preparation and preserve independent standalone operation.
5. Qualify actual published artifacts and supported normal installation before
   separately authorized provider/live adoption or predecessor migration.

## Validation and completion

- [ ] Unit and real consumer tests prove exact role/identity/current-input
  admission, no effects for client-only/disabled/unsupported selections, and
  no credential/profile ownership transfer.
- [ ] Non-editable role-selected installed closure runs with source/payload
  reads denied; a source-import negative control fails as expected.
- [ ] Fresh Windows/Linux setup, no-op repeat, explicit update, candidate
  failure, compatible state-preserving recovery and stop/uninstall exercise
  the actual owner contracts, not only mocked callbacks.
- [ ] Real process cancellation waits for watch/callback completion; failed
  stop and lost results remain explicit, with no premature acknowledgment or
  unattributed survivor. Test instances have finite owner-managed lifetime.
- [ ] Incoming generation can become ready without predecessor cooperation;
  complete selection, state compatibility, unrelated sessions and actual
  management access survive. Retirement cannot target the current generation.
- [ ] Initial and later-machine enrollment, reconnect/catch-up, stale/revoked
  grants, offline/auth-needed conditions and canonical SSH identity/exit/EOF
  are qualified through the real integration when authorized.
- [ ] Source, release, installed selection and live proof name their actual
  revisions and outcomes. Each unit, simple E2E, clean-room and available
  authorized live tier records its evidence or an explicit skipped reason.

A reviewed plan, running foreground process, thin-client installation or
successful prototype is not completion. The parent effort remains active
until its own Plan and Validation Plan are resolved.
