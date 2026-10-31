# Configuration Workbench - Vision

- **Subject:** Worktree Manager's standing Configuration surface and the
  plugin-owned backstage panels it hosts.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-24
- **Reality docs:** [Manager configuration](../../../worktree-manager/docs/configuration.md),
  [plugin contribution contract](../../../worktree-manager/docs/plugin-contribution-contract.md)

## Purpose & Intent

An operator should have one approachable place to understand how the harness is
configured and to change what this machine controls. Worktree Manager hosts
that place without becoming the owner of every plugin's configuration,
service, or repository.

Direct configuration is machine-local by default and by authority. Repository,
knowledge-repository, plugin, and preset sources are visible in the combined
view but cannot be edited there. When the desired outcome requires a source
change, the operator can request agent-driven maintenance instead of asking
the Configurator to become a Git editor.

## Concepts & Components

### Effective configuration

The workbench presents the composed result alongside its contributing sources,
precedence, scope, and any masking overrides. A source declaration, an effective
setting, and an observed running state are distinct facts.

### Machine-local controls

Editable controls belong to this machine: supported enable/disable overrides,
killswitches, local registration and binding preferences, venue choices,
service controls, and other strictly local adjustments. A local override does
not rewrite the declaration it masks.

### Plugin-owned backstage panels

Plugins contribute configuration widgets, toggles, findings, and actions under
the Manager's Configuration section. The Manager owns discovery, navigation,
presentation, and shared interaction promises. Each plugin owns its domain
semantics, validation, precedence, persistence, and authorized operations.

Configuration is a standing backstage workspace, not a succession of plugin
dialogs. Its sections make configured resources and available templates
browsable before an operator chooses an action, with descriptions, status,
statistics, provenance, and contextual actions.

Plugin-owned helper daemons may expose dynamic panels and live controls where
ongoing observation or coordination needs a service. Declarative contributions
and bounded commands remain sufficient where a daemon is unnecessary. The same
owning capability remains usable without the Manager.

### Delta requests and maintenance workers

A delta request describes a proposed change to an authoritative source that
the workbench cannot edit. With explicit operator submission, agent-dispatch
can route it to an appropriate maintenance worker pool. The request, worker,
repository change, and evaluated outcome remain one traceable chain.

## Features

### configuration-provenance

Browse the effective configuration and its origins, including repository and
knowledge-repository settings, plugin defaults, adopted presets, and local
overrides. Read-only sources are clearly distinguished from editable local
controls; masked values remain inspectable.

### machine-local-adjustments

Apply supported local enable/disable and killswitch overrides and other
machine-owned adjustments with visible scope and consequences. Removing an
override restores the underlying source's authority rather than replacing it
with a copied value. Overrides cannot weaken a non-overridable safety or
authorization boundary.

### installation-and-updates

Provide a standing Installation and Updates section showing the complete
maintained component set, including Manager, runtime services and payload-only
plugins. Distinguish known, installed and enabled components, delivered payload,
selected runtime and actually observed running versions, with attributable
sources and explicit unknown, missing and platform-policy outcomes.

Offer explicitly confirmed updates for individual components through their
existing installation authority. A selected plugin update never silently
updates Manager or unrelated plugins, or substitutes forced reinstallation.
Missing prerequisites and unsupported owners explain the precise limitation
and supported retry or recovery. Whole-suite updating remains a separate scope.
Durable acceptance, queued/running progress and terminal outcomes remain
observable after the view is dismissed; refreshed inventory establishes what
actually changed. Browsing does not enroll background maintenance.

### related-repositories-and-venues

Inspect related repositories, participation and enablement, resolved routes,
preferred venues, and harness/knowledge pairing. Adjust machine-local choices
where the owning capability supports them. Versioned topology and pairing
policy remain read-only; local selection does not imply permission to create
repositories, change accounts, or write a knowledge anchor.

### domain-configuration-panels

