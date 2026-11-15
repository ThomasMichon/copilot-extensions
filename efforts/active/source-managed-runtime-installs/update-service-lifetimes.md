# Foreground updates and independent service startup

[Canonical effort](README.md) |
[Installer lifetime intent](../../../visions/installer/README.md#foreground-update-and-independent-service-startup) |
[Service lifetime intent](../../../visions/plugin-services/README.md#independent-service-startup-lifetime) |
[Execution owner](execution-driver.md)

## Goal

Ordinary updates follow their caller and remain cancellable. Once a service
owner accepts setup/start, that operation and the service tree have independent
lifetime: aborting the updater or its ancestors cannot kill them. Retain the
parent effort's complete incoming-installation, preservation and acceptance
goals; this is a cross-owner amendment, not another updater or effort.

## Request

Operator clarification, with only the private repository command generalized:

> I guess there is no need for `<repository> update` itself to breakaway from its caller, as most of the time we want a caller to follow it through to the end, and we want Ctrl+C and other process-kill commands to properly terminate it. However, when the update flow executes the `setup/start` commands of each installed service, those *must* always breakaway from their callers. We won't want a situation where aborting the update process or its ancestors kills the now-started daemons/

The operator then requested this vision/effort alignment and resource
coordination to drive it. The precise private intake remains with its owner;
no private deployment identifiers or transcript link belong here.

**Accepted interpretation:** foreground execution is the ordinary update
default; explicit background execution remains distinguishable. A short-lived
start client may be cancellable only when an independent service owner
continues the accepted operation. Any wrapper required for the daemon's
lifetime belongs to that independent service tree.

This supersedes the earlier requirement that ordinary synchronous updates only
observe a caller-independent background installer. It preserves one writer,
request/result history, source admission and uncertain-outcome recovery.
Historical delivered source and qualification are not retroactively withdrawn.

## Ownership and cancellation contract

- The foreground updater owns acquisition, construction, staging and other
  finite helpers. Ctrl+C, caller death and caller cleanup stop that work rather
  than leaving a hidden whole-update worker. Cancelling an observer of another
  owner's operation does not authorize terminating that owner.
- At setup/start acceptance, the service's existing lifecycle owner takes
  responsibility for startup, readiness, activation/recovery and the persistent
  process tree. The updater can observe or stop waiting; it is no longer that
  operation's lifetime owner. Unaccepted remaining services do not start merely
  because an earlier service accepted startup.
- The handoff includes native process identity, startup/outcome custody and
  required immutable-runtime retention. Updater cleanup cannot kill an admitted
  service, delete its retained runtime or roll it back merely because its
  caller disappeared. Loss during an unaccepted handoff cannot leave an
  unowned running candidate or manufacture acceptance.
- Independent service activation keeps its actual write/data authority until
  a stable result. Foreground cancellation is not proof every accepted service
  operation stopped, nor permission for another updater to overlap those writes.
  Existing bounded join/continuation, data-compatibility and recovery owners
  remain authoritative.
- Independence covers caller-owned Jobs/cgroups, process-tree cleanup and
  console cancellation, not visibility or immediate-parent exit alone.
  A service-supervisor-owned Job is legitimate. Neither an any-Job predicate nor
  escaping only the immediate Job proves the relevant full lifetime contract.
  Unknown external supervisors remain explicit qualification limits.
- A production service must not be reported durably started from a
  caller-contained fallback. Do not repair this by disabling security policy,
  dropping cleanup checks, introducing an unrequested supervisor or weakening
  bounded test/tool containment.

## Associated owners and prospective obligations

| Existing effort / slice | Required alignment; custody retained |
|---|---|
| [Parent installation](README.md), P4b/W2/V5-V8 | Foreground installation and independently accepted service activation; preserve complete staging and incoming selection. |
| [Execution driver](execution-driver.md), #7758 | Ordinary direct/cancellable execution, explicit background mode, single writer and honest cancellation/handoff results. |
| [Manager control plane](../worktree-manager-control-plane/README.md) | CLI/UI distinguish cancelling an update from observing independent service work; preserve all other Manager goals. |
| [Resilient sequencing](../resilient-update-sequencing/README.md), #6820 | Complete staging barrier, cancellation before/after each service handoff, safe resumable activation. |
| [Observability](../update-flow-observability/README.md), #6812 | Identify execution owner, cancellation scope, accepted service operation and final versus unknown results. |
| [Shared installer engine](../vendored-installer-engine/README.md) | Common handoff mechanics across admitted adopters; retain Worktrees/Budget exceptions and paused adopters. |
| [Upgrade recovery](../installer-upgrade-recovery/README.md), #10377 | Older-footprint and same-boot interrupted-update cases, without losing accepted daemons or protected connectivity. |
| [Daemon custody](../leased-daemon-instances/README.md), #8403 | Distinguish finite update helpers from independently owned production service instances and retain real identity/lease authority. |
| [Lean runtime helpers](../lean-runtime-dependencies/README.md), #6234 | Preserve the compiler-free helper contract while qualifying the actual native lifetime boundary. |
| [MCP handover](../mcp-session-generation-handover/README.md), #10486 | Qualify the actual MCP service boundary without terminating client/session keepers or importing the whole SDK portfolio. |
| [Vault service proposal](../vault-service/README.md), #8729 | Carry the same setup/start lifetime boundary; its Draft architecture/owner-admission gates remain intact. |
| [Contributor/app map](wheel-first-contributors.md) | Every admitted app slice proves its own applicable lifetime boundary; no portfolio-wide rollout is implied. |

The completed [Latest Waiting Update](../latest-waiting-update/README.md)
scheduling source/release and historical journals keep their credit. They do
not qualify the new cancellation behavior and are not reopened by this map.
Existing claims, phase owners, review limits, operational holds and deployment
permissions are neither transferred nor widened.

## Dependency-ordered delivery

**Agent-recommended decomposition:** the existing #7758/#7028 coordinator first
reconciles request/operation mode, acceptance and cancellation contracts with the
service lifecycle owners. Publish versioned support and acceptance through the
actual receiving owner before migrating callers; an installed package version
or new caller alone cannot establish old-receiver support. Then admit
non-overlapping foreground-executor and
service-handoff source slices, followed by their assembled native consumer
qualification. Reuse the current queue, lifecycle and test owners; resource
allocation follows their real authority, not a new monitor scheduler.

Only reviewed intent precedes implementation. No production service restart,
credential access, new paid resource, held-adopter resumption or deployment is
authorized by this amendment.

## Validation and completion

The outcomes are required; the controls below are agent-recommended ways to
qualify them within each owner's existing platform and effect boundaries.

- [ ] L1: Through the real ordinary update entry, cancel the owner with Ctrl+C
  and separately terminate its ancestor/tree and every fixture-owned cleanup
  scope. Update-owned finite work stops; its request records cancellation or
  uncertainty honestly, without automatic replay or successful completion.
- [ ] L2: Repeat before service acceptance, during accepted startup, after
  readiness and during cutover. Unaccepted candidates retain owned cleanup;
  accepted service work and serving daemons survive updater/caller loss and
  reach an independently observable stable result.
- [ ] L3: Verify the service's required host/wrappers, immutable runtime,
  endpoints, registrations and representative operation after caller cleanup.
  Existing sessions, keepers, credentials, data and protected connectivity
  survive. Test an unready candidate and retained-good discovery recovery too.
- [ ] L4: Prove a service-owned/non-terminating Job is not confused with caller
  cleanup ownership, while a shared outer terminating scope cannot falsely
  qualify independence. Preserve negative containment controls; do not infer
  the full hierarchy from one NULL-job limits query.
- [ ] L5: Qualify explicit background mode separately, concurrent/coalesced
  requests with different lifetime owners, cancellation of a mere observer,
  same-boot recovery and source/version skew without parallel writers.
- [ ] L6: Apply the actual supported Windows and POSIX native lanes, fresh
  fixtures where relevant, required source review/CI and containing release.
  Installed/fleet adoption remains separately authorized and evidenced.

An intent PR, source merge, created PID, early ping or surviving immediate
parent exit passes none of these technical rows by itself.
