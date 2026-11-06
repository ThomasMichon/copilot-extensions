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
  "argv": ["<selected-python>", "-I", "-m", "agent_worktrees"]
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
