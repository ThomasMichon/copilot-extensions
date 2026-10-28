# Disposable Supporting Runtimes - Vision

- **Subject:** Replaceable copilot-extensions services and executable installations,
  with durable state and an independently readable maintenance hold.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-27
- **Reality docs:** [`docs/install-contract.md`](../../../docs/install-contract.md),
  [`docs/architecture.md`](../../../docs/architecture.md)

## Purpose & Intent

Every copilot-extensions supporting service and runtime, including Manager and
the update/control plane, should be disposable **outside the critical
connectivity and session substrate**. An operator can intentionally
stop and remove the executable installation, then reconstruct it from an
approved source without losing durable work or needing the removed service to
authorize its own recovery. Disposable means replaceable, not permission to
delete anything.

Machine-to-machine management access is protected like the Copilot process,
multiplexer and session host. Removing the rest of the suite must not sever the
SSH, proxy, tunnel or Gateway path used to manage the machine, or leave it
impossible to reconnect. A remote operator must not uninstall their own access
path through a blanket or forced removal.

An intentional outage should stay quiet and legible. One retained, local
maintenance banner both explains the outage to agents and vetoes automatic
updates that would race the operator or recreate the removed installation.
Neither purpose depends on the services being maintained.

## Concepts & Components

- **Disposable execution:** installed code, environments, generated invocation
  surfaces and service registrations have supported removal and reconstruction.
- **Retained authority and work:** configuration, repositories, worktrees,
  session history, tasks, claims, queues, credentials and recovery evidence keep
  their existing owners and survive ordinary runtime removal.
- **Maintenance banner:** an operator-controlled, human- and machine-readable
  local notice identifies the responsible session, explains the outage and
  carries the shared maintenance hold.
- **Independent session substrate:** the Copilot process and essential terminal
  host of an independently hosted session do not rely on a supporting suite
  runtime remaining installed merely to stay interactive.
- **Critical connectivity substrate:** the actual host/client transport,
  access-bearing Gateway, proxy or tunnel and their required executable,
  supervision, routing, configuration and authentication material. Provisioning
  a transport does not make every provisioner part of its live dependency chain.
- **Disposable credential relays:** agent credential/token brokerage may be
  removed with the supporting suite. It is not a protected machine-management
  transport merely because it forwards credentials.
- **Storage lifetime boundaries:** program scripts and environments,
  configuration, durable runtime state, and disposable temporary state have
  distinct, legible ownership and retention boundaries.

## Features

### complete-supporting-runtime-disposability

No supporting service is exempt because it installs, updates, supervises or
coordinates the others. The critical access-bearing dependency closure is
explicitly excluded; classification follows its actual role, not a plugin name.
Individual or complete removal of the remaining suite preserves durable
state, accepted work and independently hosted running sessions. Reconstruction
uses the ordinary installation and state-compatibility contracts rather than
requiring a backup copy of the removed runtime to remain alive.

### connectivity-survives-supporting-suite-removal

Normal removal, purge, force and reinstall paths preserve both established
machine-management channels and the capability to reconnect through the
declared transport. Protection includes an essential helper or runtime even if
it was shipped beside disposable tooling. Coupled packaging must be separated
or its critical closure retained; an unqualified mixed runtime cannot be
deleted merely because its package is called a supporting service.

### provisioning-is-not-serving

Transport setup, profile emission and key positioning have different lifetimes
from the OpenSSH host/client, proxy process, tunnel keeper or Gateway that
actually carries management traffic. Removing a provisioner should not remove
its independently owned working transport or durable access configuration.
Where a connection invokes provisioner-owned code on every request or
reconnect, that dependency is explicit rather than assumed absent.

### uninstall-by-clear-storage-boundaries

Supporting code can be removed as a small, clearly owned set of directories
without sorting individual files around configuration, persisted runtime state
or live transactional state. Temporary state is distinguishable from durable
authority; a temporary location does not make an irreplaceable in-flight
transaction disposable.

Each service and Manager still has a proper uninstall flow for registrations,
shutdown and dependency handling. Simple directory removal is the conceptual
storage boundary that makes safe uninstall understandable and auditable, not
a replacement for those lifecycle obligations.

Uninstall de-registers the removed component's owned tasks and other service
registrations. A normal in-place update preserves them and their enablement
intent; it does not simulate uninstall/reinstall through scheduler churn.

### shared-maintenance-banner

One banner is both the automatic-update veto and the agent-facing explanation,
not two controls that can disagree about whether maintenance is active. It is
local to the addressed machine and user environment, remains available after
service, binstub and plugin-payload removal, and can be read without a suite
command, daemon, network service or credential broker. Its owner identity is
durable attribution, not a claim that the owner is still running.

