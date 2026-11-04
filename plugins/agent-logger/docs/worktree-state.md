# Typed worktree-state collection

Parent: [Logger architecture](architecture.md#session-sync).
Owner export: [complete consistent agenda export](../../efforts/skills/driving-effort-agendas/references/agenda-export.md).
Standing intent: [durable worktree data](../../../visions/plugins/agent-worktrees/worktree-data/README.md).

## Current source boundary

`sync.worktree_state_archive` supplies additive collection and fresh local
readback for an explicitly selected, owner-verified agenda export. It uses the
existing `Target` abstraction; only `LocalTarget.worktree_state_root()` opts in.
Other targets explicitly lack that capability, including OneDrive replicas.
This first source slice is a library boundary, not a new scheduled collection,
CLI, source crawler or installed integration. Ordinary session sync is unchanged.

The orchestration caller must first qualify the worktree owner's repository,
full worktree/incarnation and selected store, obtain the complete domain-owned
export, and negotiate the selected owner's complete-export verifier. The library
takes that exact typed identity and verifier; it never imports another plugin's
runtime or guesses a state home. Provider discovery/export orchestration remains
an independent unfinished integration.

## Admission before effects

Collection requires an actual nonpassive, authorized **enforce-mode** aggregate
plan for the executing machine. A valid diagnostic/observe plan is not
permission. Exactly one collection claim must cover the canonical source
repository and bind the actual active target's filesystem root.

The selected sync configuration remains authoritative. Secret redaction or
essential-session-only publication blocks this lossless database collection;
Logger does not silently redact accepted history and call it complete. Legacy
session allow/deny/opt-in filters do not yet define typed-state admission and
therefore refuse this boundary. Claims with content profiles, landing,
retention or mutation policies also refuse until those semantics are actually
implemented here.

The initial adapter requires POSIX directory-relative publication and directory
barriers. Native Windows collection is explicitly unsupported, not inferred
from the existing owner's separate export support. The isolated Linux packet
does not qualify Windows or hardware power-loss behavior.

## Identity, publication and readback

One collection key binds canonical repository, **full** worktree identity,
worktree incarnation, agenda store and accepted revision. A hashed namespace
avoids path-shaped identifiers. The revision is immutable: a different export
at the same key conflicts instead of replacing previously retained evidence.
This also exposes a changed delivery-state cut at an unchanged accepted
revision rather than silently discarding either snapshot.

Only the verified `agenda.sqlite3` and original `manifest.json` are collected.
The owner's optional prepared manifest is not needed in a transported export.
Copying checks the exact declared byte count and SHA-256, with bounded chunks
and a complete-copy deadline. The selected owner then verifies the destination's
complete schema, ledger and immutable records. A receipt follows that readback,
never a partial copy or transport no-op.

Contributing sessions reference the same collection through additive,
content-checked metadata; the agenda is not copied beneath each session.
An exact retry verifies actual retained bytes afresh before adding references.
Conflicting or interrupted publications remain visible for owner-led
reconciliation; the library neither overwrites nor deletes them.

`readback_worktree_state` independently repeats admission and owner verification.
It does not trust a cached transfer result or receipt in place of stored bytes,
and a missing collection remains missing without creating directories.

The schema-v1 receipt binds the destination, exact source identity, manifest
digest and member size/hash. It reports **local readback verified** and
**remote archive acknowledgment false**. Neither a receipt nor a successful local
copy grants reclamation, retention changes, restoration, head ownership, claims,
leases or live execution authority.

## Qualification

The opt-in `agent-logger` `tests/test_worktree_state.py` packet covers real
efforts export/readback in separate Python processes, complete-byte retention,
multiple contributing sessions, exact retry, privacy/claim refusal, corruption,
partial publication and redirected paths. It uses synthetic private fixtures,
not an operator's actual agenda or archive destination. Required unit-only CI
admission and operational/native acceptance are separate gates; see
[testing](../../../TESTING.md).
