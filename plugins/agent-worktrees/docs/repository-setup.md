# Explicit repository setup

The engine owns acquisition, registration, source qualification and explicit
repository setup. The standalone Manager calls this CLI across a process
boundary; it does not import the engine or clone repositories itself.
This implements the bounded shared seam from #356 and the optional caller
from #8545, not first-repository discovery or guided local/remote/skip UI.

```text
agent-worktrees repos setup <https-url> [--target <absolute-path>]
  [--commit <full-commit-id>] [--apply] [--trust-repository-setup] [--context <name>]
```

Without `--apply`, inspect an existing source or report an unavailable
declaration as uninspected. Do not clone, register or execute repository code.
Apply selects the explicit target or configured source root; a real terminal
may supply a missing source root for this invocation only. No default location
is invented or persisted.

## Acquisition and ownership

Version 1 accepts credential-free HTTPS URLs with no query or fragment.
Transport paths, including a `.git` suffix, are preserved; suffix removal
is only registry-name inference, not a generic remote-identity alias.
Local/SSH transports, foreign remotes, conflicting registrations, source changes,
symlink/reparse traversal and linked/bare checkouts are refused. Git URL rewrites
to a different remote are refused before acquisition. Existing managed
registrations must reconcile through their owner; this command does not convert
an anchor, steal a live worktree or change an editing/agent preference.
The selected registry name's ownership applies even without a local-platform
path; a managed entry on another platform is not a free reference slot.
New registrations are reference-class and no-agent; repository-owned setup may
perform its separately authorized adoption work, but selecting a URL does not
itself adopt a project.

Git uses its configured credential owner with inherited stdio and no fixed
authentication deadline. It does not receive a copied token or change global
authentication. Explicit account selection is currently qualified only for
GitHub.com with a qualified `manager`/`manager-core` GCM helper, the GitHub
provider and GCM account filtering. Foreign provider or legacy authority
overrides refuse before authentication. Call-scoped resets isolate that helper
and clear inherited HTTP headers for the selected URL without changing persisted Git configuration;
unsupported explicit-account routes refuse rather than fall back
to an ambient identity. Other credential-owner routes with no configured
explicit account retain Git's own authentication behavior.
The selected registry name's explicit account takes precedence over URL aliases.
Without that selection, conflicting alias accounts refuse rather than choosing
the first entry. Only an explicit owner account map may supply an owner default;
an unrelated sibling repository's account is not inferred as that default.
Account selection is checked again before registration.

Failed acquisition preserves a partial target and does not register it.
Successful acquisition/registration is retained if subsequent repository setup
fails. No reset, stash, clean, implicit refetch, hosted repository creation or
rollback of someone else's work is performed.

### Optional immutable commit selection

`--commit` accepts a full 40- or 64-hex commit object ID, not a branch, tag,
abbreviation or revision expression. Invalid input refuses before acquisition.
Without it, existing remote-HEAD behavior is unchanged.

An existing qualified checkout must already have exactly that HEAD. A mismatch
refuses without checkout, fetch, reset, registration or setup execution. For a
new target only, clone without checking out remote HEAD, resolve the requested
object as that exact commit, then check it out detached with hooks disabled.
Ordinary source qualification and account revalidation still precede registration.
If the full clone does not contain the commit (for example an unadvertised or
pruned object), refuse without an extra fetch; preserve the partial target and
require owner reconciliation before retry. The command never selects a different
commit to make setup succeed. Execution trust remains bound to the actual
qualified commit, declaration and setup entry.

Source qualification and every subsequent revalidation use unreplaced Git
objects, not the virtual history selected by `refs/replace`. The explicit
detached checkout does likewise. An existing replacement-tree checkout cannot
present different setup bytes as a clean approved commit; refusal preserves
its source and replacement refs rather than rewriting either.

The additive `repos-setup/1` CLI capability is `immutable-commit`.
The selected executable's strict parser gates the explicit capability request
before repository effects; an older receiver without `--commit` reports an
unsupported argument. Callers must retain that failure and may not retry without
the pin. The old no-pin form remains supported without a deprecation deadline.
No package-version comparison, newer-payload inference or prerequisite service
is needed for this opt-in, in-band requirement.

## Declaration and consent

Commit `.agent-worktrees/repository-setup.json`:

