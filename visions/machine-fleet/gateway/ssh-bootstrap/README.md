# Inbound-first Gateway SSH bootstrap - Vision

- **Subject:** Standalone first-SSH onboarding and single-enrollment membership convergence
- **Scope:** leaf
- **Status:** Draft
- **Last revised:** 2026-11-24

## Purpose & Intent

An operator with an adopted Gateway can make a new machine reachable before installing the rest of the harness there. From an existing configured controller, the operator names the machine and receives a portable handoff and one command for its user to run after login. Normal provider authentication remains attended. The result is independently verified inbound SSH, not a requirement to install the full coordination stack before the first connection can work.

A machine joins Gateway SSH once. Its admitted public identity becomes available to the other selected members through the Gateway-owned directory, without a manual exchange with every peer. Later full onboarding adds outbound profiles and capabilities using the same identity; it does not repeat machine admission or replace keys merely because more tooling is now installed.

## Concepts & Components

- **Preparing controller:** resolves the selected machine, Gateway, transport and local policy, and prepares the portable receiving handoff through existing owners.
- **Standalone inbound receiver:** carries only the native SSH, authentication and transport closure needed for its selected inbound role, with an independent supported lifetime.
- **Gateway membership directory:** the existing admission authority's durable publication of selected members, public client identities and host pins. Storage on the Gateway and its consumer views are one authority, not separately editable mesh inventories.
- **Converging consumers:** existing profile and target-login authorization owners apply admitted membership changes within each machine's selected direction and scope.
- **Later full participant:** adds the harness, Fleet controller/client role and agent-ssh profile capability required for outbound use without another Gateway onboarding.

## Features

### prepared-one-command-first-touch

The configured controller prepares the receiving command and supporting assets for the named existing machine. The handoff resolves required public configuration and genuine transport prerequisites; the user need not reconstruct provider identifiers, copy a peer's private key or install the full harness first. Storage and authentication providers remain replaceable.

### standalone-inbound-before-full-onboarding

The receiving machine can serve authenticated inbound SSH with only its selected transport and native login prerequisites. Copilot, a full Harness installation, a Fleet controller and the outbound profile manager are not first-SSH prerequisites. The minimum installed receiver may reuse their shared code and supported application machinery without activating those broader products or roles.

### single-admission-public-membership

Onboarding publishes the machine's locally owned public identities through the selected Gateway admission policy. The Gateway retains that accepted membership and exposes it to the selected consumers. Private keys and access credentials stay with their existing owners; a public-key file uploaded outside admission does not become a trusted member.

### automatic-inbound-and-outbound-convergence

Existing full participants discover admitted members and converge their permitted outbound profiles and target-local login authorization without per-peer edits. A minimal inbound receiver also keeps its permitted controller authorization current before full onboarding: a controller admitted later can reach an earlier SSH-only receiver. Public host pins, login identity and directional policy remain bound to the same admitted membership.

### later-outbound-without-readmission

When the operator selects full onboarding, the machine gains the supported Harness, Fleet and agent-ssh capabilities for outbound use. Those consumers catch up with the current directory and reuse the original machine identity. The inbound connection remains available through that transition; installing more tools does not rotate the machine's keys or require the peers to enroll it again.

### durable-across-attendance-and-controller-exit

The prepared handoff and its request correlation survive the preparing command's exit and a long human pause. Completing ordinary sign-in does not depend on the originating AI session, a temporary watcher or a second routine receiver command still being present. Bounded execution, loss of a reply and partial effects have a same-request continuation through their existing owners.

### honest-results-and-independent-proof

The bootstrap saves correlated preparation and receiving results in the handoff's supported durable destination. It distinguishes waiting for attendance or admission, partial configuration, receiver readiness and independently verified reach. A successful child exit, directory publication or receiver self-check alone is not proof that another authorized machine can connect after caller cleanup.

## Behaviors

### authenticate-once-converge-through-owners

The selected normal authentication and enrollment decisions apply once through their real authorities. A configured peer-approval policy remains effective; automatic convergence is not a second discretionary approval ceremony or a way to bypass the first one. Existing agent-ssh, service, installer, credential and native lifecycle owners retain their responsibilities.

### preserve-identity-and-unrelated-state

Retries and later setup preserve accepted keys, user identity, unrelated profiles and authorization, and existing generation and revocation decisions. Rotation is an explicit supported transition. An old handoff cannot restore revoked access, and an unavailable or incomplete directory is not an authoritative empty membership set.

### management-access-outlives-bootstrap-tooling

Inbound SSH and its required reconnect closure survive the bootstrap caller and cleanup boundary. Later supporting-suite removal preserves the declared machine-management transport. User-logon availability and a separately selected always-on service role remain distinct; first touch does not silently acquire a privileged machine-wide identity.

## Non-Goals / Boundaries

- No new compute creation, reimage or whole-machine custody transfer is implied by naming a receiving machine.
- No complete harness installation, outbound permission, arbitrary forwarding or extra service exposure is implied by inbound SSH.
- No parallel mesh registry, authentication issuer, key vault, generic TCP Gateway proxy or per-session supervisor replaces an existing owner.
- No particular cloud, organization, synchronized-storage vendor, filesystem layout, command syntax or public-directory wire schema is fixed by this vision.
- No prepared package, merged source or successful test authorizes production rollout or bypasses the selected machine, Gateway, credential or deployment policy.

## See Also

- Parent: [Declarative Fleet Gateway](../README.md).
- Connectivity owner: [agent-ssh](../../../plugins/agent-ssh/README.md).
- Fleet roles and adoption: [machine-fleet](../../README.md).
- Existing control plane: [SSH service](../../../../services/ssh/README.md).
- Existing consumer: [Fleet SSH directory convergence](../../../../fleet-manager/docs/ssh-machine.md).
- Existing local writers: [Gateway SSH transport and authorization](../../../../plugins/agent-ssh/docs/gateway-transport.md).
