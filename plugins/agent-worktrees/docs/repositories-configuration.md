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

## The one local edit

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
