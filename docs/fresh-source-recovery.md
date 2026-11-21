# Fresh bootstrap source recovery

The canonical Windows entry is `worktree-manager/bootstrap.ps1`. Consumers
download the current upstream Main entry first, independently of the requested
installation commit and existing Manager transport configuration. Before invoking
discovery, require this exact passive line once:

```text
# copilot-extensions:recovery-source-contract=1
```

`--describe-recovery-source` is pure JSON capability discovery. Never probe an
older bootstrap with this flag: older entries can interpret unknown arguments
as application commands.

`--recover-source <absolute-request-json-path>` accepts this schema-1 request:

```json
{
  "schema": "copilot-extensions.recovery-request",
  "version": 1,
  "operation": "preview",
  "selection": {"mode": "main", "commit": null, "checkout": null},
  "manager_root": null,
  "project": null
}
```

Use `install` for the native installation journey. Selection modes are `main`,
`commit` (a full lowercase Git object ID), and `configured-main-pin` (an existing
exact `[runtime_source]` policy). Only `commit` accepts a non-null commit.
`checkout` and `manager_root`, when supplied, must be absolute local paths.
The checkout supplies Git objects, not writable source files or authoritative
Main refs. Native acquisition fetches Main ancestry independently and retains
one commit/tree. Main selection refuses to silently override a configured
non-Main policy. No selection rewrites that policy.

The entry requires existing Git, PATH uv 0.8.0+ and an already prepared
uv-managed Python 3.11. It reuses the normal bootstrap's qualified uv selector
in inspection mode, with project discovery and interpreter downloads disabled.
It does not discover PATH/registry Python, acquire uv or install Python.
Missing prerequisites refuse before toolkit staging or cloning, with an
instruction to run explicit Manager setup. Normal setup retains its existing
preparation behavior; neither route assumes a uv interpreter-storage location.

Recovery fetches a fresh upstream Main toolkit and never invokes an installed
dispatcher on acquisition failure. Preview may clone and export disposable
source data; it does not build
or publish a runtime, install prerequisites, change registrations, or select,
start or stop a generation. Repair decisions remain with the authorized
native apply path. Preview obtains read-only footprint inventory
through the fresh native owner when its ordinary tool dependencies are available;
otherwise it reports inspection as unavailable, never as healthy or repaired.

The bootstrap binds the toolkit's canonical `libs/agent-procutil` before loading
application dependency closures. Published Main materializes vendored copies
inside applications; those copies do not replace the repository process owner
used by source preparation. The same selection runs in the fresh isolated
execution child, without relying on a warmed parent module cache or `PYTHONPATH`.
An already-loaded different utility copy is refused rather than reloaded.
Application build inputs, utility-origin checks and native participant custody
remain unchanged.

For apply, the toolkit first prepares the ordinary external dependencies declared
by its Manager, Worktrees and local-library source closure into the request's
private `tool-dependencies` directory. This uses the existing UV dependency owner
and seasoning policy, before native installation admission. A dependency-only
input preserves Manager's approved bundled artifact mapping without installing
the first-party source projects. Host UV discovery excludes the toolkit and
current directory, not ordinary tool directories beneath the caller's home.
The isolated child uses that directory after its source and standard-library
paths; it does not
rely on a warmed parent, execute site startup hooks or install into an existing
runtime. These are the fresh controller's tools, not a change to the selected
application's dependency profile. Preview does not publish application runtimes
or activate services and may report footprint observation unavailable.

An exception escaping native execution custody still leaves cleanup unconfirmed.
The parent retains a matching child outcome as diagnostic evidence alongside the
native interrupted state and null native exit; it does not convert that receipt
into cleanup, completion or replay authority.

## Native composition

