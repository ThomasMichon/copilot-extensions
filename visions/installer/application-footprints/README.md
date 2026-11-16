# Manager-Owned Application Installation

- **Subject:** promotion-produced, independently usable application footprints
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-11-16
- **Reality docs:** [`docs/install-contract.md`](../../../docs/install-contract.md) ·
  [`fast application installation`](../../../docs/patterns/fast-application-install-and-cutover.md)

## Purpose & Intent

An application installs and updates through one coherent flow, independent of
the development checkout, Copilot plugin payload and a working Worktrees
installation. Worktree Manager owns generic installation management;
applications supply a complete setup surface and their own domain readiness.
An installed version stands on the exact prepared inputs that produced it.

## Concepts & Components

- **Install footprint:** the promotion-produced, wholly-copyable application
  directories and their declared dependency/version inputs.
- **Manager installation orchestration:** finite selection, dependency ordering,
  copy, staging and activation, separate from the optional interactive UI.
- **Application setup:** one version's self-contained preparation and full
  installation contract, usable by the bootstrap and Manager alike.
- **Embedded code dependency:** first-party code materialized into a consuming
  application by promotion for execution in that consumer's process.
- **Shared IPC component:** an independently installed and versioned provider,
  reached through its version-neutral commands and IPC, never imported as the
  consumer's implementation code.

## Features

### manager-owns-installation-management

Manager owns the generic install/update flow rather than delegating ownership
to Worktrees. Worktrees is an application consumer, not a bootstrap prerequisite
or incumbent that must be repaired before other apps can install.

The finite bootstrap can establish Manager itself through the same application
setup contract without an existing Manager service, interactive UI, Copilot
session or healthy legacy runtime. Independent component setup remains usable;
one owner does not imply a mandatory resident installation daemon.

### promotion-produced-copyable-footprints

Dev-to-Main promotion produces complete application directories, including
vendored copies of their embedded first-party code. Installation copies the
selected directories wholesale rather than resolving development indirections
or assembling code from a replaceable plugin payload.

Pinned development commits and explicit WIP-on-Dev snapshots use that same
promotion flow locally before installation. It materializes the footprint and
assigns local versions while preserving the original source and official
release authority. Local preparation never claims public release attestation.
Installed generations do not follow later edits in the source checkout.

### staged-then-selected-application-setup

A version's setup prepares its declared managed-Python environment and selected
inputs before activation. Staging changes only that candidate, not the current
version, neutral commands, live service registrations or active mutable state.

Full setup establishes the app's required configuration/readiness and coherent
version selection with neutral app/global command redirects. Successful
incoming startup commits roll-forward for a service app; writing a marker alone
does not establish that result. Repeat setup is idempotent and preserves
compatible state and healthy owners.

### dependency-ordered-ipc-components

Shared runtime providers are explicit installation dependencies with their own
setup/version lifecycle. Manager installs and stages required providers before
consumer full setup. A provider needed by that setup is also compatibly selected
and ready before invocation; staged code alone is not a reachable service.

Consumers invoke version-neutral binstubs and communicate through IPC, not
imports of provider implementation or access to a provider's private
environment. Promotion keeps these edges distinct from embedded code libraries.
Independent consumer removal cannot invalidate another live dependency.

## Behaviors

- **One preparation model:** Main, pinned development and explicit frozen WIP
  converge on a promotion-produced footprint before orchestration.
- **Source-independent installation:** a copied app remains installable and
  runnable when its original checkout or plugin payload is unavailable.
- **Explicit outcomes:** missing prerequisites, dependency readiness and partial
  activation are visible, not success-shaped fallback to a legacy installer.
- **Separated durable state:** version replacement does not discard
  configuration, secrets, databases, sessions or protected connectivity.

## Non-Goals / Boundaries

- Not a new builder, dependency resolver, execution queue or service supervisor;
  reuse the canonical owners and their admitted inputs.
- Not permission to import a shared IPC provider because it was historically
  called a library, or to turn every embedded utility into a resident service.
- Not public release approval for local WIP, automatic source mutation or
  authority to install on a host merely because a footprint exists.

## See Also

- [Installer parent vision](../README.md)
- [Runtime assembly](../../plugin-services/runtime-assembly/README.md)
- [Release pipeline](../../release-pipeline/README.md)
- [Canonical receiving effort](../../../efforts/active/source-managed-runtime-installs/README.md)