### maintenance-aware-automatic-admission

All automatic update and reprovisioning entry points honor an applicable active
banner, including Manager's own updates, recurring controllers, launch hooks,
first-use repair and delegated execution. A previously queued request or
earlier eligibility check cannot bypass the hold at the actual effect boundary.
Intentional runtime absence is not permission to recreate it.

### cooperative-maintenance-standby

While installed, background tasks and registrars remain registered and runnable
during maintenance. They check the applicable shared KillSwitch before doing
work; while it is active, they do no work and wait with occasional checks for
clearance. Maintenance is a running-but-quiescent mode, not disablement in the
operating system's scheduler. Explicit uninstall remains a separate lifecycle
operation that removes owned registrations.

When the hold clears, the existing owners resume their loops and subscriptions
without requiring task re-registration or losing progress. The standby boundary
remains usable when supporting tooling is absent and does not keep an old
replaceable runtime alive merely to wait. Ordinary reconstruction and current
capability resolution precede resumed dependent work.

### service-independent-agent-standby

Normally delivered conduct guidance tells agents how to recognize an explained
local outage when a catalogued tool disappears, preserve their work and consult
the banner through native facilities. Agents remain informed through sparse
native scheduled prompts, without a replacement service or communication path
that depends on the removed suite.

## Behaviors

### maintenance-veto-is-not-execution-authority

A banner can withhold automatic work; it cannot authorize teardown, cancel an
already admitted operation, seize another owner's resources or enable any
disabled update policy. The responsible session may continue separately
authorized maintenance through the existing manual ownership and safety gates.
Read-only inspection remains available where its dependencies exist.

The hold does not stop or withdraw critical connectivity. Transport maintenance
has its own scoped authority; a general suite-removal or force request is not
permission to retire that substrate.

### protected-access-is-not-a-force-option

A supporting-suite uninstall or reinstall cannot override connectivity retention
with a broad force or purge flag. It excludes the protected dependency closure
and refuses an operation whose effect on required access remains unknown.
An existing shell alone does not establish that future authentication,
reconnection or a client-side proxy will survive.

### credential-brokerage-is-not-connectivity-custody

Removing credential relays is allowed while native machine-management access
continues independently. A component that mixes credential brokerage with
access-bearing transport must expose and separate those roles; the relay label
does not exempt every broker from removal or excuse losing management access.

### no-self-repair-during-explained-maintenance

Agents do not reinstall tools, provision replacement runtimes, repeatedly retry
missing commands, start substitute workers or enlist another agent to repair an
outage explained by an active banner. They retain progress, claims and pending
obligations. Unrelated machines and user environments do not inherit the hold.

### explicit-clearance-not-optimistic-recovery

An established hold does not expire merely because a notice is old, missing or
temporarily unreadable. Clearance is attributable and applicable to that hold;
agents verify the required capability is available before resuming. Clearing a
banner never enables another disabled policy or grants permission for a queued
operation that has lost its original authority.

Cooperative standby does not override an independently disabled task or
service. Neither cold-start recovery nor maintenance clearance silently changes
scheduler enablement, bypasses a KillSwitch or duplicates a suspended loop or
subscription.

### data-and-host-lifetime-outlive-tooling

Runtime removal does not mean data erasure, task completion, claim release or
session retirement. The session-hosting contract owns process continuity;
durable authorities own recovery and truthful uncertainty after interruption.
Maintenance notices remain informational data, never executable instructions.

## Non-Goals / Boundaries

- No mandatory maintenance daemon, central broadcast server or parallel updater.
- No automatic deletion, process termination, service restart or blanket repair
  authority from a banner, a missing command or this disposability guarantee.
- No change of ownership for retained data, credentials, sessions or resources.
- No blanket exemption for a whole plugin, every Gateway feature or credential
  relays; protect the actual management-transport dependency closure.
- No implicit cross-user, cross-environment or unrelated-marketplace control.
- No file schema, polling interval or command-line protocol specified here;
  binding details belong in the reviewed implementation contract.

## See Also

- Parent: [Plugin Service Model](../README.md)
- [Session Hosting](../../session-hosting/README.md#installation-independent-running-sessions)
- [Installer](../../installer/README.md#consented-background-maintenance)
- [Harness Guidance](../../harness-guidance/README.md#maintenance-aware-tool-unavailability)
- [Marketplace Installation Cells](../installation-cells/README.md)
- [SSH connectivity](../../plugins/agent-ssh/README.md)
- [Fleet Gateway](../../machine-fleet/gateway/README.md)
