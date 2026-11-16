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
   Thin shell entrypoints use PATH `uv` to invoke the declared managed-Python
   project and that shared implementation without ambient Python lookup or
   assumed interpreter locations. Preserve same-process
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
   preserve a Python-free helper implementation. The acquisition front door
   itself must run before Python exists: on Windows, the native PowerShell
   seed establishes uv through its prerequisite owner and stages the real
   per-app setup entry. It does not load a Python-backed custody/installer SDK
   or require an application daemon to obtain that SDK's execution base.
   The seed is not another implementation of the shared helper protocols.
   Preserve authenticated pipe
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
prerequisite. The [current operator request and setup sequence](../source-managed-runtime-installs/managed-python.md)
extend it to every app launcher and supersede the earlier machine-wide/PATH
prerequisite. Each project declares the
Python version it needs; its existing installation owner uses `uv` to acquire
or reuse managed Python and construct the addressed environment. System/user
PATH Python need not exist or be compatible. Projects with different version
requirements coexist without a global Python change.

The shell-native explicit setup front door establishes `uv` and the declared
managed bootstrap interpreter before Python-dependent safety helpers,
installation-context evaluation or application construction. Bootstrap helpers
have static setup declarations too; they cannot require Python to discover or
acquire Python. Reuse this one acquisition owner, not a downloader in every
helper. Standalone setup works without an Agent Machines service or an already
installed Manager/application runtime.

The [finite installation-controller boundary](../../../visions/installer/README.md#finite-installation-controller)
keeps that prepared execution base separate from Manager's presentation and
application services. Installation management is directly invocable and finite,
including Manager's own install/update; it cannot require Manager or another
installation daemon to be running. Reuse existing installer-engine and
execution-owner code before proposing a separately packaged helper. Such a
helper remains optional and daemon-independent, not another installer stack.
Native foreground work retains its own lifetime/failure/cleanup obligations;
later Python helper and application-service protocols remain with their owners.

Every application launcher uses `uv` from PATH and lets it handle interpreter
selection for the declared project. Scripts do not assume installed Python
locations, including `uv` storage. The earlier rejection of interpreter
attestation still applies. Thin shells reach the setup-selected environment
through `uv`; they do not inspect signatures, rediscover
PEP 514 registrations, require a special physical namespace, pin interpreter
files or blacklist venv/worktree/temporary paths. Selecting the declared managed
interpreter is environment selection, not another admission service.
Source-only distributions retain their complete helper source through the
existing packaging owner.

Existing process, lease, update-driver and installation-context owners still
own their protocols and native OS bindings through ctypes. Trusting Python
does not relax process identity, atomic Job containment, authenticated pipes,
operation-lock compatibility or exact cleanup custody. Artifact/source receipts
for managed application runtimes remain distinct from interpreter attestation.

Explicit installation owns permitted acquisition and actionable offline,
unavailable-version, platform and OS-policy errors. Status, discovery and
ordinary launch only resolve an already-prepared managed environment; they
never acquire Python, silently use ambient Python or compile an interop helper.
The [managed-Python contract](../../../docs/install-contract.md#project-owned-managed-python)
owns declaration/construction details and retained-environment safety.

Before removing a Windows compiled implementation:

- [ ] Replace remaining compiled interpreter admission with invocation of the
  setup-selected, project-declared `uv`-managed interpreter.
  Remove signature/publisher, registry-only, PE/path-attestation and physical
  namespace launch gates without adding venv/worktree/temporary-path filters.
- [ ] Apply the same setup-owned prerequisite boundary to shared installer
  Python rediscovery and signature-driven base selection. Preserve copied-venv
  construction and module invocation where needed, without reimplementing
  Windows code-integrity policy or changing package/feed integrity checks.
- [ ] Exercise setup with no Python, old/incompatible Python and differing
  project versions on one host, an active venv, absent launcher and fresh
  terminal. Qualify offline managed reuse, missing-version and startup failure
  without ambient fallback or per-helper attestation.
- [ ] Prove the actual first-entry order: shell-native `uv` setup, declared
  managed bootstrap interpreter, standard-library helper, then each project's
  runtime construction. No Python-dependent probe precedes acquisition.
  Prove existing supported environments and rollback survive preparation.
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
- [ ] Windows proves project-owned managed-Python prerequisite ordering and genuine
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
