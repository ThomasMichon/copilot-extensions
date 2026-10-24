# Repository hosting policy

An explicit repository-owned `expose_agent: false` vetoes managed authoring
agents and project adoption. It does not unregister an existing project, stop
an existing process, remove sessions or claims, or grant a migration.

```yaml
# .copilot-extensions/agent-worktrees/config.yaml
expose_agent: false
```

The shared `repository-policy` library reads current repository bytes on every
admission. The first present base file wins: the canonical path above,
`.agent-worktrees/config.yaml`, then `.agent-worktrees.yaml`. The selected
marketplace's `.copilot-extensions/agent-worktrees/marketplaces/<id>/config.yaml`
can add a denial but cannot erase a base denial. An unrelated marketplace is
not consulted. Explicit policy must be Boolean; malformed or unreadable policy
refuses hosting rather than falling back to permission.
The receiving installation's existing validated context supplies the marketplace
identity. Invalid context or a missing identity in an explicit context is a
visible refusal, never a fallback to distribution-neutral policy or ambient CWD.

Machine-local exposure controls still apply independently. A stale local
`expose_agent: true`, explicit project/worktree selection, launch profile, or
recovery mode cannot override repository denial. Neither `true` nor absence
grants adoption, trust or launch authority. There is no generic review-purpose
exemption; externally hosted code review and source-only editing are unaffected.

**Direct eligibility and registry discovery have different defaults.** Checking
any actual repository directly reports it as possible unless its own policy
vetoes hosting. In `repos.yaml`, `agent: true` is an explicit discovery opt-in;
absence is false for every editing class. A `related.yaml` relationship or locus
alone declares no agent: a non-`none` `delegate` must be explicit. `projects.yaml`
is the adoption registry for repositories expected to use Copilot/Worktree
Manager, not a general inventory of source worktrees. A worktree-class source
repository needs only source registration and related metadata; do not adopt it
as a project merely to allocate source worktrees from an external harness.

## Inspect without adoption

```bash
agent-worktrees agent-policy check --path /path/to/checkout --operation launch --json
```

This command works outside an adopted project and does not resolve a project,
create runtime/project state, publish guidance, or start a session. `--path`
defaults to the current directory; `--operation` defaults to `launch`.

Project-bound callers can inspect the owner's source checkout without building
a launch plan or allocating a worktree:

```bash
agent-worktrees --project harness agent-policy check --project-root --json
agent-worktrees --project harness agent-policy check --worktree-id exact-id --json
```

These mutually exclusive selectors read existing owner metadata only. They
require an explicit project, refuse unresolved metadata, and never use the
caller's unrelated CWD as a substitute. `--path` still selects that exact
checkout even when a project binstub supplies an owner prefix.

Success returns policy `version: 1`, `repository`, `expose_agent`, `sources`,
`denied_by`, and `allowed: true`. The latter reports only that the negative
policy permits proceeding to the existing independent gates. Denial or invalid
policy returns exit status 3 and JSON `version: 1`, `code`, and `error`; the
codes are `repository-agent-exposure-denied` and
`repository-agent-exposure-invalid`.

## Preview conflicting registrations

```bash
agent-worktrees agent-policy audit --json
```

This read-only command examines existing `projects.yaml` registrations against
their current platform's registered roots in `repos.yaml`, using the receiving
installation's validated marketplace context and fresh shared policy resolution.
It does not infer a root from CWD, discover checkouts, scan or change session
records, acquire write locks, or perform an automatic repair.

The version-1 report contains `read_only: true`, `automatic_changes: false`,
the selected `marketplace_id`, and a `projects` array. Each row records the
project, local exposure setting, registered path when available, policy
provenance when resolvable, and one state:

- `conflict`: repository denial conflicts with the existing adoption, even if
  machine-local exposure is already false.
- `eligible`: no repository-owned veto; this grants no additional permission
  and does not override machine-local exposure restrictions.
- `unresolved`: no registered local root; no discovery or CWD fallback occurred.
- `invalid`: unusable repository policy or registration metadata.

Counters `conflicts`, `unresolved`, and `invalid` summarize the report. Any
nonzero counter returns exit status 3 with top-level `version`, `code`, and
`error` alongside the preview; a clean preview returns 0 and `allowed: true`.
That success reports only the absence of audit findings, not launch permission. Invalid
registry input or installation context returns the versioned error envelope,
never a clean empty report.

**Reconciliation requires an explicit, state-preserving decision.** Retain the
registration and all sessions, tracking, history, PR associations and claims
while reviewing the preview. Work from an eligible external harness; use the
existing [read-only record inspection](cli-reference.md#exact-durable-record-read)
to understand retained work and custody. Separately authorize any migration or
unregistration and verify state/custody preservation before executing its
supported workflow. Do not hand-edit registries to suppress a finding, silently
drop claims, or terminate an existing process to make the preview clean.
The audit never executes those actions or selects a replacement harness.

Adding a repository denial marker changes future admission. It does not
reconcile existing registrations, validate the installation, or authorize
destructive cleanup.
Retained venue delivery journals may still reconcile their original idempotent
operation and settle its receipt without a new hosting grant. This recovery
cannot launch a replacement agent; if the journal is gone, normal fresh launch
admission applies again. Preservation does not exempt a newly requested managed
`embody` or resume, even when a mux session already exists.

## Allocation is not launch

Plain `create --json` allocates a source worktree and tracking record without
building a launch plan, copying Copilot permissions, trusting the checkout, or
publishing launch projections. Its result contains `worktree`, not `launch`.
Source configuration, Git and PR commands remain usable under an external
eligible harness. Creation with a launch seed is managed authoring intent and
must pass hosting admission.

`resolve` New/base/resume, `embody`, recovery and native/deferred execution
check before their hosting effects. Resume admission precedes seed staging;
the seed writer and final execution boundary also re-read policy. A policy
added after planning blocks a new launch without deleting the retained seed.
State-restoration and completion bookkeeping remain available after denial.

Manager and remote callers send `--require-agent-hosting` so older engines
refuse before allocation or seed submission, rather than executing a weaker
compatibility path. Hosting-aware seed/admission writers require verb version 7.
Unsupported receivers fail visibly; no caller silently chooses another harness.
Manager shell diagnostics remain buffered until target admission: denial starts
no automatic update, installer, reconciliation, launcher-root registration or
recovery setup, and creates/prunes no setup logs or trace files. Direct source
inspection/allocation commands receive no implicit launch maintenance.

See [the engine/Picker contract](engine-picker-contract.md) for process-boundary
details, and [Agent Worktrees](../README.md) for the plugin overview.
