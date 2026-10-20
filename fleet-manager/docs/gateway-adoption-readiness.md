# Gateway adoption readiness

Back to the [service-role Gateway](gateway-service-profile.md).
This is an adoption prerequisite and rollback record, not a deployment approval.
The [initial campaign](../../efforts/2026/10/19%20gateway-role-tunnels/README.md)
delivers reusable source and isolated proof; it does not enroll a live fleet.

## Qualified surfaces and open gates

| Surface | Evidence | Remaining owner/gate |
|---|---|---|
| Manifest, coherent policy and real identity verification | Reviewed source and retained-verifier/socket tests | Production configuration-source authority and provider interoperability |
| Base image and direct service channels | Native amd64 and ARM64 exact-source isolated container witnesses | No production sizing or managed deployment implied |
| External-origin ingress | Actual synthetic dual verified-TLS HTTP/WebSocket/SSE proxy and renderer tests | Supported cloudflared configuration validation and live edge proof under #7257 |
| ARM64 | Actual native Linux ARM64 image, installed wheel and ten assembled stages qualified | Target-specific capacity, distribution and deployment remain adopter-owned |
| Gateway-owned provider authentication | Common-profile JWT/DPoP and scoped caller grants | Actual Entra/Authelia login, token/proof interoperability and enrollment under #7257, coordinated with #5789/#6804 |
| Durable restart and installation lifecycle | Existing owners retain responsibility | Accepted-revision custody and actual install/update/drain/rollback under #6240/#6424 |
| Managed placement and live adoption | Existing external manager retains responsibility | Native target capacity, deployment/credential/migration approval and actual manager proof under #6030 |

Source prerequisites merged through #7265, #7267, #7278, #7318 and #7336.
The standard bounded runtime portfolio passed 1,678 cases, with two platform
skips and fourteen documented full-portfolio exclusions. The final ingress
portfolio passed 37 cases, including malformed-input CLI diagnostics.
The independent runner/probe portfolio passed 43 guards; those mocked guards
are not assembled execution evidence.

The exact-source native amd64 witness built a noneditable installed wheel and
passed all ten separate-container stages. It observed API/HTML/query routing,
unfinished-backend SSE, denied caller/slot/principal hijacking, last-known-good
policy, live higher role/routes, connector replacement, restart/reconnect and
caller revocation under unchanged Gateway process identity. Exact-ID cleanup
completed. Private source/image receipts remain with the executing owner;
public examples do not contain provider credentials or deployment inventory.

## Native architecture qualification

The follow-on [native qualification effort](../../efforts/2026/10/20%20gateway-native-qualification/README.md)
executed candidate `7a6e0614034814708297935660866b8e3eb7186d` separately on
native Linux amd64 and ARM64. Both runs passed all ten assembled stages with
selected architecture, engine, inspected image and installed process agreement.
Each retained seven identical Gateway process identity samples and completed
exact-ID cleanup without errors. The ARM64 run's actual installed process
reported `aarch64`; it was not emulation or a cross-build advertisement.
The native-selection and retained runner/probe portfolio passed 88 guards.
Those mechanical checks are distinct from these actual executions.

The witness accepts explicit native amd64/ARM64
selection, preserving amd64 as its default. It does not select a remote target,
invoke QEMU or turn runner support into successful architecture qualification.
Every new candidate or target still needs its applicable qualification and
separate execution approval; retained images and private receipts are not a
released registry artifact or an installation.

This closes the native ARM64 qualification gap transferred by the initial
campaign, not #7257's provider/live-edge gates. No intended live host or running
fleet is marked ready. Supported cloudflared configuration validation and live
account proof remain unqualified; the generic proxy contract is separate.

## Adopter-owned inputs and exact changes

1. Select the authorized native target and existing deployment manager. Inspect
   available CPU, memory, persistent/temporary storage and current consumers.
   The witness's 256 MiB, one-CPU and 64-PID per-container bounds are a test
   envelope, not production sizing or permission to displace existing work.
2. Select the exact reviewed source and build/qualify its image for that native
   architecture using the [image specification](../containers/gateway/README.md)
   and [explicit witness](../../tools/clean-room/gateway-role-tunnels/README.md).
   No released registry artifact is created by the Docker specification.
   Distribution, digest verification and activation retain their existing owners.
3. Provision a private TLS certificate/key and exact SAN hostname. Supply the
   listener JSON, public proof origin if selected, trusted CA references and
   private-origin network restrictions. Any remote bind, firewall, DNS, tunnel,
   account or Access change needs its own authorization. Use the
   [ingress contract](gateway-cloudflare.md), not a TLS-verification bypass.
4. Select the real Gateway application identity provider and prove its actual
   token/proof flow. Approve issuer/audience/trust, enrollment, scoped caller
   grants, service identities, proof-key custody, renewal and revocation. Do not
   substitute tunnel login, forwarded identity, unrelated tokens, fixture keys
   or a synthetic issuer for this gate. Entra and Authelia are intended options,
   not already implemented login adapters.
5. Publish the complete policy atomically from its authorized source. Retain
   accepted revision authority across process replacement with the source and
   lifecycle owner. Parsing a local file and comparing a process-local revision
   counter do not implement that restart fence.
6. Prepare explicit read-only policy/TLS/credential mounts readable by the chosen
   non-root UID. Keep private keys out of images and repositories. Bound writable
   `/tmp` and `/state`, select private networking and deployment-owned readiness
   probes, and expose neither backend sockets nor management ports to callers.
7. Identify which existing service supplies each expected slot, its namespace,
   methods, capabilities and connector backend. Services cannot choose public
   routes. Record startup, drain, reconnect, observation and rollback ownership.
8. Review the actual existing-manager deployment declaration and its diff before
   activation. No fleet-manager automated apply or managed migration is supplied
   by the service-profile CLI; do not invent a deployment command to fill that gap.
   Platform-specific wrappers and application catalogues remain separate work.

## Scoped rollout and rollback

After those gates and separate activation approval, stage the exact selected
image, configuration and secret references through the deployment owner.
Validate absent slots and denial before connecting one authorized service.
Verify its declared API/UX and unfinished response stream, then add the second.
Observe desired/applied revision, caller/slot denial, reconnect and connection
generation custody under unchanged Gateway process identity during policy edits.
These are acceptance requirements, not a claim that live activation occurred.

Retain the previous accepted image and compatible configuration/trust references.
Binary or bootstrap-origin changes require externally supervised drain/replacement
and fresh connector admission. **Rollback does not restore an old policy revision,
revoked key, expired token or withdrawn caller grant:** publish a newly authorized
higher policy revision and preserve the source owner's restart fence. Old
handles, tickets and channels are not reusable.

Withdrawal stops new work and bounded streams; a dispatched request may already
have performed external effects. A lost mutation remains uncertain and is not
automatically retried. Restoring an image cannot undo backend data/effects,
recover accepted operations or provide a missing lifecycle authority. Stateful
placement, persistence and effect fencing remain deployment/service obligations.

No live cloudflared account, Entra/Authelia login, production key custody, durable
restart fence, managed install/update/rollback or service migration has been
demonstrated by the isolated native proofs. The broader Fleet Control V2 and named
follow-on issues stay open.
