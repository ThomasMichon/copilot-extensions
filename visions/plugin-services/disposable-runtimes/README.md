# Disposable Supporting Runtimes - Vision

- **Subject:** Replaceable copilot-extensions services and executable installations,
  with durable state and an independently readable maintenance hold.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-25
- **Reality docs:** [`docs/install-contract.md`](../../../docs/install-contract.md),
  [`docs/architecture.md`](../../../docs/architecture.md)

## Purpose & Intent

Every copilot-extensions supporting service and runtime, including Manager and
the update/control plane, should be disposable. An operator can intentionally
stop and remove the executable installation, then reconstruct it from an
approved source without losing durable work or needing the removed service to
authorize its own recovery. Disposable means replaceable, not permission to
delete anything.

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

## Features

### complete-supporting-runtime-disposability

No supporting service is exempt because it installs, updates, supervises or
coordinates the others. Individual or complete suite removal preserves durable
state, accepted work and independently hosted running sessions. Reconstruction
uses the ordinary installation and state-compatibility contracts rather than
requiring a backup copy of the removed runtime to remain alive.

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
- No implicit cross-user, cross-environment or unrelated-marketplace control.
- No file schema, polling interval or command-line protocol specified here;
  binding details belong in the reviewed implementation contract.

## See Also

- Parent: [Plugin Service Model](../README.md)
- [Session Hosting](../../session-hosting/README.md#installation-independent-running-sessions)
- [Installer](../../installer/README.md#consented-background-maintenance)
- [Harness Guidance](../../harness-guidance/README.md#maintenance-aware-tool-unavailability)
- [Marketplace Installation Cells](../installation-cells/README.md)
