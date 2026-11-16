# Project-declared, uv-managed Python

[Back to the effort](README.md) · Coordination: #12775

## Current direction and scope

This amendment extends the existing [installer intent](../../../visions/installer/README.md#uv-managed-python)
and [binding install contract](../../../docs/install-contract.md#project-owned-managed-python).
It replaces the earlier setup-owned PATH-Python requirement, not the
compiler-free requirement or the decision against interpreter attestation.
Each project installation declares the Python it needs; `uv` manages that
installation's interpreter and isolated environment without relying on system
or user PATH Python.

All copilot-extensions application launchers are in scope: they resolve `uv`
from PATH and delegate Python version installation/selection to it. Scripts
must not assume a system, user, registry or `uv`-storage Python location.
Application-generation selection remains owned by the existing runtime
resolver; interpreter-location selection belongs to `uv`.

This refines the existing contract reviewed in #12778 rather than adding a
second Python-management vision. The operator confirmed amendment of that
contract to require every app launcher to use PATH `uv`.

Operator request, verbatim:

> Let's update our copilot-extensions architecture invariants and setup plans to have `uv` manage the python installation per-package on our behalf. Basically, each project install should be able to specify the python version that it needs, and thus we should not have to care about what is on system or user PATH.

Operator refinement, verbatim:

> The thing we want to avoid doing is *assuming* a specific installed python location in our own scripts; use `uv` from path and then let `uv` handle python version install for us. Letting `python` resolve from PATH alone is turning out to be a mistake.
> We want *all* our copilot-extensions app launchers to use the uv-managed python install flow.

The current assignment is the architecture and plan revision. Implementation,
native execution and host rollout are not performed or authorized by this
document. Existing installation, shared-engine, bootstrap, helper, application
and native-qualification owners retain their scope, delivered credit and holds.
The [compiler-free plan](../lean-runtime-dependencies/compiler-free-execution.md#python-prerequisite-boundary)
consumes this same interpreter boundary rather than creating a second provider.

## Existing source delta

The shared Windows evaluator selector uses PATH Python; the Worktrees
pre-lifecycle foundation has a host-Python prerequisite. Some construction
paths already accept explicit Python/UV inputs, which is not yet a complete
cold-start acquisition path. Preserve these source facts and historical test
credit; a documentation merge does not qualify any installed path.

## Dependency-ordered implementation plan

The following implementation sequence is agent-recommended within the accepted
direction. It extends existing owners, not another installer or broker.

1. **Declare before acquiring.** Each installable Python project owns its
   `requires-python` compatibility range. Its release/setup profile supplies
   the compatible version selection needed for reproducible construction.
   Define precedence and reject conflicting pins, unsupported implementations,
   native targets and lock markers before construction. Helpers needed before
   TOML parsing have a small static declaration distributed by the existing
   bootstrap generator; they must not need Python to discover their requirement.
   Shared libraries remain in their consumers' environments.
2. **Establish the foundation without Python.** Existing explicit shell/setup
   entrypoints resolve `uv` from PATH (or establish it through the existing
   prerequisite setup), then use its managed-only Python
   selection for the declared bootstrap requirement. Reuse an installed managed
   version when compatible; permit downloads only inside the authorized setup
   operation. Do not repair PATH or require `python`, `python3`, `py`, an old
   Manager or a sibling plugin to reach this step.
   Existing private-root `ensure_uv` helpers and a temporary setup-process PATH
   change alone are insufficient: publish `uv` through the existing
   prerequisite owner's durable command exposure and provision the PATH used
   by fresh terminals, hooks and service/task environments. Do not depend on
   inherited setup state or interactive profile loading.
3. **Construct each addressed package environment.** Pass the declared project
   version/managed selection explicitly through the existing canonical engine
   and wheel/application path. Preserve wheel ABI/platform and lock matching,
   dependency/feed policy and seasoning. Record the resolved interpreter and
   reuse compatible managed base installations without sharing mutable venvs.
   OS policy failures are setup failures, not permission to fall back to another
   distribution or disable enforcement. Before migrating SAC-enforcing Windows
   hosts, qualify successful managed-Python cold setup and launch with policy
   enforcing, or explicitly resolve the supported distribution/platform
   constraint through the existing setup owner. The legacy signed-system-Python
   workaround is not an allowed managed-only implementation; an expected
   refusal alone does not qualify supported Windows operation.
4. **Connect every launcher and real consumer.** Inventory all application
   launcher sources and their generated CMD, PowerShell and POSIX copies,
   including Manager, project/standalone entrypoints, payload-local connectors
   and service startup wrappers. Migrate them and pre-runtime evaluators,
   installation-context/process helpers and installers through their existing
   owners. Every app launcher resolves PATH `uv` and delegates interpreter
   selection to it using the addressed installation's declared Python/project
   inputs. Do not guess `uv`'s storage layout, hardcode an installed Python
   directory or rediscover Python through PATH/registry. Uniform resolution
   still chooses the completed application generation; `uv` must not silently
   select a different generation, source checkout or mutable package closure.
   Preserve argument/exit-code and native invocation semantics. Warm
   connector/status/ordinary launch remains acquisition-free under the existing
   installation boundary; explicit setup owns missing-interpreter installation.
   Setup/readiness diagnostics name the missing project environment and setup
   owner. Preserve strict Job containment, headlessness and caller/service
   lifetime boundaries. Apply the existing
   [performance-measurement contract](../../../docs/patterns/performance-measurement.md)
   to this added blocking `uv` stage: capture work counts and native before/after
   cold/warm command, service-start and composed-hook measurements. Avoid
   repeated installation/dependency work; fewer Python-discovery branches alone
   do not establish lower latency.
   Include handoff's marketplace-qualified sibling boundary and removal
   launchers: handoff retains exact isolated argv and explicit setup, while
   uninstall/dry-run use already-prepared managed tooling outside the removable
   roots without downloads, dependency resolution or repair.
5. **Qualify then adopt.** Expand preparation/provider support before migrating
   independently updated callers. Preserve required skew/recovery behavior and
   explicit incoming replacement through the existing fresh entry, not an
   assumed new capability on an old installed runtime. Keep existing sessions,
   state and retained environments intact. Interpreter removal belongs to its
   existing cleanup owner and cannot invalidate a live or retained venv.
   Runtime rollout remains separately authorized.

## Validation and completion

The existing source-managed and compiler-free owners retain these pending
acceptance cases; this plan records no technical passes.

- [ ] With `uv` available and no usable Python command/launcher on PATH, explicit
  setup acquires the declared managed interpreter before any Python helper,
  builds the addressed environment and runs the actual installed command.
- [ ] The standalone first entry also works with neither Python nor `uv`
  preinstalled, using its supported shell-native prerequisite acquisition.
- [ ] After setup exits, fresh terminal, hook and service/task consumers resolve
  `uv` without setup's inherited PATH or an interactive profile. Include the
  first invocation after restart/logon and missing-`uv` diagnostics.
- [ ] With no PATH/launcher Python, uninstall and dry-run use PATH uv plus
  already-prepared managed tooling outside every removable root. Preserve
  executable/prefix/base closure and no-download/no-repair controls; unavailable
  external tooling must not partially remove an installation. Exercise
  handoff's exact-argv, isolated execution and marketplace ownership without
  ordinary-launch provisioning.
- [ ] An old/incompatible or otherwise usable system Python first on PATH does
  not affect selection. Two projects requiring different Python versions
  install and run correctly; installing/updating one does not change the other.
- [ ] Existing compatible managed Python supports offline setup/reuse.
  Missing offline versions, blocked downloads, incompatible declarations,
  unsupported platform/ABI and OS-policy startup refusal remain explicit
  failures; none selects ambient Python or compiles a helper.
- [ ] Native Windows with Smart App Control enforcing successfully completes
  managed-Python cold setup and installed launch without ambient/signed-system
  Python fallback, policy disablement or console-script trampolines. Any
  unavailable supported managed distribution is a migration blocker requiring
  an explicit support decision, not a passing refusal test.
- [ ] Warm status, discovery, hook and ordinary command/service launch neither
  acquire Python nor run a dependency-resolution/download transaction; missing environments
  give actionable setup diagnostics. Installed dispatch survives unrelated PATH
  changes and does not depend on a source checkout.
- [ ] A complete source/generated launcher inventory covers every
  copilot-extensions app, not only the first adopters. Each uses PATH `uv` with
  the owning project's version/environment inputs. Custom uv storage locations
  work without script changes; absent `uv` gives a setup diagnostic, never
  Python fallback. Guard canonical generators as well as shipped copies.
- [ ] Deterministic work-count checks bound added `uv` execution and rule out
  repeated setup/dependency work. Native before/after cold/warm and composed-hook
  reports retain individual samples, workload/cardinality, source/environment
  identity, wall/CPU/spawn coverage and tail statistics under the existing
  performance owner. Preserve the complete two-second hook budget and existing
  Picker budgets; missing native evidence is not a performance pass.
- [ ] Native Windows and supported POSIX clean-room lanes exercise the actual
  shell-to-helper-to-runtime order, process custody, caller loss and no-window
  guarantees. Unit mocks and a venv created from an already-running test Python
  are not cold-bootstrap qualification.
- [ ] Update/repair preserves configuration, sessions, databases, current
  selections and retained environments under
  [authoritative replacement](../../../visions/installer/README.md#authoritative-runtime-replacement).
  Existing acceptance and deployment owners record source, release and
  authorized installed evidence separately.

The architecture/plan contribution ends at reviewed publication. The parent
effort remains Active until its own Plan and Validation Plan are resolved;
this amendment neither takes over that campaign nor marks acquisition,
construction, native adoption or deployment complete.
