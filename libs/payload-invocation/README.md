# Payload Invocation

Canonical generator for payload-local plugin command shims.

A runtime plugin declares `payload-invocation.json`; the generator renders
equivalent POSIX, PowerShell, and CMD entry points plus POSIX/PowerShell session
command-catalog emitters. The installed marketplace payload therefore carries
the command that invokes its own runtime instead of relying on a same-named
global command found through `PATH`.

## Contract

Generated shims:

- derive and validate the payload from their own file location;
- leave a replaceable payload working directory before provisioning or launch;
- resolve the runtime only through the payload's canonical runtime resolver;
- preserve caller arguments and child exit status;
- send enabled Python bytecode caches to the resolved legacy state root's
  `cache/python`, outside version slots (extended paths for native Windows
  interpreters, including Windows Git Bash), with installation-scoped reuse or
  fresh invocation namespaces under the
  [shared cache contract](../agent-procutil/README.md#managed-python-bytecode-cache);
- self-provision only through the same payload's installer/snapshot;
- never scan installed marketplaces or resolve a sibling command through
  `PATH`.

`outputDir` defaults to `bin` and may name a nested payload-only directory when
a plugin still uses its historical top-level `bin/` files as legacy global
wrapper sources. Catalog emitters always publish the exact generated command
path. `windowsCatalogShim` defaults to `powershell`; a plugin whose stdio
contract requires entry through a native process may select `cmd`, in which case
the Windows catalog names the generated `.cmd` and reports `shell: "cmd"`.
Callers invoke that path through the platform shell and should pass structured
input through stdin or a request file rather than inline CMD arguments; the
generated Windows catalog says so beside the `argv` (`cmd.exe` ends a command at
a newline, so a multi-line argument is silently cut off).
`powershellPipelineInput: true` explicitly opts a payload dispatcher into
forwarding PowerShell object input. The dispatcher must likewise distinguish
object pipelines from native redirected stdin and preserve raw native bytes;
Windows PowerShell's `ExpectingInput` alone does not distinguish those paths.
The opted-in launcher suppresses PowerShell progress records, not errors, so
module auto-loading cannot add CLIXML to its stderr transport.
`provisionMode` defaults to `snapshot`, where first use stamps the owning
payload and provisions through its published snapshot. A self-staging installer
may select `direct`; the payload shim then invokes that same installer with
`provision` and never consults a shared snapshot pointer.

Manifest version 1 keeps the legacy runtime-root contract.
Manifest version 2 renames that field to `legacyRuntimeRoot` and may set
`installationContext: "required"`. Required-context adoption is accepted only
for runtime-bearing core `agent-*` identities defined by this suite; an
independent marketplace may carry the same identity, but an unrelated or
payload-only plugin cannot opt itself into installation cells. A required
manifest delegates both platform shims to payload-owned context dispatchers,
which must preserve legacy behavior for absent/false policy and fail closed
instead of falling back when namespaced evidence is requested, malformed, or
blocked.

Required-context dispatchers own their resolved state root and must apply the
same interpreter cache policy there before importing runtime packages; the
outer delegating shim cannot choose a legacy root for a namespaced invocation.
Read-only `-B` bootstrap and probe invocations retain their no-write policy.

Dispatch's [command admission](../../plugins/agent-dispatch/README.md#install)
uses this contract while preserving its separate global upstream-first and
payload-local provisioning routes and explicit scoped-installer readiness.

### Test isolation

Copied runtime fixtures materialize the selected consumer's declared dependencies
into the copied payload. Their import paths contain only staged sources, never
canonical checkout libraries. The Worktrees import/version regression also runs
without Python site initialization, loading only the external YAML dependency
explicitly; missing staged transport dependencies cannot be hidden by an ambient
editable installation.
Dispatch-only fixtures may omit runtime sources only when execution is refused
before dispatch or the test supplies its own explicit CLI stub. Tests invoking
the real Worktrees CLI retain the complete copied runtime and staged dependencies.

### Source-qualified command binding

The normal release-candidate materializer now delegates Invocation output
publication to `tools/materialize_payload_invocation.py`. It computes the
existing manifest-declared output set from canonical source after version
stamping, then uses the generator's own writer inside the candidate. This
includes ordinary/namespaced helpers and the self-contained admission toolkit;
the old toolkit inventory is not an authoritative source for rebuilding itself.
The adapter never activates the toolkit or runs an emitted program.
Executable generator and packager tooling are loaded only from the pinned
tooling checkout; the explicit canonical root supplies data, never Python
tooling to import or a replacement for the tooling's own module globals.

Dev CI explicitly uses `generate.py --all --source-check`: it computes the
complete canonical output inventory, validates destination custody and Python
grammar, and neither writes nor requires old derived mirrors. Existing output leaves
must be ordinary single-link files and existing ancestors must be directories.
Planned output files cannot also serve as directories for other outputs, even
when both are absent. Absent outputs otherwise remain valid source layouts. This is not a
claim that existing output bytes match. `--check` retains actual-output
comparison; the candidate materializer additionally verifies exact binary/text
bytes and executable modes after writing, before composition can freeze.

Source preparation and single-plugin previews use this same owner with their
actual canonical consumer and copied destination. They regenerate missing or
stale declared outputs rather than relying on Dev mirrors. A changed candidate
manifest or unsafe output plan refuses before the prepared source or preview
metadata can be published; generation still never executes candidate tooling.
The packaged toolkit includes its pinned generator and admission packager at
their original relative paths, including the file-loaded tooling edges that
static import closure alone cannot discover. Manager's separate preparer tool
declaration includes the Invocation adapter it imports.
Manager's existing launch-wrapper asset declaration also places the generator
and packager under `scripts/libs/payload-invocation`, the adapter's trusted
loader location. Raw-source retention addresses both declared asset groups;
the canonical Versioned Runtime build helper selected by Manager's `setup.py`
retains those tooling paths in the wheel too. The developer fallback helper is
not the source authority for that resource policy.

The remaining canonical-first witness transition is still open: source-bound
catalog checks and their current fixtures bind real consumer/helper bytes, and
the normal checker has no qualified source-versus-materialized phase input.
Those checks are not weakened or silently redirected here. An explicit
owner-supplied phase and exact positive unit/candidate origin bindings must
precede removing these remaining Dev-copy dependencies.

`runtimeAdmissionToolkit: true` packages the canonical admission/build helpers
under `scripts/_runtime_admission/` for a required-context payload. It defaults
to false and changes neither command/catalog bytes nor installer behavior.
The generator preserves the helpers' own internal resource layout, including
dynamic constructor/process modules and trusted native-build templates; no
source checkout or sibling installed plugin supplies missing files.
Copied installation-context adapters retain their sibling JSON-query script
and first-party caller-lifetime archive. Generation and stale-content checks
preserve the archive's binary bytes rather than decoding or rewriting it.

An installation owner invokes `_runtime_admission.activate()` in its separately
admitted Python 3.11+ tooling environment with its exact, governed Packaging
selection (`packaging>=23`). The toolkit contains first-party source only.
Its inventory describes delivery, not source approval, an authenticated
installation, a selected dependency profile or permission to install.
Existing source authentication, native compilation/acquisition, wheel inspection,
builder and immutable constructor remain the owners of those decisions.
The repository's Manager installation adapter is `tools/runtime_source_manager.py`;
it is not part of this reusable toolkit. Subject attestation remains the same
pinned hosted-workflow verifier, factored out of the publishing adapter.
The shared index schema and byte limit belong to that reusable attestation
module. Release-profile readers import them without reaching the repository's
publication adapter; the publisher retains the same exported index contract.

`artifact_release_discovery` packages the existing read-only frozen-source
channel reader. It retains the `python-artifacts-<dev-commit>` tag, exact
source/composition, trusted Main ancestry, subject attestation and complete
asset-set checks without importing publication tooling. The source acquisition
owner supplies ancestry against its independently acquired history. Only an
actual release-channel 404 returns absence; permission, malformed metadata,
conflicting tags, integrity and provenance failures propagate. Discovery reads
the bounded index, not executable bundles, and grants neither profile approval
nor source-fallback authority. Promotion callers retain thin compatibility
adapters over this same reader. Ordinary installer composition remains separate.

`runtime_profile_delivery` retains a caller-selected delivery and its inspected
wheel/material inputs using the existing custody owners. It authenticates neither
the delivery's publisher nor the profile, source or feed policy.

The payload-only native witness authenticates a synthetic Git source, admits
its synthetic feed closure, builds first-party wheels, constructs and health
checks an offline runtime, then reuses that same completed generation. It
denies Manager/CLI/publishing imports and checks loaded first-party locations.
The separate admitted-seed witness imports every toolkit entry and runs actual
semantic profile validation with only its governed Packaging wheel installed,
including refusal of an incomplete dependency closure.
Copied POSIX and PowerShell adapter tests execute source-identity selection
without importing a checkout or creating installation state.
This qualifies toolkit delivery, not normal-consumer enablement, production
feed access, credential-key ACLs, ordinary first-tooling integration or measured
speedups.
The credential-free witness uses the existing deterministic opaque-identity
test seam; separate native controls own keyed feed/account identity.

`installerLifecycle: true` packages the existing command-binding/lifecycle
helpers for a required-context or legacy version-1 installer's admission without
changing command catalogs or enabling their additive binding field. A legacy
installer still requires its vendored installation primitive; SDK packaging
does not enable installation cells or required-context dispatch. The installer must invoke
that admission before staging and authenticate a borrowed ticket on reentry;
packaging alone does not enforce it. Logger is the first installer-only adopter.
All current required-context Python runtime payloads package this SDK for
installed-owner source-update validation and lifecycle execution. This does not
convert the legacy version-1 manifest or change any command/catalog rendering.
Its real managed-first-stamp regression and standalone/custody checks run in the
ordinary suite. The full policy/action/root matrix is an explicit T3 portfolio:
`python tools/run-plugin-tests.py payload-invocation --test-file
tests/test_adopter_publication.py --allow-explicit-tiers`.

`catalogBinding: true` opts a required-context runtime into the additive
`binding` field of the existing version-1 session catalog. Agent Worktrees is
the first adopter. Other manifests retain byte-identical catalogs. The optional
`catalogBindingModule` vendors the same pure ownership code into the runtime
package for its existing deploy-manifest publisher.
Binding emitters use isolated Python, including the PowerShell launcher
bootstrap and binding helper: neither the project working directory, inherited
Python import paths, nor user-site startup hooks supply discovery code.
Only the explicitly addressed payload helper supplies binding code.
The PowerShell launcher admits its interpreter and runs that helper in the same
process, avoiding a second Python startup. An unavailable launcher can fall
back to a non-WindowsApps interpreter; an admitted helper's failure or empty
response cannot trigger a retry. Each emission reads current ownership again:
neither interpreter admission nor a prior catalog caches a binding decision.
`COPILOT_EXTENSIONS_BOOT_TRACE` adds timestamped binding stages to stderr only;
ordinary emission adds no trace output or diagnostic subprocess.

The binding contains normalized source identity/fingerprint, plugin identity,
and a selected installation only when its ownership has been validated.
`status` is `selected`, `unresolved`, or `blocked`; refusal includes a reason.
Legacy ownership is the `commandBinding` record in the existing schema-3
deploy manifest, published by the Python, POSIX, and PowerShell installer
boundaries. A payload path, display name, repository field, or old manifest
without that record is not ownership. Publication refuses a conflicting
incumbent. Validation and atomic manifest replacement share the installation
primitive's token-owned lifecycle lock. Legacy `stamp`, `provision`, `install`,
and `update` acquire that lock and validate the incumbent before any shared
runtime writes and hold it through activation and publication. Native admission
precedes self-staging, including stage creation and sibling-stage reclamation.
The admitting parent holds the same lease through the staged child, which
revalidates the inherited ticket before its own staging decision; an inherited
stage flag does not waive ownership. A staged-origin hint is used only by a
child whose live parent lease authenticates that source. Direct callers discard
an unauthenticated origin override and derive provenance from their own payload.
Runtime-building
actions reserve a
`pending` lifecycle owner in the existing manifest. Partial failures retain
the reservation and cannot produce a selected binding. Native children borrow
only a matching live, same-host token whose source and runtime root are
revalidated; environment-variable presence alone never admits mutation.
Successful publication marks the reservation `complete`.
Missing or syntactically corrupt legacy deployment metadata does not veto
construction. Its stably read bytes are distinguished from storage/identity
errors, which still refuse admission. Recovery uses the existing finite
removal profile and exact native command recognizer, including Index's dedicated
launcher matcher, against the resolved profile home to protect incumbent global
commands; edited or foreign-root commands are not implicitly replaced. Intact
incumbent publisher paths remain literal matching data for relocated payloads,
not executable sources or a substitute for command ownership. A pending
reservation never supplies missing footprint-recognizer evidence: only final
publication installs a complete schema-3 deployment. A failed recovery retains
its pending source owner, so another source cannot take over a partial build.
Explicit foreign service, binding, reservation, policy and cell evidence still
refuses mutation. Prepared-source currentness likewise treats missing or
malformed deployment bytes as not current, without swallowing storage errors.
Deferred `stamp` holds the same admission lock but neither reserves an
unfinished runtime build nor publishes new selectable ownership. First-use
`provision` performs the runtime-building transaction.

The same admission checks the addressed installation's runtime-deployment
policy before creating its lock or reservation, again under the lock and on
every lease ownership assertion. Managed plugin publication is refused, including
deferred stamping and direct manifest publication. Missing policy preserves
standalone behavior; an older helper may proceed only when no policy exists.
Malformed, unsupported or foreign policy cannot authorize publication.

An explicit management caller may use `--managed-deployment` with
`--run-lifecycle` (never teardown or a read/check operation). This requests
management purpose on the existing token-owned lock, not another updater or a
source-ownership bypass. The lock owner's additive `runtimeDeployment` record
binds version, origin and publication/teardown operation. Borrowed children
derive their purpose from that live durable owner and revalidate it; a plugin
lease cannot upgrade itself by passing the flag or an environment hint.
Every borrower also declares its expected operation: publication is the
default; native uninstall passes `--lifecycle-operation teardown` to
`--check-lifecycle`. The native installers check this before staging and again
before dispatch, so a valid teardown ticket cannot admit a stamping or helper
publication action.
Management admits a valid managed policy but still requires the original
installation, source and environment ownership. Teardown remains available
independently of deployment policy, and its lease cannot publish a manifest.
This API is a composition point for explicit management, not proof that every
native bootstrap or installation-cell caller has adopted it.

Windows callers with a bounded grace window may supply `--lifecycle-timeout <seconds>`
with `--run-lifecycle`. The admitting parent owns this deadline: it stops the
native installer tree and waits for the child before releasing its lease.
Windows uses the payload's bundled `agent-procutil` strict, non-breakaway
kill-on-close Job and verifies zero active processes before releasing ownership.
Bounded mode is refused before admission on other platforms; a POSIX process
group does not provide the same verified descendant-containment contract.
A timeout returns exit code 124. Failed cancellation or an unverified empty Job
is reported and leaves a fail-closed durable lock, not a ready-looking runtime;
callers must not hard-kill the admitting parent as their normal deadline
mechanism.

Standalone Worktrees `install` and `update` explicitly request
`--human-operation install|update` from this existing admission parent.
PowerShell keeps its case-insensitive action contract and forwards the original
native action unchanged; only the human-operation label is normalized.
It uses the canonical human formatter and redactor copied into a small
payload-local `_lifecycle_human` package. All three assets, including the
entrypoint-bound capability probe, come from `libs/update-telemetry`, not
another plugin's runtime source; no installed runtime, extra Python
process or formatter process is needed. Native output is merged onto the
human diagnostic stream with UTC prefixes and monotonic elapsed time, followed
by the child's actual success/failure duration and exit code. PowerShell
capture requires the existing entrypoint-bound UTF-8 declaration. The parent
retains the same lease, argument vector, execution deadline and Job cleanup.
An admitted but unattributed source is classified as a warning in human mode,
not as selectable ownership; its ordinary unframed stderr contract is unchanged.
Without explicit opt-in, child stdout and stderr remain inherited separately;
catalogs, protocol callers and other lifecycle actions do not infer human mode
from their output or environment. Structured-context installers retain their
separate native owner; this does not wrap those entrypoints.

Lifecycle/publication CLI callers may pass `--project-root` to retain their
launch project's source declarations while moving waiting supervisors out of a
replaceable payload. Omission retains the original current-directory behavior;
the option is not accepted by ordinary read-only catalog discovery. Logger and
Worktrees carry that read context across native staging while the same live
lease still validates source/root/purpose. Worktrees also retains it through
Python-owned helper publication and native manifest publication. Changing the
working directory must not qualify an unresolved source or replace the owner
of a pending reservation; an unattributed retry remains unattributed until its
own successful publication clears the reservation. On Windows Bash, re-executing
a command-string Bash before native Python avoids retaining the payload script
descriptor in MSYS's native-exec parent.

Legacy `uninstall` uses the same ownership-validated transaction without a
runtime-build reservation. The shared lock is a sibling of the canonical
runtime root, so deleting that root cannot remove the lock or admit a concurrent
installer. Obvious foreign ownership is refused before lock creation and is
revalidated after acquisition. Teardown keeps the lock until the native child
finishes, including configuration removal, and requires parent and child
interpreters outside the erased runtime. After teardown, lock ownership is
verified without requiring the intentionally removed payload or manifest.
Its metadata validation uses the teardown purpose, not permission to replace
global commands. The removal inventory retains foreign commands while source,
reservation, root and lease-purpose checks remain enforced; that exception
cannot authorize publication.

An unavailable source on an unowned legacy installation is reported without
breaking its existing command. An explicit install can still use the canonical
legacy probe's missing-policy/default-false admission, under the same lock,
but records only a pending unattributed mutation reservation and emits no
selectable source binding. Successful unattributed publication clears that
temporary reservation rather than creating a path-only durable owner that
would block a later authorized update or source qualification.
An explicit policy, conflicting source, or invalid incumbent
cannot use that compatibility path. Namespaced native installers retain their
separate schema-3 manifest writer; they never publish legacy ownership.

Namespaced selection validates the active install receipt, exact payload,
source fingerprint, and governance, and carries install/activation generations.
Legacy selection validates source, plugin, payload, runtime root, and operating
environment against the lifecycle record. Its installation id remains stable
across an authorized payload refresh; the old payload cannot use the refreshed
owner's binding. Discovery never writes metadata, repairs ownership, provisions,
or activates a cell.

The exact `commands[].argv`, shell convention, and availability remain unchanged.
Availability means the payload shim and installer exist, not that a runtime is
provisioned or healthy. Binding selection proves ownership/governance, not build
completion or liveness. Existing consumers remain compatible; a source-qualified
consumer must require `binding.status == "selected"` and retain the catalog's
argument vector rather than deriving an executable from the runtime root.
Windows binding discovery prefers the real Python 3 interpreter reported by
`py -3`, then a `python3` or `python` application outside `WindowsApps`;
Store app-execution aliases are not interpreters. Missing Python reports an
unresolved binding while retaining the supported payload command.
Stable out-of-session launchers and other runtime
adopters remain separate migration work.

## In-process session catalogs

`inProcessCatalog: true` additionally generates `scripts/emit_command_catalog.py`
from the same manifest command specifications. Exact-session writers load that
absolute payload-local source and call `render_context()` without starting a
catalog shell or another Python interpreter. The generated module is stdlib-only,
prints nothing, and performs only fresh payload-path/availability checks. It is
not a runtime selector, persisted memo, installation permission, or readiness
claim. Missing runtime/configuration does not hide an otherwise available payload
command. Direct POSIX/PowerShell catalog emitters retain their public contract. PowerShell resolves its payload root literally, including bracket-containing paths.

The opt-in is boolean and currently cannot combine with `catalogGate` or
`catalogBinding`; those independently owned policies are not silently omitted.
Windows command suffixes and CMD input warnings, POSIX executable/file predicates,
and multi-command ordering are preserved. Missing or malformed module source
leaves the writer's existing bounded unavailable guidance and empty hook response;
it never falls back to activation or a subprocess ladder. The writer compiles
the selected source in memory, without publishing Python bytecode.
Source acquisition is capped by the writer's existing 64 KiB input budget;
oversized source is unavailable rather than executed. Unexpected helper stdout
is redirected to diagnostic stderr during both module loading and rendering,
preserving the exact empty-object hook response even for a noisy broken helper.

This closes the narrow redundant catalog-process path in the circumstantial-hook
contract; it does not establish reusable full discovery facts or whole-hook
performance qualification.

## Usage

```bash
python libs/payload-invocation/generate.py \
  plugins/agent-index/payload-invocation.json

python libs/payload-invocation/generate.py --all --check
```

Generated files are committed. CI runs `--all --check` so templates, manifests,
and payload copies cannot drift.

## Static checking

The canonical pending project also checks four portable in-process catalog
witnesses (single/multi command and PowerShell/CMD catalog). The fixed
`python-catalog-inputs.json` declaration binds the real validator and renderer;
the checker never imports the generator. Diagnostics retain template lines or
the separate flight-expression source range. These witnesses do not qualify
actual consumer copies, shell execution, toolkit activation or native runtime
behavior, and do not change this project's pending status.

The four canonical programs belong to `lib-payload-invocation` in
`tools/typecheck-projects.json`, checked on Python 3.10 and 3.11.
The test/helper tree belongs to `lib-payload-invocation-tests`, checked on
Python 3.11: its native installer fixtures import `tomllib` and the real
installer backend, which also imports `tomllib`. The packaged admission
toolkit independently requires 3.11 tooling. These tooling requirements
do not raise the Python 3.10 floor of ordinary generated command shims.
The checker uses the existing hash-locked YAML SDK, including official PyYAML
stubs, for the test suite's YAML boundary.

```text
python tools/typecheck.py lib-payload-invocation lib-payload-invocation-tests --prepare
python tools/typecheck.py lib-payload-invocation lib-payload-invocation-tests --strict
```

Real installer import roots are declared on the test project. Admission remains
pending until emitted Python source correspondence and the native, candidate
and generated-copy qualification lanes pass. Checking the physical files alone
does not check Python embedded in catalog templates or test fixture strings.
JSON records and dynamically loaded installation modules retain explicit
dynamic boundaries; the test host factory and subprocess results are typed.
