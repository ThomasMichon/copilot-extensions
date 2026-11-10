# Repositories Configuration

The contributed **Configuration / Repositories** section uses agent-worktrees'
native registry and resolution APIs. Browsing and refreshing are read-only:
they do not adopt repositories, create worktrees, ensure services, inspect
credentials or launch agents.

Registration/class/local-checkout availability, directional participation,
same-machine-agent enablement, route preference, configured knowledge identity,
resolved state anchor and actual tracked writable pair are separate facts.
The cards show absent, stale, unsupported and unknown/error states explicitly.
Native registry paths use the shared execution-root platform classifier, not
inherited transport labels such as `WSL_DISTRO_NAME`; registration, binding and
read-only resolution therefore select the same platform key.
`repos list --json` reports that native `platform` alongside the inventory, so
explicit-name binding can use the same selection without a control-plane file.
An anchor is never presented as a paired writable checkout. No workspace means
pair inspection is unsupported, rather than inferred from the CLI's CWD.
Both tracked checkout directories must still exist for a pair to be writable.
Manager forwards the engine bootstrap's explicit tracked `workspace`, not its
optional anchor-directory switch destination. Older engines without that field
leave pair inspection unsupported rather than falling back to the Manager's CWD.
Route inspection validates the native selected base and marketplace-overlay
layers, including duplicate keys, while accepting native bare minimal links.
Routing fields are type-checked against the owner's supported locus/delegation
values before projection; invalid nested source values are never stringified
into JSON or inspection cards.
Project registrations must be mappings with typed `base_repo` flags; malformed
entries produce sanitized unknown-state findings instead of failing a section.
Selected repository configuration uses the same committed/fallback source
selection with failure-aware parsing. Invalid selected sources report unknown
state; machine routes retain their resolved peer identity.
Confirmed disabled managed drop-ins do not participate in preference inheritance
or block editing; invalid or indeterminate contributions still require repair.
Route/pair editing and plugin enablement remain unsupported: use their owning
capabilities, not a generic source-file editor.

## Local account preference

**Set future-launch Copilot preference** changes only the selected registered
repository's existing `copilot_account` in the native machine-local registry.
**Reset** removes that key and resumes current native inheritance: selected
project's machine-local configuration, active config drop-ins, then global
defaults. It never stores a copy of an inherited value.

The separate `copilot_identity_switch_enabled` policy is displayed, not changed.
A stored preference does not prove that switching is enabled, authentication is
ready, or an existing worker changed identity. Neither action logs in, mints or
reads credentials, changes global GitHub/Copilot state, or launches a session.
Only non-secret login names are supported; this is not a secret editor.

The CLI is independently usable without Worktree Manager:

```text
agent-worktrees repos configuration catalog --context-project sample
agent-worktrees repos configuration catalog --context-project sample --workspace <absolute-tracked-workspace>
agent-worktrees repos configuration preview --context-project sample --repository sample --operation set --login chosen-login
agent-worktrees repos configuration apply --context-project sample --repository sample --operation set --login chosen-login --preview-token <returned-token>
agent-worktrees repos configuration preview --context-project sample --repository sample --operation reset
agent-worktrees repos configuration apply --context-project sample --repository sample --operation reset --preview-token <returned-token>
```

`--context-project` is intentionally distinct from the front door's global
`--project`: the request must retain an explicit selected-project binding after
global argv processing. The repository is a native registered resource, not a
path or discovery fallback. Catalog rows carry `configuration.target`, effective
and observed state separately, ordered source contributions/revisions and
masked-vs-winning values, local authority and sanitized findings. Source content
outside this narrow projection never becomes a field or receipt.

