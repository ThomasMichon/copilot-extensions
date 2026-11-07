# Relocation owner and compatibility design

Part of [Inactive Worktree Relocation](README.md). These are proposed source
requirements, not a claim of implemented runtime support.

## First complete flow and widening

Start with an ordinary managed linked checkout whose source contribution is
merged/final and whose owners prove it is stopped and clean. It must still be
an existing checkout, not a removed/finalized directory reconstructed from Git.
Preserve all actual content, including ignored files: "clean" Git status does
not prove that no valuable ignored data exists.

Complete plan → admission → transfer → Git repair → rebinding → consumer
verification → safe source retirement before calling the first flow usable.
Publish and qualify that vertical flow early, with explicit refusals for
unsupported retained-session/pair/WIP shapes. Separately authorized adoption
starts with older completed worktrees; its feedback precedes widening.
The initial restriction is staged delivery, not a permanent exclusion of WIP.

## Existing seams and mixed-root authority

Current source has a retained `WorktreeRecord.worktree_path`; lookup and launch
already use actual Git/record paths in important paths. New allocation derives
from configured `worktree_root`. Do not replace those working resolutions with
a guessed current-root path.

Specific assumptions needing reconciliation:

| Seam | Present assumption | Required outcome |
|------|--------------------|------------------|
| `legacy_wsl_record_locality.record_namespace_matches` and `execution_spaces.require_record_mutation` | Managed tuple/some legacy mutation requires checkout at current allocation root/ID | Validate the actual receiving project, original identity, execution space, owned ledger and Git family at the admitted retained location |
| `pr_source_publication._caller` | Derives caller from current root/ID | Retained relocated callers can publish under the same independent authority and Git-family checks |
| `worktree_creation` pairing | Derives companion root from anchor before loading companion configuration | Load each receiving repository's effective allocation policy; existing pair records keep their own paths |
| Creation-tail/`creation_repair_identity` and allocation receipts | Retained allocation evidence also carries original namespace/path facts | Recover original pending intent and evolve authority through its owner without rewriting immutable receipts or silently reallocating |

Changing defaults affects only new allocations. Coexisting retained roots must
not become a machine-wide allowlist that makes arbitrary paths authoritative.
Path compatibility cannot conflate physical hosts, native/guest execution
spaces, logical projects or redirected ledgers. Adopted external Git worktrees
keep their existing independent contract. Moving an anchor/shared Git directory
is a different operation and is not included here.

## Native operation and custody

**Agent-recommended interface:** an existing-owner CLI command such as
`move <id> --to <path>` with a genuinely read-only plan mode and structured
outcomes. Final spelling follows Worktrees conventions. Declare support and
supported cases against the selected executable/runtime; an old runtime has
no inferred move support. Expand providers first, then migrate capable callers;
do not add a per-session daemon, probe an effectful command then replay, or
silently downgrade a required preservation guarantee.

Inactivity is an owner-qualified condition, not an idle AI label, age,
merged PR, missing pane or stale PID. Fence launch/resume and relevant record
mutations for the operation; check relevant live sessions, process CWD,
reservations, pending lifecycle work and opaque execution legs through their
owners. Revalidate under the same admission boundary before effects.
Unknown/unavailable evidence refuses and identifies the owner/recovery action.
No killing processes, clearing claims, inventing finalization or adopting an
opaque provider to make a move pass.

Retain worktree/session IDs, nonce and owner representations, branch/HEAD,
index/staging, relationships/pairs, claims, PR association, head/succession and
lineage. A location change is not a new allocation, head transfer, claim
settlement or completion assertion. Existing retirement/finalization and
source-publication contracts remain independently enforced.

## Transfer and failure semantics

Use the native Git worktree owner for Git administration, with supported
repair where cross-volume staging requires it. Cross-volume is a staged
transfer, not an assumed atomic rename. Journal the original identity,
source/destination and owner revisions; reserve destination custody, copy
without following unrelated link targets, verify content/metadata, repair Git,
rebind supported operational pointers and prove the resulting consumer
before removing the original copy. No global pointer-swap atomicity is claimed.

Every phase needs a recoverable boundary. Retry/status identifies the original
operation and reconciles observed effects; it never creates another checkout
or restarts a move from a guessed state. Before publication, rollback restores
the original usable state where safe; after publication, recovery must first
determine the authoritative copy and any writes before rollback or cleanup.
Ambiguous outcomes preserve both data and unsettled custody with an actionable
recovery state. An extra copy is not authority to delete the original.

Preflight destination emptiness/collision, containment, available space,
permissions and source stability. Define and test symlink, junction/reparse
point, hardlink, sparse/alternate-stream, executable-bit and ACL handling for
each supported platform/filesystem. Never traverse unrelated targets or
broaden access; unsupported lossless semantics refuse before destructive work.
Do not silently remove Git worktree locks or bypass submodule restrictions.

## Operational pointer ownership

| Domain | Required treatment |
|--------|--------------------|
| Worktrees tracking, controller/pair/claim and allocation journals | Existing typed owners update operational locators while preserving stable identity and original intent; immutable historical receipts remain historical |
| Copilot workspace/session metadata | Qualify actual supported reader/writer/schema and owner locking; update only current operational fields belonging to the moved workspace |
| Session stores and history | Inventory `workspace.yaml` CWD/Git root and SQLite `sessions.cwd` / `session_files.file_path`; qualify whether these are operational or historical for each supported client version |
| Event and proposal snapshots | Qualify `session.start` context (`cwd`/`gitRoot`), `forge_skill_proposals.git_root_path` and `workspace_before_json` against real readers; historical snapshots are not automatically mutable pointers |
| Trust, permissions, backend/Driver/Bridge, launch seed | Use supported owner APIs and preserve policy/custody; opaque or unknown participation is an explicit unsupported case |
| Guidance, index and session projections | Refresh/invalidate through the producing owner from authoritative locators; no global rewrite of generated copies |
| Venvs, build artifacts and source-bound test caches | Classify absolute-path dependence; preserve data and rebuild/rebind only through the owning tool with explicit scope, never string substitution or silent ignored-data discard |

Prefer a supported writer/API. If a necessary client seam has none, narrowly
qualify a known schema and its exclusive-writer/transaction behavior or refuse
it. Never replace a whole live session database, globally rewrite transcripts,
change conversation IDs, or touch unrelated live rows. Copying an old absolute
path into a new field does not prove that the real resume reader uses it.

The implementation must publish a bounded owner/pointer inventory and a
support/refusal classification, not a promise to discover every arbitrary
application reference on the machine. Preserve independently evolving Driver,
Bridge, allocation, claims, installer and backend contracts.

## Admission decisions still needing implementation qualification

- Which ordinary retained records can be proven without new location metadata,
  and which need an owner-written location transition?
- Which client versions and resume semantics support a bounded current-pointer
  update while retaining historical evidence?
- Which native filesystem/link/ACL shapes are losslessly transferable, and which
  require a truthful pre-effect refusal?
- How does each pending-creation/pair owner retain original immutable intent
  across a mutable-location transition?

Each decision has an explicit consumer/failure obligation in
[validation](validation.md). These questions do not authorize a live probe,
schema guess or deployment.
