# Safe fleet setup and refresh

Back to [the effort](README.md). **Proposed; no live provisioning is implied.**

## Operator workflow

`set up my fleet` resolves three independent inputs before any change:

1. The selected harness recommendation/template and its version.
2. The adopter's existing configuration from its resolved paired/state repository.
3. Authoritative provider/controller live inventory, quota and owned work state.

The generic CLI accepts explicit resolved paths and provider snapshots; it never
hardcodes a private repository identity or assumes its working directory is the
adopter's writable configuration home. The harness adapter owns pairing and the
recommended template location. The public core owns bounded parsing/planning.

An initial host without a controller has a narrowly admitted bootstrap path
through the selected provider. Once the controller exists, the thin client uses
its authenticated API; failure must not silently switch to raw worker mutation.

## Config and live diff

Record template provenance and field ownership so refresh can distinguish an
old managed recommendation from a deliberate adopter override. Unknown fields,
custom roles and live unmanaged resources remain preserved/inspectable.
Do not merge by recursively replacing an entire file or treating a missing role
in the latest recommendation as a deletion order.

The plan separates config suggestions from live actions: add, retain, drift,
unsupported, blocked, and explicitly requested retirement. Uncertain/stale live
state or unresolved provider identity blocks mutations requiring fresh evidence.
Use a revision-bound plan digest; re-read quota, inventory and config before
apply and reject changed assumptions instead of applying an obsolete plan.

Per-target receipts distinguish planned, provider-admitted, created, installed,
connected, service-ready and converged. Partial application resumes from durable
receipts without duplicate capacity or unsupported exactly-once promises.

## Capacity and essential roles

Every fleet recommendation includes Gateway, coordination, task-state and
indexing responsibilities plus their required connectors; agent-chat is a
future role whose absent implementation must remain explicitly unavailable.
First useful Gateway-host capability proves index and dispatch using existing
standalone/service authorities. Central worktree status and bridge coordination
are next role contracts, not new competing worktree/task registries.

Separate service-host budget from agent-host, build and product-code venue
budgets. A one-host fleet is service-only by default. It may use operator-enrolled
external execution hosts; attended login/admission is an explicit prerequisite,
and config-based roles/auto-assignment never enroll a device silently.

No fleet/quota means no capacity can be fabricated. Offer a smaller supported
template or explicit scaling parameters with unmet service requirements shown.
Do not label an incomplete central-service fleet ready. A provider may expose
quota without permission to create; selection and admission remain required.
Distinguish provider-reported remaining quota, configured adopter budget and
currently available admitted hosts. A local maximum is not a provider quota
receipt. Unknown provider quota blocks quota-dependent creation; it does not
prove zero quota or permission to provision up to the configured maximum.

## Preservation and cleanup

Refresh is additive/preservative unless the operator requests retirement.
Before any container/venue removal inspect active agents, worktrees, dirty or
unpublished code, pending PRs, claims, persistent volumes and novel capabilities.
Require the existing venue/source-control finalization authority; neither an
idle process nor a missing recommended role proves there is no work to retain.

Return blockers and explicit transfer/recovery destinations for unfinished work.
Do not delete a managed venue directly or force-clear a peer's claim. Data
deletion is separately confirmed; uninstall preserves documented durable state.

## Observation and telemetry

First integrate explicit user-controlled Gateway health/events as a fleet sink,
reusing existing low-cardinality telemetry producers rather than copying their
stores. Collection scope, limits, retention, redaction and downstream access are
configured. No credentials, raw process arguments, proprietary payloads or
unbounded transcript content enter generic monitoring by default.

External analytics export is default-off and independently policy-authorized.
The upstream contract accepts an adapter; organization-specific telemetry and
employee consent/compliance remain private, not assumptions in this system.

## Initial realization (agent-recommended)

- Publish bounded template/adopter/live/quota planner contracts and regression
  tests before any provider mutation.
- Supply a harness-owned recommendation plus paired-config resolver without
  private inventory or credentials in the shared harness.
- Repair private provider readiness, then use explicit quota/admission and lease
  to provision a controller-host **container**; bare host harness use is not
  needed for this experiment.
- Prove index and dispatch service capability, authenticated Gateway access and
  a full harness container with managed worktrees/plugins and scoped auth.
- Extend centralized worktree/bridge observation, user-controlled monitoring and
  optional execution roles after those core contracts pass.

This mission authorizes implementation and eligible capacity planning, but not
dropping existing work or bypassing provider, credential, budget or review gates.
