# Retired machine-record label repair

Canonical worktree records no longer persist machine or environment naming.
Current reporting identity is derived from the observed host/environment and
current configuration; a rename needs no record normalization or migration.
See [reporter naming and tracking data](execution-identity.md#reporter-naming-and-tracking-data).

The legacy selector remains accepted for compatibility:

```text
agent-worktrees -p example doctor --repair-machine-record old-host-win-history --from-machine old-host --to-machine canonical-host --json
```

Both report and `--fix` return a read-only retirement notice. JSON retains
`version: 1`, `scope: "machine-record"`, `repairable: false`, `repaired: false`
and `resident_projection: "not-verified"`, with `retired: true` and an explanatory
`reason`. Exit zero means the compatibility notice succeeded, not that any
record exists or was repaired. An exact ID, nonempty `--from-machine` and
selected project remain required; combined repair passes still refuse.
The optional `--to-machine` value is accepted but does not select a machine,
authorize a record or change naming.

The selector does not read or parse the record, inspect its checkout, acquire a
record/publication lock, create a backup/audit, update a roster or invoke another
doctor pass. Existing, absent, malformed and historical records are untouched.
No replacement migration or bulk-normalization command is introduced.

## Historical state and ordinary writes

Previously written `.machine-label-<digest>.json` receipts and original-byte
backups remain historical operation evidence. This retired selector neither
rewrites nor removes them, including incomplete receipts. It does not claim
that a prior interrupted operation completed.

Ordinary canonical record writes remove legacy naming keys while retaining
independent receiving-subject, incarnation and operation checks. Qualified
owner/pair/controller/resource locators remain original relationship evidence,
not a reason to restamp a record. Orphan-obligation readers likewise ignore
legacy naming fields; an actual ordinary orphan write omits them while
preserving original references, handoff targets and unknown fields. A read or
idempotent no-change rehome performs no migration.
The cleanup consumer independently proves receiving-source custody before
reclamation; stripping a legacy label never makes an obligation local or
disposable. Foreign, missing, live, transferred or ambiguous source evidence
retains the entry. Apply serializes fresh proof, bounded reclamation and orphan
settlement against source mutations; preview remains read-only. See
[resource obligations and custody](architecture.md).

Independent [retained local authority](execution-identity.md#retained-local-checkout-authority)
and normal integrity/ownership checks still govern real mutations. A naming
notice does not authorize cleanup, a transfer, misplaced-record repair, a
runtime update or a service restart.
