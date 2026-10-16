# Guest Execution Identity - Phase 3b Proposal

Parent: [Machine Alias Compatibility and Locality](README.md).
Tracking: [#6945](https://github.com/ThomasMichon/copilot-extensions/issues/6945).

## Requested outcome

The designated distro containing worktrees is the host's default WSL guest.
One compact host/guest logical address selects that environment; the actual
distribution name may differ without requiring another address suffix.
Persistent virtual machines can use the same host/guest convention.

The shared machine map remains the owning logical-address authority.
Installation ownership must recognize the same actual guest regardless of
direct WSL, SSH, service or interactive entry, while native and foreign guest
environments remain distinct. Missing identity is not evidence of ordinary
native POSIX execution.

The standing intent extends
[`host-qualified-guest-identity`](../../../visions/agent-fabric/README.md#host-qualified-guest-identity)
and closes installation-cells'
[`stable-installation-identity`](../../../visions/plugin-services/installation-cells/README.md#stable-installation-identity).

## Current failure

The installation primitive derives the optional WSL distribution solely from
an explicit argument or `WSL_DISTRO_NAME`. SSH can omit that variable even
inside the very same guest. A previously installed lifecycle owner then has
a named WSL environment while the caller appears to have no distribution.
The ownership comparison correctly refuses different values, but the caller's
input is incomplete and its diagnostic hides the specific mismatch.

## Proposed implementation sequence

The sequence and safeguards below are **agent-recommended**, subject to the
normal proposal review gate.

1. Reuse existing platform detection and machine/substrate authority. Preserve
   independent plugin operation: installation-context must not require a
   sibling daemon, a repository checkout or a second topology registry.
2. Resolve a named guest from authoritative substrate evidence where available.
   Where the substrate cannot expose its registered name to SSH, provide one
   explicit, user-owned guest registration established through a verified
   named direct entry. Prefer an existing owning declaration over a new state
   file. A runtime owner receipt cannot bootstrap or overwrite caller identity.
3. Keep logical address, registered distro name and host-default selection
   separate. An established guest remains explicitly selected by launchers.
   Report a default/registration mismatch instead of silently following a
   changed default. A supported repair may change the default only with
   operator consent; it does not rename a distro or move worktree data.
4. Share the same identity inputs, precedence, refusal reasons and supported
   registration/repair behavior across Python, Bash and PowerShell. Preserve
   source ownership, independent-space fences and namespaced-mode opt-in.
5. Make report-only diagnostics distinguish missing, conflicting and foreign
   guest evidence from an incomplete lifecycle operation. Name the mismatching
   dimension and supported repair without exposing private identifiers.
6. Land the implementation through bounded native validation and ordinary
   review, then qualify its containing release. Only authorized rollout and
   independent installed acceptance close this slice.

## Validation Plan

- [ ] Same registered guest, profile and installation resolve identically
  through direct, SSH-without-WSL-variables, supervised and interactive paths.
- [ ] Real guest detection does not classify a container sharing a WSL kernel
  as that guest, or a native Linux/Windows caller as WSL.
- [ ] Foreign distro, different profile, conflicting observed/declarative
  identity, malformed registration and ambiguous mapping remain blocked.
- [ ] No registration or diagnostic obtains authority by copying an incumbent
  command binding, lifecycle reservation or historical worktree label.
- [ ] Logical host/guest names need no extra distro suffix; a named persistent
  VM remains distinct from the default WSL role.
- [ ] The designated worktree distro is verifiably the host default. A later
  default change is reported without redirecting existing worktrees, changing
  their records or implicitly changing the OS default back.
- [ ] Python, Bash and PowerShell agree on registration, resolution, refusal
  and report-only behavior; lone-plugin and legacy-default operation work.
- [ ] Actual diagnostics leave records, registration, lifecycle markers and
  unrelated profiles byte-for-byte unchanged; repair changes only its explicitly
  approved target through the supported owner.
- [ ] Existing installation-context, command-binding and lifecycle guards
  pass through the bounded runner, with focused native boundary coverage.
- [ ] The containing release and authorized installed update succeed, followed
  by real launch/update acceptance across both entry paths.

## Boundaries

No new daemon, physical-host inference, suffix-derived authority, copied owner
identity, automatic distro rename, worktree relocation, ownership reset or
activation of namespaced installations. The existing compatibility, transport,
release and independent installer/preview repairs keep their own owners.
