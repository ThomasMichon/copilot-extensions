# Worktree identity codec

`agent-worktree-identity` provides the dependency-free `worktree_identity`
module for reading raw worktree JSON metadata. Importing it performs no
configuration, transport discovery or filesystem access.

`parse_machine_identity` decodes explicit version-1 records into
`WorktreeMachineIdentity`. Physical `machine`, canonical `environment`
(`windows`, `wsl`, or `linux`) and the separate `execution_machine` owner
are required; `platform` must agree with the environment. Malformed explicit
metadata raises `WorktreeMachineIdentityError`, never a legacy fallback.
Without `machine_identity_version`, parsing returns `None` without upgrading
or inferring historical metadata.

`execution_machine_from_fields` selects the explicit execution owner, or the
historical string `machine` when no version is present.
`projected_machine_fields` preserves the corresponding tuple or historical
machine/platform representation. These are metadata operations, not proof
of local authority.

Worktrees retains `agent_worktrees.worktree_machine_identity` as a compatibility
import of these same types and functions. Development consumers declare
canonical editable references; release materialization embeds the library
into each standalone consumer. No sibling plugin installation is required.
