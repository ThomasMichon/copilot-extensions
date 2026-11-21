# Protected installation-owner entry

The optional `linux-root/v1` provider composes
`install_source.install_application`; it is not another constructor or an
elevation facility. Ordinary Source/application callers, syntax and defaults
are unchanged. Windows, macOS, non-root Linux identities and developer-layout
fallbacks are unsupported by this provider.

## Trust before execution

An existing, independently authorized native installation owner must install
and select the complete packaged installer family, its policy, the prepared
Source snapshot and its complete wheel profile, UV and the OS interpreter.
The controller cannot supply executable overrides or approve these inputs
with a flag, package receipt or a manifest's own hash.

The native owner launches its fixed, already protected
`bootstrap-protected-install.sh` with no arguments, using trusted OS Bash and
a sanitized process environment. This protection must exist **before Bash
starts**, including removal of dynamic-loader overrides. Do not elevate a
user-owned copy of the bootstrap or an ordinary controller CLI. The script's
own checks and descendant environment reset cannot retroactively establish
trust in code or a loader that has already run.

The bootstrap uses fixed OS tools, resolves `/usr/bin/python3` to its physical
protected executable, and invokes the adjacent provider with `-I -S -B`.
GNU Bash, `stat` and `readlink` at the named OS paths are prerequisites.
The provider requires the OS CPython 3.11+ interpreter, not a virtual
environment. Site initialization, caller Python paths, stdin commands and
controller-selected arguments are not used.

The initial source implementation requires all addressed directories to
exist under root-owned ancestry with no group/other write permission.
Code, Source and wheel inputs must contain ordinary protected files and
directories. Protection is retained through use by native ownership, not a
check followed by execution from a controller-writable copy.
The independent seed-installation owner must publish fresh protected file
copies or a native package owner's equivalent. Merely changing ownership/modes
of controller-owned files does not revoke a controller's already-open writable
descriptors and is not supported adoption.
Supported native filesystems are ext2/3/4, XFS, Btrfs and tmpfs.
The provider checks the actual mount table and device/subtree coordinates,
so bind-mounted aliases cannot make input and output roles appear disjoint.
Nested mounts inside an addressed tree and ambiguous stacked mounts refuse.
OverlayFS is refused because its merged view and directly addressed backing
layers do not share reliable overlap coordinates. DrvFS/9p,
network and FUSE filesystems are refused rather than treated as equivalent
mode-bit enforcement.

The selected UV must be a static native ELF executable, not an interpreted
script or a dynamically linked binary. Its bounded ELF program table must
contain neither `PT_INTERP` nor `PT_DYNAMIC`. Dynamic libraries, RPATH/RUNPATH
and interpreter resolution are not supported by this initial provider;
static-PIE binaries containing a dynamic section also refuse.
This keeps a controller-operated intermediate mount or library search path
out of the selected UV execution chain instead of implementing another loader.
The native bootstrap protects this ELF-inspection helper before Python starts;
the helper is imported only after admission of the complete protected seed.
The OS installation, its interpreter/standard library/native dependencies and
the selected UV executable remain independently trusted prerequisites. This
provider acquires none of them, creates no privileged prefix, writes no native
launch rule and grants no authority to install its seed. A source declaration
or inert ownership fixture does not qualify those native prerequisites.

## Installed policy and capability

The native owner installs `protected-installation.json` **beside** the
provider module. The bootstrap accepts no policy-path, root, source,
toolchain, callback or identity overrides. Only the closed
`install-prepared-application` operation is supported. Its identity is real
and effective Linux UID/GID zero.

The following paths are illustrative administrative selections, not defaults
or an instruction to create a particular live prefix:

```json
{
  "contract": {
    "schema": "copilot-extensions.protected-installation",
    "version": 1,
    "provider": "linux-root/v1",
    "entrypoint": "bootstrap-protected-install.sh",
    "operation": "install-prepared-application"
  },
  "snapshot": "/opt/example-source",
  "component": "example-app",
  "root": "/var/lib/example/runtime",
  "uv": "/opt/example-tools/uv",
  "health_module": "example_app",
  "expected_version": "<independently selected prepared runtime version>",
  "expected_hash": "<independently selected prepared payload hash>",
  "home": "/var/lib/example/private-home",
  "cache": "/var/lib/example/cache",
  "temporary": "/var/lib/example/temporary",
  "global_bin": "/usr/local/bin",
  "arguments": [],
  "repair": false
}
```

