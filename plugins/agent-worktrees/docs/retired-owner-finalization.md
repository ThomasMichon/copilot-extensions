# Finalizing a legacy child after its parent is retired

Ordinary mutation of an owned local checkout requires its original receiving
allocation or a proven retained local parent. A clean, landed checkout alone
does not establish that authority. Legacy children can predate receiving
allocation journals and outlive their parent's checkout.

## Explicit recovery

After obtaining the normal authorization to finalize and remove the selected
checkout, query the **selected executable**, not its payload's package version:

```text
<agent-worktrees> get finalize-recovery
```

Schema `agent-worktrees.finalize-recovery`, version 1 advertises
`retired_owner.option: "--recover-retired-owner"` with scope `finalize`.
An older runtime's unknown key or argument is unsupported, not permission to
remove the argument, change identity or edit a ledger.

```text
<agent-worktrees> finalize <child-id> --recover-retired-owner <original-owner-ref> --dry-run --json
<agent-worktrees> finalize <child-id> --recover-retired-owner <original-owner-ref> --json
```

The owner must be the child's exact original qualified **worktree** owner,
without a session suffix. This narrow recovery supports an ordinary unpaired
legacy child whose original root parent is archived and absent. It requires
the original receiving ledgers, registered project configurations, reciprocal
settled ordinary child claim, actual local linked Git checkout and positively
inactive original ownership. Missing, foreign, malformed, ambiguous, live or
unknown evidence refuses recovery. Producer-backed and handoff-reserved claims,
paired relationships and recursively owned retired parents are not supported.

Records need not have creation nonces if they predate that protocol. Admission
retains their existing receiving subject fence, including the original start
incarnation, and any nonce that exists. Each authority check revalidates the
same child, parent and claim; it does not manufacture a receiving receipt.
The parent's existing record exclusion also spans finalization's bounded
cleanup section, preventing claim reactivation between fresh proof and removal.
Ordinary record locks are not held across the earlier fetch/provider work.
Revalidation still applies if ordinary custody becomes available meanwhile.
Any additional entry for the original child claim reference refuses admission,
including equivalent canonical encodings, producer-backed or opaque entries.
Inactivity is resolved from the already-qualified local parent, not by
reinterpreting its historical host label. Strict native mux/binding observation
must confirm inactivity; an archived status or absent directory alone does not.

Recovery authority exists only for this invocation. Original ownership, parent
history and settled claim disposition remain intact. This is not a claim
handoff, takeover, generic mutation exception or invoker-authorization protocol.
Session-claim settlement uses the existing locked handler in the admitted
process; no recovery authority is sent to a resident daemon.
The original parent claim is already settled, so recovery does not attempt
another parent settlement after removing the child.
It cannot be combined with abandonment or open-PR overrides. Existing content,
publication, effort, downstream responsibility, session and checkout-preservation
checks remain independent. Finalization checks the actual checked-out branch,
including a legitimate branch switch since the historical record was created.

`--dry-run` inspects admission and previews normal finalization without changing
the records or checkout. Normal success/exit and JSON shapes are unchanged;
a recovery refusal is an error, not a successful removal. If finalization
retains the directory for an independent reason, resolve that reason and retry
the same explicit finalization operation. Do not infer physical removal merely
from the finalized status.

If custody changes after finalization freezes new claims, recovery refuses
cleanup and can retain the existing `finalizing` freeze. It cannot roll that
state back by writing without current authority. Once the actual owner restores
the required original custody, repeat explicit finalization; the freeze does not
prevent that retry. Never edit the ledger or reactivate a claim to manufacture
the missing proof.

## Permanently gone parent

