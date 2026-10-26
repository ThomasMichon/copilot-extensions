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
   Thin shell entrypoints select an attributable supported interpreter and
   invoke that shared implementation. Preserve same-process admission where
   required; do not add a central supervisor service or another inventory.
3. Replace Linux C supervision/reconciliation with standard-library Python and
   existing OS interfaces. Preserve owner loss, renewable and hard deadlines,
   unreaped child identity, descendant reparenting/settlement, exact artifact
   custody and explicit unknown outcomes. Small platform bindings through
   `ctypes` call existing OS libraries; they do not compile new code.
4. Qualify the interpreter/bootstrap boundary before migrating Windows
   pre-Python callers. Preserve authenticated pipe endpoints, process handles,
   strict atomic Job containment, headlessness, operation-lock compatibility,
   filesystem/reparse/ACL checks and uncertain launch outcomes. Do not invent
   a PATH-Python dependency or declare an impossible bootstrap successful.
   An unresolved bootstrap design blocks that slice before mutation.
5. Remove canonical and generated native sources, compiler setup/build hooks,
   custom native artifact export/verification and embedded compilation/code
   emission. Keep pure-Python artifact integrity and normal dependency/source
   admission. Regenerate distributed helpers through their owning generators;
   do not manually patch installed copies or loosen source verification.
6. Add ownership-aware guards, update current reality docs and requalify actual
   minimal-machine construction, process safety and affected consumers. Old
   compiled-helper evidence is historical, not replacement qualification.

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
- [ ] Windows proves genuine cold/pre-Python bootstrap, native-handle identity,
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
