# Installer engine source and derived payloads

Edit canonical implementation files here. Contributors do not refresh committed
developer copies to publish a change: the Dev-to-Main composer materializes the
declared installer family in its candidate tree before freezing that tree.

POSIX `ensure_uv` reuses a working UV application or downloads the pinned
release with curl, then wget. It never uses ambient Python to acquire UV.
If neither native downloader succeeds, setup reports the missing prerequisite
and removes its temporary archive and staging directory. Successful downloads
still require the pinned SHA-256 before extraction.

Both native environment constructors retain the requested Python version and
require UV-managed selection. A failed attempt no longer retries with an
unspecified interpreter or accepts a partial environment after UV fails.
Windows signed-copy construction remains available when UV identifies an
already-prepared, signed managed interpreter; it never discovers a signed
system interpreter through `py`. Existing complete environments retain their
reuse behavior. This does not qualify managed Python on policy-enforcing hosts
or migrate the installers' earlier activation and bootstrap readers.
The legacy `Get-SignedBasePython` discovery API remains unchanged for
unmigrated consumers; these constructors use the separate managed owner instead.
On Windows, when UV refuses a bare `X.Y` request because Windows
RedirectionGuard rejects its minor-version link (`os error 448`, for example
on Microsoft Dev Box), the PowerShell constructor retries once with the
highest exact `X.Y.Z` managed interpreter that UV itself reports: it lists
candidate patches with `uv python list X.Y --managed-python --all-versions`
and confirms each with `uv python find X.Y.Z --managed-python`, neither of
which reads UV's on-disk layout or the refused link. It
still requires UV-managed selection and never falls back to another minor.

The shell-native `resolve_uv_runtime_python` adapter selects an already-resolved
completed application interpreter through PATH uv, without configuration/project
discovery or downloads. It refuses any different returned path. Generation
selection stays with the runtime resolver; the caller executes the selected
interpreter in place when native PID ownership precludes a `uv run` child.
This does not provision a runtime or attest an older environment's base.

`runtime_application_install.py` owns ordinary numbered Source/profile
applications. Its read-only `plan_application_removal` validates completed
construction, exact selectors and generated commands, ordinary directory identity,
and an external helper/interpreter. `application_removal_custody` revalidates that
observation under the existing generation and shared-command publication guards.
Live runtime references refuse exclusive custody. These APIs do not stop services,
withdraw plugin registrations or delete files; they are not an uninstall command.
Manager and legacy runtime removal retain their separate ownership contracts.

The entry delegates declaration, Source/profile, wheel and callback checks to
the co-located `runtime_application_qualification.py` component. It passes its
original hooks and clocks explicitly, preserving copied-entry and retained-helper
behavior. Both files ship through the same canonical installer inventory;
the component adds no installer, launcher or lifecycle owner.

Applications may opt in with a Python `remove` entry point in their existing
application declaration. Only their primary slot launcher routes `uninstall`
to `install.py` under isolated, already available managed base Python, with
`--no-project --no-sync --no-python-downloads`, before target-venv preflight.
Ordinary commands and applications without that declaration are unchanged.
`retain_application_removal_helper` copies only original sealed controls and
wheels into a new external workspace before importing that callback. It does
not copy the venv, install dependencies, retire a service or grant deletion
authority. Partial copies are never overwritten. The helper's readback validates
its original physical tree without requiring a surviving source slot.
The canonical entry then calls the application owner from retained wheels;
the application supplies its native-service and connector semantics. A retained
`packet/install.py uninstall --workspace <same-workspace> ...` supports original
settled retry after public commands and the slot are gone.

`prepare_application_removal` retains the original helper, runtime resources,
Source declaration and profile wheels outside the installation, together with
original physical cleanup and publication evidence. Preparation uses shared
runtime retention, not exclusive removal custody, so it can precede service
retirement. Its completion record is written last; existing/partial destinations
are never overwritten. `read_application_removal` checks the retained packet
without freshly inspecting a partially withdrawn or absent target. These APIs
still do not authorize a withdrawal or delete any installation object.