Retired-owner recovery needs the parent's archived record. When the parent
has no live or archived record, no checkout and no branch, plain finalization
refuses ("requires its original receiving allocation or proven local
parent"). Use the separate doctor repair:

```text
<agent-worktrees> -p <child-project> doctor --repair-orphaned-owner <child-id> --json
<agent-worktrees> -p <child-project> doctor --repair-orphaned-owner <child-id> --fix --json
```

Without `--fix`, the repair only reports its evidence. It requires an
unpaired, local child with no receiving allocation metadata and an exact
qualified worktree owner (no session suffix). The owner's machine label must
name the current execution space; a historical alias label (such as a legacy
WSL record naming the physical host) is accepted only when the shared legacy
record-locality resolver maps that label, in the child's own receiving
namespace, back to the child's record. Failing that, an owner label is
accepted when it equals the child record's own serialized `machine` label,
the record's platform matches the actual platform, the child is a proven local
checkout, and the record carries no explicit foreign execution identity: one
record's labels were written by one reporter in one vocabulary, and the
child's own label is already proven to name the current execution space. An
owner label that differs from the child's label still refuses. The owner's project must resolve to a
local configuration. The repair refuses if any of these is present:

- a record, backup, other `<parent-id>.*` file, or tracking YAML naming the
  parent ID
- a `<parent-id>.lock` that is non-empty, a symlink, not a regular file, or
  currently held (probed without blocking through the record lock itself)
- a `<parent-id>/` sidecar that is a symlink, contains any file, symlink or
  other non-directory entry at any depth, or has a subtree that cannot be
  enumerated
- the parent checkout path, or a path symlink
- the `worktree/<parent-id>` branch
- a Git worktree registration for the parent
- a live or unobservable parent terminal session
- a child `parent_session` or active controller session that session liveness
  does not report as `dead` (a live or unknown state refuses)
- an active controller relation that does not name the missing owner itself
  (its machine, project and worktree ID), including a session-only relation,
  or controller metadata the record loader marked opaque; ended relations are
  ignored

Missing or unreadable evidence also refuses; it never counts as proof of
absence.

Receiving custody is classified without the parent's ledger, which the repair
has just proven absent. A creation-journal row recording a receiving
allocation for the child refuses. A child with a creation nonce (including a
backfilled legacy nonce) and no journal, or only an ordinary journal row, is
accepted. Where qualified native locking is unavailable, the journal is read
as an immutable snapshot that must have no SQLite sidecar files and the same
identity, size and modification time before and after the read.

Ordinary record removal leaves the sidecar lock behind, and handoff sidecars
can outlive their record as empty directory trees. An unheld empty lock and a
sidecar made only of empty directories therefore do not refuse. They are listed
under `residual_parent_artifacts` in the evidence, and the repair never removes
them.

The child must have no unfinished checkout relocation: an active or invalid
relocation journal refuses both the report and `--fix`.

`--fix` takes the publication-authority guard, then the child's record lock
and the parent ID's record lock in a fixed path order, and proves everything
again while holding both. Record creation takes the same parent-ID lock, so a
parent record cannot appear between that final proof and the write. Taking the
parent-ID lock can leave an empty lock file behind, the same residue as
ordinary record removal. It removes only the `owner_ref` line, and
verifies that no other field changed. Next to the record it writes the exact
original bytes to a private `<id>.yaml.orphaned-owner-<digest>.bak`, plus a
`.json` audit file containing the evidence and a UTC timestamp. A retry after
an interrupted repair reuses an existing audit file only when its version,
action, worktree ID and backup name match; the timestamp may differ. The repair
never touches the checkout, branch, other records or session state, and never
creates a parent record. Afterwards, the normal finalization gates apply to
the unowned child, including the content, publication and session checks. The
repair cannot be combined with other doctor passes.

Finalization does not accept such a child directly. Keeping the gate strict
means one explicit, inspectable and audited step proves the parent is gone,
instead of every finalize attempt inferring it.

## Validation and relationships

The focused source packet is
`tests/test_retired_owner_finalization.py` through the contained plugin runner.
It uses synthetic receiving ledgers and real local Git worktrees. It exercises
default refusal, explicit landed removal, branch drift, dirty/unlanded source,
unknown/live ownership, changed incarnations, malformed or conflicting claims,
command-local admission and cleanup exclusion. The same file covers the
orphaned-owner repair's report, fix, follow-up finalization and refusals.

This closes the inspectable, owner-scoped reclamation gap without changing the
[continuous reclamation](../../../visions/plugins/agent-worktrees/continuous-reclamation/README.md)
rule that collection cannot silently attest legacy ownership or release another
actor's responsibility.

See [execution identity](execution-identity.md) for ordinary authority and
the [CLI reference](cli-reference.md) for finalization and other lifecycle verbs.