```json
{
  "version": 1,
  "entrypoints": {
    "windows": {"runtime": "powershell", "script": "setup-manager.ps1"},
    "posix": {"runtime": "bash", "script": "setup-manager.sh"}
  }
}
```

Declare only supported platforms. JSON is bounded UTF-8 (16 KiB, optional BOM);
duplicate/unknown fields, invalid types and unsupported versions/runtimes are
refused. Scripts must be regular tracked relative files within the qualified
clean checkout. Traversal and modified source are refused, including index
flags that hide modifications.

Before any entry executes, display canonical remote, acquired commit, checkout
and setup entry. `--trust-repository-setup` authorizes that selected revision
in this invocation; without it, a real terminal may explicitly consent.
Redirected callers never auto-consent. Declaration/script digests and source
revision are requalified before and after calls. A change requires fresh
qualification and consent.

This is an execution trust boundary, not a sandbox or an attestation against
same-user peers. The trusted repository owns its imports, effects and durable
reconciliation state. It must keep the selected source intact and keep personal
state, generated output and adoption work in their appropriate owner locations.

## Request, result and readiness

### Optional selected continuity context

Declaration version `2` retains the same entrypoints and additionally requires
bounded `contexts`, keyed by lowercase context names:

```json
{
  "version": 2,
  "entrypoints": {"posix": {"runtime": "bash", "script": "setup-manager.sh"}},
  "contexts": {
    "harness-a": {
      "provider": "local-projection-v1",
      "source": "~/.config/example/continuity.json"
    }
  }
}
```

`--context harness-a` selects that exact revision-bound declaration. No source
is guessed or opened by inspection/plan mode. Unknown names/providers refuse;
there is no repository-local reusable enrollment script or fallback to another
context. Ordinary repository setup must first be trusted and ready.
The engine then consumes the [Fleet continuity
contract](../../../fleet-manager/docs/continuity-bootstrap.md) through its own
selected Python runtime with isolation, not an ambient executable on `PATH`.
That selected engine environment needs the Fleet SDK and its service-Gateway
dependency supplied by its normal construction owner. Capability discovery,
bounded native process custody and check/apply preserve the original operation.
Retained machine state is namespaced by the canonical qualified harness repository
URL and context name, not the checkout path or commit. Two harnesses may reuse a
context name without sharing keys/operations; later revisions of the same harness
retain the original state and its strict source-binding checks.
Missing support, source access, authentication or an unknown result refuses the
optional stage without undoing repository/core readiness. Neither context
selection nor authenticated connection authorizes SSH or another lifecycle.

Without a selected context, legacy declaration and setup behavior are unchanged.
Older engines strictly refuse the new declaration/argument; upgraded callers
retain that outcome rather than retrying without selection. The repository
script's request/result protocol below remains version one: continuity is
engine/Fleet-owned orchestration, not a new responsibility hidden in that script.

### Explicit selected SSH setup

The engine's optional `fleet-ssh` extra declares its Fleet service-Gateway and
SSH-client SDK dependencies. Select it through the existing complete Source/profile
preparation (`local_install_profile.py --prepare-profile --component agent-worktrees --extra fleet-ssh`,
with the normal source, output and UV arguments), using the associated Fleet and
SSH first-party wheel inputs. This is a dependency selection, not a second installer
or permission to install packages into a running engine. The normal construction
owner must retain that selected extra and complete profile on installation/update.
The default engine dependencies are unchanged; selecting the SDK alone neither
enrolls a machine nor activates a role. A dependency declaration or prepared profile
does not replace the executable capability checks below.

The SDK's `[tool.copilot-extensions.application-sources]` declaration maps
`fleet-manager` to the canonical `standalone:fleet-manager` project. This is
an associated application input, not a UV vendored-library override. Prepare the
input snapshot with the existing repeated `local_install_source.py --project`
selections for `agent-worktrees`, `standalone:fleet-manager` and `agent-ssh`,
then select `--prepare-profile --component agent-worktrees --extra fleet-ssh` through the normal
profile command. Existing application builders retain Fleet's resources.
The profile reader checks the declaration against the actual associated wheel's
project owner before registry compilation. A missing or different selected Fleet
input refuses rather than resolving Fleet from a public registry. Without the
extra, Fleet is not required. Fleet similarly binds its selected SSH-client SDK
to the canonical `agent-ssh` application input. The dependency-age audit attributes
the same explicit association to its contained canonical source and still audits
external dependencies.
This path uses the already-merged profile APIs; it does not depend on unrelated
constructor changes or establish the complete installed Manager cohort.

