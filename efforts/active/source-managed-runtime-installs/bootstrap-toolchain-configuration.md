# Adopter Bootstrap Toolchain Configuration

Parent: [Source-Managed Runtime Installs](README.md).
Coordination: #13409, within #7028. Status: proposal before implementation.

## Goal

Let an adopter supply a small configuration file that the standard
copilot-extensions installation flow honors while setting up Python, Node,
npm and uv, without a separate adopter-owned initialization script.
Configuration must take effect before the first applicable prerequisite or
package consumer, including installation before private repository access.
Keep organization-specific endpoints and defaults outside this repository.

## Request

> Ideally, this feed-override business should just be a harness-provided config file that our standard copilot-extensions install flow just honors as part of setting up python, node, npm, and uv at this point; no custom scripts ideally necessary

This is the generic configuration/installer boundary. It does not choose an
adopter's endpoints, authorize a deployment or change TLS, credential,
quarantine or artifact-admission policy.

## Architecture and ownership

This closes the existing installer's
[native seed, prerequisite and single-entry intent](../../../visions/installer/README.md#concepts--components).
It does not add another installer, daemon, resolver, artifact producer or
configuration authority. Evaluate the final source and consumer evidence against
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
retained settings and early feed configuration are necessary inputs, not proof
that requested applications are ready and selected.

The existing native bootstrap must accept a trusted, explicitly selected local
configuration before Python or uv is available. A known repository configuration
location may supply that same input when the adopter checkout already exists.
Do not require cloning a private repository or executing adopter code merely to
configure the public foundation. Parsing must not require another installed
runtime or source/evaluate the configuration as shell code.

Keep the file small, versioned and data-only with a bounded supported field set.
Declare its exact format, discovery, explicit selection and precedence in the
standard bootstrap contract. Unknown, malformed, credential-bearing or
conflicting source selections must fail before package consumption. Preserve
supported explicit native-tool selections and unrelated user options; never
silently replace an approved source or clear an opaque additional selector.
No configuration retains the existing public installation behavior.

The standard installer owns application of the selected configuration and its
propagation through setup and child consumers. Python package indexes, managed
Python distribution acquisition, Node acquisition and npm registries are
different channels: support only the fields the corresponding real owner can
honor, and document unsupported channels rather than guessing mirrors or
claiming a Python index governs binary downloads.

Preserve #13119's native source-profile feed selection, #12905's app-footprint
orchestration, #4876/#7529 artifact/feed qualification, #7758 finite execution
and all existing application/source/credential owners. This slice changes no
live registration, maintenance policy, source pin or deployment permission.

## Plan

- [ ] Review and merge this bounded contract before implementation.
- [ ] Declare the minimal portable configuration and native selection contract.
  Keep the adopter's file independent of organization-specific code.
- [ ] Apply it in the real standard bootstrap before prerequisite/package
  consumption, then carry it through standard setup and its actual consumers.
  Reuse native tool configuration and existing installer owners.
- [ ] Replace the adopter-specific script stack with configuration plus the
  standard installation entry after compatible support is released.
- [ ] Complete focused source, native first-consumer and required CI/review
  evidence; retain the parent's broader install/update and rollout gates.

## Validation Plan

- [ ] First-touch native entry consumes a standalone trusted file with no
  adopter checkout, Python or uv prerequisite. No package operation can precede
  its configuration selection.
- [ ] Invalid/unknown/duplicate or conflicting input starts no package command
  and preserves user files and environment. Unsupported source/TLS selectors
  do not become a hidden public fallback.
- [ ] Fresh and repeated configuration preserves approved explicit feeds and
  unrelated options, including command-specific pip and virtual-environment
  configuration. Real tools read the intended persisted/effective settings.
- [ ] Python/pip/uv and Node/npm consumer boundaries are each proved at their
  actual owner. Explicitly record any unchanged/unsupported acquisition channel;
  string assertions or a Python-only success do not establish Node/npm coverage.
- [ ] Existing no-configuration bootstrap, standard setup and retained source/
  application contracts still pass. Source merge, release availability and
  authorized installed adoption remain distinct evidence.