Provide a coherent Configuration home for dispatch tasks and worker-pool
controls, SSH machine-mesh preferences, container loadouts, and additional
plugin domains. Domain owners supply their own supported controls; the Manager
does not clone their queues, inventories, credential stores, or service logic.
Configuration validity, connectivity, authorization, and live health remain
separately understandable.

### templates-and-presets

Offer attributable templates and harness-plugin presets that make useful
arrangements easy to adopt. Preview their effects and conflicts before local
application. Presets remain read-only sources, preserve explicit choices, and
do not carry credentials or silently become repository edits.

Instantiation creates a distinct, inspectable local configuration rather than
mistaking an enabled template for an instance. Saving configuration, activating
it, creating agentic tasks, and starting agents have distinguishable outcomes;
an operator sees the intended effects before authorizing them.

### source-delta-requests

Offer a distinct request-change action for read-only sources. Before dispatch,
the operator can inspect the intended target, observed baseline, desired
change, and acceptance conditions. Submission never masquerades as a completed
save. Requests are not executed merely because a panel was opened or a local
control was changed.

### maintenance-outcome-evaluation

A maintenance worker uses the target repository's authorized worktree,
coordination, validation, review, and merge flow. A separate evaluator checks
the landed source change against the requested outcome and the refreshed
effective configuration. Masking local overrides are reported, not mistaken
for a failed source edit or silently removed to make the result appear correct.
An open pull request is progress, not completed maintenance.

## Behaviors

### navigation-before-content

Entering Configuration or selecting a section immediately presents its
workspace and a legible loading state. Slow discovery and status providers
populate content without withholding navigation or freezing interaction.
Failures remain visible within their section, with a recovery action, rather
than appearing as an unresponsive control or an empty successful result.

### read-only-means-read-only

Browsing, inspecting, and refreshing never edit source repositories. A direct
local action never falls back to a repository write when no local override
exists. Unavailable controls explain the boundary and available next action.

### explicit-target-and-consent

The target machine and scope are visible before a change. A remotely displayed
machine is not implicit authorization to adjust it. Destructive actions,
credential-forwarding changes, and expanded authority require deliberate
confirmation through the owning capability.

### safe-plugin-boundaries

Configuration helpers expose bounded, attributable capabilities rather than
arbitrary execution or raw credentials. Their lifecycle, authentication,
compatibility, and cleanup remain plugin-owned. Missing helpers or incompatible
providers degrade their own panels with actionable findings, not the whole
workbench or unrelated plugin functionality.

### refresh-and-reconcile

Changes are reported from their actual result and followed by refreshed
configuration and state. Concurrent edits or changed source baselines are
reconciled explicitly or returned as conflicts; stale requests are not applied
blindly. A failed action is never rendered as successful convergence.

### deterministic-without-maintenance-agents

Inspection and supported local controls work without a maintenance pool.
Source maintenance is an optional, explicit extension through agent-dispatch.
Its absence leaves sources read-only and reports that request execution is
unavailable; it does not disable direct configuration.

## Non-Goals / Boundaries

- Editing versioned repository, knowledge-repository, or plugin-source settings
  directly from the Configurator.
- Automatically creating or launching a maintenance worker pool as a side
  effect of browsing Configuration.
- Bypassing repository coordination, required checks, review, or merge authority.
- Moving plugin domain logic or helper-service ownership into the Manager.
- Requiring a persistent helper daemon for every configuration section.
- Treating a machine override, dispatched request, or open PR as a landed
  source change.

## See Also

- Parent vision: [Installer & Configurator](../README.md)
- Related visions: [agent-dispatch](../../plugins/agent-dispatch/README.md),
  [plugin services](../../plugin-services/README.md),
  [Worktree Picker](../../picker/README.md)
- Reality docs: [Manager configuration](../../../worktree-manager/docs/configuration.md),
  [plugin contribution contract](../../../worktree-manager/docs/plugin-contribution-contract.md),
  [configuration ownership](../../../docs/configuration.md)