Declaration version `3` retains version-two entrypoints and contexts, adding
one required `ssh_setup` field to each context. `null` retains outbound-only
behavior. An absolute/home-relative private request reference explicitly selects
the [Fleet-owned SSH setup contract](../../../fleet-manager/docs/continuity-bootstrap.md#normal-selected-ssh-setup).
Inspection validates the reference without opening it. Older strict readers
refuse version three; never retry as version two or drop the selected role.

```json
{
  "version": 3,
  "entrypoints": {"posix": {"runtime": "bash", "script": "setup-manager.sh"}},
  "contexts": {
    "harness-a": {
      "provider": "local-projection-v1",
      "source": "~/.config/example/continuity.json",
      "ssh_setup": "~/.config/example/ssh-setup.json"
    }
  }
}
```

The selected engine first requires the actual Fleet executable's
`continuity-ssh-setup-v1`, `continuity-ssh-setup-pin-v1` and
`continuity-machine-reference-v1` capabilities.
Public scope/configuration and the exact complete prepared Source/profile
are checked before bootstrap enrollment. The engine consumes its own packaged
`prepared-application-install` v1 implementation, not a configurable module,
ambient installer command, editable checkout or the client-only constructor.
The profile must contain both `gateway-host` and `ssh-client`.

After the original bootstrap owner establishes outbound access, Fleet prepares
the v3 reference configuration only against the inspected request digest.
A changed request is refused before preparation or writes at either its old
or newly selected installation root. The existing Source application installer
owns construction/activation. Actual selected host status, including authenticated
directory convergence and its current input fence, establishes readiness.
An unchanged ready participant avoids installation. Unknown launch state,
changed/foreign configuration, changed request/profile or unavailable support
refuses without another identity or an automatic credential fallback.

This path needs the selected engine's existing Fleet SDK and complete native
Source inputs, plus the installer's existing UV prerequisite. It does not acquire
a provider, prepare a missing source/profile, authorize a grant or migrate
operator configuration. Generated host configuration is created or reused,
never overwritten; a different configuration needs its owner's reconciliation.
The repository script request/result protocol and ordinary client-only setup
remain unchanged. Source qualification is not release, live enrollment or
fresh-adopter acceptance.

### Explicit owning Vault SSH key

Declaration version `4` adds a required `ssh_key` to each context. `null`
retains the version-three behavior. A selected object has exactly
`argv`, `configuration_root`, `entry`, `directory` and `fingerprint`.
`argv` is the owning Vault's fully launchable argument array, with an absolute
executable first; a bare `.ps1`, `.cmd` or `.bat` is not an executable.
Supply that payload's explicit native launcher and its arguments when needed,
never another same-named Vault on `PATH`. Arguments are bounded and passed
without command-string evaluation. The configuration root and dedicated key
directory are normalized absolute/home-relative references; the entry is the
original bounded Vault entry, and the fingerprint is canonical unpadded
`SHA256:<digest>`. A nonnull key selection requires a nonnull SSH setup request.
Inspection binds this declaration to the trusted source without accessing Vault.

The actual Fleet receiver must additionally support
`continuity-ssh-key-reference-v1` and `continuity-ssh-key-pin-v1`.
Its read-only `--key-references-only` inspection validates scope, policy, paths,
host state and Source selection before key files exist. The returned version-two
description includes only `key_files.identity_file` and `key_files.public_key_file`,
which must exactly match the selected directory's `id_ed25519` and
`id_ed25519.pub`. This option cannot be used with `--apply`.

For a participant needing enrollment or SSH installation, explicit apply queries
the selected Vault's `agent-vault.invocation` version-one contract with
`--configuration-root`. Both `configuration-root-v1` and
`ssh-key-install-pin-v1` are required before credential access. The adapter then
uses `ssh-key prepare --require-running` followed by
`ssh-key install --require-running --expected-fingerprint` for that exact entry
and directory. It accepts only reuse of the pinned public identity. Actual
Fleet key inspection carries the same fingerprint and request digest before
new enrollment and through configuration preparation/construction checks.
Old receivers refuse; setup never drops the selection, scope or pin.
The entry is passed as one `--entry=<value>` argument so a valid
dash-prefixed entry remains a value rather than another CLI option.

This gate does not provision, start, unlock or initialize Vault, fetch a
credential through an identity cache, use an existing plaintext key as fallback,
or repair permissions. Explicit provisioning inhibition is retained in the
bounded, captured child invocation. Locked Vaults, unavailable services and
unsynchronized entries return actionable waiting outcomes for separately
attended owner work. Conflicting keys, unsafe locations and unsupported contracts
refuse; unknown/partial effects retain the original entry, directory and
enrollment operation for reconciliation. Receiver stdout/stderr diagnostics are
never forwarded. Key installation is reuse-only, including partial-pair recovery,
not permission to rotate an identity or invent another operation.
An inherited identity-cache `AGENT_VAULT_PINNED_CONTEXT` is removed only in this
SSH child so it cannot override the explicit configuration root. Native Vault
configuration selectors and the caller's environment remain unchanged.

A currently authenticated, converged selected SSH application is returned ready
without credential calls or reinstallation, even if Vault is now locked.
It still requires actual read-only Fleet key inspection with both the selected
public fingerprint and original request digest, followed by source
requalification; a changed key or request is not healthy selected readiness.
The same pinned inspection is required when the application becomes ready
during Vault preparation or bootstrap; late readiness does not restore the
reference-only shortcut or skip either pin.
Check-only calls perform no key installation. Default, outbound-only and
nonnull-SSH/null-key contexts retain their existing behavior.
Source tests and a captured synthetic Vault process qualify this adapter seam;
they do not prove installed Vault, synchronized storage, live fleet enrollment
or interactive logon. Those remain separately qualified owner operations.

### Data-only installed Vault selection

Declaration version `5` keeps the version-four context shape but replaces
`ssh_key.argv` with the fixed `ssh_key.owner: "agent-vault"`. A nonnull object
has exactly these fields:

```json
{
  "owner": "agent-vault",
  "configuration_root": "<absolute configuration directory>",
  "entry": "SSH/example",
  "directory": "<absolute dedicated key directory>",
  "fingerprint": "SHA256:<existing public-key digest>"
}
```

The four data fields retain their version-four validation and semantics.
Inspection binds them to the qualified repository revision without resolving
or invoking Vault. Version-five `ssh_key: null` retains the existing no-Vault
path. Versions one through four remain strict and unchanged: version four
still requires `argv`, and neither form accepts the other's selector. An older
engine refuses version five rather than silently dropping its requirements.

On explicit apply when keys are needed, the engine selects the existing
installed Vault owner, not an executable from configuration or `PATH`.
An explicit Worktrees installation context requires the active same-cell
Vault through the canonical pinned peer launcher. Without an explicit context,
the existing core payload/installation resolver must select a ready legacy
installation. Missing, foreign, inactive, migrating or unsupported ownership
refuses without another owner or implicit installation. A completed runtime
with immutable retention support is required.

The engine retains the selected physical runtime/build and installation
binding across the existing invocation-contract, prepare and install calls.
It revalidates that selection before and after each bounded child; a changed
owner, receipt or selected runtime refuses rather than switching midway.
The normal caller retains that same binding through the subsequent Fleet key
inspection and rechecks it before enrollment, SSH configuration preparation
and application installation. It never recaptures a different Vault after
credential preparation.
The native peer boundary also enforces its own execution-time governance.
Native same-cell Vault invocation currently supports Windows and Linux;
other platforms refuse rather than using another owner.
Legacy children receive the selected runtime root and no inherited Vault
credential/cache/endpoint selectors; same-cell children use Vault's native
rebound environment. Both retain explicit provisioning inhibition and the
selected `--configuration-root`.

All existing source, request-digest, key-location and fingerprint checks,
ready-application shortcuts, check-only behavior, waiting/error outcomes and
original-operation recovery remain. This is not permission to install, start,
unlock, initialize or replace Vault or an identity. Full installed Manager/
repository/Fleet setup, attended storage/authentication and logon acceptance
remain separate from source parser/selection and captured-child tests.

PowerShell receives `-RequestFile <path>`; Bash receives
`--request-file <path>`. The selected runtime must already be available.
Execution uses the selected checkout as cwd with inherited stdio. No command
string evaluation, arbitrary extra arguments or interpreter installation occurs.
On Windows, the explicitly trusted PowerShell entry uses process-scoped
`-ExecutionPolicy Bypass`, matching the toolkit's installer invocation. It does
not persist an execution-policy change or override higher-precedence Group Policy;
an enforced refusal is still failure.
Native Windows attended calls retain their actual console for Git, the setup
entry and its descendants; redirected/background calls remain windowless.
Both paths explicitly inherit native standard descriptors so a headless child
does not silently replace redirected caller pipes with another console's handles.
Nonterminal acquisition and setup also disable GCM interaction and inherited
Git/SSH askpass fallbacks call-locally, rather than relying on Git's terminal
prompt flag alone. Cached credential reads remain available.
Each invocation gets fresh caller-owned temporary communication files:

```json
{
  "version": 1,
  "mode": "check",
  "repository": {
    "url": "https://github.com/example/sample",
    "commit": "<exact-acquired-commit>",
    "path": "<absolute-checkout>"
  },
  "result_file": "<absolute-result-path>"
}
```

Check mode is read-only. Write exactly this bounded UTF-8 result shape:

```json
{"version":1,"status":"ready","message":"Repository owner verified readiness"}
```

`status` is `ready`, `needed`, `waiting`, `refused` or `failed`. The nonempty
display message is at most 4096 characters without control characters.
Do not include credentials. Result JSON has the same 16 KiB, duplicate-field,
unknown-field, version and type checks as the declaration.

The engine checks first. `needed` permits apply; successful apply is followed
by another check. Only zero exit plus `ready` from check establishes readiness.
A nonzero exit, absent/malformed result or unknown outcome never counts as
success. `waiting` returns control to the owning human/restart flow.
Interruption does not trigger a replay: a later invocation starts with the
owner's read-only check. Temporary files are communication, not a second
persistent operation ledger.

### Selected engine invocation and native configuration trust

The request/result files remain version one. The selected engine additionally
sets invocation-local `AGENT_WORKTREES_REPOSITORY_SETUP_ENGINE` for its setup
child, replacing any inherited value:

```json
{
  "schema": "copilot-extensions.repository-setup-engine",
  "version": 1,
  "argv": ["<selected-python>", "-I", "-m", "agent_worktrees"],
  "capabilities": {
    "new_admission": {
      "contract": "agent-worktrees.new-admission",
      "version": 1,
      "required_applications": 1,
      "configuration": "require_application_readiness",
      "read_only": true
    }
  }
}
```

For a shell entry that needs to start its repository-owned Python adapter before
it can parse JSON, `AGENT_WORKTREES_REPOSITORY_SETUP_PYTHON` contains that same
absolute interpreter path without JSON encoding. The engine sets both values
together and replaces inherited values; the adapter can verify they agree.
This avoids using an ambient Python merely to discover the selected one.

Invoke this exact argv plus the intended engine arguments. It preserves the
selected interpreter environment, including a virtual environment's executable
path, rather than resolving its symlink to a different base interpreter.
It does not search PATH, bootstrap another engine or advertise package versions
as capabilities. Existing scripts may ignore this additive environment contract;
new consumers requiring it must refuse its absence or an unsupported shape.
They must not retry a different engine or weaker operation after refusal.

`capabilities` is additive to the version-one engine context. `new_admission`
projects `new-admission-v1.json` packaged beside the executing Worktrees module,
not metadata from the repository being set up, an inherited context, or a newer
payload. The same selected interpreter still executes the supplied argv.
Existing setup scripts may ignore this field. A consumer requiring application
admission must validate this contract and its required capability before its
dependent setup effects or mandatory configuration opt-in. An older or rolled-back
provider without this declaration means unknown support: retain an explicit
unsupported-requirement refusal, not a package-version guess or a retry with
weaker policy. No legacy field or request/result form is retired. This projection
neither enables the policy nor qualifies authentication, application health,
installation delivery or the separate knowledge prerequisite.

The descriptor grants no folder trust. A trusted setup entry that explicitly
needs native repository configuration may invoke:

```text
<selected-engine-argv> trust-repository-configuration --repo <absolute-checkout>
  --repository <https-url> --commit <full-commit-id> --apply
```

This explicit action qualifies the clean ordinary checkout, canonical remote
and exact current commit before calling the existing serialized folder-trust
owner. It refuses missing apply, invalid/stale commit, unqualified source or a
different native Copilot home before changing trust. Success emits
`copilot-extensions.repository-configuration-trust`, version `1`, with
`status: "ready"` and `changed` (false means already trusted). It preserves
other configuration and grants no tool permissions or extension approval.
Source change detected after the trust write refuses completion without
silently undoing or replaying that completed effect.
Bounded Git timeouts also return the versioned refusal. Refusals retain
`changed: true` when a write completed, false when the owner confirmed existing
trust, or null when no write outcome has been confirmed.

This command is not a replacement for the admitted managed-worktree trust owner.
Source execution consent alone still creates no Copilot trust record: the
repository's declared setup behavior must explicitly choose the native trust
action. This additive action and environment descriptor are provider expansion;
older request-v1 scripts retain their prior behavior. A newer consumer on an
older provider must retain missing-descriptor or unknown-command refusal before
dependent effects, not infer support from a newer payload's metadata.

### Explicit enabled-payload preparation

A trusted repository entry can prepare its enabled payloads before composing
runtime/module readiness. This is a separate, opt-in engine boundary:

```text
agent-worktrees prepare-plugin-payloads --repo <absolute-checkout>
agent-worktrees prepare-plugin-payloads --repo <absolute-checkout> --apply --expected-plan-digest <digest>
```

The read-only form emits `copilot-extensions.plugin-payload-preparation`, version
`1`, with `planDigest`, qualified per-source `entries` and `payloadsReady`.
Each entry's additive `payloadRoot` is the qualified physical payload directory
when ready, or null when unavailable. It is an availability observation, not an
installer command or runtime-readiness claim. A caller may use the selected
owner's documented command catalog at that root; it must not reconstruct another
plugin's path from a sibling payload. Consumers requiring this additive field
must refuse its absence on an older provider.
Exit zero from observation means a valid observation, not that payloads are
available. The plan binds the resolved checkout and physical Copilot home as well as its
declarations, so retargeting a state-directory symlink or junction invalidates it.
Explicit apply requires that exact plan, rechecks declarations and
availability before acquisition, and independently observes the result.
It exits zero only when all selected payloads are available, two for incomplete
apply, and one for invalid or stale input.

Selection uses the strict current-checkout plus user declarations, preserving
native/local precedence and explicit repository disablement. Available local
and installed payloads use the existing identity/provenance checks. Conflicting
marketplace sources, malformed settings, ambiguous roots and unavailable local
payloads refuse; no guessed marketplace or installed-directory source replaces
the declared origin. The native Copilot home must match the inspected owner.

Only missing remote payloads are installed, through native Copilot acquisition
and the existing bounded process/activation-preservation owner. Existing
payloads are not refreshed, activation is restored, and repository source
settings are not edited. Changed inputs stop further work while retaining
completed acquisition outcomes. Neither interruption nor failure automatically
replays an install. Read-only inspection creates no preservation transaction.
An outstanding activation transaction remains visible as `activationPending`
and prevents preparation success even when payload files are available.
Explicit apply lets the existing acquisition owner finish a positively witnessed
restoration; unknown settlement remains refused without another install.

Payload availability does not prove a requested version, runtime installation,
service health, authorization or task readiness. Repository setup must compose
the required-module/readiness owners separately and retain their refusal,
restart and consent boundaries. This command is not an ordinary-launch hook,
a suite update, or permission to deploy a runtime.

Planning can inspect repository declarations without trusting their execution.
Native acquisition still enforces Copilot's own configuration trust: a
repository-only marketplace needs the selected checkout's native folder trust,
while an already user-declared source does not. This command never grants that
trust or synthesizes user configuration to bypass native trust. The caller must establish
any required trust through its admitted repository/worktree setup owner;
untrusted repository-only acquisition remains unsuccessful. Execution consent
for `repos setup` is not itself a Copilot folder-trust record.

## Manager caller and current qualification

The [Manager setup caller](../../../worktree-manager/README.md#optional-repository-setup)
establishes fresh core readiness before invoking this seam. No-repository setup
and the Manager's own update source remain separate.

Focused controls exercise real Git qualification, native Windows
PowerShell check/apply/final-check, refusal/preservation, the real
Manager-to-engine-to-repository subprocess path, and inherited Manager-to-engine
stdin/argv/exit status. A disposable loopback TLS Git server exercises real
clone, a Git-owned synthetic credential-helper challenge, registration and
final setup readiness. The equivalent POSIX consumer is platform-gated.
These synthetic controls do not establish real attended provider authentication,
fresh-box provisioning, managed-device interpreter/feed qualification or an
adopter's private binding/connectivity. Those need their own authorized lanes.
