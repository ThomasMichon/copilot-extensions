# Origin-scoped telemetry routing

## Goal

Implement configure-once, origin-scoped telemetry routing across participating
suite applications, services and tools. A harness declares sinks and defaults;
bound knowledge and machine preferences specialize usage. Only eligible driving
operations and work verifiably delegated on their behalf enter a harness-scoped
sink.

The reviewed vision and plan are accepted. Implementation follows dependency-
ordered source phases. Release and installed adoption remain separate gates.

- **Slug:** `origin-scoped-telemetry-routing`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed worktrees and dependency-ordered PRs
- **Created:** 2026-11-21
- **Status:** Active
- **Vision:** [Scoped telemetry adoption](../../../visions/telemetry/scoped-adoption/README.md)
- **Umbrella issue:** #14021

## Guiding Intent

Reuse real instrumentation and configuration owners. One adopter declaration
must not mean one receiver configuration per app, or a process-global sink for
all sessions on a machine. Context identifies whose operation is observed,
not merely where a service or child happens to run.

The [architecture proposal](architecture.md) and
[coverage/validation packet](coverage-and-validation.md) define the accepted
implementation boundary and evidence. Only a phase explicitly marked complete
has source implementation; the plan is not evidence of installed adoption.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation owner | Drive the portable phases and downstream adoption through their source gates | #14021, phase issues and the normal source contribution flow |
| Configuration owner | Existing context read-data, composition and provenance | Native configuration references and existing Index/Logger adapters |
| Producer owners | Bridge, Dispatch, Worktrees, Manager and other admitted source slices | Phase-specific issue/reservation before implementation |
| Qualification owner | Mixed-origin, platform, compatibility and selected-provider evidence | Existing contained/native test and release owners |

## Coordination

- **Topology:** independent dependency-ordered PRs, one writer for each admitted
  source slice; no shared branch or global merge lock is required.
- **Host (owns PRs):** The implementation owner coordinates dependency-ordered
  source PRs and records their exact status here.
- **Delegates:** No separate source worker is assigned. #7935/#5900 retain
  performance, measurement/export and native
  qualification scope; #10873 retains health-report ownership.
- **Handoff:** A worker reads its phase, the linked architecture section and its
  applicable validation rows. It records source, release and installed evidence
  separately. A configuration file, library import or intermediate PR is not
  whole-effort completion.

## Context

The source inventory at `88dfa72e5bd87136842c5ddc6708f1dc6825b5ba`
covered the marketplace production roots, shared libraries, Manager, tools,
services, Fleet Manager, install and bundle sources. It followed actual
producer/export construction rather than counting dependencies.

The bounded command-only predecessor is #13545 and its
[effort](../consumer-telemetry-sink-adoption/README.md). Its Bridge/Dispatch
contract is a reusable base, not proof of suite-wide enrollment.

