# Recovery facts: isolated G1 primitives

`recovery_facts.py` and `recovery_markers.py` are disconnected qualification
primitives. No CLI, session hook, record transaction, installer or service calls
them. They do not backfill a live marker, append a production journal, activate
a writer, delete state or rebuild a projection.

The approved placement contract is a small gitignored
`.agent-worktrees-recovery/identity.json` in each actual worktree, and
`agent-worktrees-recovery/` beneath its independently resolved Git common
directory for the single durable semantic journal. A marker contains only
repository/resource correlation, not current writer authority or a copy of
semantic state. Public resource correlation must not reveal private paths or
identities.

`inspect_layout` is read-only and takes explicit Git roots, identities and the
caller's Git-owner observation that the marker folder is ignored. It does not
claim those supplied roots are Git-authoritative or the marker proves ownership.
Existing unmarked directories/files, conflicting identity, links/reparse points
and unsupported required marker capabilities refuse only this inspection.
Unknown optional marker fields and other files are never rewritten or deleted.
An absent directory is a proposed location, not permission to create it.

Paths are locators, not identities. Moving a worktree retains its resource ID
and resolves the Git common directory again. A backup must include the common
journal and the actual worktree marker/user work together; a worktree-only copy
cannot recover missing semantic facts. Restored copies are evidence candidates,
never two active writers. G1 deliberately has no publication/election API.
Native Git move/repair and binding checks remain the existing owner's job.

## Envelope and reader contract

Facts use schema `agent-worktrees.recovery-fact`, envelope version 1, canonical
UUID repository/resource/operation/incarnation identifiers, positive revision,
semantic `kind`, payload and per-operation `required_capabilities`.
Incarnation and revision in a decoded fact are evidence only. Runtime authority
and CAS must be checked separately at any future effect.

`decode_fact` rejects duplicate keys, invalid JSON/nonfinite numbers, unsupported
envelope versions and malformed required fields, within 64 KiB / depth 32.
Both input bytes and canonical ASCII-escaped output must fit the byte limit;
Unicode expansion cannot expose a candidate that fails unchanged serialization.
Unknown optional JSON fields survive encoding and candidate edits recursively.
Decimal tokens whose value would change on float serialization (including
underflow or excess precision) return `number_precision_unsupported` before a
candidate is exposed. This is a scoped representation limit, not ownership
revocation; original bytes remain untouched. Supported decimal tokens are
checked against exact decimal value before acceptance.
`patch_payload` creates a separate in-memory candidate for explicitly named
scalar leaves; it neither increments revision nor persists or acknowledges it.
It refuses subtree/array replacement that could erase unknown metadata.

Readers explicitly supply supported semantic capabilities. Missing `read`
requirements refuse parsing as usable semantic state; missing `patch`
requirements still allow supported reading but refuse candidate edits. No
package-version comparator or universal producer-age floor exists.
Reason codes in `RecoveryError.reason` distinguish malformed/unsupported
records, unknown patch fields, source unavailability and path/identity conflicts;
none means foreign owner, empty obligations or authorization to clean up.

#1460 owns Bridge compatibility; #1915 was consolidated into its still-open
durable/integration evidence subsection. That inventory remains deferred and
does **not** establish a supported durable rollback-runtime floor. G1's synthetic
capability fixtures prove this envelope's reader behavior, not compatibility of
an actual historical Bridge or Worktrees binary. Selecting/qualifying such a
floor is still an admission dependency before any live writer.

## Evidence boundary

`tests/test_recovery_facts.py` exercises lossless candidate edits, scoped schema
refusals, foreign-path preservation, bounded exact-marker I/O, moved/restored
fixture roots and complete/truncated fixture records. Only test helpers create
and flush files. These fixtures demonstrate re-read persistence after close and
backup, not a production transaction, process-crash/power-loss guarantee,
multi-writer journal or accepted external-effect replay. G2 acknowledgement
transactions, G4 projection-loss recovery and G5 component/installer changes
remain disabled and separately admitted.