The fresh toolkit composes the existing immutable acquisition, local source
promotion, wheel/profile preparation, Manager installation participant, engine
repair, inventory and suite execution owners. It is not a dependency planner,
installer or supervisor. Manager installation alone is not suite completion.
Apply borrows one request-local foreground operation from the native execution
driver. An occupied or fenced queue refuses admission; recovery neither drains
another request nor reconciles a foreign foundation operation. The same live
custody covers Manager construction, engine repair and suite execution.
Every fenced foundation record, including an owner-lost `running` record,
refuses request-local admission without alteration. An older supported queue
schema is migrated by the native owner only after custody and idle/unfenced
checks, before accepting or executing the new operation.
Captured engine attribution uses the existing repair owner. Missing or
unrecognized historical attribution instead selects the native install or
independent-repair entry under its registered source; it is not retried as a
legacy flow after an advertised prepared-input failure.
The inventory and suite control code come from the fresh toolkit, so an older
selected engine dispatcher cannot discard the new input protocol. Installed
application code still comes from the one selected commit.
Payload acquisition and the runtime portfolio use the native isolated phases.
Recovery does not run repository anchor synchronization, pivot pruning, or an
integration-time Manager recovery that could replace the explicit selection.
The selected source's `source-install.json` declares its prepared-input
capability. Currently Manager and Worktrees supply it; undeclared packages keep
their native legacy source flow unless they already expose the released native
`--install-source`/`install_source` protocol. That existing protocol is inspected
without running an unknown discovery flag, so released prepared-input adapters
do not require a declaration retroactively. A later package opts in through the same
declaration, without changing external consumers or a package/version matrix.
Unknown declarations fail rather than falling back.

The native suite's V1 prepared-source packet remains supported. V2 additionally
carries per-target `install_source` and the complete native selection returned
by `install_source.native_selection`. The installed execution owner revalidates
these inputs and uses the constructor's profile generation and expected
construction hash, not the raw source base version. An independent package
source-target policy cannot be bypassed by attaching wheel inputs.
Windows application launchers also carry the profile's explicit Python platform
to UV, so a supported emulated interpreter does not inherit the host UV binary's
different architecture when checking the closed lock.

Older selected sources must support native transaction pinning and the bounded
Manager installation participant. Unsupported historical sources fail
explicitly; they are not replaced with a newer source. A selected older engine
without V2 support can still execute an entirely legacy V1 portfolio.

Apply retains its exact inputs under the selected Manager root's
`runtime-sources/recovery/` attempt directory, including on failure. The final
`copilot-extensions.recovery-result` JSON reports the immutable source, actual
per-target flows, native stage exits, and retained input path. Native output is
forwarded separately; a partial result is not rollback or cleanup evidence.
No teardown, queue recovery, policy-pin activation or foreign-resource cleanup
is authorized by this request. Native ownership, execution custody and
publication gates still apply.
The downloaded toolkit is retained for install operations too; its checkout is
reported separately from the target source. This preserves code and evidence
needed by native custody after an uncertain or interrupted outcome.

## Repository-declaration integration contract

The entry also advertises `request_versions: [1, 2]` and
`repository-preview` through `--describe-recovery-source`. After discovering
those capabilities, callers may use this distinct schema-2 request:

```json
{
  "schema": "copilot-extensions.recovery-request",
  "version": 2,
  "operation": "preview",
  "selection": {"mode": "repository", "commit": null, "checkout": null},
  "repository_root": null,
  "manager_root": null,
  "project": null
}
```

