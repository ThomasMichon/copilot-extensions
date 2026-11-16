# Compiler-free first-party execution

[Back to the effort](README.md)

## Requirement and ownership

The operator's complete requirement is captured in the parent Request. It
extends `visions/plugin-services/README.md#compiler-free-first-party-execution`:
no repository-owned native source or custom binary and no native or embedded
helper compilation during development, release construction, installation or
device execution. A prebuilt custom executable, a different build machine or
runtime code emission is not the remedy.

This existing effort coordinates the complete subtraction. The reactivated
[leased-daemon effort](../leased-daemon-instances/README.md) owns the shared
instance/lifetime replacement, not a competing registry or service.
Installation-context, operation-lock, update-driver, installer and session-host
owners retain their protocols and independent acceptance gates. Existing
dependency/profile, source-managed installation and native adoption work is
not replaced or declared complete by this amendment.

## Observed source delta

- One canonical Linux daemon-instance C implementation has fifteen distributed
  source copies. Its package build hook compiles during ordinary local source
  construction; release construction and source verification also compile the
  helper. Removing a first-use compiler alone would leave this delta open.
- Windows has executable embedded C# compilation in canonical process helpers,
  installation-context inspection, operation locking, update-driver foundation,
  participant and directory helpers, selected installer identity paths and the
  elevation worker. These include genuinely pre-Python bootstrap surfaces.
- The pane wrapper emits a runtime interop type rather than invoking a C#
  compiler. Replacing compilation with that mechanism does not satisfy this
  requirement.
- Loading an existing platform assembly is not embedded compilation. External
  operating-system facilities, supported Python distributions and admitted
  third-party binary packages remain dependencies; their implementation is not
  being rewritten. Native source-build fallback during dependency acquisition
  is not silently permitted.

## Execution sequence _(agent-recommended)_

1. Land the intent and this proposal through the normal review gate. Reserve
   bounded source slices through their existing owners before implementation.
2. Extend the existing shared Python lifecycle implementation and reuse its
   record protocol, locks, process-birth checks and Linux pidfd acquisition.
   Thin shell entrypoints use setup's explicit uv-managed Python selection
   to invoke that shared implementation. Preserve same-process
   admission where required; do not add a central supervisor service or another
   inventory.
3. Replace Linux C supervision/reconciliation with standard-library Python and
   existing OS interfaces. Preserve owner loss, renewable and hard deadlines,
   unreaped child identity, descendant reparenting/settlement, exact artifact
   custody and explicit unknown outcomes. Small platform bindings through
   `ctypes` call existing OS libraries; they do not compile new code.
