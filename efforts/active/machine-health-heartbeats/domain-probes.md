# Owner-bounded health domain probes

Parent: [Machine Health Heartbeats](README.md).

**Agent-recommended amendment: review before implementation.** This specializes
the machine-heartbeat vision's
[domain health](../../../visions/telemetry/machine-heartbeats/README.md#ssh-gateway-authentication-and-agent-health)
and [bounded publication](../../../visions/telemetry/machine-heartbeats/README.md#bounded-independent-publication).
It adds no activation, carrier, receiving, credential or remediation authority.
The [local collection contract](local-collection.md) keeps its admission,
45-second parent budget, hourly attribution, slot transaction and cleanup fence.

## First complete flow

Prove one explicitly selected outbound SSH route through its existing SSH
owner, then the normal local hourly slot and age-qualified report. This is
one route's evidence, not the health of every route or inbound reachability.
The initial selection permits at most one alias; no inventory crawl, automatic
peer selection or fan-out. A self-route still proves only the attempted
outbound direction. Inbound evidence needs its own later owner contract.

Existing `agent-ssh verify` is not that contract: it refreshes generated
profiles, can accept new host keys, and its POSIX probe has no outer process
deadline. Preserve its existing interactive behavior. Do not copy its raw
OpenSSH adapter into Agent Machines or invoke it speculatively from a job.

## Provider before consumer

Add a bounded observation operation inside Agent SSH, with proposed syntax
`agent-ssh observe --alias <alias> --timeout-ms <budget> --json`.
Its separate read-only semantics justify a new operation rather than changing
`verify` defaults. Publish `agent-ssh.health.observation` v1 support with the
exact selected executable runtime through the existing installation metadata
owner. Introduce any needed descriptor support provider-first; do not assume
legacy peers understand a new discovery command. No per-call capability
subprocess, package-version equality or mutable payload stamp proves support.

The provider reads the already-selected local profile without refreshing,
provisioning or repairing it. Require noninteractive authentication and strict
existing-host-key verification; missing keys are unavailable evidence, never
permission to accept or write one. No password, passphrase, browser, login,
credential refresh, profile emit, forwarding enrollment or persistent SSH
control connection. Reuse an already-authorized credential mechanism only
when the owner can guarantee this bounded noninteractive mode.

Route setup is part of the effect boundary. Arbitrary `ProxyCommand` or helper
execution cannot inherit a read-only guarantee from `BatchMode` alone.
Initially admit only routes whose existing owner can enforce no provisioning,
login, persistent helper startup or configuration mutation for the complete
path. Unsupported routes refuse before launching. This need not support every
configured route in its first implementation.

Use the SSH owner's finite process and exact-descendant cleanup primitives,
including hidden Windows launches. The SSH branch receives at most five
seconds, including selection, launch, connection, response and settlement,
and never more than the remaining parent budget after its settlement reserve.
An OpenSSH connect timeout alone does not bound helpers, authentication or
process exit. Keep complete stdout and stderr separately bounded at 16 KiB
each; do not serialize raw output. No automatic retry or alternative identity.
Unconfirmed cleanup retains the existing collection custody fence and stops
further work; a timeout is not proof descendants exited.

Return a closed, bounded JSON result carrying schema/version, attempted
outbound direction, source observation time, outcome and reason codes.
An authenticated success requires completion of the owner's fixed harmless
remote action on the selected route, not a local process, port or banner.
Transport/authentication failures, unsupported routes, malformed output,
timeouts and unknown settlement remain distinguishable. An undifferentiated
SSH exit code or raw stderr is not a diagnosed authorization or credential
cause. Do not export alias, host, user, key, path, command or raw exception.

## Local policy and evidence mapping

The current v2 store policy has a strict collection object; unknown fields
must remain errors. Add an explicitly selected v3 policy with the existing
publisher/continuity and collection semantics plus an optional single SSH
selection. Preserve v1/v2 readers and their existing operations unchanged.
Do not silently migrate a store or enroll a job. New selection requires normal
local configuration preview/confirmation; it is not accepted from replicated
health or preference data. Keep private route selection local.

The selected Agent Machines runtime must separately declare the extended
collection contract before a caller prepares or enrolls an action using v3.
An existing job's policy approval no longer matches after a policy change;
its normal preview/adoption boundary must explicitly approve the new effects.
Provider replacement uses the existing runtime-selection/retention owner,
not a second health-specific executable or lease registry.

The existing observation v1 already represents one authenticated direction
and proof timestamp. Use it without adding target arrays or new domain
fields: successful completion maps to `ssh_authenticated` / `ok`,
`authenticated-session`, `outbound`, and its actual source time. An
unsuccessful authentication attempt is not an authenticated-session proof;
keep that domain unknown or unsupported with the existing bounded reason.
Detailed closed attempt outcomes stay in the local operation result unless
a separately reviewed record revision can represent them truthfully.

Do not derive `ssh_transport`, inbound health, Gateway visibility, global
authorization, credential readiness or active-agent counts from that success.
Other independently selected observations keep their original owners and
timestamps. The normal reader preserves proof age; composition, staging,
reporting and later receipt never renew it. Hour crossings, future/regressed
timestamps and stale provider output use the existing clock/coverage refusal
rather than backdating or relabeling evidence.

## Compatibility and remaining domains

Follow [service-contract evolution](../../../docs/patterns/service-contract-evolution.md):
expand the SSH provider/declaration first, expand policy readers and Agent
Machines support next, then enable only explicitly adopted compatible callers.
Missing or unsupported SSH metadata yields unknown/unsupported optional
coverage without invoking new syntax or breaking existing local reports.
Old callers retain their old defaults with new providers. Provider rollback
must give a pre-effect unsupported outcome, not fall back to `verify`, refresh
profiles, discard policy fields or silently remove an enforced restriction.
No existing operation is retired in this amendment.

Gateway, authorization and credential observations require separate bounded
owner contracts for the configured resource and audience. Logical activity
requires the session owner's admitted logical snapshot and coverage. Bridge's
generic relay readiness or live-host count is not an equivalent substitute.
Do not enumerate OS processes, arbitrary credentials or the entire Bridge
inventory. These later domain outcomes and carrier qualification stay open.

## Dependency-ordered delivery and validation

1. Review and merge this amendment before source realization.
2. Expand the SSH provider with focused unit tests for noninteractive and
   no-mutation policy, strict inputs/results, deadline propagation, bounded
   output, unsupported helpers, failure classification and exact cleanup.
   Exercise its real parser/process boundary with an isolated SSH fixture:
   success, missing host key, denied authentication, live unresponsive peer,
   hanging helper and cleanup failure. Confirm profile/known-host files remain
   unchanged. Native Windows/Linux execution is separate from unit-only CI.
3. Expand v3 policy readers and capability-selected collection. Prove old/new,
   new/old, new/new, missing/invalid declarations and provider rollback;
   preserve old parsing/defaults, refuse unsupported effects before launch,
   and invalidate policy approval on selection drift.
4. Prove the real owner observation -> current-hour slot -> normal
   reread/report/chart with private synthetic targets. Verify outbound proof
   age, every other domain unknown unless independently observed, hour change,
   stale time, overlap, partial failure, shared deadline and custody refusal.
   The unchanged 168-slot ring remains the only evidence writer.
5. Record source, release, installed-provider and native-platform results
   separately. Obtain independent activation authority before any real route,
   existing schedule, cohort or carrier is changed. No live production probe
   or deployment is part of this plan-only PR.