Preview returns the version-1 JSON contract documented by
[the Manager](../../../worktree-manager/docs/plugin-contribution-contract.md#optional-provider-computed-preview).
Its ephemeral token binds installation, physical machine, selected project,
repository, normalized operation/value, exact registry bytes and the full
relevant default-source/file-tree baseline. It is a stale-proposal check, not
a credential or persistent authorization ticket.

Apply checks the token again while holding the native registry's bounded,
cross-process write lock. All ordinary registry writers use the same lock and
compare their exact read baseline before atomic replacement. A competing
ordinary command, another apply, source change, target change or malformed
composition requires a new preview and consent; there is no automatic retry
or merge of a stale proposal. Missing/invalid tokens (including old hosts'
empty token substitution) fail before a write. No lock or state directory is
created by catalog or preview. Successful writes may create the registry's
local `repos.lock` and shared `pr-rewrite-authority.lock` sidecars; no
repository/knowledge/plugin/preset source is
written. Unrecognized local registry metadata is preserved by this API.
YAML aliases are detached at the selected registration before an edit, so
unselected registrations and metadata cannot change through shared mappings.

## Acquire a remote knowledge checkout

For an eligible unbound, canonically registered worktree-class stateless harness,
**Acquire a remote knowledge checkout only** downloads an existing repository
to an absent absolute destination. This uses the shared repository-bootstrap
acquisition owner, not `repos setup`: it neither reference-registers the
checkout nor inspects or executes its repository setup.

```text
agent-worktrees repos configuration knowledge-acquire-preview --context-project sample --repository-url https://github.com/example/knowledge.git --checkout-path <absolute-new-checkout>
agent-worktrees repos configuration knowledge-acquire-apply --context-project sample --repository-url https://github.com/example/knowledge.git --checkout-path <absolute-new-checkout> --preview-token <returned-token>
```

Preview performs only local inspection. It rejects credential-bearing or
parameterized URLs, unsupported transports, configured Git URL rewrites,
existing destinations, indirect ancestors, nested Git repositories and
registered name/path/provider-identity collisions. The parent must already
exist. Preview creates no target or lock, contacts no provider and observes no
credential. Its token binds the selected installation, machine, project,
normalized URL/path, parent identity, native registry/configuration/policy and
nonsecret account selection. Malformed binding inputs are not treated as unbound.

Confirmation defaults to Cancel. Apply revalidates the token under the shared
registry serialization and reserves the destination through exclusive directory
creation before the shared hooks-disabled clone. A concurrent destination winner
is preserved, not replaced. Only the configured noninteractive credential route
is retained: terminal, askpass and GCM browser/device prompts are disabled.
Explicit account selection requires the shared qualified credential owner; it
never falls back to ambient credentials. Public access without a credential
remains supported. No login, account switch, token copying or credential pin
is added.

Preview and apply have aggregate 18/26-second native budgets, each including a
two-second cleanup reserve inside the Manager's 30-second action deadline.
One caller-owned headless worker encloses local Git, clone and credential
descendants; a timeout does not renew their clocks. Large/slow acquisitions
may exceed this finite lane. Nonzero Git exit, cancellation, timeout, lost
receipt, changed native inputs or failed final source qualification report
failure/incomplete state with the unfinished stage and target presence
(or unknown presence). Lost or invalid native completion is a sanitized failure,
not proof that cleanup completed. An unconfirmed native cleanup propagates its
original `OwnedCleanupError`, including process/Job, retained resources and retry
operation, through the in-process failure boundary. Callers must retain and
settle that owner before another attempt; it is not reduced to a PID-only receipt.
No partial checkout is deleted, reset or retried.
Inspect local ownership read-only with
`repos registration inspect --anchor <destination> --default-name <intended-name>`
before choosing recovery; that inspection does not qualify source completeness.

Success returns `status: acquired`, canonical checkout and acquired commit.
The source remains unregistered; native registry/configuration and provider
policy are unchanged apart from normal local registry-lock sidecars. It is
not bound, paired, launched or New-ready. Refresh, then use the separately
confirmed local-registration action below with explicit branch/account
metadata, followed by separate binding confirmation.

The executing version-1 catalog advertises this optional action through its
dedicated `knowledge_acquisition_available` row/marker. Older providers omit it;
a newer manifest does not emit acquisition syntax without that marker.
Older manifests retain registration/binding behavior. Missing or rolled-back
support affects only acquisition; there is no effectful fallback/replay.
Hosts that substitute an empty preview token refuse before acquisition.
Ordinary `repos setup`, including attended authentication, immutable commit
selection and reference registration, retains its existing defaults.

## Register an existing local knowledge checkout

For a canonically registered, worktree-class, unbound stateless harness, the native catalog
offers **Register an existing local knowledge checkout**. Supply a new name,
an absolute existing Git anchor, the intended default branch and an optional
nonsecret repository-account mapping. The branch and account are registration
metadata, not observations of the server default, landing policy or credentials.
Ordinary, direct bare and converted-clone bare anchors are supported; task worktrees, descendants and
existing name/path/repository-identity collisions require their normal owners.
Select the anchor itself, never its `.git` metadata directory. An existing
registration at that nested metadata path also conflicts with the anchor,
including for local-only repositories without a provider identity.

```text
agent-worktrees repos configuration knowledge-register-preview --context-project sample --repository knowledge --checkout-path <absolute-anchor> --default-branch main
agent-worktrees repos configuration knowledge-register-apply --context-project sample --repository knowledge --checkout-path <absolute-anchor> --default-branch main --preview-token <returned-token>
```

Add `--account <non-secret-login>` to both commands to declare an explicit
mapping. Preview performs bounded, noninteractive local Git reads only. It
does not contact the origin or inspect credentials. An origin containing
embedded credentials or URL parameters refuses without disclosing it. Origin
selection includes Git configuration includes and the same effective URL
rewrites used by the binding owner; it never treats an included origin as absent.
The preview identifies the local registration action and its exact selected
inputs. Apply requires confirmation, rechecks those inputs under the shared
registry lock, and uses the existing compare-before-replace writer. Unrelated
registry metadata is preserved. A concurrent registry, configuration, binding,
checkout or origin change requires a new preview; existing registrations are
never overwritten or reclassified.
Like native preference editing, selection and the final write reject registry
source indirection. A symlinked registry requires inspection by its owner;
registration never replaces that link while leaving its backing source unchanged.
The strict native reader rejects nonregular registry files before fingerprinting
can open them, retaining the existing regular-file and subsequent symlink checks.
After acquiring the guard-publication lock, apply re-inspects the full proposal
and compares its token again before withdrawing prepared views for persistence. Checkout, origin,
policy or preference drift during that wait refuses rather than persisting the
earlier selection; the same aggregate deadline covers both inspection passes.
A validation refusal leaves the existing prepared views untouched. Successful
validation still withdraws them before the registry mutation begins.
If the execution deadline expires during withdrawal before mutation starts,
the owner restores the prior views within its original cleanup reserve. It
does not restart the clock or restore old views after an uncertain source write.
Incomplete restoration is an explicit guard-owner failure, not persistence.

Successful return means registration and its normal protection publication:
a new non-agent `class: knowledge` entry, normal registry/publication lock
sidecars, and refreshed guard facts for already-prepared callers. Existing
guard views are withdrawn before persistence; they cannot keep reporting
complete coverage while omitting the new protected anchor. Unlike ordinary `repos add`, this
operation does not pin Git credentials. It does not create/clone a repository,
write Git configuration or branches, bind knowledge, compose an overlay,
allocate a pair/worktree or launch a session. Refresh and use the separately
confirmed binding action below. A lost result requires catalog readback before
retry; an already-present registration is a conflict, not an overwrite request.
If source persistence succeeds but guard publication fails, the command returns
nonzero with `status: incomplete` and `registration_committed: true`; affected
views remain unavailable. An uncertain write reports a null committed result.
Inspect actual registration and publication before retrying.

Preview and apply each share their native deadline across local Git reads and
committed-configuration probes, including policy revalidation and guard-fact
publication. The apply budget also bounds publication-authority, registry and
guard-publication lock acquisition. An enclosing policy scope cannot extend
that deadline or supply an earlier snapshot instead of a fresh revalidation.
The shared owners accept this optional budget without changing their existing
callers' default behavior.

The executing catalog supplies a dedicated optional row/marker for this
operation. Older providers omit it, so a newer manifest does not emit the new
syntax to them. Older manifests retain existing behavior; hosts that cannot
execute provider previews cannot apply without a valid token. Rollback affects
only this optional action, not preferences, inspection or existing binding.

## Confirmed existing knowledge binding

When the selected harness requires missing knowledge, **Bind this existing
knowledge repository** is offered on eligible canonical knowledge/worktree
registrations. It delegates to the enabled, selected `harness-knowledge`
payload. An unavailable payload or unresolved native composition leaves the
action unavailable; browsing never installs it or starts authentication.
Payload activation uses the native selected home, including `AGENT_HOME`, and
prefers the current project's selected root before the global fallback.
An ambiguous native default-repository selection remains a recoverable
configuration finding; binding refuses it without choosing a default.
Binding identifiers use exact canonical Configuration names beginning with an
ASCII letter or digit. The same native check drives action eligibility and
invocation; unsupported identifiers remain inspectable without a binding action.
Generic Copilot-preference editing retains its own broader name rules.
When binding is required but its payload is unavailable, the section's visible
banner directs the user to enable and prepare its qualified owner.

```text
agent-worktrees repos configuration knowledge-preview --context-project sample --repository knowledge
agent-worktrees repos configuration knowledge-apply --context-project sample --repository knowledge --preview-token <returned-token>
```

Preview qualifies existing Git roots, canonical class/path/provider/account
facts and the native stateless configuration destination. Apply rechecks
registration and configuration under the binding owner's existing lock and
uses its preserving writer and personal-plugin composer. An absent or identical
binding is supported; conflicting inherited/local selections require explicit
re-pointing through the normal binding owner. No registration, clone, repository
creation, credential editor, source commit or session launch is added.

The result distinguishes a persisted binding from an incomplete result after
the pointer committed. Refresh actual readiness before retrying or creating a
New task; a timeout does not prove that a prior write did not happen. Binding
does not retrofit an existing tracked pair or establish server landing policy.
The native New-admission gate remains authoritative.

The native binding worker itself holds the existing registration-writer lock
through the pointer commit. Its registry wait, execution and cleanup share
the existing finite caller-owned budget; a caller losing its result cannot
release a still-running writer's lock. Binding Configuration capability version 2 is
required; older binding payloads refuse rather than use an unlocked apply.
Direct binding-script Configuration apply routes through this same native
transaction. The owner's internal `apply-locked` delegate is not a standalone
apply API. Final preflight confirms registration again after its Git/account
probes. Pointer completion is captured at the preserving writer boundary, so
a later cleanup or observation failure still returns a nonzero incomplete
receipt with `pointer_committed: true`.

The native worker can supply its selected payload with a private read-only
in-process adapter for native repository inventory and this project's
configuration/state-root handlers. Reads remain fresh and all original
preflights/rechecks still execute; only repeated cold interpreter startup is
removed. Preview remains read-only without taking a writer lock; apply retains
its registration lock through pointer commit. Native JSON uses the existing real-stdout capture seam. The adapter
restores project/cwd/streams and does not intercept write, authentication,
composition or another project's commands. Unrecognized calls use the existing
subprocess path. Older payloads ignore the optional callback; a newer payload
under an older worker retains the unchanged subprocess baseline. Neither the
18/26-second native budgets nor binding Configuration capability 2 changes.

## Portable harness-to-knowledge preferences

The native identity owner exposes a read-only catalog without enrolling
installation-cell state:

```text
agent-worktrees repos configuration identities --context-project sample
agent-worktrees repos configuration knowledge-resolve --context-project sample --harness-id <native-repo-id> --knowledge-id <native-repo-id>
```

`identities` emits `agent-worktrees.repository-identities`, version `1`, with
`capabilities.knowledge_preference: 1`. IDs use the same normalized-remote
identity as native project state and configuration context, in legacy and
namespaced installations. A local-only remote has no portable ID. Display names
and checkout basenames never select a portable identity. Duplicate canonical
IDs are ambiguous, not aliases that can silently pick a registration.
Local account, class and default-branch selection stay with the native registry;
portable documents carry IDs, not account choices or absolute checkout paths.
Do not export this whole local catalog into a synchronized preference document.

Resolution requires exactly one available worktree-capable registration for
each ID, a distinct knowledge repository, and a harness ID matching the explicit
context project. Missing, stale, ambiguous, unsupported-class or local-only
registrations refuse without discovery, cloning, registration repair or
account selection. The result uses `agent-worktrees.knowledge-preference`,
version `1`, with the native logical execution-machine identity.

An effective local binding remains authoritative. Resolution/preview reports
`already-bound` when it matches, or `binding-preserved` when it differs, with
no adoption token and no binder invocation. An explicitly unresolved local
binding is an error to repair through its owner, never absence permitting a
shared fallback. A suppressed/absent portable suggestion needs no native
binding call. Related venue choices retain their existing owner; this API
does not provision or replace a local route.

The native loader validates binding-bearing global and machine sources before
default selection. Booleans, numbers and other non-string values refuse rather
than becoming an absent binding through truthiness; null/empty retain their
existing unset behavior. A quoted repository name retains its string identity.
Portable resolution also inspects the native acquisition results for global,
project-local and drop-in binding sources. Malformed or unreadable sources
refuse as `binding-unresolved`; genuinely missing sources remain unset.
Existing binding names use the native resolver's whitespace normalization before
resolution and canonical-ID comparison.

Where no binding exists, adoption remains an explicit separate operation:

```text
agent-worktrees repos configuration knowledge-preview --context-project sample --harness-id <native-harness-id> --knowledge-id <native-knowledge-id>
agent-worktrees repos configuration knowledge-apply --context-project sample --harness-id <native-harness-id> --knowledge-id <native-knowledge-id> --preview-token <returned-token>
```

Preview returns the selected binding owner's `native_preview` and a combined
confirmation token binding canonical registration, execution machine, existing
binding and the native transaction token. Apply re-resolves and previews before
passing that native token to the existing version-2 binding transaction. Changed
IDs, registry data, scope or bindings refuse rather than adopting a new target.
Portable apply additionally requires the binding owner's
`portable_binding_precondition: 1` capability. It carries the confirmed
resolution into the registry-locked worker and rechecks it inside the existing
pointer-write precondition, including machine changes while waiting for locks.
An older owner refuses portable apply rather than silently skipping that check;
the original name-based version-2 flow remains supported.
The existing locked registration/pointer rechecks, selected payload, preserving
writer and personal-plugin composer still own all effects and partial receipts.
Pair creation and running-session delivery remain separate readiness gates.

This is additive provider support. The original by-name `--repository` form is
unchanged; do not combine it with portable IDs, including supplied empty
options. Confirm the selected runtime's
`repos configuration --help` exposes `identities`, `knowledge-resolve` and the
two ID options before using them, then read the catalog's capability. Older
runtimes remain usable by name but cannot promise identity-bound adoption;
do not downgrade an ID request or replay an effectful operation by basename.
No automatic caller migration or retirement is implied by a source release.

### Accepted default in Configuration

An optional **machine-local** `knowledge_preferences` selection in the project's
`config.yaml` (or its machine-global default) supplies `state_dir` and optionally
`local_overrides`. Both are explicit absolute native paths. The state directory
is the existing unsynchronized Machines preference state, not the carrier or a
knowledge checkout. The override is the existing local preference JSON document.
Repository, knowledge and synchronized preferences cannot select these paths.
No command here creates this local selection or enrolls a carrier.

Without this setting, the catalog never resolves or invokes Machines. With it,
Worktrees derives the canonical harness ID and logical execution machine using
its native owners, selects its own installation's Machines peer and checks the
accepted-repository descriptor in that exact runtime before calling the
read-only projection. Application-readiness support is not a prerequisite.
An absent/old/refused owner leaves the optional default visibly unavailable,
without disabling existing Configuration rows or the by-name binding flow.

For an unbound harness, the matching registered knowledge row offers **Bind the
accepted knowledge default** only when native knowledge readiness is false;
already-ready/self-hosted repositories retain the existing action gate.
The form still requires the existing explicit
preview and confirmation. It uses the ID-bound transaction above with
`--accepted-default` on both `knowledge-preview` and `knowledge-apply`.
The selected preview projects the binding owner's exact version-1 Configuration
envelope with the combined token, so the existing Manager decoder and
Cancel-default confirmation remain unchanged. The richer portable-resolution
response is not passed to that strict UI decoder.
The token additionally binds the configured source, selected runtime and
Machines' scoped input revision. Apply rechecks them, including inside the
existing binding pointer-write precondition. The final portable-scope comparison
runs after the potentially blocking Machines read, so an identity change during
that read cannot commit a previous machine's preference. The machine-local source
selection is also reread after that child returns. A changed snapshot, policy,
override, scope, source or runtime requires a fresh preview.
Accepted-default preview and apply additionally require the selected binding
owner's integer `binding_precondition_freshness: 1` capability. Its preserving
writer compares the captured configuration bytes after the callback and again
before replacement, refusing unrelated external edits without losing them or
reporting a pointer commit. Older owners leave this optional action unsupported;
existing explicit portable-ID and by-name flows retain their capability contract.
Accepted-default confirmation shares one aggregate caller-owned deadline across
preference observations, capability discovery and native preview/apply: 18 seconds
for preview and 26 seconds for apply, including cleanup. Each preference read
retains its eight-second local cap within that aggregate. Sequential successful
reads cannot renew the operation beyond Manager's existing 20/30-second limits;
the pointer-bound worker remains inside the same caller-owned execution window.

Existing effective local bindings win; unresolved explicit bindings require
owner repair. Absent, withdrawn or locally suppressed defaults invoke no binder.
Missing/ambiguous native registration cannot fall back to a name or clone.
The portable preference remains distinct from effective binding, pairing,
plugin delivery, credentials and activation.

The new form is gated by `preferred_knowledge_bindable` from the capable
Worktrees catalog; an older catalog omits it and keeps its existing actions.
Machines support is provider-first metadata plus conditional consumption, not
a package-version assumption. A provider rollback makes only this opt-in feature
unavailable; it never replays binding by name.

## Validation

Native coverage runs through `tools/run-plugin-tests.py agent-worktrees -k
"repos_configuration or repos_registry_transaction"`. Cross-distribution UI
coverage uses the production Textual editor and the actual native owner CLI,
not a substitute owner implementation. Set `REPOSITORIES_CONFIG_TEST_PYTHON`
to the isolated agent-worktrees test interpreter before running the Manager's
`test_repositories_configuration.py`. Manager validates executable targets but
preserves their absolute invocation paths, so symlinked virtual-environment
interpreters retain the native owner's environment. The change-scoped Linux CI job prepares
that interpreter and enables the same lane in a separate, required five-minute
Repositories Configuration job; a normal standalone Manager suite
without a separately prepared owner reports the integration case as skipped.

These checks use redirected roots and sentinel secrets, compare complete source
bytes/directory trees, and exercise cancel, set, no-field reset, changed
inheritance, ordinary concurrent registry writes and stale/invalid requests.
They do not establish promoted/installed GUI acceptance or authorize rollout.

Portable-ID controls live in `tests/test_knowledge_preferences.py`. The opt-in
`tests/native/test_portable_knowledge_binding.py` uses actual fresh Git roots,
canonical registrations and the real selected `harness-knowledge` payload to
prove preview -> confirmed pointer -> registration/state-root readback, within
the native operation budgets, and refuse a global-machine change after the
portable check but before the locked worker. Its example non-GitHub origins require no account
credentials or network. This is isolated source execution, not installed-runtime,
cloud-carrier or cross-platform deployment qualification.

`tests/test_knowledge_default.py` covers optional-provider degradation,
confirmation, changed inputs and pointer-boundary refusal. The native binding
packet additionally uses the real Machines CLI with an explicitly
fixture-selected **non-editably** prepared interpreter
(`KNOWLEDGE_PREFERENCE_TEST_PYTHON`). Ordinary `--prepare-only` creates an
editable Machines environment and does not qualify the immutable-slot
descriptor prerequisite. Prepare the non-editable artifact through the
repository's normal seasoned/retained source installer; never copy a descriptor
into an editable environment or disable the support check. Without the explicit
artifact selector these two integration cases skip, not pass.
It exercises the actual catalog, Configuration parser and selected binding
payload through pointer/readback, consumes the preview with Manager's actual
strict decoder, and refuses changed accepted bytes at the
pointer precondition. This is not qualification of installed peer discovery or
productive Manager/carrier use.