4. Qualify the interpreter/bootstrap boundary before migrating Windows
   callers that currently run before their plugin runtime. Reuse the
   [existing Python prerequisite](#python-prerequisite-boundary), rather than
   preserve a Python-free helper implementation. Preserve authenticated pipe
   endpoints, process handles, strict atomic Job containment, headlessness,
   operation-lock compatibility, filesystem/reparse/ACL checks and uncertain
   launch outcomes. Use the setup-selected uv-managed interpreter; do not retain
   signature, registry or physical-path interpreter admission as a replacement
   for compilation.
   An unresolved bootstrap design blocks that slice before mutation.
5. Remove canonical and generated native sources, compiler setup/build hooks,
   custom native artifact export/verification and embedded compilation/code
   emission. Keep pure-Python artifact integrity and normal dependency/source
   admission. Regenerate distributed helpers through their owning generators;
   do not manually patch installed copies or loosen source verification.
6. Add ownership-aware guards, update current reality docs and requalify actual
   minimal-machine construction, process safety and affected consumers. Old
   compiled-helper evidence is historical, not replacement qualification.

## Python prerequisite boundary

The [installer vision's uv-managed Python
contract](../../../visions/installer/README.md#uv-managed-python) governs this
prerequisite. The existing installation owner uses uv to prepare the declared
supported Python before shared safety helpers need it, then passes the explicit
selection into helpers and application environments. A cold plugin runtime
does not imply a missing toolchain, and preparing that toolchain must not require
an already-working system Python. Standalone installation owns equivalent
preparation without requiring Manager or an Agent Machines service.

This architecture supersedes the earlier choice to provision official system
Python through Agent Machines and trust the first `python` on PATH. The
2026-10-27 removal of interpreter attestation remains applicable: this is not
permission to restore signature/publisher, registry-only, physical-namespace
or file-pinning admission, or venv/worktree/temporary-path blacklists.
Operating-system code-integrity enforcement remains independent.

Read-only helpers consume the prepared supported interpreter without acquiring
one, mutating PATH or trying progressively broader ambient Python candidates.
An older Machine-PATH Python, active virtual environment, absent `py` launcher
or fresh terminal must not change the declared version. Source-only distributions
carry the complete helper source through the existing packaging owner, without
a new physical-source admission ceremony.

Existing process, lease, update-driver and installation-context owners still
own their protocols and native OS bindings through ctypes. Trusting Python
does not relax process identity, atomic Job containment, authenticated pipes,
operation-lock compatibility or exact cleanup custody. Artifact/source receipts
for managed application runtimes remain distinct from interpreter attestation.

The prerequisite remains owned by explicit setup/repair, using uv's Python
management rather than a second interpreter installer. Acquisition and missing
or unsupported toolchain failures name the real prerequisite and its supported
setup path. Status, discovery and ordinary launch never install Python or
compile an interop helper. Already prepared toolchains work offline. Services
retain their explicit environment interpreters; adding `uv run` before an old
bound executable is not a migration.

Before removing a Windows compiled implementation:

- [ ] Replace compiled interpreter admission with explicit uv-managed Python
  selection prepared by the existing installation owner.
  Remove signature/publisher, registry-only, PE/path-attestation and physical
  namespace launch gates without adding venv/worktree/temporary-path filters.
- [ ] Apply the same setup-owned prerequisite boundary to shared installer
  Python rediscovery and signature-driven base selection. Preserve copied-venv
  construction and module invocation where needed, without reimplementing
  Windows code-integrity policy or changing package/feed integrity checks.
- [ ] Exercise the prepared supported Python with an incompatible first PATH
  Python, active venv, absent launcher and fresh terminal. Cover missing uv or
  managed interpreter, offline reuse and acquisition/execution failure without
  read-only downloads, system-PATH repair or per-helper attestation.
- [ ] Prove the actual first-entry order: explicit uv-owned Python preparation,
  selected-interpreter standard-library helper, then runtime construction or
  application work. Prove service launch retains its environment interpreter
  and that existing supported environments and rollback survive preparation.
- [ ] Preserve byte-range/operation-lock compatibility, authenticated pipe
  peers, strict atomic child Job assignment, headlessness and uncertain cleanup
  outcomes through the existing Python owners.
- [ ] Requalify cold and repeat invocation with compilers absent and no helper
  acquisition fallback. Include missing-prerequisite, startup failure,
  caller-loss and retained process-identity controls in bounded disposable
  fixtures. OS policy blocking Python remains an explicit startup/setup error,
  not permission to disable policy or download another interpreter.

Earlier stock-framework signature and physical-path experiments are historical
evidence, not prerequisites for this revised handoff. Do not continue that
attestation design or use it to block the compiler-free migration. Native
process/Job qualification remains required; no runtime code emission or weaker
post-start assignment substitutes for that separate safety contract.

## Validation and stopping gates

- [ ] Complete tracked-source and generated-payload inventory contains no
  first-party native source/binary, compiler build hook or executable embedded
  helper compilation. The guard distinguishes existing assembly loading.
- [ ] Linux source packages and release artifacts construct and verify with
  compilers absent, without downloading a compiler or copying a foreign
  runtime. Pure-Python wheel and source binding remain positively checked.
- [ ] Actual processes prove owner-loss expiry, renewal and hard maximum,
  same-PID admission, PID reuse refusal, descendant reparenting/settlement and
  interrupted cleanup under the existing bounded test owner.
- [ ] Production remains non-expiring. Protected/live/foreign/changed state and
  inconclusive inspection remain explicit holds; cleanup remains limited to
  declared disposable artifacts.
- [ ] Windows proves uv-managed Python prerequisite ordering and genuine
  cold plugin-runtime bootstrap, native-handle identity,
  authenticated pipes, operation-lock compatibility, filesystem custody,
  strict Job containment and zero unintended windows/focus changes, without
  embedded compilation or emitted interop types.
- [ ] Linux/macOS and Windows launch/source/runtime adapters preserve their
  independently installed and version-skew contracts; unsupported facilities
  refuse explicitly rather than falling back to PID-only signaling.
- [ ] A fresh compiler-free box runs the existing leased-instance and Manager
  maintenance scenarios. Portable units, cold construction and native desktop
  acceptance remain distinct; pytest and production deadlines are unchanged.
- [ ] Publication, required CI and independent review pass through merge.
  Release and authorized installation/running-state acceptance are recorded
  separately. No runtime rollout is authorized by this proposal.

The phase is not complete while any first-party native implementation,
compilation path or required positive bootstrap/safety proof remains. Transferring
a source slice to its existing owner does not declare the operator's complete
compiler-free outcome delivered.