At the planning source baseline, code accepted two command source projections.
Explicit Manager OTLP export, update-run records, launch/frame-health
diagnostics and health/hourly reports have other owners and formats. The
command receiver is not an OTLP collector. See the
[source inventory](coverage-and-validation.md#source-inventory).

Index and Logger already compose repository, bound-knowledge and machine
preferences through `configuration_context` and `configuration_layers`.
The existing v1 telemetry command reader reads only its admitted root's shared
declaration and optional gated local override.

## Request

The following is a **public-safe abstraction** of the accepted request;
adopter-specific names and destinations are intentionally omitted:

- Sweep actual OpenTelemetry and related telemetry hooks across services,
  applications and tools and state which can use the configured route.
- Let a harness declare available sinks and defaults once, with knowledge-owned
  machine usages, overrides, scopes and exclusions.
- Restrict a harness sink to driving agents in an eligible harness repository
  or worktree and to work verifiably performed on their behalf. Do not collect
  arbitrary sessions merely because a shared runtime is installed.

The architecture, phase grouping and validation in the accepted plan define
this implementation boundary. They do not authorize actions beyond the source,
release and installation gates stated here.

## Scope Approvals

The operator assigned end-to-end source implementation and downstream harness
adoption, including relevant tests, source review, CI and required PR merges.
This does not authorize installed-runtime updates, collector activation,
service cutover or rollout. Use each phase's existing owners and preserve their
separate effect and release gates.

## Plan

### P0 - Review standing intent and the proposal

- [x] Reconcile the existing vision, source inventory and configuration owners.
- [x] Preserve current scope and public/private ownership boundaries.
- [x] Accept this vision/architecture proposal through source review before implementation.

### P1 - Compose effective sink policy

- [x] Extend the existing shared telemetry/configuration seam with sink
  capabilities, defaults, usage and exclusions; use native configuration
  read-data and provenance, not another binder.
- [x] Define merge, disable, invalid-source and compatibility semantics and
  prove them with the C1-C4 controls in the validation packet.

Tracking: [P1 policy-composition issue](https://github.com/ThomasMichon/copilot-extensions/issues/14277).

### P2 - Admit and propagate driving origin

- [ ] Define immutable eligible-origin and delegated-service-of context at
  ordinary command/request admission, separate from target CWD and trace identity.
- [ ] Preserve that context through child processes, queued operations and
  admitted remote work; reject unknown/ineligible sink selection without
  blocking product results.
- [ ] Prove O1-O7 before expanding automatic shared collection.

### P3 - Converge the existing command consumers

- [ ] Adapt Bridge and Dispatch to the composed policy/origin contract while
  preserving their actual lifecycle offers, bounded writer and local integrations.
- [ ] Let normal adoption own any service routing projection, with no
  per-application receiver setup by the operator.
- [ ] Preserve separate taskless-service policy and exercise R1-R4.

### P4 - Enroll Worktrees and Manager through their owners

- [ ] Add reviewed projections for applicable operation/update/launch records
  through the common route, retaining the existing update-run owner and output.
- [ ] Coordinate typed Manager measurement/frame-health/OTLP seams with
  #7935/#5900 before changing reserved integration hunks. Prove an ordinary
  production consumer, not only the source probe.
- [ ] Keep local diagnostic and validation-only records distinct; do not
  forward arbitrary logs or reinterpret unknown payloads.

### P5 - Close the producer coverage matrix

- [ ] Give every inventoried hook an explicit implemented, intentionally local,
  validation-only, unavailable or tracked-follow-up disposition and rationale.
- [ ] For remaining admitted source families, add owner-approved typed
  adapters and honest support discovery; no imported library counts as enrollment.
- [ ] Prove D1-D4, platform behavior and selected mixed-version cases through
  the existing finite test owners.

### P6 - Release and qualify selected adoption

- [ ] Publish a consumable release and verify actual provider capabilities
  before any separately authorized adopter migration.
- [ ] Prove ordinary installed producer-to-receiver persistence, exclusion and
  machine-override behavior for the selected cohort. Do not infer fleet authority.
- [ ] Update reality/coverage documentation and record remaining qualified
  gaps; distinguish receiver persistence, upload acknowledgement and backend query.

Phase-sized implementation issues are carved under #14021 after P0 acceptance
and before their source reservations begin. Existing campaigns are referenced,
not duplicated or reassigned.

## Validation Plan

- [ ] Apply the [C/O/R/D evidence packet](coverage-and-validation.md#validation-packet)
  to the selected source changes and exact executing provider versions.
- [ ] Keep parser/unit controls cheap in required CI and real process/native
  evidence in the existing explicitly selected contained lanes.
- [ ] Test standalone use, optional knowledge integration and invalid explicit
  configuration without turning telemetry into a runtime prerequisite.
- [ ] Verify unknown, excluded and old/unsupported participants do not silently
  use another sink or claim that mandatory policy was enforced.
- [ ] Prove ordinary Worktrees/Manager and service consumer paths, not helper
  calls or source fixtures presented as installed application evidence.
- [ ] Retain failed, unavailable and dropped observations honestly; never
  fabricate zero cost, spans, delivery or source coverage.
- [ ] Publish reviewed exact-head source, required CI, actual release and
  separately authorized installed evidence before claiming implementation complete.

## Proposal

The planning commission completed when the revised vision and canonical plan
passed review and merge. The implementation assignment is active. Its
completion requires the common configuration and origin contract, real
participating consumers, explicit coverage dispositions,
compatibility/platform qualification and selected receiving evidence. Unknown
in-scope coverage cannot be renamed success; a follow-up needs a named tracked
objective and clear boundary.

Keep HTTP optional. A typed source may acquire a reviewed command projection
before SDK export instead of requiring a new collector to consume it.

## Journal

### 2026-11-21 - Proposal prepared

- Captured configure-once, composed usage and driving-origin scope intent.
- Reconciled the command-only predecessor and existing performance, update,
  health and configuration owners.
- Prepared vision, architecture and validation proposals. No runtime or
  installed adoption is claimed.

### 2026-11-23 - Implementation assigned

- The operator assigned end-to-end implementation after planning review. The
  source work starts with P1 and follows the dependency order through P6; the
  downstream harness phases remain separately tracked.
- P1 composes the closed sink policy through `configuration_layers`, retains
  layer provenance, makes exclusions and opt-outs monotonic, and refuses
  providers without an explicit policy capability. The P1 source PR is not
  merged or installed.
- P1 validation passed: focused policy tests, Process Telemetry strict checks
  for Linux and Windows targets, Bridge and Dispatch command compatibility
  suites, Bridge's policy test selection, a Manager dependency-resolution
  dry run, the new-code ratchet, and the full admitted `--enforced` cohort.
- Release and installed-runtime changes remain separate gates.
