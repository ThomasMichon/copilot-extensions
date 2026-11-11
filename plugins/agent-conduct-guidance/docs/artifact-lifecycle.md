# Agent-created artifacts: placement and closeout

Entry point: [managing artifacts](../skills/managing-artifacts/SKILL.md).
This is the generic agent procedure, not a new storage service or claims API.

## Classify before writing

| Class | Default home | Responsibility |
|-------|--------------|----------------|
| Disposable scratch | Private, worktree/task-stamped allocation under the resolved scratch root | Consume and clean as work progresses; otherwise record an explicit disposition |
| Execution history | Owner-resolved worktree-data home, or actual session state for session-specific records | Preserve agendas, execution plans and journals independently of a checkout |
| Durable knowledge | Associated files in a canonical effort in the user's knowledge repository | Publish text and bounded graphics through that repository's policy |
| User deliverable | Segmented user-managed OneDrive or Downloads destination | Verify delivery, close the producing obligation and retain a history receipt |
| Reusable cache | The producing tool's declared cache home | Follow its consumer, retention and eviction contract rather than treating it as task scratch |

Reference prototype source can accompany an effort; a usable report, mockup or
presentation belongs in user-managed storage. An ongoing source project belongs
in a registered repository/worktree, never an embedded checkout under another
worktree, session state or scratch. Large archives, datasets, executables,
dependency trees and build products do not belong in knowledge Git. Bounded-size
PNGs and small charts are a deliberate exception; honor configured size limits
and ask before adding unusually large content when no limit is established.

Repository-defined build outputs and test fixtures keep their declared locations
and producer lifecycle. That exception does not make the source checkout an ad hoc
staging area or a durable agenda store. Untrusted inputs, credentials and sensitive
material retain their independent handling rules regardless of storage class.

## Resolve preferences, then paths

Honor explicit destinations and the existing owners of knowledge preferences,
machine configuration and user storage. Separate portable organization/retention
choices from physical roots on a particular machine. Resolve through the owning
configuration contract; do not invent configuration keys or add storage fields
to another capability's allowlist.

Where supported by that owner, prefer the operation's explicit destination over
applicable per-machine preferences, portable knowledge preferences and capability
defaults. An explicit process-level scratch-root override is a destination choice,
not proof that its permissions are safe. Unsupported or unresolved explicit
configuration is a visible boundary, not permission to guess another account,
drive, source directory or session-state scratch fallback.

The scratch procedure owns privacy, exclusive task allocation, breadcrumbs and
short-path requirements. Use per-child temporary environment settings where
supported; do not change global temp settings or tool-managed cache policy.

Default user-deliverable organization, when the destination owner has no override:

```text
<resolved OneDrive>/Apps/Copilot/Artifacts/<YYYY-MM-DD> <Topic>/
<resolved Downloads>/Copilot/Artifacts/<YYYY-MM-DD> <Topic>/
```

Resolve the actual account/root rather than assuming an environment variable
selects the intended OneDrive. Keep agent-created output segmented by default;
sanitize topic components and allocate collision-safe folders. A user-specified
location wins and the user may move delivered content afterward. Do not silently
substitute Downloads for an explicitly required OneDrive destination.
Changing defaults neither migrates old files nor grants permission to do so.

## Register custody without growing a destination ledger

Use the existing allocation/resource owner when available. Register a coherent
task directory or artifact set, not every intermediate file. Preserve exact owner,
worktree incarnation, purpose, location and retention evidence. The target
contract is registration before an allocator returns a writable path.

**Current capability limit:** existing Worktrees `derived` reclaim admits only
checkout-contained paths. A scratch breadcrumb is not an authoritative claim,
and this guide does not make external or arbitrary absolute paths reclaimable.
Use a supported owner-qualified resource claim to record the obligation where
available; if allocation and claim cannot be composed, report that missing seam,
retain the exact ownership/disposition in the existing work record, and do not
claim runtime enforcement or deletion support. Never move scratch into a checkout
merely to satisfy an older claim API.

Before logical closeout, give unfinished artifacts one truthful disposition:
deleted with verified outcome; published/promoted to an accepted knowledge/source
destination; verified user delivery; explicitly accepted transfer of unfinished
work; or retained under a named history/cache owner and applicable retention policy.
A generic release with no disposition must not conceal unfinished responsibility.
Retained bytes alone do not mean work remains unsettled.

For completed user delivery, keep a bounded historical receipt identifying the
destination, time and result. Close the producing obligation. Do not create a
permanent "artifacts now in OneDrive" ledger, monitor user moves, or require a new
recipient custody handshake for an ordinary completed deliverable.
Unknown/failed delivery retains responsibility; a copy attempt is not delivery.

## Finalize work; reclaim separately

Finalization validates completion and disposition, not deletion. Finalized storage
may remain for reuse or retention. A separate explicit cleanup pass can be initiated
by an agent or the user and may remove finalized allocations no longer needed
when applicable scope, ownership and retention policy permit it. Do not invent a
fresh consent ceremony for every owned disposable item already covered by policy;
equally, neither a finalized label nor a merged PR grants blanket legacy deletion.

Use the existing resource owner's cleanup flow, preview exact scope and revalidate
identity, current use, retained history, source/provider state and authorization
at the effect boundary. Report retained, removed, partial and unknown outcomes
distinctly. No blind recursive sweep, process killing or forced claim release.

Checkout retirement, history collection and persistent-history reclamation are
independent. Keep worktree history outside source checkouts and use domain-owned
consistent export/readback for collection. Logger is an archivist, not another
agenda writer; export availability is not installed adoption or deletion authority.
Do not delete legacy checkout-local history merely because the new default is safer.

**Compatibility limit:** some installed finalization flows also attempt physical
checkout removal. Read the selected owner's actual contract and preview before
acting; if a no-deletion finalization is required but unsupported, preserve the
checkout and report the unsupported separation. Do not invent a retain flag,
hand-edit terminal state or invoke a removing operation without applicable scope.

## Ignored files and nested worktrees

Git ignore rules classify version-control visibility, not value or disposability.
Known scratch follows its allocation; declared build output follows its producer;
misplaced history needs owner-led preservation; unknown legacy files need review.
Do not automatically archive every ignored file or discard it because it is old.

For multi-repository work, keep child resources with their actual repository
owners. Resolve open work before parent closeout. During an explicit disk-cleanup
pass, also inspect finalized-retained children: an active-obligation query may
intentionally omit settled edges and is not a complete disk inventory.
Never infer absent children from an empty active ledger, bypass another repo's
publication gates, or report physical removal from logical finalization alone.

## Implementation boundary

This payload provides guidance only. It adds no allocator, runtime claim schema,
retained-resource scanner, logger collection switch, migration or deletion command.
Existing plugin capabilities remain optional; missing integration must not become
a mandatory installation or a second claims database.