The version/hash values come from the existing admitted prepared Source/profile
selection, not from invented version strings. `global_bin` may instead be
`null`, retaining the existing application's per-home publication default
within this policy's private home. All fields are required; unknown or duplicate
fields, a changed contract and an absent policy refuse before construction.
Output roles are disjoint from one another and from the seed, Source and UV.
Every existing output directory, including nested directories, must have no
POSIX default ACL. Unavailable ACL inspection refuses; umask alone does not
override inherited default ACLs.
The selected command directory's existing contents must also be protected:
matching launcher bytes alone cannot make a controller-writable command safe
to reuse. A dedicated protected command directory can avoid mixing this role
with unrelated writable commands.
The native child starts with umask `077` and explicit policy-owned home,
cache and temporary selections, not the controller's stores.

`installer_protected_source.select_entry(selected_scripts_directory)` reads
the actual selected provider's literal `PROTECTED_INSTALLATION_CONTRACT` and
its native entry's declared provider header without executing either.
Missing, malformed, old or withdrawn
contracts have no protected fallback. This is ordinary capability discovery,
not independent source approval, a privilege grant or proof of native
protection. The native owner must still select that same protected entry.
The Python `admit`/`install` helpers are implementation composition seams,
not an alternative elevated controller entry.

## Construction, replacement and outcomes

After native admission, the provider imports the protected packaged installer
family and selects the existing Source application. Its complete local package
closure must remain inside the admitted snapshot. Existing Source/profile
association, closed-wheel retention, expected selection, numbered construction,
application callbacks, publication, readiness and uncertain-effect recovery
remain with their existing owners.
The package-containment guard retains the selected Source's closed-wheel mode:
its development metadata remains data, while the existing inspected-wheel
owners validate the complete runtime closure. Ordinary source-building inputs
retain their development-package and release-age policy.
The prepared POSIX install launcher must declare
`installer-uv-selection: absolute/v1`. The constructor passes the admitted
absolute UV executable through `COPILOT_INSTALLER_UV`, without adding UV's
directory to `PATH`; other tools retain the sanitized OS lookup. A legacy
launcher without this capability refuses before protected construction.
Ordinary non-protected callers retain their legacy launcher compatibility.
Capability inspection reads only the fixed launcher prefix; it does not impose
a new size limit on ordinary legacy launchers.
Prepared `agent-worktrees` applications currently refuse before construction:
their publication callback needs original command-owner context that this
closed provider policy does not supply. The provider does not inherit ambient
controller variables or guess that missing context.

No incumbent receipt, provenance or recovery approval is consulted by this
provider. Root-protected internal selectors may be dangling; they are not
followed during native inspection. Existing references to ordinary protected
files in the trusted OS interpreter prefix are also permitted, including
bounded symlink chains whose every link, directory and final file is protected
and remains inside that prefix.
Root-owned output-state sockets are preserved without opening or contacting
them; sockets cannot supply the seed or Source input closure.
Escaping writable/indirect content is a real native-protection failure,
not a reason to run an ordinary legacy form.
User state is neither removed nor reset. The provider does not reclaim a lock,
retire an outgoing runtime or clean up a failed candidate.

Admission inspects at most 100,000 entries per addressed tree within a shared
30-second inspection deadline. Construction uses the existing finite
process/deadline/cancellation and descendant-settlement owners; the native
launch owner retains the whole-operation lifetime and original effect limits.
Construction/startup exceptions propagate unchanged. No exception is relabeled
as a pre-effect rejection after dispatch, and this wrapper never retries it.

## Qualification boundary

Required promotion-unit controls exercise selected-contract discovery, actual
closed policy/native admission and dispatch through in-memory native
observation actors. They contain no filesystem or subprocess execution.
Separate focused integration controls exercise native filesystem refusal,
selected-owner capability discovery, the real Source application adapter and
unchanged error/custody propagation using inert metadata and constructor
fixtures. The ordinary-UID bootstrap negative executes without privilege.
They do **not** prove a positive privileged installation, native filesystem
protection against a second identity, installed lifecycle/readiness, cross-
platform support or deployment. Those require the existing owner's separate
authorization and native qualification.
