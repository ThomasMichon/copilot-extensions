# Explicit installation repair conductor

**Status:** Agent-recommended proposal; no executor is implemented by this
document. Review this boundary before implementation under #6464.

## Intent and reconciliation

Realize the installer vision's `core-install-via-real-flow`,
`prerequisite-provisioning` and plugin updating/alignment intent through the
existing machine setup authority. This is a bounded host-side conductor, not a
new installer engine, runtime dependency, service, scheduler or configuration
workbench. Worktree Manager may present its results; `agent-machines` owns the
headless setup operation. Every plugin remains independently installable.

The read-only `installer-readiness` library keeps graph discovery, identity,
strict readiness parsing and planning. It does not acquire execution authority,
launch subprocesses or choose policy. Requirement-package restore and this
installation-qualified operation remain separate; restoring files/packages
does not prove an enabled plugin's runtime is ready.

## Proposed entry point and admission

Expose `agent-machines installation-repair`, with a nonmutating default plan,
explicit `--apply`, repeated fully qualified `--module` selectors, and a
required expected-plan digest on apply. JSON is a versioned, bounded summary,
not arbitrary command input. No command starts an installer merely because the
plugin loaded or a session began.

Resolve the host's effective enabled payloads through the existing attributable
plugin activation/resolution authority. Do not implement another cache search
or accept an arbitrary serialized `PluginInstallation` as executable authority.
Normalize marketplace source identity, preserve distinct source cells and
reject ambiguous ownership. Use typed `PluginInstallation` records with
`discover_modules` only after host admission.

Core installation-cell mode and ordinary downstream payload delivery are
different authorities:

- An already active eligible core cell requires its validated namespace,
  installation and activation receipts. Revalidate their generations, policy
  and exact owning payload through the existing management adapter.
- A downstream plugin or an explicitly supported legacy installation uses its
  host-validated enabled payload provenance and its owner's ordinary installer
  contract. It neither creates nor requires a core installation cell. Cell
  context/action variables must not leak into that invocation.
- An unsupported source, missing admission evidence or an ambiguous legacy
  writer produces an explicit refused/unknown result. It does not trigger
  migration, guess an installation root or select a same-named peer.

`discover_from_settings` is a receipt-only core adapter, not the universal
downstream discovery route. Its existing receipt requirements are not a reason
to make non-core plugins stamp core cells. Cross-marketplace workflow
composition is not a same-cell installer dependency.

## Observation, selection and stale-input binding

Readiness invokes only the owning declaration's exact platform action in
read-only/no-self-provision mode. Missing runtimes, malformed output, timeout,
unsupported platforms and configuration-empty remain distinguishable.
Discovery does not create installation receipts, state roots or config.

Bind the plan to platform, effective enablement and source declarations,
qualified module identities, dependency graph, exact installer/readiness
arguments, payload version, applicable ownership/activation receipt
generations, owner-specific activation mode and a content identity for the
admitted release payload. Metadata and executable targets must be ordinary
contained files; reject linked/reparse ancestors and containment escapes.
Payload scripts must not acquire a mutable checkout dependency.

An expected digest is a stale-input check, not permission or a signed release
proof. Apply reconstructs admission and observes readiness again, compares the
bound inputs, and refuses any changed ownership or declarations before the
first installer. Revalidate each selected owner's binding immediately before
its invocation. Do not silently replace a reviewed plan with a new one.

The selected set is the explicit qualified modules plus their transitive
same-cell prerequisites. Do not repair every enabled optional module
implicitly. Display the complete closure before consent. Required/optional
metadata affects the reported outcome; optional failure is not silently ready.
Stable lexical topological order preserves reproducibility.

## Execution and outcomes

Invoke only exact manifest-owned platform installer actions after admission.
Use the existing platform invocation and `agent-procutil` lifecycle primitives,
with fixed trusted interpreter selection, argument arrays, bounded output and
deadlines. No raw shell string, user-supplied executable, downloaded script,
credential discovery, implicit elevation or widening retry is accepted.
Windows launches are headless; timeouts/cancellation terminate and wait out
owned transient descendants without process-name cleanup.

Durable services remain installer-owned. Their explicit supervision/cutover
and durable-lifetime contract must not be broken by treating a daemon as an
ordinary transient child. Existing owner activation, health checks and rollback
remain authoritative; the conductor does not copy their logic or turn an
installer's zero exit code into service readiness.

After each installer completes, independently run its owner's declared
readiness action. Only a validated ready result, or an explicitly
dependency-satisfying configuration-empty result, unlocks dependent execution.
A failed, unknown, timeout, unsupported or restart-required prerequisite blocks
its dependents; independent selected branches may continue. No dependent runs
merely because its prerequisite was previously classified as planned.

Report per-module observed-before, selected, attempted, installer outcome,
observed-after, blockers and restart requirement. Overall completion requires
the selected required closure to satisfy its readiness contract without
unresolved restart boundaries. Build success, configuration-empty, restart
required and ready are never collapsed into one success flag. Do not return
raw subprocess output or configuration/credential values in the summary.

Interrupted or partially completed apply returns nonsecret attributable
outcomes. A later invocation rediscovers/reobserves and proposes a new plan;
there is no stale replay or automatic rollback of unrelated successful owners.

## Serial delivery and evidence gates

1. Review this plan and host-admission API. Pin the source-neutral resolver and
   core/ordinary-owner distinction before adding execution.
2. Ship nonmutating discovery/observation/plan plus digest and selection
   contracts. Prove no installer or service start occurs on default invocation.
3. Add explicit qualified apply through existing owner actions. Prove
   independent post-readiness, dependency blocking, restart boundaries, stale
   inputs and bounded interruption/timeout behavior.
4. Exercise real first-touch consumer entry points on each supported native
   host platform: Windows, Linux, WSL and macOS, using an attributable synthetic
   payload and ordinary downstream mode. Separately exercise an already-active
   core-cell fixture. A module that does not declare the selected platform must
   report unsupported and never execute; that is not a substitute for native
   proof of the conductor's own supported platforms.
   Prove repeat repair and config preservation, not just mocked installer calls.
5. Only then wire a consuming setup conductor and prove a real missing/stale
   enabled runtime becomes ready through that owning flow. A release, local
   install or graph plan alone cannot close the live setup gap.

Unavailable native lanes remain explicit pending gates; mocked platform dispatch
or another POSIX host does not count as native macOS or WSL first-touch evidence.

Unit fixtures cover source collisions, malformed/cyclic graphs, declaration and
receipt changes, selected closure, unsatisfied-empty prerequisites, partial
failure, unknown probes, restart-required results, output limits and
read-only mode. Native integration proves exact Windows/POSIX argument
boundaries on Windows, Linux, WSL and macOS, no-self-provision readiness, timeout
cleanup and actual installed consumer invocation. Fresh-environment evidence names release identity and
remaining attended prerequisites. External/live execution is separate and
requires its adopter's own authority.

## Retained ownership and non-goals

#1886 retains symmetric reference-safe uninstall. #6274/#6389 retain serving
activation/readiness convergence. #6240 retains release verification; local
payload admission/digests do not substitute for a selected authenticated-release
policy. This conductor does not enroll machines, acquire provider capacity,
start fleet roles, enable installation-cell policy, migrate credentials,
activate a dormant service, retire a source host or remove durable data.

Those actions remain explicit owner operations, not fallback repairs when
readiness fails. #6464 remains open until implementation and its real consumer
proofs pass; this reviewed proposal alone is not runtime availability.
