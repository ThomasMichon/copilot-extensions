# Native machine-record label repair

`doctor --repair-machine-record` normalizes one proven local native record's
historical machine label to the receiving configuration's local identity. It is
not a transfer, a record-ID rename or an automatic alias migration. Ordinary
`doctor --fix` never performs it.

Use the attributable command from the session catalog and select the receiving
project, exact record ID and exact previous label:

```text
agent-worktrees -p example doctor --repair-machine-record old-host-win-history --from-machine old-host --to-machine canonical-host --json
```

`--to-machine` is an optional exact assertion of the configured target, not a
selector for another machine. Existing invocations without that assertion remain
supported. Review `repairable`, `from_machine`, `to_machine` and `proof`; append
`--fix` only after accepting that exact report. There is no competing migration
command or wildcard/bulk default.
Preview validates the exact byte-preserving scalar replacement too: unsupported
multiline machine scalars refuse in both modes without a backup or audit write.

## Proof and historical records

The configured identity, previous label and observed hostname must resolve to
one native execution space. Windows and Linux are supported; WSL and native/guest
crossings remain refused. An ambiguous roster or another machine's label cannot
authorize repair. Without a roster, only the actual observed hostname is an
eligible previous label.

**Existing checkout:** retain the linked-Git proof. The checkout must have a
`.git` file, no path indirection, the expected Git top-level, and the same Git
common directory as the selected repository. An existing non-Git directory or a
checkout from another repository never falls back to historical proof.

**Removed checkout:** require stronger independent registration evidence:

- the configured target is an explicit canonical native execution-space key;
- the receiving repository has canonical worktree/knowledge registration, even
  when a knowledge-only repository has no adopted-project entry;
- its registered native root matches the configured repository anchor, which
  remains a readable Git repository;
- record ID/project/platform match the receiving store and actual platform;
- the absent absolute checkout equals the declared worktree namespace plus the
  unchanged historical ID, with no redirected descendant or symlink checkout.

JSON `proof` is `existing-linked-git` or `removed-registered-namespace`. A
misplaced record whose `repo` differs from the selected receiving project still
refuses. Label repair never relocates, deletes or deduplicates records; targeted
misplaced-record integrity handling is separately tracked in #7097.
Historical refusal messages distinguish absolute-path, existence, namespace-root,
lexical membership and redirected-descendant failures. The physical check
resolves the expected root but intentionally not its final ID component:
resolving both full paths would hide a dangling directory redirect.

## Preserve relationships, do not transfer them

Only the `machine` scalar changes. IDs, historical name prefixes, paths, branches,
sessions, head, lifecycle, PRs, flags, unknown fields, comments and line endings
remain byte-preserved.

Owner/pair/controller/resource references, reservations and follow-ups are not
rewritten or settled. Valid old qualified references remain same-machine aliases:
receiving-record resolution identifies the same project/ID/platform/namespace
before and after normalization. Known-local linked records are not refused merely
because they are linked. Existing foreign or missing-parent relationships remain
foreign or unresolved; this operation neither grants authority nor claims to fix
their independent integrity defects.

## Locking, backup and audit

Apply takes the existing publication authority, reloads identity configuration
without unrelated PR-policy discovery, and repeats native/Git or historical
proof before acquiring the short selected-record lock. The lock covers only
exact-byte recheck, receipt/backup and atomic replacement: no Git or network I/O
runs while it is held. Changed material identity/project/anchor/namespace proof
refuses; incidental preferences do not. Native path keys retain Windows case
equivalence and POSIX case sensitivity. No graph-wide record-lock set is acquired.

The existing private original-byte `.repair-<digest>.bak` backup and atomic
replacement writer are reused. A `.machine-label-<digest>.json` receipt beside
the record retains exact old/new labels, before/after checksums, receiving
namespace evidence, proof type and backup name. It is `prepared` before the
record replacement, with no claimed backup yet, and `committed` afterward using
the actual backup returned by the shared writer. A final receipt failure returns
exit 1 with `repaired: true`, the backup and audit names, and an explicit error:
the already-committed record write is not reported as a harmless refusal.
An incomplete receipt requires inspection, not a blind retry.

Successful repeated apply is a no-op. Current-code full-record writers reject a
stale snapshot that would restore the old machine label. Source-only invocation
does not upgrade older writers or patch a resident daemon.

## Record truth versus derived roster

Repair reports the exact record transaction, not a resident/list projection
refresh. A cached roster may temporarily show an older label despite canonical
record bytes. Verify the exact public `record` API separately and use only
supported resident refresh/reconciliation when required. Do not infer byte
reversion from a listing, hand-edit caches, patch installed code, or start/restart
services as an implicit part of this record-local command.

The operator's all-worktrees acceptance gate includes actual record and roster
verification plus any separately tracked misplaced records; a feature merge or
one repaired record does not satisfy that containing gate.
