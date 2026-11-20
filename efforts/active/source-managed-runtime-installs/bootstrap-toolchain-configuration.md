# Adopter Bootstrap Toolchain Configuration

Parent: [Source-Managed Runtime Installs](README.md).
Coordination: #13409, within #7028. Status: provider released; installed-reuse refinement reviewed.

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

## Installed configuration-only reuse - reviewed refinement

A healthy installed Manager must be able to reapply the same selected
configuration without requiring its original bootstrap or acquisition payload.
Reusing retained defaults alone is insufficient: native configuration may have
been removed or changed since installation. Ordinary `setup --apply` can also
provision prerequisites/core, so it is not an equivalent configuration-only
operation.

Add `worktree-manager setup --toolchain-only`, with the existing `--apply`
opt-in, to call the same configuration owner and return before prerequisite
discovery, acquisition, core installation or repository setup. Without
`--apply`, it only validates the selection. No selection is a successful no-op.
Reject combining this mode with repository options or `register` before any
configuration effect. Keep ordinary setup and strict unknown-option rejection
unchanged.

Version 1 of `copilot-extensions.toolchain-config-only` uses the existing
`setup --help` discovery surface: a standalone `toolchain-config-only=1` line
advertises these semantics from the exact selected Manager. Help must return
before configuration reads or provisioning. An older Manager's ordinary help
remains safe but does not establish support. Callers must inspect that exact
receiver without launcher self-provisioning before emitting the new option,
and must not infer support from a bootstrap, package version or another Manager.
Missing/unsupported support directs compatible setup/recovery; never invoke
new effectful syntax speculatively or silently omit mandatory configuration.
Fresh/upgrade installation retains the standard compatible native bootstrap.

This closes the existing finite configurator and repeated-configuration intent;
it adds no installer, daemon, runtime-replacement veto or new configuration
authority. Existing native writer locks, conflicts, URL/channel limits,
credential ownership and dependency-age policy remain unchanged. Configuration
success is not evidence that any application or engine is ready or selected.
Adopter migration waits for reviewed implementation and release availability.

## Native configuration-only entry - reviewed refinement

A lightweight adopter may need native feed configuration before provisioning
its own tools, without installing desktop Manager. Installed configuration-only
reuse does not cover that first touch: the ordinary bootstrap constructs
Manager before dispatching `setup --toolchain-only`. Installing an application
merely to configure native tools is not an equivalent operation.

Keep the existing `setup --toolchain-only [--apply]` syntax and configuration
owner. Extend the standard native bootstrap and its Python installation entry
to recognize that operation and dispatch it before Manager construction,
application activation, engine/core setup or installation-queue work.
Do not add a second configuration writer or a separate installer. The native
seed still selects and preflights the same data before its normal uv/managed
Python and source preparation; this mode is not an acquisition-free bootstrap.
An already-installed capable Manager retains its existing acquisition-free
configuration path.

Advertise `bootstrap-toolchain-only` in the exact bootstrap's existing
`copilot-extensions.toolchain-config` version-1 capability list. Require a
corresponding declaration from the fetched Python entry before dispatch, so
a new bootstrap cannot construct Manager through an older selected source.
Callers must establish the bootstrap capability before using this first-touch
mode. Missing or unsupported support refuses the affected operation; never
fall back to ordinary application installation or omit the required data.
Existing no-configuration and ordinary setup syntax remain unchanged.

Use the bootstrap's normal source-selection rules and finite ownership.
Configuration-only acquisition must not reuse or delete an application's
shared staging directory. Retained native configuration and the selected
input remain outside application versions; no Manager version or binstub,
application alias/registration, service, adopter repository or current
application selection is created or changed. Plan-only configuration remains
read-only after normal bootstrap preparation. Keep strict parsing, native
conflicts/locks, independent credential and source owners, and all existing
package/TLS policy.

This closes the same finite configuration and independent application intent;
it does not revise the installation model or take over application conversion.
Under [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement),
configuration success is not incoming application readiness, activation,
replacement or outgoing retirement. No incumbent-recovery veto, daemon,
deployment or native privilege authority is introduced.
Review this refinement before code, release provider support before migrating
callers, and retain prior-provider refusal and rollback coverage.

## Plan

- [x] Review and merge this bounded contract before implementation. Accepted in #13412.
- [x] Declare the minimal portable configuration and native selection contract.
  The [version-1 input](../../../worktree-manager/docs/toolchain-configuration.md)
  keeps the adopter's file independent of organization-specific code.
- [ ] Apply it in the real standard bootstrap before prerequisite/package
  consumption, then carry it through standard setup and its actual consumers.
  Reuse native tool configuration and existing installer owners.
