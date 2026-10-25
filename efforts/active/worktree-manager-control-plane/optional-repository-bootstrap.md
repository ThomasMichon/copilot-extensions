# Optional repository bootstrap caller

- **Status:** Reviewed contract with a bounded engine seam and optional Manager
  caller implemented in source. Fresh-entry and attended-provider qualification
  remain open; this is not a working adopter onboarding claim.
- **Parent:** [Worktree Manager control plane](README.md), Phase 5.
- **Coordination:** #8545 owns the optional bootstrap caller and its bounded
  prerequisites. #356 retains first-repository registration, setup, trust and
  readiness; its standalone CLI, local/remote/skip choices and discovery are
  not reassigned.
- **Vision:** closes installer
  [`one-line-bootstrap`](../../../visions/installer/README.md#one-line-bootstrap)
  as refined in #8546; preserves independently installable plugins.

## Request and boundary

Public-safe request summary: the published Manager installer may accept a Git
repository in the same attended invocation. Establish core toolkit readiness,
then acquire through Git's credential owner and run explicitly trusted,
repository-owned setup. Preserve the no-repository journey and the Manager's
separate installation/update source.

The contract below was submitted and reviewed before implementation.
It specifies the caller's requirements on the existing acquisition/setup owners,
not a second engine or permission to implement another participant's reserved
scope. Repository-specific identity, knowledge, machine and connectivity policy
remain downstream. No source maintenance, updater enrollment, agent launch,
service role or fleet allocation follows merely from selecting a repository.

### Bounded implementation ownership

Following the explicit operator-authorized transfer recorded on #356, the
#8545 driver now owns the shared acquisition/registration/setup/trust/readiness
seam required by this caller. The reviewed behavior below is unchanged. Preserve
earlier unpublished work; standalone discovery, guided local/remote/skip UI and
the broader Configuration workbench remain outside this transfer. References
to the #356 owner below continue to identify that one shared implementation
boundary, not a competing engine.

## Source-grounded composition

| Existing seam | Current behavior | Required consumer qualification |
|---|---|---|
| `worktree-manager/bootstrap.ps1` and `bootstrap.sh` | Forward command arguments through the canonical runtime installer to the selected Manager | Preserve exact argv and status; never use repository selection as Manager source configuration |
| `worktree_manager/setup_cli.py` | Plans/applies prerequisites, refreshes their actual readiness, then drives the real core installer; #8556 supplies the prerequisite correction | No repository operation before successful fresh core readiness |
| `worktree_manager/engine_client.py` | Resolves attributable engine commands; captured JSON reads and inherited-stream passthrough are separate | Use the process boundary, not plugin imports or captured stdin for attended setup |
| `agent_worktrees/repos.py::clone_repo` | Owns clone and registration; supports explicit target or configured source root | Existing-target identity, preservation, credential selection and attended execution need qualification before this caller uses it |
| #356 first-repository slice | Reserves registration/setup/trust/readiness and standalone register-URL behavior | One shared supported setup contract; caller does not invent a competing register command |

In particular, today's clone path registers an existing directory without
qualifying its remote or work, captures Git output under a fixed deadline and
uses a best-effort scoped-auth override. A zero exit from that path is not
evidence that this attended contract exists. Extend the owner seam where
necessary; do not hide those gaps behind a Manager-side direct `git clone`.

## Caller interface

The Manager command is:

```text
worktree-manager setup [--apply]
  [--repository <https-git-url>]
  [--repository-target <absolute-path>]
  [--trust-repository-setup]
```

The bounded caller exists in source; do not copy it into an adopter README as
a working onboarding command before the remaining qualifications pass.
The existing bootstrap forwards the equivalent `setup` argument array; it needs
no second installer, environment-variable repository selector or change to
`source set`. `--repository-target` and `--trust-repository-setup` without a
repository are argument errors, detected before provisioning.

Version 1 accepts only an HTTPS Git repository URL without credentials, query
or fragment. Reject local paths, arbitrary transport helpers, SSH and malformed
URLs explicitly; they remain possible future owner extensions, not silent
fallbacks. Selection never creates a hosted repository or fork.

Without `--apply`, plan core work and the requested repository stage without
network acquisition, registration or executing repository code. An unavailable
checkout means its setup declaration is uninspected, not supported by inference.
Without `--repository`, preserve existing setup and bare-bootstrap behavior.
Before setup trust is granted, an apply invocation may perform the explicitly
selected acquisition/registration, but must stop before running any repository
code, including its readiness check.

Use the explicit target or the engine's configured source root. When neither
exists, the attended owner asks for the source location; noninteractive use
refuses with the missing choice. Do not invent a root, modify a global source
preference or select another checkout to make the command succeed.

The trust flag authorizes setup from this explicitly selected repository in
this invocation, not other discovered repositories or future revisions. Before
execution, show canonical remote, exact acquired commit, selected checkout and
setup entry. Bind execution to that inspected commit/declaration/entry; a
changed source requires renewed qualification, never an unnoticed refetch.
With no flag, a genuine terminal may collect equivalent explicit consent for
that displayed source; redirected callers never auto-answer.

## Ordered operation

1. Parse and validate caller intent. Preserve the Manager source configuration.
   Plan mode stops without repository effects.
2. Establish prerequisites and the actual core through their existing owners.
   Refresh PATH/readiness after provisioning. A failure or genuine required
   restart stops repository work and retains the completed core installation.
3. Resolve the acquisition target through the engine. Inspect any existing
   checkout/registration before changing it. Reject foreign identity,
   conflicting registration, source modifications or unsupported ownership;
   never reset, stash, clean, convert an active anchor, steal a live worktree
   or overwrite a path. Reuse only a qualified matching source. The engine
   continues to own class, adoption and registration semantics.
4. Acquire using Git's configured credential owner. Give that owner the actual
   attended terminal/browser opportunity. An explicit repository account must
   not degrade to ambient authentication; no global account switch, copied
   token, credential in argv/configuration or alternate remote on failure.
   Do not inherit unrelated Git repository selectors from the bootstrap's
   staging checkout. Keep native children headless with explicitly inherited
   caller handles; background/ACP calls never become attended implicitly.
5. Resolve and validate the repository-owned declaration below. Qualify source
   identity and obtain setup trust before even its check entry executes.
6. Invoke the owner's check, then apply only if needed and explicitly selected,
   then check again. Preserve owner interruption/restart and retry semantics.
   A successful script exit alone is not repository readiness.
7. Report core, acquisition/registration and repository setup separately.
   Repository failure does not uninstall core, erase a partial checkout or
   repeat an unknown-outcome operation. Rerun reconciles through its owner.

No hard authentication timeout may be disguised as cancellation or automatic
retry. Use the existing process/progress lifecycle contract, forwarding useful
progress rather than buffering a provider prompt until it has already timed out.

## Shared repository setup declaration

The #356 owner retains implementation of this shared seam. The proposed
repository-owned path is `.agent-worktrees/repository-setup.json`:

```json
{
  "version": 1,
  "entrypoints": {
    "windows": {"runtime": "powershell", "script": "setup-manager.ps1"},
    "posix": {"runtime": "bash", "script": "setup-manager.sh"}
  }
}
```

This is a narrow repository entry declaration, not a plugin manifest, preset,
credential file, arbitrary shell command or additional operation ledger.
One entry per supported platform is sufficient. Version 1 supports the named
PowerShell/Bash runtimes supplied by the qualified toolkit. Do not silently
switch interpreter, download one or evaluate a command string.

Read a bounded UTF-8 JSON object (at most 16 KiB), rejecting duplicate/unknown
fields, unsupported versions, missing platform entries and invalid values.
The script must be a regular, tracked repository-relative file within the
inspected checkout. Reject absolute/traversing paths, symlink/reparse escapes,
modified declaration/entry bytes and source changes between inspection and
launch. The repository's trusted entry remains responsible for its imports and
effects; this is not a sandbox or an attestation chain against same-user peers.
Do not infer setup from README text, conventional filenames, Copilot hooks or
the per-worktree `setup_hook`.

Use argv, not string evaluation. PowerShell receives `-RequestFile <path>`;
Bash receives `--request-file <path>`. Those platform adapters implement the
same request/result contract. A temporary, caller-owned request contains:

```json
{
  "version": 1,
  "mode": "check",
  "repository": {
    "url": "https://github.com/example/sample-harness.git",
    "commit": "<exact-acquired-commit>",
    "path": "<qualified-absolute-checkout>"
  },
  "result_file": "<caller-owned-absolute-result-path>"
}
```

`mode` is `check` or `apply`; check is read-only. Execution uses the qualified
repository as cwd. No arbitrary caller arguments, secrets or downstream
configuration are copied into this request. Each call gets a fresh bounded
result file under the existing scratch owner; files are communication, not
persistent resume state. The repository's setup owners retain durable state.

The matching result is a UTF-8 JSON object bounded to 16 KiB, with the same
duplicate/unknown-field and type rejection as the declaration. It has exactly
`version: 1`, `status` (`ready`, `needed`,
`waiting`, `refused` or `failed`) and a bounded nonempty `message` suitable for
display, with no credentials. Validate it even when the process exits zero.
Only exit zero plus `ready` from the final check establishes repository
readiness. `needed` from check allows an authorized apply; `waiting` stops for
the owning human/restart condition; refusal, failure, missing/malformed output
or any nonzero exit is not success. Never reuse an earlier result file or
reinterpret an unknown status as readiness. After interrupted apply, query
the owner again rather than assume replay is safe.

The Manager reports readiness only for that repository's declared setup.
It does not infer authenticated connectivity, private binding, running service
health or full machine adoption unless the repository's own checks actually
establish the required outcomes.

## Implementation and validation plan

- [x] Reconcile this consumer contract with #356's shared setup owner. Preserve
  that reservation; make any API differences explicit in this one contract,
  not duplicate implementation or an invented acknowledgement ceremony.
- [ ] Qualify the owner acquisition/registration seam for existing-target
  identity, preservation, exact source selection and attended credentials.
  Keep standalone discovery/UI and local/remote/skip acceptance with #356.
- [x] Implement the #8545 caller with strict arguments, readiness ordering,
  inherited attended IO and unchanged no-repository behavior. An old engine
  lacking the required seam refuses just the requested repository stage.
- [x] Implement the shared declaration/check/apply seam only through its
  retained owner; add a synthetic repository consumer before a real adopter.
- [ ] Qualify fresh-entry interpreter and configured dependency-feed routing
  before claiming support on a managed device; do not depend on code available
  only after the selected private repository has been fetched.
- [ ] Cover URL/argument/declaration/result refusal, dry-run nonmutation,
  compatibility, credential failure, source conflict, interrupted/unknown
  outcome and retry. Byte-compare pre-existing source work, unrelated registry
  entries and Manager source configuration on every refused path; report any
  completed addressed clone/registration rather than roll it back as foreign
  cleanup.
- [ ] Run real bootstrap-to-engine-to-synthetic-entry subprocess tests on
  Windows and POSIX, proving ordering, exit status, inherited IO, final check
  and no-repository compatibility; mocks alone are insufficient.
- [ ] Run a disposable first-touch lane, including gitless Manager acquisition
  followed by required Git readiness, credential prompt opportunity and rerun.
  Record unavailable platform tiers explicitly rather than claim a pass.
- [ ] Qualify actual attended authentication in an authorized disposable
  environment before publishing an adopter command as working. Do not use a
  shared host or a production target as an incidental test.
- [ ] After source review and required CI, distinguish merge, promotion,
  authorized installation and actual runtime proof. Update living Manager
  and contract docs only when the interface exists. This plan does not close
  #8545, #356 or the parent effort.

## Bounded implementation evidence

The engine's [repository setup reference](../../../plugins/agent-worktrees/docs/repository-setup.md)
owns the implemented CLI and request/result details; the
[Manager reference](../../../worktree-manager/README.md#optional-repository-setup)
documents its caller. Existing managed anchors remain owner-reconciled rather
than converted by acquisition. Explicit account selection is currently
qualified only for GitHub.com with a supported GCM helper; unsupported routes
refuse instead of silently falling back.

Native Windows isolated controls passed: the protocol/acquisition and existing
registry-preservation packet (108 passed, one native POSIX case skipped),
Manager caller plus prerequisite compatibility (52 passed), and the corrected
runtime dependency boundary (six passed). The complete real
Manager-to-engine-to-PowerShell process path passed. A disposable loopback TLS
Git server also exercised actual clone, Git-owned credential-helper challenge,
registration and final setup readiness without changing global credential
configuration. That synthetic helper is not evidence of actual GCM browser
authentication.

The dependency guard's scan now distinguishes deployed runtime sources from
cross-component test harnesses, while a positive control still rejects
production imports. Native Windows tests used the existing contained runner;
Linux-only devcontainer isolation, native POSIX execution, a fresh managed
device and a real attended provider lane were not available in this slice.
No installed runtime or target machine was updated.
