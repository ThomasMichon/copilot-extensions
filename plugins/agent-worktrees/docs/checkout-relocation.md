# Inactive checkout relocation

The initial contract is `agent-worktrees.checkout-relocation.v1`. This is a
conservative native-Windows complete flow, not support for every inactive
worktree or an unqualified POSIX copy lane. Capability discovery reports the
qualified platform and whether the selected native process supports it.
Non-Windows plan/apply/abort refuse before receiving-record or journal custody;
bounded POSIX copying and native parity remain open in the Active source effort.

## Discover before invoking

Use the selected executable's existing read-only query surface:

```text
agent-worktrees get checkout-relocation
agent-worktrees move <id> --to "<absolute destination ending in the same id>" --json
agent-worktrees move <id> --to "<same destination>" --apply --json
```

An older executable rejects the unknown `get` key. Do not send it a new `move`
command, guess support from payload/package versions, or fall back to bare Git
movement. The capability describes the selected code, not an installed or
running sibling's readiness. A published resident must also advertise
`checkout_relocation_writer_generation: 1`; missing/old/invalid publication
refuses without starting or replacing a service. No resident is required for
standalone operation.

Without `--apply` or `--abort`, the command is read-only. It parses a bounded,
identity-stable original ledger, checks existing ordinary-shape and launch
evidence, and refuses missing original receiving proof or lifecycle markers
without recovering or acquiring their owners. It never backfills receiving
receipts, prepares a journal, recovers databases, creates launch/record
sidecars, or issues strict action-time mux/Copilot/process probes.

The current Windows provider has no qualified passive native-owner coverage
contract. An otherwise ordinary record therefore receives an explicit
unconfirmed-owner refusal, not a positive plan. Empty registered sessions,
cached mux negatives and readable CWD holders are not substitutes. Original
proof inspection and complete CWD coverage remain separate prerequisites.
`--apply` retains its existing serialized strict mutation preflight and
recovery behavior; a passive refusal is not authorization to invoke it.
Operational and checkout inventories propagate directory-enumeration failures;
an unreadable subtree is never treated as proof that no references/data exist.

## Initial supported scope

- An existing ordinary managed linked Git checkout with a retained creation
  nonce, completed/final tracking disposition and locally proven landed source
  content: ancestry, patch equivalence or identical upstream tree.
- A canonical, nonredirected recorded source whose basename is the worktree ID,
  checked before creating
  recovery custody; unsupported source aliases never create an unrecoverable
  journal.
- Confirmed inactive session/mux and process-CWD owners, no retained sessions,
  launch requests/attempts, active effort/follow-up, unmerged PRs, unsettled
  obligations, pairing or opaque backend/execution state.
- Clean Git state, with all actual ordinary checkout files copied, including
  ignored files. File bytes, modes, file modification times, ownership/DACL
  semantics and native attributes are verified before source deletion.
- Confirmed source retirement/creation access and writable receiving metadata.
  Windows uses non-mutating access-handle probes. POSIX requires owned writable
  source directories and parent; restrictive directory support remains gated.
- At most 10,000 inventory entries and 1 GiB of checkout data. Windows requires
  uniform checkout access and an equally restricted destination parent.
- An existing absolute, nonredirected destination parent outside any Git
  checkout, receiving anchor and project-state directory. The destination
  directory keeps the worktree ID as its name.

Links/junctions/reparse points, hardlinks, special storage attributes, alternate
streams, POSIX extended attributes/sparse storage, path-bound venvs, submodules,
checkout-local agendas and configured index participation are not qualified in
v1. Missing/malformed operational metadata and immutable creation receipts also
refuse. These restrictions do not finalize, abandon, clean or rewrite anything.
They are staged support boundaries, not a permanent exclusion of inactive WIP.
POSIX admission and retirement require a bounded native mount-table inventory;
mounted checkout roots/subtrees (including same-device bind mounts) refuse.
The current inventory reader uses Linux `/proc/self/mountinfo`; a platform
without that readable native table refuses rather than assuming no mounts.
Cancellation inventories the owned stage before deleting it as well.
Ignored nested Git checkouts/worktrees also refuse before custody; only the
outer linked checkout's Git registration is qualified. Legacy and itemized
open/pending-transfer follow-ups both count as outstanding responsibility.

Git-locked worktrees are refused even when their tracking disposition is final.
Case/slash or normalized permission aliases are refused until their migration is
qualified; they are not silently left pointing at the removed source.
Reference containment uses normalized native paths, including parent-component
and filesystem aliases. Unqualified network references and relative
parent-component references refuse rather than triggering network probes.

Only known operational permission/trust paths are rebound through their existing
serialized file owners. Conversation/store/event/proposal history is never
globally rewritten. An existing live session database is read narrowly and
read-only; a WAL database without its already-owned sidecars is refused.
Unrelated live rows are untouched. Copilot session-reference matches are a
refusal until their reader/writer semantics are qualified.
Reference inspection has a 10,000-entry budget and a three-second database
inventory budget; an incomplete inventory refuses.
Permission files must remain ordinary, single-link files at capture and under
the mutation locks. File links are refused without replacing their linkage.

## Transfer and recovery