- [ ] Replace the adopter-specific script stack with configuration plus the
  standard installation entry after compatible support is released.
- [ ] Complete focused source, native first-consumer and required CI/review
  evidence; retain the parent's broader install/update and rollout gates.
- [x] Review this installed configuration-only refinement before implementation.
  Accepted in #13480; implementation/release and adopter qualification remain open.
- [ ] Implement and release the additive installed setup mode and safe help
  declaration, then qualify conditional adopter reuse without acquisition files.
- [x] Review the native configuration-only refinement before implementation.
  Accepted in #13552; implementation, release and caller qualification remain open.
- [ ] Implement and release bootstrap capability/source-skew admission and
  configuration-only dispatch without Manager construction, then migrate
  lightweight adopters conditionally.

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
- [ ] The actual CLI's configuration-only help is read-only even with malformed
  selected data. Plan/apply, no selection and conflicts exercise the real
  configuration owner; no prerequisite/core/repository owner is reached.
- [ ] Existing installed reuse works with bootstrap/acquisition files unavailable,
  including Windows PowerShell 7, while retaining aliases, knowledge and unrelated
  registrations. Older/mismatched receivers refuse mandatory configuration
  without speculative effects. Keep the existing native reuse phase intact.
- [ ] Actual native Windows and POSIX first touch applies the selected
  configuration without creating a Manager version, command, application
  selection, engine or installation request. Preserve an unrelated application
  staging sentinel. Plan-only and malformed/conflicting forms do not apply
  configuration; ordinary setup and installed acquisition-free reuse still pass.
- [ ] Older bootstrap metadata and an older fetched Python entry refuse the
  configuration-only requirement before Manager construction. Exercise the
  real parser/process boundary, including invalid option combinations and
  provider rollback; do not infer support from package versions.

## Journal

2026-11-20: The native configuration-only refinement merged in #13552 after
exact-head Copilot approval, required CI and resolved review threads. Four
focused regressions then failed against the previous provider: unrelated
staging was removed, an older routing-capable source reached execution, native
metadata omitted the required capability, and the real source entry tried UV
lookup instead of configuration-only dispatch. This is measured pre-fix
evidence, not implementation, release or installed adoption.

2026-11-20: The standard provider now has capability/source-skew admission,
isolated configuration acquisition and early configuration-only dispatch.
The first implementation passed 110 selected controls but the real `-I -S`
source entry found an eager process-package import. The existing parser was
then extracted unchanged into a dependency-free shared module, and the same
configuration/error-reporting stage serves both source and installed setup.
Parser and automatic-update policy function ASTs remain identical.
The four-file configuration, handoff, repository and provisioning packet
passes 245 tests with 28 platform skips in 121.95 seconds on Windows.
Bash syntax, install-contract and changed-Python lint pass. Native Linux
execution remains for the existing path-gated hosted lane; source review/CI,
merge, release and caller adoption remain open. No live installation occurred.

2026-11-20: The implementation's first published head passed exact-head
Copilot approval and all eight active Windows/Linux native jobs. Required CI
then stopped on the 1,000-line module cap: the installation entry had grown
to 1,009 lines. Its unchanged operation-admission block now lives with the
existing native input admission helpers, reducing the entry to 993 lines
without changing validation or widening the baseline. The admission ASTs
match; both real source/installed configuration entry controls pass again,
and module size, changed-Python lint and whitespace pass. The source pin
is refreshed. The corrected published head still needs fresh review and CI;
the earlier native pass is not its qualification.

2026-11-19: The installed configuration-only refinement merged in #13480 after
current-head Copilot approval and required CI. It preserves the native
acquisition-free reuse obligation and does not claim implementation, release,
application readiness or installed adoption.

2026-11-19: The contract merged in #13412 after clean Copilot approval and required
CI. No runtime behavior or installation was qualified. Implement the native
configuration/provider seam next under #13409, then converge the adopter on
released support; the parent Goal and all technical validation above remain open.

2026-11-19: The provider candidate adds early native file selection, strict
preflight and child environment propagation, followed by standard setup-owned
native uv/pip/npm persistence. Local Windows qualification passed 107 focused
tests plus the actual setup-ordering control; real pip/npm configuration reads,
offline uv index and managed-Python-mirror controls, and source-skew refusals
are included. The new provider passes strict typing; the pending full Manager
typing probe remains blocked by three prior-CLI fixture import errors, not
qualified as a pass. Native Linux coverage is added to the existing path-gated
CI lane. Source review/CI, release availability and adopter migration remain
open; no installed runtime or deployment is qualified by this entry.
