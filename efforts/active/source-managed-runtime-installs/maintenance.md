# Manager-owned automatic maintenance - proposed slice

Parent: [Source-Managed Runtime Installs](README.md).
Request: [operator record](inception-transcript.md#manager-owned-automatic-maintenance).

**Intent and implementation plan only.** This amendment replaces the earlier
Agent Machines scheduling assignment; it does not add another update authority.
Review must precede implementation. No startup registration, maintenance worker,
source binding, cadence or adopter policy is enabled by this proposal.

## Goal and owner

Realize the Installer vision's
[consented background maintenance](../../../visions/installer/README.md#consented-background-maintenance)
and [source-backed maintenance](../../../visions/installer/README.md#source-backed-maintenance)
through Manager's existing canonical installation/update path.

Automatic installation is **off by default**. An explicit user choice or an
explicitly run trusted consuming-harness setup can opt in for the addressed
user or local installation. A harness that installs Manager can finish its
approved setup with automatic maintenance enabled. Merely opening, cloning or
discovering a repository is not enrollment authority.

Manager owns the optional worker, startup registration, recurring invocation,
effective consent and observable result. It does not require Agent Machines,
an open Picker or a foreground Copilot session. An explicitly selected external
update controller is an exclusive alternative, not an additional writer.
Existing release, runtime-publication and service-supervision owners remain
unchanged.

Agent Machines retains dependency/resource convergence, repository maintenance
and host/connectivity work. Its legacy fleet-update schedule and the suite-update
portion of its self sweep must not compete with an enrolled Manager owner.
Standalone users that have not adopted Manager keep their existing behavior.

## Scope and prerequisites

The first deliverable is consented enrollment and a background trigger over the
already-supported canonical update path. It does not silently select merged
development source, enable managed deployment or grant publication permission.
It may land independently of the remaining source-managed installation work.

The parent Phase 3/4 publication boundary and independent source-selection
prerequisites still precede recurring **source-backed** installation. Once those
are qualified, extend this same maintenance owner rather than add a second loop.
Manager self-update, service-runtime selection and plugin-content delivery retain
their independent choices. Source synchronization remains an explicitly governed
phase, not permission for an installer to edit repositories.

## Platform lifetime and consent

- **Windows:** use current-user Autorun to start a headless maintenance process,
  not Task Scheduler registration or an administrator-only service. Autorun
  starts at user logon; do not claim pre-logon or logged-out operation. Starting
  from an agent/setup context must use the canonical process-lifetime primitive
  and survive that caller's containment cleanup without visible windows or
  foreground changes. A blocked immediate start must report activation pending
  or a precise error, never claim a running worker from registry presence alone.
- **Linux/WSL:** use the supported user-scoped lifecycle and report user-manager,
  session or WSL lifetime requirements. Do not request system-wide linger or
  elevated settings silently.
- **Harness enrollment:** expose a supported programmatic setup seam after
  Manager installation. Record normalized user/installation-scoped consent and
  its provenance; the worker must not execute arbitrary repository setup code
  or rediscover new opt-ins while running in the background.
- **External ownership:** selecting an external controller leaves no competing
  Manager update worker. Ownership handover must be observable and reversible.
  Do not infer consent to remove another owner's startup entry or stop a worker.

**Agent-recommended policy detail:** preserve an explicit local user disable
over a harness's default preference, reject conflicting owner declarations, and
make repeated setup enrollment idempotent. Exact commands, policy fields and
cadence limits follow the implementation review rather than this proposal.

## Implementation sequence

- [ ] Land this ownership/consent amendment and its vision update through review.
- [ ] Resolve installation scope, existing updater invocation, authentication,
  process lifetime, locks and update telemetry in their existing owners before
  adding interfaces. Reuse those primitives rather than another installer.
- [ ] Add default-off local policy, explicit user/trusted-setup enrollment,
  owner selection, disable and read-only status/preview.
- [ ] Add bounded recurring maintenance independent of the Picker, with one
  installation-scoped worker and explicit current/blocked/retry outcomes.
- [ ] Resolve the installed Manager afresh for each bounded update cycle so a
  long-lived worker does not keep applying its original updater implementation
  after Manager self-update. Authentication must defer visibly rather than
  starting an unattended interactive credential ceremony.
- [ ] Upgrade the resident worker's own scheduling and consent implementation
  through the existing safe resident-lifecycle/singleton handover contract.
  Preserve any active update until its safe completion, permit only one
  scheduling owner and activate the qualified replacement without another
  user logon. Failed replacement must preserve a healthy controller or report
  the precise recovery outcome; updating the invoked CLI alone is insufficient.
- [ ] Implement unelevated current-user Windows Autorun plus durable headless
  startup and the matching Linux/WSL user lifecycle.
- [ ] Hand over only legacy suite-update responsibility. Preserve Agent Machines'
  independent dependency/host work and standalone behavior; prevent manual and
  background update overlap through the canonical owner. Gate legacy fleet
  execution, registration and resource reconciliation so a stale `present`
  declaration cannot resurrect a competing trigger after retirement.
- [ ] Integrate the portable enrollment seam into an explicitly consenting
  consuming-harness setup after Manager installation. Keep adopter identities,
  repository-specific wiring and local rollout evidence outside public upstream.
- [ ] After the parent source-management prerequisites, add dedicated source
  health, safe selected-ref synchronization and exact per-run source identity
  to this same maintenance path.
- [ ] Update owner documentation and the parent journal with actual reviewed,
  released and adopted behavior; a registered startup entry is not proof of a
  successful background update.

**Agent-recommended implementation safeguards:** use one canonical result format
for manual and background updates; preserve the previous healthy installation
on failure; bound retries with backoff; avoid writers for unchanged generations;
never reset dirty source or bypass installation/dependency policy. A source or
credential blocker must not become a success-shaped skip.

## Validation matrix

| Boundary | Required evidence |
|----------|-------------------|
| Default and consent | Fresh Manager installation starts no updater by default. Explicit user or trusted setup can enroll the addressed installation; repository discovery alone cannot. Repeated enrollment is idempotent and local disable remains effective. |
| Setup completion | A real consuming-harness setup installs Manager, enrolls its approved owner and reports enabled/running or a precise activation/readiness blocker. A later fresh process observes the same local policy. |
| Windows lifetime | Native unelevated enrollment writes only the current-user Autorun surface, requests no UAC and creates no scheduled task. Execute the exact registered startup command; prove worker survival after setup Job closure and zero visible-window/focus changes across at least two cycles. |
| Worker upgrade | Native Windows and Linux replace a real old worker with the selected new implementation without another logon. Exercise an in-flight update, successor failure and concurrent replacement: preserve the active update, prevent overlapping scheduling owners and verify healthy continuation or an explicit recovery outcome. |
| Linux/WSL lifetime | User-scoped registration, restart/logon behavior and current status work without an open Picker. Missing user-manager/session/WSL availability remains explicit. |
| One owner | Manager and external-controller modes cannot update concurrently. Legacy fleet/self-suite paths hand over without suppressing unrelated machine/dependency convergence; unadopted standalone behavior remains intact. |
| Recurring operation | Observe an unchanged cycle and an advancing update through the actual bounded canonical path in the scheduled/background context on Windows and Linux; no foreground setup or task-definition proxy counts as this proof. |
| Failure and overlap | Authentication/network failures, held update locks, worker/caller loss, invalid policy and failed candidates are attributable, bounded and recoverable without replacing healthy selections or starting a competing owner. |
| Source-backed extension | After parent prerequisites, reject dirty/worktree-backed/substituted/unmerged source, resolve tracking refs once, retain exact pins, preserve unchanged completion evidence and activate only a qualified new generation. |
| Inventory and authority | Cover the adopter's selected installed services/tools including Manager, while preserving unused roles, disabled services, source choices and existing release/publication/service owners. |

Native startup changes, exact cadence, credentials, legacy-owner retirement,
service impact and rollback must pass the adopter's own approval flow before
enabling anything. The parent Plan and Validation Plan remain the broader
source-managed campaign's completion gate.
