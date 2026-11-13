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

The entry requires existing Git and physical Python 3.11+, fetches a fresh
upstream Main toolkit, and never invokes an installed dispatcher on acquisition
failure. Preview may clone and export disposable source data; it does not build
or publish a runtime, install prerequisites, change registrations, or select,
start or stop a generation. Repair decisions remain with the authorized
native apply path. Preview obtains read-only footprint inventory
through the fresh native owner when its ordinary tool dependencies are available;
otherwise it reports inspection as unavailable, never as healthy or repaired.

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
Repository declarations cannot select UNC/network filesystem paths or `file:`
URLs; ordinary absolute local repository paths remain supported. These passive
declarations do not authorize network-filesystem authentication. An object-source
checkout supplies objects only: selected tags are fetched from the declared
repository, not from potentially different or missing local tags.

When a declaration exists, the physical `-I -S` toolkit prepares the Manager
manifest's existing PyYAML requirements into the request workspace through
the retained UV dependency owner, including its feed and seasoning policy.
This path needs an existing UV executable; it never installs into the base
interpreter or uses installed startup hooks. The parser runs in a separate
physical `-I -S` child restricted to that prepared parser root. Missing UV,
parser preparation failure or invalid policy is a failure, not an absent pin.
Absent repository/declaration paths need no parser preparation.

The result's `repository_policy` records the addressed root, declaration path,
source repository, requested reference, effective reference and fallback reason.
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
