# One incoming installation path

[Canonical effort](README.md) |
[Installer intent](../../../visions/installer/README.md#ordinary-update-completion) |
[Application contract](../../../docs/patterns/fast-application-install-and-cutover.md)

This is the delivery map for the existing effort, not another installer,
implementation claim or completion ledger. Fresh installation and update
construct the same incoming installation. Incumbents affect compatibility and
state transfer during activation, not acquisition or construction.

## Request and scope

The current direction is to unify outstanding relevant efforts around that
vision. The central clarification, verbatim:

> "update" is just "fresh install" but the incoming versions observe that existing versions are already present and attempt state transfer.

Both entry routes obtain the trusted upstream bootstrap, honoring its existing
environment override, carry explicit repository context, resolve and shallowly
acquire the selected source/ref, and establish Manager's complete UV application
first. Installed Manager then derives the required repository/dependency and
downstream-installable closure. Complete projects are staged before that
required set enters activation.

These are required outcomes, not proposed universal command spellings.
Use each declared application's supported stage and activation entries;
an internal setup callback is not a new portfolio API. Repository adoption
and Manager/service source selection remain distinct explicit inputs.
No new optional role, automatic maintenance enrollment or hosted resource
is selected merely because the application inventory lists it.

## Dependency-ordered delivery and receiving owners

| Observable outcome | Existing receiving owner | Evidence before progressing |
|---|---|---|
| Fresh and update entries acquire one authentic selected source and establish Manager first, without executing the old installation's constructor. | This effort W1/P4a/P4b; existing fresh-entry implementer; [Manager control plane](../worktree-manager-control-plane/README.md), #7743 and #8545/#356. | Actual public entry consumers for absent and existing installations, source override and repository context; identical construction selection and explicit acquisition failures. |
| Manager resolves the addressed repository's required apps, dependencies, profiles and downstream installables. | Existing Manager onboarding/setup owners and this effort V2; supplementary-installable declarations remain repository-owned. | A finite desired inventory with dependency edges, supported roles/profiles and original configuration/data roots; no implicit optional activation. |
| Each wanted app has complete copyable project, lock, wheels/resources, local commands and a physical environment in its immutable slot. | [Cached Source Wheel Assembly](../cached-source-wheel-assembly/README.md), [core composition](../runtime-bundle/README.md), [lock inputs](../release-lock-inputs/README.md), #4876 artifact admission and this effort's existing constructor integration. | Selected-source/profile association, complete admitted closure, unchanged-input reuse and no installed dependency on the source checkout. A bundle, venv or package receipt alone is not the whole footprint. |
| Every required app finishes staging before any member of that required set activates. | This effort W2/V5 and [Resilient Update Sequencing](../resilient-update-sequencing/README.md) Phase3; [shared engine](../vendored-installer-engine/README.md) and each app's existing adapters. | Stage consumer creates the complete footprint while live data, selectors, commands, registrations, services and incumbent work remain unchanged. A failed stage prevents activation; successful prepared peers remain reusable. |
| Incoming apps check compatibility, transfer supported state, start and cut over through their existing owners. | Existing app lifecycle/data owners and W2/V5/V6; [upgrade acceptance](../installer-upgrade-recovery/README.md). | Real installed command/serving identity and complete current selection with preserved user state; fresh/existing cases share construction. Honest partial/unknown results and failed-candidate recovery stay visible. |
| Ordinary execution follows its caller; accepted service startup survives that caller, with correct manual/automatic behavior. | #7758 [execution driver](execution-driver.md), existing service/maintenance owners, [Update Flow Observability](../update-flow-observability/README.md) and #6820 sequencing. | [Lifetime acceptance](update-service-lifetimes.md#validation-and-completion): cancel finite updater work, preserve independently accepted service work after caller/ancestor cleanup, one writer, durable honest results and no uncertain replay. Queue admission or progress is not installation success. |
| Outgoing central versions retire independently; protected sessions and connectivity survive. | Existing lifecycle/adopter owners, #9716/#10653/#10486 and [upgrade acceptance](../installer-upgrade-recovery/README.md). | Then-current generation/role and native identity qualify retirement; no keeper/client termination or predecessor-exit fence on incoming success. Unknown cleanup remains reported. |
| Source acceptance becomes a release and then separately authorized installed acceptance. | This effort V10-V12, existing release owner and addressed deployment/adopter owners. | Required review/CI, actual source merge and containing release, then the actual installed inventory's readiness and preservation. No source-only or documentation-only rollout claim. |

The stage barrier is not a claim of atomic multi-app selector publication.
App-owned private candidate readiness may use the existing passive/ownership
contract; it must keep live work admission closed and perform no live
publication, handoff or shared-data initialization/migration. This does not
permit activating an incoming live service before the required set is staged.
Activation still uses the declared compatible strategy and dependency ordering;
qualify an atomic selection contract only when that strategy actually requires it.
Independent app contributions may land separately, but a full addressed-suite
run cannot activate an earlier member while another required member is un-staged.

## Retained implementation and evidence

The original constructor integration #11121 merged with its history preserved.
Its [qualified source and containing release](<journal/2026/11.13 canonical-application-integration.md>)
are credited; they do not prove default entrypoint adoption or whole-inventory
acceptance. Consume the existing producer instead of rebuilding it.
The same record retains #12176's merged native receiver and #10570's separately
owned prepared-Worktrees consumer. Recheck their current source and claims
before an overlapping contribution; this map does not close or reassign them.

The [per-app matrix](wheel-first-contributors.md#per-app-tracking) retains
bounded app/platform evidence. [Latest Waiting Update](../latest-waiting-update/README.md)
is Done for its bounded scheduling source/release scope; it is not an unfinished
constructor to reopen or evidence that the whole incoming installation works.
Artifact input generation, core composition and cache work remain different
outputs, not three competing installation flows.

## Acceptance and ownership reconciliation

The canonical Plan W1/W2/P4b and Validation V2/V3/V5-V8/V10-V12 own this
integration. Contributing efforts keep their original Goals, checked evidence,
unresolved Plan/Validation items, review limits, source claims, operational holds
and completion owners. Their cross-links align outcomes; they are not accepted
transfers, automatic resumption of paused work or proof of Done.

Before declaring the integration complete, record a selected required inventory
and prove: fresh/existing entry parity; complete all-member staging; no live
effects before activation; failed-stage activation suppression; supported state
compatibility/transfer; actual incoming readiness/current selection; and preserved
data, sessions and connectivity. Retain release and separately authorized
installed evidence. Existing clean-room/native and resource limits still apply.

No new field installation, service restart, migration, cleanup, credential use,
maintenance enrollment or deployment authority follows from this alignment.
