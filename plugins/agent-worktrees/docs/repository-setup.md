# Explicit repository setup

The engine owns acquisition, registration, source qualification and explicit
repository setup. The standalone Manager calls this CLI across a process
boundary; it does not import the engine or clone repositories itself.
This implements the bounded shared seam from #356 and the optional caller
from #8545, not first-repository discovery or guided local/remote/skip UI.

```text
agent-worktrees repos setup <https-url> [--target <absolute-path>]
  [--commit <full-commit-id>] [--apply] [--trust-repository-setup]
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
