# Supplementary repository installables - Vision

- **Subject:** Centrally managed tools and services contributed by adopting
  repositories and their selected plugins.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-11-09
- **Reality docs:** [Install contract](../../../docs/install-contract.md),
  [application installation](../../../docs/patterns/fast-application-install-and-cutover.md)

## Purpose & Intent

An adopting harness should be able to contribute its own installable tools,
services and applications without forking the suite's installer or becoming
part of its first-party release. The same centralized installation system should
discover, prepare, install, update and remove these supplementary components
under their actual source and lifecycle owners.

Worktree Manager should distribute the common registration handler and compose
the authoritative installer and runtime-assembly capabilities. An adopter
supplies declarations and application-specific behavior, not another dependency
resolver, wheel builder, launcher generator or service supervisor. Installed
applications remain usable without a running Manager or Copilot session.

## Concepts & Components

- **Supplementary installable:** an independently owned executable tool, service
  or application contributed outside the suite's own release inventory.
- **Effective contribution roster:** the resolved plugin selection of the
  adopting repositories, including bound knowledge contributions and supported
  local overrides, with attributable marketplace and source identities.
- **Installable registration:** a conventional declaration that connects an
  eligible contribution to its package, dependencies and lifecycle owner.
  Packaging metadata alone does not request installation.
- **Repository contribution manifest:** the equivalent explicit entry for
  installables authored directly by a repository rather than by a plugin.
- **Shared installation handler:** the Manager-distributed coordinator that
  feeds these declarations into the existing construction, selection and
  lifecycle owners.
- **Application adapter:** the narrow boundary for package preparation and
  application-specific readiness or activation, independent of source language.

## Features

### source-current-roster-discovery

An update should synchronize the managed source anchors before deriving the
desired supplementary set. Repository, knowledge and local settings should
compose through their existing owner, preserving explicit disabling, source
selection and override precedence.

Discovery should follow the selected marketplaces to their eligible plugins,
then inspect conventional installable registrations. No directory spelling or
hardcoded marketplace should define eligibility. Repository contribution
manifests should join that same desired set without duplicating plugin
registrations. The plan should retain the source selections that it actually
consumes rather than changing revision midway through installation.

### one-authoritative-installation-path

Supplementary Python applications should extend the modern locked-uv,
wheel-first application model and its exact compatible dependency closure.
Preparation, caching, construction, publication and repair should reuse the
upstream pattern and helpers, not an adopter-maintained approximation.

Source and package ownership should remain distinct from installation ownership.
Application-specific setup, health and service activation should stay with the
application; common orchestration should not absorb its business logic.
Supplementary support should preserve independently installable components and
the existing authoritative runtime replacement contract.

### packaging-neutral-application-boundary

The platform should accommodate command-line tools, persistent services and
desktop applications regardless of their implementation language. A thin Python
packaging adapter may carry a non-Python application when it faithfully describes
and installs the actual artifacts, runtime dependencies and lifecycle.

Native package-manager adapters can extend that boundary without introducing a
second installation authority. Neither a Python rewrite nor immediate native
support for every ecosystem should be required. Package-manager differences must
not erase provenance, compatibility, readiness or reversible removal.

### installation-aware-tool-guidance

A declared runtime-backed command should resolve an eligible installed
application or report its absence with the supported independent installation
path. Commands, hooks and skill readiness checks should not implicitly install,
update, repair or stamp runtimes.

When a task requires a missing runtime, the authorized user or agent should
invoke that independent installer as a distinct installation action, establish
readiness and then invoke the installed tool. Bulk updates and these explicit
installations should use the same owning handler; concurrent installation
requests should converge on the same addressed installation.

Skills should make this boundary discoverable, never substitute execution from
a checkout or mutable plugin payload. Installation should precede an unstarted
operation rather than replay a possibly completed business action after an
arbitrary command failure.

## Behaviors

### bounded-and-explicit-convergence

The selected configuration and installation action should determine which code,
commands and service roles are required. Discovering or loading a plugin alone
should not authorize installation or standing background execution.

Qualified identities and dependencies should prevent name collisions or
duplicate lifecycle ownership. Unavailable or malformed contribution sources
should produce explicit outcomes, not appear to be removal requests. Retirement
should use the recorded owner and preserve configuration, credentials and durable
work rather than interpreting disappearance as permission to erase state.

### reuse-before-reconstruction

Unchanged, qualified installed generations and compatible prepared inputs should
be reused before resolution or construction. Source-built wheels and native
package artifacts should benefit from their existing caches. Relevant source,
build, dependency and target changes should invalidate reuse correctly.

Performance should be judged at the real cold, repeated and update entry points,
not only at an already-prepared inner step. Packaging overhead should be measured
without weakening immutable runtime or installed-command guarantees.

### observable-installation-completion

Plans and results should explain provenance, selection, reuse, preparation,
installation and actual readiness separately. An environment staged on disk or
a published pointer should not stand in for a usable command or required serving
generation. Failures should remain attributable to their phase and owner.

## Non-Goals / Boundaries

- A new adopter-specific builder, dependency resolver, updater or supervisor.
- Folding supplementary applications into the suite's first-party release.
- Requiring a plugin wrapper for every repository-owned application.
- Requiring a running Manager or Copilot session for installed application use.
- Runtime execution from a development checkout as an installation fallback.
- A mandatory Python implementation or immediate native support for every
  package manager.
- Installation, service activation or data deletion inferred from discovery.

## See Also

- Parent vision: [Installer & Configurator](../README.md)
- Child visions: none (leaf)
- [Runtime assembly and artifact admission](../../plugin-services/runtime-assembly/README.md)
- [Independent plugin service model](../../plugin-services/README.md)
- [Authoritative runtime replacement](../README.md#authoritative-runtime-replacement)
- [Application installation and cutover](../../../docs/patterns/fast-application-install-and-cutover.md)