The existing per-worktree lifecycle and launch-admission leases fence the move.
Admission pins the record bytes used to validate eligibility, not a later
snapshot; responsibility acquired during planning refuses before custody.
The receiving record's journal retains original identity, paths, content
fingerprint, permission state and caller incarnation. Native bounded child
custody owns Windows copying; there is no detached helper or new daemon.

Copy into an exclusively created destination-side stage, verify it, publish the
destination, repair Git through Git's worktree administration, rebind supported
permissions and the authoritative record, and verify the real consumer before
removing the unchanged original. Cross-volume transfer is not an atomic rename.
Destination data and recovery/pointer files are flushed before retiring the
source. Namespace publication uses native write-through replacement on Windows
and parent-directory synchronization on POSIX. Retirement first renames the
verified original to `<id>.retiring-<original-stage-token>` beside the old
location through the same exclusive native publication owner. Full source
equality is required before publication and before retirement; source deletions
are changes, not completed cleanup. The fully verified retirement copy and
old-parent barrier precede a durable `retiring` checkpoint. Only that checkpoint
allows retrying a partially removed owned duplicate. The old parent is
synchronized again before completion. A barrier failure can retain the original under its
old name or this tokenized retirement name; the original journal recovers either
without another allocation. Do not remove that copy manually. No global
multi-file pointer transaction is claimed.
Retry validates the partial stage's plain entries and access before copying.
POSIX retry replaces owned staged files and temporarily makes owned staged
directories writable, restoring source metadata through the normal copy owner;
it never changes source permissions.

Retry the exact original command after an interruption. It recovers that
operation, not another allocation. Another live or unobservable original caller,
changed identity/content, an occupied target or unknown effects remain explicit
holds. Do not clear journals, claims or locks manually. Do not use either copy
for new work while the move remains unfinished.
Cleanup treats the unfinished record as a per-worktree lifecycle refusal and
continues unrelated eligible candidates; it does not clear move custody.

Before destination publication, cancellation is available:

```text
agent-worktrees move <id> --to "<original destination>" --abort --json
```

Cancellation removes only the positively owned stage and leaves the original
checkout, including newly authored source edits, unchanged. Uncertain stage
creation, destination publication or active stage owners refuse. After
publication, use forward recovery rather than guessing a rollback.
The cancellation parent barrier precedes its terminal receipt and repeats when
recovery finds the owned stage already removed.

A subsequent qualified move is a new location revision of the same worktree.
The completed/cancelled preceding journal is archived byte-for-byte, not
replaced. Reversal after completion uses another qualified move while the
checkout remains within v1's supported scope. Creating retained sessions or
other unsupported state first requires the corresponding broader support.
An idempotent completed response records the earlier operation; inspect current
tracking/Git state separately rather than treating a historical receipt as new
execution evidence.

## Allocation roots and retained authority

Allocation defaults select new workspaces. Retained ordinary checkouts outside
that root need the actual receiving ledger, original identity, current execution
space, linked Git registration, branch and receiving Git family. There is no
machine-wide arbitrary-path allowlist. Foreign projects/hosts/environments,
redirected ledgers, replaced records and missing/unrelated Git proof still refuse.

Each companion repository's effective configuration selects its new allocation
root. New paired intent freezes the original parent incarnation/owner,
companion identity, anchor and target before carving. Its complete bytes are
published exclusively and its parent synchronized before allocation can proceed;
identical retry reuses it and conflicting intent is never overwritten. Recovery uses that owned
intent after defaults change. Historical no-intent companions retain the
original anchor-derived rule, not today's allocation preference. Immutable
creation receipts are not rewritten and retries never silently re-carve.

## Source qualification versus activation

The focused source packet is:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/native/test_checkout_relocation.py --allow-explicit-tiers
```

The optional `AGENT_WORKTREES_RELOCATION_SECOND_ROOT` test input must name an
explicitly enrolled private disposable parent on another filesystem. Without
it, the cross-volume case is unqualified, not passed. The fixture creates one
timestamped owned child and retires only its own successful checkout; failures
retain the ownership breadcrumb for inspection.
External-volume qualification also requires
`AGENT_WORKTREES_RELOCATION_PRIMARY_ROOT`: an explicitly enrolled persistent
private fixture parent on the primary filesystem. Its Git objects, record and
recovery journal stay outside runner teardown, so a failed external copy does
not lose its recovery authority. The caller retains that primary evidence and
cleans it only after positively confirmed completion. The two-leg native control
also abruptly exits owned children after publication and retirement rename,
then verifies original-operation recovery and retained primary authority.
It has its own 300-second item budget; production probes are not weakened.

Source tests, required PR CI, source merge, Main promotion, selected-runtime
qualification and actual operator migration are different gates. Normal rollout
uses the repository's unified updater after the release is available, subject
to explicit activation authority. It can affect all registered plugin runtimes
and central service selections, not just this command. Preserve user
configuration, worktrees, sessions and protected consumers; follow the
[install contract](../../../docs/install-contract.md).

An approved immutable development generation uses its existing owning installer
and claim/selection lifecycle, never a test venv, editable source link or
installed-file patch. See the
[development-generation pattern](../../../docs/patterns/mutable-dev-slot.md).
Verify the selected CLI contract and compatible running resident separately
before a pilot. Do not change allocation defaults or move retained user
worktrees merely because source tests or a source PR passed.