After application-owned service/startup/connector settlement,
`withdraw_application_publications` acquires canonical generation and command
guards and persists original physical intent outside the packet before unlinking
only those publications. Missing originals without that receipt, replaced
same-byte files and new owners refuse. A partial retry reuses the original intent
without fresh target preflight. `reclaim_application_removal` then uses the
original physical cleanup receipt through canonical `remove_owned_candidate`;
an optional application `validate` callback runs inside that runtime custody
immediately before finite cleanup. It can refuse unsettled independent references,
but does not replace the original cleanup receipt or grant deletion authority.
Publication guards must be released first. The caller retains service-start
exclusion across both phases. Neither helper replays native connector operations
or supplies a complete application-specific uninstall transaction.

New ordinary construction seals its original physical artifact inventory before
completion, after canonical retained-generation preparation. Completed slots
are never retroactively sealed. `runtime_application_artifacts.py` supplies a
finite cleanup callback for the installation owner: it validates that inventory,
persists the original object identities outside the candidate, and refuses changed
or unlisted objects on an interrupted retry. The callback must run under canonical
`remove_owned_candidate` custody, which still refuses selected or live runtimes.
It is not a standalone uninstall entrypoint. Service retirement, connector/startup
withdrawal and any application-owned runtime artifacts still need their own owner;
the callback does not remove them, retained connector assets or user state.
The application owner may explicitly identify a finite set of pending transient
files while preparing the receipt. Their original identities are recorded in the
packet, but they are never adopted into construction cleanup authority. The
callback refuses until their actual owner has settled them; unknown additions,
original construction objects and unsafe paths cannot use this exception.

The optional [protected installation-owner entry](protected-installation.md)
composes the existing prepared-application constructor through an independently
installed Linux root-owned bootstrap and policy. It adds no elevation,
acquisition, ordinary-caller migration or native qualification by itself.

`tools/materialize_installer_engine.py` uses `installer_engine_ref.py`'s existing
shell, Python and standalone-native consumer registries. It replaces stale or
absent derived slots only in existing declared consumers. Candidate content must
match either canonical bytes or its source-tree preimage; foreign replacements,
links, shared inodes and nonordinary paths are refused before copying.
Native canonical-reference conversion retains its existing source checks.
Standalone native SDK copies also refresh the shared settings and retained-input
support family through the same canonical/source-preimage checks.
One declared native file inventory drives materialization, maintenance sync and
materialized verification.
The separate invocation owner generates admission-toolkit copies.
Already-local native references receive the same verified writer refresh.
The normal and transitive library copy owners also pass the actual canonical
consumer and copied destination to this verifier. A packaged Update Driver
therefore receives current Installer assets before its caller can consume it;
its reviewed library source, not a missing developer mirror, supplies the
admitted preimage. This works with shared or separate source/candidate roots.

Native human-output declarations bind the original entrypoint and initializer
preimage, not equality with today's canonical initializer. An absent initializer
retains its declared digest; an existing one must match that digest. Promotion
refreshes these declared helpers, including standalone installers, through the
same owned-preimage checks, then rebinds the declaration after candidate rewrites.
Malformed declarations and foreign candidate replacements still refuse copying.

The single-consumer wrapper used by source preparation and previews invokes the
same verified refresh. Canonical references refresh missing or stale support
assets after reference validation and before native conversion; foreign assets
refuse conversion. Already-local writers and standalone Python consumers are
covered after conversion succeeds. The whole-tree composer keeps its existing
single refresh pass.

Validation phases are explicit:

- `sync-installer-engine.verify(phase="development")` checks canonical producers,
  declarations and ordinary owned layouts. Stale or absent derived bytes are not
  packaged qualification. The development install-contract check uses this phase.
- The default `verify()` / `phase="materialized"` retains exact-copy checks.
  Publication must validate and test the actual composed candidate, not infer
  packaged behavior from a successful development-layout check.

The reached composition owner also verifies every file in its derived-file plan
against admitted canonical bytes, checks ordinary single-link identity before and
after reading, and refuses changed source or candidate identity. A refusal is a
`SKIP` result, which the promotion composer rejects before freezing its tree.
The standalone exact-copy API is not itself evidence that publication called it.

