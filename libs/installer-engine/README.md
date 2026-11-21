# Installer engine source and derived payloads

Edit canonical implementation files here. Contributors do not refresh committed
developer copies to publish a change: the Dev-to-Main composer materializes the
declared installer family in its candidate tree before freezing that tree.

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

`bootstrap-uv.ps1` is a separately delivered provider for Worktrees' future
pre-lifecycle adoption, not a change to the discovery-only
`bootstrap-python.ps1` evaluator. `Resolve-UvBootstrapPython` reads the exact
Invocation-produced `bootstrap-python-version.txt` request (one LF or CRLF
terminator) without Python or TOML, qualifies the first PATH `uv` application
(0.8.0+), and asks uv for that managed-only version without project discovery or
downloads. Missing uv or a
not-yet-prepared interpreter is an explicit unavailable result. `Prepare` is a
separate, explicit setup operation: it requests `uv python install --no-bin
--no-registry` before the same acquisition-free lookup. Budget Guidance's
explicit installer is the first production caller. It caches the selection
for that installer process before entering the existing construction owner.
The POSIX `bootstrap-uv.sh` implements the same request and Inspect/Prepare
boundary in a subshell without requiring Python, including selection-hint
isolation. Both providers are declared assets of that consumer; raw development
mirrors are not packaged qualification.

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
Budget's POSIX installer also uses that selected interpreter for the canonical
`installer_pip_index.py` helper, never a PATH `pip` or `pip3`. Its
`scripts/pip-index-config` file pointer lets already-installed source preparers
include the helper without knowing a new asset-registry entry. The clean-room
copier likewise expands pointers before stamping a standalone snapshot.
The checkout reference form resolves its owning canonical reader and policy.
Its first-session stamp remains Python- and uv-free. The installer consumes
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
