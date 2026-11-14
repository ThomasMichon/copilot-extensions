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

## Validation and relationships

The focused source packet is
`tests/test_retired_owner_finalization.py` through the contained plugin runner.
It uses synthetic receiving ledgers and real local Git worktrees. It exercises
default refusal, explicit landed removal, branch drift, dirty/unlanded source,
unknown/live ownership, changed incarnations, malformed or conflicting claims,
command-local admission and cleanup exclusion.

This closes the inspectable, owner-scoped reclamation gap without changing the
[continuous reclamation](../../../visions/plugins/agent-worktrees/continuous-reclamation/README.md)
rule that collection cannot silently attest legacy ownership or release another
actor's responsibility.

See [execution identity](execution-identity.md) for ordinary authority and
the [CLI reference](cli-reference.md) for finalization and other lifecycle verbs.