Canonical bytes and stable file identity are snapshotted once per unique source
for one composition call, reverified before any writes and again after all
destination checks. Every destination retains its own preimage, ordinary-path,
single-link and postwrite byte/identity checks. The snapshot is call-local, not a
cache across candidates. A source change during copying refuses composition even
when destinations contain the originally admitted bytes.

The legacy sync CLI remains a maintenance tool, not a contributor publication
step. Raw developer copies can still be stale; do not make a failing copied
consumer pass by substituting a canonical dependency. Qualify its real composed
payload instead. Materialization does not install or update a running runtime.

`bootstrap-uv.ps1` supplies managed pre-runtime selection. Invocation's
`bootstrapLauncher: "uv"` packages this provider and the native Windows capture
owner for agent command ingress. The `bootstrap-python.ps1` evaluator consumes
that generated selector when adoption is declared; unmigrated consumers retain
their discovery-only behavior. `Resolve-UvBootstrapPython` reads the exact
Invocation-produced `bootstrap-python-version.txt` request (one LF or CRLF
terminator) without Python or TOML, qualifies the first PATH `uv` application
(0.8.0+), and asks uv for that managed-only version without project discovery or
downloads. Missing uv or a
not-yet-prepared interpreter is an explicit unavailable result. `Prepare` is a
separate, explicit setup operation. On Windows it first runs the same
acquisition-free lookup and reuses an already-prepared exact patch; only a
missing interpreter runs `uv python install --no-bin --no-registry` and a second
lookup. The exact `cpython@<major>.<minor>.<patch>` request resolves uv's
physical patch home and never traverses the minor-version junction, which
RedirectionGuard can refuse (`os error 448`) in SSH/network logon sessions.
Any 448 response still fails closed and names the remediation: prepare the
interpreter from an interactive logon session. Budget Guidance's
explicit installer is the first production caller. It caches the selection
for that installer process before entering the existing construction owner.
The POSIX `bootstrap-uv.sh` implements the same request and Inspect/Prepare
boundary in a subshell without requiring Python, including selection-hint
isolation; it still installs before its lookup. Both providers are declared assets of that consumer; raw development
mirrors are not packaged qualification.
Isolated selection suppresses UV configuration discovery and clears an explicit
`UV_CONFIG_FILE` in the child, so relative or malformed project configuration
cannot veto the prepared interpreter lookup. The caller retains its original
configuration for subsequent dependency-policy reads and construction; the
shared construction environment helper is unchanged.

`Resolve-UvRuntimePython` is the native counterpart of POSIX
`resolve_uv_runtime_python`: it asks UV to select an already-addressed completed
application environment, not the bootstrap version or a new interpreter.
The expected Python must already exist at a fully qualified path. Selection
uses `python find --no-project --managed-python --no-python-downloads` with
that path and refuses a different result. Missing UV is explicitly unavailable;
invalid paths, failed capture, policy refusal and mismatched selection throw
before worker admission. There is no acquisition, environment construction,
PATH-Python fallback or retry. Bootstrap and exact-runtime selection share the
confirmed-result and child-only environment adapter. Each caller still supplies
the capture callback and owns its deadline, cleanup and actual worker launch.
This provider addition does not migrate service callers or change their
current/last-known-good selection, activation or retirement policy.

The POSIX installer instance-reconciliation adapter is a prepared-only managed
consumer of that selector, with its own exact version request. Selection and
the existing scoped, nonterminating cleanup share a five-second native hard
deadline that kills the owned group, including TERM-resistant descendants.
Unavailable prerequisites defer optional cleanup without changing install
success. The caller's interpreter binding and environment remain unchanged.
The native interpreter/source custody checks still apply; managed selection
does not authorize a group-writable interpreter or an unsafe lifecycle archive.

The native resilient pip-policy wrappers likewise require the constructor's
explicit absolute interpreter, supplied as `--python <path>` or
`--python=<path>`. Missing, invalid or relative bindings refuse before launching
the policy or uv; they never discover another Python on PATH. Dependency
seasoning, original argv, retry and before-attempt callbacks are unchanged.