`repository_root` is an absolute addressed adopter checkout, or null when it
is unresolved. Schema 2 currently supports **preview only**. Installation,
ordinary-update association, content-source convergence, general branch/ref
expressions and qualified history remain integration work under
[#7028](https://github.com/ThomasMichon/copilot-extensions/issues/7028) and the
[Installer vision](../visions/installer/README.md#repository-selected-suite-source).
An install request refuses before construction or activation; a preview is
not a production pin or native installation qualification. Schema 1 retains
its existing strict shape and installation behavior.

The read-only declaration schema is:

```yaml
current_version: refs/tags/v1.2.3
repository: https://example.com/suite.git
last_known_good:
  - refs/tags/v1.2.2
```

All three fields are optional. `current_version` and `repository` are nonempty
strings without surrounding whitespace or control characters.
`last_known_good`, when present, is a list of nonempty reference strings;
the reader preserves it without qualifying or advancing it. Unrelated YAML
configuration is accepted and left untouched. Duplicate keys, malformed YAML,
merge keys, non-mapping documents (including explicit null), nonordinary paths
and files over 64 KiB refuse. Empty or comment-only input is an absent declaration.

Native preview currently accepts `main`, `refs/heads/main`, full lowercase
commit IDs and `refs/tags/<tag>`. Existing native source validation, credential-free
repository admission and fetched-Main ancestry checks still apply. Other
symbolic expressions refuse explicitly; they are not guessed to be tags,
mapped to Dev or retried as Main. A repository override alone explicitly
selects its Main. With neither selection field, or with an unavailable
repository, preview reports the Main-foundation fallback.
The declaration's repository override must be a remote Git locator. It cannot
select local repository paths, UNC-style paths or `file:` URLs. This prevents
passive data from sending native Git into an untrusted local repository's
configuration includes, alternates or storage links. An available local
**adopter** still works without registration; this restriction concerns the
suite source selected by its data, not the addressed adopter.
Schema 1 and an explicit caller-owned `selection.checkout` retain their existing
local-object source contract. Host Git configuration and those explicit inputs
remain caller authority, not declaration authority. An object-source checkout
supplies objects only: selected tags are fetched from the declared repository,
not from potentially different or missing local tags.
Declaration path components are checked root-first with
non-following metadata before existence probes or native normalization.
Parent components are invalid; symlink/reparse components refuse before their
targets are accessed.

When a declaration exists, the physical `-I -S` toolkit prepares the Manager
manifest's existing PyYAML requirements into the request workspace through
the retained UV dependency owner, including its feed and seasoning policy.
This path needs an existing UV executable on an ordinary absolute host PATH entry
outside the addressed adopter. Current/adopter directories are excluded by
filesystem identity after non-following component validation, rather than lexical
spelling. Empty/relative/parent entries, implicit current-directory lookup,
linked components and network path entries do not select a tool. The exact
`uv.exe`/`uv` candidate is checked with non-following metadata before file/access
probes; implicit executable discovery is not used. It never installs into the base
interpreter or uses installed startup hooks. The parser runs in a separate
physical `-I -S` child restricted to that prepared parser root. Missing UV,
parser preparation failure or invalid policy is a failure, not an absent pin.
Absent repository/declaration paths need no parser preparation.

The result's `repository_policy` records the addressed root, declaration path,
source repository, requested reference, effective reference and fallback reason.
Only admitted selections attach that document. Rejected declaration locators use
value-free diagnostics; malformed YAML and duplicate-key diagnostics report
available positions rather than source snippets or key values. Scalar
constructor failures receive the same value-free invalid-YAML result.
The existing `selection` records the one materialized immutable commit/tree.
A conflicting independent runtime-source policy refuses; a matching one is
preserved. Preview never creates an installation association or changes Manager
self-source, service-runtime policy or Copilot content delivery.

The remaining sections describe the complete integration contract, not claims
that these later installation/update consumers are implemented.

### Input and cold interpretation

An explicit repository-suite operation addresses the consuming repository
separately from both the upstream suite repository and a Git object-source
checkout. Resolve an available local repository first through the existing
repository owner. An unregistered absolute local checkout must also work on a
first installation; a working registry, old Manager or plugin runtime is not
a prerequisite. Do not select policy from ambient cwd.

Read the addressed repository's `.copilot-extensions/config.yaml` as data.
Its `current_version` expresses the desired commit or symbolic Git selection;
an optional source-repository override identifies where that selection belongs.
The implementation must publish the precise field schema and validation before
consumers author it. Preserve unrelated existing configuration and do not write
defaults, a pin, history or machine-local source policy merely to inspect it.

The trusted current toolkit owns parsing before target-suite acquisition.
It must neither import nor execute adopter setup or code from the requested
suite to learn the selection. Any required YAML/parser dependency is prepared
through the existing trusted bootstrap dependency owner, not inferred from an
installed application's environment or a second ad hoc package installer.
Missing parser support is an explicit unavailable interpretation, not proof that
the repository has no declaration. Duplicate, malformed or unsupported policy
must not be reinterpreted as an absent pin.

### Selection, fallback and native handoff

Retain the consuming repository association, declaration authority, requested
source/ref, immutable resolved commit/tree and any explicit fallback reason.
Resolve symbolic input once for the operation. Changing the ref during
preparation cannot mix commits across Manager, engine, applications or content.
Explicit supported source overrides keep their existing precedence and
admission; this contract does not permit arbitrary refs to bypass native
source, integrity or authorization checks.

When the consuming repository is not yet available or has no selection,
canonical current Main can establish the public foundation before private
repository access. Report that fallback without claiming the unresolved
repository's pin was installed or qualified. A known invalid declaration,
denied source or failure after admitting a source is not an availability
fallback. Do not catch every acquisition failure and retry Main.

Feed the resulting fixed selection into the existing recovery
request/selection boundary before `runtime_source.acquire()`. Reuse native
materialization, complete prepared-application production and installation
execution; do not create a second producer, updater or supervisor. Manager
self-source, service-runtime source and Copilot content remain separate
domains: report which were addressed and actually consumed the selection.
Preserve independent policy unless its explicit source-change operation
authorizes replacement.

Later normal update must use the installation's explicitly associated
repository selection, including an older desired suite, rather than treating
an arbitrary calling checkout as authority to retarget shared runtimes.
Establish and preserve that association through the existing installation
owner, not from discovery or a successful configuration read.

Bounded retained-good history is a separate qualification/write concern.
A reader does not invent a retention limit, advance history, label an
unexercised candidate good or enable automatic rollback. Historical
qualification retains immutable source and consuming-repository association.

### Association adapter receiving contract

The receiving design was reviewed in
[#12987](https://github.com/ThomasMichon/copilot-extensions/pull/12987), under the
[accepted association/declaration split](https://github.com/ThomasMichon/copilot-extensions/issues/7028#issuecomment-6890789048).
Manager's separate `worktree_manager.repository_suite` module now supplies the
bounded association operations below. The
[delivery and validation plan](../efforts/active/source-managed-runtime-installs/repository-suite-association.md)
retains its completion gates. Schema 1, schema-2 preview, bootstrap discovery
and all existing installation behavior remain unchanged by this adapter.

The existing Manager configuration owner retains one consuming-repository
association per explicitly addressed configuration root. Store it in that
root's existing `config.toml`, outside application version slots:

```toml
[repository_suite]
version = 1
repository_root = '<absolute-adopter-checkout>'
```

The table has exactly those two fields. `version` is integer 1, not a boolean;
`repository_root` is a fully qualified filesystem path without parent
components or control characters. An absent table is unassociated; an empty,
malformed or unsupported table is not absence. The table stores the desired
repository association, not a qualified source version, successful installation,
last-known-good history or permission to change that repository.

The adapter belongs to the existing Manager package. Its module/API
requires an explicit Manager configuration root; it does not default to cwd,
an adopted project, a registry entry or another installation. Operations:

| Operation | Boundary |
|---|---|
| Inspect | Read one bounded configuration and report the stored association or its absence; no writes, adopter-policy read, Git, setup or service work |
| Associate | Propose an existing ordinary adopter root; change the table only with explicit apply |
| Clear | Propose removal of that table only; change it only with explicit apply |

Use the existing installation/tooling owner to select the Manager interpreter
and package closure. Do not select bare PATH Python or infer a Python-storage
location. The module can then be invoked with this argv shape:

```text
<selected-manager-python> -I -B -X utf8 -m worktree_manager.repository_suite --describe
<selected-manager-python> -I -B -X utf8 -m worktree_manager.repository_suite --root <absolute-manager-config-root> inspect
<selected-manager-python> -I -B -X utf8 -m worktree_manager.repository_suite --root <absolute-manager-config-root> associate --repository-root <absolute-adopter-checkout>
```

`--describe` alone returns
`worktree-manager.repository-suite-association.capabilities`, version 1,
with explicit-root, read-only-default and apply-required support. Ordinary
results use `worktree-manager.repository-suite-association`, version 1. They
name the operation, configuration root, actual current repository root,
proposed repository root and whether configuration changed. They are data,
not installation or execution receipts.

With `--root` and no operation, inspection is the default. Add `--apply` only
to associate or clear when that configuration effect is authorized. Replacing
an existing different association adds `--expect-current-root <previous-root>`.
Clear uses `clear --expect-current-root <previous-root>` and is also read-only
without `--apply`. An already-matching desired state changes nothing.
Every parser requires the declared option spelling. Abbreviations such as
`--app` are rejected before association or clear can change configuration.

An old runtime ignoring the extra TOML table does not implement the adapter.
Missing module or unsupported schema is an explicit unsupported result, never
permission to install or invoke a guessed fallback.

Inspection accepts at most 64 KiB of configuration, rejects invalid TOML and
unknown table fields, and does not create even a missing configuration root or
lock. Reading the stored identity does not require the adopter still to exist.
The existing declaration reader retains ordinary-path, parser and availability
checks when a caller later consumes that root. Keep those data and source
admission steps separate rather than executing Git during association discovery.

Initial association requires absence or the same already-associated root.
Replacing a different root requires an explicit expected current root; clearing
also names the expected current root. Revalidate it under the existing shared
configuration writer lock. A stale expectation refuses without changing the
association. Equivalent native path spellings are not a different association.
Repeat requests that already match the desired state are observations, not
installation replay or new qualification.

Validate inputs before entering the writer. Explicit apply uses the existing
configuration path, lock and atomic-publication boundary and changes only this
table. Preserve unrelated configuration, including Manager self-source and
service-runtime policy. Do not copy their values into the association, rewrite
them to match adopter data, or use old runtime health as a prerequisite to this
explicit configuration operation. A noncanonical representation that cannot be
rewritten without changing unrelated data refuses with the original file
retained; no partial or lossy TOML writer is used as fallback.

The installation/update caller remains responsible for effect authority and
for selecting the addressed configuration root. A configuration read or bind
does not prove an installed generation. The caller passes the stored adopter
root to the existing declaration/parser owner, then resolves one immutable
selection through the existing native acquisition/composition owner. Preserve
explicit source-override precedence and independent policies. This contract
does not wire ordinary callers, enable repository installation, select Copilot
content or claim that any domain consumed the declaration.

### Associated declaration observation

The separate `worktree_manager.repository_suite_declaration` module connects
that explicit association to the existing repository-declaration reader. It
observes desired data only: it does not resolve a ref, acquire source, install
anything or change the association or independent source policies.

```text
<selected-manager-python> -I -B -X utf8 -m worktree_manager.repository_suite_declaration --describe
<selected-manager-python> -I -B -X utf8 -m worktree_manager.repository_suite_declaration --root <absolute-manager-config-root> inspect
```

`--describe` is effect-free and returns
`worktree-manager.repository-suite-declaration.capabilities`, version 1. It
advertises association schema `worktree-manager.repository-suite-association`,
versions `[1]`, the `inspect` operation, explicit-root/read-only support and
`resolves_source: false`. Missing module or incompatible capability is
unsupported, not permission to retry through an older application entry or
install a parser/runtime.

`read_declaration(root)` and the module return
`worktree-manager.repository-suite-declaration`, version 1, the addressed
`configuration_root` and the existing reader's `repository_policy`. That policy
retains the associated repository identity, declaration path, requested source
and reference, effective desired reference and explicit fallback reason.
It is not an immutable source, acquisition admission or installation receipt.
Symbolic intent can therefore be observed even when a native consumer does
not yet support resolving it. No qualified history is created or advanced.

An unassociated configuration reports the existing `repository-unavailable`
Main-foundation fallback without reading cwd or creating a root/lock. An
associated but unavailable adopter retains its stored identity and reports
that same explicit availability fallback. An available adopter without a
selection reports `selection-absent`. Invalid association or declaration data,
linked paths and missing trusted parser support fail, not fall back to Main.
The existing native source-locator validator also refuses credential-bearing
or invalid remote intent before it can enter the returned policy; diagnostics
do not echo the rejected locator.

The selected Manager/tooling dependency closure supplies the existing PyYAML
parser. This observer never prepares dependencies or changes Python import
paths. A cold source caller supplies its trusted source/parser closure through
the existing tooling owner; optional API `parser_root` retains the reader's
origin check. A configuration or YAML read grants no source-change authority.
Ordinary update/setup wiring, independent-policy reconciliation, immutable
selection and actual per-domain consumption remain separate caller gates.

### Compatibility and acceptance

Use passive bootstrap discovery before choosing the distinct supported request
revision. `repository-preview` does not advertise repository installation.
Keep schema 1 strict, with its existing Main/commit/configured-Main-pin behavior.
`project` remains an execution selector, and `selection.checkout` remains a
Git object source. Neither becomes a silent repository-policy override.

Required focused implementation controls:

| Case | Required result |
|---|---|
| Cold interpretation without installed packages or registry | Addressed local declaration is read without adopter code execution or host-policy writes |
| Missing repository or absent pin | Explicit Main-foundation fallback, no claimed pinned readiness |
| Invalid/duplicate policy or unavailable parser | Truthful refusal/unavailable state, not an absent-pin fallback |
| Symbolic selection moves during preparation | One requested/resolved identity throughout the native operation |
| Repository override, denied or invalid admitted source | Existing source admission and failure preserved; no silent Main retry |
| Supported explicit override and independent source policy | Existing precedence and protected domains preserved |
| Existing installation and older desired source | Explicit association and desired-source convergence without unrelated retargeting |
| Old/new request combinations and unsupported bootstrap | Existing schema 1 behavior preserved; capability-selected new requests or pre-effect refusal |

Use synthetic repositories and an isolated physical interpreter/process for
the cold control; an installed-reader unit test is insufficient. Real native
construction/activation, content delivery and protected-adopter acceptance
retain their own lanes and effect authority. A passing reader or source merge
does not complete those gates.