Bridge retains its specialized Windows resolver for declared bootstrap helpers.
Shared command-bootstrap adoption does not replace that resolver or claim that
every standalone service or installer child has migrated.

The required `InvokeCaptured` callback accepts `FilePath` and `ArgList` and
returns exactly one object with integer `ExitCode` and string `Stdout` and
`Stderr`. The caller must supply its existing bounded, contained process owner;
the provider introduces no launcher, Job escape or timeout implementation.
The additive `IsolateSelection` option requires that callback to also declare
and apply `Environment`: null-valued entries remove inherited child variables.
It isolates Python selection and active-environment hints for each uv child,
including both uv working-directory overrides, without changing the original
caller or its configured uv storage, mirrors,
cache and download policy. Older callbacks remain supported without the option;
requesting isolation with one refuses before any child executes.
`Get-UvBootstrapEnvironment` and the POSIX
`invoke_uv_bootstrap_environment` expose that same child-only boundary to
environment construction and the existing dependency-policy helper. Preparing
the interpreter alone does not sanitize a later `uv venv` or package step.
The existing Versioned Runtime `Invoke-Hidden` implements these child-only
null/removal semantics for bounded and ordinary capture.
Process-start/custody failures propagate. OS-policy refusal never selects
another uv or system Python. Returned Python is a selection, not executable,
profile, application or removal admission: the existing owner still validates
the helper and any required outside-removable-root closure before execution.
Explicit interpreter handoffs, guest policy-before-setup, uv prerequisite
publication and service/command launcher adoption retain their existing owners.
After lookup, pass the selected absolute Python to the existing lifetime owner.
Do not insert `uv run` between a shell and its caller-owned worker: that changes
the worker's original-parent relationship and is not the same custody protocol.
For deadline-bound uv inspection, `Invoke-UvBootstrapCapturedProcess` uses an
explicitly setup-selected stdlib interpreter and the co-shipped process-helper
capsule before uv starts. Even installed-only, cold-cache `uv python find` can
spawn an interpreter query, so a direct-process timeout is not tree custody.
The helper reuses the process owner's strict Job and bounded stream capture,
with settlement and relay inside the original one-second cleanup reserve.
It neither discovers ambient Python nor acquires a missing prerequisite.
Its isolated Python helper emits UTF-8, and the shared native capture requests
matching per-process stream decoders without changing the caller's console.
The same `bootstrap_uv_capture.py` owner supports a relative `--timeout` or an
absolute `--deadline`, with a cleanup reserve inside that allowance. POSIX
`--input-stdin` captures at most 65536 raw bytes before creating any child;
its input deadline is charged to that same budget. Binary forwarding preserves
the payload, and the existing process owner contains and settles the complete
selection/worker operation. Existing Windows empty-input calls keep their
absolute deadline and UTF-8 capture behavior.
Budget's POSIX installer also uses that selected interpreter for the canonical
`installer_pip_index.py` helper, never a PATH `pip` or `pip3`. Its
`scripts/pip-index-config` file pointer lets already-installed source preparers
include the helper without knowing a new asset-registry entry. The clean-room
copier likewise expands pointers before stamping a standalone snapshot.
The checkout reference form resolves its owning canonical reader and policy.
The canonical data reader supports Windows global, legacy-user, roaming-user
and site `pip.ini` files as well as POSIX `pip.conf`. It preserves pip's file
precedence, explicit-file suppression of user settings and environment
overrides. Missing Windows folder environment variables use the native
ApplicationData/CommonApplicationData folders; resolution failures refuse
instead of inventing a configuration directory. Vault's PowerShell installer
uses the same reader through its construction capture owner, so provider
location and effective UV context agree without executing ambient pip/Python.
Its `--json` output uses ASCII escapes so Windows PowerShell 5.1's native
pipe decoder cannot corrupt a non-ASCII index; existing text callers are unchanged.
Budget's first-session stamp remains Python- and uv-free. The installer consumes
`installer-source-assets.tsv`, inert copy/request data projected by
`tools/sync-installer-engine.py --only-source-snapshot-plans` from the existing
SDK inventory, reader alias and Invocation bootstrap-request producer.
Library entries use the source composer's existing artifact exclusions and
enumerate ordinary files rather than recursively copying a developer's library
directory. Stamping refuses a link at any declared source/destination path
component, so later-added caches are not copied and checkout links cannot
become dangling snapshot dependencies.
Validated files are copied in directory batches, not one process per file.
Ignored library lockfiles are excluded by the materializer's shared filter;
failed stamping removes its newly created temporary snapshot before releasing
the install lock, without touching earlier snapshots.
This embeds the reader, policy family and deferred interpreter request before
publishing the snapshot; explicit provision can then run after the checkout is
removed. Source and materialized verification check that the plan still matches
its producers. Prepared payloads do not search another installation for helpers.
Existing uv environment/configuration defaults win, using the dependency-policy
owner's discovery and TOML reader. Higher-priority named indexes shadow lower
defaults; distinct unshadowed defaults remain effective. Only otherwise does the `global.index-url`
bridge use `PIP_INDEX_URL` or pip's global/user/site/explicit-file order.
The reader and construction share effective working-directory selection:
`UV_WORKING_DIR` applies before resolving relative `UV_PROJECT` and
`UV_CONFIG_FILE` inputs. Construction's explicit `--directory` remains
authoritative over the environment.
The shared capture owner honors `UV_NO_SYSTEM_CONFIG` for system discovery
and retention without suppressing user, project or explicit configuration.
A true value requires the actual selected provider to report UV 0.11.16 or
newer before discovery or retention; older supported UV versions otherwise
ignore this option, so that combination refuses rather than changing feeds.
The index-reader caller supplies that same provider through `--uv`.
Qualification resolves the provider in its actual launch directory and captured
environment, retaining that absolute executable when source retention changes
the working directory. Provider-version failures remain actionable without
printing configuration or captured subprocess output.
Windows wrapper lookup normalizes enclosing PATH-entry quotes, while POSIX
entries remain literal. Qualification shares the resolver's short-directory
fallback for overlong Windows working directories. Configuration-disable and
isolation flags accept the same boolish true spellings as uv.
Explicit configuration files take precedence over discovery-disable flags.
POSIX input paths preserve symlink/parent traversal, and pip's null-device
configuration switch matches its literal spelling rather than normalized aliases.
The installer passes a valid active venv/Conda site prefix as configuration data
before isolating interpreter hints; it never executes that environment's Python.
Discovery runs only for package construction, not completed-slot reuse.
The [pip configuration rules](https://pip.pypa.io/en/stable/topics/configuration/)
include macOS Application Support and XDG data locations, empty-XDG defaults,
per-file inherited defaults, explicit-file user
suppression and null-file disabling. Malformed or unreadable configuration
refuses the bridge without printing feed values. It does not install pip,
mutate configuration or acquire another Python. Legacy option aliases are
normalized after parsing, so the last spelling in a section wins. A nonempty
whitespace-only index environment value refuses instead of selecting another feed.
The native installation-context transport controls exercise the actual
uv-selection-to-direct-worker handoff, including a large literal request,
nonzero exit, poisoned selection hints and an unavailable managed interpreter.
They do not enable any production command or catalog.

## Windows pre-interpreter capture

`bootstrap-capture.ps1` and its ahead-of-time `bootstrap-capture.exe` are the
finite Windows selector boundary. The canonical C# source is
`bootstrap-capture.cs`; Bridge declares both runtime assets in its existing
native support inventory. The normal materializer carries them together.
This is not part of the pure-Python process-owner wheel or a new service.

The host verifies the kernel's private-input-pipe server PID and the original
caller's retained process birth. It transfers a non-inheritable strict
kill-on-close Job handle into that same caller, joins the Job itself, and
closes its local Job handle before publishing the offer. Only the original
PowerShell process retains continuing ownership. No selector runs before the
correlated adoption acknowledgement; an explicit unadopted abort is the only
remote-handle reclamation authority. Unknown adoption never reclaims a numeric
handle that the caller might have disposed or reused.

Both uv lookup and prepared-interpreter probes execute within this custody.
Capture drains both output pipes concurrently, retains literal argument and
child-environment semantics, and reserves cleanup inside the caller's monotonic
deadline. The original caller uses the same prebuilt assembly's finite
retirement entrypoint to terminate the exact retained Job and confirm zero
active processes before releasing its handle. It loads the assembly from bytes
and caches the method by content, avoiding a file lock or repeated assembly
loads while allowing payload replacement in a continuing caller.
Unconfirmed retirement retains
the exact objects and fences further capture in that caller; it is not replayed.
The application SDK's original worker, private request pipe and admission
remain separate and unchanged.

Deployed callers never compile C# or require an SDK. Windows uses its existing
.NET Framework runtime for this small host; the facade works on Windows
PowerShell 5.1 and PowerShell 7. A missing artifact or unsupported execution
policy refuses rather than compiling, weakening custody or selecting another
interpreter.

Source builds use an already installed .NET 8+ SDK and existing Windows
framework references, without package restore or toolchain installation:

```powershell
python .\tools\build-bootstrap-capture.py
python .\tools\build-bootstrap-capture.py --check
```

The build is deterministic, path-mapped and warning-free. The actual native
Bridge controls exercise unhinted runtime/uv selection, original-caller death
and timeout descendant retirement on both PowerShell engines. Repeated capture
also verifies payload replacement and removal while the caller remains alive.
A bare-capture
negative control keeps the enclosing fixture Job open and confirms descendants
would otherwise remain alive. These controls do not claim a deployment or
desktop-observation result.

The retained-source constructor checks build-tool containment using resolved
physical paths and the shared Windows extended-path normalization. Equivalent
native spellings retain their original I/O paths; actual outside, sibling and
relative tool roots remain refused. `tools/test_installer_build_tools_root.py`
covers this boundary, alongside the real source-construction packets.

The separate composed Vault consumer regression builds a private payload through
the production materializer, then executes its copied remover and command
recognizer without candidate-side canonical libraries. It preserves the original
foreign-comment refusal assertions and checks exact owned-template recognition.
This does not rerun or relabel the historical raw-Dev copied-consumer failure.

Prepared applications may select their command publication directory with
`install.py --global-bin <absolute-directory> -- <application-arguments>`.
The Source adapter exposes the same optional `global_bin` keyword and
`--install-application --global-bin` CLI option. Absence preserves the ordinary
per-user destination. This selects command publication, not runtime ownership,
privilege, service behavior or a persistent update policy.

Both entrypoints retain the existing publication/target leases, all declared
commands, current-publication checks, rollback and unknown-startup custody.
Explicit selections require ordinary directory ancestry and cannot accompany
`--stage` or overlap the addressed runtime's `versions` subtree: those paths
contain current and retained slot-local launchers, not global command targets.
Manager retains its existing owned command directory. Callback
arguments remain after `--` and are not installer options.

The additive `global-bin/v1` capability is a literal
`APPLICATION_INSTALL_CAPABILITIES` declaration in the slot's actual `install.py`
and in the Source adapter; the strict base install-contract dictionary is
unchanged. Before construction, a selecting Source adapter reads the admitted
application helper's bounded metadata without executing it, then checks the
actual candidate helper before dispatch or reuse. Older helpers remain usable
with absent selection. Required unsupported selection refuses without trying
an effectful invocation or falling back to default publication. No existing
form is retired; source support alone does not qualify installed adoption.

Closed-wheel application health resolves first-party `.data/data`,
`.data/purelib` and `.data/platlib` members through the installed interpreter's
scheme instead of treating archive paths as site-package paths. Every relocated
file still requires the retained wheel's size and content hash and must resolve
inside the candidate slot. Other relocation schemes remain unsupported; script
rewriting and header installation are not qualified by this check.
`tools/test_runtime_application_binstubs.py -k relocated_wheel_data` exercises
the generated installer with real UV, fresh/reused content checks and refusals
before completion for modified, missing or escaping data. These are isolated
application controls, not additions to the required actor-free unit packet.
